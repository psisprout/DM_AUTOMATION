import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

import stage_decks  # noqa: E402
from primesim_dm import deck, stages  # noqa: E402


class Harness(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.dir)

    def analyze(self, name, **kw):
        path = os.path.join(self.dir, name + ".sp")
        with open(path, "w") as fh:
            fh.write(getattr(stage_decks, name)())
        return stages.analyze(deck.read([path]), **kw)


class TestStages(Harness):
    def test_split_block_is_one_stage(self):
        a = self.analyze("lpddr_split")
        self.assertEqual(a.stages, [["X_RDL_TX"], ["X_PKG_TX"],
                                    ["X_BRD_G0", "X_BRD_G1", "X_BRD_G2"],
                                    ["X_PKG_RX"], ["X_RDL_RX"]])
        self.assertEqual(a.direction, "sources")
        self.assertEqual([(g["piece"], g["lanes"], g["label"])
                          for g in a.groups],
                         [("X_BRD_G0", 8, "dq"), ("X_BRD_G1", 8, "dq"),
                          ("X_BRD_G2", 6, "ca")])

    def test_merged_channel(self):
        a = self.analyze("lpddr_merged")
        self.assertEqual(a.stages, [["X_CH"]])
        where = dict((r["example"], r["where"]) for r in a.lanes)
        self.assertEqual(where["V_dq0"], "before 1")
        self.assertEqual(where["XRX_dq0"], "after 1")

    def test_one_lane_wired_differently(self):
        a = self.analyze("lpddr_odd_lane")
        odd = [r for r in a.lanes if r["odd"]]
        self.assertEqual(len(odd), 1)
        self.assertEqual(odd[0]["example"], "R_dq3")

    def test_fan_out_at_the_end_is_stacked(self):
        a = self.analyze("nand_1p4p")
        self.assertEqual(a.stages[-1],
                         ["X_DIE0", "X_DIE1", "X_DIE2", "X_DIE3"])
        self.assertEqual(a.branches, {})

    def test_dies_off_a_flyby_are_branches(self):
        a = self.analyze("flyby_4rank")
        self.assertEqual([s[0] for s in a.stages],
                         ["X_PKG", "X_SEG0", "X_RANK0", "X_SEG1", "X_RANK1",
                          "X_SEG2", "X_RANK2", "X_SEG3", "X_RANK3"])
        self.assertEqual(a.branches, {"X_RANK%d" % r: ["X_DIE_R%d" % r]
                                      for r in range(4)})
        # the VTT the terminators share does not join the lanes into one
        self.assertNotIn("R x6", [r["shape"] for r in a.lanes])

    def test_wide_interposer(self):
        a = self.analyze("hbm")
        self.assertEqual(a.stages, [["X_INTERPOSER"], ["X_BASE"]])
        self.assertEqual(a.lanes[0]["count"], 64)

    def test_no_source_falls_back_and_tx_overrides(self):
        a = self.analyze("no_sources")
        self.assertEqual(a.direction, "deck order")
        a = self.analyze("no_sources", tx=["^XTX_"])
        self.assertEqual(a.direction, "--tx")
        where = dict((r["example"], r["where"]) for r in a.lanes)
        self.assertEqual(where["XTX_dq0"], "before 1")

    def test_hub_overrides(self):
        a = self.analyze("flyby_4rank", not_hub=["dram_die"])
        self.assertNotIn("X_DIE_R0", a.hubs)
        a = self.analyze("lpddr_merged", hub=["^XRX_dq0$"])
        self.assertIn("XRX_dq0", a.hubs)

    def test_render_names_everything(self):
        text = stages.render(self.analyze("flyby_4rank"))
        self.assertIn("(+ branch X_DIE_R0)", text)
        text = stages.render(self.analyze("lpddr_odd_lane"))
        self.assertIn("3 pieces, stacked", text)
        self.assertIn("differs from the others", text)


if __name__ == "__main__":
    unittest.main()
