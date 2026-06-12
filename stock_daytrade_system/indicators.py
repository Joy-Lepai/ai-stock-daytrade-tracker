from __future__ import annotations

from typing import Iterable, List, Optional

from stock_daytrade_system.data import Bar


def sma(values: List[float], window: int) -> Optional[float]:
    if len(values) < window:
        return None
    return sum(values[-window:]) / window


def pct_change(current: float, previous: float) -> float:
    if previous == 0:
        return 0.0
    return (current - previous) / previous * 100


def latest_return(bars: List[Bar], lookback: int = 1) -> float:
    if len(bars) <= lookback:
        return 0.0
    return pct_change(bars[-1].close, bars[-1 - lookback].close)


def average_volume(bars: List[Bar], window: int = 20) -> float:
    usable = bars[-window:]
    if not usable:
        return 0.0
    return sum(bar.volume for bar in usable) / len(usable)


def true_ranges(bars: List[Bar]) -> List[float]:
    ranges: List[float] = []
    previous_close: Optional[float] = None
    for bar in bars:
        if previous_close is None:
            ranges.append(bar.high - bar.low)
        else:
            ranges.append(
                max(
                    bar.high - bar.low,
                    abs(bar.high - previous_close),
                    abs(bar.low - previous_close),
                )
            )
        previous_close = bar.close
    return ranges


def atr(bars: List[Bar], window: int = 14) -> float:
    ranges = true_ranges(bars)
    if not ranges:
        return 0.0
    usable = ranges[-window:]
    return sum(usable) / len(usable)


def closes(bars: Iterable[Bar]) -> List[float]:
    return [bar.close for bar in bars]
