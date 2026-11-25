import io
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from primesim_dm import tool_cli, tools

DECK = """\
.subckt pkg die ball
R1 die ball 1
.ends
XA pad_a ball_a pkg
XB pad_a ball_b pkg
XC pad_a stub pkg
"""


class TestScript(unittest.TestCase):
    """dm_deck.py's contract: one JSON object out, status ok or error."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.deck = os.path.join(self.dir, "top.sp")
        with open(self.deck, "w") as fh:
            fh.write(DECK)
        tools._CACHE.clear()

    def tearDown(self):
        shutil.rmtree(self.dir)

    def call(self, *argv):
        saved, sys.stdout = sys.stdout, io.StringIO()
        try:
            code = tool_cli.main(list(argv))
            out = sys.stdout.getvalue()
        finally:
            sys.stdout = saved
        return code, json.loads(out)

    def test_question(self):
        code, r = self.call("si-fanout", "--deck", self.deck, "--json")
        self.assertEqual(code, 0)
        self.assertEqual(r["status"], "ok")
        self.assertIn("summary", r)
        self.assertEqual(r["too_many"], 1)

    def test_needs_user(self):
        code, r = self.call("summary", "--deck", "top.sp")
        self.assertEqual((code, r["status"]), (1, "error"))
        self.assertTrue(r["message"].startswith(tool_cli.NEED_USER))
        code, r = self.call("net", "--deck", self.deck)
        self.assertTrue(r["message"].startswith(tool_cli.NEED_USER))

    def test_output_only_with_overwrite(self):
        out = os.path.join(self.dir, "term.inc")
        code, r = self.call("terminate", "--deck", self.deck,
                            "--output", out, "--kind", "cload",
                            "--value2", "5f")
        self.assertEqual(code, 0)
        self.assertEqual(r["count"], 3)
        self.assertTrue(r["preview"][0].startswith("Cterm_1"))
        code, r = self.call("terminate", "--deck", self.deck,
                            "--output", out)
        self.assertEqual(code, 1)
        self.assertIn("이미 있습니다", r["message"])
        code, r = self.call("terminate", "--deck", self.deck,
                            "--output", out, "--overwrite")
        self.assertEqual(code, 0)
        with open(self.deck) as fh:
            self.assertEqual(fh.read(), DECK)        # the deck is untouched

    def test_graph_wants_html(self):
        code, r = self.call("graph", "--deck", self.deck,
                            "--output", os.path.join(self.dir, "g.svg"))
        self.assertEqual(code, 1)
        code, r = self.call("graph", "--deck", self.deck,
                            "--output", os.path.join(self.dir, "g.html"))
        self.assertEqual(code, 0)
        self.assertTrue(os.path.isfile(r["output"]))


if __name__ == "__main__":
    unittest.main()
