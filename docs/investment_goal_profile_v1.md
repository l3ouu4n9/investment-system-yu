# Investment Goal Profile v1

## A. Document status

| Field | Value |
|---|---|
| Version | `v1` |
| Status | `DRAFT_OPERATOR_REVIEW` |
| Runtime consumption | `NONE` |
| Behavior effect | `NONE` |
| Permission effect | `NONE` |
| Phase 2C effect | `NONE` |
| Approval owner | Operator |
| Last reviewed date | `OPERATOR_TO_COMPLETE: YYYY-MM-DD` |

### Classification vocabulary

| Classification | Meaning |
|---|---|
| `OPERATOR_CONFIRMED` | Explicitly completed and approved by the operator in this profile. No v1 field has this status yet. |
| `REPOSITORY_EXPLICIT` | Present in repository prompts, settings, runbooks, schemas, or code; not automatically a personal financial goal. |
| `INFERRED_NOT_CONFIRMED` | Possible interpretation of repository evidence; never a goal without operator confirmation. |
| `UNRESOLVED` | Requires operator input before a related design or behavior change can be authorized. |
| `NOT_APPLICABLE` | Does not apply to this field. |
| `OUT_OF_SCOPE` | Requires a separate future design review. |

## B. Core safety statement

This is an **operator-review worksheet only**. It is not consumed by runtime
code and has no parser, schema validator, CLI flag, workflow stage, or
configuration consumer. It is not an authorization source.

It does not authorize trading, permissions, budgets, caps, allocations, ETF
universe changes, orders, broker actions, or execution. It does not amend
`strategy_settings.yaml` or any prompt. Unresolved entries fail closed at the
design level by preventing related behavior changes from being authorized here.
Repository behavior remains unchanged until a separate approved design and
implementation review adopts a specific change.

This worksheet does not begin Phase 2C and does not decide evidence
sufficiency, retirement readiness, fallback disposition, or legacy deletion.

## C. Existing strategy mandate

These are **repository-explicit strategy statements**, not operator-confirmed
personal financial facts.

| Existing statement | Status | Evidence | Current deterministic control | Other status |
|---|---|---|---|---|
| QQQ/Nasdaq-100 is the benchmark anchor; orientation is benchmark-relative alpha over 3–5 years. | `REPOSITORY_EXPLICIT` | [Strategy A prompt](../prompts/strategy_a_decision_builder.txt); [settings](../inputs/current/strategy_settings.yaml) | Static benchmark/core universe settings; no return target or minimum QQQ weight. | Economic objective is prompt/operator judgment. |
| Strategy may accept higher active risk, sector/theme concentration, tracking error, and episodic drawdown than QQQ. | `REPOSITORY_EXPLICIT` | [Strategy A prompt](../prompts/strategy_a_decision_builder.txt) | No numeric drawdown, volatility, or tracking-error control. | Prompt-only qualitative mandate. |
| Long-biased ETF strategy with technology/productivity structural-growth competence, but not technology-only. | `REPOSITORY_EXPLICIT` | [Strategy A prompt](../prompts/strategy_a_decision_builder.txt) | Static universe and Step 4 buy-side checks provide partial boundaries. | Theme and alpha judgment are advisory/prompt-driven. |
| QQQ/core is an anchor; extended sector/thematic/factor ETFs may be a material but capped sleeve. | `REPOSITORY_EXPLICIT` | [Strategy A prompt](../prompts/strategy_a_decision_builder.txt); [ETF policy](../inputs/current/strategy_settings.yaml) | Static extended list and some order checks; no NAV-based sleeve enforcement. | Activation and incremental-exposure decisions are prompt/operator judgment. |
| Leveraged, inverse, low-liquidity, over-frequent, short-event, and no-12-month-thesis activity are not intended as primary behavior. | `REPOSITORY_EXPLICIT` | [Strategy A prompt](../prompts/strategy_a_decision_builder.txt) | Partial static-universe/order-shape controls only. | Product/liquidity/thesis assessment is largely prompt-only. |
| Tax intent is `LTCG_only`; holding preference is `>=12m`. | `REPOSITORY_EXPLICIT` | [settings](../inputs/current/strategy_settings.yaml) | Settings enter the workflow; sell-side validation is incomplete. | Tax suitability and lot review remain operator discretion. |
| Same-role, score-gap rotation guardrails; weekly strategy and daily execution-only cadence. | `REPOSITORY_EXPLICIT` | [settings](../inputs/current/strategy_settings.yaml); [Daily Runbook](daily_execution_check_runbook.md) | Daily validation restricts ticker, budget, thesis, ranking, and role changes. | Role validity/scoring remain research judgment. |
| Stale, invalid, or blocked research leads to `NO_TRADE`; missed trades are preferable to bad-data trades. | `REPOSITORY_EXPLICIT` | [Weekly Runbook](weekly_run_operator_runbook.md) | Availability, upstream, final-safety, and order-output gates fail closed. | Personal opportunity-cost preference is unconfirmed. |
| Orders are manual-review artifacts; broker/live execution is absent. | `REPOSITORY_EXPLICIT` | [Weekly Runbook](weekly_run_operator_runbook.md) | No broker client or submission path; deterministic gates control progression. | Manual entry/review remains operator responsibility. |

### Inferred, not confirmed

| Possible interpretation | Status | Evidence | Operator confirmation required |
|---|---|---|---|
| Growth/accumulation rather than income orientation. | `INFERRED_NOT_CONFIRMED` | Long-biased ETF / 12m+ thesis language. | Yes |
| USD is operational funding currency. | `INFERRED_NOT_CONFIRMED` | Current budgets are denominated in USD. | Yes; this is not a currency policy. |
| Cash headroom is tactical rather than strategic allocation. | `INFERRED_NOT_CONFIRMED` | Headroom and underdeployment language. | Yes |

## D. Operator financial profile

Every unfilled field is `UNRESOLVED` and cannot authorize a related code or
policy change.

| Field | Status | Operator-confirmed value / notes | Existing evidence |
|---|---|---|---|
| Total investment horizon | `UNRESOLVED` | `OPERATOR_TO_COMPLETE` | 3–5 years is a strategy window, not a confirmed total horizon. |
| Required return objective | `UNRESOLVED` | `OPERATOR_TO_COMPLETE` | No absolute-return objective. |
| Benchmark-relative objective | `UNRESOLVED` | `OPERATOR_TO_COMPLETE` | QQQ-relative alpha is repository-explicit only. |
| Maximum acceptable drawdown | `UNRESOLVED` | `OPERATOR_TO_COMPLETE` | Qualitative episodic-drawdown language only. |
| Maximum recovery period | `UNRESOLVED` | `OPERATOR_TO_COMPLETE` | No value. |
| Volatility tolerance | `UNRESOLVED` | `OPERATOR_TO_COMPLETE` | Qualitative higher-than-QQQ risk only. |
| Tracking-error tolerance | `UNRESOLVED` | `OPERATOR_TO_COMPLETE` | Qualitative higher-than-QQQ tracking error only. |
| Emergency reserve / near-term liquidity | `UNRESOLVED` | `OPERATOR_TO_COMPLETE` | No policy. |
| Expected contributions / withdrawals | `UNRESOLVED` | `OPERATOR_TO_COMPLETE` | No policy. |
| Known liabilities / retirement-income requirements | `UNRESOLVED` | `OPERATOR_TO_COMPLETE` | No policy. |
| Capital-preservation floor | `UNRESOLVED` | `OPERATOR_TO_COMPLETE` | No policy. |
| Tax jurisdiction / account types | `UNRESOLVED` | `OPERATOR_TO_COMPLETE` | `LTCG_only` is not jurisdiction or account policy. |
| Realization budget / wash-sale constraints | `UNRESOLVED` | `OPERATOR_TO_COMPLETE` | No complete policy. |

## E. Strategic allocation policy

No numeric allocation value is proposed here.

| Field | Status | Operator-review value | Existing evidence |
|---|---|---|---|
| Target equity / bond / cash ranges | `UNRESOLVED` | `OPERATOR_TO_COMPLETE` | Current universe is primarily equity; no strategic ranges. |
| Minimum QQQ/core exposure | `UNRESOLVED` | `OPERATOR_TO_COMPLETE` | Anchor concept; no numeric minimum. |
| Maximum single ETF / sector / theme / issuer exposure | `UNRESOLVED` | `OPERATOR_TO_COMPLETE` | Some open-order caps; no portfolio-NAV limits. |
| Maximum overlapping underlying exposure | `UNRESOLVED` | `OPERATOR_TO_COMPLETE` | Same-role overlap review; no holdings look-through limit. |
| Geographic allocation / currency exposure / hedging | `UNRESOLVED` | `OPERATOR_TO_COMPLETE` | No explicit policy. |
| Defensive allocation policy | `UNRESOLVED` | `OPERATOR_TO_COMPLETE` | No policy. |

## F. ETF product policy

| Field | Status | Operator-review value | Current repository rule classification |
|---|---|---|---|
| Allowed product types | `UNRESOLVED` | `OPERATOR_TO_COMPLETE` | `ABSENT` as a complete policy; current universe is ETF-oriented. |
| Leveraged/inverse prohibition | `REPOSITORY_EXPLICIT` | `OPERATOR_TO_CONFIRM_OR_REPLACE` | `PROMPT_ONLY` qualitative prohibition. |
| Derivative-heavy tolerance | `UNRESOLVED` | `OPERATOR_TO_COMPLETE` | `ABSENT`. |
| Minimum AUM / maximum spread / minimum average daily volume | `UNRESOLVED` | `OPERATOR_TO_COMPLETE` | Qualitative AUM/spread language; no numeric threshold. |
| Maximum liquidation time | `UNRESOLVED` | `OPERATOR_TO_COMPLETE` | `ABSENT`. |
| Maximum expense ratio / tracking-difference tolerance | `UNRESOLVED` | `OPERATOR_TO_COMPLETE` | `ABSENT`. |
| Issuer/product concentration / niche-thematic tolerance | `UNRESOLVED` | `OPERATOR_TO_COMPLETE` | Extended sleeve is explicit; portfolio-level product policy is absent. |

## G. Behavioral preferences

| Field | Status | Operator-review value | Existing evidence |
|---|---|---|---|
| `NO_TRADE` versus opportunity cost | `UNRESOLVED` | `OPERATOR_TO_COMPLETE` | Fail-closed `NO_TRADE` is repository-explicit; personal tradeoff is not. |
| Acceptable underdeployment duration | `UNRESOLVED` | `OPERATOR_TO_COMPLETE` | Prompts discourage chronic underdeployment; no duration. |
| Acceptable turnover / taxable realization | `UNRESOLVED` | `OPERATOR_TO_COMPLETE` | Guardrails and `LTCG_only`; no numeric portfolio policy. |
| Rebalance frequency / sell discipline | `UNRESOLVED` | `OPERATOR_TO_COMPLETE` | Weekly/daily cadence; incomplete sell-side validation. |
| Tolerance for missed entries / manual-review burden | `UNRESOLVED` | `OPERATOR_TO_COMPLETE` | Manual Step 1→4 and manual broker entry are explicit. |
| Definition of “new ticker” | `UNRESOLVED` | Choose one: `NEW_PORTFOLIO_HOLDING` / `NEW_ORDER_IN_CURRENT_RUN` | Current validator terminology requires clarification. |

## H. Goal-to-control traceability

Every row in this v1 worksheet has `code_change_authorized: NO`.

`code_change_authorized: NO` means this profile does not authorize a
goal-driven investment-policy or behavior change. It does not prohibit a
separately scoped fail-closed correctness repair that restores an already
documented contract without changing investment policy, permissions,
thresholds, caps, budgets, universe membership, or order authority. Every
such repair still requires its own design, implementation, tests, and
independent safety review.

| Goal or field | Status | Evidence | Current deterministic control | Advisory / report-only support | Missing control | Risk if unresolved | Code change authorized |
|---|---|---|---|---|---|---|---|
| QQQ-relative 3–5 year objective | `REPOSITORY_EXPLICIT` | Strategy A prompt | Static benchmark/core settings | Research/decision/audit prompts | Numeric objective and attribution | Undefined personal objective | `NO` |
| Higher active risk than QQQ | `REPOSITORY_EXPLICIT` | Strategy A prompt | No portfolio drawdown/volatility control | Risk narrative | Numeric risk/recovery limits | Loss tolerance mismatch | `NO` |
| QQQ/core anchor | `REPOSITORY_EXPLICIT` | Prompt/settings | Universe/order checks | Underdeployment diagnostics | Minimum portfolio weight | Nominal-only anchor | `NO` |
| 12m+/LTCG/low turnover | `REPOSITORY_EXPLICIT` | Settings/prompt | Partial rotation/order controls | Research/operator review | Sell-lot/tax validation | Tax/holding mismatch | `NO` |
| Extended sleeve | `REPOSITORY_EXPLICIT` | Settings/prompt | Static lists/some order limits | Activation/cap narrative | NAV-based concentration policy | Theme concentration mismatch | `NO` |
| Liquidity/nonlevered/noninverse | `REPOSITORY_EXPLICIT` | Prompt | Partial static boundary | Research scorecards | Numeric product criteria | Ineligible product proposal | `NO` |
| `NO_TRADE` safety posture | `REPOSITORY_EXPLICIT` | Runbook | Fail-closed gates | Run summaries | Operator opportunity-cost preference | Over- or under-deployment | `NO` |
| Cash, liabilities, withdrawals | `UNRESOLVED` | None | None | None | Financial profile | Forced sale/overcommitment | `NO` |
| Allocation, geography, currency | `UNRESOLVED` | None | Static ETF lists only | Role descriptions | Target ranges/look-through | Unintended concentration | `NO` |
| Manual review/no broker automation | `REPOSITORY_EXPLICIT` | Runbook | Gate/quarantine/no broker path | Operator checklist | Manual burden tolerance | Entry/state error | `NO` |
| New-ticker semantics | `UNRESOLVED` | Current terminology | Per-run ticker counting | None | Operator definition | Under/over restriction | `NO` |

## I. Known correctness findings

These are safety debt from prior review. They are not implementation
authorization and this document does not prescribe a fix.

| Finding | Failure classification | Current risk | Current blocked behavior | Goal input required? | Future review class | Implementation authorized |
|---|---|---|---|---|---|---|
| Blank `order_intent` handling | `compiler/normalizer` | Inconsistent submit versus net-new treatment. | Manual review remains required; no broker submission path. | No | Fail-closed order-validation review | `NO` |
| Existing open-order reconciliation | `gate/final-safety` | Intended exposure can be incomplete when snapshot orders are omitted from plans. | Existing checks/manual review remain. | No | Order-exposure reconciliation review | `NO` |
| Effective universe intersection | `validator/schema` | Per-run universe may not be fully constrained by static settings in every path. | Some static new-ticker checks remain. | No | Universe-authority review | `NO` |
| Artifact same-run lineage | `workflow/orchestrator` | Presence is not complete proof of one current render/run. | Missing/blocked artifacts fail closed. | No | Workflow-lineage review | `NO` |
| Independent freshness clock | `availability/permission` | Operator-provided date affects age classification. | Known stale/degraded states still block. | No | Freshness-source review | `NO` |
| `audit_fail_reasons` / `compiler_blockers` | `gate/final-safety` | Some explicit failure fields are not general final-gate blockers. | Existing final-gate blocker set remains. | No | Final-gate consistency review | `NO` |
| Nonempty unvalidated `SELL_ORDERS` readiness | `validator/schema`, `gate/final-safety` | Unvalidated sell output could approach order-readiness or canonical-order boundaries without complete deterministic validation. | Nonempty unvalidated sells must remain blocked from readiness, canonical publication, manual-order use, and any downstream order path. | No | Fail-closed sell-readiness review | `NO` |
| Sell lot, tax, and reduction policy | `design-goal ambiguity`, `validator/schema` | No approved lot eligibility, tax realization, wash-sale, reduction, or sell-discipline policy. | No new actionable sell policy is authorized. | Yes | Sell-policy and tax design review | `NO` |

## J. Operator approval checklist

Before any investment-policy-changing strategy, settings, prompt, threshold,
cap, budget, universe, allocation, permission, or order-path change, the
operator must explicitly review the applicable profile fields below.

Fail-closed correctness repairs that restore an already documented contract
may proceed only through a separate scoped design, implementation, test, and
independent safety review. Such repairs must not introduce a new investment
policy, loosen an existing control, expand permissions, or create order or
execution authority.

- [ ] Financial profile, horizon, return objective, and capital-preservation floor
- [ ] Drawdown, recovery, volatility, and tracking-error tolerance
- [ ] Emergency reserve, liquidity, contributions, withdrawals, and liabilities
- [ ] Strategic equity/bond/cash allocation
- [ ] QQQ/core, position, sector, theme, issuer, and overlap concentration
- [ ] Geographic, currency, hedging, and defensive-allocation policy
- [ ] ETF product policy, liquidity, product structure, cost, and tracking criteria
- [ ] Tax jurisdiction, accounts, realization budget, and wash-sale constraints
- [ ] `NO_TRADE` versus opportunity-cost preference and underdeployment duration
- [ ] Definition of “new ticker”
- [ ] Manual-review burden and manual execution boundary

Allowed document approval states:

- `DRAFT_OPERATOR_REVIEW`
- `APPROVED_DOCUMENT_ONLY`
- `REJECTED`
- `SUPERSEDED`

There is intentionally no runtime-enabled approval state.
`APPROVED_DOCUMENT_ONLY` confirms only document review; it does not activate
runtime configuration or authorize a behavior change.
