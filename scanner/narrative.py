"""Explanations. The default writer is deterministic: every sentence is filled from computed numbers,
so nothing can be invented. An optional AI step (ANTHROPIC_API_KEY) extracts catalysts stated in the
latest earnings release, quoting the filing — always labelled 'AI-extracted, verify against source'.
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

from .util import is_num, log


def P(x, d=0):
    return f"{x * 100:.{d}f}%" if is_num(x) else "n/a"


def D(x):
    return f"${x:,.2f}" if is_num(x) else "n/a"


def B(x):
    if not is_num(x):
        return "n/a"
    return f"${x / 1e9:,.1f}B" if abs(x) >= 1e9 else f"${x / 1e6:,.0f}M"


def X(x):
    return f"{x:.1f}x" if is_num(x) else "n/a"


def write(m: Dict[str, Any], val: Dict[str, Any], risk: Dict[str, Any], cats: List[Dict[str, Any]],
          sc_res: Dict[str, Any], rank: Optional[int], n_ranked: int) -> Dict[str, Any]:
    name, t = m.get("name"), m["ticker"]
    sc = val.get("scenarios") or {}
    cases = sc.get("cases", {})
    base, bull, bear = cases.get("base", {}), cases.get("bull", {}), cases.get("bear", {})
    iv = val.get("intrinsic") or {}
    rev = val.get("reverse_dcf") or {}
    p2 = val.get("path_to_double") or {}
    acc = m.get("growth_acceleration")
    trend = ("accelerating" if is_num(acc) and acc > 0.02 else "decelerating" if is_num(acc) and acc < -0.02 else "steady")

    thesis = (f"{name} ({t}) grew revenue {P(m.get('revenue_growth_ttm'))} over the last twelve months to "
              f"{B(m.get('revenue_ttm'))}, with growth {trend}, a {P(m.get('gross_margin'))} gross margin and a "
              f"{P(m.get('fcf_margin'))} free-cash-flow margin. The model's base case points to {D(base.get('price_24m'))} "
              f"in 24 months ({P(base.get('return_24m'))}), with a probability-weighted return of "
              f"{P(sc.get('expected_return_24m'))} against a bear-case move of {P(bear.get('return_24m'))}.")

    gap_txt = ""
    if is_num(rev.get("implied_growth")) and is_num(val.get("initial_growth")):
        gap = val["initial_growth"] - rev["implied_growth"]
        gap_txt = (f" At {D(m.get('price'))}, the share price implies roughly {P(rev['implied_growth'])} initial revenue growth "
                   f"fading to {P(val['dcf']['assumptions']['terminal_growth'])}, versus {P(val['initial_growth'])} in the model "
                   f"({val.get('growth_source')}) — a {'favourable' if gap > 0 else 'unfavourable'} gap of {abs(gap) * 100:.0f} percentage point{'s' if round(abs(gap) * 100) != 1 else ''}.")
    hist = val.get("historical") or {}
    hist_txt = ""
    if is_num(hist.get("percentile")):
        hist_txt = (f" EV/Sales of {X(hist.get('ev_sales_now'))} sits at the {P(hist['percentile'])} percentile of its own "
                    f"5-year range ({X(hist.get('min'))}–{X(hist.get('max'))}); a low percentile alone is not treated as cheapness.")
    undervalued = (f"Blended intrinsic value is {D(iv.get('per_share'))} per share (DCF {D((val.get('dcf') or {}).get('per_share'))}, "
                   f"comparables {D((val.get('comps') or {}).get('per_share'))}), a margin of safety of {P(iv.get('margin_of_safety'))}."
                   + gap_txt + hist_txt)

    fundamentals = (f"TTM revenue {B(m.get('revenue_ttm'))} ({P(m.get('revenue_growth_ttm'))} YoY; 3-yr CAGR {P(m.get('revenue_cagr_3y'))}). "
                    f"Operating margin {P(m.get('operating_margin'))} (prior year {P(m.get('operating_margin_prior'))}), "
                    f"ROIC {P(m.get('roic'))}. FCF {B(m.get('fcf_ttm'))}; after stock comp {B(m.get('fcf_after_sbc'))}. "
                    f"Net cash {B(m.get('net_cash'))}; share count {P(m.get('share_growth_yoy'), 1)} YoY. "
                    f"Competitive-advantage evidence is proxied by margins and returns on capital; qualitative moat review is not automated.")

    if p2:
        dec = p2.get("decomposition") or {}
        path = (f"A double means {D(p2.get('target_price_24m'))} by month 24. Holding the base-case margin and multiple rules, "
                f"that needs initial revenue growth of about {P(p2.get('required_initial_growth'))} versus {P(p2.get('base_initial_growth'))} "
                f"in the base case, or an exit multiple of {X(p2.get('required_exit_multiple'))} on base-case numbers ({sc.get('metric')}; "
                f"growth-justified {X(p2.get('justified_multiple'))}). Base-case return comes from fundamentals "
                f"{X(dec.get('fundamental_growth_factor'))}, multiple change {X(dec.get('multiple_change_factor'))}, dilution "
                f"{X(dec.get('dilution_factor'))}. Verdict: {p2.get('verdict')}.")
    else:
        path = "Insufficient data to model a path to 2x."

    monitor = [
        f"Quarterly YoY revenue growth staying near or above {P(val.get('initial_growth'))}",
        f"Operating margin progressing toward {P((val.get('margins') or {}).get('base'))} (currently {P(m.get('operating_margin'))})",
        "Next-year EPS estimate revisions (direction over 30/90 days)",
        f"Free-cash-flow margin (currently {P(m.get('fcf_margin'))}) and share count growth",
    ]
    if is_num(m.get("rpo")):
        monitor.append(f"Remaining performance obligations (backlog) growth, currently {P(m.get('rpo_growth'))}")
    if "concentrated_ai_exposure" in risk.get("flags", {}):
        monitor.append("Hyperscaler capex guidance (largest cloud customers' quarterly reports)")
    invalidate = (f"Thesis weakens if growth falls toward the bear case ({P(bear.get('initial_growth'))}) or margins head toward "
                  f"{P((val.get('margins') or {}).get('bear'))}; the bear-case 24-month price is {D(bear.get('price_24m'))}.")

    pos = f"#{rank} of {n_ranked}" if rank else "unranked"
    label = sc_res["tier"] if sc_res["action"] == sc_res["tier"] else f"{sc_res['tier']} — {sc_res['action']}"
    verdict = (f"{label}, score {sc_res['total']:.0f}/100, {pos}. "
               + " ".join(sc_res.get("tier_reasons", [])[:2]))
    return {"thesis": thesis, "why_undervalued": undervalued, "fundamentals": fundamentals,
            "path_to_double": path, "what_to_monitor": monitor, "invalidation": invalidate, "verdict": verdict,
            "bull_case": f"Bull: {P(bull.get('initial_growth'))} initial growth, {P(bull.get('target_margin'))} margin → {D(bull.get('price_24m'))} ({P(bull.get('return_24m'))}) in 24m.",
            "bear_case": f"Bear: {P(bear.get('initial_growth'))} initial growth, {P(bear.get('target_margin'))} margin → {D(bear.get('price_24m'))} ({P(bear.get('return_24m'))}) in 24m.",
            "generated_by": "deterministic template (no AI)"}


# ---------------------------------------------------------------------------------------------
# optional AI catalyst extraction
# ---------------------------------------------------------------------------------------------
def _strip_html(html: str) -> str:
    html = re.sub(r"(?is)<(script|style).*?</\1>", " ", html)
    text = re.sub(r"(?s)<[^>]+>", " ", html)
    text = re.sub(r"&nbsp;|&#160;", " ", text)
    text = re.sub(r"&amp;", "&", text)
    return re.sub(r"\s+", " ", text).strip()


def ai_catalysts(sec, m: Dict[str, Any], events: List[Dict[str, Any]], api_key: str, model: str) -> Optional[Dict[str, Any]]:
    """Ask Claude to list catalysts explicitly stated in the latest earnings press release (8-K 2.02, Ex-99.1)."""
    import requests

    ev = next((e for e in events if "2.02" in e["items"]), None)
    if not ev:
        return None
    docs = sec.filing_documents(m["cik"], ev["accession"])
    ex = next((d for d in docs if re.search(r"ex-?99", d.get("name", ""), re.I) and d["name"].endswith((".htm", ".html", ".txt"))), None)
    if not ex:
        return None
    from .sec import filing_url
    url = filing_url(m["cik"], ev["accession"], ex["name"])
    html = sec.http.get_text(url)
    if not html:
        return None
    text = _strip_html(html)[:30000]
    prompt = (
        "You are extracting facts from a company's earnings press release for an equity research tool.\n"
        "List up to 6 forward-looking catalysts or business developments that are EXPLICITLY stated in the text "
        "(product launches, customer or design wins, guidance, capacity, new markets). Do not infer or add anything "
        "that is not in the text. For each, give: title (<=12 words), classification ('confirmed' for completed events, "
        "'management target' for guidance/plans), and a supporting quote of at most 20 words copied exactly from the text.\n"
        'Respond with JSON only: {"catalysts":[{"title":"","classification":"","quote":""}]}\n\n'
        f"Company: {m.get('name')} ({m['ticker']}). Press release filed {ev['filed']}.\n\nTEXT:\n{text}"
    )
    try:
        r = requests.post("https://api.anthropic.com/v1/messages", timeout=90, headers={
            "x-api-key": api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
            json={"model": model, "max_tokens": 1200, "messages": [{"role": "user", "content": prompt}]})
        r.raise_for_status()
        body = r.json()
        txt = "".join(b.get("text", "") for b in body.get("content", []) if b.get("type") == "text")
        data = json.loads(txt[txt.find("{"): txt.rfind("}") + 1])
    except Exception as exc:  # never let the optional AI step break the run
        log.warning("AI catalyst extraction failed for %s: %s", m["ticker"], exc)
        return None
    verified = []
    norm_text = text.lower()
    for c in data.get("catalysts", [])[:6]:
        q = (c.get("quote") or "").strip()
        if q and q.lower()[:60] in norm_text:  # keep only quotes that really appear in the filing
            verified.append({"title": c.get("title"), "classification": c.get("classification"), "quote": q})
    return {"source_url": url, "filed": ev["filed"], "model": model, "catalysts": verified,
            "label": "AI-extracted from the SEC filing; quotes verified to appear in the source text"}
