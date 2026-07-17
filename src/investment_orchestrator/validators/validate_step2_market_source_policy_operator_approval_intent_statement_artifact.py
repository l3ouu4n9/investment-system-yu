"""Bind strict raw parsing to decoded approval-intent statement validation.

This module composes the fixed p4a1 raw parser and p4a2 decoded-object
validator.  It deliberately does not authenticate an operator, authorize an
approval, activate anything, affect a workflow, publish an artifact, or grant
trading authority.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from enum import Enum
import hashlib
import json
import math
import re
from typing import NoReturn

from investment_orchestrator.parsers.parse_step2_market_source_policy_operator_approval_intent_statement import (
    APPROVAL_INTENT_STATEMENT_PARSER_REVISION,
    APPROVAL_INTENT_STATEMENT_RESULT_REVISION,
    AUTHORITY_SCOPE as PARSER_AUTHORITY_SCOPE,
    FrozenApprovalIntentJsonArray,
    FrozenApprovalIntentJsonObject,
    MAX_APPROVAL_INTENT_STATEMENT_PARSE_DIAGNOSTICS,
    MAX_ARRAY_ITEM_COUNT,
    MAX_CUMULATIVE_STRING_CODE_POINTS,
    MAX_INDIVIDUAL_STRING_CODE_POINTS,
    MAX_JSON_NESTING_DEPTH,
    MAX_JSON_NODE_COUNT,
    MAX_JSON_NUMBER_TOKEN_CODE_POINTS,
    MAX_OBJECT_MEMBER_COUNT,
    MAX_PARSED_VALUE_CANONICAL_BYTES,
    MAX_RAW_STATEMENT_BYTES,
    MIN_RAW_STATEMENT_BYTES,
    PARSED_VALUE_IDENTITY_DOMAIN,
    Step2MarketSourcePolicyOperatorApprovalIntentStatementParseDiagnostic,
    Step2MarketSourcePolicyOperatorApprovalIntentStatementParseResult,
    Step2MarketSourcePolicyOperatorApprovalIntentStatementParseState,
    parse_step2_market_source_policy_operator_approval_intent_statement_bytes,
)
from investment_orchestrator.validators.validate_step2_market_source_policy_operator_approval_intent_statement import (
    AUTHORITY_SCOPE as STATEMENT_VALIDATOR_AUTHORITY_SCOPE,
    MAX_OPERATOR_APPROVAL_INTENT_STATEMENT_CANONICAL_BYTES,
    MAX_OPERATOR_APPROVAL_INTENT_STATEMENT_VALIDATION_DIAGNOSTICS,
    OPERATOR_APPROVAL_INTENT_STATEMENT_IDENTITY_DOMAIN,
    STATEMENT_SCHEMA_VERSION,
    Step2MarketSourcePolicyOperatorApprovalIntentAction,
    Step2MarketSourcePolicyOperatorApprovalIntentStatement,
    Step2MarketSourcePolicyOperatorApprovalIntentStatementValidationDiagnostic,
    Step2MarketSourcePolicyOperatorApprovalIntentStatementValidationResult,
    Step2MarketSourcePolicyOperatorApprovalIntentStatementValidationState,
    VALIDATION_RESULT_VERSION as STATEMENT_VALIDATION_RESULT_VERSION,
    VALIDATOR_VERSION as STATEMENT_VALIDATOR_VERSION,
    validate_step2_market_source_policy_operator_approval_intent_statement,
)


RESULT_VERSION = (
    "step2_market_source_policy_operator_approval_intent_statement_"
    "artifact_validation_result_v1"
)
VALIDATOR_VERSION = (
    "step2_market_source_policy_operator_approval_intent_statement_"
    "artifact_validator_v1"
)
MAX_OPERATOR_APPROVAL_INTENT_STATEMENT_ARTIFACT_VALIDATION_DIAGNOSTICS = 1
MAX_OPERATOR_APPROVAL_INTENT_STATEMENT_ARTIFACT_VALIDATION_CANONICAL_BYTES = 2_048
OPERATOR_APPROVAL_INTENT_STATEMENT_ARTIFACT_VALIDATION_IDENTITY_DOMAIN = (
    b"step2_market_source_policy_operator_approval_intent_statement_"
    b"artifact_validation_v1\0"
)

AUTHORITY_SCOPE = "operator_approval_intent_statement_artifact_validation_only"
TRADE_PERMISSION_EFFECT = "none"

_RESULT_CONSTRUCTION_ERROR = (
    "operator approval-intent statement artifact validation results are created "
    "only by the public validator"
)
_BOOLEAN_MISUSE_ERROR = (
    "inspect artifact_contract_valid explicitly; operator approval-intent statement "
    "artifact validation results have no truth value"
)
_INVARIANT_ERROR_MESSAGE = (
    "operator approval-intent statement artifact validator invariant violated"
)
_MISSING = object()
_UNSET = object()
_REQUIRED_KEYS = frozenset(
    {
        "statement_schema_version",
        "authentication_context_version",
        "expected_identity_binding_sha256",
        "intent_action",
        "provenance_identity_sha256",
    }
)
_RESERVED_AUTHENTICATION_CONTEXT_VERSIONS = frozenset(
    {"latest", "current", "default", "*"}
)
_AUTHENTICATION_CONTEXT_VERSION_PATTERN = re.compile(
    r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$"
)
_EXACT_JSON_DECODED_TYPES = frozenset(
    {dict, list, str, int, float, bool, type(None)}
)
_P4A1_RESULT_VERSION = (
    "step2_market_source_policy_operator_approval_intent_statement_parse_result_v1"
)
_P4A1_ENGINE_REVISION = (
    "step2_market_source_policy_operator_approval_intent_statement_parser_v1"
)
_P4A1_PARSED_VALUE_DOMAIN = (
    b"step2_market_source_policy_operator_approval_intent_statement_parsed_value_v1\0"
)
_P4A2_RESULT_VERSION = (
    "step2_market_source_policy_operator_approval_intent_statement_validation_result_v1"
)
_P4A2_VALIDATOR_VERSION = (
    "step2_market_source_policy_operator_approval_intent_statement_validator_v1"
)
_P4A2_SEMANTIC_DOMAIN = (
    b"step2_market_source_policy_operator_approval_intent_statement_v1\0"
)
_P4A1_RESULT_FIELDS = (
    "result_version",
    "parser_version",
    "parse_state",
    "raw_statement_size_bytes",
    "raw_statement_sha256",
    "parsed_value_identity_sha256",
    "text_decoding_performed",
    "text_decoding_valid",
    "json_syntax_validation_performed",
    "json_syntax_valid",
    "duplicate_key_validation_performed",
    "duplicate_keys_valid",
    "unicode_scalar_validation_performed",
    "unicode_scalars_valid",
    "structural_bound_validation_performed",
    "structural_bounds_valid",
    "parse_valid",
    "parsed_value_available",
    "immutable_parsed_value",
    "diagnostics",
    "authority_scope",
    "not_authentication",
    "not_approval_authorization",
    "not_activation_authorization",
    "not_trade_authorization",
    "trade_permission_effect",
    "statement_contract_validation_performed",
    "statement_semantic_identity_computed",
    "authentication_evaluation_performed",
    "intent_evaluation_performed",
    "freshness_evaluation_performed",
    "replay_evaluation_performed",
    "lifecycle_evaluation_performed",
    "workflow_permission_evaluated",
    "order_compilation_evaluated",
)
_P4A2_RESULT_FIELDS = (
    "result_version",
    "validator_version",
    "validation_state",
    "validated_statement",
    "operator_approval_intent_statement_identity_sha256",
    "input_type_check_performed",
    "input_type_valid",
    "root_type_check_performed",
    "root_type_valid",
    "key_set_validation_performed",
    "key_set_valid",
    "field_validation_performed",
    "field_validation_valid",
    "semantic_identity_computed",
    "statement_contract_valid",
    "intent_evaluation_performed",
    "intent_action",
    "intent_is_approval",
    "diagnostics",
    "authority_scope",
    "not_authentication",
    "not_approval_authorization",
    "not_activation_authorization",
    "not_trade_authorization",
    "trade_permission_effect",
    "authentication_evaluation_performed",
    "authorship_evaluation_performed",
    "freshness_evaluation_performed",
    "replay_evaluation_performed",
    "lifecycle_evaluation_performed",
    "activation_evaluation_performed",
    "workflow_permission_evaluated",
    "order_compilation_evaluated",
)


class Step2MarketSourcePolicyOperatorApprovalIntentStatementArtifactValidationState(
    str,
    Enum,
):
    """The closed outcome set for raw/object statement composition."""

    RAW_PARSE_INVALID = "raw_parse_invalid"
    PARSER_RESULT_INTEGRITY_INVALID = "parser_result_integrity_invalid"
    PARSED_VALUE_IDENTITY_BINDING_INVALID = (
        "parsed_value_identity_binding_invalid"
    )
    STATEMENT_CONTRACT_INVALID = "statement_contract_invalid"
    STATEMENT_VALIDATION_RESULT_INTEGRITY_INVALID = (
        "statement_validation_result_integrity_invalid"
    )
    STATEMENT_SEMANTIC_IDENTITY_BINDING_INVALID = (
        "statement_semantic_identity_binding_invalid"
    )
    VALID_APPROVE = "valid_approve"
    VALID_REJECT = "valid_reject"


class Step2MarketSourcePolicyOperatorApprovalIntentStatementArtifactValidationDiagnostic(
    str,
    Enum,
):
    """The closed, value-free diagnostic set for artifact composition."""

    RAW_STATEMENT_PARSE_INVALID = "raw_statement_parse_invalid"
    PARSER_RESULT_INTEGRITY_INVALID = "parser_result_integrity_invalid"
    PARSED_VALUE_IDENTITY_BINDING_INVALID = (
        "parsed_value_identity_binding_invalid"
    )
    STATEMENT_CONTRACT_INVALID = "statement_contract_invalid"
    STATEMENT_VALIDATION_RESULT_INTEGRITY_INVALID = (
        "statement_validation_result_integrity_invalid"
    )
    STATEMENT_SEMANTIC_IDENTITY_BINDING_INVALID = (
        "statement_semantic_identity_binding_invalid"
    )


class _OperatorApprovalIntentStatementArtifactInvariantError(RuntimeError):
    """Raised only for impossible p4a3-internal states."""

    def __init__(self) -> None:
        super().__init__(_INVARIANT_ERROR_MESSAGE)


@dataclass(frozen=True, slots=True, init=False)
class Step2MarketSourcePolicyOperatorApprovalIntentStatementArtifactValidationResult:
    """A sealed, non-authorizing p4a1/p4a2 composition result."""

    result_version: str
    validator_version: str
    composition_state: (
        Step2MarketSourcePolicyOperatorApprovalIntentStatementArtifactValidationState
    )

    parser_result: (
        Step2MarketSourcePolicyOperatorApprovalIntentStatementParseResult | None
    )
    statement_validation_result: (
        Step2MarketSourcePolicyOperatorApprovalIntentStatementValidationResult | None
    )

    parser_result_integrity_check_performed: bool
    parser_result_integrity_valid: bool | None

    parsed_value_conversion_performed: bool
    parsed_value_conversion_valid: bool | None
    parsed_value_identity_recheck_performed: bool
    parsed_value_identity_recheck_valid: bool | None

    statement_validation_performed: bool

    statement_validation_result_integrity_check_performed: bool
    statement_validation_result_integrity_valid: bool | None

    statement_validation_valid: bool | None

    statement_semantic_identity_recheck_performed: bool
    statement_semantic_identity_recheck_valid: bool | None

    operator_approval_intent_statement_artifact_validation_identity_sha256: str | None

    artifact_validation_identity_computed: bool
    artifact_contract_valid: bool | None

    literal_intent_evaluation_performed: bool

    literal_intent_action: Step2MarketSourcePolicyOperatorApprovalIntentAction | None

    literal_intent_is_approval: bool | None

    diagnostics: tuple[
        Step2MarketSourcePolicyOperatorApprovalIntentStatementArtifactValidationDiagnostic,
        ...,
    ]

    authority_scope: str
    not_authentication: bool
    not_approval_authorization: bool
    not_activation_authorization: bool
    not_trade_authorization: bool
    trade_permission_effect: str

    authentication_evaluation_performed: bool
    authorship_evaluation_performed: bool
    freshness_evaluation_performed: bool
    replay_evaluation_performed: bool
    lifecycle_evaluation_performed: bool
    activation_evaluation_performed: bool
    workflow_permission_evaluated: bool
    order_compilation_evaluated: bool

    def __new__(cls, *args: object, **kwargs: object) -> NoReturn:
        del cls, args, kwargs
        raise TypeError(_RESULT_CONSTRUCTION_ERROR)

    def __setstate__(self, state: object) -> NoReturn:
        del self, state
        raise TypeError(_RESULT_CONSTRUCTION_ERROR)

    def __reduce__(self) -> NoReturn:
        raise TypeError(_RESULT_CONSTRUCTION_ERROR)

    def __reduce_ex__(self, protocol: object) -> NoReturn:
        del protocol
        raise TypeError(_RESULT_CONSTRUCTION_ERROR)

    def __bool__(self) -> NoReturn:
        raise TypeError(_BOOLEAN_MISUSE_ERROR)


_ArtifactState = Step2MarketSourcePolicyOperatorApprovalIntentStatementArtifactValidationState
_ArtifactDiagnostic = (
    Step2MarketSourcePolicyOperatorApprovalIntentStatementArtifactValidationDiagnostic
)
_ParseState = Step2MarketSourcePolicyOperatorApprovalIntentStatementParseState
_ParseDiagnostic = Step2MarketSourcePolicyOperatorApprovalIntentStatementParseDiagnostic
_StatementState = Step2MarketSourcePolicyOperatorApprovalIntentStatementValidationState
_StatementDiagnostic = (
    Step2MarketSourcePolicyOperatorApprovalIntentStatementValidationDiagnostic
)
_IntentAction = Step2MarketSourcePolicyOperatorApprovalIntentAction


def _upstream_contract_constants_are_frozen() -> bool:
    return (
        APPROVAL_INTENT_STATEMENT_RESULT_REVISION == _P4A1_RESULT_VERSION
        and APPROVAL_INTENT_STATEMENT_PARSER_REVISION == _P4A1_ENGINE_REVISION
        and PARSED_VALUE_IDENTITY_DOMAIN == _P4A1_PARSED_VALUE_DOMAIN
        and PARSER_AUTHORITY_SCOPE == "raw_json_parsing_only"
        and MIN_RAW_STATEMENT_BYTES == 1
        and MAX_RAW_STATEMENT_BYTES == 4_096
        and MAX_JSON_NESTING_DEPTH == 8
        and MAX_JSON_NODE_COUNT == 256
        and MAX_CUMULATIVE_STRING_CODE_POINTS == 2_048
        and MAX_INDIVIDUAL_STRING_CODE_POINTS == 1_024
        and MAX_OBJECT_MEMBER_COUNT == 32
        and MAX_ARRAY_ITEM_COUNT == 64
        and MAX_JSON_NUMBER_TOKEN_CODE_POINTS == 256
        and MAX_PARSED_VALUE_CANONICAL_BYTES == 32_768
        and MAX_APPROVAL_INTENT_STATEMENT_PARSE_DIAGNOSTICS == 1
        and tuple(item.value for item in _ParseState)
        == (
            "input_absent",
            "input_type_invalid",
            "raw_size_invalid",
            "encoding_invalid",
            "json_grammar_invalid",
            "duplicate_key_invalid",
            "unicode_scalar_invalid",
            "resource_limit_invalid",
            "valid",
        )
        and tuple(item.value for item in _ParseDiagnostic)
        == (
            "statement_input_missing",
            "statement_input_type_invalid",
            "statement_raw_size_invalid",
            "statement_utf8_bom_unsupported",
            "statement_utf8_invalid",
            "statement_json_invalid",
            "statement_trailing_content",
            "statement_duplicate_key",
            "statement_surrogate_invalid",
            "statement_depth_limit_exceeded",
            "statement_node_limit_exceeded",
            "statement_cumulative_string_limit_exceeded",
            "statement_string_limit_exceeded",
            "statement_object_member_limit_exceeded",
            "statement_array_item_limit_exceeded",
            "statement_number_limit_exceeded",
        )
        and tuple(
            field.name
            for field in fields(
                Step2MarketSourcePolicyOperatorApprovalIntentStatementParseResult
            )
        )
        == _P4A1_RESULT_FIELDS
        and STATEMENT_VALIDATION_RESULT_VERSION == _P4A2_RESULT_VERSION
        and STATEMENT_VALIDATOR_VERSION == _P4A2_VALIDATOR_VERSION
        and OPERATOR_APPROVAL_INTENT_STATEMENT_IDENTITY_DOMAIN
        == _P4A2_SEMANTIC_DOMAIN
        and STATEMENT_VALIDATOR_AUTHORITY_SCOPE
        == "operator_approval_intent_statement_contract_validation_only"
        and MAX_OPERATOR_APPROVAL_INTENT_STATEMENT_CANONICAL_BYTES == 512
        and MAX_OPERATOR_APPROVAL_INTENT_STATEMENT_VALIDATION_DIAGNOSTICS == 1
        and tuple(item.value for item in _StatementState)
        == (
            "input_type_invalid",
            "root_type_invalid",
            "key_set_invalid",
            "field_type_invalid",
            "field_value_invalid",
            "valid_approve",
            "valid_reject",
        )
        and tuple(item.value for item in _StatementDiagnostic)
        == (
            "statement_input_type_invalid",
            "statement_root_type_invalid",
            "statement_key_set_invalid",
            "statement_schema_version_invalid",
            "authentication_context_type_invalid",
            "authentication_context_syntax_invalid",
            "authentication_context_reserved",
            "expected_identity_binding_type_invalid",
            "expected_identity_binding_syntax_invalid",
            "intent_action_type_invalid",
            "intent_action_value_invalid",
            "provenance_identity_type_invalid",
            "provenance_identity_syntax_invalid",
        )
        and tuple(
            field.name
            for field in fields(
                Step2MarketSourcePolicyOperatorApprovalIntentStatementValidationResult
            )
        )
        == _P4A2_RESULT_FIELDS
    )


def validate_step2_market_source_policy_operator_approval_intent_statement_artifact_bytes(
    value: object,
) -> Step2MarketSourcePolicyOperatorApprovalIntentStatementArtifactValidationResult:
    """Compose one strict raw parse with one decoded statement validation."""

    parser_output = (
        parse_step2_market_source_policy_operator_approval_intent_statement_bytes(
            value
        )
    )
    if not _parser_result_integrity_valid(parser_output, value):
        return _create_result(
            composition_state=_ArtifactState.PARSER_RESULT_INTEGRITY_INVALID,
        )
    parser_result = parser_output

    if _field(parser_result, "parse_state") is not _ParseState.VALID:
        return _create_result(
            composition_state=_ArtifactState.RAW_PARSE_INVALID,
            parser_result=parser_result,
        )

    frozen_value = _field(parser_result, "immutable_parsed_value")
    if not _frozen_tree_is_valid(frozen_value):
        return _create_result(
            composition_state=_ArtifactState.PARSER_RESULT_INTEGRITY_INVALID,
        )
    converted_value = _convert_frozen_value(frozen_value)
    canonical_parsed_value = _canonical_json_utf8(converted_value)
    if len(canonical_parsed_value) > MAX_PARSED_VALUE_CANONICAL_BYTES:
        return _create_result(
            composition_state=_ArtifactState.PARSER_RESULT_INTEGRITY_INVALID,
        )
    recomputed_parsed_identity = _sha256_hex(
        _P4A1_PARSED_VALUE_DOMAIN + canonical_parsed_value
    )
    if not _is_exact_string(
        _field(parser_result, "parsed_value_identity_sha256"),
        recomputed_parsed_identity,
    ):
        return _create_result(
            composition_state=_ArtifactState.PARSED_VALUE_IDENTITY_BINDING_INVALID,
        )

    statement_output = (
        validate_step2_market_source_policy_operator_approval_intent_statement(
            converted_value
        )
    )
    if not _statement_result_integrity_valid(statement_output, converted_value):
        return _create_result(
            composition_state=(
                _ArtifactState.STATEMENT_VALIDATION_RESULT_INTEGRITY_INVALID
            ),
            parser_result=parser_result,
        )
    statement_result = statement_output

    if _field(statement_result, "validation_state") not in {
        _StatementState.VALID_APPROVE,
        _StatementState.VALID_REJECT,
    }:
        return _create_result(
            composition_state=_ArtifactState.STATEMENT_CONTRACT_INVALID,
            parser_result=parser_result,
            statement_validation_result=statement_result,
        )

    if not _semantic_identity_matches(statement_result):
        return _create_result(
            composition_state=(
                _ArtifactState.STATEMENT_SEMANTIC_IDENTITY_BINDING_INVALID
            ),
            parser_result=parser_result,
        )

    return _create_result(
        composition_state=(
            _ArtifactState.VALID_APPROVE
            if _field(statement_result, "validation_state")
            is _StatementState.VALID_APPROVE
            else _ArtifactState.VALID_REJECT
        ),
        parser_result=parser_result,
        statement_validation_result=statement_result,
    )


def _create_result(
    *,
    composition_state: _ArtifactState,
    parser_result: Step2MarketSourcePolicyOperatorApprovalIntentStatementParseResult
    | None = None,
    statement_validation_result: Step2MarketSourcePolicyOperatorApprovalIntentStatementValidationResult
    | None = None,
) -> Step2MarketSourcePolicyOperatorApprovalIntentStatementArtifactValidationResult:
    """Allocate the sole public p4a3 result after enforcing its matrix."""

    if type(composition_state) is not _ArtifactState:
        _invariant_failure()
    branch = _branch_values(composition_state)
    parser_required, statement_required = _retention_requirements(composition_state)
    if (parser_result is not None) is not parser_required:
        _invariant_failure()
    if (statement_validation_result is not None) is not statement_required:
        _invariant_failure()
    if parser_result is not None and not _parser_result_static_valid(parser_result):
        _invariant_failure()
    if statement_validation_result is not None and not _statement_result_static_valid(
        statement_validation_result
    ):
        _invariant_failure()
    _validate_retained_state(
        composition_state,
        parser_result,
        statement_validation_result,
    )

    (
        parser_check_performed,
        parser_check_valid,
        conversion_performed,
        conversion_valid,
        parsed_identity_performed,
        parsed_identity_valid,
        statement_performed,
        statement_integrity_performed,
        statement_integrity_valid,
        statement_valid,
        semantic_performed,
        semantic_valid,
        artifact_contract_valid,
        literal_intent_performed,
        diagnostics,
    ) = branch

    literal_intent_action: _IntentAction | None = None
    literal_intent_is_approval: bool | None = None
    if literal_intent_performed:
        if statement_validation_result is None:
            _invariant_failure()
        literal_intent_action = _field(
            statement_validation_result,
            "intent_action",
        )
        if type(literal_intent_action) is not _IntentAction:
            _invariant_failure()
        literal_intent_is_approval = literal_intent_action is _IntentAction.APPROVE

    artifact_identity: str | None = None
    if _composition_identity_available(composition_state):
        if parser_result is None or statement_validation_result is None:
            _invariant_failure()
        artifact_identity = _composition_identity(
            composition_state=composition_state,
            parser_result=parser_result,
            statement_validation_result=statement_validation_result,
            artifact_contract_valid=artifact_contract_valid,
            literal_intent_evaluation_performed=literal_intent_performed,
            literal_intent_action=literal_intent_action,
            literal_intent_is_approval=literal_intent_is_approval,
            statement_validation_valid=statement_valid,
            statement_semantic_identity_recheck_performed=semantic_performed,
            statement_semantic_identity_recheck_valid=semantic_valid,
        )

    values: dict[str, object] = {
        "result_version": RESULT_VERSION,
        "validator_version": VALIDATOR_VERSION,
        "composition_state": composition_state,
        "parser_result": parser_result,
        "statement_validation_result": statement_validation_result,
        "parser_result_integrity_check_performed": parser_check_performed,
        "parser_result_integrity_valid": parser_check_valid,
        "parsed_value_conversion_performed": conversion_performed,
        "parsed_value_conversion_valid": conversion_valid,
        "parsed_value_identity_recheck_performed": parsed_identity_performed,
        "parsed_value_identity_recheck_valid": parsed_identity_valid,
        "statement_validation_performed": statement_performed,
        "statement_validation_result_integrity_check_performed": (
            statement_integrity_performed
        ),
        "statement_validation_result_integrity_valid": statement_integrity_valid,
        "statement_validation_valid": statement_valid,
        "statement_semantic_identity_recheck_performed": semantic_performed,
        "statement_semantic_identity_recheck_valid": semantic_valid,
        "operator_approval_intent_statement_artifact_validation_identity_sha256": (
            artifact_identity
        ),
        "artifact_validation_identity_computed": artifact_identity is not None,
        "artifact_contract_valid": artifact_contract_valid,
        "literal_intent_evaluation_performed": literal_intent_performed,
        "literal_intent_action": literal_intent_action,
        "literal_intent_is_approval": literal_intent_is_approval,
        "diagnostics": diagnostics,
        "authority_scope": AUTHORITY_SCOPE,
        "not_authentication": True,
        "not_approval_authorization": True,
        "not_activation_authorization": True,
        "not_trade_authorization": True,
        "trade_permission_effect": TRADE_PERMISSION_EFFECT,
        "authentication_evaluation_performed": False,
        "authorship_evaluation_performed": False,
        "freshness_evaluation_performed": False,
        "replay_evaluation_performed": False,
        "lifecycle_evaluation_performed": False,
        "activation_evaluation_performed": False,
        "workflow_permission_evaluated": False,
        "order_compilation_evaluated": False,
    }
    _validate_result_values(values)
    result = object.__new__(
        Step2MarketSourcePolicyOperatorApprovalIntentStatementArtifactValidationResult
    )
    for result_field in fields(
        Step2MarketSourcePolicyOperatorApprovalIntentStatementArtifactValidationResult
    ):
        object.__setattr__(result, result_field.name, values[result_field.name])
    return result


def _parser_result_integrity_valid(result: object, raw_input: object) -> bool:
    if not _parser_result_static_valid(result):
        return False
    state = _field(result, "parse_state")
    raw_size = _field(result, "raw_statement_size_bytes")
    raw_hash = _field(result, "raw_statement_sha256")
    if type(state) is not _ParseState:
        return False
    if raw_input is None:
        return (
            state is _ParseState.INPUT_ABSENT
            and raw_size is None
            and raw_hash is None
        )
    if type(raw_input) is not bytes:
        return (
            state is _ParseState.INPUT_TYPE_INVALID
            and raw_size is None
            and raw_hash is None
        )
    if type(raw_size) is not int or raw_size != len(raw_input):
        return False
    size_valid = MIN_RAW_STATEMENT_BYTES <= raw_size <= MAX_RAW_STATEMENT_BYTES
    if not size_valid:
        return state is _ParseState.RAW_SIZE_INVALID and raw_hash is None
    if state in {
        _ParseState.INPUT_ABSENT,
        _ParseState.INPUT_TYPE_INVALID,
        _ParseState.RAW_SIZE_INVALID,
    }:
        return False
    return _is_exact_string(raw_hash, _sha256_hex(raw_input))


def _parser_result_static_valid(result: object) -> bool:
    if (
        not _upstream_contract_constants_are_frozen()
        or type(result)
        is not Step2MarketSourcePolicyOperatorApprovalIntentStatementParseResult
    ):
        return False
    if (
        not _is_exact_string(_field(result, "result_version"), _P4A1_RESULT_VERSION)
        or not _is_exact_string(
            _field(result, "parser_version"), _P4A1_ENGINE_REVISION
        )
        or not _is_exact_string(
            _field(result, "authority_scope"), "raw_json_parsing_only"
        )
        or not _is_exact_boolean(_field(result, "not_authentication"), True)
        or not _is_exact_boolean(
            _field(result, "not_approval_authorization"), True
        )
        or not _is_exact_boolean(
            _field(result, "not_activation_authorization"), True
        )
        or not _is_exact_boolean(_field(result, "not_trade_authorization"), True)
        or not _is_exact_string(_field(result, "trade_permission_effect"), "none")
    ):
        return False
    for name in (
        "statement_contract_validation_performed",
        "statement_semantic_identity_computed",
        "authentication_evaluation_performed",
        "intent_evaluation_performed",
        "freshness_evaluation_performed",
        "replay_evaluation_performed",
        "lifecycle_evaluation_performed",
        "workflow_permission_evaluated",
        "order_compilation_evaluated",
    ):
        if not _is_exact_boolean(_field(result, name), False):
            return False

    state = _field(result, "parse_state")
    if type(state) is not _ParseState:
        return False
    matrix = _parser_branch_matrix(state)
    if matrix is None:
        return False
    matrix_names = (
        "text_decoding_performed",
        "text_decoding_valid",
        "json_syntax_validation_performed",
        "json_syntax_valid",
        "duplicate_key_validation_performed",
        "duplicate_keys_valid",
        "unicode_scalar_validation_performed",
        "unicode_scalars_valid",
        "structural_bound_validation_performed",
        "structural_bounds_valid",
        "parse_valid",
    )
    if not _exact_matrix_matches(
        tuple(_field(result, name) for name in matrix_names), matrix
    ):
        return False
    diagnostics = _field(result, "diagnostics")
    if type(diagnostics) is not tuple:
        return False
    if len(diagnostics) > MAX_APPROVAL_INTENT_STATEMENT_PARSE_DIAGNOSTICS:
        return False
    if state is _ParseState.VALID:
        if len(diagnostics) != 0:
            return False
    else:
        if len(diagnostics) != 1 or type(diagnostics[0]) is not _ParseDiagnostic:
            return False
        if diagnostics[0] not in _parser_state_diagnostics(state):
            return False

    raw_size = _field(result, "raw_statement_size_bytes")
    raw_hash = _field(result, "raw_statement_sha256")
    if state in {_ParseState.INPUT_ABSENT, _ParseState.INPUT_TYPE_INVALID}:
        if raw_size is not None or raw_hash is not None:
            return False
    else:
        if type(raw_size) is not int or raw_size < 0:
            return False
        size_valid = MIN_RAW_STATEMENT_BYTES <= raw_size <= MAX_RAW_STATEMENT_BYTES
        if (state is _ParseState.RAW_SIZE_INVALID) is not (not size_valid):
            return False
        if state is _ParseState.RAW_SIZE_INVALID:
            if raw_hash is not None:
                return False
        elif not _is_lowercase_sha256(raw_hash):
            return False

    valid = state is _ParseState.VALID
    immutable_value = _field(result, "immutable_parsed_value")
    parsed_identity = _field(result, "parsed_value_identity_sha256")
    if not _is_exact_boolean(_field(result, "parsed_value_available"), valid):
        return False
    if valid:
        if not _is_lowercase_sha256(parsed_identity):
            return False
    elif immutable_value is not None or parsed_identity is not None:
        return False
    return True


def _parser_branch_matrix(
    state: _ParseState,
) -> tuple[bool, bool | None, bool, bool | None, bool, bool | None, bool, bool | None, bool, bool | None, bool | None] | None:
    matrices = {
        _ParseState.INPUT_ABSENT: (
            False, None, False, None, False, None, False, None, False, None, None
        ),
        _ParseState.INPUT_TYPE_INVALID: (
            False, None, False, None, False, None, False, None, False, None, None
        ),
        _ParseState.RAW_SIZE_INVALID: (
            False, None, False, None, False, None, False, None, False, None, None
        ),
        _ParseState.ENCODING_INVALID: (
            True, False, False, None, False, None, False, None, False, None, False
        ),
        _ParseState.JSON_GRAMMAR_INVALID: (
            True, True, True, False, False, None, False, None, False, None, False
        ),
        _ParseState.DUPLICATE_KEY_INVALID: (
            True, True, True, True, True, False, False, None, False, None, False
        ),
        _ParseState.UNICODE_SCALAR_INVALID: (
            True, True, True, True, True, True, True, False, False, None, False
        ),
        _ParseState.RESOURCE_LIMIT_INVALID: (
            True, True, True, True, True, True, True, True, True, False, False
        ),
        _ParseState.VALID: (
            True, True, True, True, True, True, True, True, True, True, True
        ),
    }
    return matrices.get(state)


def _parser_state_diagnostics(state: _ParseState) -> frozenset[_ParseDiagnostic]:
    diagnostics = {
        _ParseState.INPUT_ABSENT: frozenset({_ParseDiagnostic.STATEMENT_INPUT_MISSING}),
        _ParseState.INPUT_TYPE_INVALID: frozenset(
            {_ParseDiagnostic.STATEMENT_INPUT_TYPE_INVALID}
        ),
        _ParseState.RAW_SIZE_INVALID: frozenset(
            {_ParseDiagnostic.STATEMENT_RAW_SIZE_INVALID}
        ),
        _ParseState.ENCODING_INVALID: frozenset(
            {
                _ParseDiagnostic.STATEMENT_UTF8_BOM_UNSUPPORTED,
                _ParseDiagnostic.STATEMENT_UTF8_INVALID,
            }
        ),
        _ParseState.JSON_GRAMMAR_INVALID: frozenset(
            {
                _ParseDiagnostic.STATEMENT_JSON_INVALID,
                _ParseDiagnostic.STATEMENT_TRAILING_CONTENT,
            }
        ),
        _ParseState.DUPLICATE_KEY_INVALID: frozenset(
            {_ParseDiagnostic.STATEMENT_DUPLICATE_KEY}
        ),
        _ParseState.UNICODE_SCALAR_INVALID: frozenset(
            {_ParseDiagnostic.STATEMENT_SURROGATE_INVALID}
        ),
        _ParseState.RESOURCE_LIMIT_INVALID: frozenset(
            {
                _ParseDiagnostic.STATEMENT_DEPTH_LIMIT_EXCEEDED,
                _ParseDiagnostic.STATEMENT_NODE_LIMIT_EXCEEDED,
                _ParseDiagnostic.STATEMENT_CUMULATIVE_STRING_LIMIT_EXCEEDED,
                _ParseDiagnostic.STATEMENT_STRING_LIMIT_EXCEEDED,
                _ParseDiagnostic.STATEMENT_OBJECT_MEMBER_LIMIT_EXCEEDED,
                _ParseDiagnostic.STATEMENT_ARRAY_ITEM_LIMIT_EXCEEDED,
                _ParseDiagnostic.STATEMENT_NUMBER_LIMIT_EXCEEDED,
            }
        ),
        _ParseState.VALID: frozenset(),
    }
    return diagnostics.get(state, frozenset())


def _frozen_tree_is_valid(value: object) -> bool:
    active: set[int] = set()
    seen: set[int] = set()
    node_count = 0
    string_count = 0
    stack: list[tuple[str, object, int]] = [("value", value, 0)]
    while stack:
        operation, current, depth = stack.pop()
        if operation == "leave":
            identifier = id(current)
            if identifier not in active:
                return False
            active.remove(identifier)
            continue
        if operation != "value":
            return False
        if depth > MAX_JSON_NESTING_DEPTH or node_count >= MAX_JSON_NODE_COUNT:
            return False
        node_count += 1
        if current is None or type(current) in {int, bool}:
            continue
        if type(current) is str:
            checked = _next_string_count(current, string_count)
            if checked is None:
                return False
            string_count = checked
            continue
        if type(current) is float:
            if not math.isfinite(current):
                return False
            continue
        if type(current) is FrozenApprovalIntentJsonArray:
            identifier = id(current)
            items = _field(current, "items")
            if (
                identifier in active
                or identifier in seen
                or type(items) is not tuple
                or len(items) > MAX_ARRAY_ITEM_COUNT
            ):
                return False
            active.add(identifier)
            seen.add(identifier)
            stack.append(("leave", current, depth))
            for child in reversed(items):
                stack.append(("value", child, depth + 1))
            continue
        if type(current) is FrozenApprovalIntentJsonObject:
            identifier = id(current)
            items = _field(current, "items")
            if (
                identifier in active
                or identifier in seen
                or type(items) is not tuple
                or len(items) > MAX_OBJECT_MEMBER_COUNT
            ):
                return False
            keys: set[str] = set()
            for item in items:
                if type(item) is not tuple or len(item) != 2:
                    return False
                key, child = item
                if type(key) is not str or key in keys:
                    return False
                checked = _next_string_count(key, string_count)
                if checked is None:
                    return False
                string_count = checked
                keys.add(key)
                if child is _MISSING:
                    return False
            active.add(identifier)
            seen.add(identifier)
            stack.append(("leave", current, depth))
            for _, child in reversed(items):
                stack.append(("value", child, depth + 1))
            continue
        return False
    return not active


def _next_string_count(value: str, current: int) -> int | None:
    if type(value) is not str or any(0xD800 <= ord(character) <= 0xDFFF for character in value):
        return None
    next_count = current + len(value)
    if len(value) > MAX_INDIVIDUAL_STRING_CODE_POINTS or next_count > (
        MAX_CUMULATIVE_STRING_CODE_POINTS
    ):
        return None
    return next_count


def _convert_frozen_value(value: object) -> object:
    root_holder: list[object] = [_UNSET]
    stack: list[tuple[object, object | None, str | int | None]] = [
        (value, None, None)
    ]
    while stack:
        current, parent, destination = stack.pop()
        if current is None or type(current) in {str, int, float, bool}:
            _assign_converted(root_holder, parent, destination, current)
            continue
        if type(current) is FrozenApprovalIntentJsonArray:
            items = _field(current, "items")
            if type(items) is not tuple:
                _invariant_failure()
            converted_array: list[object] = [None] * len(items)
            _assign_converted(root_holder, parent, destination, converted_array)
            for index in range(len(items) - 1, -1, -1):
                stack.append((items[index], converted_array, index))
            continue
        if type(current) is FrozenApprovalIntentJsonObject:
            items = _field(current, "items")
            if type(items) is not tuple:
                _invariant_failure()
            converted_object: dict[str, object] = {}
            _assign_converted(root_holder, parent, destination, converted_object)
            for item in reversed(items):
                if type(item) is not tuple or len(item) != 2:
                    _invariant_failure()
                key, child = item
                if type(key) is not str:
                    _invariant_failure()
                stack.append((child, converted_object, key))
            continue
        _invariant_failure()
    converted = root_holder[0]
    if converted is _UNSET or type(converted) not in _EXACT_JSON_DECODED_TYPES:
        _invariant_failure()
    return converted


def _assign_converted(
    root_holder: list[object],
    parent: object | None,
    destination: str | int | None,
    value: object,
) -> None:
    if parent is None:
        if root_holder[0] is not _UNSET:
            _invariant_failure()
        root_holder[0] = value
        return
    if type(parent) is list and type(destination) is int:
        if not 0 <= destination < len(parent):
            _invariant_failure()
        parent[destination] = value
        return
    if type(parent) is dict and type(destination) is str:
        if destination in parent:
            _invariant_failure()
        parent[destination] = value
        return
    _invariant_failure()


def _canonical_json_utf8(value: object) -> bytes:
    serialized = json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    if type(serialized) is not str:
        _invariant_failure()
    return serialized.encode("utf-8")


def _statement_result_integrity_valid(result: object, value: object) -> bool:
    if not _statement_result_static_valid(result):
        return False
    expected_state, expected_diagnostic, expected_action = _expected_statement_outcome(
        value
    )
    state = _field(result, "validation_state")
    diagnostics = _field(result, "diagnostics")
    if type(state) is not _StatementState or state is not expected_state:
        return False
    if not _diagnostics_match_statement_outcome(diagnostics, expected_diagnostic):
        return False
    if expected_action is None:
        return _field(result, "validated_statement") is None
    statement = _field(result, "validated_statement")
    if (
        type(value) is not dict
        or type(statement) is not Step2MarketSourcePolicyOperatorApprovalIntentStatement
    ):
        return False
    return (
        _is_exact_string(
            _field(statement, "statement_schema_version"),
            value["statement_schema_version"],
        )
        and _is_exact_string(
            _field(statement, "authentication_context_version"),
            value["authentication_context_version"],
        )
        and _is_exact_string(
            _field(statement, "expected_identity_binding_sha256"),
            value["expected_identity_binding_sha256"],
        )
        and _field(statement, "intent_action") is expected_action
        and _is_exact_string(
            _field(statement, "provenance_identity_sha256"),
            value["provenance_identity_sha256"],
        )
    )


def _statement_result_static_valid(result: object) -> bool:
    if (
        not _upstream_contract_constants_are_frozen()
        or type(result)
        is not Step2MarketSourcePolicyOperatorApprovalIntentStatementValidationResult
    ):
        return False
    if (
        not _is_exact_string(_field(result, "result_version"), _P4A2_RESULT_VERSION)
        or not _is_exact_string(
            _field(result, "validator_version"), _P4A2_VALIDATOR_VERSION
        )
        or not _is_exact_string(
            _field(
                result,
                "authority_scope",
            ),
            "operator_approval_intent_statement_contract_validation_only",
        )
        or not _is_exact_boolean(_field(result, "not_authentication"), True)
        or not _is_exact_boolean(
            _field(result, "not_approval_authorization"), True
        )
        or not _is_exact_boolean(
            _field(result, "not_activation_authorization"), True
        )
        or not _is_exact_boolean(_field(result, "not_trade_authorization"), True)
        or not _is_exact_string(_field(result, "trade_permission_effect"), "none")
    ):
        return False
    for name in (
        "authentication_evaluation_performed",
        "authorship_evaluation_performed",
        "freshness_evaluation_performed",
        "replay_evaluation_performed",
        "lifecycle_evaluation_performed",
        "activation_evaluation_performed",
        "workflow_permission_evaluated",
        "order_compilation_evaluated",
    ):
        if not _is_exact_boolean(_field(result, name), False):
            return False

    state = _field(result, "validation_state")
    if type(state) is not _StatementState:
        return False
    matrix = _statement_branch_matrix(state)
    if matrix is None:
        return False
    matrix_names = (
        "input_type_check_performed",
        "input_type_valid",
        "root_type_check_performed",
        "root_type_valid",
        "key_set_validation_performed",
        "key_set_valid",
        "field_validation_performed",
        "field_validation_valid",
        "semantic_identity_computed",
        "statement_contract_valid",
        "intent_evaluation_performed",
    )
    if not _exact_matrix_matches(
        tuple(_field(result, name) for name in matrix_names), matrix
    ):
        return False
    diagnostics = _field(result, "diagnostics")
    if type(diagnostics) is not tuple:
        return False
    if len(diagnostics) > MAX_OPERATOR_APPROVAL_INTENT_STATEMENT_VALIDATION_DIAGNOSTICS:
        return False
    valid = state in {_StatementState.VALID_APPROVE, _StatementState.VALID_REJECT}
    statement = _field(result, "validated_statement")
    identity = _field(result, "operator_approval_intent_statement_identity_sha256")
    intent_action = _field(result, "intent_action")
    intent_is_approval = _field(result, "intent_is_approval")
    if valid:
        expected_action = (
            _IntentAction.APPROVE
            if state is _StatementState.VALID_APPROVE
            else _IntentAction.REJECT
        )
        if (
            len(diagnostics) != 0
            or not _snapshot_is_valid(statement, expected_action)
            or not _is_lowercase_sha256(identity)
            or type(intent_action) is not _IntentAction
            or intent_action is not expected_action
            or not _is_exact_boolean(
                intent_is_approval,
                expected_action is _IntentAction.APPROVE,
            )
        ):
            return False
        return True
    if (
        statement is not None
        or identity is not None
        or intent_action is not None
        or intent_is_approval is not None
        or type(diagnostics) is not tuple
        or len(diagnostics) != 1
        or type(diagnostics[0]) is not _StatementDiagnostic
        or diagnostics[0] not in _statement_state_diagnostics(state)
    ):
        return False
    return True


def _statement_branch_matrix(
    state: _StatementState,
) -> tuple[bool, bool, bool, bool | None, bool, bool | None, bool, bool | None, bool, bool | None, bool] | None:
    valid = state in {_StatementState.VALID_APPROVE, _StatementState.VALID_REJECT}
    if state is _StatementState.INPUT_TYPE_INVALID:
        return (True, False, False, None, False, None, False, None, False, None, False)
    if state is _StatementState.ROOT_TYPE_INVALID:
        return (True, True, True, False, False, None, False, None, False, None, False)
    if state is _StatementState.KEY_SET_INVALID:
        return (True, True, True, True, True, False, False, None, False, False, False)
    if state in {_StatementState.FIELD_TYPE_INVALID, _StatementState.FIELD_VALUE_INVALID}:
        return (True, True, True, True, True, True, True, False, False, False, False)
    if valid:
        return (True, True, True, True, True, True, True, True, True, True, True)
    return None


def _statement_state_diagnostics(
    state: _StatementState,
) -> frozenset[_StatementDiagnostic]:
    diagnostics = {
        _StatementState.INPUT_TYPE_INVALID: frozenset(
            {_StatementDiagnostic.STATEMENT_INPUT_TYPE_INVALID}
        ),
        _StatementState.ROOT_TYPE_INVALID: frozenset(
            {_StatementDiagnostic.STATEMENT_ROOT_TYPE_INVALID}
        ),
        _StatementState.KEY_SET_INVALID: frozenset(
            {_StatementDiagnostic.STATEMENT_KEY_SET_INVALID}
        ),
        _StatementState.FIELD_TYPE_INVALID: frozenset(
            {
                _StatementDiagnostic.STATEMENT_SCHEMA_VERSION_INVALID,
                _StatementDiagnostic.AUTHENTICATION_CONTEXT_TYPE_INVALID,
                _StatementDiagnostic.EXPECTED_IDENTITY_BINDING_TYPE_INVALID,
                _StatementDiagnostic.INTENT_ACTION_TYPE_INVALID,
                _StatementDiagnostic.PROVENANCE_IDENTITY_TYPE_INVALID,
            }
        ),
        _StatementState.FIELD_VALUE_INVALID: frozenset(
            {
                _StatementDiagnostic.STATEMENT_SCHEMA_VERSION_INVALID,
                _StatementDiagnostic.AUTHENTICATION_CONTEXT_SYNTAX_INVALID,
                _StatementDiagnostic.AUTHENTICATION_CONTEXT_RESERVED,
                _StatementDiagnostic.EXPECTED_IDENTITY_BINDING_SYNTAX_INVALID,
                _StatementDiagnostic.INTENT_ACTION_VALUE_INVALID,
                _StatementDiagnostic.PROVENANCE_IDENTITY_SYNTAX_INVALID,
            }
        ),
        _StatementState.VALID_APPROVE: frozenset(),
        _StatementState.VALID_REJECT: frozenset(),
    }
    return diagnostics.get(state, frozenset())


def _snapshot_is_valid(value: object, action: _IntentAction) -> bool:
    if (
        type(value) is not Step2MarketSourcePolicyOperatorApprovalIntentStatement
        or type(action) is not _IntentAction
    ):
        return False
    schema_version = _field(value, "statement_schema_version")
    context_version = _field(value, "authentication_context_version")
    expected_identity = _field(value, "expected_identity_binding_sha256")
    intent_action = _field(value, "intent_action")
    provenance_identity = _field(value, "provenance_identity_sha256")
    return (
        _is_exact_string(schema_version, STATEMENT_SCHEMA_VERSION)
        and _authentication_context_diagnostic(context_version) is None
        and _is_lowercase_sha256(expected_identity)
        and type(intent_action) is _IntentAction
        and intent_action is action
        and _is_lowercase_sha256(provenance_identity)
    )


def _expected_statement_outcome(
    value: object,
) -> tuple[_StatementState, _StatementDiagnostic | None, _IntentAction | None]:
    value_type = type(value)
    if value_type not in _EXACT_JSON_DECODED_TYPES:
        return (
            _StatementState.INPUT_TYPE_INVALID,
            _StatementDiagnostic.STATEMENT_INPUT_TYPE_INVALID,
            None,
        )
    if value_type is not dict:
        return (
            _StatementState.ROOT_TYPE_INVALID,
            _StatementDiagnostic.STATEMENT_ROOT_TYPE_INVALID,
            None,
        )
    if len(value) != len(_REQUIRED_KEYS):
        return (
            _StatementState.KEY_SET_INVALID,
            _StatementDiagnostic.STATEMENT_KEY_SET_INVALID,
            None,
        )
    keys = tuple(value)
    if any(type(key) is not str for key in keys) or frozenset(keys) != _REQUIRED_KEYS:
        return (
            _StatementState.KEY_SET_INVALID,
            _StatementDiagnostic.STATEMENT_KEY_SET_INVALID,
            None,
        )

    schema = value["statement_schema_version"]
    if type(schema) is not str:
        return (
            _StatementState.FIELD_TYPE_INVALID,
            _StatementDiagnostic.STATEMENT_SCHEMA_VERSION_INVALID,
            None,
        )
    if not _is_exact_string(schema, STATEMENT_SCHEMA_VERSION):
        return (
            _StatementState.FIELD_VALUE_INVALID,
            _StatementDiagnostic.STATEMENT_SCHEMA_VERSION_INVALID,
            None,
        )
    context_diagnostic = _authentication_context_diagnostic(
        value["authentication_context_version"]
    )
    if context_diagnostic is not None:
        return _state_for_statement_diagnostic(context_diagnostic)
    expected_diagnostic = _digest_diagnostic(
        value["expected_identity_binding_sha256"],
        _StatementDiagnostic.EXPECTED_IDENTITY_BINDING_TYPE_INVALID,
        _StatementDiagnostic.EXPECTED_IDENTITY_BINDING_SYNTAX_INVALID,
    )
    if expected_diagnostic is not None:
        return _state_for_statement_diagnostic(expected_diagnostic)
    raw_intent = value["intent_action"]
    if type(raw_intent) is not str:
        return (
            _StatementState.FIELD_TYPE_INVALID,
            _StatementDiagnostic.INTENT_ACTION_TYPE_INVALID,
            None,
        )
    if _is_exact_string(raw_intent, _IntentAction.APPROVE.value):
        action = _IntentAction.APPROVE
    elif _is_exact_string(raw_intent, _IntentAction.REJECT.value):
        action = _IntentAction.REJECT
    else:
        return (
            _StatementState.FIELD_VALUE_INVALID,
            _StatementDiagnostic.INTENT_ACTION_VALUE_INVALID,
            None,
        )
    provenance_diagnostic = _digest_diagnostic(
        value["provenance_identity_sha256"],
        _StatementDiagnostic.PROVENANCE_IDENTITY_TYPE_INVALID,
        _StatementDiagnostic.PROVENANCE_IDENTITY_SYNTAX_INVALID,
    )
    if provenance_diagnostic is not None:
        return _state_for_statement_diagnostic(provenance_diagnostic)
    return (
        _StatementState.VALID_APPROVE
        if action is _IntentAction.APPROVE
        else _StatementState.VALID_REJECT,
        None,
        action,
    )


def _authentication_context_diagnostic(value: object) -> _StatementDiagnostic | None:
    if type(value) is not str:
        return _StatementDiagnostic.AUTHENTICATION_CONTEXT_TYPE_INVALID
    if not 1 <= len(value) <= 64 or not value.isascii():
        return _StatementDiagnostic.AUTHENTICATION_CONTEXT_SYNTAX_INVALID
    if value in _RESERVED_AUTHENTICATION_CONTEXT_VERSIONS:
        return _StatementDiagnostic.AUTHENTICATION_CONTEXT_RESERVED
    if _AUTHENTICATION_CONTEXT_VERSION_PATTERN.fullmatch(value) is None:
        return _StatementDiagnostic.AUTHENTICATION_CONTEXT_SYNTAX_INVALID
    return None


def _digest_diagnostic(
    value: object,
    type_diagnostic: _StatementDiagnostic,
    syntax_diagnostic: _StatementDiagnostic,
) -> _StatementDiagnostic | None:
    if type(value) is not str:
        return type_diagnostic
    return None if _is_lowercase_sha256(value) else syntax_diagnostic


def _state_for_statement_diagnostic(
    diagnostic: _StatementDiagnostic,
) -> tuple[_StatementState, _StatementDiagnostic, None]:
    if diagnostic in {
        _StatementDiagnostic.AUTHENTICATION_CONTEXT_TYPE_INVALID,
        _StatementDiagnostic.EXPECTED_IDENTITY_BINDING_TYPE_INVALID,
        _StatementDiagnostic.INTENT_ACTION_TYPE_INVALID,
        _StatementDiagnostic.PROVENANCE_IDENTITY_TYPE_INVALID,
    }:
        return (_StatementState.FIELD_TYPE_INVALID, diagnostic, None)
    return (_StatementState.FIELD_VALUE_INVALID, diagnostic, None)


def _semantic_identity_matches(
    result: Step2MarketSourcePolicyOperatorApprovalIntentStatementValidationResult,
) -> bool:
    if not _statement_result_static_valid(result):
        _invariant_failure()
    state = _field(result, "validation_state")
    if state is _StatementState.VALID_APPROVE:
        action = _IntentAction.APPROVE
    elif state is _StatementState.VALID_REJECT:
        action = _IntentAction.REJECT
    else:
        _invariant_failure()
    snapshot = _field(result, "validated_statement")
    stored_identity = _field(
        result, "operator_approval_intent_statement_identity_sha256"
    )
    if not _snapshot_is_valid(snapshot, action):
        _invariant_failure()
    if not _is_lowercase_sha256(stored_identity):
        _invariant_failure()
    canonical = _canonical_statement_utf8(snapshot)
    if len(canonical) > MAX_OPERATOR_APPROVAL_INTENT_STATEMENT_CANONICAL_BYTES:
        _invariant_failure()
    return _is_exact_string(
        stored_identity,
        _sha256_hex(_P4A2_SEMANTIC_DOMAIN + canonical),
    )


def _canonical_statement_utf8(
    snapshot: Step2MarketSourcePolicyOperatorApprovalIntentStatement,
) -> bytes:
    if type(snapshot) is not Step2MarketSourcePolicyOperatorApprovalIntentStatement:
        _invariant_failure()
    action = _field(snapshot, "intent_action")
    if type(action) is not _IntentAction or not _snapshot_is_valid(snapshot, action):
        _invariant_failure()
    record = {
        "authentication_context_version": _field(
            snapshot, "authentication_context_version"
        ),
        "expected_identity_binding_sha256": _field(
            snapshot, "expected_identity_binding_sha256"
        ),
        "intent_action": action.value,
        "provenance_identity_sha256": _field(
            snapshot, "provenance_identity_sha256"
        ),
        "statement_schema_version": _field(snapshot, "statement_schema_version"),
    }
    serialized = json.dumps(
        record,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    if type(serialized) is not str:
        _invariant_failure()
    return serialized.encode("utf-8")


def _composition_identity(
    *,
    composition_state: _ArtifactState,
    parser_result: Step2MarketSourcePolicyOperatorApprovalIntentStatementParseResult,
    statement_validation_result: Step2MarketSourcePolicyOperatorApprovalIntentStatementValidationResult,
    artifact_contract_valid: bool | None,
    literal_intent_evaluation_performed: bool,
    literal_intent_action: _IntentAction | None,
    literal_intent_is_approval: bool | None,
    statement_validation_valid: bool | None,
    statement_semantic_identity_recheck_performed: bool,
    statement_semantic_identity_recheck_valid: bool | None,
) -> str:
    if (
        type(composition_state) is not _ArtifactState
        or not _parser_result_static_valid(parser_result)
        or not _statement_result_static_valid(statement_validation_result)
        or type(artifact_contract_valid) is not bool
        or type(literal_intent_evaluation_performed) is not bool
        or type(statement_validation_valid) is not bool
        or type(statement_semantic_identity_recheck_performed) is not bool
        or (
            literal_intent_action is not None
            and type(literal_intent_action) is not _IntentAction
        )
        or (
            literal_intent_is_approval is not None
            and type(literal_intent_is_approval) is not bool
        )
        or (
            statement_semantic_identity_recheck_valid is not None
            and type(statement_semantic_identity_recheck_valid) is not bool
        )
    ):
        _invariant_failure()
    parser_state = _field(parser_result, "parse_state")
    parsed_identity = _field(parser_result, "parsed_value_identity_sha256")
    parser_version = _field(parser_result, "parser_version")
    raw_hash = _field(parser_result, "raw_statement_sha256")
    raw_size = _field(parser_result, "raw_statement_size_bytes")
    parser_result_version = _field(parser_result, "result_version")
    statement_diagnostics = _field(statement_validation_result, "diagnostics")
    statement_identity = _field(
        statement_validation_result,
        "operator_approval_intent_statement_identity_sha256",
    )
    statement_result_version = _field(statement_validation_result, "result_version")
    statement_contract_valid = _field(
        statement_validation_result, "statement_contract_valid"
    )
    statement_state = _field(statement_validation_result, "validation_state")
    statement_validator_version = _field(
        statement_validation_result, "validator_version"
    )
    record = {
        "artifact_contract_valid": artifact_contract_valid,
        "composition_checks": {
            "literal_intent_evaluation_performed": literal_intent_evaluation_performed,
            "parser_result_integrity_check_performed": True,
            "parser_result_integrity_valid": True,
            "parsed_value_conversion_performed": True,
            "parsed_value_conversion_valid": True,
            "parsed_value_identity_recheck_performed": True,
            "parsed_value_identity_recheck_valid": True,
            "statement_semantic_identity_recheck_performed": (
                statement_semantic_identity_recheck_performed
            ),
            "statement_semantic_identity_recheck_valid": (
                statement_semantic_identity_recheck_valid
            ),
            "statement_validation_performed": True,
            "statement_validation_result_integrity_check_performed": True,
            "statement_validation_result_integrity_valid": True,
            "statement_validation_valid": statement_validation_valid,
        },
        "composition_state": composition_state.value,
        "literal_intent_action": (
            None if literal_intent_action is None else literal_intent_action.value
        ),
        "literal_intent_is_approval": literal_intent_is_approval,
        "parser_result": {
            "parse_state": parser_state.value,
            "parsed_value_identity_sha256": parsed_identity,
            "parser_version": parser_version,
            "raw_statement_sha256": raw_hash,
            "raw_statement_size_bytes": raw_size,
            "result_version": parser_result_version,
        },
        "result_version": RESULT_VERSION,
        "statement_validation_result": {
            "diagnostics": [
                diagnostic.value for diagnostic in statement_diagnostics
            ],
            "operator_approval_intent_statement_identity_sha256": statement_identity,
            "result_version": statement_result_version,
            "statement_contract_valid": statement_contract_valid,
            "validation_state": statement_state.value,
            "validator_version": statement_validator_version,
        },
        "validator_version": VALIDATOR_VERSION,
    }
    serialized = json.dumps(
        record,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    if type(serialized) is not str:
        _invariant_failure()
    canonical = serialized.encode("utf-8")
    if len(canonical) > (
        MAX_OPERATOR_APPROVAL_INTENT_STATEMENT_ARTIFACT_VALIDATION_CANONICAL_BYTES
    ):
        _invariant_failure()
    return _sha256_hex(
        OPERATOR_APPROVAL_INTENT_STATEMENT_ARTIFACT_VALIDATION_IDENTITY_DOMAIN
        + canonical
    )


def _branch_values(
    state: _ArtifactState,
) -> tuple[
    bool,
    bool | None,
    bool,
    bool | None,
    bool,
    bool | None,
    bool,
    bool,
    bool | None,
    bool | None,
    bool,
    bool | None,
    bool | None,
    bool,
    tuple[_ArtifactDiagnostic, ...],
]:
    matrices = {
        _ArtifactState.RAW_PARSE_INVALID: (
            True, True, False, None, False, None, False, False, None, None,
            False, None, False, False,
        ),
        _ArtifactState.PARSER_RESULT_INTEGRITY_INVALID: (
            True, False, False, None, False, None, False, False, None, None,
            False, None, None, False,
        ),
        _ArtifactState.PARSED_VALUE_IDENTITY_BINDING_INVALID: (
            True, True, True, True, True, False, False, False, None, None,
            False, None, False, False,
        ),
        _ArtifactState.STATEMENT_CONTRACT_INVALID: (
            True, True, True, True, True, True, True, True, True, False,
            False, None, False, False,
        ),
        _ArtifactState.STATEMENT_VALIDATION_RESULT_INTEGRITY_INVALID: (
            True, True, True, True, True, True, True, False, None, None,
            False, None, None, False,
        ),
        _ArtifactState.STATEMENT_SEMANTIC_IDENTITY_BINDING_INVALID: (
            True, True, True, True, True, True, True, True, True, True,
            True, False, False, False,
        ),
        _ArtifactState.VALID_APPROVE: (
            True, True, True, True, True, True, True, True, True, True,
            True, True, True, True,
        ),
        _ArtifactState.VALID_REJECT: (
            True, True, True, True, True, True, True, True, True, True,
            True, True, True, True,
        ),
    }
    diagnostics = {
        _ArtifactState.RAW_PARSE_INVALID: (
            _ArtifactDiagnostic.RAW_STATEMENT_PARSE_INVALID,
        ),
        _ArtifactState.PARSER_RESULT_INTEGRITY_INVALID: (
            _ArtifactDiagnostic.PARSER_RESULT_INTEGRITY_INVALID,
        ),
        _ArtifactState.PARSED_VALUE_IDENTITY_BINDING_INVALID: (
            _ArtifactDiagnostic.PARSED_VALUE_IDENTITY_BINDING_INVALID,
        ),
        _ArtifactState.STATEMENT_CONTRACT_INVALID: (
            _ArtifactDiagnostic.STATEMENT_CONTRACT_INVALID,
        ),
        _ArtifactState.STATEMENT_VALIDATION_RESULT_INTEGRITY_INVALID: (
            _ArtifactDiagnostic.STATEMENT_VALIDATION_RESULT_INTEGRITY_INVALID,
        ),
        _ArtifactState.STATEMENT_SEMANTIC_IDENTITY_BINDING_INVALID: (
            _ArtifactDiagnostic.STATEMENT_SEMANTIC_IDENTITY_BINDING_INVALID,
        ),
        _ArtifactState.VALID_APPROVE: (),
        _ArtifactState.VALID_REJECT: (),
    }
    if state not in matrices or state not in diagnostics:
        _invariant_failure()
    return (*matrices[state], diagnostics[state])


def _retention_requirements(state: _ArtifactState) -> tuple[bool, bool]:
    requirements = {
        _ArtifactState.RAW_PARSE_INVALID: (True, False),
        _ArtifactState.PARSER_RESULT_INTEGRITY_INVALID: (False, False),
        _ArtifactState.PARSED_VALUE_IDENTITY_BINDING_INVALID: (False, False),
        _ArtifactState.STATEMENT_CONTRACT_INVALID: (True, True),
        _ArtifactState.STATEMENT_VALIDATION_RESULT_INTEGRITY_INVALID: (True, False),
        _ArtifactState.STATEMENT_SEMANTIC_IDENTITY_BINDING_INVALID: (True, False),
        _ArtifactState.VALID_APPROVE: (True, True),
        _ArtifactState.VALID_REJECT: (True, True),
    }
    try:
        return requirements[state]
    except KeyError:
        _invariant_failure()


def _composition_identity_available(state: _ArtifactState) -> bool:
    return state in {
        _ArtifactState.STATEMENT_CONTRACT_INVALID,
        _ArtifactState.VALID_APPROVE,
        _ArtifactState.VALID_REJECT,
    }


def _verified_retained_parser_conversion(
    parser_result: Step2MarketSourcePolicyOperatorApprovalIntentStatementParseResult,
) -> object:
    """Rebuild and independently bind one retained valid p4a1 value."""

    if not _parser_result_static_valid(parser_result):
        _invariant_failure()
    if _field(parser_result, "parse_state") is not _ParseState.VALID:
        _invariant_failure()
    frozen_value = _field(parser_result, "immutable_parsed_value")
    if not _frozen_tree_is_valid(frozen_value):
        _invariant_failure()
    converted_value = _convert_frozen_value(frozen_value)
    canonical = _canonical_json_utf8(converted_value)
    if len(canonical) > MAX_PARSED_VALUE_CANONICAL_BYTES:
        _invariant_failure()
    stored_identity = _field(parser_result, "parsed_value_identity_sha256")
    if not _is_exact_string(
        stored_identity,
        _sha256_hex(_P4A1_PARSED_VALUE_DOMAIN + canonical),
    ):
        _invariant_failure()
    return converted_value


def _validate_retained_state(
    state: _ArtifactState,
    parser_result: Step2MarketSourcePolicyOperatorApprovalIntentStatementParseResult
    | None,
    statement_result: Step2MarketSourcePolicyOperatorApprovalIntentStatementValidationResult
    | None,
) -> None:
    """Prove all retained dependency outputs support this sealed branch.

    Valid p4a2 results carry a sealed snapshot and semantic identity, so they
    are bound field-for-field to the reconstructed p4a1 value.  Invalid p4a2
    results deliberately retain neither invalid input nor provenance; their
    strongest available proof is exact observable-outcome compatibility.
    """

    converted_value: object = _MISSING
    if parser_result is not None:
        if not _parser_result_static_valid(parser_result):
            _invariant_failure()
        parser_state = _field(parser_result, "parse_state")
        if state is _ArtifactState.RAW_PARSE_INVALID:
            if parser_state is _ParseState.VALID:
                _invariant_failure()
        else:
            converted_value = _verified_retained_parser_conversion(parser_result)
    if statement_result is not None:
        if parser_result is None or converted_value is _MISSING:
            _invariant_failure()
        if not _statement_result_integrity_valid(statement_result, converted_value):
            _invariant_failure()
        statement_state = _field(statement_result, "validation_state")
        expected_state, _, expected_action = _expected_statement_outcome(
            converted_value
        )
        if statement_state is not expected_state:
            _invariant_failure()
        if state is _ArtifactState.STATEMENT_CONTRACT_INVALID:
            if (
                statement_state is not _StatementState.ROOT_TYPE_INVALID
                and statement_state is not _StatementState.KEY_SET_INVALID
                and statement_state is not _StatementState.FIELD_TYPE_INVALID
                and statement_state is not _StatementState.FIELD_VALUE_INVALID
            ) or expected_action is not None:
                _invariant_failure()
        elif state is _ArtifactState.VALID_APPROVE:
            if (
                statement_state is not _StatementState.VALID_APPROVE
                or expected_action is not _IntentAction.APPROVE
                or not _semantic_identity_matches(statement_result)
            ):
                _invariant_failure()
        elif state is _ArtifactState.VALID_REJECT:
            if (
                statement_state is not _StatementState.VALID_REJECT
                or expected_action is not _IntentAction.REJECT
                or not _semantic_identity_matches(statement_result)
            ):
                _invariant_failure()
        else:
            _invariant_failure()


def _validate_result_values(values: dict[str, object]) -> None:
    expected_names = tuple(
        field.name
        for field in fields(
            Step2MarketSourcePolicyOperatorApprovalIntentStatementArtifactValidationResult
        )
    )
    if tuple(values) != expected_names:
        _invariant_failure()
    state = values["composition_state"]
    if type(state) is not _ArtifactState:
        _invariant_failure()
    expected = _branch_values(state)
    actual = (
        values["parser_result_integrity_check_performed"],
        values["parser_result_integrity_valid"],
        values["parsed_value_conversion_performed"],
        values["parsed_value_conversion_valid"],
        values["parsed_value_identity_recheck_performed"],
        values["parsed_value_identity_recheck_valid"],
        values["statement_validation_performed"],
        values["statement_validation_result_integrity_check_performed"],
        values["statement_validation_result_integrity_valid"],
        values["statement_validation_valid"],
        values["statement_semantic_identity_recheck_performed"],
        values["statement_semantic_identity_recheck_valid"],
        values["artifact_contract_valid"],
        values["literal_intent_evaluation_performed"],
        values["diagnostics"],
    )
    if not _exact_matrix_matches(actual, expected):
        _invariant_failure()
    parser_required, statement_required = _retention_requirements(state)
    parser_result = values["parser_result"]
    statement_result = values["statement_validation_result"]
    if (parser_result is not None) is not parser_required:
        _invariant_failure()
    if (statement_result is not None) is not statement_required:
        _invariant_failure()
    identity = values[
        "operator_approval_intent_statement_artifact_validation_identity_sha256"
    ]
    identity_available = _composition_identity_available(state)
    if (identity is not None) is not identity_available:
        _invariant_failure()
    if values["artifact_validation_identity_computed"] is not (identity is not None):
        _invariant_failure()
    if identity is not None and not _is_lowercase_sha256(identity):
        _invariant_failure()
    literal_action = values["literal_intent_action"]
    literal_is_approval = values["literal_intent_is_approval"]
    if values["literal_intent_evaluation_performed"] is True:
        expected_action = (
            _IntentAction.APPROVE
            if state is _ArtifactState.VALID_APPROVE
            else _IntentAction.REJECT
            if state is _ArtifactState.VALID_REJECT
            else None
        )
        if literal_action is not expected_action:
            _invariant_failure()
        if literal_is_approval is not (state is _ArtifactState.VALID_APPROVE):
            _invariant_failure()
    elif literal_action is not None or literal_is_approval is not None:
        _invariant_failure()
    if (
        not _is_exact_string(values["result_version"], RESULT_VERSION)
        or not _is_exact_string(values["validator_version"], VALIDATOR_VERSION)
        or not _is_exact_string(values["authority_scope"], AUTHORITY_SCOPE)
        or not _is_exact_string(
            values["trade_permission_effect"],
            TRADE_PERMISSION_EFFECT,
        )
    ):
        _invariant_failure()
    for marker in (
        "not_authentication",
        "not_approval_authorization",
        "not_activation_authorization",
        "not_trade_authorization",
    ):
        if not _is_exact_boolean(values[marker], True):
            _invariant_failure()
    for marker in (
        "authentication_evaluation_performed",
        "authorship_evaluation_performed",
        "freshness_evaluation_performed",
        "replay_evaluation_performed",
        "lifecycle_evaluation_performed",
        "activation_evaluation_performed",
        "workflow_permission_evaluated",
        "order_compilation_evaluated",
    ):
        if not _is_exact_boolean(values[marker], False):
            _invariant_failure()
    diagnostics = values["diagnostics"]
    if (
        type(diagnostics) is not tuple
        or len(diagnostics)
        > MAX_OPERATOR_APPROVAL_INTENT_STATEMENT_ARTIFACT_VALIDATION_DIAGNOSTICS
        or any(type(item) is not _ArtifactDiagnostic for item in diagnostics)
    ):
        _invariant_failure()


def _field(value: object, name: str) -> object:
    return getattr(value, name, _MISSING)


def _is_exact_string(value: object, expected: str) -> bool:
    return type(value) is str and type(expected) is str and value == expected


def _is_exact_boolean(value: object, expected: bool) -> bool:
    return type(value) is bool and type(expected) is bool and value is expected


def _diagnostics_match_statement_outcome(
    diagnostics: object,
    expected: _StatementDiagnostic | None,
) -> bool:
    if type(diagnostics) is not tuple:
        return False
    if expected is None:
        return len(diagnostics) == 0
    return (
        len(diagnostics) == 1
        and type(diagnostics[0]) is _StatementDiagnostic
        and diagnostics[0] is expected
    )


def _exact_matrix_matches(
    actual: tuple[object, ...],
    expected: tuple[object, ...],
) -> bool:
    if type(actual) is not tuple or type(expected) is not tuple:
        return False
    if len(actual) != len(expected):
        return False
    for actual_value, expected_value in zip(actual, expected, strict=True):
        if expected_value is None:
            if actual_value is not None:
                return False
        elif type(expected_value) is bool:
            if not _is_exact_boolean(actual_value, expected_value):
                return False
        elif type(expected_value) is tuple:
            if type(actual_value) is not tuple or len(actual_value) != len(
                expected_value
            ):
                return False
            for actual_diagnostic, expected_diagnostic in zip(
                actual_value,
                expected_value,
                strict=True,
            ):
                if (
                    type(actual_diagnostic) is not _ArtifactDiagnostic
                    or actual_diagnostic is not expected_diagnostic
                ):
                    return False
        else:
            _invariant_failure()
    return True


def _is_lowercase_sha256(value: object) -> bool:
    return type(value) is str and len(value) == 64 and all(
        "0" <= character <= "9" or "a" <= character <= "f"
        for character in value
    )


def _sha256_hex(value: bytes) -> str:
    digest = hashlib.sha256(value).hexdigest()
    if not _is_lowercase_sha256(digest):
        _invariant_failure()
    return digest


def _invariant_failure() -> NoReturn:
    raise _OperatorApprovalIntentStatementArtifactInvariantError()


__all__ = (
    "AUTHORITY_SCOPE",
    "MAX_OPERATOR_APPROVAL_INTENT_STATEMENT_ARTIFACT_VALIDATION_CANONICAL_BYTES",
    "MAX_OPERATOR_APPROVAL_INTENT_STATEMENT_ARTIFACT_VALIDATION_DIAGNOSTICS",
    "OPERATOR_APPROVAL_INTENT_STATEMENT_ARTIFACT_VALIDATION_IDENTITY_DOMAIN",
    "RESULT_VERSION",
    "Step2MarketSourcePolicyOperatorApprovalIntentStatementArtifactValidationDiagnostic",
    "Step2MarketSourcePolicyOperatorApprovalIntentStatementArtifactValidationResult",
    "Step2MarketSourcePolicyOperatorApprovalIntentStatementArtifactValidationState",
    "TRADE_PERMISSION_EFFECT",
    "VALIDATOR_VERSION",
    "validate_step2_market_source_policy_operator_approval_intent_statement_artifact_bytes",
)
