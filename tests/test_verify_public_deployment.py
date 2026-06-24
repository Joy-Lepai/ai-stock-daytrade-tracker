import unittest

from scripts.verify_public_deployment import (
    validate_refresh_status,
    validate_system_version,
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


if __name__ == "__main__":
    unittest.main()
