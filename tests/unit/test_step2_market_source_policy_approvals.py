from __future__ import annotations

import ast
from copy import deepcopy
from dataclasses import FrozenInstanceError, fields
import hashlib
import json
from pathlib import Path
import sys
from typing import Any, Callable

from jsonschema import Draft202012Validator
import pytest

from investment_orchestrator.validators import (
    validate_step2_market_source_policy_approvals as approval_contract,
)
from investment_orchestrator.validators.validate_step2_market_source_policy_approvals import (
    ACTIVATION_EVALUATION_PERFORMED,
    APPROVAL_SCHEMA_FILENAME,
    APPROVAL_SCHEMA_VERSION,
    APPROVAL_VALIDATION_RESULT_VERSION,
    AUTHORITY_SCOPE,
    CANDIDATE_VALIDITY_EVALUATED,
    FRESHNESS_EVALUATION_PERFORMED,
    MAX_ALIASES_PER_SOURCE,
    MAX_APPROVAL_CONTENT_DIAGNOSTICS,
    MAX_CANONICAL_APPROVAL_CONTENT_BYTES,
    MAX_JSON_NESTING_DEPTH,
    MAX_JSON_NODE_COUNT,
    MAX_PERMISSION_TUPLES_PER_SOURCE,
    MAX_SOURCE_RECORDS,
    MAX_STRING_LENGTH,
    MAX_TOTAL_ALIASES,
    MAX_TOTAL_PERMISSION_TUPLES,
    NOT_TRADE_AUTHORIZATION,
    OPERATOR_AUTHENTICATION_PERFORMED,
    ORDER_COMPILATION_EVALUATED,
    PUBLICATION_EVALUATION_PERFORMED,
    RAW_ARTIFACT_PARSING_PERFORMED,
    SOURCE_RESOLUTION_PERFORMED,
    SOURCE_ROLES,
    TRADE_PERMISSION_EFFECT,
    UNIVERSE_RESOLUTION_PERFORMED,
    VALIDATION_BOOLEAN_COERCION_ERROR,
    WORKFLOW_PERMISSION_EVALUATED,
    Step2MarketSourcePolicyApprovalDiagnostic,
    Step2MarketSourcePolicyApprovalObjectState,
    Step2MarketSourcePolicyApprovalsObjectValidationResult,
    validate_step2_market_source_policy_approvals_object,
)


Diagnostic = Step2MarketSourcePolicyApprovalDiagnostic
State = Step2MarketSourcePolicyApprovalObjectState
_DOMAIN = b"step2_market_source_policy_approvals_v1\0"


def _permission(
    *,
    role: str = "LAST_CLOSE_VALUE",
    content_type: str = "application/json",
    adapter_id: str = "capture.http",
    adapter_version: str = "v1",
) -> dict[str, str]:
    return {
        "source_role": role,
        "content_type": content_type,
        "capture_adapter_id": adapter_id,
        "capture_adapter_version": adapter_version,
    }


def _source(
    source_id: str = "primary.market",
    *,
    source_version: str = "v1",
    aliases: list[str] | None = None,
    permissions: list[dict[str, str]] | None = None,
    approval_reason: str = "Approved for deterministic capture testing.",
) -> dict[str, Any]:
    return {
        "canonical_source_id": source_id,
        "source_version": source_version,
        "exact_aliases": ["Primary Market"] if aliases is None else aliases,
        "permissions": [_permission()] if permissions is None else permissions,
        "approval_reason": approval_reason,
    }


def _approval_content(
    *,
    sources: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "policy_version": "policy_v1",
        "supersedes_policy_version": None,
        "policy_change_reason": "Initial decoded-object contract fixture.",
        "approved_by": "operator.primary",
        "approved_at_utc": "2026-07-15T12:34:56Z",
        "sources": [] if sources is None else sources,
    }


def _canonical_oracle(content: dict[str, Any]) -> bytes:
    return json.dumps(
        content,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")


def _content_hash(content: dict[str, Any]) -> str:
    return hashlib.sha256(_DOMAIN + _canonical_oracle(content)).hexdigest()


def _approval(
    *,
    sources: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    content = _approval_content(sources=sources)
    return {
        "schema_version": APPROVAL_SCHEMA_VERSION,
        "operator_approved_source_policy_sha256": _content_hash(content),
        "approval_content": content,
    }


def _rehash(value: dict[str, Any]) -> dict[str, Any]:
    value["operator_approved_source_policy_sha256"] = _content_hash(
        value["approval_content"]
    )
    return value


def _validate(
    value: object,
) -> Step2MarketSourcePolicyApprovalsObjectValidationResult:
    return validate_step2_market_source_policy_approvals_object(value)


def _assert_fixed_markers(
    result: Step2MarketSourcePolicyApprovalsObjectValidationResult,
) -> None:
    assert result.result_version == APPROVAL_VALIDATION_RESULT_VERSION
    assert result.authority_scope == AUTHORITY_SCOPE == "approval_object_validation_only"
    assert result.not_trade_authorization is NOT_TRADE_AUTHORIZATION is True
    assert result.trade_permission_effect == TRADE_PERMISSION_EFFECT == "none"
    assert result.source_resolution_performed is SOURCE_RESOLUTION_PERFORMED is False
    assert (
        result.freshness_evaluation_performed
        is FRESHNESS_EVALUATION_PERFORMED
        is False
    )
    assert (
        result.universe_resolution_performed
        is UNIVERSE_RESOLUTION_PERFORMED
        is False
    )
    assert (
        result.candidate_validity_evaluated
        is CANDIDATE_VALIDITY_EVALUATED
        is False
    )
    assert (
        result.publication_evaluation_performed
        is PUBLICATION_EVALUATION_PERFORMED
        is False
    )
    assert (
        result.workflow_permission_evaluated
        is WORKFLOW_PERMISSION_EVALUATED
        is False
    )
    assert (
        result.order_compilation_evaluated
        is ORDER_COMPILATION_EVALUATED
        is False
    )
    assert (
        result.operator_authentication_performed
        is OPERATOR_AUTHENTICATION_PERFORMED
        is False
    )
    assert (
        result.raw_artifact_parsing_performed
        is RAW_ARTIFACT_PARSING_PERFORMED
        is False
    )
    assert (
        result.activation_evaluation_performed
        is ACTIVATION_EVALUATION_PERFORMED
        is False
    )
    assert not hasattr(result, "not_authorization")


def _assert_structural_failure(
    value: object,
    diagnostic: Diagnostic,
) -> Step2MarketSourcePolicyApprovalsObjectValidationResult:
    result = _validate(value)
    assert result.approval_schema_version is None
    assert result.approval_policy_version is None
    assert result.declared_operator_approved_source_policy_sha256 is None
    assert result.canonical_approval_content_sha256 is None
    assert result.approval_identity_matches is None
    assert result.object_validation_performed is True
    assert result.object_structure_valid is False
    assert result.semantic_validation_performed is False
    assert result.approval_object_valid is None
    assert result.approval_state is State.STRUCTURALLY_INVALID
    assert result.source_count is None
    assert result.diagnostics == (diagnostic,)
    _assert_fixed_markers(result)
    return result


def _assert_content_failure(
    value: dict[str, Any],
    *diagnostics: Diagnostic,
    rehash: bool = True,
) -> Step2MarketSourcePolicyApprovalsObjectValidationResult:
    if rehash:
        _rehash(value)
    result = _validate(value)
    assert result.approval_schema_version == APPROVAL_SCHEMA_VERSION
    assert result.canonical_approval_content_sha256 == _content_hash(
        value["approval_content"]
    )
    assert result.object_validation_performed is True
    assert result.object_structure_valid is True
    assert result.semantic_validation_performed is True
    assert result.approval_object_valid is False
    assert result.approval_state is State.SEMANTICALLY_INVALID
    assert result.source_count == len(value["approval_content"]["sources"])
    assert result.diagnostics == diagnostics
    _assert_fixed_markers(result)
    return result


def _assert_valid(
    value: dict[str, Any],
) -> Step2MarketSourcePolicyApprovalsObjectValidationResult:
    _rehash(value)
    result = _validate(value)
    assert result.approval_schema_version == APPROVAL_SCHEMA_VERSION
    assert result.approval_policy_version == value["approval_content"]["policy_version"]
    assert (
        result.declared_operator_approved_source_policy_sha256
        == value["operator_approved_source_policy_sha256"]
    )
    assert result.canonical_approval_content_sha256 == _content_hash(
        value["approval_content"]
    )
    assert result.approval_identity_matches is True
    assert result.object_validation_performed is True
    assert result.object_structure_valid is True
    assert result.semantic_validation_performed is True
    assert result.approval_object_valid is True
    assert result.source_count == len(value["approval_content"]["sources"])
    assert result.approval_state is (
        State.VALID_EMPTY if result.source_count == 0 else State.VALID_NONEMPTY
    )
    assert result.diagnostics == ()
    _assert_fixed_markers(result)
    return result


def _schema_from_file() -> dict[str, Any]:
    root = Path(__file__).resolve().parents[2]
    return json.loads(
        (root / "schemas" / APPROVAL_SCHEMA_FILENAME).read_text(encoding="utf-8")
    )


def test_schema_is_closed_draft_2020_12_and_matches_production_mirror() -> None:
    schema = _schema_from_file()
    Draft202012Validator.check_schema(schema)
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema == approval_contract._STEP2_MARKET_SOURCE_POLICY_APPROVALS_SCHEMA
    assert schema["additionalProperties"] is False
    assert schema["$defs"]["approval_content"]["additionalProperties"] is False
    assert schema["$defs"]["source"]["additionalProperties"] is False
    assert schema["$defs"]["permission"]["additionalProperties"] is False


def test_schema_owns_only_shape_nullability_and_per_array_cardinality() -> None:
    schema = _schema_from_file()
    serialized = json.dumps(schema, sort_keys=True)
    assert '"pattern"' not in serialized
    assert '"maxLength"' not in serialized
    assert '"minLength"' not in serialized
    assert '"uniqueItems"' not in serialized
    assert '"enum"' not in serialized
    assert schema["$defs"]["approval_content"]["properties"]["sources"] == {
        "type": "array",
        "minItems": 0,
        "maxItems": MAX_SOURCE_RECORDS,
        "items": {"$ref": "#/$defs/source"},
    }
    assert schema["$defs"]["source"]["properties"]["exact_aliases"][
        "maxItems"
    ] == MAX_ALIASES_PER_SOURCE
    assert schema["$defs"]["source"]["properties"]["permissions"][
        "maxItems"
    ] == MAX_PERMISSION_TUPLES_PER_SOURCE


def test_schema_acceptance_is_only_a_prerequisite_to_complete_object_validation() -> None:
    value = _approval()
    value["approval_content"]["policy_version"] = "latest"
    assert Draft202012Validator(_schema_from_file()).is_valid(value)
    _assert_content_failure(value, Diagnostic.APPROVAL_POLICY_VERSION_INVALID)


def test_empty_and_nonempty_valid_results_are_exact() -> None:
    empty = _assert_valid(_approval())
    nonempty = _assert_valid(_approval(sources=[_source()]))
    assert empty.approval_state is State.VALID_EMPTY
    assert empty.source_count == 0
    assert nonempty.approval_state is State.VALID_NONEMPTY
    assert nonempty.source_count == 1


def test_input_absent_result_is_exact() -> None:
    result = _validate(None)
    assert result.approval_schema_version is None
    assert result.approval_policy_version is None
    assert result.declared_operator_approved_source_policy_sha256 is None
    assert result.canonical_approval_content_sha256 is None
    assert result.approval_identity_matches is None
    assert result.object_validation_performed is False
    assert result.object_structure_valid is False
    assert result.semantic_validation_performed is False
    assert result.approval_object_valid is None
    assert result.approval_state is State.INPUT_ABSENT
    assert result.source_count is None
    assert result.diagnostics == (Diagnostic.APPROVAL_INPUT_MISSING,)
    _assert_fixed_markers(result)


def test_result_fields_are_exact_frozen_slotted_and_bounded() -> None:
    assert tuple(field.name for field in fields(
        Step2MarketSourcePolicyApprovalsObjectValidationResult
    )) == (
        "approval_schema_version",
        "approval_policy_version",
        "declared_operator_approved_source_policy_sha256",
        "canonical_approval_content_sha256",
        "approval_identity_matches",
        "object_validation_performed",
        "object_structure_valid",
        "semantic_validation_performed",
        "approval_object_valid",
        "approval_state",
        "source_count",
        "diagnostics",
        "result_version",
        "authority_scope",
        "not_trade_authorization",
        "trade_permission_effect",
        "source_resolution_performed",
        "freshness_evaluation_performed",
        "universe_resolution_performed",
        "candidate_validity_evaluated",
        "publication_evaluation_performed",
        "workflow_permission_evaluated",
        "order_compilation_evaluated",
        "operator_authentication_performed",
        "raw_artifact_parsing_performed",
        "activation_evaluation_performed",
    )
    for result in (
        _validate(None),
        _validate({}),
        _assert_content_failure(
            {
                **_approval(),
                "operator_approved_source_policy_sha256": "G" * 64,
            },
            Diagnostic.APPROVAL_DECLARED_IDENTITY_INVALID,
            rehash=False,
        ),
        _assert_valid(_approval()),
        _assert_valid(_approval(sources=[_source()])),
    ):
        with pytest.raises(TypeError) as exc_info:
            bool(result)
        assert str(exc_info.value) == VALIDATION_BOOLEAN_COERCION_ERROR
        with pytest.raises((FrozenInstanceError, AttributeError)):
            result.approval_object_valid = True  # type: ignore[misc]
        assert not hasattr(result, "__dict__")


def test_diagnostic_enum_and_content_bound_are_exact() -> None:
    assert tuple(Step2MarketSourcePolicyApprovalDiagnostic) == (
        Diagnostic.APPROVAL_INPUT_MISSING,
        Diagnostic.APPROVAL_INPUT_INVALID,
        Diagnostic.APPROVAL_SCHEMA_VERSION_UNSUPPORTED,
        Diagnostic.APPROVAL_DECLARED_IDENTITY_INVALID,
        Diagnostic.APPROVAL_POLICY_VERSION_INVALID,
        Diagnostic.APPROVAL_PROVENANCE_INVALID,
        Diagnostic.CANONICAL_SOURCE_ID_INVALID,
        Diagnostic.SOURCE_VERSION_INVALID,
        Diagnostic.DUPLICATE_CANONICAL_SOURCE_ID,
        Diagnostic.ALIAS_INVALID,
        Diagnostic.DUPLICATE_ALIAS,
        Diagnostic.ALIAS_CANONICAL_COLLISION,
        Diagnostic.UNKNOWN_SOURCE_ROLE,
        Diagnostic.PERMISSION_FIELD_INVALID,
        Diagnostic.IMPLICIT_OR_WILDCARD_PERMISSION,
        Diagnostic.DUPLICATE_PERMISSION_TUPLE,
        Diagnostic.APPROVAL_IDENTITY_MISMATCH,
    )
    assert len(Step2MarketSourcePolicyApprovalDiagnostic) == 17
    assert MAX_APPROVAL_CONTENT_DIAGNOSTICS == 14
    assert not hasattr(approval_contract, "MAX_APPROVAL_SEMANTIC_DIAGNOSTICS")


@pytest.mark.parametrize(
    "value",
    [
        {},
        [],
        "approval",
        True,
        1,
        1.0,
        (),
        set(),
        object(),
    ],
)
def test_gross_or_unsupported_input_is_structurally_invalid(value: object) -> None:
    _assert_structural_failure(value, Diagnostic.APPROVAL_INPUT_INVALID)


def test_snapshot_rejects_non_string_key_and_exact_builtin_subclasses() -> None:
    _assert_structural_failure({1: None}, Diagnostic.APPROVAL_INPUT_INVALID)

    class DictSubclass(dict[str, Any]):
        pass

    _assert_structural_failure(DictSubclass(), Diagnostic.APPROVAL_INPUT_INVALID)


def test_snapshot_copies_noncyclic_aliases_independently() -> None:
    shared = ["value", None]
    source = {"first": shared, "second": shared}
    outcome = approval_contract._capture_snapshot(source)
    assert outcome.failure is None
    assert outcome.snapshot == source
    assert outcome.snapshot is not source
    assert outcome.snapshot["first"] is not shared
    assert outcome.snapshot["second"] is not shared
    assert outcome.snapshot["first"] is not outcome.snapshot["second"]
    shared.append("late")
    assert outcome.snapshot == {"first": ["value", None], "second": ["value", None]}

    approval_aliases: list[str] = []
    approval = _approval(
        sources=[
            _source("source.one", aliases=approval_aliases),
            _source("source.two", aliases=approval_aliases),
        ]
    )
    _assert_valid(approval)


def test_nested_shared_alias_mutation_fails_closed_through_public_api() -> None:
    shared_aliases: list[str] = []
    value = _approval(
        sources=[
            _source("source.one", aliases=shared_aliases),
            _source("source.two", aliases=shared_aliases),
        ]
    )
    mutation_performed = False

    def mutate_between_completed_visits(frame: Any, event: str, argument: Any) -> Any:
        del argument
        nonlocal mutation_performed
        if (
            frame.f_code is approval_contract._capture_snapshot.__code__
            and event == "line"
            and not mutation_performed
            and frame.f_locals.get("operation") == "leave"
            and frame.f_locals.get("source") is shared_aliases
        ):
            completed = frame.f_locals.get("completed_container_signatures")
            active = frame.f_locals.get("active_container_ids")
            if (
                type(completed) is dict
                and id(shared_aliases) in completed
                and type(active) is set
                and id(shared_aliases) not in active
            ):
                shared_aliases[:] = ["After Mutation"]
                mutation_performed = True
        return mutate_between_completed_visits

    sys.settrace(mutate_between_completed_visits)
    try:
        result = validate_step2_market_source_policy_approvals_object(value)
    finally:
        sys.settrace(None)

    assert mutation_performed is True
    assert result.approval_schema_version is None
    assert result.approval_policy_version is None
    assert result.declared_operator_approved_source_policy_sha256 is None
    assert result.canonical_approval_content_sha256 is None
    assert result.approval_identity_matches is None
    assert result.object_validation_performed is True
    assert result.object_structure_valid is False
    assert result.semantic_validation_performed is False
    assert result.approval_object_valid is None
    assert result.approval_state is State.STRUCTURALLY_INVALID
    assert result.source_count is None
    assert result.diagnostics == (Diagnostic.APPROVAL_INPUT_INVALID,)
    _assert_fixed_markers(result)
    with pytest.raises(TypeError) as exc_info:
        bool(result)
    assert str(exc_info.value) == VALIDATION_BOOLEAN_COERCION_ERROR
    with pytest.raises((FrozenInstanceError, AttributeError)):
        result.approval_object_valid = True  # type: ignore[misc]
    assert not hasattr(result, "__dict__")


def test_snapshot_cycle_maps_to_public_input_invalid() -> None:
    cycle: list[Any] = []
    cycle.append(cycle)
    _assert_structural_failure(cycle, Diagnostic.APPROVAL_INPUT_INVALID)
    outcome = approval_contract._capture_snapshot(cycle)
    assert outcome.failure is approval_contract._SnapshotFailure.CYCLE_DETECTED


def _nested_list(depth: int) -> list[Any]:
    root: list[Any] = []
    cursor = root
    for _ in range(depth):
        child: list[Any] = []
        cursor.append(child)
        cursor = child
    return root


def test_snapshot_depth_and_node_boundaries_are_exact() -> None:
    assert approval_contract._capture_snapshot(
        _nested_list(MAX_JSON_NESTING_DEPTH)
    ).failure is None
    assert approval_contract._capture_snapshot(
        _nested_list(MAX_JSON_NESTING_DEPTH + 1)
    ).failure is approval_contract._SnapshotFailure.DEPTH_LIMIT_EXCEEDED

    allowed = [None] * (MAX_JSON_NODE_COUNT - 1)
    excessive = [None] * MAX_JSON_NODE_COUNT
    assert approval_contract._capture_snapshot(allowed).failure is None
    assert approval_contract._capture_snapshot(
        excessive
    ).failure is approval_contract._SnapshotFailure.NODE_LIMIT_EXCEEDED


def test_shallow_mutation_predicate_detects_all_claimed_changes() -> None:
    first = object()
    second = object()
    assert approval_contract._same_shallow_container([first], [first])
    assert not approval_contract._same_shallow_container([first], [second])
    assert not approval_contract._same_shallow_container([first], [first, second])
    assert approval_contract._same_shallow_container({"a": first}, {"a": first})
    assert not approval_contract._same_shallow_container({"a": first}, {"a": second})
    assert not approval_contract._same_shallow_container(
        {"a": first, "b": second}, {"b": second, "a": first}
    )


def test_completed_shallow_signatures_cover_order_and_child_replacement() -> None:
    first_list_child: list[Any] = []
    first_dict_child: dict[str, Any] = {}
    original_list = ["first", None, first_list_child, first_dict_child]
    original_signature = approval_contract._shallow_container_signature(
        original_list
    )
    assert original_signature == (
        "list",
        (
            ("str", "first"),
            ("none",),
            ("list", id(first_list_child)),
            ("dict", id(first_dict_child)),
        ),
    )
    assert approval_contract._shallow_container_signature(
        list(reversed(original_list))
    ) != original_signature
    assert approval_contract._shallow_container_signature(
        ["replacement", None, first_list_child, first_dict_child]
    ) != original_signature
    assert approval_contract._shallow_container_signature(
        ["first", None, [], first_dict_child]
    ) != original_signature
    assert approval_contract._shallow_container_signature(
        [*original_list, "inserted"]
    ) != original_signature

    ordered_mapping = {"first": "value", "second": first_list_child}
    mapping_signature = approval_contract._shallow_container_signature(
        ordered_mapping
    )
    assert mapping_signature == (
        "dict",
        (
            ("first", ("str", "value")),
            ("second", ("list", id(first_list_child))),
        ),
    )
    assert approval_contract._shallow_container_signature(
        {"second": first_list_child, "first": "value"}
    ) != mapping_signature
    assert approval_contract._shallow_container_signature(
        {"first": "changed", "second": first_list_child}
    ) != mapping_signature
    assert approval_contract._shallow_container_signature(
        {"first": "value", "second": []}
    ) != mapping_signature
    assert approval_contract._shallow_container_signature(
        {"first": "value", "second": first_list_child, "third": None}
    ) != mapping_signature
    assert not any(
        type(item) in {dict, list}
        for item in _flatten_tuple_items(original_signature)
    )


def _flatten_tuple_items(value: Any) -> list[Any]:
    flattened: list[Any] = []
    stack = [value]
    while stack:
        item = stack.pop()
        flattened.append(item)
        if type(item) is tuple:
            stack.extend(item)
    return flattened


@pytest.mark.parametrize("field", ["operator_approved_source_policy_sha256", "approval_content"])
def test_missing_required_root_content_is_generic_input_invalid(field: str) -> None:
    value = _approval()
    del value[field]
    _assert_structural_failure(value, Diagnostic.APPROVAL_INPUT_INVALID)


@pytest.mark.parametrize("location", ["root", "content", "source", "permission"])
def test_additional_properties_are_generic_input_invalid(location: str) -> None:
    value = _approval(sources=[_source()])
    target: dict[str, Any] = {
        "root": value,
        "content": value["approval_content"],
        "source": value["approval_content"]["sources"][0],
        "permission": value["approval_content"]["sources"][0]["permissions"][0],
    }[location]
    target["is_llm_generated"] = False
    assert not Draft202012Validator(_schema_from_file()).is_valid(value)
    _assert_structural_failure(value, Diagnostic.APPROVAL_INPUT_INVALID)


@pytest.mark.parametrize(
    ("version", "diagnostic"),
    [
        (None, Diagnostic.APPROVAL_SCHEMA_VERSION_UNSUPPORTED),
        (1, Diagnostic.APPROVAL_INPUT_INVALID),
        ("wrong", Diagnostic.APPROVAL_SCHEMA_VERSION_UNSUPPORTED),
        ("", Diagnostic.APPROVAL_SCHEMA_VERSION_UNSUPPORTED),
    ],
)
def test_schema_version_prerequisite_is_exclusive(
    version: object,
    diagnostic: Diagnostic,
) -> None:
    value = _approval()
    if version is None:
        del value["schema_version"]
    else:
        value["schema_version"] = version
    value["approval_content"]["policy_version"] = None
    _assert_structural_failure(
        value,
        diagnostic,
    )


@pytest.mark.parametrize(
    ("field", "diagnostic"),
    [
        ("policy_version", Diagnostic.APPROVAL_POLICY_VERSION_INVALID),
        ("policy_change_reason", Diagnostic.APPROVAL_PROVENANCE_INVALID),
        ("approved_by", Diagnostic.APPROVAL_PROVENANCE_INVALID),
        ("approved_at_utc", Diagnostic.APPROVAL_PROVENANCE_INVALID),
    ],
)
def test_content_required_field_structural_mapping(
    field: str,
    diagnostic: Diagnostic,
) -> None:
    for replacement in (None, []):
        value = _approval()
        value["approval_content"][field] = replacement
        _assert_structural_failure(value, diagnostic)
    value = _approval()
    del value["approval_content"][field]
    _assert_structural_failure(value, diagnostic)


def test_supersedes_wrong_structure_maps_to_policy_version() -> None:
    value = _approval()
    value["approval_content"]["supersedes_policy_version"] = []
    _assert_structural_failure(value, Diagnostic.APPROVAL_POLICY_VERSION_INVALID)


@pytest.mark.parametrize(
    ("field", "diagnostic"),
    [
        ("canonical_source_id", Diagnostic.CANONICAL_SOURCE_ID_INVALID),
        ("source_version", Diagnostic.SOURCE_VERSION_INVALID),
        ("exact_aliases", Diagnostic.ALIAS_INVALID),
        ("permissions", Diagnostic.PERMISSION_FIELD_INVALID),
        ("approval_reason", Diagnostic.APPROVAL_PROVENANCE_INVALID),
    ],
)
def test_source_required_field_structural_mapping(
    field: str,
    diagnostic: Diagnostic,
) -> None:
    value = _approval(sources=[_source()])
    del value["approval_content"]["sources"][0][field]
    _assert_structural_failure(value, diagnostic)


@pytest.mark.parametrize("field", [
    "source_role", "content_type", "capture_adapter_id", "capture_adapter_version"
])
def test_permission_missing_or_wrong_type_has_specific_structural_owner(field: str) -> None:
    missing = _approval(sources=[_source()])
    del missing["approval_content"]["sources"][0]["permissions"][0][field]
    _assert_structural_failure(missing, Diagnostic.PERMISSION_FIELD_INVALID)

    wrong = _approval(sources=[_source()])
    wrong["approval_content"]["sources"][0]["permissions"][0][field] = None
    _assert_structural_failure(wrong, Diagnostic.PERMISSION_FIELD_INVALID)


def test_declared_hash_wrong_type_is_specific_structural_failure() -> None:
    value = _approval()
    value["operator_approved_source_policy_sha256"] = None
    _assert_structural_failure(value, Diagnostic.APPROVAL_DECLARED_IDENTITY_INVALID)


def test_structural_diagnostic_precedence_is_enum_stable() -> None:
    value = _approval()
    value["operator_approved_source_policy_sha256"] = None
    value["approval_content"]["policy_version"] = None
    value["approval_content"]["approved_by"] = None
    _assert_structural_failure(value, Diagnostic.APPROVAL_DECLARED_IDENTITY_INVALID)

    value["unexpected"] = "field"
    _assert_structural_failure(value, Diagnostic.APPROVAL_INPUT_INVALID)


@pytest.mark.parametrize("declared", ["0" * 63, "0" * 65, "A" * 64, "g" * 64, ""])
def test_declared_hash_syntax_is_distinct_from_identity_mismatch(declared: str) -> None:
    value = _approval()
    value["operator_approved_source_policy_sha256"] = declared
    result = _assert_content_failure(
        value,
        Diagnostic.APPROVAL_DECLARED_IDENTITY_INVALID,
        rehash=False,
    )
    assert result.declared_operator_approved_source_policy_sha256 is None
    assert result.approval_identity_matches is None


def test_valid_declared_hash_mismatch_is_identity_only_diagnostic() -> None:
    value = _approval()
    declared = "0" * 64
    assert declared != _content_hash(value["approval_content"])
    value["operator_approved_source_policy_sha256"] = declared
    result = _assert_content_failure(
        value,
        Diagnostic.APPROVAL_IDENTITY_MISMATCH,
        rehash=False,
    )
    assert result.declared_operator_approved_source_policy_sha256 == declared
    assert result.approval_identity_matches is False


def test_canonical_serialization_matches_ensure_ascii_oracle_and_domain_hash() -> None:
    content = _approval_content()
    content["policy_change_reason"] = (
        'Quote " slash / backslash \\ snowman \u2603 controls '
        "\x00\b\f\n\r\t"
    )
    outcome = approval_contract._bounded_canonical_json_bytes(content)
    assert outcome.size_exceeded is False
    assert outcome.canonical_bytes == _canonical_oracle(content)

    value = {
        "schema_version": APPROVAL_SCHEMA_VERSION,
        "operator_approved_source_policy_sha256": _content_hash(content),
        "approval_content": content,
    }
    result = _validate(value)
    assert result.approval_state is State.SEMANTICALLY_INVALID
    assert result.diagnostics == (Diagnostic.APPROVAL_PROVENANCE_INVALID,)
    assert result.canonical_approval_content_sha256 == hashlib.sha256(
        _DOMAIN + _canonical_oracle(content)
    ).hexdigest()
    assert result.canonical_approval_content_sha256 != hashlib.sha256(
        _canonical_oracle(content)
    ).hexdigest()


def test_object_identity_is_not_raw_artifact_identity() -> None:
    value = _approval(sources=[_source()])
    compact = json.dumps(value, sort_keys=True, separators=(",", ":"))
    formatted = json.dumps(value, sort_keys=False, indent=2)
    assert compact.encode("utf-8") != formatted.encode("utf-8")
    first = _assert_valid(json.loads(compact))
    second = _assert_valid(json.loads(formatted))
    assert (
        first.canonical_approval_content_sha256
        == second.canonical_approval_content_sha256
    )
    assert first.raw_artifact_parsing_performed is False
    assert second.raw_artifact_parsing_performed is False


def _approval_at_canonical_size(size: int) -> dict[str, Any]:
    value = _approval()
    content = value["approval_content"]
    content["policy_change_reason"] = ""
    base_size = len(_canonical_oracle(content))
    assert size >= base_size
    content["policy_change_reason"] = "A" * (size - base_size)
    assert len(_canonical_oracle(content)) == size
    return _rehash(value)


def test_canonical_byte_limit_and_overflow_branch_are_exact() -> None:
    at_limit = _approval_at_canonical_size(MAX_CANONICAL_APPROVAL_CONTENT_BYTES)
    result = _assert_content_failure(
        at_limit,
        Diagnostic.APPROVAL_PROVENANCE_INVALID,
        rehash=False,
    )
    assert result.canonical_approval_content_sha256 == _content_hash(
        at_limit["approval_content"]
    )

    over_limit = _approval_at_canonical_size(
        MAX_CANONICAL_APPROVAL_CONTENT_BYTES + 1
    )
    _assert_structural_failure(over_limit, Diagnostic.APPROVAL_INPUT_INVALID)


@pytest.mark.parametrize(
    "value",
    ["latest", "LATEST", "current", "Default", "*", ">=1", "Policy", "1.x"],
)
def test_policy_version_invalid_tokens_and_grammar(value: str) -> None:
    approval = _approval()
    approval["approval_content"]["policy_version"] = value
    result = _assert_content_failure(
        approval,
        Diagnostic.APPROVAL_POLICY_VERSION_INVALID,
    )
    assert result.approval_policy_version is None


def test_versions_are_exact_opaque_identifiers_without_range_inference() -> None:
    value = _approval(
        sources=[
            _source(
                source_version="1.x",
                permissions=[_permission(adapter_version="v1-latest")],
            )
        ]
    )
    _assert_valid(value)
    value["approval_content"]["sources"][0]["source_version"] = "v1-latest"
    value["approval_content"]["sources"][0]["permissions"][0][
        "capture_adapter_version"
    ] = "1.x"
    _assert_valid(value)


@pytest.mark.parametrize(
    ("field", "valid_value", "invalid_value", "diagnostic"),
    [
        (
            "policy_version",
            "a" * 64,
            "a" * 65,
            Diagnostic.APPROVAL_POLICY_VERSION_INVALID,
        ),
        (
            "approved_by",
            "a" * 64,
            "a" * 65,
            Diagnostic.APPROVAL_PROVENANCE_INVALID,
        ),
    ],
)
def test_content_identifier_length_boundaries(
    field: str,
    valid_value: str,
    invalid_value: str,
    diagnostic: Diagnostic,
) -> None:
    valid = _approval()
    valid["approval_content"][field] = valid_value
    _assert_valid(valid)
    invalid = _approval()
    invalid["approval_content"][field] = invalid_value
    _assert_content_failure(invalid, diagnostic)


@pytest.mark.parametrize(
    ("field", "valid_value", "invalid_value", "diagnostic"),
    [
        (
            "source_version",
            "v" * 64,
            "v" * 65,
            Diagnostic.SOURCE_VERSION_INVALID,
        ),
        (
            "capture_adapter_id",
            "a" * 64,
            "a" * 65,
            Diagnostic.PERMISSION_FIELD_INVALID,
        ),
        (
            "capture_adapter_version",
            "v" * 64,
            "v" * 65,
            Diagnostic.PERMISSION_FIELD_INVALID,
        ),
    ],
)
def test_source_and_permission_identifier_length_boundaries(
    field: str,
    valid_value: str,
    invalid_value: str,
    diagnostic: Diagnostic,
) -> None:
    valid_source = _source(aliases=[])
    invalid_source = _source(aliases=[])
    if field == "source_version":
        valid_source[field] = valid_value
        invalid_source[field] = invalid_value
    else:
        valid_source["permissions"][0][field] = valid_value
        invalid_source["permissions"][0][field] = invalid_value
    _assert_valid(_approval(sources=[valid_source]))
    _assert_content_failure(_approval(sources=[invalid_source]), diagnostic)


def test_self_supersession_is_invalid_but_policy_version_is_retained() -> None:
    value = _approval()
    value["approval_content"]["supersedes_policy_version"] = "policy_v1"
    result = _assert_content_failure(
        value,
        Diagnostic.APPROVAL_POLICY_VERSION_INVALID,
    )
    assert result.approval_policy_version == "policy_v1"


@pytest.mark.parametrize(
    ("timestamp", "valid"),
    [
        ("2024-02-29T00:00:00Z", True),
        ("2023-02-29T00:00:00Z", False),
        ("2026-04-31T00:00:00Z", False),
        ("0000-01-01T00:00:00Z", False),
        ("2026-01-01T23:59:59Z", True),
        ("2026-01-01T24:00:00Z", False),
        ("2026-01-01T00:60:00Z", False),
        ("2026-01-01T00:00:60Z", False),
        ("2026-01-01T00:00:00.1Z", False),
        ("2026-01-01T00:00:00+00:00", False),
        ("2026-01-01T00:00:00", False),
        ("2026-01-01t00:00:00Z", False),
        ("2026-01-01T00:00:00z", False),
        ("２０２６-01-01T00:00:00Z", False),
    ],
)
def test_timestamp_syntax_and_real_calendar_validation(timestamp: str, valid: bool) -> None:
    value = _approval()
    value["approval_content"]["approved_at_utc"] = timestamp
    if valid:
        _assert_valid(value)
    else:
        _assert_content_failure(value, Diagnostic.APPROVAL_PROVENANCE_INVALID)


@pytest.mark.parametrize(
    ("field", "valid_value", "invalid_values"),
    [
        ("policy_change_reason", "A" * MAX_STRING_LENGTH, ("", "A" * 513, " lead", "trail ")),
        ("approved_by", "operator.primary", ("Operator", " operator", "operator..x", "")),
    ],
)
def test_provenance_string_boundaries(
    field: str,
    valid_value: str,
    invalid_values: tuple[str, ...],
) -> None:
    value = _approval()
    value["approval_content"][field] = valid_value
    _assert_valid(value)
    for invalid in invalid_values:
        value = _approval()
        value["approval_content"][field] = invalid
        _assert_content_failure(value, Diagnostic.APPROVAL_PROVENANCE_INVALID)


def test_per_source_approval_reason_has_exact_reason_boundaries() -> None:
    _assert_valid(
        _approval(sources=[_source(approval_reason="R" * MAX_STRING_LENGTH)])
    )
    for invalid in ("", "R" * (MAX_STRING_LENGTH + 1), " lead", "trail "):
        _assert_content_failure(
            _approval(sources=[_source(approval_reason=invalid)]),
            Diagnostic.APPROVAL_PROVENANCE_INVALID,
        )


@pytest.mark.parametrize(
    ("source_id", "valid"),
    [
        ("a", True),
        ("a.b-c_d9", True),
        ("A", False),
        ("1source", False),
        ("source..id", False),
        ("source-", False),
        ("source id", False),
        ("a" * 64, True),
        ("a" * 65, False),
    ],
)
def test_canonical_source_id_grammar(source_id: str, valid: bool) -> None:
    value = _approval(sources=[_source(source_id, aliases=[])])
    if valid:
        _assert_valid(value)
    else:
        _assert_content_failure(value, Diagnostic.CANONICAL_SOURCE_ID_INVALID)


@pytest.mark.parametrize("source_version", ["latest", "CURRENT", "default", "*", ">=1", "_v1"])
def test_source_version_invalid_values(source_version: str) -> None:
    value = _approval(sources=[_source(source_version=source_version)])
    _assert_content_failure(value, Diagnostic.SOURCE_VERSION_INVALID)


def test_duplicate_canonical_ids_are_global_and_row_checks_continue() -> None:
    first = _source("same.source", aliases=["First"])
    second = _source("same.source", aliases=["Second"], source_version="latest")
    value = _approval(sources=[first, second])
    _assert_content_failure(
        value,
        Diagnostic.SOURCE_VERSION_INVALID,
        Diagnostic.DUPLICATE_CANONICAL_SOURCE_ID,
    )


@pytest.mark.parametrize(
    ("alias", "valid"),
    [
        ("A", True),
        ("A" * 64, True),
        ("Internal  repeated  spaces", True),
        ("", False),
        (" leading", False),
        ("trailing ", False),
        ("A" * 65, False),
        ("tab\talias", False),
        ("snowman \u2603", False),
    ],
)
def test_alias_grammar_boundaries(alias: str, valid: bool) -> None:
    value = _approval(sources=[_source(aliases=[alias])])
    if valid:
        _assert_valid(value)
    else:
        _assert_content_failure(value, Diagnostic.ALIAS_INVALID)


@pytest.mark.parametrize("aliases", [["Alias", "Alias"], ["Alias", "aLIAS"]])
def test_alias_exact_and_ascii_case_duplicates_are_global(aliases: list[str]) -> None:
    value = _approval(sources=[_source(aliases=aliases)])
    _assert_content_failure(value, Diagnostic.DUPLICATE_ALIAS)


def test_alias_duplicates_and_collisions_apply_across_sources() -> None:
    value = _approval(
        sources=[
            _source("first.source", aliases=["Shared"]),
            _source("second.source", aliases=["shared", "FIRST.SOURCE"]),
        ]
    )
    _assert_content_failure(
        value,
        Diagnostic.DUPLICATE_ALIAS,
        Diagnostic.ALIAS_CANONICAL_COLLISION,
    )


def test_invalid_alias_is_excluded_from_global_alias_checks() -> None:
    value = _approval(
        sources=[
            _source("first.source", aliases=[" bad"]),
            _source("second.source", aliases=[" bad"]),
        ]
    )
    _assert_content_failure(value, Diagnostic.ALIAS_INVALID)


def _many_sources(count: int) -> list[dict[str, Any]]:
    return [
        _source(f"source.{index}", aliases=[])
        for index in range(count)
    ]


@pytest.mark.parametrize("count", [0, 1, 64])
def test_source_count_boundaries_pass(count: int) -> None:
    _assert_valid(_approval(sources=_many_sources(count)))


def test_sixty_five_sources_fail_structurally() -> None:
    _assert_structural_failure(
        _approval(sources=_many_sources(65)),
        Diagnostic.APPROVAL_INPUT_INVALID,
    )


def test_per_source_alias_and_permission_cardinality_boundaries() -> None:
    aliases = [f"Alias {index}" for index in range(32)]
    permissions = [_permission(adapter_version=f"v{index}") for index in range(32)]
    _assert_valid(_approval(sources=[_source(aliases=aliases, permissions=permissions)]))

    too_many_aliases = _approval(
        sources=[_source(aliases=[f"Alias {index}" for index in range(33)])]
    )
    _assert_structural_failure(too_many_aliases, Diagnostic.ALIAS_INVALID)

    too_many_permissions = _approval(
        sources=[
            _source(
                permissions=[_permission(adapter_version=f"v{index}") for index in range(33)]
            )
        ]
    )
    _assert_structural_failure(
        too_many_permissions,
        Diagnostic.PERMISSION_FIELD_INVALID,
    )


def test_total_alias_limit_is_fail_closed_and_suppresses_global_alias_checks() -> None:
    sources = [
        _source(
            f"source.{source_index}",
            aliases=[f"Alias {source_index} {alias_index}" for alias_index in range(32)],
        )
        for source_index in range(17)
    ]
    assert sum(len(source["exact_aliases"]) for source in sources) > MAX_TOTAL_ALIASES
    sources[-1]["exact_aliases"][-1] = sources[0]["exact_aliases"][0]
    _assert_content_failure(_approval(sources=sources), Diagnostic.ALIAS_INVALID)


def test_total_alias_limit_exactly_five_hundred_twelve_passes() -> None:
    sources = [
        _source(
            f"source.{source_index}",
            aliases=[f"Alias {source_index} {alias_index}" for alias_index in range(32)],
        )
        for source_index in range(16)
    ]
    assert sum(len(source["exact_aliases"]) for source in sources) == MAX_TOTAL_ALIASES
    _assert_valid(_approval(sources=sources))


def test_total_permission_limit_is_fail_closed_and_suppresses_duplicates() -> None:
    sources = [
        _source(
            f"source.{source_index}",
            aliases=[],
            permissions=[
                _permission(adapter_version=f"v{permission_index}")
                for permission_index in range(32)
            ],
        )
        for source_index in range(17)
    ]
    assert sum(len(source["permissions"]) for source in sources) > MAX_TOTAL_PERMISSION_TUPLES
    sources[-1]["permissions"][-1] = deepcopy(
        sources[-1]["permissions"][0]
    )
    _assert_content_failure(
        _approval(sources=sources),
        Diagnostic.PERMISSION_FIELD_INVALID,
    )


def test_total_permission_limit_exactly_five_hundred_twelve_passes() -> None:
    sources = [
        _source(
            f"source.{source_index}",
            aliases=[],
            permissions=[
                _permission(adapter_version=f"v{permission_index}")
                for permission_index in range(32)
            ],
        )
        for source_index in range(16)
    ]
    assert (
        sum(len(source["permissions"]) for source in sources)
        == MAX_TOTAL_PERMISSION_TUPLES
    )
    _assert_valid(_approval(sources=sources))


@pytest.mark.parametrize("role", SOURCE_ROLES)
def test_each_closed_source_role_is_valid(role: str) -> None:
    _assert_valid(
        _approval(sources=[_source(permissions=[_permission(role=role)])])
    )


def test_unknown_role_is_specific_and_suppresses_same_tuple_field_noise() -> None:
    value = _approval(
        sources=[
            _source(
                permissions=[
                    _permission(role="UNKNOWN_ROLE", content_type="Bad Type")
                ]
            )
        ]
    )
    _assert_content_failure(value, Diagnostic.UNKNOWN_SOURCE_ROLE)


@pytest.mark.parametrize(
    ("content_type", "valid"),
    [
        ("a/b", True),
        (f"{'a' * 63}/{'b' * 63}", True),
        ("application/json", True),
        ("Application/json", False),
        ("application", False),
        ("application/", False),
        ("/json", False),
        ("application/json; charset=utf-8", False),
        (f"{'a' * 64}/b", False),
    ],
)
def test_content_type_grammar_and_boundaries(content_type: str, valid: bool) -> None:
    value = _approval(
        sources=[_source(permissions=[_permission(content_type=content_type)])]
    )
    if valid:
        _assert_valid(value)
    else:
        _assert_content_failure(value, Diagnostic.PERMISSION_FIELD_INVALID)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("capture_adapter_id", "Capture.Http"),
        ("capture_adapter_id", "capture..http"),
        ("capture_adapter_version", ">=1"),
        ("capture_adapter_version", "_v1"),
    ],
)
def test_adapter_field_grammar(field: str, value: str) -> None:
    permission = _permission()
    permission[field] = value
    _assert_content_failure(
        _approval(sources=[_source(permissions=[permission])]),
        Diagnostic.PERMISSION_FIELD_INVALID,
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("source_role", "*"),
        ("content_type", "application/*"),
        ("capture_adapter_id", "capture.*"),
        ("capture_adapter_version", "*"),
        ("capture_adapter_version", "latest"),
        ("capture_adapter_version", "CURRENT"),
        ("capture_adapter_version", "Default"),
    ],
)
def test_explicit_wildcard_or_reserved_permission_is_specific(
    field: str,
    value: str,
) -> None:
    permission = _permission()
    permission[field] = value
    _assert_content_failure(
        _approval(sources=[_source(permissions=[permission])]),
        Diagnostic.IMPLICIT_OR_WILDCARD_PERMISSION,
    )


def test_wildcard_priority_suppresses_unknown_role_in_same_tuple() -> None:
    permission = _permission(role="UNKNOWN", adapter_version="latest")
    _assert_content_failure(
        _approval(sources=[_source(permissions=[permission])]),
        Diagnostic.IMPLICIT_OR_WILDCARD_PERMISSION,
    )


def test_duplicate_permission_tuple_is_exact_and_per_source() -> None:
    permission = _permission()
    value = _approval(
        sources=[_source(permissions=[permission, deepcopy(permission)])]
    )
    _assert_content_failure(value, Diagnostic.DUPLICATE_PERMISSION_TUPLE)

    same_tuple_different_sources = _approval(
        sources=[
            _source("first.source", aliases=[], permissions=[deepcopy(permission)]),
            _source("second.source", aliases=[], permissions=[deepcopy(permission)]),
        ]
    )
    _assert_valid(same_tuple_different_sources)


def test_invalid_permission_is_excluded_from_duplicate_detection() -> None:
    invalid = _permission(content_type="bad")
    value = _approval(
        sources=[_source(permissions=[invalid, deepcopy(invalid)])]
    )
    _assert_content_failure(value, Diagnostic.PERMISSION_FIELD_INVALID)


def test_semantic_diagnostics_are_globally_deduplicated_and_enum_ordered() -> None:
    duplicate_permission = _permission()
    first = _source(
        "same.source",
        source_version="latest",
        aliases=["Shared", "same.source"],
        permissions=[
            _permission(role="UNKNOWN"),
            _permission(content_type="bad"),
            _permission(adapter_version="latest"),
            duplicate_permission,
            deepcopy(duplicate_permission),
        ],
        approval_reason="",
    )
    second = _source(
        "same.source",
        source_version="latest",
        aliases=["shared", " bad"],
    )
    value = _approval(sources=[first, second])
    value["operator_approved_source_policy_sha256"] = "G" * 64
    value["approval_content"]["policy_version"] = "latest"
    value["approval_content"]["approved_at_utc"] = "invalid"
    result = _assert_content_failure(
        value,
        Diagnostic.APPROVAL_DECLARED_IDENTITY_INVALID,
        Diagnostic.APPROVAL_POLICY_VERSION_INVALID,
        Diagnostic.APPROVAL_PROVENANCE_INVALID,
        Diagnostic.SOURCE_VERSION_INVALID,
        Diagnostic.DUPLICATE_CANONICAL_SOURCE_ID,
        Diagnostic.ALIAS_INVALID,
        Diagnostic.DUPLICATE_ALIAS,
        Diagnostic.ALIAS_CANONICAL_COLLISION,
        Diagnostic.UNKNOWN_SOURCE_ROLE,
        Diagnostic.PERMISSION_FIELD_INVALID,
        Diagnostic.IMPLICIT_OR_WILDCARD_PERMISSION,
        Diagnostic.DUPLICATE_PERMISSION_TUPLE,
        rehash=False,
    )
    assert len(result.diagnostics) == len(set(result.diagnostics))
    assert list(result.diagnostics) == sorted(
        result.diagnostics,
        key=list(Diagnostic).index,
    )


def test_semantic_diagnostics_are_source_row_order_independent() -> None:
    first = _source(
        "same.source",
        source_version="latest",
        aliases=["Shared Alias"],
    )
    second = _source(
        "same.source",
        aliases=["shared alias", "same.source"],
        permissions=[_permission(role="UNKNOWN_ROLE")],
    )
    expected_diagnostics = (
        Diagnostic.SOURCE_VERSION_INVALID,
        Diagnostic.DUPLICATE_CANONICAL_SOURCE_ID,
        Diagnostic.DUPLICATE_ALIAS,
        Diagnostic.ALIAS_CANONICAL_COLLISION,
        Diagnostic.UNKNOWN_SOURCE_ROLE,
    )
    original = _assert_content_failure(
        _approval(sources=[deepcopy(first), deepcopy(second)]),
        *expected_diagnostics,
    )
    reversed_rows = _assert_content_failure(
        _approval(sources=[deepcopy(second), deepcopy(first)]),
        *expected_diagnostics,
    )
    assert original.approval_state is reversed_rows.approval_state
    assert original.approval_object_valid is reversed_rows.approval_object_valid
    assert original.approval_identity_matches is reversed_rows.approval_identity_matches
    assert original.diagnostics == reversed_rows.diagnostics
    assert len(original.diagnostics) == len(set(original.diagnostics))
    assert (
        original.canonical_approval_content_sha256
        != reversed_rows.canonical_approval_content_sha256
    )


def test_unrelated_semantic_failure_does_not_suppress_identity_comparison() -> None:
    value = _approval()
    value["approval_content"]["approved_by"] = "INVALID"
    value["operator_approved_source_policy_sha256"] = "0" * 64
    result = _assert_content_failure(
        value,
        Diagnostic.APPROVAL_PROVENANCE_INVALID,
        Diagnostic.APPROVAL_IDENTITY_MISMATCH,
        rehash=False,
    )
    assert result.approval_identity_matches is False


def test_public_dependency_exception_propagates_without_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    value = _approval()
    expected = ValueError("distinctive sha256 dependency failure")

    def fail(value: bytes) -> Any:
        raise expected

    monkeypatch.setattr(approval_contract.hashlib, "sha256", fail)
    with pytest.raises(ValueError) as exc_info:
        validate_step2_market_source_policy_approvals_object(value)
    assert exc_info.value is expected


def test_production_module_has_no_try_or_trystar_exception_masking() -> None:
    path = Path(approval_contract.__file__).resolve()
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

    def contains_exception_handler(source: str) -> bool:
        return any(
            isinstance(node, (ast.Try, ast.TryStar))
            for node in ast.walk(ast.parse(source))
        )

    assert not any(
        isinstance(node, (ast.Try, ast.TryStar)) for node in ast.walk(tree)
    )
    assert contains_exception_handler(
        "try:\n    pass\nexcept Exception:\n    pass\n"
    )
    assert contains_exception_handler(
        "try:\n    pass\nexcept* Exception:\n    pass\n"
    )


_MODULE = approval_contract.__name__
_BASENAME = _MODULE.rsplit(".", 1)[-1]
_RELATIVE_PATH = Path("src/investment_orchestrator/validators") / f"{_BASENAME}.py"
_PUBLIC_SYMBOLS = frozenset(
    {
        "validate_step2_market_source_policy_approvals_object",
        "Step2MarketSourcePolicyApprovalsObjectValidationResult",
        "Step2MarketSourcePolicyApprovalDiagnostic",
        "Step2MarketSourcePolicyApprovalObjectState",
        "APPROVAL_SCHEMA_VERSION",
        "APPROVAL_VALIDATION_RESULT_VERSION",
        "APPROVAL_SCHEMA_FILENAME",
    }
)


def _reference_findings(relative_path: str, source: str) -> list[str]:
    findings: list[str] = []
    tree = ast.parse(source, filename=relative_path)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == _MODULE or alias.name.startswith(f"{_MODULE}."):
                    findings.append(f"{relative_path}: import")
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module == _MODULE or module.endswith(f".{_BASENAME}"):
                findings.append(f"{relative_path}: from-import")
            if any(alias.name in _PUBLIC_SYMBOLS for alias in node.names):
                findings.append(f"{relative_path}: symbol-import")
        elif isinstance(node, ast.Name) and node.id in _PUBLIC_SYMBOLS:
            findings.append(f"{relative_path}: symbol")
        elif isinstance(node, ast.Attribute) and node.attr in _PUBLIC_SYMBOLS:
            findings.append(f"{relative_path}: attribute")
        elif isinstance(node, ast.Call):
            if (
                (
                    isinstance(node.func, ast.Attribute)
                    and node.func.attr == "import_module"
                )
                or (
                    isinstance(node.func, ast.Name)
                    and node.func.id == "__import__"
                )
            ) and any(
                isinstance(argument, ast.Constant)
                and argument.value == _MODULE
                for argument in node.args
            ):
                findings.append(f"{relative_path}: dynamic-import")
    for marker in (
        _MODULE,
        *_PUBLIC_SYMBOLS,
        APPROVAL_SCHEMA_VERSION,
        APPROVAL_VALIDATION_RESULT_VERSION,
        APPROVAL_SCHEMA_FILENAME,
    ):
        if marker in source:
            findings.append(f"{relative_path}: text")
    return sorted(set(findings))


def test_reference_detector_covers_import_alias_symbol_dynamic_and_literal_forms() -> None:
    cases = (
        f"import {_MODULE}\n",
        f"import {_MODULE} as contract\ncontract.validate_step2_market_source_policy_approvals_object({{}})\n",
        f"from {_MODULE} import Step2MarketSourcePolicyApprovalDiagnostic\n",
        "handler = Step2MarketSourcePolicyApprovalsObjectValidationResult\n",
        f"import importlib\nimportlib.import_module({_MODULE!r})\n",
        f"contract = __import__({_MODULE!r})\n",
        f"VERSION = {APPROVAL_SCHEMA_VERSION!r}\n",
    )
    assert all(
        _reference_findings(f"synthetic/{index}.py", source)
        for index, source in enumerate(cases)
    )


def test_no_production_consumer_references_approval_object_contract() -> None:
    root = Path(__file__).resolve().parents[2]
    production_root = root / "src" / "investment_orchestrator"
    contract_path = root / _RELATIVE_PATH
    findings: list[str] = []
    for path in sorted(production_root.rglob("*.py")):
        if path == contract_path:
            continue
        relative = path.relative_to(root).as_posix()
        findings.extend(
            _reference_findings(relative, path.read_text(encoding="utf-8"))
        )
    assert sorted(set(findings)) == [], "\n".join(sorted(set(findings)))


def test_production_module_has_no_io_clock_environment_workflow_or_order_capability() -> None:
    source = Path(approval_contract.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported_roots = {
        alias.name.split(".", 1)[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    imported_roots.update(
        (node.module or "").split(".", 1)[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    )
    assert imported_roots <= {"__future__", "dataclasses", "enum", "hashlib", "re", "typing"}
    forbidden_calls = {
        "open",
        "getenv",
        "system",
        "popen",
        "run",
        "request",
        "urlopen",
        "publish",
        "compile_orders",
        "submit",
        "transmit",
    }
    assert not {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    } & forbidden_calls
    assert not {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    } & forbidden_calls


def test_no_private_branch_fault_injection_or_authority_result_fields() -> None:
    test_source = Path(__file__).read_text(encoding="utf-8")
    tree = ast.parse(test_source)
    forbidden_patch_targets = (
        "_capture_snapshot",
        "_SnapshotOutcome",
        "_SnapshotFailure",
        "_same_shallow_container",
        "_structural_diagnostic",
        "_evaluate_approval_content",
        "_ordered_content_diagnostics",
        "_structural_failure",
    )
    patch_targets: list[str] = []
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "setattr"
        ):
            continue
        patch_targets.extend(
            ast.unparse(argument)
            for argument in node.args[:2]
        )
    assert all(
        target not in patch_target
        for patch_target in patch_targets
        for target in forbidden_patch_targets
    )
    assert patch_targets == ["approval_contract.hashlib", "'sha256'"]
    result = _assert_valid(_approval())
    forbidden_fields = (
        "raw_artifact_valid",
        "operator_authenticated",
        "source_role_eligible",
        "policy_activated",
        "candidate_valid",
        "publication_eligible",
        "workflow_allowed",
        "order_compilation_allowed",
        "broker_route",
    )
    assert all(not hasattr(result, field_name) for field_name in forbidden_fields)


def test_public_api_name_and_exports_are_object_specific() -> None:
    assert validate_step2_market_source_policy_approvals_object.__name__.endswith(
        "_object"
    )
    assert "validate_step2_market_source_policy_approvals" not in approval_contract.__all__
    assert "validate_step2_market_source_policy_approvals_object" in approval_contract.__all__
    assert all("parser" not in name.lower() for name in approval_contract.__all__)
