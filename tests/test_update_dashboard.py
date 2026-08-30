from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "update_dashboard.py"
SPEC = importlib.util.spec_from_file_location("update_dashboard", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"Unable to load {MODULE_PATH}")
UPDATE_DASHBOARD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(UPDATE_DASHBOARD)


class EarningsDateTests(unittest.TestCase):
    def test_soxl_does_not_request_a_corporate_earnings_calendar(self) -> None:
        with patch.object(UPDATE_DASHBOARD.yf, "Ticker") as ticker:
            self.assertIsNone(UPDATE_DASHBOARD.earnings_date("SOXL"))
            ticker.assert_not_called()


class ShortPutGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.good_put = {"spread_pct": 8.0, "delta": -0.20, "annualized_return_pct": 42.0, "iv_pct": 48.0, "dte": 25, "distance_to_support_pct": -1.0}

    def test_hold_stock_is_hard_blocked_even_with_good_option(self) -> None:
        decision, reason = UPDATE_DASHBOARD.decision_from_setup("HOLD", self.good_put, 90, True, False, False)
        self.assertEqual(decision, "NO TRADE")
        self.assertIn("BUY or STRONG BUY", reason)

    def test_unrated_stock_is_hard_blocked(self) -> None:
        decision, _ = UPDATE_DASHBOARD.decision_from_setup("UNRATED", self.good_put, 90, True, False, False)
        self.assertEqual(decision, "NO TRADE")

    def test_buy_stock_can_still_wait_when_premium_is_weak(self) -> None:
        weak = dict(self.good_put); weak["annualized_return_pct"] = 18.0
        decision, _ = UPDATE_DASHBOARD.decision_from_setup("BUY", weak, 80, True, False, False)
        self.assertEqual(decision, "WAIT")

    def test_strong_buy_can_sell_only_after_option_gates_pass(self) -> None:
        decision, _ = UPDATE_DASHBOARD.decision_from_setup("STRONG BUY", self.good_put, 82, True, False, False)
        self.assertEqual(decision, "SELL")

    def test_component_score_contains_all_execution_dimensions(self) -> None:
        components = UPDATE_DASHBOARD.component_scores(self.good_put, True, True, False, False)
        expected = {"iv", "premium", "delta", "dte", "breakeven_support", "liquidity", "support_proximity", "stabilization", "event"}
        self.assertEqual(set(components), expected)
        self.assertGreaterEqual(UPDATE_DASHBOARD.option_setup_score(components), 0)
        self.assertLessEqual(UPDATE_DASHBOARD.option_setup_score(components), 100)


if __name__ == "__main__":
    unittest.main()
