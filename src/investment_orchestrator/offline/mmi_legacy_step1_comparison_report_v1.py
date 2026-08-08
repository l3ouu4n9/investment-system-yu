"""Deterministic offline comparison of the H1 candidate against legacy Step 1.

This module owns one closed, report-only artifact that records auditable
structural and provenance facts about a validated MMI legacy-Step-1 semantic
compatibility candidate (H1) and one explicitly supplied historical legacy
Step 1 output.  It establishes nothing else.

The report deliberately cannot express semantic equivalence of prose, research
quality, investment correctness, recommendation or ranking agreement, migration
readiness, availability, freshness, ``HOLD`` / ``NO_TRADE`` / ``SELL`` /
``NEW_BUY`` / ``ORDER_COMPILATION``, or publication / order / execution
readiness.  It carries no free text from either side: only presence, absence,
counts, fixed enums, identifiers, role vocabularies, contract statuses, and
hashes.

Every contract decision is delegated to an existing owner:

* the H1 candidate is reauthenticated by
  :func:`validate_mmi_legacy_step1_compatibility_candidate_v1`;
* legacy extraction is owned by :func:`parse_research_output_text`;
* legacy normalization is owned by
  :func:`normalize_research_handoff_candidate`;
* the legacy strict handoff contract is owned by
  :func:`validate_research_handoff`.

Nothing here re-implements, repairs, or approximates any of those policies, and
``research_output.validation_summary.passed`` is mirrored but never trusted.

Two canonical forms are used and are kept strictly separate:

* legacy input payloads are hashed with the repository's existing legacy
  payload canonical form (sorted compact JSON), so the recorded hashes are
  directly comparable to identities already persisted elsewhere in the
  repository;
* the report itself and its identity use the MMI canonical / domain-separated
  framing.

This module is offline and report-only.  Its only production consumer is the
explicit operator-invoked H2c foreground capture session.  It registers no
CLI, writer, workflow, gate, pointer, or order surface, and performs no
filesystem, network, clock, or scheduling work.
"""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import io
import json
from typing import Final, NoReturn

from investment_orchestrator.common.schema_validation import (
    ArtifactSchemaError,
    validate_artifact_schema,
)
from investment_orchestrator.mmi.canonical import (
    MAX_MMI_LEGACY_STEP1_COMPARISON_REPORT_V1_CANONICAL_BYTES,
    MmiCanonicalizationError,
    _MMI_LEGACY_STEP1_COMPARISON_REPORT_V1_IDENTITY_DOMAIN,
    canonical_json_bytes,
    record_identity_sha256,
)
from investment_orchestrator.mmi.contracts import AUTHORITY_EFFECT_NONE
from investment_orchestrator.mmi.legacy_step1_compatibility_candidate_v1 import (
    MmiLegacyStep1CompatibilityCandidateV1Error,
    validate_mmi_legacy_step1_compatibility_candidate_v1,
)
from investment_orchestrator.normalizers.research_handoff_candidate import (
    normalize_research_handoff_candidate,
)
from investment_orchestrator.parsers.extract_research_json import (
    ResearchExtractionError,
    parse_research_output_text,
)
from investment_orchestrator.validators.validate_research_handoff import (
    validate_research_handoff,
)


__all__ = (
    "MmiLegacyStep1ComparisonReportV1Error",
    "build_mmi_legacy_step1_comparison_report_v1",
    "validate_mmi_legacy_step1_comparison_report_v1",
)

_SCHEMA_VERSION: Final = "mmi_legacy_step1_comparison_report_v1"
_ARTIFACT_KIND: Final = "MMI_LEGACY_STEP1_COMPARISON_REPORT"
_COMPARISON_CONTRACT_VERSION: Final = (
    "mmi_legacy_step1_comparison_compiler_v1"
)
_SCHEMA_NAME: Final = "mmi_legacy_step1_comparison_report_v1.schema.json"
_IDENTITY_FIELD: Final = "comparison_report_identity_sha256"
_ZERO_SHA256: Final = "0" * 64

_H1_IDENTITY_FIELD: Final = (
    "legacy_step1_compatibility_candidate_identity_sha256"
)

# H2 replay-input ceilings.  Neither states that larger legacy content is
# invalid under the legacy Step 1 workflow, which applies no size guard of its
# own; both bound only what this offline report is willing to ingest.
MAX_LEGACY_RESEARCH_RAW_BYTES: Final = 262_144
MAX_LEGACY_STRATEGY_SETTINGS_CANONICAL_BYTES: Final = 262_144

MAX_COMPARED_INSTRUMENTS: Final = 256
MAX_LEGACY_TICKER_CHARACTERS: Final = 32
MAX_LEGACY_ROLE_LAYER_CHARACTERS: Final = 32
MAX_LEGACY_ROLE_LAYERS_PRESENT: Final = 16

_TOP_LEVEL_FIELDS: Final = frozenset(
    {
        "schema_version",
        "artifact_kind",
        "comparison_contract_version",
        "report_only",
        "authority_effect",
        "provenance",
        "legacy_contract_status",
        "instrument_comparison",
        "coverage_comparison",
        "limitations",
        "comparison_summary",
        _IDENTITY_FIELD,
    }
)

_PARSED: Final = "PARSED"
_LEGACY_PARSE_FAILURE: Final = "LEGACY_PARSE_FAILURE"
_LEGACY_SCHEMA_FAILURE: Final = "LEGACY_SCHEMA_FAILURE"

_STRICT_HANDOFF_VALID: Final = "STRICT_HANDOFF_VALID"
_LEGACY_HANDOFF_CONTRACT_FAILURE: Final = "LEGACY_HANDOFF_CONTRACT_FAILURE"
_NOT_EVALUATED: Final = "NOT_EVALUATED"

_BASIS_STRICT_VALID: Final = "STRICT_VALID"
_BASIS_STRUCTURALLY_READABLE: Final = (
    "STRICT_INVALID_BUT_STRUCTURALLY_READABLE"
)
_BASIS_UNAVAILABLE_PARSE_FAILURE: Final = "UNAVAILABLE_PARSE_FAILURE"
_BASIS_UNAVAILABLE_SCHEMA_FAILURE: Final = "UNAVAILABLE_SCHEMA_FAILURE"
_BASIS_UNAVAILABLE_SCORECARD_SHAPE: Final = "UNAVAILABLE_SCORECARD_SHAPE"
_READABLE_BASES: Final = frozenset(
    {_BASIS_STRICT_VALID, _BASIS_STRUCTURALLY_READABLE}
)

_H1_PRESENT: Final = "PRESENT"
_H1_ABSENT: Final = "ABSENT"
_H1_TIER_A_UNAVAILABLE: Final = "EXPLICITLY_UNAVAILABLE_TIER_A"
_H1_POLICY_METHOD_ABSENT: Final = "POLICY_METHOD_ABSENT"
_H1_NOT_REPRESENTED: Final = "NOT_REPRESENTED"

_LEGACY_PRESENT: Final = "PRESENT"
_LEGACY_ABSENT: Final = "ABSENT"
_LEGACY_NOT_REPRESENTED: Final = "NOT_REPRESENTED"
_LEGACY_UNAVAILABLE: Final = "UNAVAILABLE_DUE_TO_LEGACY_CONTRACT"

_AVAILABLE_IN_BOTH: Final = "AVAILABLE_IN_BOTH"
_AVAILABLE_ONLY_IN_H1: Final = "AVAILABLE_ONLY_IN_H1"
_AVAILABLE_ONLY_IN_LEGACY: Final = "AVAILABLE_ONLY_IN_LEGACY"
_AVAILABLE_IN_NEITHER: Final = "AVAILABLE_IN_NEITHER"
_NOT_COMPARABLE: Final = "NOT_COMPARABLE"

_DETERMINISTIC_CONSUMER_PRESENT: Final = "DETERMINISTIC_CONSUMER_PRESENT"
_PROMPT_ONLY: Final = "PROMPT_ONLY"
_NO_CONSUMER: Final = "NO_CONSUMER"

# Fixed contract order of the coverage rows.
COVERAGE_CATEGORIES: Final = (
    "INSTRUMENT_RATIONALE",
    "INSTRUMENT_REFERENCES",
    "EVIDENCE_OBSERVATIONS",
    "RISKS",
    "UNCERTAINTIES",
    "CONTRADICTIONS",
    "RESEARCH_QUESTIONS",
    "SUMMARY",
    "ANCHOR_ASSOCIATIONS",
    "SCHEDULED_EVENTS",
    "REGIME_INPUTS",
    "TARGET_WEIGHTS",
    "STRUCTURAL_THEMES",
    "TOP_FIVE_NEXT_WEEK",
    "EXTENDED_ETF_SLEEVE_FIELDS",
)

# Whether a deterministic production consumer reads the legacy counterpart of a
# category, or whether it only ever reaches an LLM through the Step 2/3/4
# prompt text, or is read by nothing at all.  Pinned to this comparison
# contract version: a change in real consumers requires a new contract version.
_LEGACY_CONSUMER_CLASS: Final = {
    "INSTRUMENT_RATIONALE": _DETERMINISTIC_CONSUMER_PRESENT,
    "INSTRUMENT_REFERENCES": _DETERMINISTIC_CONSUMER_PRESENT,
    "EVIDENCE_OBSERVATIONS": _NO_CONSUMER,
    "RISKS": _NO_CONSUMER,
    "UNCERTAINTIES": _NO_CONSUMER,
    "CONTRADICTIONS": _NO_CONSUMER,
    "RESEARCH_QUESTIONS": _NO_CONSUMER,
    "SUMMARY": _NO_CONSUMER,
    "ANCHOR_ASSOCIATIONS": _DETERMINISTIC_CONSUMER_PRESENT,
    "SCHEDULED_EVENTS": _PROMPT_ONLY,
    "REGIME_INPUTS": _PROMPT_ONLY,
    "TARGET_WEIGHTS": _NO_CONSUMER,
    "STRUCTURAL_THEMES": _PROMPT_ONLY,
    "TOP_FIVE_NEXT_WEEK": _PROMPT_ONLY,
    "EXTENDED_ETF_SLEEVE_FIELDS": _DETERMINISTIC_CONSUMER_PRESENT,
}

# H1 qualitative array fields projected one-for-one onto a coverage category.
_H1_ARRAY_CATEGORY_FIELDS: Final = {
    "EVIDENCE_OBSERVATIONS": "evidence_observations",
    "RISKS": "risks",
    "UNCERTAINTIES": "uncertainties",
    "CONTRADICTIONS": "contradictions",
    "RESEARCH_QUESTIONS": "research_questions",
}

# H1 declares these capability statuses as frozen contract constants.
_H1_CAPABILITY_STATUS: Final = {
    "ANCHOR_ASSOCIATIONS": ("anchor_associations_status", "UNAVAILABLE"),
    "SCHEDULED_EVENTS": ("scheduled_events_status", "UNAVAILABLE"),
    "REGIME_INPUTS": ("regime_inputs_status", "UNAVAILABLE"),
}
_H1_TARGET_WEIGHTS_ABSENCE_REASON: Final = (
    "POLICY_METHOD_HAS_NO_TARGET_WEIGHTS"
)

# Categories the legacy strict handoff contract never represents.
_LEGACY_NOT_REPRESENTED_CATEGORIES: Final = frozenset(
    {
        "EVIDENCE_OBSERVATIONS",
        "RISKS",
        "UNCERTAINTIES",
        "CONTRADICTIONS",
        "RESEARCH_QUESTIONS",
        "SUMMARY",
        "TARGET_WEIGHTS",
    }
)

# Legacy top-level containers projected one-for-one onto a coverage category.
_LEGACY_LIST_CATEGORY_FIELDS: Final = {
    "SCHEDULED_EVENTS": "scheduled_events",
    "STRUCTURAL_THEMES": "structural_themes_6_18m",
    "TOP_FIVE_NEXT_WEEK": "top5_next_week",
}

LIMITATION_REFERENCE_SYSTEMS: Final = (
    "H1_AND_LEGACY_REFERENCE_SYSTEMS_STRUCTURALLY_DISTINCT"
)
LIMITATION_TIER_B_REQUIRED: Final = "H1_SOURCE_CAPABILITY_GAPS_REQUIRE_TIER_B"
LIMITATION_IDENTIFIERS_UNNORMALIZED: Final = (
    "LEGACY_INSTRUMENT_IDENTIFIERS_COMPARED_WITHOUT_NORMALIZATION"
)
LIMITATION_SETTINGS_NOT_SOURCE_BOUND: Final = (
    "LEGACY_STRATEGY_SETTINGS_NOT_PROVEN_IDENTICAL_TO_H1_POLICY_SOURCE"
)
LIMITATION_STRICT_VALIDATOR_UNVERSIONED: Final = (
    "LEGACY_STRICT_VALIDATOR_HAS_NO_DECLARED_CONTRACT_VERSION"
)
LIMITATION_ROLE_MAPPING_UNDEFINED: Final = (
    "POLICY_ROLE_AND_LEGACY_ROLE_LAYER_MAPPING_UNDEFINED"
)
LIMITATION_TARGET_WEIGHTS_NOT_DERIVABLE: Final = (
    "TARGET_WEIGHTS_NOT_DERIVABLE_FROM_CURRENT_POLICY_METHOD"
)


class MmiLegacyStep1ComparisonReportV1Error(ValueError):
    """Raised when no valid report-only comparison report exists."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _fail(code: str) -> NoReturn:
    raise MmiLegacyStep1ComparisonReportV1Error(code)


def _snapshot_value(
    value: object,
    *,
    active_container_ids: set[int],
) -> object:
    if value is None or type(value) in {bool, int, float, str}:
        return value
    if isinstance(value, Mapping):
        container_id = id(value)
        if container_id in active_container_ids:
            _fail("MMI_LEGACY_STEP1_COMPARISON_INPUT_INVALID")
        active_container_ids.add(container_id)
        try:
            snapshot: dict[str, object] = {}
            try:
                keys = tuple(value.keys())
                if (
                    any(type(key) is not str for key in keys)
                    or len(keys) != len(set(keys))
                ):
                    _fail("MMI_LEGACY_STEP1_COMPARISON_INPUT_INVALID")
                for key in keys:
                    snapshot[key] = _snapshot_value(
                        value[key],
                        active_container_ids=active_container_ids,
                    )
            except MmiLegacyStep1ComparisonReportV1Error:
                raise
            except Exception:
                _fail("MMI_LEGACY_STEP1_COMPARISON_INPUT_INVALID")
            return snapshot
        finally:
            active_container_ids.remove(container_id)
    if type(value) is list:
        container_id = id(value)
        if container_id in active_container_ids:
            _fail("MMI_LEGACY_STEP1_COMPARISON_INPUT_INVALID")
        active_container_ids.add(container_id)
        try:
            return [
                _snapshot_value(
                    item,
                    active_container_ids=active_container_ids,
                )
                for item in value
            ]
        finally:
            active_container_ids.remove(container_id)
    _fail("MMI_LEGACY_STEP1_COMPARISON_INPUT_INVALID")


def _snapshot_mapping(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping):
        _fail("MMI_LEGACY_STEP1_COMPARISON_INPUT_INVALID")
    snapshot = _snapshot_value(value, active_container_ids=set())
    if type(snapshot) is not dict:
        _fail("MMI_LEGACY_STEP1_COMPARISON_INPUT_INVALID")
    return snapshot


def _legacy_canonical_bytes(value: object) -> bytes:
    """Encode a legacy payload with the repository's legacy canonical form."""
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError):
        _fail("MMI_LEGACY_STEP1_COMPARISON_INPUT_INVALID")


def _legacy_canonical_sha256(value: object) -> str:
    return hashlib.sha256(_legacy_canonical_bytes(value)).hexdigest()


def _validated_h1_candidate(
    *,
    legacy_step1_compatibility_candidate: Mapping[str, object],
    validated_grounded_analysis_response: Mapping[str, object],
    raw_response_envelope: Mapping[str, object],
    evidence_bundle: Mapping[str, object],
    policy_projection: Mapping[str, object],
    policy_source: object,
    portfolio_projection: Mapping[str, object] | None,
    portfolio_source: object,
    run_context: object,
) -> dict[str, object]:
    try:
        return validate_mmi_legacy_step1_compatibility_candidate_v1(
            value=legacy_step1_compatibility_candidate,
            validated_grounded_analysis_response=(
                validated_grounded_analysis_response
            ),
            raw_response_envelope=raw_response_envelope,
            evidence_bundle=evidence_bundle,
            policy_projection=policy_projection,
            policy_source=policy_source,
            portfolio_projection=portfolio_projection,
            portfolio_source=portfolio_source,
            run_context=run_context,
        )
    except MmiLegacyStep1CompatibilityCandidateV1Error:
        _fail("MMI_LEGACY_STEP1_COMPARISON_H1_CANDIDATE_INVALID")


def _h1_dict(value: object) -> dict[str, object]:
    if type(value) is not dict:
        _fail("MMI_LEGACY_STEP1_COMPARISON_H1_CANDIDATE_INVALID")
    return value


def _h1_list(value: object) -> list[object]:
    if type(value) is not list:
        _fail("MMI_LEGACY_STEP1_COMPARISON_H1_CANDIDATE_INVALID")
    return value


def _h1_string(value: object) -> str:
    if type(value) is not str:
        _fail("MMI_LEGACY_STEP1_COMPARISON_H1_CANDIDATE_INVALID")
    return value


def _decode_legacy_bytes(legacy_research_raw_bytes: object) -> str:
    """Decode exactly as the legacy path decodes a Step 1 output file.

    The legacy ingestion path reads through ``Path.read_text(encoding="utf-8")``
    which decodes strict UTF-8, does not strip a byte-order mark, and applies
    universal newline translation.  This reproduces that text behavior in
    memory without accepting a path or performing filesystem input.
    """
    if type(legacy_research_raw_bytes) is not bytes:
        _fail("MMI_LEGACY_STEP1_COMPARISON_INPUT_INVALID")
    if len(legacy_research_raw_bytes) > MAX_LEGACY_RESEARCH_RAW_BYTES:
        _fail("MMI_LEGACY_STEP1_COMPARISON_INPUT_INVALID")
    try:
        return io.TextIOWrapper(
            io.BytesIO(legacy_research_raw_bytes),
            encoding="utf-8",
            errors="strict",
            newline=None,
        ).read()
    except UnicodeDecodeError:
        _fail("MMI_LEGACY_STEP1_COMPARISON_INPUT_INVALID")


def _parse_legacy_payload(text: str) -> tuple[str, dict[str, object] | None]:
    """Classify the legacy artifact through the existing legacy parser."""
    try:
        payload = parse_research_output_text(text)
    except ResearchExtractionError:
        return _LEGACY_PARSE_FAILURE, None
    except ArtifactSchemaError:
        return _LEGACY_SCHEMA_FAILURE, None
    except ValueError:
        return _LEGACY_SCHEMA_FAILURE, None
    return _PARSED, _snapshot_mapping(payload)


def _self_reported_validation_passed(
    payload: dict[str, object] | None,
) -> bool | None:
    """Mirror ``validation_summary.passed`` without ever consulting it."""
    if payload is None:
        return None
    summary = payload.get("validation_summary")
    if not isinstance(summary, Mapping):
        return None
    passed = summary.get("passed")
    return passed if type(passed) is bool else None


def _legacy_instrument_view(
    candidate: dict[str, object] | None,
) -> tuple[list[str], list[str]] | None:
    """Read the legacy scorecard narrowly, or report it as unreadable.

    No case folding, whitespace stripping, or other normalization is applied,
    so a legacy identifier is either reported exactly as written or the whole
    legacy instrument view is declared unreadable.
    """
    if candidate is None:
        return None
    rows = candidate.get("buy_universe_scorecard")
    if type(rows) is not list:
        return None
    tickers: list[str] = []
    role_layers: list[str] = []
    for row in rows:
        if not isinstance(row, Mapping):
            return None
        ticker = row.get("ticker")
        if (
            type(ticker) is not str
            or not 1 <= len(ticker) <= MAX_LEGACY_TICKER_CHARACTERS
        ):
            return None
        tickers.append(ticker)
        if "role_layer" in row:
            role_layer = row["role_layer"]
            if (
                type(role_layer) is not str
                or not 1 <= len(role_layer) <= MAX_LEGACY_ROLE_LAYER_CHARACTERS
            ):
                return None
            role_layers.append(role_layer)
    return tickers, role_layers


def _unavailable_basis(raw_parse_status: str) -> str:
    if raw_parse_status == _LEGACY_PARSE_FAILURE:
        return _BASIS_UNAVAILABLE_PARSE_FAILURE
    if raw_parse_status == _LEGACY_SCHEMA_FAILURE:
        return _BASIS_UNAVAILABLE_SCHEMA_FAILURE
    return _BASIS_UNAVAILABLE_SCORECARD_SHAPE


def _instrument_comparison(
    *,
    h1_tickers: list[str],
    h1_roles: list[str],
    legacy_view: tuple[list[str], list[str]] | None,
    raw_parse_status: str,
    strict_handoff_status: str,
) -> dict[str, object]:
    h1_roles_present = sorted(set(h1_roles))
    if legacy_view is None:
        return {
            "comparison_basis": _unavailable_basis(raw_parse_status),
            "h1_instrument_count": len(h1_tickers),
            "h1_policy_roles_present": h1_roles_present,
            "legacy_instrument_count": None,
            "shared_instrument_count": None,
            "membership_equal": None,
            "shared_sequence_equal": None,
            "h1_only_tickers": None,
            "legacy_only_tickers": None,
            "legacy_duplicate_tickers": None,
            "legacy_role_layers_present": None,
        }

    legacy_tickers, legacy_role_layers = legacy_view
    if len(legacy_tickers) > MAX_COMPARED_INSTRUMENTS:
        _fail("MMI_LEGACY_STEP1_COMPARISON_RESOURCE_LIMIT_EXCEEDED")
    legacy_role_layers_present = sorted(set(legacy_role_layers))
    if len(legacy_role_layers_present) > MAX_LEGACY_ROLE_LAYERS_PRESENT:
        _fail("MMI_LEGACY_STEP1_COMPARISON_RESOURCE_LIMIT_EXCEEDED")

    h1_set = set(h1_tickers)
    legacy_set = set(legacy_tickers)
    shared = h1_set & legacy_set
    h1_only = sorted(h1_set - legacy_set)
    legacy_only = sorted(legacy_set - h1_set)
    duplicates = sorted(
        {
            ticker
            for ticker in legacy_set
            if legacy_tickers.count(ticker) > 1
        }
    )
    if (
        len(h1_only) > MAX_COMPARED_INSTRUMENTS
        or len(legacy_only) > MAX_COMPARED_INSTRUMENTS
        or len(duplicates) > MAX_COMPARED_INSTRUMENTS
    ):
        _fail("MMI_LEGACY_STEP1_COMPARISON_RESOURCE_LIMIT_EXCEEDED")

    return {
        "comparison_basis": (
            _BASIS_STRICT_VALID
            if strict_handoff_status == _STRICT_HANDOFF_VALID
            else _BASIS_STRUCTURALLY_READABLE
        ),
        "h1_instrument_count": len(h1_tickers),
        "h1_policy_roles_present": h1_roles_present,
        "legacy_instrument_count": len(legacy_tickers),
        "shared_instrument_count": len(shared),
        "membership_equal": h1_set == legacy_set,
        "shared_sequence_equal": (
            [ticker for ticker in h1_tickers if ticker in shared]
            == [ticker for ticker in legacy_tickers if ticker in shared]
        ),
        "h1_only_tickers": h1_only,
        "legacy_only_tickers": legacy_only,
        "legacy_duplicate_tickers": duplicates,
        "legacy_role_layers_present": legacy_role_layers_present,
    }


def _comparison_class(h1_status: str, legacy_status: str) -> str:
    """Total deterministic function of the two independent status values."""
    if legacy_status == _LEGACY_UNAVAILABLE:
        return _NOT_COMPARABLE
    h1_present = h1_status == _H1_PRESENT
    legacy_present = legacy_status == _LEGACY_PRESENT
    if h1_present and legacy_present:
        return _AVAILABLE_IN_BOTH
    if h1_present:
        return _AVAILABLE_ONLY_IN_H1
    if legacy_present:
        return _AVAILABLE_ONLY_IN_LEGACY
    return _AVAILABLE_IN_NEITHER


def _h1_coverage(
    *,
    category: str,
    candidate: dict[str, object],
    assessments: list[dict[str, object]],
) -> tuple[str, int | None]:
    if category in _H1_CAPABILITY_STATUS:
        field, expected = _H1_CAPABILITY_STATUS[category]
        statuses = _h1_dict(candidate.get("source_capability_statuses"))
        if _h1_string(statuses.get(field)) != expected:
            _fail("MMI_LEGACY_STEP1_COMPARISON_H1_CANDIDATE_INVALID")
        return _H1_TIER_A_UNAVAILABLE, None
    if category == "TARGET_WEIGHTS":
        statuses = _h1_dict(candidate.get("source_capability_statuses"))
        reason = _h1_string(statuses.get("target_weights_absence_reason"))
        if reason != _H1_TARGET_WEIGHTS_ABSENCE_REASON:
            _fail("MMI_LEGACY_STEP1_COMPARISON_H1_CANDIDATE_INVALID")
        return _H1_POLICY_METHOD_ABSENT, None
    if category in _H1_ARRAY_CATEGORY_FIELDS:
        count = len(_h1_list(candidate.get(_H1_ARRAY_CATEGORY_FIELDS[category])))
        return (_H1_PRESENT if count else _H1_ABSENT), count
    if category == "SUMMARY":
        _h1_dict(candidate.get("summary"))
        return _H1_PRESENT, 1
    if category == "INSTRUMENT_RATIONALE":
        count = sum(
            1
            for row in assessments
            if type(row.get("rationale_12m_plus")) is str
        )
        return (_H1_PRESENT if count else _H1_ABSENT), count
    if category == "INSTRUMENT_REFERENCES":
        count = sum(
            1 for row in assessments if _h1_list(row.get("references"))
        )
        return (_H1_PRESENT if count else _H1_ABSENT), count
    return _H1_NOT_REPRESENTED, None


def _legacy_container_coverage(
    *,
    candidate: dict[str, object],
    field: str,
    container: type,
) -> tuple[str, int | None]:
    if field not in candidate:
        return _LEGACY_ABSENT, 0
    value = candidate[field]
    if container is list:
        if type(value) is not list:
            return _LEGACY_UNAVAILABLE, None
        return (_LEGACY_PRESENT if value else _LEGACY_ABSENT), len(value)
    if not isinstance(value, Mapping):
        return _LEGACY_UNAVAILABLE, None
    return (_LEGACY_PRESENT if value else _LEGACY_ABSENT), len(value)


def _legacy_scorecard_coverage(
    *,
    category: str,
    rows: list[Mapping[str, object]],
) -> tuple[str, int | None]:
    if category == "INSTRUMENT_RATIONALE":
        count = sum(
            1
            for row in rows
            if type(row.get("thesis_12m_plus_summary")) is str
            and row["thesis_12m_plus_summary"] != ""
        )
    elif category == "INSTRUMENT_REFERENCES":
        count = sum(
            1
            for row in rows
            if (
                type(row.get("event_id_refs")) is list
                and row["event_id_refs"]
            )
            or (
                type(row.get("structural_theme_refs")) is list
                and row["structural_theme_refs"]
            )
        )
    else:
        count = sum(
            1
            for row in rows
            if type(row.get("primary_anchor_event_id")) is str
            and row["primary_anchor_event_id"] != ""
        )
    return (_LEGACY_PRESENT if count else _LEGACY_ABSENT), count


def _legacy_coverage(
    *,
    category: str,
    candidate: dict[str, object] | None,
    scorecard_readable: bool,
) -> tuple[str, int | None]:
    if category in _LEGACY_NOT_REPRESENTED_CATEGORIES:
        return _LEGACY_NOT_REPRESENTED, None
    if candidate is None:
        return _LEGACY_UNAVAILABLE, None
    if category in {
        "INSTRUMENT_RATIONALE",
        "INSTRUMENT_REFERENCES",
        "ANCHOR_ASSOCIATIONS",
    }:
        if not scorecard_readable:
            return _LEGACY_UNAVAILABLE, None
        rows = [
            row
            for row in _h1_list(candidate.get("buy_universe_scorecard"))
            if isinstance(row, Mapping)
        ]
        return _legacy_scorecard_coverage(category=category, rows=rows)
    if category in _LEGACY_LIST_CATEGORY_FIELDS:
        return _legacy_container_coverage(
            candidate=candidate,
            field=_LEGACY_LIST_CATEGORY_FIELDS[category],
            container=list,
        )
    if category == "REGIME_INPUTS":
        return _legacy_container_coverage(
            candidate=candidate,
            field="regime_inputs",
            container=dict,
        )
    return _legacy_container_coverage(
        candidate=candidate,
        field="optional_extended_etf_sleeve",
        container=dict,
    )


def _coverage_comparison(
    *,
    candidate: dict[str, object],
    assessments: list[dict[str, object]],
    legacy_candidate: dict[str, object] | None,
    scorecard_readable: bool,
) -> list[object]:
    rows: list[object] = []
    for category in COVERAGE_CATEGORIES:
        h1_status, h1_count = _h1_coverage(
            category=category,
            candidate=candidate,
            assessments=assessments,
        )
        legacy_status, legacy_count = _legacy_coverage(
            category=category,
            candidate=legacy_candidate,
            scorecard_readable=scorecard_readable,
        )
        rows.append(
            {
                "category": category,
                "h1_status": h1_status,
                "legacy_status": legacy_status,
                "comparison_class": _comparison_class(
                    h1_status,
                    legacy_status,
                ),
                "h1_count": h1_count,
                "legacy_count": legacy_count,
                "legacy_consumer_class": _LEGACY_CONSUMER_CLASS[category],
            }
        )
    return rows


def _limitations(
    *,
    coverage_rows: list[object],
    instrument_comparison: dict[str, object],
    strict_handoff_status: str,
) -> list[str]:
    by_category = {
        row["category"]: row
        for row in coverage_rows
        if type(row) is dict
    }
    codes: set[str] = {LIMITATION_SETTINGS_NOT_SOURCE_BOUND}
    if any(
        type(row) is dict and row["h1_status"] == _H1_TIER_A_UNAVAILABLE
        for row in coverage_rows
    ):
        codes.add(LIMITATION_TIER_B_REQUIRED)
    if by_category["TARGET_WEIGHTS"]["h1_status"] == _H1_POLICY_METHOD_ABSENT:
        codes.add(LIMITATION_TARGET_WEIGHTS_NOT_DERIVABLE)
    if (
        by_category["INSTRUMENT_REFERENCES"]["comparison_class"]
        == _AVAILABLE_IN_BOTH
    ):
        codes.add(LIMITATION_REFERENCE_SYSTEMS)
    if instrument_comparison["comparison_basis"] in _READABLE_BASES:
        codes.add(LIMITATION_IDENTIFIERS_UNNORMALIZED)
        codes.add(LIMITATION_ROLE_MAPPING_UNDEFINED)
    if strict_handoff_status != _NOT_EVALUATED:
        codes.add(LIMITATION_STRICT_VALIDATOR_UNVERSIONED)
    return sorted(codes)


def _comparison_summary(
    *,
    coverage_rows: list[object],
    limitations: list[str],
) -> dict[str, object]:
    classes = [
        row["comparison_class"] for row in coverage_rows if type(row) is dict
    ]
    return {
        "coverage_available_in_both_count": classes.count(_AVAILABLE_IN_BOTH),
        "coverage_only_in_one_count": (
            classes.count(_AVAILABLE_ONLY_IN_H1)
            + classes.count(_AVAILABLE_ONLY_IN_LEGACY)
        ),
        "coverage_not_comparable_count": classes.count(_NOT_COMPARABLE),
        "limitation_count": len(limitations),
    }


def _validate_report_canonical_size(value: object) -> None:
    try:
        canonical_json_bytes(
            value,
            maximum_bytes=(
                MAX_MMI_LEGACY_STEP1_COMPARISON_REPORT_V1_CANONICAL_BYTES
            ),
        )
    except MmiCanonicalizationError:
        _fail("MMI_LEGACY_STEP1_COMPARISON_RESOURCE_LIMIT_EXCEEDED")


def _report_identity(report: dict[str, object]) -> str:
    if set(report) != _TOP_LEVEL_FIELDS:
        _fail("MMI_LEGACY_STEP1_COMPARISON_SCHEMA_INVALID")
    try:
        return record_identity_sha256(
            report,
            identity_field=_IDENTITY_FIELD,
            domain=_MMI_LEGACY_STEP1_COMPARISON_REPORT_V1_IDENTITY_DOMAIN,
            maximum_bytes=(
                MAX_MMI_LEGACY_STEP1_COMPARISON_REPORT_V1_CANONICAL_BYTES
            ),
        )
    except MmiCanonicalizationError:
        _fail("MMI_LEGACY_STEP1_COMPARISON_IDENTITY_MISMATCHED")


def _validate_report_schema(report: dict[str, object]) -> None:
    try:
        validate_artifact_schema(report, schema_name=_SCHEMA_NAME)
    except Exception:
        _fail("MMI_LEGACY_STEP1_COMPARISON_SCHEMA_INVALID")


def _build_mmi_legacy_step1_comparison_report_v1_from_validated_h1_candidate(
    *,
    validated_h1_candidate: dict[str, object],
    legacy_research_raw_bytes: bytes,
    legacy_strategy_settings: dict[str, object],
) -> dict[str, object]:
    settings_snapshot = legacy_strategy_settings

    candidate = validated_h1_candidate

    settings_canonical = _legacy_canonical_bytes(settings_snapshot)
    if len(settings_canonical) > MAX_LEGACY_STRATEGY_SETTINGS_CANONICAL_BYTES:
        _fail("MMI_LEGACY_STEP1_COMPARISON_INPUT_INVALID")

    text = _decode_legacy_bytes(legacy_research_raw_bytes)
    raw_bytes_sha256 = hashlib.sha256(legacy_research_raw_bytes).hexdigest()
    raw_parse_status, legacy_payload = _parse_legacy_payload(text)

    legacy_candidate: dict[str, object] | None = None
    legacy_source_shape: str | None = None
    strict_handoff_status = _NOT_EVALUATED
    strict_handoff_blocker_count: int | None = None
    if legacy_payload is not None:
        normalization = normalize_research_handoff_candidate(
            legacy_payload,
            strategy_settings=settings_snapshot,
        )
        legacy_candidate = _snapshot_mapping(normalization.candidate)
        legacy_source_shape = normalization.source_shape
        strict = validate_research_handoff(
            normalization.candidate,
            strategy_settings=settings_snapshot,
        )
        strict_handoff_blocker_count = len(strict.blocker_reasons)
        strict_handoff_status = (
            _STRICT_HANDOFF_VALID
            if strict.valid
            else _LEGACY_HANDOFF_CONTRACT_FAILURE
        )

    assessments = [
        _h1_dict(row)
        for row in _h1_list(candidate.get("ordered_instrument_assessments"))
    ]
    h1_tickers = [_h1_string(row.get("ticker")) for row in assessments]
    h1_roles = [_h1_string(row.get("policy_role")) for row in assessments]

    legacy_view = _legacy_instrument_view(legacy_candidate)
    instrument_comparison = _instrument_comparison(
        h1_tickers=h1_tickers,
        h1_roles=h1_roles,
        legacy_view=legacy_view,
        raw_parse_status=raw_parse_status,
        strict_handoff_status=strict_handoff_status,
    )
    coverage_rows = _coverage_comparison(
        candidate=candidate,
        assessments=assessments,
        legacy_candidate=legacy_candidate,
        scorecard_readable=legacy_view is not None,
    )
    limitations = _limitations(
        coverage_rows=coverage_rows,
        instrument_comparison=instrument_comparison,
        strict_handoff_status=strict_handoff_status,
    )

    report: dict[str, object] = {
        "schema_version": _SCHEMA_VERSION,
        "artifact_kind": _ARTIFACT_KIND,
        "comparison_contract_version": _COMPARISON_CONTRACT_VERSION,
        "report_only": True,
        "authority_effect": AUTHORITY_EFFECT_NONE,
        "provenance": {
            _H1_IDENTITY_FIELD: _h1_string(candidate.get(_H1_IDENTITY_FIELD)),
            "legacy_raw_bytes_sha256": raw_bytes_sha256,
            "legacy_parsed_payload_canonical_sha256": (
                None
                if legacy_payload is None
                else _legacy_canonical_sha256(legacy_payload)
            ),
            "legacy_normalized_candidate_canonical_sha256": (
                None
                if legacy_candidate is None
                else _legacy_canonical_sha256(legacy_candidate)
            ),
            "legacy_strategy_settings_canonical_sha256": (
                hashlib.sha256(settings_canonical).hexdigest()
            ),
        },
        "legacy_contract_status": {
            "raw_parse_status": raw_parse_status,
            "strict_handoff_status": strict_handoff_status,
            "strict_handoff_blocker_count": strict_handoff_blocker_count,
            "legacy_source_shape": legacy_source_shape,
            "legacy_self_reported_validation_passed": (
                _self_reported_validation_passed(legacy_payload)
            ),
        },
        "instrument_comparison": instrument_comparison,
        "coverage_comparison": coverage_rows,
        "limitations": limitations,
        "comparison_summary": _comparison_summary(
            coverage_rows=coverage_rows,
            limitations=limitations,
        ),
        _IDENTITY_FIELD: _ZERO_SHA256,
    }
    _validate_report_canonical_size(report)
    report[_IDENTITY_FIELD] = _report_identity(report)
    _validate_report_schema(report)
    return report


def _validate_mmi_legacy_step1_comparison_report_v1_from_validated_h1_candidate(
    *,
    value: Mapping[str, object],
    validated_h1_candidate: dict[str, object],
    legacy_research_raw_bytes: bytes,
    legacy_strategy_settings: dict[str, object],
) -> dict[str, object]:
    report = _snapshot_mapping(value)
    if (
        report.get("schema_version") != _SCHEMA_VERSION
        or report.get("artifact_kind") != _ARTIFACT_KIND
        or report.get("comparison_contract_version")
        != _COMPARISON_CONTRACT_VERSION
    ):
        _fail("MMI_LEGACY_STEP1_COMPARISON_CONTRACT_UNSUPPORTED")
    _validate_report_schema(report)
    _validate_report_canonical_size(report)
    if report.get(_IDENTITY_FIELD) != _report_identity(report):
        _fail("MMI_LEGACY_STEP1_COMPARISON_IDENTITY_MISMATCHED")
    expected = _build_mmi_legacy_step1_comparison_report_v1_from_validated_h1_candidate(
        validated_h1_candidate=validated_h1_candidate,
        legacy_research_raw_bytes=legacy_research_raw_bytes,
        legacy_strategy_settings=legacy_strategy_settings,
    )
    if report != expected:
        _fail("MMI_LEGACY_STEP1_COMPARISON_NON_EXPECTED")
    return report


def build_mmi_legacy_step1_comparison_report_v1(
    *,
    legacy_step1_compatibility_candidate: Mapping[str, object],
    validated_grounded_analysis_response: Mapping[str, object],
    raw_response_envelope: Mapping[str, object],
    evidence_bundle: Mapping[str, object],
    policy_projection: Mapping[str, object],
    policy_source: object,
    portfolio_projection: Mapping[str, object] | None,
    portfolio_source: object,
    run_context: object,
    legacy_research_raw_bytes: bytes,
    legacy_strategy_settings: Mapping[str, object],
) -> dict[str, object]:
    """Build one report-only comparison of H1 against one legacy artifact."""
    settings_snapshot = _snapshot_mapping(legacy_strategy_settings)
    validated_h1 = _validated_h1_candidate(
        legacy_step1_compatibility_candidate=_snapshot_mapping(
            legacy_step1_compatibility_candidate
        ),
        validated_grounded_analysis_response=_snapshot_mapping(
            validated_grounded_analysis_response
        ),
        raw_response_envelope=_snapshot_mapping(raw_response_envelope),
        evidence_bundle=_snapshot_mapping(evidence_bundle),
        policy_projection=_snapshot_mapping(policy_projection),
        policy_source=policy_source,
        portfolio_projection=(
            None
            if portfolio_projection is None
            else _snapshot_mapping(portfolio_projection)
        ),
        portfolio_source=portfolio_source,
        run_context=run_context,
    )
    return _build_mmi_legacy_step1_comparison_report_v1_from_validated_h1_candidate(
        validated_h1_candidate=validated_h1,
        legacy_research_raw_bytes=legacy_research_raw_bytes,
        legacy_strategy_settings=settings_snapshot,
    )


def validate_mmi_legacy_step1_comparison_report_v1(
    *,
    value: Mapping[str, object],
    legacy_step1_compatibility_candidate: Mapping[str, object],
    validated_grounded_analysis_response: Mapping[str, object],
    raw_response_envelope: Mapping[str, object],
    evidence_bundle: Mapping[str, object],
    policy_projection: Mapping[str, object],
    policy_source: object,
    portfolio_projection: Mapping[str, object] | None,
    portfolio_source: object,
    run_context: object,
    legacy_research_raw_bytes: bytes,
    legacy_strategy_settings: Mapping[str, object],
) -> dict[str, object]:
    """Return one stable report equal to the source-bound expected report."""
    report = _snapshot_mapping(value)
    if (
        report.get("schema_version") != _SCHEMA_VERSION
        or report.get("artifact_kind") != _ARTIFACT_KIND
        or report.get("comparison_contract_version")
        != _COMPARISON_CONTRACT_VERSION
    ):
        _fail("MMI_LEGACY_STEP1_COMPARISON_CONTRACT_UNSUPPORTED")
    _validate_report_schema(report)
    _validate_report_canonical_size(report)
    if report.get(_IDENTITY_FIELD) != _report_identity(report):
        _fail("MMI_LEGACY_STEP1_COMPARISON_IDENTITY_MISMATCHED")
    settings_snapshot = _snapshot_mapping(legacy_strategy_settings)
    validated_h1 = _validated_h1_candidate(
        legacy_step1_compatibility_candidate=_snapshot_mapping(
            legacy_step1_compatibility_candidate
        ),
        validated_grounded_analysis_response=_snapshot_mapping(
            validated_grounded_analysis_response
        ),
        raw_response_envelope=_snapshot_mapping(raw_response_envelope),
        evidence_bundle=_snapshot_mapping(evidence_bundle),
        policy_projection=_snapshot_mapping(policy_projection),
        policy_source=policy_source,
        portfolio_projection=(
            None
            if portfolio_projection is None
            else _snapshot_mapping(portfolio_projection)
        ),
        portfolio_source=portfolio_source,
        run_context=run_context,
    )
    return _validate_mmi_legacy_step1_comparison_report_v1_from_validated_h1_candidate(
        value=value,
        validated_h1_candidate=validated_h1,
        legacy_research_raw_bytes=legacy_research_raw_bytes,
        legacy_strategy_settings=settings_snapshot,
    )
