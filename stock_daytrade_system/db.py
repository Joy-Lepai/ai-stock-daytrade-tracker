from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict
from datetime import date, datetime
from pathlib import Path
from typing import Iterable, List, Optional


SCHEMA = """
CREATE TABLE IF NOT EXISTS symbols (
  symbol TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  sector TEXT NOT NULL,
  market TEXT NOT NULL DEFAULT 'TW',
  is_active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS daily_snapshots (
  date TEXT NOT NULL,
  symbol TEXT NOT NULL,
  close REAL,
  change_pct REAL,
  volume REAL,
  turnover REAL,
  avg_volume_20 REAL,
  volume_ratio REAL,
  previous_high REAL,
  high_5d REAL,
  high_10d REAL,
  break_prev_high INTEGER,
  break_5d_high INTEGER,
  break_10d_high INTEGER,
  PRIMARY KEY (date, symbol)
);

CREATE TABLE IF NOT EXISTS intraday_snapshots (
  captured_at TEXT NOT NULL,
  date TEXT NOT NULL,
  symbol TEXT NOT NULL,
  last_price REAL,
  volume REAL,
  turnover REAL,
  vwap REAL,
  above_vwap INTEGER,
  volume_ratio REAL,
  opening_range_high REAL,
  opening_range_low REAL,
  PRIMARY KEY (captured_at, symbol)
);

CREATE TABLE IF NOT EXISTS long_scores (
  captured_at TEXT NOT NULL,
  date TEXT NOT NULL,
  symbol TEXT NOT NULL,
  bullish_score REAL NOT NULL,
  risk_score REAL NOT NULL,
  grade TEXT NOT NULL,
  reasons TEXT NOT NULL,
  risk_reasons TEXT NOT NULL,
  PRIMARY KEY (captured_at, symbol)
);

CREATE TABLE IF NOT EXISTS recommendations (
  date TEXT NOT NULL,
  symbol TEXT NOT NULL,
  first_seen_at TEXT NOT NULL,
  latest_seen_at TEXT NOT NULL,
  grade TEXT NOT NULL,
  bullish_score REAL NOT NULL,
  risk_score REAL NOT NULL,
  entry_status TEXT NOT NULL,
  trigger_price REAL,
  stop_loss REAL,
  target_price REAL,
  signal_price REAL,
  PRIMARY KEY (date, symbol)
);

CREATE TABLE IF NOT EXISTS backtest_results (
  date TEXT NOT NULL,
  symbol TEXT NOT NULL,
  entry_price REAL,
  max_price_after_signal REAL,
  min_price_after_signal REAL,
  close_price REAL,
  same_day_high REAL,
  same_day_low REAL,
  same_day_close REAL,
  max_gain_after_recommend REAL,
  max_drawdown_after_recommend REAL,
  hit_target INTEGER,
  hit_stop INTEGER,
  hit_stop_loss INTEGER,
  outcome TEXT,
  return_pct REAL,
  PRIMARY KEY (date, symbol)
);
"""


def default_db_path(project_root: Path) -> Path:
    return project_root / "data" / "daytrade.db"


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    _ensure_backtest_columns(conn)
    return conn


def save_long_candidates(conn: sqlite3.Connection, captured_at: datetime, candidates: Iterable[object]) -> None:
    date_text = captured_at.strftime("%Y-%m-%d")
    captured_text = captured_at.isoformat(timespec="seconds")
    candidate_rows = [asdict(item) for item in candidates]
    eligible_recommendation_symbols = {
        data["symbol"]
        for data in candidate_rows
        if data["grade"] in {"A", "B"}
    }
    with conn:
        _delete_stale_recommendations(conn, date_text, eligible_recommendation_symbols)
        for data in candidate_rows:
            conn.execute(
                """
                INSERT INTO symbols (symbol, name, sector, market, is_active)
                VALUES (?, ?, ?, 'TW', 1)
                ON CONFLICT(symbol) DO UPDATE SET
                  name=excluded.name,
                  sector=excluded.sector,
                  is_active=1
                """,
                (data["symbol"], data["name"], data["sector"]),
            )
            conn.execute(
                """
                INSERT OR REPLACE INTO daily_snapshots (
                  date, symbol, close, change_pct, volume, turnover, avg_volume_20, volume_ratio,
                  previous_high, high_5d, high_10d, break_prev_high, break_5d_high, break_10d_high
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    date_text,
                    data["symbol"],
                    data["last_price"],
                    data["change_pct"],
                    data["volume"],
                    data["turnover"],
                    data["avg_volume_20"],
                    data["daily_volume_ratio"],
                    data["previous_high"],
                    data["high_5d"],
                    data["high_10d"],
                    int(data["break_prev_high"]),
                    int(data["break_5d_high"]),
                    int(data["break_10d_high"]),
                ),
            )
            conn.execute(
                """
                INSERT OR REPLACE INTO intraday_snapshots (
                  captured_at, date, symbol, last_price, volume, turnover, vwap, above_vwap,
                  volume_ratio, opening_range_high, opening_range_low
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    captured_text,
                    date_text,
                    data["symbol"],
                    data["last_price"],
                    data["intraday_volume"],
                    data["turnover"],
                    data["vwap"],
                    int(data["above_vwap"]),
                    data["volume_ratio"],
                    data["opening_range_high"],
                    data["opening_range_low"],
                ),
            )
            conn.execute(
                """
                INSERT OR REPLACE INTO long_scores (
                  captured_at, date, symbol, bullish_score, risk_score, grade, reasons, risk_reasons
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    captured_text,
                    date_text,
                    data["symbol"],
                    data["bullish_score"],
                    data["risk_score"],
                    data["grade"],
                    json.dumps(data["reasons"], ensure_ascii=False),
                    json.dumps(data["risk_reasons"], ensure_ascii=False),
                ),
            )
            if data["grade"] in {"A", "B"}:
                conn.execute(
                    """
                    INSERT INTO recommendations (
                      date, symbol, first_seen_at, latest_seen_at, grade, bullish_score, risk_score,
                      entry_status, trigger_price, stop_loss, target_price, signal_price
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(date, symbol) DO UPDATE SET
                      latest_seen_at=excluded.latest_seen_at,
                      grade=excluded.grade,
                      bullish_score=excluded.bullish_score,
                      risk_score=excluded.risk_score,
                      entry_status=excluded.entry_status,
                      trigger_price=excluded.trigger_price,
                      stop_loss=excluded.stop_loss,
                      target_price=excluded.target_price
                    """,
                    (
                        date_text,
                        data["symbol"],
                        captured_text,
                        captured_text,
                        data["grade"],
                        data["bullish_score"],
                        data["risk_score"],
                        data["entry_status"],
                        data["trigger_price"],
                        data["stop_loss"],
                        data["target_price"],
                        data["last_price"],
                    ),
                )


def update_backtests(conn: sqlite3.Connection, captured_at: datetime, intraday_bars_by_symbol: dict) -> None:
    cutoff_text = captured_at.strftime("%Y-%m-%d")
    rows = conn.execute(
        """
        SELECT *
        FROM recommendations
        WHERE date <= ?
          AND date >= date(?, '-7 days')
        """,
        (cutoff_text, cutoff_text),
    ).fetchall()
    with conn:
        for row in rows:
            bars = intraday_bars_by_symbol.get(row["symbol"], [])
            signal_time = _parse_datetime(row["first_seen_at"])
            if not bars or signal_time is None:
                continue
            signal_time = signal_time.replace(tzinfo=None)
            recommendation_date = datetime.strptime(row["date"], "%Y-%m-%d").date()
            same_day_bars = [bar for bar in bars if bar.timestamp.date() == recommendation_date]
            if not same_day_bars:
                continue
            relevant = [
                bar for bar in same_day_bars
                if bar.timestamp >= signal_time
            ]
            if not relevant:
                relevant = same_day_bars
            entry_price = row["signal_price"] or relevant[0].close
            max_price = max(bar.high for bar in relevant)
            min_price = min(bar.low for bar in relevant)
            close_price = same_day_bars[-1].close
            same_day_high = max(bar.high for bar in same_day_bars)
            same_day_low = min(bar.low for bar in same_day_bars)
            target_price = row["target_price"]
            stop_loss = row["stop_loss"]
            hit_target = bool(target_price is not None and max_price >= target_price)
            hit_stop_loss = bool(stop_loss is not None and min_price <= stop_loss)
            if hit_target and hit_stop_loss:
                outcome = "同K不明"
            elif hit_target:
                outcome = "達標"
            elif hit_stop_loss:
                outcome = "停損"
            else:
                outcome = "追蹤中"
            max_gain = ((max_price - entry_price) / entry_price * 100) if entry_price else 0
            max_drawdown = ((min_price - entry_price) / entry_price * 100) if entry_price else 0
            return_pct = ((close_price - entry_price) / entry_price * 100) if entry_price else 0
            conn.execute(
                """
                INSERT OR REPLACE INTO backtest_results (
                  date, symbol, entry_price, max_price_after_signal, min_price_after_signal,
                  close_price, same_day_high, same_day_low, same_day_close,
                  max_gain_after_recommend, max_drawdown_after_recommend,
                  hit_target, hit_stop, hit_stop_loss, outcome, return_pct
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row["date"],
                    row["symbol"],
                    round(entry_price, 2),
                    round(max_price, 2),
                    round(min_price, 2),
                    round(close_price, 2),
                    round(same_day_high, 2),
                    round(same_day_low, 2),
                    round(close_price, 2),
                    round(max_gain, 2),
                    round(max_drawdown, 2),
                    int(hit_target),
                    int(hit_stop_loss),
                    int(hit_stop_loss),
                    outcome,
                    round(return_pct, 2),
                ),
            )


def latest_candidates(conn: sqlite3.Connection, limit: int = 50) -> List[sqlite3.Row]:
    captured = conn.execute("SELECT MAX(captured_at) AS captured_at FROM long_scores").fetchone()["captured_at"]
    if not captured:
        return []
    return conn.execute(
        """
        SELECT
               ls.captured_at, ls.date, ls.symbol, ls.bullish_score, ls.risk_score,
               ls.grade,
               ls.reasons, ls.risk_reasons,
               s.name, s.sector, ds.close, ds.change_pct, ds.volume, ds.turnover,
               ds.volume_ratio AS daily_volume_ratio, ds.break_prev_high, ds.break_5d_high,
               ds.break_10d_high, intr.vwap, intr.above_vwap, intr.volume_ratio
        FROM long_scores ls
        JOIN symbols s ON s.symbol = ls.symbol
        LEFT JOIN daily_snapshots ds ON ds.date = ls.date AND ds.symbol = ls.symbol
        LEFT JOIN intraday_snapshots intr ON intr.captured_at = ls.captured_at AND intr.symbol = ls.symbol
        WHERE ls.captured_at = ?
        ORDER BY
          CASE ls.grade WHEN 'A' THEN 1 WHEN 'B' THEN 2 WHEN 'C' THEN 3 ELSE 4 END,
          ls.bullish_score DESC,
          ls.risk_score ASC
        LIMIT ?
        """,
        (captured, limit),
    ).fetchall()


def symbol_history(conn: sqlite3.Connection, symbol: str, limit: int = 30) -> List[sqlite3.Row]:
    return conn.execute(
        """
        SELECT r.*, b.outcome, b.return_pct, b.max_price_after_signal, b.min_price_after_signal,
               b.close_price, b.same_day_high, b.same_day_low, b.same_day_close,
               b.max_gain_after_recommend, b.max_drawdown_after_recommend,
               b.hit_target, b.hit_stop_loss
        FROM recommendations r
        LEFT JOIN backtest_results b ON b.date = r.date AND b.symbol = r.symbol
        WHERE r.symbol = ?
        ORDER BY r.date DESC
        LIMIT ?
        """,
        (symbol, limit),
    ).fetchall()


def latest_symbol_score(conn: sqlite3.Connection, symbol: str) -> Optional[sqlite3.Row]:
    return conn.execute(
        """
        SELECT
               ls.captured_at, ls.date, ls.symbol, ls.bullish_score, ls.risk_score,
               ls.grade,
               ls.reasons, ls.risk_reasons,
               s.name, s.sector, ds.*, intr.vwap, intr.above_vwap, intr.volume_ratio AS intraday_volume_ratio
        FROM long_scores ls
        JOIN symbols s ON s.symbol = ls.symbol
        LEFT JOIN daily_snapshots ds ON ds.date = ls.date AND ds.symbol = ls.symbol
        LEFT JOIN intraday_snapshots intr ON intr.captured_at = ls.captured_at AND intr.symbol = ls.symbol
        WHERE ls.symbol = ?
        ORDER BY ls.captured_at DESC
        LIMIT 1
        """,
        (symbol,),
    ).fetchone()


def backtest_summary(conn: sqlite3.Connection, day: Optional[date] = None) -> dict:
    params: List[str] = []
    where = ""
    if day is not None:
        where = "WHERE date = ?"
        params.append(day.strftime("%Y-%m-%d"))
    recommendation_count = conn.execute(f"SELECT COUNT(*) AS total FROM recommendations {where}", params).fetchone()["total"]
    rows = conn.execute(f"SELECT * FROM backtest_results {where}", params).fetchall()
    status_rows = conn.execute(
        f"""
        SELECT
          r.entry_status,
          r.symbol,
          b.outcome,
          b.return_pct
        FROM recommendations r
        LEFT JOIN backtest_results b ON b.date = r.date AND b.symbol = r.symbol
        {where.replace("date", "r.date") if where else ""}
        """,
        params,
    ).fetchall()
    trackable_count = len(rows)
    target = sum(1 for row in rows if row["outcome"] == "達標")
    stop = sum(1 for row in rows if row["outcome"] == "停損")
    avg_return = round(sum(row["return_pct"] or 0 for row in rows) / trackable_count, 2) if trackable_count else 0.0
    return {
        "total": recommendation_count,
        "recommendation_count": recommendation_count,
        "trackable_count": trackable_count,
        "target": target,
        "stop": stop,
        "avg_return": avg_return,
        "by_entry_status": _summarize_by_entry_status(status_rows),
    }


def _summarize_by_entry_status(rows: Iterable[sqlite3.Row]) -> List[dict]:
    grouped: dict[str, list[sqlite3.Row]] = {}
    for row in rows:
        grouped.setdefault(row["entry_status"] or "unknown", []).append(row)

    result = []
    for status, items in grouped.items():
        tracked = [item for item in items if item["outcome"]]
        total = len(items)
        trackable = len(tracked)
        target = sum(1 for item in tracked if item["outcome"] == "達標")
        stop = sum(1 for item in tracked if item["outcome"] == "停損")
        avg_return = round(sum(item["return_pct"] or 0 for item in tracked) / trackable, 2) if trackable else 0.0
        result.append(
            {
                "entry_status": status,
                "total": total,
                "trackable": trackable,
                "target": target,
                "stop": stop,
                "avg_return": avg_return,
            }
        )
    result.sort(key=lambda item: _entry_status_order(item["entry_status"]))
    return result


def _entry_status_order(value: str) -> int:
    return {
        "executable": 0,
        "wait_pullback": 1,
        "wait_volume": 2,
        "wait_vwap": 3,
        "high_risk": 4,
        "avoid": 5,
    }.get(value, 9)


def _ensure_backtest_columns(conn: sqlite3.Connection) -> None:
    existing = {row["name"] for row in conn.execute("PRAGMA table_info(backtest_results)").fetchall()}
    columns = {
        "same_day_high": "REAL",
        "same_day_low": "REAL",
        "same_day_close": "REAL",
        "max_gain_after_recommend": "REAL",
        "max_drawdown_after_recommend": "REAL",
        "hit_stop_loss": "INTEGER",
    }
    for name, column_type in columns.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE backtest_results ADD COLUMN {name} {column_type}")


def _delete_stale_recommendations(conn: sqlite3.Connection, date_text: str, eligible_symbols: set[str]) -> None:
    if not eligible_symbols:
        conn.execute("DELETE FROM recommendations WHERE date = ?", (date_text,))
        conn.execute("DELETE FROM backtest_results WHERE date = ?", (date_text,))
        return

    placeholders = ", ".join("?" for _ in eligible_symbols)
    params = [date_text, *sorted(eligible_symbols)]
    conn.execute(
        f"DELETE FROM recommendations WHERE date = ? AND symbol NOT IN ({placeholders})",
        params,
    )
    conn.execute(
        f"DELETE FROM backtest_results WHERE date = ? AND symbol NOT IN ({placeholders})",
        params,
    )


def _parse_datetime(value: str) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
