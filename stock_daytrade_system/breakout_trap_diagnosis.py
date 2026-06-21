from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Optional

from stock_daytrade_system.data import Bar


BREAKOUT_TRAP_DIAGNOSIS_VERSION = "breakout_trap_diagnosis_v1_2026-06-21"


@dataclass(frozen=True)
class BreakoutTrapDiagnosis:
    version: str
    status: str
    status_label: str
    risk_level: str
    summary: str
    evidence: list[str]
    warnings: list[str]
    next_step: str
    invalidation: str
    does_not_change_model: bool = True

    def to_dict(self) -> dict:
        return asdict(self)


def build_breakout_trap_diagnosis(
    *,
    candidate: Optional[Any],
    intraday_bars: Optional[list[Bar]] = None,
    entry_confirmation: Optional[dict] = None,
    data_health: Optional[dict] = None,
    market_mode: str = "intraday",
    intraday: bool = True,
) -> BreakoutTrapDiagnosis:
    candidate = candidate or {}
    bars = list(intraday_bars or [])
    confirmation = entry_confirmation or {}
    health = data_health or {}

    price = _num(_get(candidate, "last_price") or _get(candidate, "latest_price") or _get(candidate, "current_price"))
    vwap = _num(_get(candidate, "vwap"))
    prev_high = _num(_get(candidate, "previous_high"))
    trigger = _num(_get(candidate, "trigger_price"))
    volume_ratio = _num(_get(candidate, "volume_ratio"))
    risk_score = _num(_get(candidate, "risk_score"), default=0.0) or 0.0
    above_vwap = _above_vwap(candidate, price, vwap)
    breakout_level = _first_number(trigger, prev_high)
    broke_level = bool(_get(candidate, "break_prev_high")) or bool(price is not None and breakout_level is not None and price >= breakout_level)
    reclaimed_vwap = _reclaimed_vwap(bars, vwap, price)
    failed_breakout = _failed_breakout(candidate, price=price, prev_high=prev_high, trigger=trigger, bars=bars)
    distance_to_vwap = _distance_pct(price, vwap)
    orderbook_status = str(confirmation.get("orderbook_status") or "")
    large_trade_status = str(confirmation.get("large_trade_status") or "")
    price_tick_trend = str(confirmation.get("price_tick_trend") or "")
    bid_trend = str(confirmation.get("bid_volume_trend") or "")
    ask_trend = str(confirmation.get("ask_volume_trend") or "")
    upper_shadow = _num(_get(candidate, "upper_shadow_pct"), default=0.0) or 0.0
    entry_status = str(_get(candidate, "entry_status") or "")
    data_live = bool(health.get("is_live") or health.get("can_use_for_intraday_signal"))
    evidence: list[str] = []
    warnings: list[str] = []

    if market_mode != "intraday" or not intraday:
        return _payload(
            "review_only",
            "復盤觀察",
            "medium",
            "目前不是盤中模式，僅能做上一交易日復盤，不判斷即時真假突破。",
            ["非盤中模式"],
            ["不可作為即時進場依據"],
            "等待下一個台股正常盤中，再觀察 VWAP、突破後守穩、五檔與逐筆成交。",
            "資料轉為盤中 live 前，不顯示即時進場判斷。",
        )
    if price is None or vwap is None:
        return _payload(
            "data_insufficient",
            "資料不足",
            "high",
            "缺最新價或 VWAP，不能判斷真突破、假突破或假跌破。",
            [],
            ["資料不足"],
            "等待最新價、VWAP 與盤中 K 線補齊後再判斷。",
            "缺必要行情資料時，所有真假突破判斷失效。",
        )
    if not data_live:
        warnings.append("資料不是 live，僅供觀察。")

    if above_vwap:
        evidence.append("股價站上 VWAP")
    else:
        warnings.append("股價尚未站上 VWAP")
    if volume_ratio is not None:
        if volume_ratio >= 1:
            evidence.append(f"量比 {volume_ratio:.2f}x，量能有確認")
        elif volume_ratio >= 0.8:
            warnings.append(f"量比 {volume_ratio:.2f}x，接近但尚未完全確認")
        else:
            warnings.append(f"量比 {volume_ratio:.2f}x，量能不足")
    if broke_level:
        evidence.append("已突破昨日高點或觸發價")
    if orderbook_status == "supportive":
        evidence.append("五檔買盤偏強")
    elif orderbook_status == "sell_pressure":
        warnings.append("五檔賣壓偏重")
    if large_trade_status in {"buy_sweep", "large_buy", "inflow"}:
        evidence.append("出現大單敲進跡象")
    elif large_trade_status in {"sell_sweep", "large_sell", "outflow"}:
        warnings.append("疑似大單敲出")
    if price_tick_trend in {"rising", "stable"}:
        evidence.append("最新價未轉弱")
    elif price_tick_trend == "weak":
        warnings.append("最新價轉弱")
    if bid_trend == "deteriorating":
        warnings.append("委買量轉弱")
    if ask_trend == "deteriorating":
        warnings.append("委賣量增加")

    if failed_breakout:
        return _payload(
            "false_breakout_risk",
            "假突破風險",
            "high",
            "價格曾出現突破條件，但目前沒有守住突破價，需防假突破回落。",
            evidence,
            warnings + ["突破後跌回關鍵價下方"],
            _breakout_next_step(breakout_level),
            "重新跌回突破價下方、五檔賣壓轉強或大單敲出時，假突破風險升高。",
        )
    if not above_vwap:
        if price_tick_trend == "weak" or orderbook_status == "sell_pressure" or large_trade_status in {"sell_sweep", "large_sell", "outflow"}:
            return _payload(
                "breakdown_weak",
                "跌破轉弱",
                "high",
                "目前跌破 VWAP 且盤中確認偏弱，較不像洗盤，先視為轉弱處理。",
                evidence,
                warnings,
                f"等待重新站回 VWAP {vwap:g} 並維持，再重新評估。",
                "無法站回 VWAP、最新價續弱或委賣增加時，維持避開。",
            )
        return _payload(
            "washout_watch",
            "洗盤觀察",
            "medium",
            "目前跌破或尚未站回 VWAP，但尚未看到明確賣壓失控，先列為洗盤觀察，不提前追。",
            evidence,
            warnings,
            f"等待站回 VWAP {vwap:g} 並維持，最好伴隨量比回到 1.0x 附近。",
            "跌破盤中支撐、五檔賣壓轉強或大單敲出時，洗盤觀察失效。",
        )
    if reclaimed_vwap:
        return _payload(
            "fake_breakdown_reclaim",
            "假跌破後站回",
            "medium",
            "盤中曾跌破 VWAP 但已重新站回，可能是洗盤後收回；仍需確認量能與盤口是否支持。",
            evidence + ["盤中低點曾跌破 VWAP 後收回"],
            warnings,
            "觀察是否維持 VWAP 上方、最新價墊高，並等待大單敲進或委買量補強。",
            "再次跌破 VWAP 且無法快速站回時，假跌破判斷失效。",
        )
    if entry_status == "high_risk" or risk_score > 65 or (distance_to_vwap is not None and distance_to_vwap > 3) or upper_shadow >= 2:
        return _payload(
            "bull_trap_risk",
            "誘多風險",
            "high",
            "股價方向偏多，但追價風險高、上影線或風險分數偏高，需防拉高誘多後回落。",
            evidence,
            warnings + _risk_warnings(risk_score, distance_to_vwap, upper_shadow),
            "等待拉回 VWAP 附近不破、風險分數下降，或重新突破後仍站穩再評估。",
            "跌回 VWAP、五檔賣壓升高或大單敲出時，不追。",
        )
    if broke_level and above_vwap and (volume_ratio or 0) >= 1 and price_tick_trend not in {"weak"} and orderbook_status != "sell_pressure":
        return _payload(
            "true_breakout",
            "真突破",
            "low",
            "價格站上 VWAP、突破關鍵價且量能基本確認，屬於較健康的真突破結構，但仍需守停損。",
            evidence,
            warnings,
            "觀察突破後是否守住關鍵價與 VWAP，若回測不破再依部位風控評估。",
            "跌回突破價或 VWAP、量能退潮、五檔賣壓轉強時，真突破失效。",
        )
    return _payload(
        "washout_watch",
        "洗盤觀察",
        "medium",
        "股價仍在 VWAP 上方，但突破與盤口確認尚未完整，先觀察是否洗盤後續強。",
        evidence,
        warnings,
        "等待突破價站穩、量比維持 1.0x 附近，並觀察委買量與大單是否支持。",
        "跌破 VWAP 或量能退潮時，洗盤觀察失效。",
    )


def _payload(status, label, risk_level, summary, evidence, warnings, next_step, invalidation):
    return BreakoutTrapDiagnosis(
        version=BREAKOUT_TRAP_DIAGNOSIS_VERSION,
        status=status,
        status_label=label,
        risk_level=risk_level,
        summary=summary,
        evidence=_dedupe(evidence),
        warnings=_dedupe(warnings),
        next_step=next_step,
        invalidation=invalidation,
    )


def _failed_breakout(candidate: Any, *, price: Optional[float], prev_high: Optional[float], trigger: Optional[float], bars: list[Bar]) -> bool:
    level = _first_number(trigger, prev_high)
    if price is None or level is None:
        return False
    recent_high = max((bar.high for bar in bars[-6:]), default=None)
    if recent_high is not None and recent_high >= level and price < level:
        return True
    return bool(_get(candidate, "break_prev_high")) and price < level


def _reclaimed_vwap(bars: list[Bar], vwap: Optional[float], price: Optional[float]) -> bool:
    if vwap is None or price is None or price < vwap or not bars:
        return False
    recent = bars[-8:]
    return any(bar.low < vwap for bar in recent) and recent[-1].close >= vwap


def _breakout_next_step(level: Optional[float]) -> str:
    if level:
        return f"等待重新站回突破價 {level:g} 並維持，且量能與五檔買盤同步支持。"
    return "等待重新站回突破價並維持，且量能與五檔買盤同步支持。"


def _risk_warnings(risk_score: float, distance_to_vwap: Optional[float], upper_shadow: float) -> list[str]:
    rows = []
    if risk_score > 65:
        rows.append(f"風險分數 {risk_score:.0f} 偏高")
    if distance_to_vwap is not None and distance_to_vwap > 3:
        rows.append(f"距離 VWAP {distance_to_vwap:.2f}% 偏遠")
    if upper_shadow >= 2:
        rows.append("上影線偏長")
    return rows


def _above_vwap(candidate: Any, price: Optional[float], vwap: Optional[float]) -> bool:
    value = _get(candidate, "above_vwap")
    if value is not None:
        return bool(value)
    return bool(price is not None and vwap is not None and price >= vwap)


def _distance_pct(price: Optional[float], level: Optional[float]) -> Optional[float]:
    if price is None or level is None or level <= 0:
        return None
    return (price - level) / level * 100


def _first_number(*values) -> Optional[float]:
    for value in values:
        parsed = _num(value)
        if parsed is not None:
            return parsed
    return None


def _get(item: Any, key: str, default=None):
    if isinstance(item, dict):
        return item.get(key, default)
    return getattr(item, key, default)


def _num(value, default=None) -> Optional[float]:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _dedupe(rows: list[str]) -> list[str]:
    seen = set()
    output = []
    for row in rows:
        text = str(row or "").strip()
        if text and text not in seen:
            seen.add(text)
            output.append(text)
    return output
