"""Report-only research availability / freshness / degraded-mode evaluator.

Roadmap PR C of the Deep Research degraded-mode design
(``docs/deep_research_degraded_mode_design.md``).

This module is a deterministic *observer*. Given the current run's strict
handoff candidate validation, the strategy settings, and any persisted
last-known-good (LKG) handoff, it classifies a research-availability state and
emits a deterministic action permission (allowed / blocked actions). It does
**not** gate the pipeline, does not let any downstream step read its output, and
never raises on degraded input.

Key safety property: missing / stale / invalid research can never yield
``NEW_BUY``; ``HOLD`` / ``NO_TRADE`` are always allowed.
"""

from __future__ import annotations

import errno
import hashlib
import json
import os
import stat
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import Any, Final

from investment_orchestrator.common.paths import repo_root
from investment_orchestrator.common.stable_read import (
    MmiStableReadError,
    stable_read_exact_bytes,
)
from investment_orchestrator.research.h1_mapped_recognition import (
    H1MappedRecognitionFacts,
)
from investment_orchestrator.state.last_good_research_handoff import (
    decision_relevant_settings,
    strategy_settings_hash,
)


# --- states ------------------------------------------------------------------
STRICT_FRESH = "STRICT_FRESH"
STRICT_STALE = "STRICT_STALE"
# R2E.1: a deterministic, strict-valid, fresh compiled evidence-first handoff
# (Step 1C) exists, but the raw Deep Research handoff is not valid+fresh. This is
# NON-ACTIONABLE by policy — HOLD / NO_TRADE only — and never permits NEW_BUY.
# It exists only to recognize the compiled handoff instead of mislabeling the run
# as INVALID_CONTRACT / DEGRADED_*; opening any actionable path requires a future
# explicit PR.
STRICT_FRESH_EVIDENCE_ONLY = "STRICT_FRESH_EVIDENCE_ONLY"
# R2E.4: like STRICT_FRESH_EVIDENCE_ONLY, but the report-only compiled
# support-signal artifact proves a valid analyst memo referenced valid, fresh,
# applicable deterministic research anchors (accepted_support_signals non-empty,
# permission_effect="none", not_authorization=true). It is NON-ACTIONABLE by
# policy — HOLD / NO_TRADE only — and never permits NEW_BUY / ORDER_COMPILATION.
# It only sharpens observability over STRICT_FRESH_EVIDENCE_ONLY; opening any
# actionable path requires a future explicit gate PR (R2E.5b).
STRICT_FRESH_GROUNDED_MEMO_NON_ACTIONABLE = "STRICT_FRESH_GROUNDED_MEMO_NON_ACTIONABLE"
# R2E.5b-5b: a real active promotion pointer exists and proves that a compiled
# actionable handoff was promoted to the effective artifact, but the pointer is
# explicitly pending future gates. NON-ACTIONABLE by policy — HOLD / NO_TRADE
# only — and never permits NEW_BUY / ORDER_COMPILATION.
STRICT_FRESH_COMPILED_ACTIONABLE_PENDING_GATES = "STRICT_FRESH_COMPILED_ACTIONABLE_PENDING_GATES"
# R2E.5b-6c: FIRST TRUE PERMISSION CHANGE — Step 2 decision-only. The
# pending-gates promotion additionally passed the R2E.5b-6a verification and the
# R2E.5b-6b dry-run this run, so Step 2 may render/parse a decision from the
# promoted effective handoff under PROMOTED_RESEARCH_DECISION. It permits
# NOTHING else: NEW_BUY / ORDER_COMPILATION / SELL / ROTATION / REBALANCE /
# EXTENDED_ETF_ADMISSION stay blocked, Step 3/4 stay blocked, the final
# execution safety gate is unchanged, and the full order-eligible
# STRICT_FRESH_COMPILED_ACTIONABLE state remains absent / non-enabled.
STRICT_FRESH_COMPILED_ACTIONABLE_STEP2_DECISION_ONLY = (
    "STRICT_FRESH_COMPILED_ACTIONABLE_STEP2_DECISION_ONLY"
)
# R2E.5b-6f: SECOND PROMOTED PERMISSION CHANGE — Step 3 audit-only. This builds
# on the promoted Step 2 decision-only state and adds only
# PROMOTED_RESEARCH_AUDIT. It still permits no NEW_BUY / ORDER_COMPILATION and
# cannot reach Step 4 or final execution.
STRICT_FRESH_COMPILED_ACTIONABLE_STEP3_AUDIT_ONLY = (
    "STRICT_FRESH_COMPILED_ACTIONABLE_STEP3_AUDIT_ONLY"
)
DEGRADED_WITH_LAST_GOOD = "DEGRADED_WITH_LAST_GOOD"
DEGRADED_NO_RESEARCH = "DEGRADED_NO_RESEARCH"
INVALID_CONTRACT = "INVALID_CONTRACT"
NO_OUTPUT = "NO_OUTPUT"
MANUAL_REVIEW_REQUIRED = "MANUAL_REVIEW_REQUIRED"
# Validated, role-mapped H1 research recognized as *fresh* but STRICTLY
# NON-ACTIONABLE — HOLD / NO_TRADE only. It is deliberately a distinct state:
# mapped H1 research is never relabeled STRICT_FRESH (which is order-eligible)
# and stale mapped H1 is never relabeled STRICT_STALE (which carries SELL). The
# word FRESH in the name describes source freshness only; it grants nothing.
H1_MAPPED_FRESH_NON_ACTIONABLE = "H1_MAPPED_FRESH_NON_ACTIONABLE"
# V1 downstream proposal state.  The availability evaluator does not emit this
# state: the pure V1 proposal owner recognizes it only after a complete current
# positive proposal evaluation.  Its row lives here solely so the canonical
# state/action owner owns the exact permission contract.  V1-P5 grants NEW_BUY
# and ORDER_COMPILATION; every downstream route remains separately blocked.
H1_V1_DETERMINISTIC_PROPOSAL_READY = "H1_V1_DETERMINISTIC_PROPOSAL_READY"

# Source label recorded when mapped H1 research is the selected availability
# source. Matches the H1 bridge's own ``source_kind`` literal.
H1_ROLE_MAPPED_SOURCE = "H1_ROLE_MAPPED"

# Compiled-handoff modes (R2D metadata) recognized as a real compiler output.
# Kept as literals to avoid a state->research layer import; the integration passes
# the metadata the compiler wrote, so these match by construction.
_COMPILED_MODES = ("evidence_only", "evidence_plus_memo", "invalid_memo_ignored")

# Fallback states that a valid+fresh compiled handoff may relabel as
# STRICT_FRESH_EVIDENCE_ONLY (permissions stay HOLD / NO_TRADE either way). Raw
# valid states (STRICT_FRESH / STRICT_STALE) and MANUAL_REVIEW_REQUIRED are never
# relabeled — the compiled handoff only improves observability over "no usable
# fresh raw handoff", it never removes a manual-review escalation or SELL right.
_EVIDENCE_ONLY_REPLACEABLE = frozenset(
    {INVALID_CONTRACT, DEGRADED_NO_RESEARCH, NO_OUTPUT, DEGRADED_WITH_LAST_GOOD}
)

# Fallback states that fresh, validated mapped-H1 research may replace. Declared
# independently of ``_EVIDENCE_ONLY_REPLACEABLE`` (currently equal) because the
# two precedence policies are logically separate and must not drift together.
# Legacy STRICT_FRESH / STRICT_STALE, every compiled/promoted state, and
# MANUAL_REVIEW_REQUIRED are absent: mapped H1 never removes a SELL right, never
# demotes a promoted permission, and never clears a manual-review escalation.
_H1_MAPPED_REPLACEABLE = frozenset(
    {INVALID_CONTRACT, DEGRADED_NO_RESEARCH, NO_OUTPUT, DEGRADED_WITH_LAST_GOOD}
)

# Current-source projection identities retained for audit when mapped H1 is the
# selected source. Deliberately a subset of the bridge's fields: the bridge owns
# the full provenance chain, and the availability artifact keeps only the
# current-source bindings an auditor needs to tie the state to this run.
_H1_CURRENT_SOURCE_IDENTITY_FIELDS = (
    "strategy_settings_source_record_identity_sha256",
    "policy_projection_identity_sha256",
    "universe_projection_identity_sha256",
    "portfolio_source_record_identity_sha256",
    "portfolio_projection_identity_sha256",
)

# --- actions -----------------------------------------------------------------
ACTIONS = (
    "HOLD",
    "NO_TRADE",
    "SELL",
    "NEW_BUY",
    "ROTATION",
    "REBALANCE",
    "EXTENDED_ETF_ADMISSION",
    "ORDER_COMPILATION",
)

# R2E.5b-6c: the Step 2 decision-only action. Deliberately NOT appended to the
# ACTIONS baseline above: blocked_actions are derived from ACTIONS, so keeping
# it out leaves every other state's allowed/blocked artifact byte-identical
# (raw STRICT_FRESH in particular must NOT gain this action).
PROMOTED_RESEARCH_DECISION_ACTION = "PROMOTED_RESEARCH_DECISION"
# R2E.5b-6f: audit-only Step 3 action. Also deliberately kept out of ACTIONS so
# legacy blocked_actions remain stable and no unrelated state gains this action.
PROMOTED_RESEARCH_AUDIT_ACTION = "PROMOTED_RESEARCH_AUDIT"

# Allowed action set per state. Default-deny for order-generating actions;
# HOLD / NO_TRADE are always allowed. Blocked actions are derived as the
# complement, preserving ACTIONS order.
_ALLOWED_ACTIONS_BY_STATE: dict[str, tuple[str, ...]] = {
    STRICT_FRESH: ACTIONS,
    STRICT_STALE: ("HOLD", "NO_TRADE", "SELL"),
    STRICT_FRESH_EVIDENCE_ONLY: ("HOLD", "NO_TRADE"),
    STRICT_FRESH_GROUNDED_MEMO_NON_ACTIONABLE: ("HOLD", "NO_TRADE"),
    STRICT_FRESH_COMPILED_ACTIONABLE_PENDING_GATES: ("HOLD", "NO_TRADE"),
    # R2E.5b-6c: exactly HOLD / NO_TRADE / PROMOTED_RESEARCH_DECISION — no
    # NEW_BUY, no ORDER_COMPILATION, no SELL/ROTATION/REBALANCE/EXTENDED sleeve.
    STRICT_FRESH_COMPILED_ACTIONABLE_STEP2_DECISION_ONLY: (
        "HOLD",
        "NO_TRADE",
        PROMOTED_RESEARCH_DECISION_ACTION,
    ),
    STRICT_FRESH_COMPILED_ACTIONABLE_STEP3_AUDIT_ONLY: (
        "HOLD",
        "NO_TRADE",
        PROMOTED_RESEARCH_DECISION_ACTION,
        PROMOTED_RESEARCH_AUDIT_ACTION,
    ),
    DEGRADED_WITH_LAST_GOOD: ("HOLD", "NO_TRADE"),
    DEGRADED_NO_RESEARCH: ("HOLD", "NO_TRADE"),
    INVALID_CONTRACT: ("HOLD", "NO_TRADE"),
    NO_OUTPUT: ("HOLD", "NO_TRADE"),
    MANUAL_REVIEW_REQUIRED: ("HOLD", "NO_TRADE"),
    # Explicit closed row: exactly HOLD / NO_TRADE. No SELL, no NEW_BUY, no
    # ROTATION / REBALANCE / EXTENDED_ETF_ADMISSION, no ORDER_COMPILATION, and
    # none of the promoted decision/audit actions. No wildcard, no inheritance,
    # no fallthrough — unknown states remain fail closed via KeyError.
    H1_MAPPED_FRESH_NON_ACTIONABLE: ("HOLD", "NO_TRADE"),
    # V1-P5 permission only.  The complete positive deterministic proposal may
    # continue toward future deterministic order compilation, but every
    # downstream route and all remaining actions stay separately blocked.
    H1_V1_DETERMINISTIC_PROPOSAL_READY: (
        "HOLD",
        "NO_TRADE",
        "NEW_BUY",
        "ORDER_COMPILATION",
    ),
}


def canonical_allowed_actions_for_state(state: str) -> tuple[str, ...]:
    """Exact allowed action row for one recognized state."""
    return _ALLOWED_ACTIONS_BY_STATE[state]


def canonical_blocked_actions_for_state(state: str) -> tuple[str, ...]:
    """Blocked complement of one recognized state, in canonical ``ACTIONS`` order.

    This is THE derivation of ``blocked_actions``: the availability writer below
    uses it, and consumers that must recognize a canonical persisted permission
    row derive from it rather than re-declaring the complement. Unknown states
    raise ``KeyError``, the same fail-closed behavior as the table itself.
    """
    allowed = canonical_allowed_actions_for_state(state)
    return tuple(action for action in ACTIONS if action not in allowed)


# R2E.5b-6b artifact contract literals, kept as literals to avoid a
# state->research layer import; Step 1 passes the artifacts those modules wrote,
# so these match by construction and any drift fails closed (no upgrade).
_PROMOTED_STEP2_VERIFICATION_SCHEMA = "promoted_handoff_step2_verification_v1"
_PROMOTED_STEP2_DRY_RUN_SCHEMA = "promoted_step2_gate_dry_run_v1"
_PROMOTED_STEP3_VERIFICATION_SCHEMA = "promoted_handoff_step3_audit_verification_v1"
_PROMOTED_STEP3_DRY_RUN_SCHEMA = "promoted_step3_audit_gate_dry_run_v1"
_DRY_RUN_REAL_GATE_POLICY_BLOCKER = "real_gate_still_closed_by_policy"
# permission_effect label for the decision-only state's artifacts: Step 2 may
# decide from promoted research; the order path stays closed.
PERMISSION_EFFECT_STEP2_DECISION_ONLY = "promoted_step2_decision_only"
PERMISSION_EFFECT_STEP3_AUDIT_ONLY = "promoted_step3_audit_only"
_PROMOTED_STEP3_SOURCE_ARTIFACT = "research_handoff_candidate_effective.json"

# --- stale policy ------------------------------------------------------------
# age <= fresh_days       -> fresh
# fresh_days < age <= stale_days -> stale
# age > stale_days        -> too_old (manual review)
DEFAULT_STALE_POLICY: dict[str, int] = {"fresh_days": 8, "stale_days": 16}

# Signed-date invariant reason codes. A negative age means the source is dated
# after the trusted evaluation boundary; it is never fresh and is never clamped.
CURRENT_HANDOFF_FUTURE_DATED = "current_handoff_future_dated"
LAST_GOOD_HANDOFF_FUTURE_DATED = "last_good_handoff_future_dated"
COMPILED_HANDOFF_FUTURE_DATED = "compiled_handoff_future_dated"
H1_MAPPED_RESEARCH_FUTURE_DATED = "h1_mapped_research_future_dated"
H1_MAPPED_RESEARCH_TOO_OLD = "h1_mapped_research_too_old"


@dataclass(frozen=True)
class ResearchAvailabilityResult:
    """Deterministic, report-only research availability / permission decision."""

    state: str
    research_availability: str
    fresh_research_available: bool
    handoff_valid: bool
    handoff_stale: bool
    handoff_age_days: int | None
    stale_label: str
    last_good_available: bool
    last_good_usable: bool
    last_good_age_days: int | None
    settings_hash_match: bool | None
    universe_match: bool | None
    allowed_actions: list[str]
    blocked_actions: list[str]
    manual_review_required: bool
    blocker_reasons: list[str] = field(default_factory=list)
    non_blocker_reasons: list[str] = field(default_factory=list)
    fresh_days: int = DEFAULT_STALE_POLICY["fresh_days"]
    stale_days: int = DEFAULT_STALE_POLICY["stale_days"]
    source_as_of_date: str | None = None
    now_date: str | None = None
    last_good_as_of_date: str | None = None
    # R2E.1 compiled evidence-first handoff recognition (report-only fields).
    source: str = "raw_research_handoff"
    compiled_handoff_valid: bool = False
    compiled_handoff_fresh: bool = False
    compilation_mode: str | None = None
    analyst_memo_present: bool | None = None
    analyst_memo_valid: bool | None = None
    source_artifacts: dict[str, str] = field(default_factory=dict)
    # R2E.4 grounded-memo support recognition (report-only, non-actionable).
    support_signals_present: bool = False
    accepted_support_signal_count: int = 0
    grounded_memo_support_present: bool = False
    support_signals_not_authorization: bool | None = None
    # R2E.5b-5b promoted pointer recognition (diagnostic, non-actionable).
    promoted_pointer_present: bool = False
    promoted_pointer_valid: bool = False
    promotion_status: str | None = None
    effective_handoff_present: bool = False
    effective_handoff_valid: bool = False
    candidate_actionable_row_count: int | None = None
    actionable_this_run_tickers: list[str] = field(default_factory=list)
    promotion_expires_at: str | None = None
    permission_effect: str | None = None
    not_authorization: bool | None = None
    # R2E.5b-6c Step 2 decision-only upgrade (first true permission change).
    promoted_step2_decision_only: bool = False
    # R2E.5b-6f Step 3 audit-only upgrade. Still no order authority.
    promoted_step3_audit_only: bool = False
    # Mapped-H1 recognition (strictly non-actionable). ``None`` whenever no
    # validated H1 candidate was supplied, so Legacy runs are unaffected.
    h1_mapped_recognition: dict[str, Any] | None = None
    h1_mapped_selected: bool = False


def evaluate_research_availability(
    *,
    candidate_validation: Any | None,
    candidate: Mapping[str, Any] | None,
    strategy_settings: Mapping[str, Any] | None,
    source_as_of_date: str | None,
    now_date: str | None = None,
    last_good_handoff: Mapping[str, Any] | None = None,
    last_good_metadata: Mapping[str, Any] | None = None,
    stale_policy: Mapping[str, Any] | None = None,
    parsed_output_available: bool | None = None,
    compiled_candidate_validation: Any | None = None,
    compiled_metadata: Mapping[str, Any] | None = None,
    compiled_source_as_of_date: str | None = None,
    compiled_source_artifacts: Mapping[str, str] | None = None,
    compiled_support_signals: Mapping[str, Any] | None = None,
    promoted_pointer: Mapping[str, Any] | None = None,
    promoted_effective_handoff: Mapping[str, Any] | None = None,
    promoted_effective_validation: Mapping[str, Any] | None = None,
    promoted_source_artifacts: Mapping[str, str] | None = None,
    promoted_step2_verification: Mapping[str, Any] | None = None,
    promoted_step2_gate_dry_run: Mapping[str, Any] | None = None,
    promoted_step3_audit_verification: Mapping[str, Any] | None = None,
    promoted_step3_audit_gate_dry_run: Mapping[str, Any] | None = None,
    h1_mapped_facts: Any | None = None,
) -> ResearchAvailabilityResult:
    """Classify research availability and derive a deterministic permission.

    ``parsed_output_available`` disambiguates ``INVALID_CONTRACT`` /
    ``DEGRADED_NO_RESEARCH`` / ``NO_OUTPUT`` when there is no usable candidate;
    when omitted it is inferred from whether a candidate object was supplied.

    R2E.1 (report-only recognition, conservative): when the raw handoff is NOT
    valid+fresh, but a deterministic compiled evidence-first handoff (Step 1C) is
    strict-valid and fresh and its metadata reports a recognized compilation
    mode, the fallback state is relabeled ``STRICT_FRESH_EVIDENCE_ONLY``. This is
    NON-ACTIONABLE — HOLD / NO_TRADE only; it never adds NEW_BUY / ORDER_COMPILATION
    and never overrides STRICT_FRESH / STRICT_STALE / MANUAL_REVIEW_REQUIRED. When
    no compiled inputs are supplied, behavior is byte-for-byte unchanged.
    """
    fresh_days, stale_days = _resolve_stale_policy(stale_policy)

    handoff_valid = _validation_is_valid(candidate_validation)
    candidate_present = isinstance(candidate, Mapping)
    output_present = parsed_output_available if parsed_output_available is not None else candidate_present

    handoff_age_days = _age_days(now_date, source_as_of_date)

    last_good_available = isinstance(last_good_handoff, Mapping) and isinstance(last_good_metadata, Mapping)
    last_good_as_of_date = (
        _str_or_none(last_good_metadata.get("source_as_of_date")) if last_good_available else None
    )
    last_good_age_days = _age_days(now_date, last_good_as_of_date) if last_good_available else None

    current_hash = strategy_settings_hash(decision_relevant_settings(strategy_settings))
    current_universe = _current_universe_set(strategy_settings)

    blocker_reasons: list[str] = []
    non_blocker_reasons: list[str] = []

    if handoff_valid:
        settings_hash_match: bool | None = True
        universe_match: bool | None = True
        state = _classify_current_valid(
            handoff_age_days, fresh_days, stale_days, blocker_reasons, non_blocker_reasons
        )
    else:
        last_good_hash = (
            _str_or_none(last_good_metadata.get("strategy_settings_hash"))
            if last_good_available
            else None
        )
        last_good_universe = _last_good_universe_set(last_good_metadata) if last_good_available else None
        settings_hash_match = (
            (current_hash == last_good_hash)
            if (last_good_available and current_hash is not None and last_good_hash is not None)
            else (None if not last_good_available else False)
        )
        universe_match = (
            (current_universe == last_good_universe)
            if (last_good_available and current_universe is not None and last_good_universe is not None)
            else (None if not last_good_available else False)
        )
        state = _classify_fallback(
            candidate_present=candidate_present,
            output_present=output_present,
            last_good_available=last_good_available,
            last_good_age_days=last_good_age_days,
            stale_days=stale_days,
            universe_match=universe_match,
            settings_hash_match=settings_hash_match,
            blocker_reasons=blocker_reasons,
            non_blocker_reasons=non_blocker_reasons,
        )

    # --- R2E.1: recognize the compiled evidence-first handoff (non-actionable) ---
    compiled_handoff_valid = _validation_is_valid(compiled_candidate_validation)
    compiled_age_days = _age_days(now_date, compiled_source_as_of_date)
    compiled_handoff_fresh = (
        compiled_handoff_valid
        and compiled_age_days is not None
        and 0 <= compiled_age_days <= fresh_days
    )
    compilation_mode = (
        _str_or_none(compiled_metadata.get("compilation_mode"))
        if isinstance(compiled_metadata, Mapping)
        else None
    )
    analyst_memo_present = (
        compiled_metadata.get("analyst_memo_present") if isinstance(compiled_metadata, Mapping) else None
    )
    analyst_memo_valid = (
        compiled_metadata.get("analyst_memo_valid") if isinstance(compiled_metadata, Mapping) else None
    )
    # Fail closed: a missing / malformed metadata mode does NOT relabel the state.
    compiled_metadata_ok = compilation_mode in _COMPILED_MODES
    source = "raw_research_handoff"
    source_artifacts: dict[str, str] = (
        {str(k): str(v) for k, v in compiled_source_artifacts.items()}
        if isinstance(compiled_source_artifacts, Mapping)
        else {}
    )
    compiled_handoff_future_dated = (
        compiled_handoff_valid
        and compiled_age_days is not None
        and compiled_age_days < 0
    )
    if compiled_handoff_future_dated:
        # The token is a final-state blocker only when compiled evidence would
        # otherwise be eligible to replace the raw/fallback state. When valid
        # raw evidence independently controls the state, retain its state and
        # actions and record the ignored compiled-source defect diagnostically.
        if not handoff_valid and state in _EVIDENCE_ONLY_REPLACEABLE:
            blocker_reasons.append(COMPILED_HANDOFF_FUTURE_DATED)
            state = MANUAL_REVIEW_REQUIRED
        else:
            non_blocker_reasons.append(COMPILED_HANDOFF_FUTURE_DATED)
    if (
        not handoff_valid
        and compiled_handoff_valid
        and compiled_handoff_fresh
        and compiled_metadata_ok
        and state in _EVIDENCE_ONLY_REPLACEABLE
    ):
        state = STRICT_FRESH_EVIDENCE_ONLY
        source = "compiled_research_handoff"
        blocker_reasons.append(
            "compiled_handoff_non_actionable: a deterministic evidence-first strict handoff is "
            "valid and fresh, but it is non-actionable by policy."
        )
        blocker_reasons.append(
            "evidence_only_no_new_buy: NEW_BUY / ORDER_COMPILATION require a future explicit PR; "
            "this state permits HOLD / NO_TRADE only."
        )

    # --- R2E.4: recognize grounded memo support (still non-actionable) ---
    # Only sharpens the label when we are ALREADY in the evidence-only compiled
    # state and the report-only support-signal artifact proves accepted grounded
    # memo support. Permissions are identical (HOLD / NO_TRADE); fail closed on a
    # missing / malformed / non-authorization artifact.
    support_signals_present = isinstance(compiled_support_signals, Mapping)
    support_signals = compiled_support_signals if support_signals_present else {}
    accepted = support_signals.get("accepted_support_signals")
    accepted_support_signal_count = len(accepted) if isinstance(accepted, list) else 0
    support_signals_not_authorization = (
        support_signals.get("not_authorization") if support_signals_present else None
    )
    grounded_criteria = (
        support_signals_present
        and support_signals.get("analyst_memo_present") is True
        and support_signals.get("analyst_memo_valid") is True
        and accepted_support_signal_count > 0
        and support_signals.get("permission_effect") == "none"
        and support_signals.get("not_authorization") is True
    )
    grounded_memo_support_present = False
    if state == STRICT_FRESH_EVIDENCE_ONLY and grounded_criteria:
        state = STRICT_FRESH_GROUNDED_MEMO_NON_ACTIONABLE
        grounded_memo_support_present = True
        blocker_reasons.append(
            "grounded_memo_support_non_actionable: fresh evidence + a valid analyst memo + accepted "
            "grounded support signals exist, but this state is non-actionable by policy."
        )
        blocker_reasons.append(
            "new_buy_requires_future_gate_pr: NEW_BUY / ORDER_COMPILATION remain blocked and require a "
            "future explicit gate PR; this state permits HOLD / NO_TRADE only."
        )

    # --- R2E.5b-5b: recognize promoted effective handoff pending gates --------
    # This is only a label/diagnostic upgrade from the grounded non-actionable
    # state. Raw STRICT_FRESH and all stale/degraded/manual-review states keep
    # their existing precedence. Every criterion must pass; otherwise fail closed
    # to the current safe state and permissions.
    pending = _evaluate_pending_gates_promotion(
        promoted_pointer=promoted_pointer,
        promoted_effective_handoff=promoted_effective_handoff,
        promoted_effective_validation=promoted_effective_validation,
        now_date=now_date,
    )
    if isinstance(promoted_source_artifacts, Mapping):
        for key, value in promoted_source_artifacts.items():
            source_artifacts[str(key)] = str(value)
    if state == STRICT_FRESH_GROUNDED_MEMO_NON_ACTIONABLE and pending["recognized"] is True:
        state = STRICT_FRESH_COMPILED_ACTIONABLE_PENDING_GATES
        source = "promoted_compiled_actionable_handoff"
        blocker_reasons.append("promoted_actionable_handoff_pending_gates")
        blocker_reasons.append("new_buy_requires_future_gate_pr")
        blocker_reasons.append("order_compilation_requires_future_gate_pr")

    # --- R2E.5b-6c: Step 2 decision-only upgrade (FIRST TRUE PERMISSION CHANGE) --
    # Only the pending-gates promoted state may upgrade, and only when the
    # R2E.5b-6a verification and the R2E.5b-6b dry-run for THIS run both fully
    # pass (fail closed on anything missing / malformed / false / stale /
    # hash-mismatched: the state simply stays pending-gates HOLD / NO_TRADE).
    # The upgrade adds exactly PROMOTED_RESEARCH_DECISION — Step 2 decision-only.
    # It never touches raw STRICT_FRESH, never adds NEW_BUY / ORDER_COMPILATION,
    # and leaves the final execution safety gate closed.
    promoted_step2_decision_only = False
    promoted_step3_audit_only = False
    if state == STRICT_FRESH_COMPILED_ACTIONABLE_PENDING_GATES and _step2_decision_only_upgrade_ok(
        verification=promoted_step2_verification,
        dry_run=promoted_step2_gate_dry_run,
        promoted_effective_handoff=promoted_effective_handoff,
        now_date=now_date,
    ):
        state = STRICT_FRESH_COMPILED_ACTIONABLE_STEP2_DECISION_ONLY
        promoted_step2_decision_only = True
        blocker_reasons.remove("promoted_actionable_handoff_pending_gates")
        blocker_reasons.append("promoted_step2_decision_only_enabled")
        blocker_reasons.append("final_execution_requires_future_gate_pr")

    # --- R2E.5b-6f: Step 3 audit-only upgrade --------------------------------
    # This may only build on the Step 2 decision-only state and only when the
    # 6e deterministic verification + dry-run artifacts prove the exact
    # audit-only posture. It grants PROMOTED_RESEARCH_AUDIT only; NEW_BUY and
    # ORDER_COMPILATION remain absent and final execution remains closed.
    if state == STRICT_FRESH_COMPILED_ACTIONABLE_STEP2_DECISION_ONLY and _step3_audit_only_upgrade_ok(
        verification=promoted_step3_audit_verification,
        dry_run=promoted_step3_audit_gate_dry_run,
        promoted_effective_handoff=promoted_effective_handoff,
        now_date=now_date,
    ):
        state = STRICT_FRESH_COMPILED_ACTIONABLE_STEP3_AUDIT_ONLY
        promoted_step3_audit_only = True
        blocker_reasons.append("promoted_step3_audit_only_enabled")
        blocker_reasons.append("step4_order_compilation_requires_future_gate_pr")

    # --- mapped-H1 recognition (strictly non-actionable) ----------------------
    # Evaluated LAST, so every Legacy/compiled/promoted outcome above has already
    # settled. Fresh mapped H1 may replace ONLY the four fallback states in
    # _H1_MAPPED_REPLACEABLE; STRICT_FRESH, STRICT_STALE (and its SELL right),
    # every compiled/promoted state, and MANUAL_REVIEW_REQUIRED are protected by
    # construction because they are absent from that set. An absent or invalid
    # H1 candidate yields None and changes nothing at all.
    h1 = _evaluate_h1_mapped_recognition(
        h1_mapped_facts=h1_mapped_facts,
        now_date=now_date,
        fresh_days=fresh_days,
        stale_days=stale_days,
    )
    h1_mapped_selected = False
    if h1 is not None:
        solely_expired_legacy_lkg = (
            state == MANUAL_REVIEW_REQUIRED
            and not candidate_present
            and not output_present
            and last_good_available
            and last_good_age_days is not None
            and last_good_age_days > stale_days
        )
        h1_controls = state in _H1_MAPPED_REPLACEABLE
        if h1["freshness"] in ("future_dated", "too_old"):
            # Mirrors the existing compiled/current/last-good convention: escalate
            # only when the defective source would otherwise control the state;
            # when a protected Legacy result controls, record it diagnostically
            # and leave that result and its actions untouched.
            reason = (
                H1_MAPPED_RESEARCH_FUTURE_DATED
                if h1["freshness"] == "future_dated"
                else H1_MAPPED_RESEARCH_TOO_OLD
            )
            if h1_controls:
                blocker_reasons.append(reason)
                state = MANUAL_REVIEW_REQUIRED
            else:
                non_blocker_reasons.append(reason)
        elif h1["freshness"] == "fresh" and (h1_controls or solely_expired_legacy_lkg):
            if solely_expired_legacy_lkg:
                blocker_reasons.clear()
            state = H1_MAPPED_FRESH_NON_ACTIONABLE
            source = H1_ROLE_MAPPED_SOURCE
            h1_mapped_selected = True
            blocker_reasons.append(
                "h1_mapped_research_non_actionable: validated role-mapped H1 research is fresh, "
                "but this state is non-actionable by policy."
            )
            blocker_reasons.append(
                "h1_mapped_no_new_buy: SELL / NEW_BUY / ORDER_COMPILATION remain blocked and "
                "require a future explicit gate PR; this state permits HOLD / NO_TRADE only."
            )
        # "stale" and "unknown" deliberately fall through: mapped H1 is simply
        # not selected and the existing Legacy/degraded outcome is preserved.

    last_good_usable = state == DEGRADED_WITH_LAST_GOOD
    age_for_label = handoff_age_days if handoff_valid else (last_good_age_days if last_good_available else None)
    stale_label = _stale_label(age_for_label, fresh_days, stale_days)

    allowed_actions = list(canonical_allowed_actions_for_state(state))
    blocked_actions = list(canonical_blocked_actions_for_state(state))
    manual_review_required = state == MANUAL_REVIEW_REQUIRED

    return ResearchAvailabilityResult(
        state=state,
        research_availability=state.lower(),
        fresh_research_available=state == STRICT_FRESH,
        handoff_valid=handoff_valid,
        handoff_stale=state == STRICT_STALE,
        handoff_age_days=handoff_age_days,
        stale_label=stale_label,
        last_good_available=last_good_available,
        last_good_usable=last_good_usable,
        last_good_age_days=last_good_age_days,
        settings_hash_match=settings_hash_match,
        universe_match=universe_match,
        allowed_actions=allowed_actions,
        blocked_actions=blocked_actions,
        manual_review_required=manual_review_required,
        blocker_reasons=blocker_reasons,
        non_blocker_reasons=non_blocker_reasons,
        fresh_days=fresh_days,
        stale_days=stale_days,
        source_as_of_date=source_as_of_date,
        now_date=now_date,
        last_good_as_of_date=last_good_as_of_date,
        source=source,
        compiled_handoff_valid=compiled_handoff_valid,
        compiled_handoff_fresh=compiled_handoff_fresh,
        compilation_mode=compilation_mode,
        analyst_memo_present=analyst_memo_present,
        analyst_memo_valid=analyst_memo_valid,
        source_artifacts=source_artifacts,
        support_signals_present=support_signals_present,
        accepted_support_signal_count=accepted_support_signal_count,
        grounded_memo_support_present=grounded_memo_support_present,
        support_signals_not_authorization=support_signals_not_authorization,
        promoted_pointer_present=pending["promoted_pointer_present"],
        promoted_pointer_valid=pending["promoted_pointer_valid"],
        promotion_status=pending["promotion_status"],
        effective_handoff_present=pending["effective_handoff_present"],
        effective_handoff_valid=pending["effective_handoff_valid"],
        candidate_actionable_row_count=pending["candidate_actionable_row_count"],
        actionable_this_run_tickers=pending["actionable_this_run_tickers"],
        promotion_expires_at=pending["promotion_expires_at"],
        permission_effect=(
            PERMISSION_EFFECT_STEP3_AUDIT_ONLY
            if promoted_step3_audit_only
            else (
                PERMISSION_EFFECT_STEP2_DECISION_ONLY
                if promoted_step2_decision_only
                else pending["permission_effect"]
            )
        ),
        not_authorization=pending["not_authorization"],
        promoted_step2_decision_only=promoted_step2_decision_only,
        promoted_step3_audit_only=promoted_step3_audit_only,
        h1_mapped_recognition=h1,
        h1_mapped_selected=h1_mapped_selected,
    )


def _step2_decision_only_upgrade_ok(
    *,
    verification: Mapping[str, Any] | None,
    dry_run: Mapping[str, Any] | None,
    promoted_effective_handoff: Mapping[str, Any] | None,
    now_date: Any,
) -> bool:
    """R2E.5b-6c upgrade criteria over the R2E.5b-6a/6b artifacts. Fail closed.

    Every criterion must pass; a missing / malformed / false / stale artifact
    keeps the pending-gates state. ``current_real_gate_allows`` / the policy
    blocker are asserted against the dry-run's recorded pre-upgrade posture, so
    an artifact claiming the real gate was already open can never upgrade.
    """
    v = verification if isinstance(verification, Mapping) else None
    d = dry_run if isinstance(dry_run, Mapping) else None
    if v is None or d is None:
        return False

    dry_run_blockers = d.get("dry_run_blockers")
    dry_run_ok = (
        d.get("schema_version") == _PROMOTED_STEP2_DRY_RUN_SCHEMA
        and d.get("is_llm_generated") is False
        and d.get("report_only") is True
        and d.get("dry_run_only") is True
        and d.get("permission_effect") == "none"
        and d.get("not_authorization") is True
        and d.get("would_allow_step2_promoted_decision") is True
        and d.get("current_real_gate_allows") is False
        and d.get("future_permission_required") == PROMOTED_RESEARCH_DECISION_ACTION
        and d.get("future_state_required") == STRICT_FRESH_COMPILED_ACTIONABLE_STEP2_DECISION_ONLY
        and isinstance(dry_run_blockers, list)
        and _DRY_RUN_REAL_GATE_POLICY_BLOCKER in dry_run_blockers
    )
    if not dry_run_ok:
        return False

    verification_blockers = v.get("verification_blockers")
    effective_hash = (
        _sha256_of(promoted_effective_handoff)
        if isinstance(promoted_effective_handoff, Mapping)
        else None
    )
    return (
        v.get("schema_version") == _PROMOTED_STEP2_VERIFICATION_SCHEMA
        and v.get("is_llm_generated") is False
        and v.get("report_only") is True
        and v.get("permission_effect") == "none"
        and v.get("not_authorization") is True
        and v.get("valid_for_step2_decision") is True
        and isinstance(verification_blockers, list)
        and not verification_blockers
        and v.get("future_permission_required") == PROMOTED_RESEARCH_DECISION_ACTION
        and v.get("promotion_status") == "pending_gates"
        and v.get("consumed_by_step2") is False
        and _expires_not_stale(v.get("promotion_expires_at"), now_date)
        and effective_hash is not None
        and v.get("effective_handoff_sha256") == effective_hash
        and v.get("pointer_effective_handoff_sha256") == effective_hash
    )


def _step3_audit_only_upgrade_ok(
    *,
    verification: Mapping[str, Any] | None,
    dry_run: Mapping[str, Any] | None,
    promoted_effective_handoff: Mapping[str, Any] | None,
    now_date: Any,
) -> bool:
    """R2E.5b-6f upgrade criteria over the R2E.5b-6e artifacts. Fail closed."""
    v = verification if isinstance(verification, Mapping) else None
    d = dry_run if isinstance(dry_run, Mapping) else None
    if v is None or d is None:
        return False

    current_allowed_actions = d.get("current_allowed_actions")
    dry_run_blockers = d.get("dry_run_blockers")
    source_artifacts = d.get("source_artifacts")
    dry_run_ok = (
        d.get("schema_version") == _PROMOTED_STEP3_DRY_RUN_SCHEMA
        and d.get("is_llm_generated") is False
        and d.get("report_only") is True
        and d.get("dry_run_only") is True
        and d.get("permission_effect") == "none"
        and d.get("not_authorization") is True
        and d.get("not_execution_authorization") is True
        and d.get("would_allow_promoted_step3_audit") is True
        and d.get("current_real_gate_allows") is False
        and d.get("future_state_required") == STRICT_FRESH_COMPILED_ACTIONABLE_STEP3_AUDIT_ONLY
        and d.get("future_action_required") == PROMOTED_RESEARCH_AUDIT_ACTION
        and d.get("current_state") == STRICT_FRESH_COMPILED_ACTIONABLE_STEP2_DECISION_ONLY
        and current_allowed_actions
        == ["HOLD", "NO_TRADE", PROMOTED_RESEARCH_DECISION_ACTION]
        and d.get("verification_valid_for_promoted_step3_audit") is True
        and d.get("order_compilation_allowed") is False
        and d.get("new_buy_permission") is False
        and d.get("step4_allowed") is False
        and d.get("final_execution_allowed") is False
        and d.get("broker_automation_allowed") is False
        and d.get("future_step3_source_artifact") == _PROMOTED_STEP3_SOURCE_ARTIFACT
        and d.get("raw_deep_research_source_used") is False
        and isinstance(dry_run_blockers, list)
        and _DRY_RUN_REAL_GATE_POLICY_BLOCKER in dry_run_blockers
        and _source_artifacts_use_promoted_effective(source_artifacts)
    )
    if not dry_run_ok:
        return False

    verification_blockers = v.get("verification_blockers")
    live_blockers = v.get("live_step2_verification_blockers")
    effective_hash = (
        _sha256_of(promoted_effective_handoff)
        if isinstance(promoted_effective_handoff, Mapping)
        else None
    )
    return (
        v.get("schema_version") == _PROMOTED_STEP3_VERIFICATION_SCHEMA
        and v.get("is_llm_generated") is False
        and v.get("report_only") is True
        and v.get("permission_effect") == "none"
        and v.get("not_authorization") is True
        and v.get("not_execution_authorization") is True
        and v.get("valid_for_promoted_step3_audit") is True
        and isinstance(verification_blockers, list)
        and not verification_blockers
        and v.get("future_state_required") == STRICT_FRESH_COMPILED_ACTIONABLE_STEP3_AUDIT_ONLY
        and v.get("future_action_required") == PROMOTED_RESEARCH_AUDIT_ACTION
        and v.get("future_step3_source_artifact") == _PROMOTED_STEP3_SOURCE_ARTIFACT
        and v.get("raw_deep_research_source_used") is False
        and v.get("order_compilation_allowed") is False
        and v.get("new_buy_permission") is False
        and v.get("step4_allowed") is False
        and v.get("final_execution_allowed") is False
        and v.get("broker_automation_allowed") is False
        and v.get("live_step2_verification_valid") is True
        and isinstance(live_blockers, list)
        and not live_blockers
        and _expires_not_stale(v.get("promotion_expires_at"), now_date)
        and effective_hash is not None
        and v.get("effective_handoff_sha256") == effective_hash
        and v.get("pointer_effective_handoff_sha256") == effective_hash
        and _source_artifacts_use_promoted_effective(v.get("source_artifacts"))
    )


# R2E.5b-6f.1: token-level raw-source rejection, mirroring
# ``promoted_step3_audit_dry_run._is_raw_deep_research_source_token``. Kept as a
# local mirror (not an import) to avoid a circular import: that module already
# imports state constants from this one.
_RAW_DEEP_RESEARCH_ARTIFACT = "research_output.json"
_RAW_DEEP_RESEARCH_BARE_TOKENS = frozenset(
    {"research_output", "raw_deep_research", "raw_deep_research_output"}
)


def _is_raw_deep_research_source_token(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    token = value.strip().replace("\\", "/")
    if token == _RAW_DEEP_RESEARCH_ARTIFACT or token.endswith(f"/{_RAW_DEEP_RESEARCH_ARTIFACT}"):
        return True
    return token in _RAW_DEEP_RESEARCH_BARE_TOKENS


def _is_promoted_effective_source_token(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    token = value.strip().replace("\\", "/")
    return token == _PROMOTED_STEP3_SOURCE_ARTIFACT or token.endswith(
        f"/{_PROMOTED_STEP3_SOURCE_ARTIFACT}"
    )


def _source_artifacts_use_promoted_effective(source_artifacts: Any) -> bool:
    """Fail-closed source check: reject any raw Deep Research token outright,
    require the promoted effective handoff artifact to be present.

    Uses per-key/value token matching rather than a serialized-JSON substring
    scan, so a filename that merely shares a substring with either artifact
    name (e.g. ``research_output_v2.json``) cannot slip past — and, more
    importantly, so unrelated keys/values elsewhere in the mapping cannot
    trigger a false "raw source used" or false "promoted effective present"
    read by accidental substring overlap.
    """
    if not isinstance(source_artifacts, Mapping):
        return False
    uses_promoted_effective = False
    for key, value in source_artifacts.items():
        if _is_raw_deep_research_source_token(key) or _is_raw_deep_research_source_token(value):
            return False
        if _is_promoted_effective_source_token(key) or _is_promoted_effective_source_token(value):
            uses_promoted_effective = True
    return uses_promoted_effective


def _h1_temporal_date(value: Any) -> str | None:
    """Return the calendar-date part of a canonical H1 temporal fact.

    The bridge guarantees dates are ``YYYY-MM-DD`` and timestamps are exactly
    ``YYYY-MM-DDTHH:MM:SS.ffffffZ``, so the leading ten characters are the UTC
    calendar date in both cases. No clock is read and nothing is substituted.
    """
    if not isinstance(value, str) or len(value) < 10:
        return None
    return value[:10]


def _evaluate_h1_mapped_recognition(
    *,
    h1_mapped_facts: Any,
    now_date: Any,
    fresh_days: int,
    stale_days: int,
) -> dict[str, Any] | None:
    """Classify mapped-H1 freshness from validated typed facts. Report-only.

    Accepts ONLY a factory-created ``H1MappedRecognitionFacts``. The bridge owns
    structural / provenance / current-source validity and its instances cannot be
    constructed by any other caller, so a raw mapping report, a plain dict, or any
    other object is ignored entirely (returns ``None``). Bridge construction
    failure therefore never becomes a permission state, and an invalid or absent
    H1 candidate can never suppress a valid Legacy result.

    Freshness is decided HERE — availability stays the single freshness owner and
    reuses the same ``fresh_days`` / ``stale_days`` policy and ``_age_days``
    helper as every other source. The OLDEST required fact controls: ages are
    never averaged and a newer source never compensates for an older required
    one. ``context_evaluation_timestamp_utc`` is never part of the age; it serves
    only the conservative future-date integrity check. Bridge construction time,
    mapping generation time, file mtimes, and the current clock are never
    substituted for a missing fact.
    """
    if not isinstance(h1_mapped_facts, H1MappedRecognitionFacts):
        return None

    required_ages: dict[str, int | None] = {
        "policy_as_of_date": _age_days(now_date, h1_mapped_facts.policy_as_of_date),
        "portfolio_source_date": _age_days(now_date, h1_mapped_facts.portfolio_source_date),
    }
    # Optional fact: included only when present. It can make the run look older,
    # never fresher. When absent it is simply omitted — never substituted.
    if h1_mapped_facts.policy_source_run_timestamp_utc is not None:
        required_ages["policy_source_run_timestamp_utc"] = _age_days(
            now_date,
            _h1_temporal_date(h1_mapped_facts.policy_source_run_timestamp_utc),
        )
    context_age = _age_days(
        now_date,
        _h1_temporal_date(h1_mapped_facts.context_evaluation_timestamp_utc),
    )

    ages = list(required_ages.values())
    if context_age is None or any(age is None for age in ages):
        # Unknown age is never fresh; mapped H1 is simply not selected.
        freshness = "unknown"
        age_days: int | None = None
    else:
        age_days = max(ages)  # oldest required fact controls
        if min(ages) < 0 or context_age < 0:
            # Any required fact (or the evaluation context itself) dated after
            # the trusted boundary is a hard integrity defect, never freshness.
            freshness = "future_dated"
        elif age_days <= fresh_days:
            freshness = "fresh"
        elif age_days <= stale_days:
            freshness = "stale"
        else:
            freshness = "too_old"

    return {
        "source_kind": h1_mapped_facts.source_kind,
        "freshness": freshness,
        "age_days": age_days,
        "required_fact_ages_days": dict(required_ages),
        "identity": {
            "mapping_schema_version": h1_mapped_facts.mapping_schema_version,
            "mapping_report_identity_sha256": h1_mapped_facts.mapping_report_identity_sha256,
            "role_map_version": h1_mapped_facts.role_map_version,
            "target_legacy_validator_contract_version": (
                h1_mapped_facts.target_legacy_validator_contract_version
            ),
        },
        "current_source_identities": {
            name: getattr(h1_mapped_facts, name)
            for name in _H1_CURRENT_SOURCE_IDENTITY_FIELDS
        },
        "temporal_evidence": {
            "policy_as_of_date": h1_mapped_facts.policy_as_of_date,
            "portfolio_source_date": h1_mapped_facts.portfolio_source_date,
            "policy_source_run_timestamp_utc": (
                h1_mapped_facts.policy_source_run_timestamp_utc
            ),
            "context_evaluation_timestamp_utc": (
                h1_mapped_facts.context_evaluation_timestamp_utc
            ),
        },
    }


def _h1_mapped_artifact_fields(result: ResearchAvailabilityResult) -> dict[str, Any]:
    """Minimum mapped-H1 audit/provenance fields for the availability artifacts.

    Emitted ONLY when mapped H1 is the selected source. When Legacy controls (or
    no H1 candidate exists) this contributes no keys at all, so existing Legacy
    artifacts serialize exactly as before and no migration is required.
    """
    if not result.h1_mapped_selected or result.h1_mapped_recognition is None:
        return {}
    recognition = result.h1_mapped_recognition
    return {
        "h1_mapped_selected": True,
        "h1_mapped_source_kind": recognition["source_kind"],
        "h1_mapped_freshness": recognition["freshness"],
        "h1_mapped_age_days": recognition["age_days"],
        "h1_mapped_identity": dict(recognition["identity"]),
        "h1_mapped_current_source_identities": dict(recognition["current_source_identities"]),
        "h1_mapped_temporal_evidence": dict(recognition["temporal_evidence"]),
    }


class H1MappedResearchSelectionContractError(RuntimeError):
    """Raised when an owner result claims a positive H1 selection it cannot prove.

    ``research_availability`` remains the sole owner of ``h1_mapped_selected``;
    this is not a second selection algorithm. It is a structural consistency
    assertion over the ALREADY-DECIDED owner result: when
    ``h1_mapped_selected`` is ``True`` but ``state`` / ``source`` /
    ``h1_mapped_recognition`` are not what a genuine mapped-H1 selection always
    produces, the contradiction is an internal defect, never a signal to
    silently normalize the projection to unselected.
    """


# Schema version for :class:`H1MappedResearchSelectionProjection`. Independent
# of every artifact schema version above: this projection is an in-memory,
# same-run value, never persisted.
_H1_SELECTION_PROJECTION_SCHEMA_VERSION: Final = "h1_mapped_research_selection_projection_v1"

_SHA256_HEX_DIGITS: Final = frozenset("0123456789abcdef")


def _is_sha256_hex(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and set(value) <= _SHA256_HEX_DIGITS


@dataclass(frozen=True, slots=True)
class H1MappedResearchSelectionProjection:
    """Narrow, immutable, SAME-RUN snapshot of availability's H1 selection facts.

    This is NOT a Phase-3 admission, disposition, or permission grant — it
    carries no ``allowed_actions`` / ``blocked_actions`` / permission field of
    any kind, and it is not authorization for anything. It exposes only the
    already-computed availability-owner facts a future same-run consumer needs
    to bind an admission decision to the mapped-H1 candidate this run selected
    (or did not select), without ever handing that consumer the raw, only
    shallowly-frozen :class:`ResearchAvailabilityResult` or its mutable
    ``allowed_actions`` / ``h1_mapped_recognition`` containers.

    Identity doctrine: ``mapping_report_identity_sha256`` is meaningful ONLY
    for SAME-RUN candidate cohesion — tying a later same-run consumer to the
    exact mapping report this run's availability evaluation selected. It is
    NOT a cross-run content identity, NOT a persistence/continuity identity,
    NOT a policy-change identity, and NOT a currentness identity across runs.
    Nothing about this projection proves the identified mapping report is
    still current on a later run.
    """

    schema_version: str
    h1_mapped_selected: bool
    state: str
    source: str
    mapping_report_identity_sha256: str | None
    report_only: bool
    not_authorization: bool
    authority_effect: str


def build_h1_mapped_research_selection_projection(
    result: ResearchAvailabilityResult,
) -> H1MappedResearchSelectionProjection:
    """Project only the H1 selection facts ``result`` already computed.

    Does not recompute ``h1_mapped_selected``: ``research_availability``
    remains the sole selection owner. Copies only immutable scalars — never a
    reference into ``result.allowed_actions``, ``result.h1_mapped_recognition``,
    or any other mutable container on ``result`` — so mutating those containers
    after this call cannot alter the returned projection.

    Positive (``h1_mapped_selected is True``): the owner result MUST already be
    structurally consistent with ``state == H1_MAPPED_FRESH_NON_ACTIONABLE``,
    ``source == H1_ROLE_MAPPED_SOURCE``, and a present, well-formed
    ``h1_mapped_recognition["identity"]["mapping_report_identity_sha256"]`` —
    every real evaluator selection satisfies this by construction. An owner
    result that violates it is an impossible/internal contradiction, not a
    routine "not selected" outcome, so this fails closed with
    :class:`H1MappedResearchSelectionContractError` rather than silently
    normalizing to ``h1_mapped_selected = False`` or reclassifying
    state/source.

    Negative (``h1_mapped_selected is False``): ``state`` / ``source`` are
    preserved as-is, as plain observation facts about what the owner actually
    selected instead (Legacy, compiled, promoted, or a fallback state) — this
    function does not infer or reclassify why H1 was not selected.
    ``mapping_report_identity_sha256`` is always ``None`` in this case, even if
    ``result.h1_mapped_recognition`` happens to carry diagnostic identity
    fields for an unselected/stale/future-dated H1 candidate: leaving it absent
    means a future same-run consumer can never accidentally bind an admission
    to a recognition receipt that was never selected.
    """
    if not result.h1_mapped_selected:
        return H1MappedResearchSelectionProjection(
            schema_version=_H1_SELECTION_PROJECTION_SCHEMA_VERSION,
            h1_mapped_selected=False,
            state=result.state,
            source=result.source,
            mapping_report_identity_sha256=None,
            report_only=True,
            not_authorization=True,
            authority_effect="NONE",
        )

    recognition = result.h1_mapped_recognition
    identity = recognition.get("identity") if isinstance(recognition, Mapping) else None
    mapping_report_identity_sha256 = (
        identity.get("mapping_report_identity_sha256") if isinstance(identity, Mapping) else None
    )
    if (
        result.state != H1_MAPPED_FRESH_NON_ACTIONABLE
        or result.source != H1_ROLE_MAPPED_SOURCE
        or not _is_sha256_hex(mapping_report_identity_sha256)
    ):
        raise H1MappedResearchSelectionContractError(
            "h1_mapped_selected=True but the owner result is not structurally "
            "consistent with H1_MAPPED_FRESH_NON_ACTIONABLE / H1_ROLE_MAPPED / "
            "a present mapping-report identity."
        )

    return H1MappedResearchSelectionProjection(
        schema_version=_H1_SELECTION_PROJECTION_SCHEMA_VERSION,
        h1_mapped_selected=True,
        state=result.state,
        source=result.source,
        mapping_report_identity_sha256=mapping_report_identity_sha256,
        report_only=True,
        not_authorization=True,
        authority_effect="NONE",
    )


def _classify_current_valid(
    age: int | None,
    fresh_days: int,
    stale_days: int,
    blocker_reasons: list[str],
    non_blocker_reasons: list[str],
) -> str:
    if age is not None and age < 0:
        blocker_reasons.append(CURRENT_HANDOFF_FUTURE_DATED)
        return MANUAL_REVIEW_REQUIRED
    if age is not None and age > stale_days:
        blocker_reasons.append(
            f"current strict handoff is valid but too old ({age}d > {stale_days}d); manual review required."
        )
        return MANUAL_REVIEW_REQUIRED
    if age is not None and age <= fresh_days:
        return STRICT_FRESH
    if age is None:
        blocker_reasons.append(
            "current strict handoff is valid but its age is unknown (missing as_of / now_date); "
            "treated as stale (no new buy)."
        )
    else:
        non_blocker_reasons.append(
            f"current strict handoff is valid but stale ({age}d > {fresh_days}d); new buy not permitted."
        )
    return STRICT_STALE


def _classify_fallback(
    *,
    candidate_present: bool,
    output_present: bool,
    last_good_available: bool,
    last_good_age_days: int | None,
    stale_days: int,
    universe_match: bool | None,
    settings_hash_match: bool | None,
    blocker_reasons: list[str],
    non_blocker_reasons: list[str],
) -> str:
    if last_good_available:
        if last_good_age_days is not None and last_good_age_days < 0:
            blocker_reasons.append(LAST_GOOD_HANDOFF_FUTURE_DATED)
            return MANUAL_REVIEW_REQUIRED
        if last_good_age_days is not None and last_good_age_days > stale_days:
            blocker_reasons.append(
                f"no fresh valid handoff and last-known-good is too old "
                f"({last_good_age_days}d > {stale_days}d); manual review required."
            )
            return MANUAL_REVIEW_REQUIRED
        if universe_match is False:
            blocker_reasons.append(
                "no fresh valid handoff and the universe changed since last-known-good; "
                "manual review required."
            )
            return MANUAL_REVIEW_REQUIRED
        if last_good_age_days is None:
            blocker_reasons.append(
                "no fresh valid handoff and last-known-good age is unknown; manual review required."
            )
            return MANUAL_REVIEW_REQUIRED
        # Usable last-good: degraded, hold / no-trade only.
        if settings_hash_match is False:
            non_blocker_reasons.append(
                "last-known-good is usable but non-universe strategy settings changed; "
                "degraded mode permits only HOLD / NO_TRADE."
            )
        non_blocker_reasons.append(
            f"using last-known-good handoff (age {last_good_age_days}d); no fresh valid handoff this run."
        )
        return DEGRADED_WITH_LAST_GOOD

    # No usable last-good available.
    if candidate_present:
        blocker_reasons.append(
            "parsed research output exists but strict handoff candidate is invalid, "
            "and no last-known-good is available."
        )
        return INVALID_CONTRACT
    if output_present:
        blocker_reasons.append(
            "parsed research output exists but produced no valid handoff, and no last-known-good is available."
        )
        return DEGRADED_NO_RESEARCH
    blocker_reasons.append("no research output and no last-known-good available.")
    return NO_OUTPUT


def _resolve_stale_policy(stale_policy: Mapping[str, Any] | None) -> tuple[int, int]:
    policy = dict(DEFAULT_STALE_POLICY)
    if isinstance(stale_policy, Mapping):
        for key in ("fresh_days", "stale_days"):
            value = stale_policy.get(key)
            if isinstance(value, int) and not isinstance(value, bool):
                policy[key] = value
    return policy["fresh_days"], policy["stale_days"]


def _validation_is_valid(candidate_validation: Any | None) -> bool:
    if candidate_validation is None:
        return False
    if isinstance(candidate_validation, Mapping):
        return candidate_validation.get("valid") is True
    return getattr(candidate_validation, "valid", False) is True


def _evaluate_pending_gates_promotion(
    *,
    promoted_pointer: Mapping[str, Any] | None,
    promoted_effective_handoff: Mapping[str, Any] | None,
    promoted_effective_validation: Mapping[str, Any] | None,
    now_date: Any,
) -> dict[str, Any]:
    pointer = promoted_pointer if isinstance(promoted_pointer, Mapping) else None
    effective = (
        promoted_effective_handoff if isinstance(promoted_effective_handoff, Mapping) else None
    )
    validation = (
        promoted_effective_validation
        if isinstance(promoted_effective_validation, Mapping)
        else None
    )

    promotion_status = _str_or_none(pointer.get("promotion_status")) if pointer else None
    permission_effect = _str_or_none(pointer.get("permission_effect")) if pointer else None
    expires_at = _str_or_none(pointer.get("promotion_expires_at")) if pointer else None
    row_count_value = pointer.get("candidate_actionable_row_count") if pointer else None
    row_count = (
        row_count_value
        if isinstance(row_count_value, int) and not isinstance(row_count_value, bool)
        else None
    )
    tickers = (
        [ticker for ticker in _string_list(pointer.get("actionable_this_run_tickers")) if ticker.strip()]
        if pointer
        else []
    )

    pointer_markers_valid = (
        pointer is not None
        and pointer.get("schema_version") == "active_research_handoff_source_v1"
        and promotion_status == "pending_gates"
        and pointer.get("source") == "promoted_compiled_actionable_handoff"
        and pointer.get("not_authorization") is True
        and pointer.get("future_pr_required") is True
        and permission_effect == "none_until_consumed_by_future_gate_pr"
        and pointer.get("consumed_by_availability") is False
        and pointer.get("consumed_by_step2") is False
        and pointer.get("consumed_by_gates") is False
        and row_count is not None
        and row_count > 0
        and bool(tickers)
        and _expires_not_stale(expires_at, now_date)
    )

    effective_validation_valid = _validation_is_valid(validation)
    effective_hash = _sha256_of(effective) if effective is not None else None
    pointer_effective_hash = (
        _str_or_none(pointer.get("effective_handoff_sha256")) if pointer is not None else None
    )
    candidate_hash = _str_or_none(pointer.get("candidate_sha256")) if pointer is not None else None
    hash_matches = (
        effective_hash is not None
        and pointer_effective_hash is not None
        and effective_hash == pointer_effective_hash
        and (candidate_hash is None or candidate_hash == effective_hash)
    )

    effective_paths_present = (
        pointer is not None
        and isinstance(pointer.get("effective_handoff_path"), str)
        and bool(pointer.get("effective_handoff_path"))
        and isinstance(pointer.get("effective_validation_path"), str)
        and bool(pointer.get("effective_validation_path"))
    )
    effective_handoff_valid = (
        effective is not None
        and validation is not None
        and effective_paths_present
        and effective_validation_valid
        and hash_matches
    )

    recognized = pointer_markers_valid and effective_handoff_valid
    return {
        "recognized": recognized,
        "promoted_pointer_present": pointer is not None,
        "promoted_pointer_valid": pointer_markers_valid,
        "promotion_status": promotion_status,
        "effective_handoff_present": effective is not None,
        "effective_handoff_valid": effective_handoff_valid,
        "candidate_actionable_row_count": row_count,
        "actionable_this_run_tickers": tickers,
        "promotion_expires_at": expires_at,
        "permission_effect": permission_effect,
        "not_authorization": pointer.get("not_authorization") if pointer else None,
    }


def _expires_not_stale(expires_at: Any, now_date: Any) -> bool:
    expires = _parse_date(expires_at)
    now = _parse_date(now_date)
    return expires is not None and now is not None and expires >= now


def _sha256_of(value: Any) -> str | None:
    if value is None:
        return None
    try:
        serialized = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError):
        return None
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _parse_date(value: Any) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        return None


def _age_days(now_date: Any, as_of_date: Any) -> int | None:
    now = _parse_date(now_date)
    as_of = _parse_date(as_of_date)
    if now is None or as_of is None:
        return None
    return (now - as_of).days


def _stale_label(age: int | None, fresh_days: int, stale_days: int) -> str:
    if age is None:
        return "unknown"
    if age < 0:
        return "future_dated"
    if age <= fresh_days:
        return "fresh"
    if age <= stale_days:
        return "stale"
    return "too_old"


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _current_universe_set(strategy_settings: Mapping[str, Any] | None) -> set[str] | None:
    if not isinstance(strategy_settings, Mapping):
        return None
    core = _string_list(strategy_settings.get("core_universe"))
    satellite = _string_list(strategy_settings.get("satellite_universe"))
    universe = set(core) | set(satellite)
    return universe or None


def _last_good_universe_set(metadata: Mapping[str, Any] | None) -> set[str] | None:
    if not isinstance(metadata, Mapping):
        return None
    universe = metadata.get("universe")
    if not isinstance(universe, Mapping):
        return None
    core = _string_list(universe.get("core_universe"))
    satellite = _string_list(universe.get("satellite_universe"))
    derived = set(core) | set(satellite)
    if derived:
        return derived
    allowed = set(_string_list(universe.get("allowed_buy_tickers")))
    return allowed or None


def _str_or_none(value: Any) -> str | None:
    return value if isinstance(value, str) else None


# --- serialization -----------------------------------------------------------


def research_availability_result_to_dict(result: ResearchAvailabilityResult) -> dict[str, Any]:
    """Full availability result (the ``research_availability.json`` artifact)."""
    return {
        "state": result.state,
        "research_availability": result.research_availability,
        "fresh_research_available": result.fresh_research_available,
        "handoff_valid": result.handoff_valid,
        "handoff_stale": result.handoff_stale,
        "handoff_age_days": result.handoff_age_days,
        "stale_label": result.stale_label,
        "last_good_available": result.last_good_available,
        "last_good_usable": result.last_good_usable,
        "last_good_age_days": result.last_good_age_days,
        "settings_hash_match": result.settings_hash_match,
        "universe_match": result.universe_match,
        "allowed_actions": list(result.allowed_actions),
        "blocked_actions": list(result.blocked_actions),
        "manual_review_required": result.manual_review_required,
        "blocker_reasons": list(result.blocker_reasons),
        "non_blocker_reasons": list(result.non_blocker_reasons),
        "fresh_days": result.fresh_days,
        "stale_days": result.stale_days,
        "source_as_of_date": result.source_as_of_date,
        "now_date": result.now_date,
        "last_good_as_of_date": result.last_good_as_of_date,
        "source": result.source,
        "compiled_handoff_valid": result.compiled_handoff_valid,
        "compiled_handoff_fresh": result.compiled_handoff_fresh,
        "compilation_mode": result.compilation_mode,
        "analyst_memo_present": result.analyst_memo_present,
        "analyst_memo_valid": result.analyst_memo_valid,
        "support_signals_present": result.support_signals_present,
        "accepted_support_signal_count": result.accepted_support_signal_count,
        "grounded_memo_support_present": result.grounded_memo_support_present,
        "not_authorization": result.not_authorization
        if result.not_authorization is not None
        else result.support_signals_not_authorization,
        "promoted_pointer_present": result.promoted_pointer_present,
        "promoted_pointer_valid": result.promoted_pointer_valid,
        "promotion_status": result.promotion_status,
        "effective_handoff_present": result.effective_handoff_present,
        "effective_handoff_valid": result.effective_handoff_valid,
        "candidate_actionable_row_count": result.candidate_actionable_row_count,
        "actionable_this_run_tickers": list(result.actionable_this_run_tickers),
        "promotion_expires_at": result.promotion_expires_at,
        "permission_effect": result.permission_effect or _permission_effect(result.state),
        "promoted_step2_decision_only": result.promoted_step2_decision_only,
        "promoted_step3_audit_only": result.promoted_step3_audit_only,
        "step3_audit_only_allowed": result.promoted_step3_audit_only,
        "order_compilation_allowed": "ORDER_COMPILATION" in result.allowed_actions,
        "new_buy_permission": "NEW_BUY" in result.allowed_actions,
        "step4_allowed": False if result.promoted_step3_audit_only else None,
        "final_execution_allowed": False if result.promoted_step3_audit_only else None,
        "broker_automation_allowed": False if result.promoted_step3_audit_only else None,
        "source_artifacts": dict(result.source_artifacts),
        **_h1_mapped_artifact_fields(result),
        "report_only": True,
    }


def research_freshness_report_to_dict(result: ResearchAvailabilityResult) -> dict[str, Any]:
    """Freshness-focused view (the ``research_freshness_report.json`` artifact)."""
    return {
        "state": result.state,
        "handoff_valid": result.handoff_valid,
        "handoff_age_days": result.handoff_age_days,
        "stale_label": result.stale_label,
        "fresh_days": result.fresh_days,
        "stale_days": result.stale_days,
        "source_as_of_date": result.source_as_of_date,
        "now_date": result.now_date,
        "last_good_available": result.last_good_available,
        "last_good_age_days": result.last_good_age_days,
        "last_good_as_of_date": result.last_good_as_of_date,
        "report_only": True,
    }


def _permission_effect(state: str) -> str:
    """Human-readable permission summary for the decision artifact (informational)."""
    if state == STRICT_FRESH:
        return "actionable"
    if state == STRICT_STALE:
        return "sell_only"
    return "none"


def research_degraded_mode_decision_to_dict(result: ResearchAvailabilityResult) -> dict[str, Any]:
    """Permission decision view (the ``research_degraded_mode_decision.json`` artifact)."""
    return {
        "state": result.state,
        "research_state": result.state,
        "research_availability": result.research_availability,
        "fresh_research_available": result.fresh_research_available,
        "handoff_valid": result.handoff_valid,
        "handoff_stale": result.handoff_stale,
        "settings_hash_match": result.settings_hash_match,
        "universe_match": result.universe_match,
        "allowed_actions": list(result.allowed_actions),
        "blocked_actions": list(result.blocked_actions),
        "manual_review_required": result.manual_review_required,
        "blocker_reasons": list(result.blocker_reasons),
        "non_blocker_reasons": list(result.non_blocker_reasons),
        "source": result.source,
        "compilation_mode": result.compilation_mode,
        "analyst_memo_present": result.analyst_memo_present,
        "analyst_memo_valid": result.analyst_memo_valid,
        "support_signals_present": result.support_signals_present,
        "accepted_support_signal_count": result.accepted_support_signal_count,
        "grounded_memo_support_present": result.grounded_memo_support_present,
        "not_authorization": result.not_authorization
        if result.not_authorization is not None
        else result.support_signals_not_authorization,
        "promoted_pointer_present": result.promoted_pointer_present,
        "promoted_pointer_valid": result.promoted_pointer_valid,
        "promotion_status": result.promotion_status,
        "effective_handoff_present": result.effective_handoff_present,
        "effective_handoff_valid": result.effective_handoff_valid,
        "candidate_actionable_row_count": result.candidate_actionable_row_count,
        "actionable_this_run_tickers": list(result.actionable_this_run_tickers),
        "promotion_expires_at": result.promotion_expires_at,
        "permission_effect": result.permission_effect or _permission_effect(result.state),
        "promoted_step2_decision_only": result.promoted_step2_decision_only,
        "promoted_step3_audit_only": result.promoted_step3_audit_only,
        "step3_audit_only_allowed": result.promoted_step3_audit_only,
        "order_compilation_allowed": "ORDER_COMPILATION" in result.allowed_actions,
        "new_buy_permission": "NEW_BUY" in result.allowed_actions,
        "step4_allowed": False if result.promoted_step3_audit_only else None,
        "final_execution_allowed": False if result.promoted_step3_audit_only else None,
        "broker_automation_allowed": False if result.promoted_step3_audit_only else None,
        "source_artifacts": dict(result.source_artifacts),
        **_h1_mapped_artifact_fields(result),
        "report_only": True,
    }


# --- persisted research-selection refusal-only reader -------------------------
#
# This reader answers exactly one question about the *persisted*
# ``research_availability.json`` artifact: "does this artifact contain a
# usable, authenticated positive mapped-H1 admission?" It never says yes.
#
# It does NOT prove "mapped H1 is currently not selected." It proves only
# that this persisted record does not offer a positive admission a Phase-3
# consumer may rely on. A completed design audit proved that no cross-run
# currentness binding exists for the persisted artifact today (the writer's
# causal inputs include facts, such as ``h1_mapped_facts``, that are never
# persisted and cannot be re-verified from disk). Consequently:
#
#   * a structurally valid artifact recording NO mapped-H1 selection is
#     treated as usable refusal evidence (a stale negative is harmless: its
#     only downstream meaning is "do not admit H1 research this run", and an
#     always-fail-closed answer stays correct even if the artifact is stale);
#   * a structurally valid artifact recording a positive mapped-H1 selection
#     is *never* surfaced as usable, because currentness cannot be proven —
#     it is reported as UNAVAILABLE, not as an admission and not as a content
#     defect.
#
# This function performs zero writes, zero availability recomputation, and
# reads no clock. It reads exactly one fixed file.

_RESEARCH_SELECTION_ARTIFACT_LOCATOR: Final = (
    "artifacts/current/step1_research/research_availability.json"
)
_RESEARCH_SELECTION_ARTIFACT_DIR_COMPONENTS: Final = (
    "artifacts",
    "current",
    "step1_research",
)
_RESEARCH_SELECTION_ARTIFACT_LEAF: Final = "research_availability.json"
# Generous bound: the current artifact is ~2.5 KB. This only protects against
# reading an unbounded/corrupt file, not a meaningful format constraint.
_RESEARCH_SELECTION_MAXIMUM_BYTES: Final = 262_144

# The closed 4-token ``source`` vocabulary the evaluator assigns. No owning
# constant enumerates all four together; three are inline literals in
# ``evaluate_research_availability`` (raw/compiled/promoted) and the fourth is
# the existing ``H1_ROLE_MAPPED_SOURCE`` constant, reused here rather than
# duplicated.
_RESEARCH_SELECTION_VALID_SOURCES: Final = frozenset(
    {
        "raw_research_handoff",
        "compiled_research_handoff",
        "promoted_compiled_actionable_handoff",
        H1_ROLE_MAPPED_SOURCE,
    }
)

# The non-boolean keys ``_h1_mapped_artifact_fields`` emits ONLY when mapped H1
# is selected (``h1_mapped_selected`` is the companion boolean, checked
# separately). Kept as an independently declared tuple rather than refactoring
# the writer to expose it, so this reader change cannot alter writer output;
# ``test_h1_mapped_availability.py`` pins the two in sync.
_H1_MAPPED_BLOCK_KEYS: Final = (
    "h1_mapped_source_kind",
    "h1_mapped_freshness",
    "h1_mapped_age_days",
    "h1_mapped_identity",
    "h1_mapped_current_source_identities",
    "h1_mapped_temporal_evidence",
)

_RESEARCH_SELECTION_RESULT_SCHEMA_VERSION: Final = (
    "research_selection_refusal_read_result_v1"
)
# Locally declared (not imported) so this reader has no dependency beyond the
# stdlib and the two narrow, already-audited primitives below; mirrors the
# same-named private constant in ``research/h1_mapped_recognition.py``.
_RESEARCH_SELECTION_AUTHORITY_EFFECT_NONE: Final = "NONE"


class ResearchSelectionRefusalReadStatus(str, Enum):
    """Closed read-result vocabulary. Distinct from availability ``state``."""

    UNAVAILABLE = "UNAVAILABLE"
    INVALID = "INVALID"
    REFUSAL_ONLY = "REFUSAL_ONLY"


@dataclass(frozen=True, slots=True)
class ResearchSelectionRefusalReadResult:
    """Refusal-only read outcome. Carries no permission or sizing fact.

    ``persisted_state`` / ``persisted_source`` are populated only on
    ``REFUSAL_ONLY``. On every other status, including the positive-artifact
    UNAVAILABLE case, they are ``None`` by construction: this type has no
    field through which a positive admission can be returned.
    """

    schema_version: str
    status: ResearchSelectionRefusalReadStatus
    reason_codes: tuple[str, ...]
    authority_effect: str
    report_only: bool
    not_authorization: bool
    artifact_locator: str
    artifact_observed_sha256: str | None
    artifact_observed_size_bytes: int | None
    persisted_state: str | None
    persisted_source: str | None


def _close_quietly(file_descriptor: int | None) -> None:
    if file_descriptor is not None:
        try:
            os.close(file_descriptor)
        except OSError:
            pass


def _open_research_selection_artifact_directory() -> int:
    """Descend the fixed path via symlink-safe opens; caller closes the result."""
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW
    root_fd: int | None = None
    artifacts_fd: int | None = None
    current_fd: int | None = None
    step1_fd: int | None = None
    try:
        root_fd = os.open(os.fspath(repo_root()), flags)
        artifacts_fd = os.open(
            _RESEARCH_SELECTION_ARTIFACT_DIR_COMPONENTS[0], flags, dir_fd=root_fd
        )
        current_fd = os.open(
            _RESEARCH_SELECTION_ARTIFACT_DIR_COMPONENTS[1], flags, dir_fd=artifacts_fd
        )
        step1_fd = os.open(
            _RESEARCH_SELECTION_ARTIFACT_DIR_COMPONENTS[2], flags, dir_fd=current_fd
        )
        if not all(
            stat.S_ISDIR(os.fstat(fd).st_mode)
            for fd in (root_fd, artifacts_fd, current_fd, step1_fd)
        ):
            raise OSError(errno.ENOTDIR, "expected a directory")
        return step1_fd
    except OSError:
        _close_quietly(step1_fd)
        raise
    finally:
        _close_quietly(current_fd)
        _close_quietly(artifacts_fd)
        _close_quietly(root_fd)


def _reject_duplicate_object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key in research selection artifact")
        result[key] = value
    return result


def _research_selection_unavailable(reason_code: str) -> ResearchSelectionRefusalReadResult:
    return ResearchSelectionRefusalReadResult(
        schema_version=_RESEARCH_SELECTION_RESULT_SCHEMA_VERSION,
        status=ResearchSelectionRefusalReadStatus.UNAVAILABLE,
        reason_codes=(reason_code,),
        authority_effect=_RESEARCH_SELECTION_AUTHORITY_EFFECT_NONE,
        report_only=True,
        not_authorization=True,
        artifact_locator=_RESEARCH_SELECTION_ARTIFACT_LOCATOR,
        artifact_observed_sha256=None,
        artifact_observed_size_bytes=None,
        persisted_state=None,
        persisted_source=None,
    )


def _research_selection_invalid(
    reason_code: str,
    *,
    observed_sha256: str | None,
    observed_size_bytes: int | None,
) -> ResearchSelectionRefusalReadResult:
    return ResearchSelectionRefusalReadResult(
        schema_version=_RESEARCH_SELECTION_RESULT_SCHEMA_VERSION,
        status=ResearchSelectionRefusalReadStatus.INVALID,
        reason_codes=(reason_code,),
        authority_effect=_RESEARCH_SELECTION_AUTHORITY_EFFECT_NONE,
        report_only=True,
        not_authorization=True,
        artifact_locator=_RESEARCH_SELECTION_ARTIFACT_LOCATOR,
        artifact_observed_sha256=observed_sha256,
        artifact_observed_size_bytes=observed_size_bytes,
        persisted_state=None,
        persisted_source=None,
    )


def _h1_mapped_block_shape(parsed: Mapping[str, Any]) -> str:
    """Classify the persisted ``h1_mapped_*`` block as one of three shapes.

    Returns ``"ABSENT"`` (writer's not-selected contract), ``"COMPLETE_POSITIVE"``
    (writer's selected contract, fully present), or ``"MALFORMED"`` (neither —
    a partial block, or a boolean/key combination the writer never produces).
    """
    has_selected_key = "h1_mapped_selected" in parsed
    selected_value = parsed.get("h1_mapped_selected")
    present_block_keys = frozenset(
        key for key in _H1_MAPPED_BLOCK_KEYS if key in parsed
    )
    if not has_selected_key and not present_block_keys:
        return "ABSENT"
    if (
        has_selected_key
        and selected_value is True
        and present_block_keys == frozenset(_H1_MAPPED_BLOCK_KEYS)
    ):
        return "COMPLETE_POSITIVE"
    return "MALFORMED"


def read_persisted_research_selection_refusal_only() -> ResearchSelectionRefusalReadResult:
    """Read the fixed persisted availability artifact for refusal facts ONLY.

    Proves nothing about currentness. See the module section header above for
    the exact semantic contract. Performs zero writes and never calls
    ``evaluate_research_availability`` or any of its classification helpers.
    """
    try:
        directory_fd = _open_research_selection_artifact_directory()
    except OSError:
        return _research_selection_unavailable(
            "RESEARCH_SELECTION_ARTIFACT_UNAVAILABLE"
        )

    try:
        try:
            raw_bytes = stable_read_exact_bytes(
                directory_fd,
                _RESEARCH_SELECTION_ARTIFACT_LEAF,
                maximum_bytes=_RESEARCH_SELECTION_MAXIMUM_BYTES,
            )
        except (MmiStableReadError, OSError):
            return _research_selection_unavailable(
                "RESEARCH_SELECTION_ARTIFACT_UNAVAILABLE"
            )
    finally:
        _close_quietly(directory_fd)

    try:
        text = raw_bytes.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        return _research_selection_invalid(
            "RESEARCH_SELECTION_ARTIFACT_MALFORMED",
            observed_sha256=None,
            observed_size_bytes=None,
        )

    try:
        parsed = json.loads(text, object_pairs_hook=_reject_duplicate_object_pairs)
    except (ValueError, RecursionError):
        return _research_selection_invalid(
            "RESEARCH_SELECTION_ARTIFACT_MALFORMED",
            observed_sha256=None,
            observed_size_bytes=None,
        )
    if type(parsed) is not dict:
        return _research_selection_invalid(
            "RESEARCH_SELECTION_ARTIFACT_MALFORMED",
            observed_sha256=None,
            observed_size_bytes=None,
        )

    observed_sha256 = hashlib.sha256(raw_bytes).hexdigest()
    observed_size_bytes = len(raw_bytes)

    state = parsed.get("state")
    if type(state) is not str or state not in _ALLOWED_ACTIONS_BY_STATE:
        return _research_selection_invalid(
            "RESEARCH_SELECTION_ARTIFACT_STATE_INVALID",
            observed_sha256=observed_sha256,
            observed_size_bytes=observed_size_bytes,
        )

    source = parsed.get("source")
    if type(source) is not str or source not in _RESEARCH_SELECTION_VALID_SOURCES:
        return _research_selection_invalid(
            "RESEARCH_SELECTION_ARTIFACT_SOURCE_INVALID",
            observed_sha256=observed_sha256,
            observed_size_bytes=observed_size_bytes,
        )

    h1_block_shape = _h1_mapped_block_shape(parsed)
    source_claims_h1 = source == H1_ROLE_MAPPED_SOURCE
    state_claims_h1 = state == H1_MAPPED_FRESH_NON_ACTIONABLE

    if h1_block_shape == "MALFORMED":
        return _research_selection_invalid(
            "RESEARCH_SELECTION_ARTIFACT_H1_BLOCK_MALFORMED",
            observed_sha256=observed_sha256,
            observed_size_bytes=observed_size_bytes,
        )

    if h1_block_shape == "COMPLETE_POSITIVE":
        if not (source_claims_h1 and state_claims_h1):
            return _research_selection_invalid(
                "RESEARCH_SELECTION_ARTIFACT_SOURCE_STATE_INCONSISTENT",
                observed_sha256=observed_sha256,
                observed_size_bytes=observed_size_bytes,
            )
        # Structurally valid positive claim: never surfaced as usable. No
        # currentness binding exists to authenticate it (see section header).
        return ResearchSelectionRefusalReadResult(
            schema_version=_RESEARCH_SELECTION_RESULT_SCHEMA_VERSION,
            status=ResearchSelectionRefusalReadStatus.UNAVAILABLE,
            reason_codes=("RESEARCH_SELECTION_POSITIVE_CURRENTNESS_UNAVAILABLE",),
            authority_effect=_RESEARCH_SELECTION_AUTHORITY_EFFECT_NONE,
            report_only=True,
            not_authorization=True,
            artifact_locator=_RESEARCH_SELECTION_ARTIFACT_LOCATOR,
            artifact_observed_sha256=observed_sha256,
            artifact_observed_size_bytes=observed_size_bytes,
            persisted_state=None,
            persisted_source=None,
        )

    # h1_block_shape == "ABSENT"
    if source_claims_h1 or state_claims_h1:
        return _research_selection_invalid(
            "RESEARCH_SELECTION_ARTIFACT_SOURCE_STATE_INCONSISTENT",
            observed_sha256=observed_sha256,
            observed_size_bytes=observed_size_bytes,
        )

    return ResearchSelectionRefusalReadResult(
        schema_version=_RESEARCH_SELECTION_RESULT_SCHEMA_VERSION,
        status=ResearchSelectionRefusalReadStatus.REFUSAL_ONLY,
        reason_codes=(),
        authority_effect=_RESEARCH_SELECTION_AUTHORITY_EFFECT_NONE,
        report_only=True,
        not_authorization=True,
        artifact_locator=_RESEARCH_SELECTION_ARTIFACT_LOCATOR,
        artifact_observed_sha256=observed_sha256,
        artifact_observed_size_bytes=observed_size_bytes,
        persisted_state=state,
        persisted_source=source,
    )
