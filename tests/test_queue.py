from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from osi_orchestrator.queue import (
    DuplicatePublicationError,
    EventEnvelope,
    LeaseConflictError,
    QueueEventKind,
    QueueStatus,
    SQLiteWorkQueue,
)


def envelope(
    key: str,
    *,
    work_item_id: UUID | None = None,
    occurred_at: datetime | None = None,
) -> EventEnvelope:
    return EventEnvelope(
        event_type="work.ready",
        work_item_id=work_item_id or uuid4(),
        payload={"task": key},
        actor_id="planner",
        idempotency_key=key,
        occurred_at=occurred_at or datetime.now(UTC),
    )


def test_queue_survives_restart_and_acknowledges(tmp_path) -> None:
    database = tmp_path / "queue.db"
    with SQLiteWorkQueue(database) as queue:
        published = queue.publish(envelope("restart"))

    with SQLiteWorkQueue(database) as queue:
        claimed = queue.claim("worker-1")
        assert claimed is not None
        assert claimed.id == published.id
        acknowledged = queue.acknowledge(claimed.id, "worker-1")
        assert acknowledged.status is QueueStatus.ACKNOWLEDGED
        assert [event.kind for event in queue.events_for(claimed.id)] == [
            QueueEventKind.PUBLISHED,
            QueueEventKind.CLAIMED,
            QueueEventKind.ACKNOWLEDGED,
        ]


def test_duplicate_publication_is_rejected(tmp_path) -> None:
    with SQLiteWorkQueue(tmp_path / "queue.db") as queue:
        queue.publish(envelope("same-key"))
        with pytest.raises(DuplicatePublicationError):
            queue.publish(envelope("same-key"))


def test_only_one_worker_can_claim_item(tmp_path) -> None:
    database = tmp_path / "queue.db"
    with SQLiteWorkQueue(database) as first, SQLiteWorkQueue(database) as second:
        first.publish(envelope("exclusive"))
        claimed = first.claim("worker-1")
        assert claimed is not None
        assert second.claim("worker-2") is None
        with pytest.raises(LeaseConflictError):
            second.acknowledge(claimed.id, "worker-2")


def test_expired_lease_is_recovered_after_restart(tmp_path) -> None:
    database = tmp_path / "queue.db"
    start = datetime(2026, 1, 1, tzinfo=UTC)
    with SQLiteWorkQueue(database) as queue:
        item = queue.publish(envelope("expired", occurred_at=start))
        claimed = queue.claim("worker-1", now=start, lease_seconds=10)
        assert claimed is not None

    with SQLiteWorkQueue(database) as queue:
        assert queue.recover_expired(now=start + timedelta(seconds=11)) == 1
        recovered = queue.get(item.id)
        assert recovered.status is QueueStatus.READY
        reclaimed = queue.claim("worker-2", now=start + timedelta(seconds=11))
        assert reclaimed is not None
        assert reclaimed.attempt_count == 2


def test_retries_dead_letter_at_attempt_limit(tmp_path) -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    with SQLiteWorkQueue(tmp_path / "queue.db") as queue:
        item = queue.publish(envelope("dead", occurred_at=start), max_attempts=2)
        first = queue.claim("worker", now=start)
        assert first is not None
        retried = queue.retry(first.id, "worker", "temporary", now=start)
        assert retried.status is QueueStatus.READY

        second = queue.claim("worker", now=start)
        assert second is not None
        dead = queue.retry(second.id, "worker", "permanent", now=start)
        assert dead.status is QueueStatus.DEAD_LETTER
        assert dead.attempt_count == 2
        assert queue.claim("worker", now=start) is None
        assert queue.events_for(item.id)[-1].kind is QueueEventKind.DEAD_LETTERED


def test_order_is_deterministic_within_work_item(tmp_path) -> None:
    work_item_id = uuid4()
    now = datetime(2026, 1, 1, tzinfo=UTC)
    with SQLiteWorkQueue(tmp_path / "queue.db") as queue:
        first = queue.publish(envelope("first", work_item_id=work_item_id, occurred_at=now))
        second = queue.publish(envelope("second", work_item_id=work_item_id, occurred_at=now))
        assert (first.sequence, second.sequence) == (1, 2)
        claimed = queue.claim("worker", now=now)
        assert claimed is not None
        assert claimed.id == first.id
