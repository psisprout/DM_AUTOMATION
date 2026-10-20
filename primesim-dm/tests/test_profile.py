"""The DM element profile: what gets read, and what gets reported instead."""

import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from primesim_dm import check, deck


class Harness(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="dmprof")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def write(self, name, text):
        path = os.path.join(self.dir, name)
        parent = os.path.dirname(path)
        if parent and not os.path.isdir(parent):
            os.makedirs(parent)
        with open(path, "w") as fh:
            fh.write(text)
        return path

    def read(self, text, profile=deck.DEFAULT_PROFILE, name="d.sp"):
        path = self.write(name, text)
        return deck.read([path], search_dirs=[self.dir], profile=profile)

    def lint(self, text, profile=deck.DEFAULT_PROFILE, **kw):
        dk = self.read(text, profile=profile)
        c = check.Checker(dk, **kw)
        return dk, c, c.run()

    def codes(self, findings):
        return [f.code for f in findings]


class TestProfileTables(Harness):
    def test_dm_profile_is_the_default(self):
        self.assertEqual(deck.DEFAULT_PROFILE, "dm")
        self.assertEqual(self.read("R1 a b 1k\n").profile, "dm")

    def test_dm_reads_exactly_the_kinds_a_link_deck_uses(self):
        self.assertEqual(deck.profile_letters("dm"),
                         ["B", "C", "I", "K", "L", "R", "S", "V", "W", "X"])

    def test_full_is_a_superset(self):
        for letter in deck.profile_letters("dm"):
            self.assertIn(letter, deck.FULL_PROFILE)
        self.assertIn("M", deck.profile_letters("full"))

    def test_unknown_profile_name_is_refused(self):
        self.assertRaises(ValueError, deck.profile_table, "nope")


class TestWhatDmReads(Harness):
    def nodes_of(self, line, name, profile=deck.DEFAULT_PROFILE):
        dk = self.read(line + "\n", profile=profile)
        for el in dk.elements:
            if el.name.lower() == name.lower():
                return el.nodes
        raise AssertionError("no element %s (unparsed %s, off profile %s)"
                             % (name, dk.unparsed, dk.off_profile))

    def test_subckt_call(self):
        self.assertEqual(self.nodes_of("Xpkg a b c mempkg l=1", "Xpkg"),
                         ["a", "b", "c"])

    def test_sparam_channel(self):
        self.assertEqual(
            self.nodes_of("Sch in1 in2 out1 out2 ref mname=chan", "Sch"),
            ["in1", "in2", "out1", "out2", "ref"])

    def test_ibis_buffer(self):
        self.assertEqual(
            self.nodes_of("Bdq nd_pu nd_pd nd_out nd_in nd_en nd_o "
                          "buffer=output file='dq.ibs' model=DQ_OUT", "Bdq"),
            ["nd_pu", "nd_pd", "nd_out", "nd_in", "nd_en", "nd_o"])

    def test_w_line(self):
        self.assertEqual(
            self.nodes_of("Wrdl i1 i2 iref o1 o2 oref N=2 "
                          "RLGCfile='rdl.rlgc'", "Wrdl"),
            ["i1", "i2", "iref", "o1", "o2", "oref"])

    def test_passives_and_sources(self):
        for line, name in (("R1 a b 50", "R1"), ("C1 a 0 1f", "C1"),
                           ("L1 a b 1n", "L1"), ("V1 a 0 pulse 0 1", "V1"),
                           ("I1 a 0 1m", "I1")):
            self.assertEqual(len(self.nodes_of(line, name)), 2)

    def test_mutual_inductance_has_no_nodes(self):
        dk = self.read("L1 a b 1n\nL2 c d 1n\nK1 L1 L2 0.3\n")
        k = [e for e in dk.elements if e.name == "K1"][0]
        self.assertEqual(k.nodes, [])


class TestOffProfile(Harness):
    def test_transistor_is_reported_not_read(self):
        dk = self.read("M1 nd ng ns nb nch w=1u\nR1 a b 1k\n")
        self.assertEqual([e.name for e in dk.elements], ["R1"])
        self.assertEqual(len(dk.off_profile), 1)
        self.assertEqual(dk.off_profile[0][3], "M")

    def test_its_nodes_are_not_in_the_graph(self):
        dk = self.read("M1 nd ng ns nb nch\n")
        self.assertEqual(dk.nets(), [])

    def test_full_profile_reads_it(self):
        dk = self.read("M1 nd ng ns nb nch w=1u\n", profile="full")
        self.assertEqual([e.name for e in dk.elements], ["M1"])
        self.assertEqual(dk.off_profile, [])

    def test_a_warning_names_the_kinds_and_the_way_out(self):
        _dk, _c, findings = self.lint("D1 a b dio\nR1 a b 1k\n")
        hit = [f for f in findings if f.code == "off-profile-element"]
        self.assertTrue(hit)
        self.assertIn("diode", hit[0].message)
        self.assertIn("--profile full", hit[0].message)

    def test_the_lines_are_listed_so_they_can_be_checked(self):
        _dk, _c, findings = self.lint("D1 a b dio\n")
        notes = [f for f in findings
                 if f.code == "off-profile-element"
                 and f.severity == check.SEV_INFO]
        self.assertEqual(len(notes), 1)
        self.assertIn("D1", notes[0].message)

    def test_a_letter_no_profile_knows_is_unparsed_not_off_profile(self):
        # "I cannot find the nodes" and "this deck does not use that" are
        # different complaints and must not be merged
        for profile in ("dm", "full"):
            dk = self.read("Yweird a b c d\n", profile=profile)
            self.assertEqual(dk.off_profile, [], profile)
            self.assertEqual(len(dk.unparsed), 1, profile)

    def test_quiet_when_the_deck_is_all_dm_elements(self):
        _dk, _c, findings = self.lint("R1 a b 1k\nR2 b c 1k\n")
        self.assertNotIn("off-profile-element", self.codes(findings))

    def test_report_names_the_profile(self):
        dk, c, findings = self.lint("D1 a b dio\nR1 a b 1k\n")
        text = check.render(dk, findings, c.counts(), verbose=True, limit=0)
        self.assertIn("profile  : dm", text)
        self.assertIn("off prof : 1 line(s)", text)


class TestSubcktInternalsStayUnread(Harness):
    def test_bodies_are_not_top_level(self):
        dk = self.read(".subckt s a b\nR1 a b 1k\nC1 a 0 1f\n.ends\n"
                       "X1 p q s\n")
        self.assertEqual([e.name for e in dk.elements], ["X1"])

    def test_nested_bodies_too(self):
        dk = self.read(".subckt outer a b\n.subckt inner c d\n"
                       "R9 c d 1\n.ends\nXi a b inner\n.ends\n"
                       "X1 p q outer\n")
        self.assertEqual([e.name for e in dk.elements], ["X1"])

    def test_the_interface_is_still_read(self):
        dk = self.read(".subckt s a b\nR1 a b 1k\n.ends\nX1 p q s\n")
        self.assertEqual(dk.subckts["s"].ports, ["a", "b"])

    def test_an_off_profile_element_inside_a_subckt_is_not_reported(self):
        # the inside of a channel model is full of controlled sources; the
        # profile is about the top level only
        dk = self.read(".subckt bb a b\nE1 a b c d 1\nM1 a b c d nch\n"
                       ".ends\nX1 p q bb\n")
        self.assertEqual(dk.off_profile, [])
        self.assertEqual([e.name for e in dk.elements], ["X1"])


class TestModelFiles(Harness):
    def test_missing_ibis_file_is_an_error(self):
        _dk, _c, findings = self.lint(
            "Bdq a b c d e f buffer=output file='nope.ibs' model=DQ\n")
        hit = [f for f in findings if f.code == "missing-model-file"]
        self.assertEqual(len(hit), 1)
        self.assertIn("nope.ibs", hit[0].message)
        self.assertEqual(hit[0].severity, check.SEV_ERROR)

    def test_an_ibis_file_that_is_there_is_quiet(self):
        self.write("dq.ibs", "[IBIS Ver] 5.0\n")
        _dk, _c, findings = self.lint(
            "Bdq a b c d e f buffer=output file='dq.ibs' model=DQ\n")
        self.assertNotIn("missing-model-file", self.codes(findings))

    def test_touchstone_named_on_the_element(self):
        _dk, _c, findings = self.lint("S1 a b tstonefile='gone.s2p'\n")
        self.assertIn("missing-model-file", self.codes(findings))

    def test_touchstone_named_through_a_dot_model(self):
        _dk, _c, findings = self.lint(
            "S1 a b mname=chan\n.model chan S tstonefile='gone.s2p'\n")
        hit = [f for f in findings if f.code == "missing-model-file"]
        self.assertEqual(len(hit), 1)
        self.assertIn("gone.s2p", hit[0].message)

    def test_dot_model_is_collected(self):
        dk = self.read("S1 a b mname=chan\n"
                       ".model chan S tstonefile='ch.s4p'\n")
        self.assertEqual(dk.s_models["chan"].file(), "ch.s4p")

    def test_quoted_and_unquoted_params_read_the_same(self):
        dk = self.read("B1 a b c d e f file=dq.ibs model='DQ' buffer = output\n")
        el = dk.elements[0]
        self.assertEqual(el.model_file(), "dq.ibs")
        self.assertEqual(el.model_name(), "DQ")
        self.assertEqual(el.param("BUFFER"), "output")

    def test_a_subckt_call_names_no_model_file(self):
        dk = self.read("X1 a b mysub\n")
        self.assertIsNone(dk.elements[0].model_file())
        self.assertEqual(dk.elements[0].model_name(), "mysub")


class TestSparamPorts(Harness):
    def four_port(self, line):
        self.write("ch.s4p", "! touchstone\n")
        return self.lint(line + "\n")

    def test_node_count_matching_the_port_count_is_quiet(self):
        _dk, _c, findings = self.four_port(
            "S1 a b c d tstonefile='ch.s4p'")
        self.assertNotIn("sparam-ports", self.codes(findings))

    def test_a_reference_node_is_allowed(self):
        _dk, _c, findings = self.four_port(
            "S1 a b c d ref tstonefile='ch.s4p'")
        self.assertNotIn("sparam-ports", self.codes(findings))

    def test_too_few_nodes_is_an_error(self):
        _dk, _c, findings = self.four_port("S1 a b tstonefile='ch.s4p'")
        hit = [f for f in findings if f.code == "sparam-ports"]
        self.assertEqual(len(hit), 1)
        self.assertEqual(hit[0].severity, check.SEV_ERROR)
        self.assertIn("4-port", hit[0].message)

    def test_too_many_nodes_is_an_error(self):
        _dk, _c, findings = self.four_port(
            "S1 a b c d e f tstonefile='ch.s4p'")
        self.assertIn("sparam-ports", self.codes(findings))

    def test_it_works_through_a_dot_model_too(self):
        self.write("ch.s4p", "! touchstone\n")
        _dk, _c, findings = self.lint(
            "S1 a b mname=chan\n.model chan S tstonefile='ch.s4p'\n")
        self.assertIn("sparam-ports", self.codes(findings))

    def test_no_port_count_in_the_name_means_no_claim(self):
        self.write("ch.txt", "! touchstone\n")
        _dk, _c, findings = self.lint("S1 a b tstonefile='ch.txt'\n")
        self.assertNotIn("sparam-ports", self.codes(findings))


class TestIbisCompleteness(Harness):
    def test_a_complete_buffer_is_quiet(self):
        self.write("dq.ibs", "[IBIS Ver] 5.0\n")
        _dk, _c, findings = self.lint(
            "B1 a b c d e f buffer=output file='dq.ibs' model=DQ\n")
        self.assertNotIn("ibis-incomplete", self.codes(findings))

    def test_a_buffer_with_no_model_is_flagged(self):
        self.write("dq.ibs", "[IBIS Ver] 5.0\n")
        _dk, _c, findings = self.lint(
            "B1 a b c d e f buffer=output file='dq.ibs'\n")
        hit = [f for f in findings if f.code == "ibis-incomplete"]
        self.assertEqual(len(hit), 1)
        self.assertIn("model=", hit[0].message)

    def test_all_three_are_named_when_all_three_are_missing(self):
        _dk, _c, findings = self.lint("B1 a b c d e f xyz=1\n")
        hit = [f for f in findings if f.code == "ibis-incomplete"]
        self.assertEqual(len(hit), 1)
        for want in ("file=", "model=", "buffer="):
            self.assertIn(want, hit[0].message)


if __name__ == "__main__":
    unittest.main()
