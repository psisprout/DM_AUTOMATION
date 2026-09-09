"""Build a small synthetic PDN and write the fixtures the tests use.

The network is a three-node RLC mesh with each node shunted to ground, which
gives the anti-resonances a real PDN model has.  Because it is defined by a
nodal admittance matrix, the tests can check the Z-domain code against an
independent MNA solve.
"""

from __future__ import annotations

import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sparabbs.touchstone import Network, write_touchstone, z_to_s  # noqa: E402

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

# node -> (R, L, C) shunt to ground; pair -> (R, L) series branch
SHUNTS = {0: (2e-3, 8e-10, 2e-6), 1: (5e-3, 1.5e-9, 5e-7), 2: (1e-3, 4e-10, 1e-5)}
BRANCHES = {(0, 1): (3e-3, 2e-10), (0, 2): (1e-3, 5e-11), (1, 2): (8e-3, 9e-10)}


def nodal_y(freq: np.ndarray) -> np.ndarray:
    """(F, 3, 3) nodal admittance matrix with ground as the reference node."""
    w = 2 * np.pi * freq
    jw = 1j * w
    y = np.zeros((freq.size, 3, 3), dtype=complex)
    for n, (r, l, c) in SHUNTS.items():
        y[:, n, n] += 1.0 / (r + jw * l) + jw * c
    for (a, b), (r, l) in BRANCHES.items():
        g = 1.0 / (r + jw * l)
        y[:, a, a] += g
        y[:, b, b] += g
        y[:, a, b] -= g
        y[:, b, a] -= g
    return y


def reference_network(freq: np.ndarray, z0: float = 50.0) -> Network:
    z = np.linalg.inv(nodal_y(freq))
    return Network(
        freq=freq,
        s=z_to_s(z, np.full(3, z0)),
        z0=np.full(3, z0),
        port_names=["VDD_CORE", "VDD_IO", "VDD_PMIC"],
    )


def perturb(net: Network, rel: float = 0.02, seed: int = 7) -> Network:
    """Stand in for a BBS fit error: a smooth frequency-dependent tilt on Z.

    Perturbing Z rather than S keeps the result positive-real, so the fixture
    behaves like a real (passive) broadband fit instead of tripping the
    passivity check.
    """
    from sparabbs.touchstone import s_to_z

    rng = np.random.default_rng(seed)
    lf = np.log10(np.maximum(net.freq, 1.0))
    lf = (lf - lf.min()) / max(float(np.ptp(lf)), 1e-12)
    tilt = 1.0 + rel * (0.5 + lf)[:, None, None]
    jitter = 1.0 + rel * 0.1 * rng.standard_normal(net.freq.shape)[:, None, None]
    z = s_to_z(net.s, net.z0) * tilt * jitter
    return Network(
        freq=net.freq,
        s=z_to_s(z, net.z0),
        z0=net.z0.copy(),
        port_names=list(net.port_names),
    )


BBS_NETLIST = """* Fake broadband-SPICE model, only used to exercise the parser
.subckt rc_leg a b rval=1 cval=1n
R1 a m 'rval'
C1 m b 'cval'
.ends rc_leg

.subckt pdn3_bbs VDD_CORE VDD_IO VDD_PMIC GND
Xleg1 VDD_CORE GND rc_leg rval=2m cval=2u
Xleg2 VDD_IO   GND rc_leg rval=5m cval=500n
Xleg3 VDD_PMIC GND rc_leg rval=1m cval=10u
L12 VDD_CORE VDD_IO 200p
L13 VDD_CORE VDD_PMIC 50p
L23 VDD_IO VDD_PMIC 900p
.ends pdn3_bbs
"""


def main() -> None:
    os.makedirs(DATA, exist_ok=True)
    freq = np.logspace(3, 10, 400)  # 1 kHz .. 10 GHz
    ref = reference_network(freq)
    write_touchstone(ref, os.path.join(DATA, "pdn3.s3p"))
    write_touchstone(perturb(ref), os.path.join(DATA, "pdn3_bbs.s3p"))
    write_touchstone(perturb(ref, rel=0.30), os.path.join(DATA, "pdn3_bad.s3p"))
    with open(os.path.join(DATA, "pdn3_bbs.sp"), "w") as fh:
        fh.write(BBS_NETLIST)
    print(f"fixtures written to {DATA}")


if __name__ == "__main__":
    main()
