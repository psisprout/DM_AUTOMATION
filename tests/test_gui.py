"""GUI smoke tests - drive the real widgets on Qt's offscreen platform."""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from tests import make_fixtures as fx  # noqa: E402

try:
    from chanxt.gui.qtcompat import QtCore, QtWidgets  # noqa: E402
    from chanxt.gui.app import MainWindow  # noqa: E402

    QT_AVAILABLE = True
except ImportError as exc:  # pragma: no cover - depends on the environment
    QT_AVAILABLE = False
    QT_REASON = str(exc)

from chanxt.analysis import Key  # noqa: E402
from chanxt.project import TermRule  # noqa: E402

DATA = fx.DATA
REV_A = os.path.join(DATA, "lp5_revA.s41p")
REV_B = os.path.join(DATA, "lp5_revB.s41p")
NONAME = os.path.join(DATA, "lp5_noname.s41p")


@unittest.skipUnless(QT_AVAILABLE, "no Qt binding installed")
class GuiTests(unittest.TestCase):
    app = None

    @classmethod
    def setUpClass(cls):
        if not all(os.path.exists(p) for p in (REV_A, REV_B, NONAME)):
            fx.main()
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.w = MainWindow()
        self.warnings = []
        self.w._warn = lambda t, m: self.warnings.append((t, m))  # no modal dialogs
        self.addCleanup(self.w.deleteLater)

    def _lp5x(self, w=None):
        w = w or self.w
        orig_item, orig_double = QtWidgets.QInputDialog.getItem, QtWidgets.QInputDialog.getDouble
        answers = iter(["SoC", "DRAM"])
        QtWidgets.QInputDialog.getItem = staticmethod(lambda *a, **k: (next(answers), True))
        QtWidgets.QInputDialog.getDouble = staticmethod(lambda *a, **k: (8.0, True))
        try:
            w.on_lp5x()
        finally:
            QtWidgets.QInputDialog.getItem = orig_item
            QtWidgets.QInputDialog.getDouble = orig_double

    def _loaded(self):
        self.w.add_model_files([REV_A, REV_B])
        self._lp5x()
        self.assertTrue(self.w.compute())
        return self.w

    def test_models_are_mapped_from_port_names(self):
        w = self.w
        w.add_model_files([REV_A])
        self.assertEqual(self.warnings, [])
        self.assertEqual(w.table_ports.rowCount(), 41)
        self.assertEqual(w.table_ports.item(0, 2).text(), "DQ0")
        self.assertEqual(w.table_ports.item(0, 3).text(), "SoC")
        self.assertEqual(w.table_ports.item(0, 4).text(), "DQ")
        self.assertIn("NET_LOC", w.combo_name_rule.itemText(0))
        vddq = [r for r in range(41) if w.table_ports.item(r, 2).text() == "VDDQ"]
        self.assertIn("Short", w.table_ports.item(vddq[0], 6).text())
        self.assertEqual(w.project.locations, ["SoC", "DRAM"])

    def test_unnamed_ports_by_position(self):
        w = self.w
        w.add_model_files([NONAME])
        spec = w.project.models[0]
        self.assertEqual(spec.ports[0].net, "P1")
        w.edit_loc_a.setText("CTRL")
        w.edit_loc_b.setText("MEM")
        w.on_set_locations()
        self.assertEqual(spec.ports[0].loc, "CTRL")
        self.assertEqual(spec.ports[-1].loc, "MEM")
        # editing a cell writes through to the project
        w.table_ports.item(0, 2).setText("DQ0")
        self.assertEqual(spec.ports[0].net, "DQ0")
        self.assertEqual(w.table_ports.item(0, 4).text(), "DQ")

    def test_full_flow_select_matrix_breakdown_output(self):
        w = self._loaded()
        self.assertEqual(self.warnings, [])
        self.assertEqual(w.list_scen.count(), 2)
        self.assertIn("Computed", w.lbl_compute.text())
        w.apply_filter()
        n0 = len(w.selected)
        self.assertGreater(n0, 0)
        self.assertFalse(any(k.kind in ("NEXT", "FEXT", "XT") for k in w.selected))

        # facets: add per-aggressor NEXT with worst-2, victims DQ3 only
        w.f_kinds.set_checked(["NEXT"])
        w.edit_vpat.setText("DQ3")
        w.spin_k.setValue(2)
        w.apply_filter()
        self.assertEqual({(k.kind, k.obs) for k in w.selected}, {("NEXT", "SoC"), ("NEXT", "DRAM")})
        self.assertEqual(len(w.selected), 4)
        self.assertEqual(w.table_preview.rowCount(), 4)

        # untick a preview row
        item = w.table_preview.item(0, 0)
        key = Key(*item.data(QtCore.Qt.UserRole))
        item.setCheckState(QtCore.Qt.Unchecked)
        self.assertNotIn(key, w.selected)
        w.apply_filter()
        self.assertNotIn(key, w.selected)

        # matrix: tick a cell, toggle a row
        w.sel_tabs.setCurrentIndex(1)
        w.mx_model.setCurrentIndex(0)
        w.draw_matrix()
        mx = w._matrix
        r, c = mx.victims.index("DQ0"), mx.aggressors.index("DQ7")
        w.table_mx.item(r, c).setCheckState(QtCore.Qt.Checked)
        self.assertIn(mx.keys[r][c], w.selected)
        before = len(w.selected)
        w._mx_toggle(row=mx.victims.index("DQ5"))
        self.assertGreater(len(w.selected), before)
        w._mx_toggle(row=mx.victims.index("DQ5"))
        self.assertEqual(len(w.selected), before)

        # breakdown for WRITE / DQ3
        w.sel_tabs.setCurrentIndex(2)
        w.bd_scen.setCurrentIndex(w.bd_scen.findText("WRITE"))
        w.bd_victim.setCurrentIndex(w.bd_victim.findText("DQ3"))
        self.assertGreater(w.table_bd.rowCount(), 10)
        self.assertIn("WRITE PSXT@DRAM | DQ3", w.lbl_bd.text())
        w.bd_k.setValue(2)
        w._bd_add_topk()
        self.assertIn(Key("WRITE", "PSXT", "DRAM", "DQ3"), w.selected)
        self.assertIn(Key("WRITE", "XT", "DRAM", "DQ3", "DQ2", "SoC"), w.selected)
        w.bd_group.setChecked(True)
        self.assertEqual(w.table_bd.item(0, 1).text(), "DQ")

        # output
        w.edit_freqs.setText("1, 2 GHz")
        w.on_extract()
        self.assertEqual(w.table_vals.rowCount(), 2 * len(w.selected))
        html = os.path.join(self.tmp.name, "r.html")
        w.edit_html.setText(html)
        w.check_open.setChecked(False)
        w.split_checks["model"].setChecked(True)
        w.on_save_html()
        self.assertEqual(self.warnings, [])
        with open(html, encoding="utf-8") as fh:
            page = fh.read()
        data = json.loads(re.search(r'<script type="application/json" id="data">(.*?)</script>',
                                    page, re.S).group(1))
        self.assertEqual([m["alias"] for m in data["models"]], ["lp5_revA", "lp5_revB"])
        self.assertIn("NEXT · lp5_revA", [p["title"] for p in data["panels"]])

        # save, reopen: the selection comes back
        proj = os.path.join(self.tmp.name, "p.json")
        w._save(proj)
        w2 = MainWindow()
        w2._warn = lambda t, m: self.warnings.append((t, m))
        self.addCleanup(w2.deleteLater)
        w2.open_project(proj)
        self.assertEqual(self.warnings, [])
        self.assertTrue(w2.compute())
        self.assertEqual(sorted(w2.selected), sorted(w.selected))
        self.assertTrue(w2.split_checks["model"].isChecked())
        self.assertEqual(w2.edit_freqs.text(), "1, 2 GHz")

    def test_presets(self):
        w = self._loaded()
        w.f_kinds.set_checked(["IL"])
        w.apply_filter()
        il = list(w.selected)
        w._ask_text = lambda *a, **k: "only IL"
        w.on_preset_save()
        w.f_kinds.set_checked(["RL"])
        w.apply_filter()
        self.assertNotEqual(w.selected, il)
        w.combo_preset.setCurrentIndex(w.combo_preset.findText("only IL"))
        w.on_preset_load()
        self.assertEqual(w.selected, il)

    def test_terminations_and_scenarios_edit_the_project(self):
        w = self._loaded()
        w.tabs.setCurrentIndex(2)
        t = w.terms_global
        n = t.table.rowCount()
        t.add_row(TermRule("net", "DQ7", "DRAM", "r", r=40.0))
        t.changed.emit()
        self.assertEqual(len(w.project.terminations), n + 1)
        self.assertEqual(w.project.resolve_term("DQ7", "DRAM").type, "r")
        self.assertTrue(w.stale)
        # a C row with no value is not accepted and is marked
        t.add_row(TermRule("net", "DQ6", "*", "c"))
        t.changed.emit()
        self.assertEqual(len(w.project.terminations), n + 1)

        w.tabs.setCurrentIndex(3)
        w._ask_text = lambda *a, **k: "TX"
        w.on_scen_add()
        self.assertIsNotNone(w.project.scenario("TX"))
        self.assertTrue(w.project.scenario("TX").directions)
        w.list_scen.setCurrentRow(w.list_scen.count() - 1)
        w.dirs.table.item(0, 4).setCheckState(QtCore.Qt.Checked)  # first group quiet
        self.assertTrue(w.project.scenario("TX").directions[0].quiet)
        w.spin_gbps.setValue(6.4)
        self.assertAlmostEqual(w.project.default_gbps, 6.4)
        self.assertTrue(w.compute())
        self.assertIn("TX", w.f_scens.values())
        self.assertIn("TX", w.f_scens.checked())  # a new scenario starts ticked


if __name__ == "__main__":
    unittest.main()
