"""Strict strategy-settings parser and report-only MMI policy projection."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import re
from typing import Final
from zoneinfo import ZoneInfo

import yaml

from investment_orchestrator.common.schema_validation import (
    validate_artifact_schema,
)
from investment_orchestrator.mmi.canonical import (
    MMI_POLICY_PROJECTION_IDENTITY_DOMAIN,
    MMI_SOURCE_RECORD_IDENTITY_DOMAIN,
    MMI_UNIVERSE_PROJECTION_IDENTITY_DOMAIN,
    MmiCanonicalizationError,
    normalize_decimal_string,
    record_identity_sha256,
)
from investment_orchestrator.mmi.contracts import (
    AUTHORITY_EFFECT_NONE,
    CANONICAL_UTC_TIMESTAMP_FORMAT,
    MMI_SOURCE_CATALOG,
    MmiCapturedSource,
    MmiPolicyProjectionBuildResult,
    MmiPolicyProjectionValidationResult,
    MmiProjectionResultCategory,
    MmiProjectionRunContext,
    MmiSourceRole,
    _mmi_captured_source_provenance_is_valid,
    _mmi_projection_run_context_provenance_is_valid,
)


POLICY_METHOD: Final = "BUDGET_SHORTLIST_ROTATION_WITHOUT_TARGET_WEIGHTS"
MAXIMUM_YAML_DEPTH: Final = 16
MAXIMUM_YAML_NODES: Final = 4_096
MAXIMUM_SETTINGS_BYTES: Final = 262_144
MAXIMUM_UNIVERSE_MEMBERS: Final = 256
MAXIMUM_POLICY_CANONICAL_BYTES: Final = 524_288
MAXIMUM_UNIVERSE_CANONICAL_BYTES: Final = 131_072
MAXIMUM_SOURCE_RECORD_CANONICAL_BYTES: Final = 8_192
MAXIMUM_YAML_STRING_LENGTH: Final = 16_384

_DATE_RE: Final = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_SOURCE_TIMESTAMP_RE: Final = re.compile(
    r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2} ET$"
)
_TICKER_RE: Final = re.compile(r"^[A-Z][A-Z0-9.-]{0,15}$")
_THEME_RE: Final = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_YAML_DECIMAL_RE: Final = re.compile(
    r"^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$"
)
_YAML_BOOLEAN_RE: Final = re.compile(r"^(?:true|false)$")
_YAML_INTEGER_RE: Final = re.compile(r"^-?(?:0|[1-9][0-9]*)$")
_YAML_FIXED_POINT_RE: Final = re.compile(
    r"^-?(?:0|[1-9][0-9]*)\.[0-9]+$"
)
_YAML_NONFINITE_RE: Final = re.compile(
    r"^(?:[-+]?\.inf|\.nan)$",
    re.IGNORECASE,
)
_STANDARD_YAML_TAGS: Final = frozenset(
    {
        "tag:yaml.org,2002:bool",
        "tag:yaml.org,2002:int",
        "tag:yaml.org,2002:float",
        "tag:yaml.org,2002:str",
        "tag:yaml.org,2002:seq",
        "tag:yaml.org,2002:map",
    }
)
_SHORTLIST_KEYS: Final = (
    "benchmark_carrier",
    "diversified_core_buffer_max",
    "sector_alpha_tilt_max",
    "extended_etf_minority_sleeve_max",
)
_ROTATION_GUARDRAIL_KEYS: Final = (
    "require_same_role_for_rotation",
    "min_score_gap_to_rotate",
    "do_not_rotate_if_current_holding_still_role_valid",
    "no_rotation_on_one_rank_change_only",
)
_COMPLETENESS_STATUSES: Final = tuple(
    sorted(
        (
            "DETERMINISTIC_UNIVERSE_VALIDATED",
            "HARD_OPEN_ORDERS_BUDGET_CAP_VALIDATED",
            "ROTATION_POLICY_VALIDATED",
            "SHORTLIST_RULES_VALIDATED",
            "TARGET_WEIGHTS_ABSENT_BY_METHOD",
        )
    )
)
_ALWAYS_UNAVAILABLE_POLICY_GAPS: Final = (
    (
        "POLICY_CASH_MODEL_UNAVAILABLE",
        "CASH_AND_DEPLOYMENT",
    ),
    (
        "POLICY_PORTFOLIO_NAV_CONCENTRATION_UNAVAILABLE",
        "CONCENTRATION",
    ),
    (
        "POLICY_LOOKTHROUGH_EXPOSURE_UNAVAILABLE",
        "LOOKTHROUGH_EXPOSURE",
    ),
    (
        "POLICY_TURNOVER_ENFORCEMENT_INCOMPLETE",
        "TURNOVER",
    ),
    (
        "POLICY_MINIMUM_HOLDING_ENFORCEMENT_INCOMPLETE",
        "MINIMUM_HOLDING",
    ),
    (
        "POLICY_TAX_LOT_ENFORCEMENT_UNAVAILABLE",
        "TAX_LOTS",
    ),
    (
        "POLICY_SELL_ELIGIBILITY_INCOMPLETE",
        "SELL_ELIGIBILITY",
    ),
)


class _ProjectionBlocked(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class _ProjectionContractFailure(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class _StrictYamlError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class _StrictMmiLoader(yaml.SafeLoader):
    """Safe constructor set with an MMI-owned plain-scalar grammar."""


def _construct_strict_mapping(
    loader: _StrictMmiLoader,
    node: yaml.nodes.MappingNode,
    deep: bool = False,
) -> dict[str, object]:
    if not isinstance(node, yaml.nodes.MappingNode):
        raise _StrictYamlError("MMI_POLICY_YAML_MAPPING_INVALID")
    result: dict[str, object] = {}
    for key_node, value_node in node.value:
        if key_node.value == "<<" or key_node.tag == "tag:yaml.org,2002:merge":
            raise _StrictYamlError("MMI_POLICY_YAML_MERGE_KEY_PROHIBITED")
        key = loader.construct_object(key_node, deep=deep)
        if type(key) is not str:
            raise _StrictYamlError("MMI_POLICY_YAML_MAPPING_KEY_INVALID")
        if key in result:
            raise _StrictYamlError("MMI_POLICY_YAML_DUPLICATE_KEY")
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


def _construct_strict_decimal(
    loader: _StrictMmiLoader,
    node: yaml.nodes.ScalarNode,
) -> Decimal:
    raw = loader.construct_scalar(node)
    if raw.casefold() in {
        ".inf",
        "+.inf",
        "-.inf",
        ".nan",
    }:
        raise _StrictYamlError("MMI_POLICY_YAML_NONFINITE_NUMBER")
    if not _YAML_DECIMAL_RE.fullmatch(raw):
        raise _StrictYamlError("MMI_POLICY_YAML_NUMERIC_INVALID")
    try:
        value = Decimal(raw)
    except InvalidOperation:
        raise _StrictYamlError("MMI_POLICY_YAML_NUMERIC_INVALID") from None
    if not value.is_finite():
        raise _StrictYamlError("MMI_POLICY_YAML_NONFINITE_NUMBER")
    return value


def _construct_strict_boolean(
    loader: _StrictMmiLoader,
    node: yaml.nodes.ScalarNode,
) -> bool:
    raw = loader.construct_scalar(node)
    if raw == "true":
        return True
    if raw == "false":
        return False
    raise _StrictYamlError("MMI_POLICY_YAML_BOOLEAN_INVALID")


def _construct_strict_integer(
    loader: _StrictMmiLoader,
    node: yaml.nodes.ScalarNode,
) -> int:
    raw = loader.construct_scalar(node)
    if not _YAML_INTEGER_RE.fullmatch(raw):
        raise _StrictYamlError("MMI_POLICY_YAML_NUMERIC_INVALID")
    try:
        return int(raw, 10)
    except ValueError:
        raise _StrictYamlError("MMI_POLICY_YAML_NUMERIC_INVALID") from None


_StrictMmiLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_strict_mapping,
)
_StrictMmiLoader.add_constructor(
    "tag:yaml.org,2002:bool",
    _construct_strict_boolean,
)
_StrictMmiLoader.add_constructor(
    "tag:yaml.org,2002:int",
    _construct_strict_integer,
)
_StrictMmiLoader.add_constructor(
    "tag:yaml.org,2002:float",
    _construct_strict_decimal,
)
_StrictMmiLoader.yaml_implicit_resolvers = {}
_StrictMmiLoader.add_implicit_resolver(
    "tag:yaml.org,2002:bool",
    _YAML_BOOLEAN_RE,
    ("t", "f"),
)
_StrictMmiLoader.add_implicit_resolver(
    "tag:yaml.org,2002:int",
    _YAML_INTEGER_RE,
    tuple("-0123456789"),
)
_StrictMmiLoader.add_implicit_resolver(
    "tag:yaml.org,2002:float",
    _YAML_FIXED_POINT_RE,
    tuple("-0123456789"),
)
_StrictMmiLoader.add_implicit_resolver(
    "tag:yaml.org,2002:float",
    _YAML_NONFINITE_RE,
    tuple("-+."),
)


def _build_result(
    status: MmiProjectionResultCategory,
    *reason_codes: str,
    projection: Mapping[str, object] | None = None,
) -> MmiPolicyProjectionBuildResult:
    return MmiPolicyProjectionBuildResult(
        status=status,
        authority_effect=AUTHORITY_EFFECT_NONE,
        reason_codes=tuple(reason_codes),
        projection=projection,
    )


def _validation_result(
    status: MmiProjectionResultCategory,
    *reason_codes: str,
) -> MmiPolicyProjectionValidationResult:
    return MmiPolicyProjectionValidationResult(
        status=status,
        authority_effect=AUTHORITY_EFFECT_NONE,
        reason_codes=tuple(reason_codes),
    )


def _inspect_yaml_events(text: str) -> None:
    document_count = 0
    depth = 0
    nodes = 0
    try:
        events = yaml.parse(text, Loader=_StrictMmiLoader)
        for event in events:
            if isinstance(event, yaml.events.DocumentStartEvent):
                document_count += 1
                if document_count > 1:
                    raise _StrictYamlError(
                        "MMI_POLICY_YAML_MULTIPLE_DOCUMENTS"
                    )
            if isinstance(event, yaml.events.AliasEvent):
                raise _StrictYamlError("MMI_POLICY_YAML_ALIAS_PROHIBITED")
            if isinstance(
                event,
                (
                    yaml.events.ScalarEvent,
                    yaml.events.SequenceStartEvent,
                    yaml.events.MappingStartEvent,
                ),
            ):
                nodes += 1
                if nodes > MAXIMUM_YAML_NODES:
                    raise _StrictYamlError(
                        "MMI_POLICY_YAML_NODE_COUNT_EXCEEDED"
                    )
                if getattr(event, "anchor", None) is not None:
                    raise _StrictYamlError(
                        "MMI_POLICY_YAML_ANCHOR_PROHIBITED"
                    )
                tag = getattr(event, "tag", None)
                if tag is not None and tag not in _STANDARD_YAML_TAGS:
                    raise _StrictYamlError("MMI_POLICY_YAML_TAG_PROHIBITED")
            if isinstance(
                event,
                (yaml.events.SequenceStartEvent, yaml.events.MappingStartEvent),
            ):
                depth += 1
                if depth > MAXIMUM_YAML_DEPTH:
                    raise _StrictYamlError(
                        "MMI_POLICY_YAML_DEPTH_EXCEEDED"
                    )
            elif isinstance(
                event,
                (yaml.events.SequenceEndEvent, yaml.events.MappingEndEvent),
            ):
                depth -= 1
    except _StrictYamlError:
        raise
    except yaml.YAMLError:
        raise _StrictYamlError("MMI_POLICY_YAML_PARSE_FAILED") from None
    if document_count != 1 or depth != 0:
        raise _StrictYamlError("MMI_POLICY_YAML_PARSE_FAILED")


def _validate_yaml_value(
    value: object,
    *,
    depth: int,
    node_counter: list[int],
) -> None:
    if depth > MAXIMUM_YAML_DEPTH:
        raise _StrictYamlError("MMI_POLICY_YAML_DEPTH_EXCEEDED")
    node_counter[0] += 1
    if node_counter[0] > MAXIMUM_YAML_NODES:
        raise _StrictYamlError("MMI_POLICY_YAML_NODE_COUNT_EXCEEDED")
    if value is None:
        raise _StrictYamlError("MMI_POLICY_YAML_NULL_PROHIBITED")
    if type(value) in {bool, int}:
        return
    if type(value) is Decimal:
        if not value.is_finite():
            raise _StrictYamlError("MMI_POLICY_YAML_NONFINITE_NUMBER")
        return
    if type(value) is str:
        if len(value) > MAXIMUM_YAML_STRING_LENGTH:
            raise _StrictYamlError("MMI_POLICY_YAML_STRING_TOO_LONG")
        return
    if type(value) is list:
        for item in value:
            _validate_yaml_value(
                item,
                depth=depth + 1,
                node_counter=node_counter,
            )
        return
    if type(value) is dict:
        for key, item in value.items():
            if type(key) is not str:
                raise _StrictYamlError(
                    "MMI_POLICY_YAML_MAPPING_KEY_INVALID"
                )
            if len(key) > 128:
                raise _StrictYamlError(
                    "MMI_POLICY_YAML_MAPPING_KEY_TOO_LONG"
                )
            _validate_yaml_value(
                item,
                depth=depth + 1,
                node_counter=node_counter,
            )
        return
    if type(value) is float:
        raise _StrictYamlError("MMI_POLICY_YAML_BINARY_FLOAT_PROHIBITED")
    raise _StrictYamlError("MMI_POLICY_YAML_UNSUPPORTED_SCALAR")


def _parse_strict_strategy_settings(raw_bytes: bytes) -> dict[str, object]:
    if type(raw_bytes) is not bytes:
        raise _ProjectionBlocked("MMI_POLICY_SOURCE_BYTES_INVALID")
    if len(raw_bytes) > MAXIMUM_SETTINGS_BYTES:
        raise _ProjectionBlocked("MMI_POLICY_SOURCE_OVERSIZED")
    if raw_bytes.startswith(b"\xef\xbb\xbf"):
        raise _ProjectionBlocked("MMI_POLICY_SOURCE_BOM_PROHIBITED")
    try:
        text = raw_bytes.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        raise _ProjectionBlocked("MMI_POLICY_SOURCE_UTF8_INVALID") from None
    try:
        _inspect_yaml_events(text)
        payload = yaml.load(text, Loader=_StrictMmiLoader)
        _validate_yaml_value(payload, depth=0, node_counter=[0])
    except _StrictYamlError as exc:
        raise _ProjectionBlocked(exc.code) from None
    except yaml.YAMLError:
        raise _ProjectionBlocked("MMI_POLICY_YAML_PARSE_FAILED") from None
    if type(payload) is not dict:
        raise _ProjectionBlocked("MMI_POLICY_YAML_ROOT_INVALID")
    return payload


def _validate_run_context(run_context: MmiProjectionRunContext) -> None:
    if not _mmi_projection_run_context_provenance_is_valid(run_context):
        raise _ProjectionContractFailure(
            "MMI_PROJECTION_RUN_CONTEXT_PROVENANCE_INVALID"
        )
    observed = run_context.evaluation_time_utc
    if (
        type(observed) is not datetime
        or observed.tzinfo is None
        or observed.utcoffset() != timedelta(0)
        or run_context.authority_effect != AUTHORITY_EFFECT_NONE
        or observed.astimezone(timezone.utc).strftime(
            CANONICAL_UTC_TIMESTAMP_FORMAT
        )
        != run_context.evaluation_timestamp_utc
    ):
        raise _ProjectionContractFailure(
            "MMI_PROJECTION_RUN_CONTEXT_PROVENANCE_INVALID"
        )


def _validate_captured_source(source: MmiCapturedSource) -> dict[str, object]:
    if not _mmi_captured_source_provenance_is_valid(source):
        raise _ProjectionContractFailure(
            "MMI_POLICY_CAPTURE_PROVENANCE_INVALID"
        )
    if source.role is not MmiSourceRole.STRATEGY_SETTINGS:
        raise _ProjectionContractFailure(
            "MMI_POLICY_CAPTURE_ROLE_INVALID"
        )
    if type(source.raw_bytes) is not bytes:
        raise _ProjectionBlocked("MMI_POLICY_SOURCE_BYTES_INVALID")
    try:
        record = dict(source.source_record)
    except (TypeError, ValueError):
        raise _ProjectionBlocked("MMI_POLICY_SOURCE_RECORD_INVALID") from None
    try:
        validate_artifact_schema(
            record,
            schema_name="mmi_source_record_v1.schema.json",
        )
    except Exception:
        raise _ProjectionBlocked("MMI_POLICY_SOURCE_RECORD_SCHEMA_INVALID") from None
    spec = MMI_SOURCE_CATALOG[MmiSourceRole.STRATEGY_SETTINGS]
    if (
        record.get("source_role") != source.role.value
        or record.get("source_id") != spec.source_id
        or record.get("repository_relative_locator")
        != str(spec.repository_relative_locator)
        or record.get("maximum_bytes") != spec.maximum_bytes
        or record.get("observed_size_bytes") != len(source.raw_bytes)
        or record.get("observed_sha256")
        != hashlib.sha256(source.raw_bytes).hexdigest()
        or record.get("expected_sha256") != record.get("observed_sha256")
        or record.get("content_binding_status") != "EXPECTED_SHA256_MATCHED"
        or record.get("operator_origin_authentication") != "NOT_ESTABLISHED"
        or record.get("stable_read_status") != "STABLE_BEFORE_AND_AFTER"
        or record.get("regular_file_status") != "REGULAR_FILE"
        or record.get("authority_effect") != AUTHORITY_EFFECT_NONE
    ):
        raise _ProjectionBlocked("MMI_POLICY_SOURCE_RECORD_BINDING_INVALID")
    try:
        expected_identity = record_identity_sha256(
            record,
            identity_field="source_record_identity_sha256",
            domain=MMI_SOURCE_RECORD_IDENTITY_DOMAIN,
            maximum_bytes=MAXIMUM_SOURCE_RECORD_CANONICAL_BYTES,
        )
    except MmiCanonicalizationError:
        raise _ProjectionBlocked("MMI_POLICY_SOURCE_RECORD_IDENTITY_INVALID") from None
    if record.get("source_record_identity_sha256") != expected_identity:
        raise _ProjectionBlocked("MMI_POLICY_SOURCE_RECORD_IDENTITY_INVALID")
    return record


def _require_ticker(value: object, *, code: str) -> str:
    if type(value) is not str or not _TICKER_RE.fullmatch(value):
        raise _ProjectionBlocked(code)
    return value


def _require_ticker_list(
    settings: Mapping[str, object],
    key: str,
    *,
    nonempty: bool,
    missing_code: str,
    invalid_code: str,
    empty_code: str,
) -> list[str]:
    if key not in settings:
        raise _ProjectionBlocked(missing_code)
    raw = settings[key]
    if type(raw) is not list:
        raise _ProjectionBlocked(invalid_code)
    if nonempty and not raw:
        raise _ProjectionBlocked(empty_code)
    if len(raw) > MAXIMUM_UNIVERSE_MEMBERS:
        raise _ProjectionBlocked("MMI_UNIVERSE_MEMBER_LIMIT_EXCEEDED")
    tickers = [
        _require_ticker(item, code=invalid_code)
        for item in raw
    ]
    if len(tickers) != len(set(tickers)):
        raise _ProjectionBlocked(f"{invalid_code}_DUPLICATE")
    return tickers


def _gap_record(
    code: str,
    *,
    scope: str,
    affected_question_class: str,
    affected_tickers: list[str],
    source_record_identity_sha256: str,
) -> dict[str, object]:
    return {
        "code": code,
        "scope": scope,
        "affected_question_class": affected_question_class,
        "affected_tickers": list(affected_tickers),
        "source_record_identity_sha256": source_record_identity_sha256,
    }


def _gap_sort_key(value: Mapping[str, object]) -> tuple[object, ...]:
    tickers = value.get("affected_tickers")
    return (
        value.get("code"),
        value.get("scope"),
        tuple(tickers) if type(tickers) is list else (),
    )


def _derive_expected_universe_gaps(
    value: Mapping[str, object],
) -> list[dict[str, object]]:
    source_identity = value.get("source_record_identity_sha256")
    approved = value.get("approved_extended_universe")
    missing_theme = value.get(
        "approved_extended_members_without_theme"
    )
    source_status = value.get("theme_mapping_source_status")
    if (
        type(source_identity) is not str
        or type(approved) is not list
        or type(missing_theme) is not list
    ):
        raise _ProjectionContractFailure(
            "MMI_UNIVERSE_GAP_CONTRACT_MISMATCH"
        )
    gaps: list[dict[str, object]] = []
    if source_status == "SOURCE_MAP_UNAVAILABLE":
        gaps.append(
            _gap_record(
                "EXTENDED_THEME_MAP_UNAVAILABLE",
                scope="UNIVERSE",
                affected_question_class="UNIVERSE_THEME_INTERPRETATION",
                affected_tickers=list(approved),
                source_record_identity_sha256=source_identity,
            )
        )
    elif source_status == "SOURCE_MAP_PRESENT":
        if missing_theme:
            gaps.append(
                _gap_record(
                    "EXTENDED_ETF_THEME_MAPPING_INCOMPLETE",
                    scope="UNIVERSE",
                    affected_question_class=(
                        "UNIVERSE_THEME_INTERPRETATION"
                    ),
                    affected_tickers=list(missing_theme),
                    source_record_identity_sha256=source_identity,
                )
            )
    else:
        raise _ProjectionContractFailure(
            "MMI_UNIVERSE_GAP_CONTRACT_MISMATCH"
        )
    return sorted(gaps, key=_gap_sort_key)


def _build_universe_projection(
    settings: Mapping[str, object],
    *,
    source_record_identity_sha256: str,
) -> dict[str, object]:
    core = _require_ticker_list(
        settings,
        "core_universe",
        nonempty=True,
        missing_code="MMI_UNIVERSE_CORE_MISSING",
        invalid_code="MMI_UNIVERSE_CORE_INVALID",
        empty_code="MMI_UNIVERSE_CORE_EMPTY",
    )
    satellite = _require_ticker_list(
        settings,
        "satellite_universe",
        nonempty=True,
        missing_code="MMI_UNIVERSE_SATELLITE_MISSING",
        invalid_code="MMI_UNIVERSE_SATELLITE_INVALID",
        empty_code="MMI_UNIVERSE_SATELLITE_EMPTY",
    )
    approved = _require_ticker_list(
        settings,
        "user_approved_extended_etf_static_list",
        nonempty=False,
        missing_code="MMI_UNIVERSE_APPROVED_EXTENDED_MISSING",
        invalid_code="MMI_UNIVERSE_APPROVED_EXTENDED_INVALID",
        empty_code="MMI_UNIVERSE_APPROVED_EXTENDED_EMPTY",
    )
    if set(core) & set(satellite):
        raise _ProjectionBlocked("MMI_UNIVERSE_CORE_SATELLITE_OVERLAP")
    if set(approved) & (set(core) | set(satellite)):
        raise _ProjectionBlocked("MMI_UNIVERSE_EXTENDED_BASE_OVERLAP")
    analysis_scope = core + satellite + approved
    if len(analysis_scope) > MAXIMUM_UNIVERSE_MEMBERS:
        raise _ProjectionBlocked("MMI_UNIVERSE_MEMBER_LIMIT_EXCEEDED")

    benchmark = _require_ticker(
        settings.get("benchmark"),
        code="MMI_UNIVERSE_BENCHMARK_INVALID",
    )
    if benchmark not in core:
        raise _ProjectionBlocked("MMI_UNIVERSE_BENCHMARK_NOT_CORE")

    role_by_ticker = {
        ticker: "CORE"
        for ticker in core
    }
    role_by_ticker.update(
        {ticker: "SATELLITE" for ticker in satellite}
    )
    role_by_ticker.update(
        {ticker: "APPROVED_EXTENDED" for ticker in approved}
    )

    theme_by_ticker: dict[str, str] = {}
    theme_key = "user_approved_extended_etf_theme_map"
    if theme_key not in settings:
        theme_mapping_source_status = "SOURCE_MAP_UNAVAILABLE"
        unmapped = list(approved)
    else:
        theme_mapping_source_status = "SOURCE_MAP_PRESENT"
        raw_theme_map = settings[theme_key]
        if type(raw_theme_map) is not dict:
            raise _ProjectionBlocked("MMI_UNIVERSE_THEME_MAP_INVALID")
        for ticker, raw_theme in raw_theme_map.items():
            checked_ticker = _require_ticker(
                ticker,
                code="MMI_UNIVERSE_THEME_MAP_INVALID",
            )
            if checked_ticker not in approved:
                raise _ProjectionBlocked(
                    "MMI_UNIVERSE_THEME_KEY_OUTSIDE_APPROVED_EXTENDED"
                )
            if (
                type(raw_theme) is not dict
                or set(raw_theme) != {"theme_bucket"}
                or type(raw_theme.get("theme_bucket")) is not str
                or not _THEME_RE.fullmatch(raw_theme["theme_bucket"])
            ):
                raise _ProjectionBlocked("MMI_UNIVERSE_THEME_MAP_INVALID")
            theme_by_ticker[checked_ticker] = raw_theme["theme_bucket"]
        unmapped = [
            ticker for ticker in approved if ticker not in theme_by_ticker
        ]

    universe: dict[str, object] = {
        "schema_version": "mmi_universe_projection_v1",
        "projection_kind": "MMI_UNIVERSE_PROJECTION",
        "report_only": True,
        "authority_effect": AUTHORITY_EFFECT_NONE,
        "source_record_identity_sha256": source_record_identity_sha256,
        "core_universe": core,
        "satellite_universe": satellite,
        "approved_extended_universe": approved,
        "benchmark_reference_instruments": [benchmark],
        "role_by_ticker": role_by_ticker,
        "theme_by_ticker": theme_by_ticker,
        "approved_extended_members_without_theme": unmapped,
        "theme_mapping_source_status": theme_mapping_source_status,
        "extended_membership_status": (
            "APPROVED_STATIC_MEMBERS_PRESENT"
            if approved
            else "APPROVED_STATIC_MEMBERS_EMPTY"
        ),
        "extended_activation_status": "NOT_EVALUATED_REPORT_ONLY",
        "analysis_scope_instruments": analysis_scope,
        "instrument_availability_observation_status": (
            "NOT_DETERMINISTICALLY_AVAILABLE"
        ),
        "known_universe_gaps": [],
    }
    universe["known_universe_gaps"] = _derive_expected_universe_gaps(
        universe
    )
    universe["universe_projection_identity_sha256"] = (
        record_identity_sha256(
            universe,
            identity_field="universe_projection_identity_sha256",
            domain=MMI_UNIVERSE_PROJECTION_IDENTITY_DOMAIN,
            maximum_bytes=MAXIMUM_UNIVERSE_CANONICAL_BYTES,
        )
    )
    try:
        validate_artifact_schema(
            universe,
            schema_name="mmi_universe_projection_v1.schema.json",
        )
        _validate_universe_semantics(universe)
    except _ProjectionContractFailure:
        raise
    except Exception:
        raise _ProjectionContractFailure(
            "MMI_UNIVERSE_PROJECTION_CONTRACT_FAILURE"
        ) from None
    return universe


def _require_bounded_count(value: object, *, code: str) -> int:
    if type(value) is not int or value < 0 or value > 256:
        raise _ProjectionBlocked(code)
    return value


def _require_boolean(value: object, *, code: str) -> bool:
    if type(value) is not bool:
        raise _ProjectionBlocked(code)
    return value


def _require_nonnegative_decimal(
    value: object,
    *,
    code: str,
    positive: bool = False,
    maximum_one: bool = False,
) -> str:
    if type(value) not in {int, Decimal} or type(value) is bool:
        raise _ProjectionBlocked(code)
    decimal_value = Decimal(value)
    if not decimal_value.is_finite() or decimal_value < 0:
        raise _ProjectionBlocked(code)
    if positive and decimal_value <= 0:
        raise _ProjectionBlocked(code)
    if maximum_one and decimal_value > 1:
        raise _ProjectionBlocked(code)
    try:
        return normalize_decimal_string(decimal_value)
    except MmiCanonicalizationError:
        raise _ProjectionBlocked(code) from None


def _parse_policy_as_of(
    value: object,
    *,
    evaluation_time_utc: datetime,
) -> str:
    if type(value) is not str or not _DATE_RE.fullmatch(value):
        raise _ProjectionBlocked("MMI_POLICY_AS_OF_INVALID")
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        raise _ProjectionBlocked("MMI_POLICY_AS_OF_INVALID") from None
    if parsed.isoformat() != value:
        raise _ProjectionBlocked("MMI_POLICY_AS_OF_INVALID")
    if parsed > evaluation_time_utc.date():
        raise _ProjectionBlocked("MMI_POLICY_AS_OF_FUTURE")
    return value


def _parse_source_run_timestamp(
    value: object,
    *,
    evaluation_time_utc: datetime,
) -> str | None:
    if value is None:
        return None
    if type(value) is not str or not _SOURCE_TIMESTAMP_RE.fullmatch(value):
        raise _ProjectionBlocked("MMI_POLICY_SOURCE_TIMESTAMP_INVALID")
    try:
        wall_time = datetime.strptime(value, "%Y-%m-%d %H:%M ET")
        eastern = ZoneInfo("America/New_York")
    except (ValueError, KeyError):
        raise _ProjectionBlocked("MMI_POLICY_SOURCE_TIMESTAMP_INVALID") from None
    candidates: dict[datetime, datetime] = {}
    for fold in (0, 1):
        localized = wall_time.replace(tzinfo=eastern, fold=fold)
        utc_value = localized.astimezone(timezone.utc)
        round_trip = utc_value.astimezone(eastern)
        if round_trip.replace(tzinfo=None) == wall_time:
            candidates[utc_value] = localized
    if len(candidates) != 1:
        raise _ProjectionBlocked("MMI_POLICY_SOURCE_TIMESTAMP_AMBIGUOUS")
    normalized = next(iter(candidates))
    if normalized > evaluation_time_utc:
        raise _ProjectionBlocked("MMI_POLICY_SOURCE_TIMESTAMP_FUTURE")
    return normalized.strftime(CANONICAL_UTC_TIMESTAMP_FORMAT)


def _build_shortlist_rules(
    settings: Mapping[str, object],
) -> dict[str, int]:
    value = settings.get("active_shortlist_size_rule")
    if type(value) is not dict or set(value) != set(_SHORTLIST_KEYS):
        raise _ProjectionBlocked("MMI_POLICY_SHORTLIST_RULES_INVALID")
    result = {
        key: _require_bounded_count(
            value[key],
            code="MMI_POLICY_SHORTLIST_RULES_INVALID",
        )
        for key in _SHORTLIST_KEYS
    }
    if result["benchmark_carrier"] < 1:
        raise _ProjectionBlocked("MMI_POLICY_SHORTLIST_RULES_INVALID")
    return result


def _build_rotation_policy(
    settings: Mapping[str, object],
) -> dict[str, object]:
    enabled = _require_boolean(
        settings.get("relative_rotation_enabled"),
        code="MMI_POLICY_ROTATION_ENABLED_INVALID",
    )
    guardrails = settings.get("relative_rotation_guardrails")
    if (
        type(guardrails) is not dict
        or set(guardrails) != set(_ROTATION_GUARDRAIL_KEYS)
    ):
        raise _ProjectionBlocked("MMI_POLICY_ROTATION_GUARDRAILS_INVALID")
    return {
        "enabled": enabled,
        "guardrails": {
            "require_same_role_for_rotation": _require_boolean(
                guardrails["require_same_role_for_rotation"],
                code="MMI_POLICY_ROTATION_GUARDRAILS_INVALID",
            ),
            "minimum_score_gap_to_rotate": _require_bounded_count(
                guardrails["min_score_gap_to_rotate"],
                code="MMI_POLICY_ROTATION_GUARDRAILS_INVALID",
            ),
            "do_not_rotate_if_current_holding_still_role_valid": (
                _require_boolean(
                    guardrails[
                        "do_not_rotate_if_current_holding_still_role_valid"
                    ],
                    code="MMI_POLICY_ROTATION_GUARDRAILS_INVALID",
                )
            ),
            "no_rotation_on_one_rank_change_only": _require_boolean(
                guardrails["no_rotation_on_one_rank_change_only"],
                code="MMI_POLICY_ROTATION_GUARDRAILS_INVALID",
            ),
        },
    }


def _build_maximum_new_ticker_rules(
    settings: Mapping[str, object],
) -> dict[str, object]:
    key = "max_new_tickers_per_week"
    if key not in settings:
        return {
            "status": "UNAVAILABLE",
            "base_universe_new_tickers_per_week": None,
            "extended_etf_sleeve_new_tickers_per_week": None,
        }
    value = settings[key]
    expected_keys = {
        "base_universe_new_tickers_per_week",
        "extended_etf_sleeve_new_tickers_per_week",
    }
    if type(value) is not dict or set(value) != expected_keys:
        raise _ProjectionBlocked(
            "MMI_POLICY_MAX_NEW_TICKER_RULE_INVALID"
        )
    return {
        "status": "SOURCE_VALIDATED",
        "base_universe_new_tickers_per_week": _require_bounded_count(
            value["base_universe_new_tickers_per_week"],
            code="MMI_POLICY_MAX_NEW_TICKER_RULE_INVALID",
        ),
        "extended_etf_sleeve_new_tickers_per_week": (
            _require_bounded_count(
                value["extended_etf_sleeve_new_tickers_per_week"],
                code="MMI_POLICY_MAX_NEW_TICKER_RULE_INVALID",
            )
        ),
    }


def _build_extended_sleeve_constraints(
    settings: Mapping[str, object],
) -> dict[str, object]:
    key = "extended_etf_constraints"
    unavailable = {
        "status": "UNAVAILABLE",
        "sleeve_budget_cap_fraction": None,
        "single_etf_budget_cap_fraction": None,
        "activation_minimum_effective_budget_fraction": None,
        "maximum_same_theme_member_count": None,
        "maximum_same_theme_budget_fraction": None,
        "require_distinct_theme_buckets": None,
    }
    if key not in settings:
        return unavailable
    value = settings[key]
    required_keys = {
        "sleeve_budget_cap_pct_of_total_open_orders",
        "single_extended_etf_budget_cap_pct_of_total_open_orders",
        "activation_minimum_effective_budget_pct_of_total_open_orders",
        "max_same_theme_extended_etf_count",
        "max_same_theme_budget_pct_of_total_open_orders",
        "require_distinct_theme_buckets_when_multiple_extended_etfs",
    }
    if type(value) is not dict or not required_keys.issubset(value):
        raise _ProjectionBlocked(
            "MMI_POLICY_EXTENDED_SLEEVE_CONSTRAINTS_INVALID"
        )
    return {
        "status": "SOURCE_VALIDATED",
        "sleeve_budget_cap_fraction": _require_nonnegative_decimal(
            value["sleeve_budget_cap_pct_of_total_open_orders"],
            code="MMI_POLICY_EXTENDED_SLEEVE_CONSTRAINTS_INVALID",
            maximum_one=True,
        ),
        "single_etf_budget_cap_fraction": _require_nonnegative_decimal(
            value["single_extended_etf_budget_cap_pct_of_total_open_orders"],
            code="MMI_POLICY_EXTENDED_SLEEVE_CONSTRAINTS_INVALID",
            maximum_one=True,
        ),
        "activation_minimum_effective_budget_fraction": (
            _require_nonnegative_decimal(
                value[
                    "activation_minimum_effective_budget_pct_of_total_open_orders"
                ],
                code="MMI_POLICY_EXTENDED_SLEEVE_CONSTRAINTS_INVALID",
                maximum_one=True,
            )
        ),
        "maximum_same_theme_member_count": _require_bounded_count(
            value["max_same_theme_extended_etf_count"],
            code="MMI_POLICY_EXTENDED_SLEEVE_CONSTRAINTS_INVALID",
        ),
        "maximum_same_theme_budget_fraction": (
            _require_nonnegative_decimal(
                value["max_same_theme_budget_pct_of_total_open_orders"],
                code="MMI_POLICY_EXTENDED_SLEEVE_CONSTRAINTS_INVALID",
                maximum_one=True,
            )
        ),
        "require_distinct_theme_buckets": _require_boolean(
            value[
                "require_distinct_theme_buckets_when_multiple_extended_etfs"
            ],
            code="MMI_POLICY_EXTENDED_SLEEVE_CONSTRAINTS_INVALID",
        ),
    }


def _build_per_run_budget(
    settings: Mapping[str, object],
) -> dict[str, object]:
    key = "target_new_buy_budget_this_run"
    if key not in settings:
        return {
            "status": "VALUE_UNAVAILABLE",
            "currency": None,
            "amount_decimal": None,
            "authority_effect": AUTHORITY_EFFECT_NONE,
        }
    amount = _require_nonnegative_decimal(
        settings[key],
        code="MMI_POLICY_PER_RUN_NEW_BUY_BUDGET_INVALID",
    )
    return {
        "status": "VALUE_PRESENT_APPLICABILITY_UNVERIFIED",
        "currency": "USD",
        "amount_decimal": amount,
        "authority_effect": AUTHORITY_EFFECT_NONE,
    }


def _derive_expected_policy_gaps(
    value: Mapping[str, object],
) -> list[dict[str, object]]:
    source_identity = value.get("source_record_identity_sha256")
    universe = value.get("universe_projection")
    per_run = value.get("per_run_new_buy_budget")
    maximum_new = value.get("maximum_new_ticker_rules")
    constraints = value.get("extended_sleeve_constraints")
    if (
        type(source_identity) is not str
        or type(universe) is not dict
        or type(per_run) is not dict
        or type(maximum_new) is not dict
        or type(constraints) is not dict
    ):
        raise _ProjectionContractFailure(
            "MMI_POLICY_GAP_CONTRACT_MISMATCH"
        )
    universe_gaps = universe.get("known_universe_gaps")
    if type(universe_gaps) is not list:
        raise _ProjectionContractFailure(
            "MMI_POLICY_GAP_CONTRACT_MISMATCH"
        )
    gaps = [dict(item) for item in universe_gaps]

    def append_policy_gap(code: str, question_class: str) -> None:
        gaps.append(
            _gap_record(
                code,
                scope="POLICY",
                affected_question_class=question_class,
                affected_tickers=[],
                source_record_identity_sha256=source_identity,
            )
        )

    per_run_status = per_run.get("status")
    if per_run_status == "VALUE_UNAVAILABLE":
        append_policy_gap(
            "POLICY_PER_RUN_NEW_BUY_BUDGET_UNAVAILABLE",
            "PER_RUN_BUDGET",
        )
    elif per_run_status == "VALUE_PRESENT_APPLICABILITY_UNVERIFIED":
        append_policy_gap(
            "POLICY_PER_RUN_BUDGET_APPLICABILITY_UNVERIFIED",
            "PER_RUN_BUDGET",
        )
    else:
        raise _ProjectionContractFailure(
            "MMI_POLICY_GAP_CONTRACT_MISMATCH"
        )

    maximum_new_status = maximum_new.get("status")
    if maximum_new_status == "UNAVAILABLE":
        append_policy_gap(
            "POLICY_MAX_NEW_TICKER_RULE_UNAVAILABLE",
            "MAXIMUM_NEW_TICKERS",
        )
    elif maximum_new_status != "SOURCE_VALIDATED":
        raise _ProjectionContractFailure(
            "MMI_POLICY_GAP_CONTRACT_MISMATCH"
        )

    constraints_status = constraints.get("status")
    if constraints_status == "UNAVAILABLE":
        append_policy_gap(
            "POLICY_EXTENDED_ACTIVATION_CONSTRAINTS_UNAVAILABLE",
            "EXTENDED_ACTIVATION",
        )
    elif constraints_status != "SOURCE_VALIDATED":
        raise _ProjectionContractFailure(
            "MMI_POLICY_GAP_CONTRACT_MISMATCH"
        )

    for code, question_class in _ALWAYS_UNAVAILABLE_POLICY_GAPS:
        append_policy_gap(code, question_class)
    return sorted(gaps, key=_gap_sort_key)


def _build_policy_projection_value(
    settings: Mapping[str, object],
    *,
    source_record: Mapping[str, object],
    run_context: MmiProjectionRunContext,
) -> dict[str, object]:
    prohibited_target_keys = {
        "target_weights",
        "target_weights_present",
        "target_allocation",
        "target_allocations",
    }
    if prohibited_target_keys & set(settings):
        raise _ProjectionBlocked("MMI_POLICY_TARGET_WEIGHTS_PROHIBITED")
    source_identity = source_record["source_record_identity_sha256"]
    if type(source_identity) is not str:
        raise _ProjectionContractFailure(
            "MMI_POLICY_SOURCE_RECORD_CONTRACT_FAILURE"
        )
    universe = _build_universe_projection(
        settings,
        source_record_identity_sha256=source_identity,
    )
    policy_as_of = _parse_policy_as_of(
        settings.get("as_of"),
        evaluation_time_utc=run_context.evaluation_time_utc,
    )
    source_run_timestamp = _parse_source_run_timestamp(
        settings.get("run_timestamp_et"),
        evaluation_time_utc=run_context.evaluation_time_utc,
    )
    hard_cap = _require_nonnegative_decimal(
        settings.get("hard_cap_open_orders_budget"),
        code="MMI_POLICY_HARD_OPEN_ORDERS_BUDGET_CAP_INVALID",
        positive=True,
    )
    shortlist = _build_shortlist_rules(settings)
    rotation = _build_rotation_policy(settings)

    per_run_budget = _build_per_run_budget(settings)
    maximum_new_ticker_rules = _build_maximum_new_ticker_rules(settings)
    extended_constraints = _build_extended_sleeve_constraints(settings)

    projection: dict[str, object] = {
        "schema_version": "mmi_policy_projection_v1",
        "projection_kind": "MMI_POLICY_PROJECTION",
        "report_only": True,
        "authority_effect": AUTHORITY_EFFECT_NONE,
        "evaluation_timestamp_utc": (
            run_context.evaluation_timestamp_utc
        ),
        "source_record_identity_sha256": source_identity,
        "policy_method": POLICY_METHOD,
        "policy_as_of_date": policy_as_of,
        "source_run_timestamp_utc": source_run_timestamp,
        "universe_projection": universe,
        "universe_projection_identity_sha256": universe[
            "universe_projection_identity_sha256"
        ],
        "hard_open_orders_budget_cap": {
            "currency": "USD",
            "amount_decimal": hard_cap,
            "validation_status": "SOURCE_VALIDATED",
            "authority_effect": AUTHORITY_EFFECT_NONE,
        },
        "per_run_new_buy_budget": per_run_budget,
        "shortlist_size_rules": shortlist,
        "maximum_new_ticker_rules": maximum_new_ticker_rules,
        "extended_sleeve_constraints": extended_constraints,
        "rotation_policy": rotation,
        "target_weights_present": False,
        "target_weights": [],
        "target_weights_absence_reason": (
            "POLICY_METHOD_HAS_NO_TARGET_WEIGHTS"
        ),
        "policy_completeness_statuses": list(_COMPLETENESS_STATUSES),
        "known_policy_gaps": [],
    }
    projection["known_policy_gaps"] = _derive_expected_policy_gaps(
        projection
    )
    projection["policy_projection_identity_sha256"] = (
        record_identity_sha256(
            projection,
            identity_field="policy_projection_identity_sha256",
            domain=MMI_POLICY_PROJECTION_IDENTITY_DOMAIN,
            maximum_bytes=MAXIMUM_POLICY_CANONICAL_BYTES,
        )
    )
    return projection


def _derive_expected_policy_projection(
    source: MmiCapturedSource,
    *,
    run_context: MmiProjectionRunContext,
) -> dict[str, object]:
    """Derive every source-owned projection field from trusted run inputs."""
    _validate_run_context(run_context)
    source_record = _validate_captured_source(source)
    settings = _parse_strict_strategy_settings(source.raw_bytes)
    return _build_policy_projection_value(
        settings,
        source_record=source_record,
        run_context=run_context,
    )


def _validate_universe_semantics(value: Mapping[str, object]) -> None:
    core = value.get("core_universe")
    satellite = value.get("satellite_universe")
    approved = value.get("approved_extended_universe")
    benchmarks = value.get("benchmark_reference_instruments")
    if (
        type(core) is not list
        or type(satellite) is not list
        or type(approved) is not list
    ):
        raise _ProjectionContractFailure(
            "MMI_UNIVERSE_PROJECTION_SEMANTIC_INVALID"
        )
    if (
        type(benchmarks) is not list
        or len(benchmarks) != 1
        or benchmarks[0] not in core
    ):
        raise _ProjectionContractFailure(
            "MMI_UNIVERSE_BENCHMARK_CONTRACT_MISMATCH"
        )
    analysis_scope = core + satellite + approved
    expected_roles = {ticker: "CORE" for ticker in core}
    expected_roles.update({ticker: "SATELLITE" for ticker in satellite})
    expected_roles.update(
        {ticker: "APPROVED_EXTENDED" for ticker in approved}
    )
    theme_by_ticker = value.get("theme_by_ticker")
    missing_theme = value.get(
        "approved_extended_members_without_theme"
    )
    theme_source_status = value.get("theme_mapping_source_status")
    if (
        value.get("analysis_scope_instruments") != analysis_scope
        or value.get("role_by_ticker") != expected_roles
        or type(theme_by_ticker) is not dict
        or set(theme_by_ticker) - set(approved)
        or missing_theme
        != [ticker for ticker in approved if ticker not in theme_by_ticker]
        or value.get("extended_membership_status")
        != (
            "APPROVED_STATIC_MEMBERS_PRESENT"
            if approved
            else "APPROVED_STATIC_MEMBERS_EMPTY"
        )
        or (
            theme_source_status == "SOURCE_MAP_UNAVAILABLE"
            and (
                theme_by_ticker != {}
                or missing_theme != approved
            )
        )
        or theme_source_status
        not in {"SOURCE_MAP_UNAVAILABLE", "SOURCE_MAP_PRESENT"}
    ):
        raise _ProjectionContractFailure(
            "MMI_UNIVERSE_PROJECTION_SEMANTIC_INVALID"
        )
    gaps = value.get("known_universe_gaps")
    expected_gaps = _derive_expected_universe_gaps(value)
    if type(gaps) is not list or gaps != expected_gaps:
        raise _ProjectionContractFailure(
            "MMI_UNIVERSE_GAP_CONTRACT_MISMATCH"
        )
    try:
        expected_identity = record_identity_sha256(
            dict(value),
            identity_field="universe_projection_identity_sha256",
            domain=MMI_UNIVERSE_PROJECTION_IDENTITY_DOMAIN,
            maximum_bytes=MAXIMUM_UNIVERSE_CANONICAL_BYTES,
        )
    except MmiCanonicalizationError:
        raise _ProjectionContractFailure(
            "MMI_UNIVERSE_PROJECTION_IDENTITY_INVALID"
        ) from None
    if value.get("universe_projection_identity_sha256") != expected_identity:
        raise _ProjectionContractFailure(
            "MMI_UNIVERSE_PROJECTION_IDENTITY_INVALID"
        )


def _validated_projected_count(value: object, *, code: str) -> int:
    if type(value) is not int or value < 0 or value > 256:
        raise _ProjectionContractFailure(code)
    return value


def _validated_canonical_decimal_text(
    value: object,
    *,
    code: str,
    positive: bool = False,
    maximum_one: bool = False,
) -> Decimal:
    if type(value) is not str:
        raise _ProjectionContractFailure(code)
    try:
        normalized = normalize_decimal_string(value)
        decimal_value = Decimal(value)
    except (InvalidOperation, MmiCanonicalizationError):
        raise _ProjectionContractFailure(code) from None
    if (
        normalized != value
        or not decimal_value.is_finite()
        or decimal_value < 0
        or (positive and decimal_value <= 0)
        or (maximum_one and decimal_value > 1)
    ):
        raise _ProjectionContractFailure(code)
    return decimal_value


def _validate_shortlist_semantics(value: object) -> None:
    code = "MMI_POLICY_SHORTLIST_CONTRACT_MISMATCH"
    if type(value) is not dict or set(value) != set(_SHORTLIST_KEYS):
        raise _ProjectionContractFailure(code)
    for key in _SHORTLIST_KEYS:
        count = _validated_projected_count(value[key], code=code)
        if key == "benchmark_carrier" and count < 1:
            raise _ProjectionContractFailure(code)


def _validate_rotation_semantics(value: object) -> None:
    code = "MMI_POLICY_ROTATION_CONTRACT_MISMATCH"
    if (
        type(value) is not dict
        or set(value) != {"enabled", "guardrails"}
        or type(value.get("enabled")) is not bool
    ):
        raise _ProjectionContractFailure(code)
    guardrails = value.get("guardrails")
    expected_keys = {
        "require_same_role_for_rotation",
        "minimum_score_gap_to_rotate",
        "do_not_rotate_if_current_holding_still_role_valid",
        "no_rotation_on_one_rank_change_only",
    }
    if type(guardrails) is not dict or set(guardrails) != expected_keys:
        raise _ProjectionContractFailure(code)
    if (
        type(guardrails["require_same_role_for_rotation"]) is not bool
        or type(
            guardrails[
                "do_not_rotate_if_current_holding_still_role_valid"
            ]
        )
        is not bool
        or type(guardrails["no_rotation_on_one_rank_change_only"])
        is not bool
    ):
        raise _ProjectionContractFailure(code)
    _validated_projected_count(
        guardrails["minimum_score_gap_to_rotate"],
        code=code,
    )


def _validate_hard_cap_semantics(value: object) -> None:
    code = "MMI_POLICY_HARD_OPEN_ORDERS_BUDGET_CAP_CONTRACT_MISMATCH"
    if (
        type(value) is not dict
        or set(value)
        != {
            "currency",
            "amount_decimal",
            "validation_status",
            "authority_effect",
        }
        or value.get("currency") != "USD"
        or value.get("validation_status") != "SOURCE_VALIDATED"
        or value.get("authority_effect") != AUTHORITY_EFFECT_NONE
    ):
        raise _ProjectionContractFailure(code)
    _validated_canonical_decimal_text(
        value.get("amount_decimal"),
        code=code,
        positive=True,
    )


def _validate_per_run_budget_semantics(value: object) -> None:
    code = "MMI_POLICY_PER_RUN_BUDGET_CONTRACT_MISMATCH"
    if (
        type(value) is not dict
        or set(value)
        != {"status", "currency", "amount_decimal", "authority_effect"}
        or value.get("authority_effect") != AUTHORITY_EFFECT_NONE
    ):
        raise _ProjectionContractFailure(code)
    status = value.get("status")
    if status == "VALUE_UNAVAILABLE":
        if (
            value.get("currency") is not None
            or value.get("amount_decimal") is not None
        ):
            raise _ProjectionContractFailure(code)
        return
    if (
        status != "VALUE_PRESENT_APPLICABILITY_UNVERIFIED"
        or value.get("currency") != "USD"
    ):
        raise _ProjectionContractFailure(code)
    _validated_canonical_decimal_text(
        value.get("amount_decimal"),
        code=code,
    )


def _validate_maximum_new_ticker_semantics(value: object) -> None:
    code = "MMI_POLICY_MAX_NEW_TICKER_CONTRACT_MISMATCH"
    expected_keys = {
        "status",
        "base_universe_new_tickers_per_week",
        "extended_etf_sleeve_new_tickers_per_week",
    }
    if type(value) is not dict or set(value) != expected_keys:
        raise _ProjectionContractFailure(code)
    status = value.get("status")
    count_keys = expected_keys - {"status"}
    if status == "UNAVAILABLE":
        if any(value[key] is not None for key in count_keys):
            raise _ProjectionContractFailure(code)
        return
    if status != "SOURCE_VALIDATED":
        raise _ProjectionContractFailure(code)
    for key in count_keys:
        _validated_projected_count(value[key], code=code)


def _validate_extended_constraints_semantics(value: object) -> None:
    code = "MMI_POLICY_EXTENDED_SLEEVE_CONSTRAINTS_CONTRACT_MISMATCH"
    ratio_keys = {
        "sleeve_budget_cap_fraction",
        "single_etf_budget_cap_fraction",
        "activation_minimum_effective_budget_fraction",
        "maximum_same_theme_budget_fraction",
    }
    expected_keys = ratio_keys | {
        "status",
        "maximum_same_theme_member_count",
        "require_distinct_theme_buckets",
    }
    if type(value) is not dict or set(value) != expected_keys:
        raise _ProjectionContractFailure(code)
    status = value.get("status")
    constrained_keys = expected_keys - {"status"}
    if status == "UNAVAILABLE":
        if any(value[key] is not None for key in constrained_keys):
            raise _ProjectionContractFailure(code)
        return
    if status != "SOURCE_VALIDATED":
        raise _ProjectionContractFailure(code)
    for key in ratio_keys:
        _validated_canonical_decimal_text(
            value[key],
            code=code,
            maximum_one=True,
        )
    _validated_projected_count(
        value["maximum_same_theme_member_count"],
        code=code,
    )
    if type(value["require_distinct_theme_buckets"]) is not bool:
        raise _ProjectionContractFailure(code)


def _validate_policy_semantics(value: Mapping[str, object]) -> None:
    universe = value.get("universe_projection")
    if type(universe) is not dict:
        raise _ProjectionContractFailure(
            "MMI_POLICY_PROJECTION_SEMANTIC_INVALID"
        )
    try:
        validate_artifact_schema(
            universe,
            schema_name="mmi_universe_projection_v1.schema.json",
        )
    except Exception:
        raise _ProjectionContractFailure(
            "MMI_UNIVERSE_PROJECTION_SCHEMA_INVALID"
        ) from None
    _validate_universe_semantics(universe)
    if (
        value.get("authority_effect") != AUTHORITY_EFFECT_NONE
        or value.get("policy_method") != POLICY_METHOD
        or value.get("target_weights_present") is not False
        or value.get("target_weights") != []
        or value.get("target_weights_absence_reason")
        != "POLICY_METHOD_HAS_NO_TARGET_WEIGHTS"
        or value.get("policy_completeness_statuses")
        != list(_COMPLETENESS_STATUSES)
        or value.get("universe_projection_identity_sha256")
        != universe.get("universe_projection_identity_sha256")
        or value.get("source_record_identity_sha256")
        != universe.get("source_record_identity_sha256")
    ):
        raise _ProjectionContractFailure(
            "MMI_POLICY_PROJECTION_SEMANTIC_INVALID"
        )
    _validate_hard_cap_semantics(
        value.get("hard_open_orders_budget_cap")
    )
    _validate_per_run_budget_semantics(
        value.get("per_run_new_buy_budget")
    )
    _validate_shortlist_semantics(value.get("shortlist_size_rules"))
    _validate_maximum_new_ticker_semantics(
        value.get("maximum_new_ticker_rules")
    )
    _validate_extended_constraints_semantics(
        value.get("extended_sleeve_constraints")
    )
    _validate_rotation_semantics(value.get("rotation_policy"))
    gaps = value.get("known_policy_gaps")
    expected_gaps = _derive_expected_policy_gaps(value)
    if type(gaps) is not list or gaps != expected_gaps:
        raise _ProjectionContractFailure(
            "MMI_POLICY_GAP_CONTRACT_MISMATCH"
        )
    try:
        evaluation = datetime.strptime(
            value["evaluation_timestamp_utc"],
            CANONICAL_UTC_TIMESTAMP_FORMAT,
        ).replace(tzinfo=timezone.utc)
        as_of = date.fromisoformat(value["policy_as_of_date"])
        source_timestamp = value.get("source_run_timestamp_utc")
        if source_timestamp is not None:
            parsed_source_timestamp = datetime.strptime(
                source_timestamp,
                CANONICAL_UTC_TIMESTAMP_FORMAT,
            ).replace(tzinfo=timezone.utc)
            if parsed_source_timestamp > evaluation:
                raise ValueError
        if as_of > evaluation.date():
            raise ValueError
    except (KeyError, TypeError, ValueError):
        raise _ProjectionContractFailure(
            "MMI_POLICY_PROJECTION_SEMANTIC_INVALID"
        ) from None
    try:
        expected_identity = record_identity_sha256(
            dict(value),
            identity_field="policy_projection_identity_sha256",
            domain=MMI_POLICY_PROJECTION_IDENTITY_DOMAIN,
            maximum_bytes=MAXIMUM_POLICY_CANONICAL_BYTES,
        )
    except MmiCanonicalizationError:
        raise _ProjectionContractFailure(
            "MMI_POLICY_PROJECTION_IDENTITY_INVALID"
        ) from None
    if value.get("policy_projection_identity_sha256") != expected_identity:
        raise _ProjectionContractFailure(
            "MMI_POLICY_PROJECTION_IDENTITY_INVALID"
        )


def validate_mmi_policy_projection(
    value: object,
    *,
    source: MmiCapturedSource,
    run_context: MmiProjectionRunContext,
) -> MmiPolicyProjectionValidationResult:
    """Validate one projection against its same-run captured source."""
    try:
        expected = _derive_expected_policy_projection(
            source,
            run_context=run_context,
        )
    except _ProjectionBlocked as exc:
        return _validation_result(
            MmiProjectionResultCategory.PROJECTION_BLOCKED,
            exc.code,
        )
    except _ProjectionContractFailure as exc:
        return _validation_result(
            MmiProjectionResultCategory.PROJECTION_CONTRACT_FAILURE,
            exc.code,
        )
    except Exception:
        return _validation_result(
            MmiProjectionResultCategory.PROJECTION_CONTRACT_FAILURE,
            "MMI_POLICY_PROJECTION_INTERNAL_INVARIANT_FAILED",
        )
    if type(value) is not dict:
        return _validation_result(
            MmiProjectionResultCategory.PROJECTION_BLOCKED,
            "MMI_POLICY_PROJECTION_SCHEMA_INVALID",
        )
    try:
        validate_artifact_schema(
            value,
            schema_name="mmi_policy_projection_v1.schema.json",
        )
    except Exception:
        return _validation_result(
            MmiProjectionResultCategory.PROJECTION_BLOCKED,
            "MMI_POLICY_PROJECTION_SCHEMA_INVALID",
        )
    try:
        _validate_policy_semantics(value)
    except _ProjectionContractFailure as exc:
        return _validation_result(
            MmiProjectionResultCategory.PROJECTION_CONTRACT_FAILURE,
            exc.code,
        )
    if value != expected:
        return _validation_result(
            MmiProjectionResultCategory.PROJECTION_CONTRACT_FAILURE,
            "MMI_POLICY_SOURCE_FIDELITY_MISMATCH",
        )
    gaps = value["known_policy_gaps"]
    return _validation_result(
        (
            MmiProjectionResultCategory.PROJECTION_VALID_WITH_GAPS
            if gaps
            else MmiProjectionResultCategory.PROJECTION_VALID_COMPLETE
        )
    )


def build_mmi_policy_projection(
    source: MmiCapturedSource,
    *,
    run_context: MmiProjectionRunContext,
) -> MmiPolicyProjectionBuildResult:
    """Build one pure report-only policy/universe projection from exact bytes."""
    try:
        projection = _derive_expected_policy_projection(
            source,
            run_context=run_context,
        )
        try:
            validate_artifact_schema(
                projection,
                schema_name="mmi_policy_projection_v1.schema.json",
            )
            _validate_policy_semantics(projection)
        except _ProjectionContractFailure:
            raise
        except Exception:
            raise _ProjectionContractFailure(
                "MMI_POLICY_PROJECTION_CONTRACT_FAILURE"
            ) from None
    except _ProjectionBlocked as exc:
        return _build_result(
            MmiProjectionResultCategory.PROJECTION_BLOCKED,
            exc.code,
        )
    except _ProjectionContractFailure as exc:
        return _build_result(
            MmiProjectionResultCategory.PROJECTION_CONTRACT_FAILURE,
            exc.code,
        )
    except Exception:
        return _build_result(
            MmiProjectionResultCategory.PROJECTION_CONTRACT_FAILURE,
            "MMI_POLICY_PROJECTION_INTERNAL_INVARIANT_FAILED",
        )
    gaps = projection["known_policy_gaps"]
    return _build_result(
        (
            MmiProjectionResultCategory.PROJECTION_VALID_WITH_GAPS
            if gaps
            else MmiProjectionResultCategory.PROJECTION_VALID_COMPLETE
        ),
        *(gap["code"] for gap in gaps),
        projection=projection,
    )
