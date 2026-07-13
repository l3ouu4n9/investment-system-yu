# R2F-1b-a+b: strict memo validation and immutable report publication

R2F-1b-a is the pure, report-only validation contract over one completed
R2F-1a generation. R2F-1b-b adds one explicit manual command that publishes the
validator's immutable canonical result as one content-addressed file:

```bash
PYTHONPATH=src uv run python \
  -m investment_orchestrator.cli.run_step1 \
  replacement-report \
  --generation-id <64-lowercase-hex>
```

This is manual immutable single-file report-only validated-memo publication.
The generation ID is mandatory, has no default, and must be exactly 64 lowercase
hexadecimal characters. The command does not scan directories, choose a
latest/current generation, accept an output root, or run automatically from any
other command.

It is not the Sunday weekly workflow. It does not affect availability, state
recognition, permissions, `NEW_BUY`, `SELL`, `ORDER_COMPILATION`, Steps 2–4,
final safety, canonical publication, manual-order readiness, or broker/live
execution.

## Immutable report publication

Only the explicit `replacement-report` branch lazily imports the publisher. It
first retains the code-owned repository directory identity, then calls the
committed `validate_generation_memo(generation_id)` operation. The complete
validated result, report identity, final filename, exact final bytes, return
value, and bounded display text are constructed before the publisher creates an
attempt-local output entry. A v1 generation fails through the existing validator
as `MEMO_PROMPT_PROFILE_UNSUPPORTED`; malformed, incomplete, unstable, or
unbound memo input likewise produces no completed report.

Completed reports exist only at:

```text
artifacts/current/step1_research/r2f_report_only/
  reports/
    <report-id>.json
```

The single regular file is the whole completed report. Its bytes are exactly
`ValidatedMemoEnvelope.canonical_bytes` returned by the one-shot validator. The
publisher never derives output bytes by parsing and reserializing the envelope,
appends a newline, or adds a report ID or publication metadata. The file's exact
SHA-256 equals the validator's canonical SHA-256. No directory-per-report,
sidecar, completion marker, raw memo, prompt, evidence, rationale prose,
diagnostic, timestamp,
operator metadata, candidate, score, handoff, readiness result, or order material
is published. There is no `latest`, `current`, `active`, or `selected` pointer,
symlink, index, status file, or report-selection artifact.

The report ID is the full lowercase SHA-256 of the UTF-8 compact canonical JSON
encoding of this closed projection:

```json
{
  "schema_version": "r2f_validated_memo_report_identity_v2",
  "publication_profile": "r2f_single_file_validated_memo_report_v1",
  "source_generation_profile": "step1_replacement_render_observation_v2",
  "source_generation_id": "<generation-id>",
  "validated_envelope_schema_version": "r2f_validated_memo_envelope_v2",
  "validated_envelope_canonical_sha256": "<sha256>",
  "prompt_contract_canonical_sha256": "<sha256>",
  "raw_memo_file_sha256": "<sha256>",
  "normalized_memo_text_sha256": "<sha256>",
  "authority_markers": {
    "report_only": true,
    "runtime_consumed": false,
    "permission_effect": "NONE",
    "not_authorization": true,
    "order_authorization": false,
    "broker_authorization": false
  }
}
```

Serialization uses `ensure_ascii=False`, sorted keys, compact separators, and no
trailing newline. Time, PID, absolute paths, filesystem metadata, temporary
names, checkout identity, and output-directory identity are excluded. The same
validated source and memo in equivalent checkouts therefore produces the same
report ID and bytes; an accepted memo-byte edit changes the bound memo hashes
and report ID.

Every identity field is reconstructed from the validated envelope and
code-owned constants; no identity file or binding sidecar is written.

Publication is descriptor-relative and no-follow from the retained repository
root through the complete code-owned output chain. Attempt files are separate
from completed reports:

```text
artifacts/current/step1_research/r2f_report_only/
  report_attempts/
    .attempt-<unguessable>.tmp
  reports/
    <report-id>.json
```

Under the retained `report_attempts` directory descriptor, an unpredictable
name that cannot match the final report pattern is created atomically with
`O_RDWR | O_CREAT | O_EXCL | O_NOFOLLOW` and mode `0600`. The name is not
derived only from a report ID, PID, or timestamp. The successful open both
creates the entry and returns the exact owned descriptor. The publisher rejects
a nonregular or unexpected multi-link attempt state and never closes and
reopens attempt content by pathname.

POSIX write-only descriptors cannot perform the required same-fd readback, so
the atomic create uses `O_RDWR` with `O_CREAT | O_EXCL | O_NOFOLLOW` rather than
reopening an `O_WRONLY` descriptor by pathname. The attempt file remains mode
`0600`; no additional pathname authority is introduced.

Before a final filename exists, the publisher writes the exact envelope bytes,
fsyncs the attempt file, rewinds and reads it twice through the retained
descriptor, verifies exact bytes and SHA-256, reconstructs the closed envelope
and identity, revalidates the attempt entry/inode, fsyncs the attempts directory,
requires the attempts and reports directories to be on the same filesystem, and
revalidates both canonical directory chains.

The only final-visibility transition is Linux
`renameat2(RENAME_NOREPLACE)`: an atomic descriptor-relative no-replace rename
from the exact owned attempt entry to `<report-id>.json` under the retained
reports descriptor. Successful rename atomically removes the attempt name and
creates the final name with the same inode. There is no fallback to ordinary
`rename()` or `os.replace()`. If Linux `renameat2(RENAME_NOREPLACE)` is absent or
unsupported, publication fails closed with a bounded error. An existing final
name is never overwritten.

Rename outcomes are classified separately. When the syscall returns success,
the publisher records that outcome, requires the attempt name to be absent,
requires the final to be the exact owned single-link inode and exact canonical
bytes, fsyncs both retained directories, reconstructs the report ID and denial
markers, and re-resolves the canonical output chain. A later verification
failure is a post-rename failure and never enters existing-report reuse.

Only a genuine `EEXIST` returned by the required no-replace syscall can enter
the concurrent-reuse path. The owned attempt name must still resolve to its
exact inode before and after complete read-only verification of the existing
final. A broad or injected exception is instead ambiguous. Ambiguous committed
success is allowed only when the attempt name is absent and the final name is
the exact owned attempt inode with exact bytes, identity, filename, authority
markers, and canonical reports parent. A byte-identical different-inode final
is not reuse and fails closed.

Production performs no name-based cleanup or rollback deletion. It does not
delete failed attempts, losing `EEXIST` attempts, substituted attempt names,
ambiguous finals, or a final after failed canonical verification. Failed and
losing attempts may therefore leave `.attempt-*.tmp` entries. They are
incomplete, noncanonical inputs: no reader, pointer, selector, publisher retry,
weekly path, or runtime component scans or promotes them. Automatic cleanup is
intentionally out of scope because safe ownership-preserving deletion requires
a separate design. Descriptor-close failures are bounded and no-throw after a
verified successful publication.

A call that fails after the atomic rename may leave a final-name entry. That
same operation never reclassifies a post-success verification failure as reuse.
A later, independent invocation can return reuse only by applying the complete
pre-existing-report verification from the beginning; report-ID equality alone
is never sufficient.

An existing `<report-id>.json` is reused read-only only after a descriptor-
relative no-follow open, regular-file and stable-read checks, exact validator
bytes and SHA-256, envelope/denial-marker validation, identity-v2 reconstruction,
filename/report-ID equality, and canonical parent identity verification. An
invalid or nonregular existing final fails closed without repair, overwrite,
deletion, quarantine, or alternative ID.

Concurrent same-ID publishers may create distinct attempt files, but at most
one completes the no-replace rename. A loser receiving genuine `EEXIST` performs
the same full existing-file verification and succeeds only for identical bytes;
its attempt remains orphaned and unconsumed. Different report IDs publish
independently. No process writes through another process's fd, overwrites a
final, deletes another attempt, or consumes partial attempt content.

The publisher proves canonical visibility at successful publication return. It
does not claim to prevent an actor with arbitrary filesystem mutation authority
from renaming an ancestor after the final canonical verification and successful
return; that is outside the publication operation's enforceable boundary.

Successful CLI display is a bounded report ID plus repository-relative path.
As with `replacement-render`, display occurs only after publication returns. A
write/flush failure makes programmatic `main()` return the private committed-
display sentinel; the real module process entrypoint converts only that
replacement-publication sentinel to exit zero. Legacy command behavior is not
changed.

## Source generation

The sole validation production API, `validate_generation_memo(generation_id)`,
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

The resulting validator-owned object and its exact published envelope have schema
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
`VALID` does not endorse it. R2F-1b-b does not restore free-form rationale or
create a candidate artifact.

The validation production entrypoint accepts only an exact generation ID. It
performs generation verification, memo capture, strict normalization, and
result construction before closing its local descriptor owner. It returns only a
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

## Runtime and authority boundary

The only production references are the explicit manual CLI dispatch, the
isolated `replacement_report.py` publisher, and the committed one-shot validator.
Availability, state, permissions, weekly/status, Steps 2–4, final safety,
publisher/quarantine paths, handoff/order compilation, broker code, and package
auto-entrypoints neither import nor read the report.

`NO_TRADE` remains only a research observation inside the validated envelope.
Publication does not set weekly state or claim universe admission, freshness,
availability, readiness, approval, gate success, final safety, budget, cap,
quantity, order, or broker submission. It creates no `NEW_BUY`, `SELL`, or
`ORDER_COMPILATION` permission. Runtime recognition, candidate projection, and
any actionable or order-capable integration remain separate future designs.
