"""Recommendation log + forward performance tracking.

Every run appends what it recommended *before* any later price is known (append-only JSONL). Performance
is measured forward from the recorded price, net of an assumed round-trip transaction cost, against
SPY and QQQ. This is paper tracking of live recommendations — not a backtest.
"""
from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from .metrics import price_on_or_before
from .util import is_num, parse_date

HORIZONS = {"1m": 30, "3m": 91, "6m": 182, "12m": 365, "24m": 730}
TIER_ORDER = ["Tier 1", "Tier 2", "Tier 3", "Avoid"]
COST = 0.002  # 0.2% round-trip transaction cost assumption


def append(path: str | Path, rows: List[Dict[str, Any]]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    existing_keys = set()
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
                existing_keys.add((r["date"], r["ticker"]))
            except (ValueError, KeyError):
                continue
    with open(p, "a", encoding="utf-8") as fh:
        for r in rows:
            if (r["date"], r["ticker"]) in existing_keys:
                continue  # re-runs on the same day never overwrite the original record
            fh.write(json.dumps(r, separators=(",", ":")) + "\n")


def load(path: str | Path) -> List[Dict[str, Any]]:
    p = Path(path)
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def events(log: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """A recommendation event = first day a ticker appears in a tier (or changes tier)."""
    log = sorted(log, key=lambda r: (r["date"], r["ticker"]))
    last_tier: Dict[str, str] = {}
    out = []
    for r in log:
        if last_tier.get(r["ticker"]) != r["tier"]:
            out.append(r)
        last_tier[r["ticker"]] = r["tier"]
    return out


def _ret(px, start: str, end: str) -> Optional[float]:
    a, b = price_on_or_before(px, start), price_on_or_before(px, end)
    return b / a - 1 if is_num(a) and is_num(b) and a > 0 else None


def performance(log: List[Dict[str, Any]], prices: Dict[str, Dict[str, List]], asof: str) -> Dict[str, Any]:
    ev = events(log)
    spy, qqq = prices.get("SPY"), prices.get("QQQ")
    rows = []
    for e in ev:
        px = prices.get(e["ticker"])
        if not px or not is_num(e.get("price")):
            continue
        d0 = e["date"]
        row = {"date": d0, "ticker": e["ticker"], "tier": e["tier"], "action": e.get("action"), "entry_price": e["price"],
               "score": e.get("score"), "returns": {}, "excess_spy": {}, "excess_qqq": {}}
        for h, days in HORIZONS.items():
            end = (parse_date(d0) + timedelta(days=days)).isoformat()
            if end > asof:
                continue
            p1 = price_on_or_before(px, end)
            if not is_num(p1):
                continue
            r = p1 / e["price"] - 1 - COST
            row["returns"][h] = r
            rs, rq = _ret(spy, d0, end), _ret(qqq, d0, end)
            row["excess_spy"][h] = r - rs if is_num(rs) else None
            row["excess_qqq"][h] = r - rq if is_num(rq) else None
        # since-recommendation return and max drawdown
        dates, close = px["dates"], px["close"]
        seg = [c for d, c in zip(dates, close) if d >= d0]
        if seg:
            row["return_to_date"] = seg[-1] / e["price"] - 1 - COST
            peak, mdd = e["price"], 0.0
            for c in seg:
                peak = max(peak, c)
                mdd = min(mdd, c / peak - 1)
            row["max_drawdown"] = mdd
            rs = _ret(spy, d0, asof)
            row["excess_spy_to_date"] = row["return_to_date"] - rs if is_num(rs) else None
        rows.append(row)

    by_tier = {}
    for t in TIER_ORDER:
        tr = [r for r in rows if r["tier"] == t]
        stats = {"count": len(tr), "horizons": {}}
        for h in list(HORIZONS) + ["to_date"]:
            if h == "to_date":
                vals = [r.get("return_to_date") for r in tr if is_num(r.get("return_to_date"))]
                exc = [r.get("excess_spy_to_date") for r in tr if is_num(r.get("excess_spy_to_date"))]
            else:
                vals = [r["returns"][h] for r in tr if h in r["returns"]]
                exc = [r["excess_spy"][h] for r in tr if is_num(r["excess_spy"].get(h))]
            if vals:
                stats["horizons"][h] = {"n": len(vals), "avg_return": sum(vals) / len(vals),
                                        "avg_excess_spy": sum(exc) / len(exc) if exc else None,
                                        "pct_beating_spy": sum(1 for x in exc if x > 0) / len(exc) if exc else None}
        by_tier[t] = stats
    best = sorted([r for r in rows if is_num(r.get("return_to_date"))], key=lambda r: r["return_to_date"], reverse=True)
    return {"asof": asof, "events": rows, "by_tier": by_tier,
            "best": best[:10], "worst": best[-10:][::-1] if best else [],
            "first_record": min((r["date"] for r in log), default=None),
            "basis": ("Live paper tracking: each recommendation is logged with its price before later prices are known. "
                      f"Returns are net of an assumed {COST:.1%} round-trip cost and use closing prices. "
                      "This is not a backtest and no hypothetical history is shown.")}
