from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

from stock_daytrade_system.config import WatchSymbol, load_config
from stock_daytrade_system.data import Bar
from stock_daytrade_system.data_freshness import evaluate_data_freshness
from stock_daytrade_system.db import (
    connect,
    default_db_path,
    last_known_price_row,
    latest_symbol_score,
    recent_tw_orderbook_snapshots,
    save_tw_orderbook_snapshot,
    upsert_last_known_price,
)
from stock_daytrade_system.entry_confirmation import build_entry_confirmation
from stock_daytrade_system.entry_radar_summary import build_entry_radar_summary
from stock_daytrade_system.breakout_trap_diagnosis import build_breakout_trap_diagnosis
from stock_daytrade_system.frontend_language import front_decision_card, front_trade_view
from stock_daytrade_system.fugle_market_data import FugleMarketDataClient
from stock_daytrade_system.intraday import analyze_opening_confirmation
from stock_daytrade_system.long_model import build_long_candidates
from stock_daytrade_system.market_data_provider import get_market_data_provider_manager
from stock_daytrade_system.market_clock import taiwan_market_session
from stock_daytrade_system.market_context import build_market_indicators
from stock_daytrade_system.market_mode import evaluate_tw_market_mode
from stock_daytrade_system.scoring import score_market_bias
from stock_daytrade_system.session_policy import time_bucket_for_market
from stock_daytrade_system.signal_guard import SIGNAL_GUARD_VERSION, evaluate_signal_guard
from stock_daytrade_system.official_institutional import fetch_official_institutional_contexts
from stock_daytrade_system.position_management import position_action_for_symbol
from stock_daytrade_system.precision_context import build_precision_context
from stock_daytrade_system.tw_advisor_analysis import build_tw_advisor_analysis
from stock_daytrade_system.tw_realtime_quote import TwRealtimeQuoteClient
from stock_daytrade_system.tw_momentum_scanner import (
    scan_single_symbol,
    watch_symbol_for,
)


def scan_tw_symbol_payload(
    project_root: Path,
    raw_symbol: str,
    now: Optional[datetime] = None,
    *,
    prefer_snapshot: bool = True,
) -> dict:
    config = load_config(project_root / "config" / "watchlist.json")
    item = watch_symbol_for(raw_symbol, config.symbols)
    if not item.symbol:
        return {"ok": False, "message": "股票代號不可空白", "symbol": raw_symbol}

    captured_at = now or datetime.now(ZoneInfo(config.market.timezone))
    if prefer_snapshot:
        snapshot_payload = _snapshot_tw_symbol_payload(project_root, item, captured_at)
        if snapshot_payload:
            return snapshot_payload

    provider = get_market_data_provider_manager()
    provider_status = provider.status_payload()
    symbols = [item.symbol, config.market.benchmark, config.market.taiwan_futures]
    daily_data, daily_errors = provider.fetch_many_daily_with_errors(symbols, range_="6mo")
    intraday_data, intraday_errors = provider.fetch_many_intraday_with_errors([item.symbol], range_="1d", interval="5m")
    quote_intraday_data, quote_intraday_errors = provider.fetch_many_intraday_with_errors(
        [item.symbol], range_="1d", interval="1m"
    )
    market_bias = score_market_bias(daily_data, config.market.benchmark, config.market.taiwan_futures)
    official_institutional = fetch_official_institutional_contexts(project_root, now=captured_at)
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
        official_institutional.contexts,
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
    fugle_client = FugleMarketDataClient()
    fugle_quote = fugle_client.fetch_quote(item.symbol)
    fugle_trades = fugle_client.fetch_trades(item.symbol)
    fugle_candles = fugle_client.fetch_candles(item.symbol, timeframe="1")
    realtime_quote = TwRealtimeQuoteClient().fetch(item.symbol)
    realtime_payload = realtime_quote.to_dict()
    fugle_quote_payload = fugle_quote.to_dict()
    scan_payload = _scan_payload_with_last_known(scan_item.to_dict(), last_known_row)
    fugle_payload = fugle_trades.to_dict()
    fugle_candles_payload = fugle_candles.to_dict()
    effective_intraday_bars = _bars_from_fugle_candles(fugle_candles_payload) or (
        quote_intraday_data.get(item.symbol) or intraday_data.get(item.symbol, [])
    )
    display_payload = _display_payload(scan_payload, model, realtime_payload, last_known_row, fugle_quote_payload)
    data_health = _data_health_payload(
        captured_at,
        display_payload,
        daily_errors,
        intraday_errors,
        quote_intraday_errors,
        item.symbol,
        realtime_payload,
        fugle_payload,
        fugle_quote_payload,
        fugle_candles_payload,
    )
    candidate_payload = _candidate_payload(model)
    analysis_payload = build_tw_advisor_analysis(
        scan=scan_payload,
        candidate=candidate_payload,
        display=display_payload,
        market_status=market_bias.direction,
    ).to_dict()
    chart_payload = _intraday_chart_payload(effective_intraday_bars, analysis_payload)
    precision_payload = build_precision_context(
        candidate=candidate_payload,
        intraday_bars=effective_intraday_bars,
        data_health=data_health,
    ).to_dict()
    radar_quote_payload = _radar_quote_payload(
        _fugle_quote_as_realtime(fugle_quote_payload, realtime_payload),
        {},
        fugle_payload,
    )
    entry_confirmation_payload = build_entry_confirmation(
        candidate=candidate_payload,
        intraday_bars=effective_intraday_bars,
        data_health=data_health,
        realtime_quote=radar_quote_payload,
        orderbook_history=[],
    ).to_dict()
    with connect(db_path) as conn:
        save_tw_orderbook_snapshot(
            conn,
            market="TW",
            symbol=item.symbol,
            captured_at=captured_at,
            quote=radar_quote_payload,
        )
        orderbook_history = recent_tw_orderbook_snapshots(conn, market="TW", symbol=item.symbol, limit=5)
        entry_confirmation_payload = build_entry_confirmation(
            candidate=candidate_payload,
            intraday_bars=effective_intraday_bars,
            data_health=data_health,
            realtime_quote=radar_quote_payload,
            orderbook_history=orderbook_history,
        ).to_dict()
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
    market_mode_payload = _advisor_market_mode_payload(captured_at, data_health)
    market_mode_name = str(market_mode_payload.get("mode") or "closed_review")
    advisor_intraday = bool(market_mode_payload.get("allow_intraday_signal"))
    stale_for_mode = bool(market_mode_name == "stale_data" or (advisor_intraday and data_health.get("is_stale")))
    data_current_for_mode = bool(market_mode_payload.get("is_data_current_for_mode", data_health.get("is_today_data")))
    safety_payload = _safety_payload(
        candidate_payload,
        scan_payload,
        data_health,
        analysis_payload,
        captured_at,
        market_mode_payload=market_mode_payload,
    )
    front_trade_payload = front_trade_view(
        candidate_payload or scan_payload,
        data_today=data_current_for_mode,
        intraday=advisor_intraday,
        stale=stale_for_mode,
        data_missing=bool(data_health.get("is_data_missing")),
        allow_strong_long=bool(data_health.get("can_show_strong_long", True)),
        market_mode=market_mode_name,
        price_status_label=str(data_health.get("price_status") or data_health.get("quote_state") or ""),
        uses_last_known=bool(data_health.get("uses_last_known") or data_health.get("uses_cache")),
        is_delayed=bool(data_health.get("is_delayed")),
    ).to_dict()
    entry_radar_summary = build_entry_radar_summary(
        candidate=candidate_payload or scan_payload,
        data_health=data_health,
        entry_confirmation=entry_confirmation_payload,
        safety=safety_payload,
        market_mode=market_mode_name,
        intraday=advisor_intraday,
    ).to_dict()
    decision_card_payload = front_decision_card(
        candidate_payload or scan_payload,
        front_view=front_trade_payload,
        entry_radar=entry_radar_summary,
        data_today=data_current_for_mode,
        intraday=advisor_intraday,
        stale=stale_for_mode,
        data_missing=bool(data_health.get("is_data_missing")),
        allow_strong_long=bool(data_health.get("can_show_strong_long", True)),
        market_mode=market_mode_name,
        price_status_label=str(data_health.get("price_status") or data_health.get("quote_state") or ""),
        uses_last_known=bool(data_health.get("uses_last_known") or data_health.get("uses_cache")),
        is_delayed=bool(data_health.get("is_delayed")),
    ).to_dict()
    breakout_trap_diagnosis = build_breakout_trap_diagnosis(
        candidate=candidate_payload or scan_payload,
        intraday_bars=effective_intraday_bars,
        entry_confirmation=entry_confirmation_payload,
        data_health=data_health,
        market_mode=market_mode_name,
        intraday=advisor_intraday,
    ).to_dict()
    key_metrics_payload = _key_metrics_payload(candidate_payload, scan_payload, display_payload, analysis_payload)
    limit_up_playbook_payload = _limit_up_playbook_payload(
        key_metrics_payload,
        candidate_payload,
        scan_payload,
        data_health,
        entry_confirmation_payload,
        safety_payload,
    )
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
        "fugle_quote": fugle_quote_payload,
        "fugle_trades": fugle_payload,
        "fugle_candles": fugle_candles_payload,
        "realtime_quote": realtime_payload,
        "display": display_payload,
        "data_health": data_health,
        "market_mode": market_mode_payload,
        "safety": safety_payload,
        "front_trade": front_trade_payload,
        "decision_card": decision_card_payload,
        "position_action": position_payload,
        "key_metrics": key_metrics_payload,
        "limit_up_playbook": limit_up_playbook_payload,
        "reason_groups": reason_payload,
        "historical_validation": history_payload,
        "source_ranking": source_payload,
        "advisor_analysis": analysis_payload,
        "precision_context": precision_payload,
        "entry_confirmation": entry_confirmation_payload,
        "entry_radar_summary": entry_radar_summary,
        "breakout_trap_diagnosis": breakout_trap_diagnosis,
        "intraday_chart": chart_payload,
        "errors": errors,
        "warnings": warnings,
        "data_source": f"Fugle Quote/Trades/Candles MVP + TWSE MIS fallback + {provider_status.get('active_provider', 'yahoo')} market data provider",
        "provider_status": provider_status,
        "official_institutional": {
            "version": official_institutional.version,
            "symbols_count": official_institutional.symbols_count,
            "records_count": official_institutional.records_count,
            "latest_dates": official_institutional.latest_dates,
            "source_status": official_institutional.source_status,
        },
        "db_path": str(db_path),
    }


def _snapshot_tw_symbol_payload(project_root: Path, item: WatchSymbol, captured_at: datetime) -> dict:
    db_path = default_db_path(project_root)
    with connect(db_path) as conn:
        score_row = latest_symbol_score(conn, item.symbol)
        snapshot_row = conn.execute(
            """
            SELECT *
            FROM tw_full_market_snapshots
            WHERE symbol = ?
            ORDER BY date DESC, captured_at DESC
            LIMIT 1
            """,
            (item.symbol,),
        ).fetchone()
        last_known_row = last_known_price_row(conn, "TW", item.symbol)
        if score_row is None and snapshot_row is None and last_known_row is None:
            return {}

        score = dict(score_row) if score_row else {}
        snapshot = dict(snapshot_row) if snapshot_row else {}
        last_known = dict(last_known_row) if last_known_row else {}
        quote_time = (
            snapshot.get("captured_at")
            or score.get("captured_at")
            or last_known.get("last_known_price_at")
            or last_known.get("last_success_at")
            or captured_at.isoformat(timespec="seconds")
        )
        current_price = _num(snapshot.get("price"), score.get("close"), last_known.get("last_known_price"))
        vwap = _num(snapshot.get("vwap"), score.get("vwap"), last_known.get("last_known_vwap"))
        volume_ratio = _num(
            snapshot.get("volume_ratio"),
            score.get("intraday_volume_ratio"),
            score.get("volume_ratio"),
            last_known.get("last_known_volume_ratio"),
        )
        above_vwap = bool(snapshot.get("above_vwap")) if snapshot.get("above_vwap") is not None else bool(score.get("above_vwap"))
        entry_status = str(snapshot.get("entry_status") or "data_missing")
        grade = str(snapshot.get("ai_grade") or score.get("grade") or "data_missing")
        scan_payload = {
            "symbol": item.symbol,
            "name": snapshot.get("name") or score.get("name") or item.name,
            "latest_price": current_price,
            "latest_at": quote_time,
            "change_pct": _num(snapshot.get("change_pct"), score.get("change_pct")),
            "volume": _num(snapshot.get("volume"), score.get("volume")),
            "turnover": _num(snapshot.get("turnover"), score.get("turnover")),
            "volume_ratio": volume_ratio,
            "vwap": vwap,
            "above_vwap": above_vwap,
            "break_prev_high": bool(snapshot.get("break_prev_high") or score.get("break_prev_high")),
            "break_5d_high": bool(snapshot.get("break_5d_high") or score.get("break_5d_high")),
            "ai_grade": grade,
            "entry_status": entry_status,
            "trade_bias": snapshot.get("trade_bias") or "watch",
            "not_selected_reason": snapshot.get("not_selected_reason") or "",
            "reason_code": snapshot.get("reason_code") or "snapshot_fallback",
            "data_status": snapshot.get("data_status") or "snapshot",
            "source_reasons": ["使用最新模型快照快速回覆，未重新抓取即時行情。"],
            "risk_reasons": [snapshot.get("not_selected_reason")] if snapshot.get("not_selected_reason") else [],
        }
        display_payload = {
            "current_price": current_price,
            "change_pct": scan_payload["change_pct"],
            "price_source": snapshot.get("source_scope") or last_known.get("last_known_source") or "latest_db_snapshot",
            "quote_time": quote_time,
            "model_reference_price": score.get("close"),
            "scanner_latest_price": current_price,
            "fallback_source": "latest_db_snapshot",
            "fallback_reason": "advisor_snapshot_fast_path",
        }
        data_health = _snapshot_data_health(captured_at, display_payload, item.symbol, has_core=bool(current_price and vwap and volume_ratio))
        candidate_payload = _candidate_payload_from_snapshot(item, score, snapshot, scan_payload)
        market_bias_status = "未知"
        analysis_payload = build_tw_advisor_analysis(
            scan=scan_payload,
            candidate=candidate_payload,
            display=display_payload,
            market_status=market_bias_status,
        ).to_dict()
        market_mode_payload = _advisor_market_mode_payload(captured_at, data_health)
        market_mode_name = str(market_mode_payload.get("mode") or "closed_review")
        advisor_intraday = bool(market_mode_payload.get("allow_intraday_signal"))
        stale_for_mode = bool(market_mode_name == "stale_data" or (advisor_intraday and data_health.get("is_stale")))
        data_current_for_mode = bool(market_mode_payload.get("is_data_current_for_mode", data_health.get("is_today_data")))
        safety_payload = _safety_payload(
            candidate_payload,
            scan_payload,
            data_health,
            analysis_payload,
            captured_at,
            market_mode_payload=market_mode_payload,
        )
        entry_confirmation_payload = build_entry_confirmation(
            candidate=candidate_payload or scan_payload,
            intraday_bars=[],
            data_health=data_health,
            realtime_quote={},
            orderbook_history=[],
        ).to_dict()
        entry_radar_summary = build_entry_radar_summary(
            candidate=candidate_payload or scan_payload,
            data_health=data_health,
            entry_confirmation=entry_confirmation_payload,
            safety=safety_payload,
            market_mode=market_mode_name,
            intraday=advisor_intraday,
        ).to_dict()
        front_trade_payload = front_trade_view(
            candidate_payload or scan_payload,
            data_today=data_current_for_mode,
            intraday=advisor_intraday,
            stale=stale_for_mode,
            data_missing=bool(data_health.get("is_data_missing")),
            allow_strong_long=False,
            market_mode=market_mode_name,
            price_status_label=str(data_health.get("price_status") or data_health.get("quote_state") or ""),
            uses_last_known=True,
            is_delayed=True,
        ).to_dict()
        decision_card_payload = front_decision_card(
            candidate_payload or scan_payload,
            front_view=front_trade_payload,
            entry_radar=entry_radar_summary,
            data_today=data_current_for_mode,
            intraday=advisor_intraday,
            stale=stale_for_mode,
            data_missing=bool(data_health.get("is_data_missing")),
            allow_strong_long=False,
            market_mode=market_mode_name,
            price_status_label=str(data_health.get("price_status") or data_health.get("quote_state") or ""),
            uses_last_known=True,
            is_delayed=True,
        ).to_dict()
        breakout_trap_diagnosis = build_breakout_trap_diagnosis(
            candidate=candidate_payload or scan_payload,
            intraday_bars=[],
            entry_confirmation=entry_confirmation_payload,
            data_health=data_health,
            market_mode=market_mode_name,
            intraday=advisor_intraday,
        ).to_dict()
        precision_payload = build_precision_context(
            candidate=candidate_payload,
            intraday_bars=[],
            data_health=data_health,
        ).to_dict()
        key_metrics_payload = _key_metrics_payload(candidate_payload, scan_payload, display_payload, analysis_payload)
        limit_up_playbook_payload = _limit_up_playbook_payload(
            key_metrics_payload,
            candidate_payload,
            scan_payload,
            data_health,
            entry_confirmation_payload,
            safety_payload,
        )
        reason_payload = _reason_payload(candidate_payload, scan_payload, data_health, safety_payload)
        history_payload = _historical_validation_payload(conn, item.symbol)
        source_payload = _source_ranking_payload(conn, item.symbol)
        position_payload = position_action_for_symbol(conn, item.symbol, market="TW")

    return {
        "ok": bool(score_row or snapshot_row),
        "message": "已使用最新模型快照快速回覆；若需要即時重算，請使用 live scan。",
        "response_mode": "snapshot",
        "generated_at": captured_at.isoformat(timespec="seconds"),
        "symbol": item.symbol,
        "name": scan_payload["name"],
        "sector": item.sector,
        "market_status": market_bias_status,
        "market_notes": ["snapshot_fast_path"],
        "scan": scan_payload,
        "candidate": candidate_payload,
        "fugle_quote": {"status": "skipped", "status_label": "快照模式未呼叫 Fugle Quote"},
        "fugle_trades": {"status": "skipped", "status_label": "快照模式未呼叫 Fugle Trades"},
        "fugle_candles": {"status": "skipped", "status_label": "快照模式未呼叫 Fugle Candles"},
        "realtime_quote": {"status": "skipped", "source": "latest_db_snapshot"},
        "display": display_payload,
        "data_health": data_health,
        "market_mode": market_mode_payload,
        "safety": safety_payload,
        "front_trade": front_trade_payload,
        "decision_card": decision_card_payload,
        "position_action": position_payload,
        "key_metrics": key_metrics_payload,
        "limit_up_playbook": limit_up_playbook_payload,
        "reason_groups": reason_payload,
        "historical_validation": history_payload,
        "source_ranking": source_payload,
        "advisor_analysis": analysis_payload,
        "precision_context": precision_payload,
        "entry_confirmation": entry_confirmation_payload,
        "entry_radar_summary": entry_radar_summary,
        "breakout_trap_diagnosis": breakout_trap_diagnosis,
        "intraday_chart": _intraday_chart_payload([], analysis_payload),
        "errors": {},
        "warnings": {
            "snapshot_mode": "這是最新模型快照，未重新抓取即時行情；不可作為即時強烈買多。"
        },
        "data_source": "latest DB snapshot fast path",
        "provider_status": {"active_provider": "snapshot", "snapshot_fast_path": True},
        "official_institutional": {"source_status": {"snapshot_mode": "skipped"}},
        "db_path": str(db_path),
    }


def _snapshot_data_health(captured_at: datetime, display: dict, symbol: str, *, has_core: bool) -> dict:
    quote_time = str(display.get("quote_time") or "")
    quote_dt = _parse_quote_time(quote_time)
    age_minutes = None
    if quote_dt is not None:
        age_minutes = max((captured_at.astimezone(ZoneInfo("Asia/Taipei")) - quote_dt).total_seconds() / 60, 0.0)
    is_today = bool(quote_dt and quote_dt.date() == captured_at.astimezone(ZoneInfo("Asia/Taipei")).date())
    mode_preview = evaluate_tw_market_mode(
        now=captured_at,
        data_date=quote_dt.date() if quote_dt else None,
        latest_data_at=quote_dt,
        data_stale=False,
        severe_missing=not has_core,
        watchlist_fresh=True,
        positions_fresh=True,
    )
    status = "部分缺漏" if has_core else "異常"
    return {
        "status": status,
        "credibility": "中" if has_core else "資料不足 / 不可信",
        "quote_time": quote_time,
        "generated_at": captured_at.isoformat(timespec="seconds"),
        "age_minutes": round(age_minutes, 1) if age_minutes is not None else None,
        "is_today_data": is_today,
        "is_intraday_data": bool(quote_dt),
        "is_stale": False,
        "price_status": "delayed" if has_core else "missing",
        "quote_state": "delayed" if has_core else "missing",
        "quote_state_label": "使用最新模型快照",
        "quote_state_badge": "使用快照",
        "quote_state_message": "快照模式未重新抓取即時行情，僅供觀察。",
        "is_live": False,
        "is_delayed": has_core,
        "uses_last_known": True,
        "last_known_price": display.get("current_price"),
        "yahoo_daily_success": True,
        "yahoo_intraday_5m_success": True,
        "yahoo_intraday_1m_success": False,
        "twse_tpex_quote_success": False,
        "fugle_enabled": False,
        "fugle_configured": False,
        "fugle_status": "skipped",
        "fugle_status_label": "快照模式未呼叫 Fugle",
        "fugle_quote_status": "skipped",
        "fugle_quote_status_label": "快照模式未呼叫 Fugle Quote",
        "fugle_candles_status": "skipped",
        "fugle_candles_status_label": "快照模式未呼叫 Fugle 1分K",
        "uses_cache": True,
        "is_data_missing": not has_core,
        "market_mode": mode_preview.mode,
        "market_mode_label": mode_preview.label,
        "review_mode_message": mode_preview.review_mode_message,
        "is_data_current_for_mode": mode_preview.is_data_current_for_mode,
        "can_use_for_daytrade": False,
        "can_use_for_intraday_signal": False,
        "can_show_strong_long": False,
        "advice": "使用最新模型快照：可快速閱讀作戰卡，但不可作為即時進場依據。",
        "fallback_source": display.get("fallback_source") or "latest_db_snapshot",
        "fallback_reason": display.get("fallback_reason") or "advisor_snapshot_fast_path",
        "error_reason": "" if has_core else f"{symbol} 缺少快照核心資料",
    }


def _candidate_payload_from_snapshot(item: WatchSymbol, score: dict, snapshot: dict, scan: dict) -> dict:
    reasons = _json_text_list(score.get("reasons")) or list(scan.get("source_reasons") or [])
    risk_reasons = _json_text_list(score.get("risk_reasons")) or list(scan.get("risk_reasons") or [])
    entry_status = str(snapshot.get("entry_status") or "data_missing")
    return {
        "symbol": item.symbol,
        "name": scan.get("name") or item.name,
        "last_price": scan.get("latest_price"),
        "change_pct": scan.get("change_pct"),
        "volume_ratio": scan.get("volume_ratio"),
        "vwap": scan.get("vwap"),
        "above_vwap": bool(scan.get("above_vwap")),
        "previous_high": _num(score.get("previous_high")),
        "high_5d": _num(score.get("high_5d")),
        "high_10d": _num(score.get("high_10d")),
        "break_prev_high": bool(scan.get("break_prev_high")),
        "break_5d_high": bool(scan.get("break_5d_high")),
        "opening_range_high": _num(score.get("opening_range_high")),
        "opening_range_low": _num(score.get("opening_range_low")),
        "upper_shadow_pct": _num(score.get("upper_shadow_pct"), default=0.0) or 0.0,
        "trigger_price": _num(score.get("previous_high"), score.get("opening_range_high")),
        "stop_loss": _num(score.get("stop_loss")),
        "target_price": _num(score.get("target_price")),
        "bullish_score": _num(score.get("bullish_score"), default=0.0) or 0.0,
        "risk_score": _num(score.get("risk_score"), default=0.0) or 0.0,
        "grade": str(snapshot.get("ai_grade") or score.get("grade") or "data_missing"),
        "entry_status": entry_status,
        "trade_bias": str(snapshot.get("trade_bias") or "watch"),
        "trade_bias_label": "觀察" if entry_status != "avoid" else "看空",
        "trade_bias_reason": snapshot.get("not_selected_reason") or "使用最新快照，等待即時資料確認。",
        "confidence_score": _num(score.get("confidence_score"), default=0.0) or 0.0,
        "confidence_level": str(score.get("confidence_level") or ""),
        "not_selected_reason": snapshot.get("not_selected_reason") or "",
        "reasons": reasons,
        "risk_reasons": risk_reasons,
        "confidence_summary": score.get("confidence_summary") or "快照模式，僅供觀察。",
        "conflicts_count": int(_num(score.get("conflicts_count"), default=0) or 0),
        "conflict_summary": score.get("conflict_summary") or "",
        "confidence_level_label": str(score.get("confidence_level") or ""),
        "timeframe_diagnostics": {},
        "trend_diagnosis": {},
        "trend_status": "",
        "trend_label": "",
        "trend_reason_code": "",
        "institutional_context": {},
        "sector_context": {},
        "price_status": "delayed",
        "is_delayed": True,
        "uses_last_known": True,
    }


def _json_text_list(value) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item)]
    if not value:
        return []
    try:
        parsed = json.loads(str(value))
    except json.JSONDecodeError:
        return [str(value)]
    if isinstance(parsed, list):
        return [str(item) for item in parsed if str(item)]
    return [str(parsed)] if parsed else []


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


def _radar_quote_payload(realtime_payload: dict, secondary_tick_payload: Optional[dict] = None, fugle_payload: Optional[dict] = None) -> dict:
    payload = dict(realtime_payload or {})
    fugle_payload = fugle_payload or {}
    secondary_tick_payload = secondary_tick_payload or {}
    for source_payload in (fugle_payload, secondary_tick_payload):
        large_status = str(source_payload.get("large_trade_status") or "")
        if large_status and large_status != "missing":
            payload["large_trade_status"] = large_status
            payload["large_trade_summary"] = source_payload.get("large_trade_summary")
            payload["large_trade_threshold"] = source_payload.get("large_trade_threshold")
            payload["last_tick_volume"] = source_payload.get("large_trade_size") or source_payload.get("last_tick_volume")
            payload["tick_type"] = source_payload.get("tick_type")
            payload["large_trade_source"] = source_payload.get("source")
            break
    return payload


def _fugle_quote_as_realtime(fugle_quote: dict, fallback: Optional[dict] = None) -> dict:
    fallback = dict(fallback or {})
    fugle_quote = fugle_quote or {}
    price = fugle_quote.get("price")
    if price is None:
        return fallback
    payload = dict(fallback)
    payload.update(
        {
            "price": price,
            "previous_close": fugle_quote.get("previous_close") or fallback.get("previous_close"),
            "change_pct": fugle_quote.get("change_pct") if fugle_quote.get("change_pct") is not None else fallback.get("change_pct"),
            "quote_time": fugle_quote.get("quote_time") or fugle_quote.get("last_updated") or fallback.get("quote_time"),
            "source": fugle_quote.get("source") or "Fugle REST Quote",
            "bid_levels": fugle_quote.get("bid_levels") or fallback.get("bid_levels") or [],
            "ask_levels": fugle_quote.get("ask_levels") or fallback.get("ask_levels") or [],
            "bid_total_volume": fugle_quote.get("bid_total_volume"),
            "ask_total_volume": fugle_quote.get("ask_total_volume"),
            "bid_price": fugle_quote.get("bid_price"),
            "bid_volume": fugle_quote.get("bid_volume"),
            "ask_price": fugle_quote.get("ask_price"),
            "ask_volume": fugle_quote.get("ask_volume"),
            "orderbook_imbalance": fugle_quote.get("orderbook_imbalance"),
            "five_level_status": fugle_quote.get("five_level_status") or "missing",
            "five_level_status_label": fugle_quote.get("five_level_status_label") or "Fugle 五檔資料不足",
            "is_limit_up_locked": bool(fugle_quote.get("is_limit_up_bid") or fugle_quote.get("is_limit_up_price")),
            "is_limit_down_locked": bool(fugle_quote.get("is_limit_down_ask") or fugle_quote.get("is_limit_down_price")),
            "is_limit_up_bid": bool(fugle_quote.get("is_limit_up_bid")),
            "is_limit_down_ask": bool(fugle_quote.get("is_limit_down_ask")),
            "last_tick_volume": fugle_quote.get("last_trade_size") or fugle_quote.get("last_size"),
            "tick_type": fugle_quote.get("last_trade_side") or "unknown",
            "last_trade_side": fugle_quote.get("last_trade_side") or "unknown",
            "last_trade_summary": fugle_quote.get("last_trade_summary") or "",
            "total_trade_value": fugle_quote.get("total_trade_value"),
            "total_trade_volume": fugle_quote.get("total_trade_volume"),
            "total_trade_volume_at_bid": fugle_quote.get("total_trade_volume_at_bid"),
            "total_trade_volume_at_ask": fugle_quote.get("total_trade_volume_at_ask"),
            "intraday_flow_ratio": fugle_quote.get("intraday_flow_ratio"),
        }
    )
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
        "timeframe_diagnostics": getattr(candidate, "timeframe_diagnostics", {}) or {},
        "trend_diagnosis": getattr(candidate, "trend_diagnosis", {}) or {},
        "trend_status": getattr(candidate, "trend_status", ""),
        "trend_label": getattr(candidate, "trend_label", ""),
        "trend_reason_code": getattr(candidate, "trend_reason_code", ""),
        "institutional_context": getattr(candidate, "institutional_context", {}) or {},
        "sector_context": getattr(candidate, "sector_context", {}) or {},
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


def _display_payload(scan_item, candidate, realtime_quote: dict, last_known_row=None, fugle_quote: Optional[dict] = None) -> dict:
    scan_data = dict(scan_item or {})
    candidate_data = _candidate_payload(candidate) or {}
    fugle_quote = fugle_quote or {}
    current_price = (
        fugle_quote.get("price")
        if fugle_quote.get("price") is not None
        else realtime_quote.get("price")
    )
    change_pct = fugle_quote.get("change_pct") if fugle_quote.get("change_pct") is not None else realtime_quote.get("change_pct")
    source = (
        fugle_quote.get("source")
        if fugle_quote.get("price") is not None
        else realtime_quote.get("source")
        if realtime_quote.get("price") is not None
        else "Yahoo Finance intraday chart"
    )
    quote_time = fugle_quote.get("quote_time") or fugle_quote.get("last_updated") or realtime_quote.get("quote_time") or scan_data.get("latest_at") or ""
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
    realtime_quote: Optional[dict] = None,
    fugle_trades: Optional[dict] = None,
    fugle_quote: Optional[dict] = None,
    fugle_candles: Optional[dict] = None,
) -> dict:
    realtime_quote = realtime_quote or {}
    fugle_trades = fugle_trades or {}
    fugle_quote = fugle_quote or {}
    fugle_candles = fugle_candles or {}
    quote_time = str(display.get("quote_time") or "")
    quote_dt = _parse_quote_time(quote_time)
    mode_preview = evaluate_tw_market_mode(
        now=captured_at,
        data_date=quote_dt.date() if quote_dt else None,
        latest_data_at=quote_dt,
        data_stale=False,
        severe_missing=False,
        watchlist_fresh=True,
        positions_fresh=True,
    )
    mode_name = mode_preview.mode
    review_mode_current = (
        quote_dt is not None
        and mode_name in {"pre_open_prepare", "closed_review", "post_close_review"}
        and mode_preview.is_data_current_for_mode
    )
    age_minutes = None
    if quote_dt is not None:
        age_minutes = max((captured_at.astimezone(ZoneInfo("Asia/Taipei")) - quote_dt).total_seconds() / 60, 0.0)
    is_today = bool(quote_dt and quote_dt.date() == captured_at.astimezone(ZoneInfo("Asia/Taipei")).date())
    stale = bool((mode_name == "intraday" and age_minutes is not None and age_minutes > 15) or mode_name == "stale_data")
    has_error = symbol in daily_errors or symbol in intraday_errors
    freshness = evaluate_data_freshness(
        now=captured_at,
        latest_at=quote_dt,
        source_failed=has_error,
        partial=bool(symbol in quote_intraday_errors),
        is_market_open=mode_name == "intraday",
    )
    if has_error and freshness.uses_last_known:
        status = "部分缺漏"
        credibility = "中"
        advice = "使用上一筆有效價格：目前 API 暫時失敗，該判斷不可作為即時交易依據"
    elif has_error:
        status = "異常"
        credibility = "資料不足 / 不可信"
        advice = "資料不足：缺少核心行情資料，不能產生做多判斷"
    elif review_mode_current:
        status = "正常"
        credibility = "中"
        advice = mode_preview.review_mode_message
    elif stale or (mode_name == "intraday" and quote_dt is not None and not is_today):
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
        "twse_mis_five_level_status": realtime_quote.get("five_level_status") or "missing",
        "twse_mis_five_level_status_label": realtime_quote.get("five_level_status_label") or "五檔資料不足",
        "twse_mis_orderbook_imbalance": realtime_quote.get("orderbook_imbalance"),
        "twse_mis_bid_total_volume": realtime_quote.get("bid_total_volume"),
        "twse_mis_ask_total_volume": realtime_quote.get("ask_total_volume"),
        "twse_mis_is_limit_up_locked": bool(realtime_quote.get("is_limit_up_locked")),
        "twse_mis_is_limit_down_locked": bool(realtime_quote.get("is_limit_down_locked")),
        "fugle_enabled": bool(fugle_trades.get("enabled")),
        "fugle_configured": bool(fugle_trades.get("configured")),
        "fugle_status": fugle_trades.get("status") or "disabled",
        "fugle_status_label": fugle_trades.get("status_label") or "尚未啟用",
        "fugle_trades_success": bool(fugle_trades.get("trades_count")),
        "fugle_trades_count": fugle_trades.get("trades_count") or 0,
        "fugle_large_trade_status": fugle_trades.get("large_trade_status") or "missing",
        "fugle_large_trade_summary": fugle_trades.get("large_trade_summary") or "",
        "fugle_quote_status": fugle_quote.get("status") or "disabled",
        "fugle_quote_status_label": fugle_quote.get("status_label") or "尚未啟用",
        "fugle_quote_success": bool(fugle_quote.get("price") is not None),
        "fugle_quote_last_updated": fugle_quote.get("last_updated") or fugle_quote.get("quote_time") or "",
        "fugle_quote_five_level_status": fugle_quote.get("five_level_status") or "missing",
        "fugle_quote_five_level_status_label": fugle_quote.get("five_level_status_label") or "五檔資料不足",
        "fugle_candles_status": fugle_candles.get("status") or "disabled",
        "fugle_candles_status_label": fugle_candles.get("status_label") or "尚未啟用",
        "fugle_candles_success": bool(fugle_candles.get("candles_count")),
        "fugle_candles_count": fugle_candles.get("candles_count") or 0,
        "uses_cache": freshness.uses_last_known,
        "is_data_missing": has_error,
        "market_mode": mode_preview.mode,
        "market_mode_label": mode_preview.label,
        "review_mode_message": mode_preview.review_mode_message,
        "is_data_current_for_mode": mode_preview.is_data_current_for_mode,
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


def _advisor_market_mode_payload(captured_at: datetime, data_health: dict) -> dict:
    quote_time = str(data_health.get("quote_time") or "")
    session = taiwan_market_session(captured_at).session
    status = str(data_health.get("status") or "")
    severe_missing = status in {"異常", "嚴重缺漏"} or (
        bool(data_health.get("is_data_missing")) and not quote_time
    )
    data_stale = bool(data_health.get("is_stale")) if session == "regular" else False
    payload = evaluate_tw_market_mode(
        now=captured_at,
        data_date=quote_time[:10] if quote_time else None,
        latest_data_at=quote_time or None,
        data_stale=data_stale,
        severe_missing=severe_missing,
        watchlist_fresh=True,
        positions_fresh=True,
    ).to_dict()
    bucket = time_bucket_for_market(captured_at, "TW")
    payload["time_bucket"] = bucket
    payload["time_bucket_label"] = _time_bucket_label(bucket)
    payload["time_bucket_guidance"] = _time_bucket_guidance(bucket)
    return payload


def _time_bucket_label(bucket: str) -> str:
    return {
        "pre_open": "開盤前準備",
        "opening_observation": "開盤觀察 09:00-09:20",
        "main_entry": "主進場區 09:20-10:30",
        "pullback_only": "回測觀察 10:30-11:30",
        "late_avoid": "尾盤避免追價",
        "after_close": "盤後復盤",
    }.get(bucket, bucket or "-")


def _time_bucket_guidance(bucket: str) -> str:
    return {
        "pre_open": "只整理觀察名單，尚未有今日 VWAP、量比與突破確認。",
        "opening_observation": "先看量價與開盤區間，不急著進場；等 VWAP、量比、突破與買盤延續確認。",
        "main_entry": "可檢查強烈買多與買多標的，但仍需進場雷達、停損距離與資料 live。",
        "pullback_only": "不追伸，優先等待回測 VWAP 不破或風險下降。",
        "late_avoid": "避免新追價，只管理既有持倉或觀察明日續強。",
        "after_close": "只做復盤與下個交易日觀察，不作即時進場判斷。",
    }.get(bucket, "依目前市場模式判斷，先確認資料是否 live。")


def _safety_payload(
    candidate: Optional[dict],
    scan: dict,
    data_health: dict,
    analysis: dict,
    captured_at: datetime,
    market_mode_payload: Optional[dict] = None,
) -> dict:
    candidate = candidate or {}
    entry_status = str(candidate.get("entry_status") or scan.get("entry_status") or "data_missing")
    grade = str(candidate.get("grade") or scan.get("ai_grade") or "data_missing")
    clock = taiwan_market_session(captured_at)
    mode_payload = market_mode_payload or _advisor_market_mode_payload(captured_at, data_health)
    market_mode = str(mode_payload.get("mode") or ("intraday" if clock.session == "regular" else "closed_review"))
    intraday_allowed = bool(mode_payload.get("allow_intraday_signal", clock.session == "regular"))
    data_current_for_mode = bool(mode_payload.get("is_data_current_for_mode", data_health.get("is_today_data")))
    stale_for_mode = bool(market_mode == "stale_data" or (intraday_allowed and data_health.get("is_stale")))
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
        data_today=data_current_for_mode,
        intraday=intraday_allowed,
        stale=stale_for_mode,
        data_missing=bool(data_health.get("is_data_missing")),
        allow_strong_long=bool(data_health.get("can_show_strong_long", True)),
        market_mode=market_mode,
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
        "market_mode": market_mode,
        "market_mode_label": mode_payload.get("label") or "",
        "market_mode_message": mode_payload.get("message") or "",
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


def _limit_up_playbook_payload(
    key_metrics: dict,
    candidate: Optional[dict],
    scan: dict,
    data_health: dict,
    entry_confirmation: dict,
    safety: dict,
) -> dict:
    candidate = candidate or {}
    scan = scan or {}
    change_pct = _num(key_metrics.get("change_pct"), scan.get("change_pct"), candidate.get("change_pct"), default=0.0) or 0.0
    near_limit = bool(key_metrics.get("near_limit_up")) or change_pct >= 9
    orderbook_status = str(entry_confirmation.get("orderbook_status") or "")
    locked = bool(
        scan.get("is_limit_up_locked")
        or candidate.get("is_limit_up_locked")
        or orderbook_status == "limit_up_locked"
        or entry_confirmation.get("is_limit_up_locked")
    )
    if not near_limit and not locked:
        return {
            "visible": False,
            "status": "not_near_limit",
            "label": "未接近漲停",
            "summary": "目前不是接近漲停型態，回到 VWAP、量比、突破與風控判斷。",
            "now_action": "回到一般進場雷達與風控檢查。",
            "wait_for": "等待 VWAP、量比、突破與停損距離條件完整。",
            "avoid": "不要因為個股頁沒有漲停警示就忽略一般風控。",
            "does_not_change_model": True,
        }

    current_price = _num(key_metrics.get("current_price"), candidate.get("last_price"), scan.get("latest_price"))
    stop_loss = _num(key_metrics.get("stop_loss"), candidate.get("stop_loss"))
    stop_distance_pct = None
    if current_price and stop_loss and current_price > stop_loss:
        stop_distance_pct = round((current_price - stop_loss) / current_price * 100, 2)
    risk_score = _num(key_metrics.get("risk_score"), candidate.get("risk_score"), scan.get("risk_score"), default=999.0) or 999.0
    volume_ratio = _num(key_metrics.get("volume_ratio"), candidate.get("volume_ratio"), scan.get("volume_ratio"))
    above_vwap = bool(scan.get("above_vwap") or candidate.get("above_vwap"))
    data_live = bool(data_health.get("is_live") and data_health.get("can_use_for_intraday_signal"))
    entry_status = str(safety.get("effective_entry_status") or candidate.get("entry_status") or scan.get("entry_status") or "")
    confirmation_quality = str(entry_confirmation.get("confirmation_quality") or "")
    large_trade_status = str(entry_confirmation.get("large_trade_status") or "")
    warning_parts: list[str] = []
    evidence: list[str] = []

    if locked:
        evidence.append("五檔顯示漲停鎖住或買盤堆積。")
    if above_vwap:
        evidence.append("股價仍站上 VWAP。")
    if volume_ratio is not None and volume_ratio >= 1:
        evidence.append(f"量比 {volume_ratio:.2f}x，短線資金有進場跡象。")
    if large_trade_status in {"buy_sweep", "large_buy", "inflow"}:
        evidence.append("逐筆顯示疑似大單敲進。")

    if not data_live:
        warning_parts.append("資料非 live，只能觀察。")
    if entry_status in {"high_risk", "avoid", "data_missing"}:
        warning_parts.append("模型狀態不是可進場型態。")
    if not above_vwap:
        warning_parts.append("尚未站上 VWAP。")
    if stop_distance_pct is None:
        warning_parts.append("缺停損距離，不能估算風險。")
    elif stop_distance_pct > 3:
        warning_parts.append(f"停損距離 {stop_distance_pct:.2f}% 偏大。")
    if risk_score > 55:
        warning_parts.append(f"風險分數 {risk_score:.0f} 偏高。")
    if confirmation_quality in {"weak", "missing"}:
        warning_parts.append("進場雷達確認不足。")
    if large_trade_status in {"sell_sweep", "large_sell", "outflow"}:
        warning_parts.append("逐筆疑似大單敲出。")

    if warning_parts:
        status = "limit_chase_risk"
        label = "漲停追價風險"
        summary = "強勢但不代表可以追；" + "；".join(warning_parts[:3])
        now_action = "放進觀察，不直接追漲停。"
        wait_for = "等待拉回 VWAP 附近不破、停損距離縮小，或進場雷達轉強後再評估。"
        avoid = "不要因為快漲停就改成買多；不要把 high_risk 當成進場。"
    elif locked:
        status = "limit_locked_watch"
        label = "漲停鎖住觀察"
        summary = "買盤堆積且結構尚可，但漲停附近仍不適合直接追價。"
        now_action = "只盯盤與記錄，不追價。"
        wait_for = "若打開後回測不破 VWAP、買盤仍承接，再用進場雷達重新確認。"
        avoid = "不要在漲停鎖住時用市價追。"
    else:
        status = "near_limit_watch"
        label = "接近漲停觀察"
        summary = "動能很強，但仍要等 VWAP、量能、停損距離與盤口確認。"
        now_action = "先確認是否為真強延續，不提前追高。"
        wait_for = "等突破後不回落、或拉回 VWAP 不破且量能維持。"
        avoid = "不要買在距離 VWAP 過遠的位置。"

    return {
        "visible": True,
        "status": status,
        "label": label,
        "summary": summary,
        "now_action": now_action,
        "wait_for": wait_for,
        "avoid": avoid,
        "evidence": _dedupe(evidence),
        "warnings": _dedupe(warning_parts),
        "change_pct": round(change_pct, 2),
        "stop_distance_pct": stop_distance_pct,
        "risk_score": round(risk_score, 2) if risk_score != 999.0 else None,
        "volume_ratio": volume_ratio,
        "above_vwap": above_vwap,
        "data_live": data_live,
        "is_limit_up_locked": locked,
        "does_not_change_model": True,
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


def _bars_from_fugle_candles(payload: Optional[dict]) -> list[Bar]:
    rows = (payload or {}).get("candles")
    if not isinstance(rows, list):
        return []
    bars: list[Bar] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        timestamp = _parse_quote_time(str(row.get("timestamp") or ""))
        open_price = _num(row.get("open"))
        high_price = _num(row.get("high"))
        low_price = _num(row.get("low"))
        close_price = _num(row.get("close"))
        volume = _num(row.get("volume"), default=0.0)
        if timestamp is None or None in {open_price, high_price, low_price, close_price}:
            continue
        bars.append(
            Bar(
                timestamp=timestamp,
                open=float(open_price),
                high=float(high_price),
                low=float(low_price),
                close=float(close_price),
                volume=float(volume or 0.0),
            )
        )
    return bars


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
