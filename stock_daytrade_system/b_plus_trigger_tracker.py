from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Optional


B_PLUS_TRIGGER_VERSION = "b_plus_trigger_tracker_v1_2026-06-13"


def evaluate_b_plus_trigger(
    *,
    market: str,
    symbol: str,
    name_zh: str,
    name_en: str = "",
    current_price: Optional[float],
    vwap: Optional[float],
    volume_ratio: Optional[float],
    entry_status: str,
    lifecycle_status: str,
    trigger_price: Optional[float],
    confidence_score: Optional[float] = None,
    confidence_summary: str = "",
) -> dict:
    entry_status = entry_status or "unknown"
    lifecycle_status = lifecycle_status or "observed"
    current_price = _float_or_none(current_price)
    vwap = _float_or_none(vwap)
    volume_ratio = _float_or_none(volume_ratio)
    trigger_price = _float_or_none(trigger_price)
    confidence_score = _float_or_none(confidence_score)
    blocked = entry_status in {"high_risk", "avoid"}

    if entry_status == "wait_vwap":
        condition = "站回 VWAP 後觸發"
        target = vwap
        readiness = _readiness_wait_vwap(current_price, vwap, volume_ratio, blocked)
        distance = _distance_wait_vwap(current_price, vwap)
    elif entry_status == "wait_volume":
        condition = "量比放大後觸發"
        target = 0.8 if market == "TW" else 0.8
        readiness = _readiness_wait_volume(volume_ratio, target, blocked)
        distance = _distance_wait_volume(volume_ratio, target)
    elif entry_status == "wait_breakout":
        condition = "突破指定價位後觸發"
        target = trigger_price
        readiness = _readiness_wait_breakout(current_price, trigger_price, blocked)
        distance = _distance_wait_breakout(current_price, trigger_price)
    elif entry_status == "wait_pullback":
        condition = "回測 VWAP 不破後觸發"
        target = vwap
        readiness = _readiness_wait_pullback(current_price, vwap, blocked)
        distance = _distance_wait_pullback(current_price, vwap)
    elif entry_status in {"executable", "practice_long"}:
        condition = "練習買多條件成立" if entry_status == "practice_long" else "已符合可執行條件"
        target = trigger_price or current_price
        readiness = "ready" if not blocked else "blocked"
        distance = "已達觸發條件"
    else:
        condition = "等待條件明確"
        target = trigger_price
        readiness = "blocked" if blocked else "waiting"
        distance = "等待條件達成"

    if lifecycle_status in {"triggered", "hit_target", "stopped", "closed"}:
        readiness = "ready"
    next_action = _next_action(readiness, lifecycle_status)
    return {
        "market": market or "TW",
        "symbol": symbol,
        "name_zh": name_zh or symbol,
        "name_en": name_en or "",
        "current_price": current_price,
        "vwap": vwap,
        "volume_ratio": volume_ratio,
        "entry_status": entry_status,
        "lifecycle_status": lifecycle_status,
        "trigger_condition": condition,
        "trigger_price": target,
        "distance_to_trigger": distance,
        "trigger_readiness": readiness,
        "trigger_readiness_label": _readiness_label(readiness),
        "trigger_next_action": next_action,
        "confidence_score": confidence_score,
        "confidence_summary": confidence_summary or "",
        "trigger_reason": _trigger_reason(entry_status, current_price, volume_ratio),
    }


def build_b_plus_trigger_tracker(
    conn: sqlite3.Connection,
    market: Optional[str] = None,
    date_text: Optional[str] = None,
) -> list[dict]:
    rows = _b_plus_recommendation_rows(conn, market, date_text)
    return [_tracker_from_row(conn, row) for row in rows]


def update_ready_b_plus_triggers(
    conn: sqlite3.Connection,
    captured_at: datetime,
    market: Optional[str] = None,
) -> int:
    updated = 0
    for item in build_b_plus_trigger_tracker(conn, market=market):
        if item["trigger_readiness"] != "ready":
            continue
        row = conn.execute(
            """
            SELECT *
            FROM recommendations
            WHERE market = ? AND date = ? AND symbol = ? AND grade = 'B+'
            """,
            (item["market"], item["date"], item["symbol"]),
        ).fetchone()
        if row is None or (row["lifecycle_status"] or "observed") != "observed" or row["trigger_time"]:
            continue
        conn.execute(
            """
            UPDATE recommendations
            SET lifecycle_status = 'triggered',
                trigger_time = ?,
                trigger_price = ?,
                trigger_reason = ?
            WHERE market = ? AND date = ? AND symbol = ?
            """,
            (
                captured_at.isoformat(timespec="seconds"),
                item["current_price"] or item["trigger_price"] or row["signal_price"] or row["trigger_price"],
                item["trigger_reason"],
                item["market"],
                item["date"],
                item["symbol"],
            ),
        )
        updated += 1
    return updated


def _tracker_from_row(conn: sqlite3.Connection, row: sqlite3.Row) -> dict:
    quote = _latest_quote(conn, row)
    item = evaluate_b_plus_trigger(
        market=row["market"] or "TW",
        symbol=row["symbol"],
        name_zh=quote.get("name_zh") or row["symbol"],
        name_en=quote.get("name_en") or "",
        current_price=quote.get("current_price") or row["signal_price"] or row["trigger_price"],
        vwap=quote.get("vwap"),
        volume_ratio=quote.get("volume_ratio"),
        entry_status=row["entry_status"] or "",
        lifecycle_status=row["lifecycle_status"] or "observed",
        trigger_price=quote.get("breakout_price") or row["trigger_price"],
        confidence_score=row["confidence_score"],
        confidence_summary=row["confidence_summary"] or "",
    )
    item["date"] = row["date"]
    return item


def _b_plus_recommendation_rows(
    conn: sqlite3.Connection,
    market: Optional[str],
    date_text: Optional[str],
) -> list[sqlite3.Row]:
    where = ["grade = 'B+'"]
    params: list[str] = []
    if market:
        where.append("market = ?")
        params.append(market)
    if date_text:
        where.append("date = ?")
        params.append(date_text)
    else:
        where.append("date = (SELECT MAX(r2.date) FROM recommendations r2 WHERE r2.market = recommendations.market)")
    return conn.execute(
        f"""
        SELECT *
        FROM recommendations
        WHERE {' AND '.join(where)}
        ORDER BY market, date DESC, symbol
        """,
        params,
    ).fetchall()


def _latest_quote(conn: sqlite3.Connection, row: sqlite3.Row) -> dict:
    market = row["market"] or "TW"
    if market == "US":
        quote = conn.execute(
            """
            SELECT uc.latest_price AS current_price, uc.vwap, uc.volume_ratio,
                   uc.trigger_price AS breakout_price,
                   us.name_zh, us.name_en
            FROM us_candidates uc
            LEFT JOIN us_symbols us ON us.symbol = uc.symbol
            WHERE uc.symbol = ? AND uc.date = ?
            ORDER BY uc.captured_at DESC
            LIMIT 1
            """,
            (row["symbol"], row["date"]),
        ).fetchone()
        if quote:
            return dict(quote)
        meta = conn.execute("SELECT name_zh, name_en FROM us_symbols WHERE symbol = ?", (row["symbol"],)).fetchone()
        return dict(meta) if meta else {}

    quote = conn.execute(
        """
        SELECT intr.last_price AS current_price, intr.vwap, intr.volume_ratio,
               COALESCE(ds.previous_high, r.trigger_price) AS breakout_price,
               s.name AS name_zh, s.name AS name_en
        FROM recommendations r
        LEFT JOIN symbols s ON s.symbol = r.symbol
        LEFT JOIN daily_snapshots ds ON ds.symbol = r.symbol AND ds.date = r.date
        LEFT JOIN intraday_snapshots intr
          ON intr.symbol = r.symbol
         AND intr.date = r.date
         AND intr.captured_at = (
            SELECT MAX(i2.captured_at)
            FROM intraday_snapshots i2
            WHERE i2.symbol = r.symbol AND i2.date = r.date
         )
        WHERE r.market = ? AND r.date = ? AND r.symbol = ?
        LIMIT 1
        """,
        (market, row["date"], row["symbol"]),
    ).fetchone()
    return dict(quote) if quote else {}


def _readiness_wait_vwap(
    current_price: Optional[float],
    vwap: Optional[float],
    volume_ratio: Optional[float],
    blocked: bool,
) -> str:
    if blocked:
        return "blocked"
    if current_price is None or vwap is None:
        return "waiting"
    volume_ok = volume_ratio is None or volume_ratio >= 0.8
    if current_price >= vwap and volume_ok:
        return "ready"
    if _distance_pct(current_price, vwap) <= 0.2 or (volume_ratio is not None and 0 <= 0.8 - volume_ratio <= 0.1):
        return "near"
    return "waiting"


def _readiness_wait_volume(volume_ratio: Optional[float], target: float, blocked: bool) -> str:
    if blocked:
        return "blocked"
    if volume_ratio is None:
        return "waiting"
    if volume_ratio >= target:
        return "ready"
    if 0 <= target - volume_ratio <= 0.1:
        return "near"
    return "waiting"


def _readiness_wait_breakout(
    current_price: Optional[float],
    trigger_price: Optional[float],
    blocked: bool,
) -> str:
    if blocked:
        return "blocked"
    if current_price is None or trigger_price is None:
        return "waiting"
    if current_price >= trigger_price:
        return "ready"
    if _distance_pct(current_price, trigger_price) <= 0.5:
        return "near"
    return "waiting"


def _readiness_wait_pullback(
    current_price: Optional[float],
    vwap: Optional[float],
    blocked: bool,
) -> str:
    if blocked:
        return "blocked"
    if current_price is None or vwap is None:
        return "waiting"
    pct = ((current_price - vwap) / vwap * 100) if vwap else 999
    if 0 <= pct <= 0.3:
        return "ready"
    if abs(pct) <= 0.5:
        return "near"
    return "waiting"


def _distance_wait_vwap(current_price: Optional[float], vwap: Optional[float]) -> str:
    if current_price is None or vwap is None:
        return "缺少現價或 VWAP，等待資料更新"
    gap = vwap - current_price
    if gap <= 0:
        return "已站回 VWAP"
    if _distance_pct(current_price, vwap) <= 0.2:
        return f"已接近觸發，差 {gap:.2f} 元站回 VWAP"
    return f"距離觸發：差 {gap:.2f} 元站回 VWAP"


def _distance_wait_volume(volume_ratio: Optional[float], target: float) -> str:
    if volume_ratio is None:
        return "缺少量比，等待資料更新"
    gap = target - volume_ratio
    if gap <= 0:
        return "量比已達觸發門檻"
    if gap <= 0.1:
        return f"已接近觸發，量比差 {gap:.2f}"
    return f"距離觸發：量比差 {gap:.2f}"


def _distance_wait_breakout(current_price: Optional[float], trigger_price: Optional[float]) -> str:
    if current_price is None or trigger_price is None:
        return "缺少現價或觸發價，等待資料更新"
    gap_pct = ((trigger_price - current_price) / trigger_price * 100) if trigger_price else 0
    if gap_pct <= 0:
        return "已突破觸發價"
    if gap_pct <= 0.5:
        return f"已接近觸發，差 {gap_pct:.2f}% 突破觸發價"
    return f"距離觸發：差 {gap_pct:.2f}% 突破觸發價"


def _distance_wait_pullback(current_price: Optional[float], vwap: Optional[float]) -> str:
    if current_price is None or vwap is None:
        return "缺少現價或 VWAP，等待資料更新"
    pct = ((current_price - vwap) / vwap * 100) if vwap else 0
    if 0 <= pct <= 0.3:
        return "已接近 VWAP 且未跌破，可觸發"
    if pct > 0.3:
        return f"距離觸發：仍高於 VWAP {pct:.2f}%，等待回測"
    return f"距離觸發：低於 VWAP {abs(pct):.2f}%，等待站回"


def _next_action(readiness: str, lifecycle_status: str) -> str:
    if lifecycle_status in {"triggered", "hit_target", "stopped", "closed"}:
        return "已觸發，交由回測與虛擬交易追蹤"
    return {
        "ready": "可轉 triggered，等待系統下一次更新建立 paper trade",
        "near": "接近觸發，持續觀察",
        "waiting": "等待條件達成",
        "blocked": "風險或衝突過高，不觸發",
    }.get(readiness, "等待條件達成")


def _readiness_label(readiness: str) -> str:
    return {
        "ready": "ready 已達觸發",
        "near": "near 接近觸發",
        "waiting": "waiting 等待中",
        "blocked": "blocked 不可觸發",
    }.get(readiness, readiness)


def _trigger_reason(entry_status: str, current_price: Optional[float], volume_ratio: Optional[float]) -> str:
    if entry_status == "wait_vwap":
        return "B+站回VWAP"
    if entry_status == "wait_volume":
        return f"B+量比放大至 {float(volume_ratio or 0):.2f}x"
    if entry_status == "wait_breakout":
        return "B+突破指定價位"
    if entry_status == "wait_pullback":
        return "B+回測VWAP不破"
    if entry_status == "executable":
        return "B+條件可執行"
    if entry_status == "practice_long":
        return "B+練習買多條件成立"
    return "B+觸發條件成立"


def _distance_pct(current_price: Optional[float], target_price: Optional[float]) -> float:
    if current_price is None or target_price in {None, 0}:
        return 999.0
    return abs((target_price - current_price) / target_price * 100)


def _float_or_none(value) -> Optional[float]:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None
