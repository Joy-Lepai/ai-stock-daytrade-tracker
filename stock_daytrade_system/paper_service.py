from __future__ import annotations

import os
import subprocess
from pathlib import Path

from stock_daytrade_system.paper_broker import (
    PAPER_ENGINE_VERSION,
    empty_paper_dashboard_payload,
    paper_dashboard_payload,
    paper_performance,
)


def build_paper_dashboard(conn, project_root: Path) -> dict:
    payload = paper_dashboard_payload(conn)
    run = payload.get("run", {})
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
        "last_error": run.get("last_error", ""),
    }
    return payload


def build_empty_paper_dashboard(conn, project_root: Path, last_error: str = "") -> dict:
    payload = empty_paper_dashboard_payload(conn, last_error=last_error)
    run = payload.get("run", {})
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
        "last_error": last_error,
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
