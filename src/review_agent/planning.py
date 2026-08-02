from __future__ import annotations

from pathlib import PurePosixPath

from .git_changes import ChangeSet
from .models import ReviewerRole, ReviewPlan, ReviewPolicy, RoleRecommendation


ROLE_ORDER = [
    ReviewerRole.CORRECTNESS,
    ReviewerRole.SECURITY,
    ReviewerRole.DATA_INTEGRITY,
    ReviewerRole.API_COMPATIBILITY,
    ReviewerRole.CONCURRENCY_RELIABILITY,
    ReviewerRole.TESTING,
    ReviewerRole.PERFORMANCE,
    ReviewerRole.FRONTEND_ACCESSIBILITY,
    ReviewerRole.ARCHITECTURE,
]

ROLE_REASONS = {
    ReviewerRole.CORRECTNESS: "Behavior-bearing code changed.",
    ReviewerRole.TESTING: "Production behavior changed and needs regression coverage review.",
    ReviewerRole.SECURITY: "Authentication, authorization, secrets, or untrusted input changed.",
    ReviewerRole.DATA_INTEGRITY: "Persistence, schema, migration, or transaction behavior changed.",
    ReviewerRole.API_COMPATIBILITY: "A public API, route, schema, or exported contract changed.",
    ReviewerRole.FRONTEND_ACCESSIBILITY: "User-interface or presentation behavior changed.",
    ReviewerRole.CONCURRENCY_RELIABILITY: "Async, retry, queue, locking, or failure handling changed.",
    ReviewerRole.PERFORMANCE: "Query, caching, batching, or hot-path behavior changed.",
    ReviewerRole.ARCHITECTURE: "Dependencies, project structure, or framework configuration changed.",
}

DOCUMENT_SUFFIXES = {".md", ".rst", ".txt", ".adoc"}
TEST_MARKERS = {"test", "tests", "spec", "specs", "__tests__"}


def _normalized_paths(changes: ChangeSet) -> list[str]:
    return [path.replace("\\", "/").lower() for path in changes.files]


def _has_path_token(paths: list[str], *tokens: str) -> bool:
    return any(any(token in path for token in tokens) for path in paths)


def _has_text(text: str, *tokens: str) -> bool:
    return any(token in text for token in tokens)


def _is_test_path(path: str) -> bool:
    parts = set(PurePosixPath(path).parts)
    stem = PurePosixPath(path).stem
    return bool(parts & TEST_MARKERS) or stem.startswith("test_") or stem.endswith("_test")


def derive_risk_signals(changes: ChangeSet) -> set[str]:
    paths = _normalized_paths(changes)
    text = changes.diff.lower()
    suffixes = {PurePosixPath(path).suffix for path in paths}
    signals: set[str] = set()

    if paths and all(PurePosixPath(path).suffix in DOCUMENT_SUFFIXES for path in paths):
        signals.add("documentation-only")
        return signals

    production_paths = [path for path in paths if not _is_test_path(path)]
    if production_paths:
        signals.update({"code-change", "testing"})

    if _has_path_token(paths, "auth", "permission", "security", "oauth", "session") or _has_text(
        text, "authorize", "permission", "password", "token", "secret", "jwt", "oauth", "sanitize"
    ):
        signals.add("security")

    if _has_path_token(paths, "migration", "schema", "database", "/db/", "model") or _has_text(
        text, "alter table", "create table", "transaction", "commit", "rollback", "foreign key"
    ):
        signals.add("data-integrity")

    if _has_path_token(paths, "/api/", "route", "controller", "openapi", "graphql", "proto") or _has_text(
        text, "public api", "public_endpoint", "breaking change", "export ", "endpoint"
    ):
        signals.add("api-compatibility")

    if _has_path_token(paths, "component", "/ui/", "frontend", "view", "template") or suffixes & {
        ".tsx",
        ".jsx",
        ".vue",
        ".html",
        ".css",
    }:
        signals.add("frontend-accessibility")

    if _has_path_token(paths, "worker", "queue", "job", "retry", "concurrent") or _has_text(
        text, "async ", "await ", "retry", "lock", "thread", "queue", "idempot"
    ):
        signals.add("concurrency-reliability")

    if _has_path_token(paths, "cache", "query", "performance") or _has_text(
        text, "cache", "batch", "bulk", "n+1", "select ", "query"
    ):
        signals.add("performance")

    manifest_names = {
        "pyproject.toml",
        "package.json",
        "cargo.toml",
        "go.mod",
        "gemfile",
        "pom.xml",
        "build.gradle",
        "build.gradle.kts",
    }
    if any(PurePosixPath(path).name in manifest_names for path in paths) or _has_path_token(
        paths, "architecture", "infrastructure", ".github/workflows"
    ):
        signals.add("architecture")

    return signals


def _automatic_roles(signals: set[str]) -> set[ReviewerRole]:
    roles: set[ReviewerRole] = set()
    if "code-change" in signals:
        roles.add(ReviewerRole.CORRECTNESS)
    if "testing" in signals:
        roles.add(ReviewerRole.TESTING)
    for role in ReviewerRole:
        if role.value in signals:
            roles.add(role)
    return roles


def recommend_roles(
    changes: ChangeSet,
    *,
    policy: ReviewPolicy | None = None,
) -> ReviewPlan:
    policy = policy or ReviewPolicy()
    if policy.max_reviewers < 1:
        raise ValueError("max_reviewers must be a positive integer")
    include = list(dict.fromkeys(policy.include_roles))
    exclude = set(policy.exclude_roles)
    overlap = set(include) & exclude
    if overlap:
        names = ", ".join(sorted(role.value for role in overlap))
        raise ValueError(f"roles cannot be both included and excluded: {names}")

    signals = derive_risk_signals(changes)
    automatic = _automatic_roles(signals)
    candidates = [*include, *[role for role in ROLE_ORDER if role in automatic and role not in include]]
    selected_roles = [role for role in candidates if role not in exclude][: policy.max_reviewers]

    selected = [
        RoleRecommendation(
            role=role,
            reason="Included by project policy." if role in include else ROLE_REASONS[role],
            signals=[signal for signal in sorted(signals) if signal == role.value or signal in {"code-change", "testing"}],
        )
        for role in selected_roles
    ]

    skipped: list[RoleRecommendation] = []
    for role in ROLE_ORDER:
        if role in selected_roles:
            continue
        if role in exclude:
            reason = "Excluded by project policy."
        elif role in candidates:
            reason = f"Skipped because the reviewer cap is {policy.max_reviewers}."
        elif "documentation-only" in signals:
            reason = "No specialist signal was found for this documentation-only change."
        else:
            reason = "No matching risk signal was found in the change."
        skipped.append(RoleRecommendation(role=role, reason=reason))

    return ReviewPlan(
        source=changes.source,
        selected_roles=selected,
        skipped_roles=skipped,
        max_reviewers=policy.max_reviewers,
        risk_signals=sorted(signals),
    )
