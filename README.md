# investment-orchestrator

Transitional manual workflow for the investment research and decision pipeline.

## Layout

- `prompts/`: operator-maintained prompt templates
- `inputs/current/`: current-run operator inputs
- `artifacts/current/`: generated prompts plus parsed outputs for the current run
- `src/investment_orchestrator/`: workflow, parser, validator, and market-data code
- `tests/unit/`: lightweight unit tests for repo-local helpers

## Environment

Core runtime dependencies live in `pyproject.toml`.

Typical local commands in this repo use the checked-in virtualenv:

```bash
PYTHONPATH=src .venv/bin/python -m investment_orchestrator.cli.run_step1 render
PYTHONPATH=src .venv/bin/python -m investment_orchestrator.cli.run_step1 parse
```

Swap `run_step1` for `run_step2`, `run_step3`, or `run_step4` as needed.

## Tests

Run unit tests with:

```bash
PYTHONPATH=src .venv/bin/python -m pytest -q
```

If `pytest` is missing in the local environment, install the dev extras or add `pytest` to the virtualenv first.

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
