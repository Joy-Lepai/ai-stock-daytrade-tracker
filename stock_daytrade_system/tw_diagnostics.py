from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Optional
from zoneinfo import ZoneInfo

from stock_daytrade_system.config import WatchSymbol
from stock_daytrade_system.data import Bar
from stock_daytrade_system.data_freshness import evaluate_data_freshness
from stock_daytrade_system.long_model import SCORING_MODEL_VERSION, LongCandidate
from stock_daytrade_system.timeframe_diagnostics import build_timeframe_gap_report, build_trend_continuation_report
from stock_daytrade_system.tw_momentum_scanner import TW_MOMENTUM_SCANNER_VERSION


TW_DIAGNOSTICS_VERSION = "tw_diagnostics_v2_missed_seen_regret_2026-06-18"
TAIPEI = ZoneInfo("Asia/Taipei")
FILTERED_ENTRY_STATUSES = {"high_risk", "avoid", "wait_volume", "wait_vwap", "wait_breakout", "wait_pullback"}
TRUE_MISSED_REASONS = {
    "below_candidate_threshold",
    "liquidity_filter_removed",
    "data_missing",
    "tpex_failed",
    "twse_failed",
    "yahoo_intraday_failed",
    "unknown",
}


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
        "timeframe_gap_report": build_timeframe_gap_report(),
        "trend_continuation_report": build_trend_continuation_report(candidates),
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
        "這不是報明牌系統，而是當沖條件檢查工具。",
        "A 級代表量能、VWAP、突破、風險與信心都較完整，但仍不等於一定要買。",
        "B+ 代表強勢觀察，等待觸發；可用於虛擬交易練習，不代表正式做多建議。",
        "B 代表等待確認，通常還缺量能、VWAP 或突破條件。",
        "high_risk 代表股票可能很強，但追價風險高，不能包裝成推薦。",
        "avoid 代表目前條件不適合，暫不追蹤或等待結構重新轉強。",
        "data_missing 代表資料不足，不能判斷，不應產生正常當沖建議。",
        "資料過期時，系統僅供觀察，不產生正常可執行建議。",
        "使用者仍需自行承擔投資風險；本系統僅供資料整理、策略追蹤、虛擬交易與回測，不構成投資建議，也不保證獲利。",
    ]


def _data_health(inputs: DiagnosticInputs) -> dict:
    now = _taipei(inputs.now)
    symbol_count = len(list(inputs.all_symbols))
    intraday_symbol_count = len(list(inputs.intraday_symbols))
    latest_at = _latest_intraday_at(inputs.intraday_data)
    age_minutes = _age_minutes(now, latest_at)
    is_intraday_session = inputs.market_session in {"regular", "open", "台股盤中"}
    price_counts = _price_status_counts(inputs, now, is_intraday_session)
    freshness = evaluate_data_freshness(
        now=now,
        latest_at=latest_at,
        source_failed=bool(inputs.intraday_errors and not inputs.intraday_data),
        partial=bool(inputs.daily_errors or inputs.intraday_errors or inputs.taifex_errors or inputs.cmoney_errors),
        is_market_open=is_intraday_session,
    )
    stale = bool(is_intraday_session and (latest_at is None or (age_minutes is not None and age_minutes > 15)))
    partial = bool(inputs.daily_errors or inputs.intraday_errors or inputs.taifex_errors or inputs.cmoney_errors)
    unavailable = _unavailable_symbol_diagnostics(inputs)
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
    total_price_count = max(sum(price_counts.values()), 1)
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
        "data_date": latest_at.date().isoformat() if latest_at else "",
        "generated_at": now.isoformat(timespec="seconds"),
        "age_minutes": round(age_minutes, 1) if age_minutes is not None else None,
        "is_today_data": bool(latest_at and latest_at.date() == now.date()),
        "is_intraday_session": is_intraday_session,
        "is_stale": stale,
        "live_state": freshness.state,
        "live_state_label": freshness.label,
        "live_state_badge": freshness.badge,
        "live_state_message": freshness.message,
        "is_live": freshness.is_live,
        "is_delayed": freshness.is_delayed,
        "uses_last_known": freshness.uses_last_known,
        "last_known_price_policy": "單一資料源短暫失敗時保留上一筆有效價格狀態；若超過 15 分鐘或核心欄位缺漏，禁止顯示盤中可執行。",
        "live_count": price_counts["live"],
        "delayed_count": price_counts["delayed"],
        "cached_count": price_counts["cached"],
        "missing_count": price_counts["missing"],
        "live_ratio": round(price_counts["live"] / total_price_count * 100, 2),
        "delayed_ratio": round(price_counts["delayed"] / total_price_count * 100, 2),
        "cached_ratio": round(price_counts["cached"] / total_price_count * 100, 2),
        "missing_ratio": round(price_counts["missing"] / total_price_count * 100, 2),
        "most_common_error": _most_common_error(inputs),
        "symbol_not_found_count": unavailable["symbol_not_found_count"],
        "symbol_not_found_symbols": unavailable["symbol_not_found_symbols"],
        "yahoo_proxy_unavailable_count": unavailable["yahoo_proxy_unavailable_count"],
        "yahoo_proxy_unavailable_symbols": unavailable["yahoo_proxy_unavailable_symbols"],
        "unavailable_symbols_message": unavailable["message"],
        "data_source_status": status,
        "stock_pool_count": symbol_count,
        "intraday_symbol_count": intraday_symbol_count,
        "daily_success_count": max(symbol_count - len(inputs.daily_errors), 0),
        "daily_failed_count": len(inputs.daily_errors),
        "intraday_success_count": max(intraday_symbol_count - len(inputs.intraday_errors), 0),
        "intraday_failed_count": len(inputs.intraday_errors),
        "failed_symbols": failures[:40],
        "uses_realtime_or_delayed": "批次 dashboard 以 Yahoo chart endpoint 為主，可能延遲；個股頁優先 TWSE MIS。",
    }


def _price_status_counts(inputs: DiagnosticInputs, now: datetime, is_intraday_session: bool) -> dict[str, int]:
    counts = {"live": 0, "delayed": 0, "cached": 0, "missing": 0}
    symbols = list(inputs.intraday_symbols)
    for symbol in symbols:
        bars = inputs.intraday_data.get(symbol) or []
        latest = _latest_bar_at(bars)
        freshness = evaluate_data_freshness(
            now=now,
            latest_at=latest,
            source_failed=symbol in inputs.intraday_errors,
            partial=symbol in inputs.daily_errors,
            is_market_open=is_intraday_session,
        )
        if symbol in inputs.intraday_errors and latest is not None:
            counts["cached"] += 1
        elif freshness.state == "live":
            counts["live"] += 1
        elif freshness.state in {"delayed", "stale"}:
            counts["delayed"] += 1
        elif freshness.state == "last_known":
            counts["cached"] += 1
        else:
            counts["missing"] += 1
    return counts


def _latest_bar_at(bars: list[Bar]) -> Optional[datetime]:
    if not bars:
        return None
    return _taipei(bars[-1].timestamp)


def _most_common_error(inputs: DiagnosticInputs) -> str:
    counts: dict[str, int] = {}
    for errors in (inputs.daily_errors, inputs.intraday_errors, inputs.taifex_errors, inputs.cmoney_errors):
        for error in errors.values():
            text = str(error or "")
            if text:
                counts[text] = counts.get(text, 0) + 1
    return max(counts.items(), key=lambda item: item[1])[0] if counts else ""


def _unavailable_symbol_diagnostics(inputs: DiagnosticInputs) -> dict:
    not_found = set()
    proxy = set()
    for errors in (inputs.daily_errors, inputs.intraday_errors):
        for symbol, error in errors.items():
            text = str(error or "")
            if text.startswith("symbol_not_found"):
                not_found.add(symbol)
            elif text.startswith("yahoo_proxy_unavailable"):
                proxy.add(symbol)
    message_parts = []
    if not_found:
        message_parts.append(f"{len(not_found)} 檔 Yahoo 無資料或代號不存在，已排除評分")
    if proxy:
        message_parts.append(f"{len(proxy)} 個 Yahoo 代理商品不可用，不影響官方資料源")
    return {
        "symbol_not_found_count": len(not_found),
        "symbol_not_found_symbols": sorted(not_found)[:30],
        "yahoo_proxy_unavailable_count": len(proxy),
        "yahoo_proxy_unavailable_symbols": sorted(proxy)[:30],
        "message": "；".join(message_parts),
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
    selected_rows = []
    seen_but_filtered = []
    missed_by_pool = []
    regret_rows = []
    for item in scan_items:
        row = _diagnostic_row(item, candidate_map.get(str(item.get("symbol", ""))))
        row["diagnostic_bucket"] = _diagnostic_bucket(row)
        rows.append(row)
        if row["strong_move"]:
            strong_rows.append(row)
            if row["entered_ai_candidates"]:
                selected_rows.append(row)
            elif row["diagnostic_bucket"] == "seen_but_filtered":
                seen_but_filtered.append(row)
                if _regret_ready(row):
                    regret_rows.append(row)
            elif row["diagnostic_bucket"] == "missed_by_pool":
                missed_by_pool.append(row)
    strong_count = len(strong_rows)
    not_in_ab_count = max(strong_count - len(selected_rows), 0)
    missed_rate = (len(missed_by_pool) / strong_count * 100) if strong_count else 0.0
    seen_rate = (len(seen_but_filtered) / strong_count * 100) if strong_count else 0.0
    not_in_ab_rate = (not_in_ab_count / strong_count * 100) if strong_count else 0.0
    filtered_counts = _count_by(seen_but_filtered, "filter_status")
    missed_reason_counts = _count_by(missed_by_pool, "reason_code")
    return {
        "definition": "漏抓診斷已拆成三類：真漏抓 missed_by_pool、有看到但未推薦 seen_but_filtered、盤後可惜漏掉 regret_after_close。",
        "scanner_limitation": "真漏抓只計算完全沒有進入掃描池或模型評分的強勢股；已看到但因 high_risk / avoid / wait 條件未推薦者，不再算成漏抓。",
        "total_scanned": len(scan_items),
        "strong_move_count": strong_count,
        "entered_ai_count": len(selected_rows),
        "selected_count": len(selected_rows),
        "not_in_ab_count": not_in_ab_count,
        "not_in_ab_rate": round(not_in_ab_rate, 2),
        "seen_but_filtered_count": len(seen_but_filtered),
        "seen_but_filtered_rate": round(seen_rate, 2),
        "missed_by_pool_count": len(missed_by_pool),
        "missed_by_pool_rate": round(missed_rate, 2),
        "missed_count": len(missed_by_pool),
        "missed_rate": round(missed_rate, 2),
        "seen_but_filtered": {
            "count": len(seen_but_filtered),
            "rate": round(seen_rate, 2),
            "by_status": filtered_counts,
            "examples": seen_but_filtered[:30],
        },
        "missed_by_pool": {
            "count": len(missed_by_pool),
            "rate": round(missed_rate, 2),
            "reason_counts": missed_reason_counts,
            "examples": missed_by_pool[:20],
        },
        "regret_after_close": {
            "count": len(regret_rows),
            "rate": round((len(regret_rows) / len(seen_but_filtered) * 100), 2) if seen_but_filtered else 0.0,
            "examples": regret_rows[:20],
            "message": "此區需盤後補上訊號後最高、最低、最大漲幅與最大回撤後才會有完整統計。",
        },
        "rows": rows[:80],
        "missed_examples": missed_by_pool[:20],
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
        "filter_status": _filter_status(grade, entry_status, reason_code),
        "post_scan_high": _float(item.get("post_scan_high")),
        "post_scan_low": _float(item.get("post_scan_low")),
        "max_gain_after_scan": _float(item.get("max_gain_after_scan")),
        "max_drawdown_after_scan": _float(item.get("max_drawdown_after_scan")),
        "regret_after_close_ready": False,
        "latest_at": str(item.get("latest_at") or ""),
        "strong_move": strong_move,
    }


def _reason_code(item: dict, candidate: Optional[LongCandidate], grade: str, entry_status: str, reason: str) -> str:
    if item.get("reason_code"):
        return str(item.get("reason_code"))
    if item.get("data_error"):
        return "data_missing"
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
        return "below_candidate_threshold"
    return "condition_not_met"


def _filter_status(grade: str, entry_status: str, reason_code: str) -> str:
    if entry_status in FILTERED_ENTRY_STATUSES:
        return entry_status
    if reason_code in {"data_missing", "data_failed", "data_insufficient", "yahoo_intraday_failed"}:
        return "data_missing"
    if grade in {"C", "D", "未入選"}:
        return "avoid" if reason_code in {"below_vwap", "avoid"} else "wait_breakout"
    return entry_status or "-"


def _diagnostic_bucket(row: dict) -> str:
    if row["entered_ai_candidates"]:
        return "selected"
    if not row["strong_move"]:
        return "not_strong"
    if _is_seen_but_filtered(row):
        return "seen_but_filtered"
    return "missed_by_pool"


def _is_seen_but_filtered(row: dict) -> bool:
    if row.get("entry_status") in FILTERED_ENTRY_STATUSES:
        return True
    if row.get("filter_status") in FILTERED_ENTRY_STATUSES or row.get("filter_status") == "data_missing":
        return True
    grade = row.get("ai_grade")
    if grade in {"C", "D"}:
        return True
    reason_code = row.get("reason_code")
    return bool(reason_code and reason_code not in TRUE_MISSED_REASONS)


def _regret_ready(row: dict) -> bool:
    gain = _float(row.get("max_gain_after_scan"))
    if gain is None:
        return False
    return gain >= 1.0


def _count_by(rows: list[dict], key: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        value = str(row.get(key) or "unknown")
        counts[value] = counts.get(value, 0) + 1
    return counts


def _reason_message(code: str) -> str:
    return {
        "data_failed": "API 沒有抓到這檔股票或日線不足",
        "data_missing": "資料不足",
        "yahoo_intraday_failed": "Yahoo 盤中資料抓取失敗",
        "twse_failed": "TWSE 上市資料抓取失敗",
        "tpex_failed": "TPEX 上櫃資料抓取失敗",
        "below_candidate_threshold": "低於今日異動候選池門檻",
        "liquidity_filter_removed": "流動性不足，被候選池排除",
        "data_insufficient": "資料不足",
        "volume_low": "量比不足",
        "below_vwap": "未站上 VWAP",
        "extended_from_vwap": "已經漲太高，追價風險高",
        "risk_high": "risk_score 過高或波動太大",
        "confidence_low": "confidence_score 不足",
        "no_prev_high_breakout": "沒有突破昨高",
        "trend_continuation_watch": "盤中曲線偏趨勢延續，但仍屬觀察，不直接升級推薦",
        "high_risk_chase": "追價風險高，避免直接追高",
        "avoid": "已列為 avoid",
        "not_in_candidate_grade": "未進入 A / B+ / B",
        "condition_not_met": "條件未達 A / B+ / B",
        "selected": "已進入 AI 候選名單",
    }.get(code, "條件未達 A / B+ / B")


def _root_cause_diagnosis(data_health: dict, missed: dict) -> list[str]:
    notes = [
        "目前 dashboard 會先依成功納入的 TWSE / TPEX 官方掃描範圍建立異動池；若任一資料源失敗或使用快取，該市場強勢股仍可能漏抓或延遲。",
        "強勢股沒有成為 A 級，不一定是錯誤；A 級會同時要求 VWAP、量比、突破、風險與信心通過。",
        "已大漲、距離 VWAP 過遠、量比爆高或長上影的股票會被歸到 high_risk / 避開區，而不是可執行。",
    ]
    if data_health.get("is_stale"):
        notes.insert(0, "盤中資料已過期，當沖建議應暫停視為正常訊號。")
    if data_health.get("daily_failed_count") or data_health.get("intraday_failed_count"):
        notes.append("部分股票資料抓取失敗，會導致 VWAP、量比或突破條件無法計算。")
    if missed.get("missed_by_pool_count"):
        notes.append("仍有強勢股完全沒有進入掃描或模型評分，請優先查看真漏抓原因欄位。")
    if missed.get("seen_but_filtered_count"):
        notes.append("部分強勢股已被系統看到，但因 high_risk / avoid / wait 條件未推薦，這些不應算成真漏抓。")
    return notes


def _backtest_diagnostic() -> dict:
    return {
        "status": "collecting",
        "message": "系統已開始保存全市場異動快照與盤後驗證欄位；20～60 交易日策略成績會隨資料累積自動更新，樣本不足時不硬算勝率。",
        "required_next_data": [
            "每日 TWSE / TPEX 異動快照",
            "每日 A / B+ / B / high_risk / avoid / data_missing 與 reason_code",
            "訊號後最高、最低、收盤與目標 / 停損觸發結果",
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
