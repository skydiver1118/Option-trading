# CPA_v2 exploratory backtest — final result

**NO CPA STRATEGY PASSED.** This evaluation uses the experimental CPA_v2 numerical implementation and Tradier data as supplied under the user-authorized data exception. It is not a certified replication of Oliver Kell’s discretionary trades.

Tested **2,048 configuration/pair evaluations** across eight requested pairs. **0 IS candidates**, **0 Validation survivors**, and **0 OOS strategy evaluations**. OOS benchmarks were evaluated once in the terminal batch.

## Chronology and controls

| Partition | Dates | Sessions |
|---|---|---:|
| IS | 2016-09-06–2022-08-31 | 1,508 |
| Validation | 2022-09-01–2024-09-03 | 503 |
| OOS | 2024-09-04–2026-09-04 | 503 |

The same 256 prespecified parameter sets were applied to every pair. All parameter ranking uses IS only. Validation can reject but cannot retune or replace candidates. The final survivor manifest was frozen before the terminal OOS batch. No thresholds were modified after performance inspection. Positions reset to cash at partition boundaries; indicators may use historical warm-up. Next-open fills use the traded asset, with 5 bp one-way costs for ordinary ETFs and 10 bp for SOXL/TQQQ. Cash earns zero; Sharpe uses a zero risk-free rate.

## Best IS trial per pair — descriptive, not recommendations

Rows show the highest IS Sharpe configuration, including configurations rejected for inadequate evidence. These are not OOS results.

| Signal → trade | IS CAGR | IS Sharpe | IS max drawdown | Completed cycles | Exposure | Most cycles in any tested configuration |
|---|---:|---:|---:|---:|---:|---:|
| SPY → SPY | 0.80% | 0.746 | -1.88% | 1 | 2.39% | 3 |
| SPMO → SPMO | 0.29% | 0.198 | -3.31% | 1 | 1.13% | 4 |
| VGT → VGT | 3.35% | 0.973 | -3.09% | 1 | 5.44% | 5 |
| SMH → SMH | 3.13% | 0.590 | -7.93% | 2 | 4.84% | 7 |
| QQQ → QQQ | 3.68% | 0.744 | -4.95% | 3 | 5.90% | 3 |
| TQQQ → TQQQ | 4.57% | 0.683 | -9.77% | 2 | 4.71% | 4 |
| SMH → SOXL | 8.36% | 0.580 | -23.20% | 2 | 4.84% | 7 |
| QQQ → TQQQ | 10.78% | 0.758 | -14.72% | 3 | 5.90% | 3 |

IS admission requires ≥20 completed independent cycles, positive CAGR and Sharpe, followed by prespecified local-parameter and half-period stability checks. A top score based on one or two cycles is insufficient evidence. The strongest IS configuration is not promoted merely because it ranks first.

## Validation and final selection

No configuration passed initial IS admission. Validation therefore received an empty candidate manifest, and the final frozen OOS strategy set was empty. Rejected IS configurations were not evaluated on OOS. **There is no OOS-validated CPA strategy to recommend from this experiment.** This is an IS-screen failure, not evidence that CPA strategies were tested on OOS and all failed the return targets.

The original final-selection constraints remain OOS Sharpe >1, CAGR >30%, and ≥20 completed independent cycles. No low-count exception, new parameter search, or post-OOS modification was introduced.

## OOS buy-and-hold benchmarks

Buy-and-hold is shown for context only. These rows do not establish a successful CPA rule, and ranking ETF buy-and-hold returns is not a substitute for the strategy-selection protocol. Same 503 OOS sessions, entry cost and final liquidation cost; supplied-price returns, not guaranteed dividend-reinvested total returns.

| Asset | OOS CAGR | OOS Sharpe | OOS max drawdown | Calmar |
|---|---:|---:|---:|---:|
| QQQ | 25.12% | 1.141 | -22.88% | 1.10 |
| SMH | 59.75% | 1.411 | -32.65% | 1.83 |
| SOXL | 100.56% | 1.185 | -79.79% | 1.26 |
| SPMO | 32.01% | 1.290 | -20.28% | 1.58 |
| SPY | 18.24% | 1.098 | -19.00% | 0.96 |
| TQQQ | 53.79% | 0.987 | -58.30% | 0.92 |
| VGT | 33.55% | 1.230 | -27.41% | 1.22 |

## Adversarial audit

- **Overfitting / sample size:** A 256-point search across 25 modeling choices is sparse and creates selection bias. Best IS outcomes are not an estimate of future returns. Very few cycles and low exposure make attractive Sharpe values unreliable.
- **Strategy fidelity:** The state order is causal and tested, but exact Kell annotation timing remains unverified. Strict reversal-first initialization and numeric conjunctions may miss discretionary setups. The findings apply to this implementation and search space, not all possible CPA adaptations.
- **Data:** Known invalid bars are accepted as supplied. Missing rows generate no signal or fill. Missing next opens cancel entries and defer exits; stale-close valuation is flagged. Weekly aggregates may contain incomplete observations. These choices can distort risk and performance even without future leakage.
- **Look-ahead / accounting:** 17 exploratory-engine tests pass, including prefix/future-mutation invariance, weekly availability, pivot exclusion, gap/exit/cost/trim reconciliation, signal/execution separation, and new missing-data cases. The original 17-test suite previously passed. These are bounded checks, not proof against every implementation defect.
- **Trade independence:** Flat-to-flat completed cycle IDs count once; trims and forced terminal liquidation do not inflate independent counts. Unique IDs do not guarantee statistical independence.
- **Regimes / universe:** Eight user-specified surviving ETF pairs are not a survivorship-free universe. Performance does not establish robustness across future regimes or capacity.
- **Leveraged ETFs:** Actual TQQQ/SOXL prices preserve path dependence; returns are not created by multiplying an index return. SMH and SOXL have different underlying indices.
- **Historical reuse:** Earlier repository work already used these dates. This run preserves one terminal batch for this protocol, not a globally untouched historical holdout.
- **Partition reset:** Cash resets can differ from continuous live execution. This is the original prespecified convention and was not selected for improved returns.

## Reproducibility and retained evidence

The original blocked study remains intact at research/cpa_v2_20260904. This study has separate authorization, protocol, price prefixes, IS start lock, complete parameter results, frozen empty/nonempty manifests, Validation results and one OOS start/completion lock. All IS configuration directories retain equity, trades, fills and state/event histories. Benchmarks retain OOS equity and trade histories.

The full results archive retains separated prefixes and every generated configuration output. Raw Tradier inputs, source definitions, source visual evidence, code, tests and compact summaries are retained on the research branch. Do not rerun a finished OOS stage for selection.

**Decision:** Do not deploy or recommend a CPA strategy from this run. A future implementation revision must be a separately named research study with fresh validation discipline; it cannot turn these already inspected historical dates into new evidence.
