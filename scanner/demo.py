"""Synthetic demo data — FICTIONAL companies only (tickers prefixed 'DX').

Purpose: run and test the complete pipeline offline. The generator emits raw SEC 'company facts'
JSON (10-Q quarter + year-to-date facts, 10-K annual facts, instants, cover-page shares) so the real
XBRL normaliser, YTD differencing and every engine are exercised exactly as with live data.
Nothing here describes a real company. Never mix demo output with live reports.
"""
from __future__ import annotations

import math
import random
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import xbrl
from .market import MarketData
from .pipeline import Source

DEMO_TODAY = date(2026, 10, 14)

#  ticker, name, segment, sic, ttm revenue ($B), yoy growth start→end (20 quarters), gross margin,
#  op margin start→end, fcf margin, sbc %, cash $B, debt $B, shares (M), annual dilution, current EV/S,
#  consensus growth (cy, ny), 90d EPS revision, days to earnings, 6m price return, extras
SPECS: List[Dict[str, Any]] = [
    dict(t="DXQF", n="Quanta Fabric", seg="Networking & Optical", sic=3576, rev=2.4, g=(0.22, 0.38), gm=0.62, opm=(0.15, 0.22), fcf=0.20, sbc=0.06, cash=1.5, debt=0.5, sh=300, dil=0.015, evs=5.0, cons=(0.36, 0.30), rv=0.08, ed=21, r6=0.05),
    dict(t="DXLM", n="Lumen Silicon", seg="Semiconductors", sic=3674, rev=9.0, g=(0.70, 0.50), gm=0.72, opm=(0.40, 0.46), fcf=0.38, sbc=0.07, cash=6.0, debt=1.5, sh=900, dil=0.01, evs=22.0, cons=(0.45, 0.30), rv=0.12, ed=35, r6=0.30),
    dict(t="DXCP", n="Corvid Power", seg="Power, Cooling & Components", sic=3585, rev=6.0, g=(0.20, 0.24), gm=0.36, opm=(0.14, 0.17), fcf=0.13, sbc=0.02, cash=0.8, debt=2.5, sh=380, dil=0.005, evs=3.4, cons=(0.22, 0.18), rv=0.04, ed=14, r6=0.10),
    dict(t="DXSE", n="Sentinel Edge", seg="Cybersecurity", sic=7372, rev=3.5, g=(0.33, 0.27), gm=0.78, opm=(-0.02, 0.05), fcf=0.30, sbc=0.18, cash=4.0, debt=2.0, sh=250, dil=0.02, evs=11.0, cons=(0.24, 0.22), rv=0.03, ed=42, r6=0.08, rpo=True),
    dict(t="DXHL", n="Helix Logic", seg="Software & Cloud", sic=7372, rev=1.6, g=(0.38, 0.31), gm=0.80, opm=(-0.15, -0.08), fcf=0.12, sbc=0.22, cash=1.1, debt=0.0, sh=160, dil=0.045, evs=9.0, cons=(0.29, 0.26), rv=-0.02, ed=28, r6=-0.10, k8=["3.02"]),
    dict(t="DXMB", n="Membrane Memory", seg="Semiconductors", sic=3674, rev=22.0, g=(-0.30, 0.45), gm=0.42, opm=(-0.05, 0.22), fcf=0.12, sbc=0.02, cash=9.0, debt=12.0, sh=1100, dil=0.01, evs=3.0, cons=(0.35, 0.10), rv=0.15, ed=70, r6=0.25),
    dict(t="DXWF", n="Wafercraft Tools", seg="Semiconductor Equipment", sic=3559, rev=4.2, g=(0.05, 0.14), gm=0.48, opm=(0.25, 0.27), fcf=0.22, sbc=0.03, cash=2.5, debt=1.0, sh=210, dil=0.0, evs=6.5, cons=(0.12, 0.13), rv=0.01, ed=9, r6=0.02),
    dict(t="DXNV", n="Novacloud Data", seg="Software & Cloud", sic=7374, rev=0.9, g=(0.60, 0.52), gm=0.70, opm=(-0.25, -0.15), fcf=-0.05, sbc=0.25, cash=1.2, debt=1.0, sh=140, dil=0.07, evs=15.0, cons=(0.45, 0.38), rv=0.0, ed=30, r6=0.40),
    dict(t="DXOR", n="Orbital Racks", seg="Compute & Storage Hardware", sic=3571, rev=12.0, g=(0.60, 0.20), gm=0.12, opm=(0.08, 0.04), fcf=0.01, sbc=0.01, cash=1.5, debt=2.0, sh=600, dil=0.01, evs=0.9, cons=(0.18, 0.12), rv=-0.06, ed=26, r6=0.40),
    dict(t="DXGR", n="Granite Networks", seg="Networking & Optical", sic=3661, rev=1.1, g=(0.06, 0.09), gm=0.55, opm=(0.12, 0.13), fcf=0.12, sbc=0.04, cash=0.4, debt=0.3, sh=120, dil=0.0, evs=2.5, cons=(0.08, 0.07), rv=0.0, ed=19, r6=0.0),
    dict(t="DXPL", n="Pallas Analytics", seg="Software & Cloud", sic=7372, rev=2.0, g=(0.12, 0.22), gm=0.74, opm=(0.10, 0.12), fcf=0.22, sbc=0.10, cash=0.5, debt=1.4, sh=200, dil=0.02, evs=6.0, cons=(0.18, 0.12), rv=0.0, ed=33, r6=-0.05, acq=0.35),
    dict(t="DXTR", n="Tessera Retail Tech", seg="Software & Cloud", sic=7373, rev=0.25, g=(0.10, 0.12), gm=0.60, opm=(0.05, 0.06), fcf=0.08, sbc=0.06, cash=0.1, debt=0.0, sh=80, dil=0.01, evs=3.0, cons=(0.10, 0.10), rv=0.0, ed=12, r6=0.0),
    dict(t="DXBK", n="Bastion Kinetics", seg="Power, Cooling & Components", sic=3612, rev=1.4, g=(0.10, 0.18), gm=0.24, opm=(0.03, 0.02), fcf=-0.03, sbc=0.03, cash=0.2, debt=3.5, sh=150, dil=0.03, evs=4.0, cons=(0.16, 0.15), rv=-0.04, ed=16, r6=-0.15, k8=["2.03"]),
    dict(t="DXAX", n="Axion Photonics", seg="Networking & Optical", sic=3661, rev=0.7, g=(0.40, 0.85), gm=0.60, opm=(0.05, 0.18), fcf=0.15, sbc=0.08, cash=0.6, debt=0.0, sh=90, dil=0.02, evs=12.0, cons=(0.70, 0.40), rv=0.20, ed=24, r6=0.35),
    dict(t="DXCL", n="Cirrus Ledger", seg="Software & Cloud", sic=7372, rev=5.0, g=(0.18, 0.16), gm=0.82, opm=(0.26, 0.30), fcf=0.35, sbc=0.09, cash=3.0, debt=1.0, sh=310, dil=0.0, evs=7.5, cons=(0.15, 0.14), rv=0.02, ed=40, r6=0.03, rpo=True),
    dict(t="DXVT", n="Vector Test Systems", seg="Instruments & Test", sic=3825, rev=2.8, g=(0.10, 0.19), gm=0.58, opm=(0.20, 0.24), fcf=0.20, sbc=0.03, cash=1.2, debt=0.6, sh=170, dil=0.0, evs=6.0, cons=(0.18, 0.16), rv=0.05, ed=8, r6=0.06),
    dict(t="DXSN", n="Synapse Semis", seg="Semiconductors", sic=3674, rev=1.9, g=(0.25, 0.33), gm=0.64, opm=(0.08, 0.12), fcf=0.14, sbc=0.09, cash=1.0, debt=0.2, sh=140, dil=0.02, evs=10.0, cons=(0.30, 0.28), rv=0.06, ed=29, r6=0.12),
    dict(t="DXQS", n="Quasar Storage", seg="Compute & Storage Hardware", sic=3572, rev=4.0, g=(0.15, 0.21), gm=0.70, opm=(0.12, 0.15), fcf=0.18, sbc=0.07, cash=1.8, debt=0.0, sh=330, dil=0.01, evs=5.5, cons=(0.19, 0.17), rv=0.03, ed=37, r6=0.04),
    dict(t="DXHP", n="Helvetia Photonics (ADR)", seg="Semiconductor Equipment", sic=3559, rev=3.2, g=(0.16, 0.20), gm=0.50, opm=(0.26, 0.28), fcf=0.20, sbc=0.02, cash=1.5, debt=0.8, sh=400, dil=0.0, evs=6.0, cons=(0.18, 0.15), rv=0.02, ed=50, r6=0.07, foreign=True),
    dict(t="DXEN", n="Ennead Systems", seg="Software & Cloud", sic=7372, rev=12.0, g=(0.09, 0.11), gm=0.76, opm=(0.28, 0.30), fcf=0.33, sbc=0.08, cash=8.0, debt=6.0, sh=700, dil=0.0, evs=8.0, cons=(0.10, 0.10), rv=0.01, ed=22, r6=0.05),
    dict(t="DXFL", n="Flux Interconnect", seg="Semiconductors", sic=3674, rev=1.3, g=(0.30, 0.48), gm=0.63, opm=(0.10, 0.20), fcf=0.18, sbc=0.10, cash=0.9, debt=0.0, sh=170, dil=0.025, evs=18.0, cons=(0.45, 0.35), rv=0.10, ed=31, r6=0.45),
    dict(t="DXCO", n="Cobalt Cooling", seg="Power, Cooling & Components", sic=3585, rev=0.6, g=(0.28, 0.35), gm=0.32, opm=(0.06, 0.09), fcf=0.07, sbc=0.03, cash=0.15, debt=0.2, sh=60, dil=0.01, evs=4.0, cons=(0.30, 0.25), rv=0.04, ed=18, r6=0.15),
]

QUARTER_ENDS = []  # 24 calendar quarters: 2020-09-30 .. 2026-06-30
_y, _q = 2020, 3
for _ in range(24):
    _m = _q * 3
    _d = {3: 31, 6: 30, 9: 30, 12: 31}[_m]
    QUARTER_ENDS.append(date(_y, _m, _d))
    _q += 1
    if _q == 5:
        _q, _y = 1, _y + 1


def _qstart(e: date) -> date:
    m = e.month - 2
    return date(e.year, m, 1)


def _filed(e: date, annual: bool) -> str:
    return (e + timedelta(days=60 if annual else 35)).isoformat()


def _business_days(start: date, end: date) -> List[date]:
    out, d = [], start
    while d <= end:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


DAYS = _business_days(DEMO_TODAY - timedelta(days=5 * 365 + 10), DEMO_TODAY)


class DemoCompany:
    def __init__(self, s: Dict[str, Any], idx: int):
        self.s = s
        self.rng = random.Random(1000 + idx)
        self.cik = 9000000 + idx
        self._build_series()

    def _build_series(self) -> None:
        s, rng = self.s, self.rng
        n = len(QUARTER_ENDS)
        g0, g1 = s["g"]
        yoy = [g0 + (g1 - g0) * max(0, i - 4) / (n - 5) for i in range(n)]
        rev = [1.0] * 4
        for i in range(4, n):
            rev.append(rev[i - 4] * (1 + yoy[i]) * (1 + rng.uniform(-0.01, 0.01)))
        scale = s["rev"] * 1e9 / sum(rev[-4:])
        self.rev = [r * scale for r in rev]
        o0, o1 = s["opm"]
        self.opm = [o0 + (o1 - o0) * i / (n - 1) for i in range(n)]
        self.dil_sh = [s["sh"] * 1e6 / (1 + s["dil"]) ** ((n - 1 - i) / 4) for i in range(n)]

    # ---- SEC company facts ------------------------------------------------------------------
    def companyfacts(self) -> Dict[str, Any]:
        s = self.s
        unit = "EUR" if s.get("foreign") else "USD"
        fx = 1 / 1.08 if s.get("foreign") else 1.0  # reported EUR amounts
        tax = "ifrs-full" if s.get("foreign") else "us-gaap"
        facts: Dict[str, Dict[str, Any]] = {"us-gaap": {}, "dei": {}, "ifrs-full": {}}

        def add(taxo, concept, u, fact):
            facts[taxo].setdefault(concept, {"units": {}})["units"].setdefault(u, []).append(fact)

        per_q: Dict[str, List[float]] = {k: [] for k in ("revenue", "cost", "gp", "opinc", "ni", "pretax", "taxexp",
                                                         "rnd", "cfo", "capex", "sbc", "da", "acq", "intexp")}
        for i, e in enumerate(QUARTER_ENDS):
            r = self.rev[i] * fx
            gp = r * (s["gm"] + self.rng.uniform(-0.005, 0.005))
            op = r * self.opm[i]
            capex_pct = 0.06 if s["sic"] < 7000 else 0.02
            capex = r * capex_pct
            cfo = r * s["fcf"] + capex
            intexp = s["debt"] * 1e9 * fx * 0.05 / 4
            pretax = op - intexp + s["cash"] * 1e9 * fx * 0.03 / 4
            taxexp = pretax * 0.16 if pretax > 0 else 0.0
            vals = {"revenue": r, "cost": r - gp, "gp": gp, "opinc": op, "ni": pretax - taxexp, "pretax": pretax,
                    "taxexp": taxexp, "rnd": r * (0.18 if s["sic"] in (3674, 7372, 7374) else 0.10), "cfo": cfo,
                    "capex": capex, "sbc": r * s["sbc"], "da": capex * 0.8,
                    "acq": (s.get("acq", 0) * s["rev"] * 1e9 * fx / 8) if i >= len(QUARTER_ENDS) - 8 else 0.0,
                    "intexp": intexp}
            for k, v in vals.items():
                per_q[k].append(v)

        concept = {"revenue": "RevenueFromContractWithCustomerExcludingAssessedTax", "cost": "CostOfRevenue",
                   "gp": "GrossProfit", "opinc": "OperatingIncomeLoss", "ni": "NetIncomeLoss",
                   "pretax": "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
                   "taxexp": "IncomeTaxExpenseBenefit", "rnd": "ResearchAndDevelopmentExpense",
                   "cfo": "NetCashProvidedByUsedInOperatingActivities", "capex": "PaymentsToAcquirePropertyPlantAndEquipment",
                   "sbc": "ShareBasedCompensation", "da": "DepreciationDepletionAndAmortization",
                   "acq": "PaymentsToAcquireBusinessesNetOfCashAcquired", "intexp": "InterestExpense"}
        ifrs_concept = {"revenue": "Revenue", "cost": "CostOfSales", "gp": "GrossProfit",
                        "opinc": "ProfitLossFromOperatingActivities", "ni": "ProfitLoss", "pretax": "ProfitLossBeforeTax",
                        "taxexp": "IncomeTaxExpenseContinuingOperations", "cfo": "CashFlowsFromUsedInOperatingActivities",
                        "capex": "PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities",
                        "da": "DepreciationAndAmortisationExpense", "rnd": "ResearchAndDevelopmentExpense"}
        cash_flow = {"cfo", "capex", "sbc", "da", "acq"}

        for k, series in per_q.items():
            c = ifrs_concept.get(k) if s.get("foreign") else concept[k]
            if not c:
                continue
            if k == "acq" and not s.get("acq"):
                continue
            # older periods tagged 'Revenues' for one company: exercises concept merging
            for i, e in enumerate(QUARTER_ENDS):
                fy_start = date(e.year, 1, 1)
                q_in_year = (e.month // 3)
                cname = c
                if k == "revenue" and s["t"] == "DXQS" and e.year <= 2022:
                    cname = "Revenues"
                if s.get("foreign"):
                    if q_in_year == 4:  # 20-F filers: annual only
                        yr = [series[j] for j, ee in enumerate(QUARTER_ENDS) if ee.year == e.year]
                        if len(yr) == 4:
                            add(tax, cname, unit, {"start": fy_start.isoformat(), "end": e.isoformat(), "val": sum(yr),
                                                   "form": "20-F", "filed": _filed(e, True), "fy": e.year, "fp": "FY"})
                    continue
                ytd = [series[j] for j, ee in enumerate(QUARTER_ENDS) if ee.year == e.year and ee <= e]
                if len(ytd) != q_in_year:
                    continue  # partial first year in the window
                form = "10-K" if q_in_year == 4 else "10-Q"
                filed = _filed(e, q_in_year == 4)
                if q_in_year < 4 and k not in cash_flow:
                    add(tax, cname, unit, {"start": _qstart(e).isoformat(), "end": e.isoformat(), "val": series[i],
                                           "form": form, "filed": filed, "fy": e.year, "fp": f"Q{q_in_year}"})
                if q_in_year > 1:
                    add(tax, cname, unit, {"start": fy_start.isoformat(), "end": e.isoformat(), "val": sum(ytd),
                                           "form": form, "filed": filed, "fy": e.year, "fp": "FY" if q_in_year == 4 else f"Q{q_in_year}"})
                elif k in cash_flow:
                    add(tax, cname, unit, {"start": fy_start.isoformat(), "end": e.isoformat(), "val": series[i],
                                           "form": form, "filed": filed, "fy": e.year, "fp": "Q1"})

        # diluted shares (duration, 3-month)
        for i, e in enumerate(QUARTER_ENDS):
            if s.get("foreign") and e.month != 12:
                continue
            add("ifrs-full" if s.get("foreign") else "us-gaap",
                "AdjustedWeightedAverageShares" if s.get("foreign") else "WeightedAverageNumberOfDilutedSharesOutstanding",
                "shares", {"start": (_qstart(e) if not s.get("foreign") else date(e.year, 1, 1)).isoformat(),
                           "end": e.isoformat(), "val": self.dil_sh[i], "form": "20-F" if s.get("foreign") else ("10-K" if e.month == 12 else "10-Q"),
                           "filed": _filed(e, e.month == 12)})

        # balance sheet instants
        n = len(QUARTER_ENDS)
        for i, e in enumerate(QUARTER_ENDS):
            if s.get("foreign") and e.month != 12:
                continue
            ttm = sum(self.rev[max(0, i - 3): i + 1]) * fx
            grow = (1.0 + 0.03 * (i - n + 1))
            cash = s["cash"] * 1e9 * fx * max(0.4, grow)
            debt = s["debt"] * 1e9 * fx
            equity = cash * 1.1 + ttm * 0.45 + debt * 0.2
            form = "20-F" if s.get("foreign") else ("10-K" if e.month == 12 else "10-Q")
            base = {"end": e.isoformat(), "form": form, "filed": _filed(e, e.month == 12)}
            if s.get("foreign"):
                add("ifrs-full", "CashAndCashEquivalents", unit, {**base, "val": cash})
                add("ifrs-full", "Borrowings", unit, {**base, "val": debt})
                add("ifrs-full", "EquityAttributableToOwnersOfParent", unit, {**base, "val": equity})
                add("ifrs-full", "Assets", unit, {**base, "val": equity + debt + ttm * 0.3})
                add("ifrs-full", "CurrentAssets", unit, {**base, "val": cash + ttm * 0.25})
                add("ifrs-full", "CurrentLiabilities", unit, {**base, "val": ttm * 0.22})
            else:
                add("us-gaap", "CashAndCashEquivalentsAtCarryingValue", unit, {**base, "val": cash * 0.6})
                add("us-gaap", "ShortTermInvestments", unit, {**base, "val": cash * 0.4})
                if debt > 0:
                    add("us-gaap", "ConvertibleNotesPayable" if s["t"] == "DXNV" else "LongTermDebt", unit, {**base, "val": debt})
                add("us-gaap", "StockholdersEquity", unit, {**base, "val": equity})
                add("us-gaap", "Assets", unit, {**base, "val": equity + debt + ttm * 0.3})
                add("us-gaap", "AssetsCurrent", unit, {**base, "val": cash + ttm * 0.25})
                add("us-gaap", "LiabilitiesCurrent", unit, {**base, "val": ttm * (0.22 if s["t"] != "DXBK" else 0.6)})
                if s.get("acq"):
                    add("us-gaap", "Goodwill", unit, {**base, "val": ttm * 0.6})
                if s.get("rpo"):
                    rpo_g = (s["g"][1] + 0.06)
                    add("us-gaap", "RevenueRemainingPerformanceObligation", unit,
                        {**base, "val": ttm * 1.4 * (1 + rpo_g) ** ((i - n + 1) / 4)})
            # cover page shares (filed ~5 weeks after period end)
            cover = e + timedelta(days=30)
            add("dei", "EntityCommonStockSharesOutstanding", "shares",
                {"end": cover.isoformat(), "val": self.dil_sh[i] * 0.985, "form": form, "filed": _filed(e, e.month == 12)})
        return {"cik": self.cik, "entityName": s["n"], "facts": facts}

    # ---- submissions ------------------------------------------------------------------------
    def submissions(self) -> Dict[str, Any]:
        s = self.s
        filings = []
        latest_q = QUARTER_ENDS[-1]
        acc_n = 0

        def acc():
            nonlocal acc_n
            acc_n += 1
            return f"0009{self.cik % 1000000:06d}-26-{acc_n:06d}"

        filings.append({"accession": acc(), "filed": "2026-08-05", "report_date": latest_q.isoformat(),
                        "form": "20-F" if s.get("foreign") else "10-Q", "items": "", "doc": "", "desc": "demo"})
        filings.append({"accession": acc(), "filed": "2026-07-29", "report_date": latest_q.isoformat(),
                        "form": "8-K", "items": "2.02,9.01", "doc": "", "desc": "demo"})
        for k, code in enumerate(s.get("k8", [])):
            filings.append({"accession": acc(), "filed": (DEMO_TODAY - timedelta(days=20 + 7 * k)).isoformat(),
                            "report_date": "", "form": "8-K", "items": code, "doc": "", "desc": "demo"})
        filings.sort(key=lambda f: f["filed"], reverse=True)
        return {"cik": self.cik, "name": s["n"], "sic": str(s["sic"]), "sic_desc": "demo", "tickers": [s["t"]],
                "exchanges": ["Nasdaq"], "fiscal_year_end": "1231", "filings": filings, "synthetic": True}

    # ---- prices -----------------------------------------------------------------------------
    def price_series(self) -> Dict[str, List]:
        s = self.s
        fx = 1.0
        ttm = s["rev"] * 1e9
        ev = s["evs"] * ttm
        mcap = ev + (s["cash"] - s["debt"]) * 1e9
        shares = s["sh"] * 1e6 * (2.0 if s.get("foreign") else 1.0)  # ADS = half an ordinary share
        p_final = mcap / shares
        p_6m = p_final / (1 + s["r6"])
        five_y = (sum(self.rev[-4:]) / sum(self.rev[4:8])) ** 0.6
        p_start = p_6m / max(0.5, five_y)
        n = len(DAYS)
        k6 = n - 127
        rng = random.Random(self.cik)
        noise = [0.0]
        for _ in range(n - 1):
            noise.append(noise[-1] + rng.gauss(0, 0.018))
        out = []
        for i in range(n):
            if i <= k6:
                a, b, t0, t1 = math.log(p_start), math.log(p_6m), 0, k6
            else:
                a, b, t0, t1 = math.log(p_6m), math.log(p_final), k6, n - 1
            frac = (i - t0) / (t1 - t0)
            bridge = noise[i] - (noise[t0] + (noise[t1] - noise[t0]) * frac)
            out.append(math.exp(a + (b - a) * frac + bridge * 0.6))
        return {"dates": [d.isoformat() for d in DAYS], "close": out}


def _bench(seed: int, drift: float, vol: float, start: float) -> Dict[str, List]:
    rng = random.Random(seed)
    p, out = start, []
    for _ in DAYS:
        p *= math.exp(drift / 252 + rng.gauss(0, vol / math.sqrt(252)))
        out.append(p)
    return {"dates": [d.isoformat() for d in DAYS], "close": out}


class DemoMarket(MarketData):
    source_name = "SYNTHETIC DEMO DATA (fictional)"

    def __init__(self, companies: Dict[str, DemoCompany], price_multiplier: float = 1.0):
        self.c = companies
        self.k = price_multiplier
        self.bench = {"SPY": _bench(1, 0.10, 0.16, 400), "QQQ": _bench(2, 0.13, 0.22, 330),
                      "SOXX": _bench(3, 0.18, 0.35, 450), "IGV": _bench(4, 0.11, 0.28, 300)}

    def prices(self, tickers, period="5y"):
        out = {}
        for t in tickers:
            if t in self.bench:
                out[t] = self.bench[t]
            elif t in self.c:
                ps = self.c[t].price_series()
                ps["close"] = [x * self.k for x in ps["close"]]
                out[t] = ps
        return out

    def estimates(self, t):
        s = self.c[t].s
        sh = s["sh"] * 1e6
        eps_now = s["rev"] * 1e9 * s["opm"][1] * 0.84 / sh
        eps_ny = eps_now * (1 + s["cons"][0]) * (1 + s["cons"][1])
        return {"source": self.source_name, "rev_growth_cy": s["cons"][0], "rev_growth_ny": s["cons"][1],
                "rev_analysts": 18, "eps_ny": eps_ny if not s.get("foreign") else None,
                "eps_growth_ny": s["cons"][1] * 1.3, "eps_revision_30d": s["rv"] / 3, "eps_revision_90d": s["rv"],
                "next_earnings": (DEMO_TODAY + timedelta(days=s["ed"])).isoformat()}

    def info(self, t):
        s = self.c[t].s
        shares = s["sh"] * 1e6 * (2.0 if s.get("foreign") else 1.0)
        px = self.c[t].price_series()["close"][-1] * self.k
        return {"source": self.source_name, "sharesOutstanding": shares, "marketCap": px * shares,
                "financialCurrency": "EUR" if s.get("foreign") else "USD", "currency": "USD",
                "shortPercentOfFloat": 0.03, "heldPercentInstitutions": 0.7}

    def splits(self, t):
        return {}

    def fx_to_usd(self, ccy):
        return {"USD": 1.0, "EUR": 1.08}.get(ccy)


class DemoSource(Source):
    label = "SYNTHETIC DEMO DATA — fictional companies, not real securities"

    def __init__(self, price_multiplier: float = 1.0):
        self.companies = {s["t"]: DemoCompany(s, i) for i, s in enumerate(SPECS)}
        self.by_cik = {c.cik: c for c in self.companies.values()}
        self.market = DemoMarket(self.companies, price_multiplier)
        self.sec = None

    def universe(self, cfg, data_dir, force):
        return [{"ticker": s["t"], "cik": self.companies[s["t"]].cik, "name": s["n"], "exchange": "Nasdaq",
                 "sic": s["sic"], "sic_desc": "demo", "segment": s["seg"], "forced": False} for s in SPECS]

    def submissions(self, cik):
        return self.by_cik[cik].submissions()

    def fundamentals(self, cik, subs):
        return xbrl.normalize(self.by_cik[cik].companyfacts())
