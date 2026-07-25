# WEEKLY-SHADOW-01 Publication-State Evidence Observer Design

## 1. Purpose

This design specifies a future explicitly operator-invoked,
read-only, report-only evidence observer.

It is not an ambiguity resolver.

It does not publish, repair, retry, activate, authorize, or make
a report available to any investment stage.

**No implementation currently exists.** No module, CLI, entry point, schema, or
test in this repository implements any part of this design. This document
specifies architecture only; it does not approve implementation. Source work is
gated on §20 (LTETF determination) and, for the authenticated layers, on §19.

The observer exists to answer one narrow question after a WS01e run exits 4
(`WS01_BR_PUBLICATION_AMBIGUOUS`): *what does authenticated, read-only evidence
show about the state of the explicit output root?* Its correct answer is
frequently "less than you want", and the design is built so that it can say so
without overstating.

## 2. Controlling invariants

```text
current-context reconstruction match
!=
authenticated historical generation binding
!=
authenticated response binding
!=
exit-4 resolution
```

```text
filesystem observation
!= investment authority
```

These are four distinct claims of strictly increasing strength. No status, axis
value, output field, or exit code may collapse one into the next. Each
escalation requires an input class that the current design does not accept.

No current status, axis value, output field, or exit code may:

```text
close exit 4
prove the ambiguous response was published
prove non-publication
create availability or freshness
change state or permission
pass a gate
create an order
authorize execution
```

## 3. Observed publication layout

The observer reads a namespace produced by WS01d. The relevant committed facts,
restated here as design inputs:

| Object | Name | Mode |
|---|---|---|
| published namespace | `<output_root>/reports/` | `0o700` |
| published set | `<output_root>/reports/<report_identity_sha256>/` | `0o700` |
| report artifact | `weekly_shadow_01_analyst_report.json` | `0o600` |
| run-summary artifact | `weekly_shadow_01_run_summary.json` | `0o600` |
| staging namespace | `<output_root>/report_attempts/` | `0o700` |
| staging directory | `.attempt-<32 lowercase hex>.tmp` | `0o700` |

A published set is created only by `renameat2(RENAME_NOREPLACE)` of a staging
directory, so the published directory and the attempt directory are the same
inode. The publisher contains **no cleanup path** — no `unlink`, `rmdir`,
`rmtree`, or `O_TRUNC` — so a staging directory left by a run that failed before
or during the rename persists indefinitely with complete, byte-verified
contents.

The report payload carries exactly four binding fields:

| Field | Derived from | Binds |
|---|---|---|
| `run_id` | adapter identity, source generation identity and version, source-bound evaluation timestamp, contract catalog | generation + repository |
| `input_package_identity_sha256` | full input package payload | generation + repository |
| `response_capture_identity_sha256` | package identity + response hash + byte size | + response |
| `validation_identity_sha256` | package + capture | + response |

The first two are response-free; the last two are response-bound. All four are
covered by `report_identity_sha256`, so none can be altered independently
without breaking self-consistency.

## 4. Definition: published namespace

```text
published namespace
=
the authenticated WS01d reports/ namespace under the explicit output root
```

Presence in that namespace is a **location fact**. It does not prove:

```text
ambiguous-invocation attribution
report activation
availability
freshness
permission
exit-4 resolution
```

Wherever this document says a set is "in the published namespace", it means only
that the set was found under an L0-authenticated `reports/` directory beneath the
operator-supplied absolute output root.

## 5. Evidence layers

| Layer | Proves | Inputs | Drift exposure |
|---|---|---|---|
| **L0** structural authentication | authenticated root chain, non-symlinked ancestry, directory and file types, exactly the two expected filenames, complete stable reads | output root only | none |
| **L1** artifact self-consistency | recomputed report and run-summary identities equal their embedded values; recomputed report identity equals the containing directory name; canonical re-serialization reproduces the bytes; cross-artifact agreement on `report_identity_sha256` and `run_id`; `negative_authority_profile` exactly as frozen | artifact bytes only | **none** |
| **L2** current-context binding | embedded `run_id` and `input_package_identity_sha256` equal values reconstructed from (generation ID, repository root) **as the repository exists at inspection time** | + repository | yes, **unauthenticated** |

An **L0/L1-valid candidate** is a directory under the published namespace whose
name matches `^[0-9a-f]{64}$` and which satisfies every L0 and L1 predicate.

A layer that is not built or not requested is reported as `not_evaluated`. A
layer that is requested but cannot be computed is reported according to the
frozen predicates and derivation rules in §7.1 and §10. Neither condition is
reported as absence.

## 6. Finalized observation-status vocabulary

Exactly nine values, frozen:

```text
SELF_CONSISTENT_SET_OBSERVED_IN_PUBLISHED_NAMESPACE
MULTIPLE_SELF_CONSISTENT_SETS_OBSERVED
CANDIDATE_SET_INCONSISTENT
CANDIDATE_SET_INCOMPLETE
ATTEMPT_EVIDENCE_ONLY_OBSERVED
NO_CANDIDATE_SET_OBSERVED
EVIDENCE_UNSTABLE
ROOT_UNAUTHENTICATED
INSPECTION_CONTRACT_FAILURE
```

`SELF_CONSISTENT_SET_OBSERVED_IN_PUBLISHED_NAMESPACE` is the **only** accepted
singular self-consistent-set token. The qualifier is not decoration: it scopes
`published` to a namespace location rather than to an outcome, so the token
cannot be read as proven publication of the ambiguous invocation when it is
quoted alone in an operator record.

For `MULTIPLE_SELF_CONSISTENT_SETS_OBSERVED`: all observed sets are in that same
authenticated published namespace. The shorter name carries no authority-bearing
term and needs no qualifier, but its scope is identical to the singular token's.

### 6.1 Prohibited names

The following strings are retired. They must not appear in any implementation,
output, test fixture, operator record, or future revision of this document:

```text
SELF_CONSISTENT_PUBLISHED_SET_OBSERVED
VERIFIED_GENERATION_BOUND_SET_OBSERVED
VERIFIED_PUBLISHED_SET_PRESENT
```

They are prohibited, not alternative spellings. `VERIFIED_PUBLISHED_SET_PRESENT`
and `VERIFIED_GENERATION_BOUND_SET_OBSERVED` asserted publication or generation
binding on evidence that supports neither. `SELF_CONSISTENT_PUBLISHED_SET_OBSERVED`
left `published` unscoped. Also prohibited in names, tokens, fields, and
headings: `resolver`, `resolved` other than as the reserved `exit4_ambiguity`
value, `successful`, `safe`, `confirmed`, `complete`, `ok`, and
`verified publication`.

### 6.2 Status predicates

| Status | Predicate |
|---|---|
| `SELF_CONSISTENT_SET_OBSERVED_IN_PUBLISHED_NAMESPACE` | exactly one L0/L1-valid candidate observed |
| `MULTIPLE_SELF_CONSISTENT_SETS_OBSERVED` | two or more L0/L1-valid candidates observed |
| `CANDIDATE_SET_INCONSISTENT` | at least one candidate opened cleanly but failed L1: recomputed report identity differs from the directory name or from the embedded value, cross-artifact disagreement on `report_identity_sha256` or `run_id`, `negative_authority_profile` not exactly as frozen, canonical re-serialization not reproducing the bytes, or an unauthorized third artifact |
| `CANDIDATE_SET_INCOMPLETE` | at least one candidate is missing or cannot read one member of the required pair |
| `ATTEMPT_EVIDENCE_ONLY_OBSERVED` | no L0/L1-valid candidate; attempt evidence present |
| `NO_CANDIDATE_SET_OBSERVED` | no L0/L1-valid candidate and no attempt evidence, in a stable snapshot |
| `EVIDENCE_UNSTABLE` | any divergence between the two complete passes |
| `ROOT_UNAUTHENTICATED` | the output-root chain could not be authenticated; no candidate was inspected |
| `INSPECTION_CONTRACT_FAILURE` | the observer's own invariants failed |

`observation_status` describes filesystem and artifact evidence only. No token
mentions binding, generation, response, or invocation.
`INSPECTION_CONTRACT_FAILURE` is a deliberate exception: it is not an evidence
claim at all, but the observer refusing to make one.

## 7. Orthogonal evidence axes

One invocation emits exactly one value for `observation_status` and exactly one
value for each of the five fields below.

```text
current_context_binding:
  match
  no_match
  indeterminate
  not_evaluated

current_context_binding_detail:
  not_applicable
  reconstruction_unavailable
  multiple_matching_candidates

historical_generation_binding:
  authenticated_match
  authenticated_no_match
  not_proven
  not_evaluated

response_binding:
  authenticated_match
  authenticated_no_match
  not_proven
  not_evaluated

exit4_ambiguity:
  not_resolved
  resolved
```

### 7.1 Exact value predicates

| Field and value | Predicate |
|---|---|
| `current_context_binding=match` | exactly one L0/L1-valid candidate's embedded `run_id` **and** `input_package_identity_sha256` equal the reconstruction computed from (generation ID, repository root) at inspection time |
| `=no_match` | the reconstruction was computed and **zero** L0/L1-valid candidates agree with it |
| `=indeterminate` | the reconstruction could not be computed, **or** two or more L0/L1-valid candidates agree with it |
| `=not_evaluated` | the axis was not reached: a Stage 0–3 status, no comparable candidate, or L2 not built or not requested |
| `current_context_binding_detail=not_applicable` | no further explanation applies |
| `=reconstruction_unavailable` | the axis is `indeterminate` because the reconstruction could not be computed |
| `=multiple_matching_candidates` | the axis is `indeterminate` because two or more valid candidates agree |
| `historical_generation_binding=authenticated_match` | a **trusted pre-run recorded** `run_id` **and** `input_package_identity_sha256` equal a candidate's embedded values |
| `=authenticated_no_match` | trusted pre-run recorded bindings exist and no candidate matches them |
| `=not_proven` | the claim cannot be established from available inputs |
| `=not_evaluated` | authenticated bindings exist but were not supplied |
| `response_binding=authenticated_match` | separately approved authenticated response bindings equal a candidate's embedded values |
| `=authenticated_no_match` | such bindings exist and no candidate matches them |
| `=not_proven` | the claim cannot be established from available inputs |
| `=not_evaluated` | such bindings exist but were not supplied |
| `exit4_ambiguity=resolved` | an authenticated expected publication result exists **and** exactly one L0/L1-valid candidate matches all required bindings |
| `=not_resolved` | anything else |

### 7.2 Axis scope

- `observation_status` describes filesystem and artifact evidence only.
- `current_context_binding` compares with repository state at inspection time
  only. No value on this axis carries a historical or temporal claim.
- `historical_generation_binding` requires trusted pre-run bindings. A
  reconstruction can never produce an `authenticated_*` value.
- `response_binding` requires separately approved authenticated response
  bindings.
- `exit4_ambiguity=resolved` requires an authenticated expected publication
  result and exactly one matching candidate.

`current_context_binding_detail` is subordinate: it explains an axis value and
never changes or strengthens one.

## 8. Current reachability

For every current historical-run observer result, frozen:

```text
historical_generation_binding=not_proven
response_binding=not_proven
exit4_ambiguity=not_resolved
```

For the L0/L1-only implementation (PR 2):

```text
current_context_binding=not_evaluated
```

For future unauthenticated current-context L2 (PR 3), permitted only:

```text
match
no_match
indeterminate
not_evaluated
```

Unreachable in the current design:

```text
historical_generation_binding=authenticated_match
historical_generation_binding=authenticated_no_match
response_binding=authenticated_match
response_binding=authenticated_no_match
exit4_ambiguity=resolved
```

Exit `0` is likewise unreachable (§12).

Three of the five fields are therefore constants across every reachable result.
This is an invariant, not a disclaimer: an implementation must assert at source
level that no code path, helper, or output compiler assigns any of the five
unreachable values, and that the strings `authenticated_match`,
`authenticated_no_match`, and `resolved` appear in no status-producing path.

## 9. Deterministic classifier

Frozen precedence. The first matching stage fixes the primary status.

```text
Stage 0  observer invariant failure
         -> INSPECTION_CONTRACT_FAILURE

Stage 1  output-root authentication failure
         -> ROOT_UNAUTHENTICATED

Stage 2  any divergence across the two complete passes
         -> EVIDENCE_UNSTABLE

Stage 3  candidate anomalies
  3a     any inconsistent candidate
         -> CANDIDATE_SET_INCONSISTENT
  3b     otherwise, any incomplete candidate
         -> CANDIDATE_SET_INCOMPLETE

Stage 4  clean population
  4a     zero valid candidates, zero attempts
         -> NO_CANDIDATE_SET_OBSERVED
  4b     zero valid candidates, attempts present
         -> ATTEMPT_EVIDENCE_ONLY_OBSERVED
  4c     exactly one valid candidate
         -> SELF_CONSISTENT_SET_OBSERVED_IN_PUBLISHED_NAMESPACE
  4d     two or more valid candidates
         -> MULTIPLE_SELF_CONSISTENT_SETS_OBSERVED

Stage 5  derive current-context binding only
```

Explicit properties:

- The first matching stage fixes the primary status; later stages cannot revise
  it.
- Stages 0–3 prevent binding evaluation entirely. When the status is any
  Stage 0–3 value, `current_context_binding=not_evaluated`,
  `current_context_binding_detail=not_applicable`, and the three constants of §8
  apply. An anomalous root is escalated whole; no partial binding interpretation
  is offered even when a valid subset coexists with an anomaly.
- Counts may coexist with one primary status. Precedence chooses the headline
  and never suppresses a count.
- Inconsistency dominates incompleteness. A content-addressing violation is an
  integrity anomaly that must not be buried behind a plausibly interrupted
  publication. Both counts are emitted either way.
- Instability dominates all content conclusions.
- Root authentication failure prevents enumeration. It cannot overlap
  `EVIDENCE_UNSTABLE`: unauthenticated means never opened, unstable means opened
  and then changed.

Totality: Stages 0–2 cover the terminal classes, Stage 3 covers both anomaly
classes, and Stage 4 partitions on (valid-candidate count ∈ {0, 1, ≥2}) ×
(attempt presence), which is exhaustive. No snapshot escapes classification, and
each stage predicate is a pure function of the single stable snapshot, so two
implementations cannot select different primary statuses for the same snapshot.

## 10. Binding derivation

Frozen:

```text
Stage 4a or 4b:
  current_context_binding=not_evaluated
  current_context_binding_detail=not_applicable

L2 layer not built or not requested:
  current_context_binding=not_evaluated
  current_context_binding_detail=not_applicable

L2 reconstruction unavailable:
  current_context_binding=indeterminate
  current_context_binding_detail=reconstruction_unavailable

exactly one valid candidate matches:
  current_context_binding=match
  current_context_binding_detail=not_applicable

zero valid candidates match:
  current_context_binding=no_match
  current_context_binding_detail=not_applicable

two or more valid candidates match:
  current_context_binding=indeterminate
  current_context_binding_detail=multiple_matching_candidates
```

`no_match` means only:

```text
no L0/L1-valid candidate matched the package reconstructed
from the repository as it existed at inspection time
```

It does not prove historical mismatch, non-publication, wrong response, wrong
generation, or wrong output root. No current-context value is copied,
translated, or promoted onto the historical axis; `authenticated_no_match` is
unreachable by construction (§8), so there is no path by which a mismatch could
become a historical claim.

## 11. Scenario table

Every row is a current result. `HGB` = `historical_generation_binding`,
`RB` = `response_binding`, `A4` = `exit4_ambiguity`.

| # | Scenario | `observation_status` | `current_context_binding` | `..._detail` | HGB | RB | A4 | Exit |
|---|---|---|---|---|---|---|---|---|
| 1 | one valid + one inconsistent | `CANDIDATE_SET_INCONSISTENT` | `not_evaluated` | `not_applicable` | `not_proven` | `not_proven` | `not_resolved` | 21 |
| 2 | one valid + one incomplete | `CANDIDATE_SET_INCOMPLETE` | `not_evaluated` | `not_applicable` | `not_proven` | `not_proven` | `not_resolved` | 21 |
| 3 | inconsistent + incomplete | `CANDIDATE_SET_INCONSISTENT` | `not_evaluated` | `not_applicable` | `not_proven` | `not_proven` | `not_resolved` | 21 |
| 4 | multiple valid, zero matches | `MULTIPLE_SELF_CONSISTENT_SETS_OBSERVED` | `no_match` | `not_applicable` | `not_proven` | `not_proven` | `not_resolved` | 21 |
| 5 | multiple valid, exactly one match | `MULTIPLE_SELF_CONSISTENT_SETS_OBSERVED` | `match` | `not_applicable` | `not_proven` | `not_proven` | `not_resolved` | 20 |
| 6 | multiple valid, two or more matches | `MULTIPLE_SELF_CONSISTENT_SETS_OBSERVED` | `indeterminate` | `multiple_matching_candidates` | `not_proven` | `not_proven` | `not_resolved` | 21 |
| 7 | no valid, attempts present | `ATTEMPT_EVIDENCE_ONLY_OBSERVED` | `not_evaluated` | `not_applicable` | `not_proven` | `not_proven` | `not_resolved` | 20 |
| 8 | no valid, no attempts | `NO_CANDIDATE_SET_OBSERVED` | `not_evaluated` | `not_applicable` | `not_proven` | `not_proven` | `not_resolved` | 20 |
| 9 | directory appears between passes | `EVIDENCE_UNSTABLE` | `not_evaluated` | `not_applicable` | `not_proven` | `not_proven` | `not_resolved` | 21 |
| 10 | root authentication failure before enumeration | `ROOT_UNAUTHENTICATED` | `not_evaluated` | `not_applicable` | `not_proven` | `not_proven` | `not_resolved` | 23 |

Rows 1–3 show that a mixed population is escalated whole, with no partial
binding interpretation. Rows 4–6 show that the binding axis, not the status,
carries the uniqueness requirement — which is what keeps the axes orthogonal.

## 12. Exit-code contract

```text
0   reserved and unreachable in the current design
20  stable evidence observation completed; ambiguity not resolved
21  conflicting, incomplete, indeterminate, or unstable evidence
22  operator usage error
23  output-root authentication failure
24  internal observer invariant failure
```

Total mapping over every reachable combination:

| Combination | Exit |
|---|---|
| Stage 4 status **and** `current_context_binding` ∈ {`match`, `not_evaluated`} | 20 |
| Stage 4 status **and** `current_context_binding` ∈ {`no_match`, `indeterminate`} | 21 |
| `EVIDENCE_UNSTABLE`, `CANDIDATE_SET_INCONSISTENT`, `CANDIDATE_SET_INCOMPLETE` | 21 |
| operator usage error | 22 |
| `ROOT_UNAUTHENTICATED` | 23 |
| `INSPECTION_CONTRACT_FAILURE` | 24 |

Required properties:

- No current combination exits 0.
- **Exit 20 is not publication success.** It means a stable observation
  completed and resolved nothing.
- Current-context `no_match` and `indeterminate` map to 21. A non-matching or
  ambiguous population is the cause-ambiguous condition of §18 and must not be
  reported under the same code as a clean single match.
- Root authentication maps only to 23, never to 20 or 21.
- Contract failure maps only to 24.
- The exit code never replaces or suppresses the evidence axes.

The observer therefore never exits 0. This is intentional: automation that
treats non-zero as failure fails closed.

## 13. Mandatory output

Every non-usage output must contain all six fields:

```text
observation_status
current_context_binding
current_context_binding_detail
historical_generation_binding
response_binding
exit4_ambiguity
```

Every current result must contain:

```text
historical_generation_binding=not_proven
response_binding=not_proven
exit4_ambiguity=not_resolved
```

No mode, renderer, verbosity option, compact form, or error path may suppress
the mandatory fields. An implementation must enforce this over every output
path.

Permitted additional facts: bounded counts (`l0l1_valid_candidate_count`,
`inconsistent_candidate_count`, `incomplete_candidate_count`,
`current_context_matching_candidate_count`, `attempt_evidence_count`), relative
candidate paths of the form `reports/<report_identity_sha256>`, recomputed
report and run-summary identities, the two expected artifact filenames, and
explicit per-directory truncation flags. Bounds must be explicit and truncation
must be reported; silent capping is prohibited.

## 14. Privacy

Prohibited in all output, logs, records, and diagnostics:

```text
raw response bytes
raw response base64
response excerpts
report body
run-summary body
validated analyst content
absolute sensitive paths
attempt directory names
temporary names
stack traces
exception messages
credentials
provider information
```

Also prohibited: the phrases `trade allowed`, `report active`, `fresh`,
`available`, `permission granted`, `gate passed`, and `order ready`.

**Recorded hazard.** The public response validator returns a structure whose
`response_capture` mapping contains `raw_response_base64` — the entire raw
analyst response. Any future component that calls it may read only separately
approved identity strings and must never serialize, log, emit, or retain the raw
response-bearing mappings. This constraint applies to `analyst_response`,
`response_capture`, and `response_validation` alike, and must be enforced by
test.

## 15. Operator actions

For every currently reachable result, blocked:

```text
closing exit 4 as resolved
declaring the ambiguous response published
declaring non-publication
declaring that no publication action is necessary
automatic or implied WS01e invocation
cleanup or repair
attempt promotion or artifact copying
pointer creation
availability or freshness
state, permission, gate, portfolio, order, broker, or execution effects
receipt interpretation
```

Allowed, and only these:

```text
record evidence in the restricted operator record
preserve filesystem state
escalate
perform another explicit observation under approved conditions
```

Because `exit4_ambiguity=not_resolved` is a constant, **the allowed action set
is identical for every reachable result and does not change when
`current_context_binding=match`.** Only the recorded detail and the escalation
reason differ. Any later WS01e invocation remains a separately reviewed,
separately recorded operator decision, never implied by any axis value.

A second observation is permitted only under operator-controlled conditions:
after `ROOT_UNAUTHENTICATED` or a usage error once arguments are corrected;
after `EVIDENCE_UNSTABLE` once the operator has established that no writer is
active, recording both results; or after `indeterminate` with
`reconstruction_unavailable` once the repository root is confirmed. No internal
retry loop, no polling, no watch mode, and no invocation from WS01e.

## 16. Read-only boundary

The future observer must be physically read-only.

Required:

- descriptor-relative traversal from `/`, component by component;
- no-follow opens at every component;
- rejection of symlinked ancestors and leaves;
- regular-file and directory type checks;
- two complete identical passes;
- descriptor and named-entry witness agreement, before and after every read,
  over device, inode, mode, link count, size, mtime, and ctime;
- directory inventories captured and compared across passes;
- bounded traversal with explicit truncation reporting;
- no adoption of a later snapshot — any divergence is `EVIDENCE_UNSTABLE`,
  including a published set that appears mid-observation;
- explicit absolute output root; no CWD-relative resolution; no glob, newest,
  latest, current, active, or unbounded recursive scan.

Access-time disclosure: `atime` may change depending on the filesystem and mount
options. `O_NOATIME` is not portable and is not used. `atime` is non-semantic
and must never be used as evidence. The corresponding test obligation is "no
change to any semantic attribute" — type, mode, size, mtime, ctime, device,
inode, link count, and directory inventories — not an unprovable claim of zero
side effects.

Prohibited at the implementation boundary:

```text
O_CREAT
O_TRUNC
O_WRONLY
O_RDWR
O_APPEND
O_TMPFILE
mkdir
rename or renameat or renameat2
unlink or unlinkat
rmdir
symlink or link
mknod
truncate or ftruncate
chmod or chown
utime or utimensat
setxattr
locking
fsync or fdatasync
temporary files
lock files
cleanup
repair
publisher imports
publication calls
```

Permitted: `openat` with read-only, directory, no-follow, and close-on-exec
flags; `fstat`; `fstatat` with no-follow; `lseek`; `read`; `listdir` on a
directory descriptor; `close`.

## 17. Attempt and negative evidence

- Attempt names are staging names, not identities. They are random hex tokens
  with no relation to any identity, generation, or run.
- Complete attempts do not prove publication. Because the publisher has no
  cleanup path, a fully staged, byte-verified attempt persists indefinitely
  after a run that never published.
- Attempts must never be promoted, copied, renamed, repaired, or cleaned.
- Absence of attempts proves nothing. It is consistent with success, with
  failure before staging, with a different output root, and with external
  removal.
- Absence of a valid set proves neither non-publication nor a correct output
  root.
- **No current observer result may claim non-publication.** A proof of
  non-publication would require an authenticated expected identity, an
  authenticated stable enumeration, proof that the inspected root is the same
  filesystem object the ambiguous run used, and proof of exclusivity over the
  whole interval. The last is unprovable by any read-only observer and the third
  is unrecorded.

## 18. Historical capability limits

```text
For every exit-4 run already performed, authenticated historical
generation binding and response binding are unavailable.

Existing historical exit-4 ambiguity cannot be resolved by the
current read-only observer design.
```

This is the central limitation of the design, not a footnote.

- Current repository reconstruction is drift-exposed. The input-package builder
  succeeds against a drifted repository and returns a different identity rather
  than an error, and the WS01 chain uses no wall clock, so drift never announces
  itself.
- A reconstruction mismatch cannot distinguish repository drift, a wrong
  repository root, changed generation state, an unrelated candidate, candidate
  corruption, or an operator-context error.
- Retained response bytes alone do not authenticate historical package context.
  The response-bound identities are derived from the package payload identity as
  well as the response, so their derivation is drift-exposed in exactly the same
  way. The obstacle is not a missing public builder; it is unauthenticated
  drift.
- Exact expected-publication proof requires separately approved pre-run recorded
  bindings (§19).

Nothing in this design should be read as promising that a future observer will
resolve an exit-4 event that has already occurred without such a record.

## 19. Future authenticated bindings

Future, unreachable design only. Nothing in this section may be implemented
under this document.

Authenticated historical generation binding requires trusted pre-run:

```text
run_id
input_package_identity_sha256
```

Authenticated response binding additionally requires separately approved:

```text
response_capture_identity_sha256
validation_identity_sha256
```

Only when the required bindings are authenticated **and** exactly one L0/L1-valid
candidate matches all of them may a future design consider:

```text
exit4_ambiguity=resolved
exit 0
```

Such a result would prove that the ambiguous invocation's **expected publication
result** is present. It would not prove authorship. Authorship is undefined for
input-identical invocations, because publication is content-addressed and
`RENAME_NOREPLACE`-idempotent: two invocations with the same generation,
repository state, and response bytes produce byte-identical artifacts at the
same path by design.

Where pre-run bindings are recorded is itself a separate decision. Recording
them inside WS01e is not proposed: it would add a pre-flight step and a new
failure mode to the publication path. A separate, explicitly operator-invoked
read-only context command writing into the existing restricted operator record
is the shape to evaluate.

## 20. LTETF hard prerequisite

**PR 1 is a hard gate before any source implementation.**

The LTETF observer builds a production inventory that includes a field named
`dynamic_findings`. This is a repository-wide, zero-tolerance gate: it aggregates
every finding from every production module and may abort inventory construction
before isolation validation runs. A separate isolation validator additionally
requires an empty `report_artifact_readers`, membership of
`observer_external_consumers` in a closed path-exact declared set, and an empty
`prohibited_observer_capability_imports`.

PR 1 must trace:

```text
raw_relevant
classify_path
_call_is_benign_transform
_safe_callable_alias_at_call
_bound_literal_path_expression
```

and determine whether descriptor-relative WS01 artifact observation can exist
without producing:

```text
report_artifact_readers
undeclared observer_external_consumers
dynamic_findings
prohibited_observer_capability_imports
```

The following are **not approved** and must not be used to make WS01f fit:

```text
allowlist expansion
detector weakening
finding suppression
literal renaming or relocation performed to evade detection
```

Descriptor-relative traversal is independently mandated by §16 for
no-CWD, no-follow, race-resistant access; its convergence with detector
behaviour is incidental and not evasive. Avoiding a particular string literal is
legitimate only if the module has no genuine need for that literal; renaming,
splitting, or relocating a needed literal to change classification is evasion
and is prohibited.

**PR 1 may legitimately conclude that WS01f cannot proceed as a production
module.** That is an acceptable outcome of the gate, not a failure of it.

## 21. Formal PR sequence

```text
PR 0  formal corrected design document
PR 1  LTETF classification determination
PR 2  L0/L1 read-only evidence library and tests
PR 3  current-context L2 only
PR 4  authenticated historical L2 only after pre-run-binding approval
PR 5  explicit operator CLI, if justified
PR 6  operator runbook update
```

PR 2 ships only the drift-immune layers, with `current_context_binding` fixed at
`not_evaluated`. PR 3 widens exactly one axis and nothing else. PR 4 is the first
PR permitted to emit an `authenticated_*` value, and only after the pre-run
binding decision of §19. Response binding and exit-4 resolution remain a
separate future design.

No PR may combine evidence observation with:

```text
state recognition
permission changes
workflow integration
automatic retry
publication repair
pointer creation
downstream consumption
orders
broker or live execution
```

## 22. Integration boundary

The future observer must remain explicitly operator-invoked; report-only; not a
packaging entry point unless separately approved; not called by the weekly
orchestrator; not called automatically after exit 4; not consumed by state,
permission, gate, or order code; not a pointer writer; and not a publication
repair tool.

It must not import the publisher at all, so that no code path can reach the
publication API. Shared values — artifact filenames, directory names, identity
domains, byte bounds — must be restated in the observer and pinned to the
committed contract by focused tests, as WS01e already pins its own byte bound.

Its output is ephemeral. It creates no artifact, no persisted record, and no new
identity domain. Persisting a result would create something mistakable for a
receipt and would require the observer to hold a write path.

## 23. State and authority effects

```text
HOLD                         unchanged
NO_TRADE                     unchanged
SELL                         unchanged
NEW_BUY                      unchanged
ORDER_COMPILATION            unchanged
Steps 2–4                    unchanged
availability/freshness       unchanged
permissions                  unchanged
canonical publication        unchanged
report-only publication      evidence observed only
final gate                   unchanged
manual-order path            unchanged
broker/live execution        absent
```

No axis value, axis combination, exit code, or output field creates
availability, freshness, permission, gate passage, order readiness, quantity,
order, or execution authority.

## 24. Compatibility

This documentation-only design changes no:

```text
WS01 schema
WS01 reason code
report identity
run-summary identity
receipt
publisher API
validator API
LTETF detector
LTETF allowlist
state
permission
gate
order path
```

The publisher public surface remains exactly one function, and nothing in this
design requires widening it. The current-context binding layer, if ever built,
uses only the existing public input-package builder.
