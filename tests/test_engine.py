"""Engine tests: parsing, terminations, metrics, scenarios, selection, export."""

from __future__ import annotations

import csv
import json
import os
import re
import sys
import tempfile
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from chanxt import export, touchstone  # noqa: E402
from chanxt.analysis import Analysis, Key, label  # noqa: E402
from chanxt.cli import main as cli_main  # noqa: E402
from chanxt.project import (  # noqa: E402
    NAME_RULES,
    DirRule,
    ModelSpec,
    Project,
    Scenario,
    TermRule,
    apply_lp5x_preset,
    auto_name_rule,
    parse_names,
    positional_locations,
    positional_nets,
)
from chanxt.select import (  # noqa: E402
    FilterSpec,
    breakdown,
    default_spec,
    matrix,
    run_filter,
)
from chanxt.terminate import load_gamma, reduce_power  # noqa: E402
from chanxt.touchstone import Network, read_touchstone, write_touchstone, y_to_s  # noqa: E402
from chanxt.units import name_matcher, natural_key, parse_freq_list, parse_si  # noqa: E402
from chanxt.workflow import add_model  # noqa: E402
from tests import make_fixtures as fx  # noqa: E402


def project_for(nets: dict, preset: bool = True, terms=None) -> Project:
    p = Project()
    for alias, net in nets.items():
        add_model(p, net, alias)
    if preset:
        apply_lp5x_preset(p, "SoC", "DRAM", gbps=8.0)
    if terms is not None:
        p.terminations = list(terms)
    return p


class UnitsTests(unittest.TestCase):
    def test_si_values(self):
        self.assertEqual(parse_si("40"), 40.0)
        self.assertAlmostEqual(parse_si("1p"), 1e-12)
        self.assertAlmostEqual(parse_si("2.2nH"), 2.2e-9)
        self.assertAlmostEqual(parse_si("1meg"), 1e6)
        self.assertAlmostEqual(parse_si("3m"), 3e-3)
        with self.assertRaises(ValueError):
            parse_si("abc")

    def test_frequency_lists(self):
        self.assertEqual(parse_freq_list("2.5, 5 GHz; 800MHz"), [2.5e9, 5e9, 8e8])
        self.assertEqual(parse_freq_list(""), [])
        with self.assertRaises(ValueError):
            parse_freq_list("5 parsecs")

    def test_natural_sort_and_globs(self):
        self.assertEqual(sorted(["DQ10", "DQ2", "DQ1"], key=natural_key), ["DQ1", "DQ2", "DQ10"])
        m = name_matcher("DQ1, RDQS*")
        self.assertTrue(m("dq1") and m("RDQS0_t"))
        self.assertFalse(m("DQ10"))
        self.assertTrue(name_matcher("re:^DQ\\d$")("DQ7"))
        self.assertTrue(name_matcher("")("anything"))


class TouchstoneTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def _net(self, n, npts=5):
        rng = np.random.default_rng(n)
        s = (rng.normal(size=(npts, n, n)) + 1j * rng.normal(size=(npts, n, n))) * 0.1
        return Network(np.linspace(1e8, 1e9, npts), s, np.full(n, 50.0),
                       [f"N{i}_SoC" for i in range(n)])

    def test_round_trip_keeps_values_and_names(self):
        for n, fmt in ((2, "RI"), (3, "MA"), (6, "DB")):
            net = self._net(n)
            path = write_touchstone(net, os.path.join(self.tmp.name, f"x.s{n}p"), fmt)
            back = read_touchstone(path)
            np.testing.assert_allclose(back.s, net.s, rtol=1e-9, atol=1e-12)
            self.assertEqual(back.port_names, net.port_names)
            self.assertTrue(back.names_from_file)

    def test_chunked_parsing_matches(self):
        net = self._net(5, npts=40)
        path = write_touchstone(net, os.path.join(self.tmp.name, "y.s5p"))
        old = touchstone._CHUNK_LINES
        touchstone._CHUNK_LINES = 7  # force many chunks with records split across them
        try:
            back = read_touchstone(path)
        finally:
            touchstone._CHUNK_LINES = old
        np.testing.assert_allclose(back.s, net.s, rtol=1e-9, atol=1e-12)

    def test_unnamed_ports_and_bad_data(self):
        net = self._net(3)
        path = write_touchstone(net, os.path.join(self.tmp.name, "z.s3p"), names=False)
        back = read_touchstone(path)
        self.assertEqual(back.port_names, ["P1", "P2", "P3"])
        self.assertFalse(back.names_from_file)
        with open(path) as fh:
            text = fh.read()
        bad = os.path.join(self.tmp.name, "bad.s3p")
        with open(bad, "w") as fh:
            fh.write(text.replace(text.splitlines()[-1].split()[0], "oops", 1))
        with self.assertRaisesRegex(touchstone.TouchstoneError, "oops"):
            read_touchstone(bad)

    def test_y_to_s_of_a_series_resistor(self):
        # 50 ohm in series between two 50 ohm ports: S21 = 2/3, S11 = 1/3
        g = 1 / 50.0
        y = np.array([[[g, -g], [-g, g]]], dtype=complex)
        s = y_to_s(y, np.array([50.0, 50.0]))
        np.testing.assert_allclose(s[0], [[1 / 3, 2 / 3], [2 / 3, 1 / 3]], atol=1e-12)


class TerminationTests(unittest.TestCase):
    """Reduced networks against an independent nodal (Y-matrix) solve."""

    @classmethod
    def setUpClass(cls):
        cls.net, cls.yp = fx.channel(1.0, nets=["DQ0", "DQ1", "DQ2"], power="VDDQ", nc=None)
        cls.names = cls.net.port_names

    def _reference(self, keep, loads):
        """|S|^2 of the kept ports with ``loads`` = {port: admittance(F,) or 'short'}."""
        y = self.yp.copy()
        shorted = [p for p, v in loads.items() if isinstance(v, str)]
        for p, v in loads.items():
            if not isinstance(v, str):
                y[:, p, p] += v
        live = [i for i in range(y.shape[-1]) if i not in shorted]
        y = y[:, live][:, :, live]
        keep_live = [live.index(k) for k in keep]
        yr = fx.kron(y, keep_live)
        s = y_to_s(yr, np.full(len(keep), fx.Z0))
        return np.abs(s) ** 2

    def _ours(self, keep, loads_terms):
        loaded = list(loads_terms)
        gam = np.stack([load_gamma(t, self.net.freq, fx.Z0) for t in loads_terms.values()], axis=1) \
            if loaded else np.zeros((self.net.npoints, 0))
        return reduce_power(self.net.s, keep, loaded, gam)

    def test_short_open_r_c_and_rc(self):
        n = self.net.nports
        w = 2 * np.pi * self.net.freq
        power = [i for i, nm in enumerate(self.names) if nm.startswith("VDDQ")]
        dq2 = [i for i, nm in enumerate(self.names) if nm.startswith("DQ2")]
        keep = [i for i in range(n) if i not in power + dq2]
        cases = [
            ({p: "short" for p in power}, {p: TermRule(type="short") for p in power}),
            ({p: np.zeros_like(w, dtype=complex) for p in power},
             {p: TermRule(type="open") for p in power}),
            ({p: np.full_like(w, 1 / 40.0, dtype=complex) for p in power},
             {p: TermRule(type="r", r=40.0) for p in power}),
            ({p: 1j * w * 2e-12 for p in power}, {p: TermRule(type="c", c=2e-12) for p in power}),
            ({p: 1 / (10 + 1 / (1j * w * 5e-12)) for p in power},
             {p: TermRule(type="rc_series", r=10, c=5e-12) for p in power}),
            ({p: 1 / 30.0 + 1j * w * 1e-12 for p in power},
             {p: TermRule(type="rc_parallel", r=30, c=1e-12) for p in power}),
        ]
        for ref_loads, terms in cases:
            # DQ2 stays at the reference: a 50 ohm load in the nodal picture
            ref_loads = dict(ref_loads)
            ref_loads.update({p: np.full_like(w, 1 / fx.Z0, dtype=complex) for p in dq2})
            ours = self._ours(keep, terms)
            ref = self._reference(keep, ref_loads)
            np.testing.assert_allclose(ours, ref, rtol=2e-4, atol=1e-9,
                                       err_msg=str(next(iter(terms.values())).type))

    def test_inductor_and_nothing_loaded(self):
        n = self.net.nports
        w = 2 * np.pi * self.net.freq
        keep = list(range(1, n))
        ours = self._ours(keep, {0: TermRule(type="l", l=1e-9)})
        ref = self._reference(keep, {0: 1 / (1j * w * 1e-9)})
        np.testing.assert_allclose(ours, ref, rtol=2e-4, atol=1e-9)
        plain = self._ours(list(range(n)), {})
        np.testing.assert_allclose(plain, np.abs(self.net.s) ** 2, rtol=1e-6)

    def test_open_capacitor_at_dc(self):
        g = load_gamma(TermRule(type="c", c=1e-12), np.array([0.0, 1e9]), 50.0)
        self.assertAlmostEqual(g[0], 1.0)
        g = load_gamma(TermRule(type="short"), np.array([1e9]), 50.0)
        self.assertAlmostEqual(g[0], -1.0)


class NamingTests(unittest.TestCase):
    def test_auto_rule_and_alias(self):
        names = [f"DQ{i}_SoC" for i in range(4)] + [f"DQ{i}_DRAM" for i in range(4)]
        self.assertEqual(auto_name_rule(names), "NET_LOC")
        names = [f"U1_DQ{i}" for i in range(4)] + [f"U2_DQ{i}" for i in range(4)]
        self.assertEqual(auto_name_rule(names), "LOC_NET")
        maps = parse_names(names, NAME_RULES["LOC_NET"], {"u1": "SoC", "U2": "DRAM"})
        self.assertEqual((maps[0].net, maps[0].loc), ("DQ0", "SoC"))
        self.assertEqual((maps[5].net, maps[5].loc), ("DQ1", "DRAM"))
        self.assertEqual(auto_name_rule([f"P{i}" for i in range(1, 9)]), "NET")

    def test_positional_helpers(self):
        self.assertEqual(positional_locations(4, "halves", "A", "B"), ["A", "A", "B", "B"])
        self.assertEqual(positional_locations(4, "alternate", "A", "B"), ["A", "B", "A", "B"])
        self.assertEqual(positional_nets(4, "halves", ["x", "y"]), ["x", "y", "x", "y"])
        self.assertEqual(positional_nets(4, "alternate", ["x", "y"]), ["x", "x", "y", "y"])
        with self.assertRaises(ValueError):
            positional_nets(4, "halves", ["x", "y", "z"])

    def test_groups_tags_and_display_order(self):
        p = Project()
        for net, group in (("DQ12", "DQ"), ("RDQS0_t", "RDQS"), ("WCK1_c", "WCK"), ("CK_t", "CK"),
                           ("DMI0", "DM"), ("CA3", "CA"), ("CS1", "CS"), ("VDDQ", "POWER"),
                           ("VDD_CA1", "POWER"), ("NC5", "UNUSED"), ("FOO", "OTHER")):
            self.assertEqual(p.rule_group(net), group, net)
        self.assertEqual(p.rule_tag("DQ3"), "Byte0")
        self.assertEqual(p.rule_tag("DQ12"), "Byte1")
        self.assertEqual(p.rule_tag("RDQS1_c"), "Byte1")
        p.nets["DQ12"] = {"group": "SPECIAL"}
        self.assertEqual(p.group_of("DQ12"), "SPECIAL")


class AnalysisTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.net, _ = fx.channel(1.0)
        cls.p = project_for({"A": cls.net})
        cls.a = Analysis(cls.p, {"A": cls.net}).build()
        cls.idx = {nm: i for i, nm in enumerate(cls.net.port_names)}

    def test_power_and_nc_are_terminated_not_analysed(self):
        nets = {k.victim for k in self.a.keys("A")}
        self.assertNotIn("VDDQ", nets)
        self.assertNotIn("NC1", nets)
        self.assertIn("DQ0", nets)

    def test_basic_items_index_the_right_entries(self):
        # With POWER shorted and NC open the network differs from the raw file,
        # so rebuild without loads to compare against raw S directly.
        p = project_for({"A": self.net}, terms=[])
        a = Analysis(p, {"A": self.net}).build()
        s2 = np.abs(self.net.s) ** 2
        i = self.idx

        def db(x):
            return 10 * np.log10(x)

        checks = [
            (Key("", "IL", "DRAM", "DQ3", "", "SoC"), s2[:, i["DQ3_DRAM"], i["DQ3_SoC"]]),
            (Key("", "RL", "SoC", "DQ3"), s2[:, i["DQ3_SoC"], i["DQ3_SoC"]]),
            (Key("", "NEXT", "SoC", "DQ3", "DQ2"), s2[:, i["DQ3_SoC"], i["DQ2_SoC"]]),
            (Key("", "FEXT", "DRAM", "DQ3", "DQ2", "SoC"), s2[:, i["DQ3_DRAM"], i["DQ2_SoC"]]),
            (Key("", "FEXT", "SoC", "DQ3", "DQ4", "DRAM"), s2[:, i["DQ3_SoC"], i["DQ4_DRAM"]]),
        ]
        for key, ref in checks:
            np.testing.assert_allclose(a.curve("A", key), db(ref), atol=1e-4, err_msg=str(key))
        others = [n for n in fx.LP5_NETS + ["VDDQ"] if n != "DQ3"]
        ps = sum(s2[:, i["DQ3_SoC"], i[f"{n}_SoC"]] for n in others)
        np.testing.assert_allclose(a.curve("A", Key("", "PSNEXT", "SoC", "DQ3")), db(ps), atol=1e-4)
        ps = sum(s2[:, i["DQ3_DRAM"], i[f"{n}_SoC"]] for n in others)
        np.testing.assert_allclose(a.curve("A", Key("", "PSFEXT", "DRAM", "DQ3", "", "SoC")),
                                   db(ps), atol=1e-4)

    def test_scenarios_classify_next_and_fext_by_driver_location(self):
        a = self.a
        # WRITE: DQ3 received at DRAM; controller-driven groups are FEXT, RDQS is NEXT
        w = {k.aggr: a.xtype(k) for k in a.slot("A", "WRITE", "XT", "DRAM", "DQ3")}
        self.assertEqual(w["DQ2"], "FEXT")
        self.assertEqual(w["CA0"], "FEXT")
        self.assertEqual(w["WCK0_t"], "FEXT")
        self.assertEqual(w["RDQS0_t"], "NEXT")
        # READ: DQ3 received at SoC; DRAM-driven DQ/RDQS are FEXT, CA/CK/WCK are NEXT
        r = {k.aggr: a.xtype(k) for k in a.slot("A", "READ", "XT", "SoC", "DQ3")}
        self.assertEqual(r["DQ2"], "FEXT")
        self.assertEqual(r["RDQS0_c"], "FEXT")
        self.assertEqual(r["CA1"], "NEXT")
        self.assertEqual(r["CK_t"], "NEXT")
        self.assertEqual(r["WCK0_c"], "NEXT")
        self.assertEqual(a.slot("A", "WRITE", "XT", "SoC", "DQ3"), [])

    def test_scenario_sums_are_consistent(self):
        a = self.a

        def p(key):
            return 10 ** (a.curve("A", key) / 10)

        xts = a.slot("A", "WRITE", "XT", "DRAM", "DQ3")
        total = sum(p(k) for k in xts)
        # atol: curves are floored at -300 dB, so sums of floored terms differ there
        tol = dict(rtol=1e-5, atol=1e-25)
        np.testing.assert_allclose(p(Key("WRITE", "PSXT", "DRAM", "DQ3")), total, **tol)
        nxt = sum(p(k) for k in xts if a.xtype(k) == "NEXT")
        np.testing.assert_allclose(p(Key("WRITE", "PSXT_TYPE", "DRAM", "DQ3", "NEXT")), nxt, **tol)
        grp = sum(p(k) for k in xts if self.p.group_of(k.aggr) == "CA")
        np.testing.assert_allclose(p(Key("WRITE", "PSXT_GRP", "DRAM", "DQ3", "CA")), grp, **tol)
        # no scenario overrides: the scenario IL is the basic IL
        np.testing.assert_allclose(a.curve("A", Key("WRITE", "IL", "DRAM", "DQ3", "", "SoC")),
                                   a.curve("A", Key("", "IL", "DRAM", "DQ3", "", "SoC")), atol=1e-6)

    def test_value_at_interpolates_the_db_curve(self):
        key = Key("", "IL", "DRAM", "DQ3", "", "SoC")
        f = self.net.freq
        c = self.a.curve("A", key)
        mid = (f[10] + f[11]) / 2
        self.assertAlmostEqual(self.a.value_at("A", key, mid), (c[10] + c[11]) / 2, places=6)
        self.assertAlmostEqual(self.a.value_at("A", key, f[10]), c[10], places=6)
        self.assertTrue(np.isnan(self.a.value_at("A", key, 1e12)))
        self.assertAlmostEqual(self.a.nyquist_hz(key), 4e9)
        self.assertAlmostEqual(self.a.nyquist_hz(Key("", "RL", "SoC", "CA0")), 1e9)

    def test_aggressor_scope(self):
        p = project_for({"A": self.net})
        p.aggr_groups = ["DQ"]
        a = Analysis(p, {"A": self.net}).build()
        aggr = {k.aggr for k in a.slot("A", "", "NEXT", "SoC", "DQ3")}
        self.assertEqual(aggr, {f"DQ{i}" for i in range(8)} - {"DQ3"})
        p.aggr_groups = []
        p.aggr_tag_mode = "same_tag"
        a = Analysis(p, {"A": self.net}).build()
        aggr = {k.aggr for k in a.slot("A", "", "NEXT", "SoC", "DQ3")}
        self.assertNotIn("CA0", aggr)
        self.assertIn("RDQS0_t", aggr)

    def test_labels(self):
        self.assertEqual(label(Key("", "FEXT", "DRAM", "DQ3", "DQ2", "SoC")), "FEXT@DRAM | DQ3 ← DQ2@SoC")
        self.assertEqual(label(Key("WRITE", "PSXT_TYPE", "DRAM", "DQ3", "NEXT")), "WRITE PSNEXT@DRAM | DQ3")
        self.assertEqual(self.a.label(Key("WRITE", "XT", "DRAM", "DQ3", "RDQS0_t", "DRAM")),
                         "WRITE XT@DRAM | DQ3 ← RDQS0_t (NEXT)")


class MultiDropTests(unittest.TestCase):
    """DQ0..2 go SoC -> DRAM0 and DRAM1; a scenario writes to rank 0 only."""

    @classmethod
    def setUpClass(cls):
        cls.net, cls.yp = fx.channel(1.0, branch=["DQ0", "DQ1", "DQ2"], power=None, nc=None,
                                     nets=["DQ0", "DQ1", "DQ2"])
        p = Project()
        add_model(p, cls.net, "md")
        p.locations = ["SoC", "DRAM0", "DRAM1"]
        p.scenarios = [
            Scenario("WR_BOTH", [DirRule("group", "DQ", "SoC", ["DRAM0", "DRAM1"])]),
            Scenario("WR_R0", [DirRule("group", "DQ", "SoC", ["DRAM0"])],
                     [TermRule("group", "DQ", "DRAM1", "open")]),
        ]
        cls.p = p
        cls.a = Analysis(p, {"md": cls.net}).build()

    def test_basic_items_cover_every_drop(self):
        keys = set(self.a.keys("md"))
        self.assertIn(Key("", "IL", "DRAM0", "DQ1", "", "SoC"), keys)
        self.assertIn(Key("", "IL", "DRAM1", "DQ1", "", "DRAM0"), keys)
        self.assertIn(Key("", "PSFEXT", "DRAM1", "DQ1", "", "SoC"), keys)
        self.assertIn(Key("", "PSFEXT", "DRAM1", "DQ1", "", "DRAM0"), keys)
        self.assertEqual(self.a.label(Key("", "PSFEXT", "DRAM1", "DQ1", "", "SoC")),
                         "PSFEXT@DRAM1←SoC | DQ1")

    def test_several_out_ports(self):
        keys = set(self.a.keys("md"))
        for drop in ("DRAM0", "DRAM1"):
            self.assertIn(Key("WR_BOTH", "PSXT", drop, "DQ1"), keys)
            self.assertIn(Key("WR_BOTH", "IL", drop, "DQ1", "", "SoC"), keys)

    def test_idle_rank_is_opened_in_its_scenario(self):
        keys = set(self.a.keys("md"))
        self.assertNotIn(Key("WR_R0", "PSXT", "DRAM1", "DQ1"), keys)
        il_both = self.a.curve("md", Key("WR_BOTH", "IL", "DRAM0", "DQ1", "", "SoC"))
        il_r0 = self.a.curve("md", Key("WR_R0", "IL", "DRAM0", "DQ1", "", "SoC"))
        self.assertGreater(np.max(np.abs(il_both - il_r0)), 0.1)
        # independent check: open the DRAM1 ports in the nodal picture
        names = self.net.port_names
        drop = [i for i, nm in enumerate(names) if nm.endswith("_DRAM1")]
        keep = [i for i in range(len(names)) if i not in drop]
        yr = fx.kron(self.yp, keep)
        s = y_to_s(yr, np.full(len(keep), fx.Z0))
        kn = [names[i] for i in keep]
        ref = 20 * np.log10(np.abs(s[:, kn.index("DQ1_DRAM0"), kn.index("DQ1_SoC")]))
        np.testing.assert_allclose(il_r0, ref, atol=1e-3)


class ProjectFileTests(unittest.TestCase):
    def test_json_round_trip(self):
        net, _ = fx.channel(1.0, nets=["DQ0", "DQ1"], power=None, nc=None)
        with tempfile.TemporaryDirectory() as tmp:
            snp = write_touchstone(net, os.path.join(tmp, "m.s4p"))
            p = project_for({"m": read_touchstone(snp)})
            p.nets["DQ1"] = {"group": "X", "tag": "T"}
            p.scenarios[0].terminations.append(TermRule("net", "DQ*", "DRAM", "rc_parallel", r=40, c=1e-12))
            p.presets["mine"] = FilterSpec(kinds=["IL"]).to_dict()
            path = os.path.join(tmp, "proj.json")
            p.save(path)
            with open(path) as fh:
                raw = json.load(fh)
            self.assertEqual(raw["models"][0]["path"], "m.s4p")  # stored relative
            q = Project.load(path)
            self.assertEqual(q.to_dict(), p.to_dict())
            self.assertEqual(q.models[0].path, os.path.join(tmp, "m.s4p"))
            t = q.resolve_term("DQ1", "DRAM", q.scenarios[0])
            self.assertEqual((t.type, t.r, t.c), ("rc_parallel", 40.0, 1e-12))

    def test_termination_precedence(self):
        p = Project()
        p.terminations = [TermRule("group", "DQ", "*", "short"), TermRule("net", "DQ1", "*", "open"),
                          TermRule("group", "DQ", "DRAM", "r", r=40)]
        s = Scenario("S", terminations=[TermRule("group", "DQ", "SoC", "c", c=1e-12)])
        self.assertEqual(p.resolve_term("DQ0", "SoC").type, "short")
        self.assertEqual(p.resolve_term("DQ0", "DRAM").type, "r")
        self.assertEqual(p.resolve_term("DQ1", "DRAM").type, "open")  # net beats group
        self.assertEqual(p.resolve_term("DQ1", "SoC", s).type, "c")  # scenario beats global
        self.assertEqual(p.resolve_term("CA0", "SoC").type, "signal")
        self.assertEqual(p.resolve_term("", "SoC").type, "z0")


class SelectTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        a_net, _ = fx.channel(1.0)
        b_net, _ = fx.channel(0.6)
        cls.p = project_for({"revA": a_net, "revB": b_net})
        cls.a = Analysis(cls.p, {"revA": a_net, "revB": b_net}).build()

    def _spec(self, **kw):
        spec = default_spec(self.a)
        for k, v in kw.items():
            setattr(spec, k, v)
        return spec

    def test_default_has_no_per_aggressor_curves(self):
        keys = run_filter(self.a, default_spec(self.a))
        self.assertTrue(keys)
        self.assertFalse(any(k.kind in ("NEXT", "FEXT", "XT") for k in keys))

    def test_worst_k_threshold_and_patterns(self):
        spec = self._spec(kinds=["NEXT"], locs=["SoC"], v_pattern="DQ3", a_mode="topk", a_k=2)
        keys = run_filter(self.a, spec)
        self.assertEqual(len(keys), 2)
        self.assertEqual({k.aggr for k in keys}, {"DQ2", "DQ4"})
        spec.a_mode = "all"
        n_all = len(run_filter(self.a, spec))
        spec.a_mode, spec.a_threshold = "threshold", -45.0
        thr = run_filter(self.a, spec)
        self.assertTrue(0 < len(thr) < n_all)
        spec.a_mode = "none"
        self.assertEqual(run_filter(self.a, spec), [])
        spec.a_mode, spec.a_pattern = "all", "CA*"
        self.assertTrue(all(k.aggr.startswith("CA") for k in run_filter(self.a, spec)))

    def test_manual_add_and_remove(self):
        spec = self._spec(kinds=["IL"], v_pattern="DQ0")
        keys = run_filter(self.a, spec)
        self.assertEqual(len(keys), 3)  # basic + WRITE + READ
        extra = Key("", "FEXT", "DRAM", "DQ0", "DQ1", "SoC")
        spec.add_key(extra)
        spec.remove_key(keys[0])
        out = run_filter(self.a, spec)
        self.assertIn(extra, out)
        self.assertNotIn(keys[0], out)
        again = FilterSpec.from_dict(json.loads(json.dumps(spec.to_dict())))
        self.assertEqual(run_filter(self.a, again), out)

    def test_matrix_and_breakdown(self):
        mx = matrix(self.a, "revA", "", "NEXT", "SoC")
        self.assertEqual(len(mx.victims), len(fx.LP5_NETS))
        r, c = mx.victims.index("DQ3"), mx.aggressors.index("DQ2")
        self.assertEqual(mx.keys[r][c], Key("", "NEXT", "SoC", "DQ3", "DQ2"))
        self.assertIsNone(mx.keys[r][mx.aggressors.index("DQ3")])
        rows, total, f = breakdown(self.a, "revA", "WRITE", "DQ3", "DRAM")
        self.assertAlmostEqual(sum(x.share for x in rows), 1.0, places=9)
        self.assertAlmostEqual(rows[-1].cumulative, 1.0, places=9)
        self.assertEqual({rows[0].name, rows[1].name}, {"DQ2", "DQ4"})
        self.assertAlmostEqual(f, 4e9)
        psxt = self.a.value_at_nyquist("revA", Key("WRITE", "PSXT", "DRAM", "DQ3"))
        self.assertAlmostEqual(total, psxt, delta=0.05)
        grouped, gtotal, _ = breakdown(self.a, "revA", "WRITE", "DQ3", "DRAM", by_group=True)
        self.assertAlmostEqual(gtotal, total, places=9)
        self.assertEqual(grouped[0].name, "DQ")
        self.assertEqual(sum(g.members for g in grouped), len(rows))


class ExportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        a_net, _ = fx.channel(1.0)
        b_net, _ = fx.channel(0.6)
        cls.paths = [write_touchstone(a_net, os.path.join(cls.tmp.name, "revA.s41p")),
                     write_touchstone(b_net, os.path.join(cls.tmp.name, "revB.s41p"))]
        p = project_for({"revA": read_touchstone(cls.paths[0]), "revB": read_touchstone(cls.paths[1])})
        cls.p = p
        cls.a = Analysis(p, {m.alias: read_touchstone(m.path) for m in p.models}).build()
        cls.keys = run_filter(cls.a, FilterSpec(
            models=["revA", "revB"], scens=["", "WRITE"], kinds=["IL", "PSXT"], locs=["SoC", "DRAM"],
            v_groups=["DQ"], v_tags=["Byte0"], v_pattern="DQ[0-1]", a_groups=["DQ"]))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_extract_and_csv(self):
        header, rows = export.extract_table(self.a, self.keys, ["revA", "revB"], [2e9, 4e9, 1e12])
        self.assertEqual(len(rows), 2 * len(self.keys))
        self.assertEqual(header[-3:], ["dB@2GHz", "dB@4GHz", "dB@1000GHz"])
        path = export.write_csv(os.path.join(self.tmp.name, "v.csv"), header, rows)
        with open(path, encoding="utf-8-sig") as fh:
            back = list(csv.reader(fh))
        self.assertEqual(back[0], header)
        self.assertEqual(back[1][-1], "")  # out of range -> blank
        nyq = header.index("dB@nyquist")
        self.assertEqual(float(back[1][nyq]), round(self.a.value_at_nyquist(back[1][0], self.keys[0]), 3))

    def test_html_contains_data_and_plotly(self):
        path = export.write_html(os.path.join(self.tmp.name, "r.html"), self.a, self.keys,
                                 ["revA", "revB"], ["metric", "model"], title="T <1>")
        with open(path, encoding="utf-8") as fh:
            page = fh.read()
        self.assertIn("<title>T &lt;1&gt;</title>", page)
        self.assertIn("plotly.js v2", page)
        m = re.search(r'<script type="application/json" id="data">(.*?)</script>', page, re.S)
        data = json.loads(m.group(1))
        self.assertEqual([x["alias"] for x in data["models"]], ["revA", "revB"])
        self.assertEqual(len(data["panels"]), 6)  # (IL, WRITE IL, WRITE PSXT) x 2 models
        traces = sum(len(p["traces"]) for p in data["panels"])
        self.assertEqual(traces, 2 * len(self.keys))
        first = data["panels"][0]["traces"][0]
        self.assertEqual(len(first["y"]), len(data["models"][0]["freq"]))

    def test_cli(self):
        proj = os.path.join(self.tmp.name, "p.json")
        self.p.presets["dq"] = FilterSpec(
            models=["revA", "revB"], scens=["WRITE"], kinds=["PSXT", "XT"], locs=["DRAM"],
            v_groups=["DQ"], v_tags=["Byte0"], v_pattern="DQ3", a_groups=["DQ", "RDQS"],
            a_mode="topk", a_k=2).to_dict()
        self.p.save(proj)
        html = os.path.join(self.tmp.name, "cli.html")
        out = os.path.join(self.tmp.name, "cli.csv")
        import contextlib
        import io

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = cli_main([proj, "--preset", "dq", "--html", html, "--csv", out,
                             "--freqs", "1, 2 GHz", "--split", "metric", "--list"])
        self.assertEqual(code, 0, buf.getvalue())
        self.assertIn("3 curve(s) selected", buf.getvalue())
        self.assertTrue(os.path.getsize(html) > 1_000_000)
        with open(out, encoding="utf-8-sig") as fh:
            self.assertEqual(len(list(csv.reader(fh))), 1 + 3 * 2)
        with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli_main([proj, "--preset", "nope"]), 2)


if __name__ == "__main__":
    unittest.main()
