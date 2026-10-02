"""Headless driver: a saved project in, HTML plots and CSV values out.

    python -m chanxt.cli project.json --html out.html --csv out.csv
    python -m chanxt.cli project.json --preset "Byte0 WRITE" --list
    python -m chanxt.cli project.json --model revB=/new/path/revB.s41p --html cmp.html
"""

from __future__ import annotations

import argparse
import math
import sys

from . import export
from .project import Project
from .select import FilterSpec, default_spec, preview, run_filter
from .units import parse_freq_list
from .workflow import build, load_networks


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="chanxt", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("project", help="project .json saved from the GUI")
    ap.add_argument("--model", action="append", default=[], metavar="ALIAS=PATH",
                    help="use a different .sNp for a model (repeatable)")
    ap.add_argument("--preset", help="selection preset saved in the project "
                    "(default: the project's last selection, else a basic view)")
    ap.add_argument("--split", help="split panels by: comma list of "
                    + ", ".join(export.SPLIT_DIMS))
    ap.add_argument("--html", help="write the plot report here")
    ap.add_argument("--csv", help="write values at frequencies here")
    ap.add_argument("--freqs", help="frequencies for --csv, e.g. '2.67, 5.33 GHz'")
    ap.add_argument("--no-nyquist", action="store_true", help="omit the Nyquist column")
    ap.add_argument("--list", action="store_true", help="print the selected curves")
    args = ap.parse_args(argv)

    try:
        project = Project.load(args.project)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    overrides = {}
    for item in args.model:
        alias, _, path = item.partition("=")
        if not path:
            print(f"error: --model wants ALIAS=PATH, got {item!r}", file=sys.stderr)
            return 2
        overrides[alias] = path
    try:
        networks = load_networks(project, overrides)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    analysis = build(project, networks)

    if args.preset:
        if args.preset not in project.presets:
            names = ", ".join(sorted(project.presets)) or "none saved"
            print(f"error: no preset {args.preset!r} ({names})", file=sys.stderr)
            return 2
        spec = FilterSpec.from_dict(project.presets[args.preset])
    elif "_last" in project.presets:
        spec = FilterSpec.from_dict(project.presets["_last"])
    else:
        spec = default_spec(analysis)
    models = [m for m in spec.models if m in analysis.models] or list(analysis.models)
    spec.models = models
    keys = run_filter(analysis, spec)
    print(f"{len(keys)} curve(s) selected over {len(models)} model(s)")

    if args.list:
        for row in preview(analysis, keys, models):
            vals = "  ".join(
                f"{m}={'-' if math.isnan(v) else f'{v:.2f}'}" for m, v in row.values.items()
            )
            print(f"  {row.label:<48} Nyq {row.nyquist_ghz:g} GHz  {vals}")

    split = (args.split.split(",") if args.split
             else list(project.plot.get("split_by", ["metric"])))
    bad = [d for d in split if d and d not in export.SPLIT_DIMS]
    if bad:
        print(f"error: unknown split {bad}; use {', '.join(export.SPLIT_DIMS)}", file=sys.stderr)
        return 2
    if args.html:
        export.write_html(args.html, analysis, keys, models, [d for d in split if d])
        print(f"wrote {args.html}")
    if args.csv:
        try:
            freqs = parse_freq_list(args.freqs if args.freqs is not None
                                    else str(project.plot.get("freqs", "")))
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        header, rows = export.extract_table(analysis, keys, models, freqs,
                                            include_nyquist=not args.no_nyquist)
        export.write_csv(args.csv, header, rows)
        print(f"wrote {args.csv} ({len(rows)} rows)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
