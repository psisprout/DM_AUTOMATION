"""Choosing which Z terms to look at, and drawing them.

Selection is kept here as plain functions over (i, j) pairs so it can be tested
without Qt, and so the same idioms are available from a script.
"""

from __future__ import annotations

import fnmatch
from typing import Iterable, Sequence, Tuple

import numpy as np

from .compare import FAIL, WARN, CompareResult

# typing.Tuple, not tuple[...]: this is evaluated at import time, and a
# builtin generic is only subscriptable from Python 3.9
Term = Tuple[int, int]  # 1-based (i, j)

#: what a plot draws on its y axis
MODE_MAG = "mag"
MODE_MAG_ERR = "mag+err"
MODE_ERR = "err"
MODE_PHASE = "phase"
MODES = (
    (MODE_MAG, "|Z| magnitude"),
    (MODE_MAG_ERR, "|Z| with error below"),
    (MODE_ERR, "error only (dB)"),
    (MODE_PHASE, "phase"),
)

LAYOUT_OVERLAY = "overlay"
LAYOUT_GRID = "grid"
LAYOUTS = ((LAYOUT_OVERLAY, "overlay in one axes"), (LAYOUT_GRID, "one subplot per term"))

#: plotting many traces at once is slow and unreadable; warn past this
BUSY_TRACES = 24


# --------------------------------------------------------------------------
# selection
# --------------------------------------------------------------------------


def all_terms(n: int) -> list[Term]:
    return [(i, j) for i in range(1, n + 1) for j in range(1, n + 1)]


def diagonal(n: int) -> list[Term]:
    """Zii - the self impedances, which is what a PDN is usually judged on."""
    return [(i, i) for i in range(1, n + 1)]


def upper(n: int, include_diagonal: bool = True) -> list[Term]:
    """The unique half of a reciprocal matrix; Zji duplicates Zij."""
    return [
        (i, j)
        for i in range(1, n + 1)
        for j in range(1, n + 1)
        if (j > i or (include_diagonal and j == i))
    ]


def off_diagonal(n: int) -> list[Term]:
    return [t for t in upper(n, include_diagonal=False)]


def row(n: int, i: int) -> list[Term]:
    """Everything port *i* couples into."""
    return [(i, j) for j in range(1, n + 1)]


def column(n: int, j: int) -> list[Term]:
    return [(i, j) for i in range(1, n + 1)]


def neighbours(n: int, distance: int = 1) -> list[Term]:
    """Terms whose port indices are ``distance`` apart - adjacent-rail coupling."""
    return [(i, i + distance) for i in range(1, n - distance + 1)]


def by_status(result: CompareResult, statuses: Iterable[str]) -> list[Term]:
    """Terms the comparison scored at one of ``statuses``.

    The point of a validation run: plot exactly what did not match.
    """
    wanted = set(statuses)
    return [(t.i, t.j) for t in result.terms if t.status in wanted]


def failing(result: CompareResult) -> list[Term]:
    return by_status(result, (FAIL,))


def worst(result: CompareResult, count: int = 8) -> list[Term]:
    """The ``count`` terms with the largest normalized error, worst first."""

    def score(term) -> float:
        return max((b.norm_err_pct for b in term.bands), default=0.0)

    ranked = sorted(result.terms, key=score, reverse=True)
    return [(t.i, t.j) for t in ranked[:count]]


def by_name(result: CompareResult, pattern: str, both: bool = False) -> list[Term]:
    """Terms whose port names match a glob, e.g. ``VDD_CORE*``.

    With 24 rails, naming the rail is far quicker than hunting for its index.
    ``both`` requires each end to match; otherwise either end will do.
    """
    pat = pattern.strip()
    if not pat:
        return []
    if not any(c in pat for c in "*?["):
        pat = f"*{pat}*"
    hit = [
        fnmatch.fnmatch(name.lower(), pat.lower()) for name in result.port_names
    ]
    n = len(hit)
    pairs = []
    for i in range(1, n + 1):
        for j in range(1, n + 1):
            match = (hit[i - 1] and hit[j - 1]) if both else (hit[i - 1] or hit[j - 1])
            if match:
                pairs.append((i, j))
    return pairs


def dedupe(terms: Iterable[Term]) -> list[Term]:
    """Keep order, drop repeats."""
    seen: set[Term] = set()
    out = []
    for t in terms:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


def fold_to_upper(terms: Iterable[Term]) -> list[Term]:
    """Map Zji onto Zij, so a reciprocal pair is not drawn twice."""
    return dedupe((min(i, j), max(i, j)) for i, j in terms)


# --------------------------------------------------------------------------
# drawing
# --------------------------------------------------------------------------


class PlotUnavailable(RuntimeError):
    """Raised when matplotlib is not installed."""


def label(result: CompareResult, term: Term) -> str:
    i, j = term
    names = result.port_names
    if i == j:
        return f"Z{i}{i}  {names[i - 1]}"
    return f"Z{i}{j}  {names[i - 1]} / {names[j - 1]}"


def traces(result: CompareResult, term: Term) -> tuple[np.ndarray, np.ndarray]:
    i, j = term
    return result.z_ref[:, i - 1, j - 1], result.z_dut[:, i - 1, j - 1]


def _err_db(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    with np.errstate(divide="ignore", invalid="ignore"):
        return 20.0 * np.log10(np.abs(b) / np.abs(a))


def plot(
    result: CompareResult,
    terms: Sequence[Term],
    mode: str = MODE_MAG_ERR,
    layout: str = LAYOUT_OVERLAY,
    show: bool = True,
):
    """Draw ``terms``.  Returns the figure.

    Reference is drawn solid and the BBS result dashed in the same colour, so a
    term that matches reads as one line.
    """
    terms = list(terms)
    if not terms:
        raise ValueError("no terms selected")

    try:
        import matplotlib.pyplot as plt
    except ImportError as exc:  # pragma: no cover - depends on the environment
        raise PlotUnavailable(
            "plotting needs matplotlib:  pip install matplotlib"
        ) from exc

    freq = result.freq
    if layout == LAYOUT_GRID:
        cols = int(np.ceil(np.sqrt(len(terms))))
        rows = int(np.ceil(len(terms) / cols))
        fig, axes = plt.subplots(
            rows, cols, figsize=(min(4 * cols, 20), min(3 * rows, 14)), squeeze=False
        )
        flat = axes.reshape(-1)
        for ax, term in zip(flat, terms):
            _draw(ax, freq, result, [term], mode, single=True)
            ax.set_title(label(result, term), fontsize=9)
        for ax in flat[len(terms) :]:
            ax.axis("off")
    else:
        panels = 2 if mode == MODE_MAG_ERR else 1
        fig, axes = plt.subplots(
            panels, 1, sharex=True, figsize=(10, 4 + 3 * panels), squeeze=False
        )
        _draw(axes[0][0], freq, result, terms, mode, extra=axes[-1][0])
        axes[-1][0].set_xlabel("frequency (Hz)")

    fig.suptitle(
        f"{len(terms)} Z term(s) - reference solid, BBS result dashed", fontsize=10
    )
    fig.tight_layout()
    if show:
        plt.show()
    return fig


def _draw(ax, freq, result, terms, mode, single: bool = False, extra=None) -> None:
    """Draw one axes.  The dashed BBS trace never gets its own legend entry -
    it is the same term as the solid one, and the title says which is which."""
    import matplotlib.pyplot as plt

    hidden = "_nolegend_"
    cycle = plt.rcParams["axes.prop_cycle"].by_key().get("color", ["C0"])
    for k, term in enumerate(terms):
        colour = cycle[k % len(cycle)]
        a, b = traces(result, term)
        ref_label = "reference" if single else label(result, term)
        dut_label = "BBS result" if single else hidden
        if mode in (MODE_MAG, MODE_MAG_ERR):
            ax.loglog(freq, np.abs(a), color=colour, label=ref_label)
            ax.loglog(freq, np.abs(b), "--", color=colour, label=dut_label)
            ax.set_ylabel("|Z| (ohm)")
            if mode == MODE_MAG_ERR and extra is not None:
                extra.semilogx(freq, _err_db(a, b), color=colour, label=hidden)
                extra.set_ylabel("error (dB)")
                extra.grid(True, which="both", alpha=0.3)
        elif mode == MODE_ERR:
            ax.semilogx(freq, _err_db(a, b), color=colour, label=ref_label)
            ax.set_ylabel("error (dB)")
        else:
            ax.semilogx(freq, np.angle(a, deg=True), color=colour, label=ref_label)
            ax.semilogx(freq, np.angle(b, deg=True), "--", color=colour, label=dut_label)
            ax.set_ylabel("phase (deg)")
    ax.grid(True, which="both", alpha=0.3)
    if single or len(terms) <= 12:
        handles, labels = ax.get_legend_handles_labels()
        if handles:
            ax.legend(fontsize=8, ncol=1 if single else 2)
    if single:
        ax.set_xlabel("frequency (Hz)")
