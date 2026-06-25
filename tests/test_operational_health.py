import unittest

from stock_daytrade_system.operational_health import build_operational_health


class OperationalHealthTests(unittest.TestCase):
    def test_intraday_healthy_payload_is_ok(self):
        payload = {
            "market_mode": "intraday",
            "market_mode_label": "盤中",
            "allow_intraday_signal": True,
            "allow_strong_long": True,
            "can_show_any_strong_long": True,
            "price_status_summary": {
                "status": "正常",
                "live_count": 40,
                "delayed_count": 0,
                "cached_count": 0,
                "missing_count": 0,
                "missing_ratio": 0,
            },
            "required_stale_layers": [],
            "stale_layers": [],
            "refresh_guidance": {"severity": "ok"},
            "refresh_operation_summary": {"severity": "ok"},
        }

        health = build_operational_health(payload)

        self.assertEqual(health["status"], "ok")
        self.assertTrue(health["can_use_dashboard"])
        self.assertTrue(health["can_show_strong_long"])
        self.assertEqual(health["watch_readiness"], "可正常看盤")
        self.assertIn("正常", health["summary"])

    def test_stale_required_layer_blocks_dashboard(self):
        payload = {
            "market_mode": "intraday",
            "allow_intraday_signal": True,
            "allow_strong_long": False,
            "price_status_summary": {"status": "正常", "live_count": 20, "missing_ratio": 0},
            "required_stale_layers": ["watchlist"],
            "stale_layers": ["watchlist"],
            "refresh_guidance": {
                "severity": "block",
                "summary": "重點觀察過期",
                "action_label": "更新重點觀察",
                "action_endpoint": "/refresh_watchlist",
            },
            "refresh_operation_summary": {"severity": "block"},
        }

        health = build_operational_health(payload)

        self.assertEqual(health["status"], "blocked")
        self.assertFalse(health["can_use_dashboard"])
        self.assertFalse(health["can_show_strong_long"])
        self.assertEqual(health["watch_readiness"], "暫不適合進場判斷")
        self.assertEqual(health["next_action"]["endpoint"], "/refresh_watchlist")
        self.assertEqual(health["refresh_plan"], ["/refresh_watchlist"])
        self.assertIn("重點觀察", " ".join(health["blockers"]))

    def test_refresh_plan_prioritizes_full_market_before_watchlist(self):
        payload = {
            "market_mode": "pre_open_prepare",
            "allow_intraday_signal": False,
            "price_status_summary": {"status": "部分延遲", "live_count": 0, "missing_ratio": 0},
            "required_stale_layers": ["watchlist", "full_market"],
            "stale_layers": ["full_market", "watchlist"],
            "refresh_guidance": {
                "severity": "block",
                "summary": "資料層過期",
                "action_label": "更新重點觀察",
                "action_endpoint": "/refresh_watchlist",
            },
            "refresh_operation_summary": {"severity": "block", "blocking_layers": ["watchlist"]},
        }

        health = build_operational_health(payload)

        self.assertEqual(health["refresh_plan"], ["/refresh_full_market", "/refresh_watchlist"])

    def test_refresh_plan_includes_post_close_and_manual_refresh_layers(self):
        payload = {
            "market_mode": "closed_review",
            "allow_intraday_signal": False,
            "price_status_summary": {"status": "休市復盤", "live_count": 0, "missing_ratio": 0},
            "required_stale_layers": ["manual_full_refresh", "post_close_validation"],
            "stale_layers": ["manual_full_refresh", "post_close_validation"],
            "refresh_guidance": {
                "severity": "block",
                "summary": "盤後驗證與完整刷新待補",
                "action_label": "更新盤後驗證",
                "action_endpoint": "/refresh_post_close_validation",
            },
            "refresh_operation_summary": {"severity": "block", "blocking_layers": ["manual_full_refresh"]},
        }

        health = build_operational_health(payload)

        self.assertEqual(health["refresh_plan"], ["/refresh_post_close_validation", "/refresh"])

    def test_cached_and_delayed_prices_are_warning_not_strong_long(self):
        payload = {
            "market_mode": "intraday",
            "allow_intraday_signal": True,
            "allow_strong_long": False,
            "can_show_any_strong_long": False,
            "reason_if_blocked": "資料非 live，不可顯示強烈買多。",
            "price_status_summary": {
                "status": "部分延遲",
                "live_count": 8,
                "delayed_count": 2,
                "cached_count": 1,
                "missing_count": 0,
                "missing_ratio": 0,
            },
            "required_stale_layers": [],
            "stale_layers": [],
            "refresh_guidance": {"severity": "ok"},
            "refresh_operation_summary": {"severity": "ok"},
        }

        health = build_operational_health(payload)

        self.assertEqual(health["status"], "warning")
        self.assertTrue(health["can_use_dashboard"])
        self.assertFalse(health["can_show_strong_long"])
        self.assertEqual(health["watch_readiness"], "可看但需保守")
        self.assertIn("上一筆", " ".join(health["warnings"]))
        self.assertIn("延遲", " ".join(health["warnings"]))

    def test_closed_review_is_warning_for_review_not_error(self):
        payload = {
            "market_mode": "closed_review",
            "allow_intraday_signal": False,
            "allow_strong_long": False,
            "price_status_summary": {
                "status": "休市復盤：使用上一交易日資料",
                "live_count": 0,
                "missing_ratio": 0,
            },
            "required_stale_layers": [],
            "stale_layers": [],
            "refresh_guidance": {"severity": "ok"},
            "refresh_operation_summary": {"severity": "ok"},
        }

        health = build_operational_health(payload)

        self.assertEqual(health["status"], "warning")
        self.assertTrue(health["can_use_dashboard"])
        self.assertFalse(health["allow_intraday_signal"])
        self.assertEqual(health["watch_readiness"], "僅供復盤或開盤前觀察")
        self.assertIn("復盤", health["summary"])

    def test_severe_missing_blocks_dashboard(self):
        payload = {
            "market_mode": "intraday",
            "allow_intraday_signal": True,
            "price_status_summary": {
                "status": "嚴重缺漏",
                "live_count": 5,
                "missing_count": 80,
                "missing_ratio": 0.8,
            },
            "required_stale_layers": [],
            "stale_layers": [],
            "refresh_guidance": {"severity": "ok"},
            "refresh_operation_summary": {"severity": "ok"},
        }

        health = build_operational_health(payload)

        self.assertEqual(health["status"], "blocked")
        self.assertFalse(health["can_use_dashboard"])
        self.assertIn("嚴重缺漏", " ".join(health["blockers"]))


if __name__ == "__main__":
    unittest.main()
