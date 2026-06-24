from pathlib import Path
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class WorkflowTests(unittest.TestCase):
    def test_refresh_dashboard_workflow_prints_refresh_status_snapshot(self):
        workflow = PROJECT_ROOT / ".github" / "workflows" / "refresh-dashboard.yml"
        text = workflow.read_text(encoding="utf-8")

        self.assertIn("timeout-minutes: 10", text)
        self.assertIn("vars.STOCK_DASHBOARD_URL", text)
        self.assertIn("https://stock.letslepai.com", text)
        self.assertIn("/refresh_full_market", text)
        self.assertIn("/refresh_watchlist", text)
        self.assertIn("/refresh_positions", text)
        self.assertIn("/refresh_post_close_validation", text)
        self.assertIn('X-Requested-With: fetch', text)
        self.assertIn("Accept: application/json", text)
        self.assertIn("print_refresh_status", text)
        self.assertIn("/api/refresh/status", text)
        self.assertIn("python3 -m json.tool", text)


if __name__ == "__main__":
    unittest.main()
