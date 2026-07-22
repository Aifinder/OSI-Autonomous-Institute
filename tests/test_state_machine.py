from uuid import uuid4

import pytest

from osi_orchestrator import Actor, InvalidTransition, State, StateMachine, TransitionRequest


@pytest.fixture
def machine() -> StateMachine:
    return StateMachine()


def request(from_state: State, to_state: State, **overrides: object) -> TransitionRequest:
    values: dict[str, object] = {
        "work_item_id": uuid4(),
        "from_state": from_state,
        "to_state": to_state,
        "actor": Actor(id="agent-planner-1", role="planner"),
        "reason": "Acceptance criteria satisfied",
    }
    values.update(overrides)
    return TransitionRequest(**values)  # type: ignore[arg-type]


def test_happy_path_reaches_canonical(machine: StateMachine) -> None:
    states = [
        State.PROPOSED,
        State.QUALIFIED,
        State.PLANNED,
        State.READY,
        State.EXECUTING,
        State.REVIEW,
        State.APPROVED,
        State.CANONICAL,
    ]
    events = [machine.transition(request(a, b)) for a, b in zip(states, states[1:])]
    assert machine.replay(State.PROPOSED, events) is State.CANONICAL


def test_invalid_transition_is_rejected(machine: StateMachine) -> None:
    with pytest.raises(InvalidTransition, match="not allowed"):
        machine.transition(request(State.PROPOSED, State.CANONICAL))


def test_transition_requires_reason(machine: StateMachine) -> None:
    with pytest.raises(InvalidTransition, match="non-empty reason"):
        machine.transition(request(State.READY, State.EXECUTING, reason="  "))


def test_escalation_requires_rule_identifier(machine: StateMachine) -> None:
    with pytest.raises(InvalidTransition, match="constitutional rule"):
        machine.transition(request(State.REVIEW, State.ESCALATED))


def test_valid_escalation_records_rule(machine: StateMachine) -> None:
    event = machine.transition(
        request(
            State.REVIEW,
            State.ESCALATED,
            constitutional_rule_id="ESC-LEGAL-001",
            evidence=("review/security-42",),
        )
    )
    assert event.constitutional_rule_id == "ESC-LEGAL-001"
    assert event.evidence == ("review/security-42",)


def test_terminal_states_cannot_transition(machine: StateMachine) -> None:
    assert machine.allowed_targets(State.REJECTED) == frozenset()
    with pytest.raises(InvalidTransition):
        machine.transition(request(State.REJECTED, State.PROPOSED))


def test_replay_detects_broken_audit_stream(machine: StateMachine) -> None:
    event = machine.transition(request(State.READY, State.EXECUTING))
    with pytest.raises(InvalidTransition, match="Audit stream expected"):
        machine.replay(State.PROPOSED, [event])
