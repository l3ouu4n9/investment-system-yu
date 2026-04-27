# Daily Execution Check Runbook

Daily Execution Check is a weekday execution-maintenance workflow for weekly-approved open orders. It is not a daily strategy rerun, research refresh, ranking update, or new buy/sell decision workflow.

## 1. When To Use It

- Run after the full weekly Step1-Step4 pipeline has completed, typically after the Sunday weekly cycle.
- Run after the close on weekdays Monday through Thursday when open orders need execution-only maintenance.
- On Friday, use only as a light check unless there is an expiry issue, event-window issue, or operator-provided override issue.
- Do not use Daily Execution Check to rerun strategy, refresh research, change ranking, add new tickers, or create new investment decisions.

## 2. Required Preconditions

Before rendering a daily check, confirm these files exist:

```text
artifacts/current/step3_audit_engine/audited_decision_packet.json
artifacts/current/step4_order_compiler/template4_orders.txt
artifacts/current/step4_order_compiler/order_state_export.txt
inputs/current/portfolio_snapshot.txt
inputs/current/strategy_settings.yaml
```

The operator must update `inputs/current/portfolio_snapshot.txt` with the latest open orders and fills state before running the check.

If the check needs to reason about price drift, gap-to-order distance, or missed-fill risk, provide or generate a daily market snapshot first. Without that data, price-drift analysis is incomplete.

## 3. Commands

Preferred runtime commands:

```bash
PYTHONPATH=src uv run python -m investment_orchestrator.cli.run_daily_execution_check render --date YYYY-MM-DD
PYTHONPATH=src uv run python -m investment_orchestrator.cli.run_daily_execution_check parse --date YYYY-MM-DD
```

To attempt daily market data generation before rendering:

```bash
PYTHONPATH=src uv run python -m investment_orchestrator.cli.run_daily_execution_check render --date YYYY-MM-DD --generate-market-data
```

Also supported with the local `.venv`:

```bash
PYTHONPATH=src .venv/bin/python -m investment_orchestrator.cli.run_daily_execution_check render --date YYYY-MM-DD
PYTHONPATH=src .venv/bin/python -m investment_orchestrator.cli.run_daily_execution_check parse --date YYYY-MM-DD
```

Tests:

```bash
PYTHONPATH=src uv run --extra dev pytest -q
```

## 4. Expected Artifacts

Daily Execution Check writes to:

```text
artifacts/daily/YYYY-MM-DD/daily_execution_check/
```

Expected files:

```text
prompt.txt
raw_output.txt
raw_output.meta.json
daily_execution_check.txt
daily_execution_actions.json
```

It must not write to `artifacts/current/`.

## 5. Operator Rules

Daily Execution Check may recommend only:

- `KEEP`
- `REPLACE`
- `CANCEL`
- `HOLD_FOR_WEEKLY_REVIEW`
- `DATA_GAP`

Daily Execution Check must not:

- add ticker
- increase budget
- change thesis
- change alpha ranking
- change weekly role
- treat `ORDER_STATE_EXPORT` as live broker truth
- write into `artifacts/current/`
- modify Step1-Step4 weekly artifacts

`ORDER_STATE_EXPORT` is a weekly compiler output and intended-state reference. Live order status, fills, remaining quantity, and broker state must come from the updated portfolio snapshot or another operator-provided live-state source.

## 6. DATA_UNAVAILABLE Behavior

If `DAILY_MARKET_DATA_SNAPSHOT` is `DATA_UNAVAILABLE`, price drift and gap-to-order analysis are incomplete.

In that case, the LLM should prefer `DATA_GAP` or conservative `KEEP` unless `portfolio_snapshot.txt` itself contains enough current price and open-order evidence to support an execution-only conclusion.

Do not infer live prices or fill risk from `ORDER_STATE_EXPORT` alone.

When `--generate-market-data` is used, the workflow attempts to write:

```text
artifacts/daily/YYYY-MM-DD/market_data_raw.json
artifacts/daily/YYYY-MM-DD/market_data_snapshot.json
```

If the fetch/build step fails, prompt rendering continues and the error is written to:

```text
artifacts/daily/YYYY-MM-DD/market_data_generation_error.txt
```
