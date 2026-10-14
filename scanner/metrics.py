"""Deterministic financial metrics from normalized SEC fundamentals + market data.

Conventions
- TTM = sum of the last four discrete quarters ending at the latest reported quarter.
- All flows are aligned to the same TTM end date; balance sheet = latest reported date.
- Money is converted to USD at the current FX rate when a company reports in another currency.
- Anything that cannot be computed is None and listed in data_quality.missing — never estimated.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Tuple

from .util import (cagr, clamp, days_between, growth, is_num, median, parse_date, percentile_rank,
                   safe_div, stdev)

MONEY_DURATION = ["revenue", "cost_of_revenue", "gross_profit", "operating_income", "net_income", "pretax_income",
                  "income_tax", "cfo", "capex", "sbc", "da", "rnd", "interest_expense", "acquisitions",
                  "buybacks", "dividends_paid"]


# ---------------------------------------------------------------------------------------------
# period helpers
# ---------------------------------------------------------------------------------------------
def _near(a: str, b: str, tol: int = 12) -> bool:
    d = days_between(a, b)
    return d is not None and abs(d) <= tol


def _shift(end: str, days: int) -> str:
    return (parse_date(end) + timedelta(days=days)).isoformat()


def ttm_at(fund: Dict[str, Any], metric: str, end: str) -> Tuple[Optional[float], str]:
    """Trailing-twelve-month value ending at `end`. Returns (value, method)."""
    qs = fund.get("quarterly", {}).get(metric, [])
    idx = next((i for i in range(len(qs) - 1, -1, -1) if _near(qs[i]["end"], end)), None)
    if idx is not None and idx >= 3:
        window = qs[idx - 3: idx + 1]
        span = days_between(window[0].get("start") or _shift(window[0]["end"], -90), window[-1]["end"])
        consecutive = all(75 <= (days_between(window[k]["end"], window[k + 1]["end"]) or 0) <= 105
                          for k in range(3))
        if consecutive and span and 330 <= span <= 380:
            return sum(q["val"] for q in window), "ttm_quarters"
    for a in reversed(fund.get("annual", {}).get(metric, [])):
        if _near(a["end"], end, 20):
            return a["val"], "annual"
    return None, "missing"


def reference_end(fund: Dict[str, Any]) -> Optional[str]:
    """Latest end date for which TTM revenue is computable."""
    cands = []
    qs = fund.get("quarterly", {}).get("revenue", [])
    for q in reversed(qs):
        v, m = ttm_at(fund, "revenue", q["end"])
        if v is not None and m == "ttm_quarters":
            cands.append(q["end"])
            break
    ann = fund.get("annual", {}).get("revenue", [])
    if ann:
        cands.append(ann[-1]["end"])
    return max(cands) if cands else None


def instant_at(fund: Dict[str, Any], metric: str, end: str, back_tol: int = 200) -> Optional[Dict[str, Any]]:
    """Value at `end`, or the most recent earlier value within back_tol days."""
    arr = fund.get("instant", {}).get(metric, [])
    best = None
    for r in arr:
        d = days_between(r["end"], end)
        if d is None or d < -5:
            continue
        if d <= back_tol and (best is None or r["end"] > best["end"]):
            best = r
    return best


def split_factor(splits: Dict[str, float], fact_end: str, fact_filed: Optional[str], asof: str) -> float:
    """Multiplier to bring an old share count to today's share basis (skip if already restated)."""
    f = 1.0
    for d, ratio in (splits or {}).items():
        if fact_end < d <= asof and (fact_filed is None or fact_filed < d):
            f *= ratio
    return f


# ---------------------------------------------------------------------------------------------
# price helpers
# ---------------------------------------------------------------------------------------------
def price_return(px: Dict[str, List], trading_days: int) -> Optional[float]:
    c = px.get("close", []) if px else []
    if len(c) <= trading_days:
        return None
    return growth(c[-1], c[-1 - trading_days])


def price_on_or_before(px: Dict[str, List], d: str) -> Optional[float]:
    if not px:
        return None
    dates, close = px["dates"], px["close"]
    lo, hi = 0, len(dates) - 1
    if hi < 0 or dates[0] > d:
        return None
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if dates[mid] <= d:
            lo = mid
        else:
            hi = mid - 1
    return close[lo]


def weekly(px: Dict[str, List], years: float = 3.0) -> List[Tuple[str, float]]:
    if not px or not px.get("dates"):
        return []
    last = parse_date(px["dates"][-1])
    start = (last - timedelta(days=int(365 * years))).isoformat()
    out: Dict[Tuple[int, int], Tuple[str, float]] = {}
    for d, c in zip(px["dates"], px["close"]):
        if d < start:
            continue
        iso = parse_date(d).isocalendar()
        out[(iso[0], iso[1])] = (d, c)
    return [out[k] for k in sorted(out)]


def beta(px: Dict[str, List], bench: Dict[str, List]) -> Optional[float]:
    a, b = dict(weekly(px, 2.0)), dict(weekly(bench, 2.0))
    common = sorted(set(a) & set(b))
    if len(common) < 52:
        return None
    ra = [a[common[i]] / a[common[i - 1]] - 1 for i in range(1, len(common))]
    rb = [b[common[i]] / b[common[i - 1]] - 1 for i in range(1, len(common))]
    mb = sum(rb) / len(rb)
    ma = sum(ra) / len(ra)
    cov = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    var = sum((y - mb) ** 2 for y in rb)
    return cov / var if var > 0 else None


# ---------------------------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------------------------
def compute(company: Dict[str, Any], fund: Dict[str, Any], px: Dict[str, List], bench: Dict[str, List],
            est: Optional[Dict[str, Any]], info: Optional[Dict[str, Any]], splits: Optional[Dict[str, float]],
            fx: Optional[float], today: date, cfg: Dict[str, Any]) -> Dict[str, Any]:
    est = est or {}
    info = info or {}
    m: Dict[str, Any] = {"ticker": company["ticker"], "name": company.get("name") or fund.get("name"),
                         "segment": company.get("segment"), "sic": company.get("sic"),
                         "cik": company.get("cik"), "reporting_currency": fund.get("currency", "USD")}
    missing: List[str] = []
    notes: List[str] = []
    ccy = fund.get("currency", "USD")
    fxr = 1.0 if ccy == "USD" else fx
    if ccy != "USD":
        if not is_num(fxr):
            missing.append("fx_rate")
            fxr = None
        else:
            notes.append(f"Financials reported in {ccy}; converted to USD at current rate {fxr:.4f}.")
    m["fx_rate"] = fxr

    def usd(v: Optional[float]) -> Optional[float]:
        return v * fxr if is_num(v) and is_num(fxr) else None

    ref = reference_end(fund)
    m["period_end"] = ref
    if ref is None:
        missing.append("revenue")
        m["data_quality"] = {"missing": missing, "notes": notes, "usable": False}
        return m
    prior = _shift(ref, -364)
    prior2 = _shift(ref, -728)

    flows: Dict[str, Optional[float]] = {}
    methods = {}
    for k in MONEY_DURATION:
        v, meth = ttm_at(fund, k, ref)
        flows[k] = usd(v)
        methods[k] = meth
        flows[k + "_prior"] = usd(ttm_at(fund, k, prior)[0])
        flows[k + "_prior2"] = usd(ttm_at(fund, k, prior2)[0])
    m["ttm_method"] = methods["revenue"]
    if methods["revenue"] == "annual":
        notes.append("Only annual revenue available (no quarterly XBRL); TTM = latest fiscal year.")

    rev, rev_p = flows["revenue"], flows["revenue_prior"]
    m["revenue_ttm"] = rev
    m["revenue_ttm_prior"] = rev_p
    m["revenue_growth_ttm"] = growth(rev, rev_p)
    m["revenue_growth_prior_ttm"] = growth(rev_p, flows["revenue_prior2"])

    # gross profit fallback
    gp = flows["gross_profit"]
    if gp is None and is_num(rev) and is_num(flows["cost_of_revenue"]):
        gp = rev - flows["cost_of_revenue"]
    gp_p = flows["gross_profit_prior"]
    if gp_p is None and is_num(rev_p) and is_num(flows["cost_of_revenue_prior"]):
        gp_p = rev_p - flows["cost_of_revenue_prior"]
    m["gross_margin"] = safe_div(gp, rev)
    m["gross_margin_prior"] = safe_div(gp_p, rev_p)
    m["operating_income_ttm"] = flows["operating_income"]
    m["operating_margin"] = safe_div(flows["operating_income"], rev)
    m["operating_margin_prior"] = safe_div(flows["operating_income_prior"], rev_p)
    m["net_income_ttm"] = flows["net_income"]
    m["net_margin"] = safe_div(flows["net_income"], rev)

    cfo, capex = flows["cfo"], flows["capex"]
    fcf = cfo - capex if is_num(cfo) and is_num(capex) else (cfo if is_num(cfo) and capex is None else None)
    if is_num(cfo) and capex is None:
        notes.append("Capex not tagged; FCF shown equals operating cash flow (overstated).")
    m["cfo_ttm"] = cfo
    m["capex_ttm"] = capex
    m["capex_pct"] = safe_div(capex, rev)
    m["fcf_ttm"] = fcf
    m["fcf_margin"] = safe_div(fcf, rev)
    m["sbc_ttm"] = flows["sbc"]
    m["sbc_pct"] = safe_div(flows["sbc"], rev)
    m["fcf_after_sbc"] = fcf - flows["sbc"] if is_num(fcf) and is_num(flows["sbc"]) else None
    m["fcf_after_sbc_margin"] = safe_div(m["fcf_after_sbc"], rev)
    m["fcf_conversion"] = safe_div(fcf, flows["net_income"]) if is_num(flows["net_income"]) and flows["net_income"] > 0 else None
    m["da_ttm"] = flows["da"]
    m["ebitda_ttm"] = (flows["operating_income"] + flows["da"]) if is_num(flows["operating_income"]) and is_num(flows["da"]) else None
    m["rnd_pct"] = safe_div(flows["rnd"], rev)
    m["interest_expense_ttm"] = flows["interest_expense"]
    m["interest_coverage"] = safe_div(flows["operating_income"], flows["interest_expense"]) if is_num(flows["interest_expense"]) and flows["interest_expense"] > 0 else None
    acq = sum(v for v in (flows["acquisitions"], flows["acquisitions_prior"]) if is_num(v))
    m["acquisitions_2y_pct_revenue"] = safe_div(acq, rev) if is_num(rev) else None
    m["buybacks_ttm"] = flows["buybacks"]
    m["dividends_ttm"] = flows["dividends_paid"]

    pretax, tax = flows["pretax_income"], flows["income_tax"]
    vcfg = cfg["valuation"]
    eff = safe_div(tax, pretax) if is_num(pretax) and pretax > 0 else None
    m["tax_rate_effective"] = eff
    m["tax_rate_model"] = clamp(eff, vcfg["tax_rate_min"], vcfg["tax_rate_max"]) if is_num(eff) else vcfg["tax_rate_default"]

    # ---- revenue history & growth durability --------------------------------------------
    qrev = fund.get("quarterly", {}).get("revenue", [])
    qhist = []
    for q in qrev:
        yago = next((p for p in qrev if _near(p["end"], _shift(q["end"], -364), 15)), None)
        qhist.append({"end": q["end"], "revenue": usd(q["val"]), "derived": bool(q.get("derived")),
                      "yoy": growth(q["val"], yago["val"]) if yago else None})
    m["quarterly_revenue"] = qhist[-12:]
    yoys = [q["yoy"] for q in qhist[-8:] if is_num(q["yoy"])]
    m["quarterly_yoy_last8"] = yoys
    if len(yoys) >= 4:
        m["growth_acceleration"] = (sum(yoys[-2:]) / 2) - (sum(yoys[-4:-2]) / 2)
    else:
        m["growth_acceleration"] = None
    m["growth_volatility"] = stdev(yoys)

    arev = fund.get("annual", {}).get("revenue", [])
    m["annual_revenue"] = [{"end": a["end"], "revenue": usd(a["val"])} for a in arev[-6:]]
    m["revenue_cagr_3y"] = cagr(arev[-1]["val"], arev[-4]["val"], 3) if len(arev) >= 4 else None
    m["revenue_cagr_5y"] = cagr(arev[-1]["val"], arev[-6]["val"], 5) if len(arev) >= 6 else None

    def annual_series(metric):
        return {a["end"]: usd(a["val"]) for a in fund.get("annual", {}).get(metric, [])}
    aop, acfo, acap = annual_series("operating_income"), annual_series("cfo"), annual_series("capex")
    m["annual_financials"] = [
        {"end": a["end"], "revenue": usd(a["val"]), "operating_income": aop.get(a["end"]),
         "fcf": (acfo[a["end"]] - acap[a["end"]]) if is_num(acfo.get(a["end"])) and is_num(acap.get(a["end"])) else None}
        for a in arev[-6:]
    ]
    hist_m = [safe_div(r["operating_income"], r["revenue"]) for r in m["annual_financials"][-5:]
              if is_num(r["operating_income"]) and is_num(r["revenue"]) and r["revenue"] > 0]
    m["op_margin_min_5y"] = min(hist_m) if hist_m else None
    yoy12 = [q["yoy"] for q in qhist[-12:] if is_num(q["yoy"])]
    m["growth_range_12q"] = (max(yoy12) - min(yoy12)) if len(yoy12) >= 6 else None
    m["op_margin_range_5y"] = (max(hist_m) - min(hist_m)) if len(hist_m) >= 3 else None
    m["cyclical_signal"] = bool((is_num(m["growth_range_12q"]) and m["growth_range_12q"] > 0.35) or
                                (is_num(m["op_margin_range_5y"]) and m["op_margin_range_5y"] > 0.15))

    # ---- balance sheet --------------------------------------------------------------------
    bs_dates = [r["end"] for k in ("total_assets", "equity", "cash") for r in fund.get("instant", {}).get(k, [])]
    bs_end = max(bs_dates) if bs_dates else ref
    m["balance_sheet_date"] = bs_end

    def inst(metric, end=bs_end, tol=200):
        r = instant_at(fund, metric, end, tol)
        return usd(r["val"]) if r else None

    cash = inst("cash", tol=5)
    sti = inst("st_investments", tol=5)
    cash_total = (cash or 0.0) + (sti or 0.0) if is_num(cash) else None
    debt, debt_method = None, "missing"
    total = inst("lt_debt_total", tol=5)
    if is_num(total):
        debt, debt_method = total, "LongTermDebt (incl. current)"
    else:
        parts = [inst("lt_debt_noncurrent", tol=5), inst("lt_debt_current", tol=5)]
        if any(is_num(p) for p in parts):
            debt, debt_method = sum(p for p in parts if is_num(p)), "noncurrent + current"
        conv = inst("convertible_debt", tol=5)
        if is_num(conv) and not any(is_num(p) for p in parts):
            debt, debt_method = conv, "convertible notes"
    std = inst("short_term_debt", tol=5)
    if is_num(std):
        debt = (debt or 0.0) + std
    if debt is None:
        # no debt tags at the latest balance sheet date: zero only if the company never tagged debt
        ever = any(fund.get("instant", {}).get(k) for k in ("lt_debt_total", "lt_debt_noncurrent", "convertible_debt"))
        if not ever:
            debt, debt_method = 0.0, "no debt reported"
        else:
            old = inst("lt_debt_total", tol=200) or inst("lt_debt_noncurrent", tol=200)
            if is_num(old):
                debt, debt_method = old, "last reported (stale)"
                notes.append("Debt not tagged at latest balance sheet date; using last reported value.")
    m["cash_and_investments"] = cash_total
    m["debt"] = debt
    m["debt_method"] = debt_method
    m["net_cash"] = cash_total - debt if is_num(cash_total) and is_num(debt) else None
    m["equity"] = inst("equity", tol=5)
    m["goodwill"] = inst("goodwill", tol=5)
    ca, cl = inst("current_assets", tol=5), inst("current_liabilities", tol=5)
    m["current_ratio"] = safe_div(ca, cl)
    m["net_debt_to_ebitda"] = safe_div(-m["net_cash"], m["ebitda_ttm"]) if is_num(m["net_cash"]) and is_num(m["ebitda_ttm"]) and m["ebitda_ttm"] > 0 else None
    if is_num(fcf) and fcf < 0 and is_num(cash_total):
        m["cash_runway_years"] = cash_total / -fcf
    else:
        m["cash_runway_years"] = None

    rpo_now = instant_at(fund, "rpo", bs_end, 5)
    rpo_old = instant_at(fund, "rpo", _shift(bs_end, -364), 30)
    m["rpo"] = usd(rpo_now["val"]) if rpo_now else None
    m["rpo_growth"] = growth(rpo_now["val"], rpo_old["val"]) if rpo_now and rpo_old else None

    # ---- shares & dilution ----------------------------------------------------------------
    asof = today.isoformat()
    dq = fund.get("quarterly", {}).get("diluted_shares", [])
    da_ = fund.get("annual", {}).get("diluted_shares", [])
    latest_dil = dq[-1] if dq else (da_[-1] if da_ else None)
    so = fund.get("instant", {}).get("shares_outstanding", [])
    latest_so = so[-1] if so else None
    dil = latest_dil["val"] * split_factor(splits, latest_dil["end"], latest_dil.get("filed"), asof) if latest_dil else None
    sout = latest_so["val"] * split_factor(splits, latest_so["end"], latest_so.get("filed"), asof) if latest_so else None
    if latest_so and (days_between(latest_so["end"], asof) or 999) > 220:
        sout = None
    shares = max(v for v in (dil, sout) if is_num(v)) if any(is_num(v) for v in (dil, sout)) else None
    m["diluted_shares"] = dil
    m["shares_outstanding"] = sout
    m["share_count_basis"] = "SEC: max(latest diluted weighted avg, cover-page shares outstanding)"
    if dq:
        old = next((q for q in reversed(dq) if _near(q["end"], _shift(dq[-1]["end"], -364), 15)), None)
        if old:
            adj_old = old["val"] * split_factor(splits, old["end"], old.get("filed"), asof)
            m["share_growth_yoy"] = growth(dil, adj_old)
        else:
            m["share_growth_yoy"] = None
    else:
        m["share_growth_yoy"] = None

    # ---- invested capital / returns -------------------------------------------------------
    eq = m["equity"]
    ic = (eq + (debt or 0) - (cash_total or 0)) if is_num(eq) else None
    m["invested_capital"] = ic
    nopat = flows["operating_income"] * (1 - m["tax_rate_model"]) if is_num(flows["operating_income"]) else None
    m["roic"] = safe_div(nopat, ic) if is_num(ic) and ic > 0 else None
    if is_num(ic) and ic > 0 and is_num(rev):
        s2c = rev / ic
    else:
        s2c = vcfg["sales_to_capital_max"]  # asset-light / net-cash-rich: little capital needed to grow
    m["sales_to_capital"] = clamp(s2c, vcfg["sales_to_capital_min"], vcfg["sales_to_capital_max"])

    # ---- market data ----------------------------------------------------------------------
    closes = px.get("close", []) if px else []
    price = closes[-1] if closes else None
    m["price"] = price
    m["price_date"] = px["dates"][-1] if closes else None
    if price is None:
        missing.append("price")
    yahoo_shares = info.get("sharesOutstanding")
    yahoo_mcap = info.get("marketCap")
    mcap = price * shares if is_num(price) and is_num(shares) else None
    if is_num(mcap) and is_num(yahoo_mcap) and yahoo_mcap > 0:
        ratio = mcap / yahoo_mcap
        if not 0.75 <= ratio <= 1.33 and is_num(yahoo_shares):
            notes.append(f"SEC share count implies market cap {ratio:.2f}x Yahoo's (ADR ratio or share classes); "
                         "using Yahoo share count for per-share values.")
            shares = float(yahoo_shares)
            mcap = price * shares
            m["share_count_basis"] = "Yahoo sharesOutstanding (SEC count inconsistent with listed security)"
    elif mcap is None and is_num(price) and is_num(yahoo_shares):
        shares = float(yahoo_shares)
        mcap = price * shares
        m["share_count_basis"] = "Yahoo sharesOutstanding (SEC share count unavailable)"
    m["shares_for_valuation"] = shares
    m["market_cap"] = mcap
    ev = mcap - (cash_total or 0) + (debt or 0) if is_num(mcap) else None
    m["enterprise_value"] = ev
    m["ev_sales_ttm"] = safe_div(ev, rev)
    m["ev_ebit_ttm"] = safe_div(ev, flows["operating_income"]) if is_num(flows["operating_income"]) and flows["operating_income"] > 0 else None
    m["ev_ebitda_ttm"] = safe_div(ev, m["ebitda_ttm"]) if is_num(m["ebitda_ttm"]) and m["ebitda_ttm"] > 0 else None
    m["pe_ttm"] = safe_div(mcap, flows["net_income"]) if is_num(flows["net_income"]) and flows["net_income"] > 0 else None
    m["p_fcf_ttm"] = safe_div(mcap, fcf) if is_num(fcf) and fcf > 0 else None
    m["fcf_yield"] = safe_div(fcf, mcap)

    # ---- consensus (Yahoo, unofficial) ----------------------------------------------------
    gcy, gny = est.get("rev_growth_cy"), est.get("rev_growth_ny")
    fwd = [g for g in (gcy, gny) if is_num(g)]
    m["consensus_rev_growth_cy"] = gcy
    m["consensus_rev_growth_ny"] = gny
    m["consensus_growth_ntm"] = sum(fwd) / len(fwd) if fwd else None
    m["consensus_analysts"] = est.get("rev_analysts")
    m["eps_revision_30d"] = est.get("eps_revision_30d")
    m["eps_revision_90d"] = est.get("eps_revision_90d")
    m["next_earnings"] = est.get("next_earnings")
    usd_eps = (info.get("financialCurrency") or info.get("currency") or "USD") == "USD"
    eps_ny = est.get("eps_ny") if usd_eps else None
    m["pe_forward"] = safe_div(price, eps_ny) if is_num(eps_ny) and eps_ny > 0 else None
    eg = est.get("eps_growth_ny")
    m["peg"] = safe_div(m["pe_forward"], eg * 100) if is_num(m["pe_forward"]) and is_num(eg) and eg > 0.02 else None
    m["short_pct_float"] = info.get("shortPercentOfFloat")
    m["institutional_pct"] = info.get("heldPercentInstitutions")
    m["estimates_source"] = est.get("source") if est else None
    if not fwd:
        missing.append("consensus_estimates")

    # ---- price performance ----------------------------------------------------------------
    m["return_1m"] = price_return(px, 21)
    m["return_3m"] = price_return(px, 63)
    m["return_6m"] = price_return(px, 126)
    m["return_12m"] = price_return(px, 252)
    hi52 = max(closes[-252:]) if closes else None
    m["off_52w_high"] = growth(price, hi52)
    m["beta"] = beta(px, bench)
    m["price_weekly"] = weekly(px, 3.0)

    # ---- historical valuation (own EV/Sales range, last ~5y) ----------------------------
    hist = []
    for q in qrev:
        end = q["end"]
        r_ttm = ttm_at(fund, "revenue", end)[0]
        p = price_on_or_before(px, end)
        dsh = next((s for s in dq if s["end"] == end), None)
        if not (is_num(r_ttm) and is_num(p) and dsh):
            continue
        sh = dsh["val"] * split_factor(splits, dsh["end"], dsh.get("filed"), asof)
        if m["share_count_basis"].startswith("Yahoo") and is_num(dil) and dil > 0:
            sh = sh * shares / dil  # keep the ADR/class ratio consistent through time
        c = instant_at(fund, "cash", end, 5)
        si = instant_at(fund, "st_investments", end, 5)
        dt = instant_at(fund, "lt_debt_total", end, 5)
        cash_h = usd((c["val"] if c else 0) + (si["val"] if si else 0)) or 0
        debt_h = usd(dt["val"]) if dt else 0
        evh = p * sh - cash_h + (debt_h or 0)
        r_usd = usd(r_ttm)
        if is_num(r_usd) and r_usd > 0:
            hist.append({"end": end, "ev_sales": evh / r_usd})
    hist = [h for h in hist if days_between(h["end"], asof) <= 365 * 5 + 30]
    vals = [h["ev_sales"] for h in hist]
    m["ev_sales_history"] = hist
    m["ev_sales_hist_min"] = min(vals) if vals else None
    m["ev_sales_hist_median"] = median(vals)
    m["ev_sales_hist_max"] = max(vals) if vals else None
    m["ev_sales_hist_percentile"] = percentile_rank(m["ev_sales_ttm"], vals) if len(vals) >= 6 else None

    # ---- data quality ---------------------------------------------------------------------
    for k in ("operating_income_ttm", "cfo_ttm", "cash_and_investments", "debt", "shares_for_valuation", "gross_margin"):
        if m.get(k) is None:
            missing.append(k)
    age = days_between(ref, asof)
    px_age = days_between(m["price_date"], asof) if m["price_date"] else None
    limit = cfg["screen"]["max_fundamentals_age_days"]
    if m["ttm_method"] == "annual":  # e.g. 20-F filers that only tag annual reports in XBRL
        limit = cfg["screen"].get("max_fundamentals_age_days_annual_only", limit)
    stale = age is None or age > limit
    if stale:
        notes.append(f"Latest reported period ends {ref} ({age} days ago) — fundamentals are stale.")
    if px_age is not None and px_age > 5:
        notes.append(f"Latest price is from {m['price_date']} ({px_age} days old).")
    m["data_quality"] = {
        "missing": sorted(set(missing)),
        "notes": notes,
        "fundamentals_age_days": age,
        "price_age_days": px_age,
        "stale": stale or (px_age is not None and px_age > 5),
        "usable": not stale and is_num(price) and is_num(rev) and is_num(shares) and rev > 0,
    }
    return m
