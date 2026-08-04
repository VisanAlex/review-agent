from __future__ import annotations

import unittest
from pathlib import Path

from review_agent.git_changes import ChangeSet
from review_agent.models import ReviewerRole, ReviewPolicy
from review_agent.planning import parse_reviewer_limit, recommend_roles


def changes(*files: str, diff: str = "") -> ChangeSet:
    return ChangeSet(
        repo=Path("/repo"),
        mode="working-tree",
        source="working tree",
        diff=diff,
        files=list(files),
        languages=[Path(path).suffix.lstrip(".").upper() for path in files],
        truncated=False,
    )


class RiskPlanningTests(unittest.TestCase):
    def roles(self, *files: str, diff: str = "", policy: ReviewPolicy | None = None) -> list[ReviewerRole]:
        plan = recommend_roles(changes(*files, diff=diff), policy=policy)
        return [item.role for item in plan.selected_roles]

    def test_documentation_only_change_uses_parent_only_mode(self) -> None:
        plan = recommend_roles(changes("README.md", "docs/how-it-works.md", diff="+clarify usage"))

        self.assertEqual(plan.selected_roles, [])
        self.assertEqual(plan.execution_mode_hint.value, "parent-only-limited")
        self.assertTrue(all(item.reason for item in plan.skipped_roles))

    def test_authentication_change_recommends_security_and_correctness(self) -> None:
        selected = self.roles(
            "src/auth/session.py",
            diff="+if user.has_permission('admin'):\n+    issue_token()",
        )

        self.assertIn(ReviewerRole.CORRECTNESS, selected)
        self.assertIn(ReviewerRole.SECURITY, selected)

    def test_migration_change_recommends_data_correctness_and_testing(self) -> None:
        selected = self.roles(
            "db/migrations/20260801_add_status.sql",
            "src/orders.py",
            diff="+ALTER TABLE orders ADD COLUMN status TEXT;",
        )

        self.assertIn(ReviewerRole.DATA_INTEGRITY, selected)
        self.assertIn(ReviewerRole.CORRECTNESS, selected)
        self.assertIn(ReviewerRole.TESTING, selected)

    def test_public_api_change_recommends_compatibility(self) -> None:
        selected = self.roles(
            "src/api/routes.py",
            diff="-def public_endpoint(user_id):\n+def public_endpoint(account_id):",
        )

        self.assertIn(ReviewerRole.API_COMPATIBILITY, selected)

    def test_dependency_manifest_recommends_supply_chain_without_architecture_overlap(self) -> None:
        selected = self.roles(
            "package-lock.json",
            diff='+    "left-pad": "1.3.0"',
            policy=ReviewPolicy(max_reviewers=12),
        )

        self.assertIn(ReviewerRole.DEPENDENCY_SUPPLY_CHAIN, selected)
        self.assertNotIn(ReviewerRole.ARCHITECTURE, selected)

    def test_deployment_change_recommends_operations(self) -> None:
        selected = self.roles(
            ".github/workflows/deploy.yml",
            "infra/kubernetes/api-deployment.yaml",
            diff="+readinessProbe:\n+  timeoutSeconds: 1",
            policy=ReviewPolicy(max_reviewers=12),
        )

        self.assertIn(ReviewerRole.DEPLOYMENT_OPERATIONS, selected)

    def test_locale_change_recommends_internationalization(self) -> None:
        selected = self.roles(
            "src/locales/ar/messages.po",
            diff='+msgstr "مرحبا"\n+dir="rtl"',
            policy=ReviewPolicy(max_reviewers=12),
        )

        self.assertIn(ReviewerRole.INTERNATIONALIZATION, selected)

    def test_architecture_remains_for_structural_framework_configuration(self) -> None:
        selected = self.roles(
            "src/architecture/module_boundaries.py",
            diff="+ALLOWED_DEPENDENCIES = {'domain': set()}",
            policy=ReviewPolicy(max_reviewers=12),
        )

        self.assertIn(ReviewerRole.ARCHITECTURE, selected)

    def test_test_helpers_do_not_trigger_architecture(self) -> None:
        selected = self.roles(
            "spec/rails_helper.rb",
            diff="+RSpec.configure { |config| config.order = :random }",
            policy=ReviewPolicy(max_reviewers=12),
        )

        self.assertNotIn(ReviewerRole.ARCHITECTURE, selected)

    def test_reviewer_cap_is_deterministic(self) -> None:
        change = changes(
            "src/auth/api/orders.py",
            "db/migrations/001.sql",
            "src/ui/components/order.tsx",
            diff="+async retry cache transaction permission public api query",
        )
        policy = ReviewPolicy(max_reviewers=3)

        first = recommend_roles(change, policy=policy)
        second = recommend_roles(change, policy=policy)

        self.assertEqual(first.to_dict(), second.to_dict())
        self.assertEqual(len(first.selected_roles), 3)

    def test_include_and_exclude_policy_changes_roster(self) -> None:
        policy = ReviewPolicy(
            max_reviewers=3,
            include_roles=[ReviewerRole.PERFORMANCE],
            exclude_roles=[ReviewerRole.TESTING],
        )

        selected = self.roles("src/service.py", diff="+return calculate(value)", policy=policy)

        self.assertIn(ReviewerRole.PERFORMANCE, selected)
        self.assertNotIn(ReviewerRole.TESTING, selected)

    def test_natural_language_reviewer_limits_are_deterministic(self) -> None:
        self.assertEqual(parse_reviewer_limit("Review with max 7 specialists"), 7)
        self.assertEqual(parse_reviewer_limit("Use up to 6 review agents"), 6)
        self.assertEqual(parse_reviewer_limit("Use all relevant reviewers"), 12)
        self.assertIsNone(parse_reviewer_limit("Review the current branch"))

    def test_natural_language_reviewer_limit_rejects_out_of_range_values(self) -> None:
        with self.assertRaisesRegex(ValueError, "between 1 and 12"):
            parse_reviewer_limit("Review with max 13 specialists")

    def test_conflicting_natural_language_limits_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "conflicting reviewer limits"):
            parse_reviewer_limit("Use max 5 specialists, actually use max 7 reviewers")
        with self.assertRaisesRegex(ValueError, "conflicting reviewer limits"):
            parse_reviewer_limit("Use all relevant specialists with max 7 reviewers")


if __name__ == "__main__":
    unittest.main()
