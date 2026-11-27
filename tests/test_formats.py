"""양식별 읽기 테스트: 같은 기준 파형을 여러 양식으로 써 놓고 읽어서 비교한다."""
import numpy as np
import pytest

import samples as smp
from cplib import formats, ops
from cplib.common import UserInputError, parse_quantity



def load(path, **kw):
    opts = formats.LoadOptions(**{k: v for k, v in kw.items() if k in formats.LoadOptions.__dataclass_fields__})
    if "dt" in kw and isinstance(kw["dt"], str):
        opts.dt = parse_quantity(kw["dt"], "time")
    from pathlib import Path
    parsed = formats.read_file(Path(path), sheet=kw.get("sheet"))
    return parsed, formats.build_profiles(parsed, opts)


SAMPLE_KEYS = [
    "csv_units", "csv_noheader", "tsv_bracket", "txt_ws_units", "csv_suffix", "euro", "cp949_korean",
    "single_col_meta", "single_col_dt", "index_dt", "tek_old", "tek_new", "keysight_csv", "rigol_csv", "lecroy_csv",
    "picoscope", "monsoon", "joulescope", "ltspice_step_txt", "ngspice_wrdata", "ngspice_print", "hspice_table",
    "cadence_csv", "vcsv", "pwl_file", "netlist", "netlist_fileref", "ltspice_asc", "pwl_repeat",
    "raw_ngspice_bin", "raw_ngspice_ascii", "raw_ltspice_step", "csdf", "psfascii", "tek_isf", "lecroy_trc",
    "keysight_bin", "npy", "npz", "json_dict", "json_records", "gzip_csv", "xlsx", "parquet", "feather", "mat",
    "hdf5",
]


@pytest.mark.parametrize("key", SAMPLE_KEYS)
def test_sample_reads_back(samples, key):
    if key not in samples:
        pytest.skip(f"{key}: 선택 패키지 없음")
    s = samples[key]
    parsed, res = load(s["path"], **s["opts"])
    assert len(res) == 1, [r["column"] for r in res]
    r = res[0]
    t, i = r["t"], r["i"]
    assert len(t) == s["n"], (key, len(t), r["warnings"])
    assert np.all(np.diff(t) > 0)
    if s["n"] == smp.N:
        assert t[0] == pytest.approx(s["t0"], abs=1e-12)
        assert t[-1] == pytest.approx(smp.T[-1], rel=1e-6)
        assert np.max(np.abs(i - smp.I * s["scale"])) <= s["tol"] * max(1, s["scale"]) + 1e-12, \
            (key, r["info"], r["warnings"][:3])
    elif s.get("mean") is not None:
        m = ops.integral(t, i) / (t[-1] - t[0])
        assert m == pytest.approx(s["mean"], rel=1e-6)


def test_unit_inference(samples):
    _, r = load(samples["csv_units"]["path"])
    assert r[0]["info"]["time_unit"] == "ms" and r[0]["info"]["current_unit"] == "mA"
    assert not r[0]["warnings"]
    _, r = load(samples["csv_noheader"]["path"])
    assert any("A로 가정" in w for w in r[0]["warnings"])
    assert any("초(s)로 가정" in w for w in r[0]["warnings"])


def test_scope_metadata_units_no_warning(samples):
    for k in ("tek_old", "tek_new", "keysight_csv", "rigol_csv"):
        _, r = load(samples[k]["path"])
        assert not any("가정" in w for w in r[0]["warnings"]), (k, r[0]["warnings"])


def test_ambiguous_columns_ask_user(samples):
    with pytest.raises(UserInputError) as e:
        load(samples["ngspice_wrdata"]["path"])
    assert "current_col" in str(e.value) and "열 목록" in str(e.value)


def test_multi_block_requires_choice(samples):
    with pytest.raises(UserInputError) as e:
        load(samples["ltspice_step_txt"]["path"])
    assert "Rload=10" in str(e.value) and "block" in str(e.value)
    _, r = load(samples["ltspice_step_txt"]["path"], block="all")
    assert len(r) == 2 and r[1]["i"].max() == pytest.approx(2 * r[0]["i"].max())


def test_no_time_column_needs_dt(samples):
    with pytest.raises(UserInputError) as e:
        load(samples["single_col_dt"]["path"])
    assert "dt" in str(e.value)


def test_voltage_column_needs_shunt(tmp_path):
    p = tmp_path / "shunt.csv"
    p.write_text("Time (s),CH1 (mV)\n0,10\n1e-6,20\n2e-6,10\n")
    with pytest.raises(UserInputError) as e:
        load(p)
    assert "shunt_ohm" in str(e.value)
    _, r = load(p, shunt_ohm=0.01)
    assert r[0]["i"][1] == pytest.approx(2.0)     # 20 mV / 10 mΩ = 2 A
    _, r = load(p, current_unit="mA")
    assert r[0]["i"][1] == pytest.approx(0.02)


def test_netlist_lists_sources(samples):
    with pytest.raises(UserInputError) as e:
        load(samples["netlist"]["path"])
    msg = str(e.value)
    assert "Iload" in msg and "Iref" in msg


def test_all_columns(samples):
    _, r = load(samples["joulescope"]["path"], current_col="all")
    assert [x["column"] for x in r] == ["current"]


def test_duplicate_times_are_nudged(tmp_path):
    p = tmp_path / "step.csv"
    p.write_text("t,i\n0,0\n1e-6,0\n1e-6,1\n2e-6,1\n")
    _, r = load(p)
    assert len(r[0]["t"]) == 4 and np.all(np.diff(r[0]["t"]) > 0)


def test_low_precision_time_regularized(tmp_path):
    p = tmp_path / "lowprec.csv"
    t = 1.2345678e-3 + np.arange(2000) * 3.3e-9      # %.6e로 쓰면 시간값이 ±0.5 ns 흔들림
    p.write_text("time (s),current (A)\n" + "".join(f"{a:.6e},{1e-3}\n" for a in t))
    _, r = load(p)
    assert ops.uniform_dt(r[0]["t"]) == pytest.approx(3.3e-9, rel=1e-3)
    assert any("보정" in w for w in r[0]["warnings"])


def test_unsupported_binary_message(tmp_path):
    p = tmp_path / "x.wfm"
    p.write_bytes(b"\x00\x01binary")
    with pytest.raises(UserInputError) as e:
        load(p)
    assert "CSV" in str(e.value)


def test_large_csv_fast_path(tmp_path):
    import time
    n = 2_000_000
    t = np.arange(n) * 1e-7
    i = 1e-3 + 1e-4 * np.sin(t * 1e4)
    p = tmp_path / "big.csv"
    with open(p, "w") as f:
        f.write("Time (s),Current (A)\n")
        np.savetxt(f, np.column_stack([t, i]), fmt="%.10e", delimiter=",")
    t0 = time.time()
    _, r = load(p)
    el = time.time() - t0
    assert len(r[0]["t"]) == n
    assert el < 30, el


@pytest.mark.parametrize("kind", ["ws", "euro", "tek_old"])
def test_big_file_fast_path_variants(tmp_path, kind):
    n = 150_000
    t = np.arange(n) * 1e-6
    i = 1e-3 + 1e-4 * np.sin(t * 1e4)
    p = tmp_path / f"big_{kind}.txt"
    if kind == "ws":
        body = "Time (s)   Current (A)\n" + "".join(f"  {a:.9e}   {b:.9e}\n" for a, b in zip(t, i))
    elif kind == "euro":
        body = "Zeit (s);Strom (mA)\n" + "".join(f"{a:.9e};{b * 1e3:.9e}\n".replace(".", ",") for a, b in zip(t, i))
    else:  # Tektronix 구형: 앞 3열은 메타데이터(처음 몇 줄만), 4~5열이 데이터
        meta = ["Record Length,150000,Points,", "Sample Interval,1e-06,s,", "Vertical Units,A,,"]
        body = "".join((meta[k] if k < 3 else ",,,") + f"{a:.9e},{b:.9e},\n" for k, (a, b) in enumerate(zip(t, i)))
    p.write_text(body)
    assert p.stat().st_size > 2_000_000
    _, r = load(p)
    assert len(r[0]["t"]) == n
    assert np.max(np.abs(r[0]["i"] - i)) < 1e-9
