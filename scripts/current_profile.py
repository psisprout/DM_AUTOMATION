#!/usr/bin/env python3
"""
current_profile.py — 전류 파형(current profile)을 불러오고, 연산하고, 분석하고, 저장한다.

불러온 파형과 연산 결과는 작업공간(기본 ~/.current_profiles, 환경변수 CP_WORKSPACE로 변경)에
'이름.npz'로 저장되고, 이후 명령은 이름으로 파형을 가리킨다.

사용 예:
    python current_profile.py load --path=/data/burst.csv --name=burst
    python current_profile.py analyze --name=burst --window=1ms
    python current_profile.py transform --name=burst --time-scale=2 --repeat=3
    python current_profile.py combine --names idle burst_ts2 --weights=1,1
    python current_profile.py align --ref=meas --targets sim
    python current_profile.py plot --names meas sim_aligned
    python current_profile.py export --name=meas --output=/data/out.csv
    (모든 명령에 --json 을 붙이면 마지막 줄에 JSON 한 줄을 출력: MCP 연동용)

숫자 옵션은 SI 단위 숫자(초, A) 또는 '2ms', '10uA', '1MHz' 같은 단위 붙은 문자열을 받는다.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import traceback
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

from cplib import formats, ops  # noqa: E402
from cplib.common import (Profile, UserInputError, check_input_file, check_name, check_output_file,  # noqa: E402
                          emit, exists, fmt_i, fmt_si, fmt_t, list_names, load_profile, log, parse_quantity,
                          profile_path, read_meta, sanitize_name, save_profile, unique_name, unit_info, ws_dir)


def q_time(v, what):
    return parse_quantity(v, "time", what)


def q_cur(v, what):
    return parse_quantity(v, "current", what)


def _weights(s, n):
    if s is None or str(s).strip() == "":
        return [1.0] * n
    try:
        w = [float(x) for x in str(s).replace(";", ",").split(",") if x.strip()]
    except ValueError:
        raise UserInputError(f"weights는 숫자 목록이어야 합니다 (예: '1,-1'). 입력값: {s}")
    if len(w) != n:
        raise UserInputError(f"weights 개수({len(w)})가 파형 개수({n})와 다릅니다.")
    return w


def _target_name(requested, default, overwrite, taken=None):
    if requested:
        check_name(requested, "result_name")
        if exists(requested) and not overwrite:
            raise UserInputError(
                f"작업공간에 이미 '{requested}' 파형이 있습니다. 다른 이름을 정하거나 overwrite=true로 덮어써야 합니다.")
        return requested
    return unique_name(default, taken)


def _hist(parent: Profile, step: str):
    return (parent.history + [step])[-60:]


def _ok(summary, **kw):
    d = {"status": "ok", "summary": summary}
    d.update(kw)
    d.setdefault("created", [])
    d.setdefault("modified", [])
    return d


def _prof_entry(p: Profile):
    s = ops.quick_summary(p.t, p.i)
    s["name"] = p.name
    return s


# ─────────────────────────────────────────────────────────────────────────────
def cmd_load(a):
    path = check_input_file(a.path)
    t_unit = a.time_unit
    if t_unit and unit_info(t_unit)[0] != "time":
        raise UserInputError(f"time_unit '{t_unit}'은(는) 시간 단위가 아닙니다 (s, ms, us, ns 등).")
    if a.current_unit and unit_info(a.current_unit)[0] != "current":
        raise UserInputError(f"current_unit '{a.current_unit}'은(는) 전류 단위가 아닙니다 (A, mA, uA, nA 등).")
    dt = q_time(a.dt, "dt") if a.dt else None
    if a.sample_rate:
        fs = parse_quantity(a.sample_rate, "freq", "sample_rate")
        if not fs or fs <= 0:
            raise UserInputError("sample_rate는 0보다 커야 합니다.")
        dt = 1.0 / fs
    if dt is not None and dt <= 0:
        raise UserInputError("dt는 0보다 커야 합니다.")
    shunt = parse_quantity(a.shunt_ohm, "resistance", "shunt_ohm") if a.shunt_ohm else None
    if shunt is not None and shunt <= 0:
        raise UserInputError("shunt_ohm은 0보다 커야 합니다.")
    opts = formats.LoadOptions(a.current_col, a.time_col, t_unit, a.current_unit, dt, shunt, a.block)
    t0 = time.time()
    parsed = formats.read_file(path, sheet=a.sheet)
    results = formats.build_profiles(parsed, opts)
    log(f"parsed {path} as {parsed.fmt} in {time.time() - t0:.2f}s")
    base = a.name or sanitize_name(path.stem)
    if a.name:
        check_name(a.name, "name")
    multi = len(results) > 1
    names, taken = [], set()
    for r in results:
        if multi:
            parts = []
            if len({x["block_index"] for x in results}) > 1:
                parts.append(sanitize_name(r["label"]) if r["label"] and not r["label"].startswith("block")
                             else f"b{r['block_index']}")
            if len({x["column"] for x in results}) > 1:
                parts.append(sanitize_name(r["column"]))
            nm = sanitize_name(base + "_" + "_".join(parts))
        else:
            nm = base
        if a.name:
            if (exists(nm) and not a.overwrite) or nm in taken:
                raise UserInputError(
                    f"작업공간에 이미 '{nm}' 파형이 있습니다. 다른 name을 정하거나 overwrite=true로 덮어써야 합니다.")
        else:
            nm = unique_name(nm, taken)
        taken.add(nm)
        names.append(nm)
    profiles, warnings, files = [], [], []
    for nm, r in zip(names, results):
        col = r["column"] + (f" @ {r['label']}" if r["info"].get("block") else "")
        meta = {"source": str(path), "format": parsed.fmt, "load_info": r["info"], "pwl": "PWL" in parsed.fmt,
                "history": [f"load('{path.name}', 열='{col}')"]}
        p = Profile(nm, r["t"], r["i"], meta)
        files.append(save_profile(p, overwrite=bool(a.name and a.overwrite)))
        e = _prof_entry(p)
        e.update({"time_from": r["info"].get("time_column"), "time_unit": r["info"].get("time_unit"),
                  "current_column": r["info"].get("current_column"), "current_unit": r["info"].get("current_unit")})
        if r["info"].get("block"):
            e["block"] = r["info"]["block"]
        profiles.append(e)
        for w in r["warnings"]:
            if w not in warnings:
                warnings.append(w)
    for n_ in parsed.notes:
        if n_ not in warnings:
            warnings.append(n_)
    cols = formats.describe_columns(parsed.blocks[results[0]["block_index"] - 1].columns) if results else []
    if len(profiles) == 1:
        p0 = profiles[0]
        summary = (f"{path} ({parsed.fmt})의 '{p0['current_column']}' 열을 '{names[0]}' 이름으로 불러왔습니다: "
                   f"{p0['points']:,}점, {p0['t_start']} ~ {p0['t_end']} ({p0['duration']}), 평균 {p0['mean']}, "
                   f"최대 {p0['max']}, 최소 {p0['min']}.")
    else:
        summary = f"{path} ({parsed.fmt})에서 파형 {len(profiles)}개를 불러왔습니다: {', '.join(names)}."
    if warnings:
        summary += f" 확인 필요 사항 {len(warnings)}건이 warnings에 있습니다."
    return _ok(summary, names=names, profiles=profiles, file_format=parsed.fmt, columns=cols,
               warnings=warnings, workspace_files=files)


def cmd_list(a):
    names = list_names()
    rows, total = [], 0
    for nm in names:
        pth = profile_path(nm)
        size = pth.stat().st_size
        total += size
        try:
            m = read_meta(nm)
        except Exception as e:  # 손상 파일
            rows.append({"name": nm, "error": f"읽기 실패: {e}"})
            continue
        s = m.get("summary", {})
        hist = m.get("history", [])
        rows.append({"name": nm, "points": s.get("points"), "duration": s.get("duration"),
                     "t_start": s.get("t_start"), "mean": s.get("mean"), "max": s.get("max"),
                     "made_by": " → ".join(hist[-3:]) if hist else None, "saved_at": m.get("saved_at")})
    lim = 100
    summary = (f"작업공간({ws_dir()})에 파형 {len(names)}개가 있습니다 (총 {total / 1e6:.1f} MB)."
               if names else f"작업공간({ws_dir()})이 비어 있습니다. 먼저 load로 파일을 불러오세요.")
    return _ok(summary, workspace=str(ws_dir()), count=len(names), profiles=rows[:lim],
               note=(f"최근 {lim}개만 표시" if len(rows) > lim else None))


def cmd_analyze(a):
    p = load_profile(a.name)
    t, i = p.t, p.i
    ts, te = q_time(a.t_start, "t_start"), q_time(a.t_end, "t_end")
    if ts is not None or te is not None:
        t, i = ops.crop(t, i, ts, te)
    notes = []
    if a.smooth:
        w = q_time(a.smooth, "smooth_time")
        i = ops.smooth(t, i, w)
        notes.append(f"{fmt_t(w)} 이동평균으로 다듬은 파형으로 계산했습니다.")
    out = {"name": p.name, "range": {"t_start": fmt_t(float(t[0])), "t_end": fmt_t(float(t[-1])),
                                     "duration": fmt_t(float(t[-1] - t[0])), "points": int(len(t)),
                                     "sampling": ops.sampling_text(t)}}
    b = ops.basic_stats(t, i)
    out["basic"] = b
    thr_spec = a.threshold
    if thr_spec is not None and not str(thr_spec).strip().endswith("%"):
        thr_spec = q_cur(thr_spec, "threshold")
    thr, lo, hi, how = ops.resolve_threshold(t, i, thr_spec)
    ai = {"threshold": fmt_i(thr) if thr is not None else None, "threshold_rule": how,
          "idle_level_p5": fmt_i(lo), "active_level_p95": fmt_i(hi)}
    if thr is not None:
        ai.update(ops.active_idle(t, i, thr, q_time(a.min_duration, "min_duration"),
                                  q_time(a.merge_gap, "merge_gap")))
    out["active_idle"] = ai
    if a.window:
        out["window_average"] = ops.window_extremes(t, i, q_time(a.window, "window"))
    if a.supply_voltage:
        V = parse_quantity(a.supply_voltage, "voltage", "supply_voltage")
        T = float(t[-1] - t[0])
        Q = ops.integral(t, i)
        out["power"] = {"supply_voltage": fmt_si(V, "V"), "mean_power": fmt_si(V * Q / T if T > 0 else 0, "W"),
                        "energy": f"{fmt_si(V * Q, 'J')} (= {fmt_si(V * Q / 3600, 'Wh')})"}
    if notes:
        out["notes"] = notes
    s = f"'{p.name}' ({out['range']['t_start']} ~ {out['range']['t_end']}): 평균 {b['mean']}, 최대 {b['max']} @ {b['max_at']}, 최소 {b['min']}"
    if thr is not None and ai.get("active_mean"):
        s += (f", active 평균 {ai['active_mean']} (비율 {ai['active_ratio']}), idle 평균 {ai['idle_mean']}"
              f", burst {ai['bursts']['count']}개 (기준 {ai['threshold']})")
    if "window_average" in out:
        w = out["window_average"]
        s += f", {w['length']} 구간 최대 평균 {w['max_avg']} @ {w['max_avg_start']}"
    if "power" in out:
        s += f", 평균 전력 {out['power']['mean_power']}"
    return _ok(s + ".", **out)


def cmd_transform(a):
    p = load_profile(a.name)
    t, i = p.t.copy(), p.i.copy()
    steps, suffix = [], []
    pwl = bool(p.meta.get("pwl"))
    ts, te = q_time(a.t_start, "t_start"), q_time(a.t_end, "t_end")
    if ts is not None or te is not None:
        t, i = ops.crop(t, i, ts, te)
        steps.append(f"자르기 {fmt_t(float(t[0]))}~{fmt_t(float(t[-1]))}")
        suffix.append("crop")
    if a.rezero:
        steps.append(f"시작을 0초로 이동({fmt_t(-float(t[0]))})")
        t = t - t[0]
        suffix.append("z")
    if a.time_scale is not None:
        k = float(a.time_scale)
        if not k > 0:
            raise UserInputError("time_scale은 0보다 커야 합니다 (2 = 2배 느리게/길게, 0.5 = 2배 빠르게/짧게).")
        t = t * k
        steps.append(f"시간 ×{k:g}")
        suffix.append(f"ts{k:g}")
    if a.time_shift is not None:
        sh = q_time(a.time_shift, "time_shift")
        t = t + sh
        steps.append(f"시간 이동 {'+' if sh >= 0 else ''}{fmt_t(sh)}")
        suffix.append("sh")
    if a.current_scale is not None:
        k = float(a.current_scale)
        i = i * k
        steps.append(f"전류 ×{k:g}")
        suffix.append(f"cs{k:g}")
    if a.current_offset is not None:
        off = q_cur(a.current_offset, "current_offset")
        i = i + off
        steps.append(f"전류 {'+' if off >= 0 else ''}{fmt_i(off)}")
        suffix.append("off")
    if a.clip_negative:
        nneg = int(np.sum(i < 0))
        i = np.maximum(i, 0.0)
        steps.append(f"음수 전류 0으로 ({nneg:,}점)")
        suffix.append("clip")
    if a.smooth:
        w = q_time(a.smooth, "smooth_time")
        i = ops.smooth(t, i, w)
        steps.append(f"{fmt_t(w)} 이동평균")
        suffix.append("sm")
    if a.resample_dt:
        dtn = q_time(a.resample_dt, "resample_dt")
        t, i, how = ops.resample(t, i, dtn)
        pwl = False
        steps.append(f"dt={fmt_t(dtn)} 재샘플링({how})")
        suffix.append("rs")
    if a.repeat is not None and a.repeat != 1 or a.repeat_period:
        n = int(a.repeat or 1)
        per = q_time(a.repeat_period, "repeat_period") if a.repeat_period else None
        t, i, used = ops.repeat(t, i, n, per, a.repeat_fill, sampled=not pwl)
        steps.append(f"{n}회 반복(주기 {fmt_t(used)}" + (f", 빈 구간 {a.repeat_fill}" if per else "") + ")")
        suffix.append(f"rep{n}")
    if not steps:
        raise UserInputError("바꿀 내용이 없습니다. time_scale, current_scale, repeat, t_start/t_end 등 하나 이상 지정해 주세요.")
    nm = _target_name(a.result_name, f"{p.name}_{'_'.join(suffix)}"[:70], a.overwrite)
    q = Profile(nm, t, i, {"source": p.meta.get("source"), "format": p.meta.get("format"), "pwl": pwl,
                           "history": _hist(p, f"transform({p.name}: {', '.join(steps)})")})
    f = save_profile(q, overwrite=a.overwrite)
    e = _prof_entry(q)
    return _ok(f"'{p.name}'에 [{', '.join(steps)}]를 적용해 '{nm}'로 저장했습니다: {e['points']:,}점, "
               f"{e['t_start']} ~ {e['t_end']}, 평균 {e['mean']}, 최대 {e['max']}.",
               result_name=nm, applied=steps, profile=e, workspace_files=[f])


def cmd_combine(a):
    names = a.names
    if not names:
        raise UserInputError("합칠 파형 이름(names)이 필요합니다.")
    profs = [load_profile(n) for n in names]
    w = _weights(a.weights, len(profs))
    notes = []
    if a.mode == "sum":
        if len(profs) < 2 and w == [1.0]:
            raise UserInputError("sum은 파형이 2개 이상이어야 합니다 (하나를 배율만 바꾸려면 transform의 current_scale).")
        t, i, notes = ops.combine_sum([(p.t, p.i) for p in profs], w, a.outside)
        terms = []
        for k, (p, wk) in enumerate(zip(profs, w)):
            sgn = "-" if wk < 0 else "+"
            mag = abs(wk)
            term = p.name if mag == 1 else f"{mag:g}x{p.name}"
            terms.append(term if k == 0 and wk >= 0 else (f"-{term}" if k == 0 else f"{sgn}{term}"))
        expr = "".join(terms)
        default = expr if len(expr) <= 60 else "combined"
        step = f"sum({' '.join(f'{wk:+g}*{p.name}' for p, wk in zip(profs, w))}, 범위밖={a.outside})"
        desc = f"{expr} (가중합)"
    else:
        pieces = [(p.t, p.i * wk) for p, wk in zip(profs, w)]
        t, i = ops.place_sequence(pieces, sampled=[not p.meta.get("pwl") for p in profs])
        default = "~".join(p.name for p in profs)
        default = default if len(default) <= 60 else "concat"
        step = f"concat({', '.join(p.name for p in profs)})"
        desc = f"{' → '.join(p.name for p in profs)} (순서대로 이어붙이기)"
    if a.dt:
        dtn = q_time(a.dt, "dt")
        t, i, how = ops.resample(t, i, dtn)
        notes.append(f"결과를 dt={fmt_t(dtn)}로 재샘플링했습니다({how}).")
    nm = _target_name(a.result_name, sanitize_name(default), a.overwrite)
    hist = [f"[{p.name}] " + " → ".join(p.history[-3:]) for p in profs][:6] + [step]
    q = Profile(nm, t, i, {"history": hist, "pwl": all(p.meta.get("pwl") for p in profs) and not a.dt})
    f = save_profile(q, overwrite=a.overwrite)
    e = _prof_entry(q)
    return _ok(f"{desc} 결과를 '{nm}'로 저장했습니다: {e['points']:,}점, {e['t_start']} ~ {e['t_end']}, "
               f"평균 {e['mean']}, 최대 {e['max']}." + (" " + " ".join(notes) if notes else ""),
               result_name=nm, profile=e, notes=notes, workspace_files=[f])


def _edge_of(p, method, direction, thr_spec, edge_index, smooth_w):
    t, i = p.t, p.i
    if smooth_w:
        i = ops.smooth(t, i, smooth_w)
    if method == "max_didt":
        tm, slope = ops.max_didt_time(t, i, direction)
        return tm, {"time": fmt_t(tm), "didt": ops.fmt_didt(slope)}
    edges, thr, how = ops.find_edges(t, i, direction, thr_spec)
    if not edges:
        raise UserInputError(
            f"'{p.name}'에서 {'켜지는(rising)' if direction == 'rising' else '꺼지는(falling)'} edge를 찾지 못했습니다 "
            f"(기준 {fmt_i(thr)}, {how}). threshold를 지정하거나 method='max_didt'/'xcorr'를 써 보세요.")
    e = ops.pick_edge(edges, edge_index)
    k = edges.index(e) + 1
    return e["t"], {"time": fmt_t(e["t"]), "edge": f"{k}번째 / 총 {len(edges)}개", "edge_slope": ops.fmt_didt(e["slope"]),
                    "threshold": fmt_i(thr), "rule": "가장 가파른 edge" if edge_index is None else f"edge_index={edge_index}"}


def cmd_align(a):
    ref = load_profile(a.ref)
    targets = [load_profile(n) for n in a.targets]
    thr_spec = a.threshold
    if thr_spec is not None and not str(thr_spec).strip().endswith("%"):
        thr_spec = q_cur(thr_spec, "threshold")
    sw = q_time(a.smooth, "smooth_time") if a.smooth else None
    ms = q_time(a.max_shift, "max_shift") if a.max_shift else None
    out, files, names, taken = [], [], [], set()
    ref_info = None
    if a.method != "xcorr":
        t_ref, ref_info = _edge_of(ref, a.method, a.direction, thr_spec, a.edge_index, sw)
    for tp in targets:
        if a.method == "xcorr":
            shift, coef, dtg = ops.xcorr_shift(ref.t, ref.i, tp.t, tp.i, ms)
            info = {"correlation": f"{coef:.3f}", "grid_dt": fmt_t(dtg)}
        else:
            t_tg, info = _edge_of(tp, a.method, a.direction, thr_spec, a.edge_index, sw)
            shift = t_ref - t_tg
            if ms is not None and abs(shift) > ms:
                raise UserInputError(f"'{tp.name}'의 이동량 {fmt_t(shift)}가 max_shift({fmt_t(ms)})보다 큽니다. edge 선택을 확인해 주세요.")
        nm = _target_name(None, f"{tp.name}{a.suffix}", a.overwrite, taken) if not a.overwrite else \
            check_name(sanitize_name(f"{tp.name}{a.suffix}"))
        taken.add(nm)
        q = Profile(nm, tp.t + shift, tp.i.copy(), {"pwl": bool(tp.meta.get("pwl")),
                    "history": _hist(tp, f"align({tp.name} → 기준 {ref.name}, {a.method}, 이동 {fmt_t(shift)})")})
        files.append(save_profile(q, overwrite=a.overwrite))
        names.append(nm)
        out.append({"target": tp.name, "result_name": nm, "shift": ("+" if shift >= 0 else "") + fmt_t(shift),
                    "target_edge": info})
    s = f"'{ref.name}' 기준으로 정렬했습니다 ({a.method}): " + ", ".join(
        f"{o['target']} → '{o['result_name']}' (이동 {o['shift']})" for o in out) + "."
    if ref_info:
        s += f" 기준 edge 시각 {ref_info['time']}."
    return _ok(s, ref=ref.name, ref_edge=ref_info, results=out, result_names=names, workspace_files=files)


def cmd_plot(a):
    from cplib import plot
    profs = [load_profile(n) for n in a.names]
    ts, te = q_time(a.t_start, "t_start"), q_time(a.t_end, "t_end")
    if a.output:
        out, is_new = check_output_file(a.output, a.overwrite, (".png", ".svg", ".pdf"))
    else:
        d = ws_dir() / "plots"
        d.mkdir(exist_ok=True)
        stem = sanitize_name("_".join(a.names))[:60]
        out, is_new = (d / f"{stem}_{time.strftime('%Y%m%d_%H%M%S')}.png").resolve(), True
    plot.plot_profiles(profs, str(out), ts, te, a.separate, a.title)
    rng = "" if ts is None and te is None else f" ({fmt_t(ts) if ts is not None else '처음'} ~ {fmt_t(te) if te is not None else '끝'})"
    return _ok(f"{', '.join(a.names)} 그래프{rng}를 {out}에 {'저장' if is_new else '덮어써서 저장'}했습니다.",
               plot_path=str(out), created=[str(out)] if is_new else [], modified=[] if is_new else [str(out)])


def cmd_export(a):
    p = load_profile(a.name)
    fmt = a.format
    ext = Path(a.output).suffix.lower()
    if fmt == "auto":
        fmt = {".csv": "csv", ".txt": "txt", ".dat": "txt", ".tsv": "txt", ".pwl": "pwl", ".npz": "npz"}.get(ext)
        if fmt is None:
            raise UserInputError(f"확장자 '{ext or '(없음)'}'로는 저장 형식을 정할 수 없습니다. .csv/.txt/.pwl/.npz 중 하나로 하거나 format을 지정해 주세요.")
    out, is_new = check_output_file(a.output, a.overwrite)
    notes = []
    tu, cu = a.time_unit, a.current_unit
    tk, tf = unit_info(tu)
    ck, cf = unit_info(cu)
    if tk != "time" or ck != "current":
        raise UserInputError("time_unit은 s/ms/us/ns, current_unit은 A/mA/uA/nA 중 하나여야 합니다.")
    if fmt in ("pwl", "npz") and (tf != 1 or cf != 1):
        notes.append(f"{fmt} 형식은 항상 초(s)와 A로 저장합니다 (단위 옵션 무시).")
        tf = cf = 1.0
        tu, cu = "s", "A"
    t, i = p.t / tf, p.i / cf
    hist = p.history
    if fmt == "npz":
        with open(out, "wb") as fh:
            np.savez(fh, t=p.t, i=p.i, meta=np.array(json.dumps({"format": "current_profile", "name": p.name,
                                                                  "history": hist}, ensure_ascii=False)))
    else:
        sep = "," if fmt == "csv" else " " if fmt == "pwl" else "\t"
        header = []
        if fmt != "pwl" and not a.no_history:
            header = [f"# current profile '{p.name}'", f"# source: {p.meta.get('source', '-')}"]
            header += [f"# history: {h}" for h in hist[-10:]]
        if fmt != "pwl":
            header.append(f"time ({tu}){sep}current ({cu})")
        _write_table(out, t, i, sep, header)
        if fmt == "pwl" and len(t) > 200000:
            notes.append(f"PWL 점이 {len(t):,}개로 많습니다. 시뮬레이터가 느려지면 transform의 resample_dt로 줄이세요.")
    size = out.stat().st_size
    return _ok(f"'{p.name}'({len(t):,}점)을 {fmt.upper()} 형식으로 {out}에 {'새로 저장' if is_new else '덮어써서 저장'}했습니다 "
               f"(시간 {tu}, 전류 {cu}, {size / 1e6:.2f} MB)." + (" " + " ".join(notes) if notes else ""),
               output_path=str(out), format=fmt, points=int(len(t)),
               created=[str(out)] if is_new else [], modified=[] if is_new else [str(out)])


def _write_table(out, t, i, sep, header):
    try:
        import pandas as pd
        with open(out, "w", encoding="utf-8", newline="\n") as fh:
            for h in header:
                fh.write(h + "\n")
            df = pd.DataFrame({"t": t, "i": i})
            try:
                df.to_csv(fh, sep=sep, header=False, index=False, float_format="%.12g", lineterminator="\n")
            except TypeError:  # pandas < 1.5
                df.to_csv(fh, sep=sep, header=False, index=False, float_format="%.12g", line_terminator="\n")
    except ImportError:
        with open(out, "w", encoding="utf-8", newline="\n") as fh:
            for h in header:
                fh.write(h + "\n")
            np.savetxt(fh, np.column_stack([t, i]), fmt="%.12g", delimiter=sep)


def cmd_delete(a):
    gone, missing = [], []
    for n in a.names:
        pth = profile_path(n)
        if pth.exists():
            pth.unlink()
            gone.append(n)
        else:
            missing.append(n)
    if missing and not gone:
        raise UserInputError(f"작업공간에 없는 이름입니다: {', '.join(missing)}")
    s = f"작업공간에서 {', '.join(gone)}을(를) 삭제했습니다 (원본 파일은 그대로입니다)."
    if missing:
        s += f" 없는 이름은 건너뛰었습니다: {', '.join(missing)}."
    return _ok(s, deleted=gone, missing=missing)


# ─────────────────────────────────────────────────────────────────────────────
def build_parser():
    p = argparse.ArgumentParser(description="current profile(전류 파형) 도구")
    p.add_argument("--json", action="store_true", help="결과를 JSON 한 줄로 출력 (MCP 연동용)")
    sub = p.add_subparsers(dest="cmd", required=True)

    def add(name, help_):
        sp = sub.add_parser(name, help=help_)
        sp.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
        return sp

    s = add("load", "파일을 읽어 작업공간에 이름으로 저장")
    s.add_argument("--path", required=True)
    s.add_argument("--name")
    s.add_argument("--current-col")
    s.add_argument("--time-col")
    s.add_argument("--time-unit")
    s.add_argument("--current-unit")
    s.add_argument("--dt")
    s.add_argument("--sample-rate")
    s.add_argument("--shunt-ohm")
    s.add_argument("--block")
    s.add_argument("--sheet")
    s.add_argument("--overwrite", action="store_true")

    add("list", "작업공간 목록")

    s = add("analyze", "통계 분석")
    s.add_argument("--name", required=True)
    for o in ("--t-start", "--t-end", "--threshold", "--min-duration", "--merge-gap", "--window",
              "--supply-voltage", "--smooth"):
        s.add_argument(o)

    s = add("transform", "한 파형 변환")
    s.add_argument("--name", required=True)
    s.add_argument("--result-name")
    s.add_argument("--t-start")
    s.add_argument("--t-end")
    s.add_argument("--rezero", action="store_true")
    s.add_argument("--time-scale", type=float)
    s.add_argument("--time-shift")
    s.add_argument("--current-scale", type=float)
    s.add_argument("--current-offset")
    s.add_argument("--clip-negative", action="store_true")
    s.add_argument("--smooth")
    s.add_argument("--resample-dt")
    s.add_argument("--repeat", type=int)
    s.add_argument("--repeat-period")
    s.add_argument("--repeat-fill", choices=["hold", "zero"], default="hold")
    s.add_argument("--overwrite", action="store_true")

    s = add("combine", "여러 파형 합치기/이어붙이기")
    s.add_argument("--names", nargs="+", required=True)
    s.add_argument("--weights")
    s.add_argument("--mode", choices=["sum", "concat"], default="sum")
    s.add_argument("--outside", choices=["zero", "hold", "crop"], default="zero")
    s.add_argument("--dt")
    s.add_argument("--result-name")
    s.add_argument("--overwrite", action="store_true")

    s = add("align", "edge 기준 정렬")
    s.add_argument("--ref", required=True)
    s.add_argument("--targets", nargs="+", required=True)
    s.add_argument("--method", choices=["edge", "max_didt", "xcorr"], default="edge")
    s.add_argument("--direction", choices=["rising", "falling"], default="rising")
    s.add_argument("--edge-index", type=int)
    s.add_argument("--threshold")
    s.add_argument("--smooth")
    s.add_argument("--max-shift")
    s.add_argument("--suffix", default="_aligned")
    s.add_argument("--overwrite", action="store_true")

    s = add("plot", "그래프")
    s.add_argument("--names", nargs="+", required=True)
    s.add_argument("--t-start")
    s.add_argument("--t-end")
    s.add_argument("--separate", action="store_true")
    s.add_argument("--output")
    s.add_argument("--title")
    s.add_argument("--overwrite", action="store_true")

    s = add("export", "파일로 저장")
    s.add_argument("--name", required=True)
    s.add_argument("--output", required=True)
    s.add_argument("--format", choices=["auto", "csv", "txt", "pwl", "npz"], default="auto")
    s.add_argument("--time-unit", default="s")
    s.add_argument("--current-unit", default="A")
    s.add_argument("--no-history", action="store_true")
    s.add_argument("--overwrite", action="store_true")

    s = add("delete", "작업공간에서 삭제")
    s.add_argument("--names", nargs="+", required=True)
    return p


COMMANDS = {"load": cmd_load, "list": cmd_list, "analyze": cmd_analyze, "transform": cmd_transform,
            "combine": cmd_combine, "align": cmd_align, "plot": cmd_plot, "export": cmd_export, "delete": cmd_delete}


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):  # HPC(LANG=C)에서 한글 깨짐 방지
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    a = build_parser().parse_args(argv)
    as_json = getattr(a, "json", False)
    try:
        res = COMMANDS[a.cmd](a)
    except (UserInputError, FileNotFoundError, PermissionError) as e:
        msg = str(e)
        if isinstance(e, PermissionError):
            msg = f"권한이 없습니다: {e.filename or e}"
        if as_json:
            emit({"status": "error", "needs_user_input": True, "error": msg})
        else:
            print(f"오류: {msg}", file=sys.stderr)
        return 2
    except Exception as e:  # 내부 오류
        traceback.print_exc(file=sys.stderr)
        msg = f"{type(e).__name__}: {e}"
        if as_json:
            emit({"status": "error", "needs_user_input": False, "error": f"스크립트 내부 오류 — {msg}"})
        else:
            print(f"내부 오류: {msg}", file=sys.stderr)
        return 1
    res = {k: v for k, v in res.items() if v is not None}
    if as_json:
        emit(res)
    else:
        print(json.dumps(__import__("cplib.common", fromlist=["jsonable"]).jsonable(res), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
