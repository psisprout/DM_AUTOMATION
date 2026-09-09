"""Load whichever Qt binding is installed and expose one uniform surface.

Supports PyQt5, PyQt6, PySide2 and PySide6 so the tool runs on whatever the
site's EDA Python happens to ship.
"""

from __future__ import annotations

BINDING = ""
_ERRORS: list[str] = []

for _name in ("PyQt5", "PySide6", "PySide2", "PyQt6"):
    try:
        if _name == "PyQt5":
            from PyQt5 import QtCore, QtGui, QtWidgets, uic  # noqa: F401
            from PyQt5.QtCore import pyqtSignal as Signal  # noqa: F401
        elif _name == "PyQt6":
            from PyQt6 import QtCore, QtGui, QtWidgets, uic  # noqa: F401
            from PyQt6.QtCore import pyqtSignal as Signal  # noqa: F401
        elif _name == "PySide6":
            from PySide6 import QtCore, QtGui, QtWidgets  # noqa: F401
            from PySide6.QtCore import Signal  # noqa: F401

            uic = None  # type: ignore[assignment]
        else:
            from PySide2 import QtCore, QtGui, QtWidgets  # noqa: F401
            from PySide2.QtCore import Signal  # noqa: F401

            uic = None  # type: ignore[assignment]
        BINDING = _name
        break
    except ImportError as exc:  # pragma: no cover - depends on the environment
        _ERRORS.append(f"{_name}: {exc}")

if not BINDING:  # pragma: no cover - depends on the environment
    raise ImportError(
        "sparabbs' GUI needs PyQt5, PyQt6, PySide2 or PySide6.\n"
        "Install one, e.g. 'pip install PyQt5'.\nTried:\n  "
        + "\n  ".join(_ERRORS)
    )


def load_ui(path: str):
    """Load a Qt Designer .ui file and return the widget it describes.

    Returning a standalone widget (rather than populating ``self``) is the one
    pattern both PyQt's ``uic`` and PySide's ``QUiLoader`` support, so the GUI
    reaches its children through ``window.ui.<objectName>``.
    """
    if uic is not None:  # PyQt5 / PyQt6
        return uic.loadUi(path)

    # PySide2 / PySide6
    if BINDING == "PySide6":  # pragma: no cover - depends on the environment
        from PySide6.QtUiTools import QUiLoader
    else:  # pragma: no cover - depends on the environment
        from PySide2.QtUiTools import QUiLoader

    f = QtCore.QFile(path)  # pragma: no cover
    if not f.open(QtCore.QIODevice.ReadOnly):  # pragma: no cover
        raise IOError(f"cannot open {path}")
    try:  # pragma: no cover
        widget = QUiLoader().load(f)
    finally:  # pragma: no cover
        f.close()
    if widget is None:  # pragma: no cover
        raise IOError(f"Qt could not build a widget from {path}")
    return widget  # pragma: no cover
