import unittest

from scripts.verify_public_deployment import (
    parse_advisor_symbols,
    validate_dashboard_html,
    validate_health_payload,
    validate_liveness_payload,
    validate_readiness_payload,
    validate_refresh_status,
    validate_system_version,
    validate_tw_advisor_direct_html,
    validate_tw_advisor_scan,
    validate_tw_advisor_html,
)


class VerifyPublicDeploymentTests(unittest.TestCase):
    def test_parse_advisor_symbols_accepts_repeated_and_comma_separated_values(self):
        symbols = parse_advisor_symbols(["6919,2886", "8150.TW", "", " 2330 "])

        self.assertEqual(symbols, ["6919", "2886", "8150.TW", "2330"])

    def test_system_version_checks_expected_commit(self):
        payload = {
            "api_status": "ok",
            "runtime": {"commit": "abc123def456"},
            "tracker_html": {"commit": "abc123def456"},
            "consistency": {"runtime_matches_tracker": True, "is_ready": True, "warnings": []},
        }

        checks = validate_system_version(payload, expected_commit="abc123def4567890")

        self.assertTrue(all(item.ok for item in checks), checks)

    def test_system_version_detects_old_runtime_commit(self):
        payload = {
            "api_status": "ok",
            "runtime": {"commit": "old111"},
            "tracker_html": {"commit": "old111"},
            "consistency": {"runtime_matches_tracker": True, "is_ready": True, "warnings": []},
        }

        checks = validate_system_version(payload, expected_commit="new222")

        failed = [item.name for item in checks if not item.ok]
        self.assertIn("runtime matches expected commit", failed)

    def test_pre_open_refresh_status_allows_optional_stale_layers(self):
        payload = {
            "api_status": "ok",
            "market_mode": "pre_open_prepare",
            "required_refresh_layers": ["full_market", "watchlist"],
            "required_stale_layers": [],
            "stale_layers": ["positions"],
            "allow_strong_long": False,
            "price_status_summary": {"status": "部分延遲"},
            "refresh_operation_summary": {"severity": "ok", "message": "必要資料層正常。"},
            "operational_health": {
                "status": "warning",
                "summary": "開盤前準備模式",
                "next_action": {"label": "不需手動更新"},
            },
        }

        checks = validate_refresh_status(payload)

        self.assertTrue(all(item.ok for item in checks), checks)

    def test_non_intraday_must_block_strong_buy(self):
        payload = {
            "api_status": "ok",
            "market_mode": "closed_review",
            "required_refresh_layers": ["full_market"],
            "required_stale_layers": [],
            "allow_strong_long": True,
            "price_status_summary": {"status": "正常"},
            "refresh_operation_summary": {"severity": "ok", "message": "休市復盤模式。"},
            "operational_health": {
                "status": "warning",
                "summary": "休市復盤模式",
                "next_action": {"label": "查看復盤"},
            },
        }

        checks = validate_refresh_status(payload)

        failed = [item.name for item in checks if not item.ok]
        self.assertIn("non-intraday blocks strong buy", failed)

    def test_required_stale_layers_must_block_refresh_operation_summary(self):
        payload = {
            "api_status": "ok",
            "market_mode": "intraday",
            "required_refresh_layers": ["watchlist", "positions"],
            "required_stale_layers": ["watchlist"],
            "allow_strong_long": False,
            "price_status_summary": {"status": "正常"},
            "refresh_operation_summary": {"severity": "warn", "message": "重點觀察需更新。"},
            "operational_health": {
                "status": "blocked",
                "summary": "重點觀察過期",
                "next_action": {"label": "更新重點觀察"},
            },
        }

        checks = validate_refresh_status(payload)

        failed = [item.name for item in checks if not item.ok]
        self.assertIn("required layers fresh", failed)
        self.assertIn("required stale layers block operation summary", failed)

    def test_stale_market_mode_must_block_refresh_operation_summary(self):
        payload = {
            "api_status": "ok",
            "market_mode": "stale_data",
            "required_refresh_layers": ["full_market", "watchlist"],
            "required_stale_layers": [],
            "allow_strong_long": False,
            "price_status_summary": {"status": "嚴重缺漏"},
            "refresh_operation_summary": {"severity": "ok", "message": "必要資料層正常。"},
            "operational_health": {
                "status": "blocked",
                "summary": "資料異常",
                "next_action": {"label": "先修正資料"},
            },
        }

        checks = validate_refresh_status(payload)

        failed = [item.name for item in checks if not item.ok]
        self.assertIn("stale market mode blocks operation summary", failed)

    def test_health_payload_requires_core_monitoring_fields(self):
        payload = {
            "api_status": "ok",
            "status": "warning",
            "summary": "非盤中模式",
            "next_action": {"label": "查看復盤"},
            "market_mode": "closed_review",
            "price_status_summary": {"status": "休市復盤"},
            "deployment": {"runtime_commit": "abc123", "tracker_commit": "abc123"},
            "can_show_strong_long": False,
        }

        checks = validate_health_payload(payload)

        self.assertTrue(all(item.ok for item in checks), checks)

    def test_blocked_health_cannot_show_strong_buy(self):
        payload = {
            "api_status": "ok",
            "status": "blocked",
            "summary": "資料異常",
            "next_action": {"label": "先修資料"},
            "market_mode": "intraday",
            "price_status_summary": {"status": "嚴重缺漏"},
            "deployment": {"runtime_commit": "abc123"},
            "can_show_strong_long": True,
        }

        checks = validate_health_payload(payload)

        failed = [item.name for item in checks if not item.ok]
        self.assertIn("blocked health blocks strong buy", failed)

    def test_liveness_payload_requires_alive_status(self):
        payload = {"api_status": "ok", "status": "alive", "service": "tw-daytrade-tracker"}

        checks = validate_liveness_payload(200, payload)

        self.assertTrue(all(item.ok for item in checks), checks)

    def test_readiness_payload_accepts_503_when_blocked(self):
        payload = {
            "api_status": "ok",
            "status": "blocked",
            "summary": "資料異常",
            "next_action": {"label": "先修資料"},
            "market_mode": "intraday",
            "price_status_summary": {"status": "嚴重缺漏"},
            "deployment": {"runtime_commit": "abc123"},
            "can_show_strong_long": False,
        }

        checks = validate_readiness_payload(503, payload)

        self.assertTrue(all(item.ok for item in checks), checks)

    def test_readiness_payload_rejects_200_when_blocked(self):
        payload = {
            "api_status": "ok",
            "status": "blocked",
            "summary": "資料異常",
            "next_action": {"label": "先修資料"},
            "market_mode": "intraday",
            "price_status_summary": {"status": "嚴重缺漏"},
            "deployment": {"runtime_commit": "abc123"},
            "can_show_strong_long": False,
        }

        checks = validate_readiness_payload(200, payload)

        failed = [item.name for item in checks if not item.ok]
        self.assertIn("readyz HTTP status matches health status", failed)

    def test_refresh_status_requires_operational_health(self):
        payload = {
            "api_status": "ok",
            "market_mode": "intraday",
            "required_refresh_layers": ["watchlist", "positions"],
            "required_stale_layers": [],
            "allow_strong_long": False,
            "price_status_summary": {"status": "正常"},
            "refresh_operation_summary": {"severity": "ok", "message": "必要資料層正常。"},
        }

        checks = validate_refresh_status(payload)

        failed = [item.name for item in checks if not item.ok]
        self.assertIn("operational health present", failed)
        self.assertIn("operational health status valid", failed)
        self.assertIn("operational health has next action", failed)

    def test_blocked_operational_health_must_block_strong_buy(self):
        payload = {
            "api_status": "ok",
            "market_mode": "intraday",
            "required_refresh_layers": ["watchlist", "positions"],
            "required_stale_layers": [],
            "allow_strong_long": True,
            "price_status_summary": {"status": "正常"},
            "refresh_operation_summary": {"severity": "ok", "message": "必要資料層正常。"},
            "operational_health": {
                "status": "blocked",
                "summary": "資料異常",
                "next_action": {"label": "先修資料"},
            },
        }

        checks = validate_refresh_status(payload)

        failed = [item.name for item in checks if not item.ok]
        self.assertIn("blocked operational health blocks strong buy", failed)

    def test_dashboard_html_requires_core_sections_and_blocks_legacy_words(self):
        required_html = """
        今日決策摘要 今日資料可信度 最接近強烈買多 買多觀察池
        進場雷達成績單 資料健康度 台股全市場異動掃描池
        漏抓股票診斷 強烈買多漏斗 系統狀態與資料來源
        """

        checks = validate_dashboard_html(required_html + " 做多確認")

        failed = [item.name for item in checks if not item.ok]
        self.assertIn("dashboard has no legacy misleading wording", failed)

        checks = validate_dashboard_html(required_html)
        self.assertTrue(all(item.ok for item in checks), checks)

    def test_dashboard_html_detects_missing_core_sections(self):
        checks = validate_dashboard_html("今日決策摘要")

        failed = [item.name for item in checks if not item.ok]
        self.assertIn("dashboard has core decision sections", failed)

    def test_dashboard_html_detects_candidate_explainer_zero_count_contradiction(self):
        html = """
        今日決策摘要 今日資料可信度 最接近強烈買多 買多觀察池
        進場雷達成績單 資料健康度 台股全市場異動掃描池
        漏抓股票診斷 強烈買多漏斗 系統狀態與資料來源
        TWSE 上市掃描：成功，普通股池 692 檔；TPEX 上櫃掃描：成功，普通股池 389 檔；今日異動候選 40 檔。
        <details><summary>候選股怎麼選出來？</summary>
        <div class="metric"><span>完整普通股池</span><strong>0</strong></div>
        <div class="metric"><span>今日異動候選</span><strong>40</strong></div>
        <div class="metric"><span>送入模型評分</span><strong>0</strong></div>
        </details>
        """

        checks = validate_dashboard_html(html)

        failed = [item.name for item in checks if not item.ok]
        self.assertIn("dashboard candidate explainer counts are consistent", failed)

    def test_dashboard_html_accepts_consistent_candidate_explainer_counts(self):
        html = """
        今日決策摘要 今日資料可信度 最接近強烈買多 買多觀察池
        進場雷達成績單 資料健康度 台股全市場異動掃描池
        漏抓股票診斷 強烈買多漏斗 系統狀態與資料來源
        TWSE 上市掃描：成功，普通股池 692 檔；TPEX 上櫃掃描：成功，普通股池 389 檔；今日異動候選 40 檔。
        <details><summary>候選股怎麼選出來？</summary>
        <div class="metric"><span>完整普通股池</span><strong>1081</strong></div>
        <div class="metric"><span>今日異動候選</span><strong>40</strong></div>
        <div class="metric"><span>送入模型評分</span><strong>118</strong></div>
        </details>
        """

        checks = validate_dashboard_html(html)

        self.assertTrue(all(item.ok for item in checks), checks)

    def test_tw_advisor_html_requires_entry_copy_and_blocks_legacy_words(self):
        required_html = """
        個股當沖作戰卡 輸入股票代號後 本系統不是報明牌
        強烈買多、買多、觀察、看空或資料不足 台積電 兆豐金
        本系統僅供資料整理
        """

        checks = validate_tw_advisor_html(required_html + " 買多推薦")

        failed = [item.name for item in checks if not item.ok]
        self.assertIn("advisor has no legacy misleading wording", failed)

        checks = validate_tw_advisor_html(required_html)
        self.assertTrue(all(item.ok for item in checks), checks)

    def test_tw_advisor_html_detects_missing_entry_copy(self):
        checks = validate_tw_advisor_html("個股當沖作戰卡")

        failed = [item.name for item in checks if not item.ok]
        self.assertIn("advisor has combat-card entry copy", failed)

    def test_tw_advisor_direct_html_requires_query_bootstrap_and_scan_api(self):
        required_html = """
        個股當沖作戰卡 輸入股票代號後 本系統不是報明牌
        強烈買多、買多、觀察、看空或資料不足 台積電 兆豐金
        本系統僅供資料整理
        new URLSearchParams(window.location.search).get("symbol")
        /api/tw/scan/symbol
        """

        checks = validate_tw_advisor_direct_html(required_html, "6919")
        self.assertTrue(all(item.ok for item in checks), checks)

        checks = validate_tw_advisor_direct_html(required_html.replace("/api/tw/scan/symbol", ""), "6919")
        failed = [item.name for item in checks if not item.ok]
        self.assertIn("advisor direct page can call scan API", failed)

    def test_tw_advisor_scan_requires_core_payload(self):
        payload = {
            "symbol": "6919.TW",
            "front_trade": {"category": "觀察"},
            "decision_card": {
                "top_reason": "追價風險高",
                "next_trigger": "等待拉回 VWAP",
                "invalid_condition": "追價風險未降溫",
            },
            "entry_radar_summary": {"blocker_summary": "追價風險高", "next_trigger": "等待拉回 VWAP"},
            "data_health": {"price_status": "live"},
            "market_mode": {"mode": "intraday"},
        }

        checks = validate_tw_advisor_scan(payload, "6919")

        self.assertTrue(all(item.ok for item in checks), checks)

    def test_tw_advisor_scan_observation_does_not_require_stop_loss(self):
        payload = {
            "symbol": "6919.TW",
            "front_trade": {"category": "觀察"},
            "decision_card": {
                "top_reason": "追價風險高",
                "next_trigger": "等待拉回 VWAP",
                "invalid_condition": "追價風險未降溫",
            },
            "entry_radar_summary": {"blocker_summary": "追價風險高", "next_trigger": "等待拉回 VWAP"},
            "candidate": {"entry_status": "high_risk", "above_vwap": True},
            "data_health": {"price_status": "live", "can_use_for_intraday_signal": True},
            "market_mode": {"mode": "intraday"},
        }

        checks = validate_tw_advisor_scan(payload, "6919")

        self.assertTrue(all(item.ok for item in checks), checks)

    def test_tw_advisor_scan_detects_missing_radar_and_legacy_category(self):
        payload = {
            "symbol": "6919.TW",
            "front_trade": {"category": "買多推薦"},
            "decision_card": {},
            "entry_radar_summary": {},
            "data_health": {},
            "market_mode": {},
        }

        checks = validate_tw_advisor_scan(payload, "6919")

        failed = [item.name for item in checks if not item.ok]
        self.assertIn("advisor scan has front category", failed)
        self.assertIn("advisor scan has market mode", failed)
        self.assertIn("advisor scan has decision card", failed)
        self.assertIn("advisor scan has next trigger", failed)
        self.assertIn("advisor scan has invalid condition", failed)
        self.assertIn("advisor scan has entry radar summary", failed)
        self.assertIn("advisor scan has no legacy misleading category", failed)

    def test_tw_advisor_scan_blocks_non_intraday_strong_buy(self):
        payload = {
            "symbol": "2330.TW",
            "front_trade": {"category": "強烈買多"},
            "decision_card": {
                "top_reason": "多方條件完整",
                "next_trigger": "等開盤",
                "invalid_condition": "非盤中",
            },
            "entry_radar_summary": {"blocker_summary": "非盤中", "next_trigger": "等開盤"},
            "data_health": {"price_status": "delayed"},
            "market_mode": {"mode": "pre_open_prepare"},
        }

        checks = validate_tw_advisor_scan(payload, "2330")

        failed = [item.name for item in checks if not item.ok]
        self.assertIn("non-intraday advisor scan blocks strong buy", failed)
        self.assertIn("advisor buy labels require intraday live data", failed)

    def test_tw_advisor_scan_blocks_buy_label_when_not_above_vwap(self):
        payload = {
            "symbol": "2886.TW",
            "front_trade": {"category": "買多"},
            "decision_card": {
                "top_reason": "突破但未站上 VWAP",
                "next_trigger": "站回 VWAP",
                "invalid_condition": "無法站回 VWAP",
            },
            "entry_radar_summary": {"blocker_summary": "尚未站上 VWAP", "next_trigger": "站回 VWAP"},
            "candidate": {"entry_status": "wait_vwap", "above_vwap": False},
            "data_health": {"price_status": "live", "can_use_for_intraday_signal": True},
            "market_mode": {"mode": "intraday"},
        }

        checks = validate_tw_advisor_scan(payload, "2886")

        failed = [item.name for item in checks if not item.ok]
        self.assertIn("advisor buy labels require above VWAP when known", failed)
        self.assertIn("advisor buy labels block high risk and wait-vwap entries", failed)

    def test_tw_advisor_scan_blocks_buy_label_for_high_risk(self):
        payload = {
            "symbol": "8150.TW",
            "front_trade": {"category": "強烈買多"},
            "decision_card": {
                "top_reason": "強勢但追價風險高",
                "next_trigger": "等待拉回",
                "invalid_condition": "追價風險未降溫",
            },
            "entry_radar_summary": {"blocker_summary": "追價風險高", "next_trigger": "等待拉回"},
            "candidate": {"entry_status": "high_risk", "above_vwap": True},
            "data_health": {"price_status": "live", "can_use_for_intraday_signal": True},
            "market_mode": {"mode": "intraday"},
        }

        checks = validate_tw_advisor_scan(payload, "8150")

        failed = [item.name for item in checks if not item.ok]
        self.assertIn("advisor buy labels block high risk and wait-vwap entries", failed)

    def test_tw_advisor_scan_allows_buy_label_when_safety_conditions_are_met(self):
        payload = {
            "symbol": "2330.TW",
            "front_trade": {"category": "買多"},
            "decision_card": {
                "top_reason": "方向偏多，等待突破",
                "next_trigger": "突破昨高",
                "invalid_condition": "跌破 VWAP",
            },
            "entry_radar_summary": {"blocker_summary": "等待突破", "next_trigger": "突破昨高"},
            "candidate": {"entry_status": "wait_breakout", "above_vwap": True, "stop_loss": 101.5},
            "data_health": {"price_status": "live", "can_use_for_intraday_signal": True},
            "market_mode": {"mode": "intraday"},
        }

        checks = validate_tw_advisor_scan(payload, "2330")

        self.assertTrue(all(item.ok for item in checks), checks)

    def test_tw_advisor_scan_blocks_buy_label_without_stop_loss(self):
        payload = {
            "symbol": "2330.TW",
            "front_trade": {"category": "買多"},
            "decision_card": {
                "top_reason": "方向偏多，等待突破",
                "next_trigger": "突破昨高",
                "invalid_condition": "跌破 VWAP",
            },
            "entry_radar_summary": {"blocker_summary": "等待突破", "next_trigger": "突破昨高"},
            "candidate": {"entry_status": "wait_breakout", "above_vwap": True},
            "data_health": {"price_status": "live", "can_use_for_intraday_signal": True},
            "market_mode": {"mode": "intraday"},
        }

        checks = validate_tw_advisor_scan(payload, "2330")

        failed = [item.name for item in checks if not item.ok]
        self.assertIn("advisor buy labels require stop loss", failed)

    def test_tw_advisor_scan_accepts_buy_label_stop_loss_from_key_metrics(self):
        payload = {
            "symbol": "2330.TW",
            "front_trade": {"category": "強烈買多"},
            "decision_card": {
                "top_reason": "多方條件完整",
                "next_trigger": "等待進場雷達確認",
                "invalid_condition": "跌破 VWAP",
            },
            "entry_radar_summary": {"blocker_summary": "接近觸發", "next_trigger": "等待進場雷達確認"},
            "candidate": {"entry_status": "executable", "above_vwap": True},
            "key_metrics": {"stop_loss": 598.0},
            "data_health": {"price_status": "live", "can_use_for_intraday_signal": True},
            "market_mode": {"mode": "intraday"},
        }

        checks = validate_tw_advisor_scan(payload, "2330")

        self.assertTrue(all(item.ok for item in checks), checks)


if __name__ == "__main__":
    unittest.main()
