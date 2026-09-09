"""Headless driver - the same engine the GUI uses, for batch and CI runs."""

from __future__ import annotations

import argparse
import os
import sys
import time

from .compare import (  # 'compare' is also re-exported as a function
    FAIL,
    REF_GLOBAL,
    REF_MODES,
    Criteria,
    ReferenceSpec,
    compare,
)
from . import deck as deck_mod
from . import report as report_mod
from . import runner as runner_mod
from .netlist import read_netlist
from .touchstone import read_touchstone


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="sparabbs",
        description="Validate a BBS model by re-extracting S-parameters and "
        "comparing Z against the original Touchstone file.",
    )
    p.add_argument("--snp", required=True, help="reference Touchstone file (a.snp)")
    p.add_argument("--bbs", required=True, help="BBS SPICE netlist (a_sp.sp)")
    p.add_argument("--out", default="sparabbs_run", help="working directory")
    p.add_argument("--subckt", default="", help="subcircuit name (default: auto)")
    p.add_argument(
        "--z0",
        type=float,
        default=None,
        help="reference impedance when the .snp does not state one",
    )
    p.add_argument(
        "--ground-pins",
        default="",
        help="comma-separated subcircuit pins to tie to global 0",
    )
    p.add_argument(
        "--float-pins", default="", help="comma-separated pins to leave floating"
    )
    p.add_argument("--cmd", default=runner_mod.DEFAULT_COMMAND, help="run command")
    p.add_argument("--cpu", type=int, default=4)
    p.add_argument("--no-run", action="store_true", help="write the deck and stop")
    p.add_argument(
        "--use-snp", default="", help="skip the run and compare this .sNp instead"
    )
    p.add_argument("--ref-mode", default=REF_GLOBAL, choices=REF_MODES)
    p.add_argument(
        "--ref-ports", default="", help="comma-separated port indices for --ref-mode"
    )
    p.add_argument("--align", default="intersect", choices=("exact", "intersect", "interp"))
    p.add_argument("--mag-err-pct", type=float, default=5.0)
    p.add_argument("--err-db", type=float, default=0.5)
    p.add_argument("--phase-err-deg", type=float, default=5.0)
    p.add_argument("--peak-shift-pct", type=float, default=2.0)
    p.add_argument("--peak-mag-err-pct", type=float, default=10.0)
    p.add_argument("--xml", default="", help="report path (default: <out>/report.xml)")
    p.add_argument("--junit", default="", help="also write JUnit XML here")
    return p


def _split(text: str) -> list[str]:
    return [t.strip() for t in text.split(",") if t.strip()]


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    out_dir = os.path.abspath(args.out)
    os.makedirs(out_dir, exist_ok=True)

    ref = read_touchstone(args.snp)
    if not ref.z0_from_file:
        if args.z0 is None:
            print(
                f"error: {args.snp} does not state a reference impedance; "
                "pass --z0",
                file=sys.stderr,
            )
            return 2
        ref.z0[:] = args.z0
    elif args.z0 is not None:
        ref.z0[:] = args.z0
    print(f"reference: {ref.describe()}")

    info = read_netlist(args.bbs)
    subckt = info.by_name(args.subckt) if args.subckt else info.top_candidates()[0]
    print(f"BBS model: .subckt {subckt.name} ({subckt.npins} pins)")

    assignments = deck_mod.default_assignments(subckt, ref.nports)
    grounds = {g.lower() for g in _split(args.ground_pins)}
    floats = {f.lower() for f in _split(args.float_pins)}
    port_no = 0
    for a in assignments:
        if a.pin.lower() in grounds:
            a.role, a.port = deck_mod.ROLE_GROUND, 0
        elif a.pin.lower() in floats:
            a.role, a.port = deck_mod.ROLE_FLOAT, 0
    for a in assignments:  # renumber ports in pin order
        if a.role == deck_mod.ROLE_PORT:
            port_no += 1
            a.port = port_no

    cfg = deck_mod.config_from_inputs(
        ref, subckt, args.bbs, out_dir, assignments=assignments
    )
    problems = deck_mod.validate(cfg)
    if problems:
        for pb in problems:
            print(f"error: {pb}", file=sys.stderr)
        return 2
    deck_path = deck_mod.write_deck(cfg)
    print(f"deck written: {deck_path}")
    if args.no_run:
        return 0

    command = ""
    if args.use_snp:
        dut_path = os.path.abspath(args.use_snp)
    else:
        spec = runner_mod.RunSpec(
            deck_path=deck_path, command_template=args.cmd, cpu=args.cpu, cwd=out_dir
        )
        command = spec.display()
        print(f"running: {command}")
        started = time.time()
        res = runner_mod.run(spec, on_line=lambda line: print(f"  | {line}"))
        print(f"finished in {res.seconds:.1f}s, exit code {res.returncode}")
        dut_path = runner_mod.find_output_snp(
            out_dir, cfg.nports, cfg.expected_snp(), started
        )
        if not dut_path:
            for line in runner_mod.scan_log_for_errors(res.log):
                print(f"  ! {line}", file=sys.stderr)
            print(
                f"error: no .s{cfg.nports}p was produced in {out_dir}", file=sys.stderr
            )
            return 3
    print(f"BBS result: {dut_path}")

    dut = read_touchstone(dut_path)
    criteria = Criteria(
        mag_err_pct=args.mag_err_pct,
        err_db=args.err_db,
        phase_err_deg=args.phase_err_deg,
        peak_shift_pct=args.peak_shift_pct,
        peak_mag_err_pct=args.peak_mag_err_pct,
    )
    spec_ref = ReferenceSpec(
        mode=args.ref_mode, ports=[int(p) for p in _split(args.ref_ports)]
    )
    result = compare(
        ref, dut, criteria=criteria, reference=spec_ref, align_mode=args.align
    )

    xml_path = args.xml or os.path.join(out_dir, "report.xml")
    tree = report_mod.build_xml(result, ref, dut, deck_path=deck_path, command=command)
    report_mod.write_xml(tree, xml_path)
    print(f"report: {xml_path}")
    if args.junit:
        report_mod.write_junit(result, args.junit)
        print(f"junit: {args.junit}")

    counts = result.counts()
    print(
        f"{result.status}: {counts['PASS']} pass, {counts['WARN']} warn, "
        f"{counts['FAIL']} fail across {len(result.terms)} Z terms"
    )
    return 0 if result.status != FAIL else 1


if __name__ == "__main__":
    raise SystemExit(main())
