# AI Opportunity Scanner

A disciplined, data-driven research system for U.S.-listed technology and AI companies. Every trading day it:

1. scans every Nasdaq/NYSE company in technology SIC codes (≈600–900 companies) using SEC filings,
2. computes growth, margins, cash generation, balance-sheet strength and dilution from XBRL data,
3. values each candidate four ways — DCF, reverse DCF ("what's priced in"), growth/quality-adjusted comparables and its own valuation history — plus bear/base/bull 12- and 24-month scenarios,
4. tests whether a **2x in 24 months** has a credible, quantified path,
5. flags risks, collects verifiable catalysts, scores out of 100 and sorts into Tier 1 / Tier 2 / Tier 3 / Avoid,
6. publishes a dashboard on GitHub Pages, archives a Markdown report, logs every recommendation (append-only) and opens a GitHub issue when alerts fire.

It is allowed to — and often will — say **"No new high-conviction opportunity identified today."**

> Research tool, not investment advice. Intrinsic values, targets and entry ranges are model estimates from public data and stated assumptions. Verify against the linked filings.

---

## Setup (about 10 minutes)

1. **Create a GitHub repository** (public, for free Pages) and push this folder to it:
   ```bash
   cd ai-opportunity-scanner
   git init && git add . && git commit -m "Initial scanner"
   git branch -M main
   git remote add origin https://github.com/<you>/ai-opportunity-scanner.git
   git push -u origin main
   ```
2. **Add the SEC contact secret** — *Settings → Secrets and variables → Actions → New repository secret*
   - `SEC_USER_AGENT` = `AI Opportunity Scanner your.email@example.com` (SEC requires a name and email in requests).
   - Optional: `ANTHROPIC_API_KEY` — only if you turn on `ai_narrative.enabled` (paid API; see below).
3. **Enable Pages** — *Settings → Pages → Build and deployment → Source: GitHub Actions*.
4. **Allow the workflow to push** — *Settings → Actions → General → Workflow permissions → Read and write*.
5. **Run it once** — *Actions → Daily scan → Run workflow* (tick **force** if the market is closed). The first run builds the universe and downloads SEC data (~25–40 min); later runs take ~10–15 min.
6. Open `https://<you>.github.io/ai-opportunity-scanner/`.

From then on it runs automatically at 5:45 pm New York time on weekdays (holidays are detected and skipped).

**Alerts by email:** watch the repository (*Watch → Custom → Issues*). When an alert fires, the job opens an issue and GitHub emails you. Server-side alerts cover Tier 1/2 names plus the tickers in `config/watchlist.txt`.

## Run locally

```bash
pip install -r requirements.txt
python -m scanner demo                      # offline, fictional companies → _site_demo/
python -m http.server -d _site_demo 8000    # open http://localhost:8000

export SEC_USER_AGENT="AI Opportunity Scanner you@example.com"
python -m scanner run --force               # live run → _site/ and data/
python -m unittest discover -s tests -t .   # 27 offline tests
```

The **demo** uses 22 fictional companies (tickers starting `DX`) generated in SEC's exact company-facts JSON format, so the real parsing, valuation and reporting code runs end to end. The dashboard shows a banner whenever demo data is loaded.

## Configuration

Everything tunable is in `config/settings.yaml` — edit it on GitHub and the next run uses it:

| Section | What it controls |
|---|---|
| `universe` | Exchanges, SIC code ranges, weekly refresh |
| `screen` | Revenue / growth / market-cap preferences, small-company exception, staleness limits, shortlist size |
| `valuation` | Risk-free rate (update periodically), ERP, WACC bounds, terminal growth, margin ceiling, multiple rules, scenario probabilities, entry-range target |
| `scoring` | Category weights (25/20/15/15/15/10), caps, tier thresholds |
| `alerts` | GitHub issue toggle, earnings window, server watchlist file |
| `ai_narrative` | Optional Claude catalyst extraction (off by default) |

`config/universe_extra.csv` force-includes tickers (e.g. TSM, ASML) and overrides their segment (e.g. cybersecurity names that SIC codes can't distinguish). API keys never go in files — only GitHub secrets — and the dashboard's settings page shows the configuration read-only without secrets.

## Architecture

```
SEC EDGAR ──► sec.py ──► xbrl.py (normalise: concept merging, YTD→quarters, restatements)
Yahoo ──────► market.py (prices, consensus, earnings dates, splits, FX)
                     │
universe.py ─► pipeline.py ─► metrics.py ─► screen.py ─► valuation.py ─► risk.py ─► catalysts.py ─► scoring.py
                     │                                                                         │
                     └────────► narrative.py (templated text; optional AI extraction) ◄────────┘
                     └────────► report.py · history.py · alerts.py ─► _site/data/*.json + data/reports/*.md
site/ (static dashboard: index.html, app.js, style.css) reads _site/data/*.json
.github/workflows/daily.yml: test → scan → commit log → issue → deploy Pages
```

- **Deterministic calculations** (metrics, valuation, risk, scoring) are separate from **explanations** (narrative.py). Default explanations are filled from computed numbers, so they cannot invent facts. The optional AI step only extracts catalysts *stated in the latest SEC earnings release*, and keeps only quotes verified to appear in the filing.
- **Inspectable:** every stock page shows DCF assumptions and year-by-year projection, peer multiples, scenario assumptions, each score input, data-quality notes and source links.
- **Missing data stays missing:** absent fields are listed, lower the score (30% credit) and block Tier 1 where essential.

## Data providers, cost and limits

| Provider | Used for | Cost | Limits |
|---|---|---|---|
| SEC EDGAR (submissions, XBRL company facts) | Universe, financial statements, filings, 8-K events | Free | 10 req/s; tagging varies by company; foreign filers (20-F) often tag annual data only |
| Yahoo Finance via `yfinance` | Prices, consensus revenue/EPS, EPS revisions, earnings dates, splits, FX, short interest | Free | **Unofficial** — no SLA, may throttle or change; consensus is a point-in-time snapshot |
| Anthropic API (optional) | Catalyst extraction from earnings releases | ~$0.01–0.05/day with Haiku for 3 companies | Off by default |
| GitHub Actions + Pages | Scheduling, hosting | Free for public repos (private: 2,000 min/month free) | ~15 min/day of Actions time |

**Not in this free-tier MVP:** insider transaction classification (Form 4 parsing), customer concentration (not in structured XBRL — flagged for manual 10-K review), news feeds, and point-in-time historical estimates. Upgrading to a paid provider (e.g. Financial Modeling Prep, Polygon, Tiingo) means implementing the `MarketData` interface in `scanner/market.py`.

## Maintenance

- Update `valuation.risk_free_rate` when Treasury yields move materially.
- If Yahoo changes its API, upgrade `yfinance` (`requirements.txt`); the run fails loudly rather than publishing stale prices.
- The SEC cache lives in the Actions cache; if it is evicted the next run re-downloads (slower, not broken).
- `data/history/recommendations.jsonl` is the permanent track record — don't edit it.

## Roadmap (next stages)

1. Point-in-time backtest using SEC `filed` dates (no look-ahead), delisted companies, transaction costs, vs SPY/QQQ across regimes.
2. Form 4 insider parsing (open-market buys vs 10b5-1 sales).
3. Intraday price refresh option; paid data adapter.
4. Richer AI research notes with filing citations.

See `guide/METHODOLOGY.md` for formulas and every assumption.
