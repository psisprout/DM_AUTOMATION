"""Steps 3 and 4: compare several BBS candidates against one reference, and rank them.

Each candidate is either a SPICE netlist (a deck is generated, run, and the
Touchstone it produces is compared) or an already-extracted Touchstone file,
which skips the simulation.  Output is JSON so an agent can read the verdict
without parsing prose.
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from typing import List, Optional, Sequence

from . import deck as deck_mod
from . import runner as runner_mod
from .compare import (
    _RANK,
    FAIL,
    PASS,
    CompareResult,
    Criteria,
    ReferenceSpec,
    compare,
)
from .netlist import read_netlist
from .touchstone import Network, read_touchstone

TOUCHSTONE_RE = re.compile(r"\.s\d+p$", re.IGNORECASE)
#: a_sp_0p01.sp -> 0.01, the renorm impedance the NDE run used
LABEL_RE = re.compile(r"_(\d+p\d+|\d+)(?:_|\.)", re.IGNORECASE)


@dataclass
class Candidate:
    path: str
    label: str
    renorm_ohm: Optional[float] = None
    snp: str = ""  # the Touchstone compared, once it exists
    status: str = FAIL
    headroom: float = float("inf")
    margin: float = float("-inf")
    counts: dict = field(default_factory=dict)
    seconds: float = 0.0
    error: str = ""
    result: Optional[CompareResult] = None

    @property
    def ok(self) -> bool:
        return not self.error and self.result is not None

    def to_dict(self) -> dict:
        return {
            "path": self.path,
            "label": self.label,
            "renorm_ohm": self.renorm_ohm,
            "compared_snp": self.snp,
            "status": self.status,
            "headroom": None if self.headroom == float("inf") else self.headroom,
            "margin": None if self.margin == float("-inf") else self.margin,
            "counts": self.counts,
            "seconds": round(self.seconds, 3),
            "error": self.error,
        }


def label_of(path: str) -> str:
    return os.path.splitext(os.path.basename(path))[0]


def renorm_of(path: str) -> Optional[float]:
    """Read the renorm impedance out of a name like ``a_sp_0p01.sp``."""
    stem = label_of(path)
    match = None
    for m in re.finditer(r"(?:^|_)(\d+p\d+|\d+)(?=$|_)", stem):
        match = m
    if match is None:
        return None
    try:
        return float(match.group(1).replace("p", "."))
    except ValueError:  # pragma: no cover - the regex already constrains this
        return None


def rank(candidates: Sequence[Candidate], require: str = PASS) -> List[Candidate]:
    """Best first: accepted models by headroom, then everything else."""
    limit = _RANK[require]

    def key(c: Candidate):
        accepted = c.ok and _RANK[c.status] <= limit
        return (0 if accepted else 1, c.headroom)

    return sorted(candidates, key=key)


def best(candidates: Sequence[Candidate], require: str = PASS) -> Optional[Candidate]:
    limit = _RANK[require]
    accepted = [c for c in candidates if c.ok and _RANK[c.status] <= limit]
    return min(accepted, key=lambda c: c.headroom) if accepted else None


def evaluate(
    ref: Network,
    path: str,
    out_dir: str,
    criteria: Optional[Criteria] = None,
    reference: Optional[ReferenceSpec] = None,
    align_mode: str = "intersect",
    command: str = runner_mod.DEFAULT_COMMAND,
    cpu: int = 4,
    wait_minutes: float = 120.0,
    lin_options: str = deck_mod.DEFAULT_LIN_OPTIONS,
    on_line=None,
) -> Candidate:
    """Score one candidate, running SPICE only when it is a netlist."""
    cand = Candidate(path=os.path.abspath(path), label=label_of(path),
                     renorm_ohm=renorm_of(path))
    started = time.time()
    try:
        if TOUCHSTONE_RE.search(path):
            cand.snp = os.path.abspath(path)
        else:
            cand.snp = _run_one(
                ref, path, out_dir, command, cpu, wait_minutes, lin_options, on_line
            )
        dut = read_touchstone(cand.snp)
        result = compare(
            ref, dut, criteria=criteria, reference=reference, align_mode=align_mode
        )
        cand.result = result
        cand.status = result.status
        cand.headroom = result.headroom()
        cand.margin = result.margin()
        cand.counts = result.counts()
    except Exception as exc:
        cand.error = f"{type(exc).__name__}: {exc}"
    cand.seconds = time.time() - started
    return cand


def _run_one(ref, path, out_dir, command, cpu, wait_minutes, lin_options, on_line) -> str:
    work = os.path.join(out_dir, label_of(path))
    os.makedirs(work, exist_ok=True)
    sub = read_netlist(path).top_candidates()[0]
    cfg = deck_mod.config_from_inputs(ref, sub, path, work, lin_options=lin_options)
    problems = deck_mod.validate(cfg)
    if problems:
        raise ValueError("; ".join(problems))
    deck_path = deck_mod.write_deck(cfg)

    spec = runner_mod.RunSpec(
        deck_path=deck_path, command_template=command, cpu=cpu, cwd=work
    )
    started = time.time()
    runner_mod.run(spec, on_line=on_line)
    watcher = runner_mod.OutputWatcher(
        work, cfg.nports, cfg.expected_snp(), started, exclude=[ref.path]
    )
    found = runner_mod.wait_for_output(watcher, timeout=wait_minutes * 60.0)
    if not found:
        produced = ", ".join(watcher.produced()[:10]) or "nothing"
        raise RuntimeError(
            f"no .s{cfg.nports}p appeared in {work} within {wait_minutes:g} min "
            f"(the run wrote: {produced})"
        )
    return found


# --------------------------------------------------------------------------
# command line
# --------------------------------------------------------------------------


def build_parser():
    import argparse

    p = argparse.ArgumentParser(
        prog="sparabbs.batch",
        description="Compare several BBS candidates against one reference and "
        "rank them by fidelity.",
    )
    p.add_argument("--snp", required=True, help="the reference Touchstone file")
    p.add_argument(
        "bbs", nargs="+",
        help="candidate BBS netlists, or already-extracted .sNp files",
    )
    p.add_argument("--out", default="bbs_compare", help="working directory")
    p.add_argument("--json", default="", help="report path (default: <out>/compare.json)")
    p.add_argument("--require", default=PASS, choices=[PASS, "WARN"])
    p.add_argument("--cmd", default=runner_mod.DEFAULT_COMMAND)
    p.add_argument("--cpu", type=int, default=4)
    p.add_argument("--wait", type=float, default=120.0, help="minutes per candidate")
    p.add_argument("--lin-options", default=deck_mod.DEFAULT_LIN_OPTIONS)
    p.add_argument("--ref-mode", default="global",
                   choices=("global", "port", "short", "open"))
    p.add_argument("--ref-ports", default="", help="comma-separated port indices")
    p.add_argument("--align", default="intersect", choices=("exact", "intersect", "interp"))
    p.add_argument("--z0", type=float, default=None)
    p.add_argument("--mag-err-pct", type=float, default=5.0)
    p.add_argument("--err-db", type=float, default=0.5)
    p.add_argument("--phase-err-deg", type=float, default=5.0)
    p.add_argument("--peak-shift-pct", type=float, default=2.0)
    p.add_argument("--peak-mag-err-pct", type=float, default=10.0)
    p.add_argument("--quiet", action="store_true", help="only the JSON path on stdout")
    return p


def main(argv: Optional[Sequence[str]] = None) -> int:
    import sys

    args = build_parser().parse_args(argv)
    out_dir = os.path.abspath(args.out)
    os.makedirs(out_dir, exist_ok=True)

    try:
        ref = read_touchstone(args.snp)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.z0 is not None:
        ref.z0[:] = args.z0
    elif not ref.z0_from_file:
        print(
            f"error: {args.snp} states no reference impedance; pass --z0",
            file=sys.stderr,
        )
        return 2

    criteria = Criteria(
        mag_err_pct=args.mag_err_pct,
        err_db=args.err_db,
        phase_err_deg=args.phase_err_deg,
        peak_shift_pct=args.peak_shift_pct,
        peak_mag_err_pct=args.peak_mag_err_pct,
    )
    spec = ReferenceSpec(
        mode=args.ref_mode,
        ports=[int(p) for p in args.ref_ports.replace(",", " ").split()],
    )

    if not args.quiet:
        print(f"reference: {ref.describe()}")
        print(f"{'candidate':<28} {'renorm':>10} {'status':>6} {'headroom':>10} {'time':>8}")

    candidates = []
    for path in args.bbs:
        cand = evaluate(
            ref, path, out_dir,
            criteria=criteria, reference=spec, align_mode=args.align,
            command=args.cmd, cpu=args.cpu, wait_minutes=args.wait,
            lin_options=args.lin_options,
        )
        candidates.append(cand)
        if not args.quiet:
            if cand.error:
                print(f"{cand.label:<28} {'':>10} {'ERROR':>6}  {cand.error[:44]}")
            else:
                renorm = "" if cand.renorm_ohm is None else f"{cand.renorm_ohm:g}"
                print(
                    f"{cand.label:<28} {renorm:>10} {cand.status:>6} "
                    f"{cand.headroom:>10.4g} {cand.seconds:>7.1f}s"
                )
            sys.stdout.flush()

    ranked = rank(candidates, require=args.require)
    winner = best(candidates, require=args.require)
    payload = {
        "reference_snp": ref.path,
        "nports": ref.nports,
        "criteria": {
            "mag_err_pct": criteria.mag_err_pct,
            "err_db": criteria.err_db,
            "phase_err_deg": criteria.phase_err_deg,
            "peak_shift_pct": criteria.peak_shift_pct,
            "peak_mag_err_pct": criteria.peak_mag_err_pct,
            "require": args.require,
        },
        "reference_node": {"mode": spec.mode, "ports": spec.ports},
        "candidates": [c.to_dict() for c in candidates],
        "ranking": [c.label for c in ranked],
        "best": winner.to_dict() if winner else None,
        "any_accepted": winner is not None,
    }
    json_path = args.json or os.path.join(out_dir, "compare.json")
    with open(json_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)
        fh.write("\n")

    if not args.quiet:
        if winner is None:
            print(f"\nno candidate reached {args.require}")
        else:
            renorm = "" if winner.renorm_ohm is None else f" (renorm {winner.renorm_ohm:g} ohm)"
            print(
                f"\nbest: {winner.label}{renorm} - {winner.status}, "
                f"headroom {winner.headroom:.4g}"
            )
    print(f"json: {json_path}")
    return 0 if winner is not None else 1


if __name__ == "__main__":
    raise SystemExit(main())
