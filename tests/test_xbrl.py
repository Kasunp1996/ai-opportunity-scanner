import unittest

from scanner import xbrl


def fact(start, end, val, form="10-Q", filed="2026-01-01"):
    f = {"end": end, "val": val, "form": form, "filed": filed}
    if start:
        f["start"] = start
    return f


def raw(concepts):
    facts = {"us-gaap": {}, "dei": {}}
    for (tax, name, unit), arr in concepts.items():
        facts.setdefault(tax, {})[name] = {"units": {unit: arr}}
    return {"cik": 1, "entityName": "Test Co", "facts": facts}


class XbrlTests(unittest.TestCase):
    def test_ytd_cash_flow_is_differenced_into_quarters(self):
        cfo = [fact("2025-01-01", "2025-03-31", 10), fact("2025-01-01", "2025-06-30", 25),
               fact("2025-01-01", "2025-09-30", 45), fact("2025-01-01", "2025-12-31", 70, form="10-K")]
        n = xbrl.normalize(raw({("us-gaap", "NetCashProvidedByUsedInOperatingActivities", "USD"): cfo}))
        q = {r["end"]: r["val"] for r in n["quarterly"]["cfo"]}
        self.assertEqual(q, {"2025-03-31": 10, "2025-06-30": 15, "2025-09-30": 20, "2025-12-31": 25})
        self.assertTrue(n["quarterly"]["cfo"][-1]["derived"])
        self.assertEqual(n["annual"]["cfo"][-1]["val"], 70)

    def test_direct_quarter_preferred_and_q4_derived(self):
        rev = [fact("2025-01-01", "2025-03-31", 100), fact("2025-04-01", "2025-06-30", 110),
               fact("2025-01-01", "2025-06-30", 210), fact("2025-07-01", "2025-09-30", 120),
               fact("2025-01-01", "2025-09-30", 330), fact("2025-01-01", "2025-12-31", 460, form="10-K")]
        n = xbrl.normalize(raw({("us-gaap", "Revenues", "USD"): rev}))
        q = {r["end"]: (r["val"], r.get("derived", False)) for r in n["quarterly"]["revenue"]}
        self.assertEqual(q["2025-06-30"], (110, False))
        self.assertEqual(q["2025-12-31"], (130, True))

    def test_restatement_latest_filing_wins(self):
        rev = [fact("2025-01-01", "2025-03-31", 100, filed="2025-05-01"),
               fact("2025-01-01", "2025-03-31", 95, filed="2026-05-01")]
        n = xbrl.normalize(raw({("us-gaap", "Revenues", "USD"): rev}))
        self.assertEqual(n["quarterly"]["revenue"][0]["val"], 95)

    def test_concept_priority_and_gap_filling(self):
        old = [fact("2021-01-01", "2021-12-31", 50, form="10-K")]
        new = [fact("2022-01-01", "2022-12-31", 60, form="10-K"), fact("2021-01-01", "2021-12-31", 51, form="10-K")]
        n = xbrl.normalize(raw({("us-gaap", "SalesRevenueNet", "USD"): old,
                                ("us-gaap", "RevenueFromContractWithCustomerExcludingAssessedTax", "USD"): new}))
        vals = {r["end"]: r["val"] for r in n["annual"]["revenue"]}
        self.assertEqual(vals["2021-12-31"], 51)  # higher-priority concept wins for the same period
        self.assertEqual(vals["2022-12-31"], 60)

    def test_non_usd_currency_detected(self):
        rev = [fact("2025-01-01", "2025-12-31", 900, form="20-F")]
        r = raw({("ifrs-full", "Revenue", "EUR"): rev})
        n = xbrl.normalize(r)
        self.assertEqual(n["currency"], "EUR")
        self.assertEqual(n["annual"]["revenue"][0]["val"], 900)

    def test_8k_and_other_forms_ignored(self):
        rev = [fact("2025-01-01", "2025-03-31", 100, form="8-K")]
        n = xbrl.normalize(raw({("us-gaap", "Revenues", "USD"): rev}))
        self.assertNotIn("revenue", n["quarterly"])


if __name__ == "__main__":
    unittest.main()
