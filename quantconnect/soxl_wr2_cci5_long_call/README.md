# SOXL WR(2) + CCI(5) historical long-call backtest

This QuantConnect project tests expired SOXL calls without changing the frozen
underlying signal. Tradier remains the signal source; LEAN supplies only the
point-in-time option chain, Greeks, open interest, and minute bid/ask quotes.

## Frozen signal v1

- Enter after `WR(2) < -90` and `CCI(5) < -80` at the prior close.
- Exit after `WR(2) > -30`, `CCI(5) > 0`, or SOXL close above the prior high.
- One position at a time; no pyramiding.
- QQQ close above EMA200: 100% tactical exposure.
- QQQ close at/below EMA200: 50% tactical exposure.
- IS: 2016-09-06 through 2022-09-02.
- Validation: 2022-09-06 through 2024-08-30.
- OOS: 2024-09-03 through 2026-09-03.

The exporter deliberately fixes those dates. It does not move the test windows
forward on later reruns.

## Primary option protocol

| Item | Frozen rule |
|---|---|
| Instrument | Long SOXL call |
| Contract universe | Calls, including weeklies, standard 100-share multiplier |
| DTE | 30-45 calendar days |
| Expiry | Closest to 37 DTE; later expiry wins a tie |
| Strike | Closest to spot; lower strike wins a tie |
| Selection | First valid chain at/after 09:35 ET |
| Execution | Fresh quote at/after 09:36 ET |
| Buy fill | Ask |
| Sell fill | Bid |
| Fee | $0.65 per contract per side |
| Roll | Replace at seven DTE if the SOXL signal remains long |

The 100%/50% underlying allocation is mapped to approximately equal
delta-adjusted SOXL exposure. Call premium is capped at 20% of account equity
above QQQ EMA200 and 10% at/below it. This avoids interpreting “100% position”
as putting the whole account into leveraged call premium.

## Files

- `main.py`: LEAN algorithm and fill audit.
- `signal_manifest.py`: generated Tradier action dates; do not hand-edit.
- `data/williams_r/soxl_daily_v1_signal_manifest.csv`: readable audit copy in
  the repository root.
- `data/williams_r/soxl_daily_v1_source_snapshot.csv`: immutable input bars used
  to generate the manifest.
- `data/williams_r/soxl_daily_v1_signal_manifest_metadata.json`: strategy and
  source hash audit record.
- `scripts/export_soxl_daily_signal_manifest.py`: canonical exporter.

The export workflow is deliberately manual, restricted to `main`, and
read-only. It retains the generated files as a review artifact instead of
allowing a secret-bearing job to push repository changes. After the first
export, review and commit the snapshot, metadata, CSV, and Python manifest
together; then pin the reviewed source digest before treating v1 as immutable.

## Cloud run

Create a QuantConnect Python project and add `main.py` plus
`signal_manifest.py`. Run three separate flat-start backtests by setting the
project parameter `period` to `IS`, `VALIDATION`, and `OOS`.

Before interpreting performance, verify:

1. The first and last expected manifest actions appear in the logs.
2. Every filled buy matches its contemporaneous ask and every sale its bid.
3. There are no skipped entries, unexpected liquidations, exercise cleanups,
   or fill mismatches without a documented explanation.
4. QuantConnect has non-empty SOXL option quotes for the entire requested
   period. Broad dataset coverage starts in 2012, but exact SOXL coverage must
   be confirmed by the run.

The 2024-2026 period has already influenced strategy development and should be
described as a historical holdout, not untouched forward OOS evidence.
