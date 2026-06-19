import unittest

from stock_daytrade_system.signal_guard import evaluate_signal_guard


class SignalGuardTests(unittest.TestCase):
    def test_complete_executable_signal_is_allowed(self):
        result = evaluate_signal_guard(
            {
                "grade": "A",
                "entry_status": "executable",
                "last_price": 101,
                "vwap": 100,
                "volume_ratio": 1.2,
                "stop_loss": 99,
            }
        )

        self.assertTrue(result.is_executable_allowed)
        self.assertTrue(result.is_strong_long_allowed)
        self.assertEqual(result.reason_codes, ["executable"])

    def test_stale_or_missing_core_data_blocks_executable(self):
        result = evaluate_signal_guard(
            {
                "grade": "A",
                "entry_status": "executable",
                "last_price": 101,
                "volume_ratio": 1.2,
                "stop_loss": 99,
            },
            stale=True,
        )

        self.assertFalse(result.is_executable_allowed)
        self.assertEqual(result.effective_entry_status, "data_missing")
        self.assertIn("stale_data", result.reason_codes)
        self.assertIn("missing_vwap", result.reason_codes)

    def test_practice_long_is_never_executable(self):
        result = evaluate_signal_guard(
            {
                "grade": "B+",
                "entry_status": "practice_long",
                "last_price": 101,
                "vwap": 100,
                "volume_ratio": 0.9,
                "stop_loss": 99,
            }
        )

        self.assertFalse(result.is_executable_allowed)
        self.assertEqual(result.effective_entry_status, "practice_long")
        self.assertEqual(result.reason_codes, ["practice_long"])

    def test_far_from_vwap_downgrades_executable_to_high_risk(self):
        result = evaluate_signal_guard(
            {
                "grade": "A",
                "entry_status": "executable",
                "last_price": 105,
                "vwap": 100,
                "volume_ratio": 1.5,
                "stop_loss": 103,
            }
        )

        self.assertFalse(result.is_executable_allowed)
        self.assertEqual(result.effective_entry_status, "high_risk")
        self.assertIn("too_far_from_vwap", result.reason_codes)


if __name__ == "__main__":
    unittest.main()
