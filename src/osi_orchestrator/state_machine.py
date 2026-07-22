"""Canonical, auditable lifecycle state machine for governed work."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Iterable
from uuid import UUID, uuid4


class State(StrEnum):
    PROPOSED = "proposed"
    QUALIFIED = "qualified"
    PLANNED = "planned"
    READY = "ready"
    EXECUTING = "executing"
    REVIEW = "review"
    REVISION = "revision"
    APPROVED = "approved"
    CANONICAL = "canonical"
    MONITORED = "monitored"
    SUPERSEDED = "superseded"
    BLOCKED = "blocked"
    PAUSED = "paused"
    FAILED = "failed"
    REJECTED = "rejected"
    ESCALATED = "escalated"
    CANCELLED = "cancelled"


TERMINAL_STATES = frozenset({State.SUPERSEDED, State.REJECTED, State.CANCELLED})

_ALLOWED_TRANSITIONS: dict[State, frozenset[State]] = {
    State.PROPOSED: frozenset({State.QUALIFIED, State.REJECTED, State.CANCELLED}),
    State.QUALIFIED: frozenset({State.PLANNED, State.REJECTED, State.ESCALATED, State.CANCELLED}),
    State.PLANNED: frozenset({State.READY, State.BLOCKED, State.ESCALATED, State.CANCELLED}),
    State.READY: frozenset({State.EXECUTING, State.BLOCKED, State.PAUSED, State.CANCELLED}),
    State.EXECUTING: frozenset({State.REVIEW, State.BLOCKED, State.PAUSED, State.FAILED}),
    State.REVIEW: frozenset({State.REVISION, State.APPROVED, State.REJECTED, State.ESCALATED}),
    State.REVISION: frozenset({State.EXECUTING, State.REVIEW, State.BLOCKED, State.FAILED}),
    State.APPROVED: frozenset({State.CANONICAL, State.ESCALATED}),
    State.CANONICAL: frozenset({State.MONITORED, State.SUPERSEDED}),
    State.MONITORED: frozenset({State.REVISION, State.SUPERSEDED, State.ESCALATED}),
    State.BLOCKED: frozenset(
        {State.READY, State.EXECUTING, State.REVISION, State.ESCALATED, State.CANCELLED}
    ),
    State.PAUSED: frozenset({State.READY, State.EXECUTING, State.CANCELLED}),
    State.FAILED: frozenset({State.READY, State.REVISION, State.ESCALATED, State.CANCELLED}),
    State.ESCALATED: frozenset(
        {State.PLANNED, State.READY, State.REVISION, State.REJECTED, State.CANCELLED}
    ),
    State.SUPERSEDED: frozenset(),
    State.REJECTED: frozenset(),
    State.CANCELLED: frozenset(),
}


@dataclass(frozen=True, slots=True)
class Actor:
    id: str
    role: str


@dataclass(frozen=True, slots=True)
class TransitionRequest:
    work_item_id: UUID
    from_state: State
    to_state: State
    actor: Actor
    reason: str
    evidence: tuple[str, ...] = ()
    constitutional_rule_id: str | None = None
    request_id: UUID = field(default_factory=uuid4)
    occurred_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(frozen=True, slots=True)
class AuditEvent:
    event_id: UUID
    request_id: UUID
    work_item_id: UUID
    from_state: State
    to_state: State
    actor: Actor
    reason: str
    evidence: tuple[str, ...]
    constitutional_rule_id: str | None
    occurred_at: datetime
    schema_version: str = "1.0"


class InvalidTransition(ValueError):
    """Raised when a requested lifecycle transition violates kernel policy."""


class StateMachine:
    """Validates transitions and emits immutable audit events.

    Persistence is deliberately delegated to an AuditLedger contract so the
    state engine remains deterministic and independently testable.
    """

    def allowed_targets(self, state: State) -> frozenset[State]:
        return _ALLOWED_TRANSITIONS[state]

    def validate(self, request: TransitionRequest) -> None:
        if request.to_state not in self.allowed_targets(request.from_state):
            raise InvalidTransition(
                f"Transition {request.from_state.value} -> {request.to_state.value} is not allowed"
            )
        if not request.reason.strip():
            raise InvalidTransition("Every transition requires a non-empty reason")
        if request.to_state is State.ESCALATED and not request.constitutional_rule_id:
            raise InvalidTransition("Escalation requires a constitutional rule identifier")
        if request.from_state in TERMINAL_STATES:
            raise InvalidTransition(f"Terminal state {request.from_state.value} cannot transition")

    def transition(self, request: TransitionRequest) -> AuditEvent:
        self.validate(request)
        return AuditEvent(
            event_id=uuid4(),
            request_id=request.request_id,
            work_item_id=request.work_item_id,
            from_state=request.from_state,
            to_state=request.to_state,
            actor=request.actor,
            reason=request.reason.strip(),
            evidence=request.evidence,
            constitutional_rule_id=request.constitutional_rule_id,
            occurred_at=request.occurred_at,
        )

    def replay(self, initial_state: State, events: Iterable[AuditEvent]) -> State:
        current = initial_state
        for event in events:
            if event.from_state is not current:
                raise InvalidTransition(
                    f"Audit stream expected {current.value}, found {event.from_state.value}"
                )
            self.validate(
                TransitionRequest(
                    request_id=event.request_id,
                    work_item_id=event.work_item_id,
                    from_state=event.from_state,
                    to_state=event.to_state,
                    actor=event.actor,
                    reason=event.reason,
                    evidence=event.evidence,
                    constitutional_rule_id=event.constitutional_rule_id,
                    occurred_at=event.occurred_at,
                )
            )
            current = event.to_state
        return current
