#!/usr/bin/env python3
"""Research-only probe of public historical SOXL option data.

This script cannot place brokerage orders. It only calls a historical backtest
service and prints returned research data.
"""
from __future__ import annotations

import json
from pathlib import Path

import requests

BASE = "https://backtest.onclickmedia.com/"


def main() -> None:
    availability = requests.get(
        BASE, params={"ticker": "SOXL", "list": "date"}, timeout=60
    )
    print("availability status:", availability.status_code)
    print(availability.text[:4000])
    availability.raise_for_status()

    # Fixed historical research window only; no brokerage connection or order API.
    payload = {
        "ticker": "SOXL",
        "portfolio_start": 1000000,
        "start_date": "2026-02-20",
        "end_date": "2026-02-27",
        "real": True,
        "percent_per_trade": 1.0,
        "max_invested": 1.0,
        "distribution": "equal_contracts",
        "legs": [{
            "ticker": "SOXL",
            "type": "call",
            "direction": "long",
            "percent_itm": 0.0,
            "days_till_expiration": 37,
            "rebalance_period": 3650,
            "fee_per_trade": 0.0,
            "fee_per_contract": 0.65,
            "prime": True,
            "stop_loss": None,
            "stop_profit": None,
            "stop_at_midpoint": False
        }],
        "stock": None
    }
    response = requests.post(
        BASE,
        params={"data": "all", "output": "json"},
        json=payload,
        timeout=120,
    )
    print("backtest status:", response.status_code)
    print(response.text[:12000])
    response.raise_for_status()
    data = response.json()
    Path("onclick_probe_response.json").write_text(
        json.dumps(data, indent=2), encoding="utf-8"
    )
    if isinstance(data, list) and data:
        print("row count:", len(data))
        print("fields:", sorted(data[0]))


if __name__ == "__main__":
    main()
