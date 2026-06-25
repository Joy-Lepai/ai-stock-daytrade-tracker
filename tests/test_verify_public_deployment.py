import unittest

from scripts.verify_public_deployment import (
    validate_dashboard_html,
    validate_refresh_status,
    validate_system_version,
    validate_tw_advisor_direct_html,
    validate_tw_advisor_scan,
    validate_tw_advisor_html,
)


class VerifyPublicDeploymentTests(unittest.TestCase):
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
        }

        checks = validate_refresh_status(payload)

        failed = [item.name for item in checks if not item.ok]
        self.assertIn("stale market mode blocks operation summary", failed)

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
            "decision_card": {"top_reason": "追價風險高"},
            "entry_radar_summary": {"blocker_summary": "追價風險高", "next_trigger": "等待拉回 VWAP"},
            "data_health": {"price_status": "live"},
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
        self.assertIn("advisor scan has entry radar summary", failed)
        self.assertIn("advisor scan has no legacy misleading category", failed)

    def test_tw_advisor_scan_blocks_non_intraday_strong_buy(self):
        payload = {
            "symbol": "2330.TW",
            "front_trade": {"category": "強烈買多"},
            "decision_card": {"top_reason": "多方條件完整"},
            "entry_radar_summary": {"blocker_summary": "非盤中", "next_trigger": "等開盤"},
            "data_health": {"price_status": "delayed"},
            "market_mode": {"mode": "pre_open_prepare"},
        }

        checks = validate_tw_advisor_scan(payload, "2330")

        failed = [item.name for item in checks if not item.ok]
        self.assertIn("non-intraday advisor scan blocks strong buy", failed)


if __name__ == "__main__":
    unittest.main()
