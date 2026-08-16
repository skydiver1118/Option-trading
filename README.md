# Option Trading Dashboard

A GitHub Pages dashboard for systematic cash-secured put analysis of **SOXL, LITE, AAOI, and MRVL**, plus staged **SMH** stock entries.

## What it monitors

- Live/near-live underlying prices via `yfinance`
- 45-trading-day swing high/low
- Fibonacci retracements: 23.6%, 38.2%, 50%, 61.8%, 78.6%
- 20-day EMA and short-term price structure
- 28–45 DTE option chain selection
- Put midpoint premium, effective breakeven, downside cushion and cash yield
- Earnings/event-risk penalty when calendar data are available
- SELL / WAIT ranking with special leverage penalty for SOXL
- SMH staged stock-entry zones

The seeded rules preserve the trading framework developed in the referenced ChatGPT discussion: sell puts into controlled pullbacks/volatility rather than chasing vertical rallies, demand premium-adjusted breakevens near major technical support, and manage event risk explicitly.

## Automated refresh

GitHub Actions checks the dashboard at **10:30 AM, 1:00 PM and 2:30 PM America/New_York** on valid NYSE trading days. The workflow includes both EST and EDT UTC schedules; the Python script gates execution to the correct local time and skips weekends/NYSE holidays.

You can also run **Refresh option dashboard → Run workflow** manually from the Actions tab.

## GitHub Pages

The repository includes a Pages deployment workflow. In repository **Settings → Pages → Build and deployment**, choose **GitHub Actions** if GitHub has not enabled it automatically.

Expected public address: `https://skydiver1118.github.io/Option-trading/`

## Data caveat

Yahoo Finance data accessed through `yfinance` is suitable for research monitoring but should not be treated as an exchange-grade order-entry feed. Confirm the live NBBO in your brokerage before placing an option order.
