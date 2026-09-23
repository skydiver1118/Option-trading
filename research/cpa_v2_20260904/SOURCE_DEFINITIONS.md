# CPA source review and implementation boundary

Reviewed 2026-09-05. CPA_PROXY_V1 remains rejected. CPA_v2 is the lineage,
not a claim of a validated implementation. This study is a daily, long-only
ETF adaptation; Kell uses judgment, stock selection and intraday execution.

## Primary and authoritative sources

1. [Kell Trading, official framework](https://kelltrading.com/) lists the six
   phases. Its marketing page supplies no universal numeric thresholds.
2. [Oliver Kell, Cycle of Price Action, TraderLion, 2024-09-10](https://traderlion.com/technical-analysis/chart-patterns/cycle-of-price-action-by-oliver-kell/)
   supports the sequence, price/volume primacy, 10/20 EMAs and weekly/daily/
   intraday context. It explicitly allows a top without exhaustion. Therefore
   forcing every selloff to wait for an exhaustion label is not source faithful.
3. [Kell, Reversal Extension, 2024-06-11](https://traderlion.com/technical-analysis/chart-patterns/reversal-extension-how-stocks-bottom/)
   describes downside separation from the fast average, capitulation volume,
   higher-timeframe support, and a rebound. It distinguishes gradual rounded
   bottoms and warns that apparent reversals can fail. A reversal label is
   provisional at detection time; it must never be backdated after success.
4. [Kell, Wedge Pop, 2024-09-11](https://traderlion.com/technical-analysis/chart-patterns/wedge-pop-the-money-pattern/)
   requires contraction into a tradable pivot, tightening averages, and an
   accumulation breakout/reclaim. Contraction precedes the breakout. A strong
   breakout bar itself may expand or gap. Failed pivot breaks need risk exits.
5. [Kell, EMA Crossback](https://traderlion.com/technical-analysis/chart-patterns/ema-crossback/)
   describes the first supported pullback after Wedge Pop. The stock can move
   sideways or gently down as averages catch up. This is price retesting the
   averages, not a fast EMA crossing a slow EMA. Confirmation and nearby stops
   matter; published examples include NOW 2020 and PTON 2020.
6. [Kell, Base n’ Break, 2024-05-11](https://traderlion.com/technical-analysis/chart-patterns/base-n-break-how-to-catch-breakouts/)
   describes subsequent consolidations, often one to three weeks, supported
   by the averages. Breaks through their pivots can add exposure. Repetition
   indicates maturity; strong cycles can keep extending. Published examples
   include LLY 2021 and NIO 2020. It does not prescribe an ATR multiplier.
7. [Kell presenting his TSLA trade, TraderLion video](https://www.youtube.com/watch?v=VNvb0_zkYqw)
   provides direct testimony: 0:36–2:50 discusses the early-2020 blowoff,
   lower high and Wedge Drop; 3:58–5:47 covers the daily downside extension
   into weekly support; 6:10–8:24 discusses contraction, a gap reclaim and
   inside-bar confirmation; 9:37–12:14 explains discretionary stops below
   the inside bar and then the ignite bar; 12:44–14:14 describes an earnings
   exit and later reentry. Captions were read; video images did not render
   reliably in this browser. No earnings rule is added to an OHLCV-only ETF test.
8. [Tradier historical-data documentation](https://docs.tradier.com/docs/historical-data)
   describes daily OHLCV and warns that historical dividend adjustments may
   be unreliable. This does not establish the cause of a particular bad bar.
   Dividend-reinvested total return is not asserted from these fields alone.

## Operational mapping (researcher choices)

| Stage | Source-grounded meaning | Experimental daily encoding |
|---|---|---|
| Reversal Extension | Downside stretch, rebound, support and capitulation | Weak EMA alignment; bar below EMA10; ATR separation; close-location and volume tests; proximity to completed-week support |
| Wedge Pop | First contracted-pivot reclaim after reversal | Prior-window contraction; tight prior EMAs; higher low than reversal; close above prior pivot and both EMAs with volume |
| EMA Crossback | First supported retest after Wedge Pop | Later session touches EMA zone and closes supportively above EMA20 |
| Base n’ Break(s) | Subsequent supported consolidation and breakout | Non-overlapping 5/10/15-bar bases, range contraction, support fraction and volume breakout; count actual bases |
| Exhaustion Extension(s) | Increasing separation and late-cycle euphoria | Requires a prior Base n’ Break, daily low above EMA10 by ATR threshold and completed-week extension; hysteresis prevents daily recounting |
| Wedge Drop | Average loss and breakdown after late-cycle behavior | Close below both EMAs and prior pivot low after extension; incomplete-cycle failures are explicit aborts |

The normal transition chain is preserved. A risk exit is permitted at any
stage, but never relabeled as a completed six-stage cycle. The strict required
Reversal→Wedge Pop chain excludes rounded bottoms that Kell also discusses;
that loss of coverage must be reported. There is no 12/20-day shortcut that
manufactures maturity. There are no shorts, fundamental screens or discretionary
intraday probes in the experiment. Entry-stage variants start at Wedge Pop,
Crossback or first Base n’ Break; they do not claim to reproduce Kell's sizing.

Every numerical detector/stop/trim parameter is enumerated in search_space.json.
Default values are **unoptimized morphology references**, not source-mandated
or performance-selected values. EMA10/20 and weekly EMA10/20 are sourced
structural constants. Equal-window contraction comparison, 100-bar minimum
history and causal availability conventions are explicit modeling choices.
The broad parameter family itself presents considerable specification risk.

## Defects found in the ancestor (read without old performance results)

- Equity excluded the overnight move into an exit and the whole exit session.
- Costs appeared in nominal trade prices but not account returns; trims had no
  fill ledger and their cash/share behavior was not represented faithfully.
- Final trade liquidation did not reconcile with equity.
- Wedge Pop demanded a contracted breakout bar rather than contracting setup.
- Weekly features were conservative but unnecessarily delayed by another week.
- State maturity could be asserted after a fixed elapsed duration; repeated
  exhaustion counting had no genuine return-to-average rearming rule.
- No chronological split, IS-only selection, separate signal/trade asset,
  minimum independent sample, initial protective risk abort, or sealed OOS.
- Sortino used negative-day standard deviation rather than downside deviation
  across all account days. Existing numbers must not be treated as evidence.

## Visual evidence and limitations

The published Crossback and Base n’ Break schematics were visually inspected.
An annotated candlestick chart was recovered from the primary article's
[image asset](https://traderlionmedia.s3.us-east-2.amazonaws.com/wp-content/uploads/2024/09/11014922/Cycle-Of-Price-Action_8.png).
Its displayed timeline is not an independently verified IS-era TSLA mapping;
it is excluded from quantitative calibration. The complete TSLA video source
was transcribed, but captions do not replace exact author-labeled chart dates.
The morphology output compares a fixed reference detector with the TSLA 2020
story and makes its timing limitations visible. Full visual acceptance remains
open until the actual annotated IS example can be matched stage by stage.

Third-party full transcripts and chart images are not republished in the
repository. Retained source URLs, timestamps, notes and image hashes document
what was reviewed. No Validation or OOS performance was used for this review.
