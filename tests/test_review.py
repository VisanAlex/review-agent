from __future__ import annotations

import unittest

from review_agent.models import (
    BrowserAuthMethod,
    BrowserCheck,
    BrowserCheckStatus,
    BrowserVerificationRun,
    BrowserVerificationStatus,
    ExecutionOrigin,
    ReviewerRole,
    ReviewerRun,
    ReviewerStatus,
    ReviewPlan,
    RoleRecommendation,
)
from review_agent.review import consolidate, finding_from_mapping, render_markdown


def raw_finding(
    title: str,
    *,
    file: str = "orders.py",
    line: int = 44,
    severity: str = "high",
    explanation: str = "The changed flow persists before authorization.",
    affected_locations: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    return {
        "title": title,
        "severity": severity,
        "file": file,
        "line": line,
        "explanation": explanation,
        "evidence": "save() appears before authorize().",
        "failure_scenario": "An unauthorized caller persists an order.",
        "affected_behavior": "Unauthorized writes become durable.",
        "suggested_fix": "Authorize before saving.",
        "test_direction": "Add a denied-caller regression test.",
        "confidence": 0.9,
        "affected_locations": affected_locations or [],
    }


def run(
    reviewer_id: str,
    context_id: str,
    role: ReviewerRole,
    findings: list[dict[str, object]],
    *,
    origin: ExecutionOrigin = ExecutionOrigin.NATIVE_SUBAGENT,
    status: ReviewerStatus = ReviewerStatus.SUCCEEDED,
    error: str | None = None,
) -> ReviewerRun:
    normalized = [
        finding
        for value in findings
        if (finding := finding_from_mapping(reviewer_id, context_id, value)) is not None
    ]
    return ReviewerRun(
        reviewer_id=reviewer_id,
        role=role,
        origin=origin,
        target="current-host",
        context_id=context_id,
        status=status,
        findings=normalized,
        error=error,
    )


def plan(*roles: ReviewerRole) -> ReviewPlan:
    return ReviewPlan(
        source="working tree",
        selected_roles=[RoleRecommendation(role, "test") for role in roles],
        skipped_roles=[],
    )


class ConsolidationTests(unittest.TestCase):
    def test_browser_coverage_is_separate_from_static_execution_mode(self) -> None:
        reviewer = run("frontend-1", "ctx-1", ReviewerRole.FRONTEND_ACCESSIBILITY, [])
        browser = BrowserVerificationRun(
            status=BrowserVerificationStatus.COMPLETED,
            target="current-host-browser",
            display_url="https://app.example.test/dashboard",
            auth_method=BrowserAuthMethod.EXISTING_SESSION,
            duration_seconds=2.5,
            checks=[
                BrowserCheck(
                    name="Dashboard renders",
                    status=BrowserCheckStatus.PASSED,
                    route="/dashboard",
                    reproduction_steps=["Open the dashboard."],
                    expected="The dashboard renders.",
                    observed="The dashboard rendered.",
                    evidence="The heading was visible.",
                )
            ],
        )

        review = consolidate(
            [reviewer],
            source="working tree",
            plan=plan(ReviewerRole.FRONTEND_ACCESSIBILITY),
            browser_verification=browser,
        )
        rendered = render_markdown(review)

        self.assertEqual(review.execution_mode.value, "native-multi-agent")
        self.assertIn("## Browser coverage", rendered)
        self.assertIn("Dashboard renders", rendered)
        self.assertIn("1 passed, 0 failed, 0 skipped", rendered)

    def test_report_omits_browser_section_without_a_browser_decision(self) -> None:
        review = consolidate([], source="working tree", plan=plan())

        self.assertNotIn("## Browser coverage", render_markdown(review))

    def test_declined_browser_coverage_is_reported_concisely(self) -> None:
        review = consolidate(
            [],
            source="working tree",
            plan=plan(),
            browser_verification=BrowserVerificationRun(
                status=BrowserVerificationStatus.DECLINED,
            ),
        )

        rendered = render_markdown(review)
        self.assertIn("## Browser coverage", rendered)
        self.assertIn("declined", rendered.lower())

    def test_unchanged_consumers_are_preserved_as_affected_locations(self) -> None:
        reviewer = run(
            "correctness-1",
            "ctx-1",
            ReviewerRole.CORRECTNESS,
            [
                raw_finding(
                    "Changed default breaks invoice callers",
                    file="pricing.py",
                    affected_locations=[
                        {
                            "file": "invoice.py",
                            "line": 18,
                            "relationship": "Calls changed calculate_total behavior.",
                        }
                    ],
                )
            ],
        )

        review = consolidate(
            [reviewer],
            source="working tree",
            plan=plan(ReviewerRole.CORRECTNESS),
            changed_files={"pricing.py"},
        )

        self.assertEqual(review.findings[0].file, "pricing.py")
        self.assertEqual(review.findings[0].affected_locations[0].file, "invoice.py")
        self.assertIn("Affected unchanged locations", render_markdown(review))

    def test_affected_locations_reject_unsafe_paths(self) -> None:
        value = raw_finding(
            "Unsafe affected path",
            affected_locations=[
                {
                    "file": "../outside.py",
                    "line": 1,
                    "relationship": "References changed behavior.",
                }
            ],
        )

        self.assertIsNone(finding_from_mapping("reviewer", "context", value))

    def test_changed_files_are_removed_from_affected_unchanged_locations(self) -> None:
        reviewer = run(
            "correctness-1",
            "ctx-1",
            ReviewerRole.CORRECTNESS,
            [
                raw_finding(
                    "Changed default breaks callers",
                    file="pricing.py",
                    affected_locations=[
                        {
                            "file": "checkout.py",
                            "line": 18,
                            "relationship": "Changed caller uses calculate_total.",
                        },
                        {
                            "file": "invoice.py",
                            "line": 21,
                            "relationship": "Unchanged caller uses calculate_total.",
                        },
                    ],
                )
            ],
        )

        review = consolidate(
            [reviewer],
            source="working tree",
            plan=plan(ReviewerRole.CORRECTNESS),
            changed_files={"pricing.py", "checkout.py"},
        )

        self.assertEqual(
            [location.file for location in review.findings[0].affected_locations],
            ["invoice.py"],
        )

    def test_merged_findings_union_affected_locations(self) -> None:
        first = run(
            "correctness-1",
            "ctx-1",
            ReviewerRole.CORRECTNESS,
            [
                raw_finding(
                    "Authorization happens after mutation",
                    affected_locations=[
                        {"file": "api.py", "line": 9, "relationship": "Calls save_order."}
                    ],
                )
            ],
        )
        second = run(
            "security-1",
            "ctx-2",
            ReviewerRole.SECURITY,
            [
                raw_finding(
                    "Mutation occurs before authorization",
                    line=45,
                    affected_locations=[
                        {"file": "worker.py", "line": 21, "relationship": "Calls save_order."}
                    ],
                )
            ],
        )

        review = consolidate(
            [first, second],
            source="working tree",
            plan=plan(ReviewerRole.CORRECTNESS, ReviewerRole.SECURITY),
        )

        self.assertEqual(
            {location.file for location in review.findings[0].affected_locations},
            {"api.py", "worker.py"},
        )

    def test_similar_findings_from_distinct_contexts_are_corroborated(self) -> None:
        first = run(
            "correctness-1",
            "ctx-1",
            ReviewerRole.CORRECTNESS,
            [raw_finding("Authorization happens after mutation")],
        )
        second = run(
            "security-1",
            "ctx-2",
            ReviewerRole.SECURITY,
            [
                raw_finding(
                    "Mutation occurs before authorization",
                    line=46,
                    severity="medium",
                    explanation="The changed path saves before checking permission.",
                )
            ],
        )

        review = consolidate(
            [first, second],
            source="working tree",
            plan=plan(ReviewerRole.CORRECTNESS, ReviewerRole.SECURITY),
        )

        self.assertEqual(len(review.findings), 1)
        self.assertEqual(review.findings[0].reviewer_ids, ["correctness-1", "security-1"])
        self.assertEqual(review.findings[0].severity, "high")
        self.assertEqual(review.corroborated_count, 1)

    def test_fallback_roles_in_same_context_are_not_corroboration(self) -> None:
        first = run(
            "fallback-correctness",
            "parent",
            ReviewerRole.CORRECTNESS,
            [raw_finding("Authorization happens after mutation")],
            origin=ExecutionOrigin.CURRENT_AGENT_FALLBACK,
        )
        second = run(
            "fallback-security",
            "parent",
            ReviewerRole.SECURITY,
            [raw_finding("Mutation occurs before authorization", line=45)],
            origin=ExecutionOrigin.CURRENT_AGENT_FALLBACK,
        )

        review = consolidate(
            [first, second],
            source="working tree",
            plan=plan(ReviewerRole.CORRECTNESS, ReviewerRole.SECURITY),
        )

        self.assertEqual(review.execution_mode.value, "single-agent-fallback")
        self.assertEqual(review.corroborated_count, 0)

    def test_failed_reviewer_is_reported_without_discarding_success(self) -> None:
        success = run("correctness-1", "ctx-1", ReviewerRole.CORRECTNESS, [])
        failure = run(
            "security-1",
            "ctx-2",
            ReviewerRole.SECURITY,
            [],
            status=ReviewerStatus.UNAVAILABLE,
            error="native dispatch denied",
        )

        review = consolidate(
            [failure, success],
            source="working tree",
            plan=plan(ReviewerRole.CORRECTNESS, ReviewerRole.SECURITY),
        )
        rendered = render_markdown(review)

        self.assertIn("security-1", review.reviewer_errors)
        self.assertIn("native dispatch denied", rendered)
        self.assertIn("No actionable findings", rendered)

    def test_findings_for_files_outside_the_change_are_discarded(self) -> None:
        reviewer = run(
            "correctness-1",
            "ctx-1",
            ReviewerRole.CORRECTNESS,
            [raw_finding("Unrelated issue", file="old_unmodified.py")],
        )

        review = consolidate(
            [reviewer],
            source="working tree",
            plan=plan(ReviewerRole.CORRECTNESS),
            changed_files={"app.py"},
        )

        self.assertEqual(review.findings, [])

    def test_each_reviewer_is_capped_at_twenty_findings(self) -> None:
        findings = [
            raw_finding(
                f"Defect {index}",
                file=f"file_{index}.py",
                line=index + 1,
                severity="low",
                explanation=f"Concrete defect number {index} changes behavior.",
            )
            for index in range(25)
        ]
        reviewer = run("correctness-1", "ctx-1", ReviewerRole.CORRECTNESS, findings)

        review = consolidate(
            [reviewer],
            source="working tree",
            plan=plan(ReviewerRole.CORRECTNESS),
        )

        self.assertEqual(len(review.findings), 20)

    def test_missing_failure_scenario_is_rejected(self) -> None:
        value = raw_finding("Incomplete finding")
        value["failure_scenario"] = ""

        self.assertIsNone(finding_from_mapping("reviewer", "context", value))

    def test_same_title_in_different_files_is_not_merged(self) -> None:
        reviewer = run(
            "correctness-1",
            "ctx-1",
            ReviewerRole.CORRECTNESS,
            [
                raw_finding("Missing validation", file="users.py"),
                raw_finding("Missing validation", file="orders.py"),
            ],
        )

        review = consolidate(
            [reviewer],
            source="working tree",
            plan=plan(ReviewerRole.CORRECTNESS),
        )

        self.assertEqual(len(review.findings), 2)

    def test_external_review_with_empty_roster_uses_truthful_hybrid_parent_mode(self) -> None:
        external = run(
            "external-docs",
            "external-context",
            ReviewerRole.CORRECTNESS,
            [],
            origin=ExecutionOrigin.EXTERNAL_PROVIDER,
        )
        review_plan = plan()
        review_plan.requested_external_targets = ["openrouter:vendor/model"]

        review = consolidate([external], source="working tree", plan=review_plan)
        rendered = render_markdown(review)

        self.assertEqual(review.execution_mode.value, "hybrid-parent-external")
        self.assertIn("limited parent review", rendered)


if __name__ == "__main__":
    unittest.main()
