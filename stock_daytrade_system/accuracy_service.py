from __future__ import annotations

import sqlite3
from collections import Counter, defaultdict
from typing import Iterable

from stock_daytrade_system.confidence_config import DEFAULT_CONFIDENCE_CONFIG, ConfidenceConfig


def build_accuracy_dashboard_payload(
    conn: sqlite3.Connection,
    config: ConfidenceConfig = DEFAULT_CONFIDENCE_CONFIG,
) -> dict:
    samples = _accuracy_samples(conn)
    summary = build_accuracy_summary(samples, config)
    return {
        "api_status": "ok",
        "title": "策略成績單",
        "summary": summary,
        "by_status": _group_samples(samples, "entry_status", config),
        "by_market": _group_samples(samples, "market", config),
        "by_confidence": _group_samples(samples, "confidence_level", config),
        "model_suggestions": _model_suggestions(samples, config),
        "disclaimer": "本系統僅供資料整理、策略追蹤與回測，不構成投資建議，也不保證獲利。",
    }


def build_accuracy_group_payload(
    conn: sqlite3.Connection,
    group_key: str,
    config: ConfidenceConfig = DEFAULT_CONFIDENCE_CONFIG,
) -> dict:
    allowed = {"entry_status", "market", "confidence_level", "grade", "symbol"}
    if group_key not in allowed:
        group_key = "entry_status"
    samples = _accuracy_samples(conn)
    return {
        "api_status": "ok",
        "group_key": group_key,
        "rows": _group_samples(samples, group_key, config),
        "summary": build_accuracy_summary(samples, config),
    }


def build_accuracy_summary(samples: list[dict], config: ConfidenceConfig = DEFAULT_CONFIDENCE_CONFIG) -> dict:
    stats = _stats(samples, config)
    stats["message"] = _sample_message(stats["sample_size"], config)
    stats["high_confidence"] = _stats([item for item in samples if item.get("confidence_level") == "high"], config)
    stats["low_confidence"] = _stats([item for item in samples if item.get("confidence_level") == "low"], config)
    stats["unreliable"] = _stats([item for item in samples if item.get("confidence_level") == "unreliable"], config)
    return stats


def _accuracy_samples(conn: sqlite3.Connection) -> list[dict]:
    samples: list[dict] = []
    for row in conn.execute(
        """
        SELECT b.market, b.symbol, b.entry_status, b.return_pct, b.max_drawdown_after_trigger,
               b.hit_target, COALESCE(b.hit_stop_loss, b.hit_stop) AS hit_stop,
               b.outcome, r.grade, r.confidence_level, r.confidence_score, r.conflicts
        FROM backtest_results b
        LEFT JOIN recommendations r
          ON r.market = b.market AND r.date = b.date AND r.symbol = b.symbol
        WHERE b.return_pct IS NOT NULL
           OR b.hit_target IS NOT NULL
           OR b.hit_stop IS NOT NULL
           OR b.hit_stop_loss IS NOT NULL
        """
    ).fetchall():
        return_pct = _float(row["return_pct"])
        samples.append(
            {
                "source": "backtest",
                "market": row["market"] or "TW",
                "symbol": row["symbol"],
                "grade": row["grade"] or "unknown",
                "entry_status": row["entry_status"] or "unknown",
                "confidence_level": row["confidence_level"] or "unknown",
                "confidence_score": _float(row["confidence_score"]),
                "return_pct": return_pct,
                "max_drawdown_pct": _float(row["max_drawdown_after_trigger"]),
                "hit_target": bool(row["hit_target"]),
                "hit_stop": bool(row["hit_stop"]),
                "is_win": return_pct > 0 or bool(row["hit_target"]),
                "conflicts": row["conflicts"] or "[]",
            }
        )
    for row in conn.execute(
        """
        SELECT market, symbol, grade, entry_status, realized_pnl_pct,
               max_adverse_excursion, status, source
        FROM paper_trades
        WHERE status IN ('closed', 'stopped', 'target_hit', 'forced_exit')
          AND realized_pnl_pct IS NOT NULL
        """
    ).fetchall():
        return_pct = _float(row["realized_pnl_pct"])
        samples.append(
            {
                "source": row["source"] or "paper",
                "market": row["market"],
                "symbol": row["symbol"],
                "grade": row["grade"] or "unknown",
                "entry_status": row["entry_status"] or "unknown",
                "confidence_level": "unknown",
                "confidence_score": 0.0,
                "return_pct": return_pct,
                "max_drawdown_pct": _float(row["max_adverse_excursion"]),
                "hit_target": row["status"] == "target_hit",
                "hit_stop": row["status"] == "stopped",
                "is_win": return_pct > 0,
                "conflicts": "[]",
            }
        )
    return samples


def _group_samples(samples: list[dict], key: str, config: ConfidenceConfig) -> list[dict]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for item in samples:
        grouped[str(item.get(key) or "unknown")].append(item)
    return [
        {"group": group, **_stats(items, config)}
        for group, items in sorted(grouped.items(), key=lambda pair: (-len(pair[1]), pair[0]))
    ]


def _stats(samples: list[dict], config: ConfidenceConfig) -> dict:
    total = len(samples)
    wins = sum(1 for item in samples if item.get("is_win"))
    target = sum(1 for item in samples if item.get("hit_target"))
    stop = sum(1 for item in samples if item.get("hit_stop"))
    returns = [float(item.get("return_pct") or 0) for item in samples]
    drawdowns = [float(item.get("max_drawdown_pct") or 0) for item in samples]
    return {
        "sample_size": total,
        "min_sample_size": config.min_sample_size,
        "trusted_sample_size": config.trusted_sample_size,
        "is_statistically_meaningful": total >= config.min_sample_size,
        "is_trusted_sample": total >= config.trusted_sample_size,
        "win_rate": round(wins / total * 100, 2) if total else 0.0,
        "avg_return_pct": round(sum(returns) / total, 2) if total else 0.0,
        "avg_max_drawdown_pct": round(sum(drawdowns) / total, 2) if total else 0.0,
        "stop_rate": round(stop / total * 100, 2) if total else 0.0,
        "target_rate": round(target / total * 100, 2) if total else 0.0,
    }


def _model_suggestions(samples: list[dict], config: ConfidenceConfig) -> list[str]:
    if len(samples) < config.min_sample_size:
        return ["目前樣本數不足，暫不判斷策略準確度。"]
    grouped = _group_samples(samples, "entry_status", config)
    weak = [item for item in grouped if item["sample_size"] >= config.min_sample_size and item["win_rate"] < 45]
    suggestions = [f"{item['group']} 勝率偏低，建議檢查進場條件或提高信心門檻。" for item in weak[:3]]
    return suggestions or ["目前沒有明顯需要調整的模型條件，持續累積樣本。"]


def _sample_message(sample_size: int, config: ConfidenceConfig) -> str:
    if sample_size < config.min_sample_size:
        return "目前樣本數不足，暫不判斷策略準確度。"
    if sample_size < config.trusted_sample_size:
        return "目前已有初步樣本，可作為方向參考，但仍需持續累積。"
    return "目前樣本數較充足，可作為較可信的策略統計參考。"


def _float(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0
