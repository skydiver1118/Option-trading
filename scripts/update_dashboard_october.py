from __future__ import annotations

import json
from datetime import datetime
from zoneinfo import ZoneInfo

import update_dashboard as base

ET = ZoneInfo("America/New_York")
TARGET_EXPIRY = "2026-10-16"  # Standard October monthly expiration (third Friday)


def choose_october_expiry(symbol):
    """Force the dashboard to use the October 16, 2026 standard monthly expiration."""
    sources = []
    if base.TRADIER_TOKEN:
        try:
            sources.append(("Tradier", base.tradier_expirations(symbol)))
        except Exception as exc:
            print(f"Tradier expirations fallback for {symbol}: {exc}")
    try:
        sources.append(("yfinance", base.yfinance_expirations(symbol)))
    except Exception as exc:
        print(f"yfinance expirations unavailable for {symbol}: {exc}")

    for source, dates in sources:
        if TARGET_EXPIRY in dates:
            return TARGET_EXPIRY, source

    # Keep the requested expiration explicit. candidate_puts will try Tradier first
    # and fall back to yfinance; if neither has the chain the ticker remains WAIT.
    return TARGET_EXPIRY, "Tradier" if base.TRADIER_TOKEN else "yfinance"


def main():
    base.choose_expiry = choose_october_expiry
    base.main()

    path = "data/dashboard.json"
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)

    # Rank strictly by option execution score, highest first, regardless of SELL/WAIT/NO TRADE.
    data["ranking"] = sorted(data.get("analysis", []), key=lambda x: float(x.get("score") or 0), reverse=True)
    data["ranking_basis"] = "Option execution score descending"
    data["expiration_policy"] = "October 16, 2026 standard monthly options only; no weeklies"
    data["option_expiration"] = TARGET_EXPIRY
    data["requested_refresh_mode"] = "October monthly"

    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)

    print(f"Dashboard forced to {TARGET_EXPIRY}; ranking sorted by option score at {datetime.now(ET).isoformat()}")


if __name__ == "__main__":
    main()
