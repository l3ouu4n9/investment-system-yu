# R2F-1a: immutable Step 1A render observation

R2F-1a is a manually invoked, frozen, report-only observation of the production
Step 1A grounding/evidence generation. It is not the authoritative Sunday
weekly workflow and does not replace legacy Step 1 research.

Run only:

```bash
PYTHONPATH=src uv run python -m investment_orchestrator.cli.run_step1 replacement-render
```

There is no `replacement-report` command in R2F-1a.

## Immutable generation

The command opens the repository root once and retains that descriptor from
source capture through publication. It opens `inputs/current` exactly once
relative to that repository descriptor and retains the common-parent descriptor
while reading the strategy settings, portfolio snapshot, research anchors, and
research-anchor approvals. Each final source is opened once with `O_NOFOLLOW`,
verified as the same regular entry inspected immediately before open, and read
only through that descriptor. Repository and common-parent device/inode values
exist only as transient in-memory race-detection state. They are not serialized,
hashed into semantic identity, or treated as research provenance. The manifest
records only the stable code-owned capture profile
`retained_repo_and_common_parent_v1`; that marker describes the method but is not
trusted as enforcement.

Before the logical commit point, the command re-resolves the expected repository
and `inputs/current` pathnames without following symlinks and requires them to
still identify the retained descriptors. This is a coherent descriptor-bound
bundle contract—one repository identity, one `inputs/current` identity, and one
stable descriptor read per source—not a claim of an atomic filesystem snapshot.
The command invokes the shared production Step 1A evidence core once and writes
one deterministic generation:

```text
artifacts/current/step1_research/r2f_report_only/
  generations/
    <full-sha256-generation-id>/
      replacement_input_manifest.json
      evidence_packet.json
      analyst_memo_prompt.txt
      analyst_memo_raw_output.txt
      render_generation_binding.json
```

The generation ID is the full lowercase SHA-256 of an explicit content-semantic
projection: supported schema/profile versions, repository-relative source roles
and paths, raw and production-normalized source hashes, decision-settings hash,
`as_of`, active-registry identity, evidence file/canonical hashes, and stable
authority markers. It excludes device/inode identities, absolute paths, modes,
filesystem times, process state, temporary/output identities, wall-clock time,
and memo content. Equivalent clean checkouts therefore produce byte-identical
manifests, evidence, prompts, bindings, and generation IDs. Different decision-
relevant source bytes produce a different directory. Existing immutable
generation files are never overwritten. An identical render verifies and reuses
a completed generation only when its inventory contains exactly the five
listed entries, all required entries are regular files, its binding and immutable
hashes verify, and `.render_in_progress` is absent. Missing bindings, retained
in-progress state, unexpected entries, and mismatched artifacts fail closed and
are not automatically deleted. No `latest`, `active`, `current`, runtime source,
or permission pointer is created.

Directory creation and file publication remain relative to the same repository
descriptor used for source capture. No pathname reopen selects a new repository
for prompt or output work. The memo template is captured once through that
descriptor before input processing; prompt construction later uses only the
captured text and in-memory evidence. Temporary files are created exclusively
inside the opened generation directory, written and fsynced through their
descriptors, and renamed descriptor-relatively.

Publication creates `.render_in_progress` before the first artifact. While that
marker remains, the command writes and fsyncs all files, fsyncs required parent
directories, verifies binding and immutable hashes, requires the exact
pre-commit inventory of the five final files plus the marker, and revalidates
repository/input-parent pathname identities. Removing `.render_in_progress` is
the sole and final logical commit operation. No verification, fsync, cleanup, or
recovery mutation follows a successful marker unlink. Every earlier failure
leaves the marker in place (or, if initial marker creation itself failed, leaves
no binding) and reuse rejects the generation as incomplete. R2F-1a never tries
to recreate a removed marker or invalidate a binding after commit.

Descriptor cleanup after marker unlink is best effort and explicitly no-throw,
including under non-`OSError` cleanup failures. The successful return object and
its bounded CLI display string are prepared before unlink, so cleanup cannot
convert a committed generation into reported publication failure.

The final marker unlink intentionally has no following directory `fsync`. After
a crash, a non-durable unlink may conservatively reappear and cause incomplete-
generation rejection. That false negative is safe: the protocol does not claim
stronger cross-filesystem crash durability, and a partial generation cannot
become falsely reusable through a post-commit recovery failure.

This durability and no-follow model requires POSIX directory descriptors,
`O_DIRECTORY`, `O_NOFOLLOW`, `O_CLOEXEC`, nonblocking type inspection, directory-
relative operations, and directory `fsync`. R2F-1a fails closed when those
primitives are unavailable; it does not claim equivalent durability on platforms
or filesystems that do not implement them.

The binding attests exact initial bytes for the blank
`analyst_memo_raw_output.txt`, while explicitly marking that file as
operator-editable after render. Once edited, the render witness does not claim
the current memo bytes are complete or validated.

CLI stdout is observability only for replacement-render. Publication validity is
determined before display: the replacement-only helper writes and explicitly
flushes the prepared bounded success text inside its protected boundary. If that
display fails, the helper returns a private committed-display-failure status; it
does not attempt stream redirection, emit fallback stderr, or mutate a committed
generation. Only the real `replacement-render` module process entrypoint turns
that status into immediate exit zero only after `replacement_render()` has
returned—its marker commit and no-throw publication cleanup are already
complete—avoiding interpreter shutdown retry of a permanently broken stdout
stream. Programmatic callers of `main()` receive the private status and are
never forcibly terminated. Normal display success uses ordinary interpreter
shutdown. This narrow post-commit exception does not alter any legacy Step 1
CLI branch, and no workflow, status command, or runtime consumer depends on the
display text.

## Exact production grounding parity

The path-based production Step 1A accessor and R2F-1a share one captured-input
core. Approval hash validation, revocations, approvals-inclusive registry
construction, readiness, active-registry selection, and evidence grounding use
the same existing production policy helpers. R2F-1a adds no alternative
grounding source or policy.

The evidence packet is built once with a deterministic `generated_at` derived
from the frozen `as_of`. Each manifest input records two distinct identities:

- `file_sha256` hashes the exact descriptor-read bytes, so LF, CRLF, and lone-CR
  representations remain distinct raw files.
- `production_text_sha256` hashes decoded text after the same universal-newline
  transformation as the production text reader (`CRLF` and lone `CR` become LF).

Settings, anchors, approvals, portfolio evidence, and semantic source hashes use
the production-equivalent text. Consequently newline representation alone does
not change grounding semantics, while raw-file identity remains exact. The
manifest and binding separately record file-byte and canonical-content hashes.

## Domain-valid versus invalid inputs

R2F-1a reuses the existing deterministic anchor, approval, and revocation
validators before publication. A completed manifest records
`DOMAIN_VALID_BUT_NONACTIVATING`: the inputs are structurally/domain valid, but
the observation still activates no runtime path or permission.

No generation is completed (`DOMAIN_INVALID_NO_GENERATION`) for duplicate or
malformed anchors/approvals, unsupported schemas, non-operator/LLM-authored
approvals, invalid required approval structures, or a production evidence
invariant/parity failure. Each of the four bounded source files is required and
must be nonblank; “no approvals” is represented by a valid approvals manifest
with an empty `approvals` list, not by a missing or blank file. Valid
non-activating cases remain observable with
bounded reason codes: no approvals, valid revocations, expired/inactive
approvals, operator-completed-anchor hash mismatch, an empty active registry,
and unavailable deterministic market/event feeds. These distinctions add no new
activation policy.

## Memo prompt boundary

R2F-1a renders a bounded prompt containing the generation ID, manifest and
evidence file/canonical hashes, exact `as_of`, allowed ticker universe, and
bounded evidence IDs. It prohibits permissions, budgets, quantities, orders,
execution, and universe creation.

R2F-1a does not parse the memo, compile a candidate, validate a candidate, or
emit coverage/compatibility reports. A future R2F-1b must define and independently
review an exact hash-bound memo envelope before it may consume the operator-edited
memo file.

## Explicit non-effects

R2F-1a does not read or write legacy Deep Research output, last-good research,
availability, degraded-mode decisions, active/effective pointers, weekly
outcomes, Step 2, Step 3, Step 4, quarantine, publication, final safety, order
compilation, SELL authority, broker output, or live execution.

Every R2F-1a-owned JSON artifact carries code-owned report-only and
non-authorization markers. No production workflow, validator, status command,
gate, package entrypoint, or broker path consumes an R2F-1a generation.

R2F-1b and all later R2F state-recognition or permission work remain blocked.
