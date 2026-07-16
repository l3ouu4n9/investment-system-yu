"""Bind an operator-controlled expected identity to a b1p2 result.

This module compares one syntactically valid expected digest with one exact,
contract-valid Step 2 market-source-policy approval artifact validation result.
It does not authenticate an operator, infer approval, activate or materialize a
policy, resolve sources, affect workflows, publish artifacts, compile orders,
or grant trading authority.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields, is_dataclass
from enum import Enum
from hashlib import sha256
import hmac
import json
import re
from types import NoneType
from typing import Any, NoReturn, get_args, get_type_hints

from investment_orchestrator.validators.validate_step2_market_source_policy_approval_artifact import (
    ACTIVATION_EVALUATION_PERFORMED as _ARTIFACT_ACTIVATION_EVALUATION_PERFORMED,
    AUTHORITY_SCOPE as _ARTIFACT_AUTHORITY_SCOPE,
    CANDIDATE_VALIDITY_EVALUATED as _ARTIFACT_CANDIDATE_VALIDITY_EVALUATED,
    COMPOSITION_VERSION as _ARTIFACT_COMPOSITION_VERSION,
    FRESHNESS_EVALUATION_PERFORMED as _ARTIFACT_FRESHNESS_EVALUATION_PERFORMED,
    MAX_APPROVAL_ARTIFACT_COMPOSITION_DIAGNOSTICS,
    MAX_ARRAY_ITEM_COUNT,
    MAX_ARTIFACT_VALIDATION_BINDING_BYTES,
    MAX_DECODED_STRING_CODE_POINTS,
    MAX_JSON_NESTING_DEPTH,
    MAX_JSON_NODE_COUNT,
    MAX_OBJECT_MEMBER_COUNT,
    NOT_TRADE_AUTHORIZATION as _ARTIFACT_NOT_TRADE_AUTHORIZATION,
    OPERATOR_AUTHENTICATION_PERFORMED as _ARTIFACT_OPERATOR_AUTHENTICATION_PERFORMED,
    ORDER_COMPILATION_EVALUATED as _ARTIFACT_ORDER_COMPILATION_EVALUATED,
    PUBLICATION_EVALUATION_PERFORMED as _ARTIFACT_PUBLICATION_EVALUATION_PERFORMED,
    RESULT_VERSION as _ARTIFACT_RESULT_VERSION,
    SOURCE_RESOLUTION_PERFORMED as _ARTIFACT_SOURCE_RESOLUTION_PERFORMED,
    Step2MarketSourcePolicyApprovalArtifactDiagnostic,
    Step2MarketSourcePolicyApprovalArtifactState,
    Step2MarketSourcePolicyApprovalArtifactValidationResult,
    TRADE_PERMISSION_EFFECT as _ARTIFACT_TRADE_PERMISSION_EFFECT,
    UNIVERSE_RESOLUTION_PERFORMED as _ARTIFACT_UNIVERSE_RESOLUTION_PERFORMED,
    WORKFLOW_PERMISSION_EVALUATED as _ARTIFACT_WORKFLOW_PERMISSION_EVALUATED,
)


MAX_EXPECTED_IDENTITY_BINDING_BYTES = 2_048
MAX_EXPECTED_IDENTITY_BINDING_DIAGNOSTICS = 1

RESULT_VERSION = (
    "step2_market_source_policy_approval_expected_identity_binding_result_v1"
)
BINDING_VERSION = (
    "step2_market_source_policy_approval_expected_identity_binding_v1"
)
AUTHORITY_SCOPE = "operator_controlled_expected_identity_comparison_only"
NOT_TRADE_AUTHORIZATION = True
TRADE_PERMISSION_EFFECT = "none"
OPERATOR_AUTHENTICATION_PERFORMED = False
OPERATOR_APPROVAL_INFERRED = False
ACTIVATION_EVALUATION_PERFORMED = False
ACTIVE_POLICY_MATERIALIZATION_PERFORMED = False
SOURCE_RESOLUTION_PERFORMED = False
FRESHNESS_EVALUATION_PERFORMED = False
PUBLICATION_EVALUATION_PERFORMED = False
WORKFLOW_PERMISSION_EVALUATED = False
ORDER_COMPILATION_EVALUATED = False

BINDING_RESULT_BOOLEAN_COERCION_ERROR = (
    "inspect binding_valid explicitly; Step 2 source-policy approval "
    "expected-identity binding results have no truth value"
)

_ARTIFACT_IDENTITY_DOMAIN = (
    b"step2_market_source_policy_approval_artifact_validation_v1\0"
)
_BINDING_IDENTITY_DOMAIN = (
    b"step2_market_source_policy_approval_expected_identity_binding_v1\0"
)
_EMPTY_BYTES_SHA256 = (
    "e3b0c44298fc1c149afbf4c8996fb924"
    "27ae41e4649b934ca495991b7852b855"
)
_INVARIANT_MESSAGE = (
    "Step 2 source-policy approval expected-identity binding invariant "
    "violated"
)
_RESULT_CONSTRUCTION_ERROR = (
    "Step 2 source-policy approval expected-identity binding results are "
    "created only by the public binder"
)
_POLICY_VERSION_RE = re.compile(
    r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$"
)
_RESERVED_POLICY_VERSIONS = frozenset(
    {"latest", "current", "default", "*"}
)


class Step2MarketSourcePolicyApprovalExpectedIdentityBindingState(
    str,
    Enum,
):
    EXPECTED_IDENTITY_INPUT_ABSENT = (
        "expected_identity_input_absent"
    )
    EXPECTED_IDENTITY_INPUT_TYPE_INVALID = (
        "expected_identity_input_type_invalid"
    )
    EXPECTED_IDENTITY_INPUT_SYNTAX_INVALID = (
        "expected_identity_input_syntax_invalid"
    )
    ARTIFACT_VALIDATION_RESULT_TYPE_INVALID = (
        "artifact_validation_result_type_invalid"
    )
    ARTIFACT_CONTRACT_NOT_VALID = "artifact_contract_not_valid"
    EXPECTED_IDENTITY_MISMATCH = "expected_identity_mismatch"
    EXPECTED_IDENTITY_MATCH = "expected_identity_match"


class Step2MarketSourcePolicyApprovalExpectedIdentityBindingDiagnostic(
    str,
    Enum,
):
    EXPECTED_IDENTITY_INPUT_MISSING = (
        "expected_identity_input_missing"
    )
    EXPECTED_IDENTITY_INPUT_TYPE_INVALID = (
        "expected_identity_input_type_invalid"
    )
    EXPECTED_IDENTITY_INPUT_SYNTAX_INVALID = (
        "expected_identity_input_syntax_invalid"
    )
    APPROVAL_ARTIFACT_VALIDATION_RESULT_TYPE_INVALID = (
        "approval_artifact_validation_result_type_invalid"
    )
    APPROVAL_ARTIFACT_CONTRACT_NOT_VALID = (
        "approval_artifact_contract_not_valid"
    )
    EXPECTED_IDENTITY_MISMATCH = "expected_identity_mismatch"


class _ExpectedIdentityBindingInvariantError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True, init=False)
class Step2MarketSourcePolicyApprovalExpectedIdentityBindingResult:
    """Frozen comparison result with no authentication or authority."""

    result_version: str = field(default=RESULT_VERSION, init=False)
    binding_version: str = field(default=BINDING_VERSION, init=False)
    binding_state: (
        Step2MarketSourcePolicyApprovalExpectedIdentityBindingState
    )
    artifact_validation_result: (
        Step2MarketSourcePolicyApprovalArtifactValidationResult | None
    )
    expected_artifact_validation_identity_sha256: str | None
    actual_artifact_validation_identity_sha256: str | None
    expected_identity_binding_sha256: str | None
    artifact_result_check_performed: bool
    artifact_result_invariant_validation_performed: bool
    artifact_result_binding_eligible: bool | None
    artifact_identity_recheck_performed: bool
    artifact_identity_recheck_matches: bool | None
    identity_comparison_performed: bool
    identity_matches: bool | None
    binding_valid: bool | None
    diagnostics: tuple[
        Step2MarketSourcePolicyApprovalExpectedIdentityBindingDiagnostic,
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
    operator_approval_inferred: bool = field(
        default=OPERATOR_APPROVAL_INFERRED,
        init=False,
    )
    activation_evaluation_performed: bool = field(
        default=ACTIVATION_EVALUATION_PERFORMED,
        init=False,
    )
    active_policy_materialization_performed: bool = field(
        default=ACTIVE_POLICY_MATERIALIZATION_PERFORMED,
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
        raise TypeError(BINDING_RESULT_BOOLEAN_COERCION_ERROR)


_State = Step2MarketSourcePolicyApprovalExpectedIdentityBindingState
_Diagnostic = Step2MarketSourcePolicyApprovalExpectedIdentityBindingDiagnostic
_ArtifactState = Step2MarketSourcePolicyApprovalArtifactState
_ArtifactDiagnostic = Step2MarketSourcePolicyApprovalArtifactDiagnostic

_ARTIFACT_TYPE_HINTS = get_type_hints(
    Step2MarketSourcePolicyApprovalArtifactValidationResult
)
_PARSER_RESULT_TYPE = _ARTIFACT_TYPE_HINTS["parser_result"]
_OBJECT_RESULT_TYPE = next(
    item
    for item in get_args(
        _ARTIFACT_TYPE_HINTS["approval_object_validation_result"]
    )
    if item is not NoneType
)
_PARSER_TYPE_HINTS = get_type_hints(_PARSER_RESULT_TYPE)
_OBJECT_TYPE_HINTS = get_type_hints(_OBJECT_RESULT_TYPE)
_PARSE_STATE_TYPE = _PARSER_TYPE_HINTS["parse_state"]
_PARSE_DIAGNOSTIC_TYPE = get_args(
    _PARSER_TYPE_HINTS["diagnostics"]
)[0]
_OBJECT_STATE_TYPE = _OBJECT_TYPE_HINTS["approval_state"]
_OBJECT_DIAGNOSTIC_TYPE = get_args(
    _OBJECT_TYPE_HINTS["diagnostics"]
)[0]
_PARSER_FIELD_DEFAULTS = {
    item.name: item.default for item in fields(_PARSER_RESULT_TYPE)
}
_OBJECT_FIELD_DEFAULTS = {
    item.name: item.default for item in fields(_OBJECT_RESULT_TYPE)
}
_NESTED_PARSE_RESULT_REVISION = _PARSER_FIELD_DEFAULTS["result_version"]
_NESTED_PARSE_ENGINE_REVISION = _PARSER_FIELD_DEFAULTS["parser_version"]
_NESTED_PARSE_AUTHORITY = _PARSER_FIELD_DEFAULTS["authority_scope"]
_NESTED_OBJECT_RESULT_REVISION = _OBJECT_FIELD_DEFAULTS["result_version"]
_NESTED_OBJECT_AUTHORITY = _OBJECT_FIELD_DEFAULTS["authority_scope"]
_NESTED_OBJECT_SCHEMA = "_".join(
    ("step2", "market", "source", "policy", "approvals", "v1")
)
_FROZEN_VALUE_TYPES = frozenset(
    get_args(_PARSER_TYPE_HINTS["immutable_parsed_value"])
)
_FROZEN_OBJECT_TYPE = next(
    item
    for item in _FROZEN_VALUE_TYPES
    if is_dataclass(item)
    and tuple(member.name for member in fields(item)) == ("items",)
    and get_args(get_args(get_type_hints(item)["items"])[0])[:1]
    == (str,)
)
_FROZEN_ARRAY_TYPE = next(
    item
    for item in _FROZEN_VALUE_TYPES
    if is_dataclass(item)
    and tuple(member.name for member in fields(item)) == ("items",)
    and item is not _FROZEN_OBJECT_TYPE
)
_ParseState = _PARSE_STATE_TYPE
_ParseDiagnostic = _PARSE_DIAGNOSTIC_TYPE
_ObjectState = _OBJECT_STATE_TYPE
_ObjectDiagnostic = _OBJECT_DIAGNOSTIC_TYPE
_OBJECT_SCHEMA_UNSUPPORTED_DIAGNOSTIC = _ObjectDiagnostic(
    "approval_schema_version_unsupported"
)

_STRUCTURALLY_CAPABLE_OBJECT_DIAGNOSTICS = frozenset(
    {
        _ObjectDiagnostic.APPROVAL_INPUT_INVALID,
        _OBJECT_SCHEMA_UNSUPPORTED_DIAGNOSTIC,
        _ObjectDiagnostic.APPROVAL_DECLARED_IDENTITY_INVALID,
        _ObjectDiagnostic.APPROVAL_POLICY_VERSION_INVALID,
        _ObjectDiagnostic.APPROVAL_PROVENANCE_INVALID,
        _ObjectDiagnostic.CANONICAL_SOURCE_ID_INVALID,
        _ObjectDiagnostic.SOURCE_VERSION_INVALID,
        _ObjectDiagnostic.ALIAS_INVALID,
        _ObjectDiagnostic.PERMISSION_FIELD_INVALID,
    }
)

_IDENTITY_BEARING_ARTIFACT_STATES = frozenset(
    {
        _ArtifactState.OBJECT_CONTRACT_INVALID,
        _ArtifactState.VALID_EMPTY,
        _ArtifactState.VALID_NONEMPTY,
    }
)
_BINDING_ELIGIBLE_ARTIFACT_STATES = frozenset(
    {
        _ArtifactState.VALID_EMPTY,
        _ArtifactState.VALID_NONEMPTY,
    }
)


def bind_step2_market_source_policy_approval_expected_identity(
    *,
    artifact_validation_result: object,
    expected_artifact_validation_identity_sha256: object,
) -> Step2MarketSourcePolicyApprovalExpectedIdentityBindingResult:
    """Compare one expected digest with one exact accepted b1p2 result."""
    return _create_result(
        artifact_validation_result=artifact_validation_result,
        expected_identity_input=(
            expected_artifact_validation_identity_sha256
        ),
    )


def _create_result(
    *,
    artifact_validation_result: object,
    expected_identity_input: object,
) -> Step2MarketSourcePolicyApprovalExpectedIdentityBindingResult:
    artifact_result = None
    expected_identity = None
    actual_identity = None
    artifact_result_check_performed = False
    artifact_result_invariant_validation_performed = False
    artifact_result_binding_eligible = None
    artifact_identity_recheck_performed = False
    artifact_identity_recheck_matches = None
    identity_comparison_performed = False
    identity_matches = None

    if expected_identity_input is None:
        state = _State.EXPECTED_IDENTITY_INPUT_ABSENT
    elif type(expected_identity_input) is not str:
        state = _State.EXPECTED_IDENTITY_INPUT_TYPE_INVALID
    elif not _is_sha256(expected_identity_input):
        state = _State.EXPECTED_IDENTITY_INPUT_SYNTAX_INVALID
    else:
        expected_identity = expected_identity_input
        artifact_result_check_performed = True
        artifact_result_binding_eligible = False
        if (
            type(artifact_validation_result)
            is not Step2MarketSourcePolicyApprovalArtifactValidationResult
        ):
            state = _State.ARTIFACT_VALIDATION_RESULT_TYPE_INVALID
        else:
            artifact_result = artifact_validation_result
            _validate_artifact_result(artifact_result)
            artifact_result_invariant_validation_performed = True
            artifact_state = artifact_result.composition_state
            if artifact_state in _IDENTITY_BEARING_ARTIFACT_STATES:
                recomputed_identity = _artifact_validation_identity(
                    artifact_result
                )
                stored_identity = (
                    artifact_result.artifact_validation_identity_sha256
                )
                if not hmac.compare_digest(
                    recomputed_identity,
                    stored_identity,
                ):
                    _invariant_failure()
                artifact_identity_recheck_performed = True
                artifact_identity_recheck_matches = True

            if artifact_state in _BINDING_ELIGIBLE_ARTIFACT_STATES:
                actual_identity = (
                    artifact_result.artifact_validation_identity_sha256
                )
                artifact_result_binding_eligible = True
                identity_comparison_performed = True
                identity_matches = hmac.compare_digest(
                    expected_identity,
                    actual_identity,
                )
                state = (
                    _State.EXPECTED_IDENTITY_MATCH
                    if identity_matches
                    else _State.EXPECTED_IDENTITY_MISMATCH
                )
            else:
                state = _State.ARTIFACT_CONTRACT_NOT_VALID

    diagnostics = _diagnostics_for_state(state)
    binding_valid = _binding_valid_for_state(state)
    _validate_result_components(
        state=state,
        artifact_validation_result=artifact_result,
        expected_identity=expected_identity,
        actual_identity=actual_identity,
        artifact_result_check_performed=artifact_result_check_performed,
        artifact_result_invariant_validation_performed=(
            artifact_result_invariant_validation_performed
        ),
        artifact_result_binding_eligible=(
            artifact_result_binding_eligible
        ),
        artifact_identity_recheck_performed=(
            artifact_identity_recheck_performed
        ),
        artifact_identity_recheck_matches=(
            artifact_identity_recheck_matches
        ),
        identity_comparison_performed=identity_comparison_performed,
        identity_matches=identity_matches,
        binding_valid=binding_valid,
        diagnostics=diagnostics,
    )

    binding_identity = None
    if identity_comparison_performed:
        binding_identity = _binding_identity(
            artifact_validation_result=artifact_result,
            expected_identity=expected_identity,
            actual_identity=actual_identity,
            state=state,
            identity_matches=identity_matches,
            binding_valid=binding_valid,
            diagnostics=diagnostics,
        )
        if not _is_sha256(binding_identity):
            _invariant_failure()

    result = object.__new__(
        Step2MarketSourcePolicyApprovalExpectedIdentityBindingResult
    )
    values = {
        "result_version": RESULT_VERSION,
        "binding_version": BINDING_VERSION,
        "binding_state": state,
        "artifact_validation_result": artifact_result,
        "expected_artifact_validation_identity_sha256": expected_identity,
        "actual_artifact_validation_identity_sha256": actual_identity,
        "expected_identity_binding_sha256": binding_identity,
        "artifact_result_check_performed": (
            artifact_result_check_performed
        ),
        "artifact_result_invariant_validation_performed": (
            artifact_result_invariant_validation_performed
        ),
        "artifact_result_binding_eligible": (
            artifact_result_binding_eligible
        ),
        "artifact_identity_recheck_performed": (
            artifact_identity_recheck_performed
        ),
        "artifact_identity_recheck_matches": (
            artifact_identity_recheck_matches
        ),
        "identity_comparison_performed": identity_comparison_performed,
        "identity_matches": identity_matches,
        "binding_valid": binding_valid,
        "diagnostics": diagnostics,
        "authority_scope": AUTHORITY_SCOPE,
        "not_trade_authorization": NOT_TRADE_AUTHORIZATION,
        "trade_permission_effect": TRADE_PERMISSION_EFFECT,
        "operator_authentication_performed": (
            OPERATOR_AUTHENTICATION_PERFORMED
        ),
        "operator_approval_inferred": OPERATOR_APPROVAL_INFERRED,
        "activation_evaluation_performed": (
            ACTIVATION_EVALUATION_PERFORMED
        ),
        "active_policy_materialization_performed": (
            ACTIVE_POLICY_MATERIALIZATION_PERFORMED
        ),
        "source_resolution_performed": SOURCE_RESOLUTION_PERFORMED,
        "freshness_evaluation_performed": FRESHNESS_EVALUATION_PERFORMED,
        "publication_evaluation_performed": (
            PUBLICATION_EVALUATION_PERFORMED
        ),
        "workflow_permission_evaluated": WORKFLOW_PERMISSION_EVALUATED,
        "order_compilation_evaluated": ORDER_COMPILATION_EVALUATED,
    }
    for name, value in values.items():
        object.__setattr__(result, name, value)
    return result


def _validate_artifact_result(result: object) -> None:
    if (
        type(result)
        is not Step2MarketSourcePolicyApprovalArtifactValidationResult
    ):
        _invariant_failure()
    _validate_artifact_result_class_contract()
    if (
        not _is_exact_string(result.result_version, _ARTIFACT_RESULT_VERSION)
        or not _is_exact_string(
            result.composition_version,
            _ARTIFACT_COMPOSITION_VERSION,
        )
        or type(result.composition_state)
        is not Step2MarketSourcePolicyApprovalArtifactState
        or not _is_exact_string(
            result.authority_scope,
            _ARTIFACT_AUTHORITY_SCOPE,
        )
        or result.not_trade_authorization
        is not _ARTIFACT_NOT_TRADE_AUTHORIZATION
        or not _is_exact_string(
            result.trade_permission_effect,
            _ARTIFACT_TRADE_PERMISSION_EFFECT,
        )
        or result.operator_authentication_performed
        is not _ARTIFACT_OPERATOR_AUTHENTICATION_PERFORMED
        or result.source_resolution_performed
        is not _ARTIFACT_SOURCE_RESOLUTION_PERFORMED
        or result.freshness_evaluation_performed
        is not _ARTIFACT_FRESHNESS_EVALUATION_PERFORMED
        or result.universe_resolution_performed
        is not _ARTIFACT_UNIVERSE_RESOLUTION_PERFORMED
        or result.candidate_validity_evaluated
        is not _ARTIFACT_CANDIDATE_VALIDITY_EVALUATED
        or result.activation_evaluation_performed
        is not _ARTIFACT_ACTIVATION_EVALUATION_PERFORMED
        or result.publication_evaluation_performed
        is not _ARTIFACT_PUBLICATION_EVALUATION_PERFORMED
        or result.workflow_permission_evaluated
        is not _ARTIFACT_WORKFLOW_PERMISSION_EVALUATED
        or result.order_compilation_evaluated
        is not _ARTIFACT_ORDER_COMPILATION_EVALUATED
        or type(result.diagnostics) is not tuple
        or any(
            type(diagnostic)
            is not Step2MarketSourcePolicyApprovalArtifactDiagnostic
            for diagnostic in result.diagnostics
        )
        or len(result.diagnostics)
        > MAX_APPROVAL_ARTIFACT_COMPOSITION_DIAGNOSTICS
    ):
        _invariant_failure()

    _validate_parser_result(result.parser_result)
    expected = _artifact_branch_values(result.composition_state)
    actual = (
        result.root_object_check_performed,
        result.root_object_valid,
        result.exact_builtin_conversion_performed,
        result.exact_builtin_conversion_valid,
        result.parsed_identity_recheck_performed,
        result.parsed_identity_recheck_matches,
        result.object_contract_validation_performed,
        result.composition_validation_performed,
        result.artifact_contract_valid,
        result.diagnostics,
    )
    if actual != expected:
        _invariant_failure()

    state = result.composition_state
    parse_state = result.parser_result.parse_state
    if state is _ArtifactState.INPUT_ABSENT:
        parse_state_valid = parse_state is _ParseState.INPUT_ABSENT
    elif state is _ArtifactState.INPUT_TYPE_INVALID:
        parse_state_valid = parse_state is _ParseState.INPUT_TYPE_INVALID
    elif state is _ArtifactState.RAW_PARSE_INVALID:
        parse_state_valid = parse_state not in {
            _ParseState.INPUT_ABSENT,
            _ParseState.INPUT_TYPE_INVALID,
            _ParseState.VALID,
        }
    else:
        parse_state_valid = parse_state is _ParseState.VALID
    if not parse_state_valid:
        _invariant_failure()

    parsed_value = result.parser_result.immutable_parsed_value
    if state is _ArtifactState.ROOT_TYPE_INVALID:
        if type(parsed_value) is _FROZEN_OBJECT_TYPE:
            _invariant_failure()
    elif state in {
        _ArtifactState.PARSED_IDENTITY_BINDING_INVALID,
        _ArtifactState.OBJECT_CONTRACT_INVALID,
        _ArtifactState.VALID_EMPTY,
        _ArtifactState.VALID_NONEMPTY,
    } and type(parsed_value) is not _FROZEN_OBJECT_TYPE:
        _invariant_failure()

    object_result = result.approval_object_validation_result
    object_present = object_result is not None
    if object_present is not result.object_contract_validation_performed:
        _invariant_failure()
    if object_result is not None:
        _validate_object_result_fields(object_result)

    if state is _ArtifactState.OBJECT_CONTRACT_INVALID:
        if object_result.approval_state not in {
            _ObjectState.STRUCTURALLY_INVALID,
            _ObjectState.SEMANTICALLY_INVALID,
        }:
            _invariant_failure()
    elif state is _ArtifactState.VALID_EMPTY:
        if object_result.approval_state is not _ObjectState.VALID_EMPTY:
            _invariant_failure()
    elif state is _ArtifactState.VALID_NONEMPTY:
        if object_result.approval_state is not _ObjectState.VALID_NONEMPTY:
            _invariant_failure()

    identity = result.artifact_validation_identity_sha256
    if state in _IDENTITY_BEARING_ARTIFACT_STATES:
        if not _is_sha256(identity) or object_result is None:
            _invariant_failure()
    elif identity is not None or object_result is not None:
        _invariant_failure()


def _validate_artifact_result_class_contract() -> None:
    result_type = Step2MarketSourcePolicyApprovalArtifactValidationResult
    parameters = getattr(result_type, "__dataclass_params__", None)
    slots = getattr(result_type, "__slots__", None)
    field_names = tuple(item.name for item in fields(result_type))
    if (
        parameters is None
        or parameters.init is not False
        or parameters.frozen is not True
        or type(slots) is not tuple
        or tuple(slots) != field_names
        or any(
            name not in result_type.__dict__
            for name in (
                "__new__",
                "__setstate__",
                "__reduce__",
                "__reduce_ex__",
            )
        )
    ):
        _invariant_failure()


def _artifact_branch_values(
    state: Step2MarketSourcePolicyApprovalArtifactState,
) -> tuple[Any, ...]:
    matrices = {
        _ArtifactState.INPUT_ABSENT: (
            False, None, False, None, False, None, False, False, None
        ),
        _ArtifactState.INPUT_TYPE_INVALID: (
            False, None, False, None, False, None, False, False, None
        ),
        _ArtifactState.RAW_PARSE_INVALID: (
            False, None, False, None, False, None, False, True, False
        ),
        _ArtifactState.ROOT_TYPE_INVALID: (
            True, False, False, None, False, None, False, True, False
        ),
        _ArtifactState.PARSED_IDENTITY_BINDING_INVALID: (
            True, True, True, True, True, False, False, True, False
        ),
        _ArtifactState.OBJECT_CONTRACT_INVALID: (
            True, True, True, True, True, True, True, True, False
        ),
        _ArtifactState.VALID_EMPTY: (
            True, True, True, True, True, True, True, True, True
        ),
        _ArtifactState.VALID_NONEMPTY: (
            True, True, True, True, True, True, True, True, True
        ),
    }
    diagnostics = {
        _ArtifactState.INPUT_ABSENT: (
            _ArtifactDiagnostic.APPROVAL_ARTIFACT_INPUT_MISSING,
        ),
        _ArtifactState.INPUT_TYPE_INVALID: (
            _ArtifactDiagnostic.APPROVAL_ARTIFACT_INPUT_TYPE_INVALID,
        ),
        _ArtifactState.RAW_PARSE_INVALID: (
            _ArtifactDiagnostic.APPROVAL_ARTIFACT_RAW_PARSE_INVALID,
        ),
        _ArtifactState.ROOT_TYPE_INVALID: (
            _ArtifactDiagnostic.APPROVAL_ARTIFACT_ROOT_NOT_OBJECT,
        ),
        _ArtifactState.PARSED_IDENTITY_BINDING_INVALID: (
            _ArtifactDiagnostic
            .APPROVAL_ARTIFACT_PARSED_IDENTITY_BINDING_INVALID,
        ),
        _ArtifactState.OBJECT_CONTRACT_INVALID: (
            _ArtifactDiagnostic.APPROVAL_ARTIFACT_OBJECT_CONTRACT_INVALID,
        ),
        _ArtifactState.VALID_EMPTY: (),
        _ArtifactState.VALID_NONEMPTY: (),
    }
    if state not in matrices or state not in diagnostics:
        _invariant_failure()
    return (*matrices[state], diagnostics[state])


def _validate_parser_result(result: object) -> None:
    if type(result) is not _PARSER_RESULT_TYPE:
        _invariant_failure()
    if (
        not _is_exact_string(
            result.result_version,
            _NESTED_PARSE_RESULT_REVISION,
        )
        or not _is_exact_string(
            result.parser_version,
            _NESTED_PARSE_ENGINE_REVISION,
        )
        or not _is_exact_string(
            result.authority_scope,
            _NESTED_PARSE_AUTHORITY,
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
        or type(result.parse_state) is not _PARSE_STATE_TYPE
        or type(result.diagnostics) is not tuple
        or any(
            type(diagnostic) is not _PARSE_DIAGNOSTIC_TYPE
            for diagnostic in result.diagnostics
        )
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


def _valid_raw_size_failure(result: Any, diagnostic: Any) -> bool:
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


def _valid_bounded_parse_failure(result: Any, diagnostic: Any) -> bool:
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


def _validate_object_result_fields(result: object) -> None:
    if type(result) is not _OBJECT_RESULT_TYPE:
        _invariant_failure()
    if (
        not _is_exact_string(
            result.result_version,
            _NESTED_OBJECT_RESULT_REVISION,
        )
        or not _is_exact_string(
            result.authority_scope,
            _NESTED_OBJECT_AUTHORITY,
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
        or type(result.approval_state) is not _OBJECT_STATE_TYPE
        or type(result.diagnostics) is not tuple
        or any(
            type(diagnostic) is not _OBJECT_DIAGNOSTIC_TYPE
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


def _valid_structurally_invalid_object_result(result: Any) -> bool:
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


def _valid_semantically_invalid_object_result(result: Any) -> bool:
    diagnostics = result.diagnostics
    ordered_content_diagnostics = tuple(
        diagnostic
        for diagnostic in _OBJECT_DIAGNOSTIC_TYPE
        if diagnostic in diagnostics
        and diagnostic
        not in {
            _ObjectDiagnostic.APPROVAL_INPUT_MISSING,
            _ObjectDiagnostic.APPROVAL_INPUT_INVALID,
            _OBJECT_SCHEMA_UNSUPPORTED_DIAGNOSTIC,
        }
    )
    if (
        not diagnostics
        or len(diagnostics) > 14
        or len(set(diagnostics)) != len(diagnostics)
        or diagnostics != ordered_content_diagnostics
        or not _is_exact_string(
            result.approval_schema_version,
            _NESTED_OBJECT_SCHEMA,
        )
        or not _valid_optional_policy_version(
            result.approval_policy_version
        )
        or not _is_sha256(result.canonical_approval_content_sha256)
        or result.object_validation_performed is not True
        or result.object_structure_valid is not True
        or result.semantic_validation_performed is not True
        or result.approval_object_valid is not False
        or not _is_source_count(result.source_count)
    ):
        return False
    if (
        result.approval_policy_version is None
        and _ObjectDiagnostic.APPROVAL_POLICY_VERSION_INVALID
        not in diagnostics
    ):
        return False

    declared = result.declared_operator_approved_source_policy_sha256
    canonical = result.canonical_approval_content_sha256
    identity = result.approval_identity_matches
    declared_invalid = (
        _ObjectDiagnostic.APPROVAL_DECLARED_IDENTITY_INVALID
        in diagnostics
    )
    mismatch = (
        _ObjectDiagnostic.APPROVAL_IDENTITY_MISMATCH in diagnostics
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


def _valid_approval_object_result(result: Any, *, empty: bool) -> bool:
    source_count = result.source_count
    return (
        _is_exact_string(
            result.approval_schema_version,
            _NESTED_OBJECT_SCHEMA,
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


def _artifact_validation_identity(
    result: Step2MarketSourcePolicyApprovalArtifactValidationResult,
) -> str:
    record = _artifact_binding_record(result)
    encoded = _canonical_record_bytes(
        record,
        maximum_bytes=MAX_ARTIFACT_VALIDATION_BINDING_BYTES,
    )
    digest = sha256(_ARTIFACT_IDENTITY_DOMAIN + encoded).hexdigest()
    if not _is_sha256(digest):
        _invariant_failure()
    return digest


def _artifact_binding_record(
    result: Step2MarketSourcePolicyApprovalArtifactValidationResult,
) -> dict[str, Any]:
    parser_result = result.parser_result
    object_result = result.approval_object_validation_result
    if object_result is None:
        _invariant_failure()
    return {
        "approval_object_result": {
            "approval_identity_matches": (
                object_result.approval_identity_matches
            ),
            "approval_object_valid": object_result.approval_object_valid,
            "approval_policy_version": object_result.approval_policy_version,
            "approval_schema_version": object_result.approval_schema_version,
            "approval_state": object_result.approval_state.value,
            "canonical_approval_content_sha256": (
                object_result.canonical_approval_content_sha256
            ),
            "declared_operator_approved_source_policy_sha256": (
                object_result
                .declared_operator_approved_source_policy_sha256
            ),
            "diagnostics": [
                diagnostic.value for diagnostic in object_result.diagnostics
            ],
            "object_structure_valid": object_result.object_structure_valid,
            "object_validation_performed": (
                object_result.object_validation_performed
            ),
            "result_version": object_result.result_version,
            "semantic_validation_performed": (
                object_result.semantic_validation_performed
            ),
            "source_count": object_result.source_count,
        },
        "artifact_contract_valid": result.artifact_contract_valid,
        "composition_diagnostics": [
            diagnostic.value for diagnostic in result.diagnostics
        ],
        "composition_state": result.composition_state.value,
        "composition_validation_performed": True,
        "composition_version": _ARTIFACT_COMPOSITION_VERSION,
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
                diagnostic.value for diagnostic in parser_result.diagnostics
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
        "result_version": _ARTIFACT_RESULT_VERSION,
        "root_object": {
            "root_object_check_performed": True,
            "root_object_valid": True,
        },
    }


def _binding_identity(
    *,
    artifact_validation_result: (
        Step2MarketSourcePolicyApprovalArtifactValidationResult | None
    ),
    expected_identity: str | None,
    actual_identity: str | None,
    state: Step2MarketSourcePolicyApprovalExpectedIdentityBindingState,
    identity_matches: bool | None,
    binding_valid: bool | None,
    diagnostics: tuple[
        Step2MarketSourcePolicyApprovalExpectedIdentityBindingDiagnostic,
        ...,
    ],
) -> str:
    if (
        artifact_validation_result is None
        or not _is_sha256(expected_identity)
        or not _is_sha256(actual_identity)
        or type(identity_matches) is not bool
        or type(binding_valid) is not bool
    ):
        _invariant_failure()
    record = _binding_record(
        artifact_validation_result=artifact_validation_result,
        expected_identity=expected_identity,
        actual_identity=actual_identity,
        state=state,
        identity_matches=identity_matches,
        binding_valid=binding_valid,
        diagnostics=diagnostics,
    )
    encoded = _canonical_record_bytes(
        record,
        maximum_bytes=MAX_EXPECTED_IDENTITY_BINDING_BYTES,
    )
    return sha256(_BINDING_IDENTITY_DOMAIN + encoded).hexdigest()


def _binding_record(
    *,
    artifact_validation_result: (
        Step2MarketSourcePolicyApprovalArtifactValidationResult
    ),
    expected_identity: str,
    actual_identity: str,
    state: Step2MarketSourcePolicyApprovalExpectedIdentityBindingState,
    identity_matches: bool,
    binding_valid: bool,
    diagnostics: tuple[
        Step2MarketSourcePolicyApprovalExpectedIdentityBindingDiagnostic,
        ...,
    ],
) -> dict[str, Any]:
    return {
        "artifact_identity_recheck": {
            "artifact_identity_recheck_matches": True,
            "artifact_identity_recheck_performed": True,
        },
        "artifact_result_binding_eligible": True,
        "artifact_result_check_performed": True,
        "artifact_result_invariant_validation_performed": True,
        "artifact_validation_result": {
            "artifact_contract_valid": True,
            "artifact_validation_identity_sha256": actual_identity,
            "composition_state": (
                artifact_validation_result.composition_state.value
            ),
            "composition_version": (
                artifact_validation_result.composition_version
            ),
            "result_version": artifact_validation_result.result_version,
        },
        "binding_diagnostics": [
            diagnostic.value for diagnostic in diagnostics
        ],
        "binding_state": state.value,
        "binding_valid": binding_valid,
        "binding_version": BINDING_VERSION,
        "expected_artifact_validation_identity_sha256": expected_identity,
        "identity_comparison": {
            "identity_comparison_performed": True,
            "identity_matches": identity_matches,
        },
        "result_version": RESULT_VERSION,
    }


def _canonical_record_bytes(
    record: dict[str, Any],
    *,
    maximum_bytes: int,
) -> bytes:
    serialized = json.dumps(
        record,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    if type(serialized) is not str:
        _invariant_failure()
    encoded = serialized.encode("utf-8")
    if len(encoded) > maximum_bytes:
        _invariant_failure()
    return encoded


def _validate_result_components(
    *,
    state: Step2MarketSourcePolicyApprovalExpectedIdentityBindingState,
    artifact_validation_result: (
        Step2MarketSourcePolicyApprovalArtifactValidationResult | None
    ),
    expected_identity: str | None,
    actual_identity: str | None,
    artifact_result_check_performed: bool,
    artifact_result_invariant_validation_performed: bool,
    artifact_result_binding_eligible: bool | None,
    artifact_identity_recheck_performed: bool,
    artifact_identity_recheck_matches: bool | None,
    identity_comparison_performed: bool,
    identity_matches: bool | None,
    binding_valid: bool | None,
    diagnostics: tuple[
        Step2MarketSourcePolicyApprovalExpectedIdentityBindingDiagnostic,
        ...,
    ],
) -> None:
    if type(state) is not Step2MarketSourcePolicyApprovalExpectedIdentityBindingState:
        _invariant_failure()
    if (
        type(diagnostics) is not tuple
        or any(
            type(diagnostic)
            is not Step2MarketSourcePolicyApprovalExpectedIdentityBindingDiagnostic
            for diagnostic in diagnostics
        )
        or len(diagnostics) > MAX_EXPECTED_IDENTITY_BINDING_DIAGNOSTICS
        or diagnostics != _diagnostics_for_state(state)
    ):
        _invariant_failure()

    exact_artifact_retained = artifact_validation_result is not None
    if (
        artifact_result_invariant_validation_performed
        is not exact_artifact_retained
        or (
            artifact_result_invariant_validation_performed
            and not artifact_result_check_performed
        )
        or artifact_identity_recheck_performed
        and not artifact_result_invariant_validation_performed
        or (artifact_identity_recheck_matches is not None)
        is not artifact_identity_recheck_performed
        or artifact_identity_recheck_matches is False
        or (actual_identity is not None)
        is not (artifact_result_binding_eligible is True)
        or identity_comparison_performed
        is not (artifact_result_binding_eligible is True)
        or (identity_matches is not None)
        is not identity_comparison_performed
        or binding_valid is not _binding_valid_for_state(state)
    ):
        _invariant_failure()

    if expected_identity is None:
        expected_valid = state in {
            _State.EXPECTED_IDENTITY_INPUT_ABSENT,
            _State.EXPECTED_IDENTITY_INPUT_TYPE_INVALID,
            _State.EXPECTED_IDENTITY_INPUT_SYNTAX_INVALID,
        }
    else:
        expected_valid = _is_sha256(expected_identity)
    if not expected_valid:
        _invariant_failure()

    expected_matrix = _binding_branch_values(
        state,
        artifact_validation_result,
    )
    actual_matrix = (
        artifact_result_check_performed,
        artifact_result_invariant_validation_performed,
        artifact_result_binding_eligible,
        artifact_identity_recheck_performed,
        artifact_identity_recheck_matches,
        identity_comparison_performed,
        identity_matches,
        binding_valid,
    )
    if actual_matrix != expected_matrix:
        _invariant_failure()


def _binding_branch_values(
    state: Step2MarketSourcePolicyApprovalExpectedIdentityBindingState,
    artifact_validation_result: (
        Step2MarketSourcePolicyApprovalArtifactValidationResult | None
    ),
) -> tuple[Any, ...]:
    if state in {
        _State.EXPECTED_IDENTITY_INPUT_ABSENT,
        _State.EXPECTED_IDENTITY_INPUT_TYPE_INVALID,
        _State.EXPECTED_IDENTITY_INPUT_SYNTAX_INVALID,
    }:
        return (False, False, None, False, None, False, None, None)
    if state is _State.ARTIFACT_VALIDATION_RESULT_TYPE_INVALID:
        return (True, False, False, False, None, False, None, None)
    if state is _State.ARTIFACT_CONTRACT_NOT_VALID:
        if artifact_validation_result is None:
            _invariant_failure()
        recheck = (
            artifact_validation_result.composition_state
            is _ArtifactState.OBJECT_CONTRACT_INVALID
        )
        return (
            True,
            True,
            False,
            recheck,
            True if recheck else None,
            False,
            None,
            False,
        )
    if state is _State.EXPECTED_IDENTITY_MISMATCH:
        return (True, True, True, True, True, True, False, False)
    if state is _State.EXPECTED_IDENTITY_MATCH:
        return (True, True, True, True, True, True, True, True)
    _invariant_failure()


def _diagnostics_for_state(
    state: Step2MarketSourcePolicyApprovalExpectedIdentityBindingState,
) -> tuple[
    Step2MarketSourcePolicyApprovalExpectedIdentityBindingDiagnostic,
    ...,
]:
    mapping = {
        _State.EXPECTED_IDENTITY_INPUT_ABSENT: (
            _Diagnostic.EXPECTED_IDENTITY_INPUT_MISSING,
        ),
        _State.EXPECTED_IDENTITY_INPUT_TYPE_INVALID: (
            _Diagnostic.EXPECTED_IDENTITY_INPUT_TYPE_INVALID,
        ),
        _State.EXPECTED_IDENTITY_INPUT_SYNTAX_INVALID: (
            _Diagnostic.EXPECTED_IDENTITY_INPUT_SYNTAX_INVALID,
        ),
        _State.ARTIFACT_VALIDATION_RESULT_TYPE_INVALID: (
            _Diagnostic.APPROVAL_ARTIFACT_VALIDATION_RESULT_TYPE_INVALID,
        ),
        _State.ARTIFACT_CONTRACT_NOT_VALID: (
            _Diagnostic.APPROVAL_ARTIFACT_CONTRACT_NOT_VALID,
        ),
        _State.EXPECTED_IDENTITY_MISMATCH: (
            _Diagnostic.EXPECTED_IDENTITY_MISMATCH,
        ),
        _State.EXPECTED_IDENTITY_MATCH: (),
    }
    if state not in mapping:
        _invariant_failure()
    return mapping[state]


def _binding_valid_for_state(
    state: Step2MarketSourcePolicyApprovalExpectedIdentityBindingState,
) -> bool | None:
    if state in {
        _State.EXPECTED_IDENTITY_INPUT_ABSENT,
        _State.EXPECTED_IDENTITY_INPUT_TYPE_INVALID,
        _State.EXPECTED_IDENTITY_INPUT_SYNTAX_INVALID,
        _State.ARTIFACT_VALIDATION_RESULT_TYPE_INVALID,
    }:
        return None
    return state is _State.EXPECTED_IDENTITY_MATCH


def _is_exact_frozen_json_value(value: object) -> bool:
    return value is None or type(value) in {
        str,
        _FROZEN_OBJECT_TYPE,
        _FROZEN_ARRAY_TYPE,
    }


def _validate_frozen_value_tree(value: object) -> None:
    active_container_ids: set[int] = set()
    node_count = 0
    stack: list[tuple[str, object, int]] = [("value", value, 0)]
    while stack:
        operation, current, depth = stack.pop()
        if operation == "leave":
            if current not in active_container_ids:
                _invariant_failure()
            active_container_ids.remove(current)
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
        if type(current) is _FROZEN_ARRAY_TYPE:
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
        if type(current) is _FROZEN_OBJECT_TYPE:
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
    raise _ExpectedIdentityBindingInvariantError(_INVARIANT_MESSAGE)


__all__ = [
    "ACTIVATION_EVALUATION_PERFORMED",
    "ACTIVE_POLICY_MATERIALIZATION_PERFORMED",
    "AUTHORITY_SCOPE",
    "BINDING_RESULT_BOOLEAN_COERCION_ERROR",
    "BINDING_VERSION",
    "FRESHNESS_EVALUATION_PERFORMED",
    "MAX_EXPECTED_IDENTITY_BINDING_BYTES",
    "MAX_EXPECTED_IDENTITY_BINDING_DIAGNOSTICS",
    "NOT_TRADE_AUTHORIZATION",
    "OPERATOR_APPROVAL_INFERRED",
    "OPERATOR_AUTHENTICATION_PERFORMED",
    "ORDER_COMPILATION_EVALUATED",
    "PUBLICATION_EVALUATION_PERFORMED",
    "RESULT_VERSION",
    "SOURCE_RESOLUTION_PERFORMED",
    "Step2MarketSourcePolicyApprovalExpectedIdentityBindingDiagnostic",
    "Step2MarketSourcePolicyApprovalExpectedIdentityBindingResult",
    "Step2MarketSourcePolicyApprovalExpectedIdentityBindingState",
    "TRADE_PERMISSION_EFFECT",
    "WORKFLOW_PERMISSION_EVALUATED",
    "bind_step2_market_source_policy_approval_expected_identity",
]
