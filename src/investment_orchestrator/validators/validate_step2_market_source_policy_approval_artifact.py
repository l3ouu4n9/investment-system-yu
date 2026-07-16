"""Compose strict parsing and decoded-object approval validation.

This module binds one strict raw-byte parser result to one decoded-object
validation result.  It does not authenticate an operator, activate or select
a policy, resolve source roles, affect a workflow, publish an artifact,
compile an order, or grant trading authority.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from hashlib import sha256
import json
import re
from typing import Any, NoReturn

from investment_orchestrator.parsers.parse_step2_market_source_policy_approvals import (
    FrozenJsonArray,
    FrozenJsonObject,
    Step2MarketSourcePolicyApprovalParseDiagnostic,
    Step2MarketSourcePolicyApprovalParseResult,
    Step2MarketSourcePolicyApprovalParseState,
    parse_step2_market_source_policy_approvals_bytes,
)
from investment_orchestrator.validators.validate_step2_market_source_policy_approvals import (
    Step2MarketSourcePolicyApprovalDiagnostic,
    Step2MarketSourcePolicyApprovalObjectState,
    Step2MarketSourcePolicyApprovalsObjectValidationResult,
    validate_step2_market_source_policy_approvals_object,
)


MAX_JSON_NESTING_DEPTH = 8
MAX_JSON_NODE_COUNT = 4096
MAX_DECODED_STRING_CODE_POINTS = 262_144
MAX_OBJECT_MEMBER_COUNT = 1024
MAX_ARRAY_ITEM_COUNT = 1024
MAX_PARSED_VALUE_CANONICAL_BYTES = 12_582_912
MAX_ARTIFACT_VALIDATION_BINDING_BYTES = 16_384
MAX_APPROVAL_ARTIFACT_COMPOSITION_DIAGNOSTICS = 1

_MAX_CANONICAL_APPROVAL_CONTENT_BYTES = 262_144

RESULT_VERSION = (
    "step2_market_source_policy_approval_artifact_validation_result_v1"
)
COMPOSITION_VERSION = (
    "step2_market_source_policy_approval_artifact_composition_v1"
)
AUTHORITY_SCOPE = "raw_and_object_contract_validation_only"
NOT_TRADE_AUTHORIZATION = True
TRADE_PERMISSION_EFFECT = "none"
OPERATOR_AUTHENTICATION_PERFORMED = False
SOURCE_RESOLUTION_PERFORMED = False
FRESHNESS_EVALUATION_PERFORMED = False
UNIVERSE_RESOLUTION_PERFORMED = False
CANDIDATE_VALIDITY_EVALUATED = False
ACTIVATION_EVALUATION_PERFORMED = False
PUBLICATION_EVALUATION_PERFORMED = False
WORKFLOW_PERMISSION_EVALUATED = False
ORDER_COMPILATION_EVALUATED = False

ARTIFACT_RESULT_BOOLEAN_COERCION_ERROR = (
    "inspect artifact_contract_valid explicitly; Step 2 source-policy "
    "approval artifact validation results have no truth value"
)

_PARSER_RESULT_VERSION = (
    "step2_market_source_policy_approval_parse_result_v1"
)
_PARSER_VERSION = "step2_market_source_policy_approval_parser_v1"
_PARSER_AUTHORITY_SCOPE = "strict_raw_artifact_parsing_only"
_OBJECT_RESULT_VERSION = (
    "step2_market_source_policy_approval_object_validation_result_v1"
)
_OBJECT_SCHEMA_VERSION = "step2_market_source_policy_approvals_v1"
_OBJECT_AUTHORITY_SCOPE = "approval_object_validation_only"
_PARSED_VALUE_IDENTITY_DOMAIN = (
    b"step2_market_source_policy_approval_parsed_value_v1\0"
)
_ARTIFACT_VALIDATION_IDENTITY_DOMAIN = (
    b"step2_market_source_policy_approval_artifact_validation_v1\0"
)
_APPROVAL_CONTENT_IDENTITY_DOMAIN = (
    b"step2_market_source_policy_approvals_v1\0"
)
_EMPTY_BYTES_SHA256 = (
    "e3b0c44298fc1c149afbf4c8996fb924"
    "27ae41e4649b934ca495991b7852b855"
)
_INVARIANT_MESSAGE = (
    "Step 2 source-policy approval artifact composition invariant violated"
)
_RESULT_CONSTRUCTION_ERROR = (
    "Step 2 source-policy approval artifact validation results are created "
    "only by the public validator"
)
_POLICY_VERSION_RE = re.compile(
    r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$"
)
_RESERVED_POLICY_VERSIONS = frozenset(
    {"latest", "current", "default", "*"}
)


class Step2MarketSourcePolicyApprovalArtifactState(str, Enum):
    INPUT_ABSENT = "input_absent"
    INPUT_TYPE_INVALID = "input_type_invalid"
    RAW_PARSE_INVALID = "raw_parse_invalid"
    ROOT_TYPE_INVALID = "root_type_invalid"
    PARSED_IDENTITY_BINDING_INVALID = (
        "parsed_identity_binding_invalid"
    )
    OBJECT_CONTRACT_INVALID = "object_contract_invalid"
    VALID_EMPTY = "valid_empty"
    VALID_NONEMPTY = "valid_nonempty"


class Step2MarketSourcePolicyApprovalArtifactDiagnostic(str, Enum):
    APPROVAL_ARTIFACT_INPUT_MISSING = (
        "approval_artifact_input_missing"
    )
    APPROVAL_ARTIFACT_INPUT_TYPE_INVALID = (
        "approval_artifact_input_type_invalid"
    )
    APPROVAL_ARTIFACT_RAW_PARSE_INVALID = (
        "approval_artifact_raw_parse_invalid"
    )
    APPROVAL_ARTIFACT_ROOT_NOT_OBJECT = (
        "approval_artifact_root_not_object"
    )
    APPROVAL_ARTIFACT_PARSED_IDENTITY_BINDING_INVALID = (
        "approval_artifact_parsed_identity_binding_invalid"
    )
    APPROVAL_ARTIFACT_OBJECT_CONTRACT_INVALID = (
        "approval_artifact_object_contract_invalid"
    )


class _CompositionInvariantError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True, init=False)
class Step2MarketSourcePolicyApprovalArtifactValidationResult:
    """Frozen raw/object composition result with no activation authority."""

    result_version: str = field(default=RESULT_VERSION, init=False)
    composition_version: str = field(
        default=COMPOSITION_VERSION,
        init=False,
    )
    composition_state: Step2MarketSourcePolicyApprovalArtifactState
    parser_result: Step2MarketSourcePolicyApprovalParseResult
    approval_object_validation_result: (
        Step2MarketSourcePolicyApprovalsObjectValidationResult | None
    )
    artifact_validation_identity_sha256: str | None
    root_object_check_performed: bool
    root_object_valid: bool | None
    exact_builtin_conversion_performed: bool
    exact_builtin_conversion_valid: bool | None
    parsed_identity_recheck_performed: bool
    parsed_identity_recheck_matches: bool | None
    object_contract_validation_performed: bool
    composition_validation_performed: bool
    artifact_contract_valid: bool | None
    diagnostics: tuple[
        Step2MarketSourcePolicyApprovalArtifactDiagnostic,
        ...,
    ]
    authority_scope: str = field(default=AUTHORITY_SCOPE, init=False)
    not_trade_authorization: bool = field(
        default=NOT_TRADE_AUTHORIZATION,
        init=False,
    )
    trade_permission_effect: str = field(
        default=TRADE_PERMISSION_EFFECT,
        init=False,
    )
    operator_authentication_performed: bool = field(
        default=OPERATOR_AUTHENTICATION_PERFORMED,
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
    activation_evaluation_performed: bool = field(
        default=ACTIVATION_EVALUATION_PERFORMED,
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

    def __new__(cls, *args: object, **kwargs: object) -> NoReturn:
        del cls, args, kwargs
        raise TypeError(_RESULT_CONSTRUCTION_ERROR)

    def __reduce__(self) -> NoReturn:
        raise TypeError(_RESULT_CONSTRUCTION_ERROR)

    def __reduce_ex__(self, protocol: object) -> NoReturn:
        raise TypeError(_RESULT_CONSTRUCTION_ERROR)

    def __setstate__(self, state: object) -> NoReturn:
        raise TypeError(_RESULT_CONSTRUCTION_ERROR)

    def __bool__(self) -> bool:
        raise TypeError(ARTIFACT_RESULT_BOOLEAN_COERCION_ERROR)


_State = Step2MarketSourcePolicyApprovalArtifactState
_Diagnostic = Step2MarketSourcePolicyApprovalArtifactDiagnostic
_ParseState = Step2MarketSourcePolicyApprovalParseState
_ParseDiagnostic = Step2MarketSourcePolicyApprovalParseDiagnostic
_ObjectState = Step2MarketSourcePolicyApprovalObjectState

_STRUCTURALLY_CAPABLE_OBJECT_DIAGNOSTICS = frozenset(
    {
        Step2MarketSourcePolicyApprovalDiagnostic.APPROVAL_INPUT_INVALID,
        Step2MarketSourcePolicyApprovalDiagnostic
        .APPROVAL_SCHEMA_VERSION_UNSUPPORTED,
        Step2MarketSourcePolicyApprovalDiagnostic
        .APPROVAL_DECLARED_IDENTITY_INVALID,
        Step2MarketSourcePolicyApprovalDiagnostic
        .APPROVAL_POLICY_VERSION_INVALID,
        Step2MarketSourcePolicyApprovalDiagnostic.APPROVAL_PROVENANCE_INVALID,
        Step2MarketSourcePolicyApprovalDiagnostic.CANONICAL_SOURCE_ID_INVALID,
        Step2MarketSourcePolicyApprovalDiagnostic.SOURCE_VERSION_INVALID,
        Step2MarketSourcePolicyApprovalDiagnostic.ALIAS_INVALID,
        Step2MarketSourcePolicyApprovalDiagnostic.PERMISSION_FIELD_INVALID,
    }
)


def validate_step2_market_source_policy_approval_artifact_bytes(
    value: object,
) -> Step2MarketSourcePolicyApprovalArtifactValidationResult:
    """Bind strict raw parsing to decoded approval-object validation."""
    parser_result = parse_step2_market_source_policy_approvals_bytes(value)
    _validate_parser_result(parser_result)

    if parser_result.parse_state is _ParseState.INPUT_ABSENT:
        return _create_result(
            parser_result=parser_result,
            pre_object_state=_State.INPUT_ABSENT,
        )
    if parser_result.parse_state is _ParseState.INPUT_TYPE_INVALID:
        return _create_result(
            parser_result=parser_result,
            pre_object_state=_State.INPUT_TYPE_INVALID,
        )
    if parser_result.parse_valid is not True:
        return _create_result(
            parser_result=parser_result,
            pre_object_state=_State.RAW_PARSE_INVALID,
        )

    parsed_root = parser_result.immutable_parsed_value
    if type(parsed_root) is not FrozenJsonObject:
        return _create_result(
            parser_result=parser_result,
            pre_object_state=_State.ROOT_TYPE_INVALID,
        )

    converted_root = _convert_frozen_root_to_exact_builtins(parsed_root)
    recomputed_identity = _canonical_complete_root_identity(converted_root)
    if recomputed_identity != parser_result.parsed_value_identity_sha256:
        return _create_result(
            parser_result=parser_result,
            pre_object_state=_State.PARSED_IDENTITY_BINDING_INVALID,
        )

    object_result = validate_step2_market_source_policy_approvals_object(
        converted_root
    )
    return _create_result(
        parser_result=parser_result,
        approval_object_validation_result=object_result,
        converted_root=converted_root,
    )


def _create_result(
    *,
    parser_result: Step2MarketSourcePolicyApprovalParseResult,
    pre_object_state: Step2MarketSourcePolicyApprovalArtifactState | None = None,
    approval_object_validation_result: (
        Step2MarketSourcePolicyApprovalsObjectValidationResult | None
    ) = None,
    converted_root: dict[str, Any] | None = None,
) -> Step2MarketSourcePolicyApprovalArtifactValidationResult:
    _validate_parser_result(parser_result)
    object_result = approval_object_validation_result
    if object_result is None:
        if converted_root is not None or pre_object_state not in {
            _State.INPUT_ABSENT,
            _State.INPUT_TYPE_INVALID,
            _State.RAW_PARSE_INVALID,
            _State.ROOT_TYPE_INVALID,
            _State.PARSED_IDENTITY_BINDING_INVALID,
        }:
            _invariant_failure()
        state = pre_object_state
    else:
        if pre_object_state is not None or type(converted_root) is not dict:
            _invariant_failure()
        _validate_object_result(object_result, converted_root)
        if object_result.approval_state in {
            _ObjectState.STRUCTURALLY_INVALID,
            _ObjectState.SEMANTICALLY_INVALID,
        }:
            state = _State.OBJECT_CONTRACT_INVALID
        elif object_result.approval_state is _ObjectState.VALID_EMPTY:
            state = _State.VALID_EMPTY
        elif object_result.approval_state is _ObjectState.VALID_NONEMPTY:
            state = _State.VALID_NONEMPTY
        else:
            _invariant_failure()

    if type(state) is not Step2MarketSourcePolicyApprovalArtifactState:
        _invariant_failure()
    (
        root_object_check_performed,
        root_object_valid,
        exact_builtin_conversion_performed,
        exact_builtin_conversion_valid,
        parsed_identity_recheck_performed,
        parsed_identity_recheck_matches,
        object_contract_validation_performed,
        composition_validation_performed,
        artifact_contract_valid,
        diagnostics,
    ) = _branch_values(state)
    _validate_result_components(
        composition_state=state,
        parser_result=parser_result,
        approval_object_validation_result=object_result,
        root_object_check_performed=root_object_check_performed,
        root_object_valid=root_object_valid,
        exact_builtin_conversion_performed=(
            exact_builtin_conversion_performed
        ),
        exact_builtin_conversion_valid=exact_builtin_conversion_valid,
        parsed_identity_recheck_performed=(
            parsed_identity_recheck_performed
        ),
        parsed_identity_recheck_matches=parsed_identity_recheck_matches,
        object_contract_validation_performed=(
            object_contract_validation_performed
        ),
        composition_validation_performed=composition_validation_performed,
        artifact_contract_valid=artifact_contract_valid,
        diagnostics=diagnostics,
    )

    artifact_identity = None
    if object_result is not None:
        if type(artifact_contract_valid) is not bool:
            _invariant_failure()
        artifact_identity = _artifact_validation_identity(
            parser_result=parser_result,
            object_result=object_result,
            composition_state=state,
            artifact_contract_valid=artifact_contract_valid,
            composition_diagnostics=diagnostics,
        )
        if not _is_sha256(artifact_identity):
            _invariant_failure()

    result = object.__new__(
        Step2MarketSourcePolicyApprovalArtifactValidationResult
    )
    values = {
        "result_version": RESULT_VERSION,
        "composition_version": COMPOSITION_VERSION,
        "composition_state": state,
        "parser_result": parser_result,
        "approval_object_validation_result": object_result,
        "artifact_validation_identity_sha256": artifact_identity,
        "root_object_check_performed": root_object_check_performed,
        "root_object_valid": root_object_valid,
        "exact_builtin_conversion_performed": (
            exact_builtin_conversion_performed
        ),
        "exact_builtin_conversion_valid": exact_builtin_conversion_valid,
        "parsed_identity_recheck_performed": (
            parsed_identity_recheck_performed
        ),
        "parsed_identity_recheck_matches": parsed_identity_recheck_matches,
        "object_contract_validation_performed": (
            object_contract_validation_performed
        ),
        "composition_validation_performed": (
            composition_validation_performed
        ),
        "artifact_contract_valid": artifact_contract_valid,
        "diagnostics": diagnostics,
        "authority_scope": AUTHORITY_SCOPE,
        "not_trade_authorization": NOT_TRADE_AUTHORIZATION,
        "trade_permission_effect": TRADE_PERMISSION_EFFECT,
        "operator_authentication_performed": (
            OPERATOR_AUTHENTICATION_PERFORMED
        ),
        "source_resolution_performed": SOURCE_RESOLUTION_PERFORMED,
        "freshness_evaluation_performed": FRESHNESS_EVALUATION_PERFORMED,
        "universe_resolution_performed": UNIVERSE_RESOLUTION_PERFORMED,
        "candidate_validity_evaluated": CANDIDATE_VALIDITY_EVALUATED,
        "activation_evaluation_performed": ACTIVATION_EVALUATION_PERFORMED,
        "publication_evaluation_performed": (
            PUBLICATION_EVALUATION_PERFORMED
        ),
        "workflow_permission_evaluated": WORKFLOW_PERMISSION_EVALUATED,
        "order_compilation_evaluated": ORDER_COMPILATION_EVALUATED,
    }
    for name, value in values.items():
        object.__setattr__(result, name, value)
    return result


def _validate_parser_result(
    result: object,
) -> None:
    if type(result) is not Step2MarketSourcePolicyApprovalParseResult:
        _invariant_failure()
    if (
        not _is_exact_string(result.result_version, _PARSER_RESULT_VERSION)
        or not _is_exact_string(result.parser_version, _PARSER_VERSION)
        or not _is_exact_string(
            result.authority_scope,
            _PARSER_AUTHORITY_SCOPE,
        )
        or result.not_trade_authorization is not True
        or not _is_exact_string(result.trade_permission_effect, "none")
        or result.operator_authentication_performed is not False
        or result.object_contract_validation_performed is not False
        or result.source_resolution_performed is not False
        or result.freshness_evaluation_performed is not False
        or result.activation_evaluation_performed is not False
        or result.publication_evaluation_performed is not False
        or result.workflow_permission_evaluated is not False
        or result.order_compilation_evaluated is not False
    ):
        _invariant_failure()
    if type(result.parse_state) is not Step2MarketSourcePolicyApprovalParseState:
        _invariant_failure()
    if type(result.diagnostics) is not tuple or any(
        type(diagnostic) is not Step2MarketSourcePolicyApprovalParseDiagnostic
        for diagnostic in result.diagnostics
    ):
        _invariant_failure()

    if result.parse_state is _ParseState.VALID:
        if (
            result.parse_valid is not True
            or result.parsing_performed is not True
            or result.parsed_value_available is not True
            or not _is_bounded_raw_size(result.raw_artifact_size_bytes)
            or not _is_sha256(result.raw_artifact_sha256)
            or not _is_sha256(result.parsed_value_identity_sha256)
            or result.diagnostics != ()
            or not _is_exact_frozen_json_value(
                result.immutable_parsed_value
            )
        ):
            _invariant_failure()
        _validate_frozen_value_tree(result.immutable_parsed_value)
        return

    if (
        result.parsed_value_identity_sha256 is not None
        or result.parsed_value_available is not False
        or result.immutable_parsed_value is not None
        or len(result.diagnostics) != 1
    ):
        _invariant_failure()

    diagnostic = result.diagnostics[0]
    if result.parse_state is _ParseState.INPUT_ABSENT:
        valid = (
            result.raw_artifact_size_bytes is None
            and result.raw_artifact_sha256 is None
            and result.parsing_performed is False
            and result.parse_valid is None
            and diagnostic is _ParseDiagnostic.RAW_APPROVAL_INPUT_MISSING
        )
    elif result.parse_state is _ParseState.INPUT_TYPE_INVALID:
        valid = (
            result.raw_artifact_size_bytes is None
            and result.raw_artifact_sha256 is None
            and result.parsing_performed is False
            and result.parse_valid is None
            and diagnostic
            is _ParseDiagnostic.RAW_APPROVAL_INPUT_TYPE_INVALID
        )
    elif result.parse_state is _ParseState.RAW_SIZE_INVALID:
        valid = _valid_raw_size_failure(result, diagnostic)
    else:
        valid = _valid_bounded_parse_failure(result, diagnostic)
    if not valid:
        _invariant_failure()


def _valid_raw_size_failure(
    result: Step2MarketSourcePolicyApprovalParseResult,
    diagnostic: Step2MarketSourcePolicyApprovalParseDiagnostic,
) -> bool:
    if (
        diagnostic is not _ParseDiagnostic.RAW_APPROVAL_SIZE_INVALID
        or result.parsing_performed is not False
        or result.parse_valid is not None
        or type(result.raw_artifact_size_bytes) is not int
    ):
        return False
    if result.raw_artifact_size_bytes == 0:
        return result.raw_artifact_sha256 == _EMPTY_BYTES_SHA256
    return (
        result.raw_artifact_size_bytes > 2_097_152
        and result.raw_artifact_sha256 is None
    )


def _valid_bounded_parse_failure(
    result: Step2MarketSourcePolicyApprovalParseResult,
    diagnostic: Step2MarketSourcePolicyApprovalParseDiagnostic,
) -> bool:
    allowed_diagnostics = {
        _ParseState.ENCODING_INVALID: {
            _ParseDiagnostic.RAW_APPROVAL_BOM_UNSUPPORTED,
            _ParseDiagnostic.RAW_APPROVAL_UTF8_INVALID,
        },
        _ParseState.JSON_GRAMMAR_INVALID: {
            _ParseDiagnostic.RAW_APPROVAL_JSON_INVALID,
            _ParseDiagnostic.RAW_APPROVAL_TRAILING_CONTENT,
        },
        _ParseState.DUPLICATE_KEY_INVALID: {
            _ParseDiagnostic.RAW_APPROVAL_DUPLICATE_KEY,
        },
        _ParseState.RESOURCE_LIMIT_INVALID: {
            _ParseDiagnostic.RAW_APPROVAL_SIZE_INVALID,
            _ParseDiagnostic.RAW_APPROVAL_DEPTH_LIMIT_EXCEEDED,
            _ParseDiagnostic.RAW_APPROVAL_NODE_LIMIT_EXCEEDED,
            _ParseDiagnostic.RAW_APPROVAL_STRING_LIMIT_EXCEEDED,
            _ParseDiagnostic.RAW_APPROVAL_OBJECT_MEMBER_LIMIT_EXCEEDED,
            _ParseDiagnostic.RAW_APPROVAL_ARRAY_ITEM_LIMIT_EXCEEDED,
        },
        _ParseState.UNSUPPORTED_SCALAR_INVALID: {
            _ParseDiagnostic.RAW_APPROVAL_UNSUPPORTED_SCALAR,
        },
        _ParseState.UNICODE_SCALAR_INVALID: {
            _ParseDiagnostic.RAW_APPROVAL_SURROGATE_INVALID,
        },
    }
    return (
        result.parse_state in allowed_diagnostics
        and diagnostic in allowed_diagnostics[result.parse_state]
        and _is_bounded_raw_size(result.raw_artifact_size_bytes)
        and _is_sha256(result.raw_artifact_sha256)
        and result.parsing_performed is True
        and result.parse_valid is False
    )


def _validate_object_result(
    result: object,
    converted_root: dict[str, Any],
) -> None:
    _validate_object_result_fields(result)
    if result.approval_state is not _ObjectState.STRUCTURALLY_INVALID:
        _validate_object_result_against_converted_root(
            result,
            converted_root,
        )


def _validate_object_result_fields(result: object) -> None:
    if type(result) is not Step2MarketSourcePolicyApprovalsObjectValidationResult:
        _invariant_failure()
    if (
        not _is_exact_string(result.result_version, _OBJECT_RESULT_VERSION)
        or not _is_exact_string(
            result.authority_scope,
            _OBJECT_AUTHORITY_SCOPE,
        )
        or result.not_trade_authorization is not True
        or not _is_exact_string(result.trade_permission_effect, "none")
        or result.source_resolution_performed is not False
        or result.freshness_evaluation_performed is not False
        or result.universe_resolution_performed is not False
        or result.candidate_validity_evaluated is not False
        or result.publication_evaluation_performed is not False
        or result.workflow_permission_evaluated is not False
        or result.order_compilation_evaluated is not False
        or result.operator_authentication_performed is not False
        or result.raw_artifact_parsing_performed is not False
        or result.activation_evaluation_performed is not False
        or type(result.approval_state)
        is not Step2MarketSourcePolicyApprovalObjectState
        or type(result.diagnostics) is not tuple
        or any(
            type(diagnostic) is not Step2MarketSourcePolicyApprovalDiagnostic
            for diagnostic in result.diagnostics
        )
    ):
        _invariant_failure()

    if result.approval_state is _ObjectState.STRUCTURALLY_INVALID:
        valid = _valid_structurally_invalid_object_result(result)
    elif result.approval_state is _ObjectState.SEMANTICALLY_INVALID:
        valid = _valid_semantically_invalid_object_result(result)
    elif result.approval_state is _ObjectState.VALID_EMPTY:
        valid = _valid_approval_object_result(result, empty=True)
    elif result.approval_state is _ObjectState.VALID_NONEMPTY:
        valid = _valid_approval_object_result(result, empty=False)
    else:
        valid = False
    if not valid:
        _invariant_failure()


def _valid_structurally_invalid_object_result(
    result: Step2MarketSourcePolicyApprovalsObjectValidationResult,
) -> bool:
    return (
        result.approval_schema_version is None
        and result.approval_policy_version is None
        and result.declared_operator_approved_source_policy_sha256 is None
        and result.canonical_approval_content_sha256 is None
        and result.approval_identity_matches is None
        and result.object_validation_performed is True
        and result.object_structure_valid is False
        and result.semantic_validation_performed is False
        and result.approval_object_valid is None
        and result.source_count is None
        and len(result.diagnostics) == 1
        and result.diagnostics[0]
        in _STRUCTURALLY_CAPABLE_OBJECT_DIAGNOSTICS
    )


def _valid_semantically_invalid_object_result(
    result: Step2MarketSourcePolicyApprovalsObjectValidationResult,
) -> bool:
    diagnostics = result.diagnostics
    ordered_content_diagnostics = tuple(
        diagnostic
        for diagnostic in Step2MarketSourcePolicyApprovalDiagnostic
        if diagnostic in diagnostics
        and diagnostic
        not in {
            Step2MarketSourcePolicyApprovalDiagnostic.APPROVAL_INPUT_MISSING,
            Step2MarketSourcePolicyApprovalDiagnostic.APPROVAL_INPUT_INVALID,
            Step2MarketSourcePolicyApprovalDiagnostic
            .APPROVAL_SCHEMA_VERSION_UNSUPPORTED,
        }
    )
    if (
        not diagnostics
        or len(diagnostics) > 14
        or len(set(diagnostics)) != len(diagnostics)
        or diagnostics != ordered_content_diagnostics
        or not _is_exact_string(
            result.approval_schema_version,
            _OBJECT_SCHEMA_VERSION,
        )
        or not _valid_optional_policy_version(
            result.approval_policy_version
        )
        or not _is_sha256(
            result.canonical_approval_content_sha256
        )
        or result.object_validation_performed is not True
        or result.object_structure_valid is not True
        or result.semantic_validation_performed is not True
        or result.approval_object_valid is not False
        or not _is_source_count(result.source_count)
    ):
        return False
    if (
        result.approval_policy_version is None
        and Step2MarketSourcePolicyApprovalDiagnostic
        .APPROVAL_POLICY_VERSION_INVALID
        not in diagnostics
    ):
        return False

    declared = result.declared_operator_approved_source_policy_sha256
    canonical = result.canonical_approval_content_sha256
    identity = result.approval_identity_matches
    declared_invalid = (
        Step2MarketSourcePolicyApprovalDiagnostic
        .APPROVAL_DECLARED_IDENTITY_INVALID
        in diagnostics
    )
    mismatch = (
        Step2MarketSourcePolicyApprovalDiagnostic.APPROVAL_IDENTITY_MISMATCH
        in diagnostics
    )
    if declared is None:
        return identity is None and declared_invalid and not mismatch
    if declared_invalid:
        return False
    if not _is_sha256(declared) or type(identity) is not bool:
        return False
    if identity is not (declared == canonical):
        return False
    return mismatch is (identity is False)


def _valid_approval_object_result(
    result: Step2MarketSourcePolicyApprovalsObjectValidationResult,
    *,
    empty: bool,
) -> bool:
    source_count = result.source_count
    return (
        _is_exact_string(
            result.approval_schema_version,
            _OBJECT_SCHEMA_VERSION,
        )
        and _is_policy_version(result.approval_policy_version)
        and _is_sha256(
            result.declared_operator_approved_source_policy_sha256
        )
        and _is_sha256(result.canonical_approval_content_sha256)
        and result.declared_operator_approved_source_policy_sha256
        == result.canonical_approval_content_sha256
        and result.approval_identity_matches is True
        and result.object_validation_performed is True
        and result.object_structure_valid is True
        and result.semantic_validation_performed is True
        and result.approval_object_valid is True
        and type(source_count) is int
        and (source_count == 0 if empty else 1 <= source_count <= 64)
        and result.diagnostics == ()
    )


def _validate_object_result_against_converted_root(
    result: Step2MarketSourcePolicyApprovalsObjectValidationResult,
    converted_root: dict[str, Any],
) -> None:
    if (
        type(converted_root) is not dict
        or set(converted_root)
        != {
            "schema_version",
            "operator_approved_source_policy_sha256",
            "approval_content",
        }
    ):
        _invariant_failure()

    schema_version = converted_root["schema_version"]
    declared_hash = converted_root[
        "operator_approved_source_policy_sha256"
    ]
    approval_content = converted_root["approval_content"]
    if (
        not _is_exact_string(schema_version, _OBJECT_SCHEMA_VERSION)
        or type(declared_hash) is not str
        or type(approval_content) is not dict
        or set(approval_content)
        != {
            "policy_version",
            "supersedes_policy_version",
            "policy_change_reason",
            "approved_by",
            "approved_at_utc",
            "sources",
        }
    ):
        _invariant_failure()

    policy_version = approval_content["policy_version"]
    sources = approval_content["sources"]
    if type(policy_version) is not str or type(sources) is not list:
        _invariant_failure()

    retained_policy_version = (
        policy_version if _is_policy_version(policy_version) else None
    )
    retained_declared_hash = declared_hash if _is_sha256(declared_hash) else None
    canonical_hash = _canonical_approval_content_identity(approval_content)
    identity_matches = (
        retained_declared_hash == canonical_hash
        if retained_declared_hash is not None
        else None
    )
    if (
        result.approval_schema_version != schema_version
        or result.approval_policy_version != retained_policy_version
        or result.declared_operator_approved_source_policy_sha256
        != retained_declared_hash
        or result.canonical_approval_content_sha256 != canonical_hash
        or result.approval_identity_matches is not identity_matches
        or result.source_count != len(sources)
    ):
        _invariant_failure()


def _convert_frozen_root_to_exact_builtins(
    root: FrozenJsonObject,
) -> dict[str, Any]:
    holder: list[Any] = [None]
    active_container_ids: set[int] = set()
    node_count = 0
    stack: list[tuple[str, Any, Any, Any, int]] = [
        ("value", root, holder, 0, 0)
    ]

    while stack:
        operation, current, target, target_key, depth = stack.pop()
        if operation == "leave":
            if target_key not in active_container_ids:
                _invariant_failure()
            active_container_ids.remove(target_key)
            continue
        if operation != "value" or depth > MAX_JSON_NESTING_DEPTH:
            _invariant_failure()

        node_count += 1
        if node_count > MAX_JSON_NODE_COUNT:
            _invariant_failure()

        if current is None:
            _assign_converted(target, target_key, None)
            continue
        if type(current) is str:
            _validate_decoded_string(current)
            _assign_converted(target, target_key, current)
            continue
        if type(current) is FrozenJsonArray:
            if type(current.items) is not tuple:
                _invariant_failure()
            if len(current.items) > MAX_ARRAY_ITEM_COUNT:
                _invariant_failure()
            container_id = id(current)
            if container_id in active_container_ids:
                _invariant_failure()
            active_container_ids.add(container_id)
            converted_array: list[Any] = [None] * len(current.items)
            _assign_converted(target, target_key, converted_array)
            stack.append(("leave", None, None, container_id, depth))
            for index in range(len(current.items) - 1, -1, -1):
                stack.append(
                    (
                        "value",
                        current.items[index],
                        converted_array,
                        index,
                        depth + 1,
                    )
                )
            continue
        if type(current) is FrozenJsonObject:
            if (
                type(current.items) is not tuple
                or len(current.items) > MAX_OBJECT_MEMBER_COUNT
            ):
                _invariant_failure()
            keys: set[str] = set()
            for item in current.items:
                if type(item) is not tuple or len(item) != 2:
                    _invariant_failure()
                key = item[0]
                if type(key) is not str or key in keys:
                    _invariant_failure()
                _validate_decoded_string(key)
                keys.add(key)
            container_id = id(current)
            if container_id in active_container_ids:
                _invariant_failure()
            active_container_ids.add(container_id)
            converted_object: dict[str, Any] = {}
            _assign_converted(target, target_key, converted_object)
            stack.append(("leave", None, None, container_id, depth))
            for key, child in reversed(current.items):
                stack.append(
                    (
                        "value",
                        child,
                        converted_object,
                        key,
                        depth + 1,
                    )
                )
            continue
        _invariant_failure()

    converted_root = holder[0]
    if type(converted_root) is not dict or active_container_ids:
        _invariant_failure()
    return converted_root


def _assign_converted(target: Any, key: Any, value: Any) -> None:
    if type(target) is list:
        if type(key) is not int or not 0 <= key < len(target):
            _invariant_failure()
        target[key] = value
        return
    if type(target) is dict:
        if type(key) is not str or key in target:
            _invariant_failure()
        target[key] = value
        return
    _invariant_failure()


def _canonical_complete_root_identity(root: dict[str, Any]) -> str:
    return _canonical_json_identity(
        root,
        domain=_PARSED_VALUE_IDENTITY_DOMAIN,
        maximum_bytes=MAX_PARSED_VALUE_CANONICAL_BYTES,
    )


def _canonical_approval_content_identity(
    approval_content: dict[str, Any],
) -> str:
    return _canonical_json_identity(
        approval_content,
        domain=_APPROVAL_CONTENT_IDENTITY_DOMAIN,
        maximum_bytes=_MAX_CANONICAL_APPROVAL_CONTENT_BYTES,
    )


def _canonical_json_identity(
    root: object,
    *,
    domain: bytes,
    maximum_bytes: int,
) -> str:
    if type(domain) is not bytes or type(maximum_bytes) is not int:
        _invariant_failure()
    hasher = sha256()
    hasher.update(domain)
    canonical_size = 0
    active_container_ids: set[int] = set()
    stack: list[tuple[str, Any]] = [("value", root)]

    while stack:
        operation, current = stack.pop()
        if operation == "leave":
            if current not in active_container_ids:
                _invariant_failure()
            active_container_ids.remove(current)
            continue
        if operation == "raw":
            if type(current) is not bytes:
                _invariant_failure()
            canonical_size = _update_canonical_hash(
                hasher,
                canonical_size,
                current,
                maximum_bytes,
            )
            continue
        if operation == "string":
            if type(current) is not str:
                _invariant_failure()
            canonical_size = _update_canonical_string(
                hasher,
                canonical_size,
                current,
                maximum_bytes,
            )
            continue
        if operation != "value":
            _invariant_failure()

        if current is None:
            canonical_size = _update_canonical_hash(
                hasher,
                canonical_size,
                b"null",
                maximum_bytes,
            )
            continue
        if type(current) is str:
            stack.append(("string", current))
            continue
        if type(current) is list:
            container_id = id(current)
            if container_id in active_container_ids:
                _invariant_failure()
            active_container_ids.add(container_id)
            canonical_size = _update_canonical_hash(
                hasher,
                canonical_size,
                b"[",
                maximum_bytes,
            )
            stack.append(("leave", container_id))
            stack.append(("raw", b"]"))
            for index in range(len(current) - 1, -1, -1):
                stack.append(("value", current[index]))
                if index != 0:
                    stack.append(("raw", b","))
            continue
        if type(current) is dict:
            container_id = id(current)
            if container_id in active_container_ids:
                _invariant_failure()
            active_container_ids.add(container_id)
            canonical_size = _update_canonical_hash(
                hasher,
                canonical_size,
                b"{",
                maximum_bytes,
            )
            if any(type(key) is not str for key in current):
                _invariant_failure()
            keys = sorted(current)
            stack.append(("leave", container_id))
            stack.append(("raw", b"}"))
            for index in range(len(keys) - 1, -1, -1):
                key = keys[index]
                stack.append(("value", current[key]))
                stack.append(("raw", b":"))
                stack.append(("string", key))
                if index != 0:
                    stack.append(("raw", b","))
            continue
        _invariant_failure()

    if active_container_ids:
        _invariant_failure()
    return hasher.hexdigest()


def _update_canonical_hash(
    hasher: Any,
    current_size: int,
    fragment: bytes,
    maximum_bytes: int,
) -> int:
    next_size = current_size + len(fragment)
    if next_size > maximum_bytes:
        _invariant_failure()
    hasher.update(fragment)
    return next_size


def _update_canonical_string(
    hasher: Any,
    current_size: int,
    value: str,
    maximum_bytes: int,
) -> int:
    _validate_decoded_string(value)
    current_size = _update_canonical_hash(
        hasher,
        current_size,
        b'"',
        maximum_bytes,
    )
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
        current_size = _update_canonical_hash(
            hasher,
            current_size,
            fragment,
            maximum_bytes,
        )
    return _update_canonical_hash(
        hasher,
        current_size,
        b'"',
        maximum_bytes,
    )


def _artifact_validation_identity(
    *,
    parser_result: Step2MarketSourcePolicyApprovalParseResult,
    object_result: Step2MarketSourcePolicyApprovalsObjectValidationResult,
    composition_state: Step2MarketSourcePolicyApprovalArtifactState,
    artifact_contract_valid: bool,
    composition_diagnostics: tuple[
        Step2MarketSourcePolicyApprovalArtifactDiagnostic,
        ...,
    ],
) -> str:
    record = _binding_record(
        parser_result=parser_result,
        object_result=object_result,
        composition_state=composition_state,
        artifact_contract_valid=artifact_contract_valid,
        composition_diagnostics=composition_diagnostics,
    )
    serialized = json.dumps(
        record,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    if type(serialized) is not str:
        _invariant_failure()
    encoded = serialized.encode("utf-8")
    if len(encoded) > MAX_ARTIFACT_VALIDATION_BINDING_BYTES:
        _invariant_failure()
    return sha256(
        _ARTIFACT_VALIDATION_IDENTITY_DOMAIN + encoded
    ).hexdigest()


def _binding_record(
    *,
    parser_result: Step2MarketSourcePolicyApprovalParseResult,
    object_result: Step2MarketSourcePolicyApprovalsObjectValidationResult,
    composition_state: Step2MarketSourcePolicyApprovalArtifactState,
    artifact_contract_valid: bool,
    composition_diagnostics: tuple[
        Step2MarketSourcePolicyApprovalArtifactDiagnostic,
        ...,
    ],
) -> dict[str, Any]:
    return {
        "approval_object_result": {
            "approval_identity_matches": (
                object_result.approval_identity_matches
            ),
            "approval_object_valid": object_result.approval_object_valid,
            "approval_policy_version": (
                object_result.approval_policy_version
            ),
            "approval_schema_version": (
                object_result.approval_schema_version
            ),
            "approval_state": object_result.approval_state.value,
            "canonical_approval_content_sha256": (
                object_result.canonical_approval_content_sha256
            ),
            "declared_operator_approved_source_policy_sha256": (
                object_result
                .declared_operator_approved_source_policy_sha256
            ),
            "diagnostics": [
                diagnostic.value
                for diagnostic in object_result.diagnostics
            ],
            "object_structure_valid": (
                object_result.object_structure_valid
            ),
            "object_validation_performed": (
                object_result.object_validation_performed
            ),
            "result_version": object_result.result_version,
            "semantic_validation_performed": (
                object_result.semantic_validation_performed
            ),
            "source_count": object_result.source_count,
        },
        "artifact_contract_valid": artifact_contract_valid,
        "composition_diagnostics": [
            diagnostic.value
            for diagnostic in composition_diagnostics
        ],
        "composition_state": composition_state.value,
        "composition_validation_performed": True,
        "composition_version": COMPOSITION_VERSION,
        "conversion": {
            "exact_builtin_conversion_performed": True,
            "exact_builtin_conversion_valid": True,
        },
        "object_contract_validation_performed": True,
        "parsed_identity_binding": {
            "parsed_identity_recheck_matches": True,
            "parsed_identity_recheck_performed": True,
        },
        "parser_result": {
            "diagnostics": [
                diagnostic.value
                for diagnostic in parser_result.diagnostics
            ],
            "parse_state": parser_result.parse_state.value,
            "parse_valid": parser_result.parse_valid,
            "parsed_value_available": parser_result.parsed_value_available,
            "parsed_value_identity_sha256": (
                parser_result.parsed_value_identity_sha256
            ),
            "parser_version": parser_result.parser_version,
            "parsing_performed": parser_result.parsing_performed,
            "raw_artifact_sha256": parser_result.raw_artifact_sha256,
            "raw_artifact_size_bytes": (
                parser_result.raw_artifact_size_bytes
            ),
            "result_version": parser_result.result_version,
        },
        "result_version": RESULT_VERSION,
        "root_object": {
            "root_object_check_performed": True,
            "root_object_valid": True,
        },
    }


def _branch_values(
    state: Step2MarketSourcePolicyApprovalArtifactState,
) -> tuple[Any, ...]:
    if type(state) is not Step2MarketSourcePolicyApprovalArtifactState:
        _invariant_failure()
    matrices = {
        _State.INPUT_ABSENT: (
            False, None, False, None, False, None, False, False, None
        ),
        _State.INPUT_TYPE_INVALID: (
            False, None, False, None, False, None, False, False, None
        ),
        _State.RAW_PARSE_INVALID: (
            False, None, False, None, False, None, False, True, False
        ),
        _State.ROOT_TYPE_INVALID: (
            True, False, False, None, False, None, False, True, False
        ),
        _State.PARSED_IDENTITY_BINDING_INVALID: (
            True, True, True, True, True, False, False, True, False
        ),
        _State.OBJECT_CONTRACT_INVALID: (
            True, True, True, True, True, True, True, True, False
        ),
        _State.VALID_EMPTY: (
            True, True, True, True, True, True, True, True, True
        ),
        _State.VALID_NONEMPTY: (
            True, True, True, True, True, True, True, True, True
        ),
    }
    diagnostics = {
        _State.INPUT_ABSENT: (
            _Diagnostic.APPROVAL_ARTIFACT_INPUT_MISSING,
        ),
        _State.INPUT_TYPE_INVALID: (
            _Diagnostic.APPROVAL_ARTIFACT_INPUT_TYPE_INVALID,
        ),
        _State.RAW_PARSE_INVALID: (
            _Diagnostic.APPROVAL_ARTIFACT_RAW_PARSE_INVALID,
        ),
        _State.ROOT_TYPE_INVALID: (
            _Diagnostic.APPROVAL_ARTIFACT_ROOT_NOT_OBJECT,
        ),
        _State.PARSED_IDENTITY_BINDING_INVALID: (
            _Diagnostic.APPROVAL_ARTIFACT_PARSED_IDENTITY_BINDING_INVALID,
        ),
        _State.OBJECT_CONTRACT_INVALID: (
            _Diagnostic.APPROVAL_ARTIFACT_OBJECT_CONTRACT_INVALID,
        ),
        _State.VALID_EMPTY: (),
        _State.VALID_NONEMPTY: (),
    }
    if state not in matrices or state not in diagnostics:
        _invariant_failure()
    return (*matrices[state], diagnostics[state])


def _validate_result_components(
    *,
    composition_state: Step2MarketSourcePolicyApprovalArtifactState,
    parser_result: Step2MarketSourcePolicyApprovalParseResult,
    approval_object_validation_result: (
        Step2MarketSourcePolicyApprovalsObjectValidationResult | None
    ),
    root_object_check_performed: bool,
    root_object_valid: bool | None,
    exact_builtin_conversion_performed: bool,
    exact_builtin_conversion_valid: bool | None,
    parsed_identity_recheck_performed: bool,
    parsed_identity_recheck_matches: bool | None,
    object_contract_validation_performed: bool,
    composition_validation_performed: bool,
    artifact_contract_valid: bool | None,
    diagnostics: tuple[
        Step2MarketSourcePolicyApprovalArtifactDiagnostic,
        ...,
    ],
) -> None:
    _validate_parser_result(parser_result)
    expected = _branch_values(composition_state)
    actual = (
        root_object_check_performed,
        root_object_valid,
        exact_builtin_conversion_performed,
        exact_builtin_conversion_valid,
        parsed_identity_recheck_performed,
        parsed_identity_recheck_matches,
        object_contract_validation_performed,
        composition_validation_performed,
        artifact_contract_valid,
        diagnostics,
    )
    if (
        actual != expected
        or type(diagnostics) is not tuple
        or any(
            type(diagnostic)
            is not Step2MarketSourcePolicyApprovalArtifactDiagnostic
            for diagnostic in diagnostics
        )
        or len(diagnostics) > MAX_APPROVAL_ARTIFACT_COMPOSITION_DIAGNOSTICS
    ):
        _invariant_failure()

    parse_state = parser_result.parse_state
    if composition_state is _State.INPUT_ABSENT:
        parse_state_valid = parse_state is _ParseState.INPUT_ABSENT
    elif composition_state is _State.INPUT_TYPE_INVALID:
        parse_state_valid = parse_state is _ParseState.INPUT_TYPE_INVALID
    elif composition_state is _State.RAW_PARSE_INVALID:
        parse_state_valid = parse_state not in {
            _ParseState.INPUT_ABSENT,
            _ParseState.INPUT_TYPE_INVALID,
            _ParseState.VALID,
        }
    else:
        parse_state_valid = parse_state is _ParseState.VALID
    if not parse_state_valid:
        _invariant_failure()

    object_result = approval_object_validation_result
    if (object_result is not None) is not object_contract_validation_performed:
        _invariant_failure()
    if object_result is not None:
        _validate_object_result_fields(object_result)

    if composition_state is _State.OBJECT_CONTRACT_INVALID:
        if object_result.approval_state not in {
            _ObjectState.STRUCTURALLY_INVALID,
            _ObjectState.SEMANTICALLY_INVALID,
        }:
            _invariant_failure()
    elif composition_state is _State.VALID_EMPTY:
        if object_result.approval_state is not _ObjectState.VALID_EMPTY:
            _invariant_failure()
    elif composition_state is _State.VALID_NONEMPTY:
        if object_result.approval_state is not _ObjectState.VALID_NONEMPTY:
            _invariant_failure()


def _is_exact_frozen_json_value(value: object) -> bool:
    return value is None or type(value) in {
        str,
        FrozenJsonObject,
        FrozenJsonArray,
    }


def _validate_frozen_value_tree(value: object) -> None:
    active_container_ids: set[int] = set()
    node_count = 0
    stack: list[tuple[str, object, int]] = [("value", value, 0)]

    while stack:
        operation, current, depth = stack.pop()
        if operation == "leave":
            container_id = current
            if container_id not in active_container_ids:
                _invariant_failure()
            active_container_ids.remove(container_id)
            continue
        if operation != "value" or depth > MAX_JSON_NESTING_DEPTH:
            _invariant_failure()

        node_count += 1
        if node_count > MAX_JSON_NODE_COUNT:
            _invariant_failure()
        if current is None:
            continue
        if type(current) is str:
            _validate_decoded_string(current)
            continue
        if type(current) is FrozenJsonArray:
            if (
                type(current.items) is not tuple
                or len(current.items) > MAX_ARRAY_ITEM_COUNT
            ):
                _invariant_failure()
            container_id = id(current)
            if container_id in active_container_ids:
                _invariant_failure()
            active_container_ids.add(container_id)
            stack.append(("leave", container_id, depth))
            for child in reversed(current.items):
                stack.append(("value", child, depth + 1))
            continue
        if type(current) is FrozenJsonObject:
            if (
                type(current.items) is not tuple
                or len(current.items) > MAX_OBJECT_MEMBER_COUNT
            ):
                _invariant_failure()
            keys: set[str] = set()
            children: list[object] = []
            for item in current.items:
                if type(item) is not tuple or len(item) != 2:
                    _invariant_failure()
                key, child = item
                if type(key) is not str or key in keys:
                    _invariant_failure()
                _validate_decoded_string(key)
                keys.add(key)
                children.append(child)
            container_id = id(current)
            if container_id in active_container_ids:
                _invariant_failure()
            active_container_ids.add(container_id)
            stack.append(("leave", container_id, depth))
            for child in reversed(children):
                stack.append(("value", child, depth + 1))
            continue
        _invariant_failure()

    if active_container_ids:
        _invariant_failure()


def _is_bounded_raw_size(value: object) -> bool:
    return type(value) is int and 1 <= value <= 2_097_152


def _is_source_count(value: object) -> bool:
    return type(value) is int and 0 <= value <= 64


def _is_sha256(value: object) -> bool:
    return (
        type(value) is str
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _is_exact_string(value: object, expected: str) -> bool:
    return type(value) is str and value == expected


def _is_policy_version(value: object) -> bool:
    return (
        type(value) is str
        and 1 <= len(value) <= 64
        and _POLICY_VERSION_RE.fullmatch(value) is not None
        and value.lower() not in _RESERVED_POLICY_VERSIONS
    )


def _valid_optional_policy_version(value: object) -> bool:
    return value is None or _is_policy_version(value)


def _validate_decoded_string(value: str) -> None:
    if len(value) > MAX_DECODED_STRING_CODE_POINTS or any(
        0xD800 <= ord(character) <= 0xDFFF for character in value
    ):
        _invariant_failure()


def _invariant_failure() -> NoReturn:
    raise _CompositionInvariantError(_INVARIANT_MESSAGE)


__all__ = [
    "ACTIVATION_EVALUATION_PERFORMED",
    "ARTIFACT_RESULT_BOOLEAN_COERCION_ERROR",
    "AUTHORITY_SCOPE",
    "CANDIDATE_VALIDITY_EVALUATED",
    "COMPOSITION_VERSION",
    "FRESHNESS_EVALUATION_PERFORMED",
    "MAX_APPROVAL_ARTIFACT_COMPOSITION_DIAGNOSTICS",
    "MAX_ARRAY_ITEM_COUNT",
    "MAX_ARTIFACT_VALIDATION_BINDING_BYTES",
    "MAX_DECODED_STRING_CODE_POINTS",
    "MAX_JSON_NESTING_DEPTH",
    "MAX_JSON_NODE_COUNT",
    "MAX_OBJECT_MEMBER_COUNT",
    "MAX_PARSED_VALUE_CANONICAL_BYTES",
    "NOT_TRADE_AUTHORIZATION",
    "OPERATOR_AUTHENTICATION_PERFORMED",
    "ORDER_COMPILATION_EVALUATED",
    "PUBLICATION_EVALUATION_PERFORMED",
    "RESULT_VERSION",
    "SOURCE_RESOLUTION_PERFORMED",
    "Step2MarketSourcePolicyApprovalArtifactDiagnostic",
    "Step2MarketSourcePolicyApprovalArtifactState",
    "Step2MarketSourcePolicyApprovalArtifactValidationResult",
    "TRADE_PERMISSION_EFFECT",
    "UNIVERSE_RESOLUTION_PERFORMED",
    "WORKFLOW_PERMISSION_EVALUATED",
    "validate_step2_market_source_policy_approval_artifact_bytes",
]
