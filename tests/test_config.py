import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from stock_daytrade_system.config import load_config


class ConfigTests(unittest.TestCase):
    def test_fugle_priority_symbols_can_be_extended_from_env(self):
        payload = {
            "market": {
                "timezone": "Asia/Taipei",
                "premarket_run_time": "07:00",
                "benchmark": "^TWII",
                "taiwan_futures": "TX=F",
                "us_market_symbols": [],
            },
            "risk": {
                "min_price": 20,
                "min_avg_volume": 1000000,
                "max_loss_per_trade": 3000,
                "round_lot_size": 1000,
                "max_candidates_per_side": 20,
            },
            "auto_universe": [{"symbol": "2330.TW", "name": "台積電", "sector": "semiconductor"}],
            "manual_symbols": [],
            "fugle_priority_symbols": ["6919.TW"],
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "watchlist.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            with patch.dict("os.environ", {"FUGLE_PRIORITY_SYMBOLS": "8150.TW, 6919.tw, 3711.TW"}, clear=False):
                config = load_config(path)

        self.assertEqual(config.fugle_priority_symbols, ["6919.TW", "8150.TW", "3711.TW"])


if __name__ == "__main__":
    unittest.main()
