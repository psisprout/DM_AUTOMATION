"""The project: everything the user sets up, saved and loaded as JSON.

Nothing in here touches S-parameters.  It answers questions like "which net
and location is port 7 of revA", "which group is DQ12 in", "how is a POWER
port terminated in the WRITE scenario" and "what is the Nyquist frequency of
a CA victim".  :mod:`chanxt.analysis` turns those answers into numbers.
"""

from __future__ import annotations

import copy
import json
import os
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .units import name_matcher

FORMAT_VERSION = 1

#: termination types; "signal" keeps the port, "z0" leaves it at its
#: reference impedance (i.e. drops it), the rest load it
TERM_TYPES = ("signal", "z0", "short", "open", "r", "l", "c", "rc_series", "rc_parallel")
TERM_LABELS = {
    "signal": "Signal (analyse)",
    "z0": "Z0 (reference load)",
    "short": "Short to GND",
    "open": "Open",
    "r": "R",
    "l": "L",
    "c": "C",
    "rc_series": "R + C in series",
    "rc_parallel": "R || C",
}
#: which value fields each type uses
TERM_FIELDS = {
    "signal": (), "z0": (), "short": (), "open": (),
    "r": ("r",), "l": ("l",), "c": ("c",),
    "rc_series": ("r", "c"), "rc_parallel": ("r", "c"),
}

NAME_RULES = {
    "NET_LOC": r"^(?P<net>.+)[_.:@](?P<loc>[^_.:@]+)$",
    "LOC_NET": r"^(?P<loc>[^_.:@]+)[_.:@](?P<net>.+)$",
    "NET": r"^(?P<net>.+)$",
}
NAME_RULE_HELP = {
    "NET_LOC": "last token is the location (DQ0_SoC, WCK0_t_DRAM)",
    "LOC_NET": "first token is the location (U1_DQ0)",
    "NET": "name is the net only; set locations by position",
}

#: (regex, group) - first match wins, case-insensitive, searched in the net name
DEFAULT_GROUP_RULES: List[Tuple[str, str]] = [
    (r"(?<![A-Z0-9])(VDD|VSS|VCC|GND|PWR|VREF|VIN)", "POWER"),
    (r"(?<![A-Z0-9])(NC|UNUSED|DUMMY)(?![A-Z])", "UNUSED"),
    (r"(?<![A-Z])DQ\d+", "DQ"),
    (r"(?<![A-Z])DMI?\d*(?![A-Z])", "DM"),
    (r"(?<![A-Z])DQS", "DQS"),
    (r"RDQS", "RDQS"),
    (r"WCK", "WCK"),
    (r"(?<![A-Z])CA\d+", "CA"),
    (r"(?<![A-Z])CS\d*(?![A-Z])", "CS"),
    (r"(?<![A-Z])CK(?![A-Z])", "CK"),
]
#: groups listed last wherever groups are shown (they are rarely analysed)
TAIL_GROUPS = ("POWER", "UNUSED")
OTHER_GROUP = "OTHER"

#: (regex, tag) - LPDDR byte lanes; harmless for anything else
DEFAULT_TAG_RULES: List[Tuple[str, str]] = [
    (r"(?<![A-Z0-9])(DQ[0-7]|DMI?0|RDQS0|WCK0)(?![0-9])", "Byte0"),
    (r"(?<![A-Z0-9])(DQ([89]|1[0-5])|DMI?1|RDQS1|WCK1)(?![0-9])", "Byte1"),
]

#: groups that are terminated rather than analysed in a new project
DEFAULT_TERMINATIONS = [
    {"scope": "group", "name": "POWER", "loc": "*", "type": "short"},
    {"scope": "group", "name": "UNUSED", "loc": "*", "type": "open"},
]


# --------------------------------------------------------------------------
# records
# --------------------------------------------------------------------------


@dataclass
class PortMap:
    net: str = ""
    loc: str = ""


@dataclass
class ModelSpec:
    path: str
    alias: str
    ports: list[PortMap] = field(default_factory=list)
    name_rule: str = "auto"  # key of NAME_RULES, "auto" or "custom"
    name_regex: str = ""  # used when name_rule == "custom"


@dataclass
class TermRule:
    scope: str = "group"  # "group" | "net" (net names may be globs)
    name: str = ""
    loc: str = "*"
    type: str = "signal"
    r: float = 0.0
    l: float = 0.0
    c: float = 0.0

    def describe(self) -> str:
        from .units import fmt_si

        vals = [f"{k.upper()}={fmt_si(getattr(self, k))}" for k in TERM_FIELDS[self.type]]
        return TERM_LABELS[self.type] + (" " + " ".join(vals) if vals else "")

    def signature(self) -> tuple:
        return (self.type,) + tuple(getattr(self, k) for k in TERM_FIELDS[self.type])


@dataclass
class DirRule:
    scope: str = "group"  # "group" | "net"
    name: str = ""
    inp: str = ""  # location of the driver
    out: list[str] = field(default_factory=list)  # receiver locations
    quiet: bool = False


@dataclass
class Scenario:
    name: str
    directions: list[DirRule] = field(default_factory=list)
    terminations: list[TermRule] = field(default_factory=list)


@dataclass
class Project:
    models: list[ModelSpec] = field(default_factory=list)
    loc_alias: Dict[str, str] = field(default_factory=dict)
    locations: list[str] = field(default_factory=list)
    group_rules: List[Tuple[str, str]] = field(default_factory=lambda: list(DEFAULT_GROUP_RULES))
    tag_rules: List[Tuple[str, str]] = field(default_factory=lambda: list(DEFAULT_TAG_RULES))
    nets: Dict[str, Dict[str, str]] = field(default_factory=dict)  # manual group/tag
    terminations: list[TermRule] = field(
        default_factory=lambda: [TermRule(**t) for t in DEFAULT_TERMINATIONS]
    )
    scenarios: list[Scenario] = field(default_factory=list)
    default_gbps: float = 8.533
    group_gbps: Dict[str, float] = field(default_factory=dict)
    aggr_tag_mode: str = "all"  # "all" | "same_tag"
    aggr_groups: list[str] = field(default_factory=list)  # empty = every group
    presets: Dict[str, dict] = field(default_factory=dict)
    plot: Dict[str, object] = field(default_factory=dict)
    path: str = ""  # where it was loaded from / saved to (not serialized)

    def __post_init__(self) -> None:
        self._rx_cache: Dict[str, object] = {}

    # -- nets ----------------------------------------------------------

    def _rx(self, pattern: str):
        rx = self._rx_cache.get(pattern)
        if rx is None:
            rx = re.compile(pattern, re.I)
            self._rx_cache[pattern] = rx
        return rx

    def rule_group(self, net: str) -> str:
        for pattern, group in self.group_rules:
            try:
                if self._rx(pattern).search(net):
                    return group
            except re.error:
                continue
        return OTHER_GROUP

    def rule_tag(self, net: str) -> str:
        for pattern, tag in self.tag_rules:
            try:
                if self._rx(pattern).search(net):
                    return tag
            except re.error:
                continue
        return ""

    def group_of(self, net: str) -> str:
        manual = self.nets.get(net, {}).get("group")
        return manual if manual else self.rule_group(net)

    def tag_of(self, net: str) -> str:
        entry = self.nets.get(net, {})
        if "tag" in entry:
            return entry["tag"]
        return self.rule_tag(net)

    def all_nets(self) -> list[str]:
        from .units import natural_key

        seen = {p.net for m in self.models for p in m.ports if p.net}
        return sorted(seen, key=natural_key)

    def all_groups(self) -> list[str]:
        """Groups in rule order, then any others in use."""
        used = {self.group_of(n) for n in self.all_nets()}
        order: list[str] = []
        for _, g in self.group_rules:
            if g in used and g not in order:
                order.append(g)
        rest = sorted(g for g in used if g not in order and g != OTHER_GROUP)
        tail = [OTHER_GROUP] if OTHER_GROUP in used else []
        ordered = order + rest + tail
        return [g for g in ordered if g not in TAIL_GROUPS] + [g for g in ordered if g in TAIL_GROUPS]

    def all_tags(self) -> list[str]:
        return sorted({t for t in (self.tag_of(n) for n in self.all_nets()) if t})

    def sync_locations(self) -> None:
        """Keep ``locations`` ordered as the user set it, adding new ones."""
        used = []
        for m in self.models:
            for p in m.ports:
                if p.loc and p.loc not in used:
                    used.append(p.loc)
        kept = [x for x in self.locations if x in used]
        self.locations = kept + [x for x in used if x not in kept]

    # -- speed ---------------------------------------------------------

    def gbps_of(self, group: str) -> float:
        return float(self.group_gbps.get(group, self.default_gbps))

    def nyquist_hz(self, group: str) -> float:
        return self.gbps_of(group) * 1e9 / 2.0

    # -- terminations / directions ------------------------------------

    def resolve_term(self, net: str, loc: str, scenario: Optional[Scenario] = None) -> TermRule:
        """Scenario net > scenario group > global net > global group > default."""
        if not net:
            return TermRule(scope="net", name="", type="z0")
        group = self.group_of(net)
        layers = ([scenario.terminations] if scenario else []) + [self.terminations]
        for rules in layers:
            for scope, key in (("net", net), ("group", group)):
                hit = _best_rule(rules, scope, key, loc)
                if hit is not None:
                    return hit
        return TermRule(scope="net", name=net, type="signal")

    def resolve_direction(self, scenario: Scenario, net: str) -> Optional[DirRule]:
        group = self.group_of(net)
        for scope, key in (("net", net), ("group", group)):
            hit = None
            for rule in scenario.directions:
                if rule.scope == scope and _name_hit(rule.name, key, scope):
                    hit = rule  # later rows win
            if hit is not None:
                return hit
        return None

    def scenario(self, name: str) -> Optional[Scenario]:
        for s in self.scenarios:
            if s.name == name:
                return s
        return None

    # -- persistence ---------------------------------------------------

    def to_dict(self) -> dict:
        base = os.path.dirname(os.path.abspath(self.path)) if self.path else ""
        return {
            "chanxt_project": FORMAT_VERSION,
            "models": [
                {
                    "path": _rel(m.path, base),
                    "alias": m.alias,
                    "name_rule": m.name_rule,
                    "name_regex": m.name_regex,
                    "ports": [[p.net, p.loc] for p in m.ports],
                }
                for m in self.models
            ],
            "loc_alias": dict(self.loc_alias),
            "locations": list(self.locations),
            "group_rules": [list(r) for r in self.group_rules],
            "tag_rules": [list(r) for r in self.tag_rules],
            "nets": copy.deepcopy(self.nets),
            "terminations": [_term_dict(t) for t in self.terminations],
            "scenarios": [
                {
                    "name": s.name,
                    "directions": [
                        {"scope": d.scope, "name": d.name, "in": d.inp,
                         "out": list(d.out), "quiet": d.quiet}
                        for d in s.directions
                    ],
                    "terminations": [_term_dict(t) for t in s.terminations],
                }
                for s in self.scenarios
            ],
            "speed": {"default_gbps": self.default_gbps, "groups": dict(self.group_gbps)},
            "aggressor_scope": {"tag_mode": self.aggr_tag_mode, "groups": list(self.aggr_groups)},
            "presets": copy.deepcopy(self.presets),
            "plot": copy.deepcopy(self.plot),
        }

    @classmethod
    def from_dict(cls, d: dict, path: str = "") -> "Project":
        base = os.path.dirname(os.path.abspath(path)) if path else ""
        p = cls()
        p.path = path
        p.models = [
            ModelSpec(
                path=_abs(m["path"], base),
                alias=m.get("alias") or os.path.basename(m["path"]),
                name_rule=m.get("name_rule", "auto"),
                name_regex=m.get("name_regex", ""),
                ports=[PortMap(net=x[0], loc=x[1]) for x in m.get("ports", [])],
            )
            for m in d.get("models", [])
        ]
        p.loc_alias = dict(d.get("loc_alias", {}))
        p.locations = list(d.get("locations", []))
        if "group_rules" in d:
            p.group_rules = [tuple(r) for r in d["group_rules"]]
        if "tag_rules" in d:
            p.tag_rules = [tuple(r) for r in d["tag_rules"]]
        p.nets = {k: dict(v) for k, v in d.get("nets", {}).items()}
        if "terminations" in d:
            p.terminations = [_term_from(t) for t in d["terminations"]]
        p.scenarios = [
            Scenario(
                name=s["name"],
                directions=[
                    DirRule(scope=x.get("scope", "group"), name=x.get("name", ""),
                            inp=x.get("in", ""), out=list(x.get("out", [])),
                            quiet=bool(x.get("quiet", False)))
                    for x in s.get("directions", [])
                ],
                terminations=[_term_from(t) for t in s.get("terminations", [])],
            )
            for s in d.get("scenarios", [])
        ]
        speed = d.get("speed", {})
        p.default_gbps = float(speed.get("default_gbps", p.default_gbps))
        p.group_gbps = {k: float(v) for k, v in speed.get("groups", {}).items()}
        scope = d.get("aggressor_scope", {})
        p.aggr_tag_mode = scope.get("tag_mode", "all")
        p.aggr_groups = list(scope.get("groups", []))
        p.presets = copy.deepcopy(d.get("presets", {}))
        p.plot = copy.deepcopy(d.get("plot", {}))
        return p

    def save(self, path: str) -> None:
        self.path = path
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(self.to_dict(), fh, indent=1)
            fh.write("\n")

    @classmethod
    def load(cls, path: str) -> "Project":
        with open(path, encoding="utf-8") as fh:
            d = json.load(fh)
        if "chanxt_project" not in d:
            raise ValueError(f"{path} is not a chanxt project file")
        return cls.from_dict(d, path)


def _name_hit(rule_name: str, key: str, scope: str) -> bool:
    if scope == "group":
        return rule_name.lower() == key.lower()
    return name_matcher(rule_name)(key) if rule_name else False


def _best_rule(rules: list[TermRule], scope: str, key: str, loc: str) -> Optional[TermRule]:
    """A location-specific rule beats ``*``; among equals the later row wins."""
    best, best_rank = None, -1
    for rule in rules:
        if rule.scope != scope or not _name_hit(rule.name, key, scope):
            continue
        if rule.loc not in ("*", "", loc):
            continue
        rank = 1 if rule.loc == loc else 0
        if rank >= best_rank:
            best, best_rank = rule, rank
    return best


def _term_dict(t: TermRule) -> dict:
    d = {"scope": t.scope, "name": t.name, "loc": t.loc, "type": t.type}
    for k in TERM_FIELDS[t.type]:
        d[k] = getattr(t, k)
    return d


def _term_from(d: dict) -> TermRule:
    t = TermRule(scope=d.get("scope", "group"), name=d.get("name", ""),
                 loc=d.get("loc", "*") or "*", type=d.get("type", "signal"))
    if t.type not in TERM_TYPES:
        raise ValueError(f"unknown termination type {t.type!r}")
    for k in ("r", "l", "c"):
        if k in d:
            setattr(t, k, float(d[k]))
    return t


def _rel(path: str, base: str) -> str:
    if not base or not path:
        return path
    try:
        rel = os.path.relpath(path, base)
    except ValueError:  # different drive on Windows
        return path
    return path if rel.startswith("..") else rel


def _abs(path: str, base: str) -> str:
    if os.path.isabs(path) or not base:
        return path
    return os.path.normpath(os.path.join(base, path))


# --------------------------------------------------------------------------
# port names -> (net, location)
# --------------------------------------------------------------------------


def parse_names(names: list[str], regex: str, loc_alias: Dict[str, str]) -> list[PortMap]:
    """Split each port name into net and location with ``regex``.

    The regex must define ``net`` and may define ``loc``.  A name it does not
    match keeps the whole name as the net and gets no location.
    """
    rx = re.compile(regex)
    alias = {k.lower(): v for k, v in loc_alias.items()}
    out = []
    for nm in names:
        m = rx.match(nm.strip())
        if not m:
            out.append(PortMap(net=nm.strip(), loc=""))
            continue
        gd = m.groupdict()
        net = (gd.get("net") or nm).strip()
        loc = (gd.get("loc") or "").strip()
        loc = alias.get(loc.lower(), loc)
        out.append(PortMap(net=net, loc=loc))
    return out


def auto_name_rule(names: list[str]) -> str:
    """Guess which of NET_LOC / LOC_NET / NET describes these port names.

    A location token repeats across many ports (a handful of distinct values,
    each owned by several ports) while net tokens do not.  Pick the split with
    the fewest distinct locations, provided it pairs ports up into nets.
    """
    best, best_score = "NET", None
    n = len(names)
    for rule in ("NET_LOC", "LOC_NET"):
        maps = parse_names(names, NAME_RULES[rule], {})
        locs = [m.loc for m in maps if m.loc]
        if len(locs) < n:  # some names did not split
            continue
        distinct = set(locs)
        if len(distinct) < 2 or len(distinct) > max(2, n // 3):
            continue
        nets: Dict[str, int] = {}
        for m in maps:
            nets[m.net] = nets.get(m.net, 0) + 1
        paired = sum(1 for c in nets.values() if c >= 2) / max(1, len(nets))
        if paired < 0.5:
            continue
        score = (len(distinct), -paired)
        if best_score is None or score < best_score:
            best, best_score = rule, score
    return best


def regex_for(model: ModelSpec) -> str:
    if model.name_rule == "custom":
        return model.name_regex or NAME_RULES["NET"]
    return NAME_RULES.get(model.name_rule, NAME_RULES["NET"])


def positional_locations(n: int, layout: str, loc_a: str, loc_b: str) -> list[str]:
    """``halves``: ports 1..n/2 are A, the rest B.  ``alternate``: odd A, even B."""
    if layout == "halves":
        return [loc_a if i < n // 2 else loc_b for i in range(n)]
    if layout == "alternate":
        return [loc_a if i % 2 == 0 else loc_b for i in range(n)]
    raise ValueError(f"unknown layout {layout!r}")


def positional_nets(n: int, layout: str, net_names: list[str]) -> list[str]:
    """Spread a pasted net list over the ports.

    ``n`` names are used as they are.  ``n/2`` names are given to both ends of
    each pair, according to ``layout``.
    """
    names = [x.strip() for x in net_names if x.strip()]
    if len(names) == n:
        return names
    if 2 * len(names) == n:
        if layout == "halves":
            return names + names
        if layout == "alternate":
            return [names[i // 2] for i in range(n)]
    raise ValueError(f"{len(names)} names for {n} ports: give {n} or {n // 2}")


# --------------------------------------------------------------------------
# presets
# --------------------------------------------------------------------------

LP5X_CTRL_DRIVEN_WRITE = ("DQ", "DM", "CA", "CS", "CK", "WCK")
LP5X_MEM_DRIVEN_READ = ("DQ", "DM", "RDQS")


def apply_lp5x_preset(project: Project, ctrl: str, mem: str, gbps: float = 10.667) -> None:
    """WRITE/READ scenarios, LPDDR5X speeds and the usual terminations.

    WRITE: the controller drives DQ, DM, CA, CS, CK and WCK; the DRAM drives RDQS.
    READ:  the DRAM drives DQ, DM and RDQS; the controller drives CA, CS, CK, WCK.

    Speeds are equivalent bit rates: CK runs at 1/4 of WCK, CA is DDR on CK
    (DQ/4), CS is SDR on CK (DQ/8).  A clock's Nyquist is its own frequency.
    """
    def rules(ctrl_groups):
        out = []
        for g in ("DQ", "DM", "RDQS", "CA", "CS", "CK", "WCK"):
            if g in ctrl_groups:
                out.append(DirRule("group", g, ctrl, [mem]))
            else:
                out.append(DirRule("group", g, mem, [ctrl]))
        return out

    read_ctrl = ("CA", "CS", "CK", "WCK")
    scen = {
        "WRITE": Scenario("WRITE", rules(LP5X_CTRL_DRIVEN_WRITE)),
        "READ": Scenario("READ", rules(read_ctrl)),
    }
    kept = [s for s in project.scenarios if s.name not in scen]
    project.scenarios = kept + [scen["WRITE"], scen["READ"]]
    project.default_gbps = gbps
    project.group_gbps.update({"CA": round(gbps / 4, 4), "CK": round(gbps / 4, 4),
                               "CS": round(gbps / 8, 4)})
    have = {(t.scope, t.name.lower()) for t in project.terminations}
    for t in DEFAULT_TERMINATIONS:
        if (t["scope"], t["name"].lower()) not in have:
            project.terminations.append(TermRule(**t))
    for loc in (ctrl, mem):
        if loc not in project.locations:
            project.locations.append(loc)
