import unittest

from scripts.check_release_readiness import (
    ReleaseState,
    commit_matches,
    evaluate_release_state,
    is_worktree_dirty,
    parse_ahead_count,
    recommended_next_action,
)


class CheckReleaseReadinessTests(unittest.TestCase):
    def test_parse_ahead_count_from_git_status(self):
        self.assertEqual(parse_ahead_count("## main...origin/main [ahead 18]"), 18)
        self.assertEqual(parse_ahead_count("## main...origin/main"), 0)

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
        self.assertIn("Push origin/main", recommended_next_action(state))

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

    def test_commit_matches_accepts_short_or_long_prefixes(self):
        self.assertTrue(commit_matches("abcdef123456", "abcdef1234567890"))
        self.assertTrue(commit_matches("abcdef1234567890", "abcdef123456"))
        self.assertFalse(commit_matches("abcdef", "123456"))


if __name__ == "__main__":
    unittest.main()
