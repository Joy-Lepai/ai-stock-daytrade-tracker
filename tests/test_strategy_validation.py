import sqlite3
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from stock_daytrade_system.data import Bar
from stock_daytrade_system.db import connect, default_db_path, save_tw_full_market_snapshots
from stock_daytrade_system.strategy_validation import (
    build_breakout_trap_observations,
    build_breakout_trap_scorecard,
    build_entry_radar_scorecard,
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

    def test_after_midnight_snapshot_uses_quote_latest_at_for_verification_date(self):
        with tempfile.TemporaryDirectory() as directory:
            conn = connect(default_db_path(Path(directory)))
            captured = datetime(2026, 6, 19, 0, 30)
            save_tw_full_market_snapshots(
                conn,
                captured,
                [
                    {
                        "symbol": "2344.TW",
                        "name": "華邦電",
                        "latest_at": "2026-06-18T13:30:00+08:00",
                        "latest_price": 50,
                        "change_pct": 5.5,
                        "volume": 20_000_000,
                        "turnover": 1_000_000_000,
                        "volume_ratio": 1.4,
                        "vwap": 49,
                        "above_vwap": True,
                        "break_prev_high": True,
                        "break_5d_high": True,
                        "source_reasons": ["今日漲幅大於3%"],
                        "ai_grade": "B+",
                        "entry_status": "wait_breakout",
                        "trade_bias": "watch",
                        "reason_code": "full_market_detected",
                    }
                ],
            )
            bars = [
                Bar(datetime(2026, 6, 18, 13, 30), 50, 50.5, 49.8, 50.1, 1000),
                Bar(datetime(2026, 6, 18, 13, 35), 50.1, 51.2, 50.0, 51.0, 1000),
            ]

            result = update_tw_scan_result_verification(conn, captured, {"2344.TW": bars})
            row = conn.execute("SELECT * FROM tw_full_market_snapshots WHERE symbol = '2344.TW'").fetchone()

            self.assertEqual(row["date"], "2026-06-18")
            self.assertEqual(row["signal_at"], "2026-06-18T13:30:00+08:00")
            self.assertEqual(result["verified"], 1)
            self.assertEqual(row["post_scan_high"], 51.2)

    def test_post_close_verification_falls_back_to_latest_unverified_snapshot_for_date(self):
        with tempfile.TemporaryDirectory() as directory:
            conn = connect(default_db_path(Path(directory)))
            captured = datetime(2026, 6, 18, 9, 30)
            save_tw_full_market_snapshots(
                conn,
                captured,
                [
                    {
                        "symbol": "2303.TW",
                        "name": "聯電",
                        "latest_price": 50,
                        "change_pct": 3.8,
                        "volume": 8_000_000,
                        "turnover": 400_000_000,
                        "volume_ratio": 1.3,
                        "vwap": 49.5,
                        "above_vwap": True,
                        "break_prev_high": True,
                        "source_reasons": ["成交金額前段"],
                        "ai_grade": "B+",
                        "entry_status": "practice_long",
                        "trade_bias": "long",
                        "reason_code": "full_market_detected",
                    }
                ],
            )
            bars = [
                Bar(datetime(2026, 6, 18, 9, 35), 50, 50.8, 49.8, 50.5, 1000),
                Bar(datetime(2026, 6, 18, 13, 25), 50.5, 51.5, 50.2, 51.2, 1000),
            ]

            result = update_tw_scan_result_verification(conn, datetime(2026, 6, 18, 14, 5), {"2303.TW": bars})
            row = conn.execute("SELECT * FROM tw_full_market_snapshots WHERE symbol = '2303.TW'").fetchone()

            self.assertEqual(result["selection_mode"], "latest_unverified_for_date")
            self.assertEqual(result["verified"], 1)
            self.assertEqual(row["post_scan_high"], 51.5)

    def test_scorecard_and_missed_rate_are_empty_safe(self):
        with tempfile.TemporaryDirectory() as directory:
            conn = connect(default_db_path(Path(directory)))

            scorecard = build_strategy_scorecard(conn)
            missed = build_missed_rate_report(conn)
            notes = build_model_observations(scorecard, missed)

            self.assertEqual(scorecard["available_trade_days"], 0)
            self.assertEqual(scorecard["windows"]["20"]["sample_quality"], "insufficient")
            self.assertFalse(scorecard["windows"]["20"]["is_statistically_meaningful"])
            self.assertIn("trend_continuation", scorecard["windows"]["20"])
            self.assertIn("樣本不足", scorecard["windows"]["20"]["trend_continuation"]["message"])
            self.assertEqual(missed["missed_count"], 0)
            self.assertTrue(notes)

    def test_missed_rate_splits_seen_filtered_from_true_missed(self):
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
                    },
                    {
                        "symbol": "6770.TW",
                        "name": "力積電",
                        "latest_price": 18.5,
                        "change_pct": 4.2,
                        "volume": 6_000_000,
                        "turnover": 120_000_000,
                        "volume_ratio": 1.1,
                        "vwap": 18.2,
                        "source_reasons": [],
                        "ai_grade": "未入選",
                        "entry_status": "-",
                        "trade_bias": "watch",
                        "reason_code": "below_candidate_threshold",
                    }
                ],
            )

            missed = build_missed_rate_report(conn)

            self.assertEqual(missed["strong_stock_count"], 2)
            self.assertEqual(missed["missed_count"], 1)
            self.assertEqual(missed["missed_by_pool_count"], 1)
            self.assertEqual(missed["missed_examples"][0]["reason_code"], "below_candidate_threshold")
            self.assertEqual(missed["seen_but_filtered_count"], 1)
            self.assertEqual(missed["seen_but_filtered"]["by_status"]["wait_volume"], 1)

    def test_regret_after_close_counts_seen_filtered_that_later_rallies(self):
        with tempfile.TemporaryDirectory() as directory:
            conn = connect(default_db_path(Path(directory)))
            captured = datetime(2026, 6, 18, 9, 30)
            save_tw_full_market_snapshots(
                conn,
                captured,
                [
                    {
                        "symbol": "2323.TW",
                        "name": "中環",
                        "latest_price": 12.0,
                        "change_pct": 5.2,
                        "volume": 10_000_000,
                        "turnover": 120_000_000,
                        "volume_ratio": 3.0,
                        "vwap": 11.7,
                        "source_reasons": ["今日漲幅大於3%"],
                        "ai_grade": "C",
                        "entry_status": "high_risk",
                        "trade_bias": "watch",
                        "reason_code": "high_chase_risk",
                    }
                ],
            )
            conn.execute(
                """
                UPDATE tw_full_market_snapshots
                SET max_gain_after_scan = 2.4,
                    max_drawdown_after_scan = -0.6,
                    verification_outcome = '後續續漲'
                WHERE symbol = '2323.TW'
                """
            )

            missed = build_missed_rate_report(conn)

            self.assertEqual(missed["missed_by_pool_count"], 0)
            self.assertEqual(missed["seen_but_filtered_count"], 1)
            self.assertEqual(missed["regret_after_close"]["count"], 1)

    def test_entry_radar_scorecard_groups_blocker_reasons(self):
        with tempfile.TemporaryDirectory() as directory:
            conn = connect(default_db_path(Path(directory)))
            captured = datetime(2026, 6, 18, 9, 30)
            save_tw_full_market_snapshots(
                conn,
                captured,
                [
                    {
                        "symbol": "3711.TW",
                        "name": "日月光投控",
                        "latest_price": 150,
                        "change_pct": 3.4,
                        "volume": 8_000_000,
                        "turnover": 1_200_000_000,
                        "volume_ratio": 0.7,
                        "vwap": 149,
                        "source_reasons": ["成交金額前段"],
                        "ai_grade": "B",
                        "entry_status": "wait_volume",
                        "reason_code": "low_volume_ratio",
                    },
                    {
                        "symbol": "6919.TW",
                        "name": "康霈",
                        "latest_price": 109,
                        "change_pct": 9.9,
                        "volume": 12_000_000,
                        "turnover": 1_300_000_000,
                        "volume_ratio": 5.0,
                        "vwap": 106,
                        "source_reasons": ["接近漲停"],
                        "ai_grade": "C",
                        "entry_status": "high_risk",
                        "reason_code": "high_chase_risk",
                    },
                ],
            )
            conn.execute(
                """
                UPDATE tw_full_market_snapshots
                SET max_gain_after_scan = 1.2,
                    max_drawdown_after_scan = -0.4,
                    hit_1_pct = 1,
                    hit_2_pct = 0,
                    hit_stop_loss = 0,
                    hit_take_profit = 0
                WHERE symbol = '3711.TW'
                """
            )
            conn.execute(
                """
                UPDATE tw_full_market_snapshots
                SET max_gain_after_scan = 0.2,
                    max_drawdown_after_scan = -1.5,
                    hit_1_pct = 0,
                    hit_2_pct = 0,
                    hit_stop_loss = 1,
                    hit_take_profit = 0
                WHERE symbol = '6919.TW'
                """
            )

            scorecard = build_entry_radar_scorecard(conn)

            rows = scorecard["windows"]["20"]["rows"]
            by_code = {row["blocker_code"]: row for row in rows}
            self.assertEqual(scorecard["title"], "進場雷達成績單")
            self.assertEqual(by_code["low_volume_ratio"]["blocker_label"], "量比不足")
            self.assertEqual(by_code["low_volume_ratio"]["win_rate"], 100)
            self.assertEqual(by_code["high_chase_risk"]["blocker_label"], "追價風險高")
            self.assertEqual(by_code["high_chase_risk"]["pullback_rate"], 100)

    def test_breakout_trap_diagnosis_is_saved_and_scorecard_groups_results(self):
        with tempfile.TemporaryDirectory() as directory:
            conn = connect(default_db_path(Path(directory)))
            captured = datetime(2026, 6, 18, 9, 30)
            save_tw_full_market_snapshots(
                conn,
                captured,
                [
                    {
                        "symbol": "2330.TW",
                        "name": "台積電",
                        "latest_price": 100,
                        "change_pct": 3.8,
                        "volume": 8_000_000,
                        "turnover": 800_000_000,
                        "volume_ratio": 1.3,
                        "vwap": 99,
                        "above_vwap": True,
                        "break_prev_high": True,
                        "source_reasons": ["成交金額前段"],
                        "ai_grade": "A",
                        "entry_status": "executable",
                        "reason_code": "selected",
                    },
                    {
                        "symbol": "6919.TW",
                        "name": "康霈",
                        "latest_price": 109,
                        "change_pct": 9.9,
                        "volume": 12_000_000,
                        "turnover": 1_300_000_000,
                        "volume_ratio": 5.0,
                        "vwap": 106,
                        "above_vwap": True,
                        "break_prev_high": True,
                        "source_reasons": ["接近漲停"],
                        "ai_grade": "C",
                        "entry_status": "high_risk",
                        "risk_score": 76,
                        "upper_shadow_pct": 2.5,
                        "reason_code": "high_chase_risk",
                    },
                ],
            )
            conn.execute(
                """
                UPDATE tw_full_market_snapshots
                SET max_gain_after_scan = 1.5,
                    max_drawdown_after_scan = -0.3,
                    hit_1_pct = 1,
                    hit_2_pct = 0,
                    hit_stop_loss = 0,
                    hit_take_profit = 0
                WHERE symbol = '2330.TW'
                """
            )
            conn.execute(
                """
                UPDATE tw_full_market_snapshots
                SET max_gain_after_scan = 0.2,
                    max_drawdown_after_scan = -1.4,
                    hit_1_pct = 0,
                    hit_2_pct = 0,
                    hit_stop_loss = 1,
                    hit_take_profit = 0
                WHERE symbol = '6919.TW'
                """
            )

            saved = conn.execute(
                "SELECT breakout_trap_status, breakout_trap_label FROM tw_full_market_snapshots WHERE symbol = '2330.TW'"
            ).fetchone()
            scorecard = build_breakout_trap_scorecard(conn)
            rows = scorecard["windows"]["20"]["rows"]
            by_status = {row["status"]: row for row in rows}

            self.assertEqual(saved["breakout_trap_status"], "true_breakout")
            self.assertEqual(saved["breakout_trap_label"], "真突破")
            self.assertEqual(by_status["true_breakout"]["target_1_rate"], 100)
            self.assertEqual(by_status["bull_trap_risk"]["pullback_rate"], 100)

    def test_breakout_trap_observations_are_sample_safe(self):
        with tempfile.TemporaryDirectory() as directory:
            conn = connect(default_db_path(Path(directory)))
            scorecard = build_breakout_trap_scorecard(conn)
            notes = build_breakout_trap_observations(scorecard)

            self.assertIn("樣本不足", notes[0])


if __name__ == "__main__":
    unittest.main()
