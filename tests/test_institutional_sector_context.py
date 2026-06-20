import unittest

from stock_daytrade_system.cmoney import CMoneyRanking
from stock_daytrade_system.institutional_context import evaluate_institutional_context
from stock_daytrade_system.sector_context import evaluate_sector_context
from stock_daytrade_system.sectors import SectorStrength


class InstitutionalSectorContextTests(unittest.TestCase):
    def test_institutional_bullish_is_background_only(self):
        ranking = CMoneyRanking(
            rank=1,
            date="2026/06/18",
            symbol="2330.TW",
            code="2330",
            name="台積電",
            foreign_buy_million=100,
            investment_buy_million=50,
            dealers_buy_million=10,
            total_buy_million=160,
        )

        context = evaluate_institutional_context(ranking)

        self.assertEqual(context.institutional_trend, "bullish")
        self.assertEqual(context.institutional_label, "籌碼偏多")
        self.assertFalse(context.can_upgrade_signal)
        self.assertIn("只能作為背景支持", context.institutional_reason)
        self.assertIsNone(context.foreign_3d_sum)

    def test_mixed_institutional_context_is_not_bullish(self):
        ranking = CMoneyRanking(
            rank=2,
            date="2026/06/18",
            symbol="2317.TW",
            code="2317",
            name="鴻海",
            foreign_buy_million=100,
            investment_buy_million=-80,
            dealers_buy_million=5,
            total_buy_million=25,
        )

        context = evaluate_institutional_context(ranking)

        self.assertEqual(context.institutional_trend, "mixed")
        self.assertEqual(context.institutional_label, "籌碼分歧")

    def test_sector_strength_is_background_only(self):
        strength = SectorStrength(
            sector="semiconductor",
            member_count=10,
            avg_one_day_return=2.1,
            avg_five_day_return=4.2,
            avg_relative_strength=1.8,
            bullish_count=8,
            bearish_count=2,
            direction="強勢",
            score=3.5,
        )

        context = evaluate_sector_context(strength, rank=1)

        self.assertEqual(context.sector_status, "strong")
        self.assertEqual(context.sector_status_label, "族群偏強")
        self.assertTrue(context.is_sector_leader)
        self.assertIn("背景支持", context.sector_reason)


if __name__ == "__main__":
    unittest.main()
