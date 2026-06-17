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
    _tomorrow_continuation_candidates,
    _tomorrow_long_watch_pool,
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
            backtest={
                "recommendation_count": 2,
                "trackable_count": 1,
                "triggered_backtest_count": 1,
                "observed_count": 1,
                "triggered_count": 1,
                "expired_count": 0,
                "closed_count": 0,
                "target": 0,
                "stop": 0,
                "avg_return": 0,
            },
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
                "triggered_backtest": 1,
                "observed": 1,
                "triggered": 1,
                "expired": 0,
                "closed": 0,
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
        self.assertIn("observed 觀察中", html)
        self.assertIn("triggered 已觸發", html)
        self.assertIn("今日已觸發回測數量", html)
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

    def test_tomorrow_watch_pool_surfaces_more_than_executable_names(self):
        summary = LongModelSummary(
            candidates=[],
            alerts=[],
            sector_heat=[],
            market_state="偏多",
            market_notes=[],
            backtest={},
            recommendation_checklist={},
            momentum_scan={
                "items": [
                    {
                        "symbol": "3443.TW",
                        "name": "創意",
                        "sector": "semiconductor",
                        "latest_price": 5075,
                        "change_pct": 3.15,
                        "volume_ratio": 1.22,
                        "turnover": 15_680_724_850,
                        "vwap": 5010.96,
                        "above_vwap": True,
                        "break_prev_high": True,
                        "break_5d_high": True,
                        "ai_grade": "A",
                        "entry_status": "executable",
                        "trade_bias": "long",
                        "trade_bias_label": "買多",
                        "trade_bias_reason": "站上 VWAP、量能達標且訊號可執行。",
                        "not_selected_reason": "已進入正式候選",
                    },
                    {
                        "symbol": "2892.TW",
                        "name": "第一金",
                        "sector": "financial",
                        "latest_price": 32.5,
                        "change_pct": 2.2,
                        "volume_ratio": 0.86,
                        "turnover": 1_804_164_050,
                        "vwap": 32.35,
                        "above_vwap": True,
                        "break_prev_high": True,
                        "break_5d_high": True,
                        "ai_grade": "B+",
                        "entry_status": "practice_long",
                        "trade_bias": "long",
                        "trade_bias_label": "買多",
                        "trade_bias_reason": "練習買多條件成立",
                        "not_selected_reason": "已進入正式候選",
                    },
                    {
                        "symbol": "2330.TW",
                        "name": "台積電",
                        "sector": "semiconductor",
                        "latest_price": 2310,
                        "change_pct": 2.67,
                        "volume_ratio": 0.48,
                        "turnover": 49_805_004_480,
                        "vwap": 2299.81,
                        "above_vwap": True,
                        "break_prev_high": True,
                        "break_5d_high": False,
                        "ai_grade": "D",
                        "entry_status": "wait_volume",
                        "trade_bias": "watch",
                        "trade_bias_label": "觀察",
                        "trade_bias_reason": "量能尚未確認",
                        "not_selected_reason": "量比不足",
                    },
                    {
                        "symbol": "2327.TW",
                        "name": "國巨",
                        "sector": "passive_components",
                        "latest_price": 984,
                        "change_pct": 3.58,
                        "volume_ratio": 1.15,
                        "turnover": 55_219_128_000,
                        "vwap": 966.96,
                        "above_vwap": True,
                        "break_prev_high": False,
                        "break_5d_high": False,
                        "ai_grade": "C",
                        "entry_status": "high_risk",
                        "trade_bias": "watch",
                        "trade_bias_label": "觀察",
                        "trade_bias_reason": "風險或結構衝突偏高",
                        "not_selected_reason": "強勢但追價風險高，不列為 A，可列入觀察。",
                    },
                ]
            },
        )

        html = _tomorrow_long_watch_pool(summary)

        self.assertIn("明日觀察池", html)
        self.assertIn("正式買多", html)
        self.assertIn("練習買多", html)
        self.assertIn("盤中等待確認", html)
        self.assertIn("強勢但高風險", html)
        self.assertIn("台積電", html)
        self.assertIn("國巨", html)

    def test_tomorrow_continuation_candidates_apply_relaxed_but_risk_controlled_rules(self):
        summary = LongModelSummary(
            candidates=[],
            alerts=[],
            sector_heat=[],
            market_state="偏多",
            market_notes=[],
            backtest={},
            recommendation_checklist={},
            momentum_scan={
                "items": [
                    {
                        "symbol": "8110.TW",
                        "name": "華東",
                        "sector": "memory",
                        "latest_price": 58.4,
                        "change_pct": 5.2,
                        "volume_ratio": 0.82,
                        "turnover": 2_300_000_000,
                        "vwap": 57.3,
                        "above_vwap": True,
                        "break_prev_high": True,
                        "break_5d_high": False,
                        "risk_score": 45,
                        "ai_grade": "B",
                        "entry_status": "wait_volume",
                    },
                    {
                        "symbol": "2327.TW",
                        "name": "國巨",
                        "sector": "passive_components",
                        "latest_price": 984,
                        "change_pct": 4.5,
                        "volume_ratio": 1.2,
                        "turnover": 55_000_000_000,
                        "vwap": 966.9,
                        "above_vwap": True,
                        "break_prev_high": True,
                        "risk_score": 55,
                        "entry_status": "high_risk",
                        "not_selected_reason": "上影線偏長，追價風險高",
                    },
                    {
                        "symbol": "2330.TW",
                        "name": "台積電",
                        "sector": "semiconductor",
                        "latest_price": 2385,
                        "change_pct": 3.5,
                        "volume_ratio": 0.58,
                        "turnover": 70_000_000_000,
                        "vwap": 2363,
                        "above_vwap": True,
                        "break_prev_high": True,
                        "risk_score": 20,
                        "entry_status": "wait_volume",
                    },
                    {
                        "symbol": "9999.TW",
                        "name": "高風險",
                        "sector": "test",
                        "latest_price": 100,
                        "change_pct": 8.0,
                        "volume_ratio": 1.5,
                        "turnover": 2_000_000_000,
                        "vwap": 96,
                        "above_vwap": True,
                        "break_prev_high": True,
                        "risk_score": 70,
                        "entry_status": "high_risk",
                    },
                ]
            },
        )

        html = _tomorrow_continuation_candidates(summary)

        self.assertIn("續強候選", html)
        self.assertIn("華東", html)
        self.assertIn("明天開盤確認", html)
        self.assertNotIn("國巨", html)
        self.assertNotIn("台積電", html)
        self.assertNotIn("高風險", html)

    def test_data_status_block_explains_success_and_exclusion(self):
        html = _data_status_block(["盤中行情成功 20/21；失敗標的不納入 VWAP、量比與盤中回測。"])

        self.assertIn("資料狀態", html)
        self.assertIn("失敗標的不納入", html)

    def test_render_uses_mvp_sections_and_debug_without_legacy_auto_blocks(self):
        summary = LongModelSummary(
            candidates=[],
            alerts=[],
            sector_heat=[],
            market_state="偏多",
            market_notes=[],
            backtest={"recommendation_count": 0, "trackable_count": 0, "target": 0, "stop": 0, "avg_return": 0},
            recommendation_checklist={
                "executable": 1,
                "wait_volume": 2,
                "wait_vwap": 3,
                "high_risk": 4,
            },
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
        self.assertIn("executable 可執行", html)
        self.assertIn("wait_volume 等量能", html)
        self.assertNotIn("舊版參考：今日看漲焦點", html)
        self.assertNotIn("舊版參考：系統自動選股", html)


if __name__ == "__main__":
    unittest.main()
