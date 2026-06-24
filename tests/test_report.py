from datetime import datetime
from pathlib import Path
import tempfile
import unittest

from stock_daytrade_system.report import render_report
from stock_daytrade_system.scoring import CandidateScore, MarketBias


class ReportCopyTests(unittest.TestCase):
    def test_report_uses_directional_observation_copy_not_buy_recommendation(self):
        with tempfile.TemporaryDirectory() as directory:
            output_path = Path(directory) / "report.md"
            render_report(
                datetime(2026, 6, 25, 8, 30),
                MarketBias(score=1.0, direction="偏多", notes=["大盤偏多"]),
                [],
                [
                    CandidateScore(
                        symbol="2330.TW",
                        name="台積電",
                        sector="半導體",
                        direction="做多觀察",
                        score=5,
                        close=100,
                        day_change_pct=1,
                        avg_volume=1_000_000,
                        atr=2,
                        previous_high=101,
                        previous_low=98,
                        trigger_price=101,
                        stop_loss=98,
                        target_price=105,
                        risk_per_share=3,
                        suggested_shares=1000,
                        reasons=["站上 VWAP"],
                    )
                ],
                [],
                output_path,
            )
            text = output_path.read_text(encoding="utf-8")

        self.assertIn("## 方向偏多觀察", text)
        self.assertNotIn("## 做多觀察", text)
        self.assertNotIn("強勢做多觀察", text)
        self.assertNotIn("買多推薦", text)


if __name__ == "__main__":
    unittest.main()
