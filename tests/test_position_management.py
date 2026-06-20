import tempfile
import unittest
from pathlib import Path

from stock_daytrade_system.db import connect
from stock_daytrade_system.position_management import build_position_command_center, position_action_for_symbol


class PositionManagementTests(unittest.TestCase):
    def test_position_command_center_calculates_pnl_and_blocks_averaging_down(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "daytrade.db"
            with connect(db_path) as conn:
                conn.execute(
                    """
                    INSERT INTO paper_trades (
                      id, account_id, recommendation_id, market, symbol, name_zh, name_en,
                      source, side, status, risk_mode, entry_time, entry_price, quantity,
                      position_value, stop_loss, target_price, created_at, updated_at
                    ) VALUES (
                      'trade-1', 'TW-paper', 'manual-1', 'TW', '2330.TW', '台積電', '',
                      'manual', 'buy', 'open', 'manual_only', '2026-06-19T09:05:00',
                      100, 10, 1000, 98, 104, '2026-06-19T09:05:00', '2026-06-19T09:05:00'
                    )
                    """
                )
                conn.execute(
                    """
                    INSERT INTO paper_positions (
                      id, account_id, trade_id, market, symbol, quantity, entry_price,
                      current_price, market_value, unrealized_pnl, unrealized_pnl_pct,
                      stop_loss, target_price, highest_price_since_entry, lowest_price_since_entry,
                      opened_at, updated_at
                    ) VALUES (
                      'pos-1', 'TW-paper', 'trade-1', 'TW', '2330.TW', 10, 100,
                      99, 990, -10, -1, 98, 104, 101, 99,
                      '2026-06-19T09:05:00', '2026-06-19T09:10:00'
                    )
                    """
                )
                conn.execute(
                    """
                    INSERT INTO intraday_snapshots (
                      captured_at, date, symbol, last_price, volume, turnover, vwap,
                      above_vwap, volume_ratio, opening_range_high, opening_range_low
                    ) VALUES (
                      '2026-06-19T09:10:00', '2026-06-19', '2330.TW', 99, 100000,
                      9900000, 100, 0, 1.2, 101, 98
                    )
                    """
                )

                payload = build_position_command_center(conn, market="TW")
                item = payload["positions"][0]

                self.assertEqual(payload["summary"]["positions_count"], 1)
                self.assertEqual(item["action"], "減碼")
                self.assertFalse(item["can_add"])
                self.assertEqual(item["institutional_label"], "籌碼資料不足")
                self.assertIn("sector_status_label", item)
                self.assertIn("sector_concentration_high", payload["summary"])
                self.assertTrue(any("不做攤平加碼" in text for text in item["add_forbidden_reasons"]))
                self.assertEqual(position_action_for_symbol(conn, "2330.TW", market="TW")["symbol"], "2330.TW")


if __name__ == "__main__":
    unittest.main()
