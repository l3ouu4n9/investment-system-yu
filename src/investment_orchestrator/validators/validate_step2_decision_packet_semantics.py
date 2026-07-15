"""Pure non-freshness semantics for Step 2 decision-packet v2 values.

This validator proves only internal consistency visible inside one packet that
has passed the public Step 2 decision-packet v2 structural contract.  It does
not evaluate freshness, source truth, evidence existence, actual universe
membership, portfolio state or budget, permissions, publication, downstream
reachability, final safety, order compilation, or execution authority.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import math
from typing import Any

from investment_orchestrator.validators.validate_step2_decision_packet_v2 import (
    DECISION_PACKET_SCHEMA_VERSION,
    MAX_JSON_NESTING_DEPTH,
    MAX_JSON_NODE_COUNT,
    validate_step2_decision_packet_v2,
)


SEMANTIC_VALIDATION_RESULT_VERSION = (
    "step2_decision_packet_semantic_validation_result_v1"
)
MAX_SEMANTIC_DIAGNOSTICS = 36
NOT_AUTHORIZATION = True
PERMISSION_EFFECT_NONE = "none"
FRESHNESS_EVALUATION_PERFORMED = False
SOURCE_EVIDENCE_EVALUATION_PERFORMED = False
UNIVERSE_RESOLUTION_PERFORMED = False
PORTFOLIO_BUDGET_VALIDATION_PERFORMED = False
CANDIDATE_VALIDITY_EVALUATED = False

VALIDATION_BOOLEAN_COERCION_ERROR = (
    "inspect structural_validation_passed, semantic_validation_performed, "
    "and semantic_valid explicitly; semantic-validation results have no "
    "truth value"
)

_TECHNICAL_METRIC_FIELDS = (
    "atr_20_abs",
    "atr_20_30d_pct",
    "ma50",
    "ma200",
    "avg_volume_3m",
    "week_52_low",
    "week_52_high",
)

_BUY_ACTION_TO_PLAN_KIND = {
    "KEEP_EXISTING": "KEEP_EXISTING_LADDER",
    "NEW_ORDER": "NEW_LIMIT_LADDER",
    "REPLACE_EXISTING": "REPLACE_EXISTING_LADDER",
    "CANCEL_EXISTING": "CANCEL_EXISTING_ORDER",
}
_BUY_ACTIONS_REQUIRING_EXECUTION = frozenset(
    {"NEW_ORDER", "REPLACE_EXISTING", "CANCEL_EXISTING"}
)
_BUY_ACTIONS_FORBIDDING_EXECUTION = frozenset(
    {"HOLD_NO_NEW_BUDGET", "WATCHLIST_NO_TRADE"}
)
_BUY_ACTIONS_REQUIRING_UNIVERSE = frozenset(
    {"NEW_ORDER", "REPLACE_EXISTING"}
)


class Step2DecisionPacketSemanticDiagnostic(str, Enum):
    STRUCTURAL_PREREQUISITE_FAILED = "structural_prerequisite_failed"
    SNAPSHOT_CAPTURE_FAILED = "snapshot_capture_failed"
    SNAPSHOT_REVALIDATION_FAILED = "snapshot_revalidation_failed"
    SNAPSHOT_IDENTITY_MISMATCH = "snapshot_identity_mismatch"

    DUPLICATE_MARKET_OBSERVATION_TICKER = (
        "duplicate_market_observation_ticker"
    )
    MARKET_CLOSE_CLAIM_INCOMPLETE = "market_close_claim_incomplete"
    MARKET_NUMERIC_CLAIM_RETRIEVAL_TIMESTAMP_MISSING = (
        "market_numeric_claim_retrieval_timestamp_missing"
    )
    TECHNICAL_METRIC_SOURCE_MISSING = "technical_metric_source_missing"
    WEEK_52_RANGE_INVALID = "week_52_range_invalid"
    REPORTED_ISSUE_CLAIM_INCONSISTENT = (
        "reported_issue_claim_inconsistent"
    )

    DUPLICATE_SHORTLIST_TICKER = "duplicate_shortlist_ticker"
    DUPLICATE_SHORTLIST_RANK = "duplicate_shortlist_rank"
    SHORTLIST_TICKER_NOT_IN_PROPOSED_BUY_UNIVERSE = (
        "shortlist_ticker_not_in_proposed_buy_universe"
    )

    DUPLICATE_EXPOSURE_OVERLAP_TICKER = (
        "duplicate_exposure_overlap_ticker"
    )
    EXPOSURE_OVERLAP_SELF_REFERENCE = "exposure_overlap_self_reference"

    DUPLICATE_BUY_TICKER = "duplicate_buy_ticker"
    BUY_TICKER_NOT_IN_PROPOSED_BUY_UNIVERSE = (
        "buy_ticker_not_in_proposed_buy_universe"
    )
    BUY_ACTION_BUDGET_INCONSISTENT = "buy_action_budget_inconsistent"

    DUPLICATE_ROTATION_PAIR = "duplicate_rotation_pair"
    ROTATION_SELF_REFERENCE = "rotation_self_reference"
    ROTATION_CYCLE = "rotation_cycle"
    ROTATION_BUDGET_INCONSISTENT = "rotation_budget_inconsistent"

    DUPLICATE_SELL_TICKER = "duplicate_sell_ticker"
    SELL_ACTION_FIELDS_INCONSISTENT = "sell_action_fields_inconsistent"
    SELL_REPLACEMENT_SELF_REFERENCE = "sell_replacement_self_reference"
    SELL_REPLACEMENT_TICKER_NOT_IN_PROPOSED_BUY_UNIVERSE = (
        "sell_replacement_ticker_not_in_proposed_buy_universe"
    )
    SAME_TICKER_BUY_SELL_CONTRADICTION = (
        "same_ticker_buy_sell_contradiction"
    )
    ROTATION_ENDPOINT_INCONSISTENT = "rotation_endpoint_inconsistent"

    DUPLICATE_BUY_EXECUTION_TICKER = "duplicate_buy_execution_ticker"
    BUY_EXECUTION_CORRESPONDENCE_INVALID = (
        "buy_execution_correspondence_invalid"
    )
    BUY_EXECUTION_ACTION_KIND_INCONSISTENT = (
        "buy_execution_action_kind_inconsistent"
    )
    BUY_EXECUTION_STEPS_INCONSISTENT = (
        "buy_execution_steps_inconsistent"
    )

    DUPLICATE_SELL_EXECUTION_TICKER = "duplicate_sell_execution_ticker"
    SELL_EXECUTION_CORRESPONDENCE_INVALID = (
        "sell_execution_correspondence_invalid"
    )
    SELL_EXECUTION_QUANTITY_INCONSISTENT = (
        "sell_execution_quantity_inconsistent"
    )

    COLD_REGIME_PROPOSAL_INCONSISTENT = (
        "cold_regime_proposal_inconsistent"
    )
    COLD_REGIME_DEPLOYMENT_CANDIDATE_NOT_IN_PROPOSED_BUY_UNIVERSE = (
        "cold_regime_deployment_candidate_not_in_proposed_buy_universe"
    )

    POST_CANCEL_REDEPLOYMENT_PROPOSAL_INCONSISTENT = (
        "post_cancel_redeployment_proposal_inconsistent"
    )
    POST_CANCEL_REDEPLOYMENT_OVERLAP = (
        "post_cancel_redeployment_overlap"
    )
    POST_CANCEL_REDEPLOYMENT_ENDPOINT_INCONSISTENT = (
        "post_cancel_redeployment_endpoint_inconsistent"
    )


_D = Step2DecisionPacketSemanticDiagnostic


@dataclass(frozen=True, slots=True)
class Step2DecisionPacketSemanticValidationResult:
    """Immutable, non-authorizing semantic-validation result."""

    result_version: str = field(
        default=SEMANTIC_VALIDATION_RESULT_VERSION,
        init=False,
    )
    packet_schema_version: str | None
    packet_mode: str | None
    source_identity_sha256: str | None
    structural_validation_passed: bool
    semantic_validation_performed: bool
    semantic_valid: bool | None
    diagnostics: tuple[Step2DecisionPacketSemanticDiagnostic, ...]
    not_authorization: bool = field(default=NOT_AUTHORIZATION, init=False)
    permission_effect: str = field(default=PERMISSION_EFFECT_NONE, init=False)
    freshness_evaluation_performed: bool = field(
        default=FRESHNESS_EVALUATION_PERFORMED,
        init=False,
    )
    source_evidence_evaluation_performed: bool = field(
        default=SOURCE_EVIDENCE_EVALUATION_PERFORMED,
        init=False,
    )
    universe_resolution_performed: bool = field(
        default=UNIVERSE_RESOLUTION_PERFORMED,
        init=False,
    )
    portfolio_budget_validation_performed: bool = field(
        default=PORTFOLIO_BUDGET_VALIDATION_PERFORMED,
        init=False,
    )
    candidate_validity_evaluated: bool = field(
        default=CANDIDATE_VALIDITY_EVALUATED,
        init=False,
    )

    def __bool__(self) -> bool:
        raise TypeError(VALIDATION_BOOLEAN_COERCION_ERROR)


class _SnapshotCaptureFailure(str, Enum):
    UNSUPPORTED_EXACT_TYPE = "unsupported_exact_type"
    NON_STRING_MAPPING_KEY = "non_string_mapping_key"
    DEPTH_LIMIT_EXCEEDED = "depth_limit_exceeded"
    NODE_LIMIT_EXCEEDED = "node_limit_exceeded"
    CYCLE_DETECTED = "cycle_detected"
    MUTATION_DETECTED = "mutation_detected"


@dataclass(frozen=True, slots=True)
class _SnapshotCaptureOutcome:
    snapshot: Any | None
    failure: _SnapshotCaptureFailure | None


def validate_step2_decision_packet_semantics(
    value: object,
) -> Step2DecisionPacketSemanticValidationResult:
    """Validate packet-internal, non-freshness semantic consistency."""
    first = validate_step2_decision_packet_v2(value)
    if first.structure_valid is not True or first.schema_valid is not True:
        return _prerequisite_failure(
            Step2DecisionPacketSemanticDiagnostic.STRUCTURAL_PREREQUISITE_FAILED
        )

    capture = _capture_snapshot(value)
    if capture.failure is not None:
        return _prerequisite_failure(
            Step2DecisionPacketSemanticDiagnostic.SNAPSHOT_CAPTURE_FAILED
        )

    snapshot = capture.snapshot
    second = validate_step2_decision_packet_v2(snapshot)
    if second.structure_valid is not True or second.schema_valid is not True:
        return _prerequisite_failure(
            Step2DecisionPacketSemanticDiagnostic.SNAPSHOT_REVALIDATION_FAILED
        )

    first_identity = first.canonical_identity_sha256
    second_identity = second.canonical_identity_sha256
    if (
        first.schema_version != DECISION_PACKET_SCHEMA_VERSION
        or second.schema_version != DECISION_PACKET_SCHEMA_VERSION
        or first.schema_version != second.schema_version
        or first.packet_mode not in {"DECISION_DRAFT", "NO_TRADE"}
        or second.packet_mode not in {"DECISION_DRAFT", "NO_TRADE"}
        or first.packet_mode != second.packet_mode
        or type(first_identity) is not str
        or type(second_identity) is not str
        or first_identity != second_identity
    ):
        return _prerequisite_failure(
            Step2DecisionPacketSemanticDiagnostic.SNAPSHOT_IDENTITY_MISMATCH
        )

    if type(snapshot) is not dict:
        raise AssertionError

    diagnostics = _evaluate_semantics(snapshot)
    return Step2DecisionPacketSemanticValidationResult(
        packet_schema_version=second.schema_version,
        packet_mode=second.packet_mode,
        source_identity_sha256=second_identity,
        structural_validation_passed=True,
        semantic_validation_performed=True,
        semantic_valid=not diagnostics,
        diagnostics=diagnostics,
    )


def _prerequisite_failure(
    diagnostic: Step2DecisionPacketSemanticDiagnostic,
) -> Step2DecisionPacketSemanticValidationResult:
    return Step2DecisionPacketSemanticValidationResult(
        packet_schema_version=None,
        packet_mode=None,
        source_identity_sha256=None,
        structural_validation_passed=False,
        semantic_validation_performed=False,
        semantic_valid=None,
        diagnostics=(diagnostic,),
    )


def _capture_snapshot(value: Any) -> _SnapshotCaptureOutcome:
    """Copy an exact JSON-native value iteratively under public b2 bounds."""
    root: list[Any] = [None]
    active_container_ids: set[int] = set()
    node_count = 0
    # operation, source, destination, slot, depth, entry shallow copy
    stack: list[tuple[str, Any, Any, Any, int, Any]] = [
        ("visit", value, root, 0, 0, None)
    ]

    while stack:
        operation, source, destination, slot, depth, entry = stack.pop()
        if operation == "leave":
            current, failure = _stable_shallow_copy(source)
            if failure is not None:
                return _capture_failure(failure)
            if not _same_shallow_container(entry, current):
                return _capture_failure(
                    _SnapshotCaptureFailure.MUTATION_DETECTED
                )
            active_container_ids.remove(id(source))
            continue

        node_count += 1
        if depth > MAX_JSON_NESTING_DEPTH:
            return _capture_failure(
                _SnapshotCaptureFailure.DEPTH_LIMIT_EXCEEDED
            )
        if node_count > MAX_JSON_NODE_COUNT:
            return _capture_failure(
                _SnapshotCaptureFailure.NODE_LIMIT_EXCEEDED
            )

        if source is None or type(source) in {bool, str, int}:
            destination[slot] = source
            continue
        if type(source) is float:
            if not math.isfinite(source):
                return _capture_failure(
                    _SnapshotCaptureFailure.UNSUPPORTED_EXACT_TYPE
                )
            destination[slot] = source
            continue
        if type(source) not in {dict, list}:
            return _capture_failure(
                _SnapshotCaptureFailure.UNSUPPORTED_EXACT_TYPE
            )

        source_id = id(source)
        if source_id in active_container_ids:
            return _capture_failure(_SnapshotCaptureFailure.CYCLE_DETECTED)
        if node_count + len(source) > MAX_JSON_NODE_COUNT:
            return _capture_failure(
                _SnapshotCaptureFailure.NODE_LIMIT_EXCEEDED
            )

        shallow, failure = _stable_shallow_copy(source)
        if failure is not None:
            return _capture_failure(failure)
        active_container_ids.add(source_id)
        stack.append(("leave", source, None, None, depth, shallow))

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

    return _SnapshotCaptureOutcome(snapshot=root[0], failure=None)


def _stable_shallow_copy(
    source: dict[Any, Any] | list[Any],
) -> tuple[dict[str, Any] | list[Any] | None, _SnapshotCaptureFailure | None]:
    if type(source) is dict:
        first = source.copy()
        if any(type(key) is not str for key in first):
            return None, _SnapshotCaptureFailure.NON_STRING_MAPPING_KEY
        second = source.copy()
        if any(type(key) is not str for key in second):
            return None, _SnapshotCaptureFailure.NON_STRING_MAPPING_KEY
        if not _same_shallow_container(first, second):
            return None, _SnapshotCaptureFailure.MUTATION_DETECTED
        return first, None

    first_list = source.copy()
    second_list = source.copy()
    if not _same_shallow_container(first_list, second_list):
        return None, _SnapshotCaptureFailure.MUTATION_DETECTED
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


def _capture_failure(
    failure: _SnapshotCaptureFailure,
) -> _SnapshotCaptureOutcome:
    return _SnapshotCaptureOutcome(snapshot=None, failure=failure)


def _evaluate_semantics(
    packet: dict[str, Any],
) -> tuple[Step2DecisionPacketSemanticDiagnostic, ...]:
    findings: set[Step2DecisionPacketSemanticDiagnostic] = set()
    proposed_universe = set(packet["proposed_buy_universe"])

    _evaluate_market_observations(packet, findings)
    _evaluate_shortlist(packet, proposed_universe, findings)
    _evaluate_exposure(packet, findings)

    buy_rows = packet["buy_side_delta_table"]
    sell_rows = packet["sell_side_delta_table_8_2"]
    buy_execution_rows = packet["execution_plan_drafts_8_5"]
    sell_execution_rows = packet["sell_execution_plan_drafts_8_6"]
    rotation_rows = packet["rotation_decision_layer_8_15"]

    buy_counts = _key_counts(buy_rows, "ticker")
    sell_counts = _key_counts(sell_rows, "ticker")
    buy_execution_counts = _key_counts(buy_execution_rows, "ticker")
    sell_execution_counts = _key_counts(sell_execution_rows, "ticker")

    _evaluate_buy_rows(buy_rows, proposed_universe, buy_counts, findings)
    _evaluate_sell_rows(sell_rows, proposed_universe, sell_counts, findings)
    _evaluate_same_ticker_actions(
        buy_rows,
        sell_rows,
        buy_counts,
        sell_counts,
        findings,
    )
    _evaluate_rotations(
        rotation_rows,
        proposed_universe,
        buy_rows,
        sell_rows,
        buy_counts,
        sell_counts,
        findings,
    )
    _evaluate_buy_execution(
        buy_rows,
        buy_execution_rows,
        proposed_universe,
        buy_counts,
        buy_execution_counts,
        findings,
    )
    _evaluate_sell_execution(
        sell_rows,
        sell_execution_rows,
        sell_counts,
        sell_execution_counts,
        findings,
    )
    _evaluate_cold_regime(packet, proposed_universe, findings)
    _evaluate_redeployment(
        packet,
        proposed_universe,
        buy_rows,
        buy_counts,
        findings,
    )

    ordered = tuple(
        diagnostic
        for diagnostic in Step2DecisionPacketSemanticDiagnostic
        if diagnostic in findings
    )
    if len(ordered) > MAX_SEMANTIC_DIAGNOSTICS:
        raise AssertionError
    return ordered


def _evaluate_market_observations(
    packet: dict[str, Any],
    findings: set[Step2DecisionPacketSemanticDiagnostic],
) -> None:
    market = packet["market_observations"]
    if market is None:
        return
    rows = market["observations"]
    if _has_duplicates(row["ticker"] for row in rows):
        findings.add(
            Step2DecisionPacketSemanticDiagnostic.DUPLICATE_MARKET_OBSERVATION_TICKER
        )

    for row in rows:
        last_close = row["last_close"]
        technical_values = tuple(row[field] for field in _TECHNICAL_METRIC_FIELDS)
        technical_present = any(value is not None for value in technical_values)
        numeric_present = last_close is not None or technical_present

        if last_close is not None and any(
            row[field] is None
            for field in (
                "reported_price_asof",
                "reported_last_close_source",
                "reported_price_source",
            )
        ):
            findings.add(
                Step2DecisionPacketSemanticDiagnostic.MARKET_CLOSE_CLAIM_INCOMPLETE
            )
        if numeric_present and row["reported_retrieved_at_utc"] is None:
            findings.add(
                _D.MARKET_NUMERIC_CLAIM_RETRIEVAL_TIMESTAMP_MISSING
            )
        if technical_present and row["reported_technicals_source"] is None:
            findings.add(
                Step2DecisionPacketSemanticDiagnostic.TECHNICAL_METRIC_SOURCE_MISSING
            )

        low = row["week_52_low"]
        high = row["week_52_high"]
        if low is not None and high is not None and low > high:
            findings.add(
                Step2DecisionPacketSemanticDiagnostic.WEEK_52_RANGE_INVALID
            )
        if _reported_issue_claim_inconsistent(row, technical_values):
            findings.add(
                Step2DecisionPacketSemanticDiagnostic.REPORTED_ISSUE_CLAIM_INCONSISTENT
            )


def _reported_issue_claim_inconsistent(
    row: dict[str, Any],
    technical_values: tuple[Any, ...],
) -> bool:
    codes = set(row["reported_issue_codes"])
    checks = (
        (
            "MISSING_LAST_CLOSE_CLAIM" in codes
            and row["last_close"] is not None
        ),
        (
            "MISSING_PRICE_DATE_CLAIM" in codes
            and row["reported_price_asof"] is not None
        ),
        (
            "MISSING_CLOSE_SOURCE_CLAIM" in codes
            and row["reported_last_close_source"] is not None
            and row["reported_price_source"] is not None
        ),
        (
            "MISSING_TECHNICALS_CLAIM" in codes
            and all(value is not None for value in technical_values)
        ),
        (
            "MISSING_TECHNICAL_SOURCE_CLAIM" in codes
            and row["reported_technicals_source"] is not None
        ),
        (
            "MISSING_RETRIEVAL_TIMESTAMP_CLAIM" in codes
            and row["reported_retrieved_at_utc"] is not None
        ),
    )
    return any(checks)


def _evaluate_shortlist(
    packet: dict[str, Any],
    proposed_universe: set[str],
    findings: set[Step2DecisionPacketSemanticDiagnostic],
) -> None:
    rows = packet["active_shortlist"]
    if _has_duplicates(row["ticker"] for row in rows):
        findings.add(
            Step2DecisionPacketSemanticDiagnostic.DUPLICATE_SHORTLIST_TICKER
        )
    if _has_duplicates(row["rank"] for row in rows):
        findings.add(
            Step2DecisionPacketSemanticDiagnostic.DUPLICATE_SHORTLIST_RANK
        )
    if any(
        row["proposal_status"] == "SELECTED"
        and row["ticker"] not in proposed_universe
        for row in rows
    ):
        findings.add(
            _D.SHORTLIST_TICKER_NOT_IN_PROPOSED_BUY_UNIVERSE
        )


def _evaluate_exposure(
    packet: dict[str, Any],
    findings: set[Step2DecisionPacketSemanticDiagnostic],
) -> None:
    rows = packet["exposure_overlap_diagnostics"]
    if _has_duplicates(row["ticker"] for row in rows):
        findings.add(
            Step2DecisionPacketSemanticDiagnostic.DUPLICATE_EXPOSURE_OVERLAP_TICKER
        )
    if any(row["ticker"] in row["overlaps_with"] for row in rows):
        findings.add(
            Step2DecisionPacketSemanticDiagnostic.EXPOSURE_OVERLAP_SELF_REFERENCE
        )


def _evaluate_buy_rows(
    rows: list[dict[str, Any]],
    proposed_universe: set[str],
    counts: dict[str, int],
    findings: set[Step2DecisionPacketSemanticDiagnostic],
) -> None:
    if any(count > 1 for count in counts.values()):
        findings.add(Step2DecisionPacketSemanticDiagnostic.DUPLICATE_BUY_TICKER)
    for row in rows:
        action = row["proposed_action"]
        if (
            action in _BUY_ACTIONS_REQUIRING_UNIVERSE
            and row["ticker"] not in proposed_universe
        ):
            findings.add(
                _D.BUY_TICKER_NOT_IN_PROPOSED_BUY_UNIVERSE
            )
        if (
            action in _BUY_ACTIONS_REQUIRING_UNIVERSE
            and row["proposed_budget_cents"] == 0
        ):
            findings.add(
                Step2DecisionPacketSemanticDiagnostic.BUY_ACTION_BUDGET_INCONSISTENT
            )


def _evaluate_sell_rows(
    rows: list[dict[str, Any]],
    proposed_universe: set[str],
    counts: dict[str, int],
    findings: set[Step2DecisionPacketSemanticDiagnostic],
) -> None:
    if any(count > 1 for count in counts.values()):
        findings.add(Step2DecisionPacketSemanticDiagnostic.DUPLICATE_SELL_TICKER)
    for row in rows:
        action = row["proposed_action"]
        quantity = row["proposed_share_quantity"]
        replacement = row["replacement_ticker"]
        if action == "HOLD_NO_SELL":
            if quantity not in {None, 0} or replacement is not None:
                findings.add(
                    _D.SELL_ACTION_FIELDS_INCONSISTENT
                )
            continue

        if quantity == 0:
            findings.add(
                Step2DecisionPacketSemanticDiagnostic.SELL_ACTION_FIELDS_INCONSISTENT
            )
        if replacement == row["ticker"]:
            findings.add(
                Step2DecisionPacketSemanticDiagnostic.SELL_REPLACEMENT_SELF_REFERENCE
            )
        if replacement is not None and replacement not in proposed_universe:
            findings.add(
                _D.SELL_REPLACEMENT_TICKER_NOT_IN_PROPOSED_BUY_UNIVERSE
            )


def _evaluate_same_ticker_actions(
    buy_rows: list[dict[str, Any]],
    sell_rows: list[dict[str, Any]],
    buy_counts: dict[str, int],
    sell_counts: dict[str, int],
    findings: set[Step2DecisionPacketSemanticDiagnostic],
) -> None:
    buy_by_ticker = _unique_rows(buy_rows, buy_counts, "ticker")
    sell_by_ticker = _unique_rows(sell_rows, sell_counts, "ticker")
    for ticker in buy_by_ticker.keys() & sell_by_ticker.keys():
        if (
            buy_by_ticker[ticker]["proposed_action"]
            in {"NEW_ORDER", "REPLACE_EXISTING"}
            and sell_by_ticker[ticker]["proposed_action"] == "SELL"
        ):
            findings.add(
                Step2DecisionPacketSemanticDiagnostic.SAME_TICKER_BUY_SELL_CONTRADICTION
            )


def _evaluate_rotations(
    rows: list[dict[str, Any]],
    proposed_universe: set[str],
    buy_rows: list[dict[str, Any]],
    sell_rows: list[dict[str, Any]],
    buy_counts: dict[str, int],
    sell_counts: dict[str, int],
    findings: set[Step2DecisionPacketSemanticDiagnostic],
) -> None:
    pair_counts = _pair_counts(rows, "from_ticker", "to_ticker")
    if any(count > 1 for count in pair_counts.values()):
        findings.add(
            Step2DecisionPacketSemanticDiagnostic.DUPLICATE_ROTATION_PAIR
        )
    if any(row["from_ticker"] == row["to_ticker"] for row in rows):
        findings.add(Step2DecisionPacketSemanticDiagnostic.ROTATION_SELF_REFERENCE)

    graph_edges = {
        pair for pair in pair_counts if pair[0] != pair[1]
    }
    if _directed_graph_has_cycle(graph_edges):
        findings.add(Step2DecisionPacketSemanticDiagnostic.ROTATION_CYCLE)
    if any(row["proposed_budget_cents"] == 0 for row in rows):
        findings.add(
            Step2DecisionPacketSemanticDiagnostic.ROTATION_BUDGET_INCONSISTENT
        )

    buy_by_ticker = _unique_rows(buy_rows, buy_counts, "ticker")
    sell_by_ticker = _unique_rows(sell_rows, sell_counts, "ticker")
    for source, destination in pair_counts:
        if pair_counts[(source, destination)] > 1:
            continue
        if (
            buy_counts.get(source, 0) > 1
            or sell_counts.get(source, 0) > 1
            or buy_counts.get(destination, 0) > 1
        ):
            continue
        source_buy = buy_by_ticker.get(source)
        source_sell = sell_by_ticker.get(source)
        destination_buy = buy_by_ticker.get(destination)
        qualifying_cancel_source = (
            source_buy is not None
            and source_buy["proposed_action"] == "CANCEL_EXISTING"
        )
        qualifying_sell_source = (
            source_sell is not None
            and source_sell["proposed_action"] == "SELL"
        )
        source_supported = (
            int(qualifying_cancel_source) + int(qualifying_sell_source) == 1
        )
        destination_supported = (
            destination_buy is not None
            and destination_buy["proposed_action"]
            in {"NEW_ORDER", "REPLACE_EXISTING"}
            and destination in proposed_universe
        )
        if not source_supported or not destination_supported:
            findings.add(
                Step2DecisionPacketSemanticDiagnostic.ROTATION_ENDPOINT_INCONSISTENT
            )


def _directed_graph_has_cycle(edges: set[tuple[str, str]]) -> bool:
    adjacency: dict[str, set[str]] = {}
    indegree: dict[str, int] = {}
    for source, destination in edges:
        adjacency.setdefault(source, set()).add(destination)
        adjacency.setdefault(destination, set())
        indegree.setdefault(source, 0)
        indegree[destination] = indegree.get(destination, 0) + 1

    ready = sorted(node for node, degree in indegree.items() if degree == 0)
    visited = 0
    while ready:
        node = ready.pop()
        visited += 1
        for destination in sorted(adjacency[node], reverse=True):
            indegree[destination] -= 1
            if indegree[destination] == 0:
                ready.append(destination)
    return visited != len(indegree)


def _evaluate_buy_execution(
    buy_rows: list[dict[str, Any]],
    execution_rows: list[dict[str, Any]],
    proposed_universe: set[str],
    buy_counts: dict[str, int],
    execution_counts: dict[str, int],
    findings: set[Step2DecisionPacketSemanticDiagnostic],
) -> None:
    if any(count > 1 for count in execution_counts.values()):
        findings.add(
            Step2DecisionPacketSemanticDiagnostic.DUPLICATE_BUY_EXECUTION_TICKER
        )

    for row in execution_rows:
        action = row["proposal_action"]
        if (
            action in _BUY_ACTIONS_REQUIRING_UNIVERSE
            and row["ticker"] not in proposed_universe
        ):
            findings.add(
                _D.BUY_TICKER_NOT_IN_PROPOSED_BUY_UNIVERSE
            )
        if row["plan_kind"] != _BUY_ACTION_TO_PLAN_KIND[action]:
            findings.add(
                _D.BUY_EXECUTION_ACTION_KIND_INCONSISTENT
            )
        if not _execution_steps_consistent(row):
            findings.add(
                Step2DecisionPacketSemanticDiagnostic.BUY_EXECUTION_STEPS_INCONSISTENT
            )

    buy_by_ticker = _unique_rows(buy_rows, buy_counts, "ticker")
    execution_by_ticker = _unique_rows(
        execution_rows, execution_counts, "ticker"
    )
    for ticker in buy_counts.keys() | execution_counts.keys():
        if buy_counts.get(ticker, 0) > 1 or execution_counts.get(ticker, 0) > 1:
            continue
        buy = buy_by_ticker.get(ticker)
        execution = execution_by_ticker.get(ticker)
        if buy is None:
            if execution is not None:
                findings.add(
                    _D.BUY_EXECUTION_CORRESPONDENCE_INVALID
                )
            continue

        action = buy["proposed_action"]
        if action in _BUY_ACTIONS_REQUIRING_EXECUTION:
            valid = execution is not None
        elif action in _BUY_ACTIONS_FORBIDDING_EXECUTION:
            valid = execution is None
        else:
            valid = True
        if execution is not None and execution["proposal_action"] != action:
            valid = False
        if not valid:
            findings.add(
                _D.BUY_EXECUTION_CORRESPONDENCE_INVALID
            )


def _execution_steps_consistent(row: dict[str, Any]) -> bool:
    kind = row["plan_kind"]
    steps = row["proposed_steps"]
    if kind in {"NEW_LIMIT_LADDER", "REPLACE_EXISTING_LADDER"} and not steps:
        return False
    if kind == "CANCEL_EXISTING_ORDER":
        return (
            not steps
            and row["proposed_time_in_force"] == "DAY"
            and row["proposed_expiry_date"] is None
        )
    if not steps:
        return True
    labels = [step["step_label"] for step in steps]
    offsets = [step["proposed_offset_bps"] for step in steps]
    weights = [step["proposed_weight_bps"] for step in steps]
    return (
        len(labels) == len(set(labels))
        and len(offsets) == len(set(offsets))
        and all(weight > 0 for weight in weights)
        and sum(weights) == 10_000
    )


def _evaluate_sell_execution(
    sell_rows: list[dict[str, Any]],
    execution_rows: list[dict[str, Any]],
    sell_counts: dict[str, int],
    execution_counts: dict[str, int],
    findings: set[Step2DecisionPacketSemanticDiagnostic],
) -> None:
    if any(count > 1 for count in execution_counts.values()):
        findings.add(
            Step2DecisionPacketSemanticDiagnostic.DUPLICATE_SELL_EXECUTION_TICKER
        )

    sell_by_ticker = _unique_rows(sell_rows, sell_counts, "ticker")
    execution_by_ticker = _unique_rows(
        execution_rows, execution_counts, "ticker"
    )
    for ticker in sell_counts.keys() | execution_counts.keys():
        if sell_counts.get(ticker, 0) > 1 or execution_counts.get(ticker, 0) > 1:
            continue
        sell = sell_by_ticker.get(ticker)
        execution = execution_by_ticker.get(ticker)
        correspondence_valid = (
            sell is not None
            and sell["proposed_action"] == "SELL"
            and execution is not None
        ) or (
            sell is not None
            and sell["proposed_action"] == "HOLD_NO_SELL"
            and execution is None
        )
        if sell is None and execution is None:
            correspondence_valid = True
        if not correspondence_valid:
            findings.add(
                _D.SELL_EXECUTION_CORRESPONDENCE_INVALID
            )
            continue
        if sell is None or execution is None:
            continue
        sell_quantity = sell["proposed_share_quantity"]
        execution_quantity = execution["proposed_share_quantity"]
        quantities_valid = (
            sell_quantity is None and execution_quantity is None
        ) or (
            type(sell_quantity) is int
            and sell_quantity > 0
            and sell_quantity == execution_quantity
        )
        if not quantities_valid:
            findings.add(
                _D.SELL_EXECUTION_QUANTITY_INCONSISTENT
            )


def _evaluate_cold_regime(
    packet: dict[str, Any],
    proposed_universe: set[str],
    findings: set[Step2DecisionPacketSemanticDiagnostic],
) -> None:
    proposal = packet["cold_regime_review_proposal"]
    if proposal is None:
        return
    triggered = proposal["reported_triggered"]
    conclusion = proposal["conclusion_claim"]
    candidates = proposal["candidate_tickers"]
    valid = (
        (not triggered and conclusion == "NOT_TRIGGERED" and not candidates)
        or (
            triggered
            and conclusion in {"PRESERVE_HEADROOM", "INSUFFICIENT_EVIDENCE"}
        )
        or (triggered and conclusion == "PROPOSE_DEPLOYMENT" and bool(candidates))
    )
    if not valid:
        findings.add(
            Step2DecisionPacketSemanticDiagnostic.COLD_REGIME_PROPOSAL_INCONSISTENT
        )
        return
    if conclusion == "PROPOSE_DEPLOYMENT" and any(
        ticker not in proposed_universe for ticker in candidates
    ):
        findings.add(
            _D.COLD_REGIME_DEPLOYMENT_CANDIDATE_NOT_IN_PROPOSED_BUY_UNIVERSE
        )


def _evaluate_redeployment(
    packet: dict[str, Any],
    proposed_universe: set[str],
    buy_rows: list[dict[str, Any]],
    buy_counts: dict[str, int],
    findings: set[Step2DecisionPacketSemanticDiagnostic],
) -> None:
    redeployment = packet["post_cancel_redeployment_proposal"]
    if redeployment is None:
        return
    sources = redeployment["source_tickers"]
    destinations = redeployment["destination_tickers"]
    source_set = set(sources)
    destination_set = set(destinations)
    overlap = bool(source_set & destination_set)
    proposal = redeployment["proposal"]
    budget = redeployment["proposed_budget_cents"]

    if overlap:
        findings.add(
            Step2DecisionPacketSemanticDiagnostic.POST_CANCEL_REDEPLOYMENT_OVERLAP
        )
    matrix_valid = (
        proposal in {"NO_REDEPLOYMENT", "PRESERVE_HEADROOM"}
        and not destinations
        and budget is None
    ) or (
        proposal == "REDEPLOY"
        and bool(sources)
        and bool(destinations)
        and not overlap
        and type(budget) is int
        and budget > 0
    )
    if not matrix_valid:
        findings.add(
            _D.POST_CANCEL_REDEPLOYMENT_PROPOSAL_INCONSISTENT
        )
        return
    if proposal != "REDEPLOY":
        return

    buy_by_ticker = _unique_rows(buy_rows, buy_counts, "ticker")
    endpoint_invalid = False
    for source in sources:
        if buy_counts.get(source, 0) > 1:
            continue
        row = buy_by_ticker.get(source)
        if row is None or row["proposed_action"] not in {
            "CANCEL_EXISTING",
            "REPLACE_EXISTING",
        }:
            endpoint_invalid = True
    for destination in destinations:
        if buy_counts.get(destination, 0) > 1:
            continue
        row = buy_by_ticker.get(destination)
        if (
            destination not in proposed_universe
            or row is None
            or row["proposed_action"] not in {"NEW_ORDER", "REPLACE_EXISTING"}
        ):
            endpoint_invalid = True
    if endpoint_invalid:
        findings.add(
            _D.POST_CANCEL_REDEPLOYMENT_ENDPOINT_INCONSISTENT
        )


def _has_duplicates(values: Any) -> bool:
    seen: set[Any] = set()
    for value in values:
        if value in seen:
            return True
        seen.add(value)
    return False


def _key_counts(
    rows: list[dict[str, Any]],
    key: str,
) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        value = row[key]
        counts[value] = counts.get(value, 0) + 1
    return counts


def _pair_counts(
    rows: list[dict[str, Any]],
    first_key: str,
    second_key: str,
) -> dict[tuple[str, str], int]:
    counts: dict[tuple[str, str], int] = {}
    for row in rows:
        pair = (row[first_key], row[second_key])
        counts[pair] = counts.get(pair, 0) + 1
    return counts


def _unique_rows(
    rows: list[dict[str, Any]],
    counts: dict[str, int],
    key: str,
) -> dict[str, dict[str, Any]]:
    return {row[key]: row for row in rows if counts[row[key]] == 1}


__all__ = [
    "CANDIDATE_VALIDITY_EVALUATED",
    "FRESHNESS_EVALUATION_PERFORMED",
    "MAX_SEMANTIC_DIAGNOSTICS",
    "NOT_AUTHORIZATION",
    "PERMISSION_EFFECT_NONE",
    "PORTFOLIO_BUDGET_VALIDATION_PERFORMED",
    "SEMANTIC_VALIDATION_RESULT_VERSION",
    "SOURCE_EVIDENCE_EVALUATION_PERFORMED",
    "UNIVERSE_RESOLUTION_PERFORMED",
    "VALIDATION_BOOLEAN_COERCION_ERROR",
    "Step2DecisionPacketSemanticDiagnostic",
    "Step2DecisionPacketSemanticValidationResult",
    "validate_step2_decision_packet_semantics",
]
