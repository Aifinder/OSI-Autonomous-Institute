# OSI Autonomous Institute

The OSI Autonomous Institute repository is the canonical system of record for the AI-native institutional engineering platform that will design, build, review, approve, version, and evolve the Operating Systems Institute and future ventures.

## Core idea

The orchestrator is the operating center of the system. It converts founder intent, constitutional rules, strategy, and backlog priorities into governed execution across specialist agents, reviewers, software systems, and knowledge pipelines.

The founder is not the routine approver. Thomas Lee retains authority only for constitutional changes, mission changes, irreversible strategic commitments, material legal or financial exposure, and unresolved high-risk escalations.

## Initial release

Release 0.1 establishes:

- Institutional Master Orchestrator
- task queue and dependency graph
- agent contracts and authority boundaries
- automated review and approval gates
- escalation policy
- audit trail
- artifact registry
- architecture decision records
- operator CLI and dashboard requirements

## Operating principles

1. Escalate exceptions, not routine work.
2. Every autonomous decision must be traceable and reversible when practical.
3. Agents may propose; governed workflows promote artifacts to canonical status.
4. GitHub is the canonical versioned source of truth.
5. The orchestrator owns planning, delegation, review routing, revision loops, approval routing, and status reporting.
6. Founder involvement is reserved for decisions that cannot safely be delegated.
7. Autonomy expands only after measured validation.

## Implemented kernel capabilities

- Canonical governed work-item lifecycle.
- Immutable transition audit events.
- SQLite-backed work-item snapshots and audit history.
- Optimistic concurrency and stale-write protection.
- Idempotent transition request handling.
- Recovery by replaying the authoritative audit stream.
- Automated lint, strict type checking, and tests through GitHub Actions.

## Current build sequence

1. Governed lifecycle state machine — implemented.
2. Persistent audit ledger and work-item repository — implemented.
3. Durable event bus and work queue — active next component.
4. Agent registry and capability routing.
5. Governance and independent review pipeline.
6. Institutional memory and autonomous execution loop.

## Current status

The Orchestrator Kernel is under active implementation. Existing KCS production remains paused at KCS-072 until the autonomous pipeline is operational and validated.
