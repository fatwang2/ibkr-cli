import unittest

from ibkr_cli.ib_service import _wait_for_trade_resolution


class _FakeOrderStatus:
    def __init__(self, status: str) -> None:
        self.status = status


class _FakeTrade:
    def __init__(self, statuses: list[str]) -> None:
        self._statuses = statuses
        self._index = 0
        self.orderStatus = _FakeOrderStatus(statuses[0])

    def advance(self) -> None:
        if self._index + 1 < len(self._statuses):
            self._index += 1
            self.orderStatus.status = self._statuses[self._index]


class _FakeIB:
    def __init__(self, trade: _FakeTrade) -> None:
        self.trade = trade
        self.wait_calls: list[float] = []

    def waitOnUpdate(self, timeout: float) -> None:
        self.wait_calls.append(timeout)
        self.trade.advance()


class IbServiceTests(unittest.TestCase):
    def test_wait_for_trade_resolution_waits_until_done_status(self) -> None:
        trade = _FakeTrade(["Submitted", "Submitted", "Filled"])
        ib = _FakeIB(trade)

        _wait_for_trade_resolution(ib, trade, timeout=10)

        self.assertEqual(trade.orderStatus.status, "Filled")
        self.assertEqual(len(ib.wait_calls), 2)
        self.assertTrue(all(call <= 0.5 for call in ib.wait_calls))

    def test_wait_for_trade_resolution_returns_immediately_when_done(self) -> None:
        trade = _FakeTrade(["Cancelled"])
        ib = _FakeIB(trade)

        _wait_for_trade_resolution(ib, trade, timeout=10)

        self.assertEqual(ib.wait_calls, [])


if __name__ == "__main__":
    unittest.main()
