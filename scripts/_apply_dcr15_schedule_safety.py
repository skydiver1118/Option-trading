from pathlib import Path
import re

p = Path('scripts/dcr15_tradier_bot.py')
s = p.read_text(encoding='utf-8')

if 'OFFHOURS_POLL_SECONDS' not in s:
    s = s.replace(
        'POSITION_RECONCILE_GRACE_SECONDS = int(os.getenv("DCR15_POSITION_RECONCILE_GRACE_SECONDS", "15"))\n',
        'POSITION_RECONCILE_GRACE_SECONDS = int(os.getenv("DCR15_POSITION_RECONCILE_GRACE_SECONDS", "15"))\n'
        'OFFHOURS_POLL_SECONDS = int(os.getenv("DCR15_OFFHOURS_POLL_SECONDS", "60"))\n',
        1,
    )

if 'CALENDAR_CACHE:' not in s:
    s = s.replace(
        'TERMINAL_ORDER_STATUSES = {"filled", "rejected", "expired", "canceled", "error"}\n',
        'TERMINAL_ORDER_STATUSES = {"filled", "rejected", "expired", "canceled", "error"}\n'
        'CALENDAR_CACHE: dict[tuple[str, int, int], list[dict]] = {}\n',
        1,
    )

schedule_block = '''def market_calendar(ms, year: int, month: int) -> list[dict]:
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
'''

s, n = re.subn(
    r'def market_calendar\(ms, year: int, month: int\) -> list\[dict\]:.*?(?=\ndef fetch_bars\()',
    schedule_block.rstrip() + '\n',
    s,
    count=1,
    flags=re.S,
)
if n != 1:
    raise SystemExit(f'calendar block replacement count={n}')

runtime_block = '''def reconcile_runtime(bs, cfg, state: dict) -> None:
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
'''

s, n = re.subn(
    r'def reconcile_runtime\(bs, cfg, state: dict\) -> None:.*?(?=\ndef startup_reconcile\()',
    runtime_block.rstrip() + '\n',
    s,
    count=1,
    flags=re.S,
)
if n != 1:
    raise SystemExit(f'runtime block replacement count={n}')

old_loop = '''    while True:
        try:
            process_once(ms, bs, cfg, state)
        except Exception as e:
            audit("error", mode=MODE, note=repr(e))
        if once:
            break
        time.sleep(POLL)
'''
new_loop = '''    while True:
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
'''
if old_loop not in s:
    raise SystemExit('main loop pattern not found')
s = s.replace(old_loop, new_loop, 1)

p.write_text(s, encoding='utf-8')
print('DCR15 schedule/off-hours safety patch applied')
