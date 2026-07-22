"""Command-line interface for the OSI governed orchestrator."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from uuid import UUID

from .contracts import Agent, AgentRole, RiskLevel
from .governance import GovernanceContext, GovernanceStore, GovernedInstitution, StopCondition
from .orchestrator import ExecutionOutput, Goal, GovernedOrchestrator, ReviewOutput, ReviewVerdict
from .routing import SQLiteAgentRegistry


def _produce(task, agent, revision):  # type: ignore[no-untyped-def]
    suffix = f"\nRevision instructions: {revision}" if revision else ""
    return ExecutionOutput(
        name=f"{task.title}.md",
        media_type="text/markdown",
        content=f"# {task.title}\n\n{task.description}{suffix}\n",
        metadata={"agent": agent.name},
    )


def _review(task, artifact, reviewer, gate):  # type: ignore[no-untyped-def]
    if not artifact.content.strip():
        return ReviewOutput(ReviewVerdict.REJECT, "Artifact is empty.")
    return ReviewOutput(ReviewVerdict.APPROVE,
                        f"{reviewer.name} approved gate {gate} for {task.title}.")


def _bootstrap_registry(database: Path) -> SQLiteAgentRegistry:
    registry = SQLiteAgentRegistry(database)
    existing = registry.list_agents()
    if existing:
        return registry
    registry.register(Agent(
        name="Default Producer", role=AgentRole.PRODUCTION,
        capabilities=("general",), reliability_score=0.95,
    ))
    registry.register(Agent(
        name="Default Quality Reviewer", role=AgentRole.REVIEW,
        capabilities=("quality",), reliability_score=0.95,
    ))
    return registry


def _risk(value: str) -> RiskLevel:
    try:
        return RiskLevel(value.lower())
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid risk level: {value}") from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="osi", description="OSI governed orchestrator")
    parser.add_argument("--db", default="osi.db", help="SQLite database path")
    commands = parser.add_subparsers(dest="command", required=True)

    run = commands.add_parser("run", help="submit and execute a goal")
    run.add_argument("title")
    run.add_argument("description")
    run.add_argument("--success", action="append", default=[])
    run.add_argument("--capability", action="append", default=["general"])
    run.add_argument("--review-gate", action="append", default=["quality"])
    run.add_argument("--risk", type=_risk, default=RiskLevel.MODERATE)
    run.add_argument("--estimated-cost", type=float, default=0.0)
    run.add_argument("--legal-commitment", action="store_true")
    run.add_argument("--irreversible", action="store_true")
    run.add_argument("--mission-change", action="store_true")
    run.add_argument("--constitutional-change", action="store_true")
    run.add_argument("--budget-id")

    status = commands.add_parser("status", help="show a goal status")
    status.add_argument("goal_id")

    events = commands.add_parser("events", help="show goal event history")
    events.add_argument("goal_id")

    budget = commands.add_parser("create-budget", help="create a governed budget")
    budget.add_argument("name")
    budget.add_argument("limit", type=float)
    budget.add_argument("--currency", default="USD")

    commands.add_parser("init", help="initialize database and default agents")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    database = Path(args.db)
    registry = _bootstrap_registry(database)
    governance = GovernanceStore(database)
    try:
        for field_name, name in (
            ("legal_commitment", "Material legal commitment"),
            ("irreversible", "Irreversible strategic decision"),
            ("mission_change", "Mission change"),
            ("constitutional_change", "Constitutional change"),
        ):
            governance.add_stop_condition(StopCondition(name=name, field_name=field_name))

        if args.command == "init":
            print(json.dumps({"database": str(database), "status": "initialized"}))
            return 0
        if args.command == "create-budget":
            budget_id = governance.create_budget(args.name, args.currency, args.limit)
            print(json.dumps({"budget_id": str(budget_id)}))
            return 0

        with GovernedOrchestrator(database, registry, _produce, _review) as orchestrator:
            if args.command == "status":
                print(json.dumps({"goal_id": args.goal_id,
                                  "status": orchestrator.status(UUID(args.goal_id)).value}))
                return 0
            if args.command == "events":
                print(json.dumps(orchestrator.events(UUID(args.goal_id)), indent=2))
                return 0
            goal = Goal(
                title=args.title, description=args.description,
                success_criteria=tuple(args.success), risk_level=args.risk,
                required_capabilities=tuple(dict.fromkeys(args.capability)),
                review_gates=tuple(dict.fromkeys(args.review_gate)),
            )
            institution = GovernedInstitution(orchestrator, governance)
            context = GovernanceContext(
                estimated_cost=args.estimated_cost,
                legal_commitment=args.legal_commitment,
                irreversible=args.irreversible,
                mission_change=args.mission_change,
                constitutional_change=args.constitutional_change,
            )
            result, packet = institution.submit_and_run(
                goal, context=context,
                budget_id=UUID(args.budget_id) if args.budget_id else None,
            )
            output: dict[str, object] = {
                "goal_id": str(result.goal_id), "status": result.status.value,
                "artifact_ids": [str(item) for item in result.canonical_artifact_ids],
                "escalation_id": str(result.escalation_id) if result.escalation_id else None,
            }
            if packet is not None:
                output["founder_packet"] = {
                    "category": packet.category, "issue": packet.issue,
                    "options": list(packet.options),
                    "recommendation": packet.recommendation,
                    "required_decision": packet.required_decision,
                }
            print(json.dumps(output, indent=2))
            return 0 if result.status.value == "canonical" else 2
    finally:
        governance.close()
        registry.close()


if __name__ == "__main__":
    raise SystemExit(main())
