"""End-to-end tests on the fictional demo universe (offline)."""
import json
import shutil
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from scanner import history, metrics, report, scoring
from scanner.config import load_settings
from scanner.demo import DEMO_TODAY, DemoSource
from scanner.pipeline import run

CFG = load_settings()
NOW = datetime(DEMO_TODAY.year, DEMO_TODAY.month, DEMO_TODAY.day, 21, 30, tzinfo=timezone.utc)


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def _run(self, src=None, now=NOW):
        return run(src or DemoSource(), CFG, self.tmp / "site", self.tmp / "data", force=True, now=now)

    def test_full_run_writes_dashboard_and_archive(self):
        res = self._run()
        site = self.tmp / "site" / "data"
        latest = json.loads((site / "latest.json").read_text())
        self.assertEqual(latest["date"], "2026-10-14")
        self.assertIn("generated_at", latest)
        self.assertTrue((self.tmp / "data" / "reports" / "2026-10-14.md").exists())
        self.assertTrue(any((site / "stocks").glob("*.json")))
        c = latest["counts"]
        self.assertEqual(c["universe"], 22)
        self.assertLessEqual(c["passed_initial_screen"], c["with_usable_data"])
        self.assertEqual(c["high_quality"], c["tier1"] + c["tier2"])
        # low-growth and small names are screened out
        tickers = {r["ticker"] for r in res["rows"]}
        self.assertNotIn("DXGR", tickers)
        self.assertNotIn("DXTR", tickers)

    def test_zero_recommendations_when_everything_is_expensive(self):
        res = self._run(DemoSource(price_multiplier=3.0))
        self.assertFalse(any(r["tier"] == "Tier 1" for r in res["rows"]))
        self.assertEqual(res["report"]["headline"], report.NO_NEW)

    def test_fragile_and_deteriorating_names_are_avoided(self):
        res = self._run()
        tiers = {r["ticker"]: r for r in res["rows"]}
        self.assertEqual(tiers["DXBK"]["tier"], "Avoid")              # heavy debt, thin margins
        self.assertIn("weak_balance_sheet", tiers["DXBK"]["high_flags"])
        self.assertIn("price_up_fundamentals_down", tiers["DXOR"]["high_flags"])  # price up, growth down
        self.assertNotIn(tiers["DXNV"]["tier"], ("Tier 1", "Tier 2"))  # cash burn + dilution + rich multiple

    def test_no_invented_estimates(self):
        src = DemoSource()
        src.market.estimates = lambda t: {"source": "none"}  # provider returns nothing
        res = self._run(src)
        for r in res["rows"]:
            self.assertIsNone(r["consensus_growth_ntm"])
            self.assertNotEqual(r["tier"], "Tier 1")  # Tier 1 requires consensus data

    def test_history_is_append_only_and_changes_are_reported(self):
        self._run()
        log1 = history.load(self.tmp / "data" / "history" / "recommendations.jsonl")
        self._run()  # same-day rerun must not duplicate or overwrite
        log2 = history.load(self.tmp / "data" / "history" / "recommendations.jsonl")
        self.assertEqual(len(log1), len(log2))
        nxt = NOW.replace(day=15)
        res = self._run(DemoSource(price_multiplier=0.8), now=nxt)
        ch = res["report"]["changes"]
        self.assertFalse(ch["first_report"])
        self.assertEqual(ch["previous_date"], "2026-10-14")

    def test_score_components_sum_and_caps(self):
        res = self._run()
        t = res["rows"][0]["ticker"]
        d = json.loads((self.tmp / "site" / "data" / "stocks" / f"{t}.json").read_text())
        s = d["score"]
        self.assertAlmostEqual(sum(c["points"] for c in s["components"].values()), s["raw"], places=1)
        self.assertEqual(sum(c["max"] for c in s["components"].values()), 100)


class MetricTests(unittest.TestCase):
    def test_split_adjustment_skips_restated_counts(self):
        splits = {"2024-06-10": 10.0}
        self.assertEqual(metrics.split_factor(splits, "2024-03-31", "2024-05-01", "2026-01-01"), 10.0)
        self.assertEqual(metrics.split_factor(splits, "2024-03-31", "2024-08-01", "2026-01-01"), 1.0)

    def test_missing_inputs_get_partial_credit_not_full(self):
        m = {"data_quality": {"usable": True}}
        s = scoring.score(m, {}, {"flags": {}, "high_flags": []}, [], CFG)
        self.assertLess(s["total"], 35)
        self.assertNotIn(s["tier"], ("Tier 1", "Tier 2"))


if __name__ == "__main__":
    unittest.main()
