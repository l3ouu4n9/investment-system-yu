# R2F-1b-a: strict replacement memo contract

R2F-1b-a is a pure, report-only contract layer over one completed R2F-1a
generation. It does not add a CLI command, create a report directory, publish
an artifact, update a pointer, or have a runtime consumer.

It is not the Sunday weekly workflow. It does not affect availability, state
recognition, permissions, `NEW_BUY`, `SELL`, `ORDER_COMPILATION`, Steps 2–4,
final safety, canonical publication, manual-order readiness, or broker/live
execution.

## Source generation

The one supported production API, `validate_generation_memo(generation_id)`,
uses the code-owned canonical repository root and
requires one explicit lowercase 64-hex R2F-1a generation ID. It has no
operator-controlled root parameter and never scans for `latest`, `current`, or
any other generation. Isolated test repositories use a clearly private test
seam. The operation opens the repository and generation descriptor-relatively
with no-follow semantics and accepts exactly this completed inventory:

```text
replacement_input_manifest.json
evidence_packet.json
analyst_memo_prompt.txt
analyst_memo_raw_output.txt
render_generation_binding.json
```

`.render_in_progress`, missing entries, extra entries, symlinks, directories,
FIFOs, and every other non-regular entry fail closed. The reader verifies the
R2F-1a manifest, evidence, prompt, render binding, immutable hashes, canonical
hashes, and versioned semantic generation ID before it captures memo input.
Existing v1 generations retain their historical immutable verification contract,
but memo validation rejects them with `MEMO_PROMPT_PROFILE_UNSUPPORTED` before
reading editable memo content. Only generation profile v2 proceeds to capture.

The memo raw file is intentionally different: R2F-1a attests only that it was
blank when rendered. An operator may edit it later. R2F-1b-a requires it to
remain a descriptor-opened regular file, but does not compare its current bytes
to the initial blank hash and does not read it during immutable-source
verification. Immutable verification records the memo directory-entry identity
without reading memo content. An edit completed before verification is allowed;
an edit or replacement during an active verification/capture fails closed.

Within the same operation, the reader performs one accepted
descriptor-bound capture plus one same-descriptor verification pass, with
retained directory-entry identity checks before, between, and after those
passes. This is a stable fail-closed capture protocol, not an atomic filesystem
snapshot. Rename-and-replace, same-inode mutation, missing/nonregular entries,
and generation-path replacement are rejected. The memo has a 65,536-byte
limit, uses strict UTF-8 without a BOM, retains a raw-byte SHA-256, applies
deterministic universal newline normalization for parsing, and retains a
normalized-text SHA-256. No memo content is emitted in failures.
LF, CRLF, and lone-CR representations retain distinct raw hashes while sharing
the production-equivalent normalized-text identity.

All descriptors are owned by one operation-local cleanup owner and are closed
before any result is returned. Cleanup attempts every owned descriptor exactly
once even if an earlier close fails. A close failure discards any prepared
result and produces only `SOURCE_GENERATION_CLEANUP_FAILED`; an ambiguously
failed close is not retried. No handle, context manager, descriptor, directory
chain, registry entry, or close method escapes the operation.

## Cycle-free v2 prompt and strict raw memo content

Generation profile v2 is acyclic:

```text
verified frozen sources
+ immutable versioned template
+ bounded prompt projection
→ exact final prompt bytes and SHA-256
→ manifest and generation identity v2
→ generation ID
→ render binding v2
```

The prompt contains no generation ID and no manifest, evidence, prompt, or
source hash. There is no tokenized placeholder, prompt preimage, or model-reported
source binding. The manifest binds the closed prompt-contract projection,
template SHA-256, prompt-projection canonical SHA-256, final prompt SHA-256,
renderer/profile identities, and raw memo schema. The render binding attests the
same identities. During validation, the reader reopens the versioned template
descriptor-relatively, verifies it, rebuilds the bounded projection from verified
evidence, rerenders the prompt, and requires exact byte equality.

R2F-1b-a accepts only one content-only JSON object with this exact shape:

```json
{
  "schema_version": "r2f_analyst_memo_content_v2",
  "memo_result": "NO_TRADE",
  "confidence": "LOW",
  "instrument_observations": []
}
```

There is no user-controlled authority or origin marker. R2F-1b-a adds its own
code-owned markers only after validation.

The parser rejects duplicate keys at every object level, non-finite JSON
extensions, fenced JSON, trailing content, unknown keys, missing keys, wrong
types, noncanonical enum spelling, and unsupported schemas. It does not trim or
case-normalize a value in order to accept it.

Allowed values are:

- `memo_result`: `NO_TRADE` or `OBSERVATION_ONLY`.
- `confidence`: `LOW`, `MEDIUM`, or `HIGH`.
- `research_view`: `PREFER`, `NEUTRAL`, or `DEPRIORITIZE`.
- evidence-reference namespace: `ACTIVE_ANCHOR` only.

`NO_TRADE` requires an empty observations list. `OBSERVATION_ONLY` requires one
through 32 unique observations. Each observation requires one through eight
unique active-anchor references. A rationale is one through 280 Unicode code
points, already NFC, single-line, free of C0/DEL/C1 controls, and free of Unicode
line/paragraph separators (`Zl` and `Zp`). Ordinary Unicode spacing remains
allowed.

`NO_TRADE` here is an LLM memo result only. It is not a runtime weekly outcome,
permission, readiness, final-gate result, or order decision.

## Deterministic authority boundaries

The raw memo has no source-binding field. Deterministic code constructs binding
only from the verified generation: generation profile and ID, prompt-contract
schema/canonical hash, manifest exact/canonical hashes, evidence exact/canonical
hashes, final prompt hash, frozen `as_of`, and raw/normalized memo hashes. The
model neither supplies nor echoes any of those values.

Observation identifiers may come only from the verified evidence packet:

```text
universe.allowed_buy_tickers
+
universe.approved_extended_etf
```

Membership is exact and case-sensitive. It does not admit an instrument to an
execution universe. An identifier found only in the approved-extended list is
labeled `APPROVED_EXTENDED_OBSERVATION_ONLY` in the in-memory normalized result.

Evidence references may point only to exact active `anchor_id` values from the
verified `evidence_packet.active_anchor_registry.active_anchors`, with a
supported registry schema and `registry_valid: true`. The legacy
`research_anchors` diagnostic view is not R2F-1b reference authority.

The resulting in-memory object has schema
`r2f_validated_memo_envelope_v2` and code-owned markers including:

```json
{
  "artifact_role": "NON_AUTHORITATIVE_RESEARCH_OBSERVATION",
  "report_only": true,
  "runtime_consumed": false,
  "permission_effect": "NONE",
  "not_authorization": true,
  "order_authorization": false,
  "broker_authorization": false,
  "contract_validation": "VALID"
}
```

`VALID` means only that syntax, schema closure, deterministic source binding,
identifier membership, and
evidence references were deterministically verified. It does not attest factual
truth, freshness, permission, actionability, readiness, gate success, or safety.
The validated object never copies free-form rationale prose. Each structured
observation retains only its instrument, code-owned universe category,
research-view enum, verified evidence references, rationale UTF-8 SHA-256, and
rationale code-point count. The raw memo hash binds the original prose, but
`VALID` does not endorse it. R2F-1b-b must not silently restore free-form
rationale to a candidate artifact.

The production entrypoint accepts only an exact generation ID. It performs
generation verification, memo capture, strict normalization, and result
construction before closing its local descriptor owner. It returns only a
deeply immutable in-memory result containing canonical bytes and hashes; the
result contains no repository path, fd, operational inode/device identity,
weak reference, provenance token, registry membership, or close behavior.
Lower-level normalization remains private and is not the production API.

The operation protects against generation substitution during the call, memo
entry replacement during capture, same-inode concurrent memo changes detected
by the two-pass protocol, malformed or unbound memo input, caller attempts to
supply claims or descriptors through the public API, and descriptor leakage on
ordinary success and failure paths. It avoids persistent descriptor provenance
rather than claiming that POSIX fd integers are unforgeable.

This is not an authorization, permission system, or security sandbox. It does
not claim protection against monkeypatching internal validation or OS calls,
hostile frame-local or interpreter modification, or arbitrary same-process code
execution deliberately intended to disable enforcement.

## Bounded failure behavior

The public exceptions carry code-owned reasons such as
`SOURCE_GENERATION_INVALID`, `SOURCE_GENERATION_INCOMPLETE`, `MEMO_TOO_LARGE`,
`MEMO_BLANK`, `MEMO_UTF8_INVALID`, `MEMO_JSON_INVALID`,
`MEMO_DUPLICATE_KEY`, `MEMO_SCHEMA_UNSUPPORTED`,
`MEMO_KEY_CLOSURE_INVALID`, `MEMO_PROMPT_PROFILE_UNSUPPORTED`,
`MEMO_IDENTIFIER_INVALID`, `MEMO_EVIDENCE_REFERENCE_INVALID`, and
`MEMO_RESULT_CONTRADICTORY`. A cleanup failure is reported only as
`SOURCE_GENERATION_CLEANUP_FAILED`.

They do not contain raw memo content, malformed values, account or portfolio
data, absolute paths, symlink targets, or external contents.
Public parser exceptions contain only the bounded code in their arguments, have
no chained cause or context, and retain no raw parser object.

Failure precedence is deterministic: source generation/profile validity; memo
capture, size, and encoding; JSON syntax/duplicate keys; schema and key closure;
structural result contradictions; universe membership; evidence
references; then rationale canonicality.

## Compatibility boundary

Existing v1 generation directories remain immutable and readable under their
historical completion contract. They are not migrated, rewritten, or made
eligible by editing the memo. The one-shot validator rejects v1 memo validation
before reading the editable file. Newly rendered v2 generations use the strict
content-only prompt and validator. No `latest`, `current`, selected-generation,
or migration pointer exists.

## What remains blocked

R2F-1b-a does not compile a candidate observation report, publish a derived
generation, create a report binding, or add `replacement-report`. Those are
future R2F-1b-b work. Runtime recognition and any weekly integration remain
separate future stages.
