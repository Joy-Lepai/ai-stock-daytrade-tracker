from datetime import datetime
from pathlib import Path
import tempfile
import unittest
from zoneinfo import ZoneInfo

from stock_daytrade_system.b_plus_trigger_tracker import (
    build_b_plus_trigger_tracker,
    evaluate_b_plus_trigger,
    update_ready_b_plus_triggers,
)
from stock_daytrade_system.db import connect
from stock_daytrade_system.paper_broker import run_paper_trading


NOW = datetime(2026, 1, 5, 10, 0, tzinfo=ZoneInfo("Asia/Taipei"))


def insert_tw_price(conn, price=100, vwap=100.1, volume_ratio=0.8):
    conn.execute(
        "INSERT OR REPLACE INTO symbols (symbol, name, sector, market, is_active) VALUES ('2884.TW', '玉山金', 'financial', 'TW', 1)"
    )
    conn.execute(
        """
        INSERT OR REPLACE INTO intraday_snapshots (
          captured_at, date, symbol, last_price, volume, turnover, vwap, above_vwap,
          volume_ratio, opening_range_high, opening_range_low
        )
        VALUES (?, '2026-01-05', '2884.TW', ?, 1000, 100000, ?, ?, ?, 101, 99)
        """,
        (NOW.isoformat(timespec="seconds"), price, vwap, int(price >= vwap), volume_ratio),
    )


def insert_b_plus_recommendation(conn, entry_status="wait_vwap", lifecycle="observed", stop_loss=98):
    conn.execute(
        """
        INSERT OR REPLACE INTO recommendations (
          market, date, symbol, first_seen_at, latest_seen_at, grade,
          bullish_score, risk_score, entry_status, lifecycle_status, observed_at,
          trigger_price, stop_loss, target_price, signal_price, confidence_score,
          confidence_level, confidence_summary
        )
        VALUES ('TW', '2026-01-05', '2884.TW', ?, ?, 'B+', 72, 20, ?, ?, ?,
          100, ?, 104, 100, 65, 'medium', 'B+ 測試訊號')
        """,
        (
            NOW.isoformat(timespec="seconds"),
            NOW.isoformat(timespec="seconds"),
            entry_status,
            lifecycle,
            NOW.isoformat(timespec="seconds"),
            stop_loss,
        ),
    )


class BPlusTriggerTrackerTests(unittest.TestCase):
    def test_wait_vwap_trigger_condition_and_distance(self):
        item = evaluate_b_plus_trigger(
            market="TW",
            symbol="2884.TW",
            name_zh="玉山金",
            current_price=34.32,
            vwap=34.35,
            volume_ratio=0.82,
            entry_status="wait_vwap",
            lifecycle_status="observed",
            trigger_price=34.35,
        )

        self.assertEqual(item["trigger_condition"], "站回 VWAP 後觸發")
        self.assertEqual(item["trigger_readiness"], "near")
        self.assertIn("差 0.03 元站回 VWAP", item["distance_to_trigger"])

    def test_wait_volume_trigger_condition_and_ready(self):
        item = evaluate_b_plus_trigger(
            market="TW",
            symbol="2884.TW",
            name_zh="玉山金",
            current_price=34.5,
            vwap=34.3,
            volume_ratio=0.8,
            entry_status="wait_volume",
            lifecycle_status="observed",
            trigger_price=34.5,
        )

        self.assertEqual(item["trigger_condition"], "量比放大後觸發")
        self.assertEqual(item["trigger_readiness"], "ready")
        self.assertIn("量比已達", item["distance_to_trigger"])

    def test_wait_breakout_trigger_condition_and_waiting(self):
        item = evaluate_b_plus_trigger(
            market="TW",
            symbol="2884.TW",
            name_zh="玉山金",
            current_price=99,
            vwap=98.5,
            volume_ratio=0.9,
            entry_status="wait_breakout",
            lifecycle_status="observed",
            trigger_price=100,
        )

        self.assertEqual(item["trigger_condition"], "突破指定價位後觸發")
        self.assertEqual(item["trigger_readiness"], "waiting")
        self.assertIn("突破觸發價", item["distance_to_trigger"])

    def test_blocked_status_does_not_trigger(self):
        item = evaluate_b_plus_trigger(
            market="TW",
            symbol="2884.TW",
            name_zh="玉山金",
            current_price=101,
            vwap=100,
            volume_ratio=1.0,
            entry_status="high_risk",
            lifecycle_status="observed",
            trigger_price=100,
        )

        self.assertEqual(item["trigger_readiness"], "blocked")
        self.assertIn("不觸發", item["trigger_next_action"])

    def test_ready_b_plus_updates_lifecycle_to_triggered(self):
        with tempfile.TemporaryDirectory() as directory:
            with connect(Path(directory) / "db.sqlite") as conn:
                insert_tw_price(conn, price=100.2, vwap=100, volume_ratio=0.85)
                insert_b_plus_recommendation(conn, entry_status="wait_vwap")

                updated = update_ready_b_plus_triggers(conn, NOW, market="TW")
                rec = conn.execute("SELECT lifecycle_status, trigger_time, trigger_price, trigger_reason FROM recommendations").fetchone()

        self.assertEqual(updated, 1)
        self.assertEqual(rec["lifecycle_status"], "triggered")
        self.assertIsNotNone(rec["trigger_time"])
        self.assertEqual(rec["trigger_price"], 100.2)
        self.assertEqual(rec["trigger_reason"], "B+站回VWAP")

    def test_observed_b_plus_does_not_open_paper_trade_until_ready(self):
        with tempfile.TemporaryDirectory() as directory:
            with connect(Path(directory) / "db.sqlite") as conn:
                insert_tw_price(conn, price=99.5, vwap=100, volume_ratio=0.85)
                insert_b_plus_recommendation(conn, entry_status="wait_vwap")

                summary = run_paper_trading(conn, NOW)
                trades = conn.execute("SELECT COUNT(*) AS total FROM paper_trades WHERE status = 'open'").fetchone()

        self.assertEqual(summary.opened, 0)
        self.assertEqual(trades["total"], 0)

    def test_ready_b_plus_triggers_then_opens_system_paper_trade_once(self):
        with tempfile.TemporaryDirectory() as directory:
            with connect(Path(directory) / "db.sqlite") as conn:
                insert_tw_price(conn, price=100.2, vwap=100, volume_ratio=0.85)
                insert_b_plus_recommendation(conn, entry_status="wait_vwap")

                first = run_paper_trading(conn, NOW)
                second = run_paper_trading(conn, NOW)
                open_trades = conn.execute("SELECT COUNT(*) AS total FROM paper_trades WHERE status = 'open'").fetchone()
                rec = conn.execute("SELECT lifecycle_status FROM recommendations").fetchone()

        self.assertEqual(first.opened, 1)
        self.assertEqual(second.opened, 0)
        self.assertEqual(open_trades["total"], 1)
        self.assertEqual(rec["lifecycle_status"], "triggered")

    def test_tracker_rows_include_readiness_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            with connect(Path(directory) / "db.sqlite") as conn:
                insert_tw_price(conn, price=100.2, vwap=100, volume_ratio=0.85)
                insert_b_plus_recommendation(conn, entry_status="wait_vwap")
                rows = build_b_plus_trigger_tracker(conn, market="TW", date_text="2026-01-05")

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["symbol"], "2884.TW")
        self.assertEqual(rows[0]["trigger_readiness"], "ready")
        self.assertEqual(rows[0]["trigger_next_action"], "可轉 triggered，等待系統下一次更新建立 paper trade")


if __name__ == "__main__":
    unittest.main()
