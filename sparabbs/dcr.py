"""Step 1: pick a reference node, pull DCR off the diagonal, size the renorm sweep.

Everything here is non-interactive and machine-readable, because the caller is
an agent running one script per step.  In particular the reference port is
resolved by *exact* name only - see :func:`resolve_port`.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from typing import List, Optional, Sequence

import numpy as np

from .compare import REF_PORT, ReferenceSpec, apply_reference, s_to_z
from .touchstone import Network, read_touchstone


class PortNotFound(ValueError):
    """Raised when a reference port name does not match the file exactly."""


@dataclass
class PortDcr:
    index: int  # 1-based index in the re-referenced network
    name: str
    dcr_ohm: float
    at_hz: float
    z_max_ohm: float
    z_max_at_hz: float
    settled: bool  # False when Re(Z) is still moving at the lowest point


@dataclass
class DcrReport:
    snp: str
    nports: int
    reference_index: int
    reference_name: str
    f_min_hz: float
    f_max_hz: float
    ports: List[PortDcr] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def dcr_values(self) -> List[float]:
        return [p.dcr_ohm for p in self.ports]

    def z_range(self) -> tuple:
        """(min DCR, max |Z|) - the span a broadband model has to reproduce."""
        lo = min((p.dcr_ohm for p in self.ports if p.dcr_ohm > 0), default=0.0)
        hi = max((p.z_max_ohm for p in self.ports), default=0.0)
        return lo, hi

    def to_dict(self) -> dict:
        data = asdict(self)
        lo, hi = self.z_range()
        data["z_min_ohm"] = lo
        data["z_max_ohm"] = hi
        data["renorm_center_ohm"] = geometric_center(lo, hi)
        return data


def resolve_port(net: Network, wanted: str) -> int:
    """Return the 1-based index of the port named exactly ``wanted``.

    Deliberately strict.  The caller is an agent relaying a human's choice of
    reference node, and a near-miss silently referencing the wrong rail would
    invalidate every number downstream while looking perfectly fine.  Case and
    surrounding whitespace are ignored; nothing else is.  A typo raises, with
    the available names, so the agent can ask again instead of guessing.
    """
    key = wanted.strip()
    if not key:
        raise PortNotFound("no reference port given; " + _names_hint(net))
    exact = [i for i, n in enumerate(net.port_names, 1) if n == key]
    if len(exact) == 1:
        return exact[0]
    folded = [i for i, n in enumerate(net.port_names, 1) if n.strip().lower() == key.lower()]
    if len(folded) == 1:
        return folded[0]
    if len(exact) > 1 or len(folded) > 1:
        raise PortNotFound(
            f"{key!r} matches more than one port, so it cannot identify one; "
            + _names_hint(net)
        )
    raise PortNotFound(f"no port is named {key!r}. " + _names_hint(net))


def _names_hint(net: Network) -> str:
    listed = ", ".join(f"{i}:{n}" for i, n in enumerate(net.port_names, 1))
    return f"the file's ports are: {listed}"


def extract(net: Network, reference: str, settle_tol: float = 0.05) -> DcrReport:
    """Re-reference to ``reference`` and read DCR off each remaining diagonal."""
    ref_index = resolve_port(net, reference)
    z = s_to_z(net.s, net.z0)
    spec = ReferenceSpec(mode=REF_PORT, ports=[ref_index])
    z_ref, names = apply_reference(z, net.port_names, spec)

    report = DcrReport(
        snp=net.path,
        nports=net.nports,
        reference_index=ref_index,
        reference_name=net.port_names[ref_index - 1],
        f_min_hz=float(net.freq[0]),
        f_max_hz=float(net.freq[-1]),
    )
    if net.freq[0] > 1e6:
        report.warnings.append(
            f"the lowest frequency in the file is {net.freq[0]:.4g} Hz; DCR read "
            "there is an extrapolation at best"
        )

    for k, name in enumerate(names):
        diag = z_ref[:, k, k]
        dcr = float(np.real(diag[0]))
        settled = True
        if diag.size > 1:
            nxt = float(np.real(diag[1]))
            denom = max(abs(dcr), abs(nxt), 1e-30)
            settled = abs(nxt - dcr) / denom <= settle_tol
        peak_at = int(np.argmax(np.abs(diag)))
        if not settled:
            report.warnings.append(
                f"{name}: Re(Z) is still changing between the two lowest points, "
                "so this DCR is not a settled value"
            )
        report.ports.append(
            PortDcr(
                index=k + 1,
                name=name,
                dcr_ohm=dcr,
                at_hz=float(net.freq[0]),
                z_max_ohm=float(np.abs(diag[peak_at])),
                z_max_at_hz=float(net.freq[peak_at]),
                settled=settled,
            )
        )
    return report


def geometric_center(z_min: float, z_max: float) -> float:
    """The reference impedance that minimises the worst-case S->Z error.

    A relative error dS in S shows up in Z amplified by
    ``(Z + z0)^2 / (2*z0*Z)``, which is smallest at ``z0 = Z``.  Over a range
    of impedances the worst case is minimised where the two ends balance, at
    ``sqrt(Zmin * Zmax)``, and the amplification there is ``sqrt(Zmax/Zmin)/2``.
    That is why 50 ohm is hopeless for a milliohm PDN: it sits four decades off
    centre, and a fit that looks excellent in S is worthless in Z.
    """
    if z_min <= 0 or z_max <= 0:
        return 0.0
    return float(np.sqrt(z_min * z_max))


def recommend_renorm(
    report: DcrReport, count: int = 5, step_decades: float = 0.5
) -> List[float]:
    """A ladder of renorm impedances to try, centred on the geometric centre.

    Anchoring the sweep on DCR alone aims at the bottom of the range; the centre
    is typically one to two decades above it.  Half-decade steps are fine
    because the amplification curve is shallow near its minimum.
    """
    lo, hi = report.z_range()
    center = geometric_center(lo, hi)
    if center <= 0:
        return []
    half = (count - 1) / 2.0
    values = [center * 10 ** (step_decades * (k - half)) for k in range(count)]
    return [float(f"{v:.3g}") for v in values]


def label_for(value: float) -> str:
    """0.01 -> '0p01', the naming the NDE runs use."""
    text = f"{value:g}".replace("-", "m")
    return text.replace(".", "p")


# --------------------------------------------------------------------------
# command line
# --------------------------------------------------------------------------


def build_parser():
    import argparse

    p = argparse.ArgumentParser(
        prog="sparabbs.dcr",
        description="List a Touchstone file's ports, or extract DCR against a "
        "chosen reference port and recommend renorm impedances.",
    )
    p.add_argument("--snp", required=True, help="the reference Touchstone file")
    p.add_argument(
        "--list-ports",
        action="store_true",
        help="print the port names exactly as the file states them, and stop",
    )
    p.add_argument(
        "--reference",
        default="",
        help="reference port, by exact name (preferred) or 1-based index",
    )
    p.add_argument("--json", default="", help="write the report here as JSON")
    p.add_argument("--renorm-count", type=int, default=5)
    p.add_argument("--renorm-step-decades", type=float, default=0.5)
    return p


def main(argv: Optional[Sequence[str]] = None) -> int:
    import sys

    args = build_parser().parse_args(argv)
    try:
        net = read_touchstone(args.snp)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.list_ports:
        payload = {
            "snp": net.path,
            "nports": net.nports,
            "ports": [
                {"index": i, "name": n} for i, n in enumerate(net.port_names, 1)
            ],
            "port_names_are_generic": all(
                n == f"P{i}" for i, n in enumerate(net.port_names, 1)
            ),
        }
        if args.json:
            _write_json(args.json, payload)
        print(json.dumps(payload, indent=2))
        return 0

    if not args.reference:
        print(
            "error: --reference is required (or use --list-ports). "
            + _names_hint(net),
            file=sys.stderr,
        )
        return 2

    wanted = args.reference
    if wanted.strip().isdigit():  # an index is unambiguous, so allow it
        idx = int(wanted.strip())
        if not 1 <= idx <= net.nports:
            print(f"error: port index {idx} is outside 1..{net.nports}", file=sys.stderr)
            return 2
        wanted = net.port_names[idx - 1]

    try:
        report = extract(net, wanted)
    except PortNotFound as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 3

    payload = report.to_dict()
    payload["renorm_impedances"] = recommend_renorm(
        report, args.renorm_count, args.renorm_step_decades
    )
    payload["renorm_labels"] = [label_for(v) for v in payload["renorm_impedances"]]
    if args.json:
        _write_json(args.json, payload)

    print(f"reference port : {report.reference_index}  {report.reference_name}")
    print(f"{'port':>4} {'name':<20} {'DCR (ohm)':>12} {'max |Z| (ohm)':>14} {'at (Hz)':>12}")
    for p in report.ports:
        flag = "" if p.settled else "  (not settled)"
        print(
            f"{p.index:>4} {p.name:<20} {p.dcr_ohm:>12.6g} {p.z_max_ohm:>14.6g} "
            f"{p.z_max_at_hz:>12.4g}{flag}"
        )
    lo, hi = report.z_range()
    print(f"\n|Z| span {lo:.4g} .. {hi:.4g} ohm, geometric centre "
          f"{payload['renorm_center_ohm']:.4g} ohm")
    print("recommended renorm impedances: "
          + ", ".join(f"{v:g}" for v in payload["renorm_impedances"]))
    print("labels for the NDE runs       : " + ", ".join(payload["renorm_labels"]))
    for w in report.warnings:
        print(f"warning: {w}", file=sys.stderr)
    if args.json:
        print(f"\njson: {os.path.abspath(args.json)}")
    return 0


def _write_json(path: str, payload: dict) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)
        fh.write("\n")


if __name__ == "__main__":
    raise SystemExit(main())
