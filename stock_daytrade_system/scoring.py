from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

from stock_daytrade_system.config import RiskConfig, WatchSymbol
from stock_daytrade_system.data import Bar
from stock_daytrade_system.indicators import atr, average_volume, closes, latest_return, pct_change, sma


@dataclass(frozen=True)
class MarketBias:
    score: float
    direction: str
    notes: List[str]


@dataclass(frozen=True)
class CandidateScore:
    symbol: str
    name: str
    sector: str
    direction: str
    score: float
    close: float
    day_change_pct: float
    avg_volume: float
    atr: float
    previous_high: float
    previous_low: float
    trigger_price: float
    stop_loss: float
    target_price: float
    risk_per_share: float
    suggested_shares: int
    reasons: List[str]


def score_market_bias(market_data: Dict[str, List[Bar]], benchmark: str, taiwan_futures: str) -> MarketBias:
    score = 0.0
    notes: List[str] = []

    weighted_symbols = {
        "^GSPC": 0.8,
        "^IXIC": 1.0,
        "^DJI": 0.5,
        "^SOX": 1.2,
        "ES=F": 0.8,
        "NQ=F": 1.0,
        benchmark: 1.2,
        taiwan_futures: 1.5,
    }

    for symbol, weight in weighted_symbols.items():
        bars = market_data.get(symbol, [])
        if len(bars) < 21:
            continue
        one_day = latest_return(bars, 1)
        five_day = latest_return(bars, 5)
        close_values = closes(bars)
        ma20 = sma(close_values, 20)
        trend_bonus = 1.0 if ma20 is not None and close_values[-1] > ma20 else -1.0
        contribution = weight * (one_day * 0.7 + five_day * 0.2 + trend_bonus * 0.4)
        score += contribution
        notes.append(f"{symbol}: 1日 {one_day:+.2f}%, 5日 {five_day:+.2f}%")

    if score >= 2:
        direction = "偏多"
    elif score <= -2:
        direction = "偏空"
    else:
        direction = "中性"

    return MarketBias(score=round(score, 2), direction=direction, notes=notes)


def score_symbol(
    item: WatchSymbol,
    bars: List[Bar],
    benchmark_bars: List[Bar],
    market_bias: MarketBias,
    risk: RiskConfig,
) -> Optional[CandidateScore]:
    if len(bars) < 60 or len(benchmark_bars) < 21:
        return None

    close_values = closes(bars)
    last = bars[-1]
    previous = bars[-2]
    avg_vol = average_volume(bars, 20)
    if last.close < risk.min_price or avg_vol < risk.min_avg_volume:
        return None

    ma5 = sma(close_values, 5)
    ma20 = sma(close_values, 20)
    ma60 = sma(close_values, 60)
    if ma5 is None or ma20 is None or ma60 is None:
        return None

    one_day = latest_return(bars, 1)
    five_day = latest_return(bars, 5)
    benchmark_five_day = latest_return(benchmark_bars, 5)
    relative_strength = five_day - benchmark_five_day
    volume_ratio = last.volume / avg_vol if avg_vol else 0.0
    stock_atr = atr(bars, 14)
    atr_pct = stock_atr / last.close * 100 if last.close else 0.0

    long_score = 0.0
    short_score = 0.0
    reasons_long: List[str] = []
    reasons_short: List[str] = []

    if last.close > ma5 > ma20:
        long_score += 2.0
        reasons_long.append("價格站上 5/20 日均線")
    if last.close > ma60:
        long_score += 1.0
        reasons_long.append("價格位於 60 日均線之上")
    if relative_strength > 1:
        long_score += 1.5
        reasons_long.append(f"5日相對大盤強 {relative_strength:+.2f}%")
    if one_day >= 3:
        long_score += 1.5
        reasons_long.append(f"當日強勢上漲 {one_day:+.2f}%")
    if last.close > previous.high:
        long_score += 1.0
        reasons_long.append("價格突破前一日高點")
    if volume_ratio > 1.2:
        long_score += 1.0
        reasons_long.append(f"量能放大 {volume_ratio:.2f}x")
    if market_bias.direction == "偏多":
        long_score += 1.0
        reasons_long.append("市場背景偏多")

    if last.close < ma5 < ma20:
        short_score += 2.0
        reasons_short.append("價格跌破 5/20 日均線")
    if last.close < ma60:
        short_score += 1.0
        reasons_short.append("價格位於 60 日均線之下")
    if relative_strength < -1:
        short_score += 1.5
        reasons_short.append(f"5日相對大盤弱 {relative_strength:+.2f}%")
    if one_day <= -3:
        short_score += 1.5
        reasons_short.append(f"當日弱勢下跌 {one_day:+.2f}%")
    if last.close < previous.low:
        short_score += 1.0
        reasons_short.append("價格跌破前一日低點")
    if volume_ratio > 1.2:
        short_score += 1.0
        reasons_short.append(f"量能放大 {volume_ratio:.2f}x")
    if market_bias.direction == "偏空":
        short_score += 1.0
        reasons_short.append("市場背景偏空")

    if 1.2 <= atr_pct <= 6:
        long_score += 0.75
        short_score += 0.75
    elif atr_pct < 0.8:
        reasons_long.append("波動偏低，較不利當沖")
        reasons_short.append("波動偏低，較不利當沖")

    if long_score >= short_score:
        direction = "做多觀察"
        score = long_score
        reasons = reasons_long
        trigger_price = previous.high
        stop_loss = min(previous.low, trigger_price - stock_atr * 0.8)
        risk_per_share = max(trigger_price - stop_loss, 0.01)
        target_price = trigger_price + risk_per_share * 1.5
    else:
        direction = "做空觀察"
        score = short_score
        reasons = reasons_short
        trigger_price = previous.low
        stop_loss = max(previous.high, trigger_price + stock_atr * 0.8)
        risk_per_share = max(stop_loss - trigger_price, 0.01)
        target_price = trigger_price - risk_per_share * 1.5

    if score < 3:
        return None

    suggested_shares = _position_size(
        max_loss_per_trade=risk.max_loss_per_trade,
        risk_per_share=risk_per_share,
        round_lot_size=risk.round_lot_size,
    )

    return CandidateScore(
        symbol=item.symbol,
        name=item.name,
        sector=item.sector,
        direction=direction,
        score=round(score, 2),
        close=round(last.close, 2),
        day_change_pct=round(pct_change(last.close, previous.close), 2),
        avg_volume=round(avg_vol, 0),
        atr=round(stock_atr, 2),
        previous_high=round(previous.high, 2),
        previous_low=round(previous.low, 2),
        trigger_price=round(trigger_price, 2),
        stop_loss=round(stop_loss, 2),
        target_price=round(target_price, 2),
        risk_per_share=round(risk_per_share, 2),
        suggested_shares=suggested_shares,
        reasons=reasons,
    )


def _position_size(max_loss_per_trade: float, risk_per_share: float, round_lot_size: int) -> int:
    if max_loss_per_trade <= 0 or risk_per_share <= 0 or round_lot_size <= 0:
        return 0
    raw_shares = int(max_loss_per_trade // risk_per_share)
    return raw_shares // round_lot_size * round_lot_size
