from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

from stock_daytrade_system.config import WatchSymbol, load_config
from stock_daytrade_system.data_freshness import evaluate_data_freshness
from stock_daytrade_system.db import connect, default_db_path, last_known_price_row, upsert_last_known_price
from stock_daytrade_system.frontend_language import front_trade_view
from stock_daytrade_system.intraday import analyze_opening_confirmation
from stock_daytrade_system.long_model import build_long_candidates
from stock_daytrade_system.market_data_provider import get_market_data_provider_manager
from stock_daytrade_system.market_clock import taiwan_market_session
from stock_daytrade_system.market_context import build_market_indicators
from stock_daytrade_system.scoring import score_market_bias
from stock_daytrade_system.signal_guard import SIGNAL_GUARD_VERSION, evaluate_signal_guard
from stock_daytrade_system.position_management import position_action_for_symbol
from stock_daytrade_system.tw_advisor_analysis import build_tw_advisor_analysis
from stock_daytrade_system.tw_realtime_quote import TwRealtimeQuoteClient
from stock_daytrade_system.tw_momentum_scanner import (
    scan_single_symbol,
    watch_symbol_for,
)


def scan_tw_symbol_payload(project_root: Path, raw_symbol: str, now: Optional[datetime] = None) -> dict:
    config = load_config(project_root / "config" / "watchlist.json")
    item = watch_symbol_for(raw_symbol, config.symbols)
    if not item.symbol:
        return {"ok": False, "message": "股票代號不可空白", "symbol": raw_symbol}

    provider = get_market_data_provider_manager()
    provider_status = provider.status_payload()
    captured_at = now or datetime.now(ZoneInfo(config.market.timezone))
    symbols = [item.symbol, config.market.benchmark, config.market.taiwan_futures]
    daily_data, daily_errors = provider.fetch_many_daily_with_errors(symbols, range_="6mo")
    intraday_data, intraday_errors = provider.fetch_many_intraday_with_errors([item.symbol], range_="1d", interval="5m")
    quote_intraday_data, quote_intraday_errors = provider.fetch_many_intraday_with_errors(
        [item.symbol], range_="1d", interval="1m"
    )
    market_bias = score_market_bias(daily_data, config.market.benchmark, config.market.taiwan_futures)
    opening = analyze_opening_confirmation(
        item,
        intraday_data.get(item.symbol, []),
        daily_data.get(item.symbol, []),
        opening_bars=3,
    )
    candidates = build_long_candidates(
        [item],
        daily_data,
        intraday_data,
        [opening] if opening else [],
        [],
        market_bias,
        {},
        captured_at=captured_at,
    )
    model = candidates[0] if candidates else None
    scan_item = scan_single_symbol(
        item,
        daily_data.get(item.symbol, []),
        quote_intraday_data.get(item.symbol) or intraday_data.get(item.symbol, []),
        model,
    )
    db_path = default_db_path(project_root)
    with connect(db_path) as conn:
        last_known_row = last_known_price_row(conn, "TW", item.symbol)
    realtime_quote = TwRealtimeQuoteClient().fetch(item.symbol)
    realtime_payload = realtime_quote.to_dict()
    scan_payload = _scan_payload_with_last_known(scan_item.to_dict(), last_known_row)
    display_payload = _display_payload(scan_payload, model, realtime_payload, last_known_row)
    data_health = _data_health_payload(captured_at, display_payload, daily_errors, intraday_errors, quote_intraday_errors, item.symbol)
    candidate_payload = _candidate_payload(model)
    analysis_payload = build_tw_advisor_analysis(
        scan=scan_payload,
        candidate=candidate_payload,
        display=display_payload,
        market_status=market_bias.direction,
    ).to_dict()
    chart_payload = _intraday_chart_payload(
        quote_intraday_data.get(item.symbol) or intraday_data.get(item.symbol, []),
        analysis_payload,
    )
    with connect(db_path) as conn:
        if display_payload.get("current_price") is not None and not data_health.get("uses_last_known"):
            upsert_last_known_price(
                conn,
                market="TW",
                symbol=item.symbol,
                price=display_payload.get("current_price"),
                price_at=display_payload.get("quote_time") or captured_at.isoformat(timespec="seconds"),
                vwap=scan_payload.get("vwap"),
                volume_ratio=scan_payload.get("volume_ratio"),
                source=display_payload.get("price_source") or "tw_advisor",
            )
        history_payload = _historical_validation_payload(conn, item.symbol)
        source_payload = _source_ranking_payload(conn, item.symbol)
        position_payload = position_action_for_symbol(conn, item.symbol, market="TW")
    safety_payload = _safety_payload(
        candidate_payload,
        scan_payload,
        data_health,
        analysis_payload,
        captured_at,
    )
    front_trade_payload = front_trade_view(
        candidate_payload or scan_payload,
        data_today=bool(data_health.get("is_today_data")),
        intraday=bool(data_health.get("is_intraday_data")),
        stale=bool(data_health.get("is_stale")),
    ).to_dict()
    key_metrics_payload = _key_metrics_payload(candidate_payload, scan_payload, display_payload, analysis_payload)
    reason_payload = _reason_payload(candidate_payload, scan_payload, data_health, safety_payload)
    errors = {}
    if item.symbol in daily_errors:
        errors["daily"] = daily_errors[item.symbol]
    if item.symbol in intraday_errors:
        errors["intraday"] = intraday_errors[item.symbol]
    warnings = {}
    if item.symbol in quote_intraday_errors:
        warnings["intraday_1m"] = quote_intraday_errors[item.symbol]
    if realtime_quote.status == "failed":
        warnings["realtime_quote"] = "TWSE MIS 暫時沒有可用成交價，已改用 Yahoo Finance 1 分 K。"
    return {
        "ok": not bool(errors) and model is not None,
        "message": "掃描完成" if model is not None else "資料不足，尚無法進入評分模型",
        "generated_at": captured_at.isoformat(timespec="seconds"),
        "symbol": item.symbol,
        "name": item.name,
        "sector": item.sector,
        "market_status": market_bias.direction,
        "market_notes": list(market_bias.notes),
        "scan": scan_payload,
        "candidate": candidate_payload,
        "realtime_quote": realtime_payload,
        "display": display_payload,
        "data_health": data_health,
        "safety": safety_payload,
        "front_trade": front_trade_payload,
        "position_action": position_payload,
        "key_metrics": key_metrics_payload,
        "reason_groups": reason_payload,
        "historical_validation": history_payload,
        "source_ranking": source_payload,
        "advisor_analysis": analysis_payload,
        "intraday_chart": chart_payload,
        "errors": errors,
        "warnings": warnings,
        "data_source": f"TWSE MIS + {provider_status.get('active_provider', 'yahoo')} market data provider",
        "provider_status": provider_status,
        "db_path": str(db_path),
    }


def add_tw_watchlist_symbol(project_root: Path, raw_symbol: str) -> dict:
    config = load_config(project_root / "config" / "watchlist.json")
    item = watch_symbol_for(raw_symbol, config.symbols)
    if not item.symbol:
        return {"ok": False, "message": "股票代號不可空白", "symbol": raw_symbol}
    data_dir = project_root / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    path = data_dir / "tw_manual_watchlist.json"
    rows = []
    if path.exists():
        try:
            rows = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            rows = []
    existing = {row.get("symbol") for row in rows if isinstance(row, dict)}
    if item.symbol not in existing:
        rows.append({"symbol": item.symbol, "name": item.name, "sector": item.sector})
        path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    payload = scan_tw_symbol_payload(project_root, item.symbol)
    payload["watchlist_added"] = item.symbol not in existing
    payload["watchlist_path"] = str(path)
    return payload


def _candidate_payload(candidate) -> Optional[dict]:
    if candidate is None:
        return None
    return {
        "symbol": candidate.symbol,
        "name": candidate.name,
        "last_price": candidate.last_price,
        "change_pct": candidate.change_pct,
        "volume_ratio": candidate.volume_ratio,
        "vwap": candidate.vwap,
        "above_vwap": candidate.above_vwap,
        "previous_high": candidate.previous_high,
        "high_5d": candidate.high_5d,
        "high_10d": candidate.high_10d,
        "break_prev_high": candidate.break_prev_high,
        "break_5d_high": candidate.break_5d_high,
        "opening_range_high": candidate.opening_range_high,
        "opening_range_low": candidate.opening_range_low,
        "upper_shadow_pct": candidate.upper_shadow_pct,
        "trigger_price": candidate.trigger_price,
        "stop_loss": candidate.stop_loss,
        "target_price": candidate.target_price,
        "bullish_score": candidate.bullish_score,
        "risk_score": candidate.risk_score,
        "grade": candidate.grade,
        "entry_status": candidate.entry_status,
        "trade_bias": getattr(candidate, "trade_bias", "watch"),
        "trade_bias_label": getattr(candidate, "trade_bias_label", "觀察"),
        "trade_bias_reason": getattr(candidate, "trade_bias_reason", ""),
        "confidence_score": candidate.confidence_score,
        "confidence_level": candidate.confidence_level,
        "not_selected_reason": _not_selected_reason(candidate),
        "reasons": candidate.reasons,
        "risk_reasons": candidate.risk_reasons,
        "confidence_summary": candidate.confidence_summary,
        "conflicts_count": getattr(candidate, "conflicts_count", 0),
        "conflict_summary": getattr(candidate, "conflict_summary", ""),
        "confidence_level_label": getattr(candidate, "confidence_level_label", candidate.confidence_level),
    }


def _scan_payload_with_last_known(scan_data: dict, last_known_row) -> dict:
    payload = dict(scan_data or {})
    if not last_known_row:
        return payload
    if payload.get("latest_price") is None:
        payload["latest_price"] = last_known_row["last_known_price"]
        payload["latest_at"] = last_known_row["last_known_price_at"] or last_known_row["last_success_at"] or ""
    if payload.get("vwap") is None:
        payload["vwap"] = last_known_row["last_known_vwap"]
    if payload.get("volume_ratio") is None:
        payload["volume_ratio"] = last_known_row["last_known_volume_ratio"]
    payload["fallback_used"] = bool(payload.get("data_error"))
    payload["fallback_source"] = last_known_row["last_known_source"]
    payload["fallback_reason"] = payload.get("data_error") or last_known_row["fallback_reason"] or ""
    return payload


def _display_payload(scan_item, candidate, realtime_quote: dict, last_known_row=None) -> dict:
    scan_data = dict(scan_item or {})
    candidate_data = _candidate_payload(candidate) or {}
    current_price = realtime_quote.get("price")
    change_pct = realtime_quote.get("change_pct")
    source = realtime_quote.get("source") if current_price is not None else "Yahoo Finance intraday chart"
    quote_time = realtime_quote.get("quote_time") or scan_data.get("latest_at") or ""
    if current_price is None:
        current_price = scan_data.get("latest_price")
    if current_price is None:
        current_price = candidate_data.get("last_price")
    fallback_source = ""
    fallback_reason = ""
    if current_price is None and last_known_row:
        current_price = last_known_row["last_known_price"]
        quote_time = last_known_row["last_known_price_at"] or last_known_row["last_success_at"] or quote_time
        source = last_known_row["last_known_source"] or "last_known_price"
        fallback_source = source
        fallback_reason = last_known_row["fallback_reason"] or "latest_quote_failed"
    if change_pct is None:
        change_pct = scan_data.get("change_pct")
    if change_pct is None:
        change_pct = candidate_data.get("change_pct")
    return {
        "current_price": current_price,
        "change_pct": change_pct,
        "price_source": source,
        "quote_time": quote_time,
        "model_reference_price": candidate_data.get("last_price"),
        "scanner_latest_price": scan_data.get("latest_price"),
        "fallback_source": fallback_source or scan_data.get("fallback_source") or "",
        "fallback_reason": fallback_reason or scan_data.get("fallback_reason") or "",
    }


def _data_health_payload(
    captured_at: datetime,
    display: dict,
    daily_errors: dict,
    intraday_errors: dict,
    quote_intraday_errors: dict,
    symbol: str,
) -> dict:
    quote_time = str(display.get("quote_time") or "")
    quote_dt = _parse_quote_time(quote_time)
    age_minutes = None
    if quote_dt is not None:
        age_minutes = max((captured_at.astimezone(ZoneInfo("Asia/Taipei")) - quote_dt).total_seconds() / 60, 0.0)
    is_today = bool(quote_dt and quote_dt.date() == captured_at.astimezone(ZoneInfo("Asia/Taipei")).date())
    stale = bool(age_minutes is not None and age_minutes > 15)
    has_error = symbol in daily_errors or symbol in intraday_errors
    freshness = evaluate_data_freshness(
        now=captured_at,
        latest_at=quote_dt,
        source_failed=has_error,
        partial=bool(symbol in quote_intraday_errors),
        is_market_open=True,
    )
    if has_error and freshness.uses_last_known:
        status = "部分缺漏"
        credibility = "中"
        advice = "使用上一筆有效價格：目前 API 暫時失敗，該判斷不可作為即時交易依據"
    elif has_error:
        status = "異常"
        credibility = "資料不足 / 不可信"
        advice = "資料不足：缺少核心行情資料，不能產生做多判斷"
    elif stale or (quote_dt is not None and not is_today):
        status = "過期"
        credibility = "低"
        advice = "資料過期，暫停產生當沖建議"
    elif symbol in quote_intraday_errors:
        status = "部分缺漏"
        credibility = "中"
        advice = "TWSE 即時價缺漏，已回退 Yahoo 分 K，僅供觀察"
    else:
        status = "正常"
        credibility = "高"
        advice = "資料狀態可供個股當沖分析"
    return {
        "status": status,
        "credibility": credibility,
        "quote_time": quote_time,
        "generated_at": captured_at.isoformat(timespec="seconds"),
        "age_minutes": round(age_minutes, 1) if age_minutes is not None else None,
        "is_today_data": is_today,
        "is_intraday_data": bool(quote_dt),
        "is_stale": stale,
        "price_status": _price_status_from_freshness(freshness.state),
        "quote_state": freshness.state,
        "quote_state_label": freshness.label,
        "quote_state_badge": freshness.badge,
        "quote_state_message": freshness.message,
        "is_live": freshness.is_live,
        "is_delayed": freshness.is_delayed,
        "uses_last_known": freshness.uses_last_known,
        "last_known_price": display.get("current_price"),
        "yahoo_daily_success": symbol not in daily_errors,
        "yahoo_intraday_5m_success": symbol not in intraday_errors,
        "yahoo_intraday_1m_success": symbol not in quote_intraday_errors,
        "twse_tpex_quote_success": status != "異常",
        "uses_cache": freshness.uses_last_known,
        "is_data_missing": has_error,
        "can_use_for_daytrade": status == "正常" and freshness.state == "live",
        "can_use_for_intraday_signal": status == "正常" and freshness.state == "live",
        "can_show_strong_long": status == "正常" and freshness.state == "live",
        "advice": advice,
        "fallback_source": display.get("fallback_source") or "",
        "fallback_reason": display.get("fallback_reason") or "",
        "error_reason": (
            daily_errors.get(symbol)
            or intraday_errors.get(symbol)
            or quote_intraday_errors.get(symbol)
            or ""
        ),
    }


def _price_status_from_freshness(state: str) -> str:
    if state == "live":
        return "live"
    if state == "delayed":
        return "delayed"
    if state == "last_known":
        return "cached"
    if state == "stale":
        return "delayed"
    return "missing" if state == "missing" else state


def _safety_payload(
    candidate: Optional[dict],
    scan: dict,
    data_health: dict,
    analysis: dict,
    captured_at: datetime,
) -> dict:
    candidate = candidate or {}
    entry_status = str(candidate.get("entry_status") or scan.get("entry_status") or "data_missing")
    grade = str(candidate.get("grade") or scan.get("ai_grade") or "data_missing")
    clock = taiwan_market_session(captured_at)
    vwap = _num(scan.get("vwap"), candidate.get("vwap"))
    volume_ratio = _num(scan.get("volume_ratio"), candidate.get("volume_ratio"))
    stop_loss = _num(candidate.get("stop_loss"), (analysis.get("action_plan") or {}).get("stop_loss"))
    current_price = _num(scan.get("latest_price"), candidate.get("last_price"))
    risk_score = _num(candidate.get("risk_score"), scan.get("risk_score"), default=0.0) or 0.0
    guard_input = {
        "entry_status": entry_status,
        "grade": grade,
        "vwap": vwap,
        "volume_ratio": volume_ratio,
        "stop_loss": stop_loss,
        "last_price": current_price,
        "change_pct": _num(scan.get("change_pct"), candidate.get("change_pct")),
    }
    guard = evaluate_signal_guard(
        guard_input,
        data_today=bool(data_health.get("is_today_data")),
        intraday=bool(data_health.get("is_intraday_data", True)),
        stale=bool(data_health.get("is_stale")),
        data_missing=bool(data_health.get("is_data_missing")),
        allow_strong_long=bool(data_health.get("can_show_strong_long", True)),
        market_mode="intraday" if clock.session == "regular" else "closed",
        market_session=clock.session,
        current_price=current_price,
        change_pct=guard_input["change_pct"],
    )
    blocked = [item.to_dict() for item in guard.blockers]
    effective_entry = guard.effective_entry_status
    effective_grade = guard.effective_grade
    conclusion_state = _conclusion_state(grade, entry_status)
    if blocked and entry_status in {"executable", "practice_long"}:
        conclusion_state = "避開" if effective_entry == "high_risk" else "資料不足"
    elif data_health.get("is_data_missing"):
        conclusion_state = "資料不足"

    reason_codes = list(guard.reason_codes)
    if scan.get("reason_code"):
        reason_codes.append(str(scan.get("reason_code")))
    if candidate.get("not_selected_reason"):
        reason_codes.append(str(candidate.get("not_selected_reason")))
    return {
        "market_session": clock.session,
        "market_status_text": clock.status_text,
        "original_entry_status": entry_status,
        "effective_entry_status": effective_entry,
        "original_grade": grade,
        "effective_grade": effective_grade,
        "conclusion_state": conclusion_state,
        "is_executable_allowed": guard.is_executable_allowed,
        "blocked_reasons": blocked,
        "reason_codes": reason_codes,
        "vwap_distance_pct": guard.vwap_distance_pct,
        "risk_score": round(risk_score, 2),
        "signal_guard_version": SIGNAL_GUARD_VERSION,
    }


def _key_metrics_payload(candidate: Optional[dict], scan: dict, display: dict, analysis: dict) -> dict:
    candidate = candidate or {}
    current_price = _num(display.get("current_price"), scan.get("latest_price"), candidate.get("last_price"))
    vwap = _num(scan.get("vwap"), candidate.get("vwap"))
    stop_loss = _num(candidate.get("stop_loss"), (analysis.get("action_plan") or {}).get("stop_loss"))
    target_price = _num(candidate.get("target_price"), (analysis.get("action_plan") or {}).get("target_price"))
    risk_reward = None
    if current_price and stop_loss and target_price and current_price > stop_loss:
        risk_reward = (target_price - current_price) / (current_price - stop_loss)
    vwap_distance = (current_price - vwap) / vwap * 100 if current_price and vwap else None
    return {
        "current_price": current_price,
        "change_pct": _num(display.get("change_pct"), scan.get("change_pct"), candidate.get("change_pct")),
        "volume": _num(scan.get("volume")),
        "turnover": _num(scan.get("turnover")),
        "volume_ratio": _num(scan.get("volume_ratio"), candidate.get("volume_ratio")),
        "vwap": vwap,
        "distance_to_vwap_pct": round(vwap_distance, 2) if vwap_distance is not None else None,
        "previous_high": _num(candidate.get("previous_high")),
        "break_prev_high": bool(scan.get("break_prev_high") or candidate.get("break_prev_high")),
        "intraday_high": _num((analysis.get("action_plan") or {}).get("trigger_price"), candidate.get("opening_range_high")),
        "near_limit_up": (_num(display.get("change_pct"), scan.get("change_pct"), candidate.get("change_pct"), default=0.0) or 0.0) >= 9,
        "risk_score": _num(candidate.get("risk_score"), scan.get("risk_score")),
        "confidence_level": candidate.get("confidence_level_label") or candidate.get("confidence_level") or scan.get("confidence_level"),
        "stop_loss": stop_loss,
        "target_price": target_price,
        "risk_reward_ratio": round(risk_reward, 2) if risk_reward is not None else None,
    }


def _reason_payload(candidate: Optional[dict], scan: dict, data_health: dict, safety: dict) -> dict:
    candidate = candidate or {}
    long_reasons = list(candidate.get("reasons") or scan.get("source_reasons") or [])
    if scan.get("turnover") and scan.get("turnover") >= 100_000_000:
        long_reasons.append("成交金額足夠")
    risk_reasons = list(candidate.get("risk_reasons") or scan.get("risk_reasons") or [])
    risk_reasons.extend(item["message"] for item in safety.get("blocked_reasons", []))
    waiting = []
    entry = safety.get("effective_entry_status") or candidate.get("entry_status") or scan.get("entry_status")
    if entry == "wait_volume":
        waiting.append("等待量能")
    if entry == "wait_vwap":
        waiting.append("等待站回 VWAP")
    if entry == "wait_breakout":
        waiting.append("等待突破觸發價")
    if entry == "wait_pullback":
        waiting.append("等待拉回")
    if entry == "high_risk":
        waiting.append("風險分數過高或追價風險高")
    if entry in {"avoid", "data_missing"}:
        waiting.append("資料不足或條件不適合")
    if not data_health.get("can_use_for_daytrade"):
        waiting.append(data_health.get("advice") or "資料狀態不足")
    return {
        "long_reasons": _dedupe(long_reasons),
        "risk_reasons": _dedupe(risk_reasons),
        "not_executable_reasons": _dedupe(waiting),
    }


def _historical_validation_payload(conn: sqlite3.Connection, symbol: str) -> dict:
    rows = conn.execute(
        """
        SELECT *
        FROM tw_full_market_snapshots
        WHERE symbol = ?
        ORDER BY date DESC, captured_at DESC
        """,
        (symbol,),
    ).fetchall()
    latest_by_date: dict[str, sqlite3.Row] = {}
    for row in rows:
        latest_by_date.setdefault(row["date"], row)
    ordered = list(latest_by_date.values())
    windows = {str(window): _history_window(ordered[:window]) for window in (20, 40, 60)}
    sample_size = len([row for row in ordered[:60] if row["verified_at"]])
    return {
        "sample_size": sample_size,
        "min_sample_size": 20,
        "is_statistically_meaningful": sample_size >= 20,
        "message": "這檔歷史樣本不足，不建議依個股勝率判斷。" if sample_size < 20 else "這檔已有初步歷史樣本，可搭配整體策略成績單觀察。",
        "windows": windows,
    }


def _history_window(rows: list[sqlite3.Row]) -> dict:
    verified = [row for row in rows if row["verified_at"]]
    a_rows = [row for row in rows if row["ai_grade"] == "A"]
    bp_rows = [row for row in rows if row["ai_grade"] == "B+"]
    high_risk = [row for row in verified if row["entry_status"] == "high_risk"]
    avoid = [row for row in verified if row["entry_status"] == "avoid"]
    return {
        "sample_size": len(verified),
        "grade_a_count": len(a_rows),
        "grade_a_win_rate": _win_rate([row for row in verified if row["ai_grade"] == "A"]),
        "grade_b_plus_count": len(bp_rows),
        "grade_b_plus_triggered_count": 0,
        "grade_b_plus_triggered_win_rate": None,
        "high_risk_continue_up_rate": _rate(high_risk, lambda row: (_num(row["max_gain_after_scan"], default=0.0) or 0.0) >= 1),
        "avoid_big_up_rate": _rate(avoid, lambda row: (_num(row["max_gain_after_scan"], default=0.0) or 0.0) >= 1),
        "avg_max_gain_pct": _avg([row["max_gain_after_scan"] for row in verified]),
        "avg_max_drawdown_pct": _avg([row["max_drawdown_after_scan"] for row in verified]),
        "take_profit_rate": _rate(verified, lambda row: bool(row["hit_take_profit"])),
        "stop_loss_rate": _rate(verified, lambda row: bool(row["hit_stop_loss"])),
    }


def _source_ranking_payload(conn: sqlite3.Connection, symbol: str) -> dict:
    row = conn.execute(
        """
        SELECT *
        FROM tw_full_market_snapshots
        WHERE symbol = ?
        ORDER BY date DESC, captured_at DESC
        LIMIT 1
        """,
        (symbol,),
    ).fetchone()
    if row is None:
        return {
            "source_scope": "manual_scan",
            "from_watchlist": False,
            "from_full_market": False,
            "out_of_pool": False,
            "entered_candidate_pool": False,
            "today_rank": None,
            "reason_code": "not_in_snapshot",
            "message": "目前沒有全市場快照紀錄，這次為手動即時查詢。",
        }
    peers = conn.execute(
        """
        SELECT symbol
        FROM tw_full_market_snapshots
        WHERE date = ?
          AND captured_at = ?
          AND COALESCE(entered_candidate_pool, 0) = 1
        ORDER BY COALESCE(turnover, 0) DESC, COALESCE(change_pct, 0) DESC
        """,
        (row["date"], row["captured_at"]),
    ).fetchall()
    rank = next((index + 1 for index, peer in enumerate(peers) if peer["symbol"] == symbol), None)
    source_scope = row["source_scope"] or ""
    return {
        "source_scope": source_scope or "-",
        "from_watchlist": source_scope == "watchlist",
        "from_full_market": source_scope in {"full_market", "out_of_pool"},
        "out_of_pool": source_scope == "out_of_pool",
        "entered_candidate_pool": bool(row["entered_candidate_pool"]),
        "entered_ai_candidates": bool(row["entered_ai_candidates"]),
        "today_rank": rank,
        "ai_grade": row["ai_grade"],
        "entry_status": row["entry_status"],
        "reason_code": row["reason_code"] or row["not_selected_reason"] or "-",
        "not_selected_reason": row["not_selected_reason"] or "-",
        "date": row["date"],
        "captured_at": row["captured_at"],
    }


def _conclusion_state(grade: str, entry_status: str) -> str:
    if grade == "data_missing" or entry_status == "data_missing":
        return "資料不足"
    if entry_status in {"executable", "practice_long"}:
        return "可執行"
    if grade in {"A", "B+"}:
        return "觀察"
    if str(entry_status).startswith("wait_") or grade == "B":
        return "等待"
    if entry_status in {"high_risk", "avoid"} or grade in {"C", "D"}:
        return "避開"
    return "觀察"


def _dedupe(items: list) -> list[str]:
    result = []
    seen = set()
    for item in items:
        text = str(item or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _num(*values, default=None):
    for value in values:
        try:
            if value is None or value == "":
                continue
            return float(value)
        except (TypeError, ValueError):
            continue
    return default


def _avg(values) -> Optional[float]:
    nums = [_num(value) for value in values]
    nums = [value for value in nums if value is not None]
    if not nums:
        return None
    return round(sum(nums) / len(nums), 2)


def _rate(rows, predicate) -> Optional[float]:
    rows = list(rows or [])
    if not rows:
        return None
    return round(sum(1 for row in rows if predicate(row)) / len(rows) * 100, 2)


def _win_rate(rows: list[sqlite3.Row]) -> Optional[float]:
    return _rate(rows, lambda row: (_num(row["max_gain_after_scan"], default=0.0) or 0.0) >= 1)


def _not_selected_reason(candidate) -> str:
    if candidate.grade in {"A", "B+", "B"}:
        return "已進入正式候選"
    if candidate.entry_status == "high_risk":
        return "強勢但追價風險高，不列為 A，可列入觀察。"
    if not candidate.above_vwap:
        return "未站上 VWAP"
    if candidate.volume_ratio < 0.8:
        return "量比不足"
    if candidate.vwap and (candidate.last_price - candidate.vwap) / candidate.vwap * 100 > 3:
        return "距離 VWAP 太遠"
    if candidate.risk_score > 55:
        return "risk_score 過高"
    if candidate.confidence_score < 55:
        return "confidence_score 不足"
    if candidate.entry_status == "avoid":
        return "已列為 avoid"
    return "條件未達 A/B+/B"


def _parse_quote_time(value: str) -> Optional[datetime]:
    if not value:
        return None
    text = value.strip()
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ZoneInfo("Asia/Taipei"))
    return parsed.astimezone(ZoneInfo("Asia/Taipei"))


def _intraday_chart_payload(bars, analysis_payload: dict) -> dict:
    recent = list(bars or [])[-120:]
    serialized = []
    total_volume = 0.0
    weighted_value = 0.0
    prices = []
    for bar in recent:
        typical = (bar.high + bar.low + bar.close) / 3
        if bar.volume > 0:
            weighted_value += typical * bar.volume
            total_volume += bar.volume
        vwap = weighted_value / total_volume if total_volume else None
        row = {
            "time": _chart_time(bar.timestamp),
            "open": round(bar.open, 2),
            "high": round(bar.high, 2),
            "low": round(bar.low, 2),
            "close": round(bar.close, 2),
            "volume": round(bar.volume, 0),
            "vwap": round(vwap, 2) if vwap is not None else None,
        }
        serialized.append(row)
        prices.extend([bar.high, bar.low, bar.close])
        if vwap is not None:
            prices.append(vwap)
    levels = list(analysis_payload.get("key_levels") or [])
    action_plan = analysis_payload.get("action_plan") or {}
    for label, key, note in (
        ("進場參考", "entry_reference", "作戰計畫"),
        ("停損價", "stop_loss", "作戰計畫"),
        ("停利價", "target_price", "作戰計畫"),
    ):
        value = action_plan.get(key)
        if value is not None:
            levels.append({"label": label, "value": value, "note": note})
            prices.append(float(value))
    return {
        "bars": serialized,
        "levels": levels,
        "price_min": round(min(prices), 2) if prices else None,
        "price_max": round(max(prices), 2) if prices else None,
    }


def _chart_time(value: datetime) -> str:
    if value.tzinfo is not None:
        return value.astimezone(ZoneInfo("Asia/Taipei")).strftime("%H:%M")
    if value.hour < 8:
        return value.replace(tzinfo=ZoneInfo("UTC")).astimezone(ZoneInfo("Asia/Taipei")).strftime("%H:%M")
    return value.strftime("%H:%M")
