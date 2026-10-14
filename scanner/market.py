"""Market data: prices, consensus estimates, splits and FX via Yahoo Finance (yfinance).

Yahoo Finance is free but UNOFFICIAL: no SLA, occasional gaps and rate limits. Every value from
it is tagged with its source so the report can say so. The scanner never fills gaps with guesses:
missing estimates stay missing and lower the confidence of affected calculations.

Swap this module for a paid provider (FMP, Polygon, Tiingo...) by implementing the same methods.
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from .util import is_num, log

YAHOO = "Yahoo Finance (unofficial, via yfinance)"


class MarketData:
    """Interface. All methods return plain Python data (no pandas) so engines stay testable."""

    source_name = "base"

    def prices(self, tickers: List[str], period: str = "5y") -> Dict[str, Dict[str, List]]:
        """{ticker: {"dates": [iso...], "close": [float...]}} split-adjusted daily closes."""
        raise NotImplementedError

    def estimates(self, ticker: str) -> Dict[str, Any]:
        raise NotImplementedError

    def info(self, ticker: str) -> Dict[str, Any]:
        raise NotImplementedError

    def splits(self, ticker: str) -> Dict[str, float]:
        """{iso_date: ratio} e.g. {"2024-06-10": 10.0}"""
        raise NotImplementedError

    def fx_to_usd(self, currency: str) -> Optional[float]:
        raise NotImplementedError


class YahooMarket(MarketData):
    source_name = YAHOO

    def __init__(self, pause: float = 0.35):
        import yfinance as yf  # imported lazily so tests run without it

        self.yf = yf
        self.pause = pause
        self._fx: Dict[str, Optional[float]] = {}

    def prices(self, tickers: List[str], period: str = "5y") -> Dict[str, Dict[str, List]]:
        out: Dict[str, Dict[str, List]] = {}
        yahoo = {t: t.replace(".", "-") for t in tickers}  # BRK.B -> BRK-B
        chunks = [list(yahoo.values())[i:i + 150] for i in range(0, len(yahoo), 150)]
        back = {v: k for k, v in yahoo.items()}
        for chunk in chunks:
            for attempt in range(3):
                try:
                    df = self.yf.download(chunk, period=period, interval="1d", auto_adjust=True,
                                          group_by="ticker", threads=True, progress=False)
                    break
                except Exception as exc:  # network / rate limit
                    log.warning("price download failed (%s), retrying", exc)
                    time.sleep(5 * (attempt + 1))
            else:
                continue
            for yt in chunk:
                try:
                    cols = df.columns
                    sub = df[yt] if hasattr(cols, "levels") and yt in cols.get_level_values(0) else df
                    s = sub["Close"]
                    if hasattr(s, "columns"):  # some yfinance versions keep a ticker level
                        s = s.iloc[:, 0]
                    s = s.dropna()
                except Exception:
                    continue
                if s.empty:
                    continue
                out[back[yt]] = {"dates": [d.strftime("%Y-%m-%d") for d in s.index],
                                 "close": [float(x) for x in s.values]}
        return out

    def _ticker(self, t: str):
        time.sleep(self.pause)
        return self.yf.Ticker(t.replace(".", "-"))

    def estimates(self, ticker: str) -> Dict[str, Any]:
        tk = self._ticker(ticker)
        est: Dict[str, Any] = {"source": YAHOO}

        def row(df, idx, col):
            try:
                v = df.loc[idx, col]
                v = float(v)
                return v if is_num(v) else None
            except Exception:
                return None

        try:
            rev = tk.revenue_estimate
            if rev is not None and not rev.empty:
                est["rev_growth_cy"] = row(rev, "0y", "growth")
                est["rev_growth_ny"] = row(rev, "+1y", "growth")
                est["rev_cy"] = row(rev, "0y", "avg")
                est["rev_ny"] = row(rev, "+1y", "avg")
                est["rev_analysts"] = row(rev, "+1y", "numberOfAnalysts")
        except Exception as exc:
            log.debug("revenue_estimate %s: %s", ticker, exc)
        try:
            eps = tk.earnings_estimate
            if eps is not None and not eps.empty:
                est["eps_cy"] = row(eps, "0y", "avg")
                est["eps_ny"] = row(eps, "+1y", "avg")
                est["eps_growth_ny"] = row(eps, "+1y", "growth")
                est["eps_analysts"] = row(eps, "+1y", "numberOfAnalysts")
        except Exception as exc:
            log.debug("earnings_estimate %s: %s", ticker, exc)
        try:
            tr = tk.eps_trend
            if tr is not None and not tr.empty:
                cur, d30, d90 = row(tr, "+1y", "current"), row(tr, "+1y", "30daysAgo"), row(tr, "+1y", "90daysAgo")
                if cur is not None and d30 not in (None, 0):
                    est["eps_revision_30d"] = cur / d30 - 1 if d30 > 0 else None
                if cur is not None and d90 not in (None, 0):
                    est["eps_revision_90d"] = cur / d90 - 1 if d90 > 0 else None
        except Exception as exc:
            log.debug("eps_trend %s: %s", ticker, exc)
        try:
            cal = tk.calendar
            dates = cal.get("Earnings Date") if isinstance(cal, dict) else None
            if dates:
                est["next_earnings"] = str(dates[0])[:10]
        except Exception as exc:
            log.debug("calendar %s: %s", ticker, exc)
        return est

    def info(self, ticker: str) -> Dict[str, Any]:
        try:
            raw = self._ticker(ticker).info or {}
        except Exception as exc:
            log.debug("info %s: %s", ticker, exc)
            return {}
        keep = ["longName", "currency", "financialCurrency", "sharesOutstanding", "marketCap",
                "shortPercentOfFloat", "heldPercentInstitutions", "industry", "sector"]
        out = {k: raw.get(k) for k in keep if raw.get(k) is not None}
        out["source"] = YAHOO
        return out

    def splits(self, ticker: str) -> Dict[str, float]:
        try:
            s = self._ticker(ticker).splits
            return {d.strftime("%Y-%m-%d"): float(v) for d, v in s.items() if float(v) > 0}
        except Exception:
            return {}

    def fx_to_usd(self, currency: str) -> Optional[float]:
        if currency == "USD":
            return 1.0
        if currency in self._fx:
            return self._fx[currency]
        rate = None
        try:
            df = self.yf.download(f"{currency}USD=X", period="5d", progress=False, auto_adjust=True)
            s = df["Close"].dropna()
            if not s.empty:
                v = s.values[-1]
                rate = float(v[0] if hasattr(v, "__len__") else v)
        except Exception as exc:
            log.warning("FX %s failed: %s", currency, exc)
        self._fx[currency] = rate
        return rate
