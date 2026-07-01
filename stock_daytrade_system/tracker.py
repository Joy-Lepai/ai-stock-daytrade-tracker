from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from html import escape
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from stock_daytrade_system.cmoney import CMoneyRanking
from stock_daytrade_system.breakout_trap_diagnosis import build_breakout_trap_diagnosis
from stock_daytrade_system.frontend_language import front_decision_card, front_trade_counts, front_trade_view
from stock_daytrade_system.entry_radar_summary import build_entry_radar_summary
from stock_daytrade_system.intraday import OpeningSignal
from stock_daytrade_system.labels import sector_label, stock_label
from stock_daytrade_system.long_model import LongModelSummary
from stock_daytrade_system.market_mode import evaluate_tw_market_mode
from stock_daytrade_system.market_context import MarketIndicator
from stock_daytrade_system.paper_trading import PaperTradingSummary
from stock_daytrade_system.performance import SignalPerformanceSummary
from stock_daytrade_system.scoring import CandidateScore, MarketBias
from stock_daytrade_system.sectors import SectorOpeningStrength, SectorStrength
from stock_daytrade_system.session_policy import time_bucket_for_market


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
        if _is_opening_long_confirmation(opening.direction):
            score += 3.0
            reasons.append("開盤偏多確認")
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
        label = "方向偏多"
    elif score >= 5:
        label = "偏多"
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
        if _is_opening_long_confirmation(opening_direction) or opening_direction == "做空確認":
            notes.append("盤中有確認，但部位需降至零股或提高風險額度才可執行")
        return "風險過高", 3, notes

    if _is_opening_long_confirmation(opening_direction) or opening_direction == "做空確認":
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
    mode_payload = _dashboard_market_mode(long_summary, report_time)
    strong_funnel = ((long_summary.diagnostics or {}).get("strong_long_funnel") if long_summary else None) or {}
    header_front = front_trade_counts(
        list(long_summary.candidates) if long_summary else [],
        data_today=bool(mode_payload.get("is_data_current_for_mode", True)),
        intraday=bool(mode_payload.get("allow_intraday_signal", True)),
        stale=mode_payload.get("mode") == "stale_data",
        allow_strong_long=bool(mode_payload.get("allow_strong_long", True)),
        market_mode=str(mode_payload.get("mode", "intraday")),
    )["counts"]
    header_strong_long = int(strong_funnel.get("strong_long_candidate_count", header_front.get("強烈買多", 0)) or 0)
    header_executable = int(strong_funnel.get("executable_count", checklist.get("executable", 0) or 0) or 0)

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
    .notice {{
      margin: 12px 0;
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
    .debug-block summary {{ cursor: pointer; font-weight: 750; }}
    .health-ok {{ color: #067647; font-weight: 750; }}
    .health-warn {{ color: #8a5a00; font-weight: 750; }}
    .health-bad {{ color: #b42318; font-weight: 750; }}
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
    .b-方向偏多 {{ color: #fff; background: var(--long); border-color: var(--long); }}
    .b-偏多 {{ color: var(--long); background: #fff1f3; border-color: #fecdd3; }}
    .b-偏多觀察 {{ color: #9a3412; background: #fff7ed; border-color: #fed7aa; }}
    .b-低度偏多, .b-不明確 {{ color: #475467; background: #f2f4f7; }}
    .bias-long {{ color: #fff; background: var(--long); border-color: var(--long); }}
    .bias-short {{ color: #fff; background: var(--short); border-color: var(--short); }}
    .bias-watch {{ color: #475467; background: #f2f4f7; border-color: var(--line); }}
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
    .decision-center {{
      margin-top: 18px;
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 14px;
    }}
    .decision-center h2 {{ margin-top: 0; }}
    .decision-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(210px, 1fr));
      gap: 12px;
      margin-top: 12px;
    }}
    .decision-panel {{
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 12px;
      background: #fbfcfe;
    }}
    .decision-panel strong {{ display: block; margin-bottom: 4px; }}
    .decision-list {{ margin: 6px 0 0; padding-left: 18px; color: var(--muted); }}
    .selection-explainer summary {{
      cursor: pointer;
      font-weight: 800;
      font-size: 16px;
      list-style-position: inside;
    }}
    .selection-explainer[open] summary {{ margin-bottom: 10px; }}
    .signal-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(230px, 1fr));
      gap: 12px;
      margin-top: 12px;
    }}
    .signal-column {{
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--panel);
      padding: 12px;
      min-height: 120px;
    }}
    .signal-column h3 {{ margin: 0 0 10px; font-size: 15px; }}
    .signal-card {{
      border-top: 1px solid var(--line);
      padding: 10px 0;
    }}
    .signal-card:first-of-type {{ border-top: 0; padding-top: 0; }}
    .signal-title {{ font-weight: 750; }}
    .signal-meta {{ color: var(--muted); font-size: 12px; white-space: normal; }}
    .signal-next {{ margin-top: 6px; font-weight: 700; color: var(--blue); }}
    .scan-form {{
      display: flex;
      align-items: end;
      gap: 10px;
      flex-wrap: wrap;
      margin: 12px 0;
      padding: 12px;
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
    }}
    .scan-form label {{ font-weight: 700; }}
    .scan-form input {{
      display: block;
      min-width: 180px;
      margin-top: 4px;
      padding: 8px 9px;
      border: 1px solid var(--line);
      border-radius: 6px;
      font: inherit;
    }}
    .scan-form button {{
      border: 1px solid var(--blue);
      background: var(--blue);
      color: #fff;
      border-radius: 6px;
      padding: 8px 12px;
      font: inherit;
      cursor: pointer;
    }}
    .scan-result {{ margin: 8px 0 14px; }}
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
      {_metric('強烈買多', header_strong_long)}
      {_metric('進場雷達通過', header_executable)}
      {_metric('買多', int(header_front.get('買多', 0)))}
      {_metric('觀察', int(header_front.get('觀察', 0)))}
      {_metric('看空', int(header_front.get('看空', 0)))}
      {_metric_text('模式', str(mode_payload.get('label', '未知')))}
    </div>
    {_data_status_block(statuses)}
    {_warning_block(warnings)}
  </header>
  <main>
    {_market_mode_panel(long_summary, report_time)}
    {_today_playbook_panel(long_summary, report_time)}
    {_candidate_selection_explainer(long_summary)}
    {_decision_overview(long_summary, report_time)}
    {_precision_gap_overview(long_summary, report_time)}
    {_ai_decision_center(long_summary)}
    {_signal_center(long_summary, report_time)}
    {_fugle_priority_pool_panel(long_summary)}
    {_trend_continuation_panel(long_summary, report_time)}
    {_position_command_center(long_summary)}
    {_review_mode_sections(long_summary, report_time)}
    <h2>本週模型觀察</h2>
    {_model_observation_panel(long_summary)}
    <h2>進場雷達成績單</h2>
    {_entry_radar_scorecard_panel(long_summary)}
    <h2>真假突破診斷成績單</h2>
    {_breakout_trap_scorecard_panel(long_summary)}
    <h2>資料健康度</h2>
    {_data_health_panel(long_summary)}
    <h2>台股全市場異動掃描池</h2>
    {_full_market_scan_panel(long_summary)}
    <h2>漲停強勢股診斷</h2>
    {_limit_up_strength_panel(long_summary)}
    <h2>漏抓股票診斷</h2>
    {_missed_stock_diagnostic_table(long_summary)}
    <h2>模型條件診斷</h2>
    {_model_diagnostic_panel(long_summary)}
    <h2>強烈買多漏斗</h2>
    {_strong_long_funnel_panel(long_summary)}
    <h2>做多判斷時間框架診斷</h2>
    {_timeframe_gap_report_panel(long_summary)}
    <h2>明日續強候選股</h2>
    <div class="table-wrap">{_tomorrow_continuation_candidates(long_summary)}</div>
    <h2>明日觀察池</h2>
    <div class="table-wrap">{_tomorrow_long_watch_pool(long_summary)}</div>
    <h2>今日異動股掃描</h2>
    <div class="table-wrap">{_momentum_scan_table(long_summary)}</div>
    {_manual_scan_panel()}
    <h2>信心雷達</h2>
    {_confidence_radar(long_summary)}
    <h2>今日候選股分級 MVP</h2>
    <section class="notice">B+ 為策略練習觀察，不代表正式做多建議。</section>
    <div class="table-wrap">{_long_candidate_table(long_summary)}</div>
    <h2>今日推薦檢查表</h2>
    <div class="table-wrap">{_recommendation_checklist_table(long_summary)}</div>
    <h2>B+ 觸發條件追蹤</h2>
    <div class="table-wrap">{_b_plus_trigger_table(long_summary)}</div>
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
    <h2>使用者說明</h2>
    {_user_guide_panel(long_summary)}
    <h2>開發者資訊</h2>
    {_debug_block(long_summary)}
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

      const scanInput = document.getElementById("tw-scan-symbol");
      const scanResult = document.getElementById("tw-scan-result");
      const escapeHtml = (value) => String(value ?? "-")
        .replaceAll("&", "&amp;").replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;").replaceAll('"', "&quot;");
      const scanSymbol = async (endpoint) => {{
        const symbol = scanInput?.value.trim();
        if (!symbol) {{
          scanResult.innerHTML = '<section class="warn">請先輸入股票代號或名稱。</section>';
          return;
        }}
        scanResult.innerHTML = '<section class="data-status">掃描中，正在抓取行情並跑模型...</section>';
        try {{
          const response = await fetch(endpoint, {{
            method: "POST",
            headers: {{ "Content-Type": "application/json" }},
            body: JSON.stringify({{ symbol }}),
          }});
          const payload = await response.json();
          const scan = payload.scan || {{}};
          const candidate = payload.candidate || {{}};
          const status = payload.ok ? "data-status" : "warn";
          scanResult.innerHTML = `<section class="${{status}}">
            <strong>${{escapeHtml(payload.symbol)}}｜${{escapeHtml(payload.name)}}：${{escapeHtml(payload.message)}}</strong><br>
            最新價 ${{escapeHtml(scan.latest_price)}}｜漲跌幅 ${{escapeHtml(scan.change_pct)}}%｜量比 ${{escapeHtml(scan.volume_ratio)}}x｜
            AI 評級 ${{escapeHtml(candidate.grade || scan.ai_grade)}}｜entry_status ${{escapeHtml(candidate.entry_status || scan.entry_status)}}<br>
            當下狀態：${{escapeHtml(candidate.trade_bias_label || scan.trade_bias_label || "觀察")}}｜${{escapeHtml(candidate.trade_bias_reason || scan.trade_bias_reason || "")}}<br>
            未入選原因：${{escapeHtml(candidate.not_selected_reason || scan.not_selected_reason || scan.data_error || "-")}}<br>
            <span class="muted">${{escapeHtml(candidate.confidence_summary || (scan.source_reasons || []).join("；"))}}</span>
          </section>`;
        }} catch (error) {{
          scanResult.innerHTML = `<section class="warn">掃描失敗：${{escapeHtml(error.message)}}</section>`;
        }}
      }};
      document.getElementById("tw-scan-button")?.addEventListener("click", () => scanSymbol("/api/tw/scan/symbol"));
      document.getElementById("tw-add-watch-button")?.addEventListener("click", () => scanSymbol("/api/tw/watchlist/add"));
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
        candidate_direction == "做多觀察" and _is_opening_long_confirmation(opening_direction)
    ) or (
        candidate_direction == "做空觀察" and opening_direction == "做空確認"
    )


def _is_opening_long_confirmation(value: str) -> bool:
    return value == "偏多確認" or (value.startswith("做多") and value.endswith("確認"))


def _first_number(*values: Optional[float]) -> Optional[float]:
    for value in values:
        if value is not None:
            return value
    return None


def _first_int(*values) -> int:
    for value in values:
        try:
            if value is None or value == "":
                continue
            return int(value)
        except (TypeError, ValueError):
            continue
    return 0


def _status_counts(rows: Iterable[TrackedSymbol]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for row in rows:
        counts[row.status] = counts.get(row.status, 0) + 1
    return counts


def _metric(label: str, value: int) -> str:
    return f'<div class="metric"><span class="muted">{escape(label)}</span><strong>{value}</strong></div>'


def _metric_text(label: str, value: str) -> str:
    return f'<div class="metric"><span class="muted">{escape(label)}</span><strong>{escape(value)}</strong></div>'


def _advisor_link(symbol: str) -> str:
    return f"/tw/advisor?symbol={escape(str(symbol or ''), quote=True)}"


def _position_size_tag(entry_price, stop_loss) -> str:
    entry = "" if entry_price is None else str(entry_price)
    stop = "" if stop_loss is None else str(stop_loss)
    return (
        '<span class="position-size-tag" '
        f'data-position-entry="{escape(entry, quote=True)}" '
        f'data-position-stop="{escape(stop, quote=True)}"></span>'
    )


def _dashboard_market_mode(summary: Optional[LongModelSummary], report_time: datetime) -> dict:
    diagnostics = summary.diagnostics if summary else {}
    health = (diagnostics or {}).get("data_health") or {}
    mode = evaluate_tw_market_mode(
        now=report_time,
        data_date=health.get("data_date"),
        latest_data_at=health.get("latest_intraday_at") or health.get("last_success_at"),
        data_stale=bool(health.get("is_stale")),
        severe_missing=str(health.get("status") or "") in {"異常", "嚴重缺漏"},
        watchlist_fresh=True,
        positions_fresh=True,
    )
    return mode.to_dict()


def _market_mode_panel(summary: Optional[LongModelSummary], report_time: datetime) -> str:
    mode = _dashboard_market_mode(summary, report_time)
    diagnostics = summary.diagnostics if summary else {}
    health = (diagnostics or {}).get("data_health") or {}
    checklist = summary.recommendation_checklist if summary else {}
    front = front_trade_counts(
        list(summary.candidates) if summary else [],
        data_today=bool(mode.get("is_data_current_for_mode")),
        intraday=bool(mode.get("allow_intraday_signal")),
        stale=mode.get("mode") == "stale_data",
        allow_strong_long=bool(mode.get("allow_strong_long")),
        market_mode=str(mode.get("mode")),
    )["counts"]
    reason = _no_strong_long_reason(front, checklist, health, mode)
    can_trade = "可作為盤中追蹤依據" if mode.get("allow_intraday_signal") else "不提供即時進場判斷"
    confidence = _mode_aware_data_confidence(health, mode)
    now_action = _market_mode_now_action(front, checklist, health, mode)
    details = (
        '<details class="debug-block">'
        '<summary>模式細節</summary>'
        '<div class="summary">'
        f'{_metric_text("market_mode", str(mode.get("mode") or "-"))}'
        f'{_metric_text("是否交易日", "是" if mode.get("is_trading_day") else "否")}'
        f'{_metric_text("是否休市日", "是" if mode.get("is_holiday") else "否")}'
        f'{_metric_text("last_trading_date", str(mode.get("last_trading_date") or "-"))}'
        f'{_metric_text("資料日期", str(mode.get("data_date") or "-"))}'
        f'{_metric_text("盤中訊號", "允許" if mode.get("allow_intraday_signal") else "禁止")}'
        '</div>'
        '</details>'
    )
    return (
        '<section class="decision-center">'
        '<h2>台股做多當沖追蹤器 v1</h2>'
        f'<section class="notice">{escape(str(mode.get("message", "")))}</section>'
        '<div class="summary">'
        f'{_metric_text("現在模式", str(mode.get("label", "未知")))}'
        f'{_metric_text("現在要做", now_action)}'
        f'{_metric("強烈買多", int(front.get("強烈買多", 0)))}'
        f'{_metric("買多", int(front.get("買多", 0)))}'
        f'{_metric("觀察", int(front.get("觀察", 0)))}'
        f'{_metric("看空", int(front.get("看空", 0)))}'
        f'{_metric_text("資料可信度", confidence)}'
        f'{_metric_text("即時交易依據", can_trade)}'
        '</div>'
        f'<div class="notice"><strong>主要原因</strong><br>{escape(reason)}</div>'
        f'{details}'
        '</section>'
    )


def _market_mode_now_action(front: dict, checklist: dict, health: dict, mode: dict) -> str:
    mode_name = str(mode.get("mode") or "")
    if mode_name == "intraday":
        strong = int(front.get("強烈買多", 0) or 0)
        buy = int(front.get("買多", 0) or 0)
        executable = int(checklist.get("executable", 0) or 0)
        if executable > 0:
            return f"先看進場雷達通過 {executable} 檔，逐檔確認停損與部位。"
        if strong > 0 or buy > 0:
            return f"盯強烈買多 {strong} 檔與買多 {buy} 檔，等待觸發，不提前追。"
        return "沒有即時可進場條件，先等量能、VWAP 或突破確認。"
    if mode_name == "pre_open_prepare":
        return "只整理觀察清單，09:00 後等 VWAP、量比與突破。"
    if mode_name == "closed_review":
        return "休市只做復盤與下個交易日準備，不看即時進場。"
    if mode_name == "post_close_review":
        return "盤後驗證今天表現，整理明天觀察清單。"
    if mode_name == "stale_data" or health.get("status") in {"異常", "嚴重缺漏"}:
        return "先修資料更新，資料恢復前不做進場判斷。"
    return "先確認資料可信度與刷新狀態，再看觀察清單。"


def _no_strong_long_reason(front: dict, checklist: dict, health: dict, mode: dict) -> str:
    if mode.get("mode") == "stale_data":
        return "資料過期或缺漏嚴重，僅供參考。"
    if mode.get("mode") == "pre_open_prepare":
        return "目前是開盤前準備模式，尚未有今日 VWAP、量比與盤中突破確認；只整理觀察清單，不產生即時買多判斷。"
    if mode.get("mode") != "intraday":
        return "目前不是盤中模式，以下資料僅供復盤與下個交易日觀察。"
    if int(front.get("強烈買多", 0)) > 0:
        return "已有強烈買多候選，仍需依停損與部位風險控管。"
    waits = []
    for key, label in (("wait_volume", "等待量能"), ("wait_vwap", "等待站回 VWAP"), ("wait_breakout", "等待突破"), ("high_risk", "追價風險高")):
        count = int(checklist.get(key, 0) or 0)
        if count:
            waits.append(f"{label} {count} 檔")
    if health.get("status") not in {None, "", "正常"}:
        waits.append(f"資料狀態：{health.get('status')}")
    return "今日沒有強烈買多標的；" + ("、".join(waits) if waits else "主要條件尚未完整確認。")


def _today_playbook_panel(summary: Optional[LongModelSummary], report_time: datetime) -> str:
    mode = _dashboard_market_mode(summary, report_time)
    front_context = _front_context(summary, mode)
    front_counts = front_trade_counts(list(summary.candidates) if summary else [], **front_context)["counts"]
    checklist = summary.recommendation_checklist if summary else {}
    strong = int(front_counts.get("強烈買多", 0) or 0)
    buy = int(front_counts.get("買多", 0) or 0)
    watch = int(front_counts.get("觀察", 0) or 0)
    bearish = int(front_counts.get("看空", 0) or 0)
    radar_passed = int(checklist.get("executable", 0) or 0)
    mode_name = str(mode.get("mode") or "")
    time_bucket = time_bucket_for_market(report_time, "TW")

    if mode_name == "intraday" and time_bucket == "opening_observation":
        headline = "開盤觀察 09:00-09:20：先看量價，不急著進場"
        step_one = f"先看哪些股票仍站上 VWAP、量比開始放大；強烈買多 {strong} 檔、買多 {buy} 檔只列入盯盤。"
        step_two = "等 5 到 20 分鐘形成開盤區間，再確認是否突破開盤高、守住 VWAP、買盤沒有退潮。"
        step_three = "第一波急拉、接近漲停、high_risk 或停損距離過大的股票，不追；只等回測或雷達通過。"
    elif mode_name == "intraday":
        headline = "盤中作戰：只盯通過資料與風控的標的"
        step_one = f"先看強烈買多 {strong} 檔、買多 {buy} 檔；進場雷達通過 {radar_passed} 檔才進入下一步。"
        step_two = "每檔都要確認 VWAP、量比、突破、停損距離與資料狀態；high_risk 只觀察，不追價。"
        step_three = "若資料轉 delayed / cached / missing，立刻降為觀察；持倉只依停損、停利與失效條件處理。"
    elif mode_name == "pre_open_prepare":
        headline = "開盤前作戰：先挑清單，不提前進場"
        step_one = f"目前只整理上一交易日觀察池：觀察 {watch} 檔、看空 {bearish} 檔；不顯示即時買多。"
        step_two = "09:00 後先等 5 到 10 分鐘，確認今日 VWAP、量比、開盤區間與是否突破。"
        step_three = "只把最接近條件的 5 檔放進重點盯盤；資料沒有 live 前，不做強烈買多判斷。"
    elif mode_name == "closed_review":
        headline = "休市作戰：復盤與準備下個交易日"
        step_one = "檢查上一交易日哪些股票有動能，但不要把它當成即時訊號。"
        step_two = "優先看下個交易日觀察清單、真假突破結果與 high_risk 是否過度保守。"
        step_three = "下個交易日開盤後，再用 VWAP、量比與進場雷達重新確認。"
    elif mode_name == "post_close_review":
        headline = "盤後作戰：驗證今天，整理明天"
        step_one = "檢查今日強烈買多、買多與觀察股後續表現。"
        step_two = "看停利 / 停損 / 最大回撤，找出模型太嚴或太鬆的地方。"
        step_three = "只把盤後驗證後仍有結構的股票放入明日觀察，不追高。"
    else:
        headline = "資料防呆：目前不適合交易"
        step_one = "資料過期、缺漏或刷新狀態不一致時，不產生即時買多。"
        step_two = "先按「更新重點觀察」或等待自動刷新；確認資料狀態恢復 live。"
        step_three = "資料沒有恢復前，只能復盤與觀察，不能作為進場依據。"

    rows = "".join(
        f'<div class="decision-panel"><strong>{escape(title)}</strong><p class="muted">{escape(text)}</p></div>'
        for title, text in (
            ("1. 現在先做", step_one),
            ("2. 等待確認", step_two),
            ("3. 風控底線", step_three),
        )
    )
    return (
        '<section class="decision-center">'
        '<h2>今日作戰流程</h2>'
        f'<section class="notice"><strong>{escape(headline)}</strong></section>'
        f'<div class="decision-grid">{rows}</div>'
        '</section>'
    )


def _candidate_selection_explainer(summary: Optional[LongModelSummary]) -> str:
    diagnostics = summary.diagnostics if summary else {}
    full_scan = (diagnostics.get("full_market_scan") or {}) if diagnostics else {}
    full_data = full_scan.get("data") or full_scan.get("summary") or {}
    full_source = full_scan.get("source_status") or {}
    by_status = full_scan.get("by_status") or {}
    momentum_summary = ((summary.momentum_scan or {}).get("summary") if summary else {}) or {}
    debug_info = summary.debug_info if summary else {}
    funnel = (diagnostics.get("strong_long_funnel") or {}) if diagnostics else {}
    pool_count = _first_int(
        full_data.get("pool_symbols"),
        debug_info.get("full_market_pool_symbols"),
        len(full_scan.get("pool_symbols") or []),
    )
    twse_count = _first_int(full_data.get("twse_count"))
    tpex_count = _first_int(full_data.get("tpex_count"))
    momentum_count = _first_int(
        funnel.get("momentum_candidate_count"),
        full_data.get("candidate_symbols"),
        debug_info.get("full_market_candidate_symbols"),
        momentum_summary.get("total"),
        len(full_scan.get("candidate_symbols") or []),
    )
    scored_count = _first_int(
        funnel.get("model_candidates_count"),
        funnel.get("model_scored_count"),
        full_data.get("scored_symbols"),
        momentum_summary.get("model_scored"),
        debug_info.get("momentum_scan_model_scored"),
        len(summary.candidates) if summary else 0,
    )
    out_of_pool = int(by_status.get("out_of_pool", full_scan.get("out_of_pool_count", 0) or 0) or 0)
    high_risk = _first_int(funnel.get("blocked_high_risk_count"), funnel.get("blocked_high_risk"), by_status.get("high_risk"))
    wait_volume = _first_int(funnel.get("blocked_wait_volume_count"), funnel.get("blocked_wait_volume"), by_status.get("wait_volume"))
    wait_vwap = _first_int(funnel.get("blocked_wait_vwap_count"), funnel.get("blocked_wait_vwap"), by_status.get("wait_vwap"))
    wait_breakout = _first_int(funnel.get("blocked_wait_breakout_count"), funnel.get("blocked_wait_breakout"), by_status.get("wait_breakout"))
    strong = int(funnel.get("strong_long_candidate_count", 0) or 0)
    executable = int(funnel.get("executable_count", 0) or 0)
    if full_source:
        twse = "成功" if full_source.get("twse_ok") else ("使用 cache" if full_source.get("twse_used_cache") else "失敗")
        tpex = "成功" if full_source.get("tpex_ok") else ("使用 cache" if full_source.get("tpex_used_cache") else "失敗或未納入")
        source_line = f"TWSE {twse} / TPEX {tpex}"
    else:
        source_line = "等待下一次全市場掃描更新。"
    return (
        '<details class="decision-center selection-explainer">'
        '<summary>候選股怎麼選出來？</summary>'
        '<p class="muted">系統不是直接找漲最多的股票，而是先從全市場找出異動股，再送進 VWAP、量比、突破、風險、資料可信度與進場雷達檢查。'
        '沒有通過的股票不會消失，會被標成觀察、high_risk、wait_vwap、wait_volume 或看空。</p>'
        '<div class="summary">'
        f'{_metric("完整普通股池", pool_count)}'
        f'{_metric("上市", twse_count)}'
        f'{_metric("上櫃", tpex_count)}'
        f'{_metric("今日異動候選", momentum_count)}'
        f'{_metric("送入模型評分", scored_count)}'
        f'{_metric("原觀察池外新找到", out_of_pool)}'
        f'{_metric("強烈買多", strong)}'
        f'{_metric("進場雷達通過", executable)}'
        '</div>'
        '<div class="decision-grid">'
        '<div class="decision-panel">'
        '<strong>第一步：找得到</strong>'
        '<p class="muted">掃上市 + 上櫃普通股，排除 ETF、權證、特殊股與低流動性標的，再找今日漲幅、成交金額、量比、突破與接近漲停的異動股。</p>'
        f'<p class="muted">資料源狀態：{escape(source_line)}</p>'
        '</div>'
        '<div class="decision-panel">'
        '<strong>第二步：能不能追</strong>'
        '<p class="muted">每檔都檢查是否站上 VWAP、量比是否放大、是否突破昨日高點或盤中關鍵高點、停損距離是否合理、資料是否即時。</p>'
        f'<p class="muted">常見卡關：high_risk {high_risk} 檔、wait_volume {wait_volume} 檔、wait_vwap {wait_vwap} 檔、wait_breakout {wait_breakout} 檔。</p>'
        '</div>'
        '<div class="decision-panel">'
        '<strong>第三步：怎麼看</strong>'
        '<p class="muted">強烈買多代表值得重點盯盤；買多代表方向偏多但仍等確認；觀察代表有動能但目前不適合進；看空代表多方結構失效。</p>'
        '<p class="muted">法人、族群、Fugle 五檔與逐筆只作背景與進場前確認，不會直接把股票升級成強烈買多。</p>'
        '</div>'
        '<div class="decision-panel">'
        '<strong>如果覺得某檔漏掉</strong>'
        '<p class="muted">到「個股建議」輸入股票代號，例如 8150.TW，系統會顯示它是否進入異動池、目前四分類、最大卡關、下一步與 reason code。</p>'
        '<p class="muted">若顯示 high_risk、wait_vwap、wait_volume 或資料不足，代表系統有看到但沒有列為買多；這不是漏抓，而是風險或條件未過。</p>'
        '</div>'
        '</div>'
        '</details>'
    )


def _mode_aware_data_confidence(health: dict, mode: dict) -> str:
    if mode.get("mode") == "pre_open_prepare" and mode.get("is_data_current_for_mode"):
        status = str(health.get("status") or "").strip()
        if status and status not in {"正常", "過期"}:
            return f"開盤前準備：上一交易日資料完整度：{status}"
        return "開盤前準備：使用上一交易日資料"
    if mode.get("mode") == "closed_review" and mode.get("is_data_current_for_mode"):
        status = str(health.get("status") or "").strip()
        if status and status not in {"正常", "過期"}:
            return f"上一交易日資料完整度：{status}"
        return "休市復盤：使用上一交易日資料"
    if mode.get("mode") == "post_close_review" and mode.get("is_data_current_for_mode"):
        status = str(health.get("status") or "").strip()
        return f"盤後復盤：今日資料{status}" if status else "盤後復盤：今日資料"
    return str(health.get("status") or "未知")


def _front_context(summary: Optional[LongModelSummary], market_mode: Optional[dict] = None) -> dict:
    diagnostics = summary.diagnostics if summary else {}
    health = (diagnostics or {}).get("data_health") or {}
    mode = str((market_mode or {}).get("mode") or "")
    if mode:
        stale = mode == "stale_data"
        intraday = bool((market_mode or {}).get("allow_intraday_signal"))
    else:
        stale = bool(health.get("is_stale")) or str(health.get("status") or "") in {"過期", "異常", "嚴重缺漏"}
        intraday = bool(health.get("is_intraday_session", True)) and not stale
        mode = "intraday" if intraday else ("stale_data" if stale else "closed_review")
    return {
        "data_today": bool((market_mode or {}).get("is_data_current_for_mode", health.get("is_today_data", True))),
        "intraday": intraday,
        "stale": stale,
        "allow_strong_long": bool((market_mode or {}).get("allow_strong_long", intraday and not stale)),
        "market_mode": mode,
        "price_status_label": str(health.get("live_state_label") or health.get("live_state") or ""),
        "uses_last_known": bool(health.get("uses_last_known")),
        "is_delayed": bool(health.get("is_delayed")),
    }


def _decision_overview(summary: Optional[LongModelSummary], report_time: datetime) -> str:
    data = summary.decision_center if summary else {}
    diagnostics = summary.diagnostics if summary else {}
    health = (diagnostics.get("data_health") or {}) if diagnostics else {}
    checklist = summary.recommendation_checklist if summary else {}
    counts = data.get("counts", {}) if data else {}
    mode = _dashboard_market_mode(summary, report_time)
    front_context = _front_context(summary, mode)
    front = front_trade_counts(
        list(summary.candidates) if summary else [],
        **front_context,
    )
    front_counts = front["counts"]
    strong_funnel = (diagnostics or {}).get("strong_long_funnel") or {}
    limit_up = _limit_up_context_from_summary(summary, diagnostics)
    limit_brief = _limit_up_brief(limit_up)
    strong_long_count = int(strong_funnel.get("strong_long_candidate_count", front_counts.get("強烈買多", 0)) or 0)
    executable_count = int(strong_funnel.get("executable_count", checklist.get("executable", 0) or 0) or 0)
    tendency = data.get("operation_tendency") or "資料不足"
    confidence = _mode_aware_data_confidence(health, mode)
    if mode.get("mode") == "pre_open_prepare":
        reminder = "目前是開盤前準備模式；以下只整理上一交易日觀察清單。請等開盤後確認今日 VWAP、量比、突破與進場雷達，不要提前當成即時買多。"
    elif mode.get("mode") == "closed_review":
        reminder = "目前是休市復盤模式；以下只供復盤與下個交易日觀察，不提供即時買多判斷。"
    elif mode.get("mode") == "post_close_review":
        reminder = "目前是盤後復盤模式；請用來檢查今日訊號結果與下個交易日觀察清單，不提供即時買多判斷。"
    elif strong_long_count <= 0:
        reminder = f"今日沒有強烈買多標的，主要原因：{_strong_long_blocker_summary(strong_funnel)}。建議保守觀望；買多與練習買多都必須等待條件確認。"
    else:
        reminder = (
            f"目前有 {strong_long_count} 檔強烈買多候選，其中 {executable_count} 檔進場雷達通過；"
            "強烈買多代表值得立即盯盤，進場雷達通過才代表進場條件更完整。"
        )
    if mode.get("mode") == "stale_data":
        reminder = "資料不完整或過期，僅供觀察，不建議交易。"
    elif mode.get("mode") == "intraday" and limit_brief["count"] > 0:
        reminder += " " + limit_brief["reminder"]
    elif mode.get("mode") in {"post_close_review", "closed_review", "pre_open_prepare"} and limit_brief["count"] > 0:
        reminder += (
            f" 今日/上一交易日有 {int(limit_brief['count'])} 檔接近漲停或急拉，"
            "已放進漲停強勢速讀與下個交易日觀察；這不是即時買多，明天仍要等 VWAP、量比、突破與進場雷達重新確認。"
        )
    reminder += _non_intraday_bearish_guard_copy(front_counts, str(mode.get("mode") or ""))
    closest_title, observation_title, closest_empty, observation_empty = _decision_overview_section_copy(str(mode.get("mode") or ""))
    closest_items = _top_decision_items(summary, front_context, categories={"強烈買多", "買多"}, limit=5)
    observation_items = _top_decision_items(summary, front_context, categories={"觀察"}, limit=10)
    closest_html = "".join(_focus_card(item, front_context) for item in closest_items) or f'<p class="muted">{escape(closest_empty)}</p>'
    observation_html = "".join(_focus_card(item, front_context) for item in observation_items) or f'<p class="muted">{escape(observation_empty)}</p>'
    sector_summary = _top_sector_summary(summary)
    institutional_summary = _institutional_background_summary(summary)
    front_diagnostics = _front_category_diagnostics(front["views"])
    return (
        '<section class="decision-center">'
        '<h2>今日決策摘要</h2>'
        '<div class="summary">'
        f'{_metric_text("今日市場狀態", str(tendency))}'
        f'{_metric_text("今日資料可信度", str(confidence))}'
        f'{_metric("強烈買多", strong_long_count)}'
        f'{_metric("進場雷達通過", executable_count)}'
        f'{_metric("買多", int(front_counts.get("買多", 0)))}'
        f'{_metric("觀察", int(front_counts.get("觀察", 0)))}'
        f'{_metric("看空", int(front_counts.get("看空", 0)))}'
        f'{_metric("接近漲停 / 漲停", int(limit_brief["count"]))}'
        f'{_metric("漲停高風險觀察", int(limit_brief["high_risk"]))}'
        f'{_metric("漲停真漏抓", int(limit_brief["missed"]))}'
        f'{_metric_text("今日強勢族群", sector_summary)}'
        f'{_metric_text("籌碼背景提醒", institutional_summary)}'
        '</div>'
        f'<div class="notice"><strong>今日最重要提醒</strong><br>{escape(str(reminder))}</div>'
        f'{_limit_up_brief_notice(limit_brief)}'
        f'{front_diagnostics}'
        f'<h3>{escape(closest_title)}</h3>'
        f'<div class="signal-grid">{closest_html}</div>'
        f'<h3>{escape(observation_title)}</h3>'
        f'<div class="signal-grid">{observation_html}</div>'
        '</section>'
    )


def _limit_up_brief(data: dict) -> dict:
    count = int(data.get("near_limit_up_count", 0) or 0)
    high_risk = int(data.get("high_risk_count", 0) or 0)
    entered = int(data.get("entered_ai_count", 0) or 0)
    missed = int(data.get("missed_by_pool_count", 0) or 0)
    data_missing = int(data.get("data_missing_count", 0) or 0)
    locked = int(data.get("locked_count", 0) or 0)
    wait_confirm = int(data.get("wait_confirm_count", 0) or 0)
    action_summary = str(data.get("action_summary") or "")
    if count <= 0:
        headline = "目前沒有接近漲停或漲停鎖住的掃描標的。"
        reminder = ""
        action = "不用追漲停，回到強烈買多漏斗與進場雷達。"
        wait_for = "等待新的異動候選或重點觀察股觸發。"
        avoid = "不要因為別處看到漲停新聞就臨時改模型。"
    elif missed > 0:
        headline = f"有 {count} 檔接近漲停 / 漲停，其中 {missed} 檔是真漏抓，需檢查候選池或資料源。"
        reminder = f"漲停強勢股診斷顯示 {missed} 檔真漏抓，先看資料源與候選池門檻，不要只看四分類結果。"
        action = "先查真漏抓清單與 reason code，確認是否資料源、候選池門檻或上市櫃池問題。"
        wait_for = "等全市場掃描與資料源恢復後，再重新送進模型評分。"
        avoid = "不要手動把漏抓股直接升級買多。"
    elif high_risk > 0:
        headline = f"有 {count} 檔接近漲停 / 漲停，系統有看到；{action_summary or f'其中 {high_risk} 檔被列為追價高風險。'}"
        reminder = f"今天有 {count} 檔接近漲停 / 漲停，已看到的高風險股不等於看空，而是避免追價。"
        action = "先看 high_risk 股票是否拉回 VWAP 附近、停損距離縮小，或進場雷達轉強。"
        wait_for = "等待拉回不破 VWAP、量能延續、五檔賣壓降低或重新突破後再評估。"
        avoid = "不要在漲停附近直接追價，也不要把 high_risk 當成可進場。"
    elif locked > 0:
        headline = f"有 {count} 檔接近漲停 / 漲停；{action_summary or f'{locked} 檔鎖漲停先觀察。'}"
        reminder = "鎖漲停代表買盤堆積，但不能用市價追；重點是打開後是否承接。"
        action = "只記錄與盯盤，等打開後看 VWAP 是否守住、買盤是否延續。"
        wait_for = "等待打開後回測不破 VWAP、停損距離合理、進場雷達轉強。"
        avoid = "不要在鎖漲停時追價，也不要把鎖住視為保證續強。"
    elif wait_confirm > 0:
        headline = f"有 {count} 檔接近漲停 / 漲停；{action_summary or f'{wait_confirm} 檔仍等待確認。'}"
        reminder = "急拉股已被看到，但仍缺 VWAP、量能、突破或停損距離確認。"
        action = "逐檔看下一步條件，不提前追第一波。"
        wait_for = "等待缺口條件補齊，或拉回不破後再重新評估。"
        avoid = "不要把等待確認包裝成買多。"
    elif entered > 0:
        headline = f"有 {count} 檔接近漲停 / 漲停，其中 {entered} 檔進入 A/B+/B 觀察層。"
        reminder = f"接近漲停股已有 {entered} 檔進入模型層，仍需看 VWAP、停損距離與進場雷達。"
        action = "只盯已進 A/B+/B 的標的，逐檔檢查停損距離與進場雷達。"
        wait_for = "等待進場雷達通過，或等待拉回後仍站穩 VWAP。"
        avoid = "不要因為進入模型層就省略停損與部位風控。"
    elif data_missing > 0:
        headline = f"有 {count} 檔接近漲停 / 漲停，但 {data_missing} 檔資料不足。"
        reminder = f"接近漲停股有資料不足，不能硬判買多。"
        action = "先看資料狀態與最後更新時間，必要時更新重點觀察或完整刷新。"
        wait_for = "等待 price_status 回到 live，且 VWAP、量比與停損價都完整。"
        avoid = "不要用 cached / delayed / missing 資料做即時進場。"
    else:
        headline = f"有 {count} 檔接近漲停 / 漲停，已列入診斷觀察。"
        reminder = f"接近漲停股需先看追價風險與停損距離，不直接升級買多。"
        action = "先看每檔最大卡關原因，再決定是否只放觀察。"
        wait_for = "等待 VWAP、量比、突破與風控同時改善。"
        avoid = "不要只因漲幅大就追。"
    return {
        "count": count,
        "high_risk": high_risk,
        "entered": entered,
        "missed": missed,
        "data_missing": data_missing,
        "locked": locked,
        "wait_confirm": wait_confirm,
        "action_summary": action_summary,
        "headline": headline,
        "reminder": reminder,
        "action": action,
        "wait_for": wait_for,
        "avoid": avoid,
    }


def _limit_up_context_from_summary(summary: Optional[LongModelSummary], diagnostics: Optional[dict]) -> dict:
    explicit = ((diagnostics or {}).get("limit_up_strength_analysis") if diagnostics else None) or {}
    if int(explicit.get("near_limit_up_count", 0) or 0) > 0:
        return explicit
    inferred = _infer_limit_up_context(summary)
    if int(inferred.get("near_limit_up_count", 0) or 0) > 0:
        return inferred
    return explicit


def _infer_limit_up_context(summary: Optional[LongModelSummary]) -> dict:
    items: list[dict] = []
    if summary and isinstance(summary.momentum_scan, dict):
        raw_items = summary.momentum_scan.get("items")
        if isinstance(raw_items, list):
            items.extend(item for item in raw_items if isinstance(item, dict))
    if summary:
        for candidate in summary.candidates or []:
            if isinstance(candidate, dict):
                items.append(candidate)
            elif hasattr(candidate, "__dict__"):
                items.append(dict(candidate.__dict__))

    seen: dict[str, dict] = {}
    for item in items:
        symbol = str(item.get("symbol") or "").upper()
        if not symbol:
            continue
        if not _is_limit_up_like_item(item):
            continue
        seen.setdefault(symbol, item)

    rows = list(seen.values())
    if not rows:
        return {}

    entered = 0
    high_risk = 0
    wait_confirm = 0
    avoid = 0
    data_missing = 0
    locked = 0
    top_watchlist: list[dict] = []
    for item in rows:
        entry = str(item.get("entry_status") or "")
        grade = str(item.get("ai_grade") or item.get("grade") or "")
        reason_text = " ".join(
            str(value)
            for value in (
                item.get("not_selected_reason"),
                item.get("risk_reason"),
                item.get("risk_reasons"),
                item.get("source_reasons"),
                item.get("limit_up_status"),
            )
        )
        if grade in {"A", "B+", "B"}:
            entered += 1
        if entry == "high_risk" or "追價" in reason_text or "風險高" in reason_text:
            high_risk += 1
        if entry in {"wait_volume", "wait_vwap", "wait_breakout", "wait_pullback"}:
            wait_confirm += 1
        if entry == "avoid":
            avoid += 1
        if item.get("data_error") or item.get("data_missing"):
            data_missing += 1
        if item.get("is_limit_up_locked") or "鎖漲停" in reason_text:
            locked += 1
        top_watchlist.append(
            {
                "symbol": str(item.get("symbol") or ""),
                "name": str(item.get("name") or item.get("name_zh") or ""),
                "change_pct": float(item.get("change_pct") or 0),
                "entry_status": entry or "-",
                "action": "放進追價風險觀察，等拉回 VWAP、停損距離縮小或進場雷達轉強。"
                if entry == "high_risk"
                else "列入下個交易日觀察，明天重新確認 VWAP、量比與突破。",
            }
        )

    action_parts = []
    if high_risk:
        action_parts.append(f"{high_risk} 檔追價風險高")
    if wait_confirm:
        action_parts.append(f"{wait_confirm} 檔等待確認")
    if entered:
        action_parts.append(f"{entered} 檔進入 A/B+/B 觀察層")
    if data_missing:
        action_parts.append(f"{data_missing} 檔資料不足")

    return {
        "definition": "此區由全市場異動池與候選清單推估接近漲停 / 急拉股；不會把追價高風險股票升級成買多。",
        "near_limit_up_count": len(rows),
        "seen_count": len(rows),
        "entered_ai_count": entered,
        "high_risk_count": high_risk,
        "chase_risk_count": high_risk,
        "wait_confirm_count": wait_confirm,
        "avoid_count": avoid,
        "data_missing_count": data_missing,
        "locked_count": locked,
        "missed_by_pool_count": 0,
        "action_summary": "；".join(action_parts) + "。" if action_parts else f"{len(rows)} 檔急拉 / 漲停觀察。",
        "top_watchlist": top_watchlist[:10],
        "source": "inferred_from_momentum_scan",
    }


def _is_limit_up_like_item(item: dict) -> bool:
    if item.get("near_limit_up") or item.get("limit_up") or item.get("is_limit_up_locked"):
        return True
    change_pct = float(item.get("change_pct") or 0)
    if change_pct >= 9.0:
        return True
    text = " ".join(
        str(value)
        for value in (
            item.get("source_reasons"),
            item.get("not_selected_reason"),
            item.get("risk_reason"),
            item.get("risk_reasons"),
            item.get("limit_up_status"),
            item.get("entry_radar_summary"),
        )
    )
    return any(token in text for token in ("漲停", "接近漲停", "急拉", "爆量漲停"))


def _limit_up_brief_notice(brief: dict) -> str:
    if int(brief.get("count", 0) or 0) <= 0:
        return ""
    return (
        '<section class="notice">'
        f'<strong>漲停強勢速讀</strong><br>{escape(str(brief.get("headline") or ""))}'
        '<br><span class="muted">接近漲停代表動能強，但也可能是追價高風險；請往下看「漲停強勢股診斷」確認是有看到、等待確認、資料不足，還是真漏抓。不會把追價高風險股票升級成買多。</span>'
        '<div class="decision-grid">'
        f'<div class="decision-panel"><strong>現在先做</strong><p class="muted">{escape(str(brief.get("action") or ""))}</p></div>'
        f'<div class="decision-panel"><strong>等到什麼</strong><p class="muted">{escape(str(brief.get("wait_for") or ""))}</p></div>'
        f'<div class="decision-panel"><strong>不要做</strong><p class="muted">{escape(str(brief.get("avoid") or ""))}</p></div>'
        '</div>'
        '</section>'
    )


def _non_intraday_bearish_guard_copy(front_counts: dict, mode: str) -> str:
    if mode == "intraday":
        return ""
    strong = int(front_counts.get("強烈買多", 0) or 0)
    buy = int(front_counts.get("買多", 0) or 0)
    watch = int(front_counts.get("觀察", 0) or 0)
    bearish = int(front_counts.get("看空", 0) or 0)
    if strong or buy:
        return ""
    total = max(strong + buy + watch + bearish, 1)
    if (watch + bearish) / total < 0.6:
        return ""
    return " 目前非買多比例偏高，這不代表全市場都適合做空；非盤中模式只用來復盤與等待開盤確認。"


def _decision_overview_section_copy(mode: str) -> tuple[str, str, str, str]:
    if mode == "pre_open_prepare":
        return (
            "開盤前重點盯盤 5 檔",
            "開盤後等待確認清單 10 檔",
            "開盤前尚未有可列入重點盯盤的標的；請等 09:00 後確認今日 VWAP、量比與突破。",
            "目前沒有開盤後等待確認標的。",
        )
    if mode == "post_close_review":
        return (
            "今日盤後復盤重點 5 檔",
            "下個交易日觀察清單 10 檔",
            "今日沒有可列為盤後復盤重點的強烈買多 / 買多標的。",
            "目前沒有下個交易日觀察標的。",
        )
    if mode == "closed_review":
        return (
            "上一交易日復盤重點 5 檔",
            "下個交易日觀察清單 10 檔",
            "上一交易日沒有可列為復盤重點的強烈買多 / 買多標的。",
            "目前沒有下個交易日觀察標的。",
        )
    if mode == "stale_data":
        return (
            "資料不足，暫停即時重點盯盤",
            "資料不足觀察清單",
            "資料不完整或過期，暫停列出即時強烈買多。",
            "資料不完整或過期，僅保留可復盤的觀察資料。",
        )
    return (
        "最接近強烈買多 5 檔",
        "等待確認池 10 檔",
        "目前沒有接近強烈買多的標的。",
        "目前沒有等待確認標的。",
    )


def _precision_gap_overview(summary: Optional[LongModelSummary], report_time: datetime) -> str:
    mode = _dashboard_market_mode(summary, report_time)
    candidates = list(summary.candidates) if summary else []
    total = len(candidates)
    has_vwap = sum(1 for item in candidates if item.vwap is not None)
    has_volume_ratio = sum(1 for item in candidates if item.volume_ratio is not None)
    has_stop_loss = sum(1 for item in candidates if item.stop_loss is not None)
    has_target_price = sum(1 for item in candidates if item.target_price is not None)
    intraday_ready = 0
    vwap_hold = 0
    trend_structure = 0
    volume_continuation = 0
    institutional_context = 0
    sector_context = 0
    for item in candidates:
        intraday = ((getattr(item, "timeframe_diagnostics", {}) or {}).get("intraday_window") or {})
        if intraday:
            intraday_ready += 1
        if intraday.get("vwap_stay_ok") or intraday.get("vwap_hold_ok") or intraday.get("vwap_above_minutes"):
            vwap_hold += 1
        if intraday.get("higher_high") and intraday.get("higher_low"):
            trend_structure += 1
        if intraday.get("volume_continuation") or intraday.get("price_up_volume_up"):
            volume_continuation += 1
        inst = getattr(item, "institutional_context", {}) or {}
        if inst.get("institutional_data_status") in {"ok", "partial"} or inst.get("status") in {"ok", "partial"}:
            institutional_context += 1
        sector = getattr(item, "sector_context", {}) or {}
        if sector.get("sector_status") not in {None, "", "unknown"} or sector.get("sector_status_label"):
            sector_context += 1
    missing_tick = total
    missing_orderbook = total
    missing_live_news = total
    can_precise_intraday = (
        bool(mode.get("allow_intraday_signal"))
        and total > 0
        and missing_tick == 0
        and missing_orderbook == 0
    )
    if can_precise_intraday:
        message = "核心即時資料完整，可進一步檢查個股進場條件。"
    elif mode.get("mode") != "intraday":
        message = "目前不是盤中模式；此區僅用來檢查資料缺口，不提供即時進場判斷。"
    else:
        message = "尚未接入逐筆成交與五檔委買委賣，因此即使模型偏多，也不視為高精準即時進場訊號。"
    return (
        '<section class="decision-center">'
        '<h2>精準資料缺口總覽</h2>'
        f'<section class="notice">{escape(message)}</section>'
        '<div class="summary">'
        f'{_metric("追蹤標的", total)}'
        f'{_metric("有 VWAP", has_vwap)}'
        f'{_metric("有量比", has_volume_ratio)}'
        f'{_metric("有停損價", has_stop_loss)}'
        f'{_metric("有停利價", has_target_price)}'
        f'{_metric("有盤中K線診斷", intraday_ready)}'
        f'{_metric("VWAP守穩可判讀", vwap_hold)}'
        f'{_metric("趨勢結構可判讀", trend_structure)}'
        f'{_metric("量能延續可判讀", volume_continuation)}'
        f'{_metric("三大法人背景", institutional_context)}'
        f'{_metric("族群背景", sector_context)}'
        f'{_metric("缺逐筆 Tick", missing_tick)}'
        f'{_metric("缺五檔委買委賣", missing_orderbook)}'
        f'{_metric("缺即時新聞題材", missing_live_news)}'
        f'{_metric_text("高精準即時進場", "允許" if can_precise_intraday else "不允許")}'
        '</div>'
        '<p class="muted">此區只說明資料完整度與缺口，不會調整 A / B+ / B 條件，也不會增加推薦數量。</p>'
        '</section>'
    )


def _top_sector_summary(summary: Optional[LongModelSummary]) -> str:
    if summary is None or not summary.sector_heat:
        return "暫無族群資料"
    top = [item for item in summary.sector_heat if item.score > 0][:3]
    if not top:
        return "族群未明顯同步"
    return "、".join(f"{sector_label(item.sector)} {item.score:.1f}" for item in top)


def _institutional_background_summary(summary: Optional[LongModelSummary]) -> str:
    if summary is None:
        return "籌碼資料不足"
    counts: dict[str, int] = {}
    for item in summary.candidates:
        label = str((getattr(item, "institutional_context", {}) or {}).get("institutional_label") or "籌碼資料不足")
        counts[label] = counts.get(label, 0) + 1
    if not counts:
        return "籌碼資料不足"
    top = sorted(counts.items(), key=lambda row: (-row[1], row[0]))[:2]
    return "、".join(f"{label} {count} 檔" for label, count in top)


def _top_focus_items(summary: Optional[LongModelSummary]) -> list[LongCandidate]:
    if summary is None:
        return []
    order = {"executable": 0, "practice_long": 1, "wait_breakout": 2, "wait_vwap": 3, "wait_volume": 4, "high_risk": 5}
    rows = [
        item for item in summary.candidates
        if item.grade in {"A", "B+", "B"} or item.entry_status in {"executable", "practice_long", "wait_breakout", "wait_vwap", "wait_volume", "high_risk"}
    ]
    rows.sort(key=lambda item: (order.get(item.entry_status, 9), item.risk_score, -item.bullish_score, item.symbol))
    return rows[:10]


def _top_decision_items(
    summary: Optional[LongModelSummary],
    front_context: dict,
    *,
    categories: set[str],
    limit: int,
) -> list[LongCandidate]:
    if summary is None:
        return []
    scored: list[tuple[float, float, str, LongCandidate]] = []
    for item in summary.candidates:
        if item.grade not in {"A", "B+", "B", "C"} and item.entry_status not in {"executable", "practice_long", "wait_breakout", "wait_vwap", "wait_volume", "high_risk"}:
            continue
        front = front_trade_view(item, **front_context)
        radar = _dashboard_entry_radar(item, front_context)
        decision = front_decision_card(item, front_view=front, entry_radar=radar, **front_context)
        if decision.final_decision not in categories:
            continue
        scored.append((decision.precision_score, item.bullish_score, item.symbol, item))
    scored.sort(key=lambda row: (-row[0], -row[1], row[2]))
    return [item for *_rest, item in scored[:limit]]


def _focus_card(item: LongCandidate, front_context: Optional[dict] = None) -> str:
    front = front_trade_view(item, **(front_context or {}))
    radar = _dashboard_entry_radar(item, front_context)
    decision = front_decision_card(item, front_view=front, entry_radar=radar, **(front_context or {}))
    trap = _dashboard_breakout_trap(item, front_context)
    data_badge = _candidate_price_status_badge(item, front_context)
    risk_reason = "；".join(item.risk_reasons[:3]) or item.conflict_summary or item.confidence_adjustment_reason or "目前無額外風險提醒。"
    bullish_reason = _display_reason_for_candidate(item)
    background = f"{_institutional_badge(item)}｜{_sector_context_badge(item)}"
    return (
        '<div class="decision-panel">'
        f'<strong><a href="{_advisor_link(item.symbol)}">{escape(item.symbol)}｜{escape(item.name)}</a>{_position_size_tag(item.trigger_price or item.last_price, item.stop_loss)}</strong>'
        f'<div>{escape(decision.final_decision)}｜{escape(decision.entry_state)}｜{escape(decision.observation_type)}｜{data_badge}</div>'
        f'<p class="muted"><strong>背景：</strong>{background}</p>'
        f'<p class="muted"><strong>已有條件：</strong>{escape(bullish_reason)}</p>'
        f'<p class="muted"><strong>最大原因 / 最大卡關：</strong>{escape(decision.top_reason)}</p>'
        f'<p class="muted"><strong>真假突破：</strong>{escape(trap.status_label)}｜{escape(trap.summary)}</p>'
        f'<p class="muted"><strong>下一步：</strong>{escape(decision.next_trigger)}</p>'
        f'<p class="muted"><strong>失效條件：</strong>{escape(decision.invalid_condition)}</p>'
        f'<p class="muted"><strong>精準分數：</strong>{decision.precision_score:.0f} / 100</p>'
        f'<p class="muted"><strong>風險提醒：</strong>{escape(risk_reason)}</p>'
        '</div>'
    )


def _model_observation_panel(summary: Optional[LongModelSummary]) -> str:
    diagnostics = summary.diagnostics if summary else {}
    notes = list((diagnostics or {}).get("model_observations") or [])
    scorecard = ((diagnostics or {}).get("strategy_scorecard") or {}).get("windows", {}).get("20", {})
    missed = (diagnostics or {}).get("missed_rate_report") or {}
    note_html = "".join(f"<li>{escape(str(item))}</li>" for item in notes) or "<li>目前樣本不足，先累積資料。</li>"
    groups = scorecard.get("groups") or {}
    a_win = float((groups.get("A") or {}).get("win_rate", 0) or 0)
    b_plus_win = float((groups.get("B+") or {}).get("win_rate", 0) or 0)
    high_risk_up = float((groups.get("high_risk") or {}).get("continue_up_rate", 0) or 0)
    true_missed_rate = float(missed.get("missed_by_pool_rate", missed.get("missed_rate", 0)) or 0)
    seen_filtered_rate = float(missed.get("seen_but_filtered_rate", 0) or 0)
    regret = missed.get("regret_after_close") or {}
    regret_rate = float(regret.get("rate", 0) or 0)
    return (
        '<section class="decision-center">'
        '<div class="summary">'
        f'{_metric("20日樣本", int(scorecard.get("sample_size", 0) or 0))}'
        f'{_metric_text("A勝率", f"{a_win:.2f}%")}'
        f'{_metric_text("B+勝率", f"{b_plus_win:.2f}%")}'
        f'{_metric_text("high_risk續漲", f"{high_risk_up:.2f}%")}'
        f'{_metric_text("真漏抓率", f"{true_missed_rate:.2f}%")}'
        f'{_metric_text("已看到未推薦", f"{seen_filtered_rate:.2f}%")}'
        f'{_metric_text("盤後可惜漏掉", f"{regret_rate:.2f}%")}'
        '</div>'
        f'<ul class="decision-list">{note_html}</ul>'
        '</section>'
    )


def _fugle_priority_pool_panel(summary: Optional[LongModelSummary]) -> str:
    pool = ((summary.diagnostics or {}).get("fugle_priority_pool") if summary else None) or {}
    rows = list(pool.get("selected") or [])
    status_text = "已啟用" if pool.get("enabled") else "未啟用"
    configured_text = "API Key 已設定" if pool.get("configured") else "API Key 未設定"
    radar_status_text = _fugle_radar_status_label(str(pool.get("entry_radar_status") or "waiting"))
    pinned_text = "、".join(str(item) for item in (pool.get("pinned_symbols") or [])) or "未指定"
    allocation = pool.get("allocation_summary") or {}
    allocation_text = str(allocation.get("summary") or "目前沒有配置即時追蹤名額。")
    allocation_warning = str(allocation.get("warning") or "")
    allocation_warning_html = f'<br><span class="muted">{escape(allocation_warning)}</span>' if allocation_warning else ""
    api_budget_text = str(pool.get("api_budget_message") or "Fugle API 預算尚未估算。")
    selection_explanation = str(pool.get("selection_explanation") or "Fugle 追蹤池尚未整理入選說明。")
    next_candidate = str(pool.get("next_candidate_symbol") or "")
    next_gap = pool.get("next_candidate_gap")
    next_candidate_note = (
        f'<br><span class="muted">下一候補：{escape(next_candidate)}，距離第 5 名順位分差 {_fmt(next_gap)}。</span>'
        if next_candidate
        else ""
    )
    capability = pool.get("capability_summary") or {}
    capability_text = str(capability.get("summary") or "Fugle 能力狀態尚未整理。")
    capability_note = str(capability.get("trading_note") or "Fugle 只作行情確認，不作自動下單。")
    health_html = _fugle_entry_radar_health_panel(pool.get("entry_radar_health") or {})
    service_warning = ""
    if not pool.get("enabled") or not pool.get("configured"):
        service_warning = (
            '<section class="warn">'
            'Fugle 尚未完整啟用或 API Key 未設定；以下只代表「應該優先追蹤的 5 檔名單」，'
            '尚未取得即時五檔 / 逐筆成交確認，不可當成進場依據。'
            '</section>'
        )
    acceptance_html = _fugle_acceptance_checklist(pool, rows)
    quick_read_html = _fugle_quick_read_panel(rows)
    standby_html = _fugle_standby_panel(list(pool.get("standby") or []))
    if not rows:
        return (
            '<section class="decision-center">'
            '<h2>Fugle 5檔即時追蹤池</h2>'
            '<section class="notice">此區用來分配 Fugle 基本用戶 5 檔即時追蹤名額；不會改模型、不會增加推薦、不會自動下單。</section>'
            '<div class="summary">'
            f'{_metric_text("Fugle 狀態", status_text)}'
            f'{_metric_text("金鑰狀態", configured_text)}'
            f'{_metric_text("雷達狀態", radar_status_text)}'
            f'{_metric_text("指定追蹤", pinned_text)}'
            f'{_metric("可追蹤名額", int(pool.get("max_symbols", 5) or 5))}'
            f'{_metric("已選標的", 0)}'
            '</div>'
            f'<section class="notice"><strong>名額配置：</strong>{escape(allocation_text)}</section>'
            f'<section class="notice"><strong>方案能力：</strong>{escape(capability_text)}<br><span class="muted">{escape(capability_note)}</span></section>'
            f'<section class="notice"><strong>API 預算：</strong>{escape(api_budget_text)}</section>'
            f'{health_html}'
            f'<section class="notice"><strong>為什麼選這些：</strong>{escape(selection_explanation)}{next_candidate_note}</section>'
            f'{service_warning}'
            f'<p class="muted">{escape(str(pool.get("message") or "目前沒有需要使用 Fugle 即時追蹤的重點標的。"))}</p>'
            f'{acceptance_html}'
            f'{quick_read_html}'
            f'{standby_html}'
            '</section>'
        )
    body = "".join(
        '<tr>'
        f'<td><strong><a href="{_advisor_link(str(item.get("symbol", "")))}">{escape(str(item.get("symbol", "")))}｜{escape(str(item.get("name", "")))}</a></strong><br><span class="muted">{escape(str(item.get("selection_label") or item.get("tracking_purpose", "")))}</span><br><span class="muted">{escape(str(item.get("tracking_purpose", "")))}</span></td>'
        f'<td>{escape(str(item.get("grade", "-")))}</td>'
        f'<td>{escape(_entry_status_label(str(item.get("entry_status", "-"))))}</td>'
        f'<td>{_fmt(item.get("last_price"))}</td>'
        f'<td>{_fmt(item.get("vwap"))}</td>'
        f'<td>{_fmt(item.get("volume_ratio"))}x</td>'
        f'<td>{_fmt(item.get("orderbook_imbalance"))}%<br><span class="muted">買{_fmt_int(item.get("bid_total_volume"))} / 賣{_fmt_int(item.get("ask_total_volume"))}</span></td>'
        f'<td>{escape(_trend_label(str(item.get("bid_volume_trend", ""))))}<br><span class="muted">{escape(str(item.get("bid_volume_trend_summary") or "委買快照不足"))}</span></td>'
        f'<td>{escape(_trend_label(str(item.get("ask_volume_trend", ""))))}<br><span class="muted">{escape(str(item.get("ask_volume_trend_summary") or "委賣快照不足"))}</span></td>'
        f'<td>{escape(_large_trade_label(str(item.get("large_trade_status", ""))))}<br><span class="muted">{escape(str(item.get("large_trade_summary") or "缺逐筆成交資料"))}</span></td>'
        f'<td>{escape(_price_tick_label(str(item.get("price_tick_trend", ""))))}<br><span class="muted">{escape(str(item.get("price_tick_summary") or "最新價快照不足"))}</span></td>'
        f'<td><strong>{escape(_confirmation_quality_label(item))}</strong><br><span class="muted">{escape(str(item.get("confirmation_quality_reason") or "等待下一次雷達更新。"))}</span></td>'
        f'<td class="notes"><strong>{escape(str(item.get("entry_confirmation_status_label") or "等待確認"))}</strong><br>{escape(str(item.get("entry_confirmation_summary") or "等待 Fugle 更新進場雷達。"))}<br><span class="muted">下一步：{escape(str(item.get("entry_confirmation_next_step") or "等待下一次重點追蹤刷新。"))}</span></td>'
        f'<td>{escape("可做盤口確認" if item.get("can_use_for_entry_confirmation") else "僅觀察")}</td>'
        f'<td class="notes">{escape(str(item.get("selection_reason") or item.get("priority_reason", "")))}<br><span class="muted">現在看：{escape(str(item.get("watch_now") or "等待下一次重點追蹤刷新。"))}</span></td>'
        '</tr>'
        for item in rows
    )
    return (
        '<section class="decision-center">'
        '<h2>Fugle 5檔即時追蹤池</h2>'
        '<section class="notice">基本用戶最多追蹤 5 檔；此區只做即時五檔 / 逐筆確認資源配置，不會改 A / B+ / B 條件，也不會自動下單。</section>'
        '<div class="summary">'
        f'{_metric_text("Fugle 狀態", status_text)}'
        f'{_metric_text("金鑰狀態", configured_text)}'
        f'{_metric_text("雷達狀態", radar_status_text)}'
        f'{_metric_text("指定追蹤", pinned_text)}'
        f'{_metric("可追蹤名額", int(pool.get("max_symbols", 5) or 5))}'
        f'{_metric("已選標的", int(pool.get("selected_count", len(rows)) or len(rows)))}'
        f'{_metric("其餘候選", int(pool.get("excluded_count", 0) or 0))}'
        f'{_metric("雷達成功", int(pool.get("confirmation_success_count", 0) or 0))}'
        f'{_metric("雷達不足", int(pool.get("confirmation_failed_count", 0) or 0))}'
        f'{_metric("實際 API 呼叫", int(pool.get("actual_api_calls", 0) or 0))}'
        '</div>'
        f'<section class="notice"><strong>名額配置：</strong>{escape(allocation_text)}'
        f'{allocation_warning_html}</section>'
        f'<section class="notice"><strong>方案能力：</strong>{escape(capability_text)}<br><span class="muted">{escape(capability_note)}</span></section>'
        f'<section class="notice"><strong>API 預算：</strong>{escape(api_budget_text)}</section>'
        f'{health_html}'
        f'<section class="notice"><strong>為什麼選這些：</strong>{escape(selection_explanation)}{next_candidate_note}</section>'
        f'{service_warning}'
        f'<p class="muted">{escape(str(pool.get("entry_radar_message") or pool.get("message") or ""))}</p>'
        f'{acceptance_html}'
        f'{quick_read_html}'
        f'{standby_html}'
        '<div class="table-wrap"><table><thead><tr>'
        '<th>股票</th><th>分級</th><th>狀態</th><th>現價</th><th>VWAP</th><th>量比</th><th>五檔買賣盤差</th><th>委買量變化</th><th>委賣量變化</th><th>大單敲進 / 敲出</th><th>最新價墊高</th><th>確認品質</th><th>進場雷達總結</th><th>進場確認</th><th>入選原因</th>'
        '</tr></thead><tbody>'
        f'{body}'
        '</tbody></table></div>'
        '</section>'
    )


def _fugle_entry_radar_health_panel(health: dict) -> str:
    if not isinstance(health, dict) or not health:
        return (
            '<section class="notice">'
            '<strong>追蹤池健康：</strong>尚未取得 Fugle 進場雷達健康摘要；請先刷新重點觀察。'
            '</section>'
        )
    operator_status = str(health.get("operator_status") or "unknown")
    status_label = {
        "ready": "可用於進場前確認",
        "limited": "名額受限，僅前 5 檔可確認",
        "degraded": "部分失敗，只能局部確認",
        "not_ready": "尚未可用",
        "empty": "目前沒有追蹤標的",
    }.get(operator_status, "狀態待確認")
    budget_label = "API 預算安全" if str(health.get("api_budget_status") or "") == "safe" else "API 接近上限"
    can_entry = "可做完整確認" if health.get("can_use_for_entry_confirmation") else "不可直接作進場確認"
    return (
        '<section class="notice">'
        f'<strong>追蹤池健康：</strong>{escape(status_label)}｜{escape(can_entry)}｜{escape(budget_label)}<br>'
        '<span class="muted">'
        f'追蹤 {int(health.get("success_count", 0) or 0)} / {int(health.get("tracking_limit", 5) or 5)} 檔，'
        f'失敗 {int(health.get("failed_count", 0) or 0)} 檔，'
        f'跳過 {int(health.get("skipped_count", 0) or 0)} 檔，'
        f'估計 {float(health.get("estimated_calls_per_minute", 0) or 0):.2f}/min。'
        '</span><br>'
        f'<span class="muted">下一步：{escape(str(health.get("next_action") or "等待下一次重點追蹤刷新。"))}</span>'
        '</section>'
    )


def _fugle_standby_panel(rows: list[dict]) -> str:
    if not rows:
        return (
            '<section class="notice">'
            '<strong>Fugle 名額外候補：</strong>目前沒有候補標的。'
            '</section>'
        )
    items = []
    for item in rows[:10]:
        symbol = str(item.get("symbol") or "")
        name = str(item.get("name") or "")
        reason = str(item.get("not_selected_reason") or "Fugle 基本用戶 5 檔名額已滿。")
        promotion = str(item.get("promotion_condition") or "前 5 檔條件失效，或此檔更接近觸發時可升入。")
        priority = _fmt(item.get("priority_score"))
        purpose = str(item.get("tracking_purpose") or "")
        items.append(
            f'<li><a href="{_advisor_link(symbol)}">{escape(symbol)}｜{escape(name)}</a>'
            f'｜順位分 {priority}｜{escape(purpose)}<br><span class="muted">{escape(reason)} {escape(str(item.get("priority_reason") or ""))}</span>'
            f'<br><span class="muted">升入條件：{escape(promotion)}</span></li>'
        )
    return (
        '<details class="developer-info">'
        '<summary>Fugle 名額外候補</summary>'
        '<p class="muted">這些股票有進入即時追蹤候補，但基本用戶 5 檔名額已滿；不代表模型排除，只是即時 API 資源不足。</p>'
        f'<ul class="compact-list">{"".join(items)}</ul>'
        '</details>'
    )


def _fugle_quick_read_panel(rows: list[dict]) -> str:
    if not rows:
        return (
            '<section class="notice">'
            '<strong>Fugle 雷達速讀：</strong>目前沒有即時追蹤標的。'
            '</section>'
        )
    items = []
    for item in rows[:5]:
        symbol = str(item.get("symbol") or "")
        name = str(item.get("name") or "")
        label = _fugle_entry_readiness_label(item)
        quality = _confirmation_quality_label(item)
        support = _fugle_support_summary(item)
        gap = _fugle_gap_summary(item)
        items.append(
            f"<li><strong>{escape(symbol)}｜{escape(name)}</strong>："
            f"{escape(label)}｜{escape(quality)}。{escape(support)}；{escape(gap)}</li>"
        )
    return (
        '<section class="notice">'
        '<strong>Fugle 雷達速讀：</strong>'
        '<ul>'
        + "".join(items)
        + "</ul>"
        '</section>'
    )


def _fugle_entry_readiness_label(item: dict) -> str:
    entry_status = str(item.get("entry_status") or "")
    radar_status = str(item.get("entry_confirmation_status") or "")
    if item.get("entry_confirmation_can_consider"):
        return "可做進場前確認"
    if entry_status in {"high_risk", "avoid", "data_missing"}:
        return "僅作風險觀察"
    if radar_status in {"ready", "near"}:
        return "接近觸發，仍需等待原模型條件"
    if radar_status in {"review_only", "blocked"}:
        return "暫不作即時進場判斷"
    return "等待確認"


def _confirmation_quality_label(item: dict) -> str:
    label = str(item.get("confirmation_quality_label") or "")
    if label:
        return label
    quality = str(item.get("confirmation_quality") or "")
    return {
        "high_precision": "高品質確認",
        "standard": "標準確認",
        "limited": "確認資料不足",
        "blocked": "暫不進場",
    }.get(quality, "等待確認")


def _fugle_support_summary(item: dict) -> str:
    supports = []
    if str(item.get("orderbook_status") or "") in {"supportive", "limit_up_locked"}:
        supports.append("五檔買盤偏強")
    if str(item.get("large_trade_status") or "") in {"buy_sweep", "large_buy", "inflow"}:
        supports.append("有大單敲進跡象")
    if str(item.get("price_tick_trend") or "") in {"rising", "stable"}:
        supports.append("最新價未轉弱")
    if str(item.get("bid_volume_trend") or "") in {"improving", "stable"}:
        supports.append("委買量未轉弱")
    return "支持因素：" + ("、".join(supports) if supports else "尚無明確盤中支持")


def _fugle_gap_summary(item: dict) -> str:
    gaps = []
    if str(item.get("orderbook_status") or "") in {"missing", ""}:
        gaps.append("缺五檔")
    elif str(item.get("orderbook_status") or "") == "sell_pressure":
        gaps.append("賣壓偏重")
    if str(item.get("large_trade_status") or "") in {"missing", ""}:
        gaps.append("缺逐筆")
    elif str(item.get("large_trade_status") or "") in {"sell_sweep", "large_sell", "outflow"}:
        gaps.append("疑似大單敲出")
    if str(item.get("price_tick_trend") or "") == "weak":
        gaps.append("最新價轉弱")
    if str(item.get("ask_volume_trend") or "") == "deteriorating":
        gaps.append("委賣量增加")
    if str(item.get("entry_status") or "") == "high_risk":
        gaps.append("追價風險高")
    if str(item.get("entry_status") or "") == "avoid":
        gaps.append("模型避開")
    return "主要缺口：" + ("、".join(gaps) if gaps else "暫無重大缺口")


def _fugle_acceptance_checklist(pool: dict, rows: list[dict]) -> str:
    max_symbols = int(pool.get("max_symbols", 5) or 5)
    selected_symbols = {str(item.get("symbol") or "") for item in rows}
    pinned_symbols = [str(item) for item in (pool.get("pinned_symbols") or []) if str(item)]
    actual_calls = int(pool.get("actual_api_calls", 0) or 0)
    expected_limit = max_symbols * 3
    items = [
        f"追蹤池 {len(rows)} / {max_symbols} 檔，{'未超過' if len(rows) <= max_symbols else '已超過'} Fugle 基本用戶 5 檔設計。",
        f"本次實際 API 呼叫 {actual_calls} 次，{'未超過' if actual_calls <= expected_limit else '已超過'} Quote / Trades / Candles 預估上限 {expected_limit} 次。",
    ]
    if pinned_symbols:
        missing = [symbol for symbol in pinned_symbols if symbol not in selected_symbols]
        items.append(
            "指定追蹤已入池：" + "、".join(symbol for symbol in pinned_symbols if symbol in selected_symbols)
            if not missing
            else "指定追蹤尚未入池：" + "、".join(missing)
        )
    guarded = [
        str(item.get("symbol") or "")
        for item in rows
        if str(item.get("entry_status") or "") in {"high_risk", "avoid", "data_missing"}
        and not item.get("entry_confirmation_can_consider")
    ]
    if guarded:
        items.append("風險防線正常：" + "、".join(guarded[:5]) + " 仍僅供觀察，不作進場確認。")
    elif rows:
        items.append("目前沒有 high_risk / avoid 被誤列為進場確認。")
    return (
        '<section class="notice">'
        '<strong>盤中驗收重點：</strong>'
        '<ul>'
        + "".join(f"<li>{escape(item)}</li>" for item in items)
        + "</ul>"
        '</section>'
    )


def _trend_label(status: str) -> str:
    return {
        "improving": "改善",
        "stable": "持平",
        "deteriorating": "轉弱",
        "missing": "資料不足",
    }.get(status or "missing", "資料不足")


def _fugle_radar_status_label(status: str) -> str:
    return {
        "ok": "已更新",
        "partial": "部分不足",
        "disabled": "未啟用",
        "not_configured": "未設定 Key",
        "waiting": "等待更新",
    }.get(status or "waiting", status or "等待更新")


def _large_trade_label(status: str) -> str:
    return {
        "buy_sweep": "大單敲進",
        "large_buy": "大單敲進",
        "inflow": "大單流入",
        "sell_sweep": "大單敲出",
        "large_sell": "大單敲出",
        "outflow": "大單流出",
        "neutral": "未見大單",
        "unknown": "方向不明",
        "missing": "缺逐筆",
    }.get(status or "missing", "缺逐筆")


def _price_tick_label(status: str) -> str:
    return {
        "rising": "連續墊高",
        "stable": "未轉弱",
        "weak": "轉弱",
        "missing": "資料不足",
    }.get(status or "missing", "資料不足")


def _entry_radar_scorecard_panel(summary: Optional[LongModelSummary]) -> str:
    diagnostics = summary.diagnostics if summary else {}
    scorecard = ((diagnostics or {}).get("entry_radar_scorecard") or {}).get("windows", {}).get("20", {})
    rows = list(scorecard.get("rows") or [])[:6]
    if not rows:
        return (
            '<section class="decision-center">'
            '<p class="muted">目前尚無進場雷達卡關成績資料；需累積盤後驗證後才可判斷。</p>'
            '</section>'
        )
    body = "".join(
        '<tr>'
        f'<td><strong>{escape(str(item.get("blocker_label", "-")))}</strong><br><span class="muted">{escape(str(item.get("blocker_code", "-")))}</span></td>'
        f'<td>{int(item.get("sample_size", 0) or 0)}</td>'
        f'<td>{int(item.get("verified", 0) or 0)}</td>'
        f'<td>{float(item.get("win_rate", 0) or 0):.2f}%</td>'
        f'<td>{float(item.get("target_2_rate", 0) or 0):.2f}%</td>'
        f'<td>{float(item.get("avg_max_gain", 0) or 0):.2f}%</td>'
        f'<td>{float(item.get("avg_max_drawdown", 0) or 0):.2f}%</td>'
        f'<td class="notes">{escape(str(item.get("interpretation") or item.get("sample_message") or ""))}</td>'
        '</tr>'
        for item in rows
    )
    return (
        '<section class="decision-center">'
        '<section class="notice">此區只統計最大卡關原因的盤後表現，不會自動調整 A / B+ / B 條件。</section>'
        '<div class="summary">'
        f'{_metric("20日樣本", int(scorecard.get("sample_size", 0) or 0))}'
        f'{_metric("20日已驗證", int(scorecard.get("verified", 0) or 0))}'
        f'{_metric_text("樣本品質", _scorecard_quality_label(str(scorecard.get("sample_quality", "insufficient"))))}'
        f'{_metric_text("判讀狀態", "可初步觀察" if scorecard.get("is_statistically_meaningful") else "樣本不足")}'
        '</div>'
        f'<p class="muted">{escape(str(scorecard.get("message") or "樣本不足，不建議依卡關原因調整模型。"))}</p>'
        '<div class="table-wrap"><table><thead><tr><th>最大卡關</th><th>出現</th><th>已驗證</th><th>1%勝率</th><th>2%命中</th><th>平均最大漲幅</th><th>平均最大回撤</th><th>解讀</th></tr></thead><tbody>'
        f'{body}'
        '</tbody></table></div>'
        '</section>'
    )


def _breakout_trap_scorecard_panel(summary: Optional[LongModelSummary]) -> str:
    diagnostics = summary.diagnostics if summary else {}
    scorecard = ((diagnostics or {}).get("breakout_trap_scorecard") or {}).get("windows", {}).get("20", {})
    rows = list(scorecard.get("rows") or [])[:6]
    if not rows:
        return (
            '<section class="decision-center">'
            '<p class="muted">目前尚無真假突破診斷成績資料；需累積盤後驗證後才可判斷。</p>'
            '</section>'
        )
    body = "".join(
        '<tr>'
        f'<td><strong>{escape(str(item.get("status_label", "-")))}</strong><br><span class="muted">{escape(str(item.get("status", "-")))}</span></td>'
        f'<td>{int(item.get("sample_size", 0) or 0)}</td>'
        f'<td>{int(item.get("verified", 0) or 0)}</td>'
        f'<td>{float(item.get("target_1_rate", 0) or 0):.2f}%</td>'
        f'<td>{float(item.get("target_2_rate", 0) or 0):.2f}%</td>'
        f'<td>{float(item.get("pullback_rate", 0) or 0):.2f}%</td>'
        f'<td>{float(item.get("avg_max_gain", 0) or 0):.2f}%</td>'
        f'<td>{float(item.get("avg_max_drawdown", 0) or 0):.2f}%</td>'
        f'<td class="notes">{escape(str(item.get("interpretation") or item.get("sample_message") or ""))}</td>'
        '</tr>'
        for item in rows
    )
    return (
        '<section class="decision-center">'
        '<section class="notice">此區只驗證真突破 / 假突破 / 誘多風險等診斷，不會自動調整 A / B+ / B 條件。</section>'
        '<div class="summary">'
        f'{_metric("20日樣本", int(scorecard.get("sample_size", 0) or 0))}'
        f'{_metric("20日已驗證", int(scorecard.get("verified", 0) or 0))}'
        f'{_metric_text("樣本品質", _scorecard_quality_label(str(scorecard.get("sample_quality", "insufficient"))))}'
        f'{_metric_text("判讀狀態", "可初步觀察" if scorecard.get("is_statistically_meaningful") else "樣本不足")}'
        '</div>'
        f'<p class="muted">{escape(str(scorecard.get("message") or "樣本不足，不建議依真假突破診斷調整模型。"))}</p>'
        '<div class="table-wrap"><table><thead><tr><th>診斷</th><th>出現</th><th>已驗證</th><th>1%命中</th><th>2%命中</th><th>回撤率</th><th>平均最大漲幅</th><th>平均最大回撤</th><th>解讀</th></tr></thead><tbody>'
        f'{body}'
        '</tbody></table></div>'
        '</section>'
    )


def _scorecard_quality_label(value: str) -> str:
    return {
        "insufficient": "樣本不足",
        "early": "初步樣本",
        "meaningful": "具參考性",
        "trusted": "較可信",
    }.get(value, "樣本不足")


def _timeframe_gap_report_panel(summary: Optional[LongModelSummary]) -> str:
    report = ((summary.diagnostics or {}).get("timeframe_gap_report") if summary else None) or {}
    if not report:
        return '<section class="decision-center"><p class="muted">目前沒有時間框架診斷資料。</p></section>'
    current = report.get("current_inputs") or {}
    gaps = report.get("known_gaps") or []
    new_items = report.get("new_diagnostics") or []
    def block(title: str, items) -> str:
        rows = "".join(f"<li>{escape(str(item))}</li>" for item in (items or [])) or "<li>目前沒有資料。</li>"
        return f'<section class="advisor-panel"><h3>{escape(title)}</h3><ul>{rows}</ul></section>'
    return (
        '<section class="decision-center">'
        f'<p class="muted">{escape(str(report.get("summary") or ""))}</p>'
        '<div class="advisor-sections">'
        f'{block("盤中時間框架", current.get("intraday"))}'
        f'{block("短線時間框架", current.get("short_term"))}'
        f'{block("背景時間框架", current.get("context"))}'
        '</div>'
        '<div class="advisor-sections">'
        f'{block("目前限制", gaps)}'
        f'{block("本版新增診斷", new_items)}'
        '</div>'
        '</section>'
    )


def _ai_decision_center(summary: Optional[LongModelSummary]) -> str:
    if summary is None or not summary.decision_center:
        return (
            '<details class="decision-center selection-explainer"><summary>決策附錄</summary>'
            '<p class="muted">目前資料不足，系統僅能提供有限判斷。</p>'
            '<section class="notice">本系統僅供資料整理、策略追蹤、虛擬交易與回測，不構成投資建議，也不保證獲利。</section>'
            "</details>"
        )
    data = summary.decision_center
    counts = data.get("counts", {})
    confidence = data.get("confidence_summary", {})
    panels = [
        ("今日操作傾向", data.get("operation_tendency", "保守觀望"), data.get("summary_text", "")),
        ("進場雷達摘要", f"{int(counts.get('executable', 0))} 檔進場雷達通過", data.get("executable_summary", "")),
        ("主要等待條件", "、".join(data.get("main_waiting_conditions", [])) or "無明顯等待條件", data.get("main_waiting_summary", "")),
        ("主要風險", "、".join(data.get("major_risks", [])) or "無明顯集中風險", data.get("major_risk_summary", "")),
        ("今日建議動作", "策略追蹤與虛擬交易", data.get("action_suggestion", "")),
    ]
    panel_html = "".join(
        "<div class=\"decision-panel\">"
        f"<strong>{escape(title)}</strong>"
        f"<div>{escape(str(headline))}</div>"
        f"<p class=\"muted\">{escape(str(body))}</p>"
        "</div>"
        for title, headline, body in panels
    )
    no_trade = (
        f"<div class=\"warn\"><strong>今日不交易理由</strong><br>{escape(data.get('no_trade_reason', ''))}</div>"
        if data.get("no_trade_reason")
        else ""
    )
    confidence_text = f"高 {confidence.get('high', 0)} / 中 {confidence.get('medium', 0)} / 低 {confidence.get('low', 0)}"
    radar = (
        "<div class=\"summary\">"
        f"{_metric('A級數量', int(counts.get('grade_a', 0)))}"
        f"{_metric('B+數量', int(counts.get('grade_b_plus', 0)))}"
        f"{_metric('B級數量', int(counts.get('grade_b', 0)))}"
        f"{_metric('triggered', int(counts.get('triggered', 0)))}"
        f"{_metric('paper open positions', int(data.get('paper_stats', {}).get('paper_open_positions', 0)))}"
        f"{_metric('manual trades', int(data.get('paper_stats', {}).get('manual_trades', 0)))}"
        f"{_metric('system trades', int(data.get('paper_stats', {}).get('system_trades', 0)))}"
        f"{_metric_text('信心摘要', confidence_text)}"
        "</div>"
    )
    return (
        "<details class=\"decision-center selection-explainer\">"
        "<summary>決策附錄</summary>"
        f"{radar}"
        f"<div class=\"decision-grid\">{panel_html}</div>"
        f"{no_trade}"
        f"<section class=\"notice\">{escape(data.get('disclaimer', '本系統僅供資料整理、策略追蹤、虛擬交易與回測，不構成投資建議，也不保證獲利。'))}</section>"
        "</details>"
    )


def _signal_center(summary: Optional[LongModelSummary], report_time: Optional[datetime] = None) -> str:
    if summary is None or not summary.candidates:
        return (
            "<section class=\"decision-center\"><h2>訊號中心</h2>"
            "<p class=\"muted\">目前沒有符合條件的候選股。</p></section>"
        )
    buckets = {"強烈買多": [], "買多": [], "觀察": [], "看空": []}
    market_mode = _dashboard_market_mode(summary, report_time) if report_time else None
    front_context = _front_context(summary, market_mode)
    for item in summary.candidates:
        view = front_trade_view(item, **front_context)
        buckets.setdefault(view.category, []).append((item, view))
    columns = [
        ("強烈買多", "強烈買多"),
        ("買多", "買多"),
        ("觀察", "觀察"),
        ("看空", "看空"),
    ]
    html = []
    for key, title in columns:
        items = buckets.get(key, [])
        cards = "".join(_front_signal_card(item, view, front_context) for item, view in items)
        empty = _front_signal_center_empty_message(key)
        html.append(
            "<div class=\"signal-column\">"
            f"<h3>{escape(title)}（{len(items)}）</h3>"
            f"{_front_signal_center_column_note(key)}"
            f"{cards or empty}"
            "</div>"
        )
    return (
        "<section class=\"decision-center\">"
        "<h2>訊號中心</h2>"
        "<div class=\"signal-grid\">"
        + "".join(html)
        + "</div></section>"
    )


def _front_signal_card(item: LongCandidate, view, front_context: Optional[dict] = None) -> str:
    data_badge = _candidate_price_status_badge(item, front_context)
    background = f"{_institutional_badge(item)}｜{_sector_context_badge(item)}"
    radar = _dashboard_entry_radar(item, front_context)
    decision = front_decision_card(item, front_view=view, entry_radar=radar, **(front_context or {}))
    trap = _dashboard_breakout_trap(item, front_context)
    metrics = (
        f"現價 {_fmt(item.last_price)}｜VWAP {_fmt(item.vwap)}｜量比 {_fmt(item.volume_ratio)}x｜"
        f"停損 {_fmt(item.stop_loss)}｜停利 {_fmt(item.target_price)}｜{data_badge}"
    )
    return (
        "<div class=\"signal-card\">"
        f"<div class=\"signal-title\"><a href=\"{_advisor_link(item.symbol)}\">{escape(item.symbol)}｜{escape(item.name)}</a>{_position_size_tag(item.trigger_price or item.last_price, item.stop_loss)}</div>"
        f"<div class=\"signal-meta\"><strong>{escape(decision.final_decision)}</strong>｜{escape(decision.entry_state)}｜{escape(decision.observation_type)}</div>"
        f"<div class=\"signal-meta\">{escape(metrics)}</div>"
        f"<div class=\"signal-meta\">背景：{background}</div>"
        f"<div class=\"signal-meta\">精準分數：{decision.precision_score:.0f} / 100</div>"
        f"<div class=\"signal-meta\">最大原因 / 最大卡關：{escape(decision.top_reason)}</div>"
        f"<div class=\"signal-meta\">真假突破：{escape(trap.status_label)}｜{escape(trap.summary)}</div>"
        f"<div class=\"signal-next\">下一步：{escape(decision.next_trigger)}</div>"
        f"<div class=\"signal-next\">失效：{escape(decision.invalid_condition)}</div>"
        "</div>"
    )


def _dashboard_entry_radar(item: LongCandidate, front_context: Optional[dict] = None):
    context = front_context or {}
    market_mode = str(context.get("market_mode") or "intraday")
    intraday = bool(context.get("intraday", market_mode == "intraday"))
    uses_last_known = bool(context.get("uses_last_known"))
    is_delayed = bool(context.get("is_delayed"))
    price_status = str(context.get("price_status_label") or "")
    data_health = {
        "is_live": bool(not uses_last_known and not is_delayed and market_mode == "intraday"),
        "can_use_for_intraday_signal": bool(not uses_last_known and not is_delayed and market_mode == "intraday"),
        "uses_last_known": uses_last_known,
        "uses_cache": uses_last_known,
        "is_delayed": is_delayed,
        "price_status": price_status,
        "is_data_missing": item.vwap is None or item.volume_ratio is None,
    }
    return build_entry_radar_summary(
        candidate=item,
        data_health=data_health,
        entry_confirmation={},
        safety={},
        market_mode=market_mode,
        intraday=intraday,
    )


def _dashboard_breakout_trap(item: LongCandidate, front_context: Optional[dict] = None):
    context = front_context or {}
    market_mode = str(context.get("market_mode") or "intraday")
    intraday = bool(context.get("intraday", market_mode == "intraday"))
    uses_last_known = bool(context.get("uses_last_known"))
    is_delayed = bool(context.get("is_delayed"))
    data_health = {
        "is_live": bool(not uses_last_known and not is_delayed and market_mode == "intraday"),
        "can_use_for_intraday_signal": bool(not uses_last_known and not is_delayed and market_mode == "intraday"),
        "uses_last_known": uses_last_known,
        "uses_cache": uses_last_known,
        "is_delayed": is_delayed,
    }
    return build_breakout_trap_diagnosis(
        candidate=item,
        intraday_bars=[],
        entry_confirmation={},
        data_health=data_health,
        market_mode=market_mode,
        intraday=intraday,
    )


def _candidate_price_status_badge(item: LongCandidate, front_context: Optional[dict] = None) -> str:
    if item.vwap is None or item.volume_ratio is None:
        return "資料不足"
    context = front_context or {}
    if context.get("uses_last_known"):
        return "使用上一筆，不作為即時判斷"
    if context.get("is_delayed"):
        return f"資料延遲：{context.get('price_status_label') or '延遲'}"
    label = str(context.get("price_status_label") or "")
    return label or "即時"


def _institutional_badge(item: LongCandidate) -> str:
    context = getattr(item, "institutional_context", {}) or {}
    return escape(str(context.get("institutional_label") or "籌碼資料不足"))


def _sector_context_badge(item: LongCandidate) -> str:
    context = getattr(item, "sector_context", {}) or {}
    return escape(str(context.get("sector_status_label") or "暫無族群資料"))


def _signal_card(item: dict) -> str:
    name = f"{item.get('symbol', '')}｜{item.get('name_zh', '')}"
    if item.get("name_en"):
        name += f"｜{item.get('name_en')}"
    symbol = str(item.get("symbol") or "")
    meta = (
        f"{item.get('grade', '-')}｜{item.get('entry_status', '-')}｜{item.get('lifecycle_status', '-')}"
        f"｜Readiness {item.get('trigger_readiness', '-')}"
    )
    metrics = (
        f"現價 {_fmt(item.get('current_price'))}｜VWAP {_fmt(item.get('vwap'))}｜"
        f"量比 {_fmt(item.get('volume_ratio'))}x｜停損 {_fmt(item.get('stop_loss'))}｜停利 {_fmt(item.get('target_price'))}"
    )
    return (
        "<div class=\"signal-card\">"
        f"<div class=\"signal-title\"><a href=\"{_advisor_link(symbol)}\">{escape(name)}</a>{_position_size_tag(item.get('trigger_price') or item.get('current_price'), item.get('stop_loss'))}</div>"
        f"<div class=\"signal-meta\">當下狀態：{_trade_bias_badge(str(item.get('trade_bias', 'watch')), str(item.get('trade_bias_label', '觀察')), str(item.get('entry_status', '')))}</div>"
        f"<div class=\"signal-meta\">{escape(meta)}</div>"
        f"<div class=\"signal-meta\">{escape(metrics)}</div>"
        f"<div class=\"signal-meta\">信心：{escape(str(item.get('confidence_level', '-')))}</div>"
        f"<div class=\"signal-meta\">{escape(str(item.get('reason', '')))}</div>"
        f"<div class=\"signal-next\">下一步：{escape(str(item.get('next_step', '-')))}</div>"
        "</div>"
    )


def _signal_center_empty_message(key: str) -> str:
    if key == "executable":
        return '<p class="muted">今日沒有強烈買多標的。</p>'
    if key == "practice_long":
        return '<p class="muted">目前沒有練習買多標的。</p>'
    return '<p class="muted">目前沒有標的。</p>'


def _signal_center_column_note(key: str) -> str:
    if key == "practice_long":
        return '<p class="muted">僅供虛擬交易與樣本累積，不是正式可執行訊號。</p>'
    return ""


def _front_signal_center_empty_message(key: str) -> str:
    if key == "強烈買多":
        return '<p class="muted">今日沒有強烈買多標的。</p>'
    if key == "買多":
        return '<p class="muted">目前沒有買多觀察標的。</p>'
    if key == "看空":
        return '<p class="muted">目前沒有看空觀察標的。</p>'
    return '<p class="muted">目前沒有觀察標的。</p>'


def _front_category_diagnostics(views: list) -> str:
    if not views:
        return ""
    counts = Counter(str(getattr(view, "category", "") or "") for view in views)
    reason_counter: Counter[str] = Counter()
    for view in views:
        category = str(getattr(view, "category", "") or "")
        codes = list(getattr(view, "reason_codes", []) or [])
        if not codes:
            reason = str(getattr(view, "reason", "") or getattr(view, "subtitle", "") or "")
            if reason:
                codes = [reason]
        for code in codes[:2]:
            reason_counter[f"{category}：{_front_reason_label(str(code))}"] += 1
    top_reasons = reason_counter.most_common(4)
    reason_text = "、".join(f"{label} {count} 檔" for label, count in top_reasons) if top_reasons else "目前沒有明顯集中原因"
    triage_hint = _front_category_triage_hint(counts, reason_counter)
    return (
        '<div class="notice">'
        '<strong>四分類原因診斷</strong><br>'
        f'強烈買多 {int(counts.get("強烈買多", 0))} 檔、買多 {int(counts.get("買多", 0))} 檔、'
        f'觀察 {int(counts.get("觀察", 0))} 檔、看空 {int(counts.get("看空", 0))} 檔。'
        f'主要原因：{escape(reason_text)}。{triage_hint}'
        '</div>'
    )


def _front_category_triage_hint(counts: Counter[str], reason_counter: Counter[str]) -> str:
    strong = int(counts.get("強烈買多", 0) or 0)
    buy = int(counts.get("買多", 0) or 0)
    watch = int(counts.get("觀察", 0) or 0)
    bearish = int(counts.get("看空", 0) or 0)
    total = max(strong + buy + watch + bearish, 1)
    if strong or buy:
        return ""
    reason_text = " ".join(reason_counter.keys())
    checks = []
    if "資料" in reason_text or "使用上一筆" in reason_text or "非盤中" in reason_text:
        checks.append("先看資料狀態是否 live、資料日是否正確")
    if "VWAP" in reason_text:
        checks.append("再看是否多數股票未站上 VWAP")
    if "量比" in reason_text:
        checks.append("接著看量比是否不足")
    if "追價風險" in reason_text:
        checks.append("最後看是否被 high_risk 擋下")
    if not checks:
        checks = ["先看 market_mode、price_status、VWAP、量比與資料日"]
    if bearish and bearish / total >= 0.7:
        prefix = "分類異常警示：看空比例偏高，這不是做空建議；通常要先查資料模式、VWAP 與價格狀態，"
    elif bearish:
        prefix = "若看空異常偏多，這不是做空建議；先確認資料與價格結構，"
    elif watch:
        prefix = "目前沒有買多候選，"
    else:
        prefix = "目前沒有可分類標的，"
    return f'<br><span class="muted">{escape(prefix + "排查順序：" + " → ".join(checks) + "。")}</span>'


def _front_reason_label(code: str) -> str:
    mapping = {
        "missing_vwap": "缺 VWAP",
        "missing_volume_ratio": "缺量比",
        "cached": "使用上一筆",
        "delayed": "資料延遲",
        "stale_data": "資料過期",
        "data_missing": "資料不足",
        "not_intraday_mode": "非盤中模式",
        "high_risk": "追價風險高",
        "below_vwap": "未站上 VWAP",
        "wait_vwap": "等待 VWAP",
        "wait_volume": "量比不足",
        "wait_breakout": "等待突破",
        "wait_pullback": "等待拉回",
        "practice_long": "練習觀察",
        "avoid": "多方失效 / 避開",
        "failed_breakout": "假突破",
        "lower_low": "低點下彎",
        "bearish": "偏空結構",
        "short": "偏空結構",
        "strong_buy": "多方完整",
        "executable": "進場條件較完整",
        "buy_watch": "買多觀察",
    }
    return mapping.get(code, code)


def _front_signal_center_column_note(key: str) -> str:
    if key == "強烈買多":
        return '<p class="muted">多方條件完整，進入重點盯盤；仍需進場雷達與風控確認。</p>'
    if key == "買多":
        return '<p class="muted">方向偏多，但仍需量能、突破或進場雷達確認。</p>'
    if key == "觀察":
        return '<p class="muted">包含 high_risk、avoid、資料不足與風險觀察。</p>'
    return ""


def _trend_continuation_panel(summary: Optional[LongModelSummary], report_time: datetime) -> str:
    if summary is None:
        return (
            '<section class="decision-center">'
            '<h2>趨勢延續觀察</h2>'
            '<p class="muted">目前沒有候選股資料可做趨勢延續診斷。</p>'
            '</section>'
        )
    mode = _dashboard_market_mode(summary, report_time)
    title_prefix = "上一交易日" if mode.get("mode") == "closed_review" else "盤中"
    trend_items = [item for item in summary.candidates if getattr(item, "trend_status", "") == "trend_continuation_watch"]
    chase_items = [item for item in summary.candidates if getattr(item, "trend_status", "") == "high_risk_chase"]
    insufficient = [item for item in summary.candidates if getattr(item, "trend_status", "") == "insufficient_curve_data"]
    rows = []
    for item in (trend_items + chase_items)[:12]:
        diagnosis = getattr(item, "trend_diagnosis", {}) or {}
        intraday = ((getattr(item, "timeframe_diagnostics", {}) or {}).get("intraday_window") or {})
        rows.append(
            '<tr>'
            f'<td><strong><a href="{_advisor_link(item.symbol)}">{escape(item.symbol)}｜{escape(item.name)}</a></strong><br><span class="muted">{escape(item.sector)}</span></td>'
            f'<td>{escape(str(diagnosis.get("label") or item.trend_label or "-"))}<br><span class="muted">{escape(item.grade)}｜{escape(_entry_status_label(item.entry_status))}</span></td>'
            f'<td>{_fmt(item.last_price)}<br><span class="muted">VWAP {_fmt(item.vwap)}</span></td>'
            f'<td>{_fmt(item.volume_ratio)}x</td>'
            f'<td>{escape("是" if intraday.get("higher_high") else "否")} / {escape("是" if intraday.get("higher_low") else "否")}</td>'
            f'<td>{_fmt(intraday.get("vwap_above_minutes"))} 分</td>'
            f'<td>{_fmt(intraday.get("pullback_depth_pct"))}%</td>'
            f'<td>{escape("延續" if intraday.get("volume_continuation") else "未確認")}<br><span class="muted">{escape("退潮" if intraday.get("volume_decay") else "")}</span></td>'
            f'<td>{escape(str(diagnosis.get("summary") or "-"))}</td>'
            f'<td>{escape(str(diagnosis.get("next_step") or "-"))}</td>'
            '</tr>'
        )
    report = ((summary.diagnostics or {}).get("trend_continuation_report") or {})
    sample_note = _trend_validation_note(summary)
    return (
        '<section class="decision-center">'
        f'<h2>{escape(title_prefix)}趨勢延續觀察</h2>'
        '<section class="notice">此區只做「趨勢延續型做多診斷」，不會放寬 A / B+ / B，也不會把 high_risk 直接升級為推薦。</section>'
        '<div class="summary">'
        f'{_metric("趨勢延續觀察", len(trend_items))}'
        f'{_metric("追價風險型 high_risk", len(chase_items))}'
        f'{_metric("曲線資料不足", len(insufficient))}'
        f'{_metric_text("驗證狀態", sample_note)}'
        '</div>'
        f'<p class="muted">{escape(str(report.get("message") or ""))}</p>'
        '<div class="table-wrap"><table><thead><tr>'
        '<th>股票</th><th>診斷</th><th>價格 / VWAP</th><th>量比</th><th>高 / 低墊高</th>'
        '<th>VWAP上方</th><th>回檔深度</th><th>量能</th><th>摘要</th><th>下一步</th>'
        '</tr></thead><tbody>'
        + ("".join(rows) if rows else '<tr><td colspan="10">目前沒有可顯示的趨勢延續診斷標的。</td></tr>')
        + '</tbody></table></div>'
        '</section>'
    )


def _trend_validation_note(summary: Optional[LongModelSummary]) -> str:
    scorecard = ((summary.diagnostics or {}).get("strategy_scorecard") or {}).get("windows", {}).get("20", {}) if summary else {}
    trend = scorecard.get("trend_continuation") or {}
    return str(trend.get("message") or "趨勢延續樣本不足，不建議調整模型。")


def _position_command_center(summary: Optional[LongModelSummary]) -> str:
    data = ((summary.diagnostics or {}).get("position_command_center") if summary else None) or {}
    positions = data.get("positions") or []
    overview = data.get("summary") or {}
    if not positions:
        return (
            '<section class="decision-center">'
            '<h2>我的持倉作戰區</h2>'
            '<p class="muted">目前沒有台股虛擬持倉。建立虛擬交易後，這裡會顯示續抱、可加碼、減碼、停利或停損建議。</p>'
            '</section>'
        )
    invested_amount = float(overview.get("invested_amount", 0) or 0)
    unrealized_pnl = float(overview.get("unrealized_pnl", 0) or 0)
    unrealized_pnl_pct = float(overview.get("unrealized_pnl_pct", 0) or 0)
    if_all_stop_loss = float(overview.get("if_all_stop_loss", 0) or 0)
    if_all_take_profit = float(overview.get("if_all_take_profit", 0) or 0)
    metrics = (
        '<div class="summary">'
        f'{_metric("持倉檔數", int(overview.get("positions_count", 0) or 0))}'
        f'{_metric_text("投入本金", _money(invested_amount))}'
        f'{_metric_text("未實現損益", f"{unrealized_pnl:+.2f}")}'
        f'{_metric_text("今日損益率", f"{unrealized_pnl_pct:+.2f}%")}'
        f'{_metric_text("全數停損情境", f"{if_all_stop_loss:+.2f}")}'
        f'{_metric_text("全數停利情境", f"{if_all_take_profit:+.2f}")}'
        f'{_metric_text("可否再加碼", "可" if overview.get("can_add_any") else "不可")}'
        f'{_metric_text("總風險", "偏高" if overview.get("total_risk_high") else "可控")}'
        f'{_metric_text("同族群風險", "集中" if overview.get("sector_concentration_high") else "未集中")}'
        '</div>'
    )
    rows = []
    for item in positions:
        forbidden = item.get("add_forbidden_reasons") or []
        rows.append(
            '<tr>'
            f'<td><strong><a href="{_advisor_link(str(item.get("symbol", "")))}">{escape(str(item.get("symbol", "")))}｜{escape(str(item.get("name_zh", "")))}</a></strong><br><span class="muted">{escape(str(item.get("reason_code", "")))}</span></td>'
            f'<td><strong>{escape(str(item.get("action", "-")))}</strong></td>'
            f'<td>{_fmt(item.get("cost_price"))}</td>'
            f'<td>{_fmt(item.get("current_price"))}</td>'
            f'<td>{_fmt(item.get("quantity"))}</td>'
            f'<td>{float(item.get("unrealized_pnl", 0) or 0):+.2f}<br><span class="muted">{float(item.get("unrealized_pnl_pct", 0) or 0):+.2f}%</span></td>'
            f'<td>{_fmt(item.get("vwap"))}</td>'
            f'<td>{_fmt(item.get("volume_ratio"))}x</td>'
            f'<td>{_fmt(item.get("stop_loss"))}</td>'
            f'<td>{_fmt(item.get("target_price"))}</td>'
            f'<td class="notes">{escape(str(item.get("institutional_label", "籌碼資料不足")))}<br><span class="muted">{escape(str(item.get("institutional_reason", "")))}</span></td>'
            f'<td class="notes">{escape(str(item.get("sector_status_label", "暫無族群資料")))}<br><span class="muted">{escape(str(item.get("sector_reason", "")))}</span></td>'
            f'<td class="notes">{escape(str(item.get("next_step", "-")))}</td>'
            f'<td class="notes">{escape(str(item.get("invalidation", "-")))}</td>'
            f'<td class="notes">{escape("；".join(forbidden) if forbidden else "允許加碼，但仍需控制部位。")}</td>'
            '</tr>'
        )
    table = (
        '<div class="table-wrap"><table class="sortable"><thead><tr>'
        '<th data-sort="text">股票</th><th data-sort="text">持倉動作</th><th data-sort="number">成本</th>'
        '<th data-sort="number">現價</th><th data-sort="number">數量</th><th data-sort="number">未實現損益</th>'
        '<th data-sort="number">VWAP</th><th data-sort="number">量比</th><th data-sort="number">停損</th>'
        '<th data-sort="number">停利</th><th>籌碼背景</th><th>族群狀態</th><th>下一步</th><th>失效條件</th><th>不可加碼原因</th>'
        '</tr></thead><tbody>'
        + ''.join(rows)
        + '</tbody></table></div>'
    )
    return (
        '<section class="decision-center">'
        '<h2>我的持倉作戰區</h2>'
        f'{metrics}'
        f'<section class="notice">{escape(str(overview.get("total_risk_message", "")))}</section>'
        f'{table}'
        '</section>'
    )


def _review_mode_sections(summary: Optional[LongModelSummary], report_time: datetime) -> str:
    mode = _dashboard_market_mode(summary, report_time)
    diagnostics = summary.diagnostics if summary else {}
    health = (diagnostics or {}).get("data_health") or {}
    missed = (diagnostics or {}).get("missed_rate_report") or (diagnostics or {}).get("missed_stock_analysis") or {}
    verification = (diagnostics or {}).get("post_market_verification") or {}
    scorecard = ((diagnostics or {}).get("strategy_scorecard") or {}).get("windows", {}).get("20", {})
    confidence = _mode_aware_data_confidence(health, mode)
    front = front_trade_counts(
        list(summary.candidates) if summary else [],
        data_today=bool(mode.get("is_data_current_for_mode")),
        intraday=False,
        stale=mode.get("mode") == "stale_data",
        allow_strong_long=False,
        market_mode=str(mode.get("mode")),
    )["counts"]
    verification_text = f"{int(verification.get('verified', 0) or 0)}/{int(verification.get('rows', 0) or 0)}"
    true_missed_rate = float(missed.get("missed_by_pool_rate", missed.get("missed_rate", 0)) or 0)
    overview = (
        '<section class="decision-center">'
        '<h2>上一交易日復盤</h2>'
        f'<p class="muted">{escape(str(mode.get("review_mode_message", "")))}</p>'
        '<div class="summary">'
        f'{_metric_text("資料日期", str(mode.get("data_date", "-")))}'
        f'{_metric("上一交易日強烈買多", int(front.get("強烈買多", 0)))}'
        f'{_metric("上一交易日買多", int(front.get("買多", 0)))}'
        f'{_metric("上一交易日觀察", int(front.get("觀察", 0)))}'
        f'{_metric("上一交易日看空", int(front.get("看空", 0)))}'
        f'{_metric_text("資料可信度", confidence)}'
        f'{_metric("真漏抓", int(missed.get("missed_by_pool_count", missed.get("missed_count", 0)) or 0))}'
        f'{_metric("已看到但未推薦", int(missed.get("seen_but_filtered_count", 0) or 0))}'
        f'{_metric("盤後可惜漏掉", int((missed.get("regret_after_close") or {}).get("count", 0) or 0))}'
        f'{_metric_text("盤後驗證", verification_text)}'
        '</div>'
        '</section>'
    )
    watch_rows = []
    for item in _tomorrow_pool_items(((summary.momentum_scan or {}).get("items", []) if summary else []), limit=10):
        status, next_step = _tomorrow_pool_status(item)
        watch_rows.append(
            '<tr>'
            f'<td><strong><a href="{_advisor_link(str(item.get("symbol", "")))}">{escape(str(item.get("symbol", "")))}｜{escape(str(item.get("name", "")))}</a></strong></td>'
            f'<td>{escape(status)}</td>'
            f'<td class="notes">{escape(str(item.get("not_selected_reason") or item.get("trade_bias_reason") or "-"))}</td>'
            f'<td class="notes">{escape(next_step)}</td>'
            f'<td class="notes">跌破 VWAP、量能退潮、資料過期或風險分數升高。</td>'
            f'<td>{escape("完整" if not item.get("data_error") else "缺漏")}</td>'
            f'<td>{escape(str(item.get("reason_code") or "-"))}</td>'
            '</tr>'
        )
    watch_table = (
        '<section class="decision-center">'
        '<h2>下個交易日觀察清單</h2>'
        '<p class="muted">此區從上一交易日資料整理，不是推薦買進；下個交易日仍需等待 VWAP、量能與突破確認。</p>'
        '<div class="table-wrap"><table><thead><tr><th>股票</th><th>狀態</th><th>觀察原因</th><th>要等的條件</th><th>失效條件</th><th>資料</th><th>reason code</th></tr></thead><tbody>'
        + ("".join(watch_rows) if watch_rows else '<tr><td colspan="7">目前沒有下個交易日觀察清單。</td></tr>')
        + '</tbody></table></div></section>'
    )
    sample_size = int(scorecard.get("sample_size", 0) or 0)
    groups = scorecard.get("groups") or {}
    model_review = (
        '<section class="decision-center">'
        '<h2>模型檢討</h2>'
        '<div class="summary">'
        f'{_metric("20日樣本", sample_size)}'
        f'{_metric_text("強烈買多後表現", _review_group_text(groups.get("A")))}'
        f'{_metric_text("買多後表現", _review_group_text(groups.get("B+")))}'
        f'{_metric_text("high_risk續漲", _review_group_text(groups.get("high_risk"), "continue_up_rate"))}'
        f'{_metric_text("avoid後大漲", _review_group_text(groups.get("avoid"), "big_up_rate"))}'
        f'{_metric_text("真漏抓率", f"{true_missed_rate:.2f}%")}'
        '</div>'
        f'<section class="notice">{escape("樣本不足，不建議調整模型。" if sample_size < 20 else "已有初步樣本，但仍需持續驗證後再調整模型。")}</section>'
        '</section>'
    )
    return overview + watch_table + model_review


def _review_group_text(group: Optional[dict], key: str = "win_rate") -> str:
    if not group:
        return "樣本不足"
    total = int(group.get("sample_size", group.get("total", 0)) or 0)
    if total < 20:
        return f"樣本 {total}，不足"
    return f"{float(group.get(key, 0) or 0):.2f}%"


def _momentum_scan_table(summary: Optional[LongModelSummary]) -> str:
    scan = summary.momentum_scan if summary else {}
    items = scan.get("items", []) if scan else []
    summary_data = scan.get("summary", {}) if scan else {}
    if not items:
        return "<table><tbody><tr><td>目前沒有異動股掃描資料。</td></tr></tbody></table>"
    metrics = (
        "<div class=\"summary\">"
        f"{_metric('今日異動股總數', int(summary_data.get('total', 0)))}"
        f"{_metric('成功取得行情數', int(summary_data.get('data_success', 0)))}"
        f"{_metric('進入評分模型數', int(summary_data.get('model_scored', 0)))}"
        f"{_metric('A 數量', int(summary_data.get('grade_a', 0)))}"
        f"{_metric('B+ 數量', int(summary_data.get('grade_b_plus', 0)))}"
        f"{_metric('B 數量', int(summary_data.get('grade_b', 0)))}"
        f"{_metric('high_risk 數量', int(summary_data.get('high_risk', 0)))}"
        f"{_metric('被排除數量', int(summary_data.get('excluded', 0)))}"
        f"{_metric('資料失敗數量', int(summary_data.get('data_failed', 0)))}"
        "</div>"
        '<section class="notice">異動股會先送入原本 VWAP / 量比 / 突破 / 風險 / 信心模型，不會直接變成推薦；A 級條件維持嚴格。</section>'
    )
    rows = []
    for item in items:
        rows.append(
            "<tr>"
            f"<td><strong>{escape(str(item.get('symbol', '')))}｜{escape(str(item.get('name', '')))}</strong><br><span class=\"muted\">{escape(str(item.get('sector', '')))}</span></td>"
            f"<td data-sort-value=\"{_sort_value(item.get('latest_price'))}\">{_fmt(item.get('latest_price'))}</td>"
            f"{_change_cell(item.get('change_pct'))}"
            f"<td data-sort-value=\"{_sort_value(item.get('volume_ratio'))}\">{_fmt(item.get('volume_ratio'))}x</td>"
            f"<td data-sort-value=\"{_sort_value(item.get('turnover'))}\">{_money(float(item.get('turnover') or 0)) if item.get('turnover') is not None else '-'}</td>"
            f"<td>{_yes_no(bool(item.get('above_vwap')))}<br><span class=\"muted\">{_fmt(item.get('vwap'))}</span></td>"
            f"<td>{_yes_no(bool(item.get('break_prev_high')))}</td>"
            f"<td>{_yes_no(bool(item.get('break_5d_high')))}</td>"
            f"<td>{_yes_no(bool(item.get('break_20d_high')))}</td>"
            f"<td>{_trade_bias_badge(str(item.get('trade_bias', 'watch')), str(item.get('trade_bias_label', '觀察')), str(item.get('entry_status', '')))}<br><span class=\"muted\">{escape(_display_trade_bias_reason(str(item.get('entry_status', '')), str(item.get('trade_bias_reason', ''))))}</span></td>"
            f"<td class=\"notes\">{escape(str(item.get('initial_status', '-')))}<br><span class=\"muted\">{escape('；'.join(item.get('source_reasons') or []))}</span></td>"
            f"<td>{escape(str(item.get('ai_grade', '-')))}</td>"
            f"<td>{escape(_entry_status_label(str(item.get('entry_status', '-'))))}</td>"
            f"<td>{escape(str(item.get('source_scope', 'watchlist')))}</td>"
            f"<td>{escape(str(item.get('reason_code', '-')))}</td>"
            f"<td class=\"notes\">{escape(str(item.get('not_selected_reason') or item.get('data_error') or '-'))}</td>"
            "</tr>"
        )
    return (
        metrics
        + "<table class=\"sortable\"><thead><tr>"
        "<th data-sort=\"text\">股票</th><th data-sort=\"number\">最新價</th><th data-sort=\"number\">漲跌幅</th>"
        "<th data-sort=\"number\">量比</th><th data-sort=\"number\">成交金額</th><th data-sort=\"text\">站上 VWAP</th>"
        "<th data-sort=\"text\">突破昨高</th><th data-sort=\"text\">突破5日高</th><th data-sort=\"text\">突破20日高</th>"
        "<th data-sort=\"text\">當下狀態</th><th>初步狀態</th><th data-sort=\"text\">AI 評級</th><th data-sort=\"text\">entry_status</th>"
        "<th data-sort=\"text\">來源</th><th data-sort=\"text\">reason code</th><th>未入選原因</th>"
        "</tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table>"
    )


def _tomorrow_long_watch_pool(summary: Optional[LongModelSummary]) -> str:
    scan = summary.momentum_scan if summary else {}
    items = scan.get("items", []) if scan else []
    pool = _tomorrow_pool_items(items)
    if not pool:
        return (
            "<table><tbody><tr><td>目前沒有足夠資料建立明日觀察池。"
            "明天開盤後請先等待 VWAP、量比與突破條件確認。</td></tr></tbody></table>"
        )
    priority = sum(1 for item in pool if _tomorrow_pool_status(item)[0] == "明日優先觀察")
    practice = sum(1 for item in pool if _tomorrow_pool_status(item)[0] == "練習買多")
    waiting = sum(1 for item in pool if _tomorrow_pool_status(item)[0] == "盤中等待確認")
    risky = sum(1 for item in pool if _tomorrow_pool_status(item)[0] == "強勢但高風險")
    metrics = (
        "<div class=\"summary\">"
        f"{_metric('明日觀察池', len(pool))}"
        f"{_metric('明日優先觀察', priority)}"
        f"{_metric('練習買多', practice)}"
        f"{_metric('盤中等待確認', waiting)}"
        f"{_metric('強勢但高風險', risky)}"
        "</div>"
        '<section class="notice">收盤後觀察池不是明天直接買進名單；明天仍要等盤中 VWAP、量比、突破與風險條件重新確認。</section>'
    )
    rows = []
    for item in pool:
        status, next_condition = _tomorrow_pool_status(item)
        rows.append(
            "<tr>"
            f"<td><strong>{escape(str(item.get('symbol', '')))}｜{escape(str(item.get('name', '')))}</strong><br><span class=\"muted\">{escape(str(item.get('sector', '')))}</span></td>"
            f"<td>{escape(status)}</td>"
            f"<td>{_trade_bias_badge(str(item.get('trade_bias', 'watch')), str(item.get('trade_bias_label', '觀察')), str(item.get('entry_status', '')))}<br><span class=\"muted\">{escape(_display_trade_bias_reason(str(item.get('entry_status', '')), str(item.get('trade_bias_reason', ''))))}</span></td>"
            f"<td>{escape(str(item.get('ai_grade', '-')))}</td>"
            f"<td>{escape(_entry_status_label(str(item.get('entry_status', '-'))))}</td>"
            f"{_change_cell(item.get('change_pct'))}"
            f"<td data-sort-value=\"{_sort_value(item.get('turnover'))}\">{_money(float(item.get('turnover') or 0)) if item.get('turnover') is not None else '-'}</td>"
            f"<td data-sort-value=\"{_sort_value(item.get('volume_ratio'))}\">{_fmt(item.get('volume_ratio'))}x</td>"
            f"<td>{_yes_no(bool(item.get('above_vwap')))}<br><span class=\"muted\">{_fmt(item.get('vwap'))}</span></td>"
            f"<td>{_yes_no(bool(item.get('break_prev_high')))}</td>"
            f"<td>{_yes_no(bool(item.get('break_5d_high')))}</td>"
            f"<td class=\"notes\">{escape(next_condition)}</td>"
            f"<td class=\"notes\">{escape(str(item.get('not_selected_reason') or item.get('data_error') or '-'))}</td>"
            "</tr>"
        )
    return (
        metrics
        + "<table class=\"sortable\"><thead><tr>"
        "<th data-sort=\"text\">股票</th><th data-sort=\"text\">觀察層級</th><th data-sort=\"text\">當下狀態</th>"
        "<th data-sort=\"text\">AI 評級</th><th data-sort=\"text\">entry_status</th><th data-sort=\"number\">今日漲幅</th>"
        "<th data-sort=\"number\">成交金額</th><th data-sort=\"number\">量比</th><th data-sort=\"text\">站上 VWAP</th>"
        "<th data-sort=\"text\">突破昨高</th><th data-sort=\"text\">突破5日高</th><th>明天盤中確認條件</th><th>目前未直接買進原因</th>"
        "</tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table>"
    )


def _tomorrow_continuation_candidates(summary: Optional[LongModelSummary]) -> str:
    scan = summary.momentum_scan if summary else {}
    items = list(scan.get("items") or [])
    candidates = _tomorrow_continuation_items(items)
    if not candidates:
        return (
            "<table><tbody><tr><td>目前沒有符合「明日續強候選」條件的標的；"
            "可改看明日觀察池，明天開盤後再確認 VWAP、量比與突破。</td></tr></tbody></table>"
        )
    above_vwap_count = sum(1 for item in candidates if bool(item.get("above_vwap")))
    high_turnover_count = sum(1 for item in candidates if _is_high_turnover_rank(item))
    near_trigger_count = sum(1 for item in candidates if _continuation_needs(item))
    metrics = (
        '<div class="summary">'
        + _metric("續強候選", len(candidates))
        + _metric("站上 VWAP", above_vwap_count)
        + _metric("成交金額前段", high_turnover_count)
        + _metric("明天待確認", near_trigger_count)
        + "</div>"
        + '<section class="notice">明日續強候選股不是直接買進名單；這裡只代表今天動能較強、明天值得盯盤，開盤後仍需等 VWAP、量比、突破與風險重新確認。</section>'
    )
    rows = []
    for item in candidates:
        setup = _continuation_setup_text(item)
        needs = _continuation_needs(item)
        risks = _continuation_risk_text(item)
        rows.append(
            "<tr>"
            f"<td><strong>{escape(str(item.get('symbol', '')))}｜{escape(str(item.get('name', '')))}</strong><br><span class=\"muted\">{escape(str(item.get('sector', '')))}</span></td>"
            f"{_change_cell(item.get('change_pct'))}"
            f"<td data-sort-value=\"{_sort_value(item.get('turnover'))}\">{_money(float(item.get('turnover') or 0)) if item.get('turnover') is not None else '-'}</td>"
            f"<td data-sort-value=\"{_sort_value(item.get('volume_ratio'))}\">{_fmt(item.get('volume_ratio'))}x</td>"
            f"<td>{_yes_no(bool(item.get('above_vwap')))}<br><span class=\"muted\">{_fmt(item.get('vwap'))}</span></td>"
            f"<td>{_yes_no(bool(item.get('break_prev_high')))}</td>"
            f"<td>{_yes_no(bool(item.get('break_5d_high')))}</td>"
            f"<td data-sort-value=\"{_sort_value(item.get('risk_score'))}\">{_fmt(item.get('risk_score'))}</td>"
            f"<td>{escape(str(item.get('ai_grade', '-')))}</td>"
            f"<td>{escape(_entry_status_label(str(item.get('entry_status', '-'))))}</td>"
            f"<td class=\"notes\">{escape(setup)}</td>"
            f"<td class=\"notes\">{escape(needs)}</td>"
            f"<td class=\"notes\">{escape(risks)}</td>"
            "</tr>"
        )
    return (
        metrics
        + "<table class=\"sortable\"><thead><tr>"
        "<th data-sort=\"text\">股票</th><th data-sort=\"number\">今日漲幅</th><th data-sort=\"number\">成交金額</th>"
        "<th data-sort=\"number\">量比</th><th data-sort=\"text\">站上 VWAP</th><th data-sort=\"text\">突破昨高</th>"
        "<th data-sort=\"text\">突破5日高</th><th data-sort=\"number\">風險分數</th><th data-sort=\"text\">AI 評級</th>"
        "<th data-sort=\"text\">entry_status</th><th>續強理由</th><th>明天開盤確認</th><th>追蹤風險</th>"
        "</tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table>"
    )


def _tomorrow_continuation_items(items: List[dict], limit: int = 30) -> List[dict]:
    ranked_items = [
        item
        for item in items
        if not item.get("data_error")
    ]
    ranked_items.sort(key=lambda item: -(float(item.get("turnover") or 0)))
    turnover_rank = {id(item): index + 1 for index, item in enumerate(ranked_items)}
    eligible = [
        item
        for item in ranked_items
        if _is_tomorrow_continuation_candidate(item, turnover_rank.get(id(item), 9999))
    ]
    eligible.sort(key=lambda item: _tomorrow_continuation_sort_key(item, turnover_rank.get(id(item), 9999)))
    return eligible[:limit]


def _is_tomorrow_continuation_candidate(item: dict, turnover_rank: int) -> bool:
    change_pct = float(item.get("change_pct") or 0)
    volume_ratio = float(item.get("volume_ratio") or 0)
    risk_score = float(item.get("risk_score") or 0)
    turnover = float(item.get("turnover") or 0)
    entry_status = str(item.get("entry_status") or "")
    if entry_status in {"high_risk", "avoid"}:
        return False
    if change_pct < 3:
        return False
    if volume_ratio < 0.7:
        return False
    if risk_score > 65:
        return False
    if not _is_high_turnover_rank(item, turnover_rank=turnover_rank):
        return False
    if not _continuation_structure_ok(item):
        return False
    if _has_obvious_upper_shadow_risk(item):
        return False
    return turnover >= 500_000_000 or turnover_rank <= 80


def _is_high_turnover_rank(item: dict, turnover_rank: Optional[int] = None) -> bool:
    turnover = float(item.get("turnover") or 0)
    rank = turnover_rank if turnover_rank is not None else int(item.get("turnover_rank") or 9999)
    return rank <= 80 or turnover >= 1_000_000_000


def _continuation_structure_ok(item: dict) -> bool:
    if bool(item.get("above_vwap")):
        return True
    if bool(item.get("close_near_high")) or bool(item.get("near_day_high")):
        return True
    if bool(item.get("break_prev_high")) or bool(item.get("break_5d_high")):
        return True
    latest = item.get("latest_price")
    vwap = item.get("vwap")
    try:
        return float(latest) >= float(vwap) * 0.995
    except (TypeError, ValueError):
        return False


def _has_obvious_upper_shadow_risk(item: dict) -> bool:
    reason_text = "；".join(
        str(item.get(key, ""))
        for key in ("not_selected_reason", "risk_reason", "risk_reasons", "confidence_summary", "trade_bias_reason")
    )
    risk_score = float(item.get("risk_score") or 0)
    volume_ratio = float(item.get("volume_ratio") or 0)
    upper_shadow_pct = float(item.get("upper_shadow_pct") or 0)
    if upper_shadow_pct >= 1.2:
        return True
    if "長上影" in reason_text or "上影線" in reason_text or "爆量不漲" in reason_text:
        return True
    return volume_ratio >= 2.5 and risk_score >= 55


def _tomorrow_continuation_sort_key(item: dict, turnover_rank: int) -> tuple:
    return (
        turnover_rank,
        -(float(item.get("change_pct") or 0)),
        -(float(item.get("volume_ratio") or 0)),
        float(item.get("risk_score") or 0),
        str(item.get("symbol", "")),
    )


def _continuation_setup_text(item: dict) -> str:
    reasons = []
    change_pct = float(item.get("change_pct") or 0)
    volume_ratio = float(item.get("volume_ratio") or 0)
    turnover = float(item.get("turnover") or 0)
    if change_pct >= 3:
        reasons.append(f"今日漲幅 {change_pct:.2f}%")
    if turnover >= 1_000_000_000:
        reasons.append(f"成交金額 {_money(turnover)}")
    if volume_ratio >= 0.7:
        reasons.append(f"量比 {volume_ratio:.2f}x")
    if bool(item.get("above_vwap")):
        reasons.append("站上 VWAP")
    elif _continuation_structure_ok(item):
        reasons.append("接近 VWAP / 高檔結構")
    if bool(item.get("break_prev_high")):
        reasons.append("突破昨高")
    if bool(item.get("break_5d_high")):
        reasons.append("突破 5 日高")
    return "；".join(reasons) or "今日動能轉強，列入明日觀察。"


def _continuation_needs(item: dict) -> str:
    needs = []
    if not bool(item.get("above_vwap")):
        needs.append("開盤後站回 VWAP")
    if float(item.get("volume_ratio") or 0) < 1.0:
        needs.append("量比放大到 1.0x 附近")
    if not (bool(item.get("break_prev_high")) or bool(item.get("break_5d_high"))):
        needs.append("突破昨高 / 5 日高或開盤區間高點")
    needs.append("停損距離可控")
    return "；".join(needs)


def _continuation_risk_text(item: dict) -> str:
    risks = []
    risk_score = float(item.get("risk_score") or 0)
    if risk_score >= 55:
        risks.append(f"風險分數 {risk_score:.0f}，不宜追高")
    if float(item.get("volume_ratio") or 0) < 1.0:
        risks.append("量比尚未完全確認")
    entry_status = str(item.get("entry_status", ""))
    if entry_status in {"wait_vwap", "wait_volume", "wait_breakout", "wait_pullback"}:
        risks.append(_entry_status_label(entry_status))
    return "；".join(risks) or "目前未見明顯追價風險，仍需明天盤中確認。"


def _tomorrow_pool_items(items: List[dict], limit: int = 50) -> List[dict]:
    eligible = [
        item
        for item in items
        if not item.get("data_error")
        and (
            item.get("ai_grade") in {"A", "B+", "B"}
            or item.get("entry_status") in {"high_risk", "wait_volume", "wait_vwap", "wait_breakout", "wait_pullback"}
            or bool(item.get("source_reasons"))
            or float(item.get("turnover") or 0) >= 500_000_000
        )
    ]
    eligible.sort(key=_tomorrow_pool_sort_key)
    return eligible[:limit]


def _tomorrow_pool_sort_key(item: dict) -> tuple:
    status_order = {
        "明日優先觀察": 0,
        "練習買多": 1,
        "盤中等待確認": 2,
        "強勢但高風險": 3,
        "明日觀察": 4,
        "暫不追價": 5,
    }
    grade_order = {"A": 0, "B+": 1, "B": 2, "C": 3, "D": 4, "-": 5}
    status, _ = _tomorrow_pool_status(item)
    return (
        status_order.get(status, 6),
        grade_order.get(str(item.get("ai_grade", "-")), 5),
        -(float(item.get("turnover") or 0)),
        -(float(item.get("change_pct") or -999)),
        str(item.get("symbol", "")),
    )


def _tomorrow_pool_status(item: dict) -> tuple[str, str]:
    entry_status = str(item.get("entry_status", ""))
    grade = str(item.get("ai_grade", "-"))
    trade_bias = str(item.get("trade_bias", "watch"))
    above_vwap = bool(item.get("above_vwap"))
    volume_ratio = float(item.get("volume_ratio") or 0)
    break_prev_high = bool(item.get("break_prev_high"))
    break_5d_high = bool(item.get("break_5d_high"))
    high_risk = entry_status == "high_risk" or "風險高" in str(item.get("not_selected_reason", ""))
    if trade_bias == "long" and entry_status == "executable":
        return "明日優先觀察", "明天開盤後若仍站上 VWAP、量比維持 1.0x 以上且未追價過熱，可列入盤中重點盯盤。"
    if entry_status == "practice_long" or grade == "B+":
        return "練習買多", "明天開盤後若站穩 VWAP，量比放大到 0.8x～1.0x 以上，可用虛擬交易練習追蹤。"
    if entry_status == "wait_volume":
        return "盤中等待確認", "等待量比放大；量比低於 0.8x 不直接買多。"
    if entry_status == "wait_vwap":
        return "盤中等待確認", "等待股價重新站回 VWAP，且站回後不要立刻跌破。"
    if entry_status == "wait_breakout":
        return "盤中等待確認", "等待突破昨高、5日高或開盤區間高點後再評估。"
    if entry_status == "wait_pullback":
        return "盤中等待確認", "等待拉回 VWAP 附近不破，避免直接追高。"
    if high_risk:
        return "強勢但高風險", "強勢但追價風險偏高，明天以回測 VWAP 不破或風險降溫後再觀察。"
    if above_vwap and volume_ratio >= 0.8 and (break_prev_high or break_5d_high):
        return "明日觀察", "條件接近，明天需確認量能與突破延續。"
    return "暫不追價", "先放在觀察池，不列為明天直接買多；等待 VWAP、量比與突破重新成立。"


def _manual_scan_panel() -> str:
    return """
    <section class="scan-form" aria-label="手動補追蹤">
      <label>手動補追蹤 / 立即掃描
        <input id="tw-scan-symbol" data-stock-search placeholder="例如 力積電、6770、3016">
      </label>
      <button type="button" id="tw-scan-button">立即掃描</button>
      <button type="button" id="tw-add-watch-button">加入今日追蹤</button>
      <span class="muted">輸入股票名稱或代號後，系統會抓 Yahoo Finance 並跑同一套模型。</span>
    </section>
    <div id="tw-scan-result" class="scan-result"></div>
    """


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


def _data_health_panel(summary: Optional[LongModelSummary]) -> str:
    health = ((summary.diagnostics or {}).get("data_health") if summary else {}) or {}
    if not health:
        return '<div class="notice">目前沒有資料健康度診斷。</div>'
    status = str(health.get("status") or "未知")
    css = "health-ok" if status == "正常" else "health-bad" if status in {"異常", "過期"} else "health-warn"
    sources = "".join(f"<li>{escape(str(item))}</li>" for item in health.get("data_sources", [])[:5])
    failed = health.get("failed_symbols") or []
    failed_text = "、".join(str(item) for item in failed[:12]) if failed else "無"
    return (
        f'<section class="data-status"><strong>今日資料狀態：<span class="{css}">{escape(status)}</span></strong>'
        f'<p>{escape(str(health.get("recommendation_state", "")))}</p>'
        '<div class="summary">'
        f'{_metric("股票池數量", int(health.get("stock_pool_count", 0)))}'
        f'{_metric("日線成功", int(health.get("daily_success_count", 0)))}'
        f'{_metric("日線失敗", int(health.get("daily_failed_count", 0)))}'
        f'{_metric("盤中成功", int(health.get("intraday_success_count", 0)))}'
        f'{_metric("盤中失敗", int(health.get("intraday_failed_count", 0)))}'
        f'{_metric_text("最後盤中資料", str(health.get("latest_intraday_at") or "-"))}'
        f'{_metric_text("行情狀態", str(health.get("live_state_label") or health.get("live_state") or "-"))}'
        f'{_metric("即時 live", int(health.get("live_count", 0) or 0))}'
        f'{_metric("延遲 delayed", int(health.get("delayed_count", 0) or 0))}'
        f'{_metric("使用上一筆 cached", int(health.get("cached_count", 0) or 0))}'
        f'{_metric("資料不足 missing", int(health.get("missing_count", 0) or 0))}'
        f'{_metric("Yahoo無資料/無效代號", int(health.get("symbol_not_found_count", 0) or 0))}'
        f'{_metric("Yahoo代理失敗", int(health.get("yahoo_proxy_unavailable_count", 0) or 0))}'
        f'{_metric_text("是否即時", "是" if health.get("is_live") else "否")}'
        f'{_metric_text("是否延遲", "是" if health.get("is_delayed") else "否")}'
        f'{_metric_text("使用上一筆", "是" if health.get("uses_last_known") else "否")}'
        f'{_metric_text("資料日期", str(health.get("data_date") or "-"))}'
        f'{_metric_text("資料年齡", _age_text(health.get("age_minutes")))}'
        f'{_metric_text("交易時間", "是" if health.get("is_intraday_session") else "否")}'
        f'{_metric_text("是否今天資料", "是" if health.get("is_today_data") else "否")}'
        f'{_metric_text("是否過期", "是" if health.get("is_stale") else "否")}'
        '</div>'
        f'<p class="muted">失敗標的：{escape(failed_text)}</p>'
        f'<p class="muted">{escape(str(health.get("unavailable_symbols_message", "")))}</p>'
        f'<p class="muted">{escape(str(health.get("uses_realtime_or_delayed", "")))}</p>'
        f'<p class="muted">{escape(str(health.get("last_known_price_policy", "")))}</p>'
        f'<ul>{sources}</ul>'
        '</section>'
    )


def _full_market_scan_panel(summary: Optional[LongModelSummary]) -> str:
    scan = ((summary.diagnostics or {}).get("full_market_scan") if summary else {}) or {}
    if not scan:
        return '<div class="notice">目前沒有全市場掃描資料。</div>'
    data = scan.get("summary") or {}
    source = scan.get("source_status") or {}
    by_status = scan.get("by_status") or {}
    out_symbols = scan.get("out_of_pool_symbols") or []
    twse_count = int(data.get("twse_count", 0) or 0)
    tpex_count = int(data.get("tpex_count", 0) or 0)
    twse_text = f"TWSE 上市掃描：{'成功' if source.get('twse_ok') else '失敗'}，普通股池 {twse_count} 檔"
    if not source.get("twse_ok") and source.get("twse_used_cache"):
        twse_text = f"TWSE 上市掃描：抓取失敗，使用 cache，普通股池 {twse_count} 檔"
    if source.get("tpex_ok"):
        tpex_text = f"TPEX 上櫃掃描：成功，普通股池 {tpex_count} 檔"
    elif source.get("tpex_used_cache"):
        tpex_text = f"TPEX 上櫃掃描：抓取失敗，使用 cache，普通股池 {tpex_count} 檔"
    else:
        tpex_text = f"TPEX 上櫃掃描：尚未納入或抓取失敗，普通股池 {tpex_count} 檔"
    scope_text = "上市 + 上櫃" if tpex_count > 0 else "上市，不含上櫃"
    scope_warning = (
        "部分上櫃強勢股仍可能漏抓。"
        if tpex_count <= 0
        else ("上櫃資料使用 cache，可能延遲。" if source.get("tpex_used_cache") and not source.get("tpex_ok") else "")
    )
    retry_text = f"retry 次數：{int(source.get('retry_count', 0))}"
    out_text = "、".join(str(item) for item in out_symbols[:12]) if out_symbols else "目前沒有 watchlist 外新候選，或資料尚未成功。"
    return (
        '<section class="data-status">'
        '<strong>全市場掃描先找異動股，再送入既有 A / B+ / B 模型；不會直接放寬推薦標準。</strong>'
        f'<p class="muted">{escape(twse_text)}<br>{escape(tpex_text)}<br>目前掃描範圍：{escape(scope_text)}{("；" + escape(scope_warning)) if scope_warning else ""}<br>{escape(retry_text)}</p>'
        '<div class="summary">'
        f'{_metric("完整普通股池", int(data.get("pool_symbols", 0)))}'
        f'{_metric("TWSE 上市池", twse_count)}'
        f'{_metric("TPEX 上櫃池", tpex_count)}'
        f'{_metric("今日異動候選池", int(data.get("candidate_symbols", 0)))}'
        f'{_metric("排除 ETF", int(data.get("excluded_etf", 0)))}'
        f'{_metric("排除權證", int(data.get("excluded_warrant", 0)))}'
        f'{_metric("排除特別股/非普通股", int(data.get("excluded_preferred", 0)))}'
        f'{_metric("排除低流動性", int(data.get("excluded_low_liquidity", 0)))}'
        f'{_metric("out_of_pool 新找到", int(by_status.get("out_of_pool", 0)))}'
        f'{_metric("A", int(by_status.get("a", 0)))}'
        f'{_metric("B+", int(by_status.get("b_plus", 0)))}'
        f'{_metric("B", int(by_status.get("b", 0)))}'
        f'{_metric("high_risk", int(by_status.get("high_risk", 0)))}'
        f'{_metric("avoid", int(by_status.get("avoid", 0)))}'
        f'{_metric("data_missing", int(by_status.get("data_missing", 0)))}'
        '</div>'
        f'<p class="muted">watchlist 外新候選：{escape(out_text)}</p>'
        '</section>'
    )


def _missed_stock_diagnostic_table(summary: Optional[LongModelSummary]) -> str:
    diagnostic = ((summary.diagnostics or {}).get("missed_stock_analysis") if summary else {}) or {}
    rows = diagnostic.get("rows") or []
    if not diagnostic:
        return '<div class="notice">目前沒有漏抓股票診斷資料。</div>'
    intro = (
        '<section class="notice">'
        f'{escape(str(diagnostic.get("definition", "")))}<br>'
        f'{escape(str(diagnostic.get("scanner_limitation", "")))}'
        '</section>'
    )
    not_in_ab_rate = float(diagnostic.get("not_in_ab_rate", 0) or 0)
    seen_rate = float(diagnostic.get("seen_but_filtered_rate", 0) or 0)
    true_missed_count = int(diagnostic.get("missed_by_pool_count", diagnostic.get("missed_count", 0)) or 0)
    true_missed_rate = float(diagnostic.get("missed_by_pool_rate", diagnostic.get("missed_rate", 0)) or 0)
    regret_rate = float((diagnostic.get("regret_after_close") or {}).get("rate", 0) or 0)
    metrics = (
        '<div class="summary">'
        f'{_metric("掃描池股票", int(diagnostic.get("total_scanned", 0)))}'
        f'{_metric("強勢異動數", int(diagnostic.get("strong_move_count", 0)))}'
        f'{_metric("進入 A/B+/B", int(diagnostic.get("entered_ai_count", 0)))}'
        f'{_metric_text("強勢股未進 A/B+/B", f"{not_in_ab_rate:.2f}%")}'
        f'{_metric("已看到但未推薦", int(diagnostic.get("seen_but_filtered_count", 0)))}'
        f'{_metric_text("已看到未推薦比例", f"{seen_rate:.2f}%")}'
        f'{_metric("真漏抓", true_missed_count)}'
        f'{_metric_text("真漏抓率", f"{true_missed_rate:.2f}%")}'
        f'{_metric_text("盤後可惜漏掉率", f"{regret_rate:.2f}%")}'
        '</div>'
    )
    seen = diagnostic.get("seen_but_filtered") or {}
    by_status = seen.get("by_status") or {}
    status_text = "、".join(f"{_entry_status_label(str(key))} {value} 檔" for key, value in sorted(by_status.items())) or "目前沒有已看到但未推薦的強勢股。"
    missed_reasons = ((diagnostic.get("missed_by_pool") or {}).get("reason_counts") or {})
    missed_text = "、".join(f"{key} {value} 檔" for key, value in sorted(missed_reasons.items())) or "目前沒有真漏抓原因統計。"
    regret_message = str((diagnostic.get("regret_after_close") or {}).get("message") or "盤後可惜漏掉率需累積盤後資料。")
    explanation = (
        '<section class="data-status">'
        f'<strong>已看到但未推薦分類：</strong>{escape(status_text)}<br>'
        f'<strong>真漏抓原因：</strong>{escape(missed_text)}<br>'
        f'<span class="muted">{escape(regret_message)}</span>'
        '</section>'
    )
    body = []
    for item in rows[:40]:
        body.append(
            '<tr>'
            f'<td><strong>{escape(str(item.get("symbol", "")))}｜{escape(str(item.get("name", "")))}</strong><br><span class="muted">{escape(str(item.get("latest_at", "")))}</span></td>'
            f'<td>{escape(_diagnostic_bucket_label(str(item.get("diagnostic_bucket", "-"))))}</td>'
            f'{_change_cell(item.get("change_pct"))}'
            f'<td data-sort-value="{_sort_value(item.get("latest_price"))}">{_fmt(item.get("latest_price"))}</td>'
            f'<td data-sort-value="{_sort_value(item.get("volume"))}">{_fmt_int(item.get("volume"))}</td>'
            f'<td data-sort-value="{_sort_value(item.get("volume_ratio"))}">{_fmt(item.get("volume_ratio"))}x</td>'
            f'<td data-sort-value="{_sort_value(item.get("turnover"))}">{_money(float(item.get("turnover") or 0)) if item.get("turnover") is not None else "-"}</td>'
            f'<td>{_yes_no(bool(item.get("above_vwap")))}</td>'
            f'<td>{_yes_no(bool(item.get("break_prev_high")))}</td>'
            f'<td>{_yes_no(bool(item.get("break_intraday_high")))}</td>'
            f'<td>{_yes_no(bool(item.get("entered_ai_candidates")))}</td>'
            f'<td>{escape(str(item.get("ai_grade", "-")))}</td>'
            f'<td>{escape(_entry_status_label(str(item.get("entry_status", "-"))))}</td>'
            f'<td class="notes">{escape(str(item.get("not_selected_reason", "-")))}</td>'
            f'<td>{escape(str(item.get("reason_code", "-")))}</td>'
            '</tr>'
        )
    return (
        intro + metrics + explanation
        + '<div class="table-wrap"><table class="sortable"><thead><tr>'
        '<th data-sort="text">股票</th><th data-sort="text">診斷分類</th><th data-sort="number">今日漲幅</th><th data-sort="number">目前價格</th>'
        '<th data-sort="number">成交量</th><th data-sort="number">量比</th><th data-sort="number">成交金額</th><th data-sort="text">站上 VWAP</th>'
        '<th data-sort="text">突破昨高</th><th data-sort="text">突破盤中/前高</th><th data-sort="text">進入 AI 候選</th>'
        '<th data-sort="text">AI 分級</th><th data-sort="text">entry_status</th><th>未入選原因</th><th data-sort="text">reason code</th>'
        '</tr></thead><tbody>'
        + ("".join(body) or '<tr><td colspan="15">目前沒有掃描列。</td></tr>')
        + '</tbody></table></div>'
    )


def _limit_up_strength_panel(summary: Optional[LongModelSummary]) -> str:
    data = _limit_up_context_from_summary(summary, summary.diagnostics if summary else None)
    if not data:
        return '<section class="notice">目前沒有漲停強勢股診斷資料。</section>'
    rows = list(data.get("rows") or [])
    if not rows:
        rows = _limit_up_rows_from_watchlist(data)
    metrics = (
        '<div class="summary">'
        f'{_metric("接近漲停 / 漲停", int(data.get("near_limit_up_count", 0)))}'
        f'{_metric("系統有看到", int(data.get("seen_count", 0)))}'
        f'{_metric("進入 A/B+/B", int(data.get("entered_ai_count", 0)))}'
        f'{_metric("鎖漲停觀察", int(data.get("locked_count", 0)))}'
        f'{_metric("追價風險", int(data.get("chase_risk_count", 0)))}'
        f'{_metric("等待確認", int(data.get("wait_confirm_count", 0)))}'
        f'{_metric("high_risk 觀察", int(data.get("high_risk_count", 0)))}'
        f'{_metric("avoid", int(data.get("avoid_count", 0)))}'
        f'{_metric("資料不足", int(data.get("data_missing_count", 0)))}'
        f'{_metric("真漏抓", int(data.get("missed_by_pool_count", 0)))}'
        '</div>'
    )
    body = []
    for item in rows[:30]:
        advisor_url = f"/tw/advisor?symbol={escape(str(item.get('symbol', '')))}"
        body.append(
            '<tr>'
            f'<td><a href="{advisor_url}"><strong>{escape(str(item.get("symbol", "")))}｜{escape(str(item.get("name", "")))}</strong></a><br><span class="muted">{escape(str(item.get("latest_at", "")))}</span></td>'
            f'<td>{escape(str(item.get("limit_up_status", "-")))}</td>'
            f'{_change_cell(item.get("change_pct"))}'
            f'<td data-sort-value="{_sort_value(item.get("latest_price"))}">{_fmt(item.get("latest_price"))}</td>'
            f'<td data-sort-value="{_sort_value(item.get("volume_ratio"))}">{_fmt(item.get("volume_ratio"))}x</td>'
            f'<td>{_yes_no(bool(item.get("above_vwap")))}</td>'
            f'<td>{_yes_no(bool(item.get("break_prev_high")))}</td>'
            f'<td>{escape(str(item.get("ai_grade", "-")))}</td>'
            f'<td>{escape(_entry_status_label(str(item.get("entry_status", "-"))))}</td>'
            f'<td><strong>{escape(str(item.get("limit_up_decision", "-")))}</strong><br><span class="muted">{escape(str(item.get("limit_up_explanation", "-")))}</span></td>'
            f'<td><strong>{escape(str(item.get("limit_up_now_action", "-")))}</strong><br><span class="muted">等：{escape(str(item.get("limit_up_wait_for", "-")))}</span></td>'
            f'<td>{escape(str(item.get("reason_code", "-")))}</td>'
            '</tr>'
        )
    return (
        '<section class="data-status">'
        f'<strong>{escape(str(data.get("definition", "")))}</strong><br>'
        f'<span class="muted">{escape(str(data.get("not_buy_reason", "")))}</span>'
        '</section>'
        f'{metrics}'
        '<div class="table-wrap"><table class="sortable"><thead><tr>'
        '<th data-sort="text">股票</th><th data-sort="text">漲停狀態</th><th data-sort="number">漲幅</th>'
        '<th data-sort="number">現價</th><th data-sort="number">量比</th><th data-sort="text">站上 VWAP</th>'
        '<th data-sort="text">突破昨高</th><th data-sort="text">AI 分級</th><th data-sort="text">entry_status</th>'
        '<th>系統判斷</th><th>下一步</th><th data-sort="text">reason code</th>'
        '</tr></thead><tbody>'
        + ("".join(body) or '<tr><td colspan="12">目前沒有接近漲停或漲停鎖住的掃描標的。</td></tr>')
        + '</tbody></table></div>'
    )


def _limit_up_rows_from_watchlist(data: dict) -> list[dict]:
    rows = []
    for item in list(data.get("top_watchlist") or [])[:30]:
        if not isinstance(item, dict):
            continue
        entry = str(item.get("entry_status") or "-")
        action = str(item.get("action") or "列入觀察，等待明天盤中重新確認。")
        high_risk = entry == "high_risk" or "追價" in action or "風險" in action
        rows.append(
            {
                "symbol": str(item.get("symbol") or ""),
                "name": str(item.get("name") or ""),
                "latest_at": str(item.get("latest_at") or ""),
                "limit_up_status": "接近漲停 / 急拉",
                "change_pct": item.get("change_pct"),
                "latest_price": item.get("latest_price"),
                "volume_ratio": item.get("volume_ratio"),
                "above_vwap": item.get("above_vwap"),
                "break_prev_high": item.get("break_prev_high"),
                "ai_grade": item.get("ai_grade") or "-",
                "entry_status": entry,
                "limit_up_decision": "有看到，但追價風險高" if high_risk else "有看到，列入續強觀察",
                "limit_up_explanation": "強勢但追價風險高，不列入今日做多。" if high_risk else "急拉或接近漲停已被系統看到；盤後僅列入下個交易日觀察。",
                "limit_up_now_action": action,
                "limit_up_wait_for": "明天重新確認 VWAP、量比、突破、停損距離與進場雷達。",
                "reason_code": item.get("reason_code") or ("high_risk" if high_risk else "limit_up_inferred"),
            }
        )
    return rows


def _diagnostic_bucket_label(bucket: str) -> str:
    return {
        "selected": "進入 A/B+/B",
        "seen_but_filtered": "已看到但未推薦",
        "missed_by_pool": "真漏抓",
        "not_strong": "非強勢異動",
    }.get(bucket, bucket or "-")


def _model_diagnostic_panel(summary: Optional[LongModelSummary]) -> str:
    diagnostics = summary.diagnostics if summary else {}
    conditions = (diagnostics or {}).get("model_conditions") or {}
    causes = (diagnostics or {}).get("root_cause_diagnosis") or []
    backtest = (diagnostics or {}).get("backtest_diagnostic") or {}
    if not conditions:
        return '<div class="notice">目前沒有模型條件診斷。</div>'
    def condition_list(key: str) -> str:
        return "".join(f"<li>{escape(str(item))}</li>" for item in conditions.get(key, []))
    cause_items = "".join(f"<li>{escape(str(item))}</li>" for item in causes)
    required = "".join(f"<li>{escape(str(item))}</li>" for item in backtest.get("required_next_data", []))
    return (
        '<section class="decision-center">'
        '<div class="decision-grid">'
        f'<div class="decision-panel"><strong>A 級條件</strong><ul class="decision-list">{condition_list("a")}</ul></div>'
        f'<div class="decision-panel"><strong>B+ 條件</strong><ul class="decision-list">{condition_list("b_plus")}</ul></div>'
        f'<div class="decision-panel"><strong>B 級條件</strong><ul class="decision-list">{condition_list("b")}</ul></div>'
        f'<div class="decision-panel"><strong>C / D 排除條件</strong><ul class="decision-list">{condition_list("c_d_exclusion")}</ul></div>'
        f'<div class="decision-panel"><strong>entry_status 條件</strong><ul class="decision-list">{condition_list("entry_status")}</ul></div>'
        f'<div class="decision-panel"><strong>強烈買多候選條件</strong><ul class="decision-list">{condition_list("strong_long_candidate")}</ul></div>'
        f'<div class="decision-panel"><strong>進場雷達通過條件</strong><ul class="decision-list">{condition_list("executable")}</ul></div>'
        f'<div class="decision-panel"><strong>目前主要診斷</strong><ul class="decision-list">{cause_items}</ul></div>'
        '</div>'
        f'<section class="notice">回測診斷：{escape(str(backtest.get("message", "目前樣本不足時不硬算勝率。")))}'
        f'<ul>{required}</ul></section>'
        '</section>'
    )


def _strong_long_funnel_panel(summary: Optional[LongModelSummary]) -> str:
    funnel = ((summary.diagnostics or {}).get("strong_long_funnel") if summary else None) or {}
    if not funnel:
        return '<section class="notice">目前沒有強烈買多漏斗資料。</section>'
    top_blockers = funnel.get("top_blockers") or []
    action_plan = funnel.get("action_plan") or []
    blocker_text = "、".join(
        f"{item.get('reason')} {int(item.get('count', 0))} 檔"
        for item in top_blockers[:5]
    ) or "暫無主要卡關原因"
    rows = [
        ("全市場普通股總數", funnel.get("total_market_count")),
        ("今日異動候選數", funnel.get("momentum_candidate_count")),
        ("進入模型評分數", funnel.get("model_candidates_count")),
        ("資料 live 數", funnel.get("live_count")),
        ("站上 VWAP 數", funnel.get("above_vwap_count")),
        ("量比 >= 0.8 數", funnel.get("volume_ratio_gte_0_8_count")),
        ("量比 >= 1.0 數", funnel.get("volume_ratio_gte_1_0_count")),
        ("突破昨高數", funnel.get("break_prev_high_count")),
        ("多方分數 >= 65 數", funnel.get("bullish_score_gte_65_count")),
        ("多方分數 >= 70 數", funnel.get("bullish_score_gte_70_count")),
        ("多方分數 >= 75 數", funnel.get("bullish_score_gte_75_count")),
        ("風險分數 <= 55 數", funnel.get("risk_score_lte_55_count")),
        ("風險分數 <= 40 數", funnel.get("risk_score_lte_40_count")),
        ("信心分數 >= 55 數", funnel.get("confidence_score_gte_55_count")),
        ("信心分數 >= 60 數", funnel.get("confidence_score_gte_60_count")),
        ("被 high_risk 擋下", funnel.get("blocked_high_risk_count")),
        ("被 wait_volume 擋下", funnel.get("blocked_wait_volume_count")),
        ("被 wait_vwap 擋下", funnel.get("blocked_wait_vwap_count")),
        ("被 wait_breakout 擋下", funnel.get("blocked_wait_breakout_count")),
        ("最後進入強烈買多", funnel.get("strong_long_candidate_count")),
        ("最後進入 executable", funnel.get("executable_count")),
    ]
    metrics = "".join(_metric(str(label), int(value or 0)) for label, value in rows)
    blocker_rows = "".join(
        f"<tr><td>{escape(str(item.get('reason', '-')))}</td><td>{int(item.get('count', 0) or 0)}</td></tr>"
        for item in top_blockers
    ) or '<tr><td colspan="2">目前沒有卡關資料。</td></tr>'
    action_rows = "".join(
        "<tr>"
        f"<td>{escape(str(item.get('reason', '-')))}<br><span class=\"muted\">{int(item.get('count', 0) or 0)} 檔</span></td>"
        f"<td>{escape(str(item.get('action', '-')))}</td>"
        f"<td>{escape(str(item.get('wait_for', '-')))}</td>"
        f"<td>{escape(str(item.get('avoid', '-')))}</td>"
        "</tr>"
        for item in action_plan[:5]
    ) or '<tr><td colspan="4">目前沒有卡關處理建議。</td></tr>'
    primary_action = str(funnel.get("primary_action") or "先累積漏斗資料，再判斷主要卡關。")
    primary_wait = str(funnel.get("primary_wait_condition") or "等待下一次刷新。")
    return (
        '<section class="decision-center">'
        '<section class="notice">強烈買多候選不等於進場；仍需通過更嚴格的觸發、風控與資料安全規則。</section>'
        f'<div class="summary">{metrics}</div>'
        f'<section class="notice"><strong>現在先做：</strong>{escape(primary_action)}<br><strong>等到什麼：</strong>{escape(primary_wait)}</section>'
        f'<p class="muted"><strong>主要卡關原因：</strong>{escape(blocker_text)}</p>'
        '<h3>卡關處理順序</h3>'
        '<div class="table-wrap"><table><thead><tr><th>卡關</th><th>現在先做</th><th>等到什麼</th><th>不要做</th></tr></thead><tbody>'
        f'{action_rows}'
        '</tbody></table></div>'
        '<h3>卡關統計</h3>'
        '<div class="table-wrap"><table><thead><tr><th>卡關原因</th><th>檔數</th></tr></thead><tbody>'
        f'{blocker_rows}'
        '</tbody></table></div>'
        '</section>'
    )


def _strong_long_blocker_summary(funnel: dict) -> str:
    blockers = funnel.get("top_blockers") or []
    if not blockers:
        return "尚未累積足夠漏斗資料"
    return "、".join(
        f"{item.get('reason')} {int(item.get('count', 0) or 0)} 檔"
        for item in blockers[:3]
    )


def _user_guide_panel(summary: Optional[LongModelSummary]) -> str:
    guide = ((summary.diagnostics or {}).get("user_guide") if summary else None) or []
    if not guide:
        return ""
    items = "".join(f"<li>{escape(str(item))}</li>" for item in guide)
    return f'<section class="notice"><ul>{items}</ul></section>'


def _age_text(value) -> str:
    try:
        if value is None:
            return "-"
        return f"{float(value):.1f} 分鐘"
    except (TypeError, ValueError):
        return "-"


def _tracked_table(rows: List[TrackedSymbol], empty_text: str = "目前沒有追蹤標的。") -> str:
    body = []
    for row in rows:
        high_risk = _is_tracked_high_risk(row)
        bullish_label = "方向偏多" if high_risk else _safe_bullish_label(row.bullish_label)
        entry_status = "追價風險高" if high_risk else row.entry_status
        candidate_direction = "方向偏多" if high_risk and "做多" in row.candidate_direction else row.candidate_direction
        opening_direction = "避免追價" if high_risk and "做多" in row.opening_direction else row.opening_direction
        bullish_reasons = _tracked_bullish_reasons(row) if high_risk else row.bullish_reasons
        notes = _tracked_notes(row) if high_risk else row.notes
        body.append(
            "<tr>"
            f'<td><span class="badge s-{escape(row.status)}">{escape(row.status)}</span></td>'
            f'<td data-sort-value="{_sort_value(row.bullish_score)}"><span class="badge b-{escape(bullish_label)}">{escape(bullish_label)}</span><br><span class="muted">{_fmt(row.bullish_score)}</span></td>'
            f"<td>{escape(entry_status)}</td>"
            f"<td><strong><a href=\"{_advisor_link(row.symbol)}\">{escape(stock_label(row.name, row.symbol))}</a>{_position_size_tag(row.trigger_price, row.stop_loss)}</strong></td>"
            f"<td>{escape(sector_label(row.sector))}<br><span class=\"muted\">{escape(row.sector_state)}</span></td>"
            f"{_number_cell(row.last_price)}"
            f"{_change_cell(row.day_change_pct)}"
            f"<td data-sort-value=\"{_sort_value(row.candidate_score)}\">{_direction(candidate_direction)}<br><span class=\"muted\">{_fmt(row.candidate_score)}</span></td>"
            f"<td data-sort-value=\"{_sort_value(row.volume_ratio)}\">{_direction(opening_direction)}<br><span class=\"muted\">量比 {_fmt(row.volume_ratio)}x</span></td>"
            f"<td data-sort-value=\"{_sort_value(row.vwap)}\">{escape(row.vwap_state)}<br><span class=\"muted\">{_fmt(row.vwap)}</span></td>"
            f"{_number_cell(row.trigger_price)}"
            f"{_number_cell(row.stop_loss)}"
            f"{_number_cell(row.target_price)}"
            f"{_number_cell(row.risk_per_share)}"
            f"<td data-sort-value=\"{_sort_value(row.suggested_shares)}\">{'' if row.suggested_shares is None else row.suggested_shares}</td>"
            f"<td data-sort-value=\"{_sort_value(row.institutional_rank)}\">{'' if row.institutional_rank is None else row.institutional_rank}</td>"
            f"{_change_cell(row.institutional_buy_million, suffix=' 百萬')}"
            f"<td class=\"notes\">{escape('；'.join(bullish_reasons))}</td>"
            f"<td class=\"notes\">{escape('；'.join(row.cancel_conditions))}</td>"
            f"<td class=\"notes\">{escape('；'.join(notes))}</td>"
            "</tr>"
        )
    if not body:
        body.append(f'<tr><td colspan="20">{escape(empty_text)}</td></tr>')
    return (
        "<table class=\"sortable\"><thead><tr>"
        "<th data-sort=\"text\">狀態</th><th data-sort=\"number\">方向偏多</th><th data-sort=\"text\">進場狀態</th>"
        "<th data-sort=\"text\">標的</th><th data-sort=\"text\">族群</th>"
        "<th data-sort=\"number\">現價</th><th data-sort=\"number\">漲跌</th>"
        "<th data-sort=\"number\">盤前</th><th data-sort=\"number\">開盤</th>"
        "<th data-sort=\"number\">VWAP</th>"
        "<th data-sort=\"number\">觸發</th><th data-sort=\"number\">停損</th><th data-sort=\"number\">目標</th>"
        "<th data-sort=\"number\">每股風險</th><th data-sort=\"number\">股數上限</th>"
        "<th data-sort=\"number\">法人排行</th><th data-sort=\"number\">法人買超</th><th>偏多理由</th><th>取消條件</th><th>備註</th></tr></thead><tbody>"
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
            f"<td><strong><a href=\"{_advisor_link(item.symbol)}\">{escape(stock_label(item.name, item.symbol))}</a>{_position_size_tag(item.trigger_price or item.last_price, item.stop_loss)}</strong><br><span class=\"muted\">{escape(sector_label(item.sector))}</span></td>"
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
            f"<td>{_trade_bias_badge(item.trade_bias, item.trade_bias_label, item.entry_status)}<br><span class=\"muted\">{escape(_display_trade_bias_reason(item.entry_status, item.trade_bias_reason))}</span></td>"
            f"<td>{escape(_entry_status_label(item.entry_status))}<br><span class=\"muted\">{escape(_entry_status_message(item.entry_status))}</span></td>"
            f"<td>{_institutional_badge(item)}<br><span class=\"muted\">{escape(str((getattr(item, 'institutional_context', {}) or {}).get('institutional_reason', '')))}</span></td>"
            f"<td>{_sector_context_badge(item)}<br><span class=\"muted\">{escape(str((getattr(item, 'sector_context', {}) or {}).get('sector_reason', '')))}</span></td>"
            f"<td data-sort-value=\"{_sort_value(item.confidence_score)}\">{_fmt(item.confidence_score)}<br><span class=\"muted\">{escape(item.confidence_level_label)}</span></td>"
            f"<td data-sort-value=\"{_sort_value(item.conflicts_count)}\">{item.conflicts_count}<br><span class=\"muted\">{escape(item.conflict_summary)}</span></td>"
            f"<td class=\"notes\">{escape(item.confidence_summary)}</td>"
            f"<td class=\"notes\">{escape('；'.join(item.reasons[:5]))}</td>"
            f"<td class=\"notes\">{escape('；'.join(item.risk_reasons[:4]))}</td>"
            "</tr>"
        )
    return (
        "<table class=\"sortable\"><thead><tr>"
        "<th data-sort=\"text\">分級</th><th data-sort=\"text\">標的</th><th data-sort=\"number\">現價</th>"
        "<th data-sort=\"number\">今日漲幅</th><th data-sort=\"number\">成交金額</th><th data-sort=\"number\">量比</th>"
        "<th data-sort=\"number\">VWAP</th><th data-sort=\"text\">破昨高</th><th data-sort=\"text\">破5日高</th><th data-sort=\"text\">破10日高</th>"
        "<th data-sort=\"number\">多方分數</th><th data-sort=\"number\">風險分數</th><th data-sort=\"text\">當下狀態</th><th data-sort=\"text\">進場狀態</th>"
        "<th data-sort=\"text\">籌碼背景</th><th data-sort=\"text\">族群狀態</th><th data-sort=\"number\">信心分數</th><th data-sort=\"number\">衝突</th><th>信心摘要</th><th>多方理由</th><th>風險理由</th>"
        "</tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table>"
    )


def _confidence_radar(summary: Optional[LongModelSummary]) -> str:
    if summary is None:
        return "<div class=\"summary\"><div class=\"metric\"><span class=\"muted\">信心資料</span><strong>0</strong></div></div>"
    items = summary.candidates
    conflict_counts: Dict[str, int] = {}
    for item in items:
        if item.conflicts:
            message = str(item.conflicts[0].get("message", item.conflict_summary))
            conflict_counts[message] = conflict_counts.get(message, 0) + 1
    common_conflict = max(conflict_counts.items(), key=lambda pair: pair[1])[0] if conflict_counts else "無明顯衝突"
    return (
        "<div class=\"summary\">"
        f"{_metric('高信心數', sum(1 for item in items if item.confidence_level == 'high'))}"
        f"{_metric('中等信心數', sum(1 for item in items if item.confidence_level == 'medium'))}"
        f"{_metric('低信心數', sum(1 for item in items if item.confidence_level == 'low'))}"
        f"{_metric('不可信數', sum(1 for item in items if item.confidence_level == 'unreliable'))}"
        f"{_metric('指標衝突總數', sum(item.conflicts_count for item in items))}"
        f"{_metric_text('最常見衝突', common_conflict)}"
        "</div>"
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
        f"{_metric('已觸發回測', int(data.get('triggered_backtest_count', 0)))}"
        f"{_metric('觀察中', int(data.get('observed_count', 0)))}"
        f"{_metric('已觸發', int(data.get('triggered_count', 0)))}"
        f"{_metric('未觸發過期', int(data.get('expired_count', 0)))}"
        f"{_metric('已結算', int(data.get('closed_count', 0)))}"
        f"{_metric('達標', int(data.get('target', 0)))}"
        f"{_metric('停損', int(data.get('stop', 0)))}"
        f"{_metric_text('平均報酬', avg_return)}"
        "</div>"
        f"{_entry_status_backtest_table(data.get('by_entry_status', []))}"
        f"{_signal_type_backtest_table(data.get('by_signal_type', []))}"
        f"{_time_bucket_backtest_table(data.get('by_time_bucket', []))}"
        f"{_grade_backtest_table(data.get('by_grade', []))}"
    )


def _recommendation_checklist_table(summary: Optional[LongModelSummary]) -> str:
    if summary is None:
        return "<table><tbody><tr><td>目前沒有推薦檢查資料。</td></tr></tbody></table>"
    data = summary.recommendation_checklist or {}
    return (
        "<div class=\"summary\">"
        f"{_metric('今日候選股總數', int(data.get('candidate_total', 0)))}"
        f"{_metric('A級高信心數量', int(data.get('grade_a', 0)))}"
        f"{_metric('B+可練習觀察數量', int(data.get('grade_b_plus', 0)))}"
        f"{_metric('B級等待確認數量', int(data.get('grade_b', 0)))}"
        f"{_metric('C/D避開數量', int(data.get('grade_cd', 0)))}"
        f"{_metric('今日可虛擬交易觀察數量', int(data.get('paper_practice_observable', 0)))}"
        f"{_metric('進場雷達通過', int(data.get('executable', 0)))}"
        f"{_metric('practice_long 練習買多', int(data.get('practice_long', 0)))}"
        f"{_metric('當下買多', int(data.get('trade_long', 0)))}"
        f"{_metric('當下看空', int(data.get('trade_short', 0)))}"
        f"{_metric('當下觀察', int(data.get('trade_watch', 0)))}"
        f"{_metric('wait_volume 等量能', int(data.get('wait_volume', 0)))}"
        f"{_metric('wait_vwap 等VWAP', int(data.get('wait_vwap', 0)))}"
        f"{_metric('high_risk 風險過高', int(data.get('high_risk', 0)))}"
        f"{_metric('avoid 暫不追蹤', int(data.get('avoid', 0)))}"
        f"{_metric('已寫入 recommendations 數量', int(data.get('recommendations', 0)))}"
        f"{_metric('observed 觀察中', int(data.get('observed', 0)))}"
        f"{_metric('triggered 已觸發', int(data.get('triggered', 0)))}"
        f"{_metric('expired 未觸發過期', int(data.get('expired', 0)))}"
        f"{_metric('closed 已收盤結算', int(data.get('closed', 0)))}"
        f"{_metric('今日可回測數量', int(data.get('backtest_trackable', 0)))}"
        f"{_metric('今日已觸發回測數量', int(data.get('triggered_backtest', 0)))}"
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


def _signal_type_backtest_table(rows: List[dict]) -> str:
    if not rows:
        return ""
    body = []
    for item in rows:
        avg_return = f"{float(item.get('avg_return', 0)):+.2f}%"
        avg_gain = f"{float(item.get('avg_max_gain', 0)):+.2f}%"
        avg_drawdown = f"{float(item.get('avg_max_drawdown', 0)):+.2f}%"
        body.append(
            "<tr>"
            f"<td>{escape(_signal_type_label(str(item.get('signal_type', ''))))}</td>"
            f"<td data-sort-value=\"{_sort_value(item.get('total'))}\">{int(item.get('total', 0))}</td>"
            f"<td data-sort-value=\"{_sort_value(item.get('triggered'))}\">{int(item.get('triggered', 0))}</td>"
            f"<td data-sort-value=\"{_sort_value(item.get('target'))}\">{int(item.get('target', 0))}</td>"
            f"<td data-sort-value=\"{_sort_value(item.get('stop'))}\">{int(item.get('stop', 0))}</td>"
            f"<td data-sort-value=\"{_sort_value(item.get('win_rate'))}\">{_fmt(float(item.get('win_rate', 0)))}%</td>"
            f"<td data-sort-value=\"{_sort_value(item.get('avg_return'))}\">{escape(avg_return)}</td>"
            f"<td data-sort-value=\"{_sort_value(item.get('avg_max_gain'))}\">{escape(avg_gain)}</td>"
            f"<td data-sort-value=\"{_sort_value(item.get('avg_max_drawdown'))}\">{escape(avg_drawdown)}</td>"
            "</tr>"
        )
    return (
        "<h3>依訊號型態回測</h3>"
        "<table class=\"sortable\"><thead><tr><th data-sort=\"text\">訊號型態</th>"
        "<th data-sort=\"number\">推薦數</th><th data-sort=\"number\">已觸發</th>"
        "<th data-sort=\"number\">達標</th><th data-sort=\"number\">停損</th><th data-sort=\"number\">勝率</th>"
        "<th data-sort=\"number\">平均報酬</th><th data-sort=\"number\">平均最大漲幅</th><th data-sort=\"number\">平均最大回撤</th>"
        "</tr></thead><tbody>"
        + "".join(body)
        + "</tbody></table>"
    )


def _time_bucket_backtest_table(rows: List[dict]) -> str:
    if not rows:
        return ""
    body = []
    for item in rows:
        avg_return = f"{float(item.get('avg_return', 0)):+.2f}%"
        avg_gain = f"{float(item.get('avg_max_gain', 0)):+.2f}%"
        avg_drawdown = f"{float(item.get('avg_max_drawdown', 0)):+.2f}%"
        body.append(
            "<tr>"
            f"<td>{escape(_time_bucket_label(str(item.get('time_bucket', ''))))}</td>"
            f"<td data-sort-value=\"{_sort_value(item.get('total'))}\">{int(item.get('total', 0))}</td>"
            f"<td data-sort-value=\"{_sort_value(item.get('triggered'))}\">{int(item.get('triggered', 0))}</td>"
            f"<td data-sort-value=\"{_sort_value(item.get('target'))}\">{int(item.get('target', 0))}</td>"
            f"<td data-sort-value=\"{_sort_value(item.get('stop'))}\">{int(item.get('stop', 0))}</td>"
            f"<td data-sort-value=\"{_sort_value(item.get('win_rate'))}\">{_fmt(float(item.get('win_rate', 0)))}%</td>"
            f"<td data-sort-value=\"{_sort_value(item.get('avg_return'))}\">{escape(avg_return)}</td>"
            f"<td data-sort-value=\"{_sort_value(item.get('avg_max_gain'))}\">{escape(avg_gain)}</td>"
            f"<td data-sort-value=\"{_sort_value(item.get('avg_max_drawdown'))}\">{escape(avg_drawdown)}</td>"
            "</tr>"
        )
    return (
        "<h3>依時間區間回測</h3>"
        "<table class=\"sortable\"><thead><tr><th data-sort=\"text\">時間區間</th>"
        "<th data-sort=\"number\">推薦數</th><th data-sort=\"number\">已觸發</th>"
        "<th data-sort=\"number\">達標</th><th data-sort=\"number\">停損</th><th data-sort=\"number\">勝率</th>"
        "<th data-sort=\"number\">平均報酬</th><th data-sort=\"number\">平均最大漲幅</th><th data-sort=\"number\">平均最大回撤</th>"
        "</tr></thead><tbody>"
        + "".join(body)
        + "</tbody></table>"
    )


def _b_plus_trigger_table(summary: Optional[LongModelSummary]) -> str:
    if summary is None or not summary.b_plus_triggers:
        return "<table><tbody><tr><td>目前沒有 B+ 練習觀察訊號。</td></tr></tbody></table>"
    rows = []
    for item in summary.b_plus_triggers:
        name = f"{item.get('symbol', '')}｜{item.get('name_zh', '')}"
        if item.get("market") == "US" and item.get("name_en"):
            name = f"{name}｜{item.get('name_en')}"
        rows.append(
            "<tr>"
            f"<td>{escape(name)}</td>"
            f"<td>{escape(str(item.get('market', '-')))}</td>"
            f"<td data-sort-value=\"{_sort_value(item.get('current_price'))}\">{_fmt(item.get('current_price'))}</td>"
            f"<td data-sort-value=\"{_sort_value(item.get('vwap'))}\">{_fmt(item.get('vwap'))}</td>"
            f"<td data-sort-value=\"{_sort_value(item.get('volume_ratio'))}\">{_fmt(item.get('volume_ratio'))}x</td>"
            f"<td>{escape(_entry_status_label(str(item.get('entry_status', ''))))}</td>"
            f"<td>{escape(str(item.get('lifecycle_status', '-')))}</td>"
            f"<td>{escape(str(item.get('trigger_condition', '-')))}</td>"
            f"<td data-sort-value=\"{_sort_value(item.get('trigger_price'))}\">{_fmt(item.get('trigger_price'))}</td>"
            f"<td>{escape(str(item.get('distance_to_trigger', '-')))}</td>"
            f"<td>{escape(str(item.get('trigger_readiness_label', item.get('trigger_readiness', '-'))))}</td>"
            f"<td class=\"notes\">{escape(str(item.get('trigger_next_action', '-')))}</td>"
            f"<td data-sort-value=\"{_sort_value(item.get('confidence_score'))}\">{_fmt(item.get('confidence_score'))}</td>"
            f"<td class=\"notes\">{escape(str(item.get('confidence_summary', '')))}</td>"
            "</tr>"
        )
    return (
        "<table class=\"sortable\"><thead><tr>"
        "<th data-sort=\"text\">標的</th><th data-sort=\"text\">市場</th><th data-sort=\"number\">現價</th>"
        "<th data-sort=\"number\">VWAP</th><th data-sort=\"number\">量比</th><th data-sort=\"text\">進場狀態</th>"
        "<th data-sort=\"text\">生命週期</th><th data-sort=\"text\">觸發條件</th><th data-sort=\"number\">觸發價</th>"
        "<th data-sort=\"text\">距離觸發</th><th data-sort=\"text\">Readiness</th><th>下一步</th>"
        "<th data-sort=\"number\">信心分數</th><th>信心摘要</th>"
        "</tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table>"
    )


def _grade_backtest_table(rows: List[dict]) -> str:
    if not rows:
        return ""
    body = []
    for item in rows:
        avg_return = f"{float(item.get('avg_return', 0)):+.2f}%"
        avg_gain = f"{float(item.get('avg_max_gain', 0)):+.2f}%"
        avg_drawdown = f"{float(item.get('avg_max_drawdown', 0)):+.2f}%"
        body.append(
            "<tr>"
            f"<td>{escape(str(item.get('grade', '')))}</td>"
            f"<td data-sort-value=\"{_sort_value(item.get('total'))}\">{int(item.get('total', 0))}</td>"
            f"<td data-sort-value=\"{_sort_value(item.get('triggered'))}\">{int(item.get('triggered', 0))}</td>"
            f"<td data-sort-value=\"{_sort_value(item.get('untriggered_ratio'))}\">{_fmt(float(item.get('untriggered_ratio', 0)))}%</td>"
            f"<td data-sort-value=\"{_sort_value(item.get('target'))}\">{int(item.get('target', 0))}</td>"
            f"<td data-sort-value=\"{_sort_value(item.get('stop'))}\">{int(item.get('stop', 0))}</td>"
            f"<td data-sort-value=\"{_sort_value(item.get('avg_return'))}\">{escape(avg_return)}</td>"
            f"<td data-sort-value=\"{_sort_value(item.get('avg_max_gain'))}\">{escape(avg_gain)}</td>"
            f"<td data-sort-value=\"{_sort_value(item.get('avg_max_drawdown'))}\">{escape(avg_drawdown)}</td>"
            "</tr>"
        )
    return (
        "<h3>依分級回測</h3>"
        "<table class=\"sortable\"><thead><tr><th data-sort=\"text\">分級</th>"
        "<th data-sort=\"number\">推薦數</th><th data-sort=\"number\">已觸發</th>"
        "<th data-sort=\"number\">未觸發比例</th><th data-sort=\"number\">達標</th>"
        "<th data-sort=\"number\">停損</th><th data-sort=\"number\">平均報酬</th>"
        "<th data-sort=\"number\">平均最大漲幅</th><th data-sort=\"number\">平均最大回撤</th>"
        "</tr></thead><tbody>"
        + "".join(body)
        + "</tbody></table>"
    )


def _entry_status_label(value: str) -> str:
    return {
        "executable": "進場雷達通過",
        "practice_long": "practice_long 練習買多",
        "wait_volume": "wait_volume 等待量能確認",
        "wait_vwap": "wait_vwap 等待站回VWAP",
        "wait_breakout": "wait_breakout 等待突破",
        "wait_pullback": "wait_pullback 等待回測",
        "high_risk": "high_risk 風險過高",
        "avoid": "avoid 暫不追蹤",
    }.get(value, value or "-")


def _next_step_for_entry(value: str) -> str:
    return {
        "executable": "可進入虛擬交易觀察，先確認 VWAP、停損與部位風險。",
        "practice_long": "列入 B+ 練習觀察，等待觸發條件成立後再驗證。",
        "wait_volume": "等待量比放大，短線資金確認後再評估。",
        "wait_vwap": "等待股價站回 VWAP 並維持，不急著追價。",
        "wait_breakout": "等待突破觸發價或開盤區間高點。",
        "wait_pullback": "等待回測 VWAP 或關鍵價位不破。",
        "high_risk": "避免直接追高，等待風險降溫或回測確認。",
        "avoid": "暫不追蹤，等結構重新轉強再評估。",
    }.get(value, "持續觀察資料更新與風險變化。")


def _signal_type_label(value: str) -> str:
    return {
        "breakout": "突破買點",
        "vwap_pullback": "VWAP 回測買點",
        "continuation": "續強買點",
        "watch": "觀察型",
        "unknown": "未分類",
    }.get(value, value or "未分類")


def _time_bucket_label(value: str) -> str:
    return {
        "pre_open": "開盤前觀察",
        "opening_observation": "開盤觀察 09:00-09:20",
        "main_entry": "主進場區 09:20-10:30",
        "pullback_only": "只做回測 10:30-11:30",
        "late_avoid": "尾盤不追 11:30-13:30",
        "after_close": "收盤後",
        "premarket": "美股盤前",
        "us_opening": "美股開盤初段",
        "us_main": "美股主交易時段",
        "us_late": "美股尾盤",
        "afterhours": "美股盤後",
        "closed": "休市",
        "unknown": "未分類",
    }.get(value, value or "未分類")


def _entry_status_message(value: str) -> str:
    return {
        "executable": "量能與VWAP條件成立，可列入進場雷達重點檢查。",
        "practice_long": "條件接近且風險可控，可用虛擬交易練習買多。",
        "wait_volume": "多方結構不錯，但量能不足，等待量比放大後再觀察。",
        "wait_vwap": "突破條件成立，但尚未站上 VWAP，等待站回均價線。",
        "wait_breakout": "站上 VWAP 或量能條件尚可，等待突破觸發價。",
        "wait_pullback": "條件可追蹤，等待回測或整理後再評估。",
        "high_risk": "多方動能強，但追價風險偏高，避免直接追高。",
        "avoid": "條件不足或跌破VWAP，暫不追蹤。",
    }.get(value, "")


def _safe_bullish_label(label: str) -> str:
    return {
        "強烈" + "看漲": "方向偏多",
        "看漲": "偏多",
    }.get(label or "", label or "觀察")


def _display_trade_bias_label(entry_status: str, value: str, label: str) -> str:
    if entry_status == "high_risk":
        return "方向偏多"
    if entry_status == "practice_long":
        return "練習買多"
    if entry_status == "executable":
        return "進場雷達通過"
    if label in {"強烈" + "看漲", "看漲"}:
        return _safe_bullish_label(label)
    return label or {"long": "買多", "short": "看空", "watch": "觀察"}.get(value, "觀察")


def _display_trade_bias_reason(entry_status: str, reason: str) -> str:
    if entry_status == "high_risk":
        return "方向偏多，但追價風險高，不列入今日做多。"
    if entry_status == "practice_long":
        return "可作為練習買多觀察，不是正式可執行。"
    return reason or ""


def _display_conclusion_for_candidate(item: LongCandidate) -> str:
    if item.entry_status == "high_risk":
        return "方向偏多，但追價風險高，不列入今日做多。"
    return _display_trade_bias_label(item.entry_status, item.trade_bias, item.trade_bias_label) or _entry_status_label(item.entry_status)


def _display_reason_for_candidate(item: LongCandidate) -> str:
    if item.entry_status == "high_risk":
        return (
            "股價站上 VWAP 或短線動能偏多，但風險分數偏高、停利空間不足或追價風險升高，"
            "因此僅列入風險觀察，不符合可執行條件。"
        )
    return "；".join(item.reasons[:3]) or item.trade_bias_reason or item.confidence_summary or "目前沒有明確多方理由。"


def _is_tracked_high_risk(row: TrackedSymbol) -> bool:
    return row.status == "風險過高" or row.entry_status == "風險過高" or row.suggested_shares == 0


def _tracked_bullish_reasons(row: TrackedSymbol) -> List[str]:
    reasons = ["方向偏多，但追價風險高，不列入今日做多"]
    if row.vwap_state == "站上VWAP":
        reasons.append("股價站上 VWAP")
    if row.volume_ratio is not None:
        reasons.append(f"量比 {_fmt(row.volume_ratio)}x")
    return reasons[:4]


def _tracked_notes(row: TrackedSymbol) -> List[str]:
    notes = ["避免追價，等待拉回 VWAP 附近、風險分數下降或重新突破後再評估。"]
    if row.risk_per_share is not None:
        notes.append("停損距離或每股風險偏高")
    return notes


def _trade_bias_badge(value: str, label: str, entry_status: str = "") -> str:
    bias = value if value in {"long", "short", "watch"} else "watch"
    text = _display_trade_bias_label(entry_status, bias, label)
    display_bias = "watch" if entry_status in {"high_risk", "avoid"} else bias
    return f'<span class="badge bias-{escape(display_bias)}">{escape(text)}</span>'


def _debug_block(summary: Optional[LongModelSummary]) -> str:
    if summary is None or not summary.debug_info:
        return ""
    info = summary.debug_info
    rows = [
        ("app version / commit", str(info.get("app_version", "-"))),
        ("app version source", str(info.get("app_version_source", "-"))),
        ("scoring model version", str(info.get("scoring_model_version", "-"))),
        ("session policy version", str(info.get("session_policy_version", "-"))),
        ("dashboard generated_at", str(info.get("dashboard_generated_at", "-"))),
        ("recommendations count from DB", str(info.get("recommendations_count_from_db", "-"))),
        ("candidates count from current run", str(info.get("candidates_count_from_current_run", "-"))),
        ("visible candidates count", str(info.get("visible_candidates_count", "-"))),
        ("momentum scanner version", str(info.get("momentum_scanner_version", "-"))),
        ("momentum scan total", str(info.get("momentum_scan_total", "-"))),
        ("momentum scan success", str(info.get("momentum_scan_success", "-"))),
        ("momentum scan model scored", str(info.get("momentum_scan_model_scored", "-"))),
        ("full market version", str(info.get("full_market_version", "-"))),
        ("full market pool symbols", str(info.get("full_market_pool_symbols", "-"))),
        ("full market candidate symbols", str(info.get("full_market_candidate_symbols", "-"))),
        ("full market out_of_pool", str(info.get("full_market_out_of_pool", "-"))),
        ("strategy validation version", str(info.get("strategy_validation_version", "-"))),
        ("market data provider version", str(info.get("market_data_provider_version", "-"))),
        ("market data provider active", str(info.get("market_data_provider_active", "-"))),
        ("market data provider primary", str(info.get("market_data_provider_primary", "-"))),
        ("market data provider fallback", str(info.get("market_data_provider_fallback", "-"))),
        ("post-market verification rows", str(info.get("post_market_verification_rows", "-"))),
        ("post-market verification verified", str(info.get("post_market_verification_verified", "-"))),
        ("post-market verification missing intraday", str(info.get("post_market_verification_missing_intraday", "-"))),
        ("post-market verification message", str(info.get("post_market_verification_message", "-"))),
    ]
    items = "".join(f"<li><strong>{escape(label)}:</strong> {escape(value)}</li>" for label, value in rows)
    return f'<details class="debug-block"><summary>開發者資訊（系統版本 / Debug）</summary><ul>{items}</ul></details>'


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
        return "<table><tbody><tr><td>目前沒有足夠明確的方向偏多標的。</td></tr></tbody></table>"
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
            f"<td data-sort-value=\"{_sort_value(row.volume_ratio)}\">{escape(_safe_direction_text(row.opening_direction))}<br><span class=\"muted\">量比 {_fmt(row.volume_ratio)}x</span></td>"
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
        "<table class=\"sortable\"><thead><tr><th data-sort=\"number\">方向偏多</th>"
        "<th data-sort=\"text\">進場狀態</th><th data-sort=\"text\">標的</th><th data-sort=\"number\">現價</th><th data-sort=\"number\">漲跌</th>"
        "<th data-sort=\"text\">族群</th><th data-sort=\"number\">開盤</th><th data-sort=\"number\">VWAP</th>"
        "<th data-sort=\"number\">觸發</th><th data-sort=\"number\">停損</th><th data-sort=\"number\">目標</th>"
        "<th data-sort=\"number\">股數上限</th><th data-sort=\"number\">法人排行</th><th>偏多理由</th><th>取消條件</th></tr></thead><tbody>"
        + "".join(body)
        + "</tbody></table>"
    )


def _performance_table(summary: Optional[SignalPerformanceSummary]) -> str:
    if summary is None or summary.total == 0:
        return "<table><tbody><tr><td>尚未累積可評估的偏多訊號。系統更新後會自動建立紀錄。</td></tr></tbody></table>"
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
        "<th data-sort=\"number\">買多</th><th data-sort=\"number\">看空</th><th data-sort=\"number\">觀望</th>"
        "<th data-sort=\"number\">平均量比</th></tr></thead><tbody>"
        + ("".join(rows) or "<tr><td colspan=\"8\">無開盤族群資料。</td></tr>")
        + "</tbody></table>"
    )


def _direction(value: str) -> str:
    display = _safe_direction_text(value)
    class_name = "dir-long" if "多" in display else "dir-short" if "空" in display else ""
    return f'<span class="{class_name}">{escape(display)}</span>'


def _safe_direction_text(value: str) -> str:
    if value.startswith("做多") and value.endswith("確認"):
        return "偏多確認"
    return {
        "做多觀察": "方向偏多",
    }.get(value or "", value or "")


def _grade_label(value: str) -> str:
    return {
        "A": "強勢重點盯盤",
        "B+": "可練習觀察",
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


def _fmt_int(value) -> str:
    try:
        if value is None or value == "":
            return "-"
        return f"{float(value):,.0f}"
    except (TypeError, ValueError):
        return "-"


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
