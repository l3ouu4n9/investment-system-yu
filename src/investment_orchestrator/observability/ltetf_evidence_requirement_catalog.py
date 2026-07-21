"""Frozen LTETF-02a evidence requirement catalog.

The catalog owns only catalog-specific requirement records and frozen expected
identities.  All schema, profile, status, resource, and canonicalization
bindings are read from the common LTETF-02a contract on every validation.
"""

from __future__ import annotations

# Keep the module namespace exactly equal to the accepted public API.
del annotations

from dataclasses import dataclass as _dataclass, replace as _replace
import re as _re
from types import MappingProxyType as _MappingProxyType
from typing import TYPE_CHECKING as _TYPE_CHECKING

from investment_orchestrator.observability import (
    ltetf_evidence_contract_common as _common,
)

if _TYPE_CHECKING:
    from typing import Final


LTETF_02A_CATALOG_VERSION: Final = "ltetf_02a_catalog_v1"
LTETF_02A_CATALOG_IDENTITY_SHA256: Final = (
    "af12f3ea721ca428f14a95ddd9389223b52711cd1f937fc32e2b480258609590"
)

_AUTHORITY_EFFECT: Final = "none"
_SHA256_RE: Final = _re.compile(r"^[0-9a-f]{64}$")
_EXPECTED_REQUIREMENT_IDENTITIES_BY_ID: Final = _MappingProxyType(
    {
        "source_authority_policy": "03a953e0acd149ea840ccc36f0b6443a41b4791cdab743e7bf011a494942886a",
        "authorized_source_registry": "1f60e933eb28b5f2a8430c52df886e9c5e6a86533d8e0bec87eacbcb87376d49",
        "generic_evidence_manifest_contract": "50df367be3e7cf5d8b87c9b9ecf3b101653f024ee02dbfb0809d958a99df195a",
        "evidence_timestamp_semantics": "407cb57d2659d3138650a65890c97ac2178584e90775fe89bda89b0a94f67a80",
        "trusted_evaluation_time": "d80fa643ea25e629c09757eac8ca4334f3f4b4700f21f725051710dc0d1c8c55",
        "field_freshness_policy": "ad2bc162b86e2217561d0348c0ec32c63001b96ebff982e90d7b68367f49d08c",
        "evidence_conflict_gap_contract": "00e473f3e26c7c0b9606de2b478d7979d744c57966256343fccfcf43f6ee5240",
        "structured_market_metrics": "fe92f3575950dda024f9f351702f987523916dc5f7a02a2e4292efbb0b75c67b",
        "structured_scheduled_events": "5880f518e310ca857065eef380051ebfd6fd8adf156b2276d58769a8d449dab7",
        "prior_thesis_continuity": "6a536e34a79ab5cfa2e8ac232f0e72f36394a9a7024e35be6ab809b36b9c1057",
    }
)
_COMMON_PROFILE_ATTRIBUTE_NAMES: Final = (
    "NORMALIZATION_PROFILE",
    "PROHIBITED_KEY_PROFILE",
    "SOURCE_AUTHORITY_TAXONOMY",
    "SUBJECT_PROFILE",
    "LOCATOR_PROFILE",
    "METRIC_PROFILE",
    "UNIT_PROFILE",
    "EVENT_PROFILE",
    "THESIS_PROFILE",
    "STATUS_REASON_TAXONOMY",
    "CONFLICT_RULE_PROFILE",
    "RESOURCE_BOUND_PROFILE",
    "INTEGRITY_CODE_PROFILE",
)
_RESOURCE_BOUND_CATALOG_FIELDS: Final = (
    "max_explicit_manifests",
    "max_explicit_policy_payloads",
    "max_explicit_acceptance_artifacts",
    "max_canonical_artifact_bytes",
    "max_total_canonical_input_bytes",
    "max_json_depth",
    "max_json_object_members",
    "max_json_array_items",
    "max_report_diagnostics",
    "investment_sufficiency_effect",
)
_EVENTUAL_EXTERNAL_OBSERVER_CONSUMERS: Final = (
    "src/investment_orchestrator/cli/observe_ltetf_evidence_inventory.py",
)
_PROHIBITED_CONSUMER_CATEGORIES: Final = (
    "llm",
    "network_acquisition",
    "weekly_workflow",
    "investment_stage",
    "state_transition",
    "permission_evaluation",
    "gate",
    "target_stage",
    "canonical_publication",
    "final_safety",
    "order_compilation",
    "manual_order_artifact",
    "broker",
    "live_execution",
)


class _CatalogIdentityError(ValueError):
    """Raised when the frozen catalog or its canonical dependencies drift."""


@_dataclass(frozen=True, slots=True)
class RequirementRecord:
    """One identity-bound static LTETF-02a requirement definition."""

    ordinal: int
    requirement_id: str
    owner: str
    binding_mode: str
    traceability_id: str
    dependency_ids: tuple[str, ...]
    schema_versions: tuple[str, ...]
    schema_identities_sha256: tuple[str, ...]
    semantic_contract_identities_sha256: tuple[str, ...]
    profile_identities_sha256: tuple[str, ...]
    policy_dependencies: tuple[str, ...]
    subject_rule_id: str
    temporal_rule_id: str
    conflict_rule_id: str
    authority_effect: str
    requirement_identity_sha256: str


@_dataclass(frozen=True, slots=True)
class _RequirementSpec:
    ordinal: int
    requirement_id: str
    owner: str
    binding_mode: str
    traceability_id: str
    dependency_ids: tuple[str, ...]
    schema_versions: tuple[str, ...]
    profile_attribute_names: tuple[str, ...]
    policy_dependencies: tuple[str, ...]
    subject_rule_id: str
    temporal_rule_id: str
    conflict_rule_id: str


def _requirement_payload(record: RequirementRecord) -> dict[str, object]:
    if type(record) is not RequirementRecord:
        raise _CatalogIdentityError("REQUIREMENT_RECORD_TYPE_INVALID")
    if type(record.ordinal) is not int or not 1 <= record.ordinal <= 10:
        raise _CatalogIdentityError("REQUIREMENT_ORDINAL_INVALID")
    scalar_strings = (
        record.requirement_id,
        record.owner,
        record.binding_mode,
        record.traceability_id,
        record.subject_rule_id,
        record.temporal_rule_id,
        record.conflict_rule_id,
        record.authority_effect,
    )
    if any(type(value) is not str or not value or not value.isascii() for value in scalar_strings):
        raise _CatalogIdentityError("REQUIREMENT_STRING_FIELD_INVALID")
    tuple_fields = (
        record.dependency_ids,
        record.schema_versions,
        record.schema_identities_sha256,
        record.semantic_contract_identities_sha256,
        record.profile_identities_sha256,
        record.policy_dependencies,
    )
    if any(
        type(values) is not tuple
        or any(type(value) is not str or not value.isascii() for value in values)
        for values in tuple_fields
    ):
        raise _CatalogIdentityError("REQUIREMENT_TUPLE_FIELD_INVALID")
    if type(record.requirement_identity_sha256) is not str or _SHA256_RE.fullmatch(
        record.requirement_identity_sha256
    ) is None:
        raise _CatalogIdentityError("REQUIREMENT_SELF_IDENTITY_INVALID")
    for identity in (
        *record.schema_identities_sha256,
        *record.semantic_contract_identities_sha256,
        *record.profile_identities_sha256,
    ):
        if _SHA256_RE.fullmatch(identity) is None:
            raise _CatalogIdentityError("REQUIREMENT_BOUND_IDENTITY_INVALID")
    return {
        "ordinal": record.ordinal,
        "requirement_id": record.requirement_id,
        "owner": record.owner,
        "binding_mode": record.binding_mode,
        "traceability_id": record.traceability_id,
        "dependency_ids": list(record.dependency_ids),
        "schema_versions": list(record.schema_versions),
        "schema_identities_sha256": list(record.schema_identities_sha256),
        "semantic_contract_identities_sha256": list(record.semantic_contract_identities_sha256),
        "profile_identities_sha256": list(record.profile_identities_sha256),
        "policy_dependencies": list(record.policy_dependencies),
        "subject_rule_id": record.subject_rule_id,
        "temporal_rule_id": record.temporal_rule_id,
        "conflict_rule_id": record.conflict_rule_id,
        "authority_effect": record.authority_effect,
    }


def requirement_identity_sha256(record: RequirementRecord) -> str:
    """Dynamically hash a requirement using the current canonical common contract."""
    return _common.domain_separated_sha256(
        _common.DOMAIN_SEPARATORS["requirement"],
        _requirement_payload(record),
    )


_REQUIREMENT_SPECS: Final = (
    _RequirementSpec(
        1, "source_authority_policy", "operator_and_deterministic_validation",
        "operator_policy_payload_and_separate_acceptance", "source_policy_contract", (),
        ("ltetf_source_authority_policy_v1",),
        ("NORMALIZATION_PROFILE", "PROHIBITED_KEY_PROFILE", "SOURCE_AUTHORITY_TAXONOMY"),
        ("source_authority_policy",), "SUBJECT_NOT_APPLICABLE_TO_POLICY_PAYLOAD",
        "TEMPORAL_NOT_APPLICABLE_TO_SOURCE_AUTHORITY_POLICY", "SOURCE_AUTHORITY_POLICY_SLOT_CONFLICT_V1",
    ),
    _RequirementSpec(
        2, "authorized_source_registry", "operator_and_deterministic_validation",
        "operator_policy_payload_and_separate_acceptance", "authorized_source_inventory",
        ("source_authority_policy",), ("ltetf_authorized_source_registry_v1",),
        ("NORMALIZATION_PROFILE", "PROHIBITED_KEY_PROFILE", "SOURCE_AUTHORITY_TAXONOMY", "LOCATOR_PROFILE"),
        ("source_authority_policy", "authorized_source_registry"), "SUBJECT_NOT_APPLICABLE_TO_SOURCE_REGISTRY",
        "TEMPORAL_NOT_APPLICABLE_TO_SOURCE_REGISTRY", "AUTHORIZED_SOURCE_REGISTRY_SLOT_AND_SOURCE_RECORD_CONFLICT_V1",
    ),
    _RequirementSpec(
        3, "generic_evidence_manifest_contract", "deterministic_code",
        "code_owned_schema_and_future_semantic_validator", "evidence_provenance_contract",
        ("source_authority_policy", "authorized_source_registry"), ("ltetf_generic_evidence_manifest_v1",),
        ("NORMALIZATION_PROFILE", "PROHIBITED_KEY_PROFILE", "SOURCE_AUTHORITY_TAXONOMY", "SUBJECT_PROFILE", "LOCATOR_PROFILE"),
        ("source_authority_policy", "authorized_source_registry"), "MANIFEST_NEUTRAL_SUBJECT_REQUIRED",
        "MANIFEST_ACQUIRED_AT_UTC_ONLY", "GENERIC_MANIFEST_LOGICAL_ARTIFACT_CONFLICT_V1",
    ),
    _RequirementSpec(
        4, "evidence_timestamp_semantics", "deterministic_code",
        "code_owned_profile_and_future_semantic_validator", "evidence_timestamp_semantics",
        ("generic_evidence_manifest_contract",),
        (
            "ltetf_generic_evidence_manifest_v1", "ltetf_trusted_evaluation_epoch_v1",
            "ltetf_structured_market_metrics_v1", "ltetf_structured_scheduled_events_v1",
            "ltetf_prior_thesis_continuity_v1",
        ),
        ("NORMALIZATION_PROFILE", "SUBJECT_PROFILE", "METRIC_PROFILE", "EVENT_PROFILE", "THESIS_PROFILE"), (),
        "CLASS_SPECIFIC_SUBJECT_BINDING_REQUIRED", "EXACT_CLASS_SPECIFIC_UTC_FIELDS_AND_AGGREGATION_V1",
        "TIMESTAMP_INVALID_IS_NOT_CONFLICT_V1",
    ),
    _RequirementSpec(
        5, "trusted_evaluation_time", "operator_and_deterministic_validation",
        "manifest_bound_evidence_content", "trusted_evaluation_clock",
        ("source_authority_policy", "authorized_source_registry", "generic_evidence_manifest_contract", "evidence_timestamp_semantics"),
        ("ltetf_trusted_evaluation_epoch_v1",),
        ("NORMALIZATION_PROFILE", "SUBJECT_PROFILE", "CONFLICT_RULE_PROFILE"),
        ("source_authority_policy", "authorized_source_registry"), "EVALUATION_CONTEXT_SUBJECT_REQUIRED",
        "IDENTITY_BOUND_DETERMINISTIC_EVALUATION_EPOCH_V1", "MULTIPLE_TRUSTED_EVALUATION_EPOCHS_CONFLICT_V1",
    ),
    _RequirementSpec(
        6, "field_freshness_policy", "operator_and_deterministic_validation",
        "operator_policy_payload_and_separate_acceptance", "field_level_freshness_contract",
        ("source_authority_policy", "authorized_source_registry", "evidence_timestamp_semantics"),
        ("ltetf_field_freshness_policy_v1",), ("NORMALIZATION_PROFILE", "PROHIBITED_KEY_PROFILE"),
        ("source_authority_policy", "authorized_source_registry", "field_freshness_policy"),
        "SUBJECT_NOT_APPLICABLE_TO_FRESHNESS_POLICY", "FRESHNESS_AS_OF_BOUND_EVALUATION_EPOCH_V1",
        "FIELD_FRESHNESS_POLICY_SLOT_CONFLICT_V1",
    ),
    _RequirementSpec(
        7, "evidence_conflict_gap_contract", "deterministic_code",
        "code_owned_conflict_profile_and_future_semantic_validator", "evidence_conflict_gap_contract",
        ("generic_evidence_manifest_contract", "evidence_timestamp_semantics", "trusted_evaluation_time", "field_freshness_policy"),
        tuple(_common.SCHEMA_FILENAME_BY_VERSION), ("CONFLICT_RULE_PROFILE", "STATUS_REASON_TAXONOMY"),
        ("source_authority_policy", "authorized_source_registry", "field_freshness_policy"),
        "SUBJECT_IDENTITY_PARTICIPATES_ONLY_WHERE_CLASS_FACT_KEY_REQUIRES",
        "INVALID_TEMPORAL_CANDIDATES_EXCLUDED_FROM_CONFLICT_EVALUATION",
        "FROZEN_CLASS_SPECIFIC_CONFLICT_RULE_PROFILE_V1",
    ),
    _RequirementSpec(
        8, "structured_market_metrics", "deterministic_code", "manifest_bound_evidence_content",
        "structured_market_metrics",
        (
            "source_authority_policy", "authorized_source_registry", "generic_evidence_manifest_contract",
            "evidence_timestamp_semantics", "trusted_evaluation_time", "field_freshness_policy",
            "evidence_conflict_gap_contract",
        ),
        ("ltetf_structured_market_metrics_v1",),
        ("NORMALIZATION_PROFILE", "SUBJECT_PROFILE", "METRIC_PROFILE", "UNIT_PROFILE", "CONFLICT_RULE_PROFILE"),
        ("source_authority_policy", "authorized_source_registry", "field_freshness_policy"),
        "INSTRUMENT_SUBJECT_REQUIRED", "ALL_OBSERVED_AT_UTC_RECORDS_EVALUATED_V1",
        "MARKET_METRIC_LOGICAL_FACT_CONFLICT_V1",
    ),
    _RequirementSpec(
        9, "structured_scheduled_events", "deterministic_code", "manifest_bound_evidence_content",
        "structured_scheduled_events",
        (
            "source_authority_policy", "authorized_source_registry", "generic_evidence_manifest_contract",
            "evidence_timestamp_semantics", "trusted_evaluation_time", "field_freshness_policy",
            "evidence_conflict_gap_contract",
        ),
        ("ltetf_structured_scheduled_events_v1",),
        ("NORMALIZATION_PROFILE", "SUBJECT_PROFILE", "EVENT_PROFILE", "CONFLICT_RULE_PROFILE"),
        ("source_authority_policy", "authorized_source_registry", "field_freshness_policy"),
        "INSTRUMENT_SUBJECT_REQUIRED", "ALL_PUBLISHED_AT_UTC_RECORDS_EVALUATED_SCHEDULED_TIME_NOT_LOOKAHEAD_V1",
        "SCHEDULED_EVENT_LOGICAL_FACT_CONFLICT_V1",
    ),
    _RequirementSpec(
        10, "prior_thesis_continuity", "deterministic_code", "manifest_bound_evidence_content",
        "prior_thesis_continuity",
        (
            "source_authority_policy", "authorized_source_registry", "generic_evidence_manifest_contract",
            "evidence_timestamp_semantics", "trusted_evaluation_time", "field_freshness_policy",
            "evidence_conflict_gap_contract",
        ),
        ("ltetf_prior_thesis_continuity_v1",),
        ("NORMALIZATION_PROFILE", "SUBJECT_PROFILE", "THESIS_PROFILE", "CONFLICT_RULE_PROFILE"),
        ("source_authority_policy", "authorized_source_registry", "field_freshness_policy"),
        "INSTRUMENT_SUBJECT_REQUIRED", "RECORDED_AT_UTC_AND_PREDECESSOR_STRICTLY_EARLIER_V1",
        "PRIOR_THESIS_LOGICAL_FACT_CONFLICT_V1",
    ),
)
_REQUIREMENT_SPEC_BY_ID: Final = _MappingProxyType(
    {spec.requirement_id: spec for spec in _REQUIREMENT_SPECS}
)


def _current_profile_records() -> tuple[object, ...]:
    profiles = tuple(getattr(_common, name) for name in _COMMON_PROFILE_ATTRIBUTE_NAMES)
    if any(
        type(getattr(profile, "profile_version", None)) is not str
        or type(getattr(profile, "identity_sha256", None)) is not str
        or not callable(getattr(profile, "to_payload", None))
        for profile in profiles
    ):
        raise _CatalogIdentityError("COMMON_PROFILE_CAPABILITY_INVALID")
    return profiles


def _current_requirement_record(spec: _RequirementSpec) -> RequirementRecord:
    try:
        schema_identities = tuple(
            _common.SCHEMA_IDENTITY_SHA256_BY_VERSION[version] for version in spec.schema_versions
        )
        semantic_identities = tuple(
            _common.SEMANTIC_CONTRACT_IDENTITY_SHA256_BY_VERSION[version]
            for version in spec.schema_versions
        )
        profile_identities = tuple(
            getattr(_common, name).identity_sha256 for name in spec.profile_attribute_names
        )
    except (AttributeError, KeyError, TypeError) as exc:
        raise _CatalogIdentityError("COMMON_REQUIREMENT_BINDING_UNAVAILABLE") from exc
    provisional = RequirementRecord(
        ordinal=spec.ordinal,
        requirement_id=spec.requirement_id,
        owner=spec.owner,
        binding_mode=spec.binding_mode,
        traceability_id=spec.traceability_id,
        dependency_ids=spec.dependency_ids,
        schema_versions=spec.schema_versions,
        schema_identities_sha256=schema_identities,
        semantic_contract_identities_sha256=semantic_identities,
        profile_identities_sha256=profile_identities,
        policy_dependencies=spec.policy_dependencies,
        subject_rule_id=spec.subject_rule_id,
        temporal_rule_id=spec.temporal_rule_id,
        conflict_rule_id=spec.conflict_rule_id,
        authority_effect=_AUTHORITY_EFFECT,
        requirement_identity_sha256="0" * 64,
    )
    return _replace(
        provisional,
        requirement_identity_sha256=requirement_identity_sha256(provisional),
    )


LTETF_02A_REQUIREMENTS: Final = tuple(
    _replace(
        _current_requirement_record(spec),
        requirement_identity_sha256=_EXPECTED_REQUIREMENT_IDENTITIES_BY_ID[spec.requirement_id],
    )
    for spec in _REQUIREMENT_SPECS
)
LTETF_02A_REQUIREMENT_IDS: Final = tuple(record.requirement_id for record in LTETF_02A_REQUIREMENTS)
LTETF_02A_REQUIREMENT_IDENTITIES_SHA256: Final = tuple(
    record.requirement_identity_sha256 for record in LTETF_02A_REQUIREMENTS
)


def _record_with_identity_payload(record: RequirementRecord) -> dict[str, object]:
    payload = _requirement_payload(record)
    payload["requirement_identity_sha256"] = record.requirement_identity_sha256
    return payload


def _common_status_payload() -> tuple[tuple[str, ...], tuple[str, ...], dict[str, list[str]]]:
    payload = _common.STATUS_REASON_TAXONOMY.to_payload()
    statuses = payload.get("statuses")
    precedence = payload.get("precedence")
    reasons = payload.get("reason_codes_by_status")
    if (
        type(statuses) is not list
        or type(precedence) is not list
        or type(reasons) is not dict
        or any(type(status) is not str for status in statuses)
        or any(type(status) is not str for status in precedence)
        or tuple(statuses) != tuple(precedence)
        or tuple(reasons) != tuple(statuses)
    ):
        raise _CatalogIdentityError("COMMON_STATUS_REASON_CAPABILITY_INVALID")
    result: dict[str, list[str]] = {}
    for status in statuses:
        codes = reasons[status]
        if type(codes) is not list or any(type(code) is not str for code in codes):
            raise _CatalogIdentityError("COMMON_REASON_CODE_CAPABILITY_INVALID")
        result[status] = list(codes)
    return tuple(statuses), tuple(precedence), result


def _current_runtime_evidence_classes() -> tuple[str, ...]:
    subject_payload = _common.SUBJECT_PROFILE.to_payload()
    bindings = subject_payload.get("required_subject_kind_by_evidence_class")
    if type(bindings) is not list:
        raise _CatalogIdentityError("COMMON_EVIDENCE_CLASS_CAPABILITY_INVALID")
    classes = tuple(
        binding.get("evidence_class") if type(binding) is dict else None for binding in bindings
    )
    if (
        len(classes) != 4
        or any(type(evidence_class) is not str for evidence_class in classes)
        or len(set(classes)) != len(classes)
    ):
        raise _CatalogIdentityError("COMMON_EVIDENCE_CLASS_CAPABILITY_INVALID")
    return classes


def _current_resource_bounds() -> dict[str, object]:
    payload = _common.RESOURCE_BOUND_PROFILE.to_payload()
    if any(field not in payload for field in _RESOURCE_BOUND_CATALOG_FIELDS):
        raise _CatalogIdentityError("COMMON_RESOURCE_BOUND_CAPABILITY_INVALID")
    return {field: payload[field] for field in _RESOURCE_BOUND_CATALOG_FIELDS}


def _current_integrity_codes() -> tuple[str, ...]:
    payload = _common.INTEGRITY_CODE_PROFILE.to_payload()
    codes = payload.get("integrity_codes")
    if type(codes) is not list or any(type(code) is not str for code in codes):
        raise _CatalogIdentityError("COMMON_INTEGRITY_CODE_CAPABILITY_INVALID")
    return tuple(codes)


def _validate_common_contract_capabilities() -> None:
    domains = _common.DOMAIN_SEPARATORS
    if (
        type(domains) is not _MappingProxyType
        or len(domains) != 31
        or len(tuple(domains.values())) != len(set(domains.values()))
        or any(type(name) is not str or type(value) is not bytes for name, value in domains.items())
        or "catalog" not in domains
        or "requirement" not in domains
    ):
        raise _CatalogIdentityError("COMMON_DOMAIN_CAPABILITY_INVALID")
    profiles = _current_profile_records()
    identities = tuple(profile.identity_sha256 for profile in profiles)
    if len(identities) != len(set(identities)):
        raise _CatalogIdentityError("COMMON_PROFILE_IDENTITY_DUPLICATE")
    if len(_common.SCHEMA_FILENAME_BY_VERSION) != 9 or (
        tuple(_common.SCHEMA_FILENAME_BY_VERSION)
        != tuple(_common.SCHEMA_IDENTITY_SHA256_BY_VERSION)
        != tuple(_common.SEMANTIC_CONTRACT_IDENTITY_SHA256_BY_VERSION)
    ):
        raise _CatalogIdentityError("COMMON_SCHEMA_CONTRACT_CAPABILITY_INVALID")
    _common.canonical_json_bytes({"catalog": "capability"})
    _common.domain_separated_sha256(domains["catalog"], {"catalog": "capability"})
    _common_status_payload()
    _current_integrity_codes()
    _current_resource_bounds()
    _current_runtime_evidence_classes()


def _catalog_payload(requirements: tuple[RequirementRecord, ...]) -> dict[str, object]:
    profiles = _current_profile_records()
    statuses, precedence, reason_codes_by_status = _common_status_payload()
    resource_bounds = _current_resource_bounds()
    return {
        "catalog_version": LTETF_02A_CATALOG_VERSION,
        "authority_effect": _AUTHORITY_EFFECT,
        "requirements": [_record_with_identity_payload(record) for record in requirements],
        "requirement_identities_sha256": [record.requirement_identity_sha256 for record in requirements],
        "schema_bindings": [
            {
                "schema_version": version,
                "schema_path": path,
                "schema_identity_sha256": _common.SCHEMA_IDENTITY_SHA256_BY_VERSION[version],
            }
            for version, path in _common.SCHEMA_FILENAME_BY_VERSION.items()
        ],
        "semantic_contract_bindings": [
            {
                "schema_version": version,
                "semantic_contract_identity_sha256": _common.SEMANTIC_CONTRACT_IDENTITY_SHA256_BY_VERSION[version],
            }
            for version in _common.SCHEMA_FILENAME_BY_VERSION
        ],
        "profile_bindings": [
            {
                "profile_version": profile.profile_version,
                "profile_identity_sha256": profile.identity_sha256,
            }
            for profile in profiles
        ],
        "status_vocabulary": list(statuses),
        "status_precedence": list(precedence),
        "reason_codes_by_status": reason_codes_by_status,
        "integrity_codes": list(_current_integrity_codes()),
        "conflict_rule_profile_identity_sha256": _common.CONFLICT_RULE_PROFILE.identity_sha256,
        "resource_bound_profile_identity_sha256": _common.RESOURCE_BOUND_PROFILE.identity_sha256,
        "resource_bounds": resource_bounds,
        "runtime_evidence_classes": list(_current_runtime_evidence_classes()),
        "eventual_external_observer_consumers": list(_EVENTUAL_EXTERNAL_OBSERVER_CONSUMERS),
        "prohibited_consumer_categories": list(_PROHIBITED_CONSUMER_CATEGORIES),
    }


def _validate_dependency_cycles(requirements: tuple[RequirementRecord, ...]) -> None:
    records_by_id = {record.requirement_id: record for record in requirements}
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(requirement_id: str) -> None:
        if requirement_id in visiting:
            raise _CatalogIdentityError("REQUIREMENT_DEPENDENCY_CYCLE")
        if requirement_id in visited:
            return
        visiting.add(requirement_id)
        for dependency_id in records_by_id[requirement_id].dependency_ids:
            visit(dependency_id)
        visiting.remove(requirement_id)
        visited.add(requirement_id)

    for requirement_id in records_by_id:
        visit(requirement_id)


def _validate_frozen_requirement_records(requirements: tuple[RequirementRecord, ...]) -> None:
    if type(requirements) is not tuple or len(requirements) != 10:
        raise _CatalogIdentityError("REQUIREMENT_COUNT_INVALID")
    if tuple(record.ordinal for record in requirements) != tuple(range(1, 11)):
        raise _CatalogIdentityError("REQUIREMENT_ORDINALS_INVALID")
    if tuple(record.requirement_id for record in requirements) != LTETF_02A_REQUIREMENT_IDS:
        raise _CatalogIdentityError("REQUIREMENT_ORDER_INVALID")
    if len(LTETF_02A_REQUIREMENT_IDS) != len(set(LTETF_02A_REQUIREMENT_IDS)):
        raise _CatalogIdentityError("REQUIREMENT_ID_DUPLICATE")
    known_ids: set[str] = set()
    for record, frozen in zip(requirements, LTETF_02A_REQUIREMENTS, strict=True):
        if type(record) is not RequirementRecord:
            raise _CatalogIdentityError("REQUIREMENT_RECORD_TYPE_INVALID")
        if record.authority_effect != "none":
            raise _CatalogIdentityError("REQUIREMENT_AUTHORITY_EFFECT_INVALID")
        if any(dependency_id not in known_ids for dependency_id in record.dependency_ids):
            raise _CatalogIdentityError("REQUIREMENT_DEPENDENCY_ORDER_INVALID")
        if len(record.dependency_ids) != len(set(record.dependency_ids)):
            raise _CatalogIdentityError("REQUIREMENT_DEPENDENCY_DUPLICATE")
        _requirement_payload(record)
        if record != frozen:
            raise _CatalogIdentityError("REQUIREMENT_FROZEN_RECORD_MISMATCH")
        known_ids.add(record.requirement_id)
    _validate_dependency_cycles(requirements)


def _current_requirement_records() -> tuple[RequirementRecord, ...]:
    return tuple(_current_requirement_record(spec) for spec in _REQUIREMENT_SPECS)


def catalog_identity_sha256(
    requirements: tuple[RequirementRecord, ...] = LTETF_02A_REQUIREMENTS,
) -> str:
    """Dynamically recompute the catalog identity from current common bindings."""
    _validate_frozen_requirement_records(requirements)
    return _common.domain_separated_sha256(
        _common.DOMAIN_SEPARATORS["catalog"],
        _catalog_payload(_current_requirement_records()),
    )


def validate_requirement_catalog(
    requirements: tuple[RequirementRecord, ...] = LTETF_02A_REQUIREMENTS,
) -> None:
    """Fail closed if any frozen catalog or current common binding has drifted."""
    _validate_frozen_requirement_records(requirements)
    _validate_common_contract_capabilities()
    dynamic_requirements = _current_requirement_records()
    if len(dynamic_requirements) != len(requirements):
        raise _CatalogIdentityError("REQUIREMENT_DYNAMIC_COUNT_INVALID")
    for frozen, dynamic in zip(requirements, dynamic_requirements, strict=True):
        if dynamic.requirement_identity_sha256 != frozen.requirement_identity_sha256:
            raise _CatalogIdentityError("REQUIREMENT_IDENTITY_MISMATCH")
        if (
            dynamic.schema_identities_sha256 != frozen.schema_identities_sha256
            or dynamic.semantic_contract_identities_sha256 != frozen.semantic_contract_identities_sha256
            or dynamic.profile_identities_sha256 != frozen.profile_identities_sha256
        ):
            raise _CatalogIdentityError("REQUIREMENT_COMMON_BINDING_MISMATCH")
    if tuple(record.requirement_id for record in dynamic_requirements) != LTETF_02A_REQUIREMENT_IDS:
        raise _CatalogIdentityError("REQUIREMENT_DYNAMIC_ORDER_INVALID")
    if set(_REQUIREMENT_SPEC_BY_ID) != set(LTETF_02A_REQUIREMENT_IDS):
        raise _CatalogIdentityError("REQUIREMENT_SPEC_CLOSURE_INVALID")
    dynamic_identity = catalog_identity_sha256(requirements)
    if dynamic_identity != LTETF_02A_CATALOG_IDENTITY_SHA256:
        raise _CatalogIdentityError("CATALOG_IDENTITY_MISMATCH")


validate_requirement_catalog()


__all__ = [
    "RequirementRecord",
    "LTETF_02A_CATALOG_VERSION",
    "LTETF_02A_CATALOG_IDENTITY_SHA256",
    "LTETF_02A_REQUIREMENTS",
    "LTETF_02A_REQUIREMENT_IDS",
    "LTETF_02A_REQUIREMENT_IDENTITIES_SHA256",
    "requirement_identity_sha256",
    "catalog_identity_sha256",
    "validate_requirement_catalog",
]
