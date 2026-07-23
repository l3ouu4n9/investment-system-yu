"""Pure WEEKLY-SHADOW-01 analyst package and prompt construction (WS01b).

Each public operation begins from one explicit R2F generation selector.  The
module constructs verified intermediate values only within one synchronous
private call stack, then returns one closed package or rendered prompt outcome.
It performs no publication, model call, state transition, permission, gate,
portfolio, order, broker, or execution operation.
"""

from __future__ import annotations

del annotations

from collections.abc import Iterator as _Iterator, Mapping as _Mapping
from dataclasses import dataclass as _dataclass
import hashlib as _hashlib
import json as _json
from types import MappingProxyType as _MappingProxyType
from typing import TYPE_CHECKING as _TYPE_CHECKING

from investment_orchestrator.observability import (
    weekly_shadow_01_source_adapter as _source_adapter,
)

if _TYPE_CHECKING:
    from os import PathLike
    from typing import Any


_RECORD_CONTRACT_VERSION = "weekly_shadow_01_evidence_record_v2"
_LOCATOR_PAYLOAD_KIND = "weekly_shadow_01_evidence_record_locator_v2"
_RECORD_IDENTITY_PAYLOAD_KIND = "weekly_shadow_01_evidence_record_identity_v2"
_ANALYST_INPUT_SCHEMA_VERSION = "weekly_shadow_01_analyst_input_v2"
_PERMITTED_QUESTION_IDS: tuple[str, ...] = ()
_ADAPTER_FAILURE_TYPE = _source_adapter._SourceAdapterFailure
_ADAPTER_RESULT_TYPE = _source_adapter._WS01bResult
_ADAPTER_VERIFIED_GENERATION_TYPE = _source_adapter._VerifiedR2FGeneration
_ADAPTER_SNAPSHOT_TYPE = _source_adapter._VerifiedSourceSnapshot
_ADAPTER_SURFACE_TYPE = _source_adapter._AuthenticatedContractSurface
_VERIFY_R2F_V2_GENERATION = _source_adapter._verify_r2f_v2_generation
_BUILD_SOURCE_SNAPSHOT = _source_adapter._build_source_snapshot
_CONTRACT_CATALOG_FIELD = b"contract_catalog_identity_sha256".decode("ascii")
_CONSUMED_SOURCE_ARTIFACT_ROLES = (
    "replacement_input_manifest.json",
    "evidence_packet.json",
    "analyst_memo_prompt.txt",
    "render_generation_binding.json",
)
_AVAILABILITY_SUBJECTS = (
    "market_metrics",
    "scheduled_events_deterministic",
)
_EVIDENCE_VARIANT_RANKS = _MappingProxyType(
    {
        "active_anchor_v1": 0,
        "availability_status_v1": 1,
        "diagnostic_code_v1": 2,
    }
)
_RESOURCE_BOUND_PROFILE = _MappingProxyType(
    {
        "source_artifact_count": 4,
        "source_artifact_max_bytes": 1_048_576,
        "source_artifacts_total_max_bytes": 4_194_304,
        "analyst_input_max_bytes": 524_288,
        "rendered_prompt_max_bytes": 786_432,
        "raw_response_max_bytes": 131_072,
        "response_capture_max_bytes": 196_608,
        "response_validation_max_bytes": 131_072,
        "analyst_report_max_bytes": 262_144,
        "run_summary_max_bytes": 65_536,
        "max_nesting_depth": 16,
        "max_object_members": 1_024,
        "max_array_items": 1_024,
        "max_evidence_records": 256,
        "max_entries_per_analytical_section": 32,
        "max_total_analytical_entries": 128,
        "max_references_per_entry": 16,
        "max_diagnostics": 256,
        "max_text_code_points": 2_048,
        "max_aggregate_analyst_text_code_points": 32_768,
    }
)
_DOMAIN_SEPARATORS = _MappingProxyType(
    {
        "source_artifact": b"weekly_shadow_01_source_artifact_v1\0",
        "evidence_record": b"weekly_shadow_01_evidence_record_v1\0",
        "run": b"weekly_shadow_01_run_v1\0",
        "input_package": b"weekly_shadow_01_input_package_v1\0",
        "prompt_render": b"weekly_shadow_01_prompt_render_v1\0",
    }
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
        "WS01_BR_INTERNAL_INVARIANT_FAILURE",
    }
)
class _PackageBuilderFailure(RuntimeError):
    """Private reason-code carrier that never crosses a public boundary."""

    __slots__ = ("code",)

    def __init__(self, code: str) -> None:
        if code not in _BLOCKING_REASON_CODES:
            code = "WS01_BR_INTERNAL_INVARIANT_FAILURE"
        self.code = code
        super().__init__(code)


@_dataclass(frozen=True, slots=True, init=False)
class _WS01bResult:
    """Closed immutable WS01b success/failure envelope."""

    ok: bool
    value: object | None
    reason_code: str | None

    def __new__(cls, *_args: object, **_kwargs: object) -> "_WS01bResult":
        raise TypeError("WS01b results are created only by private factories")


def _result_failure(reason_code: object) -> _WS01bResult:
    code = (
        reason_code
        if type(reason_code) is str and reason_code in _BLOCKING_REASON_CODES
        else "WS01_BR_INTERNAL_INVARIANT_FAILURE"
    )
    result = object.__new__(_WS01bResult)
    object.__setattr__(result, "ok", False)
    object.__setattr__(result, "value", None)
    object.__setattr__(result, "reason_code", code)
    return result


def _result_success(value: object) -> _WS01bResult:
    if type(value) not in (_AnalystInputPackage, _RenderedAnalystPrompt):
        return _result_failure("WS01_BR_INTERNAL_INVARIANT_FAILURE")
    result = object.__new__(_WS01bResult)
    object.__setattr__(result, "ok", True)
    object.__setattr__(result, "value", value)
    object.__setattr__(result, "reason_code", None)
    return result


@_dataclass(frozen=True, slots=True, init=False)
class _AnalystInputPackage(_Mapping[str, object]):
    """Authenticated, deeply immutable analyst-input v2 package."""

    _payload: "Mapping[str, Any]"
    _schema: "Mapping[str, Any]"
    _authenticated_contract_surface: object
    authenticated_contract_surface_seal_sha256: str
    canonical_json_bytes: bytes
    input_package_identity_sha256: str

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        raise TypeError("private WS01b analyst-input package")

    @property
    def payload(self) -> "Mapping[str, Any]":
        return self._payload

    def __getitem__(self, key: str) -> object:
        return self._payload[key]

    def __iter__(self) -> _Iterator[str]:
        return iter(self._payload)

    def __len__(self) -> int:
        return len(self._payload)

    def to_dict(self) -> dict[str, object]:
        result = _deep_thaw(self._payload)
        if type(result) is not dict:
            _raise("WS01_BR_INTERNAL_INVARIANT_FAILURE")
        return result


@_dataclass(frozen=True, slots=True, init=False)
class _RenderedAnalystPrompt:
    """Immutable prompt bytes plus their deterministic in-memory binding."""

    prompt_bytes: bytes
    binding: "Mapping[str, Any]"
    prompt_render_identity_sha256: str

    def __init__(
        self,
        *_args: object,
        **_kwargs: object,
    ) -> None:
        raise TypeError("private WS01b rendered analyst prompt")


def _new_rendered_analyst_prompt(
    prompt_bytes: bytes,
    binding: "Mapping[str, Any]",
    prompt_render_identity_sha256: str,
) -> _RenderedAnalystPrompt:
    rendered = object.__new__(_RenderedAnalystPrompt)
    object.__setattr__(rendered, "prompt_bytes", bytes(prompt_bytes))
    object.__setattr__(rendered, "binding", _deep_freeze(_deep_thaw(binding)))
    object.__setattr__(
        rendered,
        "prompt_render_identity_sha256",
        prompt_render_identity_sha256,
    )
    return rendered


def _new_analyst_input_package(
    payload: dict[str, object],
    canonical_json_bytes: bytes,
    input_package_identity_sha256: str,
    schema: object,
    authenticated_surface: object,
) -> _AnalystInputPackage:
    _require_contract_surface(authenticated_surface)
    _require_analyst_input_schema_binding(schema, authenticated_surface)
    if (
        type(payload) is not dict
        or type(canonical_json_bytes) is not bytes
        or not _sha256_string(input_package_identity_sha256)
    ):
        _raise("WS01_BR_PACKAGE_CONSTRUCTION_FAILED")
    package = object.__new__(_AnalystInputPackage)
    object.__setattr__(package, "_payload", _deep_freeze(_deep_thaw(payload)))
    object.__setattr__(package, "_schema", schema)
    object.__setattr__(
        package,
        "_authenticated_contract_surface",
        authenticated_surface,
    )
    object.__setattr__(
        package,
        "authenticated_contract_surface_seal_sha256",
        authenticated_surface.seal_sha256,
    )
    object.__setattr__(package, "canonical_json_bytes", bytes(canonical_json_bytes))
    object.__setattr__(
        package,
        "input_package_identity_sha256",
        input_package_identity_sha256,
    )
    return package


def build_analyst_input_package(
    generation_id: str,
    *,
    repository_root: "str | PathLike[str] | None" = None,
) -> _WS01bResult:
    """Verify one source generation and build its inert analyst package."""
    result: _AnalystInputPackage | None = None
    reason_code: str | None = None
    try:
        result = _build_package_from_source_selection(
            generation_id,
            repository_root=repository_root,
        )
    except _ADAPTER_FAILURE_TYPE as failure:
        reason_code = failure.code
    except _PackageBuilderFailure as failure:
        reason_code = failure.code
    except Exception:
        reason_code = "WS01_BR_INTERNAL_INVARIANT_FAILURE"
    if reason_code is not None:
        return _result_failure(reason_code)
    if result is None:
        return _result_failure("WS01_BR_INTERNAL_INVARIANT_FAILURE")
    return _result_success(result)


def _build_package_from_source_selection(
    generation_id: str,
    *,
    repository_root: "str | PathLike[str] | None" = None,
) -> _AnalystInputPackage:
    """Run one private verify/project/package pipeline from a source selector."""
    verified_generation = _VERIFY_R2F_V2_GENERATION(
        generation_id,
        repository_root=repository_root,
    )
    if type(verified_generation) is not _ADAPTER_VERIFIED_GENERATION_TYPE:
        _raise("WS01_BR_INTERNAL_INVARIANT_FAILURE")
    snapshot = _BUILD_SOURCE_SNAPSHOT(verified_generation)
    if type(snapshot) is not _ADAPTER_SNAPSHOT_TYPE:
        _raise("WS01_BR_INTERNAL_INVARIANT_FAILURE")
    return _build_analyst_input_package(snapshot)


def _build_analyst_input_package(
    snapshot: object,
) -> _AnalystInputPackage:
    """Build one canonical, non-authoritative analyst-input v2 package."""
    try:
        authenticated_surface = _require_snapshot_contract(snapshot)
        _require_contract_surface(authenticated_surface)
        _require_snapshot_identity(snapshot)
        bindings = [binding.to_package_dict() for binding in snapshot.source_artifact_bindings]
        binding_by_role = _binding_map(bindings)
        records: list[dict[str, object]] = []

        for frozen_anchor in snapshot.active_anchors:
            anchor = _deep_thaw(frozen_anchor)
            if type(anchor) is not dict or set(anchor) != {"anchor_id", "normalized_value"}:
                _raise("WS01_BR_PACKAGE_CONSTRUCTION_FAILED")
            records.append(
                _build_evidence_record(
                    source_generation_id=snapshot.source_generation_id,
                    source_generation_version=snapshot.source_generation_version,
                    binding_by_role=binding_by_role,
                    value_type="active_anchor_v1",
                    source_locator={
                        "locator_type": "active_anchor_by_id",
                        "source_artifact_role": "evidence_packet.json",
                        "anchor_id": anchor["anchor_id"],
                    },
                    normalized_value=anchor["normalized_value"],
                )
            )

        for frozen_status in snapshot.availability_statuses:
            status = _deep_thaw(frozen_status)
            if type(status) is not dict or set(status) != {
                "availability_subject",
                "normalized_value",
            }:
                _raise("WS01_BR_PACKAGE_CONSTRUCTION_FAILED")
            records.append(
                _build_evidence_record(
                    source_generation_id=snapshot.source_generation_id,
                    source_generation_version=snapshot.source_generation_version,
                    binding_by_role=binding_by_role,
                    value_type="availability_status_v1",
                    source_locator={
                        "locator_type": "availability_status",
                        "source_artifact_role": "evidence_packet.json",
                        "availability_subject": status["availability_subject"],
                    },
                    normalized_value=status["normalized_value"],
                )
            )

        for code in snapshot.representation_diagnostics:
            if code != "EMPTY_ACTIVE_REGISTRY":
                _raise("WS01_BR_PACKAGE_CONSTRUCTION_FAILED")
            records.append(
                _build_evidence_record(
                    source_generation_id=snapshot.source_generation_id,
                    source_generation_version=snapshot.source_generation_version,
                    binding_by_role=binding_by_role,
                    value_type="diagnostic_code_v1",
                    source_locator={
                        "locator_type": "manifest_diagnostic",
                        "source_artifact_role": "replacement_input_manifest.json",
                        "diagnostic_code": code,
                    },
                    normalized_value=_NO_NORMALIZED_VALUE,
                )
            )

        ordered_records = _canonicalize_evidence_records(
            records,
            source_generation_id=snapshot.source_generation_id,
            source_generation_version=snapshot.source_generation_version,
            binding_by_role=binding_by_role,
            reject_noncanonical=False,
        )
        availability_ids = [
            record["evidence_record_id"]
            for record in ordered_records
            if record["value_type"] in {"availability_status_v1", "diagnostic_code_v1"}
        ]
        freshness_ids = [
            record["evidence_record_id"]
            for record in ordered_records
            if record["value_type"] == "active_anchor_v1"
        ]
        _validate_diagnostic_references(
            records=ordered_records,
            availability_ids=availability_ids,
            freshness_ids=freshness_ids,
        )

        frozen_fields = _frozen_package_fields(authenticated_surface)
        run_payload = {
            "payload_kind": "weekly_shadow_01_run_locator_v1",
            "adapter_id": frozen_fields["adapter_id"],
            "adapter_version": frozen_fields["adapter_version"],
            "source_generation_id": snapshot.source_generation_id,
            "source_generation_version": frozen_fields[
                "source_generation_version"
            ],
            "evaluation_timestamp_utc": snapshot.evaluation_timestamp_utc,
            _CONTRACT_CATALOG_FIELD: (
                frozen_fields[_CONTRACT_CATALOG_FIELD]
            ),
        }
        run_id = "ws01run-" + _ws01_identity("run", run_payload)
        payload: dict[str, object] = {
            "schema_version": frozen_fields["schema_version"],
            "run_id": run_id,
            "adapter_id": frozen_fields["adapter_id"],
            "adapter_version": frozen_fields["adapter_version"],
            "source_generation_id": snapshot.source_generation_id,
            "source_generation_version": frozen_fields[
                "source_generation_version"
            ],
            "evaluation_timestamp_utc": snapshot.evaluation_timestamp_utc,
            "source_artifact_bindings": bindings,
            "evidence_records": ordered_records,
            "availability_diagnostic_record_ids": availability_ids,
            "freshness_diagnostic_record_ids": freshness_ids,
            "permitted_question_ids": frozen_fields["permitted_question_ids"],
            "prohibited_conclusion_ids": frozen_fields[
                "prohibited_conclusion_ids"
            ],
            _CONTRACT_CATALOG_FIELD: (
                frozen_fields[_CONTRACT_CATALOG_FIELD]
            ),
            "resource_bound_profile_identity_sha256": (
                frozen_fields["resource_bound_profile_identity_sha256"]
            ),
            "prompt_template_identity_sha256": (
                frozen_fields["prompt_template_identity_sha256"]
            ),
            "negative_authority": frozen_fields["negative_authority"],
        }
        payload["input_package_identity_sha256"] = _compute_identity(
            "input_package", payload,
            exclude_fields=("input_package_identity_sha256",),
        )
        canonical = _canonical_json_bytes(payload)
        package = _new_analyst_input_package(
            payload,
            canonical,
            payload["input_package_identity_sha256"],
            snapshot.analyst_input_schema,
            authenticated_surface,
        )
        _validate_package(
            package,
            schema=snapshot.analyst_input_schema,
            authenticated_surface=authenticated_surface,
        )
        return package
    except _PackageBuilderFailure:
        raise
    except (
        AssertionError,
        AttributeError,
        KeyError,
        RecursionError,
        TypeError,
        ValueError,
    ):
        raise


def render_analyst_prompt(
    generation_id: str,
    *,
    repository_root: "str | PathLike[str] | None" = None,
) -> _WS01bResult:
    """Verify, package, and render one source generation in one private pipeline."""
    result: _RenderedAnalystPrompt | None = None
    reason_code: str | None = None
    try:
        package = _build_package_from_source_selection(
            generation_id,
            repository_root=repository_root,
        )
        result = _render_analyst_prompt(package)
    except _ADAPTER_FAILURE_TYPE as failure:
        reason_code = failure.code
    except _PackageBuilderFailure as failure:
        reason_code = failure.code
    except Exception:
        reason_code = "WS01_BR_INTERNAL_INVARIANT_FAILURE"
    if reason_code is not None:
        return _result_failure(reason_code)
    if result is None:
        return _result_failure("WS01_BR_INTERNAL_INVARIANT_FAILURE")
    return _result_success(result)


def _render_analyst_prompt(package: _AnalystInputPackage) -> _RenderedAnalystPrompt:
    """Render the frozen prompt and canonical package exactly once in memory."""
    if type(package) is not _AnalystInputPackage:
        _raise("WS01_BR_PACKAGE_CONSTRUCTION_FAILED")
    try:
        authenticated_surface = package._authenticated_contract_surface
        surface = _require_contract_surface(authenticated_surface)
        payload = _validate_package(
            package,
            schema=package._schema,
            authenticated_surface=authenticated_surface,
        )
        template = surface["prompt_template_text"].encode("utf-8")
        placeholder = surface["prompt_template_placeholder"].encode("utf-8")
        if (
            template.count(placeholder) != 1
            or template.startswith(b"\xef\xbb\xbf")
            or b"\r" in template
            or not template.endswith(b"\n")
        ):
            _raise("WS01_BR_INTERNAL_INVARIANT_FAILURE")
        rendered = template.replace(placeholder, package.canonical_json_bytes)
        if (
            rendered.startswith(b"\xef\xbb\xbf")
            or b"\r" in rendered
            or not rendered.endswith(b"\n")
            or rendered.endswith(b"\n\n")
        ):
            _raise("WS01_BR_PACKAGE_CONSTRUCTION_FAILED")
        try:
            rendered.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            _raise("WS01_BR_PACKAGE_CONSTRUCTION_FAILED")
        if len(rendered) > _RESOURCE_BOUND_PROFILE["rendered_prompt_max_bytes"]:
            _raise("WS01_BR_RESOURCE_BOUND_EXCEEDED")
        rendered_sha256 = _hashlib.sha256(rendered).hexdigest()
        identity_payload = {
            "payload_kind": "weekly_shadow_01_prompt_render_v1",
            "input_package_identity_sha256": payload[
                "input_package_identity_sha256"
            ],
            "prompt_template_identity_sha256": surface[
                "prompt_template_identity_sha256"
            ],
            "rendered_prompt_byte_size": len(rendered),
            "rendered_prompt_sha256": rendered_sha256,
        }
        render_identity = _compute_identity(
            "prompt_render", identity_payload
        )
        binding = {
            **identity_payload,
            "prompt_render_identity_sha256": render_identity,
            _CONTRACT_CATALOG_FIELD: surface[
                _CONTRACT_CATALOG_FIELD
            ],
            "resource_bound_profile_identity_sha256": surface[
                "resource_bound_profile_identity_sha256"
            ],
            "authenticated_contract_surface_seal_sha256": (
                authenticated_surface.seal_sha256
            ),
            "authority_effect": "none",
        }
        return _new_rendered_analyst_prompt(rendered, binding, render_identity)
    except _PackageBuilderFailure:
        raise
    except (
        KeyError,
        RecursionError,
        TypeError,
        ValueError,
    ):
        raise


class _NoNormalizedValue:
    __slots__ = ()


_NO_NORMALIZED_VALUE = _NoNormalizedValue()


def _build_evidence_record(
    *,
    source_generation_id: str,
    source_generation_version: str,
    binding_by_role: dict[str, dict[str, str]],
    value_type: str,
    source_locator: dict[str, object],
    normalized_value: object,
) -> dict[str, object]:
    _validate_closed_variant(
        value_type=value_type,
        source_locator=source_locator,
        normalized_value=normalized_value,
    )
    role = source_locator["source_artifact_role"]
    if type(role) is not str or role not in binding_by_role:
        _raise("WS01_BR_SOURCE_BINDING_MISMATCH")
    resolved = binding_by_role[role]
    locator_payload = {
        "payload_kind": _LOCATOR_PAYLOAD_KIND,
        "record_contract_version": _RECORD_CONTRACT_VERSION,
        "source_generation_id": source_generation_id,
        "source_generation_version": source_generation_version,
        "resolved_source_artifact_binding": dict(resolved),
        "value_type": value_type,
        "source_locator": _deep_thaw(source_locator),
    }
    record_id = "ws01ev-" + _compute_identity(
        "evidence_record", locator_payload
    )
    record: dict[str, object] = {
        "evidence_record_id": record_id,
        "value_type": value_type,
        "source_locator": _deep_thaw(source_locator),
        "authority_effect": "none",
    }
    if normalized_value is not _NO_NORMALIZED_VALUE:
        record["normalized_value"] = _deep_thaw(normalized_value)
    record_identity_payload = {
        "payload_kind": _RECORD_IDENTITY_PAYLOAD_KIND,
        "source_generation_id": source_generation_id,
        "source_generation_version": source_generation_version,
        "resolved_source_artifact_binding": dict(resolved),
        "evidence_record": dict(record),
    }
    record["evidence_record_identity_sha256"] = _compute_identity(
        "evidence_record", record_identity_payload
    )
    return record


def _validate_closed_variant(
    *,
    value_type: str,
    source_locator: dict[str, object],
    normalized_value: object,
) -> None:
    if value_type == "active_anchor_v1":
        if (
            set(source_locator)
            != {"locator_type", "source_artifact_role", "anchor_id"}
            or source_locator.get("locator_type") != "active_anchor_by_id"
            or source_locator.get("source_artifact_role") != "evidence_packet.json"
            or not _bounded_nonempty_text(source_locator.get("anchor_id"))
            or type(normalized_value) is not dict
            or set(normalized_value)
            != {
                "applicable_tickers",
                "anchor_date_et",
                "valid_from",
                "valid_until",
                "confidence_floor",
                "summary",
                "validation",
            }
        ):
            _raise("WS01_BR_PACKAGE_CONSTRUCTION_FAILED")
    elif value_type == "availability_status_v1":
        if (
            set(source_locator)
            != {"locator_type", "source_artifact_role", "availability_subject"}
            or source_locator.get("locator_type") != "availability_status"
            or source_locator.get("source_artifact_role") != "evidence_packet.json"
            or source_locator.get("availability_subject")
            not in _AVAILABILITY_SUBJECTS
            or type(normalized_value) is not dict
            or set(normalized_value) != {"available", "data_gap"}
        ):
            _raise("WS01_BR_PACKAGE_CONSTRUCTION_FAILED")
    elif value_type == "diagnostic_code_v1":
        if (
            set(source_locator)
            != {"locator_type", "source_artifact_role", "diagnostic_code"}
            or source_locator.get("locator_type") != "manifest_diagnostic"
            or source_locator.get("source_artifact_role")
            != "replacement_input_manifest.json"
            or source_locator.get("diagnostic_code") != "EMPTY_ACTIVE_REGISTRY"
            or normalized_value is not _NO_NORMALIZED_VALUE
        ):
            _raise("WS01_BR_PACKAGE_CONSTRUCTION_FAILED")
    else:
        _raise("WS01_BR_PACKAGE_CONSTRUCTION_FAILED")


def _binding_map(bindings: list[dict[str, str]]) -> dict[str, dict[str, str]]:
    if len(bindings) != _RESOURCE_BOUND_PROFILE["source_artifact_count"]:
        _raise("WS01_BR_SOURCE_BINDING_MISMATCH")
    if [item.get("source_id") for item in bindings] != list(
        _CONSUMED_SOURCE_ARTIFACT_ROLES
    ):
        _raise("WS01_BR_SOURCE_BINDING_MISMATCH")
    result: dict[str, dict[str, str]] = {}
    for binding in bindings:
        if (
            type(binding) is not dict
            or set(binding) != {
                "source_id",
                "source_artifact_identity_sha256",
            }
            or not _sha256_string(binding.get("source_artifact_identity_sha256"))
            or binding["source_id"] in result
        ):
            _raise("WS01_BR_SOURCE_BINDING_MISMATCH")
        result[binding["source_id"]] = dict(binding)
    return result


def _canonicalize_evidence_records(
    records: list[dict[str, object]],
    *,
    source_generation_id: str,
    source_generation_version: str,
    binding_by_role: dict[str, dict[str, str]],
    reject_noncanonical: bool,
) -> list[dict[str, object]]:
    maximum = _RESOURCE_BOUND_PROFILE["max_evidence_records"]
    if len(records) > maximum:
        _raise("WS01_BR_RESOURCE_BOUND_EXCEEDED")
    logical_locators: set[bytes] = set()
    record_ids: set[str] = set()
    ordering_keys: set[tuple[int, bytes, str]] = set()
    active_ids: set[str] = set()
    availability_subjects: set[str] = set()
    manifest_diagnostics: set[str] = set()
    keys: list[tuple[int, bytes, str]] = []
    for record in records:
        _validate_record_identity(
            record,
            source_generation_id=source_generation_id,
            source_generation_version=source_generation_version,
            binding_by_role=binding_by_role,
        )
        locator = record["source_locator"]
        role = locator["source_artifact_role"]
        resolved = binding_by_role[role]
        logical_payload = {
            "value_type": record["value_type"],
            "source_locator": locator,
            "package_source_generation_context": {
                "source_generation_id": source_generation_id,
                "source_generation_version": source_generation_version,
            },
            "resolved_source_artifact_binding": resolved,
        }
        logical = _canonical_json_bytes(logical_payload)
        record_id = record["evidence_record_id"]
        if logical in logical_locators or record_id in record_ids:
            _raise("WS01_BR_PACKAGE_CONSTRUCTION_FAILED")
        logical_locators.add(logical)
        record_ids.add(record_id)
        value_type = record["value_type"]
        if value_type == "active_anchor_v1":
            unique_value = locator["anchor_id"]
            if unique_value in active_ids:
                _raise("WS01_BR_PACKAGE_CONSTRUCTION_FAILED")
            active_ids.add(unique_value)
        elif value_type == "availability_status_v1":
            unique_value = locator["availability_subject"]
            if unique_value in availability_subjects:
                _raise("WS01_BR_PACKAGE_CONSTRUCTION_FAILED")
            availability_subjects.add(unique_value)
        else:
            unique_value = locator["diagnostic_code"]
            if unique_value in manifest_diagnostics:
                _raise("WS01_BR_PACKAGE_CONSTRUCTION_FAILED")
            manifest_diagnostics.add(unique_value)
        key = (
            _EVIDENCE_VARIANT_RANKS[value_type],
            _canonical_json_bytes(locator),
            record_id,
        )
        if key in ordering_keys:
            _raise("WS01_BR_PACKAGE_CONSTRUCTION_FAILED")
        ordering_keys.add(key)
        keys.append(key)
    if len(logical_locators) > maximum:
        _raise("WS01_BR_RESOURCE_BOUND_EXCEEDED")
    sorted_pairs = sorted(zip(keys, records), key=lambda pair: pair[0])
    ordered = [record for _, record in sorted_pairs]
    ordered_keys = [key for key, _ in sorted_pairs]
    if any(left >= right for left, right in zip(ordered_keys, ordered_keys[1:])):
        _raise("WS01_BR_PACKAGE_CONSTRUCTION_FAILED")
    if reject_noncanonical and records != ordered:
        _raise("WS01_BR_PACKAGE_CONSTRUCTION_FAILED")
    availability_order = [
        record["source_locator"]["availability_subject"]
        for record in ordered
        if record["value_type"] == "availability_status_v1"
    ]
    expected_availability_order = [
        value
        for value in _AVAILABILITY_SUBJECTS
        if value in availability_subjects
    ]
    if availability_order != expected_availability_order:
        _raise("WS01_BR_PACKAGE_CONSTRUCTION_FAILED")
    return ordered


def _validate_record_identity(
    record: dict[str, object],
    *,
    source_generation_id: str,
    source_generation_version: str,
    binding_by_role: dict[str, dict[str, str]],
) -> None:
    if type(record) is not dict:
        _raise("WS01_BR_PACKAGE_CONSTRUCTION_FAILED")
    value_type = record.get("value_type")
    locator = record.get("source_locator")
    has_value = "normalized_value" in record
    normalized = record.get("normalized_value", _NO_NORMALIZED_VALUE)
    expected_fields = {
        "evidence_record_id",
        "evidence_record_identity_sha256",
        "value_type",
        "source_locator",
        "authority_effect",
    }
    if has_value:
        expected_fields.add("normalized_value")
    if set(record) != expected_fields or type(locator) is not dict:
        _raise("WS01_BR_PACKAGE_CONSTRUCTION_FAILED")
    _validate_closed_variant(
        value_type=value_type,
        source_locator=locator,
        normalized_value=normalized,
    )
    role = locator["source_artifact_role"]
    if role not in binding_by_role or record.get("authority_effect") != "none":
        _raise("WS01_BR_PACKAGE_CONSTRUCTION_FAILED")
    rebuilt = _build_evidence_record(
        source_generation_id=source_generation_id,
        source_generation_version=source_generation_version,
        binding_by_role=binding_by_role,
        value_type=value_type,
        source_locator=locator,
        normalized_value=normalized,
    )
    if rebuilt != record:
        _raise("WS01_BR_PACKAGE_CONSTRUCTION_FAILED")


def _validate_diagnostic_references(
    *,
    records: list[dict[str, object]],
    availability_ids: list[str],
    freshness_ids: list[str],
) -> None:
    maximum = _RESOURCE_BOUND_PROFILE["max_diagnostics"]
    if (
        len(availability_ids) != len(set(availability_ids))
        or len(freshness_ids) != len(set(freshness_ids))
        or set(availability_ids) & set(freshness_ids)
        or len(set(availability_ids) | set(freshness_ids)) > maximum
    ):
        _raise("WS01_BR_RESOURCE_BOUND_EXCEEDED")
    by_id = {record["evidence_record_id"]: record for record in records}
    if len(by_id) != len(records):
        _raise("WS01_BR_PACKAGE_CONSTRUCTION_FAILED")
    for record_id in availability_ids:
        if (
            record_id not in by_id
            or by_id[record_id]["value_type"]
            not in {"availability_status_v1", "diagnostic_code_v1"}
        ):
            _raise("WS01_BR_PACKAGE_CONSTRUCTION_FAILED")
    for record_id in freshness_ids:
        if (
            record_id not in by_id
            or by_id[record_id]["value_type"] != "active_anchor_v1"
        ):
            _raise("WS01_BR_PACKAGE_CONSTRUCTION_FAILED")
    expected_availability = [
        record["evidence_record_id"]
        for record in records
        if record["value_type"] in {"availability_status_v1", "diagnostic_code_v1"}
    ]
    expected_freshness = [
        record["evidence_record_id"]
        for record in records
        if record["value_type"] == "active_anchor_v1"
    ]
    if availability_ids != expected_availability or freshness_ids != expected_freshness:
        _raise("WS01_BR_PACKAGE_CONSTRUCTION_FAILED")


def _validate_package(
    package: _AnalystInputPackage,
    *,
    schema: object,
    authenticated_surface: object,
) -> dict[str, object]:
    if (
        type(package) is not _AnalystInputPackage
        or package._authenticated_contract_surface is not authenticated_surface
    ):
        _raise("WS01_BR_PACKAGE_CONSTRUCTION_FAILED")
    _require_contract_surface(authenticated_surface)
    if (
        package.authenticated_contract_surface_seal_sha256
        != authenticated_surface.seal_sha256
    ):
        _raise("WS01_BR_PACKAGE_CONSTRUCTION_FAILED")
    _require_analyst_input_schema_binding(schema, authenticated_surface)
    payload = package.to_dict()
    if type(payload) is not dict:
        _raise("WS01_BR_PACKAGE_CONSTRUCTION_FAILED")
    _require_frozen_package_bindings(payload, authenticated_surface)
    bindings = payload.get("source_artifact_bindings")
    records = payload.get("evidence_records")
    if type(bindings) is not list or type(records) is not list:
        _raise("WS01_BR_PACKAGE_CONSTRUCTION_FAILED")
    run_payload = {
        "payload_kind": "weekly_shadow_01_run_locator_v1",
        "adapter_id": payload.get("adapter_id"),
        "adapter_version": payload.get("adapter_version"),
        "source_generation_id": payload.get("source_generation_id"),
        "source_generation_version": payload.get("source_generation_version"),
        "evaluation_timestamp_utc": payload.get("evaluation_timestamp_utc"),
        _CONTRACT_CATALOG_FIELD: payload[
            _CONTRACT_CATALOG_FIELD
        ],
    }
    if payload.get("run_id") != "ws01run-" + _compute_identity("run", run_payload):
        _raise("WS01_BR_PACKAGE_CONSTRUCTION_FAILED")
    binding_by_role = _binding_map(bindings)
    ordered = _canonicalize_evidence_records(
        records,
        source_generation_id=payload.get("source_generation_id"),
        source_generation_version=payload.get("source_generation_version"),
        binding_by_role=binding_by_role,
        reject_noncanonical=True,
    )
    if ordered != records:
        _raise("WS01_BR_PACKAGE_CONSTRUCTION_FAILED")
    availability_ids = payload.get("availability_diagnostic_record_ids")
    freshness_ids = payload.get("freshness_diagnostic_record_ids")
    if type(availability_ids) is not list or type(freshness_ids) is not list:
        _raise("WS01_BR_PACKAGE_CONSTRUCTION_FAILED")
    _validate_diagnostic_references(
        records=records,
        availability_ids=availability_ids,
        freshness_ids=freshness_ids,
    )
    if payload.get("input_package_identity_sha256") != _compute_identity(
        "input_package",
        payload,
        exclude_fields=("input_package_identity_sha256",),
    ):
        _raise("WS01_BR_PACKAGE_CONSTRUCTION_FAILED")
    canonical = _canonical_json_bytes(payload)
    if (
        canonical != package.canonical_json_bytes
        or payload.get("input_package_identity_sha256")
        != package.input_package_identity_sha256
    ):
        _raise("WS01_BR_PACKAGE_CONSTRUCTION_FAILED")
    _validate_runtime_resource_bounds(payload, canonical=canonical)
    schema_dict = _deep_thaw(schema)
    if type(schema_dict) is not dict:
        _raise("WS01_BR_INTERNAL_INVARIANT_FAILURE")
    _validate_against_schema(schema_dict, payload)
    return payload


def _validate_against_schema(
    schema: dict[str, object], payload: dict[str, object]
) -> None:
    from jsonschema import Draft202012Validator as validator_type
    from jsonschema.exceptions import SchemaError, ValidationError

    try:
        validator_type.check_schema(schema)
        validator_type(schema).validate(payload)
    except (SchemaError, ValidationError):
        _raise("WS01_BR_PACKAGE_CONSTRUCTION_FAILED")


def _validate_runtime_resource_bounds(
    payload: dict[str, object], *, canonical: bytes
) -> None:
    profile = _RESOURCE_BOUND_PROFILE
    if len(canonical) > profile["analyst_input_max_bytes"]:
        _raise("WS01_BR_RESOURCE_BOUND_EXCEEDED")
    _validate_json_resource_tree(payload, depth=1)
    records = payload["evidence_records"]
    if len(records) > profile["max_evidence_records"]:
        _raise("WS01_BR_RESOURCE_BOUND_EXCEEDED")
    analyst_text = 0
    for record in records:
        locator = record["source_locator"]
        if record["value_type"] == "active_anchor_v1":
            analyst_text += len(locator["anchor_id"])
        if "normalized_value" in record:
            analyst_text += _source_text_code_points(record["normalized_value"])
    if analyst_text > profile["max_aggregate_analyst_text_code_points"]:
        _raise("WS01_BR_RESOURCE_BOUND_EXCEEDED")


def _validate_json_resource_tree(value: object, *, depth: int) -> None:
    profile = _RESOURCE_BOUND_PROFILE
    if depth > profile["max_nesting_depth"]:
        _raise("WS01_BR_RESOURCE_BOUND_EXCEEDED")
    if type(value) is dict:
        if len(value) > profile["max_object_members"]:
            _raise("WS01_BR_RESOURCE_BOUND_EXCEEDED")
        for item in value.values():
            _validate_json_resource_tree(item, depth=depth + 1)
    elif type(value) is list:
        if len(value) > profile["max_array_items"]:
            _raise("WS01_BR_RESOURCE_BOUND_EXCEEDED")
        for item in value:
            _validate_json_resource_tree(item, depth=depth + 1)
    elif type(value) is str:
        if any(0xD800 <= ord(character) <= 0xDFFF for character in value):
            _raise("WS01_BR_PACKAGE_CONSTRUCTION_FAILED")
    elif type(value) not in (int, bool, type(None)):
        _raise("WS01_BR_PACKAGE_CONSTRUCTION_FAILED")


def _source_text_code_points(value: object) -> int:
    if type(value) is str:
        return len(value)
    if type(value) is dict:
        return sum(_source_text_code_points(item) for item in value.values())
    if type(value) is list:
        return sum(_source_text_code_points(item) for item in value)
    return 0


def _ws01_identity(domain_name: str, payload: dict[str, object]) -> str:
    return _compute_identity(domain_name, payload)


def _compute_identity(
    domain_name: str,
    payload: dict[str, object],
    *,
    exclude_fields: tuple[str, ...] = (),
) -> str:
    try:
        domain = _DOMAIN_SEPARATORS[domain_name]
    except KeyError:
        _raise("WS01_BR_INTERNAL_INVARIANT_FAILURE")
    if type(payload) is not dict or type(domain) is not bytes:
        _raise("WS01_BR_INTERNAL_INVARIANT_FAILURE")
    excluded = set(exclude_fields)
    detached = {key: value for key, value in payload.items() if key not in excluded}
    return _hashlib.sha256(domain + _canonical_json_bytes(detached)).hexdigest()


def _canonical_json_bytes(value: object) -> bytes:
    _validate_json_resource_tree(value, depth=1)
    return _json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _require_contract_surface(value: object) -> dict[str, object]:
    expected_surface_seal = (
        "f99f7a981fcbfa16524c5a9c505597f434dc1a64d9f50705a6a6cafb7ed88989"
    )
    if type(value) is not _ADAPTER_SURFACE_TYPE:
        _raise("WS01_BR_PACKAGE_CONSTRUCTION_FAILED")
    complete_surface_value = value.complete_surface
    runtime_surface_value = value.runtime_surface
    if (
        not _deeply_immutable_contract_value(complete_surface_value)
        or not _deeply_immutable_contract_value(runtime_surface_value)
        or not _sha256_string(value.catalog_identity_sha256)
        or not _sha256_string(value.seal_sha256)
    ):
        _raise("WS01_BR_INTERNAL_INVARIANT_FAILURE")
    complete_surface = _deep_thaw(complete_surface_value)
    surface = _deep_thaw(runtime_surface_value)
    if type(surface) is not dict or type(complete_surface) is not dict:
        _raise("WS01_BR_INTERNAL_INVARIANT_FAILURE")
    actual_surface_seal = _hashlib.sha256(
        b"weekly_shadow_01_authenticated_contract_surface_v1\0"
        + _canonical_contract_json_bytes(complete_surface)
    ).hexdigest()
    runtime_surface_sha256 = _hashlib.sha256(
        _canonical_contract_json_bytes(surface)
    ).hexdigest()
    if (
        value.seal_sha256 != expected_surface_seal
        or value.seal_sha256 != actual_surface_seal
        or complete_surface.get("runtime_surface_sha256")
        != runtime_surface_sha256
    ):
        _raise("WS01_BR_INTERNAL_INVARIANT_FAILURE")
    prompt_text = surface.get("prompt_template_text")
    placeholder = surface.get("prompt_template_placeholder")
    domains = surface.get("domain_separators_hex")
    complete_prompt = complete_surface.get("prompt_template")
    complete_adapter = complete_surface.get("adapter")
    schema_files = complete_surface.get("schema_filename_by_version")
    schema_identities = complete_surface.get("schema_identity_sha256_by_version")
    if (
        surface.get(_CONTRACT_CATALOG_FIELD)
        != value.catalog_identity_sha256
        or surface.get("consumed_source_artifact_roles")
        != list(_CONSUMED_SOURCE_ARTIFACT_ROLES)
        or surface.get("availability_subjects") != list(_AVAILABILITY_SUBJECTS)
        or surface.get("evidence_variant_ranks") != dict(_EVIDENCE_VARIANT_RANKS)
        or type(domains) is not dict
        or any(domains.get(name) != domain.hex() for name, domain in _DOMAIN_SEPARATORS.items())
        or type(complete_adapter) is not dict
        or set(complete_adapter) != {"adapter_id", "adapter_version"}
        or complete_adapter.get("adapter_id") != surface.get("adapter_id")
        or complete_surface.get("source_generation_version")
        != surface.get("source_generation_version")
        or type(schema_files) is not dict
        or _ANALYST_INPUT_SCHEMA_VERSION not in schema_files
        or type(schema_identities) is not dict
        or _ANALYST_INPUT_SCHEMA_VERSION not in schema_identities
        or type(prompt_text) is not str
        or type(placeholder) is not str
        or prompt_text.count(placeholder) != 1
        or _hashlib.sha256(prompt_text.encode("utf-8")).hexdigest()
        != surface.get("prompt_template_raw_sha256")
        or type(complete_prompt) is not dict
        or complete_prompt.get("text") != prompt_text
        or complete_prompt.get("raw_sha256")
        != surface.get("prompt_template_raw_sha256")
        or complete_prompt.get("identity_sha256")
        != surface.get("prompt_template_identity_sha256")
        or complete_prompt.get("placeholder") != placeholder
    ):
        _raise("WS01_BR_INTERNAL_INVARIANT_FAILURE")
    return surface


def _require_analyst_input_schema_binding(
    schema: object, authenticated_surface: object
) -> None:
    surface = _require_contract_surface(authenticated_surface)
    complete_surface = _deep_thaw(authenticated_surface.complete_surface)
    schema_dict = _deep_thaw(schema)
    if type(schema_dict) is not dict or type(complete_surface) is not dict:
        _raise("WS01_BR_INTERNAL_INVARIANT_FAILURE")
    schema_files = complete_surface.get("schema_filename_by_version")
    schema_identities = complete_surface.get("schema_identity_sha256_by_version")
    domains = surface.get("domain_separators_hex")
    if (
        type(schema_files) is not dict
        or type(schema_identities) is not dict
        or type(domains) is not dict
    ):
        _raise("WS01_BR_INTERNAL_INVARIANT_FAILURE")
    relative_path = schema_files.get(_ANALYST_INPUT_SCHEMA_VERSION)
    expected_identity = schema_identities.get(_ANALYST_INPUT_SCHEMA_VERSION)
    domain_hex = domains.get("schema_identity")
    if (
        type(relative_path) is not str
        or not _sha256_string(expected_identity)
        or type(domain_hex) is not str
    ):
        _raise("WS01_BR_INTERNAL_INVARIANT_FAILURE")
    try:
        domain = bytes.fromhex(domain_hex)
    except ValueError:
        _raise("WS01_BR_INTERNAL_INVARIANT_FAILURE")
    identity_payload = {
        "schema_version": _ANALYST_INPUT_SCHEMA_VERSION,
        "schema_path": relative_path,
        "schema_id": schema_dict.get("$id"),
        "schema": schema_dict,
    }
    actual_identity = _hashlib.sha256(
        domain + _canonical_contract_json_bytes(identity_payload)
    ).hexdigest()
    if (
        actual_identity != expected_identity
        or surface["schema_identity_sha256_by_version"].get(
            _ANALYST_INPUT_SCHEMA_VERSION
        )
        != expected_identity
    ):
        _raise("WS01_BR_INTERNAL_INVARIANT_FAILURE")


def _frozen_package_fields(authenticated_surface: object) -> dict[str, object]:
    surface = _require_contract_surface(authenticated_surface)
    complete_surface = _deep_thaw(authenticated_surface.complete_surface)
    complete_adapter = complete_surface.get("adapter")
    if type(complete_adapter) is not dict:
        _raise("WS01_BR_INTERNAL_INVARIANT_FAILURE")
    fields = {
        "schema_version": _ANALYST_INPUT_SCHEMA_VERSION,
        "adapter_id": surface.get("adapter_id"),
        "adapter_version": complete_adapter.get("adapter_version"),
        "source_generation_version": surface.get("source_generation_version"),
        _CONTRACT_CATALOG_FIELD: surface.get(
            _CONTRACT_CATALOG_FIELD
        ),
        "resource_bound_profile_identity_sha256": surface.get(
            "resource_bound_profile_identity_sha256"
        ),
        "prompt_template_identity_sha256": surface.get(
            "prompt_template_identity_sha256"
        ),
        "negative_authority": _deep_thaw(surface.get("negative_authority")),
        "permitted_question_ids": list(_PERMITTED_QUESTION_IDS),
        "prohibited_conclusion_ids": _deep_thaw(
            surface.get("prohibited_conclusion_ids")
        ),
    }
    if (
        not _bounded_nonempty_text(fields["adapter_id"])
        or not _bounded_nonempty_text(fields["adapter_version"])
        or not _bounded_nonempty_text(fields["source_generation_version"])
        or not _sha256_string(
            fields[_CONTRACT_CATALOG_FIELD]
        )
        or not _sha256_string(fields["resource_bound_profile_identity_sha256"])
        or not _sha256_string(fields["prompt_template_identity_sha256"])
        or type(fields["negative_authority"]) is not dict
        or type(fields["prohibited_conclusion_ids"]) is not list
    ):
        _raise("WS01_BR_INTERNAL_INVARIANT_FAILURE")
    return fields


def _require_frozen_package_bindings(
    payload: dict[str, object], authenticated_surface: object
) -> None:
    expected = _frozen_package_fields(authenticated_surface)
    if any(payload.get(name) != value for name, value in expected.items()):
        _raise("WS01_BR_PACKAGE_CONSTRUCTION_FAILED")


def _require_snapshot_identity(snapshot: object) -> None:
    payload = {
        "payload_kind": "weekly_shadow_01_verified_source_snapshot_v1",
        "adapter_id": snapshot.adapter_id,
        "adapter_version": snapshot.adapter_version,
        "source_generation_id": snapshot.source_generation_id,
        "source_generation_version": snapshot.source_generation_version,
        "evaluation_timestamp_utc": snapshot.evaluation_timestamp_utc,
        "source_artifact_bindings": [
            binding.to_package_dict() for binding in snapshot.source_artifact_bindings
        ],
        "active_anchors": _deep_thaw(snapshot.active_anchors),
        "availability_statuses": _deep_thaw(snapshot.availability_statuses),
        "representation_diagnostics": list(snapshot.representation_diagnostics),
        _CONTRACT_CATALOG_FIELD: (
            snapshot.contract_catalog_identity_sha256
        ),
        "contract_surface": _deep_thaw(snapshot.contract_surface),
    }
    expected = _hashlib.sha256(
        _DOMAIN_SEPARATORS["source_artifact"] + _canonical_json_bytes(payload)
    ).hexdigest()
    if expected != snapshot.snapshot_identity_sha256:
        _raise("WS01_BR_PACKAGE_CONSTRUCTION_FAILED")


def _require_snapshot_contract(snapshot: object) -> object:
    if type(snapshot) is not _ADAPTER_SNAPSHOT_TYPE:
        _raise("WS01_BR_PACKAGE_CONSTRUCTION_FAILED")
    authenticated_surface = snapshot.authenticated_contract_surface
    surface = _require_contract_surface(authenticated_surface)
    frozen_fields = _frozen_package_fields(authenticated_surface)
    if (
        snapshot.contract_surface is not authenticated_surface.runtime_surface
        or snapshot.adapter_id != frozen_fields["adapter_id"]
        or snapshot.adapter_version != frozen_fields["adapter_version"]
        or snapshot.source_generation_version
        != frozen_fields["source_generation_version"]
        or not _sha256_string(snapshot.source_generation_id)
        or snapshot.contract_catalog_identity_sha256
        != frozen_fields[_CONTRACT_CATALOG_FIELD]
        or tuple(item.source_id for item in snapshot.source_artifact_bindings)
        != _CONSUMED_SOURCE_ARTIFACT_ROLES
        or tuple(
            item["availability_subject"] for item in snapshot.availability_statuses
        )
        != _AVAILABILITY_SUBJECTS
        or any(
            not _sha256_string(item.source_artifact_identity_sha256)
            for item in snapshot.source_artifact_bindings
        )
        or any(
            code != "EMPTY_ACTIVE_REGISTRY"
            for code in snapshot.representation_diagnostics
        )
        or surface[_CONTRACT_CATALOG_FIELD]
        != snapshot.contract_catalog_identity_sha256
    ):
        _raise("WS01_BR_PACKAGE_CONSTRUCTION_FAILED")
    _require_analyst_input_schema_binding(
        snapshot.analyst_input_schema,
        authenticated_surface,
    )
    return authenticated_surface


def _canonical_contract_json_bytes(value: object) -> bytes:
    try:
        return _json.dumps(
            value,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError):
        _raise("WS01_BR_INTERNAL_INVARIANT_FAILURE")


def _deeply_immutable_contract_value(value: object) -> bool:
    if isinstance(value, _MappingProxyType):
        return all(
            type(key) is str and _deeply_immutable_contract_value(item)
            for key, item in value.items()
        )
    if type(value) is tuple:
        return all(_deeply_immutable_contract_value(item) for item in value)
    return type(value) in (str, int, bool, bytes, type(None))


def _bounded_nonempty_text(value: object) -> bool:
    return (
        type(value) is str
        and 1 <= len(value) <= _RESOURCE_BOUND_PROFILE["max_text_code_points"]
        and value == value.strip()
        and not any(0xD800 <= ord(character) <= 0xDFFF for character in value)
    )


def _sha256_string(value: object) -> bool:
    return (
        type(value) is str
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _deep_freeze(value: object) -> object:
    if type(value) is dict:
        return _MappingProxyType({key: _deep_freeze(item) for key, item in value.items()})
    if type(value) is list:
        return tuple(_deep_freeze(item) for item in value)
    return value


def _deep_thaw(value: object) -> object:
    if isinstance(value, _Mapping):
        return {key: _deep_thaw(item) for key, item in value.items()}
    if type(value) is tuple:
        return [_deep_thaw(item) for item in value]
    if type(value) is list:
        return [_deep_thaw(item) for item in value]
    return value


def _raise(code: str) -> None:
    raise _PackageBuilderFailure(code)


__all__ = (
    "build_analyst_input_package",
    "render_analyst_prompt",
)
