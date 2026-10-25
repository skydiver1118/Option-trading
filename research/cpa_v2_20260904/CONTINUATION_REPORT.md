# Continuation: source remediation exhausted on available evidence

Status: **BLOCKED_BEFORE_IS**, unchanged. The requested backtest is not complete.
No parameters were tuned or frozen, and no IS, Validation, or OOS performance
was evaluated. This is not a finding that no CPA strategy meets the target.

## New Tradier checks

Ten new authenticated Tradier queries completed in GitHub Actions. Exact raw
responses and retrieval provenance are preserved in
`data/cpa_v2_20260904_requery/20260905T125959947238Z/`.
All eight repeated multi-day responses are identical to the earlier JSON.
The additional single-day SPY request reproduces the invalid 2016-12-15 bar;
the SPMO 2016-09-06 request reproduces NaN open/high/low, close 27.2178 and zero
volume. Narrowing to a single session does not repair either example.

An independent repository Tradier SPY capture reproduces the same five numeric
fields for the defective SPY session. It supplies no defensible replacement.
Missing SPMO study sessions span 2016-09-07 through 2017-09-29.
No raw source, prepared price file, parameter, or gate was changed.

## Defect location

Counts combine invalid supplied bars and missing study sessions; these are
input-quality checks, not inspection of held-out performance.

| Asset | Warm-up | IS | Validation | OOS |
|---|---:|---:|---:|---:|
| QQQ | 2 | 0 | 0 | 0 |
| SMH | 5 | 1 | 0 | 0 |
| SOXL | 4 | 1 | 2 | 0 |
| SPMO | 26 | 205 | 4 | 0 |
| SPY | 3 | 12 | 2 | 4 |
| TQQQ | 1 | 0 | 0 | 0 |
| VGT | 7 | 9 | 1 | 0 |

QQQ/TQQQ scored dates have no flagged defects, but their warm-up inputs and
the common visual gate are unresolved. Their performance is therefore not
run as an undeclared substitute for the requested panel.

## Additional source review

[Kell's own site](https://kelltrading.com/) corroborates the six named stages;
it does not provide numerical thresholds or a dated event-label dataset.
His [EMA Crossback article](https://traderlion.com/technical-analysis/chart-patterns/ema-crossback/)
describes a first supported return to the averages after Wedge Pop and
identifies NOW 2020 as an example. His
[Base n' Break article](https://traderlion.com/technical-analysis/chart-patterns/base-n-break-how-to-catch-breakouts/)
identifies LLY 2021 and NIO 2020 examples and distinguishes later consolidations
from the first crossback. These descriptions corroborate the sequence, not
exact detector timing. No extra numerical setting is attributed to Kell.

The existing TraderLion browser tab remains at human verification; no bypass
was attempted. The public Tesla video was checked again at its 7:25 entry
chapter. Playback still showed captions over a black video area. Thus no
new author-labeled chart frame could be compared to Tradier bars. The visual
gate remains false. Definitions alone cannot certify the implementation.

[Tradier's endpoint reference](https://docs.tradier.com/reference/brokerage-api-markets-get-history)
lists symbol, interval, start and end inputs; no documented adjustment switch
provides a remedy. Its [history guidance](https://docs.tradier.com/docs/historical-data)
warns that dividend adjustments may be unreliable, which does not prove the
cause of these specific defects. No inferred repair is used.

## Validation and deliverables

All 17 targeted tests passed again. The synthetic OOS-lock test runs only in
a temporary directory with no candidates; its completion message is not a
historical OOS evaluation. All eight requested signal/execution pairs remain
unstarted. Existing stage statuses and the frozen search-space registration
remain unchanged.

The append-only requery change and vendor responses are saved on the research
branch. `results/continuation_requery_comparison.json` records the comparison,
and `results/defects_by_partition.csv` records the coverage table.
`TRADIER_CORRECTION_REQUEST.md` is a prepared, unsent correction request.

Completion requires corrected Tradier history plus legible annotated IS-era
Kell examples. Repeating the same requests, filling missing data, or relaxing
the gates is not a valid way to finish this experiment.
