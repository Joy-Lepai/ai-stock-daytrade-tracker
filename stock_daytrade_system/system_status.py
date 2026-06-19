from __future__ import annotations

import html
import re
from datetime import datetime
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

from stock_daytrade_system.app_version import deployment_status
from stock_daytrade_system.db import connect, default_db_path, refresh_state_rows


SYSTEM_STATUS_VERSION = "system_status_v1_version_freshness_2026-06-19"
TAIPEI = ZoneInfo("Asia/Taipei")


def build_system_version_payload(project_root: Path, report_dir: Path) -> dict:
    generated_at = datetime.now(TAIPEI)
    runtime = deployment_status(project_root)
    tracker = _tracker_html_status(report_dir)
    db_status = _db_freshness_status(project_root)
    runtime_commit = str(runtime.get("commit") or "unknown")
    tracker_commit = str(tracker.get("commit") or "unknown")
    tracker_known = tracker_commit not in {"", "-", "unknown"}
    runtime_known = runtime_commit not in {"", "-", "unknown"}
    runtime_matches_tracker = bool(runtime_known and tracker_known and runtime_commit == tracker_commit)
    tracker_missing = not bool(tracker.get("file"))
    warnings = []
    if tracker_missing:
        warnings.append("尚未產生 tracker HTML。")
    elif not runtime_matches_tracker:
        warnings.append("runtime commit 與 tracker HTML commit 不一致，公開 dashboard 可能仍是舊靜態檔。")
    if not db_status.get("latest_data_at"):
        warnings.append("DB 尚無最新盤中或全市場資料時間。")
    return {
        "api_status": "ok",
        "version": SYSTEM_STATUS_VERSION,
        "generated_at": generated_at.isoformat(timespec="seconds"),
        "runtime": runtime,
        "tracker_html": tracker,
        "db": db_status,
        "consistency": {
            "runtime_matches_tracker": runtime_matches_tracker,
            "runtime_commit": runtime_commit,
            "tracker_commit": tracker_commit,
            "tracker_missing": tracker_missing,
            "is_ready": runtime_matches_tracker and bool(db_status.get("latest_data_at")),
            "warnings": warnings,
        },
    }


def _tracker_html_status(report_dir: Path) -> dict:
    latest = _latest_tracker_file(report_dir)
    if latest is None:
        return {
            "file": None,
            "commit": "unknown",
            "commit_source": "missing_tracker_html",
            "dashboard_generated_at": None,
            "modified_at": None,
        }
    try:
        content = latest.read_text(encoding="utf-8")
    except OSError as exc:
        return {
            "file": latest.name,
            "commit": "unknown",
            "commit_source": "read_error",
            "dashboard_generated_at": None,
            "modified_at": None,
            "error": str(exc),
        }
    modified_at = datetime.fromtimestamp(latest.stat().st_mtime, TAIPEI).isoformat(timespec="seconds")
    return {
        "file": latest.name,
        "commit": _extract_debug_value(content, "app version / commit") or "unknown",
        "commit_source": "tracker_html_debug_block",
        "dashboard_generated_at": _extract_debug_value(content, "dashboard generated_at"),
        "modified_at": modified_at,
    }


def _db_freshness_status(project_root: Path) -> dict:
    with connect(default_db_path(project_root)) as conn:
        intraday = conn.execute(
            "SELECT MAX(date) AS data_date, MAX(captured_at) AS captured_at, COUNT(DISTINCT symbol) AS symbols FROM intraday_snapshots"
        ).fetchone()
        full_market = conn.execute(
            "SELECT MAX(date) AS data_date, MAX(captured_at) AS captured_at, COUNT(DISTINCT symbol) AS symbols FROM tw_full_market_snapshots"
        ).fetchone()
        recommendations = conn.execute(
            "SELECT MAX(date) AS data_date, COUNT(*) AS total FROM recommendations"
        ).fetchone()
        refresh_rows = [dict(row) for row in refresh_state_rows(conn)]
    latest_data_at = _max_text(intraday["captured_at"] if intraday else None, full_market["captured_at"] if full_market else None)
    data_date = _max_text(intraday["data_date"] if intraday else None, full_market["data_date"] if full_market else None)
    return {
        "data_date": data_date,
        "latest_data_at": latest_data_at,
        "intraday": {
            "data_date": intraday["data_date"] if intraday else None,
            "latest_data_at": intraday["captured_at"] if intraday else None,
            "symbols": int(intraday["symbols"] or 0) if intraday else 0,
        },
        "full_market": {
            "data_date": full_market["data_date"] if full_market else None,
            "latest_data_at": full_market["captured_at"] if full_market else None,
            "symbols": int(full_market["symbols"] or 0) if full_market else 0,
        },
        "recommendations": {
            "data_date": recommendations["data_date"] if recommendations else None,
            "total": int(recommendations["total"] or 0) if recommendations else 0,
        },
        "refresh_state": refresh_rows,
    }


def _latest_tracker_file(report_dir: Path) -> Optional[Path]:
    files = sorted(report_dir.glob("*-tracker.html"), reverse=True)
    return files[0] if files else None


def _extract_debug_value(content: str, label: str) -> Optional[str]:
    pattern = re.compile(rf"<strong>{re.escape(label)}:</strong>\s*([^<]+)")
    match = pattern.search(content)
    if not match:
        return None
    value = html.unescape(match.group(1)).strip()
    return value or None


def _max_text(*values: Optional[str]) -> Optional[str]:
    clean = [str(value) for value in values if value]
    return max(clean) if clean else None
