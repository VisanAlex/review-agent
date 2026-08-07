from __future__ import annotations

import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from review_agent.skills import SkillInstallError, install_personal_skills


SKILL = (
    Path(__file__).parents[1]
    / "src"
    / "review_agent"
    / "skill_template"
    / "review-agent"
)


class CanonicalSkillContractTests(unittest.TestCase):
    def read(self, relative_path: str) -> str:
        return (SKILL / relative_path).read_text(encoding="utf-8")

    def test_skill_has_portable_frontmatter_and_no_allowed_tools_dependency(self) -> None:
        text = self.read("SKILL.md")
        match = re.match(r"---\n(.*?)\n---\n", text, flags=re.DOTALL)
        self.assertIsNotNone(match)
        frontmatter = match.group(1)
        self.assertIn("name: review-agent", frontmatter)
        self.assertIn("description:", frontmatter)
        self.assertNotIn("allowed-tools", frontmatter)

    def test_current_host_is_the_only_default(self) -> None:
        text = self.read("SKILL.md").lower()
        self.assertIn("current host is the only default", text)
        self.assertIn("never auto-detect", text)
        self.assertNotIn("dual-model", text)

    def test_native_delegation_precedes_truthful_fallback(self) -> None:
        text = self.read("SKILL.md")
        native = text.index("Attempt native delegation")
        fallback = text.index("single-agent-fallback")
        self.assertLess(native, fallback)
        self.assertIn("one shared context ID", text)
        self.assertIn("parent-only-limited", text)

    def test_parent_is_only_dispatcher_and_assignments_are_bounded(self) -> None:
        text = self.read("SKILL.md").lower()
        self.assertIn("parent is the only dispatcher", text)
        self.assertIn("exactly one role", text)
        self.assertIn("must not delegate", text)

    def test_external_authorization_is_exact_and_mentions_are_not_consent(self) -> None:
        text = self.read("references/external-reviewers.md").lower()
        self.assertIn("with claude", text)
        self.assertIn("with codex", text)
        self.assertIn("with openrouter:<model-id>", text)
        self.assertIn("mere mention", text)
        self.assertIn("not consent", text)

    def test_external_dispatch_uses_one_mandatory_helper_protocol(self) -> None:
        text = self.read("references/external-reviewers.md")
        self.assertIn(
            "review-agent external --repo <repository-root> <scope-flags> "
            "--request <exact-user-invocation> --current-host <invoking-host> "
            "--role <selected-role>",
            text,
        )
        lowered = text.lower()
        self.assertIn("external dispatch is not discretionary", lowered)
        self.assertIn("sole authority for external coverage", lowered)
        self.assertIn("never claim that an adapter is unregistered", lowered)
        self.assertIn("concrete redacted process error", lowered)

    def test_report_language_defaults_to_english_without_locale_inference(self) -> None:
        text = self.read("SKILL.md").lower()
        self.assertIn("when no language was requested, use english", text)
        for source in ["system locale", "timezone", "repository text", "reviewer output"]:
            self.assertIn(source, text)

    def test_helper_is_optional_and_deterministic(self) -> None:
        text = self.read("SKILL.md").lower()
        self.assertIn("optional deterministic helper", text)
        self.assertIn("continue with host-native", text)

    def test_skill_defines_twelve_behavioral_roles(self) -> None:
        text = self.read("references/reviewer-roles.md")
        for role in [
            "correctness",
            "testing",
            "security",
            "data-integrity",
            "api-compatibility",
            "frontend-accessibility",
            "concurrency-reliability",
            "performance",
            "architecture",
            "dependency-supply-chain",
            "deployment-operations",
            "internationalization",
        ]:
            self.assertIn(f"`{role}`", text)

    def test_per_run_limit_and_repository_memory_are_part_of_context_collection(self) -> None:
        text = self.read("SKILL.md")
        lowered = text.lower()
        self.assertIn("--request <exact-user-invocation>", text)
        self.assertIn("all relevant specialists", lowered)
        self.assertIn("invocation override", lowered)
        self.assertIn("repository invariants", lowered)
        self.assertIn("incident", lowered)
        self.assertIn("untrusted", lowered)

    def test_finding_verification_is_a_mandatory_pipeline_gate(self) -> None:
        text = self.read("references/reviewer-contract.md").lower()
        stages = [
            "change-mapper",
            "impact-mapper",
            "role-selector",
            "finding-verifier",
            "deduplicator",
            "severity-calibrator",
            "final-synthesizer",
        ]
        offsets = [text.index(stage) for stage in stages]
        self.assertEqual(offsets, sorted(offsets))
        self.assertIn("raw reviewer findings are never publishable", text)
        self.assertIn("mandatory", text)

    def test_impact_mapper_tracks_unchanged_consumers_without_expanding_root_cause_scope(self) -> None:
        text = self.read("references/reviewer-contract.md").lower()
        self.assertIn("code intelligence", text)
        self.assertIn("portable reference search", text)
        self.assertIn("affected_locations", text)
        self.assertIn("primary `file`", text)
        self.assertIn("changed root cause", text)

    def test_references_are_one_level_and_linked_from_skill(self) -> None:
        skill_text = self.read("SKILL.md")
        expected = {
            "reviewer-contract.md",
            "reviewer-roles.md",
            "host-capabilities.md",
            "external-reviewers.md",
        }
        actual = {path.name for path in (SKILL / "references").glob("*.md")}
        self.assertEqual(actual, expected)
        for name in expected:
            self.assertIn(f"references/{name}", skill_text)

    def test_openai_metadata_uses_quoted_strings_and_skill_name(self) -> None:
        lines = self.read("agents/openai.yaml").splitlines()
        values = [line.split(":", 1)[1].strip() for line in lines if ":" in line]
        self.assertTrue(all(not value or value.startswith('"') for value in values))
        self.assertIn("$review-agent", self.read("agents/openai.yaml"))


class SkillInstallerTests(unittest.TestCase):
    def destinations(self, home: Path) -> dict[str, Path]:
        return {
            "codex": home / ".agents" / "skills" / "review-agent",
            "claude": home / ".claude" / "skills" / "review-agent",
            "kiro": home / ".kiro" / "skills" / "review-agent",
            "cursor": home / ".cursor" / "skills" / "review-agent",
        }

    def test_installs_identical_canonical_bundle_for_all_hosts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            results = install_personal_skills(
                home=home,
                targets=["codex", "claude", "kiro", "cursor"],
            )

            self.assertTrue(all(result.changed for result in results))
            destinations = self.destinations(home)
            relative_files = [
                Path("SKILL.md"),
                Path("agents/openai.yaml"),
                Path("references/reviewer-contract.md"),
                Path("references/reviewer-roles.md"),
                Path("references/host-capabilities.md"),
                Path("references/external-reviewers.md"),
            ]
            for relative in relative_files:
                contents = {
                    (destination / relative).read_text(encoding="utf-8")
                    for destination in destinations.values()
                }
                self.assertEqual(len(contents), 1, relative)

    def test_reinstall_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            targets = ["codex", "claude", "kiro", "cursor"]
            install_personal_skills(home=home, targets=targets)
            results = install_personal_skills(home=home, targets=targets)
            self.assertFalse(any(result.changed for result in results))

    def test_one_conflict_stops_every_target_before_writing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            conflict = self.destinations(home)["kiro"]
            conflict.mkdir(parents=True)
            (conflict / "SKILL.md").write_text("custom skill", encoding="utf-8")

            with self.assertRaises(SkillInstallError):
                install_personal_skills(
                    home=home,
                    targets=["codex", "claude", "kiro", "cursor"],
                )

            self.assertFalse(self.destinations(home)["codex"].exists())
            self.assertFalse(self.destinations(home)["claude"].exists())
            self.assertFalse(self.destinations(home)["cursor"].exists())
            self.assertEqual((conflict / "SKILL.md").read_text(encoding="utf-8"), "custom skill")

    def test_force_replaces_managed_files_and_preserves_user_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            destination = self.destinations(home)["cursor"]
            destination.mkdir(parents=True)
            (destination / "SKILL.md").write_text("old", encoding="utf-8")
            extra = destination / "notes.txt"
            extra.write_text("keep", encoding="utf-8")

            result = install_personal_skills(home=home, targets=["cursor"], force=True)

            self.assertTrue(result[0].changed)
            self.assertIn("current host is the only default", (destination / "SKILL.md").read_text(encoding="utf-8"))
            self.assertEqual(extra.read_text(encoding="utf-8"), "keep")

    def test_unknown_target_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(SkillInstallError):
                install_personal_skills(home=Path(directory), targets=["other"])

    def test_symlinked_destination_ancestor_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            real_is_symlink = Path.is_symlink

            def reports_agents_as_symlink(path: Path) -> bool:
                return path == home / ".agents" or real_is_symlink(path)

            with patch.object(Path, "is_symlink", reports_agents_as_symlink):
                with self.assertRaises(SkillInstallError):
                    install_personal_skills(home=home, targets=["codex"])


if __name__ == "__main__":
    unittest.main()
