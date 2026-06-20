from datetime import datetime, timedelta
import unittest

from stock_daytrade_system.data import Bar
from stock_daytrade_system.precision_context import build_precision_context


def bar(index: int, close: float, volume: float) -> Bar:
    return Bar(
        timestamp=datetime(2026, 6, 18, 9, 0) + timedelta(minutes=index),
        open=close - 0.2,
        high=close + 0.3,
        low=close - 0.3,
        close=close,
        volume=volume,
    )


class PrecisionContextTests(unittest.TestCase):
    def test_precision_context_marks_missing_tick_and_orderbook(self):
        bars = [bar(index, 100 + index * 0.1, 1000 + index * 50) for index in range(12)]
        context = build_precision_context(
            candidate={
                "vwap": 100,
                "volume_ratio": 1.3,
                "timeframe_diagnostics": {"intraday_window": {"vwap_stay_ok": True, "higher_high": True, "higher_low": True}},
                "institutional_context": {"institutional_data_status": "ok"},
                "sector_context": {"sector_status": "strong"},
            },
            intraday_bars=bars,
            data_health={"is_live": True, "can_use_for_intraday_signal": True},
        )

        payload = context.to_dict()
        self.assertEqual(payload["tick_data_status"], "missing")
        self.assertEqual(payload["orderbook_status"], "missing")
        self.assertFalse(payload["can_use_for_precise_daytrade"])

    def test_precision_context_marks_public_orderbook_partial(self):
        payload = build_precision_context(
            candidate={"vwap": 100, "volume_ratio": 1.1},
            intraday_bars=[],
            data_health={
                "is_live": True,
                "can_use_for_intraday_signal": True,
                "twse_mis_five_level_status": "available",
            },
        ).to_dict()

        self.assertEqual(payload["orderbook_status"], "partial")
        self.assertIn("公開五檔委買委賣", payload["available_data"])
        self.assertFalse(payload["can_use_for_precise_daytrade"])
        self.assertIn("逐筆成交 Tick", payload["missing_data"])
        self.assertNotIn("五檔委買委賣", payload["missing_data"])

    def test_precision_context_uses_fugle_trades_as_tick_data_mvp(self):
        payload = build_precision_context(
            candidate={"vwap": 100, "volume_ratio": 1.1},
            intraday_bars=[bar(index, 100 + index * 0.1, 1000) for index in range(12)],
            data_health={
                "is_live": True,
                "can_use_for_intraday_signal": True,
                "twse_mis_five_level_status": "available",
                "fugle_status": "ok",
                "fugle_trades_count": 30,
            },
        ).to_dict()

        self.assertEqual(payload["tick_data_status"], "ok")
        self.assertIn("Fugle 逐筆成交", payload["available_data"])
        self.assertNotIn("逐筆成交 Tick", payload["missing_data"])
        self.assertTrue(payload["can_use_for_precise_daytrade"])

    def test_review_only_when_data_is_not_live(self):
        context = build_precision_context(
            candidate={"vwap": 100, "volume_ratio": 1.0},
            intraday_bars=[bar(index, 100, 1000) for index in range(12)],
            data_health={"is_live": False, "can_use_for_intraday_signal": False},
        )

        self.assertEqual(context.precision_level, "review_only")
        self.assertIn("復盤", context.precision_label)


if __name__ == "__main__":
    unittest.main()
