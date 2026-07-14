"""R2G-5d-0: report-only operator-REVOCATION manifest validator (inert, audit-only).

Validates the optional ``revocations:`` section of the operator-authored
``inputs/current/research_anchor_approvals.yaml`` and writes a strictly
report-only ``research_anchor_revocations_validation.json`` artifact. It answers
one question per revocation: *does this revocation deterministically bind to
exactly one operator-approved anchor?* — as a **diagnostic only**.

R2G-5d-0/R2G-5d-1 scope (validation artifact remains report-only):

* The on-disk validation artifact **does not apply** any revocation. R2G-5d-1's
  standalone approvals-inclusive registry report can derive this validation
  in-memory from the same YAML, but it still never reads this JSON artifact as
  authority. The artifact does not change ``support_signals``, the embedded
  ``evidence_packet`` registry selection, or readiness. It is consumed by NOTHING.
* Only ``target_type: "approval_anchor"`` is supported. Baseline
  ``research_anchors.yaml`` revocation, revoke-by-``anchor_id``-alone,
  source_id-only, and candidate-based revocation are all rejected.

Binding model (deterministic, precision-first):

* A revocation must bind to exactly one approval using **all three** of
  ``approval_id`` + ``anchor_id`` + ``operator_completed_anchor_sha256``; they
  must all resolve to the same approval. ``operator_completed_anchor_sha256`` is
  the activation-binding hash; ``candidate_sha256`` is audit-only and can NEVER
  participate in binding (it is not even an allowed revocation field).

Mandatory R2G-5d-0 amendment — **``revocation_target_not_found`` FAILS CLOSED**:
an unknown target is a hard rejection (never warn/no-op), because a typo would
otherwise silently under-revoke and leave a stale approval-derived anchor
groundable. Every unresolved / inconsistent / hash-mismatched revocation is
rejected and marked ``would_fail_overlay_closed: true`` for the future overlay.

``reason`` is required but **non-authoritative** — it is never parsed for logic.
A revocation may never add anchors, budgets, allocations, actions, orders,
permissions, or execution authority. It grants nothing: ``permission_effect:
"none"``, ``not_authorization: true``, no ``NEW_BUY`` / ``ORDER_COMPILATION``.
"""

from __future__ import annotations

from collections.abc import Mapping
from collections import Counter
import json
from typing import Any

import yaml

from investment_orchestrator.research.research_anchors import (
    FORBIDDEN_ACTION_VALUE_TOKENS,
    FORBIDDEN_KEY_SUBSTRINGS,
    FORBIDDEN_KEYS,
    normalize_iso_date_value,
)
from investment_orchestrator.research.research_anchor_approval_manifest import (
    MANIFEST_SCHEMA_VERSION as APPROVALS_MANIFEST_SCHEMA_VERSION,
    ResearchAnchorApprovalYamlPolicyError,
    build_research_anchor_approvals_validation,
    load_research_anchor_approval_yaml,
    read_research_anchor_approval_source,
)


VALIDATION_SCHEMA_VERSION = "research_anchor_revocations_validation_v1"

TARGET_TYPE_APPROVAL_ANCHOR = "approval_anchor"
SUPPORTED_TARGET_TYPES = (TARGET_TYPE_APPROVAL_ANCHOR,)

# A revocation entry may contain EXACTLY these keys — nothing else.
ALLOWED_REVOCATION_KEYS = frozenset(
    {
        "revocation_id",
        "target_type",
        "approval_id",
        "anchor_id",
        "operator_completed_anchor_sha256",
        "effective_as_of",
        "reason",
        "revoked_by",
    }
)
REQUIRED_REVOCATION_FIELDS = (
    "revocation_id",
    "target_type",
    "approval_id",
    "anchor_id",
    "operator_completed_anchor_sha256",
    "effective_as_of",
    "reason",
    "revoked_by",
)

# Anchor-defining keys that must never appear in a revocation (it cannot add/modify
# an anchor). candidate_* are also disallowed (candidate can never bind a revocation).
ANCHOR_DEFINING_FIELDS = frozenset(
    {
        "operator_completed_anchor",
        "anchor_type",
        "applicable_tickers",
        "anchor_date_et",
        "valid_from",
        "valid_until",
        "source_type",
        "source_id",
        "source_category",
        "confidence_floor",
        "summary",
        "blocks_if_stale",
        "decision",
        "candidate_id",
        "candidate_sha256",
        "candidate_link_status",
    }
)

# Per-revocation status.
STATUS_VALID_ACTIVE = "valid_active"
STATUS_VALID_PENDING_FUTURE = "valid_pending_future"
STATUS_REJECTED = "rejected"

# Target-binding status.
BIND_RESOLVED = "resolved"
BIND_TARGET_NOT_FOUND = "target_not_found"
BIND_INCONSISTENT = "inconsistent"
BIND_HASH_MISMATCH = "hash_mismatch"
BIND_UNRESOLVED = "unresolved"

# Effective-date classification.
EFFECTIVE_ACTIVE = "active"
EFFECTIVE_PENDING_FUTURE = "pending_future"
EFFECTIVE_UNKNOWN_DATE = "unknown_date"

# Manifest-level structural blockers (fail the whole revocation set closed).
BLOCKER_MALFORMED_YAML = "revocations_malformed_yaml"
BLOCKER_TOP_LEVEL_NOT_MAPPING = "revocations_top_level_not_mapping"
BLOCKER_WRONG_SCHEMA = "revocations_wrong_schema_version"
BLOCKER_IS_LLM_GENERATED = "revocations_is_llm_generated_true"
BLOCKER_NOT_A_LIST = "revocations_not_a_list"
BLOCKER_DUPLICATE_ID = "duplicate_revocation_id"
BLOCKER_APPROVALS_SOURCE_INVALID = "approvals_source_invalid_cannot_bind_revocations"

SOURCE_MISSING_WARNING = (
    "research_anchor_approvals_missing (no inputs/current/research_anchor_approvals.yaml; "
    "report-only, no revocations to validate)."
)

_NOTES = (
    "Report-only R2G-5d-0 revocation validation. Validates the optional revocations: section of "
    "research_anchor_approvals.yaml. The on-disk JSON artifact DOES NOT apply revocations and is "
    "consumed by NOTHING; R2G-5d-1's standalone approvals-inclusive registry report may derive this "
    "same validation in-memory from YAML, but it never reads the JSON artifact as authority. The "
    "artifact does not change support_signals, the embedded evidence_packet registry selection, or "
    "readiness. Only target_type='approval_anchor' is "
    "supported; binding requires approval_id + anchor_id + operator_completed_anchor_sha256 all "
    "resolving to one approval. operator_completed_anchor_sha256 is the only binding hash; "
    "candidate_sha256 is audit-only and cannot bind a revocation. Per the R2G-5d-0 amendment, an "
    "unknown target FAILS CLOSED (never warn/no-op). reason is required but non-authoritative. It "
    "never authorizes a trade and adds no NEW_BUY / ORDER_COMPILATION (permission_effect=none, "
    "not_authorization=true)."
)


def build_research_anchor_revocations_validation(
    *,
    manifest: Any,
    approvals_validation: Mapping[str, Any] | None,
    source_present: bool,
    source_sha256: str | None,
    source_path: str | None,
    today: Any = None,
    as_of_date: str | None = None,
    generated_at: str | None = None,
    parse_error: str | None = None,
) -> dict[str, Any]:
    """Validate a decoded manifest's revocations section (pure; never raises)."""
    try:
        return _build(
            manifest=manifest,
            approvals_validation=approvals_validation,
            source_present=source_present,
            source_sha256=source_sha256,
            source_path=source_path,
            today=today,
            as_of_date=as_of_date,
            generated_at=generated_at,
            parse_error=parse_error,
        )
    except Exception:  # noqa: BLE001 - report-only validator must never raise
        return _result(
            source_present=source_present,
            source_valid=False,
            revocations_valid=False,
            source_path=source_path,
            source_sha256=source_sha256,
            blockers=["revocations_validator_internal_error"],
            warnings=[],
            revocation_results=[],
            as_of_date=as_of_date,
            generated_at=generated_at,
        )


def _build(
    *,
    manifest: Any,
    approvals_validation: Mapping[str, Any] | None,
    source_present: bool,
    source_sha256: str | None,
    source_path: str | None,
    today: Any,
    as_of_date: str | None,
    generated_at: str | None,
    parse_error: str | None,
) -> dict[str, Any]:
    resolved_as_of = normalize_iso_date_value(as_of_date) or normalize_iso_date_value(today)

    # --- missing manifest: benign, valid empty report (revocations are optional) ---
    if not source_present:
        return _result(
            source_present=False,
            source_valid=True,
            revocations_valid=True,
            source_path=source_path,
            source_sha256=None,
            blockers=[],
            warnings=[SOURCE_MISSING_WARNING],
            revocation_results=[],
            as_of_date=resolved_as_of,
            generated_at=generated_at,
        )

    if parse_error is not None:
        return _result(
            source_present=True, source_valid=False, revocations_valid=False,
            source_path=source_path, source_sha256=source_sha256,
            blockers=[f"{BLOCKER_MALFORMED_YAML}: {parse_error}"], warnings=[],
            revocation_results=[], as_of_date=resolved_as_of, generated_at=generated_at,
        )

    if not isinstance(manifest, Mapping):
        return _result(
            source_present=True, source_valid=False, revocations_valid=False,
            source_path=source_path, source_sha256=source_sha256,
            blockers=[BLOCKER_TOP_LEVEL_NOT_MAPPING], warnings=[],
            revocation_results=[], as_of_date=resolved_as_of, generated_at=generated_at,
        )

    blockers: list[str] = []
    warnings: list[str] = []

    if manifest.get("schema_version") != APPROVALS_MANIFEST_SCHEMA_VERSION:
        blockers.append(BLOCKER_WRONG_SCHEMA)
    if manifest.get("is_llm_generated") is not False:
        blockers.append(BLOCKER_IS_LLM_GENERATED)

    resolved_as_of = (
        normalize_iso_date_value(manifest.get("as_of_date")) or resolved_as_of
    )

    revocations_value = manifest.get("revocations")
    if revocations_value is None:
        # Optional section absent -> benign, no revocations.
        return _result(
            source_present=True, source_valid=not blockers, revocations_valid=not blockers,
            source_path=source_path, source_sha256=source_sha256,
            blockers=blockers, warnings=warnings, revocation_results=[],
            as_of_date=resolved_as_of, generated_at=generated_at,
        )
    if not isinstance(revocations_value, list):
        blockers.append(BLOCKER_NOT_A_LIST)
        return _result(
            source_present=True, source_valid=False, revocations_valid=False,
            source_path=source_path, source_sha256=source_sha256,
            blockers=blockers, warnings=warnings, revocation_results=[],
            as_of_date=resolved_as_of, generated_at=generated_at,
        )

    # Duplicate revocation_id -> structural fail closed.
    duplicate_ids = _duplicate_strings(
        _string_of(r.get("revocation_id")) for r in revocations_value if isinstance(r, Mapping)
    )
    if duplicate_ids:
        blockers.append(f"{BLOCKER_DUPLICATE_ID}: {sorted(duplicate_ids)}")

    # Binding requires trustworthy approvals. If the approvals source is structurally
    # invalid, revocation binding cannot be trusted -> fail closed.
    approvals_source_valid = (
        isinstance(approvals_validation, Mapping)
        and approvals_validation.get("source_valid") is True
    )
    approval_index = _approval_index(approvals_validation)
    if not approvals_source_valid and revocations_value:
        blockers.append(BLOCKER_APPROVALS_SOURCE_INVALID)

    structural_ok = not blockers

    revocation_results = [
        _evaluate_revocation(
            revocation,
            index=index,
            approval_index=approval_index,
            approvals_source_valid=approvals_source_valid,
            duplicate_ids=duplicate_ids,
            as_of_date=resolved_as_of,
            structural_ok=structural_ok,
        )
        for index, revocation in enumerate(revocations_value)
    ]

    any_rejected = any(r["status"] == STATUS_REJECTED for r in revocation_results)
    source_valid = structural_ok
    revocations_valid = source_valid and not any_rejected

    return _result(
        source_present=True,
        source_valid=source_valid,
        revocations_valid=revocations_valid,
        source_path=source_path,
        source_sha256=source_sha256,
        blockers=blockers,
        warnings=warnings,
        revocation_results=revocation_results,
        as_of_date=resolved_as_of,
        generated_at=generated_at,
    )


def _evaluate_revocation(
    revocation: Any,
    *,
    index: int,
    approval_index: Mapping[str, Mapping[str, Any]],
    approvals_source_valid: bool,
    duplicate_ids: set[str],
    as_of_date: str | None,
    structural_ok: bool,
) -> dict[str, Any]:
    errors: list[str] = []

    if not isinstance(revocation, Mapping):
        return _revocation_result(
            revocation_id=None, target_type=None, approval_id=None, anchor_id=None,
            declared_hash=None, effective_as_of=None, effective_classification=EFFECTIVE_UNKNOWN_DATE,
            binding_status=BIND_UNRESOLVED, errors=[f"revocations[{index}] must be an object."],
            status=STATUS_REJECTED, reason=None, revoked_by=None, audit={},
        )

    # Allow-list: any key outside the allowed set is a hard error (anchor-defining /
    # candidate / order / budget / execution fields can never appear).
    for raw_key in revocation.keys():
        if not isinstance(raw_key, str):
            errors.append(f"non-string key present: {raw_key!r}.")
            continue
        key = raw_key.strip()
        low = key.lower()
        if key in ALLOWED_REVOCATION_KEYS:
            continue
        if low in {k.lower() for k in FORBIDDEN_KEYS} or any(s in low for s in FORBIDDEN_KEY_SUBSTRINGS):
            errors.append(f"forbidden budget/order/action/execution key present: {raw_key!r}.")
        elif low in ANCHOR_DEFINING_FIELDS:
            errors.append(f"anchor-defining/candidate field forbidden in a revocation: {raw_key!r}.")
        else:
            errors.append(f"disallowed field in revocation: {raw_key!r}.")

    # Forbidden action-token scalar values anywhere in the entry.
    for value in _iter_string_values(revocation):
        if value.strip().lower() in FORBIDDEN_ACTION_VALUE_TOKENS:
            errors.append(f"forbidden authoritative action token used as a value: {value!r}.")

    # Required fields.
    for field in REQUIRED_REVOCATION_FIELDS:
        if not _present(revocation.get(field)):
            errors.append(f"missing required field: {field}.")

    revocation_id = _string_of(revocation.get("revocation_id"))
    target_type = revocation.get("target_type")
    approval_id = _string_of(revocation.get("approval_id"))
    anchor_id = _string_of(revocation.get("anchor_id"))
    declared_hash = _string_of(revocation.get("operator_completed_anchor_sha256"))
    reason = _string_of(revocation.get("reason"))
    revoked_by = _string_of(revocation.get("revoked_by"))

    if target_type is not None and target_type not in SUPPORTED_TARGET_TYPES:
        errors.append(
            f"unsupported target_type {target_type!r} (only {list(SUPPORTED_TARGET_TYPES)} supported)."
        )

    effective_raw = revocation.get("effective_as_of")
    effective_iso = normalize_iso_date_value(effective_raw)
    if _present(effective_raw) and effective_iso is None:
        errors.append(f"effective_as_of must be an ISO date (YYYY-MM-DD); got {effective_raw!r}.")

    if revocation_id is not None and revocation_id in duplicate_ids:
        errors.append("duplicate revocation_id across the manifest (fail closed).")

    # --- deterministic triple binding ------------------------------------------
    binding_status = BIND_UNRESOLVED
    audit: dict[str, Any] = {
        "target_approval_found": False,
        "target_approval_anchor_id": None,
        "target_approval_declared_sha256": None,
        "target_approval_hash_match": None,
        "reason_present": _present(revocation.get("reason")),
        "revoked_by": revoked_by,
        "candidate_fields_ignored": True,
    }

    if target_type == TARGET_TYPE_APPROVAL_ANCHOR and approval_id is not None:
        approval = approval_index.get(approval_id)
        if approval is None:
            # MANDATORY AMENDMENT: unknown target fails closed (never warn/no-op).
            binding_status = BIND_TARGET_NOT_FOUND
            errors.append("revocation_target_not_found: no approval matches approval_id (fail closed).")
        else:
            audit["target_approval_found"] = True
            audit["target_approval_anchor_id"] = approval.get("anchor_id")
            audit["target_approval_declared_sha256"] = approval.get("declared_sha256")
            audit["target_approval_hash_match"] = approval.get("hash_match")
            anchor_ok = anchor_id is not None and approval.get("anchor_id") == anchor_id
            hash_ok = declared_hash is not None and approval.get("declared_sha256") == declared_hash
            if anchor_ok and hash_ok:
                binding_status = BIND_RESOLVED
            elif not hash_ok and anchor_ok:
                binding_status = BIND_HASH_MISMATCH
                errors.append(
                    "operator_completed_anchor_sha256 does not match the target approval (fail closed)."
                )
            else:
                binding_status = BIND_INCONSISTENT
                errors.append(
                    "approval_id / anchor_id / operator_completed_anchor_sha256 triple is inconsistent "
                    "(fail closed)."
                )

    # Effective-date classification (report-only; NEVER applies anything).
    if effective_iso is None:
        effective_classification = EFFECTIVE_UNKNOWN_DATE
    elif as_of_date is not None and effective_iso > as_of_date:
        effective_classification = EFFECTIVE_PENDING_FUTURE
    else:
        effective_classification = EFFECTIVE_ACTIVE

    resolved_clean = binding_status == BIND_RESOLVED and not errors and structural_ok
    if not resolved_clean:
        status = STATUS_REJECTED
    elif effective_classification == EFFECTIVE_PENDING_FUTURE:
        status = STATUS_VALID_PENDING_FUTURE
    else:
        status = STATUS_VALID_ACTIVE

    return _revocation_result(
        revocation_id=revocation_id,
        target_type=target_type if isinstance(target_type, str) else None,
        approval_id=approval_id,
        anchor_id=anchor_id,
        declared_hash=declared_hash,
        effective_as_of=effective_iso,
        effective_classification=effective_classification,
        binding_status=binding_status,
        errors=errors,
        status=status,
        reason=reason,
        revoked_by=revoked_by,
        audit=audit,
    )


# --- disk wrappers -----------------------------------------------------------


def validate_research_anchor_revocations(
    *,
    manifest_path: Any,
    allowed_universe: Any,
    today: Any = None,
    as_of_date: str | None = None,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Read the shared approvals YAML once, then validate its revocations (never raises)."""
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

    approvals_validation = build_research_anchor_approvals_validation(
        manifest=manifest,
        source_present=source_present,
        source_sha256=source_sha256,
        source_path=str(manifest_path) if manifest_path is not None else None,
        allowed_universe=allowed_universe,
        today=today,
        as_of_date=as_of_date,
        parse_error=parse_error,
    )
    return build_research_anchor_revocations_validation(
        manifest=manifest,
        approvals_validation=approvals_validation,
        source_present=source_present,
        source_sha256=source_sha256,
        source_path=str(manifest_path) if manifest_path is not None else None,
        today=today,
        as_of_date=as_of_date,
        generated_at=generated_at,
        parse_error=parse_error,
    )


def write_research_anchor_revocations_validation(
    *,
    output_path: Any,
    manifest_path: Any,
    allowed_universe: Any,
    today: Any = None,
    as_of_date: str | None = None,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Validate + write the report-only revocation validation artifact; small summary."""
    from investment_orchestrator.common.io import write_json

    payload = validate_research_anchor_revocations(
        manifest_path=manifest_path,
        allowed_universe=allowed_universe,
        today=today,
        as_of_date=as_of_date,
        generated_at=generated_at,
    )
    write_json(output_path, payload)
    return {
        "research_anchor_revocations_validation_path": str(output_path),
        "revocations_valid": str(payload["revocations_valid"]),
        "revocation_count": str(payload["counts"]["checked"]),
    }


# --- result assembly ---------------------------------------------------------


def _revocation_result(
    *,
    revocation_id: str | None,
    target_type: str | None,
    approval_id: str | None,
    anchor_id: str | None,
    declared_hash: str | None,
    effective_as_of: str | None,
    effective_classification: str,
    binding_status: str,
    errors: list[str],
    status: str,
    reason: str | None,
    revoked_by: str | None,
    audit: dict[str, Any],
) -> dict[str, Any]:
    return {
        "revocation_id": revocation_id,
        "target_type": target_type,
        "approval_id": approval_id,
        "anchor_id": anchor_id,
        "operator_completed_anchor_sha256": declared_hash,
        "binding_hash_field": "operator_completed_anchor_sha256",
        "candidate_sha256_used_for_binding": False,
        "target_binding_status": binding_status,
        "effective_as_of": effective_as_of,
        "effective_classification": effective_classification,
        "status": status,
        "reason": reason,
        "revoked_by": revoked_by,
        # R2G-5d-0 is validation-only: NOTHING is applied.
        "applied": False,
        "would_fail_overlay_closed": status == STATUS_REJECTED,
        "errors": errors,
        "audit": audit,
    }


def _result(
    *,
    source_present: bool,
    source_valid: bool,
    revocations_valid: bool,
    source_path: str | None,
    source_sha256: str | None,
    blockers: list[str],
    warnings: list[str],
    revocation_results: list[dict[str, Any]],
    as_of_date: str | None,
    generated_at: str | None,
) -> dict[str, Any]:
    counts = {
        "checked": len(revocation_results),
        "valid": sum(
            1 for r in revocation_results if r["status"] in (STATUS_VALID_ACTIVE, STATUS_VALID_PENDING_FUTURE)
        ),
        "valid_active": sum(1 for r in revocation_results if r["status"] == STATUS_VALID_ACTIVE),
        "pending_future": sum(
            1 for r in revocation_results if r["status"] == STATUS_VALID_PENDING_FUTURE
        ),
        "invalid": sum(1 for r in revocation_results if r["status"] == STATUS_REJECTED),
    }
    return {
        "schema_version": VALIDATION_SCHEMA_VERSION,
        "is_llm_generated": False,
        "report_only": True,
        "permission_effect": "none",
        "not_authorization": True,
        "not_execution_authorization": True,
        # Explicit: this validator applies nothing and changes no runtime grounding.
        "applies_revocations": False,
        "not_applied_report_only": True,
        "does_not_change_runtime_grounding": True,
        "generated_at": generated_at,
        "as_of_date": as_of_date,
        "source_path": source_path,
        "source_sha256": source_sha256,
        "source_present": source_present,
        "source_valid": source_valid,
        "revocations_valid": revocations_valid,
        "revocation_results": revocation_results,
        "blockers": blockers,
        "warnings": warnings,
        "counts": counts,
        "candidate_sha256_used_for_binding": False,
        "consumed_by_support_signals": False,
        "consumed_by_active_registry": False,
        "consumed_by_approvals_inclusive_registry": False,
        "consumed_by_readiness": False,
        "consumed_by_compiler": False,
        "consumed_by_availability": False,
        "consumed_by_gates": False,
        "consumed_by_step2": False,
        "consumed_by_step4": False,
        "cannot_affect_allowed_actions": True,
        "notes": _NOTES,
    }


# --- helpers -----------------------------------------------------------------


def _approval_index(approvals_validation: Mapping[str, Any] | None) -> dict[str, dict[str, Any]]:
    """Index approvals by approval_id for deterministic target resolution."""
    out: dict[str, dict[str, Any]] = {}
    if not isinstance(approvals_validation, Mapping):
        return out
    for ar in _as_list(approvals_validation.get("approval_results")):
        if not isinstance(ar, Mapping):
            continue
        approval_id = _string_of(ar.get("approval_id"))
        if approval_id is None:
            continue
        preview = ar.get("normalized_anchor_preview")
        anchor_id = preview.get("anchor_id") if isinstance(preview, Mapping) else None
        out[approval_id] = {
            "anchor_id": anchor_id if isinstance(anchor_id, str) else None,
            "declared_sha256": ar.get("operator_completed_anchor_sha256")
            if isinstance(ar.get("operator_completed_anchor_sha256"), str)
            else None,
            "recomputed_sha256": ar.get("recomputed_operator_completed_anchor_sha256"),
            "hash_match": ar.get("hash_match"),
            "approval_status": ar.get("status"),
        }
    return out


def _duplicate_strings(values: Any) -> set[str]:
    counts = Counter(v for v in values if isinstance(v, str) and v)
    return {value for value, count in counts.items() if count > 1}


def _present(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip() != ""
    return True


def _string_of(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _iter_string_values(obj: Any):
    if isinstance(obj, Mapping):
        for value in obj.values():
            yield from _iter_string_values(value)
    elif isinstance(obj, list):
        for item in obj:
            yield from _iter_string_values(item)
    elif isinstance(obj, str):
        yield obj
