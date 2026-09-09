"""The sparabbs window: load -> deck -> run -> reference node -> compare."""

from __future__ import annotations

import os
import time
import traceback

import numpy as np

from .. import compare as cmp_mod
from .. import deck as deck_mod
from .. import report as report_mod
from .. import runner as runner_mod
from ..netlist import NetlistInfo, read_netlist
from ..touchstone import Network, TouchstoneError, read_touchstone
from .qtcompat import QtCore, QtGui, QtWidgets, load_ui

UI_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "main_window.ui")

PIN_COLUMNS = ("Pin", "Role", "Port", "Returns to")
STATUS_COLOURS = {
    cmp_mod.PASS: QtGui.QColor("#14691f"),
    cmp_mod.WARN: QtGui.QColor("#9a6400"),
    cmp_mod.FAIL: QtGui.QColor("#b3261e"),
}
REF_MODE_LABELS = (
    ("Raw ports (model's own reference)", cmp_mod.REF_GLOBAL),
    ("Reference every port to one port", cmp_mod.REF_PORT),
    ("Short selected ports to ground", cmp_mod.REF_SHORT),
    ("Leave selected ports open", cmp_mod.REF_OPEN),
)
ALIGN_LABELS = (
    ("Intersect (keep common points)", "intersect"),
    ("Exact (grids must match)", "exact"),
    ("Interpolate onto the reference grid", "interp"),
)


class MainWindow(QtCore.QObject):
    """Controller around the .ui layout."""

    def __init__(self) -> None:
        super().__init__()
        self.ui = load_ui(UI_FILE)
        self.ref: Network | None = None
        self.info: NetlistInfo | None = None
        self.result: cmp_mod.CompareResult | None = None
        self.dut: Network | None = None
        self.deck_path = ""
        self.command = ""
        self.proc: QtCore.QProcess | None = None
        self._run_started = 0.0

        self._init_widgets()
        self._connect()

    # -- setup ------------------------------------------------------------

    def _init_widgets(self) -> None:
        u = self.ui
        u.editCmd.setText(runner_mod.DEFAULT_COMMAND)
        u.editOutDir.setText(os.path.join(os.getcwd(), "sparabbs_run"))
        for label, value in REF_MODE_LABELS:
            u.comboRefMode.addItem(label, value)
        for label, value in ALIGN_LABELS:
            u.comboAlign.addItem(label, value)

        u.tablePins.setColumnCount(len(PIN_COLUMNS))
        u.tablePins.setHorizontalHeaderLabels(PIN_COLUMNS)
        u.tablePins.horizontalHeader().setStretchLastSection(True)

        u.treeResult.setColumnCount(6)
        u.treeResult.setHeaderLabels(
            ["Term", "max err %", "max dB", "RMSE dB", "phase deg", "Status"]
        )
        mono = QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.FixedFont)
        u.textDeck.setFont(mono)
        u.textLog.setFont(mono)
        u.textXml.setFont(mono)
        u.textDeck.setLineWrapMode(QtWidgets.QPlainTextEdit.NoWrap)
        u.textLog.setLineWrapMode(QtWidgets.QPlainTextEdit.NoWrap)
        u.runSplitter.setSizes([420, 300])
        u.cmpSplitter.setSizes([680, 460])
        self._set_enabled(loaded=False)

    def _connect(self) -> None:
        u = self.ui
        u.btnBrowseSnp.clicked.connect(lambda: self._browse_file(u.editSnp, "Touchstone (*.s*p);;All files (*)"))
        u.btnBrowseBbs.clicked.connect(lambda: self._browse_file(u.editBbs, "SPICE netlist (*.sp *.cir *.inc *.net);;All files (*)"))
        u.btnBrowseResult.clicked.connect(lambda: self._browse_file(u.editResultSnp, "Touchstone (*.s*p);;All files (*)"))
        u.btnBrowseOut.clicked.connect(self._browse_dir)
        u.btnLoad.clicked.connect(self.on_load)
        u.comboSubckt.currentIndexChanged.connect(self._on_subckt_changed)
        u.btnResetPins.clicked.connect(self._fill_pin_table)
        u.btnGenDeck.clicked.connect(self.on_generate_deck)
        u.btnRun.clicked.connect(self.on_run)
        u.btnStop.clicked.connect(self.on_stop)
        u.comboRefMode.currentIndexChanged.connect(self._on_ref_mode_changed)
        u.listRefPorts.itemSelectionChanged.connect(self._update_ref_description)
        u.btnCompare.clicked.connect(self.on_compare)
        u.btnSaveXml.clicked.connect(self.on_save_xml)
        u.btnSaveJunit.clicked.connect(self.on_save_junit)
        u.btnPlot.clicked.connect(self.on_plot)

    def show(self) -> None:
        self.ui.show()

    # -- helpers ----------------------------------------------------------

    def _status(self, text: str) -> None:
        self.ui.statusbar.showMessage(text, 15000)

    def _warn(self, title: str, text: str) -> None:
        QtWidgets.QMessageBox.warning(self.ui, title, text)

    def _browse_file(self, edit, filt: str) -> None:
        start = os.path.dirname(edit.text()) or os.getcwd()
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self.ui, "Select file", start, filt)
        if path:
            edit.setText(path)

    def _browse_dir(self) -> None:
        path = QtWidgets.QFileDialog.getExistingDirectory(
            self.ui, "Working directory", self.ui.editOutDir.text() or os.getcwd()
        )
        if path:
            self.ui.editOutDir.setText(path)

    def _set_enabled(self, loaded: bool) -> None:
        u = self.ui
        for w in (u.btnGenDeck, u.btnRun, u.btnResetPins, u.btnCompare):
            w.setEnabled(loaded)
        for w in (u.btnSaveXml, u.btnSaveJunit, u.btnPlot):
            w.setEnabled(self.result is not None)

    # -- step 1: load and parse ------------------------------------------

    def on_load(self) -> None:
        u = self.ui
        snp, bbs = u.editSnp.text().strip(), u.editBbs.text().strip()
        if not snp or not bbs:
            self._warn("Missing input", "Pick both the .snp and the BBS netlist first.")
            return
        try:
            ref = read_touchstone(snp)
        except (OSError, TouchstoneError) as exc:
            self._warn("Cannot read the Touchstone file", str(exc))
            return
        try:
            info = read_netlist(bbs)
        except (OSError, ValueError) as exc:
            self._warn("Cannot read the netlist", str(exc))
            return

        self.ref, self.info = ref, info
        u.lblSnpInfo.setText(ref.describe())
        u.lblBbsInfo.setText(
            f"{len(info.subckts)} subcircuit(s); "
            f"top candidate: {info.top_candidates()[0].name}"
        )

        if ref.z0_from_file and ref.uniform_z0() is not None:
            u.spinZ0.setValue(ref.uniform_z0())
            u.spinZ0.setEnabled(False)
            u.lblZ0Src.setText(f"taken from {os.path.basename(snp)}")
        elif ref.uniform_z0() is None:
            u.spinZ0.setEnabled(False)
            u.lblZ0Src.setText("per-port [Reference] from the file; not editable here")
        else:
            u.spinZ0.setEnabled(True)
            u.lblZ0Src.setText(
                "the file states no reference impedance - set it here (SPICE "
                "and the comparison both use this value)"
            )

        u.comboSubckt.blockSignals(True)
        u.comboSubckt.clear()
        tops = {s.name for s in info.top_candidates()}
        for sc in info.subckts:
            mark = "  (top)" if sc.name in tops else ""
            u.comboSubckt.addItem(f"{sc.name} - {sc.npins} pins{mark}", sc.name)
        u.comboSubckt.setCurrentIndex(
            max(0, u.comboSubckt.findData(info.top_candidates()[0].name))
        )
        u.comboSubckt.blockSignals(False)

        self._fill_pin_table()
        self._fill_ref_ports()
        self._set_enabled(loaded=True)
        self._status(f"Loaded {ref.nports}-port reference and {len(info.subckts)} subcircuit(s).")

    def _current_subckt(self):
        if not self.info:
            return None
        name = self.ui.comboSubckt.currentData()
        return self.info.by_name(name) if name else None

    def _on_subckt_changed(self) -> None:
        self._fill_pin_table()

    def _effective_z0(self) -> np.ndarray:
        assert self.ref is not None
        if self.ref.uniform_z0() is None:
            return self.ref.z0.copy()
        return np.full(self.ref.nports, float(self.ui.spinZ0.value()))

    def _fill_pin_table(self) -> None:
        sub, ref = self._current_subckt(), self.ref
        table = self.ui.tablePins
        table.setRowCount(0)
        if sub is None or ref is None:
            return
        assignments = deck_mod.default_assignments(sub, ref.nports)
        table.setRowCount(len(assignments))
        for row, a in enumerate(assignments):
            item = QtWidgets.QTableWidgetItem(a.pin)
            item.setFlags(item.flags() & ~QtCore.Qt.ItemIsEditable)
            table.setItem(row, 0, item)

            role = QtWidgets.QComboBox()
            role.addItems(list(deck_mod.ROLES))
            role.setCurrentText(a.role)
            role.currentTextChanged.connect(self._sync_pin_row_states)
            table.setCellWidget(row, 1, role)

            port = QtWidgets.QSpinBox()
            port.setRange(0, ref.nports)
            port.setValue(a.port)
            port.setSpecialValueText("-")
            table.setCellWidget(row, 2, port)

            minus = QtWidgets.QLineEdit(a.minus)
            minus.setPlaceholderText("0 (global ground)")
            table.setCellWidget(row, 3, minus)
        table.resizeColumnsToContents()
        self._sync_pin_row_states()

    def _sync_pin_row_states(self) -> None:
        table = self.ui.tablePins
        for row in range(table.rowCount()):
            role = table.cellWidget(row, 1)
            is_port = role is not None and role.currentText() == deck_mod.ROLE_PORT
            for col in (2, 3):
                w = table.cellWidget(row, col)
                if w is not None:
                    w.setEnabled(is_port)

    def read_pin_table(self) -> list[deck_mod.PinAssignment]:
        table = self.ui.tablePins
        out: list[deck_mod.PinAssignment] = []
        for row in range(table.rowCount()):
            pin = table.item(row, 0).text()
            role = table.cellWidget(row, 1).currentText()
            port = table.cellWidget(row, 2).value()
            minus = table.cellWidget(row, 3).text().strip() or deck_mod.GROUND
            out.append(
                deck_mod.PinAssignment(
                    pin=pin,
                    role=role,
                    port=port if role == deck_mod.ROLE_PORT else 0,
                    minus=minus if role == deck_mod.ROLE_PORT else deck_mod.GROUND,
                )
            )
        return out

    # -- step 2: deck and run --------------------------------------------

    def _deck_config(self) -> deck_mod.DeckConfig | None:
        sub, ref = self._current_subckt(), self.ref
        if sub is None or ref is None:
            self._warn("Nothing loaded", "Load the .snp and the netlist first.")
            return None
        out_dir = self.ui.editOutDir.text().strip()
        if not out_dir:
            self._warn("No working directory", "Pick a working directory.")
            return None
        cfg = deck_mod.DeckConfig(
            bbs_path=self.ui.editBbs.text().strip(),
            subckt=sub,
            assignments=self.read_pin_table(),
            freq=ref.freq,
            z0=self._effective_z0(),
            out_dir=os.path.abspath(out_dir),
            max_points=self.ui.spinMaxPoints.value(),
        )
        problems = deck_mod.validate(cfg)
        if problems:
            self._warn("Pin map problem", "\n".join(f"- {p}" for p in problems))
            return None
        return cfg

    def on_generate_deck(self) -> deck_mod.DeckConfig | None:
        cfg = self._deck_config()
        if cfg is None:
            return None
        try:
            self.deck_path = deck_mod.write_deck(cfg)
        except (OSError, deck_mod.DeckError) as exc:
            self._warn("Cannot write the deck", str(exc))
            return None
        with open(self.deck_path) as fh:
            self.ui.textDeck.setPlainText(fh.read())
        self.ui.editResultSnp.setText(cfg.expected_snp())
        self.ui.tabs.setCurrentWidget(self.ui.tabRun)
        self._status(f"Deck written to {self.deck_path}")
        return cfg

    def on_run(self) -> None:
        if self.proc is not None:
            self._warn("Already running", "Wait for the current run, or press Stop.")
            return
        cfg = self.on_generate_deck()
        if cfg is None:
            return
        spec = runner_mod.RunSpec(
            deck_path=self.deck_path,
            command_template=self.ui.editCmd.text().strip(),
            cpu=self.ui.spinCpu.value(),
            cwd=cfg.out_dir,
        )
        try:
            argv = spec.argv()
        except (runner_mod.RunnerError, KeyError, IndexError) as exc:
            self._warn("Bad run command", f"{exc}\n\nTemplate: {spec.command_template}")
            return
        self.command = spec.display()
        self._nports_running = cfg.nports
        self._expected_snp = cfg.expected_snp()
        self._out_dir = cfg.out_dir
        self._run_started = time.time()

        self.ui.textLog.clear()
        self._log(f"$ {self.command}")
        self._log(f"  (in {cfg.out_dir})")

        proc = QtCore.QProcess(self)
        proc.setWorkingDirectory(cfg.out_dir)
        proc.setProcessChannelMode(QtCore.QProcess.MergedChannels)
        proc.readyReadStandardOutput.connect(self._on_proc_output)
        proc.finished.connect(self._on_proc_finished)
        proc.errorOccurred.connect(self._on_proc_error)
        self.proc = proc
        self.ui.btnRun.setEnabled(False)
        self.ui.btnStop.setEnabled(True)
        proc.start(argv[0], argv[1:])

    def _log(self, text: str) -> None:
        self.ui.textLog.appendPlainText(text)

    def _on_proc_output(self) -> None:
        if self.proc is None:
            return
        data = bytes(self.proc.readAllStandardOutput()).decode("utf-8", "replace")
        for line in data.splitlines():
            self._log(line)

    def _on_proc_error(self, err) -> None:
        if err == QtCore.QProcess.FailedToStart:
            self._log("!! the simulator could not be started - check the command and PATH")

    def _on_proc_finished(self, code: int, _status) -> None:
        elapsed = time.time() - self._run_started
        self._log(f"-- exit code {code} after {elapsed:.1f}s")
        self.proc = None
        self.ui.btnRun.setEnabled(True)
        self.ui.btnStop.setEnabled(False)

        found = runner_mod.find_output_snp(
            self._out_dir, self._nports_running, self._expected_snp, self._run_started
        )
        if not found:
            errors = runner_mod.scan_log_for_errors(self.ui.textLog.toPlainText())
            detail = "\n".join(errors[:8]) or "(no error lines matched in the log)"
            self._warn(
                "No S-parameters were produced",
                f"Nothing matching *.s{self._nports_running}p appeared in\n"
                f"{self._out_dir}\n\nFrom the log:\n{detail}\n\n"
                "You can point at the file by hand below if the simulator wrote "
                "it somewhere else.",
            )
            self._status("Run finished, but no .sNp was found.")
            return
        self.ui.editResultSnp.setText(found)
        self._log(f"-- found {found}")
        self._status(f"S-parameters produced: {os.path.basename(found)}")
        self.ui.tabs.setCurrentWidget(self.ui.tabRef)
        self._fill_ref_ports()

    def on_stop(self) -> None:
        if self.proc is not None:
            self._log("-- stopping")
            self.proc.kill()

    # -- step 3: reference node ------------------------------------------

    def _fill_ref_ports(self) -> None:
        lst = self.ui.listRefPorts
        lst.clear()
        if self.ref is None:
            return
        for i, name in enumerate(self.ref.port_names, 1):
            lst.addItem(f"{i}: {name}")
        self._on_ref_mode_changed()

    def _on_ref_mode_changed(self) -> None:
        mode = self.ui.comboRefMode.currentData()
        lst = self.ui.listRefPorts
        if mode == cmp_mod.REF_GLOBAL:
            lst.setEnabled(False)
            lst.clearSelection()
        else:
            lst.setEnabled(True)
            lst.setSelectionMode(
                QtWidgets.QAbstractItemView.SingleSelection
                if mode == cmp_mod.REF_PORT
                else QtWidgets.QAbstractItemView.ExtendedSelection
            )
        self._update_ref_description()

    def reference_spec(self) -> cmp_mod.ReferenceSpec:
        mode = self.ui.comboRefMode.currentData()
        ports = [i.row() + 1 for i in self.ui.listRefPorts.selectedIndexes()]
        return cmp_mod.ReferenceSpec(mode=mode, ports=sorted(ports))

    def _update_ref_description(self) -> None:
        if self.ref is None:
            return
        spec = self.reference_spec()
        if spec.mode != cmp_mod.REF_GLOBAL and not spec.ports:
            self.ui.lblRefDesc.setText("Select at least one port for this mode.")
            return
        try:
            text = spec.describe(self.ref.port_names)
        except IndexError:
            text = "-"
        kept = self.ref.nports - (
            len(spec.ports) if spec.mode != cmp_mod.REF_GLOBAL else 0
        )
        self.ui.lblRefDesc.setText(
            f"{text}. The comparison will run on {kept} port(s), and the same "
            "transform is applied to the reference and the BBS result."
        )

    # -- step 4: compare and report --------------------------------------

    def criteria(self) -> cmp_mod.Criteria:
        u = self.ui
        return cmp_mod.Criteria(
            mag_err_pct=u.spinMagErr.value(),
            err_db=u.spinErrDb.value(),
            phase_err_deg=u.spinPhase.value(),
            peak_shift_pct=u.spinPeakShift.value(),
            peak_mag_err_pct=u.spinPeakMag.value(),
            gate_on_checks=u.chkGateChecks.isChecked(),
        )

    def on_compare(self) -> None:
        if self.ref is None:
            self._warn("Nothing loaded", "Load the reference .snp first.")
            return
        dut_path = self.ui.editResultSnp.text().strip()
        if not dut_path or not os.path.isfile(dut_path):
            self._warn(
                "No BBS result",
                "Run the deck, or point at the .sNp the simulator produced.",
            )
            return
        try:
            dut = read_touchstone(dut_path)
        except (OSError, TouchstoneError) as exc:
            self._warn("Cannot read the BBS result", str(exc))
            return

        ref = self.ref
        ref.z0 = self._effective_z0()
        try:
            result = cmp_mod.compare(
                ref,
                dut,
                criteria=self.criteria(),
                reference=self.reference_spec(),
                align_mode=self.ui.comboAlign.currentData(),
                upper_triangle_only=not self.ui.chkFullMatrix.isChecked(),
            )
        except cmp_mod.CompareError as exc:
            self._warn("Cannot compare", str(exc))
            return
        except Exception as exc:  # pragma: no cover - unexpected numerics
            self._warn("Comparison failed", f"{exc}\n\n{traceback.format_exc()}")
            return

        self.result, self.dut = result, dut
        self._show_result(result, ref, dut)
        self.ui.tabs.setCurrentWidget(self.ui.tabCompare)
        self._set_enabled(loaded=True)

    def _show_result(self, result, ref: Network, dut: Network) -> None:
        counts = result.counts()
        self.ui.lblVerdict.setText(
            f"{result.status}  -  {counts['PASS']} pass / {counts['WARN']} warn "
            f"/ {counts['FAIL']} fail"
        )
        pal = self.ui.lblVerdict.palette()
        pal.setColor(QtGui.QPalette.WindowText, STATUS_COLOURS[result.status])
        self.ui.lblVerdict.setPalette(pal)

        tree = self.ui.treeResult
        tree.clear()
        for name, status, detail in result.checks:
            node = QtWidgets.QTreeWidgetItem([name, detail, "", "", "", status])
            node.setForeground(5, STATUS_COLOURS[status])
            tree.addTopLevelItem(node)
        for t in result.terms:
            node = QtWidgets.QTreeWidgetItem([t.name, "", "", "", "", t.status])
            node.setForeground(5, STATUS_COLOURS[t.status])
            node.setData(0, QtCore.Qt.UserRole, (t.i, t.j))
            for b in t.bands:
                child = QtWidgets.QTreeWidgetItem(
                    [
                        b.name,
                        f"{b.norm_err_pct:.3g}",
                        f"{b.max_err_db:.3g}",
                        f"{b.rmse_db:.3g}",
                        f"{b.max_phase_deg:.3g}",
                        b.status,
                    ]
                )
                child.setForeground(5, STATUS_COLOURS[b.status])
                node.addChild(child)
            for p in t.peaks:
                child = QtWidgets.QTreeWidgetItem(
                    [
                        f"peak @ {p.ref_f:.4g} Hz",
                        f"{p.mag_err_pct:.3g}",
                        f"shift {p.shift_pct:.3g}%",
                        f"{p.ref_mag:.4g} ohm",
                        f"{p.dut_mag:.4g} ohm",
                        p.status,
                    ]
                )
                child.setForeground(5, STATUS_COLOURS[p.status])
                node.addChild(child)
            tree.addTopLevelItem(node)
        tree.expandToDepth(0)
        for c in range(tree.columnCount()):
            tree.resizeColumnToContents(c)

        tree_xml = report_mod.build_xml(
            result, ref, dut, deck_path=self.deck_path, command=self.command
        )
        self.ui.textXml.setPlainText(report_mod.to_string(tree_xml))
        self._status(f"Compared {len(result.terms)} Z terms: {result.status}")

    def on_save_xml(self) -> None:
        if not self._have_result():
            return
        start = os.path.join(self.ui.editOutDir.text() or os.getcwd(), "report.xml")
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self.ui, "Save report", start, "XML (*.xml)"
        )
        if not path:
            return
        tree = report_mod.build_xml(
            self.result, self.ref, self.dut, deck_path=self.deck_path, command=self.command
        )
        report_mod.write_xml(tree, path)
        self._status(f"Report written to {path} (report.xsl copied alongside it)")

    def on_save_junit(self) -> None:
        if not self._have_result():
            return
        start = os.path.join(self.ui.editOutDir.text() or os.getcwd(), "junit.xml")
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self.ui, "Save JUnit XML", start, "XML (*.xml)"
        )
        if path:
            report_mod.write_junit(self.result, path)
            self._status(f"JUnit XML written to {path}")

    def _have_result(self) -> bool:
        if self.result is None:
            self._warn("Nothing to save", "Run a comparison first.")
            return False
        return True

    def on_plot(self) -> None:
        if not self._have_result():
            return
        item = self.ui.treeResult.currentItem()
        while item is not None and item.data(0, QtCore.Qt.UserRole) is None:
            item = item.parent()
        if item is None:
            self._warn("No term selected", "Pick a Z term in the tree first.")
            return
        i, j = item.data(0, QtCore.Qt.UserRole)
        try:
            import matplotlib.pyplot as plt
        except ImportError:
            self._warn(
                "matplotlib is not installed",
                "Plotting needs matplotlib:\n  pip install matplotlib\n\n"
                "Everything else in sparabbs works without it.",
            )
            return
        r = self.result
        f = r.freq
        a, b = r.z_ref[:, i - 1, j - 1], r.z_dut[:, i - 1, j - 1]
        fig, (ax1, ax2) = plt.subplots(2, 1, sharex=True, figsize=(9, 7))
        ax1.loglog(f, np.abs(a), label="reference")
        ax1.loglog(f, np.abs(b), "--", label="BBS result")
        ax1.set_ylabel(f"|Z{i}{j}| (ohm)")
        ax1.grid(True, which="both", alpha=0.3)
        ax1.legend()
        ax1.set_title(
            f"Z{i}{j}  {r.port_names[i - 1]} / {r.port_names[j - 1]}  -  "
            f"{r.term(i, j).status}"
        )
        with np.errstate(divide="ignore", invalid="ignore"):
            err = 20 * np.log10(np.abs(b) / np.abs(a))
        ax2.semilogx(f, err)
        ax2.set_ylabel("error (dB)")
        ax2.set_xlabel("frequency (Hz)")
        ax2.grid(True, which="both", alpha=0.3)
        fig.tight_layout()
        plt.show()


def main(argv: list[str] | None = None) -> int:
    import sys

    argv = list(sys.argv if argv is None else argv)
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(argv)
    window = MainWindow()
    window.show()
    return app.exec_() if hasattr(app, "exec_") else app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
