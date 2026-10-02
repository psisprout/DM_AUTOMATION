"""Reusable editors: check lists, rule tables, termination and direction tables."""

from __future__ import annotations

from typing import List, Optional, Sequence

from ..project import TERM_FIELDS, TERM_LABELS, TERM_TYPES, DirRule, TermRule
from ..units import fmt_si, parse_si
from .qtcompat import QtCore, QtGui, QtWidgets, Signal

BAD = QtGui.QColor("#fbe0de")


def combo(items: Sequence[str], current: str = "", editable: bool = False,
          data: Optional[Sequence[str]] = None) -> QtWidgets.QComboBox:
    cb = QtWidgets.QComboBox()
    cb.setEditable(editable)
    for i, text in enumerate(items):
        cb.addItem(text, data[i] if data else text)
    set_combo(cb, current)
    return cb


def set_combo(cb: QtWidgets.QComboBox, value: str) -> None:
    idx = cb.findData(value)
    if idx < 0:
        idx = cb.findText(value)
    if idx >= 0:
        cb.setCurrentIndex(idx)
    elif cb.isEditable():
        cb.setEditText(value)


def combo_value(cb: QtWidgets.QComboBox) -> str:
    if cb.isEditable():
        text = cb.currentText().strip()
        idx = cb.findText(text)
        return cb.itemData(idx) if idx >= 0 and cb.itemData(idx) is not None else text
    data = cb.currentData()
    return data if data is not None else cb.currentText()


def small_button(text: str, tip: str = "") -> QtWidgets.QPushButton:
    b = QtWidgets.QPushButton(text)
    b.setToolTip(tip)
    b.setSizePolicy(QtWidgets.QSizePolicy.Maximum, QtWidgets.QSizePolicy.Fixed)
    return b


# ----------------------------------------------------------------------
# check list (facets)
# ----------------------------------------------------------------------


class CheckList(QtWidgets.QWidget):
    """A titled, scrollable list of check boxes with All / None."""

    changed = Signal()

    def __init__(self, title: str, parent=None, height: int = 130) -> None:
        super().__init__(parent)
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)
        head = QtWidgets.QHBoxLayout()
        self.title = QtWidgets.QLabel(f"<b>{title}</b>")
        head.addWidget(self.title)
        head.addStretch(1)
        self.btn_all = QtWidgets.QToolButton()
        self.btn_all.setText("All")
        self.btn_none = QtWidgets.QToolButton()
        self.btn_none.setText("None")
        head.addWidget(self.btn_all)
        head.addWidget(self.btn_none)
        lay.addLayout(head)
        self.list = QtWidgets.QListWidget()
        self.list.setMaximumHeight(height)
        self.list.setUniformItemSizes(True)
        lay.addWidget(self.list)
        self._mute = False
        self.btn_all.clicked.connect(lambda: self._set_all(True))
        self.btn_none.clicked.connect(lambda: self._set_all(False))
        self.list.itemChanged.connect(self._on_item)

    def set_items(self, items: Sequence[str], checked: Optional[Sequence[str]] = None,
                  labels: Optional[dict] = None) -> None:
        """Replace the entries; keep ``checked`` (all when None) ticked."""
        self._mute = True
        self.list.clear()
        chosen = set(items if checked is None else checked)
        for value in items:
            it = QtWidgets.QListWidgetItem((labels or {}).get(value, value or "(basic)"))
            it.setData(QtCore.Qt.UserRole, value)
            it.setFlags(it.flags() | QtCore.Qt.ItemIsUserCheckable)
            it.setCheckState(QtCore.Qt.Checked if value in chosen else QtCore.Qt.Unchecked)
            self.list.addItem(it)
        self._mute = False

    def values(self) -> List[str]:
        return [self.list.item(i).data(QtCore.Qt.UserRole) for i in range(self.list.count())]

    def checked(self) -> List[str]:
        out = []
        for i in range(self.list.count()):
            it = self.list.item(i)
            if it.checkState() == QtCore.Qt.Checked:
                out.append(it.data(QtCore.Qt.UserRole))
        return out

    def set_checked(self, values: Sequence[str]) -> None:
        self._mute = True
        chosen = set(values)
        for i in range(self.list.count()):
            it = self.list.item(i)
            it.setCheckState(QtCore.Qt.Checked if it.data(QtCore.Qt.UserRole) in chosen
                             else QtCore.Qt.Unchecked)
        self._mute = False
        self.changed.emit()

    def _set_all(self, on: bool) -> None:
        self.set_checked(self.values() if on else [])

    def _on_item(self, _item) -> None:
        if not self._mute:
            self.changed.emit()


# ----------------------------------------------------------------------
# table helpers
# ----------------------------------------------------------------------


class EditTable(QtWidgets.QWidget):
    """A table with Add / Remove / Up / Down buttons."""

    changed = Signal()

    def __init__(self, headers: List[str], parent=None, hint: str = "") -> None:
        super().__init__(parent)
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.table = QtWidgets.QTableWidget(0, len(headers))
        self.table.setHorizontalHeaderLabels(headers)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        lay.addWidget(self.table)
        row = QtWidgets.QHBoxLayout()
        self.btn_add = small_button("Add")
        self.btn_del = small_button("Remove")
        self.btn_up = small_button("Up")
        self.btn_down = small_button("Down")
        for b in (self.btn_add, self.btn_del, self.btn_up, self.btn_down):
            row.addWidget(b)
        if hint:
            lab = QtWidgets.QLabel(hint)
            lab.setStyleSheet("color:#5d6677")
            lab.setWordWrap(True)
            row.addWidget(lab, 1)
        else:
            row.addStretch(1)
        lay.addLayout(row)
        self._mute = False
        self.btn_add.clicked.connect(self._add)
        self.btn_del.clicked.connect(self._remove)
        self.btn_up.clicked.connect(lambda: self._move(-1))
        self.btn_down.clicked.connect(lambda: self._move(1))
        self.table.itemChanged.connect(self._emit)

    # subclasses: row_values(r) -> list, add_row(values)
    def row_values(self, r: int) -> list:  # pragma: no cover - abstract
        raise NotImplementedError

    def add_row(self, values=None) -> None:  # pragma: no cover - abstract
        raise NotImplementedError

    def _emit(self, *_args) -> None:
        if not self._mute:
            self.changed.emit()

    def _add(self) -> None:
        self.add_row()
        self.table.scrollToBottom()
        self._emit()

    def _remove(self) -> None:
        rows = sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True)
        if not rows and self.table.currentRow() >= 0:
            rows = [self.table.currentRow()]
        for r in rows:
            self.table.removeRow(r)
        self._emit()

    def _move(self, step: int) -> None:
        r = self.table.currentRow()
        t = r + step
        if r < 0 or not (0 <= t < self.table.rowCount()):
            return
        vals = [self.row_values(i) for i in range(self.table.rowCount())]
        vals[r], vals[t] = vals[t], vals[r]
        self.load_rows(vals)
        self.table.selectRow(t)
        self._emit()

    def load_rows(self, rows: list) -> None:
        self._mute = True
        self.table.setRowCount(0)
        for vals in rows:
            self.add_row(vals)
        self._mute = False

    def _text(self, r: int, c: int) -> str:
        it = self.table.item(r, c)
        return it.text().strip() if it else ""

    def _set_text(self, r: int, c: int, text: str) -> None:
        self.table.setItem(r, c, QtWidgets.QTableWidgetItem(text))

    def _cell_combo(self, r: int, c: int, cb: QtWidgets.QComboBox) -> None:
        cb.currentIndexChanged.connect(self._emit)
        if cb.isEditable():
            cb.editTextChanged.connect(self._emit)
        self.table.setCellWidget(r, c, cb)


class RuleTable(EditTable):
    """(regex, name) pairs: group rules and tag rules."""

    def __init__(self, what: str, parent=None) -> None:
        super().__init__(["Regex (case-insensitive, searched in net name)", what], parent,
                         hint="First match wins.")
        self.table.setColumnWidth(0, 360)

    def add_row(self, values=None) -> None:
        pattern, name = values or ("", "")
        r = self.table.rowCount()
        self.table.insertRow(r)
        self._set_text(r, 0, pattern)
        self._set_text(r, 1, name)

    def row_values(self, r: int) -> list:
        return [self._text(r, 0), self._text(r, 1)]

    def rules(self) -> list:
        out = []
        for r in range(self.table.rowCount()):
            pattern, name = self.row_values(r)
            if pattern and name:
                out.append((pattern, name))
        return out


class TermTable(EditTable):
    """Termination rules: scope, name, location, type and values."""

    def __init__(self, parent=None, hint: str = "") -> None:
        super().__init__(["Scope", "Group / net (glob)", "Location", "Termination", "R (Ω)", "L (H)", "C (F)"],
                         parent, hint=hint or "Net beats group; a location beats *; later rows win. "
                         "Values take SI suffixes: 40, 1p, 2.2n, 1meg.")
        self.groups: List[str] = []
        self.locs: List[str] = []
        for c, w in enumerate((90, 150, 90, 170, 70, 70)):
            self.table.setColumnWidth(c, w)

    def set_choices(self, groups: List[str], locs: List[str]) -> None:
        self.groups, self.locs = list(groups), list(locs)

    def add_row(self, values=None) -> None:
        t: TermRule = values if isinstance(values, TermRule) else TermRule(
            scope="group", name=self.groups[0] if self.groups else "", type="short")
        r = self.table.rowCount()
        self.table.insertRow(r)
        self._cell_combo(r, 0, combo(["group", "net"], t.scope))
        self._cell_combo(r, 1, combo(self.groups, t.name, editable=True))
        self._cell_combo(r, 2, combo(["*"] + self.locs, t.loc or "*", editable=True))
        self._cell_combo(r, 3, combo([TERM_LABELS[k] for k in TERM_TYPES], t.type, data=TERM_TYPES))
        for c, k in ((4, "r"), (5, "l"), (6, "c")):
            v = getattr(t, k)
            self._set_text(r, c, fmt_si(v) if (k in TERM_FIELDS[t.type] or v) else "")

    def row_values(self, r: int) -> TermRule:
        w = self.table.cellWidget
        t = TermRule(scope=combo_value(w(r, 0)), name=combo_value(w(r, 1)),
                     loc=combo_value(w(r, 2)) or "*", type=combo_value(w(r, 3)))
        for c, k in ((4, "r"), (5, "l"), (6, "c")):
            text = self._text(r, c)
            if text:
                try:
                    setattr(t, k, parse_si(text))
                except ValueError:
                    pass
        return t

    def rules(self) -> List[TermRule]:
        """Valid rules; marks cells that are missing a needed value."""
        out = []
        self._mute = True
        for r in range(self.table.rowCount()):
            t = self.row_values(r)
            ok = bool(t.name)
            for c, k in ((4, "r"), (5, "l"), (6, "c")):
                it = self.table.item(r, c) or QtWidgets.QTableWidgetItem("")
                if self.table.item(r, c) is None:
                    self.table.setItem(r, c, it)
                need = k in TERM_FIELDS[t.type]
                good = True
                if need:
                    try:
                        good = parse_si(it.text()) > 0
                    except ValueError:
                        good = False
                it.setBackground(BAD if (need and not good) else QtGui.QBrush())
                ok = ok and good
            if ok:
                out.append(t)
        self._mute = False
        return out


class DirTable(EditTable):
    """Scenario directions: who drives (IN) and who receives (OUT)."""

    def __init__(self, parent=None) -> None:
        super().__init__(["Scope", "Group / net (glob)", "IN (driver)", "OUT (receivers, comma)", "Quiet"],
                         parent, hint="Net rows override group rows. Quiet nets neither "
                         "aggress nor get victim curves. Several OUT locations = multi-drop.")
        self.groups: List[str] = []
        self.locs: List[str] = []
        for c, w in enumerate((90, 150, 110, 200)):
            self.table.setColumnWidth(c, w)

    def set_choices(self, groups: List[str], locs: List[str]) -> None:
        self.groups, self.locs = list(groups), list(locs)

    def add_row(self, values=None) -> None:
        d: DirRule = values if isinstance(values, DirRule) else DirRule(
            "group", self.groups[0] if self.groups else "",
            self.locs[0] if self.locs else "", self.locs[1:2])
        r = self.table.rowCount()
        self.table.insertRow(r)
        self._cell_combo(r, 0, combo(["group", "net"], d.scope))
        self._cell_combo(r, 1, combo(self.groups, d.name, editable=True))
        self._cell_combo(r, 2, combo(self.locs, d.inp, editable=True))
        self._set_text(r, 3, ", ".join(d.out))
        q = QtWidgets.QTableWidgetItem("")
        q.setFlags(q.flags() | QtCore.Qt.ItemIsUserCheckable)
        q.setCheckState(QtCore.Qt.Checked if d.quiet else QtCore.Qt.Unchecked)
        self.table.setItem(r, 4, q)

    def row_values(self, r: int) -> DirRule:
        w = self.table.cellWidget
        outs = [x.strip() for x in self._text(r, 3).replace(";", ",").split(",") if x.strip()]
        q = self.table.item(r, 4)
        return DirRule(scope=combo_value(w(r, 0)), name=combo_value(w(r, 1)),
                       inp=combo_value(w(r, 2)), out=outs,
                       quiet=bool(q and q.checkState() == QtCore.Qt.Checked))

    def rules(self) -> List[DirRule]:
        return [d for d in (self.row_values(r) for r in range(self.table.rowCount())) if d.name]


def heat_colour(value: float, lo: float, hi: float) -> QtGui.QColor:
    """White at ``lo`` and below, deep red at ``hi`` and above."""
    if value != value:  # NaN
        return QtGui.QColor("#f0f0f0")
    t = 0.0 if hi <= lo else min(1.0, max(0.0, (value - lo) / (hi - lo)))
    r = 255 - int(t * (255 - 196))
    g = 255 - int(t * (255 - 40))
    b = 255 - int(t * (255 - 40))
    return QtGui.QColor(r, g, b)
