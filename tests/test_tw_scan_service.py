import unittest
from datetime import datetime, timedelta

from stock_daytrade_system.data import Bar
from stock_daytrade_system.tw_scan_service import _intraday_chart_payload


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


if __name__ == "__main__":
    unittest.main()
