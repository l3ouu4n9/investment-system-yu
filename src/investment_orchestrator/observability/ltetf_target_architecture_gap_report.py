"""Standalone, fail-closed LTETF-01 target-architecture proof observer.

The observer is deliberately outside every investment decision path.  It reads
repository-local evidence, evaluates the frozen catalog's P01--P40 predicate
requirements, and writes one immutable report-only artifact.  It never grants
authority, changes a gate, or supplies an input to a production workflow.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from enum import Enum
from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import tomllib
from typing import Final, Iterable, Mapping, Sequence

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
import yaml

from investment_orchestrator.observability.ltetf_target_architecture_prerequisite_catalog import (
    AuthorityEffect,
    CATALOG,
    CATALOG_IDENTITY_SHA256,
    CATALOG_VERSION,
    CatalogCheck,
    CatalogIntegrityError,
    ContractOwner,
    EvidenceKind,
    PROOF_PREDICATES,
    REASON_CODES_BY_PROFILE,
    ReadinessStatus,
    STATUS_ROLLUP_PRECEDENCE,
    catalog_identity_sha256,
    validate_catalog,
)


SCHEMA_VERSION: Final = "ltetf_target_architecture_gap_report_v1"
OBSERVER_VERSION: Final = "ltetf_target_architecture_gap_observer_v1"
ARCHITECTURE_VERSION: Final = "ltetf_target_architecture_v1"
REPORT_IDENTITY_DOMAIN: Final = b"ltetf_target_architecture_gap_report_v1\0"
POLICY_IDENTITY_DOMAIN: Final = b"ltetf_operator_mandate_policy_v1\0"
RUNTIME_IDENTITY_DOMAIN: Final = b"ltetf_portfolio_state_v1\0"
SCHEMA_IDENTITY_DOMAIN: Final = b"ltetf_target_contract_schema_v1\0"
INVENTORY_IDENTITY_DOMAIN: Final = b"ltetf_repository_inventory_v1\0"
EVIDENCE_IDENTITY_DOMAIN: Final = b"ltetf_repository_evidence_v1\0"
MAX_REPORT_CANONICAL_BYTES: Final = 1_048_576
MAX_EVIDENCE_CANONICAL_BYTES: Final = 524_288
REPORT_SCHEMA_RELATIVE_PATH: Final = "schemas/ltetf_target_architecture_gap_report.schema.json"
REPORT_NAMESPACE_RELATIVE_PATH: Final = (
    "artifacts/target_architecture/report_only/ltetf_01/reports"
)
EVALUATION_TIME_SOURCE: Final = "trusted_clock_unavailable_not_used"
JSON_SCHEMA_DRAFT_2020_12: Final = "https://json-schema.org/draft/2020-12/schema"

_SHA256_RE: Final = re.compile(r"^[0-9a-f]{64}$")
_RELATIVE_PATH_RE: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/:-]*$")
_POLICY_INPUT_RELATIVE_PATH: Final = "inputs/current/ltetf_operator_mandate.json"
_PORTFOLIO_STATE_RELATIVE_PATH: Final = "inputs/current/ltetf_portfolio_state.json"
_PORTFOLIO_STATE_SCHEMA_RELATIVE_PATH: Final = "schemas/ltetf_portfolio_state.schema.json"
_OPERATOR_MANDATE_POLICY_SCHEMA_VERSION: Final = "ltetf_operator_mandate_policy_v1"
_PORTFOLIO_STATE_SCHEMA_VERSION: Final = "ltetf_portfolio_state_v1"
_CURRENT_RUNTIME_SLOT: Final = "ltetf_portfolio_state"


class _ConsumerRelationCategory(str, Enum):
    """Private per-relation inventory categories; never report vocabulary."""

    INTERNAL_IMPLEMENTATION_EDGE = "INTERNAL_IMPLEMENTATION_EDGE"
    EXTERNAL_OBSERVER_CONSUMER = "EXTERNAL_OBSERVER_CONSUMER"
    REPORT_ARTIFACT_READER = "REPORT_ARTIFACT_READER"
    UNRESOLVED_RELEVANT_CONSUMER = "UNRESOLVED_RELEVANT_CONSUMER"
    NOT_RELEVANT_TO_LTETF01 = "NOT_RELEVANT_TO_LTETF01"


@dataclass(frozen=True, slots=True)
class _DeclaredObserverContractModule:
    """One exact production module in a closed observer-contract suite."""

    relative_path: str
    module_name: str


@dataclass(frozen=True, slots=True)
class _DeclaredInternalModuleRelation:
    """One explicitly allowed module-to-module implementation relation."""

    importer_module: str
    importee_module: str
    edge_kind: str


@dataclass(frozen=True, slots=True)
class _DeclaredObserverContractSuite:
    """Closed suite membership and relations; no package inference is allowed."""

    suite_id: str
    modules: tuple[_DeclaredObserverContractModule, ...]
    allowed_internal_relations: tuple[_DeclaredInternalModuleRelation, ...]


_LTETF_02A1_MODULE_LEAF_PREFIX: Final = "ltetf_" + "evidence_"
_LTETF_02A1_CATALOG_MODULE_LEAF: Final = (
    _LTETF_02A1_MODULE_LEAF_PREFIX + "requirement_catalog"
)
_LTETF_02A1_COMMON_MODULE_LEAF: Final = (
    _LTETF_02A1_MODULE_LEAF_PREFIX + "contract_common"
)
_WS01B_MODULE_LEAF_PREFIX: Final = "weekly_" + "shadow_" + "01_"
_WS01B_PACKAGE_BUILDER_MODULE_LEAF: Final = (
    _WS01B_MODULE_LEAF_PREFIX + "package_" + "builder"
)
_WS01B_SOURCE_ADAPTER_MODULE_LEAF: Final = (
    _WS01B_MODULE_LEAF_PREFIX + "source_" + "adapter"
)
_WS01C_RESPONSE_VALIDATOR_MODULE_LEAF: Final = (
    _WS01B_MODULE_LEAF_PREFIX + "response_" + "validator"
)
_WS01D_REPORT_PUBLISHER_MODULE_LEAF: Final = (
    _WS01B_MODULE_LEAF_PREFIX + "report_" + "publisher"
)
_OBSERVER_INTERNAL_RELATIVE_PATHS: Final = frozenset(
    {
        "src/investment_orchestrator/observability/__init__.py",
        "src/investment_orchestrator/observability/ltetf_target_architecture_prerequisite_catalog.py",
        "src/investment_orchestrator/observability/ltetf_target_architecture_gap_report.py",
    }
)
_OBSERVER_CLI_RELATIVE_PATH: Final = (
    "src/investment_orchestrator/cli/observe_ltetf_target_architecture_gaps.py"
)
_DECLARED_OBSERVER_CONTRACT_SUITES: Final = (
    _DeclaredObserverContractSuite(
        suite_id="ltetf_02a1_static_evidence_contract",
        modules=(
            _DeclaredObserverContractModule(
                relative_path=(
                    "src/investment_orchestrator/observability/"
                    f"{_LTETF_02A1_COMMON_MODULE_LEAF}.py"
                ),
                module_name=(
                    "investment_orchestrator.observability."
                    f"{_LTETF_02A1_COMMON_MODULE_LEAF}"
                ),
            ),
            _DeclaredObserverContractModule(
                relative_path=(
                    "src/investment_orchestrator/observability/"
                    f"{_LTETF_02A1_CATALOG_MODULE_LEAF}.py"
                ),
                module_name=(
                    "investment_orchestrator.observability."
                    f"{_LTETF_02A1_CATALOG_MODULE_LEAF}"
                ),
            ),
        ),
        allowed_internal_relations=(
            _DeclaredInternalModuleRelation(
                importer_module=(
                    "investment_orchestrator.observability."
                    f"{_LTETF_02A1_CATALOG_MODULE_LEAF}"
                ),
                importee_module=(
                    "investment_orchestrator.observability."
                    f"{_LTETF_02A1_COMMON_MODULE_LEAF}"
                ),
                edge_kind="static_module_binding",
            ),
        ),
    ),
    _DeclaredObserverContractSuite(
        suite_id="weekly_shadow_01b_grounding_runtime",
        modules=(
            _DeclaredObserverContractModule(
                relative_path=(
                    "src/investment_orchestrator/observability/"
                    f"{_WS01B_PACKAGE_BUILDER_MODULE_LEAF}.py"
                ),
                module_name=(
                    "investment_orchestrator.observability."
                    f"{_WS01B_PACKAGE_BUILDER_MODULE_LEAF}"
                ),
            ),
            _DeclaredObserverContractModule(
                relative_path=(
                    "src/investment_orchestrator/observability/"
                    f"{_WS01B_SOURCE_ADAPTER_MODULE_LEAF}.py"
                ),
                module_name=(
                    "investment_orchestrator.observability."
                    f"{_WS01B_SOURCE_ADAPTER_MODULE_LEAF}"
                ),
            ),
            _DeclaredObserverContractModule(
                relative_path=(
                    "src/investment_orchestrator/observability/"
                    f"{_WS01C_RESPONSE_VALIDATOR_MODULE_LEAF}.py"
                ),
                module_name=(
                    "investment_orchestrator.observability."
                    f"{_WS01C_RESPONSE_VALIDATOR_MODULE_LEAF}"
                ),
            ),
            _DeclaredObserverContractModule(
                relative_path=(
                    "src/investment_orchestrator/observability/"
                    f"{_WS01D_REPORT_PUBLISHER_MODULE_LEAF}.py"
                ),
                module_name=(
                    "investment_orchestrator.observability."
                    f"{_WS01D_REPORT_PUBLISHER_MODULE_LEAF}"
                ),
            ),
        ),
        allowed_internal_relations=(
            _DeclaredInternalModuleRelation(
                importer_module=(
                    "investment_orchestrator.observability."
                    f"{_WS01B_PACKAGE_BUILDER_MODULE_LEAF}"
                ),
                importee_module=(
                    "investment_orchestrator.observability."
                    f"{_WS01B_SOURCE_ADAPTER_MODULE_LEAF}"
                ),
                edge_kind="static_module_binding",
            ),
            _DeclaredInternalModuleRelation(
                importer_module=(
                    "investment_orchestrator.observability."
                    f"{_WS01C_RESPONSE_VALIDATOR_MODULE_LEAF}"
                ),
                importee_module=(
                    "investment_orchestrator.observability."
                    f"{_WS01B_PACKAGE_BUILDER_MODULE_LEAF}"
                ),
                edge_kind="static_module_binding",
            ),
            _DeclaredInternalModuleRelation(
                importer_module=(
                    "investment_orchestrator.observability."
                    f"{_WS01D_REPORT_PUBLISHER_MODULE_LEAF}"
                ),
                importee_module=(
                    "investment_orchestrator.observability."
                    f"{_WS01C_RESPONSE_VALIDATOR_MODULE_LEAF}"
                ),
                edge_kind="static_module_binding",
            ),
        ),
    ),
)
_INVENTORY_EXCLUDED_PATH_PARTS: Final = frozenset(
    {".git", ".venv", "__pycache__", "vendor"}
)
_REPORT_MARKERS: Final = frozenset(
    {
        "ltetf_target_architecture_gap_report_v1",
        "ltetf_target_architecture_gap_report.schema.json",
        "prerequisite_catalog_identity_sha256",
        "content_identity_sha256",
        "evaluation_time_source",
        "artifacts/target_architecture/report_only/ltetf_01",
        "target_architecture/report_only",
    }
)
_POLICY_MARKERS: Final = frozenset(
    {
        _POLICY_INPUT_RELATIVE_PATH,
        "ltetf_operator_mandate",
        "permitted_consumer_set",
    }
)
_UNIQUE_REPORT_FIELD_MARKERS: Final = frozenset(
    {
        "prerequisite_catalog_identity_sha256",
        "evaluation_time_source",
        "repository_evidence_identity_sha256",
    }
)


class ObserverIntegrityError(RuntimeError):
    """A closed observer-integrity failure; callers must publish no report."""

    _CODES: Final = frozenset(
        {
            "OBSERVER_CATALOG_INVALID",
            "OBSERVER_SCHEMA_INVALID",
            "CONSUMER_INVENTORY_INCOMPLETE",
            "EVIDENCE_COLLECTION_FAILED",
            "CANONICAL_BOUND_EXCEEDED",
            "IDENTITY_COMPUTATION_FAILED",
            "REPORT_OUTPUT_CONFLICT",
            "REPORT_RECORD_INVALID",
        }
    )

    def __init__(self, code: str) -> None:
        self.code = code if type(code) is str and code in self._CODES else "EVIDENCE_COLLECTION_FAILED"
        super().__init__(self.code)


class EvidenceValidationState(str, Enum):
    VALID = "valid"
    ABSENT = "absent"
    INVALID = "invalid"
    DRAFT = "draft"
    UNACCEPTED = "unaccepted"


class AdapterKind(str, Enum):
    CONTRACT = "contract"
    POLICY = "policy"
    RUNTIME = "runtime"
    NEGATIVE = "negative"
    WEEKLY = "weekly"


class RuntimeObservation(str, Enum):
    NONE = "none"
    ABSENT = "absent"
    INVALID = "invalid"
    INCOMPLETE = "incomplete"
    COMPLETE = "complete"
    CONFLICT = "conflict"


class PolicyObservation(str, Enum):
    NONE = "none"
    DRAFT = "draft"
    INVALID = "invalid"
    UNACCEPTED = "unaccepted"
    ACCEPTED = "accepted"
    CONFLICT = "conflict"


@dataclass(frozen=True, slots=True)
class EvidenceRecord:
    evidence_id: str
    evidence_kind: EvidenceKind
    repository_relative_path: str | None
    locator_kind: str
    locator: str
    content_identity_sha256: str | None
    validation_state: EvidenceValidationState
    current: bool
    diagnostic_codes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ProductionInventory:
    """Bounded facts from a completed fail-closed production-source scan."""

    production_paths: tuple[str, ...]
    entry_points: tuple[tuple[str, str], ...]
    imports_by_path: tuple[tuple[str, tuple[str, ...]], ...]
    dynamic_findings: tuple[str, ...]
    observer_external_consumers: tuple[str, ...]
    report_artifact_readers: tuple[str, ...]
    policy_artifact_consumers: tuple[str, ...]
    prohibited_observer_capability_imports: tuple[str, ...]
    p4a_runtime_consumers: tuple[str, ...]
    broker_capability_imports: tuple[str, ...]
    weekly_llm_invocation_markers: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RepositoryEvidence:
    root: Path
    records: tuple[EvidenceRecord, ...]
    inventory: ProductionInventory


@dataclass(frozen=True, slots=True)
class PredicateOutcome:
    """One bounded deterministic outcome for one frozen P01--P40 predicate."""

    predicate_id: str
    satisfied: bool
    evidence_ids: tuple[str, ...]
    diagnostic_codes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SourceEvidenceSpec:
    """Supporting source evidence only; it cannot assert target completion."""

    evidence_id: str
    expected_symbol: str | None = None


@dataclass(frozen=True, slots=True)
class EvidenceAdapter:
    """Per-check evidence configuration, never a status or reason mapping."""

    check_id: str
    kind: AdapterKind
    target_schema_path: str | None = None
    target_schema_version: str | None = None
    producer_path: str | None = None
    validator_symbol: str | None = None
    producer_symbol: str | None = None
    test_path: str | None = None
    allowed_consumers: tuple[str, ...] = ()
    legacy_sources: tuple[SourceEvidenceSpec, ...] = ()
    required_facets: tuple[str, ...] = ()
    logical_current_slot: str | None = None
    policy_section: str | None = None
    negative_capability: str | None = None
    allow_accepted_nonactive: bool = False


@dataclass(frozen=True, slots=True)
class SourceContractFacts:
    """Machine-verifiable production-source facts for one target contract."""

    validator_exists: bool = False
    producer_exists: bool = False
    validator_reached: bool = False
    target_semantics: bool = False
    prohibited_authority_absent: bool = False
    deterministic_enforcement: bool = False


@dataclass(frozen=True, slots=True)
class TestContractFacts:
    """Bounded test-contract facts; they support but never replace production proof."""

    tests_support: bool = False


@dataclass(frozen=True, slots=True)
class AdapterFacts:
    """Evidence facts consumed by the P01--P40 evaluator and status selector."""

    evidence_ids: tuple[str, ...] = ()
    supporting_evidence_ids: tuple[str, ...] = ()
    legacy_evidence_ids: tuple[str, ...] = ()
    diagnostics: tuple[str, ...] = ()
    contract_frozen: bool = False
    schema_closed_bounded: bool = False
    schema_identity_verified: bool = False
    validator_exists: bool = False
    validator_reached: bool = False
    producer_exists: bool = False
    consumers_compatible: bool = False
    tests_support: bool = False
    fixtures_exercised: bool = False
    target_semantics: bool = False
    facets_complete: bool = False
    prohibited_authority_absent: bool = False
    deterministic_enforcement: bool = False
    fails_closed: bool = False
    lineage_complete: bool = False
    evidence_refs_bound: bool = False
    trusted_clock_valid: bool = False
    selection_bounds_enforced: bool = False
    inventory_complete: bool = False
    prohibited_capability_absent: bool = False
    vocabulary_enforced: bool = False
    bounded_llm_input: bool = False
    atomic_manual_order_package_proven: bool = False
    atomic_current_package_pointer_proven: bool = False
    postcompile_validation: bool = False
    evidence_sufficiency: bool = False
    identities_nonconflicting: bool = False
    generation_provenance: bool = False
    structured_facets_valid: bool = False
    prose_excluded: bool = False
    tests_not_sole_proof: bool = False
    producer_validator_consumer_compatible: bool = False
    policy_candidate_valid: bool = False
    policy_accepted: bool = False
    policy_effective_activation_valid: bool = False
    policy_consumers_compatible: bool = False
    runtime_observation: RuntimeObservation = RuntimeObservation.NONE
    policy_observation: PolicyObservation = PolicyObservation.NONE
    draft_only: bool = False
    legacy_semantic_mismatch: bool = False
    target_compatible_partial: bool = False
    direct_contradiction: bool = False
    contradiction_reason: str | None = None
    contradiction_evidence_ids: tuple[str, ...] = ()
    disqualifying_conditions: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PrerequisiteObservation:
    """Predicate facts, not a precomputed readiness status."""

    adapter: EvidenceAdapter
    facts: AdapterFacts
    predicate_outcomes: tuple[PredicateOutcome, ...]


@dataclass(frozen=True, slots=True)
class CheckAssessment:
    check: CatalogCheck
    status: ReadinessStatus
    reason_codes: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    diagnostic_codes: tuple[str, ...]
    predicate_outcomes: tuple[PredicateOutcome, ...]


AUTHORITY_DECLARATION: Final = {
    "report_only": True,
    "not_authorization": True,
    "authority_scope": "target_architecture_migration_observability_only",
    "permitted_consumer_scope": "operator_observability_only",
    "authority_consumers": [],
    "trade_permission_effect": "none",
    "stage_reachability_effect": "none",
    "canonical_publication_effect": "none",
    "order_path_effect": "none",
    "weekly_behavior_effect": "none",
}

DIAGNOSTIC_CODES: Final = frozenset(
    {
        "CURRENT_RUNTIME_DATA_INVALID",
        "CURRENT_RUNTIME_IDENTITY_CONFLICT",
        "DRAFT_DOCUMENT_NOT_ACTIVE_POLICY",
        "EVIDENCE_ABSENT",
        "EVIDENCE_JSON_INVALID",
        "EVIDENCE_JSON_ROOT_INVALID",
        "EVIDENCE_NOT_REGULAR_FILE",
        "EVIDENCE_PATH_UNSAFE",
        "EVIDENCE_SCHEMA_INVALID",
        "EVIDENCE_YAML_INVALID",
        "EVIDENCE_YAML_ROOT_INVALID",
        "LEGACY_SEMANTIC_MISMATCH",
        "POLICY_ACCEPTANCE_STATE_INVALID",
        "POLICY_ACTIVATION_INVALID",
        "POLICY_CANDIDATE_FIELDS_INVALID",
        "POLICY_CANDIDATE_NOT_ACCEPTED",
        "POLICY_CANDIDATE_SCHEMA_INVALID",
        "POLICY_CONSUMER_MISMATCH",
        "POLICY_EFFECTIVE_VERSION_INVALID",
        "POLICY_IDENTITY_MISMATCH",
        "ACTIVE_POLICY_IDENTITY_CONFLICT",
        "BEHAVIORAL_PROBE_UNAVAILABLE",
        "POLICY_SCHEMA_INVALID",
        "POLICY_SCHEMA_NOT_CLOSED",
        "POLICY_SCHEMA_PATH_INVALID",
        "POLICY_SCHEMA_UNAVAILABLE",
        "POLICY_SCHEMA_VERSION_UNSUPPORTED",
        "PROHIBITED_CONSUMER_PRESENT",
        "RUNTIME_DATA_INCOMPLETE",
        "RUNTIME_SLOT_NOT_CURRENT",
        "TARGET_CONTRACT_ABSENT",
        "TARGET_CONTRACT_IDENTITY_INVALID",
        "TARGET_PRODUCER_ABSENT",
        "TARGET_VALIDATOR_ABSENT",
        "UNKNOWN_CONTRACT_VERSION",
        "UNRESOLVED_DYNAMIC_CONSTRUCT",
        "UNSUPPORTED_ACTIVATION_MARKER",
        "WEEKLY_BEHAVIOR_PROOF_INCOMPLETE",
    }
)


def _bounded_unique_strings(values: Iterable[str], *, maximum: int = 32) -> tuple[str, ...]:
    if not isinstance(values, (tuple, list, set, frozenset)):
        raise ObserverIntegrityError("EVIDENCE_COLLECTION_FAILED")
    result: list[str] = []
    for value in values:
        if type(value) is not str or not value or len(value) > 160:
            raise ObserverIntegrityError("EVIDENCE_COLLECTION_FAILED")
        if value not in result:
            result.append(value)
        if len(result) > maximum:
            raise ObserverIntegrityError("EVIDENCE_COLLECTION_FAILED")
    return tuple(result)


def _canonical_json_bytes(record: object, *, maximum: int) -> bytes:
    try:
        encoded = json.dumps(
            record, ensure_ascii=True, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError):
        raise ObserverIntegrityError("IDENTITY_COMPUTATION_FAILED") from None
    if len(encoded) > maximum:
        raise ObserverIntegrityError("CANONICAL_BOUND_EXCEEDED")
    return encoded


def _sha256_identity(domain: bytes, record: object, *, maximum: int) -> str:
    if type(domain) is not bytes:
        raise ObserverIntegrityError("IDENTITY_COMPUTATION_FAILED")
    return hashlib.sha256(domain + _canonical_json_bytes(record, maximum=maximum)).hexdigest()


def _is_normalized_relative_path(value: object) -> bool:
    """Accept only one normalized, repository-relative POSIX path spelling."""
    if type(value) is not str or not value or "\x00" in value or "\\" in value:
        return False
    if not _RELATIVE_PATH_RE.fullmatch(value) or value.startswith("/"):
        return False
    if re.match(r"^[A-Za-z]:", value) or value.endswith("/") or "//" in value:
        return False
    parts = value.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        return False
    path = PurePosixPath(value)
    return not path.is_absolute() and path.as_posix() == value


def _repository_relative_path(root: Path, path: Path) -> str:
    try:
        relative = path.relative_to(root).as_posix()
    except ValueError:
        raise ObserverIntegrityError("EVIDENCE_COLLECTION_FAILED") from None
    if not _is_normalized_relative_path(relative):
        raise ObserverIntegrityError("EVIDENCE_COLLECTION_FAILED")
    return relative


def _safe_root(root: Path) -> Path:
    if not isinstance(root, Path):
        raise ObserverIntegrityError("EVIDENCE_COLLECTION_FAILED")
    try:
        resolved = root.resolve(strict=True)
    except OSError:
        raise ObserverIntegrityError("EVIDENCE_COLLECTION_FAILED") from None
    pyproject = resolved / "pyproject.toml"
    if pyproject.is_symlink() or not pyproject.is_file() or not (
        resolved / "src" / "investment_orchestrator"
    ).is_dir():
        raise ObserverIntegrityError("EVIDENCE_COLLECTION_FAILED")
    return resolved


def _safe_repository_file_path(root: Path, relative_path: str) -> Path | None:
    if not _is_normalized_relative_path(relative_path):
        return None
    cursor = root
    for part in PurePosixPath(relative_path).parts:
        cursor = cursor / part
        if cursor.is_symlink():
            return None
    try:
        if cursor.exists():
            cursor.resolve(strict=True).relative_to(root)
    except (OSError, ValueError):
        return None
    return cursor


def _file_identity(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        raise ObserverIntegrityError("EVIDENCE_COLLECTION_FAILED") from None


def _record(
    evidence_id: str,
    kind: EvidenceKind,
    relative_path: str | None,
    locator_kind: str,
    locator: str,
    state: EvidenceValidationState,
    *,
    current: bool = False,
    diagnostics: Sequence[str] = (),
    identity: str | None = None,
) -> EvidenceRecord:
    return EvidenceRecord(
        evidence_id=evidence_id,
        evidence_kind=kind,
        repository_relative_path=relative_path,
        locator_kind=locator_kind,
        locator=locator,
        content_identity_sha256=identity,
        validation_state=state,
        current=current,
        diagnostic_codes=_bounded_unique_strings(tuple(diagnostics), maximum=16),
    )


def _observe_path(
    root: Path,
    evidence_id: str,
    kind: EvidenceKind,
    relative_path: str,
    *,
    locator_kind: str = "python_symbol",
    locator: str = "module",
    parser: str = "raw",
    current: bool = False,
) -> EvidenceRecord:
    path = _safe_repository_file_path(root, relative_path)
    if path is None:
        return _record(evidence_id, kind, relative_path, locator_kind, locator, EvidenceValidationState.INVALID, current=current, diagnostics=("EVIDENCE_PATH_UNSAFE",))
    if not path.exists():
        return _record(evidence_id, kind, relative_path, locator_kind, locator, EvidenceValidationState.ABSENT, current=current, diagnostics=("EVIDENCE_ABSENT",))
    if path.is_symlink() or not path.is_file():
        return _record(evidence_id, kind, relative_path, locator_kind, locator, EvidenceValidationState.INVALID, current=current, diagnostics=("EVIDENCE_NOT_REGULAR_FILE",))
    identity = _file_identity(path)
    if parser == "raw" or parser == "python":
        return _record(evidence_id, kind, relative_path, locator_kind, locator, EvidenceValidationState.VALID, current=current, identity=identity)
    if parser == "draft":
        return _record(evidence_id, kind, relative_path, locator_kind, locator, EvidenceValidationState.DRAFT, current=current, identity=identity, diagnostics=("DRAFT_DOCUMENT_NOT_ACTIVE_POLICY",))
    if parser == "json":
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return _record(evidence_id, kind, relative_path, locator_kind, locator, EvidenceValidationState.INVALID, current=current, diagnostics=("EVIDENCE_JSON_INVALID",))
        if type(payload) is not dict:
            return _record(evidence_id, kind, relative_path, locator_kind, locator, EvidenceValidationState.INVALID, current=current, diagnostics=("EVIDENCE_JSON_ROOT_INVALID",))
        return _record(evidence_id, kind, relative_path, locator_kind, locator, EvidenceValidationState.VALID, current=current, identity=identity)
    if parser == "yaml":
        try:
            payload = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, yaml.YAMLError):
            return _record(evidence_id, kind, relative_path, locator_kind, locator, EvidenceValidationState.INVALID, current=current, diagnostics=("EVIDENCE_YAML_INVALID",))
        if type(payload) is not dict:
            return _record(evidence_id, kind, relative_path, locator_kind, locator, EvidenceValidationState.INVALID, current=current, diagnostics=("EVIDENCE_YAML_ROOT_INVALID",))
        return _record(evidence_id, kind, relative_path, locator_kind, locator, EvidenceValidationState.VALID, current=current, identity=identity)
    if parser == "schema":
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if type(payload) is not dict:
                raise ValueError
            Draft202012Validator.check_schema(payload)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError, SchemaError):
            return _record(evidence_id, kind, relative_path, locator_kind, locator, EvidenceValidationState.INVALID, current=current, diagnostics=("EVIDENCE_SCHEMA_INVALID",))
        return _record(evidence_id, kind, relative_path, locator_kind, locator, EvidenceValidationState.VALID, current=current, identity=identity)
    raise ObserverIntegrityError("EVIDENCE_COLLECTION_FAILED")


def _read_json_object(root: Path, relative_path: str) -> tuple[dict[str, object] | None, tuple[str, ...]]:
    path = _safe_repository_file_path(root, relative_path)
    if path is None or not path.exists() or path.is_symlink() or not path.is_file():
        return None, ("EVIDENCE_ABSENT",)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None, ("EVIDENCE_JSON_INVALID",)
    if type(value) is not dict:
        return None, ("EVIDENCE_JSON_ROOT_INVALID",)
    return value, ()


def _schema_is_closed_and_bounded(value: object, *, depth: int = 0) -> bool:
    """Reject open/unbounded object, array, and string schema branches."""
    if depth > 32 or type(value) is not dict:
        return False
    if "$ref" in value:
        return False
    declared_type = value.get("type")
    if declared_type == "object" or "properties" in value:
        if value.get("additionalProperties") is not False or type(value.get("properties")) is not dict:
            return False
        return all(_schema_is_closed_and_bounded(item, depth=depth + 1) for item in value["properties"].values())
    if declared_type == "array" or "items" in value:
        return type(value.get("maxItems")) is int and value["maxItems"] >= 0 and _schema_is_closed_and_bounded(value.get("items"), depth=depth + 1)
    if declared_type == "string":
        return type(value.get("maxLength")) is int and value["maxLength"] >= 0
    if declared_type in {"integer", "number", "boolean", "null"}:
        return True
    if "const" in value or "enum" in value:
        return True
    return False


def _schema_version_matches(schema: Mapping[str, object], expected: str) -> bool:
    properties = schema.get("properties")
    if type(properties) is not dict:
        return False
    version = properties.get("schema_version")
    return type(version) is dict and version.get("const") == expected


def _schema_identity_matches(schema: Mapping[str, object]) -> bool:
    identity = schema.get("x-contract-identity-sha256")
    if type(identity) is not str or not _SHA256_RE.fullmatch(identity):
        return False
    payload = dict(schema)
    payload.pop("x-contract-identity-sha256", None)
    return identity == _sha256_identity(SCHEMA_IDENTITY_DOMAIN, payload, maximum=MAX_EVIDENCE_CANONICAL_BYTES)


def _schema_required_facets_are_complete(schema: Mapping[str, object] | None) -> bool:
    """A target contract cannot call optional/unlisted fields complete facets."""
    if type(schema) is not dict:
        return False
    properties = schema.get("properties")
    required = schema.get("required")
    if type(properties) is not dict or not properties or type(required) is not list:
        return False
    return all(type(item) is str for item in required) and set(properties) == set(required)


def _module_name_for_path(relative_path: str) -> str:
    parts = list(PurePosixPath(relative_path).parts)
    if parts[:2] != ["src", "investment_orchestrator"] or not parts[-1].endswith(".py"):
        raise ObserverIntegrityError("CONSUMER_INVENTORY_INCOMPLETE")
    parts = parts[1:]
    leaf = parts.pop()
    if leaf == "__init__.py":
        parts.append("__init__")
    else:
        parts.append(leaf[:-3])
    return ".".join(parts)


def _resolve_relative_import(module_name: str, level: int, module: str | None) -> str | None:
    if level == 0:
        return module
    package_parts = module_name.split(".")[:-1]
    steps_up = level - 1
    if steps_up > len(package_parts):
        return None
    base = package_parts[: len(package_parts) - steps_up]
    if module:
        base.extend(module.split("."))
    return ".".join(base) if base else None


def _resolve_relative_dynamic_import(package: str, target: str) -> str | None:
    """Resolve a literal ``import_module('.x', package='a.b')`` exactly."""
    if not package or not target.startswith("."):
        return None
    leading = len(target) - len(target.lstrip("."))
    tail = target[leading:]
    package_parts = package.split(".")
    steps_up = leading - 1
    if steps_up < 0 or steps_up >= len(package_parts):
        return None
    base = package_parts[: len(package_parts) - steps_up]
    if tail:
        base.extend(tail.split("."))
    return ".".join(base) if base else None


def _call_name(node: ast.Call) -> str | None:
    if type(node.func) is ast.Name:
        return node.func.id
    if type(node.func) is ast.Attribute:
        return node.func.attr
    return None


def _literal_string(node: ast.AST | None) -> str | None:
    if type(node) is ast.Constant and type(node.value) is str:
        return node.value
    if type(node) is ast.BinOp and type(node.op) is ast.Add:
        left = _literal_string(node.left)
        right = _literal_string(node.right)
        return left + right if left is not None and right is not None else None
    if type(node) is ast.JoinedStr:
        pieces: list[str] = []
        for item in node.values:
            if type(item) is ast.Constant and type(item.value) is str:
                pieces.append(item.value)
            else:
                return None
        return "".join(pieces)
    return None


def _literal_path_expression(node: ast.AST | None) -> str | None:
    literal = _literal_string(node)
    if literal is not None:
        return literal
    if type(node) is ast.BinOp and type(node.op) is ast.Div:
        left = _literal_path_expression(node.left)
        right = _literal_path_expression(node.right)
        if left is not None and right is not None:
            return f"{left.rstrip('/')}/{right.lstrip('/')}"
    if type(node) is ast.Call:
        name = _call_name(node)
        if name in {"Path", "PurePath", "join", "joinpath"}:
            pieces = [_literal_path_expression(arg) for arg in node.args]
            if pieces and all(piece is not None for piece in pieces):
                return "/".join(piece.strip("/") for piece in pieces if piece is not None)
    return None


def _literal_bindings(tree: ast.AST) -> dict[str, str]:
    """Resolve only bounded local string/path assignments; never execute code."""
    bindings: dict[str, str] = {}
    assignments = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
    ]
    for _ in range(16):
        changed = False
        for assignment in assignments:
            value = _bound_literal_path_expression(assignment.value, bindings)
            if value is not None and bindings.get(assignment.targets[0].id) != value:
                bindings[assignment.targets[0].id] = value
                changed = True
        if not changed:
            break
    return bindings


def _bound_literal_path_expression(node: ast.AST | None, bindings: Mapping[str, str]) -> str | None:
    if isinstance(node, ast.Name):
        return bindings.get(node.id)
    if isinstance(node, ast.JoinedStr):
        pieces: list[str] = []
        for item in node.values:
            if isinstance(item, ast.Constant) and type(item.value) is str:
                pieces.append(item.value)
            elif isinstance(item, ast.FormattedValue):
                value = _bound_literal_path_expression(item.value, bindings)
                if value is None:
                    return None
                pieces.append(value)
            else:
                return None
        return "".join(pieces)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        left = _bound_literal_path_expression(node.left, bindings)
        right = _bound_literal_path_expression(node.right, bindings)
        return f"{left.rstrip('/')}/{right.lstrip('/')}" if left is not None and right is not None else None
    if isinstance(node, ast.Call):
        name = _call_name(node)
        if name == "open" and node.args:
            return _bound_literal_path_expression(node.args[0], bindings)
        if (
            isinstance(node.func, ast.Attribute)
            and node.func.attr in {"open", "read_text", "read_bytes"}
        ):
            return _bound_literal_path_expression(node.func.value, bindings)
        if name in {"Path", "PurePath", "join", "joinpath"}:
            pieces = [_bound_literal_path_expression(arg, bindings) for arg in node.args]
            if pieces and all(piece is not None for piece in pieces):
                return "/".join(piece.strip("/") for piece in pieces if piece is not None)
    return _literal_string(node)


def _tree_string_literals(tree: ast.AST) -> tuple[str, ...]:
    return tuple(
        node.value
        for node in ast.walk(tree)
        if type(node) is ast.Constant and type(node.value) is str
    )


def _is_observer_relevant_value(value: str) -> bool:
    lowered = value.lower()
    markers = (
        *(item.lower() for item in _REPORT_MARKERS),
        *(item.lower() for item in _POLICY_MARKERS),
        *(item.lower() for item in _UNIQUE_REPORT_FIELD_MARKERS),
        "investment_orchestrator.observability",
        "ltetf_target_architecture_prerequisite_catalog",
        "catalog_identity_sha256",
        "target_architecture/report_only",
    )
    return any(marker in lowered for marker in markers)


def _is_report_relevant_value(value: str) -> bool:
    lowered = value.lower()
    markers = (
        *(item.lower() for item in _REPORT_MARKERS),
        *(item.lower() for item in _UNIQUE_REPORT_FIELD_MARKERS),
        "investment_orchestrator.observability",
        "ltetf_target_architecture_prerequisite_catalog",
        "catalog_identity_sha256",
    )
    return any(marker in lowered for marker in markers)


def _is_report_artifact_path_value(value: str) -> bool:
    """Return whether a value denotes the isolated observer-report namespace.

    A report field name or catalog identity is observer-relevant, but it is
    not itself a report-artifact path.  Reader attribution must retain that
    distinction when independent wrapper parameters carry each category.
    """
    normalized = value.replace("\\", "/")
    return (
        REPORT_NAMESPACE_RELATIVE_PATH in normalized
        or "artifacts/target_architecture/report_only/ltetf_01" in normalized
    )


def _generic_scan_can_reach_report_namespace(value: str) -> bool:
    """Whether a literal scanner root can traverse the isolated report tree."""
    if not _is_normalized_relative_path(value):
        return False
    normalized = value.rstrip("/")
    namespace = REPORT_NAMESPACE_RELATIVE_PATH
    return (
        namespace == normalized
        or namespace.startswith(f"{normalized}/")
        or normalized.startswith(f"{namespace}/")
    )


def _node_has_observer_relevant_literal(
    node: ast.AST | None,
    bindings: Mapping[str, str],
) -> bool:
    if node is None:
        return False
    value = _bound_literal_path_expression(node, bindings)
    if value is not None:
        return _is_observer_relevant_value(value)
    return any(
        _is_observer_relevant_value(item.value)
        for item in ast.walk(node)
        if isinstance(item, ast.Constant) and type(item.value) is str
    )


def _tree_has_observer_relevance(tree: ast.AST, bindings: Mapping[str, str]) -> bool:
    return any(_is_observer_relevant_value(value) for value in (*_tree_string_literals(tree), *bindings.values()))


def _imports_in_tree(
    tree: ast.AST,
    module_name: str,
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    """Resolve bounded import bindings and fail closed only when relevant.

    The analysis deliberately follows only local literal aliases and one
    ``getattr`` dispatch.  Unknown dynamic code that has no observer/catalog
    relevance is not treated as an observer consumer; unknown relevant code is
    an inventory-integrity failure.
    """
    imports: set[str] = set()
    dynamic_imports: set[str] = set()
    findings: set[str] = set()
    bindings = _literal_bindings(tree)
    importlib_modules: set[str] = set()
    builtins_modules: set[str] = set()
    dynamic_functions: set[str] = {"__import__"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.add(alias.name)
                if alias.name == "importlib":
                    importlib_modules.add(alias.asname or alias.name)
                if alias.name == "builtins":
                    builtins_modules.add(alias.asname or alias.name)
        elif isinstance(node, ast.ImportFrom):
            base = _resolve_relative_import(module_name, node.level, node.module)
            if base is None:
                findings.add("unresolved_relative_import")
                continue
            imports.add(base)
            for alias in node.names:
                imports.add(f"{base}.{alias.name}")
                bound = alias.asname or alias.name
                if base == "importlib" and alias.name == "import_module":
                    dynamic_functions.add(bound)
                if base == "builtins" and alias.name == "__import__":
                    dynamic_functions.add(bound)

    local_definitions = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    }
    imported_getattr_bindings = {
        alias.asname or alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
        if alias.name == "getattr" and _resolve_relative_import(module_name, node.level, node.module) != "builtins"
    }
    getattr_is_builtin = "getattr" not in local_definitions | imported_getattr_bindings
    # A locally supplied function wins over a builtin/import-like spelling.
    dynamic_functions.difference_update(local_definitions)
    assignments = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
    ]
    unresolved_dispatchers: set[str] = set()
    for _ in range(8):
        changed = False
        for assignment in assignments:
            target = assignment.targets[0].id
            value = assignment.value
            kind: str | None = None
            if isinstance(value, ast.Name) and value.id in dynamic_functions:
                kind = "dynamic"
            elif isinstance(value, ast.Attribute) and isinstance(value.value, ast.Name):
                if value.value.id in importlib_modules and value.attr == "import_module":
                    kind = "dynamic"
                elif value.value.id in builtins_modules and value.attr == "__import__":
                    kind = "dynamic"
            elif (
                getattr_is_builtin
                and isinstance(value, ast.Call)
                and _call_name(value) == "getattr"
                and len(value.args) >= 2
            ):
                base = value.args[0]
                base_name = base.id if isinstance(base, ast.Name) else None
                selector = _bound_literal_path_expression(value.args[1], bindings)
                if base_name in importlib_modules | builtins_modules:
                    if selector in {"import_module", "__import__"}:
                        kind = "dynamic"
                    elif selector is None:
                        unresolved_dispatchers.add(target)
            if kind == "dynamic" and target not in dynamic_functions:
                dynamic_functions.add(target)
                changed = True
        if not changed:
            break

    def dynamic_kind(call: ast.Call) -> str | None:
        if isinstance(call.func, ast.Name):
            if call.func.id in dynamic_functions:
                return "dynamic"
            if call.func.id in unresolved_dispatchers:
                return "unresolved_dispatch"
        if isinstance(call.func, ast.Attribute) and isinstance(call.func.value, ast.Name):
            if call.func.value.id in importlib_modules and call.func.attr == "import_module":
                return "dynamic"
            if call.func.value.id in builtins_modules and call.func.attr == "__import__":
                return "dynamic"
        if (
            getattr_is_builtin
            and isinstance(call.func, ast.Call)
            and _call_name(call.func) == "getattr"
            and len(call.func.args) >= 2
        ):
            base = call.func.args[0]
            base_name = base.id if isinstance(base, ast.Name) else None
            selector = _bound_literal_path_expression(call.func.args[1], bindings)
            if base_name in importlib_modules | builtins_modules:
                return "dynamic" if selector in {"import_module", "__import__"} else "unresolved_dispatch"
        return None

    def record_dynamic_target(target: str) -> None:
        if target.startswith("."):
            resolved = _resolve_relative_dynamic_import(
                module_name.rsplit(".", 1)[0],
                target,
            )
            if resolved is None:
                findings.add("unresolved_dynamic_import")
                return
            target = resolved
        imports.add(target)
        dynamic_imports.add(target)

    parent_by_node = _ast_parent_index(tree)
    handled_wrapper_calls: set[int] = set()
    for function in (
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ):
        parameters = tuple(
            argument.arg
            for argument in (
                *function.args.posonlyargs,
                *function.args.args,
                *function.args.kwonlyargs,
                *((function.args.vararg,) if function.args.vararg is not None else ()),
                *((function.args.kwarg,) if function.args.kwarg is not None else ()),
            )
        )
        if not parameters:
            continue
        inner_calls = tuple(
            call
            for call in ast.walk(function)
            if isinstance(call, ast.Call)
            and dynamic_kind(call) == "dynamic"
            and call.args
            and isinstance(call.args[0], ast.Name)
            and call.args[0].id in parameters
        )
        if len(inner_calls) != 1:
            continue
        inner = inner_calls[0]
        parameter = inner.args[0].id
        parameter_order = parameters.index(parameter)
        call_sites = tuple(
            call
            for call in ast.walk(tree)
            if isinstance(call, ast.Call)
            and isinstance(call.func, ast.Name)
            and call.func.id == function.name
            and not _is_descendant_of(call, function, parent_by_node)
        )
        handled_wrapper_calls.add(id(inner))
        for call in call_sites:
            expression: ast.AST | None = (
                call.args[parameter_order]
                if parameter_order < len(call.args)
                else next(
                    (
                        keyword.value
                        for keyword in call.keywords
                        if keyword.arg == parameter
                    ),
                    None,
                )
            )
            target = _bound_literal_path_expression(expression, bindings)
            if target is None:
                findings.add("unresolved_dynamic_import")
            else:
                record_dynamic_target(target)

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if id(node) in handled_wrapper_calls:
            continue
        kind = dynamic_kind(node)
        if kind is None:
            continue
        target = _bound_literal_path_expression(node.args[0], bindings) if node.args else None
        if kind == "unresolved_dispatch":
            findings.add("unresolved_dynamic_import")
            continue
        if target is None:
            findings.add("unresolved_dynamic_import")
            continue
        if target.startswith("."):
            package = _bound_literal_path_expression(node.args[1], bindings) if len(node.args) > 1 else None
            if package is None:
                package = next(
                    (
                        _bound_literal_path_expression(keyword.value, bindings)
                        for keyword in node.keywords
                        if keyword.arg == "package"
                    ),
                    None,
                )
            if package is None:
                package = module_name.rsplit(".", 1)[0]
            resolved = _resolve_relative_dynamic_import(package, target)
            if resolved is None:
                findings.add("unresolved_dynamic_import")
            else:
                imports.add(resolved)
                dynamic_imports.add(resolved)
        else:
            imports.add(target)
            dynamic_imports.add(target)

    entry_point_factories = {
        alias.asname or alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and node.module in {"importlib.metadata", "importlib_metadata"}
        for alias in node.names
        if alias.name == "entry_points"
    }
    has_entry_point_provider_import = any(
        (
            isinstance(node, ast.Import)
            and any(
                alias.name in {"importlib.metadata", "importlib_metadata"}
                for alias in node.names
            )
        )
        or (
            isinstance(node, ast.ImportFrom)
            and (
                node.module in {"importlib.metadata", "importlib_metadata"}
                or (
                    node.module == "importlib"
                    and any(alias.name == "metadata" for alias in node.names)
                )
            )
        )
        for node in ast.walk(tree)
    )
    enumerates_entry_points = any(
        isinstance(node, ast.Call)
        and (
            (
                isinstance(node.func, ast.Name)
                and node.func.id in entry_point_factories
            )
            or (
                has_entry_point_provider_import
                and _call_name(node) == "entry_points"
            )
        )
        for node in ast.walk(tree)
    )
    if enumerates_entry_points and any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "load"
        for node in ast.walk(tree)
    ):
        findings.add("unresolved_dynamic_import")
    return (
        tuple(sorted(imports)),
        tuple(sorted(findings)),
        tuple(sorted(dynamic_imports)),
    )


def _literal_dynamic_code_facts(
    source: str,
    *,
    relevant_context: bool,
) -> tuple[bool, bool, bool]:
    """Inspect a literal ``exec``/``eval`` payload without ever executing it.

    The result is ``(unresolved, report_reader, policy_reader)``.  Only a
    literal connected to observer evidence is analyzed.  Constant-only
    expressions are harmless; imports, nested dynamic execution, and unknown
    calls with observer relevance are incomplete by construction.
    """
    if len(source) > 4_096:
        return relevant_context, False, False
    try:
        tree = ast.parse(source, mode="exec")
    except SyntaxError:
        return relevant_context, False, False
    nodes = tuple(ast.walk(tree))
    if len(nodes) > 256:
        return relevant_context, False, False
    literal_values = _tree_string_literals(tree)
    nested_relevant = relevant_context or any(
        _is_observer_relevant_value(value) for value in literal_values
    )
    if not nested_relevant:
        return False, False, False
    report_marker = any(_is_report_relevant_value(value) for value in literal_values)
    policy_marker = any(_POLICY_INPUT_RELATIVE_PATH in value for value in literal_values)
    report_reader = False
    policy_reader = False
    direct_read_names = {
        "open", "read_text", "read_bytes", "read_json", "read_yaml",
        "load", "safe_load", "load_json_file", "load_yaml_file",
        "load_json", "load_yaml",
    }
    scanner_names = {"glob", "rglob", "iterdir", "walk", "listdir", "scandir"}
    for node in nodes:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            return True, report_reader, policy_reader
        if isinstance(node, ast.Subscript):
            key = _literal_string(node.slice)
            if key in _UNIQUE_REPORT_FIELD_MARKERS:
                report_reader = True
            if key == "permitted_consumer_set":
                policy_reader = True
        if not isinstance(node, ast.Call):
            continue
        name = _call_name(node)
        if name in {"exec", "eval", "__import__", "import_module"}:
            return True, report_reader, policy_reader
        if name in direct_read_names or name in scanner_names:
            if report_marker or policy_marker:
                report_reader = report_reader or report_marker
                policy_reader = policy_reader or policy_marker
                continue
            return True, report_reader, policy_reader
        if isinstance(node.func, ast.Attribute) and node.func.attr in {
            "open", "read_text", "read_bytes", "glob", "rglob", "iterdir"
        }:
            if report_marker or policy_marker:
                report_reader = report_reader or report_marker
                policy_reader = policy_reader or policy_marker
                continue
            return True, report_reader, policy_reader
        # A literal call that is neither an observer-owned parser primitive nor
        # a known static file sink cannot be resolved without executing code.
        return True, report_reader, policy_reader
    return False, report_reader, policy_reader


def _iter_inventory_nodes(tree: ast.AST) -> Iterable[ast.AST]:
    """Yield direct production syntax without entering nested function bodies.

    A module-level function body remains an inventory surface, but a function
    nested inside it has no effect until the bounded parameter-flow analysis
    resolves and follows an explicit invocation.  This keeps direct inventory
    findings aligned with lexical call reachability without executing source.
    """
    def visit(node: ast.AST, function_depth: int) -> Iterable[ast.AST]:
        yield node
        if isinstance(node, ast.Lambda):
            return
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if function_depth >= 1:
                return
            function_depth += 1
        for child in ast.iter_child_nodes(node):
            yield from visit(child, function_depth)

    yield from visit(tree, 0)


def _file_access_findings(
    tree: ast.AST,
    *,
    relative_path: str,
    observer_internal_source: bool,
    module_name: str | None = None,
    sources: Mapping[str, _ParsedProductionSource] | None = None,
) -> tuple[tuple[str, ...], bool, bool, bool]:
    """Trace direct and bounded shared-loader reads into the observer namespace."""
    bindings = _literal_bindings(tree)
    global_relevant_names = _module_observer_relevant_name_bindings(tree, bindings)
    report_literal = any(_is_report_relevant_value(item) for item in _tree_string_literals(tree))
    policy_literal = any(item in _POLICY_MARKERS for item in _tree_string_literals(tree))
    observer_relevance = _tree_has_observer_relevance(tree, bindings)
    generic_artifact_scanner = False
    report_reader = False
    policy_reader = False
    findings: set[str] = set()
    scanner_names = {"glob", "rglob", "iterdir", "walk", "listdir", "scandir"}
    dynamic_import_modules: set[str] = set()
    dynamic_import_names: set[str] = {"__import__"}
    for import_node in ast.walk(tree):
        if isinstance(import_node, ast.Import):
            for alias in import_node.names:
                if alias.name in {"importlib", "builtins"}:
                    dynamic_import_modules.add(alias.asname or alias.name)
        elif isinstance(import_node, ast.ImportFrom):
            if import_node.module == "importlib":
                dynamic_import_names.update(
                    alias.asname or alias.name
                    for alias in import_node.names
                    if alias.name == "import_module"
                )
            if import_node.module == "builtins":
                dynamic_import_names.update(
                    alias.asname or alias.name
                    for alias in import_node.names
                    if alias.name == "__import__"
                )
    for _ in range(8):
        changed = False
        for assignment in (
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
        ):
            target = assignment.targets[0].id
            value = assignment.value
            dynamic = (
                isinstance(value, ast.Name)
                and value.id in dynamic_import_names
            ) or (
                isinstance(value, ast.Attribute)
                and isinstance(value.value, ast.Name)
                and value.value.id in dynamic_import_modules
                and value.attr in {"import_module", "__import__"}
            )
            if dynamic and target not in dynamic_import_names:
                dynamic_import_names.add(target)
                changed = True
        if not changed:
            break
    dynamic_wrapper_names = {
        function.name
        for function in ast.walk(tree)
        if isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef))
        and any(
            isinstance(call, ast.Call)
            and (
                (
                    isinstance(call.func, ast.Name)
                    and call.func.id in dynamic_import_names
                )
                or (
                    isinstance(call.func, ast.Attribute)
                    and isinstance(call.func.value, ast.Name)
                    and call.func.value.id in dynamic_import_modules
                    and call.func.attr in {"import_module", "__import__"}
                )
            )
            for call in ast.walk(function)
        )
    }

    def is_dynamic_import_dispatch(call: ast.Call) -> bool:
        if isinstance(call.func, ast.Name):
            return call.func.id in dynamic_import_names | dynamic_wrapper_names
        if (
            isinstance(call.func, ast.Call)
            and _call_name(call.func) == "getattr"
            and len(call.func.args) >= 2
            and isinstance(call.func.args[0], ast.Name)
            and call.func.args[0].id in dynamic_import_modules
            and _literal_string(call.func.args[1])
            in {"import_module", "__import__"}
        ):
            return True
        return bool(
            isinstance(call.func, ast.Attribute)
            and isinstance(call.func.value, ast.Name)
            and call.func.value.id in dynamic_import_modules
            and call.func.attr in {"import_module", "__import__"}
        )
    assignments = [
        node
        for node in _iter_inventory_nodes(tree)
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
    ]
    scope_index = _lexical_scope_index(tree)

    def classify_path(node: ast.AST | None) -> tuple[str | None, bool, bool, bool]:
        value = _bound_literal_path_expression(node, bindings)
        relevant = _node_has_observer_relevant_literal(node, bindings)
        literals = (
            tuple(
                item.value
                for item in ast.walk(node)
                if isinstance(item, ast.Constant) and type(item.value) is str
            )
            if node is not None
            else ()
        )
        policy = bool(
            value is not None and _POLICY_INPUT_RELATIVE_PATH in value
        ) or _POLICY_INPUT_RELATIVE_PATH in literals
        report = bool(value is not None and _is_report_artifact_path_value(value)) or any(
            _is_report_artifact_path_value(item) for item in literals
        )
        return value, report, policy, relevant

    def mark_path(node: ast.AST | None) -> None:
        nonlocal report_reader, policy_reader
        value, report, policy, relevant = classify_path(node)
        if observer_internal_source:
            return
        if value is not None and (
            value.startswith("/")
            or "\\" in value
            or ".." in PurePosixPath(value).parts
        ) and (report or policy):
            findings.add("repository_path_escape")
            return
        if value is None and relevant:
            findings.add("unresolved_dynamic_path")
            return
        report_reader = report_reader or report
        policy_reader = policy_reader or policy

    file_handles: dict[str, ast.AST] = {}
    for assignment in assignments:
        if isinstance(assignment.value, ast.Call) and (
            _known_external_loader_call(tree, assignment.value)
            or (
                isinstance(assignment.value.func, ast.Attribute)
                and assignment.value.func.attr in {"open", "read_text", "read_bytes"}
            )
        ):
            call = assignment.value
            path_node = call.args[0] if _call_name(call) == "open" and call.args else (call.func.value if isinstance(call.func, ast.Attribute) else None)
            if _node_has_observer_relevant_literal(path_node, bindings):
                file_handles[assignment.targets[0].id] = path_node

    for node in _iter_inventory_nodes(tree):
        if isinstance(node, ast.Subscript):
            key = _literal_string(node.slice)
            if not observer_internal_source and key in _UNIQUE_REPORT_FIELD_MARKERS:
                report_reader = True
            if not observer_internal_source and key == "permitted_consumer_set":
                policy_reader = True
        if not isinstance(node, ast.Call):
            continue
        name = _call_name(node)
        if is_dynamic_import_dispatch(node):
            # Dynamic module relations are classified by _imports_in_tree;
            # they are not file/report-loader calls.
            continue
        if name in {"exec", "eval"}:
            code = node.args[0] if node.args else None
            literal = _bound_literal_path_expression(code, bindings)
            relevant = _node_has_observer_relevant_literal(code, bindings) or observer_relevance
            if not observer_internal_source and relevant:
                if literal is None:
                    findings.add("dynamic_execution")
                else:
                    unresolved, nested_report, nested_policy = _literal_dynamic_code_facts(
                        literal,
                        # A literal may refer to a report path through a
                        # caller-local binding.  Once its enclosing call is
                        # observer-relevant, inspect it as such; this still
                        # permits a constant-only literal to remain harmless.
                        relevant_context=relevant,
                    )
                    if unresolved:
                        findings.add("dynamic_execution")
                    report_reader = report_reader or nested_report
                    policy_reader = policy_reader or nested_policy
            continue
        if name == "get" and node.args:
            key = _literal_string(node.args[0])
            if not observer_internal_source and key in _UNIQUE_REPORT_FIELD_MARKERS:
                report_reader = True
            if not observer_internal_source and key == "permitted_consumer_set":
                policy_reader = True
        if name in {"validate_artifact_schema", "load_schema", "load_json_schema", "get_schema"}:
            for argument in (*node.args, *(keyword.value for keyword in node.keywords)):
                mark_path(argument)
        raw_argument_relevance = tuple(
            classify_path(expression)
            for expression in _call_raw_argument_expressions(node)
        )
        callable_expression = (
            node.func.value if isinstance(node.func, ast.Attribute) else node.func
        )
        callable_relevant = (
            classify_path(callable_expression)[3]
            or _expression_uses_names(node.func, global_relevant_names)
        )
        raw_relevant = any(relevance[3] for relevance in raw_argument_relevance) or callable_relevant
        local = _lexical_function_resolution(tree, node, scope_index)
        if local.kind == "resolved" and local.function is not None:
            binding = _call_binding(node, local.function)
            bound_relevance = [
                (parameter, expression, classify_path(expression))
                for parameter, expression, _ in binding.parameter_arguments
            ]
            relevant_call = raw_relevant or any(
                relevance[3] for _, _, relevance in bound_relevance
            )
            if not observer_internal_source and relevant_call and (
                binding.invalid or binding.unresolved_argument_expressions
            ):
                findings.add("unresolved_dynamic_path")
                continue
            for parameter, expression, (_, report, policy, relevant) in bound_relevance:
                if observer_internal_source or not relevant:
                    continue
                flow = _parameter_flow_kind(
                    tree,
                    local.function,
                    {parameter},
                    module_name=module_name,
                    sources=sources,
                    scope_index=scope_index,
                )
                if flow == "reader":
                    mark_path(expression)
                elif flow == "unresolved":
                    findings.add("unresolved_dynamic_path")
            # A no-argument local wrapper can deterministically capture a
            # module-level observer value.  Trace each such value separately
            # only because the wrapper is explicitly invoked; its nested
            # definitions otherwise remain outside the direct inventory walk.
            if not observer_internal_source and not binding.invalid:
                parameters = set(_function_parameters(local.function))
                for captured_name in sorted(global_relevant_names - parameters):
                    flow = _parameter_flow_kind(
                        tree,
                        local.function,
                        set(),
                        module_name=module_name,
                        sources=sources,
                        captured_aliases=frozenset({captured_name}),
                        scope_index=scope_index,
                    )
                    if flow == "reader":
                        mark_path(ast.Name(id=captured_name, ctx=ast.Load()))
                    elif flow == "unresolved":
                        findings.add("unresolved_dynamic_path")
            continue
        if local.kind in {"ambiguous", "rebound"}:
            if (
                not observer_internal_source
                and raw_relevant
                and not (
                    local.kind == "rebound"
                    and _safe_callable_alias_at_call(tree, node, scope_index)
                )
            ):
                findings.add("unresolved_dynamic_path")
            continue

        imported = (
            _lexical_repository_import_resolution(
                tree,
                node,
                module_name=module_name,
                sources=sources,
                scope_index=scope_index,
            )
            if module_name is not None and sources is not None
            else _ImportedCallableResolution(False)
        )
        if imported.found:
            if not observer_internal_source and raw_relevant and (
                imported.module is None or imported.symbol is None
            ):
                findings.add("unresolved_dynamic_path")
            # Repository-local callables are traced by the dedicated imported
            # wrapper pass after every source tree has been parsed.
            continue

        is_direct_reader = _known_external_loader_call(tree, node) or bool(
            isinstance(node.func, ast.Attribute)
            and node.func.attr in {"open", "read_text", "read_bytes"}
        )
        is_direct_scanner = (
            name in scanner_names
            and (
                _known_external_loader_call(tree, node)
                or (
                    isinstance(node.func, ast.Attribute)
                    and node.func.attr in scanner_names
                )
            )
        )
        if is_direct_scanner:
            path_node = node.func.value if isinstance(node.func, ast.Attribute) else (node.args[0] if node.args else None)
            value, report, policy, relevant = classify_path(path_node)
            if value is None and node.args:
                path_node = node.args[0]
                value, report, policy, relevant = classify_path(path_node)
            if not observer_internal_source:
                if value is None and (relevant or report_literal or policy_literal):
                    findings.add("unresolved_dynamic_path")
                elif value is not None and (
                    value.startswith("/") or "\\" in value or ".." in PurePosixPath(value).parts
                ) and (report or policy):
                    findings.add("repository_path_escape")
                elif value is not None and _generic_scan_can_reach_report_namespace(value):
                    generic_artifact_scanner = True
        if is_direct_reader:
            path_node: ast.AST | None = None
            if name == "open" and node.args:
                path_node = node.args[0]
            elif isinstance(node.func, ast.Attribute) and name in {"open", "read_text", "read_bytes"}:
                path_node = node.func.value
            elif node.args:
                path_node = node.args[0]
            if isinstance(path_node, ast.Name) and path_node.id in file_handles:
                path_node = file_handles[path_node.id]
            mark_path(path_node)
            continue
        if (
            not observer_internal_source
            and raw_relevant
            and not _call_is_benign_transform(node)
            and not _safe_callable_alias_at_call(tree, node, scope_index)
        ):
            findings.add("unresolved_dynamic_path")
    if not observer_internal_source and generic_artifact_scanner:
        # A broad scan over artifacts can ingest this namespace after report publication.
        report_reader = True
    return tuple(sorted(findings)), report_reader, policy_reader, generic_artifact_scanner


def _broker_capability_findings(tree: ast.AST) -> tuple[str, ...]:
    """Find executable broker/live-order capability symbols, excluding prose."""
    operation_names = {
        "submit_order",
        "place_order",
        "execute_order",
        "send_order",
        "create_order",
        "cancel_order",
        "modify_order",
    }
    findings: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = _call_name(node)
            if name in operation_names:
                findings.add(f"call:{name}")
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in operation_names:
            findings.add(f"function:{node.name}")
        elif isinstance(node, ast.ClassDef) and "broker" in node.name.lower():
            findings.add(f"class:{node.name}")
    return tuple(sorted(findings))


def _scan_pyproject_entry_points(root: Path) -> tuple[tuple[str, str], ...]:
    path = _safe_repository_file_path(root, "pyproject.toml")
    if path is None:
        raise ObserverIntegrityError("CONSUMER_INVENTORY_INCOMPLETE")
    try:
        payload = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
        raise ObserverIntegrityError("CONSUMER_INVENTORY_INCOMPLETE") from None
    if type(payload) is not dict:
        raise ObserverIntegrityError("CONSUMER_INVENTORY_INCOMPLETE")
    result: list[tuple[str, str]] = []
    project = payload.get("project")
    if project is not None and type(project) is not dict:
        raise ObserverIntegrityError("CONSUMER_INVENTORY_INCOMPLETE")
    if type(project) is dict:
        for section in ("scripts", "gui-scripts"):
            values = project.get(section, {})
            if type(values) is not dict or any(type(k) is not str or type(v) is not str for k, v in values.items()):
                raise ObserverIntegrityError("CONSUMER_INVENTORY_INCOMPLETE")
            result.extend((f"pyproject.toml:project.{section}.{name}", target) for name, target in values.items())
        groups = project.get("entry-points", {})
        if type(groups) is not dict:
            raise ObserverIntegrityError("CONSUMER_INVENTORY_INCOMPLETE")
        for group, values in groups.items():
            if type(group) is not str or type(values) is not dict or any(type(k) is not str or type(v) is not str for k, v in values.items()):
                raise ObserverIntegrityError("CONSUMER_INVENTORY_INCOMPLETE")
            result.extend((f"pyproject.toml:project.entry-points.{group}.{name}", target) for name, target in values.items())

    def walk_tool(value: object, locator: str) -> None:
        if type(value) is not dict:
            return
        for key, nested in value.items():
            if type(key) is not str:
                raise ObserverIntegrityError("CONSUMER_INVENTORY_INCOMPLETE")
            child = f"{locator}.{key}"
            lowered = key.lower().replace("_", "-")
            if "entry-point" in lowered or "plugin" in lowered:
                if type(nested) is dict:
                    for name, target in nested.items():
                        if type(name) is not str or type(target) is not str:
                            raise ObserverIntegrityError("CONSUMER_INVENTORY_INCOMPLETE")
                        result.append((f"{child}.{name}", target))
                elif type(nested) is str:
                    result.append((child, nested))
                else:
                    raise ObserverIntegrityError("CONSUMER_INVENTORY_INCOMPLETE")
            walk_tool(nested, child)

    walk_tool(payload.get("tool", {}), "pyproject.toml:tool")
    return tuple(sorted(set(result)))


@dataclass(frozen=True, slots=True)
class _ParsedProductionSource:
    """One source-only production scan result used for bounded flow analysis."""

    relative_path: str
    module_name: str
    tree: ast.AST
    imports: tuple[str, ...]
    dynamic_imports: tuple[str, ...]
    findings: tuple[str, ...]
    report_reader: bool
    policy_reader: bool
    broker_capabilities: tuple[str, ...]


def _source_module_for_import(
    imported: str,
    *,
    caller_module: str,
    sources: Mapping[str, _ParsedProductionSource],
) -> str | None:
    """Resolve a repository-local import without importing the module."""
    candidates = [imported]
    if not imported.startswith("investment_orchestrator."):
        package = caller_module.rsplit(".", 1)[0]
        candidates.append(f"{package}.{imported}")
    for candidate in candidates:
        if candidate in sources:
            return candidate
        # ``_module_name_for_path`` intentionally gives a package initializer
        # a concrete source-module name.  Resolve imports of the package to
        # that source without importing the package itself.
        initializer = f"{candidate}.__init__"
        if initializer in sources:
            return initializer
    return None


def _expression_uses_names(node: ast.AST | None, names: set[str]) -> bool:
    return bool(
        node is not None
        and any(isinstance(item, ast.Name) and item.id in names for item in ast.walk(node))
    )


def _call_raw_argument_expressions(call: ast.Call) -> tuple[ast.AST, ...]:
    """Return every source expression supplied to a call without binding it."""
    return tuple(
        (
            argument.value
            if isinstance(argument, ast.Starred)
            else argument
        )
        for argument in (*call.args, *(keyword.value for keyword in call.keywords))
    )


def _assignment_target_names(node: ast.AST) -> set[str]:
    """Return simple local names rebound by one assignment-like target."""
    if isinstance(node, ast.Name):
        return {node.id}
    if isinstance(node, (ast.Tuple, ast.List)):
        return {
            name
            for element in node.elts
            for name in _assignment_target_names(element)
        }
    return set()


def _pattern_bound_names(pattern: ast.AST) -> tuple[tuple[str, ast.AST], ...]:
    """Return the non-wildcard names conditionally captured by a pattern.

    Structural pattern matching binds captures before a case guard and body.
    This observer does not evaluate patterns; it records every syntactically
    possible capture so later lexical resolution remains conservative.
    """
    result: list[tuple[str, ast.AST]] = []

    def add(name: str | None, node: ast.AST) -> None:
        if name is not None and name != "_":
            result.append((name, node))

    def visit(node: ast.AST) -> None:
        if isinstance(node, ast.MatchAs):
            add(node.name, node)
            if node.pattern is not None:
                visit(node.pattern)
            return
        if isinstance(node, ast.MatchStar):
            add(node.name, node)
            return
        if isinstance(node, ast.MatchMapping):
            add(node.rest, node)
            for child in node.patterns:
                visit(child)
            return
        if isinstance(node, ast.MatchSequence):
            for child in node.patterns:
                visit(child)
            return
        if isinstance(node, ast.MatchClass):
            for child in (*node.patterns, *node.kwd_patterns):
                visit(child)
            return
        if isinstance(node, ast.MatchOr):
            for child in node.patterns:
                visit(child)

    visit(pattern)
    return tuple(result)


def _statement_binding_names(statement: ast.stmt) -> set[str]:
    """Return names a statement can bind in its enclosing lexical scope.

    Nested function/class bodies are deliberately not descended into: their
    definitions bind only their own name in the enclosing scope.  Control
    blocks do not introduce Python lexical scopes, so their possible bindings
    are retained for ambiguity detection.
    """
    if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return {statement.name}
    if isinstance(statement, ast.Assign):
        return {
            name
            for target in statement.targets
            for name in _assignment_target_names(target)
        }
    if isinstance(statement, ast.AnnAssign):
        return _assignment_target_names(statement.target)
    if isinstance(statement, ast.AugAssign):
        return _assignment_target_names(statement.target)
    if isinstance(statement, (ast.For, ast.AsyncFor)):
        return _assignment_target_names(statement.target) | {
            name
            for child in (*statement.body, *statement.orelse)
            for name in _statement_binding_names(child)
        }
    if isinstance(statement, (ast.With, ast.AsyncWith)):
        return {
            name
            for item in statement.items
            if item.optional_vars is not None
            for name in _assignment_target_names(item.optional_vars)
        } | {
            name
            for child in statement.body
            for name in _statement_binding_names(child)
        }
    if isinstance(statement, ast.If):
        return {
            name
            for child in (*statement.body, *statement.orelse)
            for name in _statement_binding_names(child)
        }
    if isinstance(statement, ast.Match):
        return {
            name
            for case in statement.cases
            for name, _node in _pattern_bound_names(case.pattern)
        } | {
            name
            for case in statement.cases
            for child in case.body
            for name in _statement_binding_names(child)
        }
    if isinstance(statement, ast.Try):
        return {
            name
            for child in (
                *statement.body,
                *(nested for handler in statement.handlers for nested in handler.body),
                *statement.orelse,
                *statement.finalbody,
            )
            for name in _statement_binding_names(child)
        }
    if isinstance(statement, (ast.Import, ast.ImportFrom)):
        return {
            alias.asname or alias.name.split(".", 1)[0]
            for alias in statement.names
            if alias.name != "*"
        }
    return set()


@dataclass(frozen=True, slots=True)
class _CallableResolution:
    """One lexical callable lookup result, never a runtime resolution."""

    kind: str
    function: ast.FunctionDef | ast.AsyncFunctionDef | None = None


@dataclass(frozen=True, slots=True)
class _LexicalScopeIndex:
    """Parent scopes and enclosing scopes for one parsed source tree."""

    enclosing_scope_by_node: Mapping[int, ast.AST]
    parent_scope_by_scope: Mapping[int, ast.AST | None]


def _lexical_scope_index(tree: ast.AST) -> _LexicalScopeIndex:
    """Index Python lexical function scopes without importing source code."""
    enclosing: dict[int, ast.AST] = {}
    parents: dict[int, ast.AST | None] = {id(tree): None}

    def visit(node: ast.AST, scope: ast.AST) -> None:
        enclosing[id(node)] = scope
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            parents[id(node)] = scope
            for child in ast.iter_child_nodes(node):
                visit(child, node)
            return
        for child in ast.iter_child_nodes(node):
            visit(child, scope)

    visit(tree, tree)
    return _LexicalScopeIndex(enclosing, parents)


def _scope_body(scope: ast.AST) -> Sequence[ast.stmt]:
    if isinstance(
        scope,
        (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef),
    ):
        return scope.body
    return ()


def _statement_is_before(statement: ast.stmt, node: ast.AST | None) -> bool:
    if node is None:
        return True
    statement_position = (getattr(statement, "lineno", -1), getattr(statement, "col_offset", -1))
    node_position = (getattr(node, "lineno", -1), getattr(node, "col_offset", -1))
    return statement_position < node_position


def _literal_bool(node: ast.AST) -> bool | None:
    return node.value if isinstance(node, ast.Constant) and type(node.value) is bool else None


def _scope_callable_resolution(
    scope: ast.AST,
    name: str,
    *,
    before: ast.AST | None,
) -> _CallableResolution:
    """Resolve a direct lexical definition, preserving conditional ambiguity."""
    state: _CallableResolution = _CallableResolution("not_found")
    scope_binds_name = False

    def apply(statement: ast.stmt) -> None:
        nonlocal state, scope_binds_name
        if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)) and statement.name == name:
            scope_binds_name = True
            state = _CallableResolution("resolved", statement)
            return
        if isinstance(statement, ast.ClassDef) and statement.name == name:
            scope_binds_name = True
            state = _CallableResolution("rebound")
            return
        if isinstance(statement, ast.If):
            condition = _literal_bool(statement.test)
            if condition is not None:
                for child in statement.body if condition else statement.orelse:
                    apply(child)
                return
            if name in _statement_binding_names(statement):
                scope_binds_name = True
                state = _CallableResolution("ambiguous")
            return
        if isinstance(statement, (ast.Match, ast.Try, ast.For, ast.AsyncFor, ast.While, ast.With, ast.AsyncWith)):
            if name in _statement_binding_names(statement):
                scope_binds_name = True
                state = _CallableResolution("ambiguous")
            return
        if name in _statement_binding_names(statement):
            # Imports are handled by the repository-import binding resolver;
            # they retain an explicit lexical binding so an import after a
            # call cannot be mistaken for a global definition.  An explicit
            # assignment or rebinding has no analyzable callable identity.
            if isinstance(statement, (ast.Import, ast.ImportFrom)):
                scope_binds_name = True
                state = _CallableResolution("imported")
                return
            scope_binds_name = True
            state = _CallableResolution("rebound")

    for statement in _scope_body(scope):
        if before is not None and not _statement_is_before(statement, before):
            if name in _statement_binding_names(statement):
                scope_binds_name = True
            continue
        apply(statement)
    if state.kind == "not_found" and scope_binds_name:
        return _CallableResolution("rebound")
    return state


def _lexical_function_resolution(
    tree: ast.AST,
    call: ast.Call,
    scope_index: _LexicalScopeIndex,
) -> _CallableResolution:
    """Resolve a name call through lexical scopes, never a flattened AST."""
    if not isinstance(call.func, ast.Name):
        return _CallableResolution("unsupported")
    name = call.func.id
    scope = scope_index.enclosing_scope_by_node.get(id(call), tree)
    while scope is not None:
        resolution = _scope_callable_resolution(scope, name, before=call)
        if resolution.kind != "not_found":
            return resolution
        scope = scope_index.parent_scope_by_scope.get(id(scope))
    return _CallableResolution("not_found")


def _module_function_resolution(
    tree: ast.AST,
    name: str,
) -> _CallableResolution:
    """Resolve an exported source function while rejecting conditional twins."""
    return _scope_callable_resolution(tree, name, before=None)


def _is_proven_nonreader_lambda_factory(
    function: ast.FunctionDef | ast.AsyncFunctionDef,
) -> bool:
    """Recognize an exact local identity/constant lambda factory.

    This is intentionally narrower than general return-value analysis.  It
    preserves the established safe case of a locally defined replacement for
    ``getattr`` returning ``lambda value: value`` without treating arbitrary
    returned callables as resolved.
    """
    statements = tuple(
        statement
        for statement in function.body
        if not (
            isinstance(statement, ast.Expr)
            and isinstance(statement.value, ast.Constant)
            and type(statement.value.value) is str
        )
    )
    return bool(
        len(statements) == 1
        and isinstance(statements[0], ast.Return)
        and isinstance(statements[0].value, ast.Lambda)
        and isinstance(statements[0].value.body, (ast.Name, ast.Constant))
    )


def _safe_callable_alias_at_call(
    tree: ast.AST,
    call: ast.Call,
    scope_index: _LexicalScopeIndex,
) -> bool:
    """Resolve one statement-ordered alias to a proven non-reader lambda."""
    if not isinstance(call.func, ast.Name):
        return False
    name = call.func.id

    def scope_value(scope: ast.AST) -> tuple[bool, bool]:
        value: bool | None = None
        future_binding = False
        for statement in _scope_body(scope):
            if not _statement_is_before(statement, call):
                future_binding = future_binding or name in _statement_binding_names(statement)
                continue
            if (
                isinstance(statement, ast.Assign)
                and len(statement.targets) == 1
                and isinstance(statement.targets[0], ast.Name)
                and statement.targets[0].id == name
            ):
                if isinstance(statement.value, ast.Call):
                    resolution = _lexical_function_resolution(
                        tree,
                        statement.value,
                        scope_index,
                    )
                    value = bool(
                        resolution.kind == "resolved"
                        and resolution.function is not None
                        and _is_proven_nonreader_lambda_factory(resolution.function)
                    )
                else:
                    value = False
                continue
            if name in _statement_binding_names(statement):
                value = False
        if value is not None:
            return True, value
        return (True, False) if future_binding else (False, False)

    scope = scope_index.enclosing_scope_by_node.get(id(call), tree)
    while scope is not None:
        found, safe = scope_value(scope)
        if found:
            return safe
        scope = scope_index.parent_scope_by_scope.get(id(scope))
    return False


@dataclass(frozen=True, slots=True)
class _ImportedCallableResolution:
    """One lexical repository-import binding, without importing anything."""

    found: bool
    module: str | None = None
    symbol: str | None = None


def _direct_repository_import_resolution(
    statement: ast.stmt,
    name: str,
    *,
    module_name: str,
    sources: Mapping[str, _ParsedProductionSource],
) -> _ImportedCallableResolution:
    """Resolve one exact import binding in the statement's own scope."""
    if isinstance(statement, ast.ImportFrom):
        base = _resolve_relative_import(module_name, statement.level, statement.module)
        if base is None or base in {"builtins", "json", "os", "pathlib", "typing", "yaml"}:
            return _ImportedCallableResolution(False)
        for alias in statement.names:
            bound = alias.asname or alias.name
            if alias.name == "*" or bound != name:
                continue
            if statement.module is None:
                child_target = _source_module_for_import(
                    f"{base}.{alias.name}",
                    caller_module=module_name,
                    sources=sources,
                )
                if child_target is not None:
                    return _ImportedCallableResolution(True, child_target, None)
            target = _source_module_for_import(
                base,
                caller_module=module_name,
                sources=sources,
            )
            if target is not None:
                return _ImportedCallableResolution(True, target, alias.name)
            child_target = _source_module_for_import(
                f"{base}.{alias.name}",
                caller_module=module_name,
                sources=sources,
            )
            if child_target is not None:
                return _ImportedCallableResolution(True, child_target, None)
            return _ImportedCallableResolution(True)
        return _ImportedCallableResolution(False)
    if isinstance(statement, ast.Import):
        for alias in statement.names:
            bound = alias.asname or alias.name.split(".", 1)[0]
            if bound != name:
                continue
            target = _source_module_for_import(
                alias.name,
                caller_module=module_name,
                sources=sources,
            )
            return (
                _ImportedCallableResolution(True, target, None)
                if target is not None
                else _ImportedCallableResolution(False)
            )
    return _ImportedCallableResolution(False)


def _scope_repository_import_resolution(
    scope: ast.AST,
    name: str,
    *,
    before: ast.AST | None,
    module_name: str,
    sources: Mapping[str, _ParsedProductionSource],
) -> _ImportedCallableResolution:
    """Resolve a repository import in one lexical scope in source order."""
    state = _ImportedCallableResolution(False)
    future_binding = False

    def apply(statement: ast.stmt) -> None:
        nonlocal state
        imported = _direct_repository_import_resolution(
            statement,
            name,
            module_name=module_name,
            sources=sources,
        )
        if imported.found:
            state = imported
            return
        if isinstance(statement, (ast.Import, ast.ImportFrom)):
            return
        if isinstance(statement, ast.If):
            condition = _literal_bool(statement.test)
            if condition is not None:
                for child in statement.body if condition else statement.orelse:
                    apply(child)
            elif name in _statement_binding_names(statement):
                state = _ImportedCallableResolution(True)
            return
        if isinstance(statement, (ast.Match, ast.Try, ast.For, ast.AsyncFor, ast.While, ast.With, ast.AsyncWith)):
            if name in _statement_binding_names(statement):
                state = _ImportedCallableResolution(True)
            return
        if name in _statement_binding_names(statement):
            state = _ImportedCallableResolution(True)

    for statement in _scope_body(scope):
        if before is not None and not _statement_is_before(statement, before):
            future_binding = future_binding or name in _statement_binding_names(statement)
            continue
        apply(statement)
    if state.found:
        return state
    return _ImportedCallableResolution(True) if future_binding else state


def _lexical_repository_import_resolution(
    tree: ast.AST,
    call: ast.Call,
    *,
    module_name: str,
    sources: Mapping[str, _ParsedProductionSource],
    scope_index: _LexicalScopeIndex,
) -> _ImportedCallableResolution:
    """Resolve only the exact lexical repository binding used by this call."""
    if isinstance(call.func, ast.Name):
        bound_name = call.func.id
        attribute_symbol: str | None = None
    elif isinstance(call.func, ast.Attribute) and isinstance(call.func.value, ast.Name):
        bound_name = call.func.value.id
        attribute_symbol = call.func.attr
    else:
        return _ImportedCallableResolution(False)
    scope = scope_index.enclosing_scope_by_node.get(id(call), tree)
    while scope is not None:
        resolution = _scope_repository_import_resolution(
            scope,
            bound_name,
            before=call,
            module_name=module_name,
            sources=sources,
        )
        if resolution.found:
            return _ImportedCallableResolution(
                True,
                resolution.module,
                attribute_symbol if attribute_symbol is not None else resolution.symbol,
            )
        scope = scope_index.parent_scope_by_scope.get(id(scope))
    return _ImportedCallableResolution(False)


@dataclass(frozen=True, slots=True)
class _CallBinding:
    """A bounded source-only binding from callee parameters to expressions."""

    parameter_arguments: tuple[tuple[str, ast.AST, bool], ...]
    unresolved_argument_expressions: tuple[ast.AST, ...]
    rejected_argument_expressions: tuple[ast.AST, ...]
    raw_argument_expressions: tuple[ast.AST, ...]
    invalid_reasons: tuple[str, ...]
    invalid: bool


def _function_parameters(
    function: ast.FunctionDef | ast.AsyncFunctionDef,
) -> tuple[str, ...]:
    return tuple(
        argument.arg
        for argument in (
            *function.args.posonlyargs,
            *function.args.args,
            *function.args.kwonlyargs,
        )
    )


def _call_binding(
    call: ast.Call,
    function: ast.FunctionDef | ast.AsyncFunctionDef,
) -> _CallBinding:
    """Bind only ordinary Python call forms without evaluating either side.

    ``*args`` and ``**kwargs`` are deliberately retained as unresolved source
    expressions.  They may be harmless for an unrelated call, but a report-
    relevant flow through either form is not safe to infer.
    """
    positional = tuple((*function.args.posonlyargs, *function.args.args))
    keyword_capable = {argument.arg for argument in function.args.args}
    keyword_only = {argument.arg for argument in function.args.kwonlyargs}
    ordinary = (*positional, *function.args.kwonlyargs)
    ordinary_names = tuple(argument.arg for argument in ordinary)
    raw_arguments = _call_raw_argument_expressions(call)
    if len(set(ordinary_names)) != len(ordinary_names):
        return _CallBinding(
            (),
            (),
            raw_arguments,
            raw_arguments,
            ("unsupported_signature",),
            True,
        )

    defaults: dict[str, ast.AST] = {}
    if function.args.defaults:
        for argument, value in zip(positional[-len(function.args.defaults):], function.args.defaults, strict=True):
            defaults[argument.arg] = value
    for argument, value in zip(function.args.kwonlyargs, function.args.kw_defaults, strict=True):
        if value is not None:
            defaults[argument.arg] = value

    bound: dict[str, tuple[ast.AST, bool]] = {}
    unresolved: list[ast.AST] = []
    rejected: list[ast.AST] = []
    invalid_reasons: list[str] = []
    invalid = False
    position = 0
    for value in call.args:
        if isinstance(value, ast.Starred):
            unresolved.append(value.value)
            continue
        if position < len(positional):
            name = positional[position].arg
            if name in bound:
                invalid = True
                invalid_reasons.append("duplicate_binding")
                rejected.append(value)
            else:
                bound[name] = (value, False)
            position += 1
            continue
        if function.args.vararg is None:
            invalid = True
            invalid_reasons.append("too_many_positional_arguments")
            rejected.append(value)
        else:
            # The variadic receiver has no finite parameter-level mapping.
            unresolved.append(value)

    for keyword in call.keywords:
        if keyword.arg is None:
            unresolved.append(keyword.value)
            continue
        if keyword.arg in {argument.arg for argument in function.args.posonlyargs}:
            invalid = True
            invalid_reasons.append("positional_only_keyword")
            rejected.append(keyword.value)
            continue
        if keyword.arg in keyword_capable | keyword_only:
            if keyword.arg in bound:
                invalid = True
                invalid_reasons.append("duplicate_binding")
                rejected.append(keyword.value)
            else:
                bound[keyword.arg] = (keyword.value, False)
            continue
        if function.args.kwarg is None:
            invalid = True
            invalid_reasons.append("unknown_keyword")
            rejected.append(keyword.value)
        else:
            # As with ``*args``, do not attribute a dynamic **kwargs value to
            # a specific ordinary parameter.
            unresolved.append(keyword.value)

    for name, value in defaults.items():
        bound.setdefault(name, (value, True))

    # A source-only inventory may not silently model a call that Python would
    # reject before the callee runs.  Every ordinary parameter must therefore
    # be bound either by the call or by its declared default.  ``*args`` and
    # ``**kwargs`` are intentionally not ordinary parameters: their empty
    # forms are valid, while an expansion that could affect a relevant call is
    # retained above as unresolved evidence.
    if any(name not in bound for name in ordinary_names):
        invalid = True
        invalid_reasons.append("missing_required_parameter")

    return _CallBinding(
        parameter_arguments=tuple(
            (name, value, defaulted)
            for name, (value, defaulted) in bound.items()
        ),
        unresolved_argument_expressions=tuple(unresolved),
        rejected_argument_expressions=tuple(rejected),
        raw_argument_expressions=raw_arguments,
        invalid_reasons=tuple(sorted(set(invalid_reasons))),
        invalid=invalid,
    )


@lru_cache(maxsize=512)
def _known_external_loader_bindings(
    tree: ast.AST,
) -> tuple[frozenset[str], tuple[tuple[str, str], ...]]:
    """Return bounded exact standard-loader bindings for one parsed tree."""
    standard_functions = {
        "builtins": frozenset({"open"}),
        "json": frozenset({"load", "loads"}),
        "yaml": frozenset({"load", "safe_load", "full_load", "unsafe_load"}),
        "os": frozenset({"walk", "listdir", "scandir"}),
    }
    standard_attributes = {
        "json": frozenset({"load", "loads"}),
        "yaml": frozenset({"load", "safe_load", "full_load", "unsafe_load"}),
        "os": frozenset({"walk", "listdir", "scandir"}),
    }
    imported_functions: set[str] = set()
    imported_modules: dict[str, str] = {}
    locally_bound: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            for alias in node.names:
                bound = alias.asname or alias.name
                locally_bound.add(bound)
                if alias.name in standard_functions.get(module, frozenset()):
                    imported_functions.add(bound)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                bound = alias.asname or alias.name.split(".", 1)[0]
                locally_bound.add(bound)
                if alias.name in standard_attributes:
                    imported_modules[bound] = alias.name
        elif isinstance(node, ast.Assign):
            locally_bound.update(
                target.id for target in node.targets if isinstance(target, ast.Name)
            )
        elif isinstance(node, (ast.AnnAssign, ast.NamedExpr)) and isinstance(node.target, ast.Name):
            locally_bound.add(node.target.id)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            locally_bound.add(node.name)
    if "open" not in locally_bound:
        imported_functions.add("open")
    return frozenset(imported_functions), tuple(sorted(imported_modules.items()))


def _known_external_loader_call(tree: ast.AST, call: ast.Call) -> bool:
    """Recognize only exact standard-library/third-party loader bindings.

    Repository-local callables are deliberately *not* recognized by spelling.
    They are resolved and traced through their source by
    :func:`_parameter_flow_kind`; a local ``read_json`` helper therefore has
    no reader authority unless its body reaches a real source-level sink.
    """
    standard_attributes = {
        "json": frozenset({"load", "loads"}),
        "yaml": frozenset({"load", "safe_load", "full_load", "unsafe_load"}),
        "os": frozenset({"walk", "listdir", "scandir"}),
    }
    imported_functions, imported_module_items = _known_external_loader_bindings(tree)
    imported_modules = dict(imported_module_items)

    if isinstance(call.func, ast.Name):
        name = call.func.id
        if name in imported_functions:
            return True
        return False
    if isinstance(call.func, ast.Attribute) and isinstance(call.func.value, ast.Name):
        module = imported_modules.get(call.func.value.id)
        return module is not None and call.func.attr in standard_attributes[module]
    return False


def _iter_executed_calls(node: ast.AST) -> Iterable[ast.Call]:
    """Yield calls in executable syntax while excluding nested code objects."""
    def visit(current: ast.AST, *, root: bool) -> Iterable[ast.Call]:
        if not root and isinstance(
            current,
            (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda),
        ):
            return
        if isinstance(current, ast.Call):
            yield current
        for child in ast.iter_child_nodes(current):
            yield from visit(child, root=False)

    yield from visit(node, root=True)


def _iter_nodes_without_nested_scopes(node: ast.AST) -> Iterable[ast.AST]:
    """Yield lexical-body nodes without walking into a child code object."""
    def visit(current: ast.AST, *, root: bool) -> Iterable[ast.AST]:
        if not root and isinstance(
            current,
            (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda),
        ):
            return
        yield current
        for child in ast.iter_child_nodes(current):
            yield from visit(child, root=False)

    yield from visit(node, root=True)


def _function_captured_aliases(
    function: ast.FunctionDef | ast.AsyncFunctionDef,
    aliases: set[str],
) -> set[str]:
    """Return only deterministically captured aliases used by one nested def."""
    locally_bound = set(_function_parameters(function))
    global_names: set[str] = set()
    used_names: set[str] = set()
    for statement in function.body:
        locally_bound.update(_statement_binding_names(statement))
        for node in _iter_nodes_without_nested_scopes(statement):
            if isinstance(node, (ast.Global, ast.Nonlocal)):
                global_names.update(node.names)
            elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                used_names.add(node.id)
    return aliases & used_names - locally_bound - global_names


def _ambiguous_callable_captures_alias(
    scope: ast.AST,
    name: str,
    aliases: set[str],
) -> bool:
    """Whether a conditionally bound local function could capture this flow."""
    def visit(statement: ast.stmt) -> bool:
        if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return statement.name == name and bool(
                _function_captured_aliases(statement, aliases)
            )
        if isinstance(statement, ast.If):
            return any(visit(child) for child in (*statement.body, *statement.orelse))
        if isinstance(statement, ast.Match):
            return any(visit(child) for case in statement.cases for child in case.body)
        if isinstance(statement, ast.Try):
            return any(
                visit(child)
                for child in (
                    *statement.body,
                    *(nested for handler in statement.handlers for nested in handler.body),
                    *statement.orelse,
                    *statement.finalbody,
                )
            )
        if isinstance(statement, (ast.For, ast.AsyncFor, ast.While, ast.With, ast.AsyncWith)):
            children = list(statement.body)
            if isinstance(statement, (ast.For, ast.AsyncFor, ast.While)):
                children.extend(statement.orelse)
            return any(visit(child) for child in children)
        return False

    for statement in _scope_body(scope):
        if visit(statement):
            return True
    return False


def _flow_callable_identity(
    module_name: str | None,
    function: ast.FunctionDef | ast.AsyncFunctionDef,
) -> str:
    return (
        f"{module_name or '<local>'}:{function.name}:"
        f"{getattr(function, 'lineno', -1)}:{getattr(function, 'col_offset', -1)}"
    )


def _call_is_benign_transform(call: ast.Call) -> bool:
    """Recognize exact no-reader source transforms and logger calls."""
    benign_names = {
        "Path", "PurePath", "str", "bytes", "dict", "list", "tuple", "set",
        "join", "joinpath", "print", "debug", "info", "warning", "error",
        "exception", "critical", "get", "validate_artifact_schema", "load_schema",
        "load_json_schema", "get_schema",
    }
    name = _call_name(call)
    return name in benign_names


def _flow_control_is_relevant(statement: ast.stmt, aliases: set[str]) -> bool:
    """Whether an unmodeled control construct can alter this alias flow."""
    for node in _iter_nodes_without_nested_scopes(statement):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) and node.id in aliases:
            return True
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            targets = (
                node.targets
                if isinstance(node, ast.Assign)
                else (node.target,)
            )
            if any(_assignment_target_names(target) & aliases for target in targets):
                return True
    return False


def _parameter_flow_kind(
    tree: ast.AST,
    function: ast.FunctionDef | ast.AsyncFunctionDef,
    tracked_parameters: set[str],
    *,
    depth: int = 0,
    seen_functions: frozenset[str] = frozenset(),
    module_name: str | None = None,
    sources: Mapping[str, _ParsedProductionSource] | None = None,
    captured_aliases: frozenset[str] = frozenset(),
    scope_index: _LexicalScopeIndex | None = None,
) -> str:
    """Classify one parameter flow with statement-ordered aliases only.

    This intentionally supports a small deterministic subset.  A relevant
    call, assignment, or control path outside that subset is incomplete rather
    than guessed.  Each invocation receives a separate alias set, so a sink
    reached through one callee parameter cannot taint another parameter.
    """
    parameters = set(_function_parameters(function))
    if not tracked_parameters <= parameters:
        return "unresolved"
    if not tracked_parameters and not captured_aliases:
        return "none"
    aliases = set(tracked_parameters) | set(captured_aliases)
    scope_index = _lexical_scope_index(tree) if scope_index is None else scope_index
    current_identity = _flow_callable_identity(module_name, function)

    def call_result(call: ast.Call) -> str:
        raw_relevant = any(
            _expression_uses_names(expression, aliases)
            for expression in _call_raw_argument_expressions(call)
        ) or (
            isinstance(call.func, ast.Attribute)
            and _expression_uses_names(call.func.value, aliases)
        ) or (
            isinstance(call.func, ast.Name) and call.func.id in aliases
        ) or (
            not isinstance(call.func, (ast.Name, ast.Attribute))
            and _expression_uses_names(call.func, aliases)
        )
        local = _lexical_function_resolution(tree, call, scope_index)
        if local.kind == "resolved" and local.function is not None:
            binding = _call_binding(call, local.function)
            nested_parameters = {
                parameter
                for parameter, expression, _ in binding.parameter_arguments
                if _expression_uses_names(expression, aliases)
            }
            captures = _function_captured_aliases(local.function, aliases)
            relevant = raw_relevant or bool(captures)
            if binding.invalid or any(
                _expression_uses_names(expression, aliases)
                for expression in binding.unresolved_argument_expressions
            ):
                return "unresolved" if relevant else "none"
            if not nested_parameters and not captures:
                return "none"
            nested_identity = _flow_callable_identity(module_name, local.function)
            if depth >= 1 or nested_identity in seen_functions | {current_identity}:
                return "unresolved"
            return _parameter_flow_kind(
                tree,
                local.function,
                nested_parameters,
                depth=depth + 1,
                seen_functions=seen_functions | {current_identity},
                module_name=module_name,
                sources=sources,
                captured_aliases=frozenset(captures),
                scope_index=scope_index,
            )
        if local.kind in {"ambiguous", "rebound"}:
            scope = scope_index.enclosing_scope_by_node.get(id(call), tree)
            captures_alias = (
                local.kind == "ambiguous"
                and isinstance(call.func, ast.Name)
                and _ambiguous_callable_captures_alias(scope, call.func.id, aliases)
            )
            return "unresolved" if raw_relevant or captures_alias else "none"
        if not raw_relevant:
            return "none"
        if isinstance(call.func, ast.Attribute) and call.func.attr in {
            "open", "read_text", "read_bytes", "glob", "rglob", "iterdir"
        }:
            return "reader"

        imported = (
            _lexical_repository_import_resolution(
                tree,
                call,
                module_name=module_name,
                sources=sources,
                scope_index=scope_index,
            )
            if module_name is not None and sources is not None
            else _ImportedCallableResolution(False)
        )
        if imported.found:
            if imported.module is None or imported.symbol is None or sources is None:
                return "unresolved"
            imported_source = sources.get(imported.module)
            resolution = (
                _module_function_resolution(imported_source.tree, imported.symbol)
                if imported_source is not None
                else _CallableResolution("not_found")
            )
            if resolution.kind != "resolved" or resolution.function is None:
                return "unresolved"
            binding = _call_binding(call, resolution.function)
            imported_parameters = {
                parameter
                for parameter, expression, _ in binding.parameter_arguments
                if _expression_uses_names(expression, aliases)
            }
            if binding.invalid or any(
                _expression_uses_names(expression, aliases)
                for expression in binding.unresolved_argument_expressions
            ):
                return "unresolved"
            if not imported_parameters:
                return "unresolved"
            imported_identity = _flow_callable_identity(imported.module, resolution.function)
            if depth >= 1 or imported_identity in seen_functions | {current_identity}:
                return "unresolved"
            return _parameter_flow_kind(
                imported_source.tree,
                resolution.function,
                imported_parameters,
                depth=depth + 1,
                seen_functions=seen_functions | {current_identity},
                module_name=imported.module,
                sources=sources,
            )
        if _known_external_loader_call(tree, call):
            return "reader"
        if _call_is_benign_transform(call):
            return "none"
        return "unresolved"

    def expression_result(expression: ast.AST | None) -> str:
        if expression is None:
            return "none"
        if any(
            isinstance(node, ast.IfExp) and _expression_uses_names(node, aliases)
            for node in _iter_nodes_without_nested_scopes(expression)
        ):
            return "unresolved"
        unresolved = False
        for call in _iter_executed_calls(expression):
            result = call_result(call)
            if result == "reader":
                return "reader"
            if result == "unresolved":
                unresolved = True
        return "unresolved" if unresolved else "none"

    def update_assignment(targets: Sequence[ast.AST], value: ast.AST | None) -> str:
        if value is not None and any(
            isinstance(node, ast.IfExp) and _expression_uses_names(node, aliases)
            for node in _iter_nodes_without_nested_scopes(value)
        ):
            return "unresolved"
        value_is_alias = _expression_uses_names(value, aliases)
        for target in targets:
            names = _assignment_target_names(target)
            if not names:
                if value_is_alias:
                    return "unresolved"
                continue
            for name in names:
                if value_is_alias:
                    aliases.add(name)
                else:
                    aliases.discard(name)
        return "none"

    def statements_result(statements: Sequence[ast.stmt]) -> str:
        unresolved = False
        for statement in statements:
            if isinstance(statement, ast.Assign):
                result = expression_result(statement.value)
                if result == "reader":
                    return "reader"
                if result == "unresolved":
                    return "unresolved"
                result = update_assignment(statement.targets, statement.value)
                if result == "unresolved":
                    return result
                continue
            if isinstance(statement, ast.AnnAssign):
                result = expression_result(statement.value)
                if result == "reader":
                    return "reader"
                if result == "unresolved":
                    return result
                result = update_assignment((statement.target,), statement.value)
                if result == "unresolved":
                    return result
                continue
            if isinstance(statement, ast.AugAssign):
                if _assignment_target_names(statement.target) & aliases or _expression_uses_names(statement.value, aliases):
                    return "unresolved"
                continue
            if isinstance(statement, ast.Raise):
                # ``ast.Raise`` has no ``.value``; its evaluated expressions are
                # ``exc`` and ``cause`` (either or both ``None`` for a bare
                # ``raise`` or a cause-less ``raise EXC``), visited in that
                # syntactic/evaluation order.  A ``Raise`` unconditionally
                # terminates this path, exactly like ``Return``.
                for value in (statement.exc, statement.cause):
                    result = expression_result(value)
                    if result == "reader":
                        return "reader"
                    if result == "unresolved":
                        unresolved = True
                return "unresolved" if unresolved else "none"
            if isinstance(statement, (ast.Expr, ast.Return, ast.Assert)):
                values = (
                    (statement.value,)
                    if isinstance(statement, (ast.Expr, ast.Return))
                    else (statement.test, statement.msg)
                )
                for value in values:
                    result = expression_result(value)
                    if result == "reader":
                        return "reader"
                    if result == "unresolved":
                        unresolved = True
                if isinstance(statement, ast.Return):
                    return "unresolved" if unresolved else "none"
                continue
            if isinstance(statement, ast.Delete):
                for target in statement.targets:
                    aliases.difference_update(_assignment_target_names(target))
                continue
            if isinstance(statement, (ast.Global, ast.Nonlocal)):
                if set(statement.names) & aliases:
                    return "unresolved"
                continue
            if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                aliases.discard(statement.name)
                continue
            if isinstance(statement, (ast.Import, ast.ImportFrom)):
                aliases.difference_update(_statement_binding_names(statement))
                continue
            if isinstance(statement, ast.If):
                literal = _literal_bool(statement.test)
                if literal is not None:
                    result = statements_result(statement.body if literal else statement.orelse)
                    if result != "none":
                        return result
                    continue
                if _flow_control_is_relevant(statement, aliases):
                    return "unresolved"
                continue
            if isinstance(statement, (ast.Match, ast.Try, ast.For, ast.AsyncFor, ast.While, ast.With, ast.AsyncWith)):
                if _flow_control_is_relevant(statement, aliases):
                    return "unresolved"
                continue
            if _flow_control_is_relevant(statement, aliases):
                return "unresolved"
        return "unresolved" if unresolved else "none"

    return statements_result(function.body)


def _observer_relevant_name_bindings(
    tree: ast.AST,
    bindings: Mapping[str, str],
) -> set[str]:
    """Resolve bounded local aliases that carry observer-report relevance."""
    relevant: set[str] = set()
    assignments = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
    ]
    for _ in range(8):
        changed = False
        for assignment in assignments:
            if (
                _node_has_observer_relevant_literal(assignment.value, bindings)
                or _expression_uses_names(assignment.value, relevant)
            ) and assignment.targets[0].id not in relevant:
                relevant.add(assignment.targets[0].id)
                changed = True
        if not changed:
            break
    return relevant


def _module_observer_relevant_name_bindings(
    tree: ast.AST,
    bindings: Mapping[str, str],
) -> set[str]:
    """Resolve only module-scope aliases for explicit captured-value tracing.

    The direct inventory pass may trace a no-argument top-level wrapper that
    captures a module value.  Function-local assignments must not be promoted
    into that wrapper's capture set: doing so would give an uninvoked nested
    function body authority outside its lexical scope.
    """
    relevant: set[str] = set()
    assignments = [
        statement
        for statement in _scope_body(tree)
        if isinstance(statement, ast.Assign)
        and len(statement.targets) == 1
        and isinstance(statement.targets[0], ast.Name)
    ]
    for _ in range(8):
        changed = False
        for assignment in assignments:
            if (
                _node_has_observer_relevant_literal(assignment.value, bindings)
                or _expression_uses_names(assignment.value, relevant)
            ) and assignment.targets[0].id not in relevant:
                relevant.add(assignment.targets[0].id)
                changed = True
        if not changed:
            break
    return relevant


def _expression_observer_relevance(
    node: ast.AST | None,
    *,
    bindings: Mapping[str, str],
    relevant_names: set[str],
) -> tuple[bool, bool, bool, str | None]:
    """Return bounded observer/report-policy relevance for one expression."""
    value = _bound_literal_path_expression(node, bindings)
    report = value is not None and _is_report_artifact_path_value(value)
    policy = value is not None and _POLICY_INPUT_RELATIVE_PATH in value
    relevant = (
        report
        or policy
        or _node_has_observer_relevant_literal(node, bindings)
        or _expression_uses_names(node, relevant_names)
    )
    return relevant, report, policy, value


def _imported_wrapper_findings(
    source: _ParsedProductionSource,
    *,
    sources: Mapping[str, _ParsedProductionSource],
    observer_internal_source: bool,
) -> tuple[tuple[str, ...], bool, bool]:
    """Follow a relevant imported callable through at most one source level."""
    if observer_internal_source:
        return (), False, False
    bindings = _literal_bindings(source.tree)
    relevant_names = _observer_relevant_name_bindings(source.tree, bindings)
    scope_index = _lexical_scope_index(source.tree)
    findings: set[str] = set()
    report_reader = False
    policy_reader = False
    for call in _iter_inventory_nodes(source.tree):
        if not isinstance(call, ast.Call):
            continue
        local = _lexical_function_resolution(source.tree, call, scope_index)
        if local.kind in {"resolved", "ambiguous", "rebound"}:
            # The direct/local pass owns lexical definitions and rebindings;
            # this pass follows repository imports only.
            continue
        imported = _lexical_repository_import_resolution(
            source.tree,
            call,
            module_name=source.module_name,
            sources=sources,
            scope_index=scope_index,
        )
        if not imported.found:
            continue
        caller_argument_relevance = tuple(
            _expression_observer_relevance(
                argument,
                bindings=bindings,
                relevant_names=relevant_names,
            )
            for argument in _call_raw_argument_expressions(call)
        )
        caller_relevant = any(item[0] for item in caller_argument_relevance)
        if imported.module is None or imported.symbol is None:
            if caller_relevant:
                findings.add("unresolved_dynamic_path")
            continue
        target = sources.get(imported.module)
        resolution = (
            _module_function_resolution(target.tree, imported.symbol)
            if target is not None
            else _CallableResolution("not_found")
        )
        function = resolution.function if resolution.kind == "resolved" else None
        if function is None:
            if caller_relevant:
                findings.add("unresolved_dynamic_path")
            continue
        binding = _call_binding(call, function)
        target_bindings = _literal_bindings(target.tree)
        target_relevant_names = _observer_relevant_name_bindings(
            target.tree,
            target_bindings,
        )
        parameter_relevance: list[tuple[str, bool, bool, bool, str | None]] = []
        for parameter, expression, defaulted in binding.parameter_arguments:
            relevance = _expression_observer_relevance(
                expression,
                bindings=target_bindings if defaulted else bindings,
                relevant_names=target_relevant_names if defaulted else relevant_names,
            )
            if relevance[0]:
                parameter_relevance.append(
                    (parameter, relevance[1], relevance[2], relevance[0], relevance[3])
                )
        relevant = caller_relevant or bool(parameter_relevance)
        if not relevant:
            continue
        # An expanded argument can shift positional binding or overwrite a
        # keyword binding.  Once this call is observer-relevant, guessing is
        # unsafe even when the expansion itself looks unrelated.
        if binding.invalid or binding.unresolved_argument_expressions:
            findings.add("unresolved_dynamic_path")
            continue
        if not parameter_relevance:
            findings.add("unresolved_dynamic_path")
            continue
        for parameter, report, policy, _, value in parameter_relevance:
            if value is None:
                findings.add("unresolved_dynamic_path")
                continue
            if (report or policy) and (
                value.startswith("/")
                or "\\" in value
                or ".." in PurePosixPath(value).parts
            ):
                findings.add("repository_path_escape")
                continue
            # Each caller argument retains its own relevance category all the
            # way through the callee.  A policy/observer metadata parameter
            # that reaches a reader cannot make a separate report-path
            # parameter a report reader.
            flow = _parameter_flow_kind(
                target.tree,
                function,
                {parameter},
                seen_functions=frozenset({
                    _flow_callable_identity(target.module_name, function)
                }),
                module_name=target.module_name,
                sources=sources,
            )
            if flow == "reader":
                report_reader = report_reader or report
                policy_reader = policy_reader or policy
            elif flow == "unresolved":
                findings.add("unresolved_dynamic_path")
    return tuple(sorted(findings)), report_reader, policy_reader


@dataclass(frozen=True, slots=True)
class _StaticImportOccurrence:
    """One alias-level static import relation and its lexical binding."""

    statement: ast.Import | ast.ImportFrom
    alias: ast.alias
    scope: ast.AST
    target_module: str | None
    binding_name: str | None
    binds_module_object: bool


@dataclass(frozen=True, slots=True)
class _ClassifiedConsumerRelation:
    """One private relation classification; it is never serialized."""

    category: _ConsumerRelationCategory
    importer_relative_path: str
    importer_module: str
    target_module: str | None
    lineno: int
    col_offset: int


def _declared_contract_modules() -> Mapping[str, _DeclaredObserverContractModule]:
    return {
        module.module_name: module
        for suite in _DECLARED_OBSERVER_CONTRACT_SUITES
        for module in suite.modules
    }


def _declared_internal_relations() -> frozenset[tuple[str, str, str]]:
    return frozenset(
        (relation.importer_module, relation.importee_module, relation.edge_kind)
        for suite in _DECLARED_OBSERVER_CONTRACT_SUITES
        for relation in suite.allowed_internal_relations
    )


def _is_observer_relation_target(module_name: str | None) -> bool:
    return module_name is not None and (
        module_name == "investment_orchestrator.observability"
        or module_name.startswith("investment_orchestrator.observability.")
        or module_name in _declared_contract_modules()
    )


def _static_import_occurrences(
    source: _ParsedProductionSource,
    *,
    sources: Mapping[str, _ParsedProductionSource],
) -> tuple[_StaticImportOccurrence, ...]:
    """Enumerate static imports alias-by-alias without importing target code."""
    declared_modules = _declared_contract_modules()
    result: list[_StaticImportOccurrence] = []

    class Visitor(ast.NodeVisitor):
        def __init__(self) -> None:
            self.scope: ast.AST = source.tree

        def _visit_nested_scope(self, node: ast.AST, body: Sequence[ast.stmt]) -> None:
            previous = self.scope
            self.scope = node
            for statement in body:
                self.visit(statement)
            self.scope = previous

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            self._visit_nested_scope(node, node.body)

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
            self._visit_nested_scope(node, node.body)

        def visit_ClassDef(self, node: ast.ClassDef) -> None:
            self._visit_nested_scope(node, node.body)

        def visit_Lambda(self, node: ast.Lambda) -> None:
            return

        def visit_Import(self, node: ast.Import) -> None:
            for alias in node.names:
                binding = alias.asname or alias.name.split(".", 1)[0]
                result.append(
                    _StaticImportOccurrence(
                        statement=node,
                        alias=alias,
                        scope=self.scope,
                        target_module=alias.name,
                        binding_name=binding,
                        binds_module_object=(
                            alias.asname is not None or "." not in alias.name
                        ),
                    )
                )

        def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
            base = _resolve_relative_import(
                source.module_name,
                node.level,
                node.module,
            )
            for alias in node.names:
                if base is None:
                    target = None
                    binds_module = False
                elif alias.name == "*":
                    target = base
                    binds_module = False
                else:
                    child = f"{base}.{alias.name}"
                    binds_module = child in sources or child in declared_modules
                    target = child if binds_module else base
                result.append(
                    _StaticImportOccurrence(
                        statement=node,
                        alias=alias,
                        scope=self.scope,
                        target_module=target,
                        binding_name=(
                            None if alias.name == "*" else alias.asname or alias.name
                        ),
                        binds_module_object=binds_module,
                    )
                )

    Visitor().visit(source.tree)
    return tuple(result)


def _ast_parent_index(tree: ast.AST) -> Mapping[int, ast.AST]:
    return {
        id(child): parent
        for parent in ast.walk(tree)
        for child in ast.iter_child_nodes(parent)
    }


def _is_descendant_of(
    node: ast.AST,
    ancestor: ast.AST,
    parents: Mapping[int, ast.AST],
) -> bool:
    current: ast.AST | None = node
    while current is not None:
        if current is ancestor:
            return True
        current = parents.get(id(current))
    return False


def _nearest_binding_scope(
    node: ast.AST,
    *,
    tree: ast.AST,
    parents: Mapping[int, ast.AST],
) -> ast.AST:
    current = parents.get(id(node))
    while current is not None:
        if isinstance(
            current,
            (
                ast.Module,
                ast.FunctionDef,
                ast.AsyncFunctionDef,
                ast.Lambda,
                ast.ClassDef,
                ast.ListComp,
                ast.SetComp,
                ast.DictComp,
                ast.GeneratorExp,
            ),
        ):
            return current
        current = parents.get(id(current))
    return tree


def _scope_parent(
    scope: ast.AST,
    *,
    tree: ast.AST,
    parents: Mapping[int, ast.AST],
) -> ast.AST | None:
    current = parents.get(id(scope))
    while current is not None:
        if isinstance(
            current,
            (
                ast.Module,
                ast.FunctionDef,
                ast.AsyncFunctionDef,
                ast.Lambda,
                ast.ClassDef,
                ast.ListComp,
                ast.SetComp,
                ast.DictComp,
                ast.GeneratorExp,
            ),
        ):
            return current
        current = parents.get(id(current))
    return tree if scope is not tree else None


def _function_parameter_names(scope: ast.AST) -> set[str]:
    if not isinstance(scope, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
        return set()
    arguments = scope.args
    return {
        argument.arg
        for argument in (
            *arguments.posonlyargs,
            *arguments.args,
            *arguments.kwonlyargs,
            *((arguments.vararg,) if arguments.vararg is not None else ()),
            *((arguments.kwarg,) if arguments.kwarg is not None else ()),
        )
    }


def _comprehension_namedexpr_targets(
    node: ast.ListComp | ast.SetComp | ast.DictComp | ast.GeneratorExp,
) -> tuple[ast.Name, ...]:
    """Return walrus targets whose writes belong to the enclosing scope.

    Iteration targets are deliberately excluded: Python gives those names an
    implicit comprehension-local scope.  Assignment expressions are the
    opposite special case and bind in the enclosing scope (except invalid
    class-scope forms, which are syntactically rejected by Python).  The
    inventory is conservative for nested expressions and never executes one.
    """
    result: list[ast.Name] = []

    class Visitor(ast.NodeVisitor):
        def visit_NamedExpr(self, named_expression: ast.NamedExpr) -> None:
            if isinstance(named_expression.target, ast.Name):
                result.append(named_expression.target)
            self.visit(named_expression.value)

        def visit_Lambda(self, lambda_node: ast.Lambda) -> None:
            return

        def visit_FunctionDef(self, function: ast.FunctionDef) -> None:
            return

        def visit_AsyncFunctionDef(self, function: ast.AsyncFunctionDef) -> None:
            return

        def visit_ClassDef(self, class_node: ast.ClassDef) -> None:
            return

    visitor = Visitor()
    for generator in node.generators:
        visitor.visit(generator.iter)
        for condition in generator.ifs:
            visitor.visit(condition)
    if isinstance(node, ast.DictComp):
        visitor.visit(node.key)
        visitor.visit(node.value)
    else:
        visitor.visit(node.elt)
    return tuple(result)


def _scope_name_declarations(scope: ast.AST) -> tuple[set[str], set[str], set[str]]:
    """Return local, global, and nonlocal names for one lexical scope."""
    local = _function_parameter_names(scope)
    global_names: set[str] = set()
    nonlocal_names: set[str] = set()

    class Visitor(ast.NodeVisitor):
        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            local.add(node.name)

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
            local.add(node.name)

        def visit_ClassDef(self, node: ast.ClassDef) -> None:
            local.add(node.name)

        def visit_Lambda(self, node: ast.Lambda) -> None:
            return

        def visit_ListComp(self, node: ast.ListComp) -> None:
            self._visit_comprehension_namedexpr_targets(node)

        def visit_SetComp(self, node: ast.SetComp) -> None:
            self._visit_comprehension_namedexpr_targets(node)

        def visit_DictComp(self, node: ast.DictComp) -> None:
            self._visit_comprehension_namedexpr_targets(node)

        def visit_GeneratorExp(self, node: ast.GeneratorExp) -> None:
            self._visit_comprehension_namedexpr_targets(node)

        def _visit_comprehension_namedexpr_targets(
            self,
            node: ast.ListComp | ast.SetComp | ast.DictComp | ast.GeneratorExp,
        ) -> None:
            for target in _comprehension_namedexpr_targets(node):
                local.add(target.id)

        def visit_Global(self, node: ast.Global) -> None:
            global_names.update(node.names)

        def visit_Nonlocal(self, node: ast.Nonlocal) -> None:
            nonlocal_names.update(node.names)

        def visit_Import(self, node: ast.Import) -> None:
            local.update(alias.asname or alias.name.split(".", 1)[0] for alias in node.names)

        def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
            local.update(
                alias.asname or alias.name for alias in node.names if alias.name != "*"
            )

        def visit_Name(self, node: ast.Name) -> None:
            if isinstance(node.ctx, (ast.Store, ast.Del)):
                local.add(node.id)

        def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
            if node.name is not None:
                local.add(node.name)
            for statement in node.body:
                self.visit(statement)

        def visit_Match(self, node: ast.Match) -> None:
            self.visit(node.subject)
            for case in node.cases:
                local.update(name for name, _bound_node in _pattern_bound_names(case.pattern))
                if case.guard is not None:
                    self.visit(case.guard)
                for statement in case.body:
                    self.visit(statement)

    for statement in _scope_body(scope):
        Visitor().visit(statement)
    local.difference_update(global_names | nonlocal_names)
    return local, global_names, nonlocal_names


class _Reachability(str, Enum):
    """Bounded statement reachability used only for lexical proof."""

    NEVER = "never"
    MAYBE = "maybe"
    DEFINITE = "definite"


@dataclass(frozen=True, slots=True)
class _ControlFlow:
    """Whether a statement can and must fall through when it is reached."""

    can_fall_through: bool
    must_fall_through: bool


@dataclass(frozen=True, slots=True)
class _ScopeReachability:
    """Private reachability states for AST nodes in one lexical scope."""

    by_node_id: Mapping[int, _Reachability]

    def state(self, node: ast.AST) -> _Reachability:
        # Unknown shapes must never establish a unique proof.
        return self.by_node_id.get(id(node), _Reachability.MAYBE)


def _branch_reachability(state: _Reachability) -> _Reachability:
    if state is _Reachability.NEVER:
        return state
    return _Reachability.MAYBE


def _statement_control_flow(statement: ast.stmt) -> _ControlFlow:
    """Return a deliberately small, conservative fall-through model."""
    if isinstance(statement, (ast.Return, ast.Raise)):
        return _ControlFlow(False, False)
    if isinstance(statement, ast.If):
        literal = _literal_bool(statement.test)
        if literal is not None:
            return _block_control_flow(statement.body if literal else statement.orelse)
        body = _block_control_flow(statement.body)
        otherwise = _block_control_flow(statement.orelse)
        return _ControlFlow(
            body.can_fall_through or otherwise.can_fall_through,
            body.must_fall_through and otherwise.must_fall_through,
        )
    if isinstance(statement, ast.While):
        if _literal_bool(statement.test) is False:
            return _block_control_flow(statement.orelse)
        # Non-literal loops can execute zero, one, or many times; do not make
        # a termination claim from their syntax.
        return _ControlFlow(True, False)
    if isinstance(
        statement,
        (
            ast.For,
            ast.AsyncFor,
            ast.Try,
            ast.With,
            ast.AsyncWith,
        ),
    ):
        return _ControlFlow(True, False)
    if isinstance(statement, ast.Match):
        # A match executes as one sequential statement.  Its captures remain
        # conditional events, but a wildcard-only match cannot by itself
        # shadow a later module binding.
        return _ControlFlow(True, True)
    return _ControlFlow(True, True)


def _block_control_flow(statements: Sequence[ast.stmt]) -> _ControlFlow:
    """Combine a block without attempting a general control-flow graph."""
    if not statements:
        return _ControlFlow(True, True)
    first = _statement_control_flow(statements[0])
    if not first.can_fall_through:
        return first
    rest = _block_control_flow(statements[1:])
    return _ControlFlow(
        rest.can_fall_through,
        first.must_fall_through and rest.must_fall_through,
    )


def _scope_reachability(scope: ast.AST) -> _ScopeReachability:
    """Mark only bounded, syntactically certain statement reachability.

    This intentionally is not a CFG.  It recognizes sequential return/raise,
    literal booleans in ``if`` and ``while``, and otherwise retains feasible
    alternatives as ``MAYBE``.  ``MAYBE`` nodes cannot prove an internal edge.
    """
    states: dict[int, _Reachability] = {}

    def mark(node: ast.AST, state: _Reachability) -> None:
        states[id(node)] = state

    def mark_expression(node: ast.AST | None, state: _Reachability) -> None:
        if node is None:
            return
        mark(node, state)
        for child in ast.iter_child_nodes(node):
            mark_expression(child, state)

    def mark_function_header(
        node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef,
        state: _Reachability,
    ) -> None:
        mark(node, state)
        for decorator in node.decorator_list:
            mark_expression(decorator, state)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for default in (*node.args.defaults, *node.args.kw_defaults):
                mark_expression(default, state)
            mark_expression(node.returns, state)
        else:
            for base in node.bases:
                mark_expression(base, state)
            for keyword in node.keywords:
                mark_expression(keyword.value, state)

    def mark_block(statements: Sequence[ast.stmt], state: _Reachability) -> None:
        next_state = state
        for statement in statements:
            mark_statement(statement, next_state)
            flow = _statement_control_flow(statement)
            if next_state is _Reachability.NEVER or not flow.can_fall_through:
                next_state = _Reachability.NEVER
            elif next_state is _Reachability.DEFINITE and flow.must_fall_through:
                next_state = _Reachability.DEFINITE
            else:
                next_state = _Reachability.MAYBE

    def mark_statement(statement: ast.stmt, state: _Reachability) -> None:
        mark(statement, state)
        if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            mark_function_header(statement, state)
            return
        if isinstance(statement, ast.If):
            mark_expression(statement.test, state)
            literal = _literal_bool(statement.test)
            if literal is True:
                mark_block(statement.body, state)
                mark_block(statement.orelse, _Reachability.NEVER)
            elif literal is False:
                mark_block(statement.body, _Reachability.NEVER)
                mark_block(statement.orelse, state)
            else:
                branch_state = _branch_reachability(state)
                mark_block(statement.body, branch_state)
                mark_block(statement.orelse, branch_state)
            return
        if isinstance(statement, ast.While):
            mark_expression(statement.test, state)
            if _literal_bool(statement.test) is False:
                mark_block(statement.body, _Reachability.NEVER)
                mark_block(statement.orelse, state)
            else:
                branch_state = _branch_reachability(state)
                mark_block(statement.body, branch_state)
                mark_block(statement.orelse, branch_state)
            return
        if isinstance(statement, (ast.For, ast.AsyncFor)):
            mark_expression(statement.iter, state)
            branch_state = _branch_reachability(state)
            mark_expression(statement.target, branch_state)
            mark_block(statement.body, branch_state)
            mark_block(statement.orelse, branch_state)
            return
        if isinstance(statement, (ast.With, ast.AsyncWith)):
            branch_state = _branch_reachability(state)
            for item in statement.items:
                mark_expression(item.context_expr, state)
                mark_expression(item.optional_vars, branch_state)
            mark_block(statement.body, branch_state)
            return
        if isinstance(statement, ast.Try):
            branch_state = _branch_reachability(state)
            mark_block(statement.body, branch_state)
            for handler in statement.handlers:
                mark(handler, branch_state)
                mark_expression(handler.type, branch_state)
                mark_block(handler.body, branch_state)
            mark_block(statement.orelse, branch_state)
            mark_block(statement.finalbody, branch_state)
            return
        if isinstance(statement, ast.Match):
            mark_expression(statement.subject, state)
            branch_state = _branch_reachability(state)
            for case in statement.cases:
                mark(case, branch_state)
                mark_expression(case.pattern, branch_state)
                mark_expression(case.guard, branch_state)
                mark_block(case.body, branch_state)
            return
        for child in ast.iter_child_nodes(statement):
            mark_expression(child, state)

    if isinstance(scope, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
        mark(scope, _Reachability.DEFINITE)
        if scope.generators:
            mark_expression(scope.generators[0].iter, _Reachability.DEFINITE)
        for generator in scope.generators:
            mark_expression(generator.target, _Reachability.MAYBE)
            for condition in generator.ifs:
                mark_expression(condition, _Reachability.MAYBE)
            if generator is not scope.generators[0]:
                mark_expression(generator.iter, _Reachability.MAYBE)
        if isinstance(scope, ast.DictComp):
            mark_expression(scope.key, _Reachability.MAYBE)
            mark_expression(scope.value, _Reachability.MAYBE)
        else:
            mark_expression(scope.elt, _Reachability.MAYBE)
    elif isinstance(scope, ast.Lambda):
        mark(scope, _Reachability.DEFINITE)
        mark_expression(scope.body, _Reachability.DEFINITE)
    else:
        mark_block(_scope_body(scope), _Reachability.DEFINITE)
    return _ScopeReachability(states)


@dataclass(frozen=True, slots=True)
class _BindingEvent:
    node: ast.AST
    alias: ast.alias | None
    conditional: bool
    reachability: _Reachability


def _scope_binding_events(
    scope: ast.AST,
    name: str,
    *,
    reachability: _ScopeReachability | None = None,
) -> tuple[_BindingEvent, ...]:
    """Collect same-scope writes while excluding nested lexical scopes."""
    reachability = reachability or _scope_reachability(scope)
    events: list[_BindingEvent] = []

    def add(
        node: ast.AST,
        alias: ast.alias | None = None,
        *,
        conditional: bool = False,
    ) -> None:
        state = reachability.state(node)
        if state is _Reachability.NEVER:
            return
        events.append(
            _BindingEvent(
                node,
                alias,
                conditional or state is not _Reachability.DEFINITE,
                state,
            )
        )

    class Visitor(ast.NodeVisitor):
        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            if node.name == name:
                add(node)

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
            if node.name == name:
                add(node)

        def visit_ClassDef(self, node: ast.ClassDef) -> None:
            if node.name == name:
                add(node)

        def visit_Lambda(self, node: ast.Lambda) -> None:
            return

        def _visit_comprehension_namedexpr_targets(
            self,
            node: ast.ListComp | ast.SetComp | ast.DictComp | ast.GeneratorExp,
        ) -> None:
            for target in _comprehension_namedexpr_targets(node):
                if target.id == name:
                    # Any comprehension can iterate zero times.  Its walrus
                    # still belongs to this scope, but cannot be a definite
                    # binding after the expression without more analysis.
                    add(target, conditional=True)

        def visit_ListComp(self, node: ast.ListComp) -> None:
            self._visit_comprehension_namedexpr_targets(node)

        def visit_SetComp(self, node: ast.SetComp) -> None:
            self._visit_comprehension_namedexpr_targets(node)

        def visit_DictComp(self, node: ast.DictComp) -> None:
            self._visit_comprehension_namedexpr_targets(node)

        def visit_GeneratorExp(self, node: ast.GeneratorExp) -> None:
            self._visit_comprehension_namedexpr_targets(node)

        def visit_Import(self, node: ast.Import) -> None:
            for alias in node.names:
                if (alias.asname or alias.name.split(".", 1)[0]) == name:
                    add(node, alias)

        def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
            for alias in node.names:
                if alias.name != "*" and (alias.asname or alias.name) == name:
                    add(node, alias)

        def visit_Name(self, node: ast.Name) -> None:
            if node.id == name and isinstance(node.ctx, (ast.Store, ast.Del)):
                add(node)

        def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
            if node.type is not None:
                self.visit(node.type)
            if node.name == name:
                add(node)
            for statement in node.body:
                self.visit(statement)

        def visit_Match(self, node: ast.Match) -> None:
            self.visit(node.subject)
            for case in node.cases:
                for bound_name, bound_node in _pattern_bound_names(case.pattern):
                    if bound_name == name:
                        add(bound_node)
                if case.guard is not None:
                    self.visit(case.guard)
                for statement in case.body:
                    self.visit(statement)

    visitor = Visitor()
    for statement in _scope_body(scope):
        visitor.visit(statement)
    return tuple(events)


def _node_position(node: ast.AST) -> tuple[int, int]:
    return (getattr(node, "lineno", -1), getattr(node, "col_offset", -1))


def _load_is_exception_shadowed(
    load: ast.Name,
    name: str,
    parents: Mapping[int, ast.AST],
) -> bool:
    current = parents.get(id(load))
    while current is not None:
        if isinstance(current, ast.ExceptHandler) and current.name == name:
            return True
        if isinstance(
            current,
            (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef),
        ):
            return False
        current = parents.get(id(current))
    return False


def _comprehension_shadows_load(
    load: ast.Name,
    name: str,
    scope: ast.AST,
    parents: Mapping[int, ast.AST],
) -> bool:
    if not isinstance(
        scope,
        (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp),
    ):
        return False
    generators = scope.generators
    if generators and _is_descendant_of(load, generators[0].iter, parents):
        return False
    return any(name in _assignment_target_names(generator.target) for generator in generators)


def _candidate_uniquely_reaches_same_scope_load(
    occurrence: _StaticImportOccurrence,
    load: ast.Name,
    *,
    reachability: _ScopeReachability,
) -> bool:
    if reachability.state(load) is not _Reachability.DEFINITE:
        return False
    if _node_position(occurrence.statement) >= _node_position(load):
        return False
    events = tuple(
        event
        for event in _scope_binding_events(
            occurrence.scope,
            occurrence.binding_name or "",
            reachability=reachability,
        )
        if _node_position(event.node) < _node_position(load)
    )
    candidate_indexes = tuple(
        index for index, event in enumerate(events) if event.alias is occurrence.alias
    )
    if not candidate_indexes:
        return False
    candidate_index = candidate_indexes[-1]
    if events[candidate_index].conditional:
        return False
    for event in events[candidate_index + 1 :]:
        if event.conditional or event.alias is not occurrence.alias:
            return False
    return True


def _candidate_is_stable_for_nested_load(
    occurrence: _StaticImportOccurrence,
    nested_scope: ast.AST,
    *,
    occurrence_reachability: _ScopeReachability,
) -> bool:
    events = _scope_binding_events(
        occurrence.scope,
        occurrence.binding_name or "",
        reachability=occurrence_reachability,
    )
    matching = tuple(event for event in events if event.alias is occurrence.alias)
    if len(events) != 1 or len(matching) != 1 or matching[0].conditional:
        return False
    name = occurrence.binding_name or ""
    for nested in ast.walk(occurrence.scope):
        if not isinstance(nested, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        changes_outer = any(
            (
                isinstance(declaration, ast.Global)
                or isinstance(declaration, ast.Nonlocal)
            )
            and name in declaration.names
            for declaration in ast.walk(nested)
        ) and any(
            isinstance(candidate, ast.Name)
            and candidate.id == name
            and isinstance(candidate.ctx, (ast.Store, ast.Del))
            for candidate in ast.walk(nested)
        )
        if changes_outer:
            return False
    return _node_position(occurrence.statement) < _node_position(nested_scope)


def _module_binding_occurrence_has_proven_load(
    source: _ParsedProductionSource,
    occurrence: _StaticImportOccurrence,
) -> bool:
    """Prove at least one lexical load of this exact static module binding."""
    name = occurrence.binding_name
    if name is None or not occurrence.binds_module_object:
        return False
    parents = _ast_parent_index(source.tree)
    declarations: dict[int, tuple[set[str], set[str], set[str]]] = {}
    reachabilities: dict[int, _ScopeReachability] = {}

    def declaration(scope: ast.AST) -> tuple[set[str], set[str], set[str]]:
        value = declarations.get(id(scope))
        if value is None:
            value = _scope_name_declarations(scope)
            declarations[id(scope)] = value
        return value

    def reachability(scope: ast.AST) -> _ScopeReachability:
        value = reachabilities.get(id(scope))
        if value is None:
            value = _scope_reachability(scope)
            reachabilities[id(scope)] = value
        return value

    for load in (
        node
        for node in ast.walk(source.tree)
        if isinstance(node, ast.Name)
        and node.id == name
        and isinstance(node.ctx, ast.Load)
    ):
        if _load_is_exception_shadowed(load, name, parents):
            continue
        scope = _nearest_binding_scope(load, tree=source.tree, parents=parents)
        if _comprehension_shadows_load(load, name, scope, parents):
            continue
        load_reachability = reachability(scope)
        load_state = load_reachability.state(load)
        if isinstance(
            scope,
            (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp),
        ):
            if scope.generators and _is_descendant_of(
                load,
                scope.generators[0].iter,
                parents,
            ):
                load_state = reachability(
                    _scope_parent(scope, tree=source.tree, parents=parents) or source.tree
                ).state(scope)
            else:
                parent_scope = _scope_parent(scope, tree=source.tree, parents=parents)
                parent_state = reachability(parent_scope or source.tree).state(scope)
                if (
                    parent_state is _Reachability.NEVER
                    or load_state is _Reachability.NEVER
                ):
                    load_state = _Reachability.NEVER
                else:
                    load_state = _Reachability.MAYBE
            scope = _scope_parent(scope, tree=source.tree, parents=parents) or source.tree
        if load_state is not _Reachability.DEFINITE:
            continue

        resolved_scope: ast.AST | None = scope
        while resolved_scope is not None and resolved_scope is not occurrence.scope:
            if isinstance(resolved_scope, ast.ClassDef):
                prior_class_bindings = tuple(
                    event
                    for event in _scope_binding_events(
                        resolved_scope,
                        name,
                        reachability=reachability(resolved_scope),
                    )
                    if _node_position(event.node) < _node_position(load)
                )
                if prior_class_bindings:
                    resolved_scope = None
                    break
                resolved_scope = _scope_parent(
                    resolved_scope,
                    tree=source.tree,
                    parents=parents,
                )
                continue
            local, global_names, nonlocal_names = declaration(resolved_scope)
            if name in global_names:
                if any(
                    _node_position(event.node) < _node_position(load)
                    for event in _scope_binding_events(
                        resolved_scope,
                        name,
                        reachability=reachability(resolved_scope),
                    )
                ):
                    resolved_scope = None
                    break
                resolved_scope = source.tree
                break
            if name in local and name not in nonlocal_names:
                resolved_scope = None
                break
            parent_scope = _scope_parent(
                resolved_scope,
                tree=source.tree,
                parents=parents,
            )
            if isinstance(resolved_scope, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                while isinstance(parent_scope, ast.ClassDef):
                    parent_scope = _scope_parent(
                        parent_scope,
                        tree=source.tree,
                        parents=parents,
                    )
            resolved_scope = parent_scope
        if resolved_scope is not occurrence.scope:
            continue
        if scope is occurrence.scope:
            if _candidate_uniquely_reaches_same_scope_load(
                occurrence,
                load,
                reachability=reachability(occurrence.scope),
            ):
                return True
        elif _candidate_is_stable_for_nested_load(
            occurrence,
            scope,
            occurrence_reachability=reachability(occurrence.scope),
        ):
            return True
    return False


def _classify_static_import_relations(
    source: _ParsedProductionSource,
    *,
    sources: Mapping[str, _ParsedProductionSource],
) -> tuple[_ClassifiedConsumerRelation, ...]:
    """Classify every static import alias independently."""
    declared_modules = _declared_contract_modules()
    allowed = _declared_internal_relations()
    result: list[_ClassifiedConsumerRelation] = []
    for occurrence in _static_import_occurrences(source, sources=sources):
        target = occurrence.target_module
        category = _ConsumerRelationCategory.NOT_RELEVANT_TO_LTETF01
        if _is_observer_relation_target(target):
            internal = False
            declared_importee = declared_modules.get(target or "")
            actual_importee = sources.get(target or "")
            if (
                declared_importee is not None
                and actual_importee is not None
                and actual_importee.relative_path == declared_importee.relative_path
                and (
                    source.module_name,
                    target,
                    "static_module_binding",
                )
                in allowed
                and source.relative_path
                == declared_modules[source.module_name].relative_path
                and _module_binding_occurrence_has_proven_load(source, occurrence)
            ):
                internal = True
            category = (
                _ConsumerRelationCategory.INTERNAL_IMPLEMENTATION_EDGE
                if internal
                else _ConsumerRelationCategory.EXTERNAL_OBSERVER_CONSUMER
            )
        result.append(
            _ClassifiedConsumerRelation(
                category=category,
                importer_relative_path=source.relative_path,
                importer_module=source.module_name,
                target_module=target,
                lineno=getattr(occurrence.statement, "lineno", -1),
                col_offset=getattr(occurrence.statement, "col_offset", -1),
            )
        )
    return tuple(result)


def _classify_consumer_relations(
    source: _ParsedProductionSource,
    *,
    sources: Mapping[str, _ParsedProductionSource],
) -> tuple[_ClassifiedConsumerRelation, ...]:
    """Combine independent static, dynamic, reader, and unresolved relations."""
    relations = list(_classify_static_import_relations(source, sources=sources))
    for target in source.dynamic_imports:
        relations.append(
            _ClassifiedConsumerRelation(
                category=(
                    _ConsumerRelationCategory.EXTERNAL_OBSERVER_CONSUMER
                    if _is_observer_relation_target(target)
                    else _ConsumerRelationCategory.NOT_RELEVANT_TO_LTETF01
                ),
                importer_relative_path=source.relative_path,
                importer_module=source.module_name,
                target_module=target,
                lineno=-1,
                col_offset=-1,
            )
        )
    if source.report_reader:
        relations.append(
            _ClassifiedConsumerRelation(
                category=_ConsumerRelationCategory.REPORT_ARTIFACT_READER,
                importer_relative_path=source.relative_path,
                importer_module=source.module_name,
                target_module=None,
                lineno=-1,
                col_offset=-1,
            )
        )
    if any(
        finding
        in {
            "dynamic_execution",
            "unresolved_dynamic_import",
            "unresolved_dynamic_path",
        }
        for finding in source.findings
    ):
        relations.append(
            _ClassifiedConsumerRelation(
                category=_ConsumerRelationCategory.UNRESOLVED_RELEVANT_CONSUMER,
                importer_relative_path=source.relative_path,
                importer_module=source.module_name,
                target_module=None,
                lineno=-1,
                col_offset=-1,
            )
        )
    precedence = {
        _ConsumerRelationCategory.UNRESOLVED_RELEVANT_CONSUMER: 0,
        _ConsumerRelationCategory.REPORT_ARTIFACT_READER: 1,
        _ConsumerRelationCategory.EXTERNAL_OBSERVER_CONSUMER: 2,
        _ConsumerRelationCategory.INTERNAL_IMPLEMENTATION_EDGE: 3,
        _ConsumerRelationCategory.NOT_RELEVANT_TO_LTETF01: 4,
    }
    return tuple(
        sorted(
            relations,
            key=lambda relation: (
                precedence[relation.category],
                relation.target_module or "",
                relation.lineno,
                relation.col_offset,
            ),
        )
    )


def _scan_production_inventory(root: Path) -> ProductionInventory:
    source_root = root / "src" / "investment_orchestrator"
    try:
        every_path = tuple(source_root.rglob("*"))
    except OSError:
        raise ObserverIntegrityError("CONSUMER_INVENTORY_INCOMPLETE") from None
    for path in every_path:
        if path.is_symlink():
            raise ObserverIntegrityError("CONSUMER_INVENTORY_INCOMPLETE")
    paths = tuple(
        sorted(
            path
            for path in every_path
            if path.suffix == ".py"
            and path.is_file()
            and not any(part in _INVENTORY_EXCLUDED_PATH_PARTS for part in path.parts)
        )
    )
    analyses: list[tuple[str, str, tuple[str, ...], tuple[str, ...], bool, bool, tuple[str, ...]]] = []
    parsed_sources: dict[str, _ParsedProductionSource] = {}
    source_order: list[str] = []
    for path in paths:
        relative_path = _repository_relative_path(root, path)
        module_name = _module_name_for_path(relative_path)
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=relative_path)
        except (OSError, UnicodeDecodeError, SyntaxError):
            raise ObserverIntegrityError("CONSUMER_INVENTORY_INCOMPLETE") from None
        imports, import_findings, dynamic_imports = _imports_in_tree(tree, module_name)
        parsed = _ParsedProductionSource(
            relative_path=relative_path,
            module_name=module_name,
            tree=tree,
            imports=imports,
            dynamic_imports=dynamic_imports,
            findings=import_findings,
            report_reader=False,
            policy_reader=False,
            broker_capabilities=_broker_capability_findings(tree),
        )
        parsed_sources[module_name] = parsed
        source_order.append(module_name)

    # Import resolution is source-only but requires the complete local module
    # index.  Parse every production file first, then trace direct/local
    # reader flow with repository-local imported callables available.
    for module_name in source_order:
        parsed = parsed_sources[module_name]
        file_findings, report_reader, policy_reader, _ = _file_access_findings(
            parsed.tree,
            relative_path=parsed.relative_path,
            observer_internal_source=(
                parsed.relative_path
                in _OBSERVER_INTERNAL_RELATIVE_PATHS | {_OBSERVER_CLI_RELATIVE_PATH}
            ),
            module_name=parsed.module_name,
            sources=parsed_sources,
        )
        findings = tuple(sorted(set(parsed.findings) | set(file_findings)))
        parsed = _ParsedProductionSource(
            relative_path=parsed.relative_path,
            module_name=parsed.module_name,
            tree=parsed.tree,
            imports=parsed.imports,
            dynamic_imports=parsed.dynamic_imports,
            findings=findings,
            report_reader=report_reader,
            policy_reader=policy_reader,
            broker_capabilities=parsed.broker_capabilities,
        )
        parsed_sources[module_name] = parsed
        analyses.append(
            (
                parsed.relative_path,
                parsed.module_name,
                parsed.imports,
                parsed.findings,
                parsed.report_reader,
                parsed.policy_reader,
                parsed.broker_capabilities,
            )
        )
    analyses = [
        (
            relative,
            module,
            imports,
            tuple(sorted(set(findings) | set(wrapper_findings))),
            report_reader or wrapper_report_reader,
            policy_reader or wrapper_policy_reader,
            broker_capabilities,
        )
        for relative, module, imports, findings, report_reader, policy_reader, broker_capabilities in analyses
        for wrapper_findings, wrapper_report_reader, wrapper_policy_reader in (
            _imported_wrapper_findings(
                parsed_sources[module],
                sources=parsed_sources,
                observer_internal_source=relative in _OBSERVER_INTERNAL_RELATIVE_PATHS | {_OBSERVER_CLI_RELATIVE_PATH},
            ),
        )
    ]
    dynamic_findings = tuple(
        sorted(
            f"{relative}:{finding}"
            for relative, _, _, findings, _, _, _ in analyses
            for finding in findings
        )
    )
    if dynamic_findings:
        raise ObserverIntegrityError("CONSUMER_INVENTORY_INCOMPLETE")
    entry_points = _scan_pyproject_entry_points(root)
    observer_consumers: set[str] = set()
    report_readers: set[str] = set()
    policy_consumers: set[str] = set()
    p4a_consumers: set[str] = set()
    broker_imports: set[str] = set()
    prohibited_observer_imports: set[str] = set()
    imports_by_path: list[tuple[str, tuple[str, ...]]] = []
    broker_prefixes = (
        "alpaca", "ib_insync", "ccxt", "robin_stocks", "interactive_brokers", "investment_orchestrator.broker"
    )
    prohibited_observer_prefixes = (
        "anthropic", "langchain", "openai", "investment_orchestrator.llm",
        "investment_orchestrator.workflow", "investment_orchestrator.state",
        "investment_orchestrator.permissions", "investment_orchestrator.orders",
        "investment_orchestrator.broker",
        "investment_orchestrator.parsers.parse_step2_market_source_policy_operator_approval_intent_statement",
        "investment_orchestrator.validators.validate_step2_market_source_policy_operator_approval_intent_statement",
        "investment_orchestrator.validators.validate_step2_market_source_policy_operator_approval_intent_statement_artifact",
    )
    p4a_modules = (
        "investment_orchestrator.parsers.parse_step2_market_source_policy_operator_approval_intent_statement",
        "investment_orchestrator.validators.validate_step2_market_source_policy_operator_approval_intent_statement",
        "investment_orchestrator.validators.validate_step2_market_source_policy_operator_approval_intent_statement_artifact",
    )
    allowed_p4a_definition = "src/investment_orchestrator/validators/validate_step2_market_source_policy_operator_approval_intent_statement_artifact.py"
    relations_by_module = {
        module_name: _classify_consumer_relations(
            source,
            sources=parsed_sources,
        )
        for module_name, source in parsed_sources.items()
    }
    for relative, module, imports, findings, report_reader, policy_reader, broker_capabilities in analyses:
        imports_by_path.append((relative, imports))
        is_internal = relative in _OBSERVER_INTERNAL_RELATIVE_PATHS
        is_cli = relative == _OBSERVER_CLI_RELATIVE_PATH
        relations = relations_by_module[module]
        has_external_relation = any(
            relation.category
            is _ConsumerRelationCategory.EXTERNAL_OBSERVER_CONSUMER
            for relation in relations
        )
        if has_external_relation and not is_internal:
            observer_consumers.add(relative)
        if report_reader and not is_internal and not is_cli:
            report_readers.add(relative)
        if policy_reader and not is_internal:
            policy_consumers.add(relative)
        if relative != allowed_p4a_definition and any(item == module or item.startswith(f"{module}.") for item in imports for module in p4a_modules):
            p4a_consumers.add(relative)
        for imported in imports:
            if imported.startswith(broker_prefixes):
                broker_imports.add(f"{relative}:{imported}")
            if (is_internal or is_cli) and imported.startswith(prohibited_observer_prefixes):
                prohibited_observer_imports.add(f"{relative}:{imported}")
        broker_imports.update(f"{relative}:{capability}" for capability in broker_capabilities)

    for locator, target in entry_points:
        module_target = target.split(":", 1)[0]
        if module_target == "investment_orchestrator.observability" or module_target.startswith("investment_orchestrator.observability."):
            observer_consumers.add(locator)
        if "ltetf_target_architecture_gap_report" in target or "observe_ltetf_target_architecture_gaps" in target:
            report_readers.add(locator)
        if "ltetf_operator_mandate" in target:
            policy_consumers.add(locator)

    # A direct AST capability scan avoids treating prose/comments as a broker or LLM fact.
    analyses_by_module = {
        module: (relative, imports)
        for relative, module, imports, _, _, _, _ in analyses
    }
    weekly_markers: set[str] = set()
    queue = ["investment_orchestrator.workflow.weekly_orchestrator"]
    seen: set[str] = set()
    llm_prefixes = ("openai", "anthropic", "langchain", "google.generativeai", "cohere")
    while queue:
        module = queue.pop()
        if module in seen:
            continue
        seen.add(module)
        analysis = analyses_by_module.get(module)
        if analysis is None:
            continue
        relative, imports = analysis
        for imported in imports:
            if imported.startswith(llm_prefixes):
                weekly_markers.add(f"{relative}:{imported}")
            if imported in analyses_by_module:
                queue.append(imported)
            else:
                parent = imported.rsplit(".", 1)[0]
                if parent in analyses_by_module:
                    queue.append(parent)

    return ProductionInventory(
        production_paths=tuple(relative for relative, _, _, _, _, _, _ in analyses),
        entry_points=entry_points,
        imports_by_path=tuple(imports_by_path),
        dynamic_findings=dynamic_findings,
        observer_external_consumers=tuple(sorted(observer_consumers)),
        report_artifact_readers=tuple(sorted(report_readers)),
        policy_artifact_consumers=tuple(sorted(policy_consumers)),
        prohibited_observer_capability_imports=tuple(sorted(prohibited_observer_imports)),
        p4a_runtime_consumers=tuple(sorted(p4a_consumers)),
        broker_capability_imports=tuple(sorted(broker_imports)),
        weekly_llm_invocation_markers=tuple(sorted(weekly_markers)),
    )


_LEGACY_SOURCE_CONFIGURATION: Final = {
    "source_policy_contract": (SourceEvidenceSpec("schema:market_data_snapshot"),),
    "authorized_source_inventory": (SourceEvidenceSpec("module:evidence_packet"),),
    "evidence_provenance_contract": (SourceEvidenceSpec("module:evidence_packet"),),
    "evidence_timestamp_semantics": (SourceEvidenceSpec("module:evidence_packet"),),
    "field_level_freshness_contract": (SourceEvidenceSpec("module:evidence_packet"),),
    "evidence_conflict_gap_contract": (SourceEvidenceSpec("module:evidence_packet"),),
    "structured_market_metrics": (SourceEvidenceSpec("schema:market_data_snapshot"), SourceEvidenceSpec("module:market_data_snapshot_builder")),
    "structured_scheduled_events": (SourceEvidenceSpec("module:evidence_packet"),),
    "prior_thesis_continuity": (SourceEvidenceSpec("module:step2_decision_builder"),),
    "prompt_envelope_identity": (SourceEvidenceSpec("module:step2_decision_builder"),),
    "analyst_generation_provenance": (SourceEvidenceSpec("module:step2_decision_builder"),),
    "analyst_parser_validator": (SourceEvidenceSpec("module:step2_decision_builder"),),
    "analyst_semantic_identity": (SourceEvidenceSpec("module:step2_decision_builder"),),
    "portfolio_objective_contract": (SourceEvidenceSpec("module:step2_decision_builder"),),
    "portfolio_hard_constraints": (SourceEvidenceSpec("module:step2_decision_builder"),),
    "new_buy_eligibility_evaluation": (SourceEvidenceSpec("module:step2_decision_builder"),),
    "semantic_audit_parser_validator": (SourceEvidenceSpec("module:step3_audit_engine"),),
    "semantic_finding_identity": (SourceEvidenceSpec("module:step3_audit_engine"),),
    "complete_buy_order_validator": (SourceEvidenceSpec("module:step4_order_compiler"),),
    "deterministic_order_compiler": (SourceEvidenceSpec("module:step4_order_compiler"),),
    "postcompile_final_safety": (SourceEvidenceSpec("module:step4_order_compiler"),),
}

_RUNTIME_FACETS: Final = {
    "holdings_state_contract": ("holdings",),
    "cash_state_contract": ("cash",),
    "tax_lot_state_contract": ("tax_lots",),
    "open_order_state_contract": ("open_orders",),
    "account_metadata_contract": ("account_metadata",),
    "manual_order_state_contract": ("manual_orders",),
    "portfolio_snapshot_identity": ("holdings", "cash", "tax_lots", "open_orders", "account_metadata", "manual_orders"),
}

_NEGATIVE_CAPABILITIES: Final = {
    "dormant_p4a_runtime_isolation": "p4a",
    "broker_live_execution_absence": "broker",
    "automatic_weekly_llm_absence": "weekly_llm",
}

def _adapter_configurations() -> dict[str, EvidenceAdapter]:
    """Build exactly one evidence-only adapter configuration per frozen check."""
    configurations: dict[str, EvidenceAdapter] = {}
    for check in CATALOG:
        check_id = check.check_id
        if check_id in _NEGATIVE_CAPABILITIES:
            adapter = EvidenceAdapter(check_id, AdapterKind.NEGATIVE, negative_capability=_NEGATIVE_CAPABILITIES[check_id])
        elif check_id == "fail_closed_weekly_baseline":
            adapter = EvidenceAdapter(
                check_id,
                AdapterKind.WEEKLY,
                target_schema_path="schemas/target_architecture/fail_closed_weekly_baseline.schema.json",
                target_schema_version="ltetf_fail_closed_weekly_baseline_v1",
                producer_path="src/investment_orchestrator/workflow/weekly_orchestrator.py",
                validator_symbol="run_weekly",
                producer_symbol="run_weekly",
                test_path="tests/unit/test_weekly_orchestrator.py",
            )
        elif check_id in _RUNTIME_FACETS:
            adapter = EvidenceAdapter(
                check_id,
                AdapterKind.RUNTIME,
                target_schema_path=_PORTFOLIO_STATE_SCHEMA_RELATIVE_PATH,
                target_schema_version=_PORTFOLIO_STATE_SCHEMA_VERSION,
                producer_path="src/investment_orchestrator/target_architecture/portfolio_state_producer.py",
                validator_symbol="validate_portfolio_state",
                producer_symbol="produce_portfolio_state",
                test_path="tests/unit/test_ltetf_portfolio_state_contract.py",
                required_facets=_RUNTIME_FACETS[check_id],
                logical_current_slot=_CURRENT_RUNTIME_SLOT,
                allowed_consumers=(),
            )
        elif check.contract_owner is ContractOwner.OPERATOR_AND_DETERMINISTIC_VALIDATION:
            adapter = EvidenceAdapter(
                check_id,
                AdapterKind.POLICY,
                target_schema_version=_OPERATOR_MANDATE_POLICY_SCHEMA_VERSION,
                producer_path="src/investment_orchestrator/target_architecture/operator_mandate_policy.py",
                validator_symbol="validate_operator_mandate_policy",
                producer_symbol="load_operator_mandate_policy",
                test_path="tests/unit/test_ltetf_operator_mandate_policy.py",
                policy_section=check_id,
                allowed_consumers=(),
            )
        else:
            adapter = EvidenceAdapter(
                check_id,
                AdapterKind.CONTRACT,
                target_schema_path=f"schemas/target_architecture/{check_id}.schema.json",
                target_schema_version=f"ltetf_{check_id}_v1",
                producer_path=f"src/investment_orchestrator/target_architecture/{check_id}.py",
                validator_symbol="validate_contract",
                producer_symbol="produce",
                test_path=f"tests/unit/test_ltetf_target_architecture_{check_id}.py",
                legacy_sources=_LEGACY_SOURCE_CONFIGURATION.get(check_id, ()),
                allowed_consumers=(),
            )
        configurations[check_id] = adapter
    if tuple(configurations) != tuple(check.check_id for check in CATALOG):
        raise ObserverIntegrityError("OBSERVER_CATALOG_INVALID")
    return configurations


_ADAPTERS: Final = _adapter_configurations()


def _base_records(root: Path) -> tuple[EvidenceRecord, ...]:
    definitions = (
        ("draft:investment_goal_profile_v1", EvidenceKind.DRAFT_DOCUMENT, "docs/investment_goal_profile_v1.md", "config_key", "status", "draft", False),
        ("operator_input:strategy_settings", EvidenceKind.OPERATOR_INPUT, "inputs/current/strategy_settings.yaml", "config_key", "strategy_settings", "yaml", False),
        ("runtime_artifact:current_run_state", EvidenceKind.RUNTIME_ARTIFACT, "inputs/current/current_run_state.json", "json_pointer", "", "json", True),
        ("schema:market_data_snapshot", EvidenceKind.SCHEMA, "schemas/market_data_snapshot.schema.json", "schema_pointer", "#", "schema", False),
        ("module:market_data_snapshot_builder", EvidenceKind.PRODUCTION_MODULE, "src/investment_orchestrator/workflow/build_market_data_snapshot.py", "python_symbol", "module", "python", False),
        ("module:evidence_packet", EvidenceKind.PRODUCTION_MODULE, "src/investment_orchestrator/research/evidence_packet.py", "python_symbol", "module", "python", False),
        ("module:weekly_orchestrator", EvidenceKind.PRODUCTION_MODULE, "src/investment_orchestrator/workflow/weekly_orchestrator.py", "python_symbol", "run_weekly", "python", False),
        ("test:weekly_orchestrator", EvidenceKind.TEST_CONTRACT, "tests/unit/test_weekly_orchestrator.py", "ast_selector", "test_run_weekly", "python", False),
        ("module:step2_decision_builder", EvidenceKind.PRODUCTION_MODULE, "src/investment_orchestrator/workflow/step2_decision_builder.py", "python_symbol", "module", "python", False),
        ("module:step3_audit_engine", EvidenceKind.PRODUCTION_MODULE, "src/investment_orchestrator/workflow/step3_audit_engine.py", "python_symbol", "module", "python", False),
        ("module:step4_order_compiler", EvidenceKind.PRODUCTION_MODULE, "src/investment_orchestrator/workflow/step4_order_compiler.py", "python_symbol", "module", "python", False),
        ("policy_candidate:ltetf_operator_mandate", EvidenceKind.OPERATOR_INPUT, _POLICY_INPUT_RELATIVE_PATH, "json_pointer", "", "json", False),
        ("schema:ltetf_portfolio_state", EvidenceKind.SCHEMA, _PORTFOLIO_STATE_SCHEMA_RELATIVE_PATH, "schema_pointer", "#", "schema", False),
        ("runtime_artifact:ltetf_portfolio_state", EvidenceKind.RUNTIME_ARTIFACT, _PORTFOLIO_STATE_RELATIVE_PATH, "json_pointer", "", "json", True),
        ("runtime_artifact:weekly_outcome", EvidenceKind.RUNTIME_ARTIFACT, "artifacts/current/weekly_outcome.json", "json_pointer", "", "json", True),
    )
    return tuple(
        _observe_path(root, evidence_id, kind, path, locator_kind=locator_kind, locator=locator, parser=parser, current=current)
        for evidence_id, kind, path, locator_kind, locator, parser, current in definitions
    )


def _adapter_records(root: Path) -> tuple[EvidenceRecord, ...]:
    records: list[EvidenceRecord] = []
    recorded_paths: set[tuple[str, str]] = set()

    def append(record: EvidenceRecord) -> None:
        key = (record.evidence_id, record.repository_relative_path or "")
        if key in recorded_paths:
            raise ObserverIntegrityError("EVIDENCE_COLLECTION_FAILED")
        recorded_paths.add(key)
        if record.validation_state is not EvidenceValidationState.ABSENT:
            records.append(record)

    # Every candidate that can claim a current policy/runtime slot receives a
    # stable evidence id before validation.  This permits direct conflicts to
    # cite concrete evidence rather than a synthesized status.
    for index, relative_path in enumerate(_policy_candidate_paths(root)):
        if relative_path == _POLICY_INPUT_RELATIVE_PATH:
            continue
        append(
            _observe_path(
                root,
                _policy_candidate_evidence_id(relative_path, index),
                EvidenceKind.OPERATOR_INPUT,
                relative_path,
                locator_kind="json_pointer",
                locator="",
                parser="json",
            )
        )
    for index, relative_path in enumerate(_runtime_candidate_paths(root)):
        if relative_path == _PORTFOLIO_STATE_RELATIVE_PATH:
            continue
        append(
            _observe_path(
                root,
                _runtime_candidate_evidence_id(relative_path, index),
                EvidenceKind.RUNTIME_ARTIFACT,
                relative_path,
                locator_kind="json_pointer",
                locator="",
                parser="json",
                current=True,
            )
        )
    for adapter in _ADAPTERS.values():
        if adapter.kind is AdapterKind.CONTRACT and adapter.target_schema_path and adapter.producer_path and adapter.test_path:
            for label, kind, path, parser, locator in (
                ("schema", EvidenceKind.SCHEMA, adapter.target_schema_path, "schema", "#"),
                ("producer", EvidenceKind.PRODUCTION_MODULE, adapter.producer_path, "python", adapter.producer_symbol or "module"),
                ("test", EvidenceKind.TEST_CONTRACT, adapter.test_path, "python", "contract"),
            ):
                candidate = _observe_path(root, f"adapter:{adapter.check_id}:{label}", kind, path, locator_kind="schema_pointer" if label == "schema" else "python_symbol", locator=locator, parser=parser)
                append(candidate)
        elif adapter.kind is AdapterKind.RUNTIME and adapter.producer_path and adapter.test_path:
            for label, kind, path, parser, locator in (
                ("producer", EvidenceKind.PRODUCTION_MODULE, adapter.producer_path, "python", adapter.producer_symbol or "module"),
                ("test", EvidenceKind.TEST_CONTRACT, adapter.test_path, "python", "contract"),
            ):
                candidate = _observe_path(root, f"adapter:{adapter.check_id}:{label}", kind, path, locator_kind="python_symbol", locator=locator, parser=parser)
                append(candidate)
        elif adapter.kind is AdapterKind.POLICY and adapter.producer_path and adapter.test_path:
            for label, kind, path, parser, locator in (
                ("producer", EvidenceKind.PRODUCTION_MODULE, adapter.producer_path, "python", adapter.producer_symbol or "module"),
                ("test", EvidenceKind.TEST_CONTRACT, adapter.test_path, "python", "contract"),
            ):
                candidate = _observe_path(root, f"adapter:{adapter.check_id}:{label}", kind, path, locator_kind="python_symbol", locator=locator, parser=parser)
                append(candidate)
    return tuple(records)


def collect_repository_evidence(root: Path) -> RepositoryEvidence:
    """Collect only bounded deterministic evidence; no status is assigned here."""
    checked_root = _safe_root(root)
    inventory = _scan_production_inventory(checked_root)
    records = _base_records(checked_root) + _adapter_records(checked_root)
    ids = [record.evidence_id for record in records]
    if len(ids) != len(set(ids)):
        raise ObserverIntegrityError("EVIDENCE_COLLECTION_FAILED")
    return RepositoryEvidence(checked_root, tuple(sorted(records, key=lambda record: record.evidence_id)), inventory)


def _records_by_id(evidence: RepositoryEvidence) -> dict[str, EvidenceRecord]:
    if type(evidence) is not RepositoryEvidence:
        raise ObserverIntegrityError("EVIDENCE_COLLECTION_FAILED")
    result = {record.evidence_id: record for record in evidence.records}
    if len(result) != len(evidence.records):
        raise ObserverIntegrityError("EVIDENCE_COLLECTION_FAILED")
    inventory_record = _inventory_to_evidence_record(evidence.inventory)
    if inventory_record.evidence_id in result:
        raise ObserverIntegrityError("EVIDENCE_COLLECTION_FAILED")
    result[inventory_record.evidence_id] = inventory_record
    return result


def _valid_ids(records: Mapping[str, EvidenceRecord], evidence_ids: Iterable[str]) -> tuple[str, ...]:
    return tuple(
        evidence_id
        for evidence_id in evidence_ids
        if (record := records.get(evidence_id)) is not None and record.validation_state is EvidenceValidationState.VALID
    )


def _source_ast(root: Path, relative_path: str) -> ast.AST | None:
    path = _safe_repository_file_path(root, relative_path)
    if path is None or not path.exists() or path.is_symlink() or not path.is_file():
        return None
    try:
        return ast.parse(path.read_text(encoding="utf-8"), filename=relative_path)
    except (OSError, UnicodeDecodeError, SyntaxError):
        raise ObserverIntegrityError("EVIDENCE_COLLECTION_FAILED") from None


_PROHIBITED_AUTHORITY_FIELDS: Final = frozenset(
    {
        "approval",
        "approval_state",
        "authority_effect",
        "broker_authorization",
        "broker_live_execution",
        "permission",
        "permission_granted",
        "stage_reachability",
        "trade_permission",
    }
)
_PROHIBITED_AUTHORITY_CALLS: Final = frozenset(
    {
        "approve",
        "authorize_trade",
        "execute_live",
        "grant_permission",
        "publish_canonical",
    }
)


def _literal_data(node: ast.AST | None) -> object | None:
    """Return only a bounded literal data tree; never evaluate source code."""
    if isinstance(node, ast.Constant) and type(node.value) in {str, int, float, bool, type(None)}:
        return node.value
    if isinstance(node, (ast.List, ast.Tuple)):
        values = [_literal_data(item) for item in node.elts]
        return values if all(item is not None for item in values) else None
    if isinstance(node, ast.Dict):
        result: dict[str, object] = {}
        for key, value in zip(node.keys, node.values, strict=True):
            literal_key = _literal_string(key)
            literal_value = _literal_data(value)
            if literal_key is None or literal_value is None:
                return None
            result[literal_key] = literal_value
        return result
    return None


def _assigned_literal(tree: ast.AST, name: str) -> object | None:
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == name for target in node.targets
        ):
            return _literal_data(node.value)
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == name:
            return _literal_data(node.value)
    return None


def _annotation_root(node: ast.AST | None) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Subscript):
        return _annotation_root(node.value)
    if isinstance(node, ast.Constant) and type(node.value) is str:
        return node.value.split("[", 1)[0]
    return None


def _function_nodes(tree: ast.AST) -> dict[str, ast.FunctionDef | ast.AsyncFunctionDef]:
    return {
        node.name: node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _source_contract_facts(
    root: Path,
    adapter: EvidenceAdapter,
    schema: Mapping[str, object] | None = None,
) -> SourceContractFacts:
    """Collect structural target-contract facts without executing source.

    A target source must publish a machine-readable ``LTETF_TARGET_CONTRACT``
    declaration.  The declaration is cross-checked against the closed target
    schema and source declarations.  These are supporting facts only: the
    observer never executes target code and source structure cannot establish
    behavioral or runtime-integration predicates.
    """
    if not adapter.producer_path:
        return SourceContractFacts()
    tree = _source_ast(root, adapter.producer_path)
    if tree is None:
        return SourceContractFacts()
    functions = _function_nodes(tree)
    validator = functions.get(adapter.validator_symbol or "")
    producer = functions.get(adapter.producer_symbol or "")
    validator_exists = validator is not None
    producer_exists = producer is not None

    declaration = _assigned_literal(tree, "LTETF_TARGET_CONTRACT")
    required = schema.get("required") if type(schema) is dict else None
    schema_identity = schema.get("x-contract-identity-sha256") if type(schema) is dict else None
    required_fields = frozenset(required) if type(required) is list and all(type(item) is str for item in required) else frozenset()
    declaration_ready = type(declaration) is dict
    declared_required = declaration.get("required_fields") if declaration_ready else None
    declared_prohibited = declaration.get("prohibited_authority_fields") if declaration_ready else None
    invariants = declaration.get("target_invariants") if declaration_ready else None
    declaration_matches = bool(
        declaration_ready
        and declaration.get("schema_version") == adapter.target_schema_version
        and declaration.get("schema_identity_sha256") == schema_identity
        and declaration.get("validator_symbol") == adapter.validator_symbol
        and declaration.get("producer_symbol") == adapter.producer_symbol
        and type(declared_required) is list
        and all(type(item) is str for item in declared_required)
        and frozenset(declared_required) == required_fields
        and type(declared_prohibited) is list
        and all(type(item) is str for item in declared_prohibited)
        and frozenset(declared_prohibited) == _PROHIBITED_AUTHORITY_FIELDS
        and type(invariants) is list
        and bool(invariants)
        and all(type(item) is str and item for item in invariants)
        and declaration.get("legacy_semantics") is False
    )
    authority_absent = True
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            for key in node.keys:
                if _literal_string(key) in _PROHIBITED_AUTHORITY_FIELDS:
                    authority_absent = False
        elif isinstance(node, ast.Call) and _call_name(node) in _PROHIBITED_AUTHORITY_CALLS:
            authority_absent = False
    return SourceContractFacts(
        validator_exists=validator_exists,
        producer_exists=producer_exists,
        validator_reached=False,
        target_semantics=False,
        prohibited_authority_absent=authority_absent and declaration_matches,
        deterministic_enforcement=False,
    )


_NONEXECUTING_BEHAVIORAL_PREDICATES: Final = frozenset(
    {"P04", "P06", "P17", "P21", "P22", "P25", "P26", "P30", "P31", "P32", "P33", "P40"}
)


def _nonexecuting_behavioral_facts(
    check: CatalogCheck,
) -> tuple[dict[str, bool], tuple[str, ...]]:
    """Return conservative facts without importing or invoking target code.

    Source, schema, artifact, and test observations can support a gap report,
    but they cannot establish runtime behavior.  No currently authorized
    LTETF-01 evidence contract supplies behavioral proof, so every behavioral
    predicate and modifier remains unsatisfied.  This is a normal readiness
    gap, not observer-integrity failure.
    """
    values = {
        "P25": False,
        "P26": False,
        "P30": False,
        "P31": False,
        "P32": False,
        "P33": False,
    }
    diagnostics = (
        ("BEHAVIORAL_PROBE_UNAVAILABLE",)
        if set(check.required_proof_predicates) & _NONEXECUTING_BEHAVIORAL_PREDICATES
        else ()
    )
    return values, diagnostics


def _adapter_consumers_compatible(adapter: EvidenceAdapter, inventory: ProductionInventory) -> bool:
    """Compare resolved production imports/entry points to the adapter allowlist."""
    if not adapter.producer_path:
        return adapter.allowed_consumers == ()
    try:
        module_name = _module_name_for_path(adapter.producer_path)
    except ObserverIntegrityError:
        raise ObserverIntegrityError("OBSERVER_CATALOG_INVALID") from None
    actual: set[str] = set()
    for relative_path, imports in inventory.imports_by_path:
        if relative_path == adapter.producer_path:
            continue
        if any(
            imported == module_name or imported.startswith(f"{module_name}.")
            for imported in imports
        ):
            actual.add(relative_path)
    for locator, target in inventory.entry_points:
        target_module = target.split(":", 1)[0]
        if target_module == module_name or target_module.startswith(f"{module_name}."):
            actual.add(locator)
    return tuple(sorted(actual)) == adapter.allowed_consumers


def _test_contract_facts(
    root: Path,
    adapter: EvidenceAdapter,
    schema: Mapping[str, object] | None = None,
) -> TestContractFacts:
    """Collect bounded supporting-test presence, never behavioral proof.

    Tests are supporting evidence only.  This function never interprets test
    names, assertions, ``pytest.raises``, or validator-call syntax as evidence
    that a production validator accepts or rejects a fixture.
    """
    del schema
    if not adapter.test_path:
        return TestContractFacts()
    tree = _source_ast(root, adapter.test_path)
    if tree is None:
        return TestContractFacts()
    test_functions = [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_")
    ]
    return TestContractFacts(tests_support=bool(test_functions))


def _target_schema_facts(root: Path, adapter: EvidenceAdapter) -> tuple[bool, bool, bool, tuple[str, ...]]:
    if not adapter.target_schema_path or not adapter.target_schema_version:
        return False, False, False, ("TARGET_CONTRACT_ABSENT",)
    payload, _ = _read_json_object(root, adapter.target_schema_path)
    if payload is None:
        return False, False, False, ("TARGET_CONTRACT_ABSENT",)
    if payload.get("$schema") != JSON_SCHEMA_DRAFT_2020_12:
        return False, False, False, ("EVIDENCE_SCHEMA_INVALID",)
    try:
        Draft202012Validator.check_schema(payload)
    except SchemaError:
        return False, False, False, ("EVIDENCE_SCHEMA_INVALID",)
    version = _schema_version_matches(payload, adapter.target_schema_version)
    closed = _schema_is_closed_and_bounded(payload)
    identity = _schema_identity_matches(payload)
    diagnostics: list[str] = []
    if not version:
        diagnostics.append("UNKNOWN_CONTRACT_VERSION")
    if not identity:
        diagnostics.append("TARGET_CONTRACT_IDENTITY_INVALID")
    return version, closed, identity, tuple(diagnostics)


def _runtime_schema_contract_is_complete(
    schema: Mapping[str, object] | None,
    adapter: EvidenceAdapter,
) -> bool:
    """Require the full closed target runtime envelope, not merely JSON shape."""
    if (
        type(schema) is not dict
        or not adapter.logical_current_slot
        or schema.get("$schema") != JSON_SCHEMA_DRAFT_2020_12
        or schema.get("additionalProperties") is not False
        or not _schema_identity_matches(schema)
    ):
        return False
    properties = schema.get("properties")
    required = schema.get("required")
    if type(properties) is not dict or type(required) is not list or any(type(item) is not str for item in required):
        return False
    base = {
        "schema_version",
        "current_slot",
        "is_fixture",
        "is_archive",
        "content_identity_sha256",
    }
    if not base <= set(required) or not base <= set(properties):
        return False
    version_property = properties.get("schema_version")
    slot_property = properties.get("current_slot")
    if type(version_property) is not dict or version_property.get("const") != adapter.target_schema_version:
        return False
    if type(slot_property) is not dict or slot_property.get("const") != adapter.logical_current_slot:
        return False
    required_names = set(required)
    required_facets = set(adapter.required_facets)
    # A facet that is merely declared optional cannot establish a complete
    # current target contract.  Require both its schema and runtime presence.
    return required_facets <= set(properties) and required_facets <= required_names


def _policy_identity(payload: Mapping[str, object]) -> str:
    record = dict(payload)
    record.pop("policy_identity_sha256", None)
    return _sha256_identity(POLICY_IDENTITY_DOMAIN, record, maximum=MAX_EVIDENCE_CANONICAL_BYTES)


def _policy_candidate_paths(root: Path) -> tuple[str, ...]:
    """Return all regular, repository-local policy candidates for this slot."""
    directory = _safe_repository_file_path(root, "inputs/current")
    if directory is None or not directory.exists() or directory.is_symlink() or not directory.is_dir():
        return ()
    try:
        paths = tuple(
            sorted(
                path
                for path in directory.iterdir()
                if path.name.startswith("ltetf_operator_mandate") and path.suffix == ".json"
            )
        )
    except OSError:
        raise ObserverIntegrityError("EVIDENCE_COLLECTION_FAILED") from None
    result: list[str] = []
    for path in paths:
        if path.is_symlink() or not path.is_file():
            raise ObserverIntegrityError("EVIDENCE_COLLECTION_FAILED")
        result.append(_repository_relative_path(root, path))
    return tuple(result)


def _policy_candidate_evidence_id(relative_path: str, index: int) -> str:
    if relative_path == _POLICY_INPUT_RELATIVE_PATH:
        return "policy_candidate:ltetf_operator_mandate"
    return f"policy_candidate:ltetf_operator_mandate_candidate_{index}"


def _validate_policy_candidate(
    root: Path,
    payload: object,
    inventory: ProductionInventory,
    adapter: EvidenceAdapter,
) -> tuple[PolicyObservation, bool, bool, bool, bool, bool, tuple[str, ...]]:
    if type(payload) is not dict:
        return PolicyObservation.INVALID, False, False, False, False, False, ("EVIDENCE_JSON_ROOT_INVALID",)
    schema_version = payload.get("schema_version")
    schema_path_value = payload.get("schema_path")
    acceptance_state = payload.get("acceptance_state")
    supplied_identity = payload.get("policy_identity_sha256")
    effective_version = payload.get("effective_version")
    activation_marker = payload.get("activation_marker")
    permitted_consumers = payload.get("permitted_consumer_set")
    if schema_version != _OPERATOR_MANDATE_POLICY_SCHEMA_VERSION:
        return PolicyObservation.INVALID, False, False, False, False, False, ("POLICY_SCHEMA_VERSION_UNSUPPORTED",)
    if not _is_normalized_relative_path(schema_path_value) or not str(schema_path_value).startswith("schemas/"):
        return PolicyObservation.INVALID, False, False, False, False, False, ("POLICY_SCHEMA_PATH_INVALID",)
    schema_path = _safe_repository_file_path(root, schema_path_value)
    if schema_path is None or not schema_path.is_file() or schema_path.is_symlink():
        return PolicyObservation.INVALID, False, False, False, False, False, ("POLICY_SCHEMA_UNAVAILABLE",)
    if type(supplied_identity) is not str or not _SHA256_RE.fullmatch(supplied_identity) or supplied_identity != _policy_identity(payload):
        return PolicyObservation.INVALID, False, False, False, False, False, ("POLICY_IDENTITY_MISMATCH",)
    if type(effective_version) is not str or not effective_version or len(effective_version) > 128:
        return PolicyObservation.INVALID, False, False, False, False, False, ("POLICY_EFFECTIVE_VERSION_INVALID",)
    if type(permitted_consumers) is not list or len(permitted_consumers) > 64 or any(type(item) is not str or not _is_normalized_relative_path(item) for item in permitted_consumers) or len(set(permitted_consumers)) != len(permitted_consumers):
        return PolicyObservation.INVALID, False, False, False, False, False, ("POLICY_CANDIDATE_FIELDS_INVALID",)
    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        if (
            type(schema) is not dict
            or schema.get("$schema") != JSON_SCHEMA_DRAFT_2020_12
            or not _schema_is_closed_and_bounded(schema)
            or not _schema_version_matches(schema, _OPERATOR_MANDATE_POLICY_SCHEMA_VERSION)
            or not _schema_identity_matches(schema)
        ):
            return PolicyObservation.INVALID, False, False, False, False, False, ("POLICY_SCHEMA_NOT_CLOSED",)
        Draft202012Validator.check_schema(schema)
        validator = Draft202012Validator(schema)
        if tuple(validator.iter_errors(payload)):
            return PolicyObservation.INVALID, False, False, False, False, False, ("POLICY_CANDIDATE_SCHEMA_INVALID",)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, SchemaError):
        return PolicyObservation.INVALID, False, False, False, False, False, ("POLICY_SCHEMA_INVALID",)
    actual_consumers = tuple(sorted(inventory.policy_artifact_consumers))
    consumers_compatible = tuple(sorted(permitted_consumers)) == actual_consumers
    if not consumers_compatible:
        return PolicyObservation.INVALID, False, False, False, False, False, ("POLICY_CONSUMER_MISMATCH",)
    if acceptance_state in {"candidate", "unaccepted"}:
        if activation_marker != "inactive":
            return PolicyObservation.INVALID, False, False, False, False, False, ("POLICY_ACTIVATION_INVALID",)
        return PolicyObservation.UNACCEPTED, True, False, True, True, True, ()
    if acceptance_state == "accepted":
        valid_activation = activation_marker == "active" or (
            activation_marker == "contract_readiness_only" and adapter.allow_accepted_nonactive
        )
        if not valid_activation:
            return PolicyObservation.INVALID, False, False, False, False, False, ("UNSUPPORTED_ACTIVATION_MARKER",)
        return PolicyObservation.ACCEPTED, True, True, True, True, True, ()
    return PolicyObservation.INVALID, False, False, False, False, False, ("POLICY_ACCEPTANCE_STATE_INVALID",)


def _machine_value_is_present(value: object) -> bool:
    """Reject empty placeholders while preserving valid false/zero policy values."""
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value)
    if isinstance(value, (list, tuple, dict, set, frozenset)):
        return bool(value)
    return type(value) in {bool, int, float}


def _policy_section_facets_complete(
    payload: Mapping[str, object] | None,
    schema: Mapping[str, object] | None,
    section_name: str | None,
) -> bool:
    """Require every schema-defined, check-specific policy facet to be present.

    The policy schema is itself identity-bound by candidate validation.  Its
    ``policies.<check_id>.required`` sequence is therefore the only accepted
    per-check facet declaration; empty sections or optional-only placeholders
    cannot establish P18.
    """
    if type(payload) is not dict or type(schema) is not dict or not section_name:
        return False
    policies = payload.get("policies")
    root_properties = schema.get("properties")
    if type(policies) is not dict or type(root_properties) is not dict:
        return False
    policies_schema = root_properties.get("policies")
    if type(policies_schema) is not dict:
        return False
    section_definitions = policies_schema.get("properties")
    if type(section_definitions) is not dict:
        return False
    section_schema = section_definitions.get(section_name)
    section_value = policies.get(section_name)
    if type(section_schema) is not dict or type(section_value) is not dict or not section_value:
        return False
    properties = section_schema.get("properties")
    required = section_schema.get("required")
    if (
        type(properties) is not dict
        or type(required) is not list
        or not required
        or any(type(item) is not str or item not in properties for item in required)
    ):
        return False
    return all(
        facet in section_value and _machine_value_is_present(section_value[facet])
        for facet in required
    )


def _runtime_identity(payload: Mapping[str, object]) -> str:
    record = dict(payload)
    record.pop("content_identity_sha256", None)
    return _sha256_identity(RUNTIME_IDENTITY_DOMAIN, record, maximum=MAX_EVIDENCE_CANONICAL_BYTES)


def _runtime_candidate_evidence_id(relative_path: str, index: int) -> str:
    if relative_path == _PORTFOLIO_STATE_RELATIVE_PATH:
        return "runtime_artifact:ltetf_portfolio_state"
    return f"runtime_artifact:ltetf_portfolio_state_candidate_{index}"


def _runtime_candidate_paths(root: Path) -> tuple[str, ...]:
    directory = _safe_repository_file_path(root, "inputs/current")
    if directory is None or not directory.exists() or directory.is_symlink() or not directory.is_dir():
        return ()
    try:
        paths = tuple(sorted(path for path in directory.iterdir() if path.name.startswith("ltetf_portfolio_state") and path.suffix == ".json"))
    except OSError:
        raise ObserverIntegrityError("EVIDENCE_COLLECTION_FAILED") from None
    relative: list[str] = []
    for path in paths:
        if path.is_symlink() or not path.is_file():
            raise ObserverIntegrityError("EVIDENCE_COLLECTION_FAILED")
        relative.append(_repository_relative_path(root, path))
    return tuple(relative)


def _runtime_candidate_is_current_path(relative_path: str) -> bool:
    """Reject fixture/archive/history/stale names even when placed under current."""
    name = PurePosixPath(relative_path).name.lower()
    return not any(marker in name for marker in ("fixture", "archive", "history", "stale"))


def _validate_runtime_candidates(
    root: Path,
    adapter: EvidenceAdapter,
    schema: Mapping[str, object] | None,
) -> tuple[RuntimeObservation, tuple[str, ...], tuple[str, ...], bool]:
    if schema is None or not adapter.logical_current_slot:
        return RuntimeObservation.ABSENT, (), (), False
    candidate_paths = _runtime_candidate_paths(root)
    if not candidate_paths:
        return RuntimeObservation.ABSENT, (), (), False
    validator = Draft202012Validator(schema)
    valid: list[tuple[str, str, dict[str, object]]] = []
    diagnostics: list[str] = []
    invalid = False
    for index, relative_path in enumerate(candidate_paths):
        payload, _ = _read_json_object(root, relative_path)
        evidence_id = _runtime_candidate_evidence_id(relative_path, index)
        if not _runtime_candidate_is_current_path(relative_path):
            invalid = True
            diagnostics.append("RUNTIME_SLOT_NOT_CURRENT")
            continue
        if payload is None or tuple(validator.iter_errors(payload)):
            invalid = True
            diagnostics.append("CURRENT_RUNTIME_DATA_INVALID")
            continue
        identity = payload.get("content_identity_sha256")
        if type(identity) is not str or not _SHA256_RE.fullmatch(identity) or identity != _runtime_identity(payload):
            invalid = True
            diagnostics.append("CURRENT_RUNTIME_DATA_INVALID")
            continue
        if payload.get("schema_version") != adapter.target_schema_version or payload.get("current_slot") != adapter.logical_current_slot or payload.get("is_fixture") is not False or payload.get("is_archive") is not False:
            invalid = True
            diagnostics.append("RUNTIME_SLOT_NOT_CURRENT")
            continue
        valid.append((evidence_id, identity, payload))
    identities = {identity for _, identity, _ in valid}
    if len(identities) > 1:
        return RuntimeObservation.CONFLICT, tuple(item[0] for item in valid), ("CURRENT_RUNTIME_IDENTITY_CONFLICT",), False
    if invalid:
        return RuntimeObservation.INVALID, tuple(item[0] for item in valid), _bounded_unique_strings(diagnostics), False
    if not valid:
        return RuntimeObservation.ABSENT, (), (), False
    required = adapter.required_facets
    complete = all(facet in valid[0][2] and valid[0][2][facet] not in (None, [], {}, "") for facet in required)
    return (
        RuntimeObservation.COMPLETE if complete else RuntimeObservation.INCOMPLETE,
        tuple(item[0] for item in valid),
        () if complete else ("RUNTIME_DATA_INCOMPLETE",),
        complete,
    )


def _facts_for_negative(adapter: EvidenceAdapter, evidence: RepositoryEvidence) -> AdapterFacts:
    inventory = evidence.inventory
    if adapter.negative_capability == "p4a":
        prohibited = inventory.p4a_runtime_consumers
    elif adapter.negative_capability == "broker":
        prohibited = inventory.broker_capability_imports
    elif adapter.negative_capability == "weekly_llm":
        prohibited = inventory.weekly_llm_invocation_markers
    else:
        raise ObserverIntegrityError("OBSERVER_CATALOG_INVALID")
    contradiction = bool(prohibited)
    return AdapterFacts(
        evidence_ids=("repository_inventory:production",),
        inventory_complete=True,
        prohibited_capability_absent=not contradiction,
        identities_nonconflicting=not contradiction,
        prose_excluded=True,
        tests_not_sole_proof=True,
        direct_contradiction=contradiction,
        contradiction_reason="PROHIBITED_CONSUMER_PRESENT" if contradiction else None,
        contradiction_evidence_ids=("repository_inventory:production",) if contradiction else (),
        disqualifying_conditions=("prohibited_production_consumer_present",) if contradiction else (),
    )


def _facts_for_weekly(
    check: CatalogCheck,
    adapter: EvidenceAdapter,
    evidence: RepositoryEvidence,
    records: Mapping[str, EvidenceRecord],
) -> AdapterFacts:
    module = records.get("module:weekly_orchestrator")
    test = records.get("test:weekly_orchestrator")
    outcome = records.get("runtime_artifact:weekly_outcome")
    current = records.get("runtime_artifact:current_run_state")
    outcome_payload, _ = _read_json_object(evidence.root, "artifacts/current/weekly_outcome.json")
    degraded_payload, _ = _read_json_object(evidence.root, "artifacts/current/step1_research/research_degraded_mode_decision.json")
    behavior_valid = bool(
        type(outcome_payload) is dict
        and type(degraded_payload) is dict
        and outcome_payload.get("terminal_result") == "NO_TRADE"
        and outcome_payload.get("allowed_actions") == degraded_payload.get("allowed_actions") == ["HOLD", "NO_TRADE"]
        and "SELL" in outcome_payload.get("blocked_actions", [])
        and "NEW_BUY" in outcome_payload.get("blocked_actions", [])
        and "ORDER_COMPILATION" in outcome_payload.get("blocked_actions", [])
    )
    module_valid = module is not None and module.validation_state is EvidenceValidationState.VALID
    test_valid = test is not None and test.validation_state is EvidenceValidationState.VALID
    evidence_ids = tuple(item.evidence_id for item in (module, test, outcome, current) if item is not None and item.validation_state is EvidenceValidationState.VALID)
    no_broker = not evidence.inventory.broker_capability_imports
    return AdapterFacts(
        evidence_ids=evidence_ids,
        diagnostics=_bounded_unique_strings(
            (
                *(("WEEKLY_BEHAVIOR_PROOF_INCOMPLETE",) if not behavior_valid else ()),
                *_nonexecuting_behavioral_facts(check)[1],
            )
        ),
        validator_exists=module_valid,
        producer_exists=module_valid,
        validator_reached=False,
        tests_support=test_valid,
        target_semantics=False,
        facets_complete=behavior_valid,
        prohibited_authority_absent=no_broker,
        deterministic_enforcement=False,
        fails_closed=False,
        inventory_complete=True,
        prohibited_capability_absent=no_broker,
        prose_excluded=True,
        tests_not_sole_proof=module_valid and test_valid,
        producer_validator_consumer_compatible=False,
        target_compatible_partial=module_valid and behavior_valid,
        identities_nonconflicting=True,
    )


def _draft_is_relevant_to_check(
    root: Path,
    draft_record: EvidenceRecord | None,
    check: CatalogCheck,
) -> bool:
    """Attach design material only when it explicitly names this prerequisite."""
    if (
        draft_record is None
        or draft_record.validation_state is not EvidenceValidationState.DRAFT
        or draft_record.repository_relative_path is None
    ):
        return False
    path = _safe_repository_file_path(root, draft_record.repository_relative_path)
    if path is None or not path.is_file() or path.is_symlink():
        return False
    try:
        text = path.read_text(encoding="utf-8").lower()
    except (OSError, UnicodeDecodeError):
        raise ObserverIntegrityError("EVIDENCE_COLLECTION_FAILED") from None
    explicit_markers = (
        f"ltetf-01 prerequisite: {check.check_id}",
        f"ltetf-check: {check.check_id}",
        f"`{check.check_id}`",
    )
    return any(marker in text for marker in explicit_markers)


def _facts_for_policy(check: CatalogCheck, adapter: EvidenceAdapter, evidence: RepositoryEvidence, records: Mapping[str, EvidenceRecord]) -> AdapterFacts:
    draft_record = records.get("draft:investment_goal_profile_v1")
    observation = PolicyObservation.NONE
    candidate_valid = accepted = activation = effective = consumers = False
    diagnostics: list[str] = []
    selected_payload: dict[str, object] | None = None
    selected_evidence_id: str | None = None
    valid_candidates: list[tuple[str, str, dict[str, object], PolicyObservation, bool, bool, bool, bool, bool]] = []
    for index, relative_path in enumerate(_policy_candidate_paths(evidence.root)):
        payload, read_diagnostics = _read_json_object(evidence.root, relative_path)
        evidence_id = _policy_candidate_evidence_id(relative_path, index)
        if payload is None:
            diagnostics.extend(read_diagnostics)
            continue
        result = _validate_policy_candidate(evidence.root, payload, evidence.inventory, adapter)
        candidate_observation, valid, candidate_accepted, valid_activation, valid_effective, valid_consumers, candidate_diagnostics = result
        diagnostics.extend(candidate_diagnostics)
        if valid:
            valid_candidates.append(
                (
                    relative_path,
                    evidence_id,
                    payload,
                    candidate_observation,
                    valid,
                    candidate_accepted,
                    valid_activation,
                    valid_effective,
                    valid_consumers,
                )
            )
    accepted_candidates = [
        candidate
        for candidate in valid_candidates
        if candidate[3] is PolicyObservation.ACCEPTED
    ]
    accepted_identities = {
        str(candidate[2]["policy_identity_sha256"])
        for candidate in accepted_candidates
    }
    direct_conflict = len(accepted_identities) > 1
    if direct_conflict:
        observation = PolicyObservation.CONFLICT
        diagnostics.append("ACTIVE_POLICY_IDENTITY_CONFLICT")
    elif valid_candidates:
        # The canonical current path wins only for selecting policy content;
        # all valid accepted identities were already checked for conflict.
        selected = next(
            (candidate for candidate in valid_candidates if candidate[0] == _POLICY_INPUT_RELATIVE_PATH),
            valid_candidates[0],
        )
        _, selected_evidence_id, selected_payload, observation, candidate_valid, accepted, activation, effective, consumers = selected
    elif _policy_candidate_paths(evidence.root):
        observation = PolicyObservation.INVALID
    draft_relevant = _draft_is_relevant_to_check(evidence.root, draft_record, check)
    if observation is PolicyObservation.NONE and draft_relevant:
        observation = PolicyObservation.DRAFT
        diagnostics.append("DRAFT_DOCUMENT_NOT_ACTIVE_POLICY")
    policy_schema: dict[str, object] | None = None
    if type(selected_payload) is dict:
        schema_path_value = selected_payload.get("schema_path")
        if _is_normalized_relative_path(schema_path_value):
            policy_schema, _ = _read_json_object(evidence.root, schema_path_value)
    producer = _source_contract_facts(evidence.root, adapter, policy_schema)
    test_facts = _test_contract_facts(evidence.root, adapter, policy_schema)
    modifier_facts, behavioral_diagnostics = _nonexecuting_behavioral_facts(check)
    section_present = _policy_section_facets_complete(
        selected_payload,
        policy_schema,
        adapter.policy_section,
    )
    producer_evidence_id = f"adapter:{adapter.check_id}:producer"
    test_evidence_id = f"adapter:{adapter.check_id}:test"
    candidate_evidence_ids = tuple(
        _policy_candidate_evidence_id(relative_path, index)
        for index, relative_path in enumerate(_policy_candidate_paths(evidence.root))
    )
    evidence_ids = _valid_ids(records, (*candidate_evidence_ids, producer_evidence_id, test_evidence_id))
    supporting_evidence_ids = (
        ("draft:investment_goal_profile_v1",)
        if draft_relevant
        and draft_record is not None
        and draft_record.evidence_kind in check.optional_supporting_evidence
        else ()
    )
    conflict_evidence_ids = tuple(candidate[1] for candidate in accepted_candidates) if direct_conflict else ()
    return AdapterFacts(
        evidence_ids=evidence_ids,
        supporting_evidence_ids=supporting_evidence_ids,
        diagnostics=_bounded_unique_strings((*diagnostics, *behavioral_diagnostics)),
        contract_frozen=candidate_valid,
        schema_closed_bounded=candidate_valid,
        schema_identity_verified=candidate_valid,
        validator_exists=producer.validator_exists,
        producer_exists=producer.producer_exists,
        validator_reached=False,
        consumers_compatible=consumers,
        tests_support=test_facts.tests_support,
        fixtures_exercised=False,
        target_semantics=False,
        facets_complete=bool(section_present),
        deterministic_enforcement=False,
        fails_closed=False,
        trusted_clock_valid=modifier_facts["P25"],
        selection_bounds_enforced=modifier_facts["P26"],
        bounded_llm_input=modifier_facts["P30"],
        atomic_manual_order_package_proven=modifier_facts["P31"]
        if check.check_id == "atomic_manual_order_package"
        else False,
        atomic_current_package_pointer_proven=modifier_facts["P31"]
        if check.check_id == "atomic_current_package_pointer"
        else False,
        postcompile_validation=modifier_facts["P32"],
        evidence_sufficiency=modifier_facts["P33"],
        inventory_complete=True,
        prose_excluded=True,
        tests_not_sole_proof=producer.producer_exists and test_facts.tests_support,
        producer_validator_consumer_compatible=False,
        policy_candidate_valid=candidate_valid,
        policy_accepted=accepted,
        policy_effective_activation_valid=activation and effective,
        policy_consumers_compatible=consumers,
        policy_observation=observation,
        draft_only=observation is PolicyObservation.DRAFT,
        target_compatible_partial=candidate_valid,
        direct_contradiction=direct_conflict,
        contradiction_reason="ACTIVE_POLICY_IDENTITY_CONFLICT" if direct_conflict else None,
        contradiction_evidence_ids=conflict_evidence_ids,
        disqualifying_conditions=(),
    )


def _facts_for_runtime(
    check: CatalogCheck,
    adapter: EvidenceAdapter,
    evidence: RepositoryEvidence,
    records: Mapping[str, EvidenceRecord],
) -> AdapterFacts:
    schema_payload, _ = _read_json_object(evidence.root, _PORTFOLIO_STATE_SCHEMA_RELATIVE_PATH)
    version, closed, schema_identity, diagnostics = _target_schema_facts(evidence.root, adapter)
    complete_runtime_contract = _runtime_schema_contract_is_complete(schema_payload, adapter)
    if version and closed and schema_identity and not complete_runtime_contract:
        diagnostics = _bounded_unique_strings((*diagnostics, "TARGET_CONTRACT_ABSENT"))
    producer = _source_contract_facts(evidence.root, adapter, schema_payload)
    consumers_compatible = _adapter_consumers_compatible(adapter, evidence.inventory)
    test_facts = _test_contract_facts(evidence.root, adapter, schema_payload)
    modifier_facts, behavioral_diagnostics = _nonexecuting_behavioral_facts(check)
    runtime_observation, runtime_ids, runtime_diagnostics, complete = _validate_runtime_candidates(
        evidence.root,
        adapter,
        schema_payload if version and closed and schema_identity and complete_runtime_contract else None,
    )
    evidence_ids = _valid_ids(records, ("schema:ltetf_portfolio_state", f"adapter:{adapter.check_id}:producer", f"adapter:{adapter.check_id}:test", *runtime_ids))
    direct_conflict = runtime_observation is RuntimeObservation.CONFLICT
    return AdapterFacts(
        evidence_ids=evidence_ids,
        diagnostics=_bounded_unique_strings(
            (*diagnostics, *runtime_diagnostics, *behavioral_diagnostics)
        ),
        contract_frozen=version and closed and schema_identity and complete_runtime_contract,
        schema_closed_bounded=closed,
        schema_identity_verified=schema_identity,
        validator_exists=producer.validator_exists,
        producer_exists=producer.producer_exists,
        validator_reached=False,
        consumers_compatible=consumers_compatible,
        tests_support=test_facts.tests_support,
        fixtures_exercised=False,
        target_semantics=False,
        facets_complete=complete,
        deterministic_enforcement=False,
        fails_closed=False,
        trusted_clock_valid=modifier_facts["P25"],
        selection_bounds_enforced=modifier_facts["P26"],
        bounded_llm_input=modifier_facts["P30"],
        atomic_manual_order_package_proven=modifier_facts["P31"]
        if check.check_id == "atomic_manual_order_package"
        else False,
        atomic_current_package_pointer_proven=modifier_facts["P31"]
        if check.check_id == "atomic_current_package_pointer"
        else False,
        postcompile_validation=modifier_facts["P32"],
        evidence_sufficiency=modifier_facts["P33"],
        inventory_complete=True,
        identities_nonconflicting=runtime_observation is not RuntimeObservation.CONFLICT,
        structured_facets_valid=complete,
        prose_excluded=True,
        tests_not_sole_proof=producer.producer_exists and test_facts.tests_support,
        producer_validator_consumer_compatible=False,
        runtime_observation=runtime_observation,
        target_compatible_partial=version and producer.producer_exists,
        direct_contradiction=direct_conflict,
        contradiction_reason="CURRENT_RUNTIME_IDENTITY_CONFLICT" if direct_conflict else None,
        contradiction_evidence_ids=runtime_ids if direct_conflict else (),
        disqualifying_conditions=("fixture_archive_or_history_as_current_data",) if "RUNTIME_SLOT_NOT_CURRENT" in runtime_diagnostics else (),
    )


def _facts_for_contract(check: CatalogCheck, adapter: EvidenceAdapter, evidence: RepositoryEvidence, records: Mapping[str, EvidenceRecord]) -> AdapterFacts:
    version, closed, schema_identity, diagnostics = _target_schema_facts(evidence.root, adapter)
    schema_payload, _ = _read_json_object(evidence.root, adapter.target_schema_path or "")
    complete_schema_facets = _schema_required_facets_are_complete(schema_payload)
    producer = _source_contract_facts(evidence.root, adapter, schema_payload)
    consumers_compatible = _adapter_consumers_compatible(adapter, evidence.inventory)
    test_facts = _test_contract_facts(evidence.root, adapter, schema_payload)
    modifier_facts, behavioral_diagnostics = _nonexecuting_behavioral_facts(check)
    target_ids = _valid_ids(records, (f"adapter:{adapter.check_id}:schema", f"adapter:{adapter.check_id}:producer", f"adapter:{adapter.check_id}:test"))
    legacy_candidate_ids = _valid_ids(records, tuple(source.evidence_id for source in adapter.legacy_sources))
    legacy_supporting_ids = tuple(
        evidence_id
        for evidence_id in legacy_candidate_ids
        if records[evidence_id].evidence_kind in check.optional_supporting_evidence
    )
    draft_record = records.get("draft:investment_goal_profile_v1")
    draft_relevant = _draft_is_relevant_to_check(evidence.root, draft_record, check)
    supporting_ids = _bounded_unique_strings(
        (
            *legacy_supporting_ids,
            *(("draft:investment_goal_profile_v1",) if draft_relevant else ()),
        ),
        maximum=16,
    )
    target_semantics = False
    legacy_mismatch = bool(legacy_candidate_ids) and not target_semantics
    contract_diagnostics = _bounded_unique_strings(
        (
            *diagnostics,
            *behavioral_diagnostics,
            *(("LEGACY_SEMANTIC_MISMATCH",) if legacy_mismatch else ()),
            *(("DRAFT_DOCUMENT_NOT_ACTIVE_POLICY",) if draft_relevant else ()),
        )
    )
    return AdapterFacts(
        evidence_ids=target_ids,
        supporting_evidence_ids=supporting_ids,
        legacy_evidence_ids=legacy_candidate_ids,
        diagnostics=contract_diagnostics,
        contract_frozen=version,
        schema_closed_bounded=closed,
        schema_identity_verified=schema_identity,
        validator_exists=producer.validator_exists,
        producer_exists=producer.producer_exists,
        validator_reached=False,
        consumers_compatible=consumers_compatible,
        tests_support=test_facts.tests_support,
        fixtures_exercised=False,
        target_semantics=target_semantics,
        facets_complete=version and complete_schema_facets and producer.producer_exists,
        prohibited_authority_absent=producer.prohibited_authority_absent,
        deterministic_enforcement=False,
        fails_closed=False,
        trusted_clock_valid=modifier_facts["P25"],
        selection_bounds_enforced=modifier_facts["P26"],
        bounded_llm_input=modifier_facts["P30"],
        atomic_manual_order_package_proven=modifier_facts["P31"]
        if check.check_id == "atomic_manual_order_package"
        else False,
        atomic_current_package_pointer_proven=modifier_facts["P31"]
        if check.check_id == "atomic_current_package_pointer"
        else False,
        postcompile_validation=modifier_facts["P32"],
        evidence_sufficiency=modifier_facts["P33"],
        inventory_complete=True,
        prose_excluded=True,
        tests_not_sole_proof=producer.producer_exists and test_facts.tests_support,
        producer_validator_consumer_compatible=False,
        target_compatible_partial=bool(target_ids or legacy_candidate_ids),
        draft_only=(
            draft_relevant
            and not target_ids
            and not legacy_candidate_ids
        ),
        legacy_semantic_mismatch=legacy_mismatch,
        disqualifying_conditions=(
            ("legacy_semantics_substituted_for_target",)
            if legacy_mismatch
            and "legacy_semantics_substituted_for_target" in check.disqualifying_conditions
            else ()
        ),
    )


_PREDICATE_FACT_FIELDS: Final = {
    "P01": "repository_local_regular",
    "P02": "contract_frozen",
    "P03": "schema_closed_bounded",
    "P04": "fixtures_exercised",
    "P05": "validator_exists",
    "P06": "validator_reached",
    "P07": "producer_exists",
    "P08": "consumers_compatible",
    "P09": "tests_support",
    "P10": "runtime_complete",
    "P11": "runtime_current",
    "P12": "policy_candidate_valid",
    "P13": "policy_accepted",
    "P14": "identity_verified",
    "P15": "policy_effective_activation_valid",
    "P16": "policy_consumers_compatible",
    "P17": "target_semantics",
    "P18": "facets_complete",
    "P19": "prohibited_authority_absent",
    "P20": "report_only_markers",
    "P21": "deterministic_enforcement",
    "P22": "fails_closed",
    "P23": "lineage_complete",
    "P24": "evidence_refs_bound",
    "P25": "trusted_clock_valid",
    "P26": "selection_bounds_enforced",
    "P27": "inventory_complete",
    "P28": "prohibited_capability_absent",
    "P29": "vocabulary_enforced",
    "P30": "bounded_llm_input",
    "P31": "check_specific_atomicity",
    "P32": "postcompile_validation",
    "P33": "evidence_sufficiency",
    "P34": "dependencies_proven",
    "P35": "identities_nonconflicting",
    "P36": "generation_provenance",
    "P37": "structured_facets_valid",
    "P38": "prose_excluded",
    "P39": "tests_not_sole_proof",
    "P40": "producer_validator_consumer_compatible",
}


def _predicate_value(
    predicate_id: str,
    facts: AdapterFacts,
    *,
    dependencies_proven: bool,
    check_id: str | None = None,
) -> bool:
    if predicate_id not in PROOF_PREDICATES or predicate_id not in _PREDICATE_FACT_FIELDS:
        raise ObserverIntegrityError("OBSERVER_CATALOG_INVALID")
    field_name = _PREDICATE_FACT_FIELDS[predicate_id]
    if field_name == "repository_local_regular":
        return bool(facts.evidence_ids or facts.supporting_evidence_ids)
    if field_name == "runtime_complete":
        return facts.runtime_observation is RuntimeObservation.COMPLETE
    if field_name == "runtime_current":
        return facts.runtime_observation in {RuntimeObservation.COMPLETE, RuntimeObservation.INCOMPLETE}
    if field_name == "identity_verified":
        return facts.schema_identity_verified or facts.policy_candidate_valid or facts.runtime_observation in {RuntimeObservation.COMPLETE, RuntimeObservation.INCOMPLETE}
    if field_name == "report_only_markers":
        return facts.prose_excluded and facts.prohibited_authority_absent
    if field_name == "dependencies_proven":
        return dependencies_proven
    if field_name == "check_specific_atomicity":
        if check_id == "atomic_manual_order_package":
            return facts.atomic_manual_order_package_proven
        if check_id == "atomic_current_package_pointer":
            return facts.atomic_current_package_pointer_proven
        # Direct predicate tests have no catalog check context.  They must
        # still require both independently meaningful atomicity facts.
        return (
            facts.atomic_manual_order_package_proven
            and facts.atomic_current_package_pointer_proven
        )
    return bool(getattr(facts, field_name))


def _predicate_diagnostics(predicate_id: str, facts: AdapterFacts, satisfied: bool) -> tuple[str, ...]:
    if satisfied:
        return ()
    if predicate_id in _NONEXECUTING_BEHAVIORAL_PREDICATES:
        return tuple(
            code
            for code in facts.diagnostics
            if code == "BEHAVIORAL_PROBE_UNAVAILABLE"
        )
    if predicate_id in {"P02", "P03"}:
        return tuple(code for code in facts.diagnostics if code in {"TARGET_CONTRACT_ABSENT", "UNKNOWN_CONTRACT_VERSION", "EVIDENCE_SCHEMA_INVALID"}) or ("TARGET_CONTRACT_ABSENT",)
    if predicate_id in {"P05", "P06", "P07"}:
        return ("TARGET_VALIDATOR_ABSENT",) if predicate_id == "P05" else ("TARGET_PRODUCER_ABSENT",)
    if predicate_id in {"P10", "P11", "P37"}:
        return tuple(code for code in facts.diagnostics if code in {"CURRENT_RUNTIME_DATA_INVALID", "RUNTIME_SLOT_NOT_CURRENT", "RUNTIME_DATA_INCOMPLETE"})
    if predicate_id in {"P12", "P13", "P14", "P15", "P16"}:
        return tuple(code for code in facts.diagnostics if code.startswith("POLICY_"))
    if predicate_id == "P28" and facts.direct_contradiction:
        return ("PROHIBITED_CONSUMER_PRESENT",)
    if predicate_id == "P34":
        return ("DEPENDENCY_NOT_PROVEN",)
    return ()


def _predicate_evidence_ids(predicate_id: str, facts: AdapterFacts) -> tuple[str, ...]:
    if predicate_id in {"P27", "P28"} and facts.inventory_complete:
        return ("repository_inventory:production",)
    if predicate_id in {"P10", "P11", "P35", "P37"}:
        return tuple(item for item in facts.evidence_ids if item.startswith("runtime_artifact:"))
    if predicate_id in {"P12", "P13", "P14", "P15", "P16"}:
        return tuple(item for item in facts.evidence_ids if item.startswith("policy_candidate:"))
    if predicate_id == "P17":
        return (*facts.evidence_ids, *facts.legacy_evidence_ids)
    return facts.evidence_ids or facts.supporting_evidence_ids


def _evaluate_predicates(check: CatalogCheck, facts: AdapterFacts, *, dependencies_proven: bool) -> tuple[PredicateOutcome, ...]:
    outcomes: list[PredicateOutcome] = []
    for predicate_id in check.required_proof_predicates:
        satisfied = _predicate_value(
            predicate_id,
            facts,
            dependencies_proven=dependencies_proven,
            check_id=check.check_id,
        )
        outcomes.append(
            PredicateOutcome(
                predicate_id=predicate_id,
                satisfied=satisfied,
                evidence_ids=_bounded_unique_strings(_predicate_evidence_ids(predicate_id, facts), maximum=16),
                diagnostic_codes=_bounded_unique_strings(_predicate_diagnostics(predicate_id, facts, satisfied), maximum=8),
            )
        )
    return tuple(outcomes)


def _validate_adapter_evidence_authority(
    check: CatalogCheck,
    facts: AdapterFacts,
    records: Mapping[str, EvidenceRecord],
) -> None:
    """Enforce catalog evidence-kind boundaries before predicates consume facts."""
    for evidence_id in (*facts.evidence_ids, *facts.legacy_evidence_ids, *facts.contradiction_evidence_ids):
        record = records.get(evidence_id)
        if record is None or record.evidence_kind not in check.allowed_evidence_kinds:
            raise ObserverIntegrityError("EVIDENCE_COLLECTION_FAILED")
    for evidence_id in facts.supporting_evidence_ids:
        record = records.get(evidence_id)
        if record is None or record.evidence_kind not in check.optional_supporting_evidence:
            raise ObserverIntegrityError("EVIDENCE_COLLECTION_FAILED")


def derive_prerequisite_observation(check: CatalogCheck, evidence: RepositoryEvidence) -> PrerequisiteObservation:
    """Collect adapter facts and execute every exact predicate required by *check*."""
    if type(check) is not CatalogCheck or type(evidence) is not RepositoryEvidence:
        raise ObserverIntegrityError("EVIDENCE_COLLECTION_FAILED")
    adapter = _ADAPTERS.get(check.check_id)
    if adapter is None:
        raise ObserverIntegrityError("OBSERVER_CATALOG_INVALID")
    records = _records_by_id(evidence)
    if adapter.kind is AdapterKind.NEGATIVE:
        facts = _facts_for_negative(adapter, evidence)
    elif adapter.kind is AdapterKind.WEEKLY:
        facts = _facts_for_weekly(check, adapter, evidence, records)
    elif adapter.kind is AdapterKind.POLICY:
        facts = _facts_for_policy(check, adapter, evidence, records)
    elif adapter.kind is AdapterKind.RUNTIME:
        facts = _facts_for_runtime(check, adapter, evidence, records)
    else:
        facts = _facts_for_contract(check, adapter, evidence, records)
    _validate_adapter_evidence_authority(check, facts, records)
    outcomes = _evaluate_predicates(check, facts, dependencies_proven=False)
    return PrerequisiteObservation(adapter, facts, outcomes)


def _allowed_reason_codes(check: CatalogCheck, status: ReadinessStatus) -> tuple[str, ...]:
    profile = REASON_CODES_BY_PROFILE.get(check.status_decision_table)
    if profile is None:
        raise ObserverIntegrityError("OBSERVER_CATALOG_INVALID")
    expected = profile.get(status)
    for status_value, codes in check.reason_codes_by_status:
        if status_value == status.value:
            if expected != codes:
                raise ObserverIntegrityError("OBSERVER_CATALOG_INVALID")
            return codes
    if expected is not None:
        raise ObserverIntegrityError("OBSERVER_CATALOG_INVALID")
    return ()


def _allowed_status(check: CatalogCheck, status: ReadinessStatus) -> bool:
    return bool(_allowed_reason_codes(check, status))


def _choose_reason(check: CatalogCheck, status: ReadinessStatus, preferred: Sequence[str]) -> tuple[str, ...]:
    allowed = _allowed_reason_codes(check, status)
    if not allowed:
        raise ObserverIntegrityError("OBSERVER_CATALOG_INVALID")
    selected = [code for code in preferred if code in allowed]
    return _bounded_unique_strings(selected or (allowed[0],), maximum=16)


def _select_status(check: CatalogCheck, observation: PrerequisiteObservation, *, dependencies_proven: bool) -> tuple[ReadinessStatus, tuple[str, ...], tuple[PredicateOutcome, ...]]:
    facts = observation.facts
    outcomes = _evaluate_predicates(check, facts, dependencies_proven=dependencies_proven)
    required_complete = all(outcome.satisfied for outcome in outcomes)
    if any(condition not in check.disqualifying_conditions for condition in facts.disqualifying_conditions):
        raise ObserverIntegrityError("OBSERVER_CATALOG_INVALID")
    disqualified = any(condition in check.disqualifying_conditions for condition in facts.disqualifying_conditions)
    if facts.direct_contradiction:
        preferred = (
            (facts.contradiction_reason,)
            if facts.contradiction_reason
            else ("VALID_EVIDENCE_CONFLICT",)
        )
        return (
            ReadinessStatus.CONTRADICTORY,
            _choose_reason(check, ReadinessStatus.CONTRADICTORY, preferred),
            outcomes,
        )
    # A frozen target contract with no production producer is a concrete
    # implementation gap even for proof profiles whose required predicate list
    # is intentionally validator-focused.  It must not be elevated by tests or
    # schema presence alone.
    if (
        observation.adapter.kind in {AdapterKind.CONTRACT, AdapterKind.RUNTIME}
        and facts.contract_frozen
        and not facts.producer_exists
    ):
        return ReadinessStatus.MISSING, _choose_reason(
            check,
            ReadinessStatus.MISSING,
            ("TARGET_IMPLEMENTATION_ABSENT",),
        ), outcomes
    if observation.adapter.kind is AdapterKind.NEGATIVE and not all(outcome.satisfied for outcome in outcomes):
        # A negative proof has only two reportable states: a complete absence
        # proof or a direct prohibited-consumer contradiction.  Any other
        # state means the prerequisite inventory fact was not safely collected.
        raise ObserverIntegrityError("EVIDENCE_COLLECTION_FAILED")
    if required_complete and not disqualified and dependencies_proven:
        return ReadinessStatus.PROVEN_PRESENT, _choose_reason(check, ReadinessStatus.PROVEN_PRESENT, ("COMPLETE_PROOF_SATISFIED",)), outcomes
    if required_complete and not dependencies_proven:
        return ReadinessStatus.PARTIAL, _choose_reason(check, ReadinessStatus.PARTIAL, ("DEPENDENCY_NOT_PROVEN",)), outcomes
    # This status records a valid, explicitly unaccepted machine candidate;
    # it is not a readiness proof.  A non-executing observer must not make
    # candidate lifecycle visibility depend on behavioral predicates that it
    # is intentionally unauthorized to establish.
    if (
        facts.policy_observation is PolicyObservation.UNACCEPTED
        and facts.policy_candidate_valid
        and facts.facets_complete
        and facts.policy_effective_activation_valid
        and facts.policy_consumers_compatible
        and dependencies_proven
        and not disqualified
    ):
        return ReadinessStatus.PRESENT_UNACCEPTED, _choose_reason(check, ReadinessStatus.PRESENT_UNACCEPTED, ("MACHINE_READABLE_CANDIDATE_NOT_ACCEPTED",)), outcomes
    if (
        observation.adapter.kind is AdapterKind.POLICY
        and (
            facts.policy_observation
            in {PolicyObservation.NONE, PolicyObservation.DRAFT, PolicyObservation.INVALID}
            or not facts.facets_complete
        )
    ):
        return ReadinessStatus.UNRESOLVED_OPERATOR_POLICY, _choose_reason(check, ReadinessStatus.UNRESOLVED_OPERATOR_POLICY, ("OPERATOR_POLICY_DECISION_REQUIRED",)), outcomes
    if (
        observation.adapter.kind is not AdapterKind.POLICY
        and facts.draft_only
        and _allowed_status(check, ReadinessStatus.DRAFT_ONLY)
    ):
        return ReadinessStatus.DRAFT_ONLY, _choose_reason(
            check,
            ReadinessStatus.DRAFT_ONLY,
            ("DRAFT_EVIDENCE_ONLY",),
        ), outcomes
    if observation.adapter.kind is AdapterKind.POLICY and facts.policy_candidate_valid:
        preferred = ["TARGET_PROOF_PARTIAL"]
        if not dependencies_proven:
            preferred.append("DEPENDENCY_NOT_PROVEN")
        return ReadinessStatus.PARTIAL, _choose_reason(check, ReadinessStatus.PARTIAL, preferred), outcomes
    if observation.adapter.kind is AdapterKind.RUNTIME:
        if facts.runtime_observation is RuntimeObservation.CONFLICT:
            return ReadinessStatus.CONTRADICTORY, _choose_reason(check, ReadinessStatus.CONTRADICTORY, ("CURRENT_RUNTIME_IDENTITY_CONFLICT",)), outcomes
        if not facts.contract_frozen:
            preferred = ["TARGET_CONTRACT_NOT_FROZEN"]
            if "UNKNOWN_CONTRACT_VERSION" in facts.diagnostics:
                preferred.insert(0, "UNKNOWN_CONTRACT_VERSION")
            return ReadinessStatus.UNRESOLVED_CONTRACT, _choose_reason(check, ReadinessStatus.UNRESOLVED_CONTRACT, preferred), outcomes
        if not facts.producer_exists:
            return ReadinessStatus.MISSING, _choose_reason(check, ReadinessStatus.MISSING, ("TARGET_IMPLEMENTATION_ABSENT",)), outcomes
        if facts.runtime_observation is RuntimeObservation.ABSENT:
            return ReadinessStatus.UNAVAILABLE_RUNTIME_DATA, _choose_reason(check, ReadinessStatus.UNAVAILABLE_RUNTIME_DATA, ("CURRENT_STRUCTURED_DATA_UNAVAILABLE",)), outcomes
        preferred = []
        if "CURRENT_RUNTIME_DATA_INVALID" in facts.diagnostics:
            preferred.append("CURRENT_RUNTIME_DATA_INVALID")
        if "RUNTIME_DATA_INCOMPLETE" in facts.diagnostics:
            preferred.append("RUNTIME_DATA_PARTIAL")
        if not dependencies_proven:
            preferred.append("DEPENDENCY_NOT_PROVEN")
        return ReadinessStatus.PARTIAL, _choose_reason(check, ReadinessStatus.PARTIAL, preferred or ("RUNTIME_DATA_PARTIAL",)), outcomes
    if not facts.contract_frozen:
        if facts.target_compatible_partial:
            preferred = ["TARGET_PROOF_PARTIAL"]
            if facts.legacy_semantic_mismatch:
                preferred.insert(0, "LEGACY_SEMANTICS_MISMATCH")
            if not dependencies_proven:
                preferred.append("DEPENDENCY_NOT_PROVEN")
            return ReadinessStatus.PARTIAL, _choose_reason(check, ReadinessStatus.PARTIAL, preferred), outcomes
        if _allowed_status(check, ReadinessStatus.UNRESOLVED_CONTRACT):
            preferred = ["TARGET_CONTRACT_NOT_FROZEN"]
            if "UNKNOWN_CONTRACT_VERSION" in facts.diagnostics:
                preferred.insert(0, "UNKNOWN_CONTRACT_VERSION")
            return ReadinessStatus.UNRESOLVED_CONTRACT, _choose_reason(check, ReadinessStatus.UNRESOLVED_CONTRACT, preferred), outcomes
    if facts.contract_frozen and not facts.producer_exists:
        return ReadinessStatus.MISSING, _choose_reason(check, ReadinessStatus.MISSING, ("TARGET_IMPLEMENTATION_ABSENT",)), outcomes
    if facts.target_compatible_partial or facts.evidence_ids or facts.supporting_evidence_ids:
        preferred = ["TARGET_PROOF_PARTIAL"]
        if facts.legacy_semantic_mismatch:
            preferred.append("LEGACY_SEMANTICS_MISMATCH")
        if not dependencies_proven:
            preferred.append("DEPENDENCY_NOT_PROVEN")
        return ReadinessStatus.PARTIAL, _choose_reason(check, ReadinessStatus.PARTIAL, preferred), outcomes
    if _allowed_status(check, ReadinessStatus.MISSING):
        return ReadinessStatus.MISSING, _choose_reason(check, ReadinessStatus.MISSING, ("TARGET_EVIDENCE_ABSENT", "TARGET_IMPLEMENTATION_ABSENT")), outcomes
    return ReadinessStatus.UNRESOLVED_CONTRACT, _choose_reason(check, ReadinessStatus.UNRESOLVED_CONTRACT, ("TARGET_CONTRACT_NOT_FROZEN",)), outcomes


def assess_check(
    check: CatalogCheck,
    observation: PrerequisiteObservation,
    dependency_statuses: Mapping[str, ReadinessStatus],
) -> CheckAssessment:
    """Select a status only after exact predicates, disqualifiers, and deps."""
    if type(check) is not CatalogCheck or type(observation) is not PrerequisiteObservation or type(dependency_statuses) is not dict:
        raise ObserverIntegrityError("OBSERVER_CATALOG_INVALID")
    dependencies_proven = True
    for dependency_id in check.dependency_check_ids:
        status = dependency_statuses.get(dependency_id)
        if type(status) is not ReadinessStatus:
            raise ObserverIntegrityError("OBSERVER_CATALOG_INVALID")
        if status is not ReadinessStatus.PROVEN_PRESENT:
            dependencies_proven = False
    status, reasons, outcomes = _select_status(check, observation, dependencies_proven=dependencies_proven)
    evidence_ids = _bounded_unique_strings(
        (
            *observation.facts.evidence_ids,
            *observation.facts.supporting_evidence_ids,
            *observation.facts.legacy_evidence_ids,
            *observation.facts.contradiction_evidence_ids,
        ),
        maximum=16,
    )
    return CheckAssessment(
        check=check,
        status=status,
        reason_codes=reasons,
        evidence_ids=evidence_ids,
        diagnostic_codes=_bounded_unique_strings(observation.facts.diagnostics, maximum=16),
        predicate_outcomes=outcomes,
    )


def assess_catalog(evidence: RepositoryEvidence) -> tuple[CheckAssessment, ...]:
    try:
        validate_catalog()
    except CatalogIntegrityError:
        raise ObserverIntegrityError("OBSERVER_CATALOG_INVALID") from None
    checks_by_id = {check.check_id: check for check in CATALOG}
    assessments: dict[str, CheckAssessment] = {}
    statuses: dict[str, ReadinessStatus] = {}
    visiting: set[str] = set()

    def visit(check_id: str) -> None:
        if check_id in assessments:
            return
        if check_id in visiting:
            raise ObserverIntegrityError("OBSERVER_CATALOG_INVALID")
        check = checks_by_id.get(check_id)
        if check is None:
            raise ObserverIntegrityError("OBSERVER_CATALOG_INVALID")
        visiting.add(check_id)
        for dependency in check.dependency_check_ids:
            visit(dependency)
        visiting.remove(check_id)
        assessment = assess_check(check, derive_prerequisite_observation(check, evidence), statuses)
        assessments[check_id] = assessment
        statuses[check_id] = assessment.status

    for check in CATALOG:
        visit(check.check_id)
    result = tuple(assessments[check.check_id] for check in CATALOG)
    if len(result) != 81:
        raise ObserverIntegrityError("OBSERVER_CATALOG_INVALID")
    return result


def _dimension_rollup(statuses: Iterable[ReadinessStatus]) -> ReadinessStatus:
    values = tuple(statuses)
    if not values or any(type(value) is not ReadinessStatus for value in values):
        raise ObserverIntegrityError("REPORT_RECORD_INVALID")
    for candidate in STATUS_ROLLUP_PRECEDENCE:
        if candidate in values:
            return candidate
    raise ObserverIntegrityError("REPORT_RECORD_INVALID")


def _evidence_record_to_dict(record: EvidenceRecord) -> dict[str, object]:
    return {
        "evidence_id": record.evidence_id,
        "evidence_kind": record.evidence_kind.value,
        "repository_relative_path": record.repository_relative_path,
        "locator_kind": record.locator_kind,
        "locator": record.locator,
        "content_identity_sha256": record.content_identity_sha256,
        "validation_state": record.validation_state.value,
        "current": record.current,
        "diagnostic_codes": list(record.diagnostic_codes),
    }


def _inventory_to_evidence_record(inventory: ProductionInventory) -> EvidenceRecord:
    payload = {
        "production_paths": list(inventory.production_paths),
        "entry_points": [{"name": name, "target": target} for name, target in inventory.entry_points],
        "imports_by_path": [{"path": path, "imports": list(imports)} for path, imports in inventory.imports_by_path],
        "dynamic_findings": list(inventory.dynamic_findings),
        "observer_external_consumers": list(inventory.observer_external_consumers),
        "report_artifact_readers": list(inventory.report_artifact_readers),
        "policy_artifact_consumers": list(inventory.policy_artifact_consumers),
        "prohibited_observer_capability_imports": list(inventory.prohibited_observer_capability_imports),
        "p4a_runtime_consumers": list(inventory.p4a_runtime_consumers),
        "broker_capability_imports": list(inventory.broker_capability_imports),
        "weekly_llm_invocation_markers": list(inventory.weekly_llm_invocation_markers),
    }
    return _record(
        "repository_inventory:production",
        EvidenceKind.REPOSITORY_INVENTORY,
        None,
        "ast_selector",
        "production_inventory",
        EvidenceValidationState.VALID,
        identity=_sha256_identity(INVENTORY_IDENTITY_DOMAIN, payload, maximum=MAX_EVIDENCE_CANONICAL_BYTES),
    )


def _predicate_outcome_to_dict(outcome: PredicateOutcome) -> dict[str, object]:
    return {
        "predicate_id": outcome.predicate_id,
        "satisfied": outcome.satisfied,
        "evidence_ids": list(outcome.evidence_ids),
        "diagnostic_codes": list(outcome.diagnostic_codes),
    }


def _check_to_report_dict(assessment: CheckAssessment) -> dict[str, object]:
    check = assessment.check
    if check.authority_effect is not AuthorityEffect.NONE:
        raise ObserverIntegrityError("REPORT_RECORD_INVALID")
    return {
        "check_id": check.check_id,
        "title": check.title,
        "contract_owner": check.contract_owner.value,
        "runtime_actor": check.runtime_actor.value,
        "authority_effect": check.authority_effect.value,
        "status": assessment.status.value,
        "reason_codes": list(assessment.reason_codes),
        "evidence_ids": list(assessment.evidence_ids),
        "diagnostic_codes": list(assessment.diagnostic_codes),
        "predicate_outcomes": [_predicate_outcome_to_dict(outcome) for outcome in assessment.predicate_outcomes],
        "blocker_type": check.blocker_type.value,
        "blocker_code": check.blocker_code,
        "dependency_check_ids": list(check.dependency_check_ids),
    }


def repository_evidence_identity_sha256(evidence: RepositoryEvidence) -> str:
    records = tuple(sorted(evidence.records, key=lambda item: item.evidence_id))
    payload = {
        "evidence": [_evidence_record_to_dict(record) for record in records],
        "inventory": _evidence_record_to_dict(_inventory_to_evidence_record(evidence.inventory)),
    }
    return _sha256_identity(EVIDENCE_IDENTITY_DOMAIN, payload, maximum=MAX_EVIDENCE_CANONICAL_BYTES)


def _validate_observer_inventory_isolation(inventory: ProductionInventory) -> None:
    expected_cli = (_OBSERVER_CLI_RELATIVE_PATH,)
    if (
        inventory.dynamic_findings
        or inventory.observer_external_consumers != expected_cli
        or inventory.report_artifact_readers
        or inventory.prohibited_observer_capability_imports
    ):
        raise ObserverIntegrityError("CONSUMER_INVENTORY_INCOMPLETE")


def build_gap_report(root: Path) -> dict[str, object]:
    """Build a complete, validated, no-authority report; never write it here."""
    try:
        validate_catalog()
    except CatalogIntegrityError:
        raise ObserverIntegrityError("OBSERVER_CATALOG_INVALID") from None
    evidence = collect_repository_evidence(root)
    _validate_observer_inventory_isolation(evidence.inventory)
    assessments = assess_catalog(evidence)
    dimensions: list[dict[str, object]] = []
    for dimension_id in (
        "operator_mandate", "evidence_and_grounding", "structured_portfolio_state",
        "llm_analyst_and_signal_contract", "portfolio_construction",
        "semantic_audit_and_resolution", "approval_order_and_migration_safety",
    ):
        selected = tuple(item for item in assessments if item.check.dimension_id == dimension_id)
        dimensions.append({
            "dimension_id": dimension_id,
            "status": _dimension_rollup(item.status for item in selected).value,
            "check_count": len(selected),
            "checks": [_check_to_report_dict(item) for item in selected],
        })
    counts = {status.value: 0 for status in ReadinessStatus}
    for assessment in assessments:
        counts[assessment.status.value] += 1
    counts["total_checks"] = 81
    report: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "observer_version": OBSERVER_VERSION,
        "architecture_version": ARCHITECTURE_VERSION,
        "prerequisite_catalog_version": CATALOG_VERSION,
        "prerequisite_catalog_identity_sha256": catalog_identity_sha256(),
        "evaluated_at_utc": None,
        "evaluation_time_source": EVALUATION_TIME_SOURCE,
        "repository_evidence_identity_sha256": repository_evidence_identity_sha256(evidence),
        "evidence": [_evidence_record_to_dict(record) for record in tuple(sorted(evidence.records, key=lambda item: item.evidence_id)) + (_inventory_to_evidence_record(evidence.inventory),)],
        "dimensions": dimensions,
        "summary_counts": counts,
        "diagnostics": [],
        "authority": dict(AUTHORITY_DECLARATION),
        "content_identity_sha256": None,
    }
    report["content_identity_sha256"] = report_content_identity_sha256(report)
    validate_gap_report_record(report, root=root)
    return report


def report_content_identity_sha256(report: Mapping[str, object]) -> str:
    if type(report) is not dict:
        raise ObserverIntegrityError("REPORT_RECORD_INVALID")
    payload = dict(report)
    identity = payload.pop("content_identity_sha256", None)
    if identity is not None and (type(identity) is not str or not _SHA256_RE.fullmatch(identity)):
        raise ObserverIntegrityError("REPORT_RECORD_INVALID")
    return _sha256_identity(REPORT_IDENTITY_DOMAIN, payload, maximum=MAX_REPORT_CANONICAL_BYTES)


def _schema_payload(root: Path) -> dict[str, object]:
    path = _safe_repository_file_path(root, REPORT_SCHEMA_RELATIVE_PATH)
    if path is None or not path.is_file() or path.is_symlink():
        raise ObserverIntegrityError("OBSERVER_SCHEMA_INVALID")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if type(payload) is not dict:
            raise ValueError
        Draft202012Validator.check_schema(payload)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError, SchemaError):
        raise ObserverIntegrityError("OBSERVER_SCHEMA_INVALID") from None
    return payload


def _default_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _validate_predicate_projection(item: Mapping[str, object], check: CatalogCheck, evidence_kinds: Mapping[str, EvidenceKind]) -> None:
    outcomes = item.get("predicate_outcomes")
    if type(outcomes) is not list or len(outcomes) != len(check.required_proof_predicates):
        raise ObserverIntegrityError("REPORT_RECORD_INVALID")
    if tuple(outcome.get("predicate_id") if type(outcome) is dict else None for outcome in outcomes) != check.required_proof_predicates:
        raise ObserverIntegrityError("REPORT_RECORD_INVALID")
    for outcome in outcomes:
        if type(outcome) is not dict or type(outcome.get("satisfied")) is not bool:
            raise ObserverIntegrityError("REPORT_RECORD_INVALID")
        evidence_ids = outcome.get("evidence_ids")
        diagnostic_codes = outcome.get("diagnostic_codes")
        predicate_id = outcome.get("predicate_id")
        if type(evidence_ids) is not list or type(diagnostic_codes) is not list or type(predicate_id) is not str:
            raise ObserverIntegrityError("REPORT_RECORD_INVALID")
        if len(evidence_ids) > 16 or len(set(evidence_ids)) != len(evidence_ids) or len(diagnostic_codes) > 8 or len(set(diagnostic_codes)) != len(diagnostic_codes):
            raise ObserverIntegrityError("REPORT_RECORD_INVALID")
        if any(type(code) is not str or code not in DIAGNOSTIC_CODES for code in diagnostic_codes):
            raise ObserverIntegrityError("REPORT_RECORD_INVALID")
        for evidence_id in evidence_ids:
            kind = evidence_kinds.get(evidence_id) if type(evidence_id) is str else None
            if kind is None:
                raise ObserverIntegrityError("REPORT_RECORD_INVALID")
            if kind not in check.allowed_evidence_kinds and not (predicate_id in {"P27", "P28"} and kind is EvidenceKind.REPOSITORY_INVENTORY):
                raise ObserverIntegrityError("REPORT_RECORD_INVALID")


def _validate_report_catalog_projection(report: Mapping[str, object]) -> None:
    dimensions = report.get("dimensions")
    counts = report.get("summary_counts")
    evidence = report.get("evidence")
    if type(dimensions) is not list or type(counts) is not dict or type(evidence) is not list:
        raise ObserverIntegrityError("REPORT_RECORD_INVALID")
    expected_dimensions = (
        "operator_mandate", "evidence_and_grounding", "structured_portfolio_state",
        "llm_analyst_and_signal_contract", "portfolio_construction",
        "semantic_audit_and_resolution", "approval_order_and_migration_safety",
    )
    if tuple(dimension.get("dimension_id") if type(dimension) is dict else None for dimension in dimensions) != expected_dimensions:
        raise ObserverIntegrityError("REPORT_RECORD_INVALID")
    evidence_kinds: dict[str, EvidenceKind] = {}
    for record in evidence:
        if type(record) is not dict or type(record.get("evidence_id")) is not str or type(record.get("evidence_kind")) is not str:
            raise ObserverIntegrityError("REPORT_RECORD_INVALID")
        if record.get("repository_relative_path") is not None and not _is_normalized_relative_path(record.get("repository_relative_path")):
            raise ObserverIntegrityError("REPORT_RECORD_INVALID")
        try:
            evidence_kinds[record["evidence_id"]] = EvidenceKind(record["evidence_kind"])
        except ValueError:
            raise ObserverIntegrityError("REPORT_RECORD_INVALID") from None
    checks: list[dict[str, object]] = []
    for dimension, dimension_id in zip(dimensions, expected_dimensions, strict=True):
        if type(dimension) is not dict:
            raise ObserverIntegrityError("REPORT_RECORD_INVALID")
        expected = tuple(check for check in CATALOG if check.dimension_id == dimension_id)
        reported = dimension.get("checks")
        if dimension.get("check_count") != len(expected) or type(reported) is not list or len(reported) != len(expected):
            raise ObserverIntegrityError("REPORT_RECORD_INVALID")
        if tuple(item.get("check_id") if type(item) is dict else None for item in reported) != tuple(check.check_id for check in expected):
            raise ObserverIntegrityError("REPORT_RECORD_INVALID")
        for item, check in zip(reported, expected, strict=True):
            if type(item) is not dict:
                raise ObserverIntegrityError("REPORT_RECORD_INVALID")
            expected_values = {
                "title": check.title,
                "contract_owner": check.contract_owner.value,
                "runtime_actor": check.runtime_actor.value,
                "authority_effect": check.authority_effect.value,
                "blocker_type": check.blocker_type.value,
                "blocker_code": check.blocker_code,
                "dependency_check_ids": list(check.dependency_check_ids),
            }
            if any(item.get(key) != value for key, value in expected_values.items()):
                raise ObserverIntegrityError("REPORT_RECORD_INVALID")
            try:
                status = ReadinessStatus(item.get("status"))
            except (TypeError, ValueError):
                raise ObserverIntegrityError("REPORT_RECORD_INVALID") from None
            reasons = item.get("reason_codes")
            evidence_ids = item.get("evidence_ids")
            diagnostics = item.get("diagnostic_codes")
            if type(reasons) is not list or not reasons or type(evidence_ids) is not list or type(diagnostics) is not list:
                raise ObserverIntegrityError("REPORT_RECORD_INVALID")
            allowed_reasons = set(_allowed_reason_codes(check, status))
            if any(type(code) is not str or code not in allowed_reasons for code in reasons) or len(set(reasons)) != len(reasons):
                raise ObserverIntegrityError("REPORT_RECORD_INVALID")
            if any(type(code) is not str or code not in DIAGNOSTIC_CODES for code in diagnostics) or len(set(diagnostics)) != len(diagnostics):
                raise ObserverIntegrityError("REPORT_RECORD_INVALID")
            if any(type(evidence_id) is not str or evidence_id not in evidence_kinds for evidence_id in evidence_ids) or len(set(evidence_ids)) != len(evidence_ids):
                raise ObserverIntegrityError("REPORT_RECORD_INVALID")
            if any(evidence_kinds[evidence_id] not in check.allowed_evidence_kinds for evidence_id in evidence_ids):
                raise ObserverIntegrityError("REPORT_RECORD_INVALID")
            _validate_predicate_projection(item, check, evidence_kinds)
            checks.append(item)
        if dimension.get("status") != _dimension_rollup(ReadinessStatus(item["status"]) for item in reported).value:
            raise ObserverIntegrityError("REPORT_RECORD_INVALID")
    if len(checks) != 81 or len({item["check_id"] for item in checks}) != 81:
        raise ObserverIntegrityError("REPORT_RECORD_INVALID")
    actual = {status.value: 0 for status in ReadinessStatus}
    for item in checks:
        actual[item["status"]] += 1
    expected_counts = {**actual, "total_checks": 81}
    if (
        counts != expected_counts
        or counts.get("total_checks") != 81
        or sum(actual.values()) != counts["total_checks"]
    ):
        raise ObserverIntegrityError("REPORT_RECORD_INVALID")


def _validate_reported_evidence_identity(report: Mapping[str, object]) -> None:
    evidence = report.get("evidence")
    identity = report.get("repository_evidence_identity_sha256")
    if type(evidence) is not list or type(identity) is not str or not _SHA256_RE.fullmatch(identity):
        raise ObserverIntegrityError("REPORT_RECORD_INVALID")
    inventory = [item for item in evidence if type(item) is dict and item.get("evidence_id") == "repository_inventory:production"]
    if len(inventory) != 1 or any(type(item) is not dict for item in evidence):
        raise ObserverIntegrityError("REPORT_RECORD_INVALID")
    records = [item for item in evidence if item is not inventory[0]]
    ids = [item.get("evidence_id") for item in records]
    if any(type(item) is not str for item in ids) or len(set(ids)) != len(ids) or ids != sorted(ids):
        raise ObserverIntegrityError("REPORT_RECORD_INVALID")
    payload = {"evidence": records, "inventory": inventory[0]}
    if identity != _sha256_identity(EVIDENCE_IDENTITY_DOMAIN, payload, maximum=MAX_EVIDENCE_CANONICAL_BYTES):
        raise ObserverIntegrityError("IDENTITY_COMPUTATION_FAILED")


def validate_gap_report_record(report: Mapping[str, object], *, root: Path | None = None) -> None:
    if type(report) is not dict:
        raise ObserverIntegrityError("REPORT_RECORD_INVALID")
    checked_root = _safe_root(_default_root() if root is None else root)
    schema = _schema_payload(checked_root)
    errors = tuple(Draft202012Validator(schema).iter_errors(report))
    if errors:
        raise ObserverIntegrityError("OBSERVER_SCHEMA_INVALID")
    identity = report.get("content_identity_sha256")
    if type(identity) is not str or not _SHA256_RE.fullmatch(identity):
        raise ObserverIntegrityError("REPORT_RECORD_INVALID")
    if identity != report_content_identity_sha256(report):
        raise ObserverIntegrityError("IDENTITY_COMPUTATION_FAILED")
    if report.get("prerequisite_catalog_version") != CATALOG_VERSION or report.get("prerequisite_catalog_identity_sha256") != catalog_identity_sha256():
        raise ObserverIntegrityError("OBSERVER_CATALOG_INVALID")
    if report.get("authority") != AUTHORITY_DECLARATION:
        raise ObserverIntegrityError("REPORT_RECORD_INVALID")
    _validate_report_catalog_projection(report)
    _validate_reported_evidence_identity(report)


def canonical_gap_report_bytes(report: Mapping[str, object], *, root: Path | None = None) -> bytes:
    validate_gap_report_record(report, root=root)
    return _canonical_json_bytes(report, maximum=MAX_REPORT_CANONICAL_BYTES)


def report_output_path(root: Path, content_identity_sha256: str) -> Path:
    checked_root = _safe_root(root)
    if type(content_identity_sha256) is not str or not _SHA256_RE.fullmatch(content_identity_sha256):
        raise ObserverIntegrityError("REPORT_RECORD_INVALID")
    return checked_root / REPORT_NAMESPACE_RELATIVE_PATH / f"{content_identity_sha256}.json"


def _ensure_safe_output_directory(root: Path, output_path: Path) -> None:
    try:
        parent = output_path.parent.relative_to(root)
    except ValueError:
        raise ObserverIntegrityError("EVIDENCE_COLLECTION_FAILED") from None
    cursor = root
    for part in parent.parts:
        cursor = cursor / part
        if cursor.is_symlink():
            raise ObserverIntegrityError("EVIDENCE_COLLECTION_FAILED")
        try:
            cursor.mkdir(exist_ok=True)
        except OSError:
            raise ObserverIntegrityError("EVIDENCE_COLLECTION_FAILED") from None
        if cursor.is_symlink() or not cursor.is_dir():
            raise ObserverIntegrityError("EVIDENCE_COLLECTION_FAILED")


def write_gap_report(root: Path, report: Mapping[str, object]) -> Path:
    """Write/reuse one immutable content-addressed report without overwrite."""
    validate_gap_report_record(report, root=root)
    identity = report.get("content_identity_sha256")
    if type(identity) is not str:
        raise ObserverIntegrityError("REPORT_RECORD_INVALID")
    output_path = report_output_path(root, identity)
    content = canonical_gap_report_bytes(report, root=root)
    checked_root = _safe_root(root)
    _ensure_safe_output_directory(checked_root, output_path)
    if output_path.exists():
        if output_path.is_symlink() or not output_path.is_file():
            raise ObserverIntegrityError("REPORT_OUTPUT_CONFLICT")
        try:
            if output_path.read_bytes() == content:
                return output_path
        except OSError:
            raise ObserverIntegrityError("EVIDENCE_COLLECTION_FAILED") from None
        raise ObserverIntegrityError("REPORT_OUTPUT_CONFLICT")
    temporary = output_path.with_name(f".{output_path.name}.tmp.{os.getpid()}")
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, output_path)
        except FileExistsError:
            if output_path.is_symlink() or not output_path.is_file() or output_path.read_bytes() != content:
                raise ObserverIntegrityError("REPORT_OUTPUT_CONFLICT") from None
        finally:
            temporary.unlink(missing_ok=True)
    except ObserverIntegrityError:
        raise
    except OSError:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise ObserverIntegrityError("EVIDENCE_COLLECTION_FAILED") from None
    return output_path


def build_and_write_gap_report(root: Path) -> Path:
    return write_gap_report(root, build_gap_report(root))


__all__ = (
    "ARCHITECTURE_VERSION",
    "AUTHORITY_DECLARATION",
    "AdapterFacts",
    "AdapterKind",
    "CheckAssessment",
    "EVALUATION_TIME_SOURCE",
    "EvidenceAdapter",
    "EvidenceRecord",
    "EvidenceValidationState",
    "MAX_REPORT_CANONICAL_BYTES",
    "OBSERVER_VERSION",
    "ObserverIntegrityError",
    "PolicyObservation",
    "PredicateOutcome",
    "PrerequisiteObservation",
    "ProductionInventory",
    "REPORT_NAMESPACE_RELATIVE_PATH",
    "REPORT_SCHEMA_RELATIVE_PATH",
    "RepositoryEvidence",
    "RuntimeObservation",
    "SCHEMA_VERSION",
    "assess_catalog",
    "assess_check",
    "build_and_write_gap_report",
    "build_gap_report",
    "canonical_gap_report_bytes",
    "collect_repository_evidence",
    "derive_prerequisite_observation",
    "report_content_identity_sha256",
    "report_output_path",
    "repository_evidence_identity_sha256",
    "validate_gap_report_record",
    "write_gap_report",
)
