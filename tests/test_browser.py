from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from review_agent.browser import browser_policy_from_mapping, browser_run_from_mapping
from review_agent.models import BrowserCheckStatus, BrowserVerificationStatus


def completed_run(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "schema_version": 1,
        "status": "completed",
        "target": "host-browser",
        "display_url": "https://staging.example.test/address-book?token=secret#details",
        "auth_method": "existing-session",
        "duration_seconds": 1.25,
        "checks": [
            {
                "name": "Switch address-book tabs",
                "status": "passed",
                "route": "/address-book?token=secret#details",
                "reproduction_steps": ["Open the address book", "Select Personal contacts"],
                "expected": "The personal contacts panel becomes visible.",
                "observed": "The personal contacts panel became visible.",
                "evidence": "The selected tab and panel both changed.",
                "artifacts": [],
            }
        ],
        "limitations": [],
        "error": None,
    }
    value.update(overrides)
    return value


class BrowserPolicyTests(unittest.TestCase):
    def test_policy_sanitizes_urls_and_preserves_only_dedicated_environment_names(self) -> None:
        policy = browser_policy_from_mapping(
            {
                "base_url": "https://staging.example.test/app?preview=secret#panel",
                "login_url": "https://staging.example.test/login",
                "credential_env": {
                    "email": "REVIEW_AGENT_BROWSER_HRTOOLS_EMAIL",
                    "password": "REVIEW_AGENT_BROWSER_HRTOOLS_PASSWORD",
                },
            },
            eligible=True,
        )

        self.assertEqual(policy.base_url, "https://staging.example.test/app")
        self.assertEqual(policy.login_url, "https://staging.example.test/login")
        self.assertEqual(
            policy.credential_env,
            {
                "email": "REVIEW_AGENT_BROWSER_HRTOOLS_EMAIL",
                "password": "REVIEW_AGENT_BROWSER_HRTOOLS_PASSWORD",
            },
        )
        self.assertTrue(policy.login_same_origin)

    def test_policy_rejects_embedded_credentials_and_arbitrary_process_secrets(self) -> None:
        with self.assertRaisesRegex(ValueError, "embedded credentials"):
            browser_policy_from_mapping(
                {"base_url": "https://user:password@example.test/app"},
                eligible=True,
            )

        with self.assertRaisesRegex(ValueError, "REVIEW_AGENT_BROWSER_"):
            browser_policy_from_mapping(
                {
                    "credential_env": {
                        "password": "AWS_SECRET_ACCESS_KEY",
                    }
                },
                eligible=True,
            )

    def test_policy_rejects_unknown_fields_and_credential_labels(self) -> None:
        with self.assertRaisesRegex(ValueError, "unknown browser config field"):
            browser_policy_from_mapping({"start_command": "npm start"}, eligible=True)

        with self.assertRaisesRegex(ValueError, "credential_env keys"):
            browser_policy_from_mapping(
                {"credential_env": {"otp": "REVIEW_AGENT_BROWSER_OTP"}},
                eligible=True,
            )


class BrowserRunTests(unittest.TestCase):
    def test_completed_run_round_trips_without_reviewer_semantics(self) -> None:
        run = browser_run_from_mapping(completed_run())

        self.assertEqual(run.status, BrowserVerificationStatus.COMPLETED)
        self.assertEqual(run.checks[0].status, BrowserCheckStatus.PASSED)
        self.assertEqual(run.display_url, "https://staging.example.test/address-book")
        self.assertEqual(run.checks[0].route, "/address-book")
        document = run.to_dict()
        self.assertNotIn("role", document)
        self.assertNotIn("context_id", document)
        self.assertNotIn("corroborated", document)

    def test_declined_run_cannot_contain_executed_checks(self) -> None:
        with self.assertRaisesRegex(ValueError, "declined"):
            browser_run_from_mapping(completed_run(status="declined"))

    def test_unavailable_run_requires_a_redacted_error_or_limitation(self) -> None:
        with self.assertRaisesRegex(ValueError, "unavailable"):
            browser_run_from_mapping(
                completed_run(
                    status="unavailable",
                    checks=[],
                    display_url=None,
                    auth_method=None,
                    error=None,
                    limitations=[],
                )
            )

    def test_secret_values_and_secret_shaped_text_are_redacted(self) -> None:
        run = browser_run_from_mapping(
            completed_run(
                checks=[
                    {
                        "name": "Login redirect",
                        "status": "failed",
                        "route": "/login",
                        "reproduction_steps": ["Use password=hunter2"],
                        "expected": "Dashboard opens.",
                        "observed": "Authorization: Bearer abcdefgh remained visible.",
                        "evidence": "credential-value-1234 appeared in the page error.",
                        "artifacts": [],
                    }
                ]
            ),
            secret_values=["credential-value-1234"],
        )

        serialized = str(run.to_dict())
        self.assertNotIn("hunter2", serialized)
        self.assertNotIn("abcdefgh", serialized)
        self.assertNotIn("credential-value-1234", serialized)
        self.assertIn("[REDACTED]", serialized)

    def test_artifacts_must_be_regular_images_inside_the_current_run_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image = root / "failure.png"
            image.write_bytes(b"not-a-real-png")
            run = browser_run_from_mapping(
                completed_run(
                    checks=[
                        {
                            "name": "Broken modal",
                            "status": "failed",
                            "route": "/address-book",
                            "reproduction_steps": ["Open the modal"],
                            "expected": "The modal is usable.",
                            "observed": "The modal is clipped.",
                            "evidence": "The footer is outside the viewport.",
                            "artifacts": [
                                {
                                    "type": "screenshot",
                                    "path": "failure.png",
                                    "description": "Clipped modal footer",
                                }
                            ],
                        }
                    ]
                ),
                artifact_root=root,
            )

            self.assertEqual(run.checks[0].artifacts[0].path, "failure.png")

            with self.assertRaisesRegex(ValueError, "artifact"):
                browser_run_from_mapping(
                    completed_run(
                        checks=[
                            {
                                "name": "Unsafe artifact",
                                "status": "failed",
                                "route": "/address-book",
                                "reproduction_steps": ["Open the modal"],
                                "expected": "The modal is usable.",
                                "observed": "The modal is clipped.",
                                "evidence": "The footer is outside the viewport.",
                                "artifacts": [
                                    {
                                        "type": "screenshot",
                                        "path": "../outside.png",
                                        "description": "Outside the run directory",
                                    }
                                ],
                            }
                        ]
                    ),
                    artifact_root=root,
                )

            with self.assertRaisesRegex(ValueError, "login route"):
                browser_run_from_mapping(
                    completed_run(
                        checks=[
                            {
                                "name": "Unsafe login capture",
                                "status": "failed",
                                "route": "https://app.example.test/login",
                                "reproduction_steps": ["Open the login page"],
                                "expected": "No login screenshot is retained.",
                                "observed": "A login screenshot was retained.",
                                "evidence": "The artifact points at the configured login route.",
                                "artifacts": [
                                    {
                                        "type": "screenshot",
                                        "path": "failure.png",
                                        "description": "Login page",
                                    }
                                ],
                            }
                        ]
                    ),
                    artifact_root=root,
                    login_url="https://app.example.test/login",
                )


if __name__ == "__main__":
    unittest.main()
