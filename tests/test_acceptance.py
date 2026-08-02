from __future__ import annotations

import unittest
from dataclasses import dataclass, field
from pathlib import Path

from review_agent.external import parse_external_targets
from review_agent.git_changes import ChangeSet
from review_agent.models import (
    ExecutionMode,
    ExecutionOrigin,
    ReviewerRole,
    ReviewerRun,
    ReviewerStatus,
)
from review_agent.planning import recommend_roles
from review_agent.review import consolidate


def change(*files: str, diff: str = "+def changed():\n+    return True\n") -> ChangeSet:
    return ChangeSet(
        repo=Path("/fixture"),
        mode="working-tree",
        source="fixture working tree",
        diff=diff,
        files=list(files),
        languages=["Python"] if any(path.endswith(".py") for path in files) else ["Markdown"],
        truncated=False,
    )


@dataclass
class FakeHost:
    name: str
    native_available: bool
    model_context: str = "current"
    installed_external_tools: set[str] = field(default_factory=set)
    external_outcomes: dict[str, ReviewerStatus] = field(default_factory=dict)
    native_dispatches: list[tuple[str, str]] = field(default_factory=list)
    external_dispatches: list[str] = field(default_factory=list)

    def review(self, changes: ChangeSet, request: str = "Review these changes"):
        plan = recommend_roles(changes)
        runs: list[ReviewerRun] = []
        if plan.selected_roles:
            for recommendation in plan.selected_roles:
                if self.native_available:
                    origin = ExecutionOrigin.NATIVE_SUBAGENT
                    context_id = f"{self.name}-{recommendation.role.value}"
                    self.native_dispatches.append((recommendation.role.value, self.model_context))
                else:
                    origin = ExecutionOrigin.CURRENT_AGENT_FALLBACK
                    context_id = f"{self.name}-shared-parent"
                runs.append(
                    ReviewerRun(
                        reviewer_id=f"{recommendation.role.value}-1",
                        role=recommendation.role,
                        origin=origin,
                        target=self.name,
                        context_id=context_id,
                        status=ReviewerStatus.SUCCEEDED,
                    )
                )

        targets = parse_external_targets(request, current_host=self.name)
        plan.requested_external_targets = targets
        for target in targets:
            self.external_dispatches.append(target)
            status = self.external_outcomes.get(target, ReviewerStatus.SUCCEEDED)
            origin = (
                ExecutionOrigin.EXTERNAL_PROVIDER
                if target.startswith("openrouter:")
                else ExecutionOrigin.EXTERNAL_HOST
            )
            role = (
                plan.selected_roles[0].role
                if plan.selected_roles
                else ReviewerRole.CORRECTNESS
            )
            runs.append(
                ReviewerRun(
                    reviewer_id=f"external-{target}",
                    role=role,
                    origin=origin,
                    target=target,
                    context_id=f"external-{target}",
                    status=status,
                    error=None if status is ReviewerStatus.SUCCEEDED else f"{target} unavailable",
                )
            )
        return consolidate(runs, source=changes.source, plan=plan, changed_files=set(changes.files))


class ProductAcceptanceTests(unittest.TestCase):
    def test_ae1_codex_native_uses_only_codex_without_external_consent(self) -> None:
        host = FakeHost("codex", True, installed_external_tools={"claude"})
        result = host.review(change("app.py"))
        self.assertEqual(result.execution_mode, ExecutionMode.NATIVE_MULTI_AGENT)
        self.assertTrue(result.reviewer_runs)
        self.assertTrue(all(run.target == "codex" for run in result.reviewer_runs))
        self.assertEqual(host.external_dispatches, [])

    def test_ae2_claude_bedrock_subagents_inherit_active_configuration(self) -> None:
        host = FakeHost("claude", True, model_context="amazon-bedrock")
        result = host.review(change("app.py"))
        self.assertEqual(result.execution_mode, ExecutionMode.NATIVE_MULTI_AGENT)
        self.assertTrue(host.native_dispatches)
        self.assertTrue(all(context == "amazon-bedrock" for _, context in host.native_dispatches))
        self.assertEqual(host.external_dispatches, [])

    def test_ae3_installed_hosts_do_not_expand_default_pool(self) -> None:
        host = FakeHost("codex", True, installed_external_tools={"claude", "openrouter"})
        result = host.review(change("app.py"), "Claude and OpenRouter are installed")
        self.assertTrue(all(run.origin is ExecutionOrigin.NATIVE_SUBAGENT for run in result.reviewer_runs))
        self.assertEqual(host.external_dispatches, [])

    def test_ae4_explicit_hybrid_preserves_native_review_on_partial_failure(self) -> None:
        host = FakeHost(
            "kiro",
            True,
            external_outcomes={"claude": ReviewerStatus.UNAVAILABLE},
        )
        result = host.review(change("app.py"), "Review with Codex, Claude")
        self.assertEqual(result.execution_mode, ExecutionMode.HYBRID_NATIVE_EXTERNAL)
        self.assertEqual(host.external_dispatches, ["codex", "claude"])
        self.assertTrue(result.successful_reviewer_ids)
        self.assertIn("external-claude", result.reviewer_errors)

    def test_ae5_kiro_and_cursor_use_native_or_truthful_fallback(self) -> None:
        for name in ["kiro", "cursor"]:
            with self.subTest(host=name, native=True):
                native = FakeHost(name, True).review(change("app.py"))
                self.assertEqual(native.execution_mode, ExecutionMode.NATIVE_MULTI_AGENT)
            with self.subTest(host=name, native=False):
                fallback = FakeHost(name, False).review(change("app.py"))
                self.assertEqual(fallback.execution_mode, ExecutionMode.SINGLE_AGENT_FALLBACK)
                self.assertEqual(len({run.context_id for run in fallback.reviewer_runs}), 1)

    def test_ae6_openrouter_is_labeled_as_an_external_provider(self) -> None:
        host = FakeHost("codex", True)
        result = host.review(change("app.py"), "Review with openrouter:vendor/model")
        external = [run for run in result.reviewer_runs if run.target.startswith("openrouter:")]
        self.assertEqual(len(external), 1)
        self.assertEqual(external[0].origin, ExecutionOrigin.EXTERNAL_PROVIDER)

    def test_ae7_documentation_only_change_uses_parent_only_limited(self) -> None:
        result = FakeHost("codex", True).review(change("README.md", diff="+Clarify usage.\n"))
        self.assertEqual(result.execution_mode, ExecutionMode.PARENT_ONLY_LIMITED)
        self.assertEqual(result.plan.selected_roles, [])
        self.assertEqual(result.reviewer_runs, [])

    def test_ae8_zero_configuration_uses_no_service_or_external_adapter(self) -> None:
        host = FakeHost("cursor", False, installed_external_tools={"codex", "claude"})
        result = host.review(change("app.py"))
        self.assertEqual(result.execution_mode, ExecutionMode.SINGLE_AGENT_FALLBACK)
        self.assertEqual(host.external_dispatches, [])
        self.assertTrue(all(run.target == "cursor" for run in result.reviewer_runs))


class DocumentationAcceptanceTests(unittest.TestCase):
    def test_docs_cover_primary_hosts_optional_external_review_and_real_modes(self) -> None:
        root = Path(__file__).parents[1]
        readme = (root / "README.md").read_text(encoding="utf-8")
        details = (root / "docs" / "how-it-works.md").read_text(encoding="utf-8")
        combined = f"{readme}\n{details}"
        for text in [
            "Codex",
            "Claude Code",
            "Amazon Bedrock",
            "Kiro",
            "Cursor",
            "with openrouter:<model-id>",
            "native-multi-agent",
            "single-agent-fallback",
            "parent-only-limited",
            "hybrid-parent-external",
        ]:
            self.assertIn(text, combined)
        self.assertNotIn("finish --host-provider", combined)
        self.assertNotIn("dual-model", combined.lower())

    def test_docs_show_version_two_without_default_providers(self) -> None:
        readme = (Path(__file__).parents[1] / "README.md").read_text(encoding="utf-8")
        self.assertIn('"version": 2', readme)
        config = readme.split('"version": 2', 1)[1].split("```", 1)[0]
        self.assertNotIn('"providers"', config)

    def test_repository_does_not_ignore_or_keep_coordination_state(self) -> None:
        root = Path(__file__).parents[1]
        self.assertNotIn(".review-agent-state", (root / ".gitignore").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
