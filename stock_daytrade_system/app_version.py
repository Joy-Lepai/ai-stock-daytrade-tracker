from __future__ import annotations

import os
import subprocess
from pathlib import Path


def current_commit_info(project_root: Path) -> tuple[str, str]:
    for key in ("RENDER_GIT_COMMIT", "SOURCE_VERSION"):
        value = os.environ.get(key)
        if value:
            return value[:12], key
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "--short=12", "HEAD"],
            cwd=project_root,
            check=True,
            capture_output=True,
            text=True,
        )
        return completed.stdout.strip(), "git rev-parse HEAD"
    except Exception:
        return "unknown", "unknown"


def deployment_status(project_root: Path) -> dict:
    commit, source = current_commit_info(project_root)
    return {
        "commit": commit,
        "source": source,
        "render_git_commit": (os.environ.get("RENDER_GIT_COMMIT") or "")[:12],
        "source_version": (os.environ.get("SOURCE_VERSION") or "")[:12],
    }
