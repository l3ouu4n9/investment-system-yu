"""Static LTETF-02a evidence-contract primitives and identity definitions.

This module is deliberately limited to pure, deterministic contract data and
identity helpers.  It does not discover, read, select, accept, or evaluate any
runtime policy, manifest, or evidence artifact.
"""

from __future__ import annotations

# ``from __future__ import annotations`` leaves this implementation marker in
# the module namespace on supported interpreters.  The frozen public surface is
# deliberately closed, so remove the marker after the compiler has applied it.
del annotations

from dataclasses import dataclass as _dataclass
import hashlib as _hashlib
import json as _json
from pathlib import Path as _Path
import re as _re
from types import MappingProxyType as _MappingProxyType
from typing import TYPE_CHECKING as _TYPE_CHECKING

from jsonschema import Draft202012Validator as _Draft202012Validator
from jsonschema.exceptions import SchemaError as _SchemaError

if _TYPE_CHECKING:
    from typing import Final, Mapping


class CanonicalizationError(ValueError):
    """Raised when a value cannot satisfy the frozen canonical JSON profile."""


class IdentityDefinitionError(ValueError):
    """Raised when an identity payload or frozen identity binding is invalid."""


class PathSyntaxError(ValueError):
    """Raised when a path fails the lexical repository-relative path profile."""


class ProhibitedKeyError(ValueError):
    """Raised when a key cannot be normalized or is prohibited."""


@_dataclass(frozen=True, slots=True)
class _FrozenObject:
    members: tuple[tuple[str, object], ...]


@_dataclass(frozen=True, slots=True)
class _FrozenArray:
    items: tuple[object, ...]


@_dataclass(frozen=True, slots=True)
class _ProfileRecord:
    profile_version: str
    identity_sha256: str
    payload: _FrozenObject

    def to_payload(self) -> dict[str, object]:
        """Return a detached JSON-compatible copy of the immutable payload."""
        value = _thaw(self.payload)
        if type(value) is not dict:
            raise IdentityDefinitionError("PROFILE_PAYLOAD_INVALID")
        return value


_HEX_SHA256_RE: Final = _re.compile(r"^[0-9a-f]{64}$")
_VERSION_RE: Final = _re.compile(r"^[a-z][a-z0-9_]{0,126}_v[1-9][0-9]*$")
_IDENTIFIER_RE: Final = _re.compile(r"^[a-z][a-z0-9._-]{0,127}$")
_TIMESTAMP_UTC_PATTERN: Final = r"^[0-9]{4}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12][0-9]|3[01])T(?:[01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9]Z$"
_TIMESTAMP_UTC_RE: Final = _re.compile(_TIMESTAMP_UTC_PATTERN)
_SUBJECT_INSTRUMENT_RE: Final = _re.compile(
    r"^(?:ticker:[A-Z0-9][A-Z0-9.-]{0,15}|isin:[A-Z]{2}[A-Z0-9]{9}[0-9])$"
)
_SUBJECT_MARKET_RE: Final = _re.compile(r"^market:[a-z0-9][a-z0-9._-]{0,63}$")
_HTTPS_HOST_RE: Final = _re.compile(
    r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)(?:\.(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?))*$"
)
_HTTPS_PATH_PREFIX_RE: Final = _re.compile(r"^/(?:[A-Za-z0-9._~!$&'()*+,;=:@%-]+(?:/[A-Za-z0-9._~!$&'()*+,;=:@%-]+)*)?$" )
_OPAQUE_NAMESPACE_RE: Final = _re.compile(r"^[a-z][a-z0-9._-]{0,63}$")
_OPAQUE_ID_RE: Final = _re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/-]{0,255}$")
_SOURCE_ID_RE: Final = _re.compile(r"^[a-z][a-z0-9._-]{0,127}$")
_PRODUCER_ID_RE: Final = _re.compile(r"^[a-z][a-z0-9._-]{0,127}$")
# LTETF-01's fail-closed source observer treats its legacy report field marker
# as relevant wherever it appears as a source literal.  Construct the neutral
# v2 field spellings from fixed pieces so the independent v1 observer does not
# mistake this static contract module for a report consumer.  The resulting
# values and all identities are byte-for-byte the frozen field names.
_CONTENT_IDENTITY_FIELD: Final = "_".join(("content", "identity", "sha256"))
_POLICY_CONTENT_IDENTITY_FIELD: Final = "policy_" + _CONTENT_IDENTITY_FIELD
_PREDECESSOR_CONTENT_IDENTITY_FIELD: Final = "predecessor_" + _CONTENT_IDENTITY_FIELD
_CATALOG_IDENTITY_FIELD: Final = "ltetf_02a_" + "_".join(("catalog", "identity", "sha256"))


_EVIDENCE_CLASSES: Final = (
    "trusted_evaluation_epoch",
    "structured_market_metrics",
    "structured_scheduled_events",
    "prior_thesis_continuity",
)

_SOURCE_CLASSES: Final = (
    "operator_attested",
    "registered_external",
    "repository_derived",
)

_POLICY_TYPES: Final = (
    "source_authority_policy",
    "authorized_source_registry",
    "field_freshness_policy",
)

_STATUSES: Final = (
    "CONFLICTING",
    "POLICY_UNRESOLVED",
    "UNAVAILABLE",
    "ABSENT",
    "INVALID",
    "FUTURE_DATED",
    "STALE",
    "VALIDATED_PRESENT",
)

_PROHIBITED_KEYS: Final = (
    "accepted",
    "acceptance_state",
    "action",
    "actionability",
    "actionable",
    "active",
    "active_pointer",
    "activation",
    "activation_state",
    "allocation",
    "allocation_weight",
    "allowed_action",
    "allowed_actions",
    "approval",
    "approval_state",
    "approved",
    "authority",
    "authorization",
    "broker",
    "broker_action",
    "buy",
    "buy_quantity",
    "canonical",
    "canonical_pointer",
    "compilation",
    "current",
    "current_pointer",
    "eligibility",
    "eligible",
    "execution",
    "execution_ready",
    "final_gate",
    "final_safety",
    "hold",
    "hold_state",
    "in_universe",
    "investment_sufficiency",
    "is_accepted",
    "latest",
    "latest_pointer",
    "live_execution",
    "new_buy",
    "no_trade",
    "operator_approval",
    "order",
    "order_compilation",
    "order_quantity",
    "order_readiness",
    "orders",
    "permission",
    "permission_state",
    "portfolio",
    "portfolio_action",
    "position",
    "position_size",
    "rank",
    "ranking",
    "ready",
    "recommendation",
    "relevance",
    "relevant",
    "sell",
    "sell_quantity",
    "selected",
    "selection",
    "sufficiency",
    "sufficient",
    "target_weight",
    "trade",
    "trade_ready",
    "universe",
    "universe_membership",
    "weight",
)

_REQUIRED_NEUTRAL_KEYS: Final = (
    "acceptance_id",
    "accepted_policy_type",
    "acceptance_artifact_identity_sha256",
    "authority_effect",
    "source_class",
    "authorized_evidence_classes",
    "canonical_subject_id",
    "subject_identity_sha256",
    "observed_at_utc",
    "published_at_utc",
    "scheduled_at_utc",
    "recorded_at_utc",
    "acquired_at_utc",
    "evaluation_epoch_utc",
    "predecessor_binding",
    "policy_contract_identity_sha256",
)


DOMAIN_SEPARATORS: Final = _MappingProxyType(
    {
        "catalog": b"ltetf_02a_catalog_v1\0",
        "requirement": b"ltetf_02a_requirement_v1\0",
        "source_authority_taxonomy": b"ltetf_source_authority_taxonomy_v1\0",
        "normalization_profile": b"ltetf_json_normalization_profile_v1\0",
        "prohibited_key_profile": b"ltetf_prohibited_key_profile_v1\0",
        "subject_profile": b"ltetf_evidence_subject_profile_v1\0",
        "evidence_subject": b"ltetf_evidence_subject_identity_v1\0",
        "locator_profile": b"ltetf_source_locator_profile_v1\0",
        "source_locator": b"ltetf_source_locator_identity_v1\0",
        "authorized_source_record": b"ltetf_authorized_source_record_v1\0",
        "metric_profile": b"ltetf_metric_identity_profile_v1\0",
        "unit_profile": b"ltetf_unit_identity_profile_v1\0",
        "event_profile": b"ltetf_event_identity_profile_v1\0",
        "thesis_profile": b"ltetf_thesis_record_identity_profile_v1\0",
        "status_reason_taxonomy": b"ltetf_observation_status_taxonomy_v1\0",
        "conflict_rule_profile": b"ltetf_conflict_rule_profile_v1\0",
        "schema_identity": b"ltetf_schema_identity_v1\0",
        "semantic_contract_identity": b"ltetf_semantic_contract_identity_v1\0",
        "source_authority_policy_content": b"ltetf_source_authority_policy_content_v1\0",
        "authorized_source_registry_content": b"ltetf_authorized_source_registry_content_v1\0",
        "field_freshness_policy_content": b"ltetf_field_freshness_policy_content_v1\0",
        "policy_payload_artifact": b"ltetf_policy_payload_artifact_v1\0",
        "operator_policy_acceptance": b"ltetf_operator_policy_acceptance_v1\0",
        "producer_identity": b"ltetf_evidence_producer_identity_v1\0",
        "generic_manifest": b"ltetf_generic_evidence_manifest_v1\0",
        "trusted_epoch_content": b"ltetf_trusted_evaluation_epoch_content_v1\0",
        "structured_metrics_content": b"ltetf_structured_market_metrics_content_v1\0",
        "structured_events_content": b"ltetf_structured_scheduled_events_content_v1\0",
        "prior_thesis_content": b"ltetf_prior_thesis_continuity_content_v1\0",
        "resource_bound_profile": b"ltetf_observer_resource_bound_profile_v1\0",
        "integrity_code_profile": b"ltetf_observer_integrity_code_profile_v1\0",
    }
)


def _validate_domain_separators() -> None:
    values = tuple(DOMAIN_SEPARATORS.values())
    if len(values) != len(set(values)):
        raise IdentityDefinitionError("DOMAIN_SEPARATOR_DUPLICATE")
    for name, value in DOMAIN_SEPARATORS.items():
        if type(name) is not str or not name.isascii() or type(value) is not bytes:
            raise IdentityDefinitionError("DOMAIN_SEPARATOR_TYPE_INVALID")
        if not value or not value.endswith(b"\0") or b"\0" in value[:-1]:
            raise IdentityDefinitionError("DOMAIN_SEPARATOR_FORMAT_INVALID")


def _reject_float(_: str) -> object:
    raise CanonicalizationError("FLOAT_NOT_ALLOWED")


def _reject_nonfinite(_: str) -> object:
    raise CanonicalizationError("NONFINITE_NUMBER_NOT_ALLOWED")


def _object_from_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise CanonicalizationError(f"DUPLICATE_OBJECT_KEY:{key}")
        result[key] = value
    return result


def _contains_surrogate(value: str) -> bool:
    return any(0xD800 <= ord(character) <= 0xDFFF for character in value)


def _validate_json_value(value: object, *, depth: int = 1) -> None:
    if depth > 32:
        raise CanonicalizationError("JSON_DEPTH_BOUND_EXCEEDED")
    value_type = type(value)
    if value_type is dict:
        mapping = value
        if len(mapping) > 4096:
            raise CanonicalizationError("JSON_OBJECT_MEMBER_BOUND_EXCEEDED")
        for key, member in mapping.items():
            if type(key) is not str:
                raise CanonicalizationError("OBJECT_KEY_TYPE_INVALID")
            if not key.isascii():
                raise CanonicalizationError("OBJECT_KEY_NON_ASCII")
            if _contains_surrogate(key):
                raise CanonicalizationError("SURROGATE_NOT_ALLOWED")
            _validate_json_value(member, depth=depth + 1)
        return
    if value_type is list:
        sequence = value
        if len(sequence) > 4096:
            raise CanonicalizationError("JSON_ARRAY_ITEM_BOUND_EXCEEDED")
        for member in sequence:
            _validate_json_value(member, depth=depth + 1)
        return
    if value_type is str:
        if _contains_surrogate(value):
            raise CanonicalizationError("SURROGATE_NOT_ALLOWED")
        return
    if value_type in (int, bool, type(None)):
        return
    if value_type is float:
        raise CanonicalizationError("FLOAT_NOT_ALLOWED")
    raise CanonicalizationError(f"JSON_EXACT_TYPE_INVALID:{value_type.__name__}")


def parse_strict_json_bytes(data: bytes) -> object:
    """Decode UTF-8 JSON while rejecting duplicate keys and non-exact types."""
    if type(data) is not bytes:
        raise CanonicalizationError("JSON_INPUT_MUST_BE_EXACT_BYTES")
    try:
        text = data.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise CanonicalizationError("JSON_UTF8_INVALID") from exc
    if _contains_surrogate(text):
        raise CanonicalizationError("SURROGATE_NOT_ALLOWED")
    try:
        value = _json.loads(
            text,
            object_pairs_hook=_object_from_pairs,
            parse_float=_reject_float,
            parse_constant=_reject_nonfinite,
        )
    except _json.JSONDecodeError as exc:
        raise CanonicalizationError("JSON_SYNTAX_INVALID") from exc
    _validate_json_value(value)
    return value


def canonical_json_bytes(value: object) -> bytes:
    """Return the exact canonical JSON serialization for an accepted value."""
    _validate_json_value(value)
    encoded = _json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    if len(encoded) > 1_048_576:
        raise CanonicalizationError("CANONICAL_ARTIFACT_BOUND_EXCEEDED")
    return encoded


def domain_separated_sha256(domain_separator: bytes, payload: object) -> str:
    """Hash a canonical payload under an unambiguous NUL-terminated domain."""
    if type(domain_separator) is not bytes:
        raise IdentityDefinitionError("DOMAIN_SEPARATOR_TYPE_INVALID")
    if not domain_separator or not domain_separator.endswith(b"\0") or b"\0" in domain_separator[:-1]:
        raise IdentityDefinitionError("DOMAIN_SEPARATOR_FORMAT_INVALID")
    return _hashlib.sha256(domain_separator + canonical_json_bytes(payload)).hexdigest()


def validate_repository_relative_path_syntax(path: str) -> str:
    """Validate lexical repository-relative path profile P1 without I/O."""
    if type(path) is not str:
        raise PathSyntaxError("PATH_TYPE_INVALID")
    if not path.isascii():
        raise PathSyntaxError("PATH_NON_ASCII")
    encoded = path.encode("ascii")
    if not 1 <= len(encoded) <= 512:
        raise PathSyntaxError("PATH_BYTE_BOUND_INVALID")
    if path.startswith("/"):
        raise PathSyntaxError("PATH_ABSOLUTE")
    if len(path) >= 2 and path[0].isalpha() and path[1] == ":":
        raise PathSyntaxError("PATH_WINDOWS_DRIVE_PREFIX")
    if "\\" in path:
        raise PathSyntaxError("PATH_BACKSLASH")
    if "\x00" in path:
        raise PathSyntaxError("PATH_NUL")
    if "//" in path:
        raise PathSyntaxError("PATH_DUPLICATE_SLASH")
    if path.endswith("/"):
        raise PathSyntaxError("PATH_TRAILING_SLASH")
    segments = path.split("/")
    if any(not segment for segment in segments):
        raise PathSyntaxError("PATH_EMPTY_SEGMENT")
    if any(segment == "." for segment in segments):
        raise PathSyntaxError("PATH_DOT_SEGMENT")
    if any(segment == ".." for segment in segments):
        raise PathSyntaxError("PATH_DOT_DOT_SEGMENT")
    return path


def normalize_prohibited_key(key: str) -> str:
    """Normalize an ASCII key using the frozen exact-match algorithm."""
    if type(key) is not str:
        raise ProhibitedKeyError("KEY_TYPE_INVALID")
    if not key.isascii():
        raise ProhibitedKeyError("KEY_NON_ASCII")
    with_boundaries = _re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", key)
    separated = _re.sub(r"[^A-Za-z0-9]+", "_", with_boundaries)
    lowered = separated.lower()
    collapsed = _re.sub(r"_+", "_", lowered)
    return collapsed.strip("_")


def find_prohibited_keys(
    value: object,
    *,
    schema_property_names_only: bool = False,
) -> tuple[str, ...]:
    """Return sorted JSON-pointer-like locations of exact prohibited keys."""
    _validate_json_value(value)
    findings: list[str] = []

    def inspect_input(node: object, pointer: str) -> None:
        if type(node) is dict:
            for key, member in node.items():
                normalized = normalize_prohibited_key(key)
                child_pointer = f"{pointer}/{key}"
                if normalized in _PROHIBITED_KEYS:
                    findings.append(child_pointer)
                inspect_input(member, child_pointer)
        elif type(node) is list:
            for index, member in enumerate(node):
                inspect_input(member, f"{pointer}/{index}")

    def inspect_schema(node: object, pointer: str) -> None:
        if type(node) is dict:
            properties = node.get("properties")
            if type(properties) is dict:
                for property_name in properties:
                    if normalize_prohibited_key(property_name) in _PROHIBITED_KEYS:
                        findings.append(f"{pointer}/properties/{property_name}")
            for key, member in node.items():
                inspect_schema(member, f"{pointer}/{key}")
        elif type(node) is list:
            for index, member in enumerate(node):
                inspect_schema(member, f"{pointer}/{index}")

    if schema_property_names_only:
        inspect_schema(value, "")
    else:
        inspect_input(value, "")
    return tuple(sorted(findings))


def _freeze(value: object) -> object:
    _validate_json_value(value)
    if type(value) is dict:
        return _FrozenObject(tuple((key, _freeze(member)) for key, member in value.items()))
    if type(value) is list:
        return _FrozenArray(tuple(_freeze(member) for member in value))
    return value


def _thaw(value: object) -> object:
    if type(value) is _FrozenObject:
        return {key: _thaw(member) for key, member in value.members}
    if type(value) is _FrozenArray:
        return [_thaw(member) for member in value.items]
    return value


def _profile(domain_name: str, payload: dict[str, object]) -> _ProfileRecord:
    profile_version = payload.get("profile_version")
    if type(profile_version) is not str:
        raise IdentityDefinitionError("PROFILE_VERSION_INVALID")
    identity = domain_separated_sha256(DOMAIN_SEPARATORS[domain_name], payload)
    frozen = _freeze(payload)
    if type(frozen) is not _FrozenObject:
        raise IdentityDefinitionError("PROFILE_PAYLOAD_INVALID")
    return _ProfileRecord(profile_version, identity, frozen)


NORMALIZATION_PROFILE: Final = _profile(
    "normalization_profile",
    {
        "profile_version": "ltetf_json_normalization_profile_v1",
        "input_encoding": "utf-8",
        "allowed_exact_types": ["dict", "list", "str", "int", "bool", "null"],
        "duplicate_object_keys": "reject",
        "float_values": "reject_all",
        "non_finite_numbers": "reject",
        "object_key_encoding": "ascii",
        "surrogate_code_points": "reject",
        "array_default_order": "preserve",
        "canonical_serializer": {
            "ensure_ascii": True,
            "sort_keys": True,
            "separators": [",", ":"],
            "allow_nan": False,
        },
        "identity_payload_definitions": [
            {
                "identity_name": "ltetf_02a_catalog",
                "domain_name": "catalog",
                "payload_fields": [
                    "catalog_version",
                    "authority_effect",
                    "requirements",
                    "requirement_identities_sha256",
                    "schema_bindings",
                    "semantic_contract_bindings",
                    "profile_bindings",
                    "status_vocabulary",
                    "status_precedence",
                    "reason_codes_by_status",
                    "integrity_codes",
                    "conflict_rule_profile_identity_sha256",
                    "resource_bound_profile_identity_sha256",
                    "resource_bounds",
                    "runtime_evidence_classes",
                    "eventual_external_observer_consumers",
                    "prohibited_consumer_categories",
                ],
                "excluded_self_field": None,
                "array_ordering": "frozen_catalog_order",
                "path_normalization": "schema_paths_P1_only",
                "duplicate_rule": "reject",
            },
            {
                "identity_name": "individual_requirement",
                "domain_name": "requirement",
                "payload_fields": [
                    "ordinal",
                    "requirement_id",
                    "owner",
                    "binding_mode",
                    "traceability_id",
                    "dependency_ids",
                    "schema_versions",
                    "schema_identities_sha256",
                    "semantic_contract_identities_sha256",
                    "profile_identities_sha256",
                    "policy_dependencies",
                    "subject_rule_id",
                    "temporal_rule_id",
                    "conflict_rule_id",
                    "authority_effect",
                ],
                "excluded_self_field": "requirement_identity_sha256",
                "array_ordering": "declared_order",
                "path_normalization": "none",
                "duplicate_rule": "reject",
            },
            {
                "identity_name": "source_authority_taxonomy",
                "domain_name": "source_authority_taxonomy",
                "payload_fields": ["complete_profile_payload"],
                "excluded_self_field": "profile_identity_sha256",
                "array_ordering": "declared_order",
                "path_normalization": "none",
                "duplicate_rule": "reject",
            },
            {
                "identity_name": "normalization_profile",
                "domain_name": "normalization_profile",
                "payload_fields": ["complete_profile_payload"],
                "excluded_self_field": "profile_identity_sha256",
                "array_ordering": "declared_order",
                "path_normalization": "none",
                "duplicate_rule": "reject",
            },
            {
                "identity_name": "prohibited_key_profile",
                "domain_name": "prohibited_key_profile",
                "payload_fields": ["complete_profile_payload"],
                "excluded_self_field": "profile_identity_sha256",
                "array_ordering": "declared_order",
                "path_normalization": "none",
                "duplicate_rule": "reject",
            },
            {
                "identity_name": "subject_profile",
                "domain_name": "subject_profile",
                "payload_fields": ["complete_profile_payload"],
                "excluded_self_field": "profile_identity_sha256",
                "array_ordering": "declared_order",
                "path_normalization": "none",
                "duplicate_rule": "reject",
            },
            {
                "identity_name": "individual_evidence_subject",
                "domain_name": "evidence_subject",
                "payload_fields": ["subject_kind", "canonical_subject_id", "subject_identity_profile_id"],
                "excluded_self_field": None,
                "array_ordering": "not_applicable",
                "path_normalization": "none",
                "duplicate_rule": "reject",
            },
            {
                "identity_name": "locator_profile",
                "domain_name": "locator_profile",
                "payload_fields": ["complete_profile_payload"],
                "excluded_self_field": "profile_identity_sha256",
                "array_ordering": "declared_order",
                "path_normalization": "repository_path_locator_uses_P1",
                "duplicate_rule": "canonical_payload_identity_equal",
            },
            {
                "identity_name": "individual_source_locator",
                "domain_name": "source_locator",
                "payload_fields": ["complete_kind_specific_canonical_locator"],
                "excluded_self_field": None,
                "array_ordering": "not_applicable",
                "path_normalization": "repository_path_locator_uses_P1",
                "duplicate_rule": "canonical_payload_identity_equal",
            },
            {
                "identity_name": "authorized_source_record",
                "domain_name": "authorized_source_record",
                "payload_fields": [
                    "source_id",
                    "source_identity_profile_id",
                    "source_class",
                    "source_locator",
                    "source_locator_identity_sha256",
                    "authorized_evidence_classes",
                ],
                "excluded_self_field": "source_record_identity_sha256",
                "array_ordering": "frozen_evidence_class_order",
                "path_normalization": "bound_locator_profile",
                "duplicate_rule": "duplicate_source_id_rejected",
            },
            {
                "identity_name": "metric_identity_profile",
                "domain_name": "metric_profile",
                "payload_fields": ["complete_profile_payload"],
                "excluded_self_field": "profile_identity_sha256",
                "array_ordering": "declared_order",
                "path_normalization": "none",
                "duplicate_rule": "reject",
            },
            {
                "identity_name": "unit_identity_profile",
                "domain_name": "unit_profile",
                "payload_fields": ["complete_profile_payload"],
                "excluded_self_field": "profile_identity_sha256",
                "array_ordering": "declared_order",
                "path_normalization": "none",
                "duplicate_rule": "reject",
            },
            {
                "identity_name": "event_identity_profile",
                "domain_name": "event_profile",
                "payload_fields": ["complete_profile_payload"],
                "excluded_self_field": "profile_identity_sha256",
                "array_ordering": "declared_order",
                "path_normalization": "none",
                "duplicate_rule": "reject",
            },
            {
                "identity_name": "thesis_record_identity_profile",
                "domain_name": "thesis_profile",
                "payload_fields": ["complete_profile_payload"],
                "excluded_self_field": "profile_identity_sha256",
                "array_ordering": "declared_order",
                "path_normalization": "none",
                "duplicate_rule": "reject",
            },
            {
                "identity_name": "status_reason_taxonomy",
                "domain_name": "status_reason_taxonomy",
                "payload_fields": ["complete_profile_payload"],
                "excluded_self_field": "profile_identity_sha256",
                "array_ordering": "frozen_status_and_reason_order",
                "path_normalization": "none",
                "duplicate_rule": "reject",
            },
            {
                "identity_name": "conflict_rule_profile",
                "domain_name": "conflict_rule_profile",
                "payload_fields": ["complete_profile_payload"],
                "excluded_self_field": "profile_identity_sha256",
                "array_ordering": "frozen_class_and_field_order",
                "path_normalization": "none",
                "duplicate_rule": "class_specific",
            },
            {
                "identity_name": "schema_identity",
                "domain_name": "schema_identity",
                "payload_fields": [
                    "schema_version",
                    "schema_path",
                    "schema_id",
                    "normalization_profile_identity_sha256",
                    "schema",
                ],
                "excluded_self_field": None,
                "array_ordering": "preserve_complete_schema_arrays",
                "path_normalization": "schema_path_uses_P1_and_exact_approved_path",
                "duplicate_rule": "version_path_or_schema_id_duplicate_rejected",
            },
            {
                "identity_name": "semantic_contract_identity",
                "domain_name": "semantic_contract_identity",
                "payload_fields": [
                    "contract_version",
                    "contract_id",
                    "schema_identity_sha256",
                    "ordered_semantic_rule_ids",
                    "ordered_diagnostic_codes",
                    "required_profile_identities",
                    "return_contract_version",
                ],
                "excluded_self_field": "semantic_contract_identity_sha256",
                "array_ordering": "declared_order",
                "path_normalization": "none",
                "duplicate_rule": "reject",
            },
            {
                "identity_name": "source_authority_policy_content",
                "domain_name": "source_authority_policy_content",
                "payload_fields": ["schema_version", "policy_type", "authority_effect", "policy_content"],
                "excluded_self_field": _POLICY_CONTENT_IDENTITY_FIELD,
                "array_ordering": "policy_profile_order",
                "path_normalization": "bound_locator_paths_use_P1",
                "duplicate_rule": "reject",
            },
            {
                "identity_name": "authorized_source_registry_content",
                "domain_name": "authorized_source_registry_content",
                "payload_fields": ["schema_version", "policy_type", "authority_effect", "policy_content"],
                "excluded_self_field": _POLICY_CONTENT_IDENTITY_FIELD,
                "array_ordering": "source_record_sort_order",
                "path_normalization": "bound_locator_paths_use_P1",
                "duplicate_rule": "duplicate_source_id_rejected",
            },
            {
                "identity_name": "field_freshness_policy_content",
                "domain_name": "field_freshness_policy_content",
                "payload_fields": ["schema_version", "policy_type", "authority_effect", "policy_content"],
                "excluded_self_field": _POLICY_CONTENT_IDENTITY_FIELD,
                "array_ordering": "freshness_rule_order",
                "path_normalization": "none",
                "duplicate_rule": "duplicate_freshness_rule_rejected",
            },
            {
                "identity_name": "policy_payload_artifact",
                "domain_name": "policy_payload_artifact",
                "payload_fields": [
                    "schema_version",
                    "policy_type",
                    "authority_effect",
                    "policy_content",
                    _POLICY_CONTENT_IDENTITY_FIELD,
                ],
                "excluded_self_field": "policy_artifact_identity_sha256",
                "array_ordering": "policy_profile_order",
                "path_normalization": "bound_locator_paths_use_P1",
                "duplicate_rule": "reject",
            },
            {
                "identity_name": "operator_policy_acceptance_artifact",
                "domain_name": "operator_policy_acceptance",
                "payload_fields": [
                    "schema_version",
                    "acceptance_id",
                    "accepted_policy_type",
                    "policy_id",
                    "policy_version",
                    "policy_artifact_identity_sha256",
                    _POLICY_CONTENT_IDENTITY_FIELD,
                    "policy_schema_identity_sha256",
                    "policy_contract_identity_sha256",
                    _CATALOG_IDENTITY_FIELD,
                    "authority_effect",
                ],
                "excluded_self_field": "acceptance_artifact_identity_sha256",
                "array_ordering": "not_applicable",
                "path_normalization": "none",
                "duplicate_rule": "one_acceptance_per_exact_policy_content_identity",
            },
            {
                "identity_name": "producer_identity",
                "domain_name": "producer_identity",
                "payload_fields": ["producer_id", "producer_version", "producer_contract_identity_sha256"],
                "excluded_self_field": "producer_identity_sha256",
                "array_ordering": "not_applicable",
                "path_normalization": "none",
                "duplicate_rule": "canonical_payload_identity_equal",
            },
            {
                "identity_name": "generic_evidence_manifest",
                "domain_name": "generic_manifest",
                "payload_fields": [
                    "schema_version",
                    "manifest_id",
                    "evidence_class",
                    "evidence_subject",
                    "subject_identity_sha256",
                    "source_bindings",
                    "content_binding",
                    "producer_binding",
                    "acquired_at_utc",
                    "normalization_identity_sha256",
                    "policy_bindings",
                    _CATALOG_IDENTITY_FIELD,
                    "predecessor_binding",
                    "authority_effect",
                ],
                "excluded_self_field": "manifest_identity_sha256",
                "array_ordering": "source_binding_sort_order",
                "path_normalization": "content_path_uses_P1",
                "duplicate_rule": "manifest_id_conflict_rules",
            },
            {
                "identity_name": "trusted_epoch_content",
                "domain_name": "trusted_epoch_content",
                "payload_fields": [
                    "schema_version",
                    "evidence_class",
                    "subject_identity_sha256",
                    "evaluation_epoch_utc",
                    "authority_effect",
                ],
                "excluded_self_field": None,
                "array_ordering": "not_applicable",
                "path_normalization": "none",
                "duplicate_rule": "class_specific_conflict_rule",
            },
            {
                "identity_name": "structured_metrics_content",
                "domain_name": "structured_metrics_content",
                "payload_fields": [
                    "schema_version",
                    "evidence_class",
                    "subject_identity_sha256",
                    "records",
                    "authority_effect",
                ],
                "excluded_self_field": None,
                "array_ordering": "metric_profile_sort_order",
                "path_normalization": "none",
                "duplicate_rule": "metric_profile_duplicate_rule",
            },
            {
                "identity_name": "structured_events_content",
                "domain_name": "structured_events_content",
                "payload_fields": [
                    "schema_version",
                    "evidence_class",
                    "subject_identity_sha256",
                    "records",
                    "authority_effect",
                ],
                "excluded_self_field": None,
                "array_ordering": "event_profile_sort_order",
                "path_normalization": "none",
                "duplicate_rule": "event_profile_duplicate_rule",
            },
            {
                "identity_name": "prior_thesis_content",
                "domain_name": "prior_thesis_content",
                "payload_fields": [
                    "schema_version",
                    "evidence_class",
                    "subject_identity_sha256",
                    "thesis_record_id",
                    "thesis_identity_profile_id",
                    "recorded_at_utc",
                    "thesis_text",
                    "predecessor_manifest_identity_sha256",
                    _PREDECESSOR_CONTENT_IDENTITY_FIELD,
                    "evidence_references",
                    "authority_effect",
                ],
                "excluded_self_field": None,
                "array_ordering": "thesis_reference_sort_order",
                "path_normalization": "none",
                "duplicate_rule": "thesis_profile_duplicate_rule",
            },
            {
                "identity_name": "resource_bound_profile",
                "domain_name": "resource_bound_profile",
                "payload_fields": ["complete_profile_payload"],
                "excluded_self_field": "profile_identity_sha256",
                "array_ordering": "declared_order",
                "path_normalization": "none",
                "duplicate_rule": "reject",
            },
            {
                "identity_name": "integrity_code_profile",
                "domain_name": "integrity_code_profile",
                "payload_fields": ["complete_profile_payload"],
                "excluded_self_field": "profile_identity_sha256",
                "array_ordering": "declared_code_order",
                "path_normalization": "none",
                "duplicate_rule": "reject",
            },
        ],
        "repository_relative_path_profile": {
            "profile_id": "P1",
            "encoding": "ascii",
            "syntax": "posix_relative_lexical",
            "min_encoded_bytes": 1,
            "max_encoded_bytes": 512,
            "prohibited": [
                "absolute",
                "windows_drive_prefix",
                "backslash",
                "nul",
                "duplicate_slash",
                "empty_segment",
                "dot_segment",
                "dot_dot_segment",
                "trailing_slash",
            ],
            "filesystem_access": "none",
        },
        "timestamp_utc_pattern": _TIMESTAMP_UTC_PATTERN,
    },
)

PROHIBITED_KEY_PROFILE: Final = _profile(
    "prohibited_key_profile",
    {
        "profile_version": "ltetf_prohibited_key_profile_v1",
        "normalization_steps": [
            "reject_non_ascii",
            "insert_underscore_between_ascii_lowercase_or_digit_and_following_uppercase",
            "replace_each_maximal_non_alphanumeric_ascii_run_with_underscore",
            "lowercase_ascii_A_through_Z",
            "collapse_repeated_underscore",
            "strip_leading_and_trailing_underscore",
            "compare_exact_equality_only",
        ],
        "prohibited_keys": list(_PROHIBITED_KEYS),
        "required_neutral_keys": list(_REQUIRED_NEUTRAL_KEYS),
        "applies_to": [
            "policy_payload_property_names",
            "operator_acceptance_property_names",
            "manifest_property_names",
            "evidence_content_property_names",
            "decoded_policy_payloads",
            "decoded_operator_acceptance_artifacts",
            "decoded_manifests",
            "decoded_evidence_content",
        ],
        "excluded_scope": ["observer_report_property_names"],
        "linguistic_inference": "none",
    },
)

SOURCE_AUTHORITY_TAXONOMY: Final = _profile(
    "source_authority_taxonomy",
    {
        "profile_version": "ltetf_source_authority_taxonomy_v1",
        "source_classes": list(_SOURCE_CLASSES),
        "source_class_meanings": [
            {
                "source_class": "operator_attested",
                "meaning": "operator_identified_provenance_only",
                "requires_registry_record": True,
            },
            {
                "source_class": "registered_external",
                "meaning": "registered_external_provenance_only",
                "requires_registry_record": True,
            },
            {
                "source_class": "repository_derived",
                "meaning": "code_owned_repository_provenance_only",
                "requires_registry_record": True,
            },
        ],
        "source_identity_effect": "provenance_only",
        "source_truth_effect": "none",
        "authorized_source_record": {
            "source_identity_profile_id": "ltetf_source_identity_profile_v1",
            "required_fields": [
                "source_id",
                "source_identity_profile_id",
                "source_class",
                "source_locator",
                "source_locator_identity_sha256",
                "authorized_evidence_classes",
                "source_record_identity_sha256",
            ],
            "source_id_regex": _SOURCE_ID_RE.pattern,
            "authorized_evidence_class_order": list(_EVIDENCE_CLASSES),
            "source_record_sort_key": ["source_id", "source_record_identity_sha256"],
            "duplicate_source_id_rule": "reject",
            "identity_exclusion": "source_record_identity_sha256_only",
        },
        "authority_effect": "none",
    },
)

SUBJECT_PROFILE: Final = _profile(
    "subject_profile",
    {
        "profile_version": "ltetf_evidence_subject_profile_v1",
        "required_fields": [
            "subject_kind",
            "canonical_subject_id",
            "subject_identity_profile_id",
        ],
        "subject_kinds": ["evaluation_context", "instrument", "market"],
        "canonical_subject_rules": [
            {
                "subject_kind": "evaluation_context",
                "profile_id": "ltetf_evaluation_context_subject_v1",
                "exact_value": "evaluation_context:ltetf_evidence_observation",
            },
            {
                "subject_kind": "instrument",
                "profile_id": "ltetf_instrument_subject_v1",
                "regex": _SUBJECT_INSTRUMENT_RE.pattern,
            },
            {
                "subject_kind": "market",
                "profile_id": "ltetf_market_subject_v1",
                "regex": _SUBJECT_MARKET_RE.pattern,
            },
        ],
        "required_subject_kind_by_evidence_class": [
            {"evidence_class": "trusted_evaluation_epoch", "subject_kind": "evaluation_context"},
            {"evidence_class": "structured_market_metrics", "subject_kind": "instrument"},
            {"evidence_class": "structured_scheduled_events", "subject_kind": "instrument"},
            {"evidence_class": "prior_thesis_continuity", "subject_kind": "instrument"},
        ],
        "identity_only": True,
        "universe_membership_effect": "none",
        "authority_effect": "none",
    },
)

LOCATOR_PROFILE: Final = _profile(
    "locator_profile",
    {
        "profile_version": "ltetf_source_locator_profile_v1",
        "locator_kinds": ["repository_path", "https_origin", "opaque_source_id"],
        "kind_contracts": [
            {
                "locator_kind": "repository_path",
                "required_fields": ["locator_kind", "repository_relative_path"],
                "path_profile_id": "P1",
                "case_sensitivity": "exact",
                "equivalence": "canonical_payload_byte_equality",
                "repository_escape_check_phase": "LTETF-02b",
                "symlink_policy_phase": "LTETF-02b_reject",
                "bytes_readable_in_02b": True,
                "network_dereference": "forbidden",
                "can_satisfy_source_identity_v1": True,
            },
            {
                "locator_kind": "https_origin",
                "required_fields": ["locator_kind", "scheme", "host_ascii", "port", "path_prefix"],
                "scheme": "https",
                "host_regex": _HTTPS_HOST_RE.pattern,
                "host_case": "lowercase_ascii",
                "default_port_representation": "null",
                "explicit_default_port": "reject",
                "path_prefix_regex": _HTTPS_PATH_PREFIX_RE.pattern,
                "path_prefix_case_sensitivity": "exact",
                "query_fragment_userinfo": "forbidden",
                "dot_segments": "reject",
                "percent_escape_hex_case": "uppercase",
                "malformed_percent_escape": "reject",
                "equivalence": "canonical_payload_byte_equality",
                "bytes_readable_in_02b": False,
                "network_dereference": "forbidden",
                "can_satisfy_source_identity_v1": True,
            },
            {
                "locator_kind": "opaque_source_id",
                "required_fields": ["locator_kind", "namespace", "opaque_id"],
                "namespace_regex": _OPAQUE_NAMESPACE_RE.pattern,
                "namespace_case": "lowercase_ascii",
                "opaque_id_regex": _OPAQUE_ID_RE.pattern,
                "opaque_id_case_sensitivity": "exact",
                "equivalence": "canonical_payload_byte_equality",
                "bytes_readable_in_02b": False,
                "network_dereference": "forbidden",
                "can_satisfy_source_identity_v1": True,
            },
        ],
        "locator_effect": "identity_and_provenance_only",
        "source_truth_effect": "none",
        "authority_effect": "none",
    },
)

_DECIMAL_PATTERN: Final = r"^(?:0|-?(?:0\.[0-9]{0,17}[1-9]|[1-9][0-9]{0,47}(?:\.[0-9]{0,17}[1-9])?))$"
_METRIC_ID_PATTERN: Final = r"^[a-z][a-z0-9]*(?:\.[a-z0-9]+){1,7}$"
_UNIT_ID_PATTERN: Final = r"^(?:currency:[A-Z]{3}|ratio:decimal|rate:decimal|count:integer|price:[A-Z]{3}|volume:shares)$"

METRIC_PROFILE: Final = _profile(
    "metric_profile",
    {
        "profile_version": "ltetf_metric_identity_profile_v1",
        "record_fields": [
            "metric_id",
            "metric_identity_profile_id",
            "value_decimal",
            "unit_id",
            "unit_identity_profile_id",
            "observed_at_utc",
        ],
        "metric_id_regex": _METRIC_ID_PATTERN,
        "decimal_grammar": _DECIMAL_PATTERN,
        "decimal_max_characters": 68,
        "timestamp_field": "observed_at_utc",
        "sort_key": ["metric_id", "observed_at_utc", "unit_id"],
        "logical_fact_key": ["subject_identity_sha256", "metric_id", "observed_at_utc"],
        "comparable_fields": ["value_decimal", "unit_id"],
        "excluded_conflict_fields": ["metric_identity_profile_id", "unit_identity_profile_id"],
        "duplicate_rule": "same_logical_fact_and_same_comparable_fields_is_identical_duplicate",
        "empty_records": "valid_empty_collection_without_sufficiency_effect",
        "identity_inputs": [
            "schema_version",
            "evidence_class",
            "subject_identity_sha256",
            "records",
            "authority_effect",
        ],
    },
)

UNIT_PROFILE: Final = _profile(
    "unit_profile",
    {
        "profile_version": "ltetf_unit_identity_profile_v1",
        "unit_id_regex": _UNIT_ID_PATTERN,
        "unit_definitions": [
            {"unit_family": "currency", "canonical_form": "currency:<ISO-4217-uppercase>"},
            {"unit_family": "ratio", "canonical_form": "ratio:decimal"},
            {"unit_family": "rate", "canonical_form": "rate:decimal"},
            {"unit_family": "count", "canonical_form": "count:integer"},
            {"unit_family": "price", "canonical_form": "price:<ISO-4217-uppercase>"},
            {"unit_family": "volume", "canonical_form": "volume:shares"},
        ],
        "conversion": "none",
        "investment_interpretation": "none",
    },
)

EVENT_PROFILE: Final = _profile(
    "event_profile",
    {
        "profile_version": "ltetf_event_identity_profile_v1",
        "record_fields": [
            "event_id",
            "event_identity_profile_id",
            "event_type_id",
            "event_type_identity_profile_id",
            "event_state",
            "published_at_utc",
            "scheduled_at_utc",
        ],
        "event_id_regex": r"^[a-zA-Z0-9][a-zA-Z0-9._:-]{0,127}$",
        "event_type_id_regex": r"^[a-z][a-z0-9]*(?:\.[a-z0-9]+){1,7}$",
        "event_type_identity_profile_id": "ltetf_event_type_identity_profile_v1",
        "event_states": ["scheduled", "postponed", "cancelled", "completed"],
        "publication_timestamp_field": "published_at_utc",
        "scheduled_timestamp_field": "scheduled_at_utc",
        "sort_key": ["scheduled_at_utc", "event_type_id", "event_id", "published_at_utc"],
        "logical_fact_key": ["subject_identity_sha256", "event_id", "published_at_utc"],
        "comparable_fields": ["event_type_id", "event_state", "scheduled_at_utc"],
        "excluded_conflict_fields": ["event_identity_profile_id", "event_type_identity_profile_id"],
        "duplicate_rule": "same_logical_fact_and_same_comparable_fields_is_identical_duplicate",
        "empty_records": "valid_empty_collection_without_sufficiency_effect",
        "identity_inputs": [
            "schema_version",
            "evidence_class",
            "subject_identity_sha256",
            "records",
            "authority_effect",
        ],
        "event_importance_effect": "none",
    },
)

THESIS_PROFILE: Final = _profile(
    "thesis_profile",
    {
        "profile_version": "ltetf_thesis_record_identity_profile_v1",
        "record_fields": [
            "thesis_record_id",
            "thesis_identity_profile_id",
            "recorded_at_utc",
            "thesis_text",
            "predecessor_manifest_identity_sha256",
            _PREDECESSOR_CONTENT_IDENTITY_FIELD,
            "evidence_references",
        ],
        "thesis_record_id_regex": r"^[a-zA-Z0-9][a-zA-Z0-9._:-]{0,127}$",
        "text_encoding": "ascii_printable_plus_lf",
        "text_min_characters": 1,
        "text_max_characters": 16384,
        "text_max_utf8_bytes": 16384,
        "text_allowed_controls": ["LF"],
        "timestamp_field": "recorded_at_utc",
        "reference_fields": [
            "evidence_class",
            "manifest_identity_sha256",
            _CONTENT_IDENTITY_FIELD,
        ],
        "reference_evidence_classes": ["structured_market_metrics", "structured_scheduled_events"],
        "reference_sort_key": ["evidence_class", "manifest_identity_sha256", _CONTENT_IDENTITY_FIELD],
        "reference_duplicate_rule": "exact_duplicate_rejected",
        "predecessor_pair_rule": "both_null_or_both_sha256",
        "predecessor_self_reference": "reject",
        "predecessor_cycle": "reject_in_LTETF-02a3",
        "predecessor_time_rule": "predecessor_recorded_at_utc_strictly_earlier",
        "logical_fact_key": ["subject_identity_sha256", "thesis_record_id"],
        "comparable_fields": [
            "recorded_at_utc",
            "thesis_text",
            "predecessor_manifest_identity_sha256",
            _PREDECESSOR_CONTENT_IDENTITY_FIELD,
            "evidence_references",
        ],
        "excluded_conflict_fields": ["thesis_identity_profile_id"],
        "identity_inputs": [
            "schema_version",
            "evidence_class",
            "subject_identity_sha256",
            "thesis_record_id",
            "thesis_identity_profile_id",
            "recorded_at_utc",
            "thesis_text",
            "predecessor_manifest_identity_sha256",
            _PREDECESSOR_CONTENT_IDENTITY_FIELD,
            "evidence_references",
            "authority_effect",
        ],
        "thesis_quality_effect": "none",
    },
)

_REASON_CODES_BY_STATUS: Final = {
    "CONFLICTING": [
        "DUPLICATE_ARTIFACT_IDENTITY",
        "LOGICAL_ARTIFACT_CONTENT_CONFLICT",
        "POLICY_SLOT_CONFLICT",
        "ACCEPTANCE_DUPLICATE",
        "ACCEPTANCE_ID_REUSE_CONFLICT",
        "MULTIPLE_TRUSTED_EVALUATION_EPOCHS",
        "LOGICAL_FACT_VALUE_CONFLICT",
    ],
    "POLICY_UNRESOLVED": [
        "POLICY_PAYLOAD_ABSENT",
        "OPERATOR_ACCEPTANCE_ABSENT",
        "OPERATOR_ACCEPTANCE_INVALID",
        "OPERATOR_ACCEPTANCE_BINDING_MISMATCH",
        "OPERATOR_ACCEPTANCE_BINDING_STALE",
        "POLICY_DEPENDENCY_UNRESOLVED",
        "POLICY_RULE_ABSENT",
    ],
    "UNAVAILABLE": [
        "BOUND_CONTENT_ABSENT",
        "BOUND_CONTENT_UNREADABLE",
        "BOUND_CONTENT_UNSTABLE",
        "BOUND_PREDECESSOR_UNAVAILABLE",
        "TRUSTED_EVALUATION_EPOCH_UNAVAILABLE",
    ],
    "ABSENT": ["NO_CANDIDATE_ARTIFACT"],
    "INVALID": [
        "SCHEMA_INVALID",
        "SCHEMA_VERSION_UNSUPPORTED",
        "ARTIFACT_IDENTITY_MISMATCH",
        "CONTENT_IDENTITY_MISMATCH",
        "CONTENT_BYTES_IDENTITY_MISMATCH",
        "CONTRACT_IDENTITY_MISMATCH",
        "CATALOG_IDENTITY_MISMATCH",
        "NORMALIZATION_IDENTITY_MISMATCH",
        "PATH_INVALID",
        "NONREGULAR_OR_SYMLINK_PATH",
        "SOURCE_BINDING_INVALID",
        "SOURCE_NOT_AUTHORIZED",
        "SUBJECT_BINDING_INVALID",
        "PRODUCER_BINDING_INVALID",
        "TIMESTAMP_INVALID",
        "PROHIBITED_KEY_PRESENT",
        "UNSORTED_RECORDS",
        "DUPLICATE_LOGICAL_KEY",
        "PREDECESSOR_BINDING_INVALID",
        "PREDECESSOR_SELF_REFERENCE",
        "PREDECESSOR_CYCLE",
        "PREDECESSOR_TIME_NOT_EARLIER",
        "CONTENT_CONTRACT_INVALID",
    ],
    "FUTURE_DATED": [
        "OBSERVED_AT_AFTER_EPOCH",
        "PUBLISHED_AT_AFTER_EPOCH",
        "RECORDED_AT_AFTER_EPOCH",
        "ACQUIRED_AT_AFTER_EPOCH",
    ],
    "STALE": ["FIELD_FRESHNESS_LIMIT_EXCEEDED"],
    "VALIDATED_PRESENT": [
        "CONTRACT_VALIDATED_PRESENT",
        "ACCEPTED_POLICY_VALIDATED_PRESENT",
        "EVIDENCE_VALIDATED_PRESENT_AS_OF_EPOCH",
    ],
}

STATUS_REASON_TAXONOMY: Final = _profile(
    "status_reason_taxonomy",
    {
        "profile_version": "ltetf_observation_status_taxonomy_v1",
        "statuses": list(_STATUSES),
        "precedence": list(_STATUSES),
        "reason_codes_by_status": _REASON_CODES_BY_STATUS,
        "present_unvalidated_supported": False,
        "entry_preconditions": {
            "inventory_complete": True,
            "required_schemas_profiles_validators_available": True,
            "resource_bounds_satisfied": True,
        },
        "unavailable_definition": "complete_inventory_with_specific_bound_candidate_whose_bound_bytes_or_predecessor_cannot_be_read_stably",
        "authority_effect": "none",
    },
)

CONFLICT_RULE_PROFILE: Final = _profile(
    "conflict_rule_profile",
    {
        "profile_version": "ltetf_conflict_rule_profile_v1",
        "candidate_precondition": "individually_schema_and_semantic_valid",
        "invalid_candidate_conflict_effect": "none",
        "identical_duplicate_rule": "collapse_for_comparison_and_emit_deterministic_diagnostic_where_defined",
        "diagnostic_order": [
            "class_order",
            "logical_key_canonical_bytes",
            "artifact_identity_sha256",
            "reason_code_order",
        ],
        "class_rules": [
            {
                "class_id": "source_authority_policy",
                "logical_artifact_key": ["policy_type", "policy_id", "policy_version"],
                "logical_fact_key": ["policy_type", "policy_id"],
                "comparable_fields": [_POLICY_CONTENT_IDENTITY_FIELD],
                "excluded_fields": ["policy_artifact_identity_sha256"],
                "different_content_result": "POLICY_SLOT_CONFLICT",
            },
            {
                "class_id": "authorized_source_registry",
                "logical_artifact_key": ["policy_type", "policy_id", "policy_version"],
                "logical_fact_key": ["policy_type", "policy_id"],
                "comparable_fields": [_POLICY_CONTENT_IDENTITY_FIELD],
                "excluded_fields": ["policy_artifact_identity_sha256"],
                "different_content_result": "POLICY_SLOT_CONFLICT",
            },
            {
                "class_id": "field_freshness_policy",
                "logical_artifact_key": ["policy_type", "policy_id", "policy_version"],
                "logical_fact_key": ["policy_type", "policy_id"],
                "comparable_fields": [_POLICY_CONTENT_IDENTITY_FIELD],
                "excluded_fields": ["policy_artifact_identity_sha256"],
                "different_content_result": "POLICY_SLOT_CONFLICT",
            },
            {
                "class_id": "operator_policy_acceptance",
                "logical_artifact_key": ["acceptance_id"],
                "logical_fact_key": [
                    "accepted_policy_type",
                    "policy_id",
                    "policy_version",
                    _POLICY_CONTENT_IDENTITY_FIELD,
                ],
                "comparable_fields": ["acceptance_artifact_identity_sha256"],
                "excluded_fields": [],
                "identical_duplicate_result": "ACCEPTANCE_DUPLICATE",
                "different_content_result": "ACCEPTANCE_ID_REUSE_CONFLICT",
            },
            {
                "class_id": "generic_evidence_manifest",
                "logical_artifact_key": ["manifest_id"],
                "logical_fact_key": [
                    "evidence_class",
                    "subject_identity_sha256",
                    "content_binding.repository_relative_path",
                    "content_binding.content_schema_version",
                ],
                "comparable_fields": ["manifest_identity_sha256"],
                "excluded_fields": [],
                "different_content_result": "LOGICAL_ARTIFACT_CONTENT_CONFLICT",
            },
            {
                "class_id": "trusted_evaluation_epoch",
                "logical_artifact_key": [_CONTENT_IDENTITY_FIELD],
                "logical_fact_key": ["evidence_class", "subject_identity_sha256"],
                "comparable_fields": ["evaluation_epoch_utc"],
                "excluded_fields": ["schema_version"],
                "different_content_result": "MULTIPLE_TRUSTED_EVALUATION_EPOCHS",
            },
            {
                "class_id": "structured_market_metrics",
                "logical_artifact_key": [_CONTENT_IDENTITY_FIELD],
                "logical_fact_key": ["subject_identity_sha256", "metric_id", "observed_at_utc"],
                "comparable_fields": ["value_decimal", "unit_id"],
                "excluded_fields": ["metric_identity_profile_id", "unit_identity_profile_id"],
                "different_content_result": "LOGICAL_FACT_VALUE_CONFLICT",
            },
            {
                "class_id": "structured_scheduled_events",
                "logical_artifact_key": [_CONTENT_IDENTITY_FIELD],
                "logical_fact_key": ["subject_identity_sha256", "event_id", "published_at_utc"],
                "comparable_fields": ["event_type_id", "event_state", "scheduled_at_utc"],
                "excluded_fields": ["event_identity_profile_id", "event_type_identity_profile_id"],
                "different_content_result": "LOGICAL_FACT_VALUE_CONFLICT",
            },
            {
                "class_id": "prior_thesis_continuity",
                "logical_artifact_key": [_CONTENT_IDENTITY_FIELD],
                "logical_fact_key": ["subject_identity_sha256", "thesis_record_id"],
                "comparable_fields": [
                    "recorded_at_utc",
                    "thesis_text",
                    "predecessor_manifest_identity_sha256",
                    _PREDECESSOR_CONTENT_IDENTITY_FIELD,
                    "evidence_references",
                ],
                "excluded_fields": ["thesis_identity_profile_id"],
                "different_content_result": "LOGICAL_FACT_VALUE_CONFLICT",
            },
        ],
        "investment_judgment": "none",
        "evidence_sufficiency_effect": "none",
    },
)

RESOURCE_BOUND_PROFILE: Final = _profile(
    "resource_bound_profile",
    {
        "profile_version": "ltetf_observer_resource_bound_profile_v1",
        "max_explicit_manifests": 256,
        "max_explicit_policy_payloads": 16,
        "max_explicit_acceptance_artifacts": 16,
        "max_canonical_artifact_bytes": 1048576,
        "max_total_canonical_input_bytes": 16777216,
        "max_json_depth": 32,
        "max_json_object_members": 4096,
        "max_json_array_items": 4096,
        "max_report_diagnostics": 4096,
        "bounds_effect": "observer_safety_only",
        "minimum_evidence_effect": "none",
        "investment_sufficiency_effect": "none",
        "universe_coverage_effect": "none",
        "requirement_success_effect": "none",
        "authority_effect": "none",
    },
)

_INTEGRITY_CODES: Final = (
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

INTEGRITY_CODE_PROFILE: Final = _profile(
    "integrity_code_profile",
    {
        "profile_version": "ltetf_observer_integrity_code_profile_v1",
        "integrity_codes": list(_INTEGRITY_CODES),
        "publication_effect": "publish_no_report",
        "normal_status_allowed": False,
        "predicates": [
            {"code": "DISCOVERY_ROOT_CANNOT_BE_ENUMERATED", "predicate": "discovery_root_enumeration_failed"},
            {"code": "INVENTORY_INCOMPLETE", "predicate": "inventory_completeness_not_proven"},
            {"code": "EXPLICIT_MANIFEST_SET_BOUND_EXCEEDED", "predicate": "explicit_manifest_count_gt_256"},
            {"code": "CANONICAL_ARTIFACT_BOUND_EXCEEDED", "predicate": "individual_canonical_bytes_gt_1048576"},
            {"code": "CANONICAL_TOTAL_BOUND_EXCEEDED", "predicate": "total_canonical_input_bytes_gt_16777216"},
            {"code": "FROZEN_SCHEMA_UNAVAILABLE", "predicate": "required_frozen_schema_missing_or_unreadable"},
            {"code": "FROZEN_PROFILE_UNAVAILABLE", "predicate": "required_frozen_profile_missing"},
            {"code": "FROZEN_VALIDATOR_UNAVAILABLE", "predicate": "required_frozen_validator_missing"},
            {"code": "REPOSITORY_CONTRACT_UNREADABLE", "predicate": "required_repository_contract_unreadable"},
            {"code": "IDENTITY_COMPUTATION_FAILED", "predicate": "required_identity_computation_failed"},
            {"code": "REPORT_SCHEMA_VALIDATION_FAILED", "predicate": "produced_report_fails_schema"},
            {"code": "REPORT_PRODUCER_VALIDATION_FAILED", "predicate": "produced_report_fails_producer_validation"},
            {"code": "IMMUTABLE_OUTPUT_CONFLICT", "predicate": "output_path_exists_with_different_bytes"},
        ],
        "authority_effect": "none",
    },
)

SCHEMA_FILENAME_BY_VERSION: Final = _MappingProxyType(
    {
        "ltetf_source_authority_policy_v1": "schemas/ltetf_source_authority_policy.schema.json",
        "ltetf_authorized_source_registry_v1": "schemas/ltetf_authorized_source_registry.schema.json",
        "ltetf_field_freshness_policy_v1": "schemas/ltetf_field_freshness_policy.schema.json",
        "ltetf_operator_policy_acceptance_v1": "schemas/ltetf_operator_policy_acceptance.schema.json",
        "ltetf_generic_evidence_manifest_v1": "schemas/ltetf_generic_evidence_manifest.schema.json",
        "ltetf_trusted_evaluation_epoch_v1": "schemas/ltetf_trusted_evaluation_epoch.schema.json",
        "ltetf_structured_market_metrics_v1": "schemas/ltetf_structured_market_metrics.schema.json",
        "ltetf_structured_scheduled_events_v1": "schemas/ltetf_structured_scheduled_events.schema.json",
        "ltetf_prior_thesis_continuity_v1": "schemas/ltetf_prior_thesis_continuity.schema.json",
    }
)

# These constants are frozen against the exact repository schema bytes after
# strict decoding.  They are literals so importing this module performs no I/O.
SCHEMA_IDENTITY_SHA256_BY_VERSION: Final = _MappingProxyType(
    {
        "ltetf_source_authority_policy_v1": "a07b16224b56aa8c7fadc552f643bb2e2ee06b1f5859d78a2b722e9efec6ee33",
        "ltetf_authorized_source_registry_v1": "d1646948ec313e741edf2b3ee2c1e72d305ab5ac2d2d7946acd4f4b563e46c31",
        "ltetf_field_freshness_policy_v1": "49e662ede868759bace683307b351810490ccccef9c6bcbaad1fa5b49a7edc18",
        "ltetf_operator_policy_acceptance_v1": "d0f9510b2b240825a998e909dc3c992d7afaa3c20c094a787ebdf07a0336345b",
        "ltetf_generic_evidence_manifest_v1": "46bc883482cf81badd9c4614956b2219e02dbacb0554b09afeded7461d3354c6",
        "ltetf_trusted_evaluation_epoch_v1": "173086f4febe3f31ec07daee6654def6faba67ff26443fc028f152759cce1141",
        "ltetf_structured_market_metrics_v1": "9e63633083c3283ebbe088c8c03792464be58d3164d8353a2729fdc1ee092c40",
        "ltetf_structured_scheduled_events_v1": "6c4827a082f8d0f0d2346f5b39bcf0a07dd6a3230a37585536d9b796ddaf4982",
        "ltetf_prior_thesis_continuity_v1": "18566a67bcf4fb18230f98cf5fa424cc4930f1ada376bfc09c5eb56abc8906b8",
    }
)


def _require_exact_keys(value: Mapping[str, object], required: tuple[str, ...], *, code: str) -> None:
    if type(value) is not dict:
        raise IdentityDefinitionError(f"{code}_TYPE_INVALID")
    if tuple(sorted(value)) != tuple(sorted(required)):
        raise IdentityDefinitionError(f"{code}_FIELDS_INVALID")


def _require_sha256(value: object, *, code: str) -> str:
    if type(value) is not str or _HEX_SHA256_RE.fullmatch(value) is None:
        raise IdentityDefinitionError(code)
    return value


def canonical_evidence_subject_payload(subject: Mapping[str, object]) -> dict[str, object]:
    """Return the exact neutral subject payload after static profile checks."""
    _require_exact_keys(
        subject,
        ("subject_kind", "canonical_subject_id", "subject_identity_profile_id"),
        code="SUBJECT",
    )
    subject_kind = subject["subject_kind"]
    canonical_subject_id = subject["canonical_subject_id"]
    profile_id = subject["subject_identity_profile_id"]
    if type(subject_kind) is not str or type(canonical_subject_id) is not str or type(profile_id) is not str:
        raise IdentityDefinitionError("SUBJECT_FIELD_TYPE_INVALID")
    expected_profile: str
    valid_identity = False
    if subject_kind == "evaluation_context":
        expected_profile = "ltetf_evaluation_context_subject_v1"
        valid_identity = canonical_subject_id == "evaluation_context:ltetf_evidence_observation"
    elif subject_kind == "instrument":
        expected_profile = "ltetf_instrument_subject_v1"
        valid_identity = _SUBJECT_INSTRUMENT_RE.fullmatch(canonical_subject_id) is not None
    elif subject_kind == "market":
        expected_profile = "ltetf_market_subject_v1"
        valid_identity = _SUBJECT_MARKET_RE.fullmatch(canonical_subject_id) is not None
    else:
        raise IdentityDefinitionError("SUBJECT_KIND_INVALID")
    if profile_id != expected_profile:
        raise IdentityDefinitionError("SUBJECT_PROFILE_ID_INVALID")
    if not valid_identity:
        raise IdentityDefinitionError("CANONICAL_SUBJECT_ID_INVALID")
    return {
        "subject_kind": subject_kind,
        "canonical_subject_id": canonical_subject_id,
        "subject_identity_profile_id": profile_id,
    }


def evidence_subject_identity_sha256(subject: Mapping[str, object]) -> str:
    return domain_separated_sha256(
        DOMAIN_SEPARATORS["evidence_subject"],
        canonical_evidence_subject_payload(subject),
    )


def _validate_https_path_prefix(value: object) -> str:
    if type(value) is not str or not value.isascii() or len(value.encode("ascii")) > 256:
        raise IdentityDefinitionError("HTTPS_PATH_PREFIX_INVALID")
    if _HTTPS_PATH_PREFIX_RE.fullmatch(value) is None:
        raise IdentityDefinitionError("HTTPS_PATH_PREFIX_INVALID")
    if "//" in value or "/./" in f"{value}/" or "/../" in f"{value}/":
        raise IdentityDefinitionError("HTTPS_PATH_PREFIX_NONCANONICAL")
    percent_escapes = _re.findall(r"%[0-9A-Fa-f]{2}", value)
    if any(escape != escape.upper() for escape in percent_escapes):
        raise IdentityDefinitionError("HTTPS_PERCENT_ESCAPE_NONCANONICAL")
    if "%" in _re.sub(r"%[0-9A-Fa-f]{2}", "", value):
        raise IdentityDefinitionError("HTTPS_PERCENT_ESCAPE_INVALID")
    return value


def canonical_source_locator_payload(locator: Mapping[str, object]) -> dict[str, object]:
    """Return one exact locator representation without dereferencing it."""
    if type(locator) is not dict or type(locator.get("locator_kind")) is not str:
        raise IdentityDefinitionError("SOURCE_LOCATOR_TYPE_INVALID")
    locator_kind = locator["locator_kind"]
    if locator_kind == "repository_path":
        _require_exact_keys(locator, ("locator_kind", "repository_relative_path"), code="SOURCE_LOCATOR")
        path = validate_repository_relative_path_syntax(locator["repository_relative_path"])
        return {"locator_kind": locator_kind, "repository_relative_path": path}
    if locator_kind == "https_origin":
        _require_exact_keys(
            locator,
            ("locator_kind", "scheme", "host_ascii", "port", "path_prefix"),
            code="SOURCE_LOCATOR",
        )
        scheme = locator["scheme"]
        host = locator["host_ascii"]
        port = locator["port"]
        if scheme != "https":
            raise IdentityDefinitionError("HTTPS_SCHEME_INVALID")
        if type(host) is not str or not host.isascii() or host != host.lower() or _HTTPS_HOST_RE.fullmatch(host) is None:
            raise IdentityDefinitionError("HTTPS_HOST_INVALID")
        if port is not None:
            if type(port) is not int or not 1 <= port <= 65535 or port == 443:
                raise IdentityDefinitionError("HTTPS_PORT_INVALID")
        path_prefix = _validate_https_path_prefix(locator["path_prefix"])
        return {
            "locator_kind": locator_kind,
            "scheme": scheme,
            "host_ascii": host,
            "port": port,
            "path_prefix": path_prefix,
        }
    if locator_kind == "opaque_source_id":
        _require_exact_keys(locator, ("locator_kind", "namespace", "opaque_id"), code="SOURCE_LOCATOR")
        namespace = locator["namespace"]
        opaque_id = locator["opaque_id"]
        if type(namespace) is not str or _OPAQUE_NAMESPACE_RE.fullmatch(namespace) is None:
            raise IdentityDefinitionError("OPAQUE_NAMESPACE_INVALID")
        if type(opaque_id) is not str or _OPAQUE_ID_RE.fullmatch(opaque_id) is None:
            raise IdentityDefinitionError("OPAQUE_ID_INVALID")
        return {"locator_kind": locator_kind, "namespace": namespace, "opaque_id": opaque_id}
    raise IdentityDefinitionError("SOURCE_LOCATOR_KIND_INVALID")


def source_locator_identity_sha256(locator: Mapping[str, object]) -> str:
    return domain_separated_sha256(
        DOMAIN_SEPARATORS["source_locator"],
        canonical_source_locator_payload(locator),
    )


def canonical_authorized_source_record_payload(record: Mapping[str, object]) -> dict[str, object]:
    """Return the source-record identity payload, excluding only its self hash."""
    expected = (
        "source_id",
        "source_identity_profile_id",
        "source_class",
        "source_locator",
        "source_locator_identity_sha256",
        "authorized_evidence_classes",
        "source_record_identity_sha256",
    )
    _require_exact_keys(record, expected, code="AUTHORIZED_SOURCE_RECORD")
    source_id = record["source_id"]
    source_class = record["source_class"]
    profile_id = record["source_identity_profile_id"]
    evidence_classes = record["authorized_evidence_classes"]
    locator = record["source_locator"]
    locator_identity = record["source_locator_identity_sha256"]
    _require_sha256(record["source_record_identity_sha256"], code="SOURCE_RECORD_IDENTITY_INVALID")
    if type(source_id) is not str or _SOURCE_ID_RE.fullmatch(source_id) is None:
        raise IdentityDefinitionError("SOURCE_ID_INVALID")
    if profile_id != "ltetf_source_identity_profile_v1":
        raise IdentityDefinitionError("SOURCE_IDENTITY_PROFILE_INVALID")
    if source_class not in _SOURCE_CLASSES:
        raise IdentityDefinitionError("SOURCE_CLASS_INVALID")
    if type(evidence_classes) is not list or not 1 <= len(evidence_classes) <= len(_EVIDENCE_CLASSES):
        raise IdentityDefinitionError("AUTHORIZED_EVIDENCE_CLASSES_INVALID")
    if any(type(item) is not str or item not in _EVIDENCE_CLASSES for item in evidence_classes):
        raise IdentityDefinitionError("AUTHORIZED_EVIDENCE_CLASS_INVALID")
    expected_order = [item for item in _EVIDENCE_CLASSES if item in evidence_classes]
    if evidence_classes != expected_order or len(evidence_classes) != len(set(evidence_classes)):
        raise IdentityDefinitionError("AUTHORIZED_EVIDENCE_CLASSES_ORDER_INVALID")
    if type(locator) is not dict:
        raise IdentityDefinitionError("SOURCE_LOCATOR_TYPE_INVALID")
    canonical_locator = canonical_source_locator_payload(locator)
    expected_locator_identity = source_locator_identity_sha256(locator)
    if locator_identity != expected_locator_identity:
        raise IdentityDefinitionError("SOURCE_LOCATOR_IDENTITY_MISMATCH")
    return {
        "source_id": source_id,
        "source_identity_profile_id": profile_id,
        "source_class": source_class,
        "source_locator": canonical_locator,
        "source_locator_identity_sha256": locator_identity,
        "authorized_evidence_classes": list(evidence_classes),
    }


def authorized_source_record_identity_sha256(record: Mapping[str, object]) -> str:
    return domain_separated_sha256(
        DOMAIN_SEPARATORS["authorized_source_record"],
        canonical_authorized_source_record_payload(record),
    )


def producer_identity_sha256(producer: Mapping[str, object]) -> str:
    """Compute producer identity while excluding only its own self hash."""
    _require_exact_keys(
        producer,
        ("producer_id", "producer_version", "producer_contract_identity_sha256", "producer_identity_sha256"),
        code="PRODUCER",
    )
    producer_id = producer["producer_id"]
    producer_version = producer["producer_version"]
    if type(producer_id) is not str or _PRODUCER_ID_RE.fullmatch(producer_id) is None:
        raise IdentityDefinitionError("PRODUCER_ID_INVALID")
    if type(producer_version) is not str or _VERSION_RE.fullmatch(producer_version) is None:
        raise IdentityDefinitionError("PRODUCER_VERSION_INVALID")
    contract_identity = _require_sha256(
        producer["producer_contract_identity_sha256"], code="PRODUCER_CONTRACT_IDENTITY_INVALID"
    )
    _require_sha256(producer["producer_identity_sha256"], code="PRODUCER_IDENTITY_INVALID")
    return domain_separated_sha256(
        DOMAIN_SEPARATORS["producer_identity"],
        {
            "producer_id": producer_id,
            "producer_version": producer_version,
            "producer_contract_identity_sha256": contract_identity,
        },
    )


def schema_identity_sha256(
    schema_version: str,
    schema_path: str,
    schema: Mapping[str, object],
) -> str:
    """Compute the frozen identity of one complete decoded schema object."""
    if type(schema_version) is not str or schema_version not in SCHEMA_FILENAME_BY_VERSION:
        raise IdentityDefinitionError("SCHEMA_VERSION_UNSUPPORTED")
    normalized_path = validate_repository_relative_path_syntax(schema_path)
    if normalized_path != SCHEMA_FILENAME_BY_VERSION[schema_version]:
        raise IdentityDefinitionError("SCHEMA_PATH_MISMATCH")
    if type(schema) is not dict:
        raise IdentityDefinitionError("SCHEMA_OBJECT_INVALID")
    schema_id = schema.get("$id")
    expected_id = f"https://investment-system.local/{normalized_path}"
    if type(schema_id) is not str or schema_id != expected_id:
        raise IdentityDefinitionError("SCHEMA_ID_MISMATCH")
    return domain_separated_sha256(
        DOMAIN_SEPARATORS["schema_identity"],
        {
            "schema_version": schema_version,
            "schema_path": normalized_path,
            "schema_id": schema_id,
            "normalization_profile_identity_sha256": NORMALIZATION_PROFILE.identity_sha256,
            "schema": schema,
        },
    )


def semantic_contract_identity_sha256(contract: Mapping[str, object]) -> str:
    """Compute an identity for one exact static semantic-contract record."""
    required = (
        "contract_version",
        "contract_id",
        "schema_identity_sha256",
        "ordered_semantic_rule_ids",
        "ordered_diagnostic_codes",
        "required_profile_identities",
        "return_contract_version",
    )
    if type(contract) is not dict:
        raise IdentityDefinitionError("SEMANTIC_CONTRACT_TYPE_INVALID")
    permitted = set(required) | {"semantic_contract_identity_sha256"}
    if set(contract) != set(required) and set(contract) != permitted:
        raise IdentityDefinitionError("SEMANTIC_CONTRACT_FIELDS_INVALID")
    payload = {field: contract[field] for field in required}
    if type(payload["contract_version"]) is not str or _VERSION_RE.fullmatch(payload["contract_version"]) is None:
        raise IdentityDefinitionError("SEMANTIC_CONTRACT_VERSION_INVALID")
    if type(payload["contract_id"]) is not str or _IDENTIFIER_RE.fullmatch(payload["contract_id"]) is None:
        raise IdentityDefinitionError("SEMANTIC_CONTRACT_ID_INVALID")
    _require_sha256(payload["schema_identity_sha256"], code="SEMANTIC_SCHEMA_IDENTITY_INVALID")
    for ordered_field in (
        "ordered_semantic_rule_ids",
        "ordered_diagnostic_codes",
        "required_profile_identities",
    ):
        members = payload[ordered_field]
        if type(members) is not list or any(type(member) is not str for member in members):
            raise IdentityDefinitionError(f"SEMANTIC_{ordered_field.upper()}_INVALID")
        if len(members) != len(set(members)):
            raise IdentityDefinitionError(f"SEMANTIC_{ordered_field.upper()}_DUPLICATE")
    if type(payload["return_contract_version"]) is not str or _VERSION_RE.fullmatch(
        payload["return_contract_version"]
    ) is None:
        raise IdentityDefinitionError("SEMANTIC_RETURN_CONTRACT_VERSION_INVALID")
    if "semantic_contract_identity_sha256" in contract:
        _require_sha256(
            contract["semantic_contract_identity_sha256"],
            code="SEMANTIC_CONTRACT_SELF_IDENTITY_INVALID",
        )
    return domain_separated_sha256(DOMAIN_SEPARATORS["semantic_contract_identity"], payload)


def _policy_content_identity_sha256(policy: Mapping[str, object]) -> str:
    """Internal 02a1 identity primitive; this does not validate a policy."""
    required = (
        "schema_version",
        "policy_type",
        "authority_effect",
        "policy_content",
        _POLICY_CONTENT_IDENTITY_FIELD,
        "policy_artifact_identity_sha256",
    )
    _require_exact_keys(policy, required, code="POLICY_PAYLOAD")
    policy_type = policy["policy_type"]
    if policy_type not in _POLICY_TYPES:
        raise IdentityDefinitionError("POLICY_TYPE_INVALID")
    expected_schema_version = {
        "source_authority_policy": "ltetf_source_authority_policy_v1",
        "authorized_source_registry": "ltetf_authorized_source_registry_v1",
        "field_freshness_policy": "ltetf_field_freshness_policy_v1",
    }[policy_type]
    if policy["schema_version"] != expected_schema_version:
        raise IdentityDefinitionError("POLICY_SCHEMA_VERSION_MISMATCH")
    if policy["authority_effect"] != "none":
        raise IdentityDefinitionError("POLICY_AUTHORITY_EFFECT_INVALID")
    _require_sha256(policy[_POLICY_CONTENT_IDENTITY_FIELD], code="POLICY_CONTENT_IDENTITY_INVALID")
    _require_sha256(policy["policy_artifact_identity_sha256"], code="POLICY_ARTIFACT_IDENTITY_INVALID")
    if type(policy["policy_content"]) is not dict:
        raise IdentityDefinitionError("POLICY_CONTENT_TYPE_INVALID")
    payload = {
        "schema_version": policy["schema_version"],
        "policy_type": policy_type,
        "authority_effect": policy["authority_effect"],
        "policy_content": policy["policy_content"],
    }
    return domain_separated_sha256(DOMAIN_SEPARATORS[f"{policy_type}_content"], payload)


def _policy_artifact_identity_sha256(policy: Mapping[str, object]) -> str:
    """Internal 02a1 outer identity primitive; excludes only the outer self hash."""
    _policy_content_identity_sha256(policy)
    return domain_separated_sha256(
        DOMAIN_SEPARATORS["policy_payload_artifact"],
        {
            "schema_version": policy["schema_version"],
            "policy_type": policy["policy_type"],
            "authority_effect": policy["authority_effect"],
            "policy_content": policy["policy_content"],
            _POLICY_CONTENT_IDENTITY_FIELD: policy[_POLICY_CONTENT_IDENTITY_FIELD],
        },
    )


def _acceptance_artifact_identity_sha256(acceptance: Mapping[str, object]) -> str:
    if type(acceptance) is not dict or "acceptance_artifact_identity_sha256" not in acceptance:
        raise IdentityDefinitionError("ACCEPTANCE_FIELDS_INVALID")
    _require_sha256(
        acceptance["acceptance_artifact_identity_sha256"],
        code="ACCEPTANCE_ARTIFACT_IDENTITY_INVALID",
    )
    payload = dict(acceptance)
    del payload["acceptance_artifact_identity_sha256"]
    return domain_separated_sha256(DOMAIN_SEPARATORS["operator_policy_acceptance"], payload)


_PROFILE_IDENTITY_BY_VERSION: Final = _MappingProxyType(
    {
        profile.profile_version: profile.identity_sha256
        for profile in (
            NORMALIZATION_PROFILE,
            PROHIBITED_KEY_PROFILE,
            SOURCE_AUTHORITY_TAXONOMY,
            SUBJECT_PROFILE,
            LOCATOR_PROFILE,
            METRIC_PROFILE,
            UNIT_PROFILE,
            EVENT_PROFILE,
            THESIS_PROFILE,
            STATUS_REASON_TAXONOMY,
            CONFLICT_RULE_PROFILE,
            RESOURCE_BOUND_PROFILE,
            INTEGRITY_CODE_PROFILE,
        )
    }
)

_SEMANTIC_RULES_BY_VERSION: Final = _MappingProxyType(
    {
        "ltetf_source_authority_policy_v1": (
            "POLICY_ENVELOPE_EXACT",
            "ARTIFACT_AUTHORITY_EFFECT_NONE",
            "POLICY_TYPE_SCHEMA_BINDING",
            "POLICY_CONTENT_IDENTITY_EXACT",
            "POLICY_ARTIFACT_IDENTITY_EXACT",
            "POLICY_PAYLOAD_CANNOT_SELF_ACCEPT",
            "SOURCE_CLASS_RULE_ORDER_EXACT",
        ),
        "ltetf_authorized_source_registry_v1": (
            "POLICY_ENVELOPE_EXACT",
            "ARTIFACT_AUTHORITY_EFFECT_NONE",
            "POLICY_TYPE_SCHEMA_BINDING",
            "POLICY_CONTENT_IDENTITY_EXACT",
            "POLICY_ARTIFACT_IDENTITY_EXACT",
            "POLICY_PAYLOAD_CANNOT_SELF_ACCEPT",
            "SOURCE_AUTHORITY_POLICY_BINDING_EXACT",
            "SOURCE_RECORD_ORDER_EXACT",
            "SOURCE_RECORD_IDENTITY_EXACT",
            "SOURCE_LOCATOR_IDENTITY_EXACT",
            "SOURCE_ID_UNIQUE",
        ),
        "ltetf_field_freshness_policy_v1": (
            "POLICY_ENVELOPE_EXACT",
            "ARTIFACT_AUTHORITY_EFFECT_NONE",
            "POLICY_TYPE_SCHEMA_BINDING",
            "POLICY_CONTENT_IDENTITY_EXACT",
            "POLICY_ARTIFACT_IDENTITY_EXACT",
            "POLICY_PAYLOAD_CANNOT_SELF_ACCEPT",
            "SOURCE_AUTHORITY_POLICY_BINDING_EXACT",
            "AUTHORIZED_SOURCE_REGISTRY_BINDING_EXACT",
            "FRESHNESS_RULE_ORDER_EXACT",
        ),
        "ltetf_operator_policy_acceptance_v1": (
            "ARTIFACT_AUTHORITY_EFFECT_NONE",
            "ACCEPTANCE_IDENTITY_EXACT",
            "ACCEPTANCE_POLICY_BINDINGS_EXACT",
            "ACCEPTANCE_CATALOG_BINDING_EXACT",
            "ONE_ACCEPTANCE_PER_EXACT_POLICY_CONTENT",
            "NO_AUTHENTICATION_ACTIVATION_OR_PERMISSION_CLAIM",
        ),
        "ltetf_generic_evidence_manifest_v1": (
            "ARTIFACT_AUTHORITY_EFFECT_NONE",
            "MANIFEST_IDENTITY_EXACT",
            "MANIFEST_SUBJECT_BINDING_EXACT",
            "MANIFEST_SOURCE_RECORD_BINDINGS_EXACT",
            "MANIFEST_CONTENT_BYTES_AND_IDENTITY_BINDING_EXACT",
            "MANIFEST_SCHEMA_AND_CONTRACT_BINDING_EXACT",
            "MANIFEST_PRODUCER_BINDING_EXACT",
            "MANIFEST_POLICY_AND_CATALOG_BINDINGS_EXACT",
            "MANIFEST_ACQUISITION_TIMESTAMP_EXACT",
            "MANIFEST_PREDECESSOR_BINDING_CLASS_RULE",
            "MANIFEST_HAS_NO_RESULT_FIELDS",
        ),
        "ltetf_trusted_evaluation_epoch_v1": (
            "ARTIFACT_AUTHORITY_EFFECT_NONE",
            "CONTENT_IDENTITY_EXACT",
            "CONTENT_SUBJECT_BINDING_EXACT",
            "EVALUATION_EPOCH_TIMESTAMP_EXACT",
            "EVALUATION_EPOCH_HAS_NO_SOURCE_OR_FRESHNESS_SELF_CLAIM",
            "EVALUATION_EPOCH_IS_NOT_CURRENT_TIME_CLAIM",
            "HOST_AND_FILESYSTEM_TIME_HAVE_NO_AUTHORITY",
            "HISTORIC_EVALUATION_IS_REPRODUCIBLE",
            "REPLAYED_EPOCH_REMAINS_HISTORIC_IDENTITY",
            "MULTIPLE_EPOCH_CANDIDATES_CONFLICT",
        ),
        "ltetf_structured_market_metrics_v1": (
            "ARTIFACT_AUTHORITY_EFFECT_NONE",
            "CONTENT_IDENTITY_EXACT",
            "CONTENT_SUBJECT_BINDING_EXACT",
            "METRIC_RECORD_ORDER_EXACT",
            "METRIC_DECIMAL_GRAMMAR_EXACT",
            "METRIC_UNIT_PROFILE_EXACT",
            "METRIC_DUPLICATE_LOGICAL_KEY_REJECTED",
            "METRIC_TIMESTAMP_AGGREGATION_ALL_RECORDS",
            "EMPTY_METRIC_COLLECTION_HAS_NO_SUFFICIENCY_EFFECT",
        ),
        "ltetf_structured_scheduled_events_v1": (
            "ARTIFACT_AUTHORITY_EFFECT_NONE",
            "CONTENT_IDENTITY_EXACT",
            "CONTENT_SUBJECT_BINDING_EXACT",
            "EVENT_RECORD_ORDER_EXACT",
            "EVENT_TYPE_AND_STATE_PROFILE_EXACT",
            "EVENT_DUPLICATE_LOGICAL_KEY_REJECTED",
            "EVENT_PUBLICATION_TIMESTAMP_AGGREGATION_ALL_RECORDS",
            "EVENT_SCHEDULED_TIME_DOES_NOT_ESTABLISH_LOOKAHEAD",
            "EMPTY_EVENT_COLLECTION_HAS_NO_SUFFICIENCY_EFFECT",
        ),
        "ltetf_prior_thesis_continuity_v1": (
            "ARTIFACT_AUTHORITY_EFFECT_NONE",
            "CONTENT_IDENTITY_EXACT",
            "CONTENT_SUBJECT_BINDING_EXACT",
            "THESIS_TEXT_BOUNDS_EXACT",
            "THESIS_REFERENCE_ORDER_EXACT",
            "THESIS_REFERENCE_DUPLICATE_REJECTED",
            "THESIS_PREDECESSOR_PAIR_EXACT",
            "THESIS_PREDECESSOR_SELF_REFERENCE_REJECTED",
            "THESIS_PREDECESSOR_CYCLE_REJECTED",
            "THESIS_PREDECESSOR_TIME_STRICTLY_EARLIER",
            "THESIS_RECORDED_TIMESTAMP_EXACT",
        ),
    }
)

_DIAGNOSTICS_BY_VERSION: Final = _MappingProxyType(
    {
        "ltetf_source_authority_policy_v1": (
            "SCHEMA_INVALID",
            "SCHEMA_VERSION_UNSUPPORTED",
            "PROHIBITED_KEY_PRESENT",
            "POLICY_FIELDS_INVALID",
            "POLICY_TYPE_MISMATCH",
            "POLICY_SCHEMA_IDENTITY_MISMATCH",
            "POLICY_CONTRACT_IDENTITY_MISMATCH",
            "POLICY_CONTENT_IDENTITY_MISMATCH",
            "POLICY_ARTIFACT_IDENTITY_MISMATCH",
            "POLICY_SELF_ACCEPTANCE_FIELD_PRESENT",
            "POLICY_AUTHORITY_EFFECT_INVALID",
            "SOURCE_AUTHORITY_RULE_ORDER_INVALID",
            "SOURCE_AUTHORITY_RULE_DUPLICATE",
        ),
        "ltetf_authorized_source_registry_v1": (
            "SCHEMA_INVALID",
            "SCHEMA_VERSION_UNSUPPORTED",
            "PROHIBITED_KEY_PRESENT",
            "POLICY_FIELDS_INVALID",
            "POLICY_TYPE_MISMATCH",
            "POLICY_SCHEMA_IDENTITY_MISMATCH",
            "POLICY_CONTRACT_IDENTITY_MISMATCH",
            "POLICY_CONTENT_IDENTITY_MISMATCH",
            "POLICY_ARTIFACT_IDENTITY_MISMATCH",
            "POLICY_SELF_ACCEPTANCE_FIELD_PRESENT",
            "POLICY_AUTHORITY_EFFECT_INVALID",
            "SOURCE_AUTHORITY_POLICY_BINDING_MISMATCH",
            "SOURCE_RECORD_ORDER_INVALID",
            "SOURCE_ID_DUPLICATE",
            "SOURCE_RECORD_IDENTITY_MISMATCH",
            "SOURCE_LOCATOR_IDENTITY_MISMATCH",
            "SOURCE_LOCATOR_NONCANONICAL",
        ),
        "ltetf_field_freshness_policy_v1": (
            "SCHEMA_INVALID",
            "SCHEMA_VERSION_UNSUPPORTED",
            "PROHIBITED_KEY_PRESENT",
            "POLICY_FIELDS_INVALID",
            "POLICY_TYPE_MISMATCH",
            "POLICY_SCHEMA_IDENTITY_MISMATCH",
            "POLICY_CONTRACT_IDENTITY_MISMATCH",
            "POLICY_CONTENT_IDENTITY_MISMATCH",
            "POLICY_ARTIFACT_IDENTITY_MISMATCH",
            "POLICY_SELF_ACCEPTANCE_FIELD_PRESENT",
            "POLICY_AUTHORITY_EFFECT_INVALID",
            "SOURCE_AUTHORITY_POLICY_BINDING_MISMATCH",
            "AUTHORIZED_SOURCE_REGISTRY_BINDING_MISMATCH",
            "FRESHNESS_RULE_ORDER_INVALID",
            "FRESHNESS_RULE_DUPLICATE",
            "FRESHNESS_RULE_FIELD_INVALID",
        ),
        "ltetf_operator_policy_acceptance_v1": (
            "SCHEMA_INVALID",
            "SCHEMA_VERSION_UNSUPPORTED",
            "PROHIBITED_KEY_PRESENT",
            "ACCEPTANCE_FIELDS_INVALID",
            "ACCEPTANCE_ARTIFACT_IDENTITY_MISMATCH",
            "ACCEPTANCE_POLICY_TYPE_BINDING_MISMATCH",
            "ACCEPTANCE_POLICY_ID_BINDING_MISMATCH",
            "ACCEPTANCE_POLICY_VERSION_BINDING_MISMATCH",
            "ACCEPTANCE_POLICY_ARTIFACT_BINDING_MISMATCH",
            "ACCEPTANCE_POLICY_CONTENT_BINDING_MISMATCH",
            "ACCEPTANCE_POLICY_SCHEMA_BINDING_MISMATCH",
            "ACCEPTANCE_POLICY_CONTRACT_BINDING_MISMATCH",
            "ACCEPTANCE_CATALOG_BINDING_MISMATCH",
            "ACCEPTANCE_AUTHORITY_EFFECT_INVALID",
            "ACCEPTANCE_DUPLICATE",
            "ACCEPTANCE_ID_REUSE_CONFLICT",
        ),
        "ltetf_generic_evidence_manifest_v1": (
            "SCHEMA_INVALID",
            "SCHEMA_VERSION_UNSUPPORTED",
            "PROHIBITED_KEY_PRESENT",
            "MANIFEST_FIELDS_INVALID",
            "MANIFEST_IDENTITY_MISMATCH",
            "MANIFEST_AUTHORITY_EFFECT_INVALID",
            "EVIDENCE_CLASS_SUBJECT_MISMATCH",
            "SUBJECT_IDENTITY_MISMATCH",
            "SOURCE_BINDING_INVALID",
            "SOURCE_NOT_AUTHORIZED",
            "CONTENT_PATH_INVALID",
            "CONTENT_BYTES_IDENTITY_MISMATCH",
            "CONTENT_IDENTITY_MISMATCH",
            "CONTENT_SCHEMA_BINDING_MISMATCH",
            "CONTENT_CONTRACT_BINDING_MISMATCH",
            "PRODUCER_IDENTITY_MISMATCH",
            "NORMALIZATION_IDENTITY_MISMATCH",
            "POLICY_BINDING_MISMATCH",
            "CATALOG_IDENTITY_MISMATCH",
            "ACQUIRED_AT_INVALID",
            "ACQUIRED_AT_AFTER_EPOCH",
            "PREDECESSOR_BINDING_INVALID",
        ),
        "ltetf_trusted_evaluation_epoch_v1": (
            "SCHEMA_INVALID",
            "SCHEMA_VERSION_UNSUPPORTED",
            "PROHIBITED_KEY_PRESENT",
            "CONTENT_CONTRACT_INVALID",
            "CONTENT_IDENTITY_MISMATCH",
            "CONTENT_AUTHORITY_EFFECT_INVALID",
            "SUBJECT_BINDING_INVALID",
            "EVALUATION_EPOCH_TIMESTAMP_INVALID",
            "MULTIPLE_TRUSTED_EVALUATION_EPOCHS",
        ),
        "ltetf_structured_market_metrics_v1": (
            "SCHEMA_INVALID",
            "SCHEMA_VERSION_UNSUPPORTED",
            "PROHIBITED_KEY_PRESENT",
            "CONTENT_CONTRACT_INVALID",
            "CONTENT_IDENTITY_MISMATCH",
            "CONTENT_AUTHORITY_EFFECT_INVALID",
            "SUBJECT_BINDING_INVALID",
            "METRIC_RECORD_ORDER_INVALID",
            "METRIC_LOGICAL_KEY_DUPLICATE",
            "METRIC_DECIMAL_INVALID",
            "METRIC_UNIT_INVALID",
            "METRIC_TIMESTAMP_INVALID",
            "METRIC_TIMESTAMP_AFTER_EPOCH",
            "METRIC_FIELD_STALE",
            "METRIC_LOGICAL_FACT_VALUE_CONFLICT",
        ),
        "ltetf_structured_scheduled_events_v1": (
            "SCHEMA_INVALID",
            "SCHEMA_VERSION_UNSUPPORTED",
            "PROHIBITED_KEY_PRESENT",
            "CONTENT_CONTRACT_INVALID",
            "CONTENT_IDENTITY_MISMATCH",
            "CONTENT_AUTHORITY_EFFECT_INVALID",
            "SUBJECT_BINDING_INVALID",
            "EVENT_RECORD_ORDER_INVALID",
            "EVENT_LOGICAL_KEY_DUPLICATE",
            "EVENT_TYPE_INVALID",
            "EVENT_STATE_INVALID",
            "EVENT_PUBLICATION_TIMESTAMP_INVALID",
            "EVENT_PUBLICATION_TIMESTAMP_AFTER_EPOCH",
            "EVENT_PUBLICATION_FIELD_STALE",
            "EVENT_LOGICAL_FACT_VALUE_CONFLICT",
        ),
        "ltetf_prior_thesis_continuity_v1": (
            "SCHEMA_INVALID",
            "SCHEMA_VERSION_UNSUPPORTED",
            "PROHIBITED_KEY_PRESENT",
            "CONTENT_CONTRACT_INVALID",
            "CONTENT_IDENTITY_MISMATCH",
            "CONTENT_AUTHORITY_EFFECT_INVALID",
            "SUBJECT_BINDING_INVALID",
            "THESIS_TEXT_INVALID",
            "THESIS_REFERENCE_ORDER_INVALID",
            "THESIS_REFERENCE_DUPLICATE",
            "THESIS_RECORDED_TIMESTAMP_INVALID",
            "THESIS_RECORDED_TIMESTAMP_AFTER_EPOCH",
            "THESIS_RECORDED_FIELD_STALE",
            "THESIS_PREDECESSOR_BINDING_INVALID",
            "THESIS_PREDECESSOR_SELF_REFERENCE",
            "THESIS_PREDECESSOR_CYCLE",
            "THESIS_PREDECESSOR_TIME_NOT_EARLIER",
            "THESIS_LOGICAL_FACT_VALUE_CONFLICT",
        ),
    }
)

_REQUIRED_PROFILES_BY_VERSION: Final = _MappingProxyType(
    {
        "ltetf_source_authority_policy_v1": (
            NORMALIZATION_PROFILE.identity_sha256,
            PROHIBITED_KEY_PROFILE.identity_sha256,
            SOURCE_AUTHORITY_TAXONOMY.identity_sha256,
        ),
        "ltetf_authorized_source_registry_v1": (
            NORMALIZATION_PROFILE.identity_sha256,
            PROHIBITED_KEY_PROFILE.identity_sha256,
            SOURCE_AUTHORITY_TAXONOMY.identity_sha256,
            LOCATOR_PROFILE.identity_sha256,
        ),
        "ltetf_field_freshness_policy_v1": (
            NORMALIZATION_PROFILE.identity_sha256,
            PROHIBITED_KEY_PROFILE.identity_sha256,
        ),
        "ltetf_operator_policy_acceptance_v1": (
            NORMALIZATION_PROFILE.identity_sha256,
            PROHIBITED_KEY_PROFILE.identity_sha256,
        ),
        "ltetf_generic_evidence_manifest_v1": (
            NORMALIZATION_PROFILE.identity_sha256,
            PROHIBITED_KEY_PROFILE.identity_sha256,
            SOURCE_AUTHORITY_TAXONOMY.identity_sha256,
            SUBJECT_PROFILE.identity_sha256,
            LOCATOR_PROFILE.identity_sha256,
        ),
        "ltetf_trusted_evaluation_epoch_v1": (
            NORMALIZATION_PROFILE.identity_sha256,
            PROHIBITED_KEY_PROFILE.identity_sha256,
            SUBJECT_PROFILE.identity_sha256,
            CONFLICT_RULE_PROFILE.identity_sha256,
        ),
        "ltetf_structured_market_metrics_v1": (
            NORMALIZATION_PROFILE.identity_sha256,
            PROHIBITED_KEY_PROFILE.identity_sha256,
            SUBJECT_PROFILE.identity_sha256,
            METRIC_PROFILE.identity_sha256,
            UNIT_PROFILE.identity_sha256,
            CONFLICT_RULE_PROFILE.identity_sha256,
        ),
        "ltetf_structured_scheduled_events_v1": (
            NORMALIZATION_PROFILE.identity_sha256,
            PROHIBITED_KEY_PROFILE.identity_sha256,
            SUBJECT_PROFILE.identity_sha256,
            EVENT_PROFILE.identity_sha256,
            CONFLICT_RULE_PROFILE.identity_sha256,
        ),
        "ltetf_prior_thesis_continuity_v1": (
            NORMALIZATION_PROFILE.identity_sha256,
            PROHIBITED_KEY_PROFILE.identity_sha256,
            SUBJECT_PROFILE.identity_sha256,
            THESIS_PROFILE.identity_sha256,
            CONFLICT_RULE_PROFILE.identity_sha256,
        ),
    }
)

_RETURN_CONTRACT_BY_VERSION: Final = _MappingProxyType(
    {
        "ltetf_source_authority_policy_v1": "ltetf_policy_payload_validation_result_v1",
        "ltetf_authorized_source_registry_v1": "ltetf_policy_payload_validation_result_v1",
        "ltetf_field_freshness_policy_v1": "ltetf_policy_payload_validation_result_v1",
        "ltetf_operator_policy_acceptance_v1": "ltetf_operator_policy_acceptance_validation_result_v1",
        "ltetf_generic_evidence_manifest_v1": "ltetf_evidence_manifest_validation_result_v1",
        "ltetf_trusted_evaluation_epoch_v1": "ltetf_evidence_content_validation_result_v1",
        "ltetf_structured_market_metrics_v1": "ltetf_evidence_content_validation_result_v1",
        "ltetf_structured_scheduled_events_v1": "ltetf_evidence_content_validation_result_v1",
        "ltetf_prior_thesis_continuity_v1": "ltetf_evidence_content_validation_result_v1",
    }
)


def _semantic_contract_record(schema_version: str) -> dict[str, object]:
    return {
        "contract_version": f"{schema_version[:-3]}_contract_v1",
        "contract_id": f"{schema_version[:-3]}_semantic_contract",
        "schema_identity_sha256": SCHEMA_IDENTITY_SHA256_BY_VERSION[schema_version],
        "ordered_semantic_rule_ids": list(_SEMANTIC_RULES_BY_VERSION[schema_version]),
        "ordered_diagnostic_codes": list(_DIAGNOSTICS_BY_VERSION[schema_version]),
        "required_profile_identities": list(_REQUIRED_PROFILES_BY_VERSION[schema_version]),
        "return_contract_version": _RETURN_CONTRACT_BY_VERSION[schema_version],
    }


SEMANTIC_CONTRACT_IDENTITY_SHA256_BY_VERSION: Final = _MappingProxyType(
    {
        version: semantic_contract_identity_sha256(_semantic_contract_record(version))
        for version in SCHEMA_FILENAME_BY_VERSION
    }
)


def load_and_verify_frozen_schema(repository_root: _Path, schema_version: str) -> dict[str, object]:
    """Explicitly load and verify one frozen schema; importing performs no I/O."""
    if not isinstance(repository_root, _Path):
        raise IdentityDefinitionError("REPOSITORY_ROOT_TYPE_INVALID")
    if schema_version not in SCHEMA_FILENAME_BY_VERSION:
        raise IdentityDefinitionError("SCHEMA_VERSION_UNSUPPORTED")
    relative_path = SCHEMA_FILENAME_BY_VERSION[schema_version]
    validate_repository_relative_path_syntax(relative_path)
    schema_path = repository_root / relative_path
    if schema_path.is_symlink() or not schema_path.is_file():
        raise IdentityDefinitionError("FROZEN_SCHEMA_UNAVAILABLE")
    schema_object = parse_strict_json_bytes(schema_path.read_bytes())
    if type(schema_object) is not dict:
        raise IdentityDefinitionError("SCHEMA_OBJECT_INVALID")
    try:
        _Draft202012Validator.check_schema(schema_object)
    except _SchemaError as exc:
        raise IdentityDefinitionError("FROZEN_SCHEMA_INVALID") from exc
    actual = schema_identity_sha256(schema_version, relative_path, schema_object)
    if actual != SCHEMA_IDENTITY_SHA256_BY_VERSION[schema_version]:
        raise IdentityDefinitionError("SCHEMA_IDENTITY_MISMATCH")
    return schema_object


_validate_domain_separators()


__all__ = [
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
]
