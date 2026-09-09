"""Touchstone (.sNp) reader/writer supporting v1.0 and v2.0.

Only depends on numpy.  Networks are always held internally as S-parameters;
files written as Z or Y are converted on load and the original parameter type
is remembered in :attr:`Network.source_param`.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

import numpy as np

_FREQ_UNITS = {"HZ": 1.0, "KHZ": 1e3, "MHZ": 1e6, "GHZ": 1e9, "THZ": 1e12}
_PARAMS = ("S", "Z", "Y", "H", "G")
_FORMATS = ("MA", "DB", "RI")

# "! Port 1 = VDD_CORE", "!port[3]: GND", "! PORT 2 VDD_IO"
_PORT_NAME_RE = re.compile(
    r"^\s*!\s*port\s*[\[#]?\s*(\d+)\s*\]?\s*[:=]?\s+(\S.*?)\s*$", re.IGNORECASE
)


class TouchstoneError(ValueError):
    """Raised when a Touchstone file cannot be interpreted."""


@dataclass
class Network:
    """An N-port network sampled on a frequency grid."""

    freq: np.ndarray  # (F,) float, Hz, ascending
    s: np.ndarray  # (F, N, N) complex
    z0: np.ndarray  # (N,) float, reference resistance per port (ohm)
    port_names: list[str]
    path: str = ""
    version: str = "1.0"
    source_param: str = "S"
    z0_from_file: bool = True
    comments: list[str] = field(default_factory=list)

    @property
    def nports(self) -> int:
        return self.s.shape[1]

    @property
    def npoints(self) -> int:
        return self.freq.shape[0]

    def uniform_z0(self) -> float | None:
        """The single reference impedance, or None when ports differ."""
        return float(self.z0[0]) if np.allclose(self.z0, self.z0[0]) else None

    def describe(self) -> str:
        lo, hi = self.freq[0], self.freq[-1]
        z = self.uniform_z0()
        ztxt = f"{z:g} ohm" if z is not None else "per-port"
        return (
            f"{self.nports}-port, {self.npoints} pts, "
            f"{_eng(lo)}Hz .. {_eng(hi)}Hz, Z0={ztxt}, "
            f"Touchstone v{self.version} ({self.source_param}-param)"
        )


def _eng(x: float) -> str:
    """Format a frequency with an engineering suffix."""
    if x == 0:
        return "0 "
    for scale, suf in ((1e9, "G"), (1e6, "M"), (1e3, "k"), (1.0, "")):
        if abs(x) >= scale:
            return f"{x / scale:g} {suf}"
    return f"{x:g} "


# --------------------------------------------------------------------------
# reading
# --------------------------------------------------------------------------


def _nports_from_ext(path: str) -> int | None:
    m = re.search(r"\.s(\d+)p$", path, re.IGNORECASE)
    return int(m.group(1)) if m else None


def _solve_nports(ntokens: int, hint: int | None) -> int:
    """Pick N such that ntokens is a whole number of 1+2*N*N records."""
    candidates = [hint] if hint else []
    candidates += [n for n in range(1, 257) if n != hint]
    for n in candidates:
        if n and ntokens % (1 + 2 * n * n) == 0:
            return n
    raise TouchstoneError(
        f"cannot infer port count from {ntokens} data values; "
        "give the file a .sNp extension or use Touchstone v2"
    )


def read_touchstone(path: str) -> Network:
    """Parse a Touchstone v1.0 or v2.0 file into a :class:`Network`."""
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        raw_lines = fh.readlines()

    comments: list[str] = []
    port_names: dict[int, str] = {}
    version = "1.0"
    kw: dict[str, str] = {}
    kw_order: list[str] = []
    in_network_data = False
    in_v2_body = False

    freq_unit, param, fmt, z0_scalar = "GHZ", "S", "MA", 50.0
    saw_option = False
    saw_reference = False
    tokens: list[str] = []

    for line in raw_lines:
        stripped = line.strip()
        if not stripped:
            continue

        if stripped.startswith("!"):
            comments.append(stripped)
            m = _PORT_NAME_RE.match(stripped)
            if m:
                port_names[int(m.group(1))] = m.group(2).strip()
            continue

        # strip trailing inline comment
        code = stripped.split("!", 1)[0].strip()
        if not code:
            continue

        if code.startswith("#"):
            # option line: "# GHZ S MA R 50"
            saw_option = True
            parts = code[1:].upper().split()
            i = 0
            while i < len(parts):
                p = parts[i]
                if p in _FREQ_UNITS:
                    freq_unit = p
                elif p in _PARAMS:
                    param = p
                elif p in _FORMATS:
                    fmt = p
                elif p == "R":
                    if i + 1 < len(parts):
                        z0_scalar = float(parts[i + 1])
                        saw_reference = True
                        i += 1
                i += 1
            continue

        if code.startswith("["):
            m = re.match(r"\[([^\]]+)\]\s*(.*)", code)
            if not m:
                raise TouchstoneError(f"malformed keyword line: {code!r}")
            name, rest = m.group(1).strip().lower(), m.group(2).strip()
            kw_order.append(name)
            kw[name] = rest
            if name == "version":
                version = rest or "2.0"
            in_network_data = name == "network data"
            in_v2_body = True
            if name == "end":
                break
            continue

        if in_v2_body and not in_network_data:
            # continuation of a v2 keyword value (e.g. a long [Reference] list)
            if kw_order:
                kw[kw_order[-1]] = (kw[kw_order[-1]] + " " + code).strip()
            continue

        tokens.extend(code.replace(",", " ").split())

    if not saw_option:
        raise TouchstoneError("no '#' option line found - not a Touchstone file")
    if not tokens:
        raise TouchstoneError("file contains no network data")

    is_v2 = version.startswith("2")
    hint = _nports_from_ext(path)
    if is_v2 and "number of ports" in kw:
        nports = int(float(kw["number of ports"].split()[0]))
    else:
        nports = _solve_nports(len(tokens), hint)
    if hint and hint != nports:
        comments.append(
            f"! sparabbs: extension says {hint} ports but data implies {nports}"
        )

    matrix_format = kw.get("matrix format", "full").strip().lower() or "full"
    if matrix_format not in ("full", "lower", "upper"):
        raise TouchstoneError(f"unsupported [Matrix Format] {matrix_format!r}")
    ncells = nports * nports if matrix_format == "full" else nports * (nports + 1) // 2
    stride = 1 + 2 * ncells
    if len(tokens) % stride:
        raise TouchstoneError(
            f"data length {len(tokens)} is not a multiple of {stride} "
            f"for a {nports}-port {matrix_format} matrix"
        )

    try:
        values = np.asarray(tokens, dtype=float)
    except ValueError as exc:
        raise TouchstoneError(f"non-numeric value in network data: {exc}") from exc
    values = values.reshape(-1, stride)

    freq = values[:, 0] * _FREQ_UNITS[freq_unit]
    pairs = values[:, 1:].reshape(len(values), ncells, 2)
    cells = _to_complex(pairs, fmt)

    data = np.zeros((len(values), nports, nports), dtype=complex)
    if matrix_format == "full":
        data = cells.reshape(len(values), nports, nports)
        if nports == 2 and _v1_two_port_swapped(is_v2, kw):
            data = data.transpose(0, 2, 1)  # v1 stores S11 S21 S12 S22
    else:
        idx = (
            np.tril_indices(nports) if matrix_format == "lower" else np.triu_indices(nports)
        )
        data[:, idx[0], idx[1]] = cells
        data[:, idx[1], idx[0]] = cells  # triangular formats imply symmetry

    z0 = _read_reference(kw, nports, z0_scalar)
    z0_from_file = saw_reference or bool(kw.get("reference", "").strip())

    names = _read_port_names(kw, port_names, nports)
    s = _param_to_s(data, param, z0)

    order = np.argsort(freq, kind="stable")
    if not np.all(order == np.arange(len(freq))):
        freq, s = freq[order], s[order]

    return Network(
        freq=freq,
        s=s,
        z0=z0,
        port_names=names,
        path=os.path.abspath(path),
        version=version,
        source_param=param,
        z0_from_file=z0_from_file,
        comments=comments,
    )


def _v1_two_port_swapped(is_v2: bool, kw: dict) -> bool:
    """Touchstone v1 writes 2-ports as S11 S21 S12 S22; v2 defaults to 12_21."""
    if not is_v2:
        return True
    return kw.get("two-port data order", "12_21").strip().lower() == "21_12"


def _to_complex(pairs: np.ndarray, fmt: str) -> np.ndarray:
    a, b = pairs[..., 0], pairs[..., 1]
    if fmt == "RI":
        return a + 1j * b
    mag = a if fmt == "MA" else 10.0 ** (a / 20.0)
    return mag * np.exp(1j * np.deg2rad(b))


def _read_reference(kw: dict, nports: int, z0_scalar: float) -> np.ndarray:
    if "reference" in kw and kw["reference"].strip():
        vals = [float(v) for v in kw["reference"].replace(",", " ").split()]
        if len(vals) != nports:
            raise TouchstoneError(
                f"[Reference] lists {len(vals)} values for {nports} ports"
            )
        return np.asarray(vals, dtype=float)
    return np.full(nports, float(z0_scalar))


def _read_port_names(kw: dict, from_comments: dict, nports: int) -> list[str]:
    names = [f"P{i + 1}" for i in range(nports)]
    for i, nm in from_comments.items():
        if 1 <= i <= nports:
            names[i - 1] = nm
    if "port names" in kw and kw["port names"].strip():
        toks = kw["port names"].split()
        for k in range(0, len(toks) - 1, 2):
            try:
                i = int(toks[k])
            except ValueError:
                break
            if 1 <= i <= nports:
                names[i - 1] = toks[k + 1]
    return names


# --------------------------------------------------------------------------
# parameter conversions
# --------------------------------------------------------------------------


def _param_to_s(data: np.ndarray, param: str, z0: np.ndarray) -> np.ndarray:
    if param == "S":
        return data
    if param == "Z":
        return z_to_s(data, z0)
    if param == "Y":
        with np.errstate(divide="ignore", invalid="ignore"):
            return z_to_s(np.linalg.inv(data), z0)
    raise TouchstoneError(f"{param}-parameter files are not supported")


def s_to_z(s: np.ndarray, z0: np.ndarray) -> np.ndarray:
    """Convert S to Z for real, per-port reference impedances.

    ``Z = (I - A)^-1 (I + A) G`` with ``A_ij = S_ij*sqrt(z0_i/z0_j)`` and
    ``G = diag(z0)``.  Reduces to ``z0 (I-S)^-1 (I+S)`` for a uniform z0.
    """
    n = s.shape[-1]
    root = np.sqrt(np.asarray(z0, dtype=float))
    scale = root[:, None] / root[None, :]
    a = s * scale
    eye = np.eye(n, dtype=complex)
    return np.linalg.solve(eye - a, (eye + a) * np.asarray(z0, dtype=float)[None, :])


def z_to_s(z: np.ndarray, z0: np.ndarray) -> np.ndarray:
    """Inverse of :func:`s_to_z`."""
    z0 = np.asarray(z0, dtype=float)
    root = np.sqrt(z0)
    g = np.diag(z0).astype(complex)
    a = np.linalg.solve((z + g).transpose(0, 2, 1), (z - g).transpose(0, 2, 1))
    a = a.transpose(0, 2, 1)  # a = (Z-G)(Z+G)^-1
    return a / (root[:, None] / root[None, :])


def renormalize(s: np.ndarray, z_old: np.ndarray, z_new: np.ndarray) -> np.ndarray:
    """Re-reference S-parameters from ``z_old`` to ``z_new`` (real impedances)."""
    if np.allclose(z_old, z_new):
        return s
    return z_to_s(s_to_z(s, z_old), z_new)


# --------------------------------------------------------------------------
# writing (used for fixtures and for exporting re-referenced networks)
# --------------------------------------------------------------------------


def write_touchstone(net: Network, path: str, fmt: str = "RI") -> str:
    """Write ``net`` as a Touchstone v1.0 file.  Returns the path written."""
    n = net.nports
    z = net.uniform_z0()
    if z is None:
        raise TouchstoneError("v1.0 output requires a single reference impedance")
    lines = [
        "! Written by sparabbs",
        *[f"! Port {i + 1} = {nm}" for i, nm in enumerate(net.port_names)],
        f"# HZ S {fmt} R {z:g}",
    ]
    for k, f in enumerate(net.freq):
        m = net.s[k].T if n == 2 else net.s[k]  # v1 2-port order is S11 S21 S12 S22
        flat = m.reshape(-1)
        cells = [_fmt_cell(c, fmt) for c in flat]
        if n <= 2:
            # v1 puts a 1- or 2-port frequency point on a single line
            lines.append(f"{f:.10g} " + " ".join(cells))
            continue
        for row in range(n):
            chunk = cells[row * n : (row + 1) * n]
            prefix = f"{f:.10g} " if row == 0 else " "
            for j in range(0, len(chunk), 4):
                lines.append((prefix if j == 0 else " ") + " ".join(chunk[j : j + 4]))
    # LF regardless of platform: a deck or model written on Windows is
    # routinely read by a simulator on a Linux farm
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(lines) + "\n")
    return path


def _fmt_cell(c: complex, fmt: str) -> str:
    if fmt == "RI":
        return f"{c.real:.12g} {c.imag:.12g}"
    if fmt == "MA":
        return f"{abs(c):.12g} {np.rad2deg(np.angle(c)):.10g}"
    mag = 20 * np.log10(max(abs(c), 1e-300))
    return f"{mag:.12g} {np.rad2deg(np.angle(c)):.10g}"
