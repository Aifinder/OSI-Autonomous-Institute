# Institutional Master Orchestrator Architecture v0.1

## Objective

Create a persistent executive control plane that turns institutional goals into validated outcomes without requiring routine founder supervision.

## Core responsibilities

The orchestrator owns:

1. Intake and normalization of goals.
2. Portfolio prioritization.
3. Work decomposition.
4. Dependency resolution.
5. Agent selection and delegation.
6. Budget and policy enforcement.
7. Review routing.
8. Revision loops.
9. Automated approval decisions.
10. Registry and knowledge-graph updates.
11. Exception escalation.
12. Progress and risk reporting.

## Logical components

### Goal Intake
Converts founder directives, approved strategies, research signals, and system-generated needs into structured objectives.

### Portfolio Manager
Ranks initiatives by strategic value, urgency, dependency impact, expected return, risk, and resource demand.

### Planning Engine
Produces work packages with acceptance criteria, dependencies, required evidence, assigned agent capabilities, review gates, budget, and stop conditions.

### Dependency Manager
Maintains a directed acyclic graph of artifacts, decisions, systems, and prerequisites. Blocks execution when required dependencies are incomplete or contradictory.

### Agent Registry and Router
Tracks agent roles, capabilities, tools, authority, cost profile, reliability, and eligible task classes. Selects agents based on policy rather than convenience.

### Execution Supervisor
Dispatches tasks, checkpoints progress, enforces time and token budgets, retries recoverable failures, and stops unsafe loops.

### Review Board Router
Routes outputs to independent reviewers. Production agents cannot approve their own work.

### Approval Engine
Evaluates gate results and chooses one of four outcomes:

- approve automatically
- return for revision
- reject and replace
- escalate to founder

### Artifact Registry
Stores canonical identifiers, versions, status, provenance, dependencies, owners, review history, and supersession links.

### Audit Ledger
Records every consequential state transition and decision basis.

### Escalation Manager
Consolidates founder decisions into concise, decision-ready packets containing context, options, recommendation, consequences, and default safe action.

## Initial agent groups

### Executive agents
- Portfolio Manager
- Planning Agent
- Dependency Manager
- Risk Controller
- Release Manager

### Production agents
- Architecture Agent
- Research Agent
- Document Agent
- Software Specification Agent
- Implementation Agent
- Product Design Agent

### Independent review agents
- Constitutional Reviewer
- Architecture Reviewer
- Evidence Reviewer
- Duplication and Contradiction Reviewer
- Security and Privacy Reviewer
- Quality Reviewer
- Test and Validation Reviewer

## State machine

A work item moves through:

`proposed -> qualified -> planned -> ready -> executing -> review -> revision | approval -> canonical -> monitored -> superseded`

Exception states:

`blocked`, `failed`, `paused`, `rejected`, `escalated`, `cancelled`.

## Non-negotiable controls

- No agent approves its own output.
- No canonical artifact is created without provenance.
- No material action proceeds beyond approved budget or authority.
- All retries and revision loops have limits.
- High-impact ambiguity defaults to pause and escalation.
- Routine compliant work defaults to execution without founder approval.

## Founder interaction model

The founder receives:

- constitutional or mission decisions
- material financial or legal decisions
- unresolved reviewer conflicts
- high-impact irreversible actions
- consolidated strategic reports

The founder does not receive routine document approvals, task assignments, revision choices, formatting decisions, ordinary architecture details, or normal release administration.
