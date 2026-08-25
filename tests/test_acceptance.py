from __future__ import annotations

import unittest
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from review_agent.browser import browser_run_from_mapping, sanitize_http_url
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
from review_agent.review import consolidate, render_json, render_markdown


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
    browser_available: bool = False
    browser_consent: bool | None = None
    authenticated_session: bool = False
    environment_auth: bool = False
    interactive_auth: bool = False
    environment_secret: str | None = None
    browser_url: str = "https://staging.example.test/"
    browser_check_status: str = "passed"
    mutating_flow: bool = False
    browser_prompts: int = 0
    browser_navigation: list[str] = field(default_factory=list)
    auth_attempts: list[str] = field(default_factory=list)
    approved_environment_names: list[str] = field(default_factory=list)
    events: list[str] = field(default_factory=list)

    def browser_verification(self, plan):
        if "frontend-accessibility" not in plan.risk_signals:
            return None
        self.events.append("static-verified")
        self.browser_prompts += 1
        self.events.append("browser-prompt")
        if not self.browser_consent:
            return browser_run_from_mapping({"schema_version": 1, "status": "declined"})
        if not self.browser_available:
            return browser_run_from_mapping(
                {
                    "schema_version": 1,
                    "status": "unavailable",
                    "limitations": ["The current host exposes no browser capability."],
                }
            )

        self.auth_attempts.append("existing-session")
        auth_method = None
        if self.authenticated_session:
            auth_method = "existing-session"
        else:
            self.auth_attempts.append("environment")
            if self.environment_auth:
                self.approved_environment_names = [
                    "REVIEW_AGENT_BROWSER_EMAIL",
                    "REVIEW_AGENT_BROWSER_PASSWORD",
                ]
                auth_method = "environment"
            else:
                self.auth_attempts.append("interactive")
                if self.interactive_auth:
                    auth_method = "interactive"
        if auth_method is None:
            return browser_run_from_mapping(
                {
                    "schema_version": 1,
                    "status": "unavailable",
                    "limitations": ["Authentication was not available without user interaction."],
                }
            )

        display_url = sanitize_http_url(self.browser_url, label="browser fixture URL")
        self.browser_navigation.append(display_url)
        check_status = "skipped" if self.mutating_flow else self.browser_check_status
        observed = (
            "Skipped because the flow would mutate meaningful data."
            if self.mutating_flow
            else "The affected interface matched the expected behavior."
        )
        return browser_run_from_mapping(
            {
                "schema_version": 1,
                "status": "completed",
                "target": f"{self.name}-browser",
                "display_url": display_url,
                "auth_method": auth_method,
                "duration_seconds": 1.0,
                "checks": [
                    {
                        "name": "Affected frontend flow",
                        "status": check_status,
                        "route": urlsplit(display_url).path or "/",
                        "reproduction_steps": ["Open the affected route."],
                        "expected": "The changed interface behaves correctly.",
                        "observed": observed,
                        "evidence": observed,
                        "artifacts": [],
                    }
                ],
                "limitations": [observed] if self.mutating_flow else [],
                "error": None,
            }
        )

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
        browser = self.browser_verification(plan)
        return consolidate(
            runs,
            source=changes.source,
            plan=plan,
            changed_files=set(changes.files),
            browser_verification=browser,
        )


class ProductAcceptanceTests(unittest.TestCase):
    def test_browser_ae1_frontend_prompts_once_after_static_verification(self) -> None:
        host = FakeHost("codex", True, browser_available=True, browser_consent=True, authenticated_session=True)

        result = host.review(change("component.tsx"))

        self.assertEqual(host.browser_prompts, 1)
        self.assertLess(host.events.index("static-verified"), host.events.index("browser-prompt"))
        self.assertEqual(result.browser_verification.status.value, "completed")

    def test_browser_ae2_backend_change_never_prompts_or_reports_browser_coverage(self) -> None:
        host = FakeHost("claude", True, browser_available=True, browser_consent=True)

        result = host.review(change("service.py"))

        self.assertEqual(host.browser_prompts, 0)
        self.assertIsNone(result.browser_verification)
        self.assertNotIn("Browser coverage", render_markdown(result))

    def test_browser_ae3_decline_stops_before_target_auth_or_browser_work(self) -> None:
        host = FakeHost("cursor", True, browser_available=True, browser_consent=False)

        result = host.review(change("component.vue"))

        self.assertEqual(result.browser_verification.status.value, "declined")
        self.assertEqual(host.browser_navigation, [])
        self.assertEqual(host.auth_attempts, [])

    def test_browser_ae4_existing_session_precedes_environment_auth(self) -> None:
        host = FakeHost(
            "codex",
            True,
            browser_available=True,
            browser_consent=True,
            authenticated_session=True,
            environment_auth=True,
        )

        result = host.review(change("dashboard.tsx"))

        self.assertEqual(host.auth_attempts, ["existing-session"])
        self.assertEqual(result.browser_verification.auth_method.value, "existing-session")

    def test_browser_ae5_environment_auth_never_serializes_secret_values(self) -> None:
        secret = "browser-password-sentinel"
        host = FakeHost(
            "claude",
            True,
            browser_available=True,
            browser_consent=True,
            environment_auth=True,
            environment_secret=secret,
        )

        result = host.review(change("login-form.tsx"))
        output = render_json(result) + render_markdown(result)

        self.assertEqual(
            host.approved_environment_names,
            ["REVIEW_AGENT_BROWSER_EMAIL", "REVIEW_AGENT_BROWSER_PASSWORD"],
        )
        self.assertEqual(result.browser_verification.auth_method.value, "environment")
        self.assertNotIn(secret, output)

    def test_browser_ae6_and_ae7_unavailable_coverage_preserves_static_results(self) -> None:
        for host in [
            FakeHost("kiro", True, browser_consent=True, browser_available=False),
            FakeHost("cursor", True, browser_consent=True, browser_available=True),
        ]:
            with self.subTest(host=host.name, capability=host.browser_available):
                result = host.review(change("component.tsx"))
                self.assertEqual(result.browser_verification.status.value, "unavailable")
                self.assertTrue(result.successful_reviewer_ids)
                self.assertEqual(result.execution_mode, ExecutionMode.NATIVE_MULTI_AGENT)

    def test_browser_ae8_failed_runtime_observation_does_not_invent_code_finding(self) -> None:
        host = FakeHost(
            "codex",
            True,
            browser_available=True,
            browser_consent=True,
            authenticated_session=True,
            browser_check_status="failed",
        )

        result = host.review(change("modal.tsx"))

        self.assertEqual(result.browser_verification.checks[0].status.value, "failed")
        self.assertEqual(result.findings, [])

    def test_browser_ae9_and_ae10_sanitize_target_and_skip_mutating_flow(self) -> None:
        host = FakeHost(
            "claude",
            True,
            browser_available=True,
            browser_consent=True,
            authenticated_session=True,
            browser_url="https://staging.example.test/settings?token=hidden#profile",
            mutating_flow=True,
        )

        result = host.review(change("settings.vue"))
        output = render_json(result) + render_markdown(result)

        self.assertEqual(result.browser_verification.display_url, "https://staging.example.test/settings")
        self.assertEqual(result.browser_verification.checks[0].status.value, "skipped")
        self.assertNotIn("token=hidden", output)

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
    def test_docs_cover_browser_prompt_auth_safety_and_coverage(self) -> None:
        root = Path(__file__).parents[1]
        combined = (
            (root / "README.md").read_text(encoding="utf-8")
            + (root / "docs" / "how-it-works.md").read_text(encoding="utf-8")
        )
        for text in [
            "Frontend changes detected. Run browser verification?",
            "REVIEW_AGENT_BROWSER_",
            "existing session",
            "interactive sign-in",
            "declined",
            "unavailable",
            "failed",
            "completed",
            "non-destructive",
        ]:
            self.assertIn(text, combined)
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
