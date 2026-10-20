"""Read a flat SPICE/PrimeSim deck into a connectivity graph.

This is what lets the checker run on decks nobody generated - the ones
already sitting in the project, written by hand.

Parsing a netlist for *connectivity* only needs one hard question answered
per line: which tokens are nodes?  That differs by element type, and no
parser gets every HSPICE element right.  So every line this module cannot
confidently split is recorded in :attr:`Deck.unparsed` rather than guessed
at - a check result is only trustworthy if it comes with the list of lines
it could not read.
"""

import os
import re

from . import spice

# ---------------------------------------------------------------- profiles
#
# What a DM link deck is actually made of.  A channel model, a package, an
# IO model and the passives around them - nothing else appears at the top
# level.  Reading a line as an element type the deck does not use is how a
# parser invents connectivity that is not there, so the profile is a closed
# list and anything outside it is reported rather than guessed at.
#
#   letter -> (what it is, how its node list ends)
#
# node rules:
#   an int    exactly that many nodes
#   "model"   nodes are every non-parameter token but the last, which names
#             the .subckt or .model being called
#   "kv"      nodes run until the first key=value token
#   "wline"   2*(N+1) nodes, with N from the N= parameter
#   "none"    no nodes at all
DM_PROFILE = {
    "X": ("subckt call - channel broadband SPICE model, SPF IO model, "
          "package, any .subckt", "model"),
    "S": ("S-parameter channel (Touchstone)", "kv"),
    "B": ("IBIS buffer", "kv"),
    "W": ("coupled transmission line", "wline"),
    "R": ("resistor", 2),
    "C": ("capacitor", 2),
    "L": ("inductor", 2),
    "V": ("voltage source", 2),
    "I": ("current source", 2),
    "K": ("mutual inductance between two L's", "none"),
}

# The rest of what HSPICE has.  Not wrong, just not what a link deck is
# made of: a transistor or a diode at the top level is either somebody
# else's deck, or a line that was read as the wrong thing.  --profile full
# brings them back.
FULL_PROFILE = dict(DM_PROFILE)
FULL_PROFILE.update({
    "D": ("diode", 2),
    "E": ("voltage-controlled voltage source", 4),
    "G": ("voltage-controlled current source", 4),
    "F": ("current-controlled current source", 2),
    "H": ("current-controlled voltage source", 2),
    "T": ("lossless transmission line", 4),
    "P": ("port", "kv"),
    "Q": ("BJT", "model"),
    "M": ("MOSFET", "model"),
    "J": ("JFET", "model"),
    "Z": ("MESFET / HEMT", "model"),
})

PROFILES = {"dm": DM_PROFILE, "full": FULL_PROFILE}
DEFAULT_PROFILE = "dm"


def profile_table(name):
    """The element table ``name`` selects."""
    try:
        return PROFILES[name]
    except KeyError:
        raise ValueError("unknown profile %r; pick one of %s"
                         % (name, ", ".join(sorted(PROFILES))))


def profile_letters(name):
    """The element letters a profile reads, in a stable order."""
    return sorted(profile_table(name))


class OffProfile(Exception):
    """This element type is not one the active profile reads."""

    def __init__(self, kind):
        Exception.__init__(self, kind)
        self.kind = kind


# key=value parameters that name an external model file, per element type
_FILE_PARAMS = {
    "B": ("file", "ibis_file"),
    "S": ("tstonefile", "sparamfile", "file"),
}
# ... and the one that names a model inside it
_MODEL_PARAMS = {
    "B": ("model", "ibis_model"),
    "S": ("mname", "model"),
}

# Files whose insides never matter for a DM connectivity check: parasitic
# extractions are enormous and describe the inside of a cell, not how the
# link is wired.  Their .subckt interfaces are still read.
DEFAULT_OPAQUE = [r"\.spf$", r"\.dspf$", r"\.spef$", r"\.rcx$"]

_DOT_CONNECT = re.compile(r"^\s*\.connect\s+(\S+)\s+(\S+)", re.I)
_DOT_MODEL = re.compile(r"^\s*\.model\s+(\S+)\s+(\S+)(.*)", re.I | re.S)
_DOT_GLOBAL = re.compile(r"^\s*\.global\s+(.*)", re.I)
_DOT_SUBCKT = re.compile(r"^\s*\.subckt\b", re.I)
_DOT_ENDS = re.compile(r"^\s*\.ends\b", re.I)
_DOT_INCLUDE = re.compile(
    r"^\s*\.inc(?:lude)?\s+(?:'([^']+)'|\"([^\"]+)\"|(\S+))", re.I)
_DOT_LIB = re.compile(
    r"^\s*\.lib\s+(?:'([^']+)'|\"([^\"]+)\"|(\S+))(?:\s+(\S+))?", re.I)


def is_kv(token):
    return "=" in token


class Element(object):
    """One instantiated device at the top level of a deck."""

    def __init__(self, name, kind, nodes, tail, path, line):
        self.name = name
        self.kind = kind          # upper-case first letter
        self.nodes = nodes        # ordered node names
        self.tail = tail          # everything after the nodes
        self.path = path
        self.line = line

    @property
    def subckt(self):
        """For an X instance, the subckt being called."""
        if self.kind != "X":
            return None
        rest = [t for t in self.tail if not is_kv(t)]
        return rest[0] if rest else None

    def param(self, *names):
        """The value of the first of ``names`` this element sets, or None.

        Names are compared case-insensitively and the value comes back
        unquoted, because ``file='x.ibs'`` and ``FILE=x.ibs`` are the same
        line to the simulator.
        """
        wanted = [n.lower() for n in names]
        for tok in self.tail:
            if not is_kv(tok):
                continue
            key, _, val = tok.partition("=")
            if key.lower() in wanted:
                return val.strip().strip("'\"")
        return None

    def model_file(self):
        """The external model file this element names, or None.

        An IBIS buffer carries its .ibs file and an S element its Touchstone
        file on the element line itself; everything else gets its model from
        an .include, which is checked elsewhere.
        """
        return self.param(*_FILE_PARAMS.get(self.kind, ()))

    def model_name(self):
        """The model this element selects inside that file, or None.

        For an X instance that is the subckt token; for a B element the IBIS
        model name; for an S element the ``.model`` it refers to.
        """
        if self.kind == "X":
            return self.subckt
        return self.param(*_MODEL_PARAMS.get(self.kind, ()))

    def where(self):
        return "%s:%d" % (os.path.basename(self.path), self.line)

    def __repr__(self):
        return "<%s %s %d nodes>" % (self.kind, self.name, len(self.nodes))


class SModel(object):
    """A ``.model <name> S ...`` block - an S-parameter channel's data file.

    An S element usually names its Touchstone file indirectly::

        S1 in out mname=ch
        .model ch S tstonefile='ch.s4p'

    so the file cannot be checked without reading the .model line too.
    """

    def __init__(self, name, params, path, line):
        self.name = name
        self.params = params          # lower key -> unquoted value
        self.path = path
        self.line = line

    def file(self):
        for key in ("tstonefile", "sparamfile", "file"):
            if key in self.params:
                return self.params[key]
        return None

    def where(self):
        return "%s:%d" % (os.path.basename(self.path), self.line)

    def __repr__(self):
        return "<SModel %s %s>" % (self.name, self.file())


class Deck(object):
    def __init__(self):
        self.elements = []
        self.subckts = {}          # lower name -> Subckt (definitions seen)
        self.globals = set()
        self.connects = []         # (a, b, where) from .connect
        self.unparsed = []         # (path, line, text, reason)
        self.files = []
        self.missing_includes = []
        self.searched_dirs = []
        self.opaque_files = []     # read for .subckt interfaces only
        self.skipped_files = []    # not read at all
        self.unused_filters = []   # --opaque/--skip patterns that hit nothing
        self.depth_limited = []    # includes below these were not followed
        self.net_users = {}        # net -> [(element, port_index)]
        self.profile = DEFAULT_PROFILE
        self.off_profile = []      # (path, line, text, kind) not read at all
        self.s_models = {}         # lower name -> SModel
        self.deck_dirs = []        # directories of the top deck file(s)

    def index_nets(self):
        self.net_users = {}
        for el in self.elements:
            for i, n in enumerate(el.nodes):
                self.net_users.setdefault(n, []).append((el, i))
        return self.net_users

    def nets(self):
        return sorted(self.net_users)


def _split_nodes(tokens, kind, profile=None):
    """Return ``(nodes, tail)`` for one element line.

    ``profile`` is an element table from :data:`PROFILES`; it decides both
    which element types are read at all and where each one's node list
    ends.  Raises :class:`OffProfile` when the table does not carry this
    element type, and ``ValueError`` when it does but the line cannot be
    split confidently - the caller records either rather than guessing.
    """
    table = DM_PROFILE if profile is None else profile
    try:
        _what, rule = table[kind]
    except KeyError:
        if kind in FULL_PROFILE:
            # a known element type this profile leaves out, which is a
            # statement about the deck rather than a gap in the parser
            raise OffProfile(kind)
        raise ValueError("unhandled element type %r" % kind)

    if rule == "none":
        return [], tokens

    if rule == "model":
        # Xname n1 .. nN subcktname [k=v ...]   /   M1 nd ng ns nb model w=..
        head = []
        for t in tokens:
            if is_kv(t):
                break
            head.append(t)
        if len(head) < 2:
            raise ValueError("needs at least one node and a model/subckt name")
        # the last non-parameter token is the model or subckt being called
        return head[:-1], tokens[len(head) - 1:]

    if rule == "wline":
        n = None
        for t in tokens:
            if is_kv(t) and t.split("=", 1)[0].lower() == "n":
                try:
                    n = int(t.split("=", 1)[1])
                except ValueError:
                    raise ValueError("W element with non-numeric N=")
                break
        if n is None:
            raise ValueError("W element without N=")
        count = 2 * (n + 1)
        if len(tokens) < count:
            raise ValueError("W element declares N=%d but has %d tokens"
                             % (n, len(tokens)))
        return tokens[:count], tokens[count:]

    if rule == "kv":
        nodes = []
        for t in tokens:
            if is_kv(t):
                break
            nodes.append(t)
        if not nodes:
            raise ValueError("no nodes before the first key=value")
        return nodes, tokens[len(nodes):]

    count = rule                            # a fixed node count
    if len(tokens) < count:
        raise ValueError("expected %d nodes, line has %d tokens"
                         % (count, len(tokens)))
    return tokens[:count], tokens[count:]


class PathFilter(object):
    """Match paths by literal path when the pattern is one, else by regex.

    People reach for --skip by pasting a path straight out of the report,
    and a path is not a safe regex: ``/proj/a+b(1)/abc.lib`` compiles to
    something that matches nothing at all, silently.  So a pattern that
    exists on disk is compared literally - a directory matching everything
    beneath it - and only patterns that are not real paths are treated as
    regexes.
    """

    def __init__(self, patterns):
        self.entries = []
        for pattern in patterns or []:
            expanded = os.path.expandvars(os.path.expanduser(pattern))
            if os.path.isdir(expanded):
                self.entries.append(
                    ("dir", os.path.abspath(expanded) + os.sep, pattern))
            elif os.path.exists(expanded):
                self.entries.append(
                    ("file", os.path.abspath(expanded), pattern))
            else:
                self.entries.append(("re", re.compile(pattern, re.I), pattern))
        self.used = set()

    def __bool__(self):
        return bool(self.entries)

    __nonzero__ = __bool__

    def matches(self, path):
        full = os.path.abspath(path)
        for kind, value, pattern in self.entries:
            if kind == "dir":
                hit = full.startswith(value)
            elif kind == "file":
                hit = full == value
            else:
                hit = bool(value.search(path))
            if hit:
                self.used.add(pattern)
                return True
        return False

    def unused(self):
        return [p for _k, _v, p in self.entries if p not in self.used]


def resolve_dirs(base_dir, deck_dirs, search_dirs):
    """Where to look for a relative include, most specific first.

    A ``.include './DB/x.sp'`` inside a library is not necessarily relative
    to that library: SPICE resolves it against the directory the simulator
    was launched from, which in practice is where the top deck lives.  So
    the including file's directory is tried first, then the top deck's, then
    the current directory, then anything --search-dir added.
    """
    out = []
    for d in [base_dir] + list(deck_dirs) + [os.getcwd()] + list(search_dirs):
        d = os.path.abspath(d)
        if d not in out:
            out.append(d)
    return out


def _resolve(raw, dirs):
    raw = os.path.expandvars(os.path.expanduser(raw))
    if os.path.isabs(raw):
        return raw if os.path.isfile(raw) else None
    for d in dirs:
        cand = os.path.join(d, raw)
        if os.path.isfile(cand):
            return cand
    return None


def find_file(deck, raw, from_path=None):
    """Resolve a path named inside a deck, the way an include is resolved.

    An IBIS or Touchstone file is named on an element line rather than an
    .include, but the simulator looks for it in the same places, so the
    search order has to match: the naming file's own directory, then the top
    deck's, then the current directory, then anything --search-dir added.
    """
    if from_path:
        base = os.path.dirname(os.path.abspath(from_path))
    elif deck.deck_dirs:
        base = deck.deck_dirs[0]
    else:
        base = os.getcwd()
    dirs = resolve_dirs(base, deck.deck_dirs, deck.searched_dirs)
    return _resolve(raw, dirs)


def read(paths, follow_includes=True, search_dirs=(), fold_case=True,
         opaque=None, skip=(), max_depth=None, profile=DEFAULT_PROFILE):
    """Parse one or more deck files into a :class:`Deck`.

    ``opaque`` and ``skip`` are path patterns that draw a boundary around the
    parts of the tree a DM check has no business reading - a transistor-level
    IO model, a PDK, an extracted parasitic file.  An opaque file still gives
    up its ``.subckt`` interfaces, so port counts stay checkable; a skipped
    one is not opened at all.  ``max_depth`` stops the walk after N levels of
    include.

    ``profile`` picks the element table (see :data:`PROFILES`).  The default
    ``"dm"`` reads only what a link deck is built from - subckt calls, IBIS
    buffers, S-parameter channels, W lines, R/L/C and sources - and records
    every other element type in :attr:`Deck.off_profile` instead of reading
    it.  Either way only the top level is read; the inside of a ``.subckt``
    is never part of the connectivity.
    """
    table = profile_table(profile)
    opaque_f = PathFilter(DEFAULT_OPAQUE if opaque is None else opaque)
    skip_f = PathFilter(skip)

    deck = Deck()
    deck.profile = profile
    # a file can legitimately be read twice under two different .lib
    # sections, so the queue and the seen-set are keyed on both
    queue = [(p, None, 0) for p in paths]
    seen = set()
    deck_dirs = [os.path.dirname(os.path.abspath(p)) for p in paths]
    deck.deck_dirs = list(deck_dirs)
    deck.searched_dirs = []

    def norm(net):
        return net.lower() if fold_case else net

    while queue:
        path, section, depth = queue.pop(0)
        path = os.path.abspath(path)
        if (path, section) in seen:
            continue
        seen.add((path, section))
        if depth and skip_f.matches(path):
            deck.skipped_files.append(path)
            continue
        if not os.path.isfile(path):
            deck.missing_includes.append(path)
            continue
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            text = fh.read()
        if section or spice.lib_sections(text):
            # only the selected .lib section is live; without one, the
            # sections in the file are definitions nobody activated
            text = spice.select_lib_section(text, section)

        if depth and opaque_f.matches(path):
            # interface only: the ports are what the deck connects to
            for sub in spice.parse_subckts(text, path):
                deck.subckts.setdefault(sub.name.lower(), sub)
            deck.opaque_files.append(path)
            continue

        deck.files.append(path + (" [.lib %s]" % section if section else ""))

        for sub in spice.parse_subckts(text, path):
            deck.subckts.setdefault(sub.name.lower(), sub)

        at_limit = max_depth is not None and depth >= max_depth
        if at_limit and path not in deck.depth_limited:
            deck.depth_limited.append(path)

        depth = 0
        base_dir = os.path.dirname(path)
        for lineno, line in spice.logical_lines(text):
            if _DOT_SUBCKT.match(line):
                depth += 1
                continue
            if _DOT_ENDS.match(line):
                depth = max(0, depth - 1)
                continue

            if _DOT_INCLUDE.match(line) or _DOT_LIB.match(line):
                found = spice.include_target(line)
                if found:
                    raw, sub_section = found
                    dirs = resolve_dirs(base_dir, deck_dirs, search_dirs)
                    for d in dirs:
                        if d not in deck.searched_dirs:
                            deck.searched_dirs.append(d)
                    target = _resolve(raw, dirs)
                    if target is None:
                        deck.missing_includes.append(
                            "%s (from %s:%d)" % (raw, os.path.basename(path),
                                                 lineno))
                    elif follow_includes and not at_limit:
                        queue.append((target, sub_section, depth + 1))
                continue

            if depth:                      # only the top level is checked
                continue

            m = _DOT_GLOBAL.match(line)
            if m:
                deck.globals.update(norm(t) for t in m.group(1).split())
                continue
            m = _DOT_CONNECT.match(line)
            if m:
                deck.connects.append((norm(m.group(1)), norm(m.group(2)),
                                      "%s:%d" % (path, lineno)))
                continue
            m = _DOT_MODEL.match(line)
            if m and m.group(2).strip().lower() == "s":
                params = {}
                for tok in spice._tokenize(m.group(3)):
                    if is_kv(tok):
                        key, _, val = tok.partition("=")
                        params[key.lower()] = val.strip().strip("'\"")
                key = m.group(1).lower()
                deck.s_models.setdefault(
                    key, SModel(m.group(1), params, path, lineno))
                continue
            if line.lstrip().startswith("."):
                continue                   # any other directive

            toks = spice._tokenize(line)
            if not toks:
                continue
            name = toks[0]
            kind = name[0].upper()
            try:
                nodes, tail = _split_nodes(toks[1:], kind, table)
            except OffProfile:
                # a known element type this profile does not read: say so,
                # because its nodes are missing from the graph either way
                deck.off_profile.append((path, lineno, line, kind))
                continue
            except ValueError as exc:
                deck.unparsed.append((path, lineno, line, str(exc)))
                continue
            deck.elements.append(
                Element(name, kind, [norm(n) for n in nodes], tail,
                        path, lineno))

    for pattern in skip_f.unused():
        deck.unused_filters.append(("--skip", pattern))
    if opaque is not None:
        for pattern in opaque_f.unused():
            deck.unused_filters.append(("--opaque", pattern))
    deck.index_nets()
    return deck
