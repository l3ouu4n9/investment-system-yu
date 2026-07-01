# Step 1 Evidence-First Research Architecture — Design (R2)

**Status: DESIGN / INSPECTION ONLY.** This document changes no production behavior, no prompt, no
gate, no Step 2/3/4 workflow, no investment semantics, and no order compiler. It proposes a re-architecture
of Step 1 so that producing a valid STRICT_FRESH execution handoff **no longer depends on the Deep
Research LLM emitting the entire ~15-key strict handoff JSON in one shot**, and it lays out a small,
testable migration PR sequence (R2A…R2F). The downstream fail-closed safety chain is unchanged and is
treated as a hard invariant.

---

## 1. Inspected sources

- `src/investment_orchestrator/workflow/step1_research.py` — manual render→paste→parse flow; four
  report-only layers in `parse_step1_output`: (1) validate raw parsed output, (2) **normalize a strict
  handoff candidate** + validate it, (3) write last-known-good (LKG) iff candidate is strict-valid, (4)
  deterministic `research_availability` / freshness / degraded-mode decision artifacts. Step 1 parse never
  blocks on handoff invalidity — it is an *observer*.
- `src/investment_orchestrator/validators/validate_research_handoff.py` — the strict contract:
  `REQUIRED_TOP_LEVEL_FIELDS` (15), `REQUIRED_BUY_SCORECARD_FIELDS` (15 per scorecard row),
  `REQUIRED_HANDOFF_FIELDS` (13 in `strategy_a_research_handoff`), `REQUIRED_EXTENDED_GATE_FIELDS` (7 in
  `extended_lane_downstream_gate`). It explicitly does **not** trust `validation_summary.passed`.
- `src/investment_orchestrator/normalizers/research_handoff_candidate.py` — classifies source shape
  (`strict` / `wrapped_research_json` / `legacy` / `narrative_lanes`) and recovers the first three by
  copy/unwrap/rename; `narrative_lanes` is `unrecoverable` (it will not infer strict fields from prose).
- `src/investment_orchestrator/state/research_availability.py` — deterministic state machine:
  `STRICT_FRESH` / `STRICT_STALE` / `DEGRADED_WITH_LAST_GOOD` / `DEGRADED_NO_RESEARCH` /
  `INVALID_CONTRACT` / `NO_OUTPUT` / `MANUAL_REVIEW_REQUIRED`; stale policy `fresh_days=8`,
  `stale_days=16`; allowed-action table is default-deny for order-generating actions (HOLD/NO_TRADE
  always allowed; NEW_BUY only under `STRICT_FRESH`).
- `inputs/current/strategy_settings.yaml` — operator SSOT for universe (`core_universe`,
  `satellite_universe`), `user_approved_extended_etf_static_list`, theme map, `extended_etf_constraints`,
  `active_shortlist_size_rule`, `hard_cap_open_orders_budget`, `target_new_buy_budget_this_run`,
  `max_new_tickers_per_week`, role/template maps. Carries `as_of` / `run_timestamp_et`.
- `inputs/current/portfolio_snapshot.txt` — operator SSOT for holdings (1), existing buy open orders
  (2a), sell open orders (2b), LTCG-sellable lots (3).
- Current Step 1 artifacts — the live run is `DEGRADED_WITH_LAST_GOOD`: the model emitted a research-report
  JSON (`source_shape=narrative_lanes`, `normalization_mode=unrecoverable`), candidate invalid (all 15
  top-level fields missing); a valid 5-day-old LKG exists but `settings_hash_match=false`.
- `prompts/research_dual_lane.txt` — 2,038 lines / ~127 KB; asks the LLM for a full dual-lane research
  report **and** the entire strict handoff in one JSON. R1C added a compact top-of-prompt contract; this
  R2 design addresses the deeper structural cause.

## 2. Current Step 1 pain point (summary)

The failure is **not** parser/validator/normalizer (all correct and tested) and **not** downstream safety
(complete). It is **single-shot LLM contract compliance**: the Deep Research step is asked to emit one
enormous strict object (15 top-level keys, nested scorecard rows of 15 fields each, a 13-field handoff
sub-object, a 7-field extended gate) **in the same response as a long qualitative research report**. The
model intermittently emits a plausible *report-shaped* JSON missing every strict field, or Deep Research
fails to run at all. When that happens the run degrades to HOLD/NO_TRADE — safe, but it does not produce
STRICT_FRESH. The strict handoff is, however, **mostly deterministic scaffolding** (universe, roles,
approved lists, constants, gate skeleton) **plus a thin layer of genuine qualitative judgment** — so it
does not need to be generated wholesale by an LLM.

## 3. Proposed architecture (evidence-first)

Split Step 1 into four stages; only **1B** is an LLM call, and it is small and non-authoritative.

```
Step 1A  deterministic evidence packet     evidence_packet.json   (no LLM; settings + portfolio + LKG + market metrics if available)
Step 1B  small LLM analyst memo            analyst_memo.json      (qualitative judgment ONLY; cannot create tickers/budgets)
Step 1C  deterministic handoff compiler    research_handoff_candidate.json  (always emits all 15 required keys; DATA_GAP when unknown)
Step 1D  existing validator + availability research_handoff_candidate_validation.json + degraded-mode decision  (UNCHANGED)
```

Design principle: **determinism owns structure and constraints; the LLM owns only opinion.** 1C always
produces a *structurally complete* candidate (so `narrative_lanes`/`unrecoverable` can no longer happen),
and the LLM's absence degrades gracefully to an evidence-only, no-new-buy handoff rather than to an
invalid one. 1D is reused verbatim — no validator/gate changes.

### 3.1 Required-field source classification

Every `REQUIRED_TOP_LEVEL_FIELDS` entry, mapped to its authoritative source. **D** = deterministic
(settings/portfolio/constant), **L** = LLM analyst memo (qualitative), **O** = operator input, **B** =
blocked / DATA_GAP-only when its enabling input is absent.

| Required field | Source | Notes |
|---|---|---|
| `schema_version` | D | compiler constant |
| `trade_universe` (`allowed_buy_tickers`) | D/O | `core_universe ∪ satellite_universe` from settings |
| `buy_universe_scorecard` (rows) | D+L | D: one row per allowed ticker with `role_layer`; L: `execution_priority_this_run`, `actionability_status`, `entry_driver`, `thesis_12m_plus_*`, anchor/theme refs. No memo ⇒ rows present with `actionability_status` conservative + `compile_blocker_if_any=DATA_GAP` |
| `scheduled_events` | L/B | no deterministic macro-calendar source in-repo today ⇒ `[]` + DATA_GAP without memo/feed |
| `structural_themes_6_18m` | L/B | `[]` + DATA_GAP without memo |
| `regime_inputs` | L (+ D metrics if a market feed exists) | conservative empty/DATA_GAP without memo |
| `policy_items` | L/B | `[]` + DATA_GAP without memo |
| `top5_next_week` | L/B | `[]` + DATA_GAP without memo |
| `user_approved_extended_etf_static_list` | D/O | verbatim from settings |
| `proposed_extended_etf_candidates` | L | `[]` deterministically (Lane B proposals are opinion) |
| `extended_etf_candidate_universe` | D/O | derived from settings approved list + theme map |
| `extended_etf_predecision_scorecard` | L | `[]` deterministically |
| `approved_static_list_screening_log` | L | `[]` deterministically |
| `optional_extended_etf_sleeve` (`enabled`, `allowed_extended_etf_tickers`) | D/B | `enabled=false` + `allowed=[]` conservative default; `enabled=true` requires a fresh memo justification |
| `strategy_a_research_handoff` | D+L | D constants: `handoff_version`, `handoff_scope="research_to_decision_builder_only"`, `not_order_instruction=true`, `strategy_a_must_still_apply=true`, `sell_side_research_boundary`, `extended_lane_downstream_gate` (disabled skeleton). L: `base_shortlist_eligible_by_role`, `positive_delta_research_supported`, `replacement_ranking_by_role`, `rotation_handoff`, `buy_side_no_action_hints`. No memo ⇒ all-watch-only, `positive_delta_research_supported=[]` ⇒ no NEW_BUY |

**Conclusion:** ~8 of 15 top-level fields are fully deterministic; the rest are deterministic *skeletons*
filled by a small memo. The only fields that can *enable a NEW_BUY* — `actionability_status=actionable`,
`positive_delta_research_supported` non-empty, `optional_extended_etf_sleeve.enabled=true` — are exactly
the ones gated on a fresh analyst memo.

## 4. `evidence_packet.json` schema (Step 1A — deterministic, no LLM)

Path: `artifacts/current/step1_research/evidence_packet.json`. Built purely from operator inputs + state;
contains **no** LLM-generated claim.

```json
{
  "schema_version": "evidence_packet_v1",
  "is_llm_generated": false,
  "report_only": true,
  "run_metadata": { "now_date": "...", "run_timestamp_et": "...", "settings_as_of": "...", "snapshot_updated": "..." },
  "strategy_settings_hash": "...",
  "strategy_settings_hash_inputs": { "...": "decision_relevant_settings subset" },
  "universe": { "core_universe": [], "satellite_universe": [], "allowed_buy_tickers": [], "role_layer_by_ticker": {} },
  "budget_settings": { "hard_cap_open_orders_budget": null, "target_new_buy_budget_this_run": null, "max_new_tickers_per_week": {} },
  "pre_approved_extended": { "user_approved_extended_etf_static_list": [], "theme_map": {}, "extended_etf_constraints": {} },
  "portfolio_summary": { "holdings_base": [], "holdings_extended": [], "existing_buy_open_orders": [], "sell_open_orders": [], "ltcg_sellable_lot_count": 0 },
  "market_metrics": { "available": false, "tickers": {} },
  "scheduled_events_deterministic": { "available": false, "events": [] },
  "last_good_research": { "available": false, "as_of_date": null, "age_days": null, "settings_hash_match": null, "universe_match": null },
  "data_gaps": [ { "field": "...", "reason": "DATA_GAP: ..." } ],
  "source_artifacts": { "strategy_settings": "...", "portfolio_snapshot": "...", "last_good": "..." }
}
```

Rules: `is_llm_generated:false` always; missing data is represented **explicitly** as a `data_gaps` entry
(never silent omission); `market_metrics`/`scheduled_events_deterministic` are `available:false` until a
deterministic feed exists; freshness is carried in `run_metadata` + `last_good_research`. Reuses existing
helpers `strategy_settings_hash` / `decision_relevant_settings` and the section-(2a) parser.

## 5. `analyst_memo.json` schema (Step 1B — small LLM output, qualitative only)

A *much* smaller LLM contract than today's monolith — opinion only, no structure to hallucinate.

```json
{
  "schema_version": "analyst_memo_v1",
  "is_llm_generated": true,
  "as_of_date": "...",
  "regime_view": "...",
  "key_risks": ["..."],
  "opportunity_summary": "...",
  "ticker_relative_view": [ { "ticker": "QQQ", "stance": "prefer|neutral|deprioritize", "rationale_12m_plus": "..." } ],
  "preferred_exposures": ["..."],
  "avoid_or_deprioritize": ["..."],
  "scheduled_event_interpretation": ["..."],
  "confidence": "high|adequate|weak",
  "data_gaps": ["..."],
  "source_notes": [ { "claim": "...", "source": "...", "source_quality": "official|media_only" } ]
}
```

Hard rules (enforced by the 1B parser, **not** by trusting the LLM):

- The memo **cannot create allowed tickers** outside `evidence_packet.universe` /
  `user_approved_extended_etf_static_list` — any out-of-universe `ticker_relative_view` row is **rejected**
  (parser fails the memo, not the run).
- The memo **cannot set budgets** — there is no budget field; budgets stay deterministic.
- The memo **cannot bypass the validator** — it is an *input* to 1C, never a handoff itself.
- The memo **cannot be the sole authority** — 1C still applies deterministic universe/role/constraint
  rules over it.
- If Deep Research / the LLM **fails or is absent**, `analyst_memo.json` is simply missing; 1C must handle
  that conservatively (evidence-only, no NEW_BUY).

## 6. Deterministic strict-handoff compiler (Step 1C)

`compile_research_handoff(evidence_packet, analyst_memo|None, strategy_settings, portfolio_snapshot,
last_good_metadata) -> candidate` produces `research_handoff_candidate.json`. Behavior:

- **Always emits all `REQUIRED_TOP_LEVEL_FIELDS`** with the correct container types — so the candidate is
  structurally complete by construction; `narrative_lanes`/`unrecoverable` can no longer occur.
- **Never hallucinates.** Deterministic fields come from settings/portfolio; qualitative fields come
  *only* from a present, in-universe memo; anything else is an explicit **DATA_GAP** marker (the validator
  and downstream already understand DATA_GAP).
- **Deterministic container defaults:** lists default `[]`, objects default to their required skeleton
  (e.g. `optional_extended_etf_sleeve = {enabled:false, allowed_extended_etf_tickers: <from settings>}`).
- **Universe & budget stay deterministic:** `trade_universe.allowed_buy_tickers`, role layers, approved
  extended list, and the extended gate come from settings — the memo cannot widen them.
- **Memo used only for qualitative fields / ranking hints:** scorecard `execution_priority` /
  `actionability_status` / thesis, `regime_inputs`, themes/events, and the handoff's
  `positive_delta_research_supported` / `replacement_ranking_by_role` / `rotation_handoff`.
- **Fail closed for NEW_BUY:** with no memo (or a low-confidence / DATA_GAP-heavy memo), the compiler emits
  a handoff where `positive_delta_research_supported=[]`, every base ticker is `watch_only`,
  `actionability_status` is non-actionable, and the extended sleeve is disabled with an explicit
  `disable_reason`/`why_not_enabled`. Such a candidate can be *strict-valid and fresh* yet still permit
  only HOLD/NO_TRADE (see §7).

The compiler output flows into the **existing** 1D validator + availability evaluator unchanged.

## 7. Permission model

Reuse the existing states and the default-deny action table; add **one** state and refine the
classifier's *inputs* (not its safety posture).

| State | Meaning | Allowed actions |
|---|---|---|
| `STRICT_FRESH_WITH_LLM_MEMO` (= today's `STRICT_FRESH`) | fresh evidence **and** a fresh, valid, in-universe analyst memo **and** a strict-valid compiled handoff | full set incl. `NEW_BUY` / `ORDER_COMPILATION` |
| `STRICT_FRESH_EVIDENCE_ONLY` (NEW) | fresh evidence + strict-valid compiled handoff, but **no fresh valid memo** | `HOLD`, `NO_TRADE` only |
| `DEGRADED_WITH_LAST_GOOD` | unchanged | `HOLD`, `NO_TRADE` |
| `DEGRADED_NO_RESEARCH` / `INVALID_CONTRACT` / `NO_OUTPUT` / `MANUAL_REVIEW_REQUIRED` | unchanged | `HOLD`, `NO_TRADE` |

**Recommendation (matches the stated preference): evidence-only must NOT permit `NEW_BUY`.** A fresh
qualitative review (analyst memo, or an equivalent operator-attested review) is a **precondition for any
new buy**. `STRICT_FRESH_EVIDENCE_ONLY` yields HOLD/NO_TRADE + diagnostics only. (`SELL` could optionally
be permitted as risk-reduction, mirroring `STRICT_STALE`, but the conservative default is HOLD/NO_TRADE
only; defer that choice to the implementing PR.) **Hard invariant preserved:** `NEW_BUY` requires fresh
evidence **and** a fresh memo **and** a strict-valid compiled handoff — never any two of the three.

## 8. Migration plan (small, testable PRs)

| PR | Scope | Production effect |
|---|---|---|
| **R2A** | this design doc + docs tests | none (design only) |
| **R2B** ✅ implemented | `evidence_packet.json` **builder** (1A), written report-only during `parse_step1_output` (see §13) | additive artifact; no gate/validator change |
| **R2C** ✅ implemented | `analyst_memo` small prompt + parser (1B), report-only; parser rejects out-of-universe tickers/budgets (see §14) | additive; no gate change |
| **R2D** ✅ implemented | deterministic handoff **compiler** (1C), **report-only**: write a *second* candidate (`compiled_research_handoff_candidate.json`) alongside today's normalized candidate; validate it with the existing validator; do not yet feed availability (see §15) | additive; behavior unchanged |
| **R2E.1** ✅ implemented | availability evaluator **recognizes** the compiled candidate and introduces `STRICT_FRESH_EVIDENCE_ONLY` (HOLD/NO_TRADE only); no actionable path opened (see §16) | first behavior change; default-deny posture preserved |
| **R2E (rest)** | any further availability/permission refinement toward an actionable evidence+memo path | future explicit PR; default-deny posture preserved |
| **R2F** | shrink `research_dual_lane.txt` to the **memo-only** contract; deprecate the monolithic single-shot strict handoff path | prompt change; strict schema no longer LLM-authored |

Each PR is independently revertible; R2B–R2D are pure additive observers (zero behavior change), so the
risky switch (R2E) lands only after the compiler is proven report-only against real runs.

## 9. Proposed tests (per PR)

- **R2B:** evidence packet has all sections + `is_llm_generated:false`; `strategy_settings_hash` matches
  `strategy_settings_hash(decision_relevant_settings(...))`; missing inputs ⇒ explicit `data_gaps` entries
  (not silent); **no LLM-claim keys** appear (assert the packet contains only deterministic/operator keys).
- **R2C:** memo parser accepts a minimal valid memo; **rejects** an out-of-universe `ticker_relative_view`
  ticker; **rejects** any budget-setting attempt; missing memo file ⇒ parser returns "absent" (not error).
- **R2D:** compiler always emits **every** `REQUIRED_TOP_LEVEL_FIELDS` (coupled to the validator constant);
  compiler output passes `validate_research_handoff` for a full memo; **DATA_GAP propagation** — absent
  memo ⇒ valid-but-non-actionable handoff; compiler never introduces a ticker outside settings.
- **R2E:** **no memo ⇒ no NEW_BUY** (`STRICT_FRESH_EVIDENCE_ONLY` ⇒ allowed actions are HOLD/NO_TRADE only);
  fresh memo + fresh evidence ⇒ `STRICT_FRESH_WITH_LLM_MEMO` ⇒ NEW_BUY allowed; **existing degraded-mode
  gates unchanged** (regression: the current `STRICT_FRESH` / `DEGRADED_*` action tables still hold).
- **R2F:** prompt-contract tests updated to the memo schema; assert the monolithic strict-handoff
  instructions are removed and the memo schema is required.

## 10. Risk analysis

- **Over-determinism / "less LLM-driven":** moving structure to the compiler is intentional — the LLM keeps
  the part it is actually good at (qualitative judgment). Mitigation: the memo still drives ranking, thesis,
  regime, and NEW_BUY enablement; only *structure* is deterministic.
- **Over-LLM authority recreates today's problem:** if the memo were allowed to define universe/budgets it
  would reintroduce single-shot fragility. Mitigation: the 1B parser hard-rejects out-of-universe / budget
  content; 1C treats the memo as advisory.
- **Stale evidence packet / operator data drift:** the packet is only as fresh as `settings_as_of` /
  snapshot. Mitigation: freshness carried explicitly; the existing `settings_hash` / `as_of` /
  `fresh_days`/`stale_days` machinery still governs staleness and last-good reuse.
- **Prompt complexity:** R2F must *shrink* the prompt to the memo; risk is incomplete removal of the old
  monolith. Mitigation: prompt-contract tests assert removal.
- **False sense of STRICT_FRESH:** the new `STRICT_FRESH_EVIDENCE_ONLY` must never be confused with a
  fully-researched run. Mitigation: distinct state name, HOLD/NO_TRADE-only actions, and clear
  `non_blocker_reasons` ("evidence-only: no fresh analyst memo; NEW_BUY not permitted").

## 11. Non-goals

- No change to the validator contract, the degraded-mode action tables' safety posture, the stale policy,
  Step 2/3/4, investment semantics, or the order compiler.
- No automated Deep Research / broker integration.
- R2 does not itself implement 1A–1C; it specifies them for R2B–R2F.

## 12. Rollback

This R2A doc is docs-only — delete `docs/step1_evidence_first_research_design.md` and its docs test. Each
later PR (R2B–R2F) is independently revertible; R2B–R2D are report-only additive observers.

## 13. R2B implementation status (Step 1A evidence packet — implemented, report-only)

R2B implements Step 1A only. **No behavior, gate, permission, prompt, validator, normalizer, or
Step 2/3/4 change; no new action is allowed and evidence-only still cannot enter NEW_BUY.**

- **Module:** `src/investment_orchestrator/research/evidence_packet.py` — a pure
  `build_evidence_packet(...)` (inputs in, mapping out; never raises), a `check_evidence_packet_invariants(...)`
  checker, and a `write_evidence_packet(...)` disk wrapper.
- **Artifact path:** `artifacts/current/step1_research/evidence_packet.json` (`schema_version`
  `evidence_packet_v1`).
- **Deterministic inputs only:** strategy settings (universe, approved extended list, budgets, hash via the
  existing `strategy_settings_hash`/`decision_relevant_settings`), the portfolio snapshot (section **(2a)**
  summarized via the existing reliable parser — holdings (1) / sell (2b) / LTCG lots (3) are explicit
  DATA_GAPs, **no brittle free-text parsing**), and the persisted last-good metadata. `is_llm_generated:false`;
  `market_metrics` / `scheduled_events_deterministic` are `available:false` (no feed) — never LLM-filled.
- **Integration:** written as report-only **layer 0** at the start of `parse_step1_output`, **before** the
  Deep Research parse and **independent of** the degraded-mode decision; wrapped defensively so a builder
  failure never breaks Step 1 parse and never alters `research_degraded_mode_decision.json` / allowed actions.
  A missing portfolio snapshot becomes an explicit DATA_GAP rather than a crash.
- **Invariants enforced (report-only checker):** `is_llm_generated` is exactly `False`; all required
  top-level fields present; `data_gaps` is a list; universe tickers normalized/non-empty; budget fields
  present (value or explicit null); **no analyst_memo opinion field** (`regime_view`, `preferred_exposures`,
  `opportunity_summary`, …) appears.
- **Not yet wired:** the packet is not consumed by 1C/1D yet (that is R2D/R2E); R2B only produces it.

## 14. R2C implementation status (Step 1B analyst memo — implemented, report-only)

R2C implements Step 1B only: a small qualitative analyst memo, parsed and validated as a report-only
observer. **No behavior, gate, permission, prompt-of-record (the Deep Research strict handoff prompt),
validator, normalizer, degraded-mode decision, or Step 2/3/4 change; the analyst memo can never permit
`NEW_BUY`, never sets budgets, never creates an allowed universe, and is not yet consumed by any gate.**

- **Module:** `src/investment_orchestrator/research/analyst_memo.py` — a pure `parse_analyst_memo_text(...)`
  / `validate_analyst_memo(...)` (text/mapping in, problems out; never raises), `evidence_universe_from_packet(...)`,
  `render_analyst_memo_prompt(...)`, and `analyst_memo_parse_result_to_dict(...)`.
- **Prompt:** `prompts/analyst_memo.txt` (`schema_version` `analyst_memo_v1`). It takes the deterministic
  `evidence_packet.json` as its only structured input (`{{ evidence_packet_json }}`), asks for the small
  `analyst_memo_v1` JSON only, and explicitly forbids budgets, an allowed universe, `strategy_a_research_handoff`,
  orders, and any execution-authorization request. It is a small fraction of the monolithic Deep Research prompt.
- **Artifacts (report-only, under `artifacts/current/step1_research/`):** `analyst_memo_prompt.txt`
  (rendered), `analyst_memo_raw_output.txt` (operator paste target), `analyst_memo.json` (parsed memo),
  `analyst_memo_validation.json` (validation result, carrying `report_only: true` and an explicit
  `permission_effect` noting it never permits `NEW_BUY` and does not change `allowed_actions`).
- **Safety rules enforced by the parser (the LLM is never trusted):** `is_llm_generated` must be exactly
  `true`; `schema_version` must be `analyst_memo_v1`; **no budget keys** anywhere (named
  `hard_cap_open_orders_budget` / `target_new_buy_budget_this_run`, or any key containing budget / cap /
  allocation); **no allowed-universe / strict-handoff keys** (`trade_universe`, `allowed_buy_tickers`,
  `buy_universe_scorecard`, `strategy_a_research_handoff`, …); **no execution-authority / order-intent keys**
  (`allowed_actions`, `final_action`, `order_intent`, `order_compilation`, `buy_order`,
  `execution_authorization`, …) and no authoritative action token (`NEW_BUY` / `ORDER_COMPILATION` /
  `BUY_ORDER`) as a standalone value; every `ticker_relative_view` ticker must be inside the deterministic
  evidence universe (`allowed_buy_tickers ∪ approved_extended_etf`); each `stance` is prefer / neutral /
  deprioritize; and `confidence` is constrained to **low / medium / high**. (This low/medium/high enum is the
  implemented contract; it supersedes the illustrative `high|adequate|weak` placeholder in §5.)
- **Integration (report-only):** a CLI render mode (`run_step1 analyst-memo-render`) generates the memo
  prompt from the evidence packet, and `run_step1 analyst-memo-parse` parses a pasted memo standalone. Inside
  `parse_step1_output`, a defensive **layer 0b** parses the memo **only if** `analyst_memo_raw_output.txt`
  exists, writing `analyst_memo.json` / `analyst_memo_validation.json`. It is wrapped so a memo-parse failure
  never breaks Step 1 parse, and it is fully independent of `research_degraded_mode_decision.json` / allowed
  actions — a valid or invalid memo leaves the degraded-mode decision (HOLD/NO_TRADE only) unchanged.
- **Not yet wired:** the memo is not consumed by 1C/1D (that is R2D/R2E); R2C only produces and validates it.

## 15. R2D implementation status (Step 1C handoff compiler — implemented, report-only)

R2D implements Step 1C only: a deterministic compiler that turns the evidence packet (+ optional valid
analyst memo) into a structurally complete strict-handoff candidate, validated with the existing validator
and written report-only. **No behavior, gate, permission, validator, normalizer, degraded-mode decision, or
Step 2/3/4 change; the compiled candidate is NOT fed into `research_degraded_mode_decision`, does not change
`allowed_actions`, and evidence-only / invalid-memo modes never support `NEW_BUY`.**

- **Module:** `src/investment_orchestrator/research/handoff_compiler.py` — a pure
  `compile_research_handoff(evidence_packet, analyst_memo=None, *, strategy_settings=None)` (mappings in,
  mapping out; never raises), plus `build_compiled_handoff_metadata(...)` and a `write_compiled_research_handoff(...)`
  disk wrapper.
- **Artifacts (report-only, under `artifacts/current/step1_research/`):**
  `compiled_research_handoff_candidate.json`, `compiled_research_handoff_validation.json` (the existing
  validator's result over the compiled candidate), and `compiled_research_handoff_metadata.json`.
- **Required top-level keys emitted:** the compiler imports `REQUIRED_TOP_LEVEL_FIELDS` from the validator
  and always emits **every** one with the correct container type (so `narrative_lanes` / `unrecoverable`
  can no longer occur). Scorecard rows carry all `REQUIRED_BUY_SCORECARD_FIELDS`; the handoff carries all
  `REQUIRED_HANDOFF_FIELDS`; the gate carries all `REQUIRED_EXTENDED_GATE_FIELDS`.
- **Compilation modes** (recorded in metadata as `compilation_mode`): `evidence_plus_memo` (present + valid
  memo), `evidence_only` (no memo), `invalid_memo_ignored` (memo present but fails the R2C validator — e.g.
  out-of-universe ticker, budget key, bad confidence). The compiler **re-validates** the memo itself, so an
  invalid memo is never trusted.
- **Deterministic field rules:** `trade_universe.allowed_buy_tickers`, `user_approved_extended_etf_static_list`,
  and the extended candidate universe come only from the evidence packet; `role_layer` comes from the operator
  `ticker_role_fallback` map (falling back to satellite→`sector_alpha_tilt` / benchmark→`benchmark_carrier_core`
  / core→`diversified_core_buffer`). Budgets are never emitted into the handoff and never taken from the memo.
- **Evidence-only behavior:** every scorecard row is watch-only (`actionability_status` is never
  `actionable_this_run`) with an explicit `DATA_GAP: no_fresh_analyst_memo` blocker;
  `positive_delta_research_supported` is empty; `base_shortlist_eligible_by_role` is all-empty; the handoff
  carries `compilation_non_actionable_reason: missing_fresh_analyst_memo`; the extended sleeve is disabled.
  **No fresh memo ⇒ no NEW_BUY support.**
- **Evidence + valid memo behavior:** the memo populates qualitative fields only — scorecard
  `thesis_12m_plus_summary` for matching in-universe tickers, `regime_inputs.regime_view`, a stance-based
  ranking nudge on `execution_priority_this_run`, and a non-authoritative `analyst_memo_qualitative_context`
  echo. It never widens the universe / budgets, never enables the extended sleeve, and never authorizes
  execution: `positive_delta_research_supported` stays empty and no row becomes actionable in R2D (NEW_BUY
  enablement is the R2E switch).
- **Validation results:** in all three modes the compiled candidate is **strict-valid** per
  `validate_research_handoff` (DATA_GAP markers on non-actionable rows are non-blockers).
- **Integration (report-only):** a report-only **layer 0c** in `parse_step1_output` (after the evidence
  packet and analyst-memo layers) compiles, validates, and writes the three artifacts; `run_step1
  compile-handoff` runs it standalone. It is wrapped so a compiler failure never breaks Step 1 parse, and it
  never touches `research_degraded_mode_decision` / `allowed_actions`. The raw Deep Research candidate
  remains the active source for current behavior.
- **Not yet wired:** the availability evaluator does not prefer the compiled candidate yet (that is R2E);
  R2D only produces and validates it.

## 16. R2E.1 implementation status (availability recognizes the compiled handoff — non-actionable)

R2E.1 is the **first R2 behavior change**, and it is deliberately conservative. The availability evaluator
now *reads* the R2D compiled handoff (`compiled_research_handoff_validation.json` /
`compiled_research_handoff_metadata.json`) and, when the raw Deep Research handoff is not valid+fresh but the
compiled handoff is strict-valid and fresh, classifies the run as a new state **`STRICT_FRESH_EVIDENCE_ONLY`**.
**This state is non-actionable: `allowed_actions` is exactly `["HOLD", "NO_TRADE"]`; `NEW_BUY`,
`ORDER_COMPILATION`, `EXTENDED_ETF_ADMISSION`, `ROTATION`, `REBALANCE`, and `SELL` are all blocked. No
actionable trading path is opened — enabling `NEW_BUY` for evidence+memo requires a future explicit PR.**

- **State:** `STRICT_FRESH_EVIDENCE_ONLY` (added to `research_availability.py`), distinct from the
  full-actionable `STRICT_FRESH`. `fresh_research_available` stays `false` and `manual_review_required`
  stays `false` for this state.
- **Source precedence (conservative):** (A) a valid+fresh **raw** handoff still yields `STRICT_FRESH` with
  full permissions — unchanged; (B) else, if the **compiled** handoff is valid+fresh and its metadata reports
  a recognized `compilation_mode`, a fallback state (`INVALID_CONTRACT` / `DEGRADED_NO_RESEARCH` /
  `DEGRADED_WITH_LAST_GOOD` / `NO_OUTPUT`) is relabeled `STRICT_FRESH_EVIDENCE_ONLY`; (C) else existing
  last-good / degraded / no-output / invalid behavior is unchanged. The raw valid `STRICT_FRESH` /
  `STRICT_STALE` states and `MANUAL_REVIEW_REQUIRED` are **never** relabeled (a manual-review escalation and
  the stale-`SELL` right are preserved). When no compiled inputs are supplied the evaluator is byte-for-byte
  unchanged. A missing / malformed compiled metadata mode **fails closed** (no relabel). The Step 1 parse
  feeds compiled inputs only on the normal parse path; a hard Step-1 parse failure stays `NO_OUTPUT` for
  operator visibility.
- **Artifact fields (`research_degraded_mode_decision.json`):** `research_state`, `source`
  (`compiled_research_handoff` when relabeled, else `raw_research_handoff`), `compilation_mode`,
  `analyst_memo_present`, `analyst_memo_valid`, `permission_effect: "none"`, `allowed_actions: ["HOLD",
  "NO_TRADE"]`, `blocker_reasons` including `compiled_handoff_non_actionable` / `evidence_only_no_new_buy`,
  and `source_artifacts` pointing at the compiled candidate / validation / metadata. The artifact is not
  LLM-generated.
- **Gate / weekly / run_status (unchanged policy):** the Step 2 research gate still allows the actionable
  path only for `STRICT_FRESH` with `NEW_BUY`+`ORDER_COMPILATION` allowed, so `STRICT_FRESH_EVIDENCE_ONLY`
  is blocked before any Step 2 prompt render (recommended `NO_TRADE`). `run_weekly` completes a controlled
  `NO_TRADE` terminal (exit 0) for this state and never enters Step 2/3/4. `run_status` / `run_summary.json`
  report `research_state=STRICT_FRESH_EVIDENCE_ONLY`, `recommended_result=NO_TRADE`. No gate, Step 2/3/4
  workflow, investment-semantics, or order-compiler code changed.

## 17. R2E.2 design — actionable evidence+memo criteria for a future `STRICT_FRESH_WITH_LLM_MEMO`

**Status: DESIGN / INSPECTION ONLY.** This section changes no production behavior, no gate, no
`allowed_actions`, no Step 2/3/4 workflow, no order compiler, and no prompt. It specifies the *future*
conditions under which a compiled evidence-first handoff (`evidence_plus_memo` mode) could be allowed to
enter an actionable research state, and it does **not** add `NEW_BUY` / `ORDER_COMPILATION` permission.
Today's posture is unchanged: `evidence_plus_memo` still emits `positive_delta_research_supported=[]`,
no row is `actionable_this_run`, the extended sleeve is disabled, and the run stays
`STRICT_FRESH_EVIDENCE_ONLY` (HOLD / NO_TRADE only).

### 17.1 What exactly makes a strict handoff actionable today (inspection)

Actionability lives in **two independent layers**; both must hold before any order is compiled.

**Layer 1 — the handoff contract (`validate_research_handoff`).** A `buy_universe_scorecard` row is
"actionable" *iff* `actionability_status == "actionable_this_run"`. For such a row the validator promotes
every check to a **blocker** (`_validate_actionable_scorecard_item` + DATA_GAP reclassification):

- `thesis_12m_plus_supported` must be exactly `true`;
- `thesis_linkage_quality` must be `strong` or `adequate`;
- the row must carry a non-empty `event_id_refs` **or** `structural_theme_refs`;
- `primary_anchor_event_id` must be truthy;
- `primary_anchor_date_et` must be truthy;
- `compile_blocker_if_any` must be `null`;
- **no** `DATA_GAP` / `missing` / `unknown` / `unspecified` marker may appear in any
  `REQUIRED_BUY_SCORECARD_FIELDS` value (non-actionable rows treat the same markers as non-blockers).

`strategy_a_research_handoff.positive_delta_research_supported` is the buy-support signal: every ticker in
it must be inside `trade_universe.allowed_buy_tickers` **and** map to a scorecard row whose
`actionability_status == "actionable_this_run"`; otherwise it is a blocker. So `positive_delta_research_supported`
can only ever list tickers that are already actionable rows — it never *creates* actionability, it
*references* it. `optional_extended_etf_sleeve.enabled=true` additionally requires a non-empty
`allowed_extended_etf_tickers`, a matching `extended_etf_scorecard` with event/theme refs, and a consistent
`extended_lane_downstream_gate`.

`scheduled_events`, `structural_themes_6_18m`, `regime_inputs`, `top5_next_week`, `policy_items` are
**required to be present with the correct container type but may be empty** — the validator does not require
content and does not cross-check that a row's `event_id_refs` / `structural_theme_refs` actually resolve to
an entry in `scheduled_events` / `structural_themes_6_18m`. Step 2's decision-builder prompt injects the
whole `research_output` JSON, so it *consumes* these fields qualitatively but does not deterministically
require any of them to be non-empty.

**Layer 2 — availability state + gates.** Even a fully valid, actionable-row handoff only becomes a real
buy if `research_availability` classifies the run `STRICT_FRESH` **and** two fail-closed gates agree:

- `research_degraded_mode_gate.enforce_step2_research_gate` — `ACTIONABLE_REQUIRED_STATE = "STRICT_FRESH"`,
  requires `REQUIRED_ACTIONS = (NEW_BUY, ORDER_COMPILATION)` in `allowed_actions`, and
  `manual_review_required is False`;
- `final_execution_safety_gate` — independently hardcodes `ACTIONABLE_REQUIRED_STATE = "STRICT_FRESH"`,
  requires `ORDER_COMPILATION`, and requires `NEW_BUY` whenever the run carries buy intent.

**Two gates hardcode the literal `STRICT_FRESH`.** Any actionable `STRICT_FRESH_WITH_LLM_MEMO` path must
update **both** gates (and the weekly/run-status readers), not just the availability table — otherwise the
new state stays blocked (which is the safe default, but not actionable).

**Deterministic caps stay downstream.** `max_new_tickers_per_week` and `target_new_buy_budget_this_run`
are enforced in Step 4 (`step4_order_compiler` + `validate_orders_output`), not in the handoff. The
handoff/compiler never sizes an order.

**Blocking structural finding.** The R2D compiler *hardcodes* every scorecard row to
`actionability_status = "ranking_hold_watch_only"`, `primary_anchor_event_id = None`,
`primary_anchor_date_et = None`, `event_id_refs = []`, `structural_theme_refs = []`, and there is **no
deterministic `scheduled_events` / `structural_themes` / `market_metrics` feed** (all `available:false` in
`evidence_packet.json`). Therefore, **even a valid high-confidence memo cannot currently produce a
Layer-1-actionable row** — the anchor + refs requirements cannot be satisfied deterministically. Opening an
actionable path is blocked on first providing a *valid anchor source*. This is the pivotal design
constraint for R2E.2 and the reason the conservative recommendation (§17.4) does not ship `NEW_BUY` yet.

The extended ETF sleeve can and should **stay disabled** for the entire first actionable version.

### 17.2 Analyst-memo influence allowlist / denylist

The memo is qualitative opinion only. The following split is deterministic and is already largely enforced
by `validate_analyst_memo`; R2E.2 tightens *how much* a valid memo may drive.

**MAY influence (advisory / qualitative only):**

- `ticker_relative_view[].rationale_12m_plus` → scorecard `thesis_12m_plus_summary` (ticker rationale);
- `ticker_relative_view[].stance` (`prefer` / `neutral` / `deprioritize`) → a bounded ranking nudge on
  `execution_priority_this_run` (qualitative stance);
- `key_risks` → qualitative risk notes echoed in `analyst_memo_qualitative_context`;
- `regime_view` → `regime_inputs.regime_view` (flagged `is_llm_qualitative:true`);
- `preferred_exposures` → a ranking *hint* only (never universe membership);
- `opportunity_summary` → qualitative context only;
- `avoid_or_deprioritize` → ranking demotion / eligibility veto (a memo can *remove* support, never add
  membership);
- `scheduled_event_interpretation` → qualitative reading of events (never *creation* of a `scheduled_events`
  entry);
- `source_notes` → provenance for a support claim. **Note:** `source_notes` is in the prompt/schema but is
  **not yet enforced** by `validate_analyst_memo`; a future actionable path that relies on it must add a
  deterministic `source_notes` check (§17.3).
- *(future, gated)* a `ticker_relative_view` row may contribute a **buy-support signal** feeding
  `positive_delta_research_supported` — but only under the full deterministic criteria in §17.3, never from
  free-text confidence alone.

**MUST NOT influence (deterministic denylist — already hard-rejected by the memo validator):**

- universe membership (`FORBIDDEN_UNIVERSE_KEYS`: `trade_universe`, `allowed_buy_tickers`,
  `buy_universe_scorecard`, `strategy_a_research_handoff`, …) — out-of-universe `ticker_relative_view`
  tickers invalidate the memo;
- budgets / sizing (`FORBIDDEN_BUDGET_KEYS`: `hard_cap_open_orders_budget`,
  `target_new_buy_budget_this_run`; plus any key containing `budget` / `cap` / `allocation`);
- `final_action` / `order_intent` / orders / order sizing / execution authorization
  (`FORBIDDEN_ACTION_KEYS` + `NEW_BUY` / `ORDER_COMPILATION` / `BUY_ORDER` as a scalar value);
- hard caps (covered by the budget denylist);
- extended ETF admission — a memo may never enable the extended sleeve; admission stays separately gated on
  deterministic preconditions (disabled in v1 regardless of memo).

### 17.3 Proposed deterministic actionable criteria (`STRICT_FRESH_WITH_LLM_MEMO`)

A run may enter the actionable compiled state only when **all** run-level preconditions hold **and** at
least one ticker independently satisfies **all** per-ticker criteria. Every clause is deterministic; the LLM
is never trusted.

**Run-level preconditions (conjunction):**

1. `evidence_packet` is **fresh** (`age_days ≤ fresh_days`) and passes `check_evidence_packet_invariants`
   (empty problem list) with `is_llm_generated == false`;
2. the compiled handoff is **strict-valid** (`validate_research_handoff.valid`) and fresh;
3. `analyst_memo` is **present and valid** → `compilation_mode == evidence_plus_memo`;
4. memo `confidence` is **not `low`** (`medium` or `high`) — the compiler must start *reading* confidence
   (today it only echoes it);
5. deterministic drift guards hold: `strategy_settings_hash` match and `universe_match` (reuse the existing
   last-good machinery) so the universe/settings have not changed under the memo;
6. a **valid anchor source exists** (see the §17.1 blocking finding) — either a deterministic
   `scheduled_events` / `structural_themes` / `market_metrics` feed, or an explicitly gated
   memo-derived `structural_theme` anchor. Absent any anchor source, the run stays non-actionable.

**Per-ticker criteria (a ticker becomes `actionable_this_run` and eligible for
`positive_delta_research_supported` only if all hold):**

- the ticker is in the **deterministic allowed universe** (`allowed_buy_tickers`) — **never** an extended
  ETF in v1;
- it has a memo `ticker_relative_view` row with `stance == "prefer"`;
- that row has a **non-empty `rationale_12m_plus`**;
- it is **not** listed in `avoid_or_deprioritize`;
- it has **no blocking `data_gaps`** attributable to it;
- it has **`source_notes` provenance** (requires the new `source_notes` validation in §17.2);
- the resulting scorecard row independently satisfies the Layer-1 actionable contract (§17.1): a truthy
  `primary_anchor_*`, a non-empty `event_id_refs` / `structural_theme_refs`, `thesis_12m_plus_supported`,
  adequate/strong linkage, `compile_blocker_if_any=null`, and **no DATA_GAP marker** — which is precisely
  why precondition (6) is mandatory.

**Global constraints:**

- the actionable count is capped at `max_new_tickers_per_week` at the compiler as a defensive echo (the
  authoritative ceiling remains Step 4);
- **no extended ETF admission** in the first version — the sleeve stays disabled;
- **required scheduled / macro events unavailable ⇒ non-actionable / DATA_GAP** (no anchor ⇒ no
  actionable row — falls out of precondition 6 automatically);
- **`market_metrics` feed unavailable:** per the operator's stated preference, treat absence as a
  **non-blocking DATA_GAP for broad ETFs only**; for anything else the absence blocks. The
  conservative default while no feed exists is to block (§17.4).

### 17.4 Recommended first actionable version

Options considered: **(A)** no `NEW_BUY` yet — keep evidence+memo non-actionable until deterministic market
metrics exist; **(B)** limited `NEW_BUY` for the existing core/satellite universe only, no extended ETFs;
**(C)** produce richer Step 2 research *context* from evidence+memo but still block `ORDER_COMPILATION`;
**(D)** something else.

**Recommendation (matches the stated conservative preference):**

- **Ship A now.** Because of the §17.1 blocking finding, a valid memo cannot yet yield a Layer-1-actionable
  row without a deterministic anchor source; shipping `NEW_BUY` before that would force the compiler (or the
  memo) to *mint* anchors, reintroducing exactly the LLM-authority fragility R2 removed. Stay
  `STRICT_FRESH_EVIDENCE_ONLY` (HOLD / NO_TRADE) until an anchor source lands.
- **Then C as the low-risk value step** — surface the memo's qualitative context (regime view, ranking
  hints, rationale) into Step 2 read-only, still blocking `ORDER_COMPILATION` / `NEW_BUY`. This delivers the
  memo's value with zero order-generating authority.
- **B is the target first *actionable* version**, admitted only after (i) a deterministic anchor source
  exists (precondition 6), (ii) all §17.3 criteria are enforced with tests, and (iii) both gates are
  updated. B allows `NEW_BUY` **only** for existing core/satellite tickers, **never** extended ETFs, with
  `target_new_buy_budget` / `max_new_tickers_per_week` / hard cap enforced downstream unchanged.
- **Never** admit an out-of-universe ticker; **never** act on a `low`-confidence memo; keep the extended
  sleeve disabled throughout.

### 17.5 Risk analysis

- **Converting weak memo language into `positive_delta` support.** Free-text "I like QQQ" must never become
  a buy signal. Mitigation: support requires `stance == "prefer"` **and** non-empty `rationale_12m_plus`
  **and** `source_notes` **and** a deterministic anchor — never prose alone.
- **LLM overconfidence.** A confident-but-wrong memo could push actionability. Mitigation: `confidence`
  gates only the *floor* (reject `low`); it never *raises* authority; the deterministic universe / caps /
  gates bound the blast radius; `SELL` and manual-review rights are never widened by a memo.
- **Stale evidence packet.** A fresh compiled handoff over a stale packet could look actionable.
  Mitigation: precondition 1 requires packet freshness and invariants; the existing `fresh_days` /
  `stale_days` / `settings_hash` / `universe_match` machinery governs staleness and drift.
- **Missing deterministic price / market metrics.** Acting without a metrics feed risks anchoring on
  opinion. Mitigation: precondition 6 + the §17.3 market-metrics rule (broad-ETF-only non-blocking DATA_GAP;
  block otherwise); conservative default is to block.
- **Step 2 over-interpreting the qualitative memo.** Step 2 might treat advisory context as instruction.
  Mitigation: keep memo-sourced fields flagged `is_llm_qualitative:true`; option C surfaces context
  read-only; `not_order_instruction=true` and `strategy_a_must_still_apply` are preserved.
- **Reintroducing prompt/schema fragility.** Letting the memo grow toward the old monolith reverses R2.
  Mitigation: the memo schema stays intentionally small; new authority (e.g. `source_notes`) is added as a
  deterministic *validator* rule, not as trust in the LLM; prompt-contract tests guard the memo contract.

### 17.6 Proposed PR sequence

Each PR is small, independently revertible, and preserves the default-deny posture until the very last
switch. `STRICT_FRESH_WITH_LLM_MEMO` is a **distinct new availability state** (not a rename of raw
`STRICT_FRESH`) that maps to the actionable permission set only once the gates are updated.

| PR | Scope | Production effect |
|---|---|---|
| **R2E.2** (this section) | design doc + docs-content tests only | none (design only) |
| **R2E.3** ✅ implemented | compiler emits a **report-only support signal** (per-ticker "would-be-actionable" evaluation of the §17.3 criteria) into a dedicated `compiled_support_signals.json` artifact; scorecard rows stay `ranking_hold_watch_only`; still not fed to availability (see §18) | additive; behavior unchanged |
| **R2E.4** | availability introduces `STRICT_FRESH_WITH_LLM_MEMO`, but it maps to **HOLD / NO_TRADE** (still Step 2-blocked); begin *reading* memo `confidence` and the compiled support signal | new state, still non-actionable |
| **R2E.5a** (design §19; parser ✅ §20) | land a **deterministic anchor source** (operator `research_anchors.yaml`; later events / themes / market-metrics feeds) so a Layer-1-actionable row is producible; add `source_notes` validation | additive; still gated |
| **R2E.5b** | both gates (`research_degraded_mode_gate` **and** `final_execution_safety_gate`) + weekly / run-status readers accept `STRICT_FRESH_WITH_LLM_MEMO` **only** when all §17.3 criteria pass; enable limited `NEW_BUY` for core/satellite (option B), extended sleeve still disabled | first actionable path; caps enforced downstream |
| **R2F** | shrink `research_dual_lane.txt` to the memo-only contract; deprecate the monolithic single-shot strict handoff | prompt change |

Rationale for splitting the old "R2E.5": the actionable switch depends on two prerequisites (an anchor
source and a `source_notes` rule) that are cleaner as their own PR (R2E.5a) before the gate change
(R2E.5b), and the gate change must touch **both** fail-closed gates found in §17.1.

### 17.7 Non-goals (R2E.2)

No new `NEW_BUY` / `ORDER_COMPILATION` permission; no gate, `allowed_actions`, Step 2/3/4, order-compiler,
prompt, validator, or investment-semantics change. `STRICT_FRESH_WITH_LLM_MEMO` is specified here but **not
implemented**; the live posture remains `STRICT_FRESH_EVIDENCE_ONLY` (HOLD / NO_TRADE) as in §16.

## 18. R2E.3 implementation status (compiler support-signal extraction — implemented, report-only)

R2E.3 implements the §17.6 support-signal step only: a deterministic extractor that reports which analyst-memo
opinions *would* be buy-support candidates for a *future* actionable path, and the exact deterministic reason
each is currently rejected. **No behavior, gate, permission, `allowed_actions`, availability state, validator,
normalizer, prompt, or Step 2/3/4 change; this adds no `NEW_BUY` / `ORDER_COMPILATION` permission and does
NOT enable `STRICT_FRESH_WITH_LLM_MEMO`.** The live posture stays `STRICT_FRESH_EVIDENCE_ONLY` (HOLD /
NO_TRADE).

- **Module:** `src/investment_orchestrator/research/support_signals.py` — a pure
  `build_compiled_support_signals(*, evidence_packet, analyst_memo, compilation_mode, generated_at=None)`
  (mappings in, mapping out; never raises). It re-uses the compiler's already-computed `compilation_mode` as
  the single source of truth for present/valid so the artifact can never disagree with the compiler.
- **Artifact (report-only, under `artifacts/current/step1_research/`):** `compiled_support_signals.json`
  (`schema_version` `compiled_support_signals_v1`), carrying `is_llm_generated: false`, `report_only: true`,
  `permission_effect: "none"`, `anchor_source_available: false`, `actionable_signals_possible: false`,
  `analyst_memo_present`, `analyst_memo_valid`, `compilation_mode`, `candidate_ticker_signals[]`,
  `accepted_support_signals[]`, `qualitative_support_only[]`, `rejected_support_signals[]`, and
  `global_blockers[]`.
- **Candidate signal fields (per `ticker_relative_view` ticker):** `ticker`, `stance`, `confidence`,
  `rationale_present`, `source_notes_present`, `in_allowed_universe`, `listed_in_avoid_or_deprioritize`,
  `has_blocking_data_gap`, `has_valid_anchor_source` (always `false` in v1), `anchor_source_type` (always
  `none_available`), `accepted_for_future_actionability` (always `false`), and `rejection_reasons[]`.
  (`preferred_exposures` is intentionally NOT a ticker candidate source — it is free-text exposure text that
  cannot be universe-validated, matching the memo validator's `TICKER_FIELDS = ("ticker_relative_view",)`.)
- **Deterministic rejection reason codes** — global: `missing_valid_anchor_source` (always applies in v1 —
  no deterministic anchor source exists), `memo_confidence_low` (valid memo with `confidence == low`),
  `analyst_memo_absent`, `analyst_memo_invalid`; per-ticker: `stance_not_prefer`, `missing_rationale`,
  `missing_source_notes`, `out_of_universe`, `extended_etf_not_allowed_in_v1`,
  `listed_in_avoid_or_deprioritize`, `blocking_data_gap`.
- **Not authorization (hard invariant):** `accepted_support_signals` is **always empty** in R2E.3 —
  acceptance-for-actionability requires a deterministic anchor source that does not exist, so
  `missing_valid_anchor_source` is a permanent global blocker. A candidate that passes every *qualitative*
  gate is surfaced under `qualitative_support_only` (deliberately named so it is never mistaken for a buy
  authorization); it is still non-actionable. `positive_delta_research_supported` stays `[]`, every scorecard
  row stays `ranking_hold_watch_only`, and the extended sleeve stays disabled.
- **Integration (report-only):** `write_compiled_research_handoff` gained an optional `support_signals_path`;
  when supplied it also writes `compiled_support_signals.json`. Step 1's report-only layer 0c passes that path,
  so the artifact is produced whenever the compiler runs; it is **not** fed into
  `research_degraded_mode_decision` and never changes `allowed_actions`. The availability evaluator does not
  read it; a valid memo still yields `STRICT_FRESH_EVIDENCE_ONLY` → HOLD / NO_TRADE.
- **Not yet wired:** availability does not consume the support signal and no gate reads it (that is R2E.4 /
  R2E.5); R2E.3 only produces and reports it.

## 19. R2E.5a design — deterministic research-anchor source (design/inspection only)

**Status: DESIGN / INSPECTION ONLY.** This section changes no production behavior, no gate, no
`allowed_actions`, no Step 2/3/4 workflow, no order compiler, no prompt, and no validator. It designs the
**deterministic "valid anchor source"** that R2E.3 flagged as the single remaining blocker
(`missing_valid_anchor_source`) between a valid analyst memo and a *future* actionable compiled row. It does
**not** implement it, does **not** enable `STRICT_FRESH_WITH_LLM_MEMO`, and adds **no** `NEW_BUY` /
`ORDER_COMPILATION` permission. The live posture stays `STRICT_FRESH_EVIDENCE_ONLY` (HOLD / NO_TRADE).

### 19.1 Inspected sources

- `src/investment_orchestrator/research/evidence_packet.py` — `market_metrics` and
  `scheduled_events_deterministic` are both `available:false` + DATA_GAP today; there is no wired feed.
- `src/investment_orchestrator/research/analyst_memo.py` — `ticker_relative_view` is the only ticker field;
  `source_notes` exists in the schema/prompt but is **not** validated; the memo cannot create universe /
  budgets / actions.
- `src/investment_orchestrator/research/support_signals.py` — every candidate is rejected by the global
  `missing_valid_anchor_source`; `has_valid_anchor_source` is hardcoded `false`, `anchor_source_type` is
  `none_available`.
- `src/investment_orchestrator/research/handoff_compiler.py` — hardcodes `primary_anchor_event_id=None`,
  `primary_anchor_date_et=None`, `event_id_refs=[]`, `structural_theme_refs=[]`, every row watch-only.
- `src/investment_orchestrator/validators/validate_research_handoff.py` — an `actionable_this_run` row
  requires a truthy `primary_anchor_event_id`, a truthy `primary_anchor_date_et`, a non-empty `event_id_refs`
  **or** `structural_theme_refs`, and no DATA_GAP marker. **These four fields are exactly what an anchor
  must supply.**
- `inputs/current/strategy_settings.yaml` — has `user_approved_extended_etf_theme_map` (theme *buckets* for
  extended ETFs, **undated, extended-only**) and template `default_event_window_anchor_type`
  (`scheduled_macro_event` / `scheduled_theme_event`) used by Step 4 execution windows — but **no dated,
  ticker-scoped research anchor list** for the base universe.
- `inputs/current/portfolio_snapshot.txt` — holdings / open orders; no event or theme calendar.
- **Naming caution (existing, unrelated concept):** `src/investment_orchestrator/market/*anchor*`
  (`build_anchor_drift_snapshot.py`, `generate_anchor_state_input.py`) and the settings
  `daily_execution_drift_policy` use "anchor" to mean the **daily-execution price baseline**
  (`anchor_baseline_last_close`, `anchor_drift_pct`) for KEEP/REPLACE ladder maintenance. That is a
  *different* thing. To avoid collision, the concept designed here is named **research anchor** /
  `research_anchors` throughout.

### 19.2 Anchor concept

A **research anchor** is a deterministic, non-LLM, operator-authored (or, later, deterministically-fed)
**dated, ticker-scoped evidence item** that a qualitative memo claim may *cite* to justify a forward-looking,
time-anchored 12m+ buy thesis. It supplies the **structure** the validator's actionable-row contract demands
(the anchor id, its date, its type, and the tickers it applies to) — it never supplies **opinion** and never
authorizes anything. It is the missing "what grounds this, and when" behind a memo's `prefer` stance.

Two anchor kinds map onto the validator's two reference lists:

- a **`structural_theme`** anchor fills `structural_theme_refs` (+ `primary_anchor_type=structural_theme`);
- a **`scheduled_*_event`** anchor (macro / earnings / rebalance) fills `event_id_refs` +
  `primary_anchor_event_id` (+ `primary_anchor_type=scheduled_*_event`).

Both kinds carry an `anchor_date_et` so the validator's mandatory `primary_anchor_date_et` can always be
filled (structural themes use a theme as-of / review date; events use the event date). An anchor is a
**citation target**, referenced by `anchor_id`; it is never buy authorization.

### 19.3 Candidate anchor-source options

| Option | Deterministic? | Operator-controlled? | Freshness | Staleness risk | LLM-hallucination risk | Complexity | Can support future NEW_BUY? |
|---|---|---|---|---|---|---|---|
| **A. settings structural themes / anchor list** | yes | yes | weak (coarse `as_of`, rarely edited) | medium (durable themes persist → overfitting) | none | low (reuse settings parse) | partial — no dated events, no per-run freshness; pollutes the settings SSOT |
| **B. new `inputs/current/research_anchors.yaml`** | **yes** | **yes** | **strong (per-anchor `as_of` + `valid_from/until`)** | **low (`blocks_if_stale`)** | **none** | moderate (new input + parser + packet summary + validation) | **yes — the design target** |
| **C. deterministic market-metrics feed** | yes (if real feed) | no (external) | strong if live | high if feed lags | none | **high (no feed wired in Step 1A)** | eventually; a metric condition is a weak *primary* anchor for a 12m+ thesis — better as a supplementary gate |
| **D. scheduled-events feed** | yes (if real calendar) | no | feed-dependent | feed-dependent | none | **high (no calendar source in-repo)** | eventually; best long-term for `scheduled_*_event` anchors |
| **E. last-good research themes reused** | persisted, but **LLM-authored in origin** | no | fresh_days/stale + hash/universe match | real | **inherited (recreates the monolith fragility R2 removed)** | low–moderate | risky — **reject as an anchor source** (at most a cross-check) |
| **F. analyst-memo `source_notes` as anchors** | **no (LLM-authored)** | no | n/a | n/a | **high (LLM invents citations)** | low | **reject** — `source_notes` may only *reference* an `anchor_id`, never *create* one |
| **G. hybrid: operator anchors + feeds + memo `source_notes` as citations only** | yes where it matters | yes for v1 | strong | low | none (memo is citation-only) | moderate→high (phased) | **yes — the end state** |

### 19.4 Recommended first anchor design

**Recommended: Option B for v1, evolving toward G — and this matches (and is the best fit for) the stated
preference.** B is the only option that is simultaneously fully deterministic, operator-controlled, per-run
fresh, zero-hallucination, and moderate-complexity, while leaving a clean seam to add feeds (C/D) later
*without* changing the memo or compiler contract. Concretely, for the first version:

- an operator-controlled `inputs/current/research_anchors.yaml` with explicit per-run anchors (ids, dates,
  applicable tickers, type, source note, freshness window, confidence floor);
- **no** web scraping and **no** LLM-generated anchors;
- `analyst_memo` may *reference* `anchor_id`s (in `ticker_relative_view` / `source_notes`) but can never
  *create* an anchor;
- when no fresh, applicable anchor exists, support signals stay `qualitative_support_only` (never accepted) —
  exactly the R2E.3 behavior.

**Explicitly rejected as anchor sources:** E (last-good LLM themes — reintroduces the fragility R2 removed)
and F (memo `source_notes` — LLM-authored). C and D are deferred (no feed exists) and, when added, plug into
the same `anchor_source_type` seam under option G.

### 19.5 Proposed anchor schema (`research_anchors_v1`)

```yaml
schema_version: research_anchors_v1
as_of_date: 2026-06-30
is_llm_generated: false          # deterministic / operator-authored; enforced
anchors:
  - anchor_id: AI_CAPEX_2026H2          # unique token; referenced by refs / memo
    anchor_type: structural_theme        # structural_theme | scheduled_macro_event | scheduled_earnings_event | scheduled_rebalance_event
    applicable_tickers: [QQQ, SMH, IGV]   # MUST be a subset of the deterministic allowed universe
    summary: "AI capex / semiconductor demand structural theme"   # qualitative context only
    source_type: operator                 # operator (v1 only); future: deterministic_feed
    source_note: "Operator-reviewed theme; not LLM-generated"
    anchor_date_et: 2026-06-15            # fills primary_anchor_date_et (event date, or theme as-of/review date)
    valid_from: 2026-06-01
    valid_until: 2026-07-15               # staleness boundary
    confidence_floor: medium              # minimum memo confidence permitted to cite this anchor (low|medium|high)
    blocks_if_stale: true                 # a stale anchor cannot support acceptance
```

**Field decisions.** Required per anchor: `anchor_id` (unique, non-empty), `anchor_type` (enum above),
`applicable_tickers` (non-empty, subset of the allowed universe), `anchor_date_et`, `valid_from`,
`valid_until`, `source_type`, `confidence_floor`. Optional: `summary`, `source_note`, `blocks_if_stale`
(default `true`). Top-level: `schema_version`, `as_of_date`, `is_llm_generated: false`, `anchors`.

**Anchor hard rules (mirroring the memo denylist; enforced by a deterministic parser, never trusting input):**

- **No budgets/sizing** — no key containing `budget` / `cap` / `allocation`; anchors never size a trade.
- **No actions** — no `final_action` / `order_intent` / `order*` / `allowed_actions` / execution-authorization
  keys; an anchor is a citation, not an instruction.
- **No universe widening** — every `applicable_tickers` entry must be inside the deterministic allowed
  universe (`allowed_buy_tickers`); an anchor can never introduce a ticker, and (v1) can never target an
  extended ETF (extended admission stays separately gated).
- **Deterministic only** — `is_llm_generated:false` and `source_type: operator` in v1; an LLM-authored
  anchor is rejected.
- **Stale ⇒ non-supporting** — when `now_date` is outside `[valid_from, valid_until]` (or `as_of_date` is
  itself stale), a `blocks_if_stale` anchor cannot support acceptance.
- **Referenceable** — `anchor_id` must be the token that support signals and compiled-handoff refs point at;
  a ref to a missing/stale/non-applicable anchor never accepts.

### 19.6 Future integration flow (design only — not implemented here)

1. **`evidence_packet`** gains a deterministic `research_anchors` summary section (same `available:true/false`
   + DATA_GAP pattern as `market_metrics`), built from `research_anchors.yaml` if present: per-anchor
   `anchor_id`, `anchor_type`, `applicable_tickers`, `anchor_date_et`, `valid_from/until`, a computed
   `is_stale` (vs `now_date`), and `confidence_floor`. No LLM content.
2. **`analyst_memo`** may add optional `anchor_id_refs` on a `ticker_relative_view` row and/or cite
   `anchor_id`s in `source_notes`. The memo validator gains a rule: any referenced `anchor_id` must exist in
   the evidence packet's anchors — the memo can cite but never create anchors. (This is also where the
   deferred `source_notes` validation from §17.2 lands.)
3. **`support_signals`** validates, per candidate: the cited anchor exists, is fresh (within its window, not
   stale), applies to the ticker (ticker ∈ `applicable_tickers`), and the memo `confidence` ≥ the anchor's
   `confidence_floor`. When all pass, `has_valid_anchor_source=true`, `anchor_source_type=<anchor_type>`, the
   `missing_valid_anchor_source` reason is removed, and the candidate may move from `qualitative_support_only`
   into `accepted_support_signals` (which can finally be non-empty).
4. **`handoff_compiler`** — for an accepted-anchor ticker only, populates the scorecard row from the anchor:
   `primary_anchor_date_et = anchor.anchor_date_et`; `primary_anchor_event_id = anchor_id` and
   `event_id_refs = [anchor_id]` for event types, or `structural_theme_refs = [anchor_id]` for theme types;
   `primary_anchor_type` from `anchor_type`; `thesis_12m_plus_supported=true`; adequate/strong linkage;
   `compile_blocker_if_any=null`; `actionability_status=actionable_this_run`; and adds the ticker to
   `positive_delta_research_supported`. This is what finally makes a **Layer-1-actionable row producible** —
   and it can only ever draw refs from an **accepted** anchor.
5. **`availability`** may classify `STRICT_FRESH_WITH_LLM_MEMO` only after (a) the compiled candidate passes
   the validator *with* actionable rows, (b) `accepted_support_signals` is non-empty, (c) the memo is
   fresh + valid + `confidence != low`, (d) the evidence packet is fresh, and (e) the anchors are fresh.
   Permitting `NEW_BUY` from that state is the separate R2E.5b gate change (both fail-closed gates in §17.1),
   not this design.

Nothing in steps 1–5 is implemented by R2E.5a-design; acceptance stays impossible and every row stays
watch-only until those PRs land.

### 19.7 Proposed tests (for future implementation)

- `research_anchors.yaml` valid parse; malformed / wrong `schema_version` rejected;
- a stale anchor (now outside `[valid_from, valid_until]`, `blocks_if_stale=true`) does not support
  acceptance;
- an anchor whose `applicable_tickers` includes an out-of-universe (or extended-ETF, v1) ticker is rejected;
- a memo that references a **missing** `anchor_id` is rejected (memo cannot invent anchors);
- an anchor that applies to the ticker + fresh + confidence-floor-met ⇒ candidate accepted;
- `source_notes` alone (no `anchor_id`) can never create an anchor or accept a signal;
- an accepted support signal requires anchor **and** `stance==prefer` **and** non-empty rationale **and**
  `confidence != low`;
- the compiled handoff fills `event_id_refs` / `structural_theme_refs` / `primary_anchor_event_id` /
  `primary_anchor_date_et` **only** from an accepted anchor, and leaves them empty/None otherwise;
- an anchor carrying any budget / action / order-intent key is rejected;
- regression: with no `research_anchors.yaml`, behavior is byte-for-byte R2E.3
  (`accepted_support_signals=[]`, `STRICT_FRESH_EVIDENCE_ONLY`).

### 19.8 Risks

- **Operator burden** — per-run anchors are manual work; mitigate by keeping the schema tiny and letting a
  missing file degrade gracefully to `qualitative_support_only` (no anchor ⇒ no acceptance, never a crash).
- **Stale anchors** — an out-of-window anchor could look supporting; mitigate with mandatory
  `valid_from/until` + `blocks_if_stale` + the evidence-packet `is_stale` computation vs `now_date`.
- **Overfitting anchors to justify a trade** — an operator could mint an anchor to rubber-stamp a memo;
  mitigate by keeping anchors *structural* (dated, ticker-scoped, reviewable), auditable by `anchor_id`, and
  never budget/action-bearing, plus the confidence-floor and the downstream Step 4 caps that bound any
  resulting order.
- **LLM referencing nonexistent anchors** — the memo could cite a fabricated `anchor_id`; mitigate by
  validating every referenced id against the deterministic evidence-packet anchors (missing ⇒ reject).
- **Structural themes too broad** — a vague theme covering the whole universe defeats the point; mitigate by
  requiring explicit `applicable_tickers`, a dated review window, and (recommended future) an upper bound on
  how many tickers one theme anchor may support per run.
- **Data freshness** — anchors are only as fresh as the operator's `as_of_date`; mitigate by carrying
  freshness explicitly and reusing the existing `fresh_days`/`stale_days` machinery.
- **Citation-vs-authorization confusion** — an anchor must never read as a buy order; mitigate with the
  citation-only framing, the anchor denylist (no budgets/actions), and keeping `accepted_support_signals`
  distinct from any permission.
- **Name collision with the daily-execution price anchor (§19.1)** — mitigate by consistently using
  "research anchor" / `research_anchors` and never reusing `anchor_baseline_last_close` semantics.

### 19.9 Non-goals (R2E.5a-design)

No `research_anchors.yaml` is created, no parser/evidence-packet/memo/support-signal/compiler code is changed,
and no anchor is consumed anywhere. No `NEW_BUY` / `ORDER_COMPILATION` permission is added and
`STRICT_FRESH_WITH_LLM_MEMO` is **not** enabled. `accepted_support_signals` remains always empty and every
compiled row remains watch-only until the R2E.4 / R2E.5 implementation PRs.

*(Superseded in part by §20: R2E.5a-impl builds the parser + evidence-packet summary + the operator input
file — still report-only, still no permission and no support-signal acceptance.)*

## 20. R2E.5a-impl status (research-anchor parser + evidence-packet summary — implemented, report-only)

R2E.5a-impl implements only the deterministic **research-anchor parser / validator** and surfaces an anchor
**summary** in `evidence_packet.json`. **No behavior, gate, permission, `allowed_actions`, availability state,
support-signal acceptance, compiler actionability, validator, prompt, or Step 2/3/4 change; this adds no
`NEW_BUY` / `ORDER_COMPILATION` permission and does NOT enable `STRICT_FRESH_WITH_LLM_MEMO`.** The live posture
stays `STRICT_FRESH_EVIDENCE_ONLY` (HOLD / NO_TRADE); anchors are **not yet consumed for support acceptance**.

- **Input (operator-controlled):** `inputs/current/research_anchors.yaml` (`schema_version`
  `research_anchors_v1`), shipped with the minimal safe default `anchors: []` and `is_llm_generated: false`.
- **Module:** `src/investment_orchestrator/research/research_anchors.py` — pure
  `validate_research_anchors(payload, *, allowed_universe, today=None)`, disk `load_research_anchors(...)`,
  `summarize_research_anchors(...)`, and `build_research_anchors_summary(path, *, allowed_universe, today=None)`
  (missing file → `available:false` + `research_anchors_missing` DATA_GAP). Never raises.
- **Validation rules (deterministic; the input is never trusted):** top-level `schema_version ==
  research_anchors_v1`; `is_llm_generated` exactly `false`; `anchors` is a list; each anchor requires
  `anchor_id`, `anchor_type`, `applicable_tickers`, `anchor_date_et`, `valid_from`, `valid_until`,
  `source_type`, `confidence_floor`; `anchor_type ∈ {structural_theme, scheduled_macro_event,
  scheduled_earnings_event, scheduled_rebalance_event}`; `source_type ∈ {operator}`; `confidence_floor ∈
  {low, medium, high}`; `applicable_tickers` non-empty and a subset of the deterministic base allowed
  universe (`core ∪ satellite`) — **v1 rejects extended-ETF / out-of-universe tickers**; dates parse as ISO;
  duplicate `anchor_id` fails; forbidden budget/`cap`/`allocation` keys and any
  `final_action`/`order_intent`/order/execution-authorization key (or `NEW_BUY`/`ORDER_COMPILATION`/`BUY_ORDER`
  scalar token) are rejected recursively. **Stale** anchors (`valid_until < today`, `blocks_if_stale`) are
  flagged `stale:true` / `usable:false` and excluded from `valid_anchor_count` — they never crash the build.
- **Evidence-packet integration (report-only):** `evidence_packet.json` gains a required `research_anchors`
  section — `available`, `path`, `schema_version`, `as_of_date`, `valid`, `anchor_count`,
  `valid_anchor_count`, `stale_anchor_count`, `invalid_anchor_count`, `anchors[]`, `errors[]`,
  `consumed_for_support_acceptance: false`, `permission_effect: "none"`. Missing file ⇒ `available:false` +
  a `research_anchors` DATA_GAP; invalid file ⇒ `available:true`, `valid:false`, `errors` populated, and the
  packet still builds. The packet stays `is_llm_generated:false` (anchors are operator-authored, not LLM).
- **Not consumed yet:** `support_signals` still emits the global `missing_valid_anchor_source` blocker,
  `accepted_support_signals` stays `[]`, the compiler keeps every row `ranking_hold_watch_only` with empty
  refs / `positive_delta_research_supported`, and availability stays `STRICT_FRESH_EVIDENCE_ONLY`. Wiring
  anchors into acceptance + actionable rows is the separate R2E.4 / R2E.5 work.
