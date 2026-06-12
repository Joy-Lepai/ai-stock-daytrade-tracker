from __future__ import annotations

import json
import secrets
import urllib.parse
from datetime import datetime
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Dict, Optional

from stock_daytrade_system.auth import AuthConfig, load_auth_config, verify_password
from stock_daytrade_system.db import (
    backtest_summary,
    connect,
    default_db_path,
    latest_candidates,
    latest_symbol_score,
    latest_us_candidates,
    latest_us_symbol,
    symbol_history,
)
from stock_daytrade_system.market_clock import taiwan_market_session, us_market_session
from stock_daytrade_system.paper_service import build_empty_paper_dashboard, build_paper_dashboard, build_paper_performance
from stock_daytrade_system.us_service import build_us_dashboard_payload


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
        if path == "/us":
            self._redirect("/us/dashboard")
            return
        if path == "/us/dashboard":
            self._send_html(render_us_dashboard_page(show_logout=self.web_app.require_auth))
            return
        if path == "/paper":
            self._redirect("/paper/dashboard")
            return
        if path == "/paper/dashboard":
            self._send_html(render_paper_dashboard_page(show_logout=self.web_app.require_auth))
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
        if path == "/api/us/dashboard":
            self._send_json(self._us_dashboard_payload())
            return
        if path == "/api/us/candidates":
            with connect(default_db_path(PROJECT_ROOT)) as conn:
                self._send_json([dict(row) for row in latest_us_candidates(conn)])
            return
        if path.startswith("/api/us/symbol/"):
            symbol = urllib.parse.unquote(path.removeprefix("/api/us/symbol/")).upper()
            with connect(default_db_path(PROJECT_ROOT)) as conn:
                row = latest_us_symbol(conn, symbol)
                self._send_json(dict(row) if row else {"error": "找不到美股資料", "symbol": symbol}, HTTPStatus.OK if row else HTTPStatus.NOT_FOUND)
            return
        if path == "/api/us/market":
            clock = us_market_session()
            self._send_json(
                {
                    "market": clock.market,
                    "session": clock.session,
                    "status_text": clock.status_text,
                    "refresh_interval_seconds": clock.refresh_interval_seconds,
                    "now_local": clock.now_local.isoformat(timespec="seconds"),
                    "timezone": clock.timezone,
                }
            )
            return
        if path == "/api/paper/dashboard":
            self._send_json(self._paper_dashboard_payload())
            return
        if path == "/api/paper/trades":
            with connect(default_db_path(PROJECT_ROOT)) as conn:
                rows = conn.execute("SELECT * FROM paper_trades ORDER BY COALESCE(entry_time, created_at) DESC, symbol LIMIT 200").fetchall()
                self._send_json([dict(row) for row in rows])
            return
        if path == "/api/paper/positions":
            with connect(default_db_path(PROJECT_ROOT)) as conn:
                rows = conn.execute("SELECT * FROM paper_positions ORDER BY market, symbol").fetchall()
                self._send_json([dict(row) for row in rows])
            return
        if path == "/api/paper/performance":
            with connect(default_db_path(PROJECT_ROOT)) as conn:
                self._send_json(build_paper_performance(conn))
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

    def _us_dashboard_payload(self) -> dict:
        try:
            with connect(default_db_path(PROJECT_ROOT)) as conn:
                return build_us_dashboard_payload(conn, PROJECT_ROOT)
        except Exception as exc:
            clock = us_market_session()
            return {
                "error": "資料暫時無法更新",
                "error_detail": str(exc),
                "market": {
                    "market": "US",
                    "session": clock.session,
                    "status_text": clock.status_text,
                    "timezone": clock.timezone,
                    "now_local": clock.now_local.isoformat(timespec="seconds"),
                    "refresh_interval_seconds": clock.refresh_interval_seconds,
                },
                "data_source": {
                    "source": "Yahoo Finance chart endpoint",
                    "ok": False,
                    "success_count": 0,
                    "failed_symbols": [],
                    "errors": {"dashboard": str(exc)},
                    "last_success_at": None,
                    "updated_at": clock.now_local.isoformat(timespec="seconds"),
                    "next_update_seconds": clock.refresh_interval_seconds,
                },
                "candidates": [],
                "summary": {},
                "debug": {
                    "app_version": "unknown",
                    "model_version": "unavailable",
                    "dashboard_generated_at": clock.now_local.isoformat(timespec="seconds"),
                    "market_session": clock.session,
                    "refresh_interval": clock.refresh_interval_seconds,
                    "candidates_count": 0,
                    "recommendations_count": 0,
                    "data_source_status": "failure",
                },
                "disclaimer": "本系統僅供資料整理與策略回測，不構成投資建議，也不保證獲利。",
            }

    def _paper_dashboard_payload(self) -> dict:
        try:
            with connect(default_db_path(PROJECT_ROOT)) as conn:
                return build_paper_dashboard(conn, PROJECT_ROOT)
        except Exception as exc:
            with connect(default_db_path(PROJECT_ROOT)) as conn:
                return build_empty_paper_dashboard(conn, PROJECT_ROOT, str(exc))

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


def render_us_dashboard_page(show_logout: bool = False) -> str:
    logout_link = '<a href="/logout">登出</a>' if show_logout else ""
    return f"""<!doctype html>
<html lang="zh-Hant">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>美股當沖追蹤器</title>
  <style>{base_css()}{us_dashboard_css()}</style>
</head>
<body>
  <nav class="topbar">
    <strong>股票當沖追蹤器</strong>
    <div class="nav-links">
      <a href="/dashboard">台股追蹤</a>
      <a href="/us/dashboard">美股追蹤</a>
      <a href="/paper/dashboard">虛擬交易</a>
      <a href="/api/backtest">回測</a>
      <a href="#debug">Debug</a>
    </div>
    <div class="topbar-actions">
      <span id="us-refresh-status" class="refresh-status">準備更新</span>
      {logout_link}
    </div>
  </nav>
  <main class="us-page">
    <header class="us-header">
      <div>
        <h1>美股追蹤</h1>
        <p class="meta">大型熱門股 watchlist｜Yahoo Finance｜前端 polling 即時更新</p>
      </div>
      <div id="us-session" class="session-pill">讀取中</div>
    </header>
    <section class="notice">本系統僅供資料整理與策略回測，不構成投資建議，也不保證獲利。</section>
    <section id="us-error" class="warn" hidden>資料暫時無法更新。</section>
    <section class="summary" id="us-summary"></section>
    <h2>資料來源狀態</h2>
    <div class="table-wrap"><table><tbody id="us-source"></tbody></table></div>
    <h2>指數狀態</h2>
    <div class="table-wrap"><table><tbody id="us-market"></tbody></table></div>
    <h2>美股候選股</h2>
    <div class="table-wrap">
      <table class="us-table">
        <thead>
          <tr>
            <th>標的</th><th>價格</th><th>漲跌幅</th><th>成交量</th><th>量比 Volume Ratio</th>
            <th>均價線 VWAP</th><th>盤前高點</th><th>突破</th><th>多方分數 Bullish Score</th>
            <th>風險分數 Risk Score</th><th>分級</th><th>進場狀態 Entry Status</th>
            <th>生命週期 Lifecycle</th><th>理由</th><th>風險理由</th>
          </tr>
        </thead>
        <tbody id="us-candidates"><tr><td colspan="15">讀取中...</td></tr></tbody>
      </table>
    </div>
    <h2 id="debug">Debug</h2>
    <div class="debug-block"><table><tbody id="us-debug"></tbody></table></div>
  </main>
  <script>{us_dashboard_script()}</script>
</body>
</html>"""


def render_paper_dashboard_page(show_logout: bool = False) -> str:
    logout_link = '<a href="/logout">登出</a>' if show_logout else ""
    return f"""<!doctype html>
<html lang="zh-Hant">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>虛擬交易</title>
  <style>{base_css()}{paper_dashboard_css()}</style>
</head>
<body>
  <nav class="topbar">
    <strong>股票當沖追蹤器</strong>
    <div class="nav-links">
      <a href="/dashboard">台股追蹤</a>
      <a href="/us/dashboard">美股追蹤</a>
      <a href="/paper/dashboard">虛擬交易</a>
      <a href="/api/backtest">回測</a>
      <a href="#debug">Debug</a>
    </div>
    <div class="topbar-actions">
      <span id="paper-refresh-status" class="refresh-status">準備更新</span>
      {logout_link}
    </div>
  </nav>
  <main class="paper-page">
    <header class="paper-header">
      <div>
        <h1>虛擬交易 Paper Trading</h1>
        <p class="meta">依照 recommendations / entry_status 模擬買進、持倉、停損、停利與收盤出場。</p>
      </div>
      <div class="session-pill">不串接券商｜不自動下單</div>
    </header>
    <section class="notice">本系統僅供資料整理與策略回測，不構成投資建議，也不保證獲利；本頁不會送出任何真實委託。</section>
    <section id="paper-error" class="warn" hidden>虛擬交易資料暫時無法更新。</section>
    <h2>帳戶總覽</h2>
    <section class="summary" id="paper-accounts"></section>
    <h2>目前持倉</h2>
    <div class="table-wrap"><table><thead><tr><th>市場</th><th>標的</th><th>進場價</th><th>現價</th><th>數量</th><th>未實現損益</th><th>停損</th><th>停利</th></tr></thead><tbody id="paper-positions"></tbody></table></div>
    <h2>今日交易 / 最近交易</h2>
    <div class="table-wrap"><table><thead><tr><th>市場</th><th>標的</th><th>狀態</th><th>分級</th><th>進場狀態</th><th>進場</th><th>出場</th><th>損益</th><th>原因</th></tr></thead><tbody id="paper-trades"></tbody></table></div>
    <h2>跳過紀錄</h2>
    <div class="table-wrap"><table><thead><tr><th>市場</th><th>標的</th><th>訊號</th><th>原因</th><th>時間</th></tr></thead><tbody id="paper-skipped"></tbody></table></div>
    <h2>策略績效</h2>
    <div class="table-wrap" id="paper-performance"></div>
    <h2 id="debug">Debug</h2>
    <div class="debug-block"><table><tbody id="paper-debug"></tbody></table></div>
  </main>
  <script>{paper_dashboard_script()}</script>
</body>
</html>"""


def render_shell(content: str, active_file: Optional[str], extra_css: str = "", show_logout: bool = False) -> str:
    file_text = f"<span>{_escape(active_file)}</span>" if active_file else "<span>無資料檔</span>"
    logout_link = '<a href="/logout">登出</a>' if show_logout else ""
    clock = taiwan_market_session()
    refresh_interval_seconds = clock.refresh_interval_seconds
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
    <div class="nav-links">
      <a href="/dashboard">台股追蹤</a>
      <a href="/us/dashboard">美股追蹤</a>
      <a href="/paper/dashboard">虛擬交易</a>
      <a href="/api/backtest">回測</a>
      <a href="#debug">Debug</a>
    </div>
    <div class="topbar-actions">
      {file_text}
      <span id="refresh-status" class="refresh-status" data-interval="{refresh_interval_seconds}" data-session="{_escape(clock.session)}">上次更新：{_escape(clock.now_local.strftime('%H:%M:%S'))}</span>
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
      let remaining = intervalSeconds;

      const renderCountdown = () => {{
        const minutes = Math.floor(remaining / 60);
        const seconds = String(remaining % 60).padStart(2, "0");
        status.textContent = `上次更新：${{new Date().toLocaleTimeString()}}｜下一次更新 ${{minutes}}:${{seconds}}`;
      }};

      const refreshDashboard = async () => {{
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
    .nav-links { display:flex; align-items:center; gap:8px; flex-wrap:wrap; }
    .nav-links a { color:var(--muted); text-decoration:none; padding:5px 8px; border-radius:6px; }
    .nav-links a:hover { color:var(--accent); background:#eef4ff; }
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


def us_dashboard_css() -> str:
    return """
    .us-page { padding:0 28px 32px; }
    .us-header { display:flex; align-items:flex-start; justify-content:space-between; gap:16px; padding:24px 0 12px; }
    .us-header h1 { margin:0 0 4px; font-size:26px; }
    .session-pill { border:1px solid var(--line); background:#fff; border-radius:999px; padding:7px 12px; font-weight:700; white-space:nowrap; }
    .notice { margin:12px 0; padding:10px 12px; background:#fff7ed; border:1px solid #fed7aa; border-radius:8px; color:#7c2d12; }
    .debug-block { margin-top:10px; padding:10px 12px; background:#f8fafc; border:1px solid var(--line); border-radius:8px; }
    .table-wrap { overflow-x:auto; }
    table { width:100%; border-collapse:collapse; background:#fff; border:1px solid var(--line); border-radius:8px; overflow:hidden; }
    th, td { padding:9px 10px; border-bottom:1px solid var(--line); text-align:left; vertical-align:top; white-space:nowrap; }
    th { background:#eef2f6; color:#344054; font-size:12px; }
    tr:last-child td { border-bottom:0; }
    h2 { margin:26px 0 10px; font-size:18px; }
    .symbol-main { font-weight:750; }
    .symbol-sub { color:var(--muted); font-size:12px; white-space:normal; min-width:220px; }
    .num-up { color:#067647; font-weight:700; }
    .num-down { color:#b42318; font-weight:700; }
    .badge { display:inline-block; min-width:42px; padding:2px 8px; border-radius:999px; text-align:center; font-size:12px; font-weight:700; border:1px solid var(--line); background:#f8fafc; }
    .grade-A { color:#fff; background:#067647; border-color:#067647; }
    .grade-B { color:#175cd3; background:#eff6ff; border-color:#bfdbfe; }
    .grade-C { color:#9a3412; background:#fff7ed; border-color:#fed7aa; }
    .grade-D { color:#475467; background:#f2f4f7; }
    .notes { white-space:normal; min-width:220px; color:var(--muted); }
    @media (max-width:760px) { .us-page { padding-left:14px; padding-right:14px; } .us-header { flex-direction:column; } }
    """


def paper_dashboard_css() -> str:
    return """
    .paper-page { padding:0 28px 32px; }
    .paper-header { display:flex; align-items:flex-start; justify-content:space-between; gap:16px; padding:24px 0 12px; }
    .paper-header h1 { margin:0 0 4px; font-size:26px; }
    .session-pill { border:1px solid var(--line); background:#fff; border-radius:999px; padding:7px 12px; font-weight:700; white-space:nowrap; }
    .notice { margin:12px 0; padding:10px 12px; background:#fff7ed; border:1px solid #fed7aa; border-radius:8px; color:#7c2d12; }
    .debug-block { margin-top:10px; padding:10px 12px; background:#f8fafc; border:1px solid var(--line); border-radius:8px; }
    .table-wrap { overflow-x:auto; }
    table { width:100%; border-collapse:collapse; background:#fff; border:1px solid var(--line); border-radius:8px; overflow:hidden; }
    th, td { padding:9px 10px; border-bottom:1px solid var(--line); text-align:left; vertical-align:top; white-space:nowrap; }
    th { background:#eef2f6; color:#344054; font-size:12px; }
    tr:last-child td { border-bottom:0; }
    h2 { margin:26px 0 10px; font-size:18px; }
    .num-up { color:#067647; font-weight:700; }
    .num-down { color:#b42318; font-weight:700; }
    .notes { white-space:normal; min-width:180px; color:var(--muted); }
    @media (max-width:760px) { .paper-page { padding-left:14px; padding-right:14px; } .paper-header { flex-direction:column; } }
    """


def us_dashboard_script() -> str:
    return r"""
    (() => {
      const state = { interval: 60, remaining: 60, timer: null, lastPayload: null };
      const $ = (id) => document.getElementById(id);
      const text = (value) => value === null || value === undefined || value === "" ? "-" : String(value);
      const number = (value, digits = 2) => Number.isFinite(Number(value)) ? Number(value).toFixed(digits) : "-";
      const pct = (value) => {
        const n = Number(value);
        if (!Number.isFinite(n)) return "-";
        const cls = n > 0 ? "num-up" : n < 0 ? "num-down" : "";
        return `<span class="${cls}">${n > 0 ? "+" : ""}${n.toFixed(2)}%</span>`;
      };
      const volume = (value) => {
        const n = Number(value);
        if (!Number.isFinite(n)) return "-";
        if (n >= 1_000_000_000) return `${(n / 1_000_000_000).toFixed(2)}B`;
        if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(2)}M`;
        return n.toLocaleString();
      };
      const escapeHtml = (value) => text(value)
        .replaceAll("&", "&amp;").replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;").replaceAll('"', "&quot;");
      const metric = (label, value) => `<div class="metric"><span class="muted">${label}</span><strong>${value}</strong></div>`;
      const row = (label, value) => `<tr><td>${label}</td><td>${value}</td></tr>`;
      const setStatus = (message) => { $("us-refresh-status").textContent = message; };

      async function loadDashboard() {
        setStatus("更新中...");
        $("us-error").hidden = true;
        try {
          const response = await fetch("/api/us/dashboard", { cache: "no-store" });
          const payload = await response.json();
          if (!response.ok || payload.error) throw new Error(payload.error || `HTTP ${response.status}`);
          state.lastPayload = payload;
          render(payload);
          state.interval = Number(payload.market?.refresh_interval_seconds || 60);
          state.remaining = state.interval;
          setStatus(`上次更新：${new Date().toLocaleTimeString()}｜下一次更新 ${state.remaining}s`);
        } catch (error) {
          $("us-error").hidden = false;
          $("us-error").textContent = `資料暫時無法更新：${error.message}`;
          state.remaining = Math.max(state.interval, 60);
          setStatus("更新失敗，稍後自動重試");
        }
      }

      function render(payload) {
        const market = payload.market || {};
        const summary = payload.summary || {};
        const source = payload.data_source || {};
        const debug = payload.debug || {};
        $("us-session").textContent = `${market.status_text || "-"}｜${market.session || "-"}｜${market.market_status_text || "-"}`;
        $("us-summary").innerHTML = [
          metric("候選股", summary.candidate_count || 0),
          metric("A級", summary.grade_a || 0),
          metric("B級", summary.grade_b || 0),
          metric("executable 可執行", summary.executable || 0),
          metric("wait_volume 等量能", summary.wait_volume || 0),
          metric("wait_vwap 等VWAP", summary.wait_vwap || 0),
          metric("wait_breakout 等突破", summary.wait_breakout || 0),
          metric("wait_pullback 等回測", summary.wait_pullback || 0),
          metric("recommendations", summary.recommendations || 0),
          metric("triggered 已觸發", summary.triggered || 0),
        ].join("");
        $("us-source").innerHTML = [
          row("資料來源", escapeHtml(source.source)),
          row("Yahoo Finance 狀態", source.ok ? "抓取成功" : "部分失敗 / 資料暫時無法更新"),
          row("成功筆數", text(source.success_count)),
          row("失敗 symbol", escapeHtml((source.failed_symbols || []).join(", ") || "無")),
          row("最後成功更新時間", escapeHtml(source.last_success_at || "-")),
          row("下次更新", `${text(source.next_update_seconds)} 秒`),
        ].join("");
        $("us-market").innerHTML = [
          row("市場狀態", escapeHtml(market.status_text)),
          row("Session", escapeHtml(market.session)),
          row("QQQ 漲跌幅", pct(payload.indices?.qqq_change_pct || 0)),
          row("SPY 漲跌幅", pct(payload.indices?.spy_change_pct || 0)),
          row("指數環境", escapeHtml(market.market_status_text)),
        ].join("");
        $("us-debug").innerHTML = [
          row("commit hash", escapeHtml(debug.app_version)),
          row("model version", escapeHtml(debug.model_version)),
          row("dashboard generated_at", escapeHtml(debug.dashboard_generated_at)),
          row("market session", escapeHtml(debug.market_session)),
          row("refresh interval", `${text(debug.refresh_interval)} 秒`),
          row("candidates count", text(debug.candidates_count)),
          row("recommendations count", text(debug.recommendations_count)),
          row("data source status", escapeHtml(debug.data_source_status)),
        ].join("");
        renderCandidates(payload.candidates || []);
      }

      function renderCandidates(items) {
        if (!items.length) {
          $("us-candidates").innerHTML = '<tr><td colspan="15">目前沒有美股候選資料。</td></tr>';
          return;
        }
        $("us-candidates").innerHTML = items.map((item) => {
          const lifecycle = item.lifecycle_status || "observed";
          const breakout = [
            item.break_premarket_high ? "破盤前高" : "",
            item.break_previous_high ? "破昨高" : "",
            item.break_opening_range_high ? "破開盤區間" : "",
          ].filter(Boolean).join(" / ") || "尚未突破";
          return `<tr>
            <td><div class="symbol-main">${escapeHtml(item.symbol)}｜${escapeHtml(item.short_name_zh)}｜${escapeHtml(item.name_en)}</div><div class="symbol-sub">${escapeHtml(item.sector_zh)}｜${escapeHtml(item.description_zh)}</div></td>
            <td>${number(item.latest_price)}</td>
            <td>${pct(item.change_pct)}</td>
            <td>${volume(item.volume)}</td>
            <td>${number(item.volume_ratio)}x</td>
            <td>${item.above_vwap ? "是" : "否"}<br><span class="muted">${number(item.vwap)}</span></td>
            <td>${number(item.premarket_high)}</td>
            <td>${escapeHtml(breakout)}</td>
            <td>${number(item.bullish_score)}</td>
            <td>${number(item.risk_score)}</td>
            <td><span class="badge grade-${escapeHtml(item.grade)}">${escapeHtml(item.grade)}</span></td>
            <td>${escapeHtml(item.entry_status)}</td>
            <td>${escapeHtml(lifecycle)}</td>
            <td class="notes">${escapeHtml((item.reasons || []).join("；"))}</td>
            <td class="notes">${escapeHtml((item.risk_reasons || []).join("；"))}</td>
          </tr>`;
        }).join("");
      }

      function tick() {
        state.remaining -= 1;
        if (state.remaining <= 0) {
          loadDashboard();
          return;
        }
        setStatus(`上次更新：${new Date().toLocaleTimeString()}｜下一次更新 ${state.remaining}s`);
      }

      loadDashboard();
      state.timer = window.setInterval(tick, 1000);
    })();
    """


def paper_dashboard_script() -> str:
    return r"""
    (() => {
      const state = { interval: 300, remaining: 300 };
      const $ = (id) => document.getElementById(id);
      const text = (value) => value === null || value === undefined || value === "" ? "-" : String(value);
      const money = (value) => Number.isFinite(Number(value)) ? Number(value).toLocaleString(undefined, { maximumFractionDigits: 2 }) : "-";
      const pct = (value) => Number.isFinite(Number(value)) ? `${Number(value).toFixed(2)}%` : "-";
      const escapeHtml = (value) => text(value)
        .replaceAll("&", "&amp;").replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;").replaceAll('"', "&quot;");
      const cls = (value) => Number(value) > 0 ? "num-up" : Number(value) < 0 ? "num-down" : "";
      const metric = (label, value) => `<div class="metric"><span class="muted">${label}</span><strong>${value}</strong></div>`;
      const row = (label, value) => `<tr><td>${label}</td><td>${value}</td></tr>`;
      const status = $("paper-refresh-status");

      async function loadDashboard() {
        status.textContent = "更新中...";
        $("paper-error").hidden = true;
        try {
          const response = await fetch("/api/paper/dashboard", { cache: "no-store" });
          const payload = await response.json();
          if (!response.ok) throw new Error(`HTTP ${response.status}`);
          render(payload);
          state.interval = Number(payload.refresh_interval_seconds || 300);
          state.remaining = state.interval;
          status.textContent = `上次更新：${new Date().toLocaleTimeString()}｜下一次更新 ${state.remaining}s`;
        } catch (error) {
          $("paper-error").hidden = false;
          $("paper-error").textContent = `虛擬交易資料暫時無法更新：${error.message}`;
          state.remaining = Math.max(state.interval, 60);
          status.textContent = "更新失敗，稍後自動重試";
        }
      }

      function render(payload) {
        renderAccounts(payload.accounts || [], payload.performance || {});
        renderPositions(payload.positions || []);
        renderTrades(payload.trades || []);
        renderSkipped(payload.skipped_trades || payload.skipped || []);
        renderPerformance(payload.performance || {});
        renderDebug(payload.debug || {}, payload.run || {});
        const errors = payload.errors || [];
        $("paper-error").hidden = !(payload.api_status === "degraded" && errors.length);
        if (!$("paper-error").hidden) $("paper-error").textContent = `虛擬交易以空狀態顯示：${errors.join("；")}`;
      }

      function renderAccounts(accounts, performance) {
        $("paper-accounts").innerHTML = accounts.map((account) => [
          metric(`${escapeHtml(account.market)} 帳戶資金`, `${escapeHtml(account.currency)} ${money(account.equity)}`),
          metric(`${escapeHtml(account.market)} 現金`, money(account.cash_balance)),
          metric(`${escapeHtml(account.market)} 已實現損益`, `<span class="${cls(account.realized_pnl)}">${money(account.realized_pnl)}</span>`),
          metric(`${escapeHtml(account.market)} 未實現損益`, `<span class="${cls(account.unrealized_pnl)}">${money(account.unrealized_pnl)}</span>`),
          metric(`${escapeHtml(account.market)} 最大回撤`, pct(account.max_drawdown)),
        ].join("")).join("") + metric("整體勝率", pct(performance.win_rate || 0));
      }

      function renderPositions(items) {
        if (!items.length) {
          $("paper-positions").innerHTML = '<tr><td colspan="8">尚無持倉，等待 executable / triggered recommendations 產生後開始模擬。</td></tr>';
          return;
        }
        $("paper-positions").innerHTML = items.map((item) => `<tr>
          <td>${escapeHtml(item.market)}</td><td>${escapeHtml(item.symbol)}</td>
          <td>${money(item.entry_price)}</td><td>${money(item.current_price)}</td>
          <td>${money(item.quantity)}</td><td class="${cls(item.unrealized_pnl)}">${money(item.unrealized_pnl)}<br><span class="muted">${pct(item.unrealized_pnl_pct)}</span></td>
          <td>${money(item.stop_loss)}</td><td>${money(item.target_price)}</td>
        </tr>`).join("");
      }

      function renderTrades(items) {
        const tradable = items.filter((item) => item.status !== "skipped").slice(0, 40);
        if (!tradable.length) {
          $("paper-trades").innerHTML = '<tr><td colspan="9">今日尚無虛擬交易，等待符合條件的訊號。</td></tr>';
          return;
        }
        $("paper-trades").innerHTML = tradable.map((item) => `<tr>
          <td>${escapeHtml(item.market)}</td><td>${escapeHtml(item.symbol)}<br><span class="muted">${escapeHtml(item.name_zh)}</span></td>
          <td>${escapeHtml(item.status)}</td><td>${escapeHtml(item.grade)}</td><td>${escapeHtml(item.entry_status)}</td>
          <td>${escapeHtml(item.entry_time)}<br>${money(item.entry_price)}</td>
          <td>${escapeHtml(item.exit_time)}<br>${money(item.exit_price)}</td>
          <td class="${cls(item.realized_pnl)}">${money(item.realized_pnl)}<br><span class="muted">${pct(item.realized_pnl_pct)}</span></td>
          <td class="notes">${escapeHtml(item.entry_reason || item.exit_reason || "")}</td>
        </tr>`).join("");
      }

      function renderSkipped(items) {
        if (!items.length) {
          $("paper-skipped").innerHTML = '<tr><td colspan="5">尚無跳過紀錄。</td></tr>';
          return;
        }
        $("paper-skipped").innerHTML = items.slice(0, 40).map((item) => `<tr>
          <td>${escapeHtml(item.market)}</td><td>${escapeHtml(item.symbol)}<br><span class="muted">${escapeHtml(item.name_zh)}</span></td>
          <td>${escapeHtml(item.grade)} / ${escapeHtml(item.entry_status)} / ${escapeHtml(item.lifecycle_status)}</td>
          <td>${escapeHtml(item.skipped_reason)}</td><td>${escapeHtml(item.created_at)}</td>
        </tr>`).join("");
      }

      function renderPerformance(performance) {
        const sections = [
          tableFor("依市場", performance.by_market || [], "market"),
          tableFor("依分級", performance.by_grade || [], "grade"),
          tableFor("依進場狀態", performance.by_entry_status || [], "entry_status"),
        ];
        $("paper-performance").innerHTML = sections.join("");
      }

      function tableFor(title, rows, key) {
        const body = rows.length ? rows.map((item) => `<tr><td>${escapeHtml(item[key])}</td><td>${item.trades}</td><td>${pct(item.win_rate)}</td><td class="${cls(item.realized_pnl)}">${money(item.realized_pnl)}</td></tr>`).join("") : '<tr><td colspan="4">尚無資料。</td></tr>';
        return `<h3>${title}</h3><table><thead><tr><th>分類</th><th>筆數</th><th>勝率</th><th>已實現損益</th></tr></thead><tbody>${body}</tbody></table>`;
      }

      function renderDebug(debug, run) {
        $("paper-debug").innerHTML = [
          row("commit hash", escapeHtml(debug.app_version)),
          row("engine version", escapeHtml(debug.engine_version)),
          row("generated_at", escapeHtml(debug.generated_at)),
        row("API status", escapeHtml(debug.api_status || "ok")),
        row("refresh interval", `${text(debug.refresh_interval)} 秒`),
        row("accounts count", text(debug.accounts_count)),
        row("open positions count", text(debug.open_positions_count)),
        row("trades count", text(debug.trades_count)),
        row("skipped count", text(debug.skipped_count)),
        row("recommendations scanned count", text(debug.recommendations_scanned_count)),
        row("executable / triggered count", text(debug.executable_triggered_count)),
        row("last error", escapeHtml(debug.last_error || "")),
          row("本次開倉", text(run.opened)),
          row("本次平倉", text(run.closed)),
          row("本次跳過", text(run.skipped)),
        ].join("");
      }

      function tick() {
        state.remaining -= 1;
        if (state.remaining <= 0) {
          loadDashboard();
          return;
        }
        status.textContent = `上次更新：${new Date().toLocaleTimeString()}｜下一次更新 ${state.remaining}s`;
      }

      loadDashboard();
      window.setInterval(tick, 1000);
    })();
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


def datetime_now_text() -> str:
    return datetime.now().isoformat(timespec="seconds")
