from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Iterable, List

from stock_daytrade_system.confidence_model import evaluate_signal
from stock_daytrade_system.trade_bias import evaluate_trade_bias
from stock_daytrade_system.us_data import USMarketSnapshot


US_MODEL_VERSION = "us_long_model_v2_practice_long"


@dataclass(frozen=True)
class USLongCandidate:
    symbol: str
    name_en: str
    name_zh: str
    short_name_zh: str
    sector_en: str
    sector_zh: str
    industry_en: str
    industry_zh: str
    description_zh: str
    latest_price: float
    previous_close: float
    open: float
    high: float
    low: float
    volume: float
    change_pct: float
    volume_ratio: float
    vwap: float
    above_vwap: bool
    premarket_high: float | None
    break_premarket_high: bool
    break_previous_high: bool
    break_opening_range_high: bool
    opening_range_high: float | None
    bullish_score: float
    risk_score: float
    grade: str
    entry_status: str
    original_entry_status: str
    adjusted_entry_status: str
    confidence_score: float
    confidence_level: str
    confidence_level_label: str
    conflicts_count: int
    conflicts: List[dict]
    conflict_summary: str
    confidence_summary: str
    confidence_adjustment_reason: str
    trade_bias: str
    trade_bias_label: str
    trade_bias_reason: str
    lifecycle_status: str
    trigger_price: float
    stop_loss: float
    target_price: float
    reasons: List[str]
    risk_reasons: List[str]
    market_status: str

    def to_dict(self) -> dict:
        return asdict(self)


def build_us_long_candidates(
    snapshots: Iterable[USMarketSnapshot],
    market_status: str,
) -> List[USLongCandidate]:
    items = list(snapshots)
    sector_strength = _sector_strength(items)
    candidates = [_score_snapshot(item, market_status, sector_strength) for item in items]
    return sorted(
        candidates,
        key=lambda item: (
            _grade_order(item.grade),
            _entry_order(item.entry_status),
            -item.bullish_score,
            item.risk_score,
            item.symbol,
        ),
    )


def _score_snapshot(
    item: USMarketSnapshot,
    market_status: str,
    sector_strength: dict[str, int],
) -> USLongCandidate:
    bullish = 0.0
    risk = 0.0
    reasons: List[str] = []
    risk_reasons: List[str] = []
    vwap_distance_pct = ((item.latest_price - item.vwap) / item.vwap * 100) if item.vwap else 0.0
    near_vwap = abs(vwap_distance_pct) <= 0.6

    if item.above_vwap:
        bullish += 20
        reasons.append("站上均價線 VWAP")
    else:
        risk += 20
        risk_reasons.append("跌破 VWAP")

    if item.volume_ratio >= 1.5:
        bullish += 15
        reasons.append(f"量比 {item.volume_ratio:.2f}x，短線資金明顯放大")
    elif item.volume_ratio >= 1.2:
        bullish += 10
        reasons.append(f"量比 {item.volume_ratio:.2f}x，量能放大")
    elif item.volume_ratio < 1.0:
        risk += 15
        risk_reasons.append("量能不足")

    if item.break_previous_high:
        bullish += 15
        reasons.append("突破昨日高點")
    if item.break_opening_range_high:
        bullish += 15
        reasons.append("突破開盤區間高點")
    if item.break_premarket_high:
        bullish += 10
        reasons.append("突破盤前高點")
    if item.latest_price >= item.high * 0.995:
        bullish += 10
        reasons.append("盤中高點墊高")
    if market_status == "bullish":
        bullish += 10
        reasons.append("QQQ / SPY 偏多")
    elif market_status == "bearish":
        risk += 20
        risk_reasons.append("QQQ / SPY 急跌")
    if sector_strength.get(item.sector_en, 0) >= 2 and item.sector_en != "ETF":
        bullish += 10
        reasons.append("同族群科技股同步轉強")

    if vwap_distance_pct > 3:
        risk += 15
        risk_reasons.append("距離 VWAP 超過 3%，追價風險升高")
    if item.change_pct > 8:
        risk += 25
        risk_reasons.append("漲幅超過 8%，過熱")
    elif item.change_pct > 5:
        risk += 15
        risk_reasons.append("漲幅超過 5%，需等回測")
    if item.change_pct > 3 and not item.above_vwap:
        risk += 25
        risk_reasons.append("盤前或早盤大漲後跌破 VWAP")

    grade = _grade(
        bullish,
        risk,
        item.above_vwap,
        near_vwap,
        item.volume_ratio,
        market_status,
    )
    original_entry_status = _entry_status(
        grade,
        bullish,
        risk,
        item.above_vwap,
        near_vwap,
        item.volume_ratio,
        item.break_previous_high or item.break_premarket_high or item.break_opening_range_high,
        vwap_distance_pct,
        market_status,
    )
    confidence = evaluate_signal(
        latest_price=item.latest_price,
        volume=item.volume,
        vwap=item.vwap,
        above_vwap=item.above_vwap,
        volume_ratio=item.volume_ratio,
        break_prev_high=item.break_previous_high,
        break_orb=item.break_opening_range_high,
        higher_high=item.latest_price >= item.high * 0.995,
        higher_low=item.latest_price > item.low,
        distance_to_vwap_pct=vwap_distance_pct,
        risk_score=risk,
        bullish_score=bullish,
        entry_status=original_entry_status,
        market_status=market_status,
        sector_strength="strong" if sector_strength.get(item.sector_en, 0) >= 2 else "",
        failed_breakout=(item.break_previous_high and item.latest_price < item.previous_high),
        data_status="ok" if item.latest_price and item.volume and item.vwap else "partial",
    )
    entry_status = confidence.adjusted_entry_status
    trigger_price = _trigger_price(item)
    risk_per_share = max(trigger_price - min(item.vwap or trigger_price, item.low), item.latest_price * 0.006, 0.01)
    stop_loss = max(0.01, trigger_price - risk_per_share)
    target_price = trigger_price + risk_per_share * 1.5
    trade_bias = evaluate_trade_bias(
        entry_status=entry_status,
        grade=grade,
        bullish_score=bullish,
        risk_score=risk,
        confidence_score=confidence.confidence_score,
        above_vwap=item.above_vwap,
        last_price=item.latest_price,
        vwap=item.vwap,
        change_pct=item.change_pct,
        volume_ratio=item.volume_ratio,
        market_status=market_status,
        break_prev_high=item.break_previous_high,
        break_orb=item.break_opening_range_high,
        risk_reasons=risk_reasons,
    )
    return USLongCandidate(
        symbol=item.symbol,
        name_en=item.name_en,
        name_zh=item.name_zh,
        short_name_zh=item.short_name_zh,
        sector_en=item.sector_en,
        sector_zh=item.sector_zh,
        industry_en=item.industry_en,
        industry_zh=item.industry_zh,
        description_zh=item.description_zh,
        latest_price=item.latest_price,
        previous_close=item.previous_close,
        open=item.open,
        high=item.high,
        low=item.low,
        volume=item.volume,
        change_pct=item.change_pct,
        volume_ratio=item.volume_ratio,
        vwap=item.vwap,
        above_vwap=item.above_vwap,
        premarket_high=item.premarket_high,
        break_premarket_high=item.break_premarket_high,
        break_previous_high=item.break_previous_high,
        break_opening_range_high=item.break_opening_range_high,
        opening_range_high=item.opening_range_high,
        bullish_score=round(bullish, 2),
        risk_score=round(risk, 2),
        grade=grade,
        entry_status=entry_status,
        original_entry_status=confidence.original_entry_status,
        adjusted_entry_status=confidence.adjusted_entry_status,
        confidence_score=confidence.confidence_score,
        confidence_level=confidence.confidence_level,
        confidence_level_label=confidence.confidence_level_label,
        conflicts_count=confidence.conflicts_count,
        conflicts=confidence.conflicts,
        conflict_summary=confidence.conflict_summary,
        confidence_summary=confidence.confidence_summary,
        confidence_adjustment_reason=confidence.confidence_adjustment_reason,
        trade_bias=trade_bias.bias,
        trade_bias_label=trade_bias.label,
        trade_bias_reason=trade_bias.reason,
        lifecycle_status="observed",
        trigger_price=round(trigger_price, 2),
        stop_loss=round(stop_loss, 2),
        target_price=round(target_price, 2),
        reasons=reasons or ["多方條件尚未明確"],
        risk_reasons=risk_reasons,
        market_status=market_status,
    )


def _grade(
    bullish: float,
    risk: float,
    above_vwap: bool,
    near_vwap: bool,
    volume_ratio: float,
    market_status: str,
) -> str:
    if risk > 70 or not above_vwap or volume_ratio < 0.8 or market_status == "bearish":
        return "D"
    if bullish >= 80 and risk <= 40 and above_vwap and volume_ratio >= 1.2 and market_status != "bearish":
        return "A"
    if bullish >= 65 and risk <= 55 and (above_vwap or near_vwap) and volume_ratio >= 1.0:
        return "B"
    if bullish >= 55:
        return "C"
    return "D"


def _entry_status(
    grade: str,
    bullish: float,
    risk: float,
    above_vwap: bool,
    near_vwap: bool,
    volume_ratio: float,
    has_breakout: bool,
    vwap_distance_pct: float,
    market_status: str,
) -> str:
    if market_status == "bearish" or risk > 70:
        return "avoid"
    if risk > 55:
        return "high_risk"
    if grade == "A" and above_vwap and volume_ratio >= 1.2:
        return "executable"
    if grade in {"B", "C"} and above_vwap and volume_ratio >= 0.9 and risk <= 55 and bullish >= 55 and vwap_distance_pct <= 2.5:
        return "practice_long"
    if bullish >= 55 and above_vwap and vwap_distance_pct > 3:
        return "wait_pullback"
    if bullish >= 55 and volume_ratio < 1.0:
        return "wait_volume"
    if bullish >= 55 and not above_vwap:
        return "wait_vwap"
    if bullish >= 55 and not has_breakout:
        return "wait_breakout"
    if grade in {"B", "C"} and near_vwap:
        return "wait_pullback"
    if not above_vwap:
        return "avoid"
    return "wait_breakout"


def _trigger_price(item: USMarketSnapshot) -> float:
    levels = [item.latest_price, item.previous_high or 0]
    if item.premarket_high is not None:
        levels.append(item.premarket_high)
    if item.opening_range_high is not None:
        levels.append(item.opening_range_high)
    return max(levels)


def _sector_strength(items: List[USMarketSnapshot]) -> dict[str, int]:
    result: dict[str, int] = {}
    for item in items:
        if item.above_vwap and item.change_pct > 0:
            result[item.sector_en] = result.get(item.sector_en, 0) + 1
    return result


def _grade_order(value: str) -> int:
    return {"A": 0, "B": 1, "C": 2, "D": 3}.get(value, 9)


def _entry_order(value: str) -> int:
    return {
        "executable": 0,
        "practice_long": 1,
        "wait_volume": 2,
        "wait_vwap": 3,
        "wait_breakout": 4,
        "wait_pullback": 5,
        "high_risk": 6,
        "avoid": 7,
    }.get(value, 9)
