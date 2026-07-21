from __future__ import annotations

import ast
from copy import deepcopy
from dataclasses import replace
import hashlib
import json
from pathlib import Path
from types import MappingProxyType
from typing import Callable

import pytest

from investment_orchestrator.observability import ltetf_evidence_contract_common as common
from investment_orchestrator.observability import ltetf_evidence_requirement_catalog as catalog


ROOT = Path(__file__).parents[2]
COMMON_MODULE = "investment_orchestrator.observability.ltetf_evidence_contract_common"
CATALOG_MODULE = "investment_orchestrator.observability.ltetf_evidence_requirement_catalog"

EXPECTED_REQUIREMENT_IDS = (
    "source_authority_policy",
    "authorized_source_registry",
    "generic_evidence_manifest_contract",
    "evidence_timestamp_semantics",
    "trusted_evaluation_time",
    "field_freshness_policy",
    "evidence_conflict_gap_contract",
    "structured_market_metrics",
    "structured_scheduled_events",
    "prior_thesis_continuity",
)

EXPECTED_TRACEABILITY_IDS = (
    "source_policy_contract",
    "authorized_source_inventory",
    "evidence_provenance_contract",
    "evidence_timestamp_semantics",
    "trusted_evaluation_clock",
    "field_level_freshness_contract",
    "evidence_conflict_gap_contract",
    "structured_market_metrics",
    "structured_scheduled_events",
    "prior_thesis_continuity",
)


def _independent_canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _independent_hash(domain: bytes, payload: object) -> str:
    return hashlib.sha256(domain + _independent_canonical(payload)).hexdigest()


def _independent_requirement_payload(record: catalog.RequirementRecord) -> dict[str, object]:
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


def test_requirement_catalog_has_exact_ten_records_order_and_traceability() -> None:
    assert catalog.LTETF_02A_CATALOG_VERSION == "ltetf_02a_catalog_v1"
    assert catalog.LTETF_02A_REQUIREMENT_IDS == EXPECTED_REQUIREMENT_IDS
    assert tuple(record.requirement_id for record in catalog.LTETF_02A_REQUIREMENTS) == EXPECTED_REQUIREMENT_IDS
    assert tuple(record.ordinal for record in catalog.LTETF_02A_REQUIREMENTS) == tuple(range(1, 11))
    assert tuple(record.traceability_id for record in catalog.LTETF_02A_REQUIREMENTS) == EXPECTED_TRACEABILITY_IDS
    assert all(record.authority_effect == "none" for record in catalog.LTETF_02A_REQUIREMENTS)


def test_requirement_dependencies_are_known_ordered_unique_and_acyclic() -> None:
    seen: set[str] = set()
    for record in catalog.LTETF_02A_REQUIREMENTS:
        assert len(record.dependency_ids) == len(set(record.dependency_ids))
        assert set(record.dependency_ids) <= seen
        seen.add(record.requirement_id)
    catalog.validate_requirement_catalog()
    cycle = list(catalog.LTETF_02A_REQUIREMENTS)
    cycle[0] = replace(cycle[0], dependency_ids=(cycle[-1].requirement_id,))
    with pytest.raises(ValueError):
        catalog.validate_requirement_catalog(tuple(cycle))


def test_requirement_identities_are_independently_recomputed_and_exclude_only_self_hash() -> None:
    independently_computed: list[str] = []
    for record in catalog.LTETF_02A_REQUIREMENTS:
        expected = _independent_hash(
            common.DOMAIN_SEPARATORS["requirement"],
            _independent_requirement_payload(record),
        )
        assert record.requirement_identity_sha256 == expected
        assert catalog.requirement_identity_sha256(record) == expected
        independently_computed.append(expected)
        assert catalog.requirement_identity_sha256(
            replace(record, requirement_identity_sha256="f" * 64)
        ) == expected
        altered = replace(record, temporal_rule_id=f"{record.temporal_rule_id}_ALTERED")
        assert catalog.requirement_identity_sha256(altered) != expected
    assert tuple(independently_computed) == catalog.LTETF_02A_REQUIREMENT_IDENTITIES_SHA256
    assert len(independently_computed) == len(set(independently_computed))


def test_catalog_identity_is_independently_recomputed_from_complete_closed_payload() -> None:
    payload = catalog._catalog_payload(catalog.LTETF_02A_REQUIREMENTS)
    expected = _independent_hash(common.DOMAIN_SEPARATORS["catalog"], payload)
    assert catalog.LTETF_02A_CATALOG_IDENTITY_SHA256 == expected
    assert catalog.catalog_identity_sha256() == expected
    assert payload["catalog_version"] == "ltetf_02a_catalog_v1"
    assert payload["authority_effect"] == "none"


def test_catalog_closes_every_schema_semantic_contract_and_profile_identity() -> None:
    payload = catalog._catalog_payload(catalog.LTETF_02A_REQUIREMENTS)
    assert tuple(binding["schema_version"] for binding in payload["schema_bindings"]) == tuple(
        common.SCHEMA_FILENAME_BY_VERSION
    )
    assert tuple(binding["schema_path"] for binding in payload["schema_bindings"]) == tuple(
        common.SCHEMA_FILENAME_BY_VERSION.values()
    )
    assert tuple(binding["schema_identity_sha256"] for binding in payload["schema_bindings"]) == tuple(
        common.SCHEMA_IDENTITY_SHA256_BY_VERSION.values()
    )
    assert tuple(
        binding["semantic_contract_identity_sha256"]
        for binding in payload["semantic_contract_bindings"]
    ) == tuple(common.SEMANTIC_CONTRACT_IDENTITY_SHA256_BY_VERSION.values())
    expected_profiles = (
        common.NORMALIZATION_PROFILE,
        common.PROHIBITED_KEY_PROFILE,
        common.SOURCE_AUTHORITY_TAXONOMY,
        common.SUBJECT_PROFILE,
        common.LOCATOR_PROFILE,
        common.METRIC_PROFILE,
        common.UNIT_PROFILE,
        common.EVENT_PROFILE,
        common.THESIS_PROFILE,
        common.STATUS_REASON_TAXONOMY,
        common.CONFLICT_RULE_PROFILE,
        common.RESOURCE_BOUND_PROFILE,
        common.INTEGRITY_CODE_PROFILE,
    )
    assert tuple(binding["profile_version"] for binding in payload["profile_bindings"]) == tuple(
        profile.profile_version for profile in expected_profiles
    )
    assert tuple(binding["profile_identity_sha256"] for binding in payload["profile_bindings"]) == tuple(
        profile.identity_sha256 for profile in expected_profiles
    )
    catalog.validate_requirement_catalog()


def test_catalog_binds_exact_status_precedence_reason_and_integrity_tables() -> None:
    payload = catalog._catalog_payload(catalog.LTETF_02A_REQUIREMENTS)
    expected_statuses = (
        "CONFLICTING",
        "POLICY_UNRESOLVED",
        "UNAVAILABLE",
        "ABSENT",
        "INVALID",
        "FUTURE_DATED",
        "STALE",
        "VALIDATED_PRESENT",
    )
    assert tuple(payload["status_vocabulary"]) == expected_statuses
    assert tuple(payload["status_precedence"]) == expected_statuses
    assert "PRESENT_UNVALIDATED" not in payload["status_vocabulary"]
    reasons = payload["reason_codes_by_status"]
    assert tuple(reasons) == expected_statuses
    assert tuple(reasons["UNAVAILABLE"]) == (
        "BOUND_CONTENT_ABSENT",
        "BOUND_CONTENT_UNREADABLE",
        "BOUND_CONTENT_UNSTABLE",
        "BOUND_PREDECESSOR_UNAVAILABLE",
        "TRUSTED_EVALUATION_EPOCH_UNAVAILABLE",
    )
    assert tuple(reasons["CONFLICTING"])[-2:] == (
        "MULTIPLE_TRUSTED_EVALUATION_EPOCHS",
        "LOGICAL_FACT_VALUE_CONFLICT",
    )
    assert tuple(payload["integrity_codes"]) == (
        "DISCOVERY_ROOT_CANNOT_BE_ENUMERATED",
        "INVENTORY_INCOMPLETE",
        "EXPLICIT_MANIFEST_SET_BOUND_EXCEEDED",
        "CANONICAL_ARTIFACT_BOUND_EXCEEDED",
        "CANONICAL_TOTAL_BOUND_EXCEEDED",
        "FROZEN_SCHEMA_UNAVAILABLE",
        "FROZEN_PROFILE_UNAVAILABLE",
        "FROZEN_VALIDATOR_UNAVAILABLE",
        "REPOSITORY_CONTRACT_UNREADABLE",
        "IDENTITY_COMPUTATION_FAILED",
        "REPORT_SCHEMA_VALIDATION_FAILED",
        "REPORT_PRODUCER_VALIDATION_FAILED",
        "IMMUTABLE_OUTPUT_CONFLICT",
    )


def test_catalog_binds_conflict_and_resource_profiles_without_sufficiency_semantics() -> None:
    payload = catalog._catalog_payload(catalog.LTETF_02A_REQUIREMENTS)
    assert payload["conflict_rule_profile_identity_sha256"] == common.CONFLICT_RULE_PROFILE.identity_sha256
    assert payload["resource_bound_profile_identity_sha256"] == common.RESOURCE_BOUND_PROFILE.identity_sha256
    assert payload["resource_bounds"] == {
        "max_explicit_manifests": 256,
        "max_explicit_policy_payloads": 16,
        "max_explicit_acceptance_artifacts": 16,
        "max_canonical_artifact_bytes": 1_048_576,
        "max_total_canonical_input_bytes": 16_777_216,
        "max_json_depth": 32,
        "max_json_object_members": 4096,
        "max_json_array_items": 4096,
        "max_report_diagnostics": 4096,
        "investment_sufficiency_effect": "none",
    }
    conflict_payload = common.CONFLICT_RULE_PROFILE.to_payload()
    assert conflict_payload["candidate_precondition"] == "individually_schema_and_semantic_valid"
    assert conflict_payload["invalid_candidate_conflict_effect"] == "none"
    assert tuple(rule["class_id"] for rule in conflict_payload["class_rules"]) == (
        "source_authority_policy",
        "authorized_source_registry",
        "field_freshness_policy",
        "operator_policy_acceptance",
        "generic_evidence_manifest",
        "trusted_evaluation_epoch",
        "structured_market_metrics",
        "structured_scheduled_events",
        "prior_thesis_continuity",
    )


def test_requirements_contain_no_minimum_sufficiency_action_permission_or_order_fields() -> None:
    payloads = [_independent_requirement_payload(record) for record in catalog.LTETF_02A_REQUIREMENTS]
    encoded = _independent_canonical(payloads).decode("ascii")
    for prohibited_key in (
        '"min_items"',
        '"sufficiency"',
        '"actionability"',
        '"permission"',
        '"approval"',
        '"portfolio_state"',
        '"quantity"',
        '"order_readiness"',
    ):
        assert prohibited_key not in encoded


def test_catalog_binds_only_runtime_classes_and_future_standalone_cli_boundary() -> None:
    payload = catalog._catalog_payload(catalog.LTETF_02A_REQUIREMENTS)
    assert tuple(payload["runtime_evidence_classes"]) == (
        "trusted_evaluation_epoch",
        "structured_market_metrics",
        "structured_scheduled_events",
        "prior_thesis_continuity",
    )
    cli_path = "src/investment_orchestrator/cli/observe_ltetf_evidence_inventory.py"
    assert payload["eventual_external_observer_consumers"] == [cli_path]
    assert not (ROOT / cli_path).exists()
    assert tuple(payload["prohibited_consumer_categories"]) == (
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


def test_catalog_identity_contains_no_runtime_values_or_pointer_names() -> None:
    encoded = _independent_canonical(catalog._catalog_payload(catalog.LTETF_02A_REQUIREMENTS)).decode("ascii")
    for runtime_fragment in (
        "inputs/current",
        "artifacts/",
        "/home/",
        "evaluation_epoch_utc\":\"",
        "current_pointer",
        "latest_pointer",
        "active_pointer",
        "canonical_pointer",
    ):
        assert runtime_fragment not in encoded


def _production_import_edges() -> list[tuple[str, str]]:
    edges: list[tuple[str, str]] = []
    src_root = ROOT / "src"
    for path in sorted(src_root.rglob("*.py")):
        module = ".".join(path.relative_to(src_root).with_suffix("").parts)
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name in (COMMON_MODULE, CATALOG_MODULE):
                        edges.append((module, alias.name))
            elif isinstance(node, ast.ImportFrom) and node.module:
                if node.module in (COMMON_MODULE, CATALOG_MODULE):
                    edges.append((module, node.module))
                elif node.module == "investment_orchestrator.observability":
                    for alias in node.names:
                        target = f"{node.module}.{alias.name}"
                        if target in (COMMON_MODULE, CATALOG_MODULE):
                            edges.append((module, target))
    return edges


def test_internal_catalog_to_common_edge_is_the_only_production_edge_and_external_consumers_are_empty() -> None:
    assert _production_import_edges() == [(CATALOG_MODULE, COMMON_MODULE)]
    for path in sorted((ROOT / "src").rglob("*.py")):
        if path.name in {
            "ltetf_evidence_contract_common.py",
            "ltetf_evidence_requirement_catalog.py",
        }:
            continue
        text = path.read_text(encoding="utf-8")
        assert "ltetf_evidence_contract_common" not in text
        assert "ltetf_evidence_requirement_catalog" not in text
        assert "ltetf_source_authority_policy.schema.json" not in text
        assert "ltetf_generic_evidence_manifest.schema.json" not in text


class _ProfilePayloadOverride:
    """A test-only immutable-looking profile replacement with changed payload."""

    def __init__(
        self,
        profile: object,
        mutate: Callable[[dict[str, object]], None],
        *,
        identity_sha256: str | None = None,
    ) -> None:
        self.profile_version = getattr(profile, "profile_version")
        self.identity_sha256 = (
            getattr(profile, "identity_sha256")
            if identity_sha256 is None
            else identity_sha256
        )
        payload = deepcopy(getattr(profile, "to_payload")())
        mutate(payload)
        self._payload = payload

    def to_payload(self) -> dict[str, object]:
        return deepcopy(self._payload)


def _replacement_mapping(mapping: object, key: str, value: object) -> MappingProxyType:
    replacement = dict(mapping)  # type: ignore[arg-type]
    replacement[key] = value
    return MappingProxyType(replacement)


def _assert_common_drift_is_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
    attribute: str,
    replacement: object,
) -> None:
    frozen_catalog_identity = catalog.LTETF_02A_CATALOG_IDENTITY_SHA256
    frozen_requirement_identities = catalog.LTETF_02A_REQUIREMENT_IDENTITIES_SHA256
    monkeypatch.setattr(common, attribute, replacement)
    try:
        dynamic_identity = catalog.catalog_identity_sha256()
    except ValueError:
        dynamic_identity = None
    else:
        assert dynamic_identity != frozen_catalog_identity
    with pytest.raises(ValueError):
        catalog.validate_requirement_catalog()
    assert catalog.LTETF_02A_CATALOG_IDENTITY_SHA256 == frozen_catalog_identity
    assert catalog.LTETF_02A_REQUIREMENT_IDENTITIES_SHA256 == frozen_requirement_identities


def _reverse_status_vocabulary(payload: dict[str, object]) -> None:
    statuses = list(reversed(payload["statuses"]))
    reason_codes = payload["reason_codes_by_status"]
    assert type(reason_codes) is dict
    payload["statuses"] = statuses
    payload["precedence"] = list(statuses)
    payload["reason_codes_by_status"] = {
        status: list(reason_codes[status]) for status in statuses
    }


def _alter_status_precedence(payload: dict[str, object]) -> None:
    precedence = list(payload["precedence"])
    precedence[0], precedence[1] = precedence[1], precedence[0]
    payload["precedence"] = precedence


def _alter_reason_codes(payload: dict[str, object]) -> None:
    reason_codes = payload["reason_codes_by_status"]
    assert type(reason_codes) is dict
    reason_codes["CONFLICTING"] = [*reason_codes["CONFLICTING"], "DRIFT_REASON"]


def _alter_integrity_codes(payload: dict[str, object]) -> None:
    integrity_codes = payload["integrity_codes"]
    assert type(integrity_codes) is list
    payload["integrity_codes"] = [*integrity_codes, "DRIFT_INTEGRITY_CODE"]


def _alter_resource_bounds(payload: dict[str, object]) -> None:
    payload["max_report_diagnostics"] = 4095


def _alter_runtime_evidence_classes(payload: dict[str, object]) -> None:
    bindings = payload["required_subject_kind_by_evidence_class"]
    assert type(bindings) is list
    payload["required_subject_kind_by_evidence_class"] = list(reversed(bindings))


@pytest.mark.parametrize(
    ("attribute", "replacement_factory"),
    (
        (
            "SCHEMA_IDENTITY_SHA256_BY_VERSION",
            lambda: _replacement_mapping(
                common.SCHEMA_IDENTITY_SHA256_BY_VERSION,
                "ltetf_source_authority_policy_v1",
                "0" * 64,
            ),
        ),
        (
            "SEMANTIC_CONTRACT_IDENTITY_SHA256_BY_VERSION",
            lambda: _replacement_mapping(
                common.SEMANTIC_CONTRACT_IDENTITY_SHA256_BY_VERSION,
                "ltetf_source_authority_policy_v1",
                "0" * 64,
            ),
        ),
        (
            "METRIC_PROFILE",
            lambda: replace(common.METRIC_PROFILE, identity_sha256="0" * 64),
        ),
        (
            "STATUS_REASON_TAXONOMY",
            lambda: _ProfilePayloadOverride(common.STATUS_REASON_TAXONOMY, _reverse_status_vocabulary),
        ),
        (
            "STATUS_REASON_TAXONOMY",
            lambda: _ProfilePayloadOverride(common.STATUS_REASON_TAXONOMY, _alter_status_precedence),
        ),
        (
            "STATUS_REASON_TAXONOMY",
            lambda: _ProfilePayloadOverride(common.STATUS_REASON_TAXONOMY, _alter_reason_codes),
        ),
        (
            "INTEGRITY_CODE_PROFILE",
            lambda: _ProfilePayloadOverride(common.INTEGRITY_CODE_PROFILE, _alter_integrity_codes),
        ),
        (
            "CONFLICT_RULE_PROFILE",
            lambda: replace(common.CONFLICT_RULE_PROFILE, identity_sha256="0" * 64),
        ),
        (
            "RESOURCE_BOUND_PROFILE",
            lambda: replace(common.RESOURCE_BOUND_PROFILE, identity_sha256="0" * 64),
        ),
        (
            "RESOURCE_BOUND_PROFILE",
            lambda: _ProfilePayloadOverride(common.RESOURCE_BOUND_PROFILE, _alter_resource_bounds),
        ),
        (
            "SUBJECT_PROFILE",
            lambda: _ProfilePayloadOverride(common.SUBJECT_PROFILE, _alter_runtime_evidence_classes),
        ),
        (
            "DOMAIN_SEPARATORS",
            lambda: _replacement_mapping(
                common.DOMAIN_SEPARATORS,
                "catalog",
                b"ltetf_02a_catalog_drift_v1\0",
            ),
        ),
    ),
    ids=(
        "schema-identity",
        "semantic-contract-identity",
        "profile-identity",
        "status-vocabulary",
        "status-precedence",
        "reason-codes",
        "integrity-codes",
        "conflict-profile-identity",
        "resource-profile-identity",
        "resource-profile-record",
        "runtime-evidence-classes",
        "domain-separator",
    ),
)
def test_common_contract_drift_fails_closed_without_refreshing_frozen_catalog_identities(
    monkeypatch: pytest.MonkeyPatch,
    attribute: str,
    replacement_factory: Callable[[], object],
) -> None:
    _assert_common_drift_is_fail_closed(monkeypatch, attribute, replacement_factory())


def test_canonical_hash_capability_drift_fails_closed_without_refreshing_frozen_identities(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = common.domain_separated_sha256

    def drifted_hash(domain_separator: bytes, payload: object) -> str:
        if domain_separator == common.DOMAIN_SEPARATORS["catalog"]:
            return "f" * 64
        return original(domain_separator, payload)

    _assert_common_drift_is_fail_closed(monkeypatch, "domain_separated_sha256", drifted_hash)


def test_catalog_has_one_private_common_source_of_truth_with_runtime_enforcement() -> None:
    source = (
        ROOT
        / "src/investment_orchestrator/observability/ltetf_evidence_requirement_catalog.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)
    common_imports = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and node.module == "investment_orchestrator.observability"
        and any(alias.name == "ltetf_evidence_contract_common" for alias in node.names)
    ]
    assert len(common_imports) == 1
    assert [(alias.name, alias.asname) for alias in common_imports[0].names] == [
        ("ltetf_evidence_contract_common", "_common")
    ]
    assigned_names = {
        target.id
        for node in ast.walk(tree)
        if isinstance(node, (ast.Assign, ast.AnnAssign))
        for target in (
            node.targets if isinstance(node, ast.Assign) else (node.target,)
        )
        if isinstance(target, ast.Name)
    }
    assert not assigned_names & {
        "DOMAIN_SEPARATORS",
        "SCHEMA_IDENTITY_SHA256_BY_VERSION",
        "SEMANTIC_CONTRACT_IDENTITY_SHA256_BY_VERSION",
        "EVIDENCE_CLASSES",
        "SOURCE_CLASSES",
        "POLICY_TYPES",
        "STATUSES",
        "STATUS_PRECEDENCE",
        "REASON_CODES_BY_STATUS",
        "INTEGRITY_CODES",
        "CONFLICT_RULE_PROFILE",
        "RESOURCE_BOUND_PROFILE",
    }
    common_attributes = {
        node.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "_common"
    }
    assert {
        "DOMAIN_SEPARATORS",
        "SCHEMA_FILENAME_BY_VERSION",
        "SCHEMA_IDENTITY_SHA256_BY_VERSION",
        "SEMANTIC_CONTRACT_IDENTITY_SHA256_BY_VERSION",
        "STATUS_REASON_TAXONOMY",
        "CONFLICT_RULE_PROFILE",
        "RESOURCE_BOUND_PROFILE",
        "INTEGRITY_CODE_PROFILE",
        "SUBJECT_PROFILE",
        "canonical_json_bytes",
        "domain_separated_sha256",
    } <= common_attributes
    assert catalog._common is common


def _top_level_calls(tree: ast.Module) -> tuple[ast.Call, ...]:
    calls: list[ast.Call] = []

    class Visitor(ast.NodeVisitor):
        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            return

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
            return

        def visit_ClassDef(self, node: ast.ClassDef) -> None:
            return

        def visit_Call(self, node: ast.Call) -> None:
            calls.append(node)
            self.generic_visit(node)

    visitor = Visitor()
    for statement in tree.body:
        visitor.visit(statement)
    return tuple(calls)


def test_import_time_static_construction_has_no_filesystem_or_runtime_artifact_read() -> None:
    for filename in (
        "ltetf_evidence_contract_common.py",
        "ltetf_evidence_requirement_catalog.py",
    ):
        path = ROOT / "src/investment_orchestrator/observability" / filename
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for call in _top_level_calls(tree):
            if isinstance(call.func, ast.Name):
                assert call.func.id not in {"open", "__import__"}
            if isinstance(call.func, ast.Attribute):
                assert call.func.attr not in {
                    "read_bytes",
                    "read_text",
                    "open",
                    "glob",
                    "rglob",
                    "iterdir",
                    "exists",
                    "is_file",
                    "is_dir",
                    "is_symlink",
                    "resolve",
                }


def test_no_runtime_validator_inventory_report_or_cli_api_exists() -> None:
    forbidden_function_names = {
        "validate_source_authority_policy",
        "validate_authorized_source_registry",
        "validate_field_freshness_policy",
        "validate_operator_policy_acceptance",
        "resolve_accepted_policy_set",
        "detect_policy_conflicts",
        "validate_evidence_manifest",
        "validate_trusted_evaluation_epoch",
        "validate_structured_market_metrics",
        "validate_structured_scheduled_events",
        "validate_prior_thesis_continuity",
        "detect_manifest_conflicts",
        "detect_evidence_fact_conflicts",
        "select_requirement_status",
        "build_report",
        "main",
    }
    for filename in (
        "ltetf_evidence_contract_common.py",
        "ltetf_evidence_requirement_catalog.py",
    ):
        path = ROOT / "src/investment_orchestrator/observability" / filename
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        defined = {
            node.name
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        assert not defined & forbidden_function_names
        assert "inputs/current" not in path.read_text(encoding="utf-8")
        assert "artifacts/" not in path.read_text(encoding="utf-8")


def test_catalog_public_api_is_exact() -> None:
    assert tuple(catalog.__all__) == (
        "RequirementRecord",
        "LTETF_02A_CATALOG_VERSION",
        "LTETF_02A_CATALOG_IDENTITY_SHA256",
        "LTETF_02A_REQUIREMENTS",
        "LTETF_02A_REQUIREMENT_IDS",
        "LTETF_02A_REQUIREMENT_IDENTITIES_SHA256",
        "requirement_identity_sha256",
        "catalog_identity_sha256",
        "validate_requirement_catalog",
    )
    public_names = {name for name in dir(catalog) if not name.startswith("_")}
    assert public_names == set(catalog.__all__)
    assert len(catalog.__all__) == len(set(catalog.__all__))
    assert all(hasattr(catalog, name) for name in catalog.__all__)
    for accidental_name in (
        "annotations",
        "dataclasses",
        "re",
        "MappingProxyType",
        "Final",
        "EVIDENCE_CLASSES",
        "SOURCE_CLASSES",
        "POLICY_TYPES",
        "STATUSES",
    ):
        assert accidental_name not in public_names
