from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


SEVERITY_ORDER = {
    "critical": 4,
    "high": 3,
    "medium": 2,
    "low": 1,
}


class StringEnum(str, Enum):
    def __str__(self) -> str:
        return self.value


class ExecutionMode(StringEnum):
    PARENT_ONLY_LIMITED = "parent-only-limited"
    NATIVE_MULTI_AGENT = "native-multi-agent"
    SINGLE_AGENT_FALLBACK = "single-agent-fallback"
    HYBRID_PARENT_EXTERNAL = "hybrid-parent-external"
    HYBRID_NATIVE_EXTERNAL = "hybrid-native-external"
    HYBRID_FALLBACK_EXTERNAL = "hybrid-fallback-external"


class ExecutionOrigin(StringEnum):
    PARENT_REVIEW = "parent-review"
    NATIVE_SUBAGENT = "native-subagent"
    CURRENT_AGENT_FALLBACK = "current-agent-fallback"
    EXTERNAL_HOST = "external-host"
    EXTERNAL_PROVIDER = "external-provider"


class ReviewerStatus(StringEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    UNAVAILABLE = "unavailable"
    TIMED_OUT = "timed-out"


class BrowserVerificationStatus(StringEnum):
    DECLINED = "declined"
    UNAVAILABLE = "unavailable"
    FAILED = "failed"
    COMPLETED = "completed"


class BrowserCheckStatus(StringEnum):
    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"


class BrowserAuthMethod(StringEnum):
    EXISTING_SESSION = "existing-session"
    ENVIRONMENT = "environment"
    INTERACTIVE = "interactive"


class ReviewerRole(StringEnum):
    CORRECTNESS = "correctness"
    TESTING = "testing"
    SECURITY = "security"
    DATA_INTEGRITY = "data-integrity"
    API_COMPATIBILITY = "api-compatibility"
    FRONTEND_ACCESSIBILITY = "frontend-accessibility"
    CONCURRENCY_RELIABILITY = "concurrency-reliability"
    PERFORMANCE = "performance"
    ARCHITECTURE = "architecture"
    DEPENDENCY_SUPPLY_CHAIN = "dependency-supply-chain"
    DEPLOYMENT_OPERATIONS = "deployment-operations"
    INTERNATIONALIZATION = "internationalization"


@dataclass(frozen=True)
class TargetCheck:
    target: str
    target_type: str
    available: bool
    detail: str


@dataclass(frozen=True)
class ReviewPolicy:
    max_reviewers: int = 4
    include_roles: list[ReviewerRole] = field(default_factory=list)
    exclude_roles: list[ReviewerRole] = field(default_factory=list)


@dataclass(frozen=True)
class BrowserPolicy:
    eligible: bool = False
    base_url: str | None = None
    login_url: str | None = None
    credential_env: dict[str, str] = field(default_factory=dict)
    login_same_origin: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "eligible": self.eligible,
            "base_url": self.base_url,
            "login_url": self.login_url,
            "credential_env": dict(self.credential_env),
            "login_same_origin": self.login_same_origin,
        }


@dataclass(frozen=True)
class RoleRecommendation:
    role: ReviewerRole
    reason: str
    signals: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "role": self.role.value,
            "reason": self.reason,
            "signals": list(self.signals),
        }


@dataclass
class ReviewPlan:
    source: str
    selected_roles: list[RoleRecommendation]
    skipped_roles: list[RoleRecommendation]
    requested_external_targets: list[str] = field(default_factory=list)
    max_reviewers: int = 4
    risk_signals: list[str] = field(default_factory=list)
    reviewer_limit_source: str = "default"

    @property
    def execution_mode_hint(self) -> ExecutionMode:
        if not self.selected_roles:
            return ExecutionMode.PARENT_ONLY_LIMITED
        return ExecutionMode.NATIVE_MULTI_AGENT

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "execution_mode_hint": self.execution_mode_hint.value,
            "selected_roles": [item.to_dict() for item in self.selected_roles],
            "skipped_roles": [item.to_dict() for item in self.skipped_roles],
            "requested_external_targets": list(self.requested_external_targets),
            "max_reviewers": self.max_reviewers,
            "reviewer_limit_source": self.reviewer_limit_source,
            "risk_signals": list(self.risk_signals),
        }


@dataclass(frozen=True)
class ReviewerAssignment:
    reviewer_id: str
    role: ReviewerRole
    focus: str
    exclusions: list[str]
    change_context: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["role"] = self.role.value
        return value


@dataclass
class AffectedLocation:
    file: str
    line: int | None
    relationship: str


@dataclass
class Finding:
    title: str
    severity: str
    file: str
    line: int | None
    explanation: str
    evidence: str
    failure_scenario: str
    affected_behavior: str
    suggested_fix: str
    test_direction: str
    confidence: float
    affected_locations: list[AffectedLocation] = field(default_factory=list)
    reviewer_ids: list[str] = field(default_factory=list)
    context_ids: list[str] = field(default_factory=list)

    @property
    def corroborated(self) -> bool:
        return len(set(self.context_ids)) > 1

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["corroborated"] = self.corroborated
        value["independent_context_count"] = len(set(self.context_ids))
        return value


@dataclass
class ReviewerRun:
    reviewer_id: str
    role: ReviewerRole
    origin: ExecutionOrigin
    target: str
    context_id: str
    status: ReviewerStatus
    findings: list[Finding] = field(default_factory=list)
    duration_seconds: float = 0.0
    cost_usd: float | None = None
    error: str | None = None

    @property
    def succeeded(self) -> bool:
        return self.status is ReviewerStatus.SUCCEEDED and self.error is None

    def to_dict(self, *, include_findings: bool = False) -> dict[str, Any]:
        value: dict[str, Any] = {
            "reviewer_id": self.reviewer_id,
            "role": self.role.value,
            "origin": self.origin.value,
            "target": self.target,
            "context_id": self.context_id,
            "status": self.status.value,
            "duration_seconds": round(self.duration_seconds, 3),
            "cost_usd": self.cost_usd,
            "error": self.error,
        }
        if include_findings:
            value["findings"] = [finding.to_dict() for finding in self.findings]
        return value


@dataclass(frozen=True)
class BrowserArtifact:
    type: str
    path: str
    description: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class BrowserCheck:
    name: str
    status: BrowserCheckStatus
    route: str
    reproduction_steps: list[str]
    expected: str
    observed: str
    evidence: str
    artifacts: list[BrowserArtifact] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["status"] = self.status.value
        value["artifacts"] = [artifact.to_dict() for artifact in self.artifacts]
        return value


@dataclass(frozen=True)
class BrowserVerificationRun:
    status: BrowserVerificationStatus
    target: str | None = None
    display_url: str | None = None
    auth_method: BrowserAuthMethod | None = None
    duration_seconds: float = 0.0
    checks: list[BrowserCheck] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "status": self.status.value,
            "target": self.target,
            "display_url": self.display_url,
            "auth_method": self.auth_method.value if self.auth_method is not None else None,
            "duration_seconds": round(self.duration_seconds, 3),
            "checks": [check.to_dict() for check in self.checks],
            "limitations": list(self.limitations),
            "error": self.error,
        }


@dataclass
class ReviewResult:
    source: str
    execution_mode: ExecutionMode
    plan: ReviewPlan
    findings: list[Finding]
    reviewer_runs: list[ReviewerRun]
    browser_verification: BrowserVerificationRun | None = None

    @property
    def corroborated_count(self) -> int:
        return sum(1 for finding in self.findings if finding.corroborated)

    @property
    def reviewer_errors(self) -> dict[str, str]:
        return {
            run.reviewer_id: run.error or run.status.value
            for run in self.reviewer_runs
            if not run.succeeded
        }

    @property
    def successful_reviewer_ids(self) -> list[str]:
        return sorted(run.reviewer_id for run in self.reviewer_runs if run.succeeded)

    def to_dict(self) -> dict[str, Any]:
        value = {
            "source": self.source,
            "execution_mode": self.execution_mode.value,
            "plan": self.plan.to_dict(),
            "findings": [finding.to_dict() for finding in self.findings],
            "corroborated_count": self.corroborated_count,
            "successful_reviewer_ids": self.successful_reviewer_ids,
            "reviewer_errors": self.reviewer_errors,
            "reviewers": [run.to_dict() for run in self.reviewer_runs],
        }
        if self.browser_verification is not None:
            value["browser_verification"] = self.browser_verification.to_dict()
        return value
