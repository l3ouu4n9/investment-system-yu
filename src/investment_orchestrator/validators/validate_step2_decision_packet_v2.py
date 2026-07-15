"""Pure structural validation for non-authorizing Step 2 packet v2 values.

The packet is an LLM proposal.  This module proves only its closed shape,
exact JSON-native types, bounded canonical identity, and composition with the
public Step 2 market-observations v2 structural contract.  It deliberately
performs no semantic or freshness evaluation, source verification, universe
resolution, candidate-validity decision, permission lookup, publication
decision, workflow transition, order compilation, or execution action.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum
import hashlib
import json
import math
import re
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker, validators
from jsonschema.exceptions import _WrappedReferencingError
from referencing import Registry, Resource
from referencing.exceptions import NoSuchResource, Unresolvable, Unretrievable

from investment_orchestrator.validators.validate_step2_market_observations import (
    MARKET_OBSERVATIONS_SCHEMA_FILENAME,
    validate_step2_market_observations,
)


DECISION_PACKET_SCHEMA_VERSION = "step2_decision_packet_v2"
DECISION_PACKET_VALIDATION_RESULT_VERSION = (
    "step2_decision_packet_validation_result_v1"
)
DECISION_PACKET_SCHEMA_FILENAME = "step2_decision_packet.schema.json"

MAX_CANONICAL_BYTES = 1048576
MAX_JSON_NESTING_DEPTH = 32
MAX_JSON_NODE_COUNT = 4096
MAX_SAFE_INTEGER = 9007199254740991
MAX_TICKER_ARRAY = 128
MAX_REFERENCE_COUNT = 64
MAX_IDENTIFIER_LENGTH = 128
MAX_RATIONALE_LENGTH = 4096
MAX_EXECUTION_STEPS = 16

IDENTITY_ONLY = True
NOT_AUTHORIZATION = True
PERMISSION_EFFECT_NONE = "none"
SEMANTIC_VALIDATION_PERFORMED = False
FRESHNESS_EVALUATION_PERFORMED = False
UNIVERSE_RESOLUTION_PERFORMED = False
CANDIDATE_VALIDITY_EVALUATED = False

DECISION_PACKET_MODES = ("DECISION_DRAFT", "NO_TRADE")
NO_TRADE_REASON_CODES = (
    "NO_ELIGIBLE_CHANGE",
    "MISSING_REQUIRED_DATA",
    "STALE_MARKET_DATA",
    "FUTURE_DATED_DATA",
    "SOURCE_CONFLICT",
    "POLICY_BLOCKED",
    "MANUAL_REVIEW_REQUIRED",
)
NO_TRADE_REASONS_REQUIRING_MARKET_OBSERVATIONS = frozenset(
    {
        "NO_ELIGIBLE_CHANGE",
        "STALE_MARKET_DATA",
        "FUTURE_DATED_DATA",
        "SOURCE_CONFLICT",
    }
)

VALIDATION_BOOLEAN_COERCION_ERROR = (
    "inspect structure_valid and schema_valid explicitly; "
    "decision-packet validation results have no truth value"
)

_DRAFT_2020_12_URI = "https://json-schema.org/draft/2020-12/schema"
_SCHEMA_BASE_URI = "https://investment-system.local/schemas"
_MARKET_OBSERVATIONS_SCHEMA_URI = (
    f"{_SCHEMA_BASE_URI}/{MARKET_OBSERVATIONS_SCHEMA_FILENAME}"
)
_DECISION_PACKET_SCHEMA_URI = (
    f"{_SCHEMA_BASE_URI}/{DECISION_PACKET_SCHEMA_FILENAME}"
)
_MARKET_OBSERVATIONS_FORMAT = "step2-market-observations-v2-contract"
_STRICT_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

_TOP_LEVEL_REQUIRED_FIELDS = (
    "schema_version",
    "mode",
    "market_observations",
    "proposed_buy_universe",
    "active_shortlist",
    "exposure_overlap_diagnostics",
    "buy_side_delta_table",
    "rotation_decision_layer_8_15",
    "sell_side_delta_table_8_2",
    "execution_plan_drafts_8_5",
    "sell_execution_plan_drafts_8_6",
    "cold_regime_review_proposal",
    "post_cancel_redeployment_proposal",
    "reported_assumptions_and_data_gaps",
)
_DECISION_BEARING_ARRAYS = (
    "buy_side_delta_table",
    "rotation_decision_layer_8_15",
    "sell_side_delta_table_8_2",
    "execution_plan_drafts_8_5",
    "sell_execution_plan_drafts_8_6",
)
_NO_TRADE_EMPTY_ARRAYS = (
    "proposed_buy_universe",
    "active_shortlist",
    "exposure_overlap_diagnostics",
    *_DECISION_BEARING_ARRAYS,
)


def _ref(name: str) -> dict[str, str]:
    return {"$ref": f"#/$defs/{name}"}


def _array_of_ref(name: str, maximum: int) -> dict[str, Any]:
    return {
        "type": "array",
        "minItems": 0,
        "maxItems": maximum,
        "items": _ref(name),
    }


def _closed_object(
    properties: dict[str, Any],
    *,
    required: tuple[str, ...] | list[str] | None = None,
    **extra: Any,
) -> dict[str, Any]:
    schema: dict[str, Any] = {
        "type": "object",
        "required": list(required if required is not None else properties),
        "properties": properties,
        "additionalProperties": False,
    }
    schema.update(extra)
    return schema


def _tif_branches() -> list[dict[str, Any]]:
    return [
        {
            "properties": {
                "proposed_time_in_force": {"const": "DAY"},
                "proposed_expiry_date": {"type": "null"},
            }
        },
        {
            "properties": {
                "proposed_time_in_force": {"const": "GTD"},
                "proposed_expiry_date": _ref("strict_date"),
            }
        },
    ]


def _build_step2_decision_packet_schema() -> dict[str, Any]:
    """Build the in-memory schema mirrored by the checked-in JSON document."""
    nullable_nonnegative_safe_integer = {
        "anyOf": [
            {
                "type": "integer",
                "minimum": 0,
                "maximum": MAX_SAFE_INTEGER,
            },
            {"type": "null"},
        ]
    }
    market_or_null = {
        "anyOf": [
            {"$ref": _MARKET_OBSERVATIONS_SCHEMA_URI},
            {"type": "null"},
        ]
    }
    review_or_null = lambda name: {
        "anyOf": [_ref(name), {"type": "null"}]
    }

    defs: dict[str, Any] = {
        "ticker": {
            "type": "string",
            "pattern": r"^[A-Z][A-Z0-9.-]{0,9}$",
        },
        "bounded_identifier": {
            "type": "string",
            "minLength": 1,
            "maxLength": MAX_IDENTIFIER_LENGTH,
        },
        "bounded_rationale": {
            "type": "string",
            "minLength": 1,
            "maxLength": MAX_RATIONALE_LENGTH,
        },
        "reference_array": {
            "type": "array",
            "minItems": 0,
            "maxItems": MAX_REFERENCE_COUNT,
            "uniqueItems": True,
            "items": _ref("bounded_identifier"),
        },
        "risk_note_array": {
            "type": "array",
            "minItems": 0,
            "maxItems": MAX_REFERENCE_COUNT,
            "items": _ref("bounded_rationale"),
        },
        "ticker_array_64": {
            "type": "array",
            "minItems": 0,
            "maxItems": MAX_REFERENCE_COUNT,
            "uniqueItems": True,
            "items": _ref("ticker"),
        },
        "ticker_array_128": {
            "type": "array",
            "minItems": 0,
            "maxItems": MAX_TICKER_ARRAY,
            "uniqueItems": True,
            "items": _ref("ticker"),
        },
        "nullable_nonnegative_safe_integer": nullable_nonnegative_safe_integer,
        "strict_date": {
            "type": "string",
            "pattern": r"^\d{4}-\d{2}-\d{2}$",
            "format": "date",
        },
    }

    defs["no_trade_reason"] = _closed_object(
        {
            "reason_code": {"enum": list(NO_TRADE_REASON_CODES)},
            "reason_detail": _ref("bounded_rationale"),
            "reference_ids": _ref("reference_array"),
        }
    )
    defs["active_shortlist_row"] = _closed_object(
        {
            "ticker": _ref("ticker"),
            "rank": {"type": "integer", "minimum": 1, "maximum": 128},
            "role_claim": _ref("bounded_identifier"),
            "proposal_status": {"enum": ["SELECTED", "WATCH_ONLY"]},
            "rationale": _ref("bounded_rationale"),
            "reference_ids": _ref("reference_array"),
            "reported_risk_notes": _ref("risk_note_array"),
        }
    )
    defs["exposure_overlap_row"] = _closed_object(
        {
            "ticker": _ref("ticker"),
            "overlaps_with": _ref("ticker_array_64"),
            "overlap_assessment": {
                "enum": ["LOW", "MODERATE", "HIGH", "UNKNOWN"]
            },
            "rationale": _ref("bounded_rationale"),
            "reference_ids": _ref("reference_array"),
        }
    )
    defs["buy_side_delta_row"] = _closed_object(
        {
            "ticker": _ref("ticker"),
            "proposed_action": {
                "enum": [
                    "KEEP_EXISTING",
                    "HOLD_NO_NEW_BUDGET",
                    "WATCHLIST_NO_TRADE",
                    "NEW_ORDER",
                    "REPLACE_EXISTING",
                    "CANCEL_EXISTING",
                ]
            },
            "proposed_budget_cents": _ref(
                "nullable_nonnegative_safe_integer"
            ),
            "rationale": _ref("bounded_rationale"),
            "reference_ids": _ref("reference_array"),
        }
    )
    defs["rotation_row"] = _closed_object(
        {
            "from_ticker": _ref("ticker"),
            "to_ticker": _ref("ticker"),
            "proposal_type": {"const": "SAME_ROLE_ROTATION"},
            "proposed_budget_cents": _ref(
                "nullable_nonnegative_safe_integer"
            ),
            "rationale": _ref("bounded_rationale"),
            "reference_ids": _ref("reference_array"),
        }
    )
    defs["sell_side_delta_row"] = _closed_object(
        {
            "ticker": _ref("ticker"),
            "proposed_action": {"enum": ["HOLD_NO_SELL", "SELL"]},
            "proposed_share_quantity": _ref(
                "nullable_nonnegative_safe_integer"
            ),
            "replacement_ticker": {
                "anyOf": [_ref("ticker"), {"type": "null"}]
            },
            "rationale": _ref("bounded_rationale"),
            "reference_ids": _ref("reference_array"),
        }
    )
    defs["execution_step"] = _closed_object(
        {
            "step_label": _ref("bounded_identifier"),
            "proposed_offset_bps": {
                "type": "integer",
                "minimum": -MAX_SAFE_INTEGER,
                "maximum": MAX_SAFE_INTEGER,
            },
            "proposed_weight_bps": {
                "type": "integer",
                "minimum": 0,
                "maximum": 10000,
            },
        }
    )
    defs["execution_plan_row"] = _closed_object(
        {
            "ticker": _ref("ticker"),
            "proposal_action": {
                "enum": [
                    "KEEP_EXISTING",
                    "NEW_ORDER",
                    "REPLACE_EXISTING",
                    "CANCEL_EXISTING",
                ]
            },
            "plan_kind": {
                "enum": [
                    "KEEP_EXISTING_LADDER",
                    "NEW_LIMIT_LADDER",
                    "REPLACE_EXISTING_LADDER",
                    "CANCEL_EXISTING_ORDER",
                ]
            },
            "proposed_time_in_force": {"enum": ["DAY", "GTD"]},
            "proposed_expiry_date": {
                "anyOf": [_ref("strict_date"), {"type": "null"}]
            },
            "proposed_steps": _array_of_ref(
                "execution_step", MAX_EXECUTION_STEPS
            ),
            "rationale": _ref("bounded_rationale"),
            "reference_ids": _ref("reference_array"),
        },
        oneOf=_tif_branches(),
    )
    defs["sell_execution_plan_row"] = _closed_object(
        {
            "ticker": _ref("ticker"),
            "proposal_action": {"const": "SELL"},
            "plan_kind": {"const": "SINGLE_LIMIT_SELL_PROPOSAL"},
            "proposed_share_quantity": _ref(
                "nullable_nonnegative_safe_integer"
            ),
            "proposed_limit_rule": _ref("bounded_rationale"),
            "proposed_lot_policy": {"const": "LTCG_ELIGIBLE_ONLY"},
            "proposed_time_in_force": {"enum": ["DAY", "GTD"]},
            "proposed_expiry_date": {
                "anyOf": [_ref("strict_date"), {"type": "null"}]
            },
            "rationale": _ref("bounded_rationale"),
            "reference_ids": _ref("reference_array"),
        },
        oneOf=_tif_branches(),
    )
    defs["cold_regime_review"] = _closed_object(
        {
            "reported_triggered": {"type": "boolean"},
            "candidate_tickers": _ref("ticker_array_128"),
            "conclusion_claim": {
                "enum": [
                    "NOT_TRIGGERED",
                    "PRESERVE_HEADROOM",
                    "PROPOSE_DEPLOYMENT",
                    "INSUFFICIENT_EVIDENCE",
                ]
            },
            "rationale": _ref("bounded_rationale"),
            "reference_ids": _ref("reference_array"),
        }
    )
    defs["post_cancel_redeployment"] = _closed_object(
        {
            "source_tickers": _ref("ticker_array_128"),
            "destination_tickers": _ref("ticker_array_128"),
            "proposal": {
                "enum": [
                    "NO_REDEPLOYMENT",
                    "REDEPLOY",
                    "PRESERVE_HEADROOM",
                ]
            },
            "proposed_budget_cents": _ref(
                "nullable_nonnegative_safe_integer"
            ),
            "rationale": _ref("bounded_rationale"),
            "reference_ids": _ref("reference_array"),
        }
    )
    defs["reported_assumption_or_gap_row"] = _closed_object(
        {
            "category": {
                "enum": [
                    "ASSUMPTION",
                    "DATA_GAP_CLAIM",
                    "SOURCE_CLAIM",
                    "POLICY_CONCERN",
                    "MANUAL_REVIEW_CLAIM",
                ]
            },
            "code": _ref("bounded_identifier"),
            "detail": _ref("bounded_rationale"),
            "related_tickers": _ref("ticker_array_128"),
            "reference_ids": _ref("reference_array"),
        }
    )

    decision_draft_options = [
        {
            "required": [field_name],
            "properties": {field_name: {"minItems": 1}},
        }
        for field_name in _DECISION_BEARING_ARRAYS
    ]
    defs["decision_draft_branch"] = {
        "properties": {
            "mode": {"const": "DECISION_DRAFT"},
            "market_observations": {
                "$ref": _MARKET_OBSERVATIONS_SCHEMA_URI
            },
        },
        "not": {"required": ["no_trade_reason"]},
        "anyOf": decision_draft_options,
    }
    no_trade_properties: dict[str, Any] = {
        "mode": {"const": "NO_TRADE"},
        **{field_name: {"maxItems": 0} for field_name in _NO_TRADE_EMPTY_ARRAYS},
        "cold_regime_review_proposal": {"type": "null"},
        "post_cancel_redeployment_proposal": {"type": "null"},
        "reported_assumptions_and_data_gaps": {"minItems": 1},
    }
    defs["no_trade_branch"] = {
        "required": ["no_trade_reason"],
        "properties": no_trade_properties,
        "allOf": [
            {
                "if": {
                    "properties": {
                        "no_trade_reason": {
                            "properties": {
                                "reason_code": {
                                    "enum": sorted(
                                        NO_TRADE_REASONS_REQUIRING_MARKET_OBSERVATIONS,
                                        key=NO_TRADE_REASON_CODES.index,
                                    )
                                }
                            },
                            "required": ["reason_code"],
                        }
                    },
                    "required": ["no_trade_reason"],
                },
                "then": {
                    "properties": {
                        "market_observations": {
                            "$ref": _MARKET_OBSERVATIONS_SCHEMA_URI
                        }
                    }
                },
            }
        ],
    }

    return {
        "$schema": _DRAFT_2020_12_URI,
        "$id": _DECISION_PACKET_SCHEMA_URI,
        "title": "Step 2 Decision Packet v2",
        "description": (
            "Closed structural contract for a non-authorizing Step 2 decision "
            "proposal. Structural validity does not establish semantic "
            "consistency, freshness, source validity, market usability, "
            "universe membership, candidate validity, readiness, permission, "
            "publication eligibility, order eligibility, final safety, or "
            "execution authority."
        ),
        "type": "object",
        "required": list(_TOP_LEVEL_REQUIRED_FIELDS),
        "properties": {
            "schema_version": {"const": DECISION_PACKET_SCHEMA_VERSION},
            "mode": {"enum": list(DECISION_PACKET_MODES)},
            "no_trade_reason": _ref("no_trade_reason"),
            "market_observations": market_or_null,
            "proposed_buy_universe": _ref("ticker_array_128"),
            "active_shortlist": _array_of_ref("active_shortlist_row", 32),
            "exposure_overlap_diagnostics": _array_of_ref(
                "exposure_overlap_row", 128
            ),
            "buy_side_delta_table": _array_of_ref("buy_side_delta_row", 128),
            "rotation_decision_layer_8_15": _array_of_ref("rotation_row", 128),
            "sell_side_delta_table_8_2": _array_of_ref(
                "sell_side_delta_row", 128
            ),
            "execution_plan_drafts_8_5": _array_of_ref(
                "execution_plan_row", 128
            ),
            "sell_execution_plan_drafts_8_6": _array_of_ref(
                "sell_execution_plan_row", 128
            ),
            "cold_regime_review_proposal": review_or_null(
                "cold_regime_review"
            ),
            "post_cancel_redeployment_proposal": review_or_null(
                "post_cancel_redeployment"
            ),
            "reported_assumptions_and_data_gaps": _array_of_ref(
                "reported_assumption_or_gap_row", 128
            ),
        },
        "additionalProperties": False,
        "oneOf": [
            _ref("decision_draft_branch"),
            _ref("no_trade_branch"),
        ],
        "$defs": defs,
    }


_STEP2_DECISION_PACKET_SCHEMA = _build_step2_decision_packet_schema()


class Step2DecisionPacketDiagnostic(str, Enum):
    """Closed diagnostics for the pure packet structural contract."""

    DECISION_PACKET_MISSING = "decision_packet_missing"
    DECISION_PACKET_STRUCTURE_INVALID = "decision_packet_structure_invalid"
    DECISION_PACKET_SIZE_EXCEEDED = "decision_packet_size_exceeded"
    DECISION_PACKET_VERSION_INVALID = "decision_packet_version_invalid"
    DECISION_PACKET_MODE_INVALID = "decision_packet_mode_invalid"
    DECISION_PACKET_MARKET_OBSERVATIONS_INVALID = (
        "decision_packet_market_observations_invalid"
    )
    DECISION_PACKET_SCHEMA_INVALID = "decision_packet_schema_invalid"


@dataclass(frozen=True, slots=True)
class Step2DecisionPacketValidationResult:
    """Immutable, identity-only result of packet structural validation."""

    packet_mode: str | None
    structure_valid: bool
    schema_valid: bool
    market_observations_structure_valid: bool | None
    canonical_identity_sha256: str | None
    canonical_size_bytes: int | None
    diagnostics: tuple[Step2DecisionPacketDiagnostic, ...]
    result_version: str = field(
        default=DECISION_PACKET_VALIDATION_RESULT_VERSION,
        init=False,
    )
    schema_version: str = field(
        default=DECISION_PACKET_SCHEMA_VERSION,
        init=False,
    )
    identity_only: bool = field(default=IDENTITY_ONLY, init=False)
    not_authorization: bool = field(default=NOT_AUTHORIZATION, init=False)
    permission_effect: str = field(default=PERMISSION_EFFECT_NONE, init=False)
    semantic_validation_performed: bool = field(
        default=SEMANTIC_VALIDATION_PERFORMED,
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

    def __bool__(self) -> bool:
        raise TypeError(VALIDATION_BOOLEAN_COERCION_ERROR)


class _StructureInvalid(ValueError):
    """Internal bounded signal for unsupported or excessive JSON structure."""


class _CanonicalSizeExceeded(ValueError):
    """Internal bounded signal for canonical output beyond the byte contract."""


def _is_exact_object(_: Any, instance: Any) -> bool:
    return type(instance) is dict


def _is_exact_array(_: Any, instance: Any) -> bool:
    return type(instance) is list


def _is_exact_string(_: Any, instance: Any) -> bool:
    return type(instance) is str


def _is_exact_boolean(_: Any, instance: Any) -> bool:
    return type(instance) is bool


def _is_exact_integer(_: Any, instance: Any) -> bool:
    return type(instance) is int


def _is_exact_finite_number(_: Any, instance: Any) -> bool:
    return type(instance) is int or (
        type(instance) is float and math.isfinite(instance)
    )


def _is_exact_null(_: Any, instance: Any) -> bool:
    return instance is None


_EXACT_TYPE_CHECKER = Draft202012Validator.TYPE_CHECKER.redefine_many(
    {
        "object": _is_exact_object,
        "array": _is_exact_array,
        "string": _is_exact_string,
        "boolean": _is_exact_boolean,
        "integer": _is_exact_integer,
        "number": _is_exact_finite_number,
        "null": _is_exact_null,
    }
)
_ExactDraft202012Validator = validators.extend(
    Draft202012Validator,
    type_checker=_EXACT_TYPE_CHECKER,
)
_FORMAT_CHECKER = FormatChecker()


@_FORMAT_CHECKER.checks("date")
def _is_strict_calendar_date(value: Any) -> bool:
    if type(value) is not str or _STRICT_DATE_RE.fullmatch(value) is None:
        return False
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        return False
    return parsed.isoformat() == value


@_FORMAT_CHECKER.checks(_MARKET_OBSERVATIONS_FORMAT)
def _is_valid_market_observations_contract(value: Any) -> bool:
    result = validate_step2_market_observations(value)
    return result.structure_valid is True and result.schema_valid is True


_MARKET_OBSERVATIONS_REFERENCE_ADAPTER = {
    "$schema": _DRAFT_2020_12_URI,
    "$id": _MARKET_OBSERVATIONS_SCHEMA_URI,
    "format": _MARKET_OBSERVATIONS_FORMAT,
}
_SCHEMA_REGISTRY = Registry().with_resource(
    _MARKET_OBSERVATIONS_SCHEMA_URI,
    Resource.from_contents(_MARKET_OBSERVATIONS_REFERENCE_ADAPTER),
)
_ExactDraft202012Validator.check_schema(_STEP2_DECISION_PACKET_SCHEMA)
_SCHEMA_VALIDATOR = _ExactDraft202012Validator(
    _STEP2_DECISION_PACKET_SCHEMA,
    format_checker=_FORMAT_CHECKER,
    registry=_SCHEMA_REGISTRY,
)


def _has_approved_market_observations_reference_resource() -> bool:
    """Return whether the in-memory registry has the approved b1 adapter.

    The packet schema deliberately delegates its external market-observations
    reference to the public b1 validator through this format adapter.  A
    missing, incompatible, or unresolvable resource must fail closed before
    the JSON Schema implementation attempts external reference resolution.
    """
    try:
        retrieved = _SCHEMA_REGISTRY.get_or_retrieve(
            _MARKET_OBSERVATIONS_SCHEMA_URI
        )
    except (NoSuchResource, Unresolvable, Unretrievable):
        return False
    return retrieved.value.contents == _MARKET_OBSERVATIONS_REFERENCE_ADAPTER


def validate_step2_decision_packet_v2(
    value: object,
) -> Step2DecisionPacketValidationResult:
    """Validate one caller value under the pure packet v2 contract.

    Diagnostic priority is missing, structural/type/depth/node/cycle, canonical
    size, schema version, mode, embedded market-observations composition, then
    all remaining packet-schema defects.
    """
    if value is None:
        return _failure(
            Step2DecisionPacketDiagnostic.DECISION_PACKET_MISSING,
            structure_valid=False,
        )

    try:
        snapshot = _snapshot_json_value(value)
    except _StructureInvalid:
        return _failure(
            Step2DecisionPacketDiagnostic.DECISION_PACKET_STRUCTURE_INVALID,
            structure_valid=False,
        )

    try:
        canonical_bytes = _iterative_canonical_json_bytes(snapshot)
    except _CanonicalSizeExceeded:
        return _failure(
            Step2DecisionPacketDiagnostic.DECISION_PACKET_SIZE_EXCEEDED,
            structure_valid=True,
            canonical_size_bytes=MAX_CANONICAL_BYTES + 1,
        )
    except (UnicodeEncodeError, ValueError):
        return _failure(
            Step2DecisionPacketDiagnostic.DECISION_PACKET_STRUCTURE_INVALID,
            structure_valid=False,
        )

    canonical_size = len(canonical_bytes)
    canonical_identity = hashlib.sha256(canonical_bytes).hexdigest()
    packet_mode = _recognized_packet_mode(snapshot)

    if (
        type(snapshot) is dict
        and snapshot.get("schema_version") != DECISION_PACKET_SCHEMA_VERSION
    ):
        return _failure(
            Step2DecisionPacketDiagnostic.DECISION_PACKET_VERSION_INVALID,
            structure_valid=True,
            packet_mode=packet_mode,
            canonical_identity_sha256=canonical_identity,
            canonical_size_bytes=canonical_size,
        )

    if type(snapshot) is dict and packet_mode is None:
        return _failure(
            Step2DecisionPacketDiagnostic.DECISION_PACKET_MODE_INVALID,
            structure_valid=True,
            canonical_identity_sha256=canonical_identity,
            canonical_size_bytes=canonical_size,
        )

    market_validity = _market_observations_validity(snapshot, packet_mode)
    if market_validity is False:
        return _failure(
            Step2DecisionPacketDiagnostic.DECISION_PACKET_MARKET_OBSERVATIONS_INVALID,
            structure_valid=True,
            packet_mode=packet_mode,
            market_observations_structure_valid=False,
            canonical_identity_sha256=canonical_identity,
            canonical_size_bytes=canonical_size,
        )

    if not _has_approved_market_observations_reference_resource():
        return _failure(
            Step2DecisionPacketDiagnostic.DECISION_PACKET_MARKET_OBSERVATIONS_INVALID,
            structure_valid=True,
            packet_mode=packet_mode,
            market_observations_structure_valid=False,
            canonical_identity_sha256=canonical_identity,
            canonical_size_bytes=canonical_size,
        )

    try:
        schema_valid = _SCHEMA_VALIDATOR.is_valid(snapshot)
    except (
        _WrappedReferencingError,
        NoSuchResource,
        Unresolvable,
        Unretrievable,
    ):
        return _failure(
            Step2DecisionPacketDiagnostic.DECISION_PACKET_MARKET_OBSERVATIONS_INVALID,
            structure_valid=True,
            packet_mode=packet_mode,
            market_observations_structure_valid=False,
            canonical_identity_sha256=canonical_identity,
            canonical_size_bytes=canonical_size,
        )
    if not schema_valid:
        return _failure(
            Step2DecisionPacketDiagnostic.DECISION_PACKET_SCHEMA_INVALID,
            structure_valid=True,
            packet_mode=packet_mode,
            market_observations_structure_valid=market_validity,
            canonical_identity_sha256=canonical_identity,
            canonical_size_bytes=canonical_size,
        )

    return Step2DecisionPacketValidationResult(
        packet_mode=packet_mode,
        structure_valid=True,
        schema_valid=True,
        market_observations_structure_valid=market_validity,
        canonical_identity_sha256=canonical_identity,
        canonical_size_bytes=canonical_size,
        diagnostics=(),
    )


def _recognized_packet_mode(value: Any) -> str | None:
    if type(value) is not dict:
        return None
    mode = value.get("mode")
    if type(mode) is str and mode in DECISION_PACKET_MODES:
        return mode
    return None


def _market_observations_validity(
    snapshot: Any,
    packet_mode: str | None,
) -> bool | None:
    if type(snapshot) is not dict or "market_observations" not in snapshot:
        return None
    market_observations = snapshot["market_observations"]
    if market_observations is not None:
        result = validate_step2_market_observations(market_observations)
        return result.structure_valid is True and result.schema_valid is True

    if packet_mode == "DECISION_DRAFT":
        return False
    if packet_mode != "NO_TRADE":
        return None

    reason = snapshot.get("no_trade_reason")
    if type(reason) is dict:
        reason_code = reason.get("reason_code")
        if reason_code in NO_TRADE_REASONS_REQUIRING_MARKET_OBSERVATIONS:
            return False
    return None


def _failure(
    diagnostic: Step2DecisionPacketDiagnostic,
    *,
    structure_valid: bool,
    packet_mode: str | None = None,
    market_observations_structure_valid: bool | None = None,
    canonical_identity_sha256: str | None = None,
    canonical_size_bytes: int | None = None,
) -> Step2DecisionPacketValidationResult:
    return Step2DecisionPacketValidationResult(
        packet_mode=packet_mode,
        structure_valid=structure_valid,
        schema_valid=False,
        market_observations_structure_valid=(
            market_observations_structure_valid
        ),
        canonical_identity_sha256=canonical_identity_sha256,
        canonical_size_bytes=canonical_size_bytes,
        diagnostics=(diagnostic,),
    )


def _snapshot_json_value(value: Any) -> Any:
    """Iteratively copy one exact JSON-native value under packet bounds."""
    root: list[Any] = [None]
    active_container_ids: set[int] = set()
    node_count = 0
    stack: list[tuple[str, Any, Any, Any, int]] = [
        ("visit", value, root, 0, 0)
    ]

    while stack:
        operation, source, destination, slot, depth = stack.pop()
        if operation == "leave":
            active_container_ids.remove(id(source))
            continue

        node_count += 1
        if node_count > MAX_JSON_NODE_COUNT or depth > MAX_JSON_NESTING_DEPTH:
            raise _StructureInvalid from None

        if source is None or type(source) in {bool, str, int}:
            destination[slot] = source
            continue
        if type(source) is float:
            if not math.isfinite(source):
                raise _StructureInvalid from None
            destination[slot] = source
            continue
        if type(source) not in {dict, list}:
            raise _StructureInvalid from None

        source_id = id(source)
        if source_id in active_container_ids:
            raise _StructureInvalid from None
        active_container_ids.add(source_id)
        stack.append(("leave", source, None, None, depth))

        if node_count + len(source) > MAX_JSON_NODE_COUNT:
            raise _StructureInvalid from None

        if type(source) is dict:
            shallow_source = source.copy()
            if any(type(key) is not str for key in shallow_source):
                raise _StructureInvalid from None
            snapshot_object: dict[str, Any] = {}
            destination[slot] = snapshot_object
            for key in reversed(list(shallow_source)):
                stack.append(
                    (
                        "visit",
                        shallow_source[key],
                        snapshot_object,
                        key,
                        depth + 1,
                    )
                )
            continue

        shallow_array = source.copy()
        snapshot_array: list[Any] = [None] * len(shallow_array)
        destination[slot] = snapshot_array
        for index in range(len(shallow_array) - 1, -1, -1):
            stack.append(
                (
                    "visit",
                    shallow_array[index],
                    snapshot_array,
                    index,
                    depth + 1,
                )
            )

    return root[0]


def _iterative_canonical_json_bytes(value: Any) -> bytes:
    """Serialize a validated strict JSON tree without container recursion."""
    output = bytearray()
    stack: list[tuple[str, Any]] = [("value", value)]

    def append(fragment: bytes) -> None:
        if len(output) + len(fragment) > MAX_CANONICAL_BYTES:
            raise _CanonicalSizeExceeded from None
        output.extend(fragment)

    while stack:
        operation, current = stack.pop()
        if operation == "raw":
            append(current)
            continue
        if current is None:
            append(b"null")
            continue
        if type(current) is bool:
            append(b"true" if current else b"false")
            continue
        if type(current) is int:
            append(_integer_json_bytes(current))
            continue
        if type(current) is float:
            if not math.isfinite(current):
                raise ValueError from None
            append(json.dumps(current, allow_nan=False).encode("ascii"))
            continue
        if type(current) is str:
            if len(current) > MAX_CANONICAL_BYTES:
                raise _CanonicalSizeExceeded from None
            append(
                json.dumps(
                    current,
                    ensure_ascii=False,
                    allow_nan=False,
                    separators=(",", ":"),
                ).encode("utf-8")
            )
            continue
        if type(current) is list:
            append(b"[")
            stack.append(("raw", b"]"))
            for index in range(len(current) - 1, -1, -1):
                stack.append(("value", current[index]))
                if index != 0:
                    stack.append(("raw", b","))
            continue
        if type(current) is dict:
            append(b"{")
            stack.append(("raw", b"}"))
            keys = sorted(current)
            for index in range(len(keys) - 1, -1, -1):
                key = keys[index]
                if type(key) is not str:
                    raise ValueError from None
                stack.append(("value", current[key]))
                stack.append(("raw", b":"))
                stack.append(("value", key))
                if index != 0:
                    stack.append(("raw", b","))
            continue
        raise ValueError from None

    return bytes(output)


def _integer_json_bytes(value: int) -> bytes:
    if value == 0:
        return b"0"

    negative = value < 0
    magnitude = -value if negative else value
    lower_digit_bound = (
        ((magnitude.bit_length() - 1) * 30102) // 100000
    ) + 1
    if lower_digit_bound + int(negative) > MAX_CANONICAL_BYTES:
        raise _CanonicalSizeExceeded from None

    chunks: list[int] = []
    while magnitude:
        magnitude, remainder = divmod(magnitude, 1_000_000_000)
        chunks.append(remainder)

    encoded = bytearray(b"-" if negative else b"")
    encoded.extend(str(chunks.pop()).encode("ascii"))
    while chunks:
        encoded.extend(f"{chunks.pop():09d}".encode("ascii"))
        if len(encoded) > MAX_CANONICAL_BYTES:
            raise _CanonicalSizeExceeded from None
    return bytes(encoded)


__all__ = [
    "CANDIDATE_VALIDITY_EVALUATED",
    "DECISION_PACKET_MODES",
    "DECISION_PACKET_SCHEMA_FILENAME",
    "DECISION_PACKET_SCHEMA_VERSION",
    "DECISION_PACKET_VALIDATION_RESULT_VERSION",
    "FRESHNESS_EVALUATION_PERFORMED",
    "IDENTITY_ONLY",
    "MAX_CANONICAL_BYTES",
    "MAX_EXECUTION_STEPS",
    "MAX_IDENTIFIER_LENGTH",
    "MAX_JSON_NESTING_DEPTH",
    "MAX_JSON_NODE_COUNT",
    "MAX_RATIONALE_LENGTH",
    "MAX_REFERENCE_COUNT",
    "MAX_SAFE_INTEGER",
    "MAX_TICKER_ARRAY",
    "NOT_AUTHORIZATION",
    "NO_TRADE_REASON_CODES",
    "NO_TRADE_REASONS_REQUIRING_MARKET_OBSERVATIONS",
    "PERMISSION_EFFECT_NONE",
    "SEMANTIC_VALIDATION_PERFORMED",
    "UNIVERSE_RESOLUTION_PERFORMED",
    "VALIDATION_BOOLEAN_COERCION_ERROR",
    "Step2DecisionPacketDiagnostic",
    "Step2DecisionPacketValidationResult",
    "validate_step2_decision_packet_v2",
]
