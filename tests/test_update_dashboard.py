from __future__ import annotations

import importlib.util
import contextlib
import io
import os
import re
import textwrap
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "update_dashboard.py"
SPEC = importlib.util.spec_from_file_location("update_dashboard", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"Unable to load {MODULE_PATH}")
UPDATE_DASHBOARD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(UPDATE_DASHBOARD)


class DashboardUniverseTests(unittest.TestCase):
    def test_iren_replaces_qtum_without_changing_other_symbols(self) -> None:
        expected = ["SOXL", "LITE", "AAOI", "MRVL", "MU", "AVGO", "IREN", "DRAM", "SMH"]
        self.assertEqual(UPDATE_DASHBOARD.TICKERS, expected)
        self.assertEqual(UPDATE_DASHBOARD.PUT_NAMES, expected[:-1])
        self.assertNotIn("QTUM", UPDATE_DASHBOARD.TICKERS)
        self.assertNotIn("QTUM", UPDATE_DASHBOARD.PUT_NAMES)

    def test_iren_is_not_classified_as_an_etf(self) -> None:
        self.assertEqual(UPDATE_DASHBOARD.ETF_TICKERS, {"SOXL", "DRAM", "SMH"})
        self.assertNotIn("IREN", UPDATE_DASHBOARD.ETF_TICKERS)


class EarningsDateTests(unittest.TestCase):
    def test_soxl_does_not_request_a_corporate_earnings_calendar(self) -> None:
        with patch.object(UPDATE_DASHBOARD.yf, "Ticker") as ticker:
            self.assertIsNone(UPDATE_DASHBOARD.earnings_date("SOXL"))
            ticker.assert_not_called()

    def test_iren_requests_a_corporate_earnings_calendar(self) -> None:
        # Synthetic calendar response: not an actual IREN earnings forecast.
        expected = date(2030, 1, 23)
        with patch.object(UPDATE_DASHBOARD.yf, "Ticker") as ticker:
            ticker.return_value.calendar = {"Earnings Date": [expected]}
            self.assertEqual(UPDATE_DASHBOARD.earnings_date("IREN"), expected)
            ticker.assert_called_once_with("IREN")


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

    def test_corporate_earnings_before_expiration_blocks_a_sale(self) -> None:
        decision, reason = UPDATE_DASHBOARD.decision_from_setup("STRONG BUY", self.good_put, 90, True, False, True)
        self.assertEqual(decision, "WAIT")
        self.assertIn("earnings", reason.lower())

    def test_component_score_contains_all_execution_dimensions(self) -> None:
        components = UPDATE_DASHBOARD.component_scores(self.good_put, True, True, False, False)
        expected = {"iv", "premium", "delta", "dte", "breakeven_support", "liquidity", "support_proximity", "stabilization", "event"}
        self.assertEqual(set(components), expected)
        self.assertGreaterEqual(UPDATE_DASHBOARD.option_setup_score(components), 0)
        self.assertLessEqual(UPDATE_DASHBOARD.option_setup_score(components), 100)


class PagesPublicationTests(unittest.TestCase):
    def publication_check(self):
        workflow = (ROOT / '.github' / 'workflows' / 'refresh.yml').read_text(encoding='utf-8')
        step = re.search(
            r'      - name: Verify GitHub Pages publication\n(.*?)(?=\n      - |\Z)',
            workflow,
            re.S,
        )
        self.assertIsNotNone(step, 'A failed primary and retry deployment must fail the refresh job')
        self.assertIn("if: steps.gate.outputs.run == 'true'", step.group(1))
        self.assertIn('PRIMARY_DEPLOY_OUTCOME: ${{ steps.deploy1.outcome }}', step.group(1))
        self.assertIn('RETRY_DEPLOY_OUTCOME: ${{ steps.deploy2.outcome }}', step.group(1))
        script = re.search(r"          python - <<'PY'\n(.*?)\n          PY", step.group(1), re.S)
        self.assertIsNotNone(script)
        return textwrap.dedent(script.group(1))

    def run_check(self, primary, retry):
        script = self.publication_check()
        with patch.dict(os.environ, {'PRIMARY_DEPLOY_OUTCOME': primary, 'RETRY_DEPLOY_OUTCOME': retry}):
            with contextlib.redirect_stdout(io.StringIO()):
                exec(compile(script, 'refresh.yml:Verify GitHub Pages publication', 'exec'), {})

    def test_both_failed_deployments_fail_the_refresh_job(self):
        with self.assertRaisesRegex(SystemExit, 'GitHub Pages publication failed'):
            self.run_check('failure', 'failure')

    def test_primary_success_accepts_skipped_retry(self):
        self.run_check('success', 'skipped')

    def test_successful_retry_recovers_primary_failure(self):
        self.run_check('failure', 'success')

    def test_missing_successful_deployment_is_not_reported_as_success(self):
        with self.assertRaisesRegex(SystemExit, 'GitHub Pages publication failed'):
            self.run_check('failure', 'skipped')


if __name__ == "__main__":
    unittest.main()
