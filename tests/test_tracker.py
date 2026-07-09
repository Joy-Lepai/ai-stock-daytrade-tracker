from datetime import datetime
from pathlib import Path
import tempfile
import unittest

from stock_daytrade_system.intraday import OpeningSignal
from stock_daytrade_system.long_model import LongModelSummary
from stock_daytrade_system.scoring import CandidateScore
from stock_daytrade_system.tracker import (
    _backtest_table,
    _bullish_focus_table,
    _candidate_selection_explainer,
    _change_number,
    _data_status_block,
    _decision_overview,
    _display_trade_bias_label,
    _entry_radar_scorecard_panel,
    _entry_status_message,
    _focus_card,
    _fugle_priority_pool_panel,
    _grade_label,
    _limit_up_strength_panel,
    _market_mode_panel,
    _next_session_unified_watch_panel,
    _recommendation_checklist_table,
    _review_mode_sections,
    _signal_center,
    _strong_long_funnel_panel,
    _today_playbook_panel,
    _tomorrow_continuation_candidates,
    _tomorrow_long_watch_pool,
    _trend_continuation_panel,
    _tracked_table,
    bullish_profile,
    build_tracked_symbols,
    classify_status,
    render_tracker_html,
)
from stock_daytrade_system.scoring import MarketBias
from stock_daytrade_system.long_model import LongCandidate


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


def long_candidate(**overrides):
    base = dict(
        symbol="2330.TW",
        name="台積電",
        sector="semiconductor",
        last_price=100,
        change_pct=1.2,
        volume=1_000_000,
        turnover=100_000_000,
        avg_volume_20=900_000,
        daily_volume_ratio=1.1,
        intraday_volume=500_000,
        volume_ratio=1.2,
        vwap=99.5,
        above_vwap=True,
        previous_high=99,
        high_5d=101,
        high_10d=102,
        break_prev_high=True,
        break_5d_high=False,
        break_10d_high=False,
        upper_shadow_pct=0.1,
        institutional_buy_million=None,
        margin_balance=None,
        short_balance=None,
        daytrade_ratio=None,
        sector_strength=1,
        news_topics=[],
        market_state="偏多",
        bullish_score=72,
        risk_score=35,
        grade="B+",
        entry_status="wait_volume",
        original_entry_status="wait_volume",
        adjusted_entry_status="wait_volume",
        confidence_score=65,
        confidence_level="medium",
        confidence_level_label="中等信心",
        conflicts_count=0,
        conflicts=[],
        conflict_summary="",
        confidence_summary="資料完整度尚可。",
        confidence_adjustment_reason="",
        trade_bias="long",
        trade_bias_label="做多",
        trade_bias_reason="",
        trigger_price=100.5,
        stop_loss=98,
        target_price=103,
        opening_range_high=100.2,
        opening_range_low=98.8,
        reasons=["站上 VWAP"],
        risk_reasons=[],
    )
    base.update(overrides)
    return LongCandidate(**base)


def opening(direction="偏多確認"):
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
    def test_display_trade_bias_label_only_marks_executable_as_radar_passed(self):
        self.assertEqual(_display_trade_bias_label("executable", "long", "買多"), "進場雷達通過")
        self.assertEqual(_display_trade_bias_label("wait_volume", "long", "買多"), "買多")
        self.assertEqual(_display_trade_bias_label("wait_vwap", "long", ""), "買多")
        self.assertEqual(_display_trade_bias_label("high_risk", "long", "買多"), "方向偏多")
        self.assertEqual(_display_trade_bias_label("practice_long", "long", "買多"), "練習買多")

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
        signal = opening("偏多確認")

        label, score, reasons = bullish_profile(item, signal, sector=None, ranking=None)

        self.assertIn(label, {"偏多", "方向偏多"})
        self.assertGreaterEqual(score, 5)
        self.assertIn("盤前做多", reasons)

    def test_focus_table_surfaces_today_bullish_label(self):
        rows = build_tracked_symbols(
            symbols=[candidate()],
            candidates=[candidate()],
            opening_signals=[opening("偏多確認")],
            sector_strengths=[],
        )

        html = _bullish_focus_table(rows)

        self.assertIn("方向偏多", html)
        self.assertIn("偏多理由", html)
        self.assertIn("可進場", html)
        self.assertIn("站上VWAP", html)

    def test_tracked_table_labels_high_risk_as_no_chase_not_long_confirmation(self):
        rows = build_tracked_symbols(
            symbols=[candidate(shares=0)],
            candidates=[candidate(shares=0)],
            opening_signals=[opening("偏多確認")],
            sector_strengths=[],
        )

        html = _tracked_table(rows)

        self.assertIn("方向偏多", html)
        self.assertIn("追價風險高", html)
        self.assertIn("不列入今日做多", html)
        self.assertIn("避免追價", html)
        self.assertNotIn("強烈看漲", html)
        self.assertNotIn("做多確認", html)
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
        self.assertIn("進場雷達通過", html)
        self.assertIn("wait_volume 等量能", html)
        self.assertIn("high_risk 風險過高", html)
        self.assertIn("avoid 暫不追蹤", html)
        self.assertIn("已寫入 recommendations", html)
        self.assertIn("observed 觀察中", html)
        self.assertIn("triggered 已觸發", html)
        self.assertIn("今日已觸發回測數量", html)
        self.assertIn("<strong>4</strong>", html)

    def test_backtest_table_surfaces_signal_type_performance(self):
        summary = LongModelSummary(
            candidates=[],
            alerts=[],
            sector_heat=[],
            market_state="偏多",
            market_notes=[],
            backtest={
                "recommendation_count": 1,
                "trackable_count": 1,
                "triggered_backtest_count": 1,
                "avg_return": 1.2,
                "by_signal_type": [
                    {
                        "signal_type": "vwap_pullback",
                        "total": 1,
                        "triggered": 1,
                        "target": 1,
                        "stop": 0,
                        "win_rate": 100,
                        "avg_return": 1.2,
                        "avg_max_gain": 2.3,
                        "avg_max_drawdown": -0.5,
                    }
                ],
                "by_time_bucket": [
                    {
                        "time_bucket": "main_entry",
                        "total": 1,
                        "triggered": 1,
                        "target": 1,
                        "stop": 0,
                        "win_rate": 100,
                        "avg_return": 1.2,
                        "avg_max_gain": 2.3,
                        "avg_max_drawdown": -0.5,
                    }
                ],
            },
            recommendation_checklist={},
        )

        html = _backtest_table(summary)

        self.assertIn("依訊號型態回測", html)
        self.assertIn("VWAP 回測買點", html)
        self.assertIn("依時間區間回測", html)
        self.assertIn("主進場區 09:20-10:30", html)
        self.assertIn("100.00%", html)

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

    def test_user_facing_status_copy_avoids_buy_recommendation_language(self):
        combined = " ".join(
            [
                _entry_status_message("executable"),
                _entry_status_message("high_risk"),
                _grade_label("A"),
                _grade_label("B+"),
            ]
        )

        self.assertIn("進場雷達重點檢查", combined)
        self.assertIn("強勢重點盯盤", combined)
        for forbidden in ["強烈看漲", "做多確認", "買多推薦", "可執行做多", "強勢做多觀察", "可列入做多觀察"]:
            self.assertNotIn(forbidden, combined)

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
                        "trade_bias_label": "可執行",
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
                        "trade_bias_label": "練習買多",
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
        self.assertIn("明日優先觀察", html)
        self.assertNotIn("正式買多", html)
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

    def test_focus_card_tolerates_missing_trade_bias_label(self):
        item = LongCandidate(
            symbol="2330.TW",
            name="台積電",
            sector="semiconductor",
            last_price=100,
            change_pct=1.2,
            volume=1_000_000,
            turnover=100_000_000,
            avg_volume_20=900_000,
            daily_volume_ratio=1.1,
            intraday_volume=500_000,
            volume_ratio=1.2,
            vwap=99.5,
            above_vwap=True,
            previous_high=99,
            high_5d=101,
            high_10d=102,
            break_prev_high=True,
            break_5d_high=False,
            break_10d_high=False,
            upper_shadow_pct=0.1,
            institutional_buy_million=None,
            margin_balance=None,
            short_balance=None,
            daytrade_ratio=None,
            sector_strength=1,
            news_topics=[],
            market_state="偏多",
            bullish_score=72,
            risk_score=35,
            grade="B+",
            entry_status="wait_volume",
            original_entry_status="wait_volume",
            adjusted_entry_status="wait_volume",
            confidence_score=65,
            confidence_level="medium",
            confidence_level_label="中等信心",
            conflicts_count=0,
            conflicts=[],
            conflict_summary="",
            confidence_summary="資料完整度尚可。",
            confidence_adjustment_reason="",
            trade_bias="watch",
            trade_bias_label=None,
            trade_bias_reason="",
            trigger_price=101,
            stop_loss=98,
            target_price=103,
            opening_range_high=100.5,
            opening_range_low=98.5,
            reasons=["站上 VWAP"],
            risk_reasons=[],
        )

        html = _focus_card(item)

        self.assertIn("2330.TW｜台積電", html)
        self.assertIn("等待量能", html)
        self.assertIn("站上 VWAP", html)
        self.assertIn("真假突破", html)
        self.assertIn("position-size-tag", html)
        self.assertIn('data-position-entry="101"', html)
        self.assertIn('data-position-stop="98"', html)

    def test_focus_card_shows_entry_radar_blocker_and_next_trigger(self):
        item = LongCandidate(
            symbol="2886.TW",
            name="兆豐金",
            sector="finance",
            last_price=40,
            change_pct=0.8,
            volume=1_000_000,
            turnover=40_000_000,
            avg_volume_20=900_000,
            daily_volume_ratio=1.1,
            intraday_volume=500_000,
            volume_ratio=0.72,
            vwap=39.8,
            above_vwap=True,
            previous_high=40.5,
            high_5d=41,
            high_10d=42,
            break_prev_high=False,
            break_5d_high=False,
            break_10d_high=False,
            upper_shadow_pct=0.1,
            institutional_buy_million=None,
            margin_balance=None,
            short_balance=None,
            daytrade_ratio=None,
            sector_strength=1,
            news_topics=[],
            market_state="偏多",
            bullish_score=68,
            risk_score=35,
            grade="B",
            entry_status="wait_volume",
            original_entry_status="wait_volume",
            adjusted_entry_status="wait_volume",
            confidence_score=62,
            confidence_level="medium",
            confidence_level_label="中等信心",
            conflicts_count=0,
            conflicts=[],
            conflict_summary="",
            confidence_summary="資料完整度尚可。",
            confidence_adjustment_reason="",
            trade_bias="long",
            trade_bias_label="做多",
            trade_bias_reason="",
            trigger_price=40.5,
            stop_loss=39,
            target_price=42,
            opening_range_high=40.2,
            opening_range_low=39.2,
            reasons=["站上 VWAP"],
            risk_reasons=[],
        )

        html = _focus_card(item, {"market_mode": "intraday", "intraday": True})

        self.assertIn("最大卡關", html)
        self.assertIn("真假突破", html)
        self.assertIn("量比 0.72x", html)
        self.assertIn("量比放大到 1.0x", html)

    def test_signal_center_uses_market_mode_context_for_non_intraday(self):
        item = LongCandidate(
            symbol="1216.TW",
            name="統一",
            sector="food",
            last_price=80,
            change_pct=-0.5,
            volume=1_000_000,
            turnover=80_000_000,
            avg_volume_20=900_000,
            daily_volume_ratio=1.0,
            intraday_volume=300_000,
            volume_ratio=1.1,
            vwap=81,
            above_vwap=False,
            previous_high=82,
            high_5d=83,
            high_10d=84,
            break_prev_high=False,
            break_5d_high=False,
            break_10d_high=False,
            upper_shadow_pct=0.2,
            institutional_buy_million=None,
            margin_balance=None,
            short_balance=None,
            daytrade_ratio=None,
            sector_strength=0,
            news_topics=[],
            market_state="中性",
            bullish_score=30,
            risk_score=45,
            grade="D",
            entry_status="avoid",
            original_entry_status="avoid",
            adjusted_entry_status="avoid",
            confidence_score=50,
            confidence_level="low",
            confidence_level_label="低信心",
            conflicts_count=0,
            conflicts=[],
            conflict_summary="",
            confidence_summary="資料完整度尚可。",
            confidence_adjustment_reason="",
            trade_bias="short",
            trade_bias_label="看空",
            trade_bias_reason="跌破 VWAP",
            trigger_price=82,
            stop_loss=78,
            target_price=85,
            opening_range_high=81.5,
            opening_range_low=79.5,
            reasons=["跌破 VWAP"],
            risk_reasons=[],
        )
        summary = LongModelSummary(
            candidates=[item],
            alerts=[],
            sector_heat=[],
            market_state="休市",
            market_notes=[],
            backtest={},
            recommendation_checklist={},
            diagnostics={"data_health": {"data_date": "2026-01-02", "latest_intraday_at": "2026-01-02T13:30:00+08:00"}},
        )

        html = _signal_center(summary, datetime(2026, 1, 3, 10, 0))

        self.assertIn("觀察（1）", html)
        self.assertIn("看空（0）", html)
        self.assertIn("目前不是盤中", html)

    def test_trend_continuation_panel_displays_watch_items(self):
        item = LongCandidate(
            symbol="2330.TW",
            name="台積電",
            sector="semiconductor",
            last_price=100,
            change_pct=1.2,
            volume=1_000_000,
            turnover=100_000_000,
            avg_volume_20=900_000,
            daily_volume_ratio=1.1,
            intraday_volume=500_000,
            volume_ratio=1.2,
            vwap=99.5,
            above_vwap=True,
            previous_high=99,
            high_5d=101,
            high_10d=102,
            break_prev_high=True,
            break_5d_high=False,
            break_10d_high=False,
            upper_shadow_pct=0.1,
            institutional_buy_million=None,
            margin_balance=None,
            short_balance=None,
            daytrade_ratio=None,
            sector_strength=1,
            news_topics=[],
            market_state="偏多",
            bullish_score=72,
            risk_score=35,
            grade="B+",
            entry_status="practice_long",
            original_entry_status="practice_long",
            adjusted_entry_status="practice_long",
            confidence_score=65,
            confidence_level="medium",
            confidence_level_label="中等信心",
            conflicts_count=0,
            conflicts=[],
            conflict_summary="",
            confidence_summary="資料完整度尚可。",
            confidence_adjustment_reason="",
            trade_bias="watch",
            trade_bias_label="練習買多",
            trade_bias_reason="",
            trigger_price=101,
            stop_loss=98,
            target_price=103,
            opening_range_high=100.5,
            opening_range_low=98.5,
            reasons=["站上 VWAP"],
            risk_reasons=[],
            timeframe_diagnostics={
                "intraday_window": {
                    "higher_high": True,
                    "higher_low": True,
                    "vwap_above_minutes": 20,
                    "pullback_depth_pct": 0.8,
                    "volume_continuation": True,
                }
            },
            trend_diagnosis={
                "status": "trend_continuation_watch",
                "label": "做多｜趨勢延續觀察",
                "summary": "盤中曲線具備趨勢延續特徵。",
                "next_step": "等待即時確認。",
            },
            trend_status="trend_continuation_watch",
            trend_label="做多｜趨勢延續觀察",
            trend_reason_code="trend_continuation_watch",
        )
        summary = LongModelSummary(
            candidates=[item],
            alerts=[],
            sector_heat=[],
            market_state="偏多",
            market_notes=[],
            backtest={},
            recommendation_checklist={},
            diagnostics={
                "strategy_scorecard": {
                    "windows": {
                        "20": {
                            "trend_continuation": {
                                "message": "趨勢延續樣本不足，不建議調整模型。"
                            }
                        }
                    }
                }
            },
        )

        html = _trend_continuation_panel(summary, datetime(2026, 6, 18, 10, 0))

        self.assertIn("趨勢延續觀察", html)
        self.assertIn("2330.TW｜台積電", html)
        self.assertIn("趨勢延續樣本不足", html)

    def test_manual_scan_input_is_global_stock_search(self):
        from stock_daytrade_system.tracker import _manual_scan_panel

        html = _manual_scan_panel()

        self.assertIn("tw-scan-symbol", html)
        self.assertIn("data-stock-search", html)

    def test_entry_radar_scorecard_panel_renders_blocker_stats(self):
        summary = LongModelSummary(
            candidates=[],
            alerts=[],
            sector_heat=[],
            market_state="偏多",
            market_notes=[],
            backtest={},
            recommendation_checklist={},
            diagnostics={
                "entry_radar_scorecard": {
                    "windows": {
                        "20": {
                            "sample_size": 2,
                            "verified": 2,
                            "sample_quality": "insufficient",
                            "is_statistically_meaningful": False,
                            "message": "樣本不足，不建議依卡關原因調整模型。",
                            "rows": [
                                {
                                    "blocker_code": "low_volume_ratio",
                                    "blocker_label": "量比不足",
                                    "sample_size": 2,
                                    "verified": 2,
                                    "win_rate": 50,
                                    "target_2_rate": 0,
                                    "avg_max_gain": 1.1,
                                    "avg_max_drawdown": -0.5,
                                    "interpretation": "樣本不足，先累積資料，不建議調整模型。",
                                }
                            ],
                        }
                    }
                }
            },
        )

        html = _entry_radar_scorecard_panel(summary)

        self.assertIn("最大卡關", html)
        self.assertIn("量比不足", html)
        self.assertIn("1.10%", html)
        self.assertIn("不會自動調整 A / B+ / B 條件", html)

    def test_fugle_priority_pool_panel_renders_selected_symbols(self):
        summary = LongModelSummary(
            candidates=[],
            alerts=[],
            sector_heat=[],
            market_state="偏多",
            market_notes=[],
            backtest={},
            recommendation_checklist={},
            diagnostics={
                "fugle_priority_pool": {
                    "enabled": True,
                    "configured": True,
                    "entry_radar_status": "ok",
                    "max_symbols": 5,
                    "selected_count": 2,
                    "excluded_count": 3,
                    "actual_api_calls": 6,
                    "api_budget_message": "Fugle 雷達本次 6 次 API 呼叫；若每 5 分鐘刷新，估計 1.2/min，基本限制 60/min，狀態：安全。",
                    "entry_radar_health": {
                        "operator_status": "ready",
                        "success_count": 2,
                        "failed_count": 0,
                        "skipped_count": 0,
                        "tracking_limit": 5,
                        "estimated_calls_per_minute": 1.2,
                        "api_budget_status": "safe",
                        "can_use_for_entry_confirmation": True,
                        "next_action": "逐檔確認五檔、逐筆、大單、價格墊高與 VWAP 後再行動。",
                    },
                    "capability_summary": {
                        "plan": "basic",
                        "websocket_subscription_limit": 5,
                        "rest_calls_per_minute": 60,
                        "summary": "Fugle basic 方案：最多追蹤 5 檔、REST 約 60/min；不支援日內快照，不支援技術指標 API。",
                        "trading_note": "Fugle 只作行情確認；本系統不串券商下單，也不自動下單。",
                    },
                    "pinned_symbols": ["6919.TW"],
                    "message": "已依基本用戶 5 檔限制挑選即時追蹤標的。",
                    "selection_explanation": "本次從 5 檔重點候選中，依進場接近度、風險可控度、量能與使用者指定，挑出前 2 / 5 檔。",
                    "next_candidate_symbol": "2330.TW",
                    "next_candidate_gap": 80,
                    "allocation_summary": {
                        "summary": "練習買多 1 檔、高風險觀察 1 檔",
                        "warning": "高風險標的只作風險降溫觀察，不作進場確認。",
                    },
                    "standby": [
                        {
                            "symbol": "2330.TW",
                            "name": "台積電",
                            "entry_status": "wait_breakout",
                            "tracking_purpose": "確認是否突破觸發價",
                            "priority_score": 720,
                            "priority_reason": "等待突破，需追蹤觸發價",
                            "not_selected_reason": "Fugle 基本用戶名額 5 檔已滿，目前列為候補。",
                            "promotion_condition": "接近或突破觸發價，且前 5 檔有標的失效時可升入。",
                        }
                    ],
                    "selected": [
                        {
                            "symbol": "2884.TW",
                            "name": "玉山金",
                            "grade": "B+",
                            "entry_status": "practice_long",
                            "trigger_readiness": "near",
                            "last_price": 32.1,
                            "vwap": 31.9,
                            "volume_ratio": 1.1,
                            "trigger_price": 32.3,
                            "stop_loss": 31.4,
                            "priority_score": 955,
                            "selection_label": "第 1 優先追蹤",
                            "selection_reason": "屬練習買多或 B+ 觀察，適合用即時盤口累積樣本。",
                            "watch_now": "看進場雷達是否從觀察轉為可考慮，並只用虛擬交易練習。",
                            "can_use_for_entry_confirmation": True,
                            "entry_confirmation_can_consider": True,
                            "confirmation_quality": "high_precision",
                            "confirmation_quality_label": "高品質確認",
                            "confirmation_quality_reason": "VWAP、量能、停損距離、五檔、逐筆與價格墊高都可檢查。",
                            "orderbook_status": "supportive",
                            "large_trade_status": "buy_sweep",
                            "price_tick_trend": "rising",
                            "bid_volume_trend": "stable",
                            "tracking_purpose": "虛擬交易練習觀察",
                            "priority_reason": "練習買多，適合即時觀察",
                        },
                        {
                            "symbol": "6919.TW",
                            "name": "康霈生技",
                            "grade": "C",
                            "entry_status": "high_risk",
                            "trigger_readiness": "-",
                            "last_price": 109,
                            "vwap": 106.9,
                            "volume_ratio": 5.4,
                            "priority_score": 800,
                            "selection_label": "第 2 優先追蹤",
                            "selection_reason": "高風險標的只用來觀察風險是否降溫，不作進場確認。",
                            "watch_now": "看是否拉回 VWAP 附近、停損距離縮小，且大單敲出減少。",
                            "can_use_for_entry_confirmation": False,
                            "entry_confirmation_can_consider": False,
                            "confirmation_quality": "limited",
                            "confirmation_quality_label": "確認資料不足",
                            "confirmation_quality_reason": "缺逐筆成交，不能作高精準進場確認。",
                            "orderbook_status": "supportive",
                            "large_trade_status": "missing",
                            "price_tick_trend": "stable",
                            "bid_volume_trend": "missing",
                            "ask_volume_trend": "missing",
                            "tracking_purpose": "只追蹤風險降溫，不作進場",
                            "priority_reason": "使用者指定即時追蹤；高風險只追蹤風險變化，不列為進場",
                        },
                    ],
                }
            },
        )

        html = _fugle_priority_pool_panel(summary)

        self.assertIn("Fugle 5檔即時追蹤池", html)
        self.assertIn("2884.TW｜玉山金", html)
        self.assertIn("基本用戶最多追蹤 5 檔", html)
        self.assertIn("不會改 A / B+ / B 條件", html)
        self.assertIn("雷達狀態", html)
        self.assertIn("實際 API 呼叫", html)
        self.assertIn("API 預算", html)
        self.assertIn("估計 1.2/min", html)
        self.assertIn("追蹤池健康", html)
        self.assertIn("可用於進場前確認", html)
        self.assertIn("可做完整確認", html)
        self.assertIn("API 預算安全", html)
        self.assertIn("追蹤 2 / 5 檔", html)
        self.assertIn("逐檔確認五檔", html)
        self.assertIn("為什麼選這些", html)
        self.assertIn("依進場接近度", html)
        self.assertIn("下一候補：2330.TW", html)
        self.assertIn("方案能力", html)
        self.assertIn("不支援日內快照", html)
        self.assertIn("不自動下單", html)
        self.assertIn("名額配置", html)
        self.assertIn("練習買多 1 檔", html)
        self.assertIn("高風險標的只作風險降溫觀察", html)
        self.assertIn("盤中驗收重點", html)
        self.assertIn("追蹤池 2 / 5 檔", html)
        self.assertIn("本次實際 API 呼叫 6 次", html)
        self.assertIn("指定追蹤已入池：6919.TW", html)
        self.assertIn("風險防線正常：6919.TW", html)
        self.assertIn("Fugle 雷達速讀", html)
        self.assertIn("確認品質", html)
        self.assertIn("高品質確認", html)
        self.assertIn("確認資料不足", html)
        self.assertIn("Fugle 名額外候補", html)
        self.assertIn("2330.TW｜台積電", html)
        self.assertIn("即時 API 資源不足", html)
        self.assertIn("2884.TW｜玉山金", html)
        self.assertIn("第 1 優先追蹤", html)
        self.assertIn("現在看：看進場雷達是否從觀察轉為可考慮", html)
        self.assertIn("可做進場前確認", html)
        self.assertIn("6919.TW｜康霈生技", html)
        self.assertIn("第 2 優先追蹤", html)
        self.assertIn("僅作風險觀察", html)
        self.assertIn("主要缺口：缺逐筆、追價風險高", html)
        self.assertIn("升入條件：接近或突破觸發價", html)
        self.assertIn("可做盤口確認", html)

    def test_fugle_priority_pool_warns_when_api_is_not_configured(self):
        summary = LongModelSummary(
            candidates=[],
            alerts=[],
            sector_heat=[],
            market_state="偏多",
            market_notes=[],
            backtest={},
            recommendation_checklist={},
            diagnostics={
                "fugle_priority_pool": {
                    "enabled": False,
                    "configured": False,
                    "entry_radar_status": "waiting",
                    "entry_radar_health": {
                        "operator_status": "not_ready",
                        "success_count": 0,
                        "failed_count": 1,
                        "skipped_count": 0,
                        "tracking_limit": 5,
                        "estimated_calls_per_minute": 0,
                        "api_budget_status": "safe",
                        "can_use_for_entry_confirmation": False,
                        "next_action": "先完成 Fugle 設定；設定前只看原模型與資料可信度。",
                    },
                    "max_symbols": 5,
                    "selected_count": 1,
                    "selected": [
                        {
                            "symbol": "2884.TW",
                            "name": "玉山金",
                            "grade": "B+",
                            "entry_status": "practice_long",
                            "last_price": 32.1,
                            "vwap": 31.9,
                            "volume_ratio": 1.1,
                            "priority_score": 900,
                            "tracking_purpose": "虛擬交易練習觀察",
                            "priority_reason": "練習買多，適合即時觀察",
                            "can_use_for_entry_confirmation": True,
                        }
                    ],
                }
            },
        )

        html = _fugle_priority_pool_panel(summary)

        self.assertIn("Fugle 尚未完整啟用或 API Key 未設定", html)
        self.assertIn("尚未取得即時五檔 / 逐筆成交確認", html)
        self.assertIn("不可當成進場依據", html)
        self.assertIn("追蹤池健康", html)
        self.assertIn("尚未可用", html)
        self.assertIn("不可直接作進場確認", html)
        self.assertIn("先完成 Fugle 設定", html)

    def test_data_status_block_explains_success_and_exclusion(self):
        html = _data_status_block(["盤中行情成功 20/21；失敗標的不納入 VWAP、量比與盤中回測。"])

        self.assertIn("資料狀態", html)
        self.assertIn("失敗標的不納入", html)

    def test_holiday_dashboard_panel_uses_closed_review_copy(self):
        summary = LongModelSummary(
            candidates=[],
            alerts=[],
            sector_heat=[],
            market_state="偏多",
            market_notes=[],
            backtest={},
            recommendation_checklist={},
            diagnostics={
                "data_health": {
                    "status": "過期",
                    "data_date": "2026-06-18",
                    "latest_intraday_at": "2026-06-18T13:30:00+08:00",
                    "is_stale": True,
                }
            },
        )

        html = _market_mode_panel(summary, datetime(2026, 6, 19, 9, 20))

        self.assertIn("休市復盤模式", html)
        self.assertIn("以下顯示上一交易日資料", html)
        self.assertIn("休市復盤：使用上一交易日資料", html)
        self.assertIn("現在要做", html)
        self.assertIn("休市只做復盤與下個交易日準備", html)
        self.assertIn("模式細節", html)
        self.assertNotIn("資料異常模式", html)
        self.assertNotIn("資料已過期或缺漏嚴重", html)

    def test_pre_open_decision_overview_uses_actionable_copy_not_debug_counts(self):
        summary = LongModelSummary(
            candidates=[],
            alerts=[],
            sector_heat=[],
            market_state="偏多",
            market_notes=[],
            backtest={},
            recommendation_checklist={},
            decision_center={"counts": {}, "confidence_summary": {}},
            diagnostics={
                "data_health": {
                    "status": "部分缺漏",
                    "data_date": "2026-06-24",
                    "latest_intraday_at": "2026-06-24T13:30:00+08:00",
                },
                "strong_long_funnel": {
                    "top_blockers": [
                        {"reason": "使用上一筆", "count": 118},
                        {"reason": "資料不是今天", "count": 118},
                    ]
                },
            },
        )

        html = _decision_overview(summary, datetime(2026, 6, 25, 8, 50))

        self.assertIn("開盤前準備模式", html)
        self.assertIn("請等開盤後確認今日 VWAP、量比、突破與進場雷達", html)
        self.assertIn("開盤前重點盯盤 5 檔", html)
        self.assertIn("開盤後等待確認清單 10 檔", html)
        self.assertNotIn("買多觀察池 10 檔", html)
        self.assertNotIn("使用上一筆 118 檔", html)

    def test_decision_overview_explains_front_category_reasons(self):
        summary = LongModelSummary(
            candidates=[
                long_candidate(
                    symbol="1216.TW",
                    name="統一",
                    entry_status="avoid",
                    original_entry_status="avoid",
                    adjusted_entry_status="avoid",
                    trade_bias="short",
                    trade_bias_label="看空",
                    above_vwap=False,
                    vwap=101,
                    last_price=99,
                    reasons=["跌破 VWAP"],
                ),
                long_candidate(
                    symbol="2886.TW",
                    name="兆豐金",
                    entry_status="wait_vwap",
                    original_entry_status="wait_vwap",
                    adjusted_entry_status="wait_vwap",
                    above_vwap=False,
                    vwap=39.8,
                    last_price=39.6,
                    reasons=["尚未站上 VWAP"],
                ),
            ],
            alerts=[],
            sector_heat=[],
            market_state="偏多",
            market_notes=[],
            backtest={},
            recommendation_checklist={},
            decision_center={"counts": {}, "confidence_summary": {}},
            diagnostics={
                "data_health": {
                    "status": "正常",
                    "data_date": "2026-06-25",
                    "latest_intraday_at": "2026-06-25T10:00:00+08:00",
                }
            },
        )

        html = _decision_overview(summary, datetime(2026, 6, 25, 10, 0))

        self.assertIn("四分類原因診斷", html)
        self.assertIn("看空 1 檔", html)
        self.assertIn("觀察 1 檔", html)
        self.assertIn("多方失效 / 避開", html)
        self.assertIn("未站上 VWAP", html)
        self.assertIn("排查順序", html)
        self.assertIn("多數股票未站上 VWAP", html)
        self.assertIn("這不是做空建議", html)

    def test_decision_overview_surfaces_limit_up_strength_brief(self):
        summary = LongModelSummary(
            candidates=[],
            alerts=[],
            sector_heat=[],
            market_state="偏多",
            market_notes=[],
            backtest={},
            recommendation_checklist={},
            decision_center={"counts": {}, "confidence_summary": {}},
            diagnostics={
                "data_health": {
                    "status": "正常",
                    "data_date": "2026-06-30",
                    "latest_intraday_at": "2026-06-30T10:00:00+08:00",
                },
                "strong_long_funnel": {
                    "strong_long_candidate_count": 0,
                    "executable_count": 0,
                    "top_blockers": [{"reason": "high_risk", "count": 8}],
                },
                "limit_up_strength_analysis": {
                    "near_limit_up_count": 12,
                    "seen_count": 12,
                    "entered_ai_count": 0,
                    "high_risk_count": 9,
                    "missed_by_pool_count": 0,
                    "data_missing_count": 0,
                    "market_phase": "broad_limit_wave_chase_risk",
                    "market_phase_label": "漲停潮但追價風險主導",
                    "market_phase_summary": "今天有 12 檔接近漲停 / 漲停，且多數被列為追價高風險；盤面很熱，但不代表適合直接追。",
                    "operator_priority": "先挑已看到但 high_risk 的股票，等拉回 VWAP 附近不破、停損距離縮小，再重新評估。",
                },
            },
        )

        html = _decision_overview(summary, datetime(2026, 6, 30, 10, 0))

        self.assertIn("漲停強勢速讀", html)
        self.assertIn("漲停潮但追價風險主導", html)
        self.assertIn("操作優先順序", html)
        self.assertIn("接近漲停 / 漲停", html)
        self.assertIn("漲停高風險觀察", html)
        self.assertIn("漲停真漏抓", html)
        self.assertIn("已看到的高風險股不等於看空，而是避免追價", html)
        self.assertIn("其中 9 檔被列為追價高風險", html)
        self.assertIn("現在先做", html)
        self.assertIn("等到什麼", html)
        self.assertIn("不要做", html)
        self.assertIn("拉回 VWAP", html)
        self.assertIn("不要在漲停附近直接追價", html)

    def test_decision_overview_infers_limit_up_brief_from_momentum_scan(self):
        summary = LongModelSummary(
            candidates=[],
            alerts=[],
            sector_heat=[],
            market_state="偏多",
            market_notes=[],
            backtest={},
            recommendation_checklist={},
            decision_center={"counts": {}, "confidence_summary": {}},
            momentum_scan={
                "items": [
                    {
                        "symbol": "2434.TW",
                        "name": "統懋",
                        "change_pct": 9.8,
                        "entry_status": "high_risk",
                        "not_selected_reason": "前日爆量漲停，追價風險高",
                    },
                    {
                        "symbol": "3026.TW",
                        "name": "禾伸堂",
                        "change_pct": 6.7,
                        "entry_status": "wait_breakout",
                        "source_reasons": ["爆量漲停後續強觀察"],
                        "ai_grade": "B+",
                    },
                ]
            },
            diagnostics={
                "data_health": {
                    "status": "正常",
                    "data_date": "2026-06-30",
                    "latest_intraday_at": "2026-06-30T10:00:00+08:00",
                }
            },
        )

        html = _decision_overview(summary, datetime(2026, 6, 30, 14, 30))

        self.assertIn("漲停強勢速讀", html)
        self.assertIn("接近漲停 / 漲停", html)
        self.assertIn("2</strong>", html)
        self.assertIn("今日/上一交易日有 2 檔接近漲停或急拉", html)
        self.assertIn("這不是即時買多", html)
        self.assertIn("不會把追價高風險股票升級成買多", html)

    def test_limit_up_strength_panel_shows_action_split_and_next_step(self):
        summary = LongModelSummary(
            candidates=[],
            alerts=[],
            sector_heat=[],
            market_state="偏多",
            market_notes=[],
            backtest={},
            recommendation_checklist={},
            decision_center={"counts": {}, "confidence_summary": {}},
            diagnostics={
                "limit_up_strength_analysis": {
                    "definition": "此區只診斷漲停或接近漲停強勢股；不會把追價高風險股票升級成買多。",
                    "near_limit_up_count": 2,
                    "seen_count": 2,
                    "entered_ai_count": 0,
                    "locked_count": 1,
                    "chase_risk_count": 1,
                    "wait_confirm_count": 0,
                    "high_risk_count": 1,
                    "avoid_count": 0,
                    "data_missing_count": 0,
                    "missed_by_pool_count": 0,
                    "not_buy_reason": "接近漲停不直接顯示買多。",
                    "action_summary": "1 檔鎖漲停先觀察；1 檔追價風險高。",
                    "market_phase": "locked_limit_watch",
                    "market_phase_label": "鎖漲停觀察盤",
                    "market_phase_summary": "目前有 1 檔鎖漲停或買盤堆積；鎖住代表強，但不是追價理由。",
                    "operator_priority": "只記錄與觀察，等打開後看 VWAP 是否守住、買盤是否延續。",
                    "rows": [
                        {
                            "symbol": "8150.TW",
                            "name": "南茂",
                            "latest_at": "2026-06-30T09:35:00+08:00",
                            "limit_up_status": "接近漲停",
                            "change_pct": 9.8,
                            "latest_price": 58.5,
                            "volume_ratio": 4.2,
                            "above_vwap": True,
                            "break_prev_high": True,
                            "ai_grade": "C",
                            "entry_status": "high_risk",
                            "limit_up_decision": "有看到，但追價風險高",
                            "limit_up_explanation": "強勢但追價風險高，不列入今日做多。",
                            "limit_up_now_action": "放進觀察，不直接追漲停。",
                            "limit_up_wait_for": "等待拉回 VWAP 附近不破、停損距離縮小。",
                            "reason_code": "high_chase_risk",
                        }
                    ],
                }
            },
        )

        html = _limit_up_strength_panel(summary)

        self.assertIn("漲停盤面", html)
        self.assertIn("鎖漲停觀察盤", html)
        self.assertIn("操作優先順序", html)
        self.assertIn("鎖漲停觀察", html)
        self.assertIn("追價風險", html)
        self.assertIn("下一步", html)
        self.assertIn("不直接追漲停", html)
        self.assertIn("等待拉回 VWAP", html)

    def test_limit_up_strength_panel_falls_back_to_momentum_scan(self):
        summary = LongModelSummary(
            candidates=[],
            alerts=[],
            sector_heat=[],
            market_state="偏多",
            market_notes=[],
            backtest={},
            recommendation_checklist={},
            decision_center={"counts": {}, "confidence_summary": {}},
            momentum_scan={
                "items": [
                    {
                        "symbol": "2434.TW",
                        "name": "統懋",
                        "change_pct": 9.8,
                        "entry_status": "high_risk",
                        "not_selected_reason": "前日爆量漲停，追價風險高",
                    }
                ]
            },
            diagnostics={},
        )

        html = _limit_up_strength_panel(summary)

        self.assertIn("接近漲停 / 漲停", html)
        self.assertIn("2434.TW", html)
        self.assertIn("有看到，但追價風險高", html)
        self.assertIn("強勢但追價風險高，不列入今日做多", html)
        self.assertNotIn("目前沒有漲停強勢股診斷資料", html)

    def test_front_category_diagnostics_warns_when_bearish_ratio_is_abnormally_high(self):
        bearish_items = [
            long_candidate(
                symbol=f"999{i}.TW",
                name=f"測試{i}",
                entry_status="avoid",
                original_entry_status="avoid",
                adjusted_entry_status="avoid",
                above_vwap=False,
                vwap=100,
                last_price=98,
                reasons=["跌破 VWAP"],
            )
            for i in range(4)
        ]
        summary = LongModelSummary(
            candidates=bearish_items,
            alerts=[],
            sector_heat=[],
            market_state="偏多",
            market_notes=[],
            backtest={},
            recommendation_checklist={},
            diagnostics={
                "data_health": {
                    "status": "正常",
                    "data_date": "2026-06-25",
                    "latest_intraday_at": "2026-06-25T10:00:00+08:00",
                }
            },
        )

        html = _decision_overview(summary, datetime(2026, 6, 25, 10, 0))

        self.assertIn("分類異常警示", html)
        self.assertIn("這不是做空建議", html)
        self.assertIn("資料模式、VWAP 與價格狀態", html)

    def test_post_close_decision_overview_uses_review_and_next_session_copy(self):
        summary = LongModelSummary(
            candidates=[],
            alerts=[],
            sector_heat=[],
            market_state="偏多",
            market_notes=[],
            backtest={},
            recommendation_checklist={},
            decision_center={"counts": {}, "confidence_summary": {}},
            diagnostics={
                "data_health": {
                    "status": "部分缺漏",
                    "data_date": "2026-06-25",
                    "latest_intraday_at": "2026-06-25T13:30:00+08:00",
                }
            },
        )

        html = _decision_overview(summary, datetime(2026, 6, 25, 17, 0))

        self.assertIn("盤後復盤模式", html)
        self.assertIn("今日盤後復盤重點 5 檔", html)
        self.assertIn("下個交易日觀察清單 10 檔", html)
        self.assertNotIn("買多觀察池 10 檔", html)

    def test_pre_open_bearish_heavy_overview_explains_not_short_signal(self):
        bearish_items = [
            long_candidate(
                symbol=f"88{i}.TW",
                name=f"測試{i}",
                entry_status="avoid",
                original_entry_status="avoid",
                adjusted_entry_status="avoid",
                trade_bias="short",
                trade_bias_label="看空",
                above_vwap=False,
                vwap=100,
                last_price=98,
                volume_ratio=1.2,
                stop_loss=96,
                reasons=["跌破 VWAP"],
            )
            for i in range(5)
        ]
        summary = LongModelSummary(
            candidates=bearish_items,
            alerts=[],
            sector_heat=[],
            market_state="偏多",
            market_notes=[],
            backtest={},
            recommendation_checklist={},
            decision_center={"counts": {}, "confidence_summary": {}},
            diagnostics={
                "data_health": {
                    "status": "部分缺漏",
                    "data_date": "2026-06-24",
                    "latest_intraday_at": "2026-06-24T13:30:00+08:00",
                }
            },
        )

        html = _decision_overview(summary, datetime(2026, 6, 25, 8, 50))

        self.assertIn("開盤前準備模式", html)
        self.assertIn("非買多比例偏高", html)
        self.assertIn("不代表全市場都適合做空", html)
        self.assertIn("等待開盤確認", html)

    def test_today_playbook_pre_open_gives_three_step_action_plan(self):
        summary = LongModelSummary(
            candidates=[],
            alerts=[],
            sector_heat=[],
            market_state="偏多",
            market_notes=[],
            backtest={},
            recommendation_checklist={},
            diagnostics={
                "data_health": {
                    "status": "部分缺漏",
                    "data_date": "2026-06-24",
                    "latest_intraday_at": "2026-06-24T13:30:00+08:00",
                }
            },
        )

        html = _today_playbook_panel(summary, datetime(2026, 6, 25, 8, 50))

        self.assertIn("今日作戰流程", html)
        self.assertIn("開盤前作戰：先挑清單，不提前進場", html)
        self.assertIn("09:00 後先等 5 到 10 分鐘", html)
        self.assertIn("資料沒有 live 前，不做強烈買多判斷", html)

    def test_review_mode_sections_explain_how_to_use_before_open(self):
        summary = LongModelSummary(
            candidates=[],
            alerts=[],
            sector_heat=[],
            market_state="偏多",
            market_notes=[],
            backtest={},
            recommendation_checklist={},
            diagnostics={
                "data_health": {
                    "status": "部分缺漏",
                    "data_date": "2026-06-24",
                    "latest_intraday_at": "2026-06-24T13:30:00+08:00",
                }
            },
        )

        html = _review_mode_sections(summary, datetime(2026, 6, 25, 8, 50))

        self.assertIn("沒開盤時怎麼用？", html)
        self.assertIn("上一交易日復盤與下個交易日觀察清單", html)
        self.assertIn("09:00 後等 VWAP、量比、突破與進場雷達重新確認", html)

    def test_next_session_unified_watch_panel_splits_two_watchlists(self):
        summary = LongModelSummary(
            candidates=[
                long_candidate(
                    symbol="1304.TW",
                    name="台聚",
                    grade="C",
                    entry_status="high_risk",
                    bullish_score=80,
                    risk_score=70,
                    confidence_score=60,
                    risk_reasons=["追價風險高"],
                )
            ],
            alerts=[],
            sector_heat=[],
            market_state="偏多",
            market_notes=[],
            backtest={},
            recommendation_checklist={},
            momentum_scan={
                "items": [
                    {
                        "symbol": "3017.TW",
                        "name": "奇鋐",
                        "ai_grade": "D",
                        "entry_status": "wait_volume",
                        "trade_bias": "watch",
                        "change_pct": 0.73,
                        "turnover": 10_306_000_000,
                        "volume_ratio": 0.94,
                        "above_vwap": True,
                        "break_prev_high": True,
                        "break_5d_high": True,
                    },
                    {
                        "symbol": "5314.TWO",
                        "name": "世紀*",
                        "ai_grade": "D",
                        "entry_status": "high_risk",
                        "trade_bias": "long",
                        "change_pct": 10,
                        "turnover": 2_015_000_000,
                        "volume_ratio": 4.75,
                        "above_vwap": True,
                        "break_prev_high": True,
                        "break_5d_high": True,
                        "not_selected_reason": "強勢但追價風險高",
                    },
                ]
            },
            diagnostics={
                "data_health": {
                    "status": "部分缺漏",
                    "data_date": "2026-07-03",
                    "latest_intraday_at": "2026-07-03T13:30:00+08:00",
                }
            },
        )

        html = _next_session_unified_watch_panel(summary, datetime(2026, 7, 4, 22, 30))

        self.assertIn("下個交易日怎麼看", html)
        self.assertIn("A 組｜爆量 / 漲停續強觀察", html)
        self.assertIn("B 組｜大成交等待確認", html)
        self.assertIn("C 組｜強勢但高風險", html)
        self.assertIn("現在結論", html)
        self.assertIn("目前沒有即時強烈買多或買多訊號", html)
        self.assertIn("開盤後先盯", html)
        self.assertIn("現在不能直接買的原因", html)
        self.assertIn("開盤後轉買多條件", html)
        self.assertIn("使用方式", html)
        self.assertIn("1304.TW", html)
        self.assertIn("3017.TW", html)
        self.assertIn("5314.TWO", html)
        self.assertIn("週一升級條件", html)
        self.assertIn("兩組都不是買進名單", html)
        self.assertIn("把下方股票當成開盤後檢查清單", html)

    def test_today_playbook_opening_observation_warns_not_to_chase_first_move(self):
        summary = LongModelSummary(
            candidates=[],
            alerts=[],
            sector_heat=[],
            market_state="偏多",
            market_notes=[],
            backtest={},
            recommendation_checklist={"executable": 0},
            diagnostics={
                "data_health": {
                    "status": "正常",
                    "data_date": "2026-06-25",
                    "latest_intraday_at": "2026-06-25T09:05:00+08:00",
                }
            },
        )

        html = _today_playbook_panel(summary, datetime(2026, 6, 25, 9, 5))

        self.assertIn("開盤觀察 09:00-09:20", html)
        self.assertIn("先看量價，不急著進場", html)
        self.assertIn("形成開盤區間", html)
        self.assertIn("第一波急拉", html)
        self.assertIn("不追", html)

    def test_candidate_selection_explainer_shows_pool_flow_and_blockers(self):
        summary = LongModelSummary(
            candidates=[],
            alerts=[],
            sector_heat=[],
            market_state="偏多",
            market_notes=[],
            backtest={},
            recommendation_checklist={},
            diagnostics={
                "full_market_scan": {
                    "data": {
                        "pool_symbols": 1125,
                        "twse_count": 714,
                        "tpex_count": 411,
                        "candidate_symbols": 40,
                        "scored_symbols": 40,
                    },
                    "source_status": {"twse_ok": True, "tpex_ok": True},
                    "by_status": {"out_of_pool": 36, "high_risk": 9},
                },
                "strong_long_funnel": {
                    "momentum_candidate_count": 40,
                    "model_scored_count": 40,
                    "blocked_high_risk": 9,
                    "blocked_wait_volume": 8,
                    "blocked_wait_vwap": 5,
                    "blocked_wait_breakout": 4,
                    "strong_long_candidate_count": 2,
                    "executable_count": 1,
                },
            },
        )

        html = _candidate_selection_explainer(summary)

        self.assertIn("候選股怎麼選出來", html)
        self.assertIn("完整普通股池", html)
        self.assertIn("<strong>1125</strong>", html)
        self.assertIn("今日異動候選", html)
        self.assertIn("原觀察池外新找到", html)
        self.assertIn("常見卡關", html)
        self.assertIn("high_risk 9 檔", html)
        self.assertIn("Fugle 五檔與逐筆只作背景", html)
        self.assertIn("不會直接把股票升級成強烈買多", html)

    def test_candidate_selection_explainer_uses_full_market_summary_fallback(self):
        summary = LongModelSummary(
            candidates=[],
            alerts=[],
            sector_heat=[],
            market_state="偏多",
            market_notes=[],
            backtest={},
            recommendation_checklist={},
            momentum_scan={"summary": {"total": 40, "model_scored": 118}},
            debug_info={"full_market_pool_symbols": 1081, "full_market_candidate_symbols": 40},
            diagnostics={
                "full_market_scan": {
                    "summary": {
                        "pool_symbols": 1081,
                        "twse_count": 692,
                        "tpex_count": 389,
                        "candidate_symbols": 40,
                    },
                    "source_status": {"twse_ok": True, "tpex_ok": True},
                },
                "strong_long_funnel": {
                    "blocked_high_risk": 35,
                    "strong_long_candidate_count": 0,
                    "executable_count": 0,
                },
            },
        )

        html = _candidate_selection_explainer(summary)

        self.assertIn("完整普通股池", html)
        self.assertIn("<strong>1081</strong>", html)
        self.assertIn("<strong>692</strong>", html)
        self.assertIn("<strong>389</strong>", html)
        self.assertIn("送入模型評分", html)
        self.assertIn("<strong>118</strong>", html)
        self.assertIn("如果覺得某檔漏掉", html)
        self.assertIn("個股建議", html)
        self.assertIn("reason code", html)

    def test_candidate_selection_explainer_uses_current_strong_funnel_keys(self):
        summary = LongModelSummary(
            candidates=[],
            alerts=[],
            sector_heat=[],
            market_state="偏多",
            market_notes=[],
            backtest={},
            recommendation_checklist={},
            diagnostics={
                "full_market_scan": {
                    "data": {
                        "pool_symbols": 1125,
                        "twse_count": 714,
                        "tpex_count": 411,
                        "candidate_symbols": 40,
                    },
                    "source_status": {"twse_ok": True, "tpex_ok": True},
                },
                "strong_long_funnel": {
                    "momentum_candidate_count": 40,
                    "model_candidates_count": 40,
                    "blocked_high_risk_count": 12,
                    "blocked_wait_volume_count": 8,
                    "blocked_wait_vwap_count": 5,
                    "blocked_wait_breakout_count": 4,
                    "strong_long_candidate_count": 2,
                    "executable_count": 1,
                },
            },
        )

        html = _candidate_selection_explainer(summary)

        self.assertIn("送入模型評分", html)
        self.assertIn("<strong>40</strong>", html)
        self.assertIn("high_risk 12 檔", html)
        self.assertIn("wait_volume 8 檔", html)
        self.assertIn("wait_vwap 5 檔", html)
        self.assertIn("wait_breakout 4 檔", html)

    def test_strong_long_funnel_panel_shows_operator_action_plan(self):
        summary = LongModelSummary(
            candidates=[],
            alerts=[],
            sector_heat=[],
            market_state="偏多",
            market_notes=[],
            backtest={},
            recommendation_checklist={},
            diagnostics={
                "strong_long_funnel": {
                    "total_market_count": 1125,
                    "momentum_candidate_count": 40,
                    "model_candidates_count": 40,
                    "strong_long_candidate_count": 0,
                    "executable_count": 0,
                    "top_blockers": [
                        {"reason": "風險分數高於 55", "count": 12},
                        {"reason": "量比未達 1.0", "count": 8},
                    ],
                    "action_plan": [
                        {
                            "reason": "風險分數高於 55",
                            "count": 12,
                            "action": "先降追價風險。",
                            "wait_for": "等待拉回 VWAP 附近、停損距離縮小或長上影壓力解除。",
                            "avoid": "不要追高；high_risk 只能觀察。",
                        }
                    ],
                    "primary_action": "先降追價風險。",
                    "primary_wait_condition": "等待拉回 VWAP 附近、停損距離縮小或長上影壓力解除。",
                },
            },
        )

        html = _strong_long_funnel_panel(summary)

        self.assertIn("現在先做", html)
        self.assertIn("先降追價風險", html)
        self.assertIn("等到什麼", html)
        self.assertIn("卡關處理順序", html)
        self.assertIn("不要追高", html)
        self.assertIn("high_risk 只能觀察", html)

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
                "session_policy_version": "session_policy_v1_time_gated_entry_2026-06-18",
                "dashboard_generated_at": "2026-01-01T09:05:00",
                "recommendations_count_from_db": 2,
                "candidates_count_from_current_run": 17,
                "visible_candidates_count": 12,
            },
            diagnostics={
                "data_health": {
                    "status": "部分缺漏",
                    "recommendation_state": "目前資料不完整，僅供觀察，不建議交易",
                    "stock_pool_count": 30,
                    "daily_success_count": 29,
                    "daily_failed_count": 1,
                    "intraday_success_count": 28,
                    "intraday_failed_count": 2,
                    "latest_intraday_at": "2026-01-01T09:05:00+08:00",
                    "age_minutes": 3,
                    "is_intraday_session": True,
                    "is_today_data": True,
                    "failed_symbols": ["9999.TW"],
                    "symbol_not_found_count": 1,
                    "symbol_not_found_symbols": ["6485.TW"],
                    "yahoo_proxy_unavailable_count": 1,
                    "yahoo_proxy_unavailable_symbols": ["TX=F"],
                    "unavailable_symbols_message": "1 檔 Yahoo 無資料或代號不存在，已排除評分；1 個 Yahoo 代理商品不可用，不影響官方資料源",
                    "data_sources": ["Yahoo Finance chart endpoint"],
                    "uses_realtime_or_delayed": "批次 dashboard 以 Yahoo chart endpoint 為主。",
                    "data_date": "2026-01-01",
                    "is_stale": False,
                },
                "full_market_scan": {
                    "summary": {
                        "pool_symbols": 1000,
                        "twse_count": 714,
                        "tpex_count": 0,
                        "candidate_symbols": 120,
                        "excluded_etf": 80,
                        "excluded_warrant": 10,
                        "excluded_preferred": 5,
                        "excluded_low_liquidity": 300,
                    },
                    "source_status": {
                        "twse_ok": True,
                        "tpex_ok": False,
                        "tpex_error": "DNS failed",
                        "twse_used_cache": False,
                        "tpex_used_cache": False,
                        "used_cache": False,
                        "retry_count": 1,
                    },
                    "by_status": {
                        "a": 1,
                        "b_plus": 2,
                        "b": 3,
                        "high_risk": 4,
                        "avoid": 5,
                        "data_missing": 6,
                        "out_of_pool": 7,
                    },
                    "out_of_pool_symbols": ["6770.TW｜力積電"],
                },
                "missed_stock_analysis": {
                    "definition": "漲幅 > 3% 且量比 >= 0.8",
                    "scanner_limitation": "目前漏抓率以已成功取得的 TWSE/TPEX 掃描範圍計算；若 TPEX 抓取失敗或使用快取，上櫃強勢股仍可能漏抓。",
                    "total_scanned": 1,
                    "strong_move_count": 1,
                    "entered_ai_count": 0,
                    "missed_count": 1,
                    "missed_rate": 100,
                    "rows": [
                        {
                            "symbol": "6770.TW",
                            "name": "力積電",
                            "change_pct": 4.2,
                            "latest_price": 18.5,
                            "volume_ratio": 1.1,
                            "turnover": 100000000,
                            "above_vwap": True,
                            "break_prev_high": True,
                            "break_intraday_high": True,
                            "entered_ai_candidates": False,
                            "ai_grade": "D",
                            "entry_status": "high_risk",
                            "not_selected_reason": "risk_score 過高",
                            "reason_code": "risk_high",
                            "latest_at": "2026-01-01T09:05:00+08:00",
                        }
                    ],
                },
                "model_conditions": {
                    "a": ["bullish_score >= 80"],
                    "b_plus": ["bullish_score >= 70"],
                    "b": ["bullish_score >= 65"],
                    "c_d_exclusion": ["risk_score > 70"],
                    "entry_status": ["executable：A 級且信心足夠"],
                },
                "root_cause_diagnosis": ["目前 dashboard 掃描池不是全市場。"],
                "backtest_diagnostic": {"message": "目前樣本不足時不硬算勝率。"},
                "user_guide": [
                    "這不是報明牌系統，而是當沖條件檢查工具。",
                    "high_risk 代表股票可能很強，但追價風險高，不能包裝成推薦。",
                    "data_missing 代表資料不足，不能判斷，不應產生正常當沖建議。",
                ],
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
        self.assertIn("精準資料缺口總覽", html)
        self.assertIn("候選股怎麼選出來", html)
        self.assertIn("決策附錄", html)
        self.assertNotIn("AI 今日決策中心", html)
        self.assertIn("缺逐筆 Tick", html)
        self.assertIn("缺五檔委買委賣", html)
        self.assertIn("不會調整 A / B+ / B 條件", html)
        self.assertIn("資料健康度", html)
        self.assertIn("Yahoo無資料/無效代號", html)
        self.assertIn("Yahoo代理失敗", html)
        self.assertIn("已排除評分", html)
        self.assertIn("不影響官方資料源", html)
        self.assertIn("資料日期", html)
        self.assertIn("是否過期", html)
        self.assertIn("台股全市場異動掃描池", html)
        self.assertIn("TWSE 上市掃描：成功，普通股池 714 檔", html)
        self.assertIn("TPEX 上櫃掃描：尚未納入或抓取失敗，普通股池 0 檔", html)
        self.assertIn("目前掃描範圍：上市，不含上櫃", html)
        self.assertIn("部分上櫃強勢股仍可能漏抓", html)
        self.assertIn("漏抓股票診斷", html)
        self.assertIn("模型條件診斷", html)
        self.assertIn("這不是報明牌系統", html)
        self.assertIn("high_risk 代表股票可能很強", html)
        self.assertIn("data_missing 代表資料不足", html)
        self.assertIn("out_of_pool 新找到", html)
        self.assertIn("risk_high", html)
        self.assertIn("TPEX 抓取失敗或使用快取", html)
        self.assertIn("系統版本 / Debug", html)
        self.assertIn("long_model_v2_volume_vwap_2026-06-12", html)
        self.assertIn("session_policy_v1_time_gated_entry_2026-06-18", html)
        self.assertIn("recommendations count from DB", html)
        self.assertIn("進場雷達通過", html)
        self.assertIn("wait_volume 等量能", html)
        self.assertNotIn("舊版參考：今日看漲焦點", html)
        self.assertNotIn("舊版參考：系統自動選股", html)


if __name__ == "__main__":
    unittest.main()
