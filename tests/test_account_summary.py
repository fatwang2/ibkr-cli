import unittest
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch

from ibkr_cli.config import ProfileConfig
from ibkr_cli.ib_service import get_account_summary


class AccountSummaryTests(unittest.TestCase):
    def test_get_account_summary_filters_all_rows_in_multi_account_mode(self) -> None:
        fake_ib = SimpleNamespace(
            managedAccounts=lambda: ["U123456789", "U987654321"],
            accountSummary=lambda account=None: [
                SimpleNamespace(account="U123456789", tag="NetLiquidation", value="100", currency="USD"),
                SimpleNamespace(account="U987654321", tag="NetLiquidation", value="200", currency="USD"),
                SimpleNamespace(account="All", tag="UnrealizedPnL", value="300", currency="USD"),
            ],
        )

        @contextmanager
        def fake_ib_session(profile, timeout=4.0):
            yield fake_ib

        with patch("ibkr_cli.ib_service.ib_session", fake_ib_session):
            payload = get_account_summary(ProfileConfig(), account=None)

        self.assertEqual([row["account"] for row in payload["rows"]], ["U123456789", "U987654321"])
        self.assertNotIn("All", {row["account"] for row in payload["rows"]})


if __name__ == "__main__":
    unittest.main()
