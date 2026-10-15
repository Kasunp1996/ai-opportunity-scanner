"""Daily pipeline: ingest → metrics → screen → value → risk → score → rank → report → history → alerts."""
from __future__ import annotations

import shutil
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from . import alerts as alerts_mod
from . import catalysts as cat_mod
from . import history, metrics, narrative, report, risk as risk_mod, scoring, valuation
from .config import anthropic_key, load_watchlist, public_config
from .screen import initial_screen
from .sec import recent_8k_events
from .util import days_between, is_num, log, read_json, utc_now, write_json

BENCHMARKS = ["SPY", "QQQ", "SOXX", "IGV"]
TIER_ORDER = {"Tier 1": 0, "Tier 2": 1, "Tier 3": 2, "Avoid": 3, "Insufficient data": 4}


class Source:
    """Bundle of data providers. LiveSource = SEC + Yahoo; demo.DemoSource = synthetic fixtures."""
    label = "base"
    sec = None
    market = None

    def universe(self, cfg, data_dir: Path, force: bool) -> List[Dict[str, Any]]:
        raise NotImplementedError

    def submissions(self, cik: int) -> Optional[Dict[str, Any]]:
        raise NotImplementedError

    def fundamentals(self, cik: int, subs) -> Optional[Dict[str, Any]]:
        raise NotImplementedError


class LiveSource(Source):
    label = "SEC EDGAR (fundamentals, filings) + Yahoo Finance via yfinance (prices, estimates)"

    def __init__(self, user_agent: str, cache_dir: Path):
        from .market import YahooMarket
        from .sec import SecClient
        self.sec = SecClient(user_agent, cache_dir)
        self.market = YahooMarket()

    def universe(self, cfg, data_dir, force):
        from .universe import build_universe
        return build_universe(self.sec, cfg, str(data_dir / "universe.json"), force)["companies"]

    def submissions(self, cik):
        return self.sec.submissions(cik)

    def fundamentals(self, cik, subs):
        return self.sec.fundamentals(cik, subs)


def is_trading_day(d: date, cfg: Dict[str, Any]) -> bool:
    """Weekday that is not an NYSE holiday (list in config/settings.yaml → market_holidays)."""
    if d.weekday() >= 5:
        return False
    return d.isoformat() not in {str(x) for x in cfg.get("market_holidays", [])}


def summary_row(m, val, rk, sc, cats, today: date, screen_notes: List[str], exception: bool, deep: bool) -> Dict[str, Any]:
    s = (val or {}).get("scenarios") or {}
    c = s.get("cases", {})
    iv = (val or {}).get("intrinsic") or {}
    en = (val or {}).get("entry") or {}
    p2 = (val or {}).get("path_to_double") or {}
    closes = [x[1] for x in m.get("price_weekly", [])]
    ne = m.get("next_earnings")
    ed = days_between(today, ne) if ne else None
    return {
        "ticker": m["ticker"], "name": m.get("name"), "segment": m.get("segment"),
        "price": m.get("price"), "price_date": m.get("price_date"), "market_cap": m.get("market_cap"),
        "revenue_ttm": m.get("revenue_ttm"), "revenue_growth_ttm": m.get("revenue_growth_ttm"),
        "consensus_growth_ntm": m.get("consensus_growth_ntm"), "growth_acceleration": m.get("growth_acceleration"),
        "gross_margin": m.get("gross_margin"), "operating_margin": m.get("operating_margin"),
        "fcf_margin": m.get("fcf_margin"), "net_cash": m.get("net_cash"),
        "ev_sales_ttm": m.get("ev_sales_ttm"), "ev_ebit_ttm": m.get("ev_ebit_ttm"), "pe_forward": m.get("pe_forward"),
        "p_fcf_ttm": m.get("p_fcf_ttm"),
        "intrinsic": iv.get("per_share"), "margin_of_safety": iv.get("margin_of_safety"),
        "target_12m": c.get("base", {}).get("price_12m"), "target_24m": c.get("base", {}).get("price_24m"),
        "return_12m": c.get("base", {}).get("return_12m"), "return_24m": c.get("base", {}).get("return_24m"),
        "expected_return_12m": s.get("expected_return_12m"), "expected_return_24m": s.get("expected_return_24m"),
        "bull_24m": c.get("bull", {}).get("price_24m"), "bull_return_24m": c.get("bull", {}).get("return_24m"),
        "bear_24m": c.get("bear", {}).get("price_24m"), "bear_return_24m": c.get("bear", {}).get("return_24m"),
        "bear_return_12m": c.get("bear", {}).get("return_12m"),
        "up_down_24m": s.get("up_down_ratio_24m"),
        "score": sc["total"] if sc else None, "tier": sc["tier"] if sc else "Insufficient data",
        "action": sc["action"] if sc else "Insufficient data",
        "entry_low": en.get("low"), "entry_high": en.get("high"), "entry_status": en.get("status"),
        "high_flags": (rk or {}).get("high_flags", []), "flags": sorted((rk or {}).get("flags", {}).keys()),
        "next_earnings": ne, "earnings_in_days": ed,
        "potential_double": p2.get("potential_double"), "double_verdict": p2.get("verdict"),
        "small_company_exception": exception, "screen_notes": screen_notes,
        "estimates": is_num(m.get("consensus_growth_ntm")), "deep_analysis": deep,
        "invalidation_price": c.get("bear", {}).get("price_24m"),
        "return_1d": m.get("return_1d"),
        "return_1m": m.get("return_1m"), "return_6m": m.get("return_6m"),
        "spark": closes[-26:],
        "data_notes": m.get("data_quality", {}).get("notes", []),
    }


def analyse(m, peers, cfg, subs, today) -> Dict[str, Any]:
    val = valuation.run(m, cfg, peers)
    rk = risk_mod.assess(m, val, cfg)
    events = recent_8k_events(subs, 120, today) if subs else []
    cats = cat_mod.build(m, events, val, today)
    sc = scoring.score(m, val, rk, cats, cfg)
    return {"val": val, "risk": rk, "cats": cats, "score": sc, "events": events}


def run(source: Source, cfg: Dict[str, Any], site_dir: Path, data_dir: Path, force: bool = False,
        now: Optional[datetime] = None) -> Dict[str, Any]:
    tz = ZoneInfo(cfg["report"].get("timezone", "America/New_York"))
    now = now or utc_now()
    local = now.astimezone(tz)
    today = local.date()
    asof = today.isoformat()
    site_dir, data_dir = Path(site_dir), Path(data_dir)
    log.info("run started %s (%s)", local.isoformat(), source.label)

    if not force and not is_trading_day(today, cfg):
        log.info("%s is not a U.S. trading day (weekend or NYSE holiday). Nothing to do.", asof)
        return {"skipped": True, "reason": f"{asof} is not a trading day"}
    bench = source.market.prices(BENCHMARKS, period="5y")
    spy = bench.get("SPY")
    if not spy:
        raise RuntimeError("Benchmark prices unavailable from the market-data provider — aborting rather than publishing stale data.")
    last_bar = spy["dates"][-1]
    log.info("latest SPY bar from provider: %s (session date %s)", last_bar, asof)
    lag = days_between(last_bar, asof)
    if lag is not None and lag > 5:
        raise RuntimeError(f"Market data looks stale: latest SPY bar is {last_bar}. Aborting rather than publishing old prices.")
    if last_bar != asof:
        log.warning("Provider has not published %s's close yet; prices are as of %s and the report says so.", asof, last_bar)

    companies = source.universe(cfg, data_dir, force=False)
    log.info("universe: %d companies", len(companies))

    subs_map, funds = {}, {}
    for i, c in enumerate(companies):
        subs = source.submissions(c["cik"])
        subs_map[c["ticker"]] = subs
        f = source.fundamentals(c["cik"], subs)
        if f:
            funds[c["ticker"]] = f
        if i and i % 100 == 0:
            log.info("  fundamentals %d/%d", i, len(companies))

    tickers = [c["ticker"] for c in companies if c["ticker"] in funds]
    prices = source.market.prices(tickers, period="5y")
    prices.update(bench)

    fx_cache: Dict[str, Optional[float]] = {}

    def fx(ccy):
        if ccy not in fx_cache:
            fx_cache[ccy] = source.market.fx_to_usd(ccy)
        return fx_cache[ccy]

    # ---- pass 1: preliminary metrics for everyone (also the peer set for comparables) ----
    prelim: Dict[str, Dict[str, Any]] = {}
    for c in companies:
        t = c["ticker"]
        if t not in funds or t not in prices:
            continue
        ccy = funds[t].get("currency", "USD")
        m = metrics.compute(c, funds[t], prices[t], spy, None, None, None, fx(ccy), today, cfg)
        prelim[t] = m
    with_data = len(prelim)
    passed = {}
    for t, m in prelim.items():
        ok, notes, exc = initial_screen(m, cfg)
        if ok:
            passed[t] = (notes, exc)
    log.info("initial screen: %d of %d pass", len(passed), with_data)

    # rank prelim candidates cheaply (full engine without estimates) to choose the shortlist
    peers_all = [m for m in prelim.values() if m.get("data_quality", {}).get("usable")]
    pre_scores = {}
    for t in passed:
        a = analyse(prelim[t], peers_all, cfg, None, today)
        pre_scores[t] = a["score"]["total"]
    prev_report = report.load_previous(data_dir, asof)
    prev_rows = {r["ticker"]: r for r in (prev_report or {}).get("rows", [])}
    watch = load_watchlist(cfg)
    n_short = cfg["screen"]["shortlist_size"]
    shortlist = [t for t, _ in sorted(pre_scores.items(), key=lambda kv: -kv[1])[:n_short]]
    for t in list(prev_rows) + watch:
        if t in passed and t not in shortlist:
            shortlist.append(t)

    # ---- pass 2: deep analysis with consensus estimates, splits and Yahoo cross-checks ----
    final: Dict[str, Dict[str, Any]] = {}
    for i, t in enumerate(shortlist):
        c = next(x for x in companies if x["ticker"] == t)
        est = source.market.estimates(t)
        info = source.market.info(t)
        spl = source.market.splits(t)
        m = metrics.compute(c, funds[t], prices[t], spy, est, info, spl, fx(funds[t].get("currency", "USD")), today, cfg)
        cl = prices[t]["close"]
        m["return_1d"] = cl[-1] / cl[-2] - 1 if len(cl) > 1 and cl[-2] else None
        prelim[t] = m
        ok, notes, exc = initial_screen(m, cfg)  # re-check with corrected share counts / estimates
        if not ok:
            log.info("  %s dropped after full data: %s", t, "; ".join(notes))
            passed.pop(t, None)
            continue
        passed[t] = (notes, exc)
        final[t] = m
        if i and i % 20 == 0:
            log.info("  deep analysis %d/%d", i, len(shortlist))
    peers_all = [m for m in prelim.values() if m.get("data_quality", {}).get("usable")]

    rows, details = [], {}
    for t, m in final.items():
        a = analyse(m, peers_all, cfg, subs_map.get(t), today)
        notes, exc = passed[t]
        row = summary_row(m, a["val"], a["risk"], a["score"], a["cats"], today, notes, exc, True)
        rows.append(row)
        details[t] = {"metrics": m, **a}
    rows.sort(key=lambda r: (TIER_ORDER.get(r["tier"], 9), -(r["score"] or 0)))
    for i, r in enumerate(rows, 1):
        r["rank"] = i

    # screener also lists names that passed the screen but were not deep-analysed
    screener_rows = list(rows)
    for t in passed:
        if t in final:
            continue
        a = analyse(prelim[t], peers_all, cfg, None, today)
        r = summary_row(prelim[t], a["val"], a["risk"], a["score"], a["cats"], today, passed[t][0], passed[t][1], False)
        r["tier"], r["action"] = "Not shortlisted", "Not shortlisted (estimates not fetched)"
        screener_rows.append(r)

    # ---- narratives for the top opportunities -------------------------------------------
    ranked_attractive = [r for r in rows if r["tier"] in ("Tier 1", "Tier 2", "Tier 3")]
    top_n = cfg["report"]["top_detail_count"]
    top = [r for r in rows if r["tier"] in ("Tier 1", "Tier 2")][:top_n]
    key = anthropic_key() if cfg.get("ai_narrative", {}).get("enabled") else None
    for r in rows:
        d = details[r["ticker"]]
        d["narrative"] = narrative.write(d["metrics"], d["val"], d["risk"], d["cats"], d["score"], r["rank"], len(rows))
    if key and source.sec is not None:
        for r in top[: cfg["ai_narrative"].get("max_companies", 3)]:
            d = details[r["ticker"]]
            d["ai_catalysts"] = narrative.ai_catalysts(source.sec, d["metrics"], d["events"], key, cfg["ai_narrative"]["model"])

    # ---- report ---------------------------------------------------------------------------
    rep = report.build(asof=asof, generated=local, rows=rows, top=top, details=details, prev=prev_report,
                       bench=bench, universe_size=len(companies), with_data=with_data, passed=len(passed),
                       shortlisted=len(final), subs_map=subs_map, source_label=source.label, cfg=cfg)
    al = alerts_mod.evaluate(rows, prev_rows, watch, cfg["alerts"]["earnings_within_days"])
    rep["alerts"] = al

    # ---- history (append before anyone can know what happens next) -----------------------
    hist_path = data_dir / "history" / "recommendations.jsonl"
    history.append(hist_path, [{
        "date": asof, "ticker": r["ticker"], "tier": r["tier"], "action": r["action"], "score": r["score"],
        "price": r["price"], "intrinsic": r["intrinsic"], "target_12m": r["target_12m"], "target_24m": r["target_24m"],
        "bear_24m": r["bear_24m"], "entry_low": r["entry_low"], "entry_high": r["entry_high"], "segment": r["segment"],
    } for r in rows if r["tier"] != "Insufficient data"])
    perf = history.performance(history.load(hist_path), prices, asof)

    # ---- write outputs ----------------------------------------------------------------------
    report.write_all(rep, rows, screener_rows, details, perf, public_config(cfg), site_dir, data_dir, asof)
    md = alerts_mod.markdown(al, asof)
    (data_dir / "alerts.md").write_text(md, encoding="utf-8")
    log.info("done: %d ranked, %d Tier1, %d Tier2, %d alerts", len(rows),
             sum(r["tier"] == "Tier 1" for r in rows), sum(r["tier"] == "Tier 2" for r in rows), len(al))
    return {"skipped": False, "report": rep, "rows": rows}


def copy_static(site_src: Path, site_dir: Path) -> None:
    site_dir.mkdir(parents=True, exist_ok=True)
    for p in site_src.iterdir():
        if p.is_file():
            shutil.copy2(p, site_dir / p.name)
