"""Valuation engine: WACC, DCF, reverse DCF, comparables, scenarios, path-to-double, entry range.

Design choices (documented in guide/METHODOLOGY.md):
- Operating income is GAAP (after stock-based compensation), so SBC is treated as a real cost in
  the DCF. In the 12/24-month scenarios SBC is added back to cash (it is non-cash) and dilution is
  applied to the share count instead — never both, to avoid double counting.
- Reinvestment = revenue growth / sales-to-capital (captures capex and working capital).
- Exit multiples are tied to forward growth and are only allowed to move partway from today's
  multiple in the base case. Historical peak multiples are never assumed.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from .util import clamp, is_num, median

SCEN = ("bear", "base", "bull")


# ---------------------------------------------------------------------------------------------
# discount rate
# ---------------------------------------------------------------------------------------------
def wacc(m: Dict[str, Any], cfg: Dict[str, Any]) -> Dict[str, Any]:
    v = cfg["valuation"]
    b_raw = m.get("beta")
    b = clamp(b_raw, v["beta_min"], v["beta_max"]) if is_num(b_raw) else 1.3
    coe = v["risk_free_rate"] + b * v["equity_risk_premium"]
    cod = (v["risk_free_rate"] + v["credit_spread"]) * (1 - m.get("tax_rate_model", v["tax_rate_default"]))
    e = m.get("market_cap") or 0.0
    d = m.get("debt") or 0.0
    we = e / (e + d) if (e + d) > 0 else 1.0
    w = clamp(we * coe + (1 - we) * cod, v["wacc_min"], v["wacc_max"])
    return {"beta_raw": b_raw, "beta_used": b, "cost_of_equity": coe, "cost_of_debt_after_tax": cod,
            "equity_weight": we, "wacc": w,
            "note": "beta from 2y weekly returns vs SPY, clamped" if is_num(b_raw) else "beta unavailable; 1.3 assumed"}


# ---------------------------------------------------------------------------------------------
# assumptions
# ---------------------------------------------------------------------------------------------
def initial_growth(m: Dict[str, Any], cfg: Dict[str, Any]) -> Tuple[float, str]:
    v = cfg["valuation"]
    cons, ttm = m.get("consensus_growth_ntm"), m.get("revenue_growth_ttm")
    if is_num(cons):
        g, src = cons, "analyst consensus (Yahoo), next-12-month blend"
    elif is_num(ttm):
        g, src = ttm * 0.8, "trailing growth x 0.8 (no consensus available)"
    else:
        g, src = 0.03, "default 3% (no growth data)"
    return clamp(g, -0.20, v["max_initial_growth"]), src


def scenario_growth(g1: float, scen: str) -> float:
    if scen == "bull":
        return g1 * 1.25 + 0.03
    if scen == "bear":
        return g1 * 0.5 - 0.02 if g1 > 0 else g1 - 0.10
    return g1


def growth_path(g1: float, g_term: float, years: int) -> List[float]:
    if years <= 1:
        return [g1]
    step = (g1 - g_term) / (years - 1)
    return [g1 - step * t for t in range(years)]


def margin_targets(m: Dict[str, Any], cfg: Dict[str, Any]) -> Dict[str, Any]:
    v = cfg["valuation"]
    cur = m.get("operating_margin")
    if not is_num(cur):
        cur = m.get("fcf_margin") if is_num(m.get("fcf_margin")) else 0.0
    gm = m.get("gross_margin")
    ceiling = max(min(0.10, gm * 0.5), gm * v["margin_ceiling_vs_gross"]) if is_num(gm) else 0.15
    if cur < ceiling:
        base = cur + 0.5 * (ceiling - cur)
        bull = cur + 0.8 * (ceiling - cur)
    else:
        base = cur - 0.25 * (cur - ceiling)
        bull = cur
    bear = min(cur, base) - 0.05
    cyclical = bool(m.get("cyclical_signal"))
    trough = m.get("op_margin_min_5y")
    if cyclical and is_num(trough):
        bear = min(bear, trough)  # cyclical businesses: bear case revisits the worst margin of the last 5 years
    return {"current": cur, "ceiling": ceiling, "bear": bear, "base": base, "bull": bull,
            "cyclical_bear": bool(cyclical and is_num(trough))}


def margin_path(cur: float, target: float, years: int, ramp: int) -> List[float]:
    return [cur + (target - cur) * min(t, ramp) / ramp for t in range(1, years + 1)]


# ---------------------------------------------------------------------------------------------
# DCF
# ---------------------------------------------------------------------------------------------
def dcf(m: Dict[str, Any], cfg: Dict[str, Any], w: float, g1: float, margin_target: float,
        detail: bool = False) -> Optional[Dict[str, Any]]:
    v = cfg["valuation"]
    rev0, shares = m.get("revenue_ttm"), m.get("shares_for_valuation")
    if not (is_num(rev0) and is_num(shares) and shares > 0 and rev0 > 0):
        return None
    n, gt = v["dcf_years"], v["terminal_growth"]
    tax, s2c = m.get("tax_rate_model", v["tax_rate_default"]), m.get("sales_to_capital", 2.0)
    mt = margin_targets(m, cfg)
    gs = growth_path(g1, gt, n)
    ms = margin_path(mt["current"], margin_target, n, v["margin_ramp_years"])
    rows, pv_sum, rev_prev = [], 0.0, rev0
    for t in range(n):
        rev = rev_prev * (1 + gs[t])
        ebit = rev * ms[t]
        nopat = ebit * (1 - tax) if ebit > 0 else ebit
        reinv = (rev - rev_prev) / s2c
        fcff = nopat - reinv
        df = (1 + w) ** -(t + 1)
        pv_sum += fcff * df
        rows.append({"year": t + 1, "revenue": rev, "growth": gs[t], "op_margin": ms[t], "nopat": nopat,
                     "reinvestment": reinv, "fcff": fcff, "pv": fcff * df})
        rev_prev = rev
    ronic = max(v["terminal_ronic"], w + 0.01)
    nopat_t = rev_prev * (1 + gt) * ms[-1] * (1 - tax)
    tv = max(0.0, nopat_t * (1 - gt / ronic) / (w - gt))
    pv_tv = tv * (1 + w) ** -n
    ev = pv_sum + pv_tv
    net_cash = m.get("net_cash") or 0.0
    equity = ev + net_cash
    out = {"enterprise_value": ev, "equity_value": equity, "per_share": max(0.0, equity / shares),
           "pv_explicit": pv_sum, "pv_terminal": pv_tv,
           "terminal_share_of_ev": pv_tv / ev if ev > 0 else None,
           "assumptions": {"initial_growth": g1, "terminal_growth": gt, "years": n, "wacc": w,
                           "start_margin": mt["current"], "target_margin": margin_target,
                           "tax_rate": tax, "sales_to_capital": s2c, "terminal_ronic": ronic,
                           "net_cash": net_cash, "shares": shares}}
    if detail:
        out["years"] = rows
    return out


def reverse_dcf(m: Dict[str, Any], cfg: Dict[str, Any], w: float, margin_target: float) -> Dict[str, Any]:
    """Initial revenue growth (fading to terminal) that the current price implies."""
    price = m.get("price")
    if not is_num(price):
        return {"implied_growth": None, "note": "no price"}

    def f(g):
        r = dcf(m, cfg, w, g, margin_target)
        return (r["per_share"] if r else 0.0) - price

    lo, hi = -0.30, 1.50
    flo, fhi = f(lo), f(hi)
    if flo > 0:
        return {"implied_growth": lo, "bound": "below", "note": "price is below DCF value even at -30% initial growth"}
    if fhi < 0:
        return {"implied_growth": hi, "bound": "above", "note": "price exceeds DCF value even at 150% initial growth"}
    for _ in range(60):
        mid = (lo + hi) / 2
        if f(mid) < 0:
            lo = mid
        else:
            hi = mid
    return {"implied_growth": (lo + hi) / 2, "bound": None,
            "note": "initial revenue growth (fading linearly to terminal over 10y) that equates DCF value to price, "
                    "holding the base-case margin path"}


# ---------------------------------------------------------------------------------------------
# 12 / 24-month scenarios (exit-multiple method)
# ---------------------------------------------------------------------------------------------
def cash_margin(m: Dict[str, Any], op_margin_t: float) -> float:
    """Profitability used in rule-of-40 style sales multiples: today's FCF-after-SBC margin, moved by the
    change in operating margin the scenario assumes (GAAP margin alone over-penalises SBC-heavy software)."""
    cur_op = m.get("operating_margin") if is_num(m.get("operating_margin")) else 0.0
    base = m.get("fcf_after_sbc_margin")
    if not is_num(base):
        base = cur_op
    return base + (op_margin_t - cur_op)


def justified_multiple(kind: str, g: float, margin: float, gm: Optional[float], cfg: Dict[str, Any]) -> float:
    v = cfg["valuation"]
    if kind == "ev_ebit":
        p = v["ebit_multiple"]
        return clamp(p["intercept"] + p["per_growth_point"] * g * 100, p["min"], p["max"])
    p = v["sales_multiple"]
    gm_adj = (gm / 0.75) if is_num(gm) and gm > 0 else 0.6
    r40 = g * 100 + max(margin, -0.20) * 100
    return clamp(p["per_rule40_point"] * r40 * gm_adj, p["min"], p["max"])


def project(m: Dict[str, Any], cfg: Dict[str, Any], g1: float, target_margin: float, years: int = 4) -> List[Dict[str, float]]:
    v = cfg["valuation"]
    mt = margin_targets(m, cfg)
    gs = growth_path(g1, v["terminal_growth"], v["dcf_years"])[:years]
    ms = margin_path(mt["current"], target_margin, years, v["margin_ramp_years"])
    tax, s2c = m.get("tax_rate_model", v["tax_rate_default"]), m.get("sales_to_capital", 2.0)
    sbc_pct = m.get("sbc_pct") if is_num(m.get("sbc_pct")) else 0.0
    rows, prev = [], m["revenue_ttm"]
    for t in range(years):
        rev = prev * (1 + gs[t])
        ebit = rev * ms[t]
        nopat = ebit * (1 - tax) if ebit > 0 else ebit
        cash_gen = nopat - (rev - prev) / s2c + sbc_pct * rev  # SBC is non-cash; dilution handled separately
        rows.append({"year": t + 1, "revenue": rev, "growth": gs[t], "margin": ms[t], "ebit": ebit, "cash_gen": cash_gen})
        prev = rev
    return rows


def _scenario_prices(m, cfg, g1, target_margin, scen, kind, current_mult, dil):
    """Price at 12 and 24 months for one scenario. Forward metric at exit T = year T+1."""
    rows = project(m, cfg, g1, target_margin, 4)
    gm = m.get("gross_margin")
    out = {}
    for T in (1, 2):
        fwd = rows[T]  # year T+1 (0-indexed T)
        metric = fwd["ebit"] if kind == "ev_ebit" else fwd["revenue"]
        prof = fwd["margin"] if kind == "ev_ebit" else cash_margin(m, fwd["margin"])
        just = justified_multiple(kind, rows[min(T + 1, 3)]["growth"], prof, gm, cfg)
        if is_num(current_mult) and current_mult > 0:
            if scen == "base":
                mult = 0.5 * current_mult + 0.5 * just
            elif scen == "bull":
                mult = max(current_mult, just)
            else:
                mult = min(current_mult, just)
        else:
            mult = just if scen != "bear" else just * 0.8
        net_cash = (m.get("net_cash") or 0.0) + sum(r["cash_gen"] for r in rows[:T])
        shares = m["shares_for_valuation"] * (1 + dil) ** T
        price = max(0.0, (mult * metric + net_cash) / shares) if metric > 0 or kind == "ev_sales" else max(0.0, net_cash / shares)
        out[T] = {"price": price, "exit_multiple": mult, "justified_multiple": just, "forward_metric": metric,
                  "net_cash": net_cash, "shares": shares, "revenue_fwd": fwd["revenue"], "margin_fwd": fwd["margin"]}
    return out, rows


def scenarios(m: Dict[str, Any], cfg: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    v = cfg["valuation"]
    price = m.get("price")
    if not (is_num(price) and is_num(m.get("revenue_ttm")) and is_num(m.get("shares_for_valuation"))):
        return None
    g1, gsrc = initial_growth(m, cfg)
    mt = margin_targets(m, cfg)
    base_rows = project(m, cfg, g1, mt["base"], 4)
    bear_rows = project(m, cfg, scenario_growth(g1, "bear"), mt["bear"], 4)
    # earnings-based exit multiple whenever the business is profitable in both base and bear cases;
    # sales-based only for companies without dependable operating profits
    # (multiples of near-zero GAAP margins are meaningless, so thin-margin cases fall back to sales; and when
    # stock comp is large, GAAP EBIT understates cash economics, so a cash-margin-adjusted sales multiple is used)
    sbc_light = not is_num(m.get("sbc_pct")) or m["sbc_pct"] < 0.08
    kind = ("ev_ebit" if base_rows[1]["margin"] >= 0.08 and bear_rows[1]["margin"] >= 0.05 and sbc_light
            else "ev_sales")
    ntm_metric = base_rows[0]["ebit"] if kind == "ev_ebit" else base_rows[0]["revenue"]
    ev = m.get("enterprise_value")
    current_mult = ev / ntm_metric if is_num(ev) and ntm_metric > 0 else None
    sg = m.get("share_growth_yoy")
    dil = clamp(sg, 0.0, 0.08) if is_num(sg) else 0.01
    probs = v["scenario_probabilities"]
    res: Dict[str, Any] = {"metric": "EV / forward operating income (GAAP)" if kind == "ev_ebit" else "EV / forward revenue",
                           "kind": kind, "current_multiple": current_mult, "growth_source": gsrc,
                           "annual_dilution": dil, "probabilities": probs, "cases": {}}
    for s in SCEN:
        gs = scenario_growth(g1, s)
        d = dil + (0.01 if s == "bear" else 0.0)
        prices, rows = _scenario_prices(m, cfg, gs, mt[s], s, kind, current_mult, d)
        res["cases"][s] = {
            "initial_growth": gs, "target_margin": mt[s],
            "price_12m": prices[1]["price"], "price_24m": prices[2]["price"],
            "return_12m": prices[1]["price"] / price - 1, "return_24m": prices[2]["price"] / price - 1,
            "exit_multiple_12m": prices[1]["exit_multiple"], "exit_multiple_24m": prices[2]["exit_multiple"],
            "justified_multiple_24m": prices[2]["justified_multiple"],
            "revenue_y1": rows[0]["revenue"], "revenue_y2": rows[1]["revenue"], "revenue_y3": rows[2]["revenue"],
            "ebit_y2": rows[1]["ebit"], "ebit_y3": rows[2]["ebit"],
            "net_cash_24m": prices[2]["net_cash"], "shares_24m": prices[2]["shares"],
        }
    c = res["cases"]
    for T in ("12m", "24m"):
        pw = sum(probs[s] * c[s][f"price_{T}"] for s in SCEN)
        res[f"expected_price_{T}"] = pw
        res[f"expected_return_{T}"] = pw / price - 1
        down = price - c["bear"][f"price_{T}"]
        up = pw - price
        res[f"up_down_ratio_{T}"] = (up / down if down > 0 else (10.0 if up > 0 else 0.0)) if up > 0 else 0.0
        res[f"up_down_ratio_{T}"] = min(res[f"up_down_ratio_{T}"], 10.0)
    res["optimistic_only"] = c["base"]["return_24m"] < 0.15 and c["bull"]["return_24m"] >= 0.5
    return res


def path_to_double(m: Dict[str, Any], cfg: Dict[str, Any], sc: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """What has to be true for a 2x in 24 months — and is it supported by the evidence?"""
    if not sc:
        return None
    price = m["price"]
    target = 2 * price
    kind, cur = sc["kind"], sc["current_multiple"]
    mt = margin_targets(m, cfg)
    dil = sc["annual_dilution"]
    base = sc["cases"]["base"]

    def p24(g):
        return _scenario_prices(m, cfg, g, mt["base"], "base", kind, cur, dil)[0][2]["price"]

    lo, hi = -0.20, 1.50
    req_g = None
    if p24(hi) >= target:
        if p24(lo) >= target:
            req_g = lo
        else:
            for _ in range(50):
                mid = (lo + hi) / 2
                if p24(mid) < target:
                    lo = mid
                else:
                    hi = mid
            req_g = (lo + hi) / 2
    metric3 = base["ebit_y3"] if kind == "ev_ebit" else base["revenue_y3"]
    req_mult = (target * base["shares_24m"] - base["net_cash_24m"]) / metric3 if metric3 > 0 else None
    g_base = base["initial_growth"]
    # decomposition of the base-case 24m outcome
    rows = project(m, cfg, g_base, mt["base"], 4)
    m1 = rows[0]["ebit"] if kind == "ev_ebit" else rows[0]["revenue"]
    m3 = rows[2]["ebit"] if kind == "ev_ebit" else rows[2]["revenue"]
    decomposition = {
        "fundamental_growth_factor": (m3 / m1) if m1 > 0 and m3 > 0 else None,
        "multiple_change_factor": (base["exit_multiple_24m"] / cur) if is_num(cur) and cur > 0 else None,
        "dilution_factor": (1 + dil) ** -2,
    }
    if base["return_24m"] >= 1.0:
        verdict = "base case doubles"
    elif req_g is not None and req_g <= max(g_base + 0.05, g_base * 1.15):
        verdict = "credible path"
    elif sc["cases"]["bull"]["return_24m"] >= 1.0:
        verdict = "requires bull case"
    else:
        verdict = "not supported"
    return {
        "target_price_24m": target,
        "required_initial_growth": req_g,
        "base_initial_growth": g_base,
        "required_exit_multiple": req_mult,
        "base_exit_multiple": base["exit_multiple_24m"],
        "justified_multiple": base["justified_multiple_24m"],
        "metric": sc["metric"],
        "decomposition": decomposition,
        "verdict": verdict,
        "potential_double": verdict in ("base case doubles", "credible path"),
    }


# ---------------------------------------------------------------------------------------------
# comparables
# ---------------------------------------------------------------------------------------------
def _fwd(mm):
    g = mm.get("consensus_growth_ntm")
    return g if is_num(g) else mm.get("revenue_growth_ttm")


def comps(m: Dict[str, Any], universe: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    def ok(p):
        return (p is not m and p.get("ticker") != m.get("ticker") and is_num(p.get("enterprise_value"))
                and is_num(p.get("revenue_ttm")) and p["revenue_ttm"] > 0 and is_num(_fwd(p))
                and is_num(p.get("operating_margin")) and p.get("data_quality", {}).get("usable"))

    same = [p for p in universe if ok(p) and p.get("segment") == m.get("segment")]
    group, label = (same, m.get("segment")) if len(same) >= 4 else ([p for p in universe if ok(p)], "broad technology universe")
    if len(group) < 4:
        return None
    g, opm = _fwd(m), m.get("operating_margin")
    rev_ntm = m["revenue_ttm"] * (1 + g) if is_num(g) else None
    shares, net_cash = m.get("shares_for_valuation"), m.get("net_cash") or 0.0
    if not (is_num(rev_ntm) and is_num(shares) and shares > 0 and is_num(opm)):
        return None

    def quality_denominator(mm, gg):
        """(growth + cash margin) x gross margin: a 12%-gross-margin assembler is not priced like software."""
        gmm = mm.get("gross_margin")
        if not is_num(gmm) or gmm <= 0:
            return None
        return (gg * 100 + cash_margin(mm, mm["operating_margin"]) * 100) * gmm

    peer_rows, r40_ratios, peg_ratios = [], [], []
    for p in group:
        pg, pm = _fwd(p), p["operating_margin"]
        prev = p["revenue_ttm"] * (1 + pg)
        evs = p["enterprise_value"] / prev if prev > 0 else None
        evebit = p["enterprise_value"] / (prev * pm) if pm > 0.05 else None
        denom = quality_denominator(p, pg)
        if is_num(evs) and is_num(denom) and denom >= 3:
            r40_ratios.append(evs / denom)
        if is_num(evebit) and pg >= 0.03 and pm >= 0.08 and evebit < 150:
            peg_ratios.append(evebit / (pg * 100))
        peer_rows.append({"ticker": p["ticker"], "name": p.get("name"), "ev_sales_ntm": evs, "ev_ebit_ntm": evebit,
                          "growth": pg, "op_margin": pm, "revenue_ttm": p["revenue_ttm"]})
    peer_rows.sort(key=lambda r: abs((r["revenue_ttm"] or 0) - m["revenue_ttm"]))
    methods = {}
    denom = quality_denominator(m, g)
    r40 = median(r40_ratios)
    if is_num(r40) and is_num(denom) and denom >= 3:
        mult = min(r40 * denom, 40.0)
        methods["quality_adjusted_ev_sales"] = {
            "peer_median_ratio": r40, "implied_multiple": mult,
            "per_share": max(0.0, (mult * rev_ntm + net_cash) / shares),
            "basis": "EV/NTM sales ÷ [(growth + FCF-after-SBC margin) × gross margin], peer median"}
    peg = median(peg_ratios)
    if is_num(peg) and opm >= 0.08 and g >= 0.03:
        mult = min(peg * g * 100, 60.0)
        methods["growth_adjusted_ev_ebit"] = {"peer_median_ratio": peg, "implied_multiple": mult,
                                              "per_share": max(0.0, (mult * rev_ntm * opm + net_cash) / shares),
                                              "basis": "EV/NTM operating income ÷ growth (PEG-style), peer median"}
    if not methods:
        return None
    ps = [x["per_share"] for x in methods.values()]
    return {"peer_group": label, "peer_count": len(group), "peers": peer_rows[:10], "methods": methods,
            "per_share": sum(ps) / len(ps),
            "note": "Peer multiples are normalised for growth and margin, so a 35%-grower is not given an 8%-grower's multiple."}


# ---------------------------------------------------------------------------------------------
# synthesis
# ---------------------------------------------------------------------------------------------
def intrinsic_value(m, cfg, dcf_res, comps_res) -> Dict[str, Any]:
    w = dict(cfg["valuation"]["intrinsic_weights"])
    low_conf = (not is_num(m.get("operating_margin"))) or m["operating_margin"] < 0
    if low_conf:
        w["dcf"] *= 0.5
    parts = []
    if dcf_res:
        parts.append(("dcf", dcf_res["per_share"], w["dcf"]))
    if comps_res:
        parts.append(("comps", comps_res["per_share"], w["comps"]))
    if not parts:
        return {"per_share": None, "margin_of_safety": None, "weights": {}, "dcf_low_confidence": low_conf}
    tot = sum(p[2] for p in parts)
    iv = sum(p[1] * p[2] for p in parts) / tot
    price = m.get("price")
    return {"per_share": iv, "margin_of_safety": iv / price - 1 if is_num(price) and price > 0 else None,
            "weights": {p[0]: p[2] / tot for p in parts}, "dcf_low_confidence": low_conf}


def entry_range(m, cfg, iv: Dict[str, Any], sc: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    price = m.get("price")
    if not (is_num(iv.get("per_share")) and sc and is_num(price)):
        return None
    tr = cfg["valuation"]["entry_target_return_24m"]
    upper = min(iv["per_share"] * 0.90, sc["expected_price_24m"] / (1 + tr))
    if upper <= 0:
        return None
    lower = upper * 0.85
    if price < lower:
        status, gap = "below entry range", price / upper - 1
    elif price <= upper:
        status, gap = "in entry range", 0.0
    else:
        status, gap = "above entry range", price / upper - 1
    return {"low": lower, "high": upper, "status": status, "price_vs_high": gap,
            "method": (f"Upper bound = lower of 90% of intrinsic value and the price at which the probability-"
                       f"weighted 24-month target implies a {tr:.0%} return; lower bound 15% beneath.")}


def run(m: Dict[str, Any], cfg: Dict[str, Any], universe: List[Dict[str, Any]]) -> Dict[str, Any]:
    wc = wacc(m, cfg)
    mt = margin_targets(m, cfg)
    g1, gsrc = initial_growth(m, cfg)
    base_dcf = dcf(m, cfg, wc["wacc"], g1, mt["base"], detail=True)
    dcf_cases = {s: dcf(m, cfg, wc["wacc"], scenario_growth(g1, s), mt[s]) for s in SCEN}
    rev = reverse_dcf(m, cfg, wc["wacc"], mt["base"])
    cp = comps(m, universe)
    sc = scenarios(m, cfg)
    iv = intrinsic_value(m, cfg, base_dcf, cp)
    return {
        "wacc": wc, "margins": mt, "initial_growth": g1, "growth_source": gsrc,
        "dcf": base_dcf,
        "dcf_scenarios": {s: (d["per_share"] if d else None) for s, d in dcf_cases.items()},
        "reverse_dcf": rev, "comps": cp, "scenarios": sc,
        "path_to_double": path_to_double(m, cfg, sc), "intrinsic": iv,
        "entry": entry_range(m, cfg, iv, sc),
        "historical": {"ev_sales_now": m.get("ev_sales_ttm"), "min": m.get("ev_sales_hist_min"),
                       "median": m.get("ev_sales_hist_median"), "max": m.get("ev_sales_hist_max"),
                       "percentile": m.get("ev_sales_hist_percentile")},
    }
