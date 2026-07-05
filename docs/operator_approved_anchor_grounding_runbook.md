# Operator-Approved Anchor Grounding Runbook

This runbook documents the R2G-5c/R2G-5d operator-approved anchor grounding
path. It is about report-only grounding for `compiled_support_signals.json`; it
is not trade authorization.

Current runtime behavior:

- `evidence_packet.active_anchor_registry` is the registry that
  `support_signals` consumes.
- The embedded registry is selected by a fresh readiness-gated compile:
  approvals-inclusive when safe, baseline when approvals-inclusive is unsafe
  but baseline is safe, and fail-closed empty when both are unsafe.
- Valid operator-approved anchors can ground analyst memo claims in
  `support_signals`.
- Valid active revocations can remove approval-derived anchors from the active
  registry before support grounding.
- Grounded support signals remain `permission_effect: "none"` and
  `not_authorization: true`.

## 1. Source Roles

### `research_anchor_candidates.json`

`research_anchor_candidates.json` is a report-only suggestion artifact. It is
derived from gaps and memo context to help the operator decide what anchor might
be worth authoring. It is never consumed for grounding, never activates an
anchor, never revokes an anchor, and never grants authority.

Candidate fields are audit-only. In particular, `candidate_sha256` is only a
traceability link back to the suggested candidate. A candidate hash mismatch is
an audit note only: it does not authorize, does not block, does not activate,
and does not revoke anything.

### `research_anchor_approvals.yaml`

`inputs/current/research_anchor_approvals.yaml` is the operator-authored input
for approved anchors and optional revocations. It contains complete
`operator_completed_anchor` entries, not candidate skeletons. An approval can
create a grounding anchor only after deterministic validation.

The completed anchor must carry the same intrinsic anchor fields as
`research_anchors.yaml`: `anchor_id`, `anchor_type`, `applicable_tickers`,
`anchor_date_et`, `valid_from`, `valid_until`, `source_type`, and
`confidence_floor`. In v1, keep `source_type: "operator"`.

### `operator_completed_anchor_sha256`

`operator_completed_anchor_sha256` is the activation-binding hash and the
revocation-binding hash. It must match the deterministic hash of the complete
`operator_completed_anchor`. If the operator changes any field inside
`operator_completed_anchor`, the hash changes and the approval and any matching
revocation must be updated.

A missing or mismatched `operator_completed_anchor_sha256` fails closed: the
approval is inactive/rejected and cannot ground a memo claim.

### `candidate_sha256`

`candidate_sha256` is audit-only. It may be included with `candidate_id` to show
which suggestion the operator reviewed, but it is not an activation binding and
is not a revocation binding.

`candidate_sha256` does not authorize, does not block, does not activate, does
not revoke, and is not used for runtime grounding. Candidate mismatches remain
audit notes only.

### `research_anchor_revocations_validation.json`

`research_anchor_revocations_validation.json` is a report-only validation
output. It validates the optional `revocations:` section of
`research_anchor_approvals.yaml`, but the on-disk JSON file is not read as
authority and is not consumed by `support_signals`.

The approvals-inclusive registry and embedded registry selection derive
revocation state freshly from the operator-authored YAML path. Do not treat the
validation artifact as an input that can apply, approve, or revoke anchors.

### `active_research_anchor_registry_with_approvals.json`

`active_research_anchor_registry_with_approvals.json` is a standalone observer
artifact. It shows what the approvals-inclusive registry looks like and may show
revocations moved into `inactive_anchors` with `status: "revoked"`, but it is
not read directly by `support_signals`.

Use it for inspection only. Runtime grounding comes from the registry embedded
in `evidence_packet.active_anchor_registry`.

### `evidence_packet.active_anchor_registry`

`evidence_packet.active_anchor_registry` is the compiled runtime registry
consumed by `support_signals`. It is the only active-anchor registry that
`support_signals` reads.

`support_signals` does not directly read revocation files, revocation validation
artifacts, approval manifests, or candidate artifacts. `registry_valid: false`
means zero usable anchors and no partial read, even if an artifact contains a
non-empty `active_anchors` array.

## 2. Revocations

Revocations let an operator make a previously approved, approval-derived anchor
unusable for report-only support grounding. First-version R2G-5d revocations
support approval-derived anchors only.

Baseline `research_anchors.yaml` revocation is not supported in R2G-5d. To
remove a baseline anchor, edit or remove it directly in baseline YAML, or let it
expire through `valid_until`.

`reason` is required for audit readability, but it is non-authoritative and is
never parsed for logic.

### Schema Example

```yaml
schema_version: research_anchor_approvals_v1
is_llm_generated: false
as_of_date: "2026-07-04"
approvals:
  - approval_id: APR-2026-07-04-001
    decision: approve
    operator_completed_anchor:
      anchor_id: AI_CAPEX_2026H2
      anchor_type: structural_theme
      applicable_tickers: [QQQ]
      anchor_date_et: "2026-06-15"
      valid_from: "2026-06-01"
      valid_until: "2026-07-31"
      source_type: operator
      confidence_floor: medium
      summary: "Operator-dated thesis grounding."
    operator_completed_anchor_sha256: "<sha256 of operator_completed_anchor>"
    approved_by: "operator"
revocations:
  - revocation_id: REV-2026-07-04-001
    target_type: approval_anchor
    approval_id: APR-2026-07-04-001
    anchor_id: AI_CAPEX_2026H2
    operator_completed_anchor_sha256: "<sha256 of operator_completed_anchor>"
    effective_as_of: "2026-07-04"
    reason: "Thesis invalidated."
    revoked_by: "operator"
```

### Binding Rules

A revocation must use `target_type: approval_anchor` and bind to exactly one
approval using all three required binding fields:

- `approval_id`
- `anchor_id`
- `operator_completed_anchor_sha256`

The three fields must all resolve to the same approval-derived anchor.
`operator_completed_anchor_sha256` is the binding hash. `candidate_sha256`
remains audit-only and cannot bind a revocation. `research_anchor_candidates.json`
remains report-only suggestions and cannot approve or revoke anchors.

### Active And Future Revocations

A valid active revocation moves the approval-derived anchor out of
`active_anchors` and into `inactive_anchors` with `status: "revoked"`. Revoked
anchors cannot ground support.

A future-dated revocation is recorded as pending and does not deactivate the
anchor early. The anchor remains groundable until the revocation is active, if
the rest of the registry remains safe and valid.

### Invalid Revocation State

Invalid revocation state makes the approvals-inclusive registry unsafe. It does
not partially consume the approval-derived anchors.

Invalid cases include:

- unknown target
- hash mismatch
- inconsistent `approval_id` / `anchor_id` /
  `operator_completed_anchor_sha256`
- duplicate `revocation_id`
- duplicate target revocation
- unsupported `target_type`
- forbidden keys or forbidden action/order tokens
- `is_llm_generated: true`
- malformed or non-list `revocations`
- `source_valid: false`
- `revocations_valid: false`

Fallback behavior:

- approvals-inclusive unsafe + baseline safe -> baseline fallback
- approvals-inclusive unsafe + baseline unsafe -> fail-closed empty registry

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
7. Add optional `revocations:` only when an approval-derived anchor must be
   disabled for support grounding.
8. Run Step 1 parse/validation.
9. Inspect these artifacts:
   - `research_anchor_approvals_validation.json`
   - `research_anchor_revocations_validation.json`
   - `active_research_anchor_registry_with_approvals.json`
   - `approval_registry_switch_readiness.json`
   - `support_signals_dual_ground_diff.json`
   - final `evidence_packet.json`, especially
     `evidence_packet.active_anchor_registry`
10. Confirm any grounded support remains report-only:
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
- Valid active revocation: approval-derived anchor is moved to revoked inactive.
- Future revocation: recorded pending; anchor is not deactivated early.
- Invalid revocation state: approvals-inclusive unsafe; baseline fallback if
  baseline is safe.
- Unsafe baseline and unsafe approvals-inclusive registry: fail-closed empty
  embedded registry.
- Candidate mismatch: audit note only, not authority.
- `registry_valid: false`: no usable anchors, even if `active_anchors` is
  non-empty.

## 5. What This Does Not Do

Operator-approved anchor grounding and revocation do not:

- authorize buys
- create orders
- add `NEW_BUY`
- add `ORDER_COMPILATION`
- open Step 4
- enable final execution
- change the final execution safety gate
- change weekly automation
- add broker automation
- submit live orders
- place orders automatically
- grant executable order authority
- change permissions, gates, or any order path
- let LLM output activate anchors
- let LLM output revoke anchors
- let candidate artifacts activate anchors
- let candidate artifacts revoke anchors

The only runtime effect is report-only support-signal grounding against the
registry embedded in `evidence_packet.active_anchor_registry`. Support signals
remain `permission_effect: "none"` and `not_authorization: true`.

## 6. Future Work

Future work must preserve the current authority split: candidates suggest,
operator approvals bind via `operator_completed_anchor_sha256`, approval-anchor
revocations bind via the same hash plus `approval_id` and `anchor_id`, and
`evidence_packet.active_anchor_registry` remains the runtime grounding source.

Potential future extensions are out of scope unless separately implemented:

- baseline `research_anchors.yaml` revocation
- candidate-index verification as a richer report-only audit enrichment
- source allowlists and Category-A deterministic sources
- any permission or order-path change tied to grounded support signals
