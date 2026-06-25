from __future__ import annotations

import os
from collections import Counter
from dataclasses import asdict, dataclass
from typing import Any, Iterable, Optional

from stock_daytrade_system.tw_symbols import normalize_tw_stock_symbol


FUGLE_PRIORITY_POOL_VERSION = "fugle_priority_pool_v1_basic_5_2026-06-21"
DEFAULT_FUGLE_BASIC_SUBSCRIPTIONS = 5


@dataclass(frozen=True)
class FuglePriorityItem:
    symbol: str
    name: str
    grade: str
    entry_status: str
    trade_bias: str
    last_price: Optional[float]
    vwap: Optional[float]
    volume_ratio: Optional[float]
    trigger_price: Optional[float]
    stop_loss: Optional[float]
    risk_score: float
    bullish_score: float
    confidence_score: float
    trigger_readiness: str
    priority_score: float
    priority_reason: str
    tracking_purpose: str
    can_use_for_entry_confirmation: bool

    def to_dict(self) -> dict:
        return asdict(self)


def fugle_basic_subscription_limit() -> int:
    value = os.environ.get("FUGLE_MAX_SUBSCRIPTIONS") or os.environ.get("FUGLE_BASIC_SUBSCRIPTIONS")
    try:
        limit = int(value) if value else DEFAULT_FUGLE_BASIC_SUBSCRIPTIONS
    except (TypeError, ValueError):
        limit = DEFAULT_FUGLE_BASIC_SUBSCRIPTIONS
    return max(1, min(limit, 5))


def build_fugle_priority_pool(
    candidates: Iterable[Any],
    *,
    b_plus_triggers: Optional[Iterable[dict]] = None,
    pinned_symbols: Optional[Iterable[str]] = None,
    max_symbols: Optional[int] = None,
    enabled: Optional[bool] = None,
    configured: Optional[bool] = None,
) -> dict:
    limit = max_symbols or fugle_basic_subscription_limit()
    trigger_map = {str(item.get("symbol") or ""): item for item in (b_plus_triggers or [])}
    pinned = {_normalize_symbol(item) for item in (pinned_symbols or []) if str(item).strip()}
    scored = [
        _score_candidate(
            item,
            trigger_map.get(_value(item, "symbol", default="")),
            pinned=_normalize_symbol(_value(item, "symbol", default="")) in pinned,
        )
        for item in candidates
    ]
    scored = [item for item in scored if item.priority_score > 0]
    scored.sort(key=lambda item: (-item.priority_score, item.risk_score, item.symbol))
    selected = scored[:limit]
    standby = scored[limit : limit + 10]
    allocation_summary = _allocation_summary(selected, limit)
    return {
        "version": FUGLE_PRIORITY_POOL_VERSION,
        "mode": "basic_user_5_symbols",
        "max_symbols": limit,
        "enabled": bool(enabled) if enabled is not None else _env_bool("FUGLE_ENABLED"),
        "configured": bool(configured) if configured is not None else bool(os.environ.get("FUGLE_API_KEY")),
        "considered_count": len(scored),
        "selected_count": len(selected),
        "excluded_count": max(len(scored) - len(selected), 0),
        "allocation_summary": allocation_summary,
        "pinned_symbols": sorted(pinned),
        "selected": [item.to_dict() for item in selected],
        "standby": [
            {
                **item.to_dict(),
                "not_selected_reason": f"Fugle 基本用戶名額 {limit} 檔已滿，目前列為候補。",
            }
            for item in standby
        ],
        "selection_policy": [
            "優先追蹤 executable / practice_long / B+ / 等待突破 / 等待 VWAP / 等待量能。",
            "Fugle 基本用戶最多追 5 檔，避免全市場打 API 或超過訂閱限制。",
            "此池只用於即時確認，不會改 A / B+ / B 條件，也不會自動下單。",
        ],
        "message": "已依基本用戶 5 檔限制挑選即時追蹤標的。"
        if selected
        else "目前沒有需要使用 Fugle 即時追蹤的重點標的。",
    }


def _allocation_summary(selected: list[FuglePriorityItem], limit: int) -> dict:
    by_status = Counter(item.entry_status or "-" for item in selected)
    by_purpose = Counter(item.tracking_purpose or "-" for item in selected)
    confirmable = sum(1 for item in selected if item.can_use_for_entry_confirmation)
    high_risk = sum(1 for item in selected if item.entry_status == "high_risk")
    summary_parts = []
    for key, label in (
        ("executable", "可執行確認"),
        ("practice_long", "練習買多"),
        ("wait_breakout", "等待突破"),
        ("wait_vwap", "等待 VWAP"),
        ("wait_volume", "等待量能"),
        ("wait_pullback", "等待拉回"),
        ("high_risk", "高風險觀察"),
    ):
        count = by_status.get(key, 0)
        if count:
            summary_parts.append(f"{label} {count} 檔")
    return {
        "limit": limit,
        "used": len(selected),
        "remaining": max(limit - len(selected), 0),
        "confirmable_count": confirmable,
        "high_risk_observation_count": high_risk,
        "by_entry_status": dict(sorted(by_status.items())),
        "by_tracking_purpose": dict(sorted(by_purpose.items())),
        "summary": "、".join(summary_parts) if summary_parts else "目前沒有配置即時追蹤名額。",
        "warning": "高風險標的只作風險降溫觀察，不作進場確認。" if high_risk else "",
    }


def _score_candidate(item: Any, trigger: Optional[dict], *, pinned: bool = False) -> FuglePriorityItem:
    symbol = str(_value(item, "symbol", default=""))
    entry_status = str(_value(item, "entry_status", default=""))
    grade = str(_value(item, "grade", default=""))
    trade_bias = str(_value(item, "trade_bias", default=""))
    risk_score = _float(_value(item, "risk_score"), default=100.0) or 100.0
    bullish_score = _float(_value(item, "bullish_score"), default=0.0) or 0.0
    confidence_score = _float(_value(item, "confidence_score"), default=0.0) or 0.0
    volume_ratio = _float(_value(item, "volume_ratio"))
    last_price = _float(_value(item, "last_price"))
    vwap = _float(_value(item, "vwap"))
    trigger_price = _float(_value(item, "trigger_price"))
    stop_loss = _float(_value(item, "stop_loss"))
    trigger_readiness = str((trigger or {}).get("trigger_readiness") or "")
    priority = 0.0
    reasons: list[str] = []

    if pinned:
        priority += 1500
        reasons.append("使用者指定即時追蹤")

    if entry_status == "executable":
        priority += 1000
        reasons.append("可執行，需優先確認五檔與逐筆")
    elif entry_status == "practice_long":
        priority += 880
        reasons.append("練習買多，適合即時觀察")
    elif trigger_readiness == "ready":
        priority += 850
        reasons.append("B+ 觸發條件接近 ready")
    elif entry_status == "wait_breakout":
        priority += 760
        reasons.append("等待突破，需追蹤觸發價")
    elif entry_status == "wait_vwap":
        priority += 700
        reasons.append("等待站回 VWAP")
    elif entry_status == "wait_volume":
        priority += 680
        reasons.append("等待量能放大")
    elif entry_status == "wait_pullback":
        priority += 620
        reasons.append("等待拉回不破")
    elif grade == "B+":
        priority += 650
        reasons.append("B+ 練習觀察")
    elif grade == "B":
        priority += 520
        reasons.append("B 級等待確認")
    elif entry_status == "high_risk":
        priority += 220
        reasons.append("高風險只追蹤風險變化，不列為進場")
    elif entry_status == "avoid":
        priority = priority if pinned else 0
        reasons.append("avoid 僅作指定觀察，不作進場")

    if entry_status != "avoid" or pinned:
        priority += min(max(bullish_score, 0), 100) * 0.8
        priority += min(max(confidence_score, 0), 100) * 0.4
        priority += max(0, 70 - min(max(risk_score, 0), 100)) * 0.8
        if volume_ratio is not None:
            priority += min(volume_ratio, 3.0) * 18
        if trigger_readiness == "near":
            priority += 80
            reasons.append("接近觸發")
        if _near_level(last_price, trigger_price, pct=0.5):
            priority += 70
            reasons.append("接近觸發價")
        if _near_level(last_price, vwap, pct=0.35):
            priority += 55
            reasons.append("接近 VWAP 關鍵位")
        if risk_score > 70:
            priority -= 120
            reasons.append("風險過高，降低即時追蹤順位")

    can_confirm = entry_status in {
        "executable",
        "practice_long",
        "wait_breakout",
        "wait_vwap",
        "wait_volume",
        "wait_pullback",
    } and risk_score <= 70
    return FuglePriorityItem(
        symbol=symbol,
        name=str(_value(item, "name", default="")),
        grade=grade,
        entry_status=entry_status,
        trade_bias=trade_bias,
        last_price=last_price,
        vwap=vwap,
        volume_ratio=volume_ratio,
        trigger_price=trigger_price,
        stop_loss=stop_loss,
        risk_score=round(risk_score, 2),
        bullish_score=round(bullish_score, 2),
        confidence_score=round(confidence_score, 2),
        trigger_readiness=trigger_readiness or "-",
        priority_score=round(max(priority, 0.0), 2),
        priority_reason="；".join(reasons[:4]) or "一般觀察",
        tracking_purpose=_tracking_purpose(entry_status, grade, trigger_readiness),
        can_use_for_entry_confirmation=bool(can_confirm),
    )


def _tracking_purpose(entry_status: str, grade: str, readiness: str) -> str:
    if entry_status == "executable":
        return "進場前五檔 / 逐筆確認"
    if readiness in {"ready", "near"}:
        return "B+ 觸發確認"
    if entry_status == "practice_long":
        return "虛擬交易練習觀察"
    if entry_status == "wait_vwap":
        return "確認是否站回 VWAP"
    if entry_status == "wait_volume":
        return "確認量比是否放大"
    if entry_status == "wait_breakout":
        return "確認是否突破觸發價"
    if entry_status == "high_risk":
        return "只追蹤風險降溫，不作進場"
    return f"{grade or '觀察'} 重點追蹤"


def _near_level(price: Optional[float], level: Optional[float], *, pct: float) -> bool:
    if price is None or level is None or level <= 0:
        return False
    return abs(price - level) / level * 100 <= pct


def _value(item: Any, key: str, default=None):
    if isinstance(item, dict):
        return item.get(key, default)
    return getattr(item, key, default)


def _normalize_symbol(value) -> str:
    return normalize_tw_stock_symbol(value)


def _float(value, default=None) -> Optional[float]:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _env_bool(name: str) -> bool:
    return str(os.environ.get(name, "")).strip().lower() in {"1", "true", "yes", "on"}
