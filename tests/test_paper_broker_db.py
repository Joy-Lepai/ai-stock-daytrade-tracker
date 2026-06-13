from datetime import datetime
from pathlib import Path
import sqlite3
import tempfile
import unittest
from zoneinfo import ZoneInfo

from stock_daytrade_system.db import connect, save_us_symbols
from stock_daytrade_system.paper_broker import (
    close_manual_trade,
    create_manual_trade,
    empty_paper_dashboard_payload,
    paper_dashboard_payload,
    paper_quote,
    run_paper_trading,
)
from stock_daytrade_system.us_symbols import us_symbol_rows


TW_NOW = datetime(2026, 1, 5, 10, 0, tzinfo=ZoneInfo("Asia/Taipei"))
US_NOW = datetime(2026, 1, 5, 10, 0, tzinfo=ZoneInfo("America/New_York"))


def insert_tw_price(conn, symbol="2330.TW", price=100, sector="半導體"):
    conn.execute(
        "INSERT OR REPLACE INTO symbols (symbol, name, sector, market, is_active) VALUES (?, ?, ?, 'TW', 1)",
        (symbol, "台積電", sector),
    )
    conn.execute(
        """
        INSERT OR REPLACE INTO intraday_snapshots (
          captured_at, date, symbol, last_price, volume, turnover, vwap, above_vwap,
          volume_ratio, opening_range_high, opening_range_low
        )
        VALUES (?, ?, ?, ?, 1000, 100000, ?, 1, 1.5, ?, ?)
        """,
        (TW_NOW.isoformat(timespec="seconds"), "2026-01-05", symbol, price, price - 0.5, price + 1, price - 1),
    )


def insert_us_price(conn, symbol="NVDA", price=100, sector="半導體"):
    save_us_symbols(conn, us_symbol_rows(US_NOW))
    conn.execute(
        """
        INSERT OR REPLACE INTO us_candidates (
          captured_at, date, symbol, latest_price, previous_close, open, high, low,
          volume, change_pct, volume_ratio, vwap, above_vwap, premarket_high,
          break_premarket_high, break_previous_high, break_opening_range_high,
          opening_range_high, bullish_score, risk_score, grade, entry_status,
          lifecycle_status, trigger_price, stop_loss, target_price, reasons,
          risk_reasons, market_status
        )
        VALUES (?, '2026-01-05', ?, ?, 98, 99, ?, 97, 1000000, 2, 1.5, 99, 1, 100, 1, 1, 1, 100, 85, 20, 'A', 'executable', 'observed', ?, 98, 104, '[]', '[]', 'bullish')
        """,
        (US_NOW.isoformat(timespec="seconds"), symbol, price, price, price),
    )


def insert_recommendation(conn, market="TW", symbol="2330.TW", grade="A", entry_status="executable", lifecycle="triggered", stop_loss=98, target=104, trigger=100):
    now = TW_NOW if market == "TW" else US_NOW
    conn.execute(
        """
        INSERT OR REPLACE INTO recommendations (
          market, date, symbol, first_seen_at, latest_seen_at, grade, bullish_score, risk_score,
          entry_status, lifecycle_status, observed_at, trigger_time, trigger_price,
          trigger_reason, stop_loss, target_price, signal_price
        )
        VALUES (?, '2026-01-05', ?, ?, ?, ?, 85, 20, ?, ?, ?, ?, ?, '測試觸發', ?, ?, ?)
        """,
        (
            market,
            symbol,
            now.isoformat(timespec="seconds"),
            now.isoformat(timespec="seconds"),
            grade,
            entry_status,
            lifecycle,
            now.isoformat(timespec="seconds"),
            now.isoformat(timespec="seconds") if lifecycle == "triggered" else None,
            trigger,
            stop_loss,
            target,
            trigger,
        ),
    )


class PaperBrokerDatabaseTests(unittest.TestCase):
    def test_dashboard_payload_without_recommendations_returns_full_empty_state(self):
        with tempfile.TemporaryDirectory() as directory:
            with connect(Path(directory) / "db.sqlite") as conn:
                payload = paper_dashboard_payload(conn, TW_NOW)

        self.assertEqual(payload["api_status"], "ok")
        self.assertEqual(payload["data_source_status"], "ok")
        self.assertEqual(payload["errors"], [])
        self.assertEqual(len(payload["accounts"]), 2)
        self.assertEqual(payload["positions"], [])
        self.assertEqual(payload["trades"], [])
        self.assertEqual(payload["skipped_trades"], [])
        self.assertIn("尚無符合虛擬進場條件", payload["message"])
        self.assertEqual(payload["accounts"][0]["initial_cash"], 1_000_000)
        self.assertEqual(payload["accounts"][1]["initial_cash"], 30_000)

    def test_empty_fallback_payload_initializes_accounts(self):
        with tempfile.TemporaryDirectory() as directory:
            with connect(Path(directory) / "db.sqlite") as conn:
                payload = empty_paper_dashboard_payload(conn, TW_NOW, "測試錯誤")

        self.assertEqual(payload["api_status"], "degraded")
        self.assertEqual(payload["errors"], ["測試錯誤"])
        self.assertEqual(len(payload["accounts"]), 2)
        self.assertEqual(payload["positions"], [])

    def test_executable_recommendation_opens_trade_once(self):
        with tempfile.TemporaryDirectory() as directory:
            with connect(Path(directory) / "db.sqlite") as conn:
                insert_tw_price(conn)
                insert_recommendation(conn)

                first = run_paper_trading(conn, TW_NOW)
                second = run_paper_trading(conn, TW_NOW)
                open_positions = conn.execute("SELECT COUNT(*) AS total FROM paper_positions").fetchone()["total"]
                trades = conn.execute("SELECT COUNT(*) AS total FROM paper_trades WHERE status = 'open'").fetchone()["total"]

        self.assertEqual(first.opened, 1)
        self.assertEqual(second.opened, 0)
        self.assertEqual(open_positions, 1)
        self.assertEqual(trades, 1)

    def test_wait_signal_is_skipped_without_opening_position(self):
        with tempfile.TemporaryDirectory() as directory:
            with connect(Path(directory) / "db.sqlite") as conn:
                insert_tw_price(conn)
                insert_recommendation(conn, grade="B", entry_status="wait_vwap", lifecycle="observed")

                summary = run_paper_trading(conn, TW_NOW)
                skipped = conn.execute("SELECT skipped_reason FROM paper_trades WHERE status = 'skipped'").fetchone()

        self.assertEqual(summary.opened, 0)
        self.assertEqual(summary.skipped, 1)
        self.assertEqual(skipped["skipped_reason"], "not_executable")

    def test_b_plus_observed_waits_but_triggered_opens_trade(self):
        with tempfile.TemporaryDirectory() as directory:
            with connect(Path(directory) / "db.sqlite") as conn:
                insert_tw_price(conn)
                insert_recommendation(conn, grade="B+", entry_status="wait_pullback", lifecycle="observed")

                observed = run_paper_trading(conn, TW_NOW)
                conn.execute("DELETE FROM paper_trades")
                conn.execute("DELETE FROM paper_positions")
                conn.execute("UPDATE recommendations SET lifecycle_status = 'triggered', trigger_time = ?, trigger_price = 100 WHERE grade = 'B+'", (TW_NOW.isoformat(timespec="seconds"),))
                triggered = run_paper_trading(conn, TW_NOW)
                open_positions = conn.execute("SELECT COUNT(*) AS total FROM paper_positions").fetchone()["total"]

        self.assertEqual(observed.opened, 0)
        self.assertEqual(triggered.opened, 1)
        self.assertEqual(open_positions, 1)

    def test_target_hit_closes_open_position(self):
        with tempfile.TemporaryDirectory() as directory:
            with connect(Path(directory) / "db.sqlite") as conn:
                insert_tw_price(conn, price=100)
                insert_recommendation(conn, target=101)
                run_paper_trading(conn, TW_NOW)
                insert_tw_price(conn, price=102)

                summary = run_paper_trading(conn, TW_NOW)
                trade = conn.execute("SELECT status, realized_pnl FROM paper_trades WHERE status = 'target_hit'").fetchone()
                positions = conn.execute("SELECT COUNT(*) AS total FROM paper_positions").fetchone()["total"]

        self.assertEqual(summary.closed, 1)
        self.assertEqual(positions, 0)
        self.assertGreater(trade["realized_pnl"], 0)

    def test_us_and_tw_accounts_are_separate(self):
        with tempfile.TemporaryDirectory() as directory:
            with connect(Path(directory) / "db.sqlite") as conn:
                insert_us_price(conn)
                insert_recommendation(conn, market="US", symbol="NVDA", grade="A", entry_status="executable", lifecycle="triggered")

                run_paper_trading(conn, US_NOW)
                accounts = conn.execute("SELECT id, market, currency FROM paper_accounts ORDER BY id").fetchall()
                trade = conn.execute("SELECT market, symbol FROM paper_trades WHERE status = 'open'").fetchone()

        self.assertEqual([(row["id"], row["market"], row["currency"]) for row in accounts], [("TW", "TW", "TWD"), ("US", "US", "USD")])
        self.assertEqual((trade["market"], trade["symbol"]), ("US", "NVDA"))

    def test_create_us_manual_trade_opens_position_and_deducts_cash(self):
        with tempfile.TemporaryDirectory() as directory:
            with connect(Path(directory) / "db.sqlite") as conn:
                insert_us_price(conn, price=100)
                result = create_manual_trade(
                    conn,
                    {
                        "market": "US",
                        "symbol": "NVDA",
                        "entry_price": 100,
                        "quantity": 2,
                        "stop_loss": 98,
                        "target_price": 104,
                        "entry_reason": "測試手動買進",
                    },
                    US_NOW,
                )
                account = conn.execute("SELECT cash_balance FROM paper_accounts WHERE id = 'US'").fetchone()
                position = conn.execute("SELECT * FROM paper_positions WHERE symbol = 'NVDA'").fetchone()
                trade = conn.execute("SELECT source, is_manual, name_zh, risk_mode, auto_exit_enabled FROM paper_trades WHERE symbol = 'NVDA'").fetchone()

        self.assertTrue(result["ok"])
        self.assertIn("已建立手動虛擬買進：NVDA｜輝達", result["message"])
        self.assertEqual(account["cash_balance"], 29_800)
        self.assertEqual(position["quantity"], 2)
        self.assertEqual((trade["source"], trade["is_manual"], trade["name_zh"]), ("manual", 1, "輝達"))
        self.assertEqual((trade["risk_mode"], trade["auto_exit_enabled"]), ("manual_only", 0))

    def test_us_quote_uses_builtin_chinese_name_when_symbol_table_is_empty(self):
        with tempfile.TemporaryDirectory() as directory:
            with connect(Path(directory) / "db.sqlite") as conn:
                quote = paper_quote(conn, "US", "NVDA")

        self.assertFalse(quote["ok"])
        self.assertEqual(quote["name_zh"], "輝達")
        self.assertEqual(quote["name_en"], "NVIDIA Corporation")

    def test_create_tw_manual_trade_opens_position(self):
        with tempfile.TemporaryDirectory() as directory:
            with connect(Path(directory) / "db.sqlite") as conn:
                insert_tw_price(conn, price=100)
                result = create_manual_trade(
                    conn,
                    {
                        "market": "TW",
                        "symbol": "2330.TW",
                        "entry_price": 100,
                        "quantity": 1000,
                        "stop_loss": 98,
                        "target_price": 104,
                        "entry_reason": "測試台股手動買進",
                    },
                    TW_NOW,
                )
                account = conn.execute("SELECT cash_balance FROM paper_accounts WHERE id = 'TW'").fetchone()
                position = conn.execute("SELECT * FROM paper_positions WHERE symbol = '2330.TW'").fetchone()

        self.assertTrue(result["ok"])
        self.assertEqual(account["cash_balance"], 900_000)
        self.assertEqual(position["quantity"], 1000)

    def test_manual_trade_rejects_invalid_stop_loss(self):
        with tempfile.TemporaryDirectory() as directory:
            with connect(Path(directory) / "db.sqlite") as conn:
                result = create_manual_trade(
                    conn,
                    {"market": "US", "symbol": "NVDA", "entry_price": 100, "quantity": 1, "stop_loss": 101, "target_price": 104},
                    US_NOW,
                )

        self.assertFalse(result["ok"])
        self.assertEqual(result["message"], "停損價必須低於進場價")

    def test_manual_trade_rejects_invalid_target_price(self):
        with tempfile.TemporaryDirectory() as directory:
            with connect(Path(directory) / "db.sqlite") as conn:
                result = create_manual_trade(
                    conn,
                    {"market": "US", "symbol": "NVDA", "entry_price": 100, "quantity": 1, "stop_loss": 98, "target_price": 99},
                    US_NOW,
                )

        self.assertFalse(result["ok"])
        self.assertEqual(result["message"], "停利價必須高於進場價")

    def test_manual_trade_rejects_insufficient_cash(self):
        with tempfile.TemporaryDirectory() as directory:
            with connect(Path(directory) / "db.sqlite") as conn:
                result = create_manual_trade(
                    conn,
                    {"market": "US", "symbol": "NVDA", "entry_price": 100, "quantity": 1000, "stop_loss": 98, "target_price": 104},
                    US_NOW,
                )

        self.assertFalse(result["ok"])
        self.assertEqual(result["message"], "現金餘額不足")

    def test_manual_trade_rejects_duplicate_open_position(self):
        with tempfile.TemporaryDirectory() as directory:
            with connect(Path(directory) / "db.sqlite") as conn:
                payload = {"market": "US", "symbol": "NVDA", "entry_price": 100, "quantity": 1, "stop_loss": 98, "target_price": 104}
                first = create_manual_trade(conn, payload, US_NOW)
                second = create_manual_trade(conn, payload, US_NOW)

        self.assertTrue(first["ok"])
        self.assertFalse(second["ok"])
        self.assertEqual(second["message"], "已有同一檔 open position，不能重複開倉")

    def test_close_manual_trade_updates_realized_pnl_and_cash(self):
        with tempfile.TemporaryDirectory() as directory:
            with connect(Path(directory) / "db.sqlite") as conn:
                create_manual_trade(
                    conn,
                    {"market": "US", "symbol": "NVDA", "entry_price": 100, "quantity": 2, "stop_loss": 98, "target_price": 104},
                    US_NOW,
                )
                trade_id = conn.execute("SELECT id FROM paper_trades WHERE source = 'manual'").fetchone()["id"]
                result = close_manual_trade(conn, {"trade_id": trade_id, "exit_price": 110}, US_NOW)
                account = conn.execute("SELECT cash_balance, realized_pnl FROM paper_accounts WHERE id = 'US'").fetchone()
                trade = conn.execute("SELECT status, exit_reason, realized_pnl, realized_pnl_pct FROM paper_trades WHERE id = ?", (trade_id,)).fetchone()
                positions = conn.execute("SELECT COUNT(*) AS total FROM paper_positions").fetchone()["total"]

        self.assertTrue(result["ok"])
        self.assertEqual(positions, 0)
        self.assertEqual((trade["status"], trade["exit_reason"]), ("closed", "manual_close"))
        self.assertEqual(trade["realized_pnl"], 20)
        self.assertEqual(trade["realized_pnl_pct"], 10)
        self.assertEqual(account["cash_balance"], 30_020)
        self.assertEqual(account["realized_pnl"], 20)

    def test_manual_only_trade_does_not_auto_stop(self):
        with tempfile.TemporaryDirectory() as directory:
            with connect(Path(directory) / "db.sqlite") as conn:
                insert_us_price(conn, price=100)
                create_manual_trade(
                    conn,
                    {"market": "US", "symbol": "NVDA", "entry_price": 100, "quantity": 1, "stop_loss": 98, "target_price": 104},
                    US_NOW,
                )
                insert_us_price(conn, price=97)

                summary = run_paper_trading(conn, US_NOW)
                trade = conn.execute("SELECT status, exit_reason FROM paper_trades WHERE source = 'manual'").fetchone()
                position = conn.execute("SELECT current_price, unrealized_pnl FROM paper_positions WHERE symbol = 'NVDA'").fetchone()

        self.assertEqual(summary.closed, 0)
        self.assertEqual((trade["status"], trade["exit_reason"]), ("open", None))
        self.assertEqual(position["current_price"], 97)
        self.assertEqual(position["unrealized_pnl"], -3)

    def test_manual_only_reaching_stop_shows_alert_in_dashboard_payload(self):
        with tempfile.TemporaryDirectory() as directory:
            with connect(Path(directory) / "db.sqlite") as conn:
                insert_us_price(conn, price=100)
                create_manual_trade(
                    conn,
                    {"market": "US", "symbol": "NVDA", "entry_price": 100, "quantity": 1, "stop_loss": 98, "target_price": 104},
                    US_NOW,
                )
                insert_us_price(conn, price=97)

                payload = paper_dashboard_payload(conn, US_NOW)
                position = next(item for item in payload["positions"] if item["symbol"] == "NVDA")

        self.assertEqual(position["risk_mode"], "manual_only")
        self.assertEqual(position["auto_exit_enabled"], 0)
        self.assertEqual(position["risk_alert"], "已達停損提醒")

    def test_manual_auto_stop_take_profit_closes_at_stop(self):
        with tempfile.TemporaryDirectory() as directory:
            with connect(Path(directory) / "db.sqlite") as conn:
                insert_us_price(conn, price=100)
                create_manual_trade(
                    conn,
                    {
                        "market": "US",
                        "symbol": "NVDA",
                        "entry_price": 100,
                        "quantity": 1,
                        "stop_loss": 98,
                        "target_price": 104,
                        "risk_mode": "auto_stop_take_profit",
                    },
                    US_NOW,
                )
                insert_us_price(conn, price=97)

                summary = run_paper_trading(conn, US_NOW)
                trade = conn.execute("SELECT status, exit_reason, auto_exit_reason FROM paper_trades WHERE source = 'manual'").fetchone()
                positions = conn.execute("SELECT COUNT(*) AS total FROM paper_positions WHERE symbol = 'NVDA'").fetchone()["total"]

        self.assertEqual(summary.closed, 1)
        self.assertEqual(positions, 0)
        self.assertEqual((trade["status"], trade["exit_reason"], trade["auto_exit_reason"]), ("stopped", "auto_stop_loss", "auto_stop_loss"))

    def test_manual_auto_stop_take_profit_closes_at_target(self):
        with tempfile.TemporaryDirectory() as directory:
            with connect(Path(directory) / "db.sqlite") as conn:
                insert_us_price(conn, price=100)
                create_manual_trade(
                    conn,
                    {
                        "market": "US",
                        "symbol": "NVDA",
                        "entry_price": 100,
                        "quantity": 1,
                        "stop_loss": 98,
                        "target_price": 104,
                        "risk_mode": "auto_stop_take_profit",
                    },
                    US_NOW,
                )
                insert_us_price(conn, price=105)

                summary = run_paper_trading(conn, US_NOW)
                trade = conn.execute("SELECT status, exit_reason, auto_exit_reason, realized_pnl FROM paper_trades WHERE source = 'manual'").fetchone()

        self.assertEqual(summary.closed, 1)
        self.assertEqual((trade["status"], trade["exit_reason"], trade["auto_exit_reason"]), ("target_hit", "auto_take_profit", "auto_take_profit"))
        self.assertEqual(trade["realized_pnl"], 5)

    def test_manual_follow_system_uses_system_risk_rules(self):
        with tempfile.TemporaryDirectory() as directory:
            with connect(Path(directory) / "db.sqlite") as conn:
                insert_us_price(conn, price=100)
                create_manual_trade(
                    conn,
                    {
                        "market": "US",
                        "symbol": "NVDA",
                        "entry_price": 100,
                        "quantity": 1,
                        "stop_loss": 98,
                        "target_price": 104,
                        "risk_mode": "follow_system",
                    },
                    US_NOW,
                )
                insert_us_price(conn, price=97)

                summary = run_paper_trading(conn, US_NOW)
                trade = conn.execute("SELECT status, exit_reason FROM paper_trades WHERE source = 'manual'").fetchone()

        self.assertEqual(summary.closed, 1)
        self.assertEqual((trade["status"], trade["exit_reason"]), ("stopped", "stopped"))

    def test_old_manual_trades_migrate_to_manual_only(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "db.sqlite"
            raw = sqlite3.connect(db_path)
            raw.execute(
                """
                CREATE TABLE paper_trades (
                  id TEXT PRIMARY KEY,
                  source TEXT,
                  is_manual INTEGER
                )
                """
            )
            raw.execute("INSERT INTO paper_trades (id, source, is_manual) VALUES ('old-manual', 'manual', 1)")
            raw.execute("INSERT INTO paper_trades (id, source, is_manual) VALUES ('old-system', 'system', 0)")
            raw.commit()
            raw.close()

            with connect(db_path) as conn:
                rows = {
                    row["id"]: row
                    for row in conn.execute("SELECT id, risk_mode, auto_exit_enabled FROM paper_trades").fetchall()
                }

        self.assertEqual((rows["old-manual"]["risk_mode"], rows["old-manual"]["auto_exit_enabled"]), ("manual_only", 0))
        self.assertEqual((rows["old-system"]["risk_mode"], rows["old-system"]["auto_exit_enabled"]), ("follow_system", 1))


if __name__ == "__main__":
    unittest.main()
