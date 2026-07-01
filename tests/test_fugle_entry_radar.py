import unittest
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from stock_daytrade_system.db import connect
from stock_daytrade_system.fugle_entry_radar import enrich_fugle_priority_pool
from stock_daytrade_system.fugle_market_data import FugleMarketDataConfig


class Payload:
    def __init__(self, data):
        self.data = data

    def to_dict(self):
        return dict(self.data)


class FakeFugleClient:
    def __init__(self, fail_symbols=None):
        self.config = FugleMarketDataConfig(enabled=True, api_key="test", candles_timeframe="1")
        self.fail_symbols = set(fail_symbols or [])
        self.quote_calls = 0
        self.trade_calls = 0
        self.candle_calls = 0

    def fetch_quote(self, symbol):
        self.quote_calls += 1
        if symbol in self.fail_symbols:
            raise RuntimeError("quote failed")
        return Payload(
            {
                "symbol": symbol,
                "status": "ok",
                "status_label": "已接入 Fugle Quote",
                "price": 101.0,
                "quote_time": "2026-06-18T09:35:00+08:00",
                "bid_total_volume": 1600,
                "ask_total_volume": 800,
                "bid_price": 100.5,
                "bid_volume": 500,
                "ask_price": 101.0,
                "ask_volume": 200,
                "orderbook_imbalance": 33.33,
                "five_level_status": "available",
                "five_level_status_label": "已接入五檔",
                "last_trade_size": 300,
                "last_trade_side": "buy_sweep",
            }
        )

    def fetch_trades(self, symbol):
        self.trade_calls += 1
        return Payload(
            {
                "symbol": symbol,
                "status": "ok",
                "status_label": "已接入逐筆成交",
                "trades_count": 2,
                "large_trade_status": "buy_sweep",
                "large_trade_summary": "疑似大單敲進：300 股。",
                "large_trade_threshold": 200,
                "large_trade_size": 300,
            }
        )

    def fetch_candles(self, symbol, timeframe="1"):
        self.candle_calls += 1
        return Payload(
            {
                "symbol": symbol,
                "status": "ok",
                "status_label": "已接入 Fugle 1分K",
                "candles_count": 4,
                "candles": [
                    {"timestamp": "2026-06-18T09:31:00+08:00", "open": 99, "high": 100, "low": 98.5, "close": 99.5, "volume": 1000},
                    {"timestamp": "2026-06-18T09:32:00+08:00", "open": 99.5, "high": 100.5, "low": 99.0, "close": 100.0, "volume": 1200},
                    {"timestamp": "2026-06-18T09:33:00+08:00", "open": 100.0, "high": 101.0, "low": 99.5, "close": 100.5, "volume": 1300},
                    {"timestamp": "2026-06-18T09:34:00+08:00", "open": 100.5, "high": 101.5, "low": 100.0, "close": 101.0, "volume": 1500},
                ],
            }
        )


def pool_for(symbols):
    return {
        "selected": [
            {
                "symbol": symbol,
                "name": "測試股",
                "grade": "A",
                "entry_status": "executable",
                "trade_bias": "long",
                "last_price": 101.0,
                "vwap": 100.0,
                "volume_ratio": 1.2,
                "trigger_price": 101.0,
                "stop_loss": 99.0,
                "risk_score": 30,
                "bullish_score": 85,
                "confidence_score": 80,
                "can_use_for_entry_confirmation": True,
            }
            for symbol in symbols
        ]
    }


class FugleEntryRadarTests(unittest.TestCase):
    def test_enriches_priority_pool_with_orderbook_trade_and_price_radar(self):
        with TemporaryDirectory() as directory:
            with connect(Path(directory) / "daytrade.db") as conn:
                payload = enrich_fugle_priority_pool(
                    pool_for(["2330.TW"]),
                    client=FakeFugleClient(),
                    conn=conn,
                    captured_at=datetime(2026, 6, 18, 9, 35),
                )

        item = payload["selected"][0]
        self.assertEqual(payload["confirmation_success_count"], 1)
        self.assertIn("Fugle 雷達本次", payload["api_budget_message"])
        self.assertIn("狀態：安全", payload["api_budget_message"])
        self.assertEqual(item["orderbook_status"], "supportive")
        self.assertEqual(item["large_trade_status"], "buy_sweep")
        self.assertIn("大單敲進", item["large_trade_summary"])
        self.assertIn(item["price_tick_trend"], {"rising", "stable"})
        self.assertIn("entry_confirmation_summary", item)
        self.assertEqual(item["confirmation_quality"], "standard")
        self.assertEqual(item["confirmation_quality_label"], "標準確認")
        self.assertTrue(item["critical_data_ready"])
        self.assertEqual(payload["entry_radar_health"]["operator_status"], "ready")
        self.assertTrue(payload["entry_radar_health"]["can_use_for_entry_confirmation"])
        self.assertIn("逐檔確認五檔", payload["entry_radar_health"]["next_action"])

    def test_symbol_failure_does_not_break_the_pool(self):
        with TemporaryDirectory() as directory:
            with connect(Path(directory) / "daytrade.db") as conn:
                payload = enrich_fugle_priority_pool(
                    pool_for(["2330.TW", "6919.TW"]),
                    client=FakeFugleClient(fail_symbols={"6919.TW"}),
                    conn=conn,
                    captured_at=datetime(2026, 6, 18, 9, 35),
                )

        rows = {item["symbol"]: item for item in payload["selected"]}
        self.assertEqual(payload["confirmation_success_count"], 1)
        self.assertEqual(payload["confirmation_failed_count"], 1)
        self.assertEqual(rows["2330.TW"]["fugle_confirmation_status"], "ok")
        self.assertEqual(rows["6919.TW"]["fugle_confirmation_status"], "failed")
        self.assertIn("暫時無法更新", rows["6919.TW"]["entry_confirmation_summary"])
        self.assertNotIn("confirmation_quality", rows["6919.TW"])
        self.assertEqual(payload["entry_radar_health"]["operator_status"], "degraded")
        self.assertFalse(payload["entry_radar_health"]["can_use_for_entry_confirmation"])
        self.assertTrue(payload["entry_radar_health"]["can_use_partial_confirmation"])
        self.assertIn("失敗股票等下一次刷新", payload["entry_radar_health"]["next_action"])

    def test_disabled_fugle_marks_pool_without_calling_symbol_endpoints(self):
        client = FakeFugleClient()
        client.config = FugleMarketDataConfig(enabled=False, api_key="", candles_timeframe="1")
        with TemporaryDirectory() as directory:
            with connect(Path(directory) / "daytrade.db") as conn:
                payload = enrich_fugle_priority_pool(
                    pool_for(["2330.TW"]),
                    client=client,
                    conn=conn,
                    captured_at=datetime(2026, 6, 18, 9, 35),
                )

        item = payload["selected"][0]
        self.assertEqual(payload["entry_radar_status"], "disabled")
        self.assertEqual(payload["actual_api_calls"], 0)
        self.assertEqual(client.quote_calls + client.trade_calls + client.candle_calls, 0)
        self.assertEqual(item["entry_confirmation_status_label"], "等待 Fugle 設定")
        self.assertFalse(item["entry_confirmation_can_consider"])
        self.assertEqual(item["confirmation_quality_label"], "暫不進場")
        self.assertEqual(payload["entry_radar_health"]["operator_status"], "not_ready")
        self.assertFalse(payload["entry_radar_health"]["can_use_for_entry_confirmation"])
        self.assertIn("先完成 Fugle 設定", payload["entry_radar_health"]["next_action"])

    def test_basic_user_limit_only_fetches_first_five_symbols(self):
        symbols = ["2330.TW", "2317.TW", "2454.TW", "2886.TW", "6919.TW", "8150.TW"]
        client = FakeFugleClient()
        with TemporaryDirectory() as directory:
            with connect(Path(directory) / "daytrade.db") as conn:
                with patch.dict("os.environ", {"FUGLE_PRIORITY_POOL_LIMIT": "5"}):
                    payload = enrich_fugle_priority_pool(
                        pool_for(symbols),
                        client=client,
                        conn=conn,
                        captured_at=datetime(2026, 6, 18, 9, 35),
                    )

        rows = {item["symbol"]: item for item in payload["selected"]}
        self.assertEqual(payload["tracking_limit"], 5)
        self.assertEqual(payload["confirmation_success_count"], 5)
        self.assertEqual(payload["confirmation_skipped_count"], 1)
        self.assertEqual(payload["planned_api_calls"], 15)
        self.assertEqual(payload["actual_api_calls"], 15)
        self.assertIn("15 次 API 呼叫", payload["api_budget_message"])
        self.assertEqual(client.quote_calls, 5)
        self.assertEqual(client.trade_calls, 5)
        self.assertEqual(client.candle_calls, 5)
        self.assertEqual(rows["8150.TW"]["fugle_confirmation_status"], "disabled")
        self.assertIn("超過 Fugle 進場雷達 5 檔追蹤上限", rows["8150.TW"]["entry_confirmation_summary"])
        self.assertEqual(payload["entry_radar_health"]["operator_status"], "limited")
        self.assertFalse(payload["entry_radar_health"]["can_use_for_entry_confirmation"])
        self.assertTrue(payload["entry_radar_health"]["can_use_partial_confirmation"])
        self.assertEqual(payload["entry_radar_health"]["tracking_limit"], 5)
        self.assertIn("只盯前 5 檔", payload["entry_radar_health"]["next_action"])


if __name__ == "__main__":
    unittest.main()
