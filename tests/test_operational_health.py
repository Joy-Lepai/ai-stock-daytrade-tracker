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
            "front_category_summary": {
                "counts": {"強烈買多": 1, "買多": 2, "觀察": 3, "看空": 4},
                "strong_buy_count": 1,
                "buy_count": 2,
                "watch_count": 3,
                "bearish_count": 4,
                "no_signal_reason": "已有強烈買多候選，仍需逐檔確認進場雷達。",
            },
            "refresh_guidance": {"severity": "ok"},
            "refresh_operation_summary": {"severity": "ok"},
        }

        health = build_operational_health(payload)

        self.assertEqual(health["status"], "ok")
        self.assertTrue(health["can_use_dashboard"])
        self.assertTrue(health["can_show_strong_long"])
        self.assertEqual(health["watch_readiness"], "可正常看盤")
        self.assertIn("正常", health["summary"])
        self.assertEqual(health["operator_mode"], "盤中作戰模式")
        self.assertIn("強烈買多", health["primary_focus"])
        self.assertIn("不要追 high_risk。", health["do_not_do"])
        self.assertIn("是否站上 VWAP？", health["decision_checklist"])
        self.assertEqual(health["opening_preflight"]["light"], "green")
        self.assertEqual(health["opening_preflight"]["label"], "可進入盤中追蹤")
        self.assertTrue(health["opening_preflight"]["can_trust_strong_buy"])
        self.assertTrue(health["opening_preflight"]["should_trade_live"])
        self.assertEqual(health["operator_decision"]["decision"], "可盯盤")
        self.assertTrue(health["operator_decision"]["can_trade_now"])
        self.assertIn("強烈買多", health["operator_decision"]["first_action"])
        self.assertEqual(health["operator_briefing"]["posture"], "盤中作戰")
        self.assertIn("強烈買多", health["operator_briefing"]["headline"])
        self.assertIn("VWAP", health["operator_briefing"]["next_check"])

    def test_intraday_missing_front_category_summary_blocks_trusting_strong_buy(self):
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

        self.assertEqual(health["status"], "warning")
        self.assertFalse(health["can_show_strong_long"])
        self.assertFalse(health["operator_decision"]["can_trust_strong_buy"])
        self.assertIn("四分類摘要", " ".join(health["warnings"]))

    def test_limit_up_context_changes_operator_focus_without_blocking(self):
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
            "front_category_summary": {
                "counts": {"強烈買多": 1, "買多": 2, "觀察": 3, "看空": 4},
                "strong_buy_count": 1,
                "buy_count": 2,
                "watch_count": 3,
                "bearish_count": 4,
                "no_signal_reason": "已有強烈買多候選，仍需逐檔確認進場雷達。",
            },
            "limit_up_operational_summary": {
                "near_limit_up_count": 6,
                "high_risk_count": 4,
                "summary": "4 檔追價風險高；2 檔等待確認。",
                "action": "先看漲停強勢速讀與 /tw/advisor 急拉作戰卡。",
                "risk_gate": "接近漲停不可直接升級買多。",
                "market_phase": "broad_limit_wave_chase_risk",
                "market_phase_label": "漲停潮但追價風險主導",
                "market_phase_summary": "今天有 6 檔接近漲停 / 急拉，且多數被列為追價高風險；盤面很熱，但不代表適合直接追。",
                "operator_priority": "先挑已看到但 high_risk 的股票，等拉回 VWAP 附近不破、停損距離縮小，再重新評估。",
            },
            "required_stale_layers": [],
            "stale_layers": [],
            "refresh_guidance": {"severity": "ok"},
            "refresh_operation_summary": {"severity": "ok"},
        }

        health = build_operational_health(payload)

        self.assertEqual(health["status"], "ok")
        self.assertTrue(health["can_show_strong_long"])
        self.assertEqual(health["limit_up_operational_summary"]["near_limit_up_count"], 6)
        self.assertIn("盤面很熱", health["primary_focus"])
        self.assertIn("漲停潮但追價風險主導", health["operator_briefing"]["headline"])
        self.assertIn("拉回 VWAP", health["operator_briefing"]["next_check"])
        self.assertIn("急拉作戰卡", health["do_now"][0])
        self.assertIn("拉回 VWAP", " ".join(health["do_now"]))
        self.assertIn("接近漲停不可直接升級買多", health["do_not_do"][0])
        self.assertIn("急拉股是否仍站上 VWAP？", health["decision_checklist"])

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
        self.assertEqual(health["operator_steps"][0], "先執行刷新計畫：/refresh_watchlist")
        self.assertEqual(health["operator_mode"], "資料修復模式")
        self.assertEqual(health["opening_preflight"]["light"], "red")
        self.assertEqual(health["opening_preflight"]["label"], "暫停使用即時訊號")
        self.assertFalse(health["opening_preflight"]["can_open_dashboard"])
        self.assertEqual(health["opening_preflight"]["next_action_endpoint"], "/refresh_watchlist")
        self.assertEqual(health["operator_decision"]["decision"], "暫停")
        self.assertFalse(health["operator_decision"]["can_trade_now"])
        self.assertIn("不要", health["operator_decision"]["headline"])
        self.assertIn("/refresh_watchlist", health["do_now"][0])
        self.assertIn("重點觀察", " ".join(health["blockers"]))
        self.assertEqual(health["operator_briefing"]["posture"], "暫停進場判斷")
        self.assertIn("先修資料", health["operator_briefing"]["headline"])
        self.assertIn("重點觀察", health["operator_briefing"]["next_check"])

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

    def test_pre_open_prepare_gives_time_based_operator_steps(self):
        payload = {
            "market_mode": "pre_open_prepare",
            "allow_intraday_signal": False,
            "price_status_summary": {"status": "休市復盤", "live_count": 0, "missing_ratio": 0},
            "required_stale_layers": [],
            "stale_layers": [],
            "refresh_guidance": {"severity": "ok"},
            "refresh_operation_summary": {"severity": "ok"},
        }

        health = build_operational_health(payload)

        steps = " ".join(health["operator_steps"])
        do_now = " ".join(health["do_now"])
        self.assertEqual(health["operator_mode"], "開盤前準備模式")
        self.assertIn("08:55", steps)
        self.assertIn("/dashboard", steps)
        self.assertIn("09:00 後先等 5 到 10 分鐘", steps)
        self.assertIn("資料未轉 live", steps)
        self.assertIn("08:55", do_now)
        self.assertIn("資料轉 live 再看進場雷達", do_now)

    def test_pre_open_review_candidates_are_usable_without_watchlist_live_data(self):
        payload = {
            "market_mode": "pre_open_prepare",
            "market_mode_label": "開盤前準備模式",
            "allow_intraday_signal": False,
            "price_status_summary": {
                "status": "嚴重缺漏",
                "live_count": 0,
                "missing_count": 80,
                "missing_ratio": 100,
            },
            "required_stale_layers": ["watchlist"],
            "stale_layers": ["watchlist", "positions"],
            "refresh_guidance": {
                "severity": "warn",
                "summary": "重點觀察資料已過期，盤中不要依此進場；請先更新重點觀察。",
                "action_label": "更新重點觀察",
                "action_endpoint": "/refresh_watchlist",
            },
            "refresh_operation_summary": {
                "severity": "block",
                "message": "必要資料層需處理：重點觀察。先更新後再判斷。",
            },
            "review_observation_candidates": {
                "status": "ok",
                "count": 2,
                "items": [
                    {"symbol": "3037.TW", "name": "欣興"},
                    {"symbol": "3374.TWO", "name": "精材"},
                ],
            },
        }

        health = build_operational_health(payload)

        self.assertEqual(health["status"], "warning")
        self.assertTrue(health["can_use_dashboard"])
        self.assertFalse(health["can_show_strong_long"])
        self.assertEqual(health["required_stale_layers"], [])
        self.assertIn("盤前觀察", health["data_quality_status"])
        self.assertIn("下個交易日觀察", " ".join(health["warnings"]))
        self.assertIn("watchlist", health["non_blocking_review_layers"])

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
        self.assertEqual(health["opening_preflight"]["light"], "yellow")
        self.assertEqual(health["opening_preflight"]["label"], "復盤 / 開盤前觀察")
        self.assertFalse(health["opening_preflight"]["can_use_intraday_signals"])
        self.assertFalse(health["opening_preflight"]["can_trust_strong_buy"])
        self.assertEqual(health["operator_decision"]["decision"], "復盤")
        self.assertFalse(health["operator_decision"]["can_trade_now"])
        self.assertIn("下個交易日", health["operator_decision"]["first_action"])
        self.assertEqual(health["watch_readiness"], "僅供復盤或開盤前觀察")
        self.assertIn("復盤", " ".join(health["operator_steps"]))
        self.assertIn("復盤", health["summary"])
        self.assertEqual(health["operator_mode"], "復盤準備模式")
        self.assertIn("上一交易日", health["primary_focus"])
        self.assertIn("不要顯示或依賴即時強烈買多。", health["do_not_do"])

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

    def test_intraday_no_strong_buy_adds_front_category_warning(self):
        payload = {
            "market_mode": "intraday",
            "allow_intraday_signal": True,
            "allow_strong_long": True,
            "can_show_any_strong_long": True,
            "price_status_summary": {
                "status": "正常",
                "live_count": 40,
                "missing_ratio": 0,
            },
            "front_category_summary": {
                "strong_buy_count": 0,
                "buy_count": 0,
                "watch_count": 8,
                "bearish_count": 32,
                "bearish_ratio": 80,
                "no_signal_reason": "看空比例偏高，這不是做空建議；先查資料模式、VWAP、價格狀態與四分類原因診斷。",
            },
            "required_stale_layers": [],
            "stale_layers": [],
            "refresh_guidance": {"severity": "ok"},
            "refresh_operation_summary": {"severity": "ok"},
        }

        health = build_operational_health(payload)

        self.assertEqual(health["status"], "warning")
        self.assertEqual(health["front_category_summary"]["bearish_count"], 32)
        self.assertIn("這不是做空建議", " ".join(health["warnings"]))
        self.assertFalse(health["can_show_strong_long"])


if __name__ == "__main__":
    unittest.main()
