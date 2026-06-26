from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class CheckPublicReadinessScriptTests(unittest.TestCase):
    def test_script_runs_release_readiness_by_default(self):
        script = (ROOT / "scripts" / "check_public_readiness.sh").read_text()

        self.assertIn("SKIP_RELEASE_READINESS", script)
        self.assertIn("scripts/check_release_readiness.py", script)
        self.assertIn("scripts/verify_public_deployment.py", script)
        self.assertIn("6919,2886,8150,3711", script)

    def test_script_allows_explicit_skip_for_public_only_checks(self):
        script = (ROOT / "scripts" / "check_public_readiness.sh").read_text()

        self.assertIn('if [[ "$SKIP_RELEASE_READINESS" != "1" ]]', script)

    def test_script_prints_release_push_guidance_when_blocked(self):
        script = (ROOT / "scripts" / "check_public_readiness.sh").read_text()

        self.assertIn("repo_path", script)
        self.assertIn("remote_url", script)
        self.assertIn("push_method", script)
        self.assertIn("push_reason", script)
        self.assertIn("github_desktop_repo_hint", script)
        self.assertIn("${BASE_URL%/}/operator", script)


class OperationalRunbookDocsTests(unittest.TestCase):
    def test_readme_documents_operator_runbook_health_entrypoint(self):
        readme = (ROOT / "README.md").read_text()

        self.assertIn("優先讀 `/api/operator/runbook`", readme)
        self.assertIn("https://stock.letslepai.com/operator", readme)
        self.assertIn("fallback 到 `/api/health`", readme)


class RunOpenCheckScriptTests(unittest.TestCase):
    def test_open_check_script_runs_release_and_operational_gates_first(self):
        script = (ROOT / "scripts" / "run_open_check.sh").read_text()

        self.assertIn("scripts/check_release_readiness.py", script)
        self.assertIn("scripts/check_operational_health.py", script)
        self.assertIn("SKIP_RELEASE_READINESS", script)
        self.assertIn("SKIP_OPERATIONAL_HEALTH", script)
        self.assertIn("RUN_LEGACY_OPEN_REPORT", script)
        self.assertIn("${BASE_URL%/}/operator", script)
        self.assertLess(script.index("scripts/check_release_readiness.py"), script.index("stock_daytrade_system.cli open-check"))
        self.assertLess(script.index("scripts/check_operational_health.py"), script.index("stock_daytrade_system.cli open-check"))

    def test_open_check_script_blocks_when_release_or_health_fails(self):
        script = (ROOT / "scripts" / "run_open_check.sh").read_text()

        self.assertIn("Opening check stopped: 本機、GitHub 或公開站版本尚未對齊。", script)
        self.assertIn("Opening check stopped: 營運健康狀態 blocked。", script)


class RunPremarketScriptTests(unittest.TestCase):
    def test_premarket_script_runs_release_and_operational_gates_first(self):
        script = (ROOT / "scripts" / "run_premarket.sh").read_text()

        self.assertIn("scripts/check_release_readiness.py", script)
        self.assertIn("scripts/check_operational_health.py", script)
        self.assertIn("SKIP_RELEASE_READINESS", script)
        self.assertIn("SKIP_OPERATIONAL_HEALTH", script)
        self.assertIn("${BASE_URL%/}/operator", script)
        self.assertLess(script.index("scripts/check_release_readiness.py"), script.index("stock_daytrade_system.cli report"))
        self.assertLess(script.index("scripts/check_operational_health.py"), script.index("stock_daytrade_system.cli report"))

    def test_premarket_script_blocks_when_release_or_health_fails(self):
        script = (ROOT / "scripts" / "run_premarket.sh").read_text()

        self.assertIn("Premarket report stopped: 本機、GitHub 或公開站版本尚未對齊。", script)
        self.assertIn("Premarket report stopped: 營運健康狀態 blocked。", script)


class RunTrackerScriptTests(unittest.TestCase):
    def test_tracker_script_runs_release_and_operational_gates_first(self):
        script = (ROOT / "scripts" / "run_tracker.sh").read_text()

        self.assertIn("scripts/check_release_readiness.py", script)
        self.assertIn("scripts/check_operational_health.py", script)
        self.assertIn("SKIP_RELEASE_READINESS", script)
        self.assertIn("SKIP_OPERATIONAL_HEALTH", script)
        self.assertLess(script.index("scripts/check_release_readiness.py"), script.index("stock_daytrade_system.cli tracker"))
        self.assertLess(script.index("scripts/check_operational_health.py"), script.index("stock_daytrade_system.cli tracker"))

    def test_tracker_script_blocks_when_release_or_health_fails(self):
        script = (ROOT / "scripts" / "run_tracker.sh").read_text()

        self.assertIn("Tracker rebuild stopped: 本機、GitHub 或公開站版本尚未對齊。", script)
        self.assertIn("Tracker rebuild stopped: 營運健康狀態 blocked。", script)
        self.assertIn("不要用壞資料重建 tracker", script)


if __name__ == "__main__":
    unittest.main()
