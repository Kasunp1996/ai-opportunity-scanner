"""Catalysts built only from verifiable inputs: SEC filings, scheduled earnings dates, analyst
estimate revisions, and the model's own expectation-gap calculation. Each is labelled by type:

  confirmed  - an SEC filing that exists (link + filing date)
  scheduled  - a dated event from the market-data provider
  analyst    - consensus estimates / revisions (third-party opinion)
  reported   - a trend in the company's own reported numbers
  model      - the scanner's own calculation (a hypothesis, not a fact)
"""
from __future__ import annotations

from datetime import date
from typing import Any, Dict, List

from .util import days_between, is_num

MECH = {
    "2.02": "Results and guidance reset revenue/EPS expectations; beats with raised guidance tend to drive estimate revisions.",
    "1.01": "A material agreement can add contracted revenue or change cost structure.",
    "2.01": "Completed acquisition/disposal changes the revenue base and balance sheet; growth comparisons become inorganic.",
    "2.03": "New debt raises interest cost and financial risk, or funds growth/buybacks.",
    "3.02": "Equity issuance dilutes existing shareholders.",
    "5.02": "Leadership change can shift strategy and execution.",
    "1.05": "A cybersecurity incident can cause costs, customer churn and reputational damage.",
    "2.05": "Restructuring lowers future costs but signals pressure on the business.",
    "2.06": "Impairment indicates an asset or acquisition is worth less than its book value.",
}
NEGATIVE_ITEMS = {"3.02", "1.05", "2.05", "2.06", "4.02", "3.01", "1.02"}


def build(m: Dict[str, Any], events: List[Dict[str, Any]], val: Dict[str, Any], today: date) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    implied = ((val or {}).get("reverse_dcf") or {}).get("implied_growth")
    cons = m.get("consensus_growth_ntm")
    gap = cons - implied if is_num(cons) and is_num(implied) else None
    priced = ("market already prices in more growth than consensus" if is_num(gap) and gap < 0
              else "consensus growth is above what the price implies" if is_num(gap) else "unknown")

    ne = m.get("next_earnings")
    if ne:
        d = days_between(today, ne)
        if d is not None and d >= 0:
            out.append({"type": "scheduled", "title": f"Next earnings report {ne} (in {d} days)", "date": ne,
                        "days_away": d, "direction": "neutral",
                        "mechanism": MECH["2.02"], "priced_in": priced, "source": "Yahoo Finance calendar"})

    for e in events[:8]:
        for code, label in zip(e["items"], e["labels"]):
            if code in ("9.01", "7.01", "8.01") and len(e["items"]) > 1:
                continue
            out.append({"type": "confirmed", "title": f"8-K: {label}", "date": e["filed"],
                        "direction": "negative" if code in NEGATIVE_ITEMS else "neutral",
                        "mechanism": MECH.get(code, "Material event disclosed to investors; review the filing."),
                        "source": "SEC EDGAR", "url": e.get("url")})

    r90 = m.get("eps_revision_90d")
    if is_num(r90) and abs(r90) >= 0.03:
        up = r90 > 0
        out.append({"type": "analyst", "title": f"Next-year EPS estimate {'raised' if up else 'cut'} {abs(r90):.0%} over 90 days",
                    "direction": "positive" if up else "negative",
                    "mechanism": "Rising estimates lift the earnings base that valuation multiples are applied to; "
                                 "revision momentum often persists for several quarters.",
                    "priced_in": priced, "source": "Yahoo Finance EPS trend (consensus)"})

    acc, q = m.get("growth_acceleration"), m.get("quarterly_yoy_last8") or []
    if is_num(acc) and abs(acc) >= 0.03 and len(q) >= 4:
        up = acc > 0
        out.append({"type": "reported", "title": f"Revenue growth {'accelerating' if up else 'decelerating'}: "
                                                 f"{(q[-4] + q[-3]) / 2:.0%} → {(q[-2] + q[-1]) / 2:.0%} YoY",
                    "direction": "positive" if up else "negative",
                    "mechanism": "Acceleration tends to force upward estimate revisions; deceleration the reverse.",
                    "source": "SEC 10-Q/10-K (computed)"})

    rg, g = m.get("rpo_growth"), m.get("revenue_growth_ttm")
    if is_num(rg) and is_num(g) and rg - g > 0.05:
        out.append({"type": "reported", "title": f"Backlog (RPO) growing {rg:.0%} vs revenue {g:.0%}",
                    "direction": "positive",
                    "mechanism": "Contracted backlog growing faster than revenue supports future revenue growth.",
                    "source": "SEC 10-Q/10-K (computed)"})

    if is_num(gap) and abs(gap) >= 0.03:
        out.append({"type": "model", "title": f"Price implies {implied:.0%} initial growth vs consensus {cons:.0%}",
                    "direction": "positive" if gap > 0 else "negative",
                    "mechanism": "If the company delivers consensus, the market must re-price toward a higher implied "
                                 "growth rate (or the reverse). This is the scanner's own reverse-DCF calculation.",
                    "source": "Scanner reverse DCF (hypothesis)"})
    return out
