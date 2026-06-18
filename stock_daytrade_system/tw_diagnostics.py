from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Optional
from zoneinfo import ZoneInfo

from stock_daytrade_system.config import WatchSymbol
from stock_daytrade_system.data import Bar
from stock_daytrade_system.long_model import SCORING_MODEL_VERSION, LongCandidate
from stock_daytrade_system.tw_momentum_scanner import TW_MOMENTUM_SCANNER_VERSION


TW_DIAGNOSTICS_VERSION = "tw_diagnostics_v1_freshness_missed_2026-06-18"
TAIPEI = ZoneInfo("Asia/Taipei")


@dataclass(frozen=True)
class DiagnosticInputs:
    now: datetime
    all_symbols: Iterable[WatchSymbol]
    intraday_symbols: Iterable[str]
    daily_data: dict[str, list[Bar]]
    intraday_data: dict[str, list[Bar]]
    daily_errors: dict[str, str]
    intraday_errors: dict[str, str]
    taifex_errors: dict[str, str]
    cmoney_errors: dict[str, str]
    market_session: str
    market_status: str
    momentum_scan: dict
    candidates: Iterable[LongCandidate]
    full_market_scan: Optional[dict] = None


def build_tw_diagnostics(inputs: DiagnosticInputs) -> dict:
    candidates = list(inputs.candidates)
    scan_items = list((inputs.momentum_scan or {}).get("items") or [])
    data_health = _data_health(inputs)
    missed = _missed_stock_analysis(scan_items, candidates)
    full_market = _full_market_summary(inputs.full_market_scan or {}, scan_items)
    return {
        "version": TW_DIAGNOSTICS_VERSION,
        "data_health": data_health,
        "full_market_scan": full_market,
        "missed_stock_analysis": missed,
        "model_conditions": model_conditions(),
        "root_cause_diagnosis": _root_cause_diagnosis(data_health, missed),
        "user_guide": user_guide(),
        "backtest_diagnostic": _backtest_diagnostic(),
    }


def model_conditions() -> dict:
    return {
        "scoring_model_version": SCORING_MODEL_VERSION,
        "momentum_scanner_version": TW_MOMENTUM_SCANNER_VERSION,
        "a": [
            "bullish_score >= 80",
            "risk_score <= 40",
            "above_vwap = true",
            "break_prev_high = true",
            "volume_ratio >= 1.0",
            "market_status 不是偏空",
            "confidence_score >= 70",
            "沒有重大 indicator_conflicts，且沒有明顯長上影或過度延伸",
        ],
        "b_plus": [
            "bullish_score >= 70",
            "risk_score <= 55",
            "volume_ratio >= 0.8",
            "站上 VWAP 或距離 VWAP <= 0.5%",
            "突破昨高或距離突破價 <= 0.5%",
            "market_status 不是偏空",
            "confidence_score >= 55",
            "不可是 high_risk / avoid",
        ],
        "b": [
            "bullish_score >= 65",
            "risk_score <= 55",
            "站上 VWAP 或貼近 VWAP",
            "break_prev_high = true",
            "volume_ratio >= 0.8",
            "confidence_score >= 50",
        ],
        "c_d_exclusion": [
            "risk_score > 70、跌破 VWAP、大盤偏空且多方分數不足，會落入 D",
            "risk_score > 55、長上影、漲幅過度延伸，會落入 C 或 high_risk",
            "volume_ratio < 0.8 常會變成 wait_volume，不會直接列為 A/B+",
            "未站上 VWAP 常會變成 wait_vwap 或 avoid",
        ],
        "entry_status": [
            "executable：A 級且信心足夠",
            "practice_long：B+ / B 且站上 VWAP、量比 >= 0.8、風險可控",
            "wait_volume：分數足夠但量能未達標",
            "wait_vwap：分數足夠但尚未站上 VWAP",
            "high_risk：風險分數偏高或已出現追價衝突",
            "avoid：條件不足、跌破 VWAP 或大盤偏弱",
        ],
    }


def user_guide() -> list[str]:
    return [
        "這個系統不是報明牌，而是整理股票是否符合當沖追蹤條件。",
        "A 級代表量能、VWAP、突破、風險與信心都較完整，但數量本來就會少。",
        "B+ 代表強勢觀察與虛擬交易練習，不代表正式做多建議。",
        "B 代表等待確認，通常還缺量能、VWAP 或突破。",
        "避開區代表追價風險高、資料不完整或條件失效。",
        "資料過期或缺漏時，系統會暫停正常當沖建議，僅供觀察。",
        "本系統僅供資料整理、策略追蹤、虛擬交易與回測，不構成投資建議，也不保證獲利。",
    ]


def _data_health(inputs: DiagnosticInputs) -> dict:
    now = _taipei(inputs.now)
    symbol_count = len(list(inputs.all_symbols))
    intraday_symbol_count = len(list(inputs.intraday_symbols))
    latest_at = _latest_intraday_at(inputs.intraday_data)
    age_minutes = _age_minutes(now, latest_at)
    is_intraday_session = inputs.market_session in {"regular", "open", "台股盤中"}
    stale = bool(is_intraday_session and (latest_at is None or (age_minutes is not None and age_minutes > 15)))
    partial = bool(inputs.daily_errors or inputs.intraday_errors or inputs.taifex_errors or inputs.cmoney_errors)
    if stale:
        status = "過期"
        recommendation_state = "資料過期，暫停產生當沖建議"
    elif partial:
        status = "部分缺漏"
        recommendation_state = "目前資料不完整，僅供觀察，不建議交易"
    else:
        status = "正常"
        recommendation_state = "資料狀態可供模型評分"
    if not inputs.intraday_data:
        status = "異常"
        recommendation_state = "目前盤中資料不足，暫停產生當沖建議"
    failures = sorted(set(inputs.daily_errors) | set(inputs.intraday_errors))
    return {
        "status": status,
        "recommendation_state": recommendation_state,
        "data_sources": [
            "Yahoo Finance chart endpoint：日線、5 分 K 與 dashboard 批次盤中資料",
            "TWSE MIS：/tw/advisor 個股即時參考價，失敗時回退 Yahoo 1 分 K",
            "TAIFEX 官方資料：台指期與大盤輔助判斷，失敗時排除",
            "CMoney：法人排行輔助，失敗時排除",
        ],
        "latest_intraday_at": latest_at.isoformat(timespec="seconds") if latest_at else "",
        "generated_at": now.isoformat(timespec="seconds"),
        "age_minutes": round(age_minutes, 1) if age_minutes is not None else None,
        "is_today_data": bool(latest_at and latest_at.date() == now.date()),
        "is_intraday_session": is_intraday_session,
        "is_stale": stale,
        "stock_pool_count": symbol_count,
        "intraday_symbol_count": intraday_symbol_count,
        "daily_success_count": max(symbol_count - len(inputs.daily_errors), 0),
        "daily_failed_count": len(inputs.daily_errors),
        "intraday_success_count": max(intraday_symbol_count - len(inputs.intraday_errors), 0),
        "intraday_failed_count": len(inputs.intraday_errors),
        "failed_symbols": failures[:40],
        "uses_realtime_or_delayed": "批次 dashboard 以 Yahoo chart endpoint 為主，可能延遲；個股頁優先 TWSE MIS。",
    }


def _full_market_summary(full_market_scan: dict, scan_items: list[dict]) -> dict:
    summary = dict(full_market_scan.get("summary") or {})
    source_status = dict(full_market_scan.get("source_status") or {})
    out_of_pool = [item for item in scan_items if item.get("source_scope") == "out_of_pool"]
    by_status = {
        "a": sum(1 for item in scan_items if item.get("ai_grade") == "A"),
        "b_plus": sum(1 for item in scan_items if item.get("ai_grade") == "B+"),
        "b": sum(1 for item in scan_items if item.get("ai_grade") == "B"),
        "high_risk": sum(1 for item in scan_items if item.get("entry_status") == "high_risk"),
        "avoid": sum(1 for item in scan_items if item.get("entry_status") == "avoid"),
        "data_missing": sum(1 for item in scan_items if item.get("data_error")),
        "out_of_pool": len(out_of_pool),
    }
    return {
        "version": full_market_scan.get("version", ""),
        "generated_at": full_market_scan.get("generated_at", ""),
        "summary": summary,
        "source_status": source_status,
        "by_status": by_status,
        "out_of_pool_symbols": [f"{item.get('symbol')}｜{item.get('name')}" for item in out_of_pool[:20]],
    }


def _missed_stock_analysis(scan_items: list[dict], candidates: list[LongCandidate]) -> dict:
    candidate_map = {item.symbol: item for item in candidates}
    rows = []
    strong_rows = []
    missed_rows = []
    for item in scan_items:
        row = _diagnostic_row(item, candidate_map.get(str(item.get("symbol", ""))))
        rows.append(row)
        if row["strong_move"]:
            strong_rows.append(row)
            if not row["entered_ai_candidates"]:
                missed_rows.append(row)
    missed_rate = (len(missed_rows) / len(strong_rows) * 100) if strong_rows else 0.0
    return {
        "definition": "漲幅 > 3% 且量比 >= 0.8，或漲幅 > 5%，但未列入 A / B+ / B，視為本掃描池內漏抓。",
        "scanner_limitation": "目前不是全市場掃描；股票池外個股仍需手動輸入或擴充 TWSE/TPEX 全市場資料源。",
        "total_scanned": len(scan_items),
        "strong_move_count": len(strong_rows),
        "entered_ai_count": sum(1 for row in rows if row["entered_ai_candidates"]),
        "missed_count": len(missed_rows),
        "missed_rate": round(missed_rate, 2),
        "rows": rows[:80],
        "missed_examples": missed_rows[:20],
    }


def _diagnostic_row(item: dict, candidate: Optional[LongCandidate]) -> dict:
    grade = str(item.get("ai_grade") or getattr(candidate, "grade", "未入選") or "未入選")
    if grade == "-":
        grade = "未入選"
    entry_status = str(item.get("entry_status") or getattr(candidate, "entry_status", "-") or "-")
    reason = str(item.get("not_selected_reason") or item.get("data_error") or "")
    reason_code = _reason_code(item, candidate, grade, entry_status, reason)
    change = _float(item.get("change_pct"))
    volume_ratio = _float(item.get("volume_ratio"))
    entered = grade in {"A", "B+", "B"}
    strong_move = bool((change is not None and change > 3 and (volume_ratio or 0) >= 0.8) or (change is not None and change > 5))
    return {
        "symbol": item.get("symbol", ""),
        "name": item.get("name", ""),
        "change_pct": change,
        "latest_price": _float(item.get("latest_price")),
        "volume": _float(item.get("volume")),
        "volume_ratio": volume_ratio,
        "turnover": _float(item.get("turnover")),
        "above_vwap": bool(item.get("above_vwap")),
        "break_prev_high": bool(item.get("break_prev_high")),
        "break_intraday_high": bool(item.get("break_5d_high") or item.get("break_20d_high")),
        "entered_ai_candidates": entered,
        "ai_grade": grade,
        "entry_status": entry_status,
        "not_selected_reason": reason or _reason_message(reason_code),
        "reason_code": reason_code,
        "latest_at": str(item.get("latest_at") or ""),
        "strong_move": strong_move,
    }


def _reason_code(item: dict, candidate: Optional[LongCandidate], grade: str, entry_status: str, reason: str) -> str:
    if item.get("reason_code"):
        return str(item.get("reason_code"))
    if item.get("data_error"):
        return "data_failed"
    if grade in {"A", "B+", "B"}:
        return "selected"
    if "資料" in reason:
        return "data_insufficient"
    if "量比" in reason:
        return "volume_low"
    if "未站上" in reason or not bool(item.get("above_vwap")):
        return "below_vwap"
    if "太遠" in reason:
        return "extended_from_vwap"
    if "risk_score" in reason or entry_status == "high_risk":
        return "risk_high"
    if "confidence" in reason:
        return "confidence_low"
    if candidate and not candidate.break_prev_high:
        return "no_prev_high_breakout"
    if entry_status == "avoid":
        return "avoid"
    if grade == "未入選":
        return "not_in_candidate_grade"
    return "condition_not_met"


def _reason_message(code: str) -> str:
    return {
        "data_failed": "API 沒有抓到這檔股票或日線不足",
        "data_insufficient": "資料不足",
        "volume_low": "量比不足",
        "below_vwap": "未站上 VWAP",
        "extended_from_vwap": "已經漲太高，追價風險高",
        "risk_high": "risk_score 過高或波動太大",
        "confidence_low": "confidence_score 不足",
        "no_prev_high_breakout": "沒有突破昨高",
        "avoid": "已列為 avoid",
        "not_in_candidate_grade": "未進入 A / B+ / B",
        "condition_not_met": "條件未達 A / B+ / B",
        "selected": "已進入 AI 候選名單",
    }.get(code, "條件未達 A / B+ / B")


def _root_cause_diagnosis(data_health: dict, missed: dict) -> list[str]:
    notes = [
        "目前 dashboard 掃描池不是全市場，主要由 watchlist、內建熱門異動股與手動加入清單組成；股票池外個股會漏抓。",
        "強勢股沒有成為 A 級，不一定是錯誤；A 級會同時要求 VWAP、量比、突破、風險與信心通過。",
        "已大漲、距離 VWAP 過遠、量比爆高或長上影的股票會被歸到 high_risk / 避開區，而不是可執行。",
    ]
    if data_health.get("is_stale"):
        notes.insert(0, "盤中資料已過期，當沖建議應暫停視為正常訊號。")
    if data_health.get("daily_failed_count") or data_health.get("intraday_failed_count"):
        notes.append("部分股票資料抓取失敗，會導致 VWAP、量比或突破條件無法計算。")
    if missed.get("missed_count"):
        notes.append("掃描池內仍有強勢股未進入 A / B+ / B，請優先查看漏抓原因欄位。")
    return notes


def _backtest_diagnostic() -> dict:
    return {
        "status": "sample_limited",
        "message": "最近 20～60 交易日的全市場漏抓率需要完整歷史掃描資料；目前資料庫主要保存 recommendations / paper trades，樣本不足時不硬算勝率。",
        "required_next_data": [
            "每日全市場或至少成交金額前段股票快照",
            "每日 A / B+ / B / 排除清單與 reason_code",
            "隔日或盤中最高、最低、收盤與觸發結果",
        ],
    }


def _latest_intraday_at(intraday_data: dict[str, list[Bar]]) -> Optional[datetime]:
    latest: Optional[datetime] = None
    for bars in intraday_data.values():
        if not bars:
            continue
        value = _taipei(bars[-1].timestamp)
        if latest is None or value > latest:
            latest = value
    return latest


def _age_minutes(now: datetime, latest: Optional[datetime]) -> Optional[float]:
    if latest is None:
        return None
    return max((now - latest).total_seconds() / 60, 0.0)


def _taipei(value: datetime) -> datetime:
    if value.tzinfo is None:
        if value.hour < 8:
            return value.replace(tzinfo=ZoneInfo("UTC")).astimezone(TAIPEI)
        return value.replace(tzinfo=TAIPEI)
    return value.astimezone(TAIPEI)


def _float(value) -> Optional[float]:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None
