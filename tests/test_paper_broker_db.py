from datetime import datetime
from pathlib import Path
import tempfile
import unittest
from zoneinfo import ZoneInfo

from stock_daytrade_system.db import connect, save_us_symbols
from stock_daytrade_system.paper_broker import empty_paper_dashboard_payload, paper_dashboard_payload, run_paper_trading
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


if __name__ == "__main__":
    unittest.main()
