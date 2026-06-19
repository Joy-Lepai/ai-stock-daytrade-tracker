from __future__ import annotations

from datetime import datetime
from typing import Iterable, Optional

from stock_daytrade_system.data import Bar


TIMEFRAME_DIAGNOSTICS_VERSION = "timeframe_diagnostics_v1_trend_continuation_2026-06-19"
MIN_TREND_SAMPLE_SIZE = 20


def build_timeframe_gap_report() -> dict:
    return {
        "version": TIMEFRAME_DIAGNOSTICS_VERSION,
        "summary": "目前主模型以日線 + Yahoo 5 分 K 批次資料為主，已補上盤中曲線診斷；此層只做判讀透明化，不調整 A / B+ / B 條件。",
        "current_inputs": {
            "intraday": [
                "Yahoo 5 分 K：VWAP、盤中成交量、近 3 根 K 棒高低點結構",
                "個股頁可額外查 Yahoo 1 分 K / TWSE MIS 作即時參考",
            ],
            "short_term": [
                "昨日高點",
                "5 日高點",
                "10 日高點",
                "20 日均量 / 量比",
            ],
            "context": [
                "大盤方向",
                "族群強度",
                "全市場異動池與資料健康度",
            ],
        },
        "known_gaps": [
            "主模型過去偏向單一時間點評分，較少呈現 5 / 15 / 30 分鐘曲線是否延續。",
            "high_risk 可能混合兩種情境：真正追高危險，以及趨勢延續但風險分數偏高。",
            "資料延遲、快取或缺 VWAP / 量比時，不應被當成即時強烈做多。",
        ],
        "new_diagnostics": [
            "intraday_window：VWAP 上方停留、近 3 根 K 棒高低點、回檔深度、量能延續、上影線與盤中突破。",
            "short_term_window：5 / 10 / 20 日高點、20 日均量、短線突破背景。",
            "context_window：20 / 60 日高低區間、波動背景與資料限制。",
            "trend_continuation_watch：只表示趨勢延續觀察，不等於正式可執行訊號。",
            "high_risk_chase：追價風險高，避免追高。",
        ],
    }


def build_timeframe_windows(
    daily_bars: list[Bar],
    intraday_bars: list[Bar],
    *,
    last_price: float,
    vwap: Optional[float],
    previous_high: float,
    high_5d: float,
    high_10d: float,
    volume_ratio: float,
) -> dict:
    return {
        "version": TIMEFRAME_DIAGNOSTICS_VERSION,
        "intraday_window": _intraday_window(intraday_bars, last_price=last_price, vwap=vwap, previous_high=previous_high),
        "short_term_window": _short_term_window(daily_bars, high_5d=high_5d, high_10d=high_10d, volume_ratio=volume_ratio),
        "context_window": _context_window(daily_bars),
    }


def classify_trend_continuation(
    *,
    grade: str,
    entry_status: str,
    last_price: float,
    vwap: Optional[float],
    volume_ratio: float,
    risk_score: float,
    bullish_score: float,
    change_pct: float,
    stop_loss: float,
    target_price: float,
    upper_shadow_pct: float,
    break_prev_high: bool,
    break_5d_high: bool,
    market_state: str,
    timeframe_diagnostics: dict,
    price_status: str = "live",
    market_mode: str = "intraday",
) -> dict:
    intraday = timeframe_diagnostics.get("intraday_window") or {}
    stop_distance_pct = _pct_distance(last_price, stop_loss)
    reward_risk = _reward_risk(last_price, stop_loss, target_price)
    data_sufficient = bool(intraday.get("data_sufficient")) and vwap is not None and volume_ratio is not None
    above_vwap = bool(vwap and last_price >= vwap)
    volume_confirmed = volume_ratio >= 0.8
    curve_ok = bool(intraday.get("higher_high") and intraday.get("higher_low"))
    vwap_stay_ok = bool(intraday.get("vwap_stay_ok"))
    shallow_pullback = bool(intraday.get("shallow_pullback"))
    pullback_holds_vwap = bool(intraday.get("pullback_holds_vwap"))
    volume_continuation = bool(intraday.get("volume_continuation"))
    volume_decay = bool(intraday.get("volume_decay"))
    long_upper_shadow = bool(intraday.get("long_upper_shadow")) or upper_shadow_pct >= 1.2
    has_breakout = bool(break_prev_high or break_5d_high or intraday.get("break_intraday_high"))
    stop_reasonable = stop_distance_pct is not None and 0 < stop_distance_pct <= 4.0
    live_intraday = price_status == "live" and market_mode in {"intraday", "regular", "open", "台股盤中"}

    reasons: list[str] = []
    risk_reasons: list[str] = []
    blockers: list[str] = []

    if not data_sufficient:
        blockers.append("盤中曲線、VWAP 或量比資料不足")
    if above_vwap:
        reasons.append("股價站上 VWAP")
    else:
        blockers.append("尚未站上 VWAP")
    if vwap_stay_ok:
        reasons.append("盤中已有一段時間維持在 VWAP 上方")
    if volume_confirmed:
        reasons.append(f"量比 {volume_ratio:.2f}x 達觀察門檻")
    else:
        blockers.append("量比尚未達觀察門檻")
    if curve_ok:
        reasons.append("近 3 根 K 棒高低點墊高")
    else:
        blockers.append("盤中高低點尚未形成連續墊高")
    if shallow_pullback and pullback_holds_vwap:
        reasons.append("回檔幅度可控且守住 VWAP 附近")
    if has_breakout:
        reasons.append("具備昨高 / 5 日高 / 盤中高點突破背景")
    else:
        blockers.append("尚未形成明確突破背景")
    if volume_continuation:
        reasons.append("短線量能未明顯退潮")
    if stop_reasonable:
        reasons.append("停損距離仍在可控範圍")
    else:
        risk_reasons.append("停損距離過大或無法計算")
    if long_upper_shadow:
        risk_reasons.append("短線上影線偏長，追價容易回落")
    if volume_decay:
        risk_reasons.append("近期量能有退潮跡象")
    if risk_score > 55:
        risk_reasons.append("風險分數偏高")
    if change_pct >= 7:
        risk_reasons.append("漲幅已過大")
    if price_status != "live":
        risk_reasons.append("資料不是即時狀態，不可作為盤中強訊號")
    if market_state == "偏空":
        risk_reasons.append("大盤偏弱")

    continuation_ready = all([
        data_sufficient,
        above_vwap,
        volume_confirmed,
        curve_ok,
        vwap_stay_ok,
        shallow_pullback,
        pullback_holds_vwap,
        has_breakout,
        volume_continuation,
        stop_reasonable,
        not long_upper_shadow,
        not volume_decay,
        market_state != "偏空",
    ])
    strict_strong_long = continuation_ready and live_intraday and risk_score <= 45 and reward_risk >= 1.2 and entry_status == "executable"
    trend_watch = continuation_ready and bullish_score >= 65 and risk_score <= 70 and entry_status not in {"avoid", "data_missing"}
    chase_risk = (
        entry_status == "high_risk"
        or risk_score > 55
        or long_upper_shadow
        or volume_decay
        or change_pct >= 7
        or not stop_reasonable
    )

    if trend_watch:
        status = "trend_continuation_watch"
        label = "做多｜趨勢延續觀察"
        reason_code = "trend_continuation_watch"
        next_step = "等待即時資料、VWAP 守穩、量能延續與突破確認；僅可列為觀察，不直接升級 A 級。"
    elif chase_risk:
        status = "high_risk_chase"
        label = "觀察｜追價風險高"
        reason_code = "high_risk_chase"
        next_step = "避免追價，等待拉回 VWAP 附近、量能不退潮且風險分數下降後再評估。"
    elif data_sufficient:
        status = "neutral_watch"
        label = "觀察｜曲線尚未確認"
        reason_code = "trend_not_confirmed"
        next_step = "等待 VWAP、量能、突破與高低點結構同步確認。"
    else:
        status = "insufficient_curve_data"
        label = "資料不足｜無法判斷趨勢延續"
        reason_code = "insufficient_curve_data"
        next_step = "補齊盤中 K 線、VWAP 與量比後再判斷。"

    invalidation = "跌破 VWAP、近 3 根 K 棒轉為低點下彎、量能退潮、停損距離擴大或資料轉為延遲 / 快取。"
    summary = _trend_summary(status, reasons, risk_reasons, blockers)
    return {
        "version": TIMEFRAME_DIAGNOSTICS_VERSION,
        "status": status,
        "label": label,
        "reason_code": reason_code,
        "summary": summary,
        "reasons": reasons,
        "risk_reasons": risk_reasons,
        "blockers": blockers,
        "next_step": next_step,
        "invalidation": invalidation,
        "can_show_strong_trend": strict_strong_long,
        "is_trend_continuation_watch": status == "trend_continuation_watch",
        "is_high_risk_chase": status == "high_risk_chase",
        "stop_distance_pct": round(stop_distance_pct, 2) if stop_distance_pct is not None else None,
        "reward_risk_ratio": round(reward_risk, 2) if reward_risk is not None else None,
        "data_sufficient": data_sufficient,
        "price_status": price_status,
        "market_mode": market_mode,
    }


def build_trend_continuation_report(candidates: Iterable[object]) -> dict:
    items = list(candidates)
    trend = [item for item in items if (getattr(item, "trend_status", "") or "") == "trend_continuation_watch"]
    chase = [item for item in items if (getattr(item, "trend_status", "") or "") == "high_risk_chase"]
    insufficient = [item for item in items if (getattr(item, "trend_status", "") or "") == "insufficient_curve_data"]
    return {
        "version": TIMEFRAME_DIAGNOSTICS_VERSION,
        "trend_continuation_watch_count": len(trend),
        "high_risk_chase_count": len(chase),
        "insufficient_curve_data_count": len(insufficient),
        "trend_continuation_symbols": [_candidate_name(item) for item in trend[:20]],
        "high_risk_chase_symbols": [_candidate_name(item) for item in chase[:20]],
        "message": "趨勢延續觀察只代表曲線較完整，仍需即時資料、停損距離與原本模型條件通過；不會直接提高 A / B+ / B 數量。",
    }


def trend_continuation_validation(rows: Iterable[object]) -> dict:
    selected = [row for row in rows if _row_value(row, "reason_code") == "trend_continuation_watch"]
    verified = [row for row in selected if _row_float(row, "max_gain_after_scan") is not None]
    continue_up = [row for row in verified if (_row_float(row, "max_gain_after_scan") or 0) >= 1]
    pullback = [row for row in verified if (_row_float(row, "max_drawdown_after_scan") or 0) <= -1]
    sample_size = len(verified)
    return {
        "sample_size": sample_size,
        "raw_sample_size": len(selected),
        "min_sample_size": MIN_TREND_SAMPLE_SIZE,
        "is_statistically_meaningful": sample_size >= MIN_TREND_SAMPLE_SIZE,
        "continue_up_rate": round(len(continue_up) / sample_size * 100, 2) if sample_size else 0.0,
        "pullback_rate": round(len(pullback) / sample_size * 100, 2) if sample_size else 0.0,
        "message": (
            "趨勢延續樣本不足，不建議調整模型。"
            if sample_size < MIN_TREND_SAMPLE_SIZE
            else "趨勢延續樣本已可初步觀察，但仍不建議自動調整 A / B+ / B 條件。"
        ),
    }


def _intraday_window(bars: list[Bar], *, last_price: float, vwap: Optional[float], previous_high: float) -> dict:
    if not bars:
        return {
            "data_sufficient": False,
            "bars_count": 0,
            "message": "缺少盤中 K 線，無法判斷趨勢延續。",
        }
    recent3 = bars[-3:]
    latest = bars[-1]
    interval_minutes = _infer_interval_minutes(bars)
    above_vwap_bars = [bar for bar in bars if vwap and bar.close >= vwap]
    recent_volume = sum(bar.volume for bar in recent3)
    previous3 = bars[-6:-3] if len(bars) >= 6 else []
    previous_volume = sum(bar.volume for bar in previous3)
    recent_high = max(bar.high for bar in recent3)
    recent_low = min(bar.low for bar in recent3)
    pullback_depth_pct = ((recent_high - recent_low) / recent_high * 100) if recent_high else None
    high_before_latest = max((bar.high for bar in bars[:-1]), default=previous_high)
    upper_shadow_pct = _bar_upper_shadow_pct(latest)
    volume_continuation = previous_volume <= 0 or recent_volume >= previous_volume * 0.7
    volume_decay = previous_volume > 0 and recent_volume < previous_volume * 0.65
    return {
        "data_sufficient": len(bars) >= 3 and bool(vwap),
        "bars_count": len(bars),
        "interval_minutes": interval_minutes,
        "recent_window_minutes": interval_minutes * len(recent3),
        "vwap_above_bars": len(above_vwap_bars),
        "vwap_above_minutes": len(above_vwap_bars) * interval_minutes,
        "vwap_stay_ok": len(above_vwap_bars) * interval_minutes >= 15,
        "higher_high": len(recent3) >= 3 and recent3[0].high <= recent3[1].high <= recent3[2].high,
        "higher_low": len(recent3) >= 3 and recent3[0].low <= recent3[1].low <= recent3[2].low,
        "pullback_depth_pct": round(pullback_depth_pct, 2) if pullback_depth_pct is not None else None,
        "shallow_pullback": pullback_depth_pct is not None and pullback_depth_pct <= 1.5,
        "pullback_holds_vwap": bool(vwap and recent_low >= vwap * 0.995),
        "break_intraday_high": bool(last_price >= high_before_latest and high_before_latest > 0),
        "break_prev_high": bool(previous_high and last_price >= previous_high),
        "upper_shadow_pct": round(upper_shadow_pct, 2),
        "long_upper_shadow": upper_shadow_pct >= 1.2,
        "recent_volume": round(recent_volume, 0),
        "previous_volume": round(previous_volume, 0),
        "volume_continuation": volume_continuation,
        "volume_decay": volume_decay,
        "latest_bar_at": latest.timestamp.isoformat(timespec="seconds"),
    }


def _short_term_window(daily_bars: list[Bar], *, high_5d: float, high_10d: float, volume_ratio: float) -> dict:
    last = daily_bars[-1] if daily_bars else None
    avg_volume_5 = _avg(bar.volume for bar in daily_bars[-6:-1])
    avg_volume_20 = _avg(bar.volume for bar in daily_bars[-21:-1])
    last_close = last.close if last else 0
    return {
        "data_sufficient": len(daily_bars) >= 20,
        "high_5d": round(high_5d, 2),
        "high_10d": round(high_10d, 2),
        "high_20d": round(max((bar.high for bar in daily_bars[-21:-1]), default=0), 2),
        "last_close": round(last_close, 2) if last else None,
        "break_5d_high": bool(last_close and last_close >= high_5d),
        "break_10d_high": bool(last_close and last_close >= high_10d),
        "avg_volume_5": round(avg_volume_5, 0),
        "avg_volume_20": round(avg_volume_20, 0),
        "volume_ratio": round(volume_ratio, 2),
    }


def _context_window(daily_bars: list[Bar]) -> dict:
    last = daily_bars[-1] if daily_bars else None
    last_close = last.close if last else 0
    high_20 = max((bar.high for bar in daily_bars[-21:-1]), default=0)
    low_20 = min((bar.low for bar in daily_bars[-21:-1]), default=0)
    high_60 = max((bar.high for bar in daily_bars[-61:-1]), default=0)
    low_60 = min((bar.low for bar in daily_bars[-61:-1]), default=0)
    range_20 = ((high_20 - low_20) / last_close * 100) if last_close and high_20 and low_20 else None
    range_60 = ((high_60 - low_60) / last_close * 100) if last_close and high_60 and low_60 else None
    return {
        "data_sufficient": len(daily_bars) >= 60,
        "high_20d": round(high_20, 2) if high_20 else None,
        "low_20d": round(low_20, 2) if low_20 else None,
        "range_20d_pct": round(range_20, 2) if range_20 is not None else None,
        "high_60d": round(high_60, 2) if high_60 else None,
        "low_60d": round(low_60, 2) if low_60 else None,
        "range_60d_pct": round(range_60, 2) if range_60 is not None else None,
    }


def _trend_summary(status: str, reasons: list[str], risk_reasons: list[str], blockers: list[str]) -> str:
    if status == "trend_continuation_watch":
        return "盤中曲線具備趨勢延續特徵，但仍屬觀察層，不代表正式可執行。"
    if status == "high_risk_chase":
        detail = "；".join(risk_reasons[:2]) or "追價風險偏高"
        return f"目前較像追價風險情境：{detail}。"
    if status == "insufficient_curve_data":
        return "盤中曲線資料不足，不能判斷是否為趨勢延續。"
    detail = "；".join(blockers[:2]) or "等待更多條件同步"
    return f"趨勢延續尚未確認：{detail}。"


def _infer_interval_minutes(bars: list[Bar]) -> int:
    if len(bars) < 2:
        return 5
    delta = bars[-1].timestamp - bars[-2].timestamp
    minutes = int(round(abs(delta.total_seconds()) / 60))
    return minutes if 1 <= minutes <= 30 else 5


def _bar_upper_shadow_pct(bar: Bar) -> float:
    if bar.close <= 0:
        return 0.0
    return max(bar.high - max(bar.open, bar.close), 0.0) / bar.close * 100


def _pct_distance(price: float, stop_loss: float) -> Optional[float]:
    if not price or not stop_loss or stop_loss >= price:
        return None
    return (price - stop_loss) / price * 100


def _reward_risk(price: float, stop_loss: float, target_price: float) -> float:
    risk = price - stop_loss
    reward = target_price - price
    if risk <= 0:
        return 0.0
    return max(reward / risk, 0.0)


def _avg(values: Iterable[float]) -> float:
    nums = [float(value) for value in values if value is not None]
    return sum(nums) / len(nums) if nums else 0.0


def _candidate_name(item: object) -> str:
    return f"{getattr(item, 'symbol', '')}｜{getattr(item, 'name', '')}"


def _row_value(row: object, key: str):
    try:
        return row[key]  # sqlite3.Row
    except Exception:
        return getattr(row, key, None)


def _row_float(row: object, key: str) -> Optional[float]:
    try:
        value = _row_value(row, key)
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None
