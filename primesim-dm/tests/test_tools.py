import os
import shutil
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from primesim_dm import tools

DECK = """\
.subckt io pad txd vdd vss
R1 pad vss 50
.ends
.subckt pkg die ball
R1 die ball 1
.ends
XIO_DQ0  pad_dq0 txd0 vdd 0 io
XPKG_DQ0 pad_dq0 ball_dq0 pkg
XSTUB    pad_dq0 stub pkg
XIO_DQ1  pad_dq1 txd1 vdd 0 io
XPKG_DQ1 pad_dq1 ball_dq1 pkg
V1 vdd 0 1.1
"""


class Harness(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.deck = self.write("top.sp", DECK)
        self.opts = tools.ReadOptions(keep_nets=["^vdd$"])
        tools._CACHE.clear()

    def tearDown(self):
        shutil.rmtree(self.dir)

    def write(self, name, text):
        p = os.path.join(self.dir, name)
        with open(p, "w") as fh:
            fh.write(text)
        return p


class TestQuestions(Harness):
    def test_summary_counts(self):
        s = tools.deck_summary(self.deck, self.opts)
        self.assertEqual(s["elements"], 6)
        self.assertEqual(s["missing_includes"], [])
        codes = [k["code"] for k in s["by_kind"]]
        self.assertIn("floating-net", codes)
        self.assertNotIn("si-node-fanout", codes)
        self.assertIn("si-node-fanout",
                      [k["code"] for k in tools.deck_summary(
                          self.deck, self.opts, si=True)["by_kind"]])

    def test_si_fanout_splits_few_and_many(self):
        r = tools.check_si_fanout(self.deck, self.opts)
        by_net = dict((n["net"], n) for n in r["nets"])
        self.assertEqual(by_net["pad_dq0"]["count"], 3)
        self.assertEqual(by_net["pad_dq0"]["elements"],
                         ["XIO_DQ0", "XPKG_DQ0", "XSTUB"])
        self.assertNotIn("pad_dq1", by_net)
        self.assertNotIn("vdd", by_net)         # kept out by the server
        self.assertEqual(r["too_many"], 1)
        self.assertEqual(r["too_few"], r["total"] - 1)

    def test_limit_says_how_many_are_left(self):
        r = tools.find_floating_nets(self.deck, self.opts, limit=2)
        self.assertEqual(len(r["nets"]), 2)
        self.assertEqual(r["not_shown"], r["total"] - 2)

    def test_net_info_ports_and_case(self):
        r = tools.net_info(self.deck, self.opts, "PAD_DQ0")
        self.assertEqual(r["net"], "pad_dq0")
        self.assertEqual(r["element_count"], 3)
        self.assertEqual(r["elements"][0]["subckt"], "io")
        self.assertEqual(r["elements"][0]["ports"], [0])

    def test_unknown_names_suggest(self):
        with self.assertRaises(tools.DeckError) as cm:
            tools.net_info(self.deck, self.opts, "dq0")
        self.assertIn("pad_dq0", str(cm.exception))
        with self.assertRaises(tools.DeckError):
            tools.instance_info(self.deck, self.opts, "XNOPE")

    def test_instance_info(self):
        r = tools.instance_info(self.deck, self.opts, "xstub")
        self.assertEqual(r["element"], "XSTUB")
        self.assertEqual([(p["net"], p["elements_on_net"])
                          for p in r["ports"]],
                         [("pad_dq0", 3), ("stub", 1)])

    def test_termination_is_text_only(self):
        before = sorted(os.listdir(self.dir))
        r = tools.make_termination(self.deck, self.opts, kind="cload",
                                   value2="5f", exclude=["^txd"])
        self.assertEqual(sorted(os.listdir(self.dir)), before)
        self.assertIn("Cterm_1", r["text"])
        self.assertNotIn("txd0", r["text"])
        self.assertEqual(r["count"], 3)

    def test_termination_refuses_incomplete_deck(self):
        deck = self.write("broken.sp", ".include 'gone.inc'\n" + DECK)
        with self.assertRaises(tools.DeckError):
            tools.make_termination(deck, self.opts)
        self.assertEqual(
            len(tools.find_floating_nets(deck, self.opts)["missing_includes"]),
            1)

    def test_relative_path_refused(self):
        with self.assertRaises(tools.DeckError):
            tools.deck_summary("top.sp", self.opts)


class TestWriting(Harness):
    def test_created_then_refused_then_modified(self):
        out = os.path.join(self.dir, "sub", "report.txt")
        r = tools.lint_report(self.deck, self.opts, si=True)
        self.assertGreater(r["errors"], 0)
        self.assertIn("si-node-fanout", r["text"])
        self.assertEqual(tools.write_output(out, r["text"]), ([out], []))
        with self.assertRaises(tools.OutputExists) as cm:
            tools.write_output(out, "new")
        self.assertEqual(cm.exception.path, out)
        self.assertEqual(tools.write_output(out, "new", overwrite=True),
                         ([], [out]))
        with open(out) as fh:
            self.assertEqual(fh.read(), "new")


class TestCache(Harness):
    def test_reread_after_edit(self):
        tools.deck_summary(self.deck, self.opts)
        first = tools.load(self.deck, self.opts)
        self.assertIs(tools.load(self.deck, self.opts), first)
        later = time.time() + 5
        with open(self.deck, "a") as fh:
            fh.write("XEXTRA pad_dq1 x pkg\n")
        os.utime(self.deck, (later, later))
        self.assertEqual(
            tools.deck_summary(self.deck, self.opts)["elements"], 7)


if __name__ == "__main__":
    unittest.main()
