import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from stock_daytrade_system.data import Bar
from stock_daytrade_system.tw_scan_service import (
    _data_health_payload,
    _intraday_chart_payload,
    _radar_quote_payload,
    _safety_payload,
)


class TWScanServiceTests(unittest.TestCase):
    def test_cached_last_known_price_blocks_strong_long(self):
        captured_at = datetime(2026, 6, 18, 9, 40, tzinfo=ZoneInfo("Asia/Taipei"))
        data_health = _data_health_payload(
            captured_at,
            {
                "current_price": 100,
                "quote_time": "2026-06-18T09:35:00+08:00",
                "fallback_source": "last_known_price",
                "fallback_reason": "api_failed",
            },
            {"2330.TW": "daily failed"},
            {},
            {},
            "2330.TW",
        )
        payload = _safety_payload(
            {
                "entry_status": "executable",
                "grade": "A",
                "vwap": 99,
                "volume_ratio": 1.2,
                "stop_loss": 98,
                "last_price": 100,
                "risk_score": 20,
            },
            {"vwap": 99, "volume_ratio": 1.2, "latest_price": 100, "change_pct": 1.5},
            data_health,
            {"action_plan": {"stop_loss": 98}},
            captured_at,
        )

        self.assertEqual(data_health["price_status"], "cached")
        self.assertFalse(data_health["can_show_strong_long"])
        self.assertFalse(payload["is_executable_allowed"])
        self.assertIn("refresh_layer_stale", payload["reason_codes"])

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

    def test_radar_quote_prefers_fugle_large_trade_signal(self):
        payload = _radar_quote_payload(
            {"price": 100, "five_level_status": "available"},
            {"large_trade_status": "missing"},
            {
                "source": "Fugle REST Trades",
                "large_trade_status": "buy_sweep",
                "large_trade_summary": "疑似大單敲進：500 股。",
                "large_trade_size": 500,
                "large_trade_threshold": 200,
            },
        )

        self.assertEqual(payload["large_trade_status"], "buy_sweep")
        self.assertEqual(payload["last_tick_volume"], 500)
        self.assertEqual(payload["large_trade_source"], "Fugle REST Trades")

    def test_data_health_reports_fugle_mvp_status(self):
        captured_at = datetime(2026, 6, 18, 9, 40, tzinfo=ZoneInfo("Asia/Taipei"))
        payload = _data_health_payload(
            captured_at,
            {
                "current_price": 100,
                "quote_time": "2026-06-18T09:39:00+08:00",
            },
            {},
            {},
            {},
            "2330.TW",
            realtime_quote={"five_level_status": "available"},
            fugle_trades={
                "enabled": True,
                "configured": True,
                "status": "ok",
                "status_label": "已接入逐筆成交",
                "trades_count": 30,
                "large_trade_status": "buy_sweep",
                "large_trade_summary": "疑似大單敲進。",
            },
        )

        self.assertEqual(payload["fugle_status"], "ok")
        self.assertEqual(payload["fugle_trades_count"], 30)
        self.assertEqual(payload["fugle_large_trade_status"], "buy_sweep")
        self.assertNotIn("shioaji_status", payload)


if __name__ == "__main__":
    unittest.main()
