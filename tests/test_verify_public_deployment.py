import contextlib
import io
import unittest

import scripts.verify_public_deployment as module
from scripts.verify_public_deployment import (
    parse_advisor_symbols,
    validate_dashboard_html,
    validate_health_payload,
    validate_liveness_payload,
    validate_operator_page_html,
    validate_operator_runbook_payload,
    validate_readiness_payload,
    validate_refresh_status,
    validate_system_version,
    validate_tw_advisor_direct_html,
    validate_tw_advisor_scan,
    validate_tw_advisor_html,
)


class VerifyPublicDeploymentTests(unittest.TestCase):
    def _opening_preflight(self, light="yellow", label="復盤 / 開盤前觀察"):
        return {
            "light": light,
            "label": label,
            "reason": "目前不是盤中即時模式，只能看復盤與下個交易日觀察。",
            "next_action": "等待盤中 live 資料再判斷。",
        }

    def _operator_decision(self, decision="復盤", can_trade_now=False):
        return {
            "decision": decision,
            "headline": "現在只做復盤與觀察",
            "reason": "目前不是盤中即時模式，不提供即時進場判斷。",
            "first_action": "整理下個交易日觀察清單",
            "can_trade_now": can_trade_now,
        }

    def _front_category_summary(self):
        return {
            "counts": {"強烈買多": 0, "買多": 0, "觀察": 4, "看空": 1},
            "total": 5,
            "strong_buy_count": 0,
            "buy_count": 0,
            "watch_count": 4,
            "bearish_count": 1,
            "data_missing_count": 0,
            "bearish_ratio": 20,
            "no_signal_reason": "目前沒有強烈買多，先看買多清單的下一步觸發條件。",
        }

    def _limit_up_summary(self):
        return {
            "near_limit_up_count": 1,
            "entered_ai_count": 1,
            "high_risk_count": 1,
            "wait_confirm_count": 0,
            "avoid_count": 0,
            "data_missing_count": 0,
            "summary": "1 檔追價風險高。",
            "action": "先看漲停強勢速讀與急拉作戰卡。",
            "risk_gate": "接近漲停不可直接升級買多。",
            "top_watchlist": [
                {
                    "symbol": "8150.TW",
                    "name": "南茂",
                    "entry_status": "high_risk",
                    "action": "放進追價風險觀察。",
                    "avoid": "不要把 high_risk 當成買多。",
                }
            ],
        }

    def _health_payload(self, **overrides):
        payload = {
            "api_status": "ok",
            "status": "warning",
            "summary": "非盤中模式",
            "next_action": {"label": "查看復盤"},
            "watch_readiness": "僅供復盤或開盤前觀察",
            "operator_briefing": {
                "headline": "休市 / 盤後只做復盤與明日觀察",
                "posture": "復盤觀察",
                "next_check": "整理下個交易日觀察清單",
                "risk_gate": "非盤中模式不顯示即時強烈買多。",
            },
            "operator_steps": ["先看復盤與下個交易日觀察清單"],
            "operator_mode": "休市復盤模式",
            "primary_focus": "檢查上一交易日結果",
            "do_now": ["看上一交易日復盤"],
            "do_not_do": ["不要把昨日資料當即時買多"],
            "decision_checklist": ["資料是否為 live？"],
            "refresh_plan": [],
            "market_mode": "closed_review",
            "price_status_summary": {"status": "休市復盤"},
            "front_category_summary": self._front_category_summary(),
            "deployment": {"runtime_commit": "abc123", "tracker_commit": "abc123"},
            "can_show_strong_long": False,
            "opening_preflight": self._opening_preflight(),
            "operator_decision": self._operator_decision(),
        }
        payload.update(overrides)
        return payload

    def test_main_reports_endpoint_fetch_failures_without_traceback(self):
        original_fetch_json = module.fetch_json
        original_fetch_json_with_status = module.fetch_json_with_status
        original_fetch_text = module.fetch_text

        def fail(*args, **kwargs):
            raise RuntimeError("down")

        module.fetch_json = fail
        module.fetch_json_with_status = fail
        module.fetch_text = fail
        stream = io.StringIO()
        try:
            with contextlib.redirect_stdout(stream):
                exit_code = module.main(["--base-url", "https://example.test", "--expected-commit", "abc123"])
        finally:
            module.fetch_json = original_fetch_json
            module.fetch_json_with_status = original_fetch_json_with_status
            module.fetch_text = original_fetch_text

        output = stream.getvalue()
        self.assertEqual(exit_code, 1)
        self.assertIn("Health endpoint", output)
        self.assertIn("endpoint reachable", output)
        self.assertIn("down", output)
        self.assertNotIn("Traceback", output)

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
            "front_category_summary": self._front_category_summary(),
            "refresh_operation_summary": {"severity": "ok", "message": "必要資料層正常。"},
            "operational_health": {
                "status": "warning",
                "summary": "開盤前準備模式",
                "next_action": {"label": "不需手動更新"},
                "watch_readiness": "僅供復盤或開盤前觀察",
                "refresh_plan": [],
                "opening_preflight": self._opening_preflight(label="開盤前觀察"),
                "operator_decision": self._operator_decision("復盤"),
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
            "front_category_summary": self._front_category_summary(),
            "refresh_operation_summary": {"severity": "ok", "message": "休市復盤模式。"},
            "operational_health": {
                "status": "warning",
                "summary": "休市復盤模式",
                "next_action": {"label": "查看復盤"},
                "watch_readiness": "僅供復盤或開盤前觀察",
                "refresh_plan": [],
                "opening_preflight": self._opening_preflight(),
                "operator_decision": self._operator_decision(),
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
            "front_category_summary": self._front_category_summary(),
            "refresh_operation_summary": {"severity": "warn", "message": "重點觀察需更新。"},
            "operational_health": {
                "status": "blocked",
                "summary": "重點觀察過期",
                "next_action": {"label": "更新重點觀察"},
                "watch_readiness": "暫不適合進場判斷",
                "refresh_plan": ["/refresh_watchlist"],
                "opening_preflight": self._opening_preflight("red", "暫停使用即時訊號"),
                "operator_decision": self._operator_decision("暫停"),
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
            "front_category_summary": self._front_category_summary(),
            "refresh_operation_summary": {"severity": "ok", "message": "必要資料層正常。"},
            "operational_health": {
                "status": "blocked",
                "summary": "資料異常",
                "next_action": {"label": "先修正資料"},
                "watch_readiness": "暫不適合進場判斷",
                "refresh_plan": ["/refresh_watchlist"],
                "opening_preflight": self._opening_preflight("red", "暫停使用即時訊號"),
                "operator_decision": self._operator_decision("暫停"),
            },
        }

        checks = validate_refresh_status(payload)

        failed = [item.name for item in checks if not item.ok]
        self.assertIn("stale market mode blocks operation summary", failed)

    def test_health_payload_requires_core_monitoring_fields(self):
        payload = self._health_payload()

        checks = validate_health_payload(payload)

        self.assertTrue(all(item.ok for item in checks), checks)

    def test_health_payload_requires_watch_readiness(self):
        payload = self._health_payload()
        payload.pop("watch_readiness")

        checks = validate_health_payload(payload)

        failed = [item.name for item in checks if not item.ok]
        self.assertIn("health has watch readiness", failed)

    def test_health_payload_requires_front_category_summary(self):
        payload = self._health_payload()
        payload.pop("front_category_summary")

        checks = validate_health_payload(payload)

        failed = [item.name for item in checks if not item.ok]
        self.assertIn("health includes front category summary", failed)

    def test_operator_runbook_payload_requires_user_action_fields(self):
        payload = {
            "api_status": "ok",
            "mode": "盤中作戰模式",
            "headline": "可以進入盤中追蹤",
            "decision": "可盯盤",
            "first_action": "先看強烈買多，再確認進場雷達",
            "now_steps": ["先看強烈買多候選"],
            "no_signal_triage": ["若沒有強烈買多，先看強烈買多漏斗。"],
            "checklist": ["是否站上 VWAP？"],
            "do_not_do": ["不要追 high_risk"],
            "data_quality_status": "正常",
            "front_category_summary": self._front_category_summary(),
            "limit_up_operational_summary": self._limit_up_summary(),
            "operator_task_card": {
                "status_label": "等待觸發：買多 1 檔、觀察 4 檔",
                "first_step": "先看買多清單的下一步觸發條件，不提前追",
                "do_not": "不要追 high_risk",
                "refresh": "不需手動刷新",
            },
        }

        checks = validate_operator_runbook_payload(payload)

        self.assertTrue(all(item.ok for item in checks), checks)

    def test_operator_runbook_payload_fails_without_first_action(self):
        payload = {
            "api_status": "ok",
            "mode": "盤中作戰模式",
            "headline": "可以進入盤中追蹤",
            "decision": "可盯盤",
            "first_action": "",
            "now_steps": ["先看強烈買多候選"],
            "no_signal_triage": ["若沒有強烈買多，先看強烈買多漏斗。"],
            "checklist": ["是否站上 VWAP？"],
            "do_not_do": ["不要追 high_risk"],
            "data_quality_status": "正常",
            "front_category_summary": self._front_category_summary(),
            "limit_up_operational_summary": self._limit_up_summary(),
            "operator_task_card": {
                "status_label": "等待觸發：買多 1 檔、觀察 4 檔",
                "first_step": "先看買多清單的下一步觸發條件，不提前追",
                "do_not": "不要追 high_risk",
                "refresh": "不需手動刷新",
            },
        }

        checks = validate_operator_runbook_payload(payload)
        failed = [item.name for item in checks if not item.ok]

        self.assertIn("operator runbook has first action", failed)

    def test_operator_runbook_payload_requires_no_signal_triage(self):
        payload = {
            "api_status": "ok",
            "mode": "盤中作戰模式",
            "headline": "可以進入盤中追蹤",
            "decision": "可盯盤",
            "first_action": "先看強烈買多，再確認進場雷達",
            "now_steps": ["先看強烈買多候選"],
            "checklist": ["是否站上 VWAP？"],
            "do_not_do": ["不要追 high_risk"],
            "data_quality_status": "正常",
            "front_category_summary": self._front_category_summary(),
            "limit_up_operational_summary": self._limit_up_summary(),
            "operator_task_card": {
                "status_label": "等待觸發：買多 1 檔、觀察 4 檔",
                "first_step": "先看買多清單的下一步觸發條件，不提前追",
                "do_not": "不要追 high_risk",
                "refresh": "不需手動刷新",
            },
        }

        checks = validate_operator_runbook_payload(payload)
        failed = [item.name for item in checks if not item.ok]

        self.assertIn("operator runbook has no-signal triage", failed)

    def test_operator_runbook_payload_requires_task_card(self):
        payload = {
            "api_status": "ok",
            "mode": "盤中作戰模式",
            "headline": "可以進入盤中追蹤",
            "decision": "可盯盤",
            "first_action": "先看強烈買多，再確認進場雷達",
            "now_steps": ["先看強烈買多候選"],
            "no_signal_triage": ["若沒有強烈買多，先看強烈買多漏斗。"],
            "checklist": ["是否站上 VWAP？"],
            "do_not_do": ["不要追 high_risk"],
            "data_quality_status": "正常",
            "front_category_summary": self._front_category_summary(),
            "limit_up_operational_summary": self._limit_up_summary(),
        }

        checks = validate_operator_runbook_payload(payload)
        failed = [item.name for item in checks if not item.ok]

        self.assertIn("operator runbook includes task card", failed)

    def test_operator_runbook_payload_requires_front_category_summary(self):
        payload = {
            "api_status": "ok",
            "mode": "盤中作戰模式",
            "headline": "可以進入盤中追蹤",
            "decision": "可盯盤",
            "first_action": "先看強烈買多，再確認進場雷達",
            "now_steps": ["先看強烈買多候選"],
            "no_signal_triage": ["若沒有強烈買多，先看強烈買多漏斗。"],
            "checklist": ["是否站上 VWAP？"],
            "do_not_do": ["不要追 high_risk"],
            "data_quality_status": "正常",
        }

        checks = validate_operator_runbook_payload(payload)
        failed = [item.name for item in checks if not item.ok]

        self.assertIn("operator runbook includes front category summary", failed)

    def test_operator_runbook_payload_requires_limit_up_context(self):
        payload = {
            "api_status": "ok",
            "mode": "盤中作戰模式",
            "headline": "可以進入盤中追蹤",
            "decision": "可盯盤",
            "first_action": "先看強烈買多，再確認進場雷達",
            "now_steps": ["先看強烈買多候選"],
            "no_signal_triage": ["若沒有強烈買多，先看強烈買多漏斗。"],
            "checklist": ["是否站上 VWAP？"],
            "do_not_do": ["不要追 high_risk"],
            "data_quality_status": "正常",
            "front_category_summary": self._front_category_summary(),
            "operator_task_card": {
                "status_label": "等待觸發：買多 1 檔、觀察 4 檔",
                "first_step": "先看買多清單的下一步觸發條件，不提前追",
                "do_not": "不要追 high_risk",
                "refresh": "不需手動刷新",
            },
        }

        checks = validate_operator_runbook_payload(payload)
        failed = [item.name for item in checks if not item.ok]

        self.assertIn("operator runbook includes limit-up context", failed)

    def test_operator_page_html_requires_user_action_sections(self):
        html = """
        開盤前 / 盤中作戰手冊
        目前判斷
        現在照這樣做
        沒有訊號時先查
        operator-no-signal-triage
        開盤任務卡
        operator-task-status
        operator-task-first
        operator-task-do-not
        operator-task-refresh
        四分類摘要
        operator-front-strong
        operator-front-buy
        operator-front-watch
        operator-front-bearish
        operator-front-reason
        進場前檢查
        今天不要做
        手動刷新建議
        部署與資料
        作戰手冊會每 30 秒自動更新
        operator-refresh-status
        operator-deployment-warnings
        /api/operator/runbook
        急拉 / 漲停盤提醒
        operator-limit-up-context
        operator-limit-up-watchlist
        接近漲停不可直接升級買多
        本系統僅供資料整理
        """

        checks = validate_operator_page_html(html)

        self.assertTrue(all(item.ok for item in checks), checks)

    def test_operator_page_html_blocks_legacy_misleading_terms(self):
        html = """
        開盤前 / 盤中作戰手冊
        目前判斷
        現在照這樣做
        沒有訊號時先查
        operator-no-signal-triage
        開盤任務卡
        operator-task-status
        operator-task-first
        operator-task-do-not
        operator-task-refresh
        四分類摘要
        operator-front-strong
        operator-front-buy
        operator-front-watch
        operator-front-bearish
        operator-front-reason
        進場前檢查
        今天不要做
        手動刷新建議
        部署與資料
        作戰手冊會每 30 秒自動更新
        operator-refresh-status
        operator-deployment-warnings
        /api/operator/runbook
        急拉 / 漲停盤提醒
        operator-limit-up-context
        operator-limit-up-watchlist
        接近漲停不可直接升級買多
        本系統僅供資料整理
        做多確認
        """

        checks = validate_operator_page_html(html)
        failed = [item.name for item in checks if not item.ok]

        self.assertIn("operator page has no legacy misleading wording", failed)

    def test_health_payload_requires_refresh_plan(self):
        payload = self._health_payload()
        payload.pop("refresh_plan")

        checks = validate_health_payload(payload)

        failed = [item.name for item in checks if not item.ok]
        self.assertIn("health has refresh plan", failed)

    def test_health_payload_requires_operator_steps(self):
        payload = self._health_payload()
        payload.pop("operator_steps")

        checks = validate_health_payload(payload)

        failed = [item.name for item in checks if not item.ok]
        self.assertIn("health has operator steps", failed)

    def test_health_payload_requires_operator_guidance_fields(self):
        payload = self._health_payload(operator_mode="", primary_focus="", do_now=[], do_not_do=[], decision_checklist=[])

        checks = validate_health_payload(payload)

        failed = [item.name for item in checks if not item.ok]
        self.assertIn("health has operator mode", failed)
        self.assertIn("health has primary focus", failed)
        self.assertIn("health has do now actions", failed)
        self.assertIn("health has do not do actions", failed)
        self.assertIn("health has decision checklist", failed)

    def test_health_payload_requires_operator_briefing(self):
        payload = self._health_payload(operator_briefing={"headline": "", "next_check": ""})

        checks = validate_health_payload(payload)

        failed = [item.name for item in checks if not item.ok]
        self.assertIn("health has operator briefing", failed)

    def test_health_payload_requires_opening_preflight(self):
        payload = self._health_payload()
        payload.pop("opening_preflight")

        checks = validate_health_payload(payload)

        failed = [item.name for item in checks if not item.ok]
        self.assertIn("health has opening preflight", failed)

    def test_health_payload_requires_operator_decision(self):
        payload = self._health_payload()
        payload.pop("operator_decision")

        checks = validate_health_payload(payload)

        failed = [item.name for item in checks if not item.ok]
        self.assertIn("health has operator decision", failed)

    def test_blocked_health_cannot_show_strong_buy(self):
        payload = self._health_payload(
            status="blocked",
            summary="資料異常",
            next_action={"label": "先修資料"},
            watch_readiness="暫不適合進場判斷",
            operator_steps=["先執行刷新計畫"],
            refresh_plan=["/refresh_watchlist"],
            market_mode="intraday",
            price_status_summary={"status": "嚴重缺漏"},
            deployment={"runtime_commit": "abc123"},
            can_show_strong_long=True,
            opening_preflight=self._opening_preflight("red", "暫停使用即時訊號"),
            operator_decision=self._operator_decision("暫停"),
        )

        checks = validate_health_payload(payload)

        failed = [item.name for item in checks if not item.ok]
        self.assertIn("blocked health blocks strong buy", failed)

    def test_blocked_health_requires_red_opening_preflight(self):
        payload = self._health_payload(
            status="blocked",
            summary="資料異常",
            next_action={"label": "先修資料"},
            watch_readiness="暫不適合進場判斷",
            operator_steps=["先執行刷新計畫"],
            refresh_plan=["/refresh_watchlist"],
            market_mode="intraday",
            price_status_summary={"status": "嚴重缺漏"},
            deployment={"runtime_commit": "abc123"},
            can_show_strong_long=False,
            opening_preflight=self._opening_preflight("yellow", "復盤 / 開盤前觀察"),
            operator_decision=self._operator_decision("暫停"),
        )

        checks = validate_health_payload(payload)

        failed = [item.name for item in checks if not item.ok]
        self.assertIn("blocked health has red preflight", failed)

    def test_non_intraday_health_cannot_have_green_opening_preflight(self):
        payload = self._health_payload(opening_preflight=self._opening_preflight("green", "可進入盤中追蹤"))

        checks = validate_health_payload(payload)

        failed = [item.name for item in checks if not item.ok]
        self.assertIn("non-intraday health preflight is not green", failed)

    def test_liveness_payload_requires_alive_status(self):
        payload = {"api_status": "ok", "status": "alive", "service": "tw-daytrade-tracker"}

        checks = validate_liveness_payload(200, payload)

        self.assertTrue(all(item.ok for item in checks), checks)

    def test_readiness_payload_accepts_503_when_blocked(self):
        payload = self._health_payload(
            status="blocked",
            summary="資料異常",
            next_action={"label": "先修資料"},
            watch_readiness="暫不適合進場判斷",
            operator_steps=["先執行刷新計畫"],
            refresh_plan=["/refresh_watchlist"],
            market_mode="intraday",
            price_status_summary={"status": "嚴重缺漏"},
            deployment={"runtime_commit": "abc123"},
            can_show_strong_long=False,
            opening_preflight=self._opening_preflight("red", "暫停使用即時訊號"),
            operator_decision=self._operator_decision("暫停"),
        )

        checks = validate_readiness_payload(503, payload)

        self.assertTrue(all(item.ok for item in checks), checks)

    def test_readiness_payload_rejects_200_when_blocked(self):
        payload = self._health_payload(
            status="blocked",
            summary="資料異常",
            next_action={"label": "先修資料"},
            watch_readiness="暫不適合進場判斷",
            operator_steps=["先執行刷新計畫"],
            refresh_plan=["/refresh_watchlist"],
            market_mode="intraday",
            price_status_summary={"status": "嚴重缺漏"},
            deployment={"runtime_commit": "abc123"},
            can_show_strong_long=False,
            opening_preflight=self._opening_preflight("red", "暫停使用即時訊號"),
            operator_decision=self._operator_decision("暫停"),
        )

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
            "front_category_summary": self._front_category_summary(),
            "refresh_operation_summary": {"severity": "ok", "message": "必要資料層正常。"},
        }

        checks = validate_refresh_status(payload)

        failed = [item.name for item in checks if not item.ok]
        self.assertIn("operational health present", failed)
        self.assertIn("operational health status valid", failed)
        self.assertIn("operational health has next action", failed)

    def test_refresh_status_requires_front_category_summary(self):
        payload = {
            "api_status": "ok",
            "market_mode": "intraday",
            "required_refresh_layers": ["watchlist", "positions"],
            "required_stale_layers": [],
            "allow_strong_long": False,
            "price_status_summary": {"status": "正常"},
            "refresh_operation_summary": {"severity": "ok", "message": "必要資料層正常。"},
            "operational_health": {
                "status": "warning",
                "summary": "等待訊號",
                "next_action": {"label": "不需手動更新"},
                "watch_readiness": "可看但需保守",
                "refresh_plan": [],
                "opening_preflight": self._opening_preflight(label="等待訊號"),
                "operator_decision": self._operator_decision("等待"),
            },
        }

        checks = validate_refresh_status(payload)

        failed = [item.name for item in checks if not item.ok]
        self.assertIn("front category summary present", failed)

    def test_refresh_status_requires_opening_preflight(self):
        payload = {
            "api_status": "ok",
            "market_mode": "intraday",
            "required_refresh_layers": ["watchlist", "positions"],
            "required_stale_layers": [],
            "allow_strong_long": False,
            "price_status_summary": {"status": "正常"},
            "front_category_summary": self._front_category_summary(),
            "refresh_operation_summary": {"severity": "ok", "message": "必要資料層正常。"},
            "operational_health": {
                "status": "warning",
                "summary": "開盤前準備模式",
                "next_action": {"label": "不需手動更新"},
                "watch_readiness": "僅供復盤或開盤前觀察",
                "refresh_plan": [],
                "operator_decision": self._operator_decision("保守觀察"),
            },
        }

        checks = validate_refresh_status(payload)

        failed = [item.name for item in checks if not item.ok]
        self.assertIn("operational health has opening preflight", failed)

    def test_refresh_status_requires_operator_decision(self):
        payload = {
            "api_status": "ok",
            "market_mode": "intraday",
            "required_refresh_layers": ["watchlist", "positions"],
            "required_stale_layers": [],
            "allow_strong_long": False,
            "price_status_summary": {"status": "正常"},
            "front_category_summary": self._front_category_summary(),
            "refresh_operation_summary": {"severity": "ok", "message": "必要資料層正常。"},
            "operational_health": {
                "status": "warning",
                "summary": "開盤前準備模式",
                "next_action": {"label": "不需手動更新"},
                "watch_readiness": "僅供復盤或開盤前觀察",
                "refresh_plan": [],
                "opening_preflight": self._opening_preflight(label="開盤前觀察"),
            },
        }

        checks = validate_refresh_status(payload)

        failed = [item.name for item in checks if not item.ok]
        self.assertIn("operational health has operator decision", failed)

    def test_non_intraday_refresh_status_cannot_have_green_opening_preflight(self):
        payload = {
            "api_status": "ok",
            "market_mode": "closed_review",
            "required_refresh_layers": ["full_market"],
            "required_stale_layers": [],
            "allow_strong_long": False,
            "price_status_summary": {"status": "正常"},
            "front_category_summary": self._front_category_summary(),
            "refresh_operation_summary": {"severity": "ok", "message": "休市復盤模式。"},
            "operational_health": {
                "status": "warning",
                "summary": "休市復盤模式",
                "next_action": {"label": "查看復盤"},
                "watch_readiness": "僅供復盤或開盤前觀察",
                "refresh_plan": [],
                "opening_preflight": self._opening_preflight("green", "可進入盤中追蹤"),
                "operator_decision": self._operator_decision("復盤"),
            },
        }

        checks = validate_refresh_status(payload)

        failed = [item.name for item in checks if not item.ok]
        self.assertIn("non-intraday opening preflight is not green", failed)

    def test_blocked_operational_health_must_block_strong_buy(self):
        payload = {
            "api_status": "ok",
            "market_mode": "intraday",
            "required_refresh_layers": ["watchlist", "positions"],
            "required_stale_layers": [],
            "allow_strong_long": True,
            "price_status_summary": {"status": "正常"},
            "front_category_summary": self._front_category_summary(),
            "refresh_operation_summary": {"severity": "ok", "message": "必要資料層正常。"},
            "operational_health": {
                "status": "blocked",
                "summary": "資料異常",
                "next_action": {"label": "先修資料"},
                "watch_readiness": "暫不適合進場判斷",
                "refresh_plan": ["/refresh_watchlist"],
                "opening_preflight": self._opening_preflight("red", "暫停使用即時訊號"),
                "operator_decision": self._operator_decision("暫停"),
            },
        }

        checks = validate_refresh_status(payload)

        failed = [item.name for item in checks if not item.ok]
        self.assertIn("blocked operational health blocks strong buy", failed)

    def test_dashboard_html_requires_core_sections_and_blocks_legacy_words(self):
        required_html = """
        今日決策摘要 今日資料可信度 最接近強烈買多 等待確認池
        進場雷達成績單 資料健康度 台股全市場異動掃描池
        漏抓股票診斷 強烈買多漏斗 卡關處理順序 等到什麼 系統狀態與資料來源 看盤狀態 刷新順序
        """

        for legacy_word in ["做多確認", "建議買多", "今日做多推薦"]:
            with self.subTest(legacy_word=legacy_word):
                checks = validate_dashboard_html(required_html + f" {legacy_word}")

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
        今日決策摘要 今日資料可信度 最接近強烈買多 等待確認池
        進場雷達成績單 資料健康度 台股全市場異動掃描池
        漏抓股票診斷 強烈買多漏斗 卡關處理順序 等到什麼 系統狀態與資料來源 看盤狀態 刷新順序
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
        今日決策摘要 今日資料可信度 最接近強烈買多 等待確認池
        進場雷達成績單 資料健康度 台股全市場異動掃描池
        漏抓股票診斷 強烈買多漏斗 卡關處理順序 等到什麼 系統狀態與資料來源 看盤狀態 刷新順序
        TWSE 上市掃描：成功，普通股池 692 檔；TPEX 上櫃掃描：成功，普通股池 389 檔；今日異動候選 40 檔。
        <details><summary>候選股怎麼選出來？</summary>
        <div class="metric"><span>完整普通股池</span><strong>1081</strong></div>
        <div class="metric"><span>今日異動候選</span><strong>40</strong></div>
        <div class="metric"><span>送入模型評分</span><strong>118</strong></div>
        </details>
        """

        checks = validate_dashboard_html(html)

        self.assertTrue(all(item.ok for item in checks), checks)

    def test_dashboard_html_accepts_review_mode_decision_section_titles(self):
        html = """
        今日決策摘要 今日資料可信度 上一交易日復盤重點 下個交易日觀察清單
        進場雷達成績單 資料健康度 台股全市場異動掃描池
        漏抓股票診斷 強烈買多漏斗 卡關處理順序 等到什麼 系統狀態與資料來源 看盤狀態 刷新順序
        TWSE 上市掃描：成功，普通股池 692 檔；TPEX 上櫃掃描：成功，普通股池 389 檔；今日異動候選 40 檔。
        <details><summary>候選股怎麼選出來？</summary>
        <div class="metric"><span>完整普通股池</span><strong>1081</strong></div>
        <div class="metric"><span>今日異動候選</span><strong>40</strong></div>
        <div class="metric"><span>送入模型評分</span><strong>118</strong></div>
        </details>
        """

        checks = validate_dashboard_html(html)

        self.assertTrue(all(item.ok for item in checks), checks)

    def test_non_intraday_bearish_heavy_dashboard_requires_guard_copy(self):
        html = """
        今日決策摘要 今日資料可信度 上一交易日復盤重點 下個交易日觀察清單
        進場雷達成績單 資料健康度 台股全市場異動掃描池
        漏抓股票診斷 強烈買多漏斗 卡關處理順序 等到什麼 系統狀態與資料來源 看盤狀態 刷新順序
        開盤前準備模式
        <div class="metric"><span>強烈買多</span><strong>0</strong></div>
        <div class="metric"><span>買多</span><strong>0</strong></div>
        <div class="metric"><span>觀察</span><strong>0</strong></div>
        <div class="metric"><span>看空</span><strong>52</strong></div>
        <details><summary>候選股怎麼選出來？</summary>
        <div class="metric"><span>完整普通股池</span><strong>1081</strong></div>
        <div class="metric"><span>今日異動候選</span><strong>40</strong></div>
        <div class="metric"><span>送入模型評分</span><strong>118</strong></div>
        </details>
        """

        checks = validate_dashboard_html(html)

        failed = [item.name for item in checks if not item.ok]
        self.assertIn("non-intraday bearish-heavy dashboard has review guard copy", failed)

    def test_non_intraday_bearish_heavy_dashboard_accepts_guard_copy(self):
        html = """
        今日決策摘要 今日資料可信度 上一交易日復盤重點 下個交易日觀察清單
        進場雷達成績單 資料健康度 台股全市場異動掃描池
        漏抓股票診斷 強烈買多漏斗 卡關處理順序 等到什麼 系統狀態與資料來源 看盤狀態 刷新順序
        開盤前準備模式。看空偏多這不是做空建議，僅供復盤，等開盤後再確認。
        <div class="metric"><span>強烈買多</span><strong>0</strong></div>
        <div class="metric"><span>買多</span><strong>0</strong></div>
        <div class="metric"><span>觀察</span><strong>0</strong></div>
        <div class="metric"><span>看空</span><strong>52</strong></div>
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
        本系統僅供資料整理 確認品質 不代表建議放空
        """

        for legacy_word in ["買多推薦", "建議買多", "今日做多推薦"]:
            with self.subTest(legacy_word=legacy_word):
                checks = validate_tw_advisor_html(required_html + f" {legacy_word}")

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
        本系統僅供資料整理 確認品質 不代表建議放空
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
            "entry_confirmation": {"confirmation_quality": "blocked"},
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
            "entry_confirmation": {"confirmation_quality": "blocked"},
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
        self.assertIn("advisor scan has confirmation quality", failed)
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
            "entry_confirmation": {"confirmation_quality": "blocked"},
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
            "entry_confirmation": {"confirmation_quality": "limited"},
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
            "entry_confirmation": {"confirmation_quality": "blocked"},
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
            "entry_confirmation": {"confirmation_quality": "standard"},
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
            "entry_confirmation": {"confirmation_quality": "standard"},
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
            "entry_confirmation": {"confirmation_quality": "standard"},
            "candidate": {"entry_status": "executable", "above_vwap": True},
            "key_metrics": {"stop_loss": 598.0},
            "data_health": {"price_status": "live", "can_use_for_intraday_signal": True},
            "market_mode": {"mode": "intraday"},
        }

        checks = validate_tw_advisor_scan(payload, "2330")

        self.assertTrue(all(item.ok for item in checks), checks)


if __name__ == "__main__":
    unittest.main()
