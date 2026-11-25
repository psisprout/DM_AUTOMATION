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
    """The dm_*.py scripts' contract: JSON last line, status ok or error."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.deck = os.path.join(self.dir, "top.sp")
        with open(self.deck, "w") as fh:
            fh.write(DECK)
        tools._CACHE.clear()

    def tearDown(self):
        shutil.rmtree(self.dir)

    def call(self, command, *argv):
        saved = sys.stdout, sys.stderr
        sys.stdout, sys.stderr = io.StringIO(), io.StringIO()
        try:
            script = [k for k, (cmd, _o) in tool_cli.SCRIPTS.items()
                      if cmd == command][0]
            code = tool_cli.main(script, list(argv) + ["--json"])
            out, err = sys.stdout.getvalue(), sys.stderr.getvalue()
        finally:
            sys.stdout, sys.stderr = saved
        self.assertIn("[primesim-dm]", err)            # progress goes to stderr
        lines = out.splitlines()
        self.assertTrue(lines[-1].startswith("{"))  # the JSON is the last line
        return code, json.loads(lines[-1])

    def test_question(self):
        code, r = self.call("si-fanout", "--deck", self.deck)
        self.assertEqual(code, 0)
        self.assertEqual(r["status"], "ok")
        self.assertIn("summary", r)
        self.assertEqual(r["too_many"], 1)
        where = r["nets"][0]["where"]
        self.assertTrue(os.path.isabs(where.rsplit(":", 1)[0]), where)

    def test_needs_user(self):
        code, r = self.call("errors", "--deck", "top.sp")
        self.assertEqual((code, r["status"]), (2, "error"))
        self.assertTrue(r["message"].startswith(tool_cli.NEED_USER))
        code, r = self.call("terminate", "--deck", self.deck)
        self.assertTrue(r["message"].startswith(tool_cli.NEED_USER))

    def test_output_only_with_overwrite(self):
        out = os.path.join(self.dir, "term.inc")
        code, r = self.call("terminate", "--deck", self.deck,
                            "--output", out, "--kind", "cload",
                            "--value2", "5f")
        self.assertEqual(code, 0)
        self.assertEqual(r["count"], 3)
        self.assertEqual(sorted(n["net"] for n in r["nets"]),
                         ["ball_a", "ball_b", "stub"])
        with open(out) as fh:
            self.assertIn("Cterm_1", fh.read())
        code, r = self.call("terminate", "--deck", self.deck,
                            "--output", out)
        self.assertEqual(code, 2)
        self.assertIn("이미 있습니다", r["message"])
        code, r = self.call("terminate", "--deck", self.deck,
                            "--output", out, "--overwrite")
        self.assertEqual(code, 0)
        with open(self.deck) as fh:
            self.assertEqual(fh.read(), DECK)        # the deck is untouched

    def test_graph_wants_html(self):
        code, r = self.call("graph", "--deck", self.deck,
                            "--output", os.path.join(self.dir, "g.svg"))
        self.assertEqual(code, 2)
        code, r = self.call("graph", "--deck", self.deck,
                            "--output", os.path.join(self.dir, "g.html"))
        self.assertEqual(code, 0)
        self.assertTrue(os.path.isfile(r["output"]))


    def test_paths_in_result_are_absolute(self):
        with open(self.deck, "a") as fh:
            fh.write(".include 'gone.inc'\nXD pad_a q nosuch\n")
        tools._CACHE.clear()
        code, r = self.call("errors", "--deck", self.deck)
        self.assertTrue(r["findings"])
        self.assertTrue(all(os.path.isabs(f["where"].rsplit(":", 1)[0])
                            for f in r["findings"] if f["where"]))
        self.assertEqual(r["missing_includes"],
                         [{"include": "gone.inc", "from": self.deck,
                           "line": 7}])
        self.assertEqual(sorted(tool_cli.SCRIPTS),
                         ["dm_check_errors", "dm_check_si_fanout",
                          "dm_make_termination", "dm_render_graph"])

    def test_each_script_takes_only_its_own_options(self):
        with self.assertRaises(SystemExit):
            tool_cli.build_parser("dm_check_errors").parse_args(
                ["--deck", self.deck, "--output", "/tmp/x"])
        args = tool_cli.build_parser("dm_make_termination").parse_args(
            ["--deck", self.deck, "--output", "/tmp/x", "--kind", "cload"])
        self.assertEqual((args.command, args.kind), ("terminate", "cload"))

    def test_timeout_still_answers(self):
        if not hasattr(tool_cli.signal, "SIGALRM"):
            self.skipTest("no SIGALRM here")
        real = tool_cli.run

        def slow(args):
            import time
            time.sleep(3)
            return real(args)
        tool_cli.run = slow
        try:
            code, r = self.call("errors", "--deck", self.deck,
                                "--timeout", "1")
        finally:
            tool_cli.run = real
        self.assertEqual(code, 2)
        self.assertIn("1초", r["message"])


if __name__ == "__main__":
    unittest.main()
