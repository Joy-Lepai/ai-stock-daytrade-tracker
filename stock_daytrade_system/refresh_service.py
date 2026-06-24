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
from stock_daytrade_system.config import WatchSymbol, load_config
from stock_daytrade_system.data_freshness import evaluate_data_freshness
from stock_daytrade_system.db import (
    connect,
    default_db_path,
    refresh_state_rows,
    save_long_candidates,
    update_backtests,
    upsert_refresh_state,
)
from stock_daytrade_system.intraday import analyze_opening_confirmation
from stock_daytrade_system.long_model import build_long_candidates
from stock_daytrade_system.market_data_provider import get_market_data_provider_manager
from stock_daytrade_system.market_mode import evaluate_tw_market_mode
from stock_daytrade_system.official_institutional import fetch_official_institutional_contexts
from stock_daytrade_system.paper_broker import run_paper_trading
from stock_daytrade_system.resilience import health_status_compact, health_status_snapshot
from stock_daytrade_system.scoring import score_market_bias
from stock_daytrade_system.sectors import rank_sector_strength
from stock_daytrade_system.signal_guard import SIGNAL_GUARD_VERSION
from stock_daytrade_system.strategy_validation import update_tw_scan_result_verification


REFRESH_LAYER_STALE_SECONDS = {
    "full_market": 15 * 60,
    "watchlist": 5 * 60,
    "positions": 5 * 60,
    "post_close_validation": 60 * 60,
    "manual_full_refresh": 15 * 60,
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
        tracker_timeout_seconds: int = 45,
        config_path: Optional[Path] = None,
    ) -> None:
        self.project_root = project_root
        self.report_dir = report_dir
        self.tracker_timeout_seconds = tracker_timeout_seconds
        self.config_path = config_path or project_root / "config" / "watchlist.json"
        self._locks = {layer: threading.Lock() for layer in REFRESH_LAYER_STALE_SECONDS}

    def refresh_manual_full(self) -> RefreshResult:
        return self._run_tracked_layer("manual_full_refresh", lambda started_at: self._run_full_tracker("manual_full_refresh"))

    def refresh_full_market(self) -> RefreshResult:
        return self._run_tracked_layer("full_market", lambda started_at: self._run_full_tracker("full_market"))

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
        by_layer = {layer: _empty_layer_status(layer, now) for layer in REFRESH_LAYER_STALE_SECONDS}
        for row in rows:
            layer = row["layer"]
            if layer in by_layer:
                by_layer[layer] = _layer_status(row, now)
        _apply_inferred_layer_statuses(by_layer, inferred_layers, now)
        source_health = health_status_snapshot()
        source_health_compact = health_status_compact()
        provider_status = get_market_data_provider_manager().status_payload()
        watchlist_ok = not by_layer["watchlist"]["is_stale"] and by_layer["watchlist"]["status"] == "success"
        positions_ok = not by_layer["positions"]["is_stale"] and by_layer["positions"]["status"] == "success"
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
        refresh_guidance = _refresh_guidance(
            by_layer,
            market_mode.to_dict(),
            price_status,
            required_layers=required_layers,
            required_stale_layers=required_stale_layers,
        )
        return {
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
            "any_layer_stale": any_layer_stale,
            "any_stale": any_required_stale,
            "refresh_guidance": refresh_guidance,
            "can_show_any_strong_long": bool(market_mode.allow_strong_long and price_status["live_count"] > 0),
            "allow_strong_long": bool(market_mode.allow_strong_long and price_status["live_count"] > 0),
            "reason_if_blocked": "" if market_mode.allow_strong_long and price_status["live_count"] > 0 else _strong_long_block_reason(by_layer, market_mode.to_dict(), price_status),
            "strong_long_block_reason": "" if market_mode.allow_strong_long and price_status["live_count"] > 0 else _strong_long_block_reason(by_layer, market_mode.to_dict(), price_status),
        }

    def _run_tracked_layer(self, layer: str, runner: Callable[[datetime], tuple[int, str]]) -> RefreshResult:
        lock = self._locks[layer]
        if not lock.acquire(blocking=False):
            self._write_state(layer, status="skipped", error="already_running")
            return RefreshResult(layer, "skipped", "同一層刷新正在執行中，已略過本次請求。", 0.0, error="already_running")
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
    }


def _layer_status(row: dict, now: datetime) -> dict:
    stale_after = int(row.get("stale_after_seconds") or 300)
    last_success = _parse_datetime(row.get("last_success_at"))
    last_started = _parse_datetime(row.get("last_started_at"))
    age_seconds = (now - last_success).total_seconds() if last_success else None
    running_age = (now - last_started).total_seconds() if last_started and row.get("status") == "running" else None
    running_stuck = running_age is not None and running_age > stale_after
    is_stale = age_seconds is None or age_seconds > stale_after or row.get("status") == "failed" or running_stuck
    status = "stale" if is_stale and row.get("status") == "success" else row.get("status")
    if running_stuck:
        status = "stale"
    due_fields = _next_due_fields(last_success, now, stale_after)
    return {
        "layer": row["layer"],
        "last_started_at": row.get("last_started_at"),
        "last_success_at": row.get("last_success_at"),
        "duration_seconds": row.get("duration_seconds"),
        "status": status,
        "symbols_count": int(row.get("symbols_count") or 0),
        "error": row.get("error") or "",
        "stale_after_seconds": stale_after,
        "age_seconds": round(age_seconds, 1) if age_seconds is not None else None,
        "is_stale": is_stale,
        "stale_label": "已過期" if is_stale else "正常",
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


def _required_refresh_layers_for_mode(mode: str) -> list[str]:
    if mode == "intraday":
        return ["watchlist", "positions"]
    if mode == "pre_open_prepare":
        return ["full_market", "watchlist"]
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
