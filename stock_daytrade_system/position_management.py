from __future__ import annotations

import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Optional


POSITION_MANAGEMENT_VERSION = "position_management_v1_long_daytrade_2026-06-19"

MAX_TOTAL_STOP_LOSS_RISK_PCT = 3.0


@dataclass(frozen=True)
class PositionAction:
    trade_id: str
    market: str
    symbol: str
    name_zh: str
    name_en: str
    action: str
    cost_price: float
    current_price: float
    quantity: float
    invested_amount: float
    unrealized_pnl: float
    unrealized_pnl_pct: float
    vwap: Optional[float]
    volume_ratio: Optional[float]
    stop_loss: Optional[float]
    target_price: Optional[float]
    trailing_stop: Optional[float]
    first_take_profit: Optional[float]
    second_take_profit: Optional[float]
    next_step: str
    invalidation: str
    can_add: bool
    add_forbidden: bool
    add_forbidden_reasons: list[str]
    reason_code: str

    def to_dict(self) -> dict:
        return asdict(self)


def build_position_command_center(conn: sqlite3.Connection, market: str = "TW") -> dict:
    rows = conn.execute(
        """
        SELECT p.*, t.name_zh, t.name_en, t.source, t.risk_mode
        FROM paper_positions p
        LEFT JOIN paper_trades t ON t.id = p.trade_id
        WHERE p.market = ?
        ORDER BY p.symbol
        """,
        (market,),
    ).fetchall()
    actions = [_position_action(conn, dict(row)) for row in rows]
    invested = round(sum(item.invested_amount for item in actions), 2)
    unrealized = round(sum(item.unrealized_pnl for item in actions), 2)
    stop_loss_total = round(sum(_scenario_pnl(item.cost_price, item.stop_loss, item.quantity) for item in actions), 2)
    take_profit_total = round(sum(_scenario_pnl(item.cost_price, item.target_price, item.quantity) for item in actions), 2)
    pnl_pct = round(unrealized / invested * 100, 2) if invested else 0.0
    stop_loss_risk_pct = abs(stop_loss_total) / invested * 100 if invested and stop_loss_total < 0 else 0.0
    total_risk_high = stop_loss_risk_pct > MAX_TOTAL_STOP_LOSS_RISK_PCT
    return {
        "version": POSITION_MANAGEMENT_VERSION,
        "market": market,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "summary": {
            "positions_count": len(actions),
            "invested_amount": invested,
            "unrealized_pnl": unrealized,
            "unrealized_pnl_pct": pnl_pct,
            "if_all_stop_loss": stop_loss_total,
            "if_all_take_profit": take_profit_total,
            "can_add_any": any(item.can_add for item in actions) and not total_risk_high,
            "allow_new_position": len(actions) < 5 and not total_risk_high,
            "total_risk_high": total_risk_high,
            "total_risk_message": "今日總風險已偏高，不建議再加碼或新增持倉。" if total_risk_high else "總風險目前仍在可控範圍內。",
            "max_total_stop_loss_risk_pct": MAX_TOTAL_STOP_LOSS_RISK_PCT,
            "stop_loss_risk_pct": round(stop_loss_risk_pct, 2),
        },
        "positions": [item.to_dict() for item in actions],
    }


def position_action_for_symbol(conn: sqlite3.Connection, symbol: str, market: str = "TW") -> Optional[dict]:
    row = conn.execute(
        """
        SELECT p.*, t.name_zh, t.name_en, t.source, t.risk_mode
        FROM paper_positions p
        LEFT JOIN paper_trades t ON t.id = p.trade_id
        WHERE p.market = ? AND p.symbol = ?
        ORDER BY p.opened_at DESC
        LIMIT 1
        """,
        (market, symbol),
    ).fetchone()
    if row is None:
        return None
    return _position_action(conn, dict(row)).to_dict()


def _position_action(conn: sqlite3.Connection, row: dict) -> PositionAction:
    symbol = row["symbol"]
    cost = _float(row.get("entry_price")) or 0.0
    current = _latest_price(conn, symbol) or _float(row.get("current_price")) or cost
    quantity = _float(row.get("quantity")) or 0.0
    stop_loss = _float(row.get("stop_loss"))
    target = _float(row.get("target_price"))
    vwap, volume_ratio, entry_status, risk_score = _latest_signal_context(conn, symbol)
    invested = round(cost * quantity, 2)
    pnl = round((current - cost) * quantity, 2)
    pnl_pct = round((current - cost) / cost * 100, 2) if cost else 0.0
    highest = max(_float(row.get("highest_price_since_entry")) or current, current)
    trailing = round(max(stop_loss or 0, highest * 0.992), 2) if highest else stop_loss
    first_tp = round(cost * 1.01, 2) if cost else target
    second_tp = round(cost * 1.02, 2) if cost else target
    action = _holding_action(current, cost, stop_loss, target, vwap, pnl_pct)
    forbidden = _add_forbidden_reasons(
        current=current,
        cost=cost,
        vwap=vwap,
        volume_ratio=volume_ratio,
        entry_status=entry_status,
        risk_score=risk_score,
    )
    can_add = not forbidden and pnl > 0 and action in {"續抱", "可加碼"}
    if can_add:
        action = "可加碼"
    return PositionAction(
        trade_id=str(row.get("trade_id") or ""),
        market=row.get("market") or "TW",
        symbol=symbol,
        name_zh=row.get("name_zh") or symbol,
        name_en=row.get("name_en") or "",
        action=action,
        cost_price=round(cost, 2),
        current_price=round(current, 2),
        quantity=quantity,
        invested_amount=invested,
        unrealized_pnl=pnl,
        unrealized_pnl_pct=pnl_pct,
        vwap=round(vwap, 2) if vwap is not None else None,
        volume_ratio=round(volume_ratio, 2) if volume_ratio is not None else None,
        stop_loss=round(stop_loss, 2) if stop_loss is not None else None,
        target_price=round(target, 2) if target is not None else None,
        trailing_stop=trailing,
        first_take_profit=first_tp,
        second_take_profit=second_tp,
        next_step=_holding_next_step(action, current, vwap, target, trailing),
        invalidation=_holding_invalidation(stop_loss, vwap, trailing),
        can_add=can_add,
        add_forbidden=not can_add,
        add_forbidden_reasons=forbidden if forbidden else [],
        reason_code=_reason_code(action, forbidden),
    )


def _holding_action(current: float, cost: float, stop_loss: Optional[float], target: Optional[float], vwap: Optional[float], pnl_pct: float) -> str:
    if stop_loss is not None and current <= stop_loss:
        return "停損"
    if target is not None and current >= target:
        return "停利"
    if vwap is not None and current < vwap and pnl_pct < 0:
        return "減碼"
    if pnl_pct <= -0.8:
        return "減碼"
    return "續抱"


def _add_forbidden_reasons(*, current: float, cost: float, vwap: Optional[float], volume_ratio: Optional[float], entry_status: str, risk_score: Optional[float]) -> list[str]:
    reasons = []
    if current <= cost:
        reasons.append("目前虧損，不做攤平加碼。")
    if vwap is None:
        reasons.append("缺 VWAP，禁止加碼。")
    elif current < vwap:
        reasons.append("跌破 VWAP，禁止加碼。")
    if volume_ratio is None:
        reasons.append("缺量比，禁止加碼。")
    elif volume_ratio < 1.0:
        reasons.append("量能未維持，禁止加碼。")
    if entry_status in {"high_risk", "avoid", "data_missing"}:
        reasons.append("目前為風險或資料不足狀態，禁止加碼。")
    if risk_score is not None and risk_score >= 65:
        reasons.append("風險分數偏高，禁止加碼。")
    return reasons


def _holding_next_step(action: str, current: float, vwap: Optional[float], target: Optional[float], trailing: Optional[float]) -> str:
    if action == "可加碼":
        return f"若突破前高且量能維持，可少量加碼；加碼後仍需守移動停損 {trailing or '-'}。"
    if action == "停利":
        return "已達停利區，優先保護獲利，可分批出場。"
    if action == "停損":
        return "已觸及停損條件，應依規則出場。"
    if action == "減碼":
        return "若無法站回 VWAP，應減碼或退出。"
    return f"續抱觀察；若突破 {target or '停利價'} 且量能維持，再評估是否加碼。"


def _holding_invalidation(stop_loss: Optional[float], vwap: Optional[float], trailing: Optional[float]) -> str:
    parts = []
    if vwap is not None:
        parts.append(f"跌破 VWAP {vwap:.2f}")
    if trailing is not None:
        parts.append(f"跌破移動停損 {trailing:.2f}")
    if stop_loss is not None:
        parts.append(f"跌破原始停損 {stop_loss:.2f}")
    return " 或 ".join(parts) if parts else "資料不足時不做持倉判斷。"


def _reason_code(action: str, forbidden: list[str]) -> str:
    if action == "停損":
        return "hit_stop_loss"
    if action == "停利":
        return "hit_take_profit"
    if action == "減碼":
        return "reduce_risk"
    if not forbidden:
        return "add_allowed"
    return "add_blocked"


def _latest_signal_context(conn: sqlite3.Connection, symbol: str) -> tuple[Optional[float], Optional[float], str, Optional[float]]:
    row = conn.execute(
        """
        SELECT i.vwap, i.volume_ratio, r.entry_status, r.risk_score
        FROM intraday_snapshots i
        LEFT JOIN recommendations r ON r.market = 'TW' AND r.symbol = i.symbol AND r.date = i.date
        WHERE i.symbol = ?
        ORDER BY i.captured_at DESC
        LIMIT 1
        """,
        (symbol,),
    ).fetchone()
    if row is None:
        return None, None, "", None
    return _float(row["vwap"]), _float(row["volume_ratio"]), row["entry_status"] or "", _float(row["risk_score"])


def _latest_price(conn: sqlite3.Connection, symbol: str) -> Optional[float]:
    row = conn.execute(
        "SELECT last_price FROM intraday_snapshots WHERE symbol = ? ORDER BY captured_at DESC LIMIT 1",
        (symbol,),
    ).fetchone()
    return _float(row["last_price"]) if row else None


def _scenario_pnl(cost: float, target: Optional[float], quantity: float) -> float:
    if target is None:
        return 0.0
    return (target - cost) * quantity


def _float(value) -> Optional[float]:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None
