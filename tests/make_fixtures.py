"""Synthetic channel models for the tests and for trying the GUI.

Each net is an RLC ladder; neighbouring nets are coupled by mutual
capacitance between matching ladder nodes.  The nodal admittance matrix is
built over every node and Kron-reduced onto the port nodes, so the tests can
check terminations against an independent nodal solve rather than against the
formula the tool itself uses.

    python tests/make_fixtures.py        # writes tests/data/*.sNp
"""

from __future__ import annotations

import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from chanxt.touchstone import Network, write_touchstone, y_to_s  # noqa: E402

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
Z0 = 50.0

#: an LPDDR5X-like byte lane plus a few CA/CK nets, a rail and an NC pin
LP5_NETS = [
    "DQ0", "DQ1", "DQ2", "DQ3", "DQ4", "DQ5", "DQ6", "DQ7", "DMI0",
    "RDQS0_t", "RDQS0_c", "WCK0_t", "WCK0_c",
    "CA0", "CA1", "CA2", "CS0", "CK_t", "CK_c",
]
SECTIONS = 6


def freq_grid(n: int = 301) -> np.ndarray:
    return np.linspace(10e6, 20e9, n)


def ladder_y(freq, nets, cm_scale=1.0, branch=None, power=None, nc=None):
    """Nodal Y over every node of every net.

    Returns ``(Y, port_nodes, port_names)`` where ``Y`` is (F, nodes, nodes).
    ``branch``: nets that split into two DRAM drops (multi-drop).
    ``power``: name of a rail coupled to the first net.
    ``nc``: name of a one-ended stub coupled to the last net.
    """
    branch = set(branch or [])
    w = 2 * np.pi * freq
    nodes = {}
    edges = []  # (a, b, admittance(F,)) ; b None = to ground
    ports = []

    def node(name):
        if name not in nodes:
            nodes[name] = len(nodes)
        return nodes[name]

    r_sec, l_sec, c_sec, cm = 0.6, 0.45e-9, 0.18e-12, 0.035e-12 * cm_scale

    def series(a, b, r=r_sec, l=l_sec):
        edges.append((node(a), node(b), 1.0 / (r + 1j * w * l)))

    def shunt(a, c=c_sec):
        edges.append((node(a), None, 1j * w * c))

    lines = list(nets) + ([power] if power else [])
    for net in lines:
        main = SECTIONS if net not in branch else SECTIONS // 2
        for s in range(main + 1):
            shunt(f"{net}.{s}", c_sec * (25 if net == power else 1))
        for s in range(main):
            series(f"{net}.{s}", f"{net}.{s + 1}")
        ports.append((f"{net}.0", f"{net}_SoC"))
        if net in branch:
            for drop in ("DRAM0", "DRAM1"):
                prev = f"{net}.{main}"
                for s in range(1, SECTIONS - main + 1):
                    cur = f"{net}.{drop}.{s}"
                    shunt(cur)
                    series(prev, cur)
                    prev = cur
                ports.append((prev, f"{net}_{drop}"))
        else:
            ports.append((f"{net}.{main}", f"{net}_DRAM"))
    if nc:
        for s in range(3):
            shunt(f"{nc}.{s}")
        for s in range(2):
            series(f"{nc}.{s}", f"{nc}.{s + 1}")
        ports.append((f"{nc}.2", f"{nc}_DRAM"))
        couple = [(nets[-1], nc, s) for s in range(3)]
    else:
        couple = []

    # mutual capacitance between neighbours, at every shared section node
    for i in range(len(lines) - 1):
        a, b = lines[i], lines[i + 1]
        if b == power:
            a = lines[0]
        for s in range(SECTIONS + 1):
            na, nb = f"{a}.{s}", f"{b}.{s}"
            if na in nodes and nb in nodes:
                edges.append((nodes[na], nodes[nb], 1j * w * cm))
    for a, b, s in couple:
        na, nb = f"{a}.{SECTIONS - 2 + s}", f"{b}.{s}"
        if na in nodes and nb in nodes:
            edges.append((nodes[na], nodes[nb], 1j * w * cm))

    n = len(nodes)
    y = np.zeros((len(freq), n, n), dtype=complex)
    for a, b, adm in edges:
        y[:, a, a] += adm
        if b is not None:
            y[:, b, b] += adm
            y[:, a, b] -= adm
            y[:, b, a] -= adm
    port_nodes = [nodes[p] for p, _ in ports]
    return y, port_nodes, [nm for _, nm in ports]


def kron(y: np.ndarray, keep: list) -> np.ndarray:
    """Eliminate every node not in ``keep`` (no current injected there)."""
    n = y.shape[-1]
    keep = list(keep)
    drop = [i for i in range(n) if i not in keep]
    if not drop:
        return y[:, keep][:, :, keep]
    ykk = y[:, keep][:, :, keep]
    ykd = y[:, keep][:, :, drop]
    ydk = y[:, drop][:, :, keep]
    ydd = y[:, drop][:, :, drop]
    return ykk - ykd @ np.linalg.solve(ydd, ydk)


def channel(cm_scale=1.0, order="halves", branch=None, power="VDDQ", nc="NC1", nets=None):
    """A channel network and its port-node admittance matrix."""
    freq = freq_grid()
    nets = list(nets or LP5_NETS)
    y, port_nodes, names = ladder_y(freq, nets, cm_scale, branch, power, nc)
    yp = kron(y, port_nodes)
    if order == "halves":  # every SoC end first, then the rest
        idx = [i for i, nm in enumerate(names) if nm.endswith("_SoC")]
        idx += [i for i, nm in enumerate(names) if not nm.endswith("_SoC")]
        yp = yp[:, idx][:, :, idx]
        names = [names[i] for i in idx]
    s = y_to_s(yp, np.full(len(names), Z0))
    return Network(freq=freq, s=s, z0=np.full(len(names), Z0), port_names=names), yp


def main() -> None:
    os.makedirs(DATA, exist_ok=True)
    a, _ = channel(1.0)
    b, _ = channel(0.6)
    md, _ = channel(1.0, branch=["DQ0", "DQ1", "DQ2"], power=None, nc=None,
                    nets=["DQ0", "DQ1", "DQ2"])
    paths = [
        write_touchstone(a, os.path.join(DATA, f"lp5_revA.s{a.nports}p")),
        write_touchstone(b, os.path.join(DATA, f"lp5_revB.s{b.nports}p")),
        write_touchstone(a, os.path.join(DATA, f"lp5_noname.s{a.nports}p"), names=False),
        write_touchstone(md, os.path.join(DATA, f"multidrop.s{md.nports}p")),
    ]
    for p in paths:
        print(p)


if __name__ == "__main__":
    main()
