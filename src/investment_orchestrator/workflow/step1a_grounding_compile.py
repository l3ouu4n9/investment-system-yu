"""S1A-0 deterministic Step 1A grounding/evidence compile bundle.

Extraction-only builder for the future Step 1A split. It returns already-existing
deterministic/R2G artifact payloads without writing files and without being wired
into production Step 1. The bundle is a parity target and migration aid only.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from investment_orchestrator.research.active_research_anchor_registry import (
    compile_active_research_anchor_registry,
)
from investment_orchestrator.research.approval_registry_dual_read_diff import (
    build_approval_registry_dual_read_diff,
)
from investment_orchestrator.research.approval_registry_switch_readiness import (
    build_approval_registry_switch_readiness,
)
from investment_orchestrator.research.approvals_inclusive_active_registry import (
    build_active_research_anchor_registry_with_approvals,
)
from investment_orchestrator.research.evidence_packet import (
    build_embedded_active_anchor_registry_selection,
    build_evidence_packet,
)
from investment_orchestrator.research.grounding_status_observatory import (
    build_grounding_status_observatory,
)
from investment_orchestrator.research.research_anchor_approval_manifest import (
    validate_research_anchor_approvals,
)
from investment_orchestrator.research.research_anchor_revocation_manifest import (
    validate_research_anchor_revocations,
)
from investment_orchestrator.research.research_anchors import build_research_anchors_summary


SCHEMA_VERSION = "step1a_grounding_compile_bundle_v1"


def build_step1a_grounding_compile_bundle(
    *,
    strategy_settings: Mapping[str, Any] | None,
    research_anchors_path: Any,
    research_anchor_approvals_path: Any,
    portfolio_snapshot_text: str | None = None,
    portfolio_snapshot_path: Any = None,
    last_good_available: bool = False,
    last_good_metadata: Mapping[str, Any] | None = None,
    strategy_settings_path: Any = None,
    last_good_metadata_path: Any = None,
    active_registry_artifact_path: Any = None,
    approvals_registry_artifact_path: Any = None,
    optional_research_anchor_candidates: Mapping[str, Any] | None = None,
    optional_compiled_support_signals: Mapping[str, Any] | None = None,
    generated_at: str | None = None,
    now_date: str | None = None,
) -> dict[str, Any]:
    """Build deterministic Step 1A payloads without writing or wiring them.

    Source paths are explicit inputs. The builder reuses existing deterministic
    helpers for registry/readiness/approval/revocation/evidence policy and does
    not import or invoke any LLM or order-path code.
    """
    try:
        return _build(
            strategy_settings=strategy_settings,
            research_anchors_path=research_anchors_path,
            research_anchor_approvals_path=research_anchor_approvals_path,
            portfolio_snapshot_text=portfolio_snapshot_text,
            portfolio_snapshot_path=portfolio_snapshot_path,
            last_good_available=last_good_available,
            last_good_metadata=last_good_metadata,
            strategy_settings_path=strategy_settings_path,
            last_good_metadata_path=last_good_metadata_path,
            active_registry_artifact_path=active_registry_artifact_path,
            approvals_registry_artifact_path=approvals_registry_artifact_path,
            optional_research_anchor_candidates=optional_research_anchor_candidates,
            optional_compiled_support_signals=optional_compiled_support_signals,
            generated_at=generated_at,
            now_date=now_date,
        )
    except Exception as exc:  # noqa: BLE001 - extraction bundle must fail closed
        return _result(
            artifacts={},
            source_summary={},
            diagnostics={
                "diagnostics_incomplete": True,
                "internal_error": str(exc),
                "files_written": [],
                "production_wiring_added": False,
                "llm_calls_made": False,
                "permissions_opened": False,
                "order_paths_opened": False,
            },
            generated_at=generated_at,
        )


def _build(
    *,
    strategy_settings: Mapping[str, Any] | None,
    research_anchors_path: Any,
    research_anchor_approvals_path: Any,
    portfolio_snapshot_text: str | None,
    portfolio_snapshot_path: Any,
    last_good_available: bool,
    last_good_metadata: Mapping[str, Any] | None,
    strategy_settings_path: Any,
    last_good_metadata_path: Any,
    active_registry_artifact_path: Any,
    approvals_registry_artifact_path: Any,
    optional_research_anchor_candidates: Mapping[str, Any] | None,
    optional_compiled_support_signals: Mapping[str, Any] | None,
    generated_at: str | None,
    now_date: str | None,
) -> dict[str, Any]:
    settings = strategy_settings if isinstance(strategy_settings, Mapping) else None
    settings_as_of = _first_str(now_date, _get(settings, "as_of"))
    allowed_universe = _allowed_buy_universe(settings)

    active_registry = compile_active_research_anchor_registry(
        anchors_path=research_anchors_path,
        allowed_universe=allowed_universe,
        today=settings_as_of,
        generated_at=generated_at,
    )
    approvals_validation = validate_research_anchor_approvals(
        manifest_path=research_anchor_approvals_path,
        allowed_universe=allowed_universe,
        today=settings_as_of,
        as_of_date=settings_as_of,
        generated_at=generated_at,
    )
    revocations_validation = validate_research_anchor_revocations(
        manifest_path=research_anchor_approvals_path,
        allowed_universe=allowed_universe,
        today=settings_as_of,
        as_of_date=settings_as_of,
        generated_at=generated_at,
    )

    # Match the current Step 1 standalone approvals-inclusive registry writer:
    # recompute approvals/revocations directly from YAML, not from report artifacts.
    overlay_approvals_validation = validate_research_anchor_approvals(
        manifest_path=research_anchor_approvals_path,
        allowed_universe=allowed_universe,
        today=settings_as_of,
        generated_at=generated_at,
    )
    overlay_revocations_validation = validate_research_anchor_revocations(
        manifest_path=research_anchor_approvals_path,
        allowed_universe=allowed_universe,
        today=settings_as_of,
        as_of_date=active_registry.get("as_of_date") if isinstance(active_registry, Mapping) else None,
        generated_at=generated_at,
    )
    approvals_registry = build_active_research_anchor_registry_with_approvals(
        baseline=active_registry,
        approvals_validation=overlay_approvals_validation,
        revocations_validation=overlay_revocations_validation,
        generated_at=generated_at,
    )
    dual_read_diff = build_approval_registry_dual_read_diff(
        baseline_registry=active_registry,
        approvals_registry=approvals_registry,
        baseline_registry_path=_path_str(active_registry_artifact_path),
        approvals_registry_path=_path_str(approvals_registry_artifact_path),
        generated_at=generated_at,
    )
    readiness = build_approval_registry_switch_readiness(
        anchors_path=research_anchors_path,
        approvals_path=research_anchor_approvals_path,
        allowed_universe=allowed_universe,
        today=settings_as_of,
        generated_at=generated_at,
    )

    embedded_selection = build_embedded_active_anchor_registry_selection(
        anchors_path=research_anchors_path,
        approvals_path=research_anchor_approvals_path,
        allowed_universe=allowed_universe,
        today=settings_as_of,
        generated_at=generated_at,
    )
    research_anchors_summary = build_research_anchors_summary(
        research_anchors_path,
        allowed_universe=allowed_universe,
        today=settings_as_of,
    )
    evidence_packet = build_evidence_packet(
        strategy_settings=settings,
        portfolio_snapshot_text=portfolio_snapshot_text,
        portfolio_snapshot_path=portfolio_snapshot_path,
        last_good_available=last_good_available,
        last_good_metadata=last_good_metadata,
        now_date=settings_as_of,
        generated_at=generated_at,
        source_artifacts=_source_artifacts(
            strategy_settings_path=strategy_settings_path,
            portfolio_snapshot_path=portfolio_snapshot_path,
            last_good_metadata_path=last_good_metadata_path,
            research_anchors_path=research_anchors_path,
            research_anchor_approvals_path=research_anchor_approvals_path,
        ),
        research_anchors_summary=research_anchors_summary,
        active_anchor_registry=embedded_selection.get("selected_registry")
        if isinstance(embedded_selection, Mapping)
        else None,
    )

    observatory = build_grounding_status_observatory(
        evidence_packet=evidence_packet,
        embedded_registry_selection=embedded_selection if isinstance(embedded_selection, Mapping) else None,
        readiness=readiness if isinstance(readiness, Mapping) else None,
        baseline_registry=active_registry if isinstance(active_registry, Mapping) else None,
        approvals_registry=approvals_registry if isinstance(approvals_registry, Mapping) else None,
        approvals_validation=approvals_validation if isinstance(approvals_validation, Mapping) else None,
        revocations_validation=revocations_validation if isinstance(revocations_validation, Mapping) else None,
        candidates=optional_research_anchor_candidates
        if isinstance(optional_research_anchor_candidates, Mapping)
        else None,
        support_signals=optional_compiled_support_signals
        if isinstance(optional_compiled_support_signals, Mapping)
        else None,
        generated_at=generated_at,
    )

    artifacts = {
        "active_research_anchor_registry": active_registry,
        "research_anchor_approvals_validation": approvals_validation,
        "research_anchor_revocations_validation": revocations_validation,
        "active_research_anchor_registry_with_approvals": approvals_registry,
        "approval_registry_dual_read_diff": dual_read_diff,
        "approval_registry_switch_readiness": readiness,
        "embedded_active_anchor_registry_selection": embedded_selection,
        "evidence_packet": evidence_packet,
        "grounding_status_observatory": observatory,
    }
    return _result(
        artifacts=artifacts,
        source_summary={
            "allowed_universe": allowed_universe,
            "as_of_date": settings_as_of,
            "research_anchors_path": _path_str(research_anchors_path),
            "research_anchor_approvals_path": _path_str(research_anchor_approvals_path),
            "portfolio_snapshot_path": _path_str(portfolio_snapshot_path),
        },
        diagnostics={
            "diagnostics_incomplete": False,
            "files_written": [],
            "production_wiring_added": False,
            "llm_calls_made": False,
            "readiness_recomputed_with_existing_helper": True,
            "registry_selection_recomputed_with_existing_helper": True,
            "consumed_by_production": False,
            "permissions_opened": False,
            "order_paths_opened": False,
        },
        generated_at=generated_at,
    )


def _result(
    *,
    artifacts: Mapping[str, Any],
    source_summary: Mapping[str, Any],
    diagnostics: Mapping[str, Any],
    generated_at: str | None,
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "is_llm_generated": False,
        "generated_at": generated_at,
        "extraction_only": True,
        "not_wired_to_production": True,
        "report_only": True,
        "permission_effect": "none",
        "not_authorization": True,
        "not_execution_authorization": True,
        "consumed_by_gates": False,
        "consumed_by_order_path": False,
        "consumed_by_step2": False,
        "consumed_by_step3": False,
        "consumed_by_step4": False,
        "consumed_by_final_execution": False,
        "consumed_by_weekly": False,
        "consumed_by_broker_live": False,
        "cannot_affect_allowed_actions": True,
        "cannot_affect_registry_selection": True,
        "not_registry_selection_input": True,
        "not_order_input": True,
        "not_permission_input": True,
        "not_gate_input": True,
        "artifacts": dict(artifacts),
        "source_summary": dict(source_summary),
        "diagnostics": dict(diagnostics),
        "safety_invariants": {
            "no_llm_calls": True,
            "no_file_writes": True,
            "no_production_consumer": True,
            "no_new_buy_permission": True,
            "no_order_compilation_permission": True,
            "no_step4_enablement": True,
            "no_final_execution": True,
            "no_weekly_automation_change": True,
            "no_broker_live_execution": True,
            "no_executable_order_authority": True,
            "candidate_sha256_audit_only": True,
            "grounding_status_observatory_consumed_by_nothing": True,
        },
    }


def _allowed_buy_universe(strategy_settings: Mapping[str, Any] | None) -> list[str]:
    settings = strategy_settings if isinstance(strategy_settings, Mapping) else {}
    out: list[str] = []
    seen: set[str] = set()
    for key in ("core_universe", "satellite_universe"):
        value = settings.get(key)
        if not isinstance(value, list):
            continue
        for item in value:
            if isinstance(item, str) and item.strip():
                ticker = item.strip().upper()
                if ticker not in seen:
                    seen.add(ticker)
                    out.append(ticker)
    return out


def _source_artifacts(
    *,
    strategy_settings_path: Any,
    portfolio_snapshot_path: Any,
    last_good_metadata_path: Any,
    research_anchors_path: Any,
    research_anchor_approvals_path: Any,
) -> dict[str, str]:
    return {
        "strategy_settings": _path_str(strategy_settings_path),
        "portfolio_snapshot": _path_str(portfolio_snapshot_path),
        "last_good_metadata": _path_str(last_good_metadata_path),
        "research_anchors": _path_str(research_anchors_path),
        "research_anchor_approvals": _path_str(research_anchor_approvals_path),
    }


def _path_str(value: Any) -> str:
    return str(value) if value is not None else ""


def _get(value: Mapping[str, Any] | None, key: str) -> Any:
    if not isinstance(value, Mapping):
        return None
    return value.get(key)


def _first_str(*values: Any) -> str | None:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value
    return None
