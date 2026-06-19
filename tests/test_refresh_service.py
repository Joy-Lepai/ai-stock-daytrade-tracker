import tempfile
import unittest
from pathlib import Path

from stock_daytrade_system.db import connect, default_db_path
from stock_daytrade_system.refresh_service import RefreshCoordinator
from stock_daytrade_system.resilience import GLOBAL_HEALTH, record_source_health


class RefreshServiceTests(unittest.TestCase):
    def tearDown(self):
        GLOBAL_HEALTH.reset()

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


if __name__ == "__main__":
    unittest.main()
