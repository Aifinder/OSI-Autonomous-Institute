"""Versioned canonical contracts shared by every orchestrator subsystem."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Mapping
from uuid import UUID, uuid4

from .state_machine import State

SCHEMA_VERSION = "1.0"
JsonObject = dict[str, Any]


def _now() -> datetime:
    return datetime.now(UTC)


def _required(value: str, field_name: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} must not be empty")
    return normalized


def _non_negative(value: int | float, field_name: str) -> None:
    if value < 0:
        raise ValueError(f"{field_name} must be non-negative")


def _positive(value: int, field_name: str) -> None:
    if value <= 0:
        raise ValueError(f"{field_name} must be positive")


class RiskLevel(StrEnum):
    LOW = "low"
    MODERATE = "moderate"
    HIGH = "high"
    CRITICAL = "critical"


class DependencyKind(StrEnum):
    FINISH_TO_START = "finish_to_start"
    APPROVAL = "approval"
    ARTIFACT = "artifact"
    POLICY = "policy"


class AgentRole(StrEnum):
    PRODUCTION = "production"
    REVIEW = "review"
    PLANNING = "planning"
    GOVERNANCE = "governance"


class ReviewOutcome(StrEnum):
    APPROVE = "approve"
    REVISE = "revise"
    REJECT = "reject"
    ESCALATE = "escalate"


class ApprovalOutcome(StrEnum):
    APPROVED = "approved"
    REVISION_REQUIRED = "revision_required"
    REJECTED = "rejected"
    ESCALATED = "escalated"


class ArtifactStatus(StrEnum):
    DRAFT = "draft"
    REVIEWED = "reviewed"
    CANONICAL = "canonical"
    SUPERSEDED = "superseded"


class EscalationStatus(StrEnum):
    OPEN = "open"
    RESOLVED = "resolved"
    WITHDRAWN = "withdrawn"


class PolicyEffect(StrEnum):
    ALLOW = "allow"
    DENY = "deny"
    REQUIRE_REVIEW = "require_review"
    ESCALATE = "escalate"


@dataclass(frozen=True, slots=True)
class Objective:
    title: str
    description: str
    id: UUID = field(default_factory=uuid4)
    schema_version: str = SCHEMA_VERSION
    success_criteria: tuple[str, ...] = ()
    constraints: tuple[str, ...] = ()
    risk_level: RiskLevel = RiskLevel.MODERATE
    budget_id: UUID | None = None
    policy_ids: tuple[UUID, ...] = ()
    created_by: str = "system"
    created_at: datetime = field(default_factory=_now)

    def __post_init__(self) -> None:
        object.__setattr__(self, "title", _required(self.title, "title"))
        object.__setattr__(self, "description", _required(self.description, "description"))
        object.__setattr__(self, "created_by", _required(self.created_by, "created_by"))


@dataclass(frozen=True, slots=True)
class WorkItemSpec:
    objective_id: UUID
    title: str
    description: str
    id: UUID = field(default_factory=uuid4)
    schema_version: str = SCHEMA_VERSION
    state: State = State.PROPOSED
    required_capabilities: tuple[str, ...] = ()
    required_review_gates: tuple[str, ...] = ()
    dependency_ids: tuple[UUID, ...] = ()
    assigned_agent_id: UUID | None = None
    priority: int = 100
    max_attempts: int = 3
    timeout_seconds: int = 900
    idempotency_key: str | None = None
    created_at: datetime = field(default_factory=_now)

    def __post_init__(self) -> None:
        object.__setattr__(self, "title", _required(self.title, "title"))
        object.__setattr__(self, "description", _required(self.description, "description"))
        _non_negative(self.priority, "priority")
        _positive(self.max_attempts, "max_attempts")
        _positive(self.timeout_seconds, "timeout_seconds")
        if self.id in self.dependency_ids:
            raise ValueError("A work item cannot depend on itself")


@dataclass(frozen=True, slots=True)
class Dependency:
    predecessor_id: UUID
    successor_id: UUID
    kind: DependencyKind = DependencyKind.FINISH_TO_START
    id: UUID = field(default_factory=uuid4)
    schema_version: str = SCHEMA_VERSION
    required_state: State = State.CANONICAL
    description: str = ""
    created_at: datetime = field(default_factory=_now)

    def __post_init__(self) -> None:
        if self.predecessor_id == self.successor_id:
            raise ValueError("Dependency endpoints must differ")


@dataclass(frozen=True, slots=True)
class Agent:
    name: str
    role: AgentRole
    capabilities: tuple[str, ...]
    id: UUID = field(default_factory=uuid4)
    schema_version: str = SCHEMA_VERSION
    version: str = "1.0.0"
    tool_permissions: tuple[str, ...] = ()
    domain_eligibility: tuple[str, ...] = ()
    authority_policy_ids: tuple[UUID, ...] = ()
    cost_per_execution: float = 0.0
    reliability_score: float = 1.0
    enabled: bool = True
    created_at: datetime = field(default_factory=_now)

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _required(self.name, "name"))
        if not self.capabilities:
            raise ValueError("Agent must declare at least one capability")
        _non_negative(self.cost_per_execution, "cost_per_execution")
        if not 0 <= self.reliability_score <= 1:
            raise ValueError("reliability_score must be between 0 and 1")


@dataclass(frozen=True, slots=True)
class Review:
    work_item_id: UUID
    artifact_ids: tuple[UUID, ...]
    reviewer_agent_id: UUID
    gate: str
    outcome: ReviewOutcome
    rationale: str
    id: UUID = field(default_factory=uuid4)
    schema_version: str = SCHEMA_VERSION
    evidence: tuple[str, ...] = ()
    rule_ids: tuple[UUID, ...] = ()
    reviewed_at: datetime = field(default_factory=_now)

    def __post_init__(self) -> None:
        object.__setattr__(self, "gate", _required(self.gate, "gate"))
        object.__setattr__(self, "rationale", _required(self.rationale, "rationale"))
        if not self.artifact_ids:
            raise ValueError("Review must reference at least one artifact")


@dataclass(frozen=True, slots=True)
class ApprovalDecision:
    work_item_id: UUID
    outcome: ApprovalOutcome
    decided_by: str
    rationale: str
    review_ids: tuple[UUID, ...]
    id: UUID = field(default_factory=uuid4)
    schema_version: str = SCHEMA_VERSION
    rule_ids: tuple[UUID, ...] = ()
    escalation_id: UUID | None = None
    decided_at: datetime = field(default_factory=_now)

    def __post_init__(self) -> None:
        object.__setattr__(self, "decided_by", _required(self.decided_by, "decided_by"))
        object.__setattr__(self, "rationale", _required(self.rationale, "rationale"))
        if not self.review_ids:
            raise ValueError("Approval decision requires review evidence")
        if self.outcome is ApprovalOutcome.ESCALATED and self.escalation_id is None:
            raise ValueError("Escalated approval decisions require escalation_id")


@dataclass(frozen=True, slots=True)
class Artifact:
    work_item_id: UUID
    name: str
    media_type: str
    content_uri: str
    content_hash: str
    created_by_agent_id: UUID
    id: UUID = field(default_factory=uuid4)
    schema_version: str = SCHEMA_VERSION
    version: int = 1
    status: ArtifactStatus = ArtifactStatus.DRAFT
    input_artifact_ids: tuple[UUID, ...] = ()
    supersedes_id: UUID | None = None
    metadata: Mapping[str, str] = field(default_factory=dict)
    created_at: datetime = field(default_factory=_now)

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _required(self.name, "name"))
        object.__setattr__(self, "media_type", _required(self.media_type, "media_type"))
        object.__setattr__(self, "content_uri", _required(self.content_uri, "content_uri"))
        object.__setattr__(self, "content_hash", _required(self.content_hash, "content_hash"))
        _positive(self.version, "version")


@dataclass(frozen=True, slots=True)
class Escalation:
    work_item_id: UUID
    rule_id: UUID
    question: str
    reason: str
    options: tuple[str, ...]
    recommendation: str
    id: UUID = field(default_factory=uuid4)
    schema_version: str = SCHEMA_VERSION
    risk_level: RiskLevel = RiskLevel.HIGH
    status: EscalationStatus = EscalationStatus.OPEN
    evidence: tuple[str, ...] = ()
    resolution: str | None = None
    created_at: datetime = field(default_factory=_now)
    resolved_at: datetime | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "question", _required(self.question, "question"))
        object.__setattr__(self, "reason", _required(self.reason, "reason"))
        object.__setattr__(self, "recommendation", _required(self.recommendation, "recommendation"))
        if len(self.options) < 2:
            raise ValueError("Escalation must provide at least two options")
        if self.status is EscalationStatus.RESOLVED and not self.resolution:
            raise ValueError("Resolved escalation requires a resolution")


@dataclass(frozen=True, slots=True)
class Budget:
    name: str
    currency: str
    limit: float
    id: UUID = field(default_factory=uuid4)
    schema_version: str = SCHEMA_VERSION
    spent: float = 0.0
    reserved: float = 0.0
    hard_stop: bool = True
    created_at: datetime = field(default_factory=_now)

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _required(self.name, "name"))
        object.__setattr__(self, "currency", _required(self.currency, "currency").upper())
        _non_negative(self.limit, "limit")
        _non_negative(self.spent, "spent")
        _non_negative(self.reserved, "reserved")
        if self.spent + self.reserved > self.limit:
            raise ValueError("Budget commitments exceed limit")

    @property
    def available(self) -> float:
        return self.limit - self.spent - self.reserved


@dataclass(frozen=True, slots=True)
class Policy:
    name: str
    description: str
    effect: PolicyEffect
    condition: str
    id: UUID = field(default_factory=uuid4)
    schema_version: str = SCHEMA_VERSION
    priority: int = 100
    applies_to: tuple[str, ...] = ()
    authority: str = "institution"
    enabled: bool = True
    created_at: datetime = field(default_factory=_now)

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _required(self.name, "name"))
        object.__setattr__(self, "description", _required(self.description, "description"))
        object.__setattr__(self, "condition", _required(self.condition, "condition"))
        object.__setattr__(self, "authority", _required(self.authority, "authority"))
        _non_negative(self.priority, "priority")


CANONICAL_CONTRACTS = (
    Objective,
    WorkItemSpec,
    Dependency,
    Agent,
    Review,
    ApprovalDecision,
    Artifact,
    Escalation,
    Budget,
    Policy,
)
