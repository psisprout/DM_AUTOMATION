"""여러 양식의 전류 파형 파일 읽기.

모든 reader는 Parsed(블록 → 열 목록)를 만들고, build_profiles()가 그 중
시간/전류 열을 골라 SI 단위 (t[s], i[A]) 배열로 바꾼다.

지원 양식 요약 (자세한 표는 docs/current_profile_mcp.md):
- 텍스트 표: csv / tsv / txt / dat / 공백구분 / ';' + 소수점 쉼표 / 앞쪽 메타데이터 줄
  (Tektronix, Keysight, Rigol, LeCroy, PicoScope 스코프 CSV, Monsoon/Otii/Joulescope CSV,
   LTspice 'Export data as text'(.step 포함), ngspice wrdata/print, Cadence ViVA CSV/VCSV,
   HSPICE 표(SI 접미사 1.2m, 10n) 등)
- PWL: PWL 파일(.pwl, 상대시간 '+'), 넷리스트(.sp/.cir/.net/.scs ...)의 I/V PWL 소스,
  LTspice 회로도(.asc)의 PWL 값, PWL FILE= 참조
- SPICE raw: ngspice / LTspice / Xyce / Spectre(nutmeg) — ASCII와 binary, .step
- HSPICE CSDF, Cadence PSF ASCII
- 스코프 바이너리: Tektronix .isf, LeCroy .trc, Keysight/Agilent .bin
- 엑셀 .xlsx/.xlsm/.xls/.ods, JSON, NumPy .npy/.npz, MATLAB .mat(v5, v7.3), HDF5 .h5,
  Parquet, Feather, NI TDMS(npTDMS 설치 시), 그리고 위 파일들의 .gz 압축본
"""

from __future__ import annotations

import csv
import gzip
import io
import json
import re
import struct
import zipfile
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import numpy as np

from .common import (TIME_UNITS, UserInputError, fmt_t, norm_unit, parse_quantity,
                     spice_number, split_name_unit, unit_info)
from . import ops


# ─────────────────────────────────────────────────────────────────────────────
# 자료 구조
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class Column:
    name: str
    values: np.ndarray
    unit: Optional[str] = None
    role: str = "data"            # time | index | data | x | drop
    kind: Optional[str] = None    # 'current' | 'voltage' (양식이 알려주는 경우)
    x: Optional[np.ndarray] = None  # 열마다 시간축이 따로 있을 때 (Cadence X/Y 쌍, PWL 소스)
    x_unit: Optional[str] = None


@dataclass
class Block:
    label: str
    columns: list


@dataclass
class Parsed:
    fmt: str
    blocks: list
    meta: dict = field(default_factory=dict)
    si: bool = False              # 시간[s]/전류[A]가 이미 SI (SPICE 계열)
    dt: Optional[float] = None    # 메타데이터의 샘플 간격 [s]
    t0: float = 0.0               # 메타데이터의 시작 시간 [s]
    vunit: Optional[str] = None   # 메타데이터의 세로축 단위
    hunit: Optional[str] = None   # 메타데이터의 가로축 단위
    notes: list = field(default_factory=list)


def unsupported(what: str, how: str):
    raise UserInputError(f"{what} 양식은 직접 읽을 수 없습니다. {how}")


# ─────────────────────────────────────────────────────────────────────────────
# 파일 열기 / 인코딩 / 양식 판별
# ─────────────────────────────────────────────────────────────────────────────
class Source:
    def __init__(self, path: Path):
        self.path = path
        data = path.read_bytes()
        name = path.name
        if data[:2] == b"\x1f\x8b":
            data = gzip.decompress(data)
            if name.lower().endswith(".gz"):
                name = name[:-3]
        self.data = data
        self.name = name
        self.ext = Path(name).suffix.lower()
        self._text = None

    def text(self):
        if self._text is None:
            self._text = decode_bytes(self.data)
        return self._text


def decode_bytes(data: bytes):
    if data.startswith(b"\xef\xbb\xbf"):
        return data[3:].decode("utf-8", errors="replace"), "utf-8-sig"
    if data.startswith(b"\xff\xfe") or data.startswith(b"\xfe\xff"):
        return data.decode("utf-16", errors="replace"), "utf-16"
    head = data[:4096]
    if len(head) >= 8 and head[1::2].count(0) > 0.4 * len(head[1::2]) and head[0::2].count(0) < 0.1 * len(head[0::2]):
        return data.decode("utf-16-le", errors="replace"), "utf-16-le"
    for enc in ("utf-8", "cp949"):
        try:
            return data.decode(enc), enc
        except UnicodeDecodeError:
            pass
    return data.decode("latin-1"), "latin-1"


PWL_EXTS = {".pwl", ".pwlf", ".stim"}
NETLIST_EXTS = {".sp", ".spi", ".spice", ".cir", ".net", ".ckt", ".inc", ".lib", ".scs", ".mdl", ".netlist"}


def sniff(src: Source) -> str:
    d, ext = src.data, src.ext
    head = d[:4096]
    if head.startswith(b"\x93NUMPY"):
        return "npy"
    if head.startswith(b"PK\x03\x04"):
        try:
            names = zipfile.ZipFile(io.BytesIO(d)).namelist()
        except zipfile.BadZipFile:
            names = []
        if any(n.endswith(".npy") for n in names):
            return "npz"
        if any(n.startswith("xl/") for n in names):
            return "excel"
        if "content.xml" in names:
            return "excel"
        unsupported("ZIP 압축", "압축을 풀고 안의 파일 경로를 알려주세요.")
    if head.startswith(b"\x89HDF\r\n\x1a\n") or d[512:520] == b"\x89HDF\r\n\x1a\n":
        return "hdf5"
    if head.startswith(b"MATLAB 5.0 MAT-file"):
        return "mat"
    if head.startswith(b"\xd0\xcf\x11\xe0"):
        return "excel"  # 예전 .xls (OLE)
    if head[:4] == b"PAR1":
        return "parquet"
    if head[:6] == b"ARROW1":
        return "feather"
    if head[:4] == b"TDSm" or ext == ".tdms":
        return "tdms"
    if head[:2] == b"AG" and head[2:4] in (b"10", b"11", b"12"):
        return "keysight_bin"
    if b"WAVEDESC" in head[:1024]:
        return "lecroy_trc"
    if re.search(rb"(?:WFMP|WFMO)[A-Z]*:", head) or (re.search(rb"BYT_N", head) and re.search(rb"CURV", d[:1 << 16])):
        return "tek_isf"
    t16 = head.decode("utf-16-le", errors="ignore") if head[1:2] == b"\x00" else ""
    ta = head.decode("latin-1")
    for t in (t16, ta):
        if re.match(r"\s*(Title|Plotname)\s*:", t) and re.search(r"No\. Variables|Variables:", t):
            return "spice_raw"
    if ta.lstrip().startswith("#H"):
        return "csdf"
    if ta.lstrip().startswith("HEADER") and "PSFversion" in ta:
        return "psfascii"
    if b"PSFversion" in head or head.startswith(b"\x00\x00\x04\x00"):
        unsupported("Cadence PSF binary",
                    "Spectre를 psfascii 형식으로 출력하거나(Cadence의 psf 변환 유틸리티 사용), ViVA에서 CSV로 export해 주세요.")
    if ext == ".wfm":
        unsupported("Tektronix/Rigol .wfm", "스코프에서 CSV나 .isf로 저장해 주세요.")
    if ext == ".jls":
        unsupported("Joulescope .jls", "Joulescope UI에서 CSV로 export해 주세요.")
    if ext == ".dlog":
        unsupported("Keysight N6705/14585A .dlog", "14585A 또는 N6705 화면에서 CSV로 export해 주세요.")
    if ext in (".mf4", ".mdf", ".dat") and head[:8] in (b"MDF     ", b"MDF 4.10", b"MDF 4.00", b"MDF 3.30"):
        unsupported("ASAM MDF", "측정 툴에서 CSV로 export해 주세요.")
    if ext in (".xlsx", ".xlsm", ".xls", ".ods"):
        return "excel"
    if ext in (".json",) or ta.lstrip()[:1] in ("{", "["):
        try:
            json.loads(src.text()[0])
            return "json"
        except ValueError:
            pass
    if b"\x00" in head and not t16:
        unsupported(f"바이너리({ext or '확장자 없음'})",
                    "CSV(시간, 전류 열)로 export한 파일을 주세요. 지원 양식 목록은 docs/current_profile_mcp.md 참고.")
    text_head = src.text()[0][:200000]
    if ext == ".asc" and "SYMATTR" in text_head:
        return "pwl"
    if ext in PWL_EXTS:
        return "pwl"
    if re.search(r"(?im)^\s*[iv]\S*\s+\S+\s+\S+\s+.*\bpwl\b", text_head) or re.match(r"(?is)\s*pwl\s*\(", text_head):
        return "pwl"
    if ext in NETLIST_EXTS and re.search(r"(?i)\bpwl\b", text_head):
        return "pwl"
    return "table"


def read_file(path: Path, sheet: Any = None) -> Parsed:
    src = Source(path)
    kind = sniff(src)
    reader = {
        "npy": read_npy, "npz": read_npz, "hdf5": read_hdf5, "mat": read_mat, "parquet": read_parquet,
        "feather": read_feather, "tdms": read_tdms, "keysight_bin": read_keysight_bin, "lecroy_trc": read_trc,
        "tek_isf": read_isf, "spice_raw": read_spice_raw, "csdf": read_csdf, "psfascii": read_psfascii,
        "json": read_json, "pwl": read_pwl, "table": read_table_text,
    }.get(kind)
    if kind == "excel":
        return read_excel(src, sheet)
    return reader(src)


# ─────────────────────────────────────────────────────────────────────────────
# 텍스트 표 (csv/tsv/txt/스코프 CSV/LTspice txt/ngspice ...)
# ─────────────────────────────────────────────────────────────────────────────
COMMENT_PREFIX = ("//", "#", ";", "%", "*", "!")
DELIMS = (",", "\t", ";", "|", "ws")


def is_comment(line: str) -> bool:
    return line.lstrip().startswith(COMMENT_PREFIX)


def strip_comment(line: str) -> str:
    s = line.lstrip()
    for p in COMMENT_PREFIX:
        if s.startswith(p):
            return s[len(p):]
    return s


def tok_line(line: str, d: Optional[str]):
    if d is None:
        return line
    if d == "ws":
        return line.split()
    if '"' in line:
        try:
            return [c.strip() for c in next(csv.reader([line], delimiter=d))]
        except (csv.Error, StopIteration):
            pass
    return [c.strip().strip('"').strip("'").strip() for c in line.split(d)]


def to_num(tok: str, dc: bool = False, spice: bool = False):
    try:
        return float(tok)
    except (ValueError, TypeError):
        pass
    if dc and "," in tok:
        try:
            return float(tok.replace(",", "."))
        except ValueError:
            pass
    if spice:
        return spice_number(tok)
    return None


@dataclass
class TableSpec:
    delim: Optional[str]
    dc: bool
    spice: bool
    core: tuple
    cols: tuple
    start: int
    maxfields: int


def _rows_for(lines, d):
    rows = []
    for ln in lines:
        if d is None:
            rows.append(ln)
        elif not ln.strip() or is_comment(ln):
            rows.append([])
        else:
            rows.append(tok_line(ln, d))
    return rows


def detect_spec(lines, delims=DELIMS) -> Optional[TableSpec]:
    best = None
    for spice in (False, True):
        for d in delims:
            rows = _rows_for(lines, d)
            dcs = (False,) if d == "," else (False, True)
            for dc in dcs:
                if dc and not any("," in "".join(r) for r in rows[:300] if r):
                    continue
                numsets = [frozenset(j for j, tok in enumerate(r) if tok and to_num(tok, dc, spice) is not None)
                           for r in rows]
                cnt = Counter(s for s in numsets if s)
                if not cnt:
                    continue
                cands = {s for s, _ in cnt.most_common(8)}
                top = [s for s, _ in cnt.most_common(4)]
                for a in top:
                    for b in top:
                        if a & b:
                            cands.add(a & b)
                local = None
                for C in cands:
                    match = sum(1 for s in numsets if C <= s)
                    score = match * len(C)
                    if local is None or (score, len(C)) > (local[1], len(local[0])):
                        local = (C, score, match)
                C, score, match = local
                mrows = [r for r, s in zip(rows, numsets) if C <= s]
                nonempty = sum(sum(1 for t in r if t) for r in mrows)
                numeric = sum(sum(1 for j, t in enumerate(r) if t and j in C) for r in mrows)
                frac = numeric / max(nonempty, 1)
                key = (score, round(frac, 3), not spice, not dc)
                if best is None or key > best[0]:
                    best = (key, d, dc, spice, C, rows, numsets)
    if best is None:
        return None
    _, d, dc, spice, C, rows, numsets = best
    match_idx = [k for k, s in enumerate(numsets) if C <= s]
    # 드문드문 비어 있는 열도 포함
    cols = set(C)
    nmatch = len(match_idx)
    maxw = max((len(rows[k]) for k in match_idx), default=0)
    for j in range(maxw):
        if j in cols:
            continue
        num = sum(1 for k in match_idx if j < len(rows[k]) and rows[k][j] and j in numsets[k])
        bad = sum(1 for k in match_idx if j < len(rows[k]) and rows[k][j] and j not in numsets[k])
        if num >= 0.5 * nmatch and bad <= 0.02 * nmatch:
            cols.add(j)
    # 데이터 시작 위치: 충분히 긴 첫 연속 구간
    runs, cur = [], None
    for k, (r, s) in enumerate(zip(rows, numsets)):
        blank = not lines[k].strip() if d is not None else not any(r)
        if C <= s:
            if cur is None:
                cur = [k, 0]
            cur[1] += 1
        elif blank:
            continue
        else:
            if cur:
                runs.append(cur)
            cur = None
    if cur:
        runs.append(cur)
    longest = max(r[1] for r in runs)
    start = next(r[0] for r in runs if r[1] >= min(3, longest))
    return TableSpec(d, dc, spice, tuple(sorted(C)), tuple(sorted(cols)), start, maxw)


_TIME_NAME = re.compile(r"^(t|x|time|times|timestamp|time ?stamp|sec|secs|second|seconds|x-axis|xaxis|x axis|zeit|시간|"
                        r"relative ?time|elapsed ?time|time ?\(.*\))$", re.I)
_INDEX_NAME = re.compile(r"^(index|idx|no\.?|#|n|num|number|samples?|sample ?(no|num|number|#|index)|points?|pt|seq|"
                         r"sequence|x ?sequence|count)$", re.I)
_CURRENT_NAME = re.compile(r"(^|[^a-z])(i|ix|id|is|ib|ic|ie|ig)\(|current|curr|\bamps?\b|ampere|^i$|(^|[/:.\s])i[_\d]|"
                           r"ibat|idd|icc|iss|isup|ivdd|iload|i_load|전류", re.I)
_VOLTAGE_NAME = re.compile(r"(^|[^a-z])v\(|volt|^v$|^v[_ \d]|vdd|vbat|전압", re.I)


def is_time_name(n: str) -> bool:
    n = n.strip().lower().lstrip("#").strip()
    return bool(_TIME_NAME.match(n)) or n.startswith("time")


def is_current(c: Column) -> bool:
    k, _ = unit_info(c.unit)
    if c.kind == "current" or k == "current":
        return True
    if k in ("voltage", "power", "time") or c.kind == "voltage":
        return False
    return bool(_CURRENT_NAME.search(c.name))


def is_voltage(c: Column) -> bool:
    k, _ = unit_info(c.unit)
    if c.kind == "voltage" or k == "voltage":
        return True
    if k is not None or c.kind == "current" or _CURRENT_NAME.search(c.name):
        return False
    return bool(_VOLTAGE_NAME.search(c.name))


def _consecutive_ints(v: np.ndarray) -> bool:
    v = v[np.isfinite(v)][:2000]
    if len(v) < 3:
        return False
    return bool(np.all(v == np.round(v)) and np.all(np.diff(v) == 1))


def _increasing(v: np.ndarray) -> bool:
    v = v[np.isfinite(v)]
    if len(v) < 2:
        return False
    d = np.diff(v)
    return bool(np.mean(d > 0) >= 0.99)


def _ws_header_tokens(line: str, nfields: int):
    toks = line.split()
    if len(toks) == nfields:
        return toks
    t2 = [x for x in re.split(r"\s{2,}|\t", line.strip()) if x]
    if len(t2) == nfields:
        return t2
    merged = []
    for tk in toks:
        if merged and re.fullmatch(r"[\(\[\{].*[\)\]\}]", tk):
            merged[-1] += " " + tk
        else:
            merged.append(tk)
    return merged


def meta_from_lines(lines, d):
    meta = {}
    for line in lines:
        s = strip_comment(line).strip() if isinstance(line, str) else ""
        if isinstance(line, list):
            toks = [x for x in line if x]
        else:
            if not s:
                continue
            m = re.match(r"^([A-Za-z][\w .\-/()#]*?)\s*[:=]\s*(.+?)\s*$", s)
            if m and (d in (None, "ws") or d not in s):
                meta.setdefault(m.group(1).strip(), m.group(2).strip())
                continue
            toks = [x for x in (tok_line(s, d) if d else s.split()) if x]
        if len(toks) >= 2 and to_num(toks[0]) is None:
            meta.setdefault(toks[0], toks[1])
            if len(toks) >= 4 and len(toks) % 2 == 0 and all(to_num(toks[j]) is None for j in range(0, len(toks), 2)):
                for j in range(0, len(toks), 2):
                    meta.setdefault(toks[j], toks[j + 1])
    return meta


def analyze_header(above, cols, d, nfields):
    """데이터 바로 위 줄들에서 열 이름/단위/메타데이터를 찾는다. above: 원본 줄(또는 셀 목록) 리스트."""
    names, units, meta = {}, {}, {}
    xy = False
    cand = []
    for ln in above:
        if isinstance(ln, list):
            toks = [str(x) for x in ln]
        else:
            s = strip_comment(ln) if is_comment(ln) else ln
            if not s.strip() or re.fullmatch(r"[\s\-=_*~#.+|]+", s) or re.match(r"\s*Step Information", s, re.I):
                continue
            if d == "ws" or (d not in s and d != "ws"):
                toks = _ws_header_tokens(s, nfields)   # 탭 파일인데 헤더만 공백으로 정렬된 경우 등
            else:
                toks = tok_line(s, d)
        if any(t for t in toks):
            cand.append(toks)
    cand = cand[-8:]

    def at(r, j):
        return r[j].strip() if j < len(r) and r[j] is not None else ""

    def unit_score(r):
        toks = [at(r, j) for j in cols if at(r, j)]
        if not toks:
            return 0.0
        return sum(1 for x in toks if unit_info(x)[0]) / len(toks)

    def is_xy(r):
        toks = [x for x in r if x.strip()]
        return bool(toks) and all(x.strip().upper() in ("X", "Y") for x in toks)

    pos = len(cand) - 1
    unit_row = name_row = None
    skipped = 0
    while pos >= 0 and skipped <= 3:
        r = cand[pos]
        shape_ok = (len(r) == nfields) if d == "ws" else True
        if unit_row is None and name_row is None and shape_ok and unit_score(r) >= 0.5:
            unit_row = pos
            pos -= 1
            continue
        if is_xy(r):
            xy = True
            pos -= 1
            continue
        vals = [at(r, j) for j in cols]
        nonempty = sum(1 for x in vals if x)
        numeric = sum(1 for x in vals if x and to_num(x) is not None)
        if shape_ok and nonempty >= max(1, len(cols) / 2) and (numeric < nonempty or unit_row is not None):
            name_row = pos
            break
        if unit_row is None:
            skipped += 1
            pos -= 1
            continue
        break
    if not xy:
        xy = any(is_xy(r) for r in cand)
    used = set()
    if unit_row is not None:
        used.add(unit_row)
        for j in cols:
            u = at(cand[unit_row], j)
            if unit_info(u)[0]:
                units[j] = u.strip("()[]{} ")
    if name_row is not None:
        used.add(name_row)
        for j in cols:
            nm = at(cand[name_row], j)
            if not nm:
                continue
            if unit_row is None:
                base, u = split_name_unit(nm)
                if u:
                    units[j] = u
                    nm = base
            else:
                extra = at(cand[unit_row], j)
                if extra and not unit_info(extra)[0]:
                    nm = f"{nm} {extra}"
            names[j] = nm
        # Rigol처럼 이름행 아래에 값이 정렬된 메타데이터
        below = cand[unit_row] if unit_row is not None and unit_row > name_row else None
        if below is not None:
            for j in range(len(cand[name_row])):
                if j in cols:
                    continue
                k, v = at(cand[name_row], j), at(below, j)
                if k and v and to_num(k) is None:
                    meta[k] = v
    meta.update({k: v for k, v in meta_from_lines(above, d).items() if k not in meta})
    return names, units, meta, xy


_NK = lambda k: re.sub(r"[^a-z0-9]", "", str(k).lower())  # noqa: E731
DT_KEYS = {"sampleinterval", "samplinginterval", "xincrement", "xincr", "xinc", "increment", "horizinterval",
           "horizontalinterval", "timeperpoint", "timeperpt", "timestep", "dt", "deltat", "sampleperiod",
           "samplingperiod", "timeinterval", "tint", "wfincrement", "sampletime"}
RATE_KEYS = {"samplerate", "samplingrate", "samplingfrequency", "samplefrequency", "fs", "samplespersecond", "sps",
             "acquisitionrate", "srate"}
T0_KEYS = {"start", "starttime", "xorigin", "xzero", "horizoffset", "horizontaloffset", "timeoffset", "t0",
           "firsttime", "xstart", "wfstartoffset"}
VUNIT_KEYS = {"verticalunits", "verticalunit", "yunits", "yunit", "vertunit", "vunit", "unit", "units", "unitstring"}
HUNIT_KEYS = {"horizontalunits", "horizontalunit", "xunits", "xunit", "horunit", "hunit", "timeunit", "timeunits"}


def timebase_from_meta(meta: dict):
    dt = t0 = vunit = hunit = None
    for k, v in meta.items():
        key = _NK(k)
        try:
            if dt is None and key in DT_KEYS:
                dt = parse_quantity(v, "time")
            elif dt is None and key in RATE_KEYS:
                fs = parse_quantity(v, "freq")
                dt = 1.0 / fs if fs else None
            elif t0 is None and key in T0_KEYS:
                t0 = parse_quantity(v, "time")
        except (UserInputError, ValueError, TypeError):
            pass
        if vunit is None and key in VUNIT_KEYS and isinstance(v, str) and unit_info(v)[0]:
            vunit = v.strip().strip('"')
        if hunit is None and key in HUNIT_KEYS and isinstance(v, str) and unit_info(v)[0]:
            hunit = v.strip().strip('"')
    if dt is not None and not dt > 0:
        dt = None
    return dt, t0 or 0.0, vunit, hunit


def _bulk_python(lines_iter, spec):
    rows = []
    nanrow = [np.nan] * len(spec.cols)
    for ln in lines_iter:
        if spec.delim is not None:
            if not ln.strip():
                continue
            if is_comment(ln):
                rows.append(nanrow)
                continue
            toks = tok_line(ln, spec.delim)
        else:
            toks = ln
            if not any(toks):
                continue
        row = []
        for j in spec.cols:
            v = to_num(toks[j], spec.dc, spec.spice) if j < len(toks) and toks[j] else None
            row.append(np.nan if v is None else v)
        rows.append(row)
    return np.array(rows, dtype=float).reshape(-1, len(spec.cols))


def _bulk_pandas(buf, spec, encoding="utf-8"):
    """buf: str 또는 bytes (데이터 시작 위치부터)."""
    import pandas as pd  # noqa
    sep = r"\s+" if spec.delim == "ws" else spec.delim
    width = max(spec.maxfields, max(spec.cols) + 1)
    src = io.BytesIO(buf) if isinstance(buf, (bytes, bytearray, memoryview)) else io.StringIO(buf)
    kw = dict(sep=sep, header=None, names=list(range(width)), engine="c", skip_blank_lines=True,
              on_bad_lines="skip", skipinitialspace=True, dtype=str if spec.dc else None, low_memory=False,
              quotechar='"')
    if isinstance(src, io.BytesIO):
        kw.update(encoding=encoding, encoding_errors="replace")
    df = pd.read_csv(src, **kw)
    out = np.empty((len(df), len(spec.cols)))
    for k, j in enumerate(spec.cols):
        s = df[j]
        if s.dtype == object or spec.dc or str(s.dtype) in ("str", "string"):
            s = s.astype(str).str.strip()
            if spec.dc:
                s = s.str.replace(",", ".", regex=False)
            s = pd.to_numeric(s, errors="coerce")
        out[:, k] = s.to_numpy(dtype=float)
    return out


def _runs_of(valid):
    runs = []
    idx = np.nonzero(np.diff(np.concatenate([[0], valid.astype(np.int8), [0]])))[0]
    for a, b in zip(idx[0::2], idx[1::2]):
        runs.append((int(a), int(b)))
    return runs


def split_on_resets(t: np.ndarray):
    """시간이 되돌아가는 위치에서 나눈 (시작, 끝) 인덱스 목록."""
    if len(t) < 2:
        return [(0, len(t))]
    d = np.diff(t)
    if np.all(d < 0):
        return [(0, len(t))]
    cuts = np.nonzero(d < 0)[0] + 1
    bounds = [0, *cuts.tolist(), len(t)]
    return [(a, b) for a, b in zip(bounds[:-1], bounds[1:]) if b > a]


def table_to_parsed(lines, spec: TableSpec, bulk: np.ndarray, fmt: str, labels: Optional[list] = None,
                    nfields: int = 0) -> Parsed:
    above = lines[:spec.start]
    names, units, meta, xy = analyze_header(above, spec.cols, spec.delim, nfields)
    # Tek 구형 CSV: 데이터 줄 앞쪽 열에 메타데이터
    rows_head = [tok_line(ln, spec.delim) if spec.delim else ln for ln in lines[spec.start:spec.start + 200]]
    for r in rows_head:
        if not r or (spec.delim and isinstance(r, str)):
            continue
        for j in range(len(r) - 1):
            if j in spec.cols or (j + 1) in spec.cols:
                continue
            k, v = str(r[j]).strip(), str(r[j + 1]).strip()
            if k and v and to_num(k) is None:
                meta.setdefault(k, v)
    dt, t0, vunit, hunit = timebase_from_meta(meta)
    core_pos = [spec.cols.index(j) for j in spec.core]
    valid = ~np.isnan(bulk[:, core_pos]).any(axis=1)
    runs = _runs_of(valid)
    if not runs:
        raise UserInputError("파일에서 숫자 데이터를 찾지 못했습니다.")
    longest = max(b - a for a, b in runs)
    thr = max(min(3, longest), int(0.005 * longest))
    runs = [(a, b) for a, b in runs if b - a >= thr]
    data = np.concatenate([bulk[a:b] for a, b in runs])
    run_bounds = np.cumsum([0] + [b - a for a, b in runs])

    cols = []
    first = data[: run_bounds[1]]
    for k, j in enumerate(spec.cols):
        nm = names.get(j) or f"col{j + 1}"
        cols.append(Column(nm, data[:, k], units.get(j)))
    # VCSV(;Version...) / Cadence 'name X','name Y' 쌍
    pairs = []
    if xy and len(cols) % 2 == 0:
        pairs = [(cols[2 * k], cols[2 * k + 1]) for k in range(len(cols) // 2)]
        sig_names = _vcsv_names(above, len(pairs))
        for k, (cx, cy) in enumerate(pairs):
            if sig_names:
                cy.name = sig_names[k]
    else:
        for k in range(len(cols) - 1):
            mx = re.match(r"^(.*?)[\s_:]*\bX$", cols[k].name)
            my = re.match(r"^(.*?)[\s_:]*\bY$", cols[k + 1].name)
            if mx and my and mx.group(1) == my.group(1):
                cols[k + 1].name = my.group(1) or cols[k + 1].name
                pairs.append((cols[k], cols[k + 1]))
    if pairs:
        xs = [p[0] for p in pairs]
        same = all(np.array_equal(xs[0].values, x.values, equal_nan=True) for x in xs)
        for k, (cx, cy) in enumerate(pairs):
            if same and k == 0:
                cx.role = "time"
                cx.name = cx.name if is_time_name(cx.name) else "X (time)"
            else:
                cx.role = "drop" if same else "x"
                if not same:
                    cy.x, cy.x_unit = cx.values, cx.unit
    else:
        # 인덱스 열 → 시간 열
        for k, c in enumerate(cols):
            u_kind = unit_info(c.unit)[0]
            if _INDEX_NAME.match(c.name.strip()) and u_kind != "time":
                c.role = "index"
        tcol = None
        for k, c in enumerate(cols):
            if c.role != "data":
                continue
            if is_time_name(c.name) or unit_info(c.unit)[0] == "time":
                if unit_info(c.unit)[0] != "time" and _consecutive_ints(first[:, k]) and dt is not None:
                    c.role = "index"
                    continue
                c.role = "time"
                tcol = c
                break
        if tcol is None:
            datacols = [k for k, c in enumerate(cols) if c.role == "data"]
            if len(datacols) >= 2:
                for k in datacols[:2]:
                    if _consecutive_ints(first[:, k]):
                        continue
                    if _increasing(first[:, k]) and not is_current(cols[k]) and not is_voltage(cols[k]):
                        cols[k].role = "time"
                        tcol = cols[k]
                        meta.setdefault("_note_time", f"'{cols[k].name}' 열이 계속 증가해서 시간 열로 가정했습니다.")
                    break
        if tcol is None:
            for k, c in enumerate(cols):
                if c.role == "data" and _consecutive_ints(first[:, k]) and len(cols) > 1:
                    c.role = "index"
        if tcol is not None:
            for c in cols:
                if c is not tcol and c.role == "data" and np.array_equal(c.values, tcol.values, equal_nan=True):
                    c.role = "drop"   # ngspice wrdata 등 반복된 시간 열
    notes = []
    if "_note_time" in meta:
        notes.append(meta.pop("_note_time"))
    tcol = next((c for c in cols if c.role == "time"), None)
    labels = labels or []
    blocks = []
    if tcol is not None:
        seg = split_on_resets(tcol.values[~np.isnan(tcol.values)] if np.isnan(tcol.values).any() else tcol.values)
        if np.isnan(tcol.values).any():
            keep = ~np.isnan(tcol.values)
            for c in cols:
                c.values = c.values[keep]
                if c.x is not None:
                    c.x = c.x[keep]
        for bi, (a, b) in enumerate(seg):
            bc = [Column(c.name, c.values[a:b], c.unit, c.role, c.kind,
                         None if c.x is None else c.x[a:b], c.x_unit) for c in cols]
            label = labels[bi] if len(labels) == len(seg) else f"block {bi + 1}"
            blocks.append(Block(label, bc))
    else:
        for bi in range(len(runs)):
            a, b = run_bounds[bi], run_bounds[bi + 1]
            bc = [Column(c.name, c.values[a:b], c.unit, c.role, c.kind,
                         None if c.x is None else c.x[a:b], c.x_unit) for c in cols]
            label = labels[bi] if len(labels) == len(runs) else f"block {bi + 1}"
            blocks.append(Block(label, bc))
    delim_name = {",": "쉼표", "\t": "탭", ";": "세미콜론", "|": "'|'", "ws": "공백", None: "셀"}[spec.delim]
    fmt2 = f"{fmt} ({delim_name} 구분{', 소수점 쉼표' if spec.dc else ''}{', SPICE 숫자표기' if spec.spice else ''})"
    return Parsed(fmt2, blocks, meta, False, dt, t0, vunit, hunit, notes)


def _vcsv_names(above, n):
    for ln in reversed(above):
        if not isinstance(ln, str) or not ln.lstrip().startswith(";"):
            continue
        toks = [x.strip() for x in ln.lstrip()[1:].split(",") if x.strip()]
        if len(toks) == n and not all(x.upper() in ("X", "Y") for x in toks) and not all(unit_info(x)[0] for x in toks):
            if not toks[0].lower().startswith("version"):
                return toks
    return None


def _step_labels(data: bytes):
    if b"Step Information" not in data:
        return []
    labs = re.findall(rb"(?im)^[ \t]*Step Information:[ \t]*(.+?)[ \t]*(?:\(Run:[^)\r\n]*\))?[ \t]*\r?$", data)
    return [re.sub(r"\s+", " ", x.decode("latin-1")).strip() for x in labs]


BIG_FILE = 2_000_000


def read_table_text(src: Source) -> Parsed:
    data = src.data
    labels = _step_labels(data)
    big = len(data) > BIG_FILE
    if big:  # 큰 파일: 앞부분만 문자열로 풀어서 양식을 알아내고, 본문은 pandas가 바이트에서 바로 읽는다
        cut = data.rfind(b"\n", 0, 4_000_000)
        head_text, enc = decode_bytes(data[: cut + 1 if cut > 0 else 4_000_000])
    else:
        head_text, enc = src.text()
    norm = head_text.replace("\r\n", "\n").replace("\r", "\n")
    lines = norm.split("\n", 3000)[:3000]
    spec = detect_spec(lines)
    if spec is None:
        raise UserInputError(
            f"'{src.name}'에서 숫자 표를 찾지 못했습니다. 지원 양식은 csv/txt/PWL/SPICE raw/스코프 파일 등입니다. "
            "파일 앞부분이 어떤 모양인지 사용자에게 확인해 주세요.")
    nfields = len(tok_line(lines[spec.start], spec.delim))
    bulk = None
    ascii_nl = enc in ("utf-8", "utf-8-sig", "cp949", "latin-1") and b"\r" not in data[:200000].replace(b"\r\n", b"")
    if not spec.spice and big and ascii_nl:
        pos = 0
        for _ in range(spec.start):
            pos = data.index(b"\n", pos) + 1
        if pos == 0 and data.startswith(b"\xef\xbb\xbf"):
            pos = 3
        try:
            bulk = _bulk_pandas(data[pos:], spec, "utf-8" if enc == "utf-8-sig" else enc)
        except Exception as e:  # pandas 없음/파싱 실패 → 파이썬으로
            print(f"pandas 빠른 경로 실패, 파이썬으로 읽습니다: {e}", file=__import__("sys").stderr)
            bulk = None
    if bulk is None:
        text = src.text()[0].replace("\r\n", "\n").replace("\r", "\n") if big else norm
        offset = sum(len(ln) + 1 for ln in lines[:spec.start])
        body = text[offset:]
        if not spec.spice and len(body) > 200000:
            try:
                bulk = _bulk_pandas(body, spec)
            except Exception:
                bulk = None
        if bulk is None:
            bulk = _bulk_python(body.split("\n"), spec)
    fmt = "CSV" if src.ext == ".csv" else "텍스트 표"
    p = table_to_parsed(lines, spec, bulk, fmt, labels, nfields)
    if enc not in ("utf-8", "utf-8-sig"):
        p.notes.append(f"파일 인코딩: {enc}")
    return p


# ─────────────────────────────────────────────────────────────────────────────
# 엑셀
# ─────────────────────────────────────────────────────────────────────────────
def _cell(c):
    if c is None:
        return ""
    if isinstance(c, float):
        return "" if c != c else repr(c)
    if isinstance(c, (int, np.integer)) and not isinstance(c, bool):
        return str(int(c))
    if isinstance(c, (np.floating,)):
        return "" if c != c else repr(float(c))
    return str(c).strip()


def read_excel(src: Source, sheet=None) -> Parsed:
    try:
        import pandas as pd
    except ImportError:
        raise UserInputError("엑셀 파일을 읽으려면 pandas와 openpyxl이 필요합니다 (pip install pandas openpyxl). CSV로 저장해서 주셔도 됩니다.")
    try:
        sheets = pd.read_excel(io.BytesIO(src.data), sheet_name=None, header=None, dtype=object)
    except ImportError as e:
        raise UserInputError(f"엑셀 읽기 패키지가 없습니다: {e}. (xlsx: openpyxl, xls: xlrd, ods: odfpy) CSV로 저장해서 주셔도 됩니다.")
    names = list(sheets.keys())
    if sheet is not None and str(sheet).strip() != "":
        s = str(sheet).strip()
        if s.isdigit() and 1 <= int(s) <= len(names) and s not in names:
            order = [names[int(s) - 1]]
        elif s in names:
            order = [s]
        else:
            raise UserInputError(f"시트 '{sheet}'가 없습니다. 시트 목록: {', '.join(map(str, names))}")
    else:
        order = names
    last_err = None
    for sn in order:
        df = sheets[sn]
        rows = [[_cell(c) for c in r] for r in df.itertuples(index=False, name=None)]
        if not rows:
            continue
        spec = detect_spec(rows[:3000], delims=(None,))
        if spec is None:
            last_err = f"시트 '{sn}'에 숫자 표가 없습니다"
            continue
        bulk = _bulk_python(rows[spec.start:], spec)
        p = table_to_parsed(rows, spec, bulk, f"엑셀 시트 '{sn}'", None, len(rows[spec.start]))
        if len(names) > 1:
            p.notes.append(f"시트 목록: {', '.join(map(str, names))} (사용: '{sn}', 다른 시트는 sheet로 지정)")
        return p
    raise UserInputError(last_err or "엑셀 파일에 데이터가 없습니다.")


# ─────────────────────────────────────────────────────────────────────────────
# 이름 붙은 배열들 (npz / npy / mat / h5 / json / parquet / feather / tdms)
# ─────────────────────────────────────────────────────────────────────────────
def from_named_arrays(arrays: dict, meta: dict, fmt: str, units: Optional[dict] = None,
                      si: bool = False) -> Parsed:
    units = units or {}
    cols, notes = [], []
    for name, a in arrays.items():
        try:
            a = np.asarray(a)
        except Exception:
            continue
        if a.dtype.kind not in "biuf" or a.size == 0:
            continue
        if a.size == 1:
            meta.setdefault(name, float(a.reshape(-1)[0]))
            continue
        a = np.squeeze(a)
        if a.ndim == 1:
            cols.append(Column(str(name), a.astype(float), units.get(name)))
        elif a.ndim == 2:
            if a.shape[0] < a.shape[1] and a.shape[0] <= 16:
                a = a.T
            if a.shape[1] <= 16:
                for j in range(a.shape[1]):
                    cols.append(Column(f"{name}[:,{j}]", a[:, j].astype(float), units.get(name)))
            else:
                notes.append(f"'{name}'은 {a.shape} 크기의 2차원 배열이라 건너뛰었습니다.")
    if not cols:
        raise UserInputError(f"{fmt}에서 숫자 배열을 찾지 못했습니다.")
    for c in cols:
        if c.unit is None:
            base, u = split_name_unit(c.name)
            if u:
                c.unit = u
    lens = Counter(len(c.values) for c in cols)
    time_lens = [len(c.values) for c in cols if is_time_name(c.name.split("/")[-1].split(".")[-1])]
    L = time_lens[0] if time_lens else lens.most_common(1)[0][0]
    skipped = [c.name for c in cols if len(c.values) != L]
    cols = [c for c in cols if len(c.values) == L]
    if skipped:
        notes.append(f"길이가 다른 배열은 제외했습니다: {', '.join(skipped[:10])}")
    tcol = None
    for c in cols:
        leaf = re.split(r"[/.]", c.name)[-1]
        if is_time_name(leaf) or unit_info(c.unit)[0] == "time":
            c.role, tcol = "time", c
            break
    if tcol is None and len(cols) >= 2 and _increasing(cols[0].values) and not _consecutive_ints(cols[0].values) \
            and not is_current(cols[0]):
        cols[0].role, tcol = "time", cols[0]
        notes.append(f"'{cols[0].name}'이 계속 증가해서 시간 열로 가정했습니다.")
    dt, t0, vunit, hunit = timebase_from_meta(meta)
    blocks = []
    if tcol is not None:
        seg = split_on_resets(tcol.values)
        for bi, (a, b) in enumerate(seg):
            blocks.append(Block(f"block {bi + 1}", [Column(c.name, c.values[a:b], c.unit, c.role, c.kind) for c in cols]))
    else:
        blocks.append(Block("block 1", cols))
    return Parsed(fmt, blocks, meta, si, dt, t0, vunit, hunit, notes)


def _flatten(obj, prefix="", out=None):
    out = {} if out is None else out
    if isinstance(obj, dict):
        for k, v in obj.items():
            _flatten(v, f"{prefix}.{k}" if prefix else str(k), out)
    elif isinstance(obj, (list, tuple)) and obj and all(isinstance(x, dict) for x in obj):
        keys = []
        for x in obj:
            for k in x:
                if k not in keys:
                    keys.append(k)
        for k in keys:
            vals = [x.get(k) for x in obj]
            if all(isinstance(v, (int, float)) and not isinstance(v, bool) or v is None for v in vals):
                out[f"{prefix}.{k}" if prefix else str(k)] = np.array([np.nan if v is None else v for v in vals], float)
    elif isinstance(obj, (list, tuple, np.ndarray)):
        try:
            a = np.asarray(obj, dtype=float)
            out[prefix or "data"] = a
        except (ValueError, TypeError):
            pass
    elif isinstance(obj, (int, float, np.integer, np.floating)) and not isinstance(obj, bool):
        out[prefix or "value"] = np.asarray(obj, dtype=float)
    elif isinstance(obj, str) and prefix:
        out.setdefault("__meta__", {})[prefix] = obj
    elif hasattr(obj, "_fieldnames"):  # scipy mat_struct
        _flatten({k: getattr(obj, k) for k in obj._fieldnames}, prefix, out)
    return out


def _split_meta(flat):
    meta = flat.pop("__meta__", {}) if isinstance(flat.get("__meta__"), dict) else {}
    return flat, meta


def read_json(src: Source) -> Parsed:
    obj = json.loads(src.text()[0])
    flat, meta = _split_meta(_flatten(obj))
    return from_named_arrays(flat, meta, "JSON")


def read_npy(src: Source) -> Parsed:
    a = np.load(io.BytesIO(src.data), allow_pickle=False)
    return from_named_arrays({"data": a}, {}, "NumPy .npy")


def read_npz(src: Source) -> Parsed:
    with np.load(io.BytesIO(src.data), allow_pickle=False) as f:
        arrays = {k: f[k] for k in f.files}
    meta = {}
    if "meta" in arrays and arrays["meta"].dtype.kind in "US":
        try:
            m = json.loads(str(arrays.pop("meta")))
            meta.update({k: v for k, v in m.items() if isinstance(v, (str, int, float))})
        except ValueError:
            pass
    strs = {k: str(v) for k, v in arrays.items() if v.dtype.kind in "US" and v.size == 1}
    meta.update(strs)
    arrays = {k: v for k, v in arrays.items() if k not in strs}
    if set(arrays) >= {"t", "i"} and "current_profile" in str(meta.get("format", "")) or set(arrays) == {"t", "i"}:
        return from_named_arrays({"t": arrays["t"], "i": arrays["i"]}, meta, "NumPy .npz", si=True)
    return from_named_arrays(arrays, meta, "NumPy .npz")


def read_mat(src: Source) -> Parsed:
    try:
        import scipy.io
    except ImportError:
        raise UserInputError("MATLAB .mat 파일을 읽으려면 scipy가 필요합니다 (pip install scipy). CSV로 저장해서 주셔도 됩니다.")
    try:
        m = scipy.io.loadmat(io.BytesIO(src.data), squeeze_me=True, struct_as_record=False)
    except NotImplementedError:
        return read_hdf5(src)
    m = {k: v for k, v in m.items() if not k.startswith("__")}
    flat, meta = _split_meta(_flatten(m))
    return from_named_arrays(flat, meta, "MATLAB .mat")


def read_hdf5(src: Source) -> Parsed:
    try:
        import h5py
    except ImportError:
        raise UserInputError("HDF5/MATLAB v7.3 파일을 읽으려면 h5py가 필요합니다 (pip install h5py). CSV로 저장해서 주셔도 됩니다.")
    arrays, units, meta = {}, {}, {}
    with h5py.File(io.BytesIO(src.data), "r") as f:
        def visit(name, obj):
            if isinstance(obj, h5py.Dataset) and obj.dtype.kind in "biuf" and obj.size <= 50_000_000:
                arrays[name] = obj[()]
                for k, v in obj.attrs.items():
                    if isinstance(v, bytes):
                        v = v.decode("latin-1")
                    if _NK(k) in ("units", "unit"):
                        units[name] = str(v)
                    elif isinstance(v, (int, float, np.integer, np.floating, str)):
                        meta.setdefault(k, v if isinstance(v, str) else float(v))
        f.visititems(visit)
        for k, v in f.attrs.items():
            if isinstance(v, (int, float, np.integer, np.floating)):
                meta.setdefault(k, float(v))
    return from_named_arrays(arrays, meta, "HDF5", units)


def read_parquet(src: Source) -> Parsed:
    try:
        import pandas as pd
        df = pd.read_parquet(io.BytesIO(src.data))
    except ImportError:
        raise UserInputError("Parquet 파일을 읽으려면 pandas와 pyarrow가 필요합니다. CSV로 저장해서 주셔도 됩니다.")
    return from_named_arrays({c: df[c].to_numpy() for c in df.columns}, {}, "Parquet")


def read_feather(src: Source) -> Parsed:
    try:
        import pandas as pd
        df = pd.read_feather(io.BytesIO(src.data))
    except ImportError:
        raise UserInputError("Feather 파일을 읽으려면 pandas와 pyarrow가 필요합니다. CSV로 저장해서 주셔도 됩니다.")
    return from_named_arrays({c: df[c].to_numpy() for c in df.columns}, {}, "Feather")


def read_tdms(src: Source) -> Parsed:
    try:
        from nptdms import TdmsFile
    except ImportError:
        raise UserInputError("NI TDMS 파일을 읽으려면 npTDMS가 필요합니다 (pip install npTDMS). CSV로 저장해서 주셔도 됩니다.")
    f = TdmsFile.read(io.BytesIO(src.data))
    arrays, units, meta = {}, {}, {}
    for g in f.groups():
        for ch in g.channels():
            nm = f"{g.name}/{ch.name}"
            arrays[nm] = ch[:]
            p = ch.properties
            if "unit_string" in p:
                units[nm] = str(p["unit_string"])
            if "wf_increment" in p:
                meta.setdefault("wf_increment", float(p["wf_increment"]))
            if "wf_start_offset" in p:
                meta.setdefault("wf_start_offset", float(p["wf_start_offset"]))
    return from_named_arrays(arrays, meta, "NI TDMS", units)


# ─────────────────────────────────────────────────────────────────────────────
# SPICE raw (ngspice / LTspice / Xyce / Spectre nutmeg)
# ─────────────────────────────────────────────────────────────────────────────
def _raw_header(text):
    h = {"title": "", "plotname": "", "flags": "", "command": "", "nvars": 0, "npoints": 0, "vars": []}
    invars = False
    for ln in text.replace("\r", "").split("\n"):
        if invars:
            parts = ln.split()
            if len(parts) >= 2 and parts[0].isdigit():
                h["vars"].append((parts[1], parts[2] if len(parts) > 2 else ""))
                continue
            invars = False
        key, _, val = ln.partition(":")
        k = key.strip().lower()
        v = val.strip()
        if k == "variables":
            invars = True
        elif k == "title":
            h["title"] = v
        elif k == "plotname":
            h["plotname"] = v
        elif k == "flags":
            h["flags"] = v.lower()
        elif k == "command":
            h["command"] = v
        elif k == "no. variables":
            h["nvars"] = int(v.split()[0])
        elif k == "no. points":
            h["npoints"] = int(v.split()[0])
    if not h["nvars"]:
        h["nvars"] = len(h["vars"])
    return h


def read_spice_raw(src: Source) -> Parsed:
    d = src.data
    pos, plots, notes = 0, [], []
    while pos < len(d) - 16:
        while pos < len(d) and d[pos:pos + 1] in (b"\n", b"\r", b" ", b"\x00"):
            if d[pos:pos + 1] == b"\x00" and pos + 1 < len(d) and d[pos + 1:pos + 2] != b"\x00":
                break
            pos += 1
        utf16 = d[pos + 1:pos + 2] == b"\x00"
        enc = "utf-16-le" if utf16 else "latin-1"
        found = []
        for mk in ("Binary:", "Values:"):
            k = d.find(mk.encode(enc), pos)
            if k >= 0:
                found.append((k, mk))
        if not found:
            break
        k, mk = min(found)
        h = _raw_header(d[pos:k].decode(enc, errors="replace"))
        body = k + len(mk.encode(enc))
        for nl in ("\r", "\n"):
            if d[body:body + len(nl.encode(enc))] == nl.encode(enc):
                body += len(nl.encode(enc))
        nv, npts, flags = h["nvars"], h["npoints"], h["flags"]
        if nv < 1 or len(h["vars"]) < nv:
            raise UserInputError("SPICE raw 헤더의 변수 목록을 읽지 못했습니다.")
        cplx = "complex" in flags
        if mk == "Binary:":
            rem = len(d) - body
            lt = utf16 or "ltspice" in h["command"].lower() or "linear technology" in h["command"].lower()
            if cplx:
                rowsz, layout = 16 * nv, "c16"
            elif "double" in flags:
                rowsz, layout = 8 * nv, "f8"
            else:
                sz8, szlt = 8 * nv, 8 + 4 * (nv - 1)
                if lt and npts * szlt <= rem:
                    rowsz, layout = szlt, "lt"
                elif npts * sz8 <= rem:
                    rowsz, layout = sz8, "f8"
                elif npts * szlt <= rem:
                    rowsz, layout = szlt, "lt"
                else:
                    raise UserInputError("SPICE raw 파일의 데이터 길이가 헤더와 맞지 않습니다 (파일이 잘렸을 수 있음).")
            if npts == 0:
                npts = rem // rowsz
            raw = d[body: body + npts * rowsz]
            fast = "fastaccess" in flags
            if layout == "c16":
                a = np.frombuffer(raw, "<c16").reshape((nv, npts) if fast else (npts, nv))
                arr = (a.T if fast else a).real.astype(float)
            elif layout == "f8":
                a = np.frombuffer(raw, "<f8").reshape((nv, npts) if fast else (npts, nv))
                arr = (a.T if fast else a).astype(float)
            else:
                arr = np.empty((npts, nv))
                if fast:
                    arr[:, 0] = np.frombuffer(raw[:8 * npts], "<f8")
                    rest = np.frombuffer(raw[8 * npts:], "<f4").reshape(nv - 1, npts)
                    arr[:, 1:] = rest.T
                else:
                    dt = np.dtype([("t", "<f8")] + [(f"v{j}", "<f4") for j in range(nv - 1)])
                    rec = np.frombuffer(raw, dt)
                    arr[:, 0] = rec["t"]
                    for j in range(nv - 1):
                        arr[:, j + 1] = rec[f"v{j}"]
                arr[:, 0] = np.abs(arr[:, 0])  # LTspice는 시간 부호를 플래그로 쓴다
            pos = body + npts * rowsz
        else:
            rest = d[body:].decode(enc, errors="replace")
            m = re.search(r"\n\s*(Title|Plotname)\s*:", rest)
            chunk = rest[: m.start()] if m else rest
            toks = chunk.split()
            per = nv + 1
            if npts == 0:
                npts = len(toks) // per
            toks = toks[: npts * per]
            if len(toks) < npts * per:
                raise UserInputError("SPICE raw(ASCII) 데이터 개수가 헤더와 맞지 않습니다.")
            vals = np.array([float(t.split(",")[0]) for t in toks], float).reshape(npts, per)
            arr = vals[:, 1:]
            pos = body + len(chunk.encode(enc)) if m else len(d)
        plots.append((h, arr))
    if not plots:
        raise UserInputError("SPICE raw 파일에서 데이터를 찾지 못했습니다.")
    tran = [(h, a) for h, a in plots if "transient" in h["plotname"].lower() or h["vars"][0][0].lower() == "time"]
    if not tran:
        kinds = ", ".join(sorted({h['plotname'] for h, _ in plots}))
        raise UserInputError(f"raw 파일에 과도해석(transient) 결과가 없습니다 (있는 것: {kinds}). .tran 결과 파일을 주세요.")
    blocks = []
    for h, arr in tran:
        cols = []
        for j, (nm, typ) in enumerate(h["vars"][:arr.shape[1]]):
            tl = typ.lower()
            kind = "current" if "current" in tl else "voltage" if "voltage" in tl else None
            role = "time" if j == 0 else "data"
            cols.append(Column(nm, arr[:, j], "s" if j == 0 else ("A" if kind == "current" else "V" if kind == "voltage" else None),
                               role, kind))
        seg = split_on_resets(cols[0].values)
        for si, (a, b) in enumerate(seg):
            lab = h["plotname"] or "transient"
            if len(seg) > 1:
                lab = f"step {si + 1}"
            if len(tran) > 1:
                lab = f"{h['title'] or h['plotname']} / {lab}"
            blocks.append(Block(lab, [Column(c.name, c.values[a:b], c.unit, c.role, c.kind) for c in cols]))
    prog = "LTspice" if any("ltspice" in h["command"].lower() for h, _ in plots) else "SPICE"
    return Parsed(f"{prog} raw", blocks, {}, True, notes=notes)


# ─────────────────────────────────────────────────────────────────────────────
# HSPICE CSDF / Cadence PSF ASCII
# ─────────────────────────────────────────────────────────────────────────────
def read_csdf(src: Source) -> Parsed:
    text = src.text()[0]
    mN = re.search(r"#N(.*?)#C", text, re.S)
    if not mN:
        raise UserInputError("CSDF 파일에서 #N(신호 이름) 구역을 찾지 못했습니다.")
    names = re.findall(r"'([^']*)'", mN.group(1)) or mN.group(1).split()
    toks = text[mN.end() - 2:].split()
    rows, k = [], 0
    while k < len(toks):
        if toks[k] == "#C":
            tval = float(toks[k + 1])
            n = int(toks[k + 2])
            vals, j = [], k + 3
            while len(vals) < n and j < len(toks) and not toks[j].startswith("#"):
                vals.append(float(toks[j].split(":")[0]))
                j += 1
            rows.append([tval] + vals)
            k = j
        elif toks[k].startswith("#;"):
            break
        else:
            k += 1
    nv = min(len(r) for r in rows) - 1
    arr = np.array([r[: nv + 1] for r in rows], float)
    cols = [Column("TIME", arr[:, 0], "s", "time")]
    for j in range(nv):
        nm = names[j] if j < len(names) else f"sig{j + 1}"
        kind = "current" if re.match(r"(?i)i\w*\(", nm) else "voltage" if re.match(r"(?i)v\w*\(", nm) else None
        cols.append(Column(nm, arr[:, j + 1], None, "data", kind))
    seg = split_on_resets(arr[:, 0])
    blocks = [Block(f"block {b + 1}", [Column(c.name, c.values[a:e], c.unit, c.role, c.kind) for c in cols])
              for b, (a, e) in enumerate(seg)]
    return Parsed("HSPICE CSDF", blocks, {}, True)


def read_psfascii(src: Source) -> Parsed:
    text = src.text()[0]
    secs = {}
    cur = None
    for ln in text.splitlines():
        s = ln.strip()
        if s in ("HEADER", "TYPE", "SWEEP", "TRACE", "VALUE", "END"):
            cur = s
            secs.setdefault(cur, [])
            continue
        if cur:
            secs[cur].append(s)
    tunits, cur_t = {}, None
    for s in secs.get("TYPE", []):
        m = re.match(r'^"([^"]+)"\s+(FLOAT|INT|COMPLEX|STRUCT|STRING|ARRAY)', s)
        if m:
            cur_t = m.group(1)
        mu = re.search(r'"units"\s+"([^"]*)"', s)
        if mu and cur_t:
            tunits[cur_t] = mu.group(1)
    sweep = None
    for s in secs.get("SWEEP", []):
        m = re.match(r'^"([^"]+)"', s)
        if m:
            sweep = m.group(1)
            break
    trace_type, groups, gname, gleft = {}, {}, None, 0
    for s in secs.get("TRACE", []):
        m = re.match(r'^"([^"]+)"\s+GROUP\s+(\d+)', s)
        if m:
            gname, gleft = m.group(1), int(m.group(2))
            groups[gname] = []
            continue
        m = re.match(r'^"([^"]+)"\s+"([^"]+)"', s)
        if m:
            trace_type[m.group(1)] = m.group(2)
            if gname and gleft > 0:
                groups[gname].append(m.group(1))
                gleft -= 1
    if not sweep:
        raise UserInputError("PSF ASCII 파일에서 SWEEP(시간) 변수를 찾지 못했습니다.")
    data = {}
    npt = 0
    for s in secs.get("VALUE", []):
        m = re.match(r'^"([^"]+)"\s+(.*)$', s)
        if not m:
            continue
        nm, rest = m.group(1), m.group(2)
        nums = re.findall(r"[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?", rest.split("PROP")[0])
        if nm == sweep:
            npt += 1
        if nm in groups:
            for mem, v in zip(groups[nm], nums):
                data.setdefault(mem, []).append(float(v))
        elif nums:
            data.setdefault(nm, []).append(float(nums[0]))
    if sweep not in data:
        raise UserInputError("PSF ASCII 파일에서 VALUE 데이터를 찾지 못했습니다.")
    n = len(data[sweep])
    cols = [Column(sweep, np.array(data[sweep]), "s", "time")]
    for nm, v in data.items():
        if nm == sweep or len(v) != n:
            continue
        typ = trace_type.get(nm, "")
        u = tunits.get(typ)
        kind = "current" if (u and unit_info(u)[0] == "current") or "branch" in typ.lower() or "current" in typ.lower() \
            else "voltage" if (u and unit_info(u)[0] == "voltage") or "node" in typ.lower() or "volt" in typ.lower() else None
        cols.append(Column(nm, np.array(v), u, "data", kind))
    return Parsed("Cadence PSF ASCII", [Block("transient", cols)], {}, True)


# ─────────────────────────────────────────────────────────────────────────────
# PWL (파일 / 넷리스트 / LTspice .asc)
# ─────────────────────────────────────────────────────────────────────────────
_PWL_KW = re.compile(r"(?i)\b(td|delay|time_scale_factor|value_scale_factor|r|trigger|file)\s*=\s*(\"[^\"]*\"|'[^']*'|[^\s()]+)")


def _expand_repeats(s, notes):
    def rep(m):
        body = m.group(2)
        cnt = m.group(1)
        if cnt.lower() == "forever":
            notes.append("REPEAT FOREVER 구간은 한 번만 넣었습니다. 반복은 transform의 repeat로 하세요.")
            n = 1
        else:
            n = int(float(cnt))
        return " __REP%d__ %s __END__ " % (n, body)
    return re.sub(r"(?is)repeat\s+(?:for\s+)?(forever|\d+)\s*\((.*?)\)\s*endrepeat", rep, s)


def parse_pwl_body(s: str, file_mode: bool, notes: list):
    s = "\n".join(re.split(r"[;$]", ln, maxsplit=1)[0] for ln in s.splitlines()
                  if not ln.lstrip().startswith(("*", "#", "//")))
    s = re.sub(r"(?is)^.*?\bpwl\b", "", s, count=1) if re.search(r"(?i)\bpwl\b", s) else s
    s = _expand_repeats(s, notes)
    opts = {k.lower(): v.strip("\"'") for k, v in _PWL_KW.findall(s)}
    s = _PWL_KW.sub(" ", s)
    toks = [t for t in re.split(r"[\s,()]+", s) if t]
    rep_start = []             # 반복 구간은 (시간, 값) 쌍 단위로 전개
    pairs = []                 # (time_token, t, v)
    pending = []
    for tk in toks:
        m = re.match(r"__REP(\d+)__", tk)
        if m:
            rep_start.append((int(m.group(1)), len(pairs)))
            continue
        if tk == "__END__":
            n, at = rep_start.pop()
            sect = pairs[at:]
            del pairs[at:]
            base = pairs[-1][1] if pairs else 0.0
            T = sect[-1][1] if sect else 0.0
            for c in range(n):
                for (ttk, tv, vv) in sect:
                    pairs.append(("", base + c * T + tv, vv))
            notes.append(f"REPEAT 구간({len(sect)}점 × {n}회)을 펼쳤습니다 (LTspice 방식: 구간 안 시간은 반복 시작 기준).")
            continue
        v = spice_number(tk)
        if v is None:
            if tk.lower() == "endrepeat":
                continue
            raise UserInputError(f"PWL에서 숫자로 읽을 수 없는 값이 있습니다: '{tk}'")
        pending.append((tk, v))
        if len(pending) == 2:
            pairs.append((pending[0][0], pending[0][1], pending[1][1]))
            pending = []
    if pending:
        raise UserInputError("PWL 값의 개수가 홀수입니다 (시간, 값 쌍이 맞지 않음).")
    time_toks = [p[0] for p in pairs if p[0]]
    all_plus = bool(time_toks) and all(t.startswith("+") for t in time_toks)
    t_list, v_list = [], []
    last = 0.0
    for ttk, tv, vv in pairs:
        if ttk.startswith("+") and not all_plus and t_list:
            tv = last + tv
        t_list.append(tv)
        v_list.append(vv)
        last = tv
    t = np.array(t_list, float)
    v = np.array(v_list, float)
    if "time_scale_factor" in opts:
        t = t * spice_number(opts["time_scale_factor"])
    if "value_scale_factor" in opts:
        v = v * spice_number(opts["value_scale_factor"])
    for key in ("td", "delay"):
        if key in opts:
            t = t + spice_number(opts[key])
    if "r" in opts:
        notes.append("PWL의 R(반복) 옵션은 무시했습니다. 반복이 필요하면 transform의 repeat를 사용하세요.")
    if "trigger" in opts:
        notes.append("PWL의 TRIGGER 옵션은 무시했습니다.")
    return t, v, opts.get("file")


def _netlist_statements(text):
    out = []
    for ln in text.splitlines():
        s = ln.rstrip()
        if not s.strip() or s.lstrip().startswith("*"):
            continue
        if s.lstrip().startswith("+") and out:
            out[-1] += " " + s.lstrip()[1:]
        else:
            out.append(s)
    return out


def read_pwl(src: Source) -> Parsed:
    text = src.text()[0]
    notes = []
    sources = []
    if src.ext == ".asc":
        for blk in re.split(r"(?m)^SYMBOL\s+", text)[1:]:
            sym = blk.split()[0].lower() if blk.split() else ""
            mi = re.search(r"(?m)^SYMATTR\s+InstName\s+(\S+)", blk)
            mv = re.search(r"(?m)^SYMATTR\s+Value\s+(.*)$", blk)
            if mi and mv and re.search(r"(?i)\bpwl\b", mv.group(1)):
                kind = "current" if "current" in sym or mi.group(1).upper().startswith("I") else "voltage"
                sources.append((mi.group(1), kind, mv.group(1)))
    else:
        for st in _netlist_statements(text):
            m = re.match(r"^\s*([IiVv][^\s]*)\s+(\S+)\s+(\S+)\s+(.*)$", st)
            if m and re.search(r"(?i)\bpwl\b", m.group(4)):
                kind = "current" if m.group(1)[0] in "Ii" else "voltage"
                sources.append((m.group(1), kind, m.group(4)))
    cols = []
    if sources:
        for name, kind, body in sources:
            t, v, fref = parse_pwl_body(body, file_mode=False, notes=notes)
            if fref:
                fp = Path(fref).expanduser()
                if not fp.is_absolute():
                    fp = src.path.parent / fp
                if not fp.is_file():
                    raise UserInputError(f"{name}의 PWL FILE이 가리키는 파일이 없습니다: {fp}")
                t, v, _ = parse_pwl_body(decode_bytes(fp.read_bytes())[0], file_mode=True, notes=notes)
                notes.append(f"{name}: PWL FILE={fp} 를 읽었습니다.")
            cols.append(Column(name, v, "A" if kind == "current" else "V", "data", kind, x=t, x_unit="s"))
        fmt = "LTspice 회로도 PWL" if src.ext == ".asc" else "SPICE 넷리스트 PWL"
    else:
        t, v, _ = parse_pwl_body(text, file_mode=True, notes=notes)
        cols.append(Column("PWL", v, None, "data", None, x=t, x_unit="s"))
        notes.append("PWL 파일 값은 SI 단위(초, A)로 해석했습니다.")
        fmt = "PWL 파일"
    if not cols or all(len(c.values) < 2 for c in cols):
        raise UserInputError("PWL에서 (시간, 값) 쌍을 2개 이상 찾지 못했습니다.")
    return Parsed(fmt, [Block("pwl", cols)], {}, True, notes=notes)


# ─────────────────────────────────────────────────────────────────────────────
# 스코프 바이너리: Tektronix ISF / LeCroy TRC / Keysight BIN
# ─────────────────────────────────────────────────────────────────────────────
def read_isf(src: Source) -> Parsed:
    d = src.data
    m = re.search(rb"[:;]?CURV[A-Z]*\s+", d)
    if not m:
        raise UserInputError("Tektronix ISF 파일에서 CURVE 데이터를 찾지 못했습니다.")
    hdr = d[: m.start()].decode("latin-1")
    p = {}
    for part in hdr.split(";"):
        part = re.sub(r"^(?:[A-Za-z]+:)+", "", part.strip().lstrip(":"))
        kv = part.split(None, 1)
        if len(kv) == 2:
            p[kv[0].upper()] = kv[1].strip().strip('"')

    def g(*keys, default=None):
        for k in keys:
            if k in p:
                return p[k]
        return default
    enc = (g("ENCDG", "ENC", default="BIN") or "BIN").upper()
    nb = int(float(g("BYT_NR", "BYT_N", default="2")))
    bfmt = (g("BN_FMT", "BN_F", default="RI") or "RI").upper()
    order = (g("BYT_OR", "BYT_O", default="MSB") or "MSB").upper()
    xincr = float(g("XINCR", "XIN"))
    xzero = float(g("XZERO", "XZE", default="0"))
    ptoff = float(g("PT_OFF", "PT_O", default="0"))
    ymult = float(g("YMULT", "YMU"))
    yoff = float(g("YOFF", "YOF", default="0"))
    yzero = float(g("YZERO", "YZE", default="0"))
    xunit = g("XUNIT", "XUN", default="s")
    yunit = g("YUNIT", "YUN")
    after = m.end()
    if enc.startswith("ASC"):
        raw = np.array([float(x) for x in re.split(r"[,\s]+", d[after:].decode("latin-1").strip()) if x], float)
    else:
        if d[after:after + 1] != b"#":
            raise UserInputError("Tektronix ISF 바이너리 블록 형식을 읽지 못했습니다.")
        nd = int(chr(d[after + 1]))
        L = int(d[after + 2: after + 2 + nd]) if nd else len(d) - after - 2
        start = after + 2 + nd
        buf = d[start: start + L]
        dt = ("f" if bfmt == "FP" else "i" if bfmt == "RI" else "u") + str(nb)
        dt = (">" if order.startswith("MSB") else "<") + dt
        raw = np.frombuffer(buf[: len(buf) // nb * nb], dtype=dt).astype(float)
    y = (raw - yoff) * ymult + yzero
    t = xzero + (np.arange(len(y)) - ptoff) * xincr
    notes = []
    if (g("PT_FMT", "PT_F", default="Y") or "Y").upper().startswith("ENV"):
        notes.append("envelope(최소/최대 쌍) 형식이라 값이 번갈아 최소/최대입니다.")
    name = g("WFID", default="CH")
    name = name.split(",")[0] if name else "CH"
    cols = [Column("time", t, xunit, "time"), Column(name, y, yunit)]
    return Parsed("Tektronix ISF", [Block("waveform", cols)], p, False, notes=notes)


def read_trc(src: Source) -> Parsed:
    d = src.data
    p = d.find(b"WAVEDESC")
    le = struct.unpack_from("<h", d, p + 34)[0] == 1
    e = "<" if le else ">"
    U = lambda f, off: struct.unpack_from(e + f, d, p + off)[0]  # noqa: E731
    comm_type = U("h", 32)
    wd, ut, rd1, tt, rt, ra1, wa1 = (U("l", 36), U("l", 40), U("l", 44), U("l", 48), U("l", 52), U("l", 56),
                                     U("l", 60))
    nsub = max(1, U("l", 144))
    gain, voff = U("f", 156), U("f", 160)
    hint, hoff = U("f", 176), U("d", 180)
    vunit = d[p + 196: p + 244].split(b"\x00")[0].decode("latin-1").strip()
    hunit = d[p + 244: p + 292].split(b"\x00")[0].decode("latin-1").strip()
    start = p + wd + ut + rd1 + tt + rt + ra1
    if start + wa1 > len(d):
        start = p + wd + ut + tt + rt
    dtyp = e + ("i1" if comm_type == 0 else "i2")
    raw = np.frombuffer(d[start: start + wa1], dtype=dtyp).astype(float)
    y = gain * raw - voff
    blocks = []
    n = len(y) // nsub
    for s in range(nsub):
        ys = y[s * n:(s + 1) * n]
        t = hoff + np.arange(len(ys)) * hint
        blocks.append(Block(f"segment {s + 1}" if nsub > 1 else "waveform",
                            [Column("time", t, hunit or "s", "time"), Column("CH", ys, vunit or None)]))
    return Parsed("LeCroy TRC", blocks, {}, False)


_AG_UNITS = {1: "V", 2: "s", 4: "A", 6: "Hz"}


def read_keysight_bin(src: Source) -> Parsed:
    d = src.data
    nwf = struct.unpack_from("<i", d, 8)[0]
    pos = 12
    cols, times = [], []
    for w in range(nwf):
        hsz = struct.unpack_from("<i", d, pos)[0]
        wtype, nbuf, npts, count = struct.unpack_from("<4i", d, pos + 4)
        xdo, xinc, xorg = struct.unpack_from("<3d", d, pos + 24)
        xu, yu = struct.unpack_from("<2i", d, pos + 48)
        label = d[pos + 112: pos + 128].split(b"\x00")[0].decode("latin-1").strip() or f"wave{w + 1}"
        pos += hsz
        y = None
        for b in range(nbuf):
            dhs = struct.unpack_from("<i", d, pos)[0]
            btype, bpp = struct.unpack_from("<2h", d, pos + 4)
            bsz = struct.unpack_from("<i", d, pos + 8)[0]
            pos += dhs
            buf = d[pos: pos + bsz]
            pos += bsz
            if y is None:
                if btype in (1, 2, 3, 4):
                    y = np.frombuffer(buf, "<f4" if bpp == 4 else "<f8").astype(float)
                elif btype == 5:
                    y = np.frombuffer(buf, "<i4").astype(float)
                elif btype == 6:
                    y = np.frombuffer(buf, "<u1").astype(float)
        if y is None:
            continue
        t = xorg + np.arange(len(y)) * xinc
        times.append(t)
        cols.append(Column(label, y, _AG_UNITS.get(yu)))
    if not cols:
        raise UserInputError("Keysight BIN 파일에서 파형을 찾지 못했습니다.")
    same = all(len(t) == len(times[0]) and np.allclose(t, times[0]) for t in times)
    if same:
        allc = [Column("time", times[0], "s", "time")] + cols
    else:
        for c, t in zip(cols, times):
            c.x, c.x_unit = t, "s"
        allc = cols
    return Parsed("Keysight/Agilent BIN", [Block("waveform", allc)], {}, False)


# ─────────────────────────────────────────────────────────────────────────────
# 열 선택 → SI 파형 만들기
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class LoadOptions:
    current_col: Any = None
    time_col: Any = None
    time_unit: Optional[str] = None
    current_unit: Optional[str] = None
    dt: Optional[float] = None          # 초
    shunt_ohm: Optional[float] = None
    block: Any = None


def describe_columns(cols, limit=40):
    out = []
    for k, c in enumerate(cols, 1):
        if c.role == "drop":
            continue
        tag = {"time": "시간", "index": "샘플번호", "x": "X축"}.get(c.role)
        if tag is None:
            tag = "전류" if is_current(c) else "전압" if is_voltage(c) else "?"
        u = f", {c.unit}" if c.unit else ""
        out.append(f"{k}: {c.name} [{tag}{u}]")
    if len(out) > limit:
        out = out[:limit] + [f"... (총 {len(out)}개)"]
    return out


def match_col(cols, spec, what="열"):
    s = str(spec).strip()
    if re.fullmatch(r"\d+", s):
        k = int(s)
        if 1 <= k <= len(cols):
            return cols[k - 1]
        raise UserInputError(f"{what} 번호 {k}가 범위를 벗어났습니다 (1~{len(cols)}). 열 목록: {'; '.join(describe_columns(cols))}")
    for f in (lambda c: c.name == s, lambda c: c.name.lower() == s.lower()):
        hit = [c for c in cols if f(c)]
        if len(hit) == 1:
            return hit[0]
    hit = [c for c in cols if s.lower() in c.name.lower()]
    if len(hit) == 1:
        return hit[0]
    raise UserInputError(f"{what} '{spec}'을(를) {'여러 개 찾았습니다' if hit else '찾지 못했습니다'}. "
                         f"열 목록: {'; '.join(describe_columns(cols))}")


def _choose_blocks(blocks, spec):
    if len(blocks) == 1:
        return [(1, blocks[0])]
    labels = [f"{k}: {b.label} ({len(b.columns[0].values):,}점)" for k, b in enumerate(blocks, 1)]
    lab = "; ".join(labels[:30]) + (f" ... (총 {len(labels)}개)" if len(labels) > 30 else "")
    if spec is None or str(spec).strip() == "":
        raise UserInputError(
            f"이 파일에는 데이터 묶음(block)이 {len(blocks)}개 있습니다 (LTspice .step, 여러 세그먼트, 시간이 다시 0부터 시작하는 구간 등): "
            f"{lab}. block에 번호(1부터) 또는 'all'을 지정해 주세요.")
    s = str(spec).strip().lower()
    if s == "all":
        return list(enumerate(blocks, 1))
    if s.isdigit() and 1 <= int(s) <= len(blocks):
        return [(int(s), blocks[int(s) - 1])]
    raise UserInputError(f"block '{spec}'이(가) 올바르지 않습니다. 가능한 값: 1~{len(blocks)} 또는 'all'. 목록: {lab}")


def build_profiles(parsed: Parsed, opts: LoadOptions):
    """반환: [{'label', 't', 'i', 'info', 'warnings'}]"""
    if not parsed.blocks:
        raise UserInputError("파일에서 데이터를 찾지 못했습니다.")
    results = []
    chosen_blocks = _choose_blocks(parsed.blocks, opts.block)
    for bk, block in chosen_blocks:
        cols = block.columns
        tcol = next((c for c in cols if c.role == "time"), None)
        icol = next((c for c in cols if c.role == "index"), None)
        if opts.time_col is not None and str(opts.time_col).strip() != "":
            if str(opts.time_col).strip().lower() == "none":
                if tcol is not None:
                    tcol.role = "data"
                tcol = None
            else:
                c = match_col(cols, opts.time_col, "시간 열")
                if tcol is not None and tcol is not c:
                    tcol.role = "data"
                c.role, tcol = "time", c
                if icol is c:
                    icol = None
        cand = [c for c in cols if c.role == "data"]
        spec = opts.current_col
        warn_all = []
        if spec is None or str(spec).strip() == "":
            cur = [c for c in cand if is_current(c)]
            if len(cur) == 1:
                chosen = cur
            elif not cur and len(cand) == 1:
                chosen = cand
            elif not cur and len([c for c in cand if not is_voltage(c)]) == 1:
                chosen = [c for c in cand if not is_voltage(c)]
            else:
                raise UserInputError(
                    f"전류 열을 하나로 정할 수 없습니다 (후보 {len(cur) or len(cand)}개). current_col에 열 이름이나 번호를 지정해 주세요. "
                    f"열 목록: {'; '.join(describe_columns(cols))}")
        elif str(spec).strip().lower() == "all":
            def usable(c):
                k = unit_info(c.unit)[0]
                if is_current(c):
                    return True
                if k in ("power", "time"):
                    return False
                return not is_voltage(c) or bool(opts.shunt_ohm or opts.current_unit)
            chosen = [c for c in cand if usable(c)]
            skipped = [c.name for c in cand if c not in chosen]
            if skipped:
                warn_all.append(f"전류가 아닌 것으로 보이는 열(전압/전력 등)은 제외했습니다: {', '.join(skipped[:10])}")
            if not chosen:
                raise UserInputError(f"전류로 쓸 수 있는 열이 없습니다. 열 목록: {'; '.join(describe_columns(cols))}")
        else:
            c = match_col(cols, spec, "전류 열")
            if c is tcol:
                raise UserInputError(f"'{c.name}'은(는) 시간 열입니다. 전류 열을 지정해 주세요. 열 목록: {'; '.join(describe_columns(cols))}")
            chosen = [c]
        for c in chosen:
            w = list(warn_all)
            info = {"block": block.label if len(parsed.blocks) > 1 else None, "current_column": c.name}
            # ── 시간 ──
            v = np.asarray(c.values, float)
            if c.x is not None:
                traw, tunit_src = np.asarray(c.x, float), c.x_unit
                info["time_column"] = f"{c.name}의 X축"
            elif tcol is not None:
                traw, tunit_src = np.asarray(tcol.values, float), tcol.unit
                info["time_column"] = tcol.name
            else:
                traw = None
            if traw is not None:
                if opts.dt is not None:
                    w.append("파일에 시간 열이 있어서 dt/sample_rate는 무시했습니다.")
                if opts.time_unit:
                    tf = TIME_UNITS[norm_unit(opts.time_unit)]
                    info["time_unit"] = f"{opts.time_unit} (사용자 지정)"
                elif unit_info(tunit_src)[0] == "time":
                    tf = unit_info(tunit_src)[1]
                    info["time_unit"] = tunit_src
                elif parsed.si:
                    tf = 1.0
                    info["time_unit"] = "s"
                elif unit_info(parsed.hunit)[0] == "time":
                    tf = unit_info(parsed.hunit)[1]
                    info["time_unit"] = f"{parsed.hunit} (파일 메타데이터)"
                else:
                    tf = 1.0
                    info["time_unit"] = "s (가정)"
                    w.append("시간 단위 표기가 없어 초(s)로 가정했습니다. 다르면 time_unit을 지정해 다시 불러오세요.")
                t = traw * tf
            else:
                dt = opts.dt if opts.dt is not None else parsed.dt
                if dt is None:
                    raise UserInputError(
                        "파일에 시간 열이 없습니다. 샘플 간격 dt(예: '1us') 또는 sample_rate(예: '1MHz')를 알려주세요. "
                        f"열 목록: {'; '.join(describe_columns(cols))}")
                src_dt = "사용자 지정" if opts.dt is not None else "파일 메타데이터"
                if icol is not None and len(icol.values) == len(v):
                    idx = np.asarray(icol.values, float)
                    t = parsed.t0 + (idx - idx[np.isfinite(idx)][0]) * dt
                    info["time_column"] = f"{icol.name} × dt({fmt_t(dt)}, {src_dt})"
                else:
                    t = parsed.t0 + np.arange(len(v)) * dt
                    info["time_column"] = f"샘플 번호 × dt({fmt_t(dt)}, {src_dt})"
                info["time_unit"] = "s"
            # ── 전류 ──
            uk, uf = unit_info(c.unit)
            if opts.current_unit:
                k, f = unit_info(opts.current_unit)
                if k != "current":
                    raise UserInputError(f"current_unit '{opts.current_unit}'은(는) 전류 단위가 아닙니다 (A, mA, uA, nA 중 하나).")
                i = v * f
                info["current_unit"] = f"{opts.current_unit} (사용자 지정)"
                if uk == "voltage":
                    w.append(f"이 열의 표기 단위는 {c.unit}(전압)이지만 사용자 지정대로 {opts.current_unit}로 읽었습니다.")
                if opts.shunt_ohm:
                    w.append("current_unit을 지정해서 shunt_ohm은 무시했습니다.")
            elif uk == "current" or (c.kind == "current" and uk is None):
                i = v * (uf or 1.0)
                info["current_unit"] = c.unit or "A"
                if opts.shunt_ohm:
                    w.append("전류 열이라 shunt_ohm은 무시했습니다.")
            elif uk == "voltage" or c.kind == "voltage" or (uk is None and is_voltage(c)):
                if not opts.shunt_ohm:
                    raise UserInputError(
                        f"선택한 열 '{c.name}'은(는) 전압({c.unit or 'V'}) 데이터로 보입니다. 션트 저항으로 측정했다면 shunt_ohm"
                        f"(예: '10mohm'), 값이 실제로는 전류라면 current_unit(예: 'A', 'mA')을 알려주세요. "
                        f"열 목록: {'; '.join(describe_columns(cols))}")
                i = v * (uf or 1.0) / opts.shunt_ohm
                info["current_unit"] = f"{c.unit or 'V'} ÷ 션트 {opts.shunt_ohm:g} Ω"
            elif uk == "power":
                raise UserInputError(
                    f"선택한 열 '{c.name}'은(는) 전력({c.unit}) 데이터입니다. 전류 열을 지정하거나, 값 그대로 쓰려면 current_unit을 지정해 주세요.")
            elif parsed.si:
                i = v
                info["current_unit"] = "A"
            elif unit_info(parsed.vunit)[0] == "current":
                i = v * unit_info(parsed.vunit)[1]
                info["current_unit"] = f"{parsed.vunit} (파일 메타데이터)"
            elif unit_info(parsed.vunit)[0] == "voltage" and not opts.shunt_ohm:
                raise UserInputError(
                    f"파일 메타데이터상 세로축 단위가 {parsed.vunit}(전압)입니다. 션트 저항으로 측정했다면 shunt_ohm, "
                    "값이 실제로는 전류라면 current_unit(예: 'A')을 알려주세요.")
            elif unit_info(parsed.vunit)[0] == "voltage":
                i = v * unit_info(parsed.vunit)[1] / opts.shunt_ohm
                info["current_unit"] = f"{parsed.vunit} ÷ 션트 {opts.shunt_ohm:g} Ω"
            else:
                i = v
                info["current_unit"] = "A (가정)"
                w.append(f"'{c.name}' 열의 전류 단위 표기가 없어 A로 가정했습니다. mA 등이면 current_unit을 지정해 다시 불러오세요.")
            # ── 정리 ──
            n = min(len(t), len(i))
            t, i = t[:n], i[:n]
            ok = np.isfinite(t) & np.isfinite(i)
            if not np.all(ok):
                w.append(f"숫자가 아닌/비어 있는 값 {int(np.sum(~ok)):,}개를 제외했습니다.")
                t, i = t[ok], i[ok]
            if len(t) < 2:
                raise UserInputError(f"'{c.name}' 열에 유효한 데이터가 2점 미만입니다.")
            d = np.diff(t)
            if np.all(d <= 0) and np.any(d < 0):
                t, i = t[::-1], i[::-1]
                w.append("시간이 거꾸로(내림차순) 저장되어 있어 뒤집었습니다.")
                d = np.diff(t)
            if np.any(d < 0):
                nback = int(np.sum(d < 0))
                k0 = int(np.nonzero(d < 0)[0][0])
                raise UserInputError(
                    f"'{c.name}'의 시간 값이 {nback}번 감소합니다 (처음: {fmt_t(float(t[k0]))} → {fmt_t(float(t[k0 + 1]))}). "
                    "여러 측정/step이 이어진 파일이면 block을 지정하거나, 시간 열이 맞는지 time_col로 확인해 주세요.")
            t, i, ndup = ops.fix_duplicate_times(t, i)
            if ndup:
                w.append(f"같은 시간이 반복된 점 {ndup}개(계단 변화)를 아주 조금 뒤로 밀어 정리했습니다.")
            t, dtu, wreg = ops.regularize_time(t)
            if wreg:
                w.append(wreg)
            results.append({"label": block.label, "block_index": bk, "column": c.name, "t": t, "i": i,
                            "info": {k: v for k, v in info.items() if v is not None}, "warnings": w})
    return results
