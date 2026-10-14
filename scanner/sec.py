"""SEC EDGAR client: ticker list, company submissions (filings + SIC), XBRL company facts.

All endpoints are free. SEC asks for <= 10 requests/second and a User-Agent with contact info.
Normalized fundamentals are cached and only re-fetched when a new periodic report is filed.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from .http import ThrottledClient
from .util import log, read_json, utc_now, write_json
from . import xbrl

TICKERS_URL = "https://www.sec.gov/files/company_tickers_exchange.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
ARCHIVE_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{acc_nodash}/{doc}"
INDEX_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{acc_nodash}/index.json"

PERIODIC_FORMS = {"10-K", "10-Q", "20-F", "40-F", "10-K/A", "10-Q/A", "20-F/A", "40-F/A", "10-KT"}

ITEM_8K = {
    "1.01": "Entry into a material definitive agreement",
    "1.02": "Termination of a material definitive agreement",
    "1.05": "Material cybersecurity incident",
    "2.01": "Completion of acquisition or disposition of assets",
    "2.02": "Results of operations (earnings release)",
    "2.03": "Creation of a direct financial obligation (new debt)",
    "2.05": "Exit or restructuring costs",
    "2.06": "Material impairments",
    "3.01": "Delisting notice / listing standards",
    "3.02": "Unregistered sales of equity securities (possible dilution)",
    "4.01": "Change in auditor",
    "4.02": "Non-reliance on previous financial statements",
    "5.02": "Director or officer change",
    "7.01": "Regulation FD disclosure",
    "8.01": "Other events",
}


def filing_url(cik: int, accession: str, doc: str) -> str:
    return ARCHIVE_URL.format(cik=int(cik), acc_nodash=accession.replace("-", ""), doc=doc)


class SecClient:
    def __init__(self, user_agent: str, cache_dir: str | Path):
        self.http = ThrottledClient(user_agent, max_per_second=8.0)
        self.cache = Path(cache_dir)
        (self.cache / "submissions").mkdir(parents=True, exist_ok=True)
        (self.cache / "fundamentals").mkdir(parents=True, exist_ok=True)

    # ---- listings -------------------------------------------------------------------------
    def company_tickers(self) -> List[Dict[str, Any]]:
        data = self.http.get_json(TICKERS_URL)
        if not data:
            raise RuntimeError("Could not download SEC ticker list")
        fields = data["fields"]
        return [dict(zip(fields, row)) for row in data["data"]]

    # ---- submissions ----------------------------------------------------------------------
    def submissions(self, cik: int, max_age_hours: float = 18.0) -> Optional[Dict[str, Any]]:
        path = self.cache / "submissions" / f"{int(cik)}.json"
        cached = read_json(path)
        if cached and (utc_now().timestamp() - cached.get("_fetched", 0)) < max_age_hours * 3600:
            return cached
        raw = self.http.get_json(SUBMISSIONS_URL.format(cik=int(cik)))
        if raw is None:
            return cached  # stale is better than nothing; caller checks freshness of data itself
        slim = slim_submissions(raw)
        slim["_fetched"] = utc_now().timestamp()
        write_json(path, slim, compact=True)
        return slim

    # ---- fundamentals ---------------------------------------------------------------------
    def fundamentals(self, cik: int, subs: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """Normalized fundamentals; re-downloads company facts only after a new periodic filing."""
        path = self.cache / "fundamentals" / f"{int(cik)}.json"
        cached = read_json(path)
        latest = latest_periodic(subs) if subs else None
        latest_acc = latest["accession"] if latest else None
        if cached and latest_acc and cached.get("_source_accession") == latest_acc:
            return cached
        raw = self.http.get_json(FACTS_URL.format(cik=int(cik)))
        if raw is None:
            return cached
        norm = xbrl.normalize(raw)
        norm["_source_accession"] = latest_acc
        norm["_fetched"] = utc_now().isoformat()
        write_json(path, norm, compact=True)
        return norm

    def filing_documents(self, cik: int, accession: str) -> List[Dict[str, Any]]:
        idx = self.http.get_json(INDEX_URL.format(cik=int(cik), acc_nodash=accession.replace("-", "")))
        if not idx:
            return []
        return idx.get("directory", {}).get("item", [])


def slim_submissions(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Keep only what we use: identity, SIC, and the last ~120 filings."""
    recent = raw.get("filings", {}).get("recent", {})
    n = min(120, len(recent.get("accessionNumber", [])))
    filings = []
    for i in range(n):
        filings.append({
            "accession": recent["accessionNumber"][i],
            "filed": recent["filingDate"][i],
            "report_date": (recent.get("reportDate") or [""] * n)[i],
            "form": recent["form"][i],
            "items": (recent.get("items") or [""] * n)[i],
            "doc": (recent.get("primaryDocument") or [""] * n)[i],
            "desc": (recent.get("primaryDocDescription") or [""] * n)[i],
        })
    return {
        "cik": int(raw.get("cik", 0)),
        "name": raw.get("name"),
        "sic": raw.get("sic"),
        "sic_desc": raw.get("sicDescription"),
        "tickers": raw.get("tickers", []),
        "exchanges": raw.get("exchanges", []),
        "fiscal_year_end": raw.get("fiscalYearEnd"),
        "filings": filings,
    }


def latest_periodic(subs: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    for f in subs.get("filings", []):
        if f.get("form") in PERIODIC_FORMS:
            return f
    return None


def recent_8k_events(subs: Dict[str, Any], since_days: int, today) -> List[Dict[str, Any]]:
    """8-K filings in the window, with item codes translated. Facts, not interpretations."""
    from .util import days_between

    out = []
    cik = subs.get("cik")
    for f in subs.get("filings", []):
        if not f.get("form", "").startswith("8-K"):
            continue
        age = days_between(f["filed"], today)
        if age is None or age > since_days or age < 0:
            continue
        codes = [c.strip() for c in (f.get("items") or "").split(",") if c.strip()]
        out.append({
            "filed": f["filed"],
            "form": f["form"],
            "items": codes,
            "labels": [ITEM_8K[c] for c in codes if c in ITEM_8K],
            "accession": f["accession"],
            "url": filing_url(cik, f["accession"], f["doc"]) if f.get("doc") else None,
        })
    return out
