# Operator-Approved Anchor Grounding Runbook

This runbook documents the R2G-5c operator-approved anchor grounding path.
It is about report-only grounding for `compiled_support_signals.json`; it is not
trade authorization.

Current runtime behavior:

- `evidence_packet.active_anchor_registry` is the registry that
  `support_signals` consumes.
- The embedded registry is selected by a fresh R2G-5c readiness-gated compile:
  approvals-inclusive when safe, baseline when approvals are unsafe but baseline
  is safe, and fail-closed empty when baseline is unsafe.
- Valid operator-approved anchors can ground analyst memo claims in
  `support_signals`.
- Grounded support signals remain `permission_effect: "none"` and
  `not_authorization: true`.

## 1. Source Roles

### `research_anchor_candidates.json`

`research_anchor_candidates.json` is a report-only suggestion artifact. It is
derived from gaps and memo context to help the operator decide what anchor might
be worth authoring. It is never consumed for grounding, never activates an
anchor, and never grants authority.

Candidate fields are audit-only. In particular, `candidate_sha256` is only a
traceability link back to the suggested candidate. A candidate hash mismatch is
an audit note only: it does not authorize, does not block, and does not activate
anything.

### `research_anchor_approvals.yaml`

`inputs/current/research_anchor_approvals.yaml` is the operator-authored input
for approved anchors. It contains complete `operator_completed_anchor` entries,
not candidate skeletons. An approval can create a grounding anchor only after
deterministic validation.

The completed anchor must carry the same intrinsic anchor fields as
`research_anchors.yaml`: `anchor_id`, `anchor_type`, `applicable_tickers`,
`anchor_date_et`, `valid_from`, `valid_until`, `source_type`, and
`confidence_floor`. In v1, keep `source_type: "operator"`.

### `operator_completed_anchor_sha256`

`operator_completed_anchor_sha256` is the activation-binding hash. It must match
the deterministic hash of the complete `operator_completed_anchor`. If the
operator changes any field inside `operator_completed_anchor`, the hash changes
and the approval must be updated.

A missing or mismatched `operator_completed_anchor_sha256` fails closed: the
approval is inactive/rejected and cannot ground a memo claim.

### `candidate_sha256`

`candidate_sha256` is audit-only. It may be included with `candidate_id` to show
which suggestion the operator reviewed, but it is not an activation binding.

`candidate_sha256` does not authorize, does not block, does not activate, and is
not used for runtime grounding. Candidate mismatches remain audit notes only.

### `active_research_anchor_registry_with_approvals.json`

`active_research_anchor_registry_with_approvals.json` is a standalone observer
artifact. It shows what the approvals-inclusive registry looks like, but it is
not read directly by `support_signals`.

Use it for inspection only. Runtime grounding does not come from reading this
file as an authority source.

### `evidence_packet.active_anchor_registry`

`evidence_packet.active_anchor_registry` is the actual runtime registry consumed
by `support_signals`. It is selected by the R2G-5c readiness-gated fresh compile,
not by trusting stale JSON observer artifacts.

## 2. Selection Semantics

R2G-5c selection is deliberately three-way and fail-closed:

- If readiness is safe, embed the approvals-inclusive registry.
- If approvals are unsafe but the baseline registry is safe, embed the baseline
  registry.
- If the baseline registry is unsafe, embed a deterministic fail-closed empty
  registry.

Important consequences:

- A stale baseline is not a safe fallback.
- Malformed or invalid sources fail closed.
- `registry_valid: false` means zero usable anchors.
- Non-empty `active_anchors` must not be consumed when `registry_valid: false`.
- Missing or malformed approval inputs never broaden grounding.

The fail-closed empty registry is intentionally consumable but has
`active_anchors: []`, report-only markers, `permission_effect: "none"`, and
`not_authorization: true`. `support_signals` sees zero usable anchors.

## 3. Operator Approval Workflow

Use this workflow when an analyst memo is qualitatively useful but lacks a
deterministic approved anchor:

1. Review `artifacts/current/step1_research/research_anchor_candidates.json`.
2. If the operator agrees with a suggestion, author a complete
   `operator_completed_anchor` in
   `inputs/current/research_anchor_approvals.yaml`.
3. Include real dates: `anchor_date_et`, `valid_from`, and `valid_until`.
4. Keep the intrinsic anchor `source_type: "operator"`.
5. Compute and include `operator_completed_anchor_sha256` for the exact completed
   anchor.
6. Optionally include `candidate_id` and `candidate_sha256` for audit
   traceability only.
7. Run Step 1 parse/validation.
8. Inspect these artifacts:
   - `research_anchor_approvals_validation.json`
   - `active_research_anchor_registry_with_approvals.json`
   - `approval_registry_switch_readiness.json`
   - `support_signals_dual_ground_diff.json`
   - final `evidence_packet.json`, especially
     `evidence_packet.active_anchor_registry`
9. Confirm any grounded support remains report-only:
   `permission_effect: "none"` and `not_authorization: true`.

Do not copy a candidate skeleton into approvals unchanged unless it is already a
complete, operator-reviewed anchor with real dates and a matching
`operator_completed_anchor_sha256`.

## 4. Common Fail-Closed Outcomes

- Missing approvals manifest: benign empty approvals if baseline is safe.
- Malformed approvals manifest: baseline fallback if baseline is safe.
- Bad `operator_completed_anchor_sha256`: approval inactive/rejected.
- Stale or expired approval: not groundable.
- Duplicate `anchor_id` across baseline and approvals: baseline fallback if
  baseline is safe.
- Unsafe baseline: fail-closed empty embedded registry.
- Candidate mismatch: audit note only, not authority.
- `registry_valid: false`: no usable anchors, even if `active_anchors` is
  non-empty.

## 5. What This Does Not Do

Operator-approved anchor grounding does not:

- authorize buys
- create orders
- add `NEW_BUY`
- add `ORDER_COMPILATION`
- open Step 4
- change the final execution safety gate
- change weekly automation
- add broker automation
- submit live orders
- let LLM output activate anchors
- let candidate artifacts activate anchors

The only runtime effect is report-only support-signal grounding against the
registry embedded in `evidence_packet.active_anchor_registry`.

## 6. Future Work

The following are not implemented in R2G-5c:

- R2G-5d revocations. Revocation design should be separate and explicit.
- Candidate-index verification as a richer report-only audit enrichment.
- Source allowlists and Category-A deterministic sources.
- Any permission or order-path change tied to grounded support signals.

Future work must preserve the current authority split: candidates suggest,
operator approvals bind via `operator_completed_anchor_sha256`, and
`evidence_packet.active_anchor_registry` remains the runtime grounding source.
