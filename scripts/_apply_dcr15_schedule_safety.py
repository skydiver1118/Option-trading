from pathlib import Path

p = Path('scripts/dcr15_tradier_bot.py')
s = p.read_text(encoding='utf-8')

anchor = '''def recover_active_session_order(bs, cfg, state: dict) -> None:
'''

new_function = '''def recover_pending_existing_order(bs, cfg, state: dict) -> None:
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


'''

if 'def recover_pending_existing_order' not in s:
    if anchor not in s:
        raise SystemExit('recover_active_session_order anchor not found')
    s = s.replace(anchor, new_function + anchor, 1)

old_side = '''        tag = str(order.get("tag") or "")
        action = "buy" if str(order.get("side") or "") == "buy" else "sell"
        state["active_order"] = active_from_order(order, action, now_et().isoformat())
'''
new_side = '''        tag = str(order.get("tag") or "")
        side = str(order.get("side") or "").lower()
        if side not in ("buy", "sell"):
            set_halt(state, f"unexpected_dcr15_order_side:{side or 'blank'}")
            return
        action = side
        state["active_order"] = active_from_order(order, action, now_et().isoformat())
'''
if old_side in s:
    s = s.replace(old_side, new_side, 1)
elif 'unexpected_dcr15_order_side' not in s:
    raise SystemExit('recovery side block not found')

old_startup = '''    recover_submission_unknown(bs, cfg, state)
    if state.get("halted_reason"):
        return
    recover_active_session_order(bs, cfg, state)
'''
new_startup = '''    recover_submission_unknown(bs, cfg, state)
    if state.get("halted_reason"):
        return
    recover_pending_existing_order(bs, cfg, state)
    if state.get("halted_reason"):
        return
    recover_active_session_order(bs, cfg, state)
'''
# Replace only the startup copy: after def startup_reconcile.
startup_pos = s.find('def startup_reconcile')
if startup_pos < 0:
    raise SystemExit('startup_reconcile not found')
head, tail = s[:startup_pos], s[startup_pos:]
if old_startup in tail:
    tail = tail.replace(old_startup, new_startup, 1)
elif 'recover_pending_existing_order(bs, cfg, state)' not in tail:
    raise SystemExit('startup recovery block not found')
s = head + tail

p.write_text(s, encoding='utf-8')
print('DCR15 crash-recovery safety patch applied')
