# CPA_v2 sequential study protocol — 2026-09-04 endpoint

Status: protocol construction; no performance evaluated in this study.
Ancestor: ac70df6a8eec7c71ec62fd20a76dc240d65c318f,
scripts/tradier_cpa_v2_state_machine.py. CPA_PROXY_V1 is rejected.

## Governance registered before data capture

- Only Tradier daily OHLCV. No alternative provider, synthetic leveraged series,
  or reconstructed historical constituents. Requested symbols are a fixed,
  user-selected survivor universe, not a survivorship-free universe.
- Study calendar: available SPY sessions from 2016-09-05 through 2026-09-04
  inclusive. First floor(0.60*N) IS; next floor(0.20*N) Validation; remainder
  OOS. Other symbols must cover the same dates without filling missing prices.
  If the endpoint is unavailable, disclose actual endpoint; do not fabricate it.
- Fetch from 2015 for feature warm-up. Features/state may use past observations;
  account positions start flat in every scored partition. No signal from a
  prior partition initiates a trade in the next partition. Final valuation is
  net of an explicit terminal liquidation cost; censored trades do not count
  toward the independent completed-trade requirement.
- Signal/trade pairs: SPY/SPY, SPMO/SPMO, VGT/VGT, SMH/SMH, QQQ/QQQ,
  TQQQ/TQQQ, SMH/SOXL, QQQ/TQQQ. No pooled or combined equity strategy is selected.
- Study uses a daily adaptation of Kell's discretionary multitimeframe system.
  It does not claim to reproduce his discretionary execution or championship.
- Finish source mapping and visually compare IS-era source examples and detected
  events before performance optimization. Record passes, misses, and ambiguity.
- All implementation thresholds and strategy variants must be enumerated in
  search_space.json before IS evaluation. Source-defined EMA10/EMA20 are
  structural constants; researcher-defined thresholds are labeled explicitly.
- All parameter selection, neighborhood stability assessments, and anchored
  walk-forward sensitivity analyses use only the initial IS portion. No
  validation-based backup candidates, threshold changes, or replacement search.
- Select at most three candidates per signal/trade pair by prespecified IS
  ranking. Serialize and hash the candidates BEFORE validation. Apply validation
  rejection only; freeze survivors and all source/code/data hashes before OOS.
- One terminal OOS evaluation batch for all frozen survivors. No rerun,
  reselection of parameters, new candidate, or repair after OOS exposure.
  A discovered material implementation defect invalidates the study's OOS
  claims rather than authorizing a revised OOS optimization.

## Prespecified validation acceptance (all required)

These are conservative researcher choices, not Kell rules:

1. At least 6 independent, completed flat-to-flat cycles; scale-ins/outs do not
   count separately. At least 6 IS completed cycles in each half of IS and at
   least 20 total IS completed cycles for an IS candidate.
2. Net CAGR > 0; daily Sharpe (zero risk-free convention) >= 0.50 and at least
   50% of that candidate's IS Sharpe; cash earns zero because no external rate
   series is allowed.
3. Absolute maximum drawdown <= 35% and <= max(10%, 1.5 * IS absolute drawdown).
4. Profit factor > 1.0; both chronological validation halves have positive net
   returns; no single completed trade provides > 50% of positive dollar P&L.
5. Doubling declared transaction costs retains positive validation CAGR.
6. No detected input-integrity, execution-accounting, state-order, or leakage
   violations. Validation is never used to order survivors.

## Execution, costs, and metrics

- Close-of-session decisions, next available common-session opening fills;
  no same-close fills using that close. Signal-asset levels never become
  execution-asset prices. No shorting or borrowed leverage beyond the ETF.
- All-in adverse cost per one-way notional change: 5 bp for unlevered ETFs;
  10 bp for SOXL/TQQQ. Covers modeled spread, slippage and fees; these are
  assumptions rather than measured historical spreads. Stress: double costs.
- Cash/share ledger; every gap, intraday move, transaction and partial exit
  reconciles to equity. Fixed shares between orders (no free daily rebalancing).
  Protective daily close breaches execute next open; intraday stop precision
  is not inferred from daily bars.
- Benchmark: same execution asset buy-and-hold over identical dates and costs;
  also show signal-asset benchmark for cross-asset pairs. Returns reflect
  Tradier supplied prices, not guaranteed dividend-reinvested total returns.
  Reject unresolved gross split discontinuities; do not invent corrections.
- CAGR uses actual calendar elapsed time including starting-capital anchor.
  Sharpe uses all daily account returns including cash days, sample deviation
  and sqrt(252). Also report HAC-adjusted Sharpe. Sortino uses root mean square
  of min(return, 0) across ALL days. MaxDD includes initial capital. Calmar is
  CAGR/absolute MaxDD. PF uses completed trades' net dollar P&L.
- Report exposure, trades and distinct cycles, forced/censored trades, trade
  duration, win rate, turnover, annual/half-period consistency, and cost stress.

## Final eligibility and ordering

All required: OOS daily Sharpe > 1.0; OOS net CAGR > 0.30; >=20 independent
completed OOS cycles; complete integrity/visual/governance gates. Reentries
within a single CPA cycle do not manufacture independent evidence. Low trade
count is a failure of final eligibility, even if return constraints pass.

Rank eligible strategies by equal-weight percentile ranks of HAC Sharpe,
Calmar, Sortino, lower absolute MaxDD, lower half-period Sharpe,
independent trade count, capped PF (cap 5), and positive-half consistency.
Ties: lower exposure, then stable candidate identifier. Also disclose raw
metrics and inter-strategy correlation; ranking several OOS survivors adds
selection bias. No eligible strategy means **no CPA strategy passed**.

## Historical holdout contamination

The ancestor workflow already completed a full-history-through-2026-09-04 run
(GitHub Actions 33961806555, 2026-09-05). This study will not open its performance
artifacts for selection. However the historical dates have been tested by prior
work, and unrelated strategies in the repository also used these dates. A new
frozen protocol cannot retroactively make this globally virgin OOS data. Label
the result a once-evaluated holdout for this protocol, with prior-use caveat;
reserve future prospective observations for a genuinely new test.

## Required retained outputs

Source definitions/citations, ancestor defects, visual review, search space,
all IS configurations and metrics, validation criteria and candidate results,
frozen manifests with hashes/times, terminal OOS ledger, final raw results,
trades/fills/events/states/equity/benchmarks, input provenance, leakage tests,
and adversarial audit. Raw data remains in source artifacts rather than printed
in logs. OOS files must not be loaded by IS or validation entry points.
