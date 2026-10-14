"""Initial screen. Thresholds are preferences: exceptional smaller companies pass via a flagged exception."""
from __future__ import annotations

from typing import Any, Dict, List, Tuple

from .util import is_num


def initial_screen(m: Dict[str, Any], cfg: Dict[str, Any]) -> Tuple[bool, List[str], bool]:
    s = cfg["screen"]
    reasons: List[str] = []
    dq = m.get("data_quality", {})
    if not dq.get("usable"):
        return False, ["unusable or stale data: " + ", ".join(dq.get("missing", []) or dq.get("notes", [])[:1])], False
    rev, g, mcap = m.get("revenue_ttm"), m.get("revenue_growth_ttm"), m.get("market_cap")
    fwd = m.get("consensus_growth_ntm")
    growth_for_screen = max(x for x in (g, fwd) if is_num(x)) if any(is_num(x) for x in (g, fwd)) else None
    exception = False
    ok = True
    if not is_num(mcap) or mcap < s["min_market_cap"]:
        ok = False
        reasons.append("market cap below minimum")
    if not is_num(growth_for_screen) or growth_for_screen < s["min_revenue_growth"]:
        ok = False
        reasons.append("revenue growth below minimum")
    if not is_num(rev) or rev < s["min_revenue_ttm"]:
        exc = s["small_company_exception"]
        if is_num(rev) and rev >= exc["min_revenue_ttm"] and is_num(growth_for_screen) and growth_for_screen >= exc["min_revenue_growth"]:
            exception = True
            reasons.append("small-company exception (revenue below preference, growth exceptional) — higher risk")
        else:
            ok = False
            reasons.append("revenue below minimum")
    return ok, reasons, exception
