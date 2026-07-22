"""Persistent storage contracts and SQLite implementation for governed work."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from types import TracebackType
from typing import Protocol, cast
from uuid import UUID

from .state_machine import Actor, AuditEvent, State, StateMachine, TransitionRequest


class DuplicateRequestError(RuntimeError):
    """Raised when a transition request ID has already been committed."""


class ConcurrencyError(RuntimeError):
    """Raised when a caller attempts to update a stale work-item version."""


class WorkItemNotFound(KeyError):
    """Raised when a requested work item does not exist."""


@dataclass(frozen=True, slots=True)
class WorkItem:
    id: UUID
    state: State
    version: int
    created_at: datetime
    updated_at: datetime


class AuditLedger(Protocol):
    def append(self, event: AuditEvent) -> None: ...

    def events_for(self, work_item_id: UUID) -> tuple[AuditEvent, ...]: ...

    def contains_request(self, request_id: UUID) -> bool: ...


class WorkItemRepository(Protocol):
    def create(
        self,
        work_item_id: UUID,
        initial_state: State,
        occurred_at: datetime,
    ) -> WorkItem: ...

    def get(self, work_item_id: UUID) -> WorkItem: ...

    def apply_transition(
        self,
        request: TransitionRequest,
        expected_version: int,
    ) -> WorkItem: ...


_SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS work_items (
    id TEXT PRIMARY KEY,
    state TEXT NOT NULL,
    version INTEGER NOT NULL CHECK (version >= 0),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_events (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL UNIQUE,
    request_id TEXT NOT NULL UNIQUE,
    work_item_id TEXT NOT NULL,
    from_state TEXT NOT NULL,
    to_state TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    actor_role TEXT NOT NULL,
    reason TEXT NOT NULL,
    evidence_json TEXT NOT NULL,
    constitutional_rule_id TEXT,
    occurred_at TEXT NOT NULL,
    FOREIGN KEY (work_item_id) REFERENCES work_items(id)
);

CREATE INDEX IF NOT EXISTS idx_audit_events_work_item_sequence
ON audit_events(work_item_id, sequence);
"""


class SQLiteStore(AuditLedger, WorkItemRepository):
    """Transactional SQLite persistence for work items and immutable audit events."""

    def __init__(self, database: str | Path, state_machine: StateMachine | None = None) -> None:
        self._database = str(database)
        self._state_machine = state_machine or StateMachine()
        self._connection = sqlite3.connect(self._database)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._connection.executescript(_SCHEMA)
        self._connection.commit()

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> SQLiteStore:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def create(
        self,
        work_item_id: UUID,
        initial_state: State,
        occurred_at: datetime,
    ) -> WorkItem:
        timestamp = occurred_at.isoformat()
        try:
            with self._connection:
                self._connection.execute(
                    """
                    INSERT INTO work_items(
                        id, state, version, created_at, updated_at
                    ) VALUES (?, ?, 0, ?, ?)
                    """,
                    (str(work_item_id), initial_state.value, timestamp, timestamp),
                )
        except sqlite3.IntegrityError as exc:
            raise ValueError(f"Work item {work_item_id} already exists") from exc
        return self.get(work_item_id)

    def get(self, work_item_id: UUID) -> WorkItem:
        row = self._connection.execute(
            """
            SELECT id, state, version, created_at, updated_at
            FROM work_items
            WHERE id = ?
            """,
            (str(work_item_id),),
        ).fetchone()
        if row is None:
            raise WorkItemNotFound(str(work_item_id))
        return _row_to_work_item(row)

    def contains_request(self, request_id: UUID) -> bool:
        row = self._connection.execute(
            "SELECT 1 FROM audit_events WHERE request_id = ?",
            (str(request_id),),
        ).fetchone()
        return row is not None

    def append(self, event: AuditEvent) -> None:
        try:
            with self._connection:
                self._insert_event(event)
        except sqlite3.IntegrityError as exc:
            if self.contains_request(event.request_id):
                raise DuplicateRequestError(str(event.request_id)) from exc
            raise

    def events_for(self, work_item_id: UUID) -> tuple[AuditEvent, ...]:
        rows = self._connection.execute(
            """
            SELECT event_id, request_id, work_item_id, from_state, to_state,
                   actor_id, actor_role, reason, evidence_json,
                   constitutional_rule_id, occurred_at
            FROM audit_events
            WHERE work_item_id = ?
            ORDER BY sequence ASC
            """,
            (str(work_item_id),),
        ).fetchall()
        return tuple(_row_to_event(row) for row in rows)

    def apply_transition(
        self,
        request: TransitionRequest,
        expected_version: int,
    ) -> WorkItem:
        event = self._state_machine.transition(request)
        try:
            with self._connection:
                current = self._connection.execute(
                    "SELECT state, version FROM work_items WHERE id = ?",
                    (str(request.work_item_id),),
                ).fetchone()
                if current is None:
                    raise WorkItemNotFound(str(request.work_item_id))
                if int(current["version"]) != expected_version:
                    raise ConcurrencyError(
                        f"Expected version {expected_version}, found {current['version']}"
                    )
                persisted_state = State(cast(str, current["state"]))
                if persisted_state is not request.from_state:
                    raise ConcurrencyError(
                        "Persisted state "
                        f"{persisted_state.value} does not match request state "
                        f"{request.from_state.value}"
                    )
                if self.contains_request(request.request_id):
                    raise DuplicateRequestError(str(request.request_id))

                cursor = self._connection.execute(
                    """
                    UPDATE work_items
                    SET state = ?, version = version + 1, updated_at = ?
                    WHERE id = ? AND version = ?
                    """,
                    (
                        request.to_state.value,
                        request.occurred_at.isoformat(),
                        str(request.work_item_id),
                        expected_version,
                    ),
                )
                if cursor.rowcount != 1:
                    raise ConcurrencyError("Concurrent update detected")
                self._insert_event(event)
        except sqlite3.IntegrityError as exc:
            if self.contains_request(request.request_id):
                raise DuplicateRequestError(str(request.request_id)) from exc
            raise
        return self.get(request.work_item_id)

    def rebuild_state(
        self,
        work_item_id: UUID,
        initial_state: State = State.PROPOSED,
    ) -> State:
        return self._state_machine.replay(initial_state, self.events_for(work_item_id))

    def verify_snapshot(
        self,
        work_item_id: UUID,
        initial_state: State = State.PROPOSED,
    ) -> bool:
        return self.rebuild_state(work_item_id, initial_state) is self.get(work_item_id).state

    def _insert_event(self, event: AuditEvent) -> None:
        self._connection.execute(
            """
            INSERT INTO audit_events(
                event_id, request_id, work_item_id, from_state, to_state,
                actor_id, actor_role, reason, evidence_json,
                constitutional_rule_id, occurred_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(event.event_id),
                str(event.request_id),
                str(event.work_item_id),
                event.from_state.value,
                event.to_state.value,
                event.actor.id,
                event.actor.role,
                event.reason,
                json.dumps(event.evidence),
                event.constitutional_rule_id,
                event.occurred_at.isoformat(),
            ),
        )


def _row_to_work_item(row: sqlite3.Row) -> WorkItem:
    return WorkItem(
        id=UUID(cast(str, row["id"])),
        state=State(cast(str, row["state"])),
        version=cast(int, row["version"]),
        created_at=datetime.fromisoformat(cast(str, row["created_at"])),
        updated_at=datetime.fromisoformat(cast(str, row["updated_at"])),
    )


def _row_to_event(row: sqlite3.Row) -> AuditEvent:
    raw_evidence = cast(list[str], json.loads(cast(str, row["evidence_json"])))
    return AuditEvent(
        event_id=UUID(cast(str, row["event_id"])),
        request_id=UUID(cast(str, row["request_id"])),
        work_item_id=UUID(cast(str, row["work_item_id"])),
        from_state=State(cast(str, row["from_state"])),
        to_state=State(cast(str, row["to_state"])),
        actor=Actor(
            id=cast(str, row["actor_id"]),
            role=cast(str, row["actor_role"]),
        ),
        reason=cast(str, row["reason"]),
        evidence=tuple(raw_evidence),
        constitutional_rule_id=cast(str | None, row["constitutional_rule_id"]),
        occurred_at=datetime.fromisoformat(cast(str, row["occurred_at"])),
    )
