import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from stock_daytrade_system.data import Bar
from stock_daytrade_system.tw_scan_service import _intraday_chart_payload, _safety_payload


class TWScanServiceTests(unittest.TestCase):
    def test_intraday_chart_payload_contains_bars_vwap_and_levels(self):
        start = datetime(2026, 6, 18, 9, 0)
        bars = [
            Bar(start, 100, 101, 99, 100, 1000),
            Bar(start + timedelta(minutes=1), 100, 102, 100, 101, 2000),
        ]
        payload = _intraday_chart_payload(
            bars,
            {
                "key_levels": [{"label": "VWAP", "value": 100.8, "note": "盤中均價線"}],
                "action_plan": {"entry_reference": 101, "stop_loss": 99.5, "target_price": 103},
            },
        )

        self.assertEqual(len(payload["bars"]), 2)
        self.assertIsNotNone(payload["bars"][-1]["vwap"])
        labels = {item["label"] for item in payload["levels"]}
        self.assertIn("VWAP", labels)
        self.assertIn("停損價", labels)
        self.assertIn("停利價", labels)
        self.assertLess(payload["price_min"], payload["price_max"])

    def test_safety_payload_blocks_executable_outside_regular_session(self):
        payload = _safety_payload(
            {
                "entry_status": "executable",
                "grade": "A",
                "vwap": 100,
                "volume_ratio": 1.2,
                "stop_loss": 99,
                "last_price": 101,
                "risk_score": 30,
            },
            {"vwap": 100, "volume_ratio": 1.2, "latest_price": 101, "change_pct": 1.5},
            {
                "is_today_data": True,
                "is_stale": False,
                "is_data_missing": False,
            },
            {"action_plan": {"stop_loss": 99}},
            datetime(2026, 6, 18, 15, 0, tzinfo=ZoneInfo("Asia/Taipei")),
        )

        self.assertFalse(payload["is_executable_allowed"])
        self.assertEqual(payload["effective_entry_status"], "data_missing")
        self.assertIn("market_not_regular", payload["reason_codes"])

    def test_practice_long_is_not_executable_allowed(self):
        payload = _safety_payload(
            {
                "entry_status": "practice_long",
                "grade": "B+",
                "vwap": 100,
                "volume_ratio": 0.9,
                "stop_loss": 99,
                "last_price": 101,
                "risk_score": 30,
            },
            {"vwap": 100, "volume_ratio": 0.9, "latest_price": 101, "change_pct": 1.5},
            {
                "is_today_data": True,
                "is_stale": False,
                "is_data_missing": False,
            },
            {"action_plan": {"stop_loss": 99}},
            datetime(2026, 6, 18, 9, 30, tzinfo=ZoneInfo("Asia/Taipei")),
        )

        self.assertFalse(payload["is_executable_allowed"])
        self.assertEqual(payload["effective_entry_status"], "practice_long")


if __name__ == "__main__":
    unittest.main()
