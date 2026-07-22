# OSI Autonomous Institute

The OSI Autonomous Institute repository is the canonical system of record for an AI-native institutional engineering platform that plans, executes, reviews, approves, versions, and evolves autonomous institutional work.

## Core idea

The orchestrator converts founder intent, constitutional rules, strategy, budgets, and backlog priorities into governed execution across specialist agents and independent reviewers. Thomas Lee is not the routine approver. Founder involvement is reserved for constitutional or mission changes, irreversible strategic commitments, material legal or financial exposure, and unresolved high-risk exceptions.

## Release 0.1 capabilities

- Governed work-item lifecycle and immutable audit events.
- Versioned contracts for objectives, work, dependencies, agents, reviews, approvals, artifacts, escalations, budgets, and policies.
- SQLite persistence, optimistic concurrency, idempotency, and replay-based recovery.
- Durable work queue with leases, retries, delayed work, and dead-letter handling.
- Persistent agent registry and deterministic capability routing.
- Production/review role separation and self-review prevention.
- Executable goal-to-plan-to-artifact vertical slice.
- Dependency scheduling and cycle detection.
- Independent review gates, bounded revision loops, and automatic approval.
- Canonical artifact promotion with SHA-256 content hashes.
- Policy stop conditions for legal, irreversible, mission, and constitutional decisions.
- Budget creation, reservation, hard-stop enforcement, and settlement.
- Structured founder escalation packets.
- Operator CLI.
- Pytest, Ruff, and strict MyPy configuration.

## Local setup

Python 3.12 or newer is required.

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
pytest
ruff check .
mypy src
```

On Windows PowerShell, activate the environment with:

```powershell
.venv\Scripts\Activate.ps1
```

## Operator CLI

Initialize a local database and install the default demonstration producer and reviewer:

```bash
osi --db osi.db init
```

Run a routine governed goal:

```bash
osi --db osi.db run \
  "Create market brief" \
  "Produce a concise market brief with evidence and recommendations." \
  --success "The brief contains actionable recommendations"
```

Create a budget and pass its returned identifier into a cost-bearing goal:

```bash
osi --db osi.db create-budget research 25000 --currency USD
osi --db osi.db run "Research opportunity" "Evaluate the opportunity." \
  --estimated-cost 500 --budget-id <BUDGET_ID>
```

A qualifying founder-only decision is stopped before execution and returned as a structured decision packet:

```bash
osi --db osi.db run "Sign agreement" "Enter a binding supplier agreement." \
  --legal-commitment
```

Inspect status and event history:

```bash
osi --db osi.db status <GOAL_ID>
osi --db osi.db events <GOAL_ID>
```

The bundled CLI executors are deterministic demonstration adapters. Production deployments should inject real model and tool adapters through the `ProductionExecutor` and `ReviewExecutor` protocols.

## Operating principles

1. Escalate exceptions, not routine work.
2. Every autonomous decision must be traceable.
3. Agents may propose; governed workflows promote artifacts to canonical status.
4. GitHub is the canonical versioned source of truth.
5. The orchestrator owns planning, delegation, review routing, revision loops, approval, and status reporting.
6. Founder involvement is reserved for decisions that cannot safely be delegated.
7. Autonomy expands only after measured validation.

## Architecture

`GovernedOrchestrator` supplies the executable planning, routing, production, review, approval, artifact, escalation, and event path. `GovernanceStore` supplies policy stop conditions, budgets, reservations, settlement, and founder packets. `GovernedInstitution` composes those layers into a preflight-governed execution boundary.

`SQLiteAgentRegistry` filters agents by status, role, capability, tools, domain eligibility, exclusions, self-review rules, and high-risk authority. Eligible candidates are scored by capability match, reliability, and cost and selected deterministically.

## Verification status

The implementation is committed, but release readiness requires a successful GitHub Actions run covering tests, Ruff, and strict MyPy. Do not treat an unreported or missing check as a pass.

## Next milestone

After Release 0.1 verification, the next milestone is the continuous operating loop: scheduler, event consumers, worker supervision, restart recovery, operational health metrics, and plugin-based domain applications such as LOIS.
