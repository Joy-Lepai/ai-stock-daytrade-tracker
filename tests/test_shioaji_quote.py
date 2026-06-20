import unittest

from stock_daytrade_system.shioaji_quote import (
    ShioajiQuoteClient,
    ShioajiQuoteConfig,
    parse_shioaji_snapshot,
)


class ShioajiQuoteTests(unittest.TestCase):
    def test_disabled_client_returns_safe_payload(self):
        quote = ShioajiQuoteClient(ShioajiQuoteConfig(enabled=False)).fetch_snapshot("6919.TW")

        self.assertEqual(quote.status, "disabled")
        self.assertFalse(quote.enabled)
        self.assertFalse(quote.ok)
        self.assertEqual(quote.five_level_status, "not_streaming")

    def test_enabled_without_credentials_is_not_configured(self):
        quote = ShioajiQuoteClient(ShioajiQuoteConfig(enabled=True)).fetch_snapshot("2330")

        self.assertEqual(quote.status, "not_configured")
        self.assertTrue(quote.enabled)
        self.assertFalse(quote.configured)
        self.assertFalse(quote.ok)

    def test_parse_snapshot_extracts_quote_and_top_of_book(self):
        quote = parse_shioaji_snapshot(
            "2330.TW",
            {
                "close": 100,
                "reference_price": 98,
                "total_volume": 1200,
                "average_price": 99.5,
                "bid_price": 99.9,
                "bid_volume": 80,
                "ask_price": 100.1,
                "ask_volume": 20,
                "tick_type": "buy",
                "last_tick_volume": 350,
                "large_trade_threshold": 200,
                "ts": "2026-06-18T09:30:00+08:00",
            },
        )

        self.assertEqual(quote.status, "ok")
        self.assertEqual(quote.price, 100)
        self.assertEqual(quote.change_pct, 2.04)
        self.assertEqual(quote.bidask_status, "top_of_book")
        self.assertEqual(quote.five_level_status, "not_streaming")
        self.assertEqual(quote.orderbook_imbalance, 60)
        self.assertEqual(quote.large_trade_status, "buy_sweep")
        self.assertIn("疑似大單敲進", quote.large_trade_summary)


if __name__ == "__main__":
    unittest.main()
