#!/usr/bin/env python3
"""Backtest frozen SOXL TCAR signals with historical SOXL call bid/ask data.

Research only. This script does not connect to a brokerage or place orders.
Data provider: OnclickMedia historical EOD options backtester API.

Frozen signal:
  BUY after WR(2)<-90, CCI(5)<-80, ADX(20)>=15; execute next trading day.
  SELL after close>prior high OR WR(2)>-30; execute next trading day.
  QQQ>EMA200 => 100% underlying regime weight; otherwise 50%.

Option protocol is fixed before this EOD test:
  long SOXL call, ATM, target 37 DTE, buy at EOD ask, sell at EOD bid,
  $0.65 per contract per side. Premium cap is 20% of equity in the bull
  regime and 10% in the bear regime.

Because the public archive is EOD, this is an EOD execution approximation,
not the final QuantConnect 09:36 minute-NBBO test.
"""
from __future__ import annotations

import json
import math
import time
from pathlib import Path

import numpy as np
import pandas as pd
import requests

SOURCE = Path("data/williams_r/soxl_daily_v1_source_snapshot.csv")
OUT = Path("data/options/tcar_onclick_eod")
BASE = "https://backtest.onclickmedia.com/"
INITIAL_CAPITAL = 100_000.0
TARGET_DTE = 37
MONEYNESS = 0.0
FEE_PER_CONTRACT_SIDE = 0.65
BULL_PREMIUM_CAP = 0.20
BEAR_PREMIUM_CAP = 0.10


def prepare() -> pd.DataFrame:
    x = pd.read_csv(SOURCE, parse_dates=["date"]).set_index("date").sort_index()
    x = x.rename(columns={
        "soxl_open": "open", "soxl_high": "high", "soxl_low": "low", "soxl_close": "close"
    })
    hh = x.high.rolling(2).max()
    ll = x.low.rolling(2).min()
    x["wr"] = -100 * (hh - x.close) / (hh - ll)

    tp = (x.high + x.low + x.close) / 3
    ma = tp.rolling(5).mean()
    md = tp.rolling(5).apply(lambda z: np.mean(np.abs(z - np.mean(z))), raw=True)
    x["cci"] = (tp - ma) / (0.015 * md)

    tr = pd.concat([
        (x.high - x.low).abs(),
        (x.high - x.close.shift(1)).abs(),
        (x.low - x.close.shift(1)).abs(),
    ], axis=1).max(axis=1)
    up = x.high.diff()
    dn = -x.low.diff()
    plus_dm = pd.Series(np.where((up > dn) & (up > 0), up, 0.0), index=x.index)
    minus_dm = pd.Series(np.where((dn > up) & (dn > 0), dn, 0.0), index=x.index)
    atr = tr.rolling(20).mean()
    plus_di = 100 * plus_dm.rolling(20).mean() / atr
    minus_di = 100 * minus_dm.rolling(20).mean() / atr
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di)
    x["adx20"] = dx.rolling(20).mean()

    x["prev_high"] = x.high.shift(1)
    x["qqq_ema200"] = x.qqq_close.ewm(span=200, adjust=False, min_periods=200).mean()
    return x


def build_trades(x: pd.DataFrame) -> pd.DataFrame:
    pending = None
    in_position = False
    entry = None
    rows = []
    trade_id = 0

    for dt, r in x.iterrows():
        if pending is not None:
            if pending["action"] == "BUY" and not in_position:
                trade_id += 1
                in_position = True
                entry = {
                    **pending,
                    "trade_id": trade_id,
                    "entry_date": dt,
                    "entry_underlying_open": float(r.open),
                    "entry_underlying_close": float(r.close),
                }
            elif pending["action"] == "SELL" and in_position:
                rows.append({
                    **entry,
                    "exit_signal_date": pending["signal_date"],
                    "exit_reason": pending["exit_reason"],
                    "exit_date": dt,
                    "exit_underlying_open": float(r.open),
                    "exit_underlying_close": float(r.close),
                })
                in_position = False
                entry = None
            pending = None

        if not all(np.isfinite(v) for v in (r.wr, r.cci, r.adx20)):
            continue

        if in_position:
            reasons = []
            if r.close > r.prev_high:
                reasons.append("close>prev_high")
            if r.wr > -30:
                reasons.append("wr>-30")
            if reasons:
                pending = {
                    "action": "SELL",
                    "signal_date": dt,
                    "exit_reason": "+".join(reasons),
                }
        elif r.wr < -90 and r.cci < -80 and r.adx20 >= 15:
            regime_weight = 1.0 if r.qqq_close > r.qqq_ema200 else 0.5
            pending = {
                "action": "BUY",
                "entry_signal_date": dt,
                "regime_weight": regime_weight,
                "wr2": float(r.wr),
                "cci5": float(r.cci),
                "adx20": float(r.adx20),
                "qqq_close": float(r.qqq_close),
                "qqq_ema200": float(r.qqq_ema200),
            }

    return pd.DataFrame(rows)


def get_available_dates(session: requests.Session) -> list[pd.Timestamp]:
    response = session.get(BASE, params={"ticker": "SOXL", "list": "date"}, timeout=60)
    response.raise_for_status()
    values = response.json()
    return sorted(pd.Timestamp(v) for v in values)


def option_rows(session: requests.Session, entry_date: pd.Timestamp, exit_date: pd.Timestamp) -> pd.DataFrame:
    payload = {
        "ticker": "SOXL",
        "portfolio_start": 1_000_000,
        "start_date": entry_date.date().isoformat(),
        "end_date": exit_date.date().isoformat(),
        "real": True,
        "percent_per_trade": 1.0,
        "max_invested": 1.0,
        "distribution": "equal_contracts",
        "legs": [{
            "ticker": "SOXL",
            "type": "call",
            "direction": "long",
            "percent_itm": MONEYNESS,
            "days_till_expiration": TARGET_DTE,
            "rebalance_period": 3650,
            "fee_per_trade": 0.0,
            "fee_per_contract": FEE_PER_CONTRACT_SIDE,
            "prime": True,
            "stop_loss": None,
            "stop_profit": None,
            "stop_at_midpoint": False,
        }],
        "stock": None,
    }
    response = session.post(
        BASE,
        params={"data": "all", "output": "json"},
        json=payload,
        timeout=120,
    )
    response.raise_for_status()
    data = response.json()
    frame = pd.DataFrame(data)
    if frame.empty:
        raise RuntimeError(f"Empty option response for {entry_date.date()} to {exit_date.date()}")
    frame["date"] = pd.to_datetime(frame.date)
    return frame.set_index("date").sort_index()


def safe_float(value) -> float:
    if value in (None, ""):
        return float("nan")
    return float(value)


def performance(equity: pd.Series, trades: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> dict:
    equity = equity.sort_index()
    daily = equity.pct_change().fillna(0.0)
    years = max((end - start).days / 365.25, 1 / 365.25)
    total = float(equity.iloc[-1] / equity.iloc[0] - 1)
    cagr = (1 + total) ** (1 / years) - 1
    max_dd = float((equity / equity.cummax() - 1).min())
    std = float(daily.std(ddof=0))
    sharpe = float(daily.mean() / std * np.sqrt(252)) if std > 0 else np.nan
    calmar = cagr / abs(max_dd) if max_dd < 0 else np.nan
    if len(trades):
        wins = trades.loc[trades.portfolio_trade_return > 0, "portfolio_trade_return"]
        losses = trades.loc[trades.portfolio_trade_return < 0, "portfolio_trade_return"]
        pf = float(wins.sum() / abs(losses.sum())) if len(losses) else math.inf
        win_rate = float((trades.portfolio_trade_return > 0).mean())
    else:
        pf = win_rate = np.nan
    return {
        "start": start.date().isoformat(),
        "end": end.date().isoformat(),
        "trades": int(len(trades)),
        "win_rate": win_rate,
        "profit_factor": pf,
        "avg_option_premium_return": float(trades.option_premium_return.mean()) if len(trades) else np.nan,
        "median_option_premium_return": float(trades.option_premium_return.median()) if len(trades) else np.nan,
        "avg_portfolio_trade_return": float(trades.portfolio_trade_return.mean()) if len(trades) else np.nan,
        "total_return": total,
        "cagr": cagr,
        "sharpe": sharpe,
        "max_drawdown": max_dd,
        "calmar": calmar,
        "ending_equity": float(equity.iloc[-1]),
    }


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    session = requests.Session()
    session.headers.update({"User-Agent": "SOXL-TCAR-research-backtest/1.0"})

    available = get_available_dates(session)
    available_set = set(available)
    recent = [d for d in available if d >= pd.Timestamp("2020-01-01")]
    if not recent:
        raise RuntimeError("No recent SOXL options dates are available")
    data_start, data_end = min(recent), max(recent)

    underlying = prepare()
    all_trades = build_trades(underlying)
    eligible = all_trades.loc[
        all_trades.entry_date.isin(available_set)
        & all_trades.exit_date.isin(available_set)
        & (all_trades.entry_date >= data_start)
        & (all_trades.exit_date <= data_end)
    ].copy()
    if eligible.empty:
        raise RuntimeError("No frozen TCAR trades overlap the available SOXL option archive")

    capital = INITIAL_CAPITAL
    trade_records = []
    equity_points = {data_start: capital}

    for _, trade in eligible.iterrows():
        frame = option_rows(session, trade.entry_date, trade.exit_date)
        valid = frame.loc[frame.index.isin(available_set)].copy()
        valid = valid.loc[(valid.index >= trade.entry_date) & (valid.index <= trade.exit_date)]
        if valid.empty:
            continue
        errors = [str(v) for v in valid.get("error", pd.Series(index=valid.index, dtype=object)).dropna() if str(v)]
        if errors:
            print(f"SKIP trade {trade.trade_id}: provider errors {errors}")
            continue

        first = valid.iloc[0]
        last = valid.iloc[-1]
        entry_ask = safe_float(first.leg1_open_transact)
        if not np.isfinite(entry_ask) or entry_ask <= 0:
            entry_ask = safe_float(first.leg1_ask)
        exit_bid = safe_float(last.leg1_bid)
        if not np.isfinite(entry_ask) or not np.isfinite(exit_bid) or entry_ask <= 0 or exit_bid < 0:
            print(f"SKIP trade {trade.trade_id}: invalid quotes ask={entry_ask} bid={exit_bid}")
            continue

        premium_cap = BULL_PREMIUM_CAP if trade.regime_weight >= 1.0 else BEAR_PREMIUM_CAP
        per_contract_entry = entry_ask * 100 + FEE_PER_CONTRACT_SIDE
        quantity = math.floor((capital * premium_cap) / per_contract_entry)
        if quantity < 1:
            print(f"SKIP trade {trade.trade_id}: less than one contract")
            continue

        capital_before = capital
        premium_paid = quantity * entry_ask * 100
        entry_fee = quantity * FEE_PER_CONTRACT_SIDE
        cash = capital - premium_paid - entry_fee

        for dt, row in valid.iterrows():
            bid = safe_float(row.leg1_bid)
            if np.isfinite(bid) and bid >= 0:
                equity_points[dt] = cash + quantity * bid * 100

        exit_fee = quantity * FEE_PER_CONTRACT_SIDE
        proceeds = quantity * exit_bid * 100 - exit_fee
        capital = cash + proceeds
        equity_points[trade.exit_date] = capital

        option_return = exit_bid / entry_ask - 1
        portfolio_return = capital / capital_before - 1
        record = {
            "trade_id": int(trade.trade_id),
            "entry_signal_date": trade.entry_signal_date.date().isoformat(),
            "entry_date": trade.entry_date.date().isoformat(),
            "exit_signal_date": trade.exit_signal_date.date().isoformat(),
            "exit_date": trade.exit_date.date().isoformat(),
            "exit_reason": trade.exit_reason,
            "regime_weight": float(trade.regime_weight),
            "premium_cap": premium_cap,
            "wr2": float(trade.wr2),
            "cci5": float(trade.cci5),
            "adx20": float(trade.adx20),
            "strike": safe_float(first.leg1_strike),
            "expiration": str(first.leg1_expiry),
            "actual_entry_dte": int((pd.Timestamp(first.leg1_expiry) - trade.entry_date).days),
            "entry_ask": entry_ask,
            "exit_bid": exit_bid,
            "entry_bid_ask_spread": safe_float(first.leg1_ask) - safe_float(first.leg1_bid),
            "quantity": quantity,
            "fees": entry_fee + exit_fee,
            "option_premium_return": option_return,
            "portfolio_trade_return": portfolio_return,
            "capital_before": capital_before,
            "capital_after": capital,
            "underlying_open_return": trade.exit_underlying_open / trade.entry_underlying_open - 1,
            "underlying_close_aligned_return": trade.exit_underlying_close / trade.entry_underlying_close - 1,
        }
        trade_records.append(record)
        print(json.dumps(record))
        time.sleep(0.2)

    trades = pd.DataFrame(trade_records)
    if trades.empty:
        raise RuntimeError("All overlapping trades were skipped")

    calendar = underlying.loc[data_start:data_end].index
    equity = pd.Series(equity_points, dtype=float).sort_index().reindex(calendar).ffill().fillna(INITIAL_CAPITAL)
    summary = performance(equity, trades, data_start, data_end)
    summary.update({
        "strategy_id": "SOXL_TCAR_D1_LONG_CALL_EOD_V1",
        "signal": "WR2<-90 & CCI5<-80 & ADX20>=15",
        "exit": "close>prior_high OR WR2>-30",
        "signal_execution": "next trading date",
        "option_execution": "entry-date EOD ask / exit-date EOD bid",
        "option": "SOXL long call",
        "target_dte": TARGET_DTE,
        "moneyness": "ATM",
        "bull_premium_cap": BULL_PREMIUM_CAP,
        "bear_premium_cap": BEAR_PREMIUM_CAP,
        "fee_per_contract_per_side": FEE_PER_CONTRACT_SIDE,
        "provider": "OnclickMedia EOD archive",
        "available_recent_start": data_start.date().isoformat(),
        "available_recent_end": data_end.date().isoformat(),
        "eligible_tcar_trades": int(len(eligible)),
        "completed_option_trades": int(len(trades)),
    })

    trades.to_csv(OUT / "trade_ledger.csv", index=False)
    equity.rename("equity").to_csv(OUT / "equity_curve.csv")
    (OUT / "summary.json").write_text(json.dumps(summary, indent=2, allow_nan=True) + "\n")
    print("SUMMARY", json.dumps(summary, indent=2, allow_nan=True))


if __name__ == "__main__":
    main()
