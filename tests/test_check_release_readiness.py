import contextlib
import io
import subprocess
import unittest

from scripts.check_release_readiness import (
    ReleaseState,
    commit_matches,
    describe_git_failure,
    describe_git_timeout,
    evaluate_release_state,
    git_output,
    is_worktree_dirty,
    load_release_state,
    main,
    parse_ahead_count,
    recommended_next_action,
    release_report_payload,
    release_steps,
)


class CheckReleaseReadinessTests(unittest.TestCase):
    def test_parse_ahead_count_from_git_status(self):
        self.assertEqual(parse_ahead_count("## main...origin/main [ahead 18]"), 18)
        self.assertEqual(parse_ahead_count("72"), 72)
        self.assertEqual(parse_ahead_count("## main...origin/main"), 0)

    def test_load_release_state_uses_fast_status_without_ahead_behind_scan(self):
        calls = []

        def fake_runner(command, **kwargs):
            calls.append(command)
            if command[-2:] == ["rev-parse", "HEAD"]:
                return "localcommit\n"
            if command[-2:] == ["rev-parse", "origin/main"]:
                return "origincommit\n"
            if command[-3:] == ["rev-list", "--count", "origin/main..HEAD"]:
                return "72\n"
            if command[-3:] == ["status", "-sb", "--no-ahead-behind"]:
                return "## main...origin/main\n"
            raise AssertionError(command)

        state = load_release_state(runner=fake_runner, fetch_public=False)

        self.assertEqual(state.ahead_count, 72)
        self.assertEqual(state.status_line, "## main...origin/main")
        self.assertIn(["git", "status", "-sb", "--no-ahead-behind"], calls)
        self.assertNotIn(["git", "status", "-sb"], calls)

    def test_dirty_worktree_detection_ignores_header(self):
        self.assertFalse(is_worktree_dirty("## main...origin/main\n"))
        self.assertTrue(is_worktree_dirty("## main...origin/main\n M README.md\n"))

    def test_evaluate_detects_local_ahead_public_old(self):
        state = ReleaseState(
            head="newcommit123456",
            origin="oldcommit999999",
            ahead_count=18,
            dirty=False,
            status_line="## main...origin/main [ahead 18]",
            public_runtime="oldcommit999999",
            public_tracker="oldcommit999999",
        )

        checks = evaluate_release_state(state)
        failed = [item.name for item in checks if not item.ok]

        self.assertIn("local pushed to origin/main", failed)
        self.assertIn("public runtime matches local HEAD", failed)
        self.assertIn("Repository → Push", recommended_next_action(state))
        self.assertIn("Repository → Push", " ".join(release_steps(state)))

    def test_recommended_action_deploy_when_origin_matches_but_public_old(self):
        state = ReleaseState(
            head="newcommit123456",
            origin="newcommit123456",
            ahead_count=0,
            dirty=False,
            status_line="## main...origin/main",
            public_runtime="oldcommit999999",
            public_tracker="oldcommit999999",
        )

        self.assertIn("Render", recommended_next_action(state))
        self.assertIn("Deploy latest commit", " ".join(release_steps(state)))

    def test_recommended_action_ready_when_all_match(self):
        state = ReleaseState(
            head="abcdef123456",
            origin="abcdef123456",
            ahead_count=0,
            dirty=False,
            status_line="## main...origin/main",
            public_runtime="abcdef123456",
            public_tracker="abcdef123456",
        )

        self.assertTrue(all(item.ok for item in evaluate_release_state(state)))
        self.assertIn("一致", recommended_next_action(state))
        self.assertIn("verify_public_deployment.py", " ".join(release_steps(state)))

    def test_release_report_payload_exposes_operator_gates(self):
        state = ReleaseState(
            head="local123456",
            origin="origin999999",
            ahead_count=3,
            dirty=False,
            status_line="## main...origin/main [different]",
            public_runtime="origin999999",
            public_tracker="origin999999",
        )

        payload = release_report_payload(state, evaluate_release_state(state))

        self.assertEqual(payload["status"], "blocked")
        self.assertEqual(payload["ahead_count"], 3)
        self.assertTrue(payload["can_push"])
        self.assertFalse(payload["can_deploy_render"])
        self.assertFalse(payload["can_trust_public"])
        self.assertIn("local pushed to origin/main", payload["failed_checks"])
        self.assertIn("Repository → Push", payload["next_action"])

    def test_release_steps_detect_tracker_mismatch(self):
        state = ReleaseState(
            head="abcdef123456",
            origin="abcdef123456",
            ahead_count=0,
            dirty=False,
            status_line="## main...origin/main",
            public_runtime="abcdef123456",
            public_tracker="oldcommit999999",
        )

        self.assertIn("POST /refresh", " ".join(release_steps(state)))

    def test_commit_matches_accepts_short_or_long_prefixes(self):
        self.assertTrue(commit_matches("abcdef123456", "abcdef1234567890"))
        self.assertTrue(commit_matches("abcdef1234567890", "abcdef123456"))
        self.assertFalse(commit_matches("abcdef", "123456"))

    def test_git_failure_describes_signal_without_traceback(self):
        error = subprocess.CalledProcessError(-10, ["git", "status", "-sb"])

        self.assertIn("SIGBUS", describe_git_failure(["status", "-sb"], error))

    def test_git_timeout_describes_large_or_locked_worktree(self):
        error = subprocess.TimeoutExpired(["git", "status", "-sb"], timeout=7)

        message = describe_git_timeout(["status", "-sb"], error)

        self.assertIn("timed out after 7s", message)
        self.assertIn("large or locked worktree", message)

    def test_git_output_wraps_runner_timeout(self):
        def timeout_runner(*args, **kwargs):
            raise subprocess.TimeoutExpired(args[0], timeout=kwargs.get("timeout"))

        with self.assertRaisesRegex(RuntimeError, "timed out"):
            git_output(["status", "-sb"], runner=timeout_runner, timeout=1)

    def test_git_output_wraps_runner_failure(self):
        def failing_runner(*args, **kwargs):
            raise subprocess.CalledProcessError(128, ["git", "status", "-sb"])

        with self.assertRaisesRegex(RuntimeError, "git status -sb"):
            git_output(["status", "-sb"], runner=failing_runner)

    def test_main_prints_readable_failure_when_git_unavailable(self):
        import scripts.check_release_readiness as module

        original = module.git_output

        def failing_git_output(args, **kwargs):
            raise module.ReleaseReadinessError("`git status -sb` failed: SIGBUS")

        module.git_output = failing_git_output
        stream = io.StringIO()
        try:
            with contextlib.redirect_stdout(stream):
                exit_code = main(["--no-public"])
        finally:
            module.git_output = original

        self.assertEqual(exit_code, 1)
        self.assertIn("local Git state readable", stream.getvalue())
        self.assertIn("Next action", stream.getvalue())
        self.assertNotIn("Traceback", stream.getvalue())

    def test_main_json_outputs_machine_readable_payload(self):
        import scripts.check_release_readiness as module

        original = module.load_release_state

        def fake_load_release_state(**kwargs):
            return ReleaseState(
                head="abcdef123456",
                origin="abcdef123456",
                ahead_count=0,
                dirty=False,
                status_line="## main...origin/main",
                public_runtime="abcdef123456",
                public_tracker="abcdef123456",
            )

        module.load_release_state = fake_load_release_state
        stream = io.StringIO()
        try:
            with contextlib.redirect_stdout(stream):
                exit_code = main(["--json"])
        finally:
            module.load_release_state = original

        self.assertEqual(exit_code, 0)
        self.assertIn('"status": "ok"', stream.getvalue())
        self.assertIn('"can_deploy_render": true', stream.getvalue())
        self.assertIn('"can_trust_public": true', stream.getvalue())

    def test_main_json_outputs_machine_readable_failure(self):
        import scripts.check_release_readiness as module

        original = module.git_output

        def failing_git_output(args, **kwargs):
            raise module.ReleaseReadinessError("`git status -sb` timed out after 2s")

        module.git_output = failing_git_output
        stream = io.StringIO()
        try:
            with contextlib.redirect_stdout(stream):
                exit_code = main(["--no-public", "--json"])
        finally:
            module.git_output = original

        self.assertEqual(exit_code, 1)
        self.assertIn('"status": "blocked"', stream.getvalue())
        self.assertIn('"local Git state readable"', stream.getvalue())
        self.assertIn('"can_deploy_render": false', stream.getvalue())
        self.assertNotIn("Traceback", stream.getvalue())


if __name__ == "__main__":
    unittest.main()
