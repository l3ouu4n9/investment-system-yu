"""Pure decoded-object validation for Step 2 source-policy approvals.

This module validates one already-decoded approval object.  It deliberately
does not parse raw artifact bytes, authenticate an operator, activate or
select a policy, resolve source roles, evaluate freshness, affect a workflow,
publish an artifact, compile an order, or grant trading authority.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import hashlib
import re
from typing import Any


APPROVAL_SCHEMA_VERSION = "step2_market_source_policy_approvals_v1"
APPROVAL_VALIDATION_RESULT_VERSION = (
    "step2_market_source_policy_approval_object_validation_result_v1"
)
APPROVAL_SCHEMA_FILENAME = "step2_market_source_policy_approvals.schema.json"

MAX_SOURCE_RECORDS = 64
MAX_ALIASES_PER_SOURCE = 32
MAX_PERMISSION_TUPLES_PER_SOURCE = 32
MAX_TOTAL_ALIASES = 512
MAX_TOTAL_PERMISSION_TUPLES = 512
MAX_STRING_LENGTH = 512
MAX_JSON_NESTING_DEPTH = 8
MAX_JSON_NODE_COUNT = 4096
MAX_CANONICAL_APPROVAL_CONTENT_BYTES = 262_144
MAX_APPROVAL_CONTENT_DIAGNOSTICS = 14

AUTHORITY_SCOPE = "approval_object_validation_only"
NOT_TRADE_AUTHORIZATION = True
TRADE_PERMISSION_EFFECT = "none"
SOURCE_RESOLUTION_PERFORMED = False
FRESHNESS_EVALUATION_PERFORMED = False
UNIVERSE_RESOLUTION_PERFORMED = False
CANDIDATE_VALIDITY_EVALUATED = False
PUBLICATION_EVALUATION_PERFORMED = False
WORKFLOW_PERMISSION_EVALUATED = False
ORDER_COMPILATION_EVALUATED = False
OPERATOR_AUTHENTICATION_PERFORMED = False
RAW_ARTIFACT_PARSING_PERFORMED = False
ACTIVATION_EVALUATION_PERFORMED = False

VALIDATION_BOOLEAN_COERCION_ERROR = (
    "inspect approval_object_valid explicitly; Step 2 source-policy "
    "approval object results have no truth value"
)

SOURCE_ROLES = (
    "LAST_CLOSE_VALUE",
    "PRICE_SESSION",
    "TECHNICAL_METRICS",
)

_RESERVED_VERSION_TOKENS = frozenset(
    {"latest", "current", "default", "*"}
)
_ID_RE = re.compile(r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$")
_VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,63}$")
_ALIAS_RE = re.compile(r"^[!-~](?:[ -~]{0,62}[!-~])?$")
_REASON_RE = re.compile(r"^[!-~](?:[ -~]{0,510}[!-~])?$")
_TIMESTAMP_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$",
    re.ASCII,
)
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_CONTENT_TYPE_RE = re.compile(
    r"^[a-z0-9][a-z0-9!#$&^_.+-]{0,62}/"
    r"[a-z0-9][a-z0-9!#$&^_.+-]{0,62}$"
)
_HASH_DOMAIN = b"step2_market_source_policy_approvals_v1\0"

_ROOT_FIELDS = (
    "schema_version",
    "operator_approved_source_policy_sha256",
    "approval_content",
)
_CONTENT_FIELDS = (
    "policy_version",
    "supersedes_policy_version",
    "policy_change_reason",
    "approved_by",
    "approved_at_utc",
    "sources",
)
_SOURCE_FIELDS = (
    "canonical_source_id",
    "source_version",
    "exact_aliases",
    "permissions",
    "approval_reason",
)
_PERMISSION_FIELDS = (
    "source_role",
    "content_type",
    "capture_adapter_id",
    "capture_adapter_version",
)

_STEP2_MARKET_SOURCE_POLICY_APPROVALS_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": (
        "https://investment-system.local/schemas/"
        "step2_market_source_policy_approvals.schema.json"
    ),
    "title": "Step 2 Market Source Policy Operator Approvals v1",
    "description": (
        "Closed decoded-object contract for operator approval claims. "
        "Object validity does not establish raw-artifact validity, operator "
        "authentication, policy activation, source-role eligibility, "
        "workflow permission, trading permission, publication eligibility, "
        "order compilation, or execution authority."
    ),
    "type": "object",
    "required": list(_ROOT_FIELDS),
    "properties": {
        "schema_version": {"const": APPROVAL_SCHEMA_VERSION},
        "operator_approved_source_policy_sha256": {"type": "string"},
        "approval_content": {"$ref": "#/$defs/approval_content"},
    },
    "additionalProperties": False,
    "$defs": {
        "approval_content": {
            "type": "object",
            "required": list(_CONTENT_FIELDS),
            "properties": {
                "policy_version": {"type": "string"},
                "supersedes_policy_version": {
                    "anyOf": [{"type": "string"}, {"type": "null"}]
                },
                "policy_change_reason": {"type": "string"},
                "approved_by": {"type": "string"},
                "approved_at_utc": {"type": "string"},
                "sources": {
                    "type": "array",
                    "minItems": 0,
                    "maxItems": MAX_SOURCE_RECORDS,
                    "items": {"$ref": "#/$defs/source"},
                },
            },
            "additionalProperties": False,
        },
        "source": {
            "type": "object",
            "required": list(_SOURCE_FIELDS),
            "properties": {
                "canonical_source_id": {"type": "string"},
                "source_version": {"type": "string"},
                "exact_aliases": {
                    "type": "array",
                    "minItems": 0,
                    "maxItems": MAX_ALIASES_PER_SOURCE,
                    "items": {"type": "string"},
                },
                "permissions": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": MAX_PERMISSION_TUPLES_PER_SOURCE,
                    "items": {"$ref": "#/$defs/permission"},
                },
                "approval_reason": {"type": "string"},
            },
            "additionalProperties": False,
        },
        "permission": {
            "type": "object",
            "required": list(_PERMISSION_FIELDS),
            "properties": {
                "source_role": {"type": "string"},
                "content_type": {"type": "string"},
                "capture_adapter_id": {"type": "string"},
                "capture_adapter_version": {"type": "string"},
            },
            "additionalProperties": False,
        },
    },
}


class Step2MarketSourcePolicyApprovalDiagnostic(str, Enum):
    APPROVAL_INPUT_MISSING = "approval_input_missing"
    APPROVAL_INPUT_INVALID = "approval_input_invalid"
    APPROVAL_SCHEMA_VERSION_UNSUPPORTED = (
        "approval_schema_version_unsupported"
    )
    APPROVAL_DECLARED_IDENTITY_INVALID = (
        "approval_declared_identity_invalid"
    )
    APPROVAL_POLICY_VERSION_INVALID = "approval_policy_version_invalid"
    APPROVAL_PROVENANCE_INVALID = "approval_provenance_invalid"
    CANONICAL_SOURCE_ID_INVALID = "canonical_source_id_invalid"
    SOURCE_VERSION_INVALID = "source_version_invalid"
    DUPLICATE_CANONICAL_SOURCE_ID = "duplicate_canonical_source_id"
    ALIAS_INVALID = "alias_invalid"
    DUPLICATE_ALIAS = "duplicate_alias"
    ALIAS_CANONICAL_COLLISION = "alias_canonical_collision"
    UNKNOWN_SOURCE_ROLE = "unknown_source_role"
    PERMISSION_FIELD_INVALID = "permission_field_invalid"
    IMPLICIT_OR_WILDCARD_PERMISSION = (
        "implicit_or_wildcard_permission"
    )
    DUPLICATE_PERMISSION_TUPLE = "duplicate_permission_tuple"
    APPROVAL_IDENTITY_MISMATCH = "approval_identity_mismatch"


class Step2MarketSourcePolicyApprovalObjectState(str, Enum):
    INPUT_ABSENT = "input_absent"
    STRUCTURALLY_INVALID = "structurally_invalid"
    SEMANTICALLY_INVALID = "semantically_invalid"
    VALID_EMPTY = "valid_empty"
    VALID_NONEMPTY = "valid_nonempty"


@dataclass(frozen=True, slots=True)
class Step2MarketSourcePolicyApprovalsObjectValidationResult:
    """Immutable decoded-object result with no activation or trade effect."""

    approval_schema_version: str | None
    approval_policy_version: str | None
    declared_operator_approved_source_policy_sha256: str | None
    canonical_approval_content_sha256: str | None
    approval_identity_matches: bool | None
    object_validation_performed: bool
    object_structure_valid: bool
    semantic_validation_performed: bool
    approval_object_valid: bool | None
    approval_state: Step2MarketSourcePolicyApprovalObjectState
    source_count: int | None
    diagnostics: tuple[Step2MarketSourcePolicyApprovalDiagnostic, ...]
    result_version: str = field(
        default=APPROVAL_VALIDATION_RESULT_VERSION,
        init=False,
    )
    authority_scope: str = field(default=AUTHORITY_SCOPE, init=False)
    not_trade_authorization: bool = field(
        default=NOT_TRADE_AUTHORIZATION,
        init=False,
    )
    trade_permission_effect: str = field(
        default=TRADE_PERMISSION_EFFECT,
        init=False,
    )
    source_resolution_performed: bool = field(
        default=SOURCE_RESOLUTION_PERFORMED,
        init=False,
    )
    freshness_evaluation_performed: bool = field(
        default=FRESHNESS_EVALUATION_PERFORMED,
        init=False,
    )
    universe_resolution_performed: bool = field(
        default=UNIVERSE_RESOLUTION_PERFORMED,
        init=False,
    )
    candidate_validity_evaluated: bool = field(
        default=CANDIDATE_VALIDITY_EVALUATED,
        init=False,
    )
    publication_evaluation_performed: bool = field(
        default=PUBLICATION_EVALUATION_PERFORMED,
        init=False,
    )
    workflow_permission_evaluated: bool = field(
        default=WORKFLOW_PERMISSION_EVALUATED,
        init=False,
    )
    order_compilation_evaluated: bool = field(
        default=ORDER_COMPILATION_EVALUATED,
        init=False,
    )
    operator_authentication_performed: bool = field(
        default=OPERATOR_AUTHENTICATION_PERFORMED,
        init=False,
    )
    raw_artifact_parsing_performed: bool = field(
        default=RAW_ARTIFACT_PARSING_PERFORMED,
        init=False,
    )
    activation_evaluation_performed: bool = field(
        default=ACTIVATION_EVALUATION_PERFORMED,
        init=False,
    )

    def __bool__(self) -> bool:
        raise TypeError(VALIDATION_BOOLEAN_COERCION_ERROR)


class _SnapshotFailure(str, Enum):
    UNSUPPORTED_EXACT_TYPE = "unsupported_exact_type"
    NON_STRING_MAPPING_KEY = "non_string_mapping_key"
    DEPTH_LIMIT_EXCEEDED = "depth_limit_exceeded"
    NODE_LIMIT_EXCEEDED = "node_limit_exceeded"
    CYCLE_DETECTED = "cycle_detected"
    MUTATION_DETECTED = "mutation_detected"


@dataclass(frozen=True, slots=True)
class _SnapshotOutcome:
    snapshot: Any | None
    failure: _SnapshotFailure | None


@dataclass(frozen=True, slots=True)
class _CanonicalOutcome:
    canonical_bytes: bytes | None
    size_exceeded: bool


_D = Step2MarketSourcePolicyApprovalDiagnostic


def validate_step2_market_source_policy_approvals_object(
    value: object,
) -> Step2MarketSourcePolicyApprovalsObjectValidationResult:
    """Validate one decoded approval object without granting authority."""
    if value is None:
        return _input_absent_result()

    capture = _capture_snapshot(value)
    if capture.failure is not None:
        return _structural_failure(_D.APPROVAL_INPUT_INVALID)

    snapshot = capture.snapshot
    structural_diagnostic = _structural_diagnostic(snapshot)
    if structural_diagnostic is not None:
        return _structural_failure(structural_diagnostic)
    if type(snapshot) is not dict:
        raise AssertionError

    approval_content = snapshot["approval_content"]
    if type(approval_content) is not dict:
        raise AssertionError
    canonical = _bounded_canonical_json_bytes(approval_content)
    if canonical.size_exceeded:
        return _structural_failure(_D.APPROVAL_INPUT_INVALID)
    canonical_bytes = canonical.canonical_bytes
    if type(canonical_bytes) is not bytes:
        raise AssertionError

    findings, policy_version_valid, declared_hash_valid = (
        _evaluate_approval_content(snapshot)
    )
    canonical_hash = hashlib.sha256(
        _HASH_DOMAIN + canonical_bytes
    ).hexdigest()
    declared_hash = snapshot["operator_approved_source_policy_sha256"]
    identity_matches: bool | None = None
    if declared_hash_valid:
        identity_matches = declared_hash == canonical_hash
        if not identity_matches:
            findings.add(_D.APPROVAL_IDENTITY_MISMATCH)

    diagnostics = _ordered_content_diagnostics(findings)
    sources = approval_content["sources"]
    if type(sources) is not list:
        raise AssertionError
    if diagnostics:
        return Step2MarketSourcePolicyApprovalsObjectValidationResult(
            approval_schema_version=APPROVAL_SCHEMA_VERSION,
            approval_policy_version=(
                approval_content["policy_version"]
                if policy_version_valid
                else None
            ),
            declared_operator_approved_source_policy_sha256=(
                declared_hash if declared_hash_valid else None
            ),
            canonical_approval_content_sha256=canonical_hash,
            approval_identity_matches=identity_matches,
            object_validation_performed=True,
            object_structure_valid=True,
            semantic_validation_performed=True,
            approval_object_valid=False,
            approval_state=(
                Step2MarketSourcePolicyApprovalObjectState.SEMANTICALLY_INVALID
            ),
            source_count=len(sources),
            diagnostics=diagnostics,
        )

    state = (
        Step2MarketSourcePolicyApprovalObjectState.VALID_EMPTY
        if not sources
        else Step2MarketSourcePolicyApprovalObjectState.VALID_NONEMPTY
    )
    return Step2MarketSourcePolicyApprovalsObjectValidationResult(
        approval_schema_version=APPROVAL_SCHEMA_VERSION,
        approval_policy_version=approval_content["policy_version"],
        declared_operator_approved_source_policy_sha256=declared_hash,
        canonical_approval_content_sha256=canonical_hash,
        approval_identity_matches=True,
        object_validation_performed=True,
        object_structure_valid=True,
        semantic_validation_performed=True,
        approval_object_valid=True,
        approval_state=state,
        source_count=len(sources),
        diagnostics=(),
    )


def _input_absent_result(
) -> Step2MarketSourcePolicyApprovalsObjectValidationResult:
    return Step2MarketSourcePolicyApprovalsObjectValidationResult(
        approval_schema_version=None,
        approval_policy_version=None,
        declared_operator_approved_source_policy_sha256=None,
        canonical_approval_content_sha256=None,
        approval_identity_matches=None,
        object_validation_performed=False,
        object_structure_valid=False,
        semantic_validation_performed=False,
        approval_object_valid=None,
        approval_state=Step2MarketSourcePolicyApprovalObjectState.INPUT_ABSENT,
        source_count=None,
        diagnostics=(_D.APPROVAL_INPUT_MISSING,),
    )


def _structural_failure(
    diagnostic: Step2MarketSourcePolicyApprovalDiagnostic,
) -> Step2MarketSourcePolicyApprovalsObjectValidationResult:
    return Step2MarketSourcePolicyApprovalsObjectValidationResult(
        approval_schema_version=None,
        approval_policy_version=None,
        declared_operator_approved_source_policy_sha256=None,
        canonical_approval_content_sha256=None,
        approval_identity_matches=None,
        object_validation_performed=True,
        object_structure_valid=False,
        semantic_validation_performed=False,
        approval_object_valid=None,
        approval_state=(
            Step2MarketSourcePolicyApprovalObjectState.STRUCTURALLY_INVALID
        ),
        source_count=None,
        diagnostics=(diagnostic,),
    )


def _capture_snapshot(value: Any) -> _SnapshotOutcome:
    """Copy exact dict/list/string/null values under fixed iterative bounds."""
    root: list[Any] = [None]
    active_container_ids: set[int] = set()
    completed_container_signatures: dict[int, tuple[Any, ...]] = {}
    node_count = 0
    stack: list[tuple[str, Any, Any, Any, int, Any]] = [
        ("visit", value, root, 0, 0, None)
    ]

    while stack:
        operation, source, destination, slot, depth, entry = stack.pop()
        if operation == "leave":
            current, failure = _stable_shallow_copy(source)
            if failure is not None:
                return _snapshot_failure(failure)
            if current is None:
                raise AssertionError
            current_signature = _shallow_container_signature(current)
            if entry != current_signature:
                return _snapshot_failure(_SnapshotFailure.MUTATION_DETECTED)
            source_id = id(source)
            completed_signature = completed_container_signatures.get(source_id)
            if completed_signature is None:
                completed_container_signatures[source_id] = current_signature
            elif completed_signature != current_signature:
                return _snapshot_failure(_SnapshotFailure.MUTATION_DETECTED)
            active_container_ids.remove(id(source))
            continue

        node_count += 1
        if depth > MAX_JSON_NESTING_DEPTH:
            return _snapshot_failure(_SnapshotFailure.DEPTH_LIMIT_EXCEEDED)
        if node_count > MAX_JSON_NODE_COUNT:
            return _snapshot_failure(_SnapshotFailure.NODE_LIMIT_EXCEEDED)

        if source is None or type(source) is str:
            destination[slot] = source
            continue
        if type(source) not in {dict, list}:
            return _snapshot_failure(_SnapshotFailure.UNSUPPORTED_EXACT_TYPE)

        source_id = id(source)
        if source_id in active_container_ids:
            return _snapshot_failure(_SnapshotFailure.CYCLE_DETECTED)
        if node_count + len(source) > MAX_JSON_NODE_COUNT:
            return _snapshot_failure(_SnapshotFailure.NODE_LIMIT_EXCEEDED)

        shallow, failure = _stable_shallow_copy(source)
        if failure is not None:
            return _snapshot_failure(failure)
        if shallow is None:
            raise AssertionError
        entry_signature = _shallow_container_signature(shallow)
        completed_signature = completed_container_signatures.get(source_id)
        if (
            completed_signature is not None
            and completed_signature != entry_signature
        ):
            return _snapshot_failure(_SnapshotFailure.MUTATION_DETECTED)
        active_container_ids.add(source_id)
        stack.append(("leave", source, None, None, depth, entry_signature))

        if type(source) is dict:
            snapshot_object: dict[str, Any] = {}
            destination[slot] = snapshot_object
            for key in reversed(list(shallow)):
                stack.append(
                    (
                        "visit",
                        shallow[key],
                        snapshot_object,
                        key,
                        depth + 1,
                        None,
                    )
                )
            continue

        snapshot_array: list[Any] = [None] * len(shallow)
        destination[slot] = snapshot_array
        for index in range(len(shallow) - 1, -1, -1):
            stack.append(
                (
                    "visit",
                    shallow[index],
                    snapshot_array,
                    index,
                    depth + 1,
                    None,
                )
            )

    return _SnapshotOutcome(snapshot=root[0], failure=None)


def _stable_shallow_copy(
    source: dict[Any, Any] | list[Any],
) -> tuple[dict[str, Any] | list[Any] | None, _SnapshotFailure | None]:
    if type(source) is dict:
        first = source.copy()
        if any(type(key) is not str for key in first):
            return None, _SnapshotFailure.NON_STRING_MAPPING_KEY
        second = source.copy()
        if any(type(key) is not str for key in second):
            return None, _SnapshotFailure.NON_STRING_MAPPING_KEY
        if not _same_shallow_container(first, second):
            return None, _SnapshotFailure.MUTATION_DETECTED
        return first, None

    first_list = source.copy()
    second_list = source.copy()
    if not _same_shallow_container(first_list, second_list):
        return None, _SnapshotFailure.MUTATION_DETECTED
    return first_list, None


def _same_shallow_container(left: Any, right: Any) -> bool:
    if type(left) is dict and type(right) is dict:
        left_keys = tuple(left)
        right_keys = tuple(right)
        return left_keys == right_keys and all(
            left[key] is right[key] for key in left_keys
        )
    if type(left) is list and type(right) is list:
        return len(left) == len(right) and all(
            left_item is right_item
            for left_item, right_item in zip(left, right, strict=True)
        )
    return False


def _shallow_container_signature(value: Any) -> tuple[Any, ...]:
    """Return an immutable shallow signature without retaining containers."""
    if type(value) is dict:
        return (
            "dict",
            tuple(
                (key, _shallow_child_token(value[key]))
                for key in value
            ),
        )
    if type(value) is list:
        return (
            "list",
            tuple(_shallow_child_token(child) for child in value),
        )
    raise AssertionError


def _shallow_child_token(value: Any) -> tuple[Any, ...]:
    if value is None:
        return ("none",)
    if type(value) is str:
        return ("str", value)
    if type(value) is dict:
        return ("dict", id(value))
    if type(value) is list:
        return ("list", id(value))
    return ("unsupported", id(value))


def _snapshot_failure(failure: _SnapshotFailure) -> _SnapshotOutcome:
    return _SnapshotOutcome(snapshot=None, failure=failure)


def _structural_diagnostic(
    value: Any,
) -> Step2MarketSourcePolicyApprovalDiagnostic | None:
    if type(value) is not dict:
        return _D.APPROVAL_INPUT_INVALID
    root_keys = set(value)
    expected_root = set(_ROOT_FIELDS)
    if root_keys - expected_root:
        return _D.APPROVAL_INPUT_INVALID
    if (
        "operator_approved_source_policy_sha256" not in value
        or "approval_content" not in value
        or type(value.get("approval_content")) is not dict
    ):
        return _D.APPROVAL_INPUT_INVALID
    if (
        "schema_version" not in value
        or type(value["schema_version"]) is not str
        or value["schema_version"] != APPROVAL_SCHEMA_VERSION
    ):
        return _D.APPROVAL_SCHEMA_VERSION_UNSUPPORTED

    findings: set[Step2MarketSourcePolicyApprovalDiagnostic] = set()
    if type(value["operator_approved_source_policy_sha256"]) is not str:
        findings.add(_D.APPROVAL_DECLARED_IDENTITY_INVALID)

    content = value["approval_content"]
    content_keys = set(content)
    if content_keys - set(_CONTENT_FIELDS):
        return _D.APPROVAL_INPUT_INVALID
    if "sources" not in content or type(content.get("sources")) is not list:
        return _D.APPROVAL_INPUT_INVALID
    _record_required_string_structure(
        content,
        "policy_version",
        _D.APPROVAL_POLICY_VERSION_INVALID,
        findings,
    )
    if (
        "supersedes_policy_version" not in content
        or (
            content["supersedes_policy_version"] is not None
            and type(content["supersedes_policy_version"]) is not str
        )
    ):
        findings.add(_D.APPROVAL_POLICY_VERSION_INVALID)
    for field_name in (
        "policy_change_reason",
        "approved_by",
        "approved_at_utc",
    ):
        _record_required_string_structure(
            content,
            field_name,
            _D.APPROVAL_PROVENANCE_INVALID,
            findings,
        )

    sources = content["sources"]
    if len(sources) > MAX_SOURCE_RECORDS:
        return _D.APPROVAL_INPUT_INVALID
    for source in sources:
        if type(source) is not dict:
            return _D.APPROVAL_INPUT_INVALID
        if set(source) - set(_SOURCE_FIELDS):
            return _D.APPROVAL_INPUT_INVALID
        _record_required_string_structure(
            source,
            "canonical_source_id",
            _D.CANONICAL_SOURCE_ID_INVALID,
            findings,
        )
        _record_required_string_structure(
            source,
            "source_version",
            _D.SOURCE_VERSION_INVALID,
            findings,
        )
        _record_required_string_structure(
            source,
            "approval_reason",
            _D.APPROVAL_PROVENANCE_INVALID,
            findings,
        )
        aliases = source.get("exact_aliases")
        if (
            "exact_aliases" not in source
            or type(aliases) is not list
            or len(aliases) > MAX_ALIASES_PER_SOURCE
            or any(type(alias) is not str for alias in aliases)
        ):
            findings.add(_D.ALIAS_INVALID)
        permissions = source.get("permissions")
        if (
            "permissions" not in source
            or type(permissions) is not list
            or not permissions
            or len(permissions) > MAX_PERMISSION_TUPLES_PER_SOURCE
        ):
            findings.add(_D.PERMISSION_FIELD_INVALID)
            continue
        for permission in permissions:
            if type(permission) is not dict:
                findings.add(_D.PERMISSION_FIELD_INVALID)
                continue
            if set(permission) - set(_PERMISSION_FIELDS):
                return _D.APPROVAL_INPUT_INVALID
            if any(
                field_name not in permission
                or type(permission[field_name]) is not str
                for field_name in _PERMISSION_FIELDS
            ):
                findings.add(_D.PERMISSION_FIELD_INVALID)

    if findings:
        return next(
            diagnostic
            for diagnostic in Step2MarketSourcePolicyApprovalDiagnostic
            if diagnostic in findings
        )
    return None


def _record_required_string_structure(
    mapping: dict[str, Any],
    field_name: str,
    diagnostic: Step2MarketSourcePolicyApprovalDiagnostic,
    findings: set[Step2MarketSourcePolicyApprovalDiagnostic],
) -> None:
    if field_name not in mapping or type(mapping[field_name]) is not str:
        findings.add(diagnostic)


def _bounded_canonical_json_bytes(value: Any) -> _CanonicalOutcome:
    """Encode canonical ensure-ASCII JSON without exceeding the byte cap."""
    output = bytearray()
    stack: list[tuple[str, Any]] = [("value", value)]

    while stack:
        operation, current = stack.pop()
        if operation == "raw":
            if not _append_bounded(output, current):
                return _CanonicalOutcome(None, True)
            continue

        if current is None:
            if not _append_bounded(output, b"null"):
                return _CanonicalOutcome(None, True)
            continue
        if type(current) is str:
            if not _append_canonical_string(output, current):
                return _CanonicalOutcome(None, True)
            continue
        if type(current) is list:
            if not _append_bounded(output, b"["):
                return _CanonicalOutcome(None, True)
            stack.append(("raw", b"]"))
            for index in range(len(current) - 1, -1, -1):
                stack.append(("value", current[index]))
                if index != 0:
                    stack.append(("raw", b","))
            continue
        if type(current) is dict:
            if not _append_bounded(output, b"{"):
                return _CanonicalOutcome(None, True)
            stack.append(("raw", b"}"))
            keys = sorted(current)
            for index in range(len(keys) - 1, -1, -1):
                key = keys[index]
                if type(key) is not str:
                    raise AssertionError
                stack.append(("value", current[key]))
                stack.append(("raw", b":"))
                stack.append(("value", key))
                if index != 0:
                    stack.append(("raw", b","))
            continue
        raise AssertionError

    return _CanonicalOutcome(bytes(output), False)


def _append_bounded(output: bytearray, fragment: bytes) -> bool:
    if len(output) + len(fragment) > MAX_CANONICAL_APPROVAL_CONTENT_BYTES:
        return False
    output.extend(fragment)
    return True


def _append_canonical_string(output: bytearray, value: str) -> bool:
    if not _append_bounded(output, b'"'):
        return False
    escapes = {
        0x08: b"\\b",
        0x09: b"\\t",
        0x0A: b"\\n",
        0x0C: b"\\f",
        0x0D: b"\\r",
        0x22: b'\\"',
        0x5C: b"\\\\",
    }
    for character in value:
        codepoint = ord(character)
        if codepoint in escapes:
            fragment = escapes[codepoint]
        elif 0x20 <= codepoint <= 0x7E:
            fragment = bytes((codepoint,))
        elif codepoint <= 0xFFFF:
            fragment = f"\\u{codepoint:04x}".encode("ascii")
        else:
            adjusted = codepoint - 0x10000
            high = 0xD800 + (adjusted >> 10)
            low = 0xDC00 + (adjusted & 0x3FF)
            fragment = f"\\u{high:04x}\\u{low:04x}".encode("ascii")
        if not _append_bounded(output, fragment):
            return False
    return _append_bounded(output, b'"')


def _evaluate_approval_content(
    approval: dict[str, Any],
) -> tuple[set[Step2MarketSourcePolicyApprovalDiagnostic], bool, bool]:
    findings: set[Step2MarketSourcePolicyApprovalDiagnostic] = set()
    content = approval["approval_content"]
    declared_hash = approval["operator_approved_source_policy_sha256"]

    declared_hash_valid = _HASH_RE.fullmatch(declared_hash) is not None
    if not declared_hash_valid:
        findings.add(_D.APPROVAL_DECLARED_IDENTITY_INVALID)

    policy_version = content["policy_version"]
    policy_version_valid = _valid_policy_version(policy_version)
    supersedes = content["supersedes_policy_version"]
    supersedes_valid = (
        supersedes is None or _valid_policy_version(supersedes)
    )
    if (
        not policy_version_valid
        or not supersedes_valid
        or (supersedes is not None and supersedes == policy_version)
    ):
        findings.add(_D.APPROVAL_POLICY_VERSION_INVALID)

    if (
        _REASON_RE.fullmatch(content["policy_change_reason"]) is None
        or not _valid_id(content["approved_by"])
        or not _valid_canonical_utc_timestamp(content["approved_at_utc"])
    ):
        findings.add(_D.APPROVAL_PROVENANCE_INVALID)

    sources = content["sources"]
    total_aliases = sum(len(source["exact_aliases"]) for source in sources)
    total_permissions = sum(len(source["permissions"]) for source in sources)
    aliases_within_total_bound = total_aliases <= MAX_TOTAL_ALIASES
    permissions_within_total_bound = (
        total_permissions <= MAX_TOTAL_PERMISSION_TUPLES
    )
    if not aliases_within_total_bound:
        findings.add(_D.ALIAS_INVALID)
    if not permissions_within_total_bound:
        findings.add(_D.PERMISSION_FIELD_INVALID)

    valid_canonical_ids: list[str] = []
    valid_aliases: list[str] = []
    for source in sources:
        canonical_id = source["canonical_source_id"]
        if not _valid_id(canonical_id):
            findings.add(_D.CANONICAL_SOURCE_ID_INVALID)
        else:
            valid_canonical_ids.append(canonical_id)

        if not _valid_source_version(source["source_version"]):
            findings.add(_D.SOURCE_VERSION_INVALID)
        if _REASON_RE.fullmatch(source["approval_reason"]) is None:
            findings.add(_D.APPROVAL_PROVENANCE_INVALID)

        for alias in source["exact_aliases"]:
            if _ALIAS_RE.fullmatch(alias) is None:
                findings.add(_D.ALIAS_INVALID)
            else:
                valid_aliases.append(alias)

        valid_permission_tuples: set[tuple[str, str, str, str]] = set()
        for permission in source["permissions"]:
            permission_tuple = _valid_permission_tuple(permission, findings)
            if permission_tuple is None or not permissions_within_total_bound:
                continue
            if permission_tuple in valid_permission_tuples:
                findings.add(_D.DUPLICATE_PERMISSION_TUPLE)
            valid_permission_tuples.add(permission_tuple)

    if len(valid_canonical_ids) != len(set(valid_canonical_ids)):
        findings.add(_D.DUPLICATE_CANONICAL_SOURCE_ID)

    if aliases_within_total_bound:
        folded_aliases = [alias.lower() for alias in valid_aliases]
        if len(folded_aliases) != len(set(folded_aliases)):
            findings.add(_D.DUPLICATE_ALIAS)
        canonical_id_set = set(valid_canonical_ids)
        if any(alias.lower() in canonical_id_set for alias in valid_aliases):
            findings.add(_D.ALIAS_CANONICAL_COLLISION)

    return findings, policy_version_valid, declared_hash_valid


def _valid_policy_version(value: str) -> bool:
    return (
        _valid_id(value)
        and value.lower() not in _RESERVED_VERSION_TOKENS
    )


def _valid_source_version(value: str) -> bool:
    return (
        _VERSION_RE.fullmatch(value) is not None
        and value.lower() not in _RESERVED_VERSION_TOKENS
    )


def _valid_id(value: str) -> bool:
    return len(value) <= 64 and _ID_RE.fullmatch(value) is not None


def _valid_permission_tuple(
    permission: dict[str, str],
    findings: set[Step2MarketSourcePolicyApprovalDiagnostic],
) -> tuple[str, str, str, str] | None:
    role = permission["source_role"]
    content_type = permission["content_type"]
    adapter_id = permission["capture_adapter_id"]
    adapter_version = permission["capture_adapter_version"]
    values = (role, content_type, adapter_id, adapter_version)

    if any("*" in value for value in values) or (
        adapter_version.lower() in _RESERVED_VERSION_TOKENS
    ):
        findings.add(_D.IMPLICIT_OR_WILDCARD_PERMISSION)
        return None
    if any(len(value) > MAX_STRING_LENGTH for value in values):
        findings.add(_D.PERMISSION_FIELD_INVALID)
        return None
    if role not in SOURCE_ROLES:
        findings.add(_D.UNKNOWN_SOURCE_ROLE)
        return None
    if (
        _CONTENT_TYPE_RE.fullmatch(content_type) is None
        or not _valid_id(adapter_id)
        or _VERSION_RE.fullmatch(adapter_version) is None
    ):
        findings.add(_D.PERMISSION_FIELD_INVALID)
        return None
    return values


def _valid_canonical_utc_timestamp(value: str) -> bool:
    if _TIMESTAMP_RE.fullmatch(value) is None:
        return False
    year = int(value[0:4])
    month = int(value[5:7])
    day = int(value[8:10])
    hour = int(value[11:13])
    minute = int(value[14:16])
    second = int(value[17:19])
    if year == 0 or month < 1 or month > 12:
        return False
    days_by_month = (31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)
    days = days_by_month[month - 1]
    if month == 2 and (
        year % 400 == 0 or (year % 4 == 0 and year % 100 != 0)
    ):
        days = 29
    return (
        1 <= day <= days
        and 0 <= hour <= 23
        and 0 <= minute <= 59
        and 0 <= second <= 59
    )


def _ordered_content_diagnostics(
    findings: set[Step2MarketSourcePolicyApprovalDiagnostic],
) -> tuple[Step2MarketSourcePolicyApprovalDiagnostic, ...]:
    ordered = tuple(
        diagnostic
        for diagnostic in Step2MarketSourcePolicyApprovalDiagnostic
        if diagnostic in findings
    )
    if any(diagnostic.value.startswith("approval_input_") for diagnostic in ordered):
        raise AssertionError
    if _D.APPROVAL_SCHEMA_VERSION_UNSUPPORTED in ordered:
        raise AssertionError
    if len(ordered) > MAX_APPROVAL_CONTENT_DIAGNOSTICS:
        raise AssertionError
    return ordered


__all__ = [
    "ACTIVATION_EVALUATION_PERFORMED",
    "APPROVAL_SCHEMA_FILENAME",
    "APPROVAL_SCHEMA_VERSION",
    "APPROVAL_VALIDATION_RESULT_VERSION",
    "AUTHORITY_SCOPE",
    "CANDIDATE_VALIDITY_EVALUATED",
    "FRESHNESS_EVALUATION_PERFORMED",
    "MAX_ALIASES_PER_SOURCE",
    "MAX_APPROVAL_CONTENT_DIAGNOSTICS",
    "MAX_CANONICAL_APPROVAL_CONTENT_BYTES",
    "MAX_JSON_NESTING_DEPTH",
    "MAX_JSON_NODE_COUNT",
    "MAX_PERMISSION_TUPLES_PER_SOURCE",
    "MAX_SOURCE_RECORDS",
    "MAX_STRING_LENGTH",
    "MAX_TOTAL_ALIASES",
    "MAX_TOTAL_PERMISSION_TUPLES",
    "NOT_TRADE_AUTHORIZATION",
    "OPERATOR_AUTHENTICATION_PERFORMED",
    "ORDER_COMPILATION_EVALUATED",
    "PUBLICATION_EVALUATION_PERFORMED",
    "RAW_ARTIFACT_PARSING_PERFORMED",
    "SOURCE_RESOLUTION_PERFORMED",
    "SOURCE_ROLES",
    "TRADE_PERMISSION_EFFECT",
    "UNIVERSE_RESOLUTION_PERFORMED",
    "VALIDATION_BOOLEAN_COERCION_ERROR",
    "WORKFLOW_PERMISSION_EVALUATED",
    "Step2MarketSourcePolicyApprovalDiagnostic",
    "Step2MarketSourcePolicyApprovalObjectState",
    "Step2MarketSourcePolicyApprovalsObjectValidationResult",
    "validate_step2_market_source_policy_approvals_object",
]
