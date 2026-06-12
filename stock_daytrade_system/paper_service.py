from __future__ import annotations

import os
import subprocess
from pathlib import Path

from stock_daytrade_system.paper_broker import PAPER_ENGINE_VERSION, paper_dashboard_payload, paper_performance


def build_paper_dashboard(conn, project_root: Path) -> dict:
    payload = paper_dashboard_payload(conn)
    payload["debug"] = {
        "app_version": _current_commit_hash(project_root),
        "engine_version": PAPER_ENGINE_VERSION,
        "generated_at": payload["generated_at"],
        "refresh_interval": payload["refresh_interval_seconds"],
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
