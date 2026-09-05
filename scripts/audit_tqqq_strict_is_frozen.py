#!/usr/bin/env python3
"""Evaluate already-selected TQQQ rules; no optimization or order endpoints.

Preserves the original experiment's flat period starts, next-open fills,
zero costs and terminal-close liquidation. Re-fetches Tradier data and
checks the six strategy-period metrics against the saved optimization.
"""
from __future__ import annotations
import hashlib
import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path
import numpy as np
import pandas as pd
import requests

OUT = Path('data/williams_r/tqqq_strict_is_audit')
SOURCE_RESULT = Path('data/williams_r/tqqq_daily_strict_is_optimization.json')
WARMUP, START, END = '2015-09-01', '2016-09-06', '2026-09-04'
INITIAL = 100000.0
PERIODS = {
    'IS': (START, '2022-09-02'),
    'VALIDATION': ('2022-09-06', '2024-09-03'),
    'OOS': ('2024-09-04', END),
    'FULL_10Y': (START, END),
}
TRADE_COLUMNS = ['strategy', 'period', 'trade_id', 'entry_signal_date', 'entry_date',
    'entry_price', 'entry_wr3', 'entry_cci4', 'entry_adx30', 'exit_signal_date',
    'exit_date', 'exit_price', 'exit_reason', 'terminal_close', 'shares',
    'return_fraction', 'pnl_dollars', 'equity_before', 'equity_after',
    'holding_calendar_days', 'holding_trading_sessions']


def serializable(value):
    if isinstance(value, dict):
        return {k: serializable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [serializable(v) for v in value]
    if isinstance(value, (float, np.floating)):
        return float(value) if math.isfinite(value) else None
    if isinstance(value, np.integer):
        return int(value)
    return value


def fetch_history():
    token = os.environ.get('TRADIER_TOKEN')
    if not token:
        raise RuntimeError('TRADIER_TOKEN is required; no alternate data feed is used')
    response = requests.get('https://api.tradier.com/v1/markets/history',
        headers={'Authorization': 'Bearer ' + token, 'Accept': 'application/json'},
        params={'symbol': 'TQQQ', 'interval': 'daily', 'start': WARMUP, 'end': END},
        timeout=60)
    response.raise_for_status()
    payload = response.json()
    rows = (payload.get('history') or {}).get('day') or []
    if isinstance(rows, dict):
        rows = [rows]
    if not rows:
        raise RuntimeError('Tradier returned no history')
    raw = response.content
    (OUT / 'tradier_history_response.json').write_bytes(raw)
    d = pd.DataFrame(rows)
    d['date'] = pd.to_datetime(d['date'])
    d = d.set_index('date').sort_index()
    cols = ['open', 'high', 'low', 'close', 'volume']
    d = d[cols].apply(pd.to_numeric, errors='raise')
    if d.index.has_duplicates or not np.isfinite(d.to_numpy()).all():
        raise RuntimeError('Duplicate dates or invalid numeric data')
    if (d[['open', 'high', 'low', 'close']] <= 0).any().any():
        raise RuntimeError('Non-positive prices')
    if d.loc[START:END].index[0].strftime('%Y-%m-%d') != START or d.index[-1].strftime('%Y-%m-%d') != END:
        raise RuntimeError('Requested date coverage is incomplete')
    d.to_csv(OUT / 'TQQQ_tradier_daily_source.csv')
    return d, hashlib.sha256(raw).hexdigest()


def add_indicators(d):
    d = d.copy()
    hh, ll = d.high.rolling(3).max(), d.low.rolling(3).min()
    d['wr3'] = -100 * (hh - d.close) / (hh - ll).replace(0, np.nan)
    tp = (d.high + d.low + d.close) / 3
    md = tp.rolling(4).apply(lambda a: np.mean(np.abs(a - a.mean())), raw=True)
    d['cci4'] = (tp - tp.rolling(4).mean()) / (0.015 * md.replace(0, np.nan))
    up, down = d.high.diff(), -d.low.diff()
    plus = pd.Series(np.where((up > down) & (up > 0), up, 0.0), index=d.index)
    minus = pd.Series(np.where((down > up) & (down > 0), down, 0.0), index=d.index)
    tr = pd.concat([d.high - d.low, (d.high - d.close.shift()).abs(),
                    (d.low - d.close.shift()).abs()], axis=1).max(axis=1)
    smooth = lambda s: s.ewm(alpha=1/30, adjust=False, min_periods=30).mean()
    atr = smooth(tr)
    plus_di, minus_di = 100*smooth(plus)/atr, 100*smooth(minus)/atr
    dx = 100*(plus_di-minus_di).abs()/(plus_di+minus_di).replace(0, np.nan)
    d['adx30'] = smooth(dx)
    d['prior_high'] = d.high.shift(1)
    return d


def portfolio_metrics(curve, start, end):
    values = curve['equity'].to_numpy(dtype=float)
    returns = values / np.r_[INITIAL, values[:-1]] - 1
    years = (pd.Timestamp(end) - pd.Timestamp(start)).days / 365.25
    total = values[-1]/INITIAL - 1
    cagr = (1+total)**(1/years) - 1
    sd = returns.std(ddof=1)
    peaks = np.maximum.accumulate(np.r_[INITIAL, values])[1:]
    dd = float((values/peaks - 1).min())
    return {'total_return': total, 'cagr': cagr,
        'sharpe': float(returns.mean()/sd*math.sqrt(252)) if sd > 0 else None,
        'max_dd': dd, 'calmar': cagr/abs(dd) if dd < 0 else None,
        'annualized_volatility': float(sd*math.sqrt(252)), 'ending': float(values[-1])}


def evaluate(d, period, use_adx):
    start, end = PERIODS[period]
    x = d.loc[start:end]
    name = 'IS_SELECTED_WR3_CCI4_ADX30' if use_adx else 'FROZEN_WR3_CCI4_BASELINE'
    ent = ((x.wr3 < -70) & (x.cci4 < -75)).to_numpy()
    if use_adx:
        ent = ent & (x.adx30 <= 20).to_numpy()
    exits = ((x.close > x.prior_high) | (x.wr3 > -30)).to_numpy()
    cash, shares, entry = INITIAL, 0.0, None
    pending_exit, exit_signal, reason = False, '', ''
    trades, marks = [], []

    def close_trade(i, price, terminal=False):
        nonlocal cash, shares, entry
        dt = x.index[i].strftime('%Y-%m-%d')
        cash = shares * price
        ret = price / entry['entry_price'] - 1
        trades.append({**entry, 'strategy': name, 'period': period,
            'trade_id': len(trades)+1, 'exit_signal_date': '' if terminal else exit_signal,
            'exit_date': dt, 'exit_price': price,
            'exit_reason': 'PERIOD_END_CLOSE' if terminal else reason,
            'terminal_close': terminal, 'return_fraction': ret,
            'pnl_dollars': cash-entry['equity_before'], 'equity_after': cash,
            'holding_calendar_days': (x.index[i]-pd.Timestamp(entry['entry_date'])).days,
            'holding_trading_sessions': i-entry['entry_index']})
        shares, entry = 0.0, None

    for i, (dt, row) in enumerate(x.iterrows()):
        if entry is not None and pending_exit:
            close_trade(i, float(row.open))
        elif entry is None and i > 0 and ent[i-1]:
            prev = x.iloc[i-1]
            price = float(row.open)
            shares = cash/price
            entry = {'entry_index': i, 'entry_signal_date': x.index[i-1].strftime('%Y-%m-%d'),
                'entry_date': dt.strftime('%Y-%m-%d'), 'entry_price': price,
                'entry_wr3': float(prev.wr3), 'entry_cci4': float(prev.cci4),
                'entry_adx30': float(prev.adx30), 'shares': shares, 'equity_before': cash}
            cash = 0.0
        mark = cash + shares*float(row.close)
        marks.append({'date': dt, 'equity': mark, 'position': int(entry is not None)})
        if entry is not None:
            pending_exit = bool(exits[i])
            exit_signal = dt.strftime('%Y-%m-%d')
            reasons = []
            if row.close > row.prior_high:
                reasons.append('CLOSE_GT_PRIOR_HIGH')
            if row.wr3 > -30:
                reasons.append('WR3_GT_MINUS30')
            reason = '|'.join(reasons)
    if entry is not None:
        close_trade(len(x)-1, float(x.close.iloc[-1]), terminal=True)
        marks[-1]['equity'] = cash
    curve = pd.DataFrame(marks).set_index('date')
    ledger = pd.DataFrame(trades).reindex(columns=TRADE_COLUMNS)
    metrics = portfolio_metrics(curve, start, end)
    ret = ledger.return_fraction.to_numpy(dtype=float)
    negatives = ret[ret < 0]
    metrics.update({'strategy': name, 'period': period, 'start': start, 'end': end,
        'trading_days': len(x), 'trades': len(ret), 'win_rate': float((ret > 0).mean()),
        'pf': float(ret[ret>0].sum()/-negatives.sum()) if len(negatives) else None,
        'avg_trade': float(ret.mean()), 'median_trade': float(np.median(ret)),
        'avg_holding_calendar_days': float(ledger.holding_calendar_days.mean()),
        'avg_holding_trading_sessions': float(ledger.holding_trading_sessions.mean()),
        'exposure': float(curve.position.mean()),
        'terminal_liquidations': int(ledger.terminal_close.sum())})
    curve.to_csv(OUT / f'{period.lower()}_{name.lower()}_equity.csv')
    ledger.to_csv(OUT / f'{period.lower()}_{name.lower()}_trades.csv', index=False)
    return metrics, ledger


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    original_bytes = SOURCE_RESULT.read_bytes()
    original = json.loads(original_bytes)
    win = original['base_is_sharpe_winner']
    assert (win['wr_n'], win['wr_entry'], win['cci_n'], win['cci_entry']) == (3, -70, 4, -75)
    assert original['third_indicator_winner'] == 'ADX30_LE_20'
    d, raw_hash = fetch_history()
    d = add_indicators(d)
    records, checks = [], []
    for period, (start, end) in PERIODS.items():
        for use_adx in [False, True]:
            m, ledger = evaluate(d, period, use_adx)
            records.append(m)
            if period != 'FULL_10Y':
                old = original['performance' if use_adx else 'baseline_performance'][period]
                for key, value in old.items():
                    ok = bool(np.isclose(m[key], value, rtol=1e-8, atol=1e-8))
                    checks.append({'period': period, 'adx_filter': use_adx, 'metric': key,
                                   'saved': value, 'rerun': m[key], 'matched': ok})
            elif use_adx:
                ledger.to_csv(OUT / 'TQQQ_IS_Selected_All_Trades_10Y.csv', index=False)
        x = d.loc[start:end]
        hcurve = pd.DataFrame({'equity': INITIAL*x.close/x.open.iloc[0]}, index=x.index)
        hm = portfolio_metrics(hcurve, start, end)
        hm.update({'strategy': 'TQQQ_BUY_AND_HOLD', 'period': period, 'start': start,
                   'end': end, 'trading_days': len(x), 'exposure': 1.0})
        records.append(hm)
        hcurve.to_csv(OUT / f'{period.lower()}_buy_and_hold_equity.csv')
    pd.DataFrame(records).to_csv(OUT / 'TQQQ_Frozen_Strategy_Comparison.csv', index=False)
    pd.DataFrame(checks).to_csv(OUT / 'metric_reconciliation.csv', index=False)
    report = {'generated_at_utc': datetime.now(timezone.utc).isoformat(),
        'data_source': 'Tradier production GET /v1/markets/history only',
        'symbol': 'TQQQ', 'requested_start': WARMUP, 'requested_end': END,
        'source_rows': len(d), 'raw_response_sha256': raw_hash,
        'original_result_sha256': hashlib.sha256(original_bytes).hexdigest(),
        'source_optimization_commit': '2d0e6b46a6f6702e43a6ac562bbb80e1e90b24ad',
        'optimization_performed_in_audit': False,
        'all_saved_strategy_metrics_reproduced': all(c['matched'] for c in checks),
        'checks': len(checks), 'mismatch_count': sum(not c['matched'] for c in checks),
        'assumptions': {'starting_equity_per_period': INITIAL, 'position_fraction': 1.0,
            'qqq_sizing': False, 'commission_slippage_tax_cash_yield': 0,
            'dividend_cash_payments': 'not modeled; prices used as returned by Tradier',
            'split_start': 'flat; first signal evaluated at first close within each period',
            'split_end': 'liquidate residual position at terminal close, separately flagged',
            'execution': 'completed daily signal followed by next regular-session open',
            'reentry': 'no exit and reentry on same open',
            'sharpe': 'daily equity returns, sample SD, sqrt(252), risk-free rate zero',
            'benchmark_sharpe': 'includes first-day open-to-close return',
            'drawdown': 'daily close marked equity including initial capital anchor',
            'profit_factor': 'sum of positive trade percentage returns / abs(sum negative)',
            'full_period': 'one continuous retrospective run, not a new OOS test'},
        'comparison': records}
    (OUT / 'audit_report.json').write_text(json.dumps(serializable(report), indent=2, allow_nan=False))
    print(json.dumps(serializable(report), indent=2, allow_nan=False))


if __name__ == '__main__':
    main()
