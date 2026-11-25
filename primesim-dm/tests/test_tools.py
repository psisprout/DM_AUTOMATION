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
        self.assertTrue(os.path.isabs(by_net["pad_dq0"]["where"]
                                      .rsplit(":", 1)[0]))

    def test_limit_says_how_many_are_left(self):
        r = tools.check_si_fanout(self.deck, self.opts, limit=2)
        self.assertEqual(len(r["nets"]), 2)
        self.assertEqual(r["not_shown"], r["total"] - 2)

    def test_errors_leave_connectivity_out(self):
        deck = self.write("bad.sp", DECK + "XBAD a b nosuch\n"
                          "XIO_DQ0 x y z io\n")
        r = tools.check_errors(deck, self.opts)
        codes = [k["code"] for k in r["by_kind"]]
        self.assertIn("undefined-subckt", codes)
        self.assertIn("duplicate-name", codes)
        self.assertIn("port-count", codes)
        for code in tools.CONNECTIVITY_CODES + ("filter-unused",):
            self.assertNotIn(code, codes)
        self.assertEqual(r["errors"], sum(k["count"] for k in r["by_kind"]
                                          if k["severity"] == "error"))

    def test_errors_name_missing_includes(self):
        deck = self.write("broken.sp", ".include 'gone.inc'\n" + DECK)
        r = tools.check_errors(deck, self.opts)
        self.assertEqual(r["missing_includes"],
                         [{"include": "gone.inc", "from": deck, "line": 1}])
        self.assertIn("missing-include", [k["code"] for k in r["by_kind"]])

    def test_termination_lists_what_it_terminates(self):
        before = sorted(os.listdir(self.dir))
        r = tools.make_termination(self.deck, self.opts, kind="cload",
                                   value2="5f", exclude=["^txd"])
        self.assertEqual(sorted(os.listdir(self.dir)), before)
        self.assertIn("Cterm_1", r["text"])
        self.assertNotIn("txd0", r["text"])
        self.assertEqual(r["count"], 3)
        stub = [n for n in r["nets"] if n["net"] == "stub"][0]
        self.assertEqual((stub["element"], stub["ports"]), ("XSTUB", [1]))

    def test_termination_refuses_incomplete_deck(self):
        deck = self.write("broken.sp", ".include 'gone.inc'\n" + DECK)
        with self.assertRaises(tools.DeckError):
            tools.make_termination(deck, self.opts)

    def test_relative_path_refused(self):
        with self.assertRaises(tools.DeckError):
            tools.check_errors("top.sp", self.opts)


class TestWriting(Harness):
    def test_created_then_refused_then_modified(self):
        out = os.path.join(self.dir, "sub", "term.inc")
        text = tools.make_termination(self.deck, self.opts)["text"]
        self.assertEqual(tools.write_output(out, text), ([out], []))
        with self.assertRaises(tools.OutputExists) as cm:
            tools.write_output(out, "new")
        self.assertEqual(cm.exception.path, out)
        self.assertEqual(tools.write_output(out, "new", overwrite=True),
                         ([], [out]))
        with open(out) as fh:
            self.assertEqual(fh.read(), "new")


class TestCache(Harness):
    def test_reread_after_edit(self):
        tools.check_errors(self.deck, self.opts)
        first = tools.load(self.deck, self.opts)
        self.assertIs(tools.load(self.deck, self.opts), first)
        later = time.time() + 5
        with open(self.deck, "a") as fh:
            fh.write("XEXTRA pad_dq1 x pkg\n")
        os.utime(self.deck, (later, later))
        self.assertEqual(
            tools.check_errors(self.deck, self.opts)["elements"], 7)


if __name__ == "__main__":
    unittest.main()
