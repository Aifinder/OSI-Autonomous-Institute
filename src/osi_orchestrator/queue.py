"""Durable SQLite event bus and governed work queue."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from types import TracebackType
from typing import Any, Mapping, cast
from uuid import UUID, uuid4


def _now() -> datetime:
    return datetime.now(UTC)


class QueueStatus(StrEnum):
    READY = "ready"
    LEASED = "leased"
    ACKNOWLEDGED = "acknowledged"
    DEAD_LETTER = "dead_letter"


class QueueEventKind(StrEnum):
    PUBLISHED = "published"
    CLAIMED = "claimed"
    ACKNOWLEDGED = "acknowledged"
    RETRIED = "retried"
    DEAD_LETTERED = "dead_lettered"
    LEASE_RECOVERED = "lease_recovered"


class DuplicatePublicationError(RuntimeError):
    """Raised when an idempotency key has already been published."""


class QueueItemNotFound(KeyError):
    """Raised when a queue item does not exist."""


class LeaseConflictError(RuntimeError):
    """Raised when a worker does not own the active lease."""


@dataclass(frozen=True, slots=True)
class EventEnvelope:
    event_type: str
    work_item_id: UUID
    payload: Mapping[str, Any]
    actor_id: str
    idempotency_key: str
    id: UUID = field(default_factory=uuid4)
    correlation_id: UUID = field(default_factory=uuid4)
    causation_id: UUID | None = None
    occurred_at: datetime = field(default_factory=_now)

    def __post_init__(self) -> None:
        for name, value in (
            ("event_type", self.event_type),
            ("actor_id", self.actor_id),
            ("idempotency_key", self.idempotency_key),
        ):
            if not value.strip():
                raise ValueError(f"{name} must not be empty")


@dataclass(frozen=True, slots=True)
class QueueItem:
    id: UUID
    envelope: EventEnvelope
    sequence: int
    status: QueueStatus
    attempt_count: int
    max_attempts: int
    available_at: datetime
    lease_owner: str | None
    lease_expires_at: datetime | None
    last_error: str | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class QueueEvent:
    sequence: int
    queue_item_id: UUID
    kind: QueueEventKind
    actor_id: str
    attempt_count: int
    occurred_at: datetime
    details: Mapping[str, Any]


_SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS queue_items (
    id TEXT PRIMARY KEY,
    event_id TEXT NOT NULL UNIQUE,
    event_type TEXT NOT NULL,
    work_item_id TEXT NOT NULL,
    work_sequence INTEGER NOT NULL,
    payload_json TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    idempotency_key TEXT NOT NULL UNIQUE,
    correlation_id TEXT NOT NULL,
    causation_id TEXT,
    occurred_at TEXT NOT NULL,
    status TEXT NOT NULL,
    attempt_count INTEGER NOT NULL DEFAULT 0 CHECK(attempt_count >= 0),
    max_attempts INTEGER NOT NULL CHECK(max_attempts > 0),
    available_at TEXT NOT NULL,
    lease_owner TEXT,
    lease_expires_at TEXT,
    last_error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(work_item_id, work_sequence)
);
CREATE INDEX IF NOT EXISTS idx_queue_claim
ON queue_items(status, available_at, work_sequence, created_at);
CREATE TABLE IF NOT EXISTS queue_events (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    queue_item_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    attempt_count INTEGER NOT NULL,
    occurred_at TEXT NOT NULL,
    details_json TEXT NOT NULL,
    FOREIGN KEY(queue_item_id) REFERENCES queue_items(id)
);
CREATE INDEX IF NOT EXISTS idx_queue_events_item
ON queue_events(queue_item_id, sequence);
"""


class SQLiteWorkQueue:
    """Transactional durable queue with exclusive leases and audit history."""

    def __init__(self, database: str | Path) -> None:
        self._connection = sqlite3.connect(str(database), timeout=30.0)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._connection.execute("PRAGMA busy_timeout = 30000")
        self._connection.executescript(_SCHEMA)
        self._connection.commit()

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> SQLiteWorkQueue:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def publish(
        self,
        envelope: EventEnvelope,
        *,
        max_attempts: int = 3,
        available_at: datetime | None = None,
    ) -> QueueItem:
        if max_attempts <= 0:
            raise ValueError("max_attempts must be positive")
        timestamp = envelope.occurred_at
        ready_at = available_at or timestamp
        item_id = uuid4()
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            row = self._connection.execute(
                "SELECT COALESCE(MAX(work_sequence), 0) + 1 AS next_sequence "
                "FROM queue_items WHERE work_item_id = ?",
                (str(envelope.work_item_id),),
            ).fetchone()
            sequence = cast(int, row["next_sequence"])
            self._connection.execute(
                """INSERT INTO queue_items(
                    id, event_id, event_type, work_item_id, work_sequence, payload_json,
                    actor_id, idempotency_key, correlation_id, causation_id, occurred_at,
                    status, attempt_count, max_attempts, available_at, lease_owner,
                    lease_expires_at, last_error, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, NULL, NULL, NULL, ?, ?)""",
                (
                    str(item_id), str(envelope.id), envelope.event_type,
                    str(envelope.work_item_id), sequence, json.dumps(dict(envelope.payload), sort_keys=True),
                    envelope.actor_id, envelope.idempotency_key, str(envelope.correlation_id),
                    str(envelope.causation_id) if envelope.causation_id else None,
                    envelope.occurred_at.isoformat(), QueueStatus.READY.value, max_attempts,
                    ready_at.isoformat(), timestamp.isoformat(), timestamp.isoformat(),
                ),
            )
            self._record(item_id, QueueEventKind.PUBLISHED, envelope.actor_id, 0, timestamp, {})
            self._connection.commit()
        except sqlite3.IntegrityError as exc:
            self._connection.rollback()
            if self._connection.execute(
                "SELECT 1 FROM queue_items WHERE idempotency_key = ?",
                (envelope.idempotency_key,),
            ).fetchone():
                raise DuplicatePublicationError(envelope.idempotency_key) from exc
            raise
        except BaseException:
            self._connection.rollback()
            raise
        return self.get(item_id)

    def claim(self, worker_id: str, *, now: datetime | None = None, lease_seconds: int = 60) -> QueueItem | None:
        if not worker_id.strip():
            raise ValueError("worker_id must not be empty")
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        claimed_at = now or _now()
        expires_at = claimed_at + timedelta(seconds=lease_seconds)
        self._connection.execute("BEGIN IMMEDIATE")
        try:
            row = self._connection.execute(
                """SELECT id FROM queue_items
                WHERE status = ? AND available_at <= ?
                ORDER BY work_sequence ASC, created_at ASC, id ASC LIMIT 1""",
                (QueueStatus.READY.value, claimed_at.isoformat()),
            ).fetchone()
            if row is None:
                self._connection.commit()
                return None
            item_id = UUID(cast(str, row["id"]))
            cursor = self._connection.execute(
                """UPDATE queue_items SET status = ?, attempt_count = attempt_count + 1,
                lease_owner = ?, lease_expires_at = ?, updated_at = ?
                WHERE id = ? AND status = ?""",
                (
                    QueueStatus.LEASED.value, worker_id, expires_at.isoformat(),
                    claimed_at.isoformat(), str(item_id), QueueStatus.READY.value,
                ),
            )
            if cursor.rowcount != 1:
                self._connection.rollback()
                return None
            attempt = cast(int, self._connection.execute(
                "SELECT attempt_count FROM queue_items WHERE id = ?", (str(item_id),)
            ).fetchone()["attempt_count"])
            self._record(item_id, QueueEventKind.CLAIMED, worker_id, attempt, claimed_at, {"lease_expires_at": expires_at.isoformat()})
            self._connection.commit()
            return self.get(item_id)
        except BaseException:
            self._connection.rollback()
            raise

    def acknowledge(self, item_id: UUID, worker_id: str, *, now: datetime | None = None) -> QueueItem:
        return self._finish(item_id, worker_id, QueueStatus.ACKNOWLEDGED, QueueEventKind.ACKNOWLEDGED, now or _now(), None)

    def retry(
        self,
        item_id: UUID,
        worker_id: str,
        error: str,
        *,
        now: datetime | None = None,
        delay_seconds: int = 0,
    ) -> QueueItem:
        if delay_seconds < 0:
            raise ValueError("delay_seconds must be non-negative")
        timestamp = now or _now()
        item = self.get(item_id)
        self._require_lease(item, worker_id, timestamp)
        terminal = item.attempt_count >= item.max_attempts
        status = QueueStatus.DEAD_LETTER if terminal else QueueStatus.READY
        kind = QueueEventKind.DEAD_LETTERED if terminal else QueueEventKind.RETRIED
        available_at = timestamp + timedelta(seconds=delay_seconds)
        with self._connection:
            self._connection.execute(
                """UPDATE queue_items SET status = ?, available_at = ?, lease_owner = NULL,
                lease_expires_at = NULL, last_error = ?, updated_at = ? WHERE id = ?""",
                (status.value, available_at.isoformat(), error, timestamp.isoformat(), str(item_id)),
            )
            self._record(item_id, kind, worker_id, item.attempt_count, timestamp, {"error": error})
        return self.get(item_id)

    def recover_expired(self, *, now: datetime | None = None, actor_id: str = "kernel") -> int:
        timestamp = now or _now()
        self._connection.execute("BEGIN IMMEDIATE")
        try:
            rows = self._connection.execute(
                """SELECT id, attempt_count, max_attempts FROM queue_items
                WHERE status = ? AND lease_expires_at <= ? ORDER BY id""",
                (QueueStatus.LEASED.value, timestamp.isoformat()),
            ).fetchall()
            for row in rows:
                item_id = UUID(cast(str, row["id"]))
                attempt = cast(int, row["attempt_count"])
                terminal = attempt >= cast(int, row["max_attempts"])
                status = QueueStatus.DEAD_LETTER if terminal else QueueStatus.READY
                kind = QueueEventKind.DEAD_LETTERED if terminal else QueueEventKind.LEASE_RECOVERED
                self._connection.execute(
                    """UPDATE queue_items SET status = ?, lease_owner = NULL,
                    lease_expires_at = NULL, updated_at = ? WHERE id = ?""",
                    (status.value, timestamp.isoformat(), str(item_id)),
                )
                self._record(item_id, kind, actor_id, attempt, timestamp, {"reason": "lease_expired"})
            self._connection.commit()
            return len(rows)
        except BaseException:
            self._connection.rollback()
            raise

    def get(self, item_id: UUID) -> QueueItem:
        row = self._connection.execute("SELECT * FROM queue_items WHERE id = ?", (str(item_id),)).fetchone()
        if row is None:
            raise QueueItemNotFound(str(item_id))
        return _row_to_item(row)

    def events_for(self, item_id: UUID) -> tuple[QueueEvent, ...]:
        rows = self._connection.execute(
            "SELECT * FROM queue_events WHERE queue_item_id = ? ORDER BY sequence", (str(item_id),)
        ).fetchall()
        return tuple(_row_to_event(row) for row in rows)

    def _finish(
        self,
        item_id: UUID,
        worker_id: str,
        status: QueueStatus,
        kind: QueueEventKind,
        timestamp: datetime,
        error: str | None,
    ) -> QueueItem:
        item = self.get(item_id)
        self._require_lease(item, worker_id, timestamp)
        with self._connection:
            self._connection.execute(
                """UPDATE queue_items SET status = ?, lease_owner = NULL,
                lease_expires_at = NULL, last_error = ?, updated_at = ? WHERE id = ?""",
                (status.value, error, timestamp.isoformat(), str(item_id)),
            )
            self._record(item_id, kind, worker_id, item.attempt_count, timestamp, {})
        return self.get(item_id)

    @staticmethod
    def _require_lease(item: QueueItem, worker_id: str, timestamp: datetime) -> None:
        if item.status is not QueueStatus.LEASED or item.lease_owner != worker_id:
            raise LeaseConflictError(f"Worker {worker_id!r} does not own queue item {item.id}")
        if item.lease_expires_at is None or item.lease_expires_at <= timestamp:
            raise LeaseConflictError(f"Lease for queue item {item.id} has expired")

    def _record(
        self,
        item_id: UUID,
        kind: QueueEventKind,
        actor_id: str,
        attempt_count: int,
        occurred_at: datetime,
        details: Mapping[str, Any],
    ) -> None:
        self._connection.execute(
            """INSERT INTO queue_events(queue_item_id, kind, actor_id, attempt_count, occurred_at, details_json)
            VALUES (?, ?, ?, ?, ?, ?)""",
            (str(item_id), kind.value, actor_id, attempt_count, occurred_at.isoformat(), json.dumps(dict(details), sort_keys=True)),
        )


def _row_to_item(row: sqlite3.Row) -> QueueItem:
    envelope = EventEnvelope(
        id=UUID(cast(str, row["event_id"])),
        event_type=cast(str, row["event_type"]),
        work_item_id=UUID(cast(str, row["work_item_id"])),
        payload=cast(dict[str, Any], json.loads(cast(str, row["payload_json"]))),
        actor_id=cast(str, row["actor_id"]),
        idempotency_key=cast(str, row["idempotency_key"]),
        correlation_id=UUID(cast(str, row["correlation_id"])),
        causation_id=UUID(cast(str, row["causation_id"])) if row["causation_id"] else None,
        occurred_at=datetime.fromisoformat(cast(str, row["occurred_at"])),
    )
    return QueueItem(
        id=UUID(cast(str, row["id"])), envelope=envelope,
        sequence=cast(int, row["work_sequence"]), status=QueueStatus(cast(str, row["status"])),
        attempt_count=cast(int, row["attempt_count"]), max_attempts=cast(int, row["max_attempts"]),
        available_at=datetime.fromisoformat(cast(str, row["available_at"])),
        lease_owner=cast(str | None, row["lease_owner"]),
        lease_expires_at=datetime.fromisoformat(cast(str, row["lease_expires_at"])) if row["lease_expires_at"] else None,
        last_error=cast(str | None, row["last_error"]),
        created_at=datetime.fromisoformat(cast(str, row["created_at"])),
        updated_at=datetime.fromisoformat(cast(str, row["updated_at"])),
    )


def _row_to_event(row: sqlite3.Row) -> QueueEvent:
    return QueueEvent(
        sequence=cast(int, row["sequence"]), queue_item_id=UUID(cast(str, row["queue_item_id"])),
        kind=QueueEventKind(cast(str, row["kind"])), actor_id=cast(str, row["actor_id"]),
        attempt_count=cast(int, row["attempt_count"]),
        occurred_at=datetime.fromisoformat(cast(str, row["occurred_at"])),
        details=cast(dict[str, Any], json.loads(cast(str, row["details_json"]))),
    )
