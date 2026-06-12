from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, Iterable, Optional
from zoneinfo import ZoneInfo

from stock_daytrade_system.market_clock import taiwan_market_session, us_market_session
from stock_daytrade_system.paper_config import DEFAULT_PAPER_CONFIG, PaperTradingConfig
from stock_daytrade_system.us_symbols import us_symbol_rows


PAPER_ENGINE_VERSION = "paper_trading_v1_manual_trade"


@dataclass(frozen=True)
class PaperRunSummary:
    opened: int
    closed: int
    skipped: int
    positions: int
    recommendations_scanned: int
    executable_triggered: int
    last_error: str = ""


def run_paper_trading(
    conn: sqlite3.Connection,
    now: Optional[datetime] = None,
    config: PaperTradingConfig = DEFAULT_PAPER_CONFIG,
) -> PaperRunSummary:
    captured_at = now or datetime.now(ZoneInfo("Asia/Taipei"))
    with conn:
        _ensure_accounts(conn, captured_at, config)
        price_map = _latest_price_map(conn)
        closed = _evaluate_open_positions(conn, captured_at, price_map, config)
        opened, skipped, scanned, executable_triggered = _process_recommendations(conn, captured_at, price_map, config)
        _refresh_accounts(conn, captured_at)
        _record_equity_curve(conn, captured_at)
    positions = conn.execute("SELECT COUNT(*) AS total FROM paper_positions").fetchone()["total"]
    return PaperRunSummary(
        opened=opened,
        closed=closed,
        skipped=skipped,
        positions=positions,
        recommendations_scanned=scanned,
        executable_triggered=executable_triggered,
    )


def paper_dashboard_payload(conn: sqlite3.Connection, now: Optional[datetime] = None) -> dict:
    captured_at = now or datetime.now(ZoneInfo("Asia/Taipei"))
    run = run_paper_trading(conn, captured_at)
    accounts = [dict(row) for row in conn.execute("SELECT * FROM paper_accounts ORDER BY market").fetchall()]
    positions = [
        dict(row)
        for row in conn.execute(
            """
            SELECT p.*, t.name_zh, t.name_en, t.entry_reason, t.source, t.is_manual
            FROM paper_positions p
            LEFT JOIN paper_trades t ON t.id = p.trade_id
            ORDER BY p.market, p.symbol
            """
        ).fetchall()
    ]
    trades = [
        dict(row)
        for row in conn.execute(
            """
            SELECT *
            FROM paper_trades
            ORDER BY COALESCE(entry_time, created_at) DESC, symbol
            LIMIT 80
            """
        ).fetchall()
    ]
    performance = paper_performance(conn)
    skipped_trades = [item for item in trades if item.get("status") == "skipped"]
    message = "目前尚無符合虛擬進場條件的訊號" if not positions and not [item for item in trades if item.get("status") != "skipped"] else ""
    return {
        "api_status": "ok",
        "data_source_status": "ok",
        "errors": [],
        "message": message,
        "engine_version": PAPER_ENGINE_VERSION,
        "generated_at": captured_at.isoformat(timespec="seconds"),
        "run": run.__dict__,
        "accounts": accounts,
        "positions": positions,
        "trades": trades,
        "skipped": skipped_trades,
        "skipped_trades": skipped_trades,
        "performance": performance,
        "refresh_interval_seconds": _paper_refresh_interval(captured_at),
        "disclaimer": "本系統僅供資料整理與策略回測，不構成投資建議，也不保證獲利；本頁不會送出任何真實委託。",
    }


def paper_performance(conn: sqlite3.Connection) -> dict:
    rows = conn.execute("SELECT * FROM paper_trades WHERE status != 'skipped'").fetchall()
    closed = [row for row in rows if row["status"] in {"closed", "stopped", "target_hit", "forced_exit"}]
    wins = [row for row in closed if (row["realized_pnl"] or 0) > 0]
    realized = round(sum(row["realized_pnl"] or 0 for row in closed), 2)
    accounts = conn.execute("SELECT COALESCE(SUM(unrealized_pnl), 0) AS unrealized FROM paper_accounts").fetchone()
    return {
        "total_trades": len(rows),
        "closed_trades": len(closed),
        "system_trades": sum(1 for row in rows if (row["source"] or "system") == "system"),
        "manual_trades": sum(1 for row in rows if row["source"] == "manual"),
        "win_rate": _rate(len(wins), len(closed)),
        "realized_pnl": realized,
        "unrealized_pnl": round(float(accounts["unrealized"] or 0), 2),
        "by_market": _group_performance(closed, "market"),
        "by_source": _group_performance(closed, "source"),
        "by_grade": _group_performance(closed, "grade"),
        "by_entry_status": _group_performance(closed, "entry_status"),
    }


def paper_quote(conn: sqlite3.Connection, market: str, symbol: str) -> dict:
    market = (market or "").strip().upper()
    symbol = (symbol or "").strip().upper()
    if market not in {"TW", "US"} or not symbol:
        return {
            "ok": False,
            "market": market,
            "symbol": symbol,
            "message": "目前無法取得即時行情，請自行確認虛擬進場價格。",
        }
    price_info = _latest_price_map(conn).get((market, symbol))
    if not price_info:
        price_info = _symbol_metadata(conn, market, symbol)
    ok = bool(price_info and price_info.get("price"))
    return {
        "ok": ok,
        "market": market,
        "symbol": symbol,
        "latest_price": price_info.get("price") if price_info else None,
        "vwap": price_info.get("vwap") if price_info else None,
        "name_zh": (price_info or {}).get("name_zh") or symbol,
        "name_en": (price_info or {}).get("name_en") or symbol,
        "sector": (price_info or {}).get("sector") or "",
        "entry_status": (price_info or {}).get("entry_status") or "",
        "message": "" if ok else "目前無法取得即時行情，請自行確認虛擬進場價格。",
    }


def create_manual_trade(
    conn: sqlite3.Connection,
    payload: dict,
    now: Optional[datetime] = None,
    config: PaperTradingConfig = DEFAULT_PAPER_CONFIG,
) -> dict:
    captured_at = now or datetime.now(ZoneInfo("Asia/Taipei"))
    market = str(payload.get("market") or "").strip().upper()
    symbol = str(payload.get("symbol") or "").strip().upper()
    entry_price = _to_float(payload.get("entry_price"))
    quantity = _to_float(payload.get("quantity"))
    stop_loss = _to_float(payload.get("stop_loss"))
    target_price = _to_float(payload.get("target_price"))
    entry_reason = str(payload.get("entry_reason") or payload.get("manual_reason") or "手動虛擬交易").strip()
    with conn:
        _ensure_accounts(conn, captured_at, config)
    validation = _validate_manual_trade(conn, market, symbol, entry_price, quantity, stop_loss, target_price, config)
    if validation:
        return {"ok": False, "message": validation, "trade": None}

    with conn:
        price_info = paper_quote(conn, market, symbol)
        account = _account(conn, market)
        value = round(entry_price * quantity, 2)
        timestamp = captured_at.isoformat(timespec="seconds")
        trade_id = f"manual|{market}|{symbol}|{timestamp.replace(':', '').replace('-', '').replace('+', '').replace('.', '')}"
        name_zh = str(payload.get("name_zh") or price_info.get("name_zh") or symbol).strip() or symbol
        name_en = str(payload.get("name_en") or price_info.get("name_en") or symbol).strip() or symbol
        conn.execute(
            """
            INSERT INTO paper_trades (
              id, account_id, recommendation_id, market, symbol, name_zh, name_en,
              source, is_manual, manual_reason, created_by, side, status,
              grade, entry_status, lifecycle_status, entry_time, entry_price, entry_reason,
              quantity, position_value, stop_loss, target_price, max_favorable_excursion,
              max_adverse_excursion, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, 'manual', 1, ?, 'user', 'long', 'open',
                    'manual', 'manual', 'manual', ?, ?, ?, ?, ?, ?, ?, 0, 0, ?, ?)
            """,
            (
                trade_id,
                account["id"],
                trade_id,
                market,
                symbol,
                name_zh,
                name_en,
                entry_reason,
                timestamp,
                round(entry_price, 2),
                entry_reason,
                quantity,
                value,
                stop_loss,
                target_price,
                timestamp,
                timestamp,
            ),
        )
        conn.execute(
            """
            INSERT INTO paper_positions (
              id, account_id, trade_id, market, symbol, quantity, entry_price,
              current_price, market_value, unrealized_pnl, unrealized_pnl_pct,
              stop_loss, target_price, highest_price_since_entry, lowest_price_since_entry,
              opened_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"{account['id']}|{trade_id}",
                account["id"],
                trade_id,
                market,
                symbol,
                quantity,
                round(entry_price, 2),
                round(entry_price, 2),
                value,
                stop_loss,
                target_price,
                round(entry_price, 2),
                round(entry_price, 2),
                timestamp,
                timestamp,
            ),
        )
        conn.execute(
            "UPDATE paper_accounts SET cash_balance = cash_balance - ?, updated_at = ? WHERE id = ?",
            (value, timestamp, account["id"]),
        )
        _refresh_accounts(conn, captured_at)
        _record_equity_curve(conn, captured_at)
    trade = conn.execute("SELECT * FROM paper_trades WHERE id = ?", (trade_id,)).fetchone()
    return {
        "ok": True,
        "message": f"已建立手動虛擬買進：{symbol}｜{name_zh}",
        "trade": dict(trade) if trade else None,
        "quote_warning": price_info.get("message") if not price_info.get("ok") else "",
    }


def close_manual_trade(conn: sqlite3.Connection, payload: dict, now: Optional[datetime] = None) -> dict:
    captured_at = now or datetime.now(ZoneInfo("Asia/Taipei"))
    trade_id = str(payload.get("trade_id") or "").strip()
    exit_price = _to_float(payload.get("exit_price"))
    if not trade_id:
        return {"ok": False, "message": "trade_id 不可空白", "last_close_trade_status": "failed"}
    with conn:
        position = conn.execute("SELECT * FROM paper_positions WHERE trade_id = ?", (trade_id,)).fetchone()
        if not position:
            return {"ok": False, "message": "找不到可平倉的 open position", "last_close_trade_status": "failed"}
        if exit_price <= 0:
            price_info = _latest_price_map(conn).get((position["market"], position["symbol"]))
            exit_price = float((price_info or {}).get("price") or position["current_price"] or 0)
        if exit_price <= 0:
            return {"ok": False, "message": "平倉價格必須大於 0", "last_close_trade_status": "failed"}
        _close_position(conn, position, exit_price, "manual_close", captured_at)
        _refresh_accounts(conn, captured_at)
        _record_equity_curve(conn, captured_at)
    trade = conn.execute("SELECT * FROM paper_trades WHERE id = ?", (trade_id,)).fetchone()
    return {
        "ok": True,
        "message": f"已手動平倉：{trade['symbol']}｜{trade['name_zh']}",
        "trade": dict(trade) if trade else None,
        "last_close_trade_status": "success",
    }


def _ensure_accounts(conn: sqlite3.Connection, now: datetime, config: PaperTradingConfig) -> None:
    timestamp = now.isoformat(timespec="seconds")
    accounts = [
        ("TW", "TW", "TWD", config.initial_cash_tw),
        ("US", "US", "USD", config.initial_cash_us),
    ]
    for account_id, market, currency, cash in accounts:
        conn.execute(
            """
            INSERT INTO paper_accounts (
              id, market, currency, initial_cash, cash_balance, equity,
              realized_pnl, unrealized_pnl, max_drawdown, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, 0, 0, 0, ?, ?)
            ON CONFLICT(id) DO NOTHING
            """,
            (account_id, market, currency, cash, cash, cash, timestamp, timestamp),
        )


def _process_recommendations(
    conn: sqlite3.Connection,
    now: datetime,
    price_map: Dict[tuple[str, str], dict],
    config: PaperTradingConfig,
) -> tuple[int, int, int, int]:
    opened = 0
    skipped = 0
    rows = conn.execute(
        """
        SELECT rowid AS row_id, *
        FROM recommendations
        WHERE market IN ('TW', 'US')
        ORDER BY latest_seen_at DESC
        """
    ).fetchall()
    scanned = len(rows)
    executable_triggered = sum(
        1
        for row in rows
        if row["grade"] in {"A", "B"}
        and row["entry_status"] not in {"high_risk", "avoid"}
        and (row["entry_status"] == "executable" or row["lifecycle_status"] == "triggered")
    )
    for row in rows:
        recommendation_id = _recommendation_id(row)
        if _trade_exists(conn, recommendation_id):
            continue
        market = row["market"] or "TW"
        price_info = price_map.get((market, row["symbol"]))
        decision = _entry_decision(conn, row, price_info, now, config)
        if decision:
            _insert_skipped_trade(conn, row, recommendation_id, price_info, now, decision)
            skipped += 1
            continue
        _open_trade(conn, row, recommendation_id, price_info, now, config)
        opened += 1
    return opened, skipped, scanned, executable_triggered


def empty_paper_dashboard_payload(conn: sqlite3.Connection, now: Optional[datetime] = None, last_error: str = "") -> dict:
    captured_at = now or datetime.now(ZoneInfo("Asia/Taipei"))
    with conn:
        _ensure_accounts(conn, captured_at, DEFAULT_PAPER_CONFIG)
        _refresh_accounts(conn, captured_at)
    accounts = [dict(row) for row in conn.execute("SELECT * FROM paper_accounts ORDER BY market").fetchall()]
    performance = paper_performance(conn)
    run = PaperRunSummary(
        opened=0,
        closed=0,
        skipped=0,
        positions=0,
        recommendations_scanned=0,
        executable_triggered=0,
        last_error=last_error,
    )
    return {
        "api_status": "degraded" if last_error else "ok",
        "data_source_status": "degraded" if last_error else "ok",
        "errors": [last_error] if last_error else [],
        "message": "目前尚無符合虛擬進場條件的訊號",
        "engine_version": PAPER_ENGINE_VERSION,
        "generated_at": captured_at.isoformat(timespec="seconds"),
        "run": run.__dict__,
        "accounts": accounts,
        "positions": [],
        "trades": [],
        "skipped": [],
        "skipped_trades": [],
        "performance": performance,
        "refresh_interval_seconds": _paper_refresh_interval(captured_at),
        "disclaimer": "本系統僅供資料整理與策略回測，不構成投資建議，也不保證獲利；本頁不會送出任何真實委託。",
    }


def _validate_manual_trade(
    conn: sqlite3.Connection,
    market: str,
    symbol: str,
    entry_price: float,
    quantity: float,
    stop_loss: float,
    target_price: float,
    config: PaperTradingConfig,
) -> str:
    if market not in {"TW", "US"}:
        return "市場必須是 TW 或 US"
    if not symbol:
        return "股票代號不可空白"
    if entry_price <= 0:
        return "進場價格必須大於 0"
    if quantity <= 0:
        return "數量必須大於 0"
    if stop_loss <= 0 or stop_loss >= entry_price:
        return "停損價必須低於進場價"
    if target_price <= entry_price:
        return "停利價必須高於進場價"
    if _open_position_exists(conn, market, symbol):
        return "已有同一檔 open position，不能重複開倉"
    if _open_position_count(conn, market) >= config.max_open_positions:
        return "同市場持倉已達上限"
    account = _account(conn, market)
    if account is None:
        return "找不到虛擬帳戶"
    if entry_price * quantity > float(account["cash_balance"] or 0):
        return "現金餘額不足"
    return ""


def _entry_decision(
    conn: sqlite3.Connection,
    row: sqlite3.Row,
    price_info: Optional[dict],
    now: datetime,
    config: PaperTradingConfig,
) -> Optional[str]:
    market = row["market"] or "TW"
    entry_status = row["entry_status"]
    lifecycle = row["lifecycle_status"] or "observed"
    grade = row["grade"]
    if market == "US" and us_market_session(now).session != "regular":
        return "market_closed"
    if market == "TW" and taiwan_market_session(now).session != "regular":
        return "market_closed"
    if grade not in {"A", "B"}:
        return "not_executable"
    if entry_status in {"high_risk", "avoid"}:
        return "risk_too_high"
    if not (entry_status == "executable" or lifecycle == "triggered"):
        return "not_executable"
    if grade == "B" and lifecycle != "triggered":
        return "not_executable"
    if row["stop_loss"] is None:
        return "no_stop_loss"
    if not price_info or not price_info.get("price"):
        return "no_market_price"
    if _open_position_exists(conn, market, row["symbol"]):
        return "duplicate_position"
    if _open_position_count(conn, market) >= config.max_open_positions:
        return "too_many_positions"
    sector = price_info.get("sector") or "unknown"
    if _open_sector_count(conn, market, sector) >= config.max_positions_per_sector:
        return "sector_limit"
    quantity = _position_quantity(conn, row, price_info, config)
    if quantity <= 0:
        return "insufficient_cash"
    return None


def _open_trade(
    conn: sqlite3.Connection,
    row: sqlite3.Row,
    recommendation_id: str,
    price_info: dict,
    now: datetime,
    config: PaperTradingConfig,
) -> None:
    account = _account(conn, row["market"])
    entry_price = _entry_price(row, price_info)
    quantity = _position_quantity(conn, row, price_info, config)
    value = round(entry_price * quantity, 2)
    timestamp = now.isoformat(timespec="seconds")
    trade_id = recommendation_id
    conn.execute(
        """
        INSERT INTO paper_trades (
          id, account_id, recommendation_id, market, symbol, name_zh, name_en, side, status,
          source, is_manual, grade, entry_status, lifecycle_status, entry_time, entry_price, entry_reason,
          quantity, position_value, stop_loss, target_price, max_favorable_excursion,
          max_adverse_excursion, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, 'long', 'open', 'system', 0, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, ?, ?)
        """,
        (
            trade_id,
            account["id"],
            recommendation_id,
            row["market"],
            row["symbol"],
            price_info.get("name_zh") or row["symbol"],
            price_info.get("name_en") or row["symbol"],
            row["grade"],
            row["entry_status"],
            row["lifecycle_status"],
            timestamp,
            round(entry_price, 2),
            _entry_reason(row),
            quantity,
            value,
            row["stop_loss"],
            row["target_price"],
            timestamp,
            timestamp,
        ),
    )
    conn.execute(
        """
        INSERT INTO paper_positions (
          id, account_id, trade_id, market, symbol, quantity, entry_price,
          current_price, market_value, unrealized_pnl, unrealized_pnl_pct,
          stop_loss, target_price, highest_price_since_entry, lowest_price_since_entry,
          opened_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, ?, ?, ?, ?, ?, ?)
        """,
        (
            f"{account['id']}|{trade_id}",
            account["id"],
            trade_id,
            row["market"],
            row["symbol"],
            quantity,
            round(entry_price, 2),
            round(entry_price, 2),
            value,
            row["stop_loss"],
            row["target_price"],
            round(entry_price, 2),
            round(entry_price, 2),
            timestamp,
            timestamp,
        ),
    )
    conn.execute(
        "UPDATE paper_accounts SET cash_balance = cash_balance - ?, updated_at = ? WHERE id = ?",
        (value, timestamp, account["id"]),
    )


def _insert_skipped_trade(
    conn: sqlite3.Connection,
    row: sqlite3.Row,
    recommendation_id: str,
    price_info: Optional[dict],
    now: datetime,
    reason: str,
) -> None:
    timestamp = now.isoformat(timespec="seconds")
    conn.execute(
        """
        INSERT INTO paper_trades (
          id, account_id, recommendation_id, market, symbol, name_zh, name_en, side, status,
          source, is_manual, grade, entry_status, lifecycle_status, skipped_reason, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, 'long', 'skipped', 'system', 0, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO NOTHING
        """,
        (
            f"skip|{recommendation_id}",
            row["market"],
            recommendation_id,
            row["market"],
            row["symbol"],
            (price_info or {}).get("name_zh") or row["symbol"],
            (price_info or {}).get("name_en") or row["symbol"],
            row["grade"],
            row["entry_status"],
            row["lifecycle_status"],
            reason,
            timestamp,
            timestamp,
        ),
    )


def _evaluate_open_positions(
    conn: sqlite3.Connection,
    now: datetime,
    price_map: Dict[tuple[str, str], dict],
    config: PaperTradingConfig,
) -> int:
    closed = 0
    rows = conn.execute("SELECT * FROM paper_positions ORDER BY opened_at").fetchall()
    for position in rows:
        price_info = price_map.get((position["market"], position["symbol"]))
        if not price_info or not price_info.get("price"):
            continue
        close_reason, exit_price = _exit_signal(position, price_info, now, config)
        _update_position_mark(conn, position, price_info, now)
        if close_reason:
            _close_position(conn, position, exit_price, close_reason, now)
            closed += 1
    return closed


def _exit_signal(
    position: sqlite3.Row,
    price_info: dict,
    now: datetime,
    config: PaperTradingConfig,
) -> tuple[Optional[str], Optional[float]]:
    price = float(price_info["price"])
    entry = float(position["entry_price"])
    stop_loss = position["stop_loss"]
    target_price = position["target_price"]
    high = max(float(position["highest_price_since_entry"] or entry), price)
    gain_from_entry = (price - entry) / entry if entry else 0.0
    pullback = (high - price) / high if high else 0.0
    if stop_loss is not None and price <= float(stop_loss):
        return "stopped", price
    if gain_from_entry <= -config.default_stop_loss_pct:
        return "stopped", price
    if price_info.get("entry_status") == "avoid":
        return "stopped", price
    if target_price is not None and price >= float(target_price):
        return "target_hit", price
    if gain_from_entry >= config.default_take_profit_pct:
        return "target_hit", price
    if gain_from_entry >= config.trailing_start_pct and pullback >= config.trailing_pullback_pct:
        return "closed", price
    if _should_force_exit(position["market"], now, config):
        return "forced_exit", price
    return None, None


def _update_position_mark(conn: sqlite3.Connection, position: sqlite3.Row, price_info: dict, now: datetime) -> None:
    price = float(price_info["price"])
    quantity = float(position["quantity"])
    entry = float(position["entry_price"])
    value = price * quantity
    pnl = (price - entry) * quantity
    pnl_pct = ((price - entry) / entry * 100) if entry else 0.0
    high = max(float(position["highest_price_since_entry"] or entry), price)
    low = min(float(position["lowest_price_since_entry"] or entry), price)
    timestamp = now.isoformat(timespec="seconds")
    conn.execute(
        """
        UPDATE paper_positions
        SET current_price = ?, market_value = ?, unrealized_pnl = ?, unrealized_pnl_pct = ?,
            highest_price_since_entry = ?, lowest_price_since_entry = ?, updated_at = ?
        WHERE id = ?
        """,
        (
            round(price, 2),
            round(value, 2),
            round(pnl, 2),
            round(pnl_pct, 2),
            round(high, 2),
            round(low, 2),
            timestamp,
            position["id"],
        ),
    )
    conn.execute(
        """
        UPDATE paper_trades
        SET max_favorable_excursion = MAX(COALESCE(max_favorable_excursion, 0), ?),
            max_adverse_excursion = MIN(COALESCE(max_adverse_excursion, 0), ?),
            updated_at = ?
        WHERE id = ?
        """,
        (
            round((high - entry) / entry * 100, 2) if entry else 0.0,
            round((low - entry) / entry * 100, 2) if entry else 0.0,
            timestamp,
            position["trade_id"],
        ),
    )


def _close_position(
    conn: sqlite3.Connection,
    position: sqlite3.Row,
    exit_price: float,
    reason: str,
    now: datetime,
) -> None:
    timestamp = now.isoformat(timespec="seconds")
    quantity = float(position["quantity"])
    entry = float(position["entry_price"])
    value = exit_price * quantity
    pnl = (exit_price - entry) * quantity
    pnl_pct = ((exit_price - entry) / entry * 100) if entry else 0.0
    status = "closed" if reason == "manual_close" else reason
    conn.execute(
        """
        UPDATE paper_trades
        SET status = ?, exit_time = ?, exit_price = ?, exit_reason = ?,
            realized_pnl = ?, realized_pnl_pct = ?, updated_at = ?
        WHERE id = ?
        """,
        (status, timestamp, round(exit_price, 2), reason, round(pnl, 2), round(pnl_pct, 2), timestamp, position["trade_id"]),
    )
    conn.execute("DELETE FROM paper_positions WHERE id = ?", (position["id"],))
    conn.execute(
        """
        UPDATE paper_accounts
        SET cash_balance = cash_balance + ?,
            realized_pnl = realized_pnl + ?,
            updated_at = ?
        WHERE id = ?
        """,
        (round(value, 2), round(pnl, 2), timestamp, position["account_id"]),
    )


def _refresh_accounts(conn: sqlite3.Connection, now: datetime) -> None:
    timestamp = now.isoformat(timespec="seconds")
    accounts = conn.execute("SELECT * FROM paper_accounts").fetchall()
    for account in accounts:
        row = conn.execute(
            """
            SELECT COALESCE(SUM(market_value), 0) AS value,
                   COALESCE(SUM(unrealized_pnl), 0) AS unrealized
            FROM paper_positions
            WHERE account_id = ?
            """,
            (account["id"],),
        ).fetchone()
        position_value = float(row["value"] or 0)
        unrealized = float(row["unrealized"] or 0)
        equity = float(account["cash_balance"] or 0) + position_value
        peak = float(account["initial_cash"] or equity)
        drawdown = min(0.0, (equity - peak) / peak * 100) if peak else 0.0
        max_drawdown = min(float(account["max_drawdown"] or 0), drawdown)
        conn.execute(
            """
            UPDATE paper_accounts
            SET equity = ?, unrealized_pnl = ?, max_drawdown = ?, updated_at = ?
            WHERE id = ?
            """,
            (round(equity, 2), round(unrealized, 2), round(max_drawdown, 2), timestamp, account["id"]),
        )


def _record_equity_curve(conn: sqlite3.Connection, now: datetime) -> None:
    timestamp = now.isoformat(timespec="seconds")
    for account in conn.execute("SELECT * FROM paper_accounts").fetchall():
        row = conn.execute(
            "SELECT COALESCE(SUM(market_value), 0) AS value FROM paper_positions WHERE account_id = ?",
            (account["id"],),
        ).fetchone()
        position_value = float(row["value"] or 0)
        drawdown = float(account["max_drawdown"] or 0)
        conn.execute(
            """
            INSERT INTO paper_equity_curve (
              account_id, market, captured_at, cash_balance, position_value, equity,
              realized_pnl, unrealized_pnl, drawdown
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                account["id"],
                account["market"],
                timestamp,
                account["cash_balance"],
                round(position_value, 2),
                account["equity"],
                account["realized_pnl"],
                account["unrealized_pnl"],
                drawdown,
            ),
        )


def _latest_price_map(conn: sqlite3.Connection) -> Dict[tuple[str, str], dict]:
    result: Dict[tuple[str, str], dict] = {}
    tw_captured = conn.execute("SELECT MAX(captured_at) AS captured_at FROM intraday_snapshots").fetchone()["captured_at"]
    if tw_captured:
        for row in conn.execute(
            """
            SELECT intr.symbol, intr.last_price AS price, intr.vwap, s.name AS name_zh,
                   s.name AS name_en, s.sector, r.entry_status
            FROM intraday_snapshots intr
            LEFT JOIN symbols s ON s.symbol = intr.symbol
            LEFT JOIN recommendations r ON r.market = 'TW' AND r.symbol = intr.symbol AND r.date = intr.date
            WHERE intr.captured_at = ?
            """,
            (tw_captured,),
        ).fetchall():
            result[("TW", row["symbol"])] = {
                "price": row["price"],
                "vwap": row["vwap"],
                "name_zh": row["name_zh"],
                "name_en": row["name_en"],
                "sector": row["sector"],
                "entry_status": row["entry_status"],
            }
    us_captured = conn.execute("SELECT MAX(captured_at) AS captured_at FROM us_candidates").fetchone()["captured_at"]
    if us_captured:
        for row in conn.execute(
            """
            SELECT uc.symbol, uc.latest_price AS price, uc.vwap, uc.entry_status,
                   us.name_zh, us.name_en, us.sector_zh AS sector
            FROM us_candidates uc
            LEFT JOIN us_symbols us ON us.symbol = uc.symbol
            WHERE uc.captured_at = ?
            """,
            (us_captured,),
        ).fetchall():
            result[("US", row["symbol"])] = dict(row)
    return result


def _symbol_metadata(conn: sqlite3.Connection, market: str, symbol: str) -> dict:
    if market == "US":
        row = conn.execute(
            "SELECT symbol, name_zh, name_en, sector_zh AS sector FROM us_symbols WHERE symbol = ?",
            (symbol,),
        ).fetchone()
        if row:
            return dict(row)
        for item in us_symbol_rows(datetime.now(ZoneInfo("Asia/Taipei"))):
            if item["symbol"] == symbol:
                return {
                    "symbol": symbol,
                    "name_zh": item["short_name_zh"],
                    "name_en": item["name_en"],
                    "sector": item["sector_zh"],
                }
        return {"symbol": symbol}
    row = conn.execute(
        "SELECT symbol, name AS name_zh, name AS name_en, sector FROM symbols WHERE symbol = ?",
        (symbol,),
    ).fetchone()
    return dict(row) if row else {"symbol": symbol}


def _to_float(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _position_quantity(
    conn: sqlite3.Connection,
    row: sqlite3.Row,
    price_info: dict,
    config: PaperTradingConfig,
) -> float:
    account = _account(conn, row["market"])
    price = _entry_price(row, price_info)
    stop_loss = row["stop_loss"]
    if price <= 0 or stop_loss is None:
        return 0.0
    cash = float(account["cash_balance"] or 0)
    equity = float(account["equity"] or account["initial_cash"] or 0)
    max_value = min(cash, equity * config.max_position_pct)
    risk_per_share = max(price - float(stop_loss), 0)
    if risk_per_share <= 0:
        return 0.0
    risk_qty = equity * config.max_risk_per_trade_pct / risk_per_share
    value_qty = max_value / price
    quantity = min(risk_qty, value_qty)
    if row["market"] == "TW":
        quantity = int(quantity // 1000) * 1000
    elif not config.allow_fractional_us_shares:
        quantity = int(quantity)
    else:
        quantity = round(quantity, 4)
    return max(quantity, 0.0)


def _entry_price(row: sqlite3.Row, price_info: dict) -> float:
    if row["lifecycle_status"] == "triggered" and row["trigger_price"]:
        return float(row["trigger_price"])
    if row["entry_status"] == "executable" and row["trigger_price"]:
        return float(row["trigger_price"])
    if row["signal_price"]:
        return float(row["signal_price"])
    return float(price_info["price"])


def _trade_exists(conn: sqlite3.Connection, recommendation_id: str) -> bool:
    return bool(
        conn.execute(
            "SELECT 1 FROM paper_trades WHERE recommendation_id = ? LIMIT 1",
            (recommendation_id,),
        ).fetchone()
    )


def _open_position_exists(conn: sqlite3.Connection, market: str, symbol: str) -> bool:
    return bool(
        conn.execute(
            "SELECT 1 FROM paper_positions WHERE market = ? AND symbol = ? LIMIT 1",
            (market, symbol),
        ).fetchone()
    )


def _open_position_count(conn: sqlite3.Connection, market: str) -> int:
    return int(conn.execute("SELECT COUNT(*) AS total FROM paper_positions WHERE market = ?", (market,)).fetchone()["total"])


def _open_sector_count(conn: sqlite3.Connection, market: str, sector: str) -> int:
    if not sector:
        return 0
    rows = conn.execute("SELECT symbol FROM paper_positions WHERE market = ?", (market,)).fetchall()
    if not rows:
        return 0
    price_map = _latest_price_map(conn)
    return sum(1 for row in rows if (price_map.get((market, row["symbol"])) or {}).get("sector") == sector)


def _account(conn: sqlite3.Connection, market: str) -> sqlite3.Row:
    return conn.execute("SELECT * FROM paper_accounts WHERE id = ?", (market,)).fetchone()


def _recommendation_id(row: sqlite3.Row) -> str:
    return f"{row['market']}|{row['date']}|{row['symbol']}"


def _entry_reason(row: sqlite3.Row) -> str:
    if row["lifecycle_status"] == "triggered":
        return row["trigger_reason"] or "recommendation triggered"
    return "entry_status executable"


def _should_force_exit(market: str, now: datetime, config: PaperTradingConfig) -> bool:
    if market == "TW":
        local = now.astimezone(ZoneInfo("Asia/Taipei")) if now.tzinfo else now
        return local.weekday() < 5 and _minutes_since_midnight(local) >= 13 * 60 + 30 - config.force_exit_before_close_minutes
    local = now.astimezone(ZoneInfo("America/New_York")) if now.tzinfo else now.replace(tzinfo=ZoneInfo("America/New_York"))
    return local.weekday() < 5 and _minutes_since_midnight(local) >= 16 * 60 - config.force_exit_before_close_minutes


def _paper_refresh_interval(now: datetime) -> int:
    tw = taiwan_market_session(now).session == "regular"
    us = us_market_session(now).session == "regular"
    return 30 if tw or us else 300


def _group_performance(rows: Iterable[sqlite3.Row], key: str) -> list[dict]:
    grouped: dict[str, list[sqlite3.Row]] = {}
    for row in rows:
        grouped.setdefault(row[key] or "unknown", []).append(row)
    result = []
    for value, items in sorted(grouped.items()):
        wins = [item for item in items if (item["realized_pnl"] or 0) > 0]
        result.append(
            {
                key: value,
                "trades": len(items),
                "win_rate": _rate(len(wins), len(items)),
                "realized_pnl": round(sum(item["realized_pnl"] or 0 for item in items), 2),
            }
        )
    return result


def _rate(count: int, total: int) -> float:
    return round(count / total * 100, 2) if total else 0.0


def _minutes_since_midnight(value: datetime) -> int:
    return value.hour * 60 + value.minute
