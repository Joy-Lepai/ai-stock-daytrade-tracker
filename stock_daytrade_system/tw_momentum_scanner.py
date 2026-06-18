from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Dict, Iterable, List, Optional
from zoneinfo import ZoneInfo

from stock_daytrade_system.config import WatchSymbol
from stock_daytrade_system.data import Bar
from stock_daytrade_system.indicators import average_volume, pct_change


TW_MOMENTUM_SCANNER_VERSION = "tw_momentum_scanner_v1_2026-06-17"

MOMENTUM_SEED_SYMBOLS = [
    WatchSymbol("6770.TW", "力積電", "semiconductor"),
    WatchSymbol("3016.TW", "嘉晶", "semiconductor"),
    WatchSymbol("2327.TW", "國巨", "passive_components"),
    WatchSymbol("6191.TW", "精成科", "pcb"),
    WatchSymbol("6919.TW", "康霈生技", "biotech"),
    WatchSymbol("8110.TW", "華東", "semiconductor"),
    WatchSymbol("6239.TW", "力成", "semiconductor"),
    WatchSymbol("3260.TW", "威剛", "memory"),
    WatchSymbol("6485.TW", "點序", "memory"),
    WatchSymbol("4967.TW", "十銓", "memory"),
    WatchSymbol("3006.TW", "晶豪科", "memory"),
    WatchSymbol("3035.TW", "智原", "semiconductor"),
    WatchSymbol("3529.TW", "力旺", "semiconductor"),
    WatchSymbol("3653.TW", "健策", "thermal"),
    WatchSymbol("6781.TW", "AES-KY", "battery"),
    WatchSymbol("2367.TW", "燿華", "pcb"),
    WatchSymbol("2313.TW", "華通", "pcb"),
    WatchSymbol("6269.TW", "台郡", "pcb"),
    WatchSymbol("3030.TW", "德律", "electronics"),
    WatchSymbol("4938.TW", "和碩", "electronics"),
    WatchSymbol("3706.TW", "神達", "ai_server"),
    WatchSymbol("3013.TW", "晟銘電", "ai_server"),
    WatchSymbol("6531.TW", "愛普*", "semiconductor"),
    WatchSymbol("1513.TW", "中興電", "electric"),
    WatchSymbol("1609.TW", "大亞", "electric"),
    WatchSymbol("8996.TW", "高力", "thermal"),
    WatchSymbol("4763.TW", "材料-KY", "materials"),
    WatchSymbol("6446.TW", "藥華藥", "biotech"),
    WatchSymbol("4966.TW", "譜瑞-KY", "semiconductor"),
    WatchSymbol("1560.TW", "中砂", "semiconductor"),
]

SYMBOL_ALIASES = {
    "力積電": "6770.TW",
    "嘉晶": "3016.TW",
    "國巨": "2327.TW",
    "精成科": "6191.TW",
    "康霈": "6919.TW",
    "康霈生技": "6919.TW",
    "華東": "8110.TW",
}


@dataclass(frozen=True)
class MomentumScanItem:
    symbol: str
    name: str
    sector: str
    latest_price: Optional[float]
    change_pct: Optional[float]
    volume: Optional[float]
    volume_ratio: Optional[float]
    turnover: Optional[float]
    vwap: Optional[float]
    above_vwap: bool
    break_prev_high: bool
    break_5d_high: bool
    break_20d_high: bool
    initial_status: str
    source_reasons: List[str]
    data_error: str = ""
    latest_at: str = ""
    ai_grade: str = "-"
    entry_status: str = "-"
    trade_bias: str = "watch"
    trade_bias_label: str = "觀察"
    trade_bias_reason: str = "尚未進入模型評分。"
    not_selected_reason: str = ""
    risk_score: Optional[float] = None
    risk_reasons: List[str] = field(default_factory=list)
    upper_shadow_pct: Optional[float] = None
    confidence_score: Optional[float] = None
    confidence_level: str = ""
    confidence_summary: str = ""
    source_scope: str = "watchlist"
    reason_code: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class MomentumScanSummary:
    total: int
    data_success: int
    model_scored: int
    grade_a: int
    grade_b_plus: int
    grade_b: int
    high_risk: int
    excluded: int
    data_failed: int

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class MomentumScanResult:
    items: List[MomentumScanItem]
    summary: MomentumScanSummary
    version: str = TW_MOMENTUM_SCANNER_VERSION

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "summary": self.summary.to_dict(),
            "items": [item.to_dict() for item in self.items],
        }


def momentum_seed_symbols() -> List[WatchSymbol]:
    return list(MOMENTUM_SEED_SYMBOLS)


def normalize_tw_symbol(value: str) -> str:
    raw = (value or "").strip().upper()
    if not raw:
        return ""
    if raw in SYMBOL_ALIASES:
        return SYMBOL_ALIASES[raw]
    if raw.endswith(".TW") or raw.endswith(".TWO"):
        return raw
    if raw.isdigit():
        return f"{raw}.TW"
    return raw


def watch_symbol_for(value: str, known_symbols: Iterable[WatchSymbol] = ()) -> WatchSymbol:
    symbol = normalize_tw_symbol(value)
    known = {item.symbol: item for item in [*MOMENTUM_SEED_SYMBOLS, *list(known_symbols)]}
    if symbol in known:
        return known[symbol]
    return WatchSymbol(symbol, symbol.removesuffix(".TW").removesuffix(".TWO"), "manual_scan")


def build_momentum_universe(symbols: Iterable[WatchSymbol], extra_symbols: Iterable[str] = ()) -> List[WatchSymbol]:
    result: List[WatchSymbol] = []
    seen: set[str] = set()
    for item in [*symbols, *MOMENTUM_SEED_SYMBOLS, *(watch_symbol_for(symbol, symbols) for symbol in extra_symbols)]:
        if not item.symbol or item.symbol in seen:
            continue
        seen.add(item.symbol)
        result.append(item)
    return result


def scan_momentum_candidates(
    symbols: Iterable[WatchSymbol],
    daily_data: Dict[str, List[Bar]],
    intraday_data: Dict[str, List[Bar]],
    model_candidates: Iterable[object] = (),
    original_pool_symbols: Iterable[str] = (),
) -> MomentumScanResult:
    original_set = set(original_pool_symbols or [])
    base_items = [_base_item(item, daily_data.get(item.symbol, []), intraday_data.get(item.symbol, [])) for item in symbols]
    valid_items = [item for item in base_items if not item.data_error]
    selected_symbols = _selected_symbols(valid_items)
    model_map = {getattr(item, "symbol", ""): item for item in model_candidates}
    enriched: List[MomentumScanItem] = []
    for item in base_items:
        source_scope = "watchlist" if not original_set or item.symbol in original_set else "out_of_pool"
        if item.data_error:
            enriched.append(_replace_model(_with_source_scope(item, source_scope), "-", "-", item.not_selected_reason or "資料抓取失敗"))
            continue
        selected = item.symbol in selected_symbols
        model = model_map.get(item.symbol)
        enriched.append(_with_model_result(_with_source_scope(item, source_scope), model, selected))
    enriched.sort(key=_sort_key)
    summary = _summary(enriched)
    return MomentumScanResult(items=enriched[:120], summary=summary)


def scan_single_symbol(
    symbol: WatchSymbol,
    daily_bars: List[Bar],
    intraday_bars: List[Bar],
    model_candidate: Optional[object] = None,
) -> MomentumScanItem:
    item = _base_item(symbol, daily_bars, intraday_bars)
    return _with_model_result(item, model_candidate, not item.data_error)


def _base_item(symbol: WatchSymbol, bars: List[Bar], intraday_bars: List[Bar]) -> MomentumScanItem:
    if len(bars) < 21:
        return MomentumScanItem(
            symbol=symbol.symbol,
            name=symbol.name,
            sector=symbol.sector,
            latest_price=None,
            change_pct=None,
            volume=None,
            volume_ratio=None,
            turnover=None,
            vwap=None,
            above_vwap=False,
            break_prev_high=False,
            break_5d_high=False,
            break_20d_high=False,
            initial_status="資料不足",
            source_reasons=[],
            data_error="資料抓取失敗或日線不足",
            latest_at="",
            not_selected_reason="資料抓取失敗",
        )
    last = bars[-1]
    previous = bars[-2]
    latest_price = intraday_bars[-1].close if intraday_bars else last.close
    latest_at = _taipei_time(intraday_bars[-1].timestamp if intraday_bars else last.timestamp)
    intraday_volume = sum(bar.volume for bar in intraday_bars) if intraday_bars else last.volume
    avg_volume = average_volume(bars, 20)
    volume_ratio = intraday_volume / avg_volume if avg_volume else 0.0
    vwap = _vwap(intraday_bars)
    previous_high = previous.high
    high_5d = max(bar.high for bar in bars[-6:-1])
    high_20d = max(bar.high for bar in bars[-21:-1])
    change = pct_change(latest_price, previous.close)
    turnover = latest_price * intraday_volume
    above_vwap = bool(vwap and latest_price >= vwap)
    reasons = _raw_reasons(change, volume_ratio, latest_price, previous_high, high_5d, high_20d, symbol.sector)
    return MomentumScanItem(
        symbol=symbol.symbol,
        name=symbol.name,
        sector=symbol.sector,
        latest_price=round(latest_price, 2),
        change_pct=round(change, 2),
        volume=round(intraday_volume, 0),
        volume_ratio=round(volume_ratio, 2),
        turnover=round(turnover, 0),
        vwap=round(vwap, 2) if vwap else None,
        above_vwap=above_vwap,
        break_prev_high=latest_price >= previous_high,
        break_5d_high=latest_price >= high_5d,
        break_20d_high=latest_price >= high_20d,
        initial_status="異動送評分" if reasons else "未達異動門檻",
        source_reasons=reasons,
        latest_at=latest_at,
    )


def _selected_symbols(items: List[MomentumScanItem]) -> set[str]:
    selected: set[str] = set()
    for ranking, limit in (
        (sorted(items, key=lambda item: item.change_pct or -999, reverse=True), 100),
        (sorted(items, key=lambda item: item.turnover or 0, reverse=True), 200),
        (sorted(items, key=lambda item: item.volume_ratio or 0, reverse=True), 100),
    ):
        selected.update(item.symbol for item in ranking[:limit] if item.source_reasons)
    selected.update(
        item.symbol
        for item in items
        if item.break_prev_high or item.break_5d_high or item.break_20d_high or (item.change_pct or 0) >= 5
    )
    hot_sectors = _hot_sectors(items)
    selected.update(item.symbol for item in items if item.sector in hot_sectors and (item.change_pct or 0) > 0)
    return selected


def _with_model_result(item: MomentumScanItem, model: Optional[object], selected: bool) -> MomentumScanItem:
    if item.data_error:
        return item
    if model is None:
        return _replace_model(item, "-", "-", "尚未進入評分模型" if selected else "未達異動門檻")
    grade = str(getattr(model, "grade", "-"))
    entry_status = str(getattr(model, "entry_status", "-"))
    reason = _not_selected_reason(model, selected)
    return _replace_model(
        item,
        grade,
        entry_status,
        reason,
        str(getattr(model, "trade_bias", "watch")),
        str(getattr(model, "trade_bias_label", "觀察")),
        str(getattr(model, "trade_bias_reason", "")),
        float(getattr(model, "risk_score", 0) or 0),
        list(getattr(model, "risk_reasons", []) or []),
        float(getattr(model, "upper_shadow_pct", 0) or 0),
        float(getattr(model, "confidence_score", 0) or 0),
        str(getattr(model, "confidence_level", "")),
        str(getattr(model, "confidence_summary", "")),
    )


def _replace_model(
    item: MomentumScanItem,
    grade: str,
    entry_status: str,
    reason: str,
    trade_bias: str = "watch",
    trade_bias_label: str = "觀察",
    trade_bias_reason: str = "",
    risk_score: Optional[float] = None,
    risk_reasons: Optional[List[str]] = None,
    upper_shadow_pct: Optional[float] = None,
    confidence_score: Optional[float] = None,
    confidence_level: str = "",
    confidence_summary: str = "",
) -> MomentumScanItem:
    reason_code = _reason_code(reason, grade, entry_status, item)
    data = item.to_dict()
    data.update(
        {
            "ai_grade": grade,
            "entry_status": entry_status,
            "trade_bias": trade_bias or "watch",
            "trade_bias_label": trade_bias_label or "觀察",
            "trade_bias_reason": trade_bias_reason or reason,
            "not_selected_reason": reason,
            "risk_score": round(risk_score, 2) if risk_score is not None else None,
            "risk_reasons": risk_reasons or [],
            "upper_shadow_pct": round(upper_shadow_pct, 2) if upper_shadow_pct is not None else None,
            "confidence_score": round(confidence_score, 2) if confidence_score is not None else None,
            "confidence_level": confidence_level,
            "confidence_summary": confidence_summary,
            "reason_code": reason_code,
        }
    )
    return MomentumScanItem(**data)


def _with_source_scope(item: MomentumScanItem, source_scope: str) -> MomentumScanItem:
    data = item.to_dict()
    data["source_scope"] = source_scope
    return MomentumScanItem(**data)


def _not_selected_reason(model: object, selected: bool) -> str:
    grade = str(getattr(model, "grade", ""))
    entry_status = str(getattr(model, "entry_status", ""))
    risk_score = float(getattr(model, "risk_score", 0) or 0)
    confidence_score = float(getattr(model, "confidence_score", 0) or 0)
    above_vwap = bool(getattr(model, "above_vwap", False))
    volume_ratio = float(getattr(model, "volume_ratio", 0) or 0)
    last_price = float(getattr(model, "last_price", 0) or 0)
    vwap = getattr(model, "vwap", None)
    distance = ((last_price - float(vwap)) / float(vwap) * 100) if vwap else 0.0
    if grade in {"A", "B+", "B"}:
        return "已進入正式候選"
    if entry_status == "high_risk":
        return "強勢但追價風險高，不列為 A，可列入觀察。"
    if not above_vwap:
        return "未站上 VWAP"
    if volume_ratio < 0.8:
        return "量比不足"
    if distance > 3:
        return "距離 VWAP 太遠"
    if risk_score > 55:
        return "risk_score 過高"
    if confidence_score and confidence_score < 55:
        return "confidence_score 不足"
    if entry_status == "avoid":
        return "已列為 avoid"
    if not selected:
        return "未達異動門檻"
    return "條件未達 A/B+/B"


def _reason_code(reason: str, grade: str, entry_status: str, item: MomentumScanItem) -> str:
    if item.data_error:
        return "data_missing"
    if item.source_scope == "out_of_pool" and grade in {"A", "B+", "B", "C", "D"}:
        if grade in {"A", "B+", "B"}:
            return "full_market_detected"
        return "not_in_watchlist"
    if grade in {"A", "B+", "B"}:
        return "selected"
    if entry_status == "high_risk":
        return "high_chase_risk"
    if "量比" in reason:
        return "low_volume_ratio"
    if "未站上" in reason:
        return "below_vwap"
    if "太遠" in reason:
        return "too_far_from_vwap"
    if "risk_score" in reason:
        return "high_chase_risk"
    if "confidence" in reason:
        return "confidence_low"
    if entry_status == "avoid":
        return "avoid"
    if "未達異動" in reason:
        return "low_liquidity"
    if item.break_prev_high is False:
        return "no_breakout"
    return "candidate_but_not_triggered"


def _raw_reasons(
    change_pct: float,
    volume_ratio: float,
    latest_price: float,
    previous_high: float,
    high_5d: float,
    high_20d: float,
    sector: str,
) -> List[str]:
    reasons = []
    if change_pct >= 5:
        reasons.append("漲幅大於5%")
    if volume_ratio >= 1.5:
        reasons.append("量比排行候選")
    elif volume_ratio >= 1.0:
        reasons.append("量能放大")
    if latest_price >= previous_high:
        reasons.append("突破昨日高點")
    if latest_price >= high_5d:
        reasons.append("突破5日高點")
    if latest_price >= high_20d:
        reasons.append("突破20日高點")
    return reasons


def _hot_sectors(items: List[MomentumScanItem]) -> set[str]:
    grouped: dict[str, List[MomentumScanItem]] = {}
    for item in items:
        if item.sector:
            grouped.setdefault(item.sector, []).append(item)
    hot = set()
    for sector, rows in grouped.items():
        positive = [item for item in rows if (item.change_pct or 0) > 0]
        avg_change = sum(item.change_pct or 0 for item in rows) / len(rows)
        if len(positive) >= 2 and avg_change >= 1:
            hot.add(sector)
    return hot


def _summary(items: List[MomentumScanItem]) -> MomentumScanSummary:
    success = [item for item in items if not item.data_error]
    return MomentumScanSummary(
        total=len(items),
        data_success=len(success),
        model_scored=sum(1 for item in success if item.ai_grade != "-"),
        grade_a=sum(1 for item in success if item.ai_grade == "A"),
        grade_b_plus=sum(1 for item in success if item.ai_grade == "B+"),
        grade_b=sum(1 for item in success if item.ai_grade == "B"),
        high_risk=sum(1 for item in success if item.entry_status == "high_risk"),
        excluded=sum(1 for item in success if item.ai_grade not in {"A", "B+", "B"}),
        data_failed=sum(1 for item in items if item.data_error),
    )


def _sort_key(item: MomentumScanItem) -> tuple:
    grade_order = {"A": 0, "B+": 1, "B": 2, "C": 3, "D": 4, "-": 5}
    return (
        grade_order.get(item.ai_grade, 5),
        0 if item.source_reasons else 1,
        -(item.change_pct or -999),
        -(item.turnover or 0),
        item.symbol,
    )


def _vwap(bars: List[Bar]) -> Optional[float]:
    total_volume = sum(bar.volume for bar in bars)
    if total_volume <= 0:
        return None
    return sum(((bar.high + bar.low + bar.close) / 3) * bar.volume for bar in bars) / total_volume


def _taipei_time(value: datetime) -> str:
    if value.tzinfo is not None:
        return value.astimezone(ZoneInfo("Asia/Taipei")).isoformat(timespec="seconds")
    if value.hour < 8:
        value = value.replace(tzinfo=ZoneInfo("UTC")).astimezone(ZoneInfo("Asia/Taipei"))
    return value.isoformat(timespec="seconds")
