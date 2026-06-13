from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional

from stock_daytrade_system.config import WatchSymbol
from stock_daytrade_system.confidence_model import evaluate_signal
from stock_daytrade_system.data import Bar
from stock_daytrade_system.indicators import atr, average_volume, pct_change
from stock_daytrade_system.intraday import OpeningSignal
from stock_daytrade_system.market_context import MarketIndicator
from stock_daytrade_system.scoring import MarketBias
from stock_daytrade_system.sectors import SectorStrength


SCORING_MODEL_VERSION = "long_model_v2_b_plus_practice_2026-06-13"

MAJOR_CONFLICT_CODES = {
    "breakout_below_vwap",
    "high_risk_executable",
    "failed_breakout",
    "long_upper_shadow",
    "missing_data_signal",
}


@dataclass(frozen=True)
class LongCandidate:
    symbol: str
    name: str
    sector: str
    last_price: float
    change_pct: float
    volume: float
    turnover: float
    avg_volume_20: float
    daily_volume_ratio: float
    intraday_volume: float
    volume_ratio: float
    vwap: Optional[float]
    above_vwap: bool
    previous_high: float
    high_5d: float
    high_10d: float
    break_prev_high: bool
    break_5d_high: bool
    break_10d_high: bool
    upper_shadow_pct: float
    institutional_buy_million: Optional[float]
    margin_balance: Optional[float]
    short_balance: Optional[float]
    daytrade_ratio: Optional[float]
    sector_strength: float
    news_topics: List[str]
    market_state: str
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
    trigger_price: float
    stop_loss: float
    target_price: float
    opening_range_high: Optional[float]
    opening_range_low: Optional[float]
    reasons: List[str]
    risk_reasons: List[str]


@dataclass(frozen=True)
class SectorHeat:
    sector: str
    score: float
    candidates: int
    grade_a_count: int
    above_vwap_count: int
    breakout_count: int
    avg_volume_ratio: float


@dataclass(frozen=True)
class LongModelSummary:
    candidates: List[LongCandidate]
    alerts: List[str]
    sector_heat: List[SectorHeat]
    market_state: str
    market_notes: List[str]
    backtest: dict
    recommendation_checklist: dict
    b_plus_triggers: List[dict] = field(default_factory=list)
    debug_info: dict = field(default_factory=dict)
    paper_stats: dict = field(default_factory=dict)
    decision_center: dict = field(default_factory=dict)


def build_long_candidates(
    symbols: Iterable[WatchSymbol],
    daily_data: Dict[str, List[Bar]],
    intraday_data: Dict[str, List[Bar]],
    opening_signals: Iterable[OpeningSignal],
    sector_strengths: Iterable[SectorStrength],
    market_bias: MarketBias,
    institutional_rankings: Optional[dict] = None,
) -> List[LongCandidate]:
    opening_map = {item.symbol: item for item in opening_signals}
    sector_map = {item.sector: item for item in sector_strengths}
    ranking_map = institutional_rankings or {}
    candidates: List[LongCandidate] = []
    for symbol in symbols:
        bars = daily_data.get(symbol.symbol, [])
        if len(bars) < 11:
            continue
        item = _build_candidate(
            symbol,
            bars,
            intraday_data.get(symbol.symbol, []),
            opening_map.get(symbol.symbol),
            sector_map.get(symbol.sector),
            market_bias,
            ranking_map.get(symbol.symbol),
        )
        if item is not None:
            candidates.append(item)
    candidates.sort(key=lambda item: (_grade_order(item.grade), -item.bullish_score, item.risk_score, item.symbol))
    return candidates


def build_long_model_summary(
    candidates: List[LongCandidate],
    market_indicators: Iterable[MarketIndicator],
    market_bias: MarketBias,
    backtest: dict,
    recommendation_checklist: Optional[dict] = None,
    b_plus_triggers: Optional[List[dict]] = None,
    debug_info: Optional[dict] = None,
    paper_stats: Optional[dict] = None,
    decision_center: Optional[dict] = None,
) -> LongModelSummary:
    visible = [
        item for item in candidates
        if item.grade in {"A", "B+", "B", "C"} or item.entry_status in {"wait_volume", "wait_vwap", "high_risk"}
    ]
    alerts = _build_alerts(candidates)
    return LongModelSummary(
        candidates=visible[:30],
        alerts=alerts,
        sector_heat=_build_sector_heat(candidates),
        market_state=market_bias.direction,
        market_notes=[f"{item.name}: {item.status}（{item.change}）" for item in market_indicators][:8],
        backtest=backtest,
        recommendation_checklist=recommendation_checklist or {},
        b_plus_triggers=b_plus_triggers or [],
        debug_info=debug_info or {},
        paper_stats=paper_stats or {},
        decision_center=decision_center or {},
    )


def _build_candidate(
    symbol: WatchSymbol,
    bars: List[Bar],
    intraday_bars: List[Bar],
    opening: Optional[OpeningSignal],
    sector: Optional[SectorStrength],
    market_bias: MarketBias,
    ranking,
) -> Optional[LongCandidate]:
    last = bars[-1]
    previous = bars[-2]
    avg_vol = average_volume(bars, 20)
    if avg_vol <= 0:
        return None
    previous_high = previous.high
    high_5d = max(bar.high for bar in bars[-6:-1])
    high_10d = max(bar.high for bar in bars[-11:-1])
    latest_intraday = intraday_bars[-1] if intraday_bars else None
    last_price = round((opening.last_price if opening else latest_intraday.close if latest_intraday else last.close), 2)
    intraday_volume = sum(bar.volume for bar in intraday_bars) if intraday_bars else last.volume
    vwap = opening.vwap if opening else _vwap(intraday_bars)
    volume_ratio = opening.volume_ratio if opening else (intraday_volume / avg_vol if avg_vol else 0.0)
    daily_volume_ratio = last.volume / avg_vol if avg_vol else 0.0
    change_pct = pct_change(last.close, previous.close)
    turnover = last.close * last.volume
    sector_score = sector.score if sector else 0.0
    stock_atr = atr(bars, 14)
    trigger_price = max(previous_high, high_5d)
    stop_loss = min(previous.low, trigger_price - stock_atr * 0.8)
    risk_per_share = max(trigger_price - stop_loss, 0.01)
    target_price = trigger_price + risk_per_share * 1.5
    break_prev_high = last_price > previous_high
    break_5d_high = last_price > high_5d
    break_10d_high = last_price > high_10d
    above_vwap = bool(vwap and last_price > vwap)
    upper_shadow_pct = _upper_shadow_pct(last)
    vwap_distance_pct = _vwap_distance_pct(last_price, vwap)
    institutional_buy = ranking.total_buy_million if ranking else None

    bullish_score, reasons = _bullish_score(
        change_pct=change_pct,
        volume_ratio=volume_ratio,
        above_vwap=above_vwap,
        break_prev_high=break_prev_high,
        break_5d_high=break_5d_high,
        break_10d_high=break_10d_high,
        sector_score=sector_score,
        market_state=market_bias.direction,
        institutional_buy=institutional_buy,
    )
    risk_score, risk_reasons = _risk_score(
        change_pct=change_pct,
        volume_ratio=volume_ratio,
        last_price=last_price,
        vwap=vwap,
        risk_per_share=risk_per_share,
        atr_pct=(stock_atr / last_price * 100) if last_price else 0.0,
        upper_shadow_pct=upper_shadow_pct,
    )
    grade = _grade(
        bullish_score=bullish_score,
        risk_score=risk_score,
        above_vwap=above_vwap,
        market_state=market_bias.direction,
        break_prev_high=break_prev_high,
        previous_high=previous_high,
        last_price=last_price,
        volume_ratio=volume_ratio,
        change_pct=change_pct,
        vwap_distance_pct=vwap_distance_pct,
        upper_shadow_pct=upper_shadow_pct,
    )
    original_entry_status = _entry_status(
        grade=grade,
        bullish_score=bullish_score,
        risk_score=risk_score,
        above_vwap=above_vwap,
        volume_ratio=volume_ratio,
        vwap_distance_pct=vwap_distance_pct,
    )
    confidence = evaluate_signal(
        latest_price=last_price,
        volume=intraday_volume,
        vwap=vwap,
        above_vwap=above_vwap,
        volume_ratio=volume_ratio,
        break_prev_high=break_prev_high,
        break_5d_high=break_5d_high,
        higher_high=_higher_high(intraday_bars),
        higher_low=_higher_low(intraday_bars),
        distance_to_vwap_pct=vwap_distance_pct,
        risk_score=risk_score,
        bullish_score=bullish_score,
        entry_status=original_entry_status,
        market_status=market_bias.direction,
        sector_strength=sector_score,
        long_upper_shadow=upper_shadow_pct >= 1.2,
        failed_breakout=(break_prev_high and last_price < previous_high),
        data_status="ok" if latest_intraday or opening else "partial",
    )
    entry_status = confidence.adjusted_entry_status
    grade = _apply_confidence_to_grade(
        grade=grade,
        entry_status=entry_status,
        confidence_score=confidence.confidence_score,
        conflicts=confidence.conflicts,
    )
    if grade == "B+" and entry_status == "executable":
        entry_status = "wait_pullback"
    if grade == "B+":
        reasons = [
            *reasons,
            "B+練習觀察：條件接近，可列入虛擬交易練習觀察，尚未達到A級高信心標準",
        ]
    return LongCandidate(
        symbol=symbol.symbol,
        name=symbol.name,
        sector=symbol.sector,
        last_price=last_price,
        change_pct=round(change_pct, 2),
        volume=round(last.volume, 0),
        turnover=round(turnover, 0),
        avg_volume_20=round(avg_vol, 0),
        daily_volume_ratio=round(daily_volume_ratio, 2),
        intraday_volume=round(intraday_volume, 0),
        volume_ratio=round(volume_ratio, 2),
        vwap=round(vwap, 2) if vwap else None,
        above_vwap=above_vwap,
        previous_high=round(previous_high, 2),
        high_5d=round(high_5d, 2),
        high_10d=round(high_10d, 2),
        break_prev_high=break_prev_high,
        break_5d_high=break_5d_high,
        break_10d_high=break_10d_high,
        upper_shadow_pct=round(upper_shadow_pct, 2),
        institutional_buy_million=round(institutional_buy, 2) if institutional_buy is not None else None,
        margin_balance=None,
        short_balance=None,
        daytrade_ratio=None,
        sector_strength=round(sector_score, 2),
        news_topics=[],
        market_state=market_bias.direction,
        bullish_score=bullish_score,
        risk_score=risk_score,
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
        trigger_price=round(trigger_price, 2),
        stop_loss=round(stop_loss, 2),
        target_price=round(target_price, 2),
        opening_range_high=opening.opening_range_high if opening else None,
        opening_range_low=opening.opening_range_low if opening else None,
        reasons=reasons,
        risk_reasons=risk_reasons,
    )


def _bullish_score(
    change_pct: float,
    volume_ratio: float,
    above_vwap: bool,
    break_prev_high: bool,
    break_5d_high: bool,
    break_10d_high: bool,
    sector_score: float,
    market_state: str,
    institutional_buy: Optional[float],
) -> tuple[float, List[str]]:
    score = 0.0
    reasons: List[str] = []
    if above_vwap:
        score += 20
        reasons.append("站上VWAP")
    if volume_ratio >= 1.5:
        score += 15
        reasons.append(f"量比 {volume_ratio:.2f}x")
    elif volume_ratio >= 1.2:
        score += 10
        reasons.append(f"量比 {volume_ratio:.2f}x")
    elif volume_ratio >= 1.0:
        score += 5
        reasons.append(f"量比 {volume_ratio:.2f}x")
    if break_prev_high:
        score += 15
        reasons.append("突破昨日高點")
    if break_5d_high:
        score += 15
        reasons.append("突破5日高點")
    if break_10d_high:
        score += 10
        reasons.append("突破10日高點")
    if 1 <= change_pct <= 4:
        score += 10
        reasons.append(f"漲幅適中 {change_pct:+.2f}%")
    elif 0 < change_pct < 1:
        score += 5
        reasons.append(f"小漲 {change_pct:+.2f}%")
    if sector_score >= 2:
        score += 10
        reasons.append("族群強勢")
    elif sector_score > 0:
        score += 5
        reasons.append("族群偏強")
    if market_state == "偏多":
        score += 10
        reasons.append("大盤偏多")
    elif market_state == "中性":
        score += 5
        reasons.append("大盤中性")
    if institutional_buy and institutional_buy > 0:
        score += 5
        reasons.append("法人買超")
    return round(min(score, 100), 2), reasons


def _risk_score(
    change_pct: float,
    volume_ratio: float,
    last_price: float,
    vwap: Optional[float],
    risk_per_share: float,
    atr_pct: float,
    upper_shadow_pct: float,
) -> tuple[float, List[str]]:
    score = 0.0
    reasons: List[str] = []
    if change_pct > 7:
        score += 25
        reasons.append("漲幅超過7%，追價風險高")
    elif change_pct >= 5:
        score += 15
        reasons.append("漲幅5%以上")
    if vwap:
        distance = (last_price - vwap) / vwap * 100
        if distance > 3:
            score += 20
            reasons.append("距離VWAP超過3%")
        elif distance > 2:
            score += 10
            reasons.append("距離VWAP超過2%")
    if volume_ratio > 3:
        score += 15
        reasons.append("量比過高")
    if upper_shadow_pct >= 2:
        score += 20
        reasons.append("長上影，追價風險高")
    elif upper_shadow_pct >= 1.2:
        score += 10
        reasons.append("上影線偏長")
    if atr_pct > 6:
        score += 15
        reasons.append("波動過大")
    if risk_per_share / last_price * 100 > 4:
        score += 20
        reasons.append("停損距離過大")
    return round(min(score, 100), 2), reasons


def _grade(
    bullish_score: float,
    risk_score: float,
    above_vwap: bool,
    market_state: str,
    break_prev_high: bool,
    previous_high: float,
    last_price: float,
    volume_ratio: float,
    change_pct: float,
    vwap_distance_pct: Optional[float],
    upper_shadow_pct: float,
) -> str:
    near_vwap = vwap_distance_pct is not None and vwap_distance_pct >= -0.5
    near_vwap_for_practice = vwap_distance_pct is not None and abs(vwap_distance_pct) <= 0.5
    below_vwap = vwap_distance_pct is not None and vwap_distance_pct < -0.5
    near_breakout = break_prev_high or bool(previous_high and last_price >= previous_high * 0.995)
    has_upper_shadow = upper_shadow_pct >= 1.2
    is_overextended = change_pct >= 7

    if market_state == "偏空" or bullish_score < 55 or below_vwap or risk_score > 70:
        return "D"
    if (
        bullish_score >= 80
        and risk_score <= 40
        and above_vwap
        and break_prev_high
        and volume_ratio >= 1.0
        and not has_upper_shadow
        and not is_overextended
    ):
        return "A"
    if bullish_score >= 55 and (risk_score > 55 or has_upper_shadow or is_overextended):
        return "C"
    if (
        bullish_score >= 70
        and risk_score <= 55
        and volume_ratio >= 0.8
        and (above_vwap or near_vwap_for_practice)
        and near_breakout
    ):
        return "B+"
    if bullish_score >= 65 and risk_score <= 55 and (above_vwap or near_vwap) and break_prev_high and volume_ratio >= 0.8:
        return "B"
    return "D"


def _entry_status(
    grade: str,
    bullish_score: float,
    risk_score: float,
    above_vwap: bool,
    volume_ratio: float,
    vwap_distance_pct: Optional[float],
) -> str:
    if risk_score > 55 or grade == "C":
        return "high_risk"
    if not above_vwap:
        if vwap_distance_pct is not None and vwap_distance_pct < -0.5:
            return "avoid"
        if bullish_score >= 55:
            return "wait_vwap"
    if bullish_score >= 65 and volume_ratio < 1.0:
        return "wait_volume"
    if grade == "A":
        return "executable"
    if grade == "B+":
        return "wait_pullback"
    if grade == "B":
        return "wait_pullback"
    return "avoid"


def _apply_confidence_to_grade(
    grade: str,
    entry_status: str,
    confidence_score: float,
    conflicts: List[dict],
) -> str:
    if grade == "A" and (confidence_score < 70 or _has_major_conflict(conflicts)):
        return "B+"
    if grade == "B+":
        if confidence_score < 55:
            return "B"
        if entry_status == "high_risk":
            return "C"
        if entry_status == "avoid":
            return "D"
    return grade


def _has_major_conflict(conflicts: List[dict]) -> bool:
    return any(str(item.get("code") or "") in MAJOR_CONFLICT_CODES for item in conflicts)


def _build_alerts(candidates: List[LongCandidate]) -> List[str]:
    alerts: List[str] = []
    for item in candidates:
        if item.grade == "A":
            alerts.append(f"{item.name} {item.symbol}：A級強勢，{', '.join(item.reasons[:3])}")
        elif item.grade == "B+":
            alerts.append(f"{item.name} {item.symbol}：B+練習觀察，條件接近但仍待觸發確認")
        elif item.entry_status == "wait_volume":
            alerts.append(f"{item.name} {item.symbol}：多方結構不錯但量能不足，等待量比放大")
        elif item.entry_status == "high_risk":
            alerts.append(f"{item.name} {item.symbol}：多方強但風險偏高，避免追價")
        elif item.entry_status == "wait_vwap":
            alerts.append(f"{item.name} {item.symbol}：分數不差但未站上VWAP")
    return alerts[:12]


def _build_sector_heat(candidates: List[LongCandidate]) -> List[SectorHeat]:
    grouped: Dict[str, List[LongCandidate]] = {}
    for item in candidates:
        grouped.setdefault(item.sector, []).append(item)
    heat: List[SectorHeat] = []
    for sector, items in grouped.items():
        breakout_count = sum(1 for item in items if item.break_prev_high or item.break_5d_high)
        above_vwap_count = sum(1 for item in items if item.above_vwap)
        grade_a_count = sum(1 for item in items if item.grade == "A")
        avg_volume_ratio = sum(item.volume_ratio for item in items) / len(items)
        score = grade_a_count * 3 + above_vwap_count * 1.5 + breakout_count + avg_volume_ratio
        heat.append(
            SectorHeat(
                sector=sector,
                score=round(score, 2),
                candidates=len(items),
                grade_a_count=grade_a_count,
                above_vwap_count=above_vwap_count,
                breakout_count=breakout_count,
                avg_volume_ratio=round(avg_volume_ratio, 2),
            )
        )
    heat.sort(key=lambda item: item.score, reverse=True)
    return heat[:12]


def _vwap(bars: List[Bar]) -> Optional[float]:
    total_value = 0.0
    total_volume = 0.0
    for bar in bars:
        typical = (bar.high + bar.low + bar.close) / 3
        total_value += typical * bar.volume
        total_volume += bar.volume
    return total_value / total_volume if total_volume else None


def _vwap_distance_pct(last_price: float, vwap: Optional[float]) -> Optional[float]:
    if not vwap:
        return None
    return (last_price - vwap) / vwap * 100


def _upper_shadow_pct(bar: Bar) -> float:
    if bar.close <= 0:
        return 0.0
    upper_shadow = max(bar.high - max(bar.open, bar.close), 0.0)
    return upper_shadow / bar.close * 100


def _higher_high(bars: List[Bar]) -> bool:
    if len(bars) < 3:
        return False
    recent = bars[-3:]
    return recent[0].high <= recent[1].high <= recent[2].high


def _higher_low(bars: List[Bar]) -> bool:
    if len(bars) < 3:
        return False
    recent = bars[-3:]
    return recent[0].low <= recent[1].low <= recent[2].low


def _grade_order(value: str) -> int:
    return {"A": 0, "B+": 1, "B": 2, "C": 3, "D": 4}.get(value, 9)
