"""Tests for the agent-driven flow: DCR extraction, renorm sizing, batch ranking."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sparabbs import batch as batch_mod  # noqa: E402
from sparabbs import compare as cmp_mod  # noqa: E402
from sparabbs import dcr as dcr_mod  # noqa: E402
from sparabbs.touchstone import (  # noqa: E402
    Network,
    read_touchstone,
    s_to_z,
    write_touchstone,
    z_to_s,
)
from tests import make_fixtures as fx  # noqa: E402

DATA = fx.DATA


def _ensure():
    if not os.path.exists(os.path.join(DATA, "pdn3.s3p")):
        fx.main()


class ResolvePortTests(unittest.TestCase):
    """A near-miss must never silently reference the wrong rail."""

    def setUp(self):
        _ensure()
        self.net = read_touchstone(os.path.join(DATA, "pdn3.s3p"))

    def test_exact_name(self):
        self.assertEqual(dcr_mod.resolve_port(self.net, "VDD_PMIC"), 3)

    def test_surrounding_whitespace_and_case_are_forgiven(self):
        self.assertEqual(dcr_mod.resolve_port(self.net, "  vdd_pmic "), 3)

    def test_a_typo_is_refused_not_guessed(self):
        for typo in ("VDD_PMI", "VDD_PMICC", "VDD PMIC", "VDDPMIC", "VDD-PMIC"):
            with self.assertRaises(dcr_mod.PortNotFound, msg=typo):
                dcr_mod.resolve_port(self.net, typo)

    def test_a_prefix_of_a_real_name_is_refused(self):
        """VDD_ matches three ports; picking one would be a guess."""
        with self.assertRaises(dcr_mod.PortNotFound):
            dcr_mod.resolve_port(self.net, "VDD_")

    def test_an_empty_answer_is_refused(self):
        with self.assertRaises(dcr_mod.PortNotFound):
            dcr_mod.resolve_port(self.net, "   ")

    def test_the_error_lists_the_real_names(self):
        with self.assertRaises(dcr_mod.PortNotFound) as caught:
            dcr_mod.resolve_port(self.net, "GND")
        message = str(caught.exception)
        for name in self.net.port_names:
            self.assertIn(name, message)

    def test_a_duplicate_name_cannot_identify_a_port(self):
        net = read_touchstone(os.path.join(DATA, "pdn3.s3p"))
        net.port_names = ["SAME", "SAME", "OTHER"]
        with self.assertRaises(dcr_mod.PortNotFound) as caught:
            dcr_mod.resolve_port(net, "SAME")
        self.assertIn("more than one", str(caught.exception))


class DcrTests(unittest.TestCase):
    def _net(self, dcr=(1e-3, 4e-3), ref_r=5e-4, npts=200):
        f = np.logspace(2, 9, npts)
        n = len(dcr) + 1
        w = 2 * np.pi * f
        y = np.zeros((f.size, n, n), dtype=complex)
        for k, r in enumerate(dcr):
            y[:, k, k] += 1.0 / (r + 1j * w * 5e-10) + 1j * w * 2e-6
        y[:, n - 1, n - 1] += 1.0 / ref_r
        z = np.linalg.inv(y)
        z0 = np.full(n, 50.0)
        names = [f"VDD{k + 1}" for k in range(n - 1)] + ["GND_REF"]
        return Network(freq=f, s=z_to_s(z, z0), z0=z0, port_names=names)

    def test_reference_port_is_dropped_from_the_report(self):
        rep = dcr_mod.extract(self._net(), "GND_REF")
        self.assertEqual(rep.reference_name, "GND_REF")
        self.assertEqual([p.name for p in rep.ports], ["VDD1", "VDD2"])

    def test_dcr_is_the_real_part_at_the_lowest_frequency(self):
        net = self._net()
        rep = dcr_mod.extract(net, "GND_REF")
        z = s_to_z(net.s, net.z0)
        spec = cmp_mod.ReferenceSpec(mode=cmp_mod.REF_PORT, ports=[3])
        zr, _ = cmp_mod.apply_reference(z, net.port_names, spec)
        for k, port in enumerate(rep.ports):
            self.assertAlmostEqual(port.dcr_ohm, float(np.real(zr[0, k, k])))
            self.assertEqual(port.at_hz, net.freq[0])

    def test_dcr_tracks_the_resistance_that_was_built_in(self):
        rep = dcr_mod.extract(self._net(dcr=(1e-3, 4e-3)), "GND_REF")
        self.assertLess(rep.ports[0].dcr_ohm, rep.ports[1].dcr_ohm)

    def test_an_unsettled_dcr_is_flagged(self):
        net = self._net(npts=6)  # a coarse grid never reaches DC
        rep = dcr_mod.extract(net, "GND_REF", settle_tol=1e-9)
        self.assertFalse(all(p.settled for p in rep.ports))
        self.assertTrue(any("not a settled value" in w for w in rep.warnings))

    def test_a_file_that_starts_high_warns(self):
        net = self._net()
        net.freq = np.logspace(7, 9, net.freq.size)
        rep = dcr_mod.extract(net, "GND_REF")
        self.assertTrue(any("lowest frequency" in w for w in rep.warnings))

    def test_z_range_spans_dcr_to_the_resonance_peak(self):
        rep = dcr_mod.extract(self._net(), "GND_REF")
        lo, hi = rep.z_range()
        self.assertGreater(hi, lo)
        self.assertAlmostEqual(lo, min(p.dcr_ohm for p in rep.ports))


class RenormTests(unittest.TestCase):
    def test_centre_is_the_geometric_mean(self):
        self.assertAlmostEqual(dcr_mod.geometric_center(1e-3, 1e-1), 1e-2)

    def test_centre_minimises_the_worst_case_amplification(self):
        """A relative S error lands in Z amplified by (Z+z0)^2/(2 z0 Z)."""
        lo, hi = 1e-3, 1.0

        def worst(z0):
            return max((lo + z0) ** 2 / (2 * z0 * lo), (hi + z0) ** 2 / (2 * z0 * hi))

        centre = dcr_mod.geometric_center(lo, hi)
        grid = np.logspace(-5, 2, 600)
        self.assertAlmostEqual(
            np.log10(centre), np.log10(grid[np.argmin([worst(v) for v in grid])]), places=1
        )
        self.assertLess(worst(centre), worst(50.0) / 100, "50 ohm is far off centre")

    def test_degenerate_ranges_do_not_explode(self):
        self.assertEqual(dcr_mod.geometric_center(0.0, 1.0), 0.0)
        self.assertEqual(dcr_mod.geometric_center(1.0, 0.0), 0.0)

    def test_ladder_is_centred_and_log_spaced(self):
        rep = dcr_mod.DcrReport(
            snp="", nports=3, reference_index=3, reference_name="G",
            f_min_hz=1.0, f_max_hz=1e9,
            ports=[dcr_mod.PortDcr(1, "A", 1e-3, 1.0, 1e-1, 1e6, True)],
        )
        values = dcr_mod.recommend_renorm(rep, count=5, step_decades=0.5)
        self.assertEqual(len(values), 5)
        self.assertAlmostEqual(np.log10(values[2]), np.log10(1e-2), places=2)
        steps = np.diff(np.log10(values))
        np.testing.assert_allclose(steps, 0.5, atol=0.02)

    def test_labels_match_the_nde_naming(self):
        self.assertEqual(dcr_mod.label_for(0.01), "0p01")
        self.assertEqual(dcr_mod.label_for(0.001), "0p001")
        self.assertEqual(dcr_mod.label_for(1.0), "1")


class DcrCliTests(unittest.TestCase):
    def setUp(self):
        _ensure()
        self.snp = os.path.join(DATA, "pdn3.s3p")

    def test_list_ports_emits_the_file_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "ports.json")
            rc = dcr_mod.main(["--snp", self.snp, "--list-ports", "--json", out])
            self.assertEqual(rc, 0)
            with open(out) as fh:
                data = json.load(fh)
        self.assertEqual([p["name"] for p in data["ports"]],
                         ["VDD_CORE", "VDD_IO", "VDD_PMIC"])
        self.assertFalse(data["port_names_are_generic"])

    def test_generic_names_are_declared_as_such(self):
        with tempfile.TemporaryDirectory() as tmp:
            net = read_touchstone(self.snp)
            net.port_names = ["P1", "P2", "P3"]
            path = write_touchstone(net, os.path.join(tmp, "plain.s3p"))
            out = os.path.join(tmp, "p.json")
            dcr_mod.main(["--snp", path, "--list-ports", "--json", out])
            with open(out) as fh:
                self.assertTrue(json.load(fh)["port_names_are_generic"])

    def test_a_typo_exits_three_without_a_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "d.json")
            rc = dcr_mod.main(["--snp", self.snp, "--reference", "VDD_PMI", "--json", out])
        self.assertEqual(rc, 3)
        self.assertFalse(os.path.exists(out), "a refused choice must write nothing")

    def test_a_missing_reference_is_refused(self):
        self.assertEqual(dcr_mod.main(["--snp", self.snp]), 2)

    def test_an_index_is_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "d.json")
            rc = dcr_mod.main(["--snp", self.snp, "--reference", "3", "--json", out])
            self.assertEqual(rc, 0)
            with open(out) as fh:
                data = json.load(fh)
        self.assertEqual(data["reference_name"], "VDD_PMIC")

    def test_an_out_of_range_index_is_refused(self):
        self.assertEqual(dcr_mod.main(["--snp", self.snp, "--reference", "9"]), 2)

    def test_the_report_carries_the_renorm_ladder(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "d.json")
            dcr_mod.main(["--snp", self.snp, "--reference", "VDD_PMIC", "--json", out])
            with open(out) as fh:
                data = json.load(fh)
        self.assertEqual(len(data["renorm_impedances"]), 5)
        self.assertEqual(len(data["renorm_labels"]), 5)
        self.assertGreater(data["renorm_center_ohm"], 0)


class BatchTests(unittest.TestCase):
    def setUp(self):
        _ensure()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ref = read_touchstone(os.path.join(DATA, "pdn3.s3p"))
        self.snp = write_touchstone(self.ref, os.path.join(self.tmp.name, "a.s3p"))
        self.z = s_to_z(self.ref.s, self.ref.z0)

    def _candidate(self, label, scale):
        dut = Network(
            freq=self.ref.freq,
            s=z_to_s(self.z * scale, self.ref.z0),
            z0=self.ref.z0.copy(),
            port_names=list(self.ref.port_names),
        )
        return write_touchstone(dut, os.path.join(self.tmp.name, f"a_sp_{label}.s3p"))

    def test_renorm_is_read_from_the_filename(self):
        self.assertEqual(batch_mod.renorm_of("a_sp_0p01.sp"), 0.01)
        self.assertEqual(batch_mod.renorm_of("a_sp_1.sp"), 1.0)
        self.assertIsNone(batch_mod.renorm_of("model.sp"))

    def test_a_touchstone_candidate_skips_the_simulation(self):
        path = self._candidate("0p01", 1.0)
        cand = batch_mod.evaluate(self.ref, path, self.tmp.name, command="false")
        self.assertTrue(cand.ok, cand.error)
        self.assertEqual(cand.status, cmp_mod.PASS)
        self.assertEqual(cand.snp, os.path.abspath(path))

    def test_ranking_puts_the_closest_first(self):
        paths = [self._candidate("0p1", 1.18), self._candidate("0p01", 1.004),
                 self._candidate("0p001", 1.06)]
        cands = [batch_mod.evaluate(self.ref, p, self.tmp.name) for p in paths]
        self.assertEqual([c.label for c in batch_mod.rank(cands)][0], "a_sp_0p01")
        self.assertEqual(batch_mod.best(cands).renorm_ohm, 0.01)

    def test_a_failing_set_has_no_best(self):
        cands = [batch_mod.evaluate(self.ref, self._candidate("0p1", 1.5), self.tmp.name)]
        self.assertIsNone(batch_mod.best(cands))
        self.assertEqual(batch_mod.best(cands, require=cmp_mod.WARN), None)

    def test_a_broken_candidate_is_recorded_not_raised(self):
        bad = os.path.join(self.tmp.name, "truncated.s3p")
        with open(bad, "w") as fh:
            fh.write("not a touchstone file\n")
        cand = batch_mod.evaluate(self.ref, bad, self.tmp.name)
        self.assertFalse(cand.ok)
        self.assertTrue(cand.error)
        self.assertEqual(cand.status, cmp_mod.FAIL)

    def test_an_errored_candidate_never_wins(self):
        good = batch_mod.evaluate(self.ref, self._candidate("0p01", 1.0), self.tmp.name)
        bad = batch_mod.Candidate(path="x", label="x", error="boom")
        self.assertEqual(batch_mod.best([bad, good]).label, "a_sp_0p01")

    def test_cli_writes_the_json_an_agent_reads(self):
        paths = [self._candidate("0p1", 1.18), self._candidate("0p01", 1.004)]
        out = os.path.join(self.tmp.name, "run")
        rc = batch_mod.main(["--snp", self.snp, *paths, "--out", out, "--quiet"])
        self.assertEqual(rc, 0)
        with open(os.path.join(out, "compare.json")) as fh:
            data = json.load(fh)
        self.assertTrue(data["any_accepted"])
        self.assertEqual(data["best"]["label"], "a_sp_0p01")
        self.assertEqual(data["ranking"][0], "a_sp_0p01")
        self.assertEqual(len(data["candidates"]), 2)
        self.assertIn("criteria", data)

    def test_cli_exits_one_when_nothing_passes(self):
        path = self._candidate("0p1", 1.5)
        out = os.path.join(self.tmp.name, "run2")
        rc = batch_mod.main(["--snp", self.snp, path, "--out", out, "--quiet"])
        self.assertEqual(rc, 1)
        with open(os.path.join(out, "compare.json")) as fh:
            data = json.load(fh)
        self.assertFalse(data["any_accepted"])
        self.assertIsNone(data["best"])

    def test_cli_honours_the_reference_node(self):
        path = self._candidate("0p01", 1.0)
        out = os.path.join(self.tmp.name, "run3")
        batch_mod.main([
            "--snp", self.snp, path, "--out", out, "--quiet",
            "--ref-mode", "port", "--ref-ports", "3",
        ])
        with open(os.path.join(out, "compare.json")) as fh:
            data = json.load(fh)
        self.assertEqual(data["reference_node"], {"mode": "port", "ports": [3]})


class SkillDocTests(unittest.TestCase):
    """The skills are the agent's instructions; keep them present and shaped."""

    ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "skills")
    NAMES = ("select-reference-node", "compare-bbs-candidates", "report-best-bbs")

    def test_every_skill_exists_with_front_matter(self):
        for name in self.NAMES:
            path = os.path.join(self.ROOT, name, "SKILL.md")
            self.assertTrue(os.path.exists(path), path)
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
            self.assertTrue(text.startswith("---\n"), name)
            head = text.split("---")[1]
            self.assertIn(f"name: {name}", head)
            self.assertIn("description:", head)

    def test_the_commands_the_skills_name_exist(self):
        import subprocess

        for module in ("sparabbs.dcr", "sparabbs.batch"):
            proc = subprocess.run(
                [sys.executable, "-m", module, "--help"], capture_output=True
            )
            self.assertEqual(proc.returncode, 0, module)

    def test_the_reference_skill_forbids_guessing(self):
        path = os.path.join(self.ROOT, "select-reference-node", "SKILL.md")
        with open(path, encoding="utf-8") as fh:
            text = fh.read().lower()
        for phrase in ("do not guess", "fuzzy", "ask", "exact"):
            self.assertIn(phrase, text)


if __name__ == "__main__":
    unittest.main()
