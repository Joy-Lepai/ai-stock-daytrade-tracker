from datetime import datetime
from pathlib import Path
import tempfile
import unittest

from stock_daytrade_system.intraday import OpeningSignal
from stock_daytrade_system.long_model import LongModelSummary
from stock_daytrade_system.scoring import CandidateScore
from stock_daytrade_system.tracker import (
    _bullish_focus_table,
    _change_number,
    _data_status_block,
    _entry_status_message,
    _recommendation_checklist_table,
    _tracked_table,
    bullish_profile,
    build_tracked_symbols,
    classify_status,
    render_tracker_html,
)
from stock_daytrade_system.scoring import MarketBias


def candidate(direction="做多觀察", shares=1000):
    return CandidateScore(
        symbol="2330.TW",
        name="台積電",
        sector="semiconductor",
        direction=direction,
        score=5,
        close=100,
        day_change_pct=1,
        avg_volume=1_000_000,
        atr=2,
        previous_high=101,
        previous_low=98,
        trigger_price=101,
        stop_loss=98,
        target_price=105.5,
        risk_per_share=3,
        suggested_shares=shares,
        reasons=[],
    )


def opening(direction="做多確認"):
    return OpeningSignal(
        symbol="2330.TW",
        name="台積電",
        sector="semiconductor",
        direction=direction,
        score=4,
        last_price=102,
        opening_range_high=101,
        opening_range_low=99,
        vwap=101.25,
        cumulative_volume=2_000_000,
        volume_ratio=1.5,
        reasons=[],
    )


class TrackerStatusTests(unittest.TestCase):
    def test_marks_aligned_candidate_and_opening_signal_as_actionable(self):
        status, priority, _notes = classify_status(candidate(), opening())

        self.assertEqual(status, "可執行")
        self.assertEqual(priority, 0)

    def test_marks_zero_share_candidate_as_too_risky(self):
        status, _priority, notes = classify_status(candidate(shares=0), opening())

        self.assertEqual(status, "風險過高")
        self.assertTrue(notes)

    def test_marks_candidate_without_opening_signal_as_waiting(self):
        status, _priority, _notes = classify_status(candidate(), opening("觀望"))

        self.assertEqual(status, "等待確認")

    def test_tracked_table_has_sortable_columns_and_change_color(self):
        rows = build_tracked_symbols(
            symbols=[candidate()],
            candidates=[candidate()],
            opening_signals=[],
            sector_strengths=[],
        )

        html = _tracked_table(rows)

        self.assertIn('table class="sortable"', html)
        self.assertIn('data-sort="number">漲跌', html)
        self.assertIn('class="num-up">+1.00%', html)
        self.assertIn("進場狀態", html)
        self.assertIn("VWAP", html)
        self.assertIn("取消條件", html)

    def test_change_number_uses_taiwan_market_colors(self):
        self.assertIn('class="num-up">+1.20%', _change_number(1.2, suffix="%"))
        self.assertIn('class="num-down">-1.20%', _change_number(-1.2, suffix="%"))

    def test_bullish_profile_marks_clear_long_setup(self):
        item = candidate()
        signal = opening("做多確認")

        label, score, reasons = bullish_profile(item, signal, sector=None, ranking=None)

        self.assertIn(label, {"看漲", "強烈看漲"})
        self.assertGreaterEqual(score, 5)
        self.assertIn("盤前做多", reasons)

    def test_focus_table_surfaces_today_bullish_label(self):
        rows = build_tracked_symbols(
            symbols=[candidate()],
            candidates=[candidate()],
            opening_signals=[opening("做多確認")],
            sector_strengths=[],
        )

        html = _bullish_focus_table(rows)

        self.assertIn("當日看漲", html)
        self.assertIn("看漲理由", html)
        self.assertIn("可進場", html)
        self.assertIn("站上VWAP", html)
        self.assertIn("跌破VWAP 101.25", html)

    def test_recommendation_checklist_surfaces_mvp_counts(self):
        summary = LongModelSummary(
            candidates=[],
            alerts=[],
            sector_heat=[],
            market_state="偏多",
            market_notes=[],
            backtest={"recommendation_count": 2, "trackable_count": 1, "target": 0, "stop": 0, "avg_return": 0},
            recommendation_checklist={
                "candidate_total": 3,
                "grade_a": 1,
                "grade_b": 1,
                "executable": 1,
                "wait_volume": 1,
                "wait_vwap": 0,
                "high_risk": 1,
                "avoid": 2,
                "recommendations": 2,
                "backtest_trackable": 1,
                "data_missing": 4,
            },
        )

        html = _recommendation_checklist_table(summary)

        self.assertIn("今日候選股總數", html)
        self.assertIn("executable 可執行", html)
        self.assertIn("wait_volume 等量能", html)
        self.assertIn("high_risk 風險過高", html)
        self.assertIn("avoid 暫不追蹤", html)
        self.assertIn("已寫入 recommendations", html)
        self.assertIn("<strong>4</strong>", html)

    def test_entry_status_messages_explain_wait_states(self):
        self.assertEqual(
            _entry_status_message("wait_volume"),
            "多方結構不錯，但量能不足，等待量比放大後再觀察。",
        )
        self.assertEqual(
            _entry_status_message("wait_vwap"),
            "突破條件成立，但尚未站上 VWAP，等待站回均價線。",
        )
        self.assertEqual(
            _entry_status_message("high_risk"),
            "多方動能強，但追價風險偏高，避免直接追高。",
        )

    def test_data_status_block_explains_success_and_exclusion(self):
        html = _data_status_block(["盤中行情成功 20/21；失敗標的不納入 VWAP、量比與盤中回測。"])

        self.assertIn("資料狀態", html)
        self.assertIn("失敗標的不納入", html)

    def test_render_marks_legacy_sections_as_reference(self):
        summary = LongModelSummary(
            candidates=[],
            alerts=[],
            sector_heat=[],
            market_state="偏多",
            market_notes=[],
            backtest={"recommendation_count": 0, "trackable_count": 0, "target": 0, "stop": 0, "avg_return": 0},
            recommendation_checklist={},
            debug_info={
                "app_version": "abc123",
                "scoring_model_version": "long_model_v2_volume_vwap_2026-06-12",
                "dashboard_generated_at": "2026-01-01T09:05:00",
                "recommendations_count_from_db": 2,
                "candidates_count_from_current_run": 17,
                "visible_candidates_count": 12,
            },
        )
        with tempfile.TemporaryDirectory() as directory:
            output_path = Path(directory) / "tracker.html"
            render_tracker_html(
                datetime(2026, 1, 1, 9, 5),
                MarketBias(score=1, direction="偏多", notes=[]),
                [],
                [],
                [],
                [],
                output_path,
                data_status=["每日行情成功 1/1"],
                long_summary=summary,
            )
            html = output_path.read_text(encoding="utf-8")

        self.assertIn("今日推薦檢查表", html)
        self.assertIn("系統版本 / Debug", html)
        self.assertIn("long_model_v2_volume_vwap_2026-06-12", html)
        self.assertIn("recommendations count from DB", html)
        self.assertIn("舊版參考：今日看漲焦點", html)
        self.assertIn("舊版參考：系統自動選股", html)


if __name__ == "__main__":
    unittest.main()
