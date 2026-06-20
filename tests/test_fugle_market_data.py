import unittest

from stock_daytrade_system.fugle_market_data import (
    FugleMarketDataClient,
    FugleMarketDataConfig,
    parse_fugle_trades,
)


class FugleMarketDataTests(unittest.TestCase):
    def test_disabled_client_returns_safe_payload(self):
        signal = FugleMarketDataClient(FugleMarketDataConfig(enabled=False)).fetch_trades("6919.TW")

        self.assertEqual(signal.status, "disabled")
        self.assertFalse(signal.enabled)
        self.assertFalse(signal.ok)
        self.assertEqual(signal.large_trade_status, "missing")

    def test_enabled_without_key_is_not_configured(self):
        signal = FugleMarketDataClient(FugleMarketDataConfig(enabled=True, api_key="")).fetch_trades("2330")

        self.assertEqual(signal.status, "not_configured")
        self.assertTrue(signal.enabled)
        self.assertFalse(signal.configured)
        self.assertFalse(signal.ok)

    def test_parse_trades_detects_large_buy_sweep(self):
        signal = parse_fugle_trades(
            "2330.TW",
            {
                "date": "2026-06-18",
                "symbol": "2330",
                "data": [
                    {"bid": 567, "ask": 568, "price": 568, "size": 4778, "volume": 54538, "time": 1685338200000000},
                    {"bid": 566, "ask": 567, "price": 566, "size": 10, "volume": 49760, "time": 1685337899721587},
                ],
            },
            threshold=200,
        )

        self.assertEqual(signal.status, "ok")
        self.assertEqual(signal.trades_count, 2)
        self.assertEqual(signal.large_trade_status, "buy_sweep")
        self.assertEqual(signal.large_buy_count, 1)
        self.assertIn("疑似大單敲進", signal.large_trade_summary)

    def test_parse_trades_detects_large_sell_sweep(self):
        signal = parse_fugle_trades(
            "2330.TW",
            {
                "data": [
                    {"bid": 567, "ask": 568, "price": 567, "size": 800, "time": 1685338200000000},
                ],
            },
            threshold=200,
        )

        self.assertEqual(signal.large_trade_status, "sell_sweep")
        self.assertEqual(signal.large_sell_count, 1)
        self.assertIn("疑似大單敲出", signal.large_trade_summary)

    def test_parse_trades_marks_no_large_trade_as_neutral(self):
        signal = parse_fugle_trades(
            "2330.TW",
            {"data": [{"bid": 567, "ask": 568, "price": 568, "size": 10}]},
            threshold=200,
        )

        self.assertEqual(signal.large_trade_status, "neutral")
        self.assertFalse(signal.ok is False)


if __name__ == "__main__":
    unittest.main()
