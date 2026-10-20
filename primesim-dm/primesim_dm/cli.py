"""Command line entry point: ``python3 -m primesim_dm <command>``.

Three commands, one job: read a deck that already exists and say what is
wrong with it.  ``lint`` reports, ``graph`` draws the same connectivity,
``terminate`` writes the terminations for the nets nothing else touches.
Nothing here generates a deck.
"""

import argparse
import os
import re
import sys

from . import check as check_mod
from . import deck as deck_mod
from . import graph as graph_mod

__version__ = "0.1.0"


def _err(msg):
    sys.stderr.write("error: %s\n" % msg)
    return 2


# ---------------------------------------------------------------- lint
def cmd_lint(args):
    opaque = args.opaque or None
    if args.opaque and not args.no_default_opaque:
        opaque = list(deck_mod.DEFAULT_OPAQUE) + args.opaque
    elif args.no_default_opaque:
        opaque = args.opaque or []
    dk = deck_mod.read(args.deck,
                       follow_includes=not args.no_includes,
                       search_dirs=args.search_dir or [],
                       opaque=opaque, skip=args.skip or [],
                       max_depth=args.max_depth, profile=args.profile)
    if not dk.files:
        return _err("could not read any of: %s" % ", ".join(args.deck))
    checker = check_mod.Checker(dk, short_ohms=args.short_ohms,
                                keep_nets=args.keep_net or [],
                                force_connectivity=args.force_connectivity,
                                si=args.si, si_expect=args.si_expect,
                                si_ignore_passives=args.si_ignore_passives)
    findings = checker.run()
    counts = checker.counts()
    report = check_mod.render(
        dk, findings, counts, verbose=args.verbose,
        limit=0 if args.all else args.limit, summary_only=args.summary)
    sys.stdout.write(report)
    if args.output:
        # the full report, whatever the screen was trimmed to: a saved check
        # is there to be diffed against the next run
        full = check_mod.render(dk, findings, counts, verbose=True, limit=0)
        parent = os.path.dirname(os.path.abspath(args.output))
        if parent and not os.path.isdir(parent):
            os.makedirs(parent)
        with open(args.output, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(full)
        print("wrote %s" % args.output)
    if counts[check_mod.SEV_ERROR]:
        return 1
    if args.strict and counts[check_mod.SEV_WARN]:
        return 1
    return 0


# ---------------------------------------------------------------- terminate
def _read_deck(args):
    opaque = args.opaque or None
    if args.opaque and not args.no_default_opaque:
        opaque = list(deck_mod.DEFAULT_OPAQUE) + args.opaque
    elif args.no_default_opaque:
        opaque = args.opaque or []
    return deck_mod.read(args.deck,
                         follow_includes=not args.no_includes,
                         search_dirs=args.search_dir or [],
                         opaque=opaque, skip=args.skip or [],
                         max_depth=args.max_depth,
                         profile=getattr(args, "profile",
                                         deck_mod.DEFAULT_PROFILE))


def cmd_terminate(args):
    dk = _read_deck(args)
    if not dk.files:
        return _err("could not read any of: %s" % ", ".join(args.deck))
    if dk.missing_includes and not args.force:
        return _err(
            "%d include(s) could not be read, so nets that belong to them "
            "look one-sided. Terminating those would paper over the real "
            "problem - fix the includes, or pass --force if you mean it."
            % len(dk.missing_includes))

    checker = check_mod.Checker(dk, keep_nets=args.keep_net or [])
    floating = checker.floating_nets()
    if args.exclude:
        drop = [re.compile(p) for p in args.exclude]
        floating = [f for f in floating
                    if not any(rx.search(f[0]) for rx in drop)]

    text = check_mod.render_terminations(
        dk, floating, kind="none" if args.nodes_only else args.type,
        value=args.value, value2=args.value2, to=args.to,
        prefix=args.prefix, source=os.path.abspath(args.deck[0]),
        sort=args.sort)

    if args.output:
        parent = os.path.dirname(os.path.abspath(args.output))
        if parent and not os.path.isdir(parent):
            os.makedirs(parent)
        with open(args.output, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        print("wrote %s (%d node(s))" % (args.output, len(floating)))
        print("review it, then add:  .include '%s'" % args.output)
    else:
        sys.stdout.write(text)
    return 0


# -------------------------------------------------------------------- graph
def _graph_format(output):
    """-o deck.html means html; nobody should have to say it twice."""
    ext = os.path.splitext(output or "")[1].lower()
    return {".html": "html", ".htm": "html", ".dot": "dot",
            ".gv": "dot"}.get(ext, "svg")


def cmd_graph(args):
    dk = _read_deck(args)
    if not dk.files:
        return _err("could not read any of: %s" % ", ".join(args.deck))

    layout = None
    if args.layout and os.path.isfile(args.layout):
        with open(args.layout, encoding="utf-8", errors="replace") as fh:
            try:
                layout = graph_mod.load_layout(fh.read(), args.layout)
            except graph_mod.LayoutError as exc:
                return _err(str(exc))
    elif args.layout:
        print("%s does not exist yet - drawing the automatic layout. "
              "Arrange it in the viewer and save it there." % args.layout)

    rails = () if args.no_rails else (
        list(graph_mod.DEFAULT_RAILS) + (args.rail or []))
    fmt = args.format or _graph_format(args.output)
    g = graph_mod.build(dk, rails=rails, group_buses=not args.no_bus_groups,
                        max_elements=args.max_elements,
                        hide=args.hide or [], only=args.only or [],
                        hide_names=(layout.hidden if layout else []),
                        hide_nets=args.hide_net or [],
                        hide_net_names=(layout.hidden_nets if layout else []),
                        # the viewer can give hidden things back, so it is
                        # handed them; a static picture is not
                        keep_hidden=(fmt == "html"))

    # the picture is only as complete as the read that produced it, so it
    # says on its face what was left out - same reason lint prints a header
    header = ["%d element(s), %d net node(s)" % (len(g.boxes), len(g.nets))]
    if layout is not None:
        header.append("columns from %s" % os.path.basename(args.layout))
    floating = [n for n in g.nets if n.floating]
    if floating:
        header.append("%d one-sided net node(s), drawn in red"
                      % len(floating))
    if g.absorbed:
        header.append("%d passive(s) folded into their node, not drawn as "
                      "instances" % g.absorbed)
    if g.hidden:
        crossing = len([n for n in g.nets if n.crosses])
        header.append("%d instance(s) hidden; %d net(s) still drawn reach "
                      "them, dashed" % (g.hidden, crossing))
    if g.hidden_nets:
        note = "%d net(s) hidden" % g.hidden_nets
        if g.hidden_nets_floating:
            note += " - %d of those were one-sided" % g.hidden_nets_floating
        header.append(note)
    if g.lost:
        # hiding is not free, and a finding that left the picture is worth
        # more than a tidy picture
        note = ("%d net(s) dropped with them: nothing drawn is left to "
                "connect" % g.lost)
        if g.lost_floating:
            note += " - %d of those were one-sided" % g.lost_floating
        header.append(note)
    if dk.missing_includes:
        header.append("INCOMPLETE: %d include(s) could not be read"
                      % len(dk.missing_includes))
    if dk.skipped_files:
        header.append("%d file(s) skipped, their elements are not drawn"
                      % len(dk.skipped_files))
    if dk.off_profile:
        kinds = sorted(set(k for _p, _l, _t, k in dk.off_profile))
        header.append("%d line(s) are element types --profile %s does not "
                      "read (%s), so they are not drawn"
                      % (len(dk.off_profile), dk.profile, " ".join(kinds)))
    if dk.unparsed:
        header.append("%d line(s) could not be split into nodes, so they are "
                      "not drawn" % len(dk.unparsed))
    if g.dropped:
        header.append("%d least-connected element(s) not drawn - listed "
                      "under 'past --max-elements'; raise --max-elements "
                      "(or 0) to keep them" % g.dropped)

    title = os.path.basename(args.deck[0])
    if fmt == "dot":
        text = graph_mod.render_dot(g)
    elif fmt == "html":
        text = graph_mod.render_html(g, title=title, header=header,
                                     layout=layout)
    else:
        text = graph_mod.render_svg(g, title=title, header=header,
                                    layout=layout)
    if g.unplaced:
        header.append("%d instance(s) the layout does not place, in a column "
                      "of their own" % g.unplaced)

    if args.output:
        parent = os.path.dirname(os.path.abspath(args.output))
        if parent and not os.path.isdir(parent):
            os.makedirs(parent)
        with open(args.output, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        print("wrote %s" % args.output)
        for line in header:
            print("  %s" % line)
    else:
        sys.stdout.write(text)

    if args.save_layout:
        with open(args.save_layout, "w", encoding="utf-8",
                  newline="\n") as fh:
            fh.write(graph_mod.dump_layout(graph_mod.layout_of(g)))
        print("wrote %s" % args.save_layout)
    return 0


# ---------------------------------------------------------------- main
def build_parser():
    p = argparse.ArgumentParser(
        prog="primesim_dm",
        description="Check a PrimeSim deck that already exists: report its "
                    "connectivity (lint), draw it (graph), terminate the "
                    "nets nothing else touches (terminate).")
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="command")

    s = sub.add_parser("lint", help="check an existing deck (.sp) for "
                                    "connectivity and structural errors")
    s.add_argument("deck", nargs="+", help="deck file(s) to check")
    s.add_argument("--no-includes", action="store_true",
                   help="do not follow .include/.lib")
    s.add_argument("--search-dir", action="append")
    s.add_argument("--keep-net", action="append",
                   help="regex of nets to exempt from the floating check")
    s.add_argument("--short-ohms", type=float, default=1e-6,
                   help="resistance at or below which two nets are reported "
                        "as one node (default 1e-6)")
    s.add_argument("-v", "--verbose", action="store_true",
                   help="also show info notes")
    s.add_argument("--opaque", action="append", metavar="REGEX",
                   help="read matching files for their .subckt interfaces "
                        "only, never their insides (default: %s)"
                        % ", ".join(deck_mod.DEFAULT_OPAQUE))
    s.add_argument("--no-default-opaque", action="store_true",
                   help="do not apply the built-in opaque patterns")
    s.add_argument("--skip", action="append", metavar="REGEX",
                   help="do not open matching files at all (PDK model "
                        "libraries and the like)")
    s.add_argument("--max-depth", type=int, metavar="N",
                   help="follow includes at most N levels deep")
    s.add_argument("--profile", choices=sorted(deck_mod.PROFILES),
                   default=deck_mod.DEFAULT_PROFILE, help='which element types to read. "dm" (the default) reads only what a link deck is built from - subckt calls (channel broadband model, SPF IO model, package), IBIS buffers, S-parameter channels, W lines, R/L/C and sources - and reports anything else instead of guessing at it. "full" also reads transistors, diodes and controlled sources')
    s.add_argument("--force-connectivity", action="store_true",
                   help="run the floating/isolated checks even when includes "
                        "are missing (results will be noisy)")
    s.add_argument("--si", action="store_true",
                   help="SI rule: every signal node must join exactly two "
                        "elements, one driver and one receiver. Anything "
                        "else is an error. Off by default - a power or "
                        "control node breaks it legitimately")
    s.add_argument("--si-expect", type=int, default=2, metavar="N",
                   help="how many elements an SI node should join "
                        "(default 2)")
    s.add_argument("--si-ignore-passives", action="store_true",
                   help="do not count R/C/L towards the SI rule, so a node "
                        "with a driver, a receiver and a termination still "
                        "counts as two")
    s.add_argument("--summary", action="store_true",
                   help="counts per finding kind only, no individual lines")
    s.add_argument("--limit", type=int, default=10,
                   help="max findings to list per kind (default 10)")
    s.add_argument("--all", action="store_true",
                   help="list every finding, no per-kind cap")
    s.add_argument("-o", "--output", metavar="FILE",
                   help="also save the full report (every finding, info "
                        "notes included) to a file")
    s.add_argument("--strict", action="store_true",
                   help="exit non-zero on warnings too")
    s.set_defaults(func=cmd_lint)

    s = sub.add_parser("terminate",
                       help="emit R/C termination lines for the nodes "
                            "nothing else connects to")
    s.add_argument("deck", nargs="+")
    s.add_argument("-o", "--output", metavar="FILE",
                   help="write the .inc here instead of stdout")
    s.add_argument("--type", choices=("rload", "cload", "rc"),
                   default="rload",
                   help="rload: a resistor, cload: a capacitor, rc: both "
                        "(default rload)")
    s.add_argument("--value", default="1T",
                   help="resistance for rload/rc (default 1T)")
    s.add_argument("--value2", default="1f",
                   help="capacitance for cload/rc (default 1f)")
    s.add_argument("--to", default="0",
                   help="net the termination returns to (default 0)")
    s.add_argument("--prefix", default="Rterm_",
                   help="element name prefix (default Rterm_)")
    s.add_argument("--sort", choices=("net", "instance"), default="net",
                   help="net: one list ordered by net name (default, so a "
                        "saved file diffs against the next run). instance: "
                        "grouped under the instance each node came from, in "
                        "deck order then by port - the order you review in")
    s.add_argument("--nodes-only", action="store_true",
                   help="just list the nodes, generate no elements")
    s.add_argument("--exclude", action="append", metavar="REGEX",
                   help="leave nets matching this alone (repeatable)")
    s.add_argument("--keep-net", action="append", metavar="REGEX",
                   help="nets to exempt from the floating check (repeatable)")
    s.add_argument("--no-includes", action="store_true")
    s.add_argument("--search-dir", action="append")
    s.add_argument("--opaque", action="append", metavar="REGEX")
    s.add_argument("--no-default-opaque", action="store_true")
    s.add_argument("--skip", action="append", metavar="REGEX")
    s.add_argument("--max-depth", type=int, metavar="N")
    s.add_argument("--profile", choices=sorted(deck_mod.PROFILES),
                   default=deck_mod.DEFAULT_PROFILE,
                   help="which element types to read (see lint --help)")
    s.add_argument("--force", action="store_true",
                   help="proceed even though includes are missing")
    s.set_defaults(func=cmd_terminate)

    s = sub.add_parser("graph",
                       help="draw the connectivity that lint reads as an "
                            "SVG picture")
    s.add_argument("deck", nargs="+")
    s.add_argument("-o", "--output", metavar="FILE",
                   help="write here instead of stdout (.svg or .dot)")
    s.add_argument("--format", choices=("svg", "html", "dot"),
                   help="svg: a picture to embed in a report. html: the same "
                        "picture in a viewer that zooms, pans and searches - "
                        "what a big deck needs. dot: graphviz source. "
                        "Default: taken from the -o extension, else svg")
    s.add_argument("--rail", action="append", metavar="REGEX",
                   help="also treat matching nets as a supply rail: drawn as "
                        "a stub on each box, not as a wire (repeatable)")
    s.add_argument("--no-rails", action="store_true",
                   help="draw supplies as ordinary nets (expect a hairball)")
    s.add_argument("--no-bus-groups", action="store_true",
                   help="draw dq0..dq7 as eight nets rather than one bus")
    s.add_argument("--hide", action="append", metavar="REGEX",
                   help="leave instances matching this out of the picture - "
                        "matched on the instance name or the subckt it "
                        "calls (repeatable). Connectivity is still worked "
                        "out over the whole deck")
    s.add_argument("--only", action="append", metavar="REGEX",
                   help="draw only instances matching this (repeatable)")
    s.add_argument("--hide-net", action="append", metavar="REGEX",
                   help="leave nets matching this out of the picture - "
                        "matched on the drawn label or any net behind it "
                        "(repeatable)")
    s.add_argument("--layout", metavar="FILE",
                   help="column arrangement to draw with, as saved from the "
                        "html viewer. Missing file: the automatic layout is "
                        "drawn instead")
    s.add_argument("--save-layout", metavar="FILE",
                   help="write the arrangement being drawn to a layout file, "
                        "as a starting point to edit")
    s.add_argument("--max-elements", type=int,
                   default=graph_mod.DEFAULT_MAX_ELEMENTS, metavar="N",
                   help="draw at most N elements, the best-connected first, "
                        "sources kept ahead of the rest. 0 (the default) "
                        "draws every one of them")
    s.add_argument("--no-includes", action="store_true")
    s.add_argument("--search-dir", action="append")
    s.add_argument("--opaque", action="append", metavar="REGEX")
    s.add_argument("--no-default-opaque", action="store_true")
    s.add_argument("--skip", action="append", metavar="REGEX")
    s.add_argument("--max-depth", type=int, metavar="N")
    s.add_argument("--profile", choices=sorted(deck_mod.PROFILES),
                   default=deck_mod.DEFAULT_PROFILE,
                   help="which element types to read (see lint --help)")
    s.set_defaults(func=cmd_graph)

    return p


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 2
    return args.func(args)
