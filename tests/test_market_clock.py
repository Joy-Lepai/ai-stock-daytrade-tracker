from datetime import datetime
import unittest
from zoneinfo import ZoneInfo

from stock_daytrade_system.market_clock import taiwan_market_session, us_market_session


class MarketClockTests(unittest.TestCase):
    def test_us_market_sessions(self):
        ny = ZoneInfo("America/New_York")

        self.assertEqual(us_market_session(datetime(2026, 1, 5, 8, 0, tzinfo=ny)).session, "premarket")
        self.assertEqual(us_market_session(datetime(2026, 1, 5, 10, 0, tzinfo=ny)).session, "regular")
        self.assertEqual(us_market_session(datetime(2026, 1, 5, 17, 0, tzinfo=ny)).session, "afterhours")
        self.assertEqual(us_market_session(datetime(2026, 1, 3, 10, 0, tzinfo=ny)).session, "closed")

    def test_taiwan_refresh_interval(self):
        tw = ZoneInfo("Asia/Taipei")

        self.assertEqual(taiwan_market_session(datetime(2026, 1, 5, 10, 0, tzinfo=tw)).refresh_interval_seconds, 30)
        self.assertEqual(taiwan_market_session(datetime(2026, 1, 5, 15, 0, tzinfo=tw)).refresh_interval_seconds, 300)


if __name__ == "__main__":
    unittest.main()
