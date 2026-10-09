# Tradier historical-data correction request — draft, not sent

Please investigate daily OHLCV returned by `/v1/markets/history` for SPY, SPMO,
VGT, SMH, QQQ, TQQQ and SOXL, with history from 2015-01-01 through 2026-09-04.
A research capture found 128 missing SPMO study sessions and 161 invalid or
incomplete supplied bars across these symbols, including warm-up.

Reproducible single-day cases (captured again September 5, 2026):

- SPY, start=end=2016-12-15: open 226.16, high 227.81, low 225.8904,
  close 225.48, volume 124972554. Close is below the supplied daily low.
- SPMO, start=end=2016-09-06: open/high/low are NaN, close 27.2178,
  volume 0. A usable full daily bar is absent.

Repeated narrow-window queries reproduce the same responses. Missing SPMO
sessions in our study occur between 2016-09-07 and 2017-09-29.
The complete date-level list is `results/data_issues.csv` (289 records).

Please provide corrected, complete daily bars through the Tradier endpoint
or an authenticated Tradier export, and confirm the split/dividend adjustment
basis for each OHLC field and volume. Please identify whether these are known
corporate-action adjustment or symbol-history defects and which dates/symbols
are affected. We need vendor-supplied corrections; inferred values or data
from another provider cannot be used in this study.

No account identifier, API key, or trading information is included here.
