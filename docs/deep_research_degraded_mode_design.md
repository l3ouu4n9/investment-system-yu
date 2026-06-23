# Deep Research Dependency Reduction / Degraded-Mode Investment System — Design

**Status: DESIGN ONLY (roadmap PR A).** This document changes no production code, no prompt, no
Step 1/2/3/4 workflow, no validator, no normalizer, no investment logic, no order compiler, and
no order-generation logic. It specifies intended future PRs (B–F); none of them are implemented
here.

---

## 1. Problem statement

Step 1 Deep Research has become an unstable single point of dependency for the whole investment
pipeline. Recent runs show three recurring failure shapes: Deep Research does not run at all (no
raw output), it returns narrative / markdown lanes instead of a strict machine-readable handoff,
or it parses successfully yet fails strict handoff validation. The deterministic normalizer has
already proven it cannot recover strict handoff fields from narrative prose, and prompt
reinforcement can only reduce drift — it cannot guarantee the Deep Research service produces a
valid handoff on any given week.

The system must stop assuming Deep Research succeeds. When Deep Research is missing, invalid, or
stale, the pipeline must remain **safe, diagnosable, and degradable**: it should fall back to a
last-known-good handoff where appropriate, and otherwise default to **HOLD / NO_TRADE / manual
review** — never to a new buy, and never by asking a downstream LLM to invent the missing data.

This matches the target architecture for an LLM-driven, low/mid-frequency ETF allocation system:
the LLM proposes research and rationale; deterministic code owns contract validation, degraded
mode, freshness, and the execution release gate.

---

## 2. Current failure modes

Observed/derivable Step 1 states and how the pipeline reacts **today**:

| # | Failure mode | Detected by (today) | Current reaction | Risk |
|---|---|---|---|---|
| F1 | raw output **missing** | `step1_raw_output_path()` absent | `extract_research_json` raises `FileNotFoundError`; no `research_output.json`; Step 2 later raises FileNotFound | safe-stop but **undiagnosable**; no recorded state |
| F2 | raw output **empty** | blank file | parser raises `ResearchExtractionError` | same as F1 |
| F3 | raw output **unparsable** | JSON/YAML parse fails after repair | `ResearchExtractionError` | same as F1 |
| F4 | parsed dict exists but **`validate_research_output` fails** (schema) | `extract_research_json` raises | no `research_output.json` written | hard error |
| F5 | **`validate_research_output` passes but `validate_research_handoff` fails** (narrative / wrapped / legacy / incomplete) | `research_handoff_validation.json` `valid=false` (report-only) | **`research_output.json` is written and Step 2/3/4 inject it into their LLM prompts** | **most dangerous: silent degradation** |
| F6 | normalized **candidate invalid** | `research_handoff_candidate_validation.json` `valid=false` (report-only) | none — report-only, unread | observability only |
| F7 | **strict handoff valid** | candidate/raw validation `valid=true` | normal; downstream proceeds | safe (good path) |
| F8 | Deep Research **timeout / no artifact** | no raw output | same as F1 | indistinguishable from F1 |
| F9 | previous good handoff **available / unavailable** | nothing tracks this today | no last-known-good concept exists | a bad week has no safe fallback |

Summary of unsafe / undiagnosable points:
- **F5 / F6 are the central hole**: an invalid strict handoff does not stop the raw output from
  driving Step 2/3/4. The downstream LLM becomes the de-facto data-repair layer.
- **F1 / F2 / F8 collapse** into one opaque `FileNotFound`; the system cannot tell "research
  service failed" from "operator has not run it," and records no machine-readable state.
- **F9**: with no last-known-good, every bad run is a dead end (hard error or, via F5, silent
  degradation).

---

## 3. Current downstream anti-pattern

`step2_decision_builder`, `step3_audit_engine`, and `step4_order_compiler` **all load
`step1_research_output_path()` (`research_output.json`) and inject it raw into their LLM prompts
as `research_json`**, gated only by "the file exists and is a JSON object"
(`load_research_output` / `load_research_output_text`). None of them read
`research_handoff_validation.json`, `research_handoff_candidate.json`, or the candidate
validation. The strict validation and the normalized candidate that already exist are
**report-only and unread by every downstream step**.

Consequence: a narrative / wrapped / incomplete payload (F5) flows unchanged into three LLM
steps, and the downstream LLM is implicitly asked to fill the gaps — the exact anti-pattern this
design removes. **Raw `research_output.json` is being treated as an execution handoff, which it
is not.**

A second, related anti-pattern lives at the execution boundary: `step4_order_compiler`'s
`ensure_order_compiler_ready` releases order compilation **solely** on
`AUDITED_DECISION_PACKET.audit_passed == true` and `order_compiler_ready == true`. Those are
**LLM self-reported booleans** emitted by Step 3, and `validate_audited_decision_packet` only
checks that they are booleans — not that they are true for a sound, independently verifiable
reason. So today an LLM's self-report is the sole release gate for emitting orders.

---

## 4. Goals / non-goals

**Goals**
- Deep Research is no longer a hard single point of failure; its failure degrades safely.
- A deterministic, machine-readable **state** + **action permission** is produced every run.
- A **last-known-good (LKG)** strict handoff backs degraded mode.
- **Freshness / staleness** is explicit and bounded.
- Missing / invalid / stale research defaults to **HOLD / NO_TRADE / manual review**.
- Step 2/3/4 eventually consume the permission artifact and cannot bypass it.
- A deterministic execution safety gate, independent of LLM self-reported booleans, guards order
  emission.

**Non-goals**
- Not building an autonomous trader; the flow stays manual / operator-driven.
- Not changing investment scoring, ranking, sizing, rotation, or order-generation logic.
- Not making the normalizer (or any layer) fabricate missing investment content.
- Not adding cross-asset / bond-first behavior.
- Not enabling any gate in this PR (A is design only).

---

## 5. State model

A deterministic classifier maps `(current research state, last-good state, freshness,
settings/universe match)` → exactly one state. Evaluation is top-down; first match wins. The
default for every non-`STRICT_FRESH` state is **no new orders**; only HOLD / NO_TRADE are ever
default-allowed.

| State | Trigger | Required artifacts | Step 2 allowed? | Allowed actions | Blocked actions | Manual review? | No-trade? | New-buy / rotation / extended admission? |
|---|---|---|---|---|---|---|---|---|
| `STRICT_FRESH` | current strict handoff valid **and** fresh **and** settings_hash + universe match | `research_output.json` + candidate validation `valid=true` | Yes | HOLD, NO_TRADE, SELL, NEW_BUY, ROTATION, REBALANCE, EXTENDED_ETF_ADMISSION, ORDER_COMPILATION | — | No | Yes | **Yes** |
| `STRICT_STALE` | current strict handoff valid but **stale**, settings/universe match | above + freshness report | Yes (restricted) | HOLD, NO_TRADE, risk-reduction SELL, maintain existing orders | NEW_BUY, ROTATION, REBALANCE-that-adds, EXTENDED_ETF_ADMISSION | No | Yes | **No** |
| `DEGRADED_WITH_LAST_GOOD` | no valid fresh current handoff, but usable LKG (within stale window, universe + settings match) | LKG handoff + metadata | Yes (no-trade/hold only) | HOLD, NO_TRADE | all order-generating | No | Yes | **No** |
| `DEGRADED_NO_RESEARCH` | no valid current handoff **and** no usable LKG | `research_availability.json` | No | HOLD, NO_TRADE | all order-generating | No (single occurrence) | Yes | **No** |
| `INVALID_CONTRACT` | `research_output.json` parses but strict handoff invalid (F5/F6) | `research_handoff_validation.json` (`valid=false`) | No | HOLD, NO_TRADE | all order-generating | escalates if repeated | Yes | **No** |
| `NO_OUTPUT` | raw missing/empty/unparsable; no `research_output.json` (F1–F4, F8) | `research_availability.json` (records reason) | No | HOLD, NO_TRADE | all order-generating | No (single) | Yes | **No** |
| `MANUAL_REVIEW_REQUIRED` | LKG too old while no fresh; **universe changed** with no fresh valid handoff; repeated `INVALID_CONTRACT`/`NO_OUTPUT` across N runs | whatever present + decision artifact | No until cleared | HOLD, NO_TRADE (operator-confirmed) | all order-generating | **Yes** | Yes | **No** |

`MANUAL_REVIEW_REQUIRED` is also an overriding flag: any state may carry
`manual_review_required=true` when its escalation condition fires.

---

## 6. Last-known-good (LKG) handoff policy

**What qualifies.** The exact handoff object that **passed strict validation with settings-aware
context** in a prior run: `validate_research_output(payload)` succeeded **and**
`validate_research_handoff(candidate, strategy_settings=current).valid is True`.

**Source.** The normalized, strict-validated **`research_handoff_candidate.json`**, not the raw
`research_output.json`. The candidate is the canonical strict-shaped object that actually passed
`validate_research_handoff`; for valid strict runs it equals the raw payload, so nothing is lost.
**Strict validated `research_handoff_candidate.json` is the future canonical handoff.**

**Must pass before being written as LKG**
1. `validate_research_output(payload)` (permissive schema).
2. `validate_research_handoff(candidate, strategy_settings=current).valid is True`.

**Where stored (survives `prepare_next_run`, which only wipes `current/`):**
- `artifacts/state/last_good_research_handoff.json` — the validated candidate object.
- `artifacts/state/last_good_research_handoff_metadata.json` — metadata below.

`artifacts/` is gitignored, so LKG is local operational state. If an audit trail / portability is
desired, mirror the metadata into the per-run archive or relocate `state/` to a tracked path
(open question, not assumed).

**Metadata fields** (the concrete shape written by PR B's
`write_last_good_research_handoff_if_valid`):
```json
{
  "source_run_id": "20260614_232439",
  "source_as_of_date": "2026-06-07",
  "written_at": "<UTC ISO timestamp at write>",
  "strategy_settings_available": true,
  "strategy_settings_hash": "<sha256 of decision-relevant settings subset, or null if unavailable>",
  "strategy_settings_hash_inputs": {"core_universe": [], "satellite_universe": []},
  "missing_decision_relevant_settings_keys": [],
  "universe": {"core_universe": [], "satellite_universe": [], "allowed_buy_tickers": []},
  "validation_result": {"valid": true, "fail_reasons": [], "missing_fields": [], "blocker_reasons": [], "non_blocker_reasons": []},
  "handoff_source": "research_handoff_candidate",
  "schema_version": "1.0",
  "report_only": true
}
```
- `strategy_settings_hash` covers only **decision-relevant** keys (`core_universe`,
  `satellite_universe`, `user_approved_extended_etf_static_list`, and admissibility caps such as
  `extended_etf_constraints`, `active_shortlist_size_rule`, `hard_cap_open_orders_budget`,
  `max_new_tickers_per_week`). Cosmetic keys (`as_of`, `run_timestamp_et`) are excluded so a date
  bump alone does not invalidate LKG. The exact subset hashed is echoed in
  `strategy_settings_hash_inputs` for transparency.
- `source_run_id` is recorded as `"unknown"` when not derivable at write time (the archive label
  is assigned later by `prepare_next_run`); provenance is never fabricated.
- **`freshness_status` is not stored in metadata.** Freshness is computed at **read time** by the
  future degraded-mode consumer (PR C) from `source_as_of_date` vs the consuming run's date.

**Strategy settings change.** LKG stays readable. Non-universe change → `settings_drift=true`,
usable for HOLD/NO_TRADE, still not for NEW_BUY. **Universe change → LKG is invalidated for any
order-generating action** (a scorecard built over a different `allowed_buy_tickers` cannot rank
the new universe); LKG may still back HOLD / NO_TRADE.

**Universe change → must invalidate?** Yes, for actions. LKG `universe` must equal the current
settings-derived universe to support NEW_BUY / ROTATION / EXTENDED_ETF_ADMISSION; mismatch blocks
those regardless of freshness.

---

## 7. Stale / freshness policy

Cadence is weekly (one research cycle ≈ 7 days, with weekend slack) plus a separate daily
execution check. Freshness is measured in **calendar days between the handoff/LKG `as_of_date`
and the consuming run's date**. Thresholds belong in a future `research_freshness_policy` block in
`strategy_settings.yaml` (tunable, not hard-coded).

Recommended (conservative; widened one day from the suggested 0–7 / 8–14 / >14 to absorb a
weekend slip in a 7-day cycle — start with either, the model is identical):

| Bucket | Age (days) | Label | Meaning |
|---|---|---|---|
| Fresh | 0–8 | `fresh` | within one weekly cycle (+1 slack) |
| Stale | 9–16 | `stale` | within the second cycle |
| Too old | >16 | `too_old` | more than two cycles |

| Freshness | Allowed | Blocked |
|---|---|---|
| `fresh` | full set, subject to strict validity + per-action gates | none by freshness alone |
| `stale` | HOLD, NO_TRADE, risk-reduction SELL, maintain/keep existing | NEW_BUY, ROTATION, REBALANCE-that-adds, EXTENDED_ETF_ADMISSION |
| `too_old` | HOLD, NO_TRADE only | all order-generating; sets `manual_review_required=true` |

- **No-trade is always available**, regardless of freshness.
- **Manual review when**: `too_old`; universe changed with no `fresh` valid handoff; or repeated
  invalid/no-output across consecutive runs.
- Even when stale, **risk-reduction SELL / HOLD** are acceptable (de-risking on old data is safer
  than initiating exposure on old data); research-*driven* sells (rotation) are not.

---

## 8. Degraded-mode permission model (deterministic)

A deterministic evaluator (never the LLM) emits a single permission object. Example:

```json
{
  "schema_version": "1.0",
  "research_availability": "degraded_with_last_good",
  "state": "DEGRADED_WITH_LAST_GOOD",
  "fresh_research_available": false,
  "handoff_valid": true,
  "handoff_stale": true,
  "handoff_age_days": 11,
  "universe_match": true,
  "settings_hash_match": true,
  "allowed_actions": ["HOLD", "NO_TRADE"],
  "blocked_actions": ["NEW_BUY", "SELL", "ROTATION", "REBALANCE", "EXTENDED_ETF_ADMISSION", "ORDER_COMPILATION"],
  "manual_review_required": false,
  "source_handoff": {"source_run_id": "20260614_232439", "as_of_date": "2026-06-07"},
  "blocker_reasons": [
    "no fresh valid strict handoff this run; using last-known-good (age 11d, stale window)",
    "stale handoff: NEW_BUY / ROTATION / EXTENDED_ETF_ADMISSION not permitted"
  ]
}
```

**Action permission matrix (default-deny for order-generating actions):**

| Action | STRICT_FRESH | STRICT_STALE | DEGRADED_WITH_LAST_GOOD | DEGRADED_NO_RESEARCH | INVALID_CONTRACT | NO_OUTPUT | MANUAL_REVIEW_REQUIRED |
|---|---|---|---|---|---|---|---|
| `HOLD` | allow | allow | allow | allow | allow | allow | allow |
| `NO_TRADE` | allow | allow | allow | allow | allow | allow | allow |
| `SELL` (risk-reduction) | allow | allow | block | block | block | block | manual only |
| `REBALANCE` | allow | block | block | block | block | block | block |
| `NEW_BUY` | allow | block | block | block | block | block | block |
| `ROTATION` | allow | block | block | block | block | block | block |
| `EXTENDED_ETF_ADMISSION` | allow | block | block | block | block | block | block |
| `ORDER_COMPILATION` | allow | allow (HOLD/keep/SELL-derived only) | block | block | block | block | block |

**Hard rule:** when research is missing / invalid / stale beyond fresh, the deterministic default
is **NO_TRADE / HOLD — never NEW_BUY**. No order-generating action is ever default-allowed.

---

## 9. Proposed artifacts

| Artifact | Produced by | When | Report-only now? | Future gate? | Downstream use | Commit / archive |
|---|---|---|---|---|---|---|
| `artifacts/current/step1_research/research_availability.json` | Step 1 availability evaluator (new) | every Step 1 parse, **even on parse failure** | Yes | gate input | Step 2/3/4 read state | gitignored; archived with run |
| `artifacts/current/step1_research/research_freshness_report.json` | freshness evaluator | every Step 1 parse | Yes | feeds permission | observability + permission input | gitignored; archived |
| `artifacts/current/step1_research/research_degraded_mode_decision.json` | permission evaluator | after availability + freshness + LKG resolution | Yes (PR C) → **gate (PR D)** | **Yes** | Step 2/3/4 must read + honor | gitignored; archived |
| `artifacts/state/last_good_research_handoff.json` | LKG writer | when current candidate validates strict (PR B) | Yes (writer only) | feeds DEGRADED_WITH_LAST_GOOD | LKG fallback source | gitignored, **persists across runs** |
| `artifacts/state/last_good_research_handoff_metadata.json` | LKG writer | same | Yes | feeds freshness + match checks | provenance | same |

All artifacts are JSON via `common.io.write_json` (stable, pretty, `ensure_ascii=False`).
Availability/freshness/decision live under `current/step1_research/` so they archive with the
run; LKG lives under `artifacts/state/` precisely so it outlives `prepare_next_run`.

---

## 10. Implementation roadmap (small PRs)

### PR A — design doc only
- **Scope:** add this document + docs-content tests.
- **Files likely touched:** `docs/deep_research_degraded_mode_design.md`,
  `tests/unit/test_deep_research_degraded_mode_design.py`.
- **Risk:** none (no code path changes).
- **Tests:** docs-content assertions + full unit suite.
- **Behavior change:** none — docs/tests only.
- **Rollback:** delete the two files.

### PR B — last-known-good handoff state writer (report-only)
- **Scope:** when the strict candidate validates, write `artifacts/state/last_good_research_handoff*.json`.
- **Files likely touched:** new `state` writer module under `src/.../workflow` or a new
  `state/` package; one call site at the end of `parse_step1_output`; new tests.
- **Risk:** low — write-only to a new location; LKG is written but unread.
- **Tests:** writes on valid candidate; does not write / does not corrupt prior LKG on invalid;
  metadata fields correct; survives a simulated `prepare_next_run`.
- **Behavior change:** **report-only** (no downstream change).
- **Rollback:** remove the writer call + module; delete `artifacts/state/` (local only).

### PR C — research availability / freshness / degraded decision (report-only)
- **Scope:** availability evaluator classifying F1–F9 into a state (even on parse failure),
  freshness report, and `research_degraded_mode_decision.json`. Resolves LKG from PR B.
- **Files likely touched:** new evaluator module(s); Step 1 CLI/parse wiring to emit artifacts;
  tests.
- **Risk:** low–medium — must emit artifacts even when Step 1 parse fails (wrap failure paths);
  still does not block.
- **Tests:** each state produced for the right inputs; artifacts always written; no exception
  escapes; report-only (no gating).
- **Behavior change:** **report-only**.
- **Rollback:** stop emitting artifacts; remove evaluator wiring.

### PR D — Step 1 degraded-mode gate
- **Scope:** Step 2 `render` consults `research_degraded_mode_decision.json`; without a fresh
  valid handoff, Step 2 may only build a **no-trade / manual-review** path, not an actionable
  new-buy path. Explicit no-trade result allowed.
- **Files likely touched:** Step 2 render entry (read permission, select prompt path / inject
  permission); tests. No investment-logic edit.
- **Risk:** medium — first behavior change; must allow no-trade and not hard-crash on degraded.
- **Tests:** degraded → actionable new-buy path blocked; no-trade path allowed; STRICT_FRESH →
  unchanged.
- **Behavior change:** **first enforcement.**
- **Rollback:** feature-flag the gate off (read-but-don't-enforce) or revert the render change.

### PR E — permission propagation to Step 2/3/4
- **Scope:** Step 2/3/4 read the permission artifact and cannot bypass degraded restrictions
  (e.g., Step 4 will not compile order-generating actions when blocked).
- **Files likely touched:** Step 2/3/4 input loaders + deterministic checks; tests.
- **Risk:** medium — touches three steps; keep checks deterministic and additive.
- **Tests:** each step refuses order-generating work under blocked permission; passes under
  STRICT_FRESH.
- **Behavior change:** **enforced across the chain.**
- **Rollback:** revert per-step checks; permission becomes advisory again.

### PR F — P1 execution safety gate
- **Scope:** deterministic gate immediately before the order compiler emits orders. **An LLM
  self-reported boolean (`audit_passed` / `order_compiler_ready`) cannot be the sole release
  condition**; deterministic permission + validation must independently authorize emission.
- **Files likely touched:** order-compiler pre-emit gate (deterministic wrapper around
  `ensure_order_compiler_ready`); tests. No order-sizing / investment-logic edit.
- **Risk:** medium-high — guards real order emission; must fail closed.
- **Tests:** emission blocked when permission disallows even if LLM bools are true; emission
  allowed only when deterministic checks + permission both pass.
- **Behavior change:** **terminal safety gate.**
- **Rollback:** disable the deterministic gate (revert to bool-only), restoring prior behavior.

Each PR is independently shippable; B and C are zero-risk observability; D is the first, minimal
behavior change.

---

## 11. Route comparison

| Dimension | A. Prompt hardening | B. LLM extraction layer | C. Deep Research optional + last-good fallback + deterministic permission |
|---|---|---|---|
| Long-term stability | low–medium | medium (second LLM can also fail/hallucinate) | **high** |
| Fit for an LLM investment system | partial (LLM still owns the contract) | partial (contract shifts to a second LLM) | **best** (LLM proposes; deterministic code decides) |
| Hallucination avoidance | medium | **risky** (extraction can fabricate strict fields from prose) | **high** (deterministic layer never fabricates) |
| Investment safety | medium | medium-low | **high** (default no-trade) |
| Implementation complexity | low | high | medium |
| Diagnosability on failure | poor (silent drift) | medium | **high** (explicit state + decision artifacts) |
| Fit for ETF / low-frequency goals | ok | overkill / adds risk | **best** |

**Recommendation: C is the mandatory backbone** — it is what makes the system safe when Deep
Research fails. **Keep A as ongoing hygiene** (prompt reinforcement already reduces drift and is
cheap). **Defer B**: only consider an LLM extraction layer *after* C exists, and even then its
output must pass the same deterministic `validate_research_handoff` and be subject to the same
permission gate — extraction is only another candidate *proposer*, never a trusted source of
strict fields, and must never fabricate missing investment content to satisfy the validator.

---

## 12. Investment safety principles

These hold across all PRs:

1. **Deep Research failure must not be repaired by downstream LLMs.**
2. **Raw `research_output.json` is not an execution handoff.**
3. **Strict validated `research_handoff_candidate.json` is the future canonical handoff.**
4. **Missing / invalid / stale research defaults to HOLD / NO_TRADE / manual review, not NEW_BUY.**
5. **No-trade is a valid investment decision.**
6. **LLM may propose rationale, but action permission must be deterministic.**
7. **Step 2/3/4 must eventually consume permission artifacts and must not bypass degraded-mode
   restrictions.**
8. **Order compiler must not use LLM self-reported `audit_passed` / `order_compiler_ready` as the sole release gate.**
9. **For low/mid-frequency ETF allocation, missing a trade is preferable to trading on bad data.**

---

## 13. Open questions

- **LKG persistence location.** `artifacts/state/` is local (gitignored). Is a tracked /
  audited / portable LKG required, or is local operational state acceptable for a single-operator
  manual flow?
- **Freshness thresholds.** Confirm 0–8 / 9–16 / >16 vs the stricter 0–7 / 8–14 / >14; where
  should `research_freshness_policy` live in `strategy_settings.yaml`?
- **`strategy_settings_hash` scope.** Exact key set that counts as "decision-relevant" — confirm
  inclusion of each cap and the approved static list.
- **Repeated-failure escalation N.** How many consecutive `INVALID_CONTRACT` / `NO_OUTPUT` runs
  trigger `MANUAL_REVIEW_REQUIRED`?
- **SELL semantics under degraded mode.** Confirm risk-reduction SELL is allowed only in
  STRICT_* / manual review, and that research-driven rotation SELL is treated as order-generating.
- **Daily execution check interaction.** Does the daily check have its own dependence on the
  weekly handoff that the permission model must also cover?
- **PR F gate source of truth.** Which deterministic inputs (permission artifact + which
  validators) jointly authorize order emission alongside — not instead of being replaced by — the
  existing `audit_passed` / `order_compiler_ready` booleans?

---

## 14. Operating: inspect a blocked or degraded run

Deep Research no-output / invalid / stale research must **not** be repaired by a downstream LLM.
When research is missing, invalid, or stale, the system fails closed: Step 2 blocks at its
research gate and Step 3/4 block at their upstream-artifact guard, producing blocked artifacts
(`step2_blocked_by_research_gate.json`, `step3_blocked_by_upstream_gate.json`,
`step4_blocked_by_upstream_gate.json`).

To see the whole run's status at a glance, run:

```bash
PYTHONPATH=src uv run python -m investment_orchestrator.cli.run_status
```

This aggregates the Step 1 degraded-mode decision and the Step 2/3/4 blocked artifacts into a
single deterministic operational summary at `artifacts/current/run_summary.json`. How to read it:

- `run_blocked=true` with `recommended_result=NO_TRADE` is a deterministic safety outcome, not a silent failure — the pipeline intentionally chose not to trade on missing/invalid research.
- **No-trade is a valid investment decision.** Missing one trade is preferable to trading on bad
  data.
- `is_llm_generated=false` confirms the summary is deterministic operator tooling, not an LLM
  product; it is not a decision packet, audit packet, or order output.
- `manual_review_required` indicates whether a human must intervene before any further action.
- `blocked_stages` lists which downstream stages were blocked; `research_state` /
  `research_availability` explain why (e.g. `NO_OUTPUT`).
- `source_artifacts` traces back to the Step 1 degraded decision and the Step 2/3/4 blocked
  artifacts — including the Step 4 final execution safety gate block
  (`step4_blocked_by_final_execution_safety_gate.json`) — for full provenance. A run that is
  blocked *only* at the final execution safety gate still reports `run_blocked=true` /
  `recommended_result=NO_TRADE` with `step4` in `blocked_stages`.

`run_status` only reads existing artifacts; it changes no gate behavior and is safe to run at any
time.

In addition to the upstream-artifact guard, Step 4 enforces a deterministic final execution safety gate before order compilation (`step4_blocked_by_final_execution_safety_gate.json`).
This makes Step 3's LLM self-reported `audit_passed` / `order_compiler_ready` booleans necessary but not sufficient: order compilation also requires a `STRICT_FRESH` Step 1 permission allowing
`ORDER_COMPILATION`, structured Step 2/3 packets, and no explicit blockers / manual-review flags.
The gate fails closed (`recommended_result=NO_TRADE`) whenever anything is missing, malformed, or
blocked.
