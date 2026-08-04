from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from review_agent.external import (
    ClaudeExternalAdapter,
    CodexExternalAdapter,
    OpenRouterExternalAdapter,
    _windows_node_shim_command,
    build_external_prompt,
    make_adapter,
    parse_external_targets,
    run_external_reviews,
    assignment_from_mapping,
)
from review_agent.models import (
    ExecutionOrigin,
    ReviewerAssignment,
    ReviewerRole,
    ReviewerStatus,
)


FINDINGS = {
    "summary": "One bug found.",
    "findings": [
        {
            "title": "Wrong comparison",
            "severity": "high",
            "file": "app.py",
            "line": 12,
            "explanation": "The changed condition is reversed.",
            "evidence": "The new branch accepts an invalid state.",
            "failure_scenario": "An invalid request reaches the protected operation.",
            "affected_behavior": "Authorization no longer rejects the request.",
            "suggested_fix": "Reverse the comparison.",
            "test_direction": "Assert the invalid request is rejected.",
            "confidence": 0.9,
        }
    ],
}


def assignment(repo: Path) -> ReviewerAssignment:
    return ReviewerAssignment(
        reviewer_id="security-external",
        role=ReviewerRole.SECURITY,
        focus="Check the changed authorization behavior.",
        exclusions=["Do not edit files."],
        change_context={
            "repository": repo.name,
            "repository_root": str(repo),
            "source": "working tree",
            "files": ["app.py"],
            "languages": ["Python"],
            "diff": "diff --git a/app.py b/app.py\n+allowed = True\n",
            "diff_trusted": False,
            "truncated": False,
        },
    )


class RecordingRunner:
    def __init__(self, target: str, *, fail: BaseException | None = None) -> None:
        self.target = target
        self.fail = fail
        self.calls: list[tuple[list[str], dict[str, object]]] = []

    def __call__(self, args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        self.calls.append((args, kwargs))
        if self.fail:
            raise self.fail
        if "--version" in args:
            return subprocess.CompletedProcess(args, 0, stdout=f"{self.target} 1.0\n", stderr="")
        if self.target == "codex":
            output_path = Path(args[args.index("-o") + 1])
            output_path.write_text(json.dumps(FINDINGS), encoding="utf-8")
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")
        wrapper = {"type": "result", "structured_output": FINDINGS, "total_cost_usd": 0.02}
        return subprocess.CompletedProcess(args, 0, stdout=json.dumps(wrapper), stderr="")


class ExternalTargetParsingTests(unittest.TestCase):
    def test_mentions_without_with_are_not_consent(self) -> None:
        self.assertEqual(parse_external_targets("Claude and Codex are installed"), [])
        self.assertEqual(parse_external_targets("Use OpenRouter if available"), [])

    def test_explicit_targets_are_normalized_and_current_host_is_removed(self) -> None:
        targets = parse_external_targets(
            "Review this with Claude, Codex, openrouter:openai/gpt-5.4",
            current_host="claude-code",
        )
        self.assertEqual(targets, ["codex", "openrouter:openai/gpt-5.4"])

    def test_unsupported_or_duplicate_targets_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            parse_external_targets("Review with kiro")
        with self.assertRaises(ValueError):
            parse_external_targets("Review with claude, Claude")

    def test_reviewer_limit_with_clause_is_not_an_external_target(self) -> None:
        self.assertEqual(
            parse_external_targets("Review with max 7 specialists with codex"),
            ["codex"],
        )
        self.assertEqual(
            parse_external_targets("Review with up to 6 review agents"),
            [],
        )

    def test_assignment_rejects_malformed_context_fields(self) -> None:
        value = assignment(Path.cwd()).to_dict()
        value["change_context"]["diff"] = ["not", "text"]
        with self.assertRaises(ValueError):
            assignment_from_mapping(value)


class ExternalAdapterTests(unittest.TestCase):
    def test_binary_override_comes_from_environment(self) -> None:
        with patch.dict(os.environ, {"REVIEW_AGENT_CLAUDE_BIN": "C:/tools/claude.exe"}):
            adapter = make_adapter("claude")
        self.assertEqual(adapter.command, ["C:/tools/claude.exe"])

    def test_windows_npm_shim_is_resolved_to_node_process(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            node = root / "node.exe"
            script = root / "node_modules" / "example" / "cli.js"
            shim = root / "claude.cmd"
            script.parent.mkdir(parents=True)
            node.write_bytes(b"")
            script.write_text("", encoding="utf-8")
            shim.write_text(
                '@ECHO off\n"%dp0%\\node.exe" "%dp0%\\node_modules\\example\\cli.js" %*\n',
                encoding="utf-8",
            )
            self.assertEqual(
                _windows_node_shim_command(shim, ["--version"]),
                [str(node), str(script), "--version"],
            )

    def test_codex_uses_ephemeral_read_only_structured_execution(self) -> None:
        runner = RecordingRunner("codex")
        adapter = CodexExternalAdapter(["codex"], runner=runner)
        with tempfile.TemporaryDirectory() as directory:
            result = adapter.review(assignment(Path(directory)), Path(directory), 30)
        self.assertEqual(result.status, ReviewerStatus.SUCCEEDED)
        self.assertEqual(result.origin, ExecutionOrigin.EXTERNAL_HOST)
        args, kwargs = runner.calls[0]
        self.assertIn("--ephemeral", args)
        self.assertEqual(args[args.index("--sandbox") + 1], "read-only")
        self.assertIn("--output-schema", args)
        self.assertNotIn("danger-full-access", args)
        self.assertIn("untrusted", str(kwargs["input"]).lower())

    def test_claude_uses_read_tools_and_extracts_cost(self) -> None:
        runner = RecordingRunner("claude")
        adapter = ClaudeExternalAdapter(["claude"], runner=runner)
        with tempfile.TemporaryDirectory() as directory:
            result = adapter.review(assignment(Path(directory)), Path(directory), 30)
        self.assertEqual(result.status, ReviewerStatus.SUCCEEDED)
        self.assertEqual(result.cost_usd, 0.02)
        args, _ = runner.calls[0]
        self.assertEqual(args[args.index("--tools") + 1], "Read,Glob,Grep")
        self.assertNotIn("Bash", args)
        self.assertNotIn("Edit", args)
        self.assertIn("natural-language prose in English", str(runner.calls[0][1]["input"]))

    def test_external_prompt_preserves_literal_paths_and_code(self) -> None:
        task = assignment(Path.cwd())
        task.change_context["files"] = ["src/überprüfung.py"]
        task.change_context["diff"] = "+prüfung_id = '未翻译'\n"

        prompt = build_external_prompt(task)

        self.assertIn("Preserve file paths, identifiers, code excerpts", prompt)
        self.assertIn("never translate, normalize, or rewrite those literal values", prompt)
        self.assertIn("src/überprüfung.py", prompt)
        self.assertIn("prüfung_id", prompt)
        self.assertIn("未翻译", prompt)

    def test_claude_structured_output_failure_preserves_result_subtype(self) -> None:
        def runner(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            wrapper = {
                "type": "result",
                "subtype": "error_max_structured_output_retries",
                "result": "Could not satisfy the schema",
            }
            return subprocess.CompletedProcess(args, 0, stdout=json.dumps(wrapper), stderr="")

        adapter = ClaudeExternalAdapter(["claude"], runner=runner)
        with tempfile.TemporaryDirectory() as directory:
            result = adapter.review(assignment(Path(directory)), Path(directory), 30)
        self.assertEqual(result.status, ReviewerStatus.FAILED)
        self.assertIn("error_max_structured_output_retries", result.error or "")

    def test_timeout_and_validation_failure_clean_handoff_directory(self) -> None:
        for failure in [subprocess.TimeoutExpired("codex", 1), None]:
            runner = RecordingRunner("codex", fail=failure)
            if failure is None:
                runner.target = "invalid"
            with self.subTest(failure=type(failure).__name__ if failure else "invalid"):
                with tempfile.TemporaryDirectory() as root:
                    adapter = CodexExternalAdapter(["codex"], runner=runner, temp_root=Path(root))
                    result = adapter.review(assignment(Path(root)), Path(root), 1)
                    self.assertFalse(any(Path(root).iterdir()))
                    self.assertNotEqual(result.status, ReviewerStatus.SUCCEEDED)

    def test_openrouter_requires_model_and_credential(self) -> None:
        with self.assertRaises(ValueError):
            make_adapter("openrouter:")
        with patch.dict(os.environ, {}, clear=True):
            result = make_adapter("openrouter:openai/gpt-5.4").review(
                assignment(Path.cwd()), Path.cwd(), 30
            )
        self.assertEqual(result.status, ReviewerStatus.UNAVAILABLE)
        self.assertIn("OPENROUTER_API_KEY", result.error or "")

    def test_openrouter_sends_explicit_model_and_structured_schema(self) -> None:
        requests = []

        def transport(request: object, timeout: int) -> bytes:
            requests.append((request, timeout))
            return json.dumps(
                {"choices": [{"message": {"content": json.dumps(FINDINGS)}}], "usage": {"cost": 0.03}}
            ).encode("utf-8")

        adapter = OpenRouterExternalAdapter(
            "openai/gpt-5.4",
            api_key="secret-value",
            transport=transport,
        )
        result = adapter.review(assignment(Path.cwd()), Path.cwd(), 30)
        self.assertEqual(result.status, ReviewerStatus.SUCCEEDED)
        self.assertEqual(result.origin, ExecutionOrigin.EXTERNAL_PROVIDER)
        self.assertEqual(result.cost_usd, 0.03)
        request, timeout = requests[0]
        payload = json.loads(request.data)
        self.assertEqual(payload["model"], "openai/gpt-5.4")
        self.assertTrue(payload["response_format"]["json_schema"]["strict"])
        prompt_assignment = json.loads(payload["messages"][0]["content"].split("\n\n", 1)[1])
        self.assertNotIn("repository_root", prompt_assignment["change_context"])
        self.assertEqual(timeout, 30)

    def test_openrouter_failure_redacts_secret_and_never_substitutes_model(self) -> None:
        def transport(request: object, timeout: int) -> bytes:
            raise RuntimeError("structured output rejected for secret-value")

        adapter = OpenRouterExternalAdapter(
            "vendor/model",
            api_key="secret-value",
            transport=transport,
        )
        result = adapter.review(assignment(Path.cwd()), Path.cwd(), 30)
        self.assertEqual(result.status, ReviewerStatus.FAILED)
        self.assertNotIn("secret-value", result.error or "")
        self.assertEqual(result.target, "openrouter:vendor/model")

    def test_concurrent_targets_return_in_requested_order(self) -> None:
        class FakeAdapter:
            def __init__(self, target: str) -> None:
                self.target = target

            def review(self, task: ReviewerAssignment, repo: Path, timeout: int):
                time.sleep(0.03 if self.target == "claude" else 0.001)
                origin = (
                    ExecutionOrigin.EXTERNAL_PROVIDER
                    if self.target.startswith("openrouter:")
                    else ExecutionOrigin.EXTERNAL_HOST
                )
                from review_agent.models import ReviewerRun

                return ReviewerRun(
                    reviewer_id=f"reviewer@{self.target}",
                    role=task.role,
                    origin=origin,
                    target=self.target,
                    context_id=f"context@{self.target}",
                    status=ReviewerStatus.SUCCEEDED,
                )

        targets = ["claude", "openrouter:vendor/model", "codex"]
        results = run_external_reviews(
            targets,
            assignment(Path.cwd()),
            Path.cwd(),
            30,
            adapter_factory=FakeAdapter,
        )
        self.assertEqual([result.target for result in results], targets)

    def test_adapter_construction_failure_is_target_specific(self) -> None:
        class FakeAdapter:
            target = "codex"
            origin = ExecutionOrigin.EXTERNAL_HOST

            def review(self, task: ReviewerAssignment, repo: Path, timeout: int):
                from review_agent.models import ReviewerRun

                return ReviewerRun(
                    reviewer_id="codex",
                    role=task.role,
                    origin=self.origin,
                    target=self.target,
                    context_id="codex-context",
                    status=ReviewerStatus.SUCCEEDED,
                )

        def factory(target: str):
            if target == "claude":
                raise ValueError("invalid Claude executable override")
            return FakeAdapter()

        results = run_external_reviews(
            ["claude", "codex"],
            assignment(Path.cwd()),
            Path.cwd(),
            30,
            adapter_factory=factory,
        )
        self.assertEqual([result.target for result in results], ["claude", "codex"])
        self.assertEqual(results[0].status, ReviewerStatus.UNAVAILABLE)
        self.assertEqual(results[1].status, ReviewerStatus.SUCCEEDED)


if __name__ == "__main__":
    unittest.main()
