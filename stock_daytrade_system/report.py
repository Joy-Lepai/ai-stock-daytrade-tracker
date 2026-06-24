from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Iterable, List

from stock_daytrade_system.intraday import OpeningSignal
from stock_daytrade_system.labels import sector_label, stock_label
from stock_daytrade_system.scoring import CandidateScore, MarketBias
from stock_daytrade_system.sectors import SectorOpeningStrength, SectorStrength


def render_report(
    report_time: datetime,
    market_bias: MarketBias,
    sector_strengths: Iterable[SectorStrength],
    long_candidates: Iterable[CandidateScore],
    short_candidates: Iterable[CandidateScore],
    output_path: Path,
    data_warnings: Iterable[str] = (),
) -> Path:
    long_list = list(long_candidates)
    short_list = list(short_candidates)
    sector_list = list(sector_strengths)
    warning_list = list(data_warnings)
    lines: List[str] = [
        f"# 盤前當沖研究報告 - {report_time.strftime('%Y-%m-%d')}",
        "",
        f"- 產生時間：{report_time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"- 市場背景：{market_bias.direction}（分數 {market_bias.score:+.2f}）",
        "- 用途：研究輔助，不是投資建議；開盤後仍需用即時價格與成交量確認。",
        "",
        "## 市場摘要",
        "",
    ]
    if warning_list:
        lines.extend(["### 資料警告", ""])
        lines.extend(f"- {warning}" for warning in warning_list)
        lines.append("")
    lines.extend(f"- {note}" for note in market_bias.notes[:12])
    lines.extend(["", "## 族群強弱", ""])
    lines.extend(_render_sector_table(sector_list))
    lines.extend(["", "## 方向偏多觀察", ""])
    lines.extend(_render_table(long_list))
    lines.extend(["", "## 做空觀察", ""])
    lines.extend(_render_table(short_list))
    lines.extend(
        [
            "",
            "## 開盤後確認清單",
            "",
            "- 只交易開盤後成交量明顯放大的標的。",
            "- 做多優先觀察是否突破前日高點或開盤區間高點。",
            "- 做空優先觀察是否跌破前日低點或開盤區間低點。",
            "- 若大盤與台指期方向和候選股方向相反，降低部位或跳過。",
            "- 單筆交易先定義停損，不因評分高而放大風險。",
        ]
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return output_path


def _render_table(candidates: List[CandidateScore]) -> List[str]:
    if not candidates:
        return ["目前沒有符合條件的標的。"]

    lines = [
        "| 標的 | 方向 | 分數 | 收盤 | 觸發 | 停損 | 目標 | 每股風險 | 股數上限 | 理由 |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for item in candidates:
        reasons = "；".join(item.reasons[:4])
        lines.append(
            "| "
            f"{stock_label(item.name, item.symbol)} | {item.direction} | {item.score:.2f} | "
            f"{item.close:.2f} | {item.trigger_price:.2f} | {item.stop_loss:.2f} | "
            f"{item.target_price:.2f} | {item.risk_per_share:.2f} | "
            f"{item.suggested_shares} | {reasons} |"
        )
    return lines


def _render_sector_table(sectors: List[SectorStrength]) -> List[str]:
    if not sectors:
        return ["目前沒有足夠資料計算族群強弱。"]

    lines = [
        "| 族群 | 狀態 | 分數 | 檔數 | 1日均漲跌 | 5日均漲跌 | 相對大盤 | 上漲/下跌檔數 |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for item in sectors:
        lines.append(
            "| "
            f"{sector_label(item.sector)} | {item.direction} | {item.score:+.2f} | {item.member_count} | "
            f"{item.avg_one_day_return:+.2f}% | {item.avg_five_day_return:+.2f}% | "
            f"{item.avg_relative_strength:+.2f}% | {item.bullish_count}/{item.bearish_count} |"
        )
    return lines


def render_opening_report(
    report_time: datetime,
    sector_strengths: Iterable[SectorOpeningStrength],
    signals: Iterable[OpeningSignal],
    output_path: Path,
    data_warnings: Iterable[str] = (),
) -> Path:
    signal_list = sorted(list(signals), key=lambda item: item.score, reverse=True)
    sector_list = list(sector_strengths)
    warning_list = list(data_warnings)
    lines: List[str] = [
        f"# 開盤確認報告 - {report_time.strftime('%Y-%m-%d')}",
        "",
        f"- 產生時間：{report_time.strftime('%Y-%m-%d %H:%M:%S')}",
        "- 用途：確認盤前候選股是否真的放量突破/跌破開盤區間，不是投資建議。",
        "",
    ]
    if warning_list:
        lines.extend(["## 資料警告", ""])
        lines.extend(f"- {warning}" for warning in warning_list)
        lines.append("")

    lines.extend(["## 族群開盤強弱", ""])
    lines.extend(_render_opening_sector_table(sector_list))
    lines.append("")

    actionable = [item for item in signal_list if item.direction != "觀望"]
    watch = [item for item in signal_list if item.direction == "觀望"]
    lines.extend(["## 可行動訊號", ""])
    lines.extend(_render_opening_table(actionable))
    lines.extend(["", "## 觀望名單", ""])
    lines.extend(_render_opening_table(watch))
    lines.extend(
        [
            "",
            "## 執行提醒",
            "",
            "- 做多只追突破後能守住開盤區間高點的標的。",
            "- 做空只追跌破後反彈不回開盤區間低點的標的。",
            "- 若量比不足或價差過大，跳過比硬做更重要。",
        ]
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return output_path


def _render_opening_sector_table(sectors: List[SectorOpeningStrength]) -> List[str]:
    if not sectors:
        return ["目前沒有足夠資料計算族群開盤強弱。"]

    lines = [
        "| 族群 | 狀態 | 分數 | 檔數 | 偏多確認 | 做空確認 | 觀望 | 平均量比 |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for item in sectors:
        lines.append(
            "| "
            f"{sector_label(item.sector)} | {item.direction} | {item.score:+.2f} | {item.member_count} | "
            f"{item.confirmed_long_count} | {item.confirmed_short_count} | "
            f"{item.watch_count} | {item.avg_volume_ratio:.2f}x |"
        )
    return lines


def _render_opening_table(signals: List[OpeningSignal]) -> List[str]:
    if not signals:
        return ["目前沒有符合條件的標的。"]

    lines = [
        "| 標的 | 訊號 | 分數 | 現價 | 開盤區間高 | 開盤區間低 | 累積量 | 量比 | 理由 |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for item in signals:
        reasons = "；".join(item.reasons[:4])
        lines.append(
            "| "
            f"{stock_label(item.name, item.symbol)} | {item.direction} | {item.score:.2f} | "
            f"{item.last_price:.2f} | {item.opening_range_high:.2f} | "
            f"{item.opening_range_low:.2f} | {item.cumulative_volume:.0f} | "
            f"{item.volume_ratio:.2f}x | {reasons} |"
        )
    return lines
