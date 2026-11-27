"""파형 그래프 (PNG/SVG/PDF). 점이 많으면 구간별 최소/최대를 남겨 spike가 사라지지 않게 줄인다.

축 이름 같은 고정 글자는 영어로 둔다 (서버에 한글 폰트가 없어도 깨지지 않게).
"""

from __future__ import annotations

import numpy as np

from .common import fmt_i
from . import ops


def _scale(span, units):
    for f, u in units:
        if span >= f:
            return f, u
    return units[-1]


def envelope(t, i, nb=2000):
    n = len(t)
    if n <= 4 * nb:
        return t, i
    starts = np.linspace(0, n, nb + 1).astype(int)[:-1]
    mx = np.maximum.reduceat(i, starts)
    mn = np.minimum.reduceat(i, starts)
    tt = np.repeat(t[starts], 2)
    yy = np.empty(2 * nb)
    yy[0::2] = mn
    yy[1::2] = mx
    return tt, yy


def _font(plt):
    from matplotlib import font_manager
    names = {f.name for f in font_manager.fontManager.ttflist}
    for nm in ("NanumGothic", "Noto Sans CJK KR", "Noto Sans KR", "Malgun Gothic", "UnDotum", "Baekmuk Gulim"):
        if nm in names:
            plt.rcParams["font.family"] = nm
            break
    plt.rcParams["axes.unicode_minus"] = False


def plot_profiles(profs, out_path, t_start=None, t_end=None, separate=False, title=None):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        raise RuntimeError("matplotlib이 설치되어 있지 않아 그래프를 그릴 수 없습니다 (pip install matplotlib).")
    _font(plt)
    data = []
    for p in profs:
        t, i = p.t, p.i
        if t_start is not None or t_end is not None:
            t, i = ops.crop(t, i, t_start, t_end)
        data.append((p.name, t, i))
    tmin = min(float(d[1][0]) for d in data)
    tmax = max(float(d[1][-1]) for d in data)
    span = max(tmax - tmin, 1e-15)
    tf, tu = _scale(span, [(1.0, "s"), (1e-3, "ms"), (1e-6, "us"), (1e-9, "ns"), (1e-12, "ps")])
    imax = max(float(np.max(np.abs(d[2]))) for d in data) or 1.0
    cf, cu = _scale(imax, [(1.0, "A"), (1e-3, "mA"), (1e-6, "uA"), (1e-9, "nA"), (1e-12, "pA")])
    n = len(data)
    sep = separate and n > 1
    if sep:
        fig, axes = plt.subplots(n, 1, figsize=(11, 2.4 * n + 0.8), sharex=True, squeeze=False)
        axes = [a[0] for a in axes]
    else:
        fig, ax = plt.subplots(figsize=(11, 4.8))
        axes = [ax] * n
    for k, (name, t, i) in enumerate(data):
        tt, ii = envelope(t, i)
        T = t[-1] - t[0]
        mean = ops.integral(t, i) / T if T > 0 else float(i[0])
        ax = axes[k]
        ax.plot(tt / tf, ii / cf, lw=0.9, label=f"{name} (avg {fmt_i(mean)})", color=f"C{k % 10}")
        ax.grid(True, alpha=0.3)
        ax.set_ylabel(f"Current [{cu}]")
        if sep:
            ax.legend(loc="upper right", fontsize=8)
    axes[-1].set_xlabel(f"Time [{tu}]")
    if not sep:
        axes[0].legend(loc="upper right", fontsize=8)
    fig.suptitle(title or ", ".join(d[0] for d in data), fontsize=11)
    fig.tight_layout()
    fig.savefig(out_path, dpi=120)
    plt.close(fig)
