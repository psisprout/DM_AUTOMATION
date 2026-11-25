"""Work out a deck's stages and signal groups, to lay the picture out by.

A link deck, whatever the interface (LPDDR, NAND, HBM ...), is a chain of a
few big blocks - RDL, package, board, interposer, die - with many small
lanes running through them, from the driver end to the receiver end.  How
many blocks there are and what they are called changes from deck to deck: a
board can be one model or four (one per byte group), and RDL-package-board
can arrive merged into a single channel model.  So nothing here goes by
name.  It goes by connections only:

  * **hubs** are the big blocks: elements with far more signal pins than
    the deck's typical element
  * **stages** are the hubs' places along the chain, counted from the
    driver end: two hubs are neighbours when a path of small elements (or a
    shared net) joins them, and the stage is the hop count from the start.
    Pieces of a split block land on the same stage, and so do the dies of
    a 1P4P - one stage, stacked.  A dead end hanging off the chain while the
    chain goes on (the die on each rank of a fly-by) is a branch of the hub
    it hangs off, not a stage of its own
  * **groups** are the pieces of the most split stage: the lanes through
    one board piece belong together
  * **lane shapes** are the small parts left when the hubs are taken out,
    sorted by what they are made of and which stages they reach.  A shape
    many lanes share is the pattern; one lane with a shape of its own next
    to them is usually the wiring mistake

Names are only used to label what was found.  The analysis is a proposal:
``--hub`` / ``--not-hub`` / ``--tx`` correct it where it guessed wrong.
"""

import re
from collections import Counter, OrderedDict

from . import graph as graph_mod

PASSIVE_KINDS = ("R", "C", "L")
SOURCE_KINDS = ("V", "I")


class Analysis(object):
    def __init__(self):
        self.hubs = []          # element names, deck order
        self.stages = []        # [[hub name, ...], ...] from the driver end
        self.direction = ""     # why the first stage is the driver end
        self.unstaged = []      # hubs no path reaches from the start
        self.branches = {}      # stage hub -> [dead-end hubs hanging off it]
        self.groups = []        # [{"piece", "stage", "lanes", "label"}]
        self.lanes = []         # [{"shape", "count", "where", "example",
                                #   "odd"}]
        self.threshold = 0      # signal pins a hub has at least
        self.elements = 0

    def as_dict(self):
        return {"elements": self.elements, "hub_threshold": self.threshold,
                "hubs": list(self.hubs),
                "stages": [list(s) for s in self.stages],
                "direction": self.direction,
                "unstaged": list(self.unstaged),
                "branches": dict((k, list(v))
                                 for k, v in self.branches.items()),
                "groups": [dict(g) for g in self.groups],
                "lane_shapes": [dict(l) for l in self.lanes]}


def _signal_nets(deck, rails):
    is_rail = graph_mod._rail_filter(rails)
    ground = {"0", "gnd", "gnd!"}
    # a net only passives sit on, several of them, is a termination supply
    # (VTT and the like) whatever it is called: the far side of every lane's
    # terminator, not a wire that joins the lanes
    term = set()
    for net, users in deck.net_users.items():
        kinds = [el.kind for el, _i in users]
        if len(set(id(el) for el, _i in users)) >= 3 and \
                all(k in PASSIVE_KINDS for k in kinds):
            term.add(net)

    def signal(net):
        return not (net in ground or net in deck.globals or net in term
                    or is_rail(net))
    return signal


def _elements(deck, signal):
    """``{name: set(signal nets)}`` for the elements that carry a signal.

    An R/C/L with one signal end is a termination: a property of its node,
    not part of the path, so it is left out (the picture folds it the same
    way).  One between two signal nets is part of the path and stays.
    """
    out = OrderedDict()
    for el in deck.elements:
        nets = set(n for n in el.nodes if signal(n))
        if not nets:
            continue
        if el.kind in PASSIVE_KINDS and len(nets) < 2:
            continue
        out[el.name] = nets
    return out


def _lower_quartile(values):
    values = sorted(values)
    return values[len(values) // 4] if values else 0


def _matches(patterns, name, deck_el):
    """A pattern hits the instance name or the subckt it calls, each whole,
    so ``^XRX_dq0$`` and ``^io_rx$`` both mean what they say."""
    subckt = deck_el.subckt if deck_el is not None and deck_el.kind == "X" \
        else None
    hay = [name] + ([subckt] if subckt else [])
    return any(re.search(p, h, re.I) for p in patterns for h in hay)


def _label(nets):
    """A short name for a set of nets: their most common stem."""
    stems = Counter()
    for net in nets:
        stem = re.sub(r"[\d_\-\[\]<>().]+", " ", net).split()
        if stem:
            stems[stem[0].lower()] += 1
    if not stems:
        return ""
    return stems.most_common(1)[0][0]


def analyze(deck, rails=graph_mod.DEFAULT_RAILS, hub=(), not_hub=(), tx=()):
    """Propose stages, groups and lane shapes for ``deck``.

    ``hub`` / ``not_hub`` force an element in or out of the hubs, and ``tx``
    names the driver end, each a list of regexes matched on the instance
    name or the subckt it calls.
    """
    a = Analysis()
    signal = _signal_nets(deck, rails)
    els = _elements(deck, signal)
    by_name = dict((el.name, el) for el in deck.elements)
    a.elements = len(els)
    if not els:
        return a

    # -- hubs: far more signal pins than a lane's small element.  Measured on
    # the lower quartile, not the median: in a deck of few lanes and many
    # blocks the median is a block, and the blocks would not stand out
    a.threshold = max(4, 2 * _lower_quartile([len(n) for n in els.values()]))
    hubs = []
    for name, nets in els.items():
        el = by_name.get(name)
        if _matches(not_hub, name, el):
            continue
        if len(nets) >= a.threshold or _matches(hub, name, el):
            hubs.append(name)
    a.hubs = hubs
    hubset = set(hubs)

    net_els = {}
    for name, nets in els.items():
        for n in nets:
            net_els.setdefault(n, []).append(name)

    def neighbours(name):
        seen = []
        for n in els[name]:
            for other in net_els[n]:
                if other != name and other not in seen:
                    seen.append(other)
        return seen

    # -- small parts: what is left when the hubs are taken out
    part_of = {}
    parts = []
    for name in els:
        if name in hubset or name in part_of:
            continue
        idx = len(parts)
        members, todo = [], [name]
        part_of[name] = idx
        while todo:
            cur = todo.pop()
            members.append(cur)
            for other in neighbours(cur):
                if other in hubset or other in part_of:
                    continue
                part_of[other] = idx
                todo.append(other)
        touches = []
        for m in members:
            for other in neighbours(m):
                if other in hubset and other not in touches:
                    touches.append(other)
        parts.append({"members": members, "hubs": touches})

    # -- hub neighbours: a shared net, or a small part touching both
    adj = dict((h, set()) for h in hubs)
    for h in hubs:
        for other in neighbours(h):
            if other in hubset:
                adj[h].add(other)
    for part in parts:
        for x in part["hubs"]:
            for y in part["hubs"]:
                if x != y:
                    adj[x].add(y)

    def drives(part):
        return any(by_name[m].kind in SOURCE_KINDS or by_name[m].kind == "B"
                   or _matches(tx, m, by_name[m]) for m in part["members"])

    # -- the driver end
    start = [h for h in hubs if _matches(tx, h, by_name.get(h))]
    if not start and tx:
        # --tx may name a small element (a driver) rather than a hub
        for part in parts:
            if any(_matches(tx, m, by_name.get(m)) for m in part["members"]):
                start.extend(h for h in part["hubs"] if h not in start)
    if start:
        a.direction = "--tx"
    elif hubs:
        score = Counter()
        for part in parts:
            if any(by_name[m].kind in SOURCE_KINDS for m in part["members"]):
                for h in part["hubs"]:
                    score[h] += 1
        if score:
            best = max(score.values())
            start = [h for h in hubs if score[h] == best]
            a.direction = "sources"
        else:
            # no stimulus to go by: the end the deck is written from
            ends = [h for h in hubs if len(adj[h]) <= 1] or hubs
            start = [ends[0]]
            a.direction = "deck order"

    # -- stages: hops from the driver end
    stage, parent = {}, {}
    frontier = list(start)
    for h in frontier:
        stage[h] = 0
    while frontier:
        nxt = []
        for h in frontier:
            for other in sorted(adj[h], key=hubs.index):
                if other not in stage:
                    stage[other] = stage[h] + 1
                    parent[other] = h
                    nxt.append(other)
        frontier = nxt

    # a dead end whose siblings carry on is a branch, not a stage: the die
    # on a rank of a fly-by hangs off the rank while the bus goes on to the
    # next one.  Dead ends whose siblings are all dead ends too (the dies of
    # a 1P4P) are the end of the chain and stay a stage
    def dead_end(h):
        return h not in start and len(adj[h]) == 1

    def kind_of(h):
        el = by_name.get(h)
        return (el.subckt if el is not None and el.kind == "X" else None) \
            or h

    for h in list(stage):
        p = parent.get(h)
        if p is None or not dead_end(h):
            continue
        siblings = [c for c in adj[p] if parent.get(c) == p and c != h]
        if any(not dead_end(c) for c in siblings):
            a.branches.setdefault(p, []).append(h)
    # the last rank's die has nothing after it to carry on to, but it is the
    # same kind of thing as the dies already found hanging off the others
    branch_kinds = set(kind_of(h) for v in a.branches.values() for h in v)
    for h in list(stage):
        p = parent.get(h)
        if p is None or not dead_end(h) or kind_of(h) not in branch_kinds:
            continue
        if h not in a.branches.get(p, []):
            a.branches.setdefault(p, []).append(h)
    on_chain = dict((h, i) for h, i in stage.items()
                    if not any(h in v for v in a.branches.values()))
    if on_chain:
        a.stages = [[h for h in hubs if on_chain.get(h) == i]
                    for i in range(max(on_chain.values()) + 1)]
        a.stages = [st for st in a.stages if st]
    stage_no = {}
    for i, st in enumerate(a.stages):
        for h in st:
            stage_no[h] = i + 1
    for p, kids in a.branches.items():
        for h in kids:
            stage_no[h] = stage_no.get(p)
    a.unstaged = [h for h in hubs if h not in stage]

    # -- groups: the pieces of the most split stage
    split = max(a.stages, key=len) if a.stages else []
    if len(split) > 1:
        idx = a.stages.index(split)
        before = set()
        for h, n in stage_no.items():
            if n is not None and n < idx + 1:
                before |= els[h]
        for piece in split:
            nets = sorted(els[piece])
            # lanes, not pins: a piece a signal passes through has an in
            # and an out net for it; count the side facing the driver
            lanes = len([n for n in nets if n in before]) or len(nets)
            a.groups.append({"piece": piece, "stage": idx + 1,
                             "lanes": lanes, "label": _label(nets)})

    # -- lane shapes, and where each sits: before the first stage it
    # touches (it drives), after the last one, or between two
    shapes = OrderedDict()
    for part in parts:
        kinds = Counter()
        for m in part["members"]:
            el = by_name[m]
            kinds[(el.subckt if el.kind == "X" else None) or el.kind] += 1
        reach = sorted(set(stage_no[h] for h in part["hubs"]
                           if stage_no.get(h) is not None))
        if not reach:
            where = "no hub"
        elif len(reach) > 1:
            where = "between %d-%d" % (reach[0], reach[-1])
        elif drives(part):
            where = "before %d" % reach[0]
        else:
            where = "after %d" % reach[0]
        key = (tuple(sorted(kinds.items())), where)
        entry = shapes.setdefault(key, {"members": [], "kinds": kinds,
                                        "where": where})
        entry["members"].append(part["members"])
    rows = []
    for entry in shapes.values():
        shape = " + ".join("%s%s" % (name, " x%d" % n if n > 1 else "")
                           for name, n in sorted(entry["kinds"].items()))
        rows.append({"shape": shape, "count": len(entry["members"]),
                     "where": entry["where"],
                     "example": entry["members"][0][0], "odd": False})
    # a lone lane beside a crowd sitting in the same place is the one wired
    # differently
    crowd = Counter(r["where"] for r in rows if r["count"] >= 3)
    for r in rows:
        if r["count"] == 1 and crowd[r["where"]]:
            r["odd"] = True
    rows.sort(key=lambda r: (-r["count"], r["where"], r["shape"]))
    a.lanes = rows
    return a


def render(a, title=""):
    """The analysis as text for a person."""
    out = []
    if title:
        out.append(title)
    out.append("elements : %d with a signal pin; hubs have %d+ signal pins"
               % (a.elements, a.threshold))
    if not a.hubs:
        out.append("hubs     : none - a small deck; the automatic layout "
                   "needs no stages")
        return "\n".join(out) + "\n"
    why = {"sources": "the end the stimulus sources are on",
           "deck order": "no stimulus source: the end written first",
           "--tx": "given by --tx"}.get(a.direction, a.direction)
    out.append("stages   : %d, from the driver end (%s)"
               % (len(a.stages), why))
    for i, hubs in enumerate(a.stages, 1):
        note = "  <- %d pieces, stacked" % len(hubs) if len(hubs) > 1 else ""
        kids = [k for h in hubs for k in a.branches.get(h, [])]
        if kids:
            note += "  (+ branch %s)" % ", ".join(kids)
        out.append("  %d  %s%s" % (i, ", ".join(hubs), note))
    if a.unstaged:
        out.append("  ?  %s  <- no path from the driver end"
                   % ", ".join(a.unstaged))
    if a.groups:
        out.append("groups   : by the pieces of stage %d"
                   % a.groups[0]["stage"])
        for g in a.groups:
            out.append("  %-24s %3d lane(s)  %s"
                       % (g["piece"], g["lanes"], g["label"]))
    if a.lanes:
        out.append("lanes    : what hangs between and around the hubs")
        for r in a.lanes:
            mark = "  <- differs from the others here" if r["odd"] else ""
            out.append("  x%-4d %-36s %-13s e.g. %s%s"
                       % (r["count"], r["shape"][:36], r["where"],
                          r["example"], mark))
    return "\n".join(out) + "\n"
