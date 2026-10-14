"""Drive the live SEC adapter (submissions slimming, caching, universe build, fundamentals refresh logic)
with responses shaped exactly like SEC's JSON endpoints — no network needed."""
import shutil
import tempfile
import unittest
from pathlib import Path

from scanner.config import load_settings
from scanner.demo import DemoSource
from scanner.sec import SecClient, latest_periodic
from scanner.universe import build_universe

CFG = load_settings()


def raw_submissions(slim):
    f = slim["filings"]
    return {"cik": str(slim["cik"]), "name": slim["name"], "sic": slim["sic"], "sicDescription": "x",
            "tickers": slim["tickers"], "exchanges": slim["exchanges"], "fiscalYearEnd": "1231",
            "filings": {"recent": {"accessionNumber": [x["accession"] for x in f], "filingDate": [x["filed"] for x in f],
                                   "reportDate": [x["report_date"] for x in f], "form": [x["form"] for x in f],
                                   "items": [x["items"] for x in f], "primaryDocument": ["doc.htm" for _ in f],
                                   "primaryDocDescription": ["" for _ in f]}}}


class FakeHttp:
    def __init__(self, demo):
        self.demo, self.calls = demo, []
        self.requests_made = 0

    def get_json(self, url, retries=4):
        self.calls.append(url)
        if url.endswith("company_tickers_exchange.json"):
            rows = [[c.cik, c.s["n"], t, "Nasdaq"] for t, c in self.demo.companies.items()]
            rows.append([1234567, "Bank Corp", "BNKX", "NYSE"])
            return {"fields": ["cik", "name", "ticker", "exchange"], "data": rows}
        cik = int(url.split("CIK")[1][:10])
        if cik == 1234567:
            return {"cik": "1234567", "name": "Bank Corp", "sic": "6022", "filings": {"recent": {"accessionNumber": [], "filingDate": [], "form": []}}}
        c = self.demo.by_cik[cik]
        if "submissions" in url:
            return raw_submissions(c.submissions())
        return c.companyfacts()


class LiveAdapterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.demo = DemoSource()
        self.sec = SecClient("Test test@example.com", self.tmp / "cache")
        self.sec.http = FakeHttp(self.demo)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_universe_filters_by_sic_and_keeps_tech(self):
        u = build_universe(self.sec, CFG, str(self.tmp / "universe.json"), force=True)
        tickers = {c["ticker"] for c in u["companies"]}
        self.assertIn("DXQF", tickers)
        self.assertNotIn("BNKX", tickers)  # SIC 6022 (bank) excluded
        seg = {c["ticker"]: c["segment"] for c in u["companies"]}
        self.assertEqual(seg["DXLM"], "Semiconductors")

    def test_fundamentals_cached_until_new_filing(self):
        cik = self.demo.companies["DXQF"].cik
        subs = self.sec.submissions(cik)
        self.assertEqual(latest_periodic(subs)["form"], "10-Q")
        f1 = self.sec.fundamentals(cik, subs)
        n_calls = len(self.sec.http.calls)
        f2 = self.sec.fundamentals(cik, subs)  # same latest filing -> served from cache
        self.assertEqual(len(self.sec.http.calls), n_calls)
        self.assertEqual(f1["annual"]["revenue"], f2["annual"]["revenue"])
        subs["filings"].insert(0, {"accession": "NEW-1", "filed": "2026-11-05", "form": "10-Q", "items": "", "doc": "q.htm"})
        self.sec.fundamentals(cik, subs)  # new 10-Q -> re-download
        self.assertEqual(len(self.sec.http.calls), n_calls + 1)

    def test_submissions_cached_within_ttl(self):
        cik = self.demo.companies["DXQF"].cik
        self.sec.submissions(cik)
        n = len(self.sec.http.calls)
        self.sec.submissions(cik)
        self.assertEqual(len(self.sec.http.calls), n)


if __name__ == "__main__":
    unittest.main()
