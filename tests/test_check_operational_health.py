import contextlib
import io
import unittest

import scripts.check_operational_health as script


class CheckOperationalHealthScriptTests(unittest.TestCase):
    def test_render_report_returns_zero_for_ok(self):
        exit_code, report = script.render_report(
            {
                "operational_health": {
                    "status": "ok",
                    "summary": "系統狀態正常",
                    "market_mode": "intraday",
                    "data_quality_status": "正常",
                    "live_count": 20,
                    "delayed_count": 0,
                    "cached_count": 0,
                    "missing_count": 0,
                    "next_action": {"label": "不需手動更新", "endpoint": ""},
                }
            }
        )

        self.assertEqual(exit_code, 0)
        self.assertIn("[PASS]", report)
        self.assertIn("live=20", report)

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
        original = script.fetch_refresh_status

        def failing_fetch(*args, **kwargs):
            raise RuntimeError("down")

        script.fetch_refresh_status = failing_fetch
        stream = io.StringIO()
        try:
            with contextlib.redirect_stdout(stream):
                exit_code = script.main(["--base-url", "https://example.invalid"])
        finally:
            script.fetch_refresh_status = original

        self.assertEqual(exit_code, 1)
        self.assertIn("無法讀取", stream.getvalue())
        self.assertIn("next_action", stream.getvalue())


if __name__ == "__main__":
    unittest.main()
