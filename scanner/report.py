"""Daily report assembly (JSON for the dashboard, Markdown for the archive / GitHub)."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from .metrics import price_on_or_before, price_return
from .risk import AI_CYCLE_SEGMENTS
from .sec import filing_url, latest_periodic
from .util import days_between, growth, is_num, read_json, write_json

NO_NEW = "No new high-conviction opportunity identified today."
DISCLAIMER = ("Research tool output. Intrinsic values, targets and entry ranges are model estimates built from public "
              "data and stated assumptions — not predictions, guarantees, or personalised investment advice. "
              "Verify figures against the linked filings before acting.")


def load_previous(data_dir: Path, asof: str) -> Optional[Dict[str, Any]]:
    d = Path(data_dir) / "reports"
    if not d.exists():
        return None
    files = sorted(p for p in d.glob("*.json") if p.stem < asof)
    return read_json(files[-1]) if files else None


def _market(bench: Dict[str, Dict[str, List]], asof: str) -> List[Dict[str, Any]]:
    names = {"SPY": "S&P 500 (SPY)", "QQQ": "Nasdaq-100 (QQQ)", "SOXX": "Semiconductors (SOXX)", "IGV": "Software (IGV)"}
    out = []
    for t, px in bench.items():
        if not px or not px.get("close"):
            continue
        ytd_base = price_on_or_before(px, f"{asof[:4]}-01-01") or price_on_or_before(px, f"{int(asof[:4]) - 1}-12-31")
        out.append({"ticker": t, "name": names.get(t, t), "close": px["close"][-1], "date": px["dates"][-1],
                    "return_1d": price_return(px, 1), "return_1m": price_return(px, 21),
                    "return_ytd": growth(px["close"][-1], ytd_base)})
    return out


def _sources(t: str, subs: Optional[Dict[str, Any]], details: Dict[str, Any], generated: str) -> List[Dict[str, str]]:
    src = []
    if subs:
        lp = latest_periodic(subs)
        if lp and lp.get("doc"):
            src.append({"label": f"{lp['form']} filed {lp['filed']} (period {lp.get('report_date') or 'n/a'})",
                        "url": filing_url(subs["cik"], lp["accession"], lp["doc"]), "date": lp["filed"]})
    for c in details.get("cats", []):
        if c.get("url"):
            src.append({"label": f"{c['title']} ({c['date']})", "url": c["url"], "date": c["date"]})
    src.append({"label": f"Price & consensus snapshot (Yahoo Finance, retrieved {generated[:16]})",
                "url": f"https://finance.yahoo.com/quote/{t.replace('.', '-')}", "date": generated[:10]})
    seen, out = set(), []
    for s in src:
        if s["url"] not in seen:
            seen.add(s["url"])
            out.append(s)
    return out


def _changes(rows: List[Dict[str, Any]], prev: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not prev:
        return {"first_report": True}
    prev_rows = {r["ticker"]: r for r in prev.get("rows", [])}
    good = ("Tier 1", "Tier 2")
    order = {"Tier 1": 1, "Tier 2": 2, "Tier 3": 3, "Avoid": 4}
    new, up, down, moves, unchanged = [], [], [], [], []
    cur = {r["ticker"]: r for r in rows}
    for r in rows:
        p = prev_rows.get(r["ticker"])
        if r["tier"] in good and (not p or p.get("tier") not in good):
            new.append({"ticker": r["ticker"], "tier": r["tier"], "was": p.get("tier") if p else "not ranked"})
        elif p and order.get(r["tier"], 9) < order.get(p.get("tier"), 9):
            up.append({"ticker": r["ticker"], "from": p["tier"], "to": r["tier"]})
        elif p and order.get(r["tier"], 9) > order.get(p.get("tier"), 9):
            down.append({"ticker": r["ticker"], "from": p["tier"], "to": r["tier"],
                         "thesis_weakened": p.get("tier") in good})
        elif p and r["tier"] in good:
            unchanged.append(r["ticker"])
        if p and is_num(p.get("score")) and is_num(r.get("score")) and abs(r["score"] - p["score"]) >= 5:
            moves.append({"ticker": r["ticker"], "from": p["score"], "to": r["score"]})
    removed = [{"ticker": t, "was": p.get("tier")} for t, p in prev_rows.items()
               if t not in cur and p.get("tier") in good]
    return {"first_report": False, "previous_date": prev.get("date"), "new_attractive": new, "upgrades": up,
            "downgrades": down, "removed": removed, "score_moves": moves, "unchanged_attractive": unchanged}


def build(asof, generated: datetime, rows, top, details, prev, bench, universe_size, with_data, passed,
          shortlisted, subs_map, source_label, cfg) -> Dict[str, Any]:
    gen = generated.isoformat(timespec="minutes")
    prev_t1 = {r["ticker"] for r in (prev or {}).get("rows", []) if r.get("tier") == "Tier 1"}
    t1 = [r for r in rows if r["tier"] == "Tier 1"]
    new_t1 = [r for r in t1 if r["ticker"] not in prev_t1]
    best_new = new_t1[0] if new_t1 else None
    attractive = [r for r in rows if r["tier"] in ("Tier 1", "Tier 2")]
    ai_heavy = [r for r in attractive if r.get("segment") in AI_CYCLE_SEGMENTS]

    recent_earn = []
    for t, subs in subs_map.items():
        for f in (subs or {}).get("filings", [])[:15]:
            if f.get("form", "").startswith("8-K") and "2.02" in (f.get("items") or ""):
                age = days_between(f["filed"], asof)
                if age is not None and 0 <= age <= 3:
                    recent_earn.append({"ticker": t, "filed": f["filed"]})
                break

    top_details = []
    for r in top:
        d = details[r["ticker"]]
        top_details.append({
            "ticker": r["ticker"], "name": r["name"], "tier": r["tier"], "score": r["score"], "action": r["action"],
            "narrative": d.get("narrative"), "catalysts": d["cats"], "risks": d["risk"]["top_risks"],
            "ai_catalysts": d.get("ai_catalysts"),
            "sources": _sources(r["ticker"], subs_map.get(r["ticker"]), d, gen),
        })

    price_dates = sorted({r["price_date"] for r in rows if r.get("price_date")})
    return {
        "date": asof,
        "generated_at": gen,
        "data_snapshot": {
            "prices_as_of_close": price_dates[-1] if price_dates else None,
            "fundamentals": "latest SEC 10-K/10-Q/20-F filings available at generation time",
            "estimates": "Yahoo Finance consensus snapshot at generation time (unofficial)",
            "sources": source_label,
        },
        "market": _market(bench, asof),
        "tech_developments": {
            "earnings_releases_last_3_days": sorted(recent_earn, key=lambda x: x["filed"], reverse=True)[:20],
            "note": "Facts from SEC 8-K filings (Item 2.02). Macro/news commentary is not generated automatically.",
        },
        "counts": {
            "universe": universe_size, "with_usable_data": with_data, "passed_initial_screen": passed,
            "deep_analysed": shortlisted,
            "tier1": len(t1), "tier2": sum(r["tier"] == "Tier 2" for r in rows),
            "tier3": sum(r["tier"] == "Tier 3" for r in rows), "avoid": sum(r["tier"] == "Avoid" for r in rows),
            "high_quality": len(attractive),
        },
        "headline": (f"New Tier 1 opportunity: {best_new['name']} ({best_new['ticker']})" if best_new else NO_NEW),
        "top_new_opportunity": best_new["ticker"] if best_new else None,
        "concentration": {
            "attractive": len(attractive), "ai_capex_cycle": len(ai_heavy),
            "warning": (f"{len(ai_heavy)} of {len(attractive)} attractive names depend on the same AI/data-center capex "
                        f"cycle — treat them as correlated, not diversified.") if attractive and len(ai_heavy) / len(attractive) > 0.6 else None,
        },
        "rows": rows,
        "top": top_details,
        "changes": _changes(rows, prev),
        "disclaimer": DISCLAIMER,
    }


# ---------------------------------------------------------------------------------------------
def _p(x, d=0):
    return f"{x * 100:.{d}f}%" if is_num(x) else "–"


def _d(x):
    return f"${x:,.2f}" if is_num(x) else "–"


def _b(x):
    if not is_num(x):
        return "–"
    return f"${x / 1e12:.2f}T" if x >= 1e12 else f"${x / 1e9:.1f}B" if x >= 1e9 else f"${x / 1e6:.0f}M"


def markdown(rep: Dict[str, Any], table_size: int) -> str:
    c = rep["counts"]
    L = [f"# Daily Opportunity Report — {rep['date']}", "",
         f"Generated {rep['generated_at']} · prices as of close {rep['data_snapshot']['prices_as_of_close']} · {rep['data_snapshot']['sources']}", "",
         f"**{rep['headline']}**", "",
         "## Market", ""]
    for m in rep["market"]:
        L.append(f"- {m['name']}: {m['close']:.2f} ({_p(m['return_1d'], 1)} 1d, {_p(m['return_1m'], 1)} 1m, {_p(m['return_ytd'], 1)} YTD)")
    er = rep["tech_developments"]["earnings_releases_last_3_days"]
    if er:
        L.append(f"- Earnings releases filed in the last 3 days: {', '.join(e['ticker'] for e in er)}")
    L += ["", "## Funnel", "",
          f"Universe {c['universe']} → usable data {c['with_usable_data']} → passed initial screen {c['passed_initial_screen']} "
          f"→ deep-analysed {c['deep_analysed']} → Tier 1: {c['tier1']}, Tier 2: {c['tier2']}, Tier 3: {c['tier3']}, Avoid: {c['avoid']}", ""]
    if rep["concentration"].get("warning"):
        L += [f"> {rep['concentration']['warning']}", ""]
    L += ["## Ranked candidates", "",
          "| # | Company | Price | Mkt cap | Rev growth (TTM / fwd) | EV/S · IV | Target 12m / 24m | Upside 12m / 24m | Bull 24m | Bear 24m | Score | Action |",
          "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in rep["rows"][:table_size]:
        evs = f"{r['ev_sales_ttm']:.1f}x" if is_num(r.get("ev_sales_ttm")) else "–"
        score = f"{r['score']:.0f}" if is_num(r.get("score")) else "–"
        L.append(f"| {r['rank']} | {r['name']} ({r['ticker']}) | {_d(r['price'])} | {_b(r['market_cap'])} | "
                 f"{_p(r['revenue_growth_ttm'])} / {_p(r['consensus_growth_ntm'])} | {evs} · {_d(r['intrinsic'])} | "
                 f"{_d(r['target_12m'])} / {_d(r['target_24m'])} | {_p(r['return_12m'])} / {_p(r['return_24m'])} | "
                 f"{_p(r['bull_return_24m'])} | {_p(r['bear_return_24m'])} | {score} | {r['tier']} · {r['action']} |")
    for t in rep["top"]:
        n = t["narrative"] or {}
        L += ["", f"## {t['name']} ({t['ticker']}) — {t['tier']}, score {t['score']:.0f}", "",
              f"**Investment thesis.** {n.get('thesis', '')}", "",
              f"**Why it may be undervalued.** {n.get('why_undervalued', '')}", "",
              f"**Business fundamentals.** {n.get('fundamentals', '')}", "",
              f"**Path to a potential double.** {n.get('path_to_double', '')}", "",
              f"**Scenarios.** {n.get('bull_case', '')} {n.get('bear_case', '')}", "",
              "**Catalysts.**"]
        for cat in t["catalysts"][:6]:
            L.append(f"- [{cat['type']}] {cat['title']} — {cat['mechanism']}" + (f" ([source]({cat['url']}))" if cat.get("url") else ""))
        if t.get("ai_catalysts") and t["ai_catalysts"].get("catalysts"):
            L.append(f"- AI-extracted from [earnings release]({t['ai_catalysts']['source_url']}) — verify:")
            for a in t["ai_catalysts"]["catalysts"]:
                L.append(f"  - ({a['classification']}) {a['title']}: “{a['quote']}”")
        L += ["", "**Key risks.**"]
        for rk in t["risks"]:
            L.append(f"- {rk['title']} ({rk['basis']}): {rk['detail']}")
        L += ["", "**What to monitor.**"] + [f"- {x}" for x in n.get("what_to_monitor", [])]
        L += ["", f"**Invalidation.** {n.get('invalidation', '')}", "", f"**Verdict.** {n.get('verdict', '')}", "", "Sources:"]
        L += [f"- [{s['label']}]({s['url']})" for s in t["sources"]]
    ch = rep["changes"]
    L += ["", "## Changes since previous report", ""]
    if ch.get("first_report"):
        L.append("First report — no comparison available.")
    else:
        L.append(f"Compared with {ch['previous_date']}.")
        for k, label in (("new_attractive", "Newly attractive"), ("upgrades", "Upgrades"), ("downgrades", "Downgrades"),
                         ("removed", "Dropped out"), ("score_moves", "Score moves ≥5")):
            if ch.get(k):
                L.append(f"- {label}: " + ", ".join(x["ticker"] for x in ch[k]))
        if ch.get("unchanged_attractive"):
            L.append("- Still attractive, no material change: " + ", ".join(ch["unchanged_attractive"]))
    if rep.get("alerts"):
        L += ["", "## Alerts", ""] + [f"- {a['message']}" for a in rep["alerts"]]
    L += ["", "---", f"_{rep['disclaimer']}_", ""]
    return "\n".join(L)


def write_all(rep, rows, screener_rows, details, perf, pub_cfg, site_dir: Path, data_dir: Path, asof: str) -> None:
    site_data = Path(site_dir) / "data"
    (site_data / "stocks").mkdir(parents=True, exist_ok=True)
    write_json(site_data / "latest.json", rep, compact=True)
    write_json(site_data / "screener.json", {"date": asof, "rows": screener_rows}, compact=True)
    for t, d in details.items():
        write_json(site_data / "stocks" / f"{t}.json", {"date": asof, "ticker": t, **d}, compact=True)
    write_json(site_data / "performance.json", perf, compact=True)
    write_json(site_data / "config.json", pub_cfg)

    # archive (committed to the repo): compact report + markdown
    arch = Path(data_dir) / "reports"
    arch.mkdir(parents=True, exist_ok=True)
    keep = ("ticker", "name", "segment", "tier", "action", "score", "price", "intrinsic", "margin_of_safety",
            "target_12m", "target_24m", "return_24m", "expected_return_24m", "bear_24m", "entry_low", "entry_high",
            "entry_status", "high_flags", "rank", "invalidation_price", "revenue_growth_ttm", "market_cap")
    compact = {k: v for k, v in rep.items() if k not in ("rows", "top")}
    compact["rows"] = [{k: r.get(k) for k in keep} for r in rows]
    compact["top"] = [{"ticker": t["ticker"], "tier": t["tier"], "score": t["score"],
                       "verdict": (t.get("narrative") or {}).get("verdict")} for t in rep["top"]]
    write_json(arch / f"{asof}.json", compact, compact=True)
    md = markdown(rep, 25)
    (arch / f"{asof}.md").write_text(md, encoding="utf-8")

    # past reports on the site
    rdir = site_data / "reports"
    rdir.mkdir(parents=True, exist_ok=True)
    dates = sorted(p.stem for p in arch.glob("*.json"))
    shown = []
    for d in dates[-60:]:
        src = arch / f"{d}.md"
        if src.exists():
            (rdir / f"{d}.md").write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
            shown.append(d)
    write_json(rdir / "index.json", {"dates": shown[::-1]})
