import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from unittest.mock import patch

from stock_daytrade_system.db import connect, default_db_path, upsert_last_known_price, upsert_refresh_state
from stock_daytrade_system.refresh_service import (
    DEFAULT_TRACKER_TIMEOUT_SECONDS,
    REFRESH_LAYER_RUNNING_STUCK_SECONDS,
    RefreshCoordinator,
    _front_category_summary,
    _layer_has_usable_fresh_success,
    _layer_status,
    _refresh_operation_summary,
    _status_allows_strong_long,
)
from stock_daytrade_system.resilience import GLOBAL_HEALTH, record_source_health


class RefreshServiceTests(unittest.TestCase):
    def tearDown(self):
        GLOBAL_HEALTH.reset()

    def test_default_tracker_timeout_allows_render_full_market_refresh(self):
        with tempfile.TemporaryDirectory() as directory:
            coordinator = RefreshCoordinator(Path(directory), Path(directory) / "reports")

        self.assertEqual(DEFAULT_TRACKER_TIMEOUT_SECONDS, 180)
        self.assertEqual(coordinator.tracker_timeout_seconds, 180)
        self.assertEqual(REFRESH_LAYER_RUNNING_STUCK_SECONDS["full_market"], 240)

    def test_running_full_market_marks_stuck_before_generic_stale_window(self):
        now = datetime(2026, 7, 22, 9, 5, tzinfo=ZoneInfo("Asia/Taipei"))
        started = datetime(2026, 7, 22, 9, 0, tzinfo=ZoneInfo("Asia/Taipei"))

        layer = _layer_status(
            {
                "layer": "full_market",
                "last_started_at": started.isoformat(timespec="seconds"),
                "last_success_at": None,
                "duration_seconds": None,
                "status": "running",
                "symbols_count": 0,
                "error": "",
                "stale_after_seconds": 900,
            },
            now,
        )

        self.assertEqual(layer["status"], "stuck")
        self.assertTrue(layer["is_running_stuck"])
        self.assertEqual(layer["stale_label"], "刷新可能卡住")
        self.assertEqual(layer["error"], "running_exceeded_240s")
        self.assertLess(layer["running_stuck_after_seconds"], layer["stale_after_seconds"])

    def test_status_payload_returns_market_mode_without_triggering_scan(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            reports = project / "reports"
            with connect(default_db_path(project)) as conn:
                conn.execute(
                    """
                    INSERT INTO intraday_snapshots (
                      captured_at, date, symbol, last_price, volume, turnover, vwap,
                      above_vwap, volume_ratio, opening_range_high, opening_range_low
                    ) VALUES (
                      '2026-06-19T09:05:00+08:00', '2026-06-19', '2330.TW',
                      100, 1000, 100000, 99.5, 1, 1.2, 101, 98
                    )
                    """
                )
            coordinator = RefreshCoordinator(project, reports)

            payload = coordinator.status_payload()

            self.assertEqual(payload["api_status"], "ok")
            self.assertIn("market_mode", payload)
            self.assertIn("is_holiday", payload)
            self.assertIn("allow_strong_long", payload)
            self.assertIn("layers", payload)
            self.assertIn("provider_status", payload)
            self.assertEqual(payload["provider_status"]["active_provider"], "yahoo")
            self.assertIn("deployment_status", payload)
            self.assertIn("commit", payload["deployment_status"])
            self.assertIn("signal_guard_version", payload)
            self.assertIn("price_status_summary", payload)
            self.assertIn("live_count", payload)
            self.assertIn("can_show_any_strong_long", payload)
            self.assertIn("refresh_operation_summary", payload)
            self.assertIn("message", payload["refresh_operation_summary"])
            self.assertIn("operational_health", payload)
            self.assertIn("buy_signal_diagnosis", payload)
            self.assertIn(payload["operational_health"]["status"], {"ok", "warning", "blocked"})
            self.assertIn("plain_answer", payload["buy_signal_diagnosis"])

    def test_status_payload_includes_data_source_health(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            reports = project / "reports"
            with connect(default_db_path(project)):
                pass
            record_source_health("twse", "OK", success_count=714)
            record_source_health("c_money", "ERROR", failure_count=1, error="maintenance")
            coordinator = RefreshCoordinator(project, reports)

            payload = coordinator.status_payload()

        self.assertEqual(payload["data_source_health_compact"]["twse"], "OK")
        self.assertEqual(payload["data_source_health_compact"]["c_money"], "ERROR")
        self.assertTrue(payload["data_source_degraded"])

    def test_front_category_summary_distinguishes_missing_candidate_data(self):
        summary = _front_category_summary(
            [],
            market_mode="intraday",
            data_today=True,
            intraday=True,
            stale=False,
            allow_strong_long=True,
        )

        self.assertEqual(summary["total"], 0)
        self.assertIn("尚未產生四分類候選資料", summary["no_signal_reason"])

    def test_status_payload_includes_front_category_no_signal_summary(self):
        now = datetime(2026, 6, 25, 9, 30, tzinfo=ZoneInfo("Asia/Taipei"))
        captured = "2026-06-25T09:29:00+08:00"
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            reports = project / "reports"
            with connect(default_db_path(project)) as conn:
                conn.execute("INSERT INTO symbols (symbol, name, sector) VALUES ('2330.TW', '台積電', '半導體')")
                conn.execute("INSERT INTO symbols (symbol, name, sector) VALUES ('2886.TW', '兆豐金', '金融')")
                for symbol in ("2330.TW", "2886.TW"):
                    conn.execute(
                        """
                        INSERT INTO daily_snapshots (
                          date, symbol, close, change_pct, volume, turnover, volume_ratio,
                          previous_high, break_prev_high
                        ) VALUES ('2026-06-25', ?, 100, -1, 1000, 100000, 1.2, 101, 0)
                        """,
                        (symbol,),
                    )
                    conn.execute(
                        """
                        INSERT INTO intraday_snapshots (
                          captured_at, date, symbol, last_price, volume, turnover, vwap,
                          above_vwap, volume_ratio, opening_range_high, opening_range_low
                        ) VALUES (?, '2026-06-25', ?, 99, 1000, 100000, 100, 0, 1.2, 101, 98)
                        """,
                        (captured, symbol),
                    )
                    conn.execute(
                        """
                        INSERT INTO long_scores (
                          captured_at, date, symbol, bullish_score, risk_score, grade,
                          reasons, risk_reasons, confidence_score, confidence_level,
                          adjusted_entry_status
                        ) VALUES (?, '2026-06-25', ?, 40, 65, 'D', '[]', '[]', 40, 'low', 'avoid')
                        """,
                        (captured, symbol),
                    )
                upsert_refresh_state(
                    conn,
                    layer="watchlist",
                    status="success",
                    stale_after_seconds=300,
                    started_at=now,
                    success_at=now,
                    symbols_count=2,
                )
                upsert_refresh_state(
                    conn,
                    layer="positions",
                    status="success",
                    stale_after_seconds=300,
                    started_at=now,
                    success_at=now,
                    symbols_count=0,
                )
            coordinator = RefreshCoordinator(project, reports)

            payload = coordinator.status_payload(now=now)

        summary = payload["front_category_summary"]
        self.assertEqual(summary["bearish_count"], 2)
        self.assertEqual(summary["strong_buy_count"], 0)
        self.assertFalse(payload["can_show_any_strong_long"])
        self.assertIn("這不是做空建議", summary["no_signal_reason"])
        self.assertIn("這不是做空建議", " ".join(payload["operational_health"]["warnings"]))

    def test_status_payload_includes_fugle_priority_pool_from_latest_scores(self):
        now = datetime(2026, 6, 25, 9, 30, tzinfo=ZoneInfo("Asia/Taipei"))
        captured = "2026-06-25T09:29:00+08:00"
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            reports = project / "reports"
            with connect(default_db_path(project)) as conn:
                rows = [
                    ("2317.TW", "鴻海", "A", "executable", 86, 28, 80, 205, 202, 1.5, 206, 200),
                    ("2884.TW", "玉山金", "B+", "practice_long", 74, 40, 64, 33, 32.9, 1.0, 33.2, 32.6),
                    ("3037.TW", "欣興", "C", "high_risk", 90, 78, 62, 180, 165, 4.2, 181, 160),
                ]
                for symbol, name, grade, entry_status, bullish, risk, confidence, price, vwap, volume_ratio, trigger, stop in rows:
                    conn.execute("INSERT INTO symbols (symbol, name, sector) VALUES (?, ?, 'test')", (symbol, name))
                    conn.execute(
                        """
                        INSERT INTO daily_snapshots (
                          date, symbol, close, change_pct, volume, turnover, volume_ratio,
                          previous_high, break_prev_high
                        ) VALUES ('2026-06-25', ?, ?, 2, 1000, 100000, ?, ?, 1)
                        """,
                        (symbol, price, volume_ratio, trigger),
                    )
                    conn.execute(
                        """
                        INSERT INTO intraday_snapshots (
                          captured_at, date, symbol, last_price, volume, turnover, vwap,
                          above_vwap, volume_ratio, opening_range_high, opening_range_low
                        ) VALUES (?, '2026-06-25', ?, ?, 1000, 100000, ?, 1, ?, ?, ?)
                        """,
                        (captured, symbol, price, vwap, volume_ratio, trigger, stop),
                    )
                    conn.execute(
                        """
                        INSERT INTO recommendations (
                          market, date, symbol, first_seen_at, latest_seen_at, grade,
                          bullish_score, risk_score, entry_status, lifecycle_status,
                          observed_at, signal_price, trigger_price, stop_loss, target_price
                        ) VALUES ('TW', '2026-06-25', ?, ?, ?, ?, ?, ?, ?, 'observed', ?, ?, ?, ?, ?)
                        """,
                        (symbol, captured, captured, grade, bullish, risk, entry_status, captured, price, trigger, stop, trigger),
                    )
                    conn.execute(
                        """
                        INSERT INTO long_scores (
                          captured_at, date, symbol, bullish_score, risk_score, grade,
                          reasons, risk_reasons, confidence_score, confidence_level,
                          adjusted_entry_status
                        ) VALUES (?, '2026-06-25', ?, ?, ?, ?, '[]', '[]', ?, 'medium', ?)
                        """,
                        (captured, symbol, bullish, risk, grade, confidence, entry_status),
                    )
                upsert_refresh_state(conn, layer="watchlist", status="success", stale_after_seconds=300, started_at=now, success_at=now, symbols_count=3)
                upsert_refresh_state(conn, layer="positions", status="success", stale_after_seconds=300, started_at=now, success_at=now, symbols_count=0)
            coordinator = RefreshCoordinator(project, reports)

            with patch.dict("os.environ", {"FUGLE_ENABLED": "0", "FUGLE_API_KEY": "demo-key"}, clear=False):
                payload = coordinator.status_payload(now=now)

        pool = payload["fugle_priority_pool"]
        self.assertEqual(pool["source"], "latest_long_scores")
        self.assertTrue(pool["configured"])
        self.assertFalse(pool["enabled"])
        self.assertEqual(pool["selected_count"], 3)
        self.assertEqual(pool["selected_symbols"][0], "2317.TW")
        self.assertIn("FUGLE_ENABLED 尚未啟用", pool["operator_summary"])
        self.assertIn("不會直接產生強烈買多", pool["strong_buy_safety"])
        self.assertEqual(pool["confirmable_count"], 2)
        self.assertEqual(pool["high_risk_observation_count"], 1)

    def test_status_payload_includes_limit_up_operational_summary(self):
        now = datetime(2026, 6, 25, 9, 35, tzinfo=ZoneInfo("Asia/Taipei"))
        captured = "2026-06-25T09:34:00+08:00"
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            reports = project / "reports"
            with connect(default_db_path(project)) as conn:
                conn.execute(
                    """
                    INSERT INTO tw_full_market_snapshots (
                      captured_at, date, symbol, name, price, change_pct, volume,
                      turnover, volume_ratio, vwap, above_vwap, break_prev_high,
                      break_5d_high, entered_candidate_pool, entered_ai_candidates,
                      ai_grade, entry_status, trade_bias, not_selected_reason,
                      reason_code, data_status, created_at
                    ) VALUES (?, '2026-06-25', '8150.TW', '南茂', 58.5, 9.8, 1000,
                      100000, 4.2, 56, 1, 1, 1, 1, 0,
                      'C', 'high_risk', 'watch', '強勢但追價風險高',
                      'high_chase_risk', 'ok', ?)
                    """,
                    (captured, captured),
                )
            coordinator = RefreshCoordinator(project, reports)

            payload = coordinator.status_payload(now=now)

        summary = payload["limit_up_operational_summary"]
        self.assertEqual(summary["near_limit_up_count"], 1)
        self.assertEqual(summary["high_risk_count"], 1)
        self.assertIn("追價風險高", summary["summary"])
        self.assertIn("急拉作戰卡", summary["action"])
        self.assertIn("8150.TW", " ".join(summary["top_symbols"]))
        self.assertEqual(summary["top_watchlist"][0]["symbol"], "8150.TW")
        self.assertEqual(summary["top_watchlist"][0]["entry_status"], "high_risk")
        self.assertIn("追價風險觀察", summary["top_watchlist"][0]["action"])
        self.assertIn("不要把 high_risk 當成買多", summary["top_watchlist"][0]["avoid"])

    def test_status_payload_includes_review_observation_candidates_from_snapshots(self):
        now = datetime(2026, 6, 25, 14, 10, tzinfo=ZoneInfo("Asia/Taipei"))
        captured = "2026-06-25T13:35:00+08:00"
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            reports = project / "reports"
            with connect(default_db_path(project)) as conn:
                conn.execute(
                    """
                    INSERT INTO tw_full_market_snapshots (
                      captured_at, date, symbol, name, price, change_pct, volume,
                      turnover, volume_ratio, vwap, above_vwap, break_prev_high,
                      break_5d_high, entered_candidate_pool, entered_ai_candidates,
                      ai_grade, entry_status, trade_bias, not_selected_reason,
                      reason_code, data_status, created_at
                    ) VALUES (?, '2026-06-25', '8150.TW', '南茂', 58.5, 9.8, 1000,
                      100000, 4.2, 56, 1, 1, 1, 1, 0,
                      'C', 'high_risk', 'watch', '強勢但追價風險高',
                      'high_chase_risk', 'ok', ?)
                    """,
                    (captured, captured),
                )
                conn.execute(
                    """
                    INSERT INTO tw_full_market_snapshots (
                      captured_at, date, symbol, name, price, change_pct, volume,
                      turnover, volume_ratio, vwap, above_vwap, break_prev_high,
                      break_5d_high, entered_candidate_pool, entered_ai_candidates,
                      ai_grade, entry_status, trade_bias, not_selected_reason,
                      reason_code, data_status, created_at
                    ) VALUES (?, '2026-06-25', '2886.TW', '兆豐金', NULL, NULL, NULL,
                      NULL, NULL, NULL, 0, 0, 0, 0, 0,
                      '-', 'data_missing', 'watch', '資料抓取失敗',
                      'data_missing', 'data_missing', ?)
                    """,
                    (captured, captured),
                )
            coordinator = RefreshCoordinator(project, reports)

            payload = coordinator.status_payload(now=now)

        review = payload["review_observation_candidates"]
        self.assertEqual(review["status"], "ok")
        self.assertEqual(review["items"][0]["symbol"], "8150.TW")
        self.assertEqual(review["items"][0]["label"], "高風險觀察")
        self.assertIn("不是盤中即時買多", review["items"][0]["safety_note"])
        self.assertNotIn("2886.TW", " ".join(item["symbol"] for item in review["items"]))
        self.assertIn("8150.TW", payload["buy_signal_diagnosis"]["what_to_watch_now"][0])

    def test_manual_full_refresh_marks_dependent_layers_success(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            reports = project / "reports"
            with connect(default_db_path(project)):
                pass
            coordinator = RefreshCoordinator(project, reports)

            coordinator._mark_full_tracker_dependent_layers("manual_full_refresh", 120)
            payload = coordinator.status_payload()
            layers = payload["layers"]

        self.assertEqual(layers["full_market"]["status"], "success")
        self.assertEqual(layers["full_market"]["symbols_count"], 120)
        self.assertEqual(layers["watchlist"]["status"], "success")
        self.assertEqual(layers["watchlist"]["symbols_count"], 120)
        self.assertEqual(layers["positions"]["status"], "success")
        self.assertEqual(layers["positions"]["symbols_count"], 0)

    def test_status_payload_counts_cached_prices_without_global_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            reports = project / "reports"
            with connect(default_db_path(project)) as conn:
                upsert_last_known_price(
                    conn,
                    market="TW",
                    symbol="2330.TW",
                    price=100,
                    price_at="2026-06-18T09:31:00+08:00",
                    source="test",
                    fallback_used=True,
                    fallback_reason="api_failed",
                )
            coordinator = RefreshCoordinator(project, reports)

            payload = coordinator.status_payload()
            summary = payload["price_status_summary"]

        self.assertIn("cached_count", summary)
        self.assertGreaterEqual(summary["cached_count"], 0)
        self.assertIn("missing_ratio", summary)

    def test_status_payload_infers_layers_from_latest_snapshots(self):
        now = datetime.now(ZoneInfo("Asia/Taipei")).isoformat(timespec="seconds")
        today = datetime.now(ZoneInfo("Asia/Taipei")).date().isoformat()
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            reports = project / "reports"
            with connect(default_db_path(project)) as conn:
                conn.execute(
                    """
                    INSERT INTO tw_full_market_snapshots (
                      captured_at, date, symbol, name, price, change_pct, volume,
                      turnover, volume_ratio, vwap, above_vwap, break_prev_high,
                      break_5d_high, entered_candidate_pool, entered_ai_candidates,
                      ai_grade, entry_status, trade_bias, data_status, created_at
                    ) VALUES (?, ?, '2330.TW', '台積電', 100, 1, 1000,
                      100000, 1.2, 99.5, 1, 1, 1, 1, 1,
                      'A', 'wait_breakout', 'long', 'ok', ?)
                    """,
                    (now, today, now),
                )
                conn.execute(
                    """
                    INSERT INTO intraday_snapshots (
                      captured_at, date, symbol, last_price, volume, turnover, vwap,
                      above_vwap, volume_ratio, opening_range_high, opening_range_low
                    ) VALUES (?, ?, '2330.TW', 100, 1000, 100000, 99.5, 1, 1.2, 101, 98)
                    """,
                    (now, today),
                )
            coordinator = RefreshCoordinator(project, reports)

            payload = coordinator.status_payload()
            layers = payload["layers"]

        self.assertEqual(layers["full_market"]["status"], "success")
        self.assertEqual(layers["full_market"]["symbols_count"], 1)
        self.assertEqual(layers["full_market"]["error"], "inferred_from_latest_snapshot")
        self.assertIsNotNone(layers["full_market"]["next_due_at"])
        self.assertIsNotNone(layers["full_market"]["seconds_until_stale"])
        self.assertEqual(layers["watchlist"]["status"], "success")
        self.assertEqual(layers["watchlist"]["symbols_count"], 1)
        self.assertIsNotNone(layers["watchlist"]["next_due_at"])

    def test_pre_open_status_ignores_optional_position_staleness(self):
        now = datetime(2026, 6, 25, 8, 50, tzinfo=ZoneInfo("Asia/Taipei"))
        captured = "2026-06-25T08:48:00+08:00"
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            reports = project / "reports"
            with connect(default_db_path(project)) as conn:
                conn.execute(
                    """
                    INSERT INTO tw_full_market_snapshots (
                      captured_at, date, symbol, name, price, change_pct, volume,
                      turnover, volume_ratio, vwap, above_vwap, break_prev_high,
                      break_5d_high, entered_candidate_pool, entered_ai_candidates,
                      ai_grade, entry_status, trade_bias, data_status, created_at
                    ) VALUES (?, '2026-06-25', '2330.TW', '台積電', 100, 1, 1000,
                      100000, 1.2, 99.5, 1, 1, 1, 1, 1,
                      'A', 'wait_breakout', 'long', 'ok', ?)
                    """,
                    (captured, captured),
                )
                conn.execute(
                    """
                    INSERT INTO intraday_snapshots (
                      captured_at, date, symbol, last_price, volume, turnover, vwap,
                      above_vwap, volume_ratio, opening_range_high, opening_range_low
                    ) VALUES (?, '2026-06-25', '2330.TW', 100, 1000, 100000, 99.5, 1, 1.2, 101, 98)
                    """,
                    (captured,),
                )
            coordinator = RefreshCoordinator(project, reports)

            payload = coordinator.status_payload(now=now)

        self.assertEqual(payload["market_mode"], "pre_open_prepare")
        self.assertEqual(payload["required_refresh_layers"], ["full_market", "watchlist"])
        self.assertFalse(payload["any_stale"])
        self.assertIn("positions", payload["stale_layers"])
        self.assertNotIn("positions", payload["required_stale_layers"])
        self.assertEqual(payload["refresh_guidance"]["severity"], "ok")
        self.assertEqual(payload["refresh_guidance"]["action_label"], "不需手動更新")
        self.assertIn("開盤前準備模式", payload["refresh_guidance"]["summary"])
        self.assertEqual(payload["layers"]["full_market"]["next_due_at"], "2026-06-25T09:03:00+08:00")
        self.assertEqual(payload["layers"]["watchlist"]["next_due_at"], "2026-06-25T08:53:00+08:00")

    def test_intraday_stale_watchlist_guides_watchlist_refresh(self):
        now = datetime(2026, 6, 25, 9, 30, tzinfo=ZoneInfo("Asia/Taipei"))
        old = datetime(2026, 6, 25, 9, 20, tzinfo=ZoneInfo("Asia/Taipei"))
        fresh = datetime(2026, 6, 25, 9, 29, tzinfo=ZoneInfo("Asia/Taipei"))
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            reports = project / "reports"
            with connect(default_db_path(project)) as conn:
                conn.execute(
                    """
                    INSERT INTO intraday_snapshots (
                      captured_at, date, symbol, last_price, volume, turnover, vwap,
                      above_vwap, volume_ratio, opening_range_high, opening_range_low
                    ) VALUES (?, '2026-06-25', '2330.TW', 100, 1000, 100000, 99.5, 1, 1.2, 101, 98)
                    """,
                    (fresh.isoformat(timespec="seconds"),),
                )
                upsert_refresh_state(
                    conn,
                    layer="watchlist",
                    status="success",
                    stale_after_seconds=300,
                    started_at=old,
                    success_at=old,
                    symbols_count=1,
                )
                upsert_refresh_state(
                    conn,
                    layer="positions",
                    status="success",
                    stale_after_seconds=300,
                    started_at=fresh,
                    success_at=fresh,
                    symbols_count=1,
                )
            coordinator = RefreshCoordinator(project, reports)

            payload = coordinator.status_payload(now=now)

        self.assertEqual(payload["market_mode"], "intraday")
        self.assertIn("watchlist", payload["required_stale_layers"])
        self.assertTrue(payload["any_stale"])
        self.assertEqual(payload["refresh_guidance"]["severity"], "block")
        self.assertEqual(payload["refresh_guidance"]["action_endpoint"], "/refresh_watchlist")
        self.assertFalse(payload["refresh_guidance"]["can_use_dashboard"])
        self.assertFalse(payload["allow_strong_long"])
        self.assertFalse(payload["can_show_any_strong_long"])
        self.assertEqual(payload["refresh_operation_summary"]["severity"], "block")
        self.assertIn("重點觀察", payload["refresh_operation_summary"]["message"])
        self.assertIn("重點觀察", payload["refresh_operation_summary"]["blocking_layer_labels"])

    def test_refresh_layers_skip_when_another_layer_is_running(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            reports = project / "reports"
            with connect(default_db_path(project)):
                pass
            coordinator = RefreshCoordinator(project, reports)
            coordinator._global_refresh_lock.acquire()
            try:
                result = coordinator.refresh_watchlist()
            finally:
                coordinator._global_refresh_lock.release()

            payload = coordinator.status_payload()

        self.assertEqual(result.status, "skipped")
        self.assertEqual(result.error, "another_refresh_running")
        self.assertIn("避免資料庫寫入衝突", result.message)
        self.assertEqual(payload["layers"]["watchlist"]["status"], "skipped")
        self.assertEqual(payload["layers"]["watchlist"]["error"], "another_refresh_running")

    def test_skipped_layer_keeps_fresh_previous_success_usable(self):
        now = datetime(2026, 6, 25, 9, 30, tzinfo=ZoneInfo("Asia/Taipei"))
        fresh = datetime(2026, 6, 25, 9, 29, tzinfo=ZoneInfo("Asia/Taipei"))
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            reports = project / "reports"
            with connect(default_db_path(project)) as conn:
                conn.execute(
                    """
                    INSERT INTO intraday_snapshots (
                      captured_at, date, symbol, last_price, volume, turnover, vwap,
                      above_vwap, volume_ratio, opening_range_high, opening_range_low
                    ) VALUES (?, '2026-06-25', '2330.TW', 100, 1000, 100000, 99.5, 1, 1.2, 101, 98)
                    """,
                    (fresh.isoformat(timespec="seconds"),),
                )
                upsert_refresh_state(
                    conn,
                    layer="watchlist",
                    status="success",
                    stale_after_seconds=300,
                    started_at=fresh,
                    success_at=fresh,
                    symbols_count=1,
                )
                upsert_refresh_state(
                    conn,
                    layer="positions",
                    status="success",
                    stale_after_seconds=300,
                    started_at=fresh,
                    success_at=fresh,
                    symbols_count=1,
                )
                upsert_refresh_state(
                    conn,
                    layer="watchlist",
                    status="skipped",
                    stale_after_seconds=300,
                    started_at=now,
                    error="another_refresh_running",
                )
            coordinator = RefreshCoordinator(project, reports)

            payload = coordinator.status_payload(now=now)

        self.assertEqual(payload["layers"]["watchlist"]["status"], "success")
        self.assertFalse(payload["layers"]["watchlist"]["is_stale"])
        self.assertEqual(payload["market_mode"], "intraday")
        self.assertTrue(payload["allow_strong_long"])
        self.assertEqual(payload["refresh_guidance"]["severity"], "ok")

    def test_layer_usable_fresh_success_accepts_skipped_or_running_with_previous_success(self):
        self.assertTrue(
            _layer_has_usable_fresh_success(
                {"status": "skipped", "last_success_at": "2026-06-25T09:29:00+08:00", "is_stale": False}
            )
        )
        self.assertTrue(
            _layer_has_usable_fresh_success(
                {"status": "running", "last_success_at": "2026-06-25T09:29:00+08:00", "is_stale": False}
            )
        )
        self.assertFalse(
            _layer_has_usable_fresh_success(
                {"status": "failed", "last_success_at": "2026-06-25T09:29:00+08:00", "is_stale": False}
            )
        )

    def test_refresh_operation_summary_blocks_stale_market_mode_even_when_layers_look_ok(self):
        layers = {
            "full_market": {"status": "success", "is_stale": False},
            "watchlist": {"status": "success", "is_stale": False},
            "positions": {"status": "success", "is_stale": False},
        }

        summary = _refresh_operation_summary(
            layers,
            required_layers=["full_market", "watchlist"],
            required_stale_layers=[],
            market_mode={"mode": "stale_data"},
        )

        self.assertEqual(summary["severity"], "block")
        self.assertFalse(summary["can_use_dashboard"])
        self.assertIn("資料異常", summary["message"])

    def test_refresh_operation_summary_reports_required_stuck_layer(self):
        layers = {
            "full_market": {"status": "stuck", "is_stale": True},
            "watchlist": {"status": "success", "is_stale": False},
            "positions": {"status": "success", "is_stale": False},
        }

        summary = _refresh_operation_summary(
            layers,
            required_layers=["full_market"],
            required_stale_layers=["full_market"],
            market_mode={"mode": "post_close_review"},
        )

        self.assertEqual(summary["severity"], "block")
        self.assertFalse(summary["can_use_dashboard"])
        self.assertEqual(summary["required_stuck_layers"], ["full_market"])
        self.assertEqual(summary["stuck_layer_labels"], ["全市場掃描"])
        self.assertIn("刷新可能卡住", summary["message"])

    def test_strong_long_status_requires_fresh_required_layers_and_usable_price_quality(self):
        market_mode = {"mode": "intraday", "allow_strong_long": True}
        price_status = {"live_count": 10, "can_show_any_strong_long": True}

        self.assertTrue(
            _status_allows_strong_long(
                market_mode=market_mode,
                price_status=price_status,
                required_stale_layers=[],
            )
        )
        self.assertFalse(
            _status_allows_strong_long(
                market_mode=market_mode,
                price_status=price_status,
                required_stale_layers=["watchlist"],
            )
        )
        self.assertFalse(
            _status_allows_strong_long(
                market_mode=market_mode,
                price_status={"live_count": 10, "can_show_any_strong_long": False},
                required_stale_layers=[],
            )
        )


if __name__ == "__main__":
    unittest.main()
