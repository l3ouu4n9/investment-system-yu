"""S1A-0 deterministic Step 1A grounding/evidence compile bundle.

Extraction-only builder for the future Step 1A split. It returns already-existing
deterministic/R2G artifact payloads without writing files and without being wired
into production Step 1. The bundle is a parity target and migration aid only.
"""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import json
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
    normalize_evidence_packet_for_parity,
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
SHADOW_DIFF_SCHEMA_VERSION = "step1a_grounding_compile_shadow_diff_v1"

_ARTIFACT_KEYS = (
    "active_research_anchor_registry",
    "research_anchor_approvals_validation",
    "research_anchor_revocations_validation",
    "active_research_anchor_registry_with_approvals",
    "approval_registry_dual_read_diff",
    "approval_registry_switch_readiness",
    "embedded_active_anchor_registry_selection",
    "evidence_packet",
    "grounding_status_observatory",
)

# S1A-3/4/5/6/7/8/11: the production artifacts whose WRITERS source their payload
# from the narrow Step 1A accessors. This is code-level design state (artifact
# paths, schemas, and payload bytes are unchanged); per-run writer provenance —
# including any legacy fallback — is recorded in step1a_artifact_switch_status.json,
# not here. Every entry must stay a member of _ARTIFACT_KEYS so each switched
# artifact keeps a shadow comparison. Note the switch-readiness entry covers the
# DISK OBSERVER artifact only: runtime readiness is recomputed in memory by the
# evidence packet's embedded selector, so the readiness_uses_step1a_output:false
# runtime marker stays accurate. The with-approvals and dual-read-diff writers each
# carry a run-time parity guard (S1A-6/S1A-8): the Step 1A payload reaches disk only
# when it equals the legacy derivation. The evidence_packet writer (S1A-11) is the
# FIRST grounding-input switch and carries the strictest guard: the Step 1A payload
# reaches disk only when compare_evidence_packet_runtime_parity confirms the
# runtime-relevant subtree is byte-identical (only generated_at normalized), with
# NO report-only differences either — so support_signals, which grounds off the
# disk read-back, sees an identical registry. The embedded-selection artifact
# (S1A-12) is Step 1A-sourced behind a full-payload parity guard
# (compare_embedded_selection_parity) that is lineage-coupled to the packet guard:
# a packet fallback forces a selection fallback, so the artifact always describes
# the selection lineage behind the packet written by the same pass. Its canonical
# key below is the bundle key (anchor-spelled); the on-disk filename stays
# embedded_active_registry_selection.json (no path switch). Runtime authority is
# unchanged.
#
# S1A-13: this eight-entry migration is COMPLETE. grounding_status_observatory
# (the ninth _ARTIFACT_KEYS member) is deliberately NOT added here — it is a
# single shared summarizer with no separate legacy algorithm, and its remaining
# independent value (production disk-read-back vs Step 1A in-memory bundle) would
# be destroyed by switching its writer. Do not add a ninth entry without a new
# design decision superseding this one. See docs/step1a_writer_migration_status.md.
STEP1A_WRITER_SOURCE_ARTIFACTS = (
    "active_research_anchor_registry",
    "research_anchor_approvals_validation",
    "research_anchor_revocations_validation",
    "active_research_anchor_registry_with_approvals",
    "approval_registry_switch_readiness",
    "approval_registry_dual_read_diff",
    "evidence_packet",
    "embedded_active_anchor_registry_selection",
)


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


def build_step1a_active_research_anchor_registry(
    *,
    strategy_settings: Mapping[str, Any] | None,
    research_anchors_path: Any,
    generated_at: str | None = None,
    now_date: str | None = None,
) -> dict[str, Any]:
    """Build the Step 1A baseline active anchor registry payload (S1A-3).

    Narrow accessor for the first artifact switch. It is the SAME derivation the
    full Step 1A bundle uses (``_build`` calls this function), and it calls the
    same deterministic compiler with the same settings/universe/as-of inputs as
    the legacy Step 1 writer — so the payload is byte-identical to the legacy
    compile by construction. Pure: reads only the anchors file via the compiler,
    writes nothing, and carries no selection/permission/order authority.
    """
    settings = strategy_settings if isinstance(strategy_settings, Mapping) else None
    return compile_active_research_anchor_registry(
        anchors_path=research_anchors_path,
        allowed_universe=_allowed_buy_universe(settings),
        today=_first_str(now_date, _get(settings, "as_of")),
        generated_at=generated_at,
    )


def build_step1a_research_anchor_approvals_validation(
    *,
    strategy_settings: Mapping[str, Any] | None,
    research_anchor_approvals_path: Any,
    generated_at: str | None = None,
    now_date: str | None = None,
) -> dict[str, Any]:
    """Build the Step 1A standalone approvals-validation report payload (S1A-4).

    Narrow accessor for the second artifact switch: the SAME derivation the full
    Step 1A bundle uses for its REPORT variant (``_build`` calls this function),
    calling the same deterministic validator with the same manifest/universe/
    as-of inputs as the legacy Step 1 writer — byte-identical to the legacy
    compile for string-or-absent ``as_of``. A non-string ``as_of`` normalizes to
    None via ``_first_str`` (the established Step 1A convention). This is NOT the
    overlay variant that feeds the with-approvals registry; that path is
    separate and unchanged. Pure: reads only the manifest via the validator,
    writes nothing, and carries no activation/selection/permission/order
    authority (``would_activate`` stays report-only).
    """
    settings = strategy_settings if isinstance(strategy_settings, Mapping) else None
    as_of = _first_str(now_date, _get(settings, "as_of"))
    return validate_research_anchor_approvals(
        manifest_path=research_anchor_approvals_path,
        allowed_universe=_allowed_buy_universe(settings),
        today=as_of,
        as_of_date=as_of,
        generated_at=generated_at,
    )


def build_step1a_research_anchor_revocations_validation(
    *,
    strategy_settings: Mapping[str, Any] | None,
    research_anchor_approvals_path: Any,
    generated_at: str | None = None,
    now_date: str | None = None,
) -> dict[str, Any]:
    """Build the Step 1A standalone revocations-validation report payload (S1A-5).

    Narrow accessor for the third artifact switch: the SAME derivation the full
    Step 1A bundle uses for its settings-anchored REPORT variant (``_build``
    calls this function), calling the same deterministic validator with the same
    manifest/universe/as-of inputs as the legacy Step 1 writer — byte-identical
    to the legacy compile for string-or-absent ``as_of``. A non-string ``as_of``
    normalizes to None via ``_first_str`` (the established Step 1A convention);
    note a manifest carrying its own ``as_of_date`` anchors effective-date
    evaluation regardless. This is NOT the overlay variant that feeds the
    with-approvals registry — that path keeps its baseline-coupled
    ``as_of_date=active_registry.get("as_of_date")`` and is unchanged. Pure:
    reads only the manifest via the validator, writes nothing, APPLIES no
    revocation, and carries no selection/permission/order authority (the
    validator has no candidate-binding surface; ``reason`` stays
    non-authoritative).
    """
    settings = strategy_settings if isinstance(strategy_settings, Mapping) else None
    as_of = _first_str(now_date, _get(settings, "as_of"))
    return validate_research_anchor_revocations(
        manifest_path=research_anchor_approvals_path,
        allowed_universe=_allowed_buy_universe(settings),
        today=as_of,
        as_of_date=as_of,
        generated_at=generated_at,
    )


def build_step1a_active_research_anchor_registry_with_approvals(
    *,
    strategy_settings: Mapping[str, Any] | None,
    research_anchors_path: Any,
    research_anchor_approvals_path: Any,
    generated_at: str | None = None,
    now_date: str | None = None,
) -> dict[str, Any]:
    """Build the Step 1A approvals-inclusive registry payload (S1A-6).

    Narrow accessor for the fourth artifact switch — the first OVERLAY-lineage
    switch. It is the SAME derivation the full Step 1A bundle uses (``_build``
    calls this function) and replicates the Step 1 dual-read writer's overlay
    flavor exactly: the baseline comes from the S1A-3 accessor, the overlay
    approvals validation passes NO ``as_of_date`` (the settings-anchored S1A-4
    REPORT accessor must not be reused here), and the overlay revocations
    validation keeps the baseline-coupled ``as_of_date`` (the settings-anchored
    S1A-5 REPORT accessor must not be reused either). It deliberately does NOT
    delegate to ``compile_active_research_anchor_registry_with_approvals``:
    that helper's never-raise/fail-closed shell would turn accessor bugs into
    degraded payloads recorded as a "step1a" success instead of raising so the
    switched writer can fall back to legacy. It is also NOT the evidence
    packet's embedded-selection derivation (which passes ``as_of_date`` to the
    approvals validation and stays untouched). Byte-identical to the legacy
    inline derivation for string-or-absent ``as_of``; a non-string ``as_of``
    normalizes to None via ``_first_str`` (the established Step 1A convention)
    and is absorbed by the switched writer's run-time parity guard. Pure: reads
    only the two operator YAML files via the shared compilers/validators,
    writes nothing, applies revocations only inside the returned report-only
    payload, and carries no selection/permission/order authority.
    """
    settings = strategy_settings if isinstance(strategy_settings, Mapping) else None
    as_of = _first_str(now_date, _get(settings, "as_of"))
    allowed_universe = _allowed_buy_universe(settings)
    baseline = build_step1a_active_research_anchor_registry(
        strategy_settings=settings,
        research_anchors_path=research_anchors_path,
        generated_at=generated_at,
        now_date=as_of,
    )
    overlay_approvals_validation = validate_research_anchor_approvals(
        manifest_path=research_anchor_approvals_path,
        allowed_universe=allowed_universe,
        today=as_of,
        generated_at=generated_at,
    )
    overlay_revocations_validation = validate_research_anchor_revocations(
        manifest_path=research_anchor_approvals_path,
        allowed_universe=allowed_universe,
        today=as_of,
        as_of_date=baseline.get("as_of_date") if isinstance(baseline, Mapping) else None,
        generated_at=generated_at,
    )
    return build_active_research_anchor_registry_with_approvals(
        baseline=baseline,
        approvals_validation=overlay_approvals_validation,
        revocations_validation=overlay_revocations_validation,
        generated_at=generated_at,
    )


def build_step1a_approval_registry_switch_readiness(
    *,
    strategy_settings: Mapping[str, Any] | None,
    research_anchors_path: Any,
    research_anchor_approvals_path: Any,
    generated_at: str | None = None,
    now_date: str | None = None,
) -> dict[str, Any]:
    """Build the Step 1A switch-readiness DISK OBSERVER payload (S1A-7).

    Narrow accessor for the fifth artifact switch. It is the SAME derivation the
    full Step 1A bundle uses (``_build`` calls this function): a thin wrapper
    over the shared deterministic ``build_approval_registry_switch_readiness``
    — the exact builder the legacy write wrapper already delegates to — so the
    payload is byte-identical to the legacy write by construction for
    string-or-absent ``as_of``; a non-string ``as_of`` normalizes to None via
    ``_first_str`` (the established Step 1A convention). This payload is the
    disk OBSERVER artifact only: runtime readiness is recomputed in memory by
    the evidence packet's embedded selector (and the support-signals dual-ground
    dry run) and never reads this JSON as authority. ``ready`` and
    ``switch_target`` are report fields, not permissions. The accessor changes
    no registry selection, no evidence_packet behavior, no support_signals
    grounding, no gate, and no order path. Pure: reads only the two operator
    YAML files via the shared builder, writes nothing, and carries no
    selection/permission/order authority.
    """
    settings = strategy_settings if isinstance(strategy_settings, Mapping) else None
    return build_approval_registry_switch_readiness(
        anchors_path=research_anchors_path,
        approvals_path=research_anchor_approvals_path,
        allowed_universe=_allowed_buy_universe(settings),
        today=_first_str(now_date, _get(settings, "as_of")),
        generated_at=generated_at,
    )


def build_step1a_approval_registry_dual_read_diff(
    *,
    strategy_settings: Mapping[str, Any] | None,
    research_anchors_path: Any,
    research_anchor_approvals_path: Any,
    baseline_registry_artifact_path: Any = None,
    approvals_registry_artifact_path: Any = None,
    generated_at: str | None = None,
    now_date: str | None = None,
) -> dict[str, Any]:
    """Build the Step 1A dual-read diff DISK OBSERVER payload (S1A-8).

    Narrow accessor for the sixth artifact switch. It is the SAME derivation the
    full Step 1A bundle uses (``_build`` calls this function): it composes the
    already-proven S1A-3 baseline accessor and S1A-6 with-approvals accessor and
    diffs them with the shared deterministic ``build_approval_registry_dual_read_diff``.
    It deliberately does NOT delegate to
    ``compile_active_research_anchor_registry_with_approvals``: that helper's
    never-raise/fail-closed shell would mask divergence instead of raising so the
    switched writer can fall back to legacy. Byte-identical to the legacy diff by
    construction — the diff is a pure function of the baseline and with-approvals
    registries (both independently byte-proven) plus the two artifact path strings
    and ``generated_at`` — for string-or-absent ``as_of``; a non-string ``as_of``
    normalizes to None via ``_first_str`` (the established Step 1A convention) and
    is absorbed by the switched writer's run-time parity guard. The disk dual-read
    diff artifact is consumed by NOTHING except the report-only shadow comparison;
    it never authorizes a trade and changes no registry selection, evidence_packet,
    support_signals, readiness, gate, or order path, and no approval/revocation
    semantics. Pure: reads only the two operator YAML files via the shared
    accessors/builder, writes nothing, and carries no selection/permission/order
    authority.
    """
    settings = strategy_settings if isinstance(strategy_settings, Mapping) else None
    as_of = _first_str(now_date, _get(settings, "as_of"))
    baseline = build_step1a_active_research_anchor_registry(
        strategy_settings=settings,
        research_anchors_path=research_anchors_path,
        generated_at=generated_at,
        now_date=as_of,
    )
    approvals_registry = build_step1a_active_research_anchor_registry_with_approvals(
        strategy_settings=settings,
        research_anchors_path=research_anchors_path,
        research_anchor_approvals_path=research_anchor_approvals_path,
        generated_at=generated_at,
        now_date=as_of,
    )
    return build_approval_registry_dual_read_diff(
        baseline_registry=baseline,
        approvals_registry=approvals_registry,
        baseline_registry_path=_path_str(baseline_registry_artifact_path),
        approvals_registry_path=_path_str(approvals_registry_artifact_path),
        generated_at=generated_at,
    )


def build_step1a_evidence_packet(
    *,
    strategy_settings: Mapping[str, Any] | None,
    portfolio_snapshot_text: str | None = None,
    portfolio_snapshot_path: Any = None,
    last_good_available: bool = False,
    last_good_metadata: Mapping[str, Any] | None = None,
    research_anchors_path: Any,
    research_anchor_approvals_path: Any,
    source_artifacts: Mapping[str, str] | None = None,
    generated_at: str | None = None,
    now_date: str | None = None,
    embedded_selection_out: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the Step 1A deterministic evidence packet payload (S1A-11).

    Narrow accessor for the seventh artifact switch — the FIRST grounding-input
    switch. It is the SAME derivation the full Step 1A bundle uses (``_build``
    calls this function) and mirrors the Step 1 ``build_evidence_packet_and_selection``
    core: it recompiles its own embedded active-registry selection in memory (via
    the shared ``build_embedded_active_anchor_registry_selection``), builds the
    deterministic research-anchor summary, and calls the same deterministic
    ``build_evidence_packet`` with the selected registry embedded. It carries the
    established Step 1A ``as_of`` convention (``_first_str(now_date, settings.as_of)``)
    and the same core∪satellite allowed universe as the legacy writer, so the
    payload is byte-identical to the legacy build for string-or-absent ``as_of``
    (a non-string ``as_of`` normalizes to None via ``_first_str`` and is absorbed
    by the switched writer's run-time parity guard).

    ``embedded_selection_out``: when provided, the in-memory embedded selection is
    copied into it so the caller receives the EXACT selection this packet
    embedded (no drift). ``_build`` uses it for the Step 1A embedded-selection
    bundle artifact, and — since S1A-12 — the switched Step 1 disk writer passes
    it too, so the guarded embedded-selection artifact is written from the same
    selection that produced the Step 1A packet candidate (never a second
    independent selection compile). Populating the out-param does not change the
    returned packet bytes.

    Pure: reads only the two operator YAML files (via the shared compilers /
    validators / summary builder), writes nothing, calls no LLM, reads no on-disk
    artifact as authority, and reads neither support_signals nor candidates. It
    carries no selection/permission/order authority and cannot authorize a trade.
    """
    settings = strategy_settings if isinstance(strategy_settings, Mapping) else None
    as_of = _first_str(now_date, _get(settings, "as_of"))
    allowed_universe = _allowed_buy_universe(settings)
    embedded_selection = build_embedded_active_anchor_registry_selection(
        anchors_path=research_anchors_path,
        approvals_path=research_anchor_approvals_path,
        allowed_universe=allowed_universe,
        today=as_of,
        generated_at=generated_at,
    )
    if embedded_selection_out is not None and isinstance(embedded_selection, Mapping):
        embedded_selection_out.update(embedded_selection)
    research_anchors_summary = build_research_anchors_summary(
        research_anchors_path,
        allowed_universe=allowed_universe,
        today=as_of,
    )
    return build_evidence_packet(
        strategy_settings=settings,
        portfolio_snapshot_text=portfolio_snapshot_text,
        portfolio_snapshot_path=portfolio_snapshot_path,
        last_good_available=last_good_available,
        last_good_metadata=last_good_metadata,
        now_date=as_of,
        generated_at=generated_at,
        source_artifacts=source_artifacts,
        research_anchors_summary=research_anchors_summary,
        active_anchor_registry=embedded_selection.get("selected_registry")
        if isinstance(embedded_selection, Mapping)
        else None,
    )


def build_step1a_grounding_compile_shadow_diff(
    *,
    step1a_bundle: Mapping[str, Any] | None,
    current_artifacts: Mapping[str, Any] | None,
    current_artifact_paths: Mapping[str, Any] | None = None,
    generated_at: str | None = None,
    shadow_run_error: str | None = None,
) -> dict[str, Any]:
    """Compare Step 1A shadow output against current Step 1 artifacts.

    Pure/report-only: this function reads no files, writes no files, and returns
    diagnostic parity summaries only. Mismatches are never authorization inputs.
    """
    try:
        bundle = step1a_bundle if isinstance(step1a_bundle, Mapping) else None
        bundle_artifacts = _get_mapping(bundle, "artifacts")
        current = current_artifacts if isinstance(current_artifacts, Mapping) else {}
        paths = current_artifact_paths if isinstance(current_artifact_paths, Mapping) else {}

        comparisons: dict[str, Any] = {}
        for artifact_key in _ARTIFACT_KEYS:
            current_payload = current.get(artifact_key)
            step1a_payload = bundle_artifacts.get(artifact_key) if bundle_artifacts else None
            comparisons[artifact_key] = _compare_artifact(
                artifact_key=artifact_key,
                current_payload=current_payload,
                step1a_payload=step1a_payload,
                current_artifact_path=paths.get(artifact_key),
                # A key explicitly mapped to None means current Step 1 keeps the
                # object in memory only and persists no artifact to compare.
                current_artifact_not_persisted=artifact_key in paths and paths.get(artifact_key) is None,
            )

        return _shadow_result(
            comparisons=comparisons,
            source_summary=dict(bundle.get("source_summary") or {}) if bundle else {},
            bundle_diagnostics=dict(bundle.get("diagnostics") or {}) if bundle else {},
            generated_at=generated_at,
            shadow_run_error=shadow_run_error,
            internal_error=None,
        )
    except Exception as exc:  # noqa: BLE001 - shadow diff must fail closed
        return _shadow_result(
            comparisons={},
            source_summary={},
            bundle_diagnostics={},
            generated_at=generated_at,
            shadow_run_error=shadow_run_error,
            internal_error=str(exc),
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

    # S1A-3: the bundle and the switched production writer share this single
    # accessor so the two can never drift.
    active_registry = build_step1a_active_research_anchor_registry(
        strategy_settings=settings,
        research_anchors_path=research_anchors_path,
        generated_at=generated_at,
        now_date=settings_as_of,
    )
    # S1A-4: the REPORT variant is shared with the switched production writer via
    # this single accessor. The overlay variant stays separate on purpose — it
    # feeds the with-approvals registry inside the S1A-6 accessor below.
    approvals_validation = build_step1a_research_anchor_approvals_validation(
        strategy_settings=settings,
        research_anchor_approvals_path=research_anchor_approvals_path,
        generated_at=generated_at,
        now_date=settings_as_of,
    )
    # S1A-5: the settings-anchored REPORT variant is shared with the switched
    # production writer via this single accessor. The overlay variant stays
    # separate on purpose — it is baseline-coupled and feeds the with-approvals
    # registry inside the S1A-6 accessor below.
    revocations_validation = build_step1a_research_anchor_revocations_validation(
        strategy_settings=settings,
        research_anchor_approvals_path=research_anchor_approvals_path,
        generated_at=generated_at,
        now_date=settings_as_of,
    )

    # S1A-6: the approvals-inclusive registry payload is shared with the switched
    # production writer via this single accessor so the two can never drift. The
    # accessor derives its own baseline via the S1A-3 accessor (byte-identical to
    # active_registry above) and preserves the Step 1 dual-read writer's overlay
    # flavor: approvals validated WITHOUT as_of_date, revocations baseline-coupled.
    approvals_registry = build_step1a_active_research_anchor_registry_with_approvals(
        strategy_settings=settings,
        research_anchors_path=research_anchors_path,
        research_anchor_approvals_path=research_anchor_approvals_path,
        generated_at=generated_at,
        now_date=settings_as_of,
    )
    # S1A-8: the dual-read diff payload is shared with the switched production
    # writer via this single accessor so the two can never drift. The accessor
    # re-derives its own S1A-3 baseline and S1A-6 with-approvals (byte-identical
    # to active_registry / approvals_registry above) and diffs them.
    dual_read_diff = build_step1a_approval_registry_dual_read_diff(
        strategy_settings=settings,
        research_anchors_path=research_anchors_path,
        research_anchor_approvals_path=research_anchor_approvals_path,
        baseline_registry_artifact_path=active_registry_artifact_path,
        approvals_registry_artifact_path=approvals_registry_artifact_path,
        generated_at=generated_at,
        now_date=settings_as_of,
    )
    # S1A-7: the switch-readiness disk-observer payload is shared with the
    # switched production writer via this single accessor so the two can never
    # drift. Runtime readiness stays an in-memory recompute elsewhere.
    readiness = build_step1a_approval_registry_switch_readiness(
        strategy_settings=settings,
        research_anchors_path=research_anchors_path,
        research_anchor_approvals_path=research_anchor_approvals_path,
        generated_at=generated_at,
        now_date=settings_as_of,
    )

    # S1A-11: the evidence_packet payload is shared with the switched production
    # writer via this single accessor so the two can never drift. The accessor
    # recompiles its own embedded selection (byte-identical to a fresh compile
    # here) and embeds the selected registry; the out-param lets the bundle keep
    # producing the embedded-selection artifact from that exact selection.
    step1a_embedded_selection: dict[str, Any] = {}
    evidence_packet = build_step1a_evidence_packet(
        strategy_settings=settings,
        portfolio_snapshot_text=portfolio_snapshot_text,
        portfolio_snapshot_path=portfolio_snapshot_path,
        last_good_available=last_good_available,
        last_good_metadata=last_good_metadata,
        research_anchors_path=research_anchors_path,
        research_anchor_approvals_path=research_anchor_approvals_path,
        source_artifacts=_source_artifacts(
            strategy_settings_path=strategy_settings_path,
            portfolio_snapshot_path=portfolio_snapshot_path,
            last_good_metadata_path=last_good_metadata_path,
            research_anchors_path=research_anchors_path,
            research_anchor_approvals_path=research_anchor_approvals_path,
        ),
        generated_at=generated_at,
        now_date=settings_as_of,
        embedded_selection_out=step1a_embedded_selection,
    )
    embedded_selection = step1a_embedded_selection or build_embedded_active_anchor_registry_selection(
        anchors_path=research_anchors_path,
        approvals_path=research_anchor_approvals_path,
        allowed_universe=allowed_universe,
        today=settings_as_of,
        generated_at=generated_at,
    )

    observatory = build_grounding_status_observatory(
        evidence_packet=evidence_packet,
        # Match the current Step 1 observatory writer: it summarizes already
        # written artifacts only and does not consume the in-memory selection as
        # authority. The selection remains a separate Step 1A bundle artifact.
        embedded_registry_selection=None,
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


def _compare_artifact(
    *,
    artifact_key: str,
    current_payload: Any,
    step1a_payload: Any,
    current_artifact_path: Any,
    current_artifact_not_persisted: bool = False,
) -> dict[str, Any]:
    if not isinstance(current_payload, Mapping):
        if current_artifact_not_persisted:
            return _skipped_comparison(
                artifact_key=artifact_key,
                reason="not_persisted_by_current_step1",
                note=(
                    "Current Step 1 holds this object in memory only and persists "
                    "no exact artifact to compare. Skipped by design; the skip does "
                    "not indicate a missing or malformed artifact."
                ),
                current_artifact_path=current_artifact_path,
            )
        return _skipped_comparison(
            artifact_key=artifact_key,
            reason="current_step1_artifact_unavailable_or_malformed",
            current_artifact_path=current_artifact_path,
        )
    if not isinstance(step1a_payload, Mapping):
        return _skipped_comparison(
            artifact_key=artifact_key,
            reason="step1a_shadow_artifact_unavailable_or_malformed",
            current_artifact_path=current_artifact_path,
        )

    current_summary = _semantic_summary(artifact_key, current_payload)
    step1a_summary = _semantic_summary(artifact_key, step1a_payload)
    semantic_match = current_summary == step1a_summary
    return {
        "artifact_key": artifact_key,
        "comparison_skipped": False,
        "semantic_match": semantic_match,
        "status": "match" if semantic_match else "mismatch",
        "current_artifact_path": _path_str(current_artifact_path),
        "current_summary": current_summary,
        "step1a_summary": step1a_summary,
        "current_summary_sha256": _sha256_of(current_summary),
        "step1a_summary_sha256": _sha256_of(step1a_summary),
        "differences": [] if semantic_match else _summary_differences(current_summary, step1a_summary),
    }


def _skipped_comparison(
    *,
    artifact_key: str,
    reason: str,
    current_artifact_path: Any,
    note: str = "",
) -> dict[str, Any]:
    return {
        "artifact_key": artifact_key,
        "comparison_skipped": True,
        "skip_reason": reason,
        "skip_note": note,
        "semantic_match": False,
        "status": "skipped",
        "current_artifact_path": _path_str(current_artifact_path),
        "current_summary": {},
        "step1a_summary": {},
        "current_summary_sha256": None,
        "step1a_summary_sha256": None,
        "differences": [],
    }


def _shadow_result(
    *,
    comparisons: Mapping[str, Any],
    source_summary: Mapping[str, Any],
    bundle_diagnostics: Mapping[str, Any],
    generated_at: str | None,
    shadow_run_error: str | None,
    internal_error: str | None,
) -> dict[str, Any]:
    comparison_values = [c for c in comparisons.values() if isinstance(c, Mapping)]
    mismatches = [
        c.get("artifact_key")
        for c in comparison_values
        if c.get("comparison_skipped") is not True and c.get("semantic_match") is not True
    ]
    skipped = [c.get("artifact_key") for c in comparison_values if c.get("comparison_skipped") is True]
    failed = bool(shadow_run_error or internal_error)
    if failed:
        status = "failed"
    elif mismatches:
        status = "mismatch"
    elif skipped:
        # Skips never count as mismatches, but they must not read as full parity
        # either: "pass" is reserved for a complete comparison set.
        status = "pass_with_skips"
    else:
        status = "pass"
    comparison_complete = not failed and not skipped and len(comparison_values) == len(_ARTIFACT_KEYS)

    return {
        "schema_version": SHADOW_DIFF_SCHEMA_VERSION,
        "is_llm_generated": False,
        "generated_at": generated_at,
        "report_only": True,
        "permission_effect": "none",
        "not_authorization": True,
        "not_execution_authorization": True,
        "consumed_by_gates": False,
        "consumed_by_order_path": False,
        "consumed_by_downstream": False,
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
        "not_budget_input": True,
        "not_allocation_input": True,
        "shadow_run": True,
        "production_artifacts_unchanged": True,
        # S1A-5.1: precise migration-scope markers. The single ambiguous
        # production_uses_step1a_outputs boolean became stale once S1A-3/4/5
        # switched three report-only artifact WRITERS to the Step 1A source;
        # these fields state exactly what is and is not switched.
        "production_artifact_paths_switched": False,
        "step1a_writer_source_artifacts": list(STEP1A_WRITER_SOURCE_ARTIFACTS),
        "step1a_writer_source_artifact_count": len(STEP1A_WRITER_SOURCE_ARTIFACTS),
        # S1A-11 flipped evidence_packet_uses_step1a_output to True; S1A-12 flips
        # embedded_selection_uses_step1a_output to True (the selection DISK
        # artifact writer now sources from the Step 1A capture behind a
        # full-payload parity guard lineage-coupled to the packet guard). The
        # runtime-authority markers stay False: the guards prove byte-identical
        # payloads, support_signals' disk read-back grounds off an equivalent
        # registry, and the selection artifact is consumed by nothing at runtime.
        "evidence_packet_uses_step1a_output": True,
        "embedded_selection_uses_step1a_output": True,
        "support_signals_uses_step1a_output": False,
        "readiness_uses_step1a_output": False,
        "order_path_uses_step1a_output": False,
        "runtime_authority_uses_step1a_output": False,
        "step1a_writer_source_note": (
            "The S1A writer switches route the artifacts listed in "
            "step1a_writer_source_artifacts to the Step 1A source; artifact "
            "paths, schemas, and payload bytes are unchanged. The list is "
            "code-level design state — per-run provenance, including any legacy "
            "fallback, is recorded in step1a_artifact_switch_status.json. The "
            "evidence_packet writer (S1A-11) is Step 1A-sourced behind a "
            "strict runtime-parity guard: the Step 1A payload reaches disk only "
            "when the runtime subtree is byte-identical (only generated_at "
            "normalized) with no report-only differences, so support_signals — "
            "which grounds off the disk read-back — still consumes the same "
            "evidence_packet.active_anchor_registry it does today. The embedded "
            "registry selection DISK artifact (S1A-12) is likewise Step "
            "1A-sourced behind a full-payload parity guard lineage-coupled to "
            "the packet guard (legacy bytes on any divergence or packet "
            "fallback; filename unchanged). Readiness is unswitched, the "
            "grounding observatory remains the unswitched production "
            "summarizer, and Step 2/3/4/final/weekly and all order paths "
            "consume no Step 1A output; no execution authority changed."
        ),
        "safe_to_ignore": True,
        "comparison_status": status,
        # parity_passed is strict: only a complete, skip-free comparison set that
        # fully matches counts as parity. available_comparisons_passed reports the
        # weaker "everything we could compare matched" signal.
        "parity_passed": status == "pass",
        "available_comparisons_passed": status in ("pass", "pass_with_skips"),
        "comparison_complete": comparison_complete,
        "mismatch_artifacts": [m for m in mismatches if isinstance(m, str)],
        "skipped_artifacts": [s for s in skipped if isinstance(s, str)],
        "comparisons": dict(comparisons),
        "source_summary": dict(source_summary),
        "diagnostics": {
            "shadow_run_failed": failed,
            "shadow_run_error": shadow_run_error or "",
            "internal_error": internal_error or "",
            "diagnostics_incomplete": not comparison_complete,
            "mismatch_count": len(mismatches),
            "skipped_count": len(skipped),
            # Populated by the shadow writer: comparison_input_paths are the
            # intended comparison inputs; files_read lists only artifacts actually
            # loaded (empty on failure rather than guessing).
            "comparison_input_paths": [],
            "files_read": [],
            "optional_inputs_read": [],
            "optional_inputs_missing": [],
            "files_written": [],
            "bundle_diagnostics": dict(bundle_diagnostics),
            "mismatch_is_diagnostic_only": True,
            "failure_is_diagnostic_only": True,
            "production_artifacts_unchanged": True,
            # Mirrors the top-level S1A-5.1 scope markers (kept minimal here).
            "production_artifact_paths_switched": False,
            "step1a_writer_source_artifacts": list(STEP1A_WRITER_SOURCE_ARTIFACTS),
            "runtime_authority_uses_step1a_output": False,
        },
        "safety_invariants": {
            "no_artifact_path_switch": True,
            "no_downstream_consumer": True,
            "no_runtime_readiness_change": True,
            "no_evidence_packet_runtime_change": True,
            "no_support_signals_runtime_change": True,
            "no_registry_compiler_runtime_change": True,
            "no_candidate_authority": True,
            "candidate_sha256_audit_only": True,
            "approvals_not_authority_from_shadow_diff": True,
            "revocations_not_authority_from_shadow_diff": True,
            "support_signals_not_authority_from_shadow_diff": True,
            "no_new_buy_permission": True,
            "no_order_compilation_permission": True,
            "no_step4_enablement": True,
            "no_final_execution": True,
            "no_weekly_automation_change": True,
            "no_broker_live_execution": True,
            "no_executable_order_authority": True,
        },
    }


def _semantic_summary(artifact_key: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    if artifact_key == "active_research_anchor_registry":
        return _registry_summary(payload, include_revocations=False)
    if artifact_key == "active_research_anchor_registry_with_approvals":
        return _registry_summary(payload, include_revocations=True)
    if artifact_key == "research_anchor_approvals_validation":
        return _approvals_validation_summary(payload)
    if artifact_key == "research_anchor_revocations_validation":
        return _revocations_validation_summary(payload)
    if artifact_key == "approval_registry_dual_read_diff":
        return _dual_read_diff_summary(payload)
    if artifact_key == "approval_registry_switch_readiness":
        return _readiness_summary(payload)
    if artifact_key == "embedded_active_anchor_registry_selection":
        return _embedded_selection_summary(payload)
    if artifact_key == "evidence_packet":
        return _evidence_packet_summary(payload)
    if artifact_key == "grounding_status_observatory":
        return _observatory_summary(payload)
    return _generic_summary(payload)


def _registry_summary(payload: Mapping[str, Any], *, include_revocations: bool) -> dict[str, Any]:
    counts = _get_mapping(payload, "counts") or {}
    summary = {
        "schema_version": payload.get("schema_version"),
        "registry_valid": payload.get("registry_valid") is True,
        "active_anchor_count": _count_value(counts, "active", payload.get("active_anchors")),
        "inactive_anchor_count": len(_as_list(payload.get("inactive_anchors"))),
        "expired_count": int(counts.get("expired") or 0),
        "invalid_count": int(counts.get("invalid") or 0),
        "blocker_count": len(_as_list(payload.get("registry_blockers")))
        + len(_as_list(payload.get("duplicate_blockers"))),
        "warning_count": len(_as_list(payload.get("warnings"))),
        "active_anchor_ids": _anchor_ids(payload.get("active_anchors")),
        "source_manifest": _source_manifest_summary(payload),
    }
    if include_revocations:
        summary.update(
            {
                "revoked_count": int(counts.get("revoked") or 0),
                "revocations_applied_count": len(_as_list(payload.get("revocations_applied"))),
                "revocations_pending_count": len(_as_list(payload.get("revocations_pending"))),
                "revocation_problem_count": len(_as_list(payload.get("revocation_problems"))),
                "approved_active_count": int(counts.get("approved_active") or 0),
                "baseline_active_count": int(counts.get("baseline_active") or 0),
            }
        )
    return summary


def _approvals_validation_summary(payload: Mapping[str, Any]) -> dict[str, Any]:
    counts = _get_mapping(payload, "counts") or {}
    return {
        "schema_version": payload.get("schema_version"),
        "source_present": payload.get("source_present") is True,
        "source_valid": payload.get("source_valid") is True,
        "approvals_valid": payload.get("source_valid") is True
        and len(_as_list(payload.get("manifest_errors"))) == 0,
        "approval_count": int(counts.get("approvals") or 0),
        "would_activate_count": int(counts.get("would_activate") or 0),
        "valid_report_only_count": int(counts.get("valid_report_only") or 0),
        "expired_count": int(counts.get("expired") or 0),
        "rejected_count": int(counts.get("rejected") or 0),
        "blocker_count": len(_as_list(payload.get("manifest_errors"))),
        "warning_count": len(_as_list(payload.get("manifest_warnings"))),
        "source_sha256": payload.get("source_sha256"),
    }


def _revocations_validation_summary(payload: Mapping[str, Any]) -> dict[str, Any]:
    counts = _get_mapping(payload, "counts") or {}
    return {
        "schema_version": payload.get("schema_version"),
        "source_present": payload.get("source_present") is True,
        "source_valid": payload.get("source_valid") is True,
        "revocations_valid": payload.get("revocations_valid") is True,
        "checked_count": int(counts.get("checked") or 0),
        "valid_count": int(counts.get("valid") or 0),
        "valid_active_count": int(counts.get("valid_active") or 0),
        "invalid_count": int(counts.get("invalid") or 0),
        "pending_future_count": int(counts.get("pending_future") or 0),
        "blocker_count": len(_as_list(payload.get("blockers"))),
        "warning_count": len(_as_list(payload.get("warnings"))),
        "source_sha256": payload.get("source_sha256"),
    }


def _dual_read_diff_summary(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": payload.get("schema_version"),
        "registry_valid_baseline": payload.get("registry_valid_baseline") is True,
        "registry_valid_with_approvals": payload.get("registry_valid_with_approvals") is True,
        "baseline_active_count": len(_as_list(payload.get("baseline_active_anchor_ids"))),
        "approvals_active_count": len(_as_list(payload.get("approvals_inclusive_active_anchor_ids"))),
        "added_by_approvals_count": len(_as_list(payload.get("added_by_approvals"))),
        "removed_or_deactivated_count": len(_as_list(payload.get("removed_or_deactivated"))),
        "changed_existing_count": len(_as_list(payload.get("changed_existing_anchors"))),
        "duplicate_blocker_count": len(_as_list(payload.get("duplicate_blockers"))),
        "blocker_count": len(_as_list(payload.get("blockers"))),
        "warning_count": len(_as_list(payload.get("warnings"))),
        "baseline_registry_sha256": payload.get("baseline_registry_sha256"),
        "approvals_registry_sha256": payload.get("approvals_registry_sha256"),
    }


def _readiness_summary(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": payload.get("schema_version"),
        "readiness_ready": payload.get("ready") is True,
        "switch_target": payload.get("switch_target"),
        "baseline_fallback_safe": payload.get("baseline_fallback_safe") is True,
        "fail_closed_empty_required": payload.get("fail_closed_empty_required") is True,
        "fallback_reason": _fallback_reason(payload),
        "failed_condition_count": len(_as_list(payload.get("failed_conditions"))),
        "condition_count": len(_as_list(payload.get("conditions"))),
        "registry_valid_baseline": payload.get("registry_valid_baseline") is True,
        "registry_valid_with_approvals": payload.get("registry_valid_with_approvals") is True,
        "source_hashes": payload.get("source_hashes") if isinstance(payload.get("source_hashes"), Mapping) else {},
    }


def _embedded_selection_summary(payload: Mapping[str, Any]) -> dict[str, Any]:
    selected = _get_mapping(payload, "selected_registry")
    readiness = _get_mapping(payload, "readiness")
    return {
        "schema_version": payload.get("schema_version"),
        "selected_source": payload.get("selected_source"),
        "registry_valid": selected.get("registry_valid") is True if selected else False,
        "active_anchor_count": _registry_active_count(selected),
        "fallback_reason": _fallback_reason(readiness),
        "switch_target": readiness.get("switch_target") if readiness else None,
        "readiness_ready": readiness.get("ready") is True if readiness else False,
        "selected_source_manifest": _source_manifest_summary(selected),
    }


def _evidence_packet_summary(payload: Mapping[str, Any]) -> dict[str, Any]:
    # S1A-10: compare the runtime-relevant subtree EXACTLY after normalizing only
    # generated_at (production stamps a wall-clock timestamp threaded into the
    # embedded registry; a Step 1A recompute uses generated_at=None). The old
    # summary captured only counts/selected_source and could false-pass on a
    # differing anchor set / content_sha256 / source hash; embedding the exact
    # normalized active_anchor_registry closes that class. This is the guard
    # reference the future S1A-11 evidence_packet disk-writer switch will reuse.
    normalized, normalized_paths, diagnostics = normalize_evidence_packet_for_parity(payload)
    registry = _get_mapping(normalized, "active_anchor_registry")
    return {
        "schema_version": normalized.get("schema_version"),
        "source": normalized.get("source"),
        "report_only": normalized.get("report_only"),
        "is_llm_generated": normalized.get("is_llm_generated"),
        "strategy_settings_hash": normalized.get("strategy_settings_hash"),
        "universe": normalized.get("universe"),
        "budget_settings": normalized.get("budget_settings"),
        "data_gaps": normalized.get("data_gaps"),
        # Exact, generated_at-normalized embedded registry — the runtime-relevant
        # grounding source support_signals consumes in memory.
        "active_anchor_registry": registry,
        # Coarse fields retained for quick human reading of the shadow diff.
        "active_anchor_registry_selected_source": _selected_registry_source(registry),
        "active_anchor_registry_valid": registry.get("registry_valid") is True if registry else False,
        "active_anchor_count": _registry_active_count(registry),
        # Parity diagnostics: prove ONLY generated_at was normalized and no
        # unknown run-varying timestamp leaked into the runtime subtree.
        "parity_normalized_paths": normalized_paths,
        "parity_unknown_runtime_timestamp_fields": diagnostics["unknown_runtime_timestamp_fields"],
    }


def _observatory_summary(payload: Mapping[str, Any]) -> dict[str, Any]:
    selected = _get_mapping(payload, "selected_registry") or {}
    diagnostics = _get_mapping(payload, "diagnostics") or {}
    candidates = _get_mapping(payload, "candidates_summary") or {}
    support = _get_mapping(payload, "support_grounding_summary") or {}
    return {
        "schema_version": payload.get("schema_version"),
        "report_only": payload.get("report_only") is True,
        "not_authorization": payload.get("not_authorization") is True,
        "not_execution_authorization": payload.get("not_execution_authorization") is True,
        "permission_effect": payload.get("permission_effect"),
        "selected_registry": {
            "selected_source": selected.get("selected_source"),
            "readiness_switch_target": selected.get("readiness_switch_target"),
            "registry_valid": selected.get("registry_valid"),
            "active_anchor_count": selected.get("active_anchor_count"),
            "inactive_anchor_count": selected.get("inactive_anchor_count"),
            "baseline_derived_active_count": selected.get("baseline_derived_active_count"),
            "approval_derived_active_count": selected.get("approval_derived_active_count"),
            "revoked_count": selected.get("revoked_count"),
            "pending_revocation_count": selected.get("pending_revocation_count"),
        },
        "diagnostics_incomplete": diagnostics.get("diagnostics_incomplete") is True,
        "candidate_count": candidates.get("candidate_count"),
        "grounded_claim_count": support.get("grounded_claim_count"),
        "consumed_by_gates": payload.get("consumed_by_gates") is True,
        "consumed_by_order_path": payload.get("consumed_by_order_path") is True,
        "cannot_affect_allowed_actions": payload.get("cannot_affect_allowed_actions") is True,
        "cannot_affect_registry_selection": payload.get("cannot_affect_registry_selection") is True,
    }


def _generic_summary(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": payload.get("schema_version"),
        "report_only": payload.get("report_only") is True,
        "not_authorization": payload.get("not_authorization") is True,
        "permission_effect": payload.get("permission_effect"),
        "sha256": _sha256_of(_strip_generated_at(payload)),
    }


def _summary_differences(current_summary: Mapping[str, Any], step1a_summary: Mapping[str, Any]) -> list[dict[str, Any]]:
    keys = sorted(set(current_summary) | set(step1a_summary))
    return [
        {
            "field": key,
            "current": current_summary.get(key),
            "step1a": step1a_summary.get(key),
        }
        for key in keys
        if current_summary.get(key) != step1a_summary.get(key)
    ]


def _fallback_reason(payload: Mapping[str, Any] | None) -> str:
    if not isinstance(payload, Mapping):
        return "payload_unavailable"
    if payload.get("ready") is True:
        return ""
    failed = _as_list(payload.get("failed_conditions"))
    if failed:
        return ",".join(str(item) for item in failed if isinstance(item, str))
    target = payload.get("switch_target")
    return str(target) if isinstance(target, str) else ""


def _source_manifest_summary(registry: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(registry, Mapping):
        return []
    out: list[dict[str, Any]] = []
    for entry in _as_list(registry.get("source_manifest")):
        if not isinstance(entry, Mapping):
            continue
        out.append(
            {
                "source_id": entry.get("source_id"),
                "present": entry.get("present") is True,
                "sha256": entry.get("sha256"),
            }
        )
    return out


def _selected_registry_source(registry: Mapping[str, Any] | None) -> str:
    if not isinstance(registry, Mapping):
        return "unavailable"
    for entry in _as_list(registry.get("source_manifest")):
        if not isinstance(entry, Mapping):
            continue
        source_id = entry.get("source_id")
        if isinstance(source_id, str) and source_id == "operator_research_anchor_approvals_yaml":
            return "approvals_inclusive"
    return "baseline_or_empty"


def _anchor_ids(rows: Any) -> list[str]:
    return sorted(
        row.get("anchor_id")
        for row in _as_list(rows)
        if isinstance(row, Mapping) and isinstance(row.get("anchor_id"), str)
    )


def _registry_active_count(registry: Mapping[str, Any] | None) -> int:
    if not isinstance(registry, Mapping):
        return 0
    counts = _get_mapping(registry, "counts")
    if counts and isinstance(counts.get("active"), int):
        return int(counts["active"])
    return len(_as_list(registry.get("active_anchors")))


def _count_value(counts: Mapping[str, Any], key: str, fallback_rows: Any) -> int:
    value = counts.get(key)
    if isinstance(value, int):
        return int(value)
    return len(_as_list(fallback_rows))


def _get_mapping(value: Mapping[str, Any] | None, key: str) -> Mapping[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    nested = value.get(key)
    return nested if isinstance(nested, Mapping) else None


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _strip_generated_at(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {k: _strip_generated_at(v) for k, v in value.items() if k != "generated_at"}
    if isinstance(value, list):
        return [_strip_generated_at(item) for item in value]
    return value


def _sha256_of(value: Any) -> str | None:
    try:
        serialized = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    except (TypeError, ValueError):
        return None
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


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
