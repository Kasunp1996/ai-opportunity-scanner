"""Risk engine: measurable risk flags, the five most important risks, adverse-case downside."""
from __future__ import annotations

from typing import Any, Dict, List

from .util import is_num

AI_CYCLE_SEGMENTS = {"Semiconductors", "Semiconductor Equipment", "Networking & Optical",
                     "Compute & Storage Hardware", "Power, Cooling & Components"}

SECTOR_RISKS = {
    "Semiconductors": [("Export controls & geopolitics", "Restrictions on China sales and Taiwan-centred manufacturing can cut addressable demand or supply."),
                       ("Cyclical inventory correction", "Semiconductor demand is cyclical; customer inventory digestion can drop revenue sharply.")],
    "Semiconductor Equipment": [("Wafer-fab capex cycle", "Orders track customers' capital budgets, which swing with memory/foundry pricing."),
                                ("Export controls", "China is a large equipment market and subject to tightening U.S. controls.")],
    "Networking & Optical": [("Hyperscaler capex concentration", "A handful of cloud customers drive AI networking demand; a capex pause hits quickly."),
                             ("Technology transitions", "Speed upgrades (800G to 1.6T, copper vs optics) can shift share between suppliers.")],
    "Compute & Storage Hardware": [("Low-margin competition", "Hardware integration is competitive with thin margins and component cost swings."),
                                   ("Customer concentration", "Large AI server orders can be lumpy and concentrated.")],
    "Power, Cooling & Components": [("Data-center build-out timing", "Revenue depends on data-center construction schedules and power availability."),
                                    ("Supply chain constraints", "Component shortages can delay shipments and pressure margins.")],
    "Software & Cloud": [("AI disruption of pricing models", "AI agents may compress seat-based pricing or let competitors replicate features."),
                         ("Spending scrutiny", "Enterprise budget tightening lengthens sales cycles and slows net expansion.")],
    "Cybersecurity": [("Platform consolidation", "Buyers consolidating vendors favours the largest platforms and pressures point products."),
                      ("Pricing pressure", "Bundling by large platforms can compress pricing.")],
}
GENERIC_RISKS = [("Customer concentration (check 10-K)", "Major-customer concentration is not available in structured XBRL; review the 10-K risk factors."),
                 ("Execution risk", "Forecasts assume management delivers product roadmaps and margin targets.")]


def assess(m: Dict[str, Any], val: Dict[str, Any], cfg: Dict[str, Any]) -> Dict[str, Any]:
    flags: Dict[str, Dict[str, str]] = {}

    def flag(name: str, severity: str, detail: str) -> None:
        cur = flags.get(name)
        if cur is None or (cur["severity"] == "medium" and severity == "high"):
            flags[name] = {"severity": severity, "detail": detail}

    evs, eve = m.get("ev_sales_ttm"), m.get("ev_ebit_ttm")
    rev = (val or {}).get("reverse_dcf") or {}
    implied = rev.get("implied_growth")
    g_base = (val or {}).get("initial_growth")
    pct = m.get("ev_sales_hist_percentile")
    if (is_num(evs) and evs > 30) or (is_num(eve) and eve > 80) or rev.get("bound") == "above" or (is_num(implied) and implied > 0.45):
        flag("excessive_valuation", "high", f"EV/Sales {evs:.1f}x" if is_num(evs) else "price implies extreme growth")
    elif (is_num(evs) and evs > 15) or (is_num(implied) and is_num(g_base) and implied > g_base + 0.10) or (is_num(pct) and pct > 0.9):
        bits = []
        if is_num(evs) and evs > 15:
            bits.append(f"EV/Sales {evs:.1f}x")
        if is_num(implied) and is_num(g_base) and implied > g_base + 0.10:
            bits.append(f"price implies {implied:.0%} initial growth vs {g_base:.0%} modelled")
        if is_num(pct) and pct > 0.9:
            bits.append(f"EV/Sales in the top {1 - pct:.0%} of its 5-year range")
        flag("excessive_valuation", "medium", "; ".join(bits))

    nde, runway, cov, cr = m.get("net_debt_to_ebitda"), m.get("cash_runway_years"), m.get("interest_coverage"), m.get("current_ratio")
    net_borrower = is_num(m.get("net_cash")) and m["net_cash"] < 0
    if (is_num(nde) and nde > 4) or (is_num(runway) and runway < 1.5) or (is_num(cov) and cov < 2 and net_borrower):
        flag("weak_balance_sheet", "high", f"net debt/EBITDA {nde:.1f}x" if is_num(nde) else
             (f"cash runway {runway:.1f} years" if is_num(runway) else f"interest coverage {cov:.1f}x"))
    elif (is_num(nde) and nde > 2.5) or (is_num(runway) and runway < 3) or (is_num(cr) and cr < 1.0):
        flag("weak_balance_sheet", "medium", f"net debt/EBITDA {nde:.1f}x" if is_num(nde) and nde > 2.5 else
             (f"cash runway {runway:.1f} years" if is_num(runway) and runway < 3 else f"current ratio {cr:.2f}"))
    if (m.get("operating_income_ttm") or 0) <= 0 and is_num(m.get("net_cash")) and m["net_cash"] < 0:
        flag("weak_balance_sheet", "high", "unprofitable with net debt")

    sg, sbc = m.get("share_growth_yoy"), m.get("sbc_pct")
    if (is_num(sg) and sg > 0.06) or (is_num(sbc) and sbc > 0.25):
        flag("high_dilution", "high", f"share count +{sg:.1%} YoY" if is_num(sg) and sg > 0.06 else f"SBC {sbc:.0%} of revenue")
    elif (is_num(sg) and sg > 0.03) or (is_num(sbc) and sbc > 0.15):
        flag("high_dilution", "medium", f"share count +{sg:.1%} YoY" if is_num(sg) and sg > 0.03 else f"SBC {sbc:.0%} of revenue")

    opm, vol = m.get("operating_margin"), m.get("growth_volatility")
    no_cons = not is_num(m.get("consensus_growth_ntm"))
    if (is_num(opm) and opm < -0.10) or (no_cons and is_num(vol) and vol > 0.20):
        flag("uncertain_forecast", "high", "deeply unprofitable" if is_num(opm) and opm < -0.10 else "volatile growth and no consensus")
    elif (is_num(opm) and opm < 0) or (is_num(vol) and vol > 0.15) or no_cons or m.get("ttm_method") == "annual":
        why = []
        if is_num(opm) and opm < 0:
            why.append("operating loss")
        if is_num(vol) and vol > 0.15:
            why.append(f"volatile quarterly growth (sd {vol:.0%})")
        if no_cons:
            why.append("no consensus estimates")
        if m.get("ttm_method") == "annual":
            why.append("annual data only")
        flag("uncertain_forecast", "medium", ", ".join(why))

    if m.get("segment") in AI_CYCLE_SEGMENTS:
        flag("concentrated_ai_exposure", "medium", f"{m['segment']}: revenue tied to the AI/data-center capex cycle")

    acc = m.get("growth_acceleration")
    g_now, g_prev = m.get("revenue_growth_ttm"), m.get("revenue_growth_prior_ttm")
    yoy_decel = g_now - g_prev if is_num(g_now) and is_num(g_prev) else None
    if (is_num(acc) and acc < -0.15) or (is_num(yoy_decel) and yoy_decel < -0.20):
        flag("decelerating_growth", "high", f"YoY growth slowed {abs(acc) * 100:.0f} pts over two quarters"
             if is_num(acc) and acc < -0.15 else f"TTM growth {g_now:.0%} vs {g_prev:.0%} a year earlier")
    elif (is_num(acc) and acc < -0.08) or (is_num(yoy_decel) and yoy_decel < -0.08):
        flag("decelerating_growth", "medium", f"YoY growth slowed {abs(acc) * 100:.0f} pts over two quarters"
             if is_num(acc) and acc < -0.08 else f"TTM growth {g_now:.0%} vs {g_prev:.0%} a year earlier")

    acq = m.get("acquisitions_2y_pct_revenue")
    if is_num(acq) and acq > 0.25:
        flag("acquisition_driven_growth", "medium", f"acquisitions equal {acq:.0%} of revenue over two years")

    gm, gmp, opmp = m.get("gross_margin"), m.get("gross_margin_prior"), m.get("operating_margin_prior")
    if (is_num(gm) and is_num(gmp) and gm - gmp < -0.03) or (is_num(opm) and is_num(opmp) and opm - opmp < -0.05):
        flag("margin_deterioration", "medium", "gross or operating margin fell materially year over year")

    r6 = m.get("return_6m")
    weakening = ((is_num(opm) and is_num(opmp) and opm - opmp < -0.03) or (is_num(acc) and acc < -0.05)
                 or (is_num(yoy_decel) and yoy_decel < -0.08)
                 or (is_num(gm) and is_num(gmp) and gm - gmp < -0.02))
    if is_num(r6) and r6 > 0.20 and weakening:
        flag("price_up_fundamentals_down", "high", f"stock +{r6:.0%} in 6 months while margins/growth weakened")

    sc = (val or {}).get("scenarios")
    iv = ((val or {}).get("intrinsic") or {}).get("per_share")
    price = m.get("price")
    if sc and is_num(price) and price > 0:
        base24 = sc["cases"]["base"]["return_24m"]
        bull24 = sc["cases"]["bull"]["return_24m"]
        if base24 > 2.0 or bull24 > 5.0 or (is_num(iv) and iv / price > 3.0):
            flag("implausible_model_output", "high",
                 f"base case {base24:+.0%} / intrinsic {iv / price:.1f}x price — far outside normal ranges; "
                 "likely a cycle peak or data issue. Verify the inputs before relying on it."
                 if is_num(iv) else f"base case {base24:+.0%} — far outside normal ranges; verify inputs")
    if m.get("cyclical_signal"):
        flag("cyclical_business", "medium", "large swings in growth or margins over the last 5 years; "
             "valued on through-cycle margins")
    if sc and sc.get("optimistic_only"):
        flag("optimistic_assumptions_only", "high", "attractive return appears only in the bull case")

    dq = m.get("data_quality", {})
    if dq.get("missing"):
        flag("data_gaps", "medium", "missing: " + ", ".join(dq["missing"]))

    # ---- top five risks ----------------------------------------------------------------
    titles = {
        "excessive_valuation": "Valuation compression", "weak_balance_sheet": "Balance-sheet / financing risk",
        "high_dilution": "Dilution", "uncertain_forecast": "Forecast uncertainty",
        "concentrated_ai_exposure": "AI capex-cycle exposure", "decelerating_growth": "Slowing growth",
        "acquisition_driven_growth": "Acquisition-dependent growth", "margin_deterioration": "Margin deterioration",
        "price_up_fundamentals_down": "Price outrunning fundamentals",
        "optimistic_assumptions_only": "Thesis needs optimistic assumptions", "data_gaps": "Incomplete data",
        "implausible_model_output": "Model output outside normal ranges", "cyclical_business": "Cyclicality",
    }
    ordered = sorted(flags.items(), key=lambda kv: 0 if kv[1]["severity"] == "high" else 1)
    top: List[Dict[str, str]] = [{"title": titles.get(k, k), "detail": v["detail"], "severity": v["severity"],
                                  "basis": "computed from reported data"} for k, v in ordered if k != "data_gaps"]
    for t, d in SECTOR_RISKS.get(m.get("segment"), []) + GENERIC_RISKS:
        if len(top) >= 5:
            break
        top.append({"title": t, "detail": d, "severity": "context", "basis": "general sector risk (not company data)"})

    bear = sc["cases"]["bear"] if sc else None
    return {
        "flags": flags,
        "high_flags": [k for k, v in flags.items() if v["severity"] == "high"],
        "top_risks": top[:5],
        "adverse_return_12m": bear["return_12m"] if bear else None,
        "adverse_return_24m": bear["return_24m"] if bear else None,
    }
