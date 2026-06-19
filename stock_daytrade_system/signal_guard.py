from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Optional


SIGNAL_GUARD_VERSION = "signal_guard_v1_unified_executable_gate_2026-06-19"

NON_EXECUTABLE_ENTRY_STATUSES = {
    "practice_long",
    "wait_volume",
    "wait_vwap",
    "wait_breakout",
    "wait_pullback",
    "high_risk",
    "avoid",
    "data_missing",
}


@dataclass(frozen=True)
class SignalBlocker:
    code: str
    message: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class SignalGuardResult:
    entry_status: str
    grade: str
    is_executable_allowed: bool
    is_strong_long_allowed: bool
    blockers: list[SignalBlocker]
    reason_codes: list[str]
    effective_entry_status: str
    effective_grade: str
    vwap_distance_pct: Optional[float]

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload["blockers"] = [item.to_dict() for item in self.blockers]
        payload["version"] = SIGNAL_GUARD_VERSION
        return payload


def evaluate_signal_guard(
    item: Any,
    *,
    data_today: bool = True,
    intraday: bool = True,
    stale: bool = False,
    data_missing: bool = False,
    allow_strong_long: bool = True,
    market_mode: str = "intraday",
    market_session: Optional[str] = None,
    current_price: Optional[float] = None,
    change_pct: Optional[float] = None,
) -> SignalGuardResult:
    entry_status = _string(_get(item, "entry_status")) or "data_missing"
    grade = _string(_get(item, "grade")) or "data_missing"
    vwap = _number(_get(item, "vwap"))
    volume_ratio = _number(_get(item, "volume_ratio"))
    stop_loss = _number(_get(item, "stop_loss"))
    price = _number(current_price) or _number(_get(item, "last_price")) or _number(_get(item, "latest_price"))
    change = _number(change_pct) if change_pct is not None else _number(_get(item, "change_pct"))
    blockers: list[SignalBlocker] = []

    if not data_today:
        blockers.append(SignalBlocker("not_today", "資料不是今天，不能顯示可執行。"))
    if not intraday:
        blockers.append(SignalBlocker("not_intraday", "非盤中資料，不能顯示可執行。"))
    if stale:
        blockers.append(SignalBlocker("stale_data", "資料已過期，暫停顯示可執行。"))
    if data_missing or grade == "data_missing" or entry_status == "data_missing":
        blockers.append(SignalBlocker("data_missing", "核心資料缺漏，不能產生有效當沖判斷。"))
    if not allow_strong_long:
        blockers.append(SignalBlocker("refresh_layer_stale", "分層刷新資料不完整，禁止顯示強烈做多。"))
    if market_mode != "intraday":
        blockers.append(SignalBlocker("not_intraday_mode", "目前不是盤中模式，不提供即時進場判斷。"))
    if market_session is not None and market_session != "regular":
        blockers.append(SignalBlocker("market_not_regular", "目前非台股盤中，不顯示盤中可執行。"))
    if vwap is None:
        blockers.append(SignalBlocker("missing_vwap", "缺少 VWAP，不能列為可執行。"))
    if volume_ratio is None:
        blockers.append(SignalBlocker("missing_volume_ratio", "缺少量比，不能列為可執行。"))
    if stop_loss is None:
        blockers.append(SignalBlocker("missing_stop_loss", "缺少停損價，不能列為可執行。"))

    if price and stop_loss and stop_loss < price:
        stop_distance = (price - stop_loss) / price * 100
        if stop_distance > 5:
            blockers.append(SignalBlocker("stop_loss_too_far", f"停損距離約 {stop_distance:.2f}%，風險偏高。"))

    vwap_distance = None
    if price and vwap:
        vwap_distance = (price - vwap) / vwap * 100
        if vwap_distance > 3:
            blockers.append(SignalBlocker("too_far_from_vwap", f"距離 VWAP 約 {vwap_distance:.2f}%，追價風險高。"))

    if change is not None and change >= 9:
        blockers.append(SignalBlocker("near_limit_up", "接近漲停或漲幅過大，追價風險高。"))

    risk_blocked = any(item.code in {"too_far_from_vwap", "stop_loss_too_far", "near_limit_up"} for item in blockers)
    effective_entry = entry_status
    effective_grade = grade
    if blockers and entry_status in {"executable", "practice_long"}:
        effective_entry = "high_risk" if risk_blocked else "data_missing"
        effective_grade = "high_risk" if risk_blocked else "data_missing"
    elif any(item.code == "data_missing" for item in blockers):
        effective_entry = "data_missing"
        effective_grade = "data_missing"

    executable_allowed = (
        entry_status == "executable"
        and effective_entry == "executable"
        and grade != "data_missing"
        and entry_status not in NON_EXECUTABLE_ENTRY_STATUSES
        and not blockers
    )
    return SignalGuardResult(
        entry_status=entry_status,
        grade=grade,
        is_executable_allowed=executable_allowed,
        is_strong_long_allowed=executable_allowed,
        blockers=blockers,
        reason_codes=_dedupe([item.code for item in blockers] or ["executable" if executable_allowed else entry_status]),
        effective_entry_status=effective_entry,
        effective_grade=effective_grade,
        vwap_distance_pct=round(vwap_distance, 2) if vwap_distance is not None else None,
    )


def _get(item: Any, key: str) -> Any:
    if isinstance(item, dict):
        return item.get(key)
    return getattr(item, key, None)


def _string(value: Any) -> str:
    return "" if value is None else str(value)


def _number(value: Any) -> Optional[float]:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _dedupe(values: list[str]) -> list[str]:
    seen = set()
    result = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result
