from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class CheckPublicReadinessScriptTests(unittest.TestCase):
    def test_script_runs_release_readiness_by_default(self):
        script = (ROOT / "scripts" / "check_public_readiness.sh").read_text()

        self.assertIn("SKIP_RELEASE_READINESS", script)
        self.assertIn("scripts/check_release_readiness.py", script)
        self.assertIn("scripts/verify_public_deployment.py", script)

    def test_script_allows_explicit_skip_for_public_only_checks(self):
        script = (ROOT / "scripts" / "check_public_readiness.sh").read_text()

        self.assertIn('if [[ "$SKIP_RELEASE_READINESS" != "1" ]]', script)


if __name__ == "__main__":
    unittest.main()
