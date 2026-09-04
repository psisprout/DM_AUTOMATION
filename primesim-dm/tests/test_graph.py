import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from primesim_dm import deck as deck_mod       # noqa: E402
from primesim_dm import graph as graph_mod     # noqa: E402


def read_text(tmp, text, name="deck.sp"):
    path = os.path.join(tmp, name)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    return deck_mod.read([path])


class StemTest(unittest.TestCase):
    def test_bus_notations(self):
        for net, want in (("dq0", ("dq", 0)), ("dq<7>", ("dq", 7)),
                          ("dq[12]", ("dq", 12)), ("dq(3)", ("dq", 3)),
                          ("tx_data<5>", ("tx_data", 5))):
            self.assertEqual(graph_mod._stem(net), want, net)

    def test_no_index(self):
        self.assertEqual(graph_mod._stem("clk"), ("clk", None))
        self.assertEqual(graph_mod._stem("vref_dq"), ("vref_dq", None))

    def test_ranges_are_contiguous_where_they_can_be(self):
        self.assertEqual(graph_mod._ranges([0, 1, 2, 3]), "0:3")
        self.assertEqual(graph_mod._ranges([0, 1, 5]), "0:1,5")
        self.assertEqual(graph_mod._ranges([4]), "4")


class BuildTest(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_rails_become_box_stubs_not_nets(self):
        dk = read_text(self.tmp, """* t
XA vdd vss a0 sub
XB vdd vss a0 sub
""")
        g = graph_mod.build(dk)
        self.assertEqual([n.label for n in g.nets], ["a0"])
        for box in g.boxes:
            self.assertEqual(box.rails, ["vdd", "vss"])

    def test_no_rails_draws_supplies_as_nets(self):
        dk = read_text(self.tmp, """* t
XA vdd vss a0 sub
XB vdd vss a0 sub
""")
        g = graph_mod.build(dk, rails=())
        self.assertEqual(sorted(n.label for n in g.nets),
                         ["a0", "vdd", "vss"])

    def test_bus_collapses_when_endpoints_match(self):
        dk = read_text(self.tmp, """* t
XA d0 d1 d2 d3 sub
XB d0 d1 d2 d3 sub
""")
        g = graph_mod.build(dk)
        self.assertEqual([n.label for n in g.nets], ["d[0:3]"])
        self.assertEqual(g.nets[0].width, 4)
        self.assertEqual(g.nets[0].nets, ["d0", "d1", "d2", "d3"])

    def test_bus_stays_split_when_endpoints_differ(self):
        # d0/d1 go to XB, d2/d3 to XC.  Collapsing those into one bus would
        # hide exactly the kind of mis-wiring this picture is for.
        dk = read_text(self.tmp, """* t
XA d0 d1 d2 d3 sub
XB d0 d1 x x sub
XC d2 d3 y y sub
""")
        g = graph_mod.build(dk)
        labels = sorted(n.label for n in g.nets)
        self.assertIn("d[0:1]", labels)
        self.assertIn("d[2:3]", labels)

    def test_no_bus_groups_keeps_every_net(self):
        dk = read_text(self.tmp, """* t
XA d0 d1 d2 d3 sub
XB d0 d1 d2 d3 sub
""")
        g = graph_mod.build(dk, group_buses=False)
        self.assertEqual(sorted(n.label for n in g.nets),
                         ["d0", "d1", "d2", "d3"])

    def test_one_sided_net_is_flagged(self):
        dk = read_text(self.tmp, """* t
XA a0 spare sub
XB a0 b0 sub
""")
        g = graph_mod.build(dk)
        floating = sorted(n.label for n in g.nets if n.floating)
        self.assertEqual(floating, ["b0", "spare"])

    def test_max_elements_keeps_the_best_connected(self):
        body = ["* t", "XHUB n0 n1 n2 n3 n4 sub"]
        for i in range(5):
            body.append("XLEAF%d n%d z%d sub" % (i, i, i))
        dk = read_text(self.tmp, "\n".join(body) + "\n")
        g = graph_mod.build(dk, max_elements=3)
        self.assertEqual(len(g.boxes), 3)
        self.assertEqual(g.dropped, 3)
        self.assertEqual(g.boxes[0].name, "XHUB")

    def test_subckt_name_labels_the_box(self):
        dk = read_text(self.tmp, """* t
XIO1 a b hbm_tx_drv
R1 a 0 50
""")
        g = graph_mod.build(dk)
        labels = {b.name: b.sub_label for b in g.boxes}
        self.assertEqual(labels["XIO1"], "hbm_tx_drv")
        self.assertEqual(labels["R1"], "resistor")


class RenderTest(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def graph(self, text):
        return graph_mod.build(read_text(self.tmp, text))

    def test_svg_is_well_formed_and_self_contained(self):
        from xml.etree import ElementTree
        g = self.graph("* t\nXA vdd a0 sub\nXB vdd a0 sub\n")
        svg = graph_mod.render_svg(g, title="t.sp", header=["2 element(s)"])
        ElementTree.fromstring(svg)              # raises if malformed
        self.assertNotIn("http://", svg.replace(
            'xmlns="http://www.w3.org/2000/svg"', ""))

    def test_bus_brackets_are_escaped(self):
        # a net really is called dq<0>; unescaped it would break the XML
        from xml.etree import ElementTree
        g = self.graph("* t\nXA dq<0> dq<1> sub\nXB dq<0> dq<1> sub\n")
        svg = graph_mod.render_svg(g)
        self.assertIn("&lt;", svg + "&lt;")
        root = ElementTree.fromstring(svg)
        texts = [e.text for e in root.iter() if e.text]
        self.assertTrue(any("dq[0:1]" in t for t in texts), texts)

    def test_header_lines_reach_the_picture(self):
        g = self.graph("* t\nXA a0 sub\n")
        svg = graph_mod.render_svg(g, header=["INCOMPLETE: 1 include(s)"])
        self.assertIn("INCOMPLETE: 1 include(s)", svg)

    def test_floating_nets_get_the_warning_colour(self):
        g = self.graph("* t\nXA a0 spare sub\nXB a0 b sub\n")
        svg = graph_mod.render_svg(g)
        self.assertEqual(svg.count('class="float"'), 2)

    def test_empty_deck_still_renders(self):
        from xml.etree import ElementTree
        g = self.graph("* nothing here\n.end\n")
        ElementTree.fromstring(graph_mod.render_svg(g))

    def test_svg_size_is_a_positive_integer(self):
        g = self.graph("* t\nXA a0 sub\nXB a0 b0 sub\n")
        svg = graph_mod.render_svg(g)
        mo = re.search(r'width="(\d+)" height="(\d+)"', svg)
        self.assertTrue(mo)
        self.assertTrue(int(mo.group(1)) > 0 and int(mo.group(2)) > 0)

    def test_dot_names_every_box_and_net(self):
        g = self.graph("* t\nXA a0 sub\nXB a0 b0 sub\n")
        dot = graph_mod.render_dot(g)
        self.assertTrue(dot.startswith("graph deck {"))
        self.assertEqual(dot.count("[shape=box,"), len(g.boxes))
        self.assertEqual(dot.count("[shape=oval,"), len(g.nets))
        self.assertEqual(dot.count(" -- b"),
                         sum(len(n.boxes) for n in g.nets))


class LayoutTest(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_elements_and_nets_land_on_alternating_columns(self):
        dk = read_text(self.tmp, """* t
XTX txp sub
XCH txp rxp sub
XRX rxp sub
""")
        g = graph_mod.build(dk)
        graph_mod._measure(g)
        graph_mod._layer(g)
        for box in g.boxes:
            self.assertEqual(box.layer % 2, 0, box.name)
        for net in g.nets:
            self.assertEqual(net.layer % 2, 1, net.label)

    def test_nothing_overlaps_within_a_column(self):
        dk = read_text(self.tmp, """* t
XA a0 a1 a2 sub
XB a0 b0 sub
XC a1 c0 sub
XD a2 d0 sub
""")
        g = graph_mod.build(dk)
        graph_mod._measure(g)
        graph_mod._layer(g)
        layers = graph_mod._order(g)
        graph_mod._place(g, layers)
        for nodes in layers.values():
            spans = sorted((n.y, n.y + n.h) for n in nodes)
            for (_, end), (start, _) in zip(spans, spans[1:]):
                self.assertLessEqual(end, start + 0.01)


if __name__ == "__main__":
    unittest.main()
