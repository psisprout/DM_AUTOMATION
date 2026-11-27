"""테스트/데모용 샘플 파일 생성기.

같은 기준 파형(1 ms, 1 us 간격, idle 1 mA + 200~500 us 구간 100 mA burst + 작은 sin)을
여러 양식으로 써서, 어떤 양식으로 읽어도 같은 파형이 나오는지 확인한다.

    python tests/samples.py /tmp/cp_samples     # 폴더에 모든 샘플 생성 (데모용)
"""

from __future__ import annotations

import gzip
import json
import struct
import sys
from pathlib import Path

import numpy as np

N = 1000
DT = 1e-6
T = np.arange(N) * DT
I = 1e-3 + 0.1 * ((T >= 200e-6) & (T < 500e-6)) + 2e-3 * np.sin(2 * np.pi * T / 100e-6)
V = 1.8 - 0.05 * I / 0.1


def ref_mean():
    return float(np.sum(0.5 * (I[1:] + I[:-1]) * np.diff(T)) / (T[-1] - T[0]))


def _w(path: Path, text: str, enc="utf-8"):
    path.write_text(text, encoding=enc, newline="\n")
    return path


def rows(fmt_t, fmt_i, sep=",", t_scale=1.0, i_scale=1.0, extra=None):
    out = []
    for k in range(N):
        r = [fmt_t % (T[k] / t_scale), fmt_i % (I[k] / i_scale)]
        if extra:
            r += [x[k] for x in extra]
        out.append(sep.join(r))
    return "\n".join(out) + "\n"


# 각 항목: 파일이름 → (생성 함수, load 옵션, 기대: 균일간격 여부/정밀도)
def make_all(d: Path) -> dict:
    d.mkdir(parents=True, exist_ok=True)
    S = {}

    def reg(name, path, opts=None, tol=1e-6, n=N, t0=0.0, scale=1.0):
        S[name] = {"path": str(path), "opts": opts or {}, "tol": tol, "n": n, "t0": t0, "scale": scale}

    # ── 기본 CSV / TXT ──────────────────────────────────────────────────────
    reg("csv_units", _w(d / "basic_ms_mA.csv", "Time (ms),Current (mA)\n" + rows("%.6f", "%.6f", ",", 1e-3, 1e-3)))
    reg("csv_noheader", _w(d / "noheader.csv", rows("%.9e", "%.9e")), tol=1e-6)
    reg("tsv_bracket", _w(d / "bracket.tsv", "time[us]\ti[uA]\n" + rows("%.4f", "%.4f", "\t", 1e-6, 1e-6)))
    reg("txt_ws_units", _w(d / "ws_units.txt", "# measurement\n# Sample Rate: 1 MHz\nTime (s)   Current (A)\n" +
                           rows("%.9e", "%.9e", "   ")))
    reg("csv_suffix", _w(d / "suffix_names.csv", "time_us,current_mA,vbat_V\n" +
                         rows("%.4f", "%.6f", ",", 1e-6, 1e-3, [["%.4f" % v for v in V]])))
    reg("euro", _w(d / "euro.csv", "Zeit (s);Strom (A)\n" +
                   rows("%.9e", "%.9e", ";").replace(".", ",")))
    reg("cp949_korean", _w(d / "korean.csv", "시간(ms),전류(mA)\n" + rows("%.6f", "%.6f", ",", 1e-3, 1e-3), "cp949"))
    reg("single_col_meta", _w(d / "single_col.csv", "Sample interval,1e-06\nCurr avg 1 (A)\n" +
                              "\n".join("%.9e" % x for x in I) + "\n"))
    reg("single_col_dt", _w(d / "single_col_nometa.txt", "\n".join("%.9e" % x for x in I) + "\n"),
        {"dt": "1us"})
    reg("index_dt", _w(d / "index_rate.csv", "# fs = 1MHz\nSample,I (mA)\n" +
                       "\n".join(f"{k},{I[k] * 1e3:.6f}" for k in range(N)) + "\n"))
    # ── 스코프 / 계측기 CSV ──────────────────────────────────────────────────
    tek = []
    meta = [("Record Length", f"{N}", "Points"), ("Sample Interval", "1e-06", "s"), ("Trigger Point", "0", "Samples"),
            ("", "", ""), ("", "", ""), ("", "", ""), ("Source", "CH1", ""), ("Vertical Units", "A", ""),
            ("Vertical Scale", "0.02", ""), ("Vertical Offset", "0", ""), ("Horizontal Units", "s", ""),
            ("Horizontal Scale", "0.0001", ""), ("Pt Fmt", "Y", ""), ("Yzero", "0", ""), ("Probe Atten", "1", ""),
            ("Model Number", "TDS2024", ""), ("Serial Number", "C0", ""), ("Firmware Version", "FV:v1", "")]
    for k in range(N):
        a, b, c = meta[k] if k < len(meta) else ("", "", "")
        tek.append(f"{a},{b},{c},{T[k]:.9e},{I[k]:.6e},")
    reg("tek_old", _w(d / "tek_old.csv", "\n".join(tek) + "\n"))
    reg("tek_new", _w(d / "tek_mso.csv", "Model,MSO58\nFirmware Version,1.30\n\nWaveform Type,ANALOG\nPoint Format,Y\n"
                      "Horizontal Units,s\nHorizontal Scale,0.0001\nSample Interval,1e-06\nRecord Length,1000\n"
                      "Gating,0.0% to 100.0%\nVertical Units,A\nVertical Scale,0.02\nLabel,\nTIME,CH1\n" +
                      rows("%.9e", "%.6e")))
    reg("keysight_csv", _w(d / "keysight.csv", "x-axis,1\nsecond,Ampere\n" + rows("%.9e", "%.6e")))
    reg("rigol_csv", _w(d / "rigol.csv", "X,CH1,Start,Increment,\nSequence,Ampere,0.000000e+00,1.000000e-06,\n" +
                        "\n".join(f"{k},{I[k]:.6e}," for k in range(N)) + "\n"))
    reg("lecroy_csv", _w(d / "lecroy.csv", "LECROYWR104Xi,23077,Waveform\nSegments,1,SegmentSize,1000\n"
                         "Segment,TrigTime,TimeSinceSegment1\n#1,01-Jan-2024 00:00:00,0\nTime,Ampl\n" +
                         rows("%.9e", "%.6e")), {"current_unit": "A"})
    reg("picoscope", _w(d / "picoscope.csv", "Time,Channel A\n(us),(mA)\n\n" + rows("%.4f", "%.6f", ",", 1e-6, 1e-3)))
    reg("monsoon", _w(d / "monsoon.csv", "Time(ms),Main(mA),Main Voltage(V)\n" +
                      rows("%.4f", "%.6f", ",", 1e-3, 1e-3, [["%.4f" % v for v in V]])))
    reg("joulescope", _w(d / "joulescope.csv", "#time (s),current (A),voltage (V),power (W)\n" +
                         rows("%.9e", "%.9e", ",", 1, 1, [["%.4f" % v for v in V], ["%.6e" % (v * c) for v, c in zip(V, I)]])))
    # ── 시뮬레이터 텍스트 ────────────────────────────────────────────────────
    half = N // 2
    lt = "time\tI(R1)\tV(out)\n"
    for s, sc in ((1, 1.0), (2, 2.0)):
        lt += f"Step Information: Rload=10 (Run: {s}/2)\n" if s == 1 else "Step Information: Rload=20 (Run: 2/2)\n"
        lt += "".join(f"{T[k]:.15e}\t{I[k] * sc:.15e}\t{V[k]:.6e}\n" for k in range(N))
    reg("ltspice_step_txt", _w(d / "ltspice_step.txt", lt), {"block": "2"}, scale=2.0)
    reg("ngspice_wrdata", _w(d / "ngspice_wrdata.txt",
                             "".join(f" {T[k]:.8e}  {V[k]:.8e}  {T[k]:.8e}  {I[k]:.8e}\n" for k in range(N))),
        {"current_col": "4"})
    pr = "\n                                     test\n                     Transient Analysis  Mon Jan 01 2024\n"
    for page in range(2):
        pr += "-" * 70 + "\nIndex   time            i(v1)\n" + "-" * 70 + "\n"
        for k in range(page * half, (page + 1) * half):
            pr += f"{k}\t{T[k]:.6e}\t{I[k]:.6e}\t\n"
        pr += "\f\n"
    reg("ngspice_print", _w(d / "ngspice_print.txt", pr), tol=1e-6)

    def eng(x):
        if x == 0:
            return "0."
        for f, s in ((1e-3, "m"), (1e-6, "u"), (1e-9, "n"), (1e-12, "p")):
            if abs(x) >= f:
                return f"{x / f:.6f}{s}"
        return f"{x:.6e}"
    hs = (" ****** HSPICE -- test\n ******\n ******  transient analysis tnom=  25.000 temp=  25.000 *****\nx\n\n"
          "    time        current\n                  v1\n")
    hs += "".join(f"  {eng(T[k]):>12}  {eng(I[k]):>14}\n" for k in range(N)) + "y\n"
    reg("hspice_table", _w(d / "hspice_print.lis", hs), tol=2e-6)
    cad = "/I0/I_VDD X,/I0/I_VDD Y,/out X,/out Y\n" + "".join(
        f"{T[k]:.9e},{I[k]:.9e},{T[k]:.9e},{V[k]:.6e}\n" for k in range(N))
    reg("cadence_csv", _w(d / "viva.csv", cad))
    vc = ";Version, 1, 0\n;/I0/I_VDD tran, /out tran\n;X, Y, X, Y\n;s, A, s, V\n" + "".join(
        f"{T[k]:.9e},{I[k]:.9e},{T[k]:.9e},{V[k]:.6e}\n" for k in range(N))
    reg("vcsv", _w(d / "viva.vcsv", vc))
    # ── PWL / 넷리스트 ──────────────────────────────────────────────────────
    pts = [(0, 1e-3), (200e-6, 1e-3), (201e-6, 0.1), (500e-6, 0.1), (501e-6, 1e-3), (999e-6, 1e-3)]
    pwl_mean = sum(0.5 * (a[1] + b[1]) * (b[0] - a[0]) for a, b in zip(pts[:-1], pts[1:])) / 999e-6
    reg("pwl_file", _w(d / "stim.pwl", "* PWL test\n0 1m\n200u 1m\n+1u 100m\n500u 100m\n+1u 1m\n999u 1m\n"),
        n=6, tol=1e-9)
    S["pwl_file"]["mean"] = pwl_mean
    _w(d / "stim_ref.txt", "0,1m\n200u,1m\n201u,100m\n500u,100m\n501u,1m\n999u,1m\n")
    net = ("* test netlist\nV1 vdd 0 PWL(0 0 1u 1.8)\nIload vdd 0 PWL(0 1m 200u 1m\n+ 201u 100m 500u 100m\n"
           "+ 501u 1m 999u 1m)\nIref vdd 0 PWL FILE=\"stim_ref.txt\"\n.tran 1n 1m\n.end\n")
    reg("netlist", _w(d / "test.sp", net), {"current_col": "Iload"}, n=6, tol=1e-9)
    S["netlist"]["mean"] = pwl_mean
    reg("netlist_fileref", d / "test.sp", {"current_col": "Iref"}, n=6, tol=1e-9)
    S["netlist_fileref"]["mean"] = pwl_mean
    asc = ("Version 4\nSHEET 1 880 680\nSYMBOL current 128 96 R0\nSYMATTR InstName I1\n"
           "SYMATTR Value PWL(0 1m 200u 1m 201u 100m 500u 100m 501u 1m 999u 1m)\n"
           "SYMBOL voltage 0 0 R0\nSYMATTR InstName V1\nSYMATTR Value 1.8\n")
    reg("ltspice_asc", _w(d / "circuit.asc", asc), n=6, tol=1e-9)
    S["ltspice_asc"]["mean"] = pwl_mean
    reg("pwl_repeat", _w(d / "repeat.pwl", "PWL REPEAT FOR 3 (0 0 1u 1m 2u 0) ENDREPEAT\n"), n=9, tol=1e-9)
    S["pwl_repeat"]["mean"] = None
    # ── SPICE raw ───────────────────────────────────────────────────────────
    hdr = ("Title: test\nDate: Mon Jan  1 00:00:00 2024\nPlotname: Transient Analysis\nFlags: real\n"
           f"No. Variables: 3\nNo. Points: {N}\nVariables:\n\t0\ttime\ttime\n\t1\tv(out)\tvoltage\n\t2\ti(v1)\tcurrent\n")
    data = np.column_stack([T, V, I]).astype("<f8").tobytes()
    (d / "ngspice_bin.raw").write_bytes((hdr + "Binary:\n").encode() + data)
    reg("raw_ngspice_bin", d / "ngspice_bin.raw")
    asc_vals = "".join(f" {k}\t{T[k]:.15e}\n\t{V[k]:.15e}\n\t{I[k]:.15e}\n" for k in range(N))
    _w(d / "ngspice_ascii.raw", hdr + "Values:\n" + asc_vals)
    reg("raw_ngspice_ascii", d / "ngspice_ascii.raw")
    lth = ("Title: * C:\\test.asc\nDate: Mon Jan  1 00:00:00 2024\nPlotname: Transient Analysis\n"
           "Flags: real forward stepped\nNo. Variables: 3\nNo. Points: {np}\nOffset:   0.0000000000000000e+000\n"
           "Command: Linear Technology Corporation LTspice XVII\nVariables:\n\t0\ttime\ttime\n\t1\tV(out)\tvoltage\n"
           "\t2\tI(R1)\tdevice_current\nBinary:\n")
    rec = np.dtype([("t", "<f8"), ("v", "<f4"), ("i", "<f4")])
    arr = np.zeros(2 * N, rec)
    arr["t"] = np.concatenate([T, T])
    arr["t"][N + 1] *= -1  # LTspice 시간 부호 플래그 흉내
    arr["v"] = np.concatenate([V, V])
    arr["i"] = np.concatenate([I, 2 * I])
    (d / "ltspice_step.raw").write_bytes(lth.format(np=2 * N).encode("utf-16-le") + arr.tobytes())
    reg("raw_ltspice_step", d / "ltspice_step.raw", {"block": "1"}, tol=1e-6)
    # ── CSDF / PSF ──────────────────────────────────────────────────────────
    cs = ("#H\nSOURCE='HSPICE' VERSION='2020'\nTITLE='test'\nSUBTITLE=''\nTIME=0\nANALYSIS='Transient Analysis' "
          "SERIALNO='1'\nNODES='2'\n#N\n'v(out)' 'i(v1)'\n")
    cs += "".join(f"#C {T[k]:.9e} 2\n{V[k]:.9e}:1 {I[k]:.9e}:2\n" for k in range(N)) + "#;\n"
    reg("csdf", _w(d / "hspice.csdf", cs))
    ps = ('HEADER\n"PSFversion" "1.00"\n"simulator" "spectre"\nTYPE\n"sweep" FLOAT DOUBLE PROP(\n"key" "sweep"\n)\n'
          '"V" FLOAT DOUBLE PROP(\n"key" "node"\n"units" "V"\n)\n"I" FLOAT DOUBLE PROP(\n"key" "branch"\n"units" "A"\n)\n'
          'SWEEP\n"time" "sweep" PROP(\n"key" "sweep"\n)\nTRACE\n"out" "V"\n"V1:p" "I"\nVALUE\n')
    ps += "".join(f'"time" {T[k]:.15e}\n"out" {V[k]:.15e}\n"V1:p" {I[k]:.15e}\n' for k in range(N)) + "END\n"
    reg("psfascii", _w(d / "tran.tran.psfascii", ps))
    # ── 스코프 바이너리 ─────────────────────────────────────────────────────
    ymult = 1e-5
    raw16 = np.round(I / ymult).astype(">i2")
    isf_h = (f':WFMPRE:BYT_NR 2;BIT_NR 16;ENCDG BIN;BN_FMT RI;BYT_OR MSB;WFID "Ch1, DC coupling, 20.0mA/div, '
             f'100.0us/div, {N} points, Sample mode";NR_PT {N};PT_FMT Y;XUNIT "s";XINCR 1.0000E-6;XZERO 0.0E+0;'
             f'PT_OFF 0;YUNIT "A";YMULT {ymult:.4E};YOFF 0.0E+0;YZERO 0.0E+0;:CURVE ')
    blk = raw16.tobytes()
    ln = str(len(blk))
    (d / "tek.isf").write_bytes(isf_h.encode() + b"#" + str(len(ln)).encode() + ln.encode() + blk + b"\n")
    reg("tek_isf", d / "tek.isf", tol=ymult)
    desc = bytearray(346)
    desc[0:8] = b"WAVEDESC"
    desc[16:26] = b"LECROY_2_3"
    struct.pack_into("<h", desc, 32, 1)
    struct.pack_into("<h", desc, 34, 1)
    struct.pack_into("<l", desc, 36, 346)
    struct.pack_into("<l", desc, 60, 2 * N)
    struct.pack_into("<l", desc, 116, N)
    struct.pack_into("<l", desc, 144, 1)
    struct.pack_into("<f", desc, 156, ymult)
    struct.pack_into("<f", desc, 160, 0.0)
    struct.pack_into("<f", desc, 176, 1e-6)
    struct.pack_into("<d", desc, 180, 0.0)
    desc[196:197] = b"A"
    desc[244:245] = b"S"
    body = bytes(desc) + np.round(I / ymult).astype("<i2").tobytes()
    (d / "lecroy.trc").write_bytes(b"#9" + f"{len(body):09d}".encode() + body)
    reg("lecroy_trc", d / "lecroy.trc", tol=2 * ymult)
    wf = bytearray(140)
    struct.pack_into("<5i", wf, 0, 140, 1, 1, N, 1)
    struct.pack_into("<f", wf, 20, 1e-3)
    struct.pack_into("<3d", wf, 24, 0.0, 1e-6, 0.0)
    struct.pack_into("<2i", wf, 48, 2, 4)
    wf[112:115] = b"CH1"
    dh = struct.pack("<i2hi", 12, 1, 4, 4 * N)
    payload = bytes(wf) + dh + I.astype("<f4").tobytes()
    (d / "keysight.bin").write_bytes(b"AG10" + struct.pack("<2i", 12 + len(payload), 1) + payload)
    reg("keysight_bin", d / "keysight.bin", tol=1e-7)
    # ── 바이너리/구조화 데이터 ───────────────────────────────────────────────
    np.save(d / "arr.npy", np.column_stack([T, I]))
    reg("npy", d / "arr.npy")
    np.savez(d / "arrays.npz", time=T, current=I, voltage=V)
    reg("npz", d / "arrays.npz")
    _w(d / "dict.json", json.dumps({"device": "x", "dt": 1e-6, "time_s": T.tolist(), "current_A": I.tolist()}))
    reg("json_dict", d / "dict.json")
    _w(d / "records.json", json.dumps([{"t": float(a), "i_mA": float(b * 1e3)} for a, b in zip(T, I)]))
    reg("json_records", d / "records.json")
    with gzip.open(d / "gz_basic.csv.gz", "wt") as f:
        f.write("Time (ms),Current (mA)\n" + rows("%.6f", "%.6f", ",", 1e-3, 1e-3))
    reg("gzip_csv", d / "gz_basic.csv.gz")
    try:
        import pandas as pd
        with pd.ExcelWriter(d / "book.xlsx") as xw:
            pd.DataFrame({"메모": ["측정 조건", "VBAT=3.8V"]}).to_excel(xw, sheet_name="cover", index=False)
            pd.DataFrame({"Time (s)": T, "Current (mA)": I * 1e3}).to_excel(xw, sheet_name="data", index=False)
        reg("xlsx", d / "book.xlsx")
        try:
            pd.DataFrame({"t": T, "i": I}).to_parquet(d / "data.parquet")
            reg("parquet", d / "data.parquet")
            pd.DataFrame({"t": T, "i": I}).reset_index(drop=True).to_feather(d / "data.feather")
            reg("feather", d / "data.feather")
        except Exception:
            pass
    except ImportError:
        pass
    try:
        import scipy.io
        scipy.io.savemat(d / "meas.mat", {"meas": {"t": T, "i_ua": I * 1e6, "dt": 1e-6}})
        reg("mat", d / "meas.mat", {"current_col": "meas.i_ua", "current_unit": "uA"})
    except ImportError:
        pass
    try:
        import h5py
        with h5py.File(d / "meas.h5", "w") as f:
            g = f.create_group("run1")
            g.create_dataset("time", data=T)
            ds = g.create_dataset("current", data=I * 1e3)
            ds.attrs["units"] = "mA"
        reg("hdf5", d / "meas.h5")
    except ImportError:
        pass
    return S


if __name__ == "__main__":
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "cp_samples")
    s = make_all(out)
    print(f"{len(s)}개 샘플을 {out.resolve()}에 만들었습니다.")
    for k, v in s.items():
        print(f"  {k:20s} {v['path']}  {v['opts'] or ''}")
