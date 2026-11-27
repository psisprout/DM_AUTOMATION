"""공통 유틸: 오류 타입, 단위 해석, 값 포맷, 작업공간 저장소, JSON 출력."""

from __future__ import annotations

import datetime as _dt
import json
import math
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import numpy as np


class UserInputError(ValueError):
    """사용자가 값을 다시 정해 줘야 해결되는 오류 (경로 없음, 열 선택, 단위 등)."""


# ─────────────────────────────────────────────────────────────────────────────
# 단위 테이블 (소문자 key, 'µ'/'μ'는 'u'로 정규화한 뒤 찾는다)
# ─────────────────────────────────────────────────────────────────────────────
TIME_UNITS = {
    "s": 1.0, "sec": 1.0, "secs": 1.0, "second": 1.0, "seconds": 1.0,
    "ms": 1e-3, "msec": 1e-3, "millisecond": 1e-3, "milliseconds": 1e-3,
    "us": 1e-6, "usec": 1e-6, "microsecond": 1e-6, "microseconds": 1e-6,
    "ns": 1e-9, "nsec": 1e-9, "nanosecond": 1e-9, "nanoseconds": 1e-9,
    "ps": 1e-12, "psec": 1e-12, "picosecond": 1e-12, "picoseconds": 1e-12,
    "fs": 1e-15,
    "min": 60.0, "mins": 60.0, "minute": 60.0, "minutes": 60.0,
    "h": 3600.0, "hr": 3600.0, "hrs": 3600.0, "hour": 3600.0, "hours": 3600.0,
}
CURRENT_UNITS = {
    "a": 1.0, "amp": 1.0, "amps": 1.0, "ampere": 1.0, "amperes": 1.0,
    "ka": 1e3,
    "ma": 1e-3, "mamp": 1e-3, "mamps": 1e-3, "milliamp": 1e-3, "milliamps": 1e-3, "milliampere": 1e-3,
    "ua": 1e-6, "uamp": 1e-6, "microamp": 1e-6, "microamps": 1e-6, "microampere": 1e-6,
    "na": 1e-9, "nanoamp": 1e-9, "nanoamps": 1e-9,
    "pa": 1e-12, "fa": 1e-15,
}
VOLTAGE_UNITS = {
    "v": 1.0, "volt": 1.0, "volts": 1.0,
    "kv": 1e3, "mv": 1e-3, "millivolt": 1e-3, "millivolts": 1e-3,
    "uv": 1e-6, "microvolt": 1e-6, "nv": 1e-9,
}
POWER_UNITS = {"w": 1.0, "watt": 1.0, "watts": 1.0, "kw": 1e3, "mw": 1e-3, "uw": 1e-6, "nw": 1e-9}
RES_UNITS = {
    "ohm": 1.0, "ohms": 1.0, "r": 1.0, "Ω".lower(): 1.0,
    "mohm": 1e-3, "mohms": 1e-3, "mΩ".lower(): 1e-3, "milliohm": 1e-3, "milliohms": 1e-3,
    "kohm": 1e3, "kΩ".lower(): 1e3, "megohm": 1e6,
}
FREQ_UNITS = {  # 샘플레이트는 'M'을 mega로 본다 (milli-Hz 샘플레이트는 없음)
    "hz": 1.0, "khz": 1e3, "mhz": 1e6, "ghz": 1e9,
    "sa/s": 1.0, "ksa/s": 1e3, "msa/s": 1e6, "gsa/s": 1e9,
    "s/s": 1.0, "ks/s": 1e3, "ms/s": 1e6, "gs/s": 1e9,
    "sps": 1.0, "ksps": 1e3, "msps": 1e6, "gsps": 1e9,
}
UNIT_TABLES = {
    "time": TIME_UNITS, "current": CURRENT_UNITS, "voltage": VOLTAGE_UNITS,
    "power": POWER_UNITS, "resistance": RES_UNITS, "freq": FREQ_UNITS,
}
# 헤더/단위행에서 쓰는 전체 단어 (Keysight 'second', 'Volt', 'Ampere' 등)
_UNIT_WORDS_KIND = ("time", "current", "voltage", "power")

SPICE_SUFFIX = {
    "t": 1e12, "g": 1e9, "k": 1e3, "m": 1e-3, "u": 1e-6, "µ": 1e-6, "μ": 1e-6,
    "n": 1e-9, "p": 1e-12, "f": 1e-15,
}
_NUM_RE = r"[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?"
_SPICE_RE = re.compile(r"^(" + _NUM_RE + r")([a-zA-Zµμ]*)$")
_QTY_RE = re.compile(r"^\s*(" + _NUM_RE + r")\s*(.*?)\s*$")


def norm_unit(u: str) -> str:
    u = u.strip().strip("()[]{}<>").strip().rstrip(".")
    return u.replace("µ", "u").replace("μ", "u").replace(" ", "").lower()


def unit_info(u: Optional[str]):
    """단위 문자열 → (kind, factor). 모르면 (None, None)."""
    if not u:
        return None, None
    n = norm_unit(u)
    for kind in _UNIT_WORDS_KIND:
        if n in UNIT_TABLES[kind]:
            return kind, UNIT_TABLES[kind][n]
    return None, None


def spice_number(tok: str) -> Optional[float]:
    """SPICE 숫자 표기 해석: 1n, 2.5u, 10meg, 3mA(단위 글자 무시). 실패 시 None.

    SPICE 규칙상 대소문자를 구분하지 않으므로 'M'도 milli(1e-3)이다. mega는 'meg'.
    """
    m = _SPICE_RE.match(tok.strip())
    if not m:
        return None
    val = float(m.group(1))
    suf = m.group(2).lower()
    if not suf:
        return val
    if suf.startswith("meg"):
        return val * 1e6
    if suf.startswith("mil"):
        return val * 25.4e-6
    f = SPICE_SUFFIX.get(suf[0])
    if f is None:
        return val  # 'V', 'A', 's' 같은 단위 글자만 붙은 경우
    if suf[0] in "fF" and len(suf) > 1 and suf[1:2].lower() == "s":
        return val * f  # fs
    return val * f


def parse_quantity(value: Any, kind: str, what: str = "값") -> Optional[float]:
    """숫자(SI 단위) 또는 '2ms', '10 uA', '1.25GS/s', '10m' 같은 문자열을 SI 값으로 바꾼다."""
    if value is None:
        return None
    if isinstance(value, (int, float, np.integer, np.floating)) and not isinstance(value, bool):
        v = float(value)
        if not math.isfinite(v):
            raise UserInputError(f"{what}에 유한한 숫자가 필요합니다 (입력값: {value})")
        return v
    s = str(value).strip()
    if not s:
        return None
    m = _QTY_RE.match(s)
    if not m:
        raise UserInputError(f"{what}를 해석할 수 없습니다: '{value}' (예: 0.002, '2ms', '10uA')")
    num = float(m.group(1))
    unit = m.group(2)
    if not unit:
        return num
    table = UNIT_TABLES[kind]
    n = norm_unit(unit)
    if n in table:
        return num * table[n]
    # SPICE 접미사 + 기본단위 (예: '2u', '5mohm', '3k')
    low = n
    base_names = {"time": ("s", "sec"), "current": ("a", "amp"), "voltage": ("v",), "power": ("w",),
                  "resistance": ("ohm", "ohms", "r", "Ω".lower()), "freq": ("hz", "sa/s", "s/s", "sps")}[kind]
    for pre, f in (("meg", 1e6), ("t", 1e12), ("g", 1e9), ("k", 1e3), ("m", 1e-3 if kind != "freq" else 1e6),
                   ("u", 1e-6), ("n", 1e-9), ("p", 1e-12), ("f", 1e-15)):
        if low.startswith(pre):
            rest = low[len(pre):]
            if rest == "" or rest in base_names:
                return num * f
    raise UserInputError(f"{what}의 단위 '{unit}'를 알 수 없습니다 (입력값: '{value}')")


def split_name_unit(header: str):
    """'Time (s)', 'I[mA]', 'Time/us', 'current_mA' → (이름, 단위문자열). 단위를 모르면 (header, None).

    'I(R1)'처럼 괄호 안이 단위가 아니면 이름 전체를 유지한다.
    """
    h = header.strip()
    if not h:
        return h, None
    m = re.match(r"^(.*?)[\s_]*[\(\[\{<]\s*([^\)\]\}>]*?)\s*[\)\]\}>]\s*$", h)
    if m and m.group(1).strip():
        kind, _ = unit_info(m.group(2))
        if kind:
            return m.group(1).strip(), m.group(2).strip()
    m = re.match(r"^(.*?\S)\s*/\s*([A-Za-zµμ]+)$", h)
    if m:
        kind, _ = unit_info(m.group(2))
        if kind:
            return m.group(1).strip(), m.group(2)
    m = re.match(r"^(.*?\S)[\s_]+([A-Za-zµμ]+)$", h)
    if m:
        kind, _ = unit_info(m.group(2))
        if kind:
            return m.group(1).strip(), m.group(2)
    return h, None


# ─────────────────────────────────────────────────────────────────────────────
# 값 포맷 (사람/LLM이 읽기 좋은 공학 표기)
# ─────────────────────────────────────────────────────────────────────────────
_PREFIX = {-15: "f", -12: "p", -9: "n", -6: "u", -3: "m", 0: "", 3: "k", 6: "M", 9: "G"}


def fmt_si(x: Optional[float], unit: str, digits: int = 4) -> Optional[str]:
    if x is None:
        return None
    x = float(x)
    if not math.isfinite(x):
        return f"{x} {unit}"
    if x == 0:
        return f"0 {unit}"
    e3 = int(math.floor(math.log10(abs(x)) / 3.0) * 3)
    e3 = max(-15, min(9, e3))
    if unit in ("A", "C", "Ah") and e3 > 0:
        e3 = 0  # 'MA'(메가암페어)와 'mA' 혼동 방지
    mant = x / (10.0 ** e3)
    s = f"{mant:.{digits}g}"
    if "e" in s:  # 반올림으로 1000이 된 경우 등
        s = f"{mant:.{digits}f}".rstrip("0").rstrip(".")
    return f"{s} {_PREFIX[e3]}{unit}"


def fmt_t(x):
    return fmt_si(x, "s")


def fmt_i(x):
    return fmt_si(x, "A")


def fmt_didt(x):
    return None if x is None else fmt_si(x * 1e-6, "A/us")


def fmt_pct(x):
    return None if x is None else f"{x * 100:.3g} %"


# ─────────────────────────────────────────────────────────────────────────────
# 작업공간 (불러온 파형/연산 결과를 이름으로 저장)
# ─────────────────────────────────────────────────────────────────────────────
NAME_RE = re.compile(r"^[0-9A-Za-z가-힣_][0-9A-Za-z가-힣_.+\-]{0,79}$")
MAX_POINTS = int(os.environ.get("CP_MAX_POINTS", "20000000"))


def ws_dir() -> Path:
    d = Path(os.environ.get("CP_WORKSPACE") or (Path.home() / ".current_profiles")).expanduser()
    d.mkdir(parents=True, exist_ok=True)
    return d.resolve()


def sanitize_name(s: str) -> str:
    s = re.sub(r"[^0-9A-Za-z가-힣_.+\-]+", "_", s.strip()).strip("_.")
    if not s or not re.match(r"[0-9A-Za-z가-힣_]", s[0]):
        s = "p_" + s
    return s[:60].rstrip("_.") or "profile"


def check_name(name: str, what: str = "이름") -> str:
    if not name or not NAME_RE.match(name) or name.endswith(".npz"):
        raise UserInputError(
            f"{what} '{name}'은(는) 쓸 수 없습니다. 영문/숫자/한글과 _ . + - 만, 80자 이내로 지어주세요.")
    return name


def profile_path(name: str) -> Path:
    return ws_dir() / f"{check_name(name)}.npz"


def exists(name: str) -> bool:
    return profile_path(name).exists()


def list_names() -> list[str]:
    files = sorted(ws_dir().glob("*.npz"), key=lambda p: p.stat().st_mtime, reverse=True)
    return [p.stem for p in files]


def unique_name(base: str, taken: Optional[set] = None) -> str:
    base = sanitize_name(base)
    taken = taken or set()
    if not exists(base) and base not in taken:
        return base
    k = 2
    while exists(f"{base}_{k}") or f"{base}_{k}" in taken:
        k += 1
    return f"{base}_{k}"


@dataclass
class Profile:
    name: str
    t: np.ndarray
    i: np.ndarray
    meta: dict = field(default_factory=dict)

    @property
    def history(self) -> list:
        return list(self.meta.get("history", []))


def load_profile(name: str) -> Profile:
    p = profile_path(name)
    if not p.exists():
        names = list_names()
        hint = ", ".join(names[:30]) if names else "(비어 있음)"
        raise UserInputError(f"작업공간에 '{name}' 파형이 없습니다. 현재 있는 이름: {hint}")
    with np.load(p, allow_pickle=False) as f:
        t = np.array(f["t"], dtype=float)
        i = np.array(f["i"], dtype=float)
        meta = json.loads(str(f["meta"]))
    return Profile(name, t, i, meta)


def save_profile(prof: Profile, overwrite: bool = False) -> str:
    p = profile_path(prof.name)
    if p.exists() and not overwrite:
        raise UserInputError(
            f"작업공간에 이미 '{prof.name}' 이름의 파형이 있습니다. 다른 result_name을 정하거나 overwrite=true로 덮어써야 합니다.")
    if len(prof.t) > MAX_POINTS:
        raise UserInputError(
            f"결과 파형이 {len(prof.t):,}점으로 너무 큽니다 (최대 {MAX_POINTS:,}점). "
            "resample_dt로 간격을 키우거나 구간을 잘라서 줄여주세요.")
    from . import ops  # 순환 import 방지
    meta = dict(prof.meta)
    meta["name"] = prof.name
    meta["saved_at"] = _dt.datetime.now().isoformat(timespec="seconds")
    meta["summary"] = ops.quick_summary(prof.t, prof.i)
    hist = meta.get("history", [])
    meta["history"] = hist[-60:]
    tmp = p.with_name(p.name + ".tmp")
    with open(tmp, "wb") as fh:
        np.savez(fh, t=np.asarray(prof.t, dtype=float), i=np.asarray(prof.i, dtype=float),
                 meta=np.array(json.dumps(meta, ensure_ascii=False)))
    os.replace(tmp, p)
    prof.meta = meta
    return str(p)


def read_meta(name: str) -> dict:
    with np.load(profile_path(name), allow_pickle=False) as f:
        return json.loads(str(f["meta"]))


# ─────────────────────────────────────────────────────────────────────────────
# 사용자 경로 검사 (스크립트 쪽 2차 검사)
# ─────────────────────────────────────────────────────────────────────────────
def check_input_file(path_str: str) -> Path:
    path = Path(path_str).expanduser()
    if not path.is_absolute():
        raise UserInputError(f"파일 경로는 절대경로여야 합니다 (입력값: {path_str})")
    if not path.is_file():
        raise UserInputError(f"입력 파일이 없습니다: {path}")
    return path.resolve()


def check_output_file(path_str: str, overwrite: bool, allowed_ext: Optional[tuple] = None) -> tuple:
    path = Path(path_str).expanduser()
    if not path.is_absolute():
        raise UserInputError(f"저장 경로는 절대경로여야 합니다 (입력값: {path_str})")
    if allowed_ext and path.suffix.lower() not in allowed_ext:
        raise UserInputError(f"저장 파일 확장자는 {', '.join(allowed_ext)} 중 하나여야 합니다 (입력값: {path_str})")
    if not path.parent.is_dir():
        raise UserInputError(f"저장할 폴더가 없습니다: {path.parent}")
    existed = path.exists()
    if existed and not overwrite:
        raise UserInputError(
            f"이미 같은 이름의 파일이 있습니다: {path.resolve()} (덮어쓰려면 overwrite 옵션이 필요합니다)")
    if not os.access(path.parent, os.W_OK):
        raise UserInputError(f"이 경로에 쓸 권한이 없습니다: {path.parent}")
    return path.resolve(), not existed


# ─────────────────────────────────────────────────────────────────────────────
# JSON 출력
# ─────────────────────────────────────────────────────────────────────────────
def jsonable(o):
    if isinstance(o, dict):
        return {str(k): jsonable(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [jsonable(v) for v in o]
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating, float)):
        f = float(o)
        return f if math.isfinite(f) else None
    if isinstance(o, np.bool_):
        return bool(o)
    if isinstance(o, np.ndarray):
        return jsonable(o.tolist())
    if isinstance(o, Path):
        return str(o)
    return o


def emit(obj: dict) -> None:
    print(json.dumps(jsonable(obj), ensure_ascii=False))


def log(msg: str) -> None:
    print(msg, file=sys.stderr)
