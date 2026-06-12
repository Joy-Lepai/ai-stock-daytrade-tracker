from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from datetime import datetime, time
from pathlib import Path
from typing import Dict, Iterable, List, Optional, TYPE_CHECKING

from stock_daytrade_system.data import Bar

if TYPE_CHECKING:
    from stock_daytrade_system.tracker import TrackedSymbol


JSON_NAME = "paper-trades.json"
CSV_NAME = "paper-trades.csv"
INITIAL_CASH = 1_000_000.0
MARKET_OPEN = time(9, 0)
LAST_ENTRY_TIME = time(13, 20)


@dataclass(frozen=True)
class PaperTradingSummary:
    initial_cash: float
    open_count: int
    closed_count: int
    win_count: int
    loss_count: int
    realized_pnl: float
    unrealized_pnl: float
    total_pnl: float
    win_rate: float
    latest_trades: List[dict]


def update_paper_trades(
    report_time: datetime,
    rows: Iterable["TrackedSymbol"],
    intraday_data: Dict[str, List[Bar]],
    output_dir: Path,
) -> PaperTradingSummary:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / JSON_NAME
    trades = _load_trades(json_path)
    today = report_time.strftime("%Y-%m-%d")

    for trade in trades:
        _evaluate_trade(trade, intraday_data.get(trade.get("symbol", ""), []), report_time)

    active_symbols = {
        trade.get("symbol")
        for trade in trades
        if trade.get("status") == "持倉中"
    }
    if _can_open_new_trades(report_time):
        for row in rows:
            if not _should_open(row) or row.symbol in active_symbols:
                continue
            bars = intraday_data.get(row.symbol, [])
            if not _has_current_market_bar(bars, report_time):
                continue
            trade = _new_trade(today, report_time, row)
            _evaluate_trade(trade, bars, report_time)
            trades.append(trade)
            active_symbols.add(row.symbol)

    trades = sorted(trades, key=lambda item: (item.get("entry_time", ""), item.get("symbol", "")))
    json_path.write_text(json.dumps(trades, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_csv(output_dir / CSV_NAME, trades)
    return summarize_paper_trades(trades)


def summarize_paper_trades(trades: Iterable[dict]) -> PaperTradingSummary:
    items = list(trades)
    open_trades = [item for item in items if item.get("status") == "持倉中"]
    closed_trades = [item for item in items if item.get("status") == "已出場"]
    win_count = sum(1 for item in closed_trades if _as_float(item.get("realized_pnl")) and _as_float(item.get("realized_pnl")) > 0)
    loss_count = sum(1 for item in closed_trades if _as_float(item.get("realized_pnl")) and _as_float(item.get("realized_pnl")) < 0)
    realized_pnl = round(sum(_as_float(item.get("realized_pnl")) or 0 for item in closed_trades), 2)
    unrealized_pnl = round(sum(_as_float(item.get("unrealized_pnl")) or 0 for item in open_trades), 2)
    latest_trades = sorted(items, key=lambda item: item.get("entry_time", ""), reverse=True)[:8]
    return PaperTradingSummary(
        initial_cash=INITIAL_CASH,
        open_count=len(open_trades),
        closed_count=len(closed_trades),
        win_count=win_count,
        loss_count=loss_count,
        realized_pnl=realized_pnl,
        unrealized_pnl=unrealized_pnl,
        total_pnl=round(realized_pnl + unrealized_pnl, 2),
        win_rate=_rate(win_count, len(closed_trades)),
        latest_trades=latest_trades,
    )


def _should_open(row: "TrackedSymbol") -> bool:
    return (
        row.entry_status == "可進場"
        and row.candidate_direction == "做多觀察"
        and row.last_price is not None
        and row.stop_loss is not None
        and row.target_price is not None
        and (row.suggested_shares or 0) > 0
    )


def _new_trade(today: str, report_time: datetime, row: "TrackedSymbol") -> dict:
    entry_price = round(float(row.last_price or 0), 2)
    shares = int(row.suggested_shares or 0)
    return {
        "id": f"{today}|{row.symbol}|long",
        "date": today,
        "symbol": row.symbol,
        "name": row.name,
        "side": "做多",
        "status": "持倉中",
        "entry_time": report_time.isoformat(timespec="seconds"),
        "entry_status": row.entry_status,
        "entry_price": entry_price,
        "shares": shares,
        "stop_loss": row.stop_loss,
        "target_price": row.target_price,
        "latest_price": entry_price,
        "exit_time": "",
        "exit_price": "",
        "exit_reason": "",
        "realized_pnl": "",
        "unrealized_pnl": 0.0,
        "return_pct": 0.0,
        "max_favorable_pct": 0.0,
        "max_adverse_pct": 0.0,
        "last_evaluated_at": "",
    }


def _evaluate_trade(trade: dict, bars: List[Bar], report_time: datetime) -> None:
    entry_price = _as_float(trade.get("entry_price"))
    shares = int(_as_float(trade.get("shares")) or 0)
    if entry_price is None or entry_price <= 0 or shares <= 0:
        return
    relevant_bars = _bars_after_entry(trade, bars)
    if not relevant_bars:
        return

    latest = relevant_bars[-1]
    max_high = max(bar.high for bar in relevant_bars)
    min_low = min(bar.low for bar in relevant_bars)
    latest_price = round(latest.close, 2)
    trade["latest_price"] = latest_price
    trade["max_favorable_pct"] = round((max_high - entry_price) / entry_price * 100, 2)
    trade["max_adverse_pct"] = round((min_low - entry_price) / entry_price * 100, 2)
    trade["last_evaluated_at"] = report_time.isoformat(timespec="seconds")

    if trade.get("status") == "已出場":
        return

    stop_loss = _as_float(trade.get("stop_loss"))
    target_price = _as_float(trade.get("target_price"))
    for bar in relevant_bars:
        stop_hit = stop_loss is not None and bar.low <= stop_loss
        target_hit = target_price is not None and bar.high >= target_price
        if stop_hit:
            _close_trade(trade, bar.timestamp, stop_loss, "停損")
            return
        if target_hit:
            _close_trade(trade, bar.timestamp, target_price, "達標")
            return

    unrealized = (latest_price - entry_price) * shares
    trade["unrealized_pnl"] = round(unrealized, 2)
    trade["return_pct"] = round((latest_price - entry_price) / entry_price * 100, 2)


def _close_trade(trade: dict, exit_time: datetime, exit_price: float, reason: str) -> None:
    entry_price = _as_float(trade.get("entry_price")) or 0.0
    shares = int(_as_float(trade.get("shares")) or 0)
    pnl = (exit_price - entry_price) * shares
    trade["status"] = "已出場"
    trade["exit_time"] = exit_time.isoformat(timespec="seconds")
    trade["exit_price"] = round(exit_price, 2)
    trade["exit_reason"] = reason
    trade["realized_pnl"] = round(pnl, 2)
    trade["unrealized_pnl"] = 0.0
    trade["return_pct"] = round((exit_price - entry_price) / entry_price * 100, 2) if entry_price else 0.0


def _can_open_new_trades(report_time: datetime) -> bool:
    current = report_time.time()
    return MARKET_OPEN <= current <= LAST_ENTRY_TIME and report_time.weekday() < 5


def _has_current_market_bar(bars: List[Bar], report_time: datetime) -> bool:
    if not bars:
        return False
    latest = bars[-1].timestamp
    return latest.date() == report_time.replace(tzinfo=None).date()


def _bars_after_entry(trade: dict, bars: List[Bar]) -> List[Bar]:
    entry_time = _parse_datetime(trade.get("entry_time", ""))
    if entry_time is None:
        return bars
    entry_naive = entry_time.replace(tzinfo=None)
    return [
        bar for bar in bars
        if bar.timestamp.date() == entry_naive.date() and bar.timestamp >= entry_naive
    ]


def _load_trades(path: Path) -> List[dict]:
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return payload if isinstance(payload, list) else []


def _write_csv(path: Path, trades: List[dict]) -> None:
    fields = [
        "date",
        "symbol",
        "name",
        "side",
        "status",
        "entry_time",
        "entry_status",
        "entry_price",
        "shares",
        "stop_loss",
        "target_price",
        "latest_price",
        "exit_time",
        "exit_price",
        "exit_reason",
        "realized_pnl",
        "unrealized_pnl",
        "return_pct",
        "max_favorable_pct",
        "max_adverse_pct",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for trade in trades:
            writer.writerow({field: trade.get(field, "") for field in fields})


def _rate(count: int, total: int) -> float:
    if total <= 0:
        return 0.0
    return round(count / total * 100, 2)


def _as_float(value) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_datetime(value: str) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None
