import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from stock_daytrade_system.market_mode import evaluate_tw_market_mode


class MarketModeTests(unittest.TestCase):
    def test_intraday_requires_today_fresh_data(self):
        mode = evaluate_tw_market_mode(
            now=datetime(2026, 6, 19, 9, 20, tzinfo=ZoneInfo("Asia/Taipei")),
            data_date="2026-06-19",
            latest_data_at="2026-06-19T09:18:00+08:00",
            watchlist_fresh=True,
            positions_fresh=True,
        )

        self.assertEqual(mode.mode, "intraday")
        self.assertTrue(mode.allow_intraday_signal)
        self.assertTrue(mode.allow_strong_long)

    def test_weekend_with_previous_trading_day_is_closed_review_not_error(self):
        mode = evaluate_tw_market_mode(
            now=datetime(2026, 6, 20, 10, 0, tzinfo=ZoneInfo("Asia/Taipei")),
            data_date="2026-06-19",
            latest_data_at="2026-06-19T13:30:00+08:00",
        )

        self.assertEqual(mode.mode, "closed_review")
        self.assertTrue(mode.is_weekend)
        self.assertFalse(mode.allow_intraday_signal)
        self.assertFalse(mode.allow_strong_long)

    def test_intraday_stale_data_enters_stale_mode(self):
        mode = evaluate_tw_market_mode(
            now=datetime(2026, 6, 19, 9, 30, tzinfo=ZoneInfo("Asia/Taipei")),
            data_date="2026-06-19",
            latest_data_at="2026-06-19T09:00:00+08:00",
        )

        self.assertEqual(mode.mode, "stale_data")
        self.assertFalse(mode.allow_strong_long)


if __name__ == "__main__":
    unittest.main()
