from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from osi_orchestrator import Actor, State, TransitionRequest
from osi_orchestrator.storage import (
    ConcurrencyError,
    DuplicateRequestError,
    SQLiteStore,
)


def request(
    work_item_id: UUID,
    from_state: State,
    to_state: State,
    *,
    request_id: UUID | None = None,
    seconds: int = 0,
) -> TransitionRequest:
    return TransitionRequest(
        request_id=request_id or uuid4(),
        work_item_id=work_item_id,
        from_state=from_state,
        to_state=to_state,
        actor=Actor(id="agent-1", role="planner"),
        reason="advance governed work",
        evidence=("artifact://plan",),
        occurred_at=datetime(2026, 7, 22, 3, 0, tzinfo=UTC)
        + timedelta(seconds=seconds),
    )


def test_transition_survives_restart_and_replays(tmp_path: Path) -> None:
    database = tmp_path / "kernel.db"
    work_item_id = uuid4()
    created_at = datetime(2026, 7, 22, 2, 59, tzinfo=UTC)

    with SQLiteStore(database) as store:
        store.create(work_item_id, State.PROPOSED, created_at)
        updated = store.apply_transition(
            request(work_item_id, State.PROPOSED, State.QUALIFIED),
            expected_version=0,
        )
        assert updated.state is State.QUALIFIED
        assert updated.version == 1

    with SQLiteStore(database) as restarted:
        recovered = restarted.get(work_item_id)
        assert recovered.state is State.QUALIFIED
        assert recovered.version == 1
        assert restarted.rebuild_state(work_item_id) is State.QUALIFIED
        assert restarted.verify_snapshot(work_item_id)
        assert len(restarted.events_for(work_item_id)) == 1


def test_duplicate_transition_request_is_rejected_without_mutation(
    tmp_path: Path,
) -> None:
    database = tmp_path / "kernel.db"
    work_item_id = uuid4()
    request_id = uuid4()

    with SQLiteStore(database) as store:
        store.create(work_item_id, State.PROPOSED, datetime.now(UTC))
        first = request(
            work_item_id,
            State.PROPOSED,
            State.QUALIFIED,
            request_id=request_id,
        )
        store.apply_transition(first, expected_version=0)

        duplicate = request(
            work_item_id,
            State.QUALIFIED,
            State.PLANNED,
            request_id=request_id,
            seconds=1,
        )
        with pytest.raises(DuplicateRequestError):
            store.apply_transition(duplicate, expected_version=1)

        current = store.get(work_item_id)
        assert current.state is State.QUALIFIED
        assert current.version == 1
        assert len(store.events_for(work_item_id)) == 1


def test_stale_version_is_rejected(tmp_path: Path) -> None:
    work_item_id = uuid4()
    with SQLiteStore(tmp_path / "kernel.db") as store:
        store.create(work_item_id, State.PROPOSED, datetime.now(UTC))
        store.apply_transition(
            request(work_item_id, State.PROPOSED, State.QUALIFIED),
            expected_version=0,
        )

        with pytest.raises(ConcurrencyError):
            store.apply_transition(
                request(work_item_id, State.QUALIFIED, State.PLANNED, seconds=1),
                expected_version=0,
            )

        assert store.get(work_item_id).state is State.QUALIFIED
        assert len(store.events_for(work_item_id)) == 1


def test_audit_event_round_trip_preserves_governance_fields(tmp_path: Path) -> None:
    work_item_id = uuid4()
    with SQLiteStore(tmp_path / "kernel.db") as store:
        store.create(work_item_id, State.PROPOSED, datetime.now(UTC))
        transition = request(work_item_id, State.PROPOSED, State.QUALIFIED)
        store.apply_transition(transition, expected_version=0)

        [event] = store.events_for(work_item_id)
        assert event.actor == transition.actor
        assert event.reason == transition.reason
        assert event.evidence == transition.evidence
        assert event.request_id == transition.request_id
        assert event.occurred_at == transition.occurred_at
