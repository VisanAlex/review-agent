from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from review_agent.git_changes import ChangeCollectionError, collect_changes


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout


class CollectChangesTests(unittest.TestCase):
    def make_repo(self) -> Path:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        repo = Path(self.temp_dir.name)
        git(repo, "init", "-b", "main")
        git(repo, "config", "user.email", "review-agent@example.test")
        git(repo, "config", "user.name", "Review Agent Tests")
        (repo / "app.py").write_text("def answer():\n    return 41\n", encoding="utf-8")
        git(repo, "add", "app.py")
        git(repo, "commit", "-m", "initial")
        return repo

    def test_working_tree_includes_tracked_and_untracked_text_files(self) -> None:
        repo = self.make_repo()
        (repo / "app.py").write_text("def answer():\n    return 42\n", encoding="utf-8")
        (repo / "new.ts").write_text("export const enabled = true;\n", encoding="utf-8")

        changes = collect_changes(repo)

        self.assertEqual(changes.mode, "working-tree")
        self.assertEqual(changes.files, ["app.py", "new.ts"])
        self.assertEqual(changes.languages, ["Python", "TypeScript"])
        self.assertIn("return 42", changes.diff)
        self.assertIn("export const enabled", changes.diff)
        self.assertFalse(changes.truncated)

    def test_base_head_mode_uses_the_merge_base(self) -> None:
        repo = self.make_repo()
        (repo / "app.py").write_text("def answer():\n    return 43\n", encoding="utf-8")
        git(repo, "add", "app.py")
        git(repo, "commit", "-m", "change answer")

        changes = collect_changes(repo, base="HEAD~1", head="HEAD")

        self.assertEqual(changes.mode, "committed")
        self.assertEqual(changes.files, ["app.py"])
        self.assertIn("return 43", changes.diff)

    def test_staged_mode_excludes_unstaged_changes(self) -> None:
        repo = self.make_repo()
        (repo / "app.py").write_text("def answer():\n    return 42\n", encoding="utf-8")
        git(repo, "add", "app.py")
        (repo / "app.py").write_text("def answer():\n    return 99\n", encoding="utf-8")

        changes = collect_changes(repo, staged=True)

        self.assertIn("return 42", changes.diff)
        self.assertNotIn("return 99", changes.diff)

    def test_diff_is_capped_and_marked(self) -> None:
        repo = self.make_repo()
        (repo / "app.py").write_text("x = '" + ("a" * 5000) + "'\n", encoding="utf-8")

        changes = collect_changes(repo, max_diff_chars=500)

        self.assertTrue(changes.truncated)
        self.assertLessEqual(len(changes.diff), 600)
        self.assertIn("diff truncated", changes.diff)

    def test_non_git_directory_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ChangeCollectionError):
                collect_changes(Path(directory))

    def test_working_tree_supports_a_repository_before_its_first_commit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            git(repo, "init", "-b", "main")
            (repo / "first.rs").write_text("fn main() {}\n", encoding="utf-8")

            changes = collect_changes(repo)

            self.assertEqual(changes.files, ["first.rs"])
            self.assertEqual(changes.languages, ["Rust"])
            self.assertIn("fn main", changes.diff)

    def test_old_state_directory_has_no_special_exclusion(self) -> None:
        repo = self.make_repo()
        (repo / "app.py").write_text("def answer():\n    return 42\n", encoding="utf-8")
        state = repo / ".review-agent-state" / "host-codex.json"
        state.parent.mkdir()
        state.write_text('{"findings": []}\n', encoding="utf-8")

        changes = collect_changes(repo)

        self.assertEqual(changes.files, [".review-agent-state/host-codex.json", "app.py"])
        self.assertIn("host-codex.json", changes.diff)


if __name__ == "__main__":
    unittest.main()
