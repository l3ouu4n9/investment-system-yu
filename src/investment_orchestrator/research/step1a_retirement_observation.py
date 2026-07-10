"""Pure, report-only Step 1A retirement-observation builder.

This module intentionally contains no file, environment, subprocess, or LLM
access.  Its caller supplies already-resolved current-run mappings and simple
artifact-state facts after their final writes.  The result is an observation
for a future *offline* review only; it is neither a readiness assessment nor a
permission, gate, order, or execution input.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from typing import Any


SCHEMA_VERSION = "step1a_retirement_observation_v1"
CLASSIFICATION_CONTRACT_VERSION = "step1a_retirement_observation_classification_v1"
COVERAGE_CONTRACT_VERSION = "step1a_retirement_observation_coverage_v1"

_EXPECTED_SCHEMA_VERSIONS = {
    "switch_status": "step1a_artifact_switch_status_v1",
    "shadow_diff": "step1a_grounding_compile_shadow_diff_v1",
    "evidence_packet": "evidence_packet_v1",
    "embedded_selection": "embedded_active_anchor_registry_selection_v1",
    "compiled_support_signals": "compiled_support_signals_v1",
    "grounding_status_observatory": "grounding_status_observatory_v1",
}

_SOURCE_HASH_IDS = {
    "research_anchors_sha256": "operator_research_anchors_yaml",
    "research_anchor_approvals_sha256": "operator_research_anchor_approvals_yaml",
    "research_anchor_revocations_sha256": "operator_research_anchor_revocations_yaml",
}

# Prefixes emitted by the existing packet / embedded-selection writers and
# guards.  The builder keeps only these stable tokens; dynamic suffixes (often
# diff paths or exception text) never enter the observation.
_KNOWN_ERROR_PREFIXES = (
    "step1a_evidence_packet_parity_result_unavailable",
    "step1a_evidence_packet_unknown_runtime_timestamp:",
    "step1a_evidence_packet_parity_mismatch:",
    "step1a_evidence_packet_unexpected_normalized_path:",
    "step1a_evidence_packet_report_only_difference:",
    "legacy_evidence_packet_build_failed:",
    "legacy_evidence_packet_build_failed_upstream",
    "evidence_packet_write_failed:",
    "evidence_packet_write_failed_upstream",
    "step1a_embedded_selection_parity_result_unavailable",
    "step1a_embedded_selection_unknown_runtime_timestamp:",
    "step1a_embedded_selection_parity_mismatch:",
    "step1a_embedded_selection_unexpected_normalized_path:",
    "step1a_embedded_selection_guard_failed:",
    "step1a_embedded_selection_skipped_evidence_packet_fallback",
    "legacy_selection_capture_empty",
    "step1a_accessor_failed:",
    "embedded_selection_write_failed:",
    "no_write_invocation_recorded",
)


def extract_canonical_error_token(error_summary: Any) -> dict[str, Any]:
    """Return a path/content-free canonical error classification.

    An empty summary stays empty.  A recognized writer/guard prefix becomes
    its prefix without any dynamic suffix.  Any other non-empty summary is
    represented only by ``unknown_error_token`` plus its SHA-256 digest.
    """
    if not isinstance(error_summary, str) or not error_summary:
        return {
            "canonical_error_token": "",
            "unknown_error_present": False,
            "error_summary_sha256": None,
        }
    for prefix in _KNOWN_ERROR_PREFIXES:
        if error_summary.startswith(prefix):
            return {
                "canonical_error_token": prefix.rstrip(":"),
                "unknown_error_present": False,
                "error_summary_sha256": None,
            }
    return {
        "canonical_error_token": "unknown_error_token",
        "unknown_error_present": True,
        "error_summary_sha256": _sha256_text(error_summary),
    }


def build_step1a_retirement_observation(
    *,
    generated_at: Any,
    code_identity: Mapping[str, Any] | None,
    switch_status: Mapping[str, Any] | None,
    shadow_diff: Mapping[str, Any] | None,
    evidence_packet: Mapping[str, Any] | None,
    embedded_selection: Mapping[str, Any] | None,
    compiled_support_signals: Mapping[str, Any] | None,
    grounding_status_observatory: Mapping[str, Any] | None,
    research_availability: Mapping[str, Any] | None,
    evidence_packet_artifact_present: Any,
    evidence_packet_artifact_parseable: Any,
    compiled_support_signals_artifact_present: Any,
    compiled_support_signals_artifact_parseable: Any,
    observatory_integration_result: Any,
) -> dict[str, Any]:
    """Build one deterministic, fail-closed retirement observation.

    The function is deliberately pure: every source mapping, final artifact
    state, generated timestamp, permission observation, and code identity is
    injected by the report-only writer.  It reads no files, environment,
    subprocesses, clocks, or LLMs; it does not mutate any supplied input.
    """
    try:
        return _build_step1a_retirement_observation(
            generated_at=generated_at,
            code_identity=code_identity,
            switch_status=switch_status,
            shadow_diff=shadow_diff,
            evidence_packet=evidence_packet,
            embedded_selection=embedded_selection,
            compiled_support_signals=compiled_support_signals,
            grounding_status_observatory=grounding_status_observatory,
            research_availability=research_availability,
            evidence_packet_artifact_present=evidence_packet_artifact_present,
            evidence_packet_artifact_parseable=evidence_packet_artifact_parseable,
            compiled_support_signals_artifact_present=compiled_support_signals_artifact_present,
            compiled_support_signals_artifact_parseable=compiled_support_signals_artifact_parseable,
            observatory_integration_result=observatory_integration_result,
        )
    except Exception:  # noqa: BLE001 - observation must never interrupt Step 1
        return _minimal_incomplete_observation(generated_at)


def _build_step1a_retirement_observation(
    **sources: Any,
) -> dict[str, Any]:
    missing: list[str] = []
    compatibility_blockers: list[str] = []

    generated_at = _string_or_none(sources["generated_at"])
    if generated_at is None:
        _add_missing(missing, "observation_identity.generated_at")

    source_mappings = {
        name: _mapping_or_none(sources[name], name, missing)
        for name in _EXPECTED_SCHEMA_VERSIONS
    }
    availability = _mapping_or_none(
        sources["research_availability"], "research_availability", missing
    )

    contract_versions: dict[str, str | None] = {}
    for name, expected_version in _EXPECTED_SCHEMA_VERSIONS.items():
        mapping = source_mappings[name]
        version = _string_or_none(mapping.get("schema_version")) if mapping else None
        contract_versions[name] = version
        if version is None:
            _add_missing(missing, f"{name}.schema_version")
            _add_unique(compatibility_blockers, f"schema_version_missing:{name}")
        elif version != expected_version:
            _add_unique(compatibility_blockers, f"schema_version_unexpected:{name}")

    code_identity_observation = _code_identity_observation(sources["code_identity"], missing)
    configuration_hashes = _configuration_hashes(source_mappings["evidence_packet"], missing)
    composite_config_fingerprint = _sha256_value(configuration_hashes)
    if composite_config_fingerprint is None:
        _add_missing(missing, "coverage_identity.composite_config_fingerprint")

    input_state = _input_state_observations(
        evidence_packet=source_mappings["evidence_packet"],
        embedded_selection=source_mappings["embedded_selection"],
        grounding_status_observatory=source_mappings["grounding_status_observatory"],
        research_availability=availability,
        missing=missing,
    )
    writer_outcomes = _writer_outcomes(source_mappings["switch_status"], missing)
    fallback_error_tokens = sorted(
        {
            outcome["canonical_error_token"]
            for outcome in writer_outcomes.values()
            if outcome["fallback_used"] is True and outcome["canonical_error_token"]
        }
    )
    guard_summaries = _guard_summaries(
        shadow_diff=source_mappings["shadow_diff"],
        writer_outcomes=writer_outcomes,
        missing=missing,
    )
    shadow_and_observatory = _shadow_and_observatory_observation(
        shadow_diff=source_mappings["shadow_diff"],
        observatory_integration_result=sources["observatory_integration_result"],
        missing=missing,
    )
    grounding_observation = _grounding_observation(
        evidence_packet=source_mappings["evidence_packet"],
        compiled_support_signals=source_mappings["compiled_support_signals"],
        research_availability=availability,
        evidence_packet_artifact_present=sources["evidence_packet_artifact_present"],
        evidence_packet_artifact_parseable=sources["evidence_packet_artifact_parseable"],
        compiled_support_signals_artifact_present=sources[
            "compiled_support_signals_artifact_present"
        ],
        compiled_support_signals_artifact_parseable=sources[
            "compiled_support_signals_artifact_parseable"
        ],
        missing=missing,
    )
    permission_context = _permission_context_observation(availability, missing)

    coverage_material = {
        "coverage_contract_version": COVERAGE_CONTRACT_VERSION,
        "code_identity": code_identity_observation,
        "contract_versions": contract_versions,
        "composite_config_fingerprint": composite_config_fingerprint,
        "input_state_observations": input_state,
        "writer_outcome_classes": {
            key: {
                "final_writer_source": outcome["final_writer_source"],
                "fallback_used": outcome["fallback_used"],
                "canonical_error_token": outcome["canonical_error_token"],
            }
            for key, outcome in writer_outcomes.items()
        },
        "shadow_class": {
            key: shadow_and_observatory[key]
            for key in ("comparison_status", "parity_passed", "comparison_complete")
        },
        "grounding_class": {
            "evidence_packet_final_artifact_present": grounding_observation[
                "evidence_packet_final_artifact_present"
            ],
            "compiled_support_signals_present": grounding_observation[
                "compiled_support_signals_present"
            ],
            "grounded_memo_support_present": grounding_observation[
                "grounded_memo_support_present"
            ],
        },
        "permission_class": {
            "research_availability_state": permission_context["research_availability_state"],
            "new_buy_allowed": permission_context["new_buy_allowed"],
            "order_compilation_allowed": permission_context["order_compilation_allowed"],
        },
    }
    coverage_key = _sha256_value(coverage_material)

    outcome_summary = {
        "writer_outcomes": writer_outcomes,
        "shadow_and_observatory": shadow_and_observatory,
        "grounding_observation": grounding_observation,
        "permission_context_observation": permission_context,
    }
    observation_id = (
        _sha256_value(
            {
                "generated_at": generated_at,
                "coverage_key": coverage_key,
                "current_outcome_summary": outcome_summary,
            }
        )
        if generated_at is not None and coverage_key is not None
        else None
    )
    if observation_id is None:
        _add_missing(missing, "observation_identity.observation_id")

    missing = sorted(set(missing))
    compatibility_blockers = sorted(set(compatibility_blockers))
    complete = not missing and not compatibility_blockers
    return {
        "schema_version": SCHEMA_VERSION,
        "is_llm_generated": False,
        "report_only": True,
        "not_authorization": True,
        "not_execution_authorization": True,
        "permission_effect": "none",
        "consumed_by_gates": False,
        "consumed_by_order_path": False,
        "consumed_by_downstream": False,
        "safe_to_ignore": True,
        "assessment_state": "observation_only",
        "identity_semantics": {
            "observation_id": (
                "Identifies this physical current-run observation instance; it is not a "
                "logical evidence-class identifier."
            ),
            "coverage_key": (
                "Identifies a deterministic logical evidence class for future offline "
                "deduplication only; it grants no authority."
            ),
        },
        "classification_contract_version": CLASSIFICATION_CONTRACT_VERSION,
        "observation_identity": {
            "observation_id": observation_id,
            "generated_at": generated_at,
        },
        "coverage_identity": {
            "coverage_key": coverage_key,
            "composite_config_fingerprint": composite_config_fingerprint,
        },
        "code_identity": code_identity_observation,
        "contract_versions": contract_versions,
        "configuration_hashes": configuration_hashes,
        "input_state_observations": input_state,
        "writer_outcomes": writer_outcomes,
        "fallback_error_tokens": fallback_error_tokens,
        "guard_summaries": guard_summaries,
        "shadow_and_observatory_observation": shadow_and_observatory,
        "grounding_observation": grounding_observation,
        "permission_context_observation": permission_context,
        "observation_completeness": "complete" if complete else "incomplete",
        "missing_observation_fields": missing,
        "compatibility_blockers": compatibility_blockers,
    }


def _code_identity_observation(value: Any, missing: list[str]) -> dict[str, Any]:
    identity = _mapping_or_none(value, "code_identity", missing)
    state = _string_or_none(identity.get("git_state")) if identity else None
    commit = _string_or_none(identity.get("git_commit")) if identity else None
    if state not in {"clean", "dirty", "unavailable"}:
        _add_missing(missing, "code_identity.git_state")
        state = "unavailable"
        commit = None
    if state == "clean" and commit is None:
        _add_missing(missing, "code_identity.git_commit")
    if state == "unavailable":
        commit = None
    return {
        "git_commit": commit,
        "git_state": state,
        "code_version_usable_for_evidence": state == "clean" and commit is not None,
    }


def _configuration_hashes(
    evidence_packet: Mapping[str, Any] | None,
    missing: list[str],
) -> dict[str, str | None]:
    strategy_hash = _string_or_none(evidence_packet.get("strategy_settings_hash")) if evidence_packet else None
    if strategy_hash is None:
        _add_missing(missing, "evidence_packet.strategy_settings_hash")
    hashes: dict[str, str | None] = {"strategy_settings_hash": strategy_hash}
    manifest = _mapping_or_none(
        evidence_packet.get("active_anchor_registry") if evidence_packet else None,
        "evidence_packet.active_anchor_registry",
        missing,
    )
    entries = manifest.get("source_manifest") if manifest else None
    source_hashes: dict[str, str] = {}
    if isinstance(entries, list):
        for entry in entries:
            if not isinstance(entry, Mapping):
                continue
            source_id = _string_or_none(entry.get("source_id"))
            source_hash = _string_or_none(entry.get("sha256"))
            if source_id is not None and source_hash is not None:
                source_hashes[source_id] = source_hash
    else:
        _add_missing(missing, "evidence_packet.active_anchor_registry.source_manifest")
    for field, source_id in _SOURCE_HASH_IDS.items():
        value = source_hashes.get(source_id)
        hashes[field] = value
        if value is None:
            _add_missing(missing, f"evidence_packet.active_anchor_registry.{field}")
    return hashes


def _input_state_observations(
    *,
    evidence_packet: Mapping[str, Any] | None,
    embedded_selection: Mapping[str, Any] | None,
    grounding_status_observatory: Mapping[str, Any] | None,
    research_availability: Mapping[str, Any] | None,
    missing: list[str],
) -> dict[str, Any]:
    strategy_summary = _mapping_or_none(
        evidence_packet.get("strategy_settings_summary") if evidence_packet else None,
        "evidence_packet.strategy_settings_summary",
        missing,
    )
    as_of = _string_or_none(strategy_summary.get("as_of")) if strategy_summary else None
    if as_of is None:
        _add_missing(missing, "evidence_packet.strategy_settings_summary.as_of")

    manifest = _mapping_or_none(
        evidence_packet.get("active_anchor_registry") if evidence_packet else None,
        "evidence_packet.active_anchor_registry",
        missing,
    )
    manifest_entries = manifest.get("source_manifest") if manifest else None
    approvals_state = _manifest_state(manifest_entries, "operator_research_anchor_approvals_yaml")
    revocations_state = _manifest_state(manifest_entries, "operator_research_anchor_revocations_yaml")
    if approvals_state == "unknown":
        _add_missing(missing, "evidence_packet.active_anchor_registry.approvals_present")
    if revocations_state == "unknown":
        _add_missing(missing, "evidence_packet.active_anchor_registry.revocations_state")

    selected_source = _string_or_none(embedded_selection.get("selected_source")) if embedded_selection else None
    if selected_source is None:
        _add_missing(missing, "embedded_selection.selected_source")

    snapshot_summary = _mapping_or_none(
        evidence_packet.get("portfolio_snapshot_summary") if evidence_packet else None,
        "evidence_packet.portfolio_snapshot_summary",
        missing,
    )
    snapshot_available = snapshot_summary.get("available") if snapshot_summary else None
    if not isinstance(snapshot_available, bool):
        _add_missing(missing, "evidence_packet.portfolio_snapshot_summary.available")

    last_good_available = (
        research_availability.get("last_good_available") if research_availability else None
    )
    if not isinstance(last_good_available, bool):
        _add_missing(missing, "research_availability.last_good_available")

    # The supplied production observatory is intentionally not recomputed here;
    # this field just records whether its own deterministic mapping was usable.
    observatory_available = grounding_status_observatory is not None
    return {
        "as_of_input_class": _as_of_input_class(as_of),
        "approvals_state": approvals_state,
        "revocations_state": revocations_state,
        "selected_source_class": selected_source,
        "snapshot_state": _bool_state(snapshot_available),
        "last_good_availability": _bool_state(last_good_available),
        "production_observatory_mapping_available": observatory_available,
    }


def _writer_outcomes(
    switch_status: Mapping[str, Any] | None,
    missing: list[str],
) -> dict[str, dict[str, Any]]:
    switched = _mapping_or_none(
        switch_status.get("switched_artifacts") if switch_status else None,
        "switch_status.switched_artifacts",
        missing,
    )
    invocations = _mapping_or_none(
        switch_status.get("evidence_packet_write_invocations") if switch_status else None,
        "switch_status.evidence_packet_write_invocations",
        missing,
    )
    return {
        "evidence_packet": _writer_outcome(
            status=switched.get("evidence_packet") if switched else None,
            invocation=invocations.get("evidence_packet") if invocations else None,
            field_prefix="switch_status.evidence_packet",
            missing=missing,
        ),
        "embedded_selection": _writer_outcome(
            status=(
                switched.get("embedded_active_anchor_registry_selection") if switched else None
            ),
            invocation=(
                invocations.get("embedded_active_anchor_registry_selection") if invocations else None
            ),
            field_prefix="switch_status.embedded_active_anchor_registry_selection",
            missing=missing,
        ),
    }


def _writer_outcome(
    *,
    status: Any,
    invocation: Any,
    field_prefix: str,
    missing: list[str],
) -> dict[str, Any]:
    status_mapping = _mapping_or_none(status, field_prefix, missing)
    invocation_mapping = _mapping_or_none(
        invocation, f"{field_prefix}_write_invocations", missing
    )
    writer_source = _string_or_none(status_mapping.get("writer_source")) if status_mapping else None
    fallback_used = status_mapping.get("fallback_used") if status_mapping else None
    if writer_source is None:
        _add_missing(missing, f"{field_prefix}.writer_source")
    if not isinstance(fallback_used, bool):
        _add_missing(missing, f"{field_prefix}.fallback_used")
        fallback_used = None
    error = extract_canonical_error_token(status_mapping.get("error_summary") if status_mapping else None)

    invocation_count = invocation_mapping.get("invocation_count") if invocation_mapping else None
    divergence = (
        invocation_mapping.get("first_and_final_statuses_differ") if invocation_mapping else None
    )
    final_disk_write_invocation = (
        invocation_mapping.get("final_disk_write_invocation") if invocation_mapping else None
    )
    if not _non_bool_int(invocation_count):
        _add_missing(missing, f"{field_prefix}_write_invocations.invocation_count")
        invocation_count = None
    if not isinstance(divergence, bool):
        _add_missing(missing, f"{field_prefix}_write_invocations.first_and_final_statuses_differ")
        divergence = None
    if not _non_bool_int(final_disk_write_invocation):
        _add_missing(missing, f"{field_prefix}_write_invocations.final_disk_write_invocation")
        final_disk_write_invocation = None
    return {
        "final_writer_source": writer_source,
        "fallback_used": fallback_used,
        **error,
        "invocation_count": invocation_count,
        "first_final_status_divergence": divergence,
        "final_disk_write_invocation": final_disk_write_invocation,
    }


def _guard_summaries(
    *,
    shadow_diff: Mapping[str, Any] | None,
    writer_outcomes: Mapping[str, Mapping[str, Any]],
    missing: list[str],
) -> dict[str, dict[str, Any]]:
    comparisons = _mapping_or_none(
        shadow_diff.get("comparisons") if shadow_diff else None,
        "shadow_diff.comparisons",
        missing,
    )
    return {
        "evidence_packet": _guard_summary(
            comparison=comparisons.get("evidence_packet") if comparisons else None,
            writer_outcome=writer_outcomes["evidence_packet"],
            field_prefix="shadow_diff.comparisons.evidence_packet",
            missing=missing,
        ),
        "embedded_selection": _guard_summary(
            comparison=(
                comparisons.get("embedded_active_anchor_registry_selection")
                if comparisons
                else None
            ),
            writer_outcome=writer_outcomes["embedded_selection"],
            field_prefix="shadow_diff.comparisons.embedded_active_anchor_registry_selection",
            missing=missing,
        ),
    }


def _guard_summary(
    *,
    comparison: Any,
    writer_outcome: Mapping[str, Any],
    field_prefix: str,
    missing: list[str],
) -> dict[str, Any]:
    mapping = _mapping_or_none(comparison, field_prefix, missing)
    match_observed = mapping.get("semantic_match") if mapping else None
    if not isinstance(match_observed, bool):
        _add_missing(missing, f"{field_prefix}.semantic_match")
        match_observed = None
    differences = mapping.get("differences") if mapping else None
    if not isinstance(differences, list):
        _add_missing(missing, f"{field_prefix}.differences")
        differences_count = None
    else:
        differences_count = len(differences)
    current_summary = _mapping_or_none(
        mapping.get("current_summary") if mapping else None,
        f"{field_prefix}.current_summary",
        missing,
    )
    unknown_timestamp_fields = (
        current_summary.get("parity_unknown_runtime_timestamp_fields") if current_summary else None
    )
    error_token = writer_outcome.get("canonical_error_token")
    unknown_timestamp_tokens = {
        "step1a_evidence_packet_unknown_runtime_timestamp",
        "step1a_embedded_selection_unknown_runtime_timestamp",
    }
    if isinstance(unknown_timestamp_fields, list):
        unknown_timestamp_observed = bool(unknown_timestamp_fields)
    elif isinstance(error_token, str) and error_token != "unknown_error_token":
        # The existing final guard token is the authoritative current-run fact
        # when this particular shadow summary does not retain its timestamp
        # field list (the embedded-selection summary has that shape today).
        unknown_timestamp_observed = error_token in unknown_timestamp_tokens
    else:
        _add_missing(missing, f"{field_prefix}.parity_unknown_runtime_timestamp_fields")
        unknown_timestamp_observed = None
    unexpected_path_observed = (
        True
        if error_token in {
            "step1a_evidence_packet_unexpected_normalized_path",
            "step1a_embedded_selection_unexpected_normalized_path",
        }
        else False
        if isinstance(error_token, str) and error_token != "unknown_error_token"
        else None
    )
    return {
        "match_observed": match_observed,
        "unknown_timestamp_observed": unknown_timestamp_observed,
        "unexpected_normalized_path_observed": unexpected_path_observed,
        "differences_count": differences_count,
    }


def _shadow_and_observatory_observation(
    *,
    shadow_diff: Mapping[str, Any] | None,
    observatory_integration_result: Any,
    missing: list[str],
) -> dict[str, Any]:
    comparison_status = _string_or_none(shadow_diff.get("comparison_status")) if shadow_diff else None
    parity_passed = shadow_diff.get("parity_passed") if shadow_diff else None
    comparison_complete = shadow_diff.get("comparison_complete") if shadow_diff else None
    skipped = shadow_diff.get("skipped_artifacts") if shadow_diff else None
    mismatches = shadow_diff.get("mismatch_artifacts") if shadow_diff else None
    if comparison_status is None:
        _add_missing(missing, "shadow_diff.comparison_status")
    if not isinstance(parity_passed, bool):
        _add_missing(missing, "shadow_diff.parity_passed")
        parity_passed = None
    if not isinstance(comparison_complete, bool):
        _add_missing(missing, "shadow_diff.comparison_complete")
        comparison_complete = None
    if not isinstance(skipped, list) or not all(isinstance(item, str) for item in skipped):
        _add_missing(missing, "shadow_diff.skipped_artifacts")
        skipped = None
    else:
        skipped = sorted(set(skipped))
    if not isinstance(mismatches, list) or not all(isinstance(item, str) for item in mismatches):
        _add_missing(missing, "shadow_diff.mismatch_artifacts")
        mismatches = None
    else:
        mismatches = sorted(set(mismatches))
    integration = _string_or_none(observatory_integration_result)
    if integration is None:
        _add_missing(missing, "observatory_integration_result")
    return {
        "comparison_status": comparison_status,
        "parity_passed": parity_passed,
        "comparison_complete": comparison_complete,
        "skipped_artifact_keys": skipped,
        "mismatch_artifact_keys": mismatches,
        "observatory_integration_result": integration,
    }


def _grounding_observation(
    *,
    evidence_packet: Mapping[str, Any] | None,
    compiled_support_signals: Mapping[str, Any] | None,
    research_availability: Mapping[str, Any] | None,
    evidence_packet_artifact_present: Any,
    evidence_packet_artifact_parseable: Any,
    compiled_support_signals_artifact_present: Any,
    compiled_support_signals_artifact_parseable: Any,
    missing: list[str],
) -> dict[str, Any]:
    packet_present = _bool_or_none(
        evidence_packet_artifact_present, "evidence_packet_final_artifact_present", missing
    )
    packet_parseable = _bool_or_none(
        evidence_packet_artifact_parseable, "evidence_packet_final_artifact_parseable", missing
    )
    support_present = _bool_or_none(
        compiled_support_signals_artifact_present, "compiled_support_signals_present", missing
    )
    support_parseable = _bool_or_none(
        compiled_support_signals_artifact_parseable,
        "compiled_support_signals_parseable",
        missing,
    )
    if packet_present is not True:
        _add_missing(missing, "evidence_packet_final_artifact_present")
    if packet_parseable is not True:
        _add_missing(missing, "evidence_packet_final_artifact_parseable")
    if support_present is not True:
        _add_missing(missing, "compiled_support_signals_present")
    if support_parseable is not True:
        _add_missing(missing, "compiled_support_signals_parseable")
    accepted = compiled_support_signals.get("accepted_support_signals") if compiled_support_signals else None
    if not isinstance(accepted, list):
        _add_missing(missing, "compiled_support_signals.accepted_support_signals")
        accepted_count = None
    else:
        accepted_count = len(accepted)
    grounded_memo_support_present = (
        research_availability.get("grounded_memo_support_present")
        if research_availability
        else None
    )
    if not isinstance(grounded_memo_support_present, bool):
        _add_missing(missing, "research_availability.grounded_memo_support_present")
        grounded_memo_support_present = None
    return {
        "evidence_packet_final_artifact_present": packet_present,
        "evidence_packet_final_artifact_parseable": packet_parseable,
        "compiled_support_signals_present": support_present,
        "compiled_support_signals_parseable": support_parseable,
        "accepted_support_signal_count": accepted_count,
        "grounded_memo_support_present": grounded_memo_support_present,
        # Mapping presence is intentionally separate from physical file state:
        # a retained/malformed final file is never mistaken for a fresh write.
        "evidence_packet_mapping_available": evidence_packet is not None,
    }


def _permission_context_observation(
    research_availability: Mapping[str, Any] | None,
    missing: list[str],
) -> dict[str, Any]:
    state = _string_or_none(research_availability.get("state")) if research_availability else None
    availability_state = (
        _string_or_none(research_availability.get("research_availability"))
        if research_availability
        else None
    )
    actions = research_availability.get("allowed_actions") if research_availability else None
    new_buy_allowed = research_availability.get("new_buy_permission") if research_availability else None
    order_compilation_allowed = (
        research_availability.get("order_compilation_allowed") if research_availability else None
    )
    if state is None:
        _add_missing(missing, "research_availability.state")
    if availability_state is None:
        _add_missing(missing, "research_availability.research_availability")
    if not isinstance(actions, list) or not all(isinstance(item, str) for item in actions):
        _add_missing(missing, "research_availability.allowed_actions")
        actions = None
    else:
        actions = list(actions)
    if not isinstance(new_buy_allowed, bool):
        _add_missing(missing, "research_availability.new_buy_permission")
        new_buy_allowed = None
    if not isinstance(order_compilation_allowed, bool):
        _add_missing(missing, "research_availability.order_compilation_allowed")
        order_compilation_allowed = None
    return {
        "research_state": state,
        "research_availability_state": availability_state,
        "allowed_actions": actions,
        "new_buy_allowed": new_buy_allowed,
        "order_compilation_allowed": order_compilation_allowed,
    }


def _minimal_incomplete_observation(generated_at: Any) -> dict[str, Any]:
    timestamp = _string_or_none(generated_at)
    return {
        "schema_version": SCHEMA_VERSION,
        "is_llm_generated": False,
        "report_only": True,
        "not_authorization": True,
        "not_execution_authorization": True,
        "permission_effect": "none",
        "consumed_by_gates": False,
        "consumed_by_order_path": False,
        "consumed_by_downstream": False,
        "safe_to_ignore": True,
        "assessment_state": "observation_only",
        "observation_identity": {"observation_id": None, "generated_at": timestamp},
        "coverage_identity": {"coverage_key": None, "composite_config_fingerprint": None},
        "observation_completeness": "incomplete",
        "missing_observation_fields": ["builder_internal_error"],
        "compatibility_blockers": [],
    }


def _mapping_or_none(value: Any, field: str, missing: list[str]) -> Mapping[str, Any] | None:
    if isinstance(value, Mapping):
        return value
    _add_missing(missing, field)
    return None


def _bool_or_none(value: Any, field: str, missing: list[str]) -> bool | None:
    if isinstance(value, bool):
        return value
    _add_missing(missing, field)
    return None


def _manifest_state(entries: Any, source_id: str) -> str:
    if not isinstance(entries, list):
        return "unknown"
    for entry in entries:
        if not isinstance(entry, Mapping) or entry.get("source_id") != source_id:
            continue
        present = entry.get("present")
        valid = entry.get("valid")
        if present is False:
            return "absent"
        if present is True and valid is True:
            return "present_valid"
        if present is True and valid is False:
            return "present_invalid"
        return "present_unknown"
    return "unknown"


def _as_of_input_class(value: str | None) -> str:
    if value is None:
        return "missing"
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return "iso_date"
    return "non_iso_string"


def _bool_state(value: Any) -> str:
    if value is True:
        return "present"
    if value is False:
        return "absent"
    return "unknown"


def _non_bool_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _string_or_none(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _sha256_value(value: Any) -> str | None:
    try:
        canonical = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError):
        return None
    return _sha256_text(canonical)


def _add_missing(missing: list[str], field: str) -> None:
    if field not in missing:
        missing.append(field)


def _add_unique(values: list[str], value: str) -> None:
    if value not in values:
        values.append(value)
