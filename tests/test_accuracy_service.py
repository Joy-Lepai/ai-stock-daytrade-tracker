from pathlib import Path
import tempfile
import unittest

from stock_daytrade_system.accuracy_service import (
    build_accuracy_dashboard_payload,
    build_accuracy_group_payload,
)
from stock_daytrade_system.db import connect


class AccuracyServiceTests(unittest.TestCase):
    def test_empty_accuracy_payload_returns_sample_warning(self):
        with tempfile.TemporaryDirectory() as directory:
            with connect(Path(directory) / "daytrade.db") as conn:
                payload = build_accuracy_dashboard_payload(conn)

        self.assertEqual(payload["api_status"], "ok")
        self.assertEqual(payload["summary"]["sample_size"], 0)
        self.assertEqual(payload["summary"]["sample_quality"], "insufficient")
        self.assertFalse(payload["summary"]["is_statistically_meaningful"])
        self.assertIn("樣本數不足", payload["summary"]["message"])
        self.assertEqual(payload["data_completeness"]["sample_size"], 0)
        self.assertFalse(payload["data_completeness"]["ready_for_model_tuning"])
        self.assertIn("逐筆成交 Tick 尚未接入", payload["data_completeness"]["missing_items"])
        self.assertEqual(payload["by_status"], [])

    def test_high_confidence_backtest_is_counted(self):
        with tempfile.TemporaryDirectory() as directory:
            with connect(Path(directory) / "daytrade.db") as conn:
                conn.execute(
                    """
                    INSERT INTO recommendations (
                      market, date, symbol, first_seen_at, latest_seen_at, grade,
                      bullish_score, risk_score, entry_status, lifecycle_status,
                      confidence_score, confidence_level, conflicts
                    )
                    VALUES ('TW', '2026-01-01', '2330.TW', '2026-01-01T09:05:00',
                      '2026-01-01T09:10:00', 'A', 85, 20, 'executable', 'closed',
                      88, 'high', '[]')
                    """
                )
                conn.execute(
                    """
                    INSERT INTO backtest_results (
                      market, date, symbol, entry_status, return_pct,
                      max_drawdown_after_trigger, hit_target, hit_stop, outcome,
                      confidence_score, confidence_level
                    )
                    VALUES ('TW', '2026-01-01', '2330.TW', 'executable', 2.5,
                      -0.4, 1, 0, '達標', 88, 'high')
                    """
                )
                payload = build_accuracy_dashboard_payload(conn)
                by_confidence = build_accuracy_group_payload(conn, "confidence_level")

        self.assertEqual(payload["summary"]["sample_size"], 1)
        self.assertEqual(payload["summary"]["sample_quality"], "insufficient")
        self.assertEqual(payload["summary"]["high_confidence"]["sample_size"], 1)
        self.assertEqual(payload["summary"]["win_rate"], 100)
        self.assertFalse(payload["summary"]["is_statistically_meaningful"])
        self.assertEqual(by_confidence["rows"][0]["group"], "high")
        self.assertEqual(by_confidence["rows"][0]["sample_quality"], "insufficient")
        self.assertEqual(by_confidence["rows"][0]["win_rate"], 100)

    def test_b_plus_accuracy_is_counted_separately(self):
        with tempfile.TemporaryDirectory() as directory:
            with connect(Path(directory) / "daytrade.db") as conn:
                conn.execute(
                    """
                    INSERT INTO recommendations (
                      market, date, symbol, first_seen_at, latest_seen_at, grade,
                      bullish_score, risk_score, entry_status, lifecycle_status,
                      confidence_score, confidence_level, conflicts
                    )
                    VALUES ('TW', '2026-01-01', '2317.TW', '2026-01-01T09:05:00',
                      '2026-01-01T09:10:00', 'B+', 72, 30, 'wait_pullback', 'hit_target',
                      65, 'medium', '[]')
                    """
                )
                conn.execute(
                    """
                    INSERT INTO backtest_results (
                      market, date, symbol, lifecycle_status, entry_status, return_pct,
                      max_gain_after_trigger, max_drawdown_after_trigger, hit_target,
                      hit_stop, outcome, confidence_score, confidence_level
                    )
                    VALUES ('TW', '2026-01-01', '2317.TW', 'hit_target', 'wait_pullback',
                      1.8, 2.4, -0.5, 1, 0, '達標', 65, 'medium')
                    """
                )
                payload = build_accuracy_dashboard_payload(conn)

        self.assertEqual(payload["summary"]["grade_b_plus"]["sample_size"], 1)
        self.assertEqual(payload["summary"]["grade_b_plus"]["win_rate"], 100)
        self.assertEqual(payload["summary"]["grade_b_plus_triggered"]["sample_size"], 1)
        self.assertEqual(payload["by_grade"][0]["group"], "B+")
        self.assertEqual(payload["by_grade"][0]["avg_max_gain_pct"], 2.4)

    def test_paper_trade_samples_are_counted_by_market(self):
        with tempfile.TemporaryDirectory() as directory:
            with connect(Path(directory) / "daytrade.db") as conn:
                conn.execute(
                    """
                    INSERT INTO paper_accounts (
                      id, market, currency, initial_cash, cash_balance, equity,
                      realized_pnl, unrealized_pnl, max_drawdown, created_at, updated_at
                    )
                    VALUES ('US-paper', 'US', 'USD', 30000, 30000, 30000, 0, 0, 0,
                      '2026-01-01T09:00:00', '2026-01-01T09:00:00')
                    """
                )
                conn.execute(
                    """
                    INSERT INTO paper_trades (
                      id, account_id, recommendation_id, market, symbol, side, status,
                      grade, entry_status, lifecycle_status, entry_time, entry_price,
                      quantity, position_value, stop_loss, target_price, realized_pnl,
                      realized_pnl_pct, max_adverse_excursion, created_at, updated_at
                    )
                    VALUES ('trade-1', 'US-paper', 'manual-1', 'US', 'NVDA', 'buy',
                      'closed', 'A', 'executable', 'triggered', '2026-01-01T09:30:00',
                      100, 1, 100, 98, 104, -1, -1, -0.8,
                      '2026-01-01T09:30:00', '2026-01-01T10:00:00')
                    """
                )
                payload = build_accuracy_group_payload(conn, "market")

        self.assertEqual(payload["rows"][0]["group"], "US")
        self.assertEqual(payload["rows"][0]["sample_size"], 1)
        self.assertEqual(payload["rows"][0]["win_rate"], 0)

    def test_paper_trade_review_tags_are_counted_for_loss_distribution(self):
        with tempfile.TemporaryDirectory() as directory:
            with connect(Path(directory) / "daytrade.db") as conn:
                conn.execute(
                    """
                    INSERT INTO paper_accounts (
                      id, market, currency, initial_cash, cash_balance, equity,
                      realized_pnl, unrealized_pnl, max_drawdown, created_at, updated_at
                    )
                    VALUES ('US-paper', 'US', 'USD', 30000, 30000, 30000, 0, 0, 0,
                      '2026-01-01T09:00:00', '2026-01-01T09:00:00')
                    """
                )
                conn.execute(
                    """
                    INSERT INTO paper_trades (
                      id, account_id, recommendation_id, market, symbol, side, status,
                      grade, entry_status, lifecycle_status, entry_time, entry_price,
                      quantity, position_value, stop_loss, target_price, realized_pnl,
                      realized_pnl_pct, max_adverse_excursion, review_tags, reviewed_at,
                      created_at, updated_at
                    )
                    VALUES ('trade-1', 'US-paper', 'manual-1', 'US', 'NVDA', 'buy',
                      'closed', 'manual', 'manual', 'manual', '2026-01-01T09:30:00',
                      100, 1, 100, 98, 104, -2, -2, -1.2,
                      '["fomo", "hold_loser"]', '2026-01-01T10:00:00',
                      '2026-01-01T09:30:00', '2026-01-01T10:00:00')
                    """
                )
                payload = build_accuracy_dashboard_payload(conn)

        rows = payload["review_tag_loss_distribution"]["rows"]
        self.assertEqual(payload["review_tag_loss_distribution"]["sample_size"], 1)
        self.assertEqual({row["code"]: row["count"] for row in rows}, {"fomo": 1, "hold_loser": 1})


if __name__ == "__main__":
    unittest.main()
