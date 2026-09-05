import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ['DCR15_MODE'] = 'paper'
os.environ['DCR15_STATE_PATH'] = str(Path(tempfile.gettempdir()) / 'dcr15-test-state.json')
os.environ['DCR15_AUDIT_PATH'] = str(Path(tempfile.gettempdir()) / 'dcr15-test-audit.csv')
os.environ['DCR15_EXECUTION_GRACE_SECONDS'] = '60'

BOT_PATH = Path(__file__).resolve().parents[1] / 'scripts' / 'dcr15_tradier_bot.py'
spec = importlib.util.spec_from_file_location('dcr15_bot', BOT_PATH)
bot = importlib.util.module_from_spec(spec)
sys.modules['dcr15_bot'] = bot
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
            e = requests.HTTPError(f'status {self.status_code}')
            e.response = self
            raise e


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


class FakeCalendar:
    base = 'https://example.invalid/v1'

    def get(self, url, params=None, timeout=None):
        if url.endswith('/markets/calendar'):
            days = [
                {'date': '2026-09-05', 'status': 'closed'},
                {'date': '2026-09-06', 'status': 'closed'},
                {'date': '2026-09-07', 'status': 'closed'},
                {'date': '2026-09-08', 'status': 'open', 'open': {'start': '09:30', 'end': '16:00'}},
            ]
            return Resp({'calendar': {'days': {'day': days}}})
        raise AssertionError(url)


class SafetyTests(unittest.TestCase):
    def setUp(self):
        for p in (bot.STATE_PATH, bot.AUDIT_PATH):
            try:
                p.unlink()
            except FileNotFoundError:
                pass

    def test_partial_fill_accounting_is_idempotent(self):
        state = bot.default_state()
        active = {'id': 1, 'action': 'buy', 'accounted_exec_quantity': 0}
        bot.account_fill_delta(state, active, {'id': 1, 'status': 'partially_filled', 'exec_quantity': 4, 'remaining_quantity': 6, 'avg_fill_price': 10, 'tag': 'DCR15-X'})
        self.assertEqual(state['owned_qty'], 4)
        bot.account_fill_delta(state, active, {'id': 1, 'status': 'partially_filled', 'exec_quantity': 4, 'remaining_quantity': 6, 'avg_fill_price': 10, 'tag': 'DCR15-X'})
        self.assertEqual(state['owned_qty'], 4)
        bot.account_fill_delta(state, active, {'id': 1, 'status': 'filled', 'exec_quantity': 10, 'remaining_quantity': 0, 'avg_fill_price': 10, 'tag': 'DCR15-X'})
        self.assertEqual(state['owned_qty'], 10)

    def test_sell_fill_reduces_only_strategy_owned_quantity(self):
        state = bot.default_state()
        state['owned_qty'] = 10
        active = {'id': 2, 'action': 'sell', 'accounted_exec_quantity': 0}
        bot.account_fill_delta(state, active, {'id': 2, 'status': 'filled', 'exec_quantity': 6, 'remaining_quantity': 0, 'avg_fill_price': 11, 'tag': 'DCR15-Y'})
        self.assertEqual(state['owned_qty'], 4)

    def test_same_day_execution_window_is_next_bar_only(self):
        ts = bot.pd.Timestamp('2026-09-04T10:00:00-04:00')
        start, end = bot.execution_window(FakeCalendar(), ts)
        self.assertEqual(start.isoformat(), '2026-09-04T10:15:00-04:00')
        self.assertEqual((end - start).total_seconds(), 60)

    def test_1545_signal_moves_to_next_open_session(self):
        ts = bot.pd.Timestamp('2026-09-04T15:45:00-04:00')
        start, end = bot.execution_window(FakeCalendar(), ts)
        self.assertEqual(start.isoformat(), '2026-09-08T09:30:00-04:00')
        self.assertEqual((end - start).total_seconds(), 60)

    def test_existing_exact_tag_is_recovered_not_resubmitted(self):
        tag = 'DCR15-SOXL-202609041000-B'
        order = {'id': 77, 'tag': tag, 'symbol': 'SOXL', 'side': 'buy', 'quantity': 5, 'status': 'open', 'exec_quantity': 0, 'remaining_quantity': 5}
        broker = FakeBroker(orders=[order], order_by_id={77: order})
        cfg = bot.BrokerCfg('https://sandbox.tradier.com/v1', 'x', 'acct', False)
        state = bot.default_state()
        pending = {'action': 'buy', 'tag': tag, 'signal_bar': '2026-09-04T10:00:00-04:00'}
        bot.submit_pending(None, broker, cfg, state, pending)
        self.assertEqual(broker.post_count, 0)
        self.assertIsNotNone(state['active_order'])
        self.assertEqual(state['active_order']['id'], 77)
        self.assertIsNone(state['pending'])

    def test_position_mismatch_halts_instead_of_touching_manual_soxl(self):
        broker = FakeBroker(positions=[{'symbol': 'SOXL', 'quantity': 12}])
        cfg = bot.BrokerCfg('https://sandbox.tradier.com/v1', 'x', 'acct', False)
        state = bot.default_state()
        state['owned_qty'] = 0
        ok = bot.verify_position_ownership(broker, cfg, state)
        self.assertFalse(ok)
        self.assertIn('position_mismatch', state['halted_reason'])


if __name__ == '__main__':
    unittest.main()
