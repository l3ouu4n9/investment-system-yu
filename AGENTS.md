# Antigravity & Agent Instruction Rules

## Project Purpose
This repository designs and operates an LLM-assisted ETF investment system.
Primary goal: safest durable deterministic system.
Prefer:
* deterministic contracts
* fail-closed behavior
* small PRs
* simple designs
* independently verifiable fixes
* minimal authority surfaces

## Source-of-truth Hierarchy
Use this hierarchy when sources disagree:
1. Explicitly authorized current project policy
2. Committed production code + closed schemas/contracts
3. Committed authority-bearing tests
4. Committed architecture/design documentation
5. Agent-generated reports/output

If an agent report contradicts committed policy/code/tests, the report loses. Do not silently reconcile contradictions.

## Agent-report Discipline
Agent-generated reports are evidence, not authority. A PASS/READY marker never overrides code, schemas, tests, or current approved policy.
When reviewing another agent's output:
* independently verify load-bearing claims;
* identify contradictions;
* preserve explicit uncertainty;
* never propagate a mistaken summary into code or durable policy.

**Correction of known report drift example:**
A previous activation audit incorrectly said:
`STRICT_STALE + fresh H1 → H1 state`

Committed policy says:
`STRICT_STALE + fresh H1 → STRICT_STALE`
with SELL preserved.

Use this as an example of why agents must independently verify summaries against committed owners.

## Manual LLM Boundary
This is a hard invariant.
The repository may:
* deterministically construct grounded prompts
* persist prompt/evidence artifacts
* accept exact operator-supplied raw LLM response bytes
* deterministically validate those bytes/results

The operator manually performs the LLM handoff.
Never add without a separately authorized design:
* model/provider API calls
* provider selection
* automatic prompt submission
* automatic response retrieval
* polling, retries, or scheduling of LLM calls
* agents performing LLM handoff
* SDKs, credentials, or endpoints
* network/browser-based LLM workflow

The repository consumes only operator-supplied response bytes.

## Authority Boundary
This system is LLM-assisted, not LLM-authorized.
LLM prose may provide:
* research
* evidence synthesis
* qualitative interpretation

Deterministic code exclusively owns:
* schemas, normalization, validators
* provenance/authentication, current-source binding
* state, freshness, availability, permissions, gates
* budgets/caps, target quantities, order readiness
* publication, final safety, execution authority

LLM prose must never create or infer:
* membership, availability, permissions
* budgets, quantities, orders
* gate outcomes, execution authority

## Fail-closed Safety
Bad, missing, stale, contradictory, malformed, invalid, unauthenticated, unsupported, or unverifiable authority-bearing data fails closed. HOLD and NO_TRADE are valid outcomes.
Never loosen: schemas, validators, gates, freshness, budgets, caps, or provenance requirements merely to pass tests or preserve a happy path.
Do not silently coerce malformed authority data.
If deterministic normalization is authorized, preserve:
* original value
* normalized value
* diagnostics

## Broker/Order Authority
Broker/live execution is unauthorized and unimplemented.
Orders are review-only artifacts for manual entry.
Never add without a separately authorized architecture:
* broker automation
* live order submission
* automatic placement
* executable trading authority
* conversion of non-order artifacts into executable orders

## Current committed H1 policy — change only under explicit authorization
State: `H1_MAPPED_FRESH_NON_ACTIONABLE`
Source: `H1_ROLE_MAPPED`
Allowed actions exactly:
* HOLD
* NO_TRADE

Blocked: SELL, NEW_BUY, ROTATION, REBALANCE, EXTENDED_ETF_ADMISSION, ORDER_COMPILATION, promoted decision/audit permissions.

H1 must never map to `STRICT_FRESH`.
Stale H1 must never map to `STRICT_STALE`.

Current precedence:
Fresh valid H1 may replace only: `INVALID_CONTRACT`, `DEGRADED_NO_RESEARCH`, `NO_OUTPUT`, `DEGRADED_WITH_LAST_GOOD`.
Fresh H1 must NOT replace: `STRICT_FRESH`, `STRICT_STALE`, `STRICT_FRESH_EVIDENCE_ONLY`, `STRICT_FRESH_GROUNDED_MEMO_NON_ACTIONABLE`, `STRICT_FRESH_COMPILED_ACTIONABLE_PENDING_GATES`, `STRICT_FRESH_COMPILED_ACTIONABLE_STEP2_DECISION_ONLY`, `STRICT_FRESH_COMPILED_ACTIONABLE_STEP3_AUDIT_ONLY`, `MANUAL_REVIEW_REQUIRED`.

Therefore explicitly state:
`STRICT_STALE + fresh H1 → STRICT_STALE; SELL preserved.`

Current H1 recognition capability is committed but dormant until a separately authorized production activation path exists. Do NOT encode any current activation design report as closed policy.

## State and Permission Changes
For every future state/permission behavior change require agents to identify BEFORE and AFTER effects on:
* HOLD, NO_TRADE, SELL, NEW_BUY, ORDER_COMPILATION
* Step 2, Step 3, downstream stages, final safety
* publication, pointers, order paths

State recognition must never implicitly expand permissions. A new recognized state remains non-actionable unless a separate narrow permission change explicitly authorizes more.

## Workflow Sequencing
Encode the preferred progression:
architecture/design-only → report-only instrumentation → fail-closed validators/checkers → non-actionable state recognition → narrow behavior changes → smallest permission changes → safety audit.

For LLM-related stages:
deterministic prompt construction → manual operator handoff → operator-supplied exact raw response → deterministic validation → report-only/non-authority artifacts → separately reviewed activation if needed.

Never jump from "candidate validates" to "order path enabled".
Do not combine in one PR unless explicitly authorized: state recognition, permission expansion, gate change, publication activation, order-path activation.

## Ownership and Validation
Each safety invariant should have one owning layer. Prefer reuse of the authoritative owner rather than duplicate validation.
Do not:
* create a second validator for convenience
* copy state/action tables into downstream code
* duplicate freshness policy
* make downstream layers reinterpret provenance owned upstream

Downstream authority boundaries should consume authoritative deterministic results.

## Provenance and Compatibility
Require special proof for changes touching: grounding, activation, current-source selection, provenance/authentication, availability, permissions, gates, final safety, publication, pointers, persistent identities, order paths.
Preserve unless migration is explicitly authorized: hashes, artifact identities, historical artifacts, pointers, serialization compatibility.
Archived/prospective evidence never becomes current production data merely because it validates internally.
Current-source binding must remain explicit.

## LKG
H1 currently has no H1 LKG.
Do not: write H1 LKG, read H1 LKG, overwrite Legacy LKG, promote H1 into Legacy LKG, or create H1 fallback identity without separately authorized policy/design.

## Step 2 / Step 3 / Final Safety
* Step 2: must independently enforce current research admission before persistence.
* Step 3: must independently enforce current research admission before residual upstream artifacts can create new Step 3 authority.
* Final safety: must independently validate the authoritative permission contract before permission data can contribute order readiness.

Residual artifacts never create current authority by presence alone. Do not weaken one boundary because another downstream boundary exists.

## Testing Philosophy
Exhaustively prove authority-bearing boundaries: provenance/authentication, current-source binding, state/permission changes, freshness, gates, budgets/caps, quantities, publication, persistent identities, complete expected-value equality where required, final safety, order paths, broker isolation, blocked-vs-contract-failure classification.

Use representative tests for: formatting, delimiter cases, presentation, schema mutations already strongly owned upstream, size limits and equivalent non-authority permutations.

Prefer independent oracles, complete table equality, identity checks, end-to-end boundary proofs over giant mutation matrices.
Do not retest states made impossible by an upstream closed schema unless another bypass exists.
Do not freeze private helpers, incidental ordering, wording, or serialization arithmetic unless safety-critical.

## Proportionality
Unnecessary complexity is a defect.
Avoid without demonstrated need: frameworks, registries, duplicate security layers, duplicate validators, speculative abstractions, extra persistence, caches, retry managers, migrations, new public contracts.
Prefer the smallest deterministic design preserving: fail-closed behavior, provenance, authority separation, compatibility, auditability.

## Task Modes
### DESIGN / AUDIT / REVIEW
Default: read-only; no edits; no commit; no push; no deploy.
Re-derive conclusions from repo evidence. Do not trust previous agent summaries blindly.

### IMPLEMENTATION
* modify only explicitly authorized files/contracts;
* do not broaden production scope automatically;
* if an additional authority-bearing owner must change, STOP and report the blocker;
* do not commit unless separately instructed.

### MECHANICAL COMMIT
Only after explicit authorization.
Require: exact expected parent/HEAD; exact reviewed file inventory; clean staging; `git diff --check`; `git diff --cached --check`.
Never amend/squash/rebase unless explicitly instructed. Never push/deploy unless explicitly instructed.

## Parallelism
Optimize for speed without weakening authority proof.
Rule: `Parallelize evidence, not authority-bearing edits.`
Subagents may independently perform read-only: caller graph tracing, provenance/artifact tracing, test inventory, downstream authority tracing, compatibility analysis.
Subagents must NOT independently: edit overlapping production authority surfaces, change policy, expand permissions, authorize commit, synthesize final disposition.

Primary agent owns final synthesis. If subagents disagree: report the disagreement and verify against authoritative code/tests. Never majority-vote a safety invariant. Prefer at most 2–3 focused read-only subagents for a single audit unless the task explicitly warrants more.

## Failure Classification
Require agents to classify failures using the owning layer, including: artifact-content, prompt-contract, validator/schema, compiler/normalizer, workflow/orchestrator, availability/permission, gate/final-safety, publication/pointer, true code bug, operator-input, unsafe scope creep.
Do not flatten distinct failures into generic `NO_OUTPUT` when the repository already has a more precise owner/category.
