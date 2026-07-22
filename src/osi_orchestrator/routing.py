"""Persistent agent registry, deterministic capability routing, and authority boundaries."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import Iterable, cast
from uuid import UUID, uuid4

from .contracts import Agent, AgentRole, RiskLevel, WorkItemSpec


class AgentNotFound(KeyError):
    """Raised when an agent does not exist in the registry."""


class NoEligibleAgent(RuntimeError):
    """Raised when governance and capability filtering leaves no candidate."""


class AuthorityViolation(RuntimeError):
    """Raised when an assignment violates an authority boundary."""


@dataclass(frozen=True, slots=True)
class RoutingRequest:
    work_item: WorkItemSpec
    role: AgentRole
    required_tools: tuple[str, ...] = ()
    domain: str | None = None
    risk_level: RiskLevel = RiskLevel.MODERATE
    producing_agent_id: UUID | None = None
    excluded_agent_ids: tuple[UUID, ...] = ()
    max_candidates: int = 10

    def __post_init__(self) -> None:
        if self.max_candidates <= 0:
            raise ValueError("max_candidates must be positive")
        if self.role is AgentRole.REVIEW and self.producing_agent_id is None:
            raise ValueError("review routing requires producing_agent_id")


@dataclass(frozen=True, slots=True)
class CandidateEvaluation:
    agent_id: UUID
    eligible: bool
    reasons: tuple[str, ...]
    capability_score: float
    reliability_score: float
    cost_score: float
    total_score: float


@dataclass(frozen=True, slots=True)
class RoutingDecision:
    id: UUID
    work_item_id: UUID
    requested_role: AgentRole
    selected_agent_id: UUID | None
    outcome: str
    rationale: str
    candidates: tuple[CandidateEvaluation, ...]
    created_at: datetime
    replacement_for_agent_id: UUID | None = None


_SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS agents (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    role TEXT NOT NULL,
    capabilities_json TEXT NOT NULL,
    version TEXT NOT NULL,
    tool_permissions_json TEXT NOT NULL,
    domain_eligibility_json TEXT NOT NULL,
    authority_policy_ids_json TEXT NOT NULL,
    cost_per_execution REAL NOT NULL,
    reliability_score REAL NOT NULL,
    enabled INTEGER NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS routing_decisions (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    id TEXT NOT NULL UNIQUE,
    work_item_id TEXT NOT NULL,
    requested_role TEXT NOT NULL,
    selected_agent_id TEXT,
    outcome TEXT NOT NULL,
    rationale TEXT NOT NULL,
    candidates_json TEXT NOT NULL,
    replacement_for_agent_id TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_routing_work_item_sequence
ON routing_decisions(work_item_id, sequence);
"""


class SQLiteAgentRegistry:
    """SQLite-backed registry and auditable deterministic router."""

    def __init__(self, database: str | Path) -> None:
        self._connection = sqlite3.connect(str(database), timeout=30.0)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._connection.execute("PRAGMA journal_mode = WAL")
        self._connection.executescript(_SCHEMA)
        self._connection.commit()

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> SQLiteAgentRegistry:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def register(self, agent: Agent) -> Agent:
        with self._connection:
            self._connection.execute(
                """
                INSERT INTO agents(
                    id, name, role, capabilities_json, version, tool_permissions_json,
                    domain_eligibility_json, authority_policy_ids_json,
                    cost_per_execution, reliability_score, enabled, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    name=excluded.name, role=excluded.role,
                    capabilities_json=excluded.capabilities_json, version=excluded.version,
                    tool_permissions_json=excluded.tool_permissions_json,
                    domain_eligibility_json=excluded.domain_eligibility_json,
                    authority_policy_ids_json=excluded.authority_policy_ids_json,
                    cost_per_execution=excluded.cost_per_execution,
                    reliability_score=excluded.reliability_score,
                    enabled=excluded.enabled
                """,
                (
                    str(agent.id), agent.name, agent.role.value, json.dumps(agent.capabilities),
                    agent.version, json.dumps(agent.tool_permissions),
                    json.dumps(agent.domain_eligibility),
                    json.dumps([str(item) for item in agent.authority_policy_ids]),
                    agent.cost_per_execution, agent.reliability_score, int(agent.enabled),
                    agent.created_at.isoformat(),
                ),
            )
        return self.get(agent.id)

    def get(self, agent_id: UUID) -> Agent:
        row = self._connection.execute("SELECT * FROM agents WHERE id = ?", (str(agent_id),)).fetchone()
        if row is None:
            raise AgentNotFound(str(agent_id))
        return _row_to_agent(row)

    def list_agents(self, *, enabled_only: bool = False) -> tuple[Agent, ...]:
        query = "SELECT * FROM agents"
        if enabled_only:
            query += " WHERE enabled = 1"
        query += " ORDER BY id ASC"
        return tuple(_row_to_agent(row) for row in self._connection.execute(query).fetchall())

    def route(
        self,
        request: RoutingRequest,
        *,
        replacement_for_agent_id: UUID | None = None,
        now: datetime | None = None,
    ) -> RoutingDecision:
        evaluations = tuple(
            self._evaluate(agent, request)
            for agent in self.list_agents()
        )[: request.max_candidates]
        eligible = [item for item in evaluations if item.eligible]
        eligible.sort(key=lambda item: (-item.total_score, str(item.agent_id)))
        selected = eligible[0] if eligible else None
        created_at = now or datetime.now(UTC)
        if selected is None:
            decision = RoutingDecision(
                id=uuid4(), work_item_id=request.work_item.id,
                requested_role=request.role, selected_agent_id=None,
                outcome="escalated" if request.risk_level in {RiskLevel.HIGH, RiskLevel.CRITICAL} else "paused",
                rationale="No eligible agent satisfied capability, tool, domain, role, and authority constraints.",
                candidates=evaluations, replacement_for_agent_id=replacement_for_agent_id,
                created_at=created_at,
            )
            self._persist_decision(decision)
            raise NoEligibleAgent(decision.rationale)
        agent = self.get(selected.agent_id)
        self._enforce_authority(agent, request)
        decision = RoutingDecision(
            id=uuid4(), work_item_id=request.work_item.id,
            requested_role=request.role, selected_agent_id=agent.id,
            outcome="selected",
            rationale=(f"Selected {agent.name} deterministically with score "
                       f"{selected.total_score:.6f}."),
            candidates=evaluations, replacement_for_agent_id=replacement_for_agent_id,
            created_at=created_at,
        )
        self._persist_decision(decision)
        return decision

    def replace(
        self,
        request: RoutingRequest,
        failed_agent_id: UUID,
        *,
        now: datetime | None = None,
    ) -> RoutingDecision:
        replacement_request = RoutingRequest(
            work_item=request.work_item, role=request.role,
            required_tools=request.required_tools, domain=request.domain,
            risk_level=request.risk_level,
            producing_agent_id=request.producing_agent_id,
            excluded_agent_ids=tuple(set(request.excluded_agent_ids) | {failed_agent_id}),
            max_candidates=request.max_candidates,
        )
        return self.route(replacement_request, replacement_for_agent_id=failed_agent_id, now=now)

    def decisions_for(self, work_item_id: UUID) -> tuple[RoutingDecision, ...]:
        rows = self._connection.execute(
            "SELECT * FROM routing_decisions WHERE work_item_id = ? ORDER BY sequence ASC",
            (str(work_item_id),),
        ).fetchall()
        return tuple(_row_to_decision(row) for row in rows)

    def _evaluate(self, agent: Agent, request: RoutingRequest) -> CandidateEvaluation:
        reasons: list[str] = []
        required_caps = set(request.work_item.required_capabilities)
        caps = set(agent.capabilities)
        missing_caps = required_caps - caps
        if not agent.enabled:
            reasons.append("disabled")
        if agent.id in request.excluded_agent_ids:
            reasons.append("excluded")
        if agent.role is not request.role:
            reasons.append("role_mismatch")
        if missing_caps:
            reasons.append("missing_capabilities:" + ",".join(sorted(missing_caps)))
        missing_tools = set(request.required_tools) - set(agent.tool_permissions)
        if missing_tools:
            reasons.append("missing_tools:" + ",".join(sorted(missing_tools)))
        if request.domain and agent.domain_eligibility and request.domain not in agent.domain_eligibility:
            reasons.append("domain_ineligible")
        if request.role is AgentRole.REVIEW and agent.id == request.producing_agent_id:
            reasons.append("self_review_forbidden")
        if request.risk_level in {RiskLevel.HIGH, RiskLevel.CRITICAL} and not agent.authority_policy_ids:
            reasons.append("insufficient_authority")
        capability_score = 1.0 if not required_caps else len(required_caps & caps) / len(required_caps)
        reliability_score = agent.reliability_score
        cost_score = 1.0 / (1.0 + agent.cost_per_execution)
        total = capability_score * 0.55 + reliability_score * 0.35 + cost_score * 0.10
        return CandidateEvaluation(
            agent_id=agent.id, eligible=not reasons, reasons=tuple(reasons),
            capability_score=capability_score, reliability_score=reliability_score,
            cost_score=cost_score, total_score=total,
        )

    def _enforce_authority(self, agent: Agent, request: RoutingRequest) -> None:
        if agent.role is not request.role:
            raise AuthorityViolation("Agent role does not match requested role")
        if request.role is AgentRole.REVIEW and agent.id == request.producing_agent_id:
            raise AuthorityViolation("Agents cannot review or approve their own output")
        if request.risk_level in {RiskLevel.HIGH, RiskLevel.CRITICAL} and not agent.authority_policy_ids:
            raise AuthorityViolation("High-risk work requires explicit authority policy")

    def _persist_decision(self, decision: RoutingDecision) -> None:
        candidates = [
            {
                "agent_id": str(item.agent_id), "eligible": item.eligible,
                "reasons": list(item.reasons), "capability_score": item.capability_score,
                "reliability_score": item.reliability_score, "cost_score": item.cost_score,
                "total_score": item.total_score,
            }
            for item in decision.candidates
        ]
        with self._connection:
            self._connection.execute(
                """
                INSERT INTO routing_decisions(
                    id, work_item_id, requested_role, selected_agent_id, outcome,
                    rationale, candidates_json, replacement_for_agent_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(decision.id), str(decision.work_item_id), decision.requested_role.value,
                    str(decision.selected_agent_id) if decision.selected_agent_id else None,
                    decision.outcome, decision.rationale, json.dumps(candidates),
                    str(decision.replacement_for_agent_id) if decision.replacement_for_agent_id else None,
                    decision.created_at.isoformat(),
                ),
            )


def _row_to_agent(row: sqlite3.Row) -> Agent:
    return Agent(
        id=UUID(cast(str, row["id"])), name=cast(str, row["name"]),
        role=AgentRole(cast(str, row["role"])),
        capabilities=tuple(json.loads(cast(str, row["capabilities_json"]))),
        version=cast(str, row["version"]),
        tool_permissions=tuple(json.loads(cast(str, row["tool_permissions_json"]))),
        domain_eligibility=tuple(json.loads(cast(str, row["domain_eligibility_json"]))),
        authority_policy_ids=tuple(UUID(item) for item in json.loads(cast(str, row["authority_policy_ids_json"]))),
        cost_per_execution=cast(float, row["cost_per_execution"]),
        reliability_score=cast(float, row["reliability_score"]),
        enabled=bool(row["enabled"]),
        created_at=datetime.fromisoformat(cast(str, row["created_at"])),
    )


def _row_to_decision(row: sqlite3.Row) -> RoutingDecision:
    raw = json.loads(cast(str, row["candidates_json"]))
    candidates = tuple(
        CandidateEvaluation(
            agent_id=UUID(item["agent_id"]), eligible=bool(item["eligible"]),
            reasons=tuple(item["reasons"]), capability_score=float(item["capability_score"]),
            reliability_score=float(item["reliability_score"]), cost_score=float(item["cost_score"]),
            total_score=float(item["total_score"]),
        )
        for item in raw
    )
    selected = cast(str | None, row["selected_agent_id"])
    replacement = cast(str | None, row["replacement_for_agent_id"])
    return RoutingDecision(
        id=UUID(cast(str, row["id"])), work_item_id=UUID(cast(str, row["work_item_id"])),
        requested_role=AgentRole(cast(str, row["requested_role"])),
        selected_agent_id=UUID(selected) if selected else None,
        outcome=cast(str, row["outcome"]), rationale=cast(str, row["rationale"]),
        candidates=candidates, replacement_for_agent_id=UUID(replacement) if replacement else None,
        created_at=datetime.fromisoformat(cast(str, row["created_at"])),
    )
