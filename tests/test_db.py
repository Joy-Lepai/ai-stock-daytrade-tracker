from datetime import datetime, timedelta
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest

from stock_daytrade_system.data import Bar
from stock_daytrade_system.db import (
    backtest_summary,
    connect,
    latest_candidates,
    latest_us_candidates,
    save_long_candidates,
    save_us_candidates,
    save_us_symbols,
    update_backtests,
)
from stock_daytrade_system.long_model import LongCandidate
from stock_daytrade_system.us_long_model import USLongCandidate
from stock_daytrade_system.us_symbols import us_symbol_rows


def candidate(volume_ratio=1.5, grade="A", entry_status="executable"):
    return LongCandidate(
        symbol="2330.TW",
        name="台積電",
        sector="semiconductor",
        last_price=100,
        change_pct=2,
        volume=2_000_000,
        turnover=200_000_000,
        avg_volume_20=1_000_000,
        daily_volume_ratio=2,
        intraday_volume=200_000,
        volume_ratio=volume_ratio,
        vwap=99,
        above_vwap=True,
        previous_high=98,
        high_5d=99,
        high_10d=99,
        break_prev_high=True,
        break_5d_high=True,
        break_10d_high=True,
        upper_shadow_pct=0,
        institutional_buy_million=None,
        margin_balance=None,
        short_balance=None,
        daytrade_ratio=None,
        sector_strength=3,
        news_topics=[],
        market_state="偏多",
        bullish_score=85,
        risk_score=20,
        grade=grade,
        entry_status=entry_status,
        original_entry_status=entry_status,
        adjusted_entry_status=entry_status,
        confidence_score=82,
        confidence_level="high",
        confidence_level_label="高信心",
        conflicts_count=0,
        conflicts=[],
        conflict_summary="無明顯衝突",
        confidence_summary="此訊號信心偏高。",
        confidence_adjustment_reason="",
        trade_bias="long" if entry_status == "executable" else "watch",
        trade_bias_label="買多" if entry_status == "executable" else "觀察",
        trade_bias_reason="測試資料",
        trigger_price=99,
        stop_loss=97,
        target_price=102,
        opening_range_high=None,
        opening_range_low=None,
        reasons=["站上VWAP"],
        risk_reasons=[],
    )


def bar(index, high, low, close):
    return Bar(
        timestamp=datetime(2026, 1, 1, 9, 5) + timedelta(minutes=index * 5),
        open=100,
        high=high,
        low=low,
        close=close,
        volume=10_000,
    )


def us_candidate(entry_status="wait_volume", grade="B"):
    return USLongCandidate(
        symbol="NVDA",
        name_en="NVIDIA Corporation",
        name_zh="輝達",
        short_name_zh="輝達",
        sector_en="Semiconductors",
        sector_zh="半導體",
        industry_en="AI Accelerators",
        industry_zh="AI 晶片",
        description_zh="AI 晶片、GPU 與資料中心加速運算龍頭",
        latest_price=101,
        previous_close=99,
        open=100,
        high=102,
        low=99,
        volume=10_000_000,
        change_pct=2,
        volume_ratio=1.1,
        vwap=100,
        above_vwap=True,
        premarket_high=100.5,
        break_premarket_high=True,
        break_previous_high=True,
        break_opening_range_high=True,
        opening_range_high=100.8,
        bullish_score=70,
        risk_score=20,
        grade=grade,
        entry_status=entry_status,
        original_entry_status=entry_status,
        adjusted_entry_status=entry_status,
        confidence_score=75,
        confidence_level="medium",
        confidence_level_label="中等信心",
        conflicts_count=0,
        conflicts=[],
        conflict_summary="無明顯衝突",
        confidence_summary="此訊號為中等信心。",
        confidence_adjustment_reason="",
        trade_bias="watch",
        trade_bias_label="觀察",
        trade_bias_reason="測試資料",
        lifecycle_status="observed",
        trigger_price=101,
        stop_loss=99,
        target_price=104,
        reasons=["站上 VWAP"],
        risk_reasons=[],
        market_status="neutral",
    )


class DatabaseTests(unittest.TestCase):
    def test_saves_candidates_and_updates_backtest(self):
        with tempfile.TemporaryDirectory() as directory:
            with connect(Path(directory) / "daytrade.db") as conn:
                save_long_candidates(conn, datetime(2026, 1, 1, 9, 5), [candidate()])
                update_backtests(
                    conn,
                    datetime(2026, 1, 1, 9, 20),
                    {"2330.TW": [bar(0, 101, 99, 100), bar(1, 103, 100, 102)]},
                )

                rows = latest_candidates(conn)
                summary = backtest_summary(conn)
                backtest_row = conn.execute("SELECT * FROM backtest_results WHERE symbol = ?", ("2330.TW",)).fetchone()

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["grade"], "A")
        self.assertEqual(summary["recommendation_count"], 1)
        self.assertEqual(summary["trackable_count"], 1)
        self.assertEqual(summary["target"], 1)
        self.assertEqual(summary["by_entry_status"][0]["entry_status"], "executable")
        self.assertEqual(summary["by_entry_status"][0]["trackable"], 1)
        self.assertEqual(summary["by_signal_type"][0]["signal_type"], "breakout")
        self.assertEqual(summary["by_signal_type"][0]["triggered"], 1)
        self.assertEqual(backtest_row["same_day_high"], 103)
        self.assertEqual(backtest_row["same_day_close"], 102)
        self.assertEqual(backtest_row["signal_type"], "breakout")
        self.assertEqual(backtest_row["hit_stop_loss"], 0)
        self.assertGreater(backtest_row["max_gain_after_recommend"], 0)

    def test_latest_candidates_keeps_model_grade_without_sql_downgrade(self):
        with tempfile.TemporaryDirectory() as directory:
            with connect(Path(directory) / "daytrade.db") as conn:
                save_long_candidates(conn, datetime(2026, 1, 1, 9, 5), [candidate(volume_ratio=0.6)])
                rows = latest_candidates(conn)

        self.assertEqual(rows[0]["grade"], "A")

    def test_writes_a_b_plus_and_b_to_recommendations(self):
        with tempfile.TemporaryDirectory() as directory:
            with connect(Path(directory) / "daytrade.db") as conn:
                save_long_candidates(
                    conn,
                    datetime(2026, 1, 1, 9, 5),
                    [
                        candidate(grade="A"),
                        replace(candidate(grade="B+", entry_status="wait_pullback"), symbol="2303.TW", name="聯電"),
                        replace(candidate(grade="B", entry_status="wait_volume"), symbol="2317.TW", name="鴻海"),
                        replace(candidate(grade="C", entry_status="high_risk"), symbol="6919.TW", name="康霈生技"),
                    ],
                )
                rows = conn.execute(
                    "SELECT symbol, grade, entry_status FROM recommendations ORDER BY symbol"
                ).fetchall()

        self.assertEqual([(row["symbol"], row["grade"], row["entry_status"]) for row in rows], [
            ("2303.TW", "B+", "wait_pullback"),
            ("2317.TW", "B", "wait_volume"),
            ("2330.TW", "A", "executable"),
        ])

    def test_recommendations_store_signal_type_for_setup_backtests(self):
        with tempfile.TemporaryDirectory() as directory:
            with connect(Path(directory) / "daytrade.db") as conn:
                save_long_candidates(
                    conn,
                    datetime(2026, 1, 1, 9, 5),
                    [
                        candidate(grade="A", entry_status="executable"),
                        replace(candidate(grade="B+", entry_status="practice_long"), symbol="2303.TW", name="聯電", change_pct=4),
                        replace(candidate(grade="B", entry_status="wait_pullback"), symbol="2317.TW", name="鴻海"),
                    ],
                )
                rows = conn.execute(
                    "SELECT symbol, signal_type FROM recommendations ORDER BY symbol"
                ).fetchall()

        self.assertEqual(
            [(row["symbol"], row["signal_type"]) for row in rows],
            [
                ("2303.TW", "continuation"),
                ("2317.TW", "vwap_pullback"),
                ("2330.TW", "breakout"),
            ],
        )

    def test_wait_vwap_triggers_lifecycle_without_losing_entry_status(self):
        wait_candidate = replace(
            candidate(grade="B", entry_status="wait_vwap"),
            last_price=100.5,
            vwap=100,
            above_vwap=True,
            target_price=102,
            stop_loss=99,
        )
        with tempfile.TemporaryDirectory() as directory:
            with connect(Path(directory) / "daytrade.db") as conn:
                save_long_candidates(conn, datetime(2026, 1, 1, 9, 10), [wait_candidate])
                update_backtests(
                    conn,
                    datetime(2026, 1, 1, 9, 15),
                    {"2330.TW": [bar(1, 101, 100, 100.5), bar(2, 102.5, 100.5, 102)]},
                )
                rec = conn.execute("SELECT * FROM recommendations WHERE symbol = ?", ("2330.TW",)).fetchone()
                result = conn.execute("SELECT * FROM backtest_results WHERE symbol = ?", ("2330.TW",)).fetchone()
                summary = backtest_summary(conn, datetime(2026, 1, 1).date())

        self.assertEqual(rec["lifecycle_status"], "hit_target")
        self.assertEqual(rec["entry_status"], "wait_vwap")
        self.assertEqual(rec["trigger_reason"], "站回VWAP")
        self.assertIsNotNone(result["trigger_time"])
        self.assertEqual(result["entry_status"], "wait_vwap")
        self.assertEqual(summary["trackable_count"], 1)
        self.assertEqual(summary["by_entry_status"][0]["entry_status"], "wait_vwap")
        self.assertEqual(summary["by_entry_status"][0]["trackable"], 1)

    def test_wait_volume_triggers_when_volume_ratio_reaches_one(self):
        wait_candidate = replace(
            candidate(grade="B", entry_status="wait_volume"),
            volume_ratio=1.05,
            target_price=102,
            stop_loss=99,
        )
        with tempfile.TemporaryDirectory() as directory:
            with connect(Path(directory) / "daytrade.db") as conn:
                save_long_candidates(conn, datetime(2026, 1, 1, 9, 10), [wait_candidate])
                update_backtests(
                    conn,
                    datetime(2026, 1, 1, 9, 15),
                    {"2330.TW": [bar(1, 101, 100, 100.5), bar(2, 101.5, 100.2, 101)]},
                )
                rec = conn.execute("SELECT * FROM recommendations WHERE symbol = ?", ("2330.TW",)).fetchone()
                result = conn.execute("SELECT * FROM backtest_results WHERE symbol = ?", ("2330.TW",)).fetchone()

        self.assertEqual(rec["lifecycle_status"], "triggered")
        self.assertEqual(rec["entry_status"], "wait_volume")
        self.assertIn("量比放大", rec["trigger_reason"])
        self.assertEqual(result["entry_status"], "wait_volume")
        self.assertEqual(result["expired_without_trigger"], 0)

    def test_observed_recommendation_expires_after_close_without_trigger(self):
        wait_candidate = replace(
            candidate(grade="B", entry_status="wait_vwap"),
            last_price=98,
            vwap=100,
            above_vwap=False,
        )
        with tempfile.TemporaryDirectory() as directory:
            with connect(Path(directory) / "daytrade.db") as conn:
                save_long_candidates(conn, datetime(2026, 1, 1, 9, 10), [wait_candidate])
                update_backtests(
                    conn,
                    datetime(2026, 1, 1, 14, 0),
                    {"2330.TW": [bar(40, 99, 97, 98)]},
                )
                rec = conn.execute("SELECT * FROM recommendations WHERE symbol = ?", ("2330.TW",)).fetchone()
                result = conn.execute("SELECT * FROM backtest_results WHERE symbol = ?", ("2330.TW",)).fetchone()
                summary = backtest_summary(conn, datetime(2026, 1, 1).date())

        self.assertEqual(rec["lifecycle_status"], "expired")
        self.assertIsNotNone(rec["expired_at"])
        self.assertEqual(result["expired_without_trigger"], 1)
        self.assertEqual(summary["trackable_count"], 0)
        self.assertEqual(summary["expired_count"], 1)

    def test_us_recommendations_are_separated_by_market(self):
        with tempfile.TemporaryDirectory() as directory:
            with connect(Path(directory) / "daytrade.db") as conn:
                save_long_candidates(conn, datetime(2026, 1, 1, 9, 5), [candidate()])
                save_us_symbols(conn, us_symbol_rows(datetime(2026, 1, 1, 10, 0)))
                save_us_candidates(conn, datetime(2026, 1, 1, 10, 0), [us_candidate()], "regular")

                tw_summary = backtest_summary(conn, datetime(2026, 1, 1).date(), market="TW")
                us_summary = backtest_summary(conn, datetime(2026, 1, 1).date(), market="US")
                us_rows = latest_us_candidates(conn)

        self.assertEqual(tw_summary["recommendation_count"], 1)
        self.assertEqual(us_summary["recommendation_count"], 1)
        self.assertEqual(us_summary["by_entry_status"][0]["entry_status"], "wait_volume")
        self.assertEqual(us_rows[0]["name_zh"], "輝達")


if __name__ == "__main__":
    unittest.main()
