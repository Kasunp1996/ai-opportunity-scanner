import copy
import unittest

from scanner import valuation
from scanner.config import load_settings

CFG = load_settings()


def base_metrics(**over):
    m = {"ticker": "TST", "revenue_ttm": 1e9, "operating_margin": 0.20, "gross_margin": 0.60, "fcf_margin": 0.18,
         "fcf_after_sbc_margin": 0.15, "sbc_pct": 0.03, "tax_rate_model": 0.20, "sales_to_capital": 2.0,
         "net_cash": 1e8, "shares_for_valuation": 1e8, "price": 30.0, "market_cap": 3e9, "debt": 0.0,
         "enterprise_value": 2.9e9, "beta": 1.2, "consensus_growth_ntm": 0.20, "revenue_growth_ttm": 0.22,
         "share_growth_yoy": 0.01, "data_quality": {"usable": True}, "segment": "Software & Cloud"}
    m.update(over)
    return m


class DcfTests(unittest.TestCase):
    def test_zero_growth_constant_margin_matches_perpetuity(self):
        cfg = copy.deepcopy(CFG)
        cfg["valuation"]["terminal_growth"] = 0.0
        m = base_metrics(net_cash=0.0)
        mt = valuation.margin_targets(m, cfg)
        # force the margin target equal to the current margin so the business is static
        r = valuation.dcf(m, cfg, 0.10, 0.0, mt["current"])
        nopat = 1e9 * 0.20 * (1 - 0.20)
        self.assertAlmostEqual(r["enterprise_value"], nopat / 0.10, delta=nopat * 1e-6)
        self.assertAlmostEqual(r["per_share"], nopat / 0.10 / 1e8, places=4)

    def test_net_cash_and_shares_flow_into_per_share(self):
        m1 = base_metrics(net_cash=0.0)
        m2 = base_metrics(net_cash=5e8)
        mt = valuation.margin_targets(m1, CFG)
        a = valuation.dcf(m1, CFG, 0.10, 0.1, mt["base"])
        b = valuation.dcf(m2, CFG, 0.10, 0.1, mt["base"])
        self.assertAlmostEqual(b["per_share"] - a["per_share"], 5e8 / 1e8, places=6)
        m3 = base_metrics(net_cash=0.0, shares_for_valuation=2e8)
        c = valuation.dcf(m3, CFG, 0.10, 0.1, mt["base"])
        self.assertAlmostEqual(c["per_share"], a["per_share"] / 2, places=6)

    def test_reverse_dcf_recovers_growth(self):
        m = base_metrics()
        mt = valuation.margin_targets(m, CFG)
        target = valuation.dcf(m, CFG, 0.10, 0.17, mt["base"])["per_share"]
        m["price"] = target
        r = valuation.reverse_dcf(m, CFG, 0.10, mt["base"])
        self.assertAlmostEqual(r["implied_growth"], 0.17, places=3)


class ScenarioTests(unittest.TestCase):
    def test_targets_reconcile_with_multiple_metric_cash_and_shares(self):
        m = base_metrics()
        sc = valuation.scenarios(m, CFG)
        base = sc["cases"]["base"]
        metric = base["ebit_y3"] if sc["kind"] == "ev_ebit" else base["revenue_y3"]
        implied = (base["exit_multiple_24m"] * metric + base["net_cash_24m"]) / base["shares_24m"]
        self.assertAlmostEqual(implied, base["price_24m"], places=6)
        self.assertAlmostEqual(base["shares_24m"], 1e8 * 1.01 ** 2, places=0)

    def test_bear_below_base_below_bull(self):
        sc = valuation.scenarios(base_metrics(), CFG)
        c = sc["cases"]
        self.assertLess(c["bear"]["price_24m"], c["base"]["price_24m"])
        self.assertLess(c["base"]["price_24m"], c["bull"]["price_24m"])

    def test_expected_price_is_probability_weighted(self):
        sc = valuation.scenarios(base_metrics(), CFG)
        p = CFG["valuation"]["scenario_probabilities"]
        exp = sum(p[s] * sc["cases"][s]["price_24m"] for s in ("bear", "base", "bull"))
        self.assertAlmostEqual(exp, sc["expected_price_24m"], places=6)

    def test_base_multiple_moves_only_partway(self):
        sc = valuation.scenarios(base_metrics(), CFG)
        cur, just = sc["current_multiple"], sc["cases"]["base"]["justified_multiple_24m"]
        self.assertAlmostEqual(sc["cases"]["base"]["exit_multiple_24m"], 0.5 * cur + 0.5 * just, places=6)

    def test_expensive_stock_needs_more_than_consensus_to_double(self):
        m = base_metrics(price=150.0, market_cap=1.5e10, enterprise_value=1.49e10)
        sc = valuation.scenarios(m, CFG)
        p2 = valuation.path_to_double(m, CFG, sc)
        self.assertFalse(p2["potential_double"])

    def test_cheap_stock_with_credible_growth_can_double(self):
        m = base_metrics(price=10.0, market_cap=1e9, enterprise_value=0.9e9)
        sc = valuation.scenarios(m, CFG)
        p2 = valuation.path_to_double(m, CFG, sc)
        self.assertTrue(p2["potential_double"])

    def test_cycle_peak_is_not_projected_as_permanent(self):
        """Memory-style peak: 250% growth, 55% margin vs a 10% through-cycle average."""
        m = base_metrics(cyclical_signal=True, operating_margin=0.55, gross_margin=0.70, op_margin_mean_5y=0.10,
                         op_margin_min_5y=-0.10, consensus_growth_ntm=0.60, revenue_growth_ttm=2.5)
        mt = valuation.margin_targets(m, CFG)
        self.assertLessEqual(mt["base"], 0.325)
        self.assertEqual(mt["bear"], -0.10)
        path = valuation.growth_path(0.60, 0.03, 10)
        self.assertLess(path[3], 0.30)  # excess growth decays quickly
        self.assertAlmostEqual(path[-1], 0.03, delta=0.02)

    def test_cyclical_bear_revisits_trough_margin(self):
        m = base_metrics(cyclical_signal=True, op_margin_min_5y=-0.05)
        mt = valuation.margin_targets(m, CFG)
        self.assertEqual(mt["bear"], -0.05)


if __name__ == "__main__":
    unittest.main()
