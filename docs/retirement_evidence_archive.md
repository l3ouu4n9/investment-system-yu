# Step 1A retirement-evidence archive (Phase 2A)

Offline, report-only infrastructure that copies **valid** per-run
`step1a_retirement_observation_v1` reports into an append-only archive after
strict, full-contract validation.

**This tool authorizes nothing.** It performs no aggregation, no coverage /
evidence-sufficiency evaluation, and reaches no retirement decision. It has no
runtime effect on Step 1 parsing, research availability, permissions, gates, the
order path, or broker/live execution, and is never imported by production
runtime code.

## Phase ordering (only 2A is implemented)

| Phase | Scope | Status |
|-------|-------|--------|
| **2A** | Archive contract + ingestion tool | **implemented here** |
| 2B | Verifier / indexer | not started |
| 2C | Versioned coverage-contract design/spec | not started |
| 2D | Provenance / schema amendment (only if 2C proves it required) | not started |
| 2E | Offline deterministic aggregator | not started |
| 2F | Post-audit + representative evidence collection | not started |

Nothing beyond Phase 2A is authorized.

## What the tool does

`python -m investment_orchestrator.offline.retirement_evidence.cli \
  --source <observation.json> --dest <archive-root> [--provenance <claim>]`

It reads **one** explicit source observation and **one** explicit destination
archive root, validates the source against the complete committed v1 contract
(exact key allowlists, field domains/enums, authority envelope, raw-content-safe
field domains, and recomputation of the composite fingerprint, coverage key, and
observation id), then appends exactly one archive record.

Exit codes: `0` accepted/quarantined (incl. duplicate no-op), `3` rejected
(reason-only record written; no payload), `2` layout/operator error (nothing
written).

## Archive layout (`retirement_archive_layout_v1`)

```
<archive-root>/
  retirement_archive_layout_version     # must contain the layout version token
  accepted/     <record>.json           # complete, clean, recompute-verified
  quarantined/  <record>.json           # structurally valid + raw-safe, non-countable
  rejected/     <record>.json           # reason-only; never holds a payload
```

The archive root must live **outside** `artifacts/current`. A normal Step 1
parse never creates or writes it. If the layout-version file is absent it is
initialized safely; if it exists and differs or is malformed, ingestion fails
closed without writing any observation record.

These are ordinary directories: **append-only by tool policy**, not technically
immutable. True immutability requires WORM / object-lock storage and is out of
scope. Records may be set read-only by the operator as an advisory signal only.
The current implementation follows a valid symlinked destination supplied by
the operator; the destination is assumed operator-trusted. Dangling or malformed
destination structures fail closed, although their exact exception wrapping may
vary.

## Decision table

* **accepted** — recognized schema; complete observation (empty
  missing/malformed/blocker/inconsistency collections); clean known code
  identity (`git_state == "clean"`, valid commit, `code_version_usable_for_evidence`);
  valid + recomputable observation id, coverage key, and composite fingerprint;
  full authority envelope; no unknown/unsafe fields.
* **quarantined** — structurally valid and raw-safe, but non-countable:
  incomplete, dirty/unavailable code identity, or the minimal builder-internal
  error observation. The payload is preserved unchanged (no repair) with exact
  canonical reason tokens.
* **rejected** — malformed JSON, unknown/unsafe schema or unexpected keys,
  authority-envelope violation, hash/id recomputation mismatch, raw-content-unsafe
  field, or observation-id/content conflict. Rejected records embed **no**
  payload and **no** raw parser/error text — only the source basename, the exact
  input-file SHA-256, canonical reason tokens, and the ingestion timestamp.

## Archive record envelope (`retirement_archive_record_v1`)

A single versioned file wraps each accepted/quarantined observation. The parsed
observation mapping is preserved **without semantic repair or field mutation**;
this is the parsed mapping, not the original JSON bytes.

Hash semantics:

* `source_file_sha256` — SHA-256 of the **exact source bytes** at ingestion;
  audit metadata only; **not** claimed to be recomputable from the parsed payload.
* `source_canonical_payload_sha256` — SHA-256 of the canonical compact-sorted
  JSON of the payload; recomputable by a verifier; the payload identity / dedup key.
* `archive_record_content_sha256` — SHA-256 of the canonical envelope **excluding
  this field**; recomputable from the record; detects accidental record modification.

These hashes prove **content identity, not trusted authorship**. An attacker who
can rewrite both the record and its hashes is not prevented; no signing /
attestation is invented (out of scope).

## Provenance

`--provenance` records an **unverified operator/CI claim** only
(`real_current`, `isolated_production_path`, `integration_test`, `unit_test`,
`fault_injection`, `unspecified`; default `unspecified`). The record stores
`claimed_evidence_provenance`, `provenance_claim_source`, and
`provenance_verified: false`. Phase 2A never verifies the claim, never infers it
from path/filename/content, and never evaluates whether it satisfies any future
coverage class.

## Tool identity

`archive_tool_version` is a code-owned constant; there is **no** CLI option to
set it. A best-effort clean commit (`archive_tool_commit`) is resolved
offline and reported truthfully as `"unavailable"` when git does not resolve or
the tree is dirty. Tests inject a fixed identity through an internal parameter.

## No-overwrite / atomicity

Records are written by fully writing a temp file in the destination directory,
fsyncing the file, then atomically hard-linking it into place. `os.link` raises
rather than overwriting, so atomic no-overwrite is provided: an existing record
is never clobbered and two concurrent writers cannot overwrite each other. The
temp file is always removed and no partial or overwritten final record can
become visible. The destination directory is not currently fsynced after
linking, so persistence across sudden power loss is not guaranteed.
Content-addressed filenames make identical content a deterministic duplicate
no-op; a same observation-id/different-content case is rejected as a conflict.

## Boundaries

The archive and its records are consumed by nothing in production. A static test
asserts no production module imports the offline package, and a runtime test
asserts a normal Step 1 parse creates no archive artifacts.
