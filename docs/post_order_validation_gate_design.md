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
  `total_compiled_open_order_notional`, which include KEEP_EXISTING existing notional. The **G1**
  check is a **submit-side new-order notional floor**: it recomputes `Σ(shares × limit_price)` over
  submit/new legs (cancel legs excluded) and fails if that alone exceeds the cap — a sound
  one-directional check but partial.
- **Total open-order exposure reconciliation (G3 implemented):** `validate_orders_output` now, when
  both an audited decision packet and a hard cap are supplied, **recomputes totals from
  `audited_decision_packet.final_execution_plans`** (the structured, authoritative source) — not
  from the exec_summary `aggregate_summary` PASS flags, which are LLM/compiler-restated diagnostics
  and are **not** trusted as validator evidence. Over `compile_ready` plans it parses
  `compiled_open_order_notional` and `target_open_order_budget` fail-closed (non-negative Decimals),
  then requires `Σ target ≤ hard_cap` and `Σ compiled ≤ Σ target`. This captures the KEEP_EXISTING
  existing notional the submit-side floor omits. For NEW_ORDER / REPLACE_EXISTING plans it
  cross-checks the actual BUY submit rows (`Σ shares × limit_price`, ladder rows summed per ticker)
  against the plan's `compiled_open_order_notional` within a cents tolerance; KEEP_EXISTING and
  CANCEL_EXISTING are not cross-checked. The G1 submit-side floor is retained as an additional
  independent check.
  - **KEEP_EXISTING independent verification (G4 implemented):** KEEP_EXISTING existing notional is
    no longer trusted from the audited packet alone. A pure parser
    (`parsers/portfolio_snapshot_existing_orders.py`) extracts section **(2a)
    existing_buy_open_orders_summary** of `inputs/current/portfolio_snapshot.txt` — the
    operator-maintained **SSOT for buy-side existing open orders** — and `validate_orders_output`
    cross-checks, for each compile-ready `KEEP_EXISTING` plan: audited `existing_open_order_budget`
    vs snapshot `budget`; audited `compiled_open_order_notional` vs the snapshot's stated /
    reconstructed `Σ(step_qty × step_limit_price)` notional; and the snapshot's own stated vs
    reconstructed notional — all within a cents tolerance. It **fails closed** when (2a) is missing
    while KEEP plans exist, a KEEP ticker is absent from (2a), the (2a) row is parse-blocked, or any
    value disagrees. The primary `parse_step4_output` path always parses (2a) and passes it;
    non-primary/standalone callers that omit the context skip only G4 (backward compatible).
    `order_state_export.txt` is **not** used as an independent source — it is Step 4 *output*
    (downstream of the same LLM), usable only as a restated cross-check.
  - **Residual limitations:** G4 verifies KEEP existing notional only for tickers present in (2a)
    against the operator-provided values (it does not re-derive holdings from broker truth);
    NEW_ORDER / REPLACE_EXISTING are covered by the G3 submit-side cross-check, and CANCEL_EXISTING
    contributes zero. Broader portfolio accounting beyond buy-side (2a) is out of scope.
- **Scoping (safety):** universe / budget / new-ticker checks apply only to submit/new legs — never
  to cancel legs — because cancelling an out-of-universe ticker (e.g. GRID/CIBR removal) is valid,
  and the same ticker legitimately spans many ladder rows.
- **Implemented in G2 (validate-before-write / quarantine):** `extract_orders_and_summary` now
  writes candidate artifacts to a `quarantine/` subdirectory, validates the quarantine paths with
  the same G1 context (validator API unchanged), and publishes the canonical
  `template4_orders.txt` / `order_state_export.txt` / `exec_summary.txt` **only after validation
  passes**. On validation failure the exception propagates, canonical artifacts are never written
  or overwritten (a prior-good set is preserved byte-for-byte), and the rejected candidates remain
  under `quarantine/` as diagnostics. On success the three quarantine files are removed and the
  `quarantine/` directory is removed if empty.
- **Canonical publish atomicity (G2.2 — implemented):** once validation passes, each canonical Step 4
  artifact (`template4_orders.txt`, `order_state_export.txt`, `exec_summary.txt`) is published via
  `atomic_write_text` (`common/io.py`): the bytes are written to a temp file **in the same directory**,
  flushed and `fsync`-ed, then `os.replace`-d onto the target. Because the rename is same-filesystem and
  atomic, a reader sees either the complete prior file or the complete new file — **never partially
  written content**, even on a mid-publish crash. On any write/replace failure the temp is removed
  best-effort and the target is left untouched (absent or its prior content). **Per-file atomic, not
  group-atomic:** the three replaces are still independent, so a crash *between* them can leave a *mixed*
  set (one file updated, the others stale/absent) — but no individual file is ever partial. The
  validate-before-publish ordering still guarantees only validated content is published, and because
  quarantine cleanup runs only after all three replaces succeed, the **full validated set remains under
  `quarantine/` for recovery** on a mid-publish failure. True group-level (all-or-nothing) publish via a
  manifest / versioned-directory swap remains a possible **future** improvement; it is not implemented
  and is intentionally not over-engineered here.
- **Total open-order-state reconciliation:** implemented in **G3** (see the budget-semantics /
  G3 reconciliation bullets above) — totals are recomputed from `final_execution_plans` and
  reconciled against the hard cap, including KEEP_EXISTING existing notional.
- **Per-bucket `max_new_tickers_per_week` (implemented):** `validate_orders_output` enforces the
  base and extended new-ticker ceilings **separately**, not only the aggregate sum, via
  `_validate_per_bucket_new_tickers(buy_order_rows, strategy_settings)` (runs whenever strategy
  settings are supplied). Limits come from
  `max_new_tickers_per_week.base_universe_new_tickers_per_week` /
  `.extended_etf_sleeve_new_tickers_per_week`. **Classification source = the operator
  strategy-settings universe lists** (base = `core_universe ∪ satellite_universe`; extended =
  `user_approved_extended_etf_static_list`) — chosen over `final_execution_plans.role_layer` (which
  can be null) and over `effective_allowed_buy_universe` (a flat membership list that does not label
  base vs extended). Only distinct net-new tickers count (NEW_ORDER side; ladder rows count once;
  REPLACE/CANCEL/KEEP excluded). **In-both:** a ticker in both lists is conservatively counted as
  **base** (documented as a settings inconsistency). **In-neither:** a net-new ticker absent from
  both lists **fails closed**. Malformed/missing `max_new_tickers_per_week` sub-keys (when settings
  are present) **fail closed**; an absent `max_new_tickers_per_week` skips only the per-bucket check.
  The aggregate `_validate_max_new_tickers` (int param) is **retained unchanged** for
  backward-compatible legacy/standalone callers.
- **Conflicting-action detection (G1.1 implemented):** `_validate_no_conflicting_buy_actions`
  runs **always** (pure, no external context), additive to the exact-duplicate check. It fails
  closed on two narrow, format-safe conflict classes: (a) **action conflict** — the same ticker
  carries both a net-new buy leg (`NEW_ORDER` / `BUY` / `SUBMIT_BUY` / `EXECUTE_BUY`) and a
  *standalone* `CANCEL_EXISTING` leg (coordinated `REPLACE_EXISTING_*_LEG` pairs are intentionally
  exempt); and (b) **slot intent conflict** — the same `(ticker, plan_type, step_name)` ladder slot
  appears with two or more distinct non-empty `order_intent` values. Multi-step ladders (distinct
  `step_name`) and replace cancel/submit legs (distinct `plan_type`) occupy different slots and pass.
- **Nonblank BUY intent (implemented):** every parsed `BUY_ORDERS` row must contain a nonempty
  `order_intent`. Missing, malformed-without-a-key, empty, and whitespace-only values reject the
  complete candidate before submit classification, arithmetic, duplicate/conflict analysis, or
  final-plan compatibility checks. Existing nonblank normalization and aliases are unchanged.
- **Fail-closed on missing safety context (G1.1 implemented):** an opt-in
  `require_safety_context` flag makes `validate_orders_output` fail closed when BUY **submit** rows
  are present but the allowed universe / `hard_cap_open_orders_budget` is missing, or when net-new
  rows are present but no `max_new_tickers_per_week` ceiling (aggregate int or per-bucket mapping) is
  supplied. The primary `parse_step4_output` path opts in (via `extract_orders_and_summary`), so a
  settings file missing a budget / universe / new-ticker ceiling now fails closed rather than
  silently skipping the corresponding check while real BUY rows exist. Cancel-only output (no
  submit/new legs) requires no budget/universe context. The flag defaults `False`, so standalone
  callers keep the prior lenient skip-when-context-missing behavior — and are therefore **not** a
  complete safety validator (documented in the function docstring and covered by a regression test).
- **`target_new_buy_budget_this_run` (source defined in §10; wired in G5 — implemented):**
  `validate_orders_output` accepts and enforces this ceiling; the deterministic operator source is the
  `target_new_buy_budget_this_run` top-level key in `inputs/current/strategy_settings.yaml` (USD,
  non-negative), and the primary `parse_step4_output` path now wires it
  (`strategy_settings.get("target_new_buy_budget_this_run")`). It bounds **net-new** buy notional only
  (replacement / cancel / keep legs excluded); the hard cap still bounds broader exposure. Under
  `require_safety_context` it fails closed when net-new BUY rows exist but the key is missing. See §10
  for the source design and §10.8 for the implementation summary.
- **Standalone extractor CLI safety gate (G6 — implemented):** the standalone
  `extract_orders_and_summary.main()` CLI is parser-development / debugging only and is **not** the
  primary Step 4 safety path. It no longer silently runs weaker validation: by **default it refuses**
  (prints a message directing the operator to `run_step4 parse`, **writes nothing**, exits non-zero).
  Weaker parse-only behavior (`require_safety_context=False`, no settings/budgets/universe/audited
  packet) runs **only** behind the explicit `--unsafe-parse-only` flag. It creates no artifact files
  or directories and emits exactly one code-owned `step4_unsafe_parse_only_stdout_v1` JSON document
  to stdout. Legacy caller-selected output options are rejected rather than ignored. The JSON and
  stderr warning state that the output is unvalidated, non-authoritative, not manual-order-ready,
  and not broker-ready. Redirecting stdout does not make the JSON a canonical Step 4 artifact; users
  must never redirect it into canonical paths and treat it as validated. The unsafe path never calls
  the quarantine-to-canonical publisher. The authoritative `extract_orders_and_summary` function API is unchanged,
  as is the primary `run_step4 parse` path; only that validated workflow may create canonical Step 4 artifacts.
- **Still deferred:** true group-level (all-or-nothing) multi-file publish (per-file atomic publish is
  implemented in **G2.2** — see above; wiring `target_new_buy_budget_this_run` is implemented in **G5**
  — see §10; standalone CLI safety gate is implemented in **G6** — see above).

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

---

## 9. Sell-side validation (deferred, non-blocking hardening — NOT a v1 blocker)

Manual-order Step 4 safety v1 is **buy-side**. Sell-side deterministic validation is intentionally
deferred; this section records the current state, risks, smallest future design, and an explicit
implementation trigger so readiness is preserved without speculative code.

### 9.1 Current state

- `validate_orders_output` validates **`BUY_ORDERS` only**. `SELL_ORDERS` appears solely as a
  section boundary in the buy-section regex and is otherwise unparsed/unchecked.
- `SELL_ORDERS` has been **dormant in all observed runs** (every archived `template4_orders.txt`
  emits `SELL_ORDERS = NONE` / `[]`).
- `audited_decision_packet.final_sell_execution_plans` has been **empty in all observed audited
  packets** (`sell_no_action_summary` indicates no sell action).

### 9.2 Why this is acceptable for v1

- The current strategy is **long-biased / accumulation-oriented** with LTCG-only sells.
- **No sell orders have been generated** in any observed run (the sell path has never fired), so
  there is no current safety gap.
- **Buy-side order-output safety v1 is complete** (numeric, duplicate, universe, submit-side and
  total-exposure budget, per-bucket new tickers, KEEP_EXISTING verification, validate-before-write).

### 9.3 Sell-side risks if/when `SELL_ORDERS` becomes non-empty

- **Oversell** relative to sellable quantity (`shares_to_sell` exceeds the lot's `lt_shares_sellable`).
- **Ineligible lot:** selling a `lot_id` not present in portfolio snapshot section
  `(3) LTCG_ELIGIBLE_SELLABLE` (the only allowed sell source), or selling when `(3)` is empty.
- **Duplicate / double-allocated** sell rows draining the same lot beyond its sellable quantity.
- **Conflicts with existing sell open orders** from section `(2b) sell_open_orders`.
- **Buy and sell the same ticker in one run** (wash-sale / contradiction risk).
- **Tax-lot mismatch:** `acquired_before_date` / `lot_selection_mode` inconsistent with the `(3)` lot.

These are tax- and position-correctness risks; they are quantity/lot-based (sell `limit_price` may
be null, so sell notional is not always computable).

### 9.4 Smallest future design

- Parse portfolio snapshot section `(3) LTCG_ELIGIBLE_SELLABLE` as the **sellable-lot SSOT**
  (mirroring the G4 `(2a)` parser); optionally parse `(2b) sell_open_orders` for existing live sells.
- Validate each `SELL_ORDERS` row by `lot_id`, `ticker`, `shares_to_sell`, `lot_selection_mode`, and
  `acquired_before_date` against the `(3)` lot.
- Enforce `Σ shares_to_sell` per `lot_id` ≤ `lt_shares_sellable`.
- **Fail closed** when `SELL_ORDERS` is non-empty but section `(3)` is missing / malformed.
- Gate the check to run only when `SELL_ORDERS` has rows, so the dormant `NONE` path is unaffected.

### 9.5 Explicit trigger

- **Implement sell-side validation before accepting any run with a non-empty `SELL_ORDERS` or a
  non-empty `final_sell_execution_plans`.**
- Until that validator exists, **any non-empty sell output must be treated as manual-review /
  not v1-safe.**

---

## 10. `target_new_buy_budget_this_run` source design (source design — **IMPLEMENTED in G5**)

**Status: source design recorded here; IMPLEMENTED in G5 (see §10.8).** This section records *where*
the validator's `target_new_buy_budget_this_run` ceiling comes from, how it is represented, when it
applies, and how the validator consumes it. The **design** itself changed no prompt, no Step 1/2/3/4
investment semantics, no order compiler, and no broker/live path, and added no separate gate; the
**G5** implementation (§10.8) wires the operator value into the *existing* post-order validator only —
it does not change order generation, sizing, or any LLM decision. (The operator runbook called this
work item "G2 budget source"; because the doc's `G2` label is already used for validate-before-write /
quarantine, the implementation PR is labelled **G5** to keep the PR sequence unambiguous.)

### 10.1 Current state (inspected)

- `validate_orders_output(..., target_new_buy_budget_this_run=None)` **already accepts and enforces**
  this ceiling (`_validate_buy_budget`): it recomputes `Σ(shares × limit_price)` over buy-side
  submit legs and fails closed if that exceeds the supplied number. `extract_orders_and_summary`
  also already threads the parameter through. **It is never supplied a value** — the primary
  `parse_step4_output` path passes `hard_cap_open_orders_budget` (from `strategy_settings.yaml`) and
  `max_new_tickers_per_week`, but **not** `target_new_buy_budget_this_run`, so the check is inert.
- **There is no operator-controlled, deterministic source for it.** `strategy_settings.yaml` has no
  such key (only `hard_cap_open_orders_budget: <number>`), and the strategy-settings validator is
  **permissive** — it ignores unknown top-level keys — so adding the key needs **no schema change**.
- **Budget data that already flows (all LLM-computed, not operator-controlled):** the Step 2
  `decision_packet.json` `input_normalization` block carries `hard_cap_open_orders_budget` (echoed
  from settings), `existing_buy_open_orders_budget_total`, `proposed_buy_open_orders_budget_total`,
  and `open_order_budget_headroom_after_actions`; Step 3's audited packet carries the same plus
  `proposed_buy_open_orders_budget_total_after_audit` / `open_order_budget_headroom_after_audit`, and
  per-plan `target_open_order_budget` / `existing_open_order_budget` / `delta_budget`. These are
  **totals / per-ticker targets** (≈ the G3 `total_target_open_order_budget` quantity), not a clean
  "net-new deployment this run" flow, and they are **produced by the Step 2/3 LLM**.
- **No cash / account-balance input exists** anywhere (the portfolio snapshot tracks holdings,
  existing open orders, and sellable lots only). A cash-based deterministic derivation is therefore
  not possible today without a new input.

### 10.2 Candidate sources compared

| Opt | Source | Deterministic? | Pros | Cons |
|---|---|---|---|---|
| **A** | New operator key in `strategy_settings.yaml` | Yes | Sits beside `hard_cap_open_orders_budget` (same owner/units/loader); already-loaded in Step 4; permissive schema ⇒ no schema change; one-line wiring; git-auditable | Operator must update it per run (same staleness risk `hard_cap_open_orders_budget` already has) |
| **B** | New per-run input file `inputs/current/weekly_budget.yaml` | Yes | Explicit per-run artifact; isolates "policy" from "per-run input" | New file + loader + parser + new missing-file failure mode; the "don't make settings weekly-mutable" rationale is **weak** because `strategy_settings.yaml` already carries `as_of` / `run_timestamp_et` and a per-run `hard_cap_open_orders_budget` (it *is* already a per-run operator file) |
| **C** | Deterministic derivation (e.g. `hard_cap − existing_buy_open_orders_budget_total`) | Partly | No new input | Equals full hard-cap headroom ⇒ never binds tighter than the G3 total-exposure check, so it is **not a meaningful independent throttle**; the headroom figure is itself derived from LLM `proposed_*` totals; needs more design and a cash input to be a real "deployable" number |
| **D** | Explicit operator per-run override (a flag/field the operator sets each run) | Yes | Safest, fully operator-controlled | Equivalent to A or B depending on where it lives; on its own adds plumbing without deciding the home |
| **E** | Hybrid clamp: validator enforces `min(operator_target, hard_cap headroom)` and treats the Step 2/3 `proposed_*` as a *proposal* to cross-check, never the authority | Yes (authority) | Matches the stated preference: LLM may propose, deterministic operator value binds; uses artifacts that already exist | More moving parts; best delivered *after* A establishes the authoritative operator value |

### 10.3 Recommended source — **Option A**, governed by the **Option E** philosophy

Add an **operator-controlled key to `strategy_settings.yaml`** as the single authoritative,
deterministic source, and have the validator enforce it. The Step 2/3 LLM `proposed_*` totals are
treated as a *proposal* only — the deterministic operator value clamps/rejects, never the reverse.

Rationale:

1. `strategy_settings.yaml` is **already a per-run operator file** (`as_of`, `run_timestamp_et`, and a
   per-run `hard_cap_open_orders_budget`), so a per-run budget belongs there — Option B's "avoid
   weekly-mutable settings" benefit does not really apply.
2. The sibling concept `hard_cap_open_orders_budget` already lives there with the **same units, owner,
   and update cadence**, and Step 4 already loads settings and passes the hard cap to the validator —
   so wiring is **one line** (`strategy_settings.get("target_new_buy_budget_this_run")`).
3. The settings validator is **permissive**, so **no schema change** is required.
4. It satisfies the explicit preference: **the LLM is not the sole authority** — the operator value is
   deterministic and authoritative; Step 2 may *propose* (`proposed_buy_open_orders_budget_total_after_audit`)
   but the validator clamps/rejects against the operator value.

### 10.4 Field specification

- **Exact field name:** `target_new_buy_budget_this_run` (matches the existing validator parameter, so
  wiring is a direct pass-through).
- **Location / artifact:** top-level key in `inputs/current/strategy_settings.yaml`, adjacent to
  `hard_cap_open_orders_budget`.
- **Type / units:** a single non-negative number (int or float; `Decimal`-parseable), **US dollars** of
  **net-new** buy-side open-order *notional* intended this run. Example: `target_new_buy_budget_this_run: 5000.00`.
- **Required vs optional:** an *optional* key in the file, but **conditionally required at validation
  time**: when the run emits net-new BUY submit rows and `require_safety_context=True` (the primary
  `parse_step4_output` path), a missing value must **fail closed** — mirroring how the hard cap /
  universe are already required when submit rows exist, and how `max_new_tickers_per_week` is required
  when net-new rows exist. Standalone callers (`require_safety_context=False`) keep the lenient
  skip-when-missing behavior.
- **Default behavior when missing:** no implicit default and **no derivation** — absence with net-new
  rows under `require_safety_context` is a fail-closed error (operator must set it); absence with no
  net-new rows is a no-op (the check is vacuous).

### 10.5 Semantics and interactions

- **Net-new vs replacement (decision):** `target_new_buy_budget_this_run` should bound **net-new legs
  only** (`NEW_ORDER` / `BUY` / `SUBMIT_BUY` / `EXECUTE_BUY`), **excluding** `REPLACE_EXISTING_*` and
  `CANCEL_EXISTING`. A replacement re-submits an already-budgeted existing order at a new anchor (the
  drift policy mandates "same remaining budget only … no budget increase"), so it is **not** new
  capital and is governed by the *hard cap* (total stock), not by the new-buy *flow*. **Implementation
  note:** the current `_validate_buy_budget` measures the broader **submit-side** notional (which
  includes `REPLACE_EXISTING_*` legs). The G5 PR must therefore either (a, recommended) add a
  net-new-only notional helper and apply the target ceiling to that, or (b) keep the broader
  submit-side notional and document the ceiling as the conservative "net-new + replace-submit" total.
  Option (a) is the clean semantic and a small, contained change.
- **Interaction with `hard_cap_open_orders_budget`:** **independent and additive — both must hold.**
  The hard cap bounds *total* intended open-order exposure (stock, incl. KEEP_EXISTING; enforced by
  the G3 reconciliation from `final_execution_plans`). `target_new_buy_budget_this_run` bounds *net-new
  deployment this run* (flow). The operator should set it ≤ available headroom
  (`hard_cap − existing kept notional`); if it is set larger than the hard cap, the hard cap simply
  binds first (harmless misconfiguration). The validator enforces the two ceilings separately.
- **Interaction with `max_new_tickers_per_week`:** complementary, different dimensions — the budget is
  a **dollar** ceiling on net-new deployment; `max_new_tickers_per_week` is a **count** ceiling on
  distinct net-new tickers (per-bucket base/extended). A run can be limited by either.
- **No-buy / no-order runs (NO_TRADE, cancel-only, KEEP-only):** **do not require it.** With zero
  net-new submit rows the recomputed net-new notional is `0`, so the check is vacuous and the field is
  not required even under `require_safety_context`.
- **Extended ETF sleeve:** `target_new_buy_budget_this_run` is a **single aggregate** dollar ceiling on
  *all* net-new deployment (base + extended combined). The extended sleeve's own limits
  (`sleeve_budget_cap_pct_of_total_open_orders`, `single_extended_etf_budget_cap_pct_of_total_open_orders`,
  `activation_minimum_effective_budget_pct_of_total_open_orders`) are expressed as **percentages of
  total open orders** and remain **prompt-enforced upstream (Step 2/3)** — the G5 PR does **not** fold
  them into the validator. The aggregate budget sits orthogonally above the per-sleeve pct caps.
- **LLM proposal cross-check (optional, Option E):** the G5 PR *may* additionally fail closed when the
  audited packet's `proposed_buy_open_orders_budget_total_after_audit` exceeds
  `target_new_buy_budget_this_run` (the LLM proposed more new deployment than the operator authorized).
  This is belt-and-suspenders on top of the recomputed-rows check and can be split into its own PR; the
  authoritative check remains the **deterministic recomputation of net-new submit notional vs the
  operator value** (never trusting an LLM-restated total).

### 10.6 Implementation PR — **G5 (Option A — implemented; see §10.8)**

Minimal, additive, deterministic — no prompt / compiler / investment-semantics change:

1. Add `target_new_buy_budget_this_run: <number>` to `inputs/current/strategy_settings.yaml`
   (operator-maintained; permissive schema ⇒ no settings-validator change required, though an optional
   non-negative-number check could be added).
2. In `parse_step4_output`, pass `target_new_buy_budget_this_run=strategy_settings.get("target_new_buy_budget_this_run")`
   into `extract_orders_and_summary` (one line; the parameter is already threaded to the validator).
3. Extend `_enforce_safety_context_present` so that, under `require_safety_context`, **net-new** BUY
   submit rows require a `target_new_buy_budget_this_run` (fail closed when missing) — gated exactly
   like `max_new_tickers_per_week` is today.
4. (Recommended, per §10.5) apply the target ceiling to a **net-new-only** notional rather than the
   broader submit-side notional.
5. Unit tests: net-new notional over/under the operator budget; replacement-only and cancel-only runs
   not gated by it; NO_TRADE not gated; `require_safety_context` fail-closed when net-new rows exist
   but the key is missing; backward-compat skip for standalone callers.

**Why not B / C:**

- **B (new `weekly_budget.yaml`):** its main rationale (keep `strategy_settings.yaml` non-weekly-mutable)
  does not hold — settings is already per-run — so it adds a file, loader, parser, and a missing-file
  failure mode for no real isolation benefit. Reconsider only if the operator inputs are later split into
  a dedicated per-run bundle.
- **C (deterministic derivation):** equals full hard-cap headroom, so it never binds tighter than the
  existing G3 total-exposure check (not an independent throttle), and the headroom it would derive from
  is itself an LLM-computed total. A genuine cash-based "deployable" derivation needs a cash input that
  does not exist today; defer.

### 10.7 Non-goals & rollback (this design)

- **Non-goals:** no change to the order-compiler output/format, prompts, Step 1/2/3/4 decision or
  sizing semantics, or the broker/live path; no new gate enabled by this section; the validator
  parameter and its `_validate_buy_budget` behavior are unchanged by this doc.
- **Rollback:** the source design above is docs-only. The G5 implementation (§10.8) is additive
  validation + one settings key + one wiring line; rollback by reverting those and removing the key.

### 10.8 G5 implementation status (implemented)

G5 wired the deterministic operator-provided per-run new-buy budget into the existing post-order
validator. **No prompt, Step 1/2/3 LLM decision semantics, order-compiler generation logic,
investment sizing, or broker/live path was changed; no new gate was added** — G5 only feeds an
operator value into `validate_orders_output` and widens what the already-enforced checkpoint verifies.

- **Source / location:** `target_new_buy_budget_this_run` is a top-level key in
  `inputs/current/strategy_settings.yaml`, adjacent to `hard_cap_open_orders_budget`. The
  strategy-settings validator is permissive, so **no schema change** was required. The committed value
  is a conservative operator placeholder (≈ half of current open-order headroom, well under the hard
  cap) and is meant to be reviewed each run; it is **not** derived from any LLM output.
- **Type / units:** non-negative USD number (parsed as `Decimal`).
- **Wiring (primary path):** `parse_step4_output` passes
  `target_new_buy_budget_this_run=strategy_settings.get("target_new_buy_budget_this_run")` into
  `extract_orders_and_summary` → `validate_orders_output`, with `require_safety_context=True`.
- **Net-new-only semantics:** the validator measures the ceiling against a **net-new-only** notional
  (`_net_new_buy_notional`, over `NEW_ORDER` / `BUY` / `SUBMIT_BUY` / `EXECUTE_BUY`). `REPLACE_EXISTING_*`,
  `CANCEL_EXISTING`, and KEEP are **excluded** — replacements recycle already-budgeted exposure and
  must not consume the per-run new-buy budget. Blank-intent BUY rows are invalid and fail before
  this arithmetic runs.
- **Hard cap unchanged:** `hard_cap_open_orders_budget` continues to bound the broader submit-side
  notional (and the G3 total-exposure reconciliation is unchanged). Replacement/cancel notional remains
  subject to the hard cap; G5 does **not** weaken it.
- **Missing-field fail-closed:** under `require_safety_context` (the primary path), net-new BUY rows
  with a missing `target_new_buy_budget_this_run` **fail closed**. No-buy (`NONE`), cancel-only, and
  replacement-only runs have no net-new rows and therefore do **not** require it. Standalone callers
  (`require_safety_context=False`) keep the lenient skip-when-missing behavior (unchanged).
- **Tests:** `tests/unit/test_validate_orders_output_safety.py` (net-new over/under budget;
  replacement-only over budget passes but still hard-capped; mixed replacement+net-new counts only
  net-new; hard cap independent; cancel-only excluded; `require_safety_context` fail-closed on missing
  target with net-new rows; no-buy / cancel-only / replacement-only not required; standalone skip) and
  `tests/unit/test_step4_target_budget_wiring.py` (primary path forwards the settings value with
  `require_safety_context=True`; the real settings file carries a non-negative value).

### 10.9 Operational note — per-run operator review (G5.1 / UX4, docs-only)

`target_new_buy_budget_this_run` is a **deterministic operator input that must be reviewed every
weekly run**; unlike a stale `hard_cap_open_orders_budget` (still backstopped by the G3 total-exposure
reconciliation), a stale/forgotten target budget silently **over-allows or over-blocks** net-new
deployment. To mitigate this operational (not code) risk, the operator-facing docs now include
explicit review guidance:

- The **weekly run operator runbook** (`weekly_run_operator_runbook.md` §2.2) and the **README**
  ("Budgets to review before each weekly run") list both budget keys as a pre-run review step and
  explain hard-cap-vs-target-budget, the net-new-only semantics, and the missing-field fail-closed
  behavior.
- **Do not treat the Step 2 / Step 3 LLM `proposed_*` budget as the authority** — automation must not
  infer `target_new_buy_budget_this_run` from it; the operator sets it explicitly each run. (The
  optional deterministic Step-2-proposal *cross-check* in §10.5 remains deferred.)
- This G5.1 / UX4 change is **docs-only**: no production code, prompt, workflow, investment logic,
  order-compiler, or order-generation behavior changed.
