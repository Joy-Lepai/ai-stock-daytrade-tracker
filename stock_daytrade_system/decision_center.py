from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Any, Iterable, Optional
from zoneinfo import ZoneInfo


DECISION_CENTER_VERSION = "decision_center_v1_2026-06-13"
DISCLAIMER = "本系統僅供資料整理、策略追蹤、虛擬交易與回測，不構成投資建議，也不保證獲利。"


def build_decision_center(
    *,
    market: str,
    market_session: str = "",
    market_status: str = "",
    candidates: Iterable[Any] = (),
    checklist: Optional[dict] = None,
    b_plus_triggers: Iterable[dict] = (),
    data_source_status: Any = None,
    paper_stats: Optional[dict] = None,
    confidence_summary: Optional[dict] = None,
) -> dict:
    items = list(candidates or [])
    triggers = list(b_plus_triggers or [])
    counts = _counts(items, checklist or {}, triggers)
    stats = paper_stats or {}
    confidence = confidence_summary or _confidence_counts(items, checklist or {})
    data_state = _data_state(data_source_status, checklist or {})
    tendency = _operation_tendency(counts, market_status, data_state)
    waiting = _waiting_conditions(counts, market_status)
    risks = _major_risks(counts, market_status, data_state, confidence, items)
    executable = int(counts.get("executable", 0))
    practice_long = int(counts.get("practice_long", 0))
    triggered = int(counts.get("triggered", 0))
    b_plus = int(counts.get("grade_b_plus", 0))

    if not items and not triggers:
        executable_summary = "目前沒有符合條件的候選股。"
    elif executable == 0 and b_plus > 0:
        executable_summary = (
            f"目前沒有 A 級可執行標的，但有 {b_plus} 檔 B+ 練習觀察，正在等待 VWAP、量能或突破條件確認。"
        )
    elif executable == 0:
        executable_summary = (
            f"今日沒有強烈買多標的；目前有 {triggered} 檔 triggered、{practice_long} 檔練習買多，"
            "僅供觀察、虛擬交易與樣本累積。"
        )
    else:
        executable_summary = (
            f"目前有 {executable} 檔進場雷達通過、{triggered} 檔 triggered、"
            f"{practice_long} 檔練習買多、{b_plus} 檔 B+ 練習觀察；等待量能 {counts.get('wait_volume', 0)} 檔、"
            f"等待 VWAP {counts.get('wait_vwap', 0)} 檔、等待突破 {counts.get('wait_breakout', 0)} 檔。"
        )

    no_trade_reason = ""
    if executable == 0:
        no_trade_reason = "今日沒有強烈買多標的。主要原因是量能、VWAP、突破或風險條件尚未完整確認。此狀態下不建議為了交易而交易。"
        if practice_long > 0:
            no_trade_reason += " practice_long 僅供虛擬交易與樣本累積，不是正式可執行訊號。"
        if b_plus > 0:
            no_trade_reason += " 可將 B+ 作為虛擬交易練習觀察，但不代表正式做多建議。"

    if data_state["status"] == "failed":
        action = "目前資料不足，系統僅能提供有限判斷。建議先確認資料更新狀態，不要依不完整資料追價。"
    elif tendency == "積極觀察":
        action = "目前已有可執行訊號，建議先檢查 VWAP、停損距離與風控模式，再進行虛擬交易測試。"
    elif tendency == "等待確認":
        action = "今日先等待條件觸發。可觀察 B+ 是否站回 VWAP、量比放大或突破觸發價，再用虛擬交易累積紀錄。"
    elif tendency == "避免追價":
        action = "今日追價風險偏高，建議等待回測 VWAP 不破或風險降溫，不建議直接追高。"
    elif tendency == "不適合交易":
        action = "目前大盤或資料狀態不利，今日不適合主動交易，宜保留觀察與回測紀錄。"
    else:
        action = "今日不建議為了交易而交易。可先觀察等待條件是否觸發，或使用手動虛擬交易練習紀錄判斷。"

    summary_text = _summary_text(tendency, market_session, market_status, data_state, confidence, stats)
    signal_center = build_signal_center(items, triggers)

    return {
        "version": DECISION_CENTER_VERSION,
        "market": market,
        "market_session": market_session,
        "market_status": market_status or "unknown",
        "operation_tendency": tendency,
        "summary_text": summary_text,
        "executable_summary": executable_summary,
        "main_waiting_conditions": waiting,
        "main_waiting_summary": "目前主要等待條件是" + "、".join(waiting) + "。" if waiting else "目前沒有明顯等待條件。",
        "major_risks": risks,
        "major_risk_summary": "主要風險為" + "、".join(risks) + "。" if risks else "目前沒有明顯集中風險。",
        "action_suggestion": action,
        "no_trade_reason": no_trade_reason,
        "counts": counts,
        "paper_stats": stats,
        "data_state": data_state,
        "confidence_summary": confidence,
        "signal_center": signal_center,
        "disclaimer": DISCLAIMER,
    }


def build_signal_center(candidates: Iterable[Any], b_plus_triggers: Iterable[dict] = ()) -> dict:
    trigger_map = {
        (_string(item.get("market") or "TW"), _string(item.get("symbol"))): item
        for item in b_plus_triggers or []
    }
    buckets = {"executable": [], "practice_long": [], "b_plus": [], "waiting": [], "risk": []}
    for raw in candidates or []:
        item = _signal_card(raw, trigger_map)
        grade = item["grade"]
        entry = item["entry_status"]
        lifecycle = item["lifecycle_status"]
        if entry == "executable":
            buckets["executable"].append(item)
        elif entry == "practice_long":
            buckets["practice_long"].append(item)
        elif grade == "B+":
            buckets["b_plus"].append(item)
        elif entry in {"high_risk", "avoid"} or grade in {"C", "D"}:
            buckets["risk"].append(item)
        elif entry.startswith("wait_"):
            buckets["waiting"].append(item)
        else:
            buckets["waiting"].append(item)
    return buckets


def build_paper_decision_summary(payload: dict) -> dict:
    positions = list(payload.get("positions") or [])
    trades = list(payload.get("trades") or [])
    b_plus = list(payload.get("b_plus_triggers") or [])
    performance = payload.get("performance") or {}
    generated_at = str(payload.get("generated_at") or "")
    today = generated_at[:10]
    today_closed = [
        item for item in trades
        if str(item.get("exit_time") or item.get("entry_time") or item.get("created_at") or "").startswith(today)
        and item.get("status") != "skipped"
    ]
    today_realized = round(sum(float(item.get("realized_pnl") or 0) for item in today_closed), 2)
    manual_trades = int(performance.get("manual_trades") or sum(1 for item in trades if item.get("source") == "manual"))
    system_trades = int(performance.get("system_trades") or sum(1 for item in trades if (item.get("source") or "system") == "system"))
    waiting = sum(1 for item in b_plus if item.get("lifecycle_status") == "observed")
    triggered = sum(1 for item in b_plus if item.get("lifecycle_status") == "triggered")
    open_positions = len(positions)
    practice_available = waiting + triggered > 0

    if open_positions:
        text = f"目前有 {open_positions} 筆持倉，請留意停損、停利與風控模式；今日已實現損益為 {today_realized:+.2f}。"
    elif triggered:
        text = f"目前無持倉，有 {triggered} 檔 B+ 已觸發。你可以等待系統交易建立，或使用手動虛擬交易進行練習。"
    elif waiting:
        text = f"目前無持倉，有 {waiting} 檔 B+ 等待觸發。你可以等待盤中訊號，或使用手動虛擬交易進行練習。"
    else:
        text = "目前無持倉，也沒有等待觸發的 B+ 練習訊號。可以等待系統訊號，或用手動虛擬交易做小額流程測試。"

    return {
        "version": DECISION_CENTER_VERSION,
        "summary_text": text,
        "manual_trades": manual_trades,
        "system_trades": system_trades,
        "open_positions": open_positions,
        "today_realized_pnl": today_realized,
        "b_plus_waiting": waiting,
        "b_plus_triggered": triggered,
        "practice_available": practice_available,
        "disclaimer": DISCLAIMER,
    }


def paper_activity_stats(conn: sqlite3.Connection, market: Optional[str] = None) -> dict:
    where = ""
    params: list[str] = []
    if market:
        where = "WHERE market = ?"
        params.append(market)
    open_positions = conn.execute(
        f"SELECT COUNT(*) AS total FROM paper_positions {where}",
        params,
    ).fetchone()["total"]
    trade_where = where
    manual = conn.execute(
        f"SELECT COUNT(*) AS total FROM paper_trades {trade_where} {'AND' if trade_where else 'WHERE'} source = 'manual'",
        params,
    ).fetchone()["total"]
    system = conn.execute(
        f"SELECT COUNT(*) AS total FROM paper_trades {trade_where} {'AND' if trade_where else 'WHERE'} COALESCE(source, 'system') = 'system'",
        params,
    ).fetchone()["total"]
    return {
        "paper_open_positions": int(open_positions or 0),
        "manual_trades": int(manual or 0),
        "system_trades": int(system or 0),
    }


def _counts(items: list[Any], checklist: dict, triggers: list[dict]) -> dict:
    def status_count(status: str) -> int:
        return sum(1 for item in items if _string(_get(item, "entry_status")) == status)

    counts = {
        "candidate_count": len(items),
        "grade_a": _int(checklist.get("grade_a"), sum(1 for item in items if _get(item, "grade") == "A")),
        "grade_b_plus": _int(checklist.get("grade_b_plus"), sum(1 for item in items if _get(item, "grade") == "B+")),
        "grade_b": _int(checklist.get("grade_b"), sum(1 for item in items if _get(item, "grade") == "B")),
        "executable": _int(checklist.get("executable"), status_count("executable")),
        "practice_long": _int(checklist.get("practice_long"), status_count("practice_long")),
        "wait_volume": _int(checklist.get("wait_volume"), status_count("wait_volume")),
        "wait_vwap": _int(checklist.get("wait_vwap"), status_count("wait_vwap")),
        "wait_breakout": _int(checklist.get("wait_breakout"), status_count("wait_breakout")),
        "wait_pullback": _int(checklist.get("wait_pullback"), status_count("wait_pullback")),
        "high_risk": _int(checklist.get("high_risk"), status_count("high_risk")),
        "avoid": _int(checklist.get("avoid"), status_count("avoid")),
        "triggered": _int(checklist.get("triggered"), sum(1 for item in triggers if item.get("lifecycle_status") == "triggered")),
        "observed": _int(checklist.get("observed"), sum(1 for item in triggers if item.get("lifecycle_status") == "observed")),
        "b_plus_ready": sum(1 for item in triggers if item.get("trigger_readiness") == "ready"),
        "b_plus_near": sum(1 for item in triggers if item.get("trigger_readiness") == "near"),
    }
    return counts


def _confidence_counts(items: list[Any], checklist: dict) -> dict:
    levels = {"high": 0, "medium": 0, "low": 0, "unreliable": 0}
    conflicts_total = 0
    conflict_messages: dict[str, int] = {}
    for item in items:
        level = _string(_get(item, "confidence_level"))
        if level in levels:
            levels[level] += 1
        conflicts_total += _int(_get(item, "conflicts_count"), 0)
        message = _string(_get(item, "conflict_summary"))
        if message and message != "無明顯衝突":
            conflict_messages[message] = conflict_messages.get(message, 0) + 1
    top_conflict = max(conflict_messages.items(), key=lambda pair: pair[1])[0] if conflict_messages else "無明顯衝突"
    return {
        "high": _int(checklist.get("confidence_high"), levels["high"]),
        "medium": _int(checklist.get("confidence_medium"), levels["medium"]),
        "low": _int(checklist.get("confidence_low"), levels["low"]),
        "unreliable": _int(checklist.get("confidence_unreliable"), levels["unreliable"]),
        "conflicts_total": _int(checklist.get("conflicts_total"), conflicts_total),
        "top_conflict": checklist.get("top_conflict") or top_conflict,
    }


def _data_state(data_source_status: Any, checklist: dict) -> dict:
    if isinstance(data_source_status, dict):
        ok = bool(data_source_status.get("ok", True))
        failed = data_source_status.get("failed_symbols") or []
        status = "ok" if ok and not failed else "partial"
        if not ok and failed:
            status = "failed"
        return {
            "status": status,
            "message": "資料來源正常" if status == "ok" else "資料來源不完整，部分標的已排除或只能有限判斷",
            "missing_count": len(failed),
        }
    missing = _int(checklist.get("data_missing"), 0)
    if missing > 0:
        return {"status": "partial", "message": "資料來源不完整，缺漏資料已排除在評分外", "missing_count": missing}
    text = _string(data_source_status)
    if "fail" in text.lower() or "失敗" in text:
        return {"status": "failed", "message": "資料來源失敗，系統僅能提供有限判斷", "missing_count": 1}
    return {"status": "ok", "message": "資料來源正常", "missing_count": 0}


def _operation_tendency(counts: dict, market_status: str, data_state: dict) -> str:
    bearish = _is_bearish(market_status)
    if data_state["status"] == "failed":
        return "保守觀望"
    if bearish and int(counts.get("executable", 0)) == 0:
        return "不適合交易"
    if bearish:
        return "保守觀望"
    if int(counts.get("high_risk", 0)) >= max(3, int(counts.get("executable", 0)) + int(counts.get("grade_b_plus", 0))):
        return "避免追價"
    if int(counts.get("executable", 0)) > 0:
        return "積極觀察"
    if int(counts.get("grade_b_plus", 0)) > 0:
        return "等待確認"
    return "保守觀望"


def _waiting_conditions(counts: dict, market_status: str) -> list[str]:
    values = [
        ("等量能放大", int(counts.get("wait_volume", 0))),
        ("等站回 VWAP", int(counts.get("wait_vwap", 0))),
        ("等突破觸發價", int(counts.get("wait_breakout", 0))),
        ("等回測 VWAP 不破", int(counts.get("wait_pullback", 0))),
        ("等大盤轉強", 1 if _is_bearish(market_status) else 0),
    ]
    return [label for label, count in sorted(values, key=lambda item: item[1], reverse=True) if count > 0][:3]


def _major_risks(counts: dict, market_status: str, data_state: dict, confidence: dict, items: list[Any]) -> list[str]:
    risks: list[str] = []
    if int(counts.get("wait_volume", 0)):
        risks.append("量能不足")
    if int(counts.get("wait_vwap", 0)) or any(not bool(_get(item, "above_vwap")) for item in items):
        risks.append("跌破或尚未站上 VWAP")
    if int(counts.get("high_risk", 0)):
        risks.append("風險分數偏高")
    if any(float(_get(item, "risk_score") or 0) >= 60 for item in items):
        risks.append("追價風險偏高")
    if _is_bearish(market_status):
        risks.append("大盤偏弱")
    if data_state["status"] != "ok":
        risks.append("資料來源缺漏")
    if int(confidence.get("low", 0)) or int(confidence.get("unreliable", 0)):
        risks.append("信心不足標的偏多")
    top_conflict = _string(confidence.get("top_conflict"))
    if top_conflict and top_conflict != "無明顯衝突":
        risks.append(top_conflict)
    return _unique(risks)[:5]


def _summary_text(tendency: str, session: str, market_status: str, data_state: dict, confidence: dict, stats: dict) -> str:
    source_text = data_state.get("message", "資料來源正常")
    confidence_text = (
        f"高信心 {confidence.get('high', 0)}、中等信心 {confidence.get('medium', 0)}、"
        f"低信心 {confidence.get('low', 0)}、不可信 {confidence.get('unreliable', 0)}。"
    )
    paper_text = (
        f"虛擬交易目前 open positions {stats.get('paper_open_positions', 0)}，"
        f"manual trades {stats.get('manual_trades', 0)}，system trades {stats.get('system_trades', 0)}。"
    )
    return (
        f"今日操作傾向為「{tendency}」。目前 session 為 {session or 'unknown'}，"
        f"市場狀態為 {market_status or 'unknown'}；{source_text}。{confidence_text}{paper_text}"
    )


def _signal_card(raw: Any, trigger_map: dict[tuple[str, str], dict]) -> dict:
    market = _string(_get(raw, "market")) or ("US" if _get(raw, "name_en") else "TW")
    symbol = _string(_get(raw, "symbol"))
    trigger = trigger_map.get((market, symbol), {})
    entry = _string(_get(raw, "entry_status"))
    lifecycle = _string(trigger.get("lifecycle_status") or _get(raw, "lifecycle_status")) or "observed"
    grade = _string(_get(raw, "grade")) or "-"
    reason = _first_text(_get(raw, "confidence_summary"), _get(raw, "reasons"), _get(raw, "risk_reasons"))
    next_step = _next_step(entry, lifecycle, trigger.get("trigger_readiness"))
    return {
        "market": market,
        "symbol": symbol,
        "name_zh": _string(_get(raw, "name_zh") or _get(raw, "short_name_zh") or _get(raw, "name")),
        "name_en": _string(_get(raw, "name_en")),
        "grade": grade,
        "entry_status": entry,
        "lifecycle_status": lifecycle,
        "trigger_readiness": _string(trigger.get("trigger_readiness") or "-"),
        "current_price": _number(_get(raw, "latest_price"), _get(raw, "last_price"), trigger.get("current_price")),
        "vwap": _number(_get(raw, "vwap"), trigger.get("vwap")),
        "volume_ratio": _number(_get(raw, "volume_ratio"), trigger.get("volume_ratio")),
        "stop_loss": _number(_get(raw, "stop_loss"), trigger.get("stop_loss")),
        "target_price": _number(_get(raw, "target_price"), trigger.get("target_price")),
        "confidence_level": _string(_get(raw, "confidence_level_label") or _get(raw, "confidence_level")),
        "trade_bias": _string(_get(raw, "trade_bias") or "watch"),
        "trade_bias_label": _string(_get(raw, "trade_bias_label") or "觀察"),
        "trade_bias_reason": _string(_get(raw, "trade_bias_reason")),
        "reason": reason,
        "next_step": next_step,
    }


def _next_step(entry: str, lifecycle: str, readiness: Any) -> str:
    if entry == "executable" or lifecycle == "triggered":
        return "可進入虛擬交易觀察"
    if entry == "practice_long":
        return "可作為練習買多，使用虛擬交易累積樣本"
    if readiness == "ready":
        return "等待系統轉 triggered 或可手動練習"
    if entry == "wait_vwap":
        return "等待站回 VWAP"
    if entry == "wait_volume":
        return "等待量比放大"
    if entry == "wait_breakout":
        return "等待突破觸發價"
    if entry == "high_risk":
        return "避免追價"
    if entry == "avoid":
        return "暫不追蹤"
    if entry == "wait_pullback":
        return "等待回測 VWAP 不破"
    return "持續觀察"


def _get(raw: Any, key: str, default: Any = None) -> Any:
    if isinstance(raw, dict):
        return raw.get(key, default)
    return getattr(raw, key, default)


def _string(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def _int(value: Any, default: int = 0) -> int:
    try:
        if value is None or value == "":
            return int(default)
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def _number(*values: Any) -> Optional[float]:
    for value in values:
        try:
            if value is not None and value != "":
                return round(float(value), 4)
        except (TypeError, ValueError):
            continue
    return None


def _first_text(*values: Any) -> str:
    for value in values:
        if isinstance(value, list) and value:
            return "；".join(str(item) for item in value[:2])
        if value:
            return str(value)
    return "目前沒有明確理由。"


def _unique(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        if value and value not in result:
            result.append(value)
    return result


def _is_bearish(value: str) -> bool:
    text = _string(value).lower()
    return "偏空" in text or "bearish" in text


def today_text() -> str:
    return datetime.now(ZoneInfo("Asia/Taipei")).strftime("%Y-%m-%d")
