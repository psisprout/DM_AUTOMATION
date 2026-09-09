"""Minimal SPICE netlist scanner.

Just enough to discover the subcircuits a broadband-SPICE (BBS) file defines,
their pin order, and which one is the top-level model.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

_SUBCKT_RE = re.compile(r"^\s*\.subckt\s+(\S+)\s*(.*)$", re.IGNORECASE)
_ENDS_RE = re.compile(r"^\s*\.ends\b", re.IGNORECASE)
_INC_RE = re.compile(r"^\s*\.(?:include|inc|lib)\s+[\"']?([^\"'\s]+)", re.IGNORECASE)
_XINST_RE = re.compile(r"^\s*x\S+\s+(.*)$", re.IGNORECASE)


class NetlistError(ValueError):
    """Raised when a netlist cannot be interpreted."""


@dataclass
class Subckt:
    name: str
    pins: list[str]
    line: int
    instantiated_by: set[str] = field(default_factory=set)

    @property
    def npins(self) -> int:
        return len(self.pins)


@dataclass
class NetlistInfo:
    path: str
    subckts: list[Subckt]
    includes: list[str]

    def by_name(self, name: str) -> Subckt:
        for sc in self.subckts:
            if sc.name.lower() == name.lower():
                return sc
        raise NetlistError(f"no .subckt named {name!r} in {self.path}")

    def top_candidates(self) -> list[Subckt]:
        """Subcircuits nothing else instantiates - most likely the BBS model."""
        tops = [sc for sc in self.subckts if not sc.instantiated_by]
        return tops or list(self.subckts)


def _logical_lines(lines: list[str]):
    """Yield (line_number, text) with '+' continuations folded in."""
    buf, start = "", 0
    for n, raw in enumerate(lines, 1):
        text = raw.split("$", 1)[0].rstrip("\n")
        if text.lstrip().startswith("*"):
            continue
        if text.strip().startswith("+"):
            buf += " " + text.strip()[1:].strip()
            continue
        if buf.strip():
            yield start, buf
        buf, start = text, n
    if buf.strip():
        yield start, buf


def _pin_tokens(rest: str) -> list[str]:
    """Pins stop at the first ``name=value`` parameter token."""
    pins = []
    for tok in rest.replace("=", " = ").split():
        if tok == "=":
            return pins[:-1]
        pins.append(tok)
    return pins


def read_netlist(path: str) -> NetlistInfo:
    """Scan a SPICE file for .subckt definitions and X instantiations."""
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        lines = fh.readlines()

    subckts: list[Subckt] = []
    includes: list[str] = []
    instantiations: list[tuple[str, str]] = []  # (child, enclosing scope)
    stack: list[str] = []

    for lineno, text in _logical_lines(lines):
        m = _SUBCKT_RE.match(text)
        if m:
            subckts.append(
                Subckt(name=m.group(1), pins=_pin_tokens(m.group(2)), line=lineno)
            )
            stack.append(m.group(1))
            continue
        if _ENDS_RE.match(text):
            if stack:
                stack.pop()
            continue
        m = _INC_RE.match(text)
        if m:
            includes.append(m.group(1))
            continue
        m = _XINST_RE.match(text)
        if m:
            toks = _pin_tokens(m.group(1))
            if toks:
                instantiations.append((toks[-1].lower(), stack[-1] if stack else "<file>"))

    # resolved after the scan so a forward reference still counts
    defined = {sc.name.lower(): sc for sc in subckts}
    for child, parent in instantiations:
        if child in defined:
            defined[child].instantiated_by.add(parent)

    if not subckts:
        raise NetlistError(
            f"{os.path.basename(path)} defines no .subckt - "
            "sparabbs needs the BBS model wrapped in a subcircuit"
        )
    return NetlistInfo(path=os.path.abspath(path), subckts=subckts, includes=includes)
