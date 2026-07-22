from __future__ import annotations

from uuid import uuid4

from osi_orchestrator.contracts import Agent, AgentRole
from osi_orchestrator.orchestrator import (
    ExecutionOutput,
    Goal,
    GovernedOrchestrator,
    ReviewOutput,
    ReviewVerdict,
    RunStatus,
)
from osi_orchestrator.routing import SQLiteAgentRegistry


def _register_agents(registry: SQLiteAgentRegistry) -> None:
    registry.register(Agent(name="builder", role=AgentRole.PRODUCTION, capabilities=("general",)))
    registry.register(Agent(name="reviewer", role=AgentRole.REVIEW, capabilities=("quality",)))


def test_goal_runs_to_canonical_artifact(tmp_path) -> None:
    registry_path = tmp_path / "agents.db"
    runtime_path = tmp_path / "runtime.db"
    with SQLiteAgentRegistry(registry_path) as registry:
        _register_agents(registry)

        def produce(task, agent, revision):
            suffix = f" revised:{revision}" if revision else ""
            return ExecutionOutput("result.txt", "text/plain", task.description + suffix)

        def review(task, artifact, reviewer, gate):
            return ReviewOutput(ReviewVerdict.APPROVE, "Meets the quality gate")

        with GovernedOrchestrator(runtime_path, registry, produce, review) as orchestrator:
            goal = Goal(title="Build result", description="Create a governed result")
            orchestrator.submit(goal)
            result = orchestrator.run(goal.id)
            assert result.status is RunStatus.CANONICAL
            assert len(result.canonical_artifact_ids) == 1
            assert orchestrator.status(goal.id) is RunStatus.CANONICAL
            assert [event["event_type"] for event in orchestrator.events(goal.id)] == [
                "goal_submitted",
                "goal_completed",
            ]


def test_revision_loop_is_bounded_and_can_succeed(tmp_path) -> None:
    with SQLiteAgentRegistry(tmp_path / "agents.db") as registry:
        _register_agents(registry)
        calls = {"reviews": 0}

        def produce(task, agent, revision):
            return ExecutionOutput("result.txt", "text/plain", revision or "draft")

        def review(task, artifact, reviewer, gate):
            calls["reviews"] += 1
            if calls["reviews"] == 1:
                return ReviewOutput(ReviewVerdict.REVISE, "Needs evidence", "add evidence")
            return ReviewOutput(ReviewVerdict.APPROVE, "Evidence added")

        with GovernedOrchestrator(tmp_path / "runtime.db", registry, produce, review) as orchestrator:
            goal = Goal(title="Revise", description="Exercise revision")
            orchestrator.submit(goal)
            result = orchestrator.run(goal.id)
            assert result.status is RunStatus.CANONICAL
            assert calls["reviews"] == 2


def test_missing_reviewer_escalates(tmp_path) -> None:
    with SQLiteAgentRegistry(tmp_path / "agents.db") as registry:
        registry.register(Agent(name="builder", role=AgentRole.PRODUCTION, capabilities=("general",)))

        def produce(task, agent, revision):
            return ExecutionOutput("result.txt", "text/plain", "draft")

        def review(task, artifact, reviewer, gate):
            raise AssertionError("review must not run")

        with GovernedOrchestrator(tmp_path / "runtime.db", registry, produce, review) as orchestrator:
            goal = Goal(title="Escalate", description="No reviewer exists")
            orchestrator.submit(goal)
            result = orchestrator.run(goal.id)
            assert result.status is RunStatus.ESCALATED
            assert result.escalation_id is not None
            assert orchestrator.status(goal.id) is RunStatus.ESCALATED


def test_cycle_is_rejected_before_persistence(tmp_path) -> None:
    from osi_orchestrator.orchestrator import PlannedTask

    first = uuid4()
    second = uuid4()

    def planner(goal):
        return (
            PlannedTask(first, goal.id, "one", "one", ("general",), ("quality",), (second,)),
            PlannedTask(second, goal.id, "two", "two", ("general",), ("quality",), (first,)),
        )

    with SQLiteAgentRegistry(tmp_path / "agents.db") as registry:
        _register_agents(registry)
        with GovernedOrchestrator(
            tmp_path / "runtime.db",
            registry,
            lambda task, agent, revision: ExecutionOutput("x", "text/plain", "x"),
            lambda task, artifact, reviewer, gate: ReviewOutput(ReviewVerdict.APPROVE, "ok"),
            planner=planner,
        ) as orchestrator:
            try:
                orchestrator.submit(Goal(title="cycle", description="cycle"))
            except ValueError as exc:
                assert "cycle" in str(exc)
            else:
                raise AssertionError("cyclic plan should be rejected")
