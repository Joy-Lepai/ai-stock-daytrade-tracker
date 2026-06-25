from pathlib import Path
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class WorkflowTests(unittest.TestCase):
    def test_refresh_dashboard_workflow_prints_refresh_status_snapshot(self):
        workflow = PROJECT_ROOT / ".github" / "workflows" / "refresh-dashboard.yml"
        text = workflow.read_text(encoding="utf-8")

        self.assertIn("timeout-minutes: 10", text)
        self.assertIn("concurrency:", text)
        self.assertIn("stock-dashboard-refresh", text)
        self.assertIn("cancel-in-progress: false", text)
        self.assertIn("vars.STOCK_DASHBOARD_URL", text)
        self.assertIn("refresh_layer:", text)
        self.assertIn("REFRESH_LAYER", text)
        self.assertIn('refresh_layer="${REFRESH_LAYER:-auto}"', text)
        self.assertIn('if [ "$refresh_layer" != "auto" ]', text)
        self.assertIn('full_market)', text)
        self.assertIn('watchlist)', text)
        self.assertIn('positions)', text)
        self.assertIn('post_close)', text)
        self.assertIn('all)', text)
        self.assertIn("Unknown refresh_layer", text)
        self.assertIn("https://stock.letslepai.com", text)
        self.assertIn("uses: actions/checkout@v4", text)
        self.assertIn("/refresh_full_market", text)
        self.assertIn("/refresh_watchlist", text)
        self.assertIn("/refresh_positions", text)
        self.assertIn("/refresh_post_close_validation", text)
        self.assertIn('X-Requested-With: fetch', text)
        self.assertIn("Accept: application/json", text)
        self.assertIn("print_refresh_status", text)
        self.assertIn("/api/refresh/status", text)
        self.assertIn("python3 -m json.tool", text)
        self.assertIn("scripts/check_operational_health.py", text)
        self.assertIn("--base-url \"$DASHBOARD_URL\"", text)
        self.assertIn("FAILED /api/refresh/status operational health", text)
        self.assertNotIn('status == "blocked"', text)


if __name__ == "__main__":
    unittest.main()
