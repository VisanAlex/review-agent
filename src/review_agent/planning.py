from __future__ import annotations

import re
from pathlib import PurePosixPath

from .git_changes import ChangeSet
from .models import ReviewerRole, ReviewPlan, ReviewPolicy, RoleRecommendation


ROLE_ORDER = [
    ReviewerRole.CORRECTNESS,
    ReviewerRole.SECURITY,
    ReviewerRole.DATA_INTEGRITY,
    ReviewerRole.API_COMPATIBILITY,
    ReviewerRole.CONCURRENCY_RELIABILITY,
    ReviewerRole.DEPENDENCY_SUPPLY_CHAIN,
    ReviewerRole.DEPLOYMENT_OPERATIONS,
    ReviewerRole.TESTING,
    ReviewerRole.PERFORMANCE,
    ReviewerRole.FRONTEND_ACCESSIBILITY,
    ReviewerRole.INTERNATIONALIZATION,
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
    ReviewerRole.ARCHITECTURE: "Project structure, module boundaries, or framework configuration changed.",
    ReviewerRole.DEPENDENCY_SUPPLY_CHAIN: "Dependencies, lockfiles, or software supply-chain metadata changed.",
    ReviewerRole.DEPLOYMENT_OPERATIONS: "Deployment, infrastructure, CI/CD, or runtime operations changed.",
    ReviewerRole.INTERNATIONALIZATION: "Locale, translation, Unicode, timezone, or text-direction behavior changed.",
}

DOCUMENT_SUFFIXES = {".md", ".rst", ".txt", ".adoc"}
TEST_MARKERS = {"test", "tests", "spec", "specs", "__tests__"}
ROLE_LABEL = r"(?:specialists?|reviewers?|review\s+agents?|agents?)"
EXPLICIT_LIMIT_PATTERN = re.compile(
    rf"\b(?:use|using|with)?\s*(?:a\s+)?(?:max(?:imum)?(?:\s+of)?|up\s+to)\s+"
    rf"(?P<count>\d+)\s+{ROLE_LABEL}\b",
    re.IGNORECASE,
)
ALL_RELEVANT_PATTERN = re.compile(
    rf"\b(?:use|using|with)?\s*all(?:\s+the)?\s+relevant\s+{ROLE_LABEL}\b",
    re.IGNORECASE,
)

DEPENDENCY_FILES = {
    "build.gradle",
    "build.gradle.kts",
    "cargo.lock",
    "cargo.toml",
    "composer.json",
    "composer.lock",
    "directory.packages.props",
    "gemfile",
    "gemfile.lock",
    "go.mod",
    "go.sum",
    "go.work",
    "go.work.sum",
    "gradle.lockfile",
    "mix.exs",
    "mix.lock",
    "package-lock.json",
    "package.json",
    "package.resolved",
    "package.swift",
    "packages.lock.json",
    "paket.dependencies",
    "paket.lock",
    "pipfile",
    "pipfile.lock",
    "pnpm-lock.yaml",
    "podfile",
    "podfile.lock",
    "poetry.lock",
    "pom.xml",
    "pubspec.lock",
    "pubspec.yaml",
    "pyproject.toml",
    "requirements.txt",
    "uv.lock",
    "yarn.lock",
}
ARCHITECTURE_CONFIG_FILES = {
    "angular.json",
    "astro.config.mjs",
    "manage.py",
    "next.config.js",
    "next.config.mjs",
    "nuxt.config.ts",
    "settings.gradle",
    "settings.gradle.kts",
    "vite.config.js",
    "vite.config.ts",
}


def parse_reviewer_limit(request: str | None) -> int | None:
    """Return an explicit per-run reviewer cap, or ``None`` when absent."""

    if not request:
        return None
    maximum = len(ReviewerRole)
    explicit_limits = [int(match.group("count")) for match in EXPLICIT_LIMIT_PATTERN.finditer(request)]
    for limit in explicit_limits:
        if not 1 <= limit <= maximum:
            raise ValueError(f"reviewer limit must be between 1 and {maximum}")
    all_relevant = bool(ALL_RELEVANT_PATTERN.search(request))
    if (all_relevant and explicit_limits) or len(set(explicit_limits)) > 1:
        raise ValueError("conflicting reviewer limits were requested")
    if all_relevant:
        return maximum
    if not explicit_limits:
        return None
    return explicit_limits[0]


def _normalized_paths(changes: ChangeSet) -> list[str]:
    return [path.replace("\\", "/").lower() for path in changes.files]


def _has_path_token(paths: list[str], *tokens: str) -> bool:
    return any(any(token in path for token in tokens) for path in paths)


def _has_path_part(paths: list[str], *parts: str) -> bool:
    expected = set(parts)
    return any(bool(set(PurePosixPath(path).parts) & expected) for path in paths)


def _has_text(text: str, *tokens: str) -> bool:
    return any(token in text for token in tokens)


def _is_test_path(path: str) -> bool:
    parts = set(PurePosixPath(path).parts)
    stem = PurePosixPath(path).stem
    return bool(parts & TEST_MARKERS) or stem.startswith("test_") or stem.endswith("_test")


def _is_dependency_path(path: str) -> bool:
    pure = PurePosixPath(path)
    name = pure.name
    return (
        name in DEPENDENCY_FILES
        or (name.startswith("requirements") and pure.suffix == ".txt")
        or any("dependabot" in part for part in pure.parts)
        or "renovate" in name
    )


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

    if any(_is_dependency_path(path) for path in paths) or _has_path_token(
        paths, "sbom", "slsa", "supply-chain", "supply_chain"
    ):
        signals.add("dependency-supply-chain")

    if _has_path_token(
        paths,
        ".github/workflows",
        ".gitlab-ci",
        ".circleci",
        "dockerfile",
        "docker-compose",
        "compose.yaml",
        "compose.yml",
    ) or _has_path_part(
        paths,
        "deploy",
        "deployment",
        "helm",
        "infra",
        "infrastructure",
        "k8s",
        "kubernetes",
        "terraform",
    ) or any(PurePosixPath(path).suffix in {".tf", ".tfvars"} for path in paths) or _has_text(
        text,
        "healthcheck",
        "livenessprobe",
        "readinessprobe",
        "rollback strategy",
        "terraform ",
    ):
        signals.add("deployment-operations")

    if _has_path_part(
        paths,
        "i18n",
        "l10n",
        "locale",
        "locales",
        "translations",
    ) or suffixes & {".po", ".pot"} or _has_text(
        text,
        "gettext",
        "i18n",
        "l10n",
        "locale",
        "time zone",
        "timezone",
        "unicode",
        "dir=\"rtl\"",
        "dir='rtl'",
        "normalization form",
    ):
        signals.add("internationalization")

    if _has_path_token(paths, "architecture", "module-boundar", "/framework/") or any(
        PurePosixPath(path).name in ARCHITECTURE_CONFIG_FILES for path in paths
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
