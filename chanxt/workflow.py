"""Glue shared by the CLI and the GUI: load models, map ports, build."""

from __future__ import annotations

import os
from typing import Dict, List, Optional

from .analysis import Analysis
from .project import (
    NAME_RULES,
    ModelSpec,
    PortMap,
    Project,
    auto_name_rule,
    parse_names,
    regex_for,
)
from .touchstone import Network, read_touchstone


def unique_alias(project: Project, path: str) -> str:
    base = os.path.splitext(os.path.basename(path))[0]
    taken = {m.alias for m in project.models}
    alias, n = base, 2
    while alias in taken:
        alias, n = f"{base}_{n}", n + 1
    return alias


def map_ports(spec: ModelSpec, network: Network, loc_alias: Dict[str, str]) -> None:
    """Fill ``spec.ports`` from the port names using the model's name rule."""
    if spec.name_rule == "auto":
        rule = auto_name_rule(network.port_names)
        regex = NAME_RULES[rule]
    else:
        regex = regex_for(spec)
    spec.ports = parse_names(network.port_names, regex, loc_alias)


def add_model(project: Project, network: Network, alias: Optional[str] = None) -> ModelSpec:
    spec = ModelSpec(path=network.path, alias=alias or unique_alias(project, network.path))
    map_ports(spec, network, project.loc_alias)
    project.models.append(spec)
    project.sync_locations()
    return spec


def load_networks(project: Project, overrides: Optional[Dict[str, str]] = None,
                  log=print) -> Dict[str, Network]:
    """Read every model's Touchstone file; map ports for models with no map yet."""
    overrides = overrides or {}
    nets: Dict[str, Network] = {}
    for spec in project.models:
        if spec.alias in overrides:
            spec.path = os.path.abspath(overrides[spec.alias])
        log(f"reading {spec.alias}: {spec.path}")
        net = read_touchstone(spec.path)
        if not spec.ports:
            map_ports(spec, net, project.loc_alias)
        elif len(spec.ports) != net.nports:
            log(f"  {spec.alias}: saved map has {len(spec.ports)} ports, file has "
                f"{net.nports}; re-mapping from the port names")
            map_ports(spec, net, project.loc_alias)
        nets[spec.alias] = net
    project.sync_locations()
    return nets


def build(project: Project, networks: Dict[str, Network], log=print) -> Analysis:
    analysis = Analysis(project, networks).build(progress=None)
    for m in analysis.messages:
        log("note: " + m)
    return analysis


def blank_ports(n: int) -> List[PortMap]:
    return [PortMap() for _ in range(n)]
