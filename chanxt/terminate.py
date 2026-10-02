"""Load ports with a termination and reduce the network to the ports that stay.

With ``K`` the ports kept and ``T`` the ports loaded by reflection
coefficients ``G = diag(g_t)``::

    S' = S_KK + S_KT G (I - S_TT G)^-1 S_TK

A port left at its reference impedance has ``g = 0`` and simply drops out.
Every metric this tool reports is a magnitude, so the reduced network is kept
as ``|S'|^2`` in float32: a quarter of the memory of the complex matrix.
"""

from __future__ import annotations

import numpy as np

from .project import TermRule

#: frequencies solved per batch; bounds the temporary (chunk, T, T) arrays
_CHUNK = 64


def load_gamma(term: TermRule, freq: np.ndarray, z0: float) -> np.ndarray:
    """Reflection coefficient of ``term`` seen from a port of reference ``z0``.

    The load is written as ``Z = num/den`` so that an open circuit (``den=0``)
    and a capacitor at DC need no special case: ``g = (num - z0 den)/(num + z0 den)``.
    """
    w = 2 * np.pi * np.asarray(freq, dtype=float)
    jw = 1j * w
    one = np.ones_like(jw)
    t = term.type
    if t == "z0":
        return np.zeros_like(jw)
    if t == "short":
        num, den = 0 * one, one
    elif t == "open":
        num, den = one, 0 * one
    elif t == "r":
        num, den = term.r * one, one
    elif t == "l":
        num, den = jw * term.l, one
    elif t == "c":
        num, den = one, jw * term.c
    elif t == "rc_series":  # R + 1/(jwC) = (1 + jwRC) / (jwC)
        num, den = one + jw * term.r * term.c, jw * term.c
    elif t == "rc_parallel":  # R / (1 + jwRC)
        num, den = term.r * one, one + jw * term.r * term.c
    else:
        raise ValueError(f"{t!r} is not a load")
    if t in ("c", "rc_series") and term.c <= 0:
        raise ValueError("a capacitor needs C > 0")
    return (num - z0 * den) / (num + z0 * den)


def reduce_power(
    s: np.ndarray,
    keep: list[int],
    loaded: list[int],
    gammas: np.ndarray,
) -> np.ndarray:
    """``|S'|^2`` for the kept ports, as float32 of shape (F, K, K).

    ``loaded`` are the ports with a non-zero reflection, ``gammas`` their
    (F, T) reflection coefficients; every other port is at its reference.
    """
    f = s.shape[0]
    k = np.asarray(keep, dtype=int)
    t = np.asarray(loaded, dtype=int)
    out = np.empty((f, len(k), len(k)), dtype=np.float32)
    if len(t) == 0:
        for a in range(0, f, _CHUNK):
            blk = s[a : a + _CHUNK][:, k[:, None], k[None, :]]
            out[a : a + _CHUNK] = (blk.real ** 2 + blk.imag ** 2).astype(np.float32)
        return out
    eye = np.eye(len(t), dtype=complex)
    for a in range(0, f, _CHUNK):
        blk = s[a : a + _CHUNK]
        g = gammas[a : a + _CHUNK]  # (c, T)
        s_kk = blk[:, k[:, None], k[None, :]]
        s_kt = blk[:, k[:, None], t[None, :]]
        s_tk = blk[:, t[:, None], k[None, :]]
        s_tt = blk[:, t[:, None], t[None, :]]
        # (I - S_TT G) X = S_TK  ->  S' = S_KK + S_KT G X
        lhs = eye[None] - s_tt * g[:, None, :]
        x = np.linalg.solve(lhs, s_tk)
        red = s_kk + np.matmul(s_kt * g[:, None, :], x)
        out[a : a + _CHUNK] = (red.real ** 2 + red.imag ** 2).astype(np.float32)
    return out
