"""Picking curves out of thousands: facet filter, worst-K, matrix, breakdown.

A selection is a :class:`FilterSpec`.  It is plain data, so it is what a
preset saves.  The facets are combined (model x scenario x metric x location x
victim), the per-aggressor kinds are thinned (all / worst K / threshold /
none), and then the hand-picked additions and removals from the preview table
and the matrix are applied.  Keys are model-independent: picking an item picks
it in every selected model, which is what makes overlays line up.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional, Tuple

from .analysis import PER_AGGRESSOR, Analysis, Key, worse_is_lower
from .units import name_matcher, natural_key

NO_TAG = "(no tag)"
A_MODES = ("topk", "threshold", "all", "none")
A_MODE_LABELS = {
    "topk": "Worst K per victim",
    "threshold": "At or above threshold",
    "all": "All aggressors",
    "none": "None (pick in the matrix)",
}


@dataclass
class FilterSpec:
    models: list[str] = field(default_factory=list)
    scens: list[str] = field(default_factory=list)  # "" = basic
    kinds: list[str] = field(default_factory=list)
    locs: list[str] = field(default_factory=list)
    v_groups: list[str] = field(default_factory=list)
    v_tags: list[str] = field(default_factory=list)  # NO_TAG for untagged nets
    v_pattern: str = ""
    a_mode: str = "topk"
    a_k: int = 3
    a_threshold: float = -40.0
    a_groups: list[str] = field(default_factory=list)
    a_pattern: str = ""
    add: list[list[str]] = field(default_factory=list)
    remove: list[list[str]] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "FilterSpec":
        known = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        return cls(**known)

    def add_key(self, key: Key) -> None:
        k = list(key)
        if k in self.remove:
            self.remove.remove(k)
        if k not in self.add:
            self.add.append(k)

    def remove_key(self, key: Key) -> None:
        k = list(key)
        if k in self.add:
            self.add.remove(k)
        if k not in self.remove:
            self.remove.append(k)


def default_spec(analysis: Analysis) -> FilterSpec:
    """Everything in view except the per-aggressor curves."""
    proj = analysis.project
    return FilterSpec(
        models=list(analysis.models),
        scens=[""] + [s.name for s in proj.scenarios],
        kinds=["IL", "RL", "PSNEXT", "PSFEXT", "PSXT"],
        locs=list(proj.locations),
        v_groups=analysis.victim_groups(),
        v_tags=list(proj.all_tags()) + [NO_TAG],
        a_groups=analysis.victim_groups(),
    )


def net_sort_key(analysis: Analysis):
    proj = analysis.project
    groups = proj.all_groups()

    def key(net: str):
        g = proj.group_of(net)
        return (groups.index(g) if g in groups else len(groups), natural_key(net))

    return key


_KIND_ORDER = {k: i for i, k in enumerate(
    ("IL", "RL", "PSNEXT", "PSFEXT", "PSXT", "PSXT_TYPE", "PSXT_GRP", "NEXT", "FEXT", "XT"))}


def sort_keys(analysis: Analysis, keys) -> List[Key]:
    nk = net_sort_key(analysis)
    scen_order = {"": -1}
    scen_order.update({s.name: i for i, s in enumerate(analysis.project.scenarios)})
    locs = analysis.project.locations

    def k(key: Key):
        return (
            scen_order.get(key.scen, 99),
            _KIND_ORDER.get(key.kind, 99),
            nk(key.victim),
            locs.index(key.obs) if key.obs in locs else 99,
            nk(key.aggr) if key.aggr else (-1, []),
            key.src,
        )

    return sorted(set(keys), key=k)


def worst_value(analysis: Analysis, key: Key, models: List[str]) -> float:
    """The worst value at Nyquist over ``models`` (NaN if none has the item)."""
    vals = [analysis.value_at_nyquist(m, key) for m in models]
    vals = [v for v in vals if not math.isnan(v)]
    if not vals:
        return float("nan")
    return min(vals) if worse_is_lower(key.kind) else max(vals)


def run_filter(analysis: Analysis, spec: FilterSpec) -> List[Key]:
    proj = analysis.project
    models = [m for m in spec.models if m in analysis.models]
    scens, kinds, locs = set(spec.scens), set(spec.kinds), set(spec.locs)
    v_groups, v_tags = set(spec.v_groups), set(spec.v_tags)
    a_groups = set(spec.a_groups)
    v_match = name_matcher(spec.v_pattern)
    a_match = name_matcher(spec.a_pattern)

    plain: set = set()
    per_aggr: Dict[tuple, set] = {}
    for alias in models:
        for key in analysis.keys(alias):
            if key.scen not in scens or key.kind not in kinds:
                continue
            if key.kind != "IL" and key.obs not in locs:
                continue
            if proj.group_of(key.victim) not in v_groups:
                continue
            if (proj.tag_of(key.victim) or NO_TAG) not in v_tags:
                continue
            if not v_match(key.victim):
                continue
            if key.kind == "PSXT_GRP" and key.aggr not in a_groups:
                continue
            if key.kind in PER_AGGRESSOR:
                if proj.group_of(key.aggr) not in a_groups or not a_match(key.aggr):
                    continue
                per_aggr.setdefault((key.scen, key.kind, key.obs, key.victim), set()).add(key)
            else:
                plain.add(key)

    chosen = set(plain)
    if spec.a_mode == "all":
        for keys in per_aggr.values():
            chosen.update(keys)
    elif spec.a_mode in ("topk", "threshold"):
        for keys in per_aggr.values():
            scored = [(worst_value(analysis, k, models), k) for k in keys]
            scored = [(v, k) for v, k in scored if not math.isnan(v)]
            scored.sort(key=lambda vk: vk[0], reverse=True)
            if spec.a_mode == "topk":
                chosen.update(k for _, k in scored[: max(0, int(spec.a_k))])
            else:
                chosen.update(k for v, k in scored if v >= spec.a_threshold)

    for raw in spec.add:
        key = Key(*raw)
        if any(analysis.item(m, key) is not None for m in models):
            chosen.add(key)
    for raw in spec.remove:
        chosen.discard(Key(*raw))
    return sort_keys(analysis, chosen)


# ----------------------------------------------------------------------
# preview
# ----------------------------------------------------------------------


@dataclass
class PreviewRow:
    key: Key
    label: str
    nyquist_ghz: float
    values: Dict[str, float]  # model -> dB at Nyquist
    worst: float


def preview(analysis: Analysis, keys: List[Key], models: List[str]) -> List[PreviewRow]:
    rows = []
    for key in keys:
        vals = {m: analysis.value_at_nyquist(m, key) for m in models}
        good = [v for v in vals.values() if not math.isnan(v)]
        worst = (min(good) if worse_is_lower(key.kind) else max(good)) if good else float("nan")
        rows.append(PreviewRow(key, analysis.label(key), analysis.nyquist_hz(key) / 1e9, vals, worst))
    return rows


# ----------------------------------------------------------------------
# matrix
# ----------------------------------------------------------------------


@dataclass
class Matrix:
    victims: List[str]
    aggressors: List[str]
    values: List[List[float]]  # [victim][aggressor] dB at the victim's Nyquist
    keys: List[List[Optional[Key]]]


def matrix_sources(analysis: Analysis, alias: str, scen: str, kind: str, obs: str) -> List[str]:
    """Source locations that occur for FEXT observed at ``obs``."""
    md = analysis.models.get(alias)
    if md is None:
        return []
    srcs = {k.src for (s, kd, o, _), keys in md.slots.items()
            if s == scen and kd == kind and o == obs for k in keys}
    locs = analysis.project.locations
    return sorted(srcs, key=lambda x: locs.index(x) if x in locs else 99)


def matrix(analysis: Analysis, alias: str, scen: str, kind: str, obs: str,
           src: str = "") -> Matrix:
    """Victim x aggressor grid for NEXT / FEXT (basic) or XT (scenario)."""
    md = analysis.models.get(alias)
    if md is None:
        return Matrix([], [], [], [])
    cells: Dict[Tuple[str, str], Key] = {}
    for (s, kd, o, v), keys in md.slots.items():
        if s != scen or kd != kind or o != obs:
            continue
        for key in keys:
            if kind == "FEXT" and src and key.src != src:
                continue
            cells[(v, key.aggr)] = key
    order = net_sort_key(analysis)
    victims = sorted({v for v, _ in cells}, key=order)
    aggressors = sorted({a for _, a in cells}, key=order)
    values, keys = [], []
    for v in victims:
        vrow, krow = [], []
        for a in aggressors:
            key = cells.get((v, a))
            krow.append(key)
            vrow.append(analysis.value_at_nyquist(alias, key) if key else float("nan"))
        values.append(vrow)
        keys.append(krow)
    return Matrix(victims, aggressors, values, keys)


# ----------------------------------------------------------------------
# breakdown: who contributes to a victim's scenario PSXT
# ----------------------------------------------------------------------


@dataclass
class BreakdownRow:
    rank: int
    name: str  # aggressor net, or group when grouped
    group: str
    xtype: str
    db: float
    share: float  # 0..1 of the summed power
    cumulative: float
    keys: List[Key]
    members: int = 1


def breakdown(analysis: Analysis, alias: str, scen: str, victim: str, obs: str,
              by_group: bool = False, freq_hz: Optional[float] = None):
    """Rank every aggressor's contribution at the victim's Nyquist (or ``freq_hz``).

    Returns ``(rows, total_db, freq_hz)``.  Shares are of the summed power, so
    they add to 100 %.
    """
    keys = analysis.slot(alias, scen, "XT", obs, victim)
    f = freq_hz if freq_hz else (analysis.nyquist_hz(keys[0]) if keys else float("nan"))
    entries = []
    for key in keys:
        db = analysis.value_at(alias, key, f)
        if math.isnan(db):
            continue
        it = analysis.item(alias, key)
        entries.append((key, db, 10 ** (db / 10.0), it.agroup, it.xtype))
    total = sum(p for _, _, p, _, _ in entries)
    if by_group:
        agg: Dict[str, list] = {}
        for key, _, p, g, t in entries:
            slot = agg.setdefault(g, [0.0, set(), []])
            slot[0] += p
            slot[1].add(t)
            slot[2].append(key)
        entries = [
            (keys_, 10 * math.log10(p) if p > 0 else -300.0, p, g,
             "+".join(x for x in ("NEXT", "FEXT") if x in types))
            for g, (p, types, keys_) in agg.items()
        ]
        entries = [(k, db, p, g, t) for k, db, p, g, t in entries]
    entries.sort(key=lambda e: e[2], reverse=True)
    rows, cum = [], 0.0
    for i, (key, db, p, g, t) in enumerate(entries, 1):
        share = p / total if total > 0 else 0.0
        cum += share
        if by_group:
            rows.append(BreakdownRow(i, g, g, t, db, share, cum, list(key), len(key)))
        else:
            rows.append(BreakdownRow(i, key.aggr, g, t, db, share, cum, [key]))
    total_db = 10 * math.log10(total) if total > 0 else float("nan")
    return rows, total_db, f
