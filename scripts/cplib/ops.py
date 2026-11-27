"""전류 파형 연산.

파형은 (t[s], i[A]) 두 배열이고 t는 엄격히 증가한다. 샘플 사이는 선형보간으로 본다.
따라서 평균/전하량/RMS는 모두 '시간가중'이며 비균일 간격(PWL 등)에서도 정확하다.
"""

from __future__ import annotations

from typing import Optional

import numpy as np

from .common import MAX_POINTS, UserInputError, fmt_didt, fmt_i, fmt_pct, fmt_si, fmt_t

TINY = 1e-300


# ─────────────────────────────────────────────────────────────────────────────
# 기본 도구
# ─────────────────────────────────────────────────────────────────────────────
def uniform_dt(t: np.ndarray, rel_tol: float = 1e-6) -> Optional[float]:
    """균일 간격이면 dt, 아니면 None."""
    n = len(t)
    if n < 2:
        return None
    dt = (t[-1] - t[0]) / (n - 1)
    if dt <= 0:
        return None
    if n == 2:
        return dt
    eps = 8 * np.finfo(float).eps * max(abs(t[0]), abs(t[-1]))
    resid = np.max(np.abs(t - (t[0] + np.arange(n) * dt)))
    return dt if resid <= max(rel_tol * dt, eps) else None


def regularize_time(t: np.ndarray):
    """거의 균일한 시간축(텍스트 정밀도 부족 등)을 정확한 균일 격자로 보정한다.

    반환: (t, dt 또는 None, 경고 문자열 또는 None)
    """
    n = len(t)
    if n < 3:
        return t, uniform_dt(t), None
    dt = (t[-1] - t[0]) / (n - 1)
    if dt <= 0:
        return t, None, None
    ideal = t[0] + np.arange(n) * dt
    resid = np.max(np.abs(t - ideal))
    eps = 8 * np.finfo(float).eps * max(abs(t[0]), abs(t[-1]))
    if resid <= max(1e-6 * dt, eps):
        return ideal, dt, None
    # 최소제곱 직선으로 다시 (끝점도 반올림돼 있을 수 있으므로)
    k = np.arange(n, dtype=float)
    km = (n - 1) / 2.0
    tm = float(np.mean(t))
    dt = float(np.sum((k - km) * (t - tm)) / np.sum((k - km) ** 2))
    ideal = (tm - km * dt) + k * dt
    resid = float(np.max(np.abs(t - ideal)))
    if resid <= 0.3 * dt:
        return ideal, dt, (f"시간값이 균일 간격(dt={fmt_t(dt)})에서 최대 {resid / dt * 100:.2g}% 어긋나 있어 "
                           "(파일 숫자 정밀도 부족으로 보임) 정확한 균일 간격으로 보정했습니다.")
    return t, None, None


def fix_duplicate_times(t: np.ndarray, i: np.ndarray):
    """같은 시간이 연속으로 나오면(계단) 아주 조금 뒤로 밀어 엄격 증가로 만든다."""
    d = np.diff(t)
    if np.all(d > 0):
        return t, i, 0
    if np.any(d < 0):
        raise ValueError("시간이 감소하는 구간이 있습니다")
    pos = d[d > 0]
    base = float(np.min(pos)) if len(pos) else max(abs(t[0]), 1.0) * 1e-12
    eps = base * 1e-6
    t = t.copy()
    dup = 0
    for k in np.nonzero(d == 0)[0]:
        t[k + 1] = t[k] + eps
        dup += 1
    if np.any(np.diff(t) <= 0):  # 3개 이상 겹친 경우 순차 보정
        for k in range(1, len(t)):
            if t[k] <= t[k - 1]:
                t[k] = t[k - 1] + eps
    return t, i, dup


def seg_integrals(t, i):
    return 0.5 * (i[1:] + i[:-1]) * np.diff(t)


def integral(t, i) -> float:
    if len(t) < 2:
        return 0.0
    return float(np.sum(seg_integrals(t, i)))


def cumint(t, i) -> np.ndarray:
    c = np.zeros(len(t))
    if len(t) > 1:
        np.cumsum(seg_integrals(t, i), out=c[1:])
    return c


def cumint_at(t, i, c, tq):
    """누적적분 C(tq)를 정확히 계산 (구간 선형 가정)."""
    tq = np.asarray(tq, dtype=float)
    n = len(t)
    k = np.clip(np.searchsorted(t, tq, side="right") - 1, 0, n - 2)
    h = t[k + 1] - t[k]
    slope = (i[k + 1] - i[k]) / h
    d = np.clip(tq, t[0], t[-1]) - t[k]
    return c[k] + i[k] * d + 0.5 * slope * d * d


def interp(t, i, tq, outside="hold"):
    if outside == "zero":
        return np.interp(tq, t, i, left=0.0, right=0.0)
    return np.interp(tq, t, i)


def window_mean(t, i, tq, w, c=None):
    """tq를 중심으로 폭 w의 이동평균 (범위 밖은 잘라서 평균)."""
    if c is None:
        c = cumint(t, i)
    a = np.clip(tq - w / 2, t[0], t[-1])
    b = np.clip(tq + w / 2, t[0], t[-1])
    width = b - a
    out = np.interp(tq, t, i)
    ok = width > 0
    out[ok] = (cumint_at(t, i, c, b[ok]) - cumint_at(t, i, c, a[ok])) / width[ok]
    return out


def natural_period(t, sampled: bool = True) -> float:
    """반복/이어붙이기 때 다음 조각이 시작할 간격.

    샘플링된 데이터(측정/표)이고 균일 간격이면 N*dt (마지막 샘플 다음 칸에서 다음 조각 시작),
    PWL 꺾은점이거나 비균일이면 끝-시작 (마지막 점과 다음 조각 첫 점이 같은 시각).
    """
    dt = uniform_dt(t) if sampled and len(t) >= 3 else None
    if dt is not None:
        return len(t) * dt
    return float(t[-1] - t[0])


def check_points(n: int, what: str = "결과"):
    if n > MAX_POINTS:
        raise UserInputError(
            f"{what} 파형이 {n:,}점이 되어 너무 큽니다 (최대 {MAX_POINTS:,}점). "
            "resample_dt로 간격을 키우거나 구간을 잘라서 줄여주세요.")


def sampling_text(t) -> str:
    dt = uniform_dt(t)
    if dt is not None:
        return f"균일 간격 dt={fmt_t(dt)} ({fmt_si(1 / dt, 'Hz')})"
    d = np.diff(t)
    if len(d) == 0:
        return "1점"
    return f"비균일 간격 (최소 {fmt_t(float(d.min()))}, 중앙값 {fmt_t(float(np.median(d)))})"


def quick_summary(t, i) -> dict:
    dur = float(t[-1] - t[0]) if len(t) else 0.0
    mean = integral(t, i) / dur if dur > 0 else float(i[0]) if len(i) else None
    return {
        "points": int(len(t)),
        "t_start": fmt_t(float(t[0])) if len(t) else None,
        "t_end": fmt_t(float(t[-1])) if len(t) else None,
        "duration": fmt_t(dur),
        "sampling": sampling_text(t),
        "mean": fmt_i(mean),
        "max": fmt_i(float(np.max(i))) if len(i) else None,
        "min": fmt_i(float(np.min(i))) if len(i) else None,
    }


# ─────────────────────────────────────────────────────────────────────────────
# 구간 자르기 / 변환
# ─────────────────────────────────────────────────────────────────────────────
def crop(t, i, a=None, b=None):
    a = t[0] if a is None else max(float(a), float(t[0]))
    b = t[-1] if b is None else min(float(b), float(t[-1]))
    if not b > a:
        raise UserInputError(
            f"지정한 구간({fmt_t(a)} ~ {fmt_t(b)})이 파형 범위({fmt_t(float(t[0]))} ~ {fmt_t(float(t[-1]))})와 겹치지 않거나 길이가 0입니다.")
    m = (t > a) & (t < b)
    tt = np.concatenate([[a], t[m], [b]])
    ii = np.concatenate([[np.interp(a, t, i)], i[m], [np.interp(b, t, i)]])
    keep = np.concatenate([[True], np.diff(tt) > 0])
    return tt[keep], ii[keep]


def resample(t, i, dt):
    if dt <= 0:
        raise UserInputError("resample_dt는 0보다 커야 합니다.")
    n = int(np.floor((t[-1] - t[0]) / dt * (1 + 1e-12))) + 1
    check_points(n, "재샘플링")
    if n < 2:
        raise UserInputError(f"resample_dt({fmt_t(dt)})가 파형 길이({fmt_t(float(t[-1] - t[0]))})보다 큽니다.")
    tn = t[0] + np.arange(n) * dt
    d = np.diff(t)
    med = float(np.median(d))
    if dt > 1.5 * med:
        return tn, window_mean(t, i, tn, dt), "구간 평균(전하량 보존)"
    return tn, np.interp(tn, t, i), "선형보간"


def smooth(t, i, w):
    if w <= 0:
        raise UserInputError("smooth_time은 0보다 커야 합니다.")
    return window_mean(t, i, t, w)


def place_sequence(pieces, period=None, fill="hold", sampled=None):
    """조각들을 시간 순서대로 이어 붙인다. pieces: [(t, i), ...]

    period가 None이면 각 조각의 자연 주기(균일 샘플이면 N*dt, 아니면 길이)만큼 띄운다.
    period가 조각 길이보다 길면 빈 구간을 fill('hold'=직전 값 유지, 'zero'=0 A)로 채운다.
    """
    out_t, out_i = [], []
    if sampled is None:
        sampled = [True] * len(pieces)
    elif isinstance(sampled, bool):
        sampled = [sampled] * len(pieces)
    cursor = float(pieces[0][0][0])
    all_d = np.concatenate([np.diff(p[0]) for p in pieces if len(p[0]) > 1])
    eps = float(np.min(all_d[all_d > 0])) * 1e-6 if np.any(all_d > 0) else 1e-15
    prev_nat_gap = 0.0
    for (t, i), smp in zip(pieces, sampled):
        rel = t - t[0]
        seg_t = cursor + rel
        seg_i = i.copy()
        if out_t:
            pt, pi = out_t[-1][-1], out_i[-1][-1]
            gap = seg_t[0] - pt
            if gap > prev_nat_gap * (1 + 1e-6) + eps:
                if fill == "zero":
                    out_t.append(np.array([pt + eps, seg_t[0] - eps]))
                    out_i.append(np.array([0.0, 0.0]))
                else:
                    out_t.append(np.array([seg_t[0] - eps]))
                    out_i.append(np.array([pi]))
            elif abs(gap) <= eps:
                if abs(seg_i[0] - pi) <= 1e-12 * max(abs(pi), 1e-30):
                    seg_t, seg_i = seg_t[1:], seg_i[1:]
                else:
                    seg_t = seg_t.copy()
                    seg_t[0] = pt + eps
        if len(seg_t):
            out_t.append(seg_t)
            out_i.append(seg_i)
        nat = natural_period(t, smp)
        step = nat if period is None else period
        dt = uniform_dt(t) if smp and len(t) >= 3 else None
        prev_nat_gap = dt if dt is not None else 0.0
        cursor = cursor + step
        check_points(sum(len(x) for x in out_t), "이어붙인")
    tt = np.concatenate(out_t)
    ii = np.concatenate(out_i)
    keep = np.concatenate([[True], np.diff(tt) > 0])
    return tt[keep], ii[keep]


def repeat(t, i, count, period=None, fill="hold", sampled=True):
    if count < 1:
        raise UserInputError("repeat는 1 이상이어야 합니다.")
    nat = natural_period(t, sampled)
    if period is not None:
        if period <= 0:
            raise UserInputError("repeat_period는 0보다 커야 합니다.")
        if period < (t[-1] - t[0]) * (1 - 1e-9):
            raise UserInputError(
                f"repeat_period({fmt_t(period)})가 파형 길이({fmt_t(float(t[-1] - t[0]))})보다 짧습니다. "
                "먼저 t_start/t_end로 파형을 자르거나 주기를 늘려주세요.")
    check_points(len(t) * count, "반복")
    tt, ii = place_sequence([(t, i)] * count, period=period, fill=fill, sampled=sampled)
    return tt, ii, (nat if period is None else period)


# ─────────────────────────────────────────────────────────────────────────────
# 여러 파형 합치기
# ─────────────────────────────────────────────────────────────────────────────
def combine_sum(profs, weights, outside="zero"):
    """가중합. profs: [(t, i)], outside: zero|hold|crop. 반환 (t, i, notes)"""
    notes = []
    t0s = [float(p[0][0]) for p in profs]
    t1s = [float(p[0][-1]) for p in profs]
    same = all(len(p[0]) == len(profs[0][0]) and np.array_equal(p[0], profs[0][0]) for p in profs)
    if same:
        tot = np.zeros(len(profs[0][0]))
        for (t, i), w in zip(profs, weights):
            tot += w * i
        return profs[0][0].copy(), tot, notes
    if outside == "crop":
        a, b = max(t0s), min(t1s)
        if not b > a:
            raise UserInputError("파형들의 시간 범위가 겹치지 않아 crop(공통 구간) 방식으로 합칠 수 없습니다. outside='zero'를 쓰거나 time_shift로 맞춰주세요.")
        notes.append(f"공통 구간 {fmt_t(a)} ~ {fmt_t(b)}만 사용했습니다.")
    else:
        a, b = min(t0s), max(t1s)
    meds = [float(np.median(np.diff(p[0]))) for p in profs if len(p[0]) > 1]
    eps = (min(meds) if meds else 1e-12) * 1e-6
    parts = [p[0][(p[0] >= a) & (p[0] <= b)] for p in profs]
    parts.append(np.array([a, b]))
    if outside == "zero":
        for t0, t1 in zip(t0s, t1s):
            if t0 > a:
                parts.append(np.array([t0 - eps]))
            if t1 < b:
                parts.append(np.array([t1 + eps]))
    n_est = sum(len(x) for x in parts)
    check_points(n_est, "합친")
    tol = eps * 0.5
    grid = np.unique(np.concatenate(parts))
    if len(grid) > 2:  # 부동소수 오차로 생긴 거의 같은 점 제거
        keep = np.concatenate([[True], np.diff(grid) > tol])
        grid = grid[keep]
    tot = np.zeros(len(grid))
    for (t, i), w, t0, t1 in zip(profs, weights, t0s, t1s):
        v = np.interp(np.clip(grid, t0, t1), t, i)
        if outside == "zero":   # 경계에서 tol 이내는 안쪽으로 본다 (부동소수 오차 흡수)
            v[(grid < t0 - tol) | (grid > t1 + tol)] = 0.0
        tot += w * v
    if outside == "zero":
        short = [k for k, (t0, t1) in enumerate(zip(t0s, t1s)) if t0 > a or t1 < b]
        if short:
            notes.append("시간 범위가 짧은 파형은 범위 밖을 0 A로 간주했습니다.")
    elif outside == "hold":
        notes.append("시간 범위가 짧은 파형은 범위 밖에서 처음/마지막 값을 유지한다고 간주했습니다.")
    return grid, tot, notes


# ─────────────────────────────────────────────────────────────────────────────
# 통계
# ─────────────────────────────────────────────────────────────────────────────
def wpercentile(t, i, q):
    """시간가중 백분위수 (q: 0~100)."""
    n = len(t)
    if n == 1:
        return float(i[0])
    w = np.empty(n)
    d = np.diff(t)
    w[0] = d[0] / 2
    w[-1] = d[-1] / 2
    w[1:-1] = (d[:-1] + d[1:]) / 2
    order = np.argsort(i, kind="stable")
    cw = np.cumsum(w[order])
    k = int(np.searchsorted(cw, q / 100.0 * cw[-1]))
    return float(i[order][min(k, n - 1)])


def levels(t, i):
    return wpercentile(t, i, 5), wpercentile(t, i, 95)


def resolve_threshold(t, i, thr_spec):
    """threshold: None(자동) / 숫자[A] / '5mA' / '30%'(idle~active 레벨 사이 비율)."""
    lo, hi = levels(t, i)
    if thr_spec is None:
        if hi - lo <= max(1e-15, 0.02 * max(abs(hi), abs(lo))):
            return None, lo, hi, "자동 기준 불가 (거의 일정한 파형)"
        return (lo + hi) / 2, lo, hi, f"자동: idle 레벨({fmt_i(lo)})과 active 레벨({fmt_i(hi)})의 중간"
    if isinstance(thr_spec, str) and thr_spec.strip().endswith("%"):
        pct = float(thr_spec.strip()[:-1])
        return lo + pct / 100 * (hi - lo), lo, hi, f"idle({fmt_i(lo)})~active({fmt_i(hi)}) 레벨의 {pct:g}% 지점"
    return float(thr_spec), lo, hi, "사용자 지정"


def insert_crossings(t, i, thr):
    s = i - thr
    k = np.nonzero(s[:-1] * s[1:] < 0)[0]
    if len(k) == 0:
        return t, i
    tc = t[k] + (thr - i[k]) * (t[k + 1] - t[k]) / (i[k + 1] - i[k])
    return np.insert(t, k + 1, tc), np.insert(i, k + 1, np.full(len(k), thr))


def _runs(mask):
    """True 구간들의 (시작, 끝) 인덱스 (끝 포함)."""
    if len(mask) == 0:
        return np.zeros((0, 2), dtype=int)
    m = np.concatenate([[False], mask, [False]]).astype(np.int8)
    d = np.diff(m)
    starts = np.nonzero(d == 1)[0]
    ends = np.nonzero(d == -1)[0] - 1
    return np.stack([starts, ends], axis=1)


def active_idle(t, i, thr, min_duration=None, merge_gap=None, max_list=10):
    t2, i2 = insert_crossings(t, i, thr)
    h = np.diff(t2)
    mid = 0.5 * (i2[1:] + i2[:-1])
    act = mid > thr
    runs = _runs(act)
    notes = []
    if merge_gap and len(runs) > 1:
        merged = [list(runs[0])]
        for s, e in runs[1:]:
            gap = t2[s] - t2[merged[-1][1] + 1]
            if gap < merge_gap:
                merged[-1][1] = e
            else:
                merged.append([s, e])
        if len(merged) < len(runs):
            notes.append(f"{fmt_t(merge_gap)}보다 짧은 idle 틈 {len(runs) - len(merged)}개를 active로 합쳤습니다.")
        runs = np.array(merged, dtype=int)
    if min_duration and len(runs):
        dur = t2[runs[:, 1] + 1] - t2[runs[:, 0]]
        keep = dur >= min_duration
        if not np.all(keep):
            notes.append(f"{fmt_t(min_duration)}보다 짧은 active 구간 {int(np.sum(~keep))}개를 idle로 처리했습니다.")
        runs = runs[keep]
    mask = np.zeros(len(h), dtype=bool)
    if len(runs):
        cnt = np.zeros(len(h) + 1, dtype=np.int64)
        np.add.at(cnt, runs[:, 0], 1)
        np.add.at(cnt, runs[:, 1] + 1, -1)
        mask = np.cumsum(cnt[:-1]) > 0
    si = 0.5 * (i2[1:] + i2[:-1]) * h
    T = float(t2[-1] - t2[0])
    ta = float(np.sum(h[mask]))
    ti = T - ta
    qa = float(np.sum(si[mask]))
    qi = float(np.sum(si[~mask]))
    res = {
        "active_mean": fmt_i(qa / ta) if ta > 0 else None,
        "idle_mean": fmt_i(qi / ti) if ti > 0 else None,
        "active_ratio": fmt_pct(ta / T) if T > 0 else None,
        "active_time": fmt_t(ta),
        "idle_time": fmt_t(ti),
    }
    if len(runs):
        q = np.concatenate([[0.0], np.cumsum(si)])
        st = t2[runs[:, 0]]
        en = t2[runs[:, 1] + 1]
        dur = en - st
        means = (q[runs[:, 1] + 1] - q[runs[:, 0]]) / np.where(dur > 0, dur, 1)
        b = {
            "count": int(len(runs)),
            "duration_min": fmt_t(float(dur.min())),
            "duration_mean": fmt_t(float(dur.mean())),
            "duration_max": fmt_t(float(dur.max())),
        }
        if len(runs) >= 2:
            per = np.diff(st)
            b["period_mean"] = fmt_t(float(per.mean()))
            b["period_min"] = fmt_t(float(per.min()))
            b["period_max"] = fmt_t(float(per.max()))
        lst = []
        for k in range(min(max_list, len(runs))):
            s, e = runs[k]
            lst.append({"start": fmt_t(float(st[k])), "end": fmt_t(float(en[k])), "duration": fmt_t(float(dur[k])),
                        "mean": fmt_i(float(means[k])), "peak": fmt_i(float(np.max(i2[s:e + 2])))})
        b["first_bursts"] = lst
        if len(runs) > max_list:
            b["first_bursts_note"] = f"앞의 {max_list}개만 표시 (전체 {len(runs)}개)"
        if st[0] <= t2[0]:
            notes.append("파형이 active 상태로 시작합니다 (첫 burst는 잘린 것일 수 있음).")
        if en[-1] >= t2[-1]:
            notes.append("파형이 active 상태로 끝납니다 (마지막 burst는 잘린 것일 수 있음).")
        res["bursts"] = b
    else:
        res["bursts"] = {"count": 0}
    if notes:
        res["notes"] = notes
    return res


def window_extremes(t, i, w):
    T = t[-1] - t[0]
    if w > T:
        raise UserInputError(f"window({fmt_t(w)})가 분석 구간 길이({fmt_t(float(T))})보다 깁니다.")
    c = cumint(t, i)
    cand = np.concatenate([t, t - w, [t[0], t[-1] - w]])
    cand = cand[(cand >= t[0]) & (cand <= t[-1] - w)]
    if len(cand) > 4 * MAX_POINTS:
        cand = cand[:: int(np.ceil(len(cand) / (4 * MAX_POINTS)))]
    avg = (cumint_at(t, i, c, cand + w) - cumint_at(t, i, c, cand)) / w
    kx, kn = int(np.argmax(avg)), int(np.argmin(avg))
    return {
        "length": fmt_t(w),
        "max_avg": fmt_i(float(avg[kx])),
        "max_avg_start": fmt_t(float(cand[kx])),
        "min_avg": fmt_i(float(avg[kn])),
        "min_avg_start": fmt_t(float(cand[kn])),
    }


def basic_stats(t, i) -> dict:
    T = float(t[-1] - t[0])
    q = integral(t, i)
    a, b = i[:-1], i[1:]
    h = np.diff(t)
    rms = float(np.sqrt(np.sum(h * (a * a + a * b + b * b) / 3) / T)) if T > 0 else abs(float(i[0]))
    kx, kn = int(np.argmax(i)), int(np.argmin(i))
    out = {
        "mean": fmt_i(q / T if T > 0 else float(i[0])),
        "rms": fmt_i(rms),
        "max": fmt_i(float(i[kx])),
        "max_at": fmt_t(float(t[kx])),
        "min": fmt_i(float(i[kn])),
        "min_at": fmt_t(float(t[kn])),
        "peak_to_peak": fmt_i(float(i[kx] - i[kn])),
        "charge": f"{fmt_si(q, 'C')} (= {fmt_si(q / 3600, 'Ah')})",
    }
    if len(t) > 1:
        didt = np.diff(i) / h
        ku, kd = int(np.argmax(didt)), int(np.argmin(didt))
        out["max_rise_didt"] = f"{fmt_didt(float(didt[ku]))} @ {fmt_t(float((t[ku] + t[ku + 1]) / 2))}"
        out["max_fall_didt"] = f"{fmt_didt(float(didt[kd]))} @ {fmt_t(float((t[kd] + t[kd + 1]) / 2))}"
    return out


# ─────────────────────────────────────────────────────────────────────────────
# edge 찾기 / 정렬
# ─────────────────────────────────────────────────────────────────────────────
def find_edges(t, i, direction="rising", thr_spec=None):
    thr, lo, hi, how = resolve_threshold(t, i, thr_spec)
    if thr is None:
        raise UserInputError("파형이 거의 일정해서 켜지는 edge를 찾을 수 없습니다. threshold를 직접 지정해 주세요.")
    span = max(hi - lo, 1e-30)
    hyst = 0.1 * span
    sign = 1.0 if direction == "rising" else -1.0
    x = sign * (i - thr)
    lw, up = -hyst, hyst
    idx = np.nonzero((x < lw) | (x > up))[0]
    if len(idx) < 2:
        return [], thr, how
    state = x[idx] > up
    tr = np.nonzero((~state[:-1]) & state[1:])[0]
    edges = []
    for k in tr:
        a, b = int(idx[k]), int(idx[k + 1])
        seg = x[a:b + 1]
        j = np.nonzero((seg[:-1] <= 0) & (seg[1:] > 0))[0]
        if len(j):
            jj = a + int(j[-1])
            t_c = t[jj] + (0 - x[jj]) * (t[jj + 1] - t[jj]) / (x[jj + 1] - x[jj])
        else:
            t_c = 0.5 * (t[a] + t[b])
        slope = (i[b] - i[a]) / (t[b] - t[a])
        edges.append({"t": float(t_c), "slope": float(slope), "a": a, "b": b})
    return edges, thr, how


def pick_edge(edges, edge_index):
    if not edges:
        return None
    if edge_index is None:
        return max(edges, key=lambda e: abs(e["slope"]))
    k = int(edge_index)
    if k == 0 or abs(k) > len(edges):
        raise UserInputError(f"edge_index={k}가 범위를 벗어났습니다. 찾은 edge는 {len(edges)}개입니다 (1부터, -1은 마지막).")
    return edges[k - 1] if k > 0 else edges[k]


def max_didt_time(t, i, direction="rising"):
    d = np.diff(i) / np.diff(t)
    k = int(np.argmax(d) if direction == "rising" else np.argmin(d))
    return float((t[k] + t[k + 1]) / 2), float(d[k])


def xcorr_shift(tr, ir, ts, is_, max_shift=None, max_n=1 << 21):
    """target(ts,is_)를 얼마나 이동하면 ref와 가장 잘 겹치는지 (초)."""
    dr = float(np.median(np.diff(tr)))
    ds = float(np.median(np.diff(ts)))
    dt = min(dr, ds)
    span = max(tr[-1] - tr[0], ts[-1] - ts[0])
    if span / dt > max_n:
        dt = span / max_n
    gr = tr[0] + np.arange(int((tr[-1] - tr[0]) / dt) + 1) * dt
    gs = ts[0] + np.arange(int((ts[-1] - ts[0]) / dt) + 1) * dt
    r = window_mean(tr, ir, gr, dt) if dt > 1.5 * dr else np.interp(gr, tr, ir)
    s = window_mean(ts, is_, gs, dt) if dt > 1.5 * ds else np.interp(gs, ts, is_)
    r = r - r.mean()
    s = s - s.mean()
    n = len(r) + len(s)
    nfft = 1 << int(np.ceil(np.log2(n)))
    c = np.fft.irfft(np.fft.rfft(r, nfft) * np.conj(np.fft.rfft(s, nfft)), nfft)
    lags = np.arange(nfft)
    lags[lags > nfft // 2] -= nfft
    valid = (lags > -len(s)) & (lags < len(r))
    shifts = tr[0] - ts[0] + lags * dt
    if max_shift is not None:
        valid &= np.abs(shifts) <= max_shift
    if not np.any(valid):
        raise UserInputError("max_shift 범위 안에서 정렬 위치를 찾지 못했습니다. max_shift를 늘려주세요.")
    cv = np.where(valid, c, -np.inf)
    k = int(np.argmax(cv))
    norm = np.sqrt(np.sum(r * r) * np.sum(s * s))
    coef = float(c[k] / norm) if norm > 0 else 0.0
    return float(shifts[k]), coef, dt
