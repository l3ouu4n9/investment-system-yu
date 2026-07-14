"""R2G-5c-0: report-only approval-registry switch READINESS gate (inert).

Emits ``approval_registry_switch_readiness.json`` — a deterministic go/no-go
artifact that decides whether a FUTURE R2G-5c-2 PR could safely switch
``evidence_packet.active_anchor_registry`` (what ``support_signals`` consumes)
from the baseline research_anchors-only registry to the approvals-inclusive
registry. **This PR only writes the artifact; nothing consumes it.**

The gate is a pure function of freshly-recompiled inputs (baseline registry,
approvals-inclusive registry, dual-read diff — all recomputed directly from the
current ``research_anchors.yaml`` / ``research_anchor_approvals.yaml`` bytes). It
NEVER reads ``research_anchor_approvals_validation.json`` and NEVER trusts the
R2G-5a ``would_activate`` flag as authority; approval validity is recomputed via
the R2G-5b compiler (which re-validates from YAML).

Three-way, fail-closed target (per the R2G-5c-design amendment):

* ``approvals_inclusive`` — every approvals-inclusive switch condition passes.
* ``baseline_fallback`` — approvals-inclusive is not switch-ready, but the
  baseline registry is independently fresh, source-hash-matched, ``registry_valid``
  and consumable, so falling back to baseline grounding is safe.
* ``fail_closed_empty`` — the baseline itself is stale / source-hash-mismatched /
  malformed / ``registry_valid: false`` / non-consumable, so a stale baseline must
  NOT be trusted; the only safe target is an empty (zero-anchor) registry.

It grants nothing: ``permission_effect: "none"``, ``not_authorization: true``, no
``NEW_BUY`` / ``ORDER_COMPILATION``, no order path. ``candidate_sha256`` /
``candidate_link_status`` are audit-only and never participate in any condition.
"""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import json
from typing import Any


SCHEMA_VERSION = "approval_registry_switch_readiness_v1"

BASELINE_REGISTRY_SCHEMA = "active_research_anchor_registry_v1"
APPROVALS_REGISTRY_SCHEMA = "active_research_anchor_registry_with_approvals_v1"
DUAL_READ_DIFF_SCHEMA = "approval_registry_dual_read_diff_v1"

APPROVALS_SOURCE_ID = "operator_research_anchor_approvals_yaml"
BASELINE_SOURCE_ID = "operator_research_anchors_yaml"
REVOCATIONS_SOURCE_ID = "operator_research_anchor_revocations_yaml"
WORKFLOW_APPROVAL_SOURCE_IDENTITY_MISMATCH = (
    "workflow_approval_source_identity_mismatch"
)

ALLOWED_APPROVAL_TYPES = ("operator_authored", "operator_approved_candidate")

SWITCH_TARGET_APPROVALS = "approvals_inclusive"
SWITCH_TARGET_BASELINE = "baseline_fallback"
SWITCH_TARGET_FAIL_CLOSED = "fail_closed_empty"

# Keys that would make an artifact carry order / budget / execution authority.
_ORDER_SHAPED_KEYS = frozenset(
    {
        "account", "quantity", "shares", "order_type", "tif", "time_in_force",
        "limit_price", "stop_price", "venue", "routing", "broker", "new_buy",
        "order_compilation", "budget", "allocation", "order", "orders",
        "order_intent", "order_sizing", "order_instruction", "execution_authorization",
    }
)
_GRANT_TOKENS = ("new_buy", "order_compilation")

_NOTES = (
    "Report-only R2G-5c-0 switch-readiness gate. Consumed by NOTHING: it does not switch "
    "support_signals, does not change evidence_packet.active_anchor_registry, and is read by no "
    "consumer. It only reports whether a FUTURE R2G-5c-2 switch to the approvals-inclusive "
    "registry would be safe. Approval validity + source hashes are recomputed directly from the "
    "current research_anchors.yaml / research_anchor_approvals.yaml bytes; the "
    "research_anchor_approvals_validation.json artifact and its would_activate flag are NEVER "
    "read as authority. operator_completed_anchor_sha256 is the only activation-binding hash; "
    "candidate_sha256 / candidate_link_status are audit-only and gate nothing. "
    "baseline_fallback is offered ONLY when the baseline registry is itself fresh, "
    "source-hash-matched, registry_valid and consumable; otherwise fail_closed_empty is the safe "
    "target (a stale baseline is never trusted). It never authorizes a trade and adds no NEW_BUY "
    "/ ORDER_COMPILATION (permission_effect=none, not_authorization=true)."
)


# --- condition helpers -------------------------------------------------------


class _Conditions:
    """Ordered, deterministic condition accumulator."""

    def __init__(self) -> None:
        self._items: list[dict[str, Any]] = []

    def add(self, cid: str, description: str, passed: bool, detail: str = "") -> bool:
        self._items.append(
            {"id": cid, "description": description, "passed": bool(passed), "detail": detail}
        )
        return bool(passed)

    def items(self) -> list[dict[str, Any]]:
        return self._items

    def passed(self, cid: str) -> bool:
        return all(c["passed"] for c in self._items if c["id"] == cid)


def evaluate_approval_registry_switch_readiness(
    *,
    baseline_registry: Any,
    approvals_registry: Any,
    dual_read_diff: Any,
    current_research_anchors_sha256: str | None,
    current_research_anchor_approvals_sha256: str | None,
    approvals_source_present: bool,
    as_of_date: str | None = None,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Evaluate switch readiness deterministically (pure; never raises)."""
    try:
        return _evaluate(
            baseline_registry=baseline_registry,
            approvals_registry=approvals_registry,
            dual_read_diff=dual_read_diff,
            current_research_anchors_sha256=current_research_anchors_sha256,
            current_research_anchor_approvals_sha256=current_research_anchor_approvals_sha256,
            approvals_source_present=approvals_source_present,
            as_of_date=as_of_date,
            generated_at=generated_at,
        )
    except Exception:  # noqa: BLE001 - report-only gate must never raise
        conditions = [
            {"id": "internal_error", "description": "readiness evaluator raised", "passed": False, "detail": ""}
        ]
        return _result(
            ready=False,
            switch_target=SWITCH_TARGET_FAIL_CLOSED,
            baseline_fallback_safe=False,
            fail_closed_empty_required=True,
            conditions=conditions,
            baseline_registry=baseline_registry if isinstance(baseline_registry, Mapping) else None,
            approvals_registry=approvals_registry if isinstance(approvals_registry, Mapping) else None,
            dual_read_diff=dual_read_diff if isinstance(dual_read_diff, Mapping) else None,
            as_of_date=as_of_date,
            generated_at=generated_at,
        )


def _evaluate(
    *,
    baseline_registry: Any,
    approvals_registry: Any,
    dual_read_diff: Any,
    current_research_anchors_sha256: str | None,
    current_research_anchor_approvals_sha256: str | None,
    approvals_source_present: bool,
    as_of_date: str | None,
    generated_at: str | None,
) -> dict[str, Any]:
    c = _Conditions()

    base = baseline_registry if isinstance(baseline_registry, Mapping) else None
    appr = approvals_registry if isinstance(approvals_registry, Mapping) else None
    diff = dual_read_diff if isinstance(dual_read_diff, Mapping) else None

    # --- baseline conditions (B*) -------------------------------------------
    b_present = c.add("baseline_registry_present", "baseline registry present", base is not None)
    b_schema = c.add(
        "baseline_registry_schema_valid",
        f"baseline schema == {BASELINE_REGISTRY_SCHEMA}",
        base is not None and base.get("schema_version") == BASELINE_REGISTRY_SCHEMA,
    )
    b_markers = c.add(
        "baseline_registry_markers_valid",
        "baseline report-only / not_authorization markers valid",
        _markers_ok(base),
    )
    b_srchash = c.add(
        "baseline_source_hash_matches_current_research_anchors_yaml",
        "baseline source_manifest sha256 matches current research_anchors.yaml",
        _source_hash_matches(base, BASELINE_SOURCE_ID, current_research_anchors_sha256),
        detail=_hash_detail(_source_sha(base, BASELINE_SOURCE_ID), current_research_anchors_sha256),
    )
    b_valid = c.add(
        "baseline_registry_valid_true",
        "baseline registry_valid is true",
        base is not None and base.get("registry_valid") is True,
    )
    baseline_ok = b_present and b_schema and b_markers and b_srchash and b_valid
    c.add("baseline_consumable", "baseline is a consumable report-only valid registry", baseline_ok)

    # --- approvals-inclusive conditions (A*) --------------------------------
    a_present = c.add("approvals_registry_present", "approvals-inclusive registry present", appr is not None)
    a_schema = c.add(
        "approvals_registry_schema_valid",
        f"approvals-inclusive schema == {APPROVALS_REGISTRY_SCHEMA}",
        appr is not None and appr.get("schema_version") == APPROVALS_REGISTRY_SCHEMA,
    )
    a_markers = c.add(
        "approvals_registry_markers_valid",
        "approvals-inclusive report-only / not_authorization markers valid",
        _markers_ok(appr),
    )
    a_not_embedded = c.add(
        "approvals_registry_is_embedded_registry_false",
        "approvals-inclusive declares is_embedded_registry:false",
        appr is not None and appr.get("is_embedded_registry") is False,
    )
    a_not_in_ep = c.add(
        "approvals_registry_embedded_in_evidence_packet_false",
        "approvals-inclusive declares embedded_in_evidence_packet:false",
        appr is not None and appr.get("embedded_in_evidence_packet") is False,
    )
    d_present = c.add("dual_read_diff_present", "dual-read diff present", diff is not None)
    d_schema_ok = diff is not None and diff.get("schema_version") == DUAL_READ_DIFF_SCHEMA
    baseline_sha = _sha256_of(base)
    approvals_sha = _sha256_of(appr)
    d_hashes = c.add(
        "dual_read_diff_hashes_match",
        "dual-read diff registry hashes match current baseline + approvals registries",
        bool(
            d_present
            and d_schema_ok
            and diff.get("baseline_registry_sha256") == baseline_sha
            and diff.get("approvals_registry_sha256") == approvals_sha
        ),
    )
    a_appr_srchash = c.add(
        "approvals_yaml_source_hash_matches_registry_manifest",
        "approvals YAML sha256 matches approvals-inclusive source_manifest",
        _approvals_source_hash_ok(appr, current_research_anchor_approvals_sha256, approvals_source_present),
        detail=_hash_detail(_source_sha(appr, APPROVALS_SOURCE_ID), current_research_anchor_approvals_sha256),
    )
    a_anchors_srchash = c.add(
        "research_anchors_yaml_source_hash_matches_approvals_registry_manifest",
        "research_anchors.yaml sha256 matches approvals-inclusive source_manifest",
        _source_hash_matches(appr, BASELINE_SOURCE_ID, current_research_anchors_sha256),
        detail=_hash_detail(_source_sha(appr, BASELINE_SOURCE_ID), current_research_anchors_sha256),
    )
    a_workflow_identity = c.add(
        WORKFLOW_APPROVAL_SOURCE_IDENTITY_MISMATCH,
        "approval validation, revocation validation, registry, and diff use one source identity",
        _workflow_approval_source_identity_consistent(
            approvals_registry=appr,
            dual_read_diff=diff,
            expected_sha256=current_research_anchor_approvals_sha256,
            expected_present=approvals_source_present,
        ),
    )
    # Structural attestation: readiness recomputes validation from YAML (via the
    # R2G-5b compiler) and never reads the R2G-5a validation artifact.
    c.add(
        "approval_validation_recomputed_from_yaml",
        "approval validation recomputed from current YAML (not read from validation artifact)",
        True,
    )
    dup_blockers = _as_list(appr.get("duplicate_blockers")) if appr is not None else []
    reg_blockers = _as_list(appr.get("registry_blockers")) if appr is not None else []
    a_no_dup = c.add("no_duplicate_blockers", "approvals-inclusive has no duplicate_blockers", not dup_blockers)
    a_no_reg_block = c.add("no_registry_blockers", "approvals-inclusive has no registry_blockers", not reg_blockers)
    a_reg_valid = c.add(
        "registry_valid_with_approvals_true",
        "approvals-inclusive registry_valid is true",
        appr is not None and appr.get("registry_valid") is True,
    )
    a_no_cross = c.add(
        "no_cross_source_duplicate_anchor_id",
        "no cross-source duplicate anchor_id",
        not any(
            isinstance(b, Mapping) and b.get("reason") == "duplicate_anchor_id_across_sources"
            for b in dup_blockers
        ),
    )

    # Per-row checks over ACTIVE approvals-derived rows.
    active_appr_rows = _active_approval_rows(appr)
    a_has_hash = c.add(
        "active_approvals_rows_have_operator_completed_anchor_sha256",
        "every active approvals-derived row has non-empty operator_completed_anchor_sha256",
        all(_nonempty(r.get("operator_completed_anchor_sha256")) for r in active_appr_rows),
    )
    a_hash_match = c.add(
        "active_approvals_rows_hash_match_true",
        "every active approvals-derived row has validation.hash_match:true",
        all(_row_hash_match(r) for r in active_appr_rows),
    )
    a_cat = c.add(
        "active_approvals_rows_source_category_c_operator",
        "every active approvals-derived row has source_category:'C_operator'",
        all(r.get("source_category") == "C_operator" for r in active_appr_rows),
    )
    a_stype = c.add(
        "active_approvals_rows_source_type_operator",
        "every active approvals-derived row has source_type:'operator'",
        all(r.get("source_type") == "operator" for r in active_appr_rows),
    )
    a_atype = c.add(
        "active_approvals_rows_allowed_approval_type",
        f"every active approvals-derived row has approval_type in {list(ALLOWED_APPROVAL_TYPES)}",
        all(r.get("approval_type") in ALLOWED_APPROVAL_TYPES for r in active_appr_rows),
    )
    # candidate audit-only structural invariants (never gating).
    c.add(
        "no_candidate_sha256_used_as_activation_authority",
        "candidate_sha256 participates in no activation condition (audit-only)",
        True,
    )
    c.add(
        "candidate_link_status_audit_only",
        "candidate_link_status is audit-only where present",
        True,
    )

    # Safety scans over the artifacts under consideration.
    scanned = [x for x in (base, appr, diff) if isinstance(x, Mapping)]
    a_no_grant = c.add(
        "no_new_buy_or_order_compilation_grant_tokens",
        "no NEW_BUY / ORDER_COMPILATION grant tokens in the registries or diff",
        not any(_has_grant_tokens(x) for x in scanned),
    )
    a_no_order = c.add(
        "no_order_shaped_fields",
        "no order-shaped fields in the registries or diff",
        not any(_has_order_shaped_keys(x) for x in scanned),
    )

    approvals_specific_ok = all(
        (
            a_present, a_schema, a_markers, a_not_embedded, a_not_in_ep, d_present, d_hashes,
            a_appr_srchash, a_anchors_srchash, a_workflow_identity,
            a_no_dup, a_no_reg_block, a_reg_valid, a_no_cross,
            a_has_hash, a_hash_match, a_cat, a_stype, a_atype, a_no_grant, a_no_order,
        )
    )

    ready = baseline_ok and approvals_specific_ok
    if ready:
        switch_target = SWITCH_TARGET_APPROVALS
        baseline_fallback_safe = True
        fail_closed_empty_required = False
    elif baseline_ok:
        switch_target = SWITCH_TARGET_BASELINE
        baseline_fallback_safe = True
        fail_closed_empty_required = False
    else:
        switch_target = SWITCH_TARGET_FAIL_CLOSED
        baseline_fallback_safe = False
        fail_closed_empty_required = True

    return _result(
        ready=ready,
        switch_target=switch_target,
        baseline_fallback_safe=baseline_fallback_safe,
        fail_closed_empty_required=fail_closed_empty_required,
        conditions=c.items(),
        baseline_registry=base,
        approvals_registry=appr,
        dual_read_diff=diff,
        as_of_date=as_of_date if isinstance(as_of_date, str) else (appr.get("as_of_date") if appr else None),
        generated_at=generated_at,
    )


# --- disk wrappers -----------------------------------------------------------


def build_approval_registry_switch_readiness(
    *,
    anchors_path: Any,
    approvals_path: Any,
    allowed_universe: Any,
    today: Any = None,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Recompute baseline + approvals-inclusive + diff fresh from YAML, then evaluate.

    Recomputes everything directly from the current input bytes (never reads the
    R2G-5a approval validation artifact or the R2G-5d revocation validation
    artifact). Revocations are derived from the same approvals YAML bytes as the
    approvals overlay, so readiness evaluates the same revocation-aware registry
    that the embedded evidence-packet selector can safely consume. Never raises.
    """
    from investment_orchestrator.research.approvals_inclusive_active_registry import (
        capture_research_anchor_approval_source,
    )

    return build_approval_registry_switch_readiness_from_captured_source(
        anchors_path=anchors_path,
        approval_source=capture_research_anchor_approval_source(approvals_path),
        allowed_universe=allowed_universe,
        today=today,
        generated_at=generated_at,
    )


def build_approval_registry_switch_readiness_from_captured_source(
    *,
    anchors_path: Any,
    approval_source: Any,
    allowed_universe: Any,
    today: Any = None,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Evaluate readiness from one caller-owned immutable approval snapshot."""
    from investment_orchestrator.research.approvals_inclusive_active_registry import (
        _sanitize_captured_source,
    )

    return _build_approval_registry_switch_readiness_from_sanitized_source(
        anchors_path=anchors_path,
        approval_source=_sanitize_captured_source(approval_source),
        allowed_universe=allowed_universe,
        today=today,
        generated_at=generated_at,
    )


def _build_approval_registry_switch_readiness_from_sanitized_source(
    *,
    anchors_path: Any,
    approval_source: Any,
    allowed_universe: Any,
    today: Any = None,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Evaluate readiness from one already-sanitized workflow snapshot."""
    from investment_orchestrator.research.active_research_anchor_registry import (
        compile_active_research_anchor_registry,
    )
    from investment_orchestrator.research.approvals_inclusive_active_registry import (
        _build_from_sanitized_source,
        _verified_approval_source_sha256,
        _verified_approval_source_validation_present,
    )
    from investment_orchestrator.research.approval_registry_dual_read_diff import (
        _build_approval_registry_dual_read_diff_from_sanitized_source,
    )

    try:
        baseline = compile_active_research_anchor_registry(
            anchors_path=anchors_path, allowed_universe=allowed_universe, today=today
        )
        approvals = _build_from_sanitized_source(
            baseline=baseline,
            approval_source=approval_source,
            allowed_universe=allowed_universe,
            today=today,
            generated_at=None,
            candidate_index=None,
        )
        diff = _build_approval_registry_dual_read_diff_from_sanitized_source(
            baseline_registry=baseline,
            approvals_registry=approvals,
            approval_source=approval_source,
        )
        return evaluate_approval_registry_switch_readiness(
            baseline_registry=baseline,
            approvals_registry=approvals,
            dual_read_diff=diff,
            current_research_anchors_sha256=_source_sha(baseline, BASELINE_SOURCE_ID),
            current_research_anchor_approvals_sha256=(
                _verified_approval_source_sha256(approval_source)
            ),
            approvals_source_present=(
                _verified_approval_source_validation_present(approval_source)
            ),
            as_of_date=baseline.get("as_of_date") if isinstance(baseline, Mapping) else None,
            generated_at=generated_at,
        )
    except Exception:  # noqa: BLE001 - report-only: never break the reporting flow
        return evaluate_approval_registry_switch_readiness(
            baseline_registry=None,
            approvals_registry=None,
            dual_read_diff=None,
            current_research_anchors_sha256=None,
            current_research_anchor_approvals_sha256=None,
            approvals_source_present=False,
            generated_at=generated_at,
        )


def write_approval_registry_switch_readiness(
    *,
    output_path: Any,
    anchors_path: Any,
    approvals_path: Any,
    allowed_universe: Any,
    today: Any = None,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Compute + write the report-only readiness artifact; return a small summary."""
    from investment_orchestrator.common.io import write_json

    payload = build_approval_registry_switch_readiness(
        anchors_path=anchors_path,
        approvals_path=approvals_path,
        allowed_universe=allowed_universe,
        today=today,
        generated_at=generated_at,
    )
    write_json(output_path, payload)
    return {
        "approval_registry_switch_readiness_path": str(output_path),
        "ready": str(payload["ready"]),
        "switch_target": str(payload["switch_target"]),
    }


# --- result assembly ---------------------------------------------------------


def _result(
    *,
    ready: bool,
    switch_target: str,
    baseline_fallback_safe: bool,
    fail_closed_empty_required: bool,
    conditions: list[dict[str, Any]],
    baseline_registry: Mapping[str, Any] | None,
    approvals_registry: Mapping[str, Any] | None,
    dual_read_diff: Mapping[str, Any] | None,
    as_of_date: str | None,
    generated_at: str | None,
) -> dict[str, Any]:
    failed = [c["id"] for c in conditions if not c["passed"]]
    return {
        "schema_version": SCHEMA_VERSION,
        "is_llm_generated": False,
        "report_only": True,
        "permission_effect": "none",
        "not_authorization": True,
        "not_execution_authorization": True,
        "generated_at": generated_at,
        "as_of_date": as_of_date,
        "ready": ready,
        "switch_target": switch_target,
        "baseline_fallback_safe": baseline_fallback_safe,
        "fail_closed_empty_required": fail_closed_empty_required,
        "conditions": conditions,
        "failed_conditions": failed,
        "baseline_registry_sha256": _sha256_of(baseline_registry),
        "approvals_registry_sha256": _sha256_of(approvals_registry),
        "dual_read_diff_sha256": _sha256_of(dual_read_diff),
        "source_hashes": {
            "research_anchors_yaml": {
                "baseline_source_manifest": _source_sha(baseline_registry, BASELINE_SOURCE_ID),
                "approvals_source_manifest": _source_sha(approvals_registry, BASELINE_SOURCE_ID),
            },
            "research_anchor_approvals_yaml": {
                "approvals_source_manifest": _source_sha(approvals_registry, APPROVALS_SOURCE_ID),
            },
        },
        "registry_valid_baseline": baseline_registry.get("registry_valid") is True
        if isinstance(baseline_registry, Mapping)
        else False,
        "registry_valid_with_approvals": approvals_registry.get("registry_valid") is True
        if isinstance(approvals_registry, Mapping)
        else False,
        "duplicate_blockers": _as_list(approvals_registry.get("duplicate_blockers"))
        if isinstance(approvals_registry, Mapping)
        else [],
        "registry_blockers": _as_list(approvals_registry.get("registry_blockers"))
        if isinstance(approvals_registry, Mapping)
        else [],
        "added_by_approvals": _as_list(dual_read_diff.get("added_by_approvals"))
        if isinstance(dual_read_diff, Mapping)
        else [],
        "cannot_affect_allowed_actions": True,
        "consumed_by_support_signals": False,
        "consumed_by_active_registry": False,
        "consumed_by_availability": False,
        "consumed_by_gates": False,
        "consumed_by_step2": False,
        "consumed_by_step4": False,
        "notes": _NOTES,
    }


# --- predicates --------------------------------------------------------------


def _markers_ok(registry: Mapping[str, Any] | None) -> bool:
    return (
        isinstance(registry, Mapping)
        and registry.get("is_llm_generated") is False
        and registry.get("report_only") is True
        and registry.get("permission_effect") == "none"
        and registry.get("not_authorization") is True
        and registry.get("not_execution_authorization") is True
    )


def _source_sha(registry: Mapping[str, Any] | None, source_id: str) -> str | None:
    if not isinstance(registry, Mapping):
        return None
    for entry in _as_list(registry.get("source_manifest")):
        if isinstance(entry, Mapping) and entry.get("source_id") == source_id:
            sha = entry.get("sha256")
            return sha if isinstance(sha, str) else None
    return None


def _source_present(registry: Mapping[str, Any] | None, source_id: str) -> bool | None:
    if not isinstance(registry, Mapping):
        return None
    for entry in _as_list(registry.get("source_manifest")):
        if isinstance(entry, Mapping) and entry.get("source_id") == source_id:
            value = entry.get("present")
            return value if type(value) is bool else None
    return None


def _workflow_approval_source_identity_consistent(
    *,
    approvals_registry: Mapping[str, Any] | None,
    dual_read_diff: Mapping[str, Any] | None,
    expected_sha256: str | None,
    expected_present: bool,
) -> bool:
    """Require every activation-bearing combined-source identity to join."""
    if not isinstance(approvals_registry, Mapping) or not isinstance(
        dual_read_diff, Mapping
    ):
        return False
    approval_present = _source_present(approvals_registry, APPROVALS_SOURCE_ID)
    revocation_present = _source_present(
        approvals_registry, REVOCATIONS_SOURCE_ID
    )
    approval_sha = _source_sha(approvals_registry, APPROVALS_SOURCE_ID)
    revocation_sha = _source_sha(approvals_registry, REVOCATIONS_SOURCE_ID)
    diff_sha = dual_read_diff.get("approval_source_sha256")
    normalized_diff_sha = diff_sha if isinstance(diff_sha, str) else None
    return bool(
        type(expected_present) is bool
        and approval_present is expected_present
        and revocation_present is expected_present
        and approval_sha == expected_sha256
        and revocation_sha == expected_sha256
        and normalized_diff_sha == expected_sha256
        and WORKFLOW_APPROVAL_SOURCE_IDENTITY_MISMATCH
        not in _as_list(dual_read_diff.get("blockers"))
    )


def _source_hash_matches(registry: Mapping[str, Any] | None, source_id: str, current_sha: str | None) -> bool:
    recorded = _source_sha(registry, source_id)
    return recorded is not None and current_sha is not None and recorded == current_sha


def _approvals_source_hash_ok(
    registry: Mapping[str, Any] | None, current_sha: str | None, approvals_present: bool
) -> bool:
    recorded = _source_sha(registry, APPROVALS_SOURCE_ID)
    if not approvals_present:
        # Benign absence: neither the manifest nor the current input carries a hash.
        return recorded is None and current_sha is None
    return recorded is not None and current_sha is not None and recorded == current_sha


def _active_approval_rows(registry: Mapping[str, Any] | None) -> list[Mapping[str, Any]]:
    if not isinstance(registry, Mapping):
        return []
    rows: list[Mapping[str, Any]] = []
    for row in _as_list(registry.get("active_anchors")):
        if isinstance(row, Mapping) and row.get("source_id") == APPROVALS_SOURCE_ID:
            rows.append(row)
    return rows


def _row_hash_match(row: Mapping[str, Any]) -> bool:
    validation = row.get("validation")
    return isinstance(validation, Mapping) and validation.get("hash_match") is True


def _has_order_shaped_keys(obj: Any) -> bool:
    for key in _iter_keys(obj):
        if isinstance(key, str) and key.strip().lower() in _ORDER_SHAPED_KEYS:
            return True
    return False


def _has_grant_tokens(obj: Any) -> bool:
    for value in _iter_string_values(obj):
        if value.strip().lower() in _GRANT_TOKENS:
            return True
    return False


# --- helpers -----------------------------------------------------------------


def _nonempty(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _hash_detail(recorded: str | None, current: str | None) -> str:
    r = recorded[:12] + ".." if isinstance(recorded, str) else "None"
    cur = current[:12] + ".." if isinstance(current, str) else "None"
    return f"recorded={r} current={cur}"


def _iter_keys(obj: Any):
    if isinstance(obj, Mapping):
        for key, value in obj.items():
            yield key
            yield from _iter_keys(value)
    elif isinstance(obj, list):
        for item in obj:
            yield from _iter_keys(item)


def _iter_string_values(obj: Any):
    if isinstance(obj, Mapping):
        for value in obj.values():
            yield from _iter_string_values(value)
    elif isinstance(obj, list):
        for item in obj:
            yield from _iter_string_values(item)
    elif isinstance(obj, str):
        yield obj


def _sha256_of(value: Any) -> str | None:
    if value is None:
        return None
    try:
        serialized = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError):
        return None
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()
