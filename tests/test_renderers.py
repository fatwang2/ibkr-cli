from io import StringIO
import unittest

from rich.console import Console

from ibkr_cli.app import (
    render_bars_table,
    render_news_article_table,
    render_news_headlines_table,
    render_news_providers_table,
    render_option_chains_table,
    render_option_quotes_table,
    render_quote_table,
    render_quote_watch_table,
)


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

    def test_render_news_providers_table(self) -> None:
        rows = [
            {"code": "BRFG", "name": "Briefing.com"},
            {"code": "DJNL", "name": "Dow Jones Newsletters"},
        ]
        text = render_text(render_news_providers_table(rows))
        self.assertIn("News Providers", text)
        self.assertIn("BRFG", text)
        self.assertIn("Dow Jones", text)

    def test_render_news_headlines_table(self) -> None:
        payload = {
            "symbol": "AAPL",
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
        text = render_text(render_news_headlines_table(payload))
        self.assertIn("News: AAPL", text)
        self.assertIn("Apple announces new product", text)
        self.assertIn("BRFG", text)

    def test_render_option_chains_table(self) -> None:
        payload = {
            "symbol": "AAPL",
            "rows": [
                {
                    "exchange": "SMART",
                    "trading_class": "AAPL",
                    "multiplier": "100",
                    "expirations": ["20260320", "20260417", "20260515"],
                    "expiration_count": 3,
                    "strikes": [140.0, 145.0, 150.0],
                    "strike_count": 3,
                }
            ],
        }
        text = render_text(render_option_chains_table(payload))
        self.assertIn("Option Chains: AAPL", text)
        self.assertIn("SMART", text)
        self.assertIn("20260320", text)

    def test_render_option_quotes_table(self) -> None:
        payload = {
            "symbol": "AAPL",
            "expiration": "20260320",
            "count": 1,
            "rows": [
                {
                    "strike": 150.0,
                    "right": "C",
                    "bid": 5.10,
                    "ask": 5.30,
                    "last": 5.20,
                    "volume": 1000.0,
                    "open_interest": 5000.0,
                    "implied_vol": 0.2500,
                    "delta": 0.5500,
                    "gamma": 0.0300,
                    "theta": -0.0500,
                    "vega": 0.1500,
                },
            ],
        }
        text = render_text(render_option_quotes_table(payload))
        self.assertIn("Options: AAPL", text)
        self.assertIn("150.00", text)
        self.assertIn("0.5500", text)

    def test_render_news_article_table(self) -> None:
        payload = {
            "provider_code": "BRFG",
            "article_id": "BRFG$12345",
            "article_type": "text",
            "article_text": "Full article content.",
        }
        text = render_text(render_news_article_table(payload))
        self.assertIn("Article: BRFG$12345", text)
        self.assertIn("BRFG", text)
