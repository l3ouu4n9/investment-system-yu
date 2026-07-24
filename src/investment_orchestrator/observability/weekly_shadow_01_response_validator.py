"""Deterministic untrusted analyst-response validation for WEEKLY-SHADOW-01.

The only public operation begins with one explicit R2F generation selector and
keeps every grounding-bearing intermediate inside one synchronous private call
stack.  Exact caller-supplied response bytes are parsed and validated in
memory.  This module performs no model call, publication, state transition,
permission, gate, portfolio, order, broker, or execution operation.
"""

from __future__ import annotations

del annotations

import base64 as _base64
from dataclasses import dataclass as _dataclass
import hashlib as _hashlib
import json as _json
import os as _os
from pathlib import Path as _Path
import stat as _stat
from types import MappingProxyType as _MappingProxyType
from typing import TYPE_CHECKING as _TYPE_CHECKING
import unicodedata as _unicodedata

from investment_orchestrator.observability import (
    weekly_shadow_01_package_builder as _package_builder,
)

if _TYPE_CHECKING:
    from os import PathLike
    from typing import Any, Mapping


_CONCRETE_PATH_TYPE = type(_Path())
_BUILDER_FAILURE_TYPE = _package_builder._PackageBuilderFailure
_ADAPTER_FAILURE_TYPE = _package_builder._ADAPTER_FAILURE_TYPE
_PACKAGE_TYPE = _package_builder._AnalystInputPackage
_RENDERED_PROMPT_TYPE = _package_builder._RenderedAnalystPrompt
_SURFACE_TYPE = _package_builder._ADAPTER_SURFACE_TYPE
_BUILD_PACKAGE_FROM_SOURCE_SELECTION = (
    _package_builder._build_package_from_source_selection
)
_RENDER_ANALYST_PROMPT = _package_builder._render_analyst_prompt
_REQUIRE_CONTRACT_SURFACE = _package_builder._require_contract_surface
_DEEP_THAW = _package_builder._deep_thaw

_RESPONSE_SCHEMA_VERSION = "weekly_shadow_01_analyst_response_v2"
_CAPTURE_SCHEMA_VERSION = "weekly_shadow_01_response_capture_v2"
_VALIDATION_SCHEMA_VERSION = "weekly_shadow_01_response_validation_v1"
_ANALYST_REPORT_SCHEMA_VERSION = "weekly_shadow_01_analyst_report_v1"
_RUN_SUMMARY_SCHEMA_VERSION = "weekly_shadow_01_run_summary_v1"
_VALIDATED_ANALYST_CONTENT_FIELDS = (
    "analyst_conclusion",
    "analyst_confidence",
    "analytical_sections",
    "analyst_limitation_codes",
)
_RESPONSE_SCHEMA_VERSIONS = (
    _RESPONSE_SCHEMA_VERSION,
    _CAPTURE_SCHEMA_VERSION,
    _VALIDATION_SCHEMA_VERSION,
)
_EXPECTED_SCHEMA_ROWS = (
    (
        _RESPONSE_SCHEMA_VERSION,
        "schemas/weekly_shadow_01_analyst_response.schema.json",
        "5bb79dfc46343581c5d6868ae88e8fbe9b7d78987de38182838aa0c37388fb03",
        "3625d86dd84ae1243ccb4992e339d0935dff646c87e74f7792ecd635956ca160",
        "a3a14276ec697ad4e806f6c6d16250b95f279ba4c13aee573bbd8263039ea546",
    ),
    (
        _CAPTURE_SCHEMA_VERSION,
        "schemas/weekly_shadow_01_response_capture.schema.json",
        "0d0fdddc5e6d8013c94b6344153f47c14ad221ed42f89bc79ec787886b6b0207",
        "a2f727e89e29f2a3ab9791d8274236f8481b2c30175eb07bc4d4bf458d429a95",
        "2ff319f61fd445458b9cb897e9a2db83265deb5c3ea93313a73572f39efab19b",
    ),
    (
        _VALIDATION_SCHEMA_VERSION,
        "schemas/weekly_shadow_01_response_validation.schema.json",
        "255e776cbdb025083948ebb171a4b876c599ae6423645faf044f04f7d38a59be",
        "2990ad8fc4f22de8b21691f54b3a967aed66e733078bd75ea74bc1330ee02f02",
        "3a41c1b6149aaa471d3dd94bd007b74cfcddbbdd97790bf00c7cdebd9b5000d5",
    ),
)
_EXPECTED_DOWNSTREAM_SCHEMA_ROWS = (
    *_EXPECTED_SCHEMA_ROWS,
    (
        _ANALYST_REPORT_SCHEMA_VERSION,
        "schemas/weekly_shadow_01_analyst_report.schema.json",
        "1791f934d59607a70df55c80df31d6cbc2e897c86879ab5bf6e24772167a3c53",
        "7b415fa8eb7cb4ecce92ddf06eb394574f7d1435dd840657396dd2eeb0f4feb8",
        "195112bf9087b1f63f680c93a77d41487e4bceae4564a621c55c15b6cb684014",
    ),
    (
        _RUN_SUMMARY_SCHEMA_VERSION,
        "schemas/weekly_shadow_01_run_summary.schema.json",
        "35fca249f89ecc5294f57daf3577d53158042c2b239163f8794e5d1ba15502b9",
        "114e92f0d151bba7266a651172cd7dac01f9652a4c6fe47557582b10dcf706a7",
        "88bc37d815c348fa0791c51fbdc660f2527c2d9975a01ab2bde2b9853c2a99b3",
    ),
)
_EXPECTED_COMPLETE_SCHEMA_VERSIONS = frozenset(
    {
        "weekly_shadow_01_analyst_input_v2",
        *(
            row[0]
            for row in _EXPECTED_DOWNSTREAM_SCHEMA_ROWS
        ),
    }
)
_EXPECTED_RESOURCE_BOUND_ROWS = (
    ("source_artifact_count", 4),
    ("source_artifact_max_bytes", 1_048_576),
    ("source_artifacts_total_max_bytes", 4_194_304),
    ("analyst_input_max_bytes", 524_288),
    ("rendered_prompt_max_bytes", 786_432),
    ("raw_response_max_bytes", 131_072),
    ("response_capture_max_bytes", 196_608),
    ("response_validation_max_bytes", 131_072),
    ("analyst_report_max_bytes", 262_144),
    ("run_summary_max_bytes", 65_536),
    ("max_nesting_depth", 16),
    ("max_object_members", 1_024),
    ("max_array_items", 1_024),
    ("max_evidence_records", 256),
    ("max_entries_per_analytical_section", 32),
    ("max_total_analytical_entries", 128),
    ("max_references_per_entry", 16),
    ("max_diagnostics", 256),
    ("max_text_code_points", 2_048),
    ("max_aggregate_analyst_text_code_points", 32_768),
)
_EXPECTED_NEGATIVE_AUTHORITY_ROWS = (
    ("authority_effect", "none"),
    ("permission_effect", "none"),
    ("approval_eligible", False),
    ("precompile_eligible", False),
    ("order_eligible", False),
    ("portfolio_effect", "none"),
    ("order_path_effect", "none"),
    ("execution_authority", False),
)
_EXPECTED_PROHIBITED_KEY_TERMS = (
    "buy",
    "sell",
    "new_buy",
    "trade",
    "order",
    "side",
    "quantity",
    "shares",
    "weight",
    "allocation",
    "budget",
    "cap",
    "rebalance",
    "exposure",
    "approve",
    "permission",
    "eligible",
    "compile",
    "submit",
    "execute",
    "broker",
    "hold",
    "no_trade",
    "blocked",
)
_EXPECTED_PROHIBITED_INTENT_TERMS = (
    "increase",
    "decrease",
    "add",
    "trim",
    "overweight",
    "underweight",
    "buy",
    "sell",
    "rebalance",
    "submit",
    "execute",
)
_EXPECTED_PROHIBITED_CONCLUSIONS = (
    "HOLD",
    "NO_TRADE",
    "BUY",
    "SELL",
    "NEW_BUY",
    "BLOCKED",
    "APPROVED",
    "REJECTED",
    "ELIGIBLE",
    "INELIGIBLE",
)
_EXPECTED_NORMALIZATION_STEPS = (
    "unicode_nfc_normalize",
    "casefold",
    "collapse_punctuation_and_separator_runs_to_single_space",
    "no_semantic_synonym_inference_beyond_the_frozen_vocabulary",
    "no_llm_driven_additions",
    "deterministic_bounded_output",
)
_EXPECTED_CATALOG_IDENTITY = (
    "36a0f850a089c3276c62dfe677ebfbce1ee9d1289e0487c3aad358db6cb556d4"
)
_EXPECTED_CONTRACT_MODULE_SHA256 = (
    "cc6659754275991a5d244aec8f26f725dc74d339be766cdf7694e97e6f19792a"
)
_CONTRACT_CATALOG_FIELD = b"contract_catalog_identity_sha256".decode("ascii")
_EXPECTED_RESOURCE_IDENTITY = (
    "acef986d2728660acce561f7c0d6a86fb0a942fa07ba8d3aea64bd061eee0e2e"
)
_EXPECTED_NEGATIVE_AUTHORITY_IDENTITY = (
    "b20ea7218880c5799897d7d3fbd74515af88ad6fcc9e2f4c1d4cc83649e61ff1"
)
_EXPECTED_PROHIBITED_KEY_IDENTITY = (
    "88247b4e04877b3925a988bce9185181e4d2c4214cf7e58a51b415907651dc9c"
)
_EXPECTED_PROHIBITED_INTENT_IDENTITY = (
    "5376a4e55d8bb6f1d79808355f5056e35e25a004869ba62ee6ff225f55f3b0ba"
)
_BLOCKING_REASON_CODES = frozenset(
    {
        "WS01_BR_SOURCE_GENERATION_INVALID",
        "WS01_BR_SOURCE_ARTIFACT_SET_MISMATCH",
        "WS01_BR_SOURCE_VERSION_UNSUPPORTED",
        "WS01_BR_SOURCE_READ_UNSTABLE",
        "WS01_BR_SOURCE_BINDING_MISMATCH",
        "WS01_BR_PACKAGE_CONSTRUCTION_FAILED",
        "WS01_BR_RESOURCE_BOUND_EXCEEDED",
        "WS01_BR_RESPONSE_MISSING",
        "WS01_BR_RESPONSE_UNREADABLE",
        "WS01_BR_RESPONSE_OVERSIZED",
        "WS01_BR_RESPONSE_PARSE_FAILED",
        "WS01_BR_RESPONSE_DUPLICATE_KEY",
        "WS01_BR_RESPONSE_SCHEMA_INVALID",
        "WS01_BR_RUN_BINDING_MISMATCH",
        "WS01_BR_PACKAGE_BINDING_MISMATCH",
        "WS01_BR_PROMPT_TEMPLATE_BINDING_MISMATCH",
        "WS01_BR_SOURCE_GENERATION_BINDING_MISMATCH",
        "WS01_BR_ARTIFACT_ECHO_INCOMPLETE",
        "WS01_BR_ARTIFACT_ECHO_UNEXPECTED",
        "WS01_BR_EVIDENCE_ECHO_INCOMPLETE",
        "WS01_BR_EVIDENCE_ECHO_UNEXPECTED",
        "WS01_BR_EVIDENCE_REFERENCE_INVALID",
        "WS01_BR_PROHIBITED_KEY",
        "WS01_BR_PROHIBITED_INTENT",
        "WS01_BR_CROSS_FIELD_INVALID",
        "WS01_BR_REPORT_CONSTRUCTION_FAILED",
        "WS01_BR_REPORT_IDENTITY_FAILURE",
        "WS01_BR_PUBLICATION_FAILED",
        "WS01_BR_PUBLICATION_CONFLICT",
        "WS01_BR_PUBLICATION_AMBIGUOUS",
        "WS01_BR_IMMUTABLE_VERIFICATION_FAILED",
        "WS01_BR_INTERNAL_INVARIANT_FAILURE",
    }
)
_ALLOWED_NEGATIVE_AUTHORITY_PATHS = frozenset(
    {
        ("negative_authority", "authority_effect"),
        ("negative_authority", "permission_effect"),
        ("negative_authority", "approval_eligible"),
        ("negative_authority", "precompile_eligible"),
        ("negative_authority", "order_eligible"),
        ("negative_authority", "portfolio_effect"),
        ("negative_authority", "order_path_effect"),
        ("negative_authority", "execution_authority"),
    }
)


class _WS01cFailure(RuntimeError):
    """Private deterministic reason-code carrier."""

    __slots__ = ("code",)

    def __init__(self, code: str) -> None:
        if code not in _BLOCKING_REASON_CODES:
            code = "WS01_BR_INTERNAL_INVARIANT_FAILURE"
        self.code = code
        super().__init__(code)


class _DuplicateJsonKey(ValueError):
    __slots__ = ()


class _NonFiniteJsonNumber(ValueError):
    __slots__ = ()


@_dataclass(frozen=True, slots=True, init=False)
class _WS01cResult:
    """Closed immutable WS01c success/failure envelope."""

    ok: bool
    value: object | None
    reason_code: str | None

    def __new__(cls, *_args: object, **_kwargs: object) -> "_WS01cResult":
        raise TypeError("WS01c results are created only by private factories")


@_dataclass(frozen=True, slots=True, init=False)
class _ValidatedAnalystResponse:
    """Immutable accepted response, capture, and validation records."""

    analyst_response: "Mapping[str, Any]"
    response_capture: "Mapping[str, Any]"
    response_capture_canonical_bytes: bytes
    response_capture_identity_sha256: str
    response_validation: "Mapping[str, Any]"
    response_validation_canonical_bytes: bytes
    response_validation_identity_sha256: str

    def __new__(
        cls, *_args: object, **_kwargs: object
    ) -> "_ValidatedAnalystResponse":
        raise TypeError("validated responses are created only by private factories")


@_dataclass(frozen=True, slots=True, init=False, repr=False)
class _AuthenticatedArtifactContract:
    """One frozen schema/semantic/identity contract for downstream use."""

    schema_version: str
    schema: "Mapping[str, Any]"
    schema_identity_sha256: str
    semantic_contract: "Mapping[str, Any]"
    semantic_contract_identity_sha256: str
    identity_domain: bytes
    maximum_canonical_bytes: int

    def __new__(
        cls, *_args: object, **_kwargs: object
    ) -> "_AuthenticatedArtifactContract":
        raise TypeError("artifact contracts are created only by private factories")


@_dataclass(frozen=True, slots=True, init=False, repr=False)
class _WS01cDownstreamContext:
    """Minimum immutable report inputs reconstructed for one future call."""

    # weekly_shadow_01_analyst_report_v1: run_id
    # weekly_shadow_01_run_summary_v1: run_id
    run_id: str
    # weekly_shadow_01_analyst_report_v1: input_package_identity_sha256
    input_package_identity_sha256: str
    # weekly_shadow_01_analyst_report_v1: response_capture_identity_sha256
    response_capture_identity_sha256: str
    # weekly_shadow_01_analyst_report_v1: validation_identity_sha256
    validation_identity_sha256: str
    # weekly_shadow_01_analyst_report_v1: validated_analyst_content
    validated_analyst_content: "Mapping[str, Any]"
    # Exact schema, semantic record, domain, and bound needed to construct and
    # validate weekly_shadow_01_analyst_report_v1.
    analyst_report_contract: _AuthenticatedArtifactContract
    # Exact schema, semantic record, domain, and bound needed to construct and
    # validate weekly_shadow_01_run_summary_v1.
    run_summary_contract: _AuthenticatedArtifactContract
    # Both output schemas: negative_authority_profile.
    negative_authority_profile: "Mapping[str, Any]"
    # Both semantic contracts: required_profile_identities_sha256.
    negative_authority_profile_identity_sha256: str

    def __new__(
        cls, *_args: object, **_kwargs: object
    ) -> "_WS01cDownstreamContext":
        raise TypeError("downstream contexts are created only by private factories")

    def __reduce__(self) -> object:
        raise TypeError("downstream contexts are not serializable")

    def __reduce_ex__(self, _protocol: int) -> object:
        raise TypeError("downstream contexts are not serializable")


@_dataclass(frozen=True, slots=True, init=False, repr=False)
class _WS01cCoreProjections:
    """Ephemeral public/downstream projections from one validation pipeline."""

    validated_analyst_response: _ValidatedAnalystResponse
    downstream_context: _WS01cDownstreamContext

    def __new__(
        cls, *_args: object, **_kwargs: object
    ) -> "_WS01cCoreProjections":
        raise TypeError("core projections are created only by private factories")


@_dataclass(frozen=True, slots=True, init=False, repr=False)
class _WS01cCoreResult:
    """Private reason-only envelope around one ephemeral core projection pair."""

    ok: bool
    value: _WS01cCoreProjections | None
    reason_code: str | None

    def __new__(
        cls, *_args: object, **_kwargs: object
    ) -> "_WS01cCoreResult":
        raise TypeError("core results are created only by private factories")


@_dataclass(frozen=True, slots=True, init=False)
class _WS01cDownstreamResult:
    """Closed private success/failure envelope for the future publisher."""

    ok: bool
    value: _WS01cDownstreamContext | None
    reason_code: str | None

    def __new__(
        cls, *_args: object, **_kwargs: object
    ) -> "_WS01cDownstreamResult":
        raise TypeError("downstream results are created only by private factories")


@_dataclass(frozen=True, slots=True, init=False)
class _AuthenticatedResponseContracts:
    """Response schemas and profiles bound to one sealed WS01 surface."""

    schemas: "Mapping[str, Any]"
    semantic_contracts: "Mapping[str, Any]"
    schema_identities: "Mapping[str, str]"
    semantic_contract_identities: "Mapping[str, str]"
    resource_profile: "Mapping[str, Any]"
    negative_authority: "Mapping[str, Any]"
    prohibited_key_terms: tuple[str, ...]
    prohibited_intent_terms: tuple[str, ...]
    prohibited_conclusions: tuple[str, ...]
    domains: "Mapping[str, bytes]"
    surface: "Mapping[str, Any]"
    surface_object: object

    def __new__(
        cls, *_args: object, **_kwargs: object
    ) -> "_AuthenticatedResponseContracts":
        raise TypeError("response contracts are created only by private factories")


@_dataclass(frozen=True, slots=True)
class _RegularFileWitness:
    device: int
    inode: int
    mode: int
    links: int
    size: int
    modified_ns: int
    changed_ns: int


class _DescriptorOwner:
    __slots__ = ("_descriptors",)

    def __init__(self) -> None:
        self._descriptors: list[int] = []

    def register(self, descriptor: int) -> int:
        self._descriptors.append(descriptor)
        return descriptor

    def close_all(self) -> bool:
        failed = False
        descriptors, self._descriptors = self._descriptors, []
        for descriptor in reversed(descriptors):
            try:
                _os.close(descriptor)
            except Exception:
                failed = True
        return failed


def _result_failure(reason_code: object) -> _WS01cResult:
    code = (
        reason_code
        if type(reason_code) is str and reason_code in _BLOCKING_REASON_CODES
        else "WS01_BR_INTERNAL_INVARIANT_FAILURE"
    )
    result = object.__new__(_WS01cResult)
    object.__setattr__(result, "ok", False)
    object.__setattr__(result, "value", None)
    object.__setattr__(result, "reason_code", code)
    return result


def _result_success(value: object) -> _WS01cResult:
    if type(value) is not _ValidatedAnalystResponse:
        return _result_failure("WS01_BR_INTERNAL_INVARIANT_FAILURE")
    result = object.__new__(_WS01cResult)
    object.__setattr__(result, "ok", True)
    object.__setattr__(result, "value", value)
    object.__setattr__(result, "reason_code", None)
    return result


def _core_result_failure(reason_code: object) -> _WS01cCoreResult:
    code = (
        reason_code
        if type(reason_code) is str and reason_code in _BLOCKING_REASON_CODES
        else "WS01_BR_INTERNAL_INVARIANT_FAILURE"
    )
    result = object.__new__(_WS01cCoreResult)
    object.__setattr__(result, "ok", False)
    object.__setattr__(result, "value", None)
    object.__setattr__(result, "reason_code", code)
    return result


def _core_result_success(value: object) -> _WS01cCoreResult:
    if type(value) is not _WS01cCoreProjections:
        return _core_result_failure("WS01_BR_INTERNAL_INVARIANT_FAILURE")
    result = object.__new__(_WS01cCoreResult)
    object.__setattr__(result, "ok", True)
    object.__setattr__(result, "value", value)
    object.__setattr__(result, "reason_code", None)
    return result


def _downstream_result_failure(reason_code: object) -> _WS01cDownstreamResult:
    code = (
        reason_code
        if type(reason_code) is str and reason_code in _BLOCKING_REASON_CODES
        else "WS01_BR_INTERNAL_INVARIANT_FAILURE"
    )
    result = object.__new__(_WS01cDownstreamResult)
    object.__setattr__(result, "ok", False)
    object.__setattr__(result, "value", None)
    object.__setattr__(result, "reason_code", code)
    return result


def _downstream_result_success(
    value: object,
) -> _WS01cDownstreamResult:
    if type(value) is not _WS01cDownstreamContext:
        return _downstream_result_failure("WS01_BR_INTERNAL_INVARIANT_FAILURE")
    result = object.__new__(_WS01cDownstreamResult)
    object.__setattr__(result, "ok", True)
    object.__setattr__(result, "value", value)
    object.__setattr__(result, "reason_code", None)
    return result


def validate_analyst_response(
    generation_id: str,
    *,
    raw_response_bytes: bytes,
    repository_root: "str | PathLike[str] | None" = None,
) -> _WS01cResult:
    """Validate exact untrusted response bytes against one rebuilt grounding run."""
    try:
        normalized_root = _repository_root(repository_root)
    except _WS01cFailure as failure:
        return _result_failure(failure.code)
    except Exception:
        return _result_failure("WS01_BR_INTERNAL_INVARIANT_FAILURE")
    core = _validate_analyst_response_core(
        generation_id,
        raw_response_bytes=raw_response_bytes,
        repository_root=normalized_root,
    )
    if not core.ok:
        return _result_failure(core.reason_code)
    projections = core.value
    if type(projections) is not _WS01cCoreProjections:
        return _result_failure("WS01_BR_INTERNAL_INVARIANT_FAILURE")
    return _result_success(projections.validated_analyst_response)


def _validate_analyst_response_for_downstream(
    generation_id: str,
    *,
    raw_response_bytes: bytes,
    repository_root: _Path,
) -> _WS01cDownstreamResult:
    """Reconstruct one private authenticated context from primitive inputs."""
    core = _validate_analyst_response_core(
        generation_id,
        raw_response_bytes=raw_response_bytes,
        repository_root=repository_root,
    )
    if not core.ok:
        return _downstream_result_failure(core.reason_code)
    projections = core.value
    if type(projections) is not _WS01cCoreProjections:
        return _downstream_result_failure("WS01_BR_INTERNAL_INVARIANT_FAILURE")
    return _downstream_result_success(projections.downstream_context)


def _validate_analyst_response_core(
    generation_id: str,
    *,
    raw_response_bytes: bytes,
    repository_root: _Path,
) -> _WS01cCoreResult:
    """Run one pipeline and retain both projections only on this call stack."""
    projections: _WS01cCoreProjections | None = None
    reason_code: str | None = None
    try:
        _require_normalized_repository_root(repository_root)
        projections = _validate_from_source_selection(
            generation_id,
            raw_response_bytes=raw_response_bytes,
            repository_root=repository_root,
        )
    except _ADAPTER_FAILURE_TYPE as failure:
        reason_code = failure.code
    except _BUILDER_FAILURE_TYPE as failure:
        reason_code = failure.code
    except _WS01cFailure as failure:
        reason_code = failure.code
    except Exception:
        reason_code = "WS01_BR_INTERNAL_INVARIANT_FAILURE"
    if reason_code is not None:
        return _core_result_failure(reason_code)
    if projections is None:
        return _core_result_failure("WS01_BR_INTERNAL_INVARIANT_FAILURE")
    return _core_result_success(projections)


def _validate_from_source_selection(
    generation_id: str,
    *,
    raw_response_bytes: bytes,
    repository_root: _Path,
) -> _WS01cCoreProjections:
    package = _BUILD_PACKAGE_FROM_SOURCE_SELECTION(
        generation_id,
        repository_root=repository_root,
    )
    if type(package) is not _PACKAGE_TYPE:
        _fail("WS01_BR_INTERNAL_INVARIANT_FAILURE")
    rendered = _RENDER_ANALYST_PROMPT(package)
    if type(rendered) is not _RENDERED_PROMPT_TYPE:
        _fail("WS01_BR_INTERNAL_INVARIANT_FAILURE")
    contracts = _authenticate_response_contracts(
        package,
        root=repository_root,
    )
    package_payload = package.to_dict()
    if type(package_payload) is not dict:
        _fail("WS01_BR_INTERNAL_INVARIANT_FAILURE")
    _validate_prompt_binding(
        package_payload=package_payload,
        package=package,
        rendered=rendered,
        contracts=contracts,
    )
    response = _parse_untrusted_response(
        raw_response_bytes,
        contracts=contracts,
    )
    _classify_echo_cardinality(
        response,
        package_payload=package_payload,
    )
    _validate_schema(
        contracts.schemas[_RESPONSE_SCHEMA_VERSION],
        response,
        failure_code="WS01_BR_RESPONSE_SCHEMA_INVALID",
    )
    _validate_response_bindings_and_semantics(
        response,
        package_payload=package_payload,
        contracts=contracts,
    )
    capture, capture_canonical = _build_response_capture(
        raw_response_bytes,
        package_payload=package_payload,
        contracts=contracts,
    )
    validation, validation_canonical = _build_response_validation(
        package_payload=package_payload,
        capture=capture,
        contracts=contracts,
    )
    validated_response = _new_validated_response(
        response=response,
        capture=capture,
        capture_canonical=capture_canonical,
        validation=validation,
        validation_canonical=validation_canonical,
    )
    downstream_context = _new_downstream_context(
        response=response,
        capture=capture,
        validation=validation,
        contracts=contracts,
    )
    projections = object.__new__(_WS01cCoreProjections)
    object.__setattr__(
        projections,
        "validated_analyst_response",
        validated_response,
    )
    object.__setattr__(
        projections,
        "downstream_context",
        downstream_context,
    )
    return projections


def _new_validated_response(
    *,
    response: dict[str, object],
    capture: dict[str, object],
    capture_canonical: bytes,
    validation: dict[str, object],
    validation_canonical: bytes,
) -> _ValidatedAnalystResponse:
    value = object.__new__(_ValidatedAnalystResponse)
    object.__setattr__(value, "analyst_response", _deep_freeze(response))
    object.__setattr__(value, "response_capture", _deep_freeze(capture))
    object.__setattr__(
        value,
        "response_capture_canonical_bytes",
        bytes(capture_canonical),
    )
    object.__setattr__(
        value,
        "response_capture_identity_sha256",
        capture["response_capture_identity_sha256"],
    )
    object.__setattr__(value, "response_validation", _deep_freeze(validation))
    object.__setattr__(
        value,
        "response_validation_canonical_bytes",
        bytes(validation_canonical),
    )
    object.__setattr__(
        value,
        "response_validation_identity_sha256",
        validation["validation_identity_sha256"],
    )
    return value


def _new_downstream_context(
    *,
    response: object,
    capture: object,
    validation: object,
    contracts: object,
) -> _WS01cDownstreamContext:
    if (
        type(response) is not dict
        or type(capture) is not dict
        or type(validation) is not dict
        or type(contracts) is not _AuthenticatedResponseContracts
    ):
        _fail("WS01_BR_INTERNAL_INVARIANT_FAILURE")
    run_id = response.get("run_id")
    package_identity = response.get("input_package_identity_sha256")
    capture_identity = capture.get("response_capture_identity_sha256")
    validation_identity = validation.get("validation_identity_sha256")
    if (
        type(run_id) is not str
        or type(package_identity) is not str
        or not _sha256_string(capture_identity)
        or not _sha256_string(validation_identity)
        or capture.get("run_id") != run_id
        or validation.get("run_id") != run_id
        or capture.get("input_package_identity_sha256") != package_identity
        or validation.get("input_package_identity_sha256") != package_identity
        or validation.get("response_capture_identity_sha256") != capture_identity
    ):
        _fail("WS01_BR_INTERNAL_INVARIANT_FAILURE")
    try:
        validated_analyst_content = {
            field: response[field]
            for field in _VALIDATED_ANALYST_CONTENT_FIELDS
        }
    except KeyError:
        _fail("WS01_BR_INTERNAL_INVARIANT_FAILURE")
    report_contract = _new_artifact_contract(
        contracts=contracts,
        schema_version=_ANALYST_REPORT_SCHEMA_VERSION,
        identity_domain_name="report",
        maximum_profile_name="analyst_report_max_bytes",
    )
    run_summary_contract = _new_artifact_contract(
        contracts=contracts,
        schema_version=_RUN_SUMMARY_SCHEMA_VERSION,
        identity_domain_name="run_summary",
        maximum_profile_name="run_summary_max_bytes",
    )
    context = object.__new__(_WS01cDownstreamContext)
    object.__setattr__(context, "run_id", run_id)
    object.__setattr__(
        context,
        "input_package_identity_sha256",
        package_identity,
    )
    object.__setattr__(
        context,
        "response_capture_identity_sha256",
        capture_identity,
    )
    object.__setattr__(
        context,
        "validation_identity_sha256",
        validation_identity,
    )
    object.__setattr__(
        context,
        "validated_analyst_content",
        _deep_freeze(validated_analyst_content),
    )
    object.__setattr__(
        context,
        "analyst_report_contract",
        report_contract,
    )
    object.__setattr__(
        context,
        "run_summary_contract",
        run_summary_contract,
    )
    object.__setattr__(
        context,
        "negative_authority_profile",
        contracts.negative_authority,
    )
    object.__setattr__(
        context,
        "negative_authority_profile_identity_sha256",
        _EXPECTED_NEGATIVE_AUTHORITY_IDENTITY,
    )
    return context


def _new_artifact_contract(
    *,
    contracts: _AuthenticatedResponseContracts,
    schema_version: str,
    identity_domain_name: str,
    maximum_profile_name: str,
) -> _AuthenticatedArtifactContract:
    schema = contracts.schemas.get(schema_version)
    semantic_contract = contracts.semantic_contracts.get(schema_version)
    schema_identity = contracts.schema_identities.get(schema_version)
    semantic_identity = contracts.semantic_contract_identities.get(schema_version)
    identity_domain = contracts.domains.get(identity_domain_name)
    maximum = contracts.resource_profile.get(maximum_profile_name)
    if (
        not isinstance(schema, _MappingProxyType)
        or not isinstance(semantic_contract, _MappingProxyType)
        or not _sha256_string(schema_identity)
        or not _sha256_string(semantic_identity)
        or type(identity_domain) is not bytes
        or not identity_domain.endswith(b"\0")
        or type(maximum) is not int
        or maximum <= 0
    ):
        _fail("WS01_BR_INTERNAL_INVARIANT_FAILURE")
    contract = object.__new__(_AuthenticatedArtifactContract)
    object.__setattr__(contract, "schema_version", schema_version)
    object.__setattr__(contract, "schema", schema)
    object.__setattr__(
        contract,
        "schema_identity_sha256",
        schema_identity,
    )
    object.__setattr__(
        contract,
        "semantic_contract",
        semantic_contract,
    )
    object.__setattr__(
        contract,
        "semantic_contract_identity_sha256",
        semantic_identity,
    )
    object.__setattr__(
        contract,
        "identity_domain",
        bytes(identity_domain),
    )
    object.__setattr__(contract, "maximum_canonical_bytes", maximum)
    return contract


def _repository_root(value: "str | PathLike[str] | None") -> _Path:
    if value is None:
        root = _Path(__file__).parents[3]
    else:
        try:
            root = _Path(value)
        except TypeError:
            _fail("WS01_BR_SOURCE_GENERATION_INVALID")
    if not root.is_absolute() or any(
        component in ("", ".", "..") for component in root.parts[1:]
    ):
        _fail("WS01_BR_SOURCE_GENERATION_INVALID")
    return root


def _require_normalized_repository_root(value: object) -> None:
    if (
        type(value) is not _CONCRETE_PATH_TYPE
        or not value.is_absolute()
        or any(component in ("", ".", "..") for component in value.parts[1:])
    ):
        _fail("WS01_BR_SOURCE_GENERATION_INVALID")


def _authenticate_response_contracts(
    package: object,
    *,
    root: _Path,
) -> _AuthenticatedResponseContracts:
    expected_surface_seal = (
        "f99f7a981fcbfa16524c5a9c505597f434dc1a64d9f50705a6a6cafb7ed88989"
    )
    if type(package) is not _PACKAGE_TYPE:
        _fail("WS01_BR_INTERNAL_INVARIANT_FAILURE")
    surface_object = package._authenticated_contract_surface
    if type(surface_object) is not _SURFACE_TYPE:
        _fail("WS01_BR_INTERNAL_INVARIANT_FAILURE")
    surface = _REQUIRE_CONTRACT_SURFACE(surface_object)
    complete = _DEEP_THAW(surface_object.complete_surface)
    if (
        type(surface) is not dict
        or type(complete) is not dict
        or surface_object.seal_sha256 != expected_surface_seal
        or package.authenticated_contract_surface_seal_sha256
        != expected_surface_seal
    ):
        _fail("WS01_BR_INTERNAL_INVARIANT_FAILURE")

    domains_hex = surface.get("domain_separators_hex")
    if type(domains_hex) is not dict:
        _fail("WS01_BR_INTERNAL_INVARIANT_FAILURE")
    expected_domain_rows = (
        ("response_capture", b"weekly_shadow_01_response_capture_v1\0"),
        ("validation", b"weekly_shadow_01_validation_v1\0"),
        ("report", b"weekly_shadow_01_report_v1\0"),
        ("run_summary", b"weekly_shadow_01_run_summary_v1\0"),
        ("schema_identity", b"weekly_shadow_01_schema_identity_v1\0"),
        (
            "semantic_contract_identity",
            b"weekly_shadow_01_semantic_contract_identity_v1\0",
        ),
        ("resource_bound_profile", b"weekly_shadow_01_resource_bound_profile_v1\0"),
        (
            "negative_authority_profile",
            b"weekly_shadow_01_negative_authority_profile_v1\0",
        ),
        ("vocabulary_profile", b"weekly_shadow_01_vocabulary_profile_v1\0"),
        ("prompt_template", b"weekly_shadow_01_prompt_template_v1\0"),
        ("contract_catalog", b"weekly_shadow_01_contract_catalog_v1\0"),
        ("prompt_render", b"weekly_shadow_01_prompt_render_v1\0"),
    )
    domains: dict[str, bytes] = {}
    for name, expected in expected_domain_rows:
        encoded = domains_hex.get(name)
        if type(encoded) is not str:
            _fail("WS01_BR_INTERNAL_INVARIANT_FAILURE")
        try:
            actual = bytes.fromhex(encoded)
        except ValueError:
            _fail("WS01_BR_INTERNAL_INVARIANT_FAILURE")
        if actual != expected:
            _fail("WS01_BR_INTERNAL_INVARIANT_FAILURE")
        domains[name] = actual

    resource_profile = _DEEP_THAW(surface.get("resource_bound_profile"))
    negative_authority = _DEEP_THAW(surface.get("negative_authority"))
    if (
        resource_profile != dict(_EXPECTED_RESOURCE_BOUND_ROWS)
        or negative_authority != dict(_EXPECTED_NEGATIVE_AUTHORITY_ROWS)
        or surface.get("resource_bound_profile_identity_sha256")
        != _EXPECTED_RESOURCE_IDENTITY
        or surface.get("negative_authority_profile_identity_sha256")
        != _EXPECTED_NEGATIVE_AUTHORITY_IDENTITY
        or surface.get(_CONTRACT_CATALOG_FIELD)
        != _EXPECTED_CATALOG_IDENTITY
        or surface_object.catalog_identity_sha256 != _EXPECTED_CATALOG_IDENTITY
    ):
        _fail("WS01_BR_INTERNAL_INVARIANT_FAILURE")

    profile_payloads = complete.get("profile_identity_payloads")
    profile_identities = complete.get("profile_identity_sha256")
    if type(profile_payloads) is not dict or type(profile_identities) is not dict:
        _fail("WS01_BR_INTERNAL_INVARIANT_FAILURE")
    profile_domains = (
        ("negative_authority", "negative_authority_profile"),
        ("resource_bound", "resource_bound_profile"),
        ("prohibited_key", "vocabulary_profile"),
        ("prohibited_intent", "vocabulary_profile"),
        ("prompt_template", "prompt_template"),
        ("run_status", "vocabulary_profile"),
        ("analyst_conclusion", "vocabulary_profile"),
        ("analyst_confidence", "vocabulary_profile"),
        ("blocking_reason", "vocabulary_profile"),
        ("analyst_limitation", "vocabulary_profile"),
    )
    for profile_name, domain_name in profile_domains:
        payload = profile_payloads.get(profile_name)
        expected_identity = profile_identities.get(profile_name)
        if (
            type(payload) is not dict
            or not _sha256_string(expected_identity)
            or _domain_identity(domains[domain_name], payload) != expected_identity
        ):
            _fail("WS01_BR_INTERNAL_INVARIANT_FAILURE")
    if (
        profile_identities.get("resource_bound") != _EXPECTED_RESOURCE_IDENTITY
        or profile_identities.get("negative_authority")
        != _EXPECTED_NEGATIVE_AUTHORITY_IDENTITY
        or profile_identities.get("prohibited_key")
        != _EXPECTED_PROHIBITED_KEY_IDENTITY
        or profile_identities.get("prohibited_intent")
        != _EXPECTED_PROHIBITED_INTENT_IDENTITY
    ):
        _fail("WS01_BR_INTERNAL_INVARIANT_FAILURE")
    prohibited_key_payload = profile_payloads["prohibited_key"]
    prohibited_intent_payload = profile_payloads["prohibited_intent"]
    conclusion_payload = profile_payloads["analyst_conclusion"]
    if (
        prohibited_key_payload.get("terms") != list(_EXPECTED_PROHIBITED_KEY_TERMS)
        or prohibited_intent_payload.get("terms")
        != list(_EXPECTED_PROHIBITED_INTENT_TERMS)
        or prohibited_key_payload.get("normalization_steps")
        != list(_EXPECTED_NORMALIZATION_STEPS)
        or prohibited_intent_payload.get("normalization_steps")
        != list(_EXPECTED_NORMALIZATION_STEPS)
        or conclusion_payload.get("prohibited_values")
        != list(_EXPECTED_PROHIBITED_CONCLUSIONS)
    ):
        _fail("WS01_BR_INTERNAL_INVARIANT_FAILURE")

    catalog_payload = complete.get("contract_catalog_payload")
    if (
        type(catalog_payload) is not dict
        or _domain_identity(domains["contract_catalog"], catalog_payload)
        != _EXPECTED_CATALOG_IDENTITY
        or complete.get(_CONTRACT_CATALOG_FIELD)
        != _EXPECTED_CATALOG_IDENTITY
    ):
        _fail("WS01_BR_INTERNAL_INVARIANT_FAILURE")

    raw_schemas, contract_source = _read_response_schemas_stably(
        root,
        maximum_bytes=resource_profile["source_artifact_max_bytes"],
    )
    if (
        _hashlib.sha256(contract_source).hexdigest()
        != _EXPECTED_CONTRACT_MODULE_SHA256
        or complete.get("contract_module_sha256")
        != _EXPECTED_CONTRACT_MODULE_SHA256
    ):
        _fail("WS01_BR_INTERNAL_INVARIANT_FAILURE")
    schema_files = complete.get("schema_filename_by_version")
    complete_raw_hashes = complete.get("schema_raw_sha256_by_version")
    complete_schema_identities = complete.get("schema_identity_sha256_by_version")
    runtime_schema_identities = surface.get("schema_identity_sha256_by_version")
    semantic_records = complete.get("semantic_contract_records")
    complete_semantic_identities = complete.get(
        "semantic_contract_identity_sha256_by_version"
    )
    runtime_semantic_identities = surface.get(
        "semantic_contract_identity_sha256_by_version"
    )
    if not all(
        type(item) is dict
        for item in (
            schema_files,
            complete_raw_hashes,
            complete_schema_identities,
            runtime_schema_identities,
            semantic_records,
            complete_semantic_identities,
            runtime_semantic_identities,
        )
    ):
        _fail("WS01_BR_INTERNAL_INVARIANT_FAILURE")
    if any(
        set(item) != _EXPECTED_COMPLETE_SCHEMA_VERSIONS
        for item in (
            schema_files,
            complete_raw_hashes,
            complete_schema_identities,
            runtime_schema_identities,
            semantic_records,
            complete_semantic_identities,
            runtime_semantic_identities,
        )
    ):
        _fail("WS01_BR_INTERNAL_INVARIANT_FAILURE")

    schemas: dict[str, dict[str, object]] = {}
    semantic_contracts: dict[str, dict[str, object]] = {}
    for (
        version,
        relative_path,
        expected_raw_sha256,
        expected_schema_identity,
        expected_semantic_identity,
    ) in _EXPECTED_DOWNSTREAM_SCHEMA_ROWS:
        raw = raw_schemas.get(version)
        if (
            type(raw) is not bytes
            or _hashlib.sha256(raw).hexdigest() != expected_raw_sha256
            or schema_files.get(version) != relative_path
            or complete_raw_hashes.get(version) != expected_raw_sha256
        ):
            _fail("WS01_BR_INTERNAL_INVARIANT_FAILURE")
        schema = _parse_schema_json(raw)
        schema_payload = {
            "schema_version": version,
            "schema_path": relative_path,
            "schema_id": schema.get("$id"),
            "schema": schema,
        }
        if (
            _domain_identity(domains["schema_identity"], schema_payload)
            != expected_schema_identity
            or complete_schema_identities.get(version)
            != expected_schema_identity
            or runtime_schema_identities.get(version)
            != expected_schema_identity
        ):
            _fail("WS01_BR_INTERNAL_INVARIANT_FAILURE")
        semantic_record = semantic_records.get(version)
        if (
            type(semantic_record) is not dict
            or semantic_record.get("schema_identity_sha256")
            != expected_schema_identity
            or _domain_identity(
                domains["semantic_contract_identity"],
                semantic_record,
            )
            != expected_semantic_identity
            or complete_semantic_identities.get(version)
            != expected_semantic_identity
            or runtime_semantic_identities.get(version)
            != expected_semantic_identity
        ):
            _fail("WS01_BR_INTERNAL_INVARIANT_FAILURE")
        _check_schema(schema)
        schemas[version] = schema
        semantic_contracts[version] = semantic_record

    contracts = object.__new__(_AuthenticatedResponseContracts)
    object.__setattr__(contracts, "schemas", _deep_freeze(schemas))
    object.__setattr__(
        contracts,
        "semantic_contracts",
        _deep_freeze(semantic_contracts),
    )
    object.__setattr__(
        contracts,
        "schema_identities",
        _deep_freeze(complete_schema_identities),
    )
    object.__setattr__(
        contracts,
        "semantic_contract_identities",
        _deep_freeze(complete_semantic_identities),
    )
    object.__setattr__(
        contracts,
        "resource_profile",
        _deep_freeze(resource_profile),
    )
    object.__setattr__(
        contracts,
        "negative_authority",
        _deep_freeze(negative_authority),
    )
    object.__setattr__(
        contracts,
        "prohibited_key_terms",
        tuple(prohibited_key_payload["terms"]),
    )
    object.__setattr__(
        contracts,
        "prohibited_intent_terms",
        tuple(prohibited_intent_payload["terms"]),
    )
    object.__setattr__(
        contracts,
        "prohibited_conclusions",
        tuple(conclusion_payload["prohibited_values"]),
    )
    object.__setattr__(contracts, "domains", _MappingProxyType(dict(domains)))
    object.__setattr__(contracts, "surface", _deep_freeze(surface))
    object.__setattr__(contracts, "surface_object", surface_object)
    return contracts


def _read_response_schemas_stably(
    root: _Path,
    *,
    maximum_bytes: int,
) -> tuple[dict[str, bytes], bytes]:
    if type(maximum_bytes) is not int or maximum_bytes <= 0:
        _fail("WS01_BR_INTERNAL_INVARIANT_FAILURE")
    required_primitives = ("O_CLOEXEC", "O_DIRECTORY", "O_NOFOLLOW")
    if any(not hasattr(_os, name) for name in required_primitives):
        _fail("WS01_BR_INTERNAL_INVARIANT_FAILURE")
    owner = _DescriptorOwner()
    result: tuple[dict[str, bytes], bytes] | None = None
    failure_code: str | None = None
    try:
        root_fd, chain = _open_absolute_directory_chain(root, owner=owner)
        schemas_fd = _open_directory_at(root_fd, "schemas", owner=owner)
        chain.append((root_fd, "schemas", schemas_fd))
        schemas_directory_identity = _directory_identity(_os.fstat(schemas_fd))
        first: dict[str, tuple[bytes, _RegularFileWitness]] = {}
        for version, relative_path, *_ in _EXPECTED_DOWNSTREAM_SCHEMA_ROWS:
            filename = relative_path.rsplit("/", 1)[-1]
            first[version] = _read_stable_regular_file_at(
                schemas_fd,
                filename,
                maximum_bytes=maximum_bytes,
            )
        source_fd = _open_directory_at(root_fd, "src", owner=owner)
        chain.append((root_fd, "src", source_fd))
        package_fd = _open_directory_at(
            source_fd,
            "investment_orchestrator",
            owner=owner,
        )
        chain.append((source_fd, "investment_orchestrator", package_fd))
        observability_fd = _open_directory_at(
            package_fd,
            "observability",
            owner=owner,
        )
        chain.append((package_fd, "observability", observability_fd))
        observability_directory_identity = _directory_identity(
            _os.fstat(observability_fd)
        )
        first_contract = _read_stable_regular_file_at(
            observability_fd,
            "weekly_shadow_01_contracts.py",
            maximum_bytes=maximum_bytes,
        )
        for version, relative_path, *_ in _EXPECTED_DOWNSTREAM_SCHEMA_ROWS:
            filename = relative_path.rsplit("/", 1)[-1]
            current_bytes, current_witness = _read_stable_regular_file_at(
                schemas_fd,
                filename,
                maximum_bytes=maximum_bytes,
            )
            accepted_bytes, accepted_witness = first[version]
            if (
                current_bytes != accepted_bytes
                or (current_witness.device, current_witness.inode)
                != (accepted_witness.device, accepted_witness.inode)
            ):
                _fail("WS01_BR_SOURCE_READ_UNSTABLE")
        current_contract_bytes, current_contract_witness = (
            _read_stable_regular_file_at(
                observability_fd,
                "weekly_shadow_01_contracts.py",
                maximum_bytes=maximum_bytes,
            )
        )
        accepted_contract_bytes, accepted_contract_witness = first_contract
        if (
            current_contract_bytes != accepted_contract_bytes
            or (
                current_contract_witness.device,
                current_contract_witness.inode,
            )
            != (
                accepted_contract_witness.device,
                accepted_contract_witness.inode,
            )
        ):
            _fail("WS01_BR_SOURCE_READ_UNSTABLE")
        if _directory_identity(_os.fstat(schemas_fd)) != schemas_directory_identity:
            _fail("WS01_BR_SOURCE_READ_UNSTABLE")
        if (
            _directory_identity(_os.fstat(observability_fd))
            != observability_directory_identity
        ):
            _fail("WS01_BR_SOURCE_READ_UNSTABLE")
        _verify_directory_chain(chain)
        result = (
            {version: value[0] for version, value in first.items()},
            accepted_contract_bytes,
        )
    except _WS01cFailure as failure:
        failure_code = failure.code
    except Exception:
        failure_code = "WS01_BR_INTERNAL_INVARIANT_FAILURE"
    finally:
        cleanup_failed = owner.close_all()
    if cleanup_failed:
        _fail("WS01_BR_SOURCE_READ_UNSTABLE")
    if failure_code is not None:
        _fail(failure_code)
    if result is None:
        _fail("WS01_BR_INTERNAL_INVARIANT_FAILURE")
    return result


def _open_absolute_directory_chain(
    root: _Path,
    *,
    owner: _DescriptorOwner,
) -> tuple[int, list[tuple[int, str, int]]]:
    flags = _os.O_RDONLY | _os.O_DIRECTORY | _os.O_CLOEXEC | _os.O_NOFOLLOW
    try:
        current = owner.register(_os.open(root.anchor, flags))
    except OSError:
        _fail("WS01_BR_INTERNAL_INVARIANT_FAILURE")
    chain: list[tuple[int, str, int]] = []
    for component in root.parts[1:]:
        child = _open_directory_at(current, component, owner=owner)
        chain.append((current, component, child))
        current = child
    return current, chain


def _open_directory_at(
    parent_fd: int,
    component: str,
    *,
    owner: _DescriptorOwner,
) -> int:
    if (
        type(component) is not str
        or component in ("", ".", "..")
        or "/" in component
        or "\\" in component
    ):
        _fail("WS01_BR_INTERNAL_INVARIANT_FAILURE")
    flags = _os.O_RDONLY | _os.O_DIRECTORY | _os.O_CLOEXEC | _os.O_NOFOLLOW
    try:
        descriptor = owner.register(
            _os.open(component, flags, dir_fd=parent_fd)
        )
        mode = _os.fstat(descriptor).st_mode
    except OSError:
        _fail("WS01_BR_INTERNAL_INVARIANT_FAILURE")
    if not _stat.S_ISDIR(mode):
        _fail("WS01_BR_INTERNAL_INVARIANT_FAILURE")
    return descriptor


def _read_stable_regular_file_at(
    directory_fd: int,
    filename: str,
    *,
    maximum_bytes: int,
) -> tuple[bytes, _RegularFileWitness]:
    if (
        type(filename) is not str
        or filename in ("", ".", "..")
        or "/" in filename
        or "\\" in filename
    ):
        _fail("WS01_BR_INTERNAL_INVARIANT_FAILURE")
    flags = _os.O_RDONLY | _os.O_CLOEXEC | _os.O_NOFOLLOW
    descriptor: int | None = None
    try:
        descriptor = _os.open(filename, flags, dir_fd=directory_fd)
        first_witness = _regular_file_witness(_os.fstat(descriptor))
        if first_witness.size > maximum_bytes:
            _fail("WS01_BR_INTERNAL_INVARIANT_FAILURE")
        first = _read_complete_descriptor(
            descriptor,
            expected_size=first_witness.size,
            maximum_bytes=maximum_bytes,
        )
        if _regular_file_witness(_os.fstat(descriptor)) != first_witness:
            _fail("WS01_BR_SOURCE_READ_UNSTABLE")
        _os.lseek(descriptor, 0, _os.SEEK_SET)
        second = _read_complete_descriptor(
            descriptor,
            expected_size=first_witness.size,
            maximum_bytes=maximum_bytes,
        )
        if (
            _regular_file_witness(_os.fstat(descriptor)) != first_witness
            or second != first
        ):
            _fail("WS01_BR_SOURCE_READ_UNSTABLE")
        return first, first_witness
    except _WS01cFailure:
        raise
    except OSError:
        _fail("WS01_BR_INTERNAL_INVARIANT_FAILURE")
    finally:
        if descriptor is not None:
            try:
                _os.close(descriptor)
            except OSError:
                _fail("WS01_BR_SOURCE_READ_UNSTABLE")


def _read_complete_descriptor(
    descriptor: int,
    *,
    expected_size: int,
    maximum_bytes: int,
) -> bytes:
    chunks: list[bytes] = []
    remaining = expected_size
    while remaining:
        chunk = _os.read(descriptor, min(65_536, remaining))
        if not chunk:
            _fail("WS01_BR_SOURCE_READ_UNSTABLE")
        chunks.append(chunk)
        remaining -= len(chunk)
    extra = _os.read(descriptor, 1)
    value = b"".join(chunks)
    if extra or len(value) != expected_size or len(value) > maximum_bytes:
        _fail("WS01_BR_SOURCE_READ_UNSTABLE")
    return value


def _regular_file_witness(value: _os.stat_result) -> _RegularFileWitness:
    if (
        not _stat.S_ISREG(value.st_mode)
        or value.st_nlink < 1
        or value.st_size < 0
    ):
        _fail("WS01_BR_INTERNAL_INVARIANT_FAILURE")
    return _RegularFileWitness(
        device=value.st_dev,
        inode=value.st_ino,
        mode=value.st_mode,
        links=value.st_nlink,
        size=value.st_size,
        modified_ns=value.st_mtime_ns,
        changed_ns=value.st_ctime_ns,
    )


def _directory_identity(value: _os.stat_result) -> tuple[int, int]:
    if not _stat.S_ISDIR(value.st_mode):
        _fail("WS01_BR_INTERNAL_INVARIANT_FAILURE")
    return value.st_dev, value.st_ino


def _verify_directory_chain(chain: list[tuple[int, str, int]]) -> None:
    for parent_fd, component, child_fd in chain:
        try:
            path_state = _os.stat(
                component,
                dir_fd=parent_fd,
                follow_symlinks=False,
            )
            child_state = _os.fstat(child_fd)
        except OSError:
            _fail("WS01_BR_SOURCE_READ_UNSTABLE")
        if (
            not _stat.S_ISDIR(path_state.st_mode)
            or (path_state.st_dev, path_state.st_ino)
            != (child_state.st_dev, child_state.st_ino)
        ):
            _fail("WS01_BR_SOURCE_READ_UNSTABLE")


def _parse_schema_json(raw: bytes) -> dict[str, object]:
    if raw.startswith(b"\xef\xbb\xbf"):
        _fail("WS01_BR_INTERNAL_INVARIANT_FAILURE")
    try:
        text = raw.decode("utf-8", errors="strict")
        value = _json.loads(
            text,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_nonfinite,
        )
    except (UnicodeDecodeError, _json.JSONDecodeError, _DuplicateJsonKey):
        _fail("WS01_BR_INTERNAL_INVARIANT_FAILURE")
    if type(value) is not dict:
        _fail("WS01_BR_INTERNAL_INVARIANT_FAILURE")
    return value


def _check_schema(schema: dict[str, object]) -> None:
    from jsonschema import Draft202012Validator as validator_type
    from jsonschema.exceptions import SchemaError

    try:
        validator_type.check_schema(schema)
    except SchemaError:
        _fail("WS01_BR_INTERNAL_INVARIANT_FAILURE")


def _parse_untrusted_response(
    raw: object,
    *,
    contracts: _AuthenticatedResponseContracts,
) -> dict[str, object]:
    if type(raw) is not bytes:
        _fail("WS01_BR_RESPONSE_UNREADABLE")
    maximum = contracts.resource_profile["raw_response_max_bytes"]
    if len(raw) == 0:
        _fail("WS01_BR_RESPONSE_MISSING")
    if len(raw) > maximum:
        _fail("WS01_BR_RESPONSE_OVERSIZED")
    if raw.startswith(b"\xef\xbb\xbf"):
        _fail("WS01_BR_RESPONSE_UNREADABLE")
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        _fail("WS01_BR_RESPONSE_UNREADABLE")
    try:
        value = _json.loads(
            text,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_nonfinite,
        )
    except _DuplicateJsonKey:
        _fail("WS01_BR_RESPONSE_DUPLICATE_KEY")
    except (_json.JSONDecodeError, _NonFiniteJsonNumber):
        _fail("WS01_BR_RESPONSE_PARSE_FAILED")
    if type(value) is not dict:
        _fail("WS01_BR_RESPONSE_SCHEMA_INVALID")
    _validate_response_resource_tree(value, contracts=contracts, depth=1)
    _scan_prohibited_content(value, contracts=contracts, path=())
    return value


def _reject_duplicate_keys(
    pairs: list[tuple[str, object]],
) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJsonKey
        result[key] = value
    return result


def _reject_nonfinite(_value: str) -> None:
    raise _NonFiniteJsonNumber


def _validate_response_resource_tree(
    value: object,
    *,
    contracts: _AuthenticatedResponseContracts,
    depth: int,
) -> None:
    profile = contracts.resource_profile
    if depth > profile["max_nesting_depth"]:
        _fail("WS01_BR_RESOURCE_BOUND_EXCEEDED")
    if type(value) is dict:
        if len(value) > profile["max_object_members"]:
            _fail("WS01_BR_RESOURCE_BOUND_EXCEEDED")
        for key, member in value.items():
            if type(key) is not str:
                _fail("WS01_BR_RESPONSE_SCHEMA_INVALID")
            _validate_text_value(key, maximum=profile["max_text_code_points"])
            _validate_response_resource_tree(
                member,
                contracts=contracts,
                depth=depth + 1,
            )
    elif type(value) is list:
        if len(value) > profile["max_array_items"]:
            _fail("WS01_BR_RESOURCE_BOUND_EXCEEDED")
        for member in value:
            _validate_response_resource_tree(
                member,
                contracts=contracts,
                depth=depth + 1,
            )
    elif type(value) is str:
        _validate_text_value(value, maximum=profile["max_text_code_points"])
    elif type(value) not in (int, bool, type(None)):
        _fail("WS01_BR_RESPONSE_SCHEMA_INVALID")


def _validate_text_value(value: str, *, maximum: int) -> None:
    if len(value) > maximum:
        _fail("WS01_BR_RESOURCE_BOUND_EXCEEDED")
    if any(
        0xD800 <= ord(character) <= 0xDFFF
        or _unicodedata.category(character) in {"Cc", "Cf"}
        for character in value
    ):
        _fail("WS01_BR_RESPONSE_SCHEMA_INVALID")


def _scan_prohibited_content(
    value: object,
    *,
    contracts: _AuthenticatedResponseContracts,
    path: tuple[str, ...],
) -> None:
    if type(value) is dict:
        for key, member in value.items():
            current_path = (*path, key)
            if (
                current_path not in _ALLOWED_NEGATIVE_AUTHORITY_PATHS
                and _contains_frozen_term(key, contracts.prohibited_key_terms)
            ):
                _fail("WS01_BR_PROHIBITED_KEY")
            _scan_prohibited_content(
                member,
                contracts=contracts,
                path=current_path,
            )
    elif type(value) is list:
        for member in value:
            _scan_prohibited_content(
                member,
                contracts=contracts,
                path=path,
            )
    elif type(value) is str and _is_free_text_path(path):
        intent_terms = (
            *contracts.prohibited_intent_terms,
            *contracts.prohibited_conclusions,
        )
        if _contains_frozen_term(value, intent_terms):
            _fail("WS01_BR_PROHIBITED_INTENT")


def _is_free_text_path(path: tuple[str, ...]) -> bool:
    return bool(path) and path[-1] == "statement"


def _normalize_prohibited_text(value: str) -> str:
    normalized = _unicodedata.normalize("NFC", value).casefold()
    result: list[str] = []
    pending_space = False
    for character in normalized:
        category = _unicodedata.category(character)
        if category.startswith("P") or category.startswith("Z"):
            pending_space = bool(result)
            continue
        if pending_space:
            result.append(" ")
            pending_space = False
        result.append(character)
    return "".join(result).strip()


def _contains_frozen_term(value: str, terms: tuple[str, ...]) -> bool:
    normalized = _normalize_prohibited_text(value)
    padded = f" {normalized} "
    return any(
        f" {_normalize_prohibited_text(term)} " in padded
        for term in terms
    )


def _validate_schema(
    schema: object,
    payload: dict[str, object],
    *,
    failure_code: str,
) -> None:
    from jsonschema import Draft202012Validator as validator_type
    from jsonschema.exceptions import SchemaError, ValidationError

    schema_value = _DEEP_THAW(schema)
    if type(schema_value) is not dict:
        _fail("WS01_BR_INTERNAL_INVARIANT_FAILURE")
    try:
        validator_type.check_schema(schema_value)
        validator_type(schema_value).validate(payload)
    except SchemaError:
        _fail("WS01_BR_INTERNAL_INVARIANT_FAILURE")
    except ValidationError:
        _fail(failure_code)


def _validate_prompt_binding(
    *,
    package_payload: dict[str, object],
    package: object,
    rendered: object,
    contracts: _AuthenticatedResponseContracts,
) -> None:
    if type(package) is not _PACKAGE_TYPE or type(rendered) is not _RENDERED_PROMPT_TYPE:
        _fail("WS01_BR_INTERNAL_INVARIANT_FAILURE")
    binding = _DEEP_THAW(rendered.binding)
    if type(binding) is not dict:
        _fail("WS01_BR_INTERNAL_INVARIANT_FAILURE")
    prompt_bytes = rendered.prompt_bytes
    if type(prompt_bytes) is not bytes:
        _fail("WS01_BR_INTERNAL_INVARIANT_FAILURE")
    prompt_sha256 = _hashlib.sha256(prompt_bytes).hexdigest()
    identity_payload = {
        "payload_kind": "weekly_shadow_01_prompt_render_v1",
        "input_package_identity_sha256": package_payload[
            "input_package_identity_sha256"
        ],
        "prompt_template_identity_sha256": package_payload[
            "prompt_template_identity_sha256"
        ],
        "rendered_prompt_byte_size": len(prompt_bytes),
        "rendered_prompt_sha256": prompt_sha256,
    }
    expected_render_identity = _domain_identity(
        contracts.domains["prompt_render"],
        identity_payload,
    )
    expected_binding = {
        **identity_payload,
        "prompt_render_identity_sha256": expected_render_identity,
        _CONTRACT_CATALOG_FIELD: package_payload[_CONTRACT_CATALOG_FIELD],
        "resource_bound_profile_identity_sha256": package_payload[
            "resource_bound_profile_identity_sha256"
        ],
        "authenticated_contract_surface_seal_sha256": (
            contracts.surface_object.seal_sha256
        ),
        "authority_effect": "none",
    }
    if (
        binding != expected_binding
        or rendered.prompt_render_identity_sha256 != expected_render_identity
        or package_payload["input_package_identity_sha256"]
        != package.input_package_identity_sha256
    ):
        _fail("WS01_BR_PROMPT_TEMPLATE_BINDING_MISMATCH")


def _classify_echo_cardinality(
    response: dict[str, object],
    *,
    package_payload: dict[str, object],
) -> None:
    expected_evidence = package_payload.get("evidence_records")
    if type(expected_evidence) is not list:
        _fail("WS01_BR_INTERNAL_INVARIANT_FAILURE")
    rows = (
        (
            "source_artifact_bindings",
            package_payload.get("source_artifact_bindings"),
            "source_id",
            "WS01_BR_ARTIFACT_ECHO_INCOMPLETE",
            "WS01_BR_ARTIFACT_ECHO_UNEXPECTED",
        ),
        (
            "evidence_record_bindings",
            expected_evidence,
            "evidence_record_id",
            "WS01_BR_EVIDENCE_ECHO_INCOMPLETE",
            "WS01_BR_EVIDENCE_ECHO_UNEXPECTED",
        ),
    )
    for field, expected, identity_field, incomplete_code, unexpected_code in rows:
        if type(expected) is not list:
            _fail("WS01_BR_INTERNAL_INVARIANT_FAILURE")
        if field not in response:
            _fail(incomplete_code)
        actual = response[field]
        if type(actual) is not list:
            continue
        if len(actual) < len(expected):
            _fail(incomplete_code)
        if len(actual) > len(expected):
            _fail(unexpected_code)
        if all(
            type(item) is dict and type(item.get(identity_field)) is str
            for item in actual
        ):
            actual_ids = [item[identity_field] for item in actual]
            expected_ids = [item[identity_field] for item in expected]
            if any(value not in actual_ids for value in expected_ids):
                _fail(incomplete_code)
            if any(value not in expected_ids for value in actual_ids):
                _fail(unexpected_code)
            if actual_ids != expected_ids:
                _fail("WS01_BR_CROSS_FIELD_INVALID")


def _validate_response_bindings_and_semantics(
    response: dict[str, object],
    *,
    package_payload: dict[str, object],
    contracts: _AuthenticatedResponseContracts,
) -> None:
    if response["run_id"] != package_payload["run_id"]:
        _fail("WS01_BR_RUN_BINDING_MISMATCH")
    if (
        response["input_package_identity_sha256"]
        != package_payload["input_package_identity_sha256"]
    ):
        _fail("WS01_BR_PACKAGE_BINDING_MISMATCH")
    if (
        response["prompt_template_identity_sha256"]
        != package_payload["prompt_template_identity_sha256"]
    ):
        _fail("WS01_BR_PROMPT_TEMPLATE_BINDING_MISMATCH")
    if response["source_generation_id"] != package_payload["source_generation_id"]:
        _fail("WS01_BR_SOURCE_GENERATION_BINDING_MISMATCH")
    if response["negative_authority"] != dict(_EXPECTED_NEGATIVE_AUTHORITY_ROWS):
        _fail("WS01_BR_CROSS_FIELD_INVALID")
    if (
        package_payload["negative_authority"]
        != dict(_EXPECTED_NEGATIVE_AUTHORITY_ROWS)
        or contracts.negative_authority
        != _deep_freeze(dict(_EXPECTED_NEGATIVE_AUTHORITY_ROWS))
    ):
        _fail("WS01_BR_INTERNAL_INVARIANT_FAILURE")
    if response["analyst_conclusion"] in package_payload["prohibited_conclusion_ids"]:
        _fail("WS01_BR_CROSS_FIELD_INVALID")
    if package_payload["permitted_question_ids"] != []:
        _fail("WS01_BR_CROSS_FIELD_INVALID")

    _validate_exact_echo(
        response["source_artifact_bindings"],
        package_payload["source_artifact_bindings"],
        identity_field="source_id",
        incomplete_code="WS01_BR_ARTIFACT_ECHO_INCOMPLETE",
        unexpected_code="WS01_BR_ARTIFACT_ECHO_UNEXPECTED",
    )
    package_evidence_bindings = [
        {
            "evidence_record_id": record["evidence_record_id"],
            "evidence_record_identity_sha256": record[
                "evidence_record_identity_sha256"
            ],
        }
        for record in package_payload["evidence_records"]
    ]
    _validate_exact_echo(
        response["evidence_record_bindings"],
        package_evidence_bindings,
        identity_field="evidence_record_id",
        incomplete_code="WS01_BR_EVIDENCE_ECHO_INCOMPLETE",
        unexpected_code="WS01_BR_EVIDENCE_ECHO_UNEXPECTED",
    )
    _validate_references(
        response,
        package_payload=package_payload,
        contracts=contracts,
    )


def _validate_exact_echo(
    actual: object,
    expected: object,
    *,
    identity_field: str,
    incomplete_code: str,
    unexpected_code: str,
) -> None:
    if type(actual) is not list or type(expected) is not list:
        _fail("WS01_BR_RESPONSE_SCHEMA_INVALID")
    actual_ids = [
        item.get(identity_field) if type(item) is dict else None for item in actual
    ]
    expected_ids = [
        item.get(identity_field) if type(item) is dict else None for item in expected
    ]
    if any(value not in actual_ids for value in expected_ids):
        _fail(incomplete_code)
    if any(value not in expected_ids for value in actual_ids):
        _fail(unexpected_code)
    if actual != expected:
        _fail("WS01_BR_CROSS_FIELD_INVALID")


def _validate_references(
    response: dict[str, object],
    *,
    package_payload: dict[str, object],
    contracts: _AuthenticatedResponseContracts,
) -> None:
    profile = contracts.resource_profile
    evidence_ids = {
        record["evidence_record_id"] for record in package_payload["evidence_records"]
    }
    diagnostic_ids = set(package_payload["availability_diagnostic_record_ids"]) | set(
        package_payload["freshness_diagnostic_record_ids"]
    )
    sections = response["analytical_sections"]
    if type(sections) is not dict:
        _fail("WS01_BR_RESPONSE_SCHEMA_INVALID")
    total_entries = 0
    seen_entry_ids: set[str] = set()
    aggregate_text = 0
    all_diagnostic_references: list[str] = []
    for section_name in (
        "observations",
        "risks_and_uncertainties",
        "missing_evidence_notes",
    ):
        entries = sections[section_name]
        if type(entries) is not list:
            _fail("WS01_BR_RESPONSE_SCHEMA_INVALID")
        if len(entries) > profile["max_entries_per_analytical_section"]:
            _fail("WS01_BR_RESOURCE_BOUND_EXCEEDED")
        total_entries += len(entries)
        for entry in entries:
            if type(entry) is not dict:
                _fail("WS01_BR_RESPONSE_SCHEMA_INVALID")
            entry_id = entry["entry_id"]
            if entry_id in seen_entry_ids:
                _fail("WS01_BR_CROSS_FIELD_INVALID")
            seen_entry_ids.add(entry_id)
            aggregate_text += len(entry["statement"])
            reference_field = (
                "diagnostic_ids"
                if section_name == "missing_evidence_notes"
                else "evidence_record_ids"
            )
            references = entry[reference_field]
            if len(references) > profile["max_references_per_entry"]:
                _fail("WS01_BR_RESOURCE_BOUND_EXCEEDED")
            permitted = (
                diagnostic_ids
                if reference_field == "diagnostic_ids"
                else evidence_ids
            )
            if any(reference not in permitted for reference in references):
                _fail("WS01_BR_EVIDENCE_REFERENCE_INVALID")
            if reference_field == "diagnostic_ids":
                all_diagnostic_references.extend(references)

    limitations = response["analyst_limitation_codes"]
    if type(limitations) is not list:
        _fail("WS01_BR_RESPONSE_SCHEMA_INVALID")
    total_entries += len(limitations)
    seen_limitation_codes: set[str] = set()
    for limitation in limitations:
        if type(limitation) is not dict:
            _fail("WS01_BR_RESPONSE_SCHEMA_INVALID")
        code = limitation["code"]
        if code in seen_limitation_codes:
            _fail("WS01_BR_CROSS_FIELD_INVALID")
        seen_limitation_codes.add(code)
        references = limitation["reference_ids"]
        if (
            len(references) > profile["max_references_per_entry"]
            or any(reference not in evidence_ids for reference in references)
        ):
            _fail("WS01_BR_EVIDENCE_REFERENCE_INVALID")
    if total_entries > profile["max_total_analytical_entries"]:
        _fail("WS01_BR_RESOURCE_BOUND_EXCEEDED")
    if aggregate_text > profile["max_aggregate_analyst_text_code_points"]:
        _fail("WS01_BR_RESOURCE_BOUND_EXCEEDED")
    if (
        len(all_diagnostic_references) > profile["max_diagnostics"]
        or len(set(all_diagnostic_references)) != len(all_diagnostic_references)
    ):
        _fail("WS01_BR_CROSS_FIELD_INVALID")


def _build_response_capture(
    raw: bytes,
    *,
    package_payload: dict[str, object],
    contracts: _AuthenticatedResponseContracts,
) -> tuple[dict[str, object], bytes]:
    payload: dict[str, object] = {
        "schema_version": _CAPTURE_SCHEMA_VERSION,
        "run_id": package_payload["run_id"],
        "input_package_identity_sha256": package_payload[
            "input_package_identity_sha256"
        ],
        "source_generation_id": package_payload["source_generation_id"],
        "raw_response_base64": _base64.b64encode(raw).decode("ascii"),
        "raw_response_sha256": _hashlib.sha256(raw).hexdigest(),
        "raw_response_byte_size": len(raw),
        "negative_authority_profile": dict(_EXPECTED_NEGATIVE_AUTHORITY_ROWS),
    }
    payload["response_capture_identity_sha256"] = _identity_excluding(
        contracts.domains["response_capture"],
        payload,
        "response_capture_identity_sha256",
    )
    canonical = _canonical_json_bytes(payload, contracts=contracts)
    if len(canonical) > contracts.resource_profile["response_capture_max_bytes"]:
        _fail("WS01_BR_RESOURCE_BOUND_EXCEEDED")
    _validate_schema(
        contracts.schemas[_CAPTURE_SCHEMA_VERSION],
        payload,
        failure_code="WS01_BR_INTERNAL_INVARIANT_FAILURE",
    )
    return payload, canonical


def _build_response_validation(
    *,
    package_payload: dict[str, object],
    capture: dict[str, object],
    contracts: _AuthenticatedResponseContracts,
) -> tuple[dict[str, object], bytes]:
    payload: dict[str, object] = {
        "schema_version": _VALIDATION_SCHEMA_VERSION,
        "run_id": package_payload["run_id"],
        "input_package_identity_sha256": package_payload[
            "input_package_identity_sha256"
        ],
        "response_capture_identity_sha256": capture[
            "response_capture_identity_sha256"
        ],
        "validation_status": "VALID",
        "blocking_reason_codes": [],
        "validator_diagnostics": [],
        "report_payload_constructible": True,
        "negative_authority_profile": dict(_EXPECTED_NEGATIVE_AUTHORITY_ROWS),
    }
    payload["validation_identity_sha256"] = _identity_excluding(
        contracts.domains["validation"],
        payload,
        "validation_identity_sha256",
    )
    canonical = _canonical_json_bytes(payload, contracts=contracts)
    if len(canonical) > contracts.resource_profile["response_validation_max_bytes"]:
        _fail("WS01_BR_RESOURCE_BOUND_EXCEEDED")
    _validate_schema(
        contracts.schemas[_VALIDATION_SCHEMA_VERSION],
        payload,
        failure_code="WS01_BR_INTERNAL_INVARIANT_FAILURE",
    )
    return payload, canonical


def _identity_excluding(
    domain: bytes,
    payload: dict[str, object],
    excluded_field: str,
) -> str:
    detached = {
        key: value for key, value in payload.items() if key != excluded_field
    }
    return _domain_identity(domain, detached)


def _domain_identity(domain: bytes, payload: dict[str, object]) -> str:
    if (
        type(domain) is not bytes
        or not domain.endswith(b"\0")
        or b"\0" in domain[:-1]
        or type(payload) is not dict
    ):
        _fail("WS01_BR_INTERNAL_INVARIANT_FAILURE")
    return _hashlib.sha256(domain + _canonical_contract_json_bytes(payload)).hexdigest()


def _canonical_json_bytes(
    value: object,
    *,
    contracts: _AuthenticatedResponseContracts,
) -> bytes:
    if type(contracts) is not _AuthenticatedResponseContracts:
        _fail("WS01_BR_INTERNAL_INVARIANT_FAILURE")
    return _canonical_contract_json_bytes(value)


def _canonical_contract_json_bytes(value: object) -> bytes:
    return _json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _deep_freeze(value: object) -> object:
    if type(value) is dict:
        return _MappingProxyType(
            {key: _deep_freeze(member) for key, member in value.items()}
        )
    if type(value) is list:
        return tuple(_deep_freeze(member) for member in value)
    return value


def _sha256_string(value: object) -> bool:
    return (
        type(value) is str
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _fail(code: str) -> None:
    raise _WS01cFailure(code)


__all__ = ("validate_analyst_response",)
