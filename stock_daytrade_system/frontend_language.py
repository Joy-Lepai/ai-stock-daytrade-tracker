from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Optional

FRONTEND_LANGUAGE_VERSION = "frontend_language_v3_front_four_categories_2026-06-21"


FRONT_CATEGORY_STRONG_BUY = "強烈買多"
FRONT_CATEGORY_BUY = "買多"
FRONT_CATEGORY_WATCH = "觀察"
FRONT_CATEGORY_BEARISH = "看空"
FRONT_CATEGORY_DATA_MISSING = "資料不足"


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


@dataclass(frozen=True)
class FrontendTradeLabel:
    front_category: str
    front_summary: str
    top_reason: str
    next_trigger: str
    invalid_condition: str
    can_show_as_buy: bool
    can_show_as_strong_buy: bool
    reason_codes: list[str]

    def to_dict(self) -> dict:
        return asdict(self)


def build_frontend_trade_label(
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
) -> FrontendTradeLabel:
    entry = _string(_get(item, "entry_status"))
    grade = _string(_get(item, "grade"))
    trade_bias = _string(_get(item, "trade_bias"))
    above_vwap = bool(_get(item, "above_vwap")) if _get(item, "above_vwap") is not None else False
    price = _number(_get(item, "last_price")) or _number(_get(item, "latest_price"))
    vwap = _number(_get(item, "vwap"))
    volume_ratio = _number(_get(item, "volume_ratio"))
    stop_loss = _number(_get(item, "stop_loss"))
    risk_score = _number(_get(item, "risk_score"), default=999.0) or 999.0
    confidence_score = _number(_get(item, "confidence_score"), default=0.0) or 0.0
    bullish_score = _number(_get(item, "bullish_score"), default=0.0) or 0.0
    change_pct = _number(_get(item, "change_pct"), default=0.0) or 0.0
    break_prev_high = bool(_get(item, "break_prev_high"))
    break_intraday_high = bool(_get(item, "break_intraday_high")) or bool(_get(item, "break_orb_15m_high"))
    trigger_price = _number(_get(item, "trigger_price")) or _number(_get(item, "previous_high"))
    risk_reasons = _list_text(_get(item, "risk_reasons"))
    reasons = _list_text(_get(item, "reasons"))
    conflicts = _conflict_codes(_get(item, "conflicts"))
    confidence_summary = _string(_get(item, "confidence_summary"))
    item_price_status = _string(
        _get(item, "price_status")
        or _get(item, "price_status_label")
        or _get(item, "quote_state")
        or _get(item, "data_status")
    )
    price_status = (price_status_label or item_price_status).strip().lower()
    item_uses_last_known = bool(
        uses_last_known
        or _get(item, "uses_last_known")
        or _get(item, "fallback_used")
        or _get(item, "is_cached")
    )
    item_is_delayed = bool(is_delayed or _get(item, "is_delayed"))
    item_data_missing = bool(data_missing or _get(item, "data_missing") or _get(item, "is_data_missing"))
    missing_or_unusable = item_data_missing or grade == "data_missing" or entry == "data_missing" or price_status in {"missing", "資料不足"}
    non_live_data = (
        stale
        or item_uses_last_known
        or item_is_delayed
        or price_status in {"cached", "delayed", "missing", "使用上一筆", "資料延遲", "資料不足"}
    )
    stop_distance = _stop_distance_pct(price, stop_loss)
    vwap_distance = _vwap_distance_pct(price, vwap)
    near_or_at_limit_risk = change_pct >= 9 or _contains_any(risk_reasons, ["漲停", "追價"])
    chase_risk = (
        entry == "high_risk"
        or risk_score > 65
        or (stop_distance is not None and stop_distance > 5)
        or (vwap_distance is not None and vwap_distance > 3)
        or near_or_at_limit_risk
        or "long_upper_shadow" in conflicts
    )
    long_failed = (
        entry == "avoid"
        or trade_bias == "short"
        or "failed_breakout" in conflicts
        or "lower_low" in conflicts
        or bool(_get(item, "failed_breakout"))
        or bool(_get(item, "lower_low"))
    )

    reason_codes: list[str] = []

    def label(
        category: str,
        summary: str,
        top_reason: str,
        next_trigger: str,
        invalid_condition: str,
        *,
        can_buy: bool = False,
        can_strong: bool = False,
        codes: Optional[list[str]] = None,
    ) -> FrontendTradeLabel:
        return FrontendTradeLabel(
            front_category=category,
            front_summary=summary,
            top_reason=top_reason,
            next_trigger=next_trigger,
            invalid_condition=invalid_condition,
            can_show_as_buy=can_buy,
            can_show_as_strong_buy=can_strong,
            reason_codes=_dedupe((reason_codes or []) + (codes or [])),
        )

    if missing_or_unusable or vwap is None or volume_ratio is None:
        if vwap is None:
            top = "缺 VWAP，不能產生做多判斷。"
            code = "missing_vwap"
        elif volume_ratio is None:
            top = "缺量比，不能產生做多判斷。"
            code = "missing_volume_ratio"
        elif item_uses_last_known or price_status in {"cached", "使用上一筆"}:
            top = "使用上一筆有效價格，僅供觀察，不作為進場依據。"
            code = "cached"
        elif item_is_delayed or price_status in {"delayed", "資料延遲"}:
            top = "資料延遲，僅供觀察，不作為進場依據。"
            code = "delayed"
        else:
            top = "資料不足，不能產生有效當沖建議。"
            code = "data_missing"
        return label(
            FRONT_CATEGORY_WATCH,
            top,
            top,
            "等待資料恢復即時、VWAP 與量比完整後再評估。",
            "資料持續缺漏、延遲或使用上一筆。",
            codes=[code],
        )

    if non_live_data:
        if stale:
            top = "資料過期，僅供觀察，不作為進場依據。"
            code = "stale_data"
        elif item_uses_last_known or price_status in {"cached", "使用上一筆"}:
            top = "使用上一筆有效價格，僅供觀察，不作為進場依據。"
            code = "cached"
        elif item_is_delayed or price_status in {"delayed", "資料延遲"}:
            top = "資料延遲，僅供觀察，不作為進場依據。"
            code = "delayed"
        else:
            top = "資料非即時，僅供觀察，不作為進場依據。"
            code = "non_live_data"
        return label(
            FRONT_CATEGORY_WATCH,
            top,
            top,
            "等待資料狀態恢復為即時後，再重新評估 VWAP、量比與突破。",
            "資料持續延遲、使用上一筆或分層刷新過期。",
            codes=[code],
        )

    if market_mode != "intraday" or not intraday or not data_today:
        top = "目前不是盤中即時模式，僅供復盤或觀察。"
        return label(
            FRONT_CATEGORY_WATCH,
            top,
            top,
            "等下一個交易日盤中資料恢復即時後再評估。",
            "非盤中、休市或資料不是今天。",
            codes=["not_intraday_mode"],
        )

    if long_failed:
        top = "多方結構失效，暫不做多。"
        if "failed_breakout" in conflicts or bool(_get(item, "failed_breakout")):
            top = "假突破後跌回，多方結構轉弱。"
        elif trade_bias == "short":
            top = "價格結構偏弱，暫不做多。"
        return label(
            FRONT_CATEGORY_BEARISH,
            "多方結構失效，暫不做多。",
            top,
            "等待重新站回 VWAP、量能轉強且突破失敗風險解除後再評估。",
            "持續跌破 VWAP、開盤區間低點或低點下彎。",
            codes=[entry or "bearish"],
        )

    if chase_risk:
        return label(
            FRONT_CATEGORY_WATCH,
            "強勢但追價風險高，不列入今日做多。",
            _first_sentence(risk_reasons, "追價風險高，停損距離、漲幅或 VWAP 距離偏大。"),
            "避免追價，等待拉回 VWAP 附近、停損距離縮小或風險分數下降後再評估。",
            "追價風險未降溫、跌回 VWAP、長上影擴大或量能退潮。",
            codes=["high_risk"],
        )

    if not above_vwap or entry == "wait_vwap":
        return label(
            FRONT_CATEGORY_WATCH,
            "方向偏多，但尚未站上 VWAP，等待確認。",
            "尚未站上 VWAP。",
            "價格站回 VWAP 並維持，量比接近或高於 1.0 後再重新評估。",
            "無法站回 VWAP，或站回後快速跌破。",
            codes=["below_vwap" if not above_vwap else "wait_vwap"],
        )

    if entry == "practice_long":
        return label(
            FRONT_CATEGORY_WATCH,
            "練習買多，僅供虛擬交易與樣本累積，不是正式訊號。",
            "條件接近，但仍屬練習觀察。",
            "等待系統觸發，或用虛擬交易紀錄觀察，不作為正式進場依據。",
            "跌破 VWAP、量能退潮或資料轉為延遲。",
            codes=["practice_long"],
        )

    breakout_ready = break_prev_high or break_intraday_high or _near_trigger(price, trigger_price)
    stop_ok = stop_distance is not None and 0 < stop_distance <= 5
    strong_buy_ready = (
        allow_strong_long
        and above_vwap
        and volume_ratio >= 1.0
        and bullish_score >= 75
        and risk_score <= 55
        and confidence_score >= 60
        and breakout_ready
        and stop_ok
        and entry not in {"wait_vwap", "high_risk", "avoid", "data_missing", "practice_long"}
    )
    if strong_buy_ready:
        if entry == "executable":
            top = "多方條件完整，進入重點盯盤。"
            next_trigger = "進場前再確認進場雷達、停損距離與部位風控。"
            codes = ["strong_buy", "executable"]
        else:
            top = "多方條件完整，接近進場觸發。"
            next_trigger = _long_next_step(entry)
            codes = ["strong_buy", entry or "watch"]
        return label(
            FRONT_CATEGORY_STRONG_BUY,
            "多方條件完整，進入重點盯盤。仍需等待進場雷達與風控確認。",
            top,
            next_trigger,
            "跌破 VWAP、跌破停損價、突破失敗或資料轉為非即時。",
            can_buy=True,
            can_strong=True,
            codes=codes,
        )

    if above_vwap and risk_score <= 60 and confidence_score >= 50 and volume_ratio >= 0.8:
        if entry == "wait_volume":
            top = "方向偏多，但量能仍需確認。"
            next_trigger = "量比放大到 1.0 以上，且價格維持 VWAP 上方。"
            code = "wait_volume"
        elif entry == "wait_breakout" or not breakout_ready:
            top = "方向偏多，但仍等待突破確認。"
            next_trigger = "突破觸發價或昨日高點，並維持 VWAP 上方。"
            code = "wait_breakout"
        elif entry == "wait_pullback":
            top = "方向偏多，但等待拉回後風險更合理。"
            next_trigger = "拉回 VWAP 附近不跌破，再重新評估。"
            code = "wait_pullback"
        else:
            top = _first_sentence(reasons, "方向偏多，等待量能、突破或進場雷達確認。")
            next_trigger = "等待進場雷達確認價格墊高、量能延續與停損距離合理。"
            code = entry or "buy_watch"
        return label(
            FRONT_CATEGORY_BUY,
            "方向偏多，等待量能、突破或進場雷達確認。",
            top,
            next_trigger,
            "跌破 VWAP、量能退潮、突破失敗或風險分數升高。",
            can_buy=True,
            can_strong=False,
            codes=[code],
        )

    if above_vwap and volume_ratio < 0.8:
        return label(
            FRONT_CATEGORY_WATCH,
            "方向偏多，但量比不足，等待確認。",
            "量比不足。",
            "量比放大到 0.8 至 1.0 以上，且股價維持 VWAP 上方。",
            "量能無法補上，或跌破 VWAP。",
            codes=["wait_volume"],
        )

    return label(
        FRONT_CATEGORY_WATCH,
        "方向偏多但條件尚未完整，先列入觀察。",
        _first_sentence(reasons, confidence_summary or "VWAP、量能、突破或風控條件尚未完整。"),
        _long_next_step(entry),
        "跌破 VWAP、量能退潮、資料轉為非即時或風險升高。",
        codes=[entry or "watch"],
    )


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
    label = build_frontend_trade_label(
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
    return FrontTradeView(
        category=label.front_category,
        subtitle=label.front_summary,
        headline=f"{label.front_category}｜{label.front_summary}",
        reason=label.top_reason,
        next_step=label.next_trigger,
        is_strong_long_allowed=label.can_show_as_strong_buy,
        reason_codes=list(label.reason_codes),
    )


def front_trade_counts(items: list[Any], **kwargs: Any) -> dict:
    counts = {
        FRONT_CATEGORY_STRONG_BUY: 0,
        FRONT_CATEGORY_BUY: 0,
        FRONT_CATEGORY_WATCH: 0,
        FRONT_CATEGORY_BEARISH: 0,
        FRONT_CATEGORY_DATA_MISSING: 0,
    }
    views = []
    for item in items:
        view = front_trade_view(item, **kwargs)
        counts[view.category] = counts.get(view.category, 0) + 1
        if "資料不足" in view.subtitle or "缺" in view.subtitle:
            counts[FRONT_CATEGORY_DATA_MISSING] += 1
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
            category=str(view.get("category") or FRONT_CATEGORY_WATCH),
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
        if view.category == FRONT_CATEGORY_STRONG_BUY and not view.is_strong_long_allowed:
            final = FRONT_CATEGORY_WATCH
        elif view.category == FRONT_CATEGORY_STRONG_BUY and market_mode == "intraday":
            final = FRONT_CATEGORY_BUY if not _has_executable_entry(item) else view.category
        else:
            final = FRONT_CATEGORY_WATCH
        top_reason = "前日爆量漲停，等待續強確認"
        next_trigger = "開盤後站上 VWAP、量比維持 1.0 以上、未跌破開盤低點，並突破前高後再重新評估。"
        invalid_condition = "開高走低、跌破 VWAP、量能退潮、爆量但價格無法再墊高或長上影擴大。"
    else:
        final = view.category if view.category in {FRONT_CATEGORY_STRONG_BUY, FRONT_CATEGORY_BUY, FRONT_CATEGORY_WATCH, FRONT_CATEGORY_BEARISH} else FRONT_CATEGORY_WATCH
    data_unusable = _data_unusable(data_missing, stale, uses_last_known, is_delayed, price_status_label) or _item_data_unusable(item)
    if data_unusable:
        final = FRONT_CATEGORY_WATCH if final != FRONT_CATEGORY_BEARISH else final
    precision = _precision_score(item, view, radar, final_decision=final, data_unusable=data_unusable)
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
        is_strong_long_candidate=final == FRONT_CATEGORY_STRONG_BUY,
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
        "executable": "資料安全規則未完全通過，先不要視為強烈買多。",
    }.get(entry, "等待條件確認，不為了交易而交易。")


def _entry_state_from_view(view: FrontTradeView) -> str:
    if view.category == FRONT_CATEGORY_STRONG_BUY:
        return "可進場觀察" if view.is_strong_long_allowed else "接近觸發"
    if "資料不足" in view.subtitle:
        return "資料不足"
    if view.category == "觀察" and ("追價風險" in view.subtitle or "不適合" in view.subtitle):
        return "暫不進場"
    if view.category == FRONT_CATEGORY_BUY:
        return "等待確認"
    if view.category == FRONT_CATEGORY_BEARISH:
        return "暫不進場"
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


def _item_data_unusable(item: Any) -> bool:
    price_status = str(
        _get(item, "price_status")
        or _get(item, "price_status_label")
        or _get(item, "quote_state")
        or _get(item, "data_status")
        or ""
    ).lower()
    return bool(
        _get(item, "data_missing")
        or _get(item, "is_data_missing")
        or _get(item, "uses_last_known")
        or _get(item, "fallback_used")
        or _get(item, "is_cached")
        or _get(item, "is_delayed")
        or price_status in {"cached", "delayed", "missing", "使用上一筆", "資料延遲", "資料不足"}
    )


def _precision_score(item: Any, view: FrontTradeView, radar: dict, *, final_decision: str, data_unusable: bool) -> float:
    bullish = _number(_get(item, "bullish_score"), default=0.0) or 0.0
    confidence = _number(_get(item, "confidence_score"), default=0.0) or 0.0
    risk = _number(_get(item, "risk_score"), default=60.0) or 60.0
    volume_ratio = _number(_get(item, "volume_ratio"), default=0.0) or 0.0
    score = bullish * 0.35 + confidence * 0.35 + max(0.0, 100 - risk) * 0.2 + min(volume_ratio, 2.0) * 5
    if final_decision == FRONT_CATEGORY_STRONG_BUY:
        score += 8
    elif final_decision == FRONT_CATEGORY_BUY:
        score += 3
    elif final_decision == FRONT_CATEGORY_WATCH:
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


def _conflict_codes(value: Any) -> set[str]:
    if not isinstance(value, list):
        return set()
    result: set[str] = set()
    for item in value:
        if isinstance(item, dict) and item.get("code"):
            result.add(str(item["code"]))
        elif isinstance(item, str):
            result.add(item)
    return result


def _stop_distance_pct(price: Optional[float], stop_loss: Optional[float]) -> Optional[float]:
    if price is None or stop_loss is None or price <= 0 or stop_loss <= 0 or stop_loss >= price:
        return None
    return (price - stop_loss) / price * 100


def _vwap_distance_pct(price: Optional[float], vwap: Optional[float]) -> Optional[float]:
    if price is None or vwap is None or vwap <= 0:
        return None
    return (price - vwap) / vwap * 100


def _near_trigger(price: Optional[float], trigger_price: Optional[float]) -> bool:
    if price is None or trigger_price is None or trigger_price <= 0:
        return False
    return price >= trigger_price * 0.995


def _contains_any(values: list[str], keywords: list[str]) -> bool:
    joined = " ".join(values)
    return any(keyword in joined for keyword in keywords)


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result
