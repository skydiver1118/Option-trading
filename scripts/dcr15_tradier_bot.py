#!/usr/bin/env python3
"""DCR-15 automated execution service for SOXL.

Frozen strategy rules:
- 15-minute regular-session bars only.
- Enter long when WR(5)<-80 AND CCI(5)<-80.
- Exit when Close>previous 15m high OR WR(5)>-30 OR CCI(5)>0.
- Execute at the next regular-session 15-minute bar open.
- One strategy-owned long position only; no pyramiding.

Execution modes:
- dryrun: no broker call.
- preview: order preview only; never submits an order.
- paper: Tradier sandbox orders using TRADIER_SANDBOX_TOKEN.
- live: production orders, hard-locked unless explicitly enabled outside ChatGPT.

Production market data uses TRADIER_TOKEN when present. Paper orders always use the
sandbox token. The execution layer is fail-safe: active order reconciliation,
partial-fill accounting, duplicate-tag recovery, position-ownership checks, and
strict next-bar execution windows are enforced before another order can be sent.
"""
from __future__ import annotations

import csv
import json
import math
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, time as dtime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import requests

ET = ZoneInfo("America/New_York")
SYMBOL = os.getenv("DCR15_SYMBOL", "SOXL")
MODE = os.getenv("DCR15_MODE", "dryrun").lower()
ALLOC = float(os.getenv("DCR15_ALLOCATION_PCT", "1.0"))
POLL = int(os.getenv("DCR15_POLL_SECONDS", "5"))
EXEC_GRACE_SECONDS = int(os.getenv("DCR15_EXECUTION_GRACE_SECONDS", "60"))
UNKNOWN_SUBMISSION_WAIT_SECONDS = int(os.getenv("DCR15_UNKNOWN_SUBMISSION_WAIT_SECONDS", "60"))
POSITION_RECONCILE_GRACE_SECONDS = int(os.getenv("DCR15_POSITION_RECONCILE_GRACE_SECONDS", "15"))
OFFHOURS_POLL_SECONDS = int(os.getenv("DCR15_OFFHOURS_POLL_SECONDS", "60"))
STATE_PATH = Path(os.getenv("DCR15_STATE_PATH", f"runtime/dcr15/{MODE}-state.json"))
AUDIT_PATH = Path(os.getenv("DCR15_AUDIT_PATH", f"runtime/dcr15/{MODE}-audit.csv"))
LIVE_BASE = "https://api.tradier.com/v1"
SANDBOX_BASE = "https://sandbox.tradier.com/v1"
WR_N, WR_ENTRY, WR_EXIT = 5, -80.0, -30.0
CCI_N, CCI_ENTRY, CCI_EXIT = 5, -80.0, 0.0
TAG_PREFIX = "DCR15-"
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
        "strategy": "SOXL_DCR15_V1",
        "execution_engine": "v2-safe",
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
    previous_mode = raw.get("state_mode")
    state.update(raw)
    if previous_mode not in (None, MODE):
        for key in ("pending", "active_order", "submission_unknown"):
            state[key] = None
        state["owned_qty"] = 0
        state["broker_qty"] = 0
        state["sim_qty"] = 0
        state["halted_reason"] = f"state_mode_changed:{previous_mode}->{MODE}"
    elif previous_mode is None and MODE != "dryrun":
        for key in ("pending", "active_order", "submission_unknown"):
            state[key] = None
        state["owned_qty"] = 0
        state["broker_qty"] = 0
        state["sim_qty"] = 0
    state["state_mode"] = MODE
    state["execution_engine"] = "v2-safe"
    return state


def audit(event: str, **kw) -> None:
    AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)
    row = {"ts_et": now_et().isoformat(), "event": event, **kw}
    fields = [
        "ts_et", "event", "mode", "bar_dt", "wr", "cci", "close", "prev_high",
        "action", "qty", "order_id", "order_status", "exec_quantity",
        "remaining_quantity", "avg_fill_price", "tag", "note",
    ]
    exists = AUDIT_PATH.exists()
    with AUDIT_PATH.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        if not exists:
            w.writeheader()
        w.writerow(row)
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
    active = [x for x in accounts if x.get("status", "active") == "active"]
    if len(active) != 1:
        raise RuntimeError(
            f"Expected exactly one active account; found {len(active)}. "
            "Set TRADIER_ACCOUNT_ID explicitly."
        )
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
        if os.getenv("TRADIER_LIVE_ENABLE") != "YES_I_ACCEPT_REAL_ORDERS":
            raise RuntimeError(
                "Live mode hard-locked; enable only outside ChatGPT after paper validation."
            )
        if not live:
            raise RuntimeError("TRADIER_TOKEN required for live mode")
        s = session(live, LIVE_BASE)
        return BrokerCfg(LIVE_BASE, live, resolve_account(s, requested), False)
    raise RuntimeError(f"Unknown DCR15_MODE={MODE}")


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
    r = ms.get(
        f"{ms.base}/markets/calendar",
        params={"year": year, "month": month},
        timeout=20,
    )
    r.raise_for_status()
    days = (((r.json().get("calendar") or {}).get("days") or {}).get("day") or [])
    days = [days] if isinstance(days, dict) else days
    CALENDAR_CACHE[key] = days
    return days


def regular_session_bounds(ms, d) -> tuple[datetime, datetime] | None:
    days = market_calendar(ms, d.year, d.month)
    entry = next((x for x in days if x.get("date") == d.isoformat()), None)
    if not entry or entry.get("status") != "open":
        return None
    session_info = entry.get("open") or {}
    start = (session_info.get("start") or "09:30").strip()
    end = (session_info.get("end") or "16:00").strip()
    sh, sm = [int(v) for v in start.split(":")[:2]]
    eh, em = [int(v) for v in end.split(":")[:2]]
    return (
        datetime(d.year, d.month, d.day, sh, sm, tzinfo=ET),
        datetime(d.year, d.month, d.day, eh, em, tzinfo=ET),
    )


def next_regular_open(ms, signal_bar: datetime) -> datetime:
    target = signal_bar.date() + timedelta(days=1)
    for offset in range(0, 15):
        d = target + timedelta(days=offset)
        bounds = regular_session_bounds(ms, d)
        if bounds:
            return bounds[0]
    raise RuntimeError("Unable to resolve next regular market open within 15 calendar days")


def execution_window(ms, bar_dt: pd.Timestamp) -> tuple[datetime, datetime]:
    signal = bar_dt.to_pydatetime().astimezone(ET)
    candidate = signal + timedelta(minutes=15)
    bounds = regular_session_bounds(ms, signal.date())
    if bounds is None:
        raise RuntimeError(f"Signal bar fell on non-trading date: {signal.date()}")
    _, session_close = bounds
    execute_at = candidate if candidate < session_close else next_regular_open(ms, signal)
    return execute_at, execute_at + timedelta(seconds=EXEC_GRACE_SECONDS)


def should_fetch_bars(ms, state: dict, now: datetime) -> bool:
    bounds = regular_session_bounds(ms, now.date())
    if bounds is None:
        return False
    session_open, session_close = bounds
    if now < session_open:
        return False
    if now < session_close:
        return True
    final_bar_start = session_close - timedelta(minutes=15)
    last_bar = state.get("last_bar")
    if not last_bar:
        return True
    try:
        last = pd.Timestamp(last_bar).to_pydatetime().astimezone(ET)
    except Exception:
        return True
    return last < final_bar_start

def fetch_bars(ms, days=10):
    now = now_et()
    start = (now - timedelta(days=days)).strftime("%Y-%m-%d 09:30")
    end = now.strftime("%Y-%m-%d %H:%M")
    r = ms.get(
        f"{ms.base}/markets/timesales",
        params={
            "symbol": SYMBOL,
            "interval": "15min",
            "start": start,
            "end": end,
            "session_filter": "open",
        },
        timeout=20,
    )
    r.raise_for_status()
    data = ((r.json().get("series") or {}).get("data") or [])
    data = [data] if isinstance(data, dict) else data
    x = pd.DataFrame(data)
    if x.empty:
        return x
    tc = "time" if "time" in x else "timestamp"
    dt = pd.to_datetime(x[tc], errors="coerce")
    if dt.dt.tz is None:
        dt = dt.dt.tz_localize(ET, nonexistent="shift_forward", ambiguous="NaT")
    else:
        dt = dt.dt.tz_convert(ET)
    x["dt"] = dt
    for c in ["open", "high", "low", "close", "volume"]:
        if c in x:
            x[c] = pd.to_numeric(x[c], errors="coerce")
    x = (
        x.dropna(subset=["dt", "open", "high", "low", "close"])
        .sort_values("dt")
        .drop_duplicates("dt")
        .set_index("dt")
    )
    t = x.index.time
    return x[(t >= dtime(9, 30)) & (t <= dtime(15, 45))]


def indicators(x):
    y = x.copy()
    hh = y.high.rolling(WR_N).max()
    ll = y.low.rolling(WR_N).min()
    den = hh - ll
    y["wr"] = np.where(den.ne(0), -100 * (hh - y.close) / den, np.nan)
    tp = (y.high + y.low + y.close) / 3
    ma = tp.rolling(CCI_N).mean()
    md = tp.rolling(CCI_N).apply(lambda z: np.mean(np.abs(z - np.mean(z))), raw=True)
    y["cci"] = np.where(md.ne(0), (tp - ma) / (0.015 * md), np.nan)
    y["prev_high"] = y.high.shift(1)
    return y


def get_positions(bs, cfg):
    if not cfg.account_id or not cfg.token:
        return []
    r = bs.get(f"{cfg.base}/accounts/{cfg.account_id}/positions", timeout=20)
    if r.status_code == 404:
        return []
    r.raise_for_status()
    p = (r.json().get("positions") or {}).get("position") or []
    return [p] if isinstance(p, dict) else p


def soxl_qty(bs, cfg) -> int:
    for p in get_positions(bs, cfg):
        if p.get("symbol") == SYMBOL:
            return int(float(p.get("quantity", 0)))
    return 0


def allocatable_cash(bs, cfg) -> float:
    r = bs.get(f"{cfg.base}/accounts/{cfg.account_id}/balances", timeout=20)
    r.raise_for_status()
    b = r.json().get("balances") or {}
    cashobj = b.get("cash") or {}
    candidates = [b.get("total_cash"), cashobj.get("cash_available"), b.get("total_equity")]
    vals = [float(v) for v in candidates if v not in (None, "")]
    if not vals:
        raise RuntimeError("No usable cash/equity field returned by Tradier")
    return max(0.0, min(vals) if len(vals) > 1 else vals[0])


def quote(ms):
    r = ms.get(
        f"{ms.base}/markets/quotes",
        params={"symbols": SYMBOL, "greeks": "false"},
        timeout=20,
    )
    r.raise_for_status()
    return (r.json().get("quotes") or {}).get("quote") or {}


def entry_order_qty(ms, bs, cfg) -> int:
    funds = allocatable_cash(bs, cfg)
    q = quote(ms)
    px = float(q.get("ask") or q.get("last") or q.get("close"))
    qty = math.floor(funds * ALLOC / px)
    if qty < 1:
        raise RuntimeError(f"Insufficient funds: funds={funds}, px={px}")
    return qty


def normalize_orders(payload) -> list[dict]:
    orders = (payload.get("orders") or {}).get("order") or []
    return [orders] if isinstance(orders, dict) else orders


def list_orders(bs, cfg) -> list[dict]:
    r = bs.get(
        f"{cfg.base}/accounts/{cfg.account_id}/orders",
        params={"includeTags": "true", "limit": 1000},
        timeout=20,
    )
    if r.status_code == 404:
        return []
    r.raise_for_status()
    return normalize_orders(r.json())


def get_order(bs, cfg, order_id) -> dict:
    r = bs.get(
        f"{cfg.base}/accounts/{cfg.account_id}/orders/{order_id}",
        params={"includeTags": "true"},
        timeout=20,
    )
    r.raise_for_status()
    return r.json().get("order") or r.json()


def exact_tag_orders(bs, cfg, tag: str) -> list[dict]:
    return [o for o in list_orders(bs, cfg) if str(o.get("tag") or "") == tag]


def dcr15_active_orders(bs, cfg) -> list[dict]:
    out = []
    for o in list_orders(bs, cfg):
        tag = str(o.get("tag") or "")
        status = str(o.get("status") or "").lower()
        if o.get("symbol") == SYMBOL and tag.startswith(TAG_PREFIX) and status in ACTIVE_ORDER_STATUSES:
            out.append(o)
    return out


def make_tag(signal_bar: str, action: str) -> str:
    ts = pd.Timestamp(signal_bar)
    return f"DCR15-{SYMBOL}-{ts:%Y%m%d%H%M}-{'B' if action == 'buy' else 'S'}"


def order_payload(side: str, qty: int, tag: str, preview: bool) -> dict:
    return {
        "class": "equity",
        "symbol": SYMBOL,
        "side": side,
        "quantity": qty,
        "type": "market",
        "duration": "day",
        "preview": "true" if preview else "false",
        "tag": tag,
    }


def preview_order(bs, cfg, side: str, qty: int, tag: str) -> dict:
    payload = order_payload(side, qty, tag, True)
    r = bs.post(
        f"{cfg.base}/accounts/{cfg.account_id}/orders",
        data=payload,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=20,
    )
    r.raise_for_status()
    order = r.json().get("order") or r.json()
    if order.get("result") is not True:
        raise RuntimeError(f"Tradier preview failed: status={order.get('status')}")
    return order


def submit_order(bs, cfg, side: str, qty: int, tag: str) -> dict:
    payload = order_payload(side, qty, tag, False)
    r = bs.post(
        f"{cfg.base}/accounts/{cfg.account_id}/orders",
        data=payload,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=20,
    )
    r.raise_for_status()
    return r.json().get("order") or r.json()


def active_from_order(order: dict, action: str, signal_bar: str, requested_qty: int | None = None) -> dict:
    return {
        "id": order.get("id"),
        "tag": order.get("tag"),
        "action": action,
        "side": order.get("side") or ("buy" if action == "buy" else "sell"),
        "signal_bar": signal_bar,
        "requested_qty": int(float(requested_qty if requested_qty is not None else order.get("quantity", 0) or 0)),
        "accounted_exec_quantity": 0,
        "last_status": str(order.get("status") or "submitted").lower(),
        "submitted_at": now_et().isoformat(),
    }


def account_fill_delta(state: dict, active: dict, order: dict) -> None:
    exec_qty = int(float(order.get("exec_quantity", 0) or 0))
    accounted = int(active.get("accounted_exec_quantity", 0) or 0)
    if exec_qty < accounted:
        set_halt(state, f"order_exec_quantity_regressed:{active.get('id')}")
        return
    delta = exec_qty - accounted
    if delta <= 0:
        return
    action = active.get("action")
    if action == "buy":
        state["owned_qty"] = int(state.get("owned_qty", 0)) + delta
    elif action == "sell":
        state["owned_qty"] = int(state.get("owned_qty", 0)) - delta
        if state["owned_qty"] < 0:
            set_halt(state, "owned_qty_below_zero")
            state["owned_qty"] = 0
    else:
        set_halt(state, f"unknown_active_order_action:{action}")
        return
    active["accounted_exec_quantity"] = exec_qty
    state["last_fill_at"] = now_et().isoformat()
    audit(
        "fill_delta",
        mode=MODE,
        action=action,
        qty=delta,
        order_id=order.get("id"),
        order_status=order.get("status"),
        exec_quantity=exec_qty,
        remaining_quantity=order.get("remaining_quantity"),
        avg_fill_price=order.get("avg_fill_price"),
        tag=order.get("tag"),
        note=f"strategy_owned_qty={state.get('owned_qty')}",
    )


def reconcile_active_order(bs, cfg, state: dict) -> None:
    active = state.get("active_order")
    if not active or MODE in ("dryrun", "preview"):
        return
    order_id = active.get("id")
    if not order_id:
        set_halt(state, "active_order_missing_id")
        return
    try:
        order = get_order(bs, cfg, order_id)
    except requests.HTTPError as e:
        if e.response is not None and e.response.status_code == 404:
            set_halt(state, f"active_order_not_found:{order_id}")
            return
        raise
    account_fill_delta(state, active, order)
    status = str(order.get("status") or "").lower()
    previous = active.get("last_status")
    active["last_status"] = status
    state["last_order"] = order_id
    state["last_order_status"] = status
    if status != previous:
        audit(
            "order_status",
            mode=MODE,
            action=active.get("action"),
            qty=active.get("requested_qty"),
            order_id=order_id,
            order_status=status,
            exec_quantity=order.get("exec_quantity"),
            remaining_quantity=order.get("remaining_quantity"),
            avg_fill_price=order.get("avg_fill_price"),
            tag=order.get("tag") or active.get("tag"),
            note=order.get("reason_description") or "status transition",
        )
    if status in TERMINAL_ORDER_STATUSES:
        audit(
            "order_terminal",
            mode=MODE,
            action=active.get("action"),
            qty=active.get("requested_qty"),
            order_id=order_id,
            order_status=status,
            exec_quantity=order.get("exec_quantity"),
            remaining_quantity=order.get("remaining_quantity"),
            avg_fill_price=order.get("avg_fill_price"),
            tag=order.get("tag") or active.get("tag"),
            note=order.get("reason_description") or "terminal",
        )
        state["active_order"] = None
    elif status not in ACTIVE_ORDER_STATUSES:
        set_halt(state, f"unknown_order_status:{status or 'blank'}")
    jdump(STATE_PATH, state)


def recover_submission_unknown(bs, cfg, state: dict) -> None:
    unknown = state.get("submission_unknown")
    if not unknown or MODE in ("dryrun", "preview"):
        return
    matches = exact_tag_orders(bs, cfg, unknown["tag"])
    if len(matches) > 1:
        set_halt(state, f"duplicate_orders_for_tag:{unknown['tag']}")
        return
    if len(matches) == 1:
        order = matches[0]
        state["active_order"] = active_from_order(
            order,
            unknown["action"],
            unknown["signal_bar"],
            unknown.get("requested_qty"),
        )
        state["submission_unknown"] = None
        audit(
            "order_recovered_by_tag",
            mode=MODE,
            action=unknown["action"],
            qty=unknown.get("requested_qty"),
            order_id=order.get("id"),
            order_status=order.get("status"),
            tag=unknown["tag"],
            note="recovered after ambiguous submission result",
        )
        reconcile_active_order(bs, cfg, state)
        return
    started = pd.Timestamp(unknown["started_at"])
    if pd.Timestamp(now_et()) - started >= pd.Timedelta(seconds=UNKNOWN_SUBMISSION_WAIT_SECONDS):
        set_halt(state, f"submission_result_unknown:{unknown['tag']}")


def recover_pending_existing_order(bs, cfg, state: dict) -> None:
    """Recover an order that may have been submitted just before a process crash.

    A signal is persisted before submission. If the broker accepted the order but
    the process died before active_order was persisted, the same unique tag will
    still be present in state.pending. Recover that broker order instead of ever
    resubmitting the signal.
    """
    if MODE in ("dryrun", "preview"):
        return
    pending = state.get("pending")
    if not pending or not pending.get("tag"):
        return
    matches = exact_tag_orders(bs, cfg, pending["tag"])
    if len(matches) > 1:
        set_halt(state, f"duplicate_orders_for_tag:{pending['tag']}")
        return
    if not matches:
        return
    order = matches[0]
    expected_side = "buy" if pending.get("action") == "buy" else "sell"
    if order.get("symbol") != SYMBOL or str(order.get("side") or "").lower() != expected_side:
        set_halt(state, f"tag_order_mismatch:{pending['tag']}")
        return
    state["active_order"] = active_from_order(
        order,
        pending["action"],
        pending["signal_bar"],
        pending.get("qty"),
    )
    state["pending"] = None
    audit(
        "pending_order_recovered",
        mode=MODE,
        action=pending["action"],
        qty=pending.get("qty") or order.get("quantity"),
        order_id=order.get("id"),
        order_status=order.get("status"),
        tag=order.get("tag") or pending["tag"],
        note="broker order recovered from persisted pending signal after restart",
    )
    reconcile_active_order(bs, cfg, state)


def recover_active_session_order(bs, cfg, state: dict) -> None:
    if MODE in ("dryrun", "preview") or state.get("active_order"):
        return
    active = dcr15_active_orders(bs, cfg)
    if len(active) > 1:
        set_halt(state, "multiple_active_dcr15_orders")
        return
    if len(active) == 1:
        order = active[0]
        tag = str(order.get("tag") or "")
        side = str(order.get("side") or "").lower()
        if side not in ("buy", "sell"):
            set_halt(state, f"unexpected_dcr15_order_side:{side or 'blank'}")
            return
        action = side
        state["active_order"] = active_from_order(order, action, now_et().isoformat())
        audit(
            "active_order_recovered",
            mode=MODE,
            action=action,
            qty=order.get("quantity"),
            order_id=order.get("id"),
            order_status=order.get("status"),
            tag=tag,
            note="recovered current-session active DCR15 order",
        )
        reconcile_active_order(bs, cfg, state)


def verify_position_ownership(bs, cfg, state: dict, *, halt_on_mismatch: bool = True) -> bool:
    if MODE in ("dryrun", "preview"):
        return True
    broker_qty = soxl_qty(bs, cfg)
    state["broker_qty"] = broker_qty
    state["last_reconcile"] = now_et().isoformat()
    owned_qty = int(state.get("owned_qty", 0) or 0)
    if broker_qty == owned_qty:
        return True
    last_fill = state.get("last_fill_at")
    if last_fill:
        age = pd.Timestamp(now_et()) - pd.Timestamp(last_fill)
        if age < pd.Timedelta(seconds=POSITION_RECONCILE_GRACE_SECONDS):
            return False
    if halt_on_mismatch:
        set_halt(state, f"position_mismatch:broker={broker_qty},strategy_owned={owned_qty}")
    return False


def submit_pending(ms, bs, cfg, state: dict, pending: dict) -> None:
    action = pending["action"]
    tag = pending["tag"]
    matches = exact_tag_orders(bs, cfg, tag) if MODE not in ("dryrun", "preview") else []
    if len(matches) > 1:
        set_halt(state, f"duplicate_orders_for_tag:{tag}")
        return
    if len(matches) == 1:
        order = matches[0]
        state["active_order"] = active_from_order(order, action, pending["signal_bar"], pending.get("qty"))
        state["pending"] = None
        audit(
            "order_recovered_by_tag",
            mode=MODE,
            action=action,
            qty=pending.get("qty"),
            order_id=order.get("id"),
            order_status=order.get("status"),
            tag=tag,
            note="order already existed; submission skipped",
        )
        reconcile_active_order(bs, cfg, state)
        return

    owned_qty = int(state.get("owned_qty", 0) or 0)
    if action == "buy":
        if owned_qty != 0:
            state["pending"] = None
            audit("signal_canceled", mode=MODE, action="buy", tag=tag, note="strategy already owns SOXL")
            return
        qty = entry_order_qty(ms, bs, cfg) if MODE not in ("dryrun", "preview") else int(os.getenv("DCR15_DRYRUN_QTY", "1"))
        side = "buy"
    else:
        if owned_qty <= 0:
            state["pending"] = None
            audit("signal_canceled", mode=MODE, action="sell", tag=tag, note="strategy owns no SOXL")
            return
        qty = owned_qty
        side = "sell"
    pending["qty"] = qty

    if MODE == "dryrun":
        state["sim_qty"] = qty if action == "buy" else 0
        state["owned_qty"] = state["sim_qty"]
        state["pending"] = None
        audit("order", mode=MODE, action=action, qty=qty, order_id="DRYRUN", order_status="dryrun", tag=tag, note="simulated next-bar-open execution")
        jdump(STATE_PATH, state)
        return

    if MODE == "preview":
        result = preview_order(bs, cfg, side, qty, tag)
        state["pending"] = None
        audit("order_preview", mode=MODE, action=action, qty=qty, order_status=result.get("status"), tag=tag, note="preview only; no order submitted")
        jdump(STATE_PATH, state)
        return

    preview = preview_order(bs, cfg, side, qty, tag)
    audit("order_preview", mode=MODE, action=action, qty=qty, order_status=preview.get("status"), tag=tag, note="validation passed")
    try:
        submitted = submit_order(bs, cfg, side, qty, tag)
    except requests.RequestException as e:
        state["submission_unknown"] = {
            "tag": tag,
            "action": action,
            "signal_bar": pending["signal_bar"],
            "requested_qty": qty,
            "started_at": now_et().isoformat(),
        }
        state["pending"] = None
        audit("submission_unknown", mode=MODE, action=action, qty=qty, tag=tag, note=f"{type(e).__name__}; will recover by tag, never blind-retry")
        jdump(STATE_PATH, state)
        return

    order_id = submitted.get("id")
    if not order_id:
        state["submission_unknown"] = {
            "tag": tag,
            "action": action,
            "signal_bar": pending["signal_bar"],
            "requested_qty": qty,
            "started_at": now_et().isoformat(),
        }
        state["pending"] = None
        audit("submission_unknown", mode=MODE, action=action, qty=qty, tag=tag, note="broker response contained no order id")
        jdump(STATE_PATH, state)
        return

    state["active_order"] = {
        "id": order_id,
        "tag": tag,
        "action": action,
        "side": side,
        "signal_bar": pending["signal_bar"],
        "requested_qty": qty,
        "accounted_exec_quantity": 0,
        "last_status": "submitted",
        "submitted_at": now_et().isoformat(),
    }
    state["pending"] = None
    state["last_order"] = order_id
    state["last_order_status"] = "submitted"
    audit("order_submitted", mode=MODE, action=action, qty=qty, order_id=order_id, order_status=submitted.get("status"), tag=tag, note="broker accepted API request; fill not assumed")
    jdump(STATE_PATH, state)
    reconcile_active_order(bs, cfg, state)


def reconcile_runtime(bs, cfg, state: dict) -> None:
    if MODE in ("dryrun", "preview"):
        return
    if state.get("submission_unknown"):
        recover_submission_unknown(bs, cfg, state)
    if state.get("halted_reason"):
        return
    if state.get("active_order"):
        reconcile_active_order(bs, cfg, state)
    jdump(STATE_PATH, state)


def process_once(ms, bs, cfg, state):
    reconcile_runtime(bs, cfg, state)
    if state.get("halted_reason"):
        return

    now = now_et()

    # Handle a pending next-bar order before requesting a new bar. This allows an
    # overnight final-bar signal to execute promptly at the next regular open.
    if not state.get("active_order") and not state.get("submission_unknown"):
        pending = state.get("pending")
        if pending:
            execute_at = pd.Timestamp(pending["execute_at"])
            expires_at = pd.Timestamp(pending["expires_at"])
            now_ts = pd.Timestamp(now)
            if now_ts > expires_at:
                audit(
                    "missed_execution_window",
                    mode=MODE,
                    action=pending.get("action"),
                    tag=pending.get("tag"),
                    note=f"intended={pending['execute_at']} expires={pending['expires_at']}; stale signal not chased",
                )
                state["pending"] = None
                jdump(STATE_PATH, state)
            elif now_ts >= execute_at and market_open(ms):
                if MODE not in ("dryrun", "preview") and not verify_position_ownership(bs, cfg, state):
                    jdump(STATE_PATH, state)
                    return
                submit_pending(ms, bs, cfg, state, pending)

    if state.get("active_order") or state.get("submission_unknown"):
        return

    if not should_fetch_bars(ms, state, now):
        return

    x = indicators(fetch_bars(ms))
    if x.empty or len(x) < 6:
        return
    complete = x[x.index + pd.Timedelta(minutes=15) <= pd.Timestamp(now)]
    if complete.empty:
        return
    bar_dt = complete.index[-1]
    row = complete.iloc[-1]

    if state.get("last_bar") == bar_dt.isoformat():
        return
    if state.get("pending") or state.get("active_order") or state.get("submission_unknown"):
        return

    if MODE not in ("dryrun", "preview") and not verify_position_ownership(bs, cfg, state):
        jdump(STATE_PATH, state)
        return

    owned_qty = int(state.get("owned_qty", state.get("sim_qty", 0)) or 0)
    wr = float(row.wr)
    cci = float(row.cci)
    cl = float(row.close)
    ph = float(row.prev_high)
    action = None
    reason = ""
    if owned_qty > 0:
        reasons = []
        if cl > ph:
            reasons.append("close>prev_high")
        if wr > WR_EXIT:
            reasons.append("wr_exit")
        if cci > CCI_EXIT:
            reasons.append("cci_exit")
        if reasons:
            action = "sell"
            reason = "+".join(reasons)
    elif wr < WR_ENTRY and cci < CCI_ENTRY:
        action = "buy"
        reason = "wr_entry+cci_entry"

    state["last_bar"] = bar_dt.isoformat()
    if action:
        execute_at, expires_at = execution_window(ms, bar_dt)
        tag = make_tag(bar_dt.isoformat(), action)
        state["pending"] = {
            "action": action,
            "signal_bar": bar_dt.isoformat(),
            "execute_at": execute_at.isoformat(),
            "expires_at": expires_at.isoformat(),
            "reason": reason,
            "tag": tag,
        }
        audit(
            "signal",
            mode=MODE,
            bar_dt=bar_dt.isoformat(),
            wr=wr,
            cci=cci,
            close=cl,
            prev_high=ph,
            action=action,
            tag=tag,
            note=f"{reason}; execute_at={execute_at.isoformat()}",
        )
    else:
        audit(
            "bar",
            mode=MODE,
            bar_dt=bar_dt.isoformat(),
            wr=wr,
            cci=cci,
            close=cl,
            prev_high=ph,
            note="no signal",
        )
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
    recover_active_session_order(bs, cfg, state)
    reconcile_active_order(bs, cfg, state)
    if state.get("halted_reason"):
        return
    verify_position_ownership(bs, cfg, state, halt_on_mismatch=not bool(state.get("active_order")))
    jdump(STATE_PATH, state)


def main():
    if not 0 < ALLOC <= 1:
        raise RuntimeError("DCR15_ALLOCATION_PCT must be >0 and <=1")
    if EXEC_GRACE_SECONDS < POLL:
        raise RuntimeError("DCR15_EXECUTION_GRACE_SECONDS must be >= DCR15_POLL_SECONDS")

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
        except Exception as e:
            audit("error", mode=MODE, note=repr(e))
        if once:
            break
        current = now_et()
        near_rth = current.weekday() < 5 and dtime(9, 20) <= current.time() <= dtime(16, 15)
        urgent = bool(state.get("active_order") or state.get("submission_unknown"))
        pending = state.get("pending")
        if pending:
            try:
                until_exec = (pd.Timestamp(pending["execute_at"]) - pd.Timestamp(current)).total_seconds()
                urgent = urgent or (-EXEC_GRACE_SECONDS <= until_exec <= 600)
            except Exception:
                urgent = True
        time.sleep(POLL if (near_rth or urgent) else OFFHOURS_POLL_SECONDS)


if __name__ == "__main__":
    main()
