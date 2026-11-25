"""MCP server: the deck checks as tools an assistant can call one at a time.

    python3 -m primesim_dm.mcp_server [--skip REGEX] [--opaque REGEX] ...

Speaks MCP over stdio, so the client (opencode, Claude Code, ...) starts it
as a subprocess on the same machine as the decks.  Needs the ``mcp`` package
(Python 3.10+); the rest of primesim-dm still needs nothing but the stdlib.

Read options (``--search-dir`` / ``--opaque`` / ``--skip`` / ``--max-depth``
/ ``--profile`` / ``--keep-net``) are given once here, in the client's
launch config, because they describe the site rather than the question.
Every tool is read-only: they answer from the deck and never write to it.
"""

import argparse
import os
import sys

from . import cli as cli_mod
from . import deck as deck_mod
from . import tools

try:                                    # mcp 2.x
    from mcp.server import MCPServer as _Server
    from mcp.server.mcpserver.exceptions import ToolError
except ImportError:                     # mcp 1.x
    try:
        from mcp.server.fastmcp import FastMCP as _Server
        from mcp.server.fastmcp.exceptions import ToolError
    except ImportError:
        _Server = None
        ToolError = ValueError

INSTRUCTIONS = """\
Checks an existing PrimeSim / HSPICE deck (.sp) for connectivity problems.
Read-only: nothing here edits a deck. Every tool takes deck_path, an absolute
path to the top deck file.
Start with deck_summary. For signal integrity decks use check_si_fanout:
a signal net should join exactly 2 elements (driver + receiver). Use
net_info and instance_info to trace a reported net or instance.
If missing_includes is not empty, the netlist is incomplete and one-sided
nets may be caused by that; report it before anything else.
"""


def _safe(fn):
    """Report a DeckError as a tool error with its own message.

    ToolError marks the result as an error for the client (isError) and,
    unlike any other exception, passes the message through, so the
    assistant reads "no such net; similar: ..." rather than a bare failure.
    """
    try:
        return fn()
    except tools.DeckError as exc:
        raise ToolError(str(exc))


def build(opts):
    if _Server is None:
        raise SystemExit("the 'mcp' package is not installed: "
                         "pip install mcp  (needs Python 3.10+)")
    server = _Server("primesim-dm", instructions=INSTRUCTIONS)

    @server.tool()
    def deck_summary(deck_path: str, si: bool = False) -> dict:
        """Overview of a deck: files, elements and nets read, missing
        includes, and how many lint findings of each kind there are.
        Call this first. si=true also counts SI fan-out errors."""
        return _safe(lambda: tools.deck_summary(deck_path, opts, si=si))

    @server.tool()
    def check_si_fanout(deck_path: str, expect: int = 2,
                        keep_net: list[str] | None = None,
                        ignore_passives: bool = False,
                        limit: int = 50) -> dict:
        """Signal nets that do not join exactly `expect` elements (default
        2: one driver, one receiver). count 1 = not connected somewhere,
        3+ = an unintended branch (stub). keep_net: regexes of nets to leave
        out, e.g. power nets like ["^vdd", "^vss"]. ignore_passives: do not
        count R/C/L."""
        return _safe(lambda: tools.check_si_fanout(
            deck_path, opts, expect=expect, keep_net=keep_net,
            ignore_passives=ignore_passives, limit=limit))

    @server.tool()
    def find_floating_nets(deck_path: str,
                           keep_net: list[str] | None = None,
                           limit: int = 50) -> dict:
        """Nets that exactly one element touches (one-sided nets), with the
        element and port. These are what terminate would terminate."""
        return _safe(lambda: tools.find_floating_nets(
            deck_path, opts, keep_net=keep_net, limit=limit))

    @server.tool()
    def lint_findings(deck_path: str, code: str | None = None,
                      severity: str | None = None, si: bool = False,
                      limit: int = 50) -> dict:
        """lint's findings as a list, optionally only one code (for example
        missing-include, undefined-subckt, port-count, duplicate-name) or
        one severity (error, warn, info)."""
        return _safe(lambda: tools.lint_findings(
            deck_path, opts, code=code, severity=severity, si=si,
            limit=limit))

    @server.tool()
    def net_info(deck_path: str, net: str) -> dict:
        """Every element on one net, with the port index it uses. Use it to
        trace a net that another tool reported."""
        return _safe(lambda: tools.net_info(deck_path, opts, net))

    @server.tool()
    def instance_info(deck_path: str, name: str) -> dict:
        """One element (instance): its type, the subckt it calls, and for
        each port the net and how many elements share that net."""
        return _safe(lambda: tools.instance_info(deck_path, opts, name))

    @server.tool()
    def make_termination(deck_path: str, kind: str = "rload",
                         value: str = "1T", value2: str = "1f",
                         keep_net: list[str] | None = None,
                         exclude: list[str] | None = None) -> dict:
        """Termination lines for the one-sided nets, returned as text (no
        file is written). kind: rload (resistor, `value`) or cload
        (capacitor, `value2`). Show the text to the user to review."""
        return _safe(lambda: tools.make_termination(
            deck_path, opts, kind=kind, value=value, value2=value2,
            keep_net=keep_net, exclude=exclude))

    @server.tool()
    def render_graph(deck_path: str, output_path: str) -> dict:
        """Draw the deck's connectivity as an HTML viewer the user opens in
        a browser. output_path: absolute path ending in .html."""
        if not (os.path.isabs(output_path)
                and output_path.lower().endswith(".html")):
            raise ToolError("output_path must be an absolute .html path")
        # cli prints its own progress; keep stdout for the protocol
        argv = ["graph", deck_path, "-o", output_path]
        for d in opts.search_dirs:
            argv += ["--search-dir", d]
        for p in opts.opaque[len(deck_mod.DEFAULT_OPAQUE):]:
            argv += ["--opaque", p]
        for p in opts.skip:
            argv += ["--skip", p]
        if opts.max_depth is not None:
            argv += ["--max-depth", str(opts.max_depth)]
        argv += ["--profile", opts.profile]
        saved, sys.stdout = sys.stdout, sys.stderr
        try:
            code = cli_mod.main(argv)
        finally:
            sys.stdout = saved
        if code:
            raise ToolError("graph failed (exit %s)" % code)
        return {"written": output_path}

    return server


def main(argv=None):
    p = argparse.ArgumentParser(prog="primesim_dm.mcp_server",
                                description=__doc__.split("\n")[0])
    p.add_argument("--search-dir", action="append", default=[])
    p.add_argument("--opaque", action="append", default=[], metavar="REGEX")
    p.add_argument("--skip", action="append", default=[], metavar="REGEX")
    p.add_argument("--max-depth", type=int, metavar="N")
    p.add_argument("--profile", choices=sorted(deck_mod.PROFILES),
                   default=deck_mod.DEFAULT_PROFILE)
    p.add_argument("--keep-net", action="append", default=[], metavar="REGEX",
                   help="nets every tool leaves out, e.g. site power nets")
    args = p.parse_args(argv)
    opts = tools.ReadOptions(search_dirs=args.search_dir, opaque=args.opaque,
                             skip=args.skip, max_depth=args.max_depth,
                             profile=args.profile, keep_nets=args.keep_net)
    build(opts).run("stdio")


if __name__ == "__main__":
    main()
