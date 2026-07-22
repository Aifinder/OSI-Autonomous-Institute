from __future__ import annotations

import json
from pathlib import Path

from osi_orchestrator.cli import main
from osi_orchestrator.contracts import Agent, AgentRole
from osi_orchestrator.governance import (
    GovernanceContext,
    GovernanceStore,
    GovernedInstitution,
    StopCondition,
)
from osi_orchestrator.orchestrator import (
    ExecutionOutput,
    Goal,
    GovernedOrchestrator,
    ReviewOutput,
    ReviewVerdict,
    RunStatus,
)
from osi_orchestrator.routing import SQLiteAgentRegistry


def _produce(task, agent, revision):  # type: ignore[no-untyped-def]
    return ExecutionOutput("result.md", "text/markdown", task.description)


def _review(task, artifact, reviewer, gate):  # type: ignore[no-untyped-def]
    return ReviewOutput(ReviewVerdict.APPROVE, "approved")


def _institution(database: Path):  # type: ignore[no-untyped-def]
    registry = SQLiteAgentRegistry(database)
    registry.register(Agent("producer", AgentRole.PRODUCTION, ("general",)))
    registry.register(Agent("reviewer", AgentRole.REVIEW, ("quality",)))
    governance = GovernanceStore(database)
    orchestrator = GovernedOrchestrator(database, registry, _produce, _review)
    return registry, governance, orchestrator, GovernedInstitution(orchestrator, governance)


def test_legal_commitment_generates_founder_packet(tmp_path: Path) -> None:
    database = tmp_path / "osi.db"
    registry, governance, orchestrator, institution = _institution(database)
    try:
        governance.add_stop_condition(
            StopCondition("legal", "legal_commitment", reason="Legal approval required.")
        )
        result, packet = institution.submit_and_run(
            Goal("Sign agreement", "Commit the institution."),
            context=GovernanceContext(legal_commitment=True),
        )
        assert result.status is RunStatus.ESCALATED
        assert packet is not None
        assert packet.category == "legal"
        assert "Legal approval" in packet.issue
    finally:
        orchestrator.close()
        governance.close()
        registry.close()


def test_budget_blocks_then_allows_execution(tmp_path: Path) -> None:
    database = tmp_path / "osi.db"
    registry, governance, orchestrator, institution = _institution(database)
    try:
        small_budget = governance.create_budget("small", "USD", 5.0)
        blocked, packet = institution.submit_and_run(
            Goal("Expensive", "Create output."),
            context=GovernanceContext(estimated_cost=10.0), budget_id=small_budget,
        )
        assert blocked.status is RunStatus.ESCALATED
        assert packet is not None

        funded_budget = governance.create_budget("funded", "USD", 20.0)
        completed, packet = institution.submit_and_run(
            Goal("Funded", "Create output."),
            context=GovernanceContext(estimated_cost=10.0), budget_id=funded_budget,
            actual_cost=8.0,
        )
        assert completed.status is RunStatus.CANONICAL
        assert packet is None
        assert completed.canonical_artifact_ids
    finally:
        orchestrator.close()
        governance.close()
        registry.close()


def test_cli_initializes_and_runs_goal(tmp_path: Path, capsys) -> None:  # type: ignore[no-untyped-def]
    database = tmp_path / "cli.db"
    assert main(["--db", str(database), "init"]) == 0
    initialized = json.loads(capsys.readouterr().out)
    assert initialized["status"] == "initialized"

    assert main(["--db", str(database), "run", "Demo", "Create a demo artifact."]) == 0
    completed = json.loads(capsys.readouterr().out)
    assert completed["status"] == "canonical"
    assert completed["artifact_ids"]
