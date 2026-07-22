from __future__ import annotations

import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Iterable, Optional
from zoneinfo import ZoneInfo

from stock_daytrade_system.app_version import deployment_status
from stock_daytrade_system.b_plus_trigger_tracker import build_b_plus_trigger_tracker
from stock_daytrade_system.buy_signal_diagnosis import build_buy_signal_diagnosis
from stock_daytrade_system.config import WatchSymbol, load_config
from stock_daytrade_system.data_freshness import evaluate_data_freshness
from stock_daytrade_system.db import (
    connect,
    default_db_path,
    refresh_state_rows,
    save_long_candidates,
    save_tw_full_market_snapshots,
    update_backtests,
    upsert_refresh_state,
)
from stock_daytrade_system.frontend_language import front_trade_counts
from stock_daytrade_system.fugle_market_data import FugleMarketDataConfig
from stock_daytrade_system.fugle_priority_pool import build_fugle_priority_pool
from stock_daytrade_system.intraday import analyze_opening_confirmation
from stock_daytrade_system.limit_up_phase import build_limit_up_market_phase
from stock_daytrade_system.long_model import build_long_candidates
from stock_daytrade_system.market_data_provider import get_market_data_provider_manager
from stock_daytrade_system.market_mode import evaluate_tw_market_mode
from stock_daytrade_system.official_institutional import fetch_official_institutional_contexts
from stock_daytrade_system.operational_health import build_operational_health
from stock_daytrade_system.paper_broker import run_paper_trading
from stock_daytrade_system.resilience import health_status_compact, health_status_snapshot
from stock_daytrade_system.scoring import score_market_bias
from stock_daytrade_system.sectors import rank_sector_strength
from stock_daytrade_system.signal_guard import SIGNAL_GUARD_VERSION
from stock_daytrade_system.strategy_validation import update_tw_scan_result_verification
from stock_daytrade_system.tw_full_market import FullMarketQuote, build_tw_full_market_pool


REFRESH_LAYER_STALE_SECONDS = {
    "full_market": 15 * 60,
    "watchlist": 5 * 60,
    "positions": 5 * 60,
    "post_close_validation": 60 * 60,
    "manual_full_refresh": 15 * 60,
}

DEFAULT_TRACKER_TIMEOUT_SECONDS = 180

REFRESH_LAYER_RUNNING_STUCK_SECONDS = {
    "full_market": DEFAULT_TRACKER_TIMEOUT_SECONDS + 60,
    "manual_full_refresh": DEFAULT_TRACKER_TIMEOUT_SECONDS + 60,
    "watchlist": 2 * 60,
    "positions": 60,
    "post_close_validation": 2 * 60,
}

_REFRESH_LAYER_LABELS = {
    "full_market": "全市場掃描",
    "watchlist": "重點觀察",
    "positions": "交易觸發",
    "post_close_validation": "盤後驗證",
    "manual_full_refresh": "手動完整刷新",
}

WATCHLIST_ENTRY_STATUSES = {
    "executable",
    "practice_long",
    "wait_volume",
    "wait_vwap",
    "wait_breakout",
    "wait_pullback",
    "high_risk",
}


@dataclass(frozen=True)
class RefreshResult:
    layer: str
    status: str
    message: str
    duration_seconds: float
    symbols_count: int = 0
    error: str = ""

    def to_dict(self) -> dict:
        return {
            "layer": self.layer,
            "status": self.status,
            "message": self.message,
            "duration_seconds": round(self.duration_seconds, 2),
            "symbols_count": self.symbols_count,
            "error": self.error,
        }


class RefreshCoordinator:
    def __init__(
        self,
        project_root: Path,
        report_dir: Path,
        *,
        tracker_timeout_seconds: int = DEFAULT_TRACKER_TIMEOUT_SECONDS,
        config_path: Optional[Path] = None,
    ) -> None:
        self.project_root = project_root
        self.report_dir = report_dir
        self.tracker_timeout_seconds = tracker_timeout_seconds
        self.config_path = config_path or project_root / "config" / "watchlist.json"
        self._locks = {layer: threading.Lock() for layer in REFRESH_LAYER_STALE_SECONDS}
        self._global_refresh_lock = threading.Lock()

    def refresh_manual_full(self) -> RefreshResult:
        return self._run_tracked_layer("manual_full_refresh", lambda started_at: self._run_full_tracker("manual_full_refresh"))

    def refresh_full_market(self) -> RefreshResult:
        return self._run_tracked_layer("full_market", lambda started_at: self._run_full_tracker("full_market"))

    def refresh_bootstrap_review_snapshot(self) -> RefreshResult:
        return self._run_tracked_layer("full_market", self._run_bootstrap_review_snapshot)

    def refresh_watchlist(self) -> RefreshResult:
        return self._run_tracked_layer("watchlist", self._run_watchlist_refresh)

    def refresh_positions(self) -> RefreshResult:
        return self._run_tracked_layer("positions", self._run_positions_refresh)

    def refresh_post_close_validation(self) -> RefreshResult:
        return self._run_tracked_layer("post_close_validation", self._run_post_close_validation)

    def status_payload(self, now: Optional[datetime] = None) -> dict:
        now = now or datetime.now(ZoneInfo("Asia/Taipei"))
        db_path = default_db_path(self.project_root)
        with connect(db_path) as conn:
            rows = [dict(row) for row in refresh_state_rows(conn)]
            data_meta = _latest_data_meta(conn)
            inferred_layers = _latest_layer_data_meta(conn)
            price_status = _price_status_summary(conn, now=now)
            front_category_items = _latest_front_category_items(conn)
            limit_up_summary = _limit_up_operational_summary(conn)
            review_observation_candidates = _review_observation_candidates(conn)
            fugle_priority_pool = _fugle_priority_pool_status(
                conn,
                front_category_items,
                config_path=self.config_path,
                now=now,
            )
        by_layer = {layer: _empty_layer_status(layer, now) for layer in REFRESH_LAYER_STALE_SECONDS}
        for row in rows:
            layer = row["layer"]
            if layer in by_layer:
                by_layer[layer] = _layer_status(row, now)
        _apply_inferred_layer_statuses(by_layer, inferred_layers, now)
        source_health = health_status_snapshot()
        source_health_compact = health_status_compact()
        provider_status = get_market_data_provider_manager().status_payload()
        watchlist_ok = _layer_has_usable_fresh_success(by_layer["watchlist"])
        positions_ok = _layer_has_usable_fresh_success(by_layer["positions"])
        market_mode = evaluate_tw_market_mode(
            now=now,
            data_date=data_meta.get("data_date"),
            latest_data_at=data_meta.get("latest_data_at"),
            data_stale=False,
            severe_missing=False,
            watchlist_fresh=watchlist_ok,
            positions_fresh=positions_ok,
        )
        required_layers = _required_refresh_layers_for_mode(market_mode.mode)
        stale_layers = [layer for layer, item in by_layer.items() if item["is_stale"]]
        required_stale_layers = [layer for layer in required_layers if by_layer.get(layer, {}).get("is_stale")]
        any_layer_stale = bool(stale_layers)
        any_required_stale = bool(required_stale_layers)
        strong_long_allowed = _status_allows_strong_long(
            market_mode=market_mode.to_dict(),
            price_status=price_status,
            required_stale_layers=required_stale_layers,
        )
        front_category_summary = _front_category_summary(
            front_category_items,
            market_mode=market_mode.mode,
            data_today=bool(market_mode.is_data_current_for_mode),
            intraday=bool(market_mode.allow_intraday_signal),
            stale=market_mode.mode == "stale_data",
            allow_strong_long=strong_long_allowed,
        )
        can_show_any_strong_long = bool(
            strong_long_allowed and int(front_category_summary.get("strong_buy_count") or 0) > 0
        )
        strong_long_block_reason = (
            ""
            if can_show_any_strong_long
            else (
                _strong_long_block_reason(by_layer, market_mode.to_dict(), price_status)
                if not strong_long_allowed
                else str(front_category_summary.get("no_signal_reason") or "目前沒有可顯示強烈買多的即時訊號。")
            )
        )
        operation_summary = _refresh_operation_summary(
            by_layer,
            required_layers=required_layers,
            required_stale_layers=required_stale_layers,
            market_mode=market_mode.to_dict(),
        )
        refresh_guidance = _refresh_guidance(
            by_layer,
            market_mode.to_dict(),
            price_status,
            required_layers=required_layers,
            required_stale_layers=required_stale_layers,
        )
        payload = {
            "api_status": "ok",
            "generated_at": now.isoformat(timespec="seconds"),
            "market_mode": market_mode.mode,
            "market_mode_label": market_mode.label,
            "last_trading_date": market_mode.last_trading_date,
            "data_date": market_mode.data_date,
            "is_trading_day": market_mode.is_trading_day,
            "is_holiday": market_mode.is_holiday,
            "is_market_open": market_mode.is_market_open,
            "is_post_close": market_mode.is_post_close,
            "is_weekend": market_mode.is_weekend,
            "is_data_current_for_mode": market_mode.is_data_current_for_mode,
            "allow_intraday_signal": market_mode.allow_intraday_signal,
            "review_mode_message": market_mode.review_mode_message,
            "layers": by_layer,
            "data_source_health": source_health,
            "data_source_health_compact": source_health_compact,
            "provider_status": provider_status,
            "deployment_status": deployment_status(self.project_root),
            "signal_guard_version": SIGNAL_GUARD_VERSION,
            "price_status_summary": price_status,
            "front_category_summary": front_category_summary,
            "limit_up_operational_summary": limit_up_summary,
            "review_observation_candidates": review_observation_candidates,
            "fugle_priority_pool": fugle_priority_pool,
            "live_count": price_status["live_count"],
            "delayed_count": price_status["delayed_count"],
            "cached_count": price_status["cached_count"],
            "missing_count": price_status["missing_count"],
            "data_source_degraded": any(
                item.get("status") in {"ERROR", "PARTIAL"}
                for item in source_health.values()
            ),
            "required_refresh_layers": required_layers,
            "stale_layers": stale_layers,
            "required_stale_layers": required_stale_layers,
            "refresh_operation_summary": operation_summary,
            "any_layer_stale": any_layer_stale,
            "any_stale": any_required_stale,
            "refresh_guidance": refresh_guidance,
            "can_show_any_strong_long": can_show_any_strong_long,
            "allow_strong_long": strong_long_allowed,
            "reason_if_blocked": strong_long_block_reason,
            "strong_long_block_reason": strong_long_block_reason,
        }
        payload["buy_signal_diagnosis"] = build_buy_signal_diagnosis(payload)
        payload["operational_health"] = build_operational_health(payload)
        return payload

    def _run_tracked_layer(self, layer: str, runner: Callable[[datetime], tuple[int, str]]) -> RefreshResult:
        lock = self._locks[layer]
        if not lock.acquire(blocking=False):
            self._write_state(layer, status="skipped", error="already_running")
            return RefreshResult(layer, "skipped", "同一層刷新正在執行中，已略過本次請求。", 0.0, error="already_running")
        if not self._global_refresh_lock.acquire(blocking=False):
            lock.release()
            self._write_state(layer, status="skipped", error="another_refresh_running")
            return RefreshResult(layer, "skipped", "其他刷新層正在執行中，已略過本次請求，避免資料庫寫入衝突。", 0.0, error="another_refresh_running")
        started_at = datetime.now(ZoneInfo("Asia/Taipei"))
        monotonic_start = time.monotonic()
        self._write_state(layer, status="running", started_at=started_at, error="")
        try:
            symbols_count, message = runner(started_at)
            duration = time.monotonic() - monotonic_start
            success_at = datetime.now(ZoneInfo("Asia/Taipei"))
            self._write_state(
                layer,
                status="success",
                started_at=started_at,
                success_at=success_at,
                duration_seconds=duration,
                symbols_count=symbols_count,
                error="",
            )
            return RefreshResult(layer, "success", message or "刷新完成。", duration, symbols_count=symbols_count)
        except subprocess.TimeoutExpired as exc:
            duration = time.monotonic() - monotonic_start
            error = f"timeout_after_{self.tracker_timeout_seconds}s"
            self._write_state(layer, status="failed", started_at=started_at, duration_seconds=duration, error=error)
            return RefreshResult(layer, "failed", "刷新逾時，已保留上一筆可用資料。", duration, error=str(exc))
        except Exception as exc:
            duration = time.monotonic() - monotonic_start
            self._write_state(layer, status="failed", started_at=started_at, duration_seconds=duration, error=str(exc))
            return RefreshResult(layer, "failed", "刷新失敗，已保留上一筆可用資料。", duration, error=str(exc))
        finally:
            self._global_refresh_lock.release()
            lock.release()

    def _run_full_tracker(self, layer: str) -> tuple[int, str]:
        subprocess.run(
            [
                sys.executable,
                "-m",
                "stock_daytrade_system.cli",
                "tracker",
                "--output-dir",
                str(self.report_dir),
                "--daily-range",
                "6mo",
                "--intraday-range",
                "1d",
                "--interval",
                "5m",
                "--opening-bars",
                "3",
            ],
            cwd=self.project_root,
            check=True,
            timeout=self.tracker_timeout_seconds,
        )
        symbols_count = _latest_snapshot_count(default_db_path(self.project_root))
        self._mark_full_tracker_dependent_layers(layer, symbols_count)
        return symbols_count, "完整 tracker 已刷新。"

    def _run_bootstrap_review_snapshot(self, started_at: datetime) -> tuple[int, str]:
        result = build_tw_full_market_pool(self.project_root, now=started_at, max_candidates=80)
        rows = [_official_quote_bootstrap_snapshot_row(item) for item in result.candidate_quotes]
        with connect(default_db_path(self.project_root)) as conn:
            save_tw_full_market_snapshots(conn, started_at, rows)
        if not rows:
            return 0, "官方日行情啟動快照未取得候選；仍需完整刷新。"
        return len(rows), f"官方日行情啟動快照完成，建立 {len(rows)} 檔下個交易日觀察候選。"

    def _run_watchlist_refresh(self, started_at: datetime) -> tuple[int, str]:
        config = load_config(self.config_path)
        symbols = _watchlist_refresh_symbols(default_db_path(self.project_root), config.manual_symbols)
        if not symbols:
            return 0, "目前沒有重點觀察標的，略過行情刷新。"

        provider = get_market_data_provider_manager()
        market_symbols = list(
            dict.fromkeys(
                config.market.us_market_symbols
                + [config.market.benchmark, config.market.taiwan_futures]
            )
        )
        all_daily_symbols = list(dict.fromkeys(market_symbols + [item.symbol for item in symbols]))
        daily_data, _daily_errors = provider.fetch_many_daily_with_errors(all_daily_symbols, range_="6mo")
        intraday_data, _intraday_errors = provider.fetch_many_intraday_with_errors(
            [item.symbol for item in symbols],
            range_="1d",
            interval="5m",
        )
        market_bias = score_market_bias(daily_data, config.market.benchmark, config.market.taiwan_futures)
        benchmark_bars = daily_data.get(config.market.benchmark, [])
        sector_strengths = rank_sector_strength(symbols, daily_data, benchmark_bars)
        official_institutional = fetch_official_institutional_contexts(self.project_root, now=started_at)
        opening_signals = [
            signal
            for item in symbols
            if (
                signal := analyze_opening_confirmation(
                    item,
                    intraday_data.get(item.symbol, []),
                    daily_data.get(item.symbol, []),
                    opening_bars=3,
                )
            )
            is not None
        ]
        long_candidates = build_long_candidates(
            symbols,
            daily_data,
            intraday_data,
            opening_signals,
            sector_strengths,
            market_bias,
            {},
            official_institutional.contexts,
            captured_at=started_at,
        )
        with connect(default_db_path(self.project_root)) as conn:
            save_long_candidates(conn, started_at, long_candidates, prune_stale=False)
            update_backtests(conn, started_at, intraday_data)
        return len(symbols), f"重點觀察刷新完成，更新 {len(symbols)} 檔。"

    def _run_positions_refresh(self, started_at: datetime) -> tuple[int, str]:
        with connect(default_db_path(self.project_root)) as conn:
            b_plus_count = len(build_b_plus_trigger_tracker(conn, market="TW", date_text=started_at.strftime("%Y-%m-%d")))
            summary = run_paper_trading(conn, started_at)
        count = int(summary.positions or 0) + b_plus_count
        return count, f"持倉與觸發刷新完成，追蹤 {count} 筆。"

    def _run_post_close_validation(self, started_at: datetime) -> tuple[int, str]:
        with connect(default_db_path(self.project_root)) as conn:
            result = update_tw_scan_result_verification(conn, started_at, {})
        return int(result.get("rows", 0) or 0), str(result.get("message") or "盤後驗證已更新。")

    def _mark_implied_layer_success(self, layer: str, symbols_count: int) -> None:
        now = datetime.now(ZoneInfo("Asia/Taipei"))
        self._write_state(
            layer,
            status="success",
            started_at=now,
            success_at=now,
            duration_seconds=0.0,
            symbols_count=symbols_count,
            error="updated_by_full_refresh",
        )

    def _mark_full_tracker_dependent_layers(self, layer: str, symbols_count: int) -> None:
        if layer == "manual_full_refresh":
            self._mark_implied_layer_success("full_market", symbols_count)
        if layer in {"full_market", "manual_full_refresh"}:
            self._mark_implied_layer_success("watchlist", symbols_count)
            self._mark_implied_layer_success("positions", 0)

    def _write_state(
        self,
        layer: str,
        *,
        status: str,
        started_at: Optional[datetime] = None,
        success_at: Optional[datetime] = None,
        duration_seconds: Optional[float] = None,
        symbols_count: Optional[int] = None,
        error: str = "",
    ) -> None:
        with connect(default_db_path(self.project_root)) as conn:
            upsert_refresh_state(
                conn,
                layer=layer,
                status=status,
                stale_after_seconds=REFRESH_LAYER_STALE_SECONDS[layer],
                started_at=started_at,
                success_at=success_at,
                duration_seconds=duration_seconds,
                symbols_count=symbols_count,
                error=error,
            )


def _watchlist_refresh_symbols(db_path: Path, manual_symbols: Iterable[WatchSymbol]) -> list[WatchSymbol]:
    today = datetime.now(ZoneInfo("Asia/Taipei")).strftime("%Y-%m-%d")
    items: dict[str, WatchSymbol] = {item.symbol: item for item in manual_symbols}
    with connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT DISTINCT r.symbol, COALESCE(s.name, r.symbol) AS name, COALESCE(s.sector, 'watchlist') AS sector
            FROM recommendations r
            LEFT JOIN symbols s ON s.symbol = r.symbol
            WHERE r.market = 'TW'
              AND r.date = ?
              AND (
                r.grade IN ('A', 'B+', 'B')
                OR r.entry_status IN ('executable', 'practice_long', 'wait_volume', 'wait_vwap',
                                      'wait_breakout', 'wait_pullback', 'high_risk')
              )
            """,
            (today,),
        ).fetchall()
        for row in rows:
            items[row["symbol"]] = WatchSymbol(row["symbol"], row["name"], row["sector"])

        scan_rows = conn.execute(
            """
            SELECT symbol, COALESCE(name, symbol) AS name, COALESCE(market_type, 'full_market') AS sector
            FROM tw_full_market_snapshots
            WHERE date = ?
              AND (
                source_scope = 'out_of_pool'
                OR ai_grade IN ('A', 'B+', 'B')
                OR entry_status IN ('executable', 'practice_long', 'wait_volume', 'wait_vwap',
                                    'wait_breakout', 'wait_pullback', 'high_risk')
              )
            ORDER BY captured_at DESC
            LIMIT 120
            """,
            (today,),
        ).fetchall()
        for row in scan_rows:
            items.setdefault(row["symbol"], WatchSymbol(row["symbol"], row["name"], row["sector"]))
    return sorted(items.values(), key=lambda item: item.symbol)


def _latest_snapshot_count(db_path: Path) -> int:
    with connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT COUNT(*) AS total
            FROM tw_full_market_snapshots
            WHERE captured_at = (SELECT MAX(captured_at) FROM tw_full_market_snapshots)
            """
        ).fetchone()
    return int(row["total"] or 0) if row else 0


def _official_quote_bootstrap_snapshot_row(item: FullMarketQuote) -> dict:
    return {
        "symbol": item.symbol,
        "name": item.name,
        "market_type": item.market,
        "source_scope": "official_quote_bootstrap",
        "latest_price": item.price,
        "change_pct": item.change_pct,
        "volume": item.volume,
        "turnover": item.turnover,
        "volume_ratio": None,
        "vwap": None,
        "above_vwap": False,
        "break_prev_high": False,
        "break_5d_high": False,
        "ai_grade": "-",
        "entry_status": "review_observation",
        "trade_bias": "watch",
        "not_selected_reason": "官方日行情啟動快照：缺 VWAP、量比與盤中 K 線，只供下個交易日觀察，不作為即時買多。",
        "reason_code": "official_quote_bootstrap",
        "data_error": "",
        "latest_at": item.trade_date,
        "source_reasons": list(item.source_reasons or ("官方日行情異動候選",)),
    }


def _latest_data_meta(conn) -> dict:
    intraday = conn.execute(
        "SELECT MAX(date) AS data_date, MAX(captured_at) AS captured_at FROM intraday_snapshots"
    ).fetchone()
    full_market = conn.execute(
        "SELECT MAX(date) AS data_date, MAX(captured_at) AS captured_at FROM tw_full_market_snapshots"
    ).fetchone()
    data_date = (intraday["data_date"] if intraday else None) or (full_market["data_date"] if full_market else None)
    latest_data_at = (intraday["captured_at"] if intraday else None) or (full_market["captured_at"] if full_market else None)
    return {"data_date": data_date, "latest_data_at": latest_data_at}


def _latest_layer_data_meta(conn) -> dict:
    full_market = conn.execute(
        """
        SELECT captured_at, COUNT(*) AS symbols_count
        FROM tw_full_market_snapshots
        WHERE captured_at = (SELECT MAX(captured_at) FROM tw_full_market_snapshots)
        GROUP BY captured_at
        """
    ).fetchone()
    intraday = conn.execute(
        """
        SELECT captured_at, COUNT(*) AS symbols_count
        FROM intraday_snapshots
        WHERE captured_at = (SELECT MAX(captured_at) FROM intraday_snapshots)
        GROUP BY captured_at
        """
    ).fetchone()
    return {
        "full_market": {
            "captured_at": full_market["captured_at"] if full_market else None,
            "symbols_count": int(full_market["symbols_count"] or 0) if full_market else 0,
        },
        "watchlist": {
            "captured_at": intraday["captured_at"] if intraday else None,
            "symbols_count": int(intraday["symbols_count"] or 0) if intraday else 0,
        },
    }


def _apply_inferred_layer_statuses(by_layer: dict[str, dict], inferred_layers: dict, now: datetime) -> None:
    for layer, meta in (inferred_layers or {}).items():
        if layer not in by_layer:
            continue
        if by_layer[layer].get("status") not in {"idle", "skipped"}:
            continue
        captured_at = _parse_datetime((meta or {}).get("captured_at"))
        if not captured_at:
            continue
        stale_after = int(by_layer[layer].get("stale_after_seconds") or REFRESH_LAYER_STALE_SECONDS[layer])
        age_seconds = (now - captured_at).total_seconds()
        is_stale = age_seconds > stale_after
        by_layer[layer].update(
            {
                "last_started_at": captured_at.isoformat(timespec="seconds"),
                "last_success_at": captured_at.isoformat(timespec="seconds"),
                "duration_seconds": 0.0,
                "status": "stale" if is_stale else "success",
                "symbols_count": int((meta or {}).get("symbols_count") or 0),
                "error": "inferred_from_latest_snapshot",
                "age_seconds": round(age_seconds, 1),
                "is_stale": is_stale,
                "stale_label": "已過期" if is_stale else "正常",
                **_next_due_fields(captured_at, now, stale_after),
            }
        )


def _empty_layer_status(layer: str, now: datetime) -> dict:
    return {
        "layer": layer,
        "last_started_at": None,
        "last_success_at": None,
        "duration_seconds": None,
        "status": "idle",
        "symbols_count": 0,
        "error": "",
        "stale_after_seconds": REFRESH_LAYER_STALE_SECONDS[layer],
        "age_seconds": None,
        "is_stale": True,
        "stale_label": "尚未更新",
        "next_due_at": None,
        "seconds_until_stale": None,
        "running_age_seconds": None,
        "running_stuck_after_seconds": REFRESH_LAYER_RUNNING_STUCK_SECONDS.get(layer),
        "is_running_stuck": False,
    }


def _layer_status(row: dict, now: datetime) -> dict:
    layer = str(row["layer"])
    stale_after = int(row.get("stale_after_seconds") or 300)
    last_success = _parse_datetime(row.get("last_success_at"))
    last_started = _parse_datetime(row.get("last_started_at"))
    age_seconds = (now - last_success).total_seconds() if last_success else None
    running_age = (now - last_started).total_seconds() if last_started and row.get("status") == "running" else None
    running_stuck_after = int(REFRESH_LAYER_RUNNING_STUCK_SECONDS.get(layer) or stale_after)
    running_stuck = running_age is not None and running_age > running_stuck_after
    is_stale = age_seconds is None or age_seconds > stale_after or row.get("status") == "failed" or running_stuck
    status = "stale" if is_stale and row.get("status") == "success" else row.get("status")
    if running_stuck:
        status = "stuck"
    due_fields = _next_due_fields(last_success, now, stale_after)
    error = row.get("error") or ""
    if running_stuck and not error:
        error = f"running_exceeded_{running_stuck_after}s"
    return {
        "layer": layer,
        "last_started_at": row.get("last_started_at"),
        "last_success_at": row.get("last_success_at"),
        "duration_seconds": row.get("duration_seconds"),
        "status": status,
        "symbols_count": int(row.get("symbols_count") or 0),
        "error": error,
        "stale_after_seconds": stale_after,
        "age_seconds": round(age_seconds, 1) if age_seconds is not None else None,
        "is_stale": is_stale,
        "stale_label": "刷新可能卡住" if running_stuck else ("已過期" if is_stale else "正常"),
        "running_age_seconds": round(running_age, 1) if running_age is not None else None,
        "running_stuck_after_seconds": running_stuck_after,
        "is_running_stuck": running_stuck,
        **due_fields,
    }


def _next_due_fields(last_success: Optional[datetime], now: datetime, stale_after: int) -> dict:
    if not last_success:
        return {"next_due_at": None, "seconds_until_stale": None}
    next_due = last_success + timedelta(seconds=stale_after)
    seconds_until_stale = (next_due - now).total_seconds()
    return {
        "next_due_at": next_due.isoformat(timespec="seconds"),
        "seconds_until_stale": round(max(seconds_until_stale, 0.0), 1),
    }


def _parse_datetime(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=ZoneInfo("Asia/Taipei"))
    return parsed.astimezone(ZoneInfo("Asia/Taipei"))


def _price_status_summary(conn, now: datetime) -> dict:
    rows = conn.execute(
        """
        SELECT symbol, last_known_price_at AS price_at, fallback_used, fallback_reason,
               error_reason, last_known_source
        FROM last_known_prices
        WHERE market = 'TW'
        """
    ).fetchall()
    if not rows:
        rows = conn.execute(
            """
            SELECT i.symbol, i.captured_at AS price_at, 0 AS fallback_used,
                   '' AS fallback_reason, '' AS error_reason, 'intraday_snapshots' AS last_known_source
            FROM intraday_snapshots i
            JOIN (
              SELECT symbol, MAX(captured_at) AS captured_at
              FROM intraday_snapshots
              GROUP BY symbol
            ) latest ON latest.symbol = i.symbol AND latest.captured_at = i.captured_at
            """
        ).fetchall()
    counts = {"live": 0, "delayed": 0, "cached": 0, "missing": 0}
    failed_symbols: list[str] = []
    error_counts: dict[str, int] = {}
    for row in rows:
        price_at = _parse_datetime(row["price_at"])
        freshness = evaluate_data_freshness(
            now=now,
            latest_at=price_at,
            source_failed=bool(row["error_reason"]),
            partial=bool(row["fallback_reason"]),
            is_market_open=True,
        )
        status = _price_status_from_freshness(freshness.state, bool(row["fallback_used"]))
        counts[status] = counts.get(status, 0) + 1
        if status == "missing" or row["error_reason"]:
            failed_symbols.append(str(row["symbol"]))
        error = str(row["error_reason"] or row["fallback_reason"] or "")
        if error:
            error_counts[error] = error_counts.get(error, 0) + 1
    total = sum(counts.values())
    most_common_error = max(error_counts.items(), key=lambda item: item[1])[0] if error_counts else ""
    missing_ratio = (counts["missing"] / total * 100) if total else 0.0
    cached_ratio = (counts["cached"] / total * 100) if total else 0.0
    delayed_ratio = (counts["delayed"] / total * 100) if total else 0.0
    if total == 0:
        status = "資料不足"
    elif missing_ratio >= 35:
        status = "嚴重缺漏"
    elif missing_ratio >= 10:
        status = "部分缺漏"
    elif cached_ratio + delayed_ratio >= 20:
        status = "部分延遲"
    else:
        status = "正常"
    return {
        "status": status,
        "total": total,
        "live_count": counts["live"],
        "delayed_count": counts["delayed"],
        "cached_count": counts["cached"],
        "missing_count": counts["missing"],
        "live_ratio": round((counts["live"] / total * 100) if total else 0.0, 2),
        "delayed_ratio": round(delayed_ratio, 2),
        "cached_ratio": round(cached_ratio, 2),
        "missing_ratio": round(missing_ratio, 2),
        "failed_symbols": failed_symbols[:40],
        "most_common_error": most_common_error,
        "data_source_status": status,
        "can_show_any_strong_long": counts["live"] > 0 and missing_ratio < 35,
    }


def _latest_front_category_items(conn) -> list[dict]:
    captured_row = conn.execute("SELECT MAX(captured_at) AS captured_at FROM long_scores").fetchone()
    captured_at = captured_row["captured_at"] if captured_row else None
    if not captured_at:
        return []
    rows = conn.execute(
        """
        SELECT
               ls.captured_at,
               ls.date,
               ls.symbol,
               ls.bullish_score,
               ls.risk_score,
               ls.grade,
               ls.confidence_score,
               ls.confidence_level,
               ls.conflicts,
               ls.confidence_summary,
               COALESCE(NULLIF(ls.adjusted_entry_status, ''), NULLIF(ls.original_entry_status, ''), r.entry_status, '') AS entry_status,
               r.stop_loss,
               r.target_price,
               r.trigger_price,
               s.name,
               s.sector,
               ds.change_pct,
               ds.break_prev_high,
               intr.last_price,
               intr.vwap,
               intr.above_vwap,
               intr.volume_ratio
        FROM long_scores ls
        LEFT JOIN recommendations r
          ON r.market = 'TW'
         AND r.date = ls.date
         AND r.symbol = ls.symbol
        LEFT JOIN symbols s ON s.symbol = ls.symbol
        LEFT JOIN daily_snapshots ds ON ds.date = ls.date AND ds.symbol = ls.symbol
        LEFT JOIN intraday_snapshots intr ON intr.captured_at = ls.captured_at AND intr.symbol = ls.symbol
        WHERE ls.captured_at = ?
        ORDER BY
          CASE ls.grade WHEN 'A' THEN 1 WHEN 'B+' THEN 2 WHEN 'B' THEN 3 WHEN 'C' THEN 4 ELSE 5 END,
          ls.bullish_score DESC,
          ls.risk_score ASC
        LIMIT 240
        """,
        (captured_at,),
    ).fetchall()
    return [dict(row) for row in rows]


def _fugle_priority_pool_status(
    conn,
    items: list[dict],
    *,
    config_path: Path,
    now: datetime,
) -> dict:
    pinned_symbols: list[str] = []
    try:
        if config_path.exists():
            pinned_symbols = list(load_config(config_path).fugle_priority_symbols)
    except Exception:
        pinned_symbols = []
    fugle_config = FugleMarketDataConfig.from_env()
    b_plus_triggers = build_b_plus_trigger_tracker(
        conn,
        market="TW",
        date_text=now.strftime("%Y-%m-%d"),
    )
    pool = build_fugle_priority_pool(
        items,
        b_plus_triggers=b_plus_triggers,
        pinned_symbols=pinned_symbols,
        enabled=fugle_config.enabled,
        configured=fugle_config.configured,
    )
    selected = [dict(item) for item in (pool.get("selected") or [])]
    confirmable = sum(1 for item in selected if item.get("can_use_for_entry_confirmation"))
    high_risk = sum(1 for item in selected if item.get("entry_status") == "high_risk")
    pool.update(
        {
            "source": "latest_long_scores",
            "generated_at": now.isoformat(timespec="seconds"),
            "operator_summary": _fugle_priority_operator_summary(pool, confirmable=confirmable, high_risk=high_risk),
            "operator_next_action": _fugle_priority_next_action(pool, confirmable=confirmable),
            "strong_buy_safety": "Fugle 追蹤池只作五檔、逐筆、大單與最新價確認；不會直接產生強烈買多，也不會改 A / B+ / B 條件。",
            "selected_symbols": [str(item.get("symbol") or "") for item in selected if str(item.get("symbol") or "")],
            "confirmable_count": confirmable,
            "high_risk_observation_count": high_risk,
        }
    )
    return pool


def _fugle_priority_operator_summary(pool: dict, *, confirmable: int, high_risk: int) -> str:
    selected_count = int(pool.get("selected_count") or 0)
    configured = bool(pool.get("configured"))
    enabled = bool(pool.get("enabled"))
    if not selected_count:
        return "目前沒有接近進場、等待觸發或指定追蹤的股票需要佔用 Fugle 5 檔名額。"
    if not configured:
        return f"已挑出 {selected_count} 檔 Fugle 優先追蹤候選，但尚未設定 API Key，只能保留名單。"
    if not enabled:
        return f"已挑出 {selected_count} 檔 Fugle 優先追蹤候選，但 FUGLE_ENABLED 尚未啟用，仍無五檔 / 逐筆確認。"
    parts = [f"已挑出 {selected_count} 檔給 Fugle 即時確認"]
    if confirmable:
        parts.append(f"{confirmable} 檔可作進場前確認")
    if high_risk:
        parts.append(f"{high_risk} 檔只作追價風險降溫觀察")
    return "，".join(parts) + "。"


def _fugle_priority_next_action(pool: dict, *, confirmable: int) -> str:
    selected_count = int(pool.get("selected_count") or 0)
    configured = bool(pool.get("configured"))
    enabled = bool(pool.get("enabled"))
    if not selected_count:
        return "等待全市場掃描或重點觀察層產生接近進場的股票。"
    if not configured:
        return "先在 Render 設定 FUGLE_API_KEY，設定後重新部署或刷新。"
    if not enabled:
        return "將 FUGLE_ENABLED 設為 true 後重新部署，再刷新重點觀察。"
    if confirmable:
        return "逐檔看五檔買賣盤差、委買委賣量變化、大單敲進 / 敲出、最新價墊高與 VWAP。"
    return "目前名單偏觀察或高風險，先等風險降溫、站回 VWAP、量比放大或突破條件補齊。"


def _front_category_summary(
    items: list[dict],
    *,
    market_mode: str,
    data_today: bool,
    intraday: bool,
    stale: bool,
    allow_strong_long: bool,
) -> dict:
    views_payload = front_trade_counts(
        items,
        data_today=data_today,
        intraday=intraday,
        stale=stale,
        allow_strong_long=allow_strong_long,
        market_mode=market_mode,
    )
    counts = dict(views_payload.get("counts") or {})
    bearish = int(counts.get("看空", 0) or 0)
    watch = int(counts.get("觀察", 0) or 0)
    strong = int(counts.get("強烈買多", 0) or 0)
    buy = int(counts.get("買多", 0) or 0)
    data_missing = int(counts.get("資料不足", 0) or 0)
    total = strong + buy + watch + bearish
    bearish_ratio = round((bearish / total * 100) if total else 0.0, 2)
    no_signal_reason = ""
    if total <= 0:
        no_signal_reason = "尚未產生四分類候選資料，先確認 full_market / watchlist 是否完成刷新。"
    elif market_mode != "intraday":
        no_signal_reason = "非盤中模式，強烈買多為 0 屬於正常安全規則。"
    elif not intraday:
        no_signal_reason = "目前不允許盤中訊號，先看資料模式與刷新層。"
    elif strong <= 0 and buy <= 0 and bearish_ratio >= 60:
        no_signal_reason = "看空比例偏高，這不是做空建議；先查資料模式、VWAP、價格狀態與四分類原因診斷。"
    elif strong <= 0 and buy <= 0:
        no_signal_reason = "目前沒有強烈買多或買多，先查 VWAP、量比、突破、風險與信心分數卡關。"
    elif strong <= 0:
        no_signal_reason = "目前沒有強烈買多，先看買多清單的下一步觸發條件。"
    else:
        no_signal_reason = "已有強烈買多候選，仍需逐檔確認進場雷達與停損距離。"
    return {
        "counts": counts,
        "total": total,
        "strong_buy_count": strong,
        "buy_count": buy,
        "watch_count": watch,
        "bearish_count": bearish,
        "data_missing_count": data_missing,
        "bearish_ratio": bearish_ratio,
        "no_signal_reason": no_signal_reason,
    }


def _limit_up_operational_summary(conn) -> dict:
    captured_row = conn.execute("SELECT MAX(captured_at) AS captured_at FROM tw_full_market_snapshots").fetchone()
    captured_at = captured_row["captured_at"] if captured_row else None
    if not captured_at:
        return {
            "near_limit_up_count": 0,
            "entered_ai_count": 0,
            "high_risk_count": 0,
            "wait_confirm_count": 0,
            "avoid_count": 0,
            "data_missing_count": 0,
            "summary": "目前沒有接近漲停 / 急拉快照。",
            "action": "回到強烈買多漏斗與進場雷達。",
            "risk_gate": "不要因沒有摘要就臨時放寬模型。",
            **build_limit_up_market_phase(
                total=0,
                chase_risk=0,
                wait_confirm=0,
                avoid=0,
                data_missing=0,
                entered=0,
                subject="接近漲停 / 急拉",
                empty_target="快照",
            ),
            "source": "tw_full_market_snapshots",
        }
    rows = conn.execute(
        """
        SELECT symbol, name, change_pct, turnover, entered_ai_candidates, entry_status,
               ai_grade, reason_code, not_selected_reason
        FROM tw_full_market_snapshots
        WHERE captured_at = ?
          AND COALESCE(change_pct, 0) >= 9
        ORDER BY change_pct DESC, turnover DESC
        LIMIT 80
        """,
        (captured_at,),
    ).fetchall()
    near_limit_count = len(rows)
    entered = 0
    high_risk = 0
    wait_confirm = 0
    avoid = 0
    data_missing = 0
    top_symbols: list[str] = []
    top_watchlist: list[dict] = []
    for row in rows:
        status = str(row["entry_status"] or "")
        reason_code = str(row["reason_code"] or "")
        symbol = str(row["symbol"] or "")
        name = str(row["name"] or "")
        if len(top_watchlist) < 5:
            top_watchlist.append(
                {
                    "symbol": symbol,
                    "name": name,
                    "change_pct": row["change_pct"],
                    "turnover": row["turnover"],
                    "grade": row["ai_grade"] or "-",
                    "entry_status": status or "-",
                    "reason_code": reason_code or "-",
                    "not_selected_reason": row["not_selected_reason"] or "",
                    "action": _limit_up_operator_action(status, reason_code),
                    "avoid": _limit_up_operator_avoid(status, reason_code),
                }
            )
        if len(top_symbols) < 5:
            top_symbols.append(f"{symbol}｜{name}".strip("｜"))
        entered += 1 if row["entered_ai_candidates"] else 0
        high_risk += 1 if status == "high_risk" else 0
        wait_confirm += 1 if status.startswith("wait_") else 0
        avoid += 1 if status == "avoid" else 0
        data_missing += 1 if reason_code in {"data_missing", "data_insufficient", "yahoo_intraday_failed"} else 0
    parts = []
    if high_risk:
        parts.append(f"{high_risk} 檔追價風險高")
    if wait_confirm:
        parts.append(f"{wait_confirm} 檔等待確認")
    if entered:
        parts.append(f"{entered} 檔已進模型層")
    if avoid:
        parts.append(f"{avoid} 檔多方失效")
    if data_missing:
        parts.append(f"{data_missing} 檔資料不足")
    if near_limit_count and not parts:
        parts.append(f"{near_limit_count} 檔急拉觀察")
    summary = "；".join(parts) + "。" if parts else "目前沒有接近漲停 / 急拉快照。"
    action = (
        "先看漲停強勢速讀與 /tw/advisor 急拉作戰卡，逐檔確認 VWAP、停損距離與進場雷達。"
        if near_limit_count
        else "回到強烈買多漏斗與進場雷達。"
    )
    phase = build_limit_up_market_phase(
        total=near_limit_count,
        chase_risk=high_risk,
        wait_confirm=wait_confirm,
        avoid=avoid,
        data_missing=data_missing,
        entered=entered,
        subject="接近漲停 / 急拉",
        empty_target="快照",
    )
    return {
        "captured_at": captured_at,
        "near_limit_up_count": near_limit_count,
        "entered_ai_count": entered,
        "high_risk_count": high_risk,
        "wait_confirm_count": wait_confirm,
        "avoid_count": avoid,
        "data_missing_count": data_missing,
        "top_symbols": top_symbols,
        "top_watchlist": top_watchlist,
        "summary": summary,
        "action": action,
        "risk_gate": "接近漲停代表動能強，也代表追價風險高；不可直接升級買多。",
        **phase,
        "source": "tw_full_market_snapshots",
    }


def _review_observation_candidates(conn, *, limit: int = 10) -> dict:
    """Return actionable review-only candidates from the latest usable full-market snapshot."""
    captured_row = conn.execute(
        """
        SELECT captured_at, date, COUNT(*) AS usable_count
        FROM tw_full_market_snapshots
        WHERE price IS NOT NULL
          AND COALESCE(data_status, '') NOT IN ('data_missing', 'missing')
          AND COALESCE(reason_code, '') NOT IN ('data_missing', 'yahoo_intraday_failed')
        GROUP BY captured_at, date
        HAVING usable_count > 0
        ORDER BY date DESC, captured_at DESC
        LIMIT 1
        """
    ).fetchone()
    if not captured_row:
        return {
            "status": "no_usable_snapshot",
            "message": "目前沒有可用的上一交易日快照，無法整理下個交易日觀察清單。",
            "items": [],
            "source": "tw_full_market_snapshots",
        }

    captured_at = captured_row["captured_at"]
    rows = conn.execute(
        """
        SELECT symbol, name, date, captured_at, price, change_pct, turnover,
               volume_ratio, vwap, above_vwap, break_prev_high, break_5d_high,
               ai_grade, entry_status, trade_bias, reason_code, not_selected_reason,
               source_scope
        FROM tw_full_market_snapshots
        WHERE captured_at = ?
          AND price IS NOT NULL
          AND COALESCE(data_status, '') NOT IN ('data_missing', 'missing')
          AND COALESCE(reason_code, '') NOT IN ('data_missing', 'yahoo_intraday_failed')
        ORDER BY
          CASE
            WHEN ai_grade = 'A' THEN 1
            WHEN ai_grade = 'B+' THEN 2
            WHEN ai_grade = 'B' THEN 3
            WHEN entry_status IN ('wait_breakout', 'wait_volume', 'wait_vwap', 'wait_pullback') THEN 4
            WHEN entry_status = 'high_risk' THEN 5
            ELSE 6
          END,
          COALESCE(change_pct, 0) DESC,
          COALESCE(turnover, 0) DESC
        LIMIT ?
        """,
        (captured_at, limit),
    ).fetchall()
    items = [_review_observation_item(dict(row), rank=index + 1) for index, row in enumerate(rows)]
    return {
        "status": "ok" if items else "empty",
        "message": "以下是上一交易日整理出的下個交易日觀察清單；開盤後仍需重新確認 live、VWAP、量比與進場雷達。",
        "date": captured_row["date"],
        "captured_at": captured_at,
        "count": len(items),
        "items": items,
        "source": "tw_full_market_snapshots",
    }


def _review_observation_item(row: dict, *, rank: int) -> dict:
    status = str(row.get("entry_status") or "")
    reason_code = str(row.get("reason_code") or "")
    reason_text = str(row.get("not_selected_reason") or "")
    grade = str(row.get("ai_grade") or "-")
    if grade in {"A", "B+", "B"}:
        label = "已進模型觀察"
        next_step = "開盤後先確認是否維持 VWAP 上方、量比是否延續，再看進場雷達。"
    elif status.startswith("wait_"):
        label = "等待確認"
        next_step = _wait_status_next_step(status)
    elif status == "high_risk" or "追價" in reason_text or reason_code == "high_chase_risk":
        label = "高風險觀察"
        next_step = "不要追第一根，等待拉回 VWAP 附近、停損距離縮小或進場雷達轉強。"
    elif status == "avoid":
        label = "暫不做多"
        next_step = "只看是否重新站回 VWAP 並解除多方失效。"
    else:
        label = "觀察"
        next_step = "開盤後重新確認 VWAP、量比、突破與資料 live。"
    reason = reason_text or _reason_from_row(row)
    return {
        "rank": rank,
        "symbol": str(row.get("symbol") or ""),
        "name": str(row.get("name") or row.get("symbol") or ""),
        "date": row.get("date"),
        "captured_at": row.get("captured_at"),
        "price": row.get("price"),
        "change_pct": row.get("change_pct"),
        "turnover": row.get("turnover"),
        "volume_ratio": row.get("volume_ratio"),
        "above_vwap": bool(row.get("above_vwap")),
        "break_prev_high": bool(row.get("break_prev_high")),
        "break_5d_high": bool(row.get("break_5d_high")),
        "ai_grade": grade,
        "entry_status": status or "-",
        "trade_bias": row.get("trade_bias") or "watch",
        "source_scope": row.get("source_scope") or "-",
        "reason_code": reason_code or "-",
        "label": label,
        "reason": reason,
        "next_step": next_step,
        "safety_note": "這是下個交易日觀察，不是盤中即時買多；開盤後必須重新確認。"
    }


def _wait_status_next_step(status: str) -> str:
    return {
        "wait_vwap": "下一步等站回 VWAP 並維持，否則只觀察。",
        "wait_volume": "下一步等量比放大，短線資金未進場前不追。",
        "wait_breakout": "下一步等突破觸發價或昨日高點，不提前追價。",
        "wait_pullback": "下一步等拉回 VWAP 附近不破，再重新評估。",
    }.get(status, "下一步等缺口條件補齊。")


def _reason_from_row(row: dict) -> str:
    parts: list[str] = []
    if row.get("above_vwap"):
        parts.append("站上 VWAP")
    if row.get("break_prev_high"):
        parts.append("突破昨日高點")
    if row.get("break_5d_high"):
        parts.append("突破 5 日高點")
    volume_ratio = row.get("volume_ratio")
    if volume_ratio is not None:
        try:
            parts.append(f"量比 {float(volume_ratio):.2f}")
        except (TypeError, ValueError):
            pass
    return "、".join(parts) if parts else "等待開盤後重新確認條件。"


def _limit_up_operator_action(entry_status: str, reason_code: str) -> str:
    status = str(entry_status or "")
    reason = str(reason_code or "")
    if reason in {"data_missing", "data_insufficient", "yahoo_intraday_failed"}:
        return "先確認資料是否恢復 live，缺資料前只做觀察。"
    if status == "high_risk":
        return "放進追價風險觀察，等拉回 VWAP 附近、停損距離縮小或進場雷達轉強。"
    if status.startswith("wait_"):
        return "等待缺口條件補齊，再進個股作戰卡確認下一步。"
    if status in {"avoid", "data_missing"}:
        return "暫不做多，只看是否重新站回多方結構。"
    if status in {"executable", "practice_long"}:
        return "優先看五檔、逐筆、大單與停損距離，不用市價追。"
    return "先看 VWAP、停損距離與追價風險，不因漲幅直接進場。"


def _limit_up_operator_avoid(entry_status: str, reason_code: str) -> str:
    status = str(entry_status or "")
    reason = str(reason_code or "")
    if reason in {"data_missing", "data_insufficient", "yahoo_intraday_failed"}:
        return "不要用資料不足的急拉股做即時判斷。"
    if status == "high_risk":
        return "不要把 high_risk 當成買多或強烈買多。"
    if status.startswith("wait_"):
        return "不要在 VWAP、量能或突破尚未確認前追第一波。"
    if status == "avoid":
        return "不要用漲幅掩蓋多方結構失效。"
    return "不要只因接近漲停就追價。"


def _price_status_from_freshness(state: str, fallback_used: bool = False) -> str:
    if fallback_used:
        return "cached"
    if state == "live":
        return "live"
    if state in {"delayed", "stale"}:
        return "delayed"
    if state == "last_known":
        return "cached"
    return "missing"


def _status_allows_strong_long(*, market_mode: dict, price_status: dict, required_stale_layers: list[str]) -> bool:
    if not bool(market_mode.get("allow_strong_long")):
        return False
    if required_stale_layers:
        return False
    if int(price_status.get("live_count", 0) or 0) <= 0:
        return False
    if not bool(price_status.get("can_show_any_strong_long", True)):
        return False
    return True


def _layer_has_usable_fresh_success(layer: dict) -> bool:
    status = str(layer.get("status") or "")
    if status in {"failed", "stale", "idle"}:
        return False
    return bool(layer.get("last_success_at")) and not bool(layer.get("is_stale"))


def _required_refresh_layers_for_mode(mode: str) -> list[str]:
    if mode == "intraday":
        return ["watchlist", "positions"]
    if mode == "pre_open_prepare":
        return ["full_market"]
    if mode == "post_close_review":
        return ["full_market", "post_close_validation"]
    if mode == "closed_review":
        return ["full_market"]
    if mode == "stale_data":
        return ["full_market", "watchlist"]
    return ["full_market", "watchlist"]


def _refresh_guidance(
    layers: dict[str, dict],
    market_mode: dict,
    price_status: dict,
    *,
    required_layers: list[str],
    required_stale_layers: list[str],
) -> dict:
    mode = str(market_mode.get("mode") or "")
    live_count = int(price_status.get("live_count", 0) or 0)
    action_label = "不需手動更新"
    action_endpoint = ""
    severity = "ok"
    can_use_dashboard = True
    summary = "必要資料層正常，可依目前頁面作為追蹤與復盤參考。"

    if "watchlist" in required_stale_layers:
        action_label = "更新重點觀察"
        action_endpoint = "/refresh_watchlist"
        severity = "block" if mode == "intraday" else "warn"
        can_use_dashboard = mode != "intraday"
        summary = "重點觀察資料已過期，盤中不要依此進場；請先更新重點觀察。"
    elif "positions" in required_stale_layers:
        action_label = "更新持倉/觸發"
        action_endpoint = "/refresh_positions"
        severity = "block" if mode == "intraday" else "warn"
        can_use_dashboard = mode != "intraday"
        summary = "持倉與觸發資料已過期，停損停利與觸發判斷需先更新。"
    elif "full_market" in required_stale_layers:
        action_label = "更新全市場"
        action_endpoint = "/refresh_full_market"
        severity = "warn"
        summary = "全市場掃描已過期，既有重點股可觀察，但不應新增全市場強烈買多。"
    elif "post_close_validation" in required_stale_layers:
        action_label = "更新盤後驗證"
        action_endpoint = "/refresh_post_close_validation"
        severity = "warn"
        summary = "盤後驗證尚未更新，策略成績單可能還不是最新。"
    elif mode == "intraday" and live_count <= 0:
        action_label = "更新重點觀察"
        action_endpoint = "/refresh_watchlist"
        severity = "block"
        can_use_dashboard = False
        summary = "盤中目前沒有 live 價格資料，禁止顯示強烈買多；請先更新重點觀察。"
    elif mode == "intraday":
        summary = "盤中必要資料層正常，可追蹤重點股；是否進場仍需看進場雷達與風控。"
    elif mode == "pre_open_prepare":
        summary = "開盤前準備模式：可查看上一交易日觀察池與今日準備資料，開盤前不顯示即時強烈買多。"
    elif mode == "post_close_review":
        summary = "收盤後復盤模式：用於驗證今日訊號與整理下個交易日觀察清單。"
    elif mode == "closed_review":
        summary = str(market_mode.get("review_mode_message") or "休市復盤模式：顯示上一交易日資料，僅供復盤與下個交易日觀察。")
    elif mode == "stale_data":
        action_label = "完整刷新"
        action_endpoint = "/refresh"
        severity = "block"
        can_use_dashboard = False
        summary = "資料日期或新鮮度異常，僅供檢查，不建議依此交易；請先完整刷新。"

    if required_stale_layers and not action_endpoint:
        first = required_stale_layers[0]
        action_label = {
            "full_market": "更新全市場",
            "watchlist": "更新重點觀察",
            "positions": "更新持倉/觸發",
            "post_close_validation": "更新盤後驗證",
        }.get(first, "完整刷新")
        action_endpoint = {
            "full_market": "/refresh_full_market",
            "watchlist": "/refresh_watchlist",
            "positions": "/refresh_positions",
            "post_close_validation": "/refresh_post_close_validation",
        }.get(first, "/refresh")

    return {
        "severity": severity,
        "summary": summary,
        "action_label": action_label,
        "action_endpoint": action_endpoint,
        "can_use_dashboard": can_use_dashboard,
        "required_layers": required_layers,
        "required_stale_layers": required_stale_layers,
    }


def _refresh_operation_summary(
    layers: dict[str, dict],
    *,
    required_layers: list[str],
    required_stale_layers: list[str],
    market_mode: dict,
) -> dict:
    running_layers = [layer for layer, item in layers.items() if item.get("status") == "running"]
    stuck_layers = [layer for layer, item in layers.items() if item.get("status") == "stuck"]
    failed_layers = [layer for layer, item in layers.items() if item.get("status") == "failed"]
    skipped_layers = [layer for layer, item in layers.items() if item.get("status") == "skipped"]
    required_failed = [layer for layer in required_layers if layer in failed_layers]
    required_stuck = [layer for layer in required_layers if layer in stuck_layers]
    required_running = [layer for layer in required_layers if layer in running_layers]
    required_skipped = [layer for layer in required_layers if layer in skipped_layers]
    blocking_layers = list(dict.fromkeys(required_stale_layers + required_failed + required_stuck))
    can_use_dashboard = not blocking_layers and str(market_mode.get("mode") or "") != "stale_data"

    if required_stuck:
        message = f"必要資料層刷新可能卡住：{_layer_labels(required_stuck)}。請重試或查看資料源錯誤。"
        severity = "block"
    elif blocking_layers:
        message = f"必要資料層需處理：{_layer_labels(blocking_layers)}。先更新後再判斷。"
        severity = "block"
    elif str(market_mode.get("mode") or "") == "stale_data":
        message = "目前市場模式為資料異常，dashboard 僅供檢查，不應作為盤中進場依據。"
        severity = "block"
    elif required_running:
        message = f"必要資料層更新中：{_layer_labels(required_running)}。請等待完成。"
        severity = "warn"
    elif required_skipped:
        message = f"必要資料層剛略過重複更新：{_layer_labels(required_skipped)}。若最後成功時間仍新鮮，可繼續觀察。"
        severity = "warn"
    elif failed_layers:
        message = f"非必要資料層有失敗：{_layer_labels(failed_layers)}。目前核心判斷仍可觀察，但需留意資料缺口。"
        severity = "warn"
    else:
        message = "必要資料層正常，dashboard 可作為追蹤與復盤參考。"
        severity = "ok"

    return {
        "severity": severity,
        "message": message,
        "can_use_dashboard": can_use_dashboard,
        "running_layers": running_layers,
        "stuck_layers": stuck_layers,
        "failed_layers": failed_layers,
        "skipped_layers": skipped_layers,
        "required_running_layers": required_running,
        "required_stuck_layers": required_stuck,
        "required_skipped_layers": required_skipped,
        "blocking_layers": blocking_layers,
        "running_layer_labels": _layer_label_list(running_layers),
        "stuck_layer_labels": _layer_label_list(stuck_layers),
        "failed_layer_labels": _layer_label_list(failed_layers),
        "skipped_layer_labels": _layer_label_list(skipped_layers),
        "blocking_layer_labels": _layer_label_list(blocking_layers),
    }


def _layer_label_list(layers: list[str]) -> list[str]:
    return [_REFRESH_LAYER_LABELS.get(layer, layer) for layer in layers]


def _layer_labels(layers: list[str]) -> str:
    labels = _layer_label_list(layers)
    return "、".join(labels) if labels else "無"


def _strong_long_block_reason(layers: dict[str, dict], market_mode: Optional[dict] = None, price_status: Optional[dict] = None) -> str:
    if market_mode and market_mode.get("mode") != "intraday":
        return str(market_mode.get("review_mode_message") or "目前不是盤中模式，禁止顯示即時強烈買多。")
    if price_status and int(price_status.get("live_count", 0) or 0) <= 0:
        return "目前沒有即時價格資料，禁止顯示強烈買多。"
    if layers["watchlist"]["is_stale"]:
        return "watchlist 層資料過期，禁止顯示強烈買多。"
    if layers["positions"]["is_stale"]:
        return "positions 層資料過期，禁止顯示停損停利觸發判斷。"
    return "資料層狀態不完整。"
