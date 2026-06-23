# Post-Order Deterministic Validation — Inspection & Design (PR G0)

**Status: DESIGN / INSPECTION ONLY.** This document changes no production behavior, prompt, Step
1/2/3/4 investment logic, order compiler, or broker/live-execution logic, and enables no new
enforcement gate. It records the real state of Step 4 order-output validation and proposes the
next minimal safety PR (G1).

---

## 1. Current Step 4 order output path

Inspected: `workflow/step4_order_compiler.py`, `parsers/extract_orders_and_summary.py`,
`validators/validate_orders_output.py`, `validators/validate_audited_decision_packet.py`,
`cli/run_step4.py`, `tests/unit/test_core_deployment_diagnostics.py`.

Flow (manual, operator-driven):

```
run_step4 parse
  → enforce_step4_upstream_guard()              (PR E0/E1: fail closed on upstream block)
  → enforce_step4_final_execution_safety_gate() (PR F: deterministic pre-compile gate)
  → ensure_order_compiler_ready(audited_packet) (Step 3 LLM bools audit_passed/order_compiler_ready)
  → extract_orders_and_summary(..., audited_decision_packet=audited_packet)
        → parse TEMPLATE4_ORDERS / ORDER_STATE_EXPORT / TEMPLATE5_EXEC_SUMMARY markers
        → WRITE the three text artifacts
        → validate_orders_output(..., audited_decision_packet)   # runs automatically
```

Output artifacts (text, not JSON; manual broker entry by the operator):
`artifacts/current/step4_order_compiler/template4_orders.txt`, `order_state_export.txt`,
`exec_summary.txt`.

**There is no broker / live-order / automated execution path in this repo.** Orders are text the
operator places manually. So the "live-order path" risk is an operator acting on invalid order
text, and the deterministic guard immediately before that is `validate_orders_output`.

## 2. Existing validator behavior (`validate_orders_output`)

- **Automatic, not optional, fail-closed** in the primary path: `parse_step4_output` always calls
  it (via `extract_orders_and_summary`) **with** the audited packet, and it raises on failure.
- Checks today:
  - All three artifacts exist and are non-empty.
  - With the audited packet: every `BUY_ORDERS` row ticker must be a **compile-ready buy-side
    `final_execution_plans`** entry, and `order_intent` must be consistent with `final_action`
    (`_require_buy_order_rows_match_final_plans`). A BUY row with no matching compile-ready plan
    fails → this also means **no compile-ready buy plans ⇒ no BUY rows allowed** (a partial
    "no-trade ⇒ no orders" guarantee).
  - `exec_summary` must echo `core_deployment_diagnostics` fields + the WEEKLY_REVIEW_NEEDED
    disclaimer (`_require_diagnostic_summary`).
- Two weaknesses in *where/how* it runs:
  1. **write-then-validate ordering**: the three artifacts are written **before** validation runs,
     so on a validation failure the (rejected) files remain on disk (parse still exits non-zero).
  2. **standalone CLI path is weaker**: `extract_orders_and_summary.main()` calls the validator
     **without** an audited packet, so the substantive cross-checks are skipped (only existence /
     non-empty). The primary `run_step4 parse` path is unaffected (it passes the packet).
- **The validator never reads `strategy_settings` or the Step 2 `effective_allowed_buy_universe`.**
  Ticker membership is checked only against the **audited packet's** compile-ready plans — i.e.
  it transitively trusts that Step 3's LLM did not admit an out-of-universe ticker.

## 3. Validation coverage matrix

Legend: **A** already enforced deterministically (auto + fail-closed) · **B** exists but
optional/manual-only · **C** missing, should be added · **D** ambiguous / needs a design decision.

| Check | Class | Where / note |
|---|---|---|
| Text structure / markers parse | **A** | parser requires markers; validator requires non-empty |
| Ticker ∈ compile-ready `final_execution_plans` | **A** | `_require_buy_order_rows_match_final_plans` (tested) |
| Ticker ∈ strategy `effective_allowed_buy_universe` / Step 1 `trade_universe` | **C** | not checked; transitive trust on Step 3 LLM |
| Side / action validity (intent ↔ final_action) | **A** | `ROW_INTENTS_BY_FINAL_ACTION` (tested) |
| Quantity / dollar-amount numeric validity | **C** | rows parsed but numeric fields unvalidated |
| Hard cap open orders budget (`hard_cap_open_orders_budget`) | **C** | validator has no budget awareness |
| Target new-buy budget | **C** | not checked |
| `max_new_tickers_per_week` | **C** | not checked |
| Duplicate ticker / action conflict | **C** | multiple rows per ticker not flagged |
| No-trade ⇒ no orders | **A (partial)** | enforced when audited packet has no compile-ready buy plans (tested); export/summary no-trade semantics not deeply checked |
| `manual_review_required` blocks orders | **A (upstream)** | PR F final safety gate, before generation |
| Degraded / non-`STRICT_FRESH` blocks orders | **A (upstream)** | PR F final safety gate |
| Step 3 `blocker_reasons` / final-gate blocker blocks orders | **A (upstream)** | PR F final safety gate |
| Cash / budget constraints | **C** | not checked |
| Extended-ETF admission constraints | **D** | relies on upstream gates + audited packet; post-order check undecided |
| Live-order flag safety | **D / N-A** | no live/broker path exists; manual text only |
| Audit-trail / source provenance | **B/D** | cross-references audited packet; no explicit provenance record |

## 4. Existing test coverage

- `test_core_deployment_diagnostics.py` exercises `validate_orders_output`:
  - buy orders only from executable compile-ready final plans (ticker membership vs packet),
  - diagnostics do not generate buy orders (a no-orders-without-plans case),
  - `CANCEL_EXISTING` rows are valid buy-side actions,
  - exec_summary must carry diagnostics.
- **No test proves**: a budget/hard-cap cannot be exceeded; `max_new_tickers_per_week` cannot be
  exceeded; a duplicate ticker/action is rejected; a non-numeric / negative quantity is rejected;
  a ticker outside the **strategy** universe (but present in the audited packet) is rejected.
- There is no automated live-execution path, so "invalid orders cannot reach live execution" is
  currently a property of the manual process + `validate_orders_output`, not of a broker gate.

## 5. Key risks

1. **Budget / sizing blindness (highest):** order text could exceed `hard_cap_open_orders_budget`
   or `max_new_tickers_per_week`, or carry malformed/negative quantities, and
   `validate_orders_output` would not catch it.
2. **Strategy-universe trust:** ticker allowlisting is only vs the audited packet's compile-ready
   plans, not the strategy universe; an upstream LLM error that admits an out-of-universe ticker
   into the plan would pass.
3. **No duplicate detection:** conflicting/duplicate rows per ticker are not flagged.
4. **write-then-validate:** rejected artifacts persist on disk (minor; exit code is non-zero).
5. **Weaker standalone CLI path:** `extract_orders_and_summary.main()` skips substantive checks.

None of these are live-execution bugs today (no broker path), but they weaken the last
deterministic checkpoint before manual order entry.

## 6. Proposed next PR — Recommendation: **Option C (strengthen validator coverage first)**

The validator is **already automatic and fail-closed in the primary path** (so a pure enforcement
gate, Option B, is largely already in place), but its **coverage is incomplete** (many **C** rows
above). Per the stated preference — "if validator coverage is incomplete, first do C" — the next
minimal PR should **strengthen `validate_orders_output` coverage with deterministic checks + tests**,
without changing workflow wiring or order-compiler behavior.

**PR G1 (proposed) scope:**
- Add deterministic, pure checks to `validate_orders_output` (or a focused helper), fed the inputs
  it already has access to at the call site (`audited_decision_packet`) plus newly-plumbed
  read-only context (`strategy_settings`, Step 2 `effective_allowed_buy_universe`):
  - numeric validity of quantity / dollar fields (present, parseable, non-negative),
  - duplicate ticker/action-conflict detection,
  - `hard_cap_open_orders_budget` aggregate ceiling,
  - `max_new_tickers_per_week` ceiling,
  - strategy-universe allowlist cross-check (ticker ∈ `effective_allowed_buy_universe`).
- Add unit tests proving each new check rejects the bad case and passes the good case.
- Keep it deterministic; reuse existing fail-closed raise behavior (already wired into
  `parse_step4_output`).

**Deliberately deferred (not G1):**
- Fixing write-then-validate ordering (validate-before-write) — small follow-up (G2).
- Hardening the standalone `extract_orders_and_summary.main()` path — small follow-up.
- A report-only `step4_order_validation.json` artifact — optional UX follow-up; not required for
  safety since the validator already fail-closes.

Why not A/B:
- **B (enforcement gate):** the enforcement wiring already exists; bolting on a second gate without
  first widening coverage would add structure but not catch the real gaps (budget/sizing).
- **A (report-only artifact):** lower value than C because the validator already raises; a
  report-only artifact would observe, not strengthen, the weak coverage.

## 6a. G1 implementation status

PR G1 implemented the following deterministic checks in `validate_orders_output` (additive; the
existing compile-ready / diagnostics checks are unchanged; still fail-closed in the primary
`run_step4 parse` path):

- **Implemented:** numeric validity (malformed / negative / zero-on-submit `shares` & `limit_price`);
  exact-duplicate row rejection (`ticker`+`plan_type`+`step_name`+`order_intent`); universe
  allowlist for submit/new legs; budget ceiling; `max_new_tickers_per_week` ceiling on distinct
  net-new buy tickers.
- **Universe source (primary path):** the validator prefers the run-specific
  `effective_allowed_buy_universe` from the Step 2 decision packet (a validated, typically stricter
  subset), and `parse_step4_output` now wires it in; it falls back to the static strategy-settings
  floor (`core_universe` + `satellite_universe` + `user_approved_extended_etf_static_list`) when the
  per-run universe is unavailable. So the primary path enforces the **stricter** per-run universe
  when present and never weaker than the static floor.
- **Budget semantics (important):** `hard_cap_open_orders_budget` in `strategy_settings.yaml` is a
  ceiling on **total intended open-order exposure** — per the order-compiler prompt's
  `aggregate_notional_counting_rule`, it is compared against `total_target_open_order_budget` /
  `total_compiled_open_order_notional`, which include KEEP_EXISTING existing notional. The G1 check
  is therefore **submit-side new-order notional validation only**: it recomputes
  `Σ(shares × limit_price)` over submit/new legs (cancel legs excluded) and fails if that alone
  exceeds the cap. This is a sound one-directional safety check (new submits alone over the total
  cap ⇒ definite violation) but is **partial**; full open-order-state reconciliation (adding
  existing kept notional) is deferred to G2.
- **Scoping (safety):** universe / budget / new-ticker checks apply only to submit/new legs — never
  to cancel legs — because cancelling an out-of-universe ticker (e.g. GRID/CIBR removal) is valid,
  and the same ticker legitimately spans many ladder rows.
- **Deferred to G2:** full open-order-state budget reconciliation (adding existing kept notional to
  the submit-side total); per-bucket `max_new_tickers_per_week` (base vs extended); semantic
  conflicting-action detection beyond exact duplicates; validate-before-write ordering; hardening
  the standalone `extract_orders_and_summary.main()` path (still weaker by design).

## 7. Non-goals

- No change to order-compiler output format, order generation, or investment sizing logic.
- No prompt changes; no change to Step 1/2/3/4 decision semantics.
- No broker / live-execution integration.
- No new fail-closed gate beyond the existing `validate_orders_output` raise (G1 widens what that
  existing checkpoint verifies; it does not add a separate gate).

## 8. Rollback

- This PR (G0) is docs-only: delete `docs/post_order_validation_gate_design.md` and its
  docs-content test.
- For the future G1: the new checks are additive validation; rollback by reverting the validator
  changes and tests. Because validation already runs in the enforced path, reverting G1 returns to
  today's (narrower) checks without structural change.
