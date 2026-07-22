"""Governance controls layered over the runnable orchestrator."""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from types import TracebackType
from typing import Mapping
from uuid import UUID, uuid4

from .contracts import RiskLevel
from .orchestrator import Goal, GovernedOrchestrator, RunResult, RunStatus


def _now() -> str:
    return datetime.now(UTC).isoformat()


class GovernanceAction(StrEnum):
    ALLOW = "allow"
    DENY = "deny"
    ESCALATE = "escalate"


@dataclass(frozen=True, slots=True)
class GovernanceContext:
    estimated_cost: float = 0.0
    legal_commitment: bool = False
    irreversible: bool = False
    mission_change: bool = False
    constitutional_change: bool = False
    metadata: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.estimated_cost < 0:
            raise ValueError("estimated_cost must be non-negative")


@dataclass(frozen=True, slots=True)
class StopCondition:
    name: str
    field_name: str
    expected_value: bool = True
    reason: str = "Founder decision required."
    action: GovernanceAction = GovernanceAction.ESCALATE
    id: UUID = field(default_factory=uuid4)


@dataclass(frozen=True, slots=True)
class FounderEscalationPacket:
    id: UUID
    goal_id: UUID
    category: str
    issue: str
    background: str
    evidence: tuple[str, ...]
    options: tuple[str, ...]
    recommendation: str
    risk_level: RiskLevel
    estimated_impact: str
    required_decision: str
    created_at: str


_SCHEMA = """
CREATE TABLE IF NOT EXISTS governance_budgets(
    id TEXT PRIMARY KEY, name TEXT NOT NULL, currency TEXT NOT NULL,
    limit_amount REAL NOT NULL, spent REAL NOT NULL, reserved REAL NOT NULL,
    hard_stop INTEGER NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS governance_reservations(
    id TEXT PRIMARY KEY, budget_id TEXT NOT NULL, goal_id TEXT NOT NULL UNIQUE,
    amount REAL NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS governance_stop_conditions(
    id TEXT PRIMARY KEY, name TEXT NOT NULL, field_name TEXT NOT NULL,
    expected_value INTEGER NOT NULL, reason TEXT NOT NULL, action TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS founder_escalation_packets(
    id TEXT PRIMARY KEY, goal_id TEXT NOT NULL, category TEXT NOT NULL,
    payload TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL
);
"""


class GovernanceStore:
    def __init__(self, database: str | Path) -> None:
        self._db = sqlite3.connect(str(database), timeout=30.0)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript(_SCHEMA)
        self._db.commit()

    def close(self) -> None:
        self._db.close()

    def __enter__(self) -> GovernanceStore:
        return self

    def __exit__(self, exc_type: type[BaseException] | None,
                 exc: BaseException | None, traceback: TracebackType | None) -> None:
        self.close()

    def create_budget(self, name: str, currency: str, limit: float,
                      *, hard_stop: bool = True) -> UUID:
        if limit < 0:
            raise ValueError("limit must be non-negative")
        budget_id = uuid4()
        with self._db:
            self._db.execute(
                "INSERT INTO governance_budgets VALUES(?,?,?,?,?,?,?,?)",
                (str(budget_id), name.strip(), currency.upper(), limit, 0.0, 0.0,
                 int(hard_stop), _now()),
            )
        return budget_id

    def add_stop_condition(self, condition: StopCondition) -> None:
        if condition.field_name not in GovernanceContext.__dataclass_fields__:
            raise ValueError(f"Unknown governance field: {condition.field_name}")
        with self._db:
            self._db.execute(
                "INSERT OR REPLACE INTO governance_stop_conditions VALUES(?,?,?,?,?,?)",
                (str(condition.id), condition.name, condition.field_name,
                 int(condition.expected_value), condition.reason, condition.action.value),
            )

    def evaluate(self, goal: Goal, context: GovernanceContext,
                 budget_id: UUID | None = None) -> FounderEscalationPacket | None:
        rows = self._db.execute(
            "SELECT * FROM governance_stop_conditions ORDER BY name,id"
        ).fetchall()
        for row in rows:
            actual = bool(getattr(context, str(row["field_name"])))
            if actual == bool(row["expected_value"]):
                action = GovernanceAction(str(row["action"]))
                if action is GovernanceAction.ALLOW:
                    continue
                return self._packet(
                    goal, str(row["name"]), str(row["reason"]),
                    context, "Do not proceed without an explicit founder decision.",
                )
        if budget_id is not None and context.estimated_cost:
            row = self._db.execute(
                "SELECT * FROM governance_budgets WHERE id=?", (str(budget_id),)
            ).fetchone()
            if row is None:
                return self._packet(goal, "financial", "Budget does not exist.", context,
                                    "Assign a valid budget or reject the work.")
            available = float(row["limit_amount"]) - float(row["spent"]) - float(row["reserved"])
            if context.estimated_cost > available:
                return self._packet(
                    goal, "financial",
                    f"Estimated cost exceeds available budget ({available:.2f} {row['currency']}).",
                    context, "Increase the budget, reduce scope, or reject the work.",
                )
            reservation_id = uuid4()
            with self._db:
                self._db.execute(
                    "UPDATE governance_budgets SET reserved=reserved+? WHERE id=?",
                    (context.estimated_cost, str(budget_id)),
                )
                self._db.execute(
                    "INSERT OR IGNORE INTO governance_reservations VALUES(?,?,?,?,?,?)",
                    (str(reservation_id), str(budget_id), str(goal.id),
                     context.estimated_cost, "reserved", _now()),
                )
        return None

    def settle(self, goal_id: UUID, actual_cost: float) -> None:
        if actual_cost < 0:
            raise ValueError("actual_cost must be non-negative")
        row = self._db.execute(
            "SELECT * FROM governance_reservations WHERE goal_id=? AND status='reserved'",
            (str(goal_id),),
        ).fetchone()
        if row is None:
            return
        with self._db:
            self._db.execute(
                "UPDATE governance_budgets SET reserved=reserved-?,spent=spent+? WHERE id=?",
                (float(row["amount"]), actual_cost, str(row["budget_id"])),
            )
            self._db.execute(
                "UPDATE governance_reservations SET status='settled' WHERE id=?",
                (str(row["id"]),),
            )

    def persist_packet(self, packet: FounderEscalationPacket) -> None:
        payload = json.dumps({
            "issue": packet.issue, "background": packet.background,
            "evidence": packet.evidence, "options": packet.options,
            "recommendation": packet.recommendation,
            "risk_level": packet.risk_level.value,
            "estimated_impact": packet.estimated_impact,
            "required_decision": packet.required_decision,
        }, sort_keys=True)
        with self._db:
            self._db.execute(
                "INSERT INTO founder_escalation_packets VALUES(?,?,?,?,?,?)",
                (str(packet.id), str(packet.goal_id), packet.category, payload,
                 "open", packet.created_at),
            )

    def _packet(self, goal: Goal, category: str, issue: str,
                context: GovernanceContext, recommendation: str) -> FounderEscalationPacket:
        packet = FounderEscalationPacket(
            id=uuid4(), goal_id=goal.id, category=category, issue=issue,
            background=goal.description,
            evidence=(f"estimated_cost={context.estimated_cost}",
                      f"risk_level={goal.risk_level.value}"),
            options=("Approve", "Reject", "Approve with constraints", "Delegate authority"),
            recommendation=recommendation, risk_level=goal.risk_level,
            estimated_impact=f"Estimated financial impact: {context.estimated_cost:.2f}",
            required_decision="Select an option and record constraints or rationale.",
            created_at=_now(),
        )
        self.persist_packet(packet)
        return packet


class GovernedInstitution:
    """Preflight governance plus the executable orchestrator."""

    def __init__(self, orchestrator: GovernedOrchestrator, governance: GovernanceStore) -> None:
        self._orchestrator = orchestrator
        self._governance = governance

    def submit_and_run(self, goal: Goal, *, context: GovernanceContext | None = None,
                       budget_id: UUID | None = None,
                       actual_cost: float | None = None) -> tuple[RunResult, FounderEscalationPacket | None]:
        effective = context or GovernanceContext()
        packet = self._governance.evaluate(goal, effective, budget_id)
        if packet is not None:
            return RunResult(goal.id, RunStatus.ESCALATED, (), packet.id), packet
        self._orchestrator.submit(goal)
        result = self._orchestrator.run(goal.id)
        if result.status is RunStatus.CANONICAL:
            self._governance.settle(goal.id,
                                    effective.estimated_cost if actual_cost is None else actual_cost)
        return result, None
