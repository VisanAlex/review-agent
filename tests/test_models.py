from __future__ import annotations

import unittest

from review_agent.models import (
    ExecutionMode,
    ExecutionOrigin,
    Finding,
    ReviewerRole,
    ReviewerRun,
    ReviewerStatus,
    ReviewPlan,
    ReviewResult,
    RoleRecommendation,
)


class ReviewModelTests(unittest.TestCase):
    def test_reviewer_roster_has_twelve_behavioral_roles(self) -> None:
        self.assertEqual(len(ReviewerRole), 12)

    def test_fallback_roles_in_one_context_do_not_create_corroboration(self) -> None:
        finding = Finding(
            title="Mutation precedes authorization",
            severity="high",
            file="orders.py",
            line=44,
            explanation="The changed path saves before checking permission.",
            evidence="save() appears before authorize().",
            failure_scenario="An unauthorized caller persists an order.",
            affected_behavior="Unauthorized writes become durable.",
            suggested_fix="Authorize before saving.",
            test_direction="Add a denied-caller regression test.",
            confidence=0.9,
            reviewer_ids=["fallback-correctness", "fallback-security"],
            context_ids=["parent-context"],
        )
        plan = ReviewPlan(source="working tree", selected_roles=[], skipped_roles=[])
        result = ReviewResult(
            source="working tree",
            execution_mode=ExecutionMode.SINGLE_AGENT_FALLBACK,
            plan=plan,
            findings=[finding],
            reviewer_runs=[
                ReviewerRun(
                    reviewer_id="fallback-correctness",
                    role=ReviewerRole.CORRECTNESS,
                    origin=ExecutionOrigin.CURRENT_AGENT_FALLBACK,
                    target="current-host",
                    context_id="parent-context",
                    status=ReviewerStatus.SUCCEEDED,
                ),
                ReviewerRun(
                    reviewer_id="fallback-security",
                    role=ReviewerRole.SECURITY,
                    origin=ExecutionOrigin.CURRENT_AGENT_FALLBACK,
                    target="current-host",
                    context_id="parent-context",
                    status=ReviewerStatus.SUCCEEDED,
                ),
            ],
        )

        self.assertEqual(result.corroborated_count, 0)
        self.assertEqual(result.to_dict()["execution_mode"], "single-agent-fallback")

    def test_distinct_contexts_are_independent(self) -> None:
        finding = Finding(
            title="Mutation precedes authorization",
            severity="high",
            file="orders.py",
            line=44,
            explanation="The changed path saves before checking permission.",
            evidence="save() appears before authorize().",
            failure_scenario="An unauthorized caller persists an order.",
            affected_behavior="Unauthorized writes become durable.",
            suggested_fix="Authorize before saving.",
            test_direction="Add a denied-caller regression test.",
            confidence=0.9,
            reviewer_ids=["native-correctness", "native-security"],
            context_ids=["ctx-1", "ctx-2"],
        )
        result = ReviewResult(
            source="working tree",
            execution_mode=ExecutionMode.NATIVE_MULTI_AGENT,
            plan=ReviewPlan(
                source="working tree",
                selected_roles=[
                    RoleRecommendation(ReviewerRole.CORRECTNESS, "code changed"),
                    RoleRecommendation(ReviewerRole.SECURITY, "authorization changed"),
                ],
                skipped_roles=[],
            ),
            findings=[finding],
            reviewer_runs=[],
        )

        self.assertEqual(result.corroborated_count, 1)
        self.assertTrue(result.findings[0].corroborated)


if __name__ == "__main__":
    unittest.main()
