import unittest
from pathlib import Path
from unittest.mock import patch

from stock_daytrade_system.auth import AuthConfig, load_auth_config, verify_password


class AuthTests(unittest.TestCase):
    def setUp(self):
        self.config = AuthConfig(
            username="admin",
            password_hash="cf89f295632822cb55342f1ab7dd10d3d3dad61e995af94a7926ded08e6d80f8",
            salt="ai-stock-system-v1",
            iterations=200000,
        )

    def test_verify_password_accepts_matching_password(self):
        self.assertTrue(verify_password("stock1234", self.config))

    def test_verify_password_rejects_wrong_password(self):
        self.assertFalse(verify_password("wrong", self.config))

    def test_load_auth_config_prefers_environment_credentials(self):
        with patch.dict(
            "os.environ",
            {"STOCK_WEB_USERNAME": "cloud-user", "STOCK_WEB_PASSWORD": "cloud-pass"},
            clear=False,
        ):
            config = load_auth_config(Path("missing-auth.json"))

        self.assertEqual(config.username, "cloud-user")
        self.assertTrue(verify_password("cloud-pass", config))
        self.assertFalse(verify_password("stock1234", config))


if __name__ == "__main__":
    unittest.main()
