import tempfile
import unittest
from pathlib import Path

from stock_daytrade_system.db import connect, default_db_path
from stock_daytrade_system.system_status import build_system_version_payload


class SystemStatusTests(unittest.TestCase):
    def test_system_version_payload_reads_tracker_and_db_freshness(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            reports = project / "reports"
            reports.mkdir()
            commit = "abc123def456"
            (reports / "2026-06-19-tracker.html").write_text(
                f"""
                <html><body><details class="debug-block"><ul>
                <li><strong>app version / commit:</strong> {commit}</li>
                <li><strong>dashboard generated_at:</strong> 2026-06-19T09:05:00+08:00</li>
                </ul></details></body></html>
                """,
                encoding="utf-8",
            )
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

            payload = build_system_version_payload(project, reports)

            self.assertEqual(payload["api_status"], "ok")
            self.assertEqual(payload["tracker_html"]["commit"], commit)
            self.assertEqual(payload["tracker_html"]["dashboard_generated_at"], "2026-06-19T09:05:00+08:00")
            self.assertEqual(payload["db"]["data_date"], "2026-06-19")
            self.assertEqual(payload["db"]["intraday"]["symbols"], 1)
            self.assertIn("runtime_matches_tracker", payload["consistency"])


if __name__ == "__main__":
    unittest.main()
