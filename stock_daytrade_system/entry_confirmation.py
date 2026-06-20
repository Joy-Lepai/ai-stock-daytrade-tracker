from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Optional

from stock_daytrade_system.data import Bar


ENTRY_CONFIRMATION_VERSION = "entry_confirmation_v1_orderbook_radar_2026-06-21"


@dataclass(frozen=True)
class EntryConfirmation:
    version: str
    status: str
    status_label: str
    score: float
    can_consider_entry: bool
    summary: str
    next_step: str
    invalidation: str
    price_momentum_status: str
    vwap_status: str
    volume_status: str
    orderbook_status: str
    risk_status: str
    data_status: str
    bid_total_volume: Optional[float]
    ask_total_volume: Optional[float]
    orderbook_imbalance: Optional[float]
    large_trade_status: str
    large_trade_summary: str
    checks: list[dict]
    blockers: list[str]
    warnings: list[str]

    def to_dict(self) -> dict:
        return asdict(self)


def build_entry_confirmation(
    *,
    candidate: Optional[dict],
    intraday_bars: list[Bar],
    data_health: dict,
    realtime_quote: dict,
) -> EntryConfirmation:
    candidate = candidate or {}
    data_health = data_health or {}
    realtime_quote = realtime_quote or {}
    entry_status = str(candidate.get("entry_status") or "")
    trade_bias = str(candidate.get("trade_bias") or "")
    risk_score = _float(candidate.get("risk_score"), default=999.0) or 999.0
    last_price = _float(candidate.get("last_price"), realtime_quote.get("price"))
    vwap = _float(candidate.get("vwap"))
    volume_ratio = _float(candidate.get("volume_ratio"))
    stop_loss = _float(candidate.get("stop_loss"))

    data_live = bool(data_health.get("is_live") and data_health.get("can_use_for_intraday_signal"))
    above_vwap = bool(candidate.get("above_vwap")) if vwap is not None else False
    price_momentum = _price_momentum_status(intraday_bars)
    orderbook = _orderbook_status(realtime_quote)
    stop_distance_pct = _stop_distance_pct(last_price, stop_loss)
    stop_ok = bool(stop_distance_pct is not None and 0 < stop_distance_pct <= 3)
    volume_ok = bool(volume_ratio is not None and volume_ratio >= 1)
    volume_near = bool(volume_ratio is not None and 0.8 <= volume_ratio < 1)
    risk_ok = risk_score <= 55 and stop_ok
    orderbook_ok = orderbook["status"] in {"supportive", "neutral", "limit_up_locked"}
    hard_blockers = []
    warnings = []

    if not data_live:
        hard_blockers.append("資料不是即時盤中資料，僅供觀察。")
    if entry_status in {"high_risk", "avoid", "data_missing"} or trade_bias in {"watch", "avoid"} and risk_score > 55:
        hard_blockers.append("目前模型狀態不是可執行進場。")
    if not above_vwap:
        hard_blockers.append("尚未站上 VWAP。")
    if not stop_ok:
        hard_blockers.append("停損距離過大或缺停損價。")
    if orderbook["status"] == "sell_pressure":
        hard_blockers.append("五檔賣壓偏重。")
    if orderbook["status"] == "missing":
        warnings.append("缺公開五檔，盤口確認不足。")
    if volume_near:
        warnings.append("量比接近門檻，但尚未完全確認。")
    if realtime_quote.get("is_limit_up_locked"):
        warnings.append("漲停鎖住代表追價風險升高，不宜直接追高。")

    checks = [
        _check("資料即時", data_live, "資料可作盤中判斷" if data_live else "非即時資料，禁止即時進場"),
        _check("價格動能", price_momentum["ok"], price_momentum["summary"]),
        _check("站上 VWAP", above_vwap, f"VWAP {vwap:.2f}" if vwap is not None else "缺 VWAP"),
        _check(
            "量能確認",
            volume_ok,
            f"量比 {volume_ratio:.2f}x" if volume_ratio is not None else "缺量比",
        ),
        _check("五檔盤口", orderbook_ok, orderbook["summary"]),
        _check(
            "風險可控",
            risk_ok,
            _risk_summary(risk_score, stop_distance_pct),
        ),
        _check("逐筆大單", False, "目前缺逐筆成交，無法判斷大單敲進 / 敲出"),
    ]
    score = sum(item["points"] for item in checks)
    if hard_blockers:
        status = "blocked" if data_live else "review_only"
    elif entry_status == "executable" and score >= 75:
        status = "ready"
    elif score >= 60:
        status = "near"
    else:
        status = "waiting"
    label = {
        "ready": "接近進場確認",
        "near": "接近確認",
        "waiting": "等待確認",
        "blocked": "暫不進場",
        "review_only": "復盤 / 觀察",
    }[status]
    can_consider = bool(status == "ready" and entry_status == "executable" and data_live and not hard_blockers)
    summary = _summary(status, checks, hard_blockers, warnings)
    next_step = _next_step(status, volume_ok, above_vwap, orderbook["status"])
    invalidation = "跌破 VWAP、五檔賣壓轉強、量能退潮、跌破停損價或資料轉為延遲 / 快取時失效。"

    return EntryConfirmation(
        version=ENTRY_CONFIRMATION_VERSION,
        status=status,
        status_label=label,
        score=round(score, 2),
        can_consider_entry=can_consider,
        summary=summary,
        next_step=next_step,
        invalidation=invalidation,
        price_momentum_status=price_momentum["status"],
        vwap_status="above" if above_vwap else ("missing" if vwap is None else "below"),
        volume_status="confirmed" if volume_ok else ("near" if volume_near else "weak"),
        orderbook_status=orderbook["status"],
        risk_status="controlled" if risk_ok else "high",
        data_status="live" if data_live else str(data_health.get("price_status") or "not_live"),
        bid_total_volume=orderbook["bid_total_volume"],
        ask_total_volume=orderbook["ask_total_volume"],
        orderbook_imbalance=orderbook["imbalance"],
        large_trade_status="missing",
        large_trade_summary="目前缺逐筆成交資料，無法判斷大單敲進 / 敲出。",
        checks=checks,
        blockers=hard_blockers,
        warnings=warnings,
    )


def _price_momentum_status(bars: list[Bar]) -> dict:
    if len(bars) < 4:
        return {"status": "missing", "ok": False, "summary": "盤中 K 線不足"}
    recent = list(bars)[-4:]
    higher_close = recent[-1].close > recent[-2].close > recent[-3].close
    higher_low = recent[-1].low >= recent[-2].low >= recent[-3].low
    if higher_close and higher_low:
        return {"status": "rising", "ok": True, "summary": "最近價格墊高，短線動能延續"}
    if recent[-1].close >= recent[0].close and higher_low:
        return {"status": "stable", "ok": True, "summary": "價格未明顯轉弱，低點仍守住"}
    return {"status": "weak", "ok": False, "summary": "短線價格結構尚未轉強"}


def _orderbook_status(quote: dict) -> dict:
    status = str(quote.get("five_level_status") or "missing")
    bid_total = _float(quote.get("bid_total_volume"))
    ask_total = _float(quote.get("ask_total_volume"))
    imbalance = _float(quote.get("orderbook_imbalance"))
    if status == "missing":
        return _orderbook_payload("missing", bid_total, ask_total, imbalance, "五檔資料不足")
    if quote.get("is_limit_up_locked"):
        return _orderbook_payload("limit_up_locked", bid_total, ask_total, imbalance, "漲停鎖住，買盤堆積但追價風險高")
    if quote.get("is_limit_down_locked"):
        return _orderbook_payload("sell_pressure", bid_total, ask_total, imbalance, "跌停鎖住或買盤不足")
    if imbalance is None:
        return _orderbook_payload("neutral", bid_total, ask_total, imbalance, "五檔可參考，但買賣盤差不足")
    if imbalance >= 20:
        return _orderbook_payload("supportive", bid_total, ask_total, imbalance, "委買量明顯大於委賣量")
    if imbalance <= -20:
        return _orderbook_payload("sell_pressure", bid_total, ask_total, imbalance, "委賣量明顯大於委買量")
    return _orderbook_payload("neutral", bid_total, ask_total, imbalance, "買賣盤力道接近")


def _orderbook_payload(status: str, bid_total, ask_total, imbalance, summary: str) -> dict:
    return {
        "status": status,
        "bid_total_volume": bid_total,
        "ask_total_volume": ask_total,
        "imbalance": imbalance,
        "summary": summary,
    }


def _check(label: str, ok: bool, detail: str) -> dict:
    return {"label": label, "ok": bool(ok), "detail": detail, "points": 14 if ok else 0}


def _risk_summary(risk_score: float, stop_distance_pct: Optional[float]) -> str:
    distance = "-" if stop_distance_pct is None else f"{stop_distance_pct:.2f}%"
    return f"風險分數 {risk_score:.0f}，停損距離 {distance}"


def _stop_distance_pct(price: Optional[float], stop_loss: Optional[float]) -> Optional[float]:
    if price is None or stop_loss is None or price <= 0 or stop_loss <= 0 or price <= stop_loss:
        return None
    return round((price - stop_loss) / price * 100, 2)


def _summary(status: str, checks: list[dict], blockers: list[str], warnings: list[str]) -> str:
    if blockers:
        return f"尚未適合進場：{blockers[0]}"
    passed = [item["label"] for item in checks if item["ok"]]
    if status == "ready":
        return f"接近進場確認：{', '.join(passed[:4])}，仍需依停損與部位控管執行。"
    if status == "near":
        extra = f"；{warnings[0]}" if warnings else ""
        return f"條件接近，但仍需等待最後確認{extra}"
    return "目前仍在等待確認，不建議提前追價。"


def _next_step(status: str, volume_ok: bool, above_vwap: bool, orderbook_status: str) -> str:
    if status == "ready":
        return "若原模型仍為 executable，先檢查停損距離與部位大小，再進行虛擬交易觀察。"
    if not above_vwap:
        return "等待股價重新站回 VWAP 並維持。"
    if not volume_ok:
        return "等待量比放大到 1.0x 以上，確認短線資金進場。"
    if orderbook_status in {"sell_pressure", "missing"}:
        return "等待五檔賣壓下降或買盤轉強，再重新評估。"
    return "等待突破觸發價或回測 VWAP 不破。"


def _float(*values, default=None):
    for value in values:
        try:
            if value is None or value == "":
                continue
            return float(value)
        except (TypeError, ValueError):
            continue
    return default
