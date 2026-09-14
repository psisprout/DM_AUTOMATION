import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from primesim_dm import deck as deck_mod       # noqa: E402
from primesim_dm import cli as cli_mod         # noqa: E402
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

    def test_separate_parts_stack_instead_of_marching_right(self):
        # an IO called out bit by bit is N identical unconnected parts; laid
        # out left to right they make a picture nothing lines up in
        body = ["* t"]
        for i in range(4):
            body.append("XIO_DQ%d  vdd pad%d txd%d sub" % (i, i, i))
            body.append("XPKG_DQ%d ball%d pad%d pkg" % (i, i, i))
        g = graph_mod.build(read_text(self.tmp, "\n".join(body) + "\n"))
        graph_mod._measure(g)
        graph_mod._layer(g)
        cols = {}
        for box in g.boxes:
            cols.setdefault(box.layer // 2, []).append(box.name)
        self.assertEqual(len(g.columns), 2, cols)
        self.assertEqual(sorted(cols[0]),
                         ["XIO_DQ0", "XIO_DQ1", "XIO_DQ2", "XIO_DQ3"])
        self.assertEqual(sorted(cols[1]),
                         ["XPKG_DQ0", "XPKG_DQ1", "XPKG_DQ2", "XPKG_DQ3"])

    def test_subckt_name_labels_the_box(self):
        dk = read_text(self.tmp, """* t
XIO1 a b hbm_tx_drv
R1 a 0 50
""")
        g = graph_mod.build(dk)
        labels = {b.name: b.sub_label for b in g.boxes}
        self.assertEqual(labels["XIO1"], "hbm_tx_drv")
        self.assertEqual(labels["R1"], "resistor")


class HideTest(unittest.TestCase):
    """Hiding tidies the picture.  It must not change what the picture says."""

    def setUp(self):
        import tempfile
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    DECK = """* t
XIO_DQ0  vdd pad0 spare0 dq_io
XPKG_DQ0 ball0 pad0 pkg
XIO_DQ1  vdd pad1 spare1 dq_io
XPKG_DQ1 ball1 pad1 pkg
"""

    def graph(self, **kw):
        return graph_mod.build(read_text(self.tmp, self.DECK), **kw)

    def test_a_net_left_with_one_end_goes_with_it(self):
        # pad0 joined XIO to XPKG.  Hide XPKG and pad0 shows no connection
        # any more - it is a stub hanging off the one instance still drawn,
        # so it goes too rather than crowding out what does say something
        self.assertIn("pad0", [n.label for n in self.graph().nets])
        self.assertNotIn("pad0", [n.label for n in
                                  self.graph(hide=["^XPKG_"]).nets])

    def test_hiding_never_marks_a_wired_net_one_sided(self):
        # whatever leaves the picture, nothing left in it may be re-judged:
        # a false alarm from a tool whose job is telling true ones from false
        # would be worse than the clutter
        before = sorted(n.label for n in self.graph().nets if n.floating)
        after = sorted(n.label for n in self.graph(hide=["^XPKG_"]).nets
                       if n.floating)
        self.assertEqual(before, ["ball0", "ball1", "spare0", "spare1"])
        self.assertEqual(after, ["spare0", "spare1"])

    def test_a_really_one_sided_net_stays_one_sided(self):
        g = self.graph(hide=["^XPKG_"])
        spare = [n for n in g.nets if n.label.startswith("spare")][0]
        self.assertTrue(spare.floating)
        self.assertFalse(spare.crosses)

    def test_findings_that_leave_the_picture_are_counted(self):
        # ball0/ball1 hang off XPKG alone, so hiding XPKG takes two real
        # one-sided nets off screen; that has to be said, not swallowed
        g = self.graph(hide=["^XPKG_"])
        self.assertEqual(g.hidden, 2)
        self.assertEqual(g.lost, 4)          # ball0, ball1, pad0, pad1
        self.assertEqual(g.lost_floating, 2)  # only ball0/ball1 were findings
        self.assertEqual([n.label for n in g.nets if n.label.startswith("ball")],
                         [])

    def test_hide_matches_the_subckt_too(self):
        g = self.graph(hide=["pkg"])
        self.assertEqual(sorted(b.name for b in g.boxes),
                         ["XIO_DQ0", "XIO_DQ1"])

    def test_only_keeps_just_what_matches(self):
        g = self.graph(only=["^XIO_"])
        self.assertEqual(sorted(b.name for b in g.boxes),
                         ["XIO_DQ0", "XIO_DQ1"])
        self.assertEqual(g.hidden, 2)

    def test_hide_names_are_literal_not_patterns(self):
        g = self.graph(hide_names=["XPKG_DQ0"])
        self.assertEqual(sorted(b.name for b in g.boxes),
                         ["XIO_DQ0", "XIO_DQ1", "XPKG_DQ1"])

    def test_hiding_does_not_regroup_the_buses(self):
        # grouping keys on the real endpoints, hidden ones included, so a
        # label never changes shape just because the picture was tidied
        full = set(n.label for n in self.graph().nets)
        for net in self.graph(hide=["^XPKG_"]).nets:
            self.assertIn(net.label, full)

    def test_a_net_that_still_connects_two_things_stays_and_says_so(self):
        # three instances on one net: hide one and the net still shows a
        # connection, so it stays - drawn dashed, never in the red of a
        # finding, because it is wired and merely goes somewhere off screen
        deck = read_text(self.tmp, """* t
XA shared sub
XB shared sub
XC shared sub
""", name="three.sp")
        g = graph_mod.build(deck, hide=["XC"])
        shared = [n for n in g.nets if n.label == "shared"][0]
        self.assertFalse(shared.floating)
        self.assertTrue(shared.crosses)
        self.assertEqual(shared.hidden_ends, 1)
        self.assertEqual(shared.degree, 3)
        svg = graph_mod.render_svg(g)
        self.assertIn('class="cross"', svg)
        self.assertIn("also reaches 1 instance(s) not drawn", svg)

    def test_the_layout_file_remembers_what_was_hidden(self):
        lay = graph_mod.load_layout(graph_mod.dump_layout(graph_mod.Layout(
            ["a"], {"XIO_DQ0": {"column": 0, "row": 0}},
            hidden=["XPKG_DQ0"])))
        self.assertEqual(lay.hidden, ["XPKG_DQ0"])
        g = self.graph(hide_names=lay.hidden)
        self.assertNotIn("XPKG_DQ0", [b.name for b in g.boxes])

    def test_nets_can_be_hidden_on_their_own(self):
        g = self.graph(hide_nets=["^spare"])
        self.assertNotIn("spare0", [n.label for n in g.nets])
        self.assertEqual(g.hidden_nets, 2)
        # both were findings, and hiding a finding has to be said out loud
        self.assertEqual(g.hidden_nets_floating, 2)

    def test_hiding_a_net_leaves_the_instances_alone(self):
        g = self.graph(hide_nets=["^pad"])
        self.assertEqual(len(g.boxes), 4)

    def test_hidden_net_names_are_literal(self):
        g = self.graph(hide_net_names=["spare0"])
        labels = [n.label for n in g.nets]
        self.assertNotIn("spare0", labels)
        self.assertIn("spare1", labels)

    def test_the_viewer_keeps_what_it_hid_so_it_can_hand_it_back(self):
        g = self.graph(hide=["^XPKG_"], keep_hidden=True)
        self.assertEqual(sorted(b.name for b in g.gone_boxes),
                         ["XPKG_DQ0", "XPKG_DQ1"])
        # the nets that went with them are kept too, so the panel can say
        # where they went and put them back
        self.assertEqual(sorted(n.label for n in g.gone_nets),
                         ["ball0", "ball1", "pad0", "pad1"])
        svg = graph_mod.render_svg(g)
        self.assertIn('data-name="XPKG_DQ0"', svg)
        self.assertIn("box-node gone", svg)

    def test_the_static_formats_really_drop_it(self):
        g = self.graph(hide=["^XPKG_"])
        self.assertEqual(g.gone_boxes, [])
        self.assertEqual(g.gone_nets, [])
        self.assertNotIn("XPKG_DQ0", graph_mod.render_svg(g))

    def test_the_layout_file_remembers_hidden_nets_too(self):
        text = graph_mod.dump_layout(graph_mod.Layout(
            ["a"], {"XIO_DQ0": {"column": 0, "row": 0}},
            hidden=["XPKG_DQ0"], hidden_nets=["spare0"]))
        back = graph_mod.load_layout(text)
        self.assertEqual(back.hidden, ["XPKG_DQ0"])
        self.assertEqual(back.hidden_nets, ["spare0"])
        self.assertEqual(graph_mod.dump_layout(back), text)

    def test_a_bad_hidden_list_is_refused(self):
        with self.assertRaises(graph_mod.LayoutError):
            graph_mod.load_layout('{"columns":["a"],"hidden":"XPKG"}')


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


class HtmlTest(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def graph(self, text="* t\nXA vdd a0 sub\nXB vdd a0 b0 sub\n"):
        return graph_mod.build(read_text(self.tmp, text))

    def test_background_rect_is_a_real_length(self):
        # it is a plain literal, not a format string: "100%%" would reach
        # the browser verbatim and the background would not paint
        svg = graph_mod.render_svg(self.graph())
        self.assertIn('class="bg" width="100%" height="100%"', svg)
        self.assertNotIn("100%%", svg)

    def test_scene_group_wraps_the_drawing(self):
        svg = graph_mod.render_svg(self.graph())
        self.assertEqual(svg.count('<g id="scene">'), 1)
        self.assertIn('id="deck-svg"', svg)

    def test_every_wire_points_at_nodes_that_exist(self):
        svg = graph_mod.render_svg(self.graph())
        ids = set(re.findall(r'<g class="node[^"]*" id="([^"]+)"', svg))
        pairs = re.findall(r'data-net="([^"]+)" data-box="([^"]+)"', svg)
        self.assertTrue(pairs)
        for net, box in pairs:
            self.assertIn(net, ids)
            self.assertIn(box, ids)

    def test_html_carries_the_picture_and_its_controls(self):
        html = graph_mod.render_html(self.graph(), title="t.sp",
                                     header=["2 element(s)"])
        self.assertIn("<svg", html)
        self.assertIn('id="scene"', html)
        for control in ('id="in"', 'id="out"', 'id="fit"', 'id="one"',
                        'id="find"', 'id="zoom"'):
            self.assertIn(control, html)
        self.assertIn("2 element(s)", html)

    def test_html_needs_no_network(self):
        # these run where there is no pip and no internet; a viewer that
        # fetches a library is a viewer that shows a blank page
        html = graph_mod.render_html(self.graph())
        # the SVG namespace URI is an identifier, never fetched; anything
        # else pointing outward would be
        stripped = html.replace("http://www.w3.org/2000/svg", "")
        self.assertNotIn("http://", stripped)
        self.assertNotIn("https://", stripped)
        self.assertNotIn("<script src", html)
        self.assertNotIn("<link", html)

    def test_html_does_not_repeat_the_header_inside_the_svg(self):
        html = graph_mod.render_html(self.graph(), title="t.sp",
                                     header=["2 element(s)"])
        self.assertEqual(html.count("2 element(s)"), 1)

    def test_incomplete_reads_are_called_out(self):
        html = graph_mod.render_html(self.graph(),
                                     header=["INCOMPLETE: 1 include(s)"])
        self.assertIn("<b>INCOMPLETE: 1 include(s)</b>", html)

    def test_dragging_avoids_the_fragile_browser_paths(self):
        # Both of these cost a round trip with a user on a browser that was
        # not to hand, so they are pinned here rather than rediscovered.
        html = graph_mod.render_html(self.graph())
        # capturing the pointer on an <svg> is not reliable across browsers;
        # window listeners cover a drag leaving its element with nothing
        # that can throw
        # the call, not the word: the comment above it explains why it is
        # not there and would otherwise trip this
        self.assertNotIn(".setPointerCapture(", html)
        self.assertNotIn(".releasePointerCapture(", html)
        # pointer events are off by default in Firefox before 59
        self.assertIn("window.PointerEvent", html)
        self.assertIn("'mousedown'", html)
        self.assertIn("'mousemove'", html)
        self.assertIn("'mouseup'", html)
        # and the labels must not be selected instead of dragged
        self.assertIn("-moz-user-select: none", html)

    def test_status_text_sits_outside_the_button_row(self):
        # writing "3 selected" among the buttons made the bar wrap, which
        # moved the canvas between a press and its release
        html = graph_mod.render_html(self.graph())
        bar = html[html.index('<div id="bar">'):html.index('<div id="stage">')]
        status = bar[bar.index('<div id="status">'):]
        for live in ('id="selinfo"', 'id="hideinfo"', 'id="dirty"',
                     'id="notes"'):
            self.assertIn(live, status)
        # and the feedback overlays must not take a press from a box
        self.assertIn("#dropzones, #dropline, #band { pointer-events: none; }",
                      html)

    def test_the_viewer_can_hide_instances(self):
        html = graph_mod.render_html(self.graph())
        self.assertIn('id="hide"', html)
        self.assertIn('id="showall"', html)
        self.assertIn("hiddenNames", html)

    def test_the_viewer_lists_what_is_hidden(self):
        html = graph_mod.render_html(self.graph())
        self.assertIn('id="panel"', html)
        self.assertIn('id="panelbody"', html)
        self.assertIn('id="hidden"', html)
        self.assertIn("dropped with them", html)
        # nets are selectable and hideable, not only instances
        self.assertIn("selNetIds", html)
        self.assertIn("hidden_nets", html)

    def test_the_viewer_can_select_more_than_one_instance(self):
        html = graph_mod.render_html(self.graph())
        for control in ('id="siblings"', 'id="selfound"', 'id="band"',
                        'id="selinfo"'):
            self.assertIn(control, html)
        # the stem rule instances are grouped by has to be the one the nets
        # already use, or XIO_DQ0 and dq0 would disagree about what a bus is
        self.assertIn("[<\\[(]?(\\d+)[>\\])]?$", html)
        self.assertIn("__editorClick", html)

    def test_format_follows_the_output_extension(self):
        self.assertEqual(cli_mod._graph_format("deck.html"), "html")
        self.assertEqual(cli_mod._graph_format("deck.HTM"), "html")
        self.assertEqual(cli_mod._graph_format("deck.dot"), "dot")
        self.assertEqual(cli_mod._graph_format("deck.gv"), "dot")
        self.assertEqual(cli_mod._graph_format("deck.svg"), "svg")
        self.assertEqual(cli_mod._graph_format(None), "svg")


class LayoutFileTest(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    DECK = """* t
XTX txp sub
XCH txp rxp sub
XRX rxp sub
"""

    def graph(self):
        return graph_mod.build(read_text(self.tmp, self.DECK))

    def test_round_trip_through_the_file(self):
        text = graph_mod.dump_layout(graph_mod.Layout(
            ["TX", "RX"], {"XTX": {"column": 0, "row": 0},
                           "XCH": {"column": 0, "row": 1},
                           "XRX": {"column": 1, "row": 0}}))
        back = graph_mod.load_layout(text)
        self.assertEqual(back.columns, ["TX", "RX"])
        self.assertEqual(back.column_of("XCH"), 0)
        self.assertEqual(graph_mod.dump_layout(back), text)

    def test_columns_come_from_the_file(self):
        lay = graph_mod.load_layout(graph_mod.dump_layout(graph_mod.Layout(
            ["left", "middle", "right"],
            {"XTX": {"column": 0, "row": 0},
             "XCH": {"column": 1, "row": 0},
             "XRX": {"column": 2, "row": 0}})))
        g = self.graph()
        graph_mod.render_svg(g, layout=lay)
        cols = {b.name: b.layer // 2 for b in g.boxes}
        self.assertEqual(cols, {"XTX": 0, "XCH": 1, "XRX": 2})
        self.assertEqual(g.columns, ["left", "middle", "right"])
        self.assertEqual(g.unplaced, 0)

    def test_a_hand_set_row_is_not_reshuffled(self):
        lay = graph_mod.load_layout(graph_mod.dump_layout(graph_mod.Layout(
            ["all"], {"XTX": {"column": 0, "row": 2},
                      "XCH": {"column": 0, "row": 1},
                      "XRX": {"column": 0, "row": 0}})))
        g = self.graph()
        graph_mod.render_svg(g, layout=lay)
        order = sorted(g.boxes, key=lambda b: b.y)
        self.assertEqual([b.name for b in order], ["XRX", "XCH", "XTX"])

    def test_an_instance_the_file_never_heard_of_gets_its_own_column(self):
        lay = graph_mod.load_layout(graph_mod.dump_layout(graph_mod.Layout(
            ["known"], {"XTX": {"column": 0, "row": 0}})))
        g = self.graph()
        graph_mod.render_svg(g, layout=lay)
        self.assertEqual(g.unplaced, 2)
        self.assertEqual(g.columns[-1], "unplaced")
        self.assertEqual({b.name for b in g.boxes if b.layer // 2 == 1},
                         {"XCH", "XRX"})

    def test_an_emptied_column_keeps_its_place(self):
        lay = graph_mod.load_layout(graph_mod.dump_layout(graph_mod.Layout(
            ["a", "gap", "b"], {"XTX": {"column": 0, "row": 0},
                                "XCH": {"column": 0, "row": 1},
                                "XRX": {"column": 2, "row": 0}})))
        g = self.graph()
        graph_mod.render_svg(g, layout=lay)
        # column 1 holds nothing, but the boxes right of it must not slide
        # left into the space, or the arrangement would drift every reload
        self.assertIn(2, g.geom["x"])
        self.assertEqual(g.geom["width"][2], graph_mod.EMPTY_COL_W)
        self.assertTrue(g.geom["x"][4] > g.geom["x"][2])

    def test_layout_of_reads_the_arrangement_back(self):
        g = self.graph()
        graph_mod.render_svg(g)
        lay = graph_mod.layout_of(g)
        self.assertEqual(len(lay.columns), len(g.columns))
        for box in g.boxes:
            self.assertEqual(lay.column_of(box.name), box.layer // 2)

    def test_bad_layouts_are_refused_not_ignored(self):
        # silently falling back would look like the hand arrangement was lost
        for text, why in (
                ("not json", "invalid JSON"),
                ('[]', "not an object"),
                ('{"columns": []}', "no columns"),
                ('{"columns":[{"name":"a"}],"elements":{"X":{"column":9}}}',
                 "column out of range"),
                ('{"columns":[{"name":"a"}],"elements":{"X":"nope"}}',
                 "element not an object"),
                ('{"columns":[{"name":"a"}],"elements":{"X":{"column":"z"}}}',
                 "non-numeric")):
            with self.assertRaises(graph_mod.LayoutError, msg=why):
                graph_mod.load_layout(text)

    def test_columns_may_be_plain_strings(self):
        lay = graph_mod.load_layout('{"columns": ["TX", "RX"]}')
        self.assertEqual(lay.columns, ["TX", "RX"])


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
