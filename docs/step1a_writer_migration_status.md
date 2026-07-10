# Step 1A Writer-Source Migration — Status: Complete (8 of 9 artifact keys switched)

**Status: DOCUMENTATION ONLY.** This document records an architecture decision already reflected
in committed code (S1A-3 through S1A-12). It changes no production behavior, no writer, no guard,
no schema, no permission, no gate, and no order path.

## 1. Migration completion

Eight Step 1 report-only artifacts are intentionally Step 1A writer-sourced
(`STEP1A_WRITER_SOURCE_ARTIFACTS` in
`src/investment_orchestrator/workflow/step1a_grounding_compile.py`):

1. `active_research_anchor_registry`
2. `research_anchor_approvals_validation`
3. `research_anchor_revocations_validation`
4. `active_research_anchor_registry_with_approvals`
5. `approval_registry_switch_readiness`
6. `approval_registry_dual_read_diff`
7. `evidence_packet`
8. `embedded_active_anchor_registry_selection`

`grounding_status_observatory` — the ninth artifact key in
`step1a_grounding_compile._ARTIFACT_KEYS` — is **intentionally not writer-switched**.

**8/9 is the completed target architecture, not an incomplete migration.** No
`grounding_observatory_uses_step1a_output` marker exists or should be introduced. No ninth entry
should be added to `STEP1A_WRITER_SOURCE_ARTIFACTS` or to `step1a_artifact_switch_status.json`'s
`switched_artifacts`.

## 2. Why the observatory stays production-sourced

The observatory (`build_grounding_status_observatory` in
`src/investment_orchestrator/research/grounding_status_observatory.py`) is a single shared
summarizer called identically by both the production writer and the Step 1A bundle — there is no
separate legacy algorithm for it to migrate away from, unlike the eight switched artifacts.

Its only remaining independent value is plumbing / end-to-end integration:

- the **production** observatory summarizes the switched artifacts read back from **disk** (their
  final on-disk bytes, after each artifact's own writer/guard has run);
- the **Step 1A bundle** observatory summarizes the same artifacts as **in-memory bundle objects**.

This is the last end-to-end disk-integration witness in the chain:

```
Step 1A memory objects → guarded writers → disk artifacts → disk read-back → production observatory summary
```

Switching the observatory's writer to Step 1A would collapse this into a Step1A-vs-Step1A memory
comparison and destroy that witness — while retiring no legacy compiler and changing no runtime
consumer. It would be a net loss of signal for zero benefit.

## 3. Observatory role (unchanged)

- report-only; consumed **only** by the report-only shadow comparison
  (`step1a_grounding_compile_shadow_diff.json`)
- non-authoritative
- not an input to `evidence_packet`
- not an input to `support_signals`
- not an input to readiness
- not an input to permissions, gates, orders, or execution

## 4. Shadow semantics (unchanged, clarified)

On a clean run there are 9 shadow comparisons:

- **8** are Step1A-vs-final-disk **integrity/staleness** checks — one per switched artifact.
- The **9th** (`grounding_status_observatory`) is in-memory bundle vs production disk-read-back
  **integration**, not evidence of independent legacy-vs-Step1A summarizer algorithms (there is
  only one algorithm).

`parity_passed` for the observatory comparison row means integration/integrity parity — the
disk-read-back summary matches the in-memory bundle summary — not algorithmic independence. This
document changes no schema field names and no comparison code/behavior.

## 5. Permanent guards (unchanged)

- the evidence-packet full-payload guard (S1A-11) remains active
- the embedded-selection full-payload guard (S1A-12) remains active

Both remain the primary production-equivalence witnesses for their respective artifacts while
their legacy/current lineages exist. Neither guard is retired, weakened, or affected by this
decision.

## 6. Explicit scope exclusions

This decision (and the doc recording it) does **not**:

- switch the observatory writer
- add a ninth switched artifact
- add a new observatory writer guard
- change observatory inputs
- change observatory warnings
- change shadow code
- change any artifact schema or path
- change the evidence-packet or embedded-selection guards
- delete any legacy builder
- retire any comparison code
- change `support_signals`
- change runtime grounding
- change `allowed_actions`
- enable `NEW_BUY`
- enable `ORDER_COMPILATION`
- affect Step 2 / Step 3 / Step 4
- affect final execution safety
- affect the weekly workflow
- add broker/live execution
- add automatic order placement or executable authority

## 7. Known pre-existing observatory warnings (out of scope, unchanged)

The following are pre-existing, expected, and symmetric across production and Step 1A inputs.
They are diagnostics only and are **not** fixed or reinterpreted by this document:

- `missing_or_malformed_embedded_registry_selection`
- `missing_or_malformed_inputs_present`
- `evidence_packet_registry_mismatch_observer_registry`

## 8. Related / next track

The next architecture track — not started here — is legacy-lineage retirement design: what
probation evidence would justify removing the legacy/current builders behind the evidence-packet
and embedded-selection guards, once their Step 1A counterparts have accumulated sufficient clean
run history. That is a separate, explicitly scoped design effort.
