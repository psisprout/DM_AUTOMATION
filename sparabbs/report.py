"""Render a :class:`~sparabbs.compare.CompareResult` as XML."""

from __future__ import annotations

import os
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import numpy as np

from .compare import PASS, CompareResult
from .touchstone import Network

XSLT_HREF = "report.xsl"


def _n(x: float) -> str:
    """Format a number for XML: finite values get 6 significant digits."""
    if x is None or (isinstance(x, float) and not np.isfinite(x)):
        return "inf" if (x is not None and x > 0) else "nan"
    return f"{x:.6g}"


def build_xml(
    result: CompareResult,
    ref: Network,
    dut: Network,
    deck_path: str = "",
    command: str = "",
) -> ET.ElementTree:
    """Build the comparison report tree."""
    crit = result.criteria
    root = ET.Element(
        "bbs_validation",
        {
            "status": result.status,
            "nports": str(result.nports),
            "generator": "sparabbs",
            "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        },
    )

    meta = ET.SubElement(root, "meta")
    ET.SubElement(
        meta,
        "reference",
        {
            "file": ref.path,
            "nports": str(ref.nports),
            "points": str(ref.npoints),
            "f_min": _n(float(ref.freq[0])),
            "f_max": _n(float(ref.freq[-1])),
            "z0": _n(ref.uniform_z0() if ref.uniform_z0() is not None else 0.0),
            "z0_uniform": "true" if ref.uniform_z0() is not None else "false",
            "touchstone_version": ref.version,
        },
    )
    ET.SubElement(
        meta,
        "bbs_result",
        {
            "file": dut.path,
            "nports": str(dut.nports),
            "points": str(dut.npoints),
            "f_min": _n(float(dut.freq[0])),
            "f_max": _n(float(dut.freq[-1])),
            "z0": _n(dut.uniform_z0() if dut.uniform_z0() is not None else 0.0),
        },
    )
    if deck_path or command:
        ET.SubElement(meta, "spice", {"deck": deck_path, "command": command})
    ET.SubElement(
        meta,
        "reference_node",
        {
            "mode": result.reference.mode,
            "ports": " ".join(str(p) for p in result.reference.ports),
            "description": result.reference.describe(ref.port_names),
        },
    )
    ports_el = ET.SubElement(meta, "ports", {"count": str(result.nports)})
    for k, nm in enumerate(result.port_names, 1):
        ET.SubElement(ports_el, "port", {"index": str(k), "name": nm})
    for note in result.notes:
        ET.SubElement(meta, "note").text = note

    ET.SubElement(
        root,
        "criteria",
        {
            "mag_err_pct": _n(crit.mag_err_pct),
            "err_db": _n(crit.err_db),
            "phase_err_deg": _n(crit.phase_err_deg),
            "peak_shift_pct": _n(crit.peak_shift_pct),
            "peak_mag_err_pct": _n(crit.peak_mag_err_pct),
            "warn_frac": _n(crit.warn_frac),
            "significance_floor": _n(crit.significance_floor),
        },
    )

    counts = result.counts()
    ET.SubElement(
        root,
        "summary",
        {
            "status": result.status,
            "terms": str(len(result.terms)),
            "passed": str(counts[PASS]),
            "warned": str(counts["WARN"]),
            "failed": str(counts["FAIL"]),
            "points": str(result.freq.size),
        },
    )

    checks = ET.SubElement(root, "checks")
    for name, status, detail in result.checks:
        ET.SubElement(
            checks, "check", {"name": name, "status": status, "detail": detail}
        )

    terms_el = ET.SubElement(root, "terms")
    for t in result.terms:
        te = ET.SubElement(
            terms_el,
            "term",
            {
                "name": t.name,
                "i": str(t.i),
                "j": str(t.j),
                "port_i": result.port_names[t.i - 1],
                "port_j": result.port_names[t.j - 1],
                "kind": "self" if t.i == t.j else "mutual",
                "status": t.status,
            },
        )
        for b in t.bands:
            ET.SubElement(
                te,
                "band",
                {
                    "name": b.name,
                    "status": b.status,
                    "f_lo": _n(b.f_lo),
                    "f_hi": _n(b.f_hi),
                    "points": str(b.npoints),
                    "norm_err_pct": _n(b.norm_err_pct),
                    "max_err_db": _n(b.max_err_db),
                    "rmse_db": _n(b.rmse_db),
                    "max_phase_deg": _n(b.max_phase_deg),
                    "f_worst": _n(b.f_worst),
                },
            )
        for p in t.peaks:
            ET.SubElement(
                te,
                "resonance",
                {
                    "status": p.status,
                    "ref_f": _n(p.ref_f),
                    "dut_f": _n(p.dut_f),
                    "shift_pct": _n(p.shift_pct),
                    "ref_mag_ohm": _n(p.ref_mag),
                    "dut_mag_ohm": _n(p.dut_mag),
                    "mag_err_pct": _n(p.mag_err_pct),
                },
            )
    return ET.ElementTree(root)


def write_xml(tree: ET.ElementTree, path: str, stylesheet: bool = True) -> str:
    """Write the report, optionally with an XSLT processing instruction."""
    ET.indent(tree, space="  ")
    body = ET.tostring(tree.getroot(), encoding="unicode")
    head = '<?xml version="1.0" encoding="UTF-8"?>\n'
    if stylesheet:
        head += f'<?xml-stylesheet type="text/xsl" href="{XSLT_HREF}"?>\n'
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(head + body + "\n")
    _copy_resource(XSLT_HREF, os.path.dirname(os.path.abspath(path)))
    return path


def to_string(tree: ET.ElementTree) -> str:
    ET.indent(tree, space="  ")
    return ET.tostring(tree.getroot(), encoding="unicode")


def _copy_resource(name: str, dest_dir: str) -> None:
    src = os.path.join(os.path.dirname(__file__), "resources", name)
    dst = os.path.join(dest_dir, name)
    if os.path.exists(src) and not os.path.exists(dst):
        with open(src) as a, open(dst, "w") as b:
            b.write(a.read())


def write_junit(result: CompareResult, path: str, suite: str = "bbs_validation") -> str:
    """Also emit JUnit XML so the run can gate a CI job."""
    counts = result.counts()
    ts = ET.Element(
        "testsuite",
        {
            "name": suite,
            "tests": str(len(result.terms) + len(result.checks)),
            "failures": str(counts["FAIL"]),
            "errors": "0",
        },
    )
    for name, status, detail in result.checks:
        tc = ET.SubElement(ts, "testcase", {"classname": suite, "name": name})
        if status == "FAIL":
            ET.SubElement(tc, "failure", {"message": detail})
        elif status == "WARN":
            ET.SubElement(tc, "system-out").text = f"WARN: {detail}"
    for t in result.terms:
        tc = ET.SubElement(ts, "testcase", {"classname": f"{suite}.Z", "name": t.name})
        detail = "; ".join(
            f"{b.name}: {b.norm_err_pct:.3g}% / {b.max_err_db:.3g}dB [{b.status}]"
            for b in t.bands
        )
        if t.status == "FAIL":
            ET.SubElement(tc, "failure", {"message": detail})
        elif t.status == "WARN":
            ET.SubElement(tc, "system-out").text = f"WARN: {detail}"
    tree = ET.ElementTree(ts)
    ET.indent(tree, space="  ")
    tree.write(path, encoding="utf-8", xml_declaration=True)
    return path
