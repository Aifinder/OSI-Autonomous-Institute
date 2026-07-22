from uuid import uuid4

import pytest

from osi_orchestrator.contracts import Agent, AgentRole, RiskLevel, WorkItemSpec
from osi_orchestrator.routing import NoEligibleAgent, RoutingRequest, SQLiteAgentRegistry


def agent(
    name: str,
    role: AgentRole,
    *,
    reliability: float = 1.0,
    cost: float = 0.0,
    authority: bool = False,
    enabled: bool = True,
):
    return Agent(
        name=name,
        role=role,
        capabilities=("research", "write"),
        tool_permissions=("web",),
        domain_eligibility=("longevity",),
        reliability_score=reliability,
        cost_per_execution=cost,
        authority_policy_ids=(uuid4(),) if authority else (),
        enabled=enabled,
    )


def work():
    return WorkItemSpec(
        objective_id=uuid4(),
        title="Research",
        description="Research and draft",
        required_capabilities=("research",),
    )


def test_registry_persists_agents(tmp_path):
    database = tmp_path / "registry.db"
    registered = agent("producer", AgentRole.PRODUCTION)
    with SQLiteAgentRegistry(database) as registry:
        registry.register(registered)
    with SQLiteAgentRegistry(database) as registry:
        assert registry.get(registered.id) == registered


def test_router_selects_best_eligible_agent_deterministically(tmp_path):
    with SQLiteAgentRegistry(tmp_path / "registry.db") as registry:
        low = registry.register(agent("low", AgentRole.PRODUCTION, reliability=0.7))
        high = registry.register(agent("high", AgentRole.PRODUCTION, reliability=0.95))
        request = RoutingRequest(
            work_item=work(),
            role=AgentRole.PRODUCTION,
            required_tools=("web",),
            domain="longevity",
        )
        first = registry.route(request)
        second = registry.route(request)
        assert first.selected_agent_id == high.id
        assert second.selected_agent_id == high.id
        assert first.selected_agent_id != low.id
        assert len(registry.decisions_for(request.work_item.id)) == 2


def test_review_agent_cannot_review_own_output(tmp_path):
    reviewer = agent("reviewer", AgentRole.REVIEW)
    with SQLiteAgentRegistry(tmp_path / "registry.db") as registry:
        registry.register(reviewer)
        request = RoutingRequest(
            work_item=work(),
            role=AgentRole.REVIEW,
            producing_agent_id=reviewer.id,
        )
        with pytest.raises(NoEligibleAgent):
            registry.route(request)


def test_high_risk_work_requires_explicit_authority(tmp_path):
    with SQLiteAgentRegistry(tmp_path / "registry.db") as registry:
        registry.register(agent("ordinary", AgentRole.PRODUCTION))
        authorized = registry.register(
            agent("authorized", AgentRole.PRODUCTION, authority=True)
        )
        decision = registry.route(
            RoutingRequest(
                work_item=work(),
                role=AgentRole.PRODUCTION,
                risk_level=RiskLevel.HIGH,
            )
        )
        assert decision.selected_agent_id == authorized.id


def test_failed_agent_is_replaced_by_next_candidate(tmp_path):
    with SQLiteAgentRegistry(tmp_path / "registry.db") as registry:
        first = registry.register(agent("first", AgentRole.PRODUCTION, reliability=1.0))
        second = registry.register(agent("second", AgentRole.PRODUCTION, reliability=0.9))
        request = RoutingRequest(work_item=work(), role=AgentRole.PRODUCTION)
        initial = registry.route(request)
        assert initial.selected_agent_id == first.id
        replacement = registry.replace(request, first.id)
        assert replacement.selected_agent_id == second.id
        assert replacement.replacement_for_agent_id == first.id


def test_unsupported_work_is_paused_and_audited(tmp_path):
    item = WorkItemSpec(
        objective_id=uuid4(),
        title="Unsupported",
        description="Needs unavailable capability",
        required_capabilities=("quantum",),
    )
    with SQLiteAgentRegistry(tmp_path / "registry.db") as registry:
        registry.register(agent("producer", AgentRole.PRODUCTION))
        with pytest.raises(NoEligibleAgent):
            registry.route(RoutingRequest(work_item=item, role=AgentRole.PRODUCTION))
        decisions = registry.decisions_for(item.id)
        assert decisions[-1].outcome == "paused"
        assert decisions[-1].selected_agent_id is None


def test_disabled_and_wrong_role_agents_are_ineligible(tmp_path):
    with SQLiteAgentRegistry(tmp_path / "registry.db") as registry:
        registry.register(agent("disabled", AgentRole.PRODUCTION, enabled=False))
        registry.register(agent("reviewer", AgentRole.REVIEW))
        with pytest.raises(NoEligibleAgent):
            registry.route(RoutingRequest(work_item=work(), role=AgentRole.PRODUCTION))
