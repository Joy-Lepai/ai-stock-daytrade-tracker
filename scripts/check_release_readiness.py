#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import signal
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable


DEFAULT_BASE_URL = "https://stock.letslepai.com"
DEFAULT_GIT_TIMEOUT_SECONDS = 12.0


@dataclass(frozen=True)
class ReleaseState:
    head: str
    origin: str
    ahead_count: int
    dirty: bool
    status_line: str
    repo_path: str = ""
    remote_url: str = ""
    public_runtime: str = ""
    public_tracker: str = ""
    unpushed_commits: tuple[str, ...] = ()


@dataclass(frozen=True)
class ReleaseCheck:
    name: str
    ok: bool
    detail: str


class ReleaseReadinessError(RuntimeError):
    pass


def git_output(args: list[str], *, runner: Callable[..., str] = subprocess.check_output, timeout: Optional[float] = None) -> str:
    try:
        return runner(
            ["git", *args],
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=timeout if timeout is not None else git_command_timeout(),
        ).strip()
    except subprocess.CalledProcessError as exc:
        raise ReleaseReadinessError(describe_git_failure(args, exc)) from exc
    except subprocess.TimeoutExpired as exc:
        raise ReleaseReadinessError(describe_git_timeout(args, exc)) from exc
    except OSError as exc:
        raise ReleaseReadinessError(f"`git {' '.join(args)}` failed: {exc}") from exc


def git_command_timeout() -> float:
    try:
        return max(float(os.getenv("STOCK_RELEASE_GIT_TIMEOUT_SECONDS", DEFAULT_GIT_TIMEOUT_SECONDS)), 1.0)
    except (TypeError, ValueError):
        return DEFAULT_GIT_TIMEOUT_SECONDS


def optional_git_output(args: list[str], *, runner: Callable[..., str] = subprocess.check_output, default: str = "") -> str:
    try:
        return git_output(args, runner=runner)
    except ReleaseReadinessError:
        return default


def describe_git_failure(args: list[str], exc: subprocess.CalledProcessError) -> str:
    command = "git " + " ".join(args)
    code = int(exc.returncode or 0)
    if code < 0:
        try:
            reason = signal.Signals(-code).name
        except ValueError:
            reason = f"signal {-code}"
        return f"`{command}` failed: {reason}"
    return f"`{command}` failed: exit status {code}"


def describe_git_timeout(args: list[str], exc: subprocess.TimeoutExpired) -> str:
    command = "git " + " ".join(args)
    timeout = exc.timeout if exc.timeout is not None else git_command_timeout()
    return f"`{command}` timed out after {timeout:g}s; Git status may be scanning a large or locked worktree"


def load_release_state(
    *,
    base_url: str = DEFAULT_BASE_URL,
    timeout: float = 12.0,
    runner: Callable[..., str] = subprocess.check_output,
    fetch_public: bool = True,
) -> ReleaseState:
    head = git_output(["rev-parse", "HEAD"], runner=runner)
    origin = git_output(["rev-parse", "origin/main"], runner=runner)
    repo_path = git_output(["rev-parse", "--show-toplevel"], runner=runner)
    remote_url = load_remote_url(runner=runner)
    ahead_count = load_ahead_count(head, origin, runner=runner)
    if ahead_count < 0:
        status_with_ahead = optional_git_output(["status", "-sb", "--untracked-files=no"], runner=runner, default="")
        ahead_from_status = parse_ahead_count(status_with_ahead.splitlines()[0] if status_with_ahead else "")
        if ahead_from_status > 0:
            ahead_count = ahead_from_status
    unpushed_commits = load_unpushed_commits(ahead_count, runner=runner)
    try:
        status = git_output(["status", "-sb", "--no-ahead-behind", "--untracked-files=no"], runner=runner)
        dirty = is_worktree_dirty(status)
        first_line = status.splitlines()[0] if status else ""
    except ReleaseReadinessError as exc:
        dirty = worktree_dirty_fallback(runner=runner)
        first_line = f"## main...origin/main [status unavailable: {exc}]"
    public_runtime = ""
    public_tracker = ""
    if fetch_public:
        try:
            payload = fetch_system_version(base_url, timeout=timeout)
            public_runtime = str(((payload.get("runtime") or {}).get("commit")) or "")
            public_tracker = str(((payload.get("tracker_html") or {}).get("commit")) or "")
        except Exception as exc:
            public_runtime = f"ERROR:{exc}"
    return ReleaseState(
        head=head,
        origin=origin,
        ahead_count=ahead_count,
        dirty=dirty,
        status_line=first_line,
        repo_path=repo_path,
        remote_url=remote_url,
        public_runtime=public_runtime,
        public_tracker=public_tracker,
        unpushed_commits=tuple(unpushed_commits),
    )


def parse_ahead_count(status_line: str) -> int:
    value = (status_line or "").strip()
    if re.fullmatch(r"-?\d+", value):
        return int(value)
    match = re.search(r"\bahead\s+(\d+)", status_line or "")
    return int(match.group(1)) if match else 0


def load_ahead_count(head: str, origin: str, *, runner: Callable[..., str] = subprocess.check_output) -> int:
    if head == origin:
        return 0
    raw = optional_git_output(["rev-list", "--count", "origin/main..HEAD"], runner=runner, default="-1")
    try:
        parsed = int(raw)
        if parsed >= 0:
            return parsed
    except (TypeError, ValueError):
        pass
    tracking = optional_git_output(
        ["for-each-ref", "--format=%(upstream:track)", "refs/heads/main"],
        runner=runner,
        default="",
    )
    parsed_tracking = parse_ahead_count(tracking)
    return parsed_tracking if parsed_tracking > 0 else -1


def load_unpushed_commits(ahead_count: int, *, runner: Callable[..., str] = subprocess.check_output) -> list[str]:
    if ahead_count <= 0:
        return []
    raw = optional_git_output(
        ["log", "--oneline", "--max-count=10", "origin/main..HEAD"],
        runner=runner,
        default="",
    )
    return [line.strip() for line in raw.splitlines() if line.strip()]


def load_remote_url(*, runner: Callable[..., str] = subprocess.check_output) -> str:
    try:
        return git_output(["config", "--get", "remote.origin.url"], runner=runner)
    except Exception:
        return ""


def is_worktree_dirty(status_output: str) -> bool:
    lines = [line for line in (status_output or "").splitlines()[1:] if line.strip()]
    return bool(lines)


def worktree_dirty_fallback(
    *,
    runner: Callable[..., str] = subprocess.check_output,
) -> bool:
    try:
        status = git_output(["status", "--porcelain=v1", "--untracked-files=no"], runner=runner)
    except ReleaseReadinessError:
        return True
    return bool(status.strip())


def fetch_system_version(base_url: str, *, timeout: float = 12.0) -> dict[str, Any]:
    url = base_url.rstrip("/") + "/api/system/version"
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(str(exc.reason)) from exc


def commit_matches(actual: str, expected: str) -> bool:
    if not actual or not expected or actual.startswith("ERROR:"):
        return False
    return actual.startswith(expected[: len(actual)]) or expected.startswith(actual[: len(expected)])


def evaluate_release_state(state: ReleaseState) -> list[ReleaseCheck]:
    pushed = state.head == state.origin and state.ahead_count in {0, -1}
    ahead_detail = "unknown" if state.ahead_count < 0 else str(state.ahead_count)
    checks = [
        ReleaseCheck("worktree clean", not state.dirty, "clean" if not state.dirty else "有未提交或未 staged 的修改"),
        ReleaseCheck(
            "local pushed to origin/main",
            pushed,
            f"local={short(state.head)} origin={short(state.origin)} ahead={ahead_detail}",
        ),
    ]
    if state.public_runtime:
        checks.append(
            ReleaseCheck(
                "public runtime matches local HEAD",
                commit_matches(state.public_runtime, state.head),
                f"public={short(state.public_runtime)} local={short(state.head)}",
            )
        )
    if state.public_tracker and not state.public_tracker.startswith("ERROR:"):
        checks.append(
            ReleaseCheck(
                "public tracker matches runtime",
                commit_matches(state.public_tracker, state.public_runtime),
                f"tracker={short(state.public_tracker)} runtime={short(state.public_runtime)}",
            )
        )
    return checks


def recommended_next_action(state: ReleaseState) -> str:
    if state.dirty:
        return "先 commit 目前修改，再檢查是否需要 push。"
    if state.head != state.origin or state.ahead_count > 0:
        return "本機 commit 尚未推到 GitHub：請先在 GitHub Desktop 選 Repository → Push，成功後再到 Render Deploy latest commit。"
    if state.public_runtime.startswith("ERROR:"):
        return "無法讀取公開站版本：請確認網站是否啟動，再跑公開站驗收。"
    if state.public_runtime and not commit_matches(state.public_runtime, state.head):
        return "GitHub 已是最新版，但公開站仍舊版：請在 Render 執行 Deploy latest commit。"
    if state.public_tracker and not commit_matches(state.public_tracker, state.public_runtime):
        return "runtime 與 tracker HTML 不一致：請執行完整刷新或重新部署。"
    return "版本鏈路看起來一致，可以執行公開站功能驗收。"


def release_steps(state: ReleaseState) -> list[str]:
    if state.dirty:
        return [
            "先確認這些修改都屬於本次任務，跑測試後 commit。",
            "commit 完成後重跑 release readiness。",
            "確認本機乾淨後，再推送 GitHub 與部署 Render。",
        ]
    if state.head != state.origin or state.ahead_count > 0:
        repo_hint = f"確認 GitHub Desktop 目前 repo 是：{state.repo_path}" if state.repo_path else "確認 GitHub Desktop 目前開的是本專案 repo。"
        return [
            repo_hint,
            "在 GitHub Desktop 執行 Repository → Push。",
            "推送後重跑本腳本，確認 origin/main 等於 local HEAD。",
            "origin/main 對齊後，再到 Render 執行 Deploy latest commit。",
        ]
    if state.public_runtime.startswith("ERROR:"):
        return [
            "先打開公開站或 Render Logs，確認服務已啟動。",
            "服務恢復後重跑本腳本讀取 /api/system/version。",
            "若仍讀不到公開版本，再檢查網域、DNS 或 Render runtime。",
        ]
    if state.public_runtime and not commit_matches(state.public_runtime, state.head):
        return [
            "在 Render 服務頁執行 Manual Deploy → Deploy latest commit。",
            "等待 build 與 deploy 成功，確認 runtime commit 變成 local HEAD。",
            "部署成功後跑公開站驗收腳本與營運健康檢查。",
        ]
    if state.public_tracker and not commit_matches(state.public_tracker, state.public_runtime):
        return [
            "runtime 已是新版，但 tracker HTML 還是舊版。",
            "執行完整刷新 POST /refresh，重建 dashboard HTML。",
            "刷新後確認 tracker commit 與 runtime commit 一致。",
        ]
    return [
        "版本已對齊，執行 scripts/verify_public_deployment.py 做公開功能驗收。",
        "再執行 scripts/check_operational_health.py 確認今日資料與刷新層。",
        "驗收通過後才用 dashboard 當作今天的看盤工具。",
    ]


def release_report_payload(state: ReleaseState, checks: list[ReleaseCheck]) -> dict[str, Any]:
    failures = [check for check in checks if not check.ok]
    push_guidance = push_method_guidance(state)
    return {
        "status": "ok" if not failures else "blocked",
        "local_head": state.head,
        "local_head_short": short(state.head),
        "origin_main": state.origin,
        "origin_main_short": short(state.origin),
        "repo_path": state.repo_path,
        "remote_url": state.remote_url,
        "push_method": push_guidance,
        "ahead_count": state.ahead_count,
        "unpushed_commits": list(state.unpushed_commits),
        "worktree_clean": not state.dirty,
        "status_line": state.status_line,
        "public_runtime": state.public_runtime,
        "public_tracker": state.public_tracker,
        "checks": [
            {"name": check.name, "ok": check.ok, "detail": check.detail}
            for check in checks
        ],
        "failed_checks": [check.name for check in failures],
        "next_action": recommended_next_action(state),
        "github_desktop_repo_hint": f"GitHub Desktop 應開啟：{state.repo_path}" if state.repo_path else "GitHub Desktop 應開啟本專案 repo",
        "release_steps": release_steps(state),
        "can_push": (not state.dirty) and (state.head != state.origin or state.ahead_count > 0),
        "can_deploy_render": (not state.dirty) and state.head == state.origin and state.ahead_count in {0, -1},
        "can_trust_public": not failures,
    }


def push_method_guidance(state: ReleaseState) -> dict[str, str]:
    remote = str(state.remote_url or "")
    if remote.startswith("https://"):
        return {
            "recommended": "GitHub Desktop",
            "reason": "origin 使用 HTTPS；若 CLI 沒有 GitHub 認證，git push 會失敗，建議用已登入的 GitHub Desktop 按 Push。",
        }
    if remote.startswith("git@"):
        return {
            "recommended": "CLI git push",
            "reason": "origin 使用 SSH；若本機 SSH key 已設定，可直接使用 git push origin main。",
        }
    if remote:
        return {
            "recommended": "依 remote 設定推送",
            "reason": f"origin remote={remote}",
        }
    return {
        "recommended": "GitHub Desktop",
        "reason": "找不到 remote.origin.url；請先確認 GitHub Desktop 開啟正確 repo。",
    }


def short(value: str, length: int = 12) -> str:
    return str(value or "-")[:length]


def print_release_report(state: ReleaseState, checks: list[ReleaseCheck]) -> int:
    failures = 0
    payload = release_report_payload(state, checks)
    print("Release readiness")
    print(f"local HEAD:   {state.head}")
    print(f"origin/main:  {state.origin}")
    if state.repo_path:
        print(f"repo path:    {state.repo_path}")
    if state.remote_url:
        print(f"remote:       {state.remote_url}")
    print(f"status:       {state.status_line or '-'}")
    if state.public_runtime:
        print(f"public:       runtime={state.public_runtime} tracker={state.public_tracker or '-'}")
    print()
    for check in checks:
        mark = "PASS" if check.ok else "FAIL"
        print(f"[{mark}] {check.name}: {check.detail}")
        failures += 0 if check.ok else 1
    print()
    print("Next action:", recommended_next_action(state))
    print("Operator gate:")
    print(f"- push_method: {payload['push_method']['recommended']} ({payload['push_method']['reason']})")
    print(f"- can_push: {'yes' if payload['can_push'] else 'no'}")
    print(f"- can_deploy_render: {'yes' if payload['can_deploy_render'] else 'no'}")
    print(f"- can_trust_public: {'yes' if payload['can_trust_public'] else 'no'}")
    print("Release steps:")
    for index, item in enumerate(release_steps(state), start=1):
        print(f"{index}. {item}")
    return failures


def load_failure_payload(error: Exception) -> dict[str, Any]:
    return {
        "status": "blocked",
        "failed_checks": ["local Git state readable"],
        "error": str(error),
        "next_action": "無法讀取本機 Git 狀態；請先確認此目錄是正確 repo，或重跑 git status 後再驗收。",
        "can_push": False,
        "can_deploy_render": False,
        "can_trust_public": False,
    }


def print_load_failure(error: Exception) -> int:
    print("Release readiness")
    print()
    print(f"[FAIL] local Git state readable: {error}")
    print()
    print("Next action:", load_failure_payload(error)["next_action"])
    return 1


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Check local Git, origin/main, and public runtime release readiness.")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--timeout", type=float, default=12.0)
    parser.add_argument("--no-public", action="store_true", help="Skip public /api/system/version probe.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable readiness JSON.")
    args = parser.parse_args(argv)
    try:
        state = load_release_state(base_url=args.base_url, timeout=args.timeout, fetch_public=not args.no_public)
    except ReleaseReadinessError as exc:
        if args.json:
            print(json.dumps(load_failure_payload(exc), ensure_ascii=False, indent=2))
            return 1
        return print_load_failure(exc)
    checks = evaluate_release_state(state)
    if args.json:
        print(json.dumps(release_report_payload(state, checks), ensure_ascii=False, indent=2))
        return 1 if any(not check.ok for check in checks) else 0
    return 1 if print_release_report(state, checks) else 0


if __name__ == "__main__":
    raise SystemExit(main())
