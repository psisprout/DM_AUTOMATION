"""Small, question-sized checks over a deck, for an MCP server to expose.

``lint`` answers everything in one report; an assistant asks one thing at a
time ("which nets have three things on them?", "what touches txp?") and does
better with a short structured answer to each.  Every function here takes a
deck path and returns plain dicts and lists, so it can be tested without any
MCP package and serialised as JSON as it stands.

Stdlib only, like the rest of the package; the MCP layer lives in
:mod:`primesim_dm.mcp_server`.
"""

import os
import re

from . import check as check_mod
from . import deck as deck_mod


class ReadOptions(object):
    """How decks are read: set once per server, not per question.

    These are the same knobs as the CLI's ``--search-dir`` / ``--opaque`` /
    ``--skip`` / ``--max-depth`` / ``--profile``.  They describe the site
    (where the models live, which trees to keep out), so they belong in the
    server's launch config rather than in every tool call.
    """

    def __init__(self, search_dirs=(), opaque=(), skip=(), max_depth=None,
                 profile=deck_mod.DEFAULT_PROFILE, keep_nets=()):
        self.search_dirs = list(search_dirs)
        self.opaque = list(deck_mod.DEFAULT_OPAQUE) + list(opaque)
        self.skip = list(skip)
        self.max_depth = max_depth
        self.profile = profile
        self.keep_nets = list(keep_nets)


class DeckError(Exception):
    """A question that cannot be answered from this deck."""


_CACHE = {}


def _mtimes(paths):
    out = []
    for p in paths:
        try:
            out.append((p, os.path.getmtime(p)))
        except OSError:
            out.append((p, None))
    return tuple(out)


def load(deck_path, opts):
    """Read a deck, reusing the last read while none of its files changed.

    An assistant asks several questions about one deck in a row, and a deck
    with its includes can take seconds to read; re-reading it for each
    question would make the tools feel broken.
    """
    if not os.path.isabs(deck_path):
        raise DeckError("deck_path must be an absolute path: %s" % deck_path)
    if not os.path.isfile(deck_path):
        raise DeckError("no such deck file: %s" % deck_path)
    key = (deck_path, tuple(opts.search_dirs), tuple(opts.opaque),
           tuple(opts.skip), opts.max_depth, opts.profile)
    hit = _CACHE.get(key)
    if hit is not None and _mtimes(hit.files) == hit._mtimes:
        return hit
    dk = deck_mod.read([deck_path], search_dirs=opts.search_dirs,
                       opaque=opts.opaque, skip=opts.skip,
                       max_depth=opts.max_depth, profile=opts.profile)
    if not dk.files:
        raise DeckError("could not read %s" % deck_path)
    dk._mtimes = _mtimes(dk.files)
    _CACHE[key] = dk
    return dk


def _keep(opts, keep_net):
    return list(opts.keep_nets) + list(keep_net or [])


def _incomplete(dk):
    """Includes that could not be read, each as "raw (from file:line)"."""
    return list(dk.missing_includes)


def _finding(f):
    return {"severity": f.severity, "code": f.code, "message": f.message,
            "where": f.where}


def _cut(items, limit):
    if limit and len(items) > limit:
        return items[:limit], len(items) - limit
    return items, 0


# ------------------------------------------------------------------ tools
def deck_summary(deck_path, opts, si=False):
    """What was read, and how many findings of each kind lint has."""
    dk = load(deck_path, opts)
    checker = check_mod.Checker(dk, keep_nets=opts.keep_nets, si=si)
    findings = checker.run()
    counts = checker.counts()
    return {
        "deck": deck_path,
        "files_read": len(dk.files),
        "elements": len(dk.elements),
        "nets": len(dk.net_users),
        "subckt_definitions": len(dk.subckts),
        "unparsed_lines": len(dk.unparsed),
        "off_profile_lines": len(dk.off_profile),
        "skipped_files": len(dk.skipped_files),
        "opaque_files": len(dk.opaque_files),
        "missing_includes": _incomplete(dk),
        "errors": counts[check_mod.SEV_ERROR],
        "warnings": counts[check_mod.SEV_WARN],
        "notes": counts[check_mod.SEV_INFO],
        "by_kind": [{"severity": sev, "code": code, "count": n}
                    for sev, code, n in check_mod.summarize(findings)],
    }


def lint_findings(deck_path, opts, code=None, severity=None, si=False,
                  limit=50):
    """lint's findings, optionally narrowed to one code or severity."""
    dk = load(deck_path, opts)
    checker = check_mod.Checker(dk, keep_nets=opts.keep_nets, si=si)
    found = [_finding(f) for f in checker.run()
             if (not code or f.code == code)
             and (not severity or f.severity == severity)]
    shown, more = _cut(found, limit)
    return {"deck": deck_path, "total": len(found), "findings": shown,
            "not_shown": more}


def check_si_fanout(deck_path, opts, expect=2, keep_net=None,
                    ignore_passives=False, limit=50):
    """Signal nets whose element count is not ``expect``."""
    dk = load(deck_path, opts)
    keep = [re.compile(p) for p in _keep(opts, keep_net)]
    ignore = check_mod.PASSIVE_KINDS if ignore_passives else ()
    ground = {"0", "gnd"}
    rows = []
    for net, users in sorted(dk.net_users.items()):
        if net in ground or net in dk.globals:
            continue
        if any(rx.search(net) for rx in keep):
            continue
        els = check_mod.on_net(users, ignore)
        if not els or len(els) == expect:
            continue
        rows.append({"net": net, "count": len(els),
                     "elements": [el.name for el in els],
                     "where": els[0].where()})
    shown, more = _cut(rows, limit)
    return {"deck": deck_path, "expect": expect, "total": len(rows),
            "too_few": sum(1 for r in rows if r["count"] < expect),
            "too_many": sum(1 for r in rows if r["count"] > expect),
            "nets": shown, "not_shown": more,
            "missing_includes": _incomplete(dk)}


def find_floating_nets(deck_path, opts, keep_net=None, limit=50):
    """Nets exactly one element touches."""
    dk = load(deck_path, opts)
    checker = check_mod.Checker(dk, keep_nets=_keep(opts, keep_net))
    rows = []
    for net, el, idx in checker.floating_nets():
        rows.append({"net": net, "element": el.name,
                     "ports": check_mod.ports_of(dk.net_users[net], el),
                     "where": el.where()})
    shown, more = _cut(rows, limit)
    return {"deck": deck_path, "total": len(rows), "nets": shown,
            "not_shown": more,
            # one unread include makes its nets look one-sided; say so
            # rather than let the list be taken at face value
            "missing_includes": _incomplete(dk)}


def net_info(deck_path, opts, net):
    """Everything on one net, with the port each element uses."""
    dk = load(deck_path, opts)
    key = net.lower() if net.lower() in dk.net_users else net
    users = dk.net_users.get(key)
    if users is None:
        close = [n for n in dk.net_users if net.lower() in n.lower()][:10]
        raise DeckError("no net named %s; similar: %s"
                        % (net, ", ".join(close) or "none"))
    rows = []
    for el in check_mod.on_net(users):
        rows.append({"element": el.name, "kind": el.kind,
                     "subckt": el.subckt if el.kind == "X" else None,
                     "ports": check_mod.ports_of(users, el),
                     "where": el.where()})
    return {"deck": deck_path, "net": key, "element_count": len(rows),
            "is_ground_or_global": key in ("0", "gnd") or key in dk.globals,
            "elements": rows}


def instance_info(deck_path, opts, name):
    """One element: what it is, and how many others share each of its nets."""
    dk = load(deck_path, opts)
    match = [el for el in dk.elements if el.name.lower() == name.lower()]
    if not match:
        close = [el.name for el in dk.elements
                 if name.lower() in el.name.lower()][:10]
        raise DeckError("no element named %s; similar: %s"
                        % (name, ", ".join(close) or "none"))
    el = match[0]
    ports = []
    for i, n in enumerate(el.nodes):
        users = dk.net_users.get(n, [])
        ports.append({"port": i, "net": n,
                      "elements_on_net": check_mod.touching(users)})
    return {"deck": deck_path, "element": el.name, "kind": el.kind,
            "subckt": el.subckt if el.kind == "X" else None,
            "where": el.where(), "ports": ports}


def make_termination(deck_path, opts, kind="rload", value="1T", value2="1f",
                     keep_net=None, exclude=None):
    """Termination lines for the one-sided nets, as text; nothing written."""
    if kind not in ("rload", "cload"):
        raise DeckError("kind must be rload or cload")
    dk = load(deck_path, opts)
    if dk.missing_includes:
        raise DeckError(
            "%d include(s) could not be read, so nets that belong to them "
            "look one-sided; fix the includes before terminating anything"
            % len(dk.missing_includes))
    checker = check_mod.Checker(dk, keep_nets=_keep(opts, keep_net))
    floating = checker.floating_nets()
    if exclude:
        drop = [re.compile(p) for p in exclude]
        floating = [f for f in floating
                    if not any(rx.search(f[0]) for rx in drop)]
    text = check_mod.render_terminations(
        dk, floating, kind=kind, value=value, value2=value2,
        prefix="Cterm_" if kind == "cload" else "Rterm_", source=deck_path)
    return {"deck": deck_path, "count": len(floating), "text": text}


def lint_report(deck_path, opts, si=False):
    """The full lint report as the CLI's ``-o`` writes it, with its counts."""
    dk = load(deck_path, opts)
    checker = check_mod.Checker(dk, keep_nets=opts.keep_nets, si=si)
    findings = checker.run()
    counts = checker.counts()
    text = check_mod.render(dk, findings, counts, verbose=True, limit=0)
    return {"deck": deck_path, "text": text,
            "errors": counts[check_mod.SEV_ERROR],
            "warnings": counts[check_mod.SEV_WARN],
            "notes": counts[check_mod.SEV_INFO],
            "missing_includes": _incomplete(dk)}


class OutputExists(DeckError):
    """The output file is there already and nobody said to replace it."""

    def __init__(self, path):
        DeckError.__init__(self, "output file already exists: %s" % path)
        self.path = path


def write_output(path, text, overwrite=False):
    """Write ``text`` to ``path``; return ``(created, modified)`` path lists.

    An existing file is replaced only when ``overwrite`` is set: a report or
    a .inc someone already reviewed is not something to lose silently.
    """
    existed = os.path.exists(path)
    if existed and not overwrite:
        raise OutputExists(path)
    parent = os.path.dirname(path)
    if parent and not os.path.isdir(parent):
        os.makedirs(parent)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    return ([], [path]) if existed else ([path], [])
