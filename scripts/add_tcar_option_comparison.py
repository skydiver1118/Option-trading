#!/usr/bin/env python3
"""Add same-period SOXL TCAR ETF and buy-and-hold comparisons to EOD option results."""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

SOURCE = Path("data/williams_r/soxl_daily_v1_source_snapshot.csv")
OUT = Path("data/options/tcar_onclick_eod")
INITIAL = 100_000.0


def metrics(equity: pd.Series, start: pd.Timestamp, end: pd.Timestamp) -> dict:
    equity = equity.sort_index()
    ret = equity.pct_change().fillna(0.0)
    years = max((end - start).days / 365.25, 1 / 365.25)
    total = float(equity.iloc[-1] / equity.iloc[0] - 1)
    cagr = (1 + total) ** (1 / years) - 1
    dd = float((equity / equity.cummax() - 1).min())
    sd = float(ret.std(ddof=0))
    sharpe = float(ret.mean() / sd * np.sqrt(252)) if sd > 0 else np.nan
    return {
        "total_return": total,
        "cagr": cagr,
        "sharpe": sharpe,
        "max_drawdown": dd,
        "calmar": cagr / abs(dd) if dd < 0 else np.nan,
        "ending_equity": float(equity.iloc[-1]),
    }


def main() -> None:
    summary_path = OUT / "summary.json"
    summary = json.loads(summary_path.read_text())
    trades = pd.read_csv(OUT / "trade_ledger.csv", parse_dates=["entry_date", "exit_date"])
    src = pd.read_csv(SOURCE, parse_dates=["date"]).set_index("date").sort_index()
    src = src.rename(columns={
        "soxl_open": "open", "soxl_high": "high", "soxl_low": "low", "soxl_close": "close"
    })
    start = pd.Timestamp(summary["start"])
    end = pd.Timestamp(summary["end"])
    market = src.loc[start:end].copy()

    entries = {row.entry_date: row for _, row in trades.iterrows()}
    exits = {row.exit_date: row for _, row in trades.iterrows()}
    cash = INITIAL
    shares = 0.0
    in_position = False
    equity_values = []

    for dt, row in market.iterrows():
        if dt in exits and in_position:
            cash += shares * float(row.open)
            shares = 0.0
            in_position = False
        if dt in entries and not in_position:
            trade = entries[dt]
            allocation = cash * float(trade.regime_weight)
            shares = allocation / float(row.open)
            cash -= allocation
            in_position = True
        equity_values.append((dt, cash + shares * float(row.close)))

    underlying_equity = pd.Series(dict(equity_values), dtype=float).sort_index()
    underlying_equity.iloc[0] = INITIAL

    first_open = float(market.open.iloc[0])
    buy_hold_equity = INITIAL * market.close / first_open
    buy_hold_equity.iloc[0] = INITIAL

    spread_fraction = trades.entry_bid_ask_spread / trades.entry_ask
    underlying_trade_portfolio_returns = trades.regime_weight * trades.underlying_open_return
    wins = underlying_trade_portfolio_returns[underlying_trade_portfolio_returns > 0]
    losses = underlying_trade_portfolio_returns[underlying_trade_portfolio_returns < 0]
    underlying_trade_stats = {
        "trades": int(len(trades)),
        "win_rate": float((underlying_trade_portfolio_returns > 0).mean()),
        "profit_factor": float(wins.sum() / abs(losses.sum())) if len(losses) else math.inf,
        "avg_trade_return": float(underlying_trade_portfolio_returns.mean()),
        "median_trade_return": float(underlying_trade_portfolio_returns.median()),
    }

    summary["underlying_tcar_same_period"] = {
        **underlying_trade_stats,
        **metrics(underlying_equity, start, end),
    }
    summary["soxl_buy_hold_same_period"] = metrics(buy_hold_equity, start, end)
    summary["option_execution_diagnostics"] = {
        "average_entry_spread_as_pct_of_ask": float(spread_fraction.mean()),
        "median_entry_spread_as_pct_of_ask": float(spread_fraction.median()),
        "maximum_entry_spread_as_pct_of_ask": float(spread_fraction.max()),
        "underlying_trade_win_rate": float((trades.underlying_open_return > 0).mean()),
        "option_premium_trade_win_rate": float((trades.option_premium_return > 0).mean()),
        "underlying_avg_unscaled_trade_return": float(trades.underlying_open_return.mean()),
        "option_avg_premium_return": float(trades.option_premium_return.mean()),
        "option_underlying_trade_return_correlation": float(
            trades.option_premium_return.corr(trades.underlying_open_return)
        ),
    }
    summary_path.write_text(json.dumps(summary, indent=2, allow_nan=True) + "\n")

    comparison = pd.DataFrame([
        {"vehicle": "ATM 37-DTE long calls", **{k: summary[k] for k in ["total_return", "cagr", "sharpe", "max_drawdown", "calmar", "ending_equity"]}},
        {"vehicle": "TCAR traded in SOXL ETF", **summary["underlying_tcar_same_period"]},
        {"vehicle": "SOXL buy and hold", **summary["soxl_buy_hold_same_period"]},
    ])
    comparison.to_csv(OUT / "vehicle_comparison.csv", index=False)
    underlying_equity.rename("equity").to_csv(OUT / "underlying_tcar_equity.csv")
    buy_hold_equity.rename("equity").to_csv(OUT / "soxl_buy_hold_equity.csv")
    print(json.dumps(summary, indent=2, allow_nan=True))


if __name__ == "__main__":
    main()
