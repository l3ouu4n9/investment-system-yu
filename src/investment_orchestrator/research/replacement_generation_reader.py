"""Read-only verifier for one committed R2F-1a render generation.

This module is deliberately independent of R2F-1a's private publication
helpers.  It verifies one explicitly named completed generation through
retained, operation-local descriptor-relative directory handles and captures
the intentionally editable memo only after immutable verification succeeds.
No live descriptor or verified handle escapes the one-shot operation.

It creates no files, pointers, reports, availability decisions, or permissions.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from typing import Any, TypeVar

from investment_orchestrator.common.paths import repo_root
from investment_orchestrator.research.active_research_anchor_registry import (
    SCHEMA_VERSION as BASELINE_REGISTRY_SCHEMA_VERSION,
)
from investment_orchestrator.research.approvals_inclusive_active_registry import (
    SCHEMA_VERSION as APPROVALS_REGISTRY_SCHEMA_VERSION,
)
from investment_orchestrator.research.evidence_packet import (
    EVIDENCE_PACKET_REQUIRED_FIELDS,
    SCHEMA_VERSION as EVIDENCE_PACKET_SCHEMA_VERSION,
    check_evidence_packet_invariants,
)
from investment_orchestrator.research.research_anchor_approval_manifest import (
    MANIFEST_SCHEMA_VERSION as APPROVALS_SOURCE_SCHEMA_VERSION,
)
from investment_orchestrator.research.research_anchors import (
    SCHEMA_VERSION as ANCHORS_SOURCE_SCHEMA_VERSION,
)


V1_MANIFEST_SCHEMA_VERSION = "step1_replacement_input_manifest_v1"
V1_RENDER_BINDING_SCHEMA_VERSION = "step1_replacement_render_generation_binding_v1"
V1_GENERATION_IDENTITY_SCHEMA_VERSION = "step1_replacement_generation_identity_v1"
V1_COMPATIBILITY_PROFILE = "step1_replacement_render_observation_v1"
V2_MANIFEST_SCHEMA_VERSION = "step1_replacement_input_manifest_v2"
V2_RENDER_BINDING_SCHEMA_VERSION = "step1_replacement_render_generation_binding_v2"
V2_GENERATION_IDENTITY_SCHEMA_VERSION = "step1_replacement_generation_identity_v2"
V2_COMPATIBILITY_PROFILE = "step1_replacement_render_observation_v2"
PROMPT_CONTRACT_SCHEMA_VERSION = "step1_replacement_prompt_contract_v2"
PROMPT_TEMPLATE_ID = "r2f_analyst_memo_content_v2"
PROMPT_TEMPLATE_FILENAME = "r2f_analyst_memo_content_v2.txt"
PROMPT_RENDERER_PROFILE = "r2f_prompt_renderer_v2"
RAW_MEMO_SCHEMA_VERSION = "r2f_analyst_memo_content_v2"
PROMPT_PROJECTION_SCHEMA_VERSION = "r2f_bounded_research_prompt_projection_v2"
UNIVERSE_PROJECTION_PROFILE = "allowed_buy_then_extended_base_precedence_v1"
ACTIVE_ANCHOR_PROJECTION_PROFILE = "valid_active_registry_anchor_ids_sorted_v1"
TEXT_ENCODING_PROFILE = "utf8_lf_no_bom_terminal_newline_v1"
# Compatibility aliases keep the committed v1 verifier mechanically unchanged.
MANIFEST_SCHEMA_VERSION = V1_MANIFEST_SCHEMA_VERSION
RENDER_BINDING_SCHEMA_VERSION = V1_RENDER_BINDING_SCHEMA_VERSION
GENERATION_IDENTITY_SCHEMA_VERSION = V1_GENERATION_IDENTITY_SCHEMA_VERSION
COMPATIBILITY_PROFILE = V1_COMPATIBILITY_PROFILE
CAPTURE_PROFILE = "retained_repo_and_common_parent_v1"

R2F_ROOT_PARTS = ("artifacts", "current", "step1_research", "r2f_report_only")
GENERATIONS_DIRECTORY = "generations"
INPUT_PATHS = {
    "strategy_settings": "inputs/current/strategy_settings.yaml",
    "portfolio_snapshot": "inputs/current/portfolio_snapshot.txt",
    "research_anchors": "inputs/current/research_anchors.yaml",
    "research_anchor_approvals": "inputs/current/research_anchor_approvals.yaml",
}
SOURCE_VERSIONS = {
    "strategy_settings": "strategy_settings_repository_contract",
    "portfolio_snapshot": "portfolio_snapshot_repository_contract",
    "research_anchors": ANCHORS_SOURCE_SCHEMA_VERSION,
    "research_anchor_approvals": APPROVALS_SOURCE_SCHEMA_VERSION,
}

MANIFEST_FILENAME = "replacement_input_manifest.json"
EVIDENCE_FILENAME = "evidence_packet.json"
PROMPT_FILENAME = "analyst_memo_prompt.txt"
MEMO_RAW_FILENAME = "analyst_memo_raw_output.txt"
RENDER_BINDING_FILENAME = "render_generation_binding.json"
IN_PROGRESS_FILENAME = ".render_in_progress"

COMPLETED_GENERATION_FILENAMES = frozenset(
    {MANIFEST_FILENAME, EVIDENCE_FILENAME, PROMPT_FILENAME, MEMO_RAW_FILENAME, RENDER_BINDING_FILENAME}
)

AUTHORITY_MARKERS = {
    "report_only": True,
    "runtime_consumed": False,
    "permission_effect": "none",
    "not_authorization": True,
    "order_authorization": False,
    "broker_authorization": False,
}

_MANIFEST_KEYS = frozenset(
    {
        "schema_version",
        "compatibility_profile",
        "as_of",
        "generated_at",
        "capture_profile",
        "inputs",
        "parsed_decision_relevant_settings_sha256",
        "supported_source_versions",
        "evidence_packet",
        "active_registry",
        "domain_validation",
        *AUTHORITY_MARKERS,
    }
)
_V2_MANIFEST_KEYS = frozenset({*_MANIFEST_KEYS, "prompt_contract"})
_INPUT_RECORD_KEYS = frozenset({"path", "file_sha256", "production_text_sha256", "source_version"})
_EVIDENCE_RECORD_KEYS = frozenset({"schema_version", "file_sha256", "canonical_content_sha256"})
_REGISTRY_RECORD_KEYS = frozenset({"schema_version", "canonical_content_sha256", "selected_source"})
_DOMAIN_VALIDATION_KEYS = frozenset({"status", "diagnostics"})
_BINDING_KEYS = frozenset(
    {
        "schema_version",
        "compatibility_profile",
        "generation_id",
        "scope",
        "render_complete",
        "immutable_render_artifacts",
        "operator_editable_inputs",
        *AUTHORITY_MARKERS,
    }
)
_V2_BINDING_KEYS = frozenset({*_BINDING_KEYS, "generation_identity"})
_PROMPT_CONTRACT_PROJECTION_KEYS = frozenset(
    {
        "schema_version",
        "template_id",
        "template_file_sha256",
        "renderer_profile",
        "raw_memo_schema_version",
        "evidence_projection_profile",
        "universe_projection_profile",
        "active_anchor_projection_profile",
        "text_encoding_profile",
    }
)
_PROMPT_CONTRACT_RECORD_KEYS = frozenset(
    {
        "projection",
        "canonical_content_sha256",
        "prompt_projection_schema_version",
        "prompt_projection_canonical_sha256",
        "analyst_memo_prompt_file_sha256",
        "raw_memo_schema_version",
    }
)
_GENERATION_IDENTITY_BINDING_KEYS = frozenset(
    {
        "schema_version",
        "prompt_contract_canonical_sha256",
        "analyst_memo_prompt_file_sha256",
        "raw_memo_schema_version",
    }
)
_IMMUTABLE_BINDING_KEYS = frozenset({MANIFEST_FILENAME, EVIDENCE_FILENAME, PROMPT_FILENAME})
_JSON_ARTIFACT_BINDING_KEYS = frozenset(
    {"schema_version", "file_sha256", "canonical_content_sha256", "mutable_after_render"}
)
_TEXT_ARTIFACT_BINDING_KEYS = frozenset({"media_type", "file_sha256", "mutable_after_render"})
_EDITABLE_RECORD_KEYS = frozenset(
    {
        "media_type",
        "initial_file_sha256",
        "initial_state",
        "operator_editable_after_render",
        "render_witness_attests_initial_bytes_only",
    }
)
_EVIDENCE_TOP_LEVEL_KEYS = frozenset(
    {*EVIDENCE_PACKET_REQUIRED_FIELDS, "active_anchor_registry", *AUTHORITY_MARKERS}
)
_UNIVERSE_KEYS = frozenset(
    {
        "core_universe",
        "satellite_universe",
        "approved_extended_etf",
        "allowed_buy_tickers",
        "role_source_by_ticker",
    }
)
_GENERATION_ID_RE = re.compile(r"[0-9a-f]{64}\Z")
__all__ = (
    "ReplacementGenerationReaderError",
)


class ReplacementGenerationReaderError(RuntimeError):
    """Bounded failure from read-only R2F-1a generation verification."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class _DuplicateJsonKey(ValueError):
    pass


@dataclass(frozen=True)
class _RegularFileState:
    device: int
    inode: int
    size: int
    mode: int
    mtime_ns: int
    ctime_ns: int
    link_count: int


@dataclass(frozen=True)
class VerifiedSourceBinding:
    """Code-derived v2 identities; the raw memo never supplies these values."""

    generation_profile: str
    generation_identity_schema_version: str
    generation_id: str
    prompt_contract_schema_version: str
    prompt_contract_canonical_sha256: str
    raw_memo_schema_version: str
    replacement_input_manifest_file_sha256: str
    replacement_input_manifest_canonical_sha256: str
    evidence_packet_file_sha256: str
    evidence_packet_canonical_sha256: str
    analyst_memo_prompt_file_sha256: str
    as_of: str

    def to_dict(self) -> dict[str, str]:
        return {
            "generation_profile": self.generation_profile,
            "generation_identity_schema_version": self.generation_identity_schema_version,
            "generation_id": self.generation_id,
            "prompt_contract_schema_version": self.prompt_contract_schema_version,
            "prompt_contract_canonical_sha256": self.prompt_contract_canonical_sha256,
            "raw_memo_schema_version": self.raw_memo_schema_version,
            "replacement_input_manifest_file_sha256": self.replacement_input_manifest_file_sha256,
            "replacement_input_manifest_canonical_sha256": self.replacement_input_manifest_canonical_sha256,
            "evidence_packet_file_sha256": self.evidence_packet_file_sha256,
            "evidence_packet_canonical_sha256": self.evidence_packet_canonical_sha256,
            "analyst_memo_prompt_file_sha256": self.analyst_memo_prompt_file_sha256,
            "as_of": self.as_of,
        }


@dataclass(frozen=True)
class EligibleInstrument:
    """One code-owned observation-eligible identifier from verified evidence."""

    instrument_id: str
    universe_category: str
    deterministic_position: int


@dataclass(frozen=True)
class MemoRawRead:
    """One descriptor-bound memo read; raw bytes are never serialized by this module."""

    raw_bytes: bytes
    byte_size: int
    file_sha256: str


@dataclass(frozen=True, slots=True)
class _VerifiedMemoInput:
    """Pure operation-local input to the private memo normalizer."""

    source_binding: VerifiedSourceBinding
    eligible_instruments: tuple[EligibleInstrument, ...]
    active_anchor_ids: tuple[str, ...]
    memo_raw: MemoRawRead


_ResultT = TypeVar("_ResultT")


class _DescriptorOwner:
    """Operation-local descriptor ownership with no-throw exhaustive cleanup."""

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
                os.close(descriptor)
            except BaseException:
                failed = True
        return failed


def _validate_generation_memo_operation(
    generation_id: str,
    validator: Callable[[_VerifiedMemoInput], _ResultT],
) -> _ResultT:
    """Private bridge used by the sole public memo-validation entrypoint."""
    return _validate_generation_memo_operation_at_root(
        generation_id,
        Path(repo_root()),
        validator,
    )


def _validate_generation_memo_operation_at_root_for_tests(
    generation_id: str,
    repository_root: Path,
    validator: Callable[[_VerifiedMemoInput], _ResultT],
) -> _ResultT:
    """Private isolated-root seam; it never returns live descriptors."""
    return _validate_generation_memo_operation_at_root(generation_id, repository_root, validator)


def _validate_generation_memo_operation_at_root(
    generation_id: str,
    repository_root: Path,
    validator: Callable[[_VerifiedMemoInput], _ResultT],
) -> _ResultT:
    _require_descriptor_primitives()
    if not _is_sha256(generation_id):
        raise ReplacementGenerationReaderError("SOURCE_GENERATION_ID_INVALID")

    owner = _DescriptorOwner()
    chain: list[tuple[int, str, int]] = []
    result: _ResultT | None = None
    failure_code: str | None = None
    try:
        repository_fd, root_chain = _open_absolute_directory_chain(repository_root, owner=owner)
        chain.extend(root_chain)
        repository_identity = _directory_identity(os.fstat(repository_fd))
        parent_fd = repository_fd
        for component in (*R2F_ROOT_PARTS, GENERATIONS_DIRECTORY, generation_id):
            child_fd = _open_directory_at(parent_fd, component, owner=owner)
            chain.append((parent_fd, component, child_fd))
            parent_fd = child_fd
        generation_fd = parent_fd
        generation_identity = _directory_identity(os.fstat(generation_fd))
        verified = _verify_completed_generation(
            repository_fd=repository_fd,
            generation_fd=generation_fd,
            generation_id=generation_id,
            owner=owner,
        )
        _verify_directory_chain(chain, "SOURCE_GENERATION_INVALID")
        if verified["compatibility_profile"] != V2_COMPATIBILITY_PROFILE:
            raise ReplacementGenerationReaderError("MEMO_PROMPT_PROFILE_UNSUPPORTED")
        memo_bytes = _capture_verified_memo_at(
            generation_fd,
            MEMO_RAW_FILENAME,
            expected_entry=verified["memo_entry_identity"],
            maximum_bytes=65_536,
            owner=owner,
        )
        _verify_directory_chain(chain, "SOURCE_GENERATION_INVALID")
        if (
            _directory_identity(os.fstat(repository_fd)) != repository_identity
            or _directory_identity(os.fstat(generation_fd)) != generation_identity
        ):
            raise ReplacementGenerationReaderError("SOURCE_GENERATION_INVALID")
        operation_input = _VerifiedMemoInput(
            source_binding=verified["source_binding"],
            eligible_instruments=verified["eligible_instruments"],
            active_anchor_ids=verified["active_anchor_ids"],
            memo_raw=MemoRawRead(
                raw_bytes=memo_bytes,
                byte_size=len(memo_bytes),
                file_sha256=_sha256(memo_bytes),
            ),
        )
        result = validator(operation_input)
    except ReplacementGenerationReaderError as error:
        failure_code = error.code
    except BaseException:
        failure_code = "SOURCE_GENERATION_INVALID"
    finally:
        cleanup_failed = owner.close_all()
    if cleanup_failed:
        raise ReplacementGenerationReaderError("SOURCE_GENERATION_CLEANUP_FAILED") from None
    if failure_code is not None:
        raise ReplacementGenerationReaderError(failure_code) from None
    if result is None:
        raise ReplacementGenerationReaderError("SOURCE_GENERATION_INVALID")
    return result


def _verify_completed_generation(
    *,
    repository_fd: int,
    generation_fd: int,
    generation_id: str,
    owner: _DescriptorOwner,
) -> dict[str, Any]:
    names = _generation_entry_names(generation_fd)
    if IN_PROGRESS_FILENAME in names or RENDER_BINDING_FILENAME not in names:
        raise ReplacementGenerationReaderError("SOURCE_GENERATION_INCOMPLETE")
    if names != COMPLETED_GENERATION_FILENAMES:
        raise ReplacementGenerationReaderError("SOURCE_GENERATION_INVALID")
    memo_entry_identity: _RegularFileState | None = None
    for filename in COMPLETED_GENERATION_FILENAMES:
        identity = _verified_regular_file_state_at(generation_fd, filename, owner=owner)
        if filename == MEMO_RAW_FILENAME:
            memo_entry_identity = identity
    if memo_entry_identity is None:
        raise ReplacementGenerationReaderError("SOURCE_GENERATION_INVALID")

    manifest_bytes = _read_stable_regular_file_at(
        generation_fd,
        MANIFEST_FILENAME,
        maximum_bytes=None,
        too_large_code="SOURCE_GENERATION_INVALID",
        error_code="SOURCE_GENERATION_INVALID",
        owner=owner,
    )
    evidence_bytes = _read_stable_regular_file_at(
        generation_fd,
        EVIDENCE_FILENAME,
        maximum_bytes=None,
        too_large_code="SOURCE_GENERATION_INVALID",
        error_code="SOURCE_GENERATION_INVALID",
        owner=owner,
    )
    prompt_bytes = _read_stable_regular_file_at(
        generation_fd,
        PROMPT_FILENAME,
        maximum_bytes=None,
        too_large_code="SOURCE_GENERATION_INVALID",
        error_code="SOURCE_GENERATION_INVALID",
        owner=owner,
    )
    binding_bytes = _read_stable_regular_file_at(
        generation_fd,
        RENDER_BINDING_FILENAME,
        maximum_bytes=None,
        too_large_code="SOURCE_GENERATION_INVALID",
        error_code="SOURCE_GENERATION_INVALID",
        owner=owner,
    )
    manifest = _parse_json_object(manifest_bytes, "SOURCE_GENERATION_INVALID")
    evidence = _parse_json_object(evidence_bytes, "SOURCE_GENERATION_INVALID")
    binding = _parse_json_object(binding_bytes, "SOURCE_GENERATION_INVALID")

    schema_version = manifest.get("schema_version")
    if schema_version == V1_MANIFEST_SCHEMA_VERSION:
        _validate_manifest(manifest)
        _validate_evidence_packet(evidence, manifest=manifest)
        _validate_render_binding(binding, expected_generation_id=generation_id)
        generation_identity = _semantic_generation_identity(manifest)
        compatibility_profile = V1_COMPATIBILITY_PROFILE
    elif schema_version == V2_MANIFEST_SCHEMA_VERSION:
        _validate_manifest_v2(manifest)
        _validate_evidence_packet(evidence, manifest=manifest)
        _validate_render_binding_v2(binding, expected_generation_id=generation_id)
        generation_identity = _semantic_generation_identity_v2(manifest)
        compatibility_profile = V2_COMPATIBILITY_PROFILE
    else:
        raise ReplacementGenerationReaderError("SOURCE_GENERATION_INVALID")
    if binding_bytes != _json_file_bytes(binding):
        raise ReplacementGenerationReaderError("SOURCE_GENERATION_INVALID")

    if _canonical_sha256(generation_identity) != generation_id:
        raise ReplacementGenerationReaderError("SOURCE_GENERATION_INVALID")

    immutable = binding["immutable_render_artifacts"]
    manifest_record = immutable[MANIFEST_FILENAME]
    evidence_record = immutable[EVIDENCE_FILENAME]
    prompt_record = immutable[PROMPT_FILENAME]
    if (
        manifest_record["file_sha256"] != _sha256(manifest_bytes)
        or manifest_record["canonical_content_sha256"] != _canonical_sha256(manifest)
        or evidence_record["file_sha256"] != _sha256(evidence_bytes)
        or evidence_record["canonical_content_sha256"] != _canonical_sha256(evidence)
        or prompt_record["file_sha256"] != _sha256(prompt_bytes)
    ):
        raise ReplacementGenerationReaderError("SOURCE_GENERATION_INVALID")

    if compatibility_profile == V2_COMPATIBILITY_PROFILE:
        generation_binding = binding["generation_identity"]
        prompt_contract = manifest["prompt_contract"]
        if (
            generation_binding["prompt_contract_canonical_sha256"]
            != prompt_contract["canonical_content_sha256"]
            or generation_binding["analyst_memo_prompt_file_sha256"]
            != prompt_contract["analyst_memo_prompt_file_sha256"]
            or generation_binding["raw_memo_schema_version"]
            != prompt_contract["raw_memo_schema_version"]
        ):
            raise ReplacementGenerationReaderError("SOURCE_GENERATION_INVALID")
        _verify_prompt_contract_v2(
            repository_fd=repository_fd,
            manifest=manifest,
            evidence=evidence,
            prompt_bytes=prompt_bytes,
            owner=owner,
        )

    manifest_evidence = manifest["evidence_packet"]
    if (
        manifest_evidence["file_sha256"] != _sha256(evidence_bytes)
        or manifest_evidence["canonical_content_sha256"] != _canonical_sha256(evidence)
        or manifest_evidence["schema_version"] != evidence["schema_version"]
    ):
        raise ReplacementGenerationReaderError("SOURCE_GENERATION_INVALID")

    registry = evidence["active_anchor_registry"]
    if manifest["active_registry"]["schema_version"] != registry["schema_version"]:
        raise ReplacementGenerationReaderError("SOURCE_GENERATION_INVALID")
    if manifest["active_registry"]["canonical_content_sha256"] != _canonical_sha256(registry):
        raise ReplacementGenerationReaderError("SOURCE_GENERATION_INVALID")

    prompt_contract = manifest.get("prompt_contract")
    if compatibility_profile != V2_COMPATIBILITY_PROFILE:
        return {
            "compatibility_profile": compatibility_profile,
            "memo_entry_identity": memo_entry_identity,
        }
    assert isinstance(prompt_contract, Mapping)
    projection = prompt_contract["projection"]
    assert isinstance(projection, Mapping)
    binding_source = VerifiedSourceBinding(
        generation_profile=compatibility_profile,
        generation_identity_schema_version=V2_GENERATION_IDENTITY_SCHEMA_VERSION,
        generation_id=generation_id,
        prompt_contract_schema_version=projection["schema_version"],
        prompt_contract_canonical_sha256=prompt_contract["canonical_content_sha256"],
        raw_memo_schema_version=prompt_contract["raw_memo_schema_version"],
        replacement_input_manifest_file_sha256=_sha256(manifest_bytes),
        replacement_input_manifest_canonical_sha256=_canonical_sha256(manifest),
        evidence_packet_file_sha256=_sha256(evidence_bytes),
        evidence_packet_canonical_sha256=_canonical_sha256(evidence),
        analyst_memo_prompt_file_sha256=_sha256(prompt_bytes),
        as_of=manifest["as_of"],
    )
    eligible = _eligible_instruments(evidence)
    active_anchor_ids = _active_anchor_ids(registry)
    return {
        "compatibility_profile": compatibility_profile,
        "source_binding": binding_source,
        "eligible_instruments": eligible,
        "active_anchor_ids": active_anchor_ids,
        "memo_entry_identity": memo_entry_identity,
    }


def _validate_manifest(payload: Any) -> None:
    if not isinstance(payload, Mapping) or set(payload) != _MANIFEST_KEYS:
        raise ReplacementGenerationReaderError("SOURCE_GENERATION_INVALID")
    if payload.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        raise ReplacementGenerationReaderError("SOURCE_GENERATION_INVALID")
    if payload.get("compatibility_profile") != COMPATIBILITY_PROFILE:
        raise ReplacementGenerationReaderError("SOURCE_GENERATION_INVALID")
    _validate_authority_markers(payload)
    as_of = _validated_as_of(payload.get("as_of"))
    if payload.get("generated_at") != f"{as_of}T00:00:00+00:00":
        raise ReplacementGenerationReaderError("SOURCE_GENERATION_INVALID")
    if payload.get("capture_profile") != CAPTURE_PROFILE:
        raise ReplacementGenerationReaderError("SOURCE_GENERATION_INVALID")

    inputs = payload.get("inputs")
    if not isinstance(inputs, Mapping) or set(inputs) != set(INPUT_PATHS):
        raise ReplacementGenerationReaderError("SOURCE_GENERATION_INVALID")
    for name, expected_path in INPUT_PATHS.items():
        record = inputs.get(name)
        if not isinstance(record, Mapping) or set(record) != _INPUT_RECORD_KEYS:
            raise ReplacementGenerationReaderError("SOURCE_GENERATION_INVALID")
        if record.get("path") != expected_path or not _bounded_repository_relative_path(expected_path):
            raise ReplacementGenerationReaderError("SOURCE_GENERATION_INVALID")
        if not _is_sha256(record.get("file_sha256")) or not _is_sha256(
            record.get("production_text_sha256")
        ):
            raise ReplacementGenerationReaderError("SOURCE_GENERATION_INVALID")
        if record.get("source_version") != SOURCE_VERSIONS[name]:
            raise ReplacementGenerationReaderError("SOURCE_GENERATION_INVALID")
    if payload.get("supported_source_versions") != SOURCE_VERSIONS:
        raise ReplacementGenerationReaderError("SOURCE_GENERATION_INVALID")
    if not _is_sha256(payload.get("parsed_decision_relevant_settings_sha256")):
        raise ReplacementGenerationReaderError("SOURCE_GENERATION_INVALID")

    evidence = payload.get("evidence_packet")
    if not isinstance(evidence, Mapping) or set(evidence) != _EVIDENCE_RECORD_KEYS:
        raise ReplacementGenerationReaderError("SOURCE_GENERATION_INVALID")
    if evidence.get("schema_version") != EVIDENCE_PACKET_SCHEMA_VERSION:
        raise ReplacementGenerationReaderError("SOURCE_GENERATION_INVALID")
    if not _is_sha256(evidence.get("file_sha256")) or not _is_sha256(
        evidence.get("canonical_content_sha256")
    ):
        raise ReplacementGenerationReaderError("SOURCE_GENERATION_INVALID")

    registry = payload.get("active_registry")
    if not isinstance(registry, Mapping) or set(registry) != _REGISTRY_RECORD_KEYS:
        raise ReplacementGenerationReaderError("SOURCE_GENERATION_INVALID")
    if registry.get("schema_version") not in {
        BASELINE_REGISTRY_SCHEMA_VERSION,
        APPROVALS_REGISTRY_SCHEMA_VERSION,
    }:
        raise ReplacementGenerationReaderError("SOURCE_GENERATION_INVALID")
    if not _is_sha256(registry.get("canonical_content_sha256")):
        raise ReplacementGenerationReaderError("SOURCE_GENERATION_INVALID")
    if registry.get("selected_source") not in {
        "approvals_inclusive",
        "baseline_fallback",
        "fail_closed_empty",
    }:
        raise ReplacementGenerationReaderError("SOURCE_GENERATION_INVALID")
    if (
        registry.get("selected_source") == "approvals_inclusive"
        and registry.get("schema_version") != APPROVALS_REGISTRY_SCHEMA_VERSION
    ) or (
        registry.get("selected_source") in {"baseline_fallback", "fail_closed_empty"}
        and registry.get("schema_version") != BASELINE_REGISTRY_SCHEMA_VERSION
    ):
        raise ReplacementGenerationReaderError("SOURCE_GENERATION_INVALID")

    domain = payload.get("domain_validation")
    if not isinstance(domain, Mapping) or set(domain) != _DOMAIN_VALIDATION_KEYS:
        raise ReplacementGenerationReaderError("SOURCE_GENERATION_INVALID")
    if domain.get("status") != "DOMAIN_VALID_BUT_NONACTIVATING":
        raise ReplacementGenerationReaderError("SOURCE_GENERATION_INVALID")
    diagnostics = domain.get("diagnostics")
    allowed_diagnostics = {
        "APPROVAL_HASH_MISMATCH",
        "EMPTY_ACTIVE_REGISTRY",
        "EXPIRED_OR_INACTIVE_APPROVAL",
        "MARKET_METRICS_UNAVAILABLE",
        "NO_APPROVALS",
        "PORTFOLIO_COVERAGE_INCOMPLETE",
        "REVOCATION_PRESENT",
        "SCHEDULED_EVENTS_UNAVAILABLE",
    }
    if (
        not isinstance(diagnostics, list)
        or any(not isinstance(item, str) for item in diagnostics)
        or diagnostics != sorted(set(diagnostics))
        or any(item not in allowed_diagnostics for item in diagnostics)
    ):
        raise ReplacementGenerationReaderError("SOURCE_GENERATION_INVALID")


def _validate_manifest_v2(payload: Any) -> None:
    if not isinstance(payload, Mapping) or set(payload) != _V2_MANIFEST_KEYS:
        raise ReplacementGenerationReaderError("SOURCE_GENERATION_INVALID")
    if (
        payload.get("schema_version") != V2_MANIFEST_SCHEMA_VERSION
        or payload.get("compatibility_profile") != V2_COMPATIBILITY_PROFILE
    ):
        raise ReplacementGenerationReaderError("SOURCE_GENERATION_INVALID")
    v1_view = dict(payload)
    prompt_contract = v1_view.pop("prompt_contract")
    v1_view["schema_version"] = V1_MANIFEST_SCHEMA_VERSION
    v1_view["compatibility_profile"] = V1_COMPATIBILITY_PROFILE
    _validate_manifest(v1_view)
    _validate_prompt_contract_record_v2(prompt_contract)


def _validate_prompt_contract_record_v2(value: Any) -> None:
    if not isinstance(value, Mapping) or set(value) != _PROMPT_CONTRACT_RECORD_KEYS:
        raise ReplacementGenerationReaderError("SOURCE_GENERATION_INVALID")
    projection = value.get("projection")
    if not isinstance(projection, Mapping) or set(projection) != _PROMPT_CONTRACT_PROJECTION_KEYS:
        raise ReplacementGenerationReaderError("SOURCE_GENERATION_INVALID")
    expected = {
        "schema_version": PROMPT_CONTRACT_SCHEMA_VERSION,
        "template_id": PROMPT_TEMPLATE_ID,
        "renderer_profile": PROMPT_RENDERER_PROFILE,
        "raw_memo_schema_version": RAW_MEMO_SCHEMA_VERSION,
        "evidence_projection_profile": PROMPT_PROJECTION_SCHEMA_VERSION,
        "universe_projection_profile": UNIVERSE_PROJECTION_PROFILE,
        "active_anchor_projection_profile": ACTIVE_ANCHOR_PROJECTION_PROFILE,
        "text_encoding_profile": TEXT_ENCODING_PROFILE,
    }
    if any(projection.get(key) != expected_value for key, expected_value in expected.items()):
        raise ReplacementGenerationReaderError("SOURCE_GENERATION_INVALID")
    if not _is_sha256(projection.get("template_file_sha256")):
        raise ReplacementGenerationReaderError("SOURCE_GENERATION_INVALID")
    if value.get("canonical_content_sha256") != _canonical_sha256(projection):
        raise ReplacementGenerationReaderError("SOURCE_GENERATION_INVALID")
    if (
        value.get("prompt_projection_schema_version") != PROMPT_PROJECTION_SCHEMA_VERSION
        or not _is_sha256(value.get("prompt_projection_canonical_sha256"))
        or not _is_sha256(value.get("analyst_memo_prompt_file_sha256"))
        or value.get("raw_memo_schema_version") != RAW_MEMO_SCHEMA_VERSION
    ):
        raise ReplacementGenerationReaderError("SOURCE_GENERATION_INVALID")


def _validate_evidence_packet(payload: Any, *, manifest: Mapping[str, Any]) -> None:
    if not isinstance(payload, Mapping) or set(payload) != _EVIDENCE_TOP_LEVEL_KEYS:
        raise ReplacementGenerationReaderError("SOURCE_GENERATION_INVALID")
    if check_evidence_packet_invariants(payload):
        raise ReplacementGenerationReaderError("SOURCE_GENERATION_INVALID")
    _validate_authority_markers(payload)
    if payload.get("schema_version") != EVIDENCE_PACKET_SCHEMA_VERSION:
        raise ReplacementGenerationReaderError("SOURCE_GENERATION_INVALID")
    if payload.get("generated_at") != f"{manifest['as_of']}T00:00:00+00:00":
        raise ReplacementGenerationReaderError("SOURCE_GENERATION_INVALID")
    if payload.get("strategy_settings_hash") != manifest["parsed_decision_relevant_settings_sha256"]:
        raise ReplacementGenerationReaderError("SOURCE_GENERATION_INVALID")
    source_artifacts = payload.get("source_artifacts")
    if not isinstance(source_artifacts, Mapping) or source_artifacts != {
        name: INPUT_PATHS[name] for name in INPUT_PATHS
    }:
        raise ReplacementGenerationReaderError("SOURCE_GENERATION_INVALID")

    universe = payload.get("universe")
    if not isinstance(universe, Mapping) or set(universe) != _UNIVERSE_KEYS:
        raise ReplacementGenerationReaderError("SOURCE_GENERATION_INVALID")
    for key in ("allowed_buy_tickers", "approved_extended_etf"):
        values = universe.get(key)
        if not isinstance(values, list) or not _valid_identifier_list(values):
            raise ReplacementGenerationReaderError("SOURCE_GENERATION_INVALID")

    registry = payload.get("active_anchor_registry")
    _validate_active_registry(registry)


def _validate_active_registry(payload: Any) -> None:
    if not isinstance(payload, Mapping):
        raise ReplacementGenerationReaderError("SOURCE_GENERATION_INVALID")
    if payload.get("schema_version") not in {
        BASELINE_REGISTRY_SCHEMA_VERSION,
        APPROVALS_REGISTRY_SCHEMA_VERSION,
    }:
        raise ReplacementGenerationReaderError("SOURCE_GENERATION_INVALID")
    if (
        payload.get("is_llm_generated") is not False
        or payload.get("report_only") is not True
        or payload.get("permission_effect") != "none"
        or payload.get("not_authorization") is not True
        or payload.get("registry_valid") is not True
    ):
        raise ReplacementGenerationReaderError("SOURCE_GENERATION_INVALID")
    anchors = payload.get("active_anchors")
    if not isinstance(anchors, list):
        raise ReplacementGenerationReaderError("SOURCE_GENERATION_INVALID")
    _active_anchor_ids(payload)


def _validate_render_binding(payload: Any, *, expected_generation_id: str) -> None:
    if not isinstance(payload, Mapping) or set(payload) != _BINDING_KEYS:
        raise ReplacementGenerationReaderError("SOURCE_GENERATION_INVALID")
    if (
        payload.get("schema_version") != RENDER_BINDING_SCHEMA_VERSION
        or payload.get("compatibility_profile") != COMPATIBILITY_PROFILE
        or payload.get("generation_id") != expected_generation_id
        or payload.get("scope") != "IMMUTABLE_RENDER_ARTIFACTS_AND_INITIAL_BLANK_MEMO_ONLY"
        or payload.get("render_complete") is not True
    ):
        raise ReplacementGenerationReaderError("SOURCE_GENERATION_INVALID")
    _validate_authority_markers(payload)

    immutable = payload.get("immutable_render_artifacts")
    if not isinstance(immutable, Mapping) or set(immutable) != _IMMUTABLE_BINDING_KEYS:
        raise ReplacementGenerationReaderError("SOURCE_GENERATION_INVALID")
    for filename, record in immutable.items():
        expected_keys = _TEXT_ARTIFACT_BINDING_KEYS if filename.endswith(".txt") else _JSON_ARTIFACT_BINDING_KEYS
        if not isinstance(record, Mapping) or set(record) != expected_keys:
            raise ReplacementGenerationReaderError("SOURCE_GENERATION_INVALID")
        if record.get("mutable_after_render") is not False or not _is_sha256(record.get("file_sha256")):
            raise ReplacementGenerationReaderError("SOURCE_GENERATION_INVALID")
        if filename.endswith(".json"):
            expected_schema = MANIFEST_SCHEMA_VERSION if filename == MANIFEST_FILENAME else EVIDENCE_PACKET_SCHEMA_VERSION
            if record.get("schema_version") != expected_schema or not _is_sha256(
                record.get("canonical_content_sha256")
            ):
                raise ReplacementGenerationReaderError("SOURCE_GENERATION_INVALID")
        elif record.get("media_type") != "text/plain; charset=utf-8":
            raise ReplacementGenerationReaderError("SOURCE_GENERATION_INVALID")

    editable = payload.get("operator_editable_inputs")
    if not isinstance(editable, Mapping) or set(editable) != {MEMO_RAW_FILENAME}:
        raise ReplacementGenerationReaderError("SOURCE_GENERATION_INVALID")
    record = editable.get(MEMO_RAW_FILENAME)
    if not isinstance(record, Mapping) or set(record) != _EDITABLE_RECORD_KEYS:
        raise ReplacementGenerationReaderError("SOURCE_GENERATION_INVALID")
    if (
        record.get("media_type") != "text/plain; charset=utf-8"
        or record.get("initial_state") != "BLANK"
        or record.get("operator_editable_after_render") is not True
        or record.get("render_witness_attests_initial_bytes_only") is not True
        or record.get("initial_file_sha256") != _sha256(b"")
    ):
        raise ReplacementGenerationReaderError("SOURCE_GENERATION_INVALID")


def _validate_render_binding_v2(payload: Any, *, expected_generation_id: str) -> None:
    if not isinstance(payload, Mapping) or set(payload) != _V2_BINDING_KEYS:
        raise ReplacementGenerationReaderError("SOURCE_GENERATION_INVALID")
    if (
        payload.get("schema_version") != V2_RENDER_BINDING_SCHEMA_VERSION
        or payload.get("compatibility_profile") != V2_COMPATIBILITY_PROFILE
    ):
        raise ReplacementGenerationReaderError("SOURCE_GENERATION_INVALID")
    v1_view = dict(payload)
    identity = v1_view.pop("generation_identity")
    v1_view["schema_version"] = V1_RENDER_BINDING_SCHEMA_VERSION
    v1_view["compatibility_profile"] = V1_COMPATIBILITY_PROFILE
    immutable = dict(v1_view["immutable_render_artifacts"])
    manifest_record = dict(immutable[MANIFEST_FILENAME])
    manifest_record["schema_version"] = V1_MANIFEST_SCHEMA_VERSION
    immutable[MANIFEST_FILENAME] = manifest_record
    v1_view["immutable_render_artifacts"] = immutable
    _validate_render_binding(v1_view, expected_generation_id=expected_generation_id)
    if (
        not isinstance(identity, Mapping)
        or set(identity) != _GENERATION_IDENTITY_BINDING_KEYS
        or identity.get("schema_version") != V2_GENERATION_IDENTITY_SCHEMA_VERSION
        or not _is_sha256(identity.get("prompt_contract_canonical_sha256"))
        or not _is_sha256(identity.get("analyst_memo_prompt_file_sha256"))
        or identity.get("raw_memo_schema_version") != RAW_MEMO_SCHEMA_VERSION
    ):
        raise ReplacementGenerationReaderError("SOURCE_GENERATION_INVALID")
    if identity.get("analyst_memo_prompt_file_sha256") != immutable[PROMPT_FILENAME].get(
        "file_sha256"
    ):
        raise ReplacementGenerationReaderError("SOURCE_GENERATION_INVALID")


def _semantic_generation_identity(manifest: Mapping[str, Any]) -> dict[str, Any]:
    """Independent, content-only reproduction of the committed R2F-1a identity."""
    _validate_manifest(manifest)
    return {
        "schema_version": GENERATION_IDENTITY_SCHEMA_VERSION,
        "manifest_schema_version": manifest["schema_version"],
        "evidence_schema_version": manifest["evidence_packet"]["schema_version"],
        "compatibility_profile": manifest["compatibility_profile"],
        "capture_profile": manifest["capture_profile"],
        "as_of": manifest["as_of"],
        "inputs": {
            name: {
                "path": manifest["inputs"][name]["path"],
                "source_version": manifest["inputs"][name]["source_version"],
                "file_sha256": manifest["inputs"][name]["file_sha256"],
                "production_text_sha256": manifest["inputs"][name]["production_text_sha256"],
            }
            for name in INPUT_PATHS
        },
        "supported_source_versions": dict(manifest["supported_source_versions"]),
        "parsed_decision_relevant_settings_sha256": manifest[
            "parsed_decision_relevant_settings_sha256"
        ],
        "active_registry": dict(manifest["active_registry"]),
        "evidence_packet": dict(manifest["evidence_packet"]),
        "authority_markers": dict(AUTHORITY_MARKERS),
    }


def _semantic_generation_identity_v2(manifest: Mapping[str, Any]) -> dict[str, Any]:
    _validate_manifest_v2(manifest)
    v1_view = dict(manifest)
    prompt_contract = v1_view.pop("prompt_contract")
    v1_view["schema_version"] = V1_MANIFEST_SCHEMA_VERSION
    v1_view["compatibility_profile"] = V1_COMPATIBILITY_PROFILE
    identity = _semantic_generation_identity(v1_view)
    identity["schema_version"] = V2_GENERATION_IDENTITY_SCHEMA_VERSION
    identity["manifest_schema_version"] = V2_MANIFEST_SCHEMA_VERSION
    identity["compatibility_profile"] = V2_COMPATIBILITY_PROFILE
    identity["prompt_contract"] = dict(prompt_contract)
    return identity


def _verify_prompt_contract_v2(
    *,
    repository_fd: int,
    manifest: Mapping[str, Any],
    evidence: Mapping[str, Any],
    prompt_bytes: bytes,
    owner: _DescriptorOwner,
) -> None:
    prompt_contract = manifest["prompt_contract"]
    assert isinstance(prompt_contract, Mapping)
    projection = prompt_contract["projection"]
    assert isinstance(projection, Mapping)
    prompt_directory_fd = _open_directory_at(repository_fd, "prompts", owner=owner)
    template_bytes = _read_stable_regular_file_at(
        prompt_directory_fd,
        PROMPT_TEMPLATE_FILENAME,
        maximum_bytes=None,
        too_large_code="SOURCE_GENERATION_INVALID",
        error_code="SOURCE_GENERATION_INVALID",
        owner=owner,
    )
    if (
        template_bytes.startswith(b"\xef\xbb\xbf")
        or b"\r" in template_bytes
        or not template_bytes.endswith(b"\n")
        or _sha256(template_bytes) != projection["template_file_sha256"]
    ):
        raise ReplacementGenerationReaderError("SOURCE_GENERATION_INVALID")
    bounded = _bounded_prompt_projection_v2(evidence=evidence, as_of=manifest["as_of"])
    if _canonical_sha256(bounded) != prompt_contract["prompt_projection_canonical_sha256"]:
        raise ReplacementGenerationReaderError("SOURCE_GENERATION_INVALID")
    expected_prompt = _render_memo_prompt_v2(template_bytes, bounded)
    if expected_prompt != prompt_bytes:
        raise ReplacementGenerationReaderError("SOURCE_GENERATION_INVALID")
    if _sha256(prompt_bytes) != prompt_contract["analyst_memo_prompt_file_sha256"]:
        raise ReplacementGenerationReaderError("SOURCE_GENERATION_INVALID")


def _bounded_prompt_projection_v2(
    *, evidence: Mapping[str, Any], as_of: str
) -> dict[str, Any]:
    eligible = [
        {
            "instrument_id": item.instrument_id,
            "universe_category": item.universe_category,
        }
        for item in _eligible_instruments(evidence)
    ]
    registry = evidence["active_anchor_registry"]
    assert isinstance(registry, Mapping)
    rows = registry["active_anchors"]
    assert isinstance(rows, list)
    active_anchors = [
        {
            "anchor_id": row.get("anchor_id"),
            "applicable_tickers": list(row.get("applicable_tickers") or []),
            "anchor_date_et": row.get("anchor_date_et"),
            "valid_from": row.get("valid_from"),
            "valid_until": row.get("valid_until"),
            "confidence_floor": row.get("confidence_floor"),
            "summary": row.get("summary"),
        }
        for row in rows
        if isinstance(row, Mapping)
    ]
    active_anchors.sort(key=lambda row: row["anchor_id"])
    return {
        "schema_version": PROMPT_PROJECTION_SCHEMA_VERSION,
        "as_of": as_of,
        "eligible_instruments": eligible,
        "active_anchors": active_anchors,
        "research_context": {
            "market_metrics": _bounded_availability_context_v2(
                evidence.get("market_metrics")
            ),
            "scheduled_events_deterministic": _bounded_availability_context_v2(
                evidence.get("scheduled_events_deterministic")
            ),
        },
    }


def _bounded_availability_context_v2(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ReplacementGenerationReaderError("SOURCE_GENERATION_INVALID")
    data_gap = value.get("data_gap")
    if data_gap is not None and not isinstance(data_gap, str):
        raise ReplacementGenerationReaderError("SOURCE_GENERATION_INVALID")
    return {"available": value.get("available") is True, "data_gap": data_gap}


def _render_memo_prompt_v2(template_bytes: bytes, projection: Mapping[str, Any]) -> bytes:
    failed = False
    try:
        template = template_bytes.decode("utf-8")
    except UnicodeDecodeError:
        failed = True
        template = ""
    placeholder = "{{ prompt_projection_json }}"
    if failed or template.count(placeholder) != 1:
        raise ReplacementGenerationReaderError("SOURCE_GENERATION_INVALID") from None
    projection_json = json.dumps(projection, ensure_ascii=False, indent=2, sort_keys=True)
    return template.replace(placeholder, projection_json).encode("utf-8")


def _eligible_instruments(payload: Mapping[str, Any]) -> tuple[EligibleInstrument, ...]:
    universe = payload["universe"]
    assert isinstance(universe, Mapping)
    values: list[EligibleInstrument] = []
    seen: set[str] = set()
    for category, key in (
        ("BASE_EVIDENCE_UNIVERSE", "allowed_buy_tickers"),
        ("APPROVED_EXTENDED_OBSERVATION_ONLY", "approved_extended_etf"),
    ):
        raw = universe[key]
        assert isinstance(raw, list)
        for instrument_id in raw:
            assert isinstance(instrument_id, str)
            if instrument_id in seen:
                continue
            seen.add(instrument_id)
            values.append(
                EligibleInstrument(
                    instrument_id=instrument_id,
                    universe_category=category,
                    deterministic_position=len(values),
                )
            )
    return tuple(values)


def _active_anchor_ids(payload: Mapping[str, Any]) -> tuple[str, ...]:
    raw = payload.get("active_anchors")
    if not isinstance(raw, list):
        raise ReplacementGenerationReaderError("SOURCE_GENERATION_INVALID")
    values: list[str] = []
    for row in raw:
        if not isinstance(row, Mapping):
            raise ReplacementGenerationReaderError("SOURCE_GENERATION_INVALID")
        anchor_id = row.get("anchor_id")
        if not _canonical_nonempty_string(anchor_id) or anchor_id in values:
            raise ReplacementGenerationReaderError("SOURCE_GENERATION_INVALID")
        values.append(anchor_id)
    return tuple(sorted(values))


def _generation_entry_names(generation_fd: int) -> frozenset[str]:
    try:
        names = os.listdir(generation_fd)
    except OSError as exc:
        raise ReplacementGenerationReaderError("SOURCE_GENERATION_INVALID") from exc
    if any(not isinstance(name, str) for name in names):
        raise ReplacementGenerationReaderError("SOURCE_GENERATION_INVALID")
    return frozenset(names)


def _verified_regular_file_state_at(
    directory_fd: int,
    filename: str,
    *,
    owner: _DescriptorOwner,
) -> _RegularFileState:
    try:
        entry = os.stat(filename, dir_fd=directory_fd, follow_symlinks=False)
        if not stat.S_ISREG(entry.st_mode):
            raise ReplacementGenerationReaderError("SOURCE_GENERATION_INVALID")
        descriptor = owner.register(
            os.open(
                filename,
                os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK,
                dir_fd=directory_fd,
            )
        )
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or _regular_file_state(entry) != _regular_file_state(opened):
            raise ReplacementGenerationReaderError("SOURCE_GENERATION_INVALID")
        return _regular_file_state(opened)
    except ReplacementGenerationReaderError:
        raise
    except OSError as exc:
        raise ReplacementGenerationReaderError("SOURCE_GENERATION_INVALID") from exc


def _capture_verified_memo_at(
    directory_fd: int,
    filename: str,
    *,
    expected_entry: _RegularFileState,
    maximum_bytes: int,
    owner: _DescriptorOwner,
) -> bytes:
    """Accept one capture only after a same-descriptor verification pass."""
    failure_code: str | None = None
    try:
        entry_before = os.stat(filename, dir_fd=directory_fd, follow_symlinks=False)
        entry_state = _regular_file_state(entry_before)
        if not stat.S_ISREG(entry_before.st_mode) or entry_state != expected_entry:
            raise ReplacementGenerationReaderError("MEMO_SOURCE_UNSTABLE")
        if entry_before.st_size > maximum_bytes:
            raise ReplacementGenerationReaderError("MEMO_TOO_LARGE")

        descriptor = owner.register(
            os.open(
                filename,
                os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK,
                dir_fd=directory_fd,
            )
        )
        opened_state = _regular_file_state(os.fstat(descriptor))
        if opened_state != expected_entry:
            raise ReplacementGenerationReaderError("MEMO_SOURCE_UNSTABLE")

        captured = _read_bounded_descriptor(
            descriptor,
            maximum_bytes=maximum_bytes,
            too_large_code="MEMO_TOO_LARGE",
        )
        _require_memo_state_and_entry(
            descriptor=descriptor,
            directory_fd=directory_fd,
            filename=filename,
            expected_state=opened_state,
        )

        os.lseek(descriptor, 0, os.SEEK_SET)
        verification = _read_bounded_descriptor(
            descriptor,
            maximum_bytes=maximum_bytes,
            too_large_code="MEMO_TOO_LARGE",
        )
        if verification != captured:
            raise ReplacementGenerationReaderError("MEMO_SOURCE_UNSTABLE")
        _require_memo_state_and_entry(
            descriptor=descriptor,
            directory_fd=directory_fd,
            filename=filename,
            expected_state=opened_state,
        )
        return captured
    except ReplacementGenerationReaderError:
        raise
    except OSError:
        failure_code = "MEMO_SOURCE_UNSTABLE"
    except BaseException:
        failure_code = "MEMO_SOURCE_READ_FAILED"
    if failure_code is not None:
        raise ReplacementGenerationReaderError(failure_code) from None
    raise ReplacementGenerationReaderError("MEMO_SOURCE_READ_FAILED")


def _read_bounded_descriptor(
    descriptor: int,
    *,
    maximum_bytes: int,
    too_large_code: str,
) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = os.read(descriptor, min(65_536, maximum_bytes + 1 - total))
        if not chunk:
            break
        total += len(chunk)
        if total > maximum_bytes:
            raise ReplacementGenerationReaderError(too_large_code)
        chunks.append(chunk)
    return b"".join(chunks)


def _require_memo_state_and_entry(
    *,
    descriptor: int,
    directory_fd: int,
    filename: str,
    expected_state: _RegularFileState,
) -> None:
    descriptor_state = _regular_file_state(os.fstat(descriptor))
    entry = os.stat(filename, dir_fd=directory_fd, follow_symlinks=False)
    entry_state = _regular_file_state(entry)
    if (
        not stat.S_ISREG(entry.st_mode)
        or descriptor_state != expected_state
        or entry_state != expected_state
    ):
        raise ReplacementGenerationReaderError("MEMO_SOURCE_UNSTABLE")


def _read_stable_regular_file_at(
    directory_fd: int,
    filename: str,
    *,
    maximum_bytes: int | None,
    too_large_code: str,
    error_code: str,
    owner: _DescriptorOwner,
) -> bytes:
    try:
        entry = os.stat(filename, dir_fd=directory_fd, follow_symlinks=False)
        if not stat.S_ISREG(entry.st_mode):
            raise ReplacementGenerationReaderError(error_code)
        if maximum_bytes is not None and entry.st_size > maximum_bytes:
            raise ReplacementGenerationReaderError(too_large_code)
        descriptor = owner.register(
            os.open(
                filename,
                os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK,
                dir_fd=directory_fd,
            )
        )
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or _file_identity(entry) != _file_identity(opened):
            raise ReplacementGenerationReaderError(error_code)
        before = _stable_file_identity(opened)
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, 65536)
            if not chunk:
                break
            total += len(chunk)
            if maximum_bytes is not None and total > maximum_bytes:
                raise ReplacementGenerationReaderError(too_large_code)
            chunks.append(chunk)
        after = _stable_file_identity(os.fstat(descriptor))
        if before != after:
            raise ReplacementGenerationReaderError(error_code)
        return b"".join(chunks)
    except ReplacementGenerationReaderError:
        raise
    except OSError as exc:
        raise ReplacementGenerationReaderError(error_code) from exc


def _open_absolute_directory_chain(
    root: Path,
    *,
    owner: _DescriptorOwner,
) -> tuple[int, list[tuple[int, str, int]]]:
    absolute = root if root.is_absolute() else Path.cwd() / root
    parts = absolute.parts
    if not parts or parts[0] != "/":
        raise ReplacementGenerationReaderError("SOURCE_GENERATION_INVALID")
    chain: list[tuple[int, str, int]] = []
    try:
        base_fd = owner.register(
            os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        )
        parent_fd = base_fd
        for component in parts[1:]:
            child_fd = _open_directory_at(parent_fd, component, owner=owner)
            chain.append((parent_fd, component, child_fd))
            parent_fd = child_fd
        if not chain:
            # Keep a descriptor in the normal chain ownership model even for
            # the degenerate repository root.  The synthetic name is never
            # revalidated because there is no descendant entry to inspect.
            chain.append((-1, "", base_fd))
        return parent_fd, chain
    except BaseException:
        raise


def _open_directory_at(parent_fd: int, name: str, *, owner: _DescriptorOwner) -> int:
    try:
        entry = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        if not stat.S_ISDIR(entry.st_mode):
            raise ReplacementGenerationReaderError("SOURCE_GENERATION_INVALID")
        descriptor = owner.register(
            os.open(
                name,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                dir_fd=parent_fd,
            )
        )
        opened = os.fstat(descriptor)
        if not stat.S_ISDIR(opened.st_mode) or _directory_identity(entry) != _directory_identity(opened):
            raise ReplacementGenerationReaderError("SOURCE_GENERATION_INVALID")
        return descriptor
    except ReplacementGenerationReaderError:
        raise
    except OSError as exc:
        raise ReplacementGenerationReaderError("SOURCE_GENERATION_INVALID") from exc


def _verify_directory_chain(
    chain: tuple[tuple[int, str, int], ...] | list[tuple[int, str, int]],
    error_code: str,
) -> None:
    for parent_fd, name, child_fd in chain:
        if parent_fd < 0:
            continue
        try:
            entry = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            opened = os.fstat(child_fd)
        except OSError as exc:
            raise ReplacementGenerationReaderError(error_code) from exc
        if not stat.S_ISDIR(entry.st_mode) or _directory_identity(entry) != _directory_identity(opened):
            raise ReplacementGenerationReaderError(error_code)


def _validate_authority_markers(payload: Mapping[str, Any]) -> None:
    if any(payload.get(key) != value for key, value in AUTHORITY_MARKERS.items()):
        raise ReplacementGenerationReaderError("SOURCE_GENERATION_INVALID")


def _validated_as_of(value: Any) -> str:
    if not isinstance(value, str):
        raise ReplacementGenerationReaderError("SOURCE_GENERATION_INVALID")
    failed = False
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        failed = True
        parsed = None
    if failed:
        raise ReplacementGenerationReaderError("SOURCE_GENERATION_INVALID") from None
    assert parsed is not None
    if parsed.isoformat() != value or parsed > date.today():
        raise ReplacementGenerationReaderError("SOURCE_GENERATION_INVALID")
    return value


def _parse_json_object(value: bytes, error_code: str) -> dict[str, Any]:
    failed = False
    try:
        text = value.decode("utf-8")
        parsed = json.loads(text, object_pairs_hook=_reject_duplicate_keys, parse_constant=_reject_nonfinite)
    except (_DuplicateJsonKey, UnicodeDecodeError, json.JSONDecodeError, ValueError):
        failed = True
        parsed = None
    if failed:
        raise ReplacementGenerationReaderError(error_code) from None
    if not isinstance(parsed, dict):
        raise ReplacementGenerationReaderError(error_code)
    return parsed


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJsonKey
        result[key] = value
    return result


def _reject_nonfinite(value: str) -> None:
    raise ValueError


def _valid_identifier_list(value: list[Any]) -> bool:
    return all(_canonical_nonempty_string(item) and item == item.upper() for item in value) and len(value) == len(set(value))


def _canonical_nonempty_string(value: Any) -> bool:
    return isinstance(value, str) and value != "" and value == value.strip()


def _bounded_repository_relative_path(value: str) -> bool:
    path = Path(value)
    return not path.is_absolute() and ".." not in path.parts and path.as_posix() == value


def _require_descriptor_primitives() -> None:
    required_flags = ("O_DIRECTORY", "O_NOFOLLOW", "O_CLOEXEC", "O_NONBLOCK")
    if any(not hasattr(os, flag) for flag in required_flags):
        raise ReplacementGenerationReaderError("SOURCE_GENERATION_INVALID")
    required_dir_fd = (os.open, os.stat)
    if any(function not in os.supports_dir_fd for function in required_dir_fd):
        raise ReplacementGenerationReaderError("SOURCE_GENERATION_INVALID")
    if os.listdir not in os.supports_fd:
        raise ReplacementGenerationReaderError("SOURCE_GENERATION_INVALID")


def _file_identity(value: os.stat_result) -> tuple[int, int]:
    return (value.st_dev, value.st_ino)


def _stable_file_identity(value: os.stat_result) -> tuple[int, int, int, int]:
    return (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns)


def _regular_file_state(value: os.stat_result) -> _RegularFileState:
    return _RegularFileState(
        device=value.st_dev,
        inode=value.st_ino,
        size=value.st_size,
        mode=value.st_mode,
        mtime_ns=value.st_mtime_ns,
        ctime_ns=value.st_ctime_ns,
        link_count=value.st_nlink,
    )


def _directory_identity(value: os.stat_result) -> tuple[int, int]:
    return (value.st_dev, value.st_ino)


def _json_file_bytes(payload: Mapping[str, Any]) -> bytes:
    return (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _canonical_sha256(payload: Any) -> str:
    return _sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    )


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and _GENERATION_ID_RE.fullmatch(value) is not None
