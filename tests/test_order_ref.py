import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from ibkr_cli import app as app_module
from ibkr_cli import ib_service
from ibkr_cli.config import default_config

runner = CliRunner()


def stub_profile():
    config = default_config()
    selected_name = "gateway-paper"
    selected_profile = config.profiles[selected_name]
    return config, True, selected_name, selected_profile


class OrderRefServiceTests(unittest.TestCase):
    def test_normalize_order_ref_strips(self) -> None:
        self.assertEqual(ib_service.normalize_order_ref("  fr-AMPH-1  "), "fr-AMPH-1")
        self.assertIsNone(ib_service.normalize_order_ref(None))

    def test_normalize_order_ref_rejects_blank(self) -> None:
        with self.assertRaises(ValueError):
            ib_service.normalize_order_ref("   ")

    def test_filter_rows_by_order_ref_prefix(self) -> None:
        rows = [
            {"order_ref": "fr-AMPH-1"},
            {"order_ref": "rm-AAPL-1"},
            {"order_ref": None},
        ]
        filtered = ib_service._filter_rows_by_order_ref_prefix(rows, "fr-")
        self.assertEqual(filtered, [{"order_ref": "fr-AMPH-1"}])

    def test_filter_rows_by_order_ref_prefix_rejects_blank(self) -> None:
        with self.assertRaises(ValueError):
            ib_service._filter_rows_by_order_ref_prefix([{"order_ref": "fr-1"}], "   ")

    def test_build_clean_modify_order_preserves_order_ref(self) -> None:
        source = SimpleNamespace(
            orderId=10,
            clientId=1,
            permId=100,
            action="SELL",
            totalQuantity=1,
            orderType="LMT",
            lmtPrice=10.0,
            auxPrice=0.0,
            tif="DAY",
            outsideRth=True,
            account="DU123",
            orderRef="fr-AMPH-test",
            parentId=0,
            ocaGroup="",
            ocaType=0,
            transmit=True,
            trailStopPrice=0.0,
            trailingPercent=0.0,
            goodAfterTime="",
            goodTillDate="",
        )
        order = ib_service._build_clean_modify_order(source)
        self.assertEqual(order.orderRef, "fr-AMPH-test")

    def test_prepare_stock_order_sets_order_ref(self) -> None:
        contract = SimpleNamespace(
            symbol="AMPH",
            localSymbol="AMPH",
            exchange="SMART",
            primaryExchange="NASDAQ",
            currency="USD",
            secType="STK",
            conId=1,
        )
        ib = MagicMock()
        ib.managedAccounts.return_value = ["DU123"]
        ib.qualifyContracts.return_value = [contract]

        managed, account, qualified, order = ib_service._prepare_stock_order(
            ib,
            action="SELL",
            symbol="AMPH",
            quantity=1,
            exchange="SMART",
            currency="USD",
            order_type="LMT",
            limit_price=10.0,
            tif="DAY",
            outside_rth=True,
            account=None,
            order_ref=" fr-AMPH-test ",
        )

        self.assertEqual(managed, ["DU123"])
        self.assertEqual(account, "DU123")
        self.assertIs(qualified, contract)
        self.assertEqual(order.orderRef, "fr-AMPH-test")

    def test_prepare_bracket_order_sets_parent_and_child_refs(self) -> None:
        contract = SimpleNamespace(
            symbol="AAPL",
            localSymbol="AAPL",
            exchange="SMART",
            primaryExchange="NASDAQ",
            currency="USD",
            secType="STK",
            conId=2,
        )
        ib = MagicMock()
        ib.managedAccounts.return_value = ["DU123"]
        ib.qualifyContracts.return_value = [contract]

        _managed, _account, _contract, orders = ib_service._prepare_bracket_order(
            ib,
            action="BUY",
            symbol="AAPL",
            quantity=1,
            exchange="SMART",
            currency="USD",
            order_type="MKT",
            limit_price=None,
            tif="DAY",
            outside_rth=False,
            account=None,
            take_profit_price=200.0,
            stop_loss_price=100.0,
            order_ref="fr-AAPL-1",
        )

        parent, take_profit, stop_loss = orders
        self.assertEqual(parent.orderRef, "fr-AAPL-1")
        self.assertEqual(take_profit.orderRef, "fr-AAPL-1-tp")
        self.assertEqual(stop_loss.orderRef, "fr-AAPL-1-sl")


class OrderRefCliTests(unittest.TestCase):
    def test_buy_help_shows_order_ref(self) -> None:
        result = runner.invoke(app_module.app, ["buy", "--help"])
        self.assertEqual(result.exit_code, 0)
        self.assertIn("--order-ref", result.stdout)

    def test_sell_help_shows_order_ref(self) -> None:
        result = runner.invoke(app_module.app, ["sell", "--help"])
        self.assertEqual(result.exit_code, 0)
        self.assertIn("--order-ref", result.stdout)

    def test_orders_open_help_shows_order_ref_prefix(self) -> None:
        result = runner.invoke(app_module.app, ["orders", "open", "--help"])
        self.assertEqual(result.exit_code, 0)
        self.assertIn("--order-ref-prefix", result.stdout)

    def test_sell_preview_passes_order_ref(self) -> None:
        captured = {}
        rendered = {}

        def fake_preview(profile, **kwargs):
            captured["kwargs"] = kwargs
            return {
                "preview_only": True,
                "selected_account": "DU123",
                "symbol": "AMPH",
                "local_symbol": "AMPH",
                "exchange": "SMART",
                "primary_exchange": "NASDAQ",
                "currency": "USD",
                "sec_type": "STK",
                "con_id": 1,
                "action": "SELL",
                "quantity": 1.0,
                "order_type": "LMT",
                "limit_price": 10.0,
                "stop_price": None,
                "aux_price": None,
                "trailing_percent": None,
                "tif": "DAY",
                "outside_rth": True,
                "order_ref": kwargs.get("order_ref"),
                "status": "PreSubmitted",
                "init_margin_before": 0.0,
                "init_margin_change": 1.0,
                "init_margin_after": 1.0,
                "maint_margin_before": 0.0,
                "maint_margin_change": 1.0,
                "maint_margin_after": 1.0,
                "equity_with_loan_before": 100.0,
                "equity_with_loan_change": 0.0,
                "equity_with_loan_after": 100.0,
                "commission": None,
                "min_commission": None,
                "max_commission": None,
                "commission_currency": None,
                "warning_text": None,
                "raw_error_codes": [],
                "raw_errors": [],
            }

        with patch.object(
            app_module,
            "resolve_profile_or_exit",
            side_effect=lambda profile, json_output=False: stub_profile(),
        ):
            with patch.object(app_module, "preview_stock_order", side_effect=fake_preview):
                with patch.object(
                    app_module,
                    "print_json",
                    side_effect=lambda payload: rendered.setdefault("payload", payload),
                ):
                    result = runner.invoke(
                        app_module.app,
                        [
                            "sell",
                            "AMPH",
                            "1",
                            "--type",
                            "LMT",
                            "--limit",
                            "10",
                            "--outside-rth",
                            "--order-ref",
                            "fr-AMPH-test",
                            "--preview",
                            "--json",
                        ],
                    )

        self.assertEqual(result.exit_code, 0, result.stdout + result.stderr)
        self.assertEqual(captured["kwargs"]["order_ref"], "fr-AMPH-test")
        self.assertEqual(rendered["payload"]["order_ref"], "fr-AMPH-test")
