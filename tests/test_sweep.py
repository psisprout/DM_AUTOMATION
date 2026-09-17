"""Tests for the scalar objective and the model search."""

from __future__ import annotations

import csv
import os
import sys
import tempfile
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sparabbs import compare as cmp_mod  # noqa: E402
from sparabbs import sweep as sweep_mod  # noqa: E402
from sparabbs.touchstone import Network, read_touchstone, s_to_z, z_to_s  # noqa: E402
from tests import make_fixtures as fx  # noqa: E402

DATA = fx.DATA

try:
    import skrf  # noqa: F401

    HAVE_SKRF = True
except ImportError:  # pragma: no cover - depends on the environment
    HAVE_SKRF = False


def _ensure():
    if not os.path.exists(os.path.join(DATA, "pdn3.s3p")):
        fx.main()


class ObjectiveTests(unittest.TestCase):
    def setUp(self):
        _ensure()
        self.ref = read_touchstone(os.path.join(DATA, "pdn3.s3p"))

    def test_identical_networks_leave_full_margin(self):
        res = cmp_mod.compare(self.ref, self.ref)
        self.assertAlmostEqual(res.headroom(), 0.0, places=6)
        self.assertAlmostEqual(res.margin(), 1.0, places=6)

    def test_margin_is_one_minus_headroom(self):
        res = cmp_mod.compare(self.ref, read_touchstone(os.path.join(DATA, "pdn3_bbs.s3p")))
        self.assertAlmostEqual(res.margin(), 1.0 - res.headroom())

    def test_a_failing_model_has_negative_margin(self):
        res = cmp_mod.compare(self.ref, read_touchstone(os.path.join(DATA, "pdn3_bad.s3p")))
        self.assertEqual(res.status, cmp_mod.FAIL)
        self.assertGreater(res.headroom(), 1.0)
        self.assertLess(res.margin(), 0.0)

    def test_headroom_tracks_the_status_boundaries(self):
        dut = read_touchstone(os.path.join(DATA, "pdn3_bbs.s3p"))
        for limit, wanted in ((1000.0, cmp_mod.PASS), (0.001, cmp_mod.FAIL)):
            res = cmp_mod.compare(
                self.ref,
                dut,
                cmp_mod.Criteria(mag_err_pct=limit, err_db=limit, phase_err_deg=limit,
                                 peak_shift_pct=limit, peak_mag_err_pct=limit),
            )
            self.assertEqual(res.status, wanted)
            self.assertEqual(res.headroom() > 1.0, wanted == cmp_mod.FAIL)

    def test_headroom_is_linear_in_a_uniform_error(self):
        """A search needs a monotone objective, not a step function."""
        z = s_to_z(self.ref.s, self.ref.z0)
        seen = []
        for scale in (1.01, 1.02, 1.04, 1.08):
            dut = Network(
                freq=self.ref.freq,
                s=z_to_s(z * scale, self.ref.z0),
                z0=self.ref.z0.copy(),
                port_names=list(self.ref.port_names),
            )
            seen.append(cmp_mod.compare(self.ref, dut).headroom())
        self.assertEqual(seen, sorted(seen), "headroom must rise with the error")
        # 5 % is the default limit, so a 1 % error sits at 0.2 of it
        for scale, got in zip((1.01, 1.02, 1.04, 1.08), seen):
            self.assertAlmostEqual(got, (scale - 1.0) / 0.05, places=3)

    def test_headroom_ranks_two_passing_models(self):
        """PASS/FAIL cannot order two good models; the scalar has to."""
        good = cmp_mod.compare(self.ref, self.ref)
        worse = cmp_mod.compare(self.ref, read_touchstone(os.path.join(DATA, "pdn3_bbs.s3p")))
        self.assertLess(good.headroom(), worse.headroom())


class NoiseFloorTests(unittest.TestCase):
    """A decoupled port pair sits at round-off and must not drive the verdict."""

    def _pair(self, coupling: float, error: float):
        f = np.logspace(3, 9, 80)
        n = 2
        z = np.zeros((f.size, n, n), dtype=complex)
        z[:, 0, 0] = 1e-2 + 1j * f / 1e11
        z[:, 1, 1] = 2e-2 + 1j * f / 1e11
        z[:, 0, 1] = z[:, 1, 0] = coupling
        z0 = np.full(n, 50.0)
        ref = Network(freq=f, s=z_to_s(z, z0), z0=z0, port_names=["A", "B"])
        zd = z.copy()
        zd[:, 0, 1] = zd[:, 1, 0] = coupling + error
        dut = Network(freq=f, s=z_to_s(zd, z0), z0=z0, port_names=["A", "B"])
        return ref, dut

    def test_round_off_coupling_does_not_fail_the_run(self):
        ref, dut = self._pair(coupling=1e-19, error=1e-18)
        res = cmp_mod.compare(ref, dut)
        self.assertEqual(res.status, cmp_mod.PASS)
        self.assertTrue(any(b.negligible for t in res.terms for b in t.bands))

    def test_a_negligible_band_is_excluded_from_headroom(self):
        ref, dut = self._pair(coupling=1e-19, error=1e-18)
        res = cmp_mod.compare(ref, dut)
        self.assertLessEqual(res.headroom(), 1.0)

    def test_real_coupling_is_still_judged(self):
        ref, dut = self._pair(coupling=1e-3, error=1e-3)  # 100% off, and it matters
        res = cmp_mod.compare(ref, dut)
        self.assertEqual(res.status, cmp_mod.FAIL)

    def test_a_mutual_error_is_scaled_by_the_ports_it_couples(self):
        """The same absolute error matters next to small rails, not large ones."""
        err = 2e-4
        small = cmp_mod.compare(*self._pair(coupling=1e-3, error=err))
        big_ref, big_dut = self._pair(coupling=1e-3, error=err)
        for net in (big_ref, big_dut):  # lift the self impedances by 100x
            z = s_to_z(net.s, net.z0)
            z[:, 0, 0] *= 100.0
            z[:, 1, 1] *= 100.0
            net.s = z_to_s(z, net.z0)
        big = cmp_mod.compare(big_ref, big_dut)
        self.assertGreater(small.headroom(), big.headroom())


class SweepPlumbingTests(unittest.TestCase):
    def setUp(self):
        _ensure()
        self.ref = read_touchstone(os.path.join(DATA, "pdn3.s3p"))

    def test_parse_range(self):
        self.assertEqual(sweep_mod.parse_range("2:10:2"), [2, 4, 6, 8, 10])
        self.assertEqual(sweep_mod.parse_range("3:5"), [3, 4, 5])
        self.assertEqual(sweep_mod.parse_range("4,8,16"), [4, 8, 16])
        self.assertEqual(sweep_mod.parse_range(" 7 "), [7])

    def test_parse_range_rejects_nonsense(self):
        for bad in ("1:2:3:4", "2:10:0", "abc"):
            with self.assertRaises(ValueError):
                sweep_mod.parse_range(bad)

    def _fake(self, specs):
        """specs: list of (size, scale) - scale 1.0 is a perfect model."""
        ref = self.ref

        class Fake(sweep_mod.Generator):
            name = "fake"

            def candidates(self, ref_in):
                for size, scale in specs:
                    if scale is None:
                        yield sweep_mod.Candidate(
                            label=f"fake:{size}", params={}, size=size, error="boom"
                        )
                        continue
                    z = s_to_z(ref.s, ref.z0) * scale
                    yield sweep_mod.Candidate(
                        label=f"fake:{size}",
                        params={"size": size},
                        size=size,
                        network=Network(
                            freq=ref.freq, s=z_to_s(z, ref.z0), z0=ref.z0.copy(),
                            port_names=list(ref.port_names),
                        ),
                        extra={"scale": scale},
                    )

        return Fake()

    def test_run_sweep_scores_every_candidate(self):
        sw = sweep_mod.run_sweep(self.ref, self._fake([(10, 1.30), (20, 1.04), (30, 1.0)]))
        self.assertEqual([t.candidate.size for t in sw.trials], [10, 20, 30])
        self.assertEqual([t.status for t in sw.trials],
                         [cmp_mod.FAIL, cmp_mod.WARN, cmp_mod.PASS])

    def test_best_is_the_smallest_that_passes(self):
        sw = sweep_mod.run_sweep(self.ref, self._fake([(10, 1.30), (20, 1.0), (30, 1.0)]))
        self.assertEqual(sw.best().candidate.size, 20)

    def test_best_can_accept_a_warning(self):
        # nothing reaches PASS here, so the stricter default finds no winner
        sw = sweep_mod.run_sweep(self.ref, self._fake([(10, 1.30), (20, 1.04)]))
        self.assertEqual([t.status for t in sw.trials], [cmp_mod.FAIL, cmp_mod.WARN])
        self.assertIsNone(sw.best())
        self.assertEqual(sw.best(require=cmp_mod.WARN).candidate.size, 20)

    def test_most_accurate_is_not_always_the_best(self):
        sw = sweep_mod.run_sweep(self.ref, self._fake([(10, 1.0005), (40, 1.0)]))
        self.assertEqual(sw.best().candidate.size, 10, "smallest passing model wins")
        self.assertEqual(sw.most_accurate().candidate.size, 40)

    def test_stop_early_ends_at_the_first_pass(self):
        sw = sweep_mod.run_sweep(
            self.ref, self._fake([(10, 1.30), (20, 1.0), (30, 1.0)]), stop_when_passing=True
        )
        self.assertEqual(len(sw.trials), 2)

    def test_a_generator_failure_is_recorded_not_raised(self):
        sw = sweep_mod.run_sweep(self.ref, self._fake([(10, None), (20, 1.0)]))
        self.assertEqual(len(sw.trials), 2)
        self.assertFalse(sw.trials[0].ok)
        self.assertEqual(sw.trials[0].status, cmp_mod.FAIL)
        self.assertEqual(sw.best().candidate.size, 20)

    def test_pareto_drops_dominated_points(self):
        sw = sweep_mod.run_sweep(
            self.ref, self._fake([(10, 1.30), (20, 1.10), (30, 1.30)])
        )
        sizes = [t.candidate.size for t in sw.pareto()]
        self.assertIn(10, sizes)
        self.assertIn(20, sizes)
        self.assertNotIn(30, sizes, "bigger and no better is dominated")

    def test_on_trial_callback_streams(self):
        seen = []
        sweep_mod.run_sweep(self.ref, self._fake([(10, 1.0), (20, 1.0)]), on_trial=seen.append)
        self.assertEqual(len(seen), 2)

    def test_csv_history(self):
        sw = sweep_mod.run_sweep(self.ref, self._fake([(10, 1.30), (20, 1.0)]))
        with tempfile.TemporaryDirectory() as tmp:
            path = sw.to_csv(os.path.join(tmp, "sweep.csv"))
            with open(path, newline="") as fh:
                rows = list(csv.DictReader(fh))
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["size"], "10")
        self.assertEqual(rows[1]["status"], cmp_mod.PASS)
        self.assertIn("headroom", rows[0])
        self.assertIn("x_scale", rows[0])

    def test_csv_refuses_an_empty_sweep(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                sweep_mod.Sweep().to_csv(os.path.join(tmp, "x.csv"))

    def test_nde_backend_says_what_is_missing(self):
        with self.assertRaises(sweep_mod.GeneratorUnavailable) as caught:
            list(sweep_mod.NdeGenerator().candidates(self.ref))
        self.assertIn("script recording", str(caught.exception).lower())


@unittest.skipUnless(HAVE_SKRF, "scikit-rf is not installed")
class VectorFitTests(unittest.TestCase):
    def setUp(self):
        _ensure()
        self.ref = read_touchstone(os.path.join(DATA, "pdn3.s3p"))

    def test_vector_fitting_is_found_whatever_the_layout(self):
        """1.x exports VectorFitting at the top level, 2.x does not."""
        import skrf

        cls = sweep_mod.VectorFitGenerator._vector_fitting(skrf)
        self.assertEqual(cls.__name__, "VectorFitting")
        self.assertTrue(hasattr(cls, "vector_fit"))
        self.assertTrue(hasattr(cls, "write_spice_subcircuit_s"))

    def test_a_layout_with_neither_location_is_reported(self):
        class Bare:
            __version__ = "9.9.9"

        with self.assertRaises(sweep_mod.GeneratorUnavailable):
            sweep_mod.VectorFitGenerator._vector_fitting(Bare())

    def test_fits_and_scores(self):
        gen = sweep_mod.VectorFitGenerator(orders=[6, 12], n_real=2)
        sw = sweep_mod.run_sweep(self.ref, gen)
        self.assertEqual(len(sw.trials), 2)
        for t in sw.trials:
            self.assertTrue(t.ok, t.candidate.error)
            self.assertEqual(t.candidate.size, t.candidate.params["n_cmplx"] * 2 + 2)
            self.assertIn("rms", t.candidate.extra)

    def test_writes_a_netlist_sparabbs_can_drive(self):
        from sparabbs import deck as deck_mod
        from sparabbs.netlist import read_netlist

        with tempfile.TemporaryDirectory() as tmp:
            gen = sweep_mod.VectorFitGenerator(orders=[8], out_dir=tmp)
            cand = next(iter(gen.candidates(self.ref)))
            self.assertTrue(os.path.exists(cand.netlist))
            sub = read_netlist(cand.netlist).top_candidates()[0]
            self.assertEqual(sub.npins, self.ref.nports)
            cfg = deck_mod.config_from_inputs(self.ref, sub, cand.netlist, tmp)
            self.assertEqual(deck_mod.validate(cfg), [])

    def test_cli_runs_end_to_end(self):
        with tempfile.TemporaryDirectory() as tmp:
            rc = sweep_mod.main([
                "--snp", os.path.join(DATA, "pdn3.s3p"),
                "--orders", "6,10",
                "--out", tmp,
                "--mag-err-pct", "100", "--err-db", "50",
                "--phase-err-deg", "180", "--peak-shift-pct", "100",
                "--peak-mag-err-pct", "100",
            ])
            self.assertEqual(rc, 0)
            self.assertTrue(os.path.exists(os.path.join(tmp, "sweep.csv")))

    def test_cli_reports_when_nothing_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            rc = sweep_mod.main([
                "--snp", os.path.join(DATA, "pdn3.s3p"),
                "--orders", "4", "--out", tmp, "--mag-err-pct", "1e-9",
            ])
        self.assertEqual(rc, 1)

    def test_cli_reports_an_unavailable_backend(self):
        with tempfile.TemporaryDirectory() as tmp:
            rc = sweep_mod.main(
                ["--snp", os.path.join(DATA, "pdn3.s3p"), "--backend", "nde", "--out", tmp]
            )
        self.assertEqual(rc, 2)


if __name__ == "__main__":
    unittest.main()


class CsvAppenderTests(unittest.TestCase):
    def test_rows_are_readable_before_the_sweep_ends(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "s.csv")
            app = sweep_mod.CsvAppender(path)
            app.add({"index": 0, "size": 10, "status": "FAIL"})
            with open(path, newline="") as fh:  # still open for writing
                rows = list(csv.DictReader(fh))
            self.assertEqual(rows, [{"index": "0", "size": "10", "status": "FAIL"}])
            app.add({"index": 1, "size": 20, "status": "PASS"})
            with open(path, newline="") as fh:
                self.assertEqual(len(list(csv.DictReader(fh))), 2)
            app.close()

    def test_a_later_row_with_new_keys_does_not_break_the_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "s.csv")
            app = sweep_mod.CsvAppender(path)
            app.add({"index": 0, "size": 10})
            app.add({"index": 1, "size": 20, "x_rms": "1e-9"})
            app.close()
            with open(path, newline="") as fh:
                rows = list(csv.DictReader(fh))
        self.assertEqual(len(rows), 2)
        self.assertNotIn("x_rms", rows[1], "the final to_csv carries the full union")

    def test_close_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = sweep_mod.CsvAppender(os.path.join(tmp, "s.csv"))
            app.close()
            app.close()
