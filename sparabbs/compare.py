"""Compare a reference N-port against a BBS-derived one in the Z domain."""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .touchstone import Network, renormalize, s_to_z

PASS, WARN, FAIL = "PASS", "WARN", "FAIL"
_RANK = {PASS: 0, WARN: 1, FAIL: 2}

#: reference-node handling applied identically to both networks
REF_GLOBAL = "global"  # ports are already referenced to the model's own ground
REF_PORT = "port"  # re-reference every port to one chosen port (ADS-style)
REF_SHORT = "short"  # short the chosen ports to ground, then reduce
REF_OPEN = "open"  # leave the chosen ports open, then delete them
REF_MODES = (REF_GLOBAL, REF_PORT, REF_SHORT, REF_OPEN)

DEFAULT_BANDS: tuple[tuple[str, float, float], ...] = (
    ("DC-1MHz", 0.0, 1e6),
    ("1MHz-100MHz", 1e6, 1e8),
    ("100MHz-1GHz", 1e8, 1e9),
    ("above-1GHz", 1e9, float("inf")),
)


class CompareError(ValueError):
    """Raised when two networks cannot be compared."""


def worst(*statuses: str) -> str:
    return max(statuses, key=lambda s: _RANK.get(s, 0)) if statuses else PASS


# --------------------------------------------------------------------------
# reference-node handling
# --------------------------------------------------------------------------


@dataclass
class ReferenceSpec:
    """How to re-reference / terminate the raw N-port before comparing."""

    mode: str = REF_GLOBAL
    ports: list[int] = field(default_factory=list)  # 1-based indices

    def describe(self, names: list[str]) -> str:
        def nm(p: int) -> str:
            return names[p - 1] if 1 <= p <= len(names) else f"P{p}"

        if self.mode == REF_GLOBAL:
            return "raw ports, model's own reference"
        if self.mode == REF_PORT:
            return f"all ports referenced to {nm(self.ports[0])}"
        listed = ", ".join(nm(p) for p in self.ports)
        verb = "shorted to ground" if self.mode == REF_SHORT else "left open"
        return f"port(s) {listed} {verb}"


def apply_reference(
    z: np.ndarray, names: list[str], spec: ReferenceSpec
) -> tuple[np.ndarray, list[str]]:
    """Apply ``spec`` to a Z matrix stack, returning the reduced Z and port names."""
    n = z.shape[-1]
    for p in spec.ports:
        if not 1 <= p <= n:
            raise CompareError(f"port {p} is outside 1..{n}")

    if spec.mode == REF_GLOBAL:
        return z, list(names)

    if spec.mode == REF_PORT:
        if len(spec.ports) != 1:
            raise CompareError("'port' reference mode needs exactly one port")
        r = spec.ports[0] - 1
        keep = [i for i in range(n) if i != r]
        # V_i - V_r with the return current flowing out of node r
        zr = z[:, keep, :][:, :, keep]
        zr -= z[:, keep, :][:, :, [r]]
        zr -= z[:, [r], :][:, :, keep]
        zr += z[:, [r], :][:, :, [r]]
        return zr, [names[i] for i in keep]

    drop = sorted({p - 1 for p in spec.ports})
    keep = [i for i in range(n) if i not in drop]
    if not keep:
        raise CompareError("every port was removed - nothing left to compare")
    if spec.mode == REF_OPEN:
        return z[:, keep, :][:, :, keep], [names[i] for i in keep]

    # REF_SHORT: Schur complement of the shorted block
    zkk = z[:, keep, :][:, :, keep]
    zks = z[:, keep, :][:, :, drop]
    zsk = z[:, drop, :][:, :, keep]
    zss = z[:, drop, :][:, :, drop]
    return zkk - zks @ np.linalg.solve(zss, zsk), [names[i] for i in keep]


# --------------------------------------------------------------------------
# grid alignment
# --------------------------------------------------------------------------


def align(
    ref: Network, dut: Network, mode: str = "intersect", rtol: float = 1e-6
) -> tuple[np.ndarray, np.ndarray, np.ndarray, list[str]]:
    """Put both networks on one frequency grid.

    Returns ``(freq, s_ref, s_dut, notes)``.  ``mode`` is ``exact`` (grids must
    match), ``intersect`` (keep common points) or ``interp`` (resample the DUT
    onto the reference grid).
    """
    notes: list[str] = []
    if ref.nports != dut.nports:
        raise CompareError(
            f"port count differs: reference has {ref.nports}, BBS result has "
            f"{dut.nports}"
        )

    same = ref.npoints == dut.npoints and np.allclose(
        ref.freq, dut.freq, rtol=rtol, atol=0.0
    )
    if same:
        return ref.freq, ref.s, dut.s, notes
    if mode == "exact":
        raise CompareError(
            f"frequency grids differ ({ref.npoints} vs {dut.npoints} points) "
            "and alignment mode is 'exact'"
        )

    if mode == "intersect":
        idx_r, idx_d = _match_grids(ref.freq, dut.freq, rtol)
        if idx_r.size == 0:
            raise CompareError("the two frequency grids have no points in common")
        notes.append(
            f"grids differ; compared on {idx_r.size} common points "
            f"(reference had {ref.npoints}, BBS result {dut.npoints})"
        )
        return ref.freq[idx_r], ref.s[idx_r], dut.s[idx_d], notes

    if mode == "interp":
        lo, hi = dut.freq[0], dut.freq[-1]
        inside = (ref.freq >= lo) & (ref.freq <= hi)
        if not inside.any():
            raise CompareError("the BBS result does not overlap the reference grid")
        if not inside.all():
            notes.append(
                f"{int((~inside).sum())} reference points fall outside the BBS "
                "result's range and were dropped"
            )
        freq = ref.freq[inside]
        s_dut = _interp_complex(freq, dut.freq, dut.s)
        notes.append("BBS result interpolated onto the reference grid")
        return freq, ref.s[inside], s_dut, notes

    raise CompareError(f"unknown alignment mode {mode!r}")


def _match_grids(a: np.ndarray, b: np.ndarray, rtol: float):
    ia, ib = [], []
    j = 0
    for i, f in enumerate(a):
        while j < b.size and b[j] < f * (1 - rtol) - 1e-30:
            j += 1
        if j < b.size and abs(b[j] - f) <= rtol * max(abs(f), 1e-30):
            ia.append(i)
            ib.append(j)
            j += 1
    return np.asarray(ia, dtype=int), np.asarray(ib, dtype=int)


def _interp_complex(new_f, old_f, s):
    """Interpolate real and imaginary parts on a log-frequency axis."""
    lf_new = np.log10(np.maximum(new_f, 1e-30))
    lf_old = np.log10(np.maximum(old_f, 1e-30))
    flat = s.reshape(s.shape[0], -1)
    out = np.empty((new_f.size, flat.shape[1]), dtype=complex)
    for c in range(flat.shape[1]):
        out[:, c] = np.interp(lf_new, lf_old, flat[:, c].real) + 1j * np.interp(
            lf_new, lf_old, flat[:, c].imag
        )
    return out.reshape(new_f.size, s.shape[1], s.shape[2])


# --------------------------------------------------------------------------
# criteria and results
# --------------------------------------------------------------------------


@dataclass
class Criteria:
    """Pass/fail thresholds.  A metric above ``warn_frac`` of a limit warns."""

    mag_err_pct: float = 5.0
    err_db: float = 0.5
    phase_err_deg: float = 5.0
    peak_shift_pct: float = 2.0
    peak_mag_err_pct: float = 10.0
    warn_frac: float = 0.6
    #: dB/phase statistics ignore points below this fraction of the band peak
    significance_floor: float = 0.01
    #: when False a passivity/reciprocity violation warns but does not fail
    gate_on_checks: bool = False
    bands: tuple[tuple[str, float, float], ...] = DEFAULT_BANDS

    def judge(self, value: float, limit: float) -> str:
        if not np.isfinite(value):
            return FAIL
        if value > limit:
            return FAIL
        if value > limit * self.warn_frac:
            return WARN
        return PASS


@dataclass
class BandStat:
    name: str
    f_lo: float
    f_hi: float
    npoints: int
    norm_err_pct: float
    max_err_db: float
    rmse_db: float
    max_phase_deg: float
    f_worst: float
    status: str


@dataclass
class PeakStat:
    ref_f: float
    dut_f: float
    shift_pct: float
    ref_mag: float
    dut_mag: float
    mag_err_pct: float
    status: str


@dataclass
class TermResult:
    i: int
    j: int
    name: str
    bands: list[BandStat]
    peaks: list[PeakStat]
    status: str


@dataclass
class CompareResult:
    freq: np.ndarray
    z_ref: np.ndarray
    z_dut: np.ndarray
    port_names: list[str]
    terms: list[TermResult]
    checks: list[tuple[str, str, str]]  # (name, status, detail)
    notes: list[str]
    criteria: Criteria
    reference: ReferenceSpec
    status: str

    @property
    def nports(self) -> int:
        return len(self.port_names)

    def term(self, i: int, j: int) -> TermResult:
        for t in self.terms:
            if (t.i, t.j) == (i, j):
                return t
        raise KeyError(f"no term Z{i}{j}")

    def counts(self) -> dict[str, int]:
        out = {PASS: 0, WARN: 0, FAIL: 0}
        for t in self.terms:
            out[t.status] += 1
        return out


# --------------------------------------------------------------------------
# the comparison itself
# --------------------------------------------------------------------------


def compare(
    ref: Network,
    dut: Network,
    criteria: Criteria | None = None,
    reference: ReferenceSpec | None = None,
    align_mode: str = "intersect",
    upper_triangle_only: bool = True,
) -> CompareResult:
    """Compare ``dut`` against ``ref`` term by term in the Z domain."""
    criteria = criteria or Criteria()
    reference = reference or ReferenceSpec()
    notes: list[str] = []
    checks: list[tuple[str, str, str]] = []

    freq, s_ref, s_dut, align_notes = align(ref, dut, align_mode)
    notes += align_notes

    if not np.allclose(ref.z0, dut.z0):
        s_dut = renormalize(s_dut, dut.z0, ref.z0)
        notes.append(
            f"BBS result renormalized from Z0={_z0txt(dut.z0)} to "
            f"{_z0txt(ref.z0)} before comparison"
        )

    checks.append(_passivity_check("reference passivity", s_ref))
    checks.append(_passivity_check("BBS-result passivity", s_dut))
    checks.append(_reciprocity_check("BBS-result reciprocity", s_dut))

    z_ref = s_to_z(s_ref, ref.z0)
    z_dut = s_to_z(s_dut, ref.z0)
    z_ref, names = apply_reference(z_ref, ref.port_names, reference)
    z_dut, _ = apply_reference(z_dut, ref.port_names, reference)

    n = z_ref.shape[-1]
    terms: list[TermResult] = []
    for i in range(n):
        for j in range(n):
            if upper_triangle_only and j < i:
                continue
            terms.append(_term(freq, z_ref, z_dut, i, j, names, criteria))

    check_status = [c[1] for c in checks]
    if not criteria.gate_on_checks:
        # a non-passive model is worth flagging, but it is not what this
        # tool judges - the Z terms decide pass/fail
        check_status = [WARN if s == FAIL else s for s in check_status]
    status = worst(*(t.status for t in terms), *check_status)
    return CompareResult(
        freq=freq,
        z_ref=z_ref,
        z_dut=z_dut,
        port_names=names,
        terms=terms,
        checks=checks,
        notes=notes,
        criteria=criteria,
        reference=reference,
        status=status,
    )


def _z0txt(z0: np.ndarray) -> str:
    return f"{z0[0]:g} ohm" if np.allclose(z0, z0[0]) else "per-port"


def _term(freq, z_ref, z_dut, i, j, names, crit: Criteria) -> TermResult:
    a = z_ref[:, i, j]
    b = z_dut[:, i, j]
    label = f"Z{i + 1}{j + 1}" if len(names) < 10 else f"Z({i + 1},{j + 1})"

    bands: list[BandStat] = []
    for name, lo, hi in crit.bands:
        sel = (freq >= lo) & (freq < hi)
        if not sel.any():
            continue
        bands.append(_band_stat(name, lo, hi, freq[sel], a[sel], b[sel], crit))

    peaks = _peak_stats(freq, a, b, crit) if i == j else []
    status = worst(*(bs.status for bs in bands), *(p.status for p in peaks))
    return TermResult(i=i + 1, j=j + 1, name=label, bands=bands, peaks=peaks, status=status)


def _band_stat(name, lo, hi, f, a, b, crit: Criteria) -> BandStat:
    peak = float(np.max(np.abs(a))) if a.size else 0.0
    scale = peak if peak > 0 else 1.0
    norm_err = np.abs(b - a) / scale * 100.0

    keep = np.abs(a) >= crit.significance_floor * peak
    if not keep.any():
        keep = np.ones_like(f, dtype=bool)
    ratio = np.abs(b[keep]) / np.maximum(np.abs(a[keep]), 1e-300)
    err_db = 20.0 * np.log10(np.maximum(ratio, 1e-300))
    dphase = np.angle(b[keep] / np.where(a[keep] == 0, 1e-300, a[keep]), deg=True)

    worst_at = int(np.argmax(norm_err))
    status = worst(
        crit.judge(float(np.max(norm_err)), crit.mag_err_pct),
        crit.judge(float(np.max(np.abs(err_db))), crit.err_db),
        crit.judge(float(np.max(np.abs(dphase))), crit.phase_err_deg),
    )
    return BandStat(
        name=name,
        f_lo=float(f[0]),
        f_hi=float(f[-1]),
        npoints=int(f.size),
        norm_err_pct=float(np.max(norm_err)),
        max_err_db=float(np.max(np.abs(err_db))),
        rmse_db=float(np.sqrt(np.mean(err_db**2))),
        max_phase_deg=float(np.max(np.abs(dphase))),
        f_worst=float(f[worst_at]),
        status=status,
    )


def _find_peaks(mag: np.ndarray, prominence_db: float = 1.0) -> list[int]:
    """Local maxima of |Z| that stand at least ``prominence_db`` above a neighbour."""
    if mag.size < 3:
        return []
    log = 20.0 * np.log10(np.maximum(mag, 1e-300))
    out = []
    for k in range(1, mag.size - 1):
        if log[k] <= log[k - 1] or log[k] < log[k + 1]:
            continue
        left = log[k] - np.min(log[:k])
        right = log[k] - np.min(log[k + 1 :])
        if min(left, right) >= prominence_db:
            out.append(k)
    return out


def _peak_stats(freq, a, b, crit: Criteria) -> list[PeakStat]:
    """Match each reference anti-resonance to the nearest peak in the BBS result."""
    ref_pk = _find_peaks(np.abs(a))
    dut_pk = _find_peaks(np.abs(b))
    if not ref_pk:
        return []
    mag_b = np.abs(b)
    out: list[PeakStat] = []
    for k in ref_pk:
        f0 = freq[k]
        if dut_pk:
            m = min(dut_pk, key=lambda d: abs(np.log10(max(freq[d], 1e-30)) - np.log10(max(f0, 1e-30))))
        else:
            m = int(np.argmax(mag_b))
        shift = abs(freq[m] - f0) / f0 * 100.0 if f0 > 0 else float("inf")
        ref_mag, dut_mag = float(np.abs(a[k])), float(mag_b[m])
        mag_err = abs(dut_mag - ref_mag) / ref_mag * 100.0 if ref_mag > 0 else float("inf")
        out.append(
            PeakStat(
                ref_f=float(f0),
                dut_f=float(freq[m]),
                shift_pct=float(shift),
                ref_mag=ref_mag,
                dut_mag=dut_mag,
                mag_err_pct=float(mag_err),
                status=worst(
                    crit.judge(shift, crit.peak_shift_pct),
                    crit.judge(mag_err, crit.peak_mag_err_pct),
                ),
            )
        )
    return out


def _passivity_check(name: str, s: np.ndarray) -> tuple[str, str, str]:
    sv = np.linalg.svd(s, compute_uv=False)
    mx = float(np.max(sv))
    status = PASS if mx <= 1.0 + 1e-6 else (WARN if mx <= 1.01 else FAIL)
    return (name, status, f"max singular value of S = {mx:.6f}")


def _reciprocity_check(name: str, s: np.ndarray) -> tuple[str, str, str]:
    err = float(np.max(np.abs(s - s.transpose(0, 2, 1))))
    status = PASS if err <= 1e-6 else (WARN if err <= 1e-3 else FAIL)
    return (name, status, f"max |S - S^T| = {err:.3e}")
