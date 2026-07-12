# Step 4 Evidence Fixtures B1a

## Purpose and scope

B1a preserves five small, repository-owned examples of observed Step 4 artifact
shapes. They are synthetic evidence fixtures for future parser design and review.
They are not runtime inputs, canonical publication candidates, validation results,
or authorization sources.

The fixture root is `tests/fixtures/step4_artifact_compatibility_v1/`.

| Bundle | Evidence shape |
| --- | --- |
| `zero_action_keep_only` | no new BUY or SELL action shape |
| `cancel_only` | BUY cancel action shape |
| `actionable_buy` | BUY action shape |
| `sell_only_structural` | structural SELL example only |
| `blocked_data_gap` | `DATA_GAP` / `COMPILER_BLOCKED` variant |

Every bundle contains exactly `template4_orders.txt`, `order_state_export.txt`,
`exec_summary.txt`, and `fixture_metadata.json`.

## Synthetic-data and metadata contract

All artifact values are deliberately synthetic placeholders. Fixtures must not carry
account identifiers, balances, broker data, operator identities, dated run paths,
run IDs, hashes, mutable artifact pointers, or runtime lineage.

Each metadata file uses `step4_fixture_evidence_v1` and contains only bounded
fixture names, evidence kinds, and evidence categories. The allowed evidence
categories are `STRATEGY_C_PROMPT`, `CURRENT_FORMAT_ARCHIVE_OBSERVATION`,
`RUNBOOK_OR_DESIGN_EVIDENCE`, and `VALIDATOR_TEST_EVIDENCE`. They describe
evidence categories, not a particular runtime artifact or publication lineage.

All metadata authority fields are false: runtime validity, canonical publication,
manual-order readiness, broker readiness, and SELL authorization are not granted.
Canonical validity is explicitly `NOT_ASSESSED`; B2 policy is explicitly
`UNRESOLVED`.

## B1/B2 boundary

B1a records examples without deciding parser or validator policy. B2 remains the
owner of duplicate-key handling, unknown-key handling, field order, field
optionality, compatibility aliases, unknown or literal-null intents, legacy
acceptance, SELL row grammar, and blocked reason-row grammar.

The SELL-only example is structural evidence only. It does not authorize SELL or
establish SELL readiness. The blocked example demonstrates the two blocked markers
only; its metadata marks blocked reason grammar `UNRESOLVED` and it does not define
a reason-row syntax.

## Runtime and deferred work

Fixtures are not imported or consumed by Steps 1 through 4 runtime code, validators,
publication, readiness, permissions, weekly orchestration, daily execution, final
safety, or broker/live-execution paths.

B1b scanner work is deferred. B2 canonical parser work, B3 validator integration,
and B4 diagnostics remain blocked pending separately approved contracts.
