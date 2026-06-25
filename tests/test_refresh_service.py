import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from stock_daytrade_system.db import connect, default_db_path, upsert_last_known_price, upsert_refresh_state
from stock_daytrade_system.refresh_service import (
    DEFAULT_TRACKER_TIMEOUT_SECONDS,
    RefreshCoordinator,
    _layer_has_usable_fresh_success,
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
            self.assertIn(payload["operational_health"]["status"], {"ok", "warning", "blocked"})

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
