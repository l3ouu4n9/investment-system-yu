"""Validate a decoded operator approval-intent statement contract.

This module deliberately validates only one already-decoded JSON value.  It
does not parse bytes, bind the statement to parser identities, authenticate an
operator, or authorize any lifecycle or trading action.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from enum import Enum
import hashlib
import json
import re
from typing import NoReturn


STATEMENT_SCHEMA_VERSION = (
    "step2_market_source_policy_operator_approval_intent_statement_v1"
)
VALIDATION_RESULT_VERSION = (
    "step2_market_source_policy_operator_approval_intent_statement_validation_result_v1"
)
VALIDATOR_VERSION = (
    "step2_market_source_policy_operator_approval_intent_statement_validator_v1"
)
OPERATOR_APPROVAL_INTENT_STATEMENT_IDENTITY_DOMAIN = (
    b"step2_market_source_policy_operator_approval_intent_statement_v1\0"
)
MAX_OPERATOR_APPROVAL_INTENT_STATEMENT_CANONICAL_BYTES = 512
MAX_OPERATOR_APPROVAL_INTENT_STATEMENT_VALIDATION_DIAGNOSTICS = 1

AUTHORITY_SCOPE = "operator_approval_intent_statement_contract_validation_only"
TRADE_PERMISSION_EFFECT = "none"

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

_RESULT_CONSTRUCTION_ERROR = (
    "operator approval-intent statement validation results are created only by "
    "the public validator"
)
_SNAPSHOT_CONSTRUCTION_ERROR = (
    "operator approval-intent statement snapshots are created only by the "
    "public validator"
)
_BOOLEAN_MISUSE_ERROR = (
    "inspect statement_contract_valid explicitly; operator approval-intent "
    "statement validation results have no truth value"
)
_INVARIANT_ERROR_MESSAGE = "operator approval-intent statement validator invariant violated"


class Step2MarketSourcePolicyOperatorApprovalIntentStatementValidationState(
    str,
    Enum,
):
    """The closed outcome set for decoded statement validation."""

    INPUT_TYPE_INVALID = "input_type_invalid"
    ROOT_TYPE_INVALID = "root_type_invalid"
    KEY_SET_INVALID = "key_set_invalid"
    FIELD_TYPE_INVALID = "field_type_invalid"
    FIELD_VALUE_INVALID = "field_value_invalid"
    VALID_APPROVE = "valid_approve"
    VALID_REJECT = "valid_reject"


class Step2MarketSourcePolicyOperatorApprovalIntentStatementValidationDiagnostic(
    str,
    Enum,
):
    """The closed, value-free diagnostic set for decoded statement validation."""

    STATEMENT_INPUT_TYPE_INVALID = "statement_input_type_invalid"
    STATEMENT_ROOT_TYPE_INVALID = "statement_root_type_invalid"
    STATEMENT_KEY_SET_INVALID = "statement_key_set_invalid"
    STATEMENT_SCHEMA_VERSION_INVALID = "statement_schema_version_invalid"
    AUTHENTICATION_CONTEXT_TYPE_INVALID = "authentication_context_type_invalid"
    AUTHENTICATION_CONTEXT_SYNTAX_INVALID = "authentication_context_syntax_invalid"
    AUTHENTICATION_CONTEXT_RESERVED = "authentication_context_reserved"
    EXPECTED_IDENTITY_BINDING_TYPE_INVALID = "expected_identity_binding_type_invalid"
    EXPECTED_IDENTITY_BINDING_SYNTAX_INVALID = (
        "expected_identity_binding_syntax_invalid"
    )
    INTENT_ACTION_TYPE_INVALID = "intent_action_type_invalid"
    INTENT_ACTION_VALUE_INVALID = "intent_action_value_invalid"
    PROVENANCE_IDENTITY_TYPE_INVALID = "provenance_identity_type_invalid"
    PROVENANCE_IDENTITY_SYNTAX_INVALID = "provenance_identity_syntax_invalid"


class Step2MarketSourcePolicyOperatorApprovalIntentAction(str, Enum):
    """The two semantically valid literal statement intents."""

    APPROVE = "APPROVE"
    REJECT = "REJECT"


class _OperatorApprovalIntentStatementInvariantError(RuntimeError):
    """Raised only for impossible internal validator states."""

    def __init__(self) -> None:
        super().__init__(_INVARIANT_ERROR_MESSAGE)


@dataclass(frozen=True, slots=True, init=False)
class Step2MarketSourcePolicyOperatorApprovalIntentStatement:
    """Validator-owned immutable snapshot retained only for a valid statement."""

    statement_schema_version: str
    authentication_context_version: str
    expected_identity_binding_sha256: str
    intent_action: Step2MarketSourcePolicyOperatorApprovalIntentAction
    provenance_identity_sha256: str

    def __new__(cls, *args: object, **kwargs: object) -> NoReturn:
        del cls, args, kwargs
        raise TypeError(_SNAPSHOT_CONSTRUCTION_ERROR)

    def __setstate__(self, state: object) -> NoReturn:
        del self, state
        raise TypeError(_SNAPSHOT_CONSTRUCTION_ERROR)

    def __reduce__(self) -> NoReturn:
        raise TypeError(_SNAPSHOT_CONSTRUCTION_ERROR)

    def __reduce_ex__(self, protocol: object) -> NoReturn:
        del protocol
        raise TypeError(_SNAPSHOT_CONSTRUCTION_ERROR)


@dataclass(frozen=True, slots=True, init=False)
class Step2MarketSourcePolicyOperatorApprovalIntentStatementValidationResult:
    """A sealed, non-authorizing decoded statement validation result."""

    result_version: str
    validator_version: str
    validation_state: Step2MarketSourcePolicyOperatorApprovalIntentStatementValidationState

    validated_statement: Step2MarketSourcePolicyOperatorApprovalIntentStatement | None
    operator_approval_intent_statement_identity_sha256: str | None

    input_type_check_performed: bool
    input_type_valid: bool | None

    root_type_check_performed: bool
    root_type_valid: bool | None

    key_set_validation_performed: bool
    key_set_valid: bool | None

    field_validation_performed: bool
    field_validation_valid: bool | None

    semantic_identity_computed: bool
    statement_contract_valid: bool | None

    intent_evaluation_performed: bool
    intent_action: Step2MarketSourcePolicyOperatorApprovalIntentAction | None
    intent_is_approval: bool | None

    diagnostics: tuple[
        Step2MarketSourcePolicyOperatorApprovalIntentStatementValidationDiagnostic,
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


def validate_step2_market_source_policy_operator_approval_intent_statement(
    value: object,
) -> Step2MarketSourcePolicyOperatorApprovalIntentStatementValidationResult:
    """Validate one decoded JSON-compatible value against the v1 contract."""

    value_type = type(value)
    if value_type not in _EXACT_JSON_DECODED_TYPES:
        return _create_result(
            validation_state=(
                Step2MarketSourcePolicyOperatorApprovalIntentStatementValidationState.INPUT_TYPE_INVALID
            ),
            diagnostic=(
                Step2MarketSourcePolicyOperatorApprovalIntentStatementValidationDiagnostic.STATEMENT_INPUT_TYPE_INVALID
            ),
        )

    if value_type is not dict:
        return _create_result(
            validation_state=(
                Step2MarketSourcePolicyOperatorApprovalIntentStatementValidationState.ROOT_TYPE_INVALID
            ),
            diagnostic=(
                Step2MarketSourcePolicyOperatorApprovalIntentStatementValidationDiagnostic.STATEMENT_ROOT_TYPE_INVALID
            ),
        )

    if len(value) != len(_REQUIRED_KEYS):
        return _create_result(
            validation_state=(
                Step2MarketSourcePolicyOperatorApprovalIntentStatementValidationState.KEY_SET_INVALID
            ),
            diagnostic=(
                Step2MarketSourcePolicyOperatorApprovalIntentStatementValidationDiagnostic.STATEMENT_KEY_SET_INVALID
            ),
        )

    keys = tuple(value)
    if any(type(key) is not str for key in keys) or frozenset(keys) != _REQUIRED_KEYS:
        return _create_result(
            validation_state=(
                Step2MarketSourcePolicyOperatorApprovalIntentStatementValidationState.KEY_SET_INVALID
            ),
            diagnostic=(
                Step2MarketSourcePolicyOperatorApprovalIntentStatementValidationDiagnostic.STATEMENT_KEY_SET_INVALID
            ),
        )

    statement_schema_version = value["statement_schema_version"]
    if type(statement_schema_version) is not str:
        return _create_result(
            validation_state=(
                Step2MarketSourcePolicyOperatorApprovalIntentStatementValidationState.FIELD_TYPE_INVALID
            ),
            diagnostic=(
                Step2MarketSourcePolicyOperatorApprovalIntentStatementValidationDiagnostic.STATEMENT_SCHEMA_VERSION_INVALID
            ),
        )
    if statement_schema_version != STATEMENT_SCHEMA_VERSION:
        return _create_result(
            validation_state=(
                Step2MarketSourcePolicyOperatorApprovalIntentStatementValidationState.FIELD_VALUE_INVALID
            ),
            diagnostic=(
                Step2MarketSourcePolicyOperatorApprovalIntentStatementValidationDiagnostic.STATEMENT_SCHEMA_VERSION_INVALID
            ),
        )

    authentication_context_version = value["authentication_context_version"]
    context_diagnostic = _authentication_context_diagnostic(authentication_context_version)
    if context_diagnostic is not None:
        return _create_result(
            validation_state=_state_for_field_diagnostic(context_diagnostic),
            diagnostic=context_diagnostic,
        )

    expected_identity_binding_sha256 = value["expected_identity_binding_sha256"]
    expected_identity_diagnostic = _digest_diagnostic(
        expected_identity_binding_sha256,
        Step2MarketSourcePolicyOperatorApprovalIntentStatementValidationDiagnostic.EXPECTED_IDENTITY_BINDING_TYPE_INVALID,
        Step2MarketSourcePolicyOperatorApprovalIntentStatementValidationDiagnostic.EXPECTED_IDENTITY_BINDING_SYNTAX_INVALID,
    )
    if expected_identity_diagnostic is not None:
        return _create_result(
            validation_state=_state_for_field_diagnostic(expected_identity_diagnostic),
            diagnostic=expected_identity_diagnostic,
        )

    raw_intent_action = value["intent_action"]
    if type(raw_intent_action) is not str:
        return _create_result(
            validation_state=(
                Step2MarketSourcePolicyOperatorApprovalIntentStatementValidationState.FIELD_TYPE_INVALID
            ),
            diagnostic=(
                Step2MarketSourcePolicyOperatorApprovalIntentStatementValidationDiagnostic.INTENT_ACTION_TYPE_INVALID
            ),
        )
    if raw_intent_action == Step2MarketSourcePolicyOperatorApprovalIntentAction.APPROVE.value:
        intent_action = Step2MarketSourcePolicyOperatorApprovalIntentAction.APPROVE
    elif raw_intent_action == Step2MarketSourcePolicyOperatorApprovalIntentAction.REJECT.value:
        intent_action = Step2MarketSourcePolicyOperatorApprovalIntentAction.REJECT
    else:
        return _create_result(
            validation_state=(
                Step2MarketSourcePolicyOperatorApprovalIntentStatementValidationState.FIELD_VALUE_INVALID
            ),
            diagnostic=(
                Step2MarketSourcePolicyOperatorApprovalIntentStatementValidationDiagnostic.INTENT_ACTION_VALUE_INVALID
            ),
        )

    provenance_identity_sha256 = value["provenance_identity_sha256"]
    provenance_identity_diagnostic = _digest_diagnostic(
        provenance_identity_sha256,
        Step2MarketSourcePolicyOperatorApprovalIntentStatementValidationDiagnostic.PROVENANCE_IDENTITY_TYPE_INVALID,
        Step2MarketSourcePolicyOperatorApprovalIntentStatementValidationDiagnostic.PROVENANCE_IDENTITY_SYNTAX_INVALID,
    )
    if provenance_identity_diagnostic is not None:
        return _create_result(
            validation_state=_state_for_field_diagnostic(provenance_identity_diagnostic),
            diagnostic=provenance_identity_diagnostic,
        )

    return _create_result(
        validation_state=(
            Step2MarketSourcePolicyOperatorApprovalIntentStatementValidationState.VALID_APPROVE
            if intent_action is Step2MarketSourcePolicyOperatorApprovalIntentAction.APPROVE
            else Step2MarketSourcePolicyOperatorApprovalIntentStatementValidationState.VALID_REJECT
        ),
        authentication_context_version=authentication_context_version,
        expected_identity_binding_sha256=expected_identity_binding_sha256,
        intent_action=intent_action,
        provenance_identity_sha256=provenance_identity_sha256,
    )


def _authentication_context_diagnostic(
    value: object,
) -> Step2MarketSourcePolicyOperatorApprovalIntentStatementValidationDiagnostic | None:
    if type(value) is not str:
        return (
            Step2MarketSourcePolicyOperatorApprovalIntentStatementValidationDiagnostic.AUTHENTICATION_CONTEXT_TYPE_INVALID
        )
    if not 1 <= len(value) <= 64 or not value.isascii():
        return (
            Step2MarketSourcePolicyOperatorApprovalIntentStatementValidationDiagnostic.AUTHENTICATION_CONTEXT_SYNTAX_INVALID
        )
    if value in _RESERVED_AUTHENTICATION_CONTEXT_VERSIONS:
        return (
            Step2MarketSourcePolicyOperatorApprovalIntentStatementValidationDiagnostic.AUTHENTICATION_CONTEXT_RESERVED
        )
    if _AUTHENTICATION_CONTEXT_VERSION_PATTERN.fullmatch(value) is None:
        return (
            Step2MarketSourcePolicyOperatorApprovalIntentStatementValidationDiagnostic.AUTHENTICATION_CONTEXT_SYNTAX_INVALID
        )
    return None


def _digest_diagnostic(
    value: object,
    type_diagnostic: Step2MarketSourcePolicyOperatorApprovalIntentStatementValidationDiagnostic,
    syntax_diagnostic: Step2MarketSourcePolicyOperatorApprovalIntentStatementValidationDiagnostic,
) -> Step2MarketSourcePolicyOperatorApprovalIntentStatementValidationDiagnostic | None:
    if type(value) is not str:
        return type_diagnostic
    if not _is_lowercase_sha256(value):
        return syntax_diagnostic
    return None


def _is_lowercase_sha256(value: object) -> bool:
    if type(value) is not str or len(value) != 64:
        return False
    return all("0" <= character <= "9" or "a" <= character <= "f" for character in value)


def _state_for_field_diagnostic(
    diagnostic: Step2MarketSourcePolicyOperatorApprovalIntentStatementValidationDiagnostic,
) -> Step2MarketSourcePolicyOperatorApprovalIntentStatementValidationState:
    if diagnostic in {
        Step2MarketSourcePolicyOperatorApprovalIntentStatementValidationDiagnostic.AUTHENTICATION_CONTEXT_TYPE_INVALID,
        Step2MarketSourcePolicyOperatorApprovalIntentStatementValidationDiagnostic.EXPECTED_IDENTITY_BINDING_TYPE_INVALID,
        Step2MarketSourcePolicyOperatorApprovalIntentStatementValidationDiagnostic.INTENT_ACTION_TYPE_INVALID,
        Step2MarketSourcePolicyOperatorApprovalIntentStatementValidationDiagnostic.PROVENANCE_IDENTITY_TYPE_INVALID,
    }:
        return (
            Step2MarketSourcePolicyOperatorApprovalIntentStatementValidationState.FIELD_TYPE_INVALID
        )
    return Step2MarketSourcePolicyOperatorApprovalIntentStatementValidationState.FIELD_VALUE_INVALID


def _create_result(
    *,
    validation_state: Step2MarketSourcePolicyOperatorApprovalIntentStatementValidationState,
    diagnostic: Step2MarketSourcePolicyOperatorApprovalIntentStatementValidationDiagnostic | None = None,
    authentication_context_version: str | None = None,
    expected_identity_binding_sha256: str | None = None,
    intent_action: Step2MarketSourcePolicyOperatorApprovalIntentAction | None = None,
    provenance_identity_sha256: str | None = None,
) -> Step2MarketSourcePolicyOperatorApprovalIntentStatementValidationResult:
    """Create every result after enforcing the complete sealed branch matrix."""

    if type(validation_state) is not Step2MarketSourcePolicyOperatorApprovalIntentStatementValidationState:
        raise _OperatorApprovalIntentStatementInvariantError()

    is_valid = validation_state in {
        Step2MarketSourcePolicyOperatorApprovalIntentStatementValidationState.VALID_APPROVE,
        Step2MarketSourcePolicyOperatorApprovalIntentStatementValidationState.VALID_REJECT,
    }
    if is_valid:
        if diagnostic is not None:
            raise _OperatorApprovalIntentStatementInvariantError()
        if not _valid_statement_components(
            authentication_context_version,
            expected_identity_binding_sha256,
            intent_action,
            provenance_identity_sha256,
            validation_state,
        ):
            raise _OperatorApprovalIntentStatementInvariantError()
        validated_statement = object.__new__(
            Step2MarketSourcePolicyOperatorApprovalIntentStatement
        )
        object.__setattr__(validated_statement, "statement_schema_version", STATEMENT_SCHEMA_VERSION)
        object.__setattr__(
            validated_statement,
            "authentication_context_version",
            authentication_context_version,
        )
        object.__setattr__(
            validated_statement,
            "expected_identity_binding_sha256",
            expected_identity_binding_sha256,
        )
        object.__setattr__(validated_statement, "intent_action", intent_action)
        object.__setattr__(
            validated_statement,
            "provenance_identity_sha256",
            provenance_identity_sha256,
        )
        if not _valid_snapshot(validated_statement):
            raise _OperatorApprovalIntentStatementInvariantError()
        canonical_statement_utf8 = _canonical_statement_utf8(validated_statement)
        if len(canonical_statement_utf8) > MAX_OPERATOR_APPROVAL_INTENT_STATEMENT_CANONICAL_BYTES:
            raise _OperatorApprovalIntentStatementInvariantError()
        operator_approval_intent_statement_identity_sha256 = hashlib.sha256(
            OPERATOR_APPROVAL_INTENT_STATEMENT_IDENTITY_DOMAIN + canonical_statement_utf8
        ).hexdigest()
        if not _is_lowercase_sha256(
            operator_approval_intent_statement_identity_sha256
        ):
            raise _OperatorApprovalIntentStatementInvariantError()
    else:
        if (
            authentication_context_version is not None
            or expected_identity_binding_sha256 is not None
            or intent_action is not None
            or provenance_identity_sha256 is not None
        ):
            raise _OperatorApprovalIntentStatementInvariantError()
        _validate_invalid_state_diagnostic(validation_state, diagnostic)
        validated_statement = None
        operator_approval_intent_statement_identity_sha256 = None

    result_values = _result_values(
        validation_state,
        diagnostic,
        validated_statement,
        operator_approval_intent_statement_identity_sha256,
    )
    _validate_result_values(result_values)
    result = object.__new__(
        Step2MarketSourcePolicyOperatorApprovalIntentStatementValidationResult
    )
    for result_field in fields(
        Step2MarketSourcePolicyOperatorApprovalIntentStatementValidationResult
    ):
        object.__setattr__(result, result_field.name, result_values[result_field.name])
    return result


def _valid_statement_components(
    authentication_context_version: object,
    expected_identity_binding_sha256: object,
    intent_action: object,
    provenance_identity_sha256: object,
    validation_state: object,
) -> bool:
    if _authentication_context_diagnostic(authentication_context_version) is not None:
        return False
    if not _is_lowercase_sha256(expected_identity_binding_sha256):
        return False
    if not _is_lowercase_sha256(provenance_identity_sha256):
        return False
    if type(intent_action) is not Step2MarketSourcePolicyOperatorApprovalIntentAction:
        return False
    return (
        validation_state
        is Step2MarketSourcePolicyOperatorApprovalIntentStatementValidationState.VALID_APPROVE
        and intent_action is Step2MarketSourcePolicyOperatorApprovalIntentAction.APPROVE
    ) or (
        validation_state
        is Step2MarketSourcePolicyOperatorApprovalIntentStatementValidationState.VALID_REJECT
        and intent_action is Step2MarketSourcePolicyOperatorApprovalIntentAction.REJECT
    )


def _valid_snapshot(value: object) -> bool:
    if type(value) is not Step2MarketSourcePolicyOperatorApprovalIntentStatement:
        return False
    if value.statement_schema_version != STATEMENT_SCHEMA_VERSION:
        return False
    return _valid_statement_components(
        value.authentication_context_version,
        value.expected_identity_binding_sha256,
        value.intent_action,
        value.provenance_identity_sha256,
        (
            Step2MarketSourcePolicyOperatorApprovalIntentStatementValidationState.VALID_APPROVE
            if value.intent_action
            is Step2MarketSourcePolicyOperatorApprovalIntentAction.APPROVE
            else Step2MarketSourcePolicyOperatorApprovalIntentStatementValidationState.VALID_REJECT
        ),
    )


def _canonical_statement_utf8(
    statement: Step2MarketSourcePolicyOperatorApprovalIntentStatement,
) -> bytes:
    record = {
        "authentication_context_version": statement.authentication_context_version,
        "expected_identity_binding_sha256": statement.expected_identity_binding_sha256,
        "intent_action": statement.intent_action.value,
        "provenance_identity_sha256": statement.provenance_identity_sha256,
        "statement_schema_version": STATEMENT_SCHEMA_VERSION,
    }
    serialized = json.dumps(
        record,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    if type(serialized) is not str:
        raise _OperatorApprovalIntentStatementInvariantError()
    return serialized.encode("utf-8")


def _validate_invalid_state_diagnostic(
    validation_state: Step2MarketSourcePolicyOperatorApprovalIntentStatementValidationState,
    diagnostic: Step2MarketSourcePolicyOperatorApprovalIntentStatementValidationDiagnostic | None,
) -> None:
    valid_diagnostics = {
        Step2MarketSourcePolicyOperatorApprovalIntentStatementValidationState.INPUT_TYPE_INVALID: {
            Step2MarketSourcePolicyOperatorApprovalIntentStatementValidationDiagnostic.STATEMENT_INPUT_TYPE_INVALID
        },
        Step2MarketSourcePolicyOperatorApprovalIntentStatementValidationState.ROOT_TYPE_INVALID: {
            Step2MarketSourcePolicyOperatorApprovalIntentStatementValidationDiagnostic.STATEMENT_ROOT_TYPE_INVALID
        },
        Step2MarketSourcePolicyOperatorApprovalIntentStatementValidationState.KEY_SET_INVALID: {
            Step2MarketSourcePolicyOperatorApprovalIntentStatementValidationDiagnostic.STATEMENT_KEY_SET_INVALID
        },
        Step2MarketSourcePolicyOperatorApprovalIntentStatementValidationState.FIELD_TYPE_INVALID: {
            Step2MarketSourcePolicyOperatorApprovalIntentStatementValidationDiagnostic.STATEMENT_SCHEMA_VERSION_INVALID,
            Step2MarketSourcePolicyOperatorApprovalIntentStatementValidationDiagnostic.AUTHENTICATION_CONTEXT_TYPE_INVALID,
            Step2MarketSourcePolicyOperatorApprovalIntentStatementValidationDiagnostic.EXPECTED_IDENTITY_BINDING_TYPE_INVALID,
            Step2MarketSourcePolicyOperatorApprovalIntentStatementValidationDiagnostic.INTENT_ACTION_TYPE_INVALID,
            Step2MarketSourcePolicyOperatorApprovalIntentStatementValidationDiagnostic.PROVENANCE_IDENTITY_TYPE_INVALID,
        },
        Step2MarketSourcePolicyOperatorApprovalIntentStatementValidationState.FIELD_VALUE_INVALID: {
            Step2MarketSourcePolicyOperatorApprovalIntentStatementValidationDiagnostic.STATEMENT_SCHEMA_VERSION_INVALID,
            Step2MarketSourcePolicyOperatorApprovalIntentStatementValidationDiagnostic.AUTHENTICATION_CONTEXT_SYNTAX_INVALID,
            Step2MarketSourcePolicyOperatorApprovalIntentStatementValidationDiagnostic.AUTHENTICATION_CONTEXT_RESERVED,
            Step2MarketSourcePolicyOperatorApprovalIntentStatementValidationDiagnostic.EXPECTED_IDENTITY_BINDING_SYNTAX_INVALID,
            Step2MarketSourcePolicyOperatorApprovalIntentStatementValidationDiagnostic.INTENT_ACTION_VALUE_INVALID,
            Step2MarketSourcePolicyOperatorApprovalIntentStatementValidationDiagnostic.PROVENANCE_IDENTITY_SYNTAX_INVALID,
        },
    }
    if diagnostic not in valid_diagnostics.get(validation_state, frozenset()):
        raise _OperatorApprovalIntentStatementInvariantError()


def _result_values(
    validation_state: Step2MarketSourcePolicyOperatorApprovalIntentStatementValidationState,
    diagnostic: Step2MarketSourcePolicyOperatorApprovalIntentStatementValidationDiagnostic | None,
    validated_statement: Step2MarketSourcePolicyOperatorApprovalIntentStatement | None,
    identity: str | None,
) -> dict[str, object]:
    state = validation_state
    is_valid = state in {
        Step2MarketSourcePolicyOperatorApprovalIntentStatementValidationState.VALID_APPROVE,
        Step2MarketSourcePolicyOperatorApprovalIntentStatementValidationState.VALID_REJECT,
    }
    is_input_type_invalid = (
        state
        is Step2MarketSourcePolicyOperatorApprovalIntentStatementValidationState.INPUT_TYPE_INVALID
    )
    is_root_type_invalid = (
        state
        is Step2MarketSourcePolicyOperatorApprovalIntentStatementValidationState.ROOT_TYPE_INVALID
    )
    key_set_performed = not is_input_type_invalid and not is_root_type_invalid
    key_set_valid = None if not key_set_performed else state is not (
        Step2MarketSourcePolicyOperatorApprovalIntentStatementValidationState.KEY_SET_INVALID
    )
    field_performed = key_set_valid is True
    field_valid = None if not field_performed else is_valid
    input_type_valid = not is_input_type_invalid
    root_type_performed = input_type_valid
    root_type_valid = None if not root_type_performed else not is_root_type_invalid
    statement_contract_valid: bool | None
    if is_input_type_invalid or is_root_type_invalid:
        statement_contract_valid = None
    else:
        statement_contract_valid = is_valid
    valid_action = None if validated_statement is None else validated_statement.intent_action
    intent_is_approval = (
        None
        if valid_action is None
        else valid_action is Step2MarketSourcePolicyOperatorApprovalIntentAction.APPROVE
    )
    return {
        "result_version": VALIDATION_RESULT_VERSION,
        "validator_version": VALIDATOR_VERSION,
        "validation_state": state,
        "validated_statement": validated_statement,
        "operator_approval_intent_statement_identity_sha256": identity,
        "input_type_check_performed": True,
        "input_type_valid": input_type_valid,
        "root_type_check_performed": root_type_performed,
        "root_type_valid": root_type_valid,
        "key_set_validation_performed": key_set_performed,
        "key_set_valid": key_set_valid,
        "field_validation_performed": field_performed,
        "field_validation_valid": field_valid,
        "semantic_identity_computed": identity is not None,
        "statement_contract_valid": statement_contract_valid,
        "intent_evaluation_performed": valid_action is not None,
        "intent_action": valid_action,
        "intent_is_approval": intent_is_approval,
        "diagnostics": () if is_valid else (diagnostic,),
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


def _validate_result_values(values: dict[str, object]) -> None:
    expected_field_names = tuple(
        result_field.name
        for result_field in fields(
            Step2MarketSourcePolicyOperatorApprovalIntentStatementValidationResult
        )
    )
    if tuple(values) != expected_field_names:
        raise _OperatorApprovalIntentStatementInvariantError()

    state = values["validation_state"]
    diagnostics = values["diagnostics"]
    statement = values["validated_statement"]
    identity = values["operator_approval_intent_statement_identity_sha256"]
    is_valid = state in {
        Step2MarketSourcePolicyOperatorApprovalIntentStatementValidationState.VALID_APPROVE,
        Step2MarketSourcePolicyOperatorApprovalIntentStatementValidationState.VALID_REJECT,
    }
    if type(state) is not Step2MarketSourcePolicyOperatorApprovalIntentStatementValidationState:
        raise _OperatorApprovalIntentStatementInvariantError()
    if type(values["input_type_check_performed"]) is not bool or not values[
        "input_type_check_performed"
    ]:
        raise _OperatorApprovalIntentStatementInvariantError()
    if type(values["input_type_valid"]) is not bool:
        raise _OperatorApprovalIntentStatementInvariantError()
    if values["root_type_check_performed"] is not values["input_type_valid"]:
        raise _OperatorApprovalIntentStatementInvariantError()
    if values["root_type_check_performed"]:
        if type(values["root_type_valid"]) is not bool:
            raise _OperatorApprovalIntentStatementInvariantError()
    elif values["root_type_valid"] is not None:
        raise _OperatorApprovalIntentStatementInvariantError()
    if values["key_set_validation_performed"] is not (values["root_type_valid"] is True):
        raise _OperatorApprovalIntentStatementInvariantError()
    if values["key_set_validation_performed"]:
        if type(values["key_set_valid"]) is not bool:
            raise _OperatorApprovalIntentStatementInvariantError()
    elif values["key_set_valid"] is not None:
        raise _OperatorApprovalIntentStatementInvariantError()
    if values["field_validation_performed"] is not (values["key_set_valid"] is True):
        raise _OperatorApprovalIntentStatementInvariantError()
    if values["field_validation_performed"]:
        if type(values["field_validation_valid"]) is not bool:
            raise _OperatorApprovalIntentStatementInvariantError()
    elif values["field_validation_valid"] is not None:
        raise _OperatorApprovalIntentStatementInvariantError()
    if (statement is not None) is not is_valid or (identity is not None) is not is_valid:
        raise _OperatorApprovalIntentStatementInvariantError()
    if values["semantic_identity_computed"] is not (identity is not None):
        raise _OperatorApprovalIntentStatementInvariantError()
    if is_valid:
        if not _valid_snapshot(statement) or not _is_lowercase_sha256(identity):
            raise _OperatorApprovalIntentStatementInvariantError()
        if diagnostics != () or values["field_validation_valid"] is not True:
            raise _OperatorApprovalIntentStatementInvariantError()
        if values["statement_contract_valid"] is not True:
            raise _OperatorApprovalIntentStatementInvariantError()
        if values["intent_evaluation_performed"] is not True:
            raise _OperatorApprovalIntentStatementInvariantError()
        if values["intent_action"] is not statement.intent_action:
            raise _OperatorApprovalIntentStatementInvariantError()
        if values["intent_is_approval"] is not (
            state
            is Step2MarketSourcePolicyOperatorApprovalIntentStatementValidationState.VALID_APPROVE
        ):
            raise _OperatorApprovalIntentStatementInvariantError()
    else:
        if type(diagnostics) is not tuple or len(diagnostics) != 1:
            raise _OperatorApprovalIntentStatementInvariantError()
        _validate_invalid_state_diagnostic(state, diagnostics[0])
        if values["intent_evaluation_performed"] is not False:
            raise _OperatorApprovalIntentStatementInvariantError()
        if values["intent_action"] is not None or values["intent_is_approval"] is not None:
            raise _OperatorApprovalIntentStatementInvariantError()
        expected_contract_valid = None if state in {
            Step2MarketSourcePolicyOperatorApprovalIntentStatementValidationState.INPUT_TYPE_INVALID,
            Step2MarketSourcePolicyOperatorApprovalIntentStatementValidationState.ROOT_TYPE_INVALID,
        } else False
        if values["statement_contract_valid"] is not expected_contract_valid:
            raise _OperatorApprovalIntentStatementInvariantError()
    if (
        values["result_version"] != VALIDATION_RESULT_VERSION
        or values["validator_version"] != VALIDATOR_VERSION
        or values["authority_scope"] != AUTHORITY_SCOPE
        or values["trade_permission_effect"] != TRADE_PERMISSION_EFFECT
    ):
        raise _OperatorApprovalIntentStatementInvariantError()
    for marker in (
        "not_authentication",
        "not_approval_authorization",
        "not_activation_authorization",
        "not_trade_authorization",
    ):
        if values[marker] is not True:
            raise _OperatorApprovalIntentStatementInvariantError()
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
        if values[marker] is not False:
            raise _OperatorApprovalIntentStatementInvariantError()


__all__ = (
    "AUTHORITY_SCOPE",
    "MAX_OPERATOR_APPROVAL_INTENT_STATEMENT_CANONICAL_BYTES",
    "MAX_OPERATOR_APPROVAL_INTENT_STATEMENT_VALIDATION_DIAGNOSTICS",
    "OPERATOR_APPROVAL_INTENT_STATEMENT_IDENTITY_DOMAIN",
    "STATEMENT_SCHEMA_VERSION",
    "Step2MarketSourcePolicyOperatorApprovalIntentAction",
    "Step2MarketSourcePolicyOperatorApprovalIntentStatement",
    "Step2MarketSourcePolicyOperatorApprovalIntentStatementValidationDiagnostic",
    "Step2MarketSourcePolicyOperatorApprovalIntentStatementValidationResult",
    "Step2MarketSourcePolicyOperatorApprovalIntentStatementValidationState",
    "TRADE_PERMISSION_EFFECT",
    "VALIDATION_RESULT_VERSION",
    "VALIDATOR_VERSION",
    "validate_step2_market_source_policy_operator_approval_intent_statement",
)
