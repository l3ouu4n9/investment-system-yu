# V1 Buy-Only Policy Contract v1

## 1. Document status and authority

| Field | Value |
|---|---|
| Contract version | `v1` |
| Status | `APPROVED_POLICY_FOR_FUTURE_V1_IMPLEMENTATION` |
| Change class | `DESIGN_ONLY_POLICY_CONTRACT` |
| Runtime consumption | `NONE` |
| Current behavior effect | `NONE` |
| Current permission effect | `NONE` |
| Current state effect | `NONE` |
| Current gate effect | `NONE` |
| Current publication effect | `NONE` |
| Current order-path effect | `NONE` |
| Broker/live execution authority | `ABSENT_AND_UNAUTHORIZED` |

This document records operator-approved policy for the future first complete
V1 vertical slice. It does not assert that the V1 proposal, permission,
compiler, final-safety, publication, or order paths are currently implemented
or reachable.

Current committed runtime behavior remains unchanged. In particular, the
current H1 state remains non-actionable, H1 `NEW_BUY` and
`ORDER_COMPILATION` remain blocked, the future V1 state is absent, and the
future V1 publication and review-only order paths are absent.

Later implementation PRs must treat this document as policy input, preserve
the stated authority boundaries, and independently prove every authority
change. This document is not itself a runtime configuration source, state
record, permission record, gate result, freshness token, publication pointer,
or order artifact.

## 2. Policy classification

### A. Immutable and previously closed policy

The following policy was closed before this contract:

- V1 is BUY-only and increment-only.
- Only currently positively held base-universe ETFs can receive a new V1 BUY
  increment.
- Existing valid unfilled BUY orders are retained unchanged.
- `X` is the sole human-supplied ceiling on final total unfilled BUY
  commitment.
- Partial allocation and unused capacity are valid.
- Exact Decimal arithmetic and whole shares are required.
- HOLD and NO_TRADE are successful terminal outcomes with no order artifact.
- LLM output is qualitative and non-authoritative.
- Valuation acquisition and the LLM handoff remain explicit operator steps.
- Any eventual order artifact is review-only and is entered manually by a
  human.
- SELL and all broker/live execution are outside the V1 BUY-only path.

### B. Newly operator-approved V1 choices

The following choices are closed by this contract:

- Final `Y`, including retained and new BUY commitment, consumes the aggregate
  `R = r * H` increment-risk cap.
- `Z` is final deterministically classified CORE exposure.
- `A` is final deterministically classified non-core alpha exposure, including
  SATELLITE and already-existing APPROVED_EXTENDED exposure.
- Final exposure must satisfy `A <= Z` because `k = 1`.
- An initial `A > Z` condition produces NO_TRADE and no new BUY.
- Ticker evidence coverage requires a current cited LH2 entry whose
  deterministic ticker membership contains that ticker.
- PREFERRED and STANDARD membership and the six-ticker priority order are
  fixed.
- Allocation selects only the first eligible ticker and performs one pass.
- The validated current mark is the shared exposure and BUY-price anchor.

### C. Intentionally deferred post-V1 policy

The following are not part of this contract:

- SELL policy or SELL activation for the H1 V1 path.
- New ticker admission.
- Extended ETF admission or a new extended ETF order.
- Rotation or rebalance.
- Cancel, replace, or reanchor.
- Fractional shares.
- Iterative allocation, residual redistribution, optimization, or minimum
  spend.
- Additional LLM stages, scoring, ranking, or sentiment extraction.
- Broker integration, automatic placement, automatic execution, polling,
  retries, or scheduling.
- Longitudinal thesis observers, dashboards, or additional research UX.

These subjects require separate post-V1 design and authorization. They do not
alter this contract by implication.

## 3. V1 objective and terminal outcomes

The future complete V1 vertical slice is:

```text
current deterministic/local inputs
-> Step1
-> validated/current H1 qualitative context
-> deterministic disposition
-> deterministic BUY allocation
-> deterministic whole-share compilation
-> postcompile final safety
-> immutable review package
-> optional review-only BUY order artifact
-> HUMAN manual broker entry
```

The successful terminal outcomes are:

- BUY package: an immutable review package plus review-only NEW_ORDER rows.
- HOLD package: an immutable no-order review package.
- NO_TRADE package: an immutable fail-closed no-order review package.

The future publication contract must not fabricate an order for HOLD or
NO_TRADE.

## 4. Scope and universe

The V1 base universe is exactly:

| V1 role | Tickers |
|---|---|
| CORE | `QQQ`, `VOO`, `VTI`, `VT` |
| SATELLITE | `SMH`, `IGV` |

`QQQ` remains the benchmark/core anchor.

A new V1 BUY requires the ticker to be a currently positively held member of
this base universe. V1 does not authorize a new position in an unheld ticker.

V1 does not authorize:

- SELL;
- new ticker admission;
- extended ETF admission;
- rotation;
- rebalance;
- cancel;
- replace;
- reanchor;
- fractional shares;
- broker automation; or
- automatic execution.

All valid current unfilled BUY commitments are retained unchanged. V1 creates
only `NEW_ORDER` output. It never assumes a broker-side cancellation.

## 5. Glossary

All monetary values use exact Decimal or canonical-decimal arithmetic in USD.
For any ticker `i`:

| Symbol | Exact definition |
|---|---|
| `X` | Sole human-supplied ceiling on final total unfilled BUY commitment after reconciliation. |
| `H_i` | Exact current holdings exposure for ticker `i`, using current shares and the validated current mark. |
| `H` | Complete exact current strict-holdings exposure: `H = sum(H_i)`. |
| `E_i` | Retained existing valid unfilled BUY commitment for ticker `i`. |
| `E` | Total retained existing valid unfilled BUY commitment: `E = sum(E_i)`. |
| `N_i` | Actual newly compiled positive V1 BUY notional for ticker `i`. |
| `Y_i` | Final per-ticker unfilled BUY commitment: `Y_i = E_i + N_i`. |
| `Y` | Final total unfilled BUY commitment: `Y = sum(Y_i)`. |
| `D_i` | Final projected ticker exposure: `D_i = H_i + Y_i`. |
| `A` | Final deterministically classified non-core alpha exposure. |
| `Z` | Final deterministically classified CORE exposure. |
| `r` | Existing validated operator-controlled increment fraction. |
| `R` | Aggregate increment-risk cap: `R = r * H`. |
| `C` | New allocatable capacity after retained commitments: `C = min(X, R) - E`. |
| `T_i` | Precompile target commitment assigned to the single selected ticker. |

For a ticker with a retained open BUY but no current holding, `H_i = 0` for
the arithmetic above. Such a ticker cannot receive a new V1 increment because
it is not currently positively held. Its retained `E_i` still contributes to
`Y_i`, `Y`, `D_i`, and every applicable cap.

Allocation targets are not final commitment. Only retained `E_i` and actual
positive compiled `N_i` enter final `Y_i` and `Y`.

## 6. X and final commitment

`X` is the sole human-supplied ceiling on final total unfilled BUY commitment.
The operator owns selecting a safe X.

Required X contract:

- `X >= 0`.
- Currency is USD.
- Representation and arithmetic are exact Decimal/canonical decimal.
- No separate cash or buying-power prerequisite exists in V1.

Final commitment must satisfy:

```text
Y_i = E_i + N_i
Y = sum(Y_i)
0 <= Y <= X
```

Legacy `hard_cap_open_orders_budget` and
`target_new_buy_budget_this_run` fields remain legacy-path inputs. The future
V1 path must not use them to supplement, replace, subdivide, or further
constrain X. V1 has no third global budget.

`Y` is not required to equal X. Unused X is valid. V1 has no target minimum
spend and no force-to-budget or spend-all-X objective.

## 7. Existing BUY commitments and identity binding

Every valid current unfilled BUY commitment is:

- retained;
- unchanged;
- represented by `E_i`;
- included in `E`; and
- included in final `Y_i` and `Y`.

The future proposal and final-safety paths must bind the exact current
portfolio/open-order source identity. Final safety must revalidate that exact
identity before publication.

If the current portfolio/open-order identity differs from the identity bound
to the proposal being validated, the terminal result is NO_TRADE. The system
must not silently recompute or continue under the old proposal. An operator
can start a new full run using refreshed local inputs.

Without broker connectivity, the repository cannot detect an unrecorded
broker-side change. The operator is responsible for refreshing the local
portfolio/open-order source before a run.

## 8. Aggregate increment-risk cap

The existing validated operator-controlled `r` source remains the sole owner
of the increment fraction. V1 defines:

```text
H = sum(H_i)
R = r * H
```

`H` is the complete exact strict-holdings exposure owned by the existing
holdings-exposure contract. It is not restricted to the six V1 base tickers.

`R` is one aggregate increment-risk cap. V1 does not create per-ticker `rH`
caps.

Retained and new BUY commitments both consume R:

```text
Y = sum(E_i + N_i)
Y <= R
Y <= X
```

R is a deterministic risk cap, not another human budget. It does not create a
third global budget.

Terminal rules are exact:

- If `E > X`, the result is NO_TRADE.
- If `E > R`, the result is NO_TRADE.
- If `r = 0` and every other required contract is valid, the result is HOLD.

## 9. A/Z exposure contract

Use final projected `D_i` values:

```text
D_i = H_i + Y_i
Z = sum(D_i for every deterministically classified CORE exposure)
A = sum(D_i for every deterministically classified non-core alpha exposure)
k = 1
A <= k * Z
A <= Z
```

At minimum, A contains:

- all SATELLITE exposure; and
- all already-existing deterministic APPROVED_EXTENDED exposure represented
  by current holdings or retained open BUY commitments.

V1 cannot create a new APPROVED_EXTENDED order. Existing extended exposure
still counts in A and cannot escape the cap.

Membership in A or Z is determined only by validated deterministic universe
and role facts. LLM text cannot classify exposure. An exposure required for
the calculation that lacks deterministic classification makes the run
UNRESOLVED and the terminal result NO_TRADE.

### 9.1 Initial A/Z evaluation

Before a new target is assigned, calculate:

```text
D_i_initial = H_i + E_i
Z_initial = sum(D_i_initial for CORE exposure)
A_initial = sum(D_i_initial for non-core alpha exposure)
```

If `A_initial > Z_initial`:

- V1 creates no new BUY;
- the terminal result is NO_TRADE;
- no SELL is inferred; and
- no monotonic-improvement exception applies.

When initially compliant:

- a CORE BUY cannot worsen `A <= Z`;
- a SATELLITE target is capped by available A/Z headroom;
- `A <= Z` is checked after target proposal; and
- `A <= Z` is checked again using actual compiled notionals.

Postcompile final safety independently requires final `A <= Z`.

## 10. Deterministic disposition

Dispositions apply to current positive holdings. Global authority-bearing
contract failures are classified as NO_TRADE before an actionable allocation
is produced.

### 10.1 EXCLUDE

EXCLUDE means deterministic universe or product disqualification for increase
purposes. EXCLUDE grants no SELL authority and does not imply liquidation.

### 10.2 UNRESOLVED

UNRESOLVED means a required ticker-level deterministic hard-constraint fact
does not have an unambiguous valid pass/fail result.

Any required UNRESOLVED candidate or context that prevents safe allocation
produces terminal NO_TRADE.

### 10.3 MAINTAIN_ONLY

MAINTAIN_ONLY means the ticker is a valid positively held V1 base ticker but:

- the deterministic basis for increasing is insufficient; or
- an increase-only deterministic constraint fails.

Missing ticker-specific evidence coverage in an otherwise valid current
context produces MAINTAIN_ONLY, not EXCLUDE.

### 10.4 INCREMENT_ELIGIBLE

INCREMENT_ELIGIBLE requires every condition below:

- the ticker is currently positively held;
- the ticker belongs to the V1 base universe;
- the H1 context has been independently revalidated as current;
- deterministic ticker-specific evidence coverage is sufficient;
- deterministic role classification is unambiguous;
- positive shared allocation capacity exists;
- applicable A/Z headroom exists; and
- every other deterministic hard constraint is satisfied.

LLM bullish, bearish, confident, uncertain, optimistic, or pessimistic prose
cannot change this mapping.

## 11. H1 evidence coverage

Ticker `i` has sufficient H1 evidence coverage only when at least one evidence
entry satisfies every condition below:

1. Its `source_entry_identity_sha256` appears in the validated H1 qualitative
   report's `evidence_references`.
2. That identity belongs to the independently reconstructed current LH2
   payload and context.
3. The report, render, raw-response, and current-context provenance binding is
   valid.
4. The entry's deterministic `tickers` membership contains ticker `i`.
5. The entry is current under the existing LH2 temporal policy.

The future V1 workflow must not:

- search prose for ticker names;
- infer coverage from qualitative wording;
- infer coverage from report existence; or
- consume `h1_qualitative_currentness_observation.json` as a transferable
  currentness, freshness, permission, or readiness token.

Every downstream authority-bearing V1 invocation must independently
reconstruct and re-evaluate the equivalent current deterministic conditions.

## 12. PREFERRED, STANDARD, and exact priority

PREFERRED and STANDARD labels apply only after INCREMENT_ELIGIBLE has been
established.

PREFERRED is exactly:

- `QQQ`
- `VOO`
- `VTI`
- `VT`

STANDARD is exactly:

- `SMH`
- `IGV`

The exact V1 deterministic priority order is:

1. `QQQ`
2. `VOO`
3. `VTI`
4. `VT`
5. `SMH`
6. `IGV`

This order is an explicit policy constant. It is not derived from LLM output,
source order, portfolio row order, JSON order, alphabetical sorting, or market
performance.

## 13. One-pass allocation algorithm

The allocation algorithm is exact:

```text
E = sum(E_i)
R = r * H
M = min(X, R)
```

Apply these preconditions in order:

```text
if E > X:
    NO_TRADE

if E > R:
    NO_TRADE

if A_initial > Z_initial:
    NO_TRADE
```

Then calculate:

```text
C = M - E
```

If `C == 0`, the result is HOLD.

Build the INCREMENT_ELIGIBLE ticker list using the exact priority in section
12. If the list is empty, the result is HOLD.

Select only the first eligible ticker. V1 does not allocate to a second ticker
in the same run.

For the selected CORE ticker:

```text
T_i = C
```

For the selected SATELLITE ticker:

```text
T_i = min(C, Z_initial - A_initial)
```

If `T_i <= 0`, the result is HOLD. Every other new target allocation is zero.

The algorithm performs exactly one allocation pass. It has no randomization,
optimizer, second ticker, residual redistribution, or LLM numeric input.

## 14. Valuation and price freshness

V1 uses the existing fixed, validated, normalized local valuation capture.
The V1 decision and order workflow performs no provider or network
acquisition. Updating the capture is a separate explicit operator step.

The validated mark must remain bound to:

- ticker;
- current portfolio/source identity where the existing contract requires it;
- provider and source artifact;
- capture identity;
- reviewed calendar identity; and
- reviewed completed session.

The same validated mark is the deterministic anchor for both:

- `H_i` exposure valuation; and
- BUY limit-price construction.

The mark must correspond exactly to the latest completed reviewed regular
US-equity session at the trusted evaluation time. The existing reviewed
session-calendar owner remains the only freshness owner.

The following fail closed:

- non-session mark;
- older completed-session mark;
- future or later uncompleted-session mark; and
- invalid calendar or session identity.

If a newer required completed session becomes effective before final
publication and invalidates the proposal's bound inputs, the result is
NO_TRADE. V1 does not introduce another price-freshness system.

## 15. Limit-price construction and rounding

V1 uses the existing committed static role ladders, ordered steps, offsets,
weights, plan type, and time-in-force behavior from strategy settings. V1 does
not use the extended-role ladder because extended orders are outside scope.

The reachable V1 ladder contract is exact:

| Role | Tickers | Plan type | TIF | Step | Offset | Weight |
|---|---|---|---|---|---:|---:|
| `benchmark_carrier_core` | `QQQ` | `new_limit_ladder` | `DAY` | `starter` | `-0.010` | `0.40` |
| `benchmark_carrier_core` | `QQQ` | `new_limit_ladder` | `DAY` | `L1` | `-0.030` | `0.25` |
| `benchmark_carrier_core` | `QQQ` | `new_limit_ladder` | `DAY` | `L2` | `-0.060` | `0.18` |
| `benchmark_carrier_core` | `QQQ` | `new_limit_ladder` | `DAY` | `L3` | `-0.095` | `0.12` |
| `benchmark_carrier_core` | `QQQ` | `new_limit_ladder` | `DAY` | `L4` | `-0.130` | `0.05` |
| `diversified_core_buffer` | `VOO`, `VTI`, `VT` | `new_limit_ladder` | `DAY` | `L1` | `-0.025` | `0.35` |
| `diversified_core_buffer` | `VOO`, `VTI`, `VT` | `new_limit_ladder` | `DAY` | `L2` | `-0.055` | `0.30` |
| `diversified_core_buffer` | `VOO`, `VTI`, `VT` | `new_limit_ladder` | `DAY` | `L3` | `-0.085` | `0.22` |
| `diversified_core_buffer` | `VOO`, `VTI`, `VT` | `new_limit_ladder` | `DAY` | `L4` | `-0.120` | `0.13` |
| `sector_alpha_tilt` | `SMH`, `IGV` | `new_limit_ladder` | `DAY` | `L1` | `-0.040` | `0.28` |
| `sector_alpha_tilt` | `SMH`, `IGV` | `new_limit_ladder` | `DAY` | `L2` | `-0.085` | `0.27` |
| `sector_alpha_tilt` | `SMH`, `IGV` | `new_limit_ladder` | `DAY` | `L3` | `-0.135` | `0.25` |
| `sector_alpha_tilt` | `SMH`, `IGV` | `new_limit_ladder` | `DAY` | `L4` | `-0.180` | `0.20` |

Each reachable role's weights sum exactly to `1.00`. V1 uses the complete
ordered role ladder and does not synthesize, remove, reorder, or rename a
configured step before whole-share feasibility is evaluated. A zero-share
step is represented as an omitted order row under section 16, not as a changed
ladder policy.

For every selected ticker and ladder step `j`:

```text
raw_limit_price_ij = validated_mark_i * (1 + approved_role_step_offset_j)
limit_price_ij = ROUND_HALF_UP(raw_limit_price_ij, 0.01)
```

The price is rounded before whole-share quantity calculation.

Required arithmetic contract:

- exact Decimal arithmetic only;
- USD price quantum is exactly `0.01`;
- rounding mode is exactly `ROUND_HALF_UP`;
- each rounded limit price is finite and strictly positive; and
- binary floating-point order arithmetic is prohibited.

An invalid mark, offset, weight, or rounded price produces NO_TRADE.

The compiler accepts no LLM price, no per-order discretionary operator price
override, and no dynamic execution strategy. Arithmetic authority belongs to
future deterministic code; prompt text is not execution authority.

## 16. Whole-share compilation

For the selected target `T_i` and each approved role-ladder step `j`:

```text
step_target_ij = T_i * role_weight_ij
limit_price_ij = approved rounded deterministic limit price
qty_ij = floor(step_target_ij / limit_price_ij)
notional_ij = qty_ij * limit_price_ij
```

Compilation rules are exact:

- `qty_ij` is an integer number of shares.
- Fractional shares are prohibited.
- A row with `qty_ij == 0` is omitted.
- Zero-share feasibility is not a contract failure.
- `N_i = sum(notional_ij for actual positive compiled rows)`.

After compilation, recompute from actual positive compiled rows:

```text
Y_i = E_i + N_i
Y = sum(Y_i)
D_i = H_i + Y_i
A = final non-core alpha exposure
Z = final CORE exposure
```

Final requirements include:

```text
N_i <= T_i
0 <= Y <= X
Y <= R
A <= Z
```

## 17. Residual handling

Residual target, cap, or X capacity remains unused. V1 stops after the one
compilation pass.

V1 performs no:

- top-up;
- redistribution between ladder steps;
- second ticker selection;
- second allocation pass;
- minimum-spend enforcement; or
- attempt to make `Y == X`.

If every proposed row compiles to zero shares while every required contract
remains valid, the terminal result is HOLD.

## 18. HOLD contract

HOLD is a successful terminal outcome when all required authority-bearing
inputs are valid, current, authenticated, and evaluable but no portfolio
change is selected or feasible.

HOLD includes these exact cases:

- no INCREMENT_ELIGIBLE ticker;
- `C == 0`;
- applicable deterministic A/Z headroom is zero;
- the selected target is zero;
- every compiled quantity is zero; or
- valid residual capacity remains unused under the one-pass policy.

The future publication path must emit an immutable HOLD review package and no
order artifact.

## 19. NO_TRADE contract

NO_TRADE is a successful fail-closed terminal outcome when a safe actionable
proposal cannot be established because a required authority-bearing condition
is:

- missing;
- malformed;
- stale;
- future-dated or later-uncompleted;
- contradictory;
- unauthenticated;
- manual-review-required;
- unresolved;
- invalid under H1 provenance or current-context validation;
- invalid because `E > X`;
- invalid because `E > R`;
- invalid because `A_initial > Z_initial`;
- changed after proposal binding;
- invalid under postcompile budget, cap, or quantity validation; or
- blocked by final safety.

LLM pessimistic wording alone cannot create NO_TRADE.

The future publication path must emit an immutable NO_TRADE review package
when its own publication contract can be satisfied and must emit no order
artifact.

## 20. Manual LLM boundary and qualitative role

The manual LLM boundary is exact:

```text
deterministic grounded prompt
-> HUMAN submits to LLM
-> HUMAN obtains response
-> HUMAN saves exact raw response bytes
-> deterministic capture
-> deterministic validation
-> deterministic currentness and context validation
```

The repository must not add an LLM API, provider/model selection, automatic
submission, automatic response retrieval, polling, retry workflow, scheduler,
credentials, or browser automation for V1.

The following validated H1 qualitative values are carried verbatim into human
review:

- `long_horizon_opportunity`
- `valuation_context`
- `portfolio_contribution`
- `evidence_integrity`
- `prior_thesis_change`
- `evidence_references`

LLM prose has zero authority over:

- universe membership;
- product eligibility;
- disposition;
- PREFERRED or STANDARD classification;
- priority;
- budget or cap;
- allocation;
- price;
- quantity;
- state;
- permission;
- gate result;
- publication readiness;
- order readiness;
- order creation; or
- execution.

Deterministic code can inspect only closed structural and provenance facts,
including schema validity, exact provenance binding, evidence-reference
membership, and deterministic cited-entry ticker membership. V1 contains no
sentiment, conviction, confidence, or qualitative scoring authority.

## 21. Future proposal state

The future recognition state is:

```text
H1_V1_DETERMINISTIC_PROPOSAL_READY
```

Its recognition PR initially permits exactly:

- HOLD
- NO_TRADE

It initially blocks:

- NEW_BUY;
- SELL;
- ROTATION;
- REBALANCE;
- EXTENDED_ETF_ADMISSION;
- ORDER_COMPILATION; and
- any promoted authority not independently approved.

Recognition requires the complete deterministic proposal contract. H1 report
existence, currentness-observation existence, or `is_current == true` is
insufficient.

State recognition alone grants no investment permission. Existing state
precedence remains unchanged. In particular, fresh H1 does not replace a
legacy `STRICT_STALE` state: `STRICT_STALE + fresh H1 -> STRICT_STALE`, and the
existing legacy SELL permission is preserved.

## 22. Future permission and activation sequence

Implementation must preserve this sequence:

1. V1-P0: this policy contract.
2. V1-P1: report-only deterministic proposal.
3. V1-P2: proposal state recognition with HOLD and NO_TRADE only.
4. V1-P3: separate NEW_BUY permission.
5. V1-P4: deterministic compiler dry-run with no durable order artifact.
6. V1-P5: separate ORDER_COMPILATION permission.
7. V1-P6: downstream and postcompile final-safety activation.
8. V1-P7: immutable HOLD and NO_TRADE publication.
9. V1-P8: review-only BUY-order activation.

State recognition, permission expansion, gate activation, publication
activation, and order-path activation remain separate authority boundaries.
Combining them requires a separately approved design.

## 23. SELL deferral

SELL remains outside the H1 V1 BUY-only path. P0 changes no legacy SELL state,
permission, gate, final-safety, publication, or order behavior.

The future V1 BUY path must not require H1 currentness for an independently
authorized legacy or risk-reducing SELL merely because V1 BUY increments use
H1 context.

H1 SELL design and activation require a separate post-V1 policy and PR
sequence.

## 24. Review-only order and broker boundary

Every eventual V1 BUY order artifact must state:

```text
review_only = true
broker_submission = false
```

It is an immutable artifact for human review and human manual broker entry.
V1 does not authorize a broker SDK, credentials, live submission, automatic
placement, polling, automatic retry, or execution scheduling.

The repository produces no evidence that a human entered the order unless the
operator later updates an authorized local portfolio/open-order source.

## 25. P0 authority matrix

P0 changes no runtime authority:

| Authority or stage | Before P0 | After P0 |
|---|---|---|
| HOLD | allowed | allowed |
| NO_TRADE | allowed | allowed |
| PROMOTED_RESEARCH_DECISION | unchanged; blocked where currently blocked | unchanged; blocked where currently blocked |
| PROMOTED_RESEARCH_AUDIT | unchanged; blocked where currently blocked | unchanged; blocked where currently blocked |
| H1 V1 NEW_BUY | blocked | blocked |
| H1 V1 SELL | blocked | blocked |
| Legacy SELL | unchanged | unchanged |
| H1 V1 ROTATION | blocked | blocked |
| H1 V1 REBALANCE | blocked | blocked |
| H1 V1 EXTENDED_ETF_ADMISSION | blocked | blocked |
| H1 V1 ORDER_COMPILATION | blocked | blocked |
| H1 actionable Step3 | blocked | blocked |
| H1 Step4 | blocked | blocked |
| H1 V1 final-safety activation | absent/blocked | absent/blocked |
| V1 publication | absent | absent |
| V1 order path | unreachable | unreachable |
| Broker/live execution | absent/unauthorized | absent/unauthorized |

## 26. Current implementation versus future approved behavior

### 26.1 Currently implemented supporting facts

The repository currently contains report-only or non-authorizing owners for:

- exact strict-holdings exposure and validated local marks;
- the fixed human X source and exact existing unfilled BUY commitments;
- the fixed r source and exact scalar `r * H` observation;
- deterministic universe and role projection;
- reviewed US-equity session freshness;
- H1 prompt, manual response capture, structured report validation, and
  report-only currentness observation; and
- existing legacy static role ladders and legacy order validation.

These owners do not, merely by existing, implement the future V1 policy or
grant its authority.

### 26.2 Approved but not currently implemented

The repository does not currently implement or activate:

- this exact A/Z proposal policy;
- the V1 disposition contract;
- the ticker evidence-coverage eligibility rule;
- the exact fixed V1 priority and one-ticker allocation algorithm;
- the complete deterministic V1 proposal artifact;
- `H1_V1_DETERMINISTIC_PROPOSAL_READY`;
- H1 V1 NEW_BUY permission;
- H1 V1 ORDER_COMPILATION permission;
- the deterministic V1 whole-share compiler;
- the V1 postcompile final-safety gate;
- immutable V1 HOLD or NO_TRADE publication;
- review-only V1 BUY publication; or
- any V1 broker/live execution path.

Later PRs must not interpret this approved future policy as evidence that any
of these runtime behaviors already exists.
