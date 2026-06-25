import contextlib
import io
import unittest

import scripts.check_operational_health as script


class CheckOperationalHealthScriptTests(unittest.TestCase):
    def test_render_report_returns_zero_for_ok(self):
        exit_code, report = script.render_report(
            {
                "status": "ok",
                "summary": "系統狀態正常",
                "market_mode": "intraday",
                "data_quality_status": "正常",
                "price_status_summary": {"live_count": 20, "delayed_count": 0, "cached_count": 0, "missing_count": 0},
                "next_action": {"label": "不需手動更新", "endpoint": ""},
                "_health_source": "/api/health",
            }
        )

        self.assertEqual(exit_code, 0)
        self.assertIn("[PASS]", report)
        self.assertIn("live=20", report)
        self.assertIn("/api/health", report)

    def test_render_report_returns_zero_for_warning(self):
        exit_code, report = script.render_report(
            {
                "status": "warning",
                "summary": "非盤中模式",
                "market_mode": "closed_review",
                "data_quality_status": "休市復盤",
                "warnings": ["目前不是盤中即時模式"],
                "next_action": {"label": "查看復盤", "endpoint": "/dashboard"},
            }
        )

        self.assertEqual(exit_code, 0)
        self.assertIn("[WARN]", report)
        self.assertIn("查看復盤", report)

    def test_render_report_builds_health_when_api_payload_has_no_operational_health(self):
        exit_code, report = script.render_report(
            {
                "market_mode": "intraday",
                "allow_intraday_signal": True,
                "price_status_summary": {"status": "嚴重缺漏", "live_count": 0, "missing_ratio": 0.9},
                "required_stale_layers": [],
                "stale_layers": [],
                "refresh_guidance": {"severity": "ok"},
                "refresh_operation_summary": {"severity": "ok"},
            }
        )

        self.assertEqual(exit_code, 1)
        self.assertIn("[FAIL]", report)
        self.assertIn("嚴重缺漏", report)

    def test_main_prints_fetch_failure_without_traceback(self):
        original = script.fetch_health_payload

        def failing_fetch(*args, **kwargs):
            raise RuntimeError("down")

        script.fetch_health_payload = failing_fetch
        stream = io.StringIO()
        try:
            with contextlib.redirect_stdout(stream):
                exit_code = script.main(["--base-url", "https://example.invalid"])
        finally:
            script.fetch_health_payload = original

        self.assertEqual(exit_code, 1)
        self.assertIn("無法讀取", stream.getvalue())
        self.assertIn("next_action", stream.getvalue())

    def test_fetch_health_payload_prefers_health_endpoint(self):
        calls = []
        original = script.fetch_json

        def fake_fetch(base_url, path, **kwargs):
            calls.append(path)
            return {"status": "ok", "summary": "ok", "next_action": {"label": "不用更新"}}

        script.fetch_json = fake_fetch
        try:
            payload = script.fetch_health_payload("https://example.test")
        finally:
            script.fetch_json = original

        self.assertEqual(calls, ["/api/health"])
        self.assertEqual(payload["_health_source"], "/api/health")

    def test_fetch_health_payload_falls_back_to_refresh_status(self):
        calls = []
        original = script.fetch_json

        def fake_fetch(base_url, path, **kwargs):
            calls.append(path)
            if path == "/api/health":
                raise RuntimeError("404")
            return {
                "market_mode": "intraday",
                "allow_intraday_signal": True,
                "price_status_summary": {"status": "正常", "live_count": 1},
                "required_stale_layers": [],
                "stale_layers": [],
                "refresh_guidance": {"severity": "ok"},
                "refresh_operation_summary": {"severity": "ok"},
            }

        script.fetch_json = fake_fetch
        try:
            payload = script.fetch_health_payload("https://example.test")
        finally:
            script.fetch_json = original

        self.assertEqual(calls, ["/api/health", "/api/refresh/status"])
        self.assertIn("fallback", payload["_health_source"])


if __name__ == "__main__":
    unittest.main()
