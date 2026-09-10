"""Generate a PrimeSim/HSPICE deck that re-extracts S-parameters from a BBS model.

The deck drives the BBS subcircuit with ``P`` port elements over exactly the
frequency grid of the reference Touchstone file and writes a Touchstone file
back out via ``.LIN``.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

import numpy as np

from .netlist import Subckt
from .touchstone import Network

GROUND = "0"

#: pin roles used by the deck generator and the GUI pin table
ROLE_PORT = "port"
ROLE_GROUND = "ground"
ROLE_FLOAT = "float"
ROLES = (ROLE_PORT, ROLE_GROUND, ROLE_FLOAT)


class DeckError(ValueError):
    """Raised when a deck cannot be generated from the current settings."""


def sanitize(name: str) -> str:
    """Make a Touchstone/BBS identifier safe to use as a SPICE net name."""
    clean = re.sub(r"[^A-Za-z0-9_]", "_", name).strip("_")
    if not clean or clean[0].isdigit():
        clean = "n_" + clean
    return clean


@dataclass
class PinAssignment:
    """One pin of the BBS subcircuit and what the deck connects it to."""

    pin: str  # pin name as written in .subckt
    role: str = ROLE_PORT
    port: int = 0  # 1-based Touchstone port index when role == ROLE_PORT
    minus: str = GROUND  # net the port's negative terminal returns to

    @property
    def net(self) -> str:
        if self.role == ROLE_GROUND:
            return GROUND
        return sanitize(self.pin)


@dataclass
class DeckConfig:
    """Everything needed to write the extraction deck."""

    bbs_path: str
    subckt: Subckt
    assignments: list[PinAssignment]
    freq: np.ndarray  # Hz, the grid to sweep
    z0: np.ndarray  # (N,) per-port reference impedance
    out_dir: str
    out_base: str = "bbs_sparam"
    deck_name: str = "bbs2spara.sp"
    float_leak_r: float = 1e9
    title: str = "BBS to S-parameter re-extraction"
    extra_options: list[str] = field(default_factory=lambda: [".option post=2"])
    max_points: int = 0  # 0 = keep every frequency point
    include_dc_note: bool = True

    @property
    def nports(self) -> int:
        return sum(1 for a in self.assignments if a.role == ROLE_PORT)

    @property
    def deck_path(self) -> str:
        return os.path.join(self.out_dir, self.deck_name)

    def expected_snp(self) -> str:
        return os.path.join(self.out_dir, f"{self.out_base}.s{self.nports}p")


def default_assignments(subckt: Subckt, nports: int) -> list[PinAssignment]:
    """Map pin *k* to port *k* and treat any surplus trailing pins as ground.

    This matches the usual BBS convention (signal pins in Touchstone port order,
    then a reference pin), and is the starting point the GUI lets you edit.
    """
    out: list[PinAssignment] = []
    for i, pin in enumerate(subckt.pins):
        if i < nports:
            out.append(PinAssignment(pin=pin, role=ROLE_PORT, port=i + 1))
        else:
            out.append(PinAssignment(pin=pin, role=ROLE_GROUND))
    return out


def validate(cfg: DeckConfig) -> list[str]:
    """Return a list of problems; empty means the config is usable."""
    problems: list[str] = []
    expected = len(cfg.z0)
    assigned = [a.port for a in cfg.assignments if a.role == ROLE_PORT]

    if len(cfg.assignments) != len(cfg.subckt.pins):
        problems.append(
            f"{len(cfg.assignments)} pin assignments for a subcircuit with "
            f"{len(cfg.subckt.pins)} pins"
        )
    if sorted(assigned) != list(range(1, expected + 1)):
        missing = sorted(set(range(1, expected + 1)) - set(assigned))
        dupes = sorted({p for p in assigned if assigned.count(p) > 1})
        if missing:
            problems.append(
                f"no pin assigned to port(s) {missing}. The reference has "
                f"{expected} ports, so the deck has to drive all {expected}. "
                "Giving a pin the 'ground' role drops it from the deck but not "
                "from the reference, which is why the port goes missing. To "
                "leave a port out of the comparison, keep it a port here and "
                "short it (or leave it open) on the Reference node tab instead "
                "- that applies to both networks, so they stay comparable."
            )
        if dupes:
            problems.append(f"port(s) {dupes} assigned to more than one pin")
        extra = sorted(p for p in set(assigned) if p > expected or p < 1)
        if extra:
            problems.append(f"port index {extra} outside 1..{expected}")
    known = {GROUND} | {a.net for a in cfg.assignments}
    for a in cfg.assignments:
        if a.role == ROLE_PORT and a.minus not in known:
            problems.append(
                f"port {a.port} returns to net {a.minus!r}, which no pin drives"
            )
    problems += _floating_nets(cfg)
    if cfg.freq.size == 0:
        problems.append("frequency grid is empty")
    return problems


def _floating_nets(cfg: DeckConfig) -> list[str]:
    """Report nets the deck leaves with no path to global ground.

    A port element bridges its two nodes with z0, a float pin gets a leak
    resistor to 0 and a ground pin *is* 0, so almost every pin map is fine -
    including the common one where every pin is a port returning to 0 and no
    pin is grounded at all.  Only a pin map whose ports reference each other in
    a closed loop can strand a net, and that is what this catches.
    """
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for a in cfg.assignments:
        find(a.net)
        if a.role == ROLE_PORT:
            union(a.net, a.minus)  # the port's z0 bridges these two nodes
        else:
            union(a.net, GROUND)  # tied to 0, or leaked to 0 through Rleak

    stranded = sorted({a.net for a in cfg.assignments if find(a.net) != find(GROUND)})
    if not stranded:
        return []
    return [
        f"net(s) {stranded} have no path to global ground - every port there "
        "returns to another port's node, so the AC solve would be singular. "
        "Give one of those pins the 'ground' or 'float' role, or point a "
        "port's 'Returns to' at 0."
    ]


def sweep_points(cfg: DeckConfig) -> np.ndarray:
    """The frequency points the deck will actually sweep (DC dropped)."""
    freq = np.asarray(cfg.freq, dtype=float)
    freq = freq[freq > 0.0]  # .AC cannot evaluate 0 Hz
    if cfg.max_points and freq.size > cfg.max_points:
        idx = np.unique(
            np.round(np.linspace(0, freq.size - 1, cfg.max_points)).astype(int)
        )
        freq = freq[idx]
    return freq


def build_deck(cfg: DeckConfig) -> str:
    """Render the deck text.  Raises :class:`DeckError` if the config is invalid."""
    problems = validate(cfg)
    if problems:
        raise DeckError("; ".join(problems))

    ports = sorted(
        (a for a in cfg.assignments if a.role == ROLE_PORT), key=lambda a: a.port
    )
    freq = sweep_points(cfg)
    if freq.size == 0:
        raise DeckError("no positive frequency points to sweep")

    inc = os.path.relpath(os.path.abspath(cfg.bbs_path), os.path.abspath(cfg.out_dir))
    if inc.startswith(".."):  # a relative path that climbs out is fragile
        inc = os.path.abspath(cfg.bbs_path)
    lines: list[str] = [
        f"* {cfg.title}",
        "* Generated by sparabbs - do not edit by hand.",
        f"* BBS model : {os.path.basename(cfg.bbs_path)} (.subckt {cfg.subckt.name})",
        f"* Ports     : {cfg.nports}",
        f"* Sweep     : {freq.size} points, "
        f"{freq[0]:.6g} Hz .. {freq[-1]:.6g} Hz",
        "",
        f".title {sanitize(cfg.out_base)}",
    ]
    lines += cfg.extra_options
    lines += ["", f".inc '{inc}'", ""]

    if cfg.include_dc_note and np.any(np.asarray(cfg.freq) <= 0.0):
        lines.insert(
            5, "* NOTE: the reference file has a 0 Hz point; .AC cannot sweep it"
        )

    lines.append("* ---- ports ----------------------------------------------------")
    for a in ports:
        z = float(cfg.z0[a.port - 1])
        lines.append(
            f"P{a.port} {a.net} {a.minus} port={a.port} z0={z:g} dc=0 ac=0"
        )

    floats = [a for a in cfg.assignments if a.role == ROLE_FLOAT]
    if floats:
        lines += [
            "",
            "* ---- leakage to ground for unconnected pins --------------------",
        ]
        for a in floats:
            lines.append(f"Rleak_{a.net} {a.net} {GROUND} {cfg.float_leak_r:g}")

    lines += ["", "* ---- device under test ------------------------------------------"]
    nets = " ".join(a.net for a in cfg.assignments)
    lines += _wrap_continuation(f"XDUT {nets} {cfg.subckt.name}")

    lines += ["", "* ---- analysis ---------------------------------------------------"]
    lines += _ac_poi(freq)
    lines.append(
        f".lin sparcalc=1 format=touchstone filename='{cfg.out_base}'"
    )
    lines += ["", ".end", ""]
    return "\n".join(lines)


def _wrap_continuation(text: str, width: int = 100) -> list[str]:
    """Break a long SPICE statement across '+' continuation lines."""
    words = text.split()
    out, cur = [], words[0]
    for w in words[1:]:
        if len(cur) + 1 + len(w) > width:
            out.append(cur)
            cur = "+ " + w
        else:
            cur += " " + w
    out.append(cur)
    return out


def _ac_poi(freq: np.ndarray) -> list[str]:
    """``.AC POI`` sweeps exactly the reference grid, so no interpolation is needed."""
    head = f".ac poi {freq.size}"
    body = " ".join(f"{f:.10g}" for f in freq)
    return _wrap_continuation(f"{head} {body}")


def write_deck(cfg: DeckConfig) -> str:
    """Write the deck to :attr:`DeckConfig.deck_path` and return that path."""
    os.makedirs(cfg.out_dir, exist_ok=True)
    text = build_deck(cfg)
    with open(cfg.deck_path, "w") as fh:
        fh.write(text)
    return cfg.deck_path


def config_from_inputs(
    ref: Network,
    subckt: Subckt,
    bbs_path: str,
    out_dir: str,
    assignments: list[PinAssignment] | None = None,
    **kw,
) -> DeckConfig:
    """Build a :class:`DeckConfig` from a parsed reference network and subcircuit."""
    if assignments is None:
        assignments = default_assignments(subckt, ref.nports)
    return DeckConfig(
        bbs_path=os.path.abspath(bbs_path),
        subckt=subckt,
        assignments=assignments,
        freq=ref.freq,
        z0=ref.z0,
        out_dir=os.path.abspath(out_dir),
        **kw,
    )
