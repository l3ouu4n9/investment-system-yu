# Weekly Run Operator Runbook (Manual Step 1→4)

## 1. Purpose and scope

This runbook is the single operator-facing procedure for the **manual-order v1** weekly workflow
(Step 1 research → Step 2 decision → Step 3 audit → Step 4 order draft).

- This is **manual-order v1**. It produces an **order draft** that the operator reviews and places
  manually at a broker.
- It is **not live-trading / broker automation.** Nothing here submits, cancels, or reconciles
  orders at a broker.
- An LLM (Deep Research / decision / audit / order-compiler steps) may produce research, decisions,
  audits, and an order draft, but **deterministic code gates decide whether the run may proceed.**
  LLM self-reported booleans are never sufficient on their own.

## 2. Step 1→4 operator flow

Each step follows the same **render → paste → parse** pattern (run via `uv` or the checked-in venv;
`PYTHONPATH=src`):

| Step | Render | Paste LLM output into | Parse | Key artifacts produced |
|---|---|---|---|---|
| 1 Research | `run_step1 render` | `artifacts/current/step1_research/raw_output.txt` | `run_step1 parse` | `research_output.json`, `research_degraded_mode_decision.json`, handoff candidate/validation, last-known-good state |
| 2 Decision | `run_step2 render` | `step2_decision_builder/raw_output.txt` | `run_step2 parse` | `template2_output.txt`, `decision_packet.json` |
| 3 Audit | `run_step3 render` | `step3_audit_engine/raw_output.txt` | `run_step3 parse` | `audited_decision_packet.json` |
| 4 Orders | `run_step4 render` | `step4_order_compiler/raw_output.txt` | `run_step4 parse` | `template4_orders.txt`, `order_state_export.txt`, `exec_summary.txt` (only on success) |

```bash
PYTHONPATH=src uv run python -m investment_orchestrator.cli.run_step1 render
PYTHONPATH=src uv run python -m investment_orchestrator.cli.run_step1 parse
# ...repeat for run_step2 / run_step3 / run_step4
```

Run the status summary at any time (it only reads existing artifacts; it changes nothing):

```bash
PYTHONPATH=src uv run python -m investment_orchestrator.cli.run_status
# writes artifacts/current/run_summary.json
```

A `render`/`parse` that **exits non-zero** means a deterministic gate blocked the step — do not work
around it. See §4/§5.

## 3. Deterministic gates (where the run can fail closed)

| Stage | Gate | Effect |
|---|---|---|
| Step 1 | degraded-mode evaluator (report-only) | classifies research state: `STRICT_FRESH` / `STRICT_STALE` / `DEGRADED_WITH_LAST_GOOD` / `DEGRADED_NO_RESEARCH` / `INVALID_CONTRACT` / `NO_OUTPUT`; writes `research_degraded_mode_decision.json` |
| Step 2 render | research degraded-mode gate | fail closed unless Step 1 permits the actionable path; writes `step2_blocked_by_research_gate.json` |
| Step 3 render+parse | upstream artifact guard | fail closed if Step 2 blocked or required Step 2 artifacts missing |
| Step 4 render+parse | upstream artifact guard | fail closed if Step 2/3 blocked or required artifacts missing |
| Step 4 render+parse | **final execution safety gate** | fail closed unless STRICT_FRESH permission + structured Step 2/3 packets + no blockers; **LLM `audit_passed`/`order_compiler_ready` are necessary but not sufficient** |
| Step 4 parse | post-order validator | structure / numeric / duplicate / universe / submit-side + total open-order exposure budget / per-bucket new tickers / KEEP_EXISTING-vs-snapshot — all fail closed |
| Step 4 parse | **validate-before-write quarantine** | candidate order files are validated in `quarantine/` first; canonical files are **published only after validation passes**; on failure the prior-good canonical set is preserved and the rejected candidate stays under `quarantine/` |

## 4. Acceptance checklist — before any manual order placement

Place orders only if **all** of these hold:

- [ ] `artifacts/current/run_summary.json` → `run_blocked` is **false**
- [ ] `run_summary.json` → `recommended_result` is **not** `NO_TRADE`
- [ ] **No** `*blocked*.json` artifact exists in `step2_decision_builder/`, `step3_audit_engine/`, or `step4_order_compiler/`
- [ ] Canonical Step 4 files all exist:
  `step4_order_compiler/template4_orders.txt`, `order_state_export.txt`, `exec_summary.txt`
- [ ] `run_step4 parse` **exited successfully** (zero exit code; no traceback)
- [ ] `SELL_ORDERS` in `template4_orders.txt` is `NONE` / `[]` — **any non-empty sell output is
  manual-review / not v1-safe** until the sell-side validator exists
- [ ] Operator has **manually reviewed every order row** (ticker, shares, limit price, intent)
  before broker entry

If any item fails, the run is **NO_TRADE / manual review** — do not place orders.

## 5. NO_TRADE / blocked checklist

Treat the run as **NO_TRADE / blocked** if any of these are true:

- Research is missing, stale, invalid, or produced no output (Step 1 state not `STRICT_FRESH`).
- Any `*blocked*.json` artifact is present in a Step 2/3/4 directory.
- `run_summary.json` → `run_blocked` is **true**.
- `run_summary.json` → `manual_review_required` is **true**.
- A Step 4 validation failure / quarantine artifact exists (canonical Step 4 files not published).
- `SELL_ORDERS` is **non-empty**.

NO_TRADE is a valid, safe outcome. Missing one trade is preferable to trading on bad data.

## 6. What to inspect

- `artifacts/current/run_summary.json`
- `artifacts/current/step1_research/research_degraded_mode_decision.json`
- `artifacts/current/step2_decision_builder/step2_blocked_by_research_gate.json`
- `artifacts/current/step3_audit_engine/step3_blocked_by_upstream_gate.json`
- `artifacts/current/step4_order_compiler/step4_blocked_by_upstream_gate.json`
- `artifacts/current/step4_order_compiler/step4_blocked_by_final_execution_safety_gate.json`
- `artifacts/current/step4_order_compiler/template4_orders.txt`
- `artifacts/current/step4_order_compiler/order_state_export.txt`
- `artifacts/current/step4_order_compiler/exec_summary.txt`
- `artifacts/current/step4_order_compiler/quarantine/` — present only if validation/publish failed
  (holds the rejected candidate set for diagnosis/recovery)

A blocked artifact's `blocked_by_artifact` / `upstream_permission` / `fail_reasons` fields trace the
cause back to the originating stage.

## 7. What v1 guarantees

- Missing / stale / invalid / no-output research **fails closed to blocked / NO_TRADE.**
- **LLM booleans are necessary but not sufficient** — the final execution safety gate independently
  requires fresh valid permission + structured upstream packets + no blockers.
- Step 4 order drafts are **validated deterministically before canonical publish.**
- Rejected outputs are **quarantined**; a prior-good canonical set is never overwritten by a
  rejected run.
- Buy-side **structure / numeric / universe-allowlist / submit-side + total open-order budget /
  KEEP_EXISTING-vs-snapshot / per-bucket new-ticker** constraints are enforced.

## 8. Explicit non-goals

- **No broker / live execution** — the workflow never submits or cancels orders.
- **No sell-side validator yet** — `SELL_ORDERS` is not deterministically validated; non-empty sell
  output is manual-review (see `post_order_validation_gate_design.md` §9 for the trigger/design).
- **No guarantee against operator manual broker-entry error** — the operator is responsible for
  faithful order entry.
- **No broker-confirmed state reconciliation** — existing open-order state is trusted from the
  operator portfolio snapshot, not from broker truth.
- **No tax advice** — LTCG/lot handling is mechanical, not a tax recommendation.

## 9. Ready vs NO_TRADE decision rule

> **Ready for manual order review only if every item in the §4 acceptance checklist passes**
> (`run_summary.run_blocked = false`, `recommended_result ≠ NO_TRADE`, no `*blocked*.json` artifacts,
> all three canonical Step 4 files present, `run_step4 parse` exited cleanly, `SELL_ORDERS` empty,
> and the operator has reviewed each order row).
>
> **Otherwise, default to NO_TRADE / manual review.** When in doubt, do not trade.
