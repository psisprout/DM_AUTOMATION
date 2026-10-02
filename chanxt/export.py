"""Outputs: a self-contained HTML plot report and CSV value tables."""

from __future__ import annotations

import csv
import datetime
import json
import math
import os
from typing import Dict, List, Optional, Sequence

import numpy as np

from .analysis import Analysis, Key, metric_name

WEB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
SPLIT_DIMS = ("metric", "model", "victim", "loc")
SPLIT_LABELS = {"metric": "Metric", "model": "Model", "victim": "Victim net", "loc": "Location"}

#: colour per curve (models of the same curve share it); 20 distinguishable hues
PALETTE = [
    "#1f77b4", "#d62728", "#2ca02c", "#ff7f0e", "#9467bd", "#8c564b", "#e377c2",
    "#17becf", "#bcbd22", "#7f7f7f", "#393b79", "#ad494a", "#637939", "#e6550d",
    "#7b4173", "#3182bd", "#31a354", "#843c39", "#d6616b", "#636363",
]
#: line style per model
DASHES = ["solid", "dash", "dot", "dashdot", "longdash", "longdashdot"]


# ----------------------------------------------------------------------
# values at frequencies
# ----------------------------------------------------------------------


def extract_table(
    analysis: Analysis,
    keys: Sequence[Key],
    models: Sequence[str],
    freqs_hz: Sequence[float],
    include_nyquist: bool = True,
):
    """Rows of dB values, linearly interpolated, one row per (item, model)."""
    proj = analysis.project
    header = ["model", "scenario", "metric", "observed_at", "victim", "victim_group",
              "aggressor", "aggressor_at", "type", "label"]
    if include_nyquist:
        header += ["nyquist_GHz", "dB@nyquist"]
    header += [f"dB@{f / 1e9:g}GHz" for f in freqs_hz]
    rows = []
    for key in keys:
        for m in models:
            if analysis.item(m, key) is None:
                continue
            row = [
                m, key.scen or "(basic)", key.kind, key.obs, key.victim,
                proj.group_of(key.victim), key.aggr, key.src, analysis.xtype(key),
                analysis.label(key),
            ]
            if include_nyquist:
                nyq = analysis.nyquist_hz(key)
                row += [round(nyq / 1e9, 6), _r(analysis.value_at(m, key, nyq))]
            row += [_r(analysis.value_at(m, key, f)) for f in freqs_hz]
            rows.append(row)
    return header, rows


def write_csv(path: str, header: List[str], rows: List[list]) -> str:
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:  # BOM: Excel
        w = csv.writer(fh)
        w.writerow(header)
        for row in rows:
            w.writerow(["" if (isinstance(v, float) and math.isnan(v)) else v for v in row])
    return path


def _r(v: float) -> float:
    return v if math.isnan(v) else round(v, 3)


# ----------------------------------------------------------------------
# HTML
# ----------------------------------------------------------------------


def build_report(
    analysis: Analysis,
    keys: Sequence[Key],
    models: Sequence[str],
    split_by: Sequence[str] = ("metric",),
    title: str = "Channel S-parameter report",
) -> dict:
    """The data the HTML page draws: panels of traces."""
    split_by = [d for d in SPLIT_DIMS if d in split_by]
    model_idx = {m: i for i, m in enumerate(models)}
    colour: Dict[Key, str] = {}
    panels: Dict[tuple, dict] = {}
    metrics: List[str] = []
    for key in keys:
        for m in models:
            if analysis.item(m, key) is None:
                continue
            dims = {
                "metric": metric_name(key),
                "model": m,
                "victim": key.victim,
                "loc": key.obs,
            }
            pkey = tuple(dims[d] for d in split_by)
            panel = panels.get(pkey)
            if panel is None:
                title_ = " · ".join(pkey) if pkey else "All selected curves"
                panel = panels[pkey] = {"title": title_, "nyq": set(), "traces": []}
            if key not in colour:
                colour[key] = PALETTE[len(colour) % len(PALETTE)]
            lab = analysis.label(key)
            met = metric_name(key)
            if met not in metrics:
                metrics.append(met)
            y = np.round(analysis.curve(m, key), 3)
            panel["nyq"].add(round(analysis.nyquist_hz(key) / 1e9, 6))
            panel["traces"].append({
                "m": model_idx[m],
                "name": lab + (f"  [{m}]" if len(models) > 1 else ""),
                "label": lab,
                "metric": met,
                "c": colour[key],
                "d": DASHES[model_idx[m] % len(DASHES)],
                "y": y.tolist(),
            })
    out_panels = []
    for p in panels.values():
        p["nyq"] = sorted(p["nyq"])
        out_panels.append(p)
    proj = analysis.project
    return {
        "title": title,
        "generated": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
        "split_by": [SPLIT_LABELS[d] for d in split_by],
        "models": [
            {
                "alias": m,
                "path": analysis.models[m].network.path,
                "dash": DASHES[model_idx[m] % len(DASHES)],
                "freq": np.round(analysis.models[m].freq / 1e9, 9).tolist(),
            }
            for m in models
        ],
        "metrics": metrics,
        "panels": out_panels,
        "speed": {
            "default_gbps": proj.default_gbps,
            "groups": dict(proj.group_gbps),
        },
    }


def render_html(data: dict) -> str:
    with open(os.path.join(WEB, "report_template.html"), encoding="utf-8") as fh:
        page = fh.read()
    with open(os.path.join(WEB, "plotly.min.js"), encoding="utf-8") as fh:
        plotly = fh.read().replace("</script", "<\\/script")
    blob = json.dumps(data, separators=(",", ":"), allow_nan=False).replace("</", "<\\/")
    page = page.replace("__TITLE__", _html_escape(data["title"]))
    page = page.replace("/*__DATA__*/", blob)
    head, tail = page.split("/*__PLOTLY__*/")
    return head + plotly + tail


def write_html(path: str, analysis: Analysis, keys: Sequence[Key], models: Sequence[str],
               split_by: Sequence[str] = ("metric",), title: Optional[str] = None) -> str:
    data = build_report(analysis, keys, models, split_by,
                        title or os.path.splitext(os.path.basename(path))[0])
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(render_html(data))
    return path


def _html_escape(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
