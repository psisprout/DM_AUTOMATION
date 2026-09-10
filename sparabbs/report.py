"""Emit the comparison as JUnit XML so a run can gate a CI job."""

from __future__ import annotations

import xml.etree.ElementTree as ET

from .compare import CompareResult


def _indent(elem: ET.Element, space: str = "  ", level: int = 0) -> None:
    """Pretty-print in place.

    ``ET.indent`` only exists from Python 3.9, and site EDA installs are often
    older, so do it here rather than depend on the version.
    """
    pad = "\n" + space * level
    if len(elem):
        if not (elem.text or "").strip():
            elem.text = pad + space
        for child in elem:
            _indent(child, space, level + 1)
        if not (child.tail or "").strip():
            child.tail = pad
    if level and not (elem.tail or "").strip():
        elem.tail = pad


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
    _indent(ts)
    tree.write(path, encoding="utf-8", xml_declaration=True)
    return path
