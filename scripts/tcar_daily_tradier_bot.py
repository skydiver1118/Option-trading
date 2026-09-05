#!/usr/bin/env python3
"""SOXL daily TCAR execution service for Tradier.

Frozen daily strategy (the repo's finalized TCAR, not DCR-15):
- Signal source: completed SOXL daily regular-session bars from Tradier production.
- Entry after close: WR(2) < -90 AND CCI(5) < -80 AND ADX(20) >= 15.
- Exit after close: Close > previous daily High OR WR(2) > -30.
- Execution: next trading-day regular-session open.
- Long only, one strategy-owned SOXL position, no pyramiding.
- Default paper sizing: 100% of non-leveraged available cash/equity, whole shares.

Paper orders always use TRADIER_SANDBOX_TOKEN. Production market data uses
TRADIER_TOKEN. Real-money mode remains hard locked.
"""
from __future__ import annotations

import csv
import json
import math
import os
import sys
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import requests

ET = ZoneInfo("America/New_York")
SYMBOL = os.getenv("TCAR_SYMBOL", "SOXL")
MODE = os.getenv("TCAR_MODE", "dryrun").lower()
ALLOC = float(os.getenv("TCAR_ALLOCATION_PCT", "1.0"))
POLL = int(os.getenv("TCAR_POLL_SECONDS", "5"))
EXEC_GRACE_SECONDS = int(os.getenv("TCAR_EXECUTION_GRACE_SECONDS", "90"))
UNKNOWN_SUBMISSION_WAIT_SECONDS = int(os.getenv("TCAR_UNKNOWN_SUBMISSION_WAIT_SECONDS", "90"))
POSITION_RECONCILE_GRACE_SECONDS = int(os.getenv("TCAR_POSITION_RECONCILE_GRACE_SECONDS", "20"))
OFFHOURS_POLL_SECONDS = int(os.getenv("TCAR_OFFHOURS_POLL_SECONDS", "300"))
STATE_PATH = Path(os.getenv("TCAR_STATE_PATH", f"runtime/tcar_daily/{MODE}-state.json"))
AUDIT_PATH = Path(os.getenv("TCAR_AUDIT_PATH", f"runtime/tcar_daily/{MODE}-audit.csv"))
LIVE_BASE = "https://api.tradier.com/v1"
SANDBOX_BASE = "https://sandbox.tradier.com/v1"
WR_N, WR_ENTRY, WR_EXIT = 2, -90.0, -30.0
CCI_N, CCI_ENTRY = 5, -80.0
ADX_N, ADX_ENTRY = 20, 15.0
TAG_PREFIX = "TCAR-"
ACTIVE_ORDER_STATUSES = {"pending", "open", "partially_filled", "pending_cancel"}
TERMINAL_ORDER_STATUSES = {"filled", "rejected", "expired", "canceled", "error"}
CALENDAR_CACHE: dict[tuple[str, int, int], list[dict]] = {}


@dataclass
class BrokerCfg:
    base: str
    token: str
    account_id: str | None
    preview: bool


def now_et() -> datetime:
    return datetime.now(ET)


def jdump(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2, default=str), encoding="utf-8")
    tmp.replace(path)


def default_state() -> dict:
    return {
        "strategy": "SOXL_TCAR_DAILY_V1",
        "execution_engine": "daily-v1-safe",
        "state_mode": MODE,
        "last_bar": None,
        "pending": None,
        "active_order": None,
        "submission_unknown": None,
        "halted_reason": None,
        "owned_qty": 0,
        "broker_qty": 0,
        "last_order": None,
        "last_order_status": None,
        "last_reconcile": None,
        "last_fill_at": None,
        "sim_qty": 0,
    }


def load_state() -> dict:
    state = default_state()
    if not STATE_PATH.exists():
        return state
    raw = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    prior_mode = raw.get("state_mode")
    state.update(raw)
    if prior_mode not in (None, MODE):
        state.update({"pending": None, "active_order": None, "submission_unknown": None,
                      "owned_qty": 0, "broker_qty": 0, "sim_qty": 0,
                      "halted_reason": f"state_mode_changed:{prior_mode}->{MODE}"})
    elif prior_mode is None and MODE != "dryrun":
        state.update({"pending": None, "active_order": None, "submission_unknown": None,
                      "owned_qty": 0, "broker_qty": 0, "sim_qty": 0})
    state["state_mode"] = MODE
    state["execution_engine"] = "daily-v1-safe"
    return state


def audit(event: str, **kw) -> None:
    AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)
    row = {"ts_et": now_et().isoformat(), "event": event, **kw}
    fields = ["ts_et", "event", "mode", "bar_date", "wr2", "cci5", "adx20",
              "close", "prev_high", "action", "qty", "order_id", "order_status",
              "exec_quantity", "remaining_quantity", "avg_fill_price", "tag", "note"]
    exists = AUDIT_PATH.exists()
    with AUDIT_PATH.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        if not exists:
            writer.writeheader()
        writer.writerow(row)
    print(json.dumps(row, default=str), flush=True)


def set_halt(state: dict, reason: str) -> None:
    if state.get("halted_reason") != reason:
        state["halted_reason"] = reason
        audit("halt", mode=MODE, note=reason)
        jdump(STATE_PATH, state)


def session(token: str, base: str):
    s = requests.Session()
    s.headers.update({"Authorization": f"Bearer {token}", "Accept": "application/json"})
    s.base = base
    return s


def resolve_account(s, requested=None):
    if requested:
        return requested
    r = s.get(f"{s.base}/user/profile", timeout=20)
    r.raise_for_status()
    accounts = (r.json().get("profile") or {}).get("account") or []
    accounts = [accounts] if isinstance(accounts, dict) else accounts
    active = [a for a in accounts if str(a.get("status", "active")).lower() == "active"]
    if len(active) != 1:
        raise RuntimeError(f"Expected exactly one active account; found {len(active)}. Set TRADIER_ACCOUNT_ID.")
    return active[0]["account_number"]


def broker_cfg() -> BrokerCfg:
    live = os.getenv("TRADIER_TOKEN", "")
    sand = os.getenv("TRADIER_SANDBOX_TOKEN", "")
    requested = os.getenv("TRADIER_ACCOUNT_ID")
    if MODE == "dryrun":
        return BrokerCfg(LIVE_BASE, live, requested, False)
    if MODE == "preview":
        if not live:
            raise RuntimeError("TRADIER_TOKEN required for preview mode")
        s = session(live, LIVE_BASE)
        return BrokerCfg(LIVE_BASE, live, resolve_account(s, requested), True)
    if MODE == "paper":
        if not sand:
            raise RuntimeError("TRADIER_SANDBOX_TOKEN required for paper mode")
        s = session(sand, SANDBOX_BASE)
        return BrokerCfg(SANDBOX_BASE, sand, resolve_account(s, requested), False)
    if MODE == "live":
        if os.getenv("TCAR_LIVE_ENABLE") != "YES_I_ACCEPT_REAL_ORDERS":
            raise RuntimeError("Live mode hard-locked; paper validation required first.")
        if not live:
            raise RuntimeError("TRADIER_TOKEN required for live mode")
        s = session(live, LIVE_BASE)
        return BrokerCfg(LIVE_BASE, live, resolve_account(s, requested), False)
    raise RuntimeError(f"Unknown TCAR_MODE={MODE}")


def market_session():
    token = os.getenv("TRADIER_TOKEN") or os.getenv("TRADIER_SANDBOX_TOKEN")
    base = LIVE_BASE if os.getenv("TRADIER_TOKEN") else SANDBOX_BASE
    if not token:
        raise RuntimeError("Tradier token required for market data")
    return session(token, base)


def market_clock(ms) -> dict:
    r = ms.get(f"{ms.base}/markets/clock", timeout=10)
    r.raise_for_status()
    return r.json().get("clock") or {}


def market_open(ms) -> bool:
    try:
        return market_clock(ms).get("state") == "open"
    except Exception:
        return False


def market_calendar(ms, year: int, month: int) -> list[dict]:
    key = (ms.base, year, month)
    if key in CALENDAR_CACHE:
        return CALENDAR_CACHE[key]
    r = ms.get(f"{ms.base}/markets/calendar", params={"year": year, "month": month}, timeout=20)
    r.raise_for_status()
    days = (((r.json().get("calendar") or {}).get("days") or {}).get("day") or [])
    days = [days] if isinstance(days, dict) else days
    CALENDAR_CACHE[key] = days
    return days


def regular_session_bounds(ms, d: date) -> tuple[datetime, datetime] | None:
    entry = next((x for x in market_calendar(ms, d.year, d.month) if x.get("date") == d.isoformat()), None)
    if not entry or entry.get("status") != "open":
        return None
    info = entry.get("open") or {}
    start = (info.get("start") or "09:30").split(":")
    end = (info.get("end") or "16:00").split(":")
    return (datetime(d.year, d.month, d.day, int(start[0]), int(start[1]), tzinfo=ET),
            datetime(d.year, d.month, d.day, int(end[0]), int(end[1]), tzinfo=ET))


def next_regular_open(ms, after_date: date) -> datetime:
    for offset in range(1, 16):
        d = after_date + timedelta(days=offset)
        bounds = regular_session_bounds(ms, d)
        if bounds:
            return bounds[0]
    raise RuntimeError("Unable to resolve next regular open within 15 calendar days")


def execution_window(ms, signal_date: date) -> tuple[datetime, datetime]:
    execute_at = next_regular_open(ms, signal_date)
    return execute_at, execute_at + timedelta(seconds=EXEC_GRACE_SECONDS)


def fetch_daily_bars(ms, days: int = 180) -> pd.DataFrame:
    now = now_et()
    start = (now.date() - timedelta(days=days)).isoformat()
    end = now.date().isoformat()
    r = ms.get(f"{ms.base}/markets/history",
               params={"symbol": SYMBOL, "interval": "daily", "start": start, "end": end}, timeout=30)
    r.raise_for_status()
    rows = (r.json().get("history") or {}).get("day") or []
    rows = [rows] if isinstance(rows, dict) else rows
    x = pd.DataFrame(rows)
    if x.empty:
        return x
    x["date"] = pd.to_datetime(x["date"])
    for c in ("open", "high", "low", "close", "volume"):
        if c in x:
            x[c] = pd.to_numeric(x[c], errors="coerce")
    return x.dropna(subset=["date", "open", "high", "low", "close"]).drop_duplicates("date").sort_values("date").set_index("date")


def indicators(x: pd.DataFrame) -> pd.DataFrame:
    y = x.copy()
    hh = y.high.rolling(WR_N).max()
    ll = y.low.rolling(WR_N).min()
    y["wr2"] = -100 * (hh - y.close) / (hh - ll)
    tp = (y.high + y.low + y.close) / 3
    ma = tp.rolling(CCI_N).mean()
    md = tp.rolling(CCI_N).apply(lambda z: np.mean(np.abs(z - np.mean(z))), raw=True)
    y["cci5"] = (tp - ma) / (0.015 * md)
    tr = pd.concat([(y.high - y.low).abs(), (y.high - y.close.shift(1)).abs(),
                    (y.low - y.close.shift(1)).abs()], axis=1).max(axis=1)
    up = y.high.diff()
    dn = -y.low.diff()
    pdm = pd.Series(np.where((up > dn) & (up > 0), up, 0.0), index=y.index)
    mdm = pd.Series(np.where((dn > up) & (dn > 0), dn, 0.0), index=y.index)
    atr = tr.rolling(ADX_N).mean()
    pdi = 100 * pdm.rolling(ADX_N).mean() / atr
    mdi = 100 * mdm.rolling(ADX_N).mean() / atr
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi)
    y["adx20"] = dx.rolling(ADX_N).mean()
    y["prev_high"] = y.high.shift(1)
    return y


def completed_rows(ms, x: pd.DataFrame, now: datetime) -> pd.DataFrame:
    keep = []
    for ts in x.index:
        d = ts.date()
        bounds = regular_session_bounds(ms, d)
        keep.append(bool(bounds and now >= bounds[1]))
    return x.loc[keep]


def signal_for_row(row: pd.Series, in_position: bool) -> tuple[str | None, str]:
    if any(pd.isna(row.get(k)) for k in ("wr2", "cci5", "adx20", "prev_high")):
        return None, ""
    if in_position:
        reasons = []
        if float(row.close) > float(row.prev_high):
            reasons.append("close>prev_high")
        if float(row.wr2) > WR_EXIT:
            reasons.append("wr2>-30")
        return ("sell", "+".join(reasons)) if reasons else (None, "")
    if float(row.wr2) < WR_ENTRY and float(row.cci5) < CCI_ENTRY and float(row.adx20) >= ADX_ENTRY:
        return "buy", "wr2<-90+cci5<-80+adx20>=15"
    return None, ""


def get_positions(bs, cfg) -> list[dict]:
    if not cfg.account_id or not cfg.token:
        return []
    r = bs.get(f"{cfg.base}/accounts/{cfg.account_id}/positions", timeout=20)
    if r.status_code == 404:
        return []
    r.raise_for_status()
    rows = (r.json().get("positions") or {}).get("position") or []
    return [rows] if isinstance(rows, dict) else rows


def soxl_qty(bs, cfg) -> int:
    for p in get_positions(bs, cfg):
        if p.get("symbol") == SYMBOL:
            return int(float(p.get("quantity", 0)))
    return 0


def allocatable_cash(bs, cfg) -> float:
    r = bs.get(f"{cfg.base}/accounts/{cfg.account_id}/balances", timeout=20)
    r.raise_for_status()
    b = r.json().get("balances") or {}
    cash = b.get("cash") or {}
    candidates = [b.get("total_cash"), cash.get("cash_available"), b.get("total_equity")]
    vals = [float(v) for v in candidates if v not in (None, "")]
    if not vals:
        raise RuntimeError("No usable cash/equity field returned by Tradier")
    return max(0.0, min(vals))


def quote(ms) -> dict:
    r = ms.get(f"{ms.base}/markets/quotes", params={"symbols": SYMBOL, "greeks": "false"}, timeout=20)
    r.raise_for_status()
    return (r.json().get("quotes") or {}).get("quote") or {}


def entry_qty(ms, bs, cfg) -> int:
    funds = allocatable_cash(bs, cfg)
    q = quote(ms)
    px = float(q.get("ask") or q.get("last") or q.get("close"))
    qty = math.floor(funds * ALLOC / px)
    if qty < 1:
        raise RuntimeError(f"Insufficient funds for one share: funds={funds}, price={px}")
    return qty


def normalize_orders(payload) -> list[dict]:
    rows = (payload.get("orders") or {}).get("order") or []
    return [rows] if isinstance(rows, dict) else rows


def list_orders(bs, cfg) -> list[dict]:
    r = bs.get(f"{cfg.base}/accounts/{cfg.account_id}/orders",
               params={"includeTags": "true", "limit": 1000}, timeout=20)
    if r.status_code == 404:
        return []
    r.raise_for_status()
    return normalize_orders(r.json())


def get_order(bs, cfg, order_id) -> dict:
    r = bs.get(f"{cfg.base}/accounts/{cfg.account_id}/orders/{order_id}",
               params={"includeTags": "true"}, timeout=20)
    r.raise_for_status()
    return r.json().get("order") or r.json()


def exact_tag_orders(bs, cfg, tag: str) -> list[dict]:
    return [o for o in list_orders(bs, cfg) if str(o.get("tag") or "") == tag]


def tcar_active_orders(bs, cfg) -> list[dict]:
    return [o for o in list_orders(bs, cfg)
            if o.get("symbol") == SYMBOL and str(o.get("tag") or "").startswith(TAG_PREFIX)
            and str(o.get("status") or "").lower() in ACTIVE_ORDER_STATUSES]


def make_tag(signal_date: date, action: str) -> str:
    return f"TCAR-{SYMBOL}-{signal_date:%Y%m%d}-{'B' if action == 'buy' else 'S'}"


def order_payload(side: str, qty: int, tag: str, preview: bool) -> dict:
    return {"class": "equity", "symbol": SYMBOL, "side": side, "quantity": qty,
            "type": "market", "duration": "day", "preview": "true" if preview else "false", "tag": tag}


def preview_order(bs, cfg, side: str, qty: int, tag: str) -> dict:
    r = bs.post(f"{cfg.base}/accounts/{cfg.account_id}/orders",
                data=order_payload(side, qty, tag, True),
                headers={"Content-Type": "application/x-www-form-urlencoded"}, timeout=20)
    r.raise_for_status()
    order = r.json().get("order") or r.json()
    if order.get("result") is not True:
        raise RuntimeError(f"Tradier preview failed: status={order.get('status')}")
    return order


def submit_order(bs, cfg, side: str, qty: int, tag: str) -> dict:
    r = bs.post(f"{cfg.base}/accounts/{cfg.account_id}/orders",
                data=order_payload(side, qty, tag, False),
                headers={"Content-Type": "application/x-www-form-urlencoded"}, timeout=20)
    r.raise_for_status()
    return r.json().get("order") or r.json()


def active_from_order(order: dict, action: str, signal_date: str, requested_qty=None) -> dict:
    return {"id": order.get("id"), "tag": order.get("tag"), "action": action,
            "signal_date": signal_date,
            "requested_qty": int(float(requested_qty if requested_qty is not None else order.get("quantity", 0) or 0)),
            "accounted_exec_quantity": 0,
            "last_status": str(order.get("status") or "submitted").lower(),
            "submitted_at": now_et().isoformat()}


def account_fill_delta(state: dict, active: dict, order: dict) -> None:
    executed = int(float(order.get("exec_quantity", 0) or 0))
    accounted = int(active.get("accounted_exec_quantity", 0) or 0)
    if executed < accounted:
        set_halt(state, f"order_exec_quantity_regressed:{active.get('id')}")
        return
    delta = executed - accounted
    if delta <= 0:
        return
    if active.get("action") == "buy":
        state["owned_qty"] = int(state.get("owned_qty", 0)) + delta
    elif active.get("action") == "sell":
        state["owned_qty"] = max(0, int(state.get("owned_qty", 0)) - delta)
    else:
        set_halt(state, f"unknown_active_order_action:{active.get('action')}")
        return
    active["accounted_exec_quantity"] = executed
    state["last_fill_at"] = now_et().isoformat()
    audit("fill_delta", mode=MODE, action=active.get("action"), qty=delta,
          order_id=order.get("id"), order_status=order.get("status"), exec_quantity=executed,
          remaining_quantity=order.get("remaining_quantity"), avg_fill_price=order.get("avg_fill_price"),
          tag=order.get("tag"), note=f"strategy_owned_qty={state['owned_qty']}")


def reconcile_active_order(bs, cfg, state: dict) -> None:
    active = state.get("active_order")
    if not active or MODE in ("dryrun", "preview"):
        return
    order = get_order(bs, cfg, active.get("id"))
    account_fill_delta(state, active, order)
    status = str(order.get("status") or "").lower()
    if status != active.get("last_status"):
        audit("order_status", mode=MODE, action=active.get("action"), qty=active.get("requested_qty"),
              order_id=order.get("id"), order_status=status, exec_quantity=order.get("exec_quantity"),
              remaining_quantity=order.get("remaining_quantity"), avg_fill_price=order.get("avg_fill_price"),
              tag=order.get("tag"), note=order.get("reason_description") or "status transition")
    active["last_status"] = status
    state["last_order"] = order.get("id")
    state["last_order_status"] = status
    if status in TERMINAL_ORDER_STATUSES:
        state["active_order"] = None
    elif status not in ACTIVE_ORDER_STATUSES:
        set_halt(state, f"unknown_order_status:{status or 'blank'}")
    jdump(STATE_PATH, state)


def verify_position_ownership(bs, cfg, state: dict, halt_on_mismatch=True) -> bool:
    if MODE in ("dryrun", "preview"):
        return True
    broker_qty = soxl_qty(bs, cfg)
    state["broker_qty"] = broker_qty
    state["last_reconcile"] = now_et().isoformat()
    owned_qty = int(state.get("owned_qty", 0) or 0)
    if broker_qty == owned_qty:
        return True
    if state.get("last_fill_at"):
        if pd.Timestamp(now_et()) - pd.Timestamp(state["last_fill_at"]) < pd.Timedelta(seconds=POSITION_RECONCILE_GRACE_SECONDS):
            return False
    if halt_on_mismatch:
        set_halt(state, f"position_mismatch:broker={broker_qty},strategy_owned={owned_qty}")
    return False


def recover_pending_existing_order(bs, cfg, state: dict) -> None:
    pending = state.get("pending")
    if MODE in ("dryrun", "preview") or not pending or not pending.get("tag"):
        return
    matches = exact_tag_orders(bs, cfg, pending["tag"])
    if len(matches) > 1:
        set_halt(state, f"duplicate_orders_for_tag:{pending['tag']}")
        return
    if len(matches) == 1:
        o = matches[0]
        expected = "buy" if pending["action"] == "buy" else "sell"
        if o.get("symbol") != SYMBOL or str(o.get("side") or "").lower() != expected:
            set_halt(state, f"tag_order_mismatch:{pending['tag']}")
            return
        state["active_order"] = active_from_order(o, pending["action"], pending["signal_date"], pending.get("qty"))
        state["pending"] = None
        audit("pending_order_recovered", mode=MODE, action=expected, qty=o.get("quantity"),
              order_id=o.get("id"), order_status=o.get("status"), tag=o.get("tag"), note="restart recovery")
        reconcile_active_order(bs, cfg, state)


def recover_active_order(bs, cfg, state: dict) -> None:
    if MODE in ("dryrun", "preview") or state.get("active_order"):
        return
    rows = tcar_active_orders(bs, cfg)
    if len(rows) > 1:
        set_halt(state, "multiple_active_tcar_orders")
        return
    if len(rows) == 1:
        o = rows[0]
        side = str(o.get("side") or "").lower()
        if side not in ("buy", "sell"):
            set_halt(state, f"unexpected_tcar_order_side:{side or 'blank'}")
            return
        state["active_order"] = active_from_order(o, side, now_et().date().isoformat())
        audit("active_order_recovered", mode=MODE, action=side, qty=o.get("quantity"),
              order_id=o.get("id"), order_status=o.get("status"), tag=o.get("tag"), note="startup recovery")
        reconcile_active_order(bs, cfg, state)


def recover_submission_unknown(bs, cfg, state: dict) -> None:
    unknown = state.get("submission_unknown")
    if MODE in ("dryrun", "preview") or not unknown:
        return
    matches = exact_tag_orders(bs, cfg, unknown["tag"])
    if len(matches) > 1:
        set_halt(state, f"duplicate_orders_for_tag:{unknown['tag']}")
        return
    if len(matches) == 1:
        o = matches[0]
        state["active_order"] = active_from_order(o, unknown["action"], unknown["signal_date"], unknown.get("requested_qty"))
        state["submission_unknown"] = None
        audit("order_recovered_by_tag", mode=MODE, action=unknown["action"], qty=unknown.get("requested_qty"),
              order_id=o.get("id"), order_status=o.get("status"), tag=unknown["tag"], note="ambiguous submission recovered")
        reconcile_active_order(bs, cfg, state)
        return
    if pd.Timestamp(now_et()) - pd.Timestamp(unknown["started_at"]) >= pd.Timedelta(seconds=UNKNOWN_SUBMISSION_WAIT_SECONDS):
        set_halt(state, f"submission_result_unknown:{unknown['tag']}")


def submit_pending(ms, bs, cfg, state: dict, pending: dict) -> None:
    action, tag = pending["action"], pending["tag"]
    if MODE not in ("dryrun", "preview"):
        matches = exact_tag_orders(bs, cfg, tag)
        if len(matches) > 1:
            set_halt(state, f"duplicate_orders_for_tag:{tag}")
            return
        if len(matches) == 1:
            o = matches[0]
            state["active_order"] = active_from_order(o, action, pending["signal_date"], pending.get("qty"))
            state["pending"] = None
            audit("order_recovered_by_tag", mode=MODE, action=action, qty=pending.get("qty"),
                  order_id=o.get("id"), order_status=o.get("status"), tag=tag, note="submission skipped")
            reconcile_active_order(bs, cfg, state)
            return
    owned = int(state.get("owned_qty", 0) or 0)
    if action == "buy":
        if owned != 0:
            state["pending"] = None
            audit("signal_canceled", mode=MODE, action="buy", tag=tag, note="strategy already owns SOXL")
            return
        qty = entry_qty(ms, bs, cfg) if MODE not in ("dryrun", "preview") else int(os.getenv("TCAR_DRYRUN_QTY", "1"))
    else:
        if owned <= 0:
            state["pending"] = None
            audit("signal_canceled", mode=MODE, action="sell", tag=tag, note="strategy owns no SOXL")
            return
        qty = owned
    pending["qty"] = qty
    if MODE == "dryrun":
        state["sim_qty"] = qty if action == "buy" else 0
        state["owned_qty"] = state["sim_qty"]
        state["pending"] = None
        audit("order", mode=MODE, action=action, qty=qty, order_id="DRYRUN", order_status="dryrun", tag=tag,
              note="simulated next-session-open execution")
        jdump(STATE_PATH, state)
        return
    preview = preview_order(bs, cfg, action, qty, tag)
    audit("order_preview", mode=MODE, action=action, qty=qty, order_status=preview.get("status"), tag=tag, note="validation passed")
    if MODE == "preview":
        state["pending"] = None
        jdump(STATE_PATH, state)
        return
    try:
        submitted = submit_order(bs, cfg, action, qty, tag)
    except requests.RequestException as exc:
        state["submission_unknown"] = {"tag": tag, "action": action, "signal_date": pending["signal_date"],
                                         "requested_qty": qty, "started_at": now_et().isoformat()}
        state["pending"] = None
        audit("submission_unknown", mode=MODE, action=action, qty=qty, tag=tag,
              note=f"{type(exc).__name__}; recover by tag, never blind-retry")
        jdump(STATE_PATH, state)
        return
    order_id = submitted.get("id")
    if not order_id:
        state["submission_unknown"] = {"tag": tag, "action": action, "signal_date": pending["signal_date"],
                                         "requested_qty": qty, "started_at": now_et().isoformat()}
        state["pending"] = None
        jdump(STATE_PATH, state)
        return
    state["active_order"] = {"id": order_id, "tag": tag, "action": action,
                               "signal_date": pending["signal_date"], "requested_qty": qty,
                               "accounted_exec_quantity": 0, "last_status": "submitted",
                               "submitted_at": now_et().isoformat()}
    state["pending"] = None
    state["last_order"] = order_id
    state["last_order_status"] = "submitted"
    audit("order_submitted", mode=MODE, action=action, qty=qty, order_id=order_id,
          order_status=submitted.get("status"), tag=tag, note="fill not assumed")
    jdump(STATE_PATH, state)
    reconcile_active_order(bs, cfg, state)


def reconcile_runtime(bs, cfg, state: dict) -> None:
    if MODE in ("dryrun", "preview"):
        return
    recover_submission_unknown(bs, cfg, state)
    if state.get("halted_reason"):
        return
    if state.get("active_order"):
        reconcile_active_order(bs, cfg, state)
    jdump(STATE_PATH, state)


def process_once(ms, bs, cfg, state: dict) -> None:
    reconcile_runtime(bs, cfg, state)
    if state.get("halted_reason"):
        return
    now = now_et()
    pending = state.get("pending")
    if pending and not state.get("active_order") and not state.get("submission_unknown"):
        execute_at, expires_at, now_ts = map(pd.Timestamp, (pending["execute_at"], pending["expires_at"], now))
        if now_ts > expires_at:
            audit("missed_execution_window", mode=MODE, action=pending["action"], tag=pending["tag"],
                  note=f"intended={pending['execute_at']} expires={pending['expires_at']}; stale signal not chased")
            state["pending"] = None
            jdump(STATE_PATH, state)
        elif now_ts >= execute_at and market_open(ms):
            if MODE not in ("dryrun", "preview") and not verify_position_ownership(bs, cfg, state):
                jdump(STATE_PATH, state)
                return
            submit_pending(ms, bs, cfg, state, pending)
    if state.get("active_order") or state.get("submission_unknown") or state.get("pending"):
        return
    x = indicators(fetch_daily_bars(ms))
    if x.empty:
        return
    complete = completed_rows(ms, x, now)
    if complete.empty:
        return
    bar_date = complete.index[-1].date()
    if state.get("last_bar") == bar_date.isoformat():
        return
    if MODE not in ("dryrun", "preview") and not verify_position_ownership(bs, cfg, state):
        jdump(STATE_PATH, state)
        return
    row = complete.iloc[-1]
    owned = int(state.get("owned_qty", state.get("sim_qty", 0)) or 0)
    action, reason = signal_for_row(row, owned > 0)
    state["last_bar"] = bar_date.isoformat()
    if action:
        execute_at, expires_at = execution_window(ms, bar_date)
        tag = make_tag(bar_date, action)
        state["pending"] = {"action": action, "signal_date": bar_date.isoformat(),
                              "execute_at": execute_at.isoformat(), "expires_at": expires_at.isoformat(),
                              "reason": reason, "tag": tag}
        audit("signal", mode=MODE, bar_date=bar_date.isoformat(), wr2=float(row.wr2),
              cci5=float(row.cci5), adx20=float(row.adx20), close=float(row.close),
              prev_high=float(row.prev_high), action=action, tag=tag,
              note=f"{reason}; execute_at={execute_at.isoformat()}")
    else:
        audit("bar", mode=MODE, bar_date=bar_date.isoformat(), wr2=float(row.wr2) if pd.notna(row.wr2) else None,
              cci5=float(row.cci5) if pd.notna(row.cci5) else None,
              adx20=float(row.adx20) if pd.notna(row.adx20) else None, close=float(row.close),
              prev_high=float(row.prev_high) if pd.notna(row.prev_high) else None, note="no signal")
    jdump(STATE_PATH, state)


def startup_reconcile(bs, cfg, state: dict) -> None:
    if MODE in ("dryrun", "preview"):
        return
    recover_submission_unknown(bs, cfg, state)
    if state.get("halted_reason"):
        return
    recover_pending_existing_order(bs, cfg, state)
    if state.get("halted_reason"):
        return
    recover_active_order(bs, cfg, state)
    if state.get("active_order"):
        reconcile_active_order(bs, cfg, state)
    if state.get("halted_reason"):
        return
    verify_position_ownership(bs, cfg, state, halt_on_mismatch=not bool(state.get("active_order")))
    jdump(STATE_PATH, state)


def sleep_seconds(state: dict) -> int:
    now = pd.Timestamp(now_et())
    if state.get("active_order") or state.get("submission_unknown"):
        return POLL
    pending = state.get("pending")
    if pending:
        try:
            until = (pd.Timestamp(pending["execute_at"]) - now).total_seconds()
            if until <= 600:
                return POLL
            return max(POLL, min(OFFHOURS_POLL_SECONDS, int(until - 600)))
        except Exception:
            return POLL
    t = now_et().time()
    # Poll more closely just after the regular close so the new daily bar is captured.
    if datetime.strptime("15:55", "%H:%M").time() <= t <= datetime.strptime("16:30", "%H:%M").time():
        return 30
    return OFFHOURS_POLL_SECONDS


def main() -> None:
    if not 0 < ALLOC <= 1:
        raise RuntimeError("TCAR_ALLOCATION_PCT must be >0 and <=1")
    if EXEC_GRACE_SECONDS < POLL:
        raise RuntimeError("TCAR_EXECUTION_GRACE_SECONDS must be >= TCAR_POLL_SECONDS")
    cfg = broker_cfg()
    ms = market_session()
    bs = session(cfg.token, cfg.base) if cfg.token else ms
    state = load_state()
    startup_reconcile(bs, cfg, state)
    jdump(STATE_PATH, state)
    once = "--once" in sys.argv
    while True:
        try:
            process_once(ms, bs, cfg, state)
        except Exception as exc:
            audit("error", mode=MODE, note=repr(exc))
        if once:
            break
        time.sleep(sleep_seconds(state))


if __name__ == "__main__":
    main()
