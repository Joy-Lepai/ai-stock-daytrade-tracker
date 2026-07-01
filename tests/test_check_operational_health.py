import contextlib
import io
import unittest

import scripts.check_operational_health as script


class CheckOperationalHealthScriptTests(unittest.TestCase):
    def _limit_up_summary(self):
        return {
            "near_limit_up_count": 2,
            "entered_ai_count": 2,
            "high_risk_count": 1,
            "wait_confirm_count": 1,
            "avoid_count": 0,
            "data_missing_count": 0,
            "summary": "1 檔追價風險高；1 檔等待確認。",
            "action": "先看漲停強勢速讀與急拉作戰卡。",
            "risk_gate": "接近漲停不可直接升級買多。",
            "top_symbols": ["8150.TW｜南茂", "6919.TW｜康霈"],
            "top_watchlist": [
                {
                    "symbol": "8150.TW",
                    "name": "南茂",
                    "grade": "C",
                    "entry_status": "high_risk",
                    "action": "放進追價風險觀察。",
                    "avoid": "不要把 high_risk 當成買多。",
                }
            ],
        }

    def test_render_report_returns_zero_for_ok(self):
        exit_code, report = script.render_report(
            {
                "status": "ok",
                "summary": "系統狀態正常",
                "opening_preflight": {
                    "light": "green",
                    "label": "可進入盤中追蹤",
                    "reason": "資料可用。",
                    "next_action": "先看強烈買多",
                    "can_trust_strong_buy": True,
                },
                "operator_briefing": {
                    "headline": "資料可用，照強烈買多漏斗與進場雷達看盤",
                    "posture": "盤中作戰",
                    "next_check": "先看強烈買多候選。",
                    "risk_gate": "high_risk 不可當作買多。",
                },
                "operator_decision": {
                    "decision": "可盯盤",
                    "headline": "可以進入盤中追蹤",
                    "reason": "資料可用。",
                    "first_action": "先看強烈買多",
                    "can_trade_now": True,
                },
                "watch_readiness": "可正常看盤",
                "watch_readiness_message": "仍需依停損確認",
                "market_mode": "intraday",
                "data_quality_status": "正常",
                "price_status_summary": {"live_count": 20, "delayed_count": 0, "cached_count": 0, "missing_count": 0},
                "front_category_summary": {
                    "counts": {"強烈買多": 1, "買多": 2, "觀察": 3, "看空": 4},
                    "strong_buy_count": 1,
                    "buy_count": 2,
                    "watch_count": 3,
                    "bearish_count": 4,
                    "no_signal_reason": "目前有 1 檔強烈買多候選。",
                },
                "next_action": {"label": "不需手動更新", "endpoint": ""},
                "_health_source": "/api/health",
            }
        )

        self.assertEqual(exit_code, 0)
        self.assertIn("[PASS]", report)
        self.assertIn("operator_task_card:", report)
        self.assertIn("status: 可盯盤：強烈買多 1 檔", report)
        self.assertIn("first_step: 先看強烈買多，再逐檔確認進場雷達與停損距離", report)
        self.assertIn("refresh: 不需手動刷新", report)
        self.assertIn("operator_briefing:", report)
        self.assertIn("opening_preflight:", report)
        self.assertIn("operator_decision:", report)
        self.assertIn("light: green", report)
        self.assertIn("label: 可進入盤中追蹤", report)
        self.assertIn("can_trust_strong_buy: True", report)
        self.assertIn("decision: 可盯盤", report)
        self.assertIn("can_trade_now: True", report)
        self.assertIn("headline: 資料可用", report)
        self.assertIn("next_check: 先看強烈買多候選。", report)
        self.assertIn("watch_readiness: 可正常看盤，仍需依停損確認", report)
        self.assertIn("live=20", report)
        self.assertIn("front_category_summary: strong_buy=1 buy=2 watch=3 bearish=4", report)
        self.assertIn("front_no_signal_reason: 目前有 1 檔強烈買多候選。", report)
        self.assertIn("/api/health", report)
        self.assertIn("operator_page: https://stock.letslepai.com/operator", report)

    def test_render_report_understands_operator_runbook_payload(self):
        exit_code, report = script.render_report(
            {
                "api_status": "ok",
                "mode": "盤中作戰模式",
                "headline": "可以進入盤中追蹤",
                "decision": "可盯盤",
                "first_action": "先看強烈買多，再確認進場雷達",
                "can_trade_now": True,
                "can_use_intraday_signals": True,
                "can_trust_strong_buy": True,
                "data_quality_status": "正常",
                "market_mode": "intraday",
                "watch_readiness": "可正常看盤",
                "watch_readiness_message": "仍需依停損確認",
                "now_steps": ["先看強烈買多候選", "確認進場雷達", "檢查停損距離"],
                "checklist": ["是否站上 VWAP？", "量比是否足夠？"],
                "do_not_do": ["不要追 high_risk"],
                "refresh_actions": ["/refresh_watchlist"],
                "front_category_summary": {
                    "counts": {"強烈買多": 0, "買多": 1, "觀察": 7, "看空": 2},
                    "strong_buy_count": 0,
                    "buy_count": 1,
                    "watch_count": 7,
                    "bearish_count": 2,
                    "no_signal_reason": "目前沒有強烈買多，先看買多清單的下一步觸發條件。",
                },
                "limit_up_operational_summary": self._limit_up_summary(),
                "_health_source": "/api/operator/runbook",
            },
            base_url="https://stock.letslepai.com",
        )

        self.assertEqual(exit_code, 0)
        self.assertIn("Operator runbook", report)
        self.assertIn("[PASS] 可以進入盤中追蹤", report)
        self.assertIn("decision: 可盯盤", report)
        self.assertIn("first_action: 先看強烈買多，再確認進場雷達", report)
        self.assertIn("operator_steps:", report)
        self.assertIn("1. 先看強烈買多候選", report)
        self.assertIn("front_category_summary: strong_buy=0 buy=1 watch=7 bearish=2", report)
        self.assertIn("front_no_signal_reason: 目前沒有強烈買多，先看買多清單的下一步觸發條件。", report)
        self.assertIn("limit_up_operational_summary:", report)
        self.assertIn("near_limit_up=2", report)
        self.assertIn("action: 先看漲停強勢速讀與急拉作戰卡。", report)
        self.assertIn("risk_gate: 接近漲停不可直接升級買多。", report)
        self.assertIn("limit_up_watchlist:", report)
        self.assertIn("8150.TW｜南茂: status=high_risk", report)
        self.assertIn("不要把 high_risk 當成買多", report)
        self.assertIn("refresh_plan: /refresh_watchlist", report)
        self.assertIn("/api/operator/runbook", report)
        self.assertIn("operator_page: https://stock.letslepai.com/operator", report)

    def test_build_json_report_understands_operator_runbook_payload(self):
        report = script.build_json_report(
            {
                "api_status": "ok",
                "mode": "盤中作戰模式",
                "headline": "可以進入盤中追蹤",
                "decision": "可盯盤",
                "first_action": "先看強烈買多，再確認進場雷達",
                "can_trade_now": True,
                "can_use_intraday_signals": True,
                "can_trust_strong_buy": True,
                "data_quality_status": "正常",
                "market_mode": "intraday",
                "watch_readiness": "可正常看盤",
                "watch_readiness_message": "仍需依停損確認",
                "now_steps": ["先看強烈買多候選", "確認進場雷達"],
                "checklist": ["是否站上 VWAP？"],
                "do_not_do": ["不要追 high_risk"],
                "refresh_actions": ["/refresh_watchlist"],
                "front_category_summary": {
                    "counts": {"強烈買多": 0, "買多": 1, "觀察": 7, "看空": 2},
                    "strong_buy_count": 0,
                    "buy_count": 1,
                    "watch_count": 7,
                    "bearish_count": 2,
                    "no_signal_reason": "目前沒有強烈買多，先看買多清單的下一步觸發條件。",
                },
                "limit_up_operational_summary": self._limit_up_summary(),
                "_health_source": "/api/operator/runbook",
            },
            base_url="https://stock.letslepai.com",
        )

        self.assertEqual(report["status"], "ok")
        self.assertEqual(report["operator_task_card"]["status_label"], "等待觸發：買多 1 檔、觀察 7 檔")
        self.assertEqual(report["operator_task_card"]["first_step"], "先看買多清單的下一步觸發條件，不提前追")
        self.assertEqual(report["operator_task_card"]["refresh_command"], "POST /refresh_watchlist")
        self.assertEqual(report["operator_task_card"]["operator_page"], "https://stock.letslepai.com/operator")
        self.assertEqual(report["source"], "/api/operator/runbook")
        self.assertEqual(report["operator_url"], "https://stock.letslepai.com/operator")
        self.assertEqual(report["operator_decision"]["decision"], "可盯盤")
        self.assertTrue(report["operator_decision"]["can_trade_now"])
        self.assertEqual(report["do_now"], ["先看強烈買多候選", "確認進場雷達"])
        self.assertEqual(report["decision_checklist"], ["是否站上 VWAP？"])
        self.assertEqual(report["do_not_do"], ["不要追 high_risk"])
        self.assertEqual(report["front_category_summary"]["buy"], 1)
        self.assertEqual(report["front_category_summary"]["watch"], 7)
        self.assertEqual(report["limit_up_operational_summary"]["near_limit_up_count"], 2)
        self.assertEqual(report["limit_up_operational_summary"]["top_watchlist"][0]["symbol"], "8150.TW")
        self.assertIn("不可直接升級買多", report["limit_up_operational_summary"]["risk_gate"])
        self.assertEqual(
            report["front_category_summary"]["no_signal_reason"],
            "目前沒有強烈買多，先看買多清單的下一步觸發條件。",
        )
        self.assertEqual(report["manual_endpoint"], "POST /refresh_watchlist")
        self.assertEqual(report["refresh_plan"], ["/refresh_watchlist"])

    def test_old_operator_runbook_without_front_summary_is_warning(self):
        exit_code, report = script.render_report(
            {
                "api_status": "ok",
                "mode": "盤中作戰模式",
                "headline": "可以進入盤中追蹤",
                "decision": "可盯盤",
                "first_action": "先看強烈買多，再確認進場雷達",
                "can_trade_now": True,
                "can_use_intraday_signals": True,
                "can_trust_strong_buy": True,
                "data_quality_status": "正常",
                "market_mode": "intraday",
                "watch_readiness": "可正常看盤",
                "now_steps": ["先看強烈買多候選"],
                "checklist": ["是否站上 VWAP？"],
                "do_not_do": ["不要追 high_risk"],
                "_health_source": "/api/operator/runbook",
            },
            base_url="https://stock.letslepai.com",
        )

        self.assertEqual(exit_code, 0)
        self.assertIn("[WARN]", report)
        self.assertIn("尚未取得四分類摘要", report)
        self.assertIn("can_trust_strong_buy: False", report)

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
                "operator_briefing": {
                    "headline": "先修資料，不看即時訊號",
                    "posture": "暫停進場判斷",
                    "next_check": "必要資料層過期",
                    "risk_gate": "資料恢復前不可看強烈買多。",
                },
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
        self.assertIn("operator_task_card:", report)
        self.assertIn("status: 暫停：先修資料或刷新層", report)
        self.assertIn("refresh: POST /refresh_full_market", report)
        self.assertIn("watch_readiness: 暫不適合進場判斷", report)
        self.assertIn("required_stale_layers: full_market, watchlist", report)
        self.assertIn("manual_endpoint: POST /refresh_full_market", report)
        self.assertIn("curl -X POST https://stock.letslepai.com/refresh_full_market", report)
        self.assertIn("refresh_plan: /refresh_full_market -> /refresh_watchlist", report)
        self.assertIn("operator_steps:", report)
        self.assertIn("1. 先執行刷新計畫", report)

    def test_build_json_report_includes_operator_and_refresh_fields(self):
        report = script.build_json_report(
            {
                "status": "blocked",
                "summary": "必要資料層過期",
                "market_mode": "pre_open_prepare",
                "data_quality_status": "部分延遲",
                "price_status_summary": {"live_count": 0, "delayed_count": 120},
                "operator_mode": "refresh_required",
                "primary_focus": "先刷新全市場",
                "do_now": ["刷新全市場"],
                "do_not_do": ["不要按完整刷新以外的假動作"],
                "decision_checklist": ["確認 commit"],
                "required_stale_layers": ["full_market", "watchlist"],
                "stale_layers": ["full_market", "watchlist", "positions"],
                "next_action": {"label": "更新重點觀察", "endpoint": "/refresh_watchlist"},
            },
            base_url="https://stock.letslepai.com",
        )

        self.assertEqual(report["status"], "blocked")
        self.assertEqual(report["operator_task_card"]["status_label"], "暫停：先修資料或刷新層")
        self.assertEqual(report["operator_task_card"]["refresh_command"], "POST /refresh_full_market")
        self.assertEqual(report["opening_preflight"]["light"], "red")
        self.assertEqual(report["opening_preflight"]["label"], "暫停使用即時訊號")
        self.assertEqual(report["operator_decision"]["decision"], "暫停")
        self.assertFalse(report["operator_decision"]["can_trade_now"])
        self.assertEqual(report["operator_briefing"]["posture"], "暫停進場判斷")
        self.assertEqual(report["exit_code"], 1)
        self.assertEqual(report["operator_mode"], "refresh_required")
        self.assertEqual(report["primary_focus"], "先刷新全市場")
        self.assertEqual(report["counts"]["delayed"], 120)
        self.assertEqual(report["manual_endpoint"], "POST /refresh_full_market")
        self.assertIn("/refresh_full_market", report["refresh_plan"])
        self.assertIn("/refresh_watchlist", report["refresh_plan"])

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

    def test_main_json_prints_fetch_failure_without_traceback(self):
        original = script.fetch_health_payload

        def failing_fetch(*args, **kwargs):
            raise RuntimeError("down")

        script.fetch_health_payload = failing_fetch
        stream = io.StringIO()
        try:
            with contextlib.redirect_stdout(stream):
                exit_code = script.main(["--base-url", "https://example.invalid", "--json"])
        finally:
            script.fetch_health_payload = original

        payload = script.json.loads(stream.getvalue())
        self.assertEqual(exit_code, 1)
        self.assertEqual(payload["status"], "blocked")
        self.assertEqual(payload["operator_briefing"]["posture"], "暫停進場判斷")
        self.assertIn("down", payload["summary"])
        self.assertEqual(payload["refresh_plan"], [])

    def test_main_json_outputs_machine_readable_health(self):
        original = script.fetch_health_payload

        def fake_fetch(*args, **kwargs):
            return {
                "status": "warning",
                "summary": "開盤前觀察",
                "market_mode": "pre_open_prepare",
                "data_quality_status": "休市復盤",
                "price_status_summary": {"live_count": 0, "cached_count": 3},
                "required_stale_layers": ["watchlist"],
                "next_action": {"label": "更新重點觀察", "endpoint": "/refresh_watchlist"},
                "_health_source": "/api/health",
            }

        script.fetch_health_payload = fake_fetch
        stream = io.StringIO()
        try:
            with contextlib.redirect_stdout(stream):
                exit_code = script.main(["--base-url", "https://example.test", "--json"])
        finally:
            script.fetch_health_payload = original

        payload = script.json.loads(stream.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(payload["status"], "warning")
        self.assertEqual(payload["source"], "/api/health")
        self.assertEqual(payload["counts"]["cached"], 3)
        self.assertEqual(payload["refresh_plan"], ["/refresh_watchlist"])

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

    def test_main_json_can_apply_refresh_plan_and_recheck_health(self):
        original_fetch = script.fetch_health_payload
        original_apply = script.apply_refresh_plan
        fetch_calls = []
        applied = []

        def fake_fetch(base_url, **kwargs):
            fetch_calls.append(base_url)
            if len(fetch_calls) == 1:
                return {
                    "status": "blocked",
                    "summary": "重點觀察過期",
                    "market_mode": "intraday",
                    "data_quality_status": "部分延遲",
                    "price_status_summary": {"live_count": 1},
                    "required_stale_layers": ["watchlist"],
                    "next_action": {"label": "更新重點觀察", "endpoint": "/refresh_watchlist"},
                }
            return {
                "status": "ok",
                "summary": "資料正常",
                "market_mode": "intraday",
                "data_quality_status": "正常",
                "price_status_summary": {"live_count": 8},
                "next_action": {"label": "不需手動更新", "endpoint": ""},
            }

        def fake_apply(base_url, endpoints, **kwargs):
            applied.extend(endpoints)
            return [(endpoint, True, "success") for endpoint in endpoints]

        script.fetch_health_payload = fake_fetch
        script.apply_refresh_plan = fake_apply
        stream = io.StringIO()
        try:
            with contextlib.redirect_stdout(stream):
                exit_code = script.main(["--base-url", "https://example.test", "--json", "--apply-refresh-plan"])
        finally:
            script.fetch_health_payload = original_fetch
            script.apply_refresh_plan = original_apply

        payload = script.json.loads(stream.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(applied, ["/refresh_watchlist"])
        self.assertEqual(payload["initial"]["status"], "blocked")
        self.assertEqual(payload["apply_results"][0]["endpoint"], "/refresh_watchlist")
        self.assertEqual(payload["refreshed"]["status"], "ok")

    def test_fetch_health_payload_prefers_operator_runbook_endpoint(self):
        calls = []
        original = script.fetch_json

        def fake_fetch(base_url, path, **kwargs):
            calls.append(path)
            return {"api_status": "ok", "decision": "可盯盤", "now_steps": ["先看 dashboard"]}

        script.fetch_json = fake_fetch
        try:
            payload = script.fetch_health_payload("https://example.test")
        finally:
            script.fetch_json = original

        self.assertEqual(calls, ["/api/operator/runbook"])
        self.assertEqual(payload["_health_source"], "/api/operator/runbook")

    def test_fetch_health_payload_falls_back_to_health_endpoint(self):
        calls = []
        original = script.fetch_json

        def fake_fetch(base_url, path, **kwargs):
            calls.append(path)
            if path == "/api/operator/runbook":
                raise RuntimeError("404")
            return {"status": "ok", "summary": "ok", "next_action": {"label": "不用更新"}}

        script.fetch_json = fake_fetch
        try:
            payload = script.fetch_health_payload("https://example.test")
        finally:
            script.fetch_json = original

        self.assertEqual(calls, ["/api/operator/runbook", "/api/health"])
        self.assertIn("/api/health fallback", payload["_health_source"])

    def test_fetch_health_payload_falls_back_to_refresh_status(self):
        calls = []
        original = script.fetch_json

        def fake_fetch(base_url, path, **kwargs):
            calls.append(path)
            if path in {"/api/operator/runbook", "/api/health"}:
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

        self.assertEqual(calls, ["/api/operator/runbook", "/api/health", "/api/refresh/status"])
        self.assertIn("fallback", payload["_health_source"])


if __name__ == "__main__":
    unittest.main()
