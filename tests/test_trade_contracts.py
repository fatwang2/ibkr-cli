import unittest
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import Mock, patch

from ibkr_cli import ib_service
from ibkr_cli.config import ProfileConfig


class TradeContractDetectionTests(unittest.TestCase):
    def test_detects_forex_pair_by_shape(self) -> None:
        self.assertTrue(ib_service._is_forex_pair_symbol("USDJPY"))
        self.assertTrue(ib_service._is_forex_pair_symbol("ABCDEF"))
        self.assertEqual(ib_service._detect_trade_contract_kind("USDJPY"), "CASH")

    def test_detects_future_symbol_by_shape(self) -> None:
        self.assertEqual(ib_service._detect_trade_contract_kind("ESZ6"), "FUT")
        self.assertEqual(ib_service._detect_trade_contract_kind("MNQH27"), "STK")

    def test_rejects_two_digit_future_year_suffix(self) -> None:
        self.assertIsNone(ib_service._future_symbol_match("ESM26"))
        self.assertEqual(ib_service._detect_trade_contract_kind("ESM26"), "STK")

    def test_build_trade_contract_applies_exchange_override_to_futures(self) -> None:
        kind, contract = ib_service._build_trade_contract("COILM6", "IPE", "USD")
        self.assertEqual(kind, "FUT")
        self.assertEqual(contract.exchange, "IPE")
        self.assertEqual(contract.localSymbol, "COILM6")

    def test_build_trade_contract_does_not_force_smart_for_futures(self) -> None:
        kind, contract = ib_service._build_trade_contract("COILM6", "SMART", "USD")
        self.assertEqual(kind, "FUT")
        self.assertEqual(contract.exchange, "")

    def test_normalize_contract_for_order_strips_trailing_expiry_timezone(self) -> None:
        contract = SimpleNamespace(lastTradeDateOrContractMonth="20260430 19:30:00 GB")
        normalized = ib_service._normalize_contract_for_order(contract)
        self.assertIs(normalized, contract)
        self.assertEqual(contract.lastTradeDateOrContractMonth, "20260430 19:30:00")

    def test_stock_is_default_fallback(self) -> None:
        self.assertEqual(ib_service._detect_trade_contract_kind("AAPL"), "STK")

    def test_forex_qualification_failure_is_not_silently_treated_as_stock(self) -> None:
        ib = Mock()
        ib.qualifyContracts.return_value = []
        with self.assertRaisesRegex(RuntimeError, "looks like a forex pair"):
            ib_service._qualify_trade_contract(ib, "ABCDEF", "SMART", "USD")

    def test_future_qualification_failure_is_not_silently_treated_as_stock(self) -> None:
        ib = Mock()
        ib.qualifyContracts.return_value = []
        with self.assertRaisesRegex(RuntimeError, "looks like a futures code"):
            ib_service._qualify_trade_contract(ib, "ABCZ6", "SMART", "USD")

    def test_future_qualification_failure_with_explicit_exchange_mentions_exchange_hint(self) -> None:
        ib = Mock()
        ib.qualifyContracts.return_value = []
        with self.assertRaisesRegex(RuntimeError, "could not qualify futures symbol 'MESM6' on exchange 'IPE'"):
            ib_service._qualify_trade_contract(ib, "MESM6", "IPE", "USD")

    def test_successful_qualification_returns_detected_kind(self) -> None:
        ib = Mock()
        qualified_contract = Mock()
        ib.qualifyContracts.return_value = [qualified_contract]
        resolved_contract, kind = ib_service._qualify_trade_contract(ib, "ESZ6", "SMART", "USD")
        self.assertIs(resolved_contract, qualified_contract)
        self.assertEqual(kind, "FUT")

    def test_ambiguous_future_qualification_raises_clear_error(self) -> None:
        ib = Mock()
        ib.qualifyContracts.return_value = [None]
        ib.qualifyContractsAsync.return_value = object()
        ib._run.return_value = [[
            SimpleNamespace(
                localSymbol="COILM6",
                exchange="IPE",
                lastTradeDateOrContractMonth="20260430 19:30:00 GB",
            ),
            SimpleNamespace(
                localSymbol="COILM6",
                exchange="QBALGOIEU",
                lastTradeDateOrContractMonth="20260430 19:30:00 GB",
            ),
        ]]

        with self.assertRaises(RuntimeError) as exc_info:
            ib_service._qualify_trade_contract(ib, "COILM6", "SMART", "USD")
        message = str(exc_info.exception)
        self.assertIn("Ambiguous contract for symbol 'COILM6'", message)
        self.assertIn("--exchange IPE", message)
        self.assertIn("--exchange QBALGOIEU", message)

    def test_preview_order_converts_invalid_whatif_result_into_runtime_error(self) -> None:
        ib = Mock()
        ib.whatIfOrder.return_value = []
        contract = SimpleNamespace(
            conId=123,
            symbol="COIL",
            localSymbol="COILM6",
            exchange="IPE",
            primaryExchange="IPE",
            currency="USD",
            secType="FUT",
        )
        order = SimpleNamespace(
            action="BUY",
            orderType="MKT",
            auxPrice=None,
            tif="DAY",
            outsideRth=False,
        )
        raw_errors = [{"message": "Preview failed"}]

        @contextmanager
        def fake_ib_session(_profile, timeout=4.0, readonly=False):
            yield ib

        @contextmanager
        def fake_capture_ib_errors(_ib, matcher=None):
            yield raw_errors

        @contextmanager
        def fake_suppress_logs():
            yield

        with (
            patch.object(ib_service, "ib_session", fake_ib_session),
            patch.object(ib_service, "_prepare_order", return_value=(["DU123"], "DU123", contract, order)),
            patch.object(ib_service, "_capture_ib_errors", fake_capture_ib_errors),
            patch.object(ib_service, "_suppress_ib_async_logs", fake_suppress_logs),
        ):
            with self.assertRaisesRegex(RuntimeError, "IBKR could not preview this order. Preview failed"):
                ib_service.preview_order(ProfileConfig(), "BUY", "COILM6", 1, exchange="IPE")


if __name__ == "__main__":
    unittest.main()
