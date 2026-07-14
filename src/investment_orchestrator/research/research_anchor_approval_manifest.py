"""R2G-5a: report-only operator-approval MANIFEST validator (inert, audit-only).

Validates the operator-authored approval manifest
(``inputs/current/research_anchor_approvals.yaml``) into a strictly report-only
``research_anchor_approvals_validation.json`` artifact. It answers one question
per approval: *would this operator-completed anchor be eligible for a FUTURE
R2G-5b registry compiler to activate?* — as a **diagnostic only**. It activates
nothing.

Design (R2G-5-design, accepted):

* ``operator_completed_anchor_sha256`` is the **activation-binding hash**: the
  declared hash must exactly match the hash recomputed over the
  ``operator_completed_anchor`` object, or the approval is rejected (fail-closed).
* ``candidate_sha256`` / ``candidate_id`` are **audit-only** provenance links to
  an R2G-4 candidate skeleton — they have **zero activation authority**. A
  candidate mismatch (when a candidate index is supplied) is a *warning* on
  ``candidate_link_status`` only; it never changes ``hash_match`` or
  ``would_activate``.
* R2G-5a does **not** activate anchors, does **not** modify the active registry,
  ``support_signals``, or any consumer, and does **not** add revocations (deferred
  to R2G-5d). ``would_activate: true`` is purely a forward-looking diagnostic.

The operator-completed anchor is validated by REUSING the existing anchor
validator (``research_anchors.validate_research_anchors``) over a single-anchor
wrapper payload — the validator is never loosened and no new ``source_type`` is
added, so an approved anchor's intrinsic ``source_type`` must remain
``"operator"``. Every rule is enforced deterministically; malformed / missing
input fails closed and never raises.

Consumed by NOTHING: not ``support_signals``, not the active registry, not the
compiler, not the actionable preview / candidate / promotion eligibility, not
availability, not gates, not Step 2/3/4, not the final gate, not weekly, not
broker/live. It grants no ``NEW_BUY`` / ``ORDER_COMPILATION`` and cannot affect
``allowed_actions``.
"""

from __future__ import annotations

from collections.abc import Mapping
from collections import Counter
import hashlib
import json
from pathlib import Path
from typing import Any

import yaml

from investment_orchestrator.research.research_anchors import (
    normalize_iso_date_value,
    validate_research_anchor_approval_entry,
)


# Manifest (input) schema and the validation (output) artifact schema.
MANIFEST_SCHEMA_VERSION = "research_anchor_approvals_v1"
VALIDATION_SCHEMA_VERSION = "research_anchor_approvals_validation_v1"

# Closed input schemas.  The three audit-only approval fields at the end of the
# approval allowlist are documented operator annotations; none participates in
# activation.  ``revocations`` is part of the combined source document and is
# validated independently by research_anchor_revocation_manifest.
MANIFEST_ALLOWED_FIELDS = frozenset(
    {"schema_version", "is_llm_generated", "as_of_date", "approvals", "revocations"}
)
APPROVAL_ALLOWED_FIELDS = frozenset(
    {
        "approval_id",
        "decision",
        "candidate_id",
        "candidate_sha256",
        "operator_completed_anchor",
        "operator_completed_anchor_sha256",
        "operator_note",
        "approved_by",
        "approved_at",
    }
)
OPERATOR_COMPLETED_ANCHOR_ALLOWED_FIELDS = frozenset(
    {
        "anchor_id",
        "anchor_type",
        "applicable_tickers",
        "anchor_date_et",
        "valid_from",
        "valid_until",
        "source_type",
        "confidence_floor",
        "summary",
        "source_note",
        "blocks_if_stale",
    }
)

MANIFEST_UNKNOWN_FIELD = "research_anchor_approval_manifest_unknown_field"
APPROVAL_UNKNOWN_FIELD = "research_anchor_approval_entry_unknown_field"
OPERATOR_COMPLETED_ANCHOR_UNKNOWN_FIELD = (
    "research_anchor_operator_completed_anchor_unknown_field"
)
YAML_ANCHOR_NOT_ALLOWED = "research_anchor_yaml_anchor_not_allowed"
YAML_ALIAS_NOT_ALLOWED = "research_anchor_yaml_alias_not_allowed"
YAML_MERGE_NOT_ALLOWED = "research_anchor_yaml_merge_not_allowed"
_YAML_MERGE_TAG = "tag:yaml.org,2002:merge"

# The single activation-binding hash field. candidate_sha256 is audit-only.
ACTIVATION_BINDING_HASH_FIELD = "operator_completed_anchor_sha256"
CANDIDATE_HASH_ROLE = "audit_only"

# Only ``approve`` is supported in R2G-5a. Revocations / rejections are deferred
# to R2G-5d (do not add here).
SUPPORTED_DECISIONS = ("approve",)
DECISION_APPROVE = "approve"

# Per-approval terminal status.
STATUS_VALID_REPORT_ONLY = "valid_report_only"
STATUS_EXPIRED = "expired"
STATUS_REJECTED = "rejected"

# candidate_link_status values (all audit-only; never affect activation).
CANDIDATE_LINK_NONE = "no_candidate_link"
CANDIDATE_LINK_REFERENCED = "candidate_referenced"
CANDIDATE_LINK_VERIFIED = "candidate_verified"
CANDIDATE_LINK_NOT_FOUND = "candidate_not_found"
CANDIDATE_LINK_HASH_MISMATCH = "candidate_hash_mismatch"

# Benign: a missing manifest is the normal "no approvals yet" state, not an error.
SOURCE_MISSING_WARNING = (
    "research_anchor_approvals_missing (no inputs/current/research_anchor_approvals.yaml; "
    "report-only, no approvals to validate)."
)

_NOTES = (
    "Report-only operator-approval manifest validation (R2G-5a). Consumed by NOTHING: "
    "not support_signals, not the active_research_anchor_registry, not the compiler, not "
    "the actionable preview / candidate / promotion eligibility, not availability, not "
    "gates, not Step 2/3/4, not the final gate, not weekly, not broker/live. "
    "operator_completed_anchor_sha256 is the activation-binding hash; candidate_sha256 is "
    "audit-only with ZERO activation authority. would_activate=true is a forward-looking "
    "diagnostic for a FUTURE R2G-5b registry compiler ONLY — it activates nothing here. "
    "This artifact never authorizes a trade, adds no NEW_BUY / ORDER_COMPILATION, and "
    "cannot affect allowed_actions (permission_effect=none, not_authorization=true)."
)


class ResearchAnchorApprovalYamlPolicyError(yaml.YAMLError):
    """Bounded source-language rejection raised before YAML construction."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class _UniqueKeySafeLoader(yaml.SafeLoader):
    """Safe combined-manifest loader with deterministic duplicate rejection."""


def _consume_yaml_event_node(
    events: list[yaml.events.Event],
    index: int,
) -> tuple[int, bool]:
    """Consume one event-stream node and report whether it contains a merge key."""
    event = events[index]
    if isinstance(event, (yaml.events.ScalarEvent, yaml.events.AliasEvent)):
        return index + 1, False
    if isinstance(event, yaml.events.SequenceStartEvent):
        index += 1
        merge_found = False
        while not isinstance(events[index], yaml.events.SequenceEndEvent):
            index, child_merge = _consume_yaml_event_node(events, index)
            merge_found = merge_found or child_merge
        return index + 1, merge_found
    if isinstance(event, yaml.events.MappingStartEvent):
        index += 1
        merge_found = False
        while not isinstance(events[index], yaml.events.MappingEndEvent):
            key_event = events[index]
            if isinstance(key_event, yaml.events.ScalarEvent) and (
                key_event.value == "<<" or key_event.tag == _YAML_MERGE_TAG
            ):
                merge_found = True
            index, key_merge = _consume_yaml_event_node(events, index)
            index, value_merge = _consume_yaml_event_node(events, index)
            merge_found = merge_found or key_merge or value_merge
        return index + 1, merge_found
    raise yaml.YAMLError("unexpected YAML event while validating source policy")


def _validate_research_anchor_yaml_source_policy(source_text: str) -> None:
    """Reject merge syntax and all YAML graph references before construction."""
    events = list(yaml.parse(source_text, Loader=yaml.SafeLoader))
    merge_found = False
    index = 0
    while index < len(events):
        if isinstance(events[index], yaml.events.DocumentStartEvent):
            index += 1
            if index < len(events) and not isinstance(
                events[index], yaml.events.DocumentEndEvent
            ):
                index, document_merge = _consume_yaml_event_node(events, index)
                merge_found = merge_found or document_merge
            continue
        index += 1

    if merge_found:
        raise ResearchAnchorApprovalYamlPolicyError(YAML_MERGE_NOT_ALLOWED)
    if any(isinstance(event, yaml.events.AliasEvent) for event in events):
        raise ResearchAnchorApprovalYamlPolicyError(YAML_ALIAS_NOT_ALLOWED)
    if any(
        not isinstance(event, yaml.events.AliasEvent)
        and getattr(event, "anchor", None) is not None
        for event in events
    ):
        raise ResearchAnchorApprovalYamlPolicyError(YAML_ANCHOR_NOT_ALLOWED)


def _construct_unique_mapping(
    loader: _UniqueKeySafeLoader, node: yaml.nodes.MappingNode, deep: bool = False
) -> dict[Any, Any]:
    loader.flatten_mapping(node)
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            duplicate = key in mapping
        except TypeError as exc:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                "found an unhashable key",
                key_node.start_mark,
            ) from exc
        if duplicate:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                "found a duplicate key",
                key_node.start_mark,
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_UniqueKeySafeLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


def load_research_anchor_approval_yaml(source_text: str) -> Any:
    """Parse the closed combined source language with no graph/merge features."""
    _validate_research_anchor_yaml_source_policy(source_text)
    return yaml.load(source_text, Loader=_UniqueKeySafeLoader)


def compute_operator_completed_anchor_sha256(operator_completed_anchor: Any) -> str | None:
    """Deterministic activation-binding hash of an operator-completed anchor.

    Canonical JSON (sorted keys, compact, UTF-8) — identical scheme to the R2G-4
    ``candidate_sha256`` so an operator can recompute it offline. ``None`` for a
    non-serializable value (never raises).
    """
    return _sha256_of(operator_completed_anchor)


def build_research_anchor_approvals_validation(
    *,
    manifest: Any,
    source_present: bool,
    source_sha256: str | None,
    source_path: str | None,
    allowed_universe: Any,
    today: Any = None,
    as_of_date: str | None = None,
    generated_at: str | None = None,
    candidate_index: Mapping[str, Any] | None = None,
    parse_error: str | None = None,
) -> dict[str, Any]:
    """Validate a decoded approval manifest into the report-only artifact (never raises)."""
    try:
        return _build(
            manifest=manifest,
            source_present=source_present,
            source_sha256=source_sha256,
            source_path=source_path,
            allowed_universe=allowed_universe,
            today=today,
            as_of_date=as_of_date,
            generated_at=generated_at,
            candidate_index=candidate_index,
            parse_error=parse_error,
        )
    except Exception:  # noqa: BLE001 - report-only validator must never raise
        return _result(
            source_present=source_present,
            source_valid=False,
            source_path=source_path,
            source_sha256=source_sha256,
            manifest_errors=["approvals_validator_internal_error"],
            manifest_warnings=[],
            approval_results=[],
            as_of_date=as_of_date,
            generated_at=generated_at,
        )


def _build(
    *,
    manifest: Any,
    source_present: bool,
    source_sha256: str | None,
    source_path: str | None,
    allowed_universe: Any,
    today: Any,
    as_of_date: str | None,
    generated_at: str | None,
    candidate_index: Mapping[str, Any] | None,
    parse_error: str | None,
) -> dict[str, Any]:
    # --- missing manifest: benign, valid report with no approvals ----------------
    if not source_present:
        return _result(
            source_present=False,
            source_valid=False,
            source_path=source_path,
            source_sha256=None,
            manifest_errors=[],
            manifest_warnings=[SOURCE_MISSING_WARNING],
            approval_results=[],
            as_of_date=normalize_iso_date_value(today) or as_of_date,
            generated_at=generated_at,
        )

    # --- malformed YAML: fail closed --------------------------------------------
    if parse_error is not None:
        return _result(
            source_present=True,
            source_valid=False,
            source_path=source_path,
            source_sha256=source_sha256,
            manifest_errors=[f"malformed_yaml: {parse_error}"],
            manifest_warnings=[],
            approval_results=[],
            as_of_date=normalize_iso_date_value(today) or as_of_date,
            generated_at=generated_at,
        )

    manifest_errors: list[str] = []
    manifest_warnings: list[str] = []

    if not isinstance(manifest, Mapping):
        return _result(
            source_present=True,
            source_valid=False,
            source_path=source_path,
            source_sha256=source_sha256,
            manifest_errors=["research_anchor_approvals top-level must be a mapping/object."],
            manifest_warnings=[],
            approval_results=[],
            as_of_date=normalize_iso_date_value(today) or as_of_date,
            generated_at=generated_at,
        )

    if any(key not in MANIFEST_ALLOWED_FIELDS for key in manifest):
        manifest_errors.append(MANIFEST_UNKNOWN_FIELD)

    schema_version = manifest.get("schema_version")
    if schema_version != MANIFEST_SCHEMA_VERSION:
        manifest_errors.append(
            f"schema_version must be {MANIFEST_SCHEMA_VERSION!r} (got {schema_version!r})."
        )
    if manifest.get("is_llm_generated") is not False:
        manifest_errors.append(
            "is_llm_generated must be exactly false (approvals are operator-authored)."
        )

    resolved_as_of = (
        normalize_iso_date_value(manifest.get("as_of_date"))
        or normalize_iso_date_value(today)
        or as_of_date
    )

    approvals_value = manifest.get("approvals")
    if approvals_value is None:
        approvals_value = []
    if not isinstance(approvals_value, list):
        manifest_errors.append("approvals must be a list.")
        return _result(
            source_present=True,
            source_valid=False,
            source_path=source_path,
            source_sha256=source_sha256,
            manifest_errors=manifest_errors,
            manifest_warnings=manifest_warnings,
            approval_results=[],
            as_of_date=resolved_as_of,
            generated_at=generated_at,
        )

    for approval in approvals_value:
        if not isinstance(approval, Mapping):
            continue
        if any(key not in APPROVAL_ALLOWED_FIELDS for key in approval):
            if APPROVAL_UNKNOWN_FIELD not in manifest_errors:
                manifest_errors.append(APPROVAL_UNKNOWN_FIELD)
        completed = approval.get("operator_completed_anchor")
        if isinstance(completed, Mapping) and any(
            key not in OPERATOR_COMPLETED_ANCHOR_ALLOWED_FIELDS for key in completed
        ):
            if OPERATOR_COMPLETED_ANCHOR_UNKNOWN_FIELD not in manifest_errors:
                manifest_errors.append(OPERATOR_COMPLETED_ANCHOR_UNKNOWN_FIELD)

    # Duplicate detection across the whole manifest (fail-closed diagnostics).
    duplicate_approval_ids = _duplicate_strings(
        _string_of(a.get("approval_id")) for a in approvals_value if isinstance(a, Mapping)
    )
    duplicate_anchor_ids = _duplicate_strings(
        _completed_anchor_id(a) for a in approvals_value if isinstance(a, Mapping)
    )
    if duplicate_approval_ids:
        manifest_errors.append(
            f"duplicate approval_id(s) in manifest: {sorted(duplicate_approval_ids)} (fail closed)."
        )
    if duplicate_anchor_ids:
        manifest_errors.append(
            f"duplicate operator_completed_anchor anchor_id(s) in manifest: "
            f"{sorted(duplicate_anchor_ids)} (fail closed)."
        )

    # source_valid gates activation eligibility: any manifest-level integrity
    # failure forces every approval to would_activate=false (fail-closed).
    source_valid = not manifest_errors

    universe = allowed_universe
    approval_results = [
        _evaluate_approval(
            approval,
            index=index,
            universe=universe,
            today=today,
            candidate_index=candidate_index,
            duplicate_approval_ids=duplicate_approval_ids,
            duplicate_anchor_ids=duplicate_anchor_ids,
            manifest_valid=source_valid,
        )
        for index, approval in enumerate(approvals_value)
    ]

    return _result(
        source_present=True,
        source_valid=source_valid,
        source_path=source_path,
        source_sha256=source_sha256,
        manifest_errors=manifest_errors,
        manifest_warnings=manifest_warnings,
        approval_results=approval_results,
        as_of_date=resolved_as_of,
        generated_at=generated_at,
    )


def _evaluate_approval(
    approval: Any,
    *,
    index: int,
    universe: Any,
    today: Any,
    candidate_index: Mapping[str, Any] | None,
    duplicate_approval_ids: set[str],
    duplicate_anchor_ids: set[str],
    manifest_valid: bool,
) -> dict[str, Any]:
    """Evaluate a single approval deterministically. Report dict (never raises)."""
    approval_errors: list[str] = []
    approval_warnings: list[str] = []

    if not isinstance(approval, Mapping):
        return _approval_result(
            approval_id=None,
            decision=None,
            candidate_id=None,
            candidate_sha256=None,
            candidate_link_status=CANDIDATE_LINK_NONE,
            declared_hash=None,
            recomputed_hash=None,
            hash_match=False,
            validation_valid=False,
            validation_usable=False,
            validation_stale=False,
            normalized_anchor_preview=None,
            approval_errors=[f"approvals[{index}] must be an object."],
            approval_warnings=[],
            would_activate=False,
            status=STATUS_REJECTED,
        )

    if any(key not in APPROVAL_ALLOWED_FIELDS for key in approval):
        approval_errors.append(APPROVAL_UNKNOWN_FIELD)

    approval_id = _string_of(approval.get("approval_id"))
    if approval_id is None:
        approval_errors.append("approval_id is required (non-empty string).")

    decision = approval.get("decision")
    if decision not in SUPPORTED_DECISIONS:
        approval_errors.append(
            f"decision must be one of {list(SUPPORTED_DECISIONS)} in R2G-5a "
            f"(got {decision!r}; revocations/rejections are deferred to R2G-5d)."
        )

    # Audit-only candidate provenance link — never activation authority.
    candidate_id = approval.get("candidate_id")
    candidate_sha256 = approval.get("candidate_sha256")
    candidate_link_status = _candidate_link_status(
        candidate_id, candidate_sha256, candidate_index, approval_warnings
    )

    completed = approval.get("operator_completed_anchor")
    declared_hash = approval.get(ACTIVATION_BINDING_HASH_FIELD)

    recomputed_hash: str | None = None
    hash_match = False
    validation_valid = False
    validation_usable = False
    validation_stale = False
    normalized_preview: dict[str, Any] | None = None

    if decision == DECISION_APPROVE:
        if not isinstance(completed, Mapping):
            approval_errors.append("operator_completed_anchor is required for an approve decision.")
        else:
            if any(
                key not in OPERATOR_COMPLETED_ANCHOR_ALLOWED_FIELDS for key in completed
            ):
                approval_errors.append(OPERATOR_COMPLETED_ANCHOR_UNKNOWN_FIELD)
            recomputed_hash = _sha256_of(completed)
            declared = _string_of(declared_hash)
            if declared is None:
                approval_errors.append(
                    f"{ACTIVATION_BINDING_HASH_FIELD} is required (the activation-binding hash)."
                )
            else:
                hash_match = recomputed_hash is not None and recomputed_hash == declared
                if not hash_match:
                    approval_errors.append(
                        f"{ACTIVATION_BINDING_HASH_FIELD} mismatch: recomputed hash differs from "
                        "the declared hash (fail closed)."
                    )

            # Reuse the existing anchor validator (never loosened) over a single
            # wrapper payload — enforces forbidden keys/tokens, anchor_type,
            # source_type=operator, confidence_floor, in-universe, ISO dates,
            # valid_from<=valid_until, and stale/missing-date handling.
            validation_valid, validation_usable, validation_stale, v_problems, normalized_preview = (
                _validate_completed_anchor(completed, allowed_universe=universe, today=today)
            )
            for problem in v_problems:
                approval_errors.append(f"anchor_validation: {problem}")

    if approval_id is not None and approval_id in duplicate_approval_ids:
        approval_errors.append("duplicate approval_id across the manifest (fail closed).")
    completed_anchor_id = _completed_anchor_id(approval)
    if completed_anchor_id is not None and completed_anchor_id in duplicate_anchor_ids:
        approval_errors.append(
            "duplicate operator_completed_anchor anchor_id across the manifest (fail closed)."
        )

    would_activate = bool(
        manifest_valid
        and decision == DECISION_APPROVE
        and hash_match
        and validation_valid
        and validation_usable
        and not validation_stale
        and not approval_errors
    )

    status = _status_of(
        would_activate=would_activate,
        manifest_valid=manifest_valid,
        decision=decision,
        hash_match=hash_match,
        validation_valid=validation_valid,
        validation_stale=validation_stale,
        approval_errors=approval_errors,
    )

    return _approval_result(
        approval_id=approval_id,
        decision=decision if isinstance(decision, str) else None,
        candidate_id=candidate_id if isinstance(candidate_id, str) else None,
        candidate_sha256=candidate_sha256 if isinstance(candidate_sha256, str) else None,
        candidate_link_status=candidate_link_status,
        declared_hash=declared_hash if isinstance(declared_hash, str) else None,
        recomputed_hash=recomputed_hash,
        hash_match=hash_match,
        validation_valid=validation_valid,
        validation_usable=validation_usable,
        validation_stale=validation_stale,
        normalized_anchor_preview=normalized_preview,
        approval_errors=approval_errors,
        approval_warnings=approval_warnings,
        would_activate=would_activate,
        status=status,
    )


# --- anchor validation (reuse existing validator; never loosened) ------------


def _validate_completed_anchor(
    completed: Mapping[str, Any], *, allowed_universe: Any, today: Any
) -> tuple[bool, bool, bool, list[str], dict[str, Any] | None]:
    """Run ``validate_research_anchors`` over a single-anchor wrapper payload.

    Returns ``(valid, usable, stale, problems, normalized_preview)``. The wrapper
    is exactly the ``research_anchors_v1`` shape so file-level rules (forbidden
    keys/tokens, is_llm_generated, schema) apply as well as per-anchor rules.
    """
    payload = {
        "schema_version": "research_anchors_v1",
        "is_llm_generated": False,
        "anchors": [dict(completed)],
    }
    result = validate_research_anchor_approval_entry(
        payload,
        allowed_universe=allowed_universe,
        today=today,
    )
    evaluated = result.anchors[0] if result.anchors else None
    file_errors = list(result.errors)
    if evaluated is None:
        return False, False, False, (file_errors or ["no_anchor_evaluated"]), None
    valid = bool(evaluated.get("valid")) and not file_errors
    usable = bool(evaluated.get("usable")) and not file_errors
    stale = bool(evaluated.get("stale"))
    problems = list(evaluated.get("problems") or []) + file_errors
    return valid, usable, stale, problems, _safe_preview(evaluated)


def _safe_preview(evaluated: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """A safe, citation-only projection of the normalized anchor (no order fields)."""
    if not isinstance(evaluated, Mapping):
        return None
    return {
        "anchor_id": evaluated.get("anchor_id"),
        "anchor_type": evaluated.get("anchor_type"),
        "applicable_tickers": list(evaluated.get("applicable_tickers") or []),
        "anchor_date_et": evaluated.get("anchor_date_et"),
        "valid_from": evaluated.get("valid_from"),
        "valid_until": evaluated.get("valid_until"),
        "source_type": evaluated.get("source_type"),
        "confidence_floor": evaluated.get("confidence_floor"),
        "summary": evaluated.get("summary"),
    }


# --- candidate audit link (no activation authority) --------------------------


def _candidate_link_status(
    candidate_id: Any,
    candidate_sha256: Any,
    candidate_index: Mapping[str, Any] | None,
    warnings: list[str],
) -> str:
    """Classify the audit-only candidate provenance link.

    A mismatch / not-found is a WARNING only (recorded on candidate_link_status);
    it never affects ``hash_match`` or ``would_activate``. The activation binding
    is exclusively ``operator_completed_anchor_sha256``.
    """
    cid = _string_of(candidate_id)
    csha = _string_of(candidate_sha256)
    if cid is None and csha is None:
        return CANDIDATE_LINK_NONE
    if not isinstance(candidate_index, Mapping):
        # Reference recorded for audit but not verified against a candidate source.
        return CANDIDATE_LINK_REFERENCED
    if cid is None or cid not in candidate_index:
        warnings.append(
            "candidate_id not found in candidate index (audit-only; no activation effect)."
        )
        return CANDIDATE_LINK_NOT_FOUND
    known = _string_of(candidate_index.get(cid))
    if csha is not None and known is not None and csha == known:
        return CANDIDATE_LINK_VERIFIED
    warnings.append(
        "candidate_sha256 does not match the candidate index (audit-only; no activation effect)."
    )
    return CANDIDATE_LINK_HASH_MISMATCH


# --- status + result assembly ------------------------------------------------


def _status_of(
    *,
    would_activate: bool,
    manifest_valid: bool,
    decision: Any,
    hash_match: bool,
    validation_valid: bool,
    validation_stale: bool,
    approval_errors: list[str],
) -> str:
    if would_activate:
        return STATUS_VALID_REPORT_ONLY
    # Expired = the ONLY thing keeping it from activating is staleness (a fresh
    # copy of the same anchor would activate). Requires an otherwise-clean approve.
    if (
        manifest_valid
        and decision == DECISION_APPROVE
        and hash_match
        and validation_valid
        and validation_stale
        and not approval_errors
    ):
        return STATUS_EXPIRED
    return STATUS_REJECTED


def _approval_result(
    *,
    approval_id: str | None,
    decision: str | None,
    candidate_id: str | None,
    candidate_sha256: str | None,
    candidate_link_status: str,
    declared_hash: str | None,
    recomputed_hash: str | None,
    hash_match: bool,
    validation_valid: bool,
    validation_usable: bool,
    validation_stale: bool,
    normalized_anchor_preview: dict[str, Any] | None,
    approval_errors: list[str],
    approval_warnings: list[str],
    would_activate: bool,
    status: str,
) -> dict[str, Any]:
    return {
        "approval_id": approval_id,
        "decision": decision,
        # Forward-looking diagnostic ONLY — activates nothing in R2G-5a.
        "would_activate": would_activate,
        "activation_binding_hash_field": ACTIVATION_BINDING_HASH_FIELD,
        "candidate_hash_role": CANDIDATE_HASH_ROLE,
        "candidate_id": candidate_id,
        "candidate_sha256": candidate_sha256,
        "candidate_link_status": candidate_link_status,
        "operator_completed_anchor_sha256": declared_hash,
        "recomputed_operator_completed_anchor_sha256": recomputed_hash,
        "hash_match": hash_match,
        "validation_valid": validation_valid,
        "validation_usable": validation_usable,
        "validation_stale": validation_stale,
        "approval_errors": approval_errors,
        "approval_warnings": approval_warnings,
        "normalized_anchor_preview": normalized_anchor_preview,
        "status": status,
    }


def _result(
    *,
    source_present: bool,
    source_valid: bool,
    source_path: str | None,
    source_sha256: str | None,
    manifest_errors: list[str],
    manifest_warnings: list[str],
    approval_results: list[dict[str, Any]],
    as_of_date: str | None,
    generated_at: str | None,
) -> dict[str, Any]:
    counts = {
        "approvals": len(approval_results),
        "would_activate": sum(1 for a in approval_results if a.get("would_activate")),
        "valid_report_only": sum(
            1 for a in approval_results if a.get("status") == STATUS_VALID_REPORT_ONLY
        ),
        "expired": sum(1 for a in approval_results if a.get("status") == STATUS_EXPIRED),
        "rejected": sum(1 for a in approval_results if a.get("status") == STATUS_REJECTED),
    }
    return {
        "schema_version": VALIDATION_SCHEMA_VERSION,
        "is_llm_generated": False,
        "report_only": True,
        "permission_effect": "none",
        "not_authorization": True,
        "not_execution_authorization": True,
        "generated_at": generated_at,
        "as_of_date": as_of_date,
        "source_path": source_path,
        "source_sha256": source_sha256,
        "source_present": source_present,
        "source_valid": source_valid,
        "manifest_errors": manifest_errors,
        "manifest_warnings": manifest_warnings,
        "approval_results": approval_results,
        "counts": counts,
        "consumed_by_support_signals": False,
        "consumed_by_active_registry": False,
        "consumed_by_compiler": False,
        "consumed_by_promotion_eligibility": False,
        "consumed_by_availability": False,
        "consumed_by_gates": False,
        "consumed_by_step2": False,
        "consumed_by_step4": False,
        "cannot_affect_allowed_actions": True,
        "notes": _NOTES,
    }


# --- disk load + write -------------------------------------------------------


def validate_research_anchor_approvals(
    *,
    manifest_path: Any,
    allowed_universe: Any,
    today: Any = None,
    as_of_date: str | None = None,
    generated_at: str | None = None,
    candidate_index: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Read + validate the approval manifest YAML from disk (never raises)."""
    source_present, source_text, source_sha256, parse_error = (
        read_research_anchor_approval_source(manifest_path)
    )

    manifest: Any = None
    if source_present and parse_error is None and source_text is not None:
        try:
            manifest = load_research_anchor_approval_yaml(source_text)
        except ResearchAnchorApprovalYamlPolicyError as exc:
            parse_error = exc.reason
        except yaml.constructor.ConstructorError:
            parse_error = "approval_source_yaml_duplicate_key"
        except yaml.YAMLError:
            parse_error = "approval_source_yaml_invalid"

    return build_research_anchor_approvals_validation(
        manifest=manifest,
        source_present=source_present,
        source_sha256=source_sha256,
        source_path=str(manifest_path) if manifest_path is not None else None,
        allowed_universe=allowed_universe,
        today=today,
        as_of_date=as_of_date,
        generated_at=generated_at,
        candidate_index=candidate_index,
        parse_error=parse_error,
    )


def write_research_anchor_approvals_validation(
    *,
    output_path: Any,
    manifest_path: Any,
    allowed_universe: Any,
    today: Any = None,
    as_of_date: str | None = None,
    generated_at: str | None = None,
    candidate_index: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate + write the report-only artifact; return a small summary."""
    from investment_orchestrator.common.io import write_json

    payload = validate_research_anchor_approvals(
        manifest_path=manifest_path,
        allowed_universe=allowed_universe,
        today=today,
        as_of_date=as_of_date,
        generated_at=generated_at,
        candidate_index=candidate_index,
    )
    write_json(output_path, payload)
    return {
        "research_anchor_approvals_validation_path": str(output_path),
        "source_valid": str(payload["source_valid"]),
        "approval_count": str(payload["counts"]["approvals"]),
        "would_activate_count": str(payload["counts"]["would_activate"]),
    }


# --- helpers -----------------------------------------------------------------


def _completed_anchor_id(approval: Mapping[str, Any]) -> str | None:
    completed = approval.get("operator_completed_anchor")
    if isinstance(completed, Mapping):
        return _string_of(completed.get("anchor_id"))
    return None


def _duplicate_strings(values: Any) -> set[str]:
    counts = Counter(v for v in values if isinstance(v, str) and v)
    return {value for value, count in counts.items() if count > 1}


def _string_of(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _sha256_of(value: Any) -> str | None:
    if value is None:
        return None
    try:
        serialized = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError):
        return None
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def read_research_anchor_approval_source(
    path: Any,
) -> tuple[bool, str | None, str | None, str | None]:
    """Read one path once; blank is present and non-absence failures are errors."""
    if path is None:
        return False, None, None, None
    try:
        source_text = Path(path).read_text(encoding="utf-8")
    except FileNotFoundError:
        return False, None, None, None
    except UnicodeDecodeError:
        return True, None, None, "approval_source_utf8_decode_error"
    except OSError:
        return True, None, None, "approval_source_read_error"
    source_bytes = source_text.encode("utf-8")
    source_sha256 = hashlib.sha256(source_bytes).hexdigest()
    return True, source_text, source_sha256, None
