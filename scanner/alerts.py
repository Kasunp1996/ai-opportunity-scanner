"""Alert rules, evaluated by comparing today's analysis with the previous report."""
from __future__ import annotations

from typing import Any, Dict, List

from .util import is_num

WATCH_FLAGS = {"decelerating_growth", "margin_deterioration", "price_up_fundamentals_down", "weak_balance_sheet"}
TIER_RANK = {"Tier 1": 1, "Tier 2": 2, "Tier 3": 3, "Avoid": 4, "Insufficient data": 5}


def evaluate(rows: List[Dict[str, Any]], prev_rows: Dict[str, Dict[str, Any]], watchlist: List[str],
             earnings_days: int) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    watch = set(watchlist)
    for r in rows:
        t = r["ticker"]
        p = prev_rows.get(t)
        relevant = t in watch or r["tier"] in ("Tier 1", "Tier 2") or (p and p.get("tier") in ("Tier 1", "Tier 2"))

        if r["tier"] == "Tier 1" and (not p or p.get("tier") != "Tier 1"):
            out.append({"ticker": t, "kind": "new_tier1", "message": f"{t} now meets Tier 1 criteria (score {r['score']:.0f})."})
        if p and TIER_RANK.get(r["tier"], 9) > TIER_RANK.get(p.get("tier"), 9) and p.get("tier") in ("Tier 1", "Tier 2"):
            out.append({"ticker": t, "kind": "downgrade", "message": f"{t} downgraded {p['tier']} → {r['tier']}."})
        if relevant and r.get("entry_status") in ("in entry range", "below entry range") and \
                (not p or p.get("entry_status") not in ("in entry range", "below entry range")):
            out.append({"ticker": t, "kind": "entry", "message":
                        f"{t} at ${r['price']:.2f} is {r['entry_status']} (${r['entry_low']:.2f}–${r['entry_high']:.2f})."})
        d = r.get("earnings_in_days")
        if relevant and is_num(d) and 0 <= d <= earnings_days:
            out.append({"ticker": t, "kind": "earnings", "message": f"{t} reports earnings in {d} days ({r.get('next_earnings')})."})
        new_flags = set(r.get("high_flags", [])) - set((p or {}).get("high_flags", []))
        for f in sorted(new_flags & WATCH_FLAGS):
            if relevant:
                out.append({"ticker": t, "kind": "deterioration", "message": f"{t}: new high-severity flag '{f.replace('_', ' ')}'."})
        if p and relevant and is_num(p.get("intrinsic")) and is_num(r.get("intrinsic")) and p["intrinsic"] > 0:
            ch = r["intrinsic"] / p["intrinsic"] - 1
            if abs(ch) >= 0.20:
                out.append({"ticker": t, "kind": "valuation_change",
                            "message": f"{t} intrinsic value estimate moved {ch:+.0%} (${p['intrinsic']:.2f} → ${r['intrinsic']:.2f})."})
        inv = (p or {}).get("invalidation_price")
        if relevant and is_num(inv) and is_num(r.get("price")) and r["price"] < inv:
            out.append({"ticker": t, "kind": "invalidation",
                        "message": f"{t} at ${r['price']:.2f} is below its thesis-invalidation level ${inv:.2f} (prior bear case)."})
    return out


def markdown(alerts: List[Dict[str, Any]], date: str, site_url: str | None = None) -> str:
    if not alerts:
        return ""
    lines = [f"Scanner alerts for {date}", ""]
    for a in alerts:
        lines.append(f"- **{a['ticker']}** ({a['kind'].replace('_', ' ')}): {a['message']}")
    if site_url:
        lines += ["", f"Dashboard: {site_url}"]
    lines += ["", "_Model estimates for research only — not investment advice._"]
    return "\n".join(lines)
