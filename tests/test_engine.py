"""Engine tests.  Run with: python -m unittest discover -s tests -v"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sparabbs import compare as cmp_mod  # noqa: E402
from sparabbs import deck as deck_mod  # noqa: E402
from sparabbs import report as report_mod  # noqa: E402
from sparabbs import runner as runner_mod  # noqa: E402
from sparabbs.netlist import read_netlist  # noqa: E402
from sparabbs.touchstone import (  # noqa: E402
    Network,
    read_touchstone,
    renormalize,
    s_to_z,
    write_touchstone,
    z_to_s,
)
from tests import make_fixtures as fx  # noqa: E402

DATA = fx.DATA


def _ensure_fixtures():
    if not os.path.exists(os.path.join(DATA, "pdn3.s3p")):
        fx.main()


class TouchstoneTests(unittest.TestCase):
    def setUp(self):
        _ensure_fixtures()
        self.net = read_touchstone(os.path.join(DATA, "pdn3.s3p"))

    def test_header(self):
        self.assertEqual(self.net.nports, 3)
        self.assertEqual(self.net.npoints, 400)
        self.assertEqual(self.net.uniform_z0(), 50.0)
        self.assertTrue(self.net.z0_from_file)
        self.assertEqual(self.net.port_names[0], "VDD_CORE")

    def test_matches_the_analytic_network(self):
        want = fx.reference_network(self.net.freq)
        np.testing.assert_allclose(self.net.s, want.s, rtol=1e-8, atol=1e-11)

    def test_roundtrip_all_formats(self):
        with tempfile.TemporaryDirectory() as tmp:
            for fmt in ("RI", "MA", "DB"):
                p = write_touchstone(self.net, os.path.join(tmp, f"r_{fmt}.s3p"), fmt)
                back = read_touchstone(p)
                np.testing.assert_allclose(back.s, self.net.s, rtol=1e-7, atol=1e-10)

    def test_two_port_v1_ordering_is_s11_s21_s12_s22(self):
        """v1 stores the 2-port transposed; a round trip must undo that."""
        f = np.array([1e6, 2e6])
        s = np.array(
            [[[0.1 + 0j, 0.2 + 0j], [0.3 + 0j, 0.4 + 0j]],
             [[0.5 + 0j, 0.6 + 0j], [0.7 + 0j, 0.8 + 0j]]]
        )
        net = Network(freq=f, s=s, z0=np.full(2, 50.0), port_names=["A", "B"])
        with tempfile.TemporaryDirectory() as tmp:
            p = write_touchstone(net, os.path.join(tmp, "t.s2p"))
            with open(p) as fh:
                row = [ln for ln in fh if ln[0].isdigit()][0].split()
            # written order must be S11 S21 S12 S22
            self.assertEqual(row[1], "0.1")
            self.assertEqual(row[3], "0.3")
            self.assertEqual(row[5], "0.2")
            np.testing.assert_allclose(read_touchstone(p).s, s, rtol=1e-9)

    def test_reads_touchstone_v2(self):
        v2 = "\n".join(
            [
                "[Version] 2.0",
                "# HZ S RI R 50",
                "[Number of Ports] 2",
                "[Two-Port Data Order] 12_21",
                "[Reference] 25 100",
                "[Network Data]",
                "1e6 0.1 0 0.2 0 0.3 0 0.4 0",
                "2e6 0.5 0 0.6 0 0.7 0 0.8 0",
                "[End]",
            ]
        )
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, "v2.s2p")
            with open(p, "w") as fh:
                fh.write(v2 + "\n")
            net = read_touchstone(p)
        self.assertEqual(net.nports, 2)
        np.testing.assert_allclose(net.z0, [25.0, 100.0])
        self.assertAlmostEqual(net.s[0, 0, 1].real, 0.2)  # 12_21 order, not transposed
        self.assertIsNone(net.uniform_z0())

    def test_missing_reference_impedance_is_flagged(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, "n.s1p")
            with open(p, "w") as fh:
                fh.write("# HZ S RI\n1e6 0.1 0.0\n")
            net = read_touchstone(p)
        self.assertFalse(net.z0_from_file)
        self.assertEqual(net.z0[0], 50.0)  # the spec's default, but flagged


class ParameterConversionTests(unittest.TestCase):
    def setUp(self):
        _ensure_fixtures()
        self.freq = np.logspace(3, 10, 60)
        self.z = np.linalg.inv(fx.nodal_y(self.freq))
        # |Z| here spans ~1e-6..1e0 ohm; round-off shows up in near-zero
        # real parts, so pair the relative tolerance with an absolute floor
        self.atol = 1e-12 * float(np.max(np.abs(self.z)))

    def test_s_to_z_inverts_z_to_s_uniform(self):
        z0 = np.full(3, 50.0)
        np.testing.assert_allclose(
            s_to_z(z_to_s(self.z, z0), z0), self.z, rtol=1e-9, atol=self.atol
        )

    def test_s_to_z_inverts_z_to_s_per_port(self):
        z0 = np.array([1.0, 50.0, 12.5])
        np.testing.assert_allclose(
            s_to_z(z_to_s(self.z, z0), z0), self.z, rtol=1e-9, atol=self.atol
        )

    def test_uniform_case_matches_the_closed_form(self):
        z0 = 50.0
        s = z_to_s(self.z, np.full(3, z0))
        eye = np.eye(3, dtype=complex)
        want = z0 * np.linalg.solve(eye - s, eye + s)
        np.testing.assert_allclose(
            s_to_z(s, np.full(3, z0)), want, rtol=1e-9, atol=self.atol
        )

    def test_renormalize_preserves_z(self):
        s50 = z_to_s(self.z, np.full(3, 50.0))
        s1 = renormalize(s50, np.full(3, 50.0), np.full(3, 1.0))
        np.testing.assert_allclose(
            s_to_z(s1, np.full(3, 1.0)), self.z, rtol=1e-9, atol=self.atol
        )


class ReferenceNodeTests(unittest.TestCase):
    """Cross-check the Z-domain transforms against a direct nodal solve."""

    def setUp(self):
        self.freq = np.logspace(4, 9, 40)
        self.y = fx.nodal_y(self.freq)
        self.z = np.linalg.inv(self.y)
        self.names = ["A", "B", "C"]

    def test_port_reference_matches_an_mna_solve(self):
        spec = cmp_mod.ReferenceSpec(mode=cmp_mod.REF_PORT, ports=[3])
        got, names = cmp_mod.apply_reference(self.z, self.names, spec)
        self.assertEqual(names, ["A", "B"])
        # drive 1 A into node k and out of node 3, read V_i - V_3
        want = np.zeros_like(got)
        for col, k in enumerate((0, 1)):
            inj = np.zeros(3, dtype=complex)
            inj[k], inj[2] = 1.0, -1.0
            v = np.linalg.solve(self.y, np.broadcast_to(inj, (self.freq.size, 3))[..., None])
            v = v[..., 0]
            want[:, 0, col] = v[:, 0] - v[:, 2]
            want[:, 1, col] = v[:, 1] - v[:, 2]
        np.testing.assert_allclose(got, want, rtol=1e-8)

    def test_short_matches_deleting_the_node_from_y(self):
        spec = cmp_mod.ReferenceSpec(mode=cmp_mod.REF_SHORT, ports=[3])
        got, names = cmp_mod.apply_reference(self.z, self.names, spec)
        self.assertEqual(names, ["A", "B"])
        # grounding node 3 removes its row and column from the nodal matrix
        want = np.linalg.inv(self.y[:, :2, :2])
        np.testing.assert_allclose(got, want, rtol=1e-8)

    def test_open_is_the_submatrix(self):
        spec = cmp_mod.ReferenceSpec(mode=cmp_mod.REF_OPEN, ports=[3])
        got, _ = cmp_mod.apply_reference(self.z, self.names, spec)
        np.testing.assert_allclose(got, self.z[:, :2, :2], rtol=1e-12)

    def test_global_is_a_no_op(self):
        got, names = cmp_mod.apply_reference(
            self.z, self.names, cmp_mod.ReferenceSpec()
        )
        np.testing.assert_allclose(got, self.z)
        self.assertEqual(names, self.names)

    def test_rejects_out_of_range_port(self):
        with self.assertRaises(cmp_mod.CompareError):
            cmp_mod.apply_reference(
                self.z, self.names, cmp_mod.ReferenceSpec(mode=cmp_mod.REF_SHORT, ports=[9])
            )


class AlignTests(unittest.TestCase):
    def _net(self, freq):
        n = freq.size
        s = np.zeros((n, 1, 1), dtype=complex)
        s[:, 0, 0] = np.linspace(0.1, 0.9, n)
        return Network(freq=freq, s=s, z0=np.full(1, 50.0), port_names=["P1"])

    def test_identical_grids_pass_through(self):
        f = np.array([1e6, 2e6, 3e6])
        freq, a, b, notes = cmp_mod.align(self._net(f), self._net(f))
        self.assertEqual(notes, [])
        np.testing.assert_allclose(freq, f)
        np.testing.assert_allclose(a, b)

    def test_intersect_keeps_common_points(self):
        ref = self._net(np.array([1e6, 2e6, 3e6, 4e6]))
        dut = self._net(np.array([2e6, 4e6]))
        freq, _, _, notes = cmp_mod.align(ref, dut, "intersect")
        np.testing.assert_allclose(freq, [2e6, 4e6])
        self.assertTrue(notes)

    def test_exact_mode_refuses_mismatched_grids(self):
        ref = self._net(np.array([1e6, 2e6]))
        dut = self._net(np.array([1e6, 3e6]))
        with self.assertRaises(cmp_mod.CompareError):
            cmp_mod.align(ref, dut, "exact")

    def test_interp_resamples_onto_the_reference_grid(self):
        ref = self._net(np.array([1e6, 2e6, 4e6]))
        dut = self._net(np.array([1e6, 4e6]))
        freq, _, s, notes = cmp_mod.align(ref, dut, "interp")
        self.assertEqual(freq.size, 3)
        self.assertTrue(any("interpolated" in n for n in notes))

    def test_port_count_mismatch_is_an_error(self):
        one = self._net(np.array([1e6]))
        two = Network(
            freq=np.array([1e6]),
            s=np.zeros((1, 2, 2), dtype=complex),
            z0=np.full(2, 50.0),
            port_names=["a", "b"],
        )
        with self.assertRaises(cmp_mod.CompareError):
            cmp_mod.align(one, two)


class CompareTests(unittest.TestCase):
    def setUp(self):
        _ensure_fixtures()
        self.ref = read_touchstone(os.path.join(DATA, "pdn3.s3p"))

    def test_a_file_compared_against_itself_passes_perfectly(self):
        res = cmp_mod.compare(self.ref, self.ref)
        self.assertEqual(res.status, cmp_mod.PASS)
        self.assertEqual(res.counts()[cmp_mod.PASS], len(res.terms))
        for t in res.terms:
            for b in t.bands:
                self.assertLess(b.norm_err_pct, 1e-9)
                self.assertLess(b.max_err_db, 1e-9)

    def test_upper_triangle_only_by_default(self):
        res = cmp_mod.compare(self.ref, self.ref)
        self.assertEqual([t.name for t in res.terms][:3], ["Z11", "Z12", "Z13"])
        self.assertEqual(len(res.terms), 6)  # 3 self + 3 mutual
        self.assertEqual(len(cmp_mod.compare(self.ref, self.ref,
                                             upper_triangle_only=False).terms), 9)

    def test_a_badly_fitted_model_fails(self):
        bad = read_touchstone(os.path.join(DATA, "pdn3_bad.s3p"))
        res = cmp_mod.compare(self.ref, bad)
        self.assertEqual(res.status, cmp_mod.FAIL)
        self.assertGreater(res.counts()[cmp_mod.FAIL], 0)

    def test_loose_criteria_accept_a_small_error(self):
        dut = read_touchstone(os.path.join(DATA, "pdn3_bbs.s3p"))
        strict = cmp_mod.compare(self.ref, dut, cmp_mod.Criteria(mag_err_pct=0.01,
                                                                err_db=0.001,
                                                                phase_err_deg=0.01))
        loose = cmp_mod.compare(self.ref, dut, cmp_mod.Criteria(mag_err_pct=100.0,
                                                               err_db=50.0,
                                                               phase_err_deg=180.0,
                                                               peak_shift_pct=100.0,
                                                               peak_mag_err_pct=100.0))
        self.assertEqual(strict.status, cmp_mod.FAIL)
        self.assertEqual(loose.status, cmp_mod.PASS)

    def test_reference_mode_reduces_the_port_count(self):
        res = cmp_mod.compare(
            self.ref,
            self.ref,
            reference=cmp_mod.ReferenceSpec(mode=cmp_mod.REF_PORT, ports=[3]),
        )
        self.assertEqual(res.nports, 2)
        self.assertEqual(res.port_names, ["VDD_CORE", "VDD_IO"])
        self.assertEqual([t.name for t in res.terms], ["Z11", "Z12", "Z22"])

    def test_renormalizes_a_dut_with_a_different_z0(self):
        dut = read_touchstone(os.path.join(DATA, "pdn3.s3p"))
        dut.s = renormalize(dut.s, dut.z0, np.full(3, 1.0))
        dut.z0 = np.full(3, 1.0)
        res = cmp_mod.compare(self.ref, dut)
        self.assertEqual(res.status, cmp_mod.PASS)
        self.assertTrue(any("renormalized" in n for n in res.notes))

    def test_finds_the_pdn_anti_resonances(self):
        res = cmp_mod.compare(self.ref, self.ref)
        self.assertTrue(res.term(1, 1).peaks, "Z11 should have at least one peak")
        for p in res.term(1, 1).peaks:
            self.assertEqual(p.status, cmp_mod.PASS)
            self.assertAlmostEqual(p.shift_pct, 0.0)


class NetlistTests(unittest.TestCase):
    def setUp(self):
        _ensure_fixtures()
        self.info = read_netlist(os.path.join(DATA, "pdn3_bbs.sp"))

    def test_finds_both_subckts(self):
        self.assertEqual({s.name for s in self.info.subckts}, {"rc_leg", "pdn3_bbs"})

    def test_top_is_the_one_nothing_instantiates(self):
        tops = self.info.top_candidates()
        self.assertEqual([t.name for t in tops], ["pdn3_bbs"])

    def test_pins_stop_before_parameters(self):
        self.assertEqual(self.info.by_name("rc_leg").pins, ["a", "b"])
        self.assertEqual(
            self.info.by_name("pdn3_bbs").pins,
            ["VDD_CORE", "VDD_IO", "VDD_PMIC", "GND"],
        )

    def test_continuation_lines_are_folded(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, "c.sp")
            with open(p, "w") as fh:
                fh.write(".subckt wide a b\n+ c d\n+ e\nR1 a b 1\n.ends\n")
            self.assertEqual(
                read_netlist(p).by_name("wide").pins, ["a", "b", "c", "d", "e"]
            )


class DeckTests(unittest.TestCase):
    def setUp(self):
        _ensure_fixtures()
        self.ref = read_touchstone(os.path.join(DATA, "pdn3.s3p"))
        self.sub = read_netlist(os.path.join(DATA, "pdn3_bbs.sp")).by_name("pdn3_bbs")

    def _cfg(self, tmp, **kw):
        return deck_mod.config_from_inputs(
            self.ref, self.sub, os.path.join(DATA, "pdn3_bbs.sp"), tmp, **kw
        )

    def test_default_mapping_ports_then_ground(self):
        a = deck_mod.default_assignments(self.sub, 3)
        self.assertEqual([x.role for x in a], ["port", "port", "port", "ground"])
        self.assertEqual([x.port for x in a[:3]], [1, 2, 3])
        self.assertEqual(a[3].net, "0")

    def test_deck_contents(self):
        with tempfile.TemporaryDirectory() as tmp:
            text = deck_mod.build_deck(self._cfg(tmp))
        self.assertIn("P1 VDD_CORE 0 port=1 z0=50", text)
        self.assertIn("P3 VDD_PMIC 0 port=3 z0=50", text)
        self.assertIn("XDUT VDD_CORE VDD_IO VDD_PMIC 0 pdn3_bbs", text)
        self.assertIn(".lin sparcalc=1 format=touchstone", text)
        self.assertIn(".ac poi 400", text)
        self.assertTrue(text.rstrip().endswith(".end"))

    def test_sweep_uses_every_reference_frequency(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = self._cfg(tmp)
            pts = deck_mod.sweep_points(cfg)
        np.testing.assert_allclose(pts, self.ref.freq)

    def test_zero_hz_is_dropped_and_noted(self):
        ref = read_touchstone(os.path.join(DATA, "pdn3.s3p"))
        ref.freq = np.concatenate([[0.0], ref.freq[1:]])
        with tempfile.TemporaryDirectory() as tmp:
            cfg = deck_mod.config_from_inputs(
                ref, self.sub, os.path.join(DATA, "pdn3_bbs.sp"), tmp
            )
            text = deck_mod.build_deck(cfg)
        self.assertEqual(deck_mod.sweep_points(cfg).size, ref.freq.size - 1)
        self.assertIn("0 Hz", text)

    def test_max_points_decimates(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = self._cfg(tmp, max_points=50)
            pts = deck_mod.sweep_points(cfg)
        self.assertLessEqual(pts.size, 50)
        self.assertAlmostEqual(pts[0], self.ref.freq[0])
        self.assertAlmostEqual(pts[-1], self.ref.freq[-1])

    def test_float_pin_gets_a_leak_resistor(self):
        a = deck_mod.default_assignments(self.sub, 3)
        a[3].role = deck_mod.ROLE_FLOAT
        with tempfile.TemporaryDirectory() as tmp:
            text = deck_mod.build_deck(self._cfg(tmp, assignments=a))
        self.assertIn("Rleak_GND GND 0 1e+09", text)

    def test_named_minus_node_is_honoured(self):
        a = deck_mod.default_assignments(self.sub, 3)
        a[3].role = deck_mod.ROLE_FLOAT
        a[0].minus = "GND"
        with tempfile.TemporaryDirectory() as tmp:
            text = deck_mod.build_deck(self._cfg(tmp, assignments=a))
        self.assertIn("P1 VDD_CORE GND port=1", text)

    def test_validate_reports_a_missing_port(self):
        """Grounding a port pin leaves that reference port undriven."""
        a = deck_mod.default_assignments(self.sub, 3)
        a[1].role = deck_mod.ROLE_GROUND
        with tempfile.TemporaryDirectory() as tmp:
            cfg = self._cfg(tmp, assignments=a)
            problems = deck_mod.validate(cfg)
            self.assertTrue(any("no pin assigned to port(s) [2]" in p for p in problems))
            # the message has to name the remedy, not just the symptom
            self.assertTrue(any("Reference node tab" in p for p in problems))
            with self.assertRaises(deck_mod.DeckError):
                deck_mod.build_deck(cfg)

    def test_validate_reports_a_duplicate_port(self):
        a = deck_mod.default_assignments(self.sub, 3)
        a[1].port = 1
        with tempfile.TemporaryDirectory() as tmp:
            self.assertTrue(deck_mod.validate(self._cfg(tmp, assignments=a)))

    def test_include_path_is_absolute_when_it_would_climb_out(self):
        with tempfile.TemporaryDirectory() as tmp:
            text = deck_mod.build_deck(self._cfg(tmp))
        inc = [ln for ln in text.splitlines() if ln.startswith(".inc")][0]
        self.assertNotIn("..", inc)
        self.assertIn(os.path.abspath(os.path.join(DATA, "pdn3_bbs.sp")), inc)

    def test_include_path_stays_relative_when_nearby(self):
        with tempfile.TemporaryDirectory() as tmp:
            bbs = os.path.join(tmp, "model.sp")
            with open(bbs, "w") as fh:
                fh.write(".subckt pdn3_bbs VDD_CORE VDD_IO VDD_PMIC GND\n.ends\n")
            cfg = deck_mod.config_from_inputs(self.ref, self.sub, bbs, tmp)
            text = deck_mod.build_deck(cfg)
        self.assertIn(".inc 'model.sp'", text)

    def test_expected_output_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = self._cfg(tmp)
            self.assertTrue(cfg.expected_snp().endswith("bbs_sparam.s3p"))

    def test_sanitize(self):
        self.assertEqual(deck_mod.sanitize("VDD/CORE[1]"), "VDD_CORE_1")
        self.assertEqual(deck_mod.sanitize("1net"), "n_1net")

    def test_continuation_lines_stay_short(self):
        with tempfile.TemporaryDirectory() as tmp:
            text = deck_mod.build_deck(self._cfg(tmp))
        for line in text.splitlines():
            self.assertLessEqual(len(line), 120, line)


class RunnerTests(unittest.TestCase):
    def test_command_substitution(self):
        spec = runner_mod.RunSpec(deck_path="/w/d.sp", cpu=8, cwd="/w")
        self.assertEqual(spec.argv(), ["primesim_sub", "-spice", "-cpu", "8", "-i", "d.sp"])

    def test_runs_a_real_process_and_streams_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            deck = os.path.join(tmp, "d.sp")
            with open(deck, "w") as fh:
                fh.write("* deck\n")
            lines: list[str] = []
            res = runner_mod.run(
                runner_mod.RunSpec(
                    deck_path=deck, command_template="echo ran {deck}", cwd=tmp
                ),
                on_line=lines.append,
            )
        self.assertTrue(res.ok)
        self.assertEqual(lines, ["ran d.sp"])

    def test_missing_binary_raises_a_clear_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            deck = os.path.join(tmp, "d.sp")
            with open(deck, "w") as fh:
                fh.write("* deck\n")
            with self.assertRaises(runner_mod.RunnerError):
                runner_mod.run(
                    runner_mod.RunSpec(
                        deck_path=deck, command_template="no_such_binary_xyz {deck}"
                    )
                )

    def test_finds_the_newest_matching_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            for name in ("old.s3p", "new.s3p"):
                with open(os.path.join(tmp, name), "w") as fh:
                    fh.write("# HZ S RI R 50\n")
            os.utime(os.path.join(tmp, "old.s3p"), (1e9, 1e9))
            found = runner_mod.find_output_snp(tmp, 3, "", newer_than=0.0)
        self.assertTrue(found.endswith("new.s3p"))

    def test_prefers_the_expected_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            want = os.path.join(tmp, "bbs_sparam.s3p")
            for name in ("other.s3p", "bbs_sparam.s3p"):
                with open(os.path.join(tmp, name), "w") as fh:
                    fh.write("x")
            self.assertEqual(runner_mod.find_output_snp(tmp, 3, want), want)

    def test_returns_empty_when_nothing_was_produced(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(runner_mod.find_output_snp(tmp, 3, ""), "")

    def test_error_scan(self):
        log = "ok\n**error** node VDD has no dc path\nfine\n"
        self.assertEqual(len(runner_mod.scan_log_for_errors(log)), 1)


class ReportTests(unittest.TestCase):
    def setUp(self):
        _ensure_fixtures()
        self.ref = read_touchstone(os.path.join(DATA, "pdn3.s3p"))
        self.dut = read_touchstone(os.path.join(DATA, "pdn3_bbs.s3p"))
        self.res = cmp_mod.compare(self.ref, self.dut)

    def test_xml_structure(self):
        tree = report_mod.build_xml(
            self.res, self.ref, self.dut, deck_path="/w/d.sp", command="primesim_sub ..."
        )
        root = tree.getroot()
        self.assertEqual(root.tag, "bbs_validation")
        self.assertEqual(root.get("status"), self.res.status)
        self.assertEqual(root.get("nports"), "3")
        self.assertEqual(len(root.findall("terms/term")), 6)
        self.assertEqual(root.find("meta/spice").get("deck"), "/w/d.sp")
        self.assertEqual(
            [p.get("name") for p in root.findall("meta/ports/port")],
            ["VDD_CORE", "VDD_IO", "VDD_PMIC"],
        )
        z11 = root.findall("terms/term")[0]
        self.assertEqual(z11.get("name"), "Z11")
        self.assertEqual(z11.get("kind"), "self")
        self.assertTrue(z11.findall("band"))
        self.assertTrue(z11.findall("resonance"))

    def test_summary_counts_match(self):
        tree = report_mod.build_xml(self.res, self.ref, self.dut)
        s = tree.getroot().find("summary")
        counts = self.res.counts()
        self.assertEqual(int(s.get("passed")), counts["PASS"])
        self.assertEqual(int(s.get("failed")), counts["FAIL"])
        self.assertEqual(int(s.get("terms")), len(self.res.terms))

    def test_written_file_is_valid_xml_with_a_stylesheet(self):
        import xml.etree.ElementTree as ET

        with tempfile.TemporaryDirectory() as tmp:
            p = report_mod.write_xml(
                report_mod.build_xml(self.res, self.ref, self.dut),
                os.path.join(tmp, "report.xml"),
            )
            with open(p) as fh:
                text = fh.read()
            ET.parse(p)
            self.assertIn("report.xsl", text)
            self.assertTrue(os.path.exists(os.path.join(tmp, "report.xsl")))

    def test_junit_marks_failures(self):
        import xml.etree.ElementTree as ET

        bad = read_touchstone(os.path.join(DATA, "pdn3_bad.s3p"))
        res = cmp_mod.compare(self.ref, bad)
        with tempfile.TemporaryDirectory() as tmp:
            p = report_mod.write_junit(res, os.path.join(tmp, "junit.xml"))
            root = ET.parse(p).getroot()
        self.assertEqual(root.tag, "testsuite")
        self.assertGreater(len(root.findall(".//failure")), 0)
        self.assertEqual(int(root.get("failures")), res.counts()["FAIL"])

    def test_non_finite_metrics_serialise(self):
        self.assertEqual(report_mod._n(float("inf")), "inf")
        self.assertEqual(report_mod._n(float("nan")), "nan")


class CliTests(unittest.TestCase):
    def test_end_to_end_without_a_simulator(self):
        _ensure_fixtures()
        with tempfile.TemporaryDirectory() as tmp:
            from sparabbs.cli import main

            rc = main(
                [
                    "--snp", os.path.join(DATA, "pdn3.s3p"),
                    "--bbs", os.path.join(DATA, "pdn3_bbs.sp"),
                    "--out", tmp,
                    "--ground-pins", "GND",
                    "--use-snp", os.path.join(DATA, "pdn3_bbs.s3p"),
                    "--mag-err-pct", "100", "--err-db", "50",
                    "--phase-err-deg", "180", "--peak-shift-pct", "100",
                    "--peak-mag-err-pct", "100",
                    "--junit", os.path.join(tmp, "junit.xml"),
                ]
            )
            self.assertEqual(rc, 0)
            self.assertTrue(os.path.exists(os.path.join(tmp, "report.xml")))
            self.assertTrue(os.path.exists(os.path.join(tmp, "junit.xml")))
            self.assertTrue(os.path.exists(os.path.join(tmp, "bbs2spara.sp")))

    def test_strict_criteria_return_a_nonzero_exit_code(self):
        _ensure_fixtures()
        with tempfile.TemporaryDirectory() as tmp:
            from sparabbs.cli import main

            rc = main(
                [
                    "--snp", os.path.join(DATA, "pdn3.s3p"),
                    "--bbs", os.path.join(DATA, "pdn3_bbs.sp"),
                    "--out", tmp,
                    "--ground-pins", "GND",
                    "--use-snp", os.path.join(DATA, "pdn3_bad.s3p"),
                ]
            )
        self.assertEqual(rc, 1)


if __name__ == "__main__":
    unittest.main()
