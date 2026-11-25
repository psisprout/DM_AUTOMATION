import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "mcp"))

import deploy  # noqa: E402


class TestFlatten(unittest.TestCase):
    def test_relative_imports(self):
        src = ("from . import deck as deck_mod\n"
               "from . import spice\n"
               "from .references import A, B\n"
               "import os\n")
        self.assertEqual(deploy.flatten(src),
                         "import primesim_dm_deck as deck_mod\n"
                         "import primesim_dm_spice as spice\n"
                         "from primesim_dm_references import A, B\n"
                         "import os\n")

    def test_unknown_form_refused(self):
        with self.assertRaises(SystemExit):
            deploy.flatten("from .. import x\n")


class TestDeployed(unittest.TestCase):
    """scripts/ gets files only, and a deployed script runs from there."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.scripts = os.path.join(self.dir, "scripts")
        os.mkdir(self.scripts)

    def tearDown(self):
        shutil.rmtree(self.dir)

    def test_flat_and_runnable(self):
        self.assertEqual(deploy.main([self.scripts]), 0)
        self.assertNotEqual(deploy.main([self.scripts]), 0)  # no overwrite
        self.assertEqual(deploy.main([self.scripts, "--overwrite"]), 0)
        deck = os.path.join(self.dir, "top.sp")
        with open(deck, "w") as fh:
            fh.write(".subckt p a b\n.ends\nXA n1 n2 p\nXB n1 n3 p\n")
        out = subprocess.run(
            [sys.executable,
             os.path.join(self.scripts, "dm_check_si_fanout.py"),
             "--deck", deck, "--json"],
            cwd=self.dir, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            universal_newlines=True)
        self.assertEqual(out.returncode, 0, out.stderr)
        r = json.loads(out.stdout.splitlines()[-1])
        self.assertEqual([n["net"] for n in r["nets"]], ["n2", "n3"])
        dirs = [d for d in os.listdir(self.scripts)
                if os.path.isdir(os.path.join(self.scripts, d))]
        self.assertEqual(dirs, [])


if __name__ == "__main__":
    unittest.main()
