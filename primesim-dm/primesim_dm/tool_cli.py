"""The script side of the in-house MCP layout: one script per tool, JSON out.

The in-house ``MCP.py`` holds the ``@mcp.tool()`` definitions, and each tool
body hands its arguments to ``run_script(<script>, args)``, which runs that
tool's script from the ``scripts/`` folder.  ``mcp/scripts/dm_*.py`` are
those scripts for primesim-dm, one per tool; each is a few lines that call
:func:`main` here with its own command, so the logic stays in the package
and under its tests.  The package travels with them: it is copied into
``scripts/`` as a resource and found from ``Path(__file__).parent``.

    dm_check_si_fanout.py --deck /abs/top.sp [--json] [site options] ...

Follows the in-house script rules:

* every value comes in as an option; nothing is ever asked for interactively
* with ``--json`` the last line of stdout is the result as one line of JSON:
  ``status`` "ok" with a one-line ``summary`` and the answer, or ``status``
  "error" with a ``message``; without it, the same result is printed for
  a person to read
* progress and debug messages go to stderr
* exit 0 on success, 2 on failure
* every path in the result is absolute, and a relative user path is refused
* ``--output`` is written only if it does not exist yet or ``--overwrite``
  is given; the deck itself is never written
* a run is cut off after ``--timeout`` seconds (default 55, inside the 60 the
  caller allows) and reports that instead of being killed silently

A message the user has to answer starts with "[사용자 확인 필요]".  Stdlib
only, like the rest of the package.
"""

import argparse
import io
import json
import os
import signal
import sys
import time

from . import cli as cli_mod
from . import deck as deck_mod
from . import tools

NEED_USER = "[사용자 확인 필요]"
FAIL = 2

# script name -> (command, the options that command takes)
SCRIPTS = {
    "dm_deck_summary": ("summary", ("si",)),
    "dm_check_si_fanout": ("si-fanout", ("expect", "ignore_passives",
                                         "limit")),
    "dm_find_floating_nets": ("floating", ("limit",)),
    "dm_lint_findings": ("findings", ("code", "severity", "si", "limit")),
    "dm_net_info": ("net", ("net",)),
    "dm_instance_info": ("instance", ("name",)),
    "dm_save_lint_report": ("lint-report", ("output", "si")),
    "dm_make_termination": ("terminate", ("output", "kind", "value",
                                          "value2", "exclude")),
    "dm_render_graph": ("graph", ("output",)),
}
COMMANDS = tuple(cmd for cmd, _opts in SCRIPTS.values())

_OPTIONS = {
    "si": (("--si",), dict(action="store_true",
                           help="also apply the SI rule (2 elements a net)")),
    "expect": (("--expect",), dict(type=int, default=2,
                                   help="elements a signal net should join")),
    "ignore_passives": (("--ignore-passives",), dict(
        action="store_true", help="do not count R/C/L")),
    "limit": (("--limit",), dict(type=int, default=50,
                                 help="most items to list (default 50)")),
    "code": (("--code",), dict(help="only this finding code")),
    "severity": (("--severity",), dict(choices=("error", "warn", "info"))),
    "net": (("--net",), dict(help="net name")),
    "name": (("--name",), dict(help="instance name")),
    "output": (("--output",), dict(help="absolute path to write")),
    "kind": (("--kind",), dict(choices=("rload", "cload"), default="rload")),
    "value": (("--value",), dict(default="1T", help="resistor value")),
    "value2": (("--value2",), dict(default="1f", help="capacitor value")),
    "exclude": (("--exclude",), dict(action="append", default=[],
                                     metavar="REGEX",
                                     help="nets not to terminate")),
}


def build_parser(script):
    """The options one tool's script takes: the common ones and its own."""
    command, own = SCRIPTS[script]
    p = argparse.ArgumentParser(prog=script + ".py")
    p.set_defaults(command=command, output=None, overwrite=False, si=False,
                   expect=2, ignore_passives=False, limit=50, code=None,
                   severity=None, net=None, name=None, kind="rload",
                   value="1T", value2="1f", exclude=[])
    p.add_argument("--deck", required=True, help="absolute path of the deck")
    p.add_argument("--json", action="store_true",
                   help="print the result as one line of JSON, last on stdout")
    p.add_argument("--timeout", type=int, default=55, metavar="SEC",
                   help="give up after SEC seconds (default 55)")
    # site options: where the models live, which trees to keep out
    p.add_argument("--search-dir", action="append", default=[])
    p.add_argument("--opaque", action="append", default=[], metavar="REGEX")
    p.add_argument("--skip", action="append", default=[], metavar="REGEX")
    p.add_argument("--max-depth", type=int, metavar="N")
    p.add_argument("--profile", choices=sorted(deck_mod.PROFILES),
                   default=deck_mod.DEFAULT_PROFILE)
    p.add_argument("--keep-net", action="append", default=[], metavar="REGEX")
    for key in own:
        flags, kw = _OPTIONS[key]
        p.add_argument(*flags, **kw)
    if "output" in own:
        p.add_argument("--overwrite", action="store_true",
                       help="replace --output if it is already there")
    return p


class Refused(Exception):
    """A request this script will not carry out as given."""


def _path(value, what, must_exist=False):
    if not value or not os.path.isabs(value):
        raise Refused("%s %s 은(는) 절대경로여야 합니다: %s. 사용자에게 "
                      "절대경로를 물어보세요." % (NEED_USER, what, value))
    if must_exist and not os.path.isfile(value):
        raise Refused("%s %s 이(가) 없습니다: %s. 경로가 맞는지 사용자에게 "
                      "확인하세요." % (NEED_USER, what, value))
    return value


def _check_output(path, overwrite, suffix=None):
    _path(path, "저장 경로(output)")
    if suffix and not path.lower().endswith(suffix):
        raise Refused("%s 저장 경로는 %s 로 끝나야 합니다: %s"
                      % (NEED_USER, suffix, path))
    if os.path.exists(path) and not overwrite:
        raise Refused("%s 파일이 이미 있습니다: %s. 덮어쓸지(overwrite) 다른 "
                      "경로를 쓸지 사용자에게 물어보세요." % (NEED_USER, path))
    return path


def _ok(summary, result):
    out = {"status": "ok", "summary": summary}
    out.update(result)
    return out


def run(args):
    """Carry out one command; return the JSON-able result."""
    deck = _path(args.deck, "덱 경로(deck)", must_exist=True)
    opts = tools.ReadOptions(search_dirs=args.search_dir, opaque=args.opaque,
                             skip=args.skip, max_depth=args.max_depth,
                             profile=args.profile, keep_nets=args.keep_net)
    name = os.path.basename(deck)
    cmd = args.command

    if cmd == "summary":
        r = tools.deck_summary(deck, opts, si=args.si)
        return _ok("%s: 소자 %d개, 노드 %d개, error %d / warn %d"
                   % (name, r["elements"], r["nets"], r["errors"],
                      r["warnings"]), r)
    if cmd == "si-fanout":
        r = tools.check_si_fanout(deck, opts, expect=args.expect,
                                  ignore_passives=args.ignore_passives,
                                  limit=args.limit)
        return _ok("소자 수가 %d개가 아닌 노드 %d개 (부족 %d, 초과 %d)"
                   % (args.expect, r["total"], r["too_few"], r["too_many"]),
                   r)
    if cmd == "floating":
        r = tools.find_floating_nets(deck, opts, limit=args.limit)
        return _ok("한쪽만 연결된 노드 %d개" % r["total"], r)
    if cmd == "findings":
        r = tools.lint_findings(deck, opts, code=args.code,
                                severity=args.severity, si=args.si,
                                limit=args.limit)
        return _ok("조건에 맞는 문제 %d개" % r["total"], r)
    if cmd == "net":
        if not args.net:
            raise Refused("%s 볼 노드 이름(net)이 없습니다. 사용자에게 "
                          "물어보세요." % NEED_USER)
        r = tools.net_info(deck, opts, args.net)
        return _ok("노드 %s 에 소자 %d개" % (r["net"], r["element_count"]), r)
    if cmd == "instance":
        if not args.name:
            raise Refused("%s 볼 인스턴스 이름(name)이 없습니다. 사용자에게 "
                          "물어보세요." % NEED_USER)
        r = tools.instance_info(deck, opts, args.name)
        return _ok("%s (%s), 포트 %d개" % (r["element"],
                                          r["subckt"] or r["kind"],
                                          len(r["ports"])), r)

    # the commands below write --output
    if cmd == "lint-report":
        out = _check_output(args.output, args.overwrite)
        r = tools.lint_report(deck, opts, si=args.si)
        tools.write_output(out, r.pop("text"), overwrite=True)
        r["output"] = out
        return _ok("lint 리포트 저장: error %d / warn %d"
                   % (r["errors"], r["warnings"]), r)
    if cmd == "terminate":
        out = _check_output(args.output, args.overwrite)
        r = tools.make_termination(deck, opts, kind=args.kind,
                                   value=args.value, value2=args.value2,
                                   exclude=args.exclude)
        text = r.pop("text")
        tools.write_output(out, text, overwrite=True)
        r["output"] = out
        r["preview"] = [ln for ln in text.splitlines()
                        if ln and not ln.startswith("*")][:10]
        return _ok("종단 %d개를 %s 에 저장" % (r["count"], out), r)
    if cmd == "graph":
        out = _check_output(args.output, args.overwrite, suffix=".html")
        argv = ["graph", deck, "-o", out, "--profile", args.profile]
        for d in args.search_dir:
            argv += ["--search-dir", d]
        for p in args.opaque:
            argv += ["--opaque", p]
        for p in args.skip:
            argv += ["--skip", p]
        if args.max_depth is not None:
            argv += ["--max-depth", str(args.max_depth)]
        # cli prints progress; stdout is reserved for the JSON answer
        saved, sys.stdout = sys.stdout, io.StringIO()
        try:
            code = cli_mod.main(argv)
            log = sys.stdout.getvalue()
        finally:
            sys.stdout = saved
        if code:
            raise Refused("그림을 만들지 못했습니다 (exit %s)" % code)
        return _ok("연결 그림을 %s 에 저장" % out,
                   {"deck": deck, "output": out,
                    "notes": [ln.strip() for ln in log.splitlines()[1:]
                              if ln.strip()]})
    raise Refused("unknown command %s" % cmd)


def _log(msg):
    print("[primesim-dm] %s" % msg, file=sys.stderr)


class TimedOut(Exception):
    pass


def _on_alarm(signum, frame):
    raise TimedOut()


def _print(result, as_json):
    if as_json:
        # one line, and the last one: the caller reads it from there
        print(json.dumps(result, ensure_ascii=False))
        return
    print(result.get("summary") or result.get("message", ""))
    print(json.dumps(result, ensure_ascii=False, indent=2))


def main(script, argv=None):
    """Run one tool's script: ``main("dm_net_info")`` from dm_net_info.py."""
    args = build_parser(script).parse_args(argv)
    timer = hasattr(signal, "SIGALRM") and args.timeout > 0
    if timer:
        signal.signal(signal.SIGALRM, _on_alarm)
        signal.alarm(args.timeout)
    started = time.time()
    _log("%s %s" % (script, args.deck))
    try:
        result = run(args)
        code = 0
    except (Refused, tools.DeckError) as exc:
        result = {"status": "error", "message": str(exc)}
        code = FAIL
    except TimedOut:
        result = {"status": "error",
                  "message": "%d초 안에 끝나지 않아 중단했습니다. --skip / "
                             "--opaque 로 읽는 범위를 줄여 보세요."
                             % args.timeout}
        code = FAIL
    except Exception as exc:            # a bug: still answer in JSON
        import traceback
        traceback.print_exc(file=sys.stderr)
        result = {"status": "error",
                  "message": "내부 오류: %s: %s" % (type(exc).__name__, exc)}
        code = FAIL
    finally:
        if timer:
            signal.alarm(0)
    _log("%s in %.1fs" % ("done" if code == 0 else "failed",
                           time.time() - started))
    _print(result, args.json)
    return code
