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
