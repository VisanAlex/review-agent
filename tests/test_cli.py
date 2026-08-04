from __future__ import annotations

import contextlib
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from review_agent.cli import main
from review_agent.models import ExecutionOrigin, ReviewerRole, ReviewerRun, ReviewerStatus


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


class CliTests(unittest.TestCase):
    def make_repo(self) -> Path:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        repo = Path(self.temp_dir.name)
        git(repo, "init", "-b", "main")
        git(repo, "config", "user.email", "review-agent@example.test")
        git(repo, "config", "user.name", "Review Agent Tests")
        (repo / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
        git(repo, "add", "app.py")
        git(repo, "commit", "-m", "initial")
        (repo / "app.py").write_text("VALUE = 2\n", encoding="utf-8")
        return repo

    def test_init_writes_version_two_without_external_defaults(self) -> None:
        repo = self.make_repo()
        with contextlib.redirect_stdout(io.StringIO()):
            exit_code = main(["init", "--repo", str(repo)])
        self.assertEqual(exit_code, 0)
        config = json.loads((repo / ".review-agent.json").read_text(encoding="utf-8"))
        self.assertEqual(config["version"], 2)
        self.assertNotIn("providers", config)
        self.assertEqual(config["roles"], {"include": [], "exclude": []})

    def test_plan_from_nested_directory_uses_policy_and_no_external_default(self) -> None:
        repo = self.make_repo()
        nested = repo / "src" / "feature"
        nested.mkdir(parents=True)
        (repo / ".review-agent.json").write_text(
            json.dumps(
                {
                    "version": 2,
                    "review": {"max_reviewers": 2},
                    "roles": {"include": ["performance"], "exclude": ["testing"]},
                    "external": {},
                }
            ),
            encoding="utf-8",
        )
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            exit_code = main(["plan", "--repo", str(nested)])
        self.assertEqual(exit_code, 0)
        self.assertIn("Selected specialists:", stdout.getvalue())
        self.assertIn("performance", stdout.getvalue())
        self.assertIn("External targets: none", stdout.getvalue())

    def test_plan_json_is_machine_readable_and_has_no_model_call(self) -> None:
        repo = self.make_repo()
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            exit_code = main(["plan", "--repo", str(repo), "--format", "json"])
        self.assertEqual(exit_code, 0)
        document = json.loads(stdout.getvalue())
        self.assertEqual(document["schema_version"], 1)
        self.assertEqual(document["review_plan"]["requested_external_targets"], [])
        self.assertIn("app.py", document["change_context"]["files"])

    def test_invocation_limit_overrides_project_configuration(self) -> None:
        repo = self.make_repo()
        (repo / ".review-agent.json").write_text(
            json.dumps({"version": 2, "review": {"max_reviewers": 2}}),
            encoding="utf-8",
        )
        stdout = io.StringIO()

        with contextlib.redirect_stdout(stdout):
            exit_code = main(
                [
                    "plan",
                    "--repo",
                    str(repo),
                    "--request",
                    "Review using max 7 specialists",
                    "--format",
                    "json",
                ]
            )

        self.assertEqual(exit_code, 0)
        plan = json.loads(stdout.getvalue())["review_plan"]
        self.assertEqual(plan["max_reviewers"], 7)
        self.assertEqual(plan["reviewer_limit_source"], "invocation")

    def test_cli_limit_overrides_all_relevant_invocation(self) -> None:
        repo = self.make_repo()
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            self.assertEqual(
                main(
                    [
                        "context",
                        "--repo",
                        str(repo),
                        "--request",
                        "Use all relevant reviewers",
                        "--max-reviewers",
                        "5",
                    ]
                ),
                0,
            )

        plan = json.loads(stdout.getvalue())["review_plan"]
        self.assertEqual(plan["max_reviewers"], 5)
        self.assertEqual(plan["reviewer_limit_source"], "cli")

    def test_all_relevant_cli_flag_uses_the_full_roster(self) -> None:
        repo = self.make_repo()
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            self.assertEqual(
                main(["plan", "--repo", str(repo), "--all-relevant", "--format", "json"]),
                0,
            )

        plan = json.loads(stdout.getvalue())["review_plan"]
        self.assertEqual(plan["max_reviewers"], 12)
        self.assertEqual(plan["reviewer_limit_source"], "cli")

    def test_invalid_invocation_limit_returns_a_clean_error(self) -> None:
        repo = self.make_repo()
        stderr = io.StringIO()

        with contextlib.redirect_stderr(stderr):
            exit_code = main(
                ["plan", "--repo", str(repo), "--request", "Use max 13 specialists"]
            )

        self.assertEqual(exit_code, 2)
        self.assertIn("between 1 and 12", stderr.getvalue())

    def test_context_json_marks_diff_as_untrusted_and_writes_no_state(self) -> None:
        repo = self.make_repo()
        (repo / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
        memory = repo / ".review-agent"
        incidents = memory / "incidents"
        incidents.mkdir(parents=True)
        (memory / "invariants.md").write_text(
            "The app.py value is a public compatibility invariant.\n",
            encoding="utf-8",
        )
        (incidents / "app-value.md").write_text(
            "A previous app.py value change broke downstream callers.\n",
            encoding="utf-8",
        )
        git(repo, "add", ".review-agent")
        git(repo, "commit", "-m", "add review memory")
        (repo / "app.py").write_text("VALUE = 2\n", encoding="utf-8")
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            exit_code = main(["context", "--repo", str(repo), "--format", "json"])
        self.assertEqual(exit_code, 0)
        document = json.loads(stdout.getvalue())
        self.assertFalse(document["change_context"]["diff_trusted"])
        self.assertIn("VALUE = 2", document["change_context"]["diff"])
        repository_context = document["change_context"]["repository_context"]
        self.assertFalse(repository_context["trusted"])
        self.assertEqual(len(repository_context["invariants"]), 1)
        self.assertEqual(len(repository_context["incidents"]), 1)
        self.assertFalse((repo / ".review-agent-state").exists())

    def test_context_prompt_builds_one_bounded_role_assignment(self) -> None:
        repo = self.make_repo()
        (repo / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
        memory = repo / ".review-agent"
        memory.mkdir()
        (memory / "invariants.md").write_text(
            "The public value must remain stable.\n", encoding="utf-8"
        )
        git(repo, "add", ".review-agent")
        git(repo, "commit", "-m", "add review invariants")
        (repo / "app.py").write_text("VALUE = 2\n", encoding="utf-8")
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            exit_code = main(
                ["context", "--repo", str(repo), "--format", "prompt", "--role", "correctness"]
            )
        self.assertEqual(exit_code, 0)
        self.assertIn("correctness specialist", stdout.getvalue())
        self.assertIn("untrusted data", stdout.getvalue())
        self.assertIn("The public value must remain stable", stdout.getvalue())

    def test_consolidate_validates_results_and_writes_both_formats(self) -> None:
        repo = self.make_repo()
        plan_path = repo / "review-plan.json"
        result_path = repo / "review-result.json"
        markdown_path = repo / "reports" / "review.md"
        json_path = repo / "reports" / "review.json"
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            self.assertEqual(main(["plan", "--repo", str(repo), "--format", "json"]), 0)
        plan_path.write_text(stdout.getvalue(), encoding="utf-8")
        result_path.write_text(
            json.dumps(
                {
                    "reviewer_id": "correctness-1",
                    "role": "correctness",
                    "origin": "native-subagent",
                    "target": "current-host",
                    "context_id": "ctx-1",
                    "status": "succeeded",
                    "findings": [
                        {
                            "title": "Changed value breaks the contract",
                            "severity": "medium",
                            "file": "app.py",
                            "line": 1,
                            "explanation": "The changed constant is consumed as the old value.",
                            "evidence": "The diff changes VALUE from 1 to 2.",
                            "failure_scenario": "A caller rejects the unexpected value.",
                            "affected_behavior": "The public value changes incompatibly.",
                            "suggested_fix": "Restore the compatible value.",
                            "test_direction": "Assert the public constant value.",
                            "confidence": 0.8,
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        with contextlib.redirect_stdout(io.StringIO()):
            exit_code = main(
                [
                    "consolidate",
                    "--plan",
                    str(plan_path),
                    "--result",
                    str(result_path),
                    "--output",
                    str(markdown_path),
                    "--json-output",
                    str(json_path),
                ]
            )
        self.assertEqual(exit_code, 0)
        self.assertIn("Changed value breaks", markdown_path.read_text(encoding="utf-8"))
        report = json.loads(json_path.read_text(encoding="utf-8"))
        self.assertEqual(report["execution_mode"], "native-multi-agent")

    def test_version_one_config_returns_migration_guidance(self) -> None:
        repo = self.make_repo()
        (repo / ".review-agent.json").write_text(
            json.dumps({"version": 1, "providers": ["codex", "claude"]}),
            encoding="utf-8",
        )
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            exit_code = main(["plan", "--repo", str(repo)])
        self.assertEqual(exit_code, 2)
        self.assertIn("version 1", stderr.getvalue())
        self.assertIn("version 2", stderr.getvalue())
        self.assertIn("never launches external reviewers by default", stderr.getvalue())

    def test_invalid_max_reviewers_is_reported_without_traceback(self) -> None:
        repo = self.make_repo()
        (repo / ".review-agent.json").write_text(
            json.dumps({"version": 2, "review": {"max_reviewers": 0}}),
            encoding="utf-8",
        )
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            exit_code = main(["plan", "--repo", str(repo)])
        self.assertEqual(exit_code, 2)
        self.assertIn("max_reviewers", stderr.getvalue())

    def test_doctor_checks_git_without_external_targets(self) -> None:
        repo = self.make_repo()
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            exit_code = main(["doctor", "--repo", str(repo)])
        self.assertEqual(exit_code, 0)
        self.assertIn("[ok] git:", stdout.getvalue())
        self.assertNotIn("codex", stdout.getvalue().lower())
        self.assertNotIn("claude", stdout.getvalue().lower())

    def test_install_skills_defaults_to_all_four_hosts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            stdout = io.StringIO()
            with patch("review_agent.cli.Path.home", return_value=home):
                with contextlib.redirect_stdout(stdout):
                    exit_code = main(["install-skills"])
            self.assertEqual(exit_code, 0)
            self.assertTrue((home / ".agents" / "skills" / "review-agent" / "SKILL.md").exists())
            self.assertTrue((home / ".claude" / "skills" / "review-agent" / "SKILL.md").exists())
            self.assertTrue((home / ".kiro" / "skills" / "review-agent" / "SKILL.md").exists())
            self.assertTrue((home / ".cursor" / "skills" / "review-agent" / "SKILL.md").exists())

    def test_normal_commands_never_construct_external_reviews(self) -> None:
        repo = self.make_repo()
        with patch("review_agent.cli.run_external_reviews") as run_external:
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(["plan", "--repo", str(repo)]), 0)
                self.assertEqual(main(["context", "--repo", str(repo)]), 0)
        run_external.assert_not_called()

    def test_target_mention_without_with_does_not_read_or_dispatch_assignment(self) -> None:
        missing = Path(tempfile.gettempdir()) / "review-agent-missing-assignment.json"
        with patch("review_agent.cli.run_external_reviews") as run_external:
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                exit_code = main(
                    [
                        "external",
                        "--request",
                        "Claude is installed",
                        "--current-host",
                        "codex",
                        "--assignment",
                        str(missing),
                    ]
                )
        self.assertEqual(exit_code, 0)
        self.assertIn("No explicit external targets", stdout.getvalue())
        run_external.assert_not_called()

    def test_explicit_external_command_removes_current_host_and_writes_results(self) -> None:
        repo = self.make_repo()
        assignment_path = repo / "assignment.json"
        output_path = repo / "external-results.json"
        assignment_path.write_text(
            json.dumps(
                {
                    "reviewer_id": "correctness-external",
                    "role": "correctness",
                    "focus": "Check changed behavior.",
                    "exclusions": ["Do not edit files."],
                    "change_context": {
                        "repository": repo.name,
                        "repository_root": str(repo),
                        "source": "working tree",
                        "files": ["app.py"],
                        "languages": ["Python"],
                        "diff": "+VALUE = 2",
                        "diff_trusted": False,
                        "truncated": False,
                    },
                }
            ),
            encoding="utf-8",
        )
        run = ReviewerRun(
            reviewer_id="correctness-external@claude",
            role=ReviewerRole.CORRECTNESS,
            origin=ExecutionOrigin.EXTERNAL_HOST,
            target="claude",
            context_id="context@claude",
            status=ReviewerStatus.SUCCEEDED,
        )
        with patch("review_agent.cli.run_external_reviews", return_value=[run]) as external:
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                exit_code = main(
                    [
                        "external",
                        "--request",
                        "Review with Codex, Claude",
                        "--current-host",
                        "codex",
                        "--assignment",
                        str(assignment_path),
                        "--repo",
                        str(repo),
                        "--output",
                        str(output_path),
                    ]
                )
        self.assertEqual(exit_code, 0)
        self.assertEqual(external.call_args.args[0], ["claude"])
        document = json.loads(output_path.read_text(encoding="utf-8"))
        self.assertEqual(document["normalized_targets"], ["claude"])
        self.assertEqual(document["reviewer_runs"][0]["origin"], "external-host")

    def test_direct_role_dispatches_between_codex_and_claude_without_assignment_file(self) -> None:
        for current_host, target in [("claude", "codex"), ("codex", "claude")]:
            with self.subTest(current_host=current_host, target=target):
                repo = self.make_repo()
                run = ReviewerRun(
                    reviewer_id=f"correctness-external@{target}",
                    role=ReviewerRole.CORRECTNESS,
                    origin=ExecutionOrigin.EXTERNAL_HOST,
                    target=target,
                    context_id=f"correctness-external@{target}",
                    status=ReviewerStatus.SUCCEEDED,
                )
                stdout = io.StringIO()
                with patch("review_agent.cli.run_external_reviews", return_value=[run]) as external:
                    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(io.StringIO()):
                        exit_code = main(
                            [
                                "external",
                                "--repo",
                                str(repo),
                                "--request",
                                f"Review the working tree with {target}",
                                "--current-host",
                                current_host,
                                "--role",
                                "correctness",
                            ]
                        )
                self.assertEqual(exit_code, 0)
                targets, built_assignment, dispatched_repo, _ = external.call_args.args
                self.assertEqual(targets, [target])
                self.assertEqual(built_assignment.role, ReviewerRole.CORRECTNESS)
                self.assertEqual(
                    Path(built_assignment.change_context["repository_root"]), repo.resolve()
                )
                self.assertIn("VALUE = 2", built_assignment.change_context["diff"])
                self.assertIn("repository_context", built_assignment.change_context)
                self.assertEqual(dispatched_repo, repo.resolve())
                envelope = json.loads(stdout.getvalue())
                self.assertEqual(envelope["normalized_targets"], [target])

    def test_external_requires_role_or_assignment_only_after_authorization(self) -> None:
        repo = self.make_repo()
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            exit_code = main(
                [
                    "external",
                    "--repo",
                    str(repo),
                    "--request",
                    "Review with codex",
                    "--current-host",
                    "claude",
                ]
            )
        self.assertEqual(exit_code, 2)
        self.assertIn("requires --role or --assignment", stderr.getvalue())

    def test_external_fallback_role_gets_actionable_focus_when_plan_selects_none(self) -> None:
        repo = self.make_repo()
        (repo / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
        (repo / "README.md").write_text("Documentation only.\n", encoding="utf-8")
        run = ReviewerRun(
            reviewer_id="correctness-external@codex",
            role=ReviewerRole.CORRECTNESS,
            origin=ExecutionOrigin.EXTERNAL_HOST,
            target="codex",
            context_id="correctness-external@codex",
            status=ReviewerStatus.SUCCEEDED,
        )

        with patch("review_agent.cli.run_external_reviews", return_value=[run]) as external:
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                exit_code = main(
                    [
                        "external",
                        "--repo",
                        str(repo),
                        "--request",
                        "Review the working tree with codex",
                        "--current-host",
                        "claude",
                        "--role",
                        "correctness",
                    ]
                )

        self.assertEqual(exit_code, 0)
        built_assignment = external.call_args.args[1]
        self.assertEqual(
            built_assignment.focus,
            "Review the changed code for concrete correctness risks.",
        )
        self.assertNotIn("No specialist signal", built_assignment.focus)

    def test_consolidate_accepts_external_result_envelope(self) -> None:
        repo = self.make_repo()
        plan_path = repo / "plan.json"
        result_path = repo / "external.json"
        plan_stdout = io.StringIO()
        with contextlib.redirect_stdout(plan_stdout):
            self.assertEqual(main(["plan", "--repo", str(repo), "--format", "json"]), 0)
        plan_document = json.loads(plan_stdout.getvalue())
        plan_document["review_plan"]["requested_external_targets"] = ["claude"]
        plan_path.write_text(json.dumps(plan_document), encoding="utf-8")
        result_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "normalized_targets": ["openrouter:vendor/model"],
                    "reviewer_runs": [
                        {
                            "reviewer_id": "correctness@openrouter",
                            "role": "correctness",
                            "origin": "external-provider",
                            "target": "openrouter:vendor/model",
                            "context_id": "external-1",
                            "status": "unavailable",
                            "error": "OPENROUTER_API_KEY is not set",
                            "findings": [],
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            exit_code = main(["consolidate", "--plan", str(plan_path), "--result", str(result_path)])
        self.assertEqual(exit_code, 0)
        self.assertIn("claude", stdout.getvalue())
        self.assertIn("openrouter:vendor/model", stdout.getvalue())
        self.assertIn("incomplete coverage", stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
