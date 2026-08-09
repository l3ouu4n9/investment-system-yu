# MMI H2c Archived Consume Operator Runbook

## 1. Purpose and authority boundary

This procedure completes one prepared H2c paired-comparison case through the
foreground archived-consume command. It is manual, offline, report-only, and
has `authority_effect = NONE`.

The repository persists prompts and consumes response files. The human
operator alone copies and submits each prompt, receives each response,
preserves its exact bytes, and places the two response files. The command has
no LLM API, provider or model selection, SDK, HTTP/network access, credentials,
automatic submission or retrieval, polling, retry, agent loop, scheduler,
background process, or clipboard automation.

The command does not invoke the older live/manual capture CLI. It calls only
the archived persisted-case consume owner.

## 2. Fixed case layout and success rule

The prepare owner creates these relevant leaves under a fresh absolute case
root:

```text
prepared/prepared_case.json
prompts/h1_prompt.txt
prompts/legacy_prompt.txt
responses/h1_response.raw             initially absent
responses/legacy_response.raw         initially absent
artifacts/case_evidence_bundle.json    initially absent
artifacts/comparison_report.json       initially absent
artifacts/receipt.json                 initially absent
```

Success means all three conditions are true:

```text
process exit = 0
workflow_status=COMPLETED
artifacts/receipt.json exists
```

The receipt is written last. Anything else is not success.

## 3. A — prepare a fresh case

Operator action: choose a never-used absolute case root and invoke the existing
prepare command with the independently authenticated strategy and portfolio
source hashes:

```bash
PYTHONPATH=src .venv/bin/python -m \
  investment_orchestrator.cli.run_mmi_h2c_prepare \
  --strategy-settings-expected-sha256 <STRATEGY_SHA256> \
  --portfolio-snapshot-expected-sha256 <PORTFOLIO_SHA256> \
  --case-root <ABSOLUTE_FRESH_CASE_ROOT>
```

Expected result: `workflow_status=AWAITING_OPERATOR_RESPONSES`, a
`prepared_case_identity_sha256=<sha256>` stdout line, and both persisted prompt
files.

Fail closed: stop if prepare is nonzero, the root was not fresh, either prompt
is absent, or the identity line is absent or ambiguous.

Do not reuse an old case root, infer the identity from a filename or directory
name, or edit the prepared manifest.

## 4. B — copy the exact persisted prompts

Operator action: open and manually copy the complete contents of:

```text
prompts/h1_prompt.txt
prompts/legacy_prompt.txt
```

Expected result: two distinct, complete prompts ready for their respective
manual submissions.

Fail closed: stop if either file cannot be read exactly or its role is
ambiguous.

Do not edit, reformat, concatenate, regenerate, or interchange the prompts.

## 5. C and D — submit both prompts manually

Operator action:

1. Manually submit the exact H1 prompt and receive its response.
2. Manually submit the exact legacy prompt and receive its response.

Expected result: one completed H1 response and one completed legacy response,
each saved to a private staging file outside the case root.

Fail closed: stop if a response is incomplete, ambiguous, combined with
another message, or cannot be preserved exactly.

Do not use repository code to select a provider/model, submit either prompt,
retrieve either response, poll, retry, or automate a clipboard handoff.

## 6. E and F — preserve and place exact response bytes

The staging files must be completed and closed before placement. The exact byte
sequence copied or saved by the operator is the response input; it is not
authenticated provider-transport evidence.

Never do any of the following:

- trim a response;
- reformat JSON;
- add or remove Markdown code fences;
- normalize line endings;
- repair malformed output;
- merge messages; or
- otherwise modify the response byte sequence.

The staging files are operator-controlled placement sources. Archived consume
does not read or validate those staging paths. Before placement, the operator
must have obtained each complete response, preserved its exact bytes, and
finished and closed the staging writer. Placement must preserve those bytes and
must not overwrite an existing fixed response leaf.

After placement, E2 reads and validates exactly:

```text
responses/h1_response.raw
responses/legacy_response.raw
```

The committed consume-time transport contract requires each placed fixed leaf
to be a regular file, not a symlink, nonempty, no larger than 262,144 bytes, and
stable throughout the complete read. These transport/file requirements are
distinct from later deterministic H1 UTF-8/JSON/schema validation and legacy
UTF-8/Step 1 content validation.

Use completed staging files and exclusive, no-replace placement. The following
uses the same GNU `dd` no-follow/exclusive convention as the existing H2c
manual-handoff procedure:

```bash
set -euo pipefail

H2C_CASE_ROOT=/absolute/path/to/fresh/case
H2C_H1_STAGING=/absolute/path/to/completed/h1-response
H2C_LEGACY_STAGING=/absolute/path/to/completed/legacy-response

for H2C_RESPONSE_STAGING in \
  "$H2C_H1_STAGING" \
  "$H2C_LEGACY_STAGING"; do
  [[ "$H2C_RESPONSE_STAGING" = /* ]]
  [[ -f "$H2C_RESPONSE_STAGING" && ! -L "$H2C_RESPONSE_STAGING" ]]
  H2C_RESPONSE_SIZE=$(stat -c %s -- "$H2C_RESPONSE_STAGING")
  (( H2C_RESPONSE_SIZE >= 1 && H2C_RESPONSE_SIZE <= 262144 ))
done

[[ ! -e "$H2C_CASE_ROOT/responses/h1_response.raw" ]]
[[ ! -L "$H2C_CASE_ROOT/responses/h1_response.raw" ]]
[[ ! -e "$H2C_CASE_ROOT/responses/legacy_response.raw" ]]
[[ ! -L "$H2C_CASE_ROOT/responses/legacy_response.raw" ]]

umask 077
dd if="$H2C_H1_STAGING" \
  of="$H2C_CASE_ROOT/responses/h1_response.raw" \
  iflag=fullblock,nofollow oflag=nofollow conv=excl,fsync status=none
cmp -- "$H2C_H1_STAGING" \
  "$H2C_CASE_ROOT/responses/h1_response.raw"

dd if="$H2C_LEGACY_STAGING" \
  of="$H2C_CASE_ROOT/responses/legacy_response.raw" \
  iflag=fullblock,nofollow oflag=nofollow conv=excl,fsync status=none
cmp -- "$H2C_LEGACY_STAGING" \
  "$H2C_CASE_ROOT/responses/legacy_response.raw"
```

Expected result: both fixed response leaves exist as exact copies, and all
three artifact leaves remain absent.

Fail closed: if any check, copy, or comparison fails or is interrupted, do not
run consume for this case. Retain the case unchanged and prepare a fresh root.

Do not overwrite a response leaf, stream a response through stdin, or add a
response-path override.

## 7. G — run archived consume

Copy the exact `prepared_case_identity_sha256` printed by Stage A. It remains an
independent operator-supplied authentication input; do not read it back from
`prepared/prepared_case.json` to construct the expected value.

```bash
H2C_PREPARED_CASE_IDENTITY_SHA256=replace_with_exact_stage_a_value

PYTHONPATH=src .venv/bin/python -m \
  investment_orchestrator.cli.run_mmi_h2c_consume_archived \
  --case-root "$H2C_CASE_ROOT" \
  --expected-prepared-case-identity-sha256 \
  "$H2C_PREPARED_CASE_IDENTITY_SHA256"
```

Expected stdout keys are:

```text
workflow_status
prepared_case_identity_sha256
case_evidence_bundle_identity_sha256
comparison_report_identity_sha256
receipt_identity_sha256
```

Controlled failures exit 3 and report the committed error code and failure
class. Argument usage failures exit 2. Unhandled internal defects remain exit
1 process failures. There is no automatic retry.

Fail closed: after any failed or interrupted consume invocation, do not retry
the same case, even if no success artifact is visible. Preserve it and create a
fresh prepared case at a fresh absolute root.

Do not delete partial artifacts, repair or resume a case, use `--force`, or
fall back to the live consume/capture path.

## 8. H — inspect the report-only artifacts

After exit 0, inspect the fixed leaves without modifying them:

```text
artifacts/case_evidence_bundle.json
artifacts/comparison_report.json
artifacts/receipt.json
```

The evidence bundle preserves the portable paired-case evidence. The
comparison report records the deterministic H1-versus-legacy comparison. The
receipt is the final identity and linkage record.

All three are report-only and have `authority_effect = NONE`. They do not
authorize Step 1 replacement, publication, a change to `HOLD` or `NO_TRADE`,
`NEW_BUY`, `SELL`, `ORDER_COMPILATION`, any order, or broker/live execution.
They do not affect Steps 2–4, weekly routing, availability/freshness trading
gates, permissions, budgets, caps, quantities, final safety, or pointers.

If the command was nonzero, `workflow_status=COMPLETED` was absent, or the
receipt is absent, the case did not succeed. Do not repair or reuse it.
