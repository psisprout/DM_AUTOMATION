"""Search for the smallest BBS model that still passes the comparison.

The loop is: a generator proposes a model, the model is scored against the
reference, and the run keeps the cheapest candidate that meets the criteria.

Scoring uses the fitted model's own response, so no SPICE is needed to rank
candidates - a search iteration costs a fit, not a simulation.  Confirm the
winner through the normal deck-and-run path afterwards, which is what actually
proves the netlist a downstream tool will read.

"Optimal" here is the *smallest* model that passes, not the most accurate one:
a fit with hundreds of poles will match beautifully and be useless to simulate
with.
"""

from __future__ import annotations

import csv
import os
import time
from dataclasses import dataclass, field
from typing import Callable, Iterator, List, Optional, Sequence, Tuple

import numpy as np

from .compare import (
    _RANK,
    FAIL,
    PASS,
    CompareResult,
    Criteria,
    ReferenceSpec,
    compare,
)
from .touchstone import Network


class GeneratorUnavailable(RuntimeError):
    """Raised when a backend's dependency or interface is not available."""


@dataclass
class Candidate:
    """One proposed model."""

    label: str
    params: dict
    size: int  # poles, the thing we are trying to minimize
    network: Optional[Network] = None  # the model's own response
    netlist: str = ""  # path to a SPICE subcircuit, when one was written
    extra: dict = field(default_factory=dict)
    error: str = ""  # set when the generator could not produce a model


@dataclass
class Trial:
    """A candidate and how it scored."""

    index: int
    candidate: Candidate
    result: Optional[CompareResult]
    seconds: float

    @property
    def status(self) -> str:
        return self.result.status if self.result else FAIL

    @property
    def headroom(self) -> float:
        return self.result.headroom() if self.result else float("inf")

    @property
    def margin(self) -> float:
        return self.result.margin() if self.result else float("-inf")

    @property
    def ok(self) -> bool:
        return self.result is not None and not self.candidate.error

    def row(self) -> dict:
        row = {
            "index": self.index,
            "label": self.candidate.label,
            "size": self.candidate.size,
            "status": self.status,
            "headroom": f"{self.headroom:.6g}",
            "margin": f"{self.margin:.6g}",
            "seconds": f"{self.seconds:.3f}",
            "error": self.candidate.error,
        }
        row.update({f"p_{k}": v for k, v in self.candidate.params.items()})
        row.update({f"x_{k}": v for k, v in self.candidate.extra.items()})
        return row


@dataclass
class Sweep:
    trials: List[Trial] = field(default_factory=list)

    def accepted(self, require: str = PASS) -> List[Trial]:
        """Trials whose status is ``require`` or better."""
        limit = _RANK[require]
        return [t for t in self.trials if t.ok and _RANK[t.status] <= limit]

    def best(self, require: str = PASS) -> Optional[Trial]:
        """Smallest accepted model; ties broken by the larger margin."""
        candidates = self.accepted(require)
        if not candidates:
            return None
        return min(candidates, key=lambda t: (t.candidate.size, -t.margin))

    def most_accurate(self) -> Optional[Trial]:
        scored = [t for t in self.trials if t.ok]
        return max(scored, key=lambda t: t.margin) if scored else None

    def pareto(self) -> List[Trial]:
        """Trials no other trial beats on both size and headroom."""
        scored = [t for t in self.trials if t.ok]
        out = []
        for t in scored:
            dominated = any(
                o is not t
                and o.candidate.size <= t.candidate.size
                and o.headroom <= t.headroom
                and (o.candidate.size < t.candidate.size or o.headroom < t.headroom)
                for o in scored
            )
            if not dominated:
                out.append(t)
        return sorted(out, key=lambda t: t.candidate.size)

    def to_csv(self, path: str) -> str:
        rows = [t.row() for t in self.trials]
        if not rows:
            raise ValueError("the sweep recorded no trials")
        fields: List[str] = []
        for row in rows:  # union of keys, first-seen order
            for key in row:
                if key not in fields:
                    fields.append(key)
        with open(path, "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=fields)
            writer.writeheader()
            for row in rows:
                writer.writerow(row)
        return path


class CsvAppender:
    """Write trials as they finish, so an interrupted sweep keeps its history.

    A long sweep is minutes per fit and gets killed often enough - a queue
    limit, a Ctrl-C, a lost session - and losing every completed trial because
    the file is only written at the end is a bad trade.  Columns come from the
    first row; the final :meth:`Sweep.to_csv` rewrites the file with the union
    of every row's keys, so the complete run is still authoritative.
    """

    def __init__(self, path: str) -> None:
        self.path = path
        self._fields: List[str] = []
        self._handle = None
        self._writer = None

    def add(self, row: dict) -> None:
        if self._writer is None:
            self._fields = list(row)
            self._handle = open(self.path, "w", newline="", encoding="utf-8")
            self._writer = csv.DictWriter(
                self._handle, fieldnames=self._fields, extrasaction="ignore", restval=""
            )
            self._writer.writeheader()
        self._writer.writerow(row)
        self._handle.flush()

    def close(self) -> None:
        if self._handle is not None:
            self._handle.close()
            self._handle = None
            self._writer = None


# --------------------------------------------------------------------------
# generators
# --------------------------------------------------------------------------


class Generator:
    """Proposes models for the sweep to score."""

    name = "base"

    def candidates(self, ref: Network) -> Iterator[Candidate]:
        raise NotImplementedError


class VectorFitGenerator(Generator):
    """Fit the reference with scikit-rf's vector fitting.

    The same algorithm commercial broadband-SPICE tools use, and it runs in
    process, so a sweep costs seconds per point rather than a simulation.
    """

    name = "skrf"

    def __init__(
        self,
        orders: Sequence[int] = tuple(range(2, 41, 2)),
        n_real: int = 2,
        enforce_passivity: bool = False,
        out_dir: str = "",
    ) -> None:
        self.orders = list(orders)
        self.n_real = n_real
        self.enforce_passivity = enforce_passivity
        self.out_dir = out_dir

    def _skrf(self):
        try:
            import skrf
        except ImportError as exc:  # pragma: no cover - depends on the environment
            raise GeneratorUnavailable(
                "the skrf backend needs scikit-rf:  pip install scikit-rf"
            ) from exc
        return skrf

    @staticmethod
    def _vector_fitting(skrf):
        """Locate VectorFitting across scikit-rf versions.

        1.x exports it at the top level; 2.x only from skrf.vectorFitting.
        """
        cls = getattr(skrf, "VectorFitting", None)
        if cls is None:
            module = getattr(skrf, "vectorFitting", None)
            cls = getattr(module, "VectorFitting", None)
        if cls is None:
            raise GeneratorUnavailable(
                f"this scikit-rf ({getattr(skrf, '__version__', '?')}) does not "
                "expose VectorFitting at skrf.VectorFitting or "
                "skrf.vectorFitting.VectorFitting"
            )
        return cls

    def candidates(self, ref: Network) -> Iterator[Candidate]:
        skrf = self._skrf()
        network = skrf.Network(ref.path) if ref.path else _to_skrf(skrf, ref)
        for ncplx in self.orders:
            size = self.n_real + 2 * ncplx
            params = {"n_real": self.n_real, "n_cmplx": ncplx}
            label = f"{self.name}:{size}p"
            try:
                yield self._fit(skrf, network, ref, params, size, label)
            except Exception as exc:  # a fit can simply not converge
                yield Candidate(
                    label=label, params=params, size=size, error=f"{type(exc).__name__}: {exc}"
                )

    def _fit(self, skrf, network, ref: Network, params, size, label) -> Candidate:
        vf = self._vector_fitting(skrf)(network)
        vf.vector_fit(n_poles_real=params["n_real"], n_poles_cmplx=params["n_cmplx"])
        if self.enforce_passivity and not vf.is_passive():
            vf.passivity_enforce()
        n = ref.nports
        s = np.empty((ref.freq.size, n, n), dtype=complex)
        for i in range(n):
            for j in range(n):
                s[:, i, j] = vf.get_model_response(i, j, ref.freq)
        netlist = ""
        if self.out_dir:
            os.makedirs(self.out_dir, exist_ok=True)
            netlist = os.path.join(self.out_dir, f"fit_{size:04d}p.sp")
            vf.write_spice_subcircuit_s(netlist)
        return Candidate(
            label=label,
            params=params,
            size=size,
            network=Network(
                freq=ref.freq.copy(),
                s=s,
                z0=ref.z0.copy(),
                port_names=list(ref.port_names),
            ),
            netlist=netlist,
            extra={
                "rms": f"{vf.get_rms_error():.4g}",
                "passive": vf.is_passive(),
            },
        )


class NdeGenerator(Generator):
    """Slot for Ansys AEDT's Network Data Explorer.

    Not wired up: whether NDE's fitting is reachable from a script has to be
    established on a machine that has AEDT.  Record a conversion with AEDT's
    script recorder; if the NDE calls show up, they go here, and nothing else
    in the sweep changes.
    """

    name = "nde"

    def __init__(self, **kw) -> None:
        self.kw = kw

    def candidates(self, ref: Network) -> Iterator[Candidate]:
        raise GeneratorUnavailable(
            "the NDE backend is not wired up yet. Record a conversion in AEDT "
            "with script recording on; if the NDE calls appear, they belong in "
            "NdeGenerator.candidates(). Until then use the skrf backend, which "
            "fits with the same vector-fitting algorithm."
        )


GENERATORS = {VectorFitGenerator.name: VectorFitGenerator, NdeGenerator.name: NdeGenerator}


def _to_skrf(skrf, ref: Network):
    return skrf.Network(frequency=skrf.Frequency.from_f(ref.freq, unit="hz"), s=ref.s, z0=ref.z0)


# --------------------------------------------------------------------------
# the loop
# --------------------------------------------------------------------------


def run_sweep(
    ref: Network,
    generator: Generator,
    criteria: Optional[Criteria] = None,
    reference: Optional[ReferenceSpec] = None,
    align_mode: str = "intersect",
    on_trial: Optional[Callable[[Trial], None]] = None,
    stop_when_passing: bool = False,
) -> Sweep:
    """Score every candidate the generator proposes.

    ``stop_when_passing`` ends the sweep at the first accepted model, which is
    what you want when the generator walks from small to large: the first pass
    is the smallest pass.
    """
    sweep = Sweep()
    for index, cand in enumerate(generator.candidates(ref)):
        started = time.time()
        result = None
        if cand.network is not None and not cand.error:
            try:
                result = compare(
                    ref,
                    cand.network,
                    criteria=criteria,
                    reference=reference,
                    align_mode=align_mode,
                )
            except Exception as exc:
                cand.error = f"{type(exc).__name__}: {exc}"
        trial = Trial(
            index=index, candidate=cand, result=result, seconds=time.time() - started
        )
        sweep.trials.append(trial)
        if on_trial:
            on_trial(trial)
        if stop_when_passing and trial.ok and trial.status == PASS:
            break
    return sweep


def parse_range(text: str) -> List[int]:
    """``2:40:2`` -> [2, 4, ..., 40].  A plain list like ``4,8,16`` also works."""
    text = text.strip()
    if ":" in text:
        parts = [int(p) for p in text.split(":")]
        if len(parts) == 2:
            lo, hi, step = parts[0], parts[1], 1
        elif len(parts) == 3:
            lo, hi, step = parts
        else:
            raise ValueError(f"cannot read a range from {text!r}")
        if step <= 0:
            raise ValueError("the step must be positive")
        return list(range(lo, hi + 1, step))
    return [int(p) for p in text.replace(",", " ").split()]


# --------------------------------------------------------------------------
# command line
# --------------------------------------------------------------------------


def build_parser():
    import argparse

    p = argparse.ArgumentParser(
        prog="sparabbs.sweep",
        description="Search for the smallest BBS model that still passes.",
    )
    p.add_argument("--snp", required=True, help="reference Touchstone file")
    p.add_argument("--backend", default="skrf", choices=sorted(GENERATORS))
    p.add_argument(
        "--orders", default="2:40:2", help="complex-pole pairs, e.g. 2:40:2 or 4,8,16"
    )
    p.add_argument("--n-real", type=int, default=2, help="real poles per fit")
    p.add_argument("--enforce-passivity", action="store_true")
    p.add_argument("--out", default="sweep_run", help="working directory")
    p.add_argument("--csv", default="", help="history CSV (default: <out>/sweep.csv)")
    p.add_argument(
        "--keep-netlists",
        action="store_true",
        help="write a SPICE subcircuit for every candidate, not just the winner",
    )
    p.add_argument(
        "--require",
        default=PASS,
        choices=[PASS, "WARN"],
        help="the weakest status the winner may have",
    )
    p.add_argument(
        "--stop-early",
        action="store_true",
        help="stop at the first passing model (the smallest, when orders ascend)",
    )
    p.add_argument("--mag-err-pct", type=float, default=5.0)
    p.add_argument("--err-db", type=float, default=0.5)
    p.add_argument("--phase-err-deg", type=float, default=5.0)
    p.add_argument("--peak-shift-pct", type=float, default=2.0)
    p.add_argument("--peak-mag-err-pct", type=float, default=10.0)
    return p


def main(argv: Optional[Sequence[str]] = None) -> int:
    import sys

    from .touchstone import read_touchstone

    args = build_parser().parse_args(argv)
    out_dir = os.path.abspath(args.out)
    os.makedirs(out_dir, exist_ok=True)

    ref = read_touchstone(args.snp)
    print(f"reference: {ref.describe()}")

    criteria = Criteria(
        mag_err_pct=args.mag_err_pct,
        err_db=args.err_db,
        phase_err_deg=args.phase_err_deg,
        peak_shift_pct=args.peak_shift_pct,
        peak_mag_err_pct=args.peak_mag_err_pct,
    )
    if args.backend == "skrf":
        generator = VectorFitGenerator(
            orders=parse_range(args.orders),
            n_real=args.n_real,
            enforce_passivity=args.enforce_passivity,
            out_dir=out_dir if args.keep_netlists else "",
        )
    else:
        generator = GENERATORS[args.backend]()

    csv_path = args.csv or os.path.join(out_dir, "sweep.csv")
    appender = CsvAppender(csv_path)

    header = f"{'poles':>6} {'status':>6} {'headroom':>9} {'margin':>8} {'rms':>10} {'passive':>8}"
    print(header)

    def show(trial: Trial) -> None:
        c = trial.candidate
        if c.error:
            print(f"{c.size:>6} {'ERROR':>6}  {c.error[:50]}")
        else:
            print(
                f"{c.size:>6} {trial.status:>6} {trial.headroom:>9.4g} "
                f"{trial.margin:>+8.4g} {str(c.extra.get('rms', '')):>10} "
                f"{str(c.extra.get('passive', '')):>8}"
            )
        appender.add(trial.row())
        sys.stdout.flush()  # a piped or redirected sweep shows progress

    try:
        sweep = run_sweep(
            ref,
            generator,
            criteria=criteria,
            on_trial=show,
            stop_when_passing=args.stop_early,
        )
    except GeneratorUnavailable as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        appender.close()
        print(f"\ninterrupted; the trials so far are in {csv_path}", file=sys.stderr)
        return 130
    finally:
        appender.close()

    sweep.to_csv(csv_path)  # rewrite with the union of every row's columns
    print(f"\nhistory: {csv_path}")

    pareto = sweep.pareto()
    if pareto:
        print("pareto front (size, headroom):")
        for t in pareto:
            print(f"  {t.candidate.size:>4} poles  headroom {t.headroom:.3f}  {t.status}")

    best = sweep.best(require=args.require)
    if best is None:
        print(
            f"\nno candidate reached {args.require}; "
            "widen --orders or loosen the criteria",
            file=sys.stderr,
        )
        return 1
    print(
        f"\nbest: {best.candidate.size} poles, {best.status}, "
        f"margin {best.margin:+.3f}"
    )
    if not best.candidate.netlist and isinstance(generator, VectorFitGenerator):
        generator.out_dir = out_dir
        for cand in VectorFitGenerator(
            orders=[best.candidate.params["n_cmplx"]],
            n_real=best.candidate.params["n_real"],
            enforce_passivity=args.enforce_passivity,
            out_dir=out_dir,
        ).candidates(ref):
            best.candidate.netlist = cand.netlist
    if best.candidate.netlist:
        print(f"netlist: {best.candidate.netlist}")
        print(
            "confirm it through SPICE:\n"
            f"  python -m sparabbs.cli --snp {args.snp} "
            f"--bbs {best.candidate.netlist} --out {out_dir}/confirm"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
