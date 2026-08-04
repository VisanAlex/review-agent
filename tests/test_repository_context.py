from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from review_agent.repository_context import collect_repository_context


class RepositoryContextTests(unittest.TestCase):
    def test_collects_invariants_and_only_relevant_incidents(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            context_dir = repo / ".review-agent"
            incidents = context_dir / "incidents"
            incidents.mkdir(parents=True)
            (context_dir / "invariants.md").write_text(
                "Order writes must remain idempotent.\n", encoding="utf-8"
            )
            (incidents / "orders-retry.md").write_text(
                "A retry in src/orders/worker.py created duplicate orders.\n", encoding="utf-8"
            )
            (incidents / "avatar-cache.md").write_text(
                "The profile avatar cache served stale images.\n", encoding="utf-8"
            )

            context = collect_repository_context(
                repo,
                changed_files=["src/orders/worker.py"],
                risk_signals=["concurrency-reliability"],
            )

        self.assertFalse(context["trusted"])
        self.assertEqual(context["invariants"][0]["path"], ".review-agent/invariants.md")
        self.assertEqual(
            [item["path"] for item in context["incidents"]],
            [".review-agent/incidents/orders-retry.md"],
        )

    def test_context_is_bounded_and_ignores_symlinks(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            context_dir = repo / ".review-agent"
            incidents = context_dir / "incidents"
            incidents.mkdir(parents=True)
            (context_dir / "invariants.md").write_text("x" * 30_000, encoding="utf-8")
            target = repo / "outside.md"
            target.write_text("orders secret", encoding="utf-8")
            link = incidents / "orders.md"
            try:
                link.symlink_to(target)
            except OSError:
                self.skipTest("symlinks are unavailable on this platform")

            context = collect_repository_context(
                repo,
                changed_files=["src/orders.py"],
                risk_signals=[],
            )

        self.assertLessEqual(len(context["invariants"][0]["content"]), 20_000)
        self.assertTrue(context["invariants"][0]["truncated"])
        self.assertEqual(context["incidents"], [])

    def test_retrieval_uses_diff_identifiers_and_unicode_paths(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            incidents = repo / ".review-agent" / "incidents"
            incidents.mkdir(parents=True)
            (incidents / "token-refresh.md").write_text(
                "A refresh_access_token regression invalidated active sessions.\n",
                encoding="utf-8",
            )
            (incidents / "請求書障害.md").write_text(
                "請求書の生成で通貨形式が壊れた。\n",
                encoding="utf-8",
            )

            context = collect_repository_context(
                repo,
                changed_files=["src/service.py", "src/請求書.py"],
                risk_signals=["internationalization"],
                diff="+refresh_access_token(user)\n",
            )

        self.assertEqual(
            {item["path"] for item in context["incidents"]},
            {
                ".review-agent/incidents/token-refresh.md",
                ".review-agent/incidents/請求書障害.md",
            },
        )

    def test_diff_retrieval_query_is_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            incidents = repo / ".review-agent" / "incidents"
            incidents.mkdir(parents=True)
            (incidents / "late-token.md").write_text(
                "late_identifier caused the old failure.\n",
                encoding="utf-8",
            )
            noisy_diff = " ".join(f"token{index:04d}" for index in range(2_100))

            context = collect_repository_context(
                repo,
                changed_files=["src/service.py"],
                risk_signals=[],
                diff=f"{noisy_diff} late_identifier",
            )

        self.assertEqual(context["incidents"], [])
        self.assertEqual(context["limits"]["max_diff_query_tokens"], 2_000)


if __name__ == "__main__":
    unittest.main()
