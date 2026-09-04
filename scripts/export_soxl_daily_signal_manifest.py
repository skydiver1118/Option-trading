#!/usr/bin/env python3
"""Export the immutable v1 daily SOXL action manifest for QuantConnect.

Tradier daily bars remain the signal source of truth. QuantConnect consumes the
resulting action dates only, so option-universe RAW normalization cannot alter
the frozen WR(2)/CCI(5) strategy around SOXL corporate actions.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
from pprint import pformat

import numpy as np
import pandas as pd
import requests


OUT = Path("data/williams_r")
QC_OUT = Path("quantconnect/soxl_wr2_cci5_long_call")
SOURCE_SNAPSHOT = OUT / "soxl_daily_v1_source_snapshot.csv"
MANIFEST_CSV = OUT / "soxl_daily_v1_signal_manifest.csv"
MANIFEST_METADATA = OUT / "soxl_daily_v1_signal_manifest_metadata.json"

BASE_URL = "https://api.tradier.com/v1"
SOURCE_START = "2015-09-04"
SOURCE_END = "2026-09-03"
WR_N = 2
WE = -90
WX = -30
CCI_N = 5
CE = -80
CX = 0

# Fixed v1 research boundaries. Do not roll these dates forward.
PERIODS = {
    "IS": ("2016-09-06", "2022-09-02"),
    "VALIDATION": ("2022-09-06", "2024-08-30"),
    "OOS": ("2024-09-03", "2026-09-03"),
}
EXPECTED_CLOSED_TRADES = {"IS": 71, "VALIDATION": 27, "OOS": 18}


def fetch(symbol: str) -> pd.DataFrame:
    token = os.environ["TRADIER_TOKEN"]
    session = requests.Session()
    session.headers.update(
        {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    )
    rows = []
    cursor = pd.Timestamp(SOURCE_START)
    end = pd.Timestamp(SOURCE_END)
    while cursor <= end:
        stop = min(end, cursor + pd.DateOffset(years=8) - pd.Timedelta(days=1))
        response = session.get(
            f"{BASE_URL}/markets/history",
            params={
                "symbol": symbol,
                "interval": "daily",
                "start": cursor.date().isoformat(),
                "end": stop.date().isoformat(),
            },
            timeout=30,
        )
        response.raise_for_status()
        payload = (response.json().get("history") or {}).get("day") or []
        rows.extend([payload] if isinstance(payload, dict) else payload)
        cursor = stop + pd.Timedelta(days=1)

    frame = pd.DataFrame(rows)
    if frame.empty:
        raise RuntimeError(f"Tradier returned no daily history for {symbol}")
    frame["date"] = pd.to_datetime(frame.date)
    for column in ("open", "high", "low", "close"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame.drop_duplicates("date").sort_values("date").set_index("date")


def load_or_create_source_snapshot() -> tuple[pd.DataFrame, str]:
    OUT.mkdir(parents=True, exist_ok=True)
    if SOURCE_SNAPSHOT.exists() != MANIFEST_METADATA.exists():
        raise RuntimeError(
            "Frozen source snapshot and metadata must either both exist or both be absent"
        )
    if SOURCE_SNAPSHOT.exists():
        source_bytes = SOURCE_SNAPSHOT.read_bytes()
        prior_metadata = json.loads(MANIFEST_METADATA.read_text(encoding="utf-8"))
        expected_sha256 = prior_metadata.get("source_sha256")
        actual_sha256 = hashlib.sha256(source_bytes).hexdigest()
        if not expected_sha256 or actual_sha256 != expected_sha256:
            raise RuntimeError(
                "Frozen source snapshot SHA-256 does not match its pinned metadata"
            )
        source = pd.read_csv(SOURCE_SNAPSHOT, parse_dates=["date"]).set_index("date")
    else:
        soxl = fetch("SOXL")[["open", "high", "low", "close"]].rename(
            columns=lambda name: f"soxl_{name}"
        )
        qqq = fetch("QQQ")[["close"]].rename(columns={"close": "qqq_close"})
        if not soxl.index.equals(qqq.index):
            only_soxl = sorted(set(soxl.index) - set(qqq.index))
            only_qqq = sorted(set(qqq.index) - set(soxl.index))
            raise RuntimeError(
                "SOXL/QQQ trading dates differ; refusing an implicit inner join: "
                f"SOXL-only={only_soxl[:5]}, QQQ-only={only_qqq[:5]}"
            )
        source = soxl.join(qqq, how="inner")
        source.reset_index().to_csv(SOURCE_SNAPSHOT, index=False)
        source_bytes = SOURCE_SNAPSHOT.read_bytes()

    expected_columns = {"soxl_open", "soxl_high", "soxl_low", "soxl_close", "qqq_close"}
    if not expected_columns.issubset(source.columns):
        raise RuntimeError(f"Source snapshot is missing columns: {expected_columns - set(source.columns)}")
    if source.index.has_duplicates:
        raise RuntimeError("Source snapshot contains duplicate dates")
    if source.index.min().date().isoformat() != SOURCE_START:
        raise RuntimeError(
            f"Source snapshot starts {source.index.min().date()}, expected {SOURCE_START}"
        )
    if source.index.max().date().isoformat() != SOURCE_END:
        raise RuntimeError(
            f"Source snapshot ends {source.index.max().date()}, expected {SOURCE_END}"
        )
    values = source[list(expected_columns)].to_numpy(dtype=float)
    if not np.isfinite(values).all():
        raise RuntimeError("Source snapshot contains missing or non-finite prices")

    return source.sort_index(), hashlib.sha256(source_bytes).hexdigest()


def prepare(source: pd.DataFrame) -> pd.DataFrame:
    data = source.rename(
        columns={
            "soxl_open": "open",
            "soxl_high": "high",
            "soxl_low": "low",
            "soxl_close": "close",
        }
    ).copy()
    data["ema200"] = data.qqq_close.ewm(
        span=200,
        adjust=False,
        min_periods=200,
    ).mean()
    highest = data.high.rolling(WR_N).max()
    lowest = data.low.rolling(WR_N).min()
    data["wr"] = -100 * (highest - data.close) / (highest - lowest)
    typical = (data.high + data.low + data.close) / 3
    mean = typical.rolling(CCI_N).mean()
    deviation = typical.rolling(CCI_N).apply(
        lambda values: np.mean(np.abs(values - np.mean(values))),
        raw=True,
    )
    data["cci"] = (typical - mean) / (0.015 * deviation)
    data["prev_high"] = data.high.shift(1)
    return data


def _exit_reason(row: pd.Series) -> str:
    reasons = []
    if pd.notna(row.prev_high) and row.close > row.prev_high:
        reasons.append("close>prev_high")
    if row.wr > WX:
        reasons.append("wr>-30")
    if row.cci > CX:
        reasons.append("cci>0")
    return "+".join(reasons)


def build_period_manifest(data: pd.DataFrame, period: str) -> list[dict]:
    start, end = PERIODS[period]
    segment = data.loc[start:end]
    if segment.empty:
        raise RuntimeError(f"No rows for {period} period")
    if segment.index.min().date().isoformat() != start:
        raise RuntimeError(f"{period} starts {segment.index.min().date()}, expected {start}")
    if segment.index.max().date().isoformat() != end:
        raise RuntimeError(f"{period} ends {segment.index.max().date()}, expected {end}")
    rows: list[dict] = []
    pending: dict | None = None
    in_position = False
    trade_id = 0

    for execution_date, row in segment.iterrows():
        # A close-generated action executes at the following row's open.
        if pending is not None:
            if pending["action"] == "BUY":
                trade_id += 1
                in_position = True
                pending["trade_id"] = trade_id
            else:
                in_position = False
                pending["trade_id"] = trade_id

            pending["execution_date"] = execution_date.date().isoformat()
            pending["execution_underlying_open"] = float(row.open)
            rows.append(pending)
            pending = None

        if pd.isna(row.wr) or pd.isna(row.cci):
            continue

        signal_date = execution_date.date().isoformat()
        common = {
            "period": period,
            "signal_date": signal_date,
            "wr2": float(row.wr),
            "cci5": float(row.cci),
            "soxl_close": float(row.close),
            "previous_high": float(row.prev_high),
            "qqq_close": float(row.qqq_close),
            "qqq_ema200": float(row.ema200) if pd.notna(row.ema200) else None,
        }

        if in_position:
            reason = _exit_reason(row)
            if reason:
                pending = {
                    **common,
                    "action": "SELL",
                    "regime_weight": None,
                    "exit_reason": reason,
                }
        elif row.wr < WE and row.cci < CE:
            pending = {
                **common,
                "action": "BUY",
                "regime_weight": 1.0 if pd.notna(row.ema200) and row.qqq_close > row.ema200 else 0.5,
                "exit_reason": "",
            }

    return rows


def write_python_manifest(rows: list[dict]) -> None:
    grouped = {
        period: [row for row in rows if row["period"] == period]
        for period in PERIODS
    }
    header = (
        '"""Generated Tradier action dates for frozen SOXL daily strategy v1.\n\n'
        "Do not hand-edit. Regenerate with scripts/export_soxl_daily_signal_manifest.py.\n"
        '"""\n\n'
    )
    body = "SIGNAL_MANIFEST = " + pformat(grouped, width=100, sort_dicts=False) + "\n"
    QC_OUT.mkdir(parents=True, exist_ok=True)
    (QC_OUT / "signal_manifest.py").write_text(header + body, encoding="utf-8")


def validate_manifest(rows: list[dict]) -> dict:
    frame = pd.DataFrame(rows)
    required_finite = {
        "wr2",
        "cci5",
        "soxl_close",
        "previous_high",
        "qqq_close",
        "execution_underlying_open",
    }
    for index, row in frame.iterrows():
        for field in required_finite:
            if not math.isfinite(float(row[field])):
                raise RuntimeError(f"Manifest row {index} has non-finite {field}")
        if row.action == "BUY" and not math.isfinite(float(row.regime_weight)):
            raise RuntimeError(f"Manifest BUY row {index} has invalid regime weight")

    closed_counts = (
        frame.loc[frame.action == "SELL"].groupby("period").size().to_dict()
    )
    if closed_counts != EXPECTED_CLOSED_TRADES:
        raise RuntimeError(
            f"Closed-trade counts drifted: {closed_counts}; expected {EXPECTED_CLOSED_TRADES}"
        )
    for period in PERIODS:
        actions = frame.loc[frame.period == period, "action"].tolist()
        if not actions or actions[0] != "BUY":
            raise RuntimeError(f"{period} manifest does not start with BUY")
        if any(left == right for left, right in zip(actions, actions[1:])):
            raise RuntimeError(f"{period} manifest actions do not alternate")
    return closed_counts


def main() -> None:
    source, source_sha256 = load_or_create_source_snapshot()
    data = prepare(source)
    rows = [
        action
        for period in PERIODS
        for action in build_period_manifest(data, period)
    ]

    frame = pd.DataFrame(rows)
    closed_counts = validate_manifest(rows)
    frame.to_csv(MANIFEST_CSV, index=False)
    write_python_manifest(rows)

    metadata = {
        "strategy_version": "soxl-daily-wr2-cci5-qqq-ema200-v1",
        "source_start": SOURCE_START,
        "source_end": SOURCE_END,
        "source_snapshot": SOURCE_SNAPSHOT.as_posix(),
        "source_sha256": source_sha256,
        "periods": PERIODS,
        "parameters": {
            "wr_lookback": WR_N,
            "wr_entry": WE,
            "wr_exit": WX,
            "cci_lookback": CCI_N,
            "cci_entry": CE,
            "cci_exit": CX,
            "bull_weight": 1.0,
            "bear_weight": 0.5,
            "execution": "next_trading_row",
        },
        "closed_trades": closed_counts,
        "actions": len(rows),
    }
    MANIFEST_METADATA.write_text(
        json.dumps(metadata, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    counts = frame.groupby(["period", "action"]).size().unstack(fill_value=0)
    print(counts.to_string())
    print(f"Exported {len(frame)} actions across {len(PERIODS)} fixed periods")


if __name__ == "__main__":
    main()
