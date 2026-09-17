# Option Trading Dashboard

Dedicated dashboard repository for systematic cash-secured put analysis and staged SMH entries.

TCAR strategy research, backtesting, deployment, reporting, and related development now live in the separate private repository `skydiver1118/TCAR-trading`.

## Dashboard scope

- Tradier real-time option chains, bid/ask, IV, delta, theta, gamma and vega
- Standard monthly expirations only
- Dynamic expiration selection within 19–50 DTE using a Greek-weighted score
- Conservative, Preferred and Aggressive put candidates
- Premium, breakeven, distance to support, annualized return and earnings risk
- 45-trading-day price structure, Fibonacci retracements and EMA20
- Stock V2 ownership gate: a short-put SELL requires Long-Term BUY or STRONG BUY
- Ranking by Option Execution Score
- SMH staged stock-entry zones

## Refresh architecture

The primary scheduler is the isolated Windows clone:

`C:\Users\SKYDI\Documents\Option-trading-dashboard-bot`

Windows Task Scheduler runs the dashboard at 10:00 AM, 12:00 PM and 2:00 PM Eastern on weekdays. The runner validates the NYSE calendar, downloads data, regenerates `data/dashboard.json`, commits the result and pushes it to GitHub.

GitHub Actions provides a self-healing backup watchdog and GitHub Pages deployment. The dashboard automation repository must remain clean and must not be used for TCAR development.

## GitHub Pages

Public dashboard:

`https://skydiver1118.github.io/Option-trading/`

## Data and execution caveat

The dashboard is a decision-support tool. Confirm the current live NBBO, buying power, expiration, strike and order limit in the brokerage platform before placing an order.
