# CPA_v2 study: blocked before performance optimization

**No IS optimization, validation performance evaluation, or OOS performance
evaluation has been performed in this protocol. No parameters are frozen and
no strategy is selected.** This is an incomplete experiment with explicit
failed prerequisite gates. It is not evidence that CPA met or missed the
requested return constraints.

The rejected CPA_PROXY_V1 is excluded. The new engine descends from
`scripts/tradier_cpa_v2_state_machine.py` at commit
`ac70df6a8eec7c71ec62fd20a76dc240d65c318f`. The ancestor's unscored-exit and cost
accounting defects make its existing results unsuitable for selection.

## Captured data and exact chronology

Daily OHLCV was captured directly from Tradier, ending **2026-09-04**, with
2015 warm-up where available. No other provider's OHLCV or synthetic leveraged
prices were used. Five IS-era stocks were also captured for morphology review.

| Partition | First session | Last session | Sessions |
|---|---|---|---:|
| IS | 2016-09-06 | 2022-08-31 | 1,508 |
| Validation | 2022-09-01 | 2024-09-03 | 503 |
| OOS | 2024-09-04 | 2026-09-04 | 503 |

There are 2,514 study sessions. Cuts use floor(0.60*N) and floor(0.80*N).
The original capture note independently rounded segment lengths; cumulative
rounding was clarified before any performance computation. Both versions are
preserved in Git history. Feature warm-up is retained, while account positions
reset to cash and prior-partition signals do not initiate new-partition trades.

## Blocking input defects

The capture contains **128 absent SPMO study sessions** and **161 invalid or
incomplete supplied bars** across the seven ETFs, including warm-up. There are
69 OHLC inconsistencies plus 92 additional SPMO bars with missing numeric data.
Zero-volume observations are reported separately, not automatically treated
as an impossible trade or silently filled.

| Asset | Absent study sessions | Invalid/incomplete bars, including warm-up |
|---|---:|---:|
| SPY | 0 | 21 |
| SPMO | 128 | 107 |
| VGT | 0 | 17 |
| SMH | 0 | 6 |
| QQQ | 0 | 2 |
| TQQQ | 0 | 1 |
| SOXL | 0 | 7 |

Examples that lie entirely in IS:

| Asset/date | Tradier low | Tradier close | Defect |
|---|---:|---:|---|
| SPY, 2016-12-15 | 225.8904 | 225.48 | Close below low |
| SMH, 2018-12-19 | 43.25 | 42.651 | Close below low |
| SOXL, 2016-12-19 | 3.946667 | 3.86 | Close below low |

Eight narrower Tradier requests, including SPMO September 2016 and calendar
2017, returned no new dates and the same fields to 1e-10 precision (missing
values compared as missing). The initial apparent float differences disappear
at numerical parsing tolerance; **request-window price instability is not
established**. The OHLC defects and absent sessions remain established.

QQQ and TQQQ defects occur in warm-up rather than the scored window. They
should be distinguished from SPY/SMH/SOXL/VGT defects inside scored periods.
The complete requested panel nevertheless fails its registered data gate.
Truncating the window, skipping missing sessions, clamping closes into the
range, replacing missing values, or silently dropping affected bars would
change the experiment and is not used here.

[Tradier's documentation](https://docs.tradier.com/docs/historical-data)
warns about historical dividend-adjustment reliability. That warning is not
proof of the specific mechanism behind each observed anomaly. Coherent OHLCV
and a documented corporate-action basis are needed before claiming valid
signal formation or total returns.

## CPA research and visual check

[SOURCE_DEFINITIONS.md](SOURCE_DEFINITIONS.md) maps the primary rules and
distinguishes each modeling choice. The normal sequence is
Reversal Extension → Wedge Pop → EMA Crossback → Base n’ Break(s) →
Exhaustion Extension(s) → Wedge Drop. Early failures abort a cycle explicitly.
The code does not fabricate missing stages based on elapsed days.

The fixed, unoptimized TSLA reference detects:

| Date | Event |
|---|---|
| 2020-03-19 | Reversal Extension |
| 2020-04-13 | Wedge Pop |
| 2020-04-22 | EMA Crossback |
| 2020-06-01 | Base n’ Break 1 |
| 2020-06-10 | Exhaustion Extension 1 |
| 2020-06-30 | Base n’ Break 2 |
| 2020-07-02 | Exhaustion Extension 2 |
| 2020-08-11 | Wedge Drop |

The [spring chart](morphology/TSLA_2020_spring.png) and
[full-year chart](morphology/TSLA_2020_full.png) were inspected. The sequence is
causal and structurally recognizable, but this does **not** establish exact
agreement with Kell's discretionary annotations. His
[TSLA presentation](https://www.youtube.com/watch?v=VNvb0_zkYqw) describes gap
reclaims and inside-bar entries that may precede this detector's Wedge Pop.
The August Wedge Drop also precedes a later rally, underscoring that an
ordered state path alone cannot establish source-faithful event timing.
Original chart frames did not render reliably. Text/caption review and the
published schematics do not replace matching the actual author-labeled dates.
`visual_review.json` therefore remains `accepted: false`.

## Implemented study controls

- Explicit source/trade separation for the eight requested pairs: SPY/SPY,
  SPMO/SPMO, VGT/VGT, SMH/SMH, QQQ/QQQ, TQQQ/TQQQ, SMH/SOXL, QQQ/TQQQ.
- A cash/share ledger charges every order and marks every overnight gap,
  including exits. Partial exits produce cash. No cost-free daily rebalancing.
  Daily close signals fill the next session's open; terminal liquidation is
  identified and excluded from completed-cycle counts.
- Assumed adverse costs: 5 bp each way for unlevered ETFs, 10 bp for TQQQ/SOXL,
  with doubled-cost stress. These are assumptions, not measured historical
  spreads. Early SPMO liquidity could make them optimistic. Results, when
  allowed, use zero cash interest/zero risk-free Sharpe and must be labeled so.
- A deterministic 256-configuration IS screen and adjacent-parameter IS checks
  are registered. All 25 researcher-defined parameters are listed; they have
  not been optimized. The source's 10/20 EMAs are structural constants.
- Validation uses the preregistered rejection gates in PROTOCOL.md, never
  parameter retuning or survivor ranking. At most three IS candidates per
  pair proceed; rejected candidates are not replaced after validation.
- Final eligibility requires OOS Sharpe >1, CAGR >30%, and at least 20
  independently counted completed CPA cycles. Partial transactions and
  same-cycle entries do not inflate the count. Statistical independence is
  still an assumption, not guaranteed by unique cycle IDs.
- Input/protocol/code/partition hashes bind stages. IS and validation loaders
  read separate prefix files. OOS starts with an exclusive once-only marker;
  failures leave that marker in place. There is no force/rerun option.

**17 targeted tests pass.** They cover active-state prefix invariance, future
mutation, completed-week availability, signal-day pivot exclusion, exit-gap
losses, transaction costs, partial exits, price-unit separation, terminal
account reconciliation, partition resets, validation rejection and one-way
OOS locks. A synthetic empty-candidate test exercises the lock in a temporary
directory; it does not evaluate the real OOS partition.
These tests validate specific mechanics, not the detector's trading edge or
the full performance pipeline on real data. The implementation remains a draft.

## Adversarial audit and interpretation limits

| Risk | Finding / action |
|---|---|
| Overfitting | 25 numerical/specification choices create a very large family. A 256-point screen is not exhaustive estimation. A famous TSLA winner is a biased morphology example; no overfitting claim can be cleared before failed/ambiguous examples and IS stability checks. |
| Parameter instability | Not assessed statistically: zero IS configurations scored. Adjacent-parameter checks are implemented but unrun. No stable region is claimed. |
| Survivorship | User-selected surviving ETFs do not represent a survivorship-free universe of funds or growth stocks. No historical-constituent reconstruction is attempted. |
| Look-ahead | Prefix, future-mutation and weekly-availability tests pass. These do not prove point-in-time vendor revisions are absent; source-price integrity remains failed. |
| Historical OOS reuse | Ancestor workflow 33961806555 already used data through 2026-09-04. Its performance artifacts were not opened here. Other repository studies also used these dates. This cannot be marketed as a globally untouched historical test set. |
| Selection on OOS | Final ranking of several frozen survivors still creates winner-selection bias. The protocol prevents retuning, but prospective observations are needed for genuinely new confirmation. |
| Sample size | No OOS trades evaluated; ≥20 completed cycles is mandatory. No low-count exception is introduced after seeing results. |
| Regime dependence | TSLA 2020 is not evidence of an ETF edge across inflation, bear and sideways regimes. IS halves, validation halves and final subperiod metrics remain unrun. |
| Leveraged ETF path | Actual ETF prices must carry daily compounding, financing and tracking effects; do not multiply an unlevered multi-day return by three. Embedded fund expenses must not be double-charged. |
| SMH/SOXL basis mismatch | SMH and SOXL do not track the same underlying index. This pair is a sector-signal transfer test, not a clean three-times version of SMH. |
| Execution / capacity | Daily bars cannot establish intraday stop fills, historical spreads or executable capacity. Next-open stops can gap. Fractional-share normalized results are not a capacity study. |
| Price vs total return | Supplied OHLCV alone does not establish a consistent dividend-reinvested total-return series. Both strategy and benchmark labels must disclose that limitation. |

The leverage and index distinctions are supported by
[ProShares' TQQQ objective](https://www.proshares.com/our-etfs/leveraged-and-inverse/tqqq),
[Direxion's SOXL fact sheet](https://www.direxion.com/uploads/SOXL-SOXS-Fact-Sheet.pdf),
and [VanEck's SMH objective](https://www.vaneck.com/us/en/investments/semiconductor-etf-smh/).
Issuer performance tables surfaced incidentally during source research and
were not ingested into the study, used as prices, or used to set parameters.

## Reproduce and continue

Use Python 3.12 with `requirements.txt` in this folder, from the repository root:

```bash
python -m unittest discover -s tests -p 'test_cpa_v2_*.py' -v
python scripts/cpa_v2_study.py prepare
python scripts/cpa_v2_morphology.py
```

Preparation reproduces the defect list and separated prefix files. It computes
no performance. `python scripts/cpa_v2_study.py is` currently refuses to start.
The code must remain a draft until data defects are resolved and exact visual
matching passes. Once those prerequisites are genuinely satisfied, the staged
sequence is `is` → `validation` → `oos`. Do not change a gate merely to permit
execution. Preserve any corrected Tradier response and the prior raw response,
document the correction, and review the protocol before the first IS run.

Required next evidence: corrected/complete Tradier history with coherent OHLCV,
and legible Kell-annotated IS-era examples for stage-by-stage comparison.
Neither filling gaps from another provider nor inferring corrections is an
acceptable substitute under this request. Research is saved at this blocker;
no final performance claim is made.
