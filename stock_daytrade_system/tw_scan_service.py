from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

from stock_daytrade_system.config import WatchSymbol, load_config
from stock_daytrade_system.data import YahooChartClient
from stock_daytrade_system.db import default_db_path
from stock_daytrade_system.intraday import analyze_opening_confirmation
from stock_daytrade_system.long_model import build_long_candidates
from stock_daytrade_system.market_context import build_market_indicators
from stock_daytrade_system.scoring import score_market_bias
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

    client = YahooChartClient()
    captured_at = now or datetime.now(ZoneInfo(config.market.timezone))
    symbols = [item.symbol, config.market.benchmark, config.market.taiwan_futures]
    daily_data, daily_errors = client.fetch_many_daily_with_errors(symbols, range_="6mo")
    intraday_data, intraday_errors = client.fetch_many_intraday_with_errors([item.symbol], range_="1d", interval="5m")
    quote_intraday_data, quote_intraday_errors = client.fetch_many_intraday_with_errors(
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
    realtime_quote = TwRealtimeQuoteClient().fetch(item.symbol)
    realtime_payload = realtime_quote.to_dict()
    display_payload = _display_payload(scan_item, model, realtime_payload)
    candidate_payload = _candidate_payload(model)
    analysis_payload = build_tw_advisor_analysis(
        scan=scan_item.to_dict(),
        candidate=candidate_payload,
        display=display_payload,
        market_status=market_bias.direction,
    ).to_dict()
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
        "scan": scan_item.to_dict(),
        "candidate": candidate_payload,
        "realtime_quote": realtime_payload,
        "display": display_payload,
        "advisor_analysis": analysis_payload,
        "errors": errors,
        "warnings": warnings,
        "data_source": "TWSE MIS + Yahoo Finance 1m/5m chart endpoint",
        "db_path": str(default_db_path(project_root)),
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
    }


def _display_payload(scan_item, candidate, realtime_quote: dict) -> dict:
    scan_data = scan_item.to_dict() if scan_item else {}
    candidate_data = _candidate_payload(candidate) or {}
    current_price = realtime_quote.get("price")
    change_pct = realtime_quote.get("change_pct")
    source = realtime_quote.get("source") if current_price is not None else "Yahoo Finance intraday chart"
    quote_time = realtime_quote.get("quote_time") or scan_data.get("latest_at") or ""
    if current_price is None:
        current_price = scan_data.get("latest_price")
    if current_price is None:
        current_price = candidate_data.get("last_price")
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
    }


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
