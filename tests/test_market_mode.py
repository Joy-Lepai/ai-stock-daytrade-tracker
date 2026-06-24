import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from stock_daytrade_system.market_mode import evaluate_tw_market_mode


class MarketModeTests(unittest.TestCase):
    def test_intraday_requires_today_fresh_data(self):
        mode = evaluate_tw_market_mode(
            now=datetime(2026, 6, 22, 9, 20, tzinfo=ZoneInfo("Asia/Taipei")),
            data_date="2026-06-22",
            latest_data_at="2026-06-22T09:18:00+08:00",
            watchlist_fresh=True,
            positions_fresh=True,
        )

        self.assertEqual(mode.mode, "intraday")
        self.assertFalse(mode.is_holiday)
        self.assertTrue(mode.allow_intraday_signal)
        self.assertTrue(mode.allow_strong_long)

    def test_dragon_boat_holiday_uses_previous_trading_day_review(self):
        mode = evaluate_tw_market_mode(
            now=datetime(2026, 6, 19, 9, 20, tzinfo=ZoneInfo("Asia/Taipei")),
            data_date="2026-06-18",
            latest_data_at="2026-06-18T13:30:00+08:00",
        )

        self.assertEqual(mode.mode, "closed_review")
        self.assertEqual(mode.last_trading_date, "2026-06-18")
        self.assertEqual(mode.data_date, "2026-06-18")
        self.assertFalse(mode.is_trading_day)
        self.assertTrue(mode.is_holiday)
        self.assertTrue(mode.is_data_current_for_mode)
        self.assertFalse(mode.allow_intraday_signal)
        self.assertFalse(mode.allow_strong_long)
        self.assertIn("休市復盤模式", mode.review_mode_message)

    def test_holiday_data_labeled_today_uses_effective_previous_trading_day(self):
        mode = evaluate_tw_market_mode(
            now=datetime(2026, 6, 19, 19, 10, tzinfo=ZoneInfo("Asia/Taipei")),
            data_date="2026-06-19",
            latest_data_at="2026-06-19T19:03:00+08:00",
        )

        self.assertEqual(mode.mode, "closed_review")
        self.assertEqual(mode.last_trading_date, "2026-06-18")
        self.assertEqual(mode.data_date, "2026-06-18")
        self.assertTrue(mode.is_holiday)
        self.assertTrue(mode.is_data_current_for_mode)

    def test_weekend_with_previous_trading_day_is_closed_review_not_error(self):
        mode = evaluate_tw_market_mode(
            now=datetime(2026, 6, 20, 10, 0, tzinfo=ZoneInfo("Asia/Taipei")),
            data_date="2026-06-18",
            latest_data_at="2026-06-18T13:30:00+08:00",
        )

        self.assertEqual(mode.mode, "closed_review")
        self.assertEqual(mode.last_trading_date, "2026-06-18")
        self.assertTrue(mode.is_weekend)
        self.assertFalse(mode.allow_intraday_signal)
        self.assertFalse(mode.allow_strong_long)

    def test_trading_day_before_open_uses_pre_open_prepare(self):
        mode = evaluate_tw_market_mode(
            now=datetime(2026, 6, 22, 8, 57, tzinfo=ZoneInfo("Asia/Taipei")),
            data_date="2026-06-18",
            latest_data_at="2026-06-18T13:30:00+08:00",
        )

        self.assertEqual(mode.mode, "pre_open_prepare")
        self.assertEqual(mode.label, "開盤前準備模式")
        self.assertEqual(mode.last_trading_date, "2026-06-18")
        self.assertEqual(mode.data_date, "2026-06-18")
        self.assertTrue(mode.is_trading_day)
        self.assertFalse(mode.is_holiday)
        self.assertTrue(mode.is_data_current_for_mode)
        self.assertFalse(mode.allow_intraday_signal)
        self.assertFalse(mode.allow_strong_long)
        self.assertIn("尚未有今日 VWAP", mode.review_mode_message)

    def test_trading_day_before_open_today_labeled_data_is_prepare_not_stale(self):
        mode = evaluate_tw_market_mode(
            now=datetime(2026, 6, 25, 0, 51, tzinfo=ZoneInfo("Asia/Taipei")),
            data_date="2026-06-25",
            latest_data_at="2026-06-25T00:48:00+08:00",
        )

        self.assertEqual(mode.mode, "pre_open_prepare")
        self.assertEqual(mode.label, "開盤前準備模式")
        self.assertEqual(mode.last_trading_date, "2026-06-24")
        self.assertEqual(mode.data_date, "2026-06-24")
        self.assertTrue(mode.is_trading_day)
        self.assertTrue(mode.is_data_current_for_mode)
        self.assertFalse(mode.allow_intraday_signal)
        self.assertFalse(mode.allow_strong_long)

    def test_intraday_stale_data_enters_stale_mode(self):
        mode = evaluate_tw_market_mode(
            now=datetime(2026, 6, 22, 9, 30, tzinfo=ZoneInfo("Asia/Taipei")),
            data_date="2026-06-18",
            latest_data_at="2026-06-18T13:30:00+08:00",
        )

        self.assertEqual(mode.mode, "stale_data")
        self.assertTrue(mode.is_trading_day)
        self.assertFalse(mode.allow_strong_long)


if __name__ == "__main__":
    unittest.main()
