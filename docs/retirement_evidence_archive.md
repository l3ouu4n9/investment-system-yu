# Step 1A retirement-evidence archive (Phase 2A / Phase 2B)

Offline, report-only infrastructure that copies **valid** per-run
`step1a_retirement_observation_v1` reports into an append-only archive after
strict, full-contract validation.

**This tool authorizes nothing.** It performs no aggregation, no coverage /
evidence-sufficiency evaluation, and reaches no retirement decision. It has no
runtime effect on Step 1 parsing, research availability, permissions, gates, the
order path, or broker/live execution, and is never imported by production
runtime code.

## Phase ordering

| Phase | Scope | Status |
|-------|-------|--------|
| **2A** | Archive contract + coordinated ingestion tool | **implemented here** |
| **2B-1** | Pure single-record verifier | **implemented here** |
| **2B-2** | Coordinated scanner / integrity index | **implemented here** |
| 2C | Versioned coverage-contract design/spec | not started |
| 2D | Provenance / schema amendment (only if 2C proves it required) | not started |
| 2E | Offline deterministic aggregator | not started |
| 2F | Post-audit + representative evidence collection | not started |

Nothing beyond the combined Phase 2A/2B integrity tooling is authorized.

## What the tool does

`python -m investment_orchestrator.offline.retirement_evidence.cli \
  --source <observation.json> --dest <archive-root> \
  --coordination-file <external-anchor> [--provenance <claim>]`

It reads **one** explicit source observation and **one** explicit destination
archive root, validates the source against the complete committed v1 contract
(exact key allowlists, field domains/enums, authority envelope, raw-content-safe
field domains, and recomputation of the composite fingerprint, coverage key, and
observation id), then appends exactly one archive record.

Exit codes: `0` accepted/quarantined (incl. duplicate no-op), `3` rejected
(reason-only record written; no payload), `2` layout/operator error. Any ordinary
error after an archive-visible initialization or publication syscall is reported
as deterministic `coordination_indeterminate_post_publication`; it never claims
that nothing was written and must not be retried automatically. Omission of the
coordination option is also exit `2` with machine-readable JSON, not argparse
usage output. Temporary cleanup is best effort: if a temporary archive file may
remain after a duplicate/no-op, rejected-record, layout, or publication path,
the result is partial/indeterminate rather than ordinary success.

The error JSON distinguishes the strongest code-owned state known at failure:
`no_visible_mutation`, `root_or_directory_initialization_started`,
`partial_initialization`, `layout_published`, `record_published`,
`cleanup_incomplete`, or `publication_outcome_indeterminate`. Separate booleans
preserve whether layout or record publication is known and whether cleanup is
incomplete. Exit code `2` alone never proves the archive was untouched.

The Phase 2B integrity index is run with:

`python -m investment_orchestrator.offline.retirement_evidence.verify_cli \
  --archive-root <archive-root> --coordination-file <external-anchor>`

It emits `retirement_archive_index_v1`. Its assessment is archive integrity
only; it performs no coverage, sufficiency, provenance, readiness, retirement,
fallback, permission, or order-path decision. When `--output` is a file path,
containment is checked against the archive root resolved by the coordinated
scan operation itself; the verifier does not trust a separate pre-scan root
resolution.

## Cooperative writer quiescence (`retirement_archive_coordination_v1`)

Every public Phase 2A/2B entry point requires an explicit, pre-existing
coordination anchor outside the resolved archive root. The exact anchor bytes
are `retirement_archive_coordination_v1` followed by one newline. The anchor
must be a direct regular non-symlink file with the code-owned link-count and
size policy. It is opened read-only/no-follow/close-on-exec and is never created
or modified by these tools. Missing, invalid, unsupported, incompatible,
interrupted, identity-changed, or contended coordination fails closed; there is
no default anchor and no uncoordinated mode.

The capability is an exact module-created, non-subclassable, non-copyable
object bound to its acquisition PID, resolved archive root, lock mode, and one
operation. Lower-level helpers cannot authorize work through duck typing or an
overridden validation method. After `fork`, the child invalidates and closes
its inherited descriptor without issuing `LOCK_UN`; close-on-exec prevents an
executed child from retaining it. A source located within the destination
archive tree is rejected by both lexical containment and resolved-path
containment checks, and source reading occurs inside the exclusive lease.
The current fork-lifecycle implementation requires Linux `/proc/self/fd` so an
open descriptor is discoverable even before its numeric fd is registered; a
platform without that facility fails coordination closed as unsupported.

The child guarantee begins when its registered child handler has completed.
Before that handler receives CPU time during an in-flight fork, the inherited
open-file description can transiently cause canonical nonblocking lock
contention even if the owner has exited. This is fail-closed availability
behavior: no scan or mutation proceeds, and callers may explicitly retry after
child cleanup and owner release. Immediate lock availability during that
interval is not guaranteed. The lifecycle callbacks are closure-owned and are
not exposed through the module API.

This is an operator-trusted local-process contract. Capability validation
protects supported repository APIs and ordinary direct misuse, including stale,
copied, reconstructed, wrong-root, wrong-mode, and wrong-operation handles. It
is not a sandbox against malicious same-interpreter code that rewrites module
functions or uses unrestricted reflection to alter closure-owned state.

Phase 2A holds a nonblocking exclusive advisory lease before any archive layout
or existence observation and through its final decision. It revalidates the
live descriptor, mode, anchor identity, and contract bytes immediately before
and after every no-overwrite publication link. Phase 2B holds a nonblocking
shared advisory lease from before root resolution through scanning, final
source revalidation, verification, grouping, assessment, semantic report
construction, and `report_content_sha256`. Multiple scanners may coexist;
writers exclude scanners and other writers. Serialization and output writing
may occur after the complete report and self-hash are finalized and the lease
is released.

A coordinated clean report proves only that no compliant Phase 2A writer using
the same coordination anchor acquired its exclusive writer lease during the
complete protected scan interval. It does not prove arbitrary external
filesystem quiescence, archive immutability, malicious-operator resistance,
cryptographic authenticity, legacy uncoordinated writer exclusion, protection
when different anchors are supplied, power-loss durability, permission,
readiness, sufficiency, retirement, or execution authority. The operator is
responsible for supplying the same anchor to every repository-owned tool that
accesses one archive.

The index reports the coordination contract, status, shared lock mode, narrow
scope, repository-writer quiescence result, and an always-false external-
filesystem-quiescence result. These semantic fields participate in the report
self-hash. No anchor path, device/inode identity, ownership, permissions,
timestamps, or raw OS error is reported. `archive_clean` is centrally gated on
the live shared capability bound to the complete scan; booleans, status strings,
closed leases, fabricated objects, and direct lower-level builder calls cannot
enable it. The scanner records code-owned completion facts for layout
validation, required partition inspection, bounded inventories, classification,
initial reads, final record revalidation, required identity checks, and manifest
population; empty warning/error sets alone are never sufficient for clean.

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
initialized with separately tracked root, layout, and partition operations; the
initialization sequence as a whole is not atomic. If the layout entry exists and
differs, is unreadable, or is not a direct regular file, ingestion fails closed
without writing an observation record. Any earlier directory/layout side effect
is reported by the mutation state rather than described as rolled back.

Existing archives and their layout version are not rewritten. They remain
byte-identical but require operator provisioning of the explicit external
coordination file before they can produce `archive_clean`. A new archive cannot
be initialized until Phase 2A has acquired the valid exclusive lease.

These are ordinary directories: **append-only by tool policy**, not technically
immutable. True immutability requires WORM / object-lock storage and is out of
scope. Records may be set read-only by the operator as an advisory signal only.
The current implementation follows a valid symlinked destination supplied by
the operator; the destination is assumed operator-trusted. The external anchor
itself may never be a symlink. Dangling or malformed destination structures fail
closed.

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
is never clobbered and two concurrent writers cannot overwrite each other. Temp
cleanup is best effort. A cleanup failure returns `cleanup_incomplete` or an
indeterminate state and never ordinary duplicate/publication success. No
partially written final record becomes visible, but a complete linked record or
temporary file may remain when the result explicitly reports that possibility.
The destination directory is not currently fsynced after linking, so
persistence across sudden power loss is not guaranteed.
Content-addressed filenames make identical content a deterministic duplicate
no-op; a same observation-id/different-content case is rejected as a conflict.

## Boundaries

The archive and its records are consumed by nothing in production. A static test
asserts no production module imports the offline package, and a runtime test
asserts a normal Step 1 parse creates no archive artifacts.
