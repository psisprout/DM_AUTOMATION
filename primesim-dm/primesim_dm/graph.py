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

    def __init__(self, label, nets, boxes, width):
        self.label = label
        self.nets = nets          # the real net names behind this node
        self.boxes = boxes        # Box objects it touches
        self.width = width        # how many nets collapsed into it
        self.layer = 0
        self.order = 0
        self.x = self.y = 0
        self.w = self.h = 0

    @property
    def floating(self):
        return len(self.boxes) < 2


class Graph(object):
    def __init__(self):
        self.boxes = []
        self.nets = []
        self.notes = []           # what the picture does not cover
        self.dropped = 0          # elements left out by --max-elements
        self.columns = []         # names of the element columns, left to right
        self.unplaced = 0         # boxes no layout file spoke for
        self.geom = {"x": {}, "width": {}}   # per-column x and width


def _rail_filter(patterns):
    rx = [re.compile(p, re.I) for p in patterns]
    return lambda net: any(r.search(net) for r in rx)


def build(deck, rails=DEFAULT_RAILS, group_buses=True, max_elements=80):
    """Turn a parsed :class:`deck.Deck` into a drawable graph."""
    g = Graph()
    is_rail = _rail_filter(rails) if rails else (lambda net: False)

    elements = list(deck.elements)
    if max_elements and len(elements) > max_elements:
        # keep the most-connected ones: a hub tells you more than a leaf
        degree = {}
        for el in elements:
            degree[id(el)] = len({n for n in el.nodes if not is_rail(n)})
        elements.sort(key=lambda e: -degree[id(e)])
        g.dropped = len(elements) - max_elements
        elements = elements[:max_elements]

    by_el = {}
    for el in elements:
        box = Box(el)
        by_el[id(el)] = box
        g.boxes.append(box)

    # net -> the boxes that touch it (rails go on the box instead)
    touch = {}
    for net, users in deck.net_users.items():
        boxes = []
        for el, _idx in users:
            box = by_el.get(id(el))
            if box is not None and box not in boxes:
                boxes.append(box)
        if not boxes:
            continue
        if is_rail(net):
            for box in boxes:
                if net not in box.rails:
                    box.rails.append(net)
            continue
        touch[net] = boxes

    # collapse buses: same stem, same endpoints, and more than one member
    groups = {}
    for net, boxes in touch.items():
        stem, idx = _stem(net) if group_buses else (net, None)
        key = (stem, idx is None, tuple(sorted(id(b) for b in boxes)))
        groups.setdefault(key, []).append((net, idx, boxes))

    for (stem, plain, _sig), members in groups.items():
        nets = [m[0] for m in members]
        boxes = members[0][2]
        if plain or len(members) == 1:
            label = nets[0] if len(nets) == 1 else "%s x%d" % (stem, len(nets))
        else:
            label = "%s[%s]" % (stem, _ranges(m[1] for m in members))
        g.nets.append(NetNode(label, sorted(nets), boxes, len(nets)))

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

    def __init__(self, columns=None, elements=None):
        self.columns = list(columns or [])      # column names, left to right
        self.elements = dict(elements or {})    # element name -> {column,row}

    def column_of(self, name):
        spec = self.elements.get(name)
        return None if spec is None else spec.get("column")

    def to_dict(self):
        return {"version": self.VERSION,
                "columns": [{"name": n} for n in self.columns],
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
    return Layout(names, elements)


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
    for box in g.boxes:
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

    for net in g.nets:
        net.layer = (min(b.layer for b in net.boxes) + 1) if net.boxes else 1


def _layer(g):
    """Bipartite layering: elements on even columns, nets on odd ones.

    Breadth-first from the busiest element, so the instance everything hangs
    off lands on the left and the picture reads outward from it.
    """
    adj = {}
    for net in g.nets:
        for box in net.boxes:
            adj.setdefault(id(box), []).append(net)
            adj.setdefault(id(net), []).append(box)

    seen = set()
    column = 0
    roots = sorted(g.boxes, key=lambda b: -len(adj.get(id(b), ())))
    order_seq = 0

    for root in roots:
        if id(root) in seen:
            continue
        root.layer = column
        root.order = order_seq
        order_seq += 1
        seen.add(id(root))
        frontier = [root]
        depth = column
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
        # isolated nets of the next component start past this one
        column = max([n.layer for n in g.boxes + g.nets if id(n) in seen] or
                     [column]) + 1
        if column % 2:
            column += 1

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
    for box in g.boxes:
        widest = max(len(box.name), len(box.sub_label),
                     len(", ".join(box.rails)) + 2 if box.rails else 0)
        box.w = max(112, widest * CHAR_W + BOX_PAD * 2)
        box.h = 54 if box.rails else 40
    for net in g.nets:
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

    # ids let the HTML viewer light up one net and everything on it
    bid = {id(b): "b%d" % i for i, b in enumerate(g.boxes)}
    nid = {id(n): "n%d" % i for i, n in enumerate(g.nets)}

    out.append('<g id="scene">')
    out.append('<g id="colheads"></g><g id="dropzones"></g>'
               '<line id="dropline" x1="0" y1="0" x2="0" y2="0"/>')

    if layout is not None and g.columns:
        for i, name in enumerate(g.columns):
            lay = i * 2
            if lay not in g.geom.get("x", {}):
                continue
            cx = g.geom["x"][lay] + g.geom["width"][lay] / 2.0
            out.append('<text class="col" x="%.1f" y="%.1f" '
                       'text-anchor="middle">%s</text>'
                       % (cx, top + 18, _esc(name)))

    # wires first, so the boxes sit on top of them
    for net in g.nets:
        cls = "floatwire" if net.floating else ("bus" if net.width > 1
                                                else "wire")
        for box in net.boxes:
            x1, y1, x2, y2 = _edge(net, box)
            out.append('<path class="%s wire-of" data-net="%s" data-box="%s" '
                       'd="M %.1f %.1f L %.1f %.1f"/>'
                       % (cls, nid[id(net)], bid[id(box)], x1, y1, x2, y2))

    for net in g.nets:
        label = net.label + ("  x%d" % net.width if net.width > 1 else "")
        cls, lcls = ("float", "floatl") if net.floating else ("net", "netl")
        out.append('<g class="node net-node" id="%s" data-search="%s">'
                   % (nid[id(net)], _esc(" ".join([net.label] + net.nets))))
        out.append('<rect class="%s" x="%.1f" y="%.1f" width="%.1f" '
                   'height="%.1f" rx="11"/>'
                   % (cls, net.x, net.y, net.w, net.h))
        out.append('<text class="%s" x="%.1f" y="%.1f" '
                   'text-anchor="middle">%s</text>'
                   % (lcls, net.x + net.w / 2.0, net.y + 15, _esc(label)))
        out.append("<title>%s</title>" % _esc(
            ", ".join(net.nets) + (" (touched by one port only)"
                                   if net.floating else "")))
        out.append("</g>")

    for box in g.boxes:
        out.append('<g class="node box-node" id="%s" data-name="%s" '
                   'data-search="%s">'
                   % (bid[id(box)], _esc(box.name),
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
    for i, box in enumerate(g.boxes):
        boxes.append({"id": "b%d" % i, "name": box.name,
                      "col": int(box.layer // 2), "row": float(box.order),
                      "x": round(box.x, 2), "y": round(box.y, 2),
                      "w": round(box.w, 2), "h": round(box.h, 2)})
    bid = {id(b): "b%d" % i for i, b in enumerate(g.boxes)}
    nets = []
    for j, net in enumerate(g.nets):
        nets.append({"id": "n%d" % j, "label": net.label,
                     "boxes": [bid[id(b)] for b in net.boxes],
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
  padding: 8px 12px; border-bottom: 1px solid #d4dbe4; background: #f6f8fb;
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
#dropzones rect { fill: transparent; }
#dropzones rect.hot { fill: rgba(70,130,200,0.13); }
#dropline { stroke: #2f6fb5; stroke-width: 3; display: none; }
@media (prefers-color-scheme: dark) {
  #edit.on { background: #24405e; border-color: #5b86bd; }
  #dirty { color: #e0a952; }
  #dropzones rect.hot { fill: rgba(120,170,230,0.16); }
  #dropline { stroke: #8fb4d9; }
}
@media (prefers-color-scheme: dark) {
  body { background: #12161c; color: #e8edf4; }
  #bar { background: #1a212a; border-bottom-color: #333e4c; }
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

  // Capture only once a drag is really under way.  Grabbing the pointer on
  // pointerdown retargets the click that follows to the <svg>, which would
  // make every click on a node read as a click on the background.
  var drag = null, dragged = false, editDrag = null, justArranged = false;
  svg.addEventListener('pointerdown', function (e) {
    var node = e.target.closest ? e.target.closest('.node') : null;
    if (node && window.__editorDown && window.__editorDown(e, node, at(e))) {
      e.preventDefault();
      svg.setPointerCapture(e.pointerId);
      editDrag = e.pointerId;
      return;                       // arranging, not panning
    }
    drag = { p: at(e), tx: tx, ty: ty, x: e.clientX, y: e.clientY, id: e.pointerId };
    dragged = false;
  });
  svg.addEventListener('pointermove', function (e) {
    if (editDrag !== null) { window.__editorMove(at(e)); return; }
    if (!drag) return;
    if (!dragged) {
      if (Math.abs(e.clientX - drag.x) + Math.abs(e.clientY - drag.y) < 4) return;
      dragged = true;
      try { svg.setPointerCapture(drag.id); } catch (err) { /* fine */ }
    }
    var p = at(e);
    tx = drag.tx + (p.x - drag.p.x) * k;
    ty = drag.ty + (p.y - drag.p.y) * k;
    apply();
  });
  function endDrag() {
    if (editDrag !== null) {
      try { svg.releasePointerCapture(editDrag); } catch (err) {}
      editDrag = null;
      window.__editorUp();
      justArranged = true;
      return;
    }
    if (drag && dragged) { try { svg.releasePointerCapture(drag.id); } catch (err) {} }
    drag = null;
  }
  svg.addEventListener('pointerup', endDrag);
  svg.addEventListener('pointercancel', endDrag);

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
      if (n.classList.contains('hot')) { clear(); } else { focus(n.id); }
    });
  });
  svg.addEventListener('click', function () {
    if (justArranged) { justArranged = false; return; }
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
  D.boxes.forEach(function (b) { place[b.id] = { col: b.col, row: b.row }; });
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
      var lay = place[b.id].col * 2;
      layerCache[b.id] = lay;
      (layers[lay] = layers[lay] || []).push(b);
    });
    D.nets.forEach(function (n) {
      var lay = 1;
      if (n.boxes.length) {
        lay = Math.min.apply(null, n.boxes.map(function (id) {
          return place[id].col * 2; })) + 1;
      }
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
    redrawWires();
    drawHeads();
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
      if (!a || !b) return;
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

  // ---- dragging an instance into a column ------------------------------
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

  function rowAt(col, y, movingId) {
    var members = D.boxes.filter(function (b) {
      return place[b.id].col === col && b.id !== movingId; });
    members.sort(function (a, b) { return place[a.id].row - place[b.id].row; });
    for (var i = 0; i < members.length; i++) {
      if (y < members[i].ny + members[i].h / 2) return { index: i, members: members };
    }
    return { index: members.length, members: members };
  }

  window.__editorDown = function (e, node, pt) {
    if (!editing || !node.classList.contains('box-node')) return false;
    lift = { id: node.id, node: node, start: window.__toScene(pt) };
    node.classList.add('lift');
    return true;
  };

  window.__editorMove = function (raw) {
    if (!lift) return false;
    var pt = window.__toScene(raw);
    var col = columnAt(pt.x);
    var spot = rowAt(col, pt.y, lift.id);
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
    lift.node.classList.remove('lift');
    dropline.style.display = 'none';
    [].forEach.call(zones.children, function (r) { r.classList.remove('hot'); });
    if (lift.drop) {
      var d = lift.drop;
      d.members.splice(d.index, 0, { id: lift.id });
      d.members.forEach(function (m, i) {
        place[m.id].col = d.col; place[m.id].row = i; });
      place[lift.id].col = d.col;
      touch();
      relayout();
    }
    lift = null;
    return true;
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
    return JSON.stringify({
      version: 1,
      columns: cols.map(function (n) { return { name: n }; }),
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

  document.getElementById('revert').onclick = function () {
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
    <button id="save">save layout.json</button>
    <button id="copy">copy</button>
    <button id="revert">revert</button>
    <span id="dirty"></span>
  </span>
  <span id="notes">%(notes)s</span>
</div>
<div id="stage">
%(svg)s
<div id="hint">wheel: zoom &middot; drag: pan &middot; click: trace a net</div>
</div>
<script>window.DECK = %(state)s;</script>
<script>%(js)s</script>
<script>%(editjs)s</script>
</body>
</html>
""" % {"title": _esc(title), "css": VIEWER_CSS, "js": VIEWER_JS,
       "svg": svg, "notes": " &middot; ".join(notes), "state": state,
       "editjs": EDITOR_JS}
