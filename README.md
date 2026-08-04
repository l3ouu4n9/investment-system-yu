# investment-orchestrator

Transitional manual workflow for the investment research and decision pipeline.

## Layout

- `prompts/`: operator-maintained prompt templates
- `inputs/current/`: current-run operator inputs
- `artifacts/current/`: generated prompts plus parsed outputs for the current run
- `src/investment_orchestrator/`: workflow, parser, validator, and market-data code
- `tests/unit/`: lightweight unit tests for repo-local helpers

## Current Inputs

The active workflow reads `inputs/current/portfolio_snapshot.txt` and
`inputs/current/strategy_settings.yaml`. Daily Execution Check can also read
`override_event_notes.txt` from the daily artifact directory or `inputs/current/`.

`inputs/current/current_run_state.json` and `inputs/current/operator_notes.txt`
are reserved operator scratch files; they are not consumed by the current CLIs.

## Investment Goal Profile

[Investment Goal Profile v1](docs/investment_goal_profile_v1.md) is a
documentation-only operator-review worksheet. It is not consumed by runtime,
is not an authorization source, and has no effect on permissions, budgets,
allocations, orders, or execution.

## Step 4 Evidence Fixtures

[Step 4 Artifact Compatibility B1a](docs/step4_artifact_compatibility_b1.md) provides
repository-owned, synthetic Step 4 evidence fixtures. They are not runtime inputs or an
authorization source, and do not change validation, publication, readiness, permissions,
orders, or execution. Canonical parser work remains pending in B2.

## Environment

Core runtime dependencies live in `pyproject.toml`.

Typical local commands in this repo use the checked-in virtualenv:

```bash
PYTHONPATH=src .venv/bin/python -m investment_orchestrator.cli.run_step1 render
PYTHONPATH=src .venv/bin/python -m investment_orchestrator.cli.run_step1 parse
```

Swap `run_step1` for `run_step2`, `run_step3`, or `run_step4` as needed.
If you prefer `uv`, the equivalent runtime form is:

```bash
PYTHONPATH=src uv run python -m investment_orchestrator.cli.run_step1 render
PYTHONPATH=src uv run python -m investment_orchestrator.cli.run_step1 parse
```

## Weekly Orchestration

The weekly run is a manual Step 1→4 paste flow, but there is a top-level
weekly command that routes the run deterministically from the Step 1
degraded-mode decision:

```bash
PYTHONPATH=src uv run python -m investment_orchestrator.cli.run_weekly
```

- **`STRICT_FRESH` + actionable permission** → proceeds to the existing Step 2
  render path (which re-enforces the same fail-closed gate). The weekly run is
  not complete here; continue with the manual `run_step2 parse` → `run_step3`
  → `run_step4` flow.
- **Degraded / blocked / missing / malformed research** (e.g.
  `DEGRADED_WITH_LAST_GOOD`, `STRICT_STALE`, `DEGRADED_NO_RESEARCH`,
  `INVALID_CONTRACT`, `NO_OUTPUT`, `MANUAL_REVIEW_REQUIRED`) → a **deterministic
  controlled `NO_TRADE` terminal outcome**. It does **not** enter the Step 2 LLM
  path and does **not** touch Step 3/4. It writes `artifacts/current/run_summary.json`
  and `artifacts/current/weekly_outcome.json`, then finishes as a *safe completion*.

**Exit behavior** is deliberate: the weekly-level command exits `0` for a
controlled `NO_TRADE` terminal outcome — at the weekly level a fail-closed
degraded-research gate is a legitimate no-trade terminal result, not a broken
run. Automation must read `weekly_outcome.json` (`terminal_result` /
`weekly_completed`) rather than inferring "tradeable" from the exit code. The
step-level commands (e.g. `run_step2 render`) deliberately keep exiting non-zero
when a gate blocks them — that fail-closed behavior is unchanged. A non-zero
exit from `run_weekly` means a genuine operational error, never a controlled
`NO_TRADE`.

`weekly_outcome.json` is a **deterministic operational artifact**, not an LLM
output (`is_llm_generated` is always `false`) and not an order output. Key
fields: `weekly_completed`, `terminal_result`, `reason`, `research_state`,
`allowed_actions`, `blocked_actions`, `manual_review_required`, and
`source_artifacts` (tracing back to the Step 1 degraded decision and
`run_summary.json`). Use `--no-render-step2` to skip the Step 2 prompt render on
the actionable path. See the
[Weekly Run Operator Runbook](docs/weekly_run_operator_runbook.md).

### Budgets to review before each weekly run

Before each weekly run (`run_weekly` / Step 4), review the two operator-controlled
budget keys in `inputs/current/strategy_settings.yaml`:

- `hard_cap_open_orders_budget` — ceiling on **total** open-order exposure (all
  open buy orders, including kept / replaced existing orders).
- `target_new_buy_budget_this_run` — USD ceiling on **this run's net-new**
  buy-side deployment only. Replacement, cancel-only, and no-buy runs do **not**
  consume it.

Both are **deterministic operator input, not LLM-generated**, and are enforced by
the Step 4 post-order validator. Automation must **not** infer
`target_new_buy_budget_this_run` from the Step 2 / Step 3 LLM proposed budget —
set it explicitly each run. A stale value can over-allow or over-block deployment;
if it is missing while net-new BUY orders exist, primary-path Step 4 validation
**fails closed**.

## Operator-Approved Anchor Grounding

Operator-approved research anchors and optional approval-anchor revocations are
authored in `inputs/current/research_anchor_approvals.yaml`. Revocations support
approval-derived anchors only; baseline `research_anchors.yaml` anchors must be
edited directly or allowed to expire via `valid_until`.

`operator_completed_anchor_sha256` is the binding hash for both approval
activation and revocation binding. `candidate_sha256` and
`research_anchor_candidates.json` remain audit-only suggestions and cannot
approve or revoke anchors.

The runtime grounding source for `support_signals` is only
`evidence_packet.active_anchor_registry`. `support_signals` does not read
approval files, revocation files, revocation validation artifacts, or candidate
artifacts directly. Revocation validation output is report-only; invalid
revocation state makes the approvals-inclusive registry unsafe, causing baseline
fallback when baseline is safe or fail-closed empty selection when it is not.

This remains report-only grounding with `permission_effect: "none"` and
`not_authorization: true`; it grants no `NEW_BUY`, `ORDER_COMPILATION`, Step 4,
final execution, broker/live execution, automatic order placement, executable
order authority, or permission/gate/order-path change.

See the
[Operator-Approved Anchor Grounding Runbook](docs/operator_approved_anchor_grounding_runbook.md)
for source roles, approval workflow, fail-closed outcomes, and safety boundaries.

## WEEKLY-SHADOW-01 Report-Only Publication

WEEKLY-SHADOW-01 report-only artifacts are published by an explicit,
operator-invoked command. It consumes only a raw-response file the operator
obtained from an LLM by hand and saved unmodified; it performs no model
interaction of its own. It publishes report-only artifacts and creates no
investment or execution authority — no availability, freshness, permission,
gate passage, order readiness, order, or broker effect. No orchestrator, weekly
run, or downstream stage invokes it.

See the
[WEEKLY-SHADOW-01 Report-Only Publication Runbook](docs/weekly_shadow_01_report_publication_runbook.md)
for the manual handoff boundary, the exact command, exit-code actions, and the
publication-ambiguity stop rule.

## H2c Prospective Dual-Side Capture

The [MMI H2c Prospective Dual-Side Capture Runbook](docs/mmi_h2c_prospective_capture_runbook.md)
documents one operator-invoked, foreground, report-only manual handoff. It has
no automatic provider integration and grants no publication, order, broker,
weekly-routing, permission, or gate authority.

## Run Status / Blocked-Run Summary

When Step 1 / Deep Research produces no output, invalid research, or a
degraded-mode gate blocks the pipeline (Step 2/3/4 fail closed), run:

```bash
PYTHONPATH=src uv run python -m investment_orchestrator.cli.run_status
```

This reads the existing per-step artifacts and writes a deterministic
operational summary to `artifacts/current/run_summary.json`. It is **not** an
LLM decision packet, audit packet, or order output (`is_llm_generated` is always
`false`). Key fields: `run_blocked`, `recommended_result`, `research_state`,
`manual_review_required`, `blocked_stages`, `allowed_actions`, `blocked_actions`,
and `source_artifacts` (which trace back to the Step 1 degraded decision and the
Step 2/3/4 blocked artifacts). A `recommended_result` of `NO_TRADE` is a
deterministic safety outcome, not a silent failure. See
[Deep Research degraded-mode design](docs/deep_research_degraded_mode_design.md)
for the operating procedure.

## Daily Execution Check

Weekday execution maintenance is a separate manual workflow. It reads the latest
weekly artifacts and writes check artifacts to `artifacts/daily/YYYY-MM-DD/daily_execution_check/`.
When `--generate-market-data` is used, generated market data and generation errors are written
under `artifacts/daily/YYYY-MM-DD/`. The workflow must not write to `artifacts/current/`.
See [Daily Execution Check Runbook](docs/daily_execution_check_runbook.md) for the operating procedure.

```bash
PYTHONPATH=src .venv/bin/python -m investment_orchestrator.cli.run_daily_execution_check render
PYTHONPATH=src .venv/bin/python -m investment_orchestrator.cli.run_daily_execution_check parse
```

Use `--date YYYY-MM-DD` to render or parse a specific daily check date.
That date is the daily execution-check date. `inputs/current/strategy_settings.yaml`
may still carry the weekly Step1-Step4 source run timestamp until the next weekly cycle.
The same commands can be run through `uv` with `PYTHONPATH=src uv run python -m ...`.
Add `--generate-market-data` to `render` to attempt daily market data generation before prompt rendering.

## Tests

Run unit tests with:

```bash
PYTHONPATH=src .venv/bin/python -m pytest -q
```

If `pytest` is missing in the local environment, install the dev extras or add `pytest` to the virtualenv first.
With `uv`, use:

```bash
PYTHONPATH=src uv run --extra dev pytest -q
```

## Artifact housekeeping

To archive the current run artifacts and start a clean next run:

```bash
PYTHONPATH=src .venv/bin/python -m investment_orchestrator.cli.manage_artifacts prepare-next-run
```

Useful variants:

```bash
PYTHONPATH=src .venv/bin/python -m investment_orchestrator.cli.manage_artifacts archive-current --label before_prompt_refresh
PYTHONPATH=src .venv/bin/python -m investment_orchestrator.cli.manage_artifacts clear-current
```

`prepare-next-run` archives `artifacts/current/` into `artifacts/archive/<label>/` when the current directory is non-empty, then recreates a clean `artifacts/current/`.
