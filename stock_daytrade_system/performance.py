from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional, TYPE_CHECKING

from stock_daytrade_system.data import Bar

if TYPE_CHECKING:
    from stock_daytrade_system.tracker import TrackedSymbol


ACTIONABLE_ENTRY_STATUSES = {"可進場", "等突破", "等VWAP轉強", "等轉強", "量不足"}
JSON_NAME = "signal-performance.json"
CSV_NAME = "signal-performance.csv"


@dataclass(frozen=True)
class StatusPerformance:
    status: str
    total: int
    target_count: int
    stop_count: int
    target_rate: float
    avg_max_favorable_pct: float
    avg_max_adverse_pct: float


@dataclass(frozen=True)
class SignalPerformanceSummary:
    total: int
    today_count: int
    target_count: int
    stop_count: int
    active_count: int
    target_rate: float
    avg_max_favorable_pct: float
    avg_max_adverse_pct: float
    by_status: List[StatusPerformance]


def record_signal_performance(
    report_time: datetime,
    rows: Iterable["TrackedSymbol"],
    intraday_data: Dict[str, List[Bar]],
    output_dir: Path,
) -> SignalPerformanceSummary:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / JSON_NAME
    records = _load_records(json_path)
    record_map = {record["id"]: record for record in records}
    today = report_time.strftime("%Y-%m-%d")

    for row in rows:
        if not _should_track(row):
            continue
        record_id = f"{today}|{row.symbol}"
        if record_id not in record_map:
            record_map[record_id] = _new_record(record_id, today, report_time, row)
        else:
            _update_record(record_map[record_id], report_time, row)
        _evaluate_record(record_map[record_id], intraday_data.get(row.symbol, []), report_time)

    records = sorted(record_map.values(), key=lambda item: (item["date"], item["symbol"]))
    json_path.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_csv(output_dir / CSV_NAME, records)
    return summarize_signal_performance(records, today)


def summarize_signal_performance(records: Iterable[dict], today: Optional[str] = None) -> SignalPerformanceSummary:
    items = list(records)
    completed = [item for item in items if item.get("outcome") in {"達標", "停損"}]
    target_count = sum(1 for item in items if item.get("outcome") == "達標")
    stop_count = sum(1 for item in items if item.get("outcome") == "停損")
    active_count = max(len(items) - target_count - stop_count, 0)
    today_count = sum(1 for item in items if today and item.get("date") == today)
    by_status = [_summarize_status(status, group) for status, group in _group_by_status(items).items()]
    by_status.sort(key=lambda item: (-item.total, item.status))
    return SignalPerformanceSummary(
        total=len(items),
        today_count=today_count,
        target_count=target_count,
        stop_count=stop_count,
        active_count=active_count,
        target_rate=_rate(target_count, len(completed)),
        avg_max_favorable_pct=_avg_number(items, "max_favorable_pct"),
        avg_max_adverse_pct=_avg_number(items, "max_adverse_pct"),
        by_status=by_status,
    )


def _should_track(row: "TrackedSymbol") -> bool:
    return (
        row.candidate_direction == "做多觀察"
        and row.bullish_score >= 5
        and (row.suggested_shares or 0) > 0
        and row.entry_status in ACTIONABLE_ENTRY_STATUSES
    )


def _new_record(record_id: str, today: str, report_time: datetime, row: "TrackedSymbol") -> dict:
    return {
        "id": record_id,
        "date": today,
        "symbol": row.symbol,
        "name": row.name,
        "source": row.source,
        "first_seen_at": report_time.isoformat(timespec="seconds"),
        "latest_seen_at": report_time.isoformat(timespec="seconds"),
        "first_entry_status": row.entry_status,
        "latest_entry_status": row.entry_status,
        "bullish_label": row.bullish_label,
        "bullish_score": row.bullish_score,
        "signal_price": row.last_price,
        "latest_price": row.last_price,
        "trigger_price": row.trigger_price,
        "stop_loss": row.stop_loss,
        "target_price": row.target_price,
        "vwap": row.vwap,
        "volume_ratio": row.volume_ratio,
        "suggested_shares": row.suggested_shares,
        "outcome": "追蹤中",
        "hit_target_at": "",
        "hit_stop_at": "",
        "max_favorable_pct": None,
        "max_adverse_pct": None,
        "last_evaluated_at": "",
    }


def _update_record(record: dict, report_time: datetime, row: "TrackedSymbol") -> None:
    record["latest_seen_at"] = report_time.isoformat(timespec="seconds")
    record["latest_entry_status"] = row.entry_status
    record["bullish_label"] = row.bullish_label
    record["bullish_score"] = row.bullish_score
    record["latest_price"] = row.last_price
    record["trigger_price"] = row.trigger_price
    record["stop_loss"] = row.stop_loss
    record["target_price"] = row.target_price
    record["vwap"] = row.vwap
    record["volume_ratio"] = row.volume_ratio
    record["suggested_shares"] = row.suggested_shares


def _evaluate_record(record: dict, bars: List[Bar], report_time: datetime) -> None:
    signal_price = _as_float(record.get("signal_price"))
    if signal_price is None or signal_price <= 0:
        return
    relevant_bars = _bars_after_first_seen(record, bars)
    if not relevant_bars:
        return

    max_high = max(bar.high for bar in relevant_bars)
    min_low = min(bar.low for bar in relevant_bars)
    record["latest_price"] = round(relevant_bars[-1].close, 2)
    record["max_favorable_pct"] = round((max_high - signal_price) / signal_price * 100, 2)
    record["max_adverse_pct"] = round((min_low - signal_price) / signal_price * 100, 2)
    record["last_evaluated_at"] = report_time.isoformat(timespec="seconds")

    if record.get("outcome") in {"達標", "停損"}:
        return

    target_price = _as_float(record.get("target_price"))
    stop_loss = _as_float(record.get("stop_loss"))
    for bar in relevant_bars:
        target_hit = target_price is not None and bar.high >= target_price
        stop_hit = stop_loss is not None and bar.low <= stop_loss
        if target_hit and stop_hit:
            record["outcome"] = "同K不明"
            record["hit_target_at"] = bar.timestamp.isoformat(timespec="seconds")
            record["hit_stop_at"] = bar.timestamp.isoformat(timespec="seconds")
            return
        if target_hit:
            record["outcome"] = "達標"
            record["hit_target_at"] = bar.timestamp.isoformat(timespec="seconds")
            return
        if stop_hit:
            record["outcome"] = "停損"
            record["hit_stop_at"] = bar.timestamp.isoformat(timespec="seconds")
            return


def _bars_after_first_seen(record: dict, bars: List[Bar]) -> List[Bar]:
    if not bars:
        return []
    first_seen = _parse_datetime(record.get("first_seen_at", ""))
    if first_seen is None:
        return bars
    first_seen_naive = first_seen.replace(tzinfo=None)
    return [
        bar for bar in bars
        if bar.timestamp.date() == first_seen_naive.date() and bar.timestamp >= first_seen_naive
    ]


def _load_records(path: Path) -> List[dict]:
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    return payload if isinstance(payload, list) else []


def _write_csv(path: Path, records: List[dict]) -> None:
    fields = [
        "date",
        "symbol",
        "name",
        "source",
        "first_seen_at",
        "latest_seen_at",
        "first_entry_status",
        "latest_entry_status",
        "bullish_score",
        "signal_price",
        "latest_price",
        "trigger_price",
        "stop_loss",
        "target_price",
        "vwap",
        "volume_ratio",
        "suggested_shares",
        "outcome",
        "max_favorable_pct",
        "max_adverse_pct",
        "hit_target_at",
        "hit_stop_at",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for record in records:
            writer.writerow({field: record.get(field, "") for field in fields})


def _group_by_status(records: List[dict]) -> Dict[str, List[dict]]:
    grouped: Dict[str, List[dict]] = {}
    for record in records:
        status = record.get("first_entry_status") or "未分類"
        grouped.setdefault(status, []).append(record)
    return grouped


def _summarize_status(status: str, records: List[dict]) -> StatusPerformance:
    completed = [item for item in records if item.get("outcome") in {"達標", "停損"}]
    target_count = sum(1 for item in records if item.get("outcome") == "達標")
    stop_count = sum(1 for item in records if item.get("outcome") == "停損")
    return StatusPerformance(
        status=status,
        total=len(records),
        target_count=target_count,
        stop_count=stop_count,
        target_rate=_rate(target_count, len(completed)),
        avg_max_favorable_pct=_avg_number(records, "max_favorable_pct"),
        avg_max_adverse_pct=_avg_number(records, "max_adverse_pct"),
    )


def _rate(count: int, total: int) -> float:
    if total <= 0:
        return 0.0
    return round(count / total * 100, 2)


def _avg_number(records: List[dict], key: str) -> float:
    values = [_as_float(item.get(key)) for item in records]
    numbers = [value for value in values if value is not None]
    if not numbers:
        return 0.0
    return round(sum(numbers) / len(numbers), 2)


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
