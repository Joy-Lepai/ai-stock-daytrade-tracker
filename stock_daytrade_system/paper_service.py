from __future__ import annotations

import os
import subprocess
from pathlib import Path

from stock_daytrade_system.b_plus_trigger_tracker import build_b_plus_trigger_tracker
from stock_daytrade_system.paper_broker import (
    PAPER_ENGINE_VERSION,
    empty_paper_dashboard_payload,
    paper_dashboard_payload,
    paper_performance,
)


def build_paper_dashboard(conn, project_root: Path) -> dict:
    payload = paper_dashboard_payload(conn)
    run = payload.get("run", {})
    b_plus_triggers = build_b_plus_trigger_tracker(conn)
    manual_debug = _manual_debug(conn)
    payload["b_plus_triggers"] = b_plus_triggers
    payload["debug"] = {
        "app_version": _current_commit_hash(project_root),
        "engine_version": PAPER_ENGINE_VERSION,
        "generated_at": payload["generated_at"],
        "refresh_interval": payload["refresh_interval_seconds"],
        "api_status": payload.get("api_status", "ok"),
        "accounts_count": len(payload.get("accounts", [])),
        "open_positions_count": len(payload.get("positions", [])),
        "trades_count": len(payload.get("trades", [])),
        "skipped_count": len(payload.get("skipped_trades", payload.get("skipped", []))),
        "recommendations_scanned_count": int(run.get("recommendations_scanned", 0)),
        "executable_triggered_count": int(run.get("executable_triggered", 0)),
        "b_plus_waiting_count": sum(1 for item in b_plus_triggers if item.get("lifecycle_status") == "observed"),
        "b_plus_ready_count": sum(1 for item in b_plus_triggers if item.get("trigger_readiness") == "ready"),
        "last_error": run.get("last_error", ""),
        **manual_debug,
    }
    return payload


def build_empty_paper_dashboard(conn, project_root: Path, last_error: str = "") -> dict:
    payload = empty_paper_dashboard_payload(conn, last_error=last_error)
    run = payload.get("run", {})
    b_plus_triggers = build_b_plus_trigger_tracker(conn)
    manual_debug = _manual_debug(conn)
    payload["b_plus_triggers"] = b_plus_triggers
    payload["debug"] = {
        "app_version": _current_commit_hash(project_root),
        "engine_version": PAPER_ENGINE_VERSION,
        "generated_at": payload["generated_at"],
        "refresh_interval": payload["refresh_interval_seconds"],
        "api_status": payload.get("api_status", "ok"),
        "accounts_count": len(payload.get("accounts", [])),
        "open_positions_count": len(payload.get("positions", [])),
        "trades_count": len(payload.get("trades", [])),
        "skipped_count": len(payload.get("skipped_trades", [])),
        "recommendations_scanned_count": int(run.get("recommendations_scanned", 0)),
        "executable_triggered_count": int(run.get("executable_triggered", 0)),
        "b_plus_waiting_count": sum(1 for item in b_plus_triggers if item.get("lifecycle_status") == "observed"),
        "b_plus_ready_count": sum(1 for item in b_plus_triggers if item.get("trigger_readiness") == "ready"),
        "last_error": last_error,
        **manual_debug,
    }
    return payload


def build_paper_performance(conn) -> dict:
    return paper_performance(conn)


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


def _manual_debug(conn) -> dict:
    manual = conn.execute("SELECT COUNT(*) AS total FROM paper_trades WHERE source = 'manual'").fetchone()["total"]
    system = conn.execute("SELECT COUNT(*) AS total FROM paper_trades WHERE COALESCE(source, 'system') = 'system'").fetchone()["total"]
    open_manual = conn.execute(
        """
        SELECT COUNT(*) AS total
        FROM paper_positions p
        JOIN paper_trades t ON t.id = p.trade_id
        WHERE t.source = 'manual'
        """
    ).fetchone()["total"]
    last_manual = conn.execute(
        "SELECT created_at FROM paper_trades WHERE source = 'manual' ORDER BY created_at DESC LIMIT 1"
    ).fetchone()
    last_close = conn.execute(
        """
        SELECT exit_reason
        FROM paper_trades
        WHERE source = 'manual' AND exit_time IS NOT NULL
        ORDER BY exit_time DESC
        LIMIT 1
        """
    ).fetchone()
    return {
        "manual_trades_count": int(manual or 0),
        "system_trades_count": int(system or 0),
        "open_manual_positions_count": int(open_manual or 0),
        "last_manual_trade_created_at": last_manual["created_at"] if last_manual else "",
        "last_close_trade_status": last_close["exit_reason"] if last_close else "",
        "quote_api_status": "ready",
    }
