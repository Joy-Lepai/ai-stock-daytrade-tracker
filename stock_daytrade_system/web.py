from __future__ import annotations

import json
import secrets
import urllib.parse
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Dict, Optional

from stock_daytrade_system.auth import AuthConfig, load_auth_config, verify_password
from stock_daytrade_system.db import backtest_summary, connect, default_db_path, latest_candidates, latest_symbol_score, symbol_history


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_AUTH = PROJECT_ROOT / "config" / "auth.json"
DEFAULT_REPORT_DIR = PROJECT_ROOT / "reports"
SESSION_COOKIE = "ai_stock_session"


class WebApp:
    def __init__(self, auth_config: Optional[AuthConfig], report_dir: Path, require_auth: bool = False) -> None:
        self.auth_config = auth_config
        self.report_dir = report_dir
        self.require_auth = require_auth
        self.sessions: Dict[str, str] = {}

    def create_session(self, username: str) -> str:
        token = secrets.token_urlsafe(32)
        self.sessions[token] = username
        return token

    def is_authenticated(self, token: Optional[str]) -> bool:
        if not self.require_auth:
            return True
        return token in self.sessions if token else False

    def destroy_session(self, token: Optional[str]) -> None:
        if token:
            self.sessions.pop(token, None)


def serve(
    host: str = "127.0.0.1",
    port: int = 8000,
    auth_path: Path = DEFAULT_AUTH,
    report_dir: Path = DEFAULT_REPORT_DIR,
    require_auth: bool = False,
) -> None:
    auth_config = load_auth_config(auth_path) if require_auth else None
    app = WebApp(auth_config, report_dir, require_auth=require_auth)

    class Handler(StockWebHandler):
        web_app = app

    server = ThreadingHTTPServer((host, port), Handler)
    print(f"Serving stock tracker on http://{host}:{port}/")
    server.serve_forever()


class StockWebHandler(BaseHTTPRequestHandler):
    web_app: WebApp

    def do_GET(self) -> None:
        path = urllib.parse.urlparse(self.path).path
        if path == "/login":
            if not self.web_app.require_auth:
                self._redirect("/dashboard")
                return
            self._send_html(render_login_page())
            return
        if path == "/logout":
            self.web_app.destroy_session(self._session_token())
            self._redirect("/login" if self.web_app.require_auth else "/dashboard", clear_cookie=True)
            return
        if not self._authenticated():
            self._redirect("/login")
            return
        if path in {"/", "/dashboard"}:
            self._send_html(self._dashboard_html())
            return
        if path.startswith("/symbol/"):
            self._send_html(self._symbol_html(path.removeprefix("/symbol/")))
            return
        if path == "/api/candidates":
            with connect(default_db_path(PROJECT_ROOT)) as conn:
                self._send_json([dict(row) for row in latest_candidates(conn)])
            return
        if path.startswith("/api/symbol/"):
            self._send_json(self._symbol_payload(path.removeprefix("/api/symbol/")))
            return
        if path == "/api/backtest":
            with connect(default_db_path(PROJECT_ROOT)) as conn:
                self._send_json(backtest_summary(conn))
            return
        if path.startswith("/reports/"):
            self._send_report(path.removeprefix("/reports/"))
            return
        self._send_not_found()

    def do_POST(self) -> None:
        path = urllib.parse.urlparse(self.path).path
        if path == "/login":
            if not self.web_app.require_auth:
                self._redirect("/dashboard")
                return
            self._handle_login()
            return
        if not self._authenticated():
            self._redirect("/login")
            return
        if path == "/refresh":
            self._handle_refresh()
            return
        self._send_not_found()

    def _handle_login(self) -> None:
        body = self.rfile.read(int(self.headers.get("Content-Length", "0"))).decode("utf-8")
        data = urllib.parse.parse_qs(body)
        username = data.get("username", [""])[0]
        password = data.get("password", [""])[0]
        auth = self.web_app.auth_config
        if auth is None:
            self._redirect("/dashboard")
            return
        if username == auth.username and verify_password(password, auth):
            token = self.web_app.create_session(username)
            self.send_response(HTTPStatus.SEE_OTHER)
            self.send_header("Location", "/dashboard")
            self.send_header("Set-Cookie", _session_cookie(token))
            self.end_headers()
            return
        self._send_html(render_login_page("帳號或密碼錯誤"), status=HTTPStatus.UNAUTHORIZED)

    def _handle_refresh(self) -> None:
        from stock_daytrade_system.cli import DEFAULT_CONFIG, run_tracker

        run_tracker(DEFAULT_CONFIG, self.web_app.report_dir, "6mo", "1d", "5m", 3)
        self._redirect("/dashboard")

    def _dashboard_html(self) -> str:
        latest = latest_tracker_file(self.web_app.report_dir)
        if latest is None:
            return render_shell(
                "<p class=\"empty\">尚未產生追蹤器資料。</p>",
                active_file=None,
                show_logout=self.web_app.require_auth,
            )
        html = latest.read_text(encoding="utf-8")
        body = _extract_body(html)
        return render_shell(body, active_file=latest.name, extra_css=_extract_style(html), show_logout=self.web_app.require_auth)

    def _symbol_html(self, raw_symbol: str) -> str:
        symbol = urllib.parse.unquote(raw_symbol)
        payload = self._symbol_payload(symbol)
        if not payload.get("latest"):
            return render_shell("<p class=\"empty\">找不到個股資料。</p>", None, show_logout=self.web_app.require_auth)
        latest = payload["latest"]
        history_rows = "".join(
            "<tr>"
            f"<td>{_escape(str(item.get('date', '')))}</td>"
            f"<td>{_escape(str(item.get('grade', '')))}</td>"
            f"<td>{_escape(str(item.get('bullish_score', '')))}</td>"
            f"<td>{_escape(str(item.get('risk_score', '')))}</td>"
            f"<td>{_escape(str(item.get('outcome', '') or ''))}</td>"
            f"<td>{_escape(str(item.get('return_pct', '') or ''))}</td>"
            "</tr>"
            for item in payload["history"]
        )
        content = f"""
        <main>
          <h1>{_escape(str(latest.get('name', symbol)))} / {_escape(symbol)}</h1>
          <p class="meta">分級：{_escape(str(latest.get('grade', '-')))} ｜ 多方分數：{_escape(str(latest.get('bullish_score', '-')))} ｜ 風險分數：{_escape(str(latest.get('risk_score', '-')))}</p>
          <div class="table-wrap">
            <table><tbody>
              <tr><td>收盤/現價</td><td>{_escape(str(latest.get('close', '-')))}</td></tr>
              <tr><td>今日漲幅</td><td>{_escape(str(latest.get('change_pct', '-')))}%</td></tr>
              <tr><td>量比</td><td>{_escape(str(latest.get('intraday_volume_ratio', latest.get('volume_ratio', '-'))))}</td></tr>
              <tr><td>VWAP</td><td>{_escape(str(latest.get('vwap', '-')))}</td></tr>
              <tr><td>站上VWAP</td><td>{'是' if latest.get('above_vwap') else '否'}</td></tr>
              <tr><td>突破昨日高點</td><td>{'是' if latest.get('break_prev_high') else '否'}</td></tr>
              <tr><td>突破5日高點</td><td>{'是' if latest.get('break_5d_high') else '否'}</td></tr>
              <tr><td>突破10日高點</td><td>{'是' if latest.get('break_10d_high') else '否'}</td></tr>
            </tbody></table>
          </div>
          <h2>推薦與回測紀錄</h2>
          <div class="table-wrap">
            <table><thead><tr><th>日期</th><th>分級</th><th>多方分數</th><th>風險分數</th><th>結果</th><th>報酬率</th></tr></thead><tbody>
              {history_rows or '<tr><td colspan="6">尚無推薦紀錄。</td></tr>'}
            </tbody></table>
          </div>
        </main>
        """
        return render_shell(content, active_file=f"{symbol} detail", show_logout=self.web_app.require_auth)

    def _symbol_payload(self, symbol: str) -> dict:
        symbol = urllib.parse.unquote(symbol)
        with connect(default_db_path(PROJECT_ROOT)) as conn:
            latest = latest_symbol_score(conn, symbol)
            history = symbol_history(conn, symbol)
            return {
                "latest": dict(latest) if latest else None,
                "history": [dict(row) for row in history],
            }

    def _send_report(self, name: str) -> None:
        safe_name = Path(name).name
        path = self.web_app.report_dir / safe_name
        if not path.exists() or not path.is_file():
            self._send_not_found()
            return
        content_type = "text/html; charset=utf-8" if path.suffix == ".html" else "text/plain; charset=utf-8"
        data = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self._send_no_cache_headers()
        self.end_headers()
        self.wfile.write(data)

    def _authenticated(self) -> bool:
        return self.web_app.is_authenticated(self._session_token())

    def _session_token(self) -> Optional[str]:
        cookie = SimpleCookie(self.headers.get("Cookie"))
        morsel = cookie.get(SESSION_COOKIE)
        return morsel.value if morsel else None

    def _redirect(self, location: str, clear_cookie: bool = False) -> None:
        self.send_response(HTTPStatus.SEE_OTHER)
        self.send_header("Location", location)
        self._send_no_cache_headers()
        if clear_cookie:
            self.send_header("Set-Cookie", f"{SESSION_COOKIE}=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax")
        self.end_headers()

    def _send_html(self, html: str, status: HTTPStatus = HTTPStatus.OK) -> None:
        data = html.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self._send_no_cache_headers()
        self.end_headers()
        self.wfile.write(data)

    def _send_json(self, payload, status: HTTPStatus = HTTPStatus.OK) -> None:
        data = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self._send_no_cache_headers()
        self.end_headers()
        self.wfile.write(data)

    def _send_no_cache_headers(self) -> None:
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")

    def _send_not_found(self) -> None:
        self._send_html(render_shell("<p class=\"empty\">找不到頁面。</p>", None, show_logout=self.web_app.require_auth), HTTPStatus.NOT_FOUND)

    def log_message(self, format: str, *args) -> None:
        return


def latest_tracker_file(report_dir: Path) -> Optional[Path]:
    files = sorted(report_dir.glob("*-tracker.html"), reverse=True)
    return files[0] if files else None


def render_login_page(error: str = "") -> str:
    error_html = f"<div class=\"error\">{_escape(error)}</div>" if error else ""
    return f"""<!doctype html>
<html lang="zh-Hant">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>登入股票當沖系統</title>
  <style>{base_css()}</style>
</head>
<body class="login-body">
  <main class="login-panel">
    <h1>股票當沖系統</h1>
    {error_html}
    <form method="post" action="/login">
      <label>帳號<input name="username" autocomplete="username" required autofocus></label>
      <label>密碼<input name="password" type="password" autocomplete="current-password" required></label>
      <button type="submit">登入</button>
    </form>
  </main>
</body>
</html>"""


def render_shell(content: str, active_file: Optional[str], extra_css: str = "", show_logout: bool = False) -> str:
    file_text = f"<span>{_escape(active_file)}</span>" if active_file else "<span>無資料檔</span>"
    logout_link = '<a href="/logout">登出</a>' if show_logout else ""
    refresh_interval_seconds = 300
    refresh_start_minutes = 7 * 60
    refresh_end_minutes = 13 * 60 + 45
    return f"""<!doctype html>
<html lang="zh-Hant">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>股票當沖追蹤器</title>
  <style>{extra_css}{base_css()}</style>
</head>
<body>
  <nav class="topbar">
    <strong>股票當沖追蹤器</strong>
    <div class="topbar-actions">
      {file_text}
      <span id="refresh-status" class="refresh-status" data-interval="{refresh_interval_seconds}" data-start="{refresh_start_minutes}" data-end="{refresh_end_minutes}">交易時段自動更新</span>
      <form method="post" action="/refresh"><button type="submit">更新</button></form>
      {logout_link}
    </div>
  </nav>
  {content}
  <script>
    (() => {{
      const status = document.getElementById("refresh-status");
      if (!status) return;
      const intervalSeconds = Number(status.dataset.interval || "300");
      const startMinutes = Number(status.dataset.start || "420");
      const endMinutes = Number(status.dataset.end || "825");
      let remaining = intervalSeconds;

      const minutesSinceMidnight = (date) => date.getHours() * 60 + date.getMinutes();
      const isWeekday = (date) => date.getDay() >= 1 && date.getDay() <= 5;
      const isActiveWindow = (date) => {{
        const minutes = minutesSinceMidnight(date);
        return isWeekday(date) && minutes >= startMinutes && minutes <= endMinutes;
      }};
      const formatTime = (minutes) => {{
        const hour = String(Math.floor(minutes / 60)).padStart(2, "0");
        const minute = String(minutes % 60).padStart(2, "0");
        return `${{hour}}:${{minute}}`;
      }};
      const nextActiveLabel = (date) => {{
        const minutes = minutesSinceMidnight(date);
        if (isWeekday(date) && minutes < startMinutes) return `今天 ${{formatTime(startMinutes)}}`;
        return "下一個交易日 07:00";
      }};

      const renderCountdown = () => {{
        const now = new Date();
        if (!isActiveWindow(now)) {{
          status.textContent = `自動更新暫停，${{nextActiveLabel(now)}} 啟動`;
          return;
        }}
        const minutes = Math.floor(remaining / 60);
        const seconds = String(remaining % 60).padStart(2, "0");
        status.textContent = `07:00-13:45 每 5 分鐘自動更新，倒數 ${{minutes}}:${{seconds}}`;
      }};

      const refreshDashboard = async () => {{
        if (!isActiveWindow(new Date())) {{
          remaining = intervalSeconds;
          renderCountdown();
          return;
        }}
        status.textContent = "正在更新資料...";
        try {{
          const response = await fetch("/refresh", {{
            method: "POST",
            credentials: "same-origin",
            headers: {{ "X-Requested-With": "fetch" }},
          }});
          if (!response.ok && response.status !== 0) throw new Error(`HTTP ${{response.status}}`);
          window.location.href = "/dashboard";
        }} catch (error) {{
          remaining = intervalSeconds;
          status.textContent = "自動更新失敗，請按更新";
        }}
      }};

      renderCountdown();
      window.setInterval(() => {{
        if (!isActiveWindow(new Date())) {{
          remaining = intervalSeconds;
          renderCountdown();
          return;
        }}
        remaining -= 1;
        if (remaining <= 0) {{
          refreshDashboard();
          return;
        }}
        renderCountdown();
      }}, 1000);
    }})();
  </script>
</body>
</html>"""


def base_css() -> str:
    return """
    :root { --ink:#18202a; --muted:#667085; --line:#d7dde5; --bg:#f5f7fa; --panel:#fff; --accent:#175cd3; }
    * { box-sizing:border-box; }
    body { margin:0; background:var(--bg); color:var(--ink); font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; }
    .topbar { position:sticky; top:0; z-index:10; display:flex; align-items:center; justify-content:space-between; gap:16px; padding:10px 18px; background:#fff; border-bottom:1px solid var(--line); }
    .topbar-actions { display:flex; align-items:center; gap:10px; color:var(--muted); }
    .topbar form { margin:0; }
    .refresh-status { white-space:nowrap; font-size:13px; color:var(--muted); }
    button, .topbar a { border:1px solid var(--line); background:#fff; color:var(--ink); border-radius:6px; padding:6px 10px; font:inherit; text-decoration:none; cursor:pointer; }
    button:hover, .topbar a:hover { border-color:var(--accent); color:var(--accent); }
    .login-body { min-height:100vh; display:grid; place-items:center; padding:20px; }
    .login-panel { width:min(360px,100%); background:var(--panel); border:1px solid var(--line); border-radius:8px; padding:22px; }
    .login-panel h1 { margin:0 0 18px; font-size:24px; }
    label { display:block; margin:12px 0; font-weight:650; }
    input { width:100%; margin-top:6px; padding:9px 10px; border:1px solid var(--line); border-radius:6px; font:inherit; }
    .login-panel button { width:100%; margin-top:8px; background:var(--accent); color:white; border-color:var(--accent); }
    .error { margin-bottom:10px; padding:8px 10px; border:1px solid #fecdd3; background:#fff1f2; color:#9f1239; border-radius:6px; }
    .empty { margin:28px; padding:16px; background:#fff; border:1px solid var(--line); border-radius:8px; }
    @media (max-width:760px) { .topbar { align-items:flex-start; flex-direction:column; } .topbar-actions { flex-wrap:wrap; } }
    """


def _extract_body(html: str) -> str:
    lower = html.lower()
    start = lower.find("<body>")
    end = lower.rfind("</body>")
    if start == -1 or end == -1:
        return html
    return html[start + len("<body>"):end]


def _extract_style(html: str) -> str:
    lower = html.lower()
    start = lower.find("<style>")
    end = lower.find("</style>", start)
    if start == -1 or end == -1:
        return ""
    return html[start + len("<style>"):end]


def _session_cookie(token: str) -> str:
    return f"{SESSION_COOKIE}={token}; Path=/; HttpOnly; SameSite=Lax"


def _escape(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
