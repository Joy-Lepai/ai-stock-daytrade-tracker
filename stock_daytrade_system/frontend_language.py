from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Optional

from stock_daytrade_system.signal_guard import evaluate_signal_guard
from stock_daytrade_system.strong_long import evaluate_strong_long_candidate


FRONTEND_LANGUAGE_VERSION = "frontend_language_v2_strong_long_candidate_2026-06-21"


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


@dataclass(frozen=True)
class FrontDecisionCard:
    final_decision: str
    top_reason: str
    next_trigger: str
    invalid_condition: str
    precision_score: float
    entry_state: str
    observation_type: str
    is_strong_long_candidate: bool
    is_executable: bool
    user_summary: str
    reason_codes: list[str]

    def to_dict(self) -> dict:
        return asdict(self)


def front_trade_view(
    item: Any,
    *,
    data_today: bool = True,
    intraday: bool = True,
    stale: bool = False,
    data_missing: bool = False,
    allow_strong_long: bool = True,
    market_mode: str = "intraday",
    price_status_label: str = "",
    uses_last_known: bool = False,
    is_delayed: bool = False,
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
    price_status = price_status_label.strip().lower()
    fallback_or_delayed = (
        uses_last_known
        or is_delayed
        or price_status in {"cached", "delayed", "missing", "使用上一筆", "資料延遲", "資料不足"}
    )
    missing_or_unusable = data_missing or price_status in {"missing", "資料不足"}
    safe_allow_strong_long = allow_strong_long and not fallback_or_delayed and not missing_or_unusable
    safe_stale = stale or is_delayed or price_status in {"delayed", "資料延遲"}

    guard = evaluate_signal_guard(
        item,
        data_today=data_today,
        intraday=intraday,
        stale=safe_stale,
        data_missing=missing_or_unusable,
        allow_strong_long=safe_allow_strong_long,
        market_mode=market_mode,
    )
    reason_codes = list(guard.reason_codes)
    strong_blockers = [_blocker_label(item.code, item.message) for item in guard.blockers]

    if guard.is_executable_allowed:
        return FrontTradeView(
            category="強烈做多",
            subtitle="可執行候選",
            headline="強烈做多｜符合可執行條件",
            reason=_first_sentence(reasons, "股價、VWAP、量能與風控條件目前較完整。"),
            next_step="先確認停損距離與部位大小，再用虛擬交易或既定風控追蹤。",
            is_strong_long_allowed=True,
            reason_codes=reason_codes or ["executable"],
        )

    strong_candidate = evaluate_strong_long_candidate(
        item,
        data_today=data_today,
        intraday=intraday,
        stale=safe_stale,
        data_missing=missing_or_unusable,
        allow_strong_long=safe_allow_strong_long,
        market_mode=market_mode,
        price_status_label=price_status_label,
        uses_last_known=uses_last_known,
        is_delayed=is_delayed,
    )
    if strong_candidate.is_candidate:
        candidate_codes = reason_codes or ["strong_long_candidate"]
        if "not_executable_yet" not in candidate_codes:
            candidate_codes.append("not_executable_yet")
        return FrontTradeView(
            category="強烈做多",
            subtitle=strong_candidate.subtitle,
            headline=f"強烈做多｜{strong_candidate.subtitle}",
            reason=strong_candidate.reason,
            next_step=strong_candidate.next_step,
            is_strong_long_allowed=True,
            reason_codes=candidate_codes,
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


def front_decision_card(
    item: Any,
    *,
    front_view: Optional[FrontTradeView] = None,
    entry_radar: Optional[Any] = None,
    data_today: bool = True,
    intraday: bool = True,
    stale: bool = False,
    data_missing: bool = False,
    allow_strong_long: bool = True,
    market_mode: str = "intraday",
    price_status_label: str = "",
    uses_last_known: bool = False,
    is_delayed: bool = False,
) -> FrontDecisionCard:
    view = front_view or front_trade_view(
        item,
        data_today=data_today,
        intraday=intraday,
        stale=stale,
        data_missing=data_missing,
        allow_strong_long=allow_strong_long,
        market_mode=market_mode,
        price_status_label=price_status_label,
        uses_last_known=uses_last_known,
        is_delayed=is_delayed,
    )
    if isinstance(view, dict):
        view = FrontTradeView(
            category=str(view.get("category") or "觀察"),
            subtitle=str(view.get("subtitle") or ""),
            headline=str(view.get("headline") or ""),
            reason=str(view.get("reason") or ""),
            next_step=str(view.get("next_step") or ""),
            is_strong_long_allowed=bool(view.get("is_strong_long_allowed")),
            reason_codes=[str(code) for code in (view.get("reason_codes") or [])],
        )
    radar = _to_dict(entry_radar)
    entry_state = str(radar.get("entry_state") or _entry_state_from_view(view))
    top_reason = str(radar.get("blocker_summary") or view.reason or "等待條件確認。")
    if str(radar.get("blocker_code") or "") in {"missing_tick", "missing_orderbook"} and _string(_get(item, "entry_status")) not in {"executable"}:
        top_reason = view.subtitle or top_reason
    next_trigger = str(radar.get("next_trigger") or view.next_step or "等待條件確認。")
    invalid_condition = _invalid_condition(item, view, radar)
    observation_type = _observation_type(item, top_reason)
    if observation_type == "續強觀察":
        if view.category == "強烈做多" and not view.is_strong_long_allowed:
            final = "觀察"
        elif view.category == "強烈做多" and market_mode == "intraday":
            final = "做多" if not _has_executable_entry(item) else view.category
        else:
            final = "觀察"
        top_reason = "前日爆量漲停，等待續強確認"
        next_trigger = "開盤後站上 VWAP、量比維持 1.0 以上、未跌破開盤低點，並突破前高後再重新評估。"
        invalid_condition = "開高走低、跌破 VWAP、量能退潮、爆量但價格無法再墊高或長上影擴大。"
    else:
        final = view.category if view.category in {"強烈做多", "做多", "觀察", "做空"} else "觀察"
    if _data_unusable(data_missing, stale, uses_last_known, is_delayed, price_status_label):
        final = "觀察" if final != "做空" else final
    precision = _precision_score(item, view, radar, final_decision=final, data_unusable=_data_unusable(data_missing, stale, uses_last_known, is_delayed, price_status_label))
    summary = f"{final}｜{top_reason}。下一步：{next_trigger}"
    codes = list(view.reason_codes)
    blocker = str(radar.get("blocker_code") or "")
    if blocker and blocker not in codes:
        codes.append(blocker)
    if observation_type and observation_type not in codes:
        codes.append(observation_type)
    return FrontDecisionCard(
        final_decision=final,
        top_reason=top_reason,
        next_trigger=next_trigger,
        invalid_condition=invalid_condition,
        precision_score=round(precision, 2),
        entry_state=entry_state,
        observation_type=observation_type,
        is_strong_long_candidate=final == "強烈做多",
        is_executable=bool(view.is_strong_long_allowed and _has_executable_entry(item)),
        user_summary=summary,
        reason_codes=codes,
    )


def _long_next_step(entry: str) -> str:
    return {
        "practice_long": "可列入虛擬交易與樣本累積，不是正式可執行訊號。",
        "wait_volume": "等待量比放大後再重新評估。",
        "wait_vwap": "等待站回 VWAP 並維持後再觀察。",
        "wait_breakout": "等待突破觸發價或昨日高點。",
        "wait_pullback": "等待拉回 VWAP 附近不破。",
        "executable": "資料安全規則未完全通過，先不要視為強烈做多。",
    }.get(entry, "等待條件確認，不為了交易而交易。")


def _entry_state_from_view(view: FrontTradeView) -> str:
    if view.category == "強烈做多":
        return "可進場觀察" if view.is_strong_long_allowed else "接近觸發"
    if "資料不足" in view.subtitle:
        return "資料不足"
    if view.category == "觀察" and ("追價風險" in view.subtitle or "不適合" in view.subtitle):
        return "暫不進場"
    if view.category == "做多":
        return "等待確認"
    return "等待確認"


def _invalid_condition(item: Any, view: FrontTradeView, radar: dict) -> str:
    explicit = str(radar.get("invalidation") or "").strip()
    if explicit:
        return explicit
    entry = _string(_get(item, "entry_status"))
    vwap = _number(_get(item, "vwap"))
    stop_loss = _number(_get(item, "stop_loss"))
    if entry == "high_risk":
        return "追價風險未降溫、跌回 VWAP、五檔賣壓升高或大單敲出。"
    if entry == "wait_vwap":
        return "無法站回 VWAP，或站回後快速跌破。"
    if entry == "wait_volume":
        return "量比無法放大到 1.0 以上，或量能放大但價格不再墊高。"
    if entry == "wait_breakout":
        return "突破失敗、跌回觸發價下方或跌破 VWAP。"
    if stop_loss:
        return f"跌破停損價 {stop_loss:g}、跌破 VWAP 或資料轉為延遲 / 使用上一筆。"
    if vwap:
        return f"跌破 VWAP {vwap:g}、量能退潮或資料轉為延遲 / 使用上一筆。"
    return "缺 VWAP、缺量比、資料過期或風險分數升高。"


def _observation_type(item: Any, top_reason: str) -> str:
    if _is_previous_limit_volume_watch(item):
        return "續強觀察"
    entry = _string(_get(item, "entry_status"))
    reason = top_reason or ""
    if entry == "high_risk" or "追價風險" in reason:
        return "高風險觀察"
    if "VWAP" in reason:
        return "VWAP 觀察"
    if "量比" in reason or "量能" in reason:
        return "量能觀察"
    if "突破" in reason:
        return "突破觀察"
    if "籌碼" in reason or "法人" in reason:
        return "籌碼觀察"
    if "族群" in reason:
        return "族群觀察"
    if "資料" in reason:
        return "資料不足觀察"
    return "動能觀察"


def _is_previous_limit_volume_watch(item: Any) -> bool:
    change_pct = _number(_get(item, "change_pct"), default=0.0) or 0.0
    volume_ratio = _number(_get(item, "daily_volume_ratio"), default=None)
    if volume_ratio is None:
        volume_ratio = _number(_get(item, "volume_ratio"), default=0.0) or 0.0
    upper_shadow = _number(_get(item, "upper_shadow_pct"), default=0.0) or 0.0
    turnover = _number(_get(item, "turnover"), default=0.0) or 0.0
    return change_pct >= 9.0 and volume_ratio >= 2.0 and turnover >= 50_000_000 and upper_shadow <= 2.5


def _has_executable_entry(item: Any) -> bool:
    return _string(_get(item, "entry_status")) == "executable"


def _data_unusable(data_missing: bool, stale: bool, uses_last_known: bool, is_delayed: bool, price_status_label: str) -> bool:
    price_status = str(price_status_label or "").lower()
    return bool(data_missing or stale or uses_last_known or is_delayed or price_status in {"cached", "delayed", "missing", "使用上一筆", "資料延遲", "資料不足"})


def _precision_score(item: Any, view: FrontTradeView, radar: dict, *, final_decision: str, data_unusable: bool) -> float:
    bullish = _number(_get(item, "bullish_score"), default=0.0) or 0.0
    confidence = _number(_get(item, "confidence_score"), default=0.0) or 0.0
    risk = _number(_get(item, "risk_score"), default=60.0) or 60.0
    volume_ratio = _number(_get(item, "volume_ratio"), default=0.0) or 0.0
    score = bullish * 0.35 + confidence * 0.35 + max(0.0, 100 - risk) * 0.2 + min(volume_ratio, 2.0) * 5
    if final_decision == "強烈做多":
        score += 8
    elif final_decision == "做多":
        score += 3
    elif final_decision == "觀察":
        score -= 5
    if str(radar.get("entry_state") or "") in {"可進場觀察", "接近觸發"}:
        score += 5
    if data_unusable:
        score = min(score, 45)
    return max(0.0, min(100.0, score))


def _to_dict(value: Any) -> dict:
    if isinstance(value, dict):
        return dict(value)
    if hasattr(value, "to_dict"):
        return value.to_dict()
    return {}


def _blocker_label(code: str, message: str) -> str:
    return {
        "not_today": "資料不是今天",
        "not_intraday": "非盤中資料",
        "stale_data": "資料過期",
        "refresh_layer_stale": "分層資料過期",
        "not_intraday_mode": "非盤中模式",
        "market_not_regular": "非台股盤中",
        "missing_vwap": "缺 VWAP",
        "missing_volume_ratio": "缺量比",
        "missing_stop_loss": "缺停損價",
        "data_missing": "資料缺漏",
    }.get(code, message.split("，", 1)[0])


def _get(item: Any, key: str) -> Any:
    if isinstance(item, dict):
        return item.get(key)
    return getattr(item, key, None)


def _string(value: Any) -> str:
    return "" if value is None else str(value)


def _number(value: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _list_text(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item)]
    if isinstance(value, str) and value:
        return [value]
    return []


def _first_sentence(values: list[str], fallback: str) -> str:
    return values[0] if values else fallback
