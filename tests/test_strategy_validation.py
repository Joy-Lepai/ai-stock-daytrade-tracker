import sqlite3
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from stock_daytrade_system.data import Bar
from stock_daytrade_system.db import connect, default_db_path, save_tw_full_market_snapshots
from stock_daytrade_system.strategy_validation import (
    build_missed_rate_report,
    build_model_observations,
    build_strategy_scorecard,
    update_tw_scan_result_verification,
)


class StrategyValidationTests(unittest.TestCase):
    def test_updates_post_scan_verification_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            conn = connect(default_db_path(Path(directory)))
            captured = datetime(2026, 6, 18, 9, 10)
            save_tw_full_market_snapshots(
                conn,
                captured,
                [
                    {
                        "symbol": "2330.TW",
                        "name": "台積電",
                        "latest_price": 100,
                        "change_pct": 3.5,
                        "volume": 5_000_000,
                        "turnover": 500_000_000,
                        "volume_ratio": 1.2,
                        "vwap": 99,
                        "above_vwap": True,
                        "break_prev_high": True,
                        "break_5d_high": True,
                        "source_reasons": ["成交金額前段"],
                        "ai_grade": "A",
                        "entry_status": "executable",
                        "trade_bias": "long",
                        "reason_code": "selected",
                    }
                ],
            )
            bars = [
                Bar(datetime(2026, 6, 18, 9, 15), 100, 102.2, 99.8, 101, 1000),
                Bar(datetime(2026, 6, 18, 9, 20), 101, 103, 100.5, 102, 1000),
            ]

            result = update_tw_scan_result_verification(conn, captured, {"2330.TW": bars})
            row = conn.execute("SELECT * FROM tw_full_market_snapshots WHERE symbol = '2330.TW'").fetchone()

            self.assertEqual(result["verified"], 1)
            self.assertEqual(row["post_scan_high"], 103)
            self.assertEqual(row["hit_2_pct"], 1)
            self.assertEqual(row["verification_outcome"], "達到2%目標")

    def test_scorecard_and_missed_rate_are_empty_safe(self):
        with tempfile.TemporaryDirectory() as directory:
            conn = connect(default_db_path(Path(directory)))

            scorecard = build_strategy_scorecard(conn)
            missed = build_missed_rate_report(conn)
            notes = build_model_observations(scorecard, missed)

            self.assertEqual(scorecard["available_trade_days"], 0)
            self.assertEqual(missed["missed_count"], 0)
            self.assertTrue(notes)

    def test_missed_rate_counts_true_strength_not_seen_by_model(self):
        with tempfile.TemporaryDirectory() as directory:
            conn = connect(default_db_path(Path(directory)))
            captured = datetime(2026, 6, 18, 13, 40)
            save_tw_full_market_snapshots(
                conn,
                captured,
                [
                    {
                        "symbol": "8936.TWO",
                        "name": "國統",
                        "latest_price": 58.1,
                        "change_pct": 5.44,
                        "volume": 8_000_000,
                        "turnover": 500_000_000,
                        "volume_ratio": None,
                        "vwap": None,
                        "source_reasons": ["今日漲幅大於3%"],
                        "ai_grade": "D",
                        "entry_status": "wait_volume",
                        "trade_bias": "watch",
                        "reason_code": "volume_ratio_missing",
                    }
                ],
            )

            missed = build_missed_rate_report(conn)

            self.assertEqual(missed["strong_stock_count"], 1)
            self.assertEqual(missed["missed_count"], 1)
            self.assertEqual(missed["missed_examples"][0]["reason_code"], "volume_ratio_missing")


if __name__ == "__main__":
    unittest.main()
