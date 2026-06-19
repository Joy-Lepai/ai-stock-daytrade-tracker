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
            self.assertIn("allow_strong_long", payload)
            self.assertIn("layers", payload)

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


if __name__ == "__main__":
    unittest.main()
