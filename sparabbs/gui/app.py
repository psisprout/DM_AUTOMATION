"""The sparabbs window: load -> deck -> run -> reference node -> compare."""

from __future__ import annotations

import os
import time
import traceback

import numpy as np

from .. import compare as cmp_mod
from .. import deck as deck_mod
from .. import plotting as plot_mod
from .. import report as report_mod
from .. import runner as runner_mod
from ..netlist import NetlistInfo, read_netlist
from ..touchstone import Network, TouchstoneError, read_touchstone
from .qtcompat import QtCore, QtGui, QtWidgets, load_ui

UI_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "main_window.ui")

STATUS_COLOURS = {
    cmp_mod.PASS: QtGui.QColor("#14691f"),
    cmp_mod.WARN: QtGui.QColor("#9a6400"),
    cmp_mod.FAIL: QtGui.QColor("#b3261e"),
}
STATUS_TINTS = {
    cmp_mod.PASS: QtGui.QColor("#e8f4ea"),
    cmp_mod.WARN: QtGui.QColor("#fdf1d8"),
    cmp_mod.FAIL: QtGui.QColor("#fbe0de"),
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
        self.watcher: runner_mod.OutputWatcher | None = None
        self._poll_timer: QtCore.QTimer | None = None
        self._wait_deadline = 0.0
        self._out_dir = ""
        self._expected_snp = ""
        self._nports_running = 0

        self._init_widgets()
        self._connect()

    # -- setup ------------------------------------------------------------

    def _init_widgets(self) -> None:
        u = self.ui
        u.editCmd.setText(runner_mod.DEFAULT_COMMAND)
        u.editLinOptions.setText(deck_mod.DEFAULT_LIN_OPTIONS)
        u.editOutDir.setText(os.path.join(os.getcwd(), "sparabbs_run"))
        for label, value in REF_MODE_LABELS:
            u.comboRefMode.addItem(label, value)
        for label, value in ALIGN_LABELS:
            u.comboAlign.addItem(label, value)

        u.treeResult.setColumnCount(6)
        u.treeResult.setHeaderLabels(
            ["Term", "max err %", "max dB", "RMSE dB", "phase deg", "Status"]
        )
        mono = QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.FixedFont)
        u.textMapping.setFont(mono)
        u.textDeck.setFont(mono)
        u.textLog.setFont(mono)
        u.textDeck.setLineWrapMode(QtWidgets.QPlainTextEdit.NoWrap)
        u.textLog.setLineWrapMode(QtWidgets.QPlainTextEdit.NoWrap)
        u.runSplitter.setSizes([420, 300])
        for value, text in plot_mod.MODES:
            u.comboPlotMode.addItem(text, value)
        for value, text in plot_mod.LAYOUTS:
            u.comboPlotLayout.addItem(text, value)
        u.tableTerms.setSelectionMode(QtWidgets.QAbstractItemView.NoSelection)
        for header, size in (
            (u.tableTerms.horizontalHeader(), 26),
            (u.tableTerms.verticalHeader(), 22),
        ):
            header.setSectionsClickable(True)
            header.setSectionResizeMode(QtWidgets.QHeaderView.Fixed)
            header.setDefaultSectionSize(size)
        u.tableTerms.verticalHeader().setDefaultSectionSize(22)
        u.tableTerms.verticalHeader().setSectionResizeMode(QtWidgets.QHeaderView.Fixed)
        self._set_enabled(loaded=False)

    def _connect(self) -> None:
        u = self.ui
        u.btnBrowseSnp.clicked.connect(lambda: self._browse_file(u.editSnp, "Touchstone (*.s*p);;All files (*)"))
        u.btnBrowseBbs.clicked.connect(lambda: self._browse_file(u.editBbs, "SPICE netlist (*.sp *.cir *.inc *.net);;All files (*)"))
        u.btnBrowseResult.clicked.connect(lambda: self._browse_file(u.editResultSnp, "Touchstone (*.s*p);;All files (*)"))
        u.btnBrowseOut.clicked.connect(self._browse_dir)
        u.btnLoad.clicked.connect(self.on_load)
        u.comboSubckt.currentIndexChanged.connect(self._fill_mapping)
        u.btnGenDeck.clicked.connect(self.on_generate_deck)
        u.btnRun.clicked.connect(self.on_run)
        u.btnStop.clicked.connect(self.on_stop)
        u.comboRefMode.currentIndexChanged.connect(self._on_ref_mode_changed)
        u.listRefPorts.itemSelectionChanged.connect(self._update_ref_description)
        u.btnCompare.clicked.connect(self.on_compare)
        u.btnSaveJunit.clicked.connect(self.on_save_junit)
        u.btnPlotFromTree.clicked.connect(self.on_plot_from_tree)
        u.btnPlotSelected.clicked.connect(self.on_plot_selected)
        u.tableTerms.itemChanged.connect(self._on_term_toggled)
        u.tableTerms.horizontalHeader().sectionClicked.connect(self._toggle_column)
        u.tableTerms.verticalHeader().sectionClicked.connect(self._toggle_row)
        u.btnSelAll.clicked.connect(lambda: self._select(plot_mod.all_terms(self._n())))
        u.btnSelNone.clicked.connect(lambda: self._set_selection([]))
        u.btnSelInvert.clicked.connect(self._invert)
        u.btnSelDiag.clicked.connect(lambda: self._select(plot_mod.diagonal(self._n())))
        u.btnSelUpper.clicked.connect(lambda: self._select(plot_mod.upper(self._n())))
        u.btnSelOff.clicked.connect(lambda: self._select(plot_mod.off_diagonal(self._n())))
        u.btnSelFail.clicked.connect(lambda: self._select_status([cmp_mod.FAIL]))
        u.btnSelWarn.clicked.connect(lambda: self._select_status([cmp_mod.FAIL, cmp_mod.WARN]))
        u.btnSelWorst.clicked.connect(self._select_worst)
        u.btnSelName.clicked.connect(self._select_by_name)

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
        for w in (u.btnGenDeck, u.btnRun, u.btnCompare):
            w.setEnabled(loaded)
        for w in (u.btnSaveJunit, u.btnPlotFromTree, u.btnPlotSelected):
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

        self._fill_mapping()
        self._fill_ref_ports()
        self._set_enabled(loaded=True)
        self._status(f"Loaded {ref.nports}-port reference and {len(info.subckts)} subcircuit(s).")

    def _current_subckt(self):
        if not self.info:
            return None
        name = self.ui.comboSubckt.currentData()
        return self.info.by_name(name) if name else None

    def _effective_z0(self) -> np.ndarray:
        assert self.ref is not None
        if self.ref.uniform_z0() is None:
            return self.ref.z0.copy()
        return np.full(self.ref.nports, float(self.ui.spinZ0.value()))

    def assignments(self) -> list[deck_mod.PinAssignment]:
        """The automatic pin -> port mapping for the selected subcircuit."""
        sub, ref = self._current_subckt(), self.ref
        if sub is None or ref is None:
            return []
        return deck_mod.auto_map(sub, ref.port_names)[0]

    def _fill_mapping(self) -> None:
        sub, ref = self._current_subckt(), self.ref
        if sub is None or ref is None:
            self.ui.textMapping.clear()
            self.ui.lblMappingNotes.setText("-")
            return

        assignments, notes = deck_mod.auto_map(sub, ref.port_names)
        ports = sorted(
            (a for a in assignments if a.role == deck_mod.ROLE_PORT),
            key=lambda a: a.port,
        )
        width = max([len(a.pin) for a in assignments] + [4])
        lines = [f"{'pin':<{width}}  ->  port"]
        for a in ports:
            name = ref.port_names[a.port - 1]
            same = deck_mod.sanitize(name).lower() == deck_mod.sanitize(a.pin).lower()
            label = "" if same else f"   ({name})"
            lines.append(f"{a.pin:<{width}}  ->  {a.port}{label}")
        for a in assignments:
            if a.role != deck_mod.ROLE_PORT:
                lines.append(f"{a.pin:<{width}}  ->  tied to global 0")
        self.ui.textMapping.setPlainText("\n".join(lines))

        surplus = len(sub.pins) - ref.nports
        if surplus < 0:
            notes = [
                f"the subcircuit has {len(sub.pins)} pins but the reference has "
                f"{ref.nports} ports - there is no way to drive every port"
            ] + notes
        summary = (
            f"{ref.nports} ports driven"
            + (f", {surplus} surplus pin(s) tied to global 0" if surplus > 0 else "")
            + "."
        )
        self.ui.lblMappingNotes.setText(
            summary + ("  " + "  ".join(notes) if notes else "")
        )
        pal = self.ui.lblMappingNotes.palette()
        pal.setColor(
            QtGui.QPalette.WindowText,
            STATUS_COLOURS[cmp_mod.WARN if notes else cmp_mod.PASS],
        )
        self.ui.lblMappingNotes.setPalette(pal)

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
            assignments=self.assignments(),
            freq=ref.freq,
            z0=self._effective_z0(),
            out_dir=os.path.abspath(out_dir),
            max_points=self.ui.spinMaxPoints.value(),
            lin_options=self.ui.editLinOptions.text().strip()
            or deck_mod.DEFAULT_LIN_OPTIONS,
        )
        problems = deck_mod.validate(cfg)
        if problems:
            self._warn("Cannot build the deck", "\n".join(f"- {p}" for p in problems))
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
        self._log(f"-- launcher exited with code {code}")
        self.proc = None
        self._start_waiting()

    def _start_waiting(self) -> None:
        """Poll for the Touchstone file.

        The launcher is normally a queue submit command, so its exit tells us
        the job was accepted, not that it ran.  Keep watching the working
        directory until the file appears and stops growing.
        """
        self.watcher = runner_mod.OutputWatcher(
            self._out_dir,
            self._nports_running,
            self._expected_snp,
            self._run_started,
            exclude=[self.ref.path] if self.ref else [],
        )
        limit = self.ui.spinWaitMin.value()
        self._wait_deadline = (
            self._run_started + limit * 60.0 if limit else 0.0
        )
        self._log(
            f"-- waiting for *.s{self._nports_running}p in {self._out_dir}"
            + (f" (up to {limit} min)" if limit else " (no time limit)")
        )
        self.ui.btnStop.setEnabled(True)
        self.ui.btnStop.setText("Stop waiting")
        timer = QtCore.QTimer(self)
        timer.setInterval(2000)
        timer.timeout.connect(self._poll_output)
        self._poll_timer = timer
        timer.start()
        self._poll_output()

    def _poll_output(self) -> None:
        if self.watcher is None:
            return
        path, appeared = self.watcher.poll()
        for name in appeared:
            self._log(f"   appeared: {name}")
        waited = time.time() - self._run_started
        if path:
            self._finish_waiting()
            self.ui.editResultSnp.setText(path)
            self._log(f"-- found {path} after {waited:.0f}s")
            self._status(f"S-parameters produced: {os.path.basename(path)}")
            self.ui.tabs.setCurrentWidget(self.ui.tabRef)
            self._fill_ref_ports()
            return
        self._status(f"Waiting for the simulator... {waited:.0f}s elapsed")
        if self._wait_deadline and time.time() >= self._wait_deadline:
            self._give_up("the wait timed out")

    def _finish_waiting(self) -> None:
        if self._poll_timer is not None:
            self._poll_timer.stop()
            self._poll_timer = None
        self.watcher = None
        self.ui.btnRun.setEnabled(True)
        self.ui.btnStop.setEnabled(False)
        self.ui.btnStop.setText("Stop")

    def _give_up(self, why: str) -> None:
        produced = self.watcher.produced() if self.watcher else []
        self._finish_waiting()
        self._log(f"-- gave up: {why}")
        errors = runner_mod.scan_log_for_errors(self.ui.textLog.toPlainText())
        detail = [f"Nothing matching *.s{self._nports_running}p appeared in",
                  self._out_dir, ""]
        if produced:
            detail += ["The run did write:", "  " + ", ".join(produced[:20]), ""]
            if any(f.endswith((".lin", ".lin0")) for f in produced):
                detail += [
                    "A .lin file is present but no Touchstone file, so the "
                    ".LIN card's format/filename options are not doing what "
                    "the deck asks. Try a different spelling in the '.LIN "
                    "options' box - the deck preview shows the line that is "
                    "written.",
                    "",
                ]
        else:
            detail += ["The run wrote nothing to that directory at all.", ""]
        if errors:
            detail += ["From the log:", *errors[:8], ""]
        detail += [
            "If the simulator wrote the file elsewhere, point at it with "
            "Browse... below."
        ]
        self._warn(f"No S-parameters found ({why})", "\n".join(detail))
        self._status("Run finished, but no .sNp was found.")

    def on_stop(self) -> None:
        if self.proc is not None:
            self._log("-- stopping the launcher")
            self.proc.kill()
            return
        if self.watcher is not None:
            self._give_up("stopped")

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
        try:
            self._show_result(result, ref, dut)
        except Exception as exc:  # rendering must not lose the comparison
            self._warn(
                "Comparison finished, but the report could not be rendered",
                f"{exc}\n\n{traceback.format_exc()}",
            )
            return
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

        self._build_term_matrix()
        self._status(f"Compared {len(result.terms)} Z terms: {result.status}")

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
            self._warn("Nothing to plot", "Run a comparison first.")
            return False
        return True

    # -- step 5: picking terms to plot ------------------------------------

    def _n(self) -> int:
        return self.result.nports if self.result else 0

    def _build_term_matrix(self) -> None:
        """One checkbox per Zij, with the port names on the headers."""
        table = self.ui.tableTerms
        table.blockSignals(True)
        table.clear()
        n = self._n()
        table.setRowCount(n)
        table.setColumnCount(n)
        if n:
            names = self.result.port_names
            table.setHorizontalHeaderLabels([f"{k + 1}" for k in range(n)])
            table.setVerticalHeaderLabels([f"{k + 1} {names[k]}"[:18] for k in range(n)])
            for i in range(n):
                for j in range(n):
                    item = QtWidgets.QTableWidgetItem()
                    item.setFlags(QtCore.Qt.ItemIsUserCheckable | QtCore.Qt.ItemIsEnabled)
                    item.setCheckState(QtCore.Qt.Unchecked)
                    item.setToolTip(plot_mod.label(self.result, (i + 1, j + 1)))
                    if i == j:
                        item.setBackground(QtGui.QColor("#e4e9f0"))
                    table.setItem(i, j, item)
            self._colour_matrix_by_status()
        table.blockSignals(False)
        table.resizeColumnsToContents()
        self._select(plot_mod.diagonal(n))

    def _colour_matrix_by_status(self) -> None:
        """Tint each cell by its verdict.

        A letter in every cell makes a 24x24 grid unreadable; a wash of colour
        is scannable at a glance and leaves the checkbox alone.
        """
        for t in self.result.terms:
            for i, j in ((t.i, t.j), (t.j, t.i)):
                item = self.ui.tableTerms.item(i - 1, j - 1)
                if item is not None:
                    item.setBackground(STATUS_TINTS[t.status])
                    item.setToolTip(
                        f"{plot_mod.label(self.result, (i, j))}  -  {t.status}"
                    )

    def selection(self) -> list[tuple[int, int]]:
        table = self.ui.tableTerms
        return [
            (i + 1, j + 1)
            for i in range(table.rowCount())
            for j in range(table.columnCount())
            if table.item(i, j) is not None
            and table.item(i, j).checkState() == QtCore.Qt.Checked
        ]

    def _set_selection(self, terms) -> None:
        wanted = set(terms)
        table = self.ui.tableTerms
        table.blockSignals(True)
        for i in range(table.rowCount()):
            for j in range(table.columnCount()):
                item = table.item(i, j)
                if item is not None:
                    item.setCheckState(
                        QtCore.Qt.Checked
                        if (i + 1, j + 1) in wanted
                        else QtCore.Qt.Unchecked
                    )
        table.blockSignals(False)
        self._on_term_toggled()

    def _select(self, terms) -> None:
        """Add to the current selection, which is how the quick buttons compose."""
        self._set_selection(set(self.selection()) | set(terms))

    def _invert(self) -> None:
        current = set(self.selection())
        self._set_selection([t for t in plot_mod.all_terms(self._n()) if t not in current])

    def _toggle_row(self, index: int) -> None:
        terms = plot_mod.row(self._n(), index + 1)
        current = set(self.selection())
        if set(terms) <= current:
            self._set_selection(current - set(terms))
        else:
            self._set_selection(current | set(terms))

    def _toggle_column(self, index: int) -> None:
        terms = plot_mod.column(self._n(), index + 1)
        current = set(self.selection())
        if set(terms) <= current:
            self._set_selection(current - set(terms))
        else:
            self._set_selection(current | set(terms))

    def _select_status(self, statuses) -> None:
        if not self._have_result():
            return
        self._set_selection(plot_mod.by_status(self.result, statuses))

    def _select_worst(self) -> None:
        if not self._have_result():
            return
        self._set_selection(plot_mod.worst(self.result, self.ui.spinWorst.value()))

    def _select_by_name(self) -> None:
        if not self._have_result():
            return
        pattern = self.ui.editNameFilter.text()
        found = plot_mod.by_name(
            self.result, pattern, both=self.ui.chkNameBoth.isChecked()
        )
        if not found:
            self._status(f"No port name matches {pattern!r}")
            return
        self._select(found)

    def _on_term_toggled(self, *_args) -> None:
        count = len(self.selection())
        folded = len(plot_mod.fold_to_upper(self.selection()))
        text = f"{count} selected"
        if self.ui.chkFoldUpper.isChecked() and folded != count:
            text += f" -> {folded} after folding"
        self.ui.lblSelCount.setText(text)

    # -- plotting ---------------------------------------------------------

    def on_plot_from_tree(self) -> None:
        """Plot whichever term is highlighted in the results tree."""
        if not self._have_result():
            return
        item = self.ui.treeResult.currentItem()
        while item is not None and item.data(0, QtCore.Qt.UserRole) is None:
            item = item.parent()
        if item is None:
            self._warn("No term selected", "Pick a Z term in the tree first.")
            return
        self._plot([item.data(0, QtCore.Qt.UserRole)])

    def on_plot_selected(self) -> None:
        if not self._have_result():
            return
        terms = self.selection()
        if self.ui.chkFoldUpper.isChecked():
            terms = plot_mod.fold_to_upper(terms)
        if not terms:
            self._warn("Nothing selected", "Tick at least one term in the matrix.")
            return
        if len(terms) > plot_mod.BUSY_TRACES:
            answer = QtWidgets.QMessageBox.question(
                self.ui,
                "That is a lot of traces",
                f"{len(terms)} terms will be drawn, which is slow and hard to "
                "read.\n\nPlot them anyway?",
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                QtWidgets.QMessageBox.No,
            )
            if answer != QtWidgets.QMessageBox.Yes:
                return
        self._plot(terms)

    def _plot(self, terms) -> None:
        try:
            plot_mod.plot(
                self.result,
                terms,
                mode=self.ui.comboPlotMode.currentData(),
                layout=self.ui.comboPlotLayout.currentData(),
            )
        except plot_mod.PlotUnavailable as exc:
            self._warn("Cannot plot", f"{exc}\n\nEverything else works without it.")
        except Exception as exc:  # pragma: no cover - matplotlib backends vary
            self._warn("Plot failed", f"{exc}\n\n{traceback.format_exc()}")
        else:
            self._status(f"Plotted {len(terms)} term(s)")


def install_excepthook() -> None:
    """Show unhandled errors instead of letting Qt abort the process.

    PyQt calls abort() when a slot raises, which takes the whole window down
    and loses the run with it.  A dialog and a traceback on stderr are far more
    use than a core dump.
    """
    import sys

    previous = sys.excepthook

    def hook(kind, value, tb):
        text = "".join(traceback.format_exception(kind, value, tb))
        sys.stderr.write(text)
        try:
            QtWidgets.QMessageBox.critical(
                None,
                "sparabbs hit an unexpected error",
                f"{kind.__name__}: {value}\n\n{text}",
            )
        except Exception:  # a dialog is best-effort; never recurse
            previous(kind, value, tb)

    sys.excepthook = hook


def main(argv: list[str] | None = None) -> int:
    import sys

    argv = list(sys.argv if argv is None else argv)
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(argv)
    install_excepthook()
    window = MainWindow()
    window.show()
    return app.exec_() if hasattr(app, "exec_") else app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
