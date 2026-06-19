from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Optional


FRONTEND_LANGUAGE_VERSION = "frontend_language_v1_long_daytrade_2026-06-19"


@dataclass(frozen=True)
class FrontTradeView:
    category: str
    subtitle: str
    headline: str
    reason: str
    next_step: str
    is_strong_long_allowed: bool
    reason_codes: list[str]

    def to_dict(self) -> dict:
        return asdict(self)


def front_trade_view(
    item: Any,
    *,
    data_today: bool = True,
    intraday: bool = True,
    stale: bool = False,
    allow_strong_long: bool = True,
    market_mode: str = "intraday",
) -> FrontTradeView:
    entry = _string(_get(item, "entry_status"))
    grade = _string(_get(item, "grade"))
    trade_bias = _string(_get(item, "trade_bias"))
    above_vwap = bool(_get(item, "above_vwap")) if _get(item, "above_vwap") is not None else False
    vwap = _number(_get(item, "vwap"))
    volume_ratio = _number(_get(item, "volume_ratio"))
    stop_loss = _number(_get(item, "stop_loss"))
    risk_reasons = _list_text(_get(item, "risk_reasons"))
    reasons = _list_text(_get(item, "reasons"))
    confidence_summary = _string(_get(item, "confidence_summary"))
    reason_codes: list[str] = []

    strong_blockers: list[str] = []
    if not data_today:
        strong_blockers.append("資料不是今天")
        reason_codes.append("not_today")
    if not intraday:
        strong_blockers.append("非盤中資料")
        reason_codes.append("not_intraday")
    if stale:
        strong_blockers.append("資料過期")
        reason_codes.append("stale_data")
    if not allow_strong_long:
        strong_blockers.append("分層資料過期")
        reason_codes.append("refresh_layer_stale")
    if market_mode != "intraday":
        strong_blockers.append("非盤中模式")
        reason_codes.append("not_intraday_mode")
    if vwap is None:
        strong_blockers.append("缺 VWAP")
        reason_codes.append("missing_vwap")
    if volume_ratio is None:
        strong_blockers.append("缺量比")
        reason_codes.append("missing_volume_ratio")
    if stop_loss is None:
        strong_blockers.append("缺停損價")
        reason_codes.append("missing_stop_loss")

    is_true_executable = (
        entry == "executable"
        and not strong_blockers
        and grade != "data_missing"
        and entry not in {"practice_long", "wait_volume", "wait_vwap", "wait_breakout", "wait_pullback", "high_risk", "avoid", "data_missing"}
    )
    if is_true_executable:
        return FrontTradeView(
            category="強烈做多",
            subtitle="可執行候選",
            headline="強烈做多｜符合可執行條件",
            reason=_first_sentence(reasons, "股價、VWAP、量能與風控條件目前較完整。"),
            next_step="先確認停損距離與部位大小，再用虛擬交易或既定風控追蹤。",
            is_strong_long_allowed=True,
            reason_codes=reason_codes or ["executable"],
        )

    if entry in {"high_risk"}:
        return FrontTradeView(
            category="觀察",
            subtitle="方向偏多，但追價風險高，不列入今日做多",
            headline="觀察｜方向偏多，但追價風險高，不列入今日做多。",
            reason=_first_sentence(risk_reasons, "股價雖有動能，但追價風險、停損距離或風險分數偏高。"),
            next_step="避免追價，等待拉回 VWAP 附近、風險分數下降或重新突破後再評估。",
            is_strong_long_allowed=False,
            reason_codes=reason_codes or ["high_risk"],
        )
    if entry in {"avoid", "data_missing"} or grade == "data_missing":
        message = "資料不足，不能判斷" if entry == "data_missing" or grade == "data_missing" else "目前條件不適合"
        return FrontTradeView(
            category="觀察",
            subtitle=message,
            headline=f"觀察｜{message}。",
            reason=confidence_summary or _first_sentence(risk_reasons, "資料缺漏、結構不完整或風險偏高。"),
            next_step="等待資料恢復、重新站回 VWAP、量能確認或風險下降後再看。",
            is_strong_long_allowed=False,
            reason_codes=reason_codes or [entry or "avoid"],
        )

    if trade_bias == "short" and entry not in {"practice_long", "executable"}:
        return FrontTradeView(
            category="做空",
            subtitle="空方觀察",
            headline="做空｜空方條件觀察。",
            reason=_first_sentence(risk_reasons, "價格結構偏弱，需等待完整空方條件確認。"),
            next_step="若跌破 VWAP 且量能放大下殺，再進一步觀察；目前不硬做空。",
            is_strong_long_allowed=False,
            reason_codes=reason_codes or ["short_watch"],
        )

    if entry == "executable" and market_mode != "intraday":
        subtitle = "上一交易日強烈做多復盤，不提供即時進場判斷"
        reason_code = "review_executable"
    elif entry == "practice_long":
        subtitle = "練習買多，不是正式可執行"
        reason_code = "practice_long"
    elif entry == "wait_volume":
        subtitle = "等待量能"
        reason_code = "wait_volume"
    elif entry == "wait_vwap":
        subtitle = "等待站回 VWAP"
        reason_code = "wait_vwap"
    elif entry == "wait_breakout":
        subtitle = "等待突破"
        reason_code = "wait_breakout"
    elif entry == "wait_pullback":
        subtitle = "等待拉回"
        reason_code = "wait_pullback"
    elif entry == "executable":
        subtitle = "條件偏多，但資料安全規則尚未通過"
        reason_code = "executable_blocked"
    else:
        subtitle = "等待確認"
        reason_code = entry or "watch"
    if reason_code not in reason_codes:
        reason_codes.append(reason_code)
    if strong_blockers:
        subtitle = f"{subtitle}｜{strong_blockers[0]}"
    return FrontTradeView(
        category="做多",
        subtitle=subtitle,
        headline=f"做多｜{subtitle}。",
        reason=_first_sentence(reasons, confidence_summary or "方向偏多，但仍需等待 VWAP、量能、突破或風控條件確認。"),
        next_step=_long_next_step(entry),
        is_strong_long_allowed=False,
        reason_codes=reason_codes,
    )


def front_trade_counts(items: list[Any], **kwargs: Any) -> dict:
    counts = {"強烈做多": 0, "做多": 0, "觀察": 0, "做空": 0, "資料不足": 0}
    views = []
    for item in items:
        view = front_trade_view(item, **kwargs)
        counts[view.category] = counts.get(view.category, 0) + 1
        if "資料不足" in view.subtitle:
            counts["資料不足"] += 1
        views.append(view)
    return {"counts": counts, "views": views}


def _long_next_step(entry: str) -> str:
    return {
        "practice_long": "可列入虛擬交易與樣本累積，不是正式可執行訊號。",
        "wait_volume": "等待量比放大後再重新評估。",
        "wait_vwap": "等待站回 VWAP 並維持後再觀察。",
        "wait_breakout": "等待突破觸發價或昨日高點。",
        "wait_pullback": "等待拉回 VWAP 附近不破。",
        "executable": "資料安全規則未完全通過，先不要視為強烈做多。",
    }.get(entry, "等待條件確認，不為了交易而交易。")


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


def _list_text(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item)]
    if isinstance(value, str) and value:
        return [value]
    return []


def _first_sentence(values: list[str], fallback: str) -> str:
    return values[0] if values else fallback
