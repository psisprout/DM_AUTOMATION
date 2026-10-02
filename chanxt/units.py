"""Small parsing helpers shared by the engine, the CLI and the GUI."""

from __future__ import annotations

import fnmatch
import re

_SI = {
    "f": 1e-15, "p": 1e-12, "n": 1e-9, "u": 1e-6, "µ": 1e-6, "m": 1e-3,
    "": 1.0, "k": 1e3, "meg": 1e6, "g": 1e9, "t": 1e12,
}
_SI_RE = re.compile(r"^\s*([-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)\s*(meg|[fpnuµmkgt]?)\s*[a-zA-ZΩ]*\s*$", re.I)


def parse_si(text: str) -> float:
    """``'40'`` -> 40, ``'1p'`` -> 1e-12, ``'2.2nH'`` -> 2.2e-9, ``'1meg'`` -> 1e6.

    SPICE convention: ``m`` is milli and ``meg`` is mega.
    """
    if isinstance(text, (int, float)):
        return float(text)
    m = _SI_RE.match(str(text))
    if not m:
        raise ValueError(f"not a value: {text!r}")
    return float(m.group(1)) * _SI[m.group(2).lower()]


def fmt_si(x: float) -> str:
    """Inverse of :func:`parse_si`, for display."""
    if x == 0:
        return "0"
    for suf, scale in (("t", 1e12), ("g", 1e9), ("meg", 1e6), ("k", 1e3), ("", 1.0),
                       ("m", 1e-3), ("u", 1e-6), ("n", 1e-9), ("p", 1e-12), ("f", 1e-15)):
        if abs(x) >= scale * 0.9999999:
            return f"{x / scale:.4g}{suf}"
    return f"{x:.4g}"


_FREQ_SCALE = {"hz": 1.0, "khz": 1e3, "mhz": 1e6, "ghz": 1e9, "thz": 1e12}


def parse_freq_list(text: str, default_unit: str = "GHz") -> list[float]:
    """``'2.67, 5.33 GHz; 800MHz'`` -> [2.67e9, 5.33e9, 8e8] (Hz).

    A bare number takes the unit written after it in the same comma group, or
    ``default_unit``.
    """
    out: list[float] = []
    for part in re.split(r"[,;\n]+", text or ""):
        part = part.strip()
        if not part:
            continue
        m = re.match(r"^([-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)\s*([a-zA-Z]*)$", part)
        if not m:
            raise ValueError(f"not a frequency: {part!r}")
        unit = (m.group(2) or default_unit).lower()
        if unit not in _FREQ_SCALE:
            raise ValueError(f"unknown frequency unit in {part!r}")
        out.append(float(m.group(1)) * _FREQ_SCALE[unit])
    return out


def natural_key(text: str):
    """Sort ``DQ2`` before ``DQ10``."""
    return [(0, int(t), "") if t.isdigit() else (1, 0, t.lower())
            for t in re.split(r"(\d+)", text or "") if t != ""]


def name_matcher(pattern: str):
    """Return a predicate for a name filter.

    * empty -> matches everything
    * ``re:<regex>`` -> case-insensitive regex search
    * otherwise comma/space separated globs, case-insensitive, matched against
      the whole name (``DQ1`` is DQ1 only; ``DQ1*`` adds DQ10..DQ15)
    """
    pattern = (pattern or "").strip()
    if not pattern:
        return lambda name: True
    if pattern.lower().startswith("re:"):
        rx = re.compile(pattern[3:].strip(), re.I)
        return lambda name: bool(rx.search(name))
    pats = [re.compile(fnmatch.translate(g), re.I)
            for g in re.split(r"[,\s]+", pattern) if g]
    return lambda name: any(p.match(name) for p in pats)
