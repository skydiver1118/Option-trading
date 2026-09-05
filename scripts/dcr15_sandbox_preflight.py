#!/usr/bin/env python3
"""Read-only DCR-15 connectivity check. Never imports or starts the trading bot.

Only GET is implemented. Sandbox: profile, balances, positions, orders.
Production: SOXL quote and 15-minute bars only. Credentials and raw responses
are never written to disk, printed, or included in error messages.
"""
from __future__ import annotations

import json
import math
import os
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
BASES = {"sandbox": "https://sandbox.tradier.com/v1", "market": "https://api.tradier.com/v1"}
ENV_KEYS = {"sandbox": "TRADIER_SANDBOX_TOKEN", "market": "TRADIER_TOKEN"}


class CheckError(Exception):
    """Exception messages contain fixed codes, never API response contents."""


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise CheckError("REDIRECT_BLOCKED")


def read_json(environment: str, path: str, params: dict | None = None) -> dict:
    allowed = (environment == "sandbox" and (
        path == "/user/profile" or re.fullmatch(r"/accounts/[A-Za-z0-9_-]+/(balances|positions|orders)", path)
    )) or (environment == "market" and path in ("/markets/quotes", "/markets/timesales"))
    if not allowed:
        raise CheckError("ENDPOINT_NOT_ALLOWLISTED")
    token = os.environ.get(ENV_KEYS[environment], "").strip()
    if not token:
        raise CheckError("SECRET_NOT_AVAILABLE_TO_WORKFLOW")
    url = BASES[environment] + path + (("?" + urlencode(params)) if params else "")
    request = Request(url, headers={"Authorization": "Bearer " + token, "Accept": "application/json"}, method="GET")
    try:
        with build_opener(NoRedirect()).open(request, timeout=25) as response:
            payload = json.load(response)
    except HTTPError as exc:
        raise CheckError(f"HTTP_{int(exc.code)}") from None
    except (URLError, TimeoutError, OSError):
        raise CheckError("NETWORK_OR_TLS_ERROR") from None
    except (ValueError, TypeError):
        raise CheckError("INVALID_JSON") from None
    if not isinstance(payload, dict) or payload.get("errors") or payload.get("fault"):
        raise CheckError("API_RESPONSE_ERROR")
    return payload


def rows_from(parent, key: str) -> list[dict]:
    if parent in (None, "null", {}):
        return []
    if not isinstance(parent, dict):
        raise CheckError("UNEXPECTED_COLLECTION_SCHEMA")
    value = parent.get(key)
    if value in (None, "null", []):
        return []
    if isinstance(value, dict):
        return [value]
    if isinstance(value, list) and all(isinstance(row, dict) for row in value):
        return value
    raise CheckError("UNEXPECTED_COLLECTION_SCHEMA")


def number(value) -> float:
    try:
        result = float(value)
    except (ValueError, TypeError):
        raise CheckError("INVALID_NUMERIC_FIELD") from None
    if not math.isfinite(result):
        raise CheckError("INVALID_NUMERIC_FIELD")
    return result


def main() -> int:
    now = datetime.now(ET)
    report = {"checked_at_et": now.isoformat(), "check_type": "READ_ONLY_CONNECTIVITY",
              "orders_submitted": 0, "bot_started": False, "http_methods": ["GET"], "checks": {}}
    checks = report["checks"]
    for env_key in ENV_KEYS.values():
        checks[env_key] = "PRESENT" if os.environ.get(env_key, "").strip() else "MISSING"
    account = None
    try:
        payload = read_json("sandbox", "/user/profile")
        accounts = rows_from(payload.get("profile"), "account")
        checks["sandbox_authentication"] = "PASS"
        active = [a for a in accounts if str(a.get("status", "")).lower() == "active"]
        if len(active) != 1:
            checks["sandbox_account_resolution"] = "NO_ACTIVE_ACCOUNT" if not active else "MULTIPLE_ACTIVE_ACCOUNTS"
        else:
            candidate = str(active[0].get("account_number", ""))
            if not re.fullmatch(r"[A-Za-z0-9_-]+", candidate):
                raise CheckError("INVALID_ACCOUNT_SCHEMA")
            account = candidate
            checks["sandbox_account_resolution"] = "PASS"
    except CheckError as exc:
        checks["sandbox_authentication"] = str(exc)
    if account is not None:
        for endpoint in ("balances", "positions", "orders"):
            try:
                payload = read_json("sandbox", f"/accounts/{account}/{endpoint}")
                if endpoint not in payload:
                    raise CheckError("MISSING_RESPONSE_FIELD")
                if endpoint == "balances":
                    if not isinstance(payload[endpoint], dict) or not payload[endpoint]:
                        raise CheckError("INVALID_BALANCE_SCHEMA")
                else:
                    rows_from(payload[endpoint], "position" if endpoint == "positions" else "order")
                checks["sandbox_" + endpoint] = "PASS"
            except CheckError as exc:
                checks["sandbox_" + endpoint] = str(exc)
    else:
        for endpoint in ("balances", "positions", "orders"):
            checks["sandbox_" + endpoint] = "NOT_TESTED"
    try:
        quotes = rows_from(read_json("market", "/markets/quotes", {"symbols": "SOXL"}).get("quotes"), "quote")
        if len(quotes) != 1 or quotes[0].get("symbol") != "SOXL":
            raise CheckError("INVALID_QUOTE_SCHEMA")
        if number(quotes[0].get("last") or quotes[0].get("close")) <= 0:
            raise CheckError("INVALID_QUOTE_PRICE")
        checks["production_SOXL_quote"] = "PASS"
    except CheckError as exc:
        checks["production_SOXL_quote"] = str(exc)
    try:
        payload = read_json("market", "/markets/timesales", {
            "symbol": "SOXL", "interval": "15min", "session_filter": "open",
            "start": (now - timedelta(days=10)).strftime("%Y-%m-%d 09:30"), "end": now.strftime("%Y-%m-%d %H:%M")})
        complete = {}
        for bar in rows_from(payload.get("series"), "data"):
            stamp = datetime.fromisoformat(str(bar.get("time", "")))
            stamp = stamp.replace(tzinfo=ET) if stamp.tzinfo is None else stamp.astimezone(ET)
            slot = stamp.hour * 60 + stamp.minute
            if not (570 <= slot < 960 and (slot - 570) % 15 == 0 and stamp + timedelta(minutes=15) <= now):
                continue
            o, h, l, c = [number(bar.get(field)) for field in ("open", "high", "low", "close")]
            if min(o, h, l, c) <= 0 or h < max(o, c) or l > min(o, c) or l > h:
                raise CheckError("INVALID_OHLC")
            if stamp in complete:
                raise CheckError("DUPLICATE_BAR")
            complete[stamp] = (o, h, l, c)
        if len(complete) < 6:
            raise CheckError("INSUFFICIENT_COMPLETED_BARS")
        report["completed_rth_bars"] = len(complete)
        report["latest_completed_bar_start_et"] = max(complete).isoformat()
        checks["production_SOXL_15min_bars"] = "PASS"
    except (ValueError, TypeError):
        checks["production_SOXL_15min_bars"] = "INVALID_TIME_SCHEMA"
    except CheckError as exc:
        checks["production_SOXL_15min_bars"] = str(exc)
    report["status"] = "PASS" if all(v in ("PRESENT", "PASS") for v in checks.values()) else "FAILED"
    text = json.dumps(report, indent=2)
    path = Path(os.environ.get("DCR15_CHECK_REPORT", "runtime/dcr15/preflight-status.json"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text + "\n", encoding="utf-8")
    print(text, flush=True)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with Path(summary).open("a", encoding="utf-8") as handle:
            handle.write("## DCR-15 read-only connectivity\n\n```json\n" + text + "\n```\n")
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        # Suppress raw exception content: URLs can contain account identifiers.
        print('{"status":"FAILED","error":"INTERNAL_CHECK_ERROR","orders_submitted":0,"bot_started":false}')
        sys.exit(2)
