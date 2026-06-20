from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Optional

from stock_daytrade_system.data import Bar


PRECISION_CONTEXT_VERSION = "precision_context_v1_intraday_readiness_2026-06-20"


@dataclass(frozen=True)
class PrecisionContext:
    version: str
    precision_level: str
    precision_label: str
    readiness_score: float
    can_use_for_precise_daytrade: bool
    tick_data_status: str
    orderbook_status: str
    news_status: str
    intraday_k_status: str
    vwap_quality: str
    volume_profile_status: str
    sector_sync_status: str
    institutional_continuity_status: str
    recent_volume_ratio: Optional[float]
    volume_acceleration_ratio: Optional[float]
    price_up_volume_up: bool
    price_up_volume_down: bool
    vwap_hold_ok: bool
    trend_structure_ok: bool
    missing_data: list[str]
    available_data: list[str]
    summary: str
    next_data_to_add: list[str]

    def to_dict(self) -> dict:
        return asdict(self)


def build_precision_context(
    *,
    candidate: Optional[dict],
    intraday_bars: list[Bar],
    data_health: dict,
) -> PrecisionContext:
    candidate = candidate or {}
    trend = candidate.get("trend_diagnosis") or {}
    timeframe = candidate.get("timeframe_diagnostics") or {}
    intraday_window = timeframe.get("intraday_window") or {}
    institutional = candidate.get("institutional_context") or {}
    sector = candidate.get("sector_context") or {}
    has_intraday = len(intraday_bars) >= 10
    has_vwap = candidate.get("vwap") is not None
    has_volume_ratio = candidate.get("volume_ratio") is not None
    data_live = bool(data_health.get("is_live")) and bool(data_health.get("can_use_for_intraday_signal"))
    vwap_hold_ok = bool(intraday_window.get("vwap_stay_ok") or trend.get("reasons") and "股價站上 VWAP" in " ".join(trend.get("reasons") or []))
    trend_structure_ok = bool(intraday_window.get("higher_high") and intraday_window.get("higher_low"))
    volume_acceleration = _volume_acceleration(intraday_bars)
    price_up_volume_up, price_up_volume_down = _price_volume_relation(intraday_bars)
    institutional_status = str(institutional.get("institutional_data_status") or "missing")
    sector_status = str(sector.get("sector_status") or "unknown")

    available = []
    missing = []
    if has_intraday:
        available.append("1分 / 5分 K 線")
    else:
        missing.append("足夠盤中 K 線")
    if has_vwap:
        available.append("VWAP")
    else:
        missing.append("VWAP")
    if has_volume_ratio:
        available.append("量比")
    else:
        missing.append("量比")
    if institutional_status in {"ok", "partial"}:
        available.append("三大法人背景")
    else:
        missing.append("三大法人背景")
    if sector_status not in {"unknown", ""}:
        available.append("族群同步背景")
    else:
        missing.append("族群同步背景")

    orderbook_status = str(
        data_health.get("fugle_quote_five_level_status")
        or data_health.get("twse_mis_five_level_status")
        or "missing"
    )
    fugle_status = str(data_health.get("fugle_status") or "disabled")
    fugle_count = int(data_health.get("fugle_trades_count") or 0)
    fugle_candles_count = int(data_health.get("fugle_candles_count") or 0)
    has_fugle_trades = fugle_status == "ok" and fugle_count > 0
    has_fugle_candles = fugle_candles_count > 0
    has_public_orderbook = orderbook_status in {
        "available",
        "limit_up_bid_only",
        "limit_down_ask_only",
        "bid_only",
        "ask_only",
    }
    if has_public_orderbook:
        available.append("公開五檔委買委賣")
    else:
        missing.append("五檔委買委賣")
    if has_fugle_trades:
        available.append("Fugle 逐筆成交")
    else:
        missing.append("逐筆成交 Tick")
    if has_fugle_candles:
        available.append("Fugle 1分K")
    missing.append("即時新聞題材")
    score = 0.0
    score += 18 if data_live else 0
    score += 16 if has_intraday else 0
    score += 16 if has_vwap else 0
    score += 14 if has_volume_ratio else 0
    score += 8 if vwap_hold_ok else 0
    score += 8 if trend_structure_ok else 0
    score += 8 if price_up_volume_up else 0
    score += 6 if institutional_status in {"ok", "partial"} else 0
    score += 6 if sector_status == "strong" else 0
    score += 4 if has_public_orderbook else 0
    score += 8 if has_fugle_trades else 0
    score = min(score, 100.0)
    precision_level, precision_label = _precision_level(score, data_live)
    # Fugle REST trades are enough for MVP large-trade confirmation, but not a
    # broker-grade low-latency feed; keep precise entry gated by live data and
    # public orderbook availability.
    can_precise = bool(data_live and has_fugle_trades and has_public_orderbook)

    return PrecisionContext(
        version=PRECISION_CONTEXT_VERSION,
        precision_level=precision_level,
        precision_label=precision_label,
        readiness_score=round(score, 2),
        can_use_for_precise_daytrade=can_precise,
        tick_data_status="ok" if has_fugle_trades else "missing",
        orderbook_status="partial" if has_public_orderbook else "missing",
        news_status="missing",
        intraday_k_status="ok" if has_intraday else "missing",
        vwap_quality="ok" if has_vwap and vwap_hold_ok else ("partial" if has_vwap else "missing"),
        volume_profile_status=_volume_profile_status(has_volume_ratio, volume_acceleration, price_up_volume_up, price_up_volume_down),
        sector_sync_status=sector_status or "unknown",
        institutional_continuity_status=institutional_status,
        recent_volume_ratio=_safe_float(candidate.get("volume_ratio")),
        volume_acceleration_ratio=volume_acceleration,
        price_up_volume_up=price_up_volume_up,
        price_up_volume_down=price_up_volume_down,
        vwap_hold_ok=vwap_hold_ok,
        trend_structure_ok=trend_structure_ok,
        missing_data=missing,
        available_data=available,
        summary=_summary(precision_label, data_live, missing),
        next_data_to_add=[
            "Fugle WebSocket Trades",
            "更穩定的五檔串流 API",
            "即時新聞 / 題材來源",
        ],
    )


def _volume_acceleration(bars: list[Bar]) -> Optional[float]:
    if len(bars) < 6:
        return None
    recent = sum(float(item.volume or 0) for item in bars[-3:])
    previous = sum(float(item.volume or 0) for item in bars[-6:-3])
    if previous <= 0:
        return None
    return round(recent / previous, 2)


def _price_volume_relation(bars: list[Bar]) -> tuple[bool, bool]:
    if len(bars) < 6:
        return False, False
    price_up = float(bars[-1].close) > float(bars[-4].close)
    recent_vol = sum(float(item.volume or 0) for item in bars[-3:])
    prev_vol = sum(float(item.volume or 0) for item in bars[-6:-3])
    volume_up = recent_vol >= prev_vol
    return bool(price_up and volume_up), bool(price_up and not volume_up)


def _volume_profile_status(has_volume_ratio: bool, acceleration: Optional[float], up_up: bool, up_down: bool) -> str:
    if not has_volume_ratio:
        return "missing"
    if up_up or (acceleration is not None and acceleration >= 1.1):
        return "confirmed"
    if up_down:
        return "divergence"
    return "partial"


def _precision_level(score: float, data_live: bool) -> tuple[str, str]:
    if not data_live:
        return "review_only", "復盤 / 觀察用"
    if score >= 85:
        return "high", "高精準度"
    if score >= 65:
        return "medium", "中等精準度"
    if score >= 40:
        return "low", "低精準度"
    return "insufficient", "資料不足"


def _summary(label: str, data_live: bool, missing: list[str]) -> str:
    if not data_live:
        return "目前不是可用的即時盤中資料，僅適合復盤或觀察，不可作為精準當沖進出依據。"
    if missing:
        return f"目前為{label}，仍缺少 {', '.join(missing[:3])} 等資料，需保守解讀。"
    return f"目前為{label}，資料條件較完整，但仍需搭配停損與部位控管。"


def _safe_float(value) -> Optional[float]:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None
