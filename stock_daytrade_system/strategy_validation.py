from __future__ import annotations

import sqlite3
from collections import Counter, defaultdict
from datetime import datetime
from typing import Iterable

from stock_daytrade_system.data import Bar
from stock_daytrade_system.timeframe_diagnostics import trend_continuation_validation


STRATEGY_VALIDATION_VERSION = "strategy_validation_v2_missed_seen_regret_2026-06-18"
GRADES = ("A", "B+", "B", "high_risk", "avoid", "wait_volume", "wait_vwap", "wait_breakout", "data_missing")
FILTERED_STATUSES = {"high_risk", "avoid", "wait_volume", "wait_vwap", "wait_breakout", "wait_pullback", "practice_long"}
MIN_MEANINGFUL_SAMPLE_SIZE = 20
EARLY_SAMPLE_SIZE = 60
TRUSTED_SAMPLE_SIZE = 100


BLOCKER_LABELS = {
    "data_not_live": "資料非即時",
    "cached_price": "使用上一筆",
    "delayed_price": "資料延遲",
    "data_missing": "資料不足",
    "data_failed": "資料擷取失敗",
    "yahoo_intraday_failed": "盤中資料失敗",
    "missing_price": "缺價格",
    "missing_vwap": "缺 VWAP",
    "vwap_missing": "缺 VWAP",
    "missing_volume_ratio": "缺量比",
    "volume_ratio_missing": "缺量比",
    "below_vwap": "未站上 VWAP",
    "wait_vwap": "等待站回 VWAP",
    "low_volume_ratio": "量比不足",
    "wait_volume": "等待量能",
    "no_breakout": "尚未突破",
    "wait_breakout": "等待突破",
    "failed_breakout": "突破失敗",
    "high_risk": "追價風險高",
    "risk_high": "追價風險高",
    "high_chase_risk": "追價風險高",
    "too_far_from_vwap": "距離 VWAP 過遠",
    "wait_pullback": "等待拉回",
    "avoid": "避開",
    "low_liquidity": "流動性不足",
    "below_candidate_threshold": "低於異動門檻",
    "not_in_watchlist": "原追蹤池未包含",
    "full_market_detected": "全市場掃描找到",
    "candidate_but_not_triggered": "候選但未觸發",
    "missing_tick": "缺逐筆成交資料",
    "missing_orderbook": "缺五檔資料",
    "no_large_buy": "尚未確認大單敲進",
}


def update_tw_scan_result_verification(
    conn: sqlite3.Connection,
    captured_at: datetime,
    intraday_bars_by_symbol: dict[str, list[Bar]],
) -> dict:
    captured_text = captured_at.isoformat(timespec="seconds")
    rows, selection_mode = _verification_rows(conn, captured_at)
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
        "selection_mode": selection_mode,
        "rows": len(rows),
        "verified": updated,
        "missing_intraday": missing,
        "message": "已補上盤後結果驗證。" if updated else "目前尚無可驗證的盤中資料。",
    }


def _verification_rows(conn: sqlite3.Connection, captured_at: datetime) -> tuple[list[sqlite3.Row], str]:
    captured_text = captured_at.isoformat(timespec="seconds")
    exact = conn.execute(
        """
        SELECT *
        FROM tw_full_market_snapshots
        WHERE captured_at = ?
        """,
        (captured_text,),
    ).fetchall()
    if exact:
        return exact, "exact_captured_at"
    date_text = captured_at.date().isoformat()
    latest_for_date = conn.execute(
        """
        SELECT s.*
        FROM tw_full_market_snapshots s
        JOIN (
          SELECT symbol, MAX(captured_at) AS captured_at
          FROM tw_full_market_snapshots
          WHERE date = ?
            AND verified_at IS NULL
          GROUP BY symbol
        ) latest
          ON latest.symbol = s.symbol
         AND latest.captured_at = s.captured_at
        ORDER BY s.symbol
        """,
        (date_text,),
    ).fetchall()
    if latest_for_date:
        return latest_for_date, "latest_unverified_for_date"
    latest_any = conn.execute(
        """
        SELECT s.*
        FROM tw_full_market_snapshots s
        JOIN (
          SELECT symbol, MAX(captured_at) AS captured_at
          FROM tw_full_market_snapshots
          WHERE date = ?
          GROUP BY symbol
        ) latest
          ON latest.symbol = s.symbol
         AND latest.captured_at = s.captured_at
        ORDER BY s.symbol
        """,
        (date_text,),
    ).fetchall()
    return latest_any, "latest_for_date" if latest_any else "none"


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


def build_entry_radar_scorecard(conn: sqlite3.Connection, windows: Iterable[int] = (20, 40, 60)) -> dict:
    rows = _latest_snapshot_rows(conn)
    dates = sorted({row["date"] for row in rows}, reverse=True)
    payload = {
        "version": STRATEGY_VALIDATION_VERSION,
        "title": "進場雷達成績單",
        "available_trade_days": len(dates),
        "windows": {},
        "message": "此表只統計最大卡關原因後續表現，不會自動調整 A / B+ / B 條件。",
    }
    for window in windows:
        selected_dates = set(dates[:window])
        window_rows = [row for row in rows if row["date"] in selected_dates]
        payload["windows"][str(window)] = _entry_radar_window_scorecard(window_rows, window)
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
    trend = window.get("trend_continuation") or {}

    if high_risk.get("sample_size", 0) >= 10 and high_risk.get("continue_up_rate", 0) >= 35:
        notes.append("可能過度保守：high_risk 後續仍有較高比例繼續上漲，建議觀察 trend_continuation_watch 分層，但不要直接升級 A。")
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
    if not trend.get("is_statistically_meaningful"):
        notes.append("趨勢延續樣本不足，不建議調整模型；先累積 trend_continuation_watch 的盤後結果。")
    elif trend.get("continue_up_rate", 0) >= 50:
        notes.append("趨勢延續觀察已有初步續漲樣本，可繼續追蹤，但不應自動放寬 A / B+ / B 條件。")
    if not notes:
        notes.append("目前樣本仍需累積；建議觀察 20 日後再調整，暫不建議修改模型條件。")
    return notes


def build_entry_radar_observations(radar_scorecard: dict) -> list[str]:
    window = ((radar_scorecard or {}).get("windows") or {}).get("20") or {}
    rows = window.get("rows") or []
    notes: list[str] = []
    if int(window.get("verified", 0) or 0) < MIN_MEANINGFUL_SAMPLE_SIZE:
        return ["進場雷達樣本不足，先累積卡關原因與盤後結果，不建議調整模型條件。"]
    candidates = [row for row in rows if int(row.get("verified", 0) or 0) >= MIN_MEANINGFUL_SAMPLE_SIZE]
    for row in candidates[:3]:
        label = str(row.get("blocker_label") or row.get("blocker_code") or "未知卡關")
        continue_rate = float(row.get("continue_up_rate", 0) or 0)
        pullback_rate = float(row.get("pullback_rate", 0) or 0)
        if continue_rate >= 35:
            notes.append(f"{label} 後續仍有一定續漲比例，建議保留觀察層，不要直接升級為可執行。")
        elif pullback_rate >= 50:
            notes.append(f"{label} 後續回撤比例偏高，現有等待或避開規則暫時合理。")
    return notes or ["進場雷達目前沒有明顯偏差，持續累積 20 / 40 / 60 日樣本。"]


def _entry_radar_window_scorecard(rows: list[sqlite3.Row], window: int) -> dict:
    grouped: dict[str, list[sqlite3.Row]] = defaultdict(list)
    for row in rows:
        grouped[_entry_radar_blocker_code(row)].append(row)
    items = [_entry_radar_group_stats(code, items) for code, items in grouped.items()]
    items.sort(key=lambda item: (-int(item["verified"]), -int(item["sample_size"]), str(item["blocker_label"])))
    verified = sum(1 for row in rows if row["max_gain_after_scan"] is not None)
    meaningful = [item for item in items if int(item["verified"]) >= MIN_MEANINGFUL_SAMPLE_SIZE]
    top_continue = max(meaningful, key=lambda item: (float(item["continue_up_rate"]), float(item["avg_max_gain"])), default=None)
    top_pullback = max(meaningful, key=lambda item: (float(item["pullback_rate"]), abs(float(item["avg_max_drawdown"]))), default=None)
    return {
        "window_days": window,
        "sample_size": len(rows),
        "verified": verified,
        "sample_quality": _sample_quality(verified),
        "sample_message": _sample_message(verified),
        "is_statistically_meaningful": verified >= MIN_MEANINGFUL_SAMPLE_SIZE,
        "rows": items,
        "top_continue": top_continue,
        "top_pullback": top_pullback,
        "message": "樣本不足，不建議依卡關原因調整模型。" if verified < MIN_MEANINGFUL_SAMPLE_SIZE else "可初步觀察卡關原因是否過度保守或確實有效。",
    }


def _entry_radar_group_stats(code: str, rows: list[sqlite3.Row]) -> dict:
    total = len(rows)
    verified = [row for row in rows if row["max_gain_after_scan"] is not None]
    wins = [row for row in verified if (_float(row["max_gain_after_scan"]) or 0) >= 1]
    target_0_5 = [row for row in verified if bool(row["hit_0_5_pct"])]
    target_1 = [row for row in verified if bool(row["hit_1_pct"])]
    target_2 = [row for row in verified if bool(row["hit_2_pct"])]
    pullback = [row for row in verified if (_float(row["max_drawdown_after_scan"]) or 0) <= -1]
    stop = [row for row in verified if bool(row["hit_stop_loss"])]
    target = [row for row in verified if bool(row["hit_take_profit"]) or bool(row["hit_2_pct"])]
    avg_gain = _avg(_float(row["max_gain_after_scan"]) for row in verified)
    avg_drawdown = _avg(_float(row["max_drawdown_after_scan"]) for row in verified)
    label = _entry_radar_blocker_label(code)
    return {
        "blocker_code": code,
        "blocker_label": label,
        "sample_size": total,
        "verified": len(verified),
        "sample_quality": _sample_quality(len(verified)),
        "sample_message": _sample_message(len(verified)),
        "is_statistically_meaningful": len(verified) >= MIN_MEANINGFUL_SAMPLE_SIZE,
        "win_rate": round(len(wins) / len(verified) * 100, 2) if verified else 0.0,
        "target_0_5_rate": round(len(target_0_5) / len(verified) * 100, 2) if verified else 0.0,
        "target_1_rate": round(len(target_1) / len(verified) * 100, 2) if verified else 0.0,
        "target_2_rate": round(len(target_2) / len(verified) * 100, 2) if verified else 0.0,
        "avg_max_gain": avg_gain,
        "avg_max_drawdown": avg_drawdown,
        "stop_rate": round(len(stop) / len(verified) * 100, 2) if verified else 0.0,
        "target_rate": round(len(target) / len(verified) * 100, 2) if verified else 0.0,
        "continue_up_rate": round(len(target_2) / len(verified) * 100, 2) if verified else 0.0,
        "pullback_rate": round(len(pullback) / len(verified) * 100, 2) if verified else 0.0,
        "interpretation": _entry_radar_interpretation(label, len(verified), len(target_2), len(pullback)),
    }


def _entry_radar_blocker_code(row: sqlite3.Row) -> str:
    keys = set(row.keys())
    source_only_codes = {"full_market_detected", "selected", "not_in_watchlist", "out_of_pool"}
    for field in ("signal_reason_code", "reason_code", "entry_status", "signal_entry_status", "ai_grade"):
        value = row[field] if field in keys else None
        code = str(value).strip() if value is not None else ""
        if code and code not in {"-", "unknown", "未入選"} and code.lower() not in source_only_codes:
            return _normalize_blocker_code(code)
    return "unknown"


def _normalize_blocker_code(code: str) -> str:
    lower = code.lower()
    if lower in {"volume_ratio_missing", "missing_volume_ratio"}:
        return "missing_volume_ratio"
    if lower in {"vwap_missing", "missing_vwap"}:
        return "missing_vwap"
    if lower in {"risk_high", "high_chase_risk", "too_far_from_vwap"}:
        return lower
    if "volume" in lower and "missing" in lower:
        return "missing_volume_ratio"
    if "vwap" in lower and "missing" in lower:
        return "missing_vwap"
    if "volume" in lower or lower == "wait_volume":
        return "wait_volume" if lower == "wait_volume" else "low_volume_ratio"
    if "vwap" in lower or lower == "wait_vwap":
        return "wait_vwap" if lower == "wait_vwap" else "below_vwap"
    if "breakout" in lower or lower == "wait_breakout":
        return "wait_breakout" if lower == "wait_breakout" else "no_breakout"
    if lower in {"high_risk", "avoid", "data_missing", "data_failed", "wait_pullback"}:
        return lower
    return lower


def _entry_radar_blocker_label(code: str) -> str:
    return BLOCKER_LABELS.get(code, code.replace("_", " "))


def _entry_radar_interpretation(label: str, verified: int, continue_count: int, pullback_count: int) -> str:
    if verified < MIN_MEANINGFUL_SAMPLE_SIZE:
        return "樣本不足，先累積資料，不建議調整模型。"
    continue_rate = continue_count / verified * 100 if verified else 0
    pullback_rate = pullback_count / verified * 100 if verified else 0
    if continue_rate >= 35:
        return f"{label} 後續仍有續強案例，可列為觀察偏差，但不要直接放寬 A 級。"
    if pullback_rate >= 50:
        return f"{label} 後續回撤比例偏高，目前風控提醒具有參考價值。"
    return f"{label} 目前未呈現明顯偏差，建議繼續累積樣本。"


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
        "verified": sum(1 for row in rows if row["max_gain_after_scan"] is not None),
        "sample_quality": _sample_quality(len(rows)),
        "is_statistically_meaningful": len(rows) >= MIN_MEANINGFUL_SAMPLE_SIZE,
        "is_trusted_sample": len(rows) >= TRUSTED_SAMPLE_SIZE,
        "groups": groups,
        "trend_continuation": trend_continuation_validation(rows),
        "sample_message": _sample_message(len(rows)),
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
        "sample_quality": _sample_quality(len(verified)),
        "sample_message": _sample_message(len(verified)),
        "is_statistically_meaningful": len(verified) >= MIN_MEANINGFUL_SAMPLE_SIZE,
        "is_trusted_sample": len(verified) >= TRUSTED_SAMPLE_SIZE,
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


def _sample_quality(sample_size: int) -> str:
    if sample_size < MIN_MEANINGFUL_SAMPLE_SIZE:
        return "insufficient"
    if sample_size < EARLY_SAMPLE_SIZE:
        return "early"
    if sample_size < TRUSTED_SAMPLE_SIZE:
        return "meaningful"
    return "trusted"


def _sample_message(sample_size: int) -> str:
    quality = _sample_quality(sample_size)
    if quality == "insufficient":
        return "樣本不足，不建議判斷模型準確度。"
    if quality == "early":
        return "已有初步樣本，可觀察方向，但不建議大幅調整模型。"
    if quality == "meaningful":
        return "樣本量已具初步參考價值，可用於檢查模型偏差。"
    return "樣本量較充足，可作為模型調整的重要依據。"


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
