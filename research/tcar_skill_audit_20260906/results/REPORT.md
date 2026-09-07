# DATA-QUALITY GATE FAILED: CONDITIONAL RESULTS ONLY

Seven original Tradier bars have inconsistent OHLC bounds, including three inside the study. The initial strict run stopped. This follow-up preserves those prices for a conditional replication, not a validated backtest. OOS remains reused.

| date       | inside_study   |   tradier_open |   yahoo_open |   tradier_high |   yahoo_high |   tradier_low |   yahoo_low |   tradier_close |   yahoo_close |
|:-----------|:---------------|---------------:|-------------:|---------------:|-------------:|--------------:|------------:|----------------:|--------------:|
| 2016-03-11 | False          |        1.572   |      1.618   |        1.572   |      1.666   |       1.572   |     1.614   |         1.66533 |       1.66533 |
| 2016-03-15 | False          |        1.646   |      1.62933 |        1.646   |      1.63667 |       1.646   |     1.596   |         1.63667 |       1.63667 |
| 2016-03-24 | False          |        1.71933 |      1.68067 |        1.71933 |      1.71667 |       1.71933 |     1.65867 |         1.71533 |       1.71533 |
| 2016-04-05 | False          |        1.78533 |      1.742   |        1.78533 |      1.79333 |       1.78533 |     1.72667 |         1.75333 |       1.75333 |
| 2016-12-19 | True           |        4       |      4       |        4.09    |      4.09    |       3.94667 |     3.94667 |         3.86    |       4.04467 |
| 2023-06-05 | True           |       21.75    |     21.75    |       21.44    |     21.75    |      20.66    |    20.66    |        20.97    |      20.97    |
| 2023-12-20 | True           |       30.2     |     30.2     |       30.62    |     30.62    |      28.05    |    28.05    |        28.0368  |      28.1     |

Full local skill package was not available; recovered specification, partition-local warm-up and audit requirements were implemented. No claim is made that the local installed skill test suite ran.

# TCAR daily backtesting audit

Research only. OOS is reused, not an untouched holdout. No deployment changes or orders.

## Primary 10bp-per-side scenario

| period          | mode         |   total_return |     cagr |   sharpe |   sortino |   max_drawdown |   calmar |   trades |   win_rate |   profit_factor_return |    ending_equity |
|:----------------|:-------------|---------------:|---------:|---------:|----------:|---------------:|---------:|---------:|-----------:|-----------------------:|-----------------:|
| IS              | LOCAL_WARMUP |       10.4148  | 0.501783 | 1.12204  |   1.92094 |      -0.461273 | 1.08782  |       59 |   0.728814 |                3.54337 |      1.14148e+06 |
| IS              | BUY_HOLD     |        3.31595 | 0.276627 | 0.741634 |   1.04611 |      -0.841074 | 0.328897 |        0 | nan        |              nan       | 431595           |
| VALIDATION      | LOCAL_WARMUP |        1.27338 | 0.5099   | 1.30111  |   2.60873 |      -0.147101 | 3.46634  |       21 |   0.714286 |                4.6787  | 227338           |
| VALIDATION      | BUY_HOLD     |        1.32689 | 0.527629 | 0.923412 |   1.39107 |      -0.617729 | 0.854144 |        0 | nan        |              nan       | 232689           |
| OOS_REUSED      | LOCAL_WARMUP |        1.62985 | 0.622218 | 1.1127   |   2.6344  |      -0.241169 | 2.58001  |       12 |   0.916667 |              102.327   | 262985           |
| OOS_REUSED      | BUY_HOLD     |        3.03037 | 1.00854  | 1.18496  |   1.74625 |      -0.797893 | 1.264    |        0 | nan        |              nan       | 403037           |
| FULL_CONTINUOUS | LOCAL_WARMUP |       54.9132  | 0.495792 | 1.08672  |   2.0062  |      -0.461273 | 1.07483  |       97 |   0.71134  |                3.5468  |      5.59132e+06 |
| FULL_CONTINUOUS | BUY_HOLD     |       38.3604  | 0.44416  | 0.877617 |   1.26847 |      -0.905055 | 0.490754 |        0 | nan        |              nan       |      3.93604e+06 |

## Gross legacy replication

```json
{
  "source": "Tradier",
  "start": "2016-09-06",
  "end": "2026-09-04",
  "bars": 2514,
  "trades": 98,
  "win_rate": 0.7244897959183674,
  "profit_factor": 3.7729888076895612,
  "avg_trade": 0.04980088399542375,
  "median_trade": 0.04193183905754949,
  "avg_holding_days": 3.8877551020408165,
  "total_return": 71.29798925994453,
  "cagr": 0.5347581889459725,
  "sharpe": 1.1408071404333728,
  "max_drawdown": -0.45911426095451235,
  "calmar": 1.1647605714407436,
  "exposure": 0.10421638822593476,
  "ending_equity": 7229798.925994453,
  "source_note": "Fresh Tradier API snapshot; same frozen source function. Legacy external warmup is NOT primary partition-local evaluation."
}
```

## Protocol and limitations

Daily SOXL; WR2<-90 AND CCI5<-80 AND SMA-ADX20>=15. Exit close>prior high OR WR2>-30. No QQQ sizing. Next-session-open fills; no same-close fills. Primary indicators calculated only within each partition, cash during warmup. Ending positions marked to close, not invented liquidations.

10bp per side is an assumed combined friction scenario, not an empirically estimated cost. Fractional adjusted research units; no explicit dividends, cash yield, tax, settlement delays, opening-auction latency or capacity model. Tradier dividend adjustments are not guaranteed. Original dates were retained; historical OOS has already influenced prior research.

Legacy externally warmed continuous results are diagnostic only and are not the strict partition-local skill result. Full-window returns are one simulation, not multiplied reset partitions. Daily-close drawdowns omit intraday extremes. Continuous subperiod trade statistics attribute whole trades by exit date and can cross cuts.

## Independent checks

63/64 checks passed. Offline NumPy indicators, separate equity reconstruction, prefix causality, partition isolation, synthetic gap fills, calendar and OHLC validation.

## OOS block-bootstrap uncertainty

```json
{
  "total_return": {
    "p2_5": 0.2976007465989999,
    "median": 1.5699668507671505,
    "p97_5": 4.9478008557343065
  },
  "cagr": {
    "p2_5": 0.13922428946350904,
    "median": 0.603629904654264,
    "p97_5": 1.4403007945638564
  },
  "sharpe": {
    "p2_5": 0.5228346945684524,
    "median": 1.1478678597022376,
    "p97_5": 2.0848083267515376
  },
  "max_drawdown": {
    "p2_5": -0.3577748036258921,
    "median": -0.22495688851185996,
    "p97_5": -0.14611707126076745
  },
  "design": "Circular moving-block bootstrap of historical daily strategy returns; 20-session blocks, 2000 resamples. Conditional resampling uncertainty, NOT a future-return forecast or correction for prior strategy selection.",
  "fraction_resamples_positive": 0.999
}
```

## Concentration

| period          | best_trade_signal   |   best_trade_return |   top3_share_positive_log_gains |   baseline_cagr |   omit_best_trade_cagr |   baseline_sharpe |   omit_best_trade_sharpe |
|:----------------|:--------------------|--------------------:|--------------------------------:|----------------:|-----------------------:|------------------:|-------------------------:|
| FULL_CONTINUOUS | 2020-04-01          |            0.522672 |                        0.153868 |        0.495792 |               0.434161 |           1.08672 |                 1.01367  |
| OOS_REUSED      | 2026-07-29          |            0.207305 |                        0.519867 |        0.622218 |               0.476293 |           1.1127  |                 0.949523 |

## Yahoo check

```json
{
  "status": "PASS",
  "split_adjusted_legacy_metrics": {
    "start": "2016-09-06",
    "end": "2026-09-04",
    "sessions": 2514,
    "total_return": 65.12596943076116,
    "cagr": 0.5211144368077634,
    "sharpe": 1.1215539444724656,
    "sortino": 2.084228572609374,
    "annual_volatility": 0.46651737006554056,
    "max_drawdown": -0.4591143221557302,
    "calmar": 1.1350428676694666,
    "trades": 97,
    "win_rate": 0.7216494845360825,
    "profit_factor_return": 3.7202528726063515,
    "profit_factor_dollar": 5.534682219896599,
    "avg_trade": 0.049351798819023504,
    "median_trade": 0.04163197256213147,
    "avg_holding_calendar_days": 3.8969072164948453,
    "avg_holding_sessions": 2.670103092783505,
    "close_position_fraction": 0.1030230708035004,
    "exposed_return_days": 0.14160700079554495,
    "ending_equity": 6612596.943076116,
    "open_position_at_end": false,
    "sum_cost_dollars": 0.0,
    "worst_day": -0.3089593821493921
  },
  "tradier_trade_count": 98,
  "yahoo_split_adjusted_trade_count": 97,
  "matching_trades": 97,
  "tradier_only": [
    [
      "2017-12-05",
      "2017-12-08"
    ]
  ],
  "yahoo_only": [],
  "price_diff_median_abs_pct": {
    "open": 2.1953701190291497e-08,
    "high": 2.6391812402160042e-08,
    "low": 2.5291225447254817e-08,
    "close": 2.1395091476250627e-08
  }
}
```
