from __future__ import annotations

import os
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Optional

from stock_daytrade_system.db import backtest_summary, save_us_candidates, save_us_symbols
from stock_daytrade_system.market_clock import us_market_session
from stock_daytrade_system.us_data import US_DATA_VERSION, fetch_us_watchlist_data, index_environment
from stock_daytrade_system.us_long_model import US_MODEL_VERSION, build_us_long_candidates
from stock_daytrade_system.us_symbols import us_symbol_rows


def build_us_dashboard_payload(conn, project_root: Path, now: Optional[datetime] = None) -> dict:
    clock = us_market_session(now)
    bundle = fetch_us_watchlist_data(now=clock.now_local)
    index_state = index_environment(bundle.snapshots)
    candidates = build_us_long_candidates(bundle.snapshots, index_state["market_status"])
    save_us_symbols(conn, us_symbol_rows(clock.now_local))
    save_us_candidates(conn, clock.now_local, candidates, clock.session)
    backtest = backtest_summary(conn, clock.now_local.date(), market="US")
    recommendations_count = int(backtest.get("recommendation_count", 0))
    return {
        "market": {
            "market": "US",
            "session": clock.session,
            "status_text": clock.status_text,
            "timezone": clock.timezone,
            "now_local": clock.now_local.isoformat(timespec="seconds"),
            "refresh_interval_seconds": clock.refresh_interval_seconds,
            "market_status": index_state["market_status"],
            "market_status_text": index_state["status_text"],
        },
        "data_source": {
            **bundle.status.to_dict(),
            "next_update_seconds": clock.refresh_interval_seconds,
        },
        "indices": {
            "qqq_change_pct": index_state["qqq_change_pct"],
            "spy_change_pct": index_state["spy_change_pct"],
        },
        "candidates": [item.to_dict() for item in candidates],
        "summary": {
            "candidate_count": len(candidates),
            "grade_a": sum(1 for item in candidates if item.grade == "A"),
            "grade_b": sum(1 for item in candidates if item.grade == "B"),
            "executable": sum(1 for item in candidates if item.entry_status == "executable"),
            "wait_volume": sum(1 for item in candidates if item.entry_status == "wait_volume"),
            "wait_vwap": sum(1 for item in candidates if item.entry_status == "wait_vwap"),
            "wait_breakout": sum(1 for item in candidates if item.entry_status == "wait_breakout"),
            "wait_pullback": sum(1 for item in candidates if item.entry_status == "wait_pullback"),
            "high_risk": sum(1 for item in candidates if item.entry_status == "high_risk"),
            "avoid": sum(1 for item in candidates if item.entry_status == "avoid"),
            "recommendations": recommendations_count,
            "observed": int(backtest.get("observed_count", 0)),
            "triggered": int(backtest.get("triggered_count", 0)),
            "expired": int(backtest.get("expired_count", 0)),
            "closed": int(backtest.get("closed_count", 0)),
            "trackable": int(backtest.get("trackable_count", 0)),
        },
        "backtest": backtest,
        "debug": {
            "app_version": _current_commit_hash(project_root),
            "model_version": US_MODEL_VERSION,
            "data_version": US_DATA_VERSION,
            "dashboard_generated_at": clock.now_local.isoformat(timespec="seconds"),
            "market_session": clock.session,
            "refresh_interval": clock.refresh_interval_seconds,
            "candidates_count": len(candidates),
            "recommendations_count": recommendations_count,
            "data_source_status": "success" if bundle.status.ok else "partial_failure",
        },
        "disclaimer": "本系統僅供資料整理與策略回測，不構成投資建議，也不保證獲利。",
    }


def _current_commit_hash(project_root: Path) -> str:
    for key in ("RENDER_GIT_COMMIT", "SOURCE_VERSION"):
        value = os.environ.get(key)
        if value:
            return value[:12]
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "--short=12", "HEAD"],
            cwd=project_root,
            check=True,
            capture_output=True,
            text=True,
        )
        return completed.stdout.strip()
    except Exception:
        return "unknown"
