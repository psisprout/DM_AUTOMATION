"""The checks behind the MCP tools, one function per tool.

Four jobs: the SI fan-out rule, the other lint errors, the terminations
(the picture is the CLI's graph command).  Every function takes a deck path
and returns plain dicts and lists, so it is tested without any MCP package
and serialised as JSON as it stands; :mod:`primesim_dm.tool_cli` turns them
into the scripts the in-house MCP tools run.

Stdlib only, like the rest of the package.
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
    """Includes that could not be read: what was named, and where (absolute).

    ``include`` is the name as the deck wrote it, since it was never found;
    ``from`` is the absolute path of the file that named it.
    """
    return [dict(ref) for ref in dk.missing_include_refs]


def _finding(f):
    return {"severity": f.severity, "code": f.code, "message": f.message,
            "where": f.where}


def _cut(items, limit):
    if limit and len(items) > limit:
        return items[:limit], len(items) - limit
    return items, 0


# ------------------------------------------------------------------ tools
# Kept to the four jobs an assistant is asked for - the SI fan-out rule,
# everything else lint finds, the picture, the terminations - so it does
# not wander through a dozen small calls to answer one question.

# what check_errors leaves to the other tools: one-sided nets are what
# make_termination is for, and a note that two nets are one is not an error
CONNECTIVITY_CODES = ("floating-net", "checks-skipped", "merged-net",
                      "si-node-fanout")


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
                     "where": els[0].where(full=True)})
    shown, more = _cut(rows, limit)
    return {"deck": deck_path, "expect": expect, "total": len(rows),
            "too_few": sum(1 for r in rows if r["count"] < expect),
            "too_many": sum(1 for r in rows if r["count"] > expect),
            "nets": shown, "not_shown": more,
            "missing_includes": _incomplete(dk)}


def check_errors(deck_path, opts, limit=50):
    """Everything lint finds except connectivity: includes, subckt calls,
    port counts, duplicate names, model files, lines it could not read."""
    dk = load(deck_path, opts)
    checker = check_mod.Checker(dk, keep_nets=opts.keep_nets,
                                full_paths=True)
    found = [f for f in checker.run() if f.code not in CONNECTIVITY_CODES]
    by_kind = check_mod.summarize(found)
    shown, more = _cut([_finding(f) for f in found], limit)
    return {
        "deck": deck_path,
        "files_read": len(dk.files),
        "elements": len(dk.elements),
        "nets": len(dk.net_users),
        "errors": sum(1 for f in found if f.severity == check_mod.SEV_ERROR),
        "warnings": sum(1 for f in found if f.severity == check_mod.SEV_WARN),
        "by_kind": [{"severity": sev, "code": code, "count": n}
                    for sev, code, n in by_kind],
        "findings": shown,
        "not_shown": more,
        "missing_includes": _incomplete(dk),
    }


def make_termination(deck_path, opts, kind="rload", value="1T", value2="1f",
                     keep_net=None, exclude=None, limit=50):
    """Termination lines for the one-sided nets, as text; nothing written.

    The nets come back as well, each with the instance and port it hangs
    off, so the answer can be reviewed without a second call.
    """
    if kind not in ("rload", "cload"):
        raise DeckError("kind must be rload or cload")
    dk = load(deck_path, opts)
    if dk.missing_includes:
        raise DeckError(
            "%d include(s) could not be read, so nets that belong to them "
            "look one-sided; fix the includes before terminating anything"
            % len(dk.missing_includes))
    checker = check_mod.Checker(dk, keep_nets=_keep(opts, keep_net),
                                full_paths=True)
    floating = checker.floating_nets()
    if exclude:
        drop = [re.compile(p) for p in exclude]
        floating = [f for f in floating
                    if not any(rx.search(f[0]) for rx in drop)]
    text = check_mod.render_terminations(
        dk, floating, kind=kind, value=value, value2=value2,
        prefix="Cterm_" if kind == "cload" else "Rterm_", source=deck_path)
    rows = [{"net": net, "element": el.name,
             "ports": check_mod.ports_of(dk.net_users[net], el),
             "where": el.where(full=True)} for net, el, _idx in floating]
    shown, more = _cut(rows, limit)
    return {"deck": deck_path, "count": len(floating), "nets": shown,
            "not_shown": more, "text": text}


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
