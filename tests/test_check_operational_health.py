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
                "watch_readiness": "可正常看盤",
                "watch_readiness_message": "仍需依停損確認",
                "market_mode": "intraday",
                "data_quality_status": "正常",
                "price_status_summary": {"live_count": 20, "delayed_count": 0, "cached_count": 0, "missing_count": 0},
                "next_action": {"label": "不需手動更新", "endpoint": ""},
                "_health_source": "/api/health",
            }
        )

        self.assertEqual(exit_code, 0)
        self.assertIn("[PASS]", report)
        self.assertIn("watch_readiness: 可正常看盤，仍需依停損確認", report)
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
        self.assertIn("watch_readiness: 非盤中模式", report)
        self.assertIn("查看復盤", report)

    def test_render_report_prints_manual_refresh_command_for_stale_required_layer(self):
        exit_code, report = script.render_report(
            {
                "status": "blocked",
                "summary": "必要資料層過期",
                "market_mode": "pre_open_prepare",
                "data_quality_status": "部分延遲",
                "price_status_summary": {"live_count": 0, "delayed_count": 120},
                "required_stale_layers": ["full_market", "watchlist"],
                "stale_layers": ["full_market", "watchlist", "positions"],
                "next_action": {"label": "更新重點觀察", "endpoint": "/refresh_watchlist"},
            },
            base_url="https://stock.letslepai.com",
        )

        self.assertEqual(exit_code, 1)
        self.assertIn("watch_readiness: 暫不適合進場判斷", report)
        self.assertIn("required_stale_layers: full_market, watchlist", report)
        self.assertIn("manual_endpoint: POST /refresh_full_market", report)
        self.assertIn("curl -X POST https://stock.letslepai.com/refresh_full_market", report)
        self.assertIn("refresh_plan: /refresh_full_market -> /refresh_watchlist", report)

    def test_refresh_plan_orders_required_layers_safely(self):
        plan = script.refresh_plan(
            {
                "required_stale_layers": ["watchlist", "full_market"],
                "refresh_operation_summary": {"blocking_layers": ["positions"]},
            }
        )

        self.assertEqual(plan, ["/refresh_full_market", "/refresh_watchlist", "/refresh_positions"])

    def test_refresh_plan_includes_post_close_and_manual_refresh_layers(self):
        plan = script.refresh_plan(
            {
                "required_stale_layers": ["manual_full_refresh", "post_close_validation"],
                "refresh_operation_summary": {"blocking_layers": []},
            }
        )

        self.assertEqual(plan, ["/refresh_post_close_validation", "/refresh"])

    def test_refresh_plan_prefers_health_payload_plan(self):
        plan = script.refresh_plan(
            {"required_stale_layers": ["full_market"]},
            {"refresh_plan": ["/refresh_watchlist", "/dashboard", "/refresh_positions"]},
        )

        self.assertEqual(plan, ["/refresh_watchlist", "/refresh_positions"])

    def test_apply_refresh_plan_records_endpoint_failures(self):
        original = script.post_json

        def fake_post(base_url, path, **kwargs):
            if path == "/refresh_watchlist":
                raise RuntimeError("down")
            return {"status": "success"}

        script.post_json = fake_post
        try:
            results = script.apply_refresh_plan(
                "https://example.test",
                ["/refresh_full_market", "/refresh_watchlist"],
                timeout=1,
            )
        finally:
            script.post_json = original

        self.assertEqual(results[0], ("/refresh_full_market", True, "success"))
        self.assertEqual(results[1], ("/refresh_watchlist", False, "down"))

    def test_render_report_uses_refresh_guidance_when_health_has_no_next_action(self):
        exit_code, report = script.render_report(
            {
                "status": "warning",
                "summary": "重點觀察過期",
                "market_mode": "intraday",
                "data_quality_status": "部分延遲",
                "price_status_summary": {"live_count": 5, "delayed_count": 2},
                "refresh_guidance": {
                    "action_label": "更新重點觀察",
                    "action_endpoint": "/refresh_watchlist",
                },
            },
            base_url="https://stock.letslepai.com",
        )

        self.assertEqual(exit_code, 0)
        self.assertIn("watch_readiness: 可看但需保守", report)
        self.assertIn("next_action: 更新重點觀察 /refresh_watchlist", report)
        self.assertIn("manual_endpoint: POST /refresh_watchlist", report)

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

    def test_main_can_apply_refresh_plan_and_recheck_health(self):
        original_fetch = script.fetch_health_payload
        original_apply = script.apply_refresh_plan
        fetch_calls = []
        applied = []

        def fake_fetch(base_url, **kwargs):
            fetch_calls.append(base_url)
            if len(fetch_calls) == 1:
                return {
                    "status": "blocked",
                    "summary": "必要資料層過期",
                    "market_mode": "pre_open_prepare",
                    "data_quality_status": "部分延遲",
                    "price_status_summary": {"live_count": 0},
                    "required_stale_layers": ["full_market"],
                    "next_action": {"label": "更新全市場", "endpoint": "/refresh_full_market"},
                }
            return {
                "status": "warning",
                "summary": "開盤前觀察",
                "market_mode": "pre_open_prepare",
                "data_quality_status": "部分延遲",
                "price_status_summary": {"live_count": 0},
                "next_action": {"label": "查看復盤", "endpoint": "/dashboard"},
            }

        def fake_apply(base_url, endpoints, **kwargs):
            applied.extend(endpoints)
            return [(endpoint, True, "success") for endpoint in endpoints]

        script.fetch_health_payload = fake_fetch
        script.apply_refresh_plan = fake_apply
        stream = io.StringIO()
        try:
            with contextlib.redirect_stdout(stream):
                exit_code = script.main(["--base-url", "https://example.test", "--apply-refresh-plan"])
        finally:
            script.fetch_health_payload = original_fetch
            script.apply_refresh_plan = original_apply

        self.assertEqual(exit_code, 0)
        self.assertEqual(applied, ["/refresh_full_market"])
        self.assertIn("Applying refresh plan", stream.getvalue())
        self.assertIn("[PASS] POST /refresh_full_market", stream.getvalue())

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
