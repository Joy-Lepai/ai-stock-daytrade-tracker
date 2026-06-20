import unittest

from stock_daytrade_system.fugle_market_data import (
    FugleMarketDataClient,
    FugleMarketDataConfig,
    parse_fugle_candles,
    parse_fugle_quote,
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

    def test_parse_quote_extracts_orderbook_and_flow(self):
        signal = parse_fugle_quote(
            "2330.TW",
            {
                "date": "2026-06-18",
                "symbol": "2330",
                "name": "台積電",
                "lastPrice": 1000,
                "previousClose": 990,
                "changePercent": 1.01,
                "lastUpdated": 1781756400000000,
                "bids": [{"price": 999, "size": 120}, {"price": 998, "size": 80}],
                "asks": [{"price": 1000, "size": 50}, {"price": 1001, "size": 40}],
                "total": {
                    "tradeValue": 120000000,
                    "tradeVolume": 1200,
                    "tradeVolumeAtAsk": 800,
                    "tradeVolumeAtBid": 400,
                },
                "lastTrade": {"price": 1000, "bid": 999, "ask": 1000, "size": 300, "time": 1781756400000000},
            },
        )

        self.assertEqual(signal.status, "ok")
        self.assertEqual(signal.price, 1000)
        self.assertEqual(signal.five_level_status, "available")
        self.assertEqual(signal.bid_total_volume, 200)
        self.assertEqual(signal.ask_total_volume, 90)
        self.assertGreater(signal.orderbook_imbalance, 0)
        self.assertEqual(signal.last_trade_side, "buy_sweep")
        self.assertGreater(signal.intraday_flow_ratio, 0)

    def test_parse_candles_extracts_latest_bar(self):
        signal = parse_fugle_candles(
            "2330.TW",
            {
                "date": "2026-06-18",
                "symbol": "2330",
                "timeframe": "1",
                "data": [
                    {"date": "2026-06-18T09:00:00+08:00", "open": 100, "high": 101, "low": 99, "close": 100.5, "volume": 1000, "average": 100.2},
                    {"date": "2026-06-18T09:01:00+08:00", "open": 100.5, "high": 102, "low": 100, "close": 101.5, "volume": 1500, "average": 100.8},
                ],
            },
        )

        self.assertEqual(signal.status, "ok")
        self.assertEqual(signal.candles_count, 2)
        self.assertEqual(signal.latest_close, 101.5)
        self.assertEqual(signal.latest_average, 100.8)


if __name__ == "__main__":
    unittest.main()
