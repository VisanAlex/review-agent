from __future__ import annotations

import math
import re
from pathlib import Path, PurePosixPath
from typing import Any, Iterable
from urllib.parse import urlsplit, urlunsplit

from .models import (
    BrowserArtifact,
    BrowserAuthMethod,
    BrowserCheck,
    BrowserCheckStatus,
    BrowserPolicy,
    BrowserVerificationRun,
    BrowserVerificationStatus,
)


_CONFIG_FIELDS = {"base_url", "login_url", "credential_env"}
_CREDENTIAL_FIELDS = {"username", "email", "password"}
_ENV_NAME = re.compile(r"^REVIEW_AGENT_BROWSER_[A-Z0-9_]+$")
_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
_SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b(password|secret|token|cookie)\b\s*[:=]\s*[^\s,;]+"
)
_AUTHORIZATION = re.compile(r"(?i)\bauthorization\b\s*[:=]\s*(?:bearer\s+)?[^\s,;]+")


def _object(value: Any, *, label: str, fields: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    unknown = sorted(set(value) - fields)
    if unknown:
        raise ValueError(f"unknown {label} field: {unknown[0]}")
    return value


def _text(value: Any, *, label: str, maximum: int, required: bool = True) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a string")
    normalized = value.strip()
    if required and not normalized:
        raise ValueError(f"{label} must be non-empty")
    if len(normalized) > maximum:
        raise ValueError(f"{label} exceeds {maximum} characters")
    return normalized


def _redact_text(value: str, secret_values: Iterable[str]) -> str:
    redacted = value
    for secret in sorted({item for item in secret_values if len(item) >= 4}, key=len, reverse=True):
        redacted = redacted.replace(secret, "[REDACTED]")
    redacted = _AUTHORIZATION.sub("Authorization: [REDACTED]", redacted)
    redacted = _SECRET_ASSIGNMENT.sub(lambda match: f"{match.group(1)}=[REDACTED]", redacted)
    return redacted


def sanitize_http_url(value: str, *, label: str) -> str:
    normalized = _text(value, label=label, maximum=2048)
    parsed = urlsplit(normalized)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"{label} must use http or https")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError(f"{label} must not contain embedded credentials")
    if any(character in normalized for character in {"\r", "\n", "\0"}):
        raise ValueError(f"{label} contains unsafe characters")
    return urlunsplit((parsed.scheme.lower(), parsed.netloc, parsed.path, "", ""))


def _same_origin(left: str | None, right: str | None) -> bool:
    if left is None or right is None:
        return False
    left_url = urlsplit(left)
    right_url = urlsplit(right)
    return (left_url.scheme.casefold(), left_url.netloc.casefold()) == (
        right_url.scheme.casefold(),
        right_url.netloc.casefold(),
    )


def browser_policy_from_mapping(value: Any, *, eligible: bool) -> BrowserPolicy:
    if value is None:
        value = {}
    raw = _object(value, label="browser config", fields=_CONFIG_FIELDS)
    base_value = raw.get("base_url")
    login_value = raw.get("login_url")
    base_url = (
        sanitize_http_url(base_value, label="browser.base_url")
        if isinstance(base_value, str) and base_value.strip()
        else None
    )
    login_url = (
        sanitize_http_url(login_value, label="browser.login_url")
        if isinstance(login_value, str) and login_value.strip()
        else None
    )
    if base_value is not None and not isinstance(base_value, str):
        raise ValueError("browser.base_url must be a string or null")
    if login_value is not None and not isinstance(login_value, str):
        raise ValueError("browser.login_url must be a string or null")

    raw_credentials = raw.get("credential_env", {})
    if not isinstance(raw_credentials, dict):
        raise ValueError("browser.credential_env must be an object")
    unknown_credentials = sorted(set(raw_credentials) - _CREDENTIAL_FIELDS)
    if unknown_credentials:
        raise ValueError("browser.credential_env keys must be username, email, or password")
    credentials: dict[str, str] = {}
    for field, name in raw_credentials.items():
        if not isinstance(name, str) or not _ENV_NAME.fullmatch(name):
            raise ValueError(
                "browser credential environment names must start with REVIEW_AGENT_BROWSER_"
            )
        credentials[field] = name

    return BrowserPolicy(
        eligible=eligible,
        base_url=base_url,
        login_url=login_url,
        credential_env=credentials,
        login_same_origin=_same_origin(base_url, login_url),
    )


def _route(value: Any) -> str:
    route = _text(value, label="browser check route", maximum=2048)
    if route.startswith("/"):
        parsed = urlsplit(route)
        return urlunsplit(("", "", parsed.path or "/", "", ""))
    return sanitize_http_url(route, label="browser check route")


def _string_list(
    value: Any,
    *,
    label: str,
    maximum_items: int,
    maximum_chars: int,
    secret_values: Iterable[str],
) -> list[str]:
    if not isinstance(value, list) or len(value) > maximum_items:
        raise ValueError(f"{label} must be an array with at most {maximum_items} items")
    return [
        _redact_text(
            _text(item, label=f"{label} item", maximum=maximum_chars),
            secret_values,
        )
        for item in value
    ]


def _artifact(
    value: Any,
    *,
    artifact_root: Path | None,
    login_path: str | None,
    route: str,
    secret_values: Iterable[str],
) -> BrowserArtifact:
    raw = _object(value, label="browser artifact", fields={"type", "path", "description"})
    artifact_type = _text(raw.get("type"), label="browser artifact type", maximum=32)
    if artifact_type != "screenshot":
        raise ValueError("browser artifact type must be screenshot")
    path = _text(raw.get("path"), label="browser artifact path", maximum=500)
    pure = PurePosixPath(path.replace("\\", "/"))
    if pure.is_absolute() or ".." in pure.parts or pure.suffix.casefold() not in _IMAGE_SUFFIXES:
        raise ValueError("browser artifact must be a safe relative image path")
    if login_path is not None and route == login_path:
        raise ValueError("browser artifacts must not capture the login route")
    if artifact_root is not None:
        root = artifact_root.resolve()
        candidate = root.joinpath(*pure.parts)
        cursor = candidate
        while cursor != root:
            if cursor.is_symlink():
                raise ValueError("browser artifact must not use symlinks")
            cursor = cursor.parent
        try:
            resolved = candidate.resolve(strict=True)
            resolved.relative_to(root)
        except (OSError, ValueError) as exc:
            raise ValueError("browser artifact must exist inside the current run directory") from exc
        if not resolved.is_file():
            raise ValueError("browser artifact must be a regular file")
    description = _redact_text(
        _text(raw.get("description"), label="browser artifact description", maximum=500),
        secret_values,
    )
    return BrowserArtifact(type=artifact_type, path=pure.as_posix(), description=description)


def _check(
    value: Any,
    *,
    artifact_root: Path | None,
    login_path: str | None,
    secret_values: Iterable[str],
) -> BrowserCheck:
    raw = _object(
        value,
        label="browser check",
        fields={
            "name",
            "status",
            "route",
            "reproduction_steps",
            "expected",
            "observed",
            "evidence",
            "artifacts",
        },
    )
    try:
        status = BrowserCheckStatus(str(raw.get("status")))
    except ValueError as exc:
        raise ValueError("browser check status must be passed, failed, or skipped") from exc
    route = _route(raw.get("route"))
    raw_artifacts = raw.get("artifacts", [])
    if not isinstance(raw_artifacts, list) or len(raw_artifacts) > 5:
        raise ValueError("browser check artifacts must be an array with at most 5 items")
    if status is not BrowserCheckStatus.FAILED and raw_artifacts:
        raise ValueError("browser artifacts are allowed only for failed checks")
    artifacts = [
        _artifact(
            item,
            artifact_root=artifact_root,
            login_path=login_path,
            route=route,
            secret_values=secret_values,
        )
        for item in raw_artifacts
    ]
    return BrowserCheck(
        name=_redact_text(
            _text(raw.get("name"), label="browser check name", maximum=200), secret_values
        ),
        status=status,
        route=route,
        reproduction_steps=_string_list(
            raw.get("reproduction_steps"),
            label="browser reproduction_steps",
            maximum_items=20,
            maximum_chars=500,
            secret_values=secret_values,
        ),
        expected=_redact_text(
            _text(raw.get("expected"), label="browser check expected", maximum=2000),
            secret_values,
        ),
        observed=_redact_text(
            _text(raw.get("observed"), label="browser check observed", maximum=2000),
            secret_values,
        ),
        evidence=_redact_text(
            _text(raw.get("evidence"), label="browser check evidence", maximum=4000),
            secret_values,
        ),
        artifacts=artifacts,
    )


def browser_run_from_mapping(
    value: Any,
    *,
    artifact_root: Path | None = None,
    login_url: str | None = None,
    secret_values: Iterable[str] = (),
) -> BrowserVerificationRun:
    raw = _object(
        value,
        label="browser result",
        fields={
            "schema_version",
            "status",
            "target",
            "display_url",
            "auth_method",
            "duration_seconds",
            "checks",
            "limitations",
            "error",
        },
    )
    if raw.get("schema_version") != 1:
        raise ValueError("browser result must use schema_version 1")
    try:
        status = BrowserVerificationStatus(str(raw.get("status")))
    except ValueError as exc:
        raise ValueError("browser result status is invalid") from exc

    target_value = raw.get("target")
    target = (
        _text(target_value, label="browser result target", maximum=100)
        if target_value is not None
        else None
    )
    url_value = raw.get("display_url")
    display_url = (
        sanitize_http_url(url_value, label="browser result display_url")
        if url_value is not None
        else None
    )
    auth_value = raw.get("auth_method")
    try:
        auth_method = BrowserAuthMethod(str(auth_value)) if auth_value is not None else None
    except ValueError as exc:
        raise ValueError("browser result auth_method is invalid") from exc

    try:
        duration = float(raw.get("duration_seconds", 0.0))
    except (TypeError, ValueError) as exc:
        raise ValueError("browser result duration_seconds must be numeric") from exc
    if not math.isfinite(duration) or duration < 0:
        raise ValueError("browser result duration_seconds must be finite and non-negative")

    raw_checks = raw.get("checks", [])
    if not isinstance(raw_checks, list) or len(raw_checks) > 20:
        raise ValueError("browser result checks must be an array with at most 20 items")
    login_path = urlsplit(sanitize_http_url(login_url, label="browser.login_url")).path if login_url else None
    checks = [
        _check(
            item,
            artifact_root=artifact_root,
            login_path=login_path,
            secret_values=secret_values,
        )
        for item in raw_checks
    ]
    limitations = _string_list(
        raw.get("limitations", []),
        label="browser result limitations",
        maximum_items=20,
        maximum_chars=1000,
        secret_values=secret_values,
    )
    error_value = raw.get("error")
    error = (
        _redact_text(
            _text(error_value, label="browser result error", maximum=2000), secret_values
        )
        if error_value is not None
        else None
    )

    if status is BrowserVerificationStatus.DECLINED:
        if checks or target is not None or display_url is not None or auth_method is not None or error:
            raise ValueError("declined browser result cannot contain execution data")
    elif status is BrowserVerificationStatus.UNAVAILABLE:
        if checks or not (error or limitations):
            raise ValueError("unavailable browser result requires a redacted error or limitation")
    elif status is BrowserVerificationStatus.FAILED:
        if not error:
            raise ValueError("failed browser result requires a redacted error")
    elif status is BrowserVerificationStatus.COMPLETED:
        if error is not None or not target or not display_url:
            raise ValueError("completed browser result requires target and display_url without error")

    return BrowserVerificationRun(
        status=status,
        target=target,
        display_url=display_url,
        auth_method=auth_method,
        duration_seconds=duration,
        checks=checks,
        limitations=limitations,
        error=error,
    )
