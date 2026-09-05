import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path

import pandas as pd

os.environ['TCAR_MODE'] = 'paper'
os.environ['TCAR_STATE_PATH'] = str(Path(tempfile.gettempdir()) / 'tcar-daily-test-state.json')
os.environ['TCAR_AUDIT_PATH'] = str(Path(tempfile.gettempdir()) / 'tcar-daily-test-audit.csv')
os.environ['TCAR_EXECUTION_GRACE_SECONDS'] = '90'

BOT_PATH = Path(__file__).resolve().parents[1] / 'scripts' / 'tcar_daily_tradier_bot.py'
spec = importlib.util.spec_from_file_location('tcar_daily_bot', BOT_PATH)
bot = importlib.util.module_from_spec(spec)
sys.modules['tcar_daily_bot'] = bot
spec.loader.exec_module(bot)


class Resp:
    def __init__(self, data, status=200):
        self._data = data
        self.status_code = status
        self.response = self

    def json(self):
        return self._data

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests
            exc = requests.HTTPError(f'status {self.status_code}')
            exc.response = self
            raise exc


class FakeCalendar:
    base = 'https://calendar.invalid/v1'

    def get(self, url, params=None, timeout=None):
        if url.endswith('/markets/calendar'):
            days = [
                {'date': '2026-09-04', 'status': 'open', 'open': {'start': '09:30', 'end': '16:00'}},
                {'date': '2026-09-05', 'status': 'closed'},
                {'date': '2026-09-06', 'status': 'closed'},
                {'date': '2026-09-07', 'status': 'closed'},
                {'date': '2026-09-08', 'status': 'open', 'open': {'start': '09:30', 'end': '16:00'}},
            ]
            return Resp({'calendar': {'days': {'day': days}}})
        raise AssertionError(url)


class FakeBroker:
    def __init__(self, orders=None, order_by_id=None, positions=None):
        self.orders = orders or []
        self.order_by_id = order_by_id or {}
        self.positions = positions or []
        self.post_count = 0

    def get(self, url, params=None, timeout=None):
        if url.endswith('/positions'):
            return Resp({'positions': {'position': self.positions}})
        if '/orders/' in url:
            oid = int(url.rsplit('/', 1)[-1])
            return Resp({'order': self.order_by_id[oid]})
        if url.endswith('/orders'):
            return Resp({'orders': {'order': self.orders}})
        raise AssertionError(url)

    def post(self, *args, **kwargs):
        self.post_count += 1
        raise AssertionError('POST should not have been called')


class TCARDailyTests(unittest.TestCase):
    def setUp(self):
        bot.CALENDAR_CACHE.clear()
        for p in (bot.STATE_PATH, bot.AUDIT_PATH):
            try:
                p.unlink()
            except FileNotFoundError:
                pass

    def row(self, **kw):
        base = dict(wr2=-95.0, cci5=-100.0, adx20=20.0, close=50.0, prev_high=51.0)
        base.update(kw)
        return pd.Series(base)

    def test_entry_requires_all_three_frozen_confirmations(self):
        self.assertEqual(bot.signal_for_row(self.row(), False)[0], 'buy')
        self.assertIsNone(bot.signal_for_row(self.row(wr2=-89.9), False)[0])
        self.assertIsNone(bot.signal_for_row(self.row(cci5=-79.9), False)[0])
        self.assertIsNone(bot.signal_for_row(self.row(adx20=14.9), False)[0])

    def test_exit_is_price_or_wr_only_not_cci(self):
        # CCI is deliberately not an exit rule in finalized daily TCAR.
        row = self.row(wr2=-50, cci5=150, close=50, prev_high=51)
        self.assertIsNone(bot.signal_for_row(row, True)[0])
        self.assertEqual(bot.signal_for_row(self.row(wr2=-29.9), True)[0], 'sell')
        self.assertEqual(bot.signal_for_row(self.row(wr2=-50, close=52, prev_high=51), True)[0], 'sell')

    def test_friday_signal_executes_next_open_tuesday(self):
        start, end = bot.execution_window(FakeCalendar(), pd.Timestamp('2026-09-04').date())
        self.assertEqual(start.isoformat(), '2026-09-08T09:30:00-04:00')
        self.assertEqual((end - start).total_seconds(), 90)

    def test_partial_fill_accounting_is_idempotent(self):
        state = bot.default_state()
        active = {'id': 1, 'action': 'buy', 'accounted_exec_quantity': 0}
        bot.account_fill_delta(state, active, {'id': 1, 'status': 'partially_filled', 'exec_quantity': 4,
                                                'remaining_quantity': 6, 'avg_fill_price': 10, 'tag': 'TCAR-X'})
        bot.account_fill_delta(state, active, {'id': 1, 'status': 'partially_filled', 'exec_quantity': 4,
                                                'remaining_quantity': 6, 'avg_fill_price': 10, 'tag': 'TCAR-X'})
        self.assertEqual(state['owned_qty'], 4)
        bot.account_fill_delta(state, active, {'id': 1, 'status': 'filled', 'exec_quantity': 10,
                                                'remaining_quantity': 0, 'avg_fill_price': 10, 'tag': 'TCAR-X'})
        self.assertEqual(state['owned_qty'], 10)

    def test_manual_soxl_position_causes_halt(self):
        broker = FakeBroker(positions=[{'symbol': 'SOXL', 'quantity': 12}])
        cfg = bot.BrokerCfg('https://sandbox.tradier.com/v1', 'x', 'acct', False)
        state = bot.default_state()
        self.assertFalse(bot.verify_position_ownership(broker, cfg, state))
        self.assertIn('position_mismatch', state['halted_reason'])

    def test_restart_recovers_existing_filled_order_without_post(self):
        tag = 'TCAR-SOXL-20260904-B'
        order = {'id': 88, 'tag': tag, 'symbol': 'SOXL', 'side': 'buy', 'quantity': 5,
                 'status': 'filled', 'exec_quantity': 5, 'remaining_quantity': 0, 'avg_fill_price': 20.0}
        broker = FakeBroker(orders=[order], order_by_id={88: order})
        cfg = bot.BrokerCfg('https://sandbox.tradier.com/v1', 'x', 'acct', False)
        state = bot.default_state()
        state['pending'] = {'action': 'buy', 'tag': tag, 'signal_date': '2026-09-04', 'qty': 5}
        bot.recover_pending_existing_order(broker, cfg, state)
        self.assertEqual(broker.post_count, 0)
        self.assertIsNone(state['pending'])
        self.assertIsNone(state['active_order'])  # filled order becomes terminal after reconciliation
        self.assertEqual(state['owned_qty'], 5)


if __name__ == '__main__':
    unittest.main()
