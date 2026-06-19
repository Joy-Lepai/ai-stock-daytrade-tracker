from __future__ import annotations

import json
import sqlite3
from collections import Counter, defaultdict
from typing import Iterable

from stock_daytrade_system.confidence_config import DEFAULT_CONFIDENCE_CONFIG, ConfidenceConfig
from stock_daytrade_system.strategy_validation import (
    build_missed_rate_report,
    build_model_observations,
    build_strategy_scorecard,
)


REVIEW_TAG_LABELS = {
    "discipline": "紀律執行（完美停損/停利）",
    "fomo": "FOMO（衝動追高）",
    "hold_loser": "凹單（未依系統提示停損）",
    "revenge_trade": "報復性交易（過度頻繁交易）",
    "early_exit": "提前離場（少賺）",
}


def build_accuracy_dashboard_payload(
    conn: sqlite3.Connection,
    config: ConfidenceConfig = DEFAULT_CONFIDENCE_CONFIG,
) -> dict:
    samples = _accuracy_samples(conn)
    summary = build_accuracy_summary(samples, config)
    strategy_scorecard = build_strategy_scorecard(conn)
    missed_rate_report = build_missed_rate_report(conn)
    model_suggestions = _model_suggestions(samples, config)
    model_observations = build_model_observations(strategy_scorecard, missed_rate_report)
    return {
        "api_status": "ok",
        "title": "策略成績單",
        "summary": summary,
        "by_status": _group_samples(samples, "entry_status", config),
        "by_grade": _group_samples(samples, "grade", config),
        "by_market": _group_samples(samples, "market", config),
        "by_confidence": _group_samples(samples, "confidence_level", config),
        "b_plus_lifecycle": _b_plus_lifecycle_stats(conn),
        "strategy_scorecard": strategy_scorecard,
        "missed_rate_report": missed_rate_report,
        "review_tag_distribution": _paper_review_tag_distribution(conn, losing_only=False),
        "review_tag_loss_distribution": _paper_review_tag_distribution(conn, losing_only=True),
        "review_tag_options": [
            {"code": code, "label": label}
            for code, label in REVIEW_TAG_LABELS.items()
        ],
        "model_suggestions": model_suggestions + [item for item in model_observations if item not in model_suggestions],
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
    stats["grade_a"] = _stats([item for item in samples if item.get("grade") == "A"], config)
    stats["grade_b_plus"] = _stats([item for item in samples if item.get("grade") == "B+"], config)
    stats["grade_b_plus_triggered"] = _stats(
        [
            item
            for item in samples
            if item.get("grade") == "B+"
            and item.get("lifecycle_status") not in {"observed", "expired", "unknown", ""}
        ],
        config,
    )
    return stats


def _accuracy_samples(conn: sqlite3.Connection) -> list[dict]:
    samples: list[dict] = []
    for row in conn.execute(
        """
        SELECT b.market, b.symbol, b.entry_status, b.lifecycle_status, b.return_pct,
               b.max_gain_after_trigger, b.max_drawdown_after_trigger,
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
                "lifecycle_status": row["lifecycle_status"] or "unknown",
                "entry_status": row["entry_status"] or "unknown",
                "confidence_level": row["confidence_level"] or "unknown",
                "confidence_score": _float(row["confidence_score"]),
                "return_pct": return_pct,
                "max_gain_pct": _float(row["max_gain_after_trigger"]),
                "max_drawdown_pct": _float(row["max_drawdown_after_trigger"]),
                "hit_target": bool(row["hit_target"]),
                "hit_stop": bool(row["hit_stop"]),
                "is_win": return_pct > 0 or bool(row["hit_target"]),
                "conflicts": row["conflicts"] or "[]",
            }
        )
    for row in conn.execute(
        """
        SELECT market, symbol, grade, entry_status, lifecycle_status, realized_pnl_pct,
               max_favorable_excursion, max_adverse_excursion, status, source
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
                "lifecycle_status": row["lifecycle_status"] or "unknown",
                "entry_status": row["entry_status"] or "unknown",
                "confidence_level": "unknown",
                "confidence_score": 0.0,
                "return_pct": return_pct,
                "max_gain_pct": _float(row["max_favorable_excursion"]),
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
    gains = [float(item.get("max_gain_pct") or 0) for item in samples]
    return {
        "sample_size": total,
        "min_sample_size": config.min_sample_size,
        "trusted_sample_size": config.trusted_sample_size,
        "sample_quality": _sample_quality(total, config),
        "sample_message": _sample_message(total, config),
        "is_statistically_meaningful": total >= config.min_sample_size,
        "is_trusted_sample": total >= config.trusted_sample_size,
        "win_rate": round(wins / total * 100, 2) if total else 0.0,
        "avg_return_pct": round(sum(returns) / total, 2) if total else 0.0,
        "avg_max_gain_pct": round(sum(gains) / total, 2) if total else 0.0,
        "avg_max_drawdown_pct": round(sum(drawdowns) / total, 2) if total else 0.0,
        "stop_rate": round(stop / total * 100, 2) if total else 0.0,
        "target_rate": round(target / total * 100, 2) if total else 0.0,
    }


def _sample_quality(sample_size: int, config: ConfidenceConfig) -> str:
    if sample_size < config.min_sample_size:
        return "insufficient"
    if sample_size < 60:
        return "early"
    if sample_size < config.trusted_sample_size:
        return "meaningful"
    return "trusted"


def _model_suggestions(samples: list[dict], config: ConfidenceConfig) -> list[str]:
    if len(samples) < config.min_sample_size:
        return ["目前樣本數不足，暫不判斷策略準確度。"]
    grouped = _group_samples(samples, "entry_status", config)
    weak = [item for item in grouped if item["sample_size"] >= config.min_sample_size and item["win_rate"] < 45]
    suggestions = [f"{item['group']} 勝率偏低，建議檢查進場條件或提高信心門檻。" for item in weak[:3]]
    return suggestions or ["目前沒有明顯需要調整的模型條件，持續累積樣本。"]


def _b_plus_lifecycle_stats(conn: sqlite3.Connection) -> dict:
    rows = conn.execute(
        """
        SELECT lifecycle_status, COUNT(*) AS total
        FROM recommendations
        WHERE grade = 'B+'
        GROUP BY lifecycle_status
        """
    ).fetchall()
    counts = {row["lifecycle_status"] or "observed": int(row["total"] or 0) for row in rows}
    total = sum(counts.values())
    triggered = counts.get("triggered", 0) + counts.get("closed", 0) + counts.get("stopped", 0) + counts.get("hit_target", 0)
    untriggered = max(total - triggered, 0)
    return {
        "sample_size": total,
        "triggered": triggered,
        "untriggered": untriggered,
        "untriggered_ratio": round(untriggered / total * 100, 2) if total else 0.0,
        "by_lifecycle": counts,
    }


def _paper_review_tag_distribution(conn: sqlite3.Connection, *, losing_only: bool) -> dict:
    rows = conn.execute(
        """
        SELECT review_tags, realized_pnl
        FROM paper_trades
        WHERE status IN ('closed', 'stopped', 'target_hit', 'forced_exit')
          AND review_tags IS NOT NULL
          AND review_tags != ''
        """
    ).fetchall()
    counts: Counter[str] = Counter()
    reviewed_trades = 0
    for row in rows:
        if losing_only and _float(row["realized_pnl"]) >= 0:
            continue
        tags = _parse_review_tags(row["review_tags"])
        if not tags:
            continue
        reviewed_trades += 1
        counts.update(tags)
    total_tags = sum(counts.values())
    return {
        "sample_size": reviewed_trades,
        "total_tags": total_tags,
        "losing_only": losing_only,
        "message": (
            "目前尚無已覆盤的虧損交易，暫無心魔分佈。"
            if losing_only and not total_tags
            else "目前尚無已覆盤交易。"
            if not total_tags
            else ""
        ),
        "rows": [
            {
                "code": code,
                "label": REVIEW_TAG_LABELS[code],
                "count": counts[code],
                "pct": round(counts[code] / total_tags * 100, 2) if total_tags else 0.0,
            }
            for code in REVIEW_TAG_LABELS
            if counts[code] > 0
        ],
    }


def _parse_review_tags(value: str) -> list[str]:
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        parsed = [part.strip() for part in str(value or "").split(",")]
    if not isinstance(parsed, list):
        return []
    result = []
    for item in parsed:
        code = str(item or "").strip()
        if code in REVIEW_TAG_LABELS and code not in result:
            result.append(code)
    return result


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
