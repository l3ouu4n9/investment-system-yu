"""R2G-2: anchor-source dual-read equivalence oracle (report-only).

Compares the **grounding-relevant anchor view** produced by the current
authoritative source (``evidence_packet.research_anchors``, which
``support_signals`` reads today) against the future candidate source (the R2G-1
``active_research_anchor_registry.json``). It answers exactly one question:
"would switching ``support_signals`` to the registry select the same *usable*
anchors as it selects today?"

Strictly report-only and additive. It changes no behavior, switches no consumer,
and is consumed by nothing (``support_signals`` still reads
``evidence_packet.research_anchors``). It grants nothing: ``permission_effect:
"none"``, ``not_authorization: true``, no ``NEW_BUY`` / ``ORDER_COMPILATION``.

What is compared — exactly the fields ``support_signals._evaluate_anchor_refs``
uses to accept an anchor as grounding: ``anchor_id`` presence, ``valid`` /
``stale`` / ``usable`` flags, ``anchor_type``, ``source_type`` (operator-source
requirement), ``applicable_tickers``, and ``confidence_floor``. The comparison
is over the *usable* set (present + valid + usable + not stale + type-allowed +
operator-sourced) — the anchors that could actually ground a memo claim.

What is intentionally NOT compared — memo-dependent per-ticker acceptance
(stance / rationale / source_notes / avoid / memo-confidence-vs-floor), free-text
``problems`` wording, ``summary`` prose, and ``anchor_date_et`` (not part of the
acceptance predicate). Those depend on the analyst memo, not the anchor source,
and are out of scope for a source-equivalence oracle.

Divergence classification (the safety hinge):

* an anchor usable in the authoritative view but NOT active in the registry ->
  the registry is **stricter** -> a WARNING (safe to switch; the registry fails
  closed harder, e.g. on a file-level integrity failure like
  ``is_llm_generated: true`` where the old per-anchor view still trusts a
  structurally-valid row);
* an anchor active in the registry but NOT usable in the authoritative view ->
  the registry is **more permissive** -> a BLOCKER (must never happen; the
  switch could ground something the current path rejects);
* an acceptance-relevant field mismatch on a shared anchor_id -> a BLOCKER.

``equivalent`` is ``true`` only when the two usable sets are identical (no diffs
at all). ``registry_no_more_permissive`` is the weaker, critical safety property:
the registry never activates an anchor the authoritative path would not use.
"""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import json
from typing import Any

from investment_orchestrator.research.research_anchors import ANCHOR_TYPES, SOURCE_TYPES


SCHEMA_VERSION = "anchor_source_equivalence_v1"

CURRENT_AUTHORITATIVE_SOURCE = "evidence_packet.research_anchors"
FUTURE_CANDIDATE_SOURCE = "active_research_anchor_registry"

# Diff kinds.
DIFF_FIELD_MISMATCH = "acceptance_field_mismatch"
DIFF_AUTHORITATIVE_ONLY = "usable_in_authoritative_only"  # registry stricter
DIFF_REGISTRY_ONLY = "active_in_registry_only"  # registry more permissive (unsafe)

# Diff directions.
DIRECTION_REGISTRY_STRICTER = "registry_stricter"
DIRECTION_REGISTRY_MORE_PERMISSIVE = "registry_more_permissive"
DIRECTION_AMBIGUOUS = "ambiguous"

# The acceptance-relevant fields (exactly what _evaluate_anchor_refs reads).
_ACCEPTANCE_FIELDS = (
    "anchor_type",
    "source_type",
    "applicable_tickers",
    "confidence_floor",
    "valid",
    "stale",
    "usable",
)

_NON_AUTHORIZATION_NOTE = (
    "Report-only anchor-source equivalence oracle (R2G-2). Diagnostic only: it "
    "compares the usable-anchor grounding view of evidence_packet.research_anchors "
    "(authoritative) vs the active_research_anchor_registry (future candidate). It "
    "switches no consumer, changes no behavior, and is consumed by nothing. It "
    "never authorizes a trade and adds no NEW_BUY / ORDER_COMPILATION "
    "(permission_effect=none, not_authorization=true)."
)


def evaluate_anchor_source_equivalence(
    *,
    evidence_packet: Mapping[str, Any] | None,
    active_registry: Mapping[str, Any] | None,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Build the report-only equivalence artifact (pure; never raises)."""
    try:
        return _evaluate(
            evidence_packet=evidence_packet,
            active_registry=active_registry,
            generated_at=generated_at,
        )
    except Exception as exc:  # noqa: BLE001 - report-only oracle must never raise
        return _result(
            equivalent=False,
            blockers=["equivalence_oracle_internal_error"],
            warnings=[],
            diffs=[{"anchor_id": None, "kind": "internal_error", "details": {"error": str(exc)}}],
            old_view={},
            registry_view={},
            old_summary={"available": None, "error": str(exc)},
            registry_summary={"registry_valid": None, "error": str(exc)},
            registry_no_more_permissive=False,
            generated_at=generated_at,
        )


def _evaluate(
    *,
    evidence_packet: Mapping[str, Any] | None,
    active_registry: Mapping[str, Any] | None,
    generated_at: str | None,
) -> dict[str, Any]:
    old_view, old_available = _authoritative_usable_view(evidence_packet)
    registry_view, registry_valid, registry_sha = _registry_usable_view(active_registry)

    blockers: list[str] = []
    warnings: list[str] = []
    diffs: list[dict[str, Any]] = []

    all_ids = sorted(set(old_view) | set(registry_view))
    for anchor_id in all_ids:
        in_old = anchor_id in old_view
        in_reg = anchor_id in registry_view
        if in_old and in_reg:
            mismatched = _field_mismatches(old_view[anchor_id], registry_view[anchor_id])
            if mismatched:
                diffs.append(
                    {
                        "anchor_id": anchor_id,
                        "kind": DIFF_FIELD_MISMATCH,
                        "direction": DIRECTION_AMBIGUOUS,
                        "details": mismatched,
                    }
                )
                blockers.append(f"{DIFF_FIELD_MISMATCH}:{anchor_id}")
        elif in_old and not in_reg:
            # Registry is stricter — safe to switch (fails closed harder).
            diffs.append(
                {
                    "anchor_id": anchor_id,
                    "kind": DIFF_AUTHORITATIVE_ONLY,
                    "direction": DIRECTION_REGISTRY_STRICTER,
                    "details": {"authoritative": old_view[anchor_id]},
                }
            )
            warnings.append(f"{DIFF_AUTHORITATIVE_ONLY}:{anchor_id}")
        else:  # in registry only — registry MORE permissive: unsafe.
            diffs.append(
                {
                    "anchor_id": anchor_id,
                    "kind": DIFF_REGISTRY_ONLY,
                    "direction": DIRECTION_REGISTRY_MORE_PERMISSIVE,
                    "details": {"registry": registry_view[anchor_id]},
                }
            )
            blockers.append(f"{DIFF_REGISTRY_ONLY}:{anchor_id}")

    registry_no_more_permissive = not any(
        d["kind"] in (DIFF_REGISTRY_ONLY, DIFF_FIELD_MISMATCH) for d in diffs
    )
    equivalent = not blockers and not warnings

    checked_tickers = sorted(
        {
            t
            for view in (old_view, registry_view)
            for entry in view.values()
            for t in entry.get("applicable_tickers", [])
        }
    )

    old_summary = {
        "available": old_available,
        "usable_anchor_count": len(old_view),
        "usable_anchor_ids": sorted(old_view),
        "sha256": _sha256_of(_research_anchors_section(evidence_packet)),
    }
    registry_summary = {
        "registry_valid": registry_valid,
        "active_anchor_count": len(registry_view),
        "active_anchor_ids": sorted(registry_view),
        "source_sha256": registry_sha,
    }

    return _result(
        equivalent=equivalent,
        blockers=blockers,
        warnings=warnings,
        diffs=diffs,
        old_view=old_view,
        registry_view=registry_view,
        old_summary=old_summary,
        registry_summary=registry_summary,
        registry_no_more_permissive=registry_no_more_permissive,
        checked_anchor_ids=all_ids,
        checked_tickers=checked_tickers,
        generated_at=generated_at,
    )


def _result(
    *,
    equivalent: bool,
    blockers: list[str],
    warnings: list[str],
    diffs: list[dict[str, Any]],
    old_view: Mapping[str, Any],
    registry_view: Mapping[str, Any],
    old_summary: Mapping[str, Any],
    registry_summary: Mapping[str, Any],
    registry_no_more_permissive: bool,
    generated_at: str | None,
    checked_anchor_ids: list[str] | None = None,
    checked_tickers: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "is_llm_generated": False,
        "report_only": True,
        "permission_effect": "none",
        "not_authorization": True,
        "not_execution_authorization": True,
        "current_authoritative_source": CURRENT_AUTHORITATIVE_SOURCE,
        "future_candidate_source": FUTURE_CANDIDATE_SOURCE,
        "authoritative_behavior_unchanged": True,
        "consumed_by_support_signals": False,
        "consumed_by_compiler": False,
        "consumed_by_promotion_eligibility": False,
        "consumed_by_availability": False,
        "consumed_by_step2": False,
        "consumed_by_gates": False,
        "consumed_by_step4": False,
        "equivalent": bool(equivalent),
        # The critical directional safety property: the registry never activates an
        # anchor the authoritative path would not use (no false-positive grounding).
        "registry_no_more_permissive": bool(registry_no_more_permissive),
        "equivalence_blockers": list(blockers),
        "equivalence_warnings": list(warnings),
        "old_anchor_summary": dict(old_summary),
        "registry_anchor_summary": dict(registry_summary),
        "diffs": list(diffs),
        "checked_anchor_ids": list(checked_anchor_ids or []),
        "checked_tickers": list(checked_tickers or []),
        "generated_at": generated_at,
        "notes": _NON_AUTHORIZATION_NOTE,
    }


# --- disk wrapper -------------------------------------------------------------


def write_anchor_source_equivalence(
    *,
    output_path: Any,
    evidence_packet: Mapping[str, Any] | None,
    active_registry: Mapping[str, Any] | None,
    generated_at: str | None = None,
) -> dict[str, Any]:
    """Evaluate + write the report-only equivalence artifact; return a small summary."""
    from investment_orchestrator.common.io import write_json

    payload = evaluate_anchor_source_equivalence(
        evidence_packet=evidence_packet,
        active_registry=active_registry,
        generated_at=generated_at,
    )
    write_json(output_path, payload)
    return {
        "anchor_source_equivalence_path": str(output_path),
        "equivalent": str(payload["equivalent"]),
        "registry_no_more_permissive": str(payload["registry_no_more_permissive"]),
    }


# --- view builders -----------------------------------------------------------


def _authoritative_usable_view(
    evidence_packet: Mapping[str, Any] | None,
) -> tuple[dict[str, dict[str, Any]], bool | None]:
    """Reconstruct exactly the usable-anchor set support_signals would ground on.

    Mirrors ``support_signals._usable_anchors_by_id`` (index every anchor by id)
    then keeps only anchors that pass ``_evaluate_anchor_refs``' anchor-level gates
    (valid + usable + not stale + operator-sourced + type-allowed). Deliberately
    does NOT apply file-level integrity gating — the current path does not — so a
    divergence with the (stricter) registry is surfaced honestly.
    """
    section = _research_anchors_section(evidence_packet)
    if section is None:
        return {}, None
    available = section.get("available") is True
    if not available:
        return {}, section.get("available")
    anchors = section.get("anchors")
    if not isinstance(anchors, list):
        return {}, available

    view: dict[str, dict[str, Any]] = {}
    for anchor in anchors:
        if not isinstance(anchor, Mapping):
            continue
        anchor_id = anchor.get("anchor_id")
        if not (isinstance(anchor_id, str) and anchor_id.strip()):
            continue
        key = anchor_id.strip()
        # Last occurrence wins — matches support_signals' dict-overwrite semantics.
        if _anchor_is_usable_authoritative(anchor):
            view[key] = _acceptance_fields_from_authoritative(anchor)
        else:
            view.pop(key, None)
    return view, available


def _registry_usable_view(
    active_registry: Mapping[str, Any] | None,
) -> tuple[dict[str, dict[str, Any]], bool | None, str | None]:
    """The registry's active-anchor set, projected onto the acceptance fields.

    Active anchors already encode (valid + usable + not stale + no file-level
    failure); we additionally filter by type/source for exact parity with the
    authoritative gates (they always pass, but the filter keeps the comparison
    definitionally identical).
    """
    if not isinstance(active_registry, Mapping):
        return {}, None, None
    registry_valid = active_registry.get("registry_valid")
    source_sha = _registry_source_sha(active_registry)
    active = active_registry.get("active_anchors")
    if not isinstance(active, list):
        return {}, registry_valid, source_sha

    view: dict[str, dict[str, Any]] = {}
    for anchor in active:
        if not isinstance(anchor, Mapping):
            continue
        anchor_id = anchor.get("anchor_id")
        if not (isinstance(anchor_id, str) and anchor_id.strip()):
            continue
        fields = _acceptance_fields_from_registry(anchor)
        if (
            fields["valid"]
            and fields["usable"]
            and not fields["stale"]
            and fields["anchor_type"] in ANCHOR_TYPES
            and fields["source_type"] in SOURCE_TYPES
        ):
            view[anchor_id.strip()] = fields
    return view, registry_valid, source_sha


def _anchor_is_usable_authoritative(anchor: Mapping[str, Any]) -> bool:
    return (
        anchor.get("valid") is True
        and anchor.get("usable") is True
        and anchor.get("stale") is not True
        and anchor.get("anchor_type") in ANCHOR_TYPES
        and anchor.get("source_type") in SOURCE_TYPES
    )


def _acceptance_fields_from_authoritative(anchor: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "anchor_type": anchor.get("anchor_type"),
        "source_type": anchor.get("source_type"),
        "applicable_tickers": _ticker_list(anchor.get("applicable_tickers")),
        "confidence_floor": anchor.get("confidence_floor"),
        "valid": anchor.get("valid") is True,
        "stale": anchor.get("stale") is True,
        "usable": anchor.get("usable") is True,
    }


def _acceptance_fields_from_registry(anchor: Mapping[str, Any]) -> dict[str, Any]:
    validation = anchor.get("validation")
    validation = validation if isinstance(validation, Mapping) else {}
    return {
        "anchor_type": anchor.get("anchor_type"),
        "source_type": anchor.get("source_type"),
        "applicable_tickers": _ticker_list(anchor.get("applicable_tickers")),
        "confidence_floor": anchor.get("confidence_floor"),
        "valid": validation.get("valid") is True,
        "stale": validation.get("stale") is True,
        "usable": validation.get("usable") is True,
    }


def _field_mismatches(
    old_fields: Mapping[str, Any], registry_fields: Mapping[str, Any]
) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for field in _ACCEPTANCE_FIELDS:
        old_value = old_fields.get(field)
        reg_value = registry_fields.get(field)
        if field == "applicable_tickers":
            old_value = sorted(old_value or [])
            reg_value = sorted(reg_value or [])
        if old_value != reg_value:
            out[field] = {"authoritative": old_fields.get(field), "registry": registry_fields.get(field)}
    return out


# --- helpers -----------------------------------------------------------------


def _research_anchors_section(evidence_packet: Mapping[str, Any] | None) -> Mapping[str, Any] | None:
    if not isinstance(evidence_packet, Mapping):
        return None
    section = evidence_packet.get("research_anchors")
    return section if isinstance(section, Mapping) else None


def _registry_source_sha(active_registry: Mapping[str, Any]) -> str | None:
    manifest = active_registry.get("source_manifest")
    if isinstance(manifest, list) and manifest and isinstance(manifest[0], Mapping):
        sha = manifest[0].get("sha256")
        return sha if isinstance(sha, str) else None
    return None


def _ticker_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in value:
        if isinstance(item, str) and item.strip():
            ticker = item.strip().upper()
            if ticker not in seen:
                seen.add(ticker)
                out.append(ticker)
    return out


def _sha256_of(value: Any) -> str | None:
    if value is None:
        return None
    try:
        serialized = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError):
        return None
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()
