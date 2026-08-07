from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from review_agent.git_changes import collect_changes
from review_agent.impact import collect_impact_context


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


class ImpactContextTests(unittest.TestCase):
    def make_repo(self) -> Path:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        repo = Path(self.temp_dir.name)
        git(repo, "init", "-b", "main")
        git(repo, "config", "user.email", "review-agent@example.test")
        git(repo, "config", "user.name", "Review Agent Tests")
        (repo / "pricing.py").write_text(
            "def calculate_total(items):\n    return sum(items)\n",
            encoding="utf-8",
        )
        (repo / "invoice.py").write_text(
            "from pricing import calculate_total\n\nTOTAL = calculate_total([1, 2])\n",
            encoding="utf-8",
        )
        (repo / "test_invoice.py").write_text(
            "from pricing import calculate_total\n\ndef test_total():\n    assert calculate_total([1]) == 1\n",
            encoding="utf-8",
        )
        git(repo, "add", ".")
        git(repo, "commit", "-m", "initial")
        return repo

    def test_maps_changed_symbol_references_in_unchanged_files(self) -> None:
        repo = self.make_repo()
        (repo / "pricing.py").write_text(
            "def calculate_total(items):\n    return max(items, default=0)\n",
            encoding="utf-8",
        )

        context = collect_impact_context(collect_changes(repo))

        self.assertFalse(context["trusted"])
        self.assertIn("calculate_total", context["changed_identifiers"])
        self.assertEqual(
            {item["file"] for item in context["affected_locations"]},
            {"invoice.py", "test_invoice.py"},
        )
        self.assertTrue(all(item["file"] != "pricing.py" for item in context["affected_locations"]))
        self.assertTrue(all(item["line"] >= 1 for item in context["affected_locations"]))
        self.assertTrue(all("calculate_total" in item["relationship"] for item in context["affected_locations"]))

    def test_deleted_symbol_still_maps_unchanged_consumers(self) -> None:
        repo = self.make_repo()
        (repo / "pricing.py").write_text("DEFAULT_TOTAL = 0\n", encoding="utf-8")

        context = collect_impact_context(collect_changes(repo))

        self.assertIn("calculate_total", context["changed_identifiers"])
        self.assertIn("invoice.py", {item["file"] for item in context["affected_locations"]})

    def test_maps_camel_case_symbols_without_language_specific_parsing(self) -> None:
        repo = self.make_repo()
        (repo / "pricing.ts").write_text(
            "export function calculateTotal(items: number[]) { return items.length; }\n",
            encoding="utf-8",
        )
        (repo / "checkout.ts").write_text(
            "import { calculateTotal } from './pricing';\nconst total = calculateTotal([1]);\n",
            encoding="utf-8",
        )
        git(repo, "add", "pricing.ts", "checkout.ts")
        git(repo, "commit", "-m", "add TypeScript pricing")
        (repo / "pricing.ts").write_text(
            "export function calculateTotal(items: number[]) { return Math.max(...items); }\n",
            encoding="utf-8",
        )

        context = collect_impact_context(collect_changes(repo))

        self.assertIn("calculateTotal", context["changed_identifiers"])
        self.assertIn("checkout.ts", {item["file"] for item in context["affected_locations"]})

    def test_committed_comparison_reads_the_reviewed_head_not_the_checkout(self) -> None:
        repo = self.make_repo()
        (repo / "generated").mkdir()
        (repo / "generated" / "caller.py").write_text(
            "calculate_total([])\n",
            encoding="utf-8",
        )
        (repo / "archive.pdf").write_text("calculate_total([])\n", encoding="utf-8")
        git(repo, "add", "archive.pdf", "generated/caller.py")
        git(repo, "commit", "-m", "add generated caller")
        git(repo, "switch", "-c", "feature")
        (repo / "pricing.py").write_text(
            "def calculate_total(items):\n    return max(items, default=0)\n",
            encoding="utf-8",
        )
        git(repo, "add", "pricing.py")
        git(repo, "commit", "-m", "change pricing")
        git(repo, "switch", "-c", "other", "main")
        (repo / "invoice.py").write_text("TOTAL = 0\n", encoding="utf-8")
        git(repo, "add", "invoice.py")
        git(repo, "commit", "-m", "remove invoice caller")

        changes = collect_changes(repo, base="main", head="feature")
        context = collect_impact_context(changes, max_files=1)

        self.assertEqual(changes.snapshot_ref, "feature")
        self.assertIn("invoice.py", {item["file"] for item in context["affected_locations"]})
        self.assertNotIn("archive.pdf", {item["file"] for item in context["affected_locations"]})
        self.assertNotIn("generated/caller.py", {item["file"] for item in context["affected_locations"]})

    def test_staged_comparison_reads_the_index_not_unstaged_consumers(self) -> None:
        repo = self.make_repo()
        (repo / "generated").mkdir()
        (repo / "generated" / "caller.py").write_text(
            "calculate_total([])\n",
            encoding="utf-8",
        )
        (repo / "archive.pdf").write_text("calculate_total([])\n", encoding="utf-8")
        git(repo, "add", "archive.pdf", "generated/caller.py")
        git(repo, "commit", "-m", "add generated caller")
        (repo / "pricing.py").write_text(
            "def calculate_total(items):\n    return max(items, default=0)\n",
            encoding="utf-8",
        )
        git(repo, "add", "pricing.py")
        (repo / "invoice.py").write_text("TOTAL = 0\n", encoding="utf-8")

        context = collect_impact_context(collect_changes(repo, staged=True), max_files=1)

        self.assertIn("invoice.py", {item["file"] for item in context["affected_locations"]})
        self.assertNotIn("archive.pdf", {item["file"] for item in context["affected_locations"]})
        self.assertNotIn("generated/caller.py", {item["file"] for item in context["affected_locations"]})

    def test_documentation_only_change_takes_an_empty_fast_path(self) -> None:
        repo = self.make_repo()
        (repo / "README.md").write_text("calculate_total usage\n", encoding="utf-8")

        context = collect_impact_context(collect_changes(repo))

        self.assertEqual(context["status"], "empty")
        self.assertEqual(context["changed_identifiers"], [])
        self.assertEqual(context["affected_locations"], [])
        self.assertEqual(context["scanned_files"], 0)

    def test_scan_is_bounded_and_skips_symlinked_consumers(self) -> None:
        repo = self.make_repo()
        target = repo / "outside.py"
        target.write_text("calculate_total([])\n", encoding="utf-8")
        link = repo / "linked.py"
        try:
            link.symlink_to(target)
        except OSError:
            self.skipTest("symlinks are unavailable on this platform")
        git(repo, "add", "linked.py")
        git(repo, "commit", "-m", "add symlink")
        (repo / "pricing.py").write_text(
            "def calculate_total(items):\n    return max(items, default=0)\n",
            encoding="utf-8",
        )

        context = collect_impact_context(collect_changes(repo), max_files=1)

        self.assertEqual(context["status"], "limited")
        self.assertEqual(context["scanned_files"], 1)
        self.assertNotIn("linked.py", {item["file"] for item in context["affected_locations"]})


if __name__ == "__main__":
    unittest.main()
