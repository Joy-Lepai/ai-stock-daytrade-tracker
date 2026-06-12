from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict
from datetime import date, datetime
from pathlib import Path
from typing import Iterable, List, Optional

from stock_daytrade_system.b_plus_trigger_tracker import evaluate_b_plus_trigger


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
  confidence_score REAL,
  confidence_level TEXT,
  conflicts_count INTEGER,
  conflicts TEXT,
  conflict_summary TEXT,
  confidence_summary TEXT,
  original_entry_status TEXT,
  adjusted_entry_status TEXT,
  confidence_adjustment_reason TEXT,
  PRIMARY KEY (captured_at, symbol)
);

CREATE TABLE IF NOT EXISTS recommendations (
  market TEXT NOT NULL DEFAULT 'TW',
  date TEXT NOT NULL,
  symbol TEXT NOT NULL,
  first_seen_at TEXT NOT NULL,
  latest_seen_at TEXT NOT NULL,
  grade TEXT NOT NULL,
  bullish_score REAL NOT NULL,
  risk_score REAL NOT NULL,
  entry_status TEXT NOT NULL,
  lifecycle_status TEXT NOT NULL DEFAULT 'observed',
  observed_at TEXT,
  trigger_time TEXT,
  trigger_price REAL,
  trigger_reason TEXT,
  stop_loss REAL,
  target_price REAL,
  signal_price REAL,
  confidence_score REAL,
  confidence_level TEXT,
  conflicts_count INTEGER,
  conflicts TEXT,
  conflict_summary TEXT,
  confidence_summary TEXT,
  original_entry_status TEXT,
  adjusted_entry_status TEXT,
  confidence_adjustment_reason TEXT,
  expired_at TEXT,
  closed_at TEXT,
  PRIMARY KEY (date, symbol)
);

CREATE TABLE IF NOT EXISTS backtest_results (
  market TEXT NOT NULL DEFAULT 'TW',
  date TEXT NOT NULL,
  symbol TEXT NOT NULL,
  lifecycle_status TEXT,
  entry_status TEXT,
  trigger_time TEXT,
  trigger_price REAL,
  entry_price REAL,
  max_price_after_signal REAL,
  min_price_after_signal REAL,
  max_price_after_trigger REAL,
  min_price_after_trigger REAL,
  close_price REAL,
  same_day_high REAL,
  same_day_low REAL,
  same_day_close REAL,
  max_gain_after_recommend REAL,
  max_drawdown_after_recommend REAL,
  max_gain_after_trigger REAL,
  max_drawdown_after_trigger REAL,
  hit_target INTEGER,
  hit_stop INTEGER,
  hit_stop_loss INTEGER,
  expired_without_trigger INTEGER,
  outcome TEXT,
  return_pct REAL,
  confidence_score REAL,
  confidence_level TEXT,
  entry_status_at_signal TEXT,
  PRIMARY KEY (date, symbol)
);

CREATE TABLE IF NOT EXISTS us_symbols (
  symbol TEXT PRIMARY KEY,
  name_en TEXT NOT NULL,
  name_zh TEXT NOT NULL,
  short_name_zh TEXT NOT NULL,
  sector_en TEXT NOT NULL,
  sector_zh TEXT NOT NULL,
  industry_en TEXT NOT NULL,
  industry_zh TEXT NOT NULL,
  description_zh TEXT NOT NULL,
  is_etf INTEGER NOT NULL DEFAULT 0,
  is_active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT,
  updated_at TEXT
);

CREATE TABLE IF NOT EXISTS us_candidates (
  captured_at TEXT NOT NULL,
  date TEXT NOT NULL,
  symbol TEXT NOT NULL,
  latest_price REAL,
  previous_close REAL,
  open REAL,
  high REAL,
  low REAL,
  volume REAL,
  change_pct REAL,
  volume_ratio REAL,
  vwap REAL,
  above_vwap INTEGER,
  premarket_high REAL,
  break_premarket_high INTEGER,
  break_previous_high INTEGER,
  break_opening_range_high INTEGER,
  opening_range_high REAL,
  bullish_score REAL,
  risk_score REAL,
  grade TEXT,
  entry_status TEXT,
  lifecycle_status TEXT,
  trigger_price REAL,
  stop_loss REAL,
  target_price REAL,
  reasons TEXT,
  risk_reasons TEXT,
  market_status TEXT,
  confidence_score REAL,
  confidence_level TEXT,
  conflicts_count INTEGER,
  conflicts TEXT,
  conflict_summary TEXT,
  confidence_summary TEXT,
  original_entry_status TEXT,
  adjusted_entry_status TEXT,
  confidence_adjustment_reason TEXT,
  PRIMARY KEY (captured_at, symbol)
);

CREATE TABLE IF NOT EXISTS paper_accounts (
  id TEXT PRIMARY KEY,
  market TEXT NOT NULL,
  currency TEXT NOT NULL,
  initial_cash REAL NOT NULL,
  cash_balance REAL NOT NULL,
  equity REAL NOT NULL,
  realized_pnl REAL NOT NULL DEFAULT 0,
  unrealized_pnl REAL NOT NULL DEFAULT 0,
  max_drawdown REAL NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS paper_trades (
  id TEXT PRIMARY KEY,
  account_id TEXT NOT NULL,
  recommendation_id TEXT NOT NULL,
  market TEXT NOT NULL,
  symbol TEXT NOT NULL,
  name_zh TEXT,
  name_en TEXT,
  source TEXT NOT NULL DEFAULT 'system',
  is_manual INTEGER NOT NULL DEFAULT 0,
  manual_reason TEXT,
  created_by TEXT,
  side TEXT NOT NULL,
  status TEXT NOT NULL,
  grade TEXT,
  entry_status TEXT,
  lifecycle_status TEXT,
  entry_time TEXT,
  entry_price REAL,
  entry_reason TEXT,
  quantity REAL,
  position_value REAL,
  stop_loss REAL,
  target_price REAL,
  exit_time TEXT,
  exit_price REAL,
  exit_reason TEXT,
  realized_pnl REAL,
  realized_pnl_pct REAL,
  max_favorable_excursion REAL,
  max_adverse_excursion REAL,
  skipped_reason TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS paper_positions (
  id TEXT PRIMARY KEY,
  account_id TEXT NOT NULL,
  trade_id TEXT NOT NULL,
  market TEXT NOT NULL,
  symbol TEXT NOT NULL,
  quantity REAL NOT NULL,
  entry_price REAL NOT NULL,
  current_price REAL NOT NULL,
  market_value REAL NOT NULL,
  unrealized_pnl REAL NOT NULL,
  unrealized_pnl_pct REAL NOT NULL,
  stop_loss REAL,
  target_price REAL,
  highest_price_since_entry REAL,
  lowest_price_since_entry REAL,
  opened_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS paper_equity_curve (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  account_id TEXT NOT NULL,
  market TEXT NOT NULL,
  captured_at TEXT NOT NULL,
  cash_balance REAL NOT NULL,
  position_value REAL NOT NULL,
  equity REAL NOT NULL,
  realized_pnl REAL NOT NULL,
  unrealized_pnl REAL NOT NULL,
  drawdown REAL NOT NULL
);
"""


def default_db_path(project_root: Path) -> Path:
    return project_root / "data" / "daytrade.db"


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    _ensure_recommendation_columns(conn)
    _ensure_backtest_columns(conn)
    _ensure_confidence_columns(conn)
    _ensure_paper_trade_columns(conn)
    return conn


def save_long_candidates(conn: sqlite3.Connection, captured_at: datetime, candidates: Iterable[object]) -> None:
    date_text = captured_at.strftime("%Y-%m-%d")
    captured_text = captured_at.isoformat(timespec="seconds")
    candidate_rows = [asdict(item) for item in candidates]
    eligible_recommendation_symbols = {
        data["symbol"]
        for data in candidate_rows
        if data["grade"] in {"A", "B+", "B"}
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
                  captured_at, date, symbol, bullish_score, risk_score, grade, reasons, risk_reasons,
                  confidence_score, confidence_level, conflicts_count, conflicts, conflict_summary,
                  confidence_summary, original_entry_status, adjusted_entry_status,
                  confidence_adjustment_reason
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    data.get("confidence_score"),
                    data.get("confidence_level"),
                    data.get("conflicts_count"),
                    json.dumps(data.get("conflicts", []), ensure_ascii=False),
                    data.get("conflict_summary"),
                    data.get("confidence_summary"),
                    data.get("original_entry_status"),
                    data.get("adjusted_entry_status"),
                    data.get("confidence_adjustment_reason"),
                ),
            )
            if data["grade"] in {"A", "B+", "B"}:
                lifecycle_status = "triggered" if data["entry_status"] == "executable" else "observed"
                trigger_time = captured_text if data["entry_status"] == "executable" else None
                trigger_price = data["last_price"] if data["entry_status"] == "executable" else data["trigger_price"]
                trigger_reason = "initial_executable" if data["entry_status"] == "executable" else None
                conn.execute(
                    """
                    INSERT INTO recommendations (
                      date, symbol, first_seen_at, latest_seen_at, grade, bullish_score, risk_score,
                      entry_status, lifecycle_status, observed_at, trigger_time, trigger_price,
                      trigger_reason, stop_loss, target_price, signal_price,
                      confidence_score, confidence_level, conflicts_count, conflicts, conflict_summary,
                      confidence_summary, original_entry_status, adjusted_entry_status,
                      confidence_adjustment_reason
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(date, symbol) DO UPDATE SET
                      latest_seen_at=excluded.latest_seen_at,
                      grade=excluded.grade,
                      bullish_score=excluded.bullish_score,
                      risk_score=excluded.risk_score,
                      entry_status=CASE
                        WHEN recommendations.lifecycle_status = 'observed'
                         AND recommendations.trigger_time IS NULL
                         AND excluded.entry_status != 'executable'
                          THEN excluded.entry_status
                        ELSE recommendations.entry_status
                      END,
                      lifecycle_status=CASE
                        WHEN recommendations.lifecycle_status IN ('triggered', 'stopped', 'hit_target', 'closed', 'expired')
                          THEN recommendations.lifecycle_status
                        WHEN excluded.entry_status = 'executable'
                          THEN 'triggered'
                        ELSE recommendations.lifecycle_status
                      END,
                      trigger_time=CASE
                        WHEN recommendations.trigger_time IS NOT NULL THEN recommendations.trigger_time
                        WHEN excluded.entry_status = 'executable' THEN excluded.trigger_time
                        ELSE recommendations.trigger_time
                      END,
                      trigger_price=CASE
                        WHEN recommendations.trigger_time IS NOT NULL THEN recommendations.trigger_price
                        WHEN excluded.entry_status = 'executable' THEN excluded.signal_price
                        ELSE excluded.trigger_price
                      END,
                      trigger_reason=CASE
                        WHEN recommendations.trigger_reason IS NOT NULL THEN recommendations.trigger_reason
                        WHEN excluded.entry_status = 'executable' THEN excluded.trigger_reason
                        ELSE recommendations.trigger_reason
                      END,
                      stop_loss=excluded.stop_loss,
                      target_price=excluded.target_price,
                      confidence_score=excluded.confidence_score,
                      confidence_level=excluded.confidence_level,
                      conflicts_count=excluded.conflicts_count,
                      conflicts=excluded.conflicts,
                      conflict_summary=excluded.conflict_summary,
                      confidence_summary=excluded.confidence_summary,
                      original_entry_status=excluded.original_entry_status,
                      adjusted_entry_status=excluded.adjusted_entry_status,
                      confidence_adjustment_reason=excluded.confidence_adjustment_reason
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
                        lifecycle_status,
                        captured_text,
                        trigger_time,
                        trigger_price,
                        trigger_reason,
                        data["stop_loss"],
                        data["target_price"],
                        data["last_price"],
                        data.get("confidence_score"),
                        data.get("confidence_level"),
                        data.get("conflicts_count"),
                        json.dumps(data.get("conflicts", []), ensure_ascii=False),
                        data.get("conflict_summary"),
                        data.get("confidence_summary"),
                        data.get("original_entry_status"),
                        data.get("adjusted_entry_status"),
                        data.get("confidence_adjustment_reason"),
                    ),
                )


def save_us_symbols(conn: sqlite3.Connection, rows: Iterable[dict]) -> None:
    with conn:
        for row in rows:
            conn.execute(
                """
                INSERT INTO us_symbols (
                  symbol, name_en, name_zh, short_name_zh, sector_en, sector_zh,
                  industry_en, industry_zh, description_zh, is_etf, is_active,
                  created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(symbol) DO UPDATE SET
                  name_en=excluded.name_en,
                  name_zh=excluded.name_zh,
                  short_name_zh=excluded.short_name_zh,
                  sector_en=excluded.sector_en,
                  sector_zh=excluded.sector_zh,
                  industry_en=excluded.industry_en,
                  industry_zh=excluded.industry_zh,
                  description_zh=excluded.description_zh,
                  is_etf=excluded.is_etf,
                  is_active=excluded.is_active,
                  updated_at=excluded.updated_at
                """,
                (
                    row["symbol"],
                    row["name_en"],
                    row["name_zh"],
                    row["short_name_zh"],
                    row["sector_en"],
                    row["sector_zh"],
                    row["industry_en"],
                    row["industry_zh"],
                    row["description_zh"],
                    int(row["is_etf"]),
                    int(row["is_active"]),
                    row["created_at"],
                    row["updated_at"],
                ),
            )


def save_us_candidates(
    conn: sqlite3.Connection,
    captured_at: datetime,
    candidates: Iterable[object],
    market_session: str,
) -> None:
    date_text = captured_at.strftime("%Y-%m-%d")
    captured_text = captured_at.isoformat(timespec="seconds")
    rows = [asdict(item) for item in candidates]
    eligible_symbols = {row["symbol"] for row in rows if row["grade"] in {"A", "B+", "B"}}
    with conn:
        _delete_stale_recommendations(conn, date_text, eligible_symbols, market="US")
        for data in rows:
            conn.execute(
                """
                INSERT OR REPLACE INTO us_candidates (
                  captured_at, date, symbol, latest_price, previous_close, open, high, low,
                  volume, change_pct, volume_ratio, vwap, above_vwap, premarket_high,
                  break_premarket_high, break_previous_high, break_opening_range_high,
                  opening_range_high, bullish_score, risk_score, grade, entry_status,
                  lifecycle_status, trigger_price, stop_loss, target_price, reasons,
                  risk_reasons, market_status, confidence_score, confidence_level,
                  conflicts_count, conflicts, conflict_summary, confidence_summary,
                  original_entry_status, adjusted_entry_status, confidence_adjustment_reason
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    captured_text,
                    date_text,
                    data["symbol"],
                    data["latest_price"],
                    data["previous_close"],
                    data["open"],
                    data["high"],
                    data["low"],
                    data["volume"],
                    data["change_pct"],
                    data["volume_ratio"],
                    data["vwap"],
                    int(data["above_vwap"]),
                    data["premarket_high"],
                    int(data["break_premarket_high"]),
                    int(data["break_previous_high"]),
                    int(data["break_opening_range_high"]),
                    data["opening_range_high"],
                    data["bullish_score"],
                    data["risk_score"],
                    data["grade"],
                    data["entry_status"],
                    data["lifecycle_status"],
                    data["trigger_price"],
                    data["stop_loss"],
                    data["target_price"],
                    json.dumps(data["reasons"], ensure_ascii=False),
                    json.dumps(data["risk_reasons"], ensure_ascii=False),
                    data["market_status"],
                    data.get("confidence_score"),
                    data.get("confidence_level"),
                    data.get("conflicts_count"),
                    json.dumps(data.get("conflicts", []), ensure_ascii=False),
                    data.get("conflict_summary"),
                    data.get("confidence_summary"),
                    data.get("original_entry_status"),
                    data.get("adjusted_entry_status"),
                    data.get("confidence_adjustment_reason"),
                ),
            )
            if data["grade"] in {"A", "B+", "B"}:
                _upsert_us_recommendation(conn, date_text, captured_text, data, market_session)


def _upsert_us_recommendation(
    conn: sqlite3.Connection,
    date_text: str,
    captured_text: str,
    data: dict,
    market_session: str,
) -> None:
    existing = conn.execute(
        "SELECT * FROM recommendations WHERE market = 'US' AND date = ? AND symbol = ?",
        (date_text, data["symbol"]),
    ).fetchone()
    lifecycle_status = "triggered" if data["entry_status"] == "executable" else "observed"
    trigger_time = captured_text if data["entry_status"] == "executable" else None
    trigger_price = data["latest_price"] if data["entry_status"] == "executable" else data["trigger_price"]
    trigger_reason = "美股條件即時成立" if data["entry_status"] == "executable" else None
    if existing and (existing["lifecycle_status"] or "observed") == "observed":
        trigger = _us_trigger_from_candidate(existing["entry_status"], data)
        if trigger:
            lifecycle_status = "triggered"
            trigger_time = captured_text
            trigger_price = data["latest_price"]
            trigger_reason = trigger
        elif market_session == "closed":
            lifecycle_status = "expired"
    elif existing:
        lifecycle_status = existing["lifecycle_status"]
        trigger_time = existing["trigger_time"]
        trigger_price = existing["trigger_price"]
        trigger_reason = existing["trigger_reason"]

    expired_at = captured_text if lifecycle_status == "expired" else None
    closed_at = captured_text if lifecycle_status in {"closed", "expired"} else None
    conn.execute(
        """
        INSERT INTO recommendations (
          market, date, symbol, first_seen_at, latest_seen_at, grade, bullish_score, risk_score,
          entry_status, lifecycle_status, observed_at, trigger_time, trigger_price,
          trigger_reason, stop_loss, target_price, signal_price, confidence_score, confidence_level,
          conflicts_count, conflicts, conflict_summary, confidence_summary, original_entry_status,
          adjusted_entry_status, confidence_adjustment_reason, expired_at, closed_at
        )
        VALUES ('US', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(date, symbol) DO UPDATE SET
          market='US',
          latest_seen_at=excluded.latest_seen_at,
          grade=excluded.grade,
          bullish_score=excluded.bullish_score,
          risk_score=excluded.risk_score,
          entry_status=CASE
            WHEN recommendations.lifecycle_status = 'observed' THEN excluded.entry_status
            ELSE recommendations.entry_status
          END,
          lifecycle_status=excluded.lifecycle_status,
          trigger_time=COALESCE(recommendations.trigger_time, excluded.trigger_time),
          trigger_price=COALESCE(recommendations.trigger_price, excluded.trigger_price),
          trigger_reason=COALESCE(recommendations.trigger_reason, excluded.trigger_reason),
          stop_loss=excluded.stop_loss,
          target_price=excluded.target_price,
          confidence_score=excluded.confidence_score,
          confidence_level=excluded.confidence_level,
          conflicts_count=excluded.conflicts_count,
          conflicts=excluded.conflicts,
          conflict_summary=excluded.conflict_summary,
          confidence_summary=excluded.confidence_summary,
          original_entry_status=excluded.original_entry_status,
          adjusted_entry_status=excluded.adjusted_entry_status,
          confidence_adjustment_reason=excluded.confidence_adjustment_reason,
          expired_at=COALESCE(recommendations.expired_at, excluded.expired_at),
          closed_at=COALESCE(recommendations.closed_at, excluded.closed_at)
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
            lifecycle_status,
            captured_text,
            trigger_time,
            trigger_price,
            trigger_reason,
            data["stop_loss"],
            data["target_price"],
            data["latest_price"],
            data.get("confidence_score"),
            data.get("confidence_level"),
            data.get("conflicts_count"),
            json.dumps(data.get("conflicts", []), ensure_ascii=False),
            data.get("conflict_summary"),
            data.get("confidence_summary"),
            data.get("original_entry_status"),
            data.get("adjusted_entry_status"),
            data.get("confidence_adjustment_reason"),
            expired_at,
            closed_at,
        ),
    )
    if lifecycle_status == "triggered":
        _upsert_us_backtest(conn, date_text, captured_text, data, lifecycle_status, trigger_time, trigger_price)
    elif lifecycle_status == "expired":
        conn.execute(
            """
            INSERT OR REPLACE INTO backtest_results (
              market, date, symbol, lifecycle_status, entry_status, expired_without_trigger, outcome
            )
            VALUES ('US', ?, ?, ?, ?, 1, ?)
            """,
            (date_text, data["symbol"], lifecycle_status, data["entry_status"], "未觸發過期"),
        )


def _upsert_us_backtest(
    conn: sqlite3.Connection,
    date_text: str,
    captured_text: str,
    data: dict,
    lifecycle_status: str,
    trigger_time: Optional[str],
    trigger_price: Optional[float],
) -> None:
    entry_price = float(trigger_price or data["latest_price"])
    max_price = float(data["high"])
    min_price = float(data["low"])
    close_price = float(data["latest_price"])
    hit_target = bool(data["target_price"] and max_price >= float(data["target_price"]))
    hit_stop = bool(data["stop_loss"] and min_price <= float(data["stop_loss"]))
    outcome = "達標" if hit_target else "停損" if hit_stop else "追蹤中"
    max_gain = ((max_price - entry_price) / entry_price * 100) if entry_price else 0.0
    max_drawdown = ((min_price - entry_price) / entry_price * 100) if entry_price else 0.0
    return_pct = ((close_price - entry_price) / entry_price * 100) if entry_price else 0.0
    conn.execute(
        """
        INSERT OR REPLACE INTO backtest_results (
          market, date, symbol, lifecycle_status, entry_status, trigger_time, trigger_price,
          entry_price, max_price_after_signal, min_price_after_signal,
          max_price_after_trigger, min_price_after_trigger, close_price,
          same_day_high, same_day_low, same_day_close,
          max_gain_after_recommend, max_drawdown_after_recommend,
          max_gain_after_trigger, max_drawdown_after_trigger,
          hit_target, hit_stop, hit_stop_loss, expired_without_trigger, outcome, return_pct,
          confidence_score, confidence_level, entry_status_at_signal
        )
        VALUES ('US', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?)
        """,
        (
            date_text,
            data["symbol"],
            lifecycle_status,
            data["entry_status"],
            trigger_time or captured_text,
            round(entry_price, 2),
            round(entry_price, 2),
            round(max_price, 2),
            round(min_price, 2),
            round(max_price, 2),
            round(min_price, 2),
            round(close_price, 2),
            round(max_price, 2),
            round(min_price, 2),
            round(close_price, 2),
            round(max_gain, 2),
            round(max_drawdown, 2),
            round(max_gain, 2),
            round(max_drawdown, 2),
            int(hit_target),
            int(hit_stop),
            int(hit_stop),
            outcome,
            round(return_pct, 2),
            data.get("confidence_score"),
            data.get("confidence_level"),
            data.get("original_entry_status") or data.get("entry_status"),
        ),
    )


def _us_trigger_from_candidate(entry_status: str, data: dict) -> Optional[str]:
    if data.get("grade") == "B+":
        tracker = evaluate_b_plus_trigger(
            market="US",
            symbol=data["symbol"],
            name_zh=data.get("name_zh", data["symbol"]),
            name_en=data.get("name_en", ""),
            current_price=data.get("latest_price"),
            vwap=data.get("vwap"),
            volume_ratio=data.get("volume_ratio"),
            entry_status=entry_status,
            lifecycle_status=data.get("lifecycle_status", "observed"),
            trigger_price=data.get("trigger_price"),
            confidence_score=data.get("confidence_score"),
            confidence_summary=data.get("confidence_summary", ""),
        )
        return tracker["trigger_reason"] if tracker["trigger_readiness"] == "ready" else None
    if entry_status == "wait_vwap" and data["above_vwap"]:
        return "站回 VWAP"
    if entry_status == "wait_volume" and float(data["volume_ratio"] or 0) >= 1.2:
        return f"量比放大至 {float(data['volume_ratio']):.2f}x"
    if entry_status == "wait_breakout" and (
        data["break_previous_high"] or data["break_premarket_high"] or data["break_opening_range_high"]
    ):
        return "突破關鍵高點"
    if entry_status == "wait_pullback" and data["above_vwap"]:
        distance = ((data["latest_price"] - data["vwap"]) / data["vwap"] * 100) if data["vwap"] else 0
        if distance <= 1.2:
            return "回測 VWAP 後未跌破"
    return None


def update_backtests(conn: sqlite3.Connection, captured_at: datetime, intraday_bars_by_symbol: dict) -> None:
    cutoff_text = captured_at.strftime("%Y-%m-%d")
    rows = conn.execute(
        """
        SELECT *
        FROM recommendations
        WHERE market = 'TW'
          AND date <= ?
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
            snapshot = _latest_intraday_snapshot(conn, row["symbol"], row["date"], captured_at)
            lifecycle_status = row["lifecycle_status"] or "observed"
            trigger_time = _parse_datetime(row["trigger_time"])
            trigger_price = row["trigger_price"]
            trigger_reason = row["trigger_reason"]
            if lifecycle_status == "observed":
                trigger = _trigger_from_observation(row, snapshot, captured_at)
                if trigger is not None:
                    lifecycle_status = "triggered"
                    trigger_time, trigger_price, trigger_reason = trigger
                    conn.execute(
                        """
                        UPDATE recommendations
                        SET lifecycle_status = ?, trigger_time = ?, trigger_price = ?,
                            trigger_reason = ?
                        WHERE market = 'TW' AND date = ? AND symbol = ?
                        """,
                        (
                            lifecycle_status,
                            trigger_time.isoformat(timespec="seconds"),
                            round(trigger_price, 2),
                            trigger_reason,
                            row["date"],
                            row["symbol"],
                        ),
                    )
                elif _is_after_market_close(captured_at, recommendation_date):
                    lifecycle_status = "expired"
                    expired_at = captured_at.isoformat(timespec="seconds")
                    conn.execute(
                        """
                        UPDATE recommendations
                        SET lifecycle_status = ?, expired_at = ?
                        WHERE market = 'TW' AND date = ? AND symbol = ?
                        """,
                        (lifecycle_status, expired_at, row["date"], row["symbol"]),
                    )
                    _upsert_expired_backtest(conn, row, lifecycle_status)
                    continue

            if trigger_time is None:
                continue
            trigger_time = trigger_time.replace(tzinfo=None)
            relevant = [bar for bar in same_day_bars if bar.timestamp >= trigger_time]
            if not relevant:
                relevant = [bar for bar in same_day_bars if bar.timestamp >= signal_time] or same_day_bars
            entry_price = trigger_price or row["signal_price"] or relevant[0].close
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
                outcome = "收盤結算" if _is_after_market_close(captured_at, recommendation_date) else "追蹤中"
            if hit_target:
                lifecycle_status = "hit_target"
            elif hit_stop_loss:
                lifecycle_status = "stopped"
            elif _is_after_market_close(captured_at, recommendation_date):
                lifecycle_status = "closed"
            closed_at = captured_at.isoformat(timespec="seconds") if lifecycle_status in {"hit_target", "stopped", "closed"} else row["closed_at"]
            max_gain = ((max_price - entry_price) / entry_price * 100) if entry_price else 0
            max_drawdown = ((min_price - entry_price) / entry_price * 100) if entry_price else 0
            return_pct = ((close_price - entry_price) / entry_price * 100) if entry_price else 0
            conn.execute(
                """
                UPDATE recommendations
                SET lifecycle_status = ?, closed_at = ?
                WHERE market = 'TW' AND date = ? AND symbol = ?
                """,
                (lifecycle_status, closed_at, row["date"], row["symbol"]),
            )
            conn.execute(
                """
                INSERT OR REPLACE INTO backtest_results (
                  date, symbol, lifecycle_status, entry_status, trigger_time, trigger_price,
                  entry_price, max_price_after_signal, min_price_after_signal,
                  max_price_after_trigger, min_price_after_trigger, close_price,
                  same_day_high, same_day_low, same_day_close,
                  max_gain_after_recommend, max_drawdown_after_recommend,
                  max_gain_after_trigger, max_drawdown_after_trigger,
                  hit_target, hit_stop, hit_stop_loss, expired_without_trigger, outcome, return_pct,
                  confidence_score, confidence_level, entry_status_at_signal
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row["date"],
                    row["symbol"],
                    lifecycle_status,
                    row["entry_status"],
                    trigger_time.isoformat(timespec="seconds"),
                    round(entry_price, 2),
                    round(entry_price, 2),
                    round(max_price, 2),
                    round(min_price, 2),
                    round(max_price, 2),
                    round(min_price, 2),
                    round(close_price, 2),
                    round(same_day_high, 2),
                    round(same_day_low, 2),
                    round(close_price, 2),
                    round(max_gain, 2),
                    round(max_drawdown, 2),
                    round(max_gain, 2),
                    round(max_drawdown, 2),
                    int(hit_target),
                    int(hit_stop_loss),
                    int(hit_stop_loss),
                    0,
                    outcome,
                    round(return_pct, 2),
                    row["confidence_score"],
                    row["confidence_level"],
                    row["original_entry_status"] or row["entry_status"],
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
          CASE ls.grade WHEN 'A' THEN 1 WHEN 'B+' THEN 2 WHEN 'B' THEN 3 WHEN 'C' THEN 4 ELSE 5 END,
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
        LEFT JOIN backtest_results b ON b.market = r.market AND b.date = r.date AND b.symbol = r.symbol
        WHERE r.market = 'TW'
          AND r.symbol = ?
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


def latest_us_candidates(conn: sqlite3.Connection, limit: int = 50) -> List[sqlite3.Row]:
    captured = conn.execute("SELECT MAX(captured_at) AS captured_at FROM us_candidates").fetchone()["captured_at"]
    if not captured:
        return []
    return conn.execute(
        """
        SELECT uc.*, us.name_en, us.name_zh, us.short_name_zh, us.sector_en, us.sector_zh,
               us.industry_en, us.industry_zh, us.description_zh, us.is_etf,
               r.lifecycle_status AS recommendation_lifecycle_status,
               r.trigger_time, r.trigger_reason
        FROM us_candidates uc
        JOIN us_symbols us ON us.symbol = uc.symbol
        LEFT JOIN recommendations r ON r.market = 'US' AND r.date = uc.date AND r.symbol = uc.symbol
        WHERE uc.captured_at = ?
        ORDER BY
          CASE uc.grade WHEN 'A' THEN 1 WHEN 'B+' THEN 2 WHEN 'B' THEN 3 WHEN 'C' THEN 4 ELSE 5 END,
          uc.bullish_score DESC,
          uc.risk_score ASC
        LIMIT ?
        """,
        (captured, limit),
    ).fetchall()


def latest_us_symbol(conn: sqlite3.Connection, symbol: str) -> Optional[sqlite3.Row]:
    return conn.execute(
        """
        SELECT uc.*, us.name_en, us.name_zh, us.short_name_zh, us.sector_en, us.sector_zh,
               us.industry_en, us.industry_zh, us.description_zh, us.is_etf,
               r.lifecycle_status AS recommendation_lifecycle_status,
               r.trigger_time, r.trigger_price, r.trigger_reason
        FROM us_candidates uc
        JOIN us_symbols us ON us.symbol = uc.symbol
        LEFT JOIN recommendations r ON r.market = 'US' AND r.date = uc.date AND r.symbol = uc.symbol
        WHERE uc.symbol = ?
        ORDER BY uc.captured_at DESC
        LIMIT 1
        """,
        (symbol.upper(),),
    ).fetchone()


def backtest_summary(conn: sqlite3.Connection, day: Optional[date] = None, market: str = "TW") -> dict:
    params: List[str] = [market]
    where = "WHERE market = ?"
    if day is not None:
        where += " AND date = ?"
        params.append(day.strftime("%Y-%m-%d"))
    recommendation_count = conn.execute(f"SELECT COUNT(*) AS total FROM recommendations {where}", params).fetchone()["total"]
    lifecycle_rows = conn.execute(
        f"SELECT lifecycle_status, COUNT(*) AS total FROM recommendations {where} GROUP BY lifecycle_status",
        params,
    ).fetchall()
    rows = conn.execute(f"SELECT * FROM backtest_results {where}", params).fetchall()
    status_rows = conn.execute(
        f"""
        SELECT
          r.entry_status,
          r.grade,
          r.lifecycle_status,
          r.symbol,
          b.outcome,
          b.return_pct,
          b.trigger_time,
          b.expired_without_trigger,
          b.max_gain_after_trigger,
          b.max_drawdown_after_trigger
        FROM recommendations r
        LEFT JOIN backtest_results b ON b.market = r.market AND b.date = r.date AND b.symbol = r.symbol
        {where.replace("market", "r.market").replace("date", "r.date")}
        """,
        params,
    ).fetchall()
    trackable_rows = [row for row in rows if row["trigger_time"]]
    trackable_count = len(trackable_rows)
    triggered_backtest_count = sum(1 for row in rows if row["trigger_time"])
    target = sum(1 for row in rows if row["outcome"] == "達標")
    stop = sum(1 for row in rows if row["outcome"] == "停損")
    avg_return = round(sum(row["return_pct"] or 0 for row in trackable_rows) / trackable_count, 2) if trackable_count else 0.0
    lifecycle_counts = {row["lifecycle_status"] or "observed": row["total"] for row in lifecycle_rows}
    return {
        "total": recommendation_count,
        "recommendation_count": recommendation_count,
        "trackable_count": trackable_count,
        "triggered_backtest_count": triggered_backtest_count,
        "observed_count": int(lifecycle_counts.get("observed", 0)),
        "triggered_count": int(lifecycle_counts.get("triggered", 0)),
        "expired_count": int(lifecycle_counts.get("expired", 0)),
        "closed_count": int(lifecycle_counts.get("closed", 0)),
        "stopped_count": int(lifecycle_counts.get("stopped", 0)),
        "hit_target_count": int(lifecycle_counts.get("hit_target", 0)),
        "target": target,
        "stop": stop,
        "avg_return": avg_return,
        "by_entry_status": _summarize_by_entry_status(status_rows),
        "by_grade": _summarize_by_grade(status_rows),
    }


def _summarize_by_entry_status(rows: Iterable[sqlite3.Row]) -> List[dict]:
    grouped: dict[str, list[sqlite3.Row]] = {}
    for row in rows:
        grouped.setdefault(row["entry_status"] or "unknown", []).append(row)

    result = []
    for status, items in grouped.items():
        tracked = [item for item in items if item["trigger_time"]]
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


def _summarize_by_grade(rows: Iterable[sqlite3.Row]) -> List[dict]:
    grouped: dict[str, list[sqlite3.Row]] = {}
    for row in rows:
        grouped.setdefault(row["grade"] or "unknown", []).append(row)

    result = []
    for grade, items in grouped.items():
        tracked = [item for item in items if item["trigger_time"]]
        total = len(items)
        triggered = len(tracked)
        expired = sum(1 for item in items if item["lifecycle_status"] == "expired" or item["expired_without_trigger"])
        target = sum(1 for item in tracked if item["outcome"] == "達標")
        stop = sum(1 for item in tracked if item["outcome"] == "停損")
        avg_return = round(sum(item["return_pct"] or 0 for item in tracked) / triggered, 2) if triggered else 0.0
        avg_max_gain = round(sum(item["max_gain_after_trigger"] or 0 for item in tracked) / triggered, 2) if triggered else 0.0
        avg_max_drawdown = round(sum(item["max_drawdown_after_trigger"] or 0 for item in tracked) / triggered, 2) if triggered else 0.0
        result.append(
            {
                "grade": grade,
                "total": total,
                "triggered": triggered,
                "expired": expired,
                "untriggered_ratio": round((total - triggered) / total * 100, 2) if total else 0.0,
                "target": target,
                "stop": stop,
                "avg_return": avg_return,
                "avg_max_gain": avg_max_gain,
                "avg_max_drawdown": avg_max_drawdown,
            }
        )
    result.sort(key=lambda item: _grade_order(item["grade"]))
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


def _grade_order(value: str) -> int:
    return {"A": 0, "B+": 1, "B": 2, "C": 3, "D": 4}.get(value, 9)


def _ensure_backtest_columns(conn: sqlite3.Connection) -> None:
    existing = {row["name"] for row in conn.execute("PRAGMA table_info(backtest_results)").fetchall()}
    columns = {
        "market": "TEXT NOT NULL DEFAULT 'TW'",
        "lifecycle_status": "TEXT",
        "entry_status": "TEXT",
        "trigger_time": "TEXT",
        "trigger_price": "REAL",
        "same_day_high": "REAL",
        "same_day_low": "REAL",
        "same_day_close": "REAL",
        "max_price_after_trigger": "REAL",
        "min_price_after_trigger": "REAL",
        "max_gain_after_recommend": "REAL",
        "max_drawdown_after_recommend": "REAL",
        "max_gain_after_trigger": "REAL",
        "max_drawdown_after_trigger": "REAL",
        "hit_stop_loss": "INTEGER",
        "expired_without_trigger": "INTEGER",
    }
    for name, column_type in columns.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE backtest_results ADD COLUMN {name} {column_type}")
    conn.execute("UPDATE backtest_results SET market = 'TW' WHERE market IS NULL OR market = ''")


def _ensure_recommendation_columns(conn: sqlite3.Connection) -> None:
    existing = {row["name"] for row in conn.execute("PRAGMA table_info(recommendations)").fetchall()}
    columns = {
        "market": "TEXT NOT NULL DEFAULT 'TW'",
        "lifecycle_status": "TEXT NOT NULL DEFAULT 'observed'",
        "observed_at": "TEXT",
        "trigger_time": "TEXT",
        "trigger_reason": "TEXT",
        "expired_at": "TEXT",
        "closed_at": "TEXT",
    }
    for name, column_type in columns.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE recommendations ADD COLUMN {name} {column_type}")
    conn.execute("UPDATE recommendations SET observed_at = first_seen_at WHERE observed_at IS NULL")
    conn.execute("UPDATE recommendations SET lifecycle_status = 'observed' WHERE lifecycle_status IS NULL OR lifecycle_status = ''")
    conn.execute("UPDATE recommendations SET market = 'TW' WHERE market IS NULL OR market = ''")


def _ensure_confidence_columns(conn: sqlite3.Connection) -> None:
    shared = {
        "confidence_score": "REAL",
        "confidence_level": "TEXT",
        "conflicts_count": "INTEGER",
        "conflicts": "TEXT",
        "conflict_summary": "TEXT",
        "confidence_summary": "TEXT",
        "original_entry_status": "TEXT",
        "adjusted_entry_status": "TEXT",
        "confidence_adjustment_reason": "TEXT",
    }
    for table in ("recommendations", "long_scores", "us_candidates"):
        _add_missing_columns(conn, table, shared)
    _add_missing_columns(
        conn,
        "backtest_results",
        {
            "confidence_score": "REAL",
            "confidence_level": "TEXT",
            "entry_status_at_signal": "TEXT",
        },
    )


def _add_missing_columns(conn: sqlite3.Connection, table: str, columns: dict[str, str]) -> None:
    existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    for name, column_type in columns.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {column_type}")


def _ensure_paper_trade_columns(conn: sqlite3.Connection) -> None:
    existing = {row["name"] for row in conn.execute("PRAGMA table_info(paper_trades)").fetchall()}
    columns = {
        "source": "TEXT NOT NULL DEFAULT 'system'",
        "is_manual": "INTEGER NOT NULL DEFAULT 0",
        "manual_reason": "TEXT",
        "created_by": "TEXT",
    }
    for name, column_type in columns.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE paper_trades ADD COLUMN {name} {column_type}")
    conn.execute("UPDATE paper_trades SET source = 'system' WHERE source IS NULL OR source = ''")
    conn.execute("UPDATE paper_trades SET is_manual = 0 WHERE is_manual IS NULL")


def _delete_stale_recommendations(
    conn: sqlite3.Connection,
    date_text: str,
    eligible_symbols: set[str],
    market: str = "TW",
) -> None:
    if not eligible_symbols:
        conn.execute("DELETE FROM recommendations WHERE market = ? AND date = ?", (market, date_text))
        conn.execute("DELETE FROM backtest_results WHERE market = ? AND date = ?", (market, date_text))
        return

    placeholders = ", ".join("?" for _ in eligible_symbols)
    params = [market, date_text, *sorted(eligible_symbols)]
    conn.execute(
        f"DELETE FROM recommendations WHERE market = ? AND date = ? AND symbol NOT IN ({placeholders})",
        params,
    )
    conn.execute(
        f"DELETE FROM backtest_results WHERE market = ? AND date = ? AND symbol NOT IN ({placeholders})",
        params,
    )


def _parse_datetime(value: str) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def _latest_intraday_snapshot(
    conn: sqlite3.Connection,
    symbol: str,
    date_text: str,
    captured_at: datetime,
) -> Optional[sqlite3.Row]:
    return conn.execute(
        """
        SELECT *
        FROM intraday_snapshots
        WHERE symbol = ?
          AND date = ?
          AND captured_at <= ?
        ORDER BY captured_at DESC
        LIMIT 1
        """,
        (symbol, date_text, captured_at.isoformat(timespec="seconds")),
    ).fetchone()


def _trigger_from_observation(
    row: sqlite3.Row,
    snapshot: Optional[sqlite3.Row],
    captured_at: datetime,
) -> Optional[tuple[datetime, float, str]]:
    entry_status = row["entry_status"]
    if entry_status == "executable":
        return captured_at, float(row["signal_price"] or row["trigger_price"] or 0), "initial_executable"
    if snapshot is None:
        return None

    last_price = snapshot["last_price"]
    if last_price is None:
        return None
    last_price = float(last_price)
    vwap = snapshot["vwap"]
    volume_ratio = snapshot["volume_ratio"]
    above_vwap = bool(snapshot["above_vwap"]) or (vwap is not None and last_price >= float(vwap))

    if row["grade"] == "B+":
        tracker = evaluate_b_plus_trigger(
            market=row["market"] or "TW",
            symbol=row["symbol"],
            name_zh=row["symbol"],
            current_price=last_price,
            vwap=vwap,
            volume_ratio=volume_ratio,
            entry_status=entry_status,
            lifecycle_status=row["lifecycle_status"] or "observed",
            trigger_price=row["trigger_price"],
            confidence_score=row["confidence_score"],
            confidence_summary=row["confidence_summary"] or "",
        )
        if tracker["trigger_readiness"] == "ready":
            return captured_at, last_price, tracker["trigger_reason"]
        return None

    if entry_status == "wait_vwap" and above_vwap:
        return captured_at, last_price, "站回VWAP"
    if entry_status == "wait_volume" and volume_ratio is not None and float(volume_ratio) >= 1.0:
        return captured_at, last_price, f"量比放大至 {float(volume_ratio):.2f}x"
    if entry_status == "wait_pullback" and above_vwap:
        return captured_at, last_price, "回測VWAP不破"
    return None


def _is_after_market_close(captured_at: datetime, recommendation_date: date) -> bool:
    if captured_at.date() > recommendation_date:
        return True
    return captured_at.date() == recommendation_date and (captured_at.hour, captured_at.minute) >= (13, 30)


def _upsert_expired_backtest(conn: sqlite3.Connection, row: sqlite3.Row, lifecycle_status: str) -> None:
    conn.execute(
        """
        INSERT OR REPLACE INTO backtest_results (
          date, symbol, lifecycle_status, entry_status, expired_without_trigger, outcome
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (row["date"], row["symbol"], lifecycle_status, row["entry_status"], 1, "未觸發過期"),
    )
