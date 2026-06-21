from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Iterable, Optional


STRONG_LONG_VERSION = "strong_long_v1_candidate_vs_executable_2026-06-21"


@dataclass(frozen=True)
class StrongLongResult:
    is_candidate: bool
    is_executable: bool
    subtitle: str
    reason: str
    next_step: str
    blockers: list[str]

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload["version"] = STRONG_LONG_VERSION
        return payload


def evaluate_strong_long_candidate(
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
) -> StrongLongResult:
    entry = _string(_get(item, "entry_status"))
    price_status = price_status_label.strip().lower()
    blockers: list[str] = []

    if market_mode != "intraday" or not intraday:
        blockers.append("非盤中模式")
    if not data_today:
        blockers.append("資料不是今天")
    if stale:
        blockers.append("資料過期")
    if not allow_strong_long:
        blockers.append("分層資料未達即時條件")
    if data_missing or entry == "data_missing":
        blockers.append("資料不足")
    if uses_last_known or price_status in {"cached", "使用上一筆"}:
        blockers.append("使用上一筆")
    if is_delayed or price_status in {"delayed", "資料延遲"}:
        blockers.append("資料延遲")
    if price_status in {"missing", "資料不足"}:
        blockers.append("資料不足")

    if entry in {"high_risk", "avoid", "data_missing"}:
        blockers.append(entry)

    price = _number(_get(item, "last_price")) or _number(_get(item, "latest_price"))
    vwap = _number(_get(item, "vwap"))
    volume_ratio = _number(_get(item, "volume_ratio"))
    bullish_score = _number(_get(item, "bullish_score")) or 0.0
    risk_score = _number(_get(item, "risk_score")) or 999.0
    confidence_score = _number(_get(item, "confidence_score")) or 0.0
    stop_loss = _number(_get(item, "stop_loss"))
    prev_high = _number(_get(item, "previous_high"))
    trigger_price = _number(_get(item, "trigger_price"))
    upper_shadow_pct = _number(_get(item, "upper_shadow_pct")) or 0.0
    above_vwap = bool(_get(item, "above_vwap"))
    break_prev_high = bool(_get(item, "break_prev_high"))
    conflicts = _conflict_codes(_get(item, "conflicts"))

    if vwap is None:
        blockers.append("缺 VWAP")
    if volume_ratio is None:
        blockers.append("缺量比")
    if not above_vwap:
        blockers.append("未站上 VWAP")
    if volume_ratio is not None and volume_ratio < 1.0:
        blockers.append("量比未達 1.0")
    if bullish_score < 75:
        blockers.append("多方分數低於 75")
    if risk_score > 55:
        blockers.append("風險分數高於 55")
    if confidence_score < 60:
        blockers.append("信心分數低於 60")
    if not _breakout_ready(price, prev_high, trigger_price, break_prev_high):
        blockers.append("尚未突破或接近突破價")
    if not _stop_loss_reasonable(price, stop_loss):
        blockers.append("停損距離不合理或缺停損價")
    if _vwap_distance_pct(price, vwap) is not None and (_vwap_distance_pct(price, vwap) or 0) > 3:
        blockers.append("距離 VWAP 過遠")
    if upper_shadow_pct >= 4 or "long_upper_shadow" in conflicts or "failed_breakout" in conflicts:
        blockers.append("長上影或假突破風險")

    blockers = _dedupe(blockers)
    is_candidate = not blockers
    is_executable = is_candidate and entry == "executable"

    if is_executable:
        subtitle = "可執行"
        reason = "強烈買多候選且已達 executable，可進一步檢查部位與風控。"
        next_step = "確認停損、停利與部位大小後，再依紀律執行或做虛擬交易驗證。"
    elif is_candidate:
        subtitle = _candidate_waiting_subtitle(entry)
        reason = "盤中條件高度符合，值得立即盯盤，但尚未等同可執行進場。"
        next_step = _candidate_next_step(entry)
    else:
        subtitle = "尚未達強烈買多"
        reason = "、".join(blockers[:3]) or "條件尚未完整。"
        next_step = "等待 VWAP、量能、突破、風控與資料條件同時改善。"

    return StrongLongResult(
        is_candidate=is_candidate,
        is_executable=is_executable,
        subtitle=subtitle,
        reason=reason,
        next_step=next_step,
        blockers=blockers,
    )


def build_strong_long_funnel(
    candidates: Iterable[Any],
    *,
    total_market_count: int = 0,
    momentum_candidate_count: int = 0,
    live_count: int = 0,
    context: Optional[dict[str, Any]] = None,
) -> dict:
    rows = list(candidates)
    context = context or {}
    results = [evaluate_strong_long_candidate(item, **context) for item in rows]
    blockers: dict[str, int] = {}
    for result in results:
        for blocker in result.blockers:
            blockers[blocker] = blockers.get(blocker, 0) + 1
    top_blockers = [
        {"reason": reason, "count": count}
        for reason, count in sorted(blockers.items(), key=lambda row: (-row[1], row[0]))[:8]
    ]
    return {
        "version": STRONG_LONG_VERSION,
        "total_market_count": int(total_market_count or 0),
        "momentum_candidate_count": int(momentum_candidate_count or 0),
        "model_candidates_count": len(rows),
        "live_count": int(live_count or 0),
        "above_vwap_count": sum(1 for item in rows if bool(_get(item, "above_vwap"))),
        "volume_ratio_gte_0_8_count": sum(1 for item in rows if (_number(_get(item, "volume_ratio")) or 0) >= 0.8),
        "volume_ratio_gte_1_0_count": sum(1 for item in rows if (_number(_get(item, "volume_ratio")) or 0) >= 1.0),
        "break_prev_high_count": sum(1 for item in rows if bool(_get(item, "break_prev_high"))),
        "bullish_score_gte_65_count": sum(1 for item in rows if (_number(_get(item, "bullish_score")) or 0) >= 65),
        "bullish_score_gte_70_count": sum(1 for item in rows if (_number(_get(item, "bullish_score")) or 0) >= 70),
        "bullish_score_gte_75_count": sum(1 for item in rows if (_number(_get(item, "bullish_score")) or 0) >= 75),
        "risk_score_lte_55_count": sum(1 for item in rows if (_number(_get(item, "risk_score")) or 999) <= 55),
        "risk_score_lte_40_count": sum(1 for item in rows if (_number(_get(item, "risk_score")) or 999) <= 40),
        "confidence_score_gte_55_count": sum(1 for item in rows if (_number(_get(item, "confidence_score")) or 0) >= 55),
        "confidence_score_gte_60_count": sum(1 for item in rows if (_number(_get(item, "confidence_score")) or 0) >= 60),
        "blocked_high_risk_count": sum(1 for item in rows if _string(_get(item, "entry_status")) == "high_risk"),
        "blocked_wait_volume_count": sum(1 for item in rows if _string(_get(item, "entry_status")) == "wait_volume"),
        "blocked_wait_vwap_count": sum(1 for item in rows if _string(_get(item, "entry_status")) == "wait_vwap"),
        "blocked_wait_breakout_count": sum(1 for item in rows if _string(_get(item, "entry_status")) == "wait_breakout"),
        "strong_long_candidate_count": sum(1 for result in results if result.is_candidate),
        "executable_count": sum(1 for item in rows if _string(_get(item, "entry_status")) == "executable"),
        "top_blockers": top_blockers,
    }


def _candidate_waiting_subtitle(entry: str) -> str:
    return {
        "wait_breakout": "等待突破",
        "wait_volume": "等待量能",
        "wait_vwap": "等待站回 VWAP",
        "wait_pullback": "等待拉回",
        "practice_long": "等待觸發",
    }.get(entry, "等待觸發")


def _candidate_next_step(entry: str) -> str:
    return {
        "wait_breakout": "等待突破昨高或觸發價後，再檢查是否可執行。",
        "wait_volume": "等待量比維持 1.0 以上，再重新評估。",
        "wait_vwap": "等待站上 VWAP 並維持後，再重新評估。",
        "wait_pullback": "等待拉回 VWAP 附近不破，再重新評估。",
        "practice_long": "可列入盯盤與虛擬交易練習，等待系統觸發。",
    }.get(entry, "持續盯盤，等觸發價與風控條件確認。")


def _breakout_ready(price: Optional[float], prev_high: Optional[float], trigger_price: Optional[float], break_prev_high: bool) -> bool:
    if break_prev_high:
        return True
    target = trigger_price or prev_high
    if price is None or target is None or target <= 0:
        return False
    return price >= target * 0.995


def _stop_loss_reasonable(price: Optional[float], stop_loss: Optional[float]) -> bool:
    if price is None or stop_loss is None or stop_loss <= 0 or stop_loss >= price:
        return False
    return ((price - stop_loss) / price * 100) <= 5


def _vwap_distance_pct(price: Optional[float], vwap: Optional[float]) -> Optional[float]:
    if price is None or vwap is None or vwap <= 0:
        return None
    return (price - vwap) / vwap * 100


def _conflict_codes(value: Any) -> set[str]:
    if not isinstance(value, list):
        return set()
    result = set()
    for item in value:
        if isinstance(item, dict) and item.get("code"):
            result.add(str(item["code"]))
    return result


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
