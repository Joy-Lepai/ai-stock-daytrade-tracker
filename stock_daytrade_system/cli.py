from __future__ import annotations

import argparse
import os
from datetime import datetime
from pathlib import Path
from typing import List
from zoneinfo import ZoneInfo

from stock_daytrade_system.cmoney import CMoneyClient, CMoneyDataError, merge_cmoney_symbols, rankings_by_symbol
from stock_daytrade_system.config import load_config
from stock_daytrade_system.data import YahooChartClient
from stock_daytrade_system.db import backtest_summary, connect, default_db_path, save_long_candidates, update_backtests
from stock_daytrade_system.intraday import OpeningSignal, analyze_opening_confirmation
from stock_daytrade_system.long_model import build_long_candidates, build_long_model_summary
from stock_daytrade_system.market_context import build_market_indicators
from stock_daytrade_system.paper_trading import update_paper_trades
from stock_daytrade_system.performance import record_signal_performance
from stock_daytrade_system.report import render_opening_report, render_report
from stock_daytrade_system.scoring import CandidateScore, score_market_bias, score_symbol
from stock_daytrade_system.sectors import rank_opening_sector_strength, rank_sector_strength
from stock_daytrade_system.taifex import TaifexClient, TaifexDataError
from stock_daytrade_system.tracker import build_tracked_symbols, render_tracker_html
from stock_daytrade_system.web import DEFAULT_AUTH, serve


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = PROJECT_ROOT / "config" / "watchlist.json"
DEFAULT_REPORT_DIR = PROJECT_ROOT / "reports"


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate premarket day-trading research reports.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    report_parser = subparsers.add_parser("report", help="Generate a premarket report.")
    report_parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    report_parser.add_argument("--output-dir", type=Path, default=DEFAULT_REPORT_DIR)
    report_parser.add_argument("--range", default="6mo", help="Yahoo chart range, e.g. 3mo, 6mo, 1y.")

    open_parser = subparsers.add_parser("open-check", help="Generate an opening-range confirmation report.")
    open_parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    open_parser.add_argument("--output-dir", type=Path, default=DEFAULT_REPORT_DIR)
    open_parser.add_argument("--daily-range", default="3mo")
    open_parser.add_argument("--intraday-range", default="1d")
    open_parser.add_argument("--interval", default="5m")
    open_parser.add_argument("--opening-bars", type=int, default=3)

    tracker_parser = subparsers.add_parser("tracker", help="Generate a unified HTML tracking dashboard.")
    tracker_parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    tracker_parser.add_argument("--output-dir", type=Path, default=DEFAULT_REPORT_DIR)
    tracker_parser.add_argument("--daily-range", default="6mo")
    tracker_parser.add_argument("--intraday-range", default="1d")
    tracker_parser.add_argument("--interval", default="5m")
    tracker_parser.add_argument("--opening-bars", type=int, default=3)

    web_parser = subparsers.add_parser("web", help="Run the web dashboard.")
    web_parser.add_argument("--host", default=os.getenv("STOCK_WEB_HOST", "127.0.0.1"))
    web_parser.add_argument("--port", type=int, default=int(os.getenv("PORT", "8000")))
    web_parser.add_argument("--auth", type=Path, default=DEFAULT_AUTH)
    web_parser.add_argument("--report-dir", type=Path, default=DEFAULT_REPORT_DIR)
    web_parser.add_argument("--require-auth", action="store_true", help="Require username/password login.")

    args = parser.parse_args()
    if args.command == "report":
        return run_report(args.config, args.output_dir, args.range)
    if args.command == "open-check":
        return run_open_check(
            args.config,
            args.output_dir,
            args.daily_range,
            args.intraday_range,
            args.interval,
            args.opening_bars,
        )
    if args.command == "tracker":
        return run_tracker(
            args.config,
            args.output_dir,
            args.daily_range,
            args.intraday_range,
            args.interval,
            args.opening_bars,
        )
    if args.command == "web":
        serve(args.host, args.port, args.auth, args.report_dir, require_auth=args.require_auth)
        return 0
    return 1


def run_report(config_path: Path, output_dir: Path, range_: str) -> int:
    config = load_config(config_path)
    client = YahooChartClient()
    market_symbols = list(
        dict.fromkeys(
            config.market.us_market_symbols
            + [config.market.benchmark, config.market.taiwan_futures]
        )
    )
    watch_symbols = [item.symbol for item in config.symbols]
    all_symbols = list(dict.fromkeys(market_symbols + watch_symbols))

    print(f"Fetching {len(all_symbols)} symbols...")
    data, errors = client.fetch_many_daily_with_errors(all_symbols, range_=range_)
    for symbol, error in errors.items():
        print(f"Warning: {symbol}: {error}")
    market_bias = score_market_bias(data, config.market.benchmark, config.market.taiwan_futures)
    market_notes = list(market_bias.notes)
    taifex_errors = {}
    try:
        tx_quote = TaifexClient().fetch_latest_future_quote("TX")
        market_notes.append(tx_quote.summary())
        if tx_quote.change_pct is not None:
            adjusted_score = market_bias.score + tx_quote.change_pct * 0.8
            direction = "偏多" if adjusted_score >= 2 else "偏空" if adjusted_score <= -2 else "中性"
            market_bias = type(market_bias)(
                score=round(adjusted_score, 2),
                direction=direction,
                notes=market_notes,
            )
    except TaifexDataError as exc:
        taifex_errors["TAIFEX TX"] = str(exc)
        market_bias = type(market_bias)(score=market_bias.score, direction=market_bias.direction, notes=market_notes)
    benchmark_bars = data.get(config.market.benchmark, [])
    sector_strengths = rank_sector_strength(config.symbols, data, benchmark_bars)

    candidates: List[CandidateScore] = []
    for item in config.symbols:
        score = score_symbol(
            item=item,
            bars=data.get(item.symbol, []),
            benchmark_bars=benchmark_bars,
            market_bias=market_bias,
            risk=config.risk,
        )
        if score is not None:
            candidates.append(score)

    candidates.sort(key=lambda item: item.score, reverse=True)
    max_side = config.risk.max_candidates_per_side
    long_candidates = [item for item in candidates if item.direction == "做多觀察"][:max_side]
    short_candidates = [item for item in candidates if item.direction == "做空觀察"][:max_side]

    now = datetime.now(ZoneInfo(config.market.timezone))
    output_path = output_dir / f"{now.strftime('%Y-%m-%d')}-premarket.md"
    data_warnings = [
        f"{symbol} 資料擷取失敗，已從本次計算排除。"
        for symbol in sorted(errors.keys())
    ]
    data_warnings.extend(
        f"{symbol} 官方資料擷取失敗，已從本次計算排除。"
        for symbol in sorted(taifex_errors.keys())
    )
    render_report(
        now,
        market_bias,
        sector_strengths,
        long_candidates,
        short_candidates,
        output_path,
        data_warnings,
    )
    print(f"Report written to {output_path}")
    return 0


def run_open_check(
    config_path: Path,
    output_dir: Path,
    daily_range: str,
    intraday_range: str,
    interval: str,
    opening_bars: int,
) -> int:
    config = load_config(config_path)
    client = YahooChartClient()
    watch_symbols = [item.symbol for item in config.symbols]

    print(f"Fetching intraday data for {len(watch_symbols)} symbols...")
    intraday_data, intraday_errors = client.fetch_many_intraday_with_errors(
        watch_symbols,
        range_=intraday_range,
        interval=interval,
    )
    print(f"Fetching daily context for {len(watch_symbols)} symbols...")
    daily_data, daily_errors = client.fetch_many_daily_with_errors(watch_symbols, range_=daily_range)

    for symbol, error in {**intraday_errors, **daily_errors}.items():
        print(f"Warning: {symbol}: {error}")

    signals: List[OpeningSignal] = []
    for item in config.symbols:
        signal = analyze_opening_confirmation(
            item,
            intraday_data.get(item.symbol, []),
            daily_data.get(item.symbol, []),
            opening_bars=opening_bars,
        )
        if signal is not None:
            signals.append(signal)

    now = datetime.now(ZoneInfo(config.market.timezone))
    output_path = output_dir / f"{now.strftime('%Y-%m-%d')}-opening.md"
    sector_strengths = rank_opening_sector_strength(signals)
    warning_symbols = sorted(set(intraday_errors.keys()) | set(daily_errors.keys()))
    data_warnings = [
        f"{symbol} 盤中或日線資料擷取失敗，已從開盤確認排除。"
        for symbol in warning_symbols
    ]
    render_opening_report(now, sector_strengths, signals, output_path, data_warnings)
    print(f"Opening report written to {output_path}")
    return 0


def run_tracker(
    config_path: Path,
    output_dir: Path,
    daily_range: str,
    intraday_range: str,
    interval: str,
    opening_bars: int,
) -> int:
    config = load_config(config_path)
    client = YahooChartClient()
    cmoney_errors = {}
    cmoney_rankings = []
    try:
        cmoney_rankings = CMoneyClient().fetch_institutional_buy_rankings(limit=30)
    except CMoneyDataError as exc:
        cmoney_errors["CMoney 法人買超排行"] = str(exc)
    cmoney_ranking_map = rankings_by_symbol(cmoney_rankings)
    auto_universe = merge_cmoney_symbols(config.auto_universe, cmoney_rankings)
    all_watch_items = _dedupe_watch_symbols(auto_universe + config.manual_symbols)
    market_symbols = list(
        dict.fromkeys(
            config.market.us_market_symbols
            + [config.market.benchmark, config.market.taiwan_futures]
        )
    )
    watch_symbols = [item.symbol for item in all_watch_items]
    all_daily_symbols = list(dict.fromkeys(market_symbols + watch_symbols))

    print(f"Fetching daily data for {len(all_daily_symbols)} symbols...")
    daily_data, daily_errors = client.fetch_many_daily_with_errors(all_daily_symbols, range_=daily_range)
    print(f"Fetching intraday data for {len(watch_symbols)} watch symbols...")
    intraday_data, intraday_errors = client.fetch_many_intraday_with_errors(
        watch_symbols,
        range_=intraday_range,
        interval=interval,
    )

    market_bias = score_market_bias(daily_data, config.market.benchmark, config.market.taiwan_futures)
    market_notes = list(market_bias.notes)
    taifex_errors = {}
    tx_quote = None
    try:
        tx_quote = TaifexClient().fetch_latest_future_quote("TX")
        market_notes.append(tx_quote.summary())
        if tx_quote.change_pct is not None:
            adjusted_score = market_bias.score + tx_quote.change_pct * 0.8
            direction = "偏多" if adjusted_score >= 2 else "偏空" if adjusted_score <= -2 else "中性"
            market_bias = type(market_bias)(round(adjusted_score, 2), direction, market_notes)
    except TaifexDataError as exc:
        taifex_errors["TAIFEX TX"] = str(exc)
        market_bias = type(market_bias)(market_bias.score, market_bias.direction, market_notes)
    market_indicators = build_market_indicators(daily_data, tx_quote)

    benchmark_bars = daily_data.get(config.market.benchmark, [])
    sector_strengths = rank_sector_strength(all_watch_items, daily_data, benchmark_bars)
    candidates: List[CandidateScore] = []
    opening_signals: List[OpeningSignal] = []

    for item in all_watch_items:
        candidate = score_symbol(
            item=item,
            bars=daily_data.get(item.symbol, []),
            benchmark_bars=benchmark_bars,
            market_bias=market_bias,
            risk=config.risk,
        )
        if candidate is not None:
            candidates.append(candidate)

        signal = analyze_opening_confirmation(
            item,
            intraday_data.get(item.symbol, []),
            daily_data.get(item.symbol, []),
            opening_bars=opening_bars,
        )
        if signal is not None:
            opening_signals.append(signal)

    opening_sector_strengths = rank_opening_sector_strength(opening_signals)
    candidates.sort(key=lambda item: item.score, reverse=True)
    auto_symbols = {item.symbol for item in auto_universe}
    auto_selected = _select_auto_symbols(
        auto_universe,
        [candidate for candidate in candidates if candidate.symbol in auto_symbols],
        config.risk.max_candidates_per_side,
        cmoney_ranking_map,
    )
    auto_tracked = build_tracked_symbols(
        auto_selected,
        candidates,
        opening_signals,
        sector_strengths,
        source="auto",
        institutional_rankings=cmoney_ranking_map,
    )
    manual_tracked = build_tracked_symbols(
        config.manual_symbols,
        candidates,
        opening_signals,
        sector_strengths,
        source="manual",
        institutional_rankings=cmoney_ranking_map,
    )
    tracked_symbols = auto_tracked + manual_tracked

    now = datetime.now(ZoneInfo(config.market.timezone))
    output_path = output_dir / f"{now.strftime('%Y-%m-%d')}-tracker.html"
    warning_symbols = sorted(set(daily_errors.keys()) | set(intraday_errors.keys()))
    data_missing_count = len(warning_symbols) + len(taifex_errors) + len(cmoney_errors)
    db_path = default_db_path(PROJECT_ROOT)
    with connect(db_path) as conn:
        long_candidates = build_long_candidates(
            all_watch_items,
            daily_data,
            intraday_data,
            opening_signals,
            sector_strengths,
            market_bias,
            cmoney_ranking_map,
        )
        save_long_candidates(conn, now, long_candidates)
        update_backtests(conn, now, intraday_data)
        backtest_data = backtest_summary(conn, now.date())
        recommendation_checklist = {
            "candidate_total": len([item for item in long_candidates if item.grade in {"A", "B", "C"}]),
            "grade_a": sum(1 for item in long_candidates if item.grade == "A"),
            "grade_b": sum(1 for item in long_candidates if item.grade == "B"),
            "recommendations": int(backtest_data.get("recommendation_count", 0)),
            "backtest_trackable": int(backtest_data.get("trackable_count", 0)),
            "data_missing": data_missing_count,
        }
        long_summary = build_long_model_summary(
            long_candidates,
            market_indicators,
            market_bias,
            backtest_data,
            recommendation_checklist,
        )
    performance_summary = record_signal_performance(now, tracked_symbols, intraday_data, output_dir)
    paper_summary = update_paper_trades(now, tracked_symbols, intraday_data, output_dir)
    data_status = [
        f"每日行情成功 {len(all_daily_symbols) - len(daily_errors)}/{len(all_daily_symbols)}；失敗標的已排除或以缺漏提示處理。",
        f"盤中行情成功 {len(watch_symbols) - len(intraday_errors)}/{len(watch_symbols)}；失敗標的不納入 VWAP、量比與盤中回測。",
    ]
    if taifex_errors:
        data_status.append("台指期官方資料擷取失敗；已排除在大盤加權判斷外。")
    else:
        data_status.append("台指期官方資料擷取成功；已納入大盤狀態。")
    if cmoney_errors:
        data_status.append("CMoney 法人買超排行擷取失敗；法人排行已排除在評分加分外。")
    else:
        data_status.append(f"CMoney 法人買超排行擷取成功 {len(cmoney_rankings)} 筆；僅作現有 MVP 輔助排序。")
    data_warnings = [
        f"{symbol} 資料擷取失敗；該資料已從相關評分欄位排除或標為缺漏。"
        for symbol in warning_symbols
    ]
    data_warnings.extend(
        f"{symbol} 官方資料擷取失敗；已從大盤狀態加權排除。"
        for symbol in sorted(taifex_errors.keys())
    )
    data_warnings.extend(
        f"{symbol} 擷取失敗；法人排行暫不納入評分加分。"
        for symbol in sorted(cmoney_errors.keys())
    )
    render_tracker_html(
        now,
        market_bias,
        market_indicators,
        sector_strengths,
        opening_sector_strengths,
        tracked_symbols,
        output_path,
        data_warnings,
        data_status,
        performance_summary,
        paper_summary,
        long_summary,
    )
    print(f"Tracker written to {output_path}")
    return 0


def _select_auto_symbols(symbols, candidates: List[CandidateScore], max_per_side: int, institutional_rankings=None):
    symbol_map = {item.symbol: item for item in symbols}
    long_candidates = [item for item in candidates if item.direction == "做多觀察"]
    tradable_longs = [item for item in long_candidates if item.suggested_shares > 0]
    high_risk_longs = [item for item in long_candidates if item.suggested_shares <= 0]
    ranking_map = institutional_rankings or {}
    tradable_longs.sort(key=lambda item: _auto_selection_key(item, ranking_map))
    high_risk_longs.sort(key=lambda item: _auto_selection_key(item, ranking_map))
    selected = (tradable_longs + high_risk_longs)[:max_per_side]
    return [symbol_map[item.symbol] for item in selected if item.symbol in symbol_map]


def _auto_selection_key(candidate: CandidateScore, institutional_rankings):
    ranking = institutional_rankings.get(candidate.symbol)
    rank = ranking.rank if ranking else 9999
    return (rank == 9999, rank, -candidate.score)


def _dedupe_watch_symbols(symbols):
    result = []
    seen = set()
    for item in symbols:
        if item.symbol in seen:
            continue
        seen.add(item.symbol)
        result.append(item)
    return result


if __name__ == "__main__":
    raise SystemExit(main())
