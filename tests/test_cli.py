"""CLI(스크립트 단독) 테스트: 가이드 3장 규약 — 마지막 줄 JSON, 종료 코드, needs_user_input."""
import json
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "scripts" / "current_profile.py"


def run(*args, expect=0):
    p = subprocess.run([sys.executable, str(CLI), *map(str, args), "--json"], capture_output=True, text=True,
                       cwd=str(ROOT / "scripts"), env=dict(os.environ), stdin=subprocess.DEVNULL)
    last = [ln for ln in p.stdout.splitlines() if ln.strip()][-1]
    out = json.loads(last)
    assert p.returncode == expect, (p.returncode, out, p.stderr[-2000:])
    return out


def mA(s):
    v, u = s.split()
    return float(v) * {"A": 1e3, "mA": 1, "uA": 1e-3, "nA": 1e-6}[u]


@pytest.fixture
def files(tmp_path):
    k = np.arange(2000)
    t = k * 1e-6
    idle = np.full_like(t, 1e-3)
    burst = np.where((k > 500) & (k < 800), 0.2, 0.0)
    (tmp_path / "idle.csv").write_text("Time (s),Current (A)\n" + "".join(f"{a:.9e},{b:.9e}\n" for a, b in zip(t, idle)))
    (tmp_path / "burst.txt").write_text("time[ms]\tI[mA]\n" + "".join(f"{a * 1e3:.6f}\t{b * 1e3:.6f}\n" for a, b in zip(t, burst)))
    meas = np.where((k > 600) & (k < 900), 0.2, 0.0) + 1e-3
    (tmp_path / "meas.csv").write_text("t (s),i (A)\n" + "".join(f"{a:.9e},{b:.9e}\n" for a, b in zip(t, meas)))
    return tmp_path


def test_full_workflow(files, tmp_path):
    r = run("load", f"--path={files / 'idle.csv'}", "--name=idle")
    assert r["names"] == ["idle"] and mA(r["profiles"][0]["mean"]) == pytest.approx(1.0)
    r = run("load", f"--path={files / 'burst.txt'}", "--name=burst")
    assert r["profiles"][0]["current_unit"] == "mA" and r["profiles"][0]["time_unit"] == "ms"
    r = run("transform", "--name=burst", "--time-scale=2", "--time-shift=-1ms")
    bt = r["result_name"]
    assert bt == "burst_ts2_sh" and r["profile"]["t_start"] == "-1 ms"
    r = run("combine", "--names", "idle", bt, "--weights=1,1")
    s = r["result_name"]
    assert s == "idle+burst_ts2_sh"
    r = run("analyze", f"--name={s}", "--window=0.5ms", "--supply-voltage=3.8V")
    ai = r["active_idle"]
    assert mA(ai["active_mean"]) == pytest.approx(201, rel=1e-3)
    # idle 구간 3.4 ms 중 1.4 ms만 1 mA (나머지는 범위 밖 0 A) + burst 경사의 threshold 아래 부분
    assert 0.40 < mA(ai["idle_mean"]) < 0.45
    r = run("load", f"--path={files / 'meas.csv'}", "--name=meas")
    r = run("align", "--ref=meas", "--targets", "burst")
    assert r["results"][0]["shift"] == "+100 us"
    r = run("combine", "--names", "meas", "burst_aligned", "--weights=1,-1", "--outside=crop")
    diff = run("analyze", f"--name={r['result_name']}")
    assert mA(diff["basic"]["mean"]) == pytest.approx(1.0, rel=1e-3)
    out = tmp_path / "out.csv"
    r = run("export", f"--name={s}", f"--output={out}", "--time-unit=ms", "--current-unit=mA")
    assert r["created"] == [str(out)]
    r2 = run("load", f"--path={out}", "--name=reloaded")
    assert r2["profiles"][0]["mean"] == run("analyze", f"--name={s}")["basic"]["mean"]
    pwl = tmp_path / "out.pwl"
    run("export", f"--name={s}", f"--output={pwl}")
    r3 = run("load", f"--path={pwl}", "--name=re_pwl")
    assert r3["profiles"][0]["points"] == r2["profiles"][0]["points"]
    lst = run("list")
    assert lst["count"] >= 6
    run("delete", "--names", "reloaded", "re_pwl")
    assert run("list")["count"] == lst["count"] - 2


def test_plot(files, tmp_path):
    pytest.importorskip("matplotlib")
    run("load", f"--path={files / 'idle.csv'}", "--name=idle")
    run("load", f"--path={files / 'meas.csv'}", "--name=meas")
    r = run("plot", "--names", "idle", "meas")
    assert Path(r["plot_path"]).is_file() and r["plot_path"].endswith(".png")
    out = tmp_path / "p.png"
    r = run("plot", "--names", "meas", f"--output={out}", "--t-start=0.5ms", "--t-end=1ms", "--separate")
    assert out.is_file()
    r = run("plot", "--names", "meas", f"--output={out}", expect=2)
    assert r["needs_user_input"] and "overwrite" in r["error"]


def test_errors_are_user_input(files, tmp_path):
    r = run("load", "--path=/no/such/file.csv", expect=2)
    assert r["status"] == "error" and r["needs_user_input"] is True
    run("load", f"--path={files / 'idle.csv'}", "--name=idle")
    r = run("load", f"--path={files / 'idle.csv'}", "--name=idle", expect=2)
    assert "이미" in r["error"]
    r = run("analyze", "--name=nothing", expect=2)
    assert "idle" in r["error"]
    r = run("export", "--name=idle", f"--output={tmp_path / 'nodir' / 'x.csv'}", expect=2)
    assert "폴더가 없습니다" in r["error"]
    r = run("transform", "--name=idle", expect=2)
    assert "바꿀 내용" in r["error"]
    r = run("transform", "--name=idle", "--repeat=3", "--repeat-period=0.5ms", expect=2)
    assert "repeat_period" in r["error"]
