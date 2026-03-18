import unittest
from unittest.mock import patch

from typer.testing import CliRunner

from ibkr_cli import app as app_module
from ibkr_cli.config import default_config

runner = CliRunner()


def stub_profile():
    config = default_config()
    selected_name = "gateway-paper"
    selected_profile = config.profiles[selected_name]
    return config, True, selected_name, selected_profile


class CliTests(unittest.TestCase):
    def test_quote_watch_json_routes_to_watch_service(self) -> None:
        captured = {}
        rendered = {}

        def fake_watch_quote(profile, **kwargs):
            captured["profile"] = profile
            captured["kwargs"] = kwargs
            return {
                "watch": True,
                "symbol": "AAPL",
                "local_symbol": "AAPL",
                "exchange": "SMART",
                "primary_exchange": "NASDAQ",
                "currency": "USD",
                "sec_type": "STK",
                "con_id": 265598,
                "updates": kwargs["updates"],
                "interval": kwargs["interval"],
                "requested_market_data_type": 1,
                "fallback_applied": False,
                "row_count": 1,
                "rows": [
                    {
                        "update_index": 1,
                        "observed_at": "2026-03-17T15:25:22+00:00",
                        "quote_source": "live",
                        "bid": 254.31,
                        "ask": 254.33,
                        "last": 254.32,
                        "volume": 1000,
                    }
                ],
                "raw_error_codes": [],
                "raw_errors": [],
            }

        with patch.object(
            app_module,
            "resolve_profile_or_exit",
            side_effect=lambda profile, json_output=False: stub_profile(),
        ):
            with patch.object(app_module, "watch_quote", side_effect=fake_watch_quote):
                with patch.object(
                    app_module,
                    "get_quote_snapshot",
                    side_effect=AssertionError("snapshot path should not be used"),
                ):
                    with patch.object(app_module, "print_json", side_effect=lambda payload: rendered.setdefault("payload", payload)):
                        result = runner.invoke(
                            app_module.app,
                            ["quote", "AAPL", "--watch", "--updates", "2", "--interval", "1.5", "--json"],
                        )

        self.assertEqual(result.exit_code, 0)
        payload = rendered["payload"]
        self.assertEqual(payload["profile"], "gateway-paper")
        self.assertTrue(payload["watch"])
        self.assertEqual(
            captured["kwargs"],
            {
                "symbol": "AAPL",
                "exchange": "SMART",
                "currency": "USD",
                "updates": 2,
                "interval": 1.5,
                "timeout": 4.0,
            },
        )

    def test_quote_snapshot_json_routes_to_snapshot_service(self) -> None:
        captured = {}
        rendered = {}

        def fake_snapshot(profile, **kwargs):
            captured["profile"] = profile
            captured["kwargs"] = kwargs
            return {
                "symbol": "AAPL",
                "local_symbol": "AAPL",
                "exchange": "SMART",
                "primary_exchange": "NASDAQ",
                "currency": "USD",
                "sec_type": "STK",
                "con_id": 265598,
                "market_data_type": 3,
                "bid": 254.31,
                "bid_size": 200,
                "ask": 254.33,
                "ask_size": 200,
                "last": 254.32,
                "last_size": 100,
                "close": 254.21,
                "open": 253.04,
                "high": 255.05,
                "low": 252.18,
                "volume": 1000,
                "quote_source": "delayed",
            }

        with patch.object(
            app_module,
            "resolve_profile_or_exit",
            side_effect=lambda profile, json_output=False: stub_profile(),
        ):
            with patch.object(app_module, "get_quote_snapshot", side_effect=fake_snapshot):
                with patch.object(
                    app_module,
                    "watch_quote",
                    side_effect=AssertionError("watch path should not be used"),
                ):
                    with patch.object(app_module, "print_json", side_effect=lambda payload: rendered.setdefault("payload", payload)):
                        result = runner.invoke(app_module.app, ["quote", "AAPL", "--json"])

        self.assertEqual(result.exit_code, 0)
        payload = rendered["payload"]
        self.assertEqual(payload["quote_source"], "delayed")
        self.assertEqual(
            captured["kwargs"],
            {
                "symbol": "AAPL",
                "exchange": "SMART",
                "currency": "USD",
                "timeout": 4.0,
                "debug_market_data": False,
            },
        )

    def test_buy_preview_routes_to_preview_service(self) -> None:
        captured = {}
        rendered = {}

        def fake_preview(profile, **kwargs):
            captured["profile"] = profile
            captured["kwargs"] = kwargs
            return {
                "selected_account": None,
                "symbol": "AAPL",
                "local_symbol": "AAPL",
                "exchange": "SMART",
                "primary_exchange": "NASDAQ",
                "currency": "USD",
                "sec_type": "STK",
                "con_id": 265598,
                "action": "BUY",
                "quantity": 10.0,
                "order_type": "MKT",
                "limit_price": None,
                "tif": "DAY",
                "outside_rth": False,
                "status": "PreSubmitted",
                "init_margin_before": None,
                "init_margin_change": None,
                "init_margin_after": None,
                "maint_margin_before": None,
                "maint_margin_change": None,
                "maint_margin_after": None,
                "equity_with_loan_before": None,
                "equity_with_loan_change": None,
                "equity_with_loan_after": None,
                "commission": None,
                "min_commission": None,
                "max_commission": None,
                "commission_currency": None,
                "warning_text": None,
                "raw_error_codes": [],
            }

        with patch.object(
            app_module,
            "resolve_profile_or_exit",
            side_effect=lambda profile, json_output=False: stub_profile(),
        ):
            with patch.object(app_module, "preview_stock_order", side_effect=fake_preview):
                with patch.object(
                    app_module,
                    "submit_stock_order",
                    side_effect=AssertionError("submit path should not be used"),
                ):
                    with patch.object(app_module, "print_json", side_effect=lambda payload: rendered.setdefault("payload", payload)):
                        result = runner.invoke(app_module.app, ["buy", "AAPL", "10", "--preview", "--json"])

        self.assertEqual(result.exit_code, 0)
        payload = rendered["payload"]
        self.assertEqual(payload["action"], "BUY")
        self.assertEqual(
            captured["kwargs"],
            {
                "action": "BUY",
                "symbol": "AAPL",
                "quantity": 10.0,
                "exchange": "SMART",
                "currency": "USD",
                "order_type": "MKT",
                "limit_price": None,
                "tif": "DAY",
                "outside_rth": False,
                "timeout": 4.0,
                "account": None,
            },
        )

    def test_buy_requires_exactly_one_of_preview_or_submit(self) -> None:
        rendered = {}

        with patch.object(app_module, "print_json", side_effect=lambda payload: rendered.setdefault("payload", payload)):
            result = runner.invoke(app_module.app, ["buy", "AAPL", "10", "--json"])

        self.assertEqual(result.exit_code, app_module.EXIT_CODE_USAGE)
        self.assertFalse(rendered["payload"]["ok"])
        self.assertEqual(rendered["payload"]["error"]["code"], app_module.ERROR_INVALID_ARGUMENTS)
        self.assertEqual(rendered["payload"]["error"]["exit_code"], app_module.EXIT_CODE_USAGE)
        self.assertEqual(
            rendered["payload"]["error"]["details"],
            {"preview": False, "submit": False},
        )

    def test_unknown_profile_returns_structured_json_error(self) -> None:
        rendered = {}

        with patch.object(app_module, "load_config", return_value=(default_config(), True)):
            with patch.object(app_module, "print_json", side_effect=lambda payload: rendered.setdefault("payload", payload)):
                result = runner.invoke(app_module.app, ["quote", "AAPL", "--profile", "missing", "--json"])

        self.assertEqual(result.exit_code, app_module.EXIT_CODE_CONFIG)
        self.assertFalse(rendered["payload"]["ok"])
        self.assertEqual(rendered["payload"]["error"]["code"], app_module.ERROR_UNKNOWN_PROFILE)
        self.assertEqual(rendered["payload"]["error"]["details"]["requested_profile"], "missing")
        self.assertIn("gateway-paper", rendered["payload"]["error"]["details"]["available_profiles"])

    def test_news_providers_json(self) -> None:
        rendered = {}

        def fake_providers(profile, **kwargs):
            return {
                "count": 2,
                "rows": [
                    {"code": "BRFG", "name": "Briefing.com"},
                    {"code": "DJNL", "name": "Dow Jones"},
                ],
            }

        with patch.object(
            app_module,
            "resolve_profile_or_exit",
            side_effect=lambda profile, json_output=False: stub_profile(),
        ):
            with patch.object(app_module, "get_news_providers", side_effect=fake_providers):
                with patch.object(app_module, "print_json", side_effect=lambda payload: rendered.setdefault("payload", payload)):
                    result = runner.invoke(app_module.app, ["news", "providers", "--json"])

        self.assertEqual(result.exit_code, 0)
        payload = rendered["payload"]
        self.assertEqual(payload["profile"], "gateway-paper")
        self.assertEqual(payload["count"], 2)
        self.assertEqual(payload["rows"][0]["code"], "BRFG")

    def test_news_headlines_json(self) -> None:
        rendered = {}

        def fake_headlines(profile, **kwargs):
            return {
                "symbol": "AAPL",
                "local_symbol": "AAPL",
                "exchange": "SMART",
                "primary_exchange": "NASDAQ",
                "currency": "USD",
                "sec_type": "STK",
                "con_id": 265598,
                "provider_codes": "",
                "limit": 10,
                "count": 1,
                "rows": [
                    {
                        "time": "2026-03-17T15:00:00+00:00",
                        "provider_code": "BRFG",
                        "article_id": "BRFG$12345",
                        "headline": "Apple announces new product",
                    }
                ],
            }

        with patch.object(
            app_module,
            "resolve_profile_or_exit",
            side_effect=lambda profile, json_output=False: stub_profile(),
        ):
            with patch.object(app_module, "get_news_headlines", side_effect=fake_headlines):
                with patch.object(app_module, "print_json", side_effect=lambda payload: rendered.setdefault("payload", payload)):
                    result = runner.invoke(app_module.app, ["news", "headlines", "AAPL", "--json"])

        self.assertEqual(result.exit_code, 0)
        payload = rendered["payload"]
        self.assertEqual(payload["symbol"], "AAPL")
        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["rows"][0]["headline"], "Apple announces new product")

    def test_news_article_json(self) -> None:
        rendered = {}

        def fake_article(profile, **kwargs):
            return {
                "provider_code": "BRFG",
                "article_id": "BRFG$12345",
                "article_type": "text",
                "article_text": "Full article content here.",
            }

        with patch.object(
            app_module,
            "resolve_profile_or_exit",
            side_effect=lambda profile, json_output=False: stub_profile(),
        ):
            with patch.object(app_module, "get_news_article", side_effect=fake_article):
                with patch.object(app_module, "print_json", side_effect=lambda payload: rendered.setdefault("payload", payload)):
                    result = runner.invoke(app_module.app, ["news", "article", "BRFG", "BRFG$12345", "--json"])

        self.assertEqual(result.exit_code, 0)
        payload = rendered["payload"]
        self.assertEqual(payload["provider_code"], "BRFG")
        self.assertEqual(payload["article_text"], "Full article content here.")

    def test_quote_service_failure_returns_structured_json_error(self) -> None:
        rendered = {}

        with patch.object(app_module, "resolve_profile_or_exit", side_effect=lambda profile, json_output=False: stub_profile()):
            with patch.object(app_module, "get_quote_snapshot", side_effect=RuntimeError("boom")):
                with patch.object(app_module, "print_json", side_effect=lambda payload: rendered.setdefault("payload", payload)):
                    result = runner.invoke(app_module.app, ["quote", "AAPL", "--json"])

        self.assertEqual(result.exit_code, app_module.EXIT_CODE_API)
        self.assertFalse(rendered["payload"]["ok"])
        self.assertEqual(rendered["payload"]["error"]["code"], app_module.ERROR_MARKET_DATA_REQUEST_FAILED)
        self.assertEqual(rendered["payload"]["error"]["details"]["operation"], "snapshot")
        self.assertEqual(rendered["payload"]["error"]["details"]["symbol"], "AAPL")
