"""The chanxt window.

1 Models & ports -> 2 Nets & groups -> 3 Terminations -> 4 Scenarios & speed
-> 5 Select (facets / matrix / breakdown) -> 6 Output (HTML / CSV)

Every edit writes straight into ``self.project``; the analysis is rebuilt
when it is next needed (Compute, or opening tab 5/6 after a change).
"""

from __future__ import annotations

import copy
import math
import os
import sys
import traceback
from typing import Dict, List, Optional

from .. import export
from ..analysis import ALL_KINDS, KIND_LABELS, Analysis, Key
from ..project import (
    NAME_RULE_HELP,
    NAME_RULES,
    auto_name_rule,
    DirRule,
    Project,
    Scenario,
    apply_lp5x_preset,
    positional_locations,
    positional_nets,
)
from ..select import (
    A_MODE_LABELS,
    A_MODES,
    NO_TAG,
    FilterSpec,
    breakdown,
    default_spec,
    matrix,
    matrix_sources,
    preview,
    run_filter,
)
from ..touchstone import TouchstoneError, read_touchstone
from ..units import parse_freq_list
from ..workflow import add_model, map_ports
from .qtcompat import QtCore, QtGui, QtWidgets
from .widgets import (
    CheckList,
    DirTable,
    RuleTable,
    TermTable,
    combo,
    combo_value,
    heat_colour,
    set_combo,
    small_button,
)

PREVIEW_LIMIT = 3000
EXTRACT_LIMIT = 5000
NAME_RULE_CHOICES = [
    ("auto", "Auto-detect"),
    ("NET_LOC", "NET_LOC  - " + NAME_RULE_HELP["NET_LOC"]),
    ("LOC_NET", "LOC_NET  - " + NAME_RULE_HELP["LOC_NET"]),
    ("NET", "NET  - " + NAME_RULE_HELP["NET"]),
    ("custom", "Custom regex (?P<net>...) (?P<loc>...)"),
]


class NumItem(QtWidgets.QTableWidgetItem):
    """Sorts by the number in UserRole; NaN sorts last."""

    def __init__(self, value: float, text: Optional[str] = None) -> None:
        super().__init__(text if text is not None else ("" if math.isnan(value) else f"{value:.2f}"))
        self.setData(QtCore.Qt.UserRole, value)
        self.setTextAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)

    def __lt__(self, other) -> bool:
        a = self.data(QtCore.Qt.UserRole)
        b = other.data(QtCore.Qt.UserRole)
        try:
            a, b = float(a), float(b)
        except (TypeError, ValueError):
            return super().__lt__(other)
        if math.isnan(a):
            return False
        if math.isnan(b):
            return True
        return a < b


def _exec(obj) -> int:
    """``exec_`` on PyQt5 / PySide2, ``exec`` on Qt 6 bindings."""
    run = getattr(obj, "exec_", None) or getattr(obj, "exec")
    return run()


def ro_item(text: str) -> QtWidgets.QTableWidgetItem:
    it = QtWidgets.QTableWidgetItem(text)
    it.setFlags(it.flags() & ~QtCore.Qt.ItemIsEditable)
    return it


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("chanxt - channel IL / RL / crosstalk")
        self.resize(1400, 900)
        self.project = Project()
        self.networks: Dict[str, object] = {}
        self.analysis: Optional[Analysis] = None
        self.stale = True
        self.spec: Optional[FilterSpec] = None
        self.selected: List[Key] = []
        self._universe: Dict[str, set] = {}
        self._loading = False
        self._scen_index = -1
        self._matrix = None
        self._bd_rows = []

        self.tabs = QtWidgets.QTabWidget()
        self.setCentralWidget(self.tabs)
        self.tabs.addTab(self._build_models_tab(), "1 · Models && ports")
        self.tabs.addTab(self._build_nets_tab(), "2 · Nets && groups")
        self.tabs.addTab(self._build_terms_tab(), "3 · Terminations")
        self.tabs.addTab(self._build_scen_tab(), "4 · Scenarios && speed")
        self.tabs.addTab(self._build_select_tab(), "5 · Select")
        self.tabs.addTab(self._build_output_tab(), "6 · Output")
        self.tabs.currentChanged.connect(self._on_tab)
        self._build_menu()
        self._sel_timer = QtCore.QTimer(self)
        self._sel_timer.setSingleShot(True)
        self._sel_timer.setInterval(250)
        self._sel_timer.timeout.connect(self.apply_filter)
        self.refresh_all()

    # ==================================================================
    # small helpers
    # ==================================================================

    def _status(self, text: str) -> None:
        self.statusBar().showMessage(text, 15000)

    def _warn(self, title: str, text: str) -> None:
        QtWidgets.QMessageBox.warning(self, title, text)

    def _busy(self, on: bool) -> None:
        if on:
            QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
            QtWidgets.QApplication.processEvents()
        else:
            QtWidgets.QApplication.restoreOverrideCursor()

    def mark_stale(self) -> None:
        if self._loading:
            return
        self.stale = True
        self.lbl_compute.setText("Settings changed - Compute to update (done automatically on tab 5/6).")

    def _ask_text(self, title: str, label: str, text: str = "") -> Optional[str]:
        value, ok = QtWidgets.QInputDialog.getText(self, title, label, text=text)
        value = value.strip()
        return value if ok and value else None

    # ==================================================================
    # menu
    # ==================================================================

    def _build_menu(self) -> None:
        m = self.menuBar().addMenu("&File")
        for text, slot, key in (
            ("&New project", self.on_new, "Ctrl+N"),
            ("&Open project...", self.on_open, "Ctrl+O"),
            ("&Save project", self.on_save, "Ctrl+S"),
            ("Save project &as...", self.on_save_as, None),
        ):
            act = m.addAction(text)
            act.triggered.connect(slot)
            if key:
                act.setShortcut(QtGui.QKeySequence(key))
        m.addSeparator()
        m.addAction("&Quit").triggered.connect(self.close)
        t = self.menuBar().addMenu("&Tools")
        t.addAction("Apply &LPDDR5X preset...").triggered.connect(self.on_lp5x)
        t.addAction("&Compute").triggered.connect(self.compute)

    def on_new(self) -> None:
        self.project = Project()
        self.networks = {}
        self.analysis, self.spec, self.selected = None, None, []
        self._universe = {}
        self.stale = True
        self.refresh_all()

    def on_open(self) -> None:
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "Open project", os.getcwd(),
                                                        "chanxt project (*.json);;All files (*)")
        if path:
            self.open_project(path)

    def open_project(self, path: str) -> None:
        try:
            proj = Project.load(path)
        except (OSError, ValueError) as exc:
            self._warn("Cannot open project", str(exc))
            return
        nets, problems = {}, []
        self._busy(True)
        try:
            for spec in proj.models:
                try:
                    net = read_touchstone(spec.path)
                except (OSError, TouchstoneError) as exc:
                    problems.append(f"{spec.alias}: {exc}")
                    continue
                if len(spec.ports) != net.nports:
                    map_ports(spec, net, proj.loc_alias)
                    problems.append(f"{spec.alias}: port count changed - re-mapped from names")
                nets[spec.alias] = net
        finally:
            self._busy(False)
        self.project, self.networks = proj, nets
        self.analysis, self.selected = None, []
        last = proj.presets.get("_last")
        self.spec = FilterSpec.from_dict(last) if last else None
        self._universe = {}
        self.stale = True
        self.refresh_all()
        self._restore_output_options()
        self.setWindowTitle(f"chanxt - {os.path.basename(path)}")
        if problems:
            self._warn("Some models need attention", "\n".join(problems))
        self._status(f"opened {path}")

    def on_save(self) -> None:
        if not self.project.path:
            self.on_save_as()
            return
        self._save(self.project.path)

    def on_save_as(self) -> None:
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Save project", self.project.path or os.path.join(os.getcwd(), "chanxt_project.json"),
            "chanxt project (*.json)")
        if path:
            if not path.lower().endswith(".json"):
                path += ".json"
            self._save(path)

    def _save(self, path: str) -> None:
        if self.spec is not None:
            self.project.presets["_last"] = self.spec.to_dict()
        self._store_output_options()
        try:
            self.project.save(path)
        except OSError as exc:
            self._warn("Cannot save", str(exc))
            return
        self.setWindowTitle(f"chanxt - {os.path.basename(path)}")
        self._status(f"saved {path}")

    def on_lp5x(self) -> None:
        locs = list(self.project.locations)
        if len(locs) < 2:
            self._warn("LPDDR5X preset", "Map at least two port locations first (tab 1).")
            return
        guess_ctrl = next((x for x in locs if x.lower() in ("soc", "ap", "cpu", "ctrl", "host", "mc")), locs[0])
        ctrl, ok = QtWidgets.QInputDialog.getItem(self, "LPDDR5X preset", "Controller (SoC) location:",
                                                  locs, locs.index(guess_ctrl), False)
        if not ok:
            return
        rest = [x for x in locs if x != ctrl]
        mem, ok = QtWidgets.QInputDialog.getItem(self, "LPDDR5X preset", "DRAM location:", rest, 0, False)
        if not ok:
            return
        gbps, ok = QtWidgets.QInputDialog.getDouble(self, "LPDDR5X preset", "DQ speed (Gbps):",
                                                    10.667, 0.1, 100.0, 3)
        if not ok:
            return
        apply_lp5x_preset(self.project, ctrl, mem, gbps)
        self.mark_stale()
        self.refresh_all()
        self.tabs.setCurrentIndex(3)
        self._status("LPDDR5X WRITE/READ scenarios, speeds and terminations applied")

    # ==================================================================
    # tab 1: models & ports
    # ==================================================================

    def _build_models_tab(self) -> QtWidgets.QWidget:
        w = QtWidgets.QWidget()
        lay = QtWidgets.QHBoxLayout(w)

        left = QtWidgets.QVBoxLayout()
        left.addWidget(QtWidgets.QLabel("<b>Models</b> (Touchstone files)"))
        self.list_models = QtWidgets.QListWidget()
        left.addWidget(self.list_models, 1)
        row = QtWidgets.QHBoxLayout()
        self.btn_add_model = QtWidgets.QPushButton("Add .sNp...")
        self.btn_del_model = QtWidgets.QPushButton("Remove")
        row.addWidget(self.btn_add_model)
        row.addWidget(self.btn_del_model)
        left.addLayout(row)
        row = QtWidgets.QHBoxLayout()
        row.addWidget(QtWidgets.QLabel("Alias"))
        self.edit_alias = QtWidgets.QLineEdit()
        self.edit_alias.setToolTip("Short name used in legends and tables, e.g. revA")
        row.addWidget(self.edit_alias)
        self.btn_alias = small_button("Rename")
        row.addWidget(self.btn_alias)
        left.addLayout(row)
        self.lbl_model_info = QtWidgets.QLabel("")
        self.lbl_model_info.setWordWrap(True)
        self.lbl_model_info.setStyleSheet("color:#5d6677")
        left.addWidget(self.lbl_model_info)
        lw = QtWidgets.QWidget()
        lw.setLayout(left)
        lw.setMaximumWidth(330)
        lay.addWidget(lw)

        right = QtWidgets.QVBoxLayout()
        box = QtWidgets.QGroupBox("Port name -> net + location")
        g = QtWidgets.QGridLayout(box)
        self.combo_name_rule = combo([t for _, t in NAME_RULE_CHOICES], "auto",
                                     data=[k for k, _ in NAME_RULE_CHOICES])
        self.edit_name_regex = QtWidgets.QLineEdit()
        self.edit_name_regex.setPlaceholderText(r"^(?P<net>.+)_(?P<loc>[^_]+)$")
        self.edit_loc_alias = QtWidgets.QLineEdit()
        self.edit_loc_alias.setPlaceholderText("U1=SoC, U2=DRAM   (rename raw location tokens)")
        self.btn_remap = QtWidgets.QPushButton("Re-map this model from port names")
        self.btn_remap_all = QtWidgets.QPushButton("Re-map all models")
        g.addWidget(QtWidgets.QLabel("Rule"), 0, 0)
        g.addWidget(self.combo_name_rule, 0, 1, 1, 3)
        g.addWidget(QtWidgets.QLabel("Regex"), 1, 0)
        g.addWidget(self.edit_name_regex, 1, 1, 1, 3)
        g.addWidget(QtWidgets.QLabel("Location alias"), 2, 0)
        g.addWidget(self.edit_loc_alias, 2, 1, 1, 3)
        g.addWidget(self.btn_remap, 3, 1)
        g.addWidget(self.btn_remap_all, 3, 2)
        right.addWidget(box)

        box = QtWidgets.QGroupBox("No usable names? Fill by port position")
        h = QtWidgets.QHBoxLayout(box)
        self.combo_layout = combo(["Ports 1..N/2 = A, rest = B", "Odd ports = A, even = B"],
                                  "halves", data=["halves", "alternate"])
        self.edit_loc_a = QtWidgets.QLineEdit("SoC")
        self.edit_loc_b = QtWidgets.QLineEdit("DRAM")
        for e in (self.edit_loc_a, self.edit_loc_b):
            e.setMaximumWidth(90)
        self.btn_set_locs = QtWidgets.QPushButton("Set locations")
        self.btn_paste_nets = QtWidgets.QPushButton("Paste net names...")
        h.addWidget(self.combo_layout)
        h.addWidget(QtWidgets.QLabel("A"))
        h.addWidget(self.edit_loc_a)
        h.addWidget(QtWidgets.QLabel("B"))
        h.addWidget(self.edit_loc_b)
        h.addWidget(self.btn_set_locs)
        h.addWidget(self.btn_paste_nets)
        h.addStretch(1)
        right.addWidget(box)

        self.table_ports = QtWidgets.QTableWidget(0, 7)
        self.table_ports.setHorizontalHeaderLabels(
            ["#", "Port name", "Net", "Location", "Group", "Tag", "Termination"])
        self.table_ports.verticalHeader().setVisible(False)
        self.table_ports.horizontalHeader().setStretchLastSection(True)
        for c, wdt in enumerate((40, 200, 140, 90, 80, 70)):
            self.table_ports.setColumnWidth(c, wdt)
        right.addWidget(QtWidgets.QLabel("Net and Location are editable. Group/Tag come from tab 2, "
                                         "Termination from tab 3."))
        right.addWidget(self.table_ports, 1)
        lay.addLayout(right, 1)

        self.btn_add_model.clicked.connect(self.on_add_models)
        self.btn_del_model.clicked.connect(self.on_remove_model)
        self.btn_alias.clicked.connect(self.on_rename_model)
        self.list_models.currentRowChanged.connect(self._fill_ports)
        self.combo_name_rule.currentIndexChanged.connect(self._on_name_rule)
        self.btn_remap.clicked.connect(lambda: self.on_remap(all_models=False))
        self.btn_remap_all.clicked.connect(lambda: self.on_remap(all_models=True))
        self.btn_set_locs.clicked.connect(self.on_set_locations)
        self.btn_paste_nets.clicked.connect(self.on_paste_nets)
        self.table_ports.itemChanged.connect(self._on_port_edit)
        return w

    def _current_model(self):
        r = self.list_models.currentRow()
        return self.project.models[r] if 0 <= r < len(self.project.models) else None

    def on_add_models(self) -> None:
        paths, _ = QtWidgets.QFileDialog.getOpenFileNames(
            self, "Add Touchstone models", os.getcwd(), "Touchstone (*.s*p);;All files (*)")
        self.add_model_files(paths)

    def add_model_files(self, paths: List[str]) -> None:
        errors = []
        self._busy(True)
        try:
            for p in paths:
                try:
                    net = read_touchstone(p)
                except (OSError, TouchstoneError) as exc:
                    errors.append(f"{os.path.basename(p)}: {exc}")
                    continue
                self._parse_loc_alias()
                spec = add_model(self.project, net)
                self.networks[spec.alias] = net
        finally:
            self._busy(False)
        if errors:
            self._warn("Some files could not be read", "\n".join(errors))
        self.mark_stale()
        self.refresh_all()
        self.list_models.setCurrentRow(len(self.project.models) - 1)

    def on_remove_model(self) -> None:
        spec = self._current_model()
        if spec is None:
            return
        self.project.models.remove(spec)
        self.networks.pop(spec.alias, None)
        self.project.sync_locations()
        self.mark_stale()
        self.refresh_all()

    def on_rename_model(self) -> None:
        spec = self._current_model()
        new = self.edit_alias.text().strip()
        if spec is None or not new or new == spec.alias:
            return
        if any(m.alias == new for m in self.project.models):
            self._warn("Rename", f"{new} is already used")
            return
        if spec.alias in self.networks:
            self.networks[new] = self.networks.pop(spec.alias)
        if self.spec is not None:
            self.spec.models = [new if m == spec.alias else m for m in self.spec.models]
        spec.alias = new
        self.mark_stale()
        self.refresh_all()

    def _on_name_rule(self) -> None:
        rule = combo_value(self.combo_name_rule)
        if rule in NAME_RULES:
            self.edit_name_regex.setText(NAME_RULES[rule])
        self.edit_name_regex.setReadOnly(rule != "custom")

    def _parse_loc_alias(self) -> None:
        alias = {}
        for part in self.edit_loc_alias.text().replace(";", ",").split(","):
            if "=" in part:
                k, v = part.split("=", 1)
                if k.strip() and v.strip():
                    alias[k.strip()] = v.strip()
        self.project.loc_alias = alias

    def on_remap(self, all_models: bool) -> None:
        self._parse_loc_alias()
        rule = combo_value(self.combo_name_rule)
        targets = self.project.models if all_models else [m for m in [self._current_model()] if m]
        for spec in targets:
            net = self.networks.get(spec.alias)
            if net is None:
                continue
            spec.name_rule = rule
            spec.name_regex = self.edit_name_regex.text().strip() if rule == "custom" else ""
            try:
                map_ports(spec, net, self.project.loc_alias)
            except Exception as exc:  # bad custom regex
                self._warn("Cannot apply the rule", str(exc))
                return
        self.project.sync_locations()
        self.mark_stale()
        self.refresh_all()

    def on_set_locations(self) -> None:
        spec = self._current_model()
        if spec is None:
            return
        a, b = self.edit_loc_a.text().strip(), self.edit_loc_b.text().strip()
        if not a or not b or a == b:
            self._warn("Locations", "Give two different location names.")
            return
        locs = positional_locations(len(spec.ports), combo_value(self.combo_layout), a, b)
        for pm, loc in zip(spec.ports, locs):
            pm.loc = loc
        self.project.sync_locations()
        self.mark_stale()
        self.refresh_all()

    def on_paste_nets(self) -> None:
        spec = self._current_model()
        if spec is None:
            return
        n = len(spec.ports)
        dlg = QtWidgets.QDialog(self)
        dlg.setWindowTitle("Paste net names")
        v = QtWidgets.QVBoxLayout(dlg)
        v.addWidget(QtWidgets.QLabel(
            f"One net per line, in port order: {n} names (one per port) or {n // 2} names "
            "(given to both ends, using the position layout chosen on the left)."))
        text = QtWidgets.QPlainTextEdit()
        v.addWidget(text)
        bb = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        v.addWidget(bb)
        dlg.resize(420, 500)
        if _exec(dlg) != QtWidgets.QDialog.Accepted:
            return
        try:
            nets = positional_nets(n, combo_value(self.combo_layout), text.toPlainText().splitlines())
        except ValueError as exc:
            self._warn("Paste net names", str(exc))
            return
        for pm, net in zip(spec.ports, nets):
            pm.net = net
        self.mark_stale()
        self.refresh_all()

    def _fill_model_list(self) -> None:
        cur = self.list_models.currentRow()
        self.list_models.blockSignals(True)
        self.list_models.clear()
        for m in self.project.models:
            ok = m.alias in self.networks
            self.list_models.addItem(f"{m.alias}  ({os.path.basename(m.path)})" + ("" if ok else "  [missing]"))
        self.list_models.blockSignals(False)
        if self.project.models:
            self.list_models.setCurrentRow(min(max(cur, 0), len(self.project.models) - 1))
        self._fill_ports()

    def _fill_ports(self, *_args) -> None:
        spec = self._current_model()
        self._loading = True
        self.table_ports.setRowCount(0)
        if spec is None:
            self.lbl_model_info.setText("Add one or more .sNp files.")
            self.edit_alias.setText("")
            self._loading = False
            return
        net = self.networks.get(spec.alias)
        self.edit_alias.setText(spec.alias)
        set_combo(self.combo_name_rule, spec.name_rule)
        rule = spec.name_rule
        if rule == "auto" and net is not None:
            rule = auto_name_rule(net.port_names)
            self.combo_name_rule.setItemText(0, f"Auto-detect (found {rule})")
        self.edit_name_regex.setText(spec.name_regex if spec.name_rule == "custom"
                                     else NAME_RULES.get(rule, ""))
        self.edit_name_regex.setReadOnly(spec.name_rule != "custom")
        self.edit_loc_alias.setText(", ".join(f"{k}={v}" for k, v in self.project.loc_alias.items()))
        self.lbl_model_info.setText(f"{spec.path}\n" + (net.describe() if net else "file not loaded"))
        names = net.port_names if net else [f"P{i + 1}" for i in range(len(spec.ports))]
        self.table_ports.setRowCount(len(spec.ports))
        for i, pm in enumerate(spec.ports):
            self.table_ports.setItem(i, 0, ro_item(str(i + 1)))
            self.table_ports.setItem(i, 1, ro_item(names[i] if i < len(names) else ""))
            self.table_ports.setItem(i, 2, QtWidgets.QTableWidgetItem(pm.net))
            self.table_ports.setItem(i, 3, QtWidgets.QTableWidgetItem(pm.loc))
            self._fill_port_derived(i, pm)
        self._loading = False

    def _fill_port_derived(self, i: int, pm) -> None:
        proj = self.project
        group = proj.group_of(pm.net) if pm.net else ""
        tag = proj.tag_of(pm.net) if pm.net else ""
        term = proj.resolve_term(pm.net, pm.loc) if pm.net and pm.loc else None
        self.table_ports.setItem(i, 4, ro_item(group))
        self.table_ports.setItem(i, 5, ro_item(tag))
        self.table_ports.setItem(i, 6, ro_item(term.describe() if term else "Z0 (unmapped)"))

    def _on_port_edit(self, item) -> None:
        if self._loading or item.column() not in (2, 3):
            return
        spec = self._current_model()
        if spec is None or item.row() >= len(spec.ports):
            return
        pm = spec.ports[item.row()]
        if item.column() == 2:
            pm.net = item.text().strip()
        else:
            pm.loc = item.text().strip()
            self.project.sync_locations()
        self._loading = True
        self._fill_port_derived(item.row(), pm)
        self._loading = False
        self.mark_stale()

    # ==================================================================
    # tab 2: nets & groups
    # ==================================================================

    def _build_nets_tab(self) -> QtWidgets.QWidget:
        w = QtWidgets.QWidget()
        lay = QtWidgets.QHBoxLayout(w)
        left = QtWidgets.QVBoxLayout()
        left.addWidget(QtWidgets.QLabel("<b>Location order</b> (drag to reorder)"))
        self.list_locs = QtWidgets.QListWidget()
        self.list_locs.setDragDropMode(QtWidgets.QAbstractItemView.InternalMove)
        self.list_locs.setMaximumHeight(120)
        left.addWidget(self.list_locs)
        hint = QtWidgets.QLabel("Order sets IL direction for basic metrics (first -> later) "
                                "and the order of rows and columns.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#5d6677")
        left.addWidget(hint)
        left.addWidget(QtWidgets.QLabel("<b>Group rules</b>"))
        self.rules_group = RuleTable("Group")
        left.addWidget(self.rules_group, 2)
        left.addWidget(QtWidgets.QLabel("<b>Tag rules</b> (optional, e.g. byte lanes)"))
        self.rules_tag = RuleTable("Tag")
        left.addWidget(self.rules_tag, 1)
        lw = QtWidgets.QWidget()
        lw.setLayout(left)
        lay.addWidget(lw, 1)

        right = QtWidgets.QVBoxLayout()
        right.addWidget(QtWidgets.QLabel("<b>Nets</b> - edit Group or Tag to override the rules "
                                         "(bold = manual)"))
        self.table_nets = QtWidgets.QTableWidget(0, 4)
        self.table_nets.setHorizontalHeaderLabels(["Net", "Group", "Tag", "Ports (location per model)"])
        self.table_nets.verticalHeader().setVisible(False)
        self.table_nets.horizontalHeader().setStretchLastSection(True)
        for c, wdt in enumerate((150, 100, 90)):
            self.table_nets.setColumnWidth(c, wdt)
        right.addWidget(self.table_nets, 1)
        self.btn_clear_manual = QtWidgets.QPushButton("Clear manual group/tag edits")
        right.addWidget(self.btn_clear_manual)
        lay.addLayout(right, 1)

        for sig in (self.list_locs.model().rowsMoved, self.list_locs.model().rowsRemoved):
            sig.connect(lambda *a: QtCore.QTimer.singleShot(0, self._on_locs_moved))
        self.rules_group.changed.connect(self._on_rules)
        self.rules_tag.changed.connect(self._on_rules)
        self.table_nets.itemChanged.connect(self._on_net_edit)
        self.btn_clear_manual.clicked.connect(self._on_clear_manual)
        return w

    def _on_locs_moved(self, *_args) -> None:
        if self._loading:
            return
        order = [self.list_locs.item(i).text() for i in range(self.list_locs.count())]
        if sorted(order) != sorted(self.project.locations) or order == self.project.locations:
            return
        self.project.locations = [self.list_locs.item(i).text() for i in range(self.list_locs.count())]
        self.mark_stale()

    def _on_rules(self) -> None:
        if self._loading:
            return
        self.project.group_rules = self.rules_group.rules()
        self.project.tag_rules = self.rules_tag.rules()
        self.project._rx_cache.clear()
        self.mark_stale()
        self._fill_nets()

    def _on_net_edit(self, item) -> None:
        if self._loading or item.column() not in (1, 2):
            return
        net = self.table_nets.item(item.row(), 0).text()
        entry = self.project.nets.setdefault(net, {})
        value = item.text().strip()
        if item.column() == 1:
            if not value or value == self.project.rule_group(net):
                entry.pop("group", None)
            else:
                entry["group"] = value
        else:
            if value == self.project.rule_tag(net):
                entry.pop("tag", None)
            else:
                entry["tag"] = value
        if not entry:
            self.project.nets.pop(net, None)
        self.mark_stale()
        self._fill_nets()

    def _on_clear_manual(self) -> None:
        self.project.nets.clear()
        self.mark_stale()
        self._fill_nets()

    def _fill_nets_tab(self) -> None:
        self._loading = True
        self.list_locs.clear()
        for loc in self.project.locations:
            self.list_locs.addItem(loc)
        self.rules_group.load_rows([list(r) for r in self.project.group_rules])
        self.rules_tag.load_rows([list(r) for r in self.project.tag_rules])
        self._loading = False
        self._fill_nets()

    def _fill_nets(self) -> None:
        proj = self.project
        self._loading = True
        nets = proj.all_nets()
        where: Dict[str, List[str]] = {}
        for m in proj.models:
            per: Dict[str, List[str]] = {}
            for pm in m.ports:
                if pm.net:
                    per.setdefault(pm.net, []).append(pm.loc or "?")
            for net, locs in per.items():
                where.setdefault(net, []).append(f"{m.alias}: {', '.join(locs)}")
        self.table_nets.setRowCount(len(nets))
        bold = QtGui.QFont()
        bold.setBold(True)
        for r, net in enumerate(nets):
            self.table_nets.setItem(r, 0, ro_item(net))
            g = QtWidgets.QTableWidgetItem(proj.group_of(net))
            t = QtWidgets.QTableWidgetItem(proj.tag_of(net))
            if "group" in proj.nets.get(net, {}):
                g.setFont(bold)
            if "tag" in proj.nets.get(net, {}):
                t.setFont(bold)
            self.table_nets.setItem(r, 1, g)
            self.table_nets.setItem(r, 2, t)
            self.table_nets.setItem(r, 3, ro_item("   ".join(where.get(net, []))))
        self._loading = False

    # ==================================================================
    # tab 3: terminations
    # ==================================================================

    def _build_terms_tab(self) -> QtWidgets.QWidget:
        w = QtWidgets.QWidget()
        lay = QtWidgets.QVBoxLayout(w)
        lay.addWidget(QtWidgets.QLabel(
            "<b>Project-wide terminations.</b> Ports not matched stay <i>Signal</i> (analysed). "
            "Unmapped ports stay at Z0. Scenarios can override these on tab 4."))
        self.terms_global = TermTable()
        lay.addWidget(self.terms_global, 2)
        lay.addWidget(QtWidgets.QLabel("<b>Resolved</b>"))
        self.text_terms = QtWidgets.QPlainTextEdit()
        self.text_terms.setReadOnly(True)
        self.text_terms.setFont(QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.FixedFont))
        lay.addWidget(self.text_terms, 1)
        self.terms_global.changed.connect(self._on_global_terms)
        return w

    def _on_global_terms(self) -> None:
        if self._loading:
            return
        self.project.terminations = self.terms_global.rules()
        self.mark_stale()
        self._fill_term_summary()

    def _fill_terms_tab(self) -> None:
        self._loading = True
        self.terms_global.set_choices(self.project.all_groups() or ["POWER"], self.project.locations)
        self.terms_global.load_rows(self.project.terminations)
        self._loading = False
        self._fill_term_summary()

    def _fill_term_summary(self) -> None:
        proj = self.project
        lines = []
        for m in proj.models:
            counts: Dict[str, int] = {}
            loaded = []
            for i, pm in enumerate(m.ports):
                t = proj.resolve_term(pm.net, pm.loc) if pm.net and pm.loc else None
                kind = t.type if t else "z0"
                counts[kind] = counts.get(kind, 0) + 1
                if t and t.type not in ("signal",):
                    loaded.append(f"  port {i + 1:>3} {pm.net}@{pm.loc}: {t.describe()}")
            summary = ", ".join(f"{k} {v}" for k, v in sorted(counts.items()))
            lines.append(f"{m.alias}: {summary}")
            lines += loaded[:200]
            if len(loaded) > 200:
                lines.append(f"  ... {len(loaded) - 200} more")
        self.text_terms.setPlainText("\n".join(lines) or "No models.")

    # ==================================================================
    # tab 4: scenarios & speed
    # ==================================================================

    def _build_scen_tab(self) -> QtWidgets.QWidget:
        w = QtWidgets.QWidget()
        outer = QtWidgets.QVBoxLayout(w)
        lay = QtWidgets.QHBoxLayout()
        outer.addLayout(lay, 1)

        left = QtWidgets.QVBoxLayout()
        left.addWidget(QtWidgets.QLabel("<b>Scenarios</b>"))
        self.list_scen = QtWidgets.QListWidget()
        left.addWidget(self.list_scen, 1)
        grid = QtWidgets.QGridLayout()
        self.btn_scen_add = QtWidgets.QPushButton("Add")
        self.btn_scen_copy = QtWidgets.QPushButton("Copy")
        self.btn_scen_rename = QtWidgets.QPushButton("Rename")
        self.btn_scen_del = QtWidgets.QPushButton("Delete")
        self.btn_scen_lp5x = QtWidgets.QPushButton("LPDDR5X preset...")
        grid.addWidget(self.btn_scen_add, 0, 0)
        grid.addWidget(self.btn_scen_copy, 0, 1)
        grid.addWidget(self.btn_scen_rename, 1, 0)
        grid.addWidget(self.btn_scen_del, 1, 1)
        grid.addWidget(self.btn_scen_lp5x, 2, 0, 1, 2)
        left.addLayout(grid)
        lw = QtWidgets.QWidget()
        lw.setLayout(left)
        lw.setMaximumWidth(230)
        lay.addWidget(lw)

        mid = QtWidgets.QVBoxLayout()
        self.lbl_scen = QtWidgets.QLabel("<b>Directions</b>")
        mid.addWidget(self.lbl_scen)
        self.dirs = DirTable()
        mid.addWidget(self.dirs, 3)
        self.btn_dir_fill = small_button("One row per group", "Replace the rows with one per group")
        mid.addWidget(self.btn_dir_fill)
        mid.addWidget(QtWidgets.QLabel("<b>Termination overrides in this scenario</b> "
                                       "(e.g. the idle rank of a multi-drop bus)"))
        self.terms_scen = TermTable()
        mid.addWidget(self.terms_scen, 2)
        lay.addLayout(mid, 3)

        right = QtWidgets.QVBoxLayout()
        box = QtWidgets.QGroupBox("Speed -> Nyquist (NRZ: Gbps / 2)")
        v = QtWidgets.QVBoxLayout(box)
        row = QtWidgets.QHBoxLayout()
        row.addWidget(QtWidgets.QLabel("Default"))
        self.spin_gbps = QtWidgets.QDoubleSpinBox()
        self.spin_gbps.setRange(0.001, 1000.0)
        self.spin_gbps.setDecimals(4)
        self.spin_gbps.setSuffix(" Gbps")
        row.addWidget(self.spin_gbps)
        self.lbl_nyq = QtWidgets.QLabel("")
        row.addWidget(self.lbl_nyq)
        row.addStretch(1)
        v.addLayout(row)
        self.table_speed = QtWidgets.QTableWidget(0, 3)
        self.table_speed.setHorizontalHeaderLabels(["Victim group", "Gbps", "Nyq GHz"])
        self.table_speed.setToolTip("Leave Gbps blank to use the default speed.")
        self.table_speed.verticalHeader().setVisible(False)
        self.table_speed.horizontalHeader().setStretchLastSection(True)
        v.addWidget(self.table_speed)
        right.addWidget(box, 2)

        box = QtWidgets.QGroupBox("Aggressors counted in every power sum")
        v = QtWidgets.QVBoxLayout(box)
        self.combo_tag_mode = combo(["Every net", "Same tag as the victim only"], "all",
                                    data=["all", "same_tag"])
        v.addWidget(self.combo_tag_mode)
        self.check_aggr_groups = CheckList("Aggressor groups", height=160)
        v.addWidget(self.check_aggr_groups)
        right.addWidget(box, 1)
        rw = QtWidgets.QWidget()
        rw.setLayout(right)
        rw.setMaximumWidth(380)
        lay.addWidget(rw)

        row = QtWidgets.QHBoxLayout()
        self.btn_compute = QtWidgets.QPushButton("Compute")
        self.btn_compute.setMinimumHeight(32)
        row.addWidget(self.btn_compute)
        self.lbl_compute = QtWidgets.QLabel("")
        row.addWidget(self.lbl_compute, 1)
        outer.addLayout(row)
        self.text_log = QtWidgets.QPlainTextEdit()
        self.text_log.setReadOnly(True)
        self.text_log.setMaximumHeight(140)
        self.text_log.setFont(QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.FixedFont))
        outer.addWidget(self.text_log)

        self.list_scen.currentRowChanged.connect(self._on_scen_selected)
        self.btn_scen_add.clicked.connect(self.on_scen_add)
        self.btn_scen_copy.clicked.connect(self.on_scen_copy)
        self.btn_scen_rename.clicked.connect(self.on_scen_rename)
        self.btn_scen_del.clicked.connect(self.on_scen_delete)
        self.btn_scen_lp5x.clicked.connect(self.on_lp5x)
        self.btn_dir_fill.clicked.connect(self.on_dir_fill)
        self.dirs.changed.connect(self._on_dirs)
        self.terms_scen.changed.connect(self._on_scen_terms)
        self.spin_gbps.valueChanged.connect(self._on_speed)
        self.table_speed.itemChanged.connect(self._on_speed_table)
        self.combo_tag_mode.currentIndexChanged.connect(self._on_scope)
        self.check_aggr_groups.changed.connect(self._on_scope)
        self.btn_compute.clicked.connect(self.compute)
        return w

    def _cur_scen(self) -> Optional[Scenario]:
        r = self.list_scen.currentRow()
        return self.project.scenarios[r] if 0 <= r < len(self.project.scenarios) else None

    def _fill_scen_tab(self) -> None:
        proj = self.project
        self._loading = True
        cur = self.list_scen.currentRow()
        self.list_scen.blockSignals(True)
        self.list_scen.clear()
        for s in proj.scenarios:
            self.list_scen.addItem(s.name)
        self.list_scen.blockSignals(False)
        groups = proj.all_groups()
        self.dirs.set_choices(groups, proj.locations)
        self.terms_scen.set_choices(groups, proj.locations)
        self.spin_gbps.setValue(proj.default_gbps)
        self.lbl_nyq.setText(f"Nyquist {proj.default_gbps / 2:g} GHz")
        self.table_speed.setRowCount(len(groups))
        for r, g in enumerate(groups):
            self.table_speed.setItem(r, 0, ro_item(g))
            gb = proj.group_gbps.get(g)
            self.table_speed.setItem(r, 1, QtWidgets.QTableWidgetItem("" if gb is None else f"{gb:g}"))
            self.table_speed.setItem(r, 2, ro_item(f"{proj.gbps_of(g) / 2:g}"))
        set_combo(self.combo_tag_mode, proj.aggr_tag_mode)
        self.check_aggr_groups.set_items(groups, proj.aggr_groups or groups)
        self._loading = False
        if proj.scenarios:
            self.list_scen.setCurrentRow(min(max(cur, 0), len(proj.scenarios) - 1))
        self._on_scen_selected()

    def _on_scen_selected(self, *_args) -> None:
        s = self._cur_scen()
        self._loading = True
        enabled = s is not None
        self.dirs.setEnabled(enabled)
        self.terms_scen.setEnabled(enabled)
        self.btn_dir_fill.setEnabled(enabled)
        self.lbl_scen.setText(f"<b>Directions - {s.name}</b>" if s else
                              "<b>Directions</b> (add a scenario, or apply the LPDDR5X preset)")
        self.dirs.load_rows(s.directions if s else [])
        self.terms_scen.load_rows(s.terminations if s else [])
        self._loading = False

    def _scen_rows_per_group(self) -> List[DirRule]:
        locs = self.project.locations
        return [DirRule("group", g, locs[0] if locs else "", locs[1:2])
                for g in self.project.all_groups() if g not in ("POWER", "UNUSED")]

    def on_scen_add(self) -> None:
        name = self._ask_text("Add scenario", "Name (e.g. WRITE, READ, TX):")
        if not name:
            return
        if self.project.scenario(name):
            self._warn("Add scenario", f"{name} exists")
            return
        self.project.scenarios.append(Scenario(name, self._scen_rows_per_group()))
        self.mark_stale()
        self._fill_scen_tab()
        self.list_scen.setCurrentRow(len(self.project.scenarios) - 1)

    def on_scen_copy(self) -> None:
        s = self._cur_scen()
        if not s:
            return
        name = self._ask_text("Copy scenario", "New name:", s.name + "_copy")
        if not name or self.project.scenario(name):
            return
        c = copy.deepcopy(s)
        c.name = name
        self.project.scenarios.append(c)
        self.mark_stale()
        self._fill_scen_tab()
        self.list_scen.setCurrentRow(len(self.project.scenarios) - 1)

    def on_scen_rename(self) -> None:
        s = self._cur_scen()
        if not s:
            return
        name = self._ask_text("Rename scenario", "New name:", s.name)
        if not name or name == s.name or self.project.scenario(name):
            return
        old = s.name
        s.name = name
        if self.spec is not None:
            self.spec.scens = [name if x == old else x for x in self.spec.scens]
        self.mark_stale()
        self._fill_scen_tab()

    def on_scen_delete(self) -> None:
        s = self._cur_scen()
        if not s:
            return
        self.project.scenarios.remove(s)
        self.mark_stale()
        self._fill_scen_tab()

    def on_dir_fill(self) -> None:
        s = self._cur_scen()
        if not s:
            return
        s.directions = self._scen_rows_per_group()
        self.mark_stale()
        self._on_scen_selected()

    def _on_dirs(self) -> None:
        s = self._cur_scen()
        if self._loading or not s:
            return
        s.directions = self.dirs.rules()
        self.mark_stale()

    def _on_scen_terms(self) -> None:
        s = self._cur_scen()
        if self._loading or not s:
            return
        s.terminations = self.terms_scen.rules()
        self.mark_stale()

    def _on_speed(self, value: float) -> None:
        if self._loading:
            return
        self.project.default_gbps = float(value)
        self.lbl_nyq.setText(f"Nyquist {value / 2:g} GHz")
        self._refresh_speed_nyq()
        self.mark_stale()

    def _on_speed_table(self, item) -> None:
        if self._loading or item.column() != 1:
            return
        g = self.table_speed.item(item.row(), 0).text()
        text = item.text().strip()
        if not text:
            self.project.group_gbps.pop(g, None)
        else:
            try:
                self.project.group_gbps[g] = float(text)
            except ValueError:
                self._warn("Speed", f"{text!r} is not a number")
                return
        self._refresh_speed_nyq()
        self.mark_stale()

    def _refresh_speed_nyq(self) -> None:
        self._loading = True
        for r in range(self.table_speed.rowCount()):
            g = self.table_speed.item(r, 0).text()
            self.table_speed.setItem(r, 2, ro_item(f"{self.project.gbps_of(g) / 2:g}"))
        self._loading = False

    def _on_scope(self, *_args) -> None:
        if self._loading:
            return
        self.project.aggr_tag_mode = combo_value(self.combo_tag_mode)
        checked = self.check_aggr_groups.checked()
        self.project.aggr_groups = [] if len(checked) == len(self.check_aggr_groups.values()) else checked
        self.mark_stale()

    # ==================================================================
    # compute
    # ==================================================================

    def compute(self) -> bool:
        if not self.project.models:
            self._warn("Compute", "Add at least one model first.")
            return False
        self._busy(True)
        try:
            log = []
            self.analysis = Analysis(self.project, self.networks).build(
                progress=lambda m: (self._status(m), QtWidgets.QApplication.processEvents()))
            log += self.analysis.messages
        except Exception as exc:  # show anything unexpected instead of dying
            self._busy(False)
            self._warn("Compute failed", f"{exc}\n\n{traceback.format_exc()}")
            return False
        self._busy(False)
        n = sum(len(m.items) for m in self.analysis.models.values())
        self.stale = False
        self.lbl_compute.setText(f"Computed {n} curves over {len(self.analysis.models)} model(s).")
        self._status(f"computed {n} curves")
        self.text_log.setPlainText("\n".join(log) if log else "No warnings.")
        self._fill_select_facets()
        self.apply_filter()
        return True

    def _ensure_computed(self) -> bool:
        if self.analysis is None or self.stale:
            return self.compute()
        return True

    def _on_tab(self, index: int) -> None:
        if index == 0:
            self._fill_ports()
        elif index == 1:
            self._fill_nets_tab()
        elif index == 2:
            self._fill_terms_tab()
        elif index == 3:
            self._fill_scen_tab()
        elif index in (4, 5):
            if self.project.models and (self.analysis is None or self.stale):
                self.compute()

    # ==================================================================
    # tab 5: select
    # ==================================================================

    def _build_select_tab(self) -> QtWidgets.QWidget:
        w = QtWidgets.QWidget()
        lay = QtWidgets.QVBoxLayout()

        facets = QtWidgets.QHBoxLayout()
        self.f_models = CheckList("Models", height=110)
        self.f_scens = CheckList("Scenario", height=110)
        self.f_kinds = CheckList("Metric", height=110)
        self.f_locs = CheckList("Observed at", height=110)
        self.f_vgroups = CheckList("Victim group", height=110)
        self.f_vtags = CheckList("Victim tag", height=110)
        for f in (self.f_models, self.f_scens, self.f_kinds, self.f_locs, self.f_vgroups, self.f_vtags):
            facets.addWidget(f)
            f.changed.connect(self._facet_changed)
        lay.addLayout(facets)

        row = QtWidgets.QHBoxLayout()
        row.addWidget(QtWidgets.QLabel("Victim name"))
        self.edit_vpat = QtWidgets.QLineEdit()
        self.edit_vpat.setPlaceholderText("DQ[0-3], RDQS*   (glob, comma separated)   or   re:^DQ\\d$")
        row.addWidget(self.edit_vpat, 1)
        lay.addLayout(row)

        box = QtWidgets.QGroupBox("Aggressors - for NEXT / FEXT / scenario XT (per-aggressor curves)")
        h = QtWidgets.QHBoxLayout(box)
        v = QtWidgets.QVBoxLayout()
        self.combo_amode = combo([A_MODE_LABELS[m] for m in A_MODES], "topk", data=list(A_MODES))
        v.addWidget(self.combo_amode)
        r2 = QtWidgets.QHBoxLayout()
        r2.addWidget(QtWidgets.QLabel("K"))
        self.spin_k = QtWidgets.QSpinBox()
        self.spin_k.setRange(1, 500)
        self.spin_k.setValue(3)
        r2.addWidget(self.spin_k)
        r2.addWidget(QtWidgets.QLabel("Threshold"))
        self.spin_thr = QtWidgets.QDoubleSpinBox()
        self.spin_thr.setRange(-300, 50)
        self.spin_thr.setValue(-40)
        self.spin_thr.setSuffix(" dB")
        r2.addWidget(self.spin_thr)
        v.addLayout(r2)
        r3 = QtWidgets.QHBoxLayout()
        r3.addWidget(QtWidgets.QLabel("Name"))
        self.edit_apat = QtWidgets.QLineEdit()
        self.edit_apat.setPlaceholderText("glob / re:")
        r3.addWidget(self.edit_apat)
        v.addLayout(r3)
        hint = QtWidgets.QLabel("Ranked by the value at the victim's Nyquist (worst over the selected models).")
        hint.setStyleSheet("color:#5d6677")
        hint.setWordWrap(True)
        v.addWidget(hint)
        h.addLayout(v, 1)
        self.f_agroups = CheckList("Aggressor group", height=110)
        self.f_agroups.changed.connect(self._facet_changed)
        h.addWidget(self.f_agroups, 1)
        lay.addWidget(box)

        row = QtWidgets.QHBoxLayout()
        self.lbl_count = QtWidgets.QLabel("")
        self.lbl_count.setStyleSheet("font-weight:600")
        row.addWidget(self.lbl_count, 1)
        self.btn_clear_picks = small_button("Clear manual picks", "Forget rows unticked in the preview "
                                            "and cells picked in the matrix")
        row.addWidget(self.btn_clear_picks)
        row.addWidget(QtWidgets.QLabel("Preset"))
        self.combo_preset = QtWidgets.QComboBox()
        self.combo_preset.setMinimumWidth(160)
        row.addWidget(self.combo_preset)
        self.btn_preset_load = small_button("Load")
        self.btn_preset_save = small_button("Save as...")
        self.btn_preset_del = small_button("Delete")
        for b in (self.btn_preset_load, self.btn_preset_save, self.btn_preset_del):
            row.addWidget(b)
        lay.addLayout(row)

        self.sel_tabs = QtWidgets.QTabWidget()
        self.sel_tabs.addTab(self._build_preview(), "Preview")
        self.sel_tabs.addTab(self._build_matrix(), "Crosstalk matrix")
        self.sel_tabs.addTab(self._build_breakdown(), "Scenario breakdown")
        top = QtWidgets.QWidget()
        top.setLayout(lay)
        split = QtWidgets.QSplitter(QtCore.Qt.Vertical)
        split.addWidget(top)
        split.addWidget(self.sel_tabs)
        split.setStretchFactor(1, 1)
        outer = QtWidgets.QVBoxLayout(w)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(split)

        self.edit_vpat.textChanged.connect(self._facet_changed)
        self.edit_apat.textChanged.connect(self._facet_changed)
        self.combo_amode.currentIndexChanged.connect(self._facet_changed)
        self.spin_k.valueChanged.connect(self._facet_changed)
        self.spin_thr.valueChanged.connect(self._facet_changed)
        self.btn_clear_picks.clicked.connect(self._clear_picks)
        self.btn_preset_load.clicked.connect(self.on_preset_load)
        self.btn_preset_save.clicked.connect(self.on_preset_save)
        self.btn_preset_del.clicked.connect(self.on_preset_delete)
        return w

    def _build_preview(self) -> QtWidgets.QWidget:
        w = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(w)
        self.lbl_preview = QtWidgets.QLabel("Untick a row to drop that curve. Click a header to sort.")
        self.lbl_preview.setStyleSheet("color:#5d6677")
        v.addWidget(self.lbl_preview)
        self.table_preview = QtWidgets.QTableWidget(0, 3)
        self.table_preview.verticalHeader().setVisible(False)
        self.table_preview.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.table_preview.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.table_preview.itemChanged.connect(self._on_preview_check)
        v.addWidget(self.table_preview, 1)
        return w

    def _build_matrix(self) -> QtWidgets.QWidget:
        w = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(w)
        row = QtWidgets.QHBoxLayout()
        self.mx_model = QtWidgets.QComboBox()
        self.mx_scen = QtWidgets.QComboBox()
        self.mx_kind = QtWidgets.QComboBox()
        self.mx_obs = QtWidgets.QComboBox()
        self.mx_src = QtWidgets.QComboBox()
        self.mx_lo = QtWidgets.QDoubleSpinBox()
        self.mx_hi = QtWidgets.QDoubleSpinBox()
        for s, val in ((self.mx_lo, -80.0), (self.mx_hi, -20.0)):
            s.setRange(-300, 50)
            s.setValue(val)
            s.setSuffix(" dB")
        for label, wid in (("Model", self.mx_model), ("Scenario", self.mx_scen), ("Kind", self.mx_kind),
                           ("Observed at", self.mx_obs), ("Driven at", self.mx_src),
                           ("White", self.mx_lo), ("Red", self.mx_hi)):
            row.addWidget(QtWidgets.QLabel(label))
            row.addWidget(wid)
        self.btn_mx_auto = small_button("Auto range")
        row.addWidget(self.btn_mx_auto)
        row.addStretch(1)
        v.addLayout(row)
        hint = QtWidgets.QLabel("Rows = victims, columns = aggressors; dB at the victim's Nyquist. "
                                "Tick a cell to add that curve (in every selected model); click a "
                                "row / column header to toggle the whole row / column.")
        hint.setStyleSheet("color:#5d6677")
        hint.setWordWrap(True)
        v.addWidget(hint)
        self.table_mx = QtWidgets.QTableWidget(0, 0)
        self.table_mx.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.table_mx.setSelectionMode(QtWidgets.QAbstractItemView.NoSelection)
        self.table_mx.horizontalHeader().setDefaultSectionSize(74)
        self.table_mx.verticalHeader().setDefaultSectionSize(22)
        self.table_mx.horizontalHeader().setSectionsClickable(True)
        self.table_mx.verticalHeader().setSectionsClickable(True)
        v.addWidget(self.table_mx, 1)
        self.mx_model.currentIndexChanged.connect(self._mx_scen_changed)
        self.mx_scen.currentIndexChanged.connect(self._mx_scen_changed)
        self.mx_kind.currentIndexChanged.connect(self._mx_obs_changed)
        self.mx_obs.currentIndexChanged.connect(self._mx_obs_changed)
        self.mx_src.currentIndexChanged.connect(self.draw_matrix)
        self.mx_lo.valueChanged.connect(self._mx_recolour)
        self.mx_hi.valueChanged.connect(self._mx_recolour)
        self.btn_mx_auto.clicked.connect(self._mx_auto_range)
        self.table_mx.itemChanged.connect(self._on_mx_cell)
        self.table_mx.horizontalHeader().sectionClicked.connect(lambda c: self._mx_toggle(col=c))
        self.table_mx.verticalHeader().sectionClicked.connect(lambda r: self._mx_toggle(row=r))
        return w

    def _build_breakdown(self) -> QtWidgets.QWidget:
        w = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(w)
        row = QtWidgets.QHBoxLayout()
        self.bd_model = QtWidgets.QComboBox()
        self.bd_scen = QtWidgets.QComboBox()
        self.bd_victim = QtWidgets.QComboBox()
        self.bd_victim.setMinimumWidth(110)
        self.bd_obs = QtWidgets.QComboBox()
        self.bd_group = QtWidgets.QCheckBox("Group by aggressor group")
        self.bd_freq = QtWidgets.QLineEdit()
        self.bd_freq.setPlaceholderText("Nyquist")
        self.bd_freq.setMaximumWidth(90)
        for label, wid in (("Model", self.bd_model), ("Scenario", self.bd_scen),
                           ("Victim", self.bd_victim), ("Observed at", self.bd_obs)):
            row.addWidget(QtWidgets.QLabel(label))
            row.addWidget(wid)
        row.addWidget(self.bd_group)
        row.addWidget(QtWidgets.QLabel("at"))
        row.addWidget(self.bd_freq)
        row.addWidget(QtWidgets.QLabel("GHz"))
        row.addStretch(1)
        v.addLayout(row)
        self.lbl_bd = QtWidgets.QLabel("")
        self.lbl_bd.setStyleSheet("font-weight:600")
        v.addWidget(self.lbl_bd)
        self.table_bd = QtWidgets.QTableWidget(0, 7)
        self.table_bd.setHorizontalHeaderLabels(["#", "Aggressor", "Group", "Type", "dB", "Share", "Cumulative"])
        self.table_bd.verticalHeader().setVisible(False)
        self.table_bd.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.table_bd.horizontalHeader().setStretchLastSection(True)
        v.addWidget(self.table_bd, 1)
        row = QtWidgets.QHBoxLayout()
        self.bd_k = QtWidgets.QSpinBox()
        self.bd_k.setRange(1, 200)
        self.bd_k.setValue(3)
        self.btn_bd_topk = QtWidgets.QPushButton("Add top K curves + PSXT to selection")
        self.btn_bd_checked = QtWidgets.QPushButton("Add ticked rows to selection")
        row.addWidget(QtWidgets.QLabel("K"))
        row.addWidget(self.bd_k)
        row.addWidget(self.btn_bd_topk)
        row.addWidget(self.btn_bd_checked)
        row.addStretch(1)
        v.addLayout(row)
        self.bd_model.currentIndexChanged.connect(self._bd_scen_changed)
        self.bd_scen.currentIndexChanged.connect(self._bd_scen_changed)
        self.bd_victim.currentIndexChanged.connect(self._bd_victim_changed)
        self.bd_obs.currentIndexChanged.connect(self.draw_breakdown)
        self.bd_group.toggled.connect(self.draw_breakdown)
        self.bd_freq.editingFinished.connect(self.draw_breakdown)
        self.btn_bd_topk.clicked.connect(self._bd_add_topk)
        self.btn_bd_checked.clicked.connect(self._bd_add_checked)
        return w

    # -- facets --------------------------------------------------------

    def _facet_values(self) -> Dict[str, tuple]:
        """(values, labels) per facet for the current analysis."""
        a, proj = self.analysis, self.project
        groups = a.victim_groups()
        return {
            "models": (list(a.models), None),
            "scens": ([""] + [s.name for s in proj.scenarios], {"": "(basic)"}),
            "kinds": (list(ALL_KINDS), KIND_LABELS),
            "locs": (list(proj.locations), None),
            "v_groups": (groups, None),
            "v_tags": (proj.all_tags() + [NO_TAG], None),
            "a_groups": (groups, None),
        }

    def _facet_widgets(self) -> Dict[str, CheckList]:
        return {"models": self.f_models, "scens": self.f_scens, "kinds": self.f_kinds,
                "locs": self.f_locs, "v_groups": self.f_vgroups, "v_tags": self.f_vtags,
                "a_groups": self.f_agroups}

    def _fill_select_facets(self) -> None:
        if self.analysis is None:
            return
        if self.spec is None:
            self.spec = default_spec(self.analysis)
        values = self._facet_values()
        self._loading = True
        for name, wid in self._facet_widgets().items():
            vals, labels = values[name]
            seen = self._universe.get(name)
            current = getattr(self.spec, name)
            # values new since last time start ticked (except metrics)
            new = [] if (seen is None or name == "kinds") else [x for x in vals if x not in seen]
            checked = [x for x in vals if x in current] + new
            wid.set_items(vals, checked, labels)
            self._universe[name] = set(vals)
        self.edit_vpat.setText(self.spec.v_pattern)
        self.edit_apat.setText(self.spec.a_pattern)
        set_combo(self.combo_amode, self.spec.a_mode)
        self.spin_k.setValue(self.spec.a_k)
        self.spin_thr.setValue(self.spec.a_threshold)
        self._loading = False
        self._fill_presets()
        self._fill_matrix_controls()
        self._fill_breakdown_controls()

    def _spec_from_widgets(self) -> FilterSpec:
        old = self.spec or FilterSpec()
        return FilterSpec(
            models=self.f_models.checked(), scens=self.f_scens.checked(),
            kinds=self.f_kinds.checked(), locs=self.f_locs.checked(),
            v_groups=self.f_vgroups.checked(), v_tags=self.f_vtags.checked(),
            v_pattern=self.edit_vpat.text().strip(),
            a_mode=combo_value(self.combo_amode), a_k=self.spin_k.value(),
            a_threshold=self.spin_thr.value(), a_groups=self.f_agroups.checked(),
            a_pattern=self.edit_apat.text().strip(), add=list(old.add), remove=list(old.remove),
        )

    def _facet_changed(self, *_args) -> None:
        if not self._loading:
            self._sel_timer.start()

    def apply_filter(self) -> None:
        if self.analysis is None:
            return
        self.spec = self._spec_from_widgets()
        try:
            self.selected = run_filter(self.analysis, self.spec)
        except Exception as exc:  # a bad regex in a name filter
            self.lbl_count.setText(f"Filter error: {exc}")
            return
        self._fill_preview()
        if self.sel_tabs.currentIndex() == 1:
            self._mx_sync_checks()

    def _models_in_view(self) -> List[str]:
        return [m for m in (self.spec.models if self.spec else []) if m in self.analysis.models]

    def _fill_preview(self) -> None:
        models = self._models_in_view()
        rows = preview(self.analysis, self.selected, models)
        scen_order = {"": -1}
        scen_order.update({s.name: i for i, s in enumerate(self.project.scenarios)})
        kinds = list(ALL_KINDS)

        def order(r):
            sev = r.worst if r.key.kind == "IL" else -r.worst
            return (scen_order.get(r.key.scen, 99), kinds.index(r.key.kind),
                    math.isnan(r.worst), 0.0 if math.isnan(sev) else sev)

        rows.sort(key=order)
        shown = rows[:PREVIEW_LIMIT]
        t = self.table_preview
        self._loading = True
        t.setSortingEnabled(False)
        t.clear()
        headers = ["Curve", "Nyq GHz"] + [f"{m} dB" for m in models] + ["Worst dB"]
        t.setColumnCount(len(headers))
        t.setHorizontalHeaderLabels(headers)
        t.setRowCount(len(shown))
        for r, row in enumerate(shown):
            it = QtWidgets.QTableWidgetItem(row.label)
            it.setFlags(it.flags() | QtCore.Qt.ItemIsUserCheckable)
            it.setCheckState(QtCore.Qt.Checked)
            it.setData(QtCore.Qt.UserRole, list(row.key))
            t.setItem(r, 0, it)
            t.setItem(r, 1, NumItem(row.nyquist_ghz, f"{row.nyquist_ghz:g}"))
            for c, m in enumerate(models, 2):
                t.setItem(r, c, NumItem(row.values.get(m, float("nan"))))
            t.setItem(r, len(headers) - 1, NumItem(row.worst))
        t.setColumnWidth(0, 380)
        t.horizontalHeader().setSortIndicator(-1, QtCore.Qt.AscendingOrder)
        t.setSortingEnabled(True)
        self._loading = False
        extra = f" (preview shows the first {PREVIEW_LIMIT})" if len(self.selected) > PREVIEW_LIMIT else ""
        self.lbl_count.setText(f"{len(self.selected)} curve(s) selected x {len(models)} model(s){extra}")

    def _on_preview_check(self, item) -> None:
        if self._loading or item.column() != 0 or self.spec is None:
            return
        key = Key(*item.data(QtCore.Qt.UserRole))
        if item.checkState() == QtCore.Qt.Checked:
            self.spec.add_key(key)
            if key not in self.selected:
                self.selected.append(key)
        else:
            self.spec.remove_key(key)
            if key in self.selected:
                self.selected.remove(key)
        self.lbl_count.setText(f"{len(self.selected)} curve(s) selected x {len(self._models_in_view())} model(s)")

    def _clear_picks(self) -> None:
        if self.spec is not None:
            self.spec.add, self.spec.remove = [], []
            self.apply_filter()

    # -- presets -------------------------------------------------------

    def _fill_presets(self) -> None:
        self.combo_preset.clear()
        for name in sorted(n for n in self.project.presets if n != "_last"):
            self.combo_preset.addItem(name)

    def on_preset_save(self) -> None:
        if self.spec is None:
            return
        name = self._ask_text("Save selection preset", "Preset name:", self.combo_preset.currentText())
        if not name or name == "_last":
            return
        self.project.presets[name] = self._spec_from_widgets().to_dict()
        self._fill_presets()
        set_combo(self.combo_preset, name)
        self._status(f"preset {name} saved in the project (save the project to keep it)")

    def on_preset_load(self) -> None:
        name = self.combo_preset.currentText()
        if not name or name not in self.project.presets or self.analysis is None:
            return
        self.spec = FilterSpec.from_dict(self.project.presets[name])
        self._universe = {}
        self._fill_select_facets()
        self.apply_filter()

    def on_preset_delete(self) -> None:
        name = self.combo_preset.currentText()
        if name in self.project.presets:
            del self.project.presets[name]
            self._fill_presets()

    # -- matrix --------------------------------------------------------

    def _fill_matrix_controls(self) -> None:
        a = self.analysis
        self._loading = True
        cur_model = self.mx_model.currentText()
        self.mx_model.clear()
        self.mx_model.addItems(list(a.models))
        set_combo(self.mx_model, cur_model)
        cur = self.mx_scen.currentData()
        self.mx_scen.clear()
        self.mx_scen.addItem("(basic)", "")
        for s in self.project.scenarios:
            self.mx_scen.addItem(s.name, s.name)
        if cur is not None:
            set_combo(self.mx_scen, cur)
        self._loading = False
        self._mx_scen_changed()

    def _mx_scen_changed(self, *_args) -> None:
        if self._loading:
            return
        self._loading = True
        scen = self.mx_scen.currentData() or ""
        kind = self.mx_kind.currentText()
        self.mx_kind.clear()
        self.mx_kind.addItems(["XT"] if scen else ["NEXT", "FEXT"])
        set_combo(self.mx_kind, kind)
        obs = self.mx_obs.currentText()
        self.mx_obs.clear()
        self.mx_obs.addItems(self.project.locations)
        set_combo(self.mx_obs, obs)
        self._loading = False
        self._mx_obs_changed()

    def _mx_obs_changed(self, *_args) -> None:
        if self._loading or self.analysis is None:
            return
        self._loading = True
        kind = self.mx_kind.currentText()
        srcs = matrix_sources(self.analysis, self.mx_model.currentText(), self.mx_scen.currentData() or "",
                              kind, self.mx_obs.currentText()) if kind == "FEXT" else []
        cur = self.mx_src.currentText()
        self.mx_src.clear()
        self.mx_src.addItems(srcs or ["-"])
        set_combo(self.mx_src, cur)
        self.mx_src.setEnabled(bool(srcs))
        self._loading = False
        self.draw_matrix()

    def draw_matrix(self, *_args) -> None:
        if self._loading or self.analysis is None:
            return
        kind = self.mx_kind.currentText()
        src = self.mx_src.currentText() if self.mx_src.isEnabled() else ""
        self._matrix = matrix(self.analysis, self.mx_model.currentText(),
                              self.mx_scen.currentData() or "", kind, self.mx_obs.currentText(),
                              src if src != "-" else "")
        mx = self._matrix
        t = self.table_mx
        self._loading = True
        t.clear()
        t.setRowCount(len(mx.victims))
        t.setColumnCount(len(mx.aggressors))
        t.setHorizontalHeaderLabels(mx.aggressors)
        t.setVerticalHeaderLabels(mx.victims)
        chosen = set(self.selected)
        for r, _v in enumerate(mx.victims):
            for c, _a in enumerate(mx.aggressors):
                key = mx.keys[r][c]
                val = mx.values[r][c]
                if key is None:
                    it = QtWidgets.QTableWidgetItem("")
                    it.setFlags(QtCore.Qt.NoItemFlags)
                    it.setBackground(QtGui.QColor("#e8e8e8"))
                else:
                    it = QtWidgets.QTableWidgetItem("" if math.isnan(val) else f"{val:.0f}")
                    it.setFlags(QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsUserCheckable)
                    it.setCheckState(QtCore.Qt.Checked if key in chosen else QtCore.Qt.Unchecked)
                    it.setToolTip(f"{self.analysis.label(key)}\n{val:.2f} dB at "
                                  f"{self.analysis.nyquist_hz(key) / 1e9:g} GHz")
                t.setItem(r, c, it)
        self._loading = False
        self._mx_recolour()

    def _mx_auto_range(self) -> None:
        if not self._matrix:
            return
        vals = sorted(v for row in self._matrix.values for v in row if not math.isnan(v) and v > -250)
        if not vals:
            return
        hi = round(vals[-1])
        lo = max(round(vals[int(0.1 * (len(vals) - 1))]), hi - 60)
        self.mx_hi.setValue(hi)
        self.mx_lo.setValue(lo)

    def _mx_recolour(self, *_args) -> None:
        mx = self._matrix
        if not mx:
            return
        lo, hi = self.mx_lo.value(), self.mx_hi.value()
        self._loading = True
        for r in range(len(mx.victims)):
            for c in range(len(mx.aggressors)):
                if mx.keys[r][c] is not None:
                    item = self.table_mx.item(r, c)
                    item.setBackground(heat_colour(mx.values[r][c], lo, hi))
        self._loading = False

    def _mx_sync_checks(self) -> None:
        mx = self._matrix
        if not mx:
            return
        chosen = set(self.selected)
        self._loading = True
        for r in range(len(mx.victims)):
            for c in range(len(mx.aggressors)):
                key = mx.keys[r][c]
                if key is not None:
                    self.table_mx.item(r, c).setCheckState(
                        QtCore.Qt.Checked if key in chosen else QtCore.Qt.Unchecked)
        self._loading = False

    def _on_mx_cell(self, item) -> None:
        if self._loading or not self._matrix or self.spec is None:
            return
        key = self._matrix.keys[item.row()][item.column()]
        if key is None:
            return
        if item.checkState() == QtCore.Qt.Checked:
            self.spec.add_key(key)
        else:
            self.spec.remove_key(key)
        self.apply_filter()

    def _mx_toggle(self, row: Optional[int] = None, col: Optional[int] = None) -> None:
        mx = self._matrix
        if not mx or self.spec is None:
            return
        cells = ([mx.keys[row][c] for c in range(len(mx.aggressors))] if row is not None
                 else [mx.keys[r][col] for r in range(len(mx.victims))])
        cells = [k for k in cells if k is not None]
        chosen = set(self.selected)
        turn_on = not all(k in chosen for k in cells)
        for k in cells:
            (self.spec.add_key if turn_on else self.spec.remove_key)(k)
        self.apply_filter()
        self._mx_sync_checks()

    # -- breakdown -----------------------------------------------------

    def _fill_breakdown_controls(self) -> None:
        a = self.analysis
        self._loading = True
        cur_model = self.bd_model.currentText()
        self.bd_model.clear()
        self.bd_model.addItems(list(a.models))
        set_combo(self.bd_model, cur_model)
        cur = self.bd_scen.currentText()
        self.bd_scen.clear()
        self.bd_scen.addItems([s.name for s in self.project.scenarios])
        set_combo(self.bd_scen, cur)
        self._loading = False
        self._bd_scen_changed()

    def _bd_scen_changed(self, *_args) -> None:
        if self._loading or self.analysis is None:
            return
        md = self.analysis.models.get(self.bd_model.currentText())
        roles = md.roles.get(self.bd_scen.currentText(), {}) if md else {}
        from ..select import net_sort_key

        victims = sorted((v for v, r in roles.items() if r[2]), key=net_sort_key(self.analysis))
        self._loading = True
        cur = self.bd_victim.currentText()
        self.bd_victim.clear()
        self.bd_victim.addItems(victims)
        set_combo(self.bd_victim, cur)
        self._loading = False
        self._bd_victim_changed()

    def _bd_victim_changed(self, *_args) -> None:
        if self._loading or self.analysis is None:
            return
        md = self.analysis.models.get(self.bd_model.currentText())
        roles = md.roles.get(self.bd_scen.currentText(), {}) if md else {}
        role = roles.get(self.bd_victim.currentText())
        self._loading = True
        cur = self.bd_obs.currentText()
        self.bd_obs.clear()
        if role:
            self.bd_obs.addItems([loc for loc, _ in role[2]])
        set_combo(self.bd_obs, cur)
        self._loading = False
        self.draw_breakdown()

    def draw_breakdown(self, *_args) -> None:
        if self._loading or self.analysis is None:
            return
        t = self.table_bd
        t.setRowCount(0)
        self._bd_rows = []
        victim, obs = self.bd_victim.currentText(), self.bd_obs.currentText()
        if not victim or not obs:
            self.lbl_bd.setText("Pick a scenario victim.")
            return
        f = None
        text = self.bd_freq.text().strip()
        if text:
            try:
                f = parse_freq_list(text)[0]
            except (ValueError, IndexError):
                self.lbl_bd.setText(f"Bad frequency {text!r}")
                return
        grouped = self.bd_group.isChecked()
        rows, total, f_used = breakdown(self.analysis, self.bd_model.currentText(), self.bd_scen.currentText(),
                                        victim, obs, by_group=grouped, freq_hz=f)
        self._bd_rows = rows
        t.setHorizontalHeaderLabels(["#", "Group" if grouped else "Aggressor", "Group" if not grouped else "Members",
                                     "Type", "dB", "Share", "Cumulative"])
        t.setRowCount(len(rows))
        for r, row in enumerate(rows):
            it = QtWidgets.QTableWidgetItem(str(row.rank))
            it.setFlags(QtCore.Qt.ItemIsEnabled | QtCore.Qt.ItemIsUserCheckable)
            it.setCheckState(QtCore.Qt.Unchecked)
            t.setItem(r, 0, it)
            t.setItem(r, 1, ro_item(row.name))
            t.setItem(r, 2, ro_item(str(row.members) if grouped else row.group))
            t.setItem(r, 3, ro_item(row.xtype))
            t.setItem(r, 4, NumItem(row.db))
            bar = QtWidgets.QProgressBar()
            bar.setRange(0, 1000)
            bar.setValue(int(round(row.share * 1000)))
            bar.setFormat(f"{row.share * 100:.1f} %")
            t.setCellWidget(r, 5, bar)
            t.setItem(r, 6, NumItem(row.cumulative * 100, f"{row.cumulative * 100:.1f} %"))
        t.setColumnWidth(1, 140)
        t.setColumnWidth(5, 180)
        top = rows[0].share * 100 if rows else 0
        n3 = rows[min(2, len(rows) - 1)].cumulative * 100 if rows else 0
        self.lbl_bd.setText(
            f"{self.bd_scen.currentText()} PSXT@{obs} | {victim} at {f_used / 1e9:g} GHz: "
            f"{total:.2f} dB from {len(rows)} {'group(s)' if grouped else 'aggressor(s)'}"
            + (f";  largest {top:.1f} %, top 3 {n3:.1f} %" if rows else ""))

    def _bd_keys(self, rows) -> List[Key]:
        scen, victim, obs = self.bd_scen.currentText(), self.bd_victim.currentText(), self.bd_obs.currentText()
        keys = [Key(scen, "PSXT", obs, victim)]
        for row in rows:
            if self.bd_group.isChecked():
                keys.append(Key(scen, "PSXT_GRP", obs, victim, row.name))
            else:
                keys += row.keys
        return keys

    def _bd_add_topk(self) -> None:
        self._bd_add(self._bd_keys(self._bd_rows[: self.bd_k.value()]))

    def _bd_add_checked(self) -> None:
        rows = [row for r, row in enumerate(self._bd_rows)
                if self.table_bd.item(r, 0).checkState() == QtCore.Qt.Checked]
        self._bd_add(self._bd_keys(rows))

    def _bd_add(self, keys: List[Key]) -> None:
        if self.spec is None:
            return
        for k in keys:
            self.spec.add_key(k)
        self.apply_filter()
        self._status(f"added {len(keys)} curve(s) to the selection")

    # ==================================================================
    # tab 6: output
    # ==================================================================

    def _build_output_tab(self) -> QtWidgets.QWidget:
        w = QtWidgets.QWidget()
        lay = QtWidgets.QVBoxLayout(w)
        box = QtWidgets.QGroupBox("HTML plot report")
        g = QtWidgets.QGridLayout(box)
        g.addWidget(QtWidgets.QLabel("Separate graphs per"), 0, 0)
        row = QtWidgets.QHBoxLayout()
        self.split_checks = {}
        for d in export.SPLIT_DIMS:
            cb = QtWidgets.QCheckBox(export.SPLIT_LABELS[d])
            cb.setChecked(d == "metric")
            self.split_checks[d] = cb
            row.addWidget(cb)
        row.addWidget(QtWidgets.QLabel("   (unticked dimensions are overlaid; models differ by line style, "
                                       "curves by colour)"))
        row.addStretch(1)
        g.addLayout(row, 0, 1, 1, 3)
        g.addWidget(QtWidgets.QLabel("Title"), 1, 0)
        self.edit_title = QtWidgets.QLineEdit("Channel S-parameter report")
        g.addWidget(self.edit_title, 1, 1, 1, 3)
        g.addWidget(QtWidgets.QLabel("File"), 2, 0)
        self.edit_html = QtWidgets.QLineEdit(os.path.join(os.getcwd(), "chanxt_report.html"))
        g.addWidget(self.edit_html, 2, 1, 1, 2)
        self.btn_html_browse = small_button("...")
        g.addWidget(self.btn_html_browse, 2, 3)
        self.check_open = QtWidgets.QCheckBox("Open in the browser after saving")
        self.check_open.setChecked(True)
        g.addWidget(self.check_open, 3, 1)
        self.btn_html = QtWidgets.QPushButton("Save HTML report")
        self.btn_html.setMinimumHeight(30)
        g.addWidget(self.btn_html, 3, 2, 1, 2)
        lay.addWidget(box)

        box = QtWidgets.QGroupBox("Values at frequencies (linear interpolation of the dB curve)")
        v = QtWidgets.QVBoxLayout(box)
        row = QtWidgets.QHBoxLayout()
        row.addWidget(QtWidgets.QLabel("Frequencies"))
        self.edit_freqs = QtWidgets.QLineEdit()
        self.edit_freqs.setPlaceholderText("2.67, 5.33, 8   (GHz unless a unit is given: 800MHz)")
        row.addWidget(self.edit_freqs, 1)
        self.check_nyq = QtWidgets.QCheckBox("Each curve's Nyquist")
        self.check_nyq.setChecked(True)
        row.addWidget(self.check_nyq)
        self.btn_extract = QtWidgets.QPushButton("Extract")
        self.btn_csv = QtWidgets.QPushButton("Save CSV...")
        row.addWidget(self.btn_extract)
        row.addWidget(self.btn_csv)
        v.addLayout(row)
        self.table_vals = QtWidgets.QTableWidget(0, 0)
        self.table_vals.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.table_vals.verticalHeader().setVisible(False)
        v.addWidget(self.table_vals, 1)
        lay.addWidget(box, 1)

        self.btn_html_browse.clicked.connect(self._browse_html)
        self.btn_html.clicked.connect(self.on_save_html)
        self.btn_extract.clicked.connect(self.on_extract)
        self.btn_csv.clicked.connect(self.on_save_csv)
        return w

    def _browse_html(self) -> None:
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "HTML report", self.edit_html.text(),
                                                        "HTML (*.html)")
        if path:
            self.edit_html.setText(path if path.lower().endswith(".html") else path + ".html")

    def _split(self) -> List[str]:
        return [d for d, cb in self.split_checks.items() if cb.isChecked()]

    def _store_output_options(self) -> None:
        self.project.plot = {"split_by": self._split(), "freqs": self.edit_freqs.text().strip(),
                             "title": self.edit_title.text().strip()}

    def _restore_output_options(self) -> None:
        plot = self.project.plot or {}
        for d, cb in self.split_checks.items():
            cb.setChecked(d in plot.get("split_by", ["metric"]))
        self.edit_freqs.setText(str(plot.get("freqs", "")))
        if plot.get("title"):
            self.edit_title.setText(str(plot["title"]))

    def _ready_for_output(self) -> bool:
        if not self._ensure_computed():
            return False
        if not self.selected:
            self._warn("Nothing selected", "Pick some curves on tab 5 first.")
            return False
        return True

    def on_save_html(self) -> None:
        if not self._ready_for_output():
            return
        path = self.edit_html.text().strip()
        if not path:
            self._browse_html()
            path = self.edit_html.text().strip()
            if not path:
                return
        self._store_output_options()
        self._busy(True)
        try:
            export.write_html(path, self.analysis, self.selected, self._models_in_view(),
                              self._split(), self.edit_title.text().strip() or None)
        except OSError as exc:
            self._busy(False)
            self._warn("Cannot write the report", str(exc))
            return
        self._busy(False)
        self._status(f"wrote {path}")
        if self.check_open.isChecked():
            QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(os.path.abspath(path)))

    def _extract(self):
        try:
            freqs = parse_freq_list(self.edit_freqs.text())
        except ValueError as exc:
            self._warn("Frequencies", str(exc))
            return None
        return export.extract_table(self.analysis, self.selected, self._models_in_view(), freqs,
                                    include_nyquist=self.check_nyq.isChecked())

    def on_extract(self) -> None:
        if not self._ready_for_output():
            return
        table = self._extract()
        if table is None:
            return
        header, rows = table
        t = self.table_vals
        t.setSortingEnabled(False)
        t.clear()
        t.setColumnCount(len(header))
        t.setHorizontalHeaderLabels(header)
        shown = rows[:EXTRACT_LIMIT]
        t.setRowCount(len(shown))
        for r, row in enumerate(shown):
            for c, v in enumerate(row):
                if isinstance(v, float):
                    t.setItem(r, c, NumItem(v, "" if math.isnan(v) else f"{v:g}"))
                else:
                    t.setItem(r, c, QtWidgets.QTableWidgetItem(str(v)))
        t.setSortingEnabled(True)
        t.resizeColumnsToContents()
        more = f" (showing {EXTRACT_LIMIT}; the CSV has all)" if len(rows) > EXTRACT_LIMIT else ""
        self._status(f"{len(rows)} row(s){more}")

    def on_save_csv(self) -> None:
        if not self._ready_for_output():
            return
        table = self._extract()
        if table is None:
            return
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Save values", os.path.join(os.getcwd(), "chanxt_values.csv"), "CSV (*.csv)")
        if not path:
            return
        export.write_csv(path, *table)
        self._status(f"wrote {path} ({len(table[1])} rows)")

    # ==================================================================
    # refresh
    # ==================================================================

    def refresh_all(self) -> None:
        self._fill_model_list()
        self._fill_nets_tab()
        self._fill_terms_tab()
        self._fill_scen_tab()


def main(argv=None) -> int:
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv if argv is None else argv)
    win = MainWindow()
    args = (sys.argv[1:] if argv is None else argv[1:])
    for a in args:
        if a.lower().endswith(".json"):
            win.open_project(a)
        elif os.path.exists(a):
            win.add_model_files([a])
    win.show()
    return _exec(app)
