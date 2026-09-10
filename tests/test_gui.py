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

    def _flat_model(self, n, pin_order=None, name="flat"):
        """An n-port .snp and a matching n-pin subcircuit, no reference pin."""
        import numpy as np
        from sparabbs.touchstone import Network, write_touchstone

        names = [f"VDD{i}" for i in range(1, n + 1)]
        s = np.zeros((3, n, n), dtype=complex)
        s[:, range(n), range(n)] = 0.9
        snp = os.path.join(self.tmp.name, f"{name}.s{n}p")
        write_touchstone(
            Network(
                freq=np.logspace(3, 9, 3),
                s=s,
                z0=np.full(n, 50.0),
                port_names=names,
            ),
            snp,
        )
        pins = [names[i] for i in pin_order] if pin_order else names
        bbs = os.path.join(self.tmp.name, f"{name}.sp")
        with open(bbs, "w") as fh:
            fh.write(
                f".subckt {name} {' '.join(pins)}\nR1 {pins[0]} 0 1\n.ends\n"
            )
        return snp, bbs

    # -- step 1 ----------------------------------------------------------

    def test_ui_file_wires_up_every_widget_the_code_touches(self):
        for name in (
            "editSnp", "editBbs", "editOutDir", "btnLoad", "comboSubckt",
            "textMapping", "lblMappingNotes", "spinZ0", "lblZ0Src",
            "editCmd", "spinCpu",
            "spinMaxPoints", "btnGenDeck", "btnRun", "btnStop", "textDeck",
            "textLog", "editResultSnp", "comboRefMode", "listRefPorts",
            "lblRefDesc", "spinMagErr", "spinErrDb", "spinPhase",
            "spinPeakShift", "spinPeakMag", "comboAlign", "chkGateChecks",
            "chkFullMatrix", "btnCompare", "treeResult", "textXml",
            "lblVerdict", "statusbar", "tabs",
        ):
            self.assertTrue(hasattr(self.w.ui, name), f"missing widget: {name}")

    def test_load_populates_the_summary_and_mapping(self):
        self._load()
        self.assertIn("3-port", self.w.ui.lblSnpInfo.text())
        self.assertIn("pdn3_bbs", self.w.ui.lblBbsInfo.text())
        text = self.w.ui.textMapping.toPlainText()
        self.assertIn("VDD_CORE  ->  1", text)
        self.assertIn("VDD_PMIC  ->  3", text)
        self.assertIn("GND", text)
        self.assertIn("tied to global 0", text)
        self.assertIn("3 ports driven", self.w.ui.lblMappingNotes.text())
        self.assertIn("1 surplus pin(s)", self.w.ui.lblMappingNotes.text())

    def test_user_supplied_z0_reaches_the_deck(self):
        path = os.path.join(self.tmp.name, "noz0.s1p")
        with open(path, "w") as fh:
            fh.write("# HZ S RI\n1e6 0.1 0.0\n2e6 0.2 0.0\n")
        bbs = os.path.join(self.tmp.name, "one.sp")
        with open(bbs, "w") as fh:
            fh.write(".subckt one A\nR1 A 0 1\n.ends\n")
        self.w.ui.editSnp.setText(path)
        self.w.ui.editBbs.setText(bbs)
        self._load()
        self.w.ui.spinZ0.setValue(1.0)
        self.w.on_generate_deck()
        self.assertEqual(self.warnings, [])
        self.assertIn("z0=1 ", self.w.ui.textDeck.toPlainText())

    def test_mapping_needs_no_interaction_when_pins_match_ports(self):
        """The 24-port-to-24-pin case: load, then straight to Generate deck."""
        snp, bbs = self._flat_model(6)
        self.w.ui.editSnp.setText(snp)
        self.w.ui.editBbs.setText(bbs)
        self._load()
        self.assertEqual(self.w.ui.lblMappingNotes.text(), "6 ports driven.")
        cfg = self.w.on_generate_deck()
        self.assertIsNotNone(cfg)
        self.assertEqual(self.warnings, [])
        text = self.w.ui.textDeck.toPlainText()
        self.assertIn("P1 VDD1 0 port=1 z0=50", text)
        self.assertIn("P6 VDD6 0 port=6 z0=50", text)
        self.assertNotIn("Rleak", text)

    def test_shuffled_pin_order_is_mapped_by_name_and_flagged(self):
        snp, bbs = self._flat_model(4, pin_order=[2, 0, 3, 1])
        self.w.ui.editSnp.setText(snp)
        self.w.ui.editBbs.setText(bbs)
        self._load()
        self.assertIn("mapped by name", self.w.ui.lblMappingNotes.text())
        self.w.on_generate_deck()
        self.assertEqual(self.warnings, [])
        text = self.w.ui.textDeck.toPlainText()
        for k in range(1, 5):
            self.assertIn(f"P{k} VDD{k} 0 port={k} ", text)

    def test_too_few_pins_is_reported(self):
        snp, _ = self._flat_model(5)
        _, bbs = self._flat_model(3, name="small")
        self.w.ui.editSnp.setText(snp)
        self.w.ui.editBbs.setText(bbs)
        self._load()
        self.assertIn("no way to drive every port", self.w.ui.lblMappingNotes.text())
        self.assertIsNone(self.w.on_generate_deck())
        self.assertTrue(any("no pin drives port(s)" in m for _, m in self.warnings))

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

    def _run_and_settle(self, command="true"):
        """Run with a launcher that exits at once, then stop its poll timer."""
        import time as _t

        self._load()
        self.w.ui.editCmd.setText(command)
        self.w.on_run()
        deadline = 5.0
        while self.w.proc is not None and deadline > 0:
            self.app.processEvents()
            _t.sleep(0.02)
            deadline -= 0.02
        self.assertIsNone(self.w.proc, "the launcher never finished")
        if self.w._poll_timer is not None:
            self.w._poll_timer.stop()  # drive polling by hand from here

    def test_keeps_waiting_after_a_submit_launcher_exits(self):
        """primesim_sub returns as soon as the job is queued, not when it ran."""
        self._run_and_settle()
        self.assertEqual(self.warnings, [], "must not give up the moment the job is submitted")
        self.assertIsNotNone(self.w.watcher)
        self.assertTrue(self.w.ui.btnStop.isEnabled())
        self.assertEqual(self.w.ui.btnStop.text(), "Stop waiting")
        self.assertIn("waiting for", self.w.ui.textLog.toPlainText())

    def test_picks_up_a_result_that_arrives_later(self):
        self._run_and_settle()
        out = os.path.join(self.tmp.name, "bbs_sparam.s3p")
        with open(out, "w") as fh:
            fh.write("# HZ S RI R 50\n1e6 " + " ".join(["0.1 0.0"] * 9) + "\n")
        for _ in range(4):
            self.w._poll_output()
        self.assertEqual(self.warnings, [])
        self.assertEqual(self.w.ui.editResultSnp.text(), out)
        self.assertIsNone(self.w.watcher, "the watcher stops once the file is in")
        self.assertFalse(self.w.ui.btnStop.isEnabled())

    def test_stopping_the_wait_lists_what_the_run_did_write(self):
        self._run_and_settle()
        for name in ("ac0.ac", "lin0.lin"):
            with open(os.path.join(self.tmp.name, name), "w") as fh:
                fh.write("x")
        self.w._poll_output()
        self.w.on_stop()
        self.assertTrue(self.warnings)
        title, msg = self.warnings[-1]
        self.assertIn("No S-parameters", title)
        self.assertIn("ac0.ac", msg)
        self.assertIn("lin0.lin", msg)
        self.assertIn(".LIN card", msg, "a .lin with no .sNp needs the syntax hint")
        self.assertIsNone(self.w.watcher)

    def test_wait_times_out(self):
        self.w.ui.spinWaitMin.setValue(0)
        self._run_and_settle()
        self.w._wait_deadline = 1.0  # already in the past
        self.w._poll_output()
        self.assertTrue(any("timed out" in t for t, _ in self.warnings))

    def test_lin_options_are_editable_and_reach_the_deck(self):
        self._load()
        self.assertEqual(
            self.w.ui.editLinOptions.text(),
            "sparcalc=1 format=touchstone filename={base}",
        )
        self.w.ui.editLinOptions.setText("sparcalc=1 format=touchstone2 filename={base}")
        self.w.on_generate_deck()
        self.assertIn(
            ".lin sparcalc=1 format=touchstone2 filename=bbs_sparam",
            self.w.ui.textDeck.toPlainText(),
        )

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
