"""Build the investable universe: every Nasdaq/NYSE company in technology-related SIC codes,
plus an always-include list (e.g. foreign filers like TSM/ASML) with theme overrides."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from .config import load_extra_universe
from .util import days_between, log, read_json, utc_now, write_json


def segment_for_sic(sic: Optional[int]) -> str:
    if sic is None:
        return "Other Technology"
    if sic == 3674:
        return "Semiconductors"
    if sic == 3559:
        return "Semiconductor Equipment"
    if sic in (3576, 3661, 3663, 3669):
        return "Networking & Optical"
    if 3570 <= sic <= 3579:
        return "Compute & Storage Hardware"
    if sic in (3585, 3612, 3613) or 3620 <= sic <= 3629 or 3670 <= sic <= 3679:
        return "Power, Cooling & Components"
    if 3820 <= sic <= 3829:
        return "Instruments & Test"
    if 7370 <= sic <= 7379:
        return "Software & Cloud"
    return "Other Technology"


def in_ranges(sic: Optional[int], ranges: List[List[int]]) -> bool:
    return sic is not None and any(lo <= sic <= hi for lo, hi in ranges)


def build_universe(sec, cfg: Dict[str, Any], path: str, force: bool = False) -> Dict[str, Any]:
    existing = read_json(path)
    ucfg = cfg["universe"]
    if existing and not force:
        age = days_between(existing.get("built", "")[:10], utc_now().date())
        if age is not None and age < ucfg.get("refresh_days", 7):
            return existing

    extras = {r["ticker"].upper(): r for r in load_extra_universe(cfg)}
    rows = sec.company_tickers()
    seen_cik = set()
    candidates = []
    for r in rows:
        t = str(r.get("ticker", "")).upper()
        if r.get("exchange") not in ucfg["exchanges"] and t not in extras:
            continue
        if r["cik"] in seen_cik and t not in extras:
            continue  # keep one share class per company
        seen_cik.add(r["cik"])
        candidates.append(r)

    log.info("universe: checking SIC codes for %d listed companies", len(candidates))
    companies = []
    for i, r in enumerate(candidates):
        t = str(r["ticker"]).upper()
        subs = sec.submissions(r["cik"], max_age_hours=24 * ucfg.get("refresh_days", 7))
        sic = None
        if subs and subs.get("sic"):
            try:
                sic = int(subs["sic"])
            except (TypeError, ValueError):
                sic = None
        forced = t in extras
        if not forced and not in_ranges(sic, ucfg["sic_ranges"]):
            continue
        seg = (extras.get(t) or {}).get("segment") or segment_for_sic(sic)
        companies.append({"ticker": t, "cik": int(r["cik"]), "name": r.get("name"),
                          "exchange": r.get("exchange"), "sic": sic,
                          "sic_desc": (subs or {}).get("sic_desc"), "segment": seg, "forced": forced})
        if i % 500 == 0:
            log.info("  ...%d/%d checked, %d kept", i, len(candidates), len(companies))

    missing = sorted(set(extras) - {c["ticker"] for c in companies})
    if missing:
        log.warning("always-include tickers not found in SEC list: %s", ", ".join(missing))
    uni = {"built": utc_now().isoformat(), "count": len(companies), "companies": companies,
           "source": "SEC company_tickers_exchange.json + submissions SIC codes"}
    write_json(path, uni)
    return uni
