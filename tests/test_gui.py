"""GUI smoke tests - drive the real widgets on Qt's offscreen platform."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from tests import make_fixtures as fx  # noqa: E402

try:
    from sparabbs.gui.qtcompat import QtWidgets  # noqa: E402
    from sparabbs.gui.app import MainWindow  # noqa: E402

    QT_AVAILABLE = True
except ImportError as exc:  # pragma: no cover - depends on the environment
    QT_AVAILABLE = False
    QT_REASON = str(exc)

DATA = fx.DATA


@unittest.skipUnless(QT_AVAILABLE, "no Qt binding installed")
class GuiTests(unittest.TestCase):
    app = None

    @classmethod
    def setUpClass(cls):
        if not os.path.exists(os.path.join(DATA, "pdn3.s3p")):
            fx.main()
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.w = MainWindow()
        self.warnings: list[tuple[str, str]] = []
        self.w._warn = lambda t, m: self.warnings.append((t, m))  # no modal dialogs
        self.w.ui.editSnp.setText(os.path.join(DATA, "pdn3.s3p"))
        self.w.ui.editBbs.setText(os.path.join(DATA, "pdn3_bbs.sp"))
        self.w.ui.editOutDir.setText(self.tmp.name)

    def _load(self):
        self.w.on_load()
        self.assertEqual(self.warnings, [])

    # -- step 1 ----------------------------------------------------------

    def test_ui_file_wires_up_every_widget_the_code_touches(self):
        for name in (
            "editSnp", "editBbs", "editOutDir", "btnLoad", "comboSubckt",
            "tablePins", "spinZ0", "lblZ0Src", "editCmd", "spinCpu",
            "spinMaxPoints", "btnGenDeck", "btnRun", "btnStop", "textDeck",
            "textLog", "editResultSnp", "comboRefMode", "listRefPorts",
            "lblRefDesc", "spinMagErr", "spinErrDb", "spinPhase",
            "spinPeakShift", "spinPeakMag", "comboAlign", "chkGateChecks",
            "chkFullMatrix", "btnCompare", "treeResult", "textXml",
            "lblVerdict", "statusbar", "tabs",
        ):
            self.assertTrue(hasattr(self.w.ui, name), f"missing widget: {name}")

    def test_load_populates_the_summary_and_pin_table(self):
        self._load()
        self.assertIn("3-port", self.w.ui.lblSnpInfo.text())
        self.assertIn("pdn3_bbs", self.w.ui.lblBbsInfo.text())
        self.assertEqual(self.w.ui.tablePins.rowCount(), 4)
        self.assertEqual(self.w.ui.tablePins.item(0, 0).text(), "VDD_CORE")
        roles = [
            self.w.ui.tablePins.cellWidget(r, 1).currentText() for r in range(4)
        ]
        self.assertEqual(roles, ["port", "port", "port", "ground"])

    def test_z0_is_locked_when_the_file_states_it(self):
        self._load()
        self.assertFalse(self.w.ui.spinZ0.isEnabled())
        self.assertEqual(self.w.ui.spinZ0.value(), 50.0)
        self.assertIn("pdn3.s3p", self.w.ui.lblZ0Src.text())

    def test_z0_is_asked_for_when_the_file_omits_it(self):
        path = os.path.join(self.tmp.name, "noz0.s1p")
        with open(path, "w") as fh:
            fh.write("# HZ S RI\n1e6 0.1 0.0\n2e6 0.2 0.0\n")
        self.w.ui.editSnp.setText(path)
        self._load()
        self.assertTrue(self.w.ui.spinZ0.isEnabled())
        self.assertIn("states no reference impedance", self.w.ui.lblZ0Src.text())

    def test_user_supplied_z0_reaches_the_deck(self):
        path = os.path.join(self.tmp.name, "noz0.s1p")
        with open(path, "w") as fh:
            fh.write("# HZ S RI\n1e6 0.1 0.0\n2e6 0.2 0.0\n")
        self.w.ui.editSnp.setText(path)
        self._load()
        self.w.ui.spinZ0.setValue(1.0)
        # the 1-port reference needs a 1-port pin map
        self.w.ui.tablePins.cellWidget(1, 1).setCurrentText("ground")
        self.w.ui.tablePins.cellWidget(2, 1).setCurrentText("ground")
        self.w.on_generate_deck()
        self.assertEqual(self.warnings, [])
        self.assertIn("z0=1", self.w.ui.textDeck.toPlainText())

    def test_pin_role_change_disables_the_port_columns(self):
        self._load()
        self.w.ui.tablePins.cellWidget(0, 1).setCurrentText("ground")
        self.assertFalse(self.w.ui.tablePins.cellWidget(0, 2).isEnabled())
        self.assertFalse(self.w.ui.tablePins.cellWidget(0, 3).isEnabled())

    def test_reset_restores_the_default_mapping(self):
        self._load()
        self.w.ui.tablePins.cellWidget(0, 1).setCurrentText("float")
        self.w.ui.btnResetPins.click()
        self.assertEqual(self.w.ui.tablePins.cellWidget(0, 1).currentText(), "port")

    # -- step 2 ----------------------------------------------------------

    def test_generate_deck_writes_a_file_and_previews_it(self):
        self._load()
        cfg = self.w.on_generate_deck()
        self.assertIsNotNone(cfg)
        self.assertEqual(self.warnings, [])
        self.assertTrue(os.path.exists(self.w.deck_path))
        text = self.w.ui.textDeck.toPlainText()
        self.assertIn("P1 VDD_CORE 0 port=1 z0=50", text)
        self.assertIn(".lin sparcalc=1", text)
        self.assertTrue(self.w.ui.editResultSnp.text().endswith(".s3p"))

    def test_bad_pin_map_is_reported_not_crashed(self):
        self._load()
        self.w.ui.tablePins.cellWidget(1, 1).setCurrentText("ground")
        self.assertIsNone(self.w.on_generate_deck())
        self.assertTrue(self.warnings)
        self.assertIn("Pin map", self.warnings[0][0])

    def test_default_command_is_the_primesim_launcher(self):
        self.assertEqual(
            self.w.ui.editCmd.text(), "primesim_sub -spice -cpu {cpu} -i {deck}"
        )

    def test_a_broken_command_template_is_reported(self):
        self._load()
        self.w.ui.editCmd.setText("")
        self.w.on_run()
        self.assertTrue(any("command" in t.lower() for t, _ in self.warnings))
        self.assertIsNone(self.w.proc)

    def test_run_reports_a_missing_output_file(self):
        self._load()
        self.w.ui.editCmd.setText("true")
        self.w.on_run()
        deadline = 5.0
        while self.w.proc is not None and deadline > 0:
            self.app.processEvents()
            deadline -= 0.02
            import time as _t

            _t.sleep(0.02)
        self.assertIsNone(self.w.proc, "the process never finished")
        self.assertTrue(any("No S-parameters" in t for t, _ in self.warnings))

    # -- step 3 ----------------------------------------------------------

    def test_reference_ports_list_uses_the_touchstone_names(self):
        self._load()
        items = [
            self.w.ui.listRefPorts.item(i).text()
            for i in range(self.w.ui.listRefPorts.count())
        ]
        self.assertEqual(items, ["1: VDD_CORE", "2: VDD_IO", "3: VDD_PMIC"])

    def test_global_mode_disables_the_port_list(self):
        self._load()
        self.assertFalse(self.w.ui.listRefPorts.isEnabled())
        self.assertEqual(self.w.reference_spec().mode, "global")

    def test_port_mode_is_single_select_and_short_mode_is_multi(self):
        self._load()
        self.w.ui.comboRefMode.setCurrentIndex(1)  # reference to one port
        self.assertTrue(self.w.ui.listRefPorts.isEnabled())
        self.assertEqual(
            self.w.ui.listRefPorts.selectionMode(),
            QtWidgets.QAbstractItemView.SingleSelection,
        )
        self.w.ui.comboRefMode.setCurrentIndex(2)  # short to ground
        self.assertEqual(
            self.w.ui.listRefPorts.selectionMode(),
            QtWidgets.QAbstractItemView.ExtendedSelection,
        )

    def test_selecting_a_reference_port_updates_the_description(self):
        self._load()
        self.w.ui.comboRefMode.setCurrentIndex(1)
        self.w.ui.listRefPorts.setCurrentRow(2)
        spec = self.w.reference_spec()
        self.assertEqual(spec.ports, [3])
        self.assertIn("VDD_PMIC", self.w.ui.lblRefDesc.text())
        self.assertIn("2 port(s)", self.w.ui.lblRefDesc.text())

    # -- step 4 ----------------------------------------------------------

    def _compare_with(self, snp="pdn3_bbs.s3p"):
        self._load()
        self.w.ui.editResultSnp.setText(os.path.join(DATA, snp))
        self.w.on_compare()

    def test_compare_fills_the_tree_and_the_xml_pane(self):
        self._compare_with()
        self.assertEqual(self.warnings, [])
        self.assertIsNotNone(self.w.result)
        tree = self.w.ui.treeResult
        labels = [tree.topLevelItem(i).text(0) for i in range(tree.topLevelItemCount())]
        self.assertIn("Z11", labels)
        self.assertIn("Z13", labels)
        self.assertIn("BBS-result passivity", labels)
        xml = self.w.ui.textXml.toPlainText()
        self.assertTrue(xml.startswith("<bbs_validation"))
        self.assertIn("VDD_CORE", xml)
        self.assertIn(self.w.result.status, self.w.ui.lblVerdict.text())

    def test_tree_terms_carry_their_indices_for_plotting(self):
        self._compare_with()
        tree = self.w.ui.treeResult
        from sparabbs.gui.qtcompat import QtCore

        found = {
            tree.topLevelItem(i).data(0, QtCore.Qt.UserRole)
            for i in range(tree.topLevelItemCount())
        }
        self.assertIn((1, 1), found)
        self.assertIn((2, 3), found)

    def test_criteria_from_the_widgets_reach_the_comparison(self):
        self._load()
        self.w.ui.spinMagErr.setValue(0.01)
        self.w.ui.spinErrDb.setValue(0.001)
        self.w.ui.editResultSnp.setText(os.path.join(DATA, "pdn3_bad.s3p"))
        self.w.on_compare()
        self.assertEqual(self.w.result.status, "FAIL")
        self.assertIn("FAIL", self.w.ui.lblVerdict.text())

    def test_reference_mode_reduces_the_compared_port_count(self):
        self._load()
        self.w.ui.comboRefMode.setCurrentIndex(1)
        self.w.ui.listRefPorts.setCurrentRow(2)
        self.w.ui.editResultSnp.setText(os.path.join(DATA, "pdn3_bbs.s3p"))
        self.w.on_compare()
        self.assertEqual(self.warnings, [])
        self.assertEqual(self.w.result.nports, 2)
        self.assertIn('mode="port"', self.w.ui.textXml.toPlainText())

    def test_full_matrix_checkbox_adds_the_lower_triangle(self):
        self._load()
        self.w.ui.chkFullMatrix.setChecked(True)
        self.w.ui.editResultSnp.setText(os.path.join(DATA, "pdn3_bbs.s3p"))
        self.w.on_compare()
        self.assertEqual(len(self.w.result.terms), 9)

    def test_compare_without_a_result_file_is_reported(self):
        self._load()
        self.w.ui.editResultSnp.setText("")
        self.w.on_compare()
        self.assertTrue(any("No BBS result" in t for t, _ in self.warnings))

    def test_save_xml_writes_a_readable_report(self):
        import xml.etree.ElementTree as ET

        self._compare_with()
        out = os.path.join(self.tmp.name, "r.xml")
        self.w.ui.editOutDir.setText(self.tmp.name)
        # bypass the file dialog, exercise the same write path
        from sparabbs import report as report_mod

        tree = report_mod.build_xml(self.w.result, self.w.ref, self.w.dut)
        report_mod.write_xml(tree, out)
        root = ET.parse(out).getroot()
        self.assertEqual(root.tag, "bbs_validation")
        self.assertTrue(os.path.exists(os.path.join(self.tmp.name, "report.xsl")))

    def test_plot_without_matplotlib_explains_itself(self):
        self._compare_with()
        self.w.ui.treeResult.setCurrentItem(self.w.ui.treeResult.topLevelItem(3))
        try:
            import matplotlib  # noqa: F401

            self.skipTest("matplotlib is installed, nothing to report")
        except ImportError:
            pass
        self.w.on_plot()
        self.assertTrue(any("matplotlib" in t for t, _ in self.warnings))


if __name__ == "__main__":
    unittest.main()
