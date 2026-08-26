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


if __name__ == "__main__":
    unittest.main()
