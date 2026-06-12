import unittest

from stock_daytrade_system.us_data import USMarketSnapshot
from stock_daytrade_system.us_long_model import build_us_long_candidates


def snapshot(**overrides):
    base = dict(
        symbol="NVDA",
        name_en="NVIDIA Corporation",
        name_zh="輝達",
        short_name_zh="輝達",
        sector_en="Semiconductors",
        sector_zh="半導體",
        industry_en="AI Accelerators",
        industry_zh="AI 晶片",
        description_zh="AI 晶片、GPU 與資料中心加速運算龍頭",
        is_etf=False,
        latest_price=101,
        previous_close=98,
        open=99,
        high=101.2,
        low=98.5,
        volume=10_000_000,
        change_pct=3.06,
        vwap=100,
        above_vwap=True,
        volume_ratio=1.6,
        premarket_price=99.5,
        afterhours_price=None,
        premarket_high=100.5,
        break_premarket_high=True,
        break_previous_high=True,
        break_opening_range_high=True,
        opening_range_high=100.8,
        previous_high=100,
        average_volume=50_000_000,
        market_cap=None,
        data_missing=[],
    )
    base.update(overrides)
    return USMarketSnapshot(**base)


class USLongModelTests(unittest.TestCase):
    def test_strong_setup_can_be_executable(self):
        candidates = build_us_long_candidates([snapshot()], market_status="bullish")

        self.assertEqual(candidates[0].grade, "A")
        self.assertEqual(candidates[0].entry_status, "executable")
        self.assertGreaterEqual(candidates[0].bullish_score, 80)

    def test_high_score_with_low_volume_waits_for_volume(self):
        candidates = build_us_long_candidates([snapshot(volume_ratio=0.8)], market_status="bullish")

        self.assertNotEqual(candidates[0].grade, "A")
        self.assertEqual(candidates[0].entry_status, "wait_volume")

    def test_below_vwap_is_avoided(self):
        candidates = build_us_long_candidates(
            [
                snapshot(
                    latest_price=98,
                    vwap=100,
                    above_vwap=False,
                    change_pct=-0.2,
                    volume_ratio=0.7,
                    break_premarket_high=False,
                    break_previous_high=False,
                    break_opening_range_high=False,
                )
            ],
            market_status="neutral",
        )

        self.assertEqual(candidates[0].grade, "D")
        self.assertEqual(candidates[0].entry_status, "avoid")


if __name__ == "__main__":
    unittest.main()
