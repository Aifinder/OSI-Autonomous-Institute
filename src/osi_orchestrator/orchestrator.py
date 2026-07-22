"""Runnable governed orchestrator vertical slice for Release 0.1."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from types import TracebackType
from typing import Callable, Mapping, Protocol, cast
from uuid import UUID, uuid4

from .contracts import Agent, AgentRole, RiskLevel, WorkItemSpec
from .routing import NoEligibleAgent, RoutingRequest, SQLiteAgentRegistry


def _now() -> str:
    return datetime.now(UTC).isoformat()


class RunStatus(StrEnum):
    PLANNED = "planned"
    RUNNING = "running"
    REVIEWING = "reviewing"
    REVISION_REQUIRED = "revision_required"
    CANONICAL = "canonical"
    ESCALATED = "escalated"
    FAILED = "failed"


class ReviewVerdict(StrEnum):
    APPROVE = "approve"
    REVISE = "revise"
    REJECT = "reject"
    ESCALATE = "escalate"


@dataclass(frozen=True, slots=True)
class Goal:
    title: str
    description: str
    success_criteria: tuple[str, ...] = ()
    risk_level: RiskLevel = RiskLevel.MODERATE
    required_capabilities: tuple[str, ...] = ("general",)
    review_gates: tuple[str, ...] = ("quality",)
    id: UUID = field(default_factory=uuid4)


@dataclass(frozen=True, slots=True)
class PlannedTask:
    id: UUID
    goal_id: UUID
    title: str
    description: str
    required_capabilities: tuple[str, ...]
    review_gates: tuple[str, ...]
    dependency_ids: tuple[UUID, ...] = ()
    priority: int = 100


@dataclass(frozen=True, slots=True)
class ExecutionOutput:
    name: str
    media_type: str
    content: str
    metadata: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ReviewOutput:
    verdict: ReviewVerdict
    rationale: str
    revised_instructions: str | None = None


@dataclass(frozen=True, slots=True)
class RunResult:
    goal_id: UUID
    status: RunStatus
    canonical_artifact_ids: tuple[UUID, ...]
    escalation_id: UUID | None = None


class ProductionExecutor(Protocol):
    def __call__(self, task: PlannedTask, agent: Agent, revision: str | None) -> ExecutionOutput: ...


class ReviewExecutor(Protocol):
    def __call__(self, task: PlannedTask, artifact: ExecutionOutput, reviewer: Agent, gate: str) -> ReviewOutput: ...


Planner = Callable[[Goal], tuple[PlannedTask, ...]]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS goals(id TEXT PRIMARY KEY,payload TEXT NOT NULL,status TEXT NOT NULL,created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS tasks(id TEXT PRIMARY KEY,goal_id TEXT NOT NULL,payload TEXT NOT NULL,status TEXT NOT NULL,producing_agent_id TEXT,attempts INTEGER NOT NULL DEFAULT 0,created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS artifacts(id TEXT PRIMARY KEY,goal_id TEXT NOT NULL,task_id TEXT NOT NULL,version INTEGER NOT NULL,status TEXT NOT NULL,name TEXT NOT NULL,media_type TEXT NOT NULL,content TEXT NOT NULL,content_hash TEXT NOT NULL,created_by_agent_id TEXT NOT NULL,metadata TEXT NOT NULL,created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS reviews(id TEXT PRIMARY KEY,task_id TEXT NOT NULL,artifact_id TEXT NOT NULL,reviewer_agent_id TEXT NOT NULL,gate_name TEXT NOT NULL,verdict TEXT NOT NULL,rationale TEXT NOT NULL,created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS approvals(id TEXT PRIMARY KEY,task_id TEXT NOT NULL,artifact_id TEXT NOT NULL,rationale TEXT NOT NULL,created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS escalations(id TEXT PRIMARY KEY,goal_id TEXT NOT NULL,task_id TEXT,reason TEXT NOT NULL,recommendation TEXT NOT NULL,status TEXT NOT NULL,created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS events(sequence INTEGER PRIMARY KEY AUTOINCREMENT,id TEXT UNIQUE NOT NULL,aggregate_type TEXT NOT NULL,aggregate_id TEXT NOT NULL,event_type TEXT NOT NULL,payload TEXT NOT NULL,created_at TEXT NOT NULL);
"""


class GovernedOrchestrator:
    def __init__(self, database: str | Path, registry: SQLiteAgentRegistry,
                 production_executor: ProductionExecutor, review_executor: ReviewExecutor,
                 *, planner: Planner | None = None, max_revisions: int = 2) -> None:
        if max_revisions < 0:
            raise ValueError("max_revisions must be non-negative")
        self._db = sqlite3.connect(str(database), timeout=30.0)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript(_SCHEMA)
        self._db.commit()
        self._registry = registry
        self._produce = production_executor
        self._review = review_executor
        self._planner = planner or default_planner
        self._max_revisions = max_revisions

    def close(self) -> None:
        self._db.close()

    def __enter__(self) -> GovernedOrchestrator:
        return self

    def __exit__(self, exc_type: type[BaseException] | None,
                 exc: BaseException | None, traceback: TracebackType | None) -> None:
        self.close()

    def submit(self, goal: Goal) -> UUID:
        tasks = self._planner(goal)
        self._validate_plan(tasks)
        payload = _json({"title": goal.title, "description": goal.description,
                         "success_criteria": goal.success_criteria,
                         "risk_level": goal.risk_level.value,
                         "required_capabilities": goal.required_capabilities,
                         "review_gates": goal.review_gates})
        with self._db:
            self._db.execute("INSERT INTO goals VALUES(?,?,?,?)",
                             (str(goal.id), payload, RunStatus.PLANNED.value, _now()))
            self._event("goal", goal.id, "goal_submitted", {"title": goal.title})
            for task in tasks:
                self._db.execute(
                    "INSERT INTO tasks(id,goal_id,payload,status,created_at) VALUES(?,?,?,?,?)",
                    (str(task.id), str(goal.id), _task_json(task),
                     RunStatus.PLANNED.value, _now()))
                self._event("task", task.id, "task_planned", {"goal_id": str(goal.id)})
        return goal.id

    def run(self, goal_id: UUID) -> RunResult:
        goal = self._load_goal(goal_id)
        pending = list(self._load_tasks(goal_id))
        completed = set(self._canonical_task_ids(goal_id))
        artifacts: list[UUID] = []
        self._set_goal(goal_id, RunStatus.RUNNING)
        while pending:
            ready = sorted((t for t in pending if set(t.dependency_ids) <= completed),
                           key=lambda t: (t.priority, str(t.id)))
            if not ready:
                return self._escalate(goal, None, "Dependency graph is blocked or cyclic.")
            for task in ready:
                result = self._run_task(goal, task)
                if result.status is not RunStatus.CANONICAL:
                    return result
                artifacts.extend(result.canonical_artifact_ids)
                completed.add(task.id)
                pending.remove(task)
        self._set_goal(goal_id, RunStatus.CANONICAL)
        self._record_event("goal", goal_id, "goal_completed",
                           {"artifacts": [str(item) for item in artifacts]})
        return RunResult(goal_id, RunStatus.CANONICAL, tuple(artifacts))

    def status(self, goal_id: UUID) -> RunStatus:
        row = self._db.execute("SELECT status FROM goals WHERE id=?", (str(goal_id),)).fetchone()
        if row is None:
            raise KeyError(str(goal_id))
        return RunStatus(cast(str, row["status"]))

    def events(self, aggregate_id: UUID) -> tuple[dict[str, object], ...]:
        rows = self._db.execute(
            "SELECT sequence,event_type,payload,created_at FROM events WHERE aggregate_id=? ORDER BY sequence",
            (str(aggregate_id),)).fetchall()
        return tuple({"sequence": int(r["sequence"]), "event_type": cast(str, r["event_type"]),
                      "payload": json.loads(cast(str, r["payload"])),
                      "created_at": cast(str, r["created_at"])} for r in rows)

    def _run_task(self, goal: Goal, task: PlannedTask) -> RunResult:
        work = WorkItemSpec(objective_id=goal.id, id=task.id, title=task.title,
                            description=task.description,
                            required_capabilities=task.required_capabilities,
                            required_review_gates=task.review_gates,
                            dependency_ids=task.dependency_ids)
        try:
            route = self._registry.route(RoutingRequest(
                work_item=work, role=AgentRole.PRODUCTION, risk_level=goal.risk_level))
        except NoEligibleAgent:
            return self._escalate(goal, task, "No eligible production agent.")
        if route.selected_agent_id is None:
            return self._escalate(goal, task, "Production routing returned no agent.")
        producer = self._registry.get(route.selected_agent_id)
        revision: str | None = None
        for version in range(1, self._max_revisions + 2):
            self._set_task(task.id, RunStatus.RUNNING, producer.id, version)
            output = self._produce(task, producer, revision)
            artifact_id = self._artifact(goal.id, task.id, producer.id, output, version)
            self._set_task(task.id, RunStatus.REVIEWING, producer.id, version)
            approved = True
            for gate in task.review_gates:
                review_spec = WorkItemSpec(objective_id=goal.id, id=task.id,
                                           title=task.title, description=task.description,
                                           required_capabilities=(gate,),
                                           required_review_gates=(gate,))
                try:
                    decision = self._registry.route(RoutingRequest(
                        work_item=review_spec, role=AgentRole.REVIEW,
                        risk_level=goal.risk_level,
                        producing_agent_id=producer.id))
                except NoEligibleAgent:
                    return self._escalate(goal, task, f"No independent reviewer for {gate}.")
                if decision.selected_agent_id is None:
                    return self._escalate(goal, task, f"Review routing returned no agent for {gate}.")
                reviewer = self._registry.get(decision.selected_agent_id)
                review = self._review(task, output, reviewer, gate)
                self._save_review(task.id, artifact_id, reviewer.id, gate, review)
                if review.verdict is ReviewVerdict.ESCALATE:
                    return self._escalate(goal, task, review.rationale)
                if review.verdict is ReviewVerdict.REJECT:
                    self._set_task(task.id, RunStatus.FAILED, producer.id, version)
                    self._set_goal(goal.id, RunStatus.FAILED)
                    return RunResult(goal.id, RunStatus.FAILED, ())
                if review.verdict is ReviewVerdict.REVISE:
                    approved = False
                    revision = review.revised_instructions or review.rationale
                    self._set_task(task.id, RunStatus.REVISION_REQUIRED, producer.id, version)
                    break
            if approved:
                self._promote(task.id, artifact_id)
                self._set_task(task.id, RunStatus.CANONICAL, producer.id, version)
                return RunResult(goal.id, RunStatus.CANONICAL, (artifact_id,))
        return self._escalate(goal, task, "Maximum revision count exhausted.")

    def _artifact(self, goal_id: UUID, task_id: UUID, agent_id: UUID,
                  output: ExecutionOutput, version: int) -> UUID:
        artifact_id = uuid4()
        digest = hashlib.sha256(output.content.encode()).hexdigest()
        with self._db:
            self._db.execute("INSERT INTO artifacts VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                             (str(artifact_id), str(goal_id), str(task_id), version, "draft",
                              output.name, output.media_type, output.content, digest,
                              str(agent_id), _json(dict(output.metadata)), _now()))
            self._event("artifact", artifact_id, "artifact_created",
                        {"task_id": str(task_id), "version": version})
        return artifact_id

    def _save_review(self, task_id: UUID, artifact_id: UUID, reviewer_id: UUID,
                     gate: str, review: ReviewOutput) -> None:
        review_id = uuid4()
        with self._db:
            self._db.execute("INSERT INTO reviews VALUES(?,?,?,?,?,?,?,?)",
                             (str(review_id), str(task_id), str(artifact_id), str(reviewer_id),
                              gate, review.verdict.value, review.rationale, _now()))
            self._event("task", task_id, "review_recorded",
                        {"review_id": str(review_id), "gate": gate,
                         "verdict": review.verdict.value})

    def _promote(self, task_id: UUID, artifact_id: UUID) -> None:
        approval_id = uuid4()
        with self._db:
            self._db.execute("UPDATE artifacts SET status='canonical' WHERE id=?",
                             (str(artifact_id),))
            self._db.execute("INSERT INTO approvals VALUES(?,?,?,?,?)",
                             (str(approval_id), str(task_id), str(artifact_id),
                              "All independent review gates approved.", _now()))
            self._event("artifact", artifact_id, "artifact_promoted",
                        {"approval_id": str(approval_id)})

    def _escalate(self, goal: Goal, task: PlannedTask | None, reason: str) -> RunResult:
        escalation_id = uuid4()
        with self._db:
            self._db.execute("INSERT INTO escalations VALUES(?,?,?,?,?,'open',?)",
                             (str(escalation_id), str(goal.id), str(task.id) if task else None,
                              reason, "Provide constraints or delegated authority.", _now()))
            self._db.execute("UPDATE goals SET status=? WHERE id=?",
                             (RunStatus.ESCALATED.value, str(goal.id)))
            self._event("goal", goal.id, "goal_escalated",
                        {"escalation_id": str(escalation_id), "reason": reason})
        return RunResult(goal.id, RunStatus.ESCALATED, (), escalation_id)

    def _set_goal(self, goal_id: UUID, status: RunStatus) -> None:
        with self._db:
            self._db.execute("UPDATE goals SET status=? WHERE id=?",
                             (status.value, str(goal_id)))

    def _set_task(self, task_id: UUID, status: RunStatus,
                  agent_id: UUID, attempts: int) -> None:
        with self._db:
            self._db.execute(
                "UPDATE tasks SET status=?,producing_agent_id=?,attempts=? WHERE id=?",
                (status.value, str(agent_id), attempts, str(task_id)))
            self._event("task", task_id, "task_status_changed",
                        {"status": status.value, "attempt": attempts})

    def _record_event(self, kind: str, aggregate_id: UUID,
                      event_type: str, payload: object) -> None:
        with self._db:
            self._event(kind, aggregate_id, event_type, payload)

    def _event(self, kind: str, aggregate_id: UUID,
               event_type: str, payload: object) -> None:
        self._db.execute("INSERT INTO events VALUES(NULL,?,?,?,?,?,?)",
                         (str(uuid4()), kind, str(aggregate_id), event_type,
                          _json(payload), _now()))

    def _load_goal(self, goal_id: UUID) -> Goal:
        row = self._db.execute("SELECT payload FROM goals WHERE id=?",
                               (str(goal_id),)).fetchone()
        if row is None:
            raise KeyError(str(goal_id))
        raw = json.loads(cast(str, row["payload"]))
        return Goal(id=goal_id, title=raw["title"], description=raw["description"],
                    success_criteria=tuple(raw["success_criteria"]),
                    risk_level=RiskLevel(raw["risk_level"]),
                    required_capabilities=tuple(raw["required_capabilities"]),
                    review_gates=tuple(raw["review_gates"]))

    def _load_tasks(self, goal_id: UUID) -> tuple[PlannedTask, ...]:
        rows = self._db.execute(
            "SELECT payload FROM tasks WHERE goal_id=? AND status!=? ORDER BY id",
            (str(goal_id), RunStatus.CANONICAL.value)).fetchall()
        return tuple(_task_from_json(cast(str, row["payload"])) for row in rows)

    def _canonical_task_ids(self, goal_id: UUID) -> tuple[UUID, ...]:
        rows = self._db.execute("SELECT id FROM tasks WHERE goal_id=? AND status=?",
                                (str(goal_id), RunStatus.CANONICAL.value)).fetchall()
        return tuple(UUID(cast(str, row["id"])) for row in rows)

    @staticmethod
    def _validate_plan(tasks: tuple[PlannedTask, ...]) -> None:
        ids = {task.id for task in tasks}
        if len(ids) != len(tasks):
            raise ValueError("Plan contains duplicate task ids")
        if any(not set(task.dependency_ids) <= ids for task in tasks):
            raise ValueError("Plan references an unknown dependency")
        remaining = {task.id: set(task.dependency_ids) for task in tasks}
        resolved: set[UUID] = set()
        while remaining:
            ready = {item for item, deps in remaining.items() if deps <= resolved}
            if not ready:
                raise ValueError("Plan contains a dependency cycle")
            resolved |= ready
            remaining = {item: deps for item, deps in remaining.items() if item not in ready}


def default_planner(goal: Goal) -> tuple[PlannedTask, ...]:
    return (PlannedTask(uuid4(), goal.id, goal.title, goal.description,
                        goal.required_capabilities, goal.review_gates),)


def _task_json(task: PlannedTask) -> str:
    return _json({"id": str(task.id), "goal_id": str(task.goal_id),
                  "title": task.title, "description": task.description,
                  "required_capabilities": task.required_capabilities,
                  "review_gates": task.review_gates,
                  "dependency_ids": [str(item) for item in task.dependency_ids],
                  "priority": task.priority})


def _task_from_json(payload: str) -> PlannedTask:
    raw = json.loads(payload)
    return PlannedTask(UUID(raw["id"]), UUID(raw["goal_id"]), raw["title"],
                       raw["description"], tuple(raw["required_capabilities"]),
                       tuple(raw["review_gates"]),
                       tuple(UUID(item) for item in raw["dependency_ids"]),
                       int(raw["priority"]))


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))
