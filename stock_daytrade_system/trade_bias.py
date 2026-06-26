from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional


TRADE_BIAS_VERSION = "trade_bias_v1_long_short_watch_2026-06-17"


@dataclass(frozen=True)
class TradeBias:
    bias: str
    label: str
    reason: str


def evaluate_trade_bias(
    *,
    entry_status: str = "",
    grade: str = "",
    bullish_score: float = 0.0,
    risk_score: float = 0.0,
    confidence_score: Optional[float] = None,
    above_vwap: bool = False,
    last_price: Optional[float] = None,
    vwap: Optional[float] = None,
    change_pct: float = 0.0,
    volume_ratio: float = 0.0,
    market_status: str = "",
    break_prev_high: bool = False,
    break_orb: bool = False,
    risk_reasons: Optional[Iterable[str]] = None,
) -> TradeBias:
    confidence = float(confidence_score or 0.0)
    status = entry_status or ""
    reasons = list(risk_reasons or [])
    vwap_distance = _distance_to_vwap_pct(last_price, vwap)
    market_bearish = _is_bearish(market_status)
    market_bullish = _is_bullish(market_status)
    has_breakout = break_prev_high or break_orb

    if (
        status == "practice_long"
        and grade in {"B+", "B"}
        and above_vwap
        and volume_ratio >= 0.8
        and risk_score <= 55
        and confidence >= 55
    ):
        return TradeBias("long", "練習買多", "練習買多條件成立；尚未等同 A 級正式訊號，適合用虛擬交易累積樣本。")

    if (
        status == "executable"
        and grade in {"A", "B+", "B"}
        and above_vwap
        and volume_ratio >= 1.0
        and risk_score <= 55
        and confidence >= 60
    ):
        return TradeBias("long", "進場雷達通過", "站上 VWAP、量能達標且進場雷達通過。")

    if (
        grade == "A"
        and above_vwap
        and has_breakout
        and volume_ratio >= 1.0
        and risk_score <= 40
        and confidence >= 70
    ):
        return TradeBias("long", "進場雷達通過", "A 級多方結構完整，可列入進場雷達重點檢查。")

    if _short_conditions(
        above_vwap=above_vwap,
        vwap_distance=vwap_distance,
        change_pct=change_pct,
        volume_ratio=volume_ratio,
        market_bearish=market_bearish,
        risk_reasons=reasons,
    ):
        return TradeBias("short", "賣空", "跌破 VWAP 且下跌有量，短線偏空。")

    if status in {"high_risk", "avoid"}:
        return TradeBias("watch", "觀察", "風險或結構衝突偏高，先觀察不追價。")
    if not above_vwap:
        return TradeBias("watch", "觀察", "尚未站上 VWAP，等待結構轉強或轉弱確認。")
    if volume_ratio < 1.0:
        return TradeBias("watch", "觀察", "量能尚未確認，等待量比放大。")
    if market_bullish and bullish_score >= 55:
        return TradeBias("watch", "觀察", "多方條件尚未完整，先列入觀察。")
    return TradeBias("watch", "觀察", "目前沒有明確買多或賣空條件。")


def _short_conditions(
    *,
    above_vwap: bool,
    vwap_distance: Optional[float],
    change_pct: float,
    volume_ratio: float,
    market_bearish: bool,
    risk_reasons: list[str],
) -> bool:
    if above_vwap:
        return False
    below_vwap_enough = vwap_distance is not None and vwap_distance <= -0.5
    down_with_volume = change_pct <= -1.0 and volume_ratio >= 1.0
    weak_market_breakdown = market_bearish and change_pct <= -0.5 and volume_ratio >= 0.8
    explicit_vwap_break = any("跌破 VWAP" in reason or "跌破VWAP" in reason for reason in risk_reasons)
    return bool((below_vwap_enough or explicit_vwap_break) and (down_with_volume or weak_market_breakdown))


def _distance_to_vwap_pct(last_price: Optional[float], vwap: Optional[float]) -> Optional[float]:
    try:
        if last_price is None or vwap is None or float(vwap) <= 0:
            return None
        return (float(last_price) - float(vwap)) / float(vwap) * 100
    except (TypeError, ValueError):
        return None


def _is_bearish(value: str) -> bool:
    text = (value or "").lower()
    return "偏空" in text or "bearish" in text


def _is_bullish(value: str) -> bool:
    text = (value or "").lower()
    return "偏多" in text or "bullish" in text
