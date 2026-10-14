"""Normalize SEC XBRL 'company facts' into consistent annual / quarterly / point-in-time series.

Companies tag the same economic item with different concepts over time (e.g. 'Revenues' vs
'RevenueFromContractWithCustomerExcludingAssessedTax'). For each metric we merge a priority list
of concepts by reporting period. 10-Q cash-flow items are year-to-date, so discrete quarters are
derived by differencing cumulative values (Q4 = FY - 9M). Restated values: the latest filing wins.
Every derived value is marked so it can be audited.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import timedelta
from typing import Any, Dict, List, Optional, Tuple

from .util import days_between, parse_date

G, I, D = "us-gaap", "ifrs-full", "dei"

DURATION: Dict[str, List[Tuple[str, str]]] = {
    "revenue": [(G, "RevenueFromContractWithCustomerExcludingAssessedTax"), (G, "Revenues"),
                (G, "SalesRevenueNet"), (G, "RevenueFromContractWithCustomerIncludingAssessedTax"),
                (G, "SalesRevenueGoodsNet"), (G, "SalesRevenueServicesNet"),
                (I, "Revenue"), (I, "RevenueFromContractsWithCustomers")],
    "cost_of_revenue": [(G, "CostOfRevenue"), (G, "CostOfGoodsAndServicesSold"), (G, "CostOfGoodsSold"),
                        (G, "CostOfServices"), (I, "CostOfSales")],
    "gross_profit": [(G, "GrossProfit"), (I, "GrossProfit")],
    "operating_income": [(G, "OperatingIncomeLoss"), (I, "ProfitLossFromOperatingActivities")],
    "net_income": [(G, "NetIncomeLoss"), (G, "ProfitLoss"), (G, "NetIncomeLossAvailableToCommonStockholdersBasic"),
                   (I, "ProfitLossAttributableToOwnersOfParent"), (I, "ProfitLoss")],
    "pretax_income": [(G, "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest"),
                      (G, "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments"),
                      (I, "ProfitLossBeforeTax")],
    "income_tax": [(G, "IncomeTaxExpenseBenefit"), (I, "IncomeTaxExpenseContinuingOperations")],
    "cfo": [(G, "NetCashProvidedByUsedInOperatingActivities"),
            (G, "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"),
            (I, "CashFlowsFromUsedInOperatingActivities")],
    "capex": [(G, "PaymentsToAcquirePropertyPlantAndEquipment"), (G, "PaymentsToAcquireProductiveAssets"),
              (G, "PaymentsForCapitalImprovements"),
              (I, "PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities")],
    "sbc": [(G, "ShareBasedCompensation"), (G, "AllocatedShareBasedCompensationExpense")],
    "da": [(G, "DepreciationDepletionAndAmortization"), (G, "DepreciationAmortizationAndAccretionNet"),
           (G, "DepreciationAndAmortization"), (G, "Depreciation"), (I, "DepreciationAndAmortisationExpense")],
    "rnd": [(G, "ResearchAndDevelopmentExpense"), (G, "ResearchAndDevelopmentExpenseExcludingAcquiredInProcessCost"),
            (I, "ResearchAndDevelopmentExpense")],
    "interest_expense": [(G, "InterestExpense"), (G, "InterestExpenseNonoperating"), (G, "InterestExpenseDebt")],
    "acquisitions": [(G, "PaymentsToAcquireBusinessesNetOfCashAcquired"), (G, "PaymentsToAcquireBusinessesGross")],
    "buybacks": [(G, "PaymentsForRepurchaseOfCommonStock")],
    "dividends_paid": [(G, "PaymentsOfDividends"), (G, "PaymentsOfDividendsCommonStock")],
}
SHARE_DURATION = {
    "diluted_shares": [(G, "WeightedAverageNumberOfDilutedSharesOutstanding"), (I, "AdjustedWeightedAverageShares")],
}
INSTANT: Dict[str, List[Tuple[str, str]]] = {
    "cash": [(G, "CashAndCashEquivalentsAtCarryingValue"), (G, "Cash"),
             (G, "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"), (I, "CashAndCashEquivalents")],
    "st_investments": [(G, "ShortTermInvestments"), (G, "MarketableSecuritiesCurrent"),
                       (G, "AvailableForSaleSecuritiesDebtSecuritiesCurrent"), (G, "HeldToMaturitySecuritiesCurrent")],
    "lt_debt_total": [(G, "LongTermDebt"), (G, "LongTermDebtAndCapitalLeaseObligationsIncludingCurrentMaturities"),
                      (I, "Borrowings")],
    "lt_debt_noncurrent": [(G, "LongTermDebtNoncurrent"), (G, "LongTermDebtAndCapitalLeaseObligations"),
                           (I, "NoncurrentPortionOfNoncurrentBorrowings")],
    "lt_debt_current": [(G, "LongTermDebtCurrent"), (G, "LongTermDebtAndCapitalLeaseObligationsCurrent"),
                        (G, "DebtCurrent"), (I, "CurrentBorrowingsAndCurrentPortionOfNoncurrentBorrowings")],
    "convertible_debt": [(G, "ConvertibleNotesPayable"), (G, "ConvertibleDebtNoncurrent"),
                         (G, "ConvertibleLongTermNotesPayable"), (G, "ConvertibleSeniorNotesNoncurrent")],
    "short_term_debt": [(G, "ShortTermBorrowings"), (G, "CommercialPaper")],
    "equity": [(G, "StockholdersEquity"), (G, "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"),
               (I, "EquityAttributableToOwnersOfParent"), (I, "Equity")],
    "total_assets": [(G, "Assets"), (I, "Assets")],
    "current_assets": [(G, "AssetsCurrent"), (I, "CurrentAssets")],
    "current_liabilities": [(G, "LiabilitiesCurrent"), (I, "CurrentLiabilities")],
    "goodwill": [(G, "Goodwill"), (I, "Goodwill")],
    "rpo": [(G, "RevenueRemainingPerformanceObligation")],
}
SHARE_INSTANT = {
    "shares_outstanding": [(D, "EntityCommonStockSharesOutstanding"), (G, "CommonStockSharesOutstanding")],
}

ACCEPTED_FORMS = ("10-K", "10-Q", "20-F", "40-F", "6-K", "10-KT")


def _pick_currency(facts: Dict[str, Any]) -> str:
    """Reporting currency = unit used for revenue (USD preferred)."""
    counts: Dict[str, int] = defaultdict(int)
    for tax, concept in DURATION["revenue"]:
        units = facts.get(tax, {}).get(concept, {}).get("units", {})
        for u, arr in units.items():
            if len(u) == 3 and u.isalpha() and u.isupper():
                counts[u] += len(arr)
    if not counts:
        return "USD"
    if counts.get("USD"):
        return "USD"
    return max(counts, key=counts.get)


def _collect(facts: Dict[str, Any], candidates: List[Tuple[str, str]], unit: str,
             instant: bool) -> List[Dict[str, Any]]:
    merged: Dict[Tuple[Optional[str], str], Dict[str, Any]] = {}
    for tax, concept in candidates:
        arr = facts.get(tax, {}).get(concept, {}).get("units", {}).get(unit, [])
        best: Dict[Tuple[Optional[str], str], Dict[str, Any]] = {}
        for f in arr:
            if not str(f.get("form", "")).startswith(ACCEPTED_FORMS):
                continue
            if "end" not in f or "val" not in f:
                continue
            if instant and "start" in f:
                continue
            if not instant and "start" not in f:
                continue
            key = (f.get("start"), f["end"])
            prev = best.get(key)
            if prev is None or str(f.get("filed", "")) > str(prev.get("filed", "")):
                best[key] = f
        for key, f in best.items():
            if key not in merged:  # higher-priority concept already covers this period
                merged[key] = {"start": f.get("start"), "end": f["end"], "val": float(f["val"]),
                               "filed": f.get("filed"), "concept": concept}
    return sorted(merged.values(), key=lambda x: (x["end"], x.get("start") or ""))


def _duration_days(f: Dict[str, Any]) -> int:
    return days_between(f["start"], f["end"]) or 0


def derive_quarters(series: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Discrete ~3-month periods from a mix of quarterly, YTD and annual facts."""
    quarters: Dict[str, Dict[str, Any]] = {}
    for f in series:
        if 75 <= _duration_days(f) <= 105:
            quarters[f["end"]] = {**f, "derived": False}
    by_start: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for f in series:
        by_start[f["start"]].append(f)
    for start, group in by_start.items():
        group.sort(key=lambda x: x["end"])
        prev = None
        for f in group:
            if prev is not None:
                gap = days_between(prev["end"], f["end"]) or 0
                if 75 <= gap <= 105 and f["end"] not in quarters:
                    s = parse_date(prev["end"]) + timedelta(days=1)
                    quarters[f["end"]] = {"start": s.isoformat(), "end": f["end"], "val": f["val"] - prev["val"],
                                          "filed": f.get("filed"), "concept": f.get("concept"), "derived": True}
            prev = f
    return sorted(quarters.values(), key=lambda x: x["end"])


def annual_periods(series: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = [f for f in series if 340 <= _duration_days(f) <= 390]
    # one value per fiscal year end
    seen: Dict[str, Dict[str, Any]] = {}
    for f in out:
        seen[f["end"]] = f
    return sorted(seen.values(), key=lambda x: x["end"])


def normalize(raw: Dict[str, Any]) -> Dict[str, Any]:
    facts = raw.get("facts", {})
    ccy = _pick_currency(facts)
    annual: Dict[str, List[Dict[str, Any]]] = {}
    quarterly: Dict[str, List[Dict[str, Any]]] = {}
    instant: Dict[str, List[Dict[str, Any]]] = {}
    concepts_used: Dict[str, List[str]] = {}

    def slim(rows: List[Dict[str, Any]], keep_start: bool = True) -> List[Dict[str, Any]]:
        out = []
        for r in rows:
            d = {"end": r["end"], "val": r["val"]}
            if keep_start and r.get("start"):
                d["start"] = r["start"]
            if r.get("derived"):
                d["derived"] = True
            if r.get("filed"):
                d["filed"] = r["filed"]
            out.append(d)
        return out

    for metric, cands in list(DURATION.items()) + list(SHARE_DURATION.items()):
        unit = "shares" if metric in SHARE_DURATION else ccy
        series = _collect(facts, cands, unit, instant=False)
        if not series:
            continue
        concepts_used[metric] = sorted({s["concept"] for s in series})
        annual[metric] = slim(annual_periods(series))[-8:]
        quarterly[metric] = slim(derive_quarters(series))[-16:]

    for metric, cands in list(INSTANT.items()) + list(SHARE_INSTANT.items()):
        unit = "shares" if metric in SHARE_INSTANT else ccy
        series = _collect(facts, cands, unit, instant=True)
        if not series:
            continue
        by_end: Dict[str, Dict[str, Any]] = {}
        for s in series:
            by_end[s["end"]] = s
        concepts_used[metric] = sorted({s["concept"] for s in series})
        instant[metric] = slim(sorted(by_end.values(), key=lambda x: x["end"]), keep_start=False)[-24:]

    return {
        "cik": raw.get("cik"),
        "name": raw.get("entityName"),
        "currency": ccy,
        "annual": annual,
        "quarterly": quarterly,
        "instant": instant,
        "concepts": concepts_used,
    }
