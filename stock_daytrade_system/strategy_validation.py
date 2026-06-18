from __future__ import annotations

import sqlite3
from collections import Counter, defaultdict
from datetime import datetime
from typing import Iterable

from stock_daytrade_system.data import Bar


STRATEGY_VALIDATION_VERSION = "strategy_validation_v2_missed_seen_regret_2026-06-18"
GRADES = ("A", "B+", "B", "high_risk", "avoid", "wait_volume", "wait_vwap", "wait_breakout", "data_missing")
FILTERED_STATUSES = {"high_risk", "avoid", "wait_volume", "wait_vwap", "wait_breakout", "wait_pullback", "practice_long"}


def update_tw_scan_result_verification(
    conn: sqlite3.Connection,
    captured_at: datetime,
    intraday_bars_by_symbol: dict[str, list[Bar]],
) -> dict:
    captured_text = captured_at.isoformat(timespec="seconds")
    rows = conn.execute(
        """
        SELECT *
        FROM tw_full_market_snapshots
        WHERE captured_at = ?
        """,
        (captured_text,),
    ).fetchall()
    updated = 0
    missing = 0
    with conn:
        for row in rows:
            bars = intraday_bars_by_symbol.get(row["symbol"], [])
            result = _verify_row(row, captured_at, bars)
            if not result:
                missing += 1
                continue
            updated += 1
            conn.execute(
                """
                UPDATE tw_full_market_snapshots
                SET post_scan_high = ?,
                    post_scan_low = ?,
                    post_scan_close = ?,
                    max_gain_after_scan = ?,
                    max_drawdown_after_scan = ?,
                    hit_stop_loss = ?,
                    hit_take_profit = ?,
                    hit_0_5_pct = ?,
                    hit_1_pct = ?,
                    hit_2_pct = ?,
                    first_touch = ?,
                    verification_outcome = ?,
                    verified_at = ?
                WHERE captured_at = ? AND symbol = ?
                """,
                (
                    result["post_scan_high"],
                    result["post_scan_low"],
                    result["post_scan_close"],
                    result["max_gain_after_scan"],
                    result["max_drawdown_after_scan"],
                    int(result["hit_stop_loss"]),
                    int(result["hit_take_profit"]),
                    int(result["hit_0_5_pct"]),
                    int(result["hit_1_pct"]),
                    int(result["hit_2_pct"]),
                    result["first_touch"],
                    result["verification_outcome"],
                    captured_text,
                    row["captured_at"],
                    row["symbol"],
                ),
            )
    return {
        "version": STRATEGY_VALIDATION_VERSION,
        "captured_at": captured_text,
        "rows": len(rows),
        "verified": updated,
        "missing_intraday": missing,
        "message": "已補上盤後結果驗證。" if updated else "目前尚無可驗證的盤中資料。",
    }


def build_strategy_scorecard(conn: sqlite3.Connection, windows: Iterable[int] = (20, 40, 60)) -> dict:
    rows = _latest_snapshot_rows(conn)
    dates = sorted({row["date"] for row in rows}, reverse=True)
    payload = {
        "version": STRATEGY_VALIDATION_VERSION,
        "available_trade_days": len(dates),
        "windows": {},
    }
    for window in windows:
        selected_dates = set(dates[:window])
        window_rows = [row for row in rows if row["date"] in selected_dates]
        payload["windows"][str(window)] = _window_scorecard(window_rows, window)
    return payload


def build_missed_rate_report(conn: sqlite3.Connection, window: int = 20) -> dict:
    rows = _latest_snapshot_rows(conn)
    dates = sorted({row["date"] for row in rows}, reverse=True)
    selected_dates = set(dates[:window])
    selected = [row for row in rows if row["date"] in selected_dates]
    strong = [row for row in selected if _is_true_strength(row)]
    selected_rows = [row for row in strong if row["ai_grade"] in {"A", "B+", "B"}]
    seen_filtered = [row for row in strong if _is_seen_but_filtered(row)]
    missed_by_pool = [row for row in strong if _is_missed_by_pool(row)]
    regret_rows = [row for row in seen_filtered if _is_regret_after_close(row)]
    reason_counts = Counter(_missed_reason(row) for row in missed_by_pool)
    filtered_counts = Counter(_filter_status(row) for row in seen_filtered)
    return {
        "version": STRATEGY_VALIDATION_VERSION,
        "window_days": window,
        "strong_stock_count": len(strong),
        "selected_count": len(selected_rows),
        "system_seen_count": len(selected_rows) + len(seen_filtered),
        "not_in_ab_count": max(len(strong) - len(selected_rows), 0),
        "not_in_ab_rate": round((len(strong) - len(selected_rows)) / len(strong) * 100, 2) if strong else 0.0,
        "seen_but_filtered_count": len(seen_filtered),
        "seen_but_filtered_rate": round(len(seen_filtered) / len(strong) * 100, 2) if strong else 0.0,
        "missed_by_pool_count": len(missed_by_pool),
        "missed_by_pool_rate": round(len(missed_by_pool) / len(strong) * 100, 2) if strong else 0.0,
        "missed_count": len(missed_by_pool),
        "missed_rate": round(len(missed_by_pool) / len(strong) * 100, 2) if strong else 0.0,
        "reason_counts": dict(reason_counts),
        "seen_but_filtered": {
            "count": len(seen_filtered),
            "by_status": dict(filtered_counts),
            "examples": [_filtered_example(row) for row in seen_filtered[:30]],
        },
        "missed_by_pool": {
            "count": len(missed_by_pool),
            "rate": round(len(missed_by_pool) / len(strong) * 100, 2) if strong else 0.0,
            "examples": [_missed_example(row) for row in missed_by_pool[:20]],
            "reason_counts": dict(reason_counts),
        },
        "regret_after_close": {
            "count": len(regret_rows),
            "rate": round(len(regret_rows) / len(seen_filtered) * 100, 2) if seen_filtered else 0.0,
            "examples": [_regret_example(row) for row in regret_rows[:20]],
            "message": "需累積盤後驗證資料；此數字只統計已補上訊號後高低點的樣本。",
        },
        "missed_examples": [_missed_example(row) for row in missed_by_pool[:20]],
        "message": "真漏抓率只計算完全沒有被掃描池或模型看到的強勢股；已看到但被 high_risk / avoid / wait 擋下者另列。",
    }


def build_model_observations(scorecard: dict, missed_report: dict) -> list[str]:
    window = (scorecard.get("windows") or {}).get("20") or {}
    rows = window.get("groups") or {}
    notes: list[str] = []
    high_risk = rows.get("high_risk") or {}
    b_plus = rows.get("B+") or {}
    grade_a = rows.get("A") or {}
    data_missing = rows.get("data_missing") or {}
    avoid = rows.get("avoid") or {}

    if high_risk.get("sample_size", 0) >= 10 and high_risk.get("continue_up_rate", 0) >= 35:
        notes.append("可能過度保守：high_risk 後續仍有較高比例繼續上漲，建議觀察 high_risk_watch 分層，但不要直接升級 A。")
    if b_plus.get("sample_size", 0) >= 10 and b_plus.get("trigger_rate", 0) < 25:
        notes.append("B+ 觸發率偏低，建議檢查 triggered 條件是否過嚴，先觀察不要放寬 A 級。")
    if grade_a.get("sample_size", 0) < 5 and grade_a.get("win_rate", 0) >= 60:
        notes.append("A 級可能準但太少，建議用 B+ 繼續累積樣本，不建議調低 A 級門檻。")
    if avoid.get("sample_size", 0) >= 10 and avoid.get("false_negative_rate", 0) >= 20:
        notes.append("avoid 可能過度保守：部分避開標的後續仍大漲，建議檢查排除條件是否太早觸發。")
    if data_missing.get("sample_size", 0) or missed_report.get("reason_counts", {}).get("data_missing", 0):
        notes.append("資料源造成無法判斷：請優先修資料缺漏，不建議用降低模型門檻解決。")
    if missed_report.get("missed_by_pool_rate", 0) >= 20 and missed_report.get("strong_stock_count", 0) >= 20:
        notes.append("真漏抓率偏高，建議先檢查掃描池與資料源穩定性，不要直接調低 A / B+ / B 條件。")
    if (missed_report.get("regret_after_close") or {}).get("rate", 0) >= 30:
        notes.append("盤後可惜漏掉率偏高，建議先觀察 high_risk / wait 類別，不要直接升級為推薦。")
    if not notes:
        notes.append("目前樣本仍需累積；建議觀察 20 日後再調整，暫不建議修改模型條件。")
    return notes


def _verify_row(row: sqlite3.Row, captured_at: datetime, bars: list[Bar]) -> dict | None:
    signal_price = _float(row["signal_price"] if "signal_price" in row.keys() else None) or _float(row["price"])
    if not bars or not signal_price or signal_price <= 0:
        return None
    signal_time = _parse_dt(row["signal_at"] if "signal_at" in row.keys() else None) or captured_at
    signal_naive = signal_time.replace(tzinfo=None)
    same_day = [bar for bar in bars if bar.timestamp.date() == signal_naive.date()]
    if not same_day:
        return None
    relevant = [bar for bar in same_day if bar.timestamp >= signal_naive]
    if not relevant:
        relevant = same_day
    high = max(bar.high for bar in relevant)
    low = min(bar.low for bar in relevant)
    close = relevant[-1].close
    max_gain = (high - signal_price) / signal_price * 100
    max_drawdown = (low - signal_price) / signal_price * 100
    stop_loss = _float(row["signal_vwap"] if "signal_vwap" in row.keys() else None)
    if stop_loss is None or stop_loss >= signal_price:
        stop_loss = signal_price * 0.99
    take_profit = signal_price * 1.02
    first_touch = _first_touch(relevant, stop_loss, take_profit)
    if max_gain >= 2:
        outcome = "達到2%目標"
    elif max_gain >= 1:
        outcome = "達到1%目標"
    elif max_gain >= 0.5:
        outcome = "達到0.5%目標"
    elif max_drawdown <= -1:
        outcome = "回撤超過1%"
    else:
        outcome = "盤整或尚未明確"
    return {
        "post_scan_high": round(high, 2),
        "post_scan_low": round(low, 2),
        "post_scan_close": round(close, 2),
        "max_gain_after_scan": round(max_gain, 2),
        "max_drawdown_after_scan": round(max_drawdown, 2),
        "hit_stop_loss": low <= stop_loss,
        "hit_take_profit": high >= take_profit,
        "hit_0_5_pct": max_gain >= 0.5,
        "hit_1_pct": max_gain >= 1,
        "hit_2_pct": max_gain >= 2,
        "first_touch": first_touch,
        "verification_outcome": outcome,
    }


def _window_scorecard(rows: list[sqlite3.Row], window: int) -> dict:
    grouped: dict[str, list[sqlite3.Row]] = defaultdict(list)
    for row in rows:
        grouped[_group_name(row)].append(row)
    groups = {name: _group_stats(items) for name, items in grouped.items()}
    for name in GRADES:
        groups.setdefault(name, _group_stats([]))
    return {
        "window_days": window,
        "sample_size": len(rows),
        "groups": groups,
        "sample_message": "樣本不足，先累積資料。" if len(rows) < 20 else "已有初步樣本，可觀察模型方向。",
    }


def _group_stats(rows: list[sqlite3.Row]) -> dict:
    total = len(rows)
    verified = [row for row in rows if row["max_gain_after_scan"] is not None]
    wins = [row for row in verified if _float(row["max_gain_after_scan"]) >= 1]
    triggered = [
        row for row in rows
        if row["entry_status"] in {"executable", "practice_long"}
        or row["ai_grade"] == "A"
        or (_float(row["max_gain_after_scan"]) or 0) >= 0.5
    ]
    big_up = [row for row in verified if (_float(row["max_gain_after_scan"]) or 0) >= 2]
    pullback = [row for row in verified if (_float(row["max_drawdown_after_scan"]) or 0) <= -1]
    stop = [row for row in verified if bool(row["hit_stop_loss"])]
    target = [row for row in verified if bool(row["hit_take_profit"]) or bool(row["hit_2_pct"])]
    avg_gain = _avg(_float(row["max_gain_after_scan"]) for row in verified)
    avg_drawdown = _avg(_float(row["max_drawdown_after_scan"]) for row in verified)
    avg_return = _avg(((_float(row["post_scan_close"]) or 0) - (_float(row["signal_price"]) or _float(row["price"]) or 0)) / (_float(row["signal_price"]) or _float(row["price"]) or 1) * 100 for row in verified)
    return {
        "sample_size": total,
        "verified": len(verified),
        "triggered": len(triggered),
        "trigger_rate": round(len(triggered) / total * 100, 2) if total else 0.0,
        "win_rate": round(len(wins) / len(verified) * 100, 2) if verified else 0.0,
        "avg_max_gain": avg_gain,
        "avg_max_drawdown": avg_drawdown,
        "avg_return": avg_return,
        "stop_rate": round(len(stop) / len(verified) * 100, 2) if verified else 0.0,
        "target_rate": round(len(target) / len(verified) * 100, 2) if verified else 0.0,
        "reward_risk_ratio": round(abs(avg_gain / avg_drawdown), 2) if avg_drawdown else 0.0,
        "continue_up_rate": round(len(big_up) / len(verified) * 100, 2) if verified else 0.0,
        "pullback_rate": round(len(pullback) / len(verified) * 100, 2) if verified else 0.0,
        "false_negative_rate": round(len(big_up) / len(verified) * 100, 2) if verified else 0.0,
    }


def _latest_snapshot_rows(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT s.*
        FROM tw_full_market_snapshots s
        JOIN (
          SELECT date, symbol, MAX(captured_at) AS captured_at
          FROM tw_full_market_snapshots
          GROUP BY date, symbol
        ) latest
          ON latest.date = s.date
         AND latest.symbol = s.symbol
         AND latest.captured_at = s.captured_at
        ORDER BY s.date DESC, s.symbol
        """
    ).fetchall()


def _group_name(row: sqlite3.Row) -> str:
    if row["data_status"] == "data_missing" or row["reason_code"] in {"data_missing", "data_failed"}:
        return "data_missing"
    if row["ai_grade"] in {"A", "B+", "B"}:
        return row["ai_grade"]
    if row["entry_status"] == "high_risk":
        return "high_risk"
    if row["entry_status"] == "avoid":
        return "avoid"
    if row["entry_status"] in {"wait_volume", "wait_vwap", "wait_breakout"}:
        return row["entry_status"]
    return row["ai_grade"] or row["entry_status"] or "data_missing"


def _is_true_strength(row: sqlite3.Row) -> bool:
    change_pct = _float(row["change_pct"]) or 0
    turnover = _float(row["turnover"]) or 0
    volume = _float(row["volume"]) or 0
    return change_pct > 3 and (turnover >= 100_000_000 or volume >= 3_000_000)


def _missed_reason(row: sqlite3.Row) -> str:
    if row["data_status"] == "data_missing":
        return "data_missing"
    if (_float(row["turnover"]) or 0) < 10_000_000 or (_float(row["volume"]) or 0) < 100_000:
        return "liquidity_filter_removed"
    if row["volume_ratio"] is None:
        return "yahoo_intraday_failed"
    if row["vwap"] is None:
        return "yahoo_intraday_failed"
    if not row["entered_candidate_pool"]:
        return "below_candidate_threshold"
    return row["reason_code"] or "unknown"


def _is_seen_but_filtered(row: sqlite3.Row) -> bool:
    if row["ai_grade"] in {"A", "B+", "B"}:
        return False
    if row["entry_status"] in FILTERED_STATUSES:
        return True
    if row["entered_candidate_pool"] or row["entered_ai_candidates"]:
        return True
    return False


def _is_missed_by_pool(row: sqlite3.Row) -> bool:
    if row["ai_grade"] in {"A", "B+", "B"}:
        return False
    if _is_seen_but_filtered(row):
        return False
    return True


def _filter_status(row: sqlite3.Row) -> str:
    if row["data_status"] == "data_missing" or row["reason_code"] in {"data_missing", "data_failed"}:
        return "data_missing"
    if row["entry_status"] in {"high_risk", "avoid", "wait_volume", "wait_vwap", "wait_breakout", "wait_pullback", "practice_long"}:
        return row["entry_status"]
    return row["reason_code"] or row["entry_status"] or "filtered"


def _is_regret_after_close(row: sqlite3.Row) -> bool:
    gain = _float(row["max_gain_after_scan"])
    if gain is None:
        return False
    return _is_seen_but_filtered(row) and gain >= 1


def _missed_example(row: sqlite3.Row) -> dict:
    return {
        "date": row["date"],
        "symbol": row["symbol"],
        "name": row["name"],
        "change_pct": row["change_pct"],
        "turnover": row["turnover"],
        "volume": row["volume"],
        "reason_code": _missed_reason(row),
    }


def _filtered_example(row: sqlite3.Row) -> dict:
    return {
        "date": row["date"],
        "symbol": row["symbol"],
        "name": row["name"],
        "change_pct": row["change_pct"],
        "entry_status": row["entry_status"],
        "ai_grade": row["ai_grade"],
        "reason_code": row["reason_code"],
        "max_gain_after_scan": row["max_gain_after_scan"],
    }


def _regret_example(row: sqlite3.Row) -> dict:
    return {
        **_filtered_example(row),
        "max_drawdown_after_scan": row["max_drawdown_after_scan"],
        "verification_outcome": row["verification_outcome"],
    }


def _first_touch(bars: list[Bar], stop_loss: float, take_profit: float) -> str:
    for bar in bars:
        stop = bar.low <= stop_loss
        target = bar.high >= take_profit
        if stop and target:
            return "same_bar_unknown"
        if stop:
            return "stop_loss"
        if target:
            return "take_profit"
    return "none"


def _parse_dt(value) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def _float(value) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _avg(values: Iterable[float | None]) -> float:
    nums = [float(value) for value in values if value is not None]
    return round(sum(nums) / len(nums), 2) if nums else 0.0
