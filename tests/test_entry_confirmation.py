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
            },
        ).to_dict()

        self.assertEqual(payload["status"], "ready")
        self.assertTrue(payload["can_consider_entry"])
        self.assertEqual(payload["orderbook_status"], "supportive")

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


if __name__ == "__main__":
    unittest.main()
