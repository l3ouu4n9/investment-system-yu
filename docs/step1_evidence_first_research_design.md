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
| **R2E.4** ✅ implemented (§22) | availability introduces `STRICT_FRESH_GROUNDED_MEMO_NON_ACTIONABLE` (the safety-explicit name chosen instead of `STRICT_FRESH_WITH_LLM_MEMO`), mapping to **HOLD / NO_TRADE** (still Step 2-blocked); reads the compiled support-signal acceptance | new state, still non-actionable |
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
Every ISO date field above (`as_of_date`, `anchor_date_et`, `valid_from`, `valid_until`) may be written
quoted or unquoted, as in the example — the parser normalizes both to the same canonical `"YYYY-MM-DD"`
string internally.

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
  universe (`core ∪ satellite`) — **v1 rejects extended-ETF / out-of-universe tickers**; dates parse as ISO
  (`YYYY-MM-DD`) — a date may be written **quoted or unquoted** in YAML (PyYAML decodes an unquoted date
  scalar as a `datetime.date` / `datetime.datetime`; the parser normalizes either form to the same canonical
  ISO string, so `2026-06-15` and `"2026-06-15"` are equivalent); an unparseable or ambiguous non-ISO string
  still fails validation; duplicate `anchor_id` fails; forbidden budget/`cap`/`allocation` keys and any
  `final_action`/`order_intent`/order/execution-authorization key (or `NEW_BUY`/`ORDER_COMPILATION`/`BUY_ORDER`
  scalar token) are rejected recursively. **Stale** anchors (`valid_until < today`, `blocks_if_stale`) are
  flagged `stale:true` / `usable:false` and excluded from `valid_anchor_count` — they never crash the build.
- **Evidence-packet integration (report-only):** `evidence_packet.json` gains a required `research_anchors`
  section — `available`, `path`, `schema_version`, `as_of_date`, `valid`, `anchor_count`,
  `valid_anchor_count`, `stale_anchor_count`, `invalid_anchor_count`, `anchors[]`, `errors[]`,
  `consumed_for_support_acceptance: false`, `permission_effect: "none"`. Missing file ⇒ `available:false` +
  a `research_anchors` DATA_GAP; invalid file ⇒ `available:true`, `valid:false`, `errors` populated, and the
  packet still builds. The packet stays `is_llm_generated:false` (anchors are operator-authored, not LLM).
- **Not consumed yet (superseded by §21):** at R2E.5a-impl, `support_signals` still emitted the global
  `missing_valid_anchor_source` blocker and `accepted_support_signals` stayed `[]`. **R2E.5a-2 (§21) wires
  anchors into support-signal *acceptance*** (report-only, still not authorization). Wiring anchors into
  *actionable compiled rows* + the availability state remains the separate R2E.4 / R2E.5b work — the
  compiler still keeps every row `ranking_hold_watch_only`, `positive_delta_research_supported=[]`, and
  availability stays `STRICT_FRESH_EVIDENCE_ONLY`.

## 21. R2E.5a-2 status (research anchors → support-signal acceptance — implemented, report-only)

R2E.5a-2 lets `compiled_support_signals.json` consume the deterministic research anchors so a *valid* analyst
memo that **references** a *valid, fresh, applicable* anchor can move a candidate into
`accepted_support_signals`. **This is still report-only and is NOT authorization: no gate, permission,
`allowed_actions`, availability state, Step 2/3/4 workflow, order compiler, validator, or compiler
actionability changes; no `NEW_BUY` / `ORDER_COMPILATION` permission is added and `STRICT_FRESH_WITH_LLM_MEMO`
is NOT enabled.** The live posture stays `STRICT_FRESH_EVIDENCE_ONLY` (HOLD / NO_TRADE).

- **Memo schema:** `ticker_relative_view` rows gain an **optional** `anchor_id_refs` list of strings
  (`analyst_memo.py` validates type/format only; default empty). The memo may only *reference* existing
  `evidence_packet.research_anchors` ids — it can **never create** an anchor; existence / freshness /
  applicability are validated deterministically in `support_signals`. `prompts/analyst_memo.txt` documents
  it: reference existing ids only, do not invent, empty list if none, reference is grounding — **not** trade
  authorization. Universe / budget / order / action rules are unchanged.
- **Acceptance criteria (all required):** analyst memo present **and** valid (`evidence_plus_memo`); memo
  `confidence != low`; ticker `stance == prefer`; ticker in the base allowed universe (never extended in v1);
  ticker not in `avoid_or_deprioritize`; rationale present; `source_notes` present; no blocking data gap for
  the ticker; **at least one `anchor_id_ref` that resolves to an anchor which exists, is valid + fresh/usable,
  is `source_type: operator`, has an allowed `anchor_type`, applies to the ticker, and whose `confidence_floor`
  ≤ the memo confidence.** Any failure → `qualitative_support_only` (if only anchor grounding is missing) or
  `rejected_support_signals` (if a qualitative/global gate fails).
- **New rejection reason codes:** `missing_anchor_id_refs`, `referenced_anchor_not_found`,
  `referenced_anchor_stale`, `anchor_not_applicable_to_ticker`, `anchor_confidence_floor_not_met`,
  `anchor_source_type_not_allowed`, `anchor_type_not_allowed` (the umbrella `missing_valid_anchor_source` is
  retained when a candidate has no valid referenced anchor).
- **Artifact fields (`compiled_support_signals.json`):** `accepted_support_signals` may now be non-empty
  (each entry: `ticker`, `stance`, `anchor_id`, `anchor_type`, `not_authorization: true`); each candidate
  gains `anchor_id_refs`, `matched_anchor_id`, and a truthful `has_valid_anchor_source` /
  `accepted_for_future_actionability`. Top-level adds **`not_authorization: true`**; `permission_effect`
  stays `"none"`; `anchor_source_available` reflects whether any usable anchor exists; the diagnostic
  `actionable_signals_possible` is true only when an accepted signal exists (paired with `not_authorization`
  and `permission_effect: none` so it can never read as a live authorization).
- **Invariants preserved (proven by tests):** the handoff compiler ignores support signals entirely — even
  with an accepted signal, `positive_delta_research_supported=[]`, every scorecard row stays
  `ranking_hold_watch_only`, and `primary_anchor_event_id` stays `null`. Availability stays
  `STRICT_FRESH_EVIDENCE_ONLY`, `allowed_actions=["HOLD","NO_TRADE"]`, and the Step 2 gate still blocks. An
  invented / stale / non-applicable / floor-missing anchor is never accepted; `source_notes` text alone never
  creates or references an anchor.
- **Not yet wired:** making a compiled row `actionable_this_run` from an accepted signal, and any availability
  / gate change, remain the separate R2E.4 / R2E.5b work.

## 22. R2E.4 status (availability recognizes grounded memo support — implemented, HOLD / NO_TRADE only)

R2E.4 is a conservative **state-semantics** change: the availability evaluator now distinguishes a run with
*fresh deterministic evidence + a valid analyst memo + accepted grounded support signals* from a plain
evidence-only run — **while keeping the exact same non-actionable permission set (HOLD / NO_TRADE)**. **No
`NEW_BUY` / `ORDER_COMPILATION` permission is added, no gate is opened, and no Step 2/3/4 workflow, order
compiler, prompt, or compiled-handoff actionability changes.** The Step 2 gate still blocks.

- **State name (chosen for safety):** **`STRICT_FRESH_GROUNDED_MEMO_NON_ACTIONABLE`**. The name states its
  own non-actionability so it can never be mistaken for trading authorization. It **supersedes** the
  tentative `STRICT_FRESH_WITH_LLM_MEMO` label used in §7 / §17.6 for the *non-actionable* case; the
  `STRICT_FRESH_WITH_LLM_MEMO` name (or a successor) is reserved for a *future actionable* PR (R2E.5b).
- **Trigger criteria (all required; fail closed otherwise):** the run is already
  `STRICT_FRESH_EVIDENCE_ONLY` (raw handoff not valid+fresh, compiled handoff valid+fresh + recognized mode)
  **and** the report-only `compiled_support_signals.json` proves `analyst_memo_present == true`,
  `analyst_memo_valid == true`, `accepted_support_signals` non-empty, `permission_effect == "none"`, and
  `not_authorization == true`. Missing / malformed / `not_authorization != true` / empty-accepted →
  stays `STRICT_FRESH_EVIDENCE_ONLY`. A valid+fresh **raw** `STRICT_FRESH` (and `STRICT_STALE` /
  `MANUAL_REVIEW_REQUIRED`) is **never** upgraded/relabeled.
- **Allowed actions (exactly):** `HOLD`, `NO_TRADE`. **Blocked actions include:** `SELL`, `NEW_BUY`,
  `ROTATION`, `REBALANCE`, `EXTENDED_ETF_ADMISSION`, `ORDER_COMPILATION`. Identical to
  `STRICT_FRESH_EVIDENCE_ONLY`; only the *label* + diagnostics are sharper.
- **Artifact fields (`research_availability.json` / `research_degraded_mode_decision.json`):**
  `support_signals_present`, `accepted_support_signal_count`, `grounded_memo_support_present`,
  `not_authorization`, `permission_effect: "none"`, `source_artifacts.compiled_support_signals`, and
  `blocker_reasons` including `grounded_memo_support_non_actionable` and `new_buy_requires_future_gate_pr`.
  Not LLM-generated (`report_only: true`).
- **Gate / weekly / run_status (unchanged policy, verified):** the Step 2 research gate still keys off
  `state == STRICT_FRESH` + `NEW_BUY`/`ORDER_COMPILATION` allowed, so the grounded state is **blocked** before
  any Step 2 prompt render (recommended `NO_TRADE`); `run_weekly` completes a controlled `NO_TRADE` terminal
  (`actionable=false`, exit 0) and never enters Step 2/3/4; `run_summary.json` records the grounded state and
  `recommended_result: NO_TRADE`. No decision packet / order artifacts are produced.
- **Non-actionable invariant (proven by tests):** even with a non-empty `accepted_support_signals`, the
  compiled handoff stays non-actionable (`positive_delta_research_supported=[]`, no `actionable_this_run`
  row, `primary_anchor_event_id=null`). Making an actionable row + opening the gate is the separate R2E.5b
  work and requires a future explicit PR.

## 23. R2E.5b-0 status (actionable-handoff PREVIEW — implemented, report-only *separate* artifact)

R2E.5b-0 adds a **separate, report-only preview artifact** so we can observe whether the *future* actionable
mapping looks reasonable — **without changing any active trading path today**. It takes the already-compiled
`accepted_support_signals` + `evidence_packet.research_anchors` + the analyst memo and previews which tickers
**would** become `actionable_this_run` rows **IF** a future PR opened an actionable path. **This is NOT
authorization.** No gate, permission, `allowed_actions`, availability state, Step 2/3/4 workflow, order
compiler, prompt, validator, or *active* compiled-handoff actionability changes.
**No `NEW_BUY` / `ORDER_COMPILATION` permission is added** and `STRICT_FRESH_WITH_LLM_MEMO` is **not** enabled.
The live posture stays `STRICT_FRESH_EVIDENCE_ONLY` / `STRICT_FRESH_GROUNDED_MEMO_NON_ACTIONABLE` (HOLD /
NO_TRADE).

- **Artifact (new, separate):** `artifacts/current/step1_research/compiled_actionable_handoff_preview.json`
  (schema `compiled_actionable_handoff_preview_v1`). It is a *distinct* file — it never mutates
  `compiled_research_handoff_candidate.json`, `compiled_support_signals.json`, or the evidence packet, and no
  downstream step reads it.
- **Top-level fields:** `schema_version`, `is_llm_generated: false`, `report_only: true`,
  `permission_effect: "none"`, `not_authorization: true`, `source_compiled_support_signals` /
  `source_compiled_handoff_candidate` / `source_evidence_packet` (each `{path, schema_version, sha256}`),
  `max_new_tickers_per_week_snapshot`, `base_new_ticker_cap_applied`,
  `extended_etf_sleeve_preview_enabled: false`, `preview_actionable_rows[]`,
  `preview_positive_delta_research_supported[]`, `rejected_preview_rows[]`, `global_blockers[]`, `notes`.
- **Row acceptance criteria (all required; inherited from `accepted_support_signals`, then two preview-only
  gates):** an accepted support signal exists; ticker is in the base allowed universe; ticker is **not** an
  extended ETF; a matching valid research anchor exists and applies to the ticker; the memo confidence meets the
  anchor `confidence_floor`; memo `stance == prefer`; rationale non-empty; `source_notes` present; ticker not in
  `avoid_or_deprioritize`; no blocking `data_gap`; **plus** (preview-only) the anchor yields a
  `structural_theme_refs` or `event_id_refs`, a scheduled-event anchor carries a `primary_anchor_date_et`, and
  the running count does not exceed `max_new_tickers_per_week` (base-universe cap; **fail-closed to 0** when
  unset — the current production default of `0` yields an empty preview).
- **Preview row fields:** `ticker`, `source_anchor_id`, `anchor_type`, `primary_anchor_event_id` /
  `primary_anchor_ref`, `primary_anchor_date_et`, `structural_theme_refs` / `event_id_refs`,
  `thesis_12m_plus_supported_preview: true`, `thesis_linkage_quality_preview`,
  `actionability_status_preview: "actionable_this_run"`, `not_authorization: true`.
- **Deterministic rejection reason codes:** `preview_limit_max_new_tickers_exceeded`, `preview_missing_anchor`,
  `preview_extended_etf_not_allowed`, `preview_missing_primary_anchor_date`, `preview_missing_event_or_theme_ref`,
  `preview_blocking_data_gap`, `preview_low_confidence`, `preview_no_accepted_support_signal`,
  `preview_out_of_base_allowed_universe`, `preview_avoid_or_deprioritize`, `preview_missing_rationale`,
  `preview_missing_source_notes`, `preview_stance_not_prefer`, `preview_analyst_memo_absent`,
  `preview_analyst_memo_invalid`. Each rejected row also retains the granular `source_rejection_reasons` from the
  support extractor. **Global blockers:** with no accepted support signals the preview is empty and
  `global_blockers` includes `no_accepted_support_signals`; a zero base cap adds
  `preview_base_new_ticker_cap_zero`.
- **Step 1 integration point:** built as report-only *layer 0d* in `parse_step1_output`, immediately **after**
  the R2E.3 support-signals / R2D compiler flow. It only runs when `compiled_support_signals.json` exists, reads
  the just-written report-only artifacts, and is fully defensive — a preview-builder failure is swallowed and
  **never breaks Step 1 parse**. The preview path is surfaced in the parse result
  (`actionable_handoff_preview_path`). The preview is **not** passed into the availability evaluator and **not**
  into Step 2.
- **Non-actionable invariant (proven by tests):** even when the preview surfaces a
  `preview_actionable_rows` entry (`actionability_status_preview: "actionable_this_run"`), the **active**
  compiled handoff stays non-actionable (`positive_delta_research_supported=[]`, no `actionable_this_run`
  scorecard row, `primary_anchor_event_id=null`), availability stays
  `STRICT_FRESH_GROUNDED_MEMO_NON_ACTIONABLE` / `STRICT_FRESH_EVIDENCE_ONLY` with
  `allowed_actions=["HOLD","NO_TRADE"]`, the Step 2 research gate still blocks, and `run_weekly` still terminates
  `NO_TRADE`. The preview is an observation, not authorization.
- **Promotion path (future, separate PRs):** promoting this preview into the **active** compiled handoff (making
  `positive_delta_research_supported` / `actionable_this_run` rows real) would be one future explicit PR; opening
  the availability state + Step 2 gate for `NEW_BUY` / `ORDER_COMPILATION` would be a **separate** future PR
  after that. Neither is done here.

## 24. R2E.5b-1 status (actionable compiled-handoff CANDIDATE — implemented, report-only *separate* artifact)

R2E.5b-1 adds a **separate, report-only** actionable *compiled-handoff candidate* that answers only: **"can the
R2E.5b-0 preview rows be transformed into a full strict handoff candidate that passes
`validate_research_handoff`, without yet being used for availability or trading?"** It overlays the preview's
`preview_actionable_rows` onto a full strict handoff and validates the *shape*. **This validates the future
actionable handoff shape only — it is NOT authorization.** No gate, permission, `allowed_actions`, availability
state, Step 2/3/4 workflow, order compiler, prompt, or *active* compiled-handoff actionability changes.
**No `NEW_BUY` / `ORDER_COMPILATION` permission is added** and `STRICT_FRESH_WITH_LLM_MEMO` is **not** enabled.

- **Artifacts (new, separate):**
  `artifacts/current/step1_research/compiled_actionable_research_handoff_candidate.json`
  (schema `research_handoff_compiled_actionable_v1`),
  `.../compiled_actionable_research_handoff_validation.json`, and
  `.../compiled_actionable_research_handoff_metadata.json` (schema
  `compiled_actionable_research_handoff_metadata_v1`). These are **distinct** files — they never overwrite or
  change the active `compiled_research_handoff_candidate.json` (which stays non-actionable), and no downstream
  step reads them.
- **Construction rules:** for each ticker in `preview_actionable_rows` the matching scorecard row is overlaid to
  `actionability_status = actionable_this_run`, `thesis_12m_plus_supported = true`, `thesis_linkage_quality`
  from the preview (`strong`/`adequate`), `event_id_refs` / `structural_theme_refs` from the preview,
  `primary_anchor_event_id` (falling back to the anchor ref for a `structural_theme`) + `primary_anchor_date_et`
  from the preview, `primary_anchor_type` from the preview, and `compile_blocker_if_any = null` with no DATA_GAP
  marker on the row (a DATA_GAP-tainted 12m+ summary is replaced with a clean deterministic thesis).
  `strategy_a_research_handoff.positive_delta_research_supported` is populated from the promoted rows **only in
  this separate candidate**; the base-universe `max_new_tickers_per_week` cap is re-asserted; the extended ETF
  sleeve stays **disabled**; no budgets / order sizing are set; no out-of-universe tickers are added. Rejected /
  non-preview tickers stay watch-only.
- **Fail-closed:** a preview row that cannot satisfy the strict validator's actionable-row contract (no
  event/theme ref, no `primary_anchor_event_id`/ref, or **no `primary_anchor_date_et`** — e.g. an anchor whose
  YAML date was not a string) is left **watch-only** rather than emitted invalid. With no promotable rows the
  candidate is a valid **non-actionable** handoff and the metadata records
  `candidate_actionable_row_count: 0`. Any builder / validation failure is isolated to these artifacts and
  **never crashes Step 1 parse** and never affects the active handoff or availability.
- **Validation:** the separate candidate is validated with the existing `validate_research_handoff`; the result
  is written to the validation artifact (errors included on failure).
- **Metadata fields:** `source_actionable_handoff_preview` / `source_compiled_support_signals` /
  `source_evidence_packet` / `source_active_compiled_handoff` (each `{path, schema_version, sha256}`),
  `used_active_compiled_handoff_as_base`, `preview_actionable_row_count`, `candidate_actionable_row_count`,
  `actionable_this_run_tickers`, `validation_passed`, `report_only: true`, `permission_effect: "none"`,
  `not_authorization: true`, `consumed_by_availability: false`, `consumed_by_step2: false`.
- **Step 1 integration point:** built as report-only *layer 0e* in `parse_step1_output`, immediately **after**
  the R2E.5b-0 preview (layer 0d). It only runs when `compiled_actionable_handoff_preview.json` exists, reads
  the just-written report-only artifacts (preview + support signals + evidence packet + the active compiled
  handoff as base), and is fully defensive. The paths are surfaced in the parse result
  (`actionable_handoff_candidate_path` / `_validation_path` / `_metadata_path`).
- **Separation invariant (proven by tests):** even when the separate candidate validates with a promoted
  `actionable_this_run` row and non-empty `positive_delta_research_supported`, the **active** compiled handoff
  is untouched and non-actionable (`positive_delta_research_supported=[]`, no `actionable_this_run` row,
  `primary_anchor_event_id=null`), availability stays `STRICT_FRESH_GROUNDED_MEMO_NON_ACTIONABLE` /
  `STRICT_FRESH_EVIDENCE_ONLY` with `allowed_actions=["HOLD","NO_TRADE"]`, the Step 2 research gate still blocks,
  and `run_weekly` still terminates `NO_TRADE`. The candidate is **not** consumed by the availability evaluator,
  the degraded-mode decision, Step 2 render, the weekly actionable path, or the final execution safety gate.
- **Promotion path (future, separate PRs):** using this validated shape as the **active** compiled handoff, and
  then opening the availability state + gates for `NEW_BUY` / `ORDER_COMPILATION`, each require a **separate**
  future explicit PR. Neither is done here.

## 25. R2E.5b-2 design — promotion path for the actionable compiled handoff (design/inspection only)

> **DESIGN / INSPECTION ONLY.** This section designs the future promotion path from the report-only
> `compiled_actionable_research_handoff_candidate.json` to the active compiled handoff, and from there — in
> later, separate PRs — to a gated actionable workflow. **Nothing here is implemented in R2E.5b-2**: no
> production behavior, `allowed_actions`, gate, availability state, Step 2/3/4 workflow, order compiler, or
> prompt changes; **no `NEW_BUY` / `ORDER_COMPILATION` permission is added** and `STRICT_FRESH_WITH_LLM_MEMO`
> is **not** enabled. The live posture stays `STRICT_FRESH_GROUNDED_MEMO_NON_ACTIONABLE` /
> `STRICT_FRESH_EVIDENCE_ONLY` (HOLD / NO_TRADE).

### 25.1 Inspected surfaces

- `research/actionable_handoff_candidate.py` + `research/actionable_handoff_preview.py` — the R2E.5b-0/1
  report-only chain; the candidate metadata already records `{path, schema_version, sha256}` source refs for
  the preview, support signals, evidence packet, and the active base candidate.
- `research/support_signals.py`, `research/handoff_compiler.py`, `research/research_anchors.py` — the
  deterministic grounding chain (`accepted_support_signals`, compilation modes, anchor staleness).
- `state/research_availability.py` — states + `_ALLOWED_ACTIONS_BY_STATE`; the evaluator is fed the compiled
  handoff and support signals but **never** the actionable candidate; relabeling only ever lands on
  HOLD/NO_TRADE states.
- `state/research_degraded_mode_gate.py` — Step 2 gate: hardcodes `ACTIONABLE_REQUIRED_STATE = "STRICT_FRESH"`
  and `REQUIRED_ACTIONS = ("NEW_BUY", "ORDER_COMPILATION")`; fails closed on missing/malformed permission.
- `state/final_execution_safety_gate.py` — final gate before Step 4 order compilation: independently
  re-requires `ACTIONABLE_REQUIRED_STATE = "STRICT_FRESH"`, `ORDER_COMPILATION` allowed, `NEW_BUY` when buy
  intent exists, structural Step 2/3 packet checks, no upstream block artifacts, no manual review.
- `workflow/step2_decision_builder.py` — `render_step2_prompt` re-enforces the Step 2 gate (defense in depth)
  and renders from `research_output.json` (the raw parsed handoff), not the compiled candidate.
- `workflow/weekly_orchestrator.py` — routes on the Step 2 gate evaluator's `.allowed`; not-allowed is a
  controlled `NO_TRADE` terminal (`weekly_outcome.json` + `run_summary.json`).
- `state/last_good_research_handoff.py` — writes only the strict-valid **raw normalized** candidate
  (`handoff_source="research_handoff_candidate"`); records `strategy_settings_hash` over
  `DECISION_RELEVANT_SETTINGS_KEYS` (incl. `hard_cap_open_orders_budget`, `max_new_tickers_per_week`) and the
  universe; the report-only actionable candidate is never passed to it today.
- `workflow/step4_order_compiler.py` — reads `hard_cap_open_orders_budget`, `target_new_buy_budget_this_run`,
  `max_new_tickers_per_week` from strategy settings (deterministic caps live downstream, as designed in §17).
- `cli/run_status.py` — currently has no promotion/actionable observability (a future surface).
- Validator tests + docs tests (`test_step1_evidence_first_research_design.py`,
  `test_step1_handoff_compiler_integration.py`) — prove the separation invariants this design must preserve
  until each explicit gate PR.

### 25.2 Promotion preconditions (deterministic checklist)

Promotion means: designating the validated actionable candidate as the **effective** research handoff for a
run. Every precondition below must pass deterministically (fail closed — any missing / malformed / stale input
⇒ not eligible, never a crash). Grouped:

**A. Input-chain validity & freshness**

1. `evidence_packet.json` present, `schema_version=evidence_packet_v1`, invariant-valid,
   `is_llm_generated=false`, and fresh (`as_of` within `fresh_days` of `now_date`).
2. `evidence_packet.research_anchors` `available:true`, `valid:true`, `valid_anchor_count ≥ 1`.
3. `analyst_memo.json` present and valid — compiled metadata `compilation_mode == evidence_plus_memo`; memo
   `confidence` is not `low`; memo `as_of_date` fresh.
4. `compiled_support_signals.json` present with `accepted_support_signals` non-empty,
   `permission_effect: "none"`, `not_authorization: true` (the artifact itself must still be report-only —
   promotion consumes it; it never self-authorizes).
5. `compiled_actionable_handoff_preview.json` present, `report_only: true`, `not_authorization: true`,
   `preview_actionable_rows` non-empty.

**B. Actionable-candidate quality**

6. `compiled_actionable_research_handoff_candidate.json` present and its validation artifact reports
   `validation_passed: true` (strict `validate_research_handoff`).
7. `candidate_actionable_row_count > 0` and `candidate_actionable_row_count ≤` the base-universe
   `max_new_tickers_per_week` cap (re-asserted a third time here; preview and candidate builder already
   enforce it).
8. Every `actionable_this_run` ticker ∈ `allowed_buy_tickers` (base universe); **no out-of-universe tickers**.
9. `optional_extended_etf_sleeve.enabled == false` and no extended-ETF ticker appears in any promoted row.
10. **No stale anchors at promotion time**: every anchor cited by a promoted row is re-checked against
    `now_date` (`valid_until ≥ now_date`); staleness is re-evaluated at promotion, not frozen at build time.
11. No blocking data gaps: no `data_gaps` entry with `blocking: true` in the evidence packet, and no DATA_GAP
    marker on any promoted row.

**C. Consistency (hash chain)**

12. **Source-hash match**: the candidate metadata's recorded `sha256` for
    `source_evidence_packet` / `source_compiled_support_signals` / `source_actionable_handoff_preview` /
    `source_active_compiled_handoff` must equal freshly recomputed hashes of the on-disk artifacts — the
    candidate must have been derived from exactly the bytes present now (protects against partial re-runs).
13. **Settings/universe match**: `strategy_settings_hash` over `DECISION_RELEVANT_SETTINGS_KEYS` at
    promotion time equals the hash at compile time, and the core∪satellite universe set is identical.

**D. Downstream budget context**

14. Strategy settings carry `hard_cap_open_orders_budget` and `max_new_tickers_per_week` (and Step 4 will
    additionally require `target_new_buy_budget_this_run` before compiling); promotion never proceeds into a
    run whose order compiler would lack its deterministic caps.

### 25.3 Promotion artifact strategy

| option | mechanism | safety | observability | accidental-consumption risk | rollback | verdict |
|---|---|---|---|---|---|---|
| **A. overwrite** `compiled_research_handoff_candidate.json` | promotion silently replaces the active file | poor — destroys the non-actionable baseline; every existing separation test breaks | poor (no decision record) | high — anything reading the active file becomes actionable with zero diff | hard (restore from where?) | **rejected** |
| **B. pointer** `active_research_handoff_source.json` | small deterministic pointer records which candidate is *effective* + hashes + the eligibility result | strong — both files stay put; missing/malformed pointer ⇒ consumers fall back to the active non-actionable candidate | strong (the pointer *is* the audit record) | low — consumers must opt in to pointer resolution | pointer flip / delete | **recommended core** |
| **C. availability reads the actionable candidate directly** | evaluator consumes it and relabels | weak — conflates the *observer* with the *promotion mechanism*; promotion becomes implicit | poor | highest | unclear | **rejected** |
| **D. new canonical** `research_handoff_candidate_effective.json` | materialized copy written only on eligibility pass | good if hash-stamped | good | low-moderate (stable filename invites casual readers) | delete file | **optional companion to B** |

**Recommendation: B as the single source of truth, with D as an optional materialized view.** The pointer
(`active_research_handoff_source.json`) records: `source` (`active_compiled_handoff` |
`promoted_compiled_actionable_handoff`), the referenced candidate path + `sha256`, the eligibility artifact
path + `sha256`, `promoted_at`, the earliest cited-anchor `valid_until` (an expiry), and `report_only` /
`permission_effect` markers until the gate PRs land. Consumption rules (future PRs): a consumer resolves the
pointer, re-verifies the `sha256` of whatever file it reads, and **falls back to the active non-actionable
candidate on any mismatch / absence** (fail closed). If D is added, the pointer remains the SSOT and the
effective copy is only valid when its hash matches the pointer. A silent overwrite (A) and implicit evaluator
consumption (C) are rejected outright.

### 25.4 Future availability states

Current non-actionable states stay exactly as-is: `STRICT_FRESH_EVIDENCE_ONLY` and
`STRICT_FRESH_GROUNDED_MEMO_NON_ACTIONABLE` both map to `["HOLD", "NO_TRADE"]` and must remain HOLD / NO_TRADE
forever (they describe *grounding without promotion*).

Recommended new states (names chosen so no existing gate literal ever matches them):

| state | introduced by | semantics | allowed_actions |
|---|---|---|---|
| `STRICT_FRESH_COMPILED_ACTIONABLE_PENDING_GATES` | R2E.5b-5 | eligibility passed + pointer written + hashes verified, but the gate PRs have not landed; promotion exists and is visible, gates stay closed | `["HOLD", "NO_TRADE"]` |
| `STRICT_FRESH_COMPILED_ACTIONABLE` | R2E.5b-6 (**first permission change**) | same preconditions **and** the explicit permission PR landed | `["HOLD", "NO_TRADE", "NEW_BUY", "ORDER_COMPILATION"]` — `SELL` / `ROTATION` / `REBALANCE` / `EXTENDED_ETF_ADMISSION` stay blocked |

- **Do not reuse `STRICT_FRESH`.** Both gates compare against the literal `STRICT_FRESH`; reusing it would
  silently open Step 2 *and* the final gate with zero diff at the gate layer — precisely the implicit
  permission change this program forbids. A new literal keeps both gates closed until each is explicitly
  changed in its own PR.
- **`STRICT_FRESH_WITH_LLM_MEMO` is superseded as a name** (already once replaced by the safety-explicit
  R2E.4 naming): what becomes actionable is the *compiled, anchor-grounded* handoff — naming the state after
  the LLM memo overstates the memo's authority. Keep the old name in this doc as a historical alias only.
- The buy-side compiled path deliberately does **not** grant `SELL`: sell-side validation is a separately
  deferred trigger (see the operator runbook), and `SELL` continues to come only from the raw
  `STRICT_FRESH` / `STRICT_STALE` states.

### 25.5 Gate change design (Step 2 + final execution safety gate)

Split across separate PRs, one gate each; both gates keep failing closed on anything unexpected.

- **Step 2 gate (R2E.5b-6):** replace the single-literal comparison with a tuple
  `ACTIONABLE_ALLOWED_STATES = ("STRICT_FRESH", "STRICT_FRESH_COMPILED_ACTIONABLE")`. `REQUIRED_ACTIONS`
  stays `("NEW_BUY", "ORDER_COMPILATION")`. When the state is `STRICT_FRESH_COMPILED_ACTIONABLE`, the gate
  additionally requires: the decision artifact's `source == "promoted_compiled_actionable_handoff"`; the
  promotion pointer exists; the pointer's `sha256` matches the effective candidate's content; the referenced
  eligibility artifact reports pass; and `now_date` has not passed the pointer's recorded earliest-anchor
  `valid_until`. Any failed check ⇒ blocked exactly as today.
- **Final execution safety gate (R2E.5b-7):** accept the same state tuple, keep
  `REQUIRED_ALLOWED_ACTION = "ORDER_COMPILATION"` and the buy-intent `NEW_BUY` check, and add two new
  deterministic checks for the promoted state: (a) **budget context present** —
  `hard_cap_open_orders_budget`, `max_new_tickers_per_week`, and `target_new_buy_budget_this_run` must be
  present before order compilation; (b) **promoted-ticker subset** — every buy-side ticker in the Step 2/3
  packets must be ∈ the effective handoff's `actionable_this_run_tickers` (the order compiler can never buy
  a ticker research did not promote).
- **Both gates verify source + hashes independently** (defense in depth, mirroring how Step 2 render already
  re-enforces its gate): neither gate ever trusts the other's verification.
- **Blocker artifact when promotion exists but a gate stays closed:** `step2_blocked_by_research_gate.json`
  (and the final-gate block artifact) gain `promotion_present: true`, the pointer path + hash, and a
  deterministic code `promotion_present_but_gates_closed` — the operator sees "a promoted candidate exists;
  the gate PR intentionally has not opened this state" instead of a generic state mismatch.

### 25.6 Last-good writer

- **Report-only actionable candidate: excluded — yes, and defensively.** Today the writer is only ever
  called with the raw normalized candidate; keep that, and add a defensive rejection of any candidate whose
  `schema_version == research_handoff_compiled_actionable_v1` or that carries `report_only: true` /
  `not_authorization: true` markers, so a future wiring mistake cannot seed last-good with a report-only
  artifact.
- **Promoted handoff eligibility: yes, but only after R2E.5b-7 and into a separate slot** —
  `last_good_promoted_handoff.json` + metadata, never overwriting the raw
  `last_good_research_handoff.json` (whose `DEGRADED_WITH_LAST_GOOD` semantics assume raw Deep Research
  provenance; mixing sources would corrupt the fallback contract).
- **Required metadata for a promoted last-good:** `handoff_source="promoted_compiled_actionable_handoff"`,
  promotion pointer hash, the full source-chain sha256s (evidence packet / memo / support signals / preview /
  candidate), the cited anchor ids **with their `valid_until` dates**, memo `as_of_date` + `confidence`,
  `strategy_settings_hash`, and the universe snapshot.
- **Stale-anchor / stale-memo protection:** a promoted last-good records
  `promoted_last_good_valid_until = min(cited anchor valid_until)` and is **re-checked at read time**: it is
  usable only while `now_date ≤ promoted_last_good_valid_until` and the memo age is within `fresh_days`;
  past either bound it degrades to observability-only (HOLD / NO_TRADE), never actionable. Staleness is
  always evaluated at consumption, never frozen at write.

### 25.7 Risk analysis

- **Preview accidentally promoted into the active trading path** — the preview's schema
  (`compiled_actionable_handoff_preview_v1`) is never an accepted consumer schema; promotion goes only
  through the pointer; consumers hash-verify; existing separation tests keep asserting the preview is never
  read by availability / Step 2 / weekly / the final gate.
- **Stale anchors** — three-layer defense: eligibility re-check at promotion (25.2 #10), pointer expiry
  (`valid_until`) re-check at each gate, and read-time re-check on any promoted last-good.
- **Memo overconfidence** — unchanged deterministic controls: `low` confidence never accepted, anchor
  `confidence_floor` must be met, the memo can never create anchors / tickers / budgets (denylists), and the
  memo is only ever one input into an anchor-grounded chain.
- **Source hash mismatch** (partial re-run, manual file edit) — eligibility fails closed; gates re-verify
  independently; result is the controlled NO_TRADE terminal, never a crash.
- **Step 2 over-interpreting the qualitative memo** — when Step 2 later consumes the effective handoff, the
  render should pass only the compiled deterministic fields; qualitative memo context stays display-only and
  clearly labeled non-authoritative (no memo text is ever re-promoted to authority by the prompt).
- **Order compiler receiving actionable rows without budget context** — final-gate precondition (25.5b):
  missing `hard_cap_open_orders_budget` / `max_new_tickers_per_week` / `target_new_buy_budget_this_run` ⇒
  blocked; the weekly cap is enforced at preview, candidate build, AND promotion eligibility.
- **Rollback complexity** — every step is a small artifact or constant: delete/flip the pointer ⇒ consumers
  fall back to the non-actionable active candidate; revert a gate PR ⇒ that gate closes again (new state
  literals guarantee reverts restore closed behavior); no step rewrites history.
- **Operator observability confusion** — the `PENDING_GATES` state name, the pointer-as-audit-record, the
  `promotion_present_but_gates_closed` blocker code, and run-status surfacing of
  eligibility/pointer/gate status together give a single-glance answer to "why is this run not actionable?".

### 25.8 Recommended PR sequence

| PR | scope | permission change? |
|---|---|---|
| **R2E.5b-2** (this) | design + docs tests only | none |
| **R2E.5b-3** ✅ implemented (§26) | deterministic promotion-eligibility checker, report-only: new module evaluating the 25.2 checklist into a promotion-eligibility artifact; consumed by nothing | none |
| **R2E.5b-4** ✅ implemented as report-only PREVIEW (§27) | pointer + effective handoff implemented as report-only *previews*; the real `active_research_handoff_source.json` (+ `research_handoff_candidate_effective.json`) remain **reserved** for a future explicit promotion PR; consumed by nothing | none |
| **R2E.5b-5** (split: **5a** ✅ real pointer writer (§28); **5b** ✅ availability recognition (§29)) | 5a: write the real pending-gates pointer artifacts. 5b: availability recognizes the verified pointer ⇒ new state `STRICT_FRESH_COMPILED_ACTIONABLE_PENDING_GATES`, **still `["HOLD", "NO_TRADE"]`**; blocker artifacts gain pending-gates blockers; weekly still terminates NO_TRADE | none (new state is HOLD/NO_TRADE) |
| **R2E.5b-6** | **first true permission change**: state `STRICT_FRESH_COMPILED_ACTIONABLE` with `NEW_BUY` + `ORDER_COMPILATION`; Step 2 gate accepts the state tuple + pointer/hash/expiry verification. The final gate is intentionally NOT changed yet, so Step 4 still blocks — the actionable surface opens one gate at a time | **yes** (Step 2 render path opens) |
| **R2E.5b-7** | final execution safety gate accepts the new state + budget-context + promoted-ticker-subset checks; weekly actionable-path polish; promoted last-good slot | yes (order compilation can complete) |
| **R2F** | shrink/deprecate the monolithic Deep Research strict handoff | none |

R2E.5b-6 is the first PR after which any live permission differs; everything before it is observability. Its
review must treat the `_ALLOWED_ACTIONS_BY_STATE` diff and the Step 2 gate tuple as the entire risk surface.

### 25.9 Non-goals (R2E.5b-2)

No promotion-eligibility checker, pointer, effective file, new availability state, gate change, weekly /
run-status change, last-good change, order-compiler change, or prompt change is implemented here. This PR does
**not** add `NEW_BUY` / `ORDER_COMPILATION` permission, does **not** enable `STRICT_FRESH_WITH_LLM_MEMO` or
either recommended future state, and leaves every live artifact, gate constant, and test invariant byte-for-byte
unchanged. Each subsequent step (R2E.5b-3 … R2E.5b-7) requires its own explicit PR.

## 26. R2E.5b-3 status (promotion-eligibility checker — implemented, report-only, no promotion)

R2E.5b-3 implements the deterministic **promotion-eligibility checker** designed in §25: it evaluates whether
the separate, report-only `compiled_actionable_research_handoff_candidate.json` **would** be eligible for a
*future* promotion — and writes only its own report-only artifact. **It never promotes.** No
`active_research_handoff_source.json` pointer and no `research_handoff_candidate_effective.json` effective
handoff is created (those remain the future R2E.5b-4 pointer PR); the active
`compiled_research_handoff_candidate.json` stays non-actionable; no gate, permission, `allowed_actions`,
availability state, Step 2/3/4 workflow, order compiler, or prompt changes. **No `NEW_BUY` /
`ORDER_COMPILATION` permission is added** and `STRICT_FRESH_COMPILED_ACTIONABLE` /
`STRICT_FRESH_COMPILED_ACTIONABLE_PENDING_GATES` / `STRICT_FRESH_WITH_LLM_MEMO` are **not** enabled. The live
posture stays `STRICT_FRESH_GROUNDED_MEMO_NON_ACTIONABLE` / `STRICT_FRESH_EVIDENCE_ONLY` (HOLD / NO_TRADE).

- **Module:** `src/investment_orchestrator/research/actionable_promotion_eligibility.py` — pure
  `evaluate_actionable_handoff_promotion_eligibility(...)` (never raises; any missing / malformed input fails
  its check deterministically) + disk wrapper `write_actionable_promotion_eligibility(...)`.
- **Artifact (new, separate):**
  `artifacts/current/step1_research/compiled_actionable_handoff_promotion_eligibility.json` (schema
  `compiled_actionable_handoff_promotion_eligibility_v1`). Top-level fields: `eligible_for_promotion`,
  `promotion_blockers[]`, `promotion_warnings[]`, `checks[]` (each
  `{check_id, passed, severity, details}`), `source_artifacts`, `source_hashes`, `hash_chain_valid`,
  `candidate_sha256` (content hash of the evaluated candidate, so the R2E.5b-4 pointer preview can verify it
  points at exactly the candidate this verdict approved),
  `candidate_validation_passed`, `candidate_actionable_row_count`, `preview_actionable_row_count`,
  `accepted_support_signal_count`, `actionable_this_run_tickers[]`, `strategy_settings_hash`,
  `earliest_anchor_valid_until`, `promotion_expires_at`, `today`, plus the standard report-only markers
  (`is_llm_generated: false`, `report_only: true`, `permission_effect: "none"`, `not_authorization: true`,
  `consumed_by_availability: false`, `consumed_by_step2: false`, `consumed_by_gates: false`).
- **Check groups (the §25.2 fail-closed checklist):**
  **A. input chain** — evidence packet present / `evidence_packet_v1` / `is_llm_generated:false`;
  `research_anchors` available+valid; `valid_anchor_count ≥ 1`; analyst memo present+valid (via the
  support-signal artifact); memo confidence not low; `accepted_support_signals` non-empty; preview present
  (schema + report markers); `preview_actionable_rows` non-empty.
  **B. candidate quality** — candidate present (schema + report markers); strict validation passed;
  `candidate_actionable_row_count > 0` and `≤` the base-universe `max_new_tickers_per_week` cap (cap must be
  present and `> 0`); extended ETF sleeve disabled and no extended ticker promoted; every promoted ticker in
  the base allowed universe; no DATA_GAP marker on any promoted row; `primary_anchor_event_id` /
  `primary_anchor_date_et` / event-or-theme refs present on every promoted row; every referenced anchor
  resolves in the packet, is valid/usable/not stale, and (re-checked at eligibility time) `valid_until ≥ today`.
  **C. hash chain** — the candidate metadata's recorded `sha256` for the evidence packet / support signals /
  preview are recomputed and must match (fail closed on missing metadata / hash / object); the active-base ref
  is verified when both sides are available, otherwise recorded as unverified (`match: null`); the current
  `strategy_settings_hash` (over `DECISION_RELEVANT_SETTINGS_KEYS`) must equal the hash recorded in the
  evidence packet; the candidate's `trade_universe.allowed_buy_tickers` must equal the packet universe.
  **D. budget context** — `hard_cap_open_orders_budget`, `target_new_buy_budget_this_run`, and
  `max_new_tickers_per_week` must be present in the packet's deterministic `budget_settings` snapshot (this
  never approves orders; it only confirms the deterministic caps exist before any future Step 4 path).
- **Blocker reason codes (deterministic; one owning check each):** `missing_evidence_packet`,
  `invalid_research_anchors`, `no_valid_research_anchor`, `analyst_memo_absent_or_invalid`,
  `memo_confidence_low`, `no_accepted_support_signals`, `missing_actionable_preview`,
  `no_preview_actionable_rows`, `missing_actionable_candidate`, `candidate_validation_failed`,
  `no_candidate_actionable_rows`, `max_new_tickers_cap_missing_or_zero`, `candidate_exceeds_max_new_tickers`,
  `extended_etf_enabled`, `out_of_universe_actionable_ticker`, `blocking_data_gap_on_actionable_row`,
  `missing_primary_anchor`, `stale_referenced_anchor`, `hash_chain_mismatch`,
  `strategy_settings_hash_mismatch`, `universe_mismatch`, `missing_budget_context`. **Warnings (never affect
  eligibility):** `recompiled_base_used_not_active_compiled_handoff`, `non_blocking_data_gaps_present`.
- **Anchor expiry:** `earliest_anchor_valid_until` = min `valid_until` among anchors referenced by promoted
  rows; `promotion_expires_at` carries the same date — a future promotion must never be consumed past it
  (recorded for the R2E.5b-4 pointer). Staleness is **re-checked at eligibility time** against `today`
  (settings `as_of`), not frozen at candidate-build time: an anchor that expired between build and check
  fails with `stale_referenced_anchor`.
- **Step 1 integration point:** report-only *layer 0f* in `parse_step1_output`, immediately after the
  R2E.5b-1 candidate (layer 0e). It only runs when the actionable candidate + metadata exist, re-reads the
  just-written report-only artifacts, and is fully defensive (a checker failure yields an empty summary and
  **never crashes Step 1 parse**). Paths surface in the parse result
  (`actionable_promotion_eligibility_path` / `actionable_promotion_eligible`).
- **No-promotion invariant (proven by tests):** even when `eligible_for_promotion: true`, **nothing is
  promoted** — no `active_research_handoff_source.json` / `research_handoff_candidate_effective.json` exists,
  the active compiled handoff stays non-actionable (`positive_delta_research_supported=[]`, no
  `actionable_this_run` row), availability stays `STRICT_FRESH_GROUNDED_MEMO_NON_ACTIONABLE` with
  `allowed_actions=["HOLD","NO_TRADE"]`, the degraded-mode decision never references the eligibility
  artifact, the Step 2 research gate still blocks, and `run_weekly` still terminates `NO_TRADE`. The artifact
  is **not** consumed by the availability evaluator, the degraded-mode decision, Step 2 render, the weekly
  path, the order compiler, or any gate.
- **Next (future, separate PRs):** R2E.5b-4 creates the pointer (`active_research_handoff_source.json`),
  R2E.5b-5 the `PENDING_GATES` availability state (still HOLD / NO_TRADE), R2E.5b-6/7 the explicit gate
  openings. None of that is done here.

## 27. R2E.5b-4 status (promotion pointer PREVIEW + effective-handoff PREVIEW — implemented, report-only, nothing promoted)

R2E.5b-4 implements the §25.3 pointer strategy as a **report-only PREVIEW**: it shows what the future active
pointer and effective handoff *would* look like — **without making them active or consumed**. `would_promote:
true` is **strictly diagnostic**. The reserved real-promotion names
`active_research_handoff_source.json` and `research_handoff_candidate_effective.json` are **NOT created** —
they stay reserved for a future explicit promotion PR (R2E.5b-5 may create the real pointer and/or the
`STRICT_FRESH_COMPILED_ACTIONABLE_PENDING_GATES` availability state; neither is done here). No consumer reads
the previews: they are never fed into the availability evaluator, the degraded-mode decision, Step 2 render,
the weekly path, the order compiler, or any gate. No gate, permission, `allowed_actions`, availability state,
Step 2/3/4 workflow, order compiler, or prompt changes. **No `NEW_BUY` / `ORDER_COMPILATION` permission is
added** and `STRICT_FRESH_COMPILED_ACTIONABLE` / `STRICT_FRESH_COMPILED_ACTIONABLE_PENDING_GATES` are **not**
enabled. The live posture stays `STRICT_FRESH_GROUNDED_MEMO_NON_ACTIONABLE` / `STRICT_FRESH_EVIDENCE_ONLY`
(HOLD / NO_TRADE).

- **Module:** `src/investment_orchestrator/research/actionable_promotion_pointer_preview.py` — pure
  `build_actionable_promotion_pointer_preview(...)` (never raises; missing / malformed inputs yield
  `would_promote: false` with deterministic blockers) + disk wrapper
  `write_actionable_promotion_pointer_preview(...)`.
- **Artifacts (new, separate, report-only):**
  `artifacts/current/step1_research/compiled_actionable_handoff_promotion_pointer_preview.json` (schema
  `compiled_actionable_handoff_promotion_pointer_preview_v1`); when `would_promote: true` also
  `compiled_actionable_research_handoff_effective_preview.json` (a byte-identical, **unmutated** copy of the
  actionable candidate — promotion metadata lives only in the pointer preview so the strict validator sees the
  handoff body unchanged) and `compiled_actionable_research_handoff_effective_preview_validation.json`
  (re-validated with the existing `validate_research_handoff`). When `would_promote: false` the effective
  preview files are not written — the pointer preview's explicit `pointer_blockers[]` are the record.
- **Pointer-preview fields:** `would_promote`,
  `promotion_source: "compiled_actionable_research_handoff_candidate"`, `candidate_path`, `candidate_sha256`,
  `candidate_schema_version`, `eligibility_path`, `eligibility_sha256`, `eligibility_schema_version`,
  `eligibility_hash` (alias of `eligibility_sha256` under the future real pointer's field name),
  `candidate_validation_passed`, `candidate_actionable_row_count`, `actionable_this_run_tickers[]`,
  `earliest_anchor_valid_until`, `promotion_expires_at`, `source_chain_hashes`, `pointer_blockers[]`,
  `pointer_warnings[]`, `effective_preview_written` / `effective_preview_path` /
  `effective_preview_validation_path` / `effective_preview_valid`,
  `reserved_active_pointer_path: "artifacts/current/step1_research/active_research_handoff_source.json"`,
  `reserved_effective_handoff_path: "artifacts/current/step1_research/research_handoff_candidate_effective.json"`,
  plus the loud no-promotion markers `active_pointer_created: false`, `effective_handoff_created: false`,
  `future_pr_required: true`, and the standard report-only markers (`is_llm_generated: false`,
  `report_only: true`, `permission_effect: "none"`, `not_authorization: true`,
  `consumed_by_availability: false`, `consumed_by_step2: false`, `consumed_by_gates: false`).
- **`would_promote` decision rules (all fail closed):** the R2E.5b-3 eligibility artifact exists, has the
  expected schema, carries valid report-only / not-authorization / `permission_effect: "none"` markers, and
  reports `eligible_for_promotion: true`; the actionable candidate exists with its report markers intact; the
  strict validation artifact passes (and metadata `validation_passed` agrees); the candidate's recomputed
  content hash equals the `candidate_sha256` the eligibility verdict approved (a candidate edited after the
  eligibility check can never preview-promote); `promotion_expires_at` is present and not past `today`
  (falling back to the eligibility artifact's own `today`; unverifiable expiry fails closed); the candidate
  has `> 0` actionable rows whose count/tickers are consistent with the eligibility verdict; and the
  eligibility's `source_chain_hashes` are present with every required entry matched (`hash_chain_valid`).
- **Blocker reason codes:** `eligibility_missing`, `eligibility_malformed`, `eligibility_not_eligible`,
  `permission_markers_invalid`, `candidate_missing`, `candidate_validation_failed`,
  `candidate_hash_mismatch`, `candidate_expired`, `no_candidate_actionable_rows`, `source_chain_missing`.
  **Warnings (never affect `would_promote`):** `active_compiled_handoff_hash_unverified`.
- **Step 1 integration point:** report-only *layer 0g* in `parse_step1_output`, immediately after the
  R2E.5b-3 eligibility layer (0f). It only runs when the eligibility artifact exists and is fully defensive
  (a builder failure yields an empty summary and **never crashes Step 1 parse**). Paths surface in the parse
  result (`actionable_promotion_pointer_preview_path` / `actionable_promotion_would_promote` /
  `actionable_effective_handoff_preview_path` / `actionable_effective_handoff_preview_validation_path`).
- **Nothing-promoted invariant (proven by tests):** even on a `would_promote: true` run, the reserved
  `active_research_handoff_source.json` / `research_handoff_candidate_effective.json` do **not** exist; the
  effective preview is a distinct file that equals the candidate and validates, while the active
  `compiled_research_handoff_candidate.json` stays non-actionable
  (`positive_delta_research_supported=[]`, no `actionable_this_run` row); availability stays
  `STRICT_FRESH_GROUNDED_MEMO_NON_ACTIONABLE` with `allowed_actions=["HOLD","NO_TRADE"]`; the degraded-mode
  decision contains no reference to the eligibility / pointer / effective previews; the Step 2 research gate
  still blocks; and `run_weekly` still terminates `NO_TRADE`.
- **Next (future, separate PRs):** R2E.5b-5 may create the real pointer and/or the
  `STRICT_FRESH_COMPILED_ACTIONABLE_PENDING_GATES` state (still HOLD / NO_TRADE); R2E.5b-6/7 are the explicit
  gate openings. None of that is done here.

## 28. R2E.5b-5a status (REAL active pointer writer — pending-gates artifacts, HOLD / NO_TRADE unchanged)

R2E.5b-5a creates the **real** promotion-pointer artifacts (superseding §27's "reserved / NOT created"
status): when the R2E.5b-4 pointer preview reports `would_promote: true` and every fail-closed creation rule
passes, Step 1 now writes `active_research_handoff_source.json`, a byte-identical
`research_handoff_candidate_effective.json`, and `research_handoff_candidate_effective_validation.json`.
**This PR creates artifacts only — it does not make them trading authority.** In 5a itself, nothing read the
pointer or the effective handoff: the availability evaluator, degraded-mode decision, Step 2 render, weekly
path, order compiler, and every gate stayed unchanged and keyed off the non-actionable active
`compiled_research_handoff_candidate.json`. The pointer explicitly carries
`promotion_status: "pending_gates"` and `permission_effect: "none_until_consumed_by_future_gate_pr"` — it is
**not** trading authorization. **No `NEW_BUY` / `ORDER_COMPILATION` permission is added** and
`STRICT_FRESH_COMPILED_ACTIONABLE` is **not** enabled;
the live posture stays `STRICT_FRESH_GROUNDED_MEMO_NON_ACTIONABLE` / `STRICT_FRESH_EVIDENCE_ONLY`
(HOLD / NO_TRADE). **R2E.5b-5b** (§29) is the follow-on availability *recognition* of this pointer (the
`PENDING_GATES` state, still HOLD / NO_TRADE); the R2E.5b-6/7 gate openings remain future explicit PRs.

- **Module:** `src/investment_orchestrator/research/actionable_promotion_pointer.py` —
  `write_actionable_promotion_pointer_if_eligible(...)` (never raises; fail-closed).
- **Artifacts (real, pending gates, unconsumed):**
  `artifacts/current/step1_research/active_research_handoff_source.json` (schema
  `active_research_handoff_source_v1`), `.../research_handoff_candidate_effective.json` (byte-identical copy
  of the R2E.5b-4 effective preview — the strict handoff body is **not** mutated and carries no wrapper
  metadata), `.../research_handoff_candidate_effective_validation.json` (independent re-validation with the
  existing `validate_research_handoff`; it **must pass or the pointer is not created**), plus an
  always-written outcome record `.../active_research_handoff_source_write_status.json` (schema
  `active_research_handoff_source_write_status_v1`, `active_pointer_created: true/false` +
  `pointer_blockers[]`).
- **Pointer fields:** `source: "promoted_compiled_actionable_handoff"`,
  `promotion_status: "pending_gates"`, `active_pointer_created: true`, `effective_handoff_created: true`,
  `permission_effect: "none_until_consumed_by_future_gate_pr"`, `not_authorization: true`,
  `candidate_path` / `candidate_sha256` / `candidate_schema_version` / `candidate_validation_passed`,
  `effective_handoff_path` / `effective_handoff_sha256` (equals the approved `candidate_sha256`),
  `effective_validation_path`, `eligibility_path` / `eligibility_sha256`, `pointer_preview_path` /
  `pointer_preview_sha256`, `candidate_actionable_row_count`, `actionable_this_run_tickers[]`,
  `earliest_anchor_valid_until`, `promotion_expires_at`, `source_chain_hashes`, `created_at`,
  `consumed_by_availability: false`, `consumed_by_step2: false`, `consumed_by_gates: false`,
  `future_pr_required: true`.
- **Creation rules (all fail closed):** the pointer preview exists with the expected schema, internally
  consistent `would_promote`/`pointer_blockers`, and intact report-only / `permission_effect: "none"` /
  `not_authorization` / `future_pr_required` / `active_pointer_created: false` markers;
  `would_promote: true`; the effective preview exists (candidate schema); its validation artifact passes AND
  the exact body about to be written independently re-passes `validate_research_handoff`; the effective
  body's recomputed sha256 equals the preview's approved `candidate_sha256`; `promotion_expires_at` is
  present and not past `today` (falling back to the preview's own `today`; unverifiable expiry fails
  closed); and `candidate_actionable_row_count > 0` with non-empty tickers.
- **Blocker reason codes:** `pointer_preview_missing`, `pointer_preview_malformed`,
  `preview_markers_invalid`, `would_promote_false`, `effective_preview_missing`,
  `effective_preview_validation_failed`, `effective_hash_mismatch`, `promotion_expired`,
  `no_actionable_rows`.
- **Fail-closed hygiene:** on any failed rule no pointer / effective files are written, and any **stale**
  pointer files from a previous promotable run are removed (recorded in the status artifact's
  `removed_stale_artifacts`) — the pointer file exists **iff** the latest run was promotable, so a stale
  pointer can never linger for a future consumer.
- **Step 1 integration point:** *layer 0h* in `parse_step1_output`, immediately after the R2E.5b-4 preview
  layer (0g). Runs only when the pointer-preview artifact exists; fully defensive (a writer crash yields an
  empty summary and **never breaks Step 1 parse**). Paths surface in the parse result
  (`active_pointer_created` / `active_research_handoff_source_path` / `effective_research_handoff_path` /
  `active_pointer_write_status_path`).
- **5a nothing-consumed invariant (proven by tests at that step):** on a run where the real pointer IS created, the active
  compiled handoff remains non-actionable (`positive_delta_research_supported=[]`, no `actionable_this_run`
  row), availability stays `STRICT_FRESH_GROUNDED_MEMO_NON_ACTIONABLE` with
  `allowed_actions=["HOLD","NO_TRADE"]`, neither the availability artifact nor the degraded-mode decision
  contains any reference to `active_research_handoff_source` or the effective handoff, the Step 2 research
  gate still blocks, and `run_weekly` still terminates `NO_TRADE`. At 5a, both gates still hardcoded the
  literal `STRICT_FRESH` and the proposed future states remained absent from `_ALLOWED_ACTIONS_BY_STATE`.

## 29. R2E.5b-5b status (availability recognizes pending-gates pointer — implemented, HOLD / NO_TRADE only)

R2E.5b-5b is a conservative **availability state-semantics** change. The availability evaluator may now
recognize that a real promoted actionable handoff exists via `active_research_handoff_source.json` and
`research_handoff_candidate_effective.json`, but only as a pending-gates diagnostic. **No production gate is
opened.** The new state is **`STRICT_FRESH_COMPILED_ACTIONABLE_PENDING_GATES`** and its allowed actions are
exactly `["HOLD", "NO_TRADE"]`; blocked actions include `SELL`, `NEW_BUY`, `ROTATION`, `REBALANCE`,
`EXTENDED_ETF_ADMISSION`, and `ORDER_COMPILATION`.

- **Recognition criteria (all required; fail closed otherwise):** the run would otherwise be
  `STRICT_FRESH_GROUNDED_MEMO_NON_ACTIONABLE`; the active pointer exists with schema
  `active_research_handoff_source_v1`; `promotion_status == "pending_gates"`; `source ==
  "promoted_compiled_actionable_handoff"`; `not_authorization == true`; `future_pr_required == true`;
  `permission_effect == "none_until_consumed_by_future_gate_pr"`; `consumed_by_availability`,
  `consumed_by_step2`, and `consumed_by_gates` are all false; the effective handoff and validation artifacts
  exist; the effective validation passes; the effective handoff sha256 matches the pointer; `promotion_expires_at`
  is present and not stale; `candidate_actionable_row_count > 0`; and `actionable_this_run_tickers` is non-empty.
  A raw valid+fresh `STRICT_FRESH` still wins and is never relabeled.
- **Artifact fields (`research_availability.json` / `research_degraded_mode_decision.json`):**
  `promoted_pointer_present`, `promoted_pointer_valid`, `promotion_status`, `effective_handoff_present`,
  `effective_handoff_valid`, `candidate_actionable_row_count`, `actionable_this_run_tickers`,
  `promotion_expires_at`, `permission_effect`, `not_authorization`, and
  `source_artifacts.active_research_handoff_source` /
  `source_artifacts.research_handoff_candidate_effective` /
  `source_artifacts.research_handoff_candidate_effective_validation`. Blocker reasons include
  `promoted_actionable_handoff_pending_gates`, `new_buy_requires_future_gate_pr`, and
  `order_compilation_requires_future_gate_pr`. These are deterministic artifacts, not LLM-generated.
- **Step 1 integration point:** `_compiled_handoff_availability_inputs()` passes the pointer, effective handoff,
  effective validation, and source-artifact paths into `evaluate_research_availability(...)`. The effective
  handoff is **not** passed to Step 2, the active `compiled_research_handoff_candidate.json` behavior is
  unchanged, `research_handoff_candidate.json` remains the raw path, and no order artifacts are created.
- **Fail-closed behavior:** missing pointer keeps the existing grounded state; malformed markers, stale expiry,
  hash mismatch, failed effective validation, consumed markers, zero actionable rows, or missing tickers all
  keep the existing safe state and never become actionable.
- **Gate / weekly / run-status behavior:** the Step 2 gate still requires literal `STRICT_FRESH` plus
  `NEW_BUY` and `ORDER_COMPILATION` in `allowed_actions`, so the pending-gates state blocks before any Step 2
  prompt or decision packet is generated (recommended `NO_TRADE`). The final execution safety gate still
  requires literal `STRICT_FRESH` and `ORDER_COMPILATION`; it also remains closed. `run_weekly` treats the
  state as controlled `NO_TRADE` (`actionable=false`, exit 0), writes `run_summary.json` with
  `run_blocked=true` and `recommended_result: NO_TRADE`, and produces no Step 2/3/4 downstream artifacts.
- **Still not enabled:** `STRICT_FRESH_COMPILED_ACTIONABLE` remains absent / non-enabled. `NEW_BUY` and
  `ORDER_COMPILATION` require future explicit gate PRs (R2E.5b-6/7).

## 30. R2E.5b-6-design (first permission-change design — Step 2 decision-only recommended)

R2E.5b-6-design is **DESIGN / INSPECTION ONLY**. It does not change production behavior, does not change
`_ALLOWED_ACTIONS_BY_STATE`, does not change the Step 2/3/4 workflow, does not change the order compiler, does
not change prompts, does not change either gate, and does **not** add `NEW_BUY` / `ORDER_COMPILATION`
permission. `STRICT_FRESH_COMPILED_ACTIONABLE` remains absent / non-enabled. The live posture remains
`STRICT_FRESH_COMPILED_ACTIONABLE_PENDING_GATES` with exactly `["HOLD", "NO_TRADE"]`; the current Step 2 gate
still requires literal `STRICT_FRESH` plus `("NEW_BUY", "ORDER_COMPILATION")`, and the final execution safety
gate still requires literal `STRICT_FRESH` plus `ORDER_COMPILATION`.

Explicit invariant for this design PR: it **does not change `_ALLOWED_ACTIONS_BY_STATE`**.

### 30.1 Boundary recommendation

Recommended first permission boundary: **Option A — Step 2 decision-only**.

| Option | Scope | Risk | Recommendation |
|---|---|---|---|
| A. Step 2 only | Allow render + manual Step 2 decision packet from the promoted effective handoff; Step 3/4 remain blocked | Lowest useful permission change; lets operators inspect LLM decision quality without an order path | **Recommend** |
| B. Step 2 + Step 3 audit | Allow decision and audit; Step 4/final gate remain blocked | More observability, but Step 3 audited packets can be mistaken for order readiness | Defer until Step 2-only artifacts and operator UX are proven |
| C. Full Step 2→4 path | Allow `NEW_BUY` + `ORDER_COMPILATION` if all gates pass | Highest risk; couples prompt, audit, final gate, budgets, and orders in one PR | Do **not** use as first permission PR |
| D. Keep pending-gates | No new permission | Safest, but no LLM decision dry-run from the promoted handoff | Current state; useful fallback if Step 2-only design is not accepted |

The design separates four permissions that are currently entangled by the old Step 2 gate:

1. **Step 2 render / LLM decision** — first permission change, decision-only.
2. **Step 3 audit** — still blocked in the first PR unless a later explicit audit-only PR opens it.
3. **Step 4 order compilation** — still blocked; no `ORDER_COMPILATION`.
4. **Final execution safety gate** — unchanged and still closed for promoted states.

The final execution safety gate still requires literal `STRICT_FRESH` in the current production code.

### 30.2 State / action model

Do **not** reuse `NEW_BUY` or `ORDER_COMPILATION` for the first Step 2-only PR. Today, the Step 2 gate treats
`ORDER_COMPILATION` as part of its entry condition, so adding only `NEW_BUY` would still block; adding both
would accidentally make the permission artifact look order-eligible to downstream code and operators.

Recommended new action literal for a future implementation PR:

```text
PROMOTED_RESEARCH_DECISION
```

Rationale:

- It names the specific permission: Step 2 may render a decision from promoted research.
- It is not an order action and must never be accepted by Step 4.
- It is clearer than `STEP2_DECISION` because it applies only to the promoted/effective handoff path, not raw
  `STRICT_FRESH`.
- It avoids the ambiguous `ORDER_COMPILATION_PENDING_FINAL_GATE`, which contains the order permission name and
  can be misread as a partial order-compiler release.

Future state split:

- Keep `STRICT_FRESH_COMPILED_ACTIONABLE_PENDING_GATES` as HOLD / NO_TRADE only.
- Add a future decision-only state such as
  **`STRICT_FRESH_COMPILED_ACTIONABLE_STEP2_DECISION_ONLY`** when the first true permission PR lands.
- Map that future state to `["HOLD", "NO_TRADE", "PROMOTED_RESEARCH_DECISION"]` only.
- Keep `NEW_BUY` and `ORDER_COMPILATION` absent until the later order-path PRs.
- Reserve `STRICT_FRESH_COMPILED_ACTIONABLE` for the full order-eligible state, not for Step 2-only.

### 30.3 Effective handoff consumption design

When Step 2 is opened in a future PR, Step 2 should read
`research_handoff_candidate_effective.json`, not the raw `research_output.json`, for the promoted path. The
effective handoff must be treated as deterministic compiled research context, not as trade authorization.

Future Step 2 source rules:

- `research_degraded_mode_decision.json` remains required and remains the permission source.
- `active_research_handoff_source.json` is required for the promoted path.
- `research_handoff_candidate_effective.json` is required for the promoted path.
- `research_handoff_candidate_effective_validation.json` is required and must pass.
- Step 2 re-verifies pointer schema `active_research_handoff_source_v1`, `source:
  "promoted_compiled_actionable_handoff"`, `promotion_status`, `permission_effect:
  "none_until_consumed_by_future_gate_pr"`, `not_authorization`, `future_pr_required`, and consumed markers.
- Step 2 recomputes the effective handoff sha256 and checks it against the pointer before rendering.
- Step 2 re-checks `promotion_expires_at` at render time; if the pointer expired between Step 1 and Step 2,
  rendering blocks.
- Step 2 checks that `candidate_actionable_row_count > 0` and `actionable_this_run_tickers` are non-empty and
  match the effective handoff's actionable rows.
- The rendered prompt should include explicit source metadata: `source=promoted_compiled_actionable_handoff`,
  `promotion_status`, `promotion_expires_at`, `actionable_this_run_tickers`, anchor ids / event refs already in
  the effective handoff, and a clear statement that the source is **not raw Deep Research** and is **not order
  authorization**.
- The existing `research_degraded_mode_decision` should still be included so Step 2 sees the permission state,
  blocker language, and not-authorization markers.

The future prompt should not include the pointer as free-form authority text. It should include only a compact
deterministic source summary plus the effective handoff JSON body, so the LLM cannot reinterpret marker prose as
permission.

### 30.4 Future Step 2 gate checks

The future Step 2 decision-only gate should be a separate mode from the current order-generating gate. Suggested
shape:

- Current mode remains unchanged: `STRICT_FRESH` + `NEW_BUY` + `ORDER_COMPILATION` ⇒ existing raw actionable
  render path.
- New promoted decision-only mode:
  - state is `STRICT_FRESH_COMPILED_ACTIONABLE_STEP2_DECISION_ONLY`;
  - `allowed_actions` contains `PROMOTED_RESEARCH_DECISION`;
  - `allowed_actions` does **not** contain `NEW_BUY` or `ORDER_COMPILATION`;
  - active pointer exists and has schema `active_research_handoff_source_v1`;
  - pointer `source == "promoted_compiled_actionable_handoff"`;
  - pointer status indicates the expected Step 2-only state, not final order authorization;
  - pointer hash matches the effective handoff;
  - effective validation passes;
  - promotion is not expired;
  - `candidate_actionable_row_count > 0`;
  - `actionable_this_run_tickers` exactly match effective handoff actionable rows;
  - extended ETF sleeve remains disabled for v1;
  - budget context is present as diagnostics if already available, but final budget enforcement remains deferred
    to the final execution safety gate / order path.

If this mode renders Step 2, it should also write a deterministic **Step 2 decision-only permission artifact**
or mark the Step 2 output as `decision_only: true`, `order_compilation_allowed: false`, and
`not_execution_authorization: true`. Step 3 and Step 4 guards should refuse to proceed from a decision-only Step
2 artifact unless a later explicit audit/order PR changes them.

### 30.5 Weekly behavior

Conservative recommendation: keep weekly terminal `NO_TRADE` until the final order path is deliberately opened.
Do not let weekly automatically run Step 2 for promoted states in the first permission PR.

For a future Step 2-only PR:

- CLI/manual `run_step2 render` may be allowed for the promoted decision-only state.
- `run_weekly` should remain a controlled non-order terminal by default:
  `terminal_result=NO_TRADE_PENDING_FINAL_GATES`, `actionable=false`, exit 0.
- If a manual flag is later added, it may render Step 2 and then stop with
  `terminal_result=NO_TRADE_PENDING_FINAL_GATES`; it must not call Step 3/4 automatically.
- `weekly_outcome.json` should clearly say that Step 2 decision-only was allowed or available, but final gates
  and order compilation remain closed.

This avoids operator confusion where a weekly command appears to progress through an actionable workflow but
still must end in no orders.

### 30.6 Last-good recommendation

Do **not** write the Step 2-only promoted handoff to the existing last-good slot. A decision-only run is not an
audited, order-eligible run, and treating it as last-good would blur the boundary between research observability
and execution authorization.

Recommended later path:

- No last-good write until the full final-gate/order path is enabled and audited.
- If useful, create a separate promoted-research slot only after the final gate PR, for example
  `last_good_promoted_handoff.json`, with independent expiry and pointer hash checks.
- The existing last-good research fallback must not consume the Step 2-only promoted handoff.

### 30.7 Risk analysis

- **LLM sees actionable rows before orders are open:** Step 2 may generate buy-like language. Mitigate by
  `decision_only`, `not_execution_authorization`, no `ORDER_COMPILATION`, and no Step 3/4 progression.
- **Step 2 output misread as authorization:** require explicit output artifact markers and weekly/run-summary
  wording that says `NO_TRADE_PENDING_FINAL_GATES`.
- **Accidental Step 3/4 progression:** Step 3/4 guards must reject decision-only artifacts; Step 4 final gate
  remains unchanged and still requires literal `STRICT_FRESH` + `ORDER_COMPILATION`.
- **Pointer expiry mid-run:** Step 2 must re-check expiry at render time, not trust the Step 1 availability
  result.
- **Source hash mismatch:** Step 2 must recompute the effective handoff sha256 and compare to the active pointer.
- **Prompt confusion between raw Deep Research and compiled promoted handoff:** prompt source metadata must say
  promoted compiled handoff, not raw Deep Research; raw `research_output.json` should not be the promoted-path
  research body.
- **Operator confusion after Step 2:** weekly should remain `NO_TRADE_PENDING_FINAL_GATES` until final gates are
  opened, and manual Step 2 should emit decision-only markers.

### 30.8 Recommended implementation sequence

1. **R2E.5b-6-design** — this section and docs-content tests only; no behavior change.
2. **R2E.5b-6a** — add a reusable promoted-pointer verification helper for gates; no behavior change. It
   should verify schema, marker fields, hash, effective validation, expiry, ticker/actionable-row consistency,
   and extended-ETF exclusion.
3. **R2E.5b-6b** — add a report-only Step 2 gate dry-run artifact for promoted decision-only eligibility; no
   render permission yet.
4. **R2E.5b-6c** — first true permission change: add `PROMOTED_RESEARCH_DECISION` and
   `STRICT_FRESH_COMPILED_ACTIONABLE_STEP2_DECISION_ONLY`; allow `run_step2 render` to use the effective
   handoff in decision-only mode; Step 3/4 remain blocked and weekly remains `NO_TRADE_PENDING_FINAL_GATES`.
5. **R2E.5b-6d** — optional audit-only PR if desired: Step 3 can audit decision-only packets, but Step 4 and
   final safety remain closed.
6. **R2E.5b-7** — final gate + order compilation permission. This is where `NEW_BUY`, `ORDER_COMPILATION`,
   promoted-ticker subset checks, deterministic budget checks, and any promoted last-good slot are enabled.

The first true permission change is **R2E.5b-6c**, and it is Step 2 decision-only. It must not add
`NEW_BUY` or `ORDER_COMPILATION`.

## 31. R2E.5b-6a status (promoted pointer verification helper, no behavior change)

**R2E.5b-6a** adds a reusable pure verifier for the future Step 2 decision-only gate. It is a helper only: no
Step 1 report-only artifact is added in this PR, no workflow consumes the helper, and no production behavior
changes. The active live state remains `STRICT_FRESH_COMPILED_ACTIONABLE_PENDING_GATES` with exactly
`["HOLD", "NO_TRADE"]`; `_ALLOWED_ACTIONS_BY_STATE` is unchanged; `PROMOTED_RESEARCH_DECISION` is not added;
`NEW_BUY` and `ORDER_COMPILATION` remain absent from the promoted pending-gates state.

The helper lives at:

```text
src/investment_orchestrator/research/promoted_handoff_verifier.py
```

Public API:

```python
verify_promoted_handoff_for_step2_decision(
    *,
    active_pointer: Mapping[str, Any] | None,
    effective_handoff: Mapping[str, Any] | None,
    effective_validation: Mapping[str, Any] | None,
    today: date | None = None,
) -> dict[str, Any]
```

The function is deterministic, pure, fail-closed, and must never raise. It returns schema
`promoted_handoff_step2_verification_v1` and `is_llm_generated: false`. The result includes
`valid_for_step2_decision`, `verification_blockers`, `verification_warnings`, `checks`, `source`,
`promotion_status`, `pointer_permission_effect`, `permission_effect: "none"`, `not_authorization`,
`candidate_actionable_row_count`, `actionable_this_run_tickers`, `promotion_expires_at`,
`effective_handoff_sha256`, `pointer_effective_handoff_sha256`, `effective_validation_valid`,
`consumed_by_step2`, `future_permission_required: "PROMOTED_RESEARCH_DECISION"`, and `report_only: true`.
This result is diagnostic only; `future_permission_required` names the later PR boundary and is not live
authorization.

### 31.1 Verification criteria

The verifier accepts only a fully consistent promoted handoff candidate for future Step 2 decision-only use:

- active pointer exists and is a mapping;
- pointer schema is `active_research_handoff_source_v1`;
- pointer source is `promoted_compiled_actionable_handoff`;
- pointer `promotion_status == "pending_gates"`;
- pointer has safe markers: `not_authorization == true`, `future_pr_required == true`, `consumed_by_availability
  == false`, `consumed_by_step2 == false`, `consumed_by_gates == false`, and `permission_effect ==
  "none_until_consumed_by_future_gate_pr"`;
- `promotion_expires_at` is present and not stale as of `today`;
- `candidate_actionable_row_count > 0` and `actionable_this_run_tickers` is non-empty;
- effective handoff exists, has schema `research_handoff_compiled_actionable_v1`, and its canonical sha256
  matches `effective_handoff_sha256` in the pointer;
- effective handoff actionable rows, `positive_delta_research_supported`, and pointer
  `actionable_this_run_tickers` match exactly; when `trade_universe.allowed_buy_tickers` is present, promoted
  tickers must be a subset of that universe;
- optional extended ETF sleeve remains disabled for v1;
- effective validation exists and reports `valid == true` or `validation_passed == true`; if validation carries
  hash fields such as `candidate_sha256`, `effective_handoff_sha256`, `handoff_sha256`, or `source_sha256`, each
  present hash must match the recomputed effective handoff sha256.

Blocker reason-code contract:

```text
pointer_missing
pointer_malformed
pointer_schema_invalid
pointer_source_invalid
pointer_status_invalid
pointer_permission_markers_invalid
promotion_expired
no_actionable_rows
effective_handoff_missing
effective_handoff_hash_mismatch
effective_handoff_schema_invalid
effective_handoff_actionable_ticker_mismatch
effective_handoff_extended_sleeve_enabled
effective_validation_missing
effective_validation_failed
```

### 31.2 No gate or workflow changes

This PR deliberately does not call the helper from Step 1, Step 2, Step 3, Step 4, weekly, prompts, or the order
compiler. The Step 2 gate still requires literal `STRICT_FRESH` plus `("NEW_BUY", "ORDER_COMPILATION")`; the
final execution safety gate still requires literal `STRICT_FRESH` plus `ORDER_COMPILATION`. Therefore promoted
pending-gates research still blocks before Step 2 render and still cannot reach Step 3, Step 4, order
compilation, or execution.

Recommended next step remains **R2E.5b-6b**: add a report-only Step 2 gate dry-run artifact that may call this
helper, but still grants no render permission and adds no order-path action.

## 32. R2E.5b-6b status (promoted Step 2 gate dry-run, report-only)

**R2E.5b-6b** adds a report-only DRY-RUN of the future Step 2 promoted decision-only gate. It answers two
questions per run: *if a future PR added the `PROMOTED_RESEARCH_DECISION` permission, would this promoted
handoff be sufficient for Step 2 decision-only?* and *why is real Step 2 still not allowed today?*

**`would_allow_step2_promoted_decision: true` is diagnostic only — it is NOT permission.** At R2E.5b-6b the
real Step 2 gate was unchanged and remained closed: it required literal `STRICT_FRESH` plus
`("NEW_BUY", "ORDER_COMPILATION")`, so every promoted pending-gates run still blocked before any Step 2 prompt
or decision packet was generated. **No `PROMOTED_RESEARCH_DECISION` permission was added by R2E.5b-6b** (and no
`NEW_BUY` / `ORDER_COMPILATION`): `_ALLOWED_ACTIONS_BY_STATE` was unchanged, the live pending-gates state
remained `STRICT_FRESH_COMPILED_ACTIONABLE_PENDING_GATES` with exactly `["HOLD", "NO_TRADE"]`, the
`STRICT_FRESH_COMPILED_ACTIONABLE_STEP2_DECISION_ONLY` state remained unimplemented at R2E.5b-6b, the
availability evaluator / Step 2/3/4 workflow / weekly behavior / order compiler / prompts / final execution
safety gate were untouched, and no consumer read the dry-run artifacts. **R2E.5b-6c was the designated first
true permission change and has since been implemented — see §33.** For the pending-gates posture this dry-run
diagnoses, the real Step 2 gate still blocks today; only a fully-verified upgrade to the decision-only state
(§33) opens the Step 2 decision-only mode.

The evaluator lives at:

```text
src/investment_orchestrator/research/promoted_step2_gate_dry_run.py
```

Public API (deterministic, fail-closed, never raises):

```python
evaluate_promoted_step2_gate_dry_run(
    *,
    research_decision: Mapping[str, Any] | None,
    promoted_verification: Mapping[str, Any] | None,
) -> Mapping[str, Any]
```

### 32.1 Step 1 report-only artifacts

Step 1 parse gained a defensive report-only layer 5 (`_write_promoted_step2_gate_dry_run_report_only`) that
runs after the availability artifacts are written. It loads the real active pointer / effective handoff /
effective validation, runs the R2E.5b-6a verifier, and writes:

```text
artifacts/current/step1_research/promoted_handoff_step2_verification.json
```

then feeds that verification plus the pre-upgrade degraded-mode decision into the dry-run evaluator and writes:

```text
artifacts/current/step1_research/promoted_step2_gate_dry_run.json
```

Any error in this layer is swallowed; Step 1 parse never fails because of it. At R2E.5b-6b neither artifact was
fed into the availability evaluator, Step 2, weekly, the order compiler, or any gate. (R2E.5b-6c later
restructured this into the two-pass availability flow of §33.2: the dry-run is evaluated against the in-memory
pre-upgrade decision, and the final availability evaluation consumes both artifacts as upgrade inputs.)

### 32.2 Dry-run artifact schema

Schema `promoted_step2_gate_dry_run_v1`. Top-level fields: `schema_version`, `is_llm_generated: false`,
`report_only: true`, `permission_effect: "none"`, `not_authorization: true`, `dry_run_only: true`,
`would_allow_step2_promoted_decision`, `current_real_gate_allows: false` (computed read-only with the existing
production gate evaluation and false unless that real gate already allows, which it does not),
`future_permission_required: "PROMOTED_RESEARCH_DECISION"`,
`future_state_required: "STRICT_FRESH_COMPILED_ACTIONABLE_STEP2_DECISION_ONLY"`, `current_state`,
`current_allowed_actions`, `verification_valid_for_step2_decision`, `dry_run_blockers[]`, `dry_run_warnings[]`,
`checks[]`, `consumed_by_step2: false`, and `consumed_by_gates: false`.

### 32.3 Dry-run criteria

`would_allow_step2_promoted_decision` is `true` only when every criterion passes:

- the degraded-mode decision exists and its state is `STRICT_FRESH_COMPILED_ACTIONABLE_PENDING_GATES`;
- the decision's `allowed_actions` are exactly `HOLD` / `NO_TRADE`;
- the R2E.5b-6a promoted verification exists, reports `valid_for_step2_decision: true`, and has no
  `verification_blockers`;
- verification `future_permission_required == PROMOTED_RESEARCH_DECISION`;
- verification report-only / not-authorization markers are intact (schema
  `promoted_handoff_step2_verification_v1`, `report_only: true`, `permission_effect: "none"`,
  `not_authorization: true`, `is_llm_generated: false`, pointer/effective status still pending gates:
  `promotion_status == "pending_gates"`, `pointer_permission_effect ==
  "none_until_consumed_by_future_gate_pr"`, `consumed_by_step2 == false`).

Even when all criteria pass, the policy blocker `real_gate_still_closed_by_policy` is recorded in
`dry_run_blockers`, so the artifact can never be read as an actual Step 2 render permission.

Dry-run blocker reason-code contract:

```text
decision_missing
decision_state_not_pending_gates
decision_actions_not_hold_no_trade
verification_missing
verification_invalid
verification_permission_mismatch
verification_markers_invalid
real_gate_still_closed_by_policy
```

### 32.4 No gate, permission, or workflow changes (in R2E.5b-6b)

At R2E.5b-6b the real Step 2 gate (`enforce_step2_research_gate`) was byte-for-byte unchanged and still blocked
promoted pending-gates research; the final execution safety gate still required literal `STRICT_FRESH` plus
`ORDER_COMPILATION`; `run_weekly` still terminated as controlled `NO_TRADE` with no Step 2/3/4 downstream
artifacts; Step 2 never rendered the promoted handoff. The first true permission change is **R2E.5b-6c**
(add `PROMOTED_RESEARCH_DECISION` + `STRICT_FRESH_COMPILED_ACTIONABLE_STEP2_DECISION_ONLY`, Step 2
decision-only), which landed as its own PR after this one — see §33.

## 33. R2E.5b-6c status (Step 2 promoted decision-only — FIRST TRUE PERMISSION CHANGE)

**R2E.5b-6c is the first true permission change in the R2E.5b series.** It permits exactly one new thing:
**Step 2 may render and parse a research decision from the promoted effective handoff** under the new
`PROMOTED_RESEARCH_DECISION` action. Nothing else opens:

- **No `NEW_BUY` permission is added.** **No `ORDER_COMPILATION` permission is added.**
- **Step 3 audit, Step 4 order compilation, and the order path stay blocked.** Step 3 deterministically blocks
  with `promoted_step2_decision_only_no_audit_permission`; Step 4 blocks on its upstream guard and on the
  **final execution safety gate, which is unchanged** and still requires literal `STRICT_FRESH` plus
  `ORDER_COMPILATION`.
- The full order-eligible `STRICT_FRESH_COMPILED_ACTIONABLE` state remains absent / non-enabled.
- Raw `STRICT_FRESH` behavior is unchanged (it does not gain `PROMOTED_RESEARCH_DECISION`).
- `run_weekly` never auto-runs Step 2 for the promoted state and never compiles orders: it terminates as a
  controlled `NO_TRADE_PENDING_FINAL_GATES` (reason `promoted_step2_decision_only_pending_final_gates`).
- The last-good writer is untouched: no promoted handoff is written to the existing last-good slot, no raw
  last-good is written from the promoted path, and no promoted last-good slot is created.

### 33.1 New state / action

New availability state and action (`state/research_availability.py`):

```text
STRICT_FRESH_COMPILED_ACTIONABLE_STEP2_DECISION_ONLY
PROMOTED_RESEARCH_DECISION
```

`_ALLOWED_ACTIONS_BY_STATE` maps the new state to exactly
`("HOLD", "NO_TRADE", "PROMOTED_RESEARCH_DECISION")`. Blocked actions are derived from the unchanged ACTIONS
baseline and are exactly `SELL`, `NEW_BUY`, `ROTATION`, `REBALANCE`, `EXTENDED_ETF_ADMISSION`, and
`ORDER_COMPILATION`. `PROMOTED_RESEARCH_DECISION` is deliberately NOT added to the ACTIONS baseline so every
other state's allowed/blocked artifact stays byte-identical; it appears in no other state's allowed_actions.
The decision-only artifacts carry `permission_effect: "promoted_step2_decision_only"`,
`promoted_step2_decision_only: true`, `order_compilation_allowed: false`, `new_buy_permission: false`,
`not_authorization: true` (the order path stays unauthorized), and blocker reasons
`promoted_step2_decision_only_enabled`, `new_buy_requires_future_gate_pr`,
`order_compilation_requires_future_gate_pr`, and `final_execution_requires_future_gate_pr`. `source_artifacts`
references `active_research_handoff_source`, `research_handoff_candidate_effective`,
`research_handoff_candidate_effective_validation`, `promoted_handoff_step2_verification`, and
`promoted_step2_gate_dry_run`. Permission artifacts remain deterministic (`is_llm_generated: false`).

### 33.2 Availability upgrade criteria (fail closed)

Step 1 evaluates availability in two passes: pass 1 (pre-upgrade posture) feeds the R2E.5b-6a verification and
R2E.5b-6b dry-run writers; pass 2 consumes this run's `promoted_handoff_step2_verification.json` /
`promoted_step2_gate_dry_run.json` objects. A run upgrades from
`STRICT_FRESH_COMPILED_ACTIONABLE_PENDING_GATES` to `STRICT_FRESH_COMPILED_ACTIONABLE_STEP2_DECISION_ONLY`
only when ALL criteria pass (`_step2_decision_only_upgrade_ok`):

- the state would otherwise be exactly the pending-gates state;
- dry-run present with schema `promoted_step2_gate_dry_run_v1`, `would_allow_step2_promoted_decision: true`,
  `current_real_gate_allows: false`, `future_permission_required: "PROMOTED_RESEARCH_DECISION"`,
  `future_state_required: "STRICT_FRESH_COMPILED_ACTIONABLE_STEP2_DECISION_ONLY"`,
  `permission_effect: "none"`, `not_authorization: true`, `dry_run_only: true`, `report_only: true`, and
  `real_gate_still_closed_by_policy` present in `dry_run_blockers`;
- verification present with schema `promoted_handoff_step2_verification_v1`,
  `valid_for_step2_decision: true`, empty `verification_blockers`, intact report-only / not-authorization
  markers, `promotion_status: "pending_gates"`, `consumed_by_step2: false`, `promotion_expires_at` not stale
  as of `now_date`, and `effective_handoff_sha256` / `pointer_effective_handoff_sha256` both equal to the
  recomputed sha256 of the promoted effective handoff (hash re-check).

Fail closed: a missing / malformed / false dry-run, a missing / invalid / stale verification, or a hash
mismatch keeps the run at pending-gates HOLD / NO_TRADE. Raw `STRICT_FRESH` is never upgraded or altered.

### 33.3 Step 2 gate: two disjoint paths

`evaluate_step2_research_gate` recognizes two disjoint allowed paths and reports a `mode`:

- **Legacy path (unchanged):** literal `STRICT_FRESH` + `NEW_BUY` + `ORDER_COMPILATION` ⇒
  `mode: "strict_fresh_actionable"` with `order_compilation_allowed / new_buy_permission / step3_allowed /
  step4_allowed` all true. The legacy conditions are not loosened.
- **Promoted decision-only path (new):** state `STRICT_FRESH_COMPILED_ACTIONABLE_STEP2_DECISION_ONLY`,
  `PROMOTED_RESEARCH_DECISION` allowed, NEITHER `NEW_BUY` nor `ORDER_COMPILATION` allowed (a widened artifact
  is refused), `source: "promoted_compiled_actionable_handoff"`, `promoted_step2_decision_only: true`, and no
  manual review ⇒ `allowed: true`, `mode: "promoted_step2_decision_only"`,
  `order_compilation_allowed: false`, `new_buy_permission: false`, `step3_allowed: false`,
  `step4_allowed: false`, `recommended_terminal_result_after_step2: "NO_TRADE_PENDING_FINAL_GATES"`.

Every other state — including the pending-gates state — still fails closed exactly as before.

### 33.4 Step 2 promoted handoff source

On the promoted path, Step 2 renders from `research_handoff_candidate_effective.json` — never from the raw
Deep Research `research_output.json` and never from the active non-actionable compiled candidate. Rendering
requires `active_research_handoff_source.json`, `research_handoff_candidate_effective.json`, and
`research_handoff_candidate_effective_validation.json`, and re-runs
`verify_promoted_handoff_for_step2_decision` live at render time (pointer markers, hashes, actionable-ticker
consistency, and `promotion_expires_at` are re-checked *now*). A failed verification writes the Step 2 blocked
artifact with reason `promoted_step2_verification_failed` and renders nothing. The rendered prompt appends a
deterministic source-metadata block: `source: promoted_compiled_actionable_handoff`, `promotion_status`, the
active pointer sha256, the effective handoff sha256, `promotion_expires_at`, the actionable tickers, and
explicit notes that the run is Step 2 decision-only, NOT order authorization, and that `ORDER_COMPILATION` and
`NEW_BUY` are NOT allowed in this state. Render and parse both write the deterministic marker artifact
`step2_promoted_decision_only.json` (schema `step2_promoted_decision_only_v1`, `is_llm_generated: false`) with
`decision_only: true`, `order_compilation_allowed: false`, `new_buy_permission: false`, `step3_allowed: false`,
`step4_allowed: false`, `not_execution_authorization: true`, and
`recommended_terminal_result_after_step2: "NO_TRADE_PENDING_FINAL_GATES"`. The LLM decision packet itself is
never mutated by deterministic code.

### 33.5 Step 3 / Step 4 blocking

`enforce_step3_upstream_guard` first evaluates the research gate: when the mode is
`promoted_step2_decision_only`, Step 3 writes the deterministic block artifact
`step3_blocked_by_promoted_decision_only_gate.json` with reason
`promoted_step2_decision_only_no_audit_permission` and raises — regardless of which Step 2 artifacts exist.
Step 4's upstream guard additionally watches that block artifact, and the **final execution safety gate is
unchanged**: it still requires literal `STRICT_FRESH` and `ORDER_COMPILATION` in `allowed_actions`, so the
decision-only state fails both checks. Opening Step 3 audit requires a future explicit PR (**R2E.5b-6d**);
opening `NEW_BUY` / `ORDER_COMPILATION` / the final gate / any order path requires **R2E.5b-7**.

### 33.6 Weekly behavior

`run_weekly` routes the promoted decision-only mode to a controlled terminal WITHOUT auto-running Step 2:
`actionable: false`, `weekly_completed: true`, `terminal_result: "NO_TRADE_PENDING_FINAL_GATES"`, reason
`promoted_step2_decision_only_pending_final_gates`, exit 0, `order_compilation_allowed: false`,
`new_buy_permission: false`, and no Step 2/3/4 or order artifacts. The manual `run_step2 render` / `parse`
commands are the only enabled decision-only flow. `run_summary.json` reports the decision-only state with
`run_blocked: true` and `recommended_result: "NO_TRADE"` (severity remains benign).

### 33.7 Future PRs

- **R2E.5b-6d** (optional, future): audit-only PR — Step 3 may audit decision-only packets; Step 4 and the
  final execution safety gate stay closed.
- **R2E.5b-7** (future): the order path — `NEW_BUY`, `ORDER_COMPILATION`, final-gate opening for promoted
  states, promoted-ticker subset checks, deterministic budget checks, and any promoted last-good slot. Until
  it lands, every promoted run must end in `NO_TRADE` / `NO_TRADE_PENDING_FINAL_GATES` with zero orders.
