from datetime import UTC, datetime
from uuid import uuid4

import pytest

from osi_orchestrator.contract_codec import ContractDecodeError, decode_contract, encode_contract
from osi_orchestrator.contracts import (
    Agent,
    AgentRole,
    ApprovalDecision,
    ApprovalOutcome,
    Artifact,
    Budget,
    Dependency,
    Escalation,
    Objective,
    Policy,
    PolicyEffect,
    Review,
    ReviewOutcome,
    RiskLevel,
    WorkItemSpec,
)

NOW = datetime(2026, 7, 22, tzinfo=UTC)


def test_all_canonical_contracts_round_trip() -> None:
    objective = Objective(
        title="Complete orchestrator kernel",
        description="Deliver the governed execution foundation.",
        success_criteria=("End-to-end recovery passes",),
        created_at=NOW,
    )
    work_item = WorkItemSpec(
        objective_id=objective.id,
        title="Lock schemas",
        description="Define stable versioned contracts.",
        required_capabilities=("python", "architecture"),
        required_review_gates=("architecture", "tests"),
        created_at=NOW,
    )
    dependency = Dependency(
        predecessor_id=uuid4(),
        successor_id=work_item.id,
        created_at=NOW,
    )
    agent = Agent(
        name="Kernel Engineer",
        role=AgentRole.PRODUCTION,
        capabilities=("python",),
        created_at=NOW,
    )
    artifact = Artifact(
        work_item_id=work_item.id,
        name="contracts.py",
        media_type="text/x-python",
        content_uri="git://contracts.py",
        content_hash="sha256:abc",
        created_by_agent_id=agent.id,
        metadata={"repository": "OSI-Autonomous-Institute"},
        created_at=NOW,
    )
    review = Review(
        work_item_id=work_item.id,
        artifact_ids=(artifact.id,),
        reviewer_agent_id=uuid4(),
        gate="architecture",
        outcome=ReviewOutcome.APPROVE,
        rationale="Contracts are complete and internally consistent.",
        reviewed_at=NOW,
    )
    decision = ApprovalDecision(
        work_item_id=work_item.id,
        outcome=ApprovalOutcome.APPROVED,
        decided_by="approval-engine",
        rationale="All required gates passed.",
        review_ids=(review.id,),
        decided_at=NOW,
    )
    escalation = Escalation(
        work_item_id=work_item.id,
        rule_id=uuid4(),
        question="Approve a material scope change?",
        reason="The change exceeds delegated authority.",
        options=("approve", "reject"),
        recommendation="reject",
        risk_level=RiskLevel.HIGH,
        created_at=NOW,
    )
    budget = Budget(name="Kernel release", currency="usd", limit=1000, created_at=NOW)
    policy = Policy(
        name="No self approval",
        description="Production agents cannot approve their own output.",
        effect=PolicyEffect.DENY,
        condition="producer_agent_id == reviewer_agent_id",
        created_at=NOW,
    )

    contracts = (
        objective,
        work_item,
        dependency,
        agent,
        review,
        decision,
        artifact,
        escalation,
        budget,
        policy,
    )

    for contract in contracts:
        assert decode_contract(encode_contract(contract)) == contract


def test_codec_rejects_unknown_schema_version() -> None:
    document = encode_contract(
        Objective(title="Goal", description="Description", created_at=NOW)
    )
    payload = document["payload"]
    assert isinstance(payload, dict)
    payload["schema_version"] = "99.0"

    with pytest.raises(ContractDecodeError, match="Unsupported schema version"):
        decode_contract(document)


def test_codec_rejects_unknown_fields() -> None:
    document = encode_contract(
        Objective(title="Goal", description="Description", created_at=NOW)
    )
    payload = document["payload"]
    assert isinstance(payload, dict)
    payload["unrecognized"] = True

    with pytest.raises(ContractDecodeError, match="Unknown fields"):
        decode_contract(document)


def test_work_item_rejects_self_dependency() -> None:
    work_item_id = uuid4()

    with pytest.raises(ValueError, match="cannot depend on itself"):
        WorkItemSpec(
            id=work_item_id,
            objective_id=uuid4(),
            title="Invalid",
            description="Invalid dependency graph node.",
            dependency_ids=(work_item_id,),
        )


def test_agent_requires_capability_and_bounded_reliability() -> None:
    with pytest.raises(ValueError, match="at least one capability"):
        Agent(name="Empty", role=AgentRole.PRODUCTION, capabilities=())

    with pytest.raises(ValueError, match="between 0 and 1"):
        Agent(
            name="Unbounded",
            role=AgentRole.PRODUCTION,
            capabilities=("python",),
            reliability_score=1.1,
        )


def test_escalated_decision_requires_escalation_reference() -> None:
    with pytest.raises(ValueError, match="require escalation_id"):
        ApprovalDecision(
            work_item_id=uuid4(),
            outcome=ApprovalOutcome.ESCALATED,
            decided_by="approval-engine",
            rationale="Policy requires founder authority.",
            review_ids=(uuid4(),),
        )


def test_budget_available_and_overcommitment_guard() -> None:
    budget = Budget(name="Release", currency="usd", limit=100, spent=20, reserved=30)
    assert budget.currency == "USD"
    assert budget.available == 50

    with pytest.raises(ValueError, match="exceed limit"):
        Budget(name="Invalid", currency="USD", limit=100, spent=80, reserved=30)
