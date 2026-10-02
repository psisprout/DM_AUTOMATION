"""Turn a project and its networks into named curves.

Every curve the tool can show is an *item*, identified by a model-independent
:class:`Key`, so the same item in two models (revA, revB) is overlaid and
compared by key.

Basic items use the project-wide terminations and look at every signal port:

* ``IL``      S(net@obs, net@src)                         per pair of a net's ports
* ``RL``      S(net@obs, net@obs)
* ``NEXT``    S(v@obs, a@obs)                              aggressor port at the same location
* ``FEXT``    S(v@obs, a@src), src != obs
* ``PSNEXT``  power sum of NEXT over the aggressors in scope
* ``PSFEXT``  power sum of FEXT from aggressors driven at ``src``

Scenario items use the scenario's terminations and its IN/OUT roles.  For a
victim observed at one of its OUT ports, aggressor ``a`` contributes
``S(v_OUT, a_IN)``: NEXT when ``a_IN`` sits at the victim's OUT location,
FEXT otherwise.

* ``IL``         IN -> each OUT
* ``RL``         at IN and at each OUT
* ``XT``         one aggressor's contribution
* ``PSXT``       power sum of every contribution
* ``PSXT_TYPE``  power sum of the NEXT (or FEXT) contributions only
* ``PSXT_GRP``   power sum of one aggressor group's contributions
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, NamedTuple, Optional, Tuple

import numpy as np

from .project import Project, Scenario, TermRule
from .terminate import load_gamma, reduce_power
from .touchstone import Network
from .units import natural_key

BASIC_KINDS = ("IL", "RL", "NEXT", "FEXT", "PSNEXT", "PSFEXT")
SCENARIO_KINDS = ("IL", "RL", "PSXT", "PSXT_TYPE", "PSXT_GRP", "XT")
ALL_KINDS = ("IL", "RL", "NEXT", "FEXT", "PSNEXT", "PSFEXT", "PSXT", "PSXT_TYPE", "PSXT_GRP", "XT")
KIND_LABELS = {
    "IL": "IL",
    "RL": "RL",
    "NEXT": "NEXT (each aggressor)",
    "FEXT": "FEXT (each aggressor)",
    "PSNEXT": "PSNEXT",
    "PSFEXT": "PSFEXT",
    "PSXT": "Scenario PSXT (total)",
    "PSXT_TYPE": "Scenario PSXT, NEXT / FEXT part",
    "PSXT_GRP": "Scenario PSXT per aggressor group",
    "XT": "Scenario XT (each aggressor)",
}
#: kinds with one curve per aggressor - the ones that need aggressor filtering
PER_AGGRESSOR = ("NEXT", "FEXT", "XT")
FLOOR_DB = -300.0


class Key(NamedTuple):
    scen: str  # "" for the basic (scenario-free) items
    kind: str
    obs: str  # where the victim is observed (IL: receiving end)
    victim: str
    aggr: str = ""  # aggressor net, aggressor group (PSXT_GRP) or NEXT/FEXT (PSXT_TYPE)
    src: str = ""  # where the aggressor is driven (IL: sending end)


@dataclass
class Item:
    ds: int  # dataset index in the model
    row: int
    cols: Tuple[int, ...]
    xtype: str = ""  # NEXT / FEXT / NEXT+FEXT for crosstalk items
    agroup: str = ""


@dataclass
class PortInfo:
    index: int
    name: str
    net: str
    loc: str
    group: str
    tag: str


@dataclass
class Dataset:
    """One terminated, reduced network: ``|S|^2`` of its signal ports."""

    power: np.ndarray  # (F, K, K) float32
    pos: Dict[Tuple[str, str], int]  # (net, loc) -> row/column in power
    terms: List[TermRule]  # resolved termination per original port


@dataclass
class ModelData:
    alias: str
    network: Network
    ports: List[PortInfo]
    datasets: List[Dataset] = field(default_factory=list)
    items: Dict[Key, Item] = field(default_factory=dict)
    slots: Dict[tuple, List[Key]] = field(default_factory=dict)  # (scen,kind,obs,victim)
    roles: Dict[str, Dict[str, tuple]] = field(default_factory=dict)  # scen -> net -> (in, outs)

    @property
    def freq(self) -> np.ndarray:
        return self.network.freq


class Analysis:
    """Resolved ports, terminated networks and the item catalogue."""

    def __init__(self, project: Project, networks: Dict[str, Network]) -> None:
        self.project = project
        self.networks = networks
        self.models: Dict[str, ModelData] = {}
        self.messages: List[str] = []
        self._sig_cache: Dict[str, Dict[tuple, int]] = {}
        self.multi_loc = False

    # ------------------------------------------------------------------
    # build
    # ------------------------------------------------------------------

    def build(self, progress=None) -> "Analysis":
        proj = self.project
        proj.sync_locations()
        self.multi_loc = len(proj.locations) > 2
        for spec in proj.models:
            net = self.networks.get(spec.alias)
            if net is None:
                self.messages.append(f"{spec.alias}: not loaded - skipped")
                continue
            if len(spec.ports) != net.nports:
                self.messages.append(
                    f"{spec.alias}: port map has {len(spec.ports)} rows for a "
                    f"{net.nports}-port file - skipped"
                )
                continue
            if progress:
                progress(f"{spec.alias}: resolving ports")
            md = ModelData(alias=spec.alias, network=net, ports=self._ports(spec, net))
            self.models[spec.alias] = md
            self._sig_cache[spec.alias] = {}
            if progress:
                progress(f"{spec.alias}: basic metrics")
            ds = self._dataset(md, None)
            self._basic_items(md, ds)
            for scen in proj.scenarios:
                if progress:
                    progress(f"{spec.alias}: scenario {scen.name}")
                ds = self._dataset(md, scen)
                self._scenario_items(md, ds, scen)
        return self

    def _ports(self, spec, net: Network) -> List[PortInfo]:
        proj = self.project
        out, seen, no_loc = [], {}, []
        for i, pm in enumerate(spec.ports):
            net_name, loc = pm.net.strip(), pm.loc.strip()
            if net_name and not loc:
                no_loc.append(str(i + 1))
                net_name = ""
            if net_name and (net_name, loc) in seen:
                self.messages.append(
                    f"{spec.alias}: port {i + 1} repeats {net_name}@{loc} "
                    f"(already port {seen[(net_name, loc)] + 1}) - left at Z0"
                )
                net_name = ""
            if net_name:
                seen[(net_name, loc)] = i
            group = proj.group_of(net_name) if net_name else ""
            tag = proj.tag_of(net_name) if net_name else ""
            out.append(PortInfo(i, net.port_names[i], net_name, loc if net_name else "", group, tag))
        if no_loc:
            self.messages.append(
                f"{spec.alias}: {len(no_loc)} port(s) have a net but no location and are left "
                f"at Z0 (ports {_short(no_loc)}) - set locations on tab 1"
            )
        return out

    def _dataset(self, md: ModelData, scen: Optional[Scenario]) -> int:
        proj = self.project
        terms = [proj.resolve_term(p.net, p.loc, scen) for p in md.ports]
        sig = tuple(t.signature() for t in terms)
        cache = self._sig_cache[md.alias]
        if sig in cache:
            return cache[sig]
        keep = [p.index for p, t in zip(md.ports, terms) if t.type == "signal" and p.net]
        loaded = [p.index for p, t in zip(md.ports, terms) if t.type not in ("signal", "z0")]
        net = md.network
        gam = np.empty((net.npoints, len(loaded)), dtype=complex)
        for j, idx in enumerate(loaded):
            gam[:, j] = load_gamma(terms[idx], net.freq, float(net.z0[idx]))
        power = reduce_power(net.s, keep, loaded, gam)
        pos = {(md.ports[i].net, md.ports[i].loc): k for k, i in enumerate(keep)}
        md.datasets.append(Dataset(power=power, pos=pos, terms=terms))
        cache[sig] = len(md.datasets) - 1
        return cache[sig]

    # -- helpers -------------------------------------------------------

    def _loc_order(self, loc: str) -> int:
        locs = self.project.locations
        return locs.index(loc) if loc in locs else len(locs)

    def _signal_nets(self, md: ModelData, ds: int) -> Dict[str, Dict[str, int]]:
        nets: Dict[str, Dict[str, int]] = {}
        for (net, loc), k in md.datasets[ds].pos.items():
            nets.setdefault(net, {})[loc] = k
        ordered = {}
        for net in sorted(nets, key=natural_key):
            ordered[net] = dict(sorted(nets[net].items(), key=lambda kv: self._loc_order(kv[0])))
        return ordered

    def in_scope(self, victim: str, aggressor: str) -> bool:
        if victim == aggressor:
            return False
        proj = self.project
        if proj.aggr_groups and proj.group_of(aggressor) not in proj.aggr_groups:
            return False
        if proj.aggr_tag_mode == "same_tag":
            vt = proj.tag_of(victim)
            if vt and proj.tag_of(aggressor) != vt:
                return False
        return True

    def _add(self, md: ModelData, key: Key, item: Item) -> None:
        md.items[key] = item
        md.slots.setdefault((key.scen, key.kind, key.obs, key.victim), []).append(key)

    # -- basic ----------------------------------------------------------

    def _basic_items(self, md: ModelData, ds: int) -> None:
        proj = self.project
        nets = self._signal_nets(md, ds)
        for v, vports in nets.items():
            locs = list(vports)
            for x in locs:
                self._add(md, Key("", "RL", x, v), Item(ds, vports[x], (vports[x],)))
            for i, x in enumerate(locs):
                for y in locs[i + 1 :]:
                    self._add(md, Key("", "IL", y, v, "", x), Item(ds, vports[y], (vports[x],)))
            for x, kx in vports.items():
                next_cols: List[int] = []
                fext: Dict[str, List[int]] = {}
                for a, aports in nets.items():
                    if not self.in_scope(v, a):
                        continue
                    ag = proj.group_of(a)
                    for y, ky in aports.items():
                        if y == x:
                            self._add(md, Key("", "NEXT", x, v, a), Item(ds, kx, (ky,), "NEXT", ag))
                            next_cols.append(ky)
                        else:
                            self._add(md, Key("", "FEXT", x, v, a, y), Item(ds, kx, (ky,), "FEXT", ag))
                            fext.setdefault(y, []).append(ky)
                if next_cols:
                    self._add(md, Key("", "PSNEXT", x, v), Item(ds, kx, tuple(next_cols), "NEXT"))
                for y in sorted(fext, key=self._loc_order):
                    self._add(md, Key("", "PSFEXT", x, v, "", y), Item(ds, kx, tuple(fext[y]), "FEXT"))

    # -- scenarios -------------------------------------------------------

    def _scenario_items(self, md: ModelData, ds: int, scen: Scenario) -> None:
        proj = self.project
        nets = self._signal_nets(md, ds)
        roles: Dict[str, tuple] = {}
        no_rule, missing = [], []
        all_nets = sorted({p.net for p in md.ports if p.net}, key=natural_key)
        for net in all_nets:
            rule = proj.resolve_direction(scen, net)
            if rule is None:
                if net in nets:
                    no_rule.append(net)
                continue
            if rule.quiet:
                continue
            ports = nets.get(net, {})
            if rule.inp not in ports:
                missing.append(f"{net}: IN {rule.inp or '?'}")
                continue
            outs = [(o, ports[o]) for o in rule.out if o in ports and o != rule.inp]
            for o in rule.out:
                if o not in ports:
                    missing.append(f"{net}: OUT {o}")
            roles[net] = (rule.inp, ports[rule.inp], outs)
        md.roles[scen.name] = roles
        if no_rule:
            self.messages.append(
                f"{md.alias} / {scen.name}: no direction for {len(no_rule)} signal net(s), "
                f"treated as quiet: {_short(no_rule)}"
            )
        if missing:
            self.messages.append(
                f"{md.alias} / {scen.name}: {len(missing)} IN/OUT port(s) not found as "
                f"signal ports: {_short(missing)}"
            )

        name = scen.name
        for v, (vin, kin, outs) in roles.items():
            self._add(md, Key(name, "RL", vin, v), Item(ds, kin, (kin,)))
            for x, kx in outs:
                self._add(md, Key(name, "IL", x, v, "", vin), Item(ds, kx, (kin,)))
                self._add(md, Key(name, "RL", x, v), Item(ds, kx, (kx,)))
            for x, kx in outs:
                total: List[int] = []
                by_type: Dict[str, List[int]] = {"NEXT": [], "FEXT": []}
                by_group: Dict[str, List[Tuple[int, str]]] = {}
                for a, (ain, ka, _) in roles.items():
                    if not self.in_scope(v, a):
                        continue
                    typ = "NEXT" if ain == x else "FEXT"
                    ag = proj.group_of(a)
                    self._add(md, Key(name, "XT", x, v, a, ain), Item(ds, kx, (ka,), typ, ag))
                    total.append(ka)
                    by_type[typ].append(ka)
                    by_group.setdefault(ag, []).append((ka, typ))
                if not total:
                    continue
                self._add(md, Key(name, "PSXT", x, v), Item(ds, kx, tuple(total), _types(by_type)))
                for typ, cols in by_type.items():
                    if cols:
                        self._add(md, Key(name, "PSXT_TYPE", x, v, typ), Item(ds, kx, tuple(cols), typ))
                for g in sorted(by_group, key=self._group_order):
                    cols = [c for c, _ in by_group[g]]
                    typs = {t for _, t in by_group[g]}
                    xt = "NEXT+FEXT" if len(typs) > 1 else typs.pop()
                    self._add(md, Key(name, "PSXT_GRP", x, v, g), Item(ds, kx, tuple(cols), xt, g))

    def _group_order(self, group: str) -> int:
        groups = self.project.all_groups()
        return groups.index(group) if group in groups else len(groups)

    def victim_groups(self) -> List[str]:
        """Groups that have at least one curve, in display order."""
        used = {self.project.group_of(k.victim) for md in self.models.values() for k in md.items}
        return [g for g in self.project.all_groups() if g in used]

    # ------------------------------------------------------------------
    # queries
    # ------------------------------------------------------------------

    def item(self, alias: str, key: Key) -> Optional[Item]:
        md = self.models.get(alias)
        return md.items.get(key) if md else None

    def curve(self, alias: str, key: Key) -> np.ndarray:
        """The item's curve in dB over the model's frequency grid."""
        md = self.models[alias]
        it = md.items[key]
        p = md.datasets[it.ds].power[:, it.row, list(it.cols)].astype(float).sum(axis=-1)
        return _db(p)

    def value_at(self, alias: str, key: Key, f_hz: float) -> float:
        """The dB curve linearly interpolated at ``f_hz`` (NaN outside the data)."""
        md = self.models[alias]
        it = md.items.get(key)
        freq = md.freq
        if it is None or not (freq[0] <= f_hz <= freq[-1]):
            return float("nan")
        i1 = int(np.searchsorted(freq, f_hz))
        power = md.datasets[it.ds].power
        cols = list(it.cols)
        if freq[i1] == f_hz or i1 == 0:
            return float(_db(power[i1, it.row, cols].astype(float).sum()))
        i0 = i1 - 1
        d0 = _db(power[i0, it.row, cols].astype(float).sum())
        d1 = _db(power[i1, it.row, cols].astype(float).sum())
        t = (f_hz - freq[i0]) / (freq[i1] - freq[i0])
        return float(d0 + t * (d1 - d0))

    def nyquist_hz(self, key: Key) -> float:
        return self.project.nyquist_hz(self.project.group_of(key.victim))

    def value_at_nyquist(self, alias: str, key: Key) -> float:
        return self.value_at(alias, key, self.nyquist_hz(key))

    def keys(self, alias: str) -> List[Key]:
        md = self.models.get(alias)
        return list(md.items) if md else []

    def slot(self, alias: str, scen: str, kind: str, obs: str, victim: str) -> List[Key]:
        md = self.models.get(alias)
        return list(md.slots.get((scen, kind, obs, victim), [])) if md else []

    def xtype(self, key: Key) -> str:
        for md in self.models.values():
            it = md.items.get(key)
            if it is not None:
                return it.xtype
        return ""

    def label(self, key: Key) -> str:
        return label(key, self.multi_loc, self.xtype(key))


# ----------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------


def worse_is_lower(kind: str) -> bool:
    """IL is worst at its lowest value; everything else at its highest."""
    return kind == "IL"


def label(key: Key, multi_loc: bool = False, xtype: str = "") -> str:
    """Readable, searchable name: ``METRIC@obs | victim <- aggressor``."""
    s = f"{key.scen} " if key.scen else ""
    k, v, a = key.kind, key.victim, key.aggr
    if k == "IL":
        return f"{s}IL | {v} {key.src}→{key.obs}"
    if k == "RL":
        return f"{s}RL@{key.obs} | {v}"
    if k == "NEXT":
        return f"NEXT@{key.obs} | {v} ← {a}"
    if k == "FEXT":
        return f"FEXT@{key.obs} | {v} ← {a}@{key.src}"
    if k == "PSNEXT":
        return f"PSNEXT@{key.obs} | {v}"
    if k == "PSFEXT":
        return f"PSFEXT@{key.obs}" + (f"←{key.src}" if multi_loc else "") + f" | {v}"
    if k == "XT":
        at = f"@{key.src}" if multi_loc else ""
        return f"{s}XT@{key.obs} | {v} ← {a}{at} ({xtype})" if xtype else f"{s}XT@{key.obs} | {v} ← {a}{at}"
    if k == "PSXT":
        return f"{s}PSXT@{key.obs} | {v}"
    if k == "PSXT_TYPE":
        return f"{s}PS{a}@{key.obs} | {v}"
    if k == "PSXT_GRP":
        tail = f" ({xtype})" if xtype else ""
        return f"{s}PSXT@{key.obs} | {v} ← [{a}]{tail}"
    return f"{s}{k}@{key.obs} | {v}"


def metric_name(key: Key) -> str:
    """The metric part of a label, used to group curves into panels."""
    s = f"{key.scen} " if key.scen else ""
    if key.kind == "PSXT_TYPE":
        return f"{s}PS{key.aggr}"
    if key.kind == "PSXT_GRP":
        return f"{s}PSXT by group"
    return f"{s}{key.kind}"


def _db(p):
    with np.errstate(divide="ignore"):
        return np.maximum(10.0 * np.log10(np.maximum(p, 1e-300)), FLOOR_DB)


def _types(by_type: Dict[str, list]) -> str:
    present = [t for t in ("NEXT", "FEXT") if by_type[t]]
    return "+".join(present)


def _short(names: List[str], limit: int = 8) -> str:
    if len(names) <= limit:
        return ", ".join(names)
    return ", ".join(names[:limit]) + f", ... (+{len(names) - limit})"
