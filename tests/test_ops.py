"""파형 연산이 손으로 계산한 값과 맞는지 확인."""
import numpy as np
import pytest

from cplib import ops
from cplib.common import UserInputError, fmt_si, parse_quantity, spice_number


def square(duty=0.25, period=2e-3, n_per=2, dt=1e-6, hi=0.15, lo=0.002):
    t = np.arange(int(n_per * period / dt)) * dt
    i = np.where((t % period) < duty * period, hi, lo)
    return t, i


def mean(t, i):
    return ops.integral(t, i) / (t[-1] - t[0])


def val(s):
    """'12.3 mA' → 0.0123"""
    num, unit = s.split()
    return parse_quantity(num + unit.replace("Ah", "A"), "current") if unit.endswith("A") else float(num)


def test_parse_quantity_and_spice():
    assert parse_quantity("2ms", "time") == pytest.approx(2e-3)
    assert parse_quantity("10 us", "time") == pytest.approx(1e-5)
    assert parse_quantity("2u", "time") == pytest.approx(2e-6)
    assert parse_quantity("5mA", "current") == pytest.approx(5e-3)
    assert parse_quantity("1.25GS/s", "freq") == pytest.approx(1.25e9)
    assert parse_quantity("1MHz", "freq") == pytest.approx(1e6)
    assert parse_quantity("10mohm", "resistance") == pytest.approx(0.01)
    assert parse_quantity(0.5, "time") == 0.5
    with pytest.raises(UserInputError):
        parse_quantity("3 parsecs", "time")
    assert spice_number("1meg") == 1e6 and spice_number("3mA") == pytest.approx(3e-3)
    assert spice_number("10n") == pytest.approx(1e-8) and spice_number("abc") is None


def test_fmt_si():
    assert fmt_si(0.0123, "A") == "12.3 mA"
    assert fmt_si(1500, "A") == "1500 A"       # MA(메가) 표기 금지
    assert fmt_si(2.5e-6, "s") == "2.5 us"


def test_mean_rms_exact_for_linear():
    t = np.array([0.0, 1.0])
    i = np.array([0.0, 1.0])
    s = ops.basic_stats(t, i)
    assert val(s["mean"]) == pytest.approx(0.5)
    assert val(s["rms"]) == pytest.approx(1 / np.sqrt(3), rel=1e-3)


def test_time_weighted_mean_nonuniform():
    t = np.array([0, 1e-3, 1e-3 + 1e-9, 10e-3])     # 1 ms 동안 100 mA, 9 ms 동안 0
    i = np.array([0.1, 0.1, 0.0, 0.0])
    assert mean(t, i) == pytest.approx(0.01, rel=1e-5)


def test_active_idle_square():
    t, i = square()
    thr, lo, hi, _ = ops.resolve_threshold(t, i, None)
    assert lo == pytest.approx(0.002) and hi == pytest.approx(0.15)
    r = ops.active_idle(t, i, thr)
    assert val(r["active_mean"]) == pytest.approx(0.15, rel=1e-3)
    assert val(r["idle_mean"]) == pytest.approx(0.002, rel=1e-2)
    assert float(r["active_ratio"].split()[0]) == pytest.approx(25, abs=0.2)
    assert r["bursts"]["count"] == 2


def test_active_idle_merge_and_min_duration():
    t = np.arange(0, 1e-3, 1e-7)
    i = np.zeros_like(t)
    i[(t > 100e-6) & (t < 200e-6)] = 1
    i[(t > 205e-6) & (t < 300e-6)] = 1      # 5 us 틈
    i[(t > 500e-6) & (t < 501e-6)] = 1      # 1 us glitch
    r = ops.active_idle(t, i, 0.5)
    assert r["bursts"]["count"] == 3
    r = ops.active_idle(t, i, 0.5, min_duration=5e-6, merge_gap=10e-6)
    assert r["bursts"]["count"] == 1


def test_window_extremes():
    t, i = square(period=10e-3, duty=0.1, n_per=3)     # 1 ms 동안 150 mA
    w = ops.window_extremes(t, i, 1e-3)
    assert val(w["max_avg"]) == pytest.approx(0.15, rel=1e-3)
    w = ops.window_extremes(t, i, 2e-3)
    assert val(w["max_avg"]) == pytest.approx((0.15 + 0.002) / 2, rel=1e-3)


def test_combine_different_grids_zero_outside():
    ta = np.arange(1001) * 1e-6
    ia = np.full_like(ta, 1e-3)
    tb = 200e-6 + np.arange(1001) * 0.3e-6          # 200~500 us, 0.3 us 간격
    ib = np.full_like(tb, 10e-3)
    t, i, notes = ops.combine_sum([(ta, ia), (tb, ib)], [1, 1], "zero")
    assert mean(t, i) == pytest.approx(1e-3 + 10e-3 * 0.3, rel=1e-4)
    assert np.interp(100e-6, t, i) == pytest.approx(1e-3)
    assert np.interp(300e-6, t, i) == pytest.approx(11e-3)
    t2, i2, _ = ops.combine_sum([(ta, ia), (tb, ib)], [1, 1], "crop")
    assert t2[0] == pytest.approx(200e-6) and t2[-1] == pytest.approx(500e-6)
    t3, i3, _ = ops.combine_sum([(ta, ia), (ta, ia)], [1, -1], "zero")
    assert np.all(i3 == 0)


def test_repeat_uniform_and_period():
    t = np.arange(100) * 1e-6
    i = np.where(t < 50e-6, 0.1, 0.0)
    tt, ii, per = ops.repeat(t, i, 3)
    assert per == pytest.approx(100e-6)
    assert len(tt) == 300 and ops.uniform_dt(tt) == pytest.approx(1e-6)
    tt, ii, per = ops.repeat(t, i, 2, period=300e-6, fill="zero")
    assert tt[-1] == pytest.approx(300e-6 + 99e-6)
    assert np.interp(200e-6, tt, ii) == 0
    with pytest.raises(UserInputError):
        ops.repeat(t, i, 2, period=50e-6)


def test_repeat_pwl_junction():
    t = np.array([0, 1e-3])
    i = np.array([0.0, 1.0])            # 톱니
    tt, ii, _ = ops.repeat(t, i, 3)
    assert tt[-1] == pytest.approx(3e-3)
    t = np.array([0, 1e-3, 2e-3])       # 균일 간격 PWL 꺾은점: sampled=False면 주기 = 길이
    i = np.array([0.0, 1.0, 0.0])
    tt, ii, per = ops.repeat(t, i, 3, sampled=False)
    assert per == pytest.approx(2e-3)
    assert np.all(np.diff(tt) > 0)
    assert tt[-1] == pytest.approx(6e-3)
    assert mean(tt, ii) == pytest.approx(0.5, rel=1e-4)


def test_resample_preserves_charge():
    t, i = square(period=1e-3, duty=0.3, n_per=4, dt=1e-7)
    q0 = ops.integral(t, i)
    tn, inn, how = ops.resample(t, i, 10e-6)
    assert "평균" in how
    assert ops.integral(tn, inn) == pytest.approx(q0, rel=2e-3)


def test_crop_exact():
    t, i = square()
    tt, ii = ops.crop(t, i, 0.1e-3, 0.4e-3)
    assert tt[0] == pytest.approx(0.1e-3) and tt[-1] == pytest.approx(0.4e-3)
    assert mean(tt, ii) == pytest.approx(0.15)


@pytest.mark.parametrize("noise", [0.0, 2e-3])
def test_align_edge_recovers_shift(noise):
    rng = np.random.default_rng(1)
    t = np.arange(0, 2e-3, 1e-7)
    base = lambda tt: 1e-3 + 0.1 / (1 + np.exp(-np.clip((tt - 0.5e-3) / 2e-7, -60, 60)))  # noqa: E731
    ref = base(t) + rng.normal(0, noise, len(t))
    tgt = base(t - 123.4e-6) + rng.normal(0, noise, len(t))
    e_r, _, _ = ops.find_edges(t, ref)
    e_t, _, _ = ops.find_edges(t, tgt)
    shift = ops.pick_edge(e_r, None)["t"] - ops.pick_edge(e_t, None)["t"]
    assert shift == pytest.approx(-123.4e-6, abs=1e-6)


def test_align_xcorr_and_maxdidt():
    t = np.arange(0, 2e-3, 1e-6)
    f = lambda tt: np.where((tt > 0.4e-3) & (tt < 0.6e-3), 0.1, 0.0) + np.where((tt > 1.0e-3) & (tt < 1.1e-3), 0.05, 0)  # noqa
    sh, coef, _ = ops.xcorr_shift(t, f(t), t, f(t - 77e-6))
    assert sh == pytest.approx(-77e-6, abs=2e-6) and coef > 0.95
    tm, slope = ops.max_didt_time(t, f(t))
    assert tm == pytest.approx(0.4e-3, abs=2e-6)


def test_place_sequence_concat():
    a = (np.arange(10) * 1e-6, np.full(10, 1.0))
    b = (np.arange(5) * 2e-6, np.full(5, 2.0))
    t, i = ops.place_sequence([a, b])
    assert t[10] == pytest.approx(10e-6) and i[10] == 2.0
