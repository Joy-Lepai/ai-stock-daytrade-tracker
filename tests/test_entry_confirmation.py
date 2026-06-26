import unittest
from datetime import datetime, timedelta

from stock_daytrade_system.data import Bar
from stock_daytrade_system.entry_confirmation import build_entry_confirmation


def bar(index: int, close: float, low=None, volume: float = 1000) -> Bar:
    return Bar(
        timestamp=datetime(2026, 6, 18, 9, 0) + timedelta(minutes=index),
        open=close - 0.2,
        high=close + 0.4,
        low=low if low is not None else close - 0.3,
        close=close,
        volume=volume,
    )


class EntryConfirmationTests(unittest.TestCase):
    def test_executable_with_live_orderbook_can_be_ready(self):
        payload = build_entry_confirmation(
            candidate={
                "entry_status": "executable",
                "trade_bias": "long",
                "last_price": 101,
                "vwap": 100,
                "above_vwap": True,
                "volume_ratio": 1.3,
                "stop_loss": 99,
                "risk_score": 30,
            },
            intraday_bars=[
                bar(0, 99.8, 99.3),
                bar(1, 100.2, 99.6),
                bar(2, 100.6, 100.0),
                bar(3, 101.0, 100.4),
            ],
            data_health={"is_live": True, "can_use_for_intraday_signal": True},
            realtime_quote={
                "five_level_status": "available",
                "bid_total_volume": 1500,
                "ask_total_volume": 600,
                "orderbook_imbalance": 42.86,
                "tick_type": "buy",
                "last_tick_volume": 350,
                "large_trade_threshold": 200,
            },
            orderbook_history=[
                {"price": 100.2, "bid_total_volume": 1100, "ask_total_volume": 900},
                {"price": 100.6, "bid_total_volume": 1300, "ask_total_volume": 700},
                {"price": 101.0, "bid_total_volume": 1500, "ask_total_volume": 600},
            ],
        ).to_dict()

        self.assertEqual(payload["status"], "ready")
        self.assertTrue(payload["can_consider_entry"])
        self.assertEqual(payload["orderbook_status"], "supportive")
        self.assertEqual(payload["bid_volume_trend"], "improving")
        self.assertEqual(payload["ask_volume_trend"], "improving")
        self.assertEqual(payload["price_tick_trend"], "rising")
        self.assertEqual(payload["orderbook_history_count"], 3)
        self.assertEqual(payload["confirmation_quality"], "high_precision")
        self.assertEqual(payload["confirmation_quality_label"], "高品質確認")
        self.assertTrue(payload["critical_data_ready"])

    def test_high_risk_is_blocked_even_with_orderbook_support(self):
        payload = build_entry_confirmation(
            candidate={
                "entry_status": "high_risk",
                "trade_bias": "watch",
                "last_price": 109,
                "vwap": 106.9,
                "above_vwap": True,
                "volume_ratio": 5.4,
                "stop_loss": 95.1,
                "risk_score": 70,
            },
            intraday_bars=[bar(index, 105 + index) for index in range(4)],
            data_health={"is_live": True, "can_use_for_intraday_signal": True},
            realtime_quote={
                "five_level_status": "limit_up_bid_only",
                "bid_total_volume": 10000,
                "ask_total_volume": None,
                "orderbook_imbalance": 100,
                "is_limit_up_locked": True,
            },
        ).to_dict()

        self.assertEqual(payload["status"], "blocked")
        self.assertFalse(payload["can_consider_entry"])
        self.assertIn("目前模型狀態不是可執行進場。", payload["blockers"])
        self.assertIn("漲停鎖住代表追價風險升高，不宜直接追高。", payload["warnings"])

    def test_non_live_data_is_review_only(self):
        payload = build_entry_confirmation(
            candidate={"entry_status": "executable", "last_price": 101, "vwap": 100, "above_vwap": True},
            intraday_bars=[],
            data_health={"is_live": False, "can_use_for_intraday_signal": False, "price_status": "delayed"},
            realtime_quote={},
        ).to_dict()

        self.assertEqual(payload["status"], "review_only")
        self.assertFalse(payload["can_consider_entry"])

    def test_large_buy_trade_can_be_detected_when_tick_payload_exists(self):
        payload = build_entry_confirmation(
            candidate={
                "entry_status": "executable",
                "trade_bias": "long",
                "last_price": 101,
                "vwap": 100,
                "above_vwap": True,
                "volume_ratio": 1.4,
                "stop_loss": 99,
                "risk_score": 30,
            },
            intraday_bars=[bar(index, 100 + index * 0.3) for index in range(4)],
            data_health={"is_live": True, "can_use_for_intraday_signal": True},
            realtime_quote={
                "five_level_status": "available",
                "bid_total_volume": 1500,
                "ask_total_volume": 700,
                "orderbook_imbalance": 36.36,
                "tick_type": "buy",
                "last_tick_volume": 350,
                "large_trade_threshold": 200,
            },
            orderbook_history=[
                {"price": 100.2, "bid_total_volume": 1200, "ask_total_volume": 900},
                {"price": 100.5, "bid_total_volume": 1500, "ask_total_volume": 700},
            ],
        ).to_dict()

        self.assertEqual(payload["large_trade_status"], "buy_sweep")
        self.assertIn("疑似大單敲進", payload["large_trade_summary"])

    def test_missing_orderbook_history_is_explicit_not_error(self):
        payload = build_entry_confirmation(
            candidate={
                "entry_status": "executable",
                "trade_bias": "long",
                "last_price": 101,
                "vwap": 100,
                "above_vwap": True,
                "volume_ratio": 1.1,
                "stop_loss": 99,
                "risk_score": 30,
            },
            intraday_bars=[bar(index, 100 + index * 0.2) for index in range(4)],
            data_health={"is_live": True, "can_use_for_intraday_signal": True},
            realtime_quote={"five_level_status": "missing"},
            orderbook_history=[],
        ).to_dict()

        self.assertEqual(payload["bid_volume_trend"], "missing")
        self.assertEqual(payload["ask_volume_trend"], "missing")
        self.assertEqual(payload["large_trade_status"], "missing")
        self.assertFalse(payload["can_consider_entry"])
        self.assertEqual(payload["confirmation_quality"], "blocked")
        self.assertEqual(payload["confirmation_quality_label"], "暫不進場")

    def test_core_conditions_without_tick_or_orderbook_are_limited_confirmation(self):
        payload = build_entry_confirmation(
            candidate={
                "entry_status": "executable",
                "trade_bias": "long",
                "last_price": 101,
                "vwap": 100,
                "above_vwap": True,
                "volume_ratio": 1.2,
                "stop_loss": 99,
                "risk_score": 30,
            },
            intraday_bars=[bar(index, 100 + index * 0.2) for index in range(4)],
            data_health={"is_live": True, "can_use_for_intraday_signal": True},
            realtime_quote={"five_level_status": "missing"},
            orderbook_history=[],
        ).to_dict()

        self.assertEqual(payload["confirmation_quality"], "blocked")
        self.assertIn(payload["confirmation_quality_label"], {"暫不進場", "確認資料不足"})

    def test_partial_intraday_confirmation_is_standard_quality(self):
        payload = build_entry_confirmation(
            candidate={
                "entry_status": "executable",
                "trade_bias": "long",
                "last_price": 101,
                "vwap": 100,
                "above_vwap": True,
                "volume_ratio": 1.2,
                "stop_loss": 99,
                "risk_score": 30,
            },
            intraday_bars=[bar(index, 100 + index * 0.2) for index in range(4)],
            data_health={"is_live": True, "can_use_for_intraday_signal": True},
            realtime_quote={
                "five_level_status": "available",
                "bid_total_volume": 1500,
                "ask_total_volume": 900,
                "orderbook_imbalance": 25,
                "large_trade_status": "missing",
            },
            orderbook_history=[
                {"price": 100.2, "bid_total_volume": 1400, "ask_total_volume": 920},
                {"price": 100.5, "bid_total_volume": 1500, "ask_total_volume": 900},
            ],
        ).to_dict()

        self.assertEqual(payload["confirmation_quality"], "standard")
        self.assertEqual(payload["confirmation_quality_label"], "標準確認")


if __name__ == "__main__":
    unittest.main()
