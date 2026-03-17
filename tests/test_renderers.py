from io import StringIO
import unittest

from rich.console import Console

from ibkr_cli.app import render_bars_table, render_quote_table, render_quote_watch_table


def render_text(table) -> str:
    console = Console(record=True, width=140, file=StringIO())
    console.print(table)
    return console.export_text()


class RendererTests(unittest.TestCase):
    def test_render_quote_table_shows_core_fields(self) -> None:
        payload = {
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
            "ask_size": 300,
            "last": 254.32,
            "last_size": 100,
            "open": 253.04,
            "high": 255.05,
            "low": 252.18,
            "close": 254.21,
            "volume": 123456,
            "quote_source": "delayed",
            "requested_market_data_type": 1,
            "returned_market_data_type": 3,
            "fallback_applied": True,
            "raw_error_codes": [],
        }

        text = render_text(render_quote_table(payload))

        self.assertIn("Quote: AAPL", text)
        self.assertIn("quote_source", text)
        self.assertIn("delayed", text)
        self.assertIn("fallback_applied", text)

    def test_render_quote_watch_table_shows_updates(self) -> None:
        payload = {
            "symbol": "AAPL",
            "row_count": 2,
            "rows": [
                {
                    "update_index": 1,
                    "observed_at": "2026-03-17T15:25:22+00:00",
                    "quote_source": "delayed",
                    "bid": 254.31,
                    "ask": 254.33,
                    "last": None,
                    "volume": None,
                },
                {
                    "update_index": 2,
                    "observed_at": "2026-03-17T15:25:23+00:00",
                    "quote_source": "delayed",
                    "bid": 254.32,
                    "ask": 254.34,
                    "last": 254.33,
                    "volume": 5000,
                },
            ],
        }

        text = render_text(render_quote_watch_table(payload))

        self.assertIn("Quote Watch: AAPL", text)
        self.assertIn("2026-03-17T15:25:22+00:00", text)
        self.assertIn("254.34", text)

    def test_render_bars_table_shows_rows(self) -> None:
        payload = {
            "symbol": "AAPL",
            "bar_size": "5 mins",
            "duration": "1 D",
            "rows": [
                {
                    "date": "2026-03-17T13:30:00+00:00",
                    "open": 253.04,
                    "high": 253.59,
                    "low": 252.18,
                    "close": 253.56,
                    "volume": 961643,
                    "average": 252.83,
                    "bar_count": 3162,
                }
            ],
        }

        text = render_text(render_bars_table(payload))

        self.assertIn("Bars: AAPL (5 mins, 1 D)", text)
        self.assertIn("2026-03-17T13:30:00+00:00", text)
        self.assertIn("3162", text)
