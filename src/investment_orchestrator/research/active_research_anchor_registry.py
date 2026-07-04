"""R2G-1: deterministic active research-anchor registry compiler (report-only).

Compiles the existing, already-validated operator-authored research anchors
(``inputs/current/research_anchors.yaml``) into a single deterministic
``active_research_anchor_registry.json``. This is the R2G-design "active
registry" artifact, built here from the **operator YAML source only** — no
candidates, no approval manifest, no auto-generated or LLM-sourced anchors.

Strictly report-only and additive:

* It **wraps** ``research_anchors.load_research_anchors`` / ``validate_research_anchors``
  (it does not re-implement or loosen any anchor validation).
* NOTHING consumes this artifact in R2G-1: not ``support_signals``, not the
  compiler, not the actionable preview / candidate / promotion eligibility, not
  availability, not Step 2/3/4, not the final gate, not weekly. It is emitted
  purely so a later PR (R2G-2 dual-read dry-run) can prove equivalence before
  any consumer is switched.
* It grants nothing: ``permission_effect: "none"``, ``not_authorization: true``,
  no ``NEW_BUY`` / ``ORDER_COMPILATION``, no order path.

Fail-closed rules (never raises in the reporting flow):

* missing / empty source file  -> ``registry_valid: true``, zero active anchors,
  an explicit source problem (a missing source is the normal "no anchors" state,
  mirroring the evidence packet's DATA_GAP);
* malformed YAML or a file-level integrity failure (wrong ``schema_version``,
  ``is_llm_generated`` not false, forbidden budget/order/action key, duplicate
  ``anchor_id``, ``anchors`` not a list) -> ``registry_valid: false`` and **zero
  active anchors** (hard fail-closed);
* a per-anchor problem (out-of-universe ticker, bad dates, ``valid_from >
  valid_until``) -> that anchor is placed in ``inactive_anchors`` while any other
  clean anchor may still be active — exactly how ``support_signals`` already
  treats anchors per row (this changes no behavior, it only reports it);
* a valid-but-stale/expired anchor -> ``inactive_anchors`` with ``status:
  "expired"`` (never active).
"""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import json
from typing import Any

from investment_orchestrator.research.research_anchors import (
    ResearchAnchorsResult,
    load_research_anchors,
    normalize_iso_date_value,
)


SCHEMA_VERSION = "active_research_anchor_registry_v1"
COMPILER_VERSION = "active_registry_compiler_v1"

# The single R2G-1 source: the existing operator-authored anchors YAML.
OPERATOR_SOURCE_ID = "operator_research_anchors_yaml"
OPERATOR_SOURCE_CATEGORY = "C_operator"
OPERATOR_SOURCE_TYPE = "operator"
OPERATOR_APPROVAL_TYPE = "operator_authored"

# Anchor statuses used in R2G-1 (revoked / superseded are reserved for later PRs).
STATUS_ACTIVE = "active"
STATUS_EXPIRED = "expired"
STATUS_INVALID = "invalid"

# Registry-level (structural) blocker codes. These set ``registry_valid: false``
# and force zero active anchors.
BLOCKER_SOURCE_YAML_MALFORMED = "source_yaml_malformed"
BLOCKER_RESEARCH_ANCHORS_SOURCE_INVALID = "research_anchors_source_invalid"

# Source-manifest problem code when the file is simply absent (benign).
SOURCE_PROBLEM_MISSING = "research_anchors_missing"

_NON_AUTHORIZATION_NOTE = (
    "Report-only active anchor registry (R2G-1). Compiled from the operator "
    "research_anchors.yaml source only. NOTHING consumes this artifact yet: not "
    "support_signals, not the compiler, not the actionable preview / candidate / "
    "promotion eligibility, not availability, not Step 2/3/4, not the final gate, "
    "not weekly. It never authorizes a trade and adds no NEW_BUY / ORDER_COMPILATION "
    "(permission_effect=none, not_authorization=true)."
)


def build_active_research_anchor_registry(
    *,
    anchors_result: ResearchAnchorsResult | None,
    source_present: bool,
    source_sha256: str | None,
    source_path: str | None,
    as_of_date: str | None,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Compile a ``ResearchAnchorsResult`` into the report-only registry (pure).

    ``anchors_result`` is the outcome of the existing ``load_research_anchors``
    (``None`` when the source file is absent/empty). This function never raises.
    """
    active_anchors: list[dict[str, Any]] = []
    inactive_anchors: list[dict[str, Any]] = []
    audit_trail: list[dict[str, Any]] = []
    source_problems: list[str] = []
    registry_blockers: list[str] = []

    # --- source manifest --------------------------------------------------------
    source_valid = False
    if not source_present or anchors_result is None:
        source_problems.append(SOURCE_PROBLEM_MISSING)
    elif anchors_result.parse_error is not None:
        source_valid = False
        source_problems.append(f"malformed_yaml: {anchors_result.parse_error}")
        registry_blockers.append(BLOCKER_SOURCE_YAML_MALFORMED)
    elif anchors_result.errors:
        # File-level integrity failure (schema / is_llm_generated / forbidden key /
        # duplicate anchor_id / anchors-not-a-list). Fail closed: zero active.
        source_valid = False
        source_problems.extend(anchors_result.errors)
        registry_blockers.append(BLOCKER_RESEARCH_ANCHORS_SOURCE_INVALID)
    else:
        source_valid = True

    source_manifest = [
        {
            "source_id": OPERATOR_SOURCE_ID,
            "source_category": OPERATOR_SOURCE_CATEGORY,
            "source_type": OPERATOR_SOURCE_TYPE,
            "path": source_path,
            "sha256": source_sha256,
            "present": bool(source_present and anchors_result is not None),
            "valid": source_valid,
            "problems": source_problems,
        }
    ]

    # --- anchors ----------------------------------------------------------------
    # When the file failed at the top level, EVERY anchor is refused activation
    # (hard fail-closed). Otherwise split per-anchor exactly as support_signals
    # already reads them: active iff valid AND usable AND not stale.
    file_level_failure = bool(registry_blockers)
    anchors = anchors_result.anchors if anchors_result is not None else []

    for evaluated in anchors:
        row = _normalize_anchor_row(evaluated)
        validation = {
            "valid": bool(evaluated.get("valid")),
            "stale": bool(evaluated.get("stale")),
            "usable": bool(evaluated.get("usable")),
            "problems": list(evaluated.get("problems") or []),
        }
        anchor_id = row["anchor_id"]

        if file_level_failure:
            inactive_anchors.append(
                _inactive_row(
                    row,
                    status=STATUS_INVALID,
                    reason="source-level integrity failure; no anchor activated.",
                    validation=validation,
                )
            )
            audit_trail.append(
                {"event": "anchor_rejected", "anchor_id": anchor_id, "reason": "source_invalid"}
            )
            continue

        is_active = (
            validation["valid"] and validation["usable"] and not validation["stale"]
        )
        if is_active:
            active_anchors.append(_active_row(row, validation=validation))
            audit_trail.append(
                {
                    "event": "anchor_activated",
                    "anchor_id": anchor_id,
                    "source_id": OPERATOR_SOURCE_ID,
                    "approval_id": None,
                }
            )
        elif validation["valid"] and validation["stale"]:
            inactive_anchors.append(
                _inactive_row(
                    row,
                    status=STATUS_EXPIRED,
                    reason="valid_until precedes as_of_date (stale, blocks_if_stale).",
                    validation=validation,
                )
            )
            audit_trail.append(
                {"event": "anchor_expired", "anchor_id": anchor_id, "as_of_date": as_of_date}
            )
        else:
            reason = "; ".join(validation["problems"]) or "anchor invalid."
            inactive_anchors.append(
                _inactive_row(row, status=STATUS_INVALID, reason=reason, validation=validation)
            )
            audit_trail.append(
                {"event": "anchor_rejected", "anchor_id": anchor_id, "reason": "anchor_invalid"}
            )

    counts = {
        "active": len(active_anchors),
        "expired": sum(1 for a in inactive_anchors if a["status"] == STATUS_EXPIRED),
        "revoked": 0,
        "invalid": sum(1 for a in inactive_anchors if a["status"] == STATUS_INVALID),
        "superseded": 0,
    }

    registry_valid = not registry_blockers

    return {
        "schema_version": SCHEMA_VERSION,
        "is_llm_generated": False,
        "report_only": True,
        "permission_effect": "none",
        "not_authorization": True,
        "not_execution_authorization": True,
        "compiler_version": COMPILER_VERSION,
        "as_of_date": as_of_date,
        "generated_at": generated_at,
        "source_manifest": source_manifest,
        "active_anchors": active_anchors,
        "inactive_anchors": inactive_anchors,
        "counts": counts,
        "registry_valid": registry_valid,
        "registry_blockers": registry_blockers,
        "audit_trail": audit_trail,
        "consumed_by_availability": False,
        "consumed_by_step2": False,
        "consumed_by_gates": False,
        "consumed_by_step4": False,
        "notes": _NON_AUTHORIZATION_NOTE,
    }


def compile_active_research_anchor_registry(
    *,
    anchors_path: Any,
    allowed_universe: Any,
    today: Any = None,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Read + validate the operator anchors YAML and compile the registry (never raises).

    Reuses ``load_research_anchors`` for all validation; separately hashes the raw
    source bytes for the manifest. A missing / unreadable source yields a valid
    report with zero active anchors, never a crash.
    """
    try:
        source_text = _read_text_or_none(anchors_path)
        source_present = source_text is not None and source_text.strip() != ""
        source_sha256 = _sha256_of_text(source_text) if source_present else None
        anchors_result = load_research_anchors(
            anchors_path, allowed_universe=allowed_universe, today=today
        )
        as_of_date = _resolve_as_of_date(anchors_result, today)
        return build_active_research_anchor_registry(
            anchors_result=anchors_result,
            source_present=source_present,
            source_sha256=source_sha256,
            source_path=str(anchors_path) if anchors_path is not None else None,
            as_of_date=as_of_date,
            generated_at=generated_at,
        )
    except Exception:  # noqa: BLE001 - report-only: never break the reporting flow
        return build_active_research_anchor_registry(
            anchors_result=None,
            source_present=False,
            source_sha256=None,
            source_path=str(anchors_path) if anchors_path is not None else None,
            as_of_date=normalize_iso_date_value(today),
            generated_at=generated_at,
        )


def write_active_research_anchor_registry(
    *,
    output_path: Any,
    anchors_path: Any,
    allowed_universe: Any,
    today: Any = None,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Compile + write the report-only registry artifact; return a small summary."""
    from investment_orchestrator.common.io import write_json

    registry = compile_active_research_anchor_registry(
        anchors_path=anchors_path,
        allowed_universe=allowed_universe,
        today=today,
        generated_at=generated_at,
    )
    write_json(output_path, registry)
    return {
        "active_research_anchor_registry_path": str(output_path),
        "registry_valid": str(registry["registry_valid"]),
        "active_anchor_count": str(registry["counts"]["active"]),
    }


# --- helpers -----------------------------------------------------------------


def _normalize_anchor_row(evaluated: Mapping[str, Any]) -> dict[str, Any]:
    """Build the intrinsic (identity) anchor row + its stable content hash.

    ``content_sha256`` covers only the deterministic identity/provenance fields —
    not the run-dependent ``validation`` / ``status`` — so it is stable across
    runs while the anchor definition is unchanged.
    """
    identity = {
        "anchor_id": evaluated.get("anchor_id"),
        "anchor_type": evaluated.get("anchor_type"),
        "applicable_tickers": list(evaluated.get("applicable_tickers") or []),
        "anchor_date_et": evaluated.get("anchor_date_et"),
        "valid_from": evaluated.get("valid_from"),
        "valid_until": evaluated.get("valid_until"),
        "confidence_floor": evaluated.get("confidence_floor"),
        "blocks_if_stale": bool(evaluated.get("blocks_if_stale", True)),
        "summary": evaluated.get("summary"),
        "source_type": OPERATOR_SOURCE_TYPE,
        "source_id": OPERATOR_SOURCE_ID,
        "source_category": OPERATOR_SOURCE_CATEGORY,
        "approval_type": OPERATOR_APPROVAL_TYPE,
        "approval_id": None,
        "candidate_id": None,
        "candidate_sha256": None,
    }
    identity["content_sha256"] = _sha256_of(identity)
    return identity


def _active_row(row: Mapping[str, Any], *, validation: Mapping[str, Any]) -> dict[str, Any]:
    return {**row, "status": STATUS_ACTIVE, "validation": dict(validation)}


def _inactive_row(
    row: Mapping[str, Any], *, status: str, reason: str, validation: Mapping[str, Any]
) -> dict[str, Any]:
    return {**row, "status": status, "reason": reason, "validation": dict(validation)}


def _resolve_as_of_date(anchors_result: ResearchAnchorsResult | None, today: Any) -> str | None:
    """Prefer the explicit ``today`` used for expiry; fall back to the file as_of."""
    today_iso = normalize_iso_date_value(today)
    if today_iso is not None:
        return today_iso
    if anchors_result is not None and anchors_result.as_of_date is not None:
        return anchors_result.as_of_date
    return None


def _read_text_or_none(path: Any) -> str | None:
    from investment_orchestrator.common.io import file_exists, read_text

    if path is None or not file_exists(path):
        return None
    try:
        return read_text(path)
    except Exception:  # noqa: BLE001 - unreadable file treated as absent
        return None


def _sha256_of(value: Any) -> str | None:
    if value is None:
        return None
    try:
        serialized = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError):
        return None
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _sha256_of_text(value: str | None) -> str | None:
    if not isinstance(value, str) or value == "":
        return None
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
