"""Transparent 100-point score. Every component lists the measured inputs that produced it.

Missing inputs earn partial credit (30%) and are listed, so data gaps never inflate a score.
Caps stop a strong growth score from masking a fragile balance sheet or an extreme valuation.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Tuple

from .util import is_num, lerp_score

MISSING_CREDIT = 0.3


def _blend(parts: List[Tuple[str, Optional[float], float, str]]) -> Tuple[float, List[Dict[str, Any]]]:
    tot, wsum, det = 0.0, 0.0, []
    for name, s, w, shown in parts:
        val = s if s is not None else MISSING_CREDIT
        tot += val * w
        wsum += w
        det.append({"factor": name, "score": round(val, 3), "weight": w, "value": shown,
                    "missing": s is None})
    return (tot / wsum if wsum else 0.0), det


def _pct(x: Any, digits: int = 0) -> str:
    return f"{x * 100:.{digits}f}%" if is_num(x) else "n/a"


def _x(x: Any) -> str:
    return f"{x:.1f}x" if is_num(x) else "n/a"


def score(m: Dict[str, Any], val: Dict[str, Any], risk: Dict[str, Any], cats: List[Dict[str, Any]],
          cfg: Dict[str, Any]) -> Dict[str, Any]:
    W = cfg["scoring"]["weights"]
    comp: Dict[str, Dict[str, Any]] = {}
    iv = (val or {}).get("intrinsic") or {}
    sc = (val or {}).get("scenarios") or {}
    rev = (val or {}).get("reverse_dcf") or {}
    g_base = (val or {}).get("initial_growth")
    implied = rev.get("implied_growth")
    exp_gap = (g_base - implied) if is_num(g_base) and is_num(implied) else None

    pct = m.get("ev_sales_hist_percentile")
    s, d = _blend([
        ("Margin of safety vs intrinsic value", lerp_score(iv.get("margin_of_safety"), -0.4, 0.5), 0.6, _pct(iv.get("margin_of_safety"))),
        ("EV/Sales vs own 5-year range (lower is better)", (1 - pct) if is_num(pct) else None, 0.2, f"percentile {_pct(pct)}"),
        ("Modelled growth minus price-implied growth", lerp_score(exp_gap, -0.15, 0.15), 0.2, _pct(exp_gap, 1)),
    ])
    comp["valuation"] = {"score": s, "details": d}

    acq = m.get("acquisitions_2y_pct_revenue")
    fwd = m.get("consensus_growth_ntm")
    s, d = _blend([
        ("TTM revenue growth", lerp_score(m.get("revenue_growth_ttm"), 0.0, 0.40), 0.35, _pct(m.get("revenue_growth_ttm"))),
        ("Consensus next-12-month growth", lerp_score(fwd, 0.0, 0.35), 0.25, _pct(fwd)),
        ("3-year revenue CAGR", lerp_score(m.get("revenue_cagr_3y"), 0.0, 0.35), 0.15, _pct(m.get("revenue_cagr_3y"))),
        ("Growth acceleration (2Q vs prior 2Q)", lerp_score(m.get("growth_acceleration"), -0.10, 0.10), 0.15, _pct(m.get("growth_acceleration"), 1)),
        ("Growth consistency (low volatility)", lerp_score(m.get("growth_volatility"), 0.25, 0.03), 0.10, f"sd {_pct(m.get('growth_volatility'))}"),
    ])
    if is_num(acq) and acq > 0.25:
        s *= 1 - min(0.5, acq)
        d.append({"factor": "Penalty: acquisition-driven growth", "score": None, "weight": None, "value": _pct(acq), "missing": False})
    comp["growth"] = {"score": s, "details": d}

    rev_ttm = m.get("revenue_ttm")
    scale = lerp_score(math.log10(rev_ttm), math.log10(3e8), math.log10(5e9)) if is_num(rev_ttm) and rev_ttm > 0 else None
    gm, gmp = m.get("gross_margin"), m.get("gross_margin_prior")
    s, d = _blend([
        ("Gross margin (pricing power proxy)", lerp_score(gm, 0.30, 0.75), 0.35, _pct(gm)),
        ("Return on invested capital", lerp_score(m.get("roic"), 0.0, 0.30), 0.25, _pct(m.get("roic"))),
        ("Operating margin (GAAP)", lerp_score(m.get("operating_margin"), -0.10, 0.35), 0.20, _pct(m.get("operating_margin"))),
        ("Gross margin trend YoY", lerp_score(gm - gmp if is_num(gm) and is_num(gmp) else None, -0.05, 0.03), 0.10,
         _pct(gm - gmp, 1) if is_num(gm) and is_num(gmp) else "n/a"),
        ("Scale (TTM revenue)", scale, 0.10, f"${rev_ttm / 1e9:.2f}B" if is_num(rev_ttm) else "n/a"),
    ])
    comp["quality"] = {"score": s, "details": d}

    nc, ebitda, nde = m.get("net_cash"), m.get("ebitda_ttm"), m.get("net_debt_to_ebitda")
    if is_num(nc) and nc >= 0:
        lev = 1.0
    elif is_num(nde):
        lev = lerp_score(nde, 3.0, 0.0)
    elif is_num(nc) and nc < 0:
        lev = 0.0  # net debt with no positive EBITDA
    else:
        lev = None
    s, d = _blend([
        ("Net cash / leverage", lev, 0.35, (f"net cash ${nc / 1e9:.2f}B" if is_num(nc) else "n/a")),
        ("Free cash flow margin", lerp_score(m.get("fcf_margin"), -0.10, 0.30), 0.30, _pct(m.get("fcf_margin"))),
        ("Share count growth (dilution)", lerp_score(m.get("share_growth_yoy"), 0.06, 0.0), 0.15, _pct(m.get("share_growth_yoy"), 1)),
        ("Stock-based comp / revenue", lerp_score(m.get("sbc_pct"), 0.25, 0.05), 0.10, _pct(m.get("sbc_pct"))),
        ("Current ratio (liquidity)", lerp_score(m.get("current_ratio"), 0.8, 2.0), 0.10, _x(m.get("current_ratio"))),
    ])
    comp["financial_strength"] = {"score": s, "details": d}

    s, d = _blend([
        ("Probability-weighted 24-month return", lerp_score(sc.get("expected_return_24m"), 0.0, 1.0), 0.6, _pct(sc.get("expected_return_24m"))),
        ("Upside / downside ratio (24m)", lerp_score(sc.get("up_down_ratio_24m"), 1.0, 5.0), 0.4, _x(sc.get("up_down_ratio_24m"))),
    ])
    comp["upside"] = {"score": s, "details": d}

    cons, impl = m.get("consensus_growth_ntm"), implied
    cgap = cons - impl if is_num(cons) and is_num(impl) else None
    earn = next((c for c in cats if c["type"] == "scheduled"), None)
    rg, g = m.get("rpo_growth"), m.get("revenue_growth_ttm")
    s, d = _blend([
        ("EPS estimate revisions (90 days)", lerp_score(m.get("eps_revision_90d"), -0.10, 0.10), 0.35, _pct(m.get("eps_revision_90d"), 1)),
        ("Positive growth acceleration", lerp_score(m.get("growth_acceleration"), 0.0, 0.08), 0.20, _pct(m.get("growth_acceleration"), 1)),
        ("Consensus growth above price-implied growth", lerp_score(cgap, 0.0, 0.15), 0.25, _pct(cgap, 1)),
        ("Earnings event within 60 days", (1.0 if earn["days_away"] <= 60 else 0.5) if earn else None, 0.10,
         f"{earn['days_away']} days" if earn else "n/a"),
        ("Backlog growth vs revenue growth", lerp_score(rg - g, -0.10, 0.10) if is_num(rg) and is_num(g) else None, 0.10,
         _pct(rg - g, 1) if is_num(rg) and is_num(g) else "n/a"),
    ])
    comp["catalysts"] = {"score": s, "details": d}

    for k, c in comp.items():
        c["max"] = W[k]
        c["points"] = round(c["score"] * W[k], 1)
    raw = sum(c["points"] for c in comp.values())
    total = raw
    caps = []
    flags = risk.get("flags", {})
    capcfg = cfg["scoring"]["caps"]
    if flags.get("weak_balance_sheet", {}).get("severity") == "high" and total > capcfg["weak_balance_sheet"]:
        total = capcfg["weak_balance_sheet"]
        caps.append(f"capped at {capcfg['weak_balance_sheet']} (weak balance sheet)")
    for f in ("excessive_valuation", "optimistic_assumptions_only", "implausible_model_output"):
        if flags.get(f, {}).get("severity") == "high" and total > capcfg["extreme_valuation"]:
            total = capcfg["extreme_valuation"]
            caps.append(f"capped at {capcfg['extreme_valuation']} ({f.replace('_', ' ')})")

    tier, reasons = classify(total, m, val, risk, cfg)
    return {"total": round(total, 1), "raw": round(raw, 1), "components": comp, "caps_applied": caps,
            "tier": tier, "tier_reasons": reasons, "action": action_for(tier, val)}


def classify(total: float, m, val, risk, cfg) -> Tuple[str, List[str]]:
    T = cfg["scoring"]["tiers"]
    dq = m.get("data_quality", {})
    if not dq.get("usable"):
        return "Insufficient data", ["core data missing or stale"]
    sc = (val or {}).get("scenarios") or {}
    iv = (val or {}).get("intrinsic") or {}
    p2 = (val or {}).get("path_to_double") or {}
    er, ud, mos = sc.get("expected_return_24m"), sc.get("up_down_ratio_24m"), iv.get("margin_of_safety")
    serious = [f for f in risk.get("high_flags", [])]
    why: List[str] = []
    t1 = T["tier1"]
    checks1 = [
        (total >= t1["min_score"], f"score {total:.0f} ≥ {t1['min_score']}"),
        (is_num(mos) and mos >= t1["min_margin_of_safety"], f"margin of safety {_pct(mos)} ≥ {_pct(t1['min_margin_of_safety'])}"),
        (is_num(er) and er >= t1["min_expected_return_24m"], f"expected 24m return {_pct(er)} ≥ {_pct(t1['min_expected_return_24m'])}"),
        (is_num(ud) and ud >= t1["min_up_down_ratio"], f"upside/downside {_x(ud)} ≥ {t1['min_up_down_ratio']}x"),
        (not serious, "no high-severity risk flags" if not serious else "high-severity flags: " + ", ".join(serious)),
        (bool(p2.get("potential_double")), f"path to 2x: {p2.get('verdict', 'n/a')}"),
        (is_num(m.get("consensus_growth_ntm")), "consensus estimates available"),
    ]
    if all(ok for ok, _ in checks1):
        return "Tier 1", [txt for _, txt in checks1]
    t2 = T["tier2"]
    checks2 = [
        (total >= t2["min_score"], f"score {total:.0f} ≥ {t2['min_score']}"),
        (is_num(er) and er >= t2["min_expected_return_24m"], f"expected 24m return {_pct(er)} ≥ {_pct(t2['min_expected_return_24m'])}"),
        (is_num(ud) and ud >= t2["min_up_down_ratio"], f"upside/downside {_x(ud)} ≥ {t2['min_up_down_ratio']}x"),
        (not any(f in serious for f in ("weak_balance_sheet", "optimistic_assumptions_only", "implausible_model_output")),
         "no balance-sheet, optimism or implausible-output red flag"),
    ]
    failed1 = [txt for ok, txt in checks1 if not ok]
    if all(ok for ok, _ in checks2):
        return "Tier 2", ["Tier 1 not met: " + "; ".join(failed1)]
    if total >= T["tier3"]["min_score"]:
        failed2 = [txt for ok, txt in checks2 if not ok]
        return "Tier 3", ["Tier 2 not met: " + "; ".join(failed2)]
    return "Avoid", [f"score {total:.0f} below {T['tier3']['min_score']}"] + (["high-severity flags: " + ", ".join(serious)] if serious else [])


def action_for(tier: str, val: Dict[str, Any]) -> str:
    entry = (val or {}).get("entry") or {}
    above = entry.get("status") == "above entry range"
    if tier == "Tier 1":
        return "Wait for entry" if above else "Buy candidate"
    if tier == "Tier 2":
        return "Wait for entry" if above else "Buy candidate (conditional)"
    if tier == "Tier 3":
        return "Watchlist"
    if tier == "Avoid":
        return "Avoid"
    return "Insufficient data"
