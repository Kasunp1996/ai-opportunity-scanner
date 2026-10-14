# Methodology

All parameters below are configurable in `config/settings.yaml`. These are transparent heuristics, not
proprietary truths — the point is that every step is visible and testable.

## 1. Data normalisation (SEC XBRL)

- **Concept merging.** Each metric has a priority list of XBRL concepts (e.g. revenue:
  `RevenueFromContractWithCustomerExcludingAssessedTax` → `Revenues` → `SalesRevenueNet` …; IFRS equivalents).
  Values are merged by reporting period; a higher-priority concept wins where both exist.
- **Restatements.** For the same period, the most recently filed value wins.
- **Quarters.** 10-Q cash-flow items are year-to-date. Discrete quarters are derived by differencing
  cumulative values with the same start date (Q2 = H1 − Q1; Q4 = FY − 9M). Derived values are marked.
- **TTM.** Sum of four consecutive quarters ending at the latest reported quarter; all flows aligned to the
  same end date. Annual-only filers use the latest fiscal year (flagged).
- **Currency.** Non-USD reporters are converted at the current FX rate (flagged).
- **Shares.** max(latest diluted weighted average, cover-page shares outstanding), split-adjusted unless the
  value was already restated after the split. If the SEC count implies a market cap inconsistent with the
  listed security (ADRs, share classes), Yahoo's share count is used and flagged.
- **Debt.** `LongTermDebt` (incl. current) if tagged, else non-current + current, else convertible notes,
  plus short-term borrowings. Method shown on each stock page.
- **Staleness.** Latest period older than 200 days (460 for annual-only filers) ⇒ excluded.

## 2. Initial screen

Market cap ≥ $1B; revenue growth ≥ 10% (TTM or consensus); TTM revenue ≥ $300M — or the
small-company exception (≥ $100M revenue and ≥ 40% growth, flagged as higher risk).
The top 120 by a preliminary score (computed without analyst estimates), plus any previously ranked or
watchlisted names, get consensus estimates and full analysis.

## 3. Valuation

**WACC** = E/V·(rf + β·ERP) + D/V·(rf + spread)(1 − t), β from two years of weekly returns vs SPY, clamped
0.8–2.0; WACC clamped 8–14%.

**Growth.** Initial growth = consensus next-12-month revenue growth (average of current- and next-fiscal-year
consensus); without consensus, 0.8 × TTM growth. Fades linearly to terminal growth (3%) over 10 years.
Bull: g × 1.25 + 3 pts. Bear: g × 0.5 − 2 pts.

**Operating margin path** (GAAP, i.e. after stock-based compensation). Ceiling = gross margin × 0.55.
Base: halfway from today to the ceiling over 5 years (or 25% mean reversion if above it). Bull: 80% of the way.
Bear: 5 pts below the lower of today and base. *Cyclical* businesses (12-quarter growth range > 35 pts or
5-year margin range > 15 pts): bear revisits the worst annual margin of the last five years.

**DCF (FCFF).** NOPAT = revenue × margin × (1 − tax); reinvestment = Δrevenue ÷ sales-to-capital
(captures capex and working capital). Terminal value with reinvestment g/RONIC (RONIC 15%).
Equity = EV + net cash; per share on the diluted count. SBC is treated as a real cost here.

**Reverse DCF.** Solves for the initial growth that makes DCF value equal the price (base margin path).
Compared with consensus to show what the market is pricing in.

**Comparables.** Peers = same segment (≥ 4 usable) else the broad universe.
- Quality-adjusted EV/NTM sales: EV/S ÷ [(growth + FCF-after-SBC margin) × gross margin], peer median,
  re-applied to the company. A 12%-gross-margin assembler is not given software multiples.
- Growth-adjusted EV/NTM EBIT: (EV/EBIT) ÷ growth, peer median (profitable peers only).

**Historical range.** EV/TTM sales at each of the last ~20 quarter-ends; current percentile shown. Never used
alone as evidence of cheapness.

**Intrinsic value** = 50% DCF + 50% comparables (DCF weight halved for unprofitable companies).

**12/24-month scenarios (exit-multiple method).** Revenue and margins projected per scenario. At month T the
market prices the next 12 months. Exit metric: EV/forward EBIT when margins are ≥ 8% (base) and ≥ 5% (bear)
and SBC < 8% of revenue; otherwise EV/forward sales with the cash-margin rule-of-40 adjustment.
Growth-justified multiples: EV/EBIT = 12 + 0.6 × growth(pts), 8–40x; EV/S = 0.2 × (growth + cash margin)(pts)
× gross margin/0.75, 0.5–20x.
Base exit = halfway between today's multiple and the justified one; bull = higher of the two; bear = lower.
Net cash accumulates projected cash generation (SBC added back as non-cash); the share count dilutes at the
recent rate (+1 pt in the bear case). Probability-weighted with 25/50/25.

**Path to 2x.** Solves for the initial growth that produces 2× the price in 24 months under base rules, and the
exit multiple needed on base numbers. Verdict: *base case doubles*, *credible path* (required growth ≤
base + 5 pts or base × 1.15), *requires bull case*, or *not supported*. Only the first two count as a potential double.

**Entry range.** Upper = min(90% of intrinsic value, price at which the probability-weighted 24-month target
implies +50%); lower = upper × 0.85.

## 4. Risk flags

Excessive valuation, weak balance sheet (net debt/EBITDA, cash runway, coverage for net borrowers),
high dilution (share growth, SBC), uncertain forecast (losses, volatile growth, no consensus), AI capex-cycle
exposure, decelerating growth, acquisition-driven growth, margin deterioration, price rising while
fundamentals weaken, attractive-only-in-bull-case, data gaps. Top-five risks = highest-severity computed
flags, then labelled general sector risks.

## 5. Score (100) and tiers

Valuation 25 · Growth 20 · Quality 15 · Financial strength 15 · Upside 15 · Catalysts 10. Each category is a
weighted blend of measured inputs mapped linearly between stated bounds (all visible per stock).
Missing inputs earn 30%. Caps: high-severity weak balance sheet ⇒ ≤ 55; extreme valuation or
optimism-only ⇒ ≤ 60.

- **Tier 1:** score ≥ 75, margin of safety ≥ 15%, expected 24m return ≥ 50%, upside/downside ≥ 3,
  no high-severity flags, credible path to 2x, consensus available.
- **Tier 2:** score ≥ 62, expected 24m ≥ 25%, upside/downside ≥ 2, no balance-sheet/optimism red flag.
- **Tier 3:** score ≥ 50. **Avoid:** below.

## 6. Track record

Every run appends its recommendations with the price at that moment (never overwritten). Performance is
measured forward from the recorded price, net of 0.2% round-trip cost, vs SPY and QQQ, by tier and horizon.
It is live paper tracking — no backtests are shown on the dashboard.

## Known limitations

- Free data: Yahoo is unofficial; consensus history is not available retroactively.
- Competitive advantage is proxied by gross margin, ROIC, margin stability and scale — qualitative moat review is manual.
- Customer concentration and insider transactions are not parsed in this version.
- Heuristic multiples are deliberately simple; adjust them in config and watch the track record.
