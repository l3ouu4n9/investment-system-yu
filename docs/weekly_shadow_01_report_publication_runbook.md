# WEEKLY-SHADOW-01 Report-Only Publication Runbook

## 1. Purpose

This runbook documents an explicit operator procedure for publishing
WEEKLY-SHADOW-01 report-only artifacts.

It covers one narrow task: the operator has a deterministically constructed
grounded prompt, has obtained a response from an LLM by hand, has saved the
exact raw response bytes to a file, and now wants those bytes validated and
published once as report-only artifacts.

It is the only documented procedure for invoking WS01e. There is no other
supported invocation form.

## 2. Authority and non-authority

It is not trade authorization.

A successful publication does not create availability, freshness,
permission, gate passage, order readiness, quantity, an order, or
execution authority.

`HOLD` and `NO_TRADE` remain valid outcomes before and after any run of this
procedure, including a successful one. A WS01 report is not required before
trading and is not required to continue any other stage: a missing, failed, or
ambiguous publication does not block, authorize, or alter any decision path.

The published artifacts are records. They are not inputs to state, permission,
gate, portfolio, order, broker, or execution code.

## 3. Manual LLM boundary

The complete boundary is:

```text
deterministic grounded prompt construction
→ operator manually submits the prompt to an LLM
→ operator manually obtains the response
→ operator saves the exact raw response bytes
→ operator explicitly invokes WS01e
→ WS01e validates and publishes report-only artifacts
```

The three middle steps are human actions performed outside this procedure. The
following are prohibited:

```text
LLM API calls
provider or model automation
automatic prompt submission
automatic response retrieval
polling
model retries
scheduling
agent loops
credentials
provider SDKs or endpoints
```

WS01e and this documented workflow do not perform these steps.
No automatic model interaction is part of this procedure.

Wherever this runbook says "response", it means the bytes the operator saved by
hand — never something the command fetched.

## 4. Preconditions

Before starting:

- The generation whose response is being published is known by its exact
  generation ID.
- The response was obtained manually and saved to a single file (§5).
- The output root for report-only publication is chosen and is an absolute
  path the operator controls.
- No automatic tooling, scheduler, wrapper, or agent loop is arranged to invoke
  the command.
- A restricted operator record (§12) is available to write the run into.

## 5. Saving exact response bytes

Save the response so that the file holds exactly what the LLM produced:

- Do not edit, trim, decode, pretty-print, or reformat it.
- Preserve encoding, line endings, BOM, final newline, embedded NULs, and all
  other bytes exactly as received.
- Write it to a regular file. Do not use a symlink, and do not place it behind a
  symlinked parent directory.
- Keep the file at or below **131072 bytes**.
- Stop every writer before invoking the CLI. The command reads the file twice
  and cross-checks file identity, size, and timestamps; a file still being
  written is rejected rather than partially published.
- Retain the file unchanged until the run record has been reviewed.

Zero-byte, malformed, or invalid responses are handled by deterministic
validation. A frozen validation reason is an expected fail-closed result, not a
defect in the command or the response file.

Do not repair or regenerate the response before classifying the failure (§11).

## 6. CLI command

```bash
python -m investment_orchestrator.cli.weekly_shadow_01_report_publisher_cli publish \
  --generation-id <generation-id> \
  --raw-response-file /absolute/path/to/raw-response \
  --output-root /absolute/path/to/output \
  [--repository-root /absolute/path/to/repository]
```

- Every selector is explicit. `--generation-id`, `--raw-response-file`, and
  `--output-root` are required. `--repository-root` is optional; when omitted,
  the publisher uses its own default.
- The response-file path must be absolute: it must begin with `/` and contain no
  empty, `.`, or `..` component.
- Each selector may appear at most once.
- Duplicate selectors are rejected even when their values are identical.
- One invocation processes one generation and one response file.
- There is no stdin, glob, scan, latest/current discovery, batch, watch, retry,
  wrapper, alias, or alternative entry point.

For selecting the repository interpreter, use the form shown in the README
"Environment" section. The subcommand, option names, and arity above are the
invocation contract regardless of how the interpreter is launched.

## 7. Pre-run checklist

```text
[ ] Correct generation ID
[ ] Correct absolute response-file path
[ ] Correct output root
[ ] Correct optional repository root, or deliberately omitted
[ ] Response file is no longer being written
[ ] Response file is regular and non-symlinked
[ ] Every selector flag appears once
[ ] No automatic tooling is invoking the command
[ ] Exact command has been recorded in the restricted operator record
```

Record the exact command in the restricted operator record (§12) before running
it, so any later classification refers to a known command rather than a
reconstruction. Do not place the unredacted command into shared chat, tickets,
or logs.

## 8. Success output

On exit 0 the command writes exactly five fields:

```text
publication_reused
report_identity_sha256
run_summary_identity_sha256
publication_relative_path
artifact_filenames
```

`publication_relative_path` has the form `reports/<report_identity_sha256>`.
`artifact_filenames` is the pair
`weekly_shadow_01_analyst_report.json,weekly_shadow_01_run_summary.json`.

These five fields:

- form a report-only receipt;
- include `publication_reused=true` as a normal verified-reuse result, meaning
  an identical artifact set already existed at that path and was re-verified —
  it is not a duplicate-run error and requires no cleanup;
- report a relative path that is informational only;
- do not activate the report;
- change no availability, freshness, permission, gate, `HOLD`, `NO_TRADE`,
  `SELL`, `NEW_BUY`, `ORDER_COMPILATION`, order, or broker state.

Record the fields. This runbook does not prescribe opening or parsing the
published report artifacts (§14).

## 9. Exit-code table

```text
0  Published or independently verified reuse
1  Deterministic WS01_BR_* failure other than publication ambiguity
2  CLI usage or duplicate-selector error
3  Local raw-response-file error
4  Publication ambiguity or unclassifiable post-publication uncertainty
```

Exits 1 and 4 print one frozen `WS01_BR_*` reason code. Exit 3 prints one closed
local token (§11). Exit 2 prints the command's own usage error.

### Exit 0

- Record the five receipt fields (§8).
- Retain the response file until the record is complete.
- Treat the artifacts as report-only.
- Do not infer downstream authority — no availability, freshness, permission,
  gate passage, or order readiness follows from a successful publication.

### Exit 1

- Record the exact frozen reason code as printed.
- Classify the failure as a response-content failure (parse, schema,
  cross-field, echo, duplicate key, missing) or a contract/binding
  failure (source, generation, run, prompt-template, artifact-set, version)
  before doing anything else.
- Do not edit or repair published artifacts.
- Do not regenerate blindly. A retry that skips classification is prohibited.
- Any later response must be manually obtained and submitted through the
  complete normal procedure, including the pre-run checklist.

### Exit 2

- Review the complete command end to end.
- Correct syntax or repeated selectors. A required selector may be missing, an
  unknown option may be present, the `publish` subcommand may be absent, or a
  selector may be repeated.
- No response-file read or publication should have occurred.
- Rerun only after the command has been checked end to end.

### Exit 3

One of five closed local tokens is printed:

```text
raw_response_file_not_absolute
raw_response_file_wrong_type
raw_response_file_unstable
raw_response_file_unreadable
raw_response_file_oversized
```

| Token | Meaning |
|---|---|
| `raw_response_file_not_absolute` | The selector is not an absolute path, or contains an empty, `.`, or `..` component. |
| `raw_response_file_wrong_type` | The path is not a regular file — for example a directory, device, FIFO, or symlink. |
| `raw_response_file_unstable` | The file changed while it was being read. |
| `raw_response_file_unreadable` | The file or one of its parent directories could not be opened or read. |
| `raw_response_file_oversized` | The file exceeds 131072 bytes. |

A file larger than 131072 bytes is always this local exit-3 condition, detected
before WS01d is invoked; it is never an exit-1 validation failure in this
procedure.

- Do not reuse an unstable file without a controlled recreation of that file
  through a deliberate operator action.
- Stop all writers before any further attempt.
- Do not assume WS01d was called.
- Do not expose the response body in support records.

## 10. Exit-4 stop procedure

```text
STOP

Do not blind-retry.
Do not delete or overwrite the output root.
Do not clean report_attempts.
Do not create a pointer.
Do not manually copy artifacts.
Do not infer that publication failed.
```

Exit 4 reports `WS01_BR_PUBLICATION_AMBIGUOUS`. Ambiguity means the result is
unknown, not failed: the artifacts may or may not have been published.

No approved read-only WS01 publication-state inspection procedure currently
exists. Therefore:

- Record the run and escalate.
- Take no filesystem action.
- Do not improvise `find`, `mv`, `rm`, editor, cleanup, or manual-copy
  procedures. An ad-hoc command can mutate or destroy exactly the state needed
  to classify the outcome, turning recoverable ambiguity into permanent loss.

The available context for an exit-4 run is:

```text
operator-recorded generation ID
exact command context
explicit output root
exit code
WS01_BR_PUBLICATION_AMBIGUOUS
```

There is no receipt and no identity for an exit-4 run. Do not record or cite one.

## 11. Privacy and logging

### Restricted local operator record

May contain:

```text
operator-recorded timestamp
generation ID
exact command under approved access controls
exit code
exact reason/token
five receipt fields only for exit 0
operator notes
```

### Shared logs, tickets, or chat

Must not contain:

```text
raw response bytes
response excerpts
unredacted sensitive absolute paths
stack traces
credentials
provider-account information
prompt body
report body
```

In shared records, refer to paths by role — for example "configured output
root" — rather than by absolute value.

The record is maintained by the operator. No telemetry, upload, or automatic
reporting is part of this procedure, and none may be added under it.

## 12. Prohibited actions

```text
automatic model interaction
blind retries
artifact mutation or deletion
report_attempts cleanup
manual artifact copying
pointer creation
wrapper or alternate entry point
stdin/glob/batch/watch invocation
automatic workflow invocation
response repair before classification
raw-response disclosure
receipt-as-authority interpretation
```

## 13. HOLD/NO_TRADE implications

- The absence of a successful report remains non-authoritative.
- A failed or ambiguous run does not authorize any fallback action.
- `HOLD` and `NO_TRADE` remain valid outcomes.
- No report may be converted into a buy, sell, quantity, or order artifact.

A report is not a precondition for any decision. The correct response to any
non-zero exit is to record, classify, and stop — never to substitute operator
judgment for a missing report.

## 14. Troubleshooting boundaries

Troubleshooting under this runbook is limited to:

```text
record
classify by exit code and exact token
correct operator input where applicable
stop when publication state is ambiguous
```

No shell-based publication-state inspection is defined here, and none may be
invented while following this runbook. A read-only WS01 publication-state
inspection procedure does not exist yet; designing one is a separate future
design prerequisite and is out of scope for this document.

## 15. Post-run record

Write one entry per invocation into the restricted operator record:

```text
timestamp (operator-recorded)
generation ID
exact command (restricted access only)
exit code
exact WS01_BR_* reason code or local token
five receipt fields (exit 0 only)
operator notes: classification, decision, escalation if any
```

Review the entry before releasing the response file from retention (§5).

## 16. No automation statement

WS01e:

- creates no latest/current/active pointer;
- has no weekly-orchestrator caller;
- is not invoked automatically after prompt construction;
- is not consumed by state, permission, gate, order, or broker code;
- reports a relative receipt path that is informational only.

This procedure is manual end to end. It must be invoked explicitly by an
operator, once per generation and response file.
