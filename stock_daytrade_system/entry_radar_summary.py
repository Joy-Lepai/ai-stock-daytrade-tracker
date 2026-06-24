from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Optional


ENTRY_RADAR_SUMMARY_VERSION = "entry_radar_summary_v1_reason_ranking_2026-06-21"


@dataclass(frozen=True)
class EntryRadarSummary:
    version: str
    entry_state: str
    blocker_code: str
    blocker_summary: str
    next_trigger: str
    confirmation_note: str
    reason_rank: list[dict]
    does_not_change_model: bool = True

    def to_dict(self) -> dict:
        return asdict(self)


def build_entry_radar_summary(
    *,
    candidate: Optional[Any],
    data_health: Optional[dict] = None,
    entry_confirmation: Optional[dict] = None,
    safety: Optional[dict] = None,
    market_mode: str = "intraday",
    intraday: bool = True,
) -> EntryRadarSummary:
    candidate = candidate or {}
    data_health = data_health or {}
    entry_confirmation = entry_confirmation or {}
    safety = safety or {}
    reasons = _rank_reasons(candidate, data_health, entry_confirmation, safety, market_mode=market_mode, intraday=intraday)
    core_reasons = [item for item in reasons if not item.get("confirmation_only")]
    confirmation_reasons = [item for item in reasons if item.get("confirmation_only")]
    top = core_reasons[0] if core_reasons else (confirmation_reasons[0] if confirmation_reasons else _ok_reason(candidate))
    entry_state = _entry_state(candidate, data_health, entry_confirmation, safety, top, market_mode=market_mode, intraday=intraday)
    confirmation_note = _confirmation_note(confirmation_reasons)
    blocker_summary = str(top.get("message") or "核心條件大致接近，仍需等待盤中確認。")
    if bool(top.get("confirmation_only")) and confirmation_note:
        blocker_summary = confirmation_note
    if confirmation_note and confirmation_note not in blocker_summary and _entry_status(candidate) in {"high_risk", "avoid"}:
        blocker_summary = f"{blocker_summary}，且{confirmation_note}"
    return EntryRadarSummary(
        version=ENTRY_RADAR_SUMMARY_VERSION,
        entry_state=entry_state,
        blocker_code=str(top.get("code") or "ready_watch"),
        blocker_summary=blocker_summary,
        next_trigger=_next_trigger_for(top, candidate, data_health, entry_confirmation),
        confirmation_note=confirmation_note,
        reason_rank=reasons,
    )


def _rank_reasons(
    candidate: Any,
    data_health: dict,
    entry_confirmation: dict,
    safety: dict,
    *,
    market_mode: str,
    intraday: bool,
) -> list[dict]:
    reasons: list[dict] = []
    price_status = str(data_health.get("price_status") or data_health.get("quote_state") or "").lower()
    is_live = bool(data_health.get("is_live") or data_health.get("can_use_for_intraday_signal"))
    uses_last_known = bool(data_health.get("uses_last_known") or data_health.get("uses_cache"))
    is_delayed = bool(data_health.get("is_delayed") or price_status in {"delayed", "cached"})
    data_missing = bool(data_health.get("is_data_missing") or _entry_status(candidate) == "data_missing")
    if data_missing or price_status == "missing":
        reasons.append(_reason(10, "data_missing", "資料不足，缺少必要行情資料，不能判斷。"))
    if market_mode != "intraday" or not intraday:
        reasons.append(_reason(11, "not_intraday", "目前不是台股盤中模式，不顯示即時進場判斷。"))
    if uses_last_known:
        reasons.append(_reason(12, "cached_price", "使用上一筆有效價格，僅供觀察，不可作為即時進場依據。"))
    if is_delayed and not uses_last_known:
        reasons.append(_reason(13, "delayed_price", "資料延遲，僅供觀察，不顯示即時進場。"))
    if not is_live and market_mode == "intraday" and not data_missing:
        reasons.append(_reason(14, "not_live", "資料非即時，等待資料狀態恢復 live。"))
    if _num(_get(candidate, "last_price")) is None:
        reasons.append(_reason(15, "missing_price", "缺最新成交價，不能判斷。"))
    if _num(_get(candidate, "vwap")) is None:
        reasons.append(_reason(16, "missing_vwap", "缺 VWAP，不能產生有效進場判斷。"))
    if _num(_get(candidate, "volume_ratio")) is None:
        reasons.append(_reason(17, "missing_volume_ratio", "缺量比，不能確認短線資金是否進場。"))
    if _num(_get(candidate, "stop_loss")) is None:
        reasons.append(_reason(18, "missing_stop_loss", "缺停損價，不能計算部位風險。"))

    price = _num(_get(candidate, "last_price"))
    vwap = _num(_get(candidate, "vwap"))
    if vwap is not None and price is not None:
        distance = (price - vwap) / vwap * 100 if vwap else 0
        if not bool(_get(candidate, "above_vwap")):
            reasons.append(_reason(30, "below_vwap", f"尚未站穩 VWAP，目前 VWAP 約 {vwap:g}。"))
        elif distance > 3:
            reasons.append(_reason(31, "too_far_from_vwap", f"距離 VWAP 約 {distance:.2f}%，追價風險偏高。"))
    trigger_price = _num(_get(candidate, "trigger_price"))
    if _entry_status(candidate) == "wait_breakout" and trigger_price:
        reasons.append(_reason(32, "wait_breakout", f"尚未突破觸發價 {trigger_price:g}。"))
    if not bool(_get(candidate, "break_prev_high")) and _entry_status(candidate) in {"wait_breakout", "practice_long"}:
        prev_high = _num(_get(candidate, "previous_high"))
        message = f"尚未突破昨日高點 {prev_high:g}。" if prev_high else "尚未突破昨日高點。"
        reasons.append(_reason(33, "no_prev_high_breakout", message))
    if str(entry_confirmation.get("price_tick_trend") or "") == "weak":
        reasons.append(_reason(34, "price_not_rising", "最新價尚未連續墊高，短線動能未完全確認。"))

    volume_ratio = _num(_get(candidate, "volume_ratio"))
    if volume_ratio is not None and volume_ratio < 1:
        reasons.append(_reason(50, "low_volume_ratio", f"量比 {volume_ratio:.2f}x，尚未達 1.0x 確認門檻。"))
    turnover = _num(_get(candidate, "turnover"))
    if turnover is not None and turnover < 30_000_000:
        reasons.append(_reason(51, "low_turnover", "成交金額偏低，當沖流動性仍需觀察。"))
    if str(entry_confirmation.get("volume_status") or "") == "weak":
        reasons.append(_reason(52, "volume_weak", "量能尚未確認或出現退潮。"))

    entry = _entry_status(candidate)
    risk_score = _num(_get(candidate, "risk_score"), default=0.0) or 0.0
    if entry == "high_risk":
        reasons.append(_reason(70, "high_risk", "追價風險高，不列入今日做多。"))
    if entry == "avoid":
        reasons.append(_reason(71, "avoid", "目前條件不適合，暫時避開。"))
    if risk_score > 55:
        reasons.append(_reason(72, "risk_score_high", f"風險分數 {risk_score:.0f} 偏高。"))
    stop_distance = _stop_distance_pct(candidate)
    if stop_distance is not None and stop_distance > 3:
        reasons.append(_reason(73, "stop_distance_large", f"停損距離約 {stop_distance:.2f}%，部位風險偏大。"))
    if _has_text(_get(candidate, "risk_reasons"), "上影"):
        reasons.append(_reason(74, "long_upper_shadow", "上影線偏長，追價容易回落。"))
    if _has_text(_get(candidate, "risk_reasons"), "停利空間"):
        reasons.append(_reason(75, "target_space_limited", "停利空間不足，賺賠比不佳。"))

    large_trade_status = str(entry_confirmation.get("large_trade_status") or data_health.get("fugle_large_trade_status") or "missing")
    orderbook_status = str(entry_confirmation.get("orderbook_status") or data_health.get("fugle_quote_five_level_status") or data_health.get("twse_mis_five_level_status") or "missing")
    if large_trade_status == "missing":
        reasons.append(_reason(90, "missing_tick", "缺逐筆成交資料，無法判斷大單敲進 / 敲出。", confirmation_only=True))
    elif large_trade_status == "sell_sweep":
        reasons.append(_reason(91, "large_sell_sweep", "疑似大單敲出，進場需保守。"))
    elif large_trade_status in {"neutral", "unknown"}:
        reasons.append(_reason(92, "no_large_buy", "尚未看到明確大單敲進。", confirmation_only=True))
    if orderbook_status == "missing":
        reasons.append(_reason(93, "missing_orderbook", "缺五檔委買委賣資料，無法判斷委買委賣壓力。", confirmation_only=True))
    elif orderbook_status == "sell_pressure":
        reasons.append(_reason(94, "orderbook_sell_pressure", "五檔賣壓偏重。"))
    if str(entry_confirmation.get("bid_volume_trend") or "") == "deteriorating":
        reasons.append(_reason(95, "bid_volume_down", "委買量轉弱，買盤支撐不足。"))
    if str(entry_confirmation.get("ask_volume_trend") or "") == "deteriorating":
        reasons.append(_reason(96, "ask_volume_up", "委賣量增加，短線賣壓升高。"))

    return sorted(_dedupe_reasons(reasons), key=lambda item: (int(item["priority"]), str(item["code"])))


def _entry_state(
    candidate: Any,
    data_health: dict,
    entry_confirmation: dict,
    safety: dict,
    top: dict,
    *,
    market_mode: str,
    intraday: bool,
) -> str:
    entry = _entry_status(candidate)
    code = str(top.get("code") or "")
    confirmation_only = bool(top.get("confirmation_only"))
    if confirmation_only:
        if entry == "executable" and market_mode == "intraday" and intraday and bool(data_health.get("is_live", True)):
            return "可進場觀察"
        if entry in {"practice_long", "wait_breakout"}:
            return "接近觸發"
        return "等待確認"
    if code.startswith("missing_") or code == "data_missing":
        return "資料不足"
    if code in {"not_intraday", "cached_price", "delayed_price", "not_live"}:
        return "暫不進場"
    if entry in {"high_risk", "avoid"} or code in {"high_risk", "avoid", "risk_score_high", "stop_distance_large"}:
        return "暫不進場"
    if bool(safety.get("is_executable_allowed")) or (
        entry == "executable"
        and market_mode == "intraday"
        and intraday
        and bool(data_health.get("is_live", True))
        and code not in {"below_vwap", "too_far_from_vwap", "low_volume_ratio"}
    ):
        return "可進場觀察"
    if str(entry_confirmation.get("status") or "") in {"ready", "near"} or entry in {"practice_long", "wait_breakout"}:
        return "接近觸發"
    return "等待確認"


def _next_trigger_for(top: dict, candidate: Any, data_health: dict, entry_confirmation: dict) -> str:
    code = str(top.get("code") or "")
    vwap = _num(_get(candidate, "vwap"))
    trigger = _num(_get(candidate, "trigger_price"))
    prev_high = _num(_get(candidate, "previous_high"))
    volume_ratio = _num(_get(candidate, "volume_ratio"))
    stop_distance = _stop_distance_pct(candidate)
    if code in {"not_intraday"}:
        return "等待進入台股正常盤中後，再重新判斷即時進場條件。"
    if code in {"cached_price", "delayed_price", "not_live"}:
        return "等待資料狀態從 delayed / cached 恢復為 live。"
    if code in {"data_missing", "missing_price"}:
        return "等待最新價、VWAP、量比與停損資料補齊後再判斷。"
    if code == "missing_vwap":
        return "等待 VWAP 成功計算後再重新評估。"
    if code == "missing_volume_ratio":
        return "等待量比成功計算後再重新評估。"
    if code == "missing_stop_loss":
        return "補齊停損價後，確認部位風險再重新評估。"
    if code == "below_vwap":
        return f"等待價格站上 VWAP {vwap:g} 並維持。" if vwap else "等待價格站上 VWAP 並維持。"
    if code == "too_far_from_vwap":
        return f"等待拉回 VWAP {vwap:g} 附近不跌破後，再重新評估。" if vwap else "等待拉回 VWAP 附近不跌破後，再重新評估。"
    if code in {"wait_breakout", "price_not_rising"}:
        return f"等待突破觸發價 {trigger:g}，且最新價連續墊高。" if trigger else "等待突破觸發價，且最新價連續墊高。"
    if code == "no_prev_high_breakout":
        return f"等待突破昨日高點 {prev_high:g}。" if prev_high else "等待突破昨日高點。"
    if code in {"low_volume_ratio", "volume_weak"}:
        return "等待量比放大到 1.0x 以上，且股價維持 VWAP 上方。"
    if code == "low_turnover":
        return "等待成交金額與流動性放大，再重新評估。"
    if code in {"high_risk", "risk_score_high", "long_upper_shadow", "target_space_limited"}:
        return f"等待拉回 VWAP {vwap:g} 附近、風險分數下降或重新突破後再評估。" if vwap else "等待追價風險降溫後再重新評估。"
    if code == "stop_distance_large":
        return f"等待停損距離從 {stop_distance:.2f}% 縮小到合理範圍。" if stop_distance is not None else "等待停損距離縮小到合理範圍。"
    if code == "avoid":
        return "等待結構重新轉強、站回 VWAP 並量能確認後再看。"
    if code == "large_sell_sweep":
        return "等待賣壓減弱，或重新出現大單敲進後再評估。"
    if code == "orderbook_sell_pressure":
        return "等待委買量增加、委賣量下降後再重新評估。"
    if code == "bid_volume_down":
        return "等待委買量回升，買盤支撐恢復。"
    if code == "ask_volume_up":
        return "等待委賣量下降，賣壓減輕。"
    if code == "missing_tick":
        return "若未來接上逐筆資料，可觀察是否出現大單敲進。"
    if code == "missing_orderbook":
        return "若未來接上五檔資料，可觀察委買量是否增加、委賣量是否下降。"
    if code == "no_large_buy":
        return "等待出現大單敲進，或價格突破後仍站穩 VWAP。"
    if trigger:
        return f"等待突破觸發價 {trigger:g}，並確認量比維持 1.0x 附近。"
    if volume_ratio is not None and volume_ratio < 1:
        return "等待量比放大到 1.0x 以上。"
    return "維持 VWAP 上方、量能不退潮，並確認停損距離合理後再評估。"


def _confirmation_note(reasons: list[dict]) -> str:
    messages = [str(item.get("message") or "") for item in reasons if item.get("message")]
    if not messages:
        return ""
    return "；".join(messages[:2])


def _ok_reason(candidate: Any) -> dict:
    entry = _entry_status(candidate)
    if entry == "executable":
        return _reason(999, "ready_watch", "核心進場條件接近完成，仍需確認停損與部位風控。")
    return _reason(999, "wait_confirmation", "方向偏多但仍需等待 VWAP、量能、突破或風控確認。")


def _reason(priority: int, code: str, message: str, *, confirmation_only: bool = False) -> dict:
    return {
        "priority": priority,
        "code": code,
        "message": message,
        "confirmation_only": confirmation_only,
    }


def _dedupe_reasons(items: list[dict]) -> list[dict]:
    seen = set()
    result = []
    for item in items:
        code = item.get("code")
        if code in seen:
            continue
        seen.add(code)
        result.append(item)
    return result


def _entry_status(candidate: Any) -> str:
    return str(_get(candidate, "entry_status") or "")


def _stop_distance_pct(candidate: Any) -> Optional[float]:
    price = _num(_get(candidate, "last_price"))
    stop = _num(_get(candidate, "stop_loss"))
    if price is None or stop is None or price <= 0:
        return None
    return max((price - stop) / price * 100, 0.0)


def _has_text(value: Any, needle: str) -> bool:
    if isinstance(value, list):
        text = " ".join(str(item) for item in value)
    else:
        text = str(value or "")
    return needle in text


def _get(item: Any, key: str) -> Any:
    if isinstance(item, dict):
        return item.get(key)
    return getattr(item, key, None)


def _num(value: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default
