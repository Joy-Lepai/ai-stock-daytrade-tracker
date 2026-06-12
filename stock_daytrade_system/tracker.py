from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from html import escape
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from stock_daytrade_system.cmoney import CMoneyRanking
from stock_daytrade_system.intraday import OpeningSignal
from stock_daytrade_system.labels import sector_label, stock_label
from stock_daytrade_system.long_model import LongModelSummary
from stock_daytrade_system.market_context import MarketIndicator
from stock_daytrade_system.paper_trading import PaperTradingSummary
from stock_daytrade_system.performance import SignalPerformanceSummary
from stock_daytrade_system.scoring import CandidateScore, MarketBias
from stock_daytrade_system.sectors import SectorOpeningStrength, SectorStrength


@dataclass(frozen=True)
class TrackedSymbol:
    source: str
    symbol: str
    name: str
    sector: str
    status: str
    priority: int
    bullish_label: str
    bullish_score: float
    bullish_reasons: List[str]
    entry_status: str
    cancel_conditions: List[str]
    last_price: Optional[float]
    day_change_pct: Optional[float]
    candidate_direction: str
    candidate_score: Optional[float]
    opening_direction: str
    opening_score: Optional[float]
    sector_state: str
    trigger_price: Optional[float]
    stop_loss: Optional[float]
    target_price: Optional[float]
    risk_per_share: Optional[float]
    suggested_shares: Optional[int]
    volume_ratio: Optional[float]
    vwap: Optional[float]
    vwap_state: str
    institutional_rank: Optional[int]
    institutional_buy_million: Optional[float]
    notes: List[str]


def build_tracked_symbols(
    symbols,
    candidates: Iterable[CandidateScore],
    opening_signals: Iterable[OpeningSignal],
    sector_strengths: Iterable[SectorStrength],
    source: str = "auto",
    institutional_rankings: Optional[Dict[str, CMoneyRanking]] = None,
) -> List[TrackedSymbol]:
    candidate_map = {item.symbol: item for item in candidates}
    opening_map = {item.symbol: item for item in opening_signals}
    sector_map = {item.sector: item for item in sector_strengths}
    ranking_map = institutional_rankings or {}
    tracked: List[TrackedSymbol] = []

    for item in symbols:
        candidate = candidate_map.get(item.symbol)
        opening = opening_map.get(item.symbol)
        sector = sector_map.get(item.sector)
        ranking = ranking_map.get(item.symbol)
        status, priority, notes = classify_status(candidate, opening)
        if ranking:
            notes = notes + [f"CMoney 近一日三大法人買超第 {ranking.rank} 名"]
        bullish_label, bullish_score, bullish_reasons = bullish_profile(candidate, opening, sector, ranking)
        vwap_state = _vwap_state(opening)
        entry_status = _entry_status(candidate, opening, vwap_state)
        cancel_conditions = _cancel_conditions(candidate, opening, vwap_state)
        tracked.append(
            TrackedSymbol(
                source=source,
                symbol=item.symbol,
                name=item.name,
                sector=item.sector,
                status=status,
                priority=priority,
                bullish_label=bullish_label,
                bullish_score=bullish_score,
                bullish_reasons=bullish_reasons,
                entry_status=entry_status,
                cancel_conditions=cancel_conditions,
                last_price=_first_number(opening.last_price if opening else None, candidate.close if candidate else None),
                day_change_pct=candidate.day_change_pct if candidate else None,
                candidate_direction=candidate.direction if candidate else "-",
                candidate_score=candidate.score if candidate else None,
                opening_direction=opening.direction if opening else "無盤中資料",
                opening_score=opening.score if opening else None,
                sector_state=sector.direction if sector else "未知",
                trigger_price=candidate.trigger_price if candidate else None,
                stop_loss=candidate.stop_loss if candidate else None,
                target_price=candidate.target_price if candidate else None,
                risk_per_share=candidate.risk_per_share if candidate else None,
                suggested_shares=candidate.suggested_shares if candidate else None,
                volume_ratio=opening.volume_ratio if opening else None,
                vwap=opening.vwap if opening else None,
                vwap_state=vwap_state,
                institutional_rank=ranking.rank if ranking else None,
                institutional_buy_million=ranking.total_buy_million if ranking else None,
                notes=notes,
            )
        )

    tracked.sort(key=lambda row: (row.priority, -(row.candidate_score or 0), row.symbol))
    return tracked


def _vwap_state(opening: Optional[OpeningSignal]) -> str:
    if opening is None or opening.vwap <= 0:
        return "無盤中資料"
    if opening.last_price > opening.vwap * 1.002:
        return "站上VWAP"
    if opening.last_price < opening.vwap * 0.998:
        return "跌破VWAP"
    return "貼近VWAP"


def _entry_status(
    candidate: Optional[CandidateScore],
    opening: Optional[OpeningSignal],
    vwap_state: str,
) -> str:
    if candidate is None or candidate.direction != "做多觀察":
        return "-"
    if candidate.suggested_shares <= 0:
        return "風險過高"
    if opening and opening.direction == "做空確認":
        return "轉弱取消"
    if opening is None:
        return "等開盤確認"
    if opening.volume_ratio < 0.8:
        return "量不足"
    if vwap_state == "跌破VWAP":
        return "等轉強"
    if opening.last_price >= candidate.trigger_price and vwap_state == "站上VWAP":
        return "可進場"
    if opening.last_price < candidate.trigger_price:
        return "等突破"
    if vwap_state == "貼近VWAP":
        return "等VWAP轉強"
    return "觀察"


def _cancel_conditions(
    candidate: Optional[CandidateScore],
    opening: Optional[OpeningSignal],
    vwap_state: str,
) -> List[str]:
    conditions: List[str] = []
    if opening:
        conditions.append(f"跌破開盤低點 {opening.opening_range_low:.2f}")
        if opening.vwap > 0:
            conditions.append(f"跌破VWAP {opening.vwap:.2f}")
        if opening.volume_ratio < 0.8:
            conditions.append("量比低於0.8")
        if opening.direction == "做空確認":
            conditions.append("盤中轉做空確認")
    else:
        conditions.append("無盤中資料不追價")
    if candidate:
        conditions.append(f"跌破停損 {candidate.stop_loss:.2f}")
    if vwap_state == "跌破VWAP":
        conditions.append("目前已跌破VWAP")
    return conditions[:5]


def bullish_profile(
    candidate: Optional[CandidateScore],
    opening: Optional[OpeningSignal],
    sector: Optional[SectorStrength],
    ranking: Optional[CMoneyRanking],
) -> tuple[str, float, List[str]]:
    score = 0.0
    reasons: List[str] = []

    if candidate and candidate.direction == "做多觀察":
        score += 2.0
        reasons.append("盤前做多")
        if candidate.score >= 7:
            score += 1.5
            reasons.append("盤前分數高")
        if candidate.day_change_pct >= 3:
            score += 2.0
            reasons.append(f"當日強漲 {candidate.day_change_pct:+.2f}%")
        elif candidate.day_change_pct > 0:
            score += 1.0
            reasons.append(f"當日上漲 {candidate.day_change_pct:+.2f}%")
        if "價格突破前一日高點" in candidate.reasons:
            score += 1.5
            reasons.append("突破前高")
        if "量能放大" in " ".join(candidate.reasons):
            score += 1.0
            reasons.append("日線量能放大")
        if candidate.suggested_shares > 0:
            score += 1.0
            reasons.append("風控可下單")
    elif candidate and candidate.direction == "做空觀察":
        score -= 2.0
        reasons.append("盤前偏空")

    if opening:
        if opening.direction == "做多確認":
            score += 3.0
            reasons.append("開盤做多確認")
        elif opening.direction == "做空確認":
            score -= 3.0
            reasons.append("開盤做空訊號")
        if opening.volume_ratio >= 1.2:
            score += 1.0
            reasons.append(f"盤中量比 {opening.volume_ratio:.2f}x")

    if sector and sector.direction == "強勢":
        score += 1.0
        reasons.append("族群強勢")

    if ranking:
        if ranking.rank <= 10:
            score += 2.0
            reasons.append(f"法人買超第 {ranking.rank} 名")
        else:
            score += 1.0
            reasons.append(f"法人買超第 {ranking.rank} 名")

    score = round(score, 2)
    if score >= 8:
        label = "強烈看漲"
    elif score >= 5:
        label = "看漲"
    elif score >= 3:
        label = "偏多觀察"
    elif score <= 0:
        label = "不明確"
    else:
        label = "低度偏多"
    return label, score, reasons[:5]


def classify_status(
    candidate: Optional[CandidateScore],
    opening: Optional[OpeningSignal],
) -> tuple[str, int, List[str]]:
    notes: List[str] = []
    opening_direction = opening.direction if opening else ""
    candidate_direction = candidate.direction if candidate else ""

    if candidate and candidate.suggested_shares == 0:
        notes.append("每股風險高於目前單筆風險額度")
        if opening_direction in {"做多確認", "做空確認"}:
            notes.append("盤中有確認，但部位需降至零股或提高風險額度才可執行")
        return "風險過高", 3, notes

    if opening_direction in {"做多確認", "做空確認"}:
        if candidate and _directions_align(candidate_direction, opening_direction):
            notes.append("盤前方向與開盤確認同向")
            return "可執行", 0, notes
        notes.append("盤中訊號出現，但盤前條件不足或方向不同")
        return "盤中異動", 1, notes

    if candidate:
        notes.append("盤前條件成立，等待開盤區間與量能確認")
        return "等待確認", 2, notes

    notes.append("未進入盤前候選，保持低優先觀察")
    return "低優先", 4, notes


def render_tracker_html(
    report_time: datetime,
    market_bias: MarketBias,
    market_indicators: Iterable[MarketIndicator],
    sector_strengths: Iterable[SectorStrength],
    opening_sector_strengths: Iterable[SectorOpeningStrength],
    tracked_symbols: Iterable[TrackedSymbol],
    output_path: Path,
    data_warnings: Iterable[str] = (),
    data_status: Iterable[str] = (),
    performance_summary: Optional[SignalPerformanceSummary] = None,
    paper_summary: Optional[PaperTradingSummary] = None,
    long_summary: Optional[LongModelSummary] = None,
) -> Path:
    sectors = list(sector_strengths)
    opening_sectors = list(opening_sector_strengths)
    indicators = list(market_indicators)
    rows = list(tracked_symbols)
    auto_rows = [row for row in rows if row.source == "auto"]
    manual_rows = [row for row in rows if row.source == "manual"]
    warnings = list(data_warnings)
    statuses = list(data_status)
    checklist = long_summary.recommendation_checklist if long_summary else {}

    html = f"""<!doctype html>
<html lang="zh-Hant">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>股票當沖追蹤器 {escape(report_time.strftime('%Y-%m-%d'))}</title>
  <style>
    :root {{
      color-scheme: light;
      --ink: #18202a;
      --muted: #667085;
      --line: #d7dde5;
      --panel: #ffffff;
      --bg: #f5f7fa;
      --long: #b42318;
      --short: #067647;
      --wait: #8a5a00;
      --blue: #175cd3;
      --risk: #9f1239;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: var(--bg);
      color: var(--ink);
      font: 14px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }}
    header {{
      padding: 24px 28px 18px;
      background: #ffffff;
      border-bottom: 1px solid var(--line);
    }}
    h1 {{ margin: 0 0 6px; font-size: 26px; font-weight: 750; letter-spacing: 0; }}
    h2 {{ margin: 28px 0 10px; font-size: 18px; }}
    .meta {{ color: var(--muted); }}
    main {{ padding: 0 28px 32px; }}
    .summary {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
      gap: 12px;
      margin-top: 16px;
    }}
    .metric {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 12px 14px;
    }}
    .metric strong {{ display: block; font-size: 22px; margin-top: 2px; }}
    .muted {{ color: var(--muted); }}
    .warn {{
      margin-top: 14px;
      padding: 10px 12px;
      background: #fff7ed;
      border: 1px solid #fed7aa;
      border-radius: 8px;
      color: #7c2d12;
    }}
    .data-status {{
      margin-top: 14px;
      padding: 10px 12px;
      background: #f0f9ff;
      border: 1px solid #bae6fd;
      border-radius: 8px;
      color: #075985;
    }}
    .debug-block {{
      margin-top: 14px;
      padding: 10px 12px;
      background: #f8fafc;
      border: 1px solid var(--line);
      border-radius: 8px;
      color: #344054;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      overflow: hidden;
    }}
    th, td {{
      padding: 9px 10px;
      border-bottom: 1px solid var(--line);
      text-align: left;
      vertical-align: top;
      white-space: nowrap;
    }}
    th {{ background: #eef2f6; color: #344054; font-size: 12px; }}
    tr:last-child td {{ border-bottom: 0; }}
    .table-wrap {{ overflow-x: auto; }}
    .badge {{
      display: inline-block;
      min-width: 64px;
      padding: 2px 8px;
      border-radius: 999px;
      text-align: center;
      font-size: 12px;
      font-weight: 700;
      border: 1px solid var(--line);
      background: #f8fafc;
    }}
    .s-可執行 {{ color: var(--long); background: #fff1f3; border-color: #fecdd3; }}
    .s-盤中異動 {{ color: var(--blue); background: #eff6ff; border-color: #bfdbfe; }}
    .s-風險過高 {{ color: var(--risk); background: #fff1f2; border-color: #fecdd3; }}
    .s-等待確認 {{ color: var(--wait); background: #fffbeb; border-color: #fde68a; }}
    .s-低優先 {{ color: #475467; background: #f2f4f7; }}
    .b-強烈看漲 {{ color: #fff; background: var(--long); border-color: var(--long); }}
    .b-看漲 {{ color: var(--long); background: #fff1f3; border-color: #fecdd3; }}
    .b-偏多觀察 {{ color: #9a3412; background: #fff7ed; border-color: #fed7aa; }}
    .b-低度偏多, .b-不明確 {{ color: #475467; background: #f2f4f7; }}
    .dir-long {{ color: var(--long); font-weight: 700; }}
    .dir-short {{ color: var(--short); font-weight: 700; }}
    .num-up {{ color: var(--long); font-weight: 700; }}
    .num-down {{ color: var(--short); font-weight: 700; }}
    .num-flat {{ color: var(--muted); }}
    th.sortable {{ cursor: pointer; user-select: none; }}
    th.sortable::after {{ content: " ↕"; color: #98a2b3; font-weight: 500; }}
    th.sort-asc::after {{ content: " ▲"; color: var(--blue); }}
    th.sort-desc::after {{ content: " ▼"; color: var(--blue); }}
    .notes {{ white-space: normal; min-width: 220px; color: var(--muted); }}
    @media (max-width: 760px) {{
      header, main {{ padding-left: 14px; padding-right: 14px; }}
      h1 {{ font-size: 22px; }}
      th, td {{ padding: 8px; }}
    }}
  </style>
</head>
<body>
  <header>
    <h1>股票當沖追蹤器</h1>
    <div class="meta">產生時間：{escape(report_time.strftime('%Y-%m-%d %H:%M:%S'))} ｜ 市場背景：{escape(market_bias.direction)}（{market_bias.score:+.2f}）</div>
    <div class="summary">
      {_metric('executable 可執行', int(checklist.get('executable', 0)))}
      {_metric('wait_volume 等量能', int(checklist.get('wait_volume', 0)))}
      {_metric('wait_vwap 等VWAP', int(checklist.get('wait_vwap', 0)))}
      {_metric('high_risk 風險過高', int(checklist.get('high_risk', 0)))}
    </div>
    {_data_status_block(statuses)}
    {_warning_block(warnings)}
    {_debug_block(long_summary)}
  </header>
  <main>
    <h2>今日做多候選股 MVP</h2>
    <div class="table-wrap">{_long_candidate_table(long_summary)}</div>
    <h2>今日推薦檢查表</h2>
    <div class="table-wrap">{_recommendation_checklist_table(long_summary)}</div>
    <h2>盤中警示</h2>
    <div class="table-wrap">{_alert_table(long_summary)}</div>
    <h2>族群熱度</h2>
    <div class="table-wrap">{_sector_heat_table(long_summary)}</div>
    <h2>大盤狀態</h2>
    <div class="table-wrap">{_market_state_table(long_summary)}</div>
    <h2>每日回測</h2>
    <div class="table-wrap">{_backtest_table(long_summary)}</div>
    <h2>盤前市場指標</h2>
    <div class="table-wrap">{_market_indicator_table(indicators)}</div>
    <h2>我的自選追蹤</h2>
    <div class="table-wrap">{_tracked_table(manual_rows, empty_text='目前沒有自選標的。')}</div>
    <h2>訊號績效追蹤</h2>
    <div class="table-wrap">{_performance_table(performance_summary)}</div>
    <h2>虛擬交易</h2>
    <div class="table-wrap">{_paper_trading_table(paper_summary)}</div>
    <h2>市場摘要</h2>
    <div class="table-wrap">{_market_table(market_bias.notes)}</div>
    <h2>族群強弱</h2>
    <div class="table-wrap">{_sector_table(sectors)}</div>
    <h2>開盤族群狀態</h2>
    <div class="table-wrap">{_opening_sector_table(opening_sectors)}</div>
  </main>
  <script>
    (() => {{
      const compare = (a, b, type) => {{
        if (type === "number") {{
          const an = Number(a);
          const bn = Number(b);
          if (Number.isNaN(an) && Number.isNaN(bn)) return 0;
          if (Number.isNaN(an)) return 1;
          if (Number.isNaN(bn)) return -1;
          return an - bn;
        }}
        return String(a).localeCompare(String(b), "zh-Hant");
      }};

      document.querySelectorAll("table.sortable").forEach((table) => {{
        const headers = table.querySelectorAll("th[data-sort]");
        headers.forEach((header, index) => {{
          header.classList.add("sortable");
          header.addEventListener("click", () => {{
            const tbody = table.tBodies[0];
            const type = header.dataset.sort || "text";
            const current = header.dataset.direction === "asc" ? "desc" : "asc";
            headers.forEach((item) => {{
              item.dataset.direction = "";
              item.classList.remove("sort-asc", "sort-desc");
            }});
            header.dataset.direction = current;
            header.classList.add(current === "asc" ? "sort-asc" : "sort-desc");

            const rows = Array.from(tbody.rows);
            rows.sort((rowA, rowB) => {{
              const cellA = rowA.cells[index];
              const cellB = rowB.cells[index];
              const valueA = cellA?.dataset.sortValue || cellA?.textContent.trim() || "";
              const valueB = cellB?.dataset.sortValue || cellB?.textContent.trim() || "";
              const result = compare(valueA, valueB, type);
              return current === "asc" ? result : -result;
            }});
            rows.forEach((row) => tbody.appendChild(row));
          }});
        }});
      }});
    }})();
  </script>
</body>
</html>
"""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html, encoding="utf-8")
    return output_path


def _directions_align(candidate_direction: str, opening_direction: str) -> bool:
    return (
        candidate_direction == "做多觀察" and opening_direction == "做多確認"
    ) or (
        candidate_direction == "做空觀察" and opening_direction == "做空確認"
    )


def _first_number(*values: Optional[float]) -> Optional[float]:
    for value in values:
        if value is not None:
            return value
    return None


def _status_counts(rows: Iterable[TrackedSymbol]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for row in rows:
        counts[row.status] = counts.get(row.status, 0) + 1
    return counts


def _metric(label: str, value: int) -> str:
    return f'<div class="metric"><span class="muted">{escape(label)}</span><strong>{value}</strong></div>'


def _metric_text(label: str, value: str) -> str:
    return f'<div class="metric"><span class="muted">{escape(label)}</span><strong>{escape(value)}</strong></div>'


def _warning_block(warnings: List[str]) -> str:
    if not warnings:
        return ""
    items = "".join(f"<li>{escape(item)}</li>" for item in warnings)
    return f'<div class="warn"><strong>資料警告</strong><ul>{items}</ul></div>'


def _data_status_block(statuses: List[str]) -> str:
    if not statuses:
        return ""
    items = "".join(f"<li>{escape(item)}</li>" for item in statuses)
    return f'<div class="data-status"><strong>資料狀態</strong><ul>{items}</ul></div>'


def _tracked_table(rows: List[TrackedSymbol], empty_text: str = "目前沒有追蹤標的。") -> str:
    body = []
    for row in rows:
        body.append(
            "<tr>"
            f'<td><span class="badge s-{escape(row.status)}">{escape(row.status)}</span></td>'
            f'<td data-sort-value="{_sort_value(row.bullish_score)}"><span class="badge b-{escape(row.bullish_label)}">{escape(row.bullish_label)}</span><br><span class="muted">{_fmt(row.bullish_score)}</span></td>'
            f"<td>{escape(row.entry_status)}</td>"
            f"<td><strong>{escape(stock_label(row.name, row.symbol))}</strong></td>"
            f"<td>{escape(sector_label(row.sector))}<br><span class=\"muted\">{escape(row.sector_state)}</span></td>"
            f"{_number_cell(row.last_price)}"
            f"{_change_cell(row.day_change_pct)}"
            f"<td data-sort-value=\"{_sort_value(row.candidate_score)}\">{_direction(row.candidate_direction)}<br><span class=\"muted\">{_fmt(row.candidate_score)}</span></td>"
            f"<td data-sort-value=\"{_sort_value(row.volume_ratio)}\">{_direction(row.opening_direction)}<br><span class=\"muted\">量比 {_fmt(row.volume_ratio)}x</span></td>"
            f"<td data-sort-value=\"{_sort_value(row.vwap)}\">{escape(row.vwap_state)}<br><span class=\"muted\">{_fmt(row.vwap)}</span></td>"
            f"{_number_cell(row.trigger_price)}"
            f"{_number_cell(row.stop_loss)}"
            f"{_number_cell(row.target_price)}"
            f"{_number_cell(row.risk_per_share)}"
            f"<td data-sort-value=\"{_sort_value(row.suggested_shares)}\">{'' if row.suggested_shares is None else row.suggested_shares}</td>"
            f"<td data-sort-value=\"{_sort_value(row.institutional_rank)}\">{'' if row.institutional_rank is None else row.institutional_rank}</td>"
            f"{_change_cell(row.institutional_buy_million, suffix=' 百萬')}"
            f"<td class=\"notes\">{escape('；'.join(row.bullish_reasons))}</td>"
            f"<td class=\"notes\">{escape('；'.join(row.cancel_conditions))}</td>"
            f"<td class=\"notes\">{escape('；'.join(row.notes))}</td>"
            "</tr>"
        )
    if not body:
        body.append(f'<tr><td colspan="20">{escape(empty_text)}</td></tr>')
    return (
        "<table class=\"sortable\"><thead><tr>"
        "<th data-sort=\"text\">狀態</th><th data-sort=\"number\">當日看漲</th><th data-sort=\"text\">進場狀態</th>"
        "<th data-sort=\"text\">標的</th><th data-sort=\"text\">族群</th>"
        "<th data-sort=\"number\">現價</th><th data-sort=\"number\">漲跌</th>"
        "<th data-sort=\"number\">盤前</th><th data-sort=\"number\">開盤</th>"
        "<th data-sort=\"number\">VWAP</th>"
        "<th data-sort=\"number\">觸發</th><th data-sort=\"number\">停損</th><th data-sort=\"number\">目標</th>"
        "<th data-sort=\"number\">每股風險</th><th data-sort=\"number\">股數上限</th>"
        "<th data-sort=\"number\">法人排行</th><th data-sort=\"number\">法人買超</th><th>看漲理由</th><th>取消條件</th><th>備註</th></tr></thead><tbody>"
        + "".join(body)
        + "</tbody></table>"
    )


def _long_candidate_table(summary: Optional[LongModelSummary]) -> str:
    if summary is None or not summary.candidates:
        return "<table><tbody><tr><td>目前沒有符合 MVP 做多條件的候選股。</td></tr></tbody></table>"
    rows = []
    for item in summary.candidates:
        rows.append(
            "<tr>"
            f"<td><span class=\"badge b-{escape(_grade_label(item.grade))}\">{escape(item.grade)}</span><br><span class=\"muted\">{escape(_grade_label(item.grade))}</span></td>"
            f"<td><strong><a href=\"/symbol/{escape(item.symbol)}\">{escape(stock_label(item.name, item.symbol))}</a></strong><br><span class=\"muted\">{escape(sector_label(item.sector))}</span></td>"
            f"{_number_cell(item.last_price)}"
            f"{_change_cell(item.change_pct)}"
            f"<td data-sort-value=\"{_sort_value(item.turnover)}\">{_money(item.turnover)}</td>"
            f"<td data-sort-value=\"{_sort_value(item.volume_ratio)}\">{_fmt(item.volume_ratio)}x</td>"
            f"<td data-sort-value=\"{_sort_value(item.vwap)}\">{_yes_no(item.above_vwap)}<br><span class=\"muted\">{_fmt(item.vwap)}</span></td>"
            f"<td>{_yes_no(item.break_prev_high)}</td>"
            f"<td>{_yes_no(item.break_5d_high)}</td>"
            f"<td>{_yes_no(item.break_10d_high)}</td>"
            f"<td data-sort-value=\"{_sort_value(item.bullish_score)}\">{_fmt(item.bullish_score)}</td>"
            f"<td data-sort-value=\"{_sort_value(item.risk_score)}\">{_fmt(item.risk_score)}</td>"
            f"<td>{escape(_entry_status_label(item.entry_status))}<br><span class=\"muted\">{escape(_entry_status_message(item.entry_status))}</span></td>"
            f"<td class=\"notes\">{escape('；'.join(item.reasons[:5]))}</td>"
            f"<td class=\"notes\">{escape('；'.join(item.risk_reasons[:4]))}</td>"
            "</tr>"
        )
    return (
        "<table class=\"sortable\"><thead><tr>"
        "<th data-sort=\"text\">分級</th><th data-sort=\"text\">標的</th><th data-sort=\"number\">現價</th>"
        "<th data-sort=\"number\">今日漲幅</th><th data-sort=\"number\">成交金額</th><th data-sort=\"number\">量比</th>"
        "<th data-sort=\"number\">VWAP</th><th data-sort=\"text\">破昨高</th><th data-sort=\"text\">破5日高</th><th data-sort=\"text\">破10日高</th>"
        "<th data-sort=\"number\">多方分數</th><th data-sort=\"number\">風險分數</th><th data-sort=\"text\">狀態</th><th>多方理由</th><th>風險理由</th>"
        "</tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table>"
    )


def _alert_table(summary: Optional[LongModelSummary]) -> str:
    alerts = summary.alerts if summary else []
    rows = "".join(f"<tr><td>{escape(item)}</td></tr>" for item in alerts)
    return f"<table><tbody>{rows or '<tr><td>目前沒有盤中警示。</td></tr>'}</tbody></table>"


def _sector_heat_table(summary: Optional[LongModelSummary]) -> str:
    heat = summary.sector_heat if summary else []
    rows = []
    for item in heat:
        rows.append(
            "<tr>"
            f"<td>{escape(sector_label(item.sector))}</td>"
            f"<td data-sort-value=\"{_sort_value(item.score)}\">{_fmt(item.score)}</td>"
            f"<td data-sort-value=\"{_sort_value(item.candidates)}\">{item.candidates}</td>"
            f"<td data-sort-value=\"{_sort_value(item.grade_a_count)}\">{item.grade_a_count}</td>"
            f"<td data-sort-value=\"{_sort_value(item.above_vwap_count)}\">{item.above_vwap_count}</td>"
            f"<td data-sort-value=\"{_sort_value(item.breakout_count)}\">{item.breakout_count}</td>"
            f"<td data-sort-value=\"{_sort_value(item.avg_volume_ratio)}\">{_fmt(item.avg_volume_ratio)}x</td>"
            "</tr>"
        )
    return (
        "<table class=\"sortable\"><thead><tr><th data-sort=\"text\">族群</th><th data-sort=\"number\">熱度</th>"
        "<th data-sort=\"number\">候選數</th><th data-sort=\"number\">A級</th><th data-sort=\"number\">站上VWAP</th>"
        "<th data-sort=\"number\">突破</th><th data-sort=\"number\">平均量比</th></tr></thead><tbody>"
        + ("".join(rows) or "<tr><td colspan=\"7\">目前沒有族群熱度資料。</td></tr>")
        + "</tbody></table>"
    )


def _market_state_table(summary: Optional[LongModelSummary]) -> str:
    if summary is None:
        return "<table><tbody><tr><td>目前沒有大盤狀態資料。</td></tr></tbody></table>"
    notes = "".join(f"<tr><td>{escape(note)}</td></tr>" for note in summary.market_notes)
    return (
        "<table><tbody>"
        f"<tr><td><strong>大盤狀態</strong></td><td>{escape(summary.market_state)}</td></tr>"
        f"{notes}"
        "</tbody></table>"
    )


def _backtest_table(summary: Optional[LongModelSummary]) -> str:
    if summary is None:
        return "<table><tbody><tr><td>目前沒有回測資料。</td></tr></tbody></table>"
    data = summary.backtest
    avg_return = f"{float(data.get('avg_return', 0)):+.2f}%"
    return (
        "<div class=\"summary\">"
        f"{_metric('推薦紀錄', int(data.get('recommendation_count', data.get('total', 0))))}"
        f"{_metric('可回測追蹤', int(data.get('trackable_count', 0)))}"
        f"{_metric('達標', int(data.get('target', 0)))}"
        f"{_metric('停損', int(data.get('stop', 0)))}"
        f"{_metric_text('平均報酬', avg_return)}"
        "</div>"
        f"{_entry_status_backtest_table(data.get('by_entry_status', []))}"
    )


def _recommendation_checklist_table(summary: Optional[LongModelSummary]) -> str:
    if summary is None:
        return "<table><tbody><tr><td>目前沒有推薦檢查資料。</td></tr></tbody></table>"
    data = summary.recommendation_checklist or {}
    return (
        "<div class=\"summary\">"
        f"{_metric('今日候選股總數', int(data.get('candidate_total', 0)))}"
        f"{_metric('A級數量', int(data.get('grade_a', 0)))}"
        f"{_metric('B級數量', int(data.get('grade_b', 0)))}"
        f"{_metric('executable 可執行', int(data.get('executable', 0)))}"
        f"{_metric('wait_volume 等量能', int(data.get('wait_volume', 0)))}"
        f"{_metric('wait_vwap 等VWAP', int(data.get('wait_vwap', 0)))}"
        f"{_metric('high_risk 風險過高', int(data.get('high_risk', 0)))}"
        f"{_metric('avoid 暫不追蹤', int(data.get('avoid', 0)))}"
        f"{_metric('已寫入 recommendations 數量', int(data.get('recommendations', 0)))}"
        f"{_metric('今日可回測數量', int(data.get('backtest_trackable', 0)))}"
        f"{_metric('資料缺漏數量', int(data.get('data_missing', 0)))}"
        "</div>"
    )


def _entry_status_backtest_table(rows: List[dict]) -> str:
    if not rows:
        return ""
    body = []
    for item in rows:
        avg_return = f"{float(item.get('avg_return', 0)):+.2f}%"
        body.append(
            "<tr>"
            f"<td>{escape(_entry_status_label(str(item.get('entry_status', ''))))}</td>"
            f"<td data-sort-value=\"{_sort_value(item.get('total'))}\">{int(item.get('total', 0))}</td>"
            f"<td data-sort-value=\"{_sort_value(item.get('trackable'))}\">{int(item.get('trackable', 0))}</td>"
            f"<td data-sort-value=\"{_sort_value(item.get('target'))}\">{int(item.get('target', 0))}</td>"
            f"<td data-sort-value=\"{_sort_value(item.get('stop'))}\">{int(item.get('stop', 0))}</td>"
            f"<td data-sort-value=\"{_sort_value(item.get('avg_return'))}\">{escape(avg_return)}</td>"
            "</tr>"
        )
    return (
        "<table class=\"sortable\"><thead><tr><th data-sort=\"text\">進場狀態</th>"
        "<th data-sort=\"number\">推薦數</th><th data-sort=\"number\">可回測</th>"
        "<th data-sort=\"number\">達標</th><th data-sort=\"number\">停損</th><th data-sort=\"number\">平均報酬</th>"
        "</tr></thead><tbody>"
        + "".join(body)
        + "</tbody></table>"
    )


def _entry_status_label(value: str) -> str:
    return {
        "executable": "executable 可執行觀察",
        "wait_volume": "wait_volume 等待量能確認",
        "wait_vwap": "wait_vwap 等待站回VWAP",
        "wait_pullback": "wait_pullback 等待回測",
        "high_risk": "high_risk 風險過高",
        "avoid": "avoid 暫不追蹤",
    }.get(value, value or "-")


def _entry_status_message(value: str) -> str:
    return {
        "executable": "量能與VWAP條件成立，可列入做多觀察。",
        "wait_volume": "多方結構不錯，但量能不足，等待量比放大後再觀察。",
        "wait_vwap": "突破條件成立，但尚未站上 VWAP，等待站回均價線。",
        "wait_pullback": "條件可追蹤，等待回測或整理後再評估。",
        "high_risk": "多方動能強，但追價風險偏高，避免直接追高。",
        "avoid": "條件不足或跌破VWAP，暫不追蹤。",
    }.get(value, "")


def _debug_block(summary: Optional[LongModelSummary]) -> str:
    if summary is None or not summary.debug_info:
        return ""
    info = summary.debug_info
    rows = [
        ("app version / commit", str(info.get("app_version", "-"))),
        ("scoring model version", str(info.get("scoring_model_version", "-"))),
        ("dashboard generated_at", str(info.get("dashboard_generated_at", "-"))),
        ("recommendations count from DB", str(info.get("recommendations_count_from_db", "-"))),
        ("candidates count from current run", str(info.get("candidates_count_from_current_run", "-"))),
        ("visible candidates count", str(info.get("visible_candidates_count", "-"))),
    ]
    items = "".join(f"<li><strong>{escape(label)}:</strong> {escape(value)}</li>" for label, value in rows)
    return f'<div class="debug-block"><strong>系統版本 / Debug</strong><ul>{items}</ul></div>'


def _bullish_focus_rows(rows: List[TrackedSymbol]) -> List[TrackedSymbol]:
    focus = [
        row for row in rows
        if row.bullish_score >= 5 and row.candidate_direction == "做多觀察"
        and (row.suggested_shares or 0) > 0
        and row.opening_direction != "做空確認"
        and row.entry_status not in {"風險過高", "轉弱取消"}
    ]
    focus.sort(
        key=lambda row: (
            -(row.bullish_score or 0),
            row.suggested_shares == 0,
            -(row.candidate_score or 0),
            row.symbol,
        )
    )
    return focus[:8]


def _bullish_focus_table(rows: List[TrackedSymbol]) -> str:
    if not rows:
        return "<table><tbody><tr><td>目前沒有足夠明確的當日看漲標的。</td></tr></tbody></table>"
    body = []
    for row in rows:
        body.append(
            "<tr>"
            f'<td data-sort-value="{_sort_value(row.bullish_score)}"><span class="badge b-{escape(row.bullish_label)}">{escape(row.bullish_label)}</span><br><span class="muted">{_fmt(row.bullish_score)}</span></td>'
            f"<td>{escape(row.entry_status)}</td>"
            f"<td><strong>{escape(stock_label(row.name, row.symbol))}</strong></td>"
            f"{_number_cell(row.last_price)}"
            f"{_change_cell(row.day_change_pct)}"
            f"<td>{escape(sector_label(row.sector))}<br><span class=\"muted\">{escape(row.sector_state)}</span></td>"
            f"<td data-sort-value=\"{_sort_value(row.volume_ratio)}\">{escape(row.opening_direction)}<br><span class=\"muted\">量比 {_fmt(row.volume_ratio)}x</span></td>"
            f"<td data-sort-value=\"{_sort_value(row.vwap)}\">{escape(row.vwap_state)}<br><span class=\"muted\">{_fmt(row.vwap)}</span></td>"
            f"{_number_cell(row.trigger_price)}"
            f"{_number_cell(row.stop_loss)}"
            f"{_number_cell(row.target_price)}"
            f"<td data-sort-value=\"{_sort_value(row.suggested_shares)}\">{'' if row.suggested_shares is None else row.suggested_shares}</td>"
            f"<td data-sort-value=\"{_sort_value(row.institutional_rank)}\">{'' if row.institutional_rank is None else row.institutional_rank}</td>"
            f"<td class=\"notes\">{escape('；'.join(row.bullish_reasons))}</td>"
            f"<td class=\"notes\">{escape('；'.join(row.cancel_conditions))}</td>"
            "</tr>"
        )
    return (
        "<table class=\"sortable\"><thead><tr><th data-sort=\"number\">當日看漲</th>"
        "<th data-sort=\"text\">進場狀態</th><th data-sort=\"text\">標的</th><th data-sort=\"number\">現價</th><th data-sort=\"number\">漲跌</th>"
        "<th data-sort=\"text\">族群</th><th data-sort=\"number\">開盤</th><th data-sort=\"number\">VWAP</th>"
        "<th data-sort=\"number\">觸發</th><th data-sort=\"number\">停損</th><th data-sort=\"number\">目標</th>"
        "<th data-sort=\"number\">股數上限</th><th data-sort=\"number\">法人排行</th><th>看漲理由</th><th>取消條件</th></tr></thead><tbody>"
        + "".join(body)
        + "</tbody></table>"
    )


def _performance_table(summary: Optional[SignalPerformanceSummary]) -> str:
    if summary is None or summary.total == 0:
        return "<table><tbody><tr><td>尚未累積可評估的看漲訊號。系統更新後會自動建立紀錄。</td></tr></tbody></table>"
    status_rows = []
    for item in summary.by_status:
        status_rows.append(
            "<tr>"
            f"<td>{escape(item.status)}</td>"
            f"<td data-sort-value=\"{_sort_value(item.total)}\">{item.total}</td>"
            f"<td data-sort-value=\"{_sort_value(item.target_count)}\">{item.target_count}</td>"
            f"<td data-sort-value=\"{_sort_value(item.stop_count)}\">{item.stop_count}</td>"
            f"<td data-sort-value=\"{_sort_value(item.target_rate)}\">{_fmt(item.target_rate)}%</td>"
            f"<td data-sort-value=\"{_sort_value(item.avg_max_favorable_pct)}\">{_change_number(item.avg_max_favorable_pct, suffix='%')}</td>"
            f"<td data-sort-value=\"{_sort_value(item.avg_max_adverse_pct)}\">{_change_number(item.avg_max_adverse_pct, suffix='%')}</td>"
            "</tr>"
        )
    return (
        "<div class=\"summary\">"
        f"{_metric('今日新增訊號', summary.today_count)}"
        f"{_metric('累積訊號', summary.total)}"
        f"{_metric('達標', summary.target_count)}"
        f"{_metric('停損', summary.stop_count)}"
        f"{_metric('追蹤中', summary.active_count)}"
        f"{_metric_text('已完成達標率', f'{summary.target_rate:.0f}%')}"
        "</div>"
        "<table class=\"sortable\"><thead><tr>"
        "<th data-sort=\"text\">初始進場狀態</th><th data-sort=\"number\">訊號數</th>"
        "<th data-sort=\"number\">達標</th><th data-sort=\"number\">停損</th><th data-sort=\"number\">達標率</th>"
        "<th data-sort=\"number\">平均最大漲幅</th><th data-sort=\"number\">平均最大回撤</th>"
        "</tr></thead><tbody>"
        + ("".join(status_rows) or "<tr><td colspan=\"7\">尚無分組資料。</td></tr>")
        + "</tbody></table>"
    )


def _paper_trading_table(summary: Optional[PaperTradingSummary]) -> str:
    if summary is None or (summary.open_count == 0 and summary.closed_count == 0):
        return "<table><tbody><tr><td>尚未產生虛擬交易。交易時段內出現「可進場」訊號時，系統會自動建立模擬單。</td></tr></tbody></table>"
    rows = []
    for trade in summary.latest_trades:
        pnl = _first_number(_as_number(trade.get("realized_pnl")), _as_number(trade.get("unrealized_pnl")))
        rows.append(
            "<tr>"
            f"<td>{escape(str(trade.get('status', '')))}</td>"
            f"<td><strong>{escape(stock_label(str(trade.get('name', '')), str(trade.get('symbol', ''))))}</strong></td>"
            f"<td>{escape(str(trade.get('side', '')))}</td>"
            f"<td>{escape(str(trade.get('entry_time', '')))}</td>"
            f"<td data-sort-value=\"{_sort_value(_as_number(trade.get('entry_price')))}\">{_fmt(_as_number(trade.get('entry_price')))}</td>"
            f"<td data-sort-value=\"{_sort_value(_as_number(trade.get('shares')))}\">{escape(str(trade.get('shares', '')))}</td>"
            f"<td data-sort-value=\"{_sort_value(_as_number(trade.get('latest_price')))}\">{_fmt(_as_number(trade.get('latest_price')))}</td>"
            f"<td data-sort-value=\"{_sort_value(_as_number(trade.get('exit_price')))}\">{_fmt(_as_number(trade.get('exit_price')))}</td>"
            f"<td>{escape(str(trade.get('exit_reason', '')))}</td>"
            f"<td data-sort-value=\"{_sort_value(pnl)}\">{_change_number(pnl, suffix=' 元')}</td>"
            f"<td data-sort-value=\"{_sort_value(_as_number(trade.get('return_pct')))}\">{_change_number(_as_number(trade.get('return_pct')), suffix='%')}</td>"
            "</tr>"
        )
    return (
        "<div class=\"summary\">"
        f"{_metric('持倉中', summary.open_count)}"
        f"{_metric('已出場', summary.closed_count)}"
        f"{_metric('獲利筆數', summary.win_count)}"
        f"{_metric('虧損筆數', summary.loss_count)}"
        f"{_metric_text('總損益', f'{summary.total_pnl:+.0f} 元')}"
        f"{_metric_text('勝率', f'{summary.win_rate:.0f}%')}"
        "</div>"
        "<table class=\"sortable\"><thead><tr>"
        "<th data-sort=\"text\">狀態</th><th data-sort=\"text\">標的</th><th data-sort=\"text\">方向</th>"
        "<th data-sort=\"text\">進場時間</th><th data-sort=\"number\">進場價</th><th data-sort=\"number\">股數</th>"
        "<th data-sort=\"number\">最新價</th><th data-sort=\"number\">出場價</th><th data-sort=\"text\">出場原因</th>"
        "<th data-sort=\"number\">損益</th><th data-sort=\"number\">報酬率</th></tr></thead><tbody>"
        + ("".join(rows) or "<tr><td colspan=\"11\">尚無交易明細。</td></tr>")
        + "</tbody></table>"
    )


def _market_table(notes: List[str]) -> str:
    rows = "".join(f"<tr><td>{escape(note)}</td></tr>" for note in notes) or "<tr><td>無市場摘要。</td></tr>"
    return f"<table><tbody>{rows}</tbody></table>"


def _market_indicator_table(indicators: List[MarketIndicator]) -> str:
    if not indicators:
        return "<table><tbody><tr><td>目前沒有足夠資料計算盤前市場指標。</td></tr></tbody></table>"
    rows = []
    for item in indicators:
        rows.append(
            "<tr>"
            f"<td>{escape(item.group)}</td>"
            f"<td><strong>{escape(item.name)}</strong><br><span class=\"muted\">{escape(item.symbol)}</span></td>"
            f"<td data-sort-value=\"{escape(item.value)}\">{escape(item.value)}</td>"
            f"<td data-sort-value=\"{_change_sort_value(item.change)}\">{_change_text(item.change)}</td>"
            f"<td>{escape(item.status)}</td>"
            f"<td class=\"notes\">{escape(item.note)}</td>"
            "</tr>"
        )
    return (
        "<table class=\"sortable\"><thead><tr><th data-sort=\"text\">類別</th><th data-sort=\"text\">指標</th>"
        "<th data-sort=\"number\">最新值</th><th data-sort=\"number\">漲跌</th>"
        "<th data-sort=\"text\">解讀</th><th>備註</th></tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table>"
    )


def _sector_table(sectors: List[SectorStrength]) -> str:
    rows = []
    for item in sectors:
        rows.append(
            "<tr>"
            f"<td>{escape(sector_label(item.sector))}</td><td>{escape(item.direction)}</td><td data-sort-value=\"{_sort_value(item.score)}\">{_change_number(item.score)}</td>"
            f"<td data-sort-value=\"{_sort_value(item.member_count)}\">{item.member_count}</td><td data-sort-value=\"{_sort_value(item.avg_one_day_return)}\">{_change_number(item.avg_one_day_return, suffix='%')}</td>"
            f"<td data-sort-value=\"{_sort_value(item.avg_five_day_return)}\">{_change_number(item.avg_five_day_return, suffix='%')}</td><td data-sort-value=\"{_sort_value(item.avg_relative_strength)}\">{_change_number(item.avg_relative_strength, suffix='%')}</td>"
            f"<td data-sort-value=\"{_sort_value(item.bullish_count - item.bearish_count)}\">{item.bullish_count}/{item.bearish_count}</td></tr>"
        )
    return (
        "<table class=\"sortable\"><thead><tr><th data-sort=\"text\">族群</th><th data-sort=\"text\">狀態</th>"
        "<th data-sort=\"number\">分數</th><th data-sort=\"number\">檔數</th>"
        "<th data-sort=\"number\">1日</th><th data-sort=\"number\">5日</th><th data-sort=\"number\">相對大盤</th>"
        "<th data-sort=\"number\">上/下</th></tr></thead><tbody>"
        + ("".join(rows) or "<tr><td colspan=\"8\">無族群資料。</td></tr>")
        + "</tbody></table>"
    )


def _opening_sector_table(sectors: List[SectorOpeningStrength]) -> str:
    rows = []
    for item in sectors:
        rows.append(
            "<tr>"
            f"<td>{escape(sector_label(item.sector))}</td><td>{escape(item.direction)}</td><td data-sort-value=\"{_sort_value(item.score)}\">{_change_number(item.score)}</td>"
            f"<td data-sort-value=\"{_sort_value(item.member_count)}\">{item.member_count}</td><td data-sort-value=\"{_sort_value(item.confirmed_long_count)}\">{item.confirmed_long_count}</td>"
            f"<td data-sort-value=\"{_sort_value(item.confirmed_short_count)}\">{item.confirmed_short_count}</td><td data-sort-value=\"{_sort_value(item.watch_count)}\">{item.watch_count}</td>"
            f"<td data-sort-value=\"{_sort_value(item.avg_volume_ratio)}\">{item.avg_volume_ratio:.2f}x</td></tr>"
        )
    return (
        "<table class=\"sortable\"><thead><tr><th data-sort=\"text\">族群</th><th data-sort=\"text\">狀態</th>"
        "<th data-sort=\"number\">分數</th><th data-sort=\"number\">檔數</th>"
        "<th data-sort=\"number\">做多</th><th data-sort=\"number\">做空</th><th data-sort=\"number\">觀望</th>"
        "<th data-sort=\"number\">平均量比</th></tr></thead><tbody>"
        + ("".join(rows) or "<tr><td colspan=\"8\">無開盤族群資料。</td></tr>")
        + "</tbody></table>"
    )


def _direction(value: str) -> str:
    class_name = "dir-long" if "多" in value else "dir-short" if "空" in value else ""
    return f'<span class="{class_name}">{escape(value)}</span>'


def _grade_label(value: str) -> str:
    return {
        "A": "強勢做多觀察",
        "B": "可追蹤，等回測",
        "C": "題材股，風險偏高",
        "D": "暫不建議做多",
    }.get(value, value)


def _yes_no(value: bool) -> str:
    return "是" if value else "否"


def _money(value: Optional[float]) -> str:
    if value is None:
        return "-"
    if value >= 100_000_000:
        return f"{value / 100_000_000:.2f} 億"
    if value >= 10_000:
        return f"{value / 10_000:.0f} 萬"
    return f"{value:.0f}"


def _fmt(value: Optional[float]) -> str:
    if value is None:
        return "-"
    return f"{value:.2f}"


def _as_number(value) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _sort_value(value: Optional[float]) -> str:
    if value is None:
        return ""
    return f"{float(value):.6f}"


def _number_cell(value: Optional[float]) -> str:
    return f'<td data-sort-value="{_sort_value(value)}">{_fmt(value)}</td>'


def _change_cell(value: Optional[float], suffix: str = "%") -> str:
    return f'<td data-sort-value="{_sort_value(value)}">{_change_number(value, suffix=suffix)}</td>'


def _change_number(value: Optional[float], suffix: str = "") -> str:
    if value is None:
        return '<span class="num-flat">-</span>'
    class_name = "num-up" if value > 0 else "num-down" if value < 0 else "num-flat"
    return f'<span class="{class_name}">{value:+.2f}{escape(suffix)}</span>'


def _change_text(value: str) -> str:
    text = value.strip()
    if text.startswith("+"):
        class_name = "num-up"
    elif text.startswith("-"):
        class_name = "num-down"
    else:
        class_name = "num-flat"
    return f'<span class="{class_name}">{escape(value)}</span>'


def _change_sort_value(value: str) -> str:
    text = value.strip().replace("%", "")
    if not text or text == "-":
        return ""
    try:
        return f"{float(text):.6f}"
    except ValueError:
        return ""
