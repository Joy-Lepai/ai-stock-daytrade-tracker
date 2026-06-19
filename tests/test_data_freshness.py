import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from stock_daytrade_system.data_freshness import evaluate_data_freshness


class DataFreshnessTests(unittest.TestCase):
    def test_live_data_can_be_used_for_intraday_signal(self):
        now = datetime(2026, 6, 18, 9, 31, tzinfo=ZoneInfo("Asia/Taipei"))
        result = evaluate_data_freshness(now=now, latest_at=now - timedelta(seconds=30), is_market_open=True)

        self.assertEqual(result.state, "live")
        self.assertTrue(result.is_live)
        self.assertTrue(result.can_use_for_daytrade)

    def test_delayed_data_is_observation_only(self):
        now = datetime(2026, 6, 18, 9, 35, tzinfo=ZoneInfo("Asia/Taipei"))
        result = evaluate_data_freshness(now=now, latest_at=now - timedelta(minutes=3), is_market_open=True)

        self.assertEqual(result.state, "delayed")
        self.assertTrue(result.is_delayed)

    def test_last_known_data_is_cached_when_source_failed(self):
        now = datetime(2026, 6, 18, 9, 40, tzinfo=ZoneInfo("Asia/Taipei"))
        result = evaluate_data_freshness(
            now=now,
            latest_at=now - timedelta(minutes=8),
            source_failed=True,
            is_market_open=True,
        )

        self.assertEqual(result.state, "last_known")
        self.assertTrue(result.uses_last_known)
        self.assertFalse(result.can_use_for_daytrade)

    def test_missing_data_cannot_be_used(self):
        now = datetime(2026, 6, 18, 9, 40, tzinfo=ZoneInfo("Asia/Taipei"))
        result = evaluate_data_freshness(now=now, latest_at=None, source_failed=True, is_market_open=True)

        self.assertEqual(result.state, "missing")
        self.assertTrue(result.is_stale)
        self.assertFalse(result.can_use_for_daytrade)


if __name__ == "__main__":
    unittest.main()
