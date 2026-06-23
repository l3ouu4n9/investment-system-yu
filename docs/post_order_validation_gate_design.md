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
- **Canonical publish atomicity (G2.1 note):** G2 prevents validation-failed artifacts from
  overwriting the canonical Step 4 artifacts. However, once validation passes, the three canonical
  writes (`template4_orders.txt`, `order_state_export.txt`, `exec_summary.txt`) are still
  **sequential and not cross-file atomic**. A rare mid-publish write/process failure (e.g. disk
  full, permission error, or process kill between writes) could therefore leave a **partial
  canonical set** (one file updated, the others stale or missing). In that case the exception
  propagates so the CLI exits non-zero, and — because quarantine cleanup runs only after all three
  canonical writes succeed — the **full validated set remains under `quarantine/` for recovery**.
  This is recoverability, not all-or-nothing canonical atomicity. Per-file `os.replace` publishing
  or a manifest-based atomic publish is deferred as optional **G2.2** hardening (independent of
  budget reconciliation).
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
- **Still deferred:** semantic conflicting-action detection beyond exact duplicates; hardening the
  standalone `extract_orders_and_summary.main()` context coverage (it shares the validate-before-write
  ordering but is still not supplied settings/budgets/universe — weaker by design); atomic publish /
  `os.replace` (G2.2).

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
