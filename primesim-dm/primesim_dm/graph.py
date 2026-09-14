"""Connectivity as a picture: the deck the checker read, drawn.

A netlist is a graph, and reading one as text stops working somewhere around
the third instance.  This draws what ``lint`` already parsed - the same
elements, the same nets - so a wiring mistake shows up as a shape instead of
as line 400 of a report.

Two decisions keep the picture from turning into a hairball, and both matter
more than the drawing itself:

  * **Power and ground reach everything.**  Drawn as wires they bury the
    signal path, so each box carries them as a local stub instead, the way a
    schematic does it.
  * **IO models are buses.**  ``dq0`` .. ``dq7`` wired identically is one
    connection an engineer checks once, not eight, so nets that share a stem
    *and* a set of endpoints collapse into a single edge marked with its
    width.  Nets that share a stem but not endpoints stay apart - that
    difference is usually the bug.

What is drawn is only what was read.  A file behind ``--skip`` contributes
no elements, so the header says so; a picture that quietly omits half the
deck is worse than no picture.
"""

import os
import re

# Nets that tie to nearly every instance.  Drawn as wires they dominate the
# layout and say nothing, so they become stubs on the box that uses them.
DEFAULT_RAILS = (r"^0$", r"^gnd", r"^vss", r"^vdd", r"^vcc", r"^avss",
                 r"^avdd", r"^vbb", r"^vpp")

# stem + index, for the bus grouping: dq0 / dq<0> / dq[0] / dq(0)
_BUS = re.compile(r"^(.*?)[<\[(]?(\d+)[>\])]?$")

SEV_FLOATING = "floating"

# A termination resistor is not a block of the design, it is a property of
# the node it sits on.  Drawn as boxes they double the instance count and
# push apart the things that actually talk to each other, so a two-terminal
# passive with only one signal end is folded into that node instead.  One
# that bridges two signal nets is left alone: it carries the connection, and
# absorbing it would break the path the picture exists to show.
PASSIVE_KINDS = ("R", "C", "L")


def _stem(net):
    """('dq<7>') -> ('dq', 7); a net with no index -> (net, None)."""
    mo = _BUS.match(net)
    if not mo or not mo.group(1):
        return net, None
    return mo.group(1).rstrip("_"), int(mo.group(2))


def _ranges(nums):
    """[0,1,2,5] -> '0:2,5' - a bus label an engineer can read at a glance."""
    out, run = [], []
    for n in sorted(nums):
        if run and n == run[-1] + 1:
            run.append(n)
            continue
        if run:
            out.append(run)
        run = [n]
    if run:
        out.append(run)
    bits = []
    for r in out:
        bits.append(str(r[0]) if len(r) == 1 else "%d:%d" % (r[0], r[-1]))
    return ",".join(bits)


class Box(object):
    """One element of the deck, drawn as a rectangle."""

    hidden = False
    dropped = False           # past --max-elements, not a choice of anyone's

    def __init__(self, el):
        self.el = el
        self.name = el.name
        self.kind = el.kind
        self.subckt = el.subckt or ""
        self.rails = []           # rail nets this element ties to
        self.layer = 0
        self.order = 0
        self.x = self.y = 0
        self.w = self.h = 0

    @property
    def sub_label(self):
        if self.subckt:
            return self.subckt
        return {"R": "resistor", "C": "capacitor", "V": "source",
                "I": "source", "L": "inductor"}.get(self.kind, self.kind)


class NetNode(object):
    """A net, or a bus of nets wired the same way, drawn as a pill."""

    hidden = False            # taken out of the picture on purpose
    orphan = False            # nothing left that it touches is drawn

    def __init__(self, label, nets, boxes, width, degree=None,
                 hidden_ends=0, loads=()):
        self.label = label
        self.loads = list(loads)  # R/C/L folded into this node
        self.nets = nets          # the real net names behind this node
        self.boxes = boxes        # the Box objects it touches AND that are drawn
        self.width = width        # how many nets collapsed into it
        self.degree = len(boxes) if degree is None else degree
        self.hidden_ends = hidden_ends
        self.layer = 0
        self.order = 0
        self.x = self.y = 0
        self.w = self.h = 0

    @property
    def floating(self):
        """One-sided *in the deck* - never merely in the drawing.

        Counted on what the checker read, not on what survived --hide.  A
        net whose other end sits on a hidden instance is wired; calling it
        one-sided because the picture was tidied would turn this from a
        verification tool into a generator of false alarms.
        """
        return self.degree < 2

    @property
    def crosses(self):
        """Connected, but to something not drawn."""
        return self.hidden_ends > 0

    @property
    def leaf(self):
        """One instance on it, and whatever passives - not a finding.

        This is the node that looks like it should be red and is not: it is
        terminated, or loaded, and the thing on the other end is an R or a C
        rather than a block.  What exactly is there is in ``loads``.
        """
        return len(self.boxes) < 2 and not self.floating

    @property
    def load_mark(self):
        kinds = sorted(set(l["kind"] for l in self.loads))
        return "".join(kinds)


class Graph(object):
    def __init__(self):
        self.boxes = []
        self.nets = []
        self.notes = []           # what the picture does not cover
        self.dropped = 0          # elements left out by --max-elements
        self.hidden = 0           # instances left out of the picture
        self.lost = 0             # net nodes no visible instance touches
        self.lost_floating = 0    # ... of those, ones that were one-sided
        self.hidden_nets = 0      # nets hidden on purpose
        self.hidden_nets_floating = 0
        # In the html viewer nothing is thrown away: what is hidden is still
        # in the page, drawn as gone, so the page can give it back.  The
        # static formats really do drop it - a picture for a report should
        # not carry what was taken out of it.
        self.gone_boxes = []
        self.gone_nets = []
        self.absorbed = 0         # passives folded into their node
        self.columns = []         # names of the element columns, left to right
        self.unplaced = 0         # boxes no layout file spoke for
        self.geom = {"x": {}, "width": {}}   # per-column x and width


def _rail_filter(patterns):
    rx = [re.compile(p, re.I) for p in patterns]
    return lambda net: any(r.search(net) for r in rx)


def _box_filter(patterns):
    """Match an instance by its name or by the subckt it calls."""
    rx = [re.compile(p, re.I) for p in patterns]

    def match(box):
        hay = "%s %s" % (box.name, box.subckt or box.kind)
        return any(r.search(hay) for r in rx)
    return match


def _load_of(el):
    """The bit of an R/C/L worth showing: its name, kind and value."""
    value = ""
    for tok in el.tail:
        if "=" not in tok:
            value = tok
            break
    return {"name": el.name, "kind": el.kind, "value": value,
            "where": el.where()}


def _net_filter(patterns):
    """Match a net node by its label or by any net collapsed into it."""
    rx = [re.compile(p, re.I) for p in patterns]

    def match(node):
        for hay in [node.label] + list(node.nets):
            if any(r.search(hay) for r in rx):
                return True
        return False
    return match


def build(deck, rails=DEFAULT_RAILS, group_buses=True, max_elements=80,
          hide=(), only=(), hide_names=(), hide_nets=(), hide_net_names=(),
          keep_hidden=False):
    """Turn a parsed :class:`deck.Deck` into a drawable graph.

    The hide arguments take instances and nets out of the *picture*.
    Connectivity is still worked out over the whole deck, so what is left
    tells the truth about what was hidden rather than about itself.

    With ``keep_hidden`` what was hidden is kept in the graph, marked, so the
    html viewer can hand it back; the static formats drop it for real.
    """
    g = Graph()
    is_rail = _rail_filter(rails) if rails else (lambda net: False)

    by_el, boxes = {}, []
    for el in deck.elements:
        if el.kind in PASSIVE_KINDS:
            signal = [n for n in el.nodes if not is_rail(n)]
            if len(set(signal)) <= 1:
                g.absorbed += 1
                continue              # folded into its node further down
        box = Box(el)
        by_el[id(el)] = box
        boxes.append(box)

    drop = _box_filter(hide) if hide else None
    keep = _box_filter(only) if only else None
    names = set(hide_names or ())
    for box in boxes:
        if box.name in names:
            box.hidden = True
        elif keep is not None and not keep(box):
            box.hidden = True
        elif drop is not None and drop(box):
            box.hidden = True

    # net -> every box that touches it, hidden ones included: the picture is
    # allowed to leave things out, the connectivity behind it is not
    touch, loads_of, degree_of = {}, {}, {}
    for net, users in deck.net_users.items():
        on, loads, seen = [], [], set()
        for el, _idx in users:
            if id(el) in seen:
                continue
            seen.add(id(el))
            box = by_el.get(id(el))
            if box is not None:
                on.append(box)
            else:
                loads.append(_load_of(el))
        if is_rail(net):
            for box in on:
                if net not in box.rails:
                    box.rails.append(net)
            continue
        if not on and not loads:
            continue
        touch[net] = on
        loads_of[net] = loads
        # counted over every element on the net, passives included, so that
        # "one-sided" still means what the checker means by it
        degree_of[net] = len(seen)

    live = [b for b in boxes if not b.hidden]
    if max_elements and len(live) > max_elements:
        # a size guard, not a choice: keep the most-connected ones, since a
        # hub tells you more than a leaf
        degree = {}
        for box in live:
            degree[id(box)] = len([n for n in box.el.nodes if not is_rail(n)])
        live.sort(key=lambda b: -degree[id(b)])
        for box in live[max_elements:]:
            box.hidden = True
            box.dropped = True
        g.dropped = len(live) - max_elements

    # collapse buses: same stem, same endpoints, and more than one member.
    # Endpoints here are the real ones, so two nets wired alike still group
    # when one of their instances is not on screen.
    groups = {}
    for net, on in touch.items():
        stem, idx = _stem(net) if group_buses else (net, None)
        key = (stem, idx is None, tuple(sorted(id(b) for b in on)),
               tuple(sorted(l["kind"] for l in loads_of[net])))
        groups.setdefault(key, []).append((net, idx, on))

    drop_net = _net_filter(hide_nets) if hide_nets else None
    net_names = set(hide_net_names or ())

    nodes = []
    for (stem, plain, _sig, _loads), members in groups.items():
        nets = [m[0] for m in members]
        on = members[0][2]
        if plain or len(members) == 1:
            label = nets[0] if len(nets) == 1 else "%s x%d" % (stem, len(nets))
        else:
            label = "%s[%s]" % (stem, _ranges(m[1] for m in members))
        shown = [b for b in on if not b.hidden]
        loads = []
        for member in members:
            loads.extend(loads_of[member[0]])
        node = NetNode(label, sorted(nets), shown, len(nets),
                       degree=degree_of[members[0][0]],
                       hidden_ends=len(on) - len(shown), loads=loads)
        node.all_boxes = on
        nodes.append(node)

    for node in nodes:
        if node.label in net_names or any(n in net_names for n in node.nets):
            node.hidden = True
        elif drop_net is not None and drop_net(node):
            node.hidden = True

        if node.hidden:
            g.hidden_nets += 1
            if node.degree < 2:
                g.hidden_nets_floating += 1
            continue

        # A net that LOST something to hiding and is left without two drawn
        # ends is a stub: hide the source that drove a_dq0 and what remains
        # hangs off one box saying nothing.  A net that never lost anything
        # is left alone however few boxes it has - one instance and a
        # termination is a real node, not a stub - and a net that was
        # already one-sided in the deck is the finding, not clutter.
        lost_a_box = len(node.boxes) < len(node.all_boxes)
        if (not node.boxes) or (lost_a_box and len(node.boxes) < 2
                                and node.degree >= 2):
            node.orphan = True
            g.lost += 1
            if node.degree < 2:
                g.lost_floating += 1

    g.boxes = [b for b in boxes if not b.hidden]
    g.nets = [n for n in nodes if not (n.hidden or n.orphan)]
    g.hidden = sum(1 for b in boxes if b.hidden and not getattr(b, "dropped", False))
    if keep_hidden:
        g.gone_boxes = [b for b in boxes
                        if b.hidden and not getattr(b, "dropped", False)]
        g.gone_nets = [n for n in nodes if n.hidden or n.orphan]

    for box in g.boxes:
        box.rails.sort()
    g.nets.sort(key=lambda n: n.label)
    return g


# ------------------------------------------------------------------ layout
class Layout(object):
    """Where the columns are and which instance sits in which.

    The automatic layering is a guess from connectivity alone.  It cannot
    know that these four instances are "the TX side" and those are "package",
    so once someone has arranged a deck by hand that arrangement is worth
    more than the guess - and worth keeping, which is why it lives in a file
    next to the deck rather than in the picture.
    """

    VERSION = 1

    def __init__(self, columns=None, elements=None, hidden=None,
                 hidden_nets=None):
        self.columns = list(columns or [])      # column names, left to right
        self.elements = dict(elements or {})    # element name -> {column,row}
        self.hidden = list(hidden or [])        # instances left out on purpose
        self.hidden_nets = list(hidden_nets or [])

    def column_of(self, name):
        spec = self.elements.get(name)
        return None if spec is None else spec.get("column")

    def to_dict(self):
        return {"version": self.VERSION,
                "columns": [{"name": n} for n in self.columns],
                "hidden": sorted(self.hidden),
                "hidden_nets": sorted(self.hidden_nets),
                # rows are written as whole numbers so that reading a file
                # and writing it back leaves it byte for byte the same - a
                # layout file lives in version control
                "elements": {k: {"column": int(v["column"]),
                                 "row": int(round(v["row"]))}
                             for k, v in sorted(self.elements.items())}}


class LayoutError(Exception):
    pass


def load_layout(text, where="layout"):
    """Parse a layout file.  Anything malformed is an error, not a shrug:
    silently falling back to the automatic layout would look like the hand
    arrangement was lost."""
    import json
    try:
        raw = json.loads(text)
    except ValueError as exc:
        raise LayoutError("%s is not valid JSON: %s" % (where, exc))
    if not isinstance(raw, dict):
        raise LayoutError("%s: expected an object at the top level" % where)

    cols = raw.get("columns") or []
    names = []
    for i, col in enumerate(cols):
        if isinstance(col, dict):
            names.append(str(col.get("name", "column %d" % (i + 1))))
        else:
            names.append(str(col))
    if not names:
        raise LayoutError("%s: no columns defined" % where)

    elements = {}
    for name, spec in (raw.get("elements") or {}).items():
        if not isinstance(spec, dict):
            raise LayoutError("%s: element %s should be an object" %
                              (where, name))
        try:
            col = int(spec.get("column", 0))
            row = float(spec.get("row", 0))
        except (TypeError, ValueError):
            raise LayoutError("%s: element %s has a non-numeric column/row"
                              % (where, name))
        if not 0 <= col < len(names):
            raise LayoutError("%s: element %s is in column %d, but only %d "
                              "column(s) are defined" %
                              (where, name, col, len(names)))
        elements[name] = {"column": col, "row": row}

    lists = {}
    for key in ("hidden", "hidden_nets"):
        got = raw.get(key) or []
        if not isinstance(got, list):
            raise LayoutError("%s: '%s' should be a list of names"
                              % (where, key))
        lists[key] = [str(h) for h in got]
    return Layout(names, elements, lists["hidden"], lists["hidden_nets"])


def dump_layout(layout):
    import json
    return json.dumps(layout.to_dict(), indent=2, sort_keys=False) + "\n"


def layout_of(g):
    """The arrangement a picture currently has, as a saveable Layout."""
    elements = {}
    for box in g.boxes:
        elements[box.name] = {"column": int(box.layer // 2),
                              "row": int(box.order)}
    return Layout(g.columns, elements)


def _layer_from_layout(g, layout):
    """Columns come from the file; nets still find their own gap."""
    ncols = len(layout.columns)
    g.columns = list(layout.columns)
    unplaced = []
    # hidden boxes are laid out too: the viewer needs somewhere to put one
    # back to, and the column it was in is the only answer anyone expects
    for box in g.boxes + g.gone_boxes:
        spec = layout.elements.get(box.name)
        if spec is None:
            unplaced.append(box)
            continue
        box.layer = int(spec["column"]) * 2
        box.order = float(spec["row"])

    if unplaced:
        # a deck grows; what the file has not heard of goes in a column of
        # its own rather than being scattered where it might pass unnoticed
        col = ncols
        g.columns.append("unplaced")
        for i, box in enumerate(sorted(unplaced, key=lambda b: b.name)):
            box.layer = col * 2
            box.order = i
    g.unplaced = len(unplaced)

    for net in g.nets + g.gone_nets:
        ends = net.boxes or getattr(net, "all_boxes", [])
        net.layer = (min(b.layer for b in ends) + 1) if ends else 1


def _layer(g):
    """Bipartite layering: elements on even columns, nets on odd ones.

    Breadth-first from the busiest element, so the instance everything hangs
    off lands on the left and the picture reads outward from it.

    Every connected part starts again at column 0 and stacks underneath the
    last, rather than continuing to the right.  An IO called out bit by bit
    is eight or sixty-four separate parts of exactly the same shape, and
    marching them rightwards turns a three-column picture into a sixty-column
    one where nothing lines up with its opposite number.
    """
    adj = {}
    for net in g.nets:
        for box in net.boxes:
            adj.setdefault(id(box), []).append(net)
            adj.setdefault(id(net), []).append(box)

    seen = set()
    roots = sorted(g.boxes, key=lambda b: -len(adj.get(id(b), ())))
    order_seq = 0

    for root in roots:
        if id(root) in seen:
            continue
        root.layer = 0
        root.order = order_seq
        order_seq += 1
        seen.add(id(root))
        frontier = [root]
        depth = 0
        while frontier:
            nxt = []
            for node in frontier:
                for peer in adj.get(id(node), ()):
                    if id(peer) in seen:
                        continue
                    seen.add(id(peer))
                    peer.layer = depth + 1
                    peer.order = order_seq
                    order_seq += 1
                    nxt.append(peer)
            frontier = nxt
            depth += 1

    # nets nothing reached (every endpoint dropped) sit one past their box
    for net in g.nets:
        if net.boxes and net.layer == 0:
            net.layer = max(b.layer for b in net.boxes) + 1

    widest = max([b.layer for b in g.boxes] or [0])
    g.columns = ["column %d" % (i + 1) for i in range(widest // 2 + 1)]


def _order(g, passes=4, pinned=()):
    """Barycentre sweeps: pull each node next to the average of its peers.

    Nodes in ``pinned`` keep the order they came with - a row somebody set by
    hand is an instruction, not a starting guess.
    """
    fixed = set(pinned)
    adj = {}
    for net in g.nets:
        for box in net.boxes:
            adj.setdefault(id(box), []).append(net)
            adj.setdefault(id(net), []).append(box)

    layers = {}
    for node in g.boxes + g.nets:
        layers.setdefault(node.layer, []).append(node)
    # a column somebody made and then emptied is still a column: dropping it
    # silently would move everything to its right and lose the arrangement
    for i in range(len(g.columns)):
        layers.setdefault(i * 2, [])
    for nodes in layers.values():
        nodes.sort(key=lambda n: n.order)
        for i, node in enumerate(nodes):
            node.order = i

    keys = sorted(layers)
    for step in range(passes):
        seq = keys if step % 2 == 0 else list(reversed(keys))
        for lay in seq:
            nodes = layers[lay]
            for node in nodes:
                if id(node) in fixed:
                    continue
                peers = [p for p in adj.get(id(node), ())
                         if abs(p.layer - lay) == 1]
                if peers:
                    node.order = sum(p.order for p in peers) / float(len(peers))
            nodes.sort(key=lambda n: n.order)
            for i, node in enumerate(nodes):
                node.order = i
    return layers


# ------------------------------------------------------------------ render
CHAR_W = 6.9          # monospace advance at 11.5px, near enough for sizing
MARGIN = 30
EMPTY_COL_W = 130     # a column you emptied keeps its slot to drop into
BOX_PAD = 18
COL_GAP = 62
ROW_GAP = 16


def _esc(text):
    return (str(text).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def _measure(g):
    for box in g.boxes + g.gone_boxes:
        widest = max(len(box.name), len(box.sub_label),
                     len(", ".join(box.rails)) + 2 if box.rails else 0)
        box.w = max(112, widest * CHAR_W + BOX_PAD * 2)
        box.h = 54 if box.rails else 40
    for net in g.nets + g.gone_nets:
        label = net.label
        if net.width > 1:
            label += "  x%d" % net.width
        net.w = max(56, len(label) * CHAR_W + 20)
        net.h = 22


def _place(g, layers):
    xs, widths, x = {}, {}, float(MARGIN)
    for lay in sorted(layers):
        wide = max([n.w for n in layers[lay]] or [EMPTY_COL_W])
        xs[lay] = x
        widths[lay] = wide
        x += wide + COL_GAP
    total_w = x - COL_GAP + MARGIN
    g.geom = {"x": xs, "width": widths}

    heights = {}
    for lay, nodes in layers.items():
        heights[lay] = (sum(n.h for n in nodes) + ROW_GAP * (len(nodes) - 1)
                        if nodes else 0)
    tallest = max(heights.values()) if heights else 0

    for lay, nodes in layers.items():
        wide = widths[lay]
        y = MARGIN + (tallest - heights[lay]) / 2.0
        for node in sorted(nodes, key=lambda n: n.order):
            node.x = xs[lay] + (wide - node.w) / 2.0
            node.y = y
            y += node.h + ROW_GAP
    return total_w, tallest + MARGIN * 2


def _edge(a, b):
    """Line between two boxes, clipped to their borders."""
    ax, ay = a.x + a.w / 2.0, a.y + a.h / 2.0
    bx, by = b.x + b.w / 2.0, b.y + b.h / 2.0
    return _clip(ax, ay, bx, by, a) + _clip(bx, by, ax, ay, b)


def _clip(cx, cy, tx, ty, node):
    dx, dy = tx - cx, ty - cy
    if dx == 0 and dy == 0:
        return [cx, cy]
    hw, hh = node.w / 2.0 + 1, node.h / 2.0 + 1
    scale = min(hw / abs(dx) if dx else 1e9, hh / abs(dy) if dy else 1e9)
    return [cx + dx * scale, cy + dy * scale]


STYLE = """
  .bg   { fill: #ffffff; }
  .box  { fill: #eef2f8; stroke: #4a5b73; stroke-width: 1.4; }
  .name { fill: #1b2431; font: 600 12px ui-monospace, Menlo, Consolas, monospace; }
  .sub  { fill: #5c6b80; font: 11px ui-monospace, Menlo, Consolas, monospace; }
  .rail { fill: #7a6a45; font: 10.5px ui-monospace, Menlo, Consolas, monospace; }
  .net  { fill: #ffffff; stroke: #6b7c94; stroke-width: 1.2; }
  .netl { fill: #24303f; font: 11.5px ui-monospace, Menlo, Consolas, monospace; }
  .wire { stroke: #7f8fa4; stroke-width: 1.3; fill: none; }
  .bus  { stroke: #46617f; stroke-width: 2.6; fill: none; }
  .leaf      { fill: #fbf7ec; stroke: #b08d3f; stroke-width: 1.3; }
  .leafl     { fill: #6d5720; font: 11.5px ui-monospace, Menlo, Consolas, monospace; }
  .cross     { fill: #f4f6f8; stroke: #8a93a0; stroke-width: 1.3;
               stroke-dasharray: 5 3; }
  .crossl    { fill: #5c6b80; font: 11.5px ui-monospace, Menlo, Consolas, monospace; }
  .crosswire { stroke: #98a2b0; stroke-width: 1.3; stroke-dasharray: 2 3; fill: none; }
  .float     { fill: #fdecea; stroke: #c0392b; stroke-width: 1.5; }
  .floatl    { fill: #90291d; font: 11.5px ui-monospace, Menlo, Consolas, monospace; }
  .floatwire { stroke: #c0392b; stroke-width: 1.3; stroke-dasharray: 5 3; fill: none; }
  .head { fill: #1b2431; font: 600 13px ui-monospace, Menlo, Consolas, monospace; }
  .note { fill: #5c6b80; font: 11px ui-monospace, Menlo, Consolas, monospace; }
  .col  { fill: #7286a0; font: 600 11px ui-monospace, Menlo, Consolas, monospace;
          letter-spacing: 0.06em; }
  .dim  { opacity: 0.10; }
  .hot rect { stroke-width: 2.6; }
  .hot-wire { stroke-width: 3.2; }
  #scene .node { cursor: pointer; }
@media (prefers-color-scheme: dark) {
  .bg   { fill: #12161c; }
  .box  { fill: #1e2733; stroke: #7e93ad; }
  .name { fill: #e8edf4; }
  .sub  { fill: #93a3b8; }
  .rail { fill: #c3ad78; }
  .net  { fill: #171d25; stroke: #8598ad; }
  .netl { fill: #dae2ec; }
  .wire { stroke: #6c7d92; }
  .bus  { stroke: #8fb4d9; }
  .leaf      { fill: #241f13; stroke: #c3ad78; }
  .leafl     { fill: #d8c08a; }
  .cross     { fill: #1a1f27; stroke: #7d8795; }
  .crossl    { fill: #93a3b8; }
  .crosswire { stroke: #79838f; }
  .float     { fill: #33191a; stroke: #e2725f; }
  .floatl    { fill: #f0a596; }
  .floatwire { stroke: #e2725f; }
  .head { fill: #e8edf4; }
  .note { fill: #93a3b8; }
  .col  { fill: #8fa2bb; }
}
"""


def render_svg(g, title="deck connectivity", header=(), embed_header=True,
               layout=None):
    """The picture.  ``embed_header`` off when the HTML chrome shows it.

    With a ``layout`` the element columns come from the file and stay put;
    without one they are guessed from connectivity.
    """
    _measure(g)
    if g.boxes or g.nets:
        if layout is not None:
            _layer_from_layout(g, layout)
            layers = _order(g, passes=4, pinned=[id(b) for b in g.boxes])
        else:
            _layer(g)
            layers = _order(g, passes=4)
    else:
        layers = {}
    width, height = _place(g, layers) if layers else (420, 120)

    head_lines = [title] + list(header) if embed_header else []
    top = (22 + 16 * len(head_lines)) if head_lines else 20
    height += top

    out = ['<svg xmlns="http://www.w3.org/2000/svg" id="deck-svg" '
           'width="%d" height="%d" viewBox="0 0 %d %d" '
           'font-family="monospace">' % (width, height, width, height),
           "<style>%s</style>" % STYLE,
           '<rect class="bg" width="100%" height="100%"/>']

    if head_lines:
        out.append('<text class="head" x="24" y="30">%s</text>' % _esc(title))
        for i, line in enumerate(header):
            out.append('<text class="note" x="24" y="%d">%s</text>'
                       % (48 + i * 15, _esc(line)))

    g.geom["top"] = top
    for node in g.boxes + g.nets:
        node.y += top
    # what is hidden was never placed; it sits at the origin, which is where
    # the viewer's transforms measure from when it puts one back
    for node in g.gone_boxes + g.gone_nets:
        node.x = node.y = 0

    # ids let the HTML viewer light up one net and everything on it - and
    # hidden nodes are in the page too, so it can give them back
    all_boxes = g.boxes + g.gone_boxes
    all_nets = g.nets + g.gone_nets
    bid = {id(b): "b%d" % i for i, b in enumerate(all_boxes)}
    nid = {id(n): "n%d" % i for i, n in enumerate(all_nets)}

    out.append('<g id="scene">')
    out.append('<g id="colheads"></g><g id="dropzones"></g>'
               '<line id="dropline" x1="0" y1="0" x2="0" y2="0"/>'
               '<rect id="band" x="0" y="0" width="0" height="0"/>')

    if layout is not None and g.columns:
        for i, name in enumerate(g.columns):
            lay = i * 2
            if lay not in g.geom.get("x", {}):
                continue
            cx = g.geom["x"][lay] + g.geom["width"][lay] / 2.0
            out.append('<text class="col" x="%.1f" y="%.1f" '
                       'text-anchor="middle">%s</text>'
                       % (cx, top + 18, _esc(name)))

    # wires first, so the boxes sit on top of them.  Every endpoint gets one,
    # hidden ones marked, so nothing has to be invented when one comes back.
    for net in all_nets:
        if net.floating:
            cls = "floatwire"
        elif net.crosses:
            cls = "crosswire"
        else:
            cls = "bus" if net.width > 1 else "wire"
        for box in getattr(net, "all_boxes", net.boxes):
            if id(box) not in bid:
                continue
            gone = (net.hidden or net.orphan or box.hidden) and " gone" or ""
            x1, y1, x2, y2 = _edge(net, box)
            out.append('<path class="%s wire-of%s" data-net="%s" '
                       'data-box="%s" d="M %.1f %.1f L %.1f %.1f"/>'
                       % (cls, gone, nid[id(net)], bid[id(box)],
                          x1, y1, x2, y2))

    for net in all_nets:
        label = net.label + ("  x%d" % net.width if net.width > 1 else "")
        if net.floating:
            cls, lcls = "float", "floatl"
        elif net.crosses:
            cls, lcls = "cross", "crossl"
            label += "  \u2192"          # it carries on somewhere not drawn
        elif net.leaf:
            # one instance and a termination: not a finding, and the mark
            # says why without having to ask
            cls, lcls = "leaf", "leafl"
            if net.load_mark:
                label += "  \u00b7" + net.load_mark
        else:
            cls, lcls = "net", "netl"
        out.append('<g class="node net-node%s" id="%s" data-label="%s" '
                   'data-search="%s">'
                   % (" gone" if (net.hidden or net.orphan) else "",
                      nid[id(net)], _esc(net.label),
                      _esc(" ".join([net.label] + net.nets))))
        out.append('<rect class="%s" x="%.1f" y="%.1f" width="%.1f" '
                   'height="%.1f" rx="11"/>'
                   % (cls, net.x, net.y, net.w, net.h))
        out.append('<text class="%s" x="%.1f" y="%.1f" '
                   'text-anchor="middle">%s</text>'
                   % (lcls, net.x + net.w / 2.0, net.y + 15, _esc(label)))
        note = ""
        if net.floating:
            note = " (touched by one port only)"
        elif net.crosses:
            note = (" (also reaches %d instance(s) not drawn)"
                    % net.hidden_ends)
        if net.loads:
            note += " [%s]" % ", ".join(
                "%s %s" % (l["name"], l["value"]) for l in net.loads[:6])
        out.append("<title>%s</title>" % _esc(", ".join(net.nets) + note))
        out.append("</g>")

    for box in all_boxes:
        out.append('<g class="node box-node%s" id="%s" data-name="%s" '
                   'data-search="%s">'
                   % (" gone" if box.hidden else "",
                      bid[id(box)], _esc(box.name),
                      _esc(" ".join([box.name, box.sub_label]))))
        out.append('<rect class="box" x="%.1f" y="%.1f" width="%.1f" '
                   'height="%.1f" rx="4"/>' % (box.x, box.y, box.w, box.h))
        mid = box.x + box.w / 2.0
        out.append('<text class="name" x="%.1f" y="%.1f" '
                   'text-anchor="middle">%s</text>'
                   % (mid, box.y + 17, _esc(box.name)))
        out.append('<text class="sub" x="%.1f" y="%.1f" '
                   'text-anchor="middle">%s</text>'
                   % (mid, box.y + 31, _esc(box.sub_label)))
        if box.rails:
            out.append('<text class="rail" x="%.1f" y="%.1f" '
                       'text-anchor="middle">%s</text>'
                       % (mid, box.y + 45, _esc("\u23da " + ", ".join(box.rails))))
        out.append("<title>%s at %s</title>"
                   % (_esc(box.name), _esc(box.el.where())))
        out.append("</g>")

    out.append("</g>")
    out.append("</svg>")
    return "\n".join(out) + "\n"


def viewer_state(g):
    """Everything the in-page editor needs to re-place nodes as they move.

    It mirrors :func:`_place`, and deliberately so: the arrangement you drag
    into shape must come back identical when the file is regenerated, so the
    two placements have to follow the same rule.
    """
    boxes = []
    for i, box in enumerate(g.boxes + g.gone_boxes):
        boxes.append({"id": "b%d" % i, "name": box.name,
                      "sub": box.sub_label, "hidden": bool(box.hidden),
                      "where": box.el.where(), "rails": list(box.rails),
                      "col": int(box.layer // 2), "row": float(box.order),
                      "x": round(box.x, 2), "y": round(box.y, 2),
                      "w": round(box.w, 2), "h": round(box.h, 2)})
    bid = {id(b): "b%d" % i for i, b in enumerate(g.boxes + g.gone_boxes)}
    nets = []
    for j, net in enumerate(g.nets + g.gone_nets):
        nets.append({"id": "n%d" % j, "label": net.label,
                     "hidden": bool(net.hidden), "degree": net.degree,
                     "red": bool(net.floating), "nets": net.nets[:24],
                     "loads": [{"name": l["name"], "kind": l["kind"],
                                "value": l["value"], "where": l["where"]}
                               for l in net.loads[:24]],
                     "boxes": [bid[id(b)] for b in
                               getattr(net, "all_boxes", net.boxes)
                               if id(b) in bid],
                     "x": round(net.x, 2), "y": round(net.y, 2),
                     "w": round(net.w, 2), "h": round(net.h, 2)})
    return {"columns": list(g.columns), "boxes": boxes, "nets": nets,
            "geom": {"colGap": COL_GAP, "rowGap": ROW_GAP, "margin": MARGIN,
                     "emptyW": EMPTY_COL_W, "top": g.geom.get("top", 20)}}


def render_dot(g):
    """Graphviz source, for a deck too big for the built-in layout."""
    lines = ["graph deck {", '  graph [rankdir=LR, splines=true];',
             '  node [fontname="monospace", fontsize=10];']
    for i, box in enumerate(g.boxes):
        rails = ("\\n" + ", ".join(box.rails)) if box.rails else ""
        lines.append('  b%d [shape=box, style=filled, fillcolor="#eef2f8", '
                     'label="%s\\n%s%s"];'
                     % (i, box.name, box.sub_label, rails))
    ids = {id(b): i for i, b in enumerate(g.boxes)}
    for j, net in enumerate(g.nets):
        label = net.label + (" x%d" % net.width if net.width > 1 else "")
        # nets are pills and elements are boxes, the same way round as the
        # SVG, so the two formats read as one picture
        if net.floating:
            lines.append('  n%d [shape=oval, style=filled, '
                         'fillcolor="#fdecea", color="#c0392b", label="%s"];'
                         % (j, label))
        else:
            lines.append('  n%d [shape=oval, style=filled, '
                         'fillcolor="#ffffff", label="%s"];' % (j, label))
        for box in net.boxes:
            style = ' [color="#c0392b", style=dashed]' if net.floating else (
                ' [penwidth=2.2]' if net.width > 1 else "")
            lines.append("  n%d -- b%d%s;" % (j, ids[id(box)], style))
    lines.append("}")
    return "\n".join(lines) + "\n"


# -------------------------------------------------------------- html viewer
# A deck with eighty instances does not fit on a screen, and a picture you
# cannot get into is not much better than the report it replaced.  This wraps
# the same SVG in the smallest viewer that makes a big one usable: wheel to
# zoom, drag to pan, type to find a net, click to light up what touches it.
# No library and no network - the environments this runs in have neither.
VIEWER_CSS = """
:root { color-scheme: light dark; }
* { box-sizing: border-box; }
html, body { margin: 0; height: 100%; }
body {
  display: flex; flex-direction: column; background: #ffffff; color: #1b2431;
  font: 12px ui-monospace, Menlo, Consolas, monospace;
}
#bar {
  display: flex; align-items: center; gap: 10px; flex-wrap: wrap;
  padding: 8px 12px; background: #f6f8fb;
}
/* Status lives on its own fixed-height line, never among the buttons.
   Writing "3 selected" into the button row made it wrap, which pushed the
   canvas down by a line - between a press and its release, so the click
   landed on the background and selecting an instance did nothing. */
#status {
  display: flex; gap: 14px; align-items: center; height: 22px;
  padding: 0 12px 4px; background: #f6f8fb; white-space: nowrap;
  overflow-x: auto; border-bottom: 1px solid #d4dbe4;
}
#bar h1 { margin: 0 8px 0 0; font-size: 13px; font-weight: 600; }
#notes { color: #5c6b80; }
#notes b { color: #c0392b; font-weight: 600; }
button, input {
  font: inherit; color: inherit; background: #ffffff;
  border: 1px solid #b6c0cd; border-radius: 4px; padding: 3px 8px;
}
button { cursor: pointer; }
button:hover { background: #e9eff6; }
input { width: 170px; }
#zoom { min-width: 52px; text-align: right; color: #5c6b80; }
#stage { flex: 1; overflow: hidden; position: relative; }
#stage, #deck-svg {
  -webkit-user-select: none; -moz-user-select: none; -ms-user-select: none;
  user-select: none; touch-action: none;
}
#deck-svg { width: 100%; height: 100%; display: block; }
#hint {
  position: absolute; right: 12px; bottom: 10px; color: #8794a6;
  pointer-events: none;
}
#edit.on { background: #dcebff; border-color: #6f9ad6; }
#editbar { display: none; gap: 8px; align-items: center; }
#editbar.on { display: flex; }
#dirty { color: #b06a00; }
body.editing #scene .node { cursor: grab; }
body.editing #scene .node.box-node rect { stroke-dasharray: none; }
body.editing #scene .node.lift { cursor: grabbing; opacity: 0.85; }
/* feedback overlays only: the drop column is worked out from coordinates,
   never by hit-testing, so these must not take the press away from a box.
   fill:transparent still receives pointer events - fill:none is not enough
   either, since the stroke would. */
#dropzones rect { fill: transparent; pointer-events: none; }
#dropzones, #dropline, #band { pointer-events: none; }
#dropzones rect.hot { fill: rgba(70,130,200,0.13); }
#dropline { stroke: #2f6fb5; stroke-width: 3; display: none; }
#band { fill: rgba(47,111,181,0.10); stroke: #2f6fb5; stroke-width: 1.2;
        stroke-dasharray: 4 3; display: none; }
#scene .node.sel rect { stroke: #2f6fb5; stroke-width: 2.8; }
#scene .node.sel rect.box { fill: #dcebff; }
#selinfo { color: #2f6fb5; }
#hideinfo { color: #b06a00; }
#scene .gone { display: none; }
#panel {
  position: absolute; top: 8px; right: 8px; width: 260px; max-height: 82%;
  display: none; flex-direction: column; background: #ffffff;
  border: 1px solid #b6c0cd; border-radius: 6px; overflow: hidden;
  box-shadow: 0 4px 16px rgba(20,30,45,0.14);
}
#panel.on { display: flex; }
#panelhead {
  display: flex; align-items: center; gap: 8px; padding: 6px 8px;
  border-bottom: 1px solid #e2e7ee; font-weight: 600;
}
#panelhead button { padding: 1px 6px; }
#panelbody { overflow-y: auto; padding: 4px 0 8px; }
#panelbody h4 {
  margin: 8px 8px 4px; font-size: 11px; font-weight: 600; color: #5c6b80;
  text-transform: uppercase; letter-spacing: 0.06em;
}
#panelbody .row {
  display: flex; align-items: baseline; gap: 6px; padding: 3px 10px;
  cursor: pointer;
}
#panelbody .row:hover { background: #e9eff6; }
#panelbody .row .why { color: #8794a6; font-size: 11px; margin-left: auto; }
#panelbody .row.auto { cursor: default; opacity: 0.75; }
#panelbody .row.auto:hover { background: none; }
#panelbody .none { padding: 3px 10px; color: #8794a6; }
/* The inspector floats over the canvas: it answers "why is this node not
   red" for one selected node, so it is built once per selection rather
   than per node, and it can never reflow the page under the pointer. */
#inspect {
  position: absolute; left: 8px; bottom: 8px; width: 300px; max-height: 46%;
  display: none; flex-direction: column; background: #ffffff;
  border: 1px solid #b6c0cd; border-radius: 6px; overflow: hidden;
  box-shadow: 0 4px 16px rgba(20,30,45,0.14);
}
#inspect.on { display: flex; }
#inspecthead {
  padding: 6px 9px; border-bottom: 1px solid #e2e7ee; font-weight: 600;
}
#inspecthead .sub { font-weight: 400; color: #5c6b80; }
#inspectbody { overflow-y: auto; padding: 5px 0 7px; }
#inspectbody .line { display: flex; gap: 8px; padding: 2px 9px; }
#inspectbody .line .k { color: #8794a6; min-width: 74px; }
#inspectbody .line .v { flex: 1; word-break: break-all; }
#inspectbody .why { padding: 4px 9px; color: #6d5720; background: #fbf7ec; }
#scene .node.crossed rect { stroke: #8a93a0; stroke-dasharray: 5 3; }
@media (prefers-color-scheme: dark) {
  #edit.on { background: #24405e; border-color: #5b86bd; }
  #dirty { color: #e0a952; }
  #dropzones rect.hot { fill: rgba(120,170,230,0.16); }
  #dropline { stroke: #8fb4d9; }
  #band { fill: rgba(143,180,217,0.12); stroke: #8fb4d9; }
  #scene .node.sel rect { stroke: #8fb4d9; }
  #scene .node.sel rect.box { fill: #2b3a4d; }
  #selinfo { color: #8fb4d9; }
  #hideinfo { color: #e0a952; }
  #scene .node.crossed rect { stroke: #7d8795; }
  #panel { background: #171d25; border-color: #3d4957;
           box-shadow: 0 4px 16px rgba(0,0,0,0.5); }
  #panelhead { border-bottom-color: #2b3541; }
  #panelbody h4 { color: #93a3b8; }
  #panelbody .row:hover { background: #212b37; }
  #panelbody .row .why, #panelbody .none { color: #7f8fa4; }
  #inspect { background: #171d25; border-color: #3d4957;
             box-shadow: 0 4px 16px rgba(0,0,0,0.5); }
  #inspecthead { border-bottom-color: #2b3541; }
  #inspecthead .sub, #inspectbody .line .k { color: #93a3b8; }
  #inspectbody .why { color: #d8c08a; background: #241f13; }
}
@media (prefers-color-scheme: dark) {
  body { background: #12161c; color: #e8edf4; }
  #bar { background: #1a212a; }
  #status { background: #1a212a; border-bottom-color: #333e4c; }
  #notes { color: #93a3b8; }
  #notes b { color: #e2725f; }
  button, input { background: #202834; border-color: #3d4957; }
  button:hover { background: #2b3644; }
  #zoom, #hint { color: #7f8fa4; }
}
"""

VIEWER_JS = r"""
(function () {
  var svg = document.getElementById('deck-svg');
  var scene = document.getElementById('scene');
  var zoomLabel = document.getElementById('zoom');
  var find = document.getElementById('find');
  var k = 1, tx = 0, ty = 0;

  function apply() {
    scene.setAttribute('transform',
      'translate(' + tx.toFixed(2) + ' ' + ty.toFixed(2) + ') ' +
      'scale(' + k.toFixed(4) + ')');
    zoomLabel.textContent = Math.round(k * 100) + '%';
  }

  // client pixels -> the svg's own units, so zooming holds the point under
  // the cursor still whatever size the window is
  function at(evt) {
    var box = svg.getBoundingClientRect();
    var vb = svg.viewBox.baseVal;
    var scale = Math.min(box.width / vb.width, box.height / vb.height);
    var offX = (box.width - vb.width * scale) / 2;
    var offY = (box.height - vb.height * scale) / 2;
    return { x: (evt.clientX - box.left - offX) / scale,
             y: (evt.clientY - box.top - offY) / scale };
  }

  function zoomTo(next, at_) {
    next = Math.max(0.08, Math.min(12, next));
    tx = at_.x - (at_.x - tx) * (next / k);
    ty = at_.y - (at_.y - ty) * (next / k);
    k = next;
    apply();
  }

  function fit() {
    var b = scene.getBBox();
    if (!b.width || !b.height) { k = 1; tx = ty = 0; apply(); return; }
    var vb = svg.viewBox.baseVal;
    k = Math.min(vb.width / b.width, vb.height / b.height) * 0.94;
    tx = (vb.width - b.width * k) / 2 - b.x * k;
    ty = (vb.height - b.height * k) / 2 - b.y * k;
    apply();
  }

  svg.addEventListener('wheel', function (e) {
    e.preventDefault();
    zoomTo(k * (e.deltaY < 0 ? 1.12 : 1 / 1.12), at(e));
  }, { passive: false });

  // Dragging deliberately uses neither pointer capture nor, where it is
  // missing, pointer events at all.
  //
  //   * setPointerCapture on an <svg> element is not reliable across
  //     browsers, and capturing is only needed because a drag wanders off
  //     the element it started on - listening on the window covers that
  //     everywhere, with nothing to throw.
  //   * Pointer events themselves are off by default in Firefox before 59,
  //     which an EDA site running an old ESR may well be on, so mouse
  //     events stand in when window.PointerEvent is absent.
  var HAS_PTR = !!window.PointerEvent;
  var DOWN = HAS_PTR ? 'pointerdown' : 'mousedown';
  var MOVE = HAS_PTR ? 'pointermove' : 'mousemove';
  var UP = HAS_PTR ? 'pointerup' : 'mouseup';

  var drag = null, dragged = false, editDrag = false, justArranged = false;

  // Element.closest is not on SVG elements in every browser that can run
  // the rest of this, and walking up is three lines
  function nodeAt(target) {
    for (var el = target; el && el !== scene; el = el.parentNode) {
      if (el.classList && el.classList.contains('node')) return el;
    }
    return null;
  }

  function listen() {
    window.addEventListener(MOVE, onMove, true);
    window.addEventListener(UP, onUp, true);
  }
  function unlisten() {
    window.removeEventListener(MOVE, onMove, true);
    window.removeEventListener(UP, onUp, true);
  }

  function onDown(e) {
    if (e.button) return;                     // left button only
    var node = nodeAt(e.target);
    if (window.__editorDown && window.__editorDown(e, node, at(e))) {
      e.preventDefault();                     // no native text drag
      editDrag = true;
      listen();
      return;
    }
    drag = { p: at(e), tx: tx, ty: ty, x: e.clientX, y: e.clientY };
    dragged = false;
    e.preventDefault();
    listen();
  }

  function onMove(e) {
    if (editDrag) { window.__editorMove(at(e)); e.preventDefault(); return; }
    if (!drag) return;
    if (!dragged) {
      if (Math.abs(e.clientX - drag.x) + Math.abs(e.clientY - drag.y) < 4) return;
      dragged = true;
    }
    var p = at(e);
    tx = drag.tx + (p.x - drag.p.x) * k;
    ty = drag.ty + (p.y - drag.p.y) * k;
    apply();
  }

  function onUp() {
    unlisten();
    if (editDrag) {
      editDrag = false;
      // only swallow the click that follows if something actually moved
      justArranged = !!window.__editorUp();
      return;
    }
    drag = null;
  }

  svg.addEventListener(DOWN, onDown);

  document.getElementById('in').onclick = function () {
    var vb = svg.viewBox.baseVal;
    zoomTo(k * 1.3, { x: vb.width / 2, y: vb.height / 2 });
  };
  document.getElementById('out').onclick = function () {
    var vb = svg.viewBox.baseVal;
    zoomTo(k / 1.3, { x: vb.width / 2, y: vb.height / 2 });
  };
  document.getElementById('fit').onclick = fit;
  document.getElementById('one').onclick = function () {
    k = 1; tx = ty = 0; apply();
  };

  var nodes = [].slice.call(scene.querySelectorAll('.node'));
  var wires = [].slice.call(scene.querySelectorAll('.wire-of'));

  function clear() {
    nodes.forEach(function (n) { n.classList.remove('dim', 'hot'); });
    wires.forEach(function (w) { w.classList.remove('dim', 'hot-wire'); });
  }

  // clicking a net lights up every box on it, and the other way round -
  // the question a connectivity picture gets asked most
  function focus(id) {
    var keep = {}, keepW = [];
    wires.forEach(function (w) {
      if (w.dataset.net === id || w.dataset.box === id) {
        keep[w.dataset.net] = keep[w.dataset.box] = 1;
        keepW.push(w);
      }
    });
    keep[id] = 1;
    nodes.forEach(function (n) {
      n.classList.toggle('dim', !keep[n.id]);
      n.classList.toggle('hot', n.id === id);
    });
    wires.forEach(function (w) {
      var on = keepW.indexOf(w) >= 0;
      w.classList.toggle('dim', !on);
      w.classList.toggle('hot-wire', on);
    });
  }

  nodes.forEach(function (n) {
    n.addEventListener('click', function (e) {
      e.stopPropagation();
      if (justArranged) { justArranged = false; return; }
      if (window.__editorClick && window.__editorClick(n, e)) return;
      if (n.classList.contains('hot')) { clear(); } else { focus(n.id); }
    });
  });
  svg.addEventListener('click', function () {
    if (justArranged) { justArranged = false; return; }
    if (window.__editorBgClick && window.__editorBgClick()) return;
    if (!dragged) clear();
  });

  find.addEventListener('input', function () {
    var q = find.value.trim().toLowerCase();
    if (!q) { clear(); return; }
    var hit = {};
    nodes.forEach(function (n) {
      var on = (n.dataset.search || '').toLowerCase().indexOf(q) >= 0;
      n.classList.toggle('dim', !on);
      n.classList.remove('hot');
      if (on) hit[n.id] = 1;
    });
    wires.forEach(function (w) {
      var on = hit[w.dataset.net] || hit[w.dataset.box];
      w.classList.toggle('dim', !on);
      w.classList.remove('hot-wire');
    });
  });

  document.addEventListener('keydown', function (e) {
    if (e.target === find) { if (e.key === 'Escape') { find.value = ''; clear(); find.blur(); } return; }
    if (e.key === '0' || e.key === 'f') fit();
    else if (e.key === '+' || e.key === '=') document.getElementById('in').click();
    else if (e.key === '-') document.getElementById('out').click();
    else if (e.key === '/') { e.preventDefault(); find.focus(); }
    else if (e.key === 'Escape') clear();
  });

  // the editor compares against untransformed scene coordinates, so it
  // needs the pan and zoom taken back out of the pointer position
  window.__toScene = function (p) {
    return { x: (p.x - tx) / k, y: (p.y - ty) / k };
  };
  window.__fit = fit;
  fit();
  window.addEventListener('resize', function () { apply(); });
})();
"""


EDITOR_JS = r"""
(function () {
  var D = window.DECK;
  if (!D) return;
  var svg = document.getElementById('deck-svg');
  var scene = document.getElementById('scene');
  var heads = document.getElementById('colheads');
  var zones = document.getElementById('dropzones');
  var dropline = document.getElementById('dropline');
  var dirty = document.getElementById('dirty');
  var SVGNS = 'http://www.w3.org/2000/svg';

  var start = JSON.parse(JSON.stringify(
    { columns: D.columns, boxes: D.boxes.map(function (b) {
        return { id: b.id, col: b.col, row: b.row }; }) }));
  var cols = D.columns.slice();
  var place = {};                       // box id -> {col, row}
  D.boxes.forEach(function (b) {
    place[b.id] = { col: b.col || 0, row: b.row || 0 };
  });
  var byId = {}, cur = -1, editing = false, changed = false;
  D.boxes.concat(D.nets).forEach(function (n) { byId[n.id] = n; });

  var G = D.geom, geo = {};             // layer -> {x, w}

  // The same rule the generator follows, step for step, so what you arrange
  // here comes back identical when the deck is drawn again.  Instances stay
  // where they were put; the nets between them are re-sorted by the same
  // barycentre sweep the Python side runs.  If these two ever disagree the
  // arrangement would shift the moment it was reloaded, which is the one
  // failure that would make the whole feature pointless.
  function orderLayers(layers, keys) {
    var adj = {};
    D.nets.forEach(function (n) {
      n.boxes.forEach(function (bid) {
        (adj[n.id] = adj[n.id] || []).push(bid);
        (adj[bid] = adj[bid] || []).push(n.id);
      });
    });
    var ord = {};
    D.boxes.forEach(function (b) { ord[b.id] = place[b.id].row; });
    D.nets.forEach(function (n, i) { ord[n.id] = i; });

    keys.forEach(function (lay) {
      layers[lay].sort(function (a, b) { return ord[a.id] - ord[b.id]; });
      layers[lay].forEach(function (n, i) { ord[n.id] = i; });
    });

    for (var pass = 0; pass < 4; pass++) {
      var seq = pass % 2 === 0 ? keys : keys.slice().reverse();
      seq.forEach(function (lay) {
        layers[lay].forEach(function (n) {
          if (place[n.id]) return;            // an instance stays put
          var peers = (adj[n.id] || []).filter(function (pid) {
            return Math.abs(layerOf(pid) - lay) === 1; });
          if (peers.length) {
            var sum = 0;
            peers.forEach(function (pid) { sum += ord[pid]; });
            ord[n.id] = sum / peers.length;
          }
        });
        layers[lay].sort(function (a, b) { return ord[a.id] - ord[b.id]; });
        layers[lay].forEach(function (n, i) { ord[n.id] = i; });
      });
    }
    return ord;
  }

  var layerCache = {};
  function layerOf(id) { return layerCache[id]; }

  function relayout() {
    var layers = {};
    layerCache = {};
    D.boxes.forEach(function (b) {
      if (hid[b.id]) return;
      var lay = place[b.id].col * 2;
      layerCache[b.id] = lay;
      (layers[lay] = layers[lay] || []).push(b);
    });
    D.nets.forEach(function (n) {
      var st = netState(n);
      if (st === 'hidden' || st === 'orphan') return;
      var shown = n.boxes.filter(function (id) { return !hid[id]; });
      var lay = shown.length
        ? Math.min.apply(null, shown.map(function (id) {
            return place[id].col * 2; })) + 1
        : 1;
      layerCache[n.id] = lay;
      (layers[lay] = layers[lay] || []).push(n);
    });

    cols.forEach(function (_, c) { layers[c * 2] = layers[c * 2] || []; });
    var keys = Object.keys(layers).map(Number).sort(function (a, b) {
      return a - b; });
    orderLayers(layers, keys);

    var x = G.margin, tallest = 0, heights = {};
    geo = {};
    keys.forEach(function (lay) {
      var wide = G.emptyW, h = 0;
      if (layers[lay].length) {
        wide = 0;
        layers[lay].forEach(function (n) {
          wide = Math.max(wide, n.w); h += n.h + G.rowGap; });
        h -= G.rowGap;
      }
      geo[lay] = { x: x, w: wide };
      heights[lay] = h;
      tallest = Math.max(tallest, h);
      x += wide + G.colGap;
    });

    keys.forEach(function (lay) {
      var y = G.margin + (tallest - heights[lay]) / 2 + G.top;
      layers[lay].forEach(function (n, idx) {
        n.nx = geo[lay].x + (geo[lay].w - n.w) / 2;
        n.ny = y;
        y += n.h + G.rowGap;
        if (place[n.id]) place[n.id].row = idx;
      });
    });

    D.boxes.concat(D.nets).forEach(function (n) {
      var el = document.getElementById(n.id);
      if (el) el.setAttribute('transform', 'translate(' +
        (n.nx - n.x).toFixed(2) + ' ' + (n.ny - n.y).toFixed(2) + ')');
    });
    applyHidden();
    redrawWires();
    drawHeads();
    if (typeof paintSel === 'function') paintSel();
  }

  function clip(cx, cy, tx, ty, n) {
    var dx = tx - cx, dy = ty - cy;
    if (!dx && !dy) return [cx, cy];
    var hw = n.w / 2 + 1, hh = n.h / 2 + 1;
    var k = Math.min(dx ? hw / Math.abs(dx) : 1e9, dy ? hh / Math.abs(dy) : 1e9);
    return [cx + dx * k, cy + dy * k];
  }

  function redrawWires() {
    [].forEach.call(scene.querySelectorAll('.wire-of'), function (w) {
      var a = byId[w.dataset.net], b = byId[w.dataset.box];
      if (!a || !b || a.nx === undefined || b.nx === undefined) return;
      var ax = a.nx + a.w / 2, ay = a.ny + a.h / 2;
      var bx = b.nx + b.w / 2, by = b.ny + b.h / 2;
      var p = clip(ax, ay, bx, by, a), q = clip(bx, by, ax, ay, b);
      w.setAttribute('d', 'M ' + p[0].toFixed(1) + ' ' + p[1].toFixed(1) +
                          ' L ' + q[0].toFixed(1) + ' ' + q[1].toFixed(1));
    });
  }

  function bounds() {
    var t = 1e9, b = -1e9;
    D.boxes.concat(D.nets).forEach(function (n) {
      t = Math.min(t, n.ny); b = Math.max(b, n.ny + n.h); });
    return { top: t === 1e9 ? G.top : t, bottom: b === -1e9 ? G.top + 100 : b };
  }

  function drawHeads() {
    heads.textContent = ''; zones.textContent = '';
    if (!editing) return;
    var bb = bounds();
    cols.forEach(function (name, c) {
      var g = geo[c * 2];
      if (!g) return;
      var t = document.createElementNS(SVGNS, 'text');
      t.setAttribute('class', 'col');
      t.setAttribute('x', g.x + g.w / 2);
      t.setAttribute('y', bb.top - 14);
      t.setAttribute('text-anchor', 'middle');
      t.textContent = (c === cur ? '▸ ' : '') + name;
      t.style.cursor = 'pointer';
      t.onclick = function (e) { e.stopPropagation(); select(c); };
      heads.appendChild(t);

      var r = document.createElementNS(SVGNS, 'rect');
      r.setAttribute('x', g.x - G.colGap / 2);
      r.setAttribute('y', bb.top - 26);
      r.setAttribute('width', g.w + G.colGap);
      r.setAttribute('height', bb.bottom - bb.top + 40);
      r.dataset.col = c;
      zones.appendChild(r);
    });
  }

  function select(c) {
    cur = c;
    drawHeads();
    dirty.textContent = (changed ? 'unsaved  ' : '') +
      (cur >= 0 ? '[' + cols[cur] + ']' : '');
  }

  function touch() {
    changed = true;
    select(cur);
  }

  // ---- hiding ----------------------------------------------------------
  // Hiding tidies the picture; it must never tidy away a finding silently.
  // Whether a net is one-sided was decided over the whole deck before the
  // page was written, so nothing here can re-judge it.  What this does
  // decide is whether a net still shows anything: with the instance that
  // drove it hidden, a_dq0 is a stub off the one box still drawn, and the
  // stub goes too.  A net that was already one-sided is not a stub - that
  // is the finding, and it stays.
  var hid = {}, hidNet = {}, showStubs = false;
  var hideinfo = document.getElementById('hideinfo');
  var panel = document.getElementById('panel');
  var panelBody = document.getElementById('panelbody');

  D.boxes.forEach(function (b) { if (b.hidden) hid[b.id] = 'file'; });
  D.nets.forEach(function (n) { if (n.hidden) hidNet[n.id] = 'file'; });

  function netState(n) {
    if (hidNet[n.id]) return 'hidden';
    var shown = n.boxes.filter(function (id) { return !hid[id]; });
    if (!shown.length) return 'orphan';          // nothing to hang it off
    var lostBox = shown.length < n.boxes.length;
    if (lostBox && shown.length < 2 && n.degree >= 2 && !showStubs) {
      return 'orphan';                           // a stub, see the toggle
    }
    return lostBox ? 'crossed' : 'on';
  }

  // the nets that hang off exactly one instance and are not findings: a
  // terminated pin says the same thing eight times over on a per-bit IO
  function leafNetsOf(boxIds) {
    var want = {};
    boxIds.forEach(function (id) { want[id] = true; });
    return D.nets.filter(function (n) {
      if (n.red) return false;                   // a finding stays, always
      var shown = n.boxes.filter(function (id) { return !hid[id]; });
      return shown.length === 1 && want[shown[0]];
    }).map(function (n) { return n.id; });
  }

  function applyHidden() {
    var lost = 0, lostRed = 0, crossing = 0;
    D.boxes.forEach(function (b) {
      var el = document.getElementById(b.id);
      if (el) el.classList.toggle('gone', !!hid[b.id]);
    });
    D.nets.forEach(function (n) {
      var el = document.getElementById(n.id);
      if (!el) return;
      var st = netState(n);
      el.classList.toggle('gone', st === 'hidden' || st === 'orphan');
      el.classList.toggle('crossed', st === 'crossed');
      if (st === 'orphan') {
        lost += 1;
        if (n.degree < 2) lostRed += 1;
      } else if (st === 'crossed') {
        crossing += 1;
      }
    });
    [].forEach.call(scene.querySelectorAll('.wire-of'), function (w) {
      var net = document.getElementById(w.getAttribute('data-net'));
      var gone = hid[w.getAttribute('data-box')] ||
                 (net && net.classList.contains('gone'));
      w.classList.toggle('gone', !!gone);
    });

    var nb = 0, nn = 0, nnRed = 0;
    D.boxes.forEach(function (b) { if (hid[b.id]) nb += 1; });
    D.nets.forEach(function (n) {
      if (!hidNet[n.id]) return;
      nn += 1;
      if (n.degree < 2) nnRed += 1;      // a finding, hidden on purpose
    });
    var bits = [];
    if (showStubs) bits.push('stubs shown');
    if (nb) bits.push(nb + ' instance(s) hidden');
    if (nn) {
      bits.push(nn + ' net(s) hidden' +
                (nnRed ? ' (' + nnRed + ' one-sided!)' : ''));
    }
    if (crossing) bits.push(crossing + ' net(s) dashed');
    if (lost) {
      bits.push(lost + ' net(s) dropped with them' +
                (lostRed ? ' (' + lostRed + ' one-sided!)' : ''));
    }
    hideinfo.textContent = bits.join(' · ');
    if (panel.classList.contains('on')) drawPanel();
  }

  function hideSelection() {
    var n = 0;
    selIds().forEach(function (id) { hid[id] = 'you'; n += 1; });
    selNetIds().forEach(function (id) { hidNet[id] = 'you'; n += 1; });
    if (!n) { alert('select what to hide first'); return; }
    clearSel();
    touch();
    relayout();
  }

  function showAll() {
    hid = {}; hidNet = {};
    touch();
    relayout();
  }

  // ---- the list of what is hidden, so any of it can come back ----------
  function drawPanel() {
    panelBody.textContent = '';

    function section(title, rows) {
      var h = document.createElement('h4');
      h.textContent = title + ' (' + rows.length + ')';
      panelBody.appendChild(h);
      if (!rows.length) {
        var none = document.createElement('div');
        none.className = 'none';
        none.textContent = 'nothing';
        panelBody.appendChild(none);
        return;
      }
      rows.forEach(function (r) {
        var row = document.createElement('div');
        row.className = 'row' + (r.restore ? '' : ' auto');
        var name = document.createElement('span');
        name.textContent = r.label;
        row.appendChild(name);
        if (r.why) {
          var why = document.createElement('span');
          why.className = 'why';
          why.textContent = r.why;
          row.appendChild(why);
        }
        if (r.restore) {
          row.title = 'click to put it back';
          row.onclick = r.restore;
        }
        panelBody.appendChild(row);
      });
    }

    section('instances', D.boxes.filter(function (b) { return hid[b.id]; })
      .map(function (b) {
        return { label: b.name, why: b.sub,
                 restore: function () { delete hid[b.id]; touch(); relayout(); } };
      }));

    section('nets', D.nets.filter(function (n) { return hidNet[n.id]; })
      .map(function (n) {
        return { label: n.label,
                 why: n.degree < 2 ? 'was one-sided!' : '',
                 restore: function () { delete hidNet[n.id]; touch(); relayout(); } };
      }));

    // Not hidden by anyone - these went because everything they touch did.
    // Listed because "where did that net go" is the first thing asked, and
    // a one-sided one leaving the picture is a finding leaving with it.
    section('dropped with them', D.nets.filter(function (n) {
      return !hidNet[n.id] && netState(n) === 'orphan';
    }).map(function (n) {
      var shown = n.boxes.filter(function (id) { return !hid[id]; });
      return { label: n.label,
               why: n.degree < 2 ? 'was one-sided!'
                    : (shown.length ? 'left with one end - see [stubs]'
                                    : 'nothing drawn on it') };
    }));
  }

  document.getElementById('leafnets').onclick = function () {
    var ids = selIds();
    if (!ids.length) { alert('select the instances first'); return; }
    var leaves = leafNetsOf(ids);
    if (!leaves.length) {
      alert('nothing hangs off just those - the red ones are findings and '
            + 'are never hidden this way');
      return;
    }
    var allHidden = leaves.every(function (id) { return hidNet[id]; });
    leaves.forEach(function (id) {
      if (allHidden) { delete hidNet[id]; } else { hidNet[id] = 'leaf'; }
    });
    touch();
    relayout();
  };

  document.getElementById('stubs').onclick = function () {
    showStubs = !showStubs;
    this.classList.toggle('on', showStubs);
    touch();
    relayout();
  };

  document.getElementById('hide').onclick = hideSelection;
  document.getElementById('showall').onclick = showAll;
  document.getElementById('panelall').onclick = showAll;
  document.getElementById('panelclose').onclick = function () {
    panel.classList.remove('on');
  };
  document.getElementById('hidden').onclick = function () {
    panel.classList.toggle('on');
    if (panel.classList.contains('on')) drawPanel();
  };

  // ---- selection -------------------------------------------------------
  // An IO called out bit by bit is 8 or 64 instances that belong in the same
  // column, and dragging them one at a time is not a feature.  The stem rule
  // is the one the nets already use: XIO_DQ0..7 share a stem, so one of them
  // can pick up the rest.
  var sel = {}, selNet = {};
  var band = document.getElementById('band');
  var selinfo = document.getElementById('selinfo');

  function stemOf(name) {
    var mo = /^(.*?)[<\[(]?(\d+)[>\])]?$/.exec(name);
    return (mo && mo[1]) ? mo[1].replace(/_+$/, '') : name;
  }

  function selIds() {
    return D.boxes.filter(function (b) { return sel[b.id]; })
                  .map(function (b) { return b.id; });
  }

  function selNetIds() {
    return D.nets.filter(function (n) { return selNet[n.id]; })
                 .map(function (n) { return n.id; });
  }

  function isNet(id) { return id.charAt(0) === 'n'; }

  // ---- the inspector ---------------------------------------------------
  var inspect = document.getElementById('inspect');
  var inspectHead = document.getElementById('inspecthead');
  var inspectBody = document.getElementById('inspectbody');

  function line(k, v) {
    var row = document.createElement('div');
    row.className = 'line';
    var a = document.createElement('span');
    a.className = 'k';
    a.textContent = k;
    var b = document.createElement('span');
    b.className = 'v';
    b.textContent = v;
    row.appendChild(a); row.appendChild(b);
    inspectBody.appendChild(row);
  }

  function why(text) {
    var d = document.createElement('div');
    d.className = 'why';
    d.textContent = text;
    inspectBody.appendChild(d);
  }

  function drawInspector() {
    var ids = selIds().concat(selNetIds());
    if (ids.length !== 1) { inspect.classList.remove('on'); return; }
    var n = byId[ids[0]];
    if (!n) { inspect.classList.remove('on'); return; }
    inspectHead.textContent = '';
    inspectBody.textContent = '';

    var title = document.createElement('span');
    title.textContent = isNet(ids[0]) ? n.label : n.name;
    inspectHead.appendChild(title);
    var sub = document.createElement('span');
    sub.className = 'sub';
    sub.textContent = isNet(ids[0]) ? '  net' : '  ' + n.sub;
    inspectHead.appendChild(sub);

    if (!isNet(ids[0])) {
      line('at', n.where || '');
      if (n.rails && n.rails.length) line('supplies', n.rails.join(', '));
      var on = D.nets.filter(function (m) {
        return m.boxes.indexOf(n.id) >= 0; });
      line('nets', on.length + ' (' + on.slice(0, 8).map(function (m) {
        return m.label; }).join(', ') + (on.length > 8 ? ', …' : '') + ')');
      inspect.classList.add('on');
      return;
    }

    if (n.nets && n.nets.length > 1) line('nets', n.nets.join(', '));
    var shown = n.boxes.filter(function (id) { return !hid[id]; });
    line('instances', n.boxes.map(function (id) {
      var b = byId[id];
      return (b ? b.name : id) + (hid[id] ? ' (hidden)' : '');
    }).join(', ') || 'none');
    if (n.loads && n.loads.length) {
      line('passives', n.loads.map(function (l) {
        return l.name + ' ' + l.value; }).join(', '));
    }
    line('elements', String(n.degree));

    // the question this panel exists for
    if (n.red) {
      why('one element touches this net in the whole deck - this is the '
          + 'one-sided finding, drawn red.');
    } else if (shown.length < 2) {
      var kinds = (n.loads || []).map(function (l) { return l.kind; });
      if (kinds.length) {
        why('not red because ' + n.loads.length + ' passive(s) ('
            + kinds.sort().join('') + ') sit on it as well as the instance, '
            + 'so the deck has ' + n.degree + ' elements on this net.');
      } else {
        why('only one instance is drawn on it; the rest of what touches it '
            + 'is hidden, so nothing here is a finding.');
      }
    }
    inspect.classList.add('on');
  }

  function paintSel() {
    D.boxes.forEach(function (b) {
      var el = document.getElementById(b.id);
      if (el) el.classList.toggle('sel', !!sel[b.id]);
    });
    D.nets.forEach(function (n) {
      var el = document.getElementById(n.id);
      if (el) el.classList.toggle('sel', !!selNet[n.id]);
    });
    var nb = selIds().length, nn = selNetIds().length;
    var bits = [];
    if (nb) bits.push(nb + ' instance(s)');
    if (nn) bits.push(nn + ' net(s)');
    selinfo.textContent = bits.length ? bits.join(' + ') + ' selected' : '';
    drawInspector();
  }

  function setSel(ids) {
    sel = {}; selNet = {};
    addSel(ids);
  }

  function addSel(ids) {
    ids.forEach(function (id) {
      if (isNet(id)) selNet[id] = true; else sel[id] = true;
    });
    paintSel();
  }

  function clearSel() { sel = {}; selNet = {}; paintSel(); }

  function siblingsOf(ids) {
    var stems = {}, wantNet = false, wantBox = false;
    ids.forEach(function (id) {
      var n = byId[id];
      if (!n) return;
      if (isNet(id)) { wantNet = true; stems[stemOf(n.label)] = 1; }
      else { wantBox = true; stems[stemOf(n.name)] = 1; }
    });
    var out = [];
    if (wantBox) {
      D.boxes.forEach(function (b) {
        if (stems[stemOf(b.name)]) out.push(b.id); });
    }
    if (wantNet) {
      D.nets.forEach(function (n) {
        if (stems[stemOf(n.label)]) out.push(n.id); });
    }
    return out;
  }

  window.__editorClick = function (node, e) {
    if (!editing) return false;
    var bag = isNet(node.id) ? selNet : sel;
    if (e.ctrlKey || e.metaKey || e.shiftKey) {
      if (bag[node.id]) { delete bag[node.id]; paintSel(); }
      else { addSel([node.id]); }
    } else if (e.detail >= 2) {
      setSel(siblingsOf([node.id]));
    } else {
      setSel([node.id]);
    }
    return true;
  };

  window.__editorBgClick = function () {
    if (!editing) return false;
    clearSel();
    return true;
  };

  document.getElementById('siblings').onclick = function () {
    var ids = selIds();
    if (!ids.length) { alert('select an instance first'); return; }
    setSel(siblingsOf(ids));
  };

  document.getElementById('selfound').onclick = function () {
    var q = (document.getElementById('find').value || '').trim().toLowerCase();
    if (!q) { alert('type something in the find box first'); return; }
    var hit = [];
    D.boxes.concat(D.nets).forEach(function (n) {
      var el = document.getElementById(n.id);
      var hay = (el && el.getAttribute('data-search')) || n.name || n.label;
      if (hay.toLowerCase().indexOf(q) >= 0) hit.push(n.id);
    });
    setSel(hit);
  };

  // ---- dragging the selection into a column ----------------------------
  var lift = null;

  function columnAt(x) {
    var best = 0, bestd = 1e9;
    cols.forEach(function (_, c) {
      var g = geo[c * 2];
      if (!g) return;
      var d = Math.abs(x - (g.x + g.w / 2));
      if (d < bestd) { bestd = d; best = c; }
    });
    return best;
  }

  function rowAt(col, y, moving) {
    var members = D.boxes.filter(function (b) {
      return place[b.id].col === col && !moving[b.id]; });
    members.sort(function (a, b) { return place[a.id].row - place[b.id].row; });
    for (var i = 0; i < members.length; i++) {
      if (y < members[i].ny + members[i].h / 2) return { index: i, members: members };
    }
    return { index: members.length, members: members };
  }

  window.__editorDown = function (e, node, pt) {
    if (!editing) return false;
    var at = window.__toScene(pt);

    if (!node) {
      if (!e.shiftKey) return false;          // plain background drag pans
      lift = { band: true, x0: at.x, y0: at.y };
      band.style.display = 'block';
      band.setAttribute('width', 0);
      band.setAttribute('height', 0);
      return true;
    }
    if (!node.classList.contains('box-node')) return false;
    // a modifier means the press is about the selection, not a drag: leave
    // it to the click handler, which knows add from replace
    if (e.ctrlKey || e.metaKey || e.shiftKey) return false;

    // pressing something already selected drags the whole selection;
    // pressing anything else starts a fresh one-instance selection
    if (!sel[node.id]) setSel([node.id]);
    var ids = selIds();
    var moving = {};
    ids.forEach(function (id) {
      moving[id] = true;
      var el = document.getElementById(id);
      if (el) el.classList.add('lift');
    });
    ids.sort(function (a, b) {
      var pa = place[a], pb = place[b];
      return pa.col - pb.col || pa.row - pb.row;
    });
    lift = { ids: ids, moving: moving, start: at };
    return true;
  };

  window.__editorMove = function (raw) {
    if (!lift) return false;
    var pt = window.__toScene(raw);

    if (lift.band) {
      band.setAttribute('x', Math.min(lift.x0, pt.x));
      band.setAttribute('y', Math.min(lift.y0, pt.y));
      band.setAttribute('width', Math.abs(pt.x - lift.x0));
      band.setAttribute('height', Math.abs(pt.y - lift.y0));
      return true;
    }

    var col = columnAt(pt.x);
    var spot = rowAt(col, pt.y, lift.moving);
    var g = geo[col * 2] || { x: G.margin, w: 130 };
    var y;
    if (spot.members.length === 0) { y = bounds().top; }
    else if (spot.index === 0) { y = spot.members[0].ny - G.rowGap / 2; }
    else { var m = spot.members[spot.index - 1]; y = m.ny + m.h + G.rowGap / 2; }
    dropline.setAttribute('x1', g.x); dropline.setAttribute('x2', g.x + g.w);
    dropline.setAttribute('y1', y); dropline.setAttribute('y2', y);
    dropline.style.display = 'block';
    [].forEach.call(zones.children, function (r) {
      r.classList.toggle('hot', Number(r.dataset.col) === col); });
    lift.drop = { col: col, index: spot.index, members: spot.members };
    return true;
  };

  window.__editorUp = function () {
    if (!lift) return false;

    if (lift.band) {
      var bx = +band.getAttribute('x'), by = +band.getAttribute('y');
      var bw = +band.getAttribute('width'), bh = +band.getAttribute('height');
      band.style.display = 'none';
      lift = null;
      if (bw < 3 && bh < 3) return false;     // a shift-click, not a sweep
      setSel(D.boxes.concat(D.nets).filter(function (n) {
        if (n.nx === undefined) return false;
        return n.nx < bx + bw && n.nx + n.w > bx &&
               n.ny < by + bh && n.ny + n.h > by;
      }).map(function (n) { return n.id; }));
      return true;
    }

    lift.ids.forEach(function (id) {
      var el = document.getElementById(id);
      if (el) el.classList.remove('lift');
    });
    dropline.style.display = 'none';
    [].forEach.call(zones.children, function (r) { r.classList.remove('hot'); });
    var moved = false;
    if (lift.drop) {
      var d = lift.drop;
      var rows = lift.ids.map(function (id) { return { id: id }; });
      d.members.splice.apply(d.members, [d.index, 0].concat(rows));
      d.members.forEach(function (m, i) {
        place[m.id].col = d.col; place[m.id].row = i; });
      touch();
      relayout();
      moved = true;
    }
    lift = null;
    return moved;
  };

  // ---- the toolbar -----------------------------------------------------
  document.getElementById('edit').onclick = function () {
    editing = !editing;
    this.classList.toggle('on', editing);
    document.body.classList.toggle('editing', editing);
    document.getElementById('editbar').classList.toggle('on', editing);
    if (editing && cur < 0) select(cols.length - 1); else select(cur);
    relayout();
  };

  document.getElementById('addcol').onclick = function () {
    var at = cur < 0 ? cols.length : cur + 1;
    cols.splice(at, 0, 'column ' + (cols.length + 1));
    D.boxes.forEach(function (b) {
      if (place[b.id].col >= at) place[b.id].col += 1; });
    cur = at; touch(); relayout(); window.__fit();
  };

  document.getElementById('delcol').onclick = function () {
    if (cur < 0) return;
    if (cols.length < 2) { alert('a deck needs at least one column'); return; }
    var here = D.boxes.filter(function (b) { return place[b.id].col === cur; });
    if (here.length) {
      // moving them somewhere arbitrary would lose an arrangement someone
      // made on purpose; better to say so than to guess
      alert('column "' + cols[cur] + '" still holds ' + here.length +
            ' instance(s). Drag them out first.');
      return;
    }
    cols.splice(cur, 1);
    D.boxes.forEach(function (b) {
      if (place[b.id].col > cur) place[b.id].col -= 1; });
    cur = Math.min(cur, cols.length - 1);
    touch(); relayout(); window.__fit();
  };

  document.getElementById('rencol').onclick = function () {
    if (cur < 0) return;
    var name = prompt('column name', cols[cur]);
    if (name === null) return;
    cols[cur] = name.trim() || cols[cur];
    touch(); relayout();
  };

  function layoutJSON() {
    var elements = {}, names = D.boxes.map(function (b) { return b.name; });
    names.sort();
    D.boxes.slice().sort(function (a, b) {
      return a.name < b.name ? -1 : 1; }).forEach(function (b) {
        elements[b.name] = { column: place[b.id].col,
                             row: Math.round(place[b.id].row) };
      });
    var hiddenNames = [], hiddenNets = [];
    D.boxes.forEach(function (b) { if (hid[b.id]) hiddenNames.push(b.name); });
    D.nets.forEach(function (n) { if (hidNet[n.id]) hiddenNets.push(n.label); });
    return JSON.stringify({
      version: 1,
      columns: cols.map(function (n) { return { name: n }; }),
      hidden: hiddenNames.sort(),
      hidden_nets: hiddenNets.sort(),
      elements: elements
    }, null, 2) + '\n';
  }

  document.getElementById('save').onclick = function () {
    var blob = new Blob([layoutJSON()], { type: 'application/json' });
    var a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = (D.name || 'deck') + '.layout.json';
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(function () { URL.revokeObjectURL(a.href); }, 1000);
    changed = false; select(cur);
  };

  document.getElementById('copy').onclick = function () {
    var text = layoutJSON();
    var done = function () { dirty.textContent = 'copied'; 
      setTimeout(function () { select(cur); }, 1200); };
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(done, function () { fallback(); });
    } else { fallback(); }
    function fallback() {
      // a file:// page often has no clipboard permission, and losing the
      // arrangement to a denied promise would be worse than a text box
      var ta = document.createElement('textarea');
      ta.value = text; ta.style.position = 'fixed'; ta.style.top = '40px';
      ta.style.left = '10px'; ta.style.width = '60%'; ta.style.height = '60%';
      document.body.appendChild(ta); ta.select();
      try { document.execCommand('copy'); done(); } catch (err) { /* shown */ }
      ta.onblur = function () { ta.remove(); };
    }
  };

  document.addEventListener('keydown', function (e) {
    if (editing && e.key === 'Escape' && e.target.id !== 'find') clearSel();
  });

  document.getElementById('revert').onclick = function () {
    hid = {}; hidNet = {};
    D.boxes.forEach(function (b) { if (b.hidden) hid[b.id] = 'file'; });
    D.nets.forEach(function (n) { if (n.hidden) hidNet[n.id] = 'file'; });
    cols = start.columns.slice();
    start.boxes.forEach(function (b) {
      place[b.id] = { col: b.col, row: b.row }; });
    changed = false; cur = Math.min(cur, cols.length - 1);
    select(cur); relayout(); window.__fit();
  };

  window.__relayout = relayout;
  relayout();
})();
"""


def render_html(g, title="deck connectivity", header=(), layout=None):
    """The same picture, in a viewer you can get around a big deck with."""
    svg = render_svg(g, title=title, header=header, embed_header=False,
                     layout=layout)
    notes = []
    for line in header:
        notes.append("<b>%s</b>" % _esc(line) if line.startswith("INCOMPLETE")
                     else _esc(line))
    import json
    st = viewer_state(g)
    st["name"] = os.path.splitext(title)[0] or "deck"
    # instances hidden before this page was drawn are not in the DOM, so the
    # export has to be told about them or saving would quietly bring them back
    st["hiddenNames"] = list(layout.hidden) if layout is not None else []
    state = json.dumps(st)
    return """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>%(title)s - connectivity</title>
<style>%(css)s</style>
</head>
<body>
<div id="bar">
  <h1>%(title)s</h1>
  <button id="out" title="zoom out (-)">-</button>
  <span id="zoom">100%%</span>
  <button id="in" title="zoom in (+)">+</button>
  <button id="fit" title="fit to window (0)">fit</button>
  <button id="one" title="actual size">1:1</button>
  <input id="find" type="search" placeholder="find net or instance  (/)">
  <button id="edit" title="arrange the columns by hand">edit layout</button>
  <span id="editbar">
    <button id="addcol">+ column</button>
    <button id="delcol">- column</button>
    <button id="rencol">rename</button>
    <button id="siblings" title="select every instance sharing this name stem: XIO_DQ0 picks up XIO_DQ1..7 (double-click a box does the same)">siblings</button>
    <button id="selfound" title="select every instance the find box matches">select found</button>
    <button id="hide" title="take the selected instances out of the picture (not out of the deck)">hide</button>
    <button id="leafnets" title="hide or show the nets that hang off one selected instance - the red ones stay whatever you do">leaf nets</button>
    <button id="stubs" title="show the nets that were dropped along with a hidden instance">stubs</button>
    <button id="hidden" title="list what is hidden and put back the ones you want">hidden\u2026</button>
    <button id="showall" title="bring everything hidden back">show all</button>
    <button id="save">save layout.json</button>
    <button id="copy">copy</button>
    <button id="revert">revert</button>
  </span>
</div>
<div id="status">
  <span id="notes">%(notes)s</span>
  <span id="dirty"></span>
  <span id="selinfo"></span>
  <span id="hideinfo"></span>
</div>
<div id="stage">
%(svg)s
<div id="panel">
  <div id="panelhead">
    <span>hidden</span>
    <button id="panelall">show all</button>
    <button id="panelclose">close</button>
  </div>
  <div id="panelbody"></div>
</div>
<div id="inspect"><div id="inspecthead"></div><div id="inspectbody"></div></div>
<div id="hint">wheel: zoom &middot; drag: pan &middot; click: trace a net
&middot; editing: shift+drag to box-select, double-click for siblings</div>
</div>
<script>window.DECK = %(state)s;</script>
<script>%(js)s</script>
<script>%(editjs)s</script>
</body>
</html>
""" % {"title": _esc(title), "css": VIEWER_CSS, "js": VIEWER_JS,
       "svg": svg, "notes": " &middot; ".join(notes), "state": state,
       "editjs": EDITOR_JS}
