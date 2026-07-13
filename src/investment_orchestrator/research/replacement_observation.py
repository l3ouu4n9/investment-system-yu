"""R2F-1a immutable, report-only Step 1A render observation.

R2F-1a freezes bounded operator inputs, calls the production Step 1A captured-
input core once, renders a hash-bound memo prompt, and publishes one immutable
generation through retained directory descriptors.  It intentionally stops
before memo parsing, candidate compilation, availability, permission, routing,
or any order-adjacent work.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
import hashlib
import json
import os
from pathlib import Path
import stat
from typing import Any

import yaml

from investment_orchestrator.common.paths import repo_root
from investment_orchestrator.research.active_research_anchor_registry import (
    SCHEMA_VERSION as BASELINE_REGISTRY_SCHEMA_VERSION,
)
from investment_orchestrator.research.approvals_inclusive_active_registry import (
    SCHEMA_VERSION as APPROVALS_REGISTRY_SCHEMA_VERSION,
)
from investment_orchestrator.research.evidence_packet import (
    SCHEMA_VERSION as EVIDENCE_PACKET_SCHEMA_VERSION,
    check_evidence_packet_invariants,
)
from investment_orchestrator.research.research_anchor_approval_manifest import (
    MANIFEST_SCHEMA_VERSION as APPROVALS_SOURCE_SCHEMA_VERSION,
    STATUS_EXPIRED as APPROVAL_STATUS_EXPIRED,
    STATUS_REJECTED as APPROVAL_STATUS_REJECTED,
    STATUS_VALID_REPORT_ONLY as APPROVAL_STATUS_VALID,
    build_research_anchor_approvals_validation,
)
from investment_orchestrator.research.research_anchor_revocation_manifest import (
    build_research_anchor_revocations_validation,
)
from investment_orchestrator.research.research_anchors import (
    SCHEMA_VERSION as ANCHORS_SOURCE_SCHEMA_VERSION,
    ResearchAnchorsResult,
    summarize_research_anchors,
    validate_research_anchors,
)
from investment_orchestrator.state.last_good_research_handoff import (
    decision_relevant_settings,
    strategy_settings_hash,
)
from investment_orchestrator.validators.strategy_settings import parse_strategy_settings_text
from investment_orchestrator.workflow.step1a_grounding_compile import (
    build_step1a_evidence_packet_from_captured_inputs,
)


MANIFEST_SCHEMA_VERSION = "step1_replacement_input_manifest_v2"
RENDER_BINDING_SCHEMA_VERSION = "step1_replacement_render_generation_binding_v2"
GENERATION_IDENTITY_SCHEMA_VERSION = "step1_replacement_generation_identity_v2"
COMPATIBILITY_PROFILE = "step1_replacement_render_observation_v2"
CAPTURE_PROFILE = "retained_repo_and_common_parent_v1"
PROMPT_CONTRACT_SCHEMA_VERSION = "step1_replacement_prompt_contract_v2"
PROMPT_TEMPLATE_ID = "r2f_analyst_memo_content_v2"
PROMPT_RENDERER_PROFILE = "r2f_prompt_renderer_v2"
RAW_MEMO_SCHEMA_VERSION = "r2f_analyst_memo_content_v2"
PROMPT_PROJECTION_SCHEMA_VERSION = "r2f_bounded_research_prompt_projection_v2"
UNIVERSE_PROJECTION_PROFILE = "allowed_buy_then_extended_base_precedence_v1"
ACTIVE_ANCHOR_PROJECTION_PROFILE = "valid_active_registry_anchor_ids_sorted_v1"
TEXT_ENCODING_PROFILE = "utf8_lf_no_bom_terminal_newline_v1"

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
IMMUTABLE_FILENAMES = {
    "manifest": "replacement_input_manifest.json",
    "evidence_packet": "evidence_packet.json",
    "memo_prompt": "analyst_memo_prompt.txt",
}
MEMO_RAW_FILENAME = "analyst_memo_raw_output.txt"
RENDER_BINDING_FILENAME = "render_generation_binding.json"
IN_PROGRESS_FILENAME = ".render_in_progress"
COMPLETED_GENERATION_FILENAMES = frozenset(
    {
        *IMMUTABLE_FILENAMES.values(),
        MEMO_RAW_FILENAME,
        RENDER_BINDING_FILENAME,
    }
)
PRECOMMIT_GENERATION_FILENAMES = frozenset(
    {*COMPLETED_GENERATION_FILENAMES, IN_PROGRESS_FILENAME}
)
INPUT_PARENT_PATH = "inputs/current"
MEMO_PROMPT_TEMPLATE_PATH = "prompts/r2f_analyst_memo_content_v2.txt"

DOMAIN_VALID_STATUS = "DOMAIN_VALID_BUT_NONACTIVATING"
DOMAIN_INVALID_STATUS = "DOMAIN_INVALID_NO_GENERATION"
DOMAIN_INVALID_ERROR = DOMAIN_INVALID_STATUS

_AUTHORITY_MARKERS = {
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
        "prompt_contract",
        "domain_validation",
        *_AUTHORITY_MARKERS,
    }
)
_INPUT_RECORD_KEYS = frozenset(
    {"path", "file_sha256", "production_text_sha256", "source_version"}
)
_EVIDENCE_RECORD_KEYS = frozenset(
    {"schema_version", "file_sha256", "canonical_content_sha256"}
)
_REGISTRY_RECORD_KEYS = frozenset(
    {"schema_version", "canonical_content_sha256", "selected_source"}
)
_DOMAIN_VALIDATION_KEYS = frozenset({"status", "diagnostics"})
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
_BINDING_KEYS = frozenset(
    {
        "schema_version",
        "compatibility_profile",
        "generation_id",
        "scope",
        "render_complete",
        "immutable_render_artifacts",
        "operator_editable_inputs",
        "generation_identity",
        *_AUTHORITY_MARKERS,
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
_IMMUTABLE_BINDING_KEYS = frozenset(
    {"replacement_input_manifest.json", "evidence_packet.json", "analyst_memo_prompt.txt"}
)
_JSON_ARTIFACT_BINDING_KEYS = frozenset(
    {"schema_version", "file_sha256", "canonical_content_sha256", "mutable_after_render"}
)
_TEXT_ARTIFACT_BINDING_KEYS = frozenset(
    {"media_type", "file_sha256", "mutable_after_render"}
)
_EDITABLE_BINDING_KEYS = frozenset({MEMO_RAW_FILENAME})
_EDITABLE_RECORD_KEYS = frozenset(
    {
        "media_type",
        "initial_file_sha256",
        "initial_state",
        "operator_editable_after_render",
        "render_witness_attests_initial_bytes_only",
    }
)


class ReplacementObservationError(RuntimeError):
    """R2F-1a cannot safely create or verify an immutable render generation."""


def replacement_render() -> dict[str, str]:
    """Create or verify one immutable descriptor-bound R2F-1a generation."""
    root = repo_root()
    repository_chain = _open_repository_directory_chain(root)
    retained_input_descriptors: list[int] = []
    try:
        repository_fd = repository_chain[-1][2]
        repository_identity = _directory_identity(os.fstat(repository_fd))
        inputs_fd = _open_source_directory_at(repository_fd, "inputs", "source_bundle")
        retained_input_descriptors.append(inputs_fd)
        inputs_identity = _directory_identity(os.fstat(inputs_fd))
        input_parent_fd = _open_source_directory_at(inputs_fd, "current", "source_bundle")
        retained_input_descriptors.append(input_parent_fd)
        input_parent_identity = _directory_identity(os.fstat(input_parent_fd))

        # Capture the repository prompt through the retained repository identity
        # before the operator-input bundle is exposed to any later processing.
        prompt_template_bytes = _capture_memo_prompt_template(repository_fd)
        captured = _capture_inputs(input_parent_fd=input_parent_fd)
        _verify_retained_descriptor_identity(
            repository_fd,
            repository_identity,
            "REPOSITORY_IDENTITY_CHANGED",
        )
        _verify_retained_descriptor_identity(
            input_parent_fd,
            input_parent_identity,
            "INPUT_PARENT_IDENTITY_CHANGED",
        )

        strategy_text = captured["strategy_settings"]["production_text"]
        portfolio_text = captured["portfolio_snapshot"]["production_text"]
        anchors_text = captured["research_anchors"]["production_text"]
        approvals_text = captured["research_anchor_approvals"]["production_text"]
        strategy_settings = _parse_strategy_settings(strategy_text)
        as_of = _validated_as_of(strategy_settings.get("as_of"))
        generated_at = f"{as_of}T00:00:00+00:00"
        domain_validation = _validate_source_domain(
            strategy_settings=strategy_settings,
            anchors_text=anchors_text,
            approvals_text=approvals_text,
            as_of=as_of,
            generated_at=generated_at,
        )

        embedded_selection: dict[str, Any] = {}
        # The production Step 1A shared core is called exactly once.
        evidence_packet = build_step1a_evidence_packet_from_captured_inputs(
            strategy_settings=strategy_settings,
            portfolio_snapshot_text=portfolio_text,
            portfolio_snapshot_path=INPUT_PATHS["portfolio_snapshot"],
            last_good_available=False,
            last_good_metadata=None,
            research_anchors_text=anchors_text,
            research_anchors_path=INPUT_PATHS["research_anchors"],
            research_anchor_approvals_text=approvals_text,
            research_anchor_approvals_path=INPUT_PATHS["research_anchor_approvals"],
            # Raw file identity is bound in the manifest. The production evidence
            # view retains stable repository-relative paths so newline-only
            # representation changes do not alter grounding semantics.
            source_artifacts={name: record["path"] for name, record in captured.items()},
            generated_at=generated_at,
            now_date=as_of,
            embedded_selection_out=embedded_selection,
        )
        if check_evidence_packet_invariants(evidence_packet):
            raise ReplacementObservationError("PRODUCTION_EVIDENCE_INVARIANT_FAILURE")
        _validate_production_semantic_parity(
            evidence_packet=evidence_packet,
            embedded_selection=embedded_selection,
            domain_validation=domain_validation,
            anchors_text=anchors_text,
            approvals_text=approvals_text,
        )
        domain_diagnostics = _domain_diagnostics(
            evidence_packet=evidence_packet,
            domain_validation=domain_validation,
        )
        evidence_packet = _with_authority_markers(evidence_packet)
        evidence_bytes = _json_file_bytes(evidence_packet)
        evidence_file_sha = _sha256(evidence_bytes)
        evidence_canonical_sha = _canonical_sha256(evidence_packet)

        selected_registry = evidence_packet.get("active_anchor_registry")
        if not isinstance(selected_registry, Mapping):
            raise ReplacementObservationError("active_registry_missing_from_evidence_packet")
        registry_schema = selected_registry.get("schema_version")
        if registry_schema not in {
            BASELINE_REGISTRY_SCHEMA_VERSION,
            APPROVALS_REGISTRY_SCHEMA_VERSION,
        }:
            raise ReplacementObservationError("active_registry_schema_unsupported")

        prompt_contract_projection = _prompt_contract_projection(prompt_template_bytes)
        prompt_projection = _bounded_prompt_projection(
            evidence_packet=evidence_packet,
            as_of=as_of,
        )
        prompt_bytes = _render_memo_prompt_v2(
            template_bytes=prompt_template_bytes,
            prompt_projection=prompt_projection,
        )
        prompt_contract = {
            "projection": prompt_contract_projection,
            "canonical_content_sha256": _canonical_sha256(prompt_contract_projection),
            "prompt_projection_schema_version": PROMPT_PROJECTION_SCHEMA_VERSION,
            "prompt_projection_canonical_sha256": _canonical_sha256(prompt_projection),
            "analyst_memo_prompt_file_sha256": _sha256(prompt_bytes),
            "raw_memo_schema_version": RAW_MEMO_SCHEMA_VERSION,
        }

        manifest = _with_authority_markers(
            {
                "schema_version": MANIFEST_SCHEMA_VERSION,
                "compatibility_profile": COMPATIBILITY_PROFILE,
                "as_of": as_of,
                "generated_at": generated_at,
                "capture_profile": CAPTURE_PROFILE,
                "inputs": {
                    name: {
                        "path": record["path"],
                        "file_sha256": record["file_sha256"],
                        "production_text_sha256": record["production_text_sha256"],
                        "source_version": SOURCE_VERSIONS[name],
                    }
                    for name, record in captured.items()
                },
                "parsed_decision_relevant_settings_sha256": strategy_settings_hash(
                    decision_relevant_settings(strategy_settings)
                ),
                "supported_source_versions": dict(SOURCE_VERSIONS),
                "evidence_packet": {
                    "schema_version": EVIDENCE_PACKET_SCHEMA_VERSION,
                    "file_sha256": evidence_file_sha,
                    "canonical_content_sha256": evidence_canonical_sha,
                },
                "active_registry": {
                    "schema_version": registry_schema,
                    "canonical_content_sha256": _canonical_sha256(selected_registry),
                    "selected_source": embedded_selection.get("selected_source"),
                },
                "prompt_contract": prompt_contract,
                "domain_validation": {
                    "status": DOMAIN_VALID_STATUS,
                    "diagnostics": domain_diagnostics,
                },
            }
        )
        _validate_manifest(manifest)
        manifest_bytes = _json_file_bytes(manifest)
        manifest_file_sha = _sha256(manifest_bytes)
        manifest_canonical_sha = _canonical_sha256(manifest)

        generation_identity = _semantic_generation_identity(manifest)
        generation_id = _canonical_sha256(generation_identity)
        memo_raw_bytes = b""

        binding = _with_authority_markers(
            {
                "schema_version": RENDER_BINDING_SCHEMA_VERSION,
                "compatibility_profile": COMPATIBILITY_PROFILE,
                "generation_id": generation_id,
                "scope": "IMMUTABLE_RENDER_ARTIFACTS_AND_INITIAL_BLANK_MEMO_ONLY",
                "render_complete": True,
                "generation_identity": {
                    "schema_version": GENERATION_IDENTITY_SCHEMA_VERSION,
                    "prompt_contract_canonical_sha256": prompt_contract[
                        "canonical_content_sha256"
                    ],
                    "analyst_memo_prompt_file_sha256": _sha256(prompt_bytes),
                    "raw_memo_schema_version": RAW_MEMO_SCHEMA_VERSION,
                },
                "immutable_render_artifacts": {
                    "replacement_input_manifest.json": {
                        "schema_version": MANIFEST_SCHEMA_VERSION,
                        "file_sha256": manifest_file_sha,
                        "canonical_content_sha256": manifest_canonical_sha,
                        "mutable_after_render": False,
                    },
                    "evidence_packet.json": {
                        "schema_version": EVIDENCE_PACKET_SCHEMA_VERSION,
                        "file_sha256": evidence_file_sha,
                        "canonical_content_sha256": evidence_canonical_sha,
                        "mutable_after_render": False,
                    },
                    "analyst_memo_prompt.txt": {
                        "media_type": "text/plain; charset=utf-8",
                        "file_sha256": _sha256(prompt_bytes),
                        "mutable_after_render": False,
                    },
                },
                "operator_editable_inputs": {
                    MEMO_RAW_FILENAME: {
                        "media_type": "text/plain; charset=utf-8",
                        "initial_file_sha256": _sha256(memo_raw_bytes),
                        "initial_state": "BLANK",
                        "operator_editable_after_render": True,
                        "render_witness_attests_initial_bytes_only": True,
                    }
                },
            }
        )
        _validate_render_binding(binding, expected_generation_id=generation_id)
        binding_bytes = _json_file_bytes(binding)

        immutable_files = {
            IMMUTABLE_FILENAMES["manifest"]: manifest_bytes,
            IMMUTABLE_FILENAMES["evidence_packet"]: evidence_bytes,
            IMMUTABLE_FILENAMES["memo_prompt"]: prompt_bytes,
        }
        generation_path = root.joinpath(*R2F_ROOT_PARTS, GENERATIONS_DIRECTORY, generation_id)
        common_result = {
            "generation_id": generation_id,
            "generation_path": str(generation_path),
            "replacement_input_manifest_path": str(
                generation_path / IMMUTABLE_FILENAMES["manifest"]
            ),
            "evidence_packet_path": str(
                generation_path / IMMUTABLE_FILENAMES["evidence_packet"]
            ),
            "analyst_memo_prompt_path": str(
                generation_path / IMMUTABLE_FILENAMES["memo_prompt"]
            ),
            "analyst_memo_raw_output_path": str(generation_path / MEMO_RAW_FILENAME),
            "render_generation_binding_path": str(
                generation_path / RENDER_BINDING_FILENAME
            ),
            "cli_output": str(generation_path / IMMUTABLE_FILENAMES["memo_prompt"]),
        }
        new_result = {**common_result, "generation_reused": "false"}
        reused_result = {**common_result, "generation_reused": "true"}
        return _publish_or_verify_generation(
            repository_fd=repository_fd,
            repository_chain=repository_chain,
            inputs_fd=inputs_fd,
            input_parent_fd=input_parent_fd,
            repository_identity=repository_identity,
            inputs_identity=inputs_identity,
            input_parent_identity=input_parent_identity,
            generation_id=generation_id,
            immutable_files=immutable_files,
            memo_raw_bytes=memo_raw_bytes,
            binding_bytes=binding_bytes,
            new_result=new_result,
            reused_result=reused_result,
        )
    finally:
        try:
            for descriptor in reversed(retained_input_descriptors):
                _cleanup_noexcept(_close_fd_noexcept, descriptor)
        except BaseException:  # noqa: BLE001 - outer cleanup cannot alter result
            pass
        try:
            _cleanup_noexcept(_close_directory_chain_noexcept, repository_chain)
        except BaseException:  # noqa: BLE001 - even an injected dispatcher is local
            pass


def _capture_inputs(*, input_parent_fd: int) -> dict[str, dict[str, Any]]:
    """Capture all bounded sources through one retained ``inputs/current`` fd."""
    captured: dict[str, dict[str, Any]] = {}
    for name, relative_path in INPUT_PATHS.items():
        if not _bounded_repository_relative_path(relative_path):
            raise ReplacementObservationError("SOURCE_PATH_INVALID")
        parts = Path(relative_path).parts
        if parts[:-1] != tuple(INPUT_PARENT_PATH.split("/")):
            raise ReplacementObservationError("SOURCE_PATH_INVALID")
        value = _read_source_file_at(
            input_parent_fd=input_parent_fd,
            filename=parts[-1],
            source_name=name,
        )
        if not value.strip():
            raise ReplacementObservationError(f"SOURCE_BLANK:{name}")
        decoded = _decode(value, name)
        production_text = _normalize_production_text_newlines(decoded)
        captured[name] = {
            "path": relative_path,
            "file_sha256": _sha256(value),
            "production_text_sha256": _sha256(production_text.encode("utf-8")),
            "bytes": value,
            "production_text": production_text,
        }
    return captured


def _read_source_file_at(
    *,
    input_parent_fd: int,
    filename: str,
    source_name: str,
) -> bytes:
    """Read one repository source without following any pathname component.

    ``O_NOFOLLOW`` and retained parent descriptors provide containment.  The
    no-follow entry/descriptor identity comparison additionally rejects an
    ordinary regular-file swap between discovery and open; bytes are not read
    until the comparison succeeds.
    """
    return _read_stable_regular_file_at(
        directory_fd=input_parent_fd,
        filename=filename,
        source_name=source_name,
    )


def _read_stable_regular_file_at(
    *,
    directory_fd: int,
    filename: str,
    source_name: str,
) -> bytes:
    """Read stable bytes from one no-follow regular entry under a retained fd."""
    descriptor: int | None = None
    try:
        try:
            entry = os.stat(filename, dir_fd=directory_fd, follow_symlinks=False)
        except OSError as exc:
            raise ReplacementObservationError(f"SOURCE_UNAVAILABLE:{source_name}") from exc
        if not stat.S_ISREG(entry.st_mode):
            raise ReplacementObservationError(f"SOURCE_SYMLINK_OR_NONREGULAR:{source_name}")
        flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK
        try:
            descriptor = os.open(filename, flags, dir_fd=directory_fd)
        except OSError as exc:
            raise ReplacementObservationError(f"SOURCE_OPEN_FAILED:{source_name}") from exc
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode):
            raise ReplacementObservationError(f"SOURCE_SYMLINK_OR_NONREGULAR:{source_name}")
        if (entry.st_dev, entry.st_ino) != (opened.st_dev, opened.st_ino):
            raise ReplacementObservationError(f"SOURCE_IDENTITY_CHANGED:{source_name}")
        before = _source_stat_identity(opened)
        try:
            value = _read_all_descriptor_bytes(descriptor)
            after = _source_stat_identity(os.fstat(descriptor))
        except OSError as exc:
            raise ReplacementObservationError(f"SOURCE_READ_FAILED:{source_name}") from exc
        if before != after:
            raise ReplacementObservationError(f"SOURCE_IDENTITY_CHANGED:{source_name}")
        return value
    finally:
        if descriptor is not None:
            _cleanup_noexcept(_close_fd_noexcept, descriptor)


def _capture_memo_prompt_template(repository_fd: int) -> bytes:
    prompt_directory_fd = _open_source_directory_at(
        repository_fd,
        "prompts",
        "analyst_memo_prompt_template",
    )
    try:
        value = _read_stable_regular_file_at(
            directory_fd=prompt_directory_fd,
            filename="r2f_analyst_memo_content_v2.txt",
            source_name="analyst_memo_prompt_template",
        )
        if (
            not value.strip()
            or value.startswith(b"\xef\xbb\xbf")
            or b"\r" in value
            or not value.endswith(b"\n")
        ):
            raise ReplacementObservationError("analyst_memo_prompt_template_unsupported")
        text = _decode(value, "analyst_memo_prompt_template")
        if text.count("{{ prompt_projection_json }}") != 1:
            raise ReplacementObservationError("analyst_memo_prompt_template_unsupported")
        return value
    finally:
        _cleanup_noexcept(_close_fd_noexcept, prompt_directory_fd)


def _open_source_directory_at(parent_fd: int, name: str, source_name: str) -> int:
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    descriptor: int | None = None
    try:
        entry = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        if not stat.S_ISDIR(entry.st_mode):
            raise ReplacementObservationError(f"SOURCE_PARENT_INVALID:{source_name}")
        descriptor = os.open(name, flags, dir_fd=parent_fd)
        opened = os.fstat(descriptor)
    except OSError as exc:
        if descriptor is not None:
            _cleanup_noexcept(_close_fd_noexcept, descriptor)
        raise ReplacementObservationError(f"SOURCE_PARENT_INVALID:{source_name}") from exc
    assert descriptor is not None  # narrowed after successful descriptor open/fstat
    if not stat.S_ISDIR(opened.st_mode):
        _cleanup_noexcept(_close_fd_noexcept, descriptor)
        raise ReplacementObservationError(f"SOURCE_PARENT_INVALID:{source_name}")
    if (entry.st_dev, entry.st_ino) != (opened.st_dev, opened.st_ino):
        _cleanup_noexcept(_close_fd_noexcept, descriptor)
        raise ReplacementObservationError(f"SOURCE_PARENT_IDENTITY_CHANGED:{source_name}")
    return descriptor


def _read_all_descriptor_bytes(descriptor: int) -> bytes:
    chunks: list[bytes] = []
    while True:
        chunk = os.read(descriptor, 65536)
        if not chunk:
            return b"".join(chunks)
        chunks.append(chunk)


def _source_stat_identity(value: os.stat_result) -> tuple[int, int, int, int]:
    # A pathname-only rename may update ctime while the retained descriptor still
    # binds the same unchanged bytes. Size/mtime detect ordinary in-place content
    # mutation without turning that safe rename into a false failure.
    return (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns)


def _normalize_production_text_newlines(value: str) -> str:
    """Apply Python text-mode universal-newline semantics deterministically."""
    return value.replace("\r\n", "\n").replace("\r", "\n")


def _validate_source_domain(
    *,
    strategy_settings: Mapping[str, Any],
    anchors_text: str,
    approvals_text: str,
    as_of: str,
    generated_at: str,
) -> dict[str, Any]:
    """Apply existing deterministic source validators before publication."""
    try:
        anchors = yaml.safe_load(anchors_text)
        approvals = yaml.safe_load(approvals_text)
    except yaml.YAMLError as exc:
        raise ReplacementObservationError(DOMAIN_INVALID_ERROR) from exc
    if not isinstance(anchors, Mapping) or anchors.get("schema_version") != ANCHORS_SOURCE_SCHEMA_VERSION:
        raise ReplacementObservationError(DOMAIN_INVALID_ERROR)
    if not isinstance(approvals, Mapping) or approvals.get("schema_version") != APPROVALS_SOURCE_SCHEMA_VERSION:
        raise ReplacementObservationError(DOMAIN_INVALID_ERROR)

    allowed_universe = _allowed_universe_from_settings(strategy_settings)
    anchors_result = validate_research_anchors(
        anchors,
        allowed_universe=allowed_universe,
        today=as_of,
    )
    if anchors_result.valid is not True:
        raise ReplacementObservationError(DOMAIN_INVALID_ERROR)

    approvals_sha = _sha256(approvals_text.encode("utf-8"))
    approvals_validation = build_research_anchor_approvals_validation(
        manifest=approvals,
        source_present=True,
        source_sha256=approvals_sha,
        source_path=INPUT_PATHS["research_anchor_approvals"],
        allowed_universe=allowed_universe,
        today=as_of,
        as_of_date=anchors_result.as_of_date or as_of,
        generated_at=generated_at,
    )
    if approvals_validation.get("source_valid") is not True:
        raise ReplacementObservationError(DOMAIN_INVALID_ERROR)
    for result in _mapping_rows(approvals_validation.get("approval_results")):
        status = result.get("status")
        if status in {APPROVAL_STATUS_VALID, APPROVAL_STATUS_EXPIRED}:
            continue
        if status == APPROVAL_STATUS_REJECTED and _is_valid_hash_mismatch_observation(result):
            continue
        raise ReplacementObservationError(DOMAIN_INVALID_ERROR)

    revocations_validation = build_research_anchor_revocations_validation(
        manifest=approvals,
        approvals_validation=approvals_validation,
        source_present=True,
        source_sha256=approvals_sha,
        source_path=INPUT_PATHS["research_anchor_approvals"],
        today=as_of,
        as_of_date=anchors_result.as_of_date or as_of,
        generated_at=generated_at,
    )
    if revocations_validation.get("revocations_valid") is not True:
        raise ReplacementObservationError(DOMAIN_INVALID_ERROR)
    return {
        "anchors_result": anchors_result,
        "approvals_validation": approvals_validation,
        "revocations_validation": revocations_validation,
    }


def _allowed_universe_from_settings(strategy_settings: Mapping[str, Any]) -> list[str]:
    values: list[str] = []
    for key in ("core_universe", "satellite_universe"):
        raw = strategy_settings.get(key)
        if not isinstance(raw, list):
            continue
        for item in raw:
            if isinstance(item, str) and item.strip():
                ticker = item.strip().upper()
                if ticker not in values:
                    values.append(ticker)
    return values


def _mapping_rows(value: Any) -> list[Mapping[str, Any]]:
    if not isinstance(value, list):
        return []
    return [row for row in value if isinstance(row, Mapping)]


def _is_valid_hash_mismatch_observation(result: Mapping[str, Any]) -> bool:
    errors = result.get("approval_errors")
    return (
        result.get("decision") == "approve"
        and result.get("validation_valid") is True
        and result.get("hash_match") is False
        and _is_sha256(result.get("operator_completed_anchor_sha256"))
        and _is_sha256(result.get("recomputed_operator_completed_anchor_sha256"))
        and isinstance(errors, list)
        and len(errors) == 1
        and isinstance(errors[0], str)
        and errors[0].startswith("operator_completed_anchor_sha256 mismatch:")
    )


def _validate_production_semantic_parity(
    *,
    evidence_packet: Mapping[str, Any],
    embedded_selection: Mapping[str, Any],
    domain_validation: Mapping[str, Any],
    anchors_text: str,
    approvals_text: str,
) -> None:
    anchors_result = domain_validation.get("anchors_result")
    if not isinstance(anchors_result, ResearchAnchorsResult):
        raise ReplacementObservationError("PRODUCTION_PARITY_FAILURE")
    expected_summary = summarize_research_anchors(
        anchors_result,
        path=INPUT_PATHS["research_anchors"],
    )
    if evidence_packet.get("research_anchors") != expected_summary:
        raise ReplacementObservationError("PRODUCTION_PARITY_FAILURE")
    selected = embedded_selection.get("selected_registry")
    if not isinstance(selected, Mapping) or evidence_packet.get("active_anchor_registry") != selected:
        raise ReplacementObservationError("PRODUCTION_PARITY_FAILURE")

    expected_anchor_sha = _sha256(anchors_text.encode("utf-8"))
    expected_approvals_sha = _sha256(approvals_text.encode("utf-8"))
    baseline = embedded_selection.get("baseline_registry")
    approvals_registry = embedded_selection.get("approvals_registry")
    if (
        _source_hash_for_path(baseline, INPUT_PATHS["research_anchors"])
        != expected_anchor_sha
        or _source_hash_for_path(approvals_registry, INPUT_PATHS["research_anchors"])
        != expected_anchor_sha
        or _source_hash_for_path(
            approvals_registry,
            INPUT_PATHS["research_anchor_approvals"],
        )
        != expected_approvals_sha
    ):
        raise ReplacementObservationError("PRODUCTION_PARITY_FAILURE")


def _source_hash_for_path(registry: Any, expected_path: str) -> str | None:
    if not isinstance(registry, Mapping):
        return None
    manifest = registry.get("source_manifest")
    for record in _mapping_rows(manifest):
        if record.get("path") == expected_path and isinstance(record.get("sha256"), str):
            return record["sha256"]
    return None


def _domain_diagnostics(
    *,
    evidence_packet: Mapping[str, Any],
    domain_validation: Mapping[str, Any],
) -> list[str]:
    diagnostics: set[str] = set()
    approvals = domain_validation.get("approvals_validation")
    if isinstance(approvals, Mapping):
        results = _mapping_rows(approvals.get("approval_results"))
        if not results:
            diagnostics.add("NO_APPROVALS")
        for result in results:
            if result.get("status") == APPROVAL_STATUS_EXPIRED:
                diagnostics.add("EXPIRED_OR_INACTIVE_APPROVAL")
            elif _is_valid_hash_mismatch_observation(result):
                diagnostics.add("APPROVAL_HASH_MISMATCH")
    revocations = domain_validation.get("revocations_validation")
    if isinstance(revocations, Mapping) and _mapping_rows(revocations.get("revocation_results")):
        diagnostics.add("REVOCATION_PRESENT")
    registry = evidence_packet.get("active_anchor_registry")
    if not isinstance(registry, Mapping) or not _mapping_rows(registry.get("active_anchors")):
        diagnostics.add("EMPTY_ACTIVE_REGISTRY")
    market = evidence_packet.get("market_metrics")
    if not isinstance(market, Mapping) or market.get("available") is not True:
        diagnostics.add("MARKET_METRICS_UNAVAILABLE")
    events = evidence_packet.get("scheduled_events_deterministic")
    if not isinstance(events, Mapping) or events.get("available") is not True:
        diagnostics.add("SCHEDULED_EVENTS_UNAVAILABLE")
    portfolio = evidence_packet.get("portfolio_snapshot_summary")
    if not isinstance(portfolio, Mapping) or any(
        isinstance(portfolio.get(key), Mapping)
        and portfolio[key].get("structured_parse_available") is False
        for key in ("current_holdings", "sell_open_orders", "ltcg_sellable_lots")
    ):
        diagnostics.add("PORTFOLIO_COVERAGE_INCOMPLETE")
    return sorted(diagnostics)


def _prompt_contract_projection(template_bytes: bytes) -> dict[str, str]:
    return {
        "schema_version": PROMPT_CONTRACT_SCHEMA_VERSION,
        "template_id": PROMPT_TEMPLATE_ID,
        "template_file_sha256": _sha256(template_bytes),
        "renderer_profile": PROMPT_RENDERER_PROFILE,
        "raw_memo_schema_version": RAW_MEMO_SCHEMA_VERSION,
        "evidence_projection_profile": PROMPT_PROJECTION_SCHEMA_VERSION,
        "universe_projection_profile": UNIVERSE_PROJECTION_PROFILE,
        "active_anchor_projection_profile": ACTIVE_ANCHOR_PROJECTION_PROFILE,
        "text_encoding_profile": TEXT_ENCODING_PROFILE,
    }


def _bounded_prompt_projection(
    *, evidence_packet: Mapping[str, Any], as_of: str
) -> dict[str, Any]:
    universe = evidence_packet.get("universe")
    registry = evidence_packet.get("active_anchor_registry")
    if not isinstance(universe, Mapping) or not isinstance(registry, Mapping):
        raise ReplacementObservationError("prompt_projection_source_invalid")
    if registry.get("registry_valid") is not True:
        raise ReplacementObservationError("prompt_projection_registry_invalid")

    eligible: list[dict[str, str]] = []
    seen: set[str] = set()
    for key, category in (
        ("allowed_buy_tickers", "BASE_EVIDENCE_UNIVERSE"),
        ("approved_extended_etf", "APPROVED_EXTENDED_OBSERVATION_ONLY"),
    ):
        values = universe.get(key)
        if not isinstance(values, list):
            raise ReplacementObservationError("prompt_projection_universe_invalid")
        for instrument_id in values:
            if not isinstance(instrument_id, str) or not instrument_id or instrument_id in seen:
                if instrument_id in seen:
                    continue
                raise ReplacementObservationError("prompt_projection_universe_invalid")
            seen.add(instrument_id)
            eligible.append(
                {"instrument_id": instrument_id, "universe_category": category}
            )

    active_rows = registry.get("active_anchors")
    if not isinstance(active_rows, list):
        raise ReplacementObservationError("prompt_projection_registry_invalid")
    active_anchors: list[dict[str, Any]] = []
    active_ids: set[str] = set()
    for row in active_rows:
        if not isinstance(row, Mapping):
            raise ReplacementObservationError("prompt_projection_registry_invalid")
        anchor_id = row.get("anchor_id")
        if not isinstance(anchor_id, str) or not anchor_id or anchor_id in active_ids:
            raise ReplacementObservationError("prompt_projection_registry_invalid")
        active_ids.add(anchor_id)
        active_anchors.append(
            {
                "anchor_id": anchor_id,
                "applicable_tickers": list(row.get("applicable_tickers") or []),
                "anchor_date_et": row.get("anchor_date_et"),
                "valid_from": row.get("valid_from"),
                "valid_until": row.get("valid_until"),
                "confidence_floor": row.get("confidence_floor"),
                "summary": row.get("summary"),
            }
        )
    active_anchors.sort(key=lambda row: row["anchor_id"])

    research_context = {
        "market_metrics": _bounded_availability_context(
            evidence_packet.get("market_metrics")
        ),
        "scheduled_events_deterministic": _bounded_availability_context(
            evidence_packet.get("scheduled_events_deterministic")
        ),
    }
    return {
        "schema_version": PROMPT_PROJECTION_SCHEMA_VERSION,
        "as_of": as_of,
        "eligible_instruments": eligible,
        "active_anchors": active_anchors,
        "research_context": research_context,
    }


def _bounded_availability_context(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ReplacementObservationError("prompt_projection_context_invalid")
    data_gap = value.get("data_gap")
    if data_gap is not None and not isinstance(data_gap, str):
        raise ReplacementObservationError("prompt_projection_context_invalid")
    return {
        "available": value.get("available") is True,
        "data_gap": data_gap,
    }


def _render_memo_prompt_v2(
    *, template_bytes: bytes, prompt_projection: Mapping[str, Any]
) -> bytes:
    if (
        template_bytes.startswith(b"\xef\xbb\xbf")
        or b"\r" in template_bytes
        or not template_bytes.endswith(b"\n")
    ):
        raise ReplacementObservationError("analyst_memo_prompt_template_unsupported")
    template = _decode(template_bytes, "analyst_memo_prompt_template")
    placeholder = "{{ prompt_projection_json }}"
    if template.count(placeholder) != 1:
        raise ReplacementObservationError("analyst_memo_prompt_template_unsupported")
    projection_json = json.dumps(
        prompt_projection,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )
    rendered = template.replace(placeholder, projection_json)
    return rendered.encode("utf-8")


def _publish_or_verify_generation(
    *,
    repository_fd: int,
    repository_chain: list[tuple[int, str, int]],
    inputs_fd: int,
    input_parent_fd: int,
    repository_identity: tuple[int, int],
    inputs_identity: tuple[int, int],
    input_parent_identity: tuple[int, int],
    generation_id: str,
    immutable_files: Mapping[str, bytes],
    memo_raw_bytes: bytes,
    binding_bytes: bytes,
    new_result: dict[str, str],
    reused_result: dict[str, str],
) -> dict[str, str]:
    output_chain: list[tuple[int, str, int]] = []
    publication_committed = False
    try:
        parent_fd = repository_fd
        for component in (*R2F_ROOT_PARTS, GENERATIONS_DIRECTORY):
            child_fd = _open_or_create_directory_at(parent_fd, component)
            output_chain.append((parent_fd, component, child_fd))
            parent_fd = child_fd
        generations_fd = parent_fd
        generation_created = _mkdir_exclusive_at(generations_fd, generation_id)
        if generation_created:
            _fsync_directory(generations_fd, "PARENT_FSYNC_FAILURE")
        generation_fd = _open_directory_at(generations_fd, generation_id)
        output_chain.append((generations_fd, generation_id, generation_fd))
        if generation_created:
            try:
                _create_in_progress_marker_at(generation_fd)
                for filename, content in immutable_files.items():
                    _atomic_create_file_at(
                        generation_fd,
                        filename,
                        content,
                        containment_guard=lambda: _verify_publication_paths(
                            repository_chain,
                            output_chain,
                        ),
                    )
                _atomic_create_file_at(
                    generation_fd,
                    MEMO_RAW_FILENAME,
                    memo_raw_bytes,
                    containment_guard=lambda: _verify_publication_paths(
                        repository_chain,
                        output_chain,
                    ),
                )
                # The content witness is durable and verified while the explicit
                # in-progress marker still makes reuse impossible.
                _atomic_create_file_at(
                    generation_fd,
                    RENDER_BINDING_FILENAME,
                    binding_bytes,
                    containment_guard=lambda: _verify_publication_paths(
                        repository_chain,
                        output_chain,
                    ),
                )
                _fsync_directory(generation_fd, "BINDING_DURABILITY_FAILURE")
                _fsync_directory(generations_fd, "PARENT_FSYNC_FAILURE")
                _verify_precommit_generation(
                    generation_fd=generation_fd,
                    generation_id=generation_id,
                    immutable_files=immutable_files,
                    binding_bytes=binding_bytes,
                    expected_initial_memo_raw=memo_raw_bytes,
                )
                _verify_publication_paths(repository_chain, output_chain)
                _revalidate_retained_paths(
                    repository_chain=repository_chain,
                    repository_fd=repository_fd,
                    inputs_fd=inputs_fd,
                    input_parent_fd=input_parent_fd,
                    repository_identity=repository_identity,
                    inputs_identity=inputs_identity,
                    input_parent_identity=input_parent_identity,
                )
            except ReplacementObservationError:
                raise
            except BaseException as exc:
                raise ReplacementObservationError("GENERATION_PUBLICATION_FAILURE") from exc
            # Logical commit point: every fallible durability and verification
            # operation is complete. No recovery or filesystem operation follows
            # a successful unlink; descriptor closes in the outer finally are
            # best-effort resource release and cannot change the result.
            _remove_in_progress_marker_at(generation_fd)
            publication_committed = True
            return new_result
        _verify_existing_generation(
            generation_fd=generation_fd,
            generation_id=generation_id,
            immutable_files=immutable_files,
            binding_bytes=binding_bytes,
            expected_initial_memo_raw=None,
        )
        _verify_publication_paths(repository_chain, output_chain)
        _revalidate_retained_paths(
            repository_chain=repository_chain,
            repository_fd=repository_fd,
            inputs_fd=inputs_fd,
            input_parent_fd=input_parent_fd,
            repository_identity=repository_identity,
            inputs_identity=inputs_identity,
            input_parent_identity=input_parent_identity,
        )
        return reused_result
    finally:
        # This executes during Python return transfer, including after commit.
        # Both the dispatcher and cleanup target are explicitly no-throw so no
        # cleanup test double can convert committed publication into failure.
        try:
            _cleanup_noexcept(_close_directory_chain_noexcept, output_chain)
        except BaseException:  # noqa: BLE001 - even an injected dispatcher is local
            pass


def _verify_precommit_generation(
    *,
    generation_fd: int,
    generation_id: str,
    immutable_files: Mapping[str, bytes],
    binding_bytes: bytes,
    expected_initial_memo_raw: bytes,
) -> None:
    names = _generation_entry_names(generation_fd)
    if names != PRECOMMIT_GENERATION_FILENAMES:
        raise ReplacementObservationError("GENERATION_INVENTORY_MISMATCH")
    for filename in PRECOMMIT_GENERATION_FILENAMES:
        if not _entry_is_regular_at(generation_fd, filename):
            raise ReplacementObservationError("GENERATION_INVENTORY_MISMATCH")
    _verify_generation_payloads(
        generation_fd=generation_fd,
        generation_id=generation_id,
        immutable_files=immutable_files,
        binding_bytes=binding_bytes,
        expected_initial_memo_raw=expected_initial_memo_raw,
    )


def _verify_existing_generation(
    *,
    generation_fd: int,
    generation_id: str,
    immutable_files: Mapping[str, bytes],
    binding_bytes: bytes,
    expected_initial_memo_raw: bytes | None,
) -> None:
    names = _generation_entry_names(generation_fd)
    if IN_PROGRESS_FILENAME in names or RENDER_BINDING_FILENAME not in names:
        raise ReplacementObservationError("INCOMPLETE_GENERATION_PRESENT")
    if names != COMPLETED_GENERATION_FILENAMES:
        raise ReplacementObservationError("GENERATION_INVENTORY_MISMATCH")
    for filename in COMPLETED_GENERATION_FILENAMES:
        if not _entry_is_regular_at(generation_fd, filename):
            raise ReplacementObservationError("GENERATION_INVENTORY_MISMATCH")
    _verify_generation_payloads(
        generation_fd=generation_fd,
        generation_id=generation_id,
        immutable_files=immutable_files,
        binding_bytes=binding_bytes,
        expected_initial_memo_raw=expected_initial_memo_raw,
    )


def _verify_generation_payloads(
    *,
    generation_fd: int,
    generation_id: str,
    immutable_files: Mapping[str, bytes],
    binding_bytes: bytes,
    expected_initial_memo_raw: bytes | None,
) -> None:
    for filename, expected in immutable_files.items():
        if _read_regular_file_at(generation_fd, filename) != expected:
            raise ReplacementObservationError("IMMUTABLE_ARTIFACT_HASH_MISMATCH")
    actual_binding = _read_regular_file_at(generation_fd, RENDER_BINDING_FILENAME)
    binding = _parse_json_object(actual_binding, "render_generation_binding_invalid")
    _validate_render_binding(binding, expected_generation_id=generation_id)
    if actual_binding != binding_bytes:
        raise ReplacementObservationError("BINDING_HASH_MISMATCH")
    # Memo raw is intentionally editable; only regular-file/no-follow identity is
    # checked on reuse. The binding attests its initial blank bytes, not its
    # current operator-edited content.
    if expected_initial_memo_raw is None:
        _verify_regular_file_at(generation_fd, MEMO_RAW_FILENAME)
    elif _read_regular_file_at(generation_fd, MEMO_RAW_FILENAME) != expected_initial_memo_raw:
        raise ReplacementObservationError("INITIAL_MEMO_STATE_MISMATCH")


def _generation_entry_names(generation_fd: int) -> frozenset[str]:
    try:
        names = os.listdir(generation_fd)
    except OSError as exc:
        raise ReplacementObservationError("GENERATION_INVENTORY_MISMATCH") from exc
    if any(not isinstance(name, str) for name in names):
        raise ReplacementObservationError("GENERATION_INVENTORY_MISMATCH")
    return frozenset(names)


def _entry_is_regular_at(directory_fd: int, filename: str) -> bool:
    try:
        entry = os.stat(filename, dir_fd=directory_fd, follow_symlinks=False)
    except OSError:
        return False
    return stat.S_ISREG(entry.st_mode)


def _create_in_progress_marker_at(generation_fd: int) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC
    descriptor: int | None = None
    try:
        descriptor = os.open(IN_PROGRESS_FILENAME, flags, 0o600, dir_fd=generation_fd)
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise ReplacementObservationError("IN_PROGRESS_MARKER_INVALID")
        os.fsync(descriptor)
    except ReplacementObservationError:
        raise
    except OSError as exc:
        raise ReplacementObservationError("IN_PROGRESS_MARKER_CREATE_FAILED") from exc
    finally:
        if descriptor is not None:
            _cleanup_noexcept(_close_fd_noexcept, descriptor)
    _fsync_directory(generation_fd, "BINDING_DURABILITY_FAILURE")


def _remove_in_progress_marker_at(generation_fd: int) -> None:
    if not _entry_is_regular_at(generation_fd, IN_PROGRESS_FILENAME):
        raise ReplacementObservationError("IN_PROGRESS_MARKER_INVALID")
    try:
        os.unlink(IN_PROGRESS_FILENAME, dir_fd=generation_fd)
    except OSError as exc:
        raise ReplacementObservationError("IN_PROGRESS_MARKER_REMOVE_FAILED") from exc


def _fsync_directory(directory_fd: int, error_code: str) -> None:
    try:
        os.fsync(directory_fd)
    except OSError as exc:
        raise ReplacementObservationError(error_code) from exc


def _open_repository_directory_chain(root: Path) -> list[tuple[int, str, int]]:
    _require_descriptor_primitives()
    if not root.is_absolute():
        raise ReplacementObservationError("repository_root_must_be_absolute")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    filesystem_fd = os.open("/", flags)
    chain: list[tuple[int, str, int]] = [(-1, "/", filesystem_fd)]
    parent_fd = filesystem_fd
    try:
        for component in root.parts[1:]:
            child_fd = os.open(component, flags, dir_fd=parent_fd)
            if not stat.S_ISDIR(os.fstat(child_fd).st_mode):
                _cleanup_noexcept(_close_fd_noexcept, child_fd)
                raise ReplacementObservationError("repository_component_not_directory")
            chain.append((parent_fd, component, child_fd))
            parent_fd = child_fd
        return chain
    except OSError as exc:
        _cleanup_noexcept(_close_directory_chain_noexcept, chain)
        raise ReplacementObservationError("repository_component_open_failed") from exc


def _open_or_create_directory_at(parent_fd: int, name: str) -> int:
    created = False
    try:
        os.mkdir(name, mode=0o700, dir_fd=parent_fd)
        created = True
    except FileExistsError:
        pass
    except OSError as exc:
        raise ReplacementObservationError("output_directory_create_failed") from exc
    if created:
        _fsync_directory(parent_fd, "PARENT_FSYNC_FAILURE")
    return _open_directory_at(parent_fd, name)


def _mkdir_exclusive_at(parent_fd: int, name: str) -> bool:
    try:
        os.mkdir(name, mode=0o700, dir_fd=parent_fd)
        return True
    except FileExistsError:
        return False
    except OSError as exc:
        raise ReplacementObservationError("generation_directory_create_failed") from exc


def _open_directory_at(parent_fd: int, name: str) -> int:
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    try:
        descriptor = os.open(name, flags, dir_fd=parent_fd)
    except OSError as exc:
        raise ReplacementObservationError("output_directory_open_failed") from exc
    if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
        _cleanup_noexcept(_close_fd_noexcept, descriptor)
        raise ReplacementObservationError("output_entry_not_directory")
    return descriptor


def _atomic_create_file_at(
    directory_fd: int,
    filename: str,
    content: bytes,
    *,
    containment_guard: Any = None,
) -> None:
    temporary = f".{filename}.r2f1a.tmp"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC
    descriptor: int | None = None
    try:
        if containment_guard is not None:
            containment_guard()
        try:
            descriptor = os.open(temporary, flags, 0o600, dir_fd=directory_fd)
        except OSError as exc:
            raise ReplacementObservationError("OUTPUT_TEMP_CREATE_FAILED") from exc
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise ReplacementObservationError("temporary_output_not_regular")
        offset = 0
        while offset < len(content):
            written = os.write(descriptor, content[offset:])
            if written <= 0:
                raise ReplacementObservationError("output_write_incomplete")
            offset += written
        try:
            os.fsync(descriptor)
        except OSError as exc:
            raise ReplacementObservationError("OUTPUT_FILE_DURABILITY_FAILURE") from exc
        _cleanup_noexcept(_close_fd_noexcept, descriptor)
        descriptor = None
        try:
            os.rename(
                temporary,
                filename,
                src_dir_fd=directory_fd,
                dst_dir_fd=directory_fd,
            )
        except OSError as exc:
            raise ReplacementObservationError("OUTPUT_RENAME_FAILURE") from exc
        _fsync_directory(
            directory_fd,
            "BINDING_DURABILITY_FAILURE"
            if filename == RENDER_BINDING_FILENAME
            else "ARTIFACT_DURABILITY_FAILURE",
        )
    except Exception:
        if descriptor is not None:
            _cleanup_noexcept(_close_fd_noexcept, descriptor)
        _cleanup_noexcept(_unlink_at_noexcept, directory_fd, temporary)
        raise


def _read_regular_file_at(directory_fd: int, filename: str) -> bytes:
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK
    try:
        descriptor = os.open(filename, flags, dir_fd=directory_fd)
    except OSError as exc:
        raise ReplacementObservationError("GENERATION_ARTIFACT_OPEN_FAILED") from exc
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise ReplacementObservationError("GENERATION_ARTIFACT_NOT_REGULAR")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 65536)
            if not chunk:
                return b"".join(chunks)
            chunks.append(chunk)
    finally:
        _cleanup_noexcept(_close_fd_noexcept, descriptor)


def _verify_regular_file_at(directory_fd: int, filename: str) -> None:
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK
    try:
        descriptor = os.open(filename, flags, dir_fd=directory_fd)
    except OSError as exc:
        raise ReplacementObservationError("GENERATION_ARTIFACT_OPEN_FAILED") from exc
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise ReplacementObservationError("GENERATION_ARTIFACT_NOT_REGULAR")
    finally:
        _cleanup_noexcept(_close_fd_noexcept, descriptor)


def _verify_directory_chain(
    chain: list[tuple[int, str, int]],
    error_code: str = "output_directory_identity_changed",
) -> None:
    for parent_fd, name, child_fd in chain:
        if parent_fd < 0:
            continue
        try:
            entry = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            opened = os.fstat(child_fd)
        except OSError as exc:
            raise ReplacementObservationError(error_code) from exc
        if (
            not stat.S_ISDIR(entry.st_mode)
            or entry.st_dev != opened.st_dev
            or entry.st_ino != opened.st_ino
        ):
            raise ReplacementObservationError(error_code)


def _verify_publication_paths(
    repository_chain: list[tuple[int, str, int]],
    output_chain: list[tuple[int, str, int]],
) -> None:
    _verify_directory_chain(repository_chain, "REPOSITORY_IDENTITY_CHANGED")
    _verify_directory_chain(output_chain, "output_directory_identity_changed")


def _directory_identity(value: os.stat_result) -> tuple[int, int]:
    return (value.st_dev, value.st_ino)


def _verify_retained_descriptor_identity(
    descriptor: int,
    expected: tuple[int, int],
    error_code: str,
) -> None:
    try:
        observed = os.fstat(descriptor)
    except OSError as exc:
        raise ReplacementObservationError(error_code) from exc
    if not stat.S_ISDIR(observed.st_mode) or _directory_identity(observed) != expected:
        raise ReplacementObservationError(error_code)


def _verify_retained_directory_entry(
    *,
    parent_fd: int,
    name: str,
    retained_fd: int,
    expected: tuple[int, int],
    error_code: str,
) -> None:
    try:
        entry = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        retained = os.fstat(retained_fd)
    except OSError as exc:
        raise ReplacementObservationError(error_code) from exc
    if (
        not stat.S_ISDIR(entry.st_mode)
        or not stat.S_ISDIR(retained.st_mode)
        or _directory_identity(entry) != expected
        or _directory_identity(retained) != expected
    ):
        raise ReplacementObservationError(error_code)


def _revalidate_retained_paths(
    *,
    repository_chain: list[tuple[int, str, int]],
    repository_fd: int,
    inputs_fd: int,
    input_parent_fd: int,
    repository_identity: tuple[int, int],
    inputs_identity: tuple[int, int],
    input_parent_identity: tuple[int, int],
) -> None:
    """Re-resolve bounded pathnames without changing publication descriptors."""
    _verify_retained_descriptor_identity(
        repository_fd,
        repository_identity,
        "REPOSITORY_IDENTITY_CHANGED",
    )
    _verify_directory_chain(repository_chain, "REPOSITORY_IDENTITY_CHANGED")
    _verify_retained_descriptor_identity(
        inputs_fd,
        inputs_identity,
        "INPUT_PARENT_IDENTITY_CHANGED",
    )
    _verify_retained_directory_entry(
        parent_fd=repository_fd,
        name="inputs",
        retained_fd=inputs_fd,
        expected=inputs_identity,
        error_code="INPUT_PARENT_IDENTITY_CHANGED",
    )
    _verify_retained_directory_entry(
        parent_fd=inputs_fd,
        name="current",
        retained_fd=input_parent_fd,
        expected=input_parent_identity,
        error_code="INPUT_PARENT_IDENTITY_CHANGED",
    )


def _cleanup_noexcept(action: Any, *args: Any) -> None:
    """Run one cleanup action without allowing any exception to escape."""
    try:
        action(*args)
    except BaseException:  # noqa: BLE001 - cleanup must never replace the result
        return


def _close_fd_noexcept(descriptor: int) -> None:
    """Best-effort descriptor close that is safe after publication commit."""
    try:
        os.close(descriptor)
    except BaseException:  # noqa: BLE001 - cleanup is deliberately no-throw
        return


def _close_directory_chain_noexcept(chain: list[tuple[int, str, int]]) -> None:
    """Best-effort close of every owned directory descriptor; never raise."""
    try:
        closed: set[int] = set()
        for _parent_fd, _name, child_fd in reversed(chain):
            if child_fd in closed:
                continue
            closed.add(child_fd)
            _close_fd_noexcept(child_fd)
    except BaseException:  # noqa: BLE001 - injected cleanup failures stay local
        return


def _unlink_at_noexcept(directory_fd: int, filename: str) -> None:
    """Best-effort removal for pre-commit temporary files; never raise."""
    try:
        os.unlink(filename, dir_fd=directory_fd)
    except BaseException:  # noqa: BLE001 - preserve the primary failure reason
        return


def _require_descriptor_primitives() -> None:
    required_flags = ("O_DIRECTORY", "O_NOFOLLOW", "O_CLOEXEC", "O_NONBLOCK")
    if any(not hasattr(os, flag) for flag in required_flags):
        raise ReplacementObservationError("required_nofollow_primitives_unavailable")
    required_dir_fd = (os.open, os.mkdir, os.rename, os.stat, os.unlink)
    if any(function not in os.supports_dir_fd for function in required_dir_fd):
        raise ReplacementObservationError("required_dirfd_primitives_unavailable")
    if os.listdir not in os.supports_fd:
        raise ReplacementObservationError("required_fd_listing_primitive_unavailable")


def _validate_manifest(payload: Any) -> None:
    if not isinstance(payload, Mapping) or set(payload) != _MANIFEST_KEYS:
        raise ReplacementObservationError("manifest_key_closure_invalid")
    if payload.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        raise ReplacementObservationError("manifest_schema_unsupported")
    if payload.get("compatibility_profile") != COMPATIBILITY_PROFILE:
        raise ReplacementObservationError("manifest_compatibility_profile_unsupported")
    _validate_authority_markers(payload, "manifest_authority_markers_invalid")
    _validated_as_of(payload.get("as_of"))
    if payload.get("generated_at") != f"{payload['as_of']}T00:00:00+00:00":
        raise ReplacementObservationError("manifest_generated_at_invalid")
    if payload.get("capture_profile") != CAPTURE_PROFILE:
        raise ReplacementObservationError("manifest_capture_profile_invalid")
    inputs = payload.get("inputs")
    if not isinstance(inputs, Mapping) or set(inputs) != set(INPUT_PATHS):
        raise ReplacementObservationError("manifest_inputs_key_closure_invalid")
    for name, expected_path in INPUT_PATHS.items():
        record = inputs.get(name)
        if not isinstance(record, Mapping) or set(record) != _INPUT_RECORD_KEYS:
            raise ReplacementObservationError("manifest_input_record_key_closure_invalid")
        if record.get("path") != expected_path or not _bounded_repository_relative_path(expected_path):
            raise ReplacementObservationError("manifest_input_path_invalid")
        if not _is_sha256(record.get("file_sha256")) or not _is_sha256(
            record.get("production_text_sha256")
        ):
            raise ReplacementObservationError("manifest_input_hash_invalid")
        if record.get("source_version") != SOURCE_VERSIONS[name]:
            raise ReplacementObservationError("manifest_source_version_invalid")
    if payload.get("supported_source_versions") != SOURCE_VERSIONS:
        raise ReplacementObservationError("manifest_supported_versions_invalid")
    if not _is_sha256(payload.get("parsed_decision_relevant_settings_sha256")):
        raise ReplacementObservationError("manifest_settings_hash_invalid")
    evidence = payload.get("evidence_packet")
    if not isinstance(evidence, Mapping) or set(evidence) != _EVIDENCE_RECORD_KEYS:
        raise ReplacementObservationError("manifest_evidence_record_invalid")
    if evidence.get("schema_version") != EVIDENCE_PACKET_SCHEMA_VERSION:
        raise ReplacementObservationError("manifest_evidence_schema_invalid")
    if not _is_sha256(evidence.get("file_sha256")) or not _is_sha256(
        evidence.get("canonical_content_sha256")
    ):
        raise ReplacementObservationError("manifest_evidence_hash_invalid")
    registry = payload.get("active_registry")
    if not isinstance(registry, Mapping) or set(registry) != _REGISTRY_RECORD_KEYS:
        raise ReplacementObservationError("manifest_registry_record_invalid")
    if registry.get("schema_version") not in {
        BASELINE_REGISTRY_SCHEMA_VERSION,
        APPROVALS_REGISTRY_SCHEMA_VERSION,
    }:
        raise ReplacementObservationError("manifest_registry_schema_invalid")
    if not _is_sha256(registry.get("canonical_content_sha256")):
        raise ReplacementObservationError("manifest_registry_hash_invalid")
    if registry.get("selected_source") not in {
        "approvals_inclusive",
        "baseline_fallback",
        "fail_closed_empty",
    }:
        raise ReplacementObservationError("manifest_registry_source_invalid")
    if (
        registry.get("selected_source") == "approvals_inclusive"
        and registry.get("schema_version") != APPROVALS_REGISTRY_SCHEMA_VERSION
    ) or (
        registry.get("selected_source") in {"baseline_fallback", "fail_closed_empty"}
        and registry.get("schema_version") != BASELINE_REGISTRY_SCHEMA_VERSION
    ):
        raise ReplacementObservationError("manifest_registry_source_schema_mismatch")
    prompt_contract = payload.get("prompt_contract")
    _validate_prompt_contract_record(prompt_contract)
    domain = payload.get("domain_validation")
    if not isinstance(domain, Mapping) or set(domain) != _DOMAIN_VALIDATION_KEYS:
        raise ReplacementObservationError("manifest_domain_validation_invalid")
    if domain.get("status") != DOMAIN_VALID_STATUS:
        raise ReplacementObservationError("manifest_domain_status_invalid")
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
        raise ReplacementObservationError("manifest_domain_diagnostics_invalid")


def _validate_prompt_contract_record(value: Any) -> None:
    if not isinstance(value, Mapping) or set(value) != _PROMPT_CONTRACT_RECORD_KEYS:
        raise ReplacementObservationError("manifest_prompt_contract_invalid")
    projection = value.get("projection")
    if not isinstance(projection, Mapping) or set(projection) != _PROMPT_CONTRACT_PROJECTION_KEYS:
        raise ReplacementObservationError("manifest_prompt_contract_projection_invalid")
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
    for key, expected_value in expected.items():
        if projection.get(key) != expected_value:
            raise ReplacementObservationError("manifest_prompt_contract_projection_invalid")
    if not _is_sha256(projection.get("template_file_sha256")):
        raise ReplacementObservationError("manifest_prompt_contract_projection_invalid")
    if value.get("canonical_content_sha256") != _canonical_sha256(projection):
        raise ReplacementObservationError("manifest_prompt_contract_hash_invalid")
    if (
        value.get("prompt_projection_schema_version") != PROMPT_PROJECTION_SCHEMA_VERSION
        or not _is_sha256(value.get("prompt_projection_canonical_sha256"))
        or not _is_sha256(value.get("analyst_memo_prompt_file_sha256"))
        or value.get("raw_memo_schema_version") != RAW_MEMO_SCHEMA_VERSION
    ):
        raise ReplacementObservationError("manifest_prompt_contract_invalid")


def _semantic_generation_identity(manifest: Mapping[str, Any]) -> dict[str, Any]:
    """Return the stable content-only projection that names one generation.

    Retained descriptor device/inode identities are deliberately absent. They
    are ephemeral in-memory race checks, not research or generation identity.
    """
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
                "production_text_sha256": manifest["inputs"][name][
                    "production_text_sha256"
                ],
            }
            for name in INPUT_PATHS
        },
        "supported_source_versions": dict(manifest["supported_source_versions"]),
        "parsed_decision_relevant_settings_sha256": manifest[
            "parsed_decision_relevant_settings_sha256"
        ],
        "active_registry": dict(manifest["active_registry"]),
        "evidence_packet": dict(manifest["evidence_packet"]),
        "prompt_contract": dict(manifest["prompt_contract"]),
        "authority_markers": dict(_AUTHORITY_MARKERS),
    }


def _validate_render_binding(payload: Any, *, expected_generation_id: str) -> None:
    if not isinstance(payload, Mapping) or set(payload) != _BINDING_KEYS:
        raise ReplacementObservationError("render_binding_key_closure_invalid")
    if payload.get("schema_version") != RENDER_BINDING_SCHEMA_VERSION:
        raise ReplacementObservationError("render_binding_schema_unsupported")
    if payload.get("compatibility_profile") != COMPATIBILITY_PROFILE:
        raise ReplacementObservationError("render_binding_profile_unsupported")
    if payload.get("generation_id") != expected_generation_id:
        raise ReplacementObservationError("render_binding_generation_mismatch")
    if payload.get("scope") != "IMMUTABLE_RENDER_ARTIFACTS_AND_INITIAL_BLANK_MEMO_ONLY":
        raise ReplacementObservationError("render_binding_scope_invalid")
    if payload.get("render_complete") is not True:
        raise ReplacementObservationError("render_binding_not_complete")
    _validate_authority_markers(payload, "render_binding_authority_markers_invalid")
    generation_identity = payload.get("generation_identity")
    if (
        not isinstance(generation_identity, Mapping)
        or set(generation_identity) != _GENERATION_IDENTITY_BINDING_KEYS
        or generation_identity.get("schema_version") != GENERATION_IDENTITY_SCHEMA_VERSION
        or not _is_sha256(generation_identity.get("prompt_contract_canonical_sha256"))
        or not _is_sha256(generation_identity.get("analyst_memo_prompt_file_sha256"))
        or generation_identity.get("raw_memo_schema_version") != RAW_MEMO_SCHEMA_VERSION
    ):
        raise ReplacementObservationError("render_binding_generation_identity_invalid")
    immutable = payload.get("immutable_render_artifacts")
    if not isinstance(immutable, Mapping) or set(immutable) != _IMMUTABLE_BINDING_KEYS:
        raise ReplacementObservationError("render_binding_immutable_key_closure_invalid")
    for name, record in immutable.items():
        expected_keys = (
            _TEXT_ARTIFACT_BINDING_KEYS
            if name.endswith(".txt")
            else _JSON_ARTIFACT_BINDING_KEYS
        )
        if not isinstance(record, Mapping) or set(record) != expected_keys:
            raise ReplacementObservationError("render_binding_artifact_record_invalid")
        if record.get("mutable_after_render") is not False:
            raise ReplacementObservationError("render_binding_immutable_marker_invalid")
        if not _is_sha256(record.get("file_sha256")):
            raise ReplacementObservationError("render_binding_file_hash_invalid")
        if name.endswith(".json"):
            expected_schema = (
                MANIFEST_SCHEMA_VERSION
                if name == "replacement_input_manifest.json"
                else EVIDENCE_PACKET_SCHEMA_VERSION
            )
            if record.get("schema_version") != expected_schema:
                raise ReplacementObservationError("render_binding_artifact_schema_invalid")
            if not _is_sha256(record.get("canonical_content_sha256")):
                raise ReplacementObservationError("render_binding_canonical_hash_invalid")
        elif record.get("media_type") != "text/plain; charset=utf-8":
            raise ReplacementObservationError("render_binding_artifact_media_type_invalid")
    prompt_record = immutable["analyst_memo_prompt.txt"]
    if prompt_record.get("file_sha256") != generation_identity.get(
        "analyst_memo_prompt_file_sha256"
    ):
        raise ReplacementObservationError("render_binding_prompt_identity_mismatch")
    editable = payload.get("operator_editable_inputs")
    if not isinstance(editable, Mapping) or set(editable) != _EDITABLE_BINDING_KEYS:
        raise ReplacementObservationError("render_binding_editable_key_closure_invalid")
    record = editable.get(MEMO_RAW_FILENAME)
    if not isinstance(record, Mapping) or set(record) != _EDITABLE_RECORD_KEYS:
        raise ReplacementObservationError("render_binding_editable_record_invalid")
    if (
        record.get("media_type") != "text/plain; charset=utf-8"
        or record.get("initial_state") != "BLANK"
        or record.get("operator_editable_after_render") is not True
        or record.get("render_witness_attests_initial_bytes_only") is not True
        or record.get("initial_file_sha256") != _sha256(b"")
    ):
        raise ReplacementObservationError("render_binding_editable_contract_invalid")


def _validate_authority_markers(payload: Mapping[str, Any], error: str) -> None:
    if any(payload.get(key) != value for key, value in _AUTHORITY_MARKERS.items()):
        raise ReplacementObservationError(error)


def _validated_as_of(value: Any) -> str:
    if not isinstance(value, str):
        raise ReplacementObservationError("as_of_invalid")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ReplacementObservationError("as_of_invalid") from exc
    if parsed.isoformat() != value or parsed > _today():
        raise ReplacementObservationError("as_of_future_or_invalid")
    return value


def _today() -> date:
    return date.today()


def _bounded_repository_relative_path(value: str) -> bool:
    path = Path(value)
    return not path.is_absolute() and ".." not in path.parts and path.as_posix() == value


def _parse_strategy_settings(text: str) -> dict[str, Any]:
    try:
        return parse_strategy_settings_text(text)
    except Exception as exc:  # noqa: BLE001 - emit stable code-only diagnostic
        raise ReplacementObservationError("strategy_settings_invalid") from exc


def _parse_json_object(value: bytes, error: str) -> dict[str, Any]:
    try:
        payload = json.loads(value.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReplacementObservationError(error) from exc
    if not isinstance(payload, dict):
        raise ReplacementObservationError(error)
    return payload


def _decode(value: Any, label: str) -> str:
    if not isinstance(value, bytes):
        raise ReplacementObservationError(f"captured_input_missing:{label}")
    try:
        return value.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ReplacementObservationError(f"captured_input_not_utf8:{label}") from exc


def _with_authority_markers(payload: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(payload)
    result.update(_AUTHORITY_MARKERS)
    return result


def _json_file_bytes(payload: Mapping[str, Any]) -> bytes:
    return (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _canonical_sha256(payload: Any) -> str:
    return _sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    )


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(char in "0123456789abcdef" for char in value)
