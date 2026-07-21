from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path

from jsonschema import Draft202012Validator
import pytest

from investment_orchestrator.observability import ltetf_evidence_contract_common as common


ROOT = Path(__file__).parents[2]
SHA = "1" * 64
OTHER_SHA = "2" * 64


def _independent_canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _independent_identity(domain: bytes, payload: object) -> str:
    return hashlib.sha256(domain + _independent_canonical(payload)).hexdigest()


def _schema(version: str) -> dict[str, object]:
    value = common.parse_strict_json_bytes((ROOT / common.SCHEMA_FILENAME_BY_VERSION[version]).read_bytes())
    assert type(value) is dict
    return value


def _policy_binding(policy_type: str, policy_id: str) -> dict[str, object]:
    return {
        "policy_type": policy_type,
        "policy_id": policy_id,
        "policy_version": f"{policy_id}_v1",
        "policy_artifact_identity_sha256": SHA,
        "policy_content_identity_sha256": SHA,
        "policy_schema_identity_sha256": SHA,
        "policy_contract_identity_sha256": SHA,
    }


def _manifest_policy_binding(policy_type: str, policy_id: str) -> dict[str, object]:
    value = _policy_binding(policy_type, policy_id)
    value["acceptance_artifact_identity_sha256"] = SHA
    return value


def _minimal_payloads() -> dict[str, dict[str, object]]:
    source_policy_binding = _policy_binding("source_authority_policy", "ltetf_source_authority_policy")
    registry_binding = _policy_binding("authorized_source_registry", "ltetf_authorized_source_registry")
    source_policy = {
        "schema_version": "ltetf_source_authority_policy_v1",
        "policy_type": "source_authority_policy",
        "authority_effect": "none",
        "policy_content": {
            "policy_id": "ltetf_source_authority_policy",
            "policy_version": "ltetf_source_authority_policy_v1",
            "policy_schema_identity_sha256": SHA,
            "policy_contract_identity_sha256": SHA,
            "rules": [
                {
                    "evidence_class": evidence_class,
                    "allowed_source_classes": ["repository_derived"],
                    "registry_required": True,
                }
                for evidence_class in (
                    "trusted_evaluation_epoch",
                    "structured_market_metrics",
                    "structured_scheduled_events",
                    "prior_thesis_continuity",
                )
            ],
        },
        "policy_content_identity_sha256": SHA,
        "policy_artifact_identity_sha256": SHA,
    }
    registry = {
        "schema_version": "ltetf_authorized_source_registry_v1",
        "policy_type": "authorized_source_registry",
        "authority_effect": "none",
        "policy_content": {
            "policy_id": "ltetf_authorized_source_registry",
            "policy_version": "ltetf_authorized_source_registry_v1",
            "policy_schema_identity_sha256": SHA,
            "policy_contract_identity_sha256": SHA,
            "source_authority_policy_binding": source_policy_binding,
            "sources": [],
        },
        "policy_content_identity_sha256": SHA,
        "policy_artifact_identity_sha256": SHA,
    }
    freshness = {
        "schema_version": "ltetf_field_freshness_policy_v1",
        "policy_type": "field_freshness_policy",
        "authority_effect": "none",
        "policy_content": {
            "policy_id": "ltetf_field_freshness_policy",
            "policy_version": "ltetf_field_freshness_policy_v1",
            "policy_schema_identity_sha256": SHA,
            "policy_contract_identity_sha256": SHA,
            "source_authority_policy_binding": source_policy_binding,
            "authorized_source_registry_binding": registry_binding,
            "rules": [
                {
                    "evidence_class": "structured_market_metrics",
                    "field_profile_id": "market_metric_value",
                    "timestamp_field": "observed_at_utc",
                    "max_age_seconds": 86400,
                    "future_tolerance_seconds": 0,
                },
                {
                    "evidence_class": "structured_scheduled_events",
                    "field_profile_id": "scheduled_event_record",
                    "timestamp_field": "published_at_utc",
                    "max_age_seconds": 604800,
                    "future_tolerance_seconds": 0,
                },
                {
                    "evidence_class": "prior_thesis_continuity",
                    "field_profile_id": "prior_thesis_record",
                    "timestamp_field": "recorded_at_utc",
                    "max_age_seconds": 31536000,
                    "future_tolerance_seconds": 0,
                },
            ],
        },
        "policy_content_identity_sha256": SHA,
        "policy_artifact_identity_sha256": SHA,
    }
    acceptance = {
        "schema_version": "ltetf_operator_policy_acceptance_v1",
        "acceptance_id": "source-policy-acceptance-v1",
        "accepted_policy_type": "source_authority_policy",
        "policy_id": "ltetf_source_authority_policy",
        "policy_version": "ltetf_source_authority_policy_v1",
        "policy_artifact_identity_sha256": SHA,
        "policy_content_identity_sha256": SHA,
        "policy_schema_identity_sha256": SHA,
        "policy_contract_identity_sha256": SHA,
        "ltetf_02a_catalog_identity_sha256": SHA,
        "authority_effect": "none",
        "acceptance_artifact_identity_sha256": SHA,
    }
    manifest = {
        "schema_version": "ltetf_generic_evidence_manifest_v1",
        "manifest_id": "epoch-manifest-001",
        "evidence_class": "trusted_evaluation_epoch",
        "evidence_subject": {
            "subject_kind": "evaluation_context",
            "canonical_subject_id": "evaluation_context:ltetf_evidence_observation",
            "subject_identity_profile_id": "ltetf_evaluation_context_subject_v1",
        },
        "subject_identity_sha256": SHA,
        "source_bindings": [
            {
                "source_id": "repository-clock",
                "source_record_identity_sha256": SHA,
                "source_class": "repository_derived",
                "authorized_source_registry_artifact_identity_sha256": SHA,
            }
        ],
        "content_binding": {
            "repository_relative_path": "evidence/epoch.json",
            "media_type": "application/json",
            "content_bytes_sha256": SHA,
            "content_identity_sha256": SHA,
            "content_schema_version": "ltetf_trusted_evaluation_epoch_v1",
            "content_schema_identity_sha256": SHA,
            "content_contract_identity_sha256": SHA,
        },
        "producer_binding": {
            "producer_id": "operator-input",
            "producer_version": "operator_input_v1",
            "producer_contract_identity_sha256": SHA,
            "producer_identity_sha256": SHA,
        },
        "acquired_at_utc": "2026-07-20T12:00:00Z",
        "normalization_identity_sha256": SHA,
        "policy_bindings": {
            "source_authority_policy": _manifest_policy_binding(
                "source_authority_policy", "ltetf_source_authority_policy"
            ),
            "authorized_source_registry": _manifest_policy_binding(
                "authorized_source_registry", "ltetf_authorized_source_registry"
            ),
            "field_freshness_policy": None,
        },
        "ltetf_02a_catalog_identity_sha256": SHA,
        "predecessor_binding": None,
        "authority_effect": "none",
        "manifest_identity_sha256": SHA,
    }
    return {
        "ltetf_source_authority_policy_v1": source_policy,
        "ltetf_authorized_source_registry_v1": registry,
        "ltetf_field_freshness_policy_v1": freshness,
        "ltetf_operator_policy_acceptance_v1": acceptance,
        "ltetf_generic_evidence_manifest_v1": manifest,
        "ltetf_trusted_evaluation_epoch_v1": {
            "schema_version": "ltetf_trusted_evaluation_epoch_v1",
            "evidence_class": "trusted_evaluation_epoch",
            "subject_identity_sha256": SHA,
            "evaluation_epoch_utc": "2026-07-20T12:00:00Z",
            "authority_effect": "none",
        },
        "ltetf_structured_market_metrics_v1": {
            "schema_version": "ltetf_structured_market_metrics_v1",
            "evidence_class": "structured_market_metrics",
            "subject_identity_sha256": SHA,
            "records": [],
            "authority_effect": "none",
        },
        "ltetf_structured_scheduled_events_v1": {
            "schema_version": "ltetf_structured_scheduled_events_v1",
            "evidence_class": "structured_scheduled_events",
            "subject_identity_sha256": SHA,
            "records": [],
            "authority_effect": "none",
        },
        "ltetf_prior_thesis_continuity_v1": {
            "schema_version": "ltetf_prior_thesis_continuity_v1",
            "evidence_class": "prior_thesis_continuity",
            "subject_identity_sha256": SHA,
            "thesis_record_id": "thesis-001",
            "thesis_identity_profile_id": "ltetf_thesis_record_identity_profile_v1",
            "recorded_at_utc": "2026-07-19T12:00:00Z",
            "thesis_text": "Neutral prior thesis text.",
            "predecessor_manifest_identity_sha256": None,
            "predecessor_content_identity_sha256": None,
            "evidence_references": [],
            "authority_effect": "none",
        },
    }


def test_strict_json_rejects_duplicate_keys_floats_nonfinite_and_invalid_utf8() -> None:
    for data, code in (
        (b'{"a":1,"a":2}', "DUPLICATE_OBJECT_KEY"),
        (b"1.0", "FLOAT_NOT_ALLOWED"),
        (b"NaN", "NONFINITE_NUMBER_NOT_ALLOWED"),
        (b"Infinity", "NONFINITE_NUMBER_NOT_ALLOWED"),
        (b'"\xff"', "JSON_UTF8_INVALID"),
    ):
        with pytest.raises(common.CanonicalizationError, match=code):
            common.parse_strict_json_bytes(data)


def test_canonicalization_rejects_non_exact_types_non_ascii_keys_and_surrogates() -> None:
    class DictSubclass(dict):
        pass

    class ListSubclass(list):
        pass

    class StringSubclass(str):
        pass

    class IntegerSubclass(int):
        pass

    values = (
        DictSubclass(),
        ListSubclass(),
        StringSubclass("x"),
        IntegerSubclass(1),
        {"é": 1},
        {"value": "\ud800"},
    )
    for value in values:
        with pytest.raises(common.CanonicalizationError):
            common.canonical_json_bytes(value)
    with pytest.raises(common.CanonicalizationError, match="SURROGATE_NOT_ALLOWED"):
        common.parse_strict_json_bytes(b'"\\ud800"')


def test_booleans_are_not_accepted_as_integers() -> None:
    locator = {
        "locator_kind": "https_origin",
        "scheme": "https",
        "host_ascii": "example.com",
        "port": True,
        "path_prefix": "/",
    }
    with pytest.raises(common.IdentityDefinitionError, match="HTTPS_PORT_INVALID"):
        common.canonical_source_locator_payload(locator)


def test_canonical_serialization_is_stable_and_never_reorders_arrays() -> None:
    first = {"z": [3, 1, 2], "a": {"b": True, "a": None}}
    equivalent = {"a": {"a": None, "b": True}, "z": [3, 1, 2]}
    different = {"z": [1, 2, 3], "a": {"b": True, "a": None}}
    assert common.canonical_json_bytes(first) == _independent_canonical(first)
    assert common.canonical_json_bytes(first) == common.canonical_json_bytes(equivalent)
    assert common.canonical_json_bytes(first) != common.canonical_json_bytes(different)
    assert common.parse_strict_json_bytes(common.canonical_json_bytes(first)) == first


@pytest.mark.parametrize(
    "path",
    (
        "/absolute",
        "C:/drive",
        "folder\\file",
        "folder\x00file",
        "folder//file",
        "folder/./file",
        "folder/../file",
        "../file",
        "folder/",
        "é/file",
        "",
        "a" * 513,
    ),
)
def test_repository_relative_path_profile_rejects_invalid_lexical_paths(path: str) -> None:
    with pytest.raises(common.PathSyntaxError):
        common.validate_repository_relative_path_syntax(path)


def test_repository_relative_path_profile_accepts_valid_paths_without_filesystem_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unexpected(*_: object, **__: object) -> object:
        raise AssertionError("filesystem call is forbidden")

    for method in ("exists", "is_file", "is_dir", "is_symlink", "resolve", "read_bytes", "read_text"):
        monkeypatch.setattr(Path, method, unexpected)
    assert common.validate_repository_relative_path_syntax("schemas/contract-v1.schema.json") == (
        "schemas/contract-v1.schema.json"
    )
    assert common.validate_repository_relative_path_syntax("x") == "x"
    assert common.validate_repository_relative_path_syntax("evidence/a b.json") == "evidence/a b.json"
    assert common.validate_repository_relative_path_syntax("evidence/a\tb.json") == "evidence/a\tb.json"


@pytest.mark.parametrize(
    ("raw", "normalized"),
    (
        ("acceptanceState", "acceptance_state"),
        ("BUY-Quantity", "buy_quantity"),
        ("__Final  Gate__", "final_gate"),
        ("source.class", "source_class"),
        ("HTTP2Value", "http2_value"),
    ),
)
def test_prohibited_key_normalization_is_exact(raw: str, normalized: str) -> None:
    assert common.normalize_prohibited_key(raw) == normalized


def test_every_frozen_prohibited_key_is_detected_and_neutral_keys_are_allowed() -> None:
    profile = common.PROHIBITED_KEY_PROFILE.to_payload()
    for key in profile["prohibited_keys"]:
        assert common.find_prohibited_keys({key: None}) == (f"/{key}",)
    for key in profile["required_neutral_keys"]:
        assert common.find_prohibited_keys({key: None}) == ()
    for key in ("ticker", "symbol", "instrument_id", "approvals", "preapproval_notes", "canonical_id"):
        assert common.find_prohibited_keys({key: None}) == ()


def test_prohibited_key_search_is_recursive_through_objects_and_arrays_without_fuzzy_matching() -> None:
    value = {"outer": [{"action": None}, {"buying_context": {"approval": None}}]}
    assert common.find_prohibited_keys(value) == (
        "/outer/0/action",
        "/outer/1/buying_context/approval",
    )
    assert common.find_prohibited_keys({"action_notes": None, "relevance_window": None}) == ()
    with pytest.raises(common.CanonicalizationError, match="OBJECT_KEY_NON_ASCII"):
        common.find_prohibited_keys({"actión": None})


def test_schema_property_inspection_ignores_json_schema_keywords() -> None:
    schema_fragment = {
        "properties": {"authority_effect": {"const": "none"}, "action": {"type": "string"}},
        "required": ["action"],
        "description": "action",
    }
    assert common.find_prohibited_keys(schema_fragment, schema_property_names_only=True) == (
        "/properties/action",
    )


def test_all_domain_separators_are_exact_bytes_unique_and_unambiguous() -> None:
    domains = tuple(common.DOMAIN_SEPARATORS.values())
    assert len(domains) == 31
    assert len(domains) == len(set(domains))
    assert all(type(domain) is bytes and domain.endswith(b"\0") and b"\0" not in domain[:-1] for domain in domains)


def test_normalization_profile_closes_every_identity_payload_definition_in_domain_order() -> None:
    definitions = common.NORMALIZATION_PROFILE.to_payload()["identity_payload_definitions"]
    assert len(definitions) == len(common.DOMAIN_SEPARATORS) == 31
    assert tuple(definition["domain_name"] for definition in definitions) == tuple(
        common.DOMAIN_SEPARATORS
    )
    assert len({definition["identity_name"] for definition in definitions}) == 31
    assert all(definition["payload_fields"] for definition in definitions)
    assert all(
        tuple(definition) == (
            "identity_name",
            "domain_name",
            "payload_fields",
            "excluded_self_field",
            "array_ordering",
            "path_normalization",
            "duplicate_rule",
        )
        for definition in definitions
    )
    by_name = {definition["identity_name"]: definition for definition in definitions}
    assert by_name["individual_requirement"]["excluded_self_field"] == (
        "requirement_identity_sha256"
    )
    assert by_name["policy_payload_artifact"]["excluded_self_field"] == (
        "policy_artifact_identity_sha256"
    )
    assert by_name["operator_policy_acceptance_artifact"]["excluded_self_field"] == (
        "acceptance_artifact_identity_sha256"
    )
    assert by_name["generic_evidence_manifest"]["excluded_self_field"] == (
        "manifest_identity_sha256"
    )
    for content_name in (
        "trusted_epoch_content",
        "structured_metrics_content",
        "structured_events_content",
        "prior_thesis_content",
    ):
        assert by_name[content_name]["excluded_self_field"] is None
        assert "authority_effect" in by_name[content_name]["payload_fields"]


def test_every_profile_identity_is_independently_recomputed() -> None:
    profiles_and_domains = (
        (common.NORMALIZATION_PROFILE, "normalization_profile"),
        (common.PROHIBITED_KEY_PROFILE, "prohibited_key_profile"),
        (common.SOURCE_AUTHORITY_TAXONOMY, "source_authority_taxonomy"),
        (common.SUBJECT_PROFILE, "subject_profile"),
        (common.LOCATOR_PROFILE, "locator_profile"),
        (common.METRIC_PROFILE, "metric_profile"),
        (common.UNIT_PROFILE, "unit_profile"),
        (common.EVENT_PROFILE, "event_profile"),
        (common.THESIS_PROFILE, "thesis_profile"),
        (common.STATUS_REASON_TAXONOMY, "status_reason_taxonomy"),
        (common.CONFLICT_RULE_PROFILE, "conflict_rule_profile"),
        (common.RESOURCE_BOUND_PROFILE, "resource_bound_profile"),
        (common.INTEGRITY_CODE_PROFILE, "integrity_code_profile"),
    )
    for profile, domain_name in profiles_and_domains:
        assert profile.identity_sha256 == _independent_identity(
            common.DOMAIN_SEPARATORS[domain_name], profile.to_payload()
        )
        with pytest.raises((AttributeError, TypeError)):
            profile.identity_sha256 = OTHER_SHA


def test_schema_identities_are_independently_recomputed_and_explicit_loader_verifies() -> None:
    identities: set[str] = set()
    for version, path in common.SCHEMA_FILENAME_BY_VERSION.items():
        schema = _schema(version)
        independent_payload = {
            "schema_version": version,
            "schema_path": path,
            "schema_id": schema["$id"],
            "normalization_profile_identity_sha256": common.NORMALIZATION_PROFILE.identity_sha256,
            "schema": schema,
        }
        expected = _independent_identity(common.DOMAIN_SEPARATORS["schema_identity"], independent_payload)
        assert expected == common.SCHEMA_IDENTITY_SHA256_BY_VERSION[version]
        assert common.schema_identity_sha256(version, path, schema) == expected
        assert common.load_and_verify_frozen_schema(ROOT, version) == schema
        identities.add(expected)
    assert len(identities) == 9


def test_schema_identity_binds_path_id_complete_object_and_normalization_identity() -> None:
    version = "ltetf_trusted_evaluation_epoch_v1"
    path = common.SCHEMA_FILENAME_BY_VERSION[version]
    schema = _schema(version)
    baseline = common.schema_identity_sha256(version, path, schema)
    altered = deepcopy(schema)
    altered["description"] = f"{altered['description']} altered"
    assert common.schema_identity_sha256(version, path, altered) != baseline
    with pytest.raises(common.IdentityDefinitionError, match="SCHEMA_PATH_MISMATCH"):
        common.schema_identity_sha256(version, "schemas/other.schema.json", schema)
    wrong_id = deepcopy(schema)
    wrong_id["$id"] = "https://investment-system.local/schemas/other.schema.json"
    with pytest.raises(common.IdentityDefinitionError, match="SCHEMA_ID_MISMATCH"):
        common.schema_identity_sha256(version, path, wrong_id)


def test_every_semantic_contract_identity_is_independently_recomputed() -> None:
    for version, expected in common.SEMANTIC_CONTRACT_IDENTITY_SHA256_BY_VERSION.items():
        record = common._semantic_contract_record(version)
        assert expected == _independent_identity(
            common.DOMAIN_SEPARATORS["semantic_contract_identity"], record
        )
        with_self = {**record, "semantic_contract_identity_sha256": OTHER_SHA}
        assert common.semantic_contract_identity_sha256(with_self) == expected
        altered = deepcopy(record)
        altered["ordered_semantic_rule_ids"] = [*altered["ordered_semantic_rule_ids"], "ALTERED_RULE"]
        assert common.semantic_contract_identity_sha256(altered) != expected


def test_subject_identity_is_neutral_exact_and_binds_all_subject_fields() -> None:
    subject = {
        "subject_kind": "instrument",
        "canonical_subject_id": "ticker:SPY",
        "subject_identity_profile_id": "ltetf_instrument_subject_v1",
    }
    expected = _independent_identity(
        common.DOMAIN_SEPARATORS["evidence_subject"],
        common.canonical_evidence_subject_payload(subject),
    )
    assert common.evidence_subject_identity_sha256(subject) == expected
    altered = {**subject, "canonical_subject_id": "ticker:QQQ"}
    assert common.evidence_subject_identity_sha256(altered) != expected
    for prohibited_field in ("in_universe", "actionable", "position"):
        with pytest.raises(common.IdentityDefinitionError, match="SUBJECT_FIELDS_INVALID"):
            common.evidence_subject_identity_sha256({**subject, prohibited_field: False})


def test_all_three_locator_identities_are_canonical_and_non_dereferencing() -> None:
    locators = (
        {"locator_kind": "repository_path", "repository_relative_path": "evidence/source.json"},
        {
            "locator_kind": "https_origin",
            "scheme": "https",
            "host_ascii": "data.example.com",
            "port": None,
            "path_prefix": "/v1",
        },
        {"locator_kind": "opaque_source_id", "namespace": "vendor", "opaque_id": "feed:ABC/1"},
    )
    identities = []
    for locator in locators:
        payload = common.canonical_source_locator_payload(locator)
        expected = _independent_identity(common.DOMAIN_SEPARATORS["source_locator"], payload)
        assert common.source_locator_identity_sha256(locator) == expected
        identities.append(expected)
    assert len(set(identities)) == 3
    with pytest.raises(common.IdentityDefinitionError, match="HTTPS_HOST_INVALID"):
        common.source_locator_identity_sha256({**locators[1], "host_ascii": "DATA.example.com"})
    with pytest.raises(common.IdentityDefinitionError, match="HTTPS_PORT_INVALID"):
        common.source_locator_identity_sha256({**locators[1], "port": 443})


def test_source_record_identity_excludes_only_own_hash_and_binds_locator_hash_and_order() -> None:
    locator = {"locator_kind": "repository_path", "repository_relative_path": "evidence/source.json"}
    locator_identity = common.source_locator_identity_sha256(locator)
    record = {
        "source_id": "repository-source",
        "source_identity_profile_id": "ltetf_source_identity_profile_v1",
        "source_class": "repository_derived",
        "source_locator": locator,
        "source_locator_identity_sha256": locator_identity,
        "authorized_evidence_classes": [
            "trusted_evaluation_epoch",
            "structured_market_metrics",
        ],
        "source_record_identity_sha256": SHA,
    }
    expected_payload = common.canonical_authorized_source_record_payload(record)
    expected = _independent_identity(common.DOMAIN_SEPARATORS["authorized_source_record"], expected_payload)
    assert common.authorized_source_record_identity_sha256(record) == expected
    assert common.authorized_source_record_identity_sha256(
        {**record, "source_record_identity_sha256": OTHER_SHA}
    ) == expected
    changed_locator = {"locator_kind": "repository_path", "repository_relative_path": "evidence/other.json"}
    changed = {
        **record,
        "source_locator": changed_locator,
        "source_locator_identity_sha256": common.source_locator_identity_sha256(changed_locator),
    }
    assert common.authorized_source_record_identity_sha256(changed) != expected
    with pytest.raises(common.IdentityDefinitionError, match="SOURCE_LOCATOR_IDENTITY_MISMATCH"):
        common.authorized_source_record_identity_sha256({**record, "source_locator_identity_sha256": OTHER_SHA})
    with pytest.raises(common.IdentityDefinitionError, match="AUTHORIZED_EVIDENCE_CLASSES_ORDER_INVALID"):
        common.authorized_source_record_identity_sha256(
            {**record, "authorized_evidence_classes": list(reversed(record["authorized_evidence_classes"]))}
        )


def test_producer_identity_excludes_only_own_hash_and_binds_sibling_contract_hash() -> None:
    producer = {
        "producer_id": "operator-input",
        "producer_version": "operator_input_v1",
        "producer_contract_identity_sha256": SHA,
        "producer_identity_sha256": SHA,
    }
    expected_payload = {
        "producer_id": producer["producer_id"],
        "producer_version": producer["producer_version"],
        "producer_contract_identity_sha256": SHA,
    }
    expected = _independent_identity(common.DOMAIN_SEPARATORS["producer_identity"], expected_payload)
    assert common.producer_identity_sha256(producer) == expected
    assert common.producer_identity_sha256({**producer, "producer_identity_sha256": OTHER_SHA}) == expected
    assert common.producer_identity_sha256(
        {**producer, "producer_contract_identity_sha256": OTHER_SHA}
    ) != expected


def test_policy_nested_content_and_artifact_identities_are_non_circular_and_exact() -> None:
    policy = {
        "schema_version": "ltetf_source_authority_policy_v1",
        "policy_type": "source_authority_policy",
        "authority_effect": "none",
        "policy_content": {"policy_id": "ltetf_source_authority_policy", "rules": []},
        "policy_content_identity_sha256": SHA,
        "policy_artifact_identity_sha256": SHA,
    }
    content_payload = {
        "schema_version": policy["schema_version"],
        "policy_type": policy["policy_type"],
        "authority_effect": "none",
        "policy_content": policy["policy_content"],
    }
    expected_content = _independent_identity(
        common.DOMAIN_SEPARATORS["source_authority_policy_content"], content_payload
    )
    assert common._policy_content_identity_sha256(policy) == expected_content
    assert common._policy_content_identity_sha256(
        {**policy, "policy_content_identity_sha256": OTHER_SHA, "policy_artifact_identity_sha256": OTHER_SHA}
    ) == expected_content

    sealed = {**policy, "policy_content_identity_sha256": expected_content}
    artifact_payload = {**content_payload, "policy_content_identity_sha256": expected_content}
    expected_artifact = _independent_identity(
        common.DOMAIN_SEPARATORS["policy_payload_artifact"], artifact_payload
    )
    assert common._policy_artifact_identity_sha256(sealed) == expected_artifact
    assert common._policy_artifact_identity_sha256(
        {**sealed, "policy_artifact_identity_sha256": OTHER_SHA}
    ) == expected_artifact
    assert common._policy_artifact_identity_sha256(
        {**sealed, "policy_content_identity_sha256": OTHER_SHA}
    ) != expected_artifact
    changed_context = {
        **sealed,
        "schema_version": "ltetf_authorized_source_registry_v1",
        "policy_type": "authorized_source_registry",
    }
    assert common._policy_content_identity_sha256(changed_context) != expected_content


def test_acceptance_identity_excludes_only_its_self_hash_and_binds_policy_and_catalog() -> None:
    acceptance = _minimal_payloads()["ltetf_operator_policy_acceptance_v1"]
    payload = dict(acceptance)
    del payload["acceptance_artifact_identity_sha256"]
    expected = _independent_identity(common.DOMAIN_SEPARATORS["operator_policy_acceptance"], payload)
    assert common._acceptance_artifact_identity_sha256(acceptance) == expected
    assert common._acceptance_artifact_identity_sha256(
        {**acceptance, "acceptance_artifact_identity_sha256": OTHER_SHA}
    ) == expected
    assert common._acceptance_artifact_identity_sha256(
        {**acceptance, "ltetf_02a_catalog_identity_sha256": OTHER_SHA}
    ) != expected
    assert common._acceptance_artifact_identity_sha256(
        {**acceptance, "policy_content_identity_sha256": OTHER_SHA}
    ) != expected


def test_manifest_and_content_identity_definitions_bind_complete_frozen_payloads() -> None:
    definitions = {
        definition["identity_name"]: definition
        for definition in common.NORMALIZATION_PROFILE.to_payload()["identity_payload_definitions"]
    }
    payloads = _minimal_payloads()
    manifest = payloads["ltetf_generic_evidence_manifest_v1"]
    manifest_fields = definitions["generic_evidence_manifest"]["payload_fields"]
    assert set(manifest_fields) == set(manifest) - {"manifest_identity_sha256"}
    manifest_payload = {field: manifest[field] for field in manifest_fields}
    manifest_identity = _independent_identity(
        common.DOMAIN_SEPARATORS["generic_manifest"], manifest_payload
    )
    assert manifest_identity == _independent_identity(
        common.DOMAIN_SEPARATORS["generic_manifest"],
        {
            field: ({**manifest, "manifest_identity_sha256": OTHER_SHA})[field]
            for field in manifest_fields
        },
    )
    assert manifest_identity != _independent_identity(
        common.DOMAIN_SEPARATORS["generic_manifest"],
        {**manifest_payload, "authority_effect": "altered"},
    )

    content_definitions = (
        ("ltetf_trusted_evaluation_epoch_v1", "trusted_epoch_content", "trusted_epoch_content"),
        ("ltetf_structured_market_metrics_v1", "structured_metrics_content", "structured_metrics_content"),
        ("ltetf_structured_scheduled_events_v1", "structured_events_content", "structured_events_content"),
        ("ltetf_prior_thesis_continuity_v1", "prior_thesis_content", "prior_thesis_content"),
    )
    for version, definition_name, domain_name in content_definitions:
        content = payloads[version]
        fields = definitions[definition_name]["payload_fields"]
        assert set(fields) == set(content)
        baseline = _independent_identity(
            common.DOMAIN_SEPARATORS[domain_name],
            {field: content[field] for field in fields},
        )
        assert baseline != _independent_identity(
            common.DOMAIN_SEPARATORS[domain_name],
            {**content, "authority_effect": "altered"},
        )


def test_identity_helpers_are_independent_of_host_time_path_and_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    subject = {
        "subject_kind": "instrument",
        "canonical_subject_id": "ticker:SPY",
        "subject_identity_profile_id": "ltetf_instrument_subject_v1",
    }
    before = common.evidence_subject_identity_sha256(subject)
    monkeypatch.setenv("TZ", "Pacific/Kiritimati")
    monkeypatch.setenv("LTETF_CURRENT_TIME", "2099-01-01T00:00:00Z")
    monkeypatch.setenv("PWD", "/unrelated/host/path")
    os.environ.get("TZ")
    assert common.evidence_subject_identity_sha256(subject) == before


def test_frozen_schemas_accept_exact_minimal_structures_and_reject_result_or_action_fields() -> None:
    payloads = _minimal_payloads()
    assert tuple(payloads) == tuple(common.SCHEMA_FILENAME_BY_VERSION)
    for version, payload in payloads.items():
        validator = Draft202012Validator(_schema(version))
        assert not tuple(validator.iter_errors(payload)), version
        if "authority_effect" in payload:
            assert payload["authority_effect"] == "none"
            assert tuple(validator.iter_errors({**payload, "authority_effect": "permission"}))
        for forbidden_field in ("action", "permission", "order"):
            altered = {**payload, forbidden_field: None}
            assert tuple(validator.iter_errors(altered)), (version, forbidden_field)

    source_policy = payloads["ltetf_source_authority_policy_v1"]
    policy_validator = Draft202012Validator(_schema("ltetf_source_authority_policy_v1"))
    for self_acceptance_field in ("accepted", "acceptance_state", "activation_state", "operator_approval"):
        altered = deepcopy(source_policy)
        altered["policy_content"][self_acceptance_field] = False
        assert tuple(policy_validator.iter_errors(altered))

    manifest = payloads["ltetf_generic_evidence_manifest_v1"]
    manifest_validator = Draft202012Validator(_schema("ltetf_generic_evidence_manifest_v1"))
    for result_field in ("validation_status", "freshness_status", "sufficiency", "actionable"):
        assert tuple(manifest_validator.iter_errors({**manifest, result_field: "none"}))


def test_empty_metric_and_event_collections_are_structurally_valid_without_sufficiency_fields() -> None:
    payloads = _minimal_payloads()
    for version in ("ltetf_structured_market_metrics_v1", "ltetf_structured_scheduled_events_v1"):
        payload = payloads[version]
        assert payload["records"] == []
        assert not tuple(Draft202012Validator(_schema(version)).iter_errors(payload))
        assert "min_items" not in payload and "sufficiency" not in payload


def test_metric_schema_accepts_frozen_price_unit_and_maximal_canonical_decimal() -> None:
    payload = _minimal_payloads()["ltetf_structured_market_metrics_v1"]
    payload["records"] = [
        {
            "metric_id": "market.price",
            "metric_identity_profile_id": "ltetf_metric_identity_profile_v1",
            "value_decimal": "-" + "9" * 48 + "." + "0" * 17 + "1",
            "unit_id": "price:USD",
            "unit_identity_profile_id": "ltetf_unit_identity_profile_v1",
            "observed_at_utc": "2026-07-20T12:00:00Z",
        }
    ]
    assert len(payload["records"][0]["value_decimal"]) == 68
    assert not tuple(
        Draft202012Validator(_schema("ltetf_structured_market_metrics_v1")).iter_errors(payload)
    )


def test_manifest_path_schema_matches_p1_for_remaining_ascii_characters() -> None:
    payload = _minimal_payloads()["ltetf_generic_evidence_manifest_v1"]
    payload["content_binding"]["repository_relative_path"] = "evidence/a b.json"
    assert not tuple(
        Draft202012Validator(_schema("ltetf_generic_evidence_manifest_v1")).iter_errors(payload)
    )


def test_resource_bounds_are_exact_observer_safety_bounds_only() -> None:
    payload = common.RESOURCE_BOUND_PROFILE.to_payload()
    assert {key: payload[key] for key in payload if key.startswith("max_")} == {
        "max_explicit_manifests": 256,
        "max_explicit_policy_payloads": 16,
        "max_explicit_acceptance_artifacts": 16,
        "max_canonical_artifact_bytes": 1_048_576,
        "max_total_canonical_input_bytes": 16_777_216,
        "max_json_depth": 32,
        "max_json_object_members": 4096,
        "max_json_array_items": 4096,
        "max_report_diagnostics": 4096,
    }
    assert payload["bounds_effect"] == "observer_safety_only"
    assert payload["minimum_evidence_effect"] == "none"
    assert payload["investment_sufficiency_effect"] == "none"
    assert payload["requirement_success_effect"] == "none"


def test_common_public_api_is_exact_and_contains_no_runtime_validator() -> None:
    assert tuple(common.__all__) == (
        "CanonicalizationError",
        "IdentityDefinitionError",
        "PathSyntaxError",
        "ProhibitedKeyError",
        "parse_strict_json_bytes",
        "canonical_json_bytes",
        "domain_separated_sha256",
        "validate_repository_relative_path_syntax",
        "normalize_prohibited_key",
        "find_prohibited_keys",
        "load_and_verify_frozen_schema",
        "canonical_evidence_subject_payload",
        "evidence_subject_identity_sha256",
        "canonical_source_locator_payload",
        "source_locator_identity_sha256",
        "canonical_authorized_source_record_payload",
        "authorized_source_record_identity_sha256",
        "producer_identity_sha256",
        "schema_identity_sha256",
        "semantic_contract_identity_sha256",
        "DOMAIN_SEPARATORS",
        "SCHEMA_FILENAME_BY_VERSION",
        "SCHEMA_IDENTITY_SHA256_BY_VERSION",
        "SEMANTIC_CONTRACT_IDENTITY_SHA256_BY_VERSION",
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
    public_names = {name for name in dir(common) if not name.startswith("_")}
    assert public_names == set(common.__all__)
    assert len(common.__all__) == len(set(common.__all__))
    assert all(hasattr(common, name) for name in common.__all__)
    for accidental_name in (
        "annotations",
        "dataclasses",
        "hashlib",
        "json",
        "re",
        "Path",
        "MappingProxyType",
        "Draft202012Validator",
        "SchemaError",
        "Final",
        "Mapping",
    ):
        assert accidental_name not in public_names
    forbidden = (
        "validate_source_authority_policy",
        "validate_authorized_source_registry",
        "validate_field_freshness_policy",
        "validate_operator_policy_acceptance",
        "resolve_accepted_policy_set",
        "detect_policy_conflicts",
        "validate_evidence_manifest",
        "validate_content_for_evidence_class",
        "select_requirement_status",
    )
    assert all(not hasattr(common, name) for name in forbidden)
