# OSI Executive Program Status

**System of record:** This document is the canonical executive-level program tracker for the OSI Autonomous Institute.

**Last updated:** 2026-07-22

## Executive summary

Release 0.1 is functionally complete and CI-verified. The program is transitioning from governed orchestration foundations into Release 0.2: a continuously operating autonomous kernel. The immediate critical path is to confirm the CI workflow remains green after upgrading GitHub Actions to Node.js 24-compatible versions, then establish the formal `v0.1.0` baseline.

## Program health

| Dimension | Status | Notes |
|---|---|---|
| Scope | Green | Release 0.2 scope is bounded to runtime and platform foundations. |
| Architecture | Green | Governed execution boundaries and canonical contracts are established. |
| Engineering | Green | Release 0.1 passed tests, Ruff, and strict MyPy. |
| CI/CD | Green/monitoring | CI passed on `e3b0236`; action versions were upgraded in `f597dc3`. |
| Governance | Green | Policy stops, budgets, escalations, and audit events are implemented. |
| Delivery risk | Yellow | Runtime complexity increases materially in Release 0.2. |
| Founder decisions | Green | No founder decision is currently blocking execution. |

## Release status

### Release 0.1 — Governed Orchestrator

**Status:** CI-verified; release-hardening in progress.

Completed capabilities:

- Governed work lifecycle and immutable audit events
- Canonical versioned contracts
- SQLite persistence and replay recovery
- Durable queue primitives, leases, retries, delays, and dead-letter handling
- Agent registry and deterministic routing
- Independent production and review roles
- Planning, execution, review, approval, and artifact promotion
- Dependency scheduling and cycle detection
- Governance policy stops and founder escalation packets
- Budget reservation, enforcement, and settlement
- Operator CLI
- Automated tests, Ruff, and strict MyPy

Exit criteria remaining:

- Confirm CI success after action-version upgrade
- Reconcile documentation with the final implementation
- Create formal `v0.1.0` Git tag/release
- Record release notes and known limitations

### Release 0.2 — Autonomous Kernel

**Status:** Planned; implementation begins after the Release 0.1 baseline is locked.

Planned capabilities, in dependency order:

1. Persistent worker supervisor
2. Durable queue consumption loop
3. Lease renewal and timeout recovery
4. Retry policy and dead-letter operations
5. Scheduler and delayed execution
6. Event bus and event consumers
7. Checkpointing and restart recovery
8. Plugin runtime and lifecycle
9. Operational health, metrics, logs, and tracing
10. Agent lifecycle management

## Critical path

1. CI validates commit `f597dc30c272e8413d07670fcee8da807eef5427` or a later documentation-only commit containing it.
2. Release 0.1 documentation and known limitations are finalized.
3. `v0.1.0` is tagged as the immutable baseline.
4. Release 0.2 worker-runtime design is converted into implementation issues and acceptance tests.
5. Persistent execution begins behind governed interfaces.

## Milestone register

| Milestone | Target state | Status |
|---|---|---|
| M0 — Repository and architecture foundation | Canonical repo, packaging, contracts, storage | Complete |
| M1 — Governed orchestrator | Goal-to-artifact governed vertical slice | Complete |
| M2 — Release 0.1 verification | Tests, lint, types, CI green | Complete |
| M3 — Release 0.1 baseline | Documentation reconciled and `v0.1.0` tagged | In progress |
| M4 — Persistent runtime | Supervised workers consume durable work | Not started |
| M5 — Recovery and scheduling | Restart recovery, retries, scheduling | Not started |
| M6 — Plugin platform | Governed plugin lifecycle and isolation | Not started |
| M7 — First institutional application | LOIS or another domain system runs as a plugin | Not started |

## Risk register

| ID | Risk | Probability | Impact | Mitigation |
|---|---|---:|---:|---|
| R-001 | Runtime concurrency introduces duplicate execution | Medium | High | Idempotency keys, leases, optimistic concurrency, replay tests |
| R-002 | Worker crashes leave work stranded | Medium | High | Lease expiry, heartbeat renewal, recovery scans, checkpointing |
| R-003 | Plugin code bypasses governance boundaries | Medium | Critical | Capability-limited interfaces, policy preflight, sandbox strategy, audit events |
| R-004 | Scope expands into domain applications before kernel stability | Medium | High | Enforce release gates and critical-path sequencing |
| R-005 | Documentation diverges from implementation | Medium | Medium | Update docs in the same pull request/commit as behavior changes |
| R-006 | Operational failures are difficult to diagnose | Medium | High | Structured logs, metrics, traces, correlation IDs, health endpoints |

## Decision register

| ID | Decision | Status | Rationale |
|---|---|---|---|
| D-001 | GitHub is the canonical versioned source of truth | Accepted | Provides auditability, review, history, and release baselines |
| D-002 | Founder approval is exception-based, not routine | Accepted | Preserves autonomy while protecting constitutional and material decisions |
| D-003 | Domain systems run as governed plugins on a shared kernel | Accepted | Prevents duplicated infrastructure and creates reusable institutional capabilities |
| D-004 | Release 0.1 must be locked before Release 0.2 implementation | Accepted | Establishes a stable rollback and comparison baseline |
| D-005 | Executive program management artifacts live in the repository | Accepted | Creates a durable, inspectable program record rather than conversational estimates |

## Document control

The following program-management documents should be maintained as the program grows:

- `PROGRAM_STATUS.md` — executive status and critical path
- `ROADMAP.md` — release sequencing and acceptance outcomes
- `RISK_REGISTER.md` — detailed operational, technical, legal, and security risks
- `DECISION_REGISTER.md` — executive and product decisions
- `docs/adr/` — architecture decision records
- `RELEASES.md` — release history and known limitations
- `DOCUMENT_REGISTER.md` — planned and completed institutional documents

Until those files are created separately, this document is the authoritative consolidated register.

## Next actions

1. Verify the post-upgrade CI run is green.
2. Prepare Release 0.1 release notes and known limitations.
3. Create the formal `v0.1.0` release baseline.
4. Draft the Release 0.2 runtime architecture and acceptance-test matrix.
5. Open implementation work for the worker supervisor and durable consumption loop.
