"""Source-bound, report-only MMI portfolio snapshot projection."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime, timedelta, timezone
from decimal import Context, Decimal, InvalidOperation, localcontext
import hashlib
import re
from typing import Final

from investment_orchestrator.common.schema_validation import (
    validate_artifact_schema,
)
from investment_orchestrator.mmi.canonical import (
    MMI_PORTFOLIO_SNAPSHOT_PROJECTION_IDENTITY_DOMAIN,
    MMI_SOURCE_RECORD_IDENTITY_DOMAIN,
    MmiCanonicalizationError,
    normalize_decimal_string,
    record_identity_sha256,
)
from investment_orchestrator.mmi.contracts import (
    AUTHORITY_EFFECT_NONE,
    CANONICAL_UTC_TIMESTAMP_FORMAT,
    MMI_SOURCE_CATALOG,
    MmiCapturedSource,
    MmiPolicyProjectionValidationResult,
    MmiPortfolioProjectionBuildResult,
    MmiPortfolioProjectionValidationResult,
    MmiProjectionResultCategory,
    MmiProjectionRunContext,
    MmiSourceRole,
    _mmi_captured_source_provenance_is_valid,
    _mmi_projection_run_context_provenance_is_valid,
)
from investment_orchestrator.mmi.policy_projection import (
    validate_mmi_policy_projection,
)
from investment_orchestrator.parsers.portfolio_snapshot_existing_orders import (
    parse_existing_buy_open_orders_summary,
)


MAXIMUM_PORTFOLIO_CANONICAL_BYTES: Final = 524_288
MAXIMUM_PORTFOLIO_SOURCE_BYTES: Final = 1_048_576
MAXIMUM_OPEN_BUY_RECORDS: Final = 256
MAXIMUM_SOURCE_RECORD_CANONICAL_BYTES: Final = 8_192

_PORTFOLIO_SCHEMA_NAME: Final = (
    "mmi_portfolio_snapshot_projection_v1.schema.json"
)
_PORTFOLIO_SECTION_START: Final = (
    "(2a) existing_buy_open_orders_summary"
    "（optional, ticker-level summary; buy-side existing open orders SSOT）"
)
_PORTFOLIO_SECTION_END: Final = (
    "(2b) sell_open_orders"
    "（optional, lot-aware open sell orders summary）"
)
_OPEN_BUY_HEADER_COLUMNS: Final = (
    "TICKER",
    "budget",
    "compiled_open_order_notional(optional)",
    "residual_cash_not_allocated(optional)",
    "template_id",
    "anchor_baseline_last_close",
    "anchor_price_asof",
    "last_refresh_date_et(optional)",
    "highest_live_limit(optional)",
    "lowest_live_limit(optional)",
    "live_step_count(optional)",
    "live_order_steps_summary(optional)",
    "live_order_qtys_summary(optional)",
)
_OPEN_BUY_HEADER: Final = " | ".join(_OPEN_BUY_HEADER_COLUMNS)
_SOURCE_RECORD_FIELDS: Final = frozenset(
    {
        "schema_version",
        "source_role",
        "source_id",
        "repository_relative_locator",
        "maximum_bytes",
        "observed_size_bytes",
        "expected_sha256",
        "observed_sha256",
        "content_binding_status",
        "operator_origin_authentication",
        "stable_read_status",
        "regular_file_status",
        "authority_effect",
        "source_record_identity_sha256",
    }
)
_TICKER_RE: Final = re.compile(r"^[A-Z][A-Z0-9.-]{0,15}$")
_DATA_LIKE_RE: Final = re.compile(
    r"^[A-Z][A-Z0-9.-]{0,15}(?:\s+|\s*\|)"
)
_MALFORMED_DATA_LIKE_RE: Final = re.compile(
    r"^[A-Za-z][A-Za-z0-9.-]{0,15}\s+"
    r"(?:[-+0-9.]|true(?:\s|$)|false(?:\s|$)|nan(?:\s|$)|inf(?:\s|$))",
    re.IGNORECASE,
)
_SOURCE_MONEY_RE: Final = re.compile(
    r"^(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$"
)
_SOURCE_COUNT_RE: Final = re.compile(r"^(?:0|[1-9][0-9]*)$")
_STEP_NAME_RE: Final = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,31}$")
_TEMPLATE_ID_RE: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_DATE_RE: Final = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_HEADER_DATE_RE: Final = re.compile(
    r"^# updated (?P<date>\d{4}-\d{2}-\d{2})$"
)
_HEADER_MARKER_CANDIDATE_RE: Final = re.compile(
    r"^\s*#\s*updated\b",
    re.IGNORECASE,
)
_ANY_SECTION_RE: Final = re.compile(r"^\([0-9]+[a-z_]*\)")
_SHA256_RE: Final = re.compile(r"^[0-9a-f]{64}$")

_UNSTRUCTURED_HOLDINGS: Final = {
    "status": "UNSTRUCTURED_NOT_PROJECTED",
    "records": [],
}
_UNAVAILABLE_AREA: Final = {"status": "UNAVAILABLE_NOT_PROJECTED"}
_UNSTRUCTURED_OPEN_SELLS: Final = {
    "status": "UNSTRUCTURED_NOT_PROJECTED",
    "records": [],
}
_UNSTRUCTURED_TAX_LOTS: Final = {
    "status": "UNSTRUCTURED_NOT_PROJECTED",
    "records": [],
}
_STATIC_GAP_CODES: Final = (
    "PORTFOLIO_HOLDINGS_UNSTRUCTURED",
    "PORTFOLIO_OPEN_SELL_ORDERS_UNSTRUCTURED",
    "PORTFOLIO_TAX_LOTS_UNSTRUCTURED",
    "PORTFOLIO_DEPLOYABLE_CASH_UNAVAILABLE",
    "PORTFOLIO_WEIGHTS_UNAVAILABLE",
    "PORTFOLIO_NAV_CONCENTRATION_UNAVAILABLE",
    "PORTFOLIO_LOOKTHROUGH_EXPOSURE_UNAVAILABLE",
)
_GAP_CODE_ORDER: Final = (
    "PORTFOLIO_SOURCE_MISSING",
    "PORTFOLIO_SOURCE_TIMESTAMP_UNAVAILABLE",
    "PORTFOLIO_HOLDINGS_UNSTRUCTURED",
    "PORTFOLIO_OPEN_BUY_ORDERS_PARSE_FAILED",
    "PORTFOLIO_OPEN_BUY_ORDER_OUTSIDE_POLICY_UNIVERSE",
    "PORTFOLIO_OPEN_SELL_ORDERS_UNSTRUCTURED",
    "PORTFOLIO_TAX_LOTS_UNSTRUCTURED",
    "PORTFOLIO_DEPLOYABLE_CASH_UNAVAILABLE",
    "PORTFOLIO_WEIGHTS_UNAVAILABLE",
    "PORTFOLIO_NAV_CONCENTRATION_UNAVAILABLE",
    "PORTFOLIO_LOOKTHROUGH_EXPOSURE_UNAVAILABLE",
)
_GAP_CODE_SET: Final = frozenset(_GAP_CODE_ORDER)


class _PortfolioBlocked(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class _PortfolioContractFailure(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class _OpenBuyParseFailure(RuntimeError):
    """Internal marker; raw parser diagnostics never cross the boundary."""


def _build_result(
    status: MmiProjectionResultCategory,
    *reason_codes: str,
    projection: Mapping[str, object] | None = None,
) -> MmiPortfolioProjectionBuildResult:
    return MmiPortfolioProjectionBuildResult(
        status=status,
        authority_effect=AUTHORITY_EFFECT_NONE,
        reason_codes=tuple(reason_codes),
        projection=projection,
    )


def _validation_result(
    status: MmiProjectionResultCategory,
    *reason_codes: str,
) -> MmiPortfolioProjectionValidationResult:
    return MmiPortfolioProjectionValidationResult(
        status=status,
        authority_effect=AUTHORITY_EFFECT_NONE,
        reason_codes=tuple(reason_codes),
    )


def _validate_run_context(run_context: MmiProjectionRunContext) -> None:
    if not _mmi_projection_run_context_provenance_is_valid(run_context):
        raise _PortfolioContractFailure(
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
        raise _PortfolioContractFailure(
            "MMI_PROJECTION_RUN_CONTEXT_PROVENANCE_INVALID"
        )


def _validate_policy_inputs(
    policy_projection: Mapping[str, object],
    *,
    policy_source: MmiCapturedSource,
    run_context: MmiProjectionRunContext,
) -> tuple[str, dict[str, str]]:
    _validate_run_context(run_context)
    if not _mmi_captured_source_provenance_is_valid(policy_source):
        raise _PortfolioContractFailure(
            "MMI_PORTFOLIO_POLICY_SOURCE_PROVENANCE_INVALID"
        )
    if policy_source.role is not MmiSourceRole.STRATEGY_SETTINGS:
        raise _PortfolioContractFailure(
            "MMI_PORTFOLIO_POLICY_SOURCE_ROLE_INVALID"
        )
    if not isinstance(policy_projection, Mapping):
        raise _PortfolioBlocked(
            "MMI_PORTFOLIO_POLICY_PROJECTION_INVALID"
        )
    try:
        policy_value = dict(policy_projection)
    except (TypeError, ValueError):
        raise _PortfolioBlocked(
            "MMI_PORTFOLIO_POLICY_PROJECTION_INVALID"
        ) from None
    validation: MmiPolicyProjectionValidationResult = (
        validate_mmi_policy_projection(
            policy_value,
            source=policy_source,
            run_context=run_context,
        )
    )
    if not validation.valid:
        if (
            validation.status
            is MmiProjectionResultCategory.PROJECTION_BLOCKED
        ):
            raise _PortfolioBlocked(
                "MMI_PORTFOLIO_POLICY_PROJECTION_INVALID"
            )
        raise _PortfolioContractFailure(
            "MMI_PORTFOLIO_POLICY_PROJECTION_CONTRACT_INVALID"
        )
    policy_identity = policy_value.get(
        "policy_projection_identity_sha256"
    )
    universe = policy_value.get("universe_projection")
    if (
        type(policy_identity) is not str
        or not _SHA256_RE.fullmatch(policy_identity)
        or type(universe) is not dict
    ):
        raise _PortfolioContractFailure(
            "MMI_PORTFOLIO_POLICY_PROJECTION_CONTRACT_INVALID"
        )
    raw_roles = universe.get("role_by_ticker")
    if type(raw_roles) is not dict:
        raise _PortfolioContractFailure(
            "MMI_PORTFOLIO_POLICY_PROJECTION_CONTRACT_INVALID"
        )
    roles: dict[str, str] = {}
    for ticker, role in raw_roles.items():
        if (
            type(ticker) is not str
            or not _TICKER_RE.fullmatch(ticker)
            or role
            not in {"CORE", "SATELLITE", "APPROVED_EXTENDED"}
        ):
            raise _PortfolioContractFailure(
                "MMI_PORTFOLIO_POLICY_PROJECTION_CONTRACT_INVALID"
            )
        roles[ticker] = role
    return policy_identity, roles


def _validate_portfolio_source(
    source: MmiCapturedSource,
) -> dict[str, object]:
    if not _mmi_captured_source_provenance_is_valid(source):
        raise _PortfolioContractFailure(
            "MMI_PORTFOLIO_CAPTURE_PROVENANCE_INVALID"
        )
    if source.role is not MmiSourceRole.PORTFOLIO_SNAPSHOT:
        raise _PortfolioContractFailure(
            "MMI_PORTFOLIO_CAPTURE_ROLE_INVALID"
        )
    if type(source.raw_bytes) is not bytes:
        raise _PortfolioBlocked("MMI_PORTFOLIO_SOURCE_BYTES_INVALID")
    spec = MMI_SOURCE_CATALOG[MmiSourceRole.PORTFOLIO_SNAPSHOT]
    if len(source.raw_bytes) > spec.maximum_bytes:
        raise _PortfolioBlocked("MMI_PORTFOLIO_SOURCE_OVERSIZED")
    try:
        record = dict(source.source_record)
    except (TypeError, ValueError):
        raise _PortfolioBlocked(
            "MMI_PORTFOLIO_SOURCE_RECORD_INVALID"
        ) from None
    observed_sha256 = hashlib.sha256(source.raw_bytes).hexdigest()
    if (
        set(record) != _SOURCE_RECORD_FIELDS
        or record.get("schema_version") != "mmi_source_record_v1"
        or record.get("source_role") != source.role.value
        or record.get("source_id") != spec.source_id
        or record.get("repository_relative_locator")
        != str(spec.repository_relative_locator)
        or record.get("maximum_bytes") != spec.maximum_bytes
        or type(record.get("observed_size_bytes")) is not int
        or record.get("observed_size_bytes") != len(source.raw_bytes)
        or record.get("expected_sha256") != observed_sha256
        or record.get("observed_sha256") != observed_sha256
        or record.get("content_binding_status")
        != "EXPECTED_SHA256_MATCHED"
        or record.get("operator_origin_authentication")
        != "NOT_ESTABLISHED"
        or record.get("stable_read_status")
        != "STABLE_BEFORE_AND_AFTER"
        or record.get("regular_file_status") != "REGULAR_FILE"
        or record.get("authority_effect") != AUTHORITY_EFFECT_NONE
    ):
        raise _PortfolioBlocked(
            "MMI_PORTFOLIO_SOURCE_RECORD_BINDING_INVALID"
        )
    try:
        expected_identity = record_identity_sha256(
            record,
            identity_field="source_record_identity_sha256",
            domain=MMI_SOURCE_RECORD_IDENTITY_DOMAIN,
            maximum_bytes=MAXIMUM_SOURCE_RECORD_CANONICAL_BYTES,
        )
    except MmiCanonicalizationError:
        raise _PortfolioBlocked(
            "MMI_PORTFOLIO_SOURCE_RECORD_IDENTITY_INVALID"
        ) from None
    if record.get("source_record_identity_sha256") != expected_identity:
        raise _PortfolioBlocked(
            "MMI_PORTFOLIO_SOURCE_RECORD_IDENTITY_INVALID"
        )
    return record


def _decode_portfolio_source(raw_bytes: bytes) -> str:
    if type(raw_bytes) is not bytes:
        raise _PortfolioBlocked("MMI_PORTFOLIO_SOURCE_BYTES_INVALID")
    if len(raw_bytes) > MAXIMUM_PORTFOLIO_SOURCE_BYTES:
        raise _PortfolioBlocked("MMI_PORTFOLIO_SOURCE_OVERSIZED")
    if raw_bytes.startswith(b"\xef\xbb\xbf"):
        raise _PortfolioBlocked("MMI_PORTFOLIO_SOURCE_BOM_PROHIBITED")
    try:
        text = raw_bytes.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        raise _PortfolioBlocked(
            "MMI_PORTFOLIO_SOURCE_UTF8_INVALID"
        ) from None
    if "\x00" in text:
        raise _PortfolioBlocked(
            "MMI_PORTFOLIO_SOURCE_TEXT_INVALID"
        )
    return text


def _parse_source_header_date(
    text: str,
    *,
    evaluation_date_utc: date,
) -> str | None:
    lines = text.splitlines()
    first_section = next(
        (
            index
            for index, line in enumerate(lines)
            if _ANY_SECTION_RE.match(line)
        ),
        len(lines),
    )
    header_markers = [
        line
        for line in lines[:first_section]
        if _HEADER_MARKER_CANDIDATE_RE.match(line)
    ]
    if not header_markers:
        return None
    if len(header_markers) != 1:
        raise _PortfolioBlocked(
            "MMI_PORTFOLIO_SOURCE_TIMESTAMP_AMBIGUOUS"
        )
    match = _HEADER_DATE_RE.fullmatch(header_markers[0])
    if match is None:
        raise _PortfolioBlocked(
            "MMI_PORTFOLIO_SOURCE_TIMESTAMP_INVALID"
        )
    source_date_text = match.group("date")
    try:
        source_date = date.fromisoformat(source_date_text)
    except ValueError:
        raise _PortfolioBlocked(
            "MMI_PORTFOLIO_SOURCE_TIMESTAMP_INVALID"
        ) from None
    if source_date > evaluation_date_utc:
        raise _PortfolioBlocked(
            "MMI_PORTFOLIO_SOURCE_TIMESTAMP_FUTURE"
        )
    return source_date_text


def _strict_source_decimal(
    raw: str,
    *,
    optional: bool,
) -> tuple[str | None, Decimal | None]:
    if optional and raw == "":
        return None, None
    if (
        not raw
        or len(raw) > 64
        or _SOURCE_MONEY_RE.fullmatch(raw) is None
    ):
        raise _OpenBuyParseFailure
    try:
        decimal_value = Decimal(raw)
        normalized = normalize_decimal_string(decimal_value)
    except (InvalidOperation, MmiCanonicalizationError):
        raise _OpenBuyParseFailure from None
    if not decimal_value.is_finite() or decimal_value < 0:
        raise _OpenBuyParseFailure
    return normalized, decimal_value


def _is_data_like(line: str) -> bool:
    stripped = line.strip()
    return (
        "|" in stripped
        or _DATA_LIKE_RE.match(stripped) is not None
        or _MALFORMED_DATA_LIKE_RE.match(stripped) is not None
    )


def _strict_optional_source_date(raw: str) -> None:
    if raw == "":
        return
    if _DATE_RE.fullmatch(raw) is None:
        raise _OpenBuyParseFailure
    try:
        date.fromisoformat(raw)
    except ValueError:
        raise _OpenBuyParseFailure from None


def _strict_optional_count(raw: str) -> int | None:
    if raw == "":
        return None
    if (
        len(raw) > 3
        or _SOURCE_COUNT_RE.fullmatch(raw) is None
    ):
        raise _OpenBuyParseFailure
    count = int(raw, 10)
    if count > MAXIMUM_OPEN_BUY_RECORDS:
        raise _OpenBuyParseFailure
    return count


def _strict_named_numeric_summary(
    raw: str,
    *,
    separator: str,
) -> dict[str, Decimal]:
    if raw == "":
        return {}
    values: dict[str, Decimal] = {}
    for token in raw.split(";"):
        stripped = token.strip()
        name, found, number_text = stripped.partition(separator)
        name = name.strip()
        number_text = number_text.strip()
        if (
            not found
            or not name
            or _STEP_NAME_RE.fullmatch(name) is None
            or name in values
        ):
            raise _OpenBuyParseFailure
        _normalized, decimal_value = _strict_source_decimal(
            number_text,
            optional=False,
        )
        if decimal_value is None:
            raise _OpenBuyParseFailure
        values[name] = decimal_value
    return values


def _extract_strict_open_buy_rows(text: str) -> list[list[str]]:
    lines = text.splitlines()
    exact_starts = [
        index
        for index, line in enumerate(lines)
        if line == _PORTFOLIO_SECTION_START
    ]
    any_starts = [
        index
        for index, line in enumerate(lines)
        if line.startswith("(2a)")
    ]
    exact_ends = [
        index
        for index, line in enumerate(lines)
        if line == _PORTFOLIO_SECTION_END
    ]
    any_ends = [
        index
        for index, line in enumerate(lines)
        if line.startswith("(2b)")
    ]
    if (
        len(exact_starts) != 1
        or exact_starts != any_starts
        or len(exact_ends) != 1
        or exact_ends != any_ends
        or exact_ends[0] <= exact_starts[0]
    ):
        raise _OpenBuyParseFailure
    section = lines[exact_starts[0] + 1 : exact_ends[0]]
    header_indices = [
        index for index, line in enumerate(section) if line == _OPEN_BUY_HEADER
    ]
    similar_headers = [
        index
        for index, line in enumerate(section)
        if line.strip().startswith("TICKER")
        and "|" in line
    ]
    if len(header_indices) != 1 or header_indices != similar_headers:
        raise _OpenBuyParseFailure
    header_index = header_indices[0]
    if any(_is_data_like(line) for line in section[:header_index]):
        raise _OpenBuyParseFailure

    after_header = section[header_index + 1 :]
    first_row_index = next(
        (
            index
            for index, line in enumerate(after_header)
            if _is_data_like(line)
        ),
        None,
    )
    if first_row_index is None:
        return []
    table_region = after_header[first_row_index:]
    rows: list[list[str]] = []
    table_ended = False
    for line in table_region:
        if line.strip() == "":
            if rows:
                table_ended = True
            continue
        if table_ended:
            if _is_data_like(line):
                raise _OpenBuyParseFailure
            raise _OpenBuyParseFailure
        fields = [part.strip() for part in line.split("|")]
        if len(fields) != len(_OPEN_BUY_HEADER_COLUMNS):
            raise _OpenBuyParseFailure
        rows.append(fields)
    return rows


def _render_nonnegative_units(total_units: int, *, scale: int) -> str:
    digits = str(total_units)
    if scale:
        digits = digits.rjust(scale + 1, "0")
        rendered = (
            f"{digits[:-scale]}.{digits[-scale:]}"
        )
    else:
        rendered = digits
    try:
        return normalize_decimal_string(rendered)
    except MmiCanonicalizationError:
        raise _OpenBuyParseFailure from None


def _decimal_units(value: str) -> tuple[int, int]:
    if "." in value:
        integral, fractional = value.split(".", 1)
    else:
        integral, fractional = value, ""
    return int(integral + fractional), len(fractional)


def _sum_canonical_nonnegative_decimals(values: list[str]) -> str:
    if not values:
        return "0"
    unit_values = [_decimal_units(value) for value in values]
    maximum_scale = max(scale for _units, scale in unit_values)
    total_units = sum(
        units * (10 ** (maximum_scale - scale))
        for units, scale in unit_values
    )
    return _render_nonnegative_units(
        total_units,
        scale=maximum_scale,
    )


def _reconstruct_summary_notional(
    steps: Mapping[str, Decimal],
    quantities: Mapping[str, Decimal],
) -> str:
    products: list[tuple[int, int]] = []
    for name, price in steps.items():
        price_units, price_scale = _decimal_units(
            normalize_decimal_string(price)
        )
        quantity_units, quantity_scale = _decimal_units(
            normalize_decimal_string(quantities[name])
        )
        products.append(
            (
                price_units * quantity_units,
                price_scale + quantity_scale,
            )
        )
    if not products:
        return "0"
    maximum_scale = max(scale for _units, scale in products)
    total_units = sum(
        units * (10 ** (maximum_scale - scale))
        for units, scale in products
    )
    return _render_nonnegative_units(
        total_units,
        scale=maximum_scale,
    )


def _strict_open_buy_records(
    text: str,
    *,
    policy_roles: Mapping[str, str],
) -> tuple[list[dict[str, object]], str]:
    rows = _extract_strict_open_buy_rows(text)
    if len(rows) > MAXIMUM_OPEN_BUY_RECORDS:
        raise _OpenBuyParseFailure
    tickers: list[str] = []
    parsed_values: list[dict[str, object]] = []
    for fields in rows:
        ticker = fields[0]
        if not ticker or _TICKER_RE.fullmatch(ticker) is None:
            raise _OpenBuyParseFailure
        if ticker in tickers:
            raise _OpenBuyParseFailure
        tickers.append(ticker)
        budget_text, budget_decimal = _strict_source_decimal(
            fields[1],
            optional=False,
        )
        stated_text, stated_decimal = _strict_source_decimal(
            fields[2],
            optional=True,
        )
        residual_text, _residual_decimal = _strict_source_decimal(
            fields[3],
            optional=True,
        )
        if budget_text is None or budget_decimal is None:
            raise _OpenBuyParseFailure
        if (
            stated_decimal is not None
            and stated_decimal > budget_decimal
        ) or (
            _residual_decimal is not None
            and _residual_decimal > budget_decimal
        ):
            raise _OpenBuyParseFailure
        if (
            stated_text is not None
            and residual_text is not None
            and _sum_canonical_nonnegative_decimals(
                [stated_text, residual_text]
            )
            != budget_text
        ):
            raise _OpenBuyParseFailure
        if _TEMPLATE_ID_RE.fullmatch(fields[4]) is None:
            raise _OpenBuyParseFailure
        _anchor_text, _anchor_decimal = _strict_source_decimal(
            fields[5],
            optional=True,
        )
        _strict_optional_source_date(fields[6])
        _strict_optional_source_date(fields[7])
        _highest_text, highest_decimal = _strict_source_decimal(
            fields[8],
            optional=True,
        )
        _lowest_text, lowest_decimal = _strict_source_decimal(
            fields[9],
            optional=True,
        )
        if (
            highest_decimal is not None
            and lowest_decimal is not None
            and highest_decimal < lowest_decimal
        ):
            raise _OpenBuyParseFailure
        live_step_count = _strict_optional_count(fields[10])
        steps = _strict_named_numeric_summary(
            fields[11],
            separator="@",
        )
        quantities = _strict_named_numeric_summary(
            fields[12],
            separator=":",
        )
        if bool(steps) != bool(quantities) or set(steps) != set(quantities):
            raise _OpenBuyParseFailure
        if live_step_count is not None:
            if live_step_count != len(steps):
                raise _OpenBuyParseFailure
        if (
            stated_decimal is not None
            and steps
            and _reconstruct_summary_notional(steps, quantities)
            != stated_text
        ):
            raise _OpenBuyParseFailure
        parsed_values.append(
            {
                "ticker": ticker,
                "budget_text": budget_text,
                "budget_decimal": budget_decimal,
                "stated_text": stated_text,
                "stated_decimal": stated_decimal,
                "steps": steps,
                "quantities": quantities,
            }
        )
    try:
        with localcontext(Context(prec=256)):
            parser_result = parse_existing_buy_open_orders_summary(
                text
            )
    except Exception:
        raise _OpenBuyParseFailure from None
    if (
        parser_result.section_present is not True
        or parser_result.diagnostics
        or list(parser_result.orders) != tickers
    ):
        raise _OpenBuyParseFailure
    records: list[dict[str, object]] = []
    for expected in parsed_values:
        ticker = expected["ticker"]
        budget_text = expected["budget_text"]
        budget_decimal = expected["budget_decimal"]
        stated_text = expected["stated_text"]
        stated_decimal = expected["stated_decimal"]
        steps = expected["steps"]
        quantities = expected["quantities"]
        if (
            type(ticker) is not str
            or type(budget_text) is not str
            or type(budget_decimal) is not Decimal
            or (
                stated_text is not None
                and type(stated_text) is not str
            )
            or (
                stated_decimal is not None
                and type(stated_decimal) is not Decimal
            )
            or type(steps) is not dict
            or type(quantities) is not dict
        ):
            raise _OpenBuyParseFailure
        parsed = parser_result.orders.get(ticker)
        if (
            parsed is None
            or parsed.data_gap
            or parsed.diagnostics
            or parsed.ticker != ticker
            or parsed.budget != budget_decimal
            or parsed.stated_compiled_notional != stated_decimal
            or parsed.steps != steps
            or parsed.qtys != quantities
            or (
                stated_decimal is not None
                and steps
                and parsed.reconstructed_notional
                != stated_decimal
            )
        ):
            raise _OpenBuyParseFailure
        role = policy_roles.get(ticker)
        outside = role is None
        records.append(
            {
                "ticker": ticker,
                "reserved_budget_decimal": budget_text,
                "stated_compiled_notional_decimal": stated_text,
                "policy_membership_classification": (
                    "OUTSIDE_POLICY_UNIVERSE" if outside else role
                ),
                "policy_role_annotation": role,
                "outside_policy_universe": outside,
            }
        )
    total = _sum_canonical_nonnegative_decimals(
        [
            record["reserved_budget_decimal"]
            for record in records
            if type(record.get("reserved_budget_decimal")) is str
        ]
    )
    return records, total


def _gap_record(
    code: str,
    *,
    affected_tickers: list[str],
    policy_projection_identity_sha256: str,
    portfolio_source_record_identity_sha256: str | None,
) -> dict[str, object]:
    if code not in _GAP_CODE_SET:
        raise _PortfolioContractFailure(
            "MMI_PORTFOLIO_GAP_CONTRACT_MISMATCH"
        )
    return {
        "code": code,
        "scope": "PORTFOLIO_SNAPSHOT",
        "affected_tickers": list(affected_tickers),
        "policy_projection_identity_sha256": (
            policy_projection_identity_sha256
        ),
        "portfolio_source_record_identity_sha256": (
            portfolio_source_record_identity_sha256
        ),
    }


def _derive_expected_gaps(
    value: Mapping[str, object],
) -> list[dict[str, object]]:
    policy_identity = value.get(
        "policy_projection_identity_sha256"
    )
    source_identity = value.get(
        "portfolio_source_record_identity_sha256"
    )
    source_status = value.get("portfolio_source_status")
    source_date = value.get("portfolio_source_date")
    open_buy = value.get("open_buy_orders")
    if (
        type(policy_identity) is not str
        or (
            source_identity is not None
            and type(source_identity) is not str
        )
        or type(open_buy) is not dict
    ):
        raise _PortfolioContractFailure(
            "MMI_PORTFOLIO_GAP_CONTRACT_MISMATCH"
        )
    open_status = open_buy.get("status")
    records = open_buy.get("records")
    if type(records) is not list:
        raise _PortfolioContractFailure(
            "MMI_PORTFOLIO_GAP_CONTRACT_MISMATCH"
        )
    outside_tickers = [
        record["ticker"]
        for record in records
        if type(record) is dict
        and record.get("outside_policy_universe") is True
        and type(record.get("ticker")) is str
    ]
    active_codes: dict[str, list[str]] = {
        code: [] for code in _STATIC_GAP_CODES
    }
    if source_status == "SOURCE_ABSENT":
        active_codes["PORTFOLIO_SOURCE_MISSING"] = []
    elif source_status == "SOURCE_PRESENT_CONTENT_BOUND":
        if source_date is None:
            active_codes[
                "PORTFOLIO_SOURCE_TIMESTAMP_UNAVAILABLE"
            ] = []
    else:
        raise _PortfolioContractFailure(
            "MMI_PORTFOLIO_GAP_CONTRACT_MISMATCH"
        )
    if open_status == "PARSE_FAILED":
        active_codes["PORTFOLIO_OPEN_BUY_ORDERS_PARSE_FAILED"] = []
    elif open_status not in {"SOURCE_ABSENT", "SOURCE_VALIDATED"}:
        raise _PortfolioContractFailure(
            "MMI_PORTFOLIO_GAP_CONTRACT_MISMATCH"
        )
    if outside_tickers:
        active_codes[
            "PORTFOLIO_OPEN_BUY_ORDER_OUTSIDE_POLICY_UNIVERSE"
        ] = outside_tickers
    return [
        _gap_record(
            code,
            affected_tickers=active_codes[code],
            policy_projection_identity_sha256=policy_identity,
            portfolio_source_record_identity_sha256=source_identity,
        )
        for code in _GAP_CODE_ORDER
        if code in active_codes
    ]


def _base_projection(
    *,
    evaluation_timestamp_utc: str,
    policy_projection_identity_sha256: str,
    portfolio_source_status: str,
    portfolio_source_record_identity_sha256: str | None,
    portfolio_source_date: str | None,
    open_buy_status: str,
    open_buy_records: list[dict[str, object]],
    total_reserved_budget_decimal: str | None,
) -> dict[str, object]:
    projection: dict[str, object] = {
        "schema_version": "mmi_portfolio_snapshot_projection_v1",
        "projection_kind": "MMI_PORTFOLIO_SNAPSHOT_PROJECTION",
        "report_only": True,
        "authority_effect": AUTHORITY_EFFECT_NONE,
        "evaluation_timestamp_utc": evaluation_timestamp_utc,
        "policy_projection_identity_sha256": (
            policy_projection_identity_sha256
        ),
        "portfolio_source_status": portfolio_source_status,
        "portfolio_source_record_identity_sha256": (
            portfolio_source_record_identity_sha256
        ),
        "portfolio_source_date": portfolio_source_date,
        "holdings": {
            "status": _UNSTRUCTURED_HOLDINGS["status"],
            "records": [],
        },
        "open_buy_orders": {
            "status": open_buy_status,
            "records": open_buy_records,
            "total_reserved_budget_decimal": (
                total_reserved_budget_decimal
            ),
        },
        "cash": dict(_UNAVAILABLE_AREA),
        "deployable_cash": dict(_UNAVAILABLE_AREA),
        "open_sell_orders": {
            "status": _UNSTRUCTURED_OPEN_SELLS["status"],
            "records": [],
        },
        "tax_lots": {
            "status": _UNSTRUCTURED_TAX_LOTS["status"],
            "records": [],
        },
        "holding_dates": dict(_UNAVAILABLE_AREA),
        "gains_losses": dict(_UNAVAILABLE_AREA),
        "weights": dict(_UNAVAILABLE_AREA),
        "nav_concentration": dict(_UNAVAILABLE_AREA),
        "lookthrough_exposure": dict(_UNAVAILABLE_AREA),
        "known_gaps": [],
        "completeness_status": "PROJECTION_VALID_WITH_GAPS",
    }
    projection["known_gaps"] = _derive_expected_gaps(projection)
    projection["portfolio_projection_identity_sha256"] = (
        record_identity_sha256(
            projection,
            identity_field="portfolio_projection_identity_sha256",
            domain=(
                MMI_PORTFOLIO_SNAPSHOT_PROJECTION_IDENTITY_DOMAIN
            ),
            maximum_bytes=MAXIMUM_PORTFOLIO_CANONICAL_BYTES,
        )
    )
    return projection


def _build_mmi_portfolio_snapshot_projection_from_source_bytes(
    raw_bytes: bytes | None,
    *,
    source_record_identity_sha256: str | None,
    policy_projection_identity_sha256: str,
    policy_roles: dict[str, str],
    run_context: MmiProjectionRunContext,
) -> tuple[dict[str, object], dict[str, str]]:
    if raw_bytes is None:
        return (
            _base_projection(
                evaluation_timestamp_utc=(
                    run_context.evaluation_timestamp_utc
                ),
                policy_projection_identity_sha256=policy_projection_identity_sha256,
                portfolio_source_status="SOURCE_ABSENT",
                portfolio_source_record_identity_sha256=None,
                portfolio_source_date=None,
                open_buy_status="SOURCE_ABSENT",
                open_buy_records=[],
                total_reserved_budget_decimal=None,
            ),
            policy_roles,
        )
    text = _decode_portfolio_source(raw_bytes)
    source_date = _parse_source_header_date(
        text,
        evaluation_date_utc=run_context.evaluation_time_utc.date(),
    )
    try:
        records, total = _strict_open_buy_records(
            text,
            policy_roles=policy_roles,
        )
        open_status = "SOURCE_VALIDATED"
    except _OpenBuyParseFailure:
        records = []
        total = None
        open_status = "PARSE_FAILED"
    if type(source_record_identity_sha256) is not str:
        raise _PortfolioContractFailure(
            "MMI_PORTFOLIO_SOURCE_RECORD_CONTRACT_INVALID"
        )
    return (
        _base_projection(
            evaluation_timestamp_utc=(
                run_context.evaluation_timestamp_utc
            ),
            policy_projection_identity_sha256=policy_projection_identity_sha256,
            portfolio_source_status="SOURCE_PRESENT_CONTENT_BOUND",
            portfolio_source_record_identity_sha256=source_record_identity_sha256,
            portfolio_source_date=source_date,
            open_buy_status=open_status,
            open_buy_records=records,
            total_reserved_budget_decimal=total,
        ),
        policy_roles,
    )


def _require_canonical_nonnegative_decimal(
    value: object,
    *,
    code: str,
) -> str:
    if type(value) is not str:
        raise _PortfolioContractFailure(code)
    try:
        normalized = normalize_decimal_string(value)
        decimal_value = Decimal(value)
    except (InvalidOperation, MmiCanonicalizationError):
        raise _PortfolioContractFailure(code) from None
    if (
        normalized != value
        or not decimal_value.is_finite()
        or decimal_value < 0
    ):
        raise _PortfolioContractFailure(code)
    return value


def _validate_record_semantics(
    record: object,
    *,
    policy_roles: Mapping[str, str],
) -> str:
    code = "MMI_PORTFOLIO_OPEN_BUY_RECORD_CONTRACT_INVALID"
    if type(record) is not dict:
        raise _PortfolioContractFailure(code)
    ticker = record.get("ticker")
    if type(ticker) is not str or _TICKER_RE.fullmatch(ticker) is None:
        raise _PortfolioContractFailure(code)
    reserved = _require_canonical_nonnegative_decimal(
        record.get("reserved_budget_decimal"),
        code=code,
    )
    stated = record.get("stated_compiled_notional_decimal")
    if stated is not None:
        _require_canonical_nonnegative_decimal(stated, code=code)
        if Decimal(stated) > Decimal(reserved):
            raise _PortfolioContractFailure(code)
    expected_role = policy_roles.get(ticker)
    outside = expected_role is None
    if (
        record.get("policy_membership_classification")
        != (
            "OUTSIDE_POLICY_UNIVERSE"
            if outside
            else expected_role
        )
        or record.get("policy_role_annotation") != expected_role
        or record.get("outside_policy_universe") is not outside
    ):
        raise _PortfolioContractFailure(code)
    return reserved


def _validate_portfolio_semantics(
    value: Mapping[str, object],
    *,
    policy_projection_identity_sha256: str,
    policy_roles: Mapping[str, str],
    run_context: MmiProjectionRunContext,
) -> None:
    code = "MMI_PORTFOLIO_PROJECTION_SEMANTIC_INVALID"
    if (
        value.get("report_only") is not True
        or value.get("authority_effect") != AUTHORITY_EFFECT_NONE
        or value.get("evaluation_timestamp_utc")
        != run_context.evaluation_timestamp_utc
        or value.get("policy_projection_identity_sha256")
        != policy_projection_identity_sha256
        or value.get("completeness_status")
        != "PROJECTION_VALID_WITH_GAPS"
        or value.get("holdings") != _UNSTRUCTURED_HOLDINGS
        or value.get("cash") != _UNAVAILABLE_AREA
        or value.get("deployable_cash") != _UNAVAILABLE_AREA
        or value.get("open_sell_orders") != _UNSTRUCTURED_OPEN_SELLS
        or value.get("tax_lots") != _UNSTRUCTURED_TAX_LOTS
        or value.get("holding_dates") != _UNAVAILABLE_AREA
        or value.get("gains_losses") != _UNAVAILABLE_AREA
        or value.get("weights") != _UNAVAILABLE_AREA
        or value.get("nav_concentration") != _UNAVAILABLE_AREA
        or value.get("lookthrough_exposure") != _UNAVAILABLE_AREA
    ):
        raise _PortfolioContractFailure(code)
    source_status = value.get("portfolio_source_status")
    source_identity = value.get(
        "portfolio_source_record_identity_sha256"
    )
    source_date_text = value.get("portfolio_source_date")
    if source_status == "SOURCE_ABSENT":
        if source_identity is not None or source_date_text is not None:
            raise _PortfolioContractFailure(code)
    elif source_status == "SOURCE_PRESENT_CONTENT_BOUND":
        if (
            type(source_identity) is not str
            or _SHA256_RE.fullmatch(source_identity) is None
        ):
            raise _PortfolioContractFailure(code)
        if source_date_text is not None:
            if (
                type(source_date_text) is not str
                or _DATE_RE.fullmatch(source_date_text) is None
            ):
                raise _PortfolioContractFailure(code)
            try:
                source_date = date.fromisoformat(source_date_text)
            except ValueError:
                raise _PortfolioContractFailure(code) from None
            if source_date > run_context.evaluation_time_utc.date():
                raise _PortfolioContractFailure(code)
    else:
        raise _PortfolioContractFailure(code)

    open_buy = value.get("open_buy_orders")
    if type(open_buy) is not dict:
        raise _PortfolioContractFailure(code)
    status = open_buy.get("status")
    records = open_buy.get("records")
    total = open_buy.get("total_reserved_budget_decimal")
    if type(records) is not list:
        raise _PortfolioContractFailure(code)
    if status == "SOURCE_VALIDATED":
        if source_status != "SOURCE_PRESENT_CONTENT_BOUND":
            raise _PortfolioContractFailure(code)
        tickers: list[str] = []
        reserved_values: list[str] = []
        for record in records:
            reserved = _validate_record_semantics(
                record,
                policy_roles=policy_roles,
            )
            ticker = record["ticker"]
            if ticker in tickers:
                raise _PortfolioContractFailure(code)
            tickers.append(ticker)
            reserved_values.append(reserved)
        try:
            expected_total = _sum_canonical_nonnegative_decimals(
                reserved_values
            )
        except _OpenBuyParseFailure:
            raise _PortfolioContractFailure(code) from None
        if total != expected_total:
            raise _PortfolioContractFailure(code)
    elif status == "PARSE_FAILED":
        if (
            source_status != "SOURCE_PRESENT_CONTENT_BOUND"
            or records != []
            or total is not None
        ):
            raise _PortfolioContractFailure(code)
    elif status == "SOURCE_ABSENT":
        if (
            source_status != "SOURCE_ABSENT"
            or records != []
            or total is not None
        ):
            raise _PortfolioContractFailure(code)
    else:
        raise _PortfolioContractFailure(code)

    gaps = value.get("known_gaps")
    expected_gaps = _derive_expected_gaps(value)
    if type(gaps) is not list or gaps != expected_gaps or not gaps:
        raise _PortfolioContractFailure(
            "MMI_PORTFOLIO_GAP_CONTRACT_MISMATCH"
        )
    try:
        expected_identity = record_identity_sha256(
            dict(value),
            identity_field="portfolio_projection_identity_sha256",
            domain=(
                MMI_PORTFOLIO_SNAPSHOT_PROJECTION_IDENTITY_DOMAIN
            ),
            maximum_bytes=MAXIMUM_PORTFOLIO_CANONICAL_BYTES,
        )
    except MmiCanonicalizationError:
        raise _PortfolioContractFailure(
            "MMI_PORTFOLIO_PROJECTION_IDENTITY_INVALID"
        ) from None
    if value.get("portfolio_projection_identity_sha256") != expected_identity:
        raise _PortfolioContractFailure(
            "MMI_PORTFOLIO_PROJECTION_IDENTITY_INVALID"
        )


def build_mmi_portfolio_snapshot_projection(
    portfolio_source: MmiCapturedSource | None,
    *,
    policy_projection: Mapping[str, object],
    policy_source: MmiCapturedSource,
    run_context: MmiProjectionRunContext,
) -> MmiPortfolioProjectionBuildResult:
    """Build one deterministic report-only projection from trusted inputs."""
    try:
        policy_identity, policy_roles = _validate_policy_inputs(
            policy_projection,
            policy_source=policy_source,
            run_context=run_context,
        )
        if portfolio_source is None:
            raw_bytes = None
            source_identity = None
        else:
            source_record = _validate_portfolio_source(portfolio_source)
            raw_bytes = portfolio_source.raw_bytes
            source_identity = source_record.get(
                "source_record_identity_sha256"
            )
            if type(source_identity) is not str:
                raise _PortfolioContractFailure(
                    "MMI_PORTFOLIO_SOURCE_RECORD_CONTRACT_INVALID"
                )
        projection, policy_roles = _build_mmi_portfolio_snapshot_projection_from_source_bytes(
            raw_bytes,
            source_record_identity_sha256=source_identity,
            policy_projection_identity_sha256=policy_identity,
            policy_roles=policy_roles,
            run_context=run_context,
        )
        policy_identity = projection.get(
            "policy_projection_identity_sha256"
        )
        if type(policy_identity) is not str:
            raise _PortfolioContractFailure(
                "MMI_PORTFOLIO_PROJECTION_CONTRACT_FAILURE"
            )
        try:
            validate_artifact_schema(
                projection,
                schema_name=_PORTFOLIO_SCHEMA_NAME,
            )
            _validate_portfolio_semantics(
                projection,
                policy_projection_identity_sha256=policy_identity,
                policy_roles=policy_roles,
                run_context=run_context,
            )
        except _PortfolioContractFailure:
            raise
        except Exception:
            raise _PortfolioContractFailure(
                "MMI_PORTFOLIO_PROJECTION_CONTRACT_FAILURE"
            ) from None
    except _PortfolioBlocked as exc:
        return _build_result(
            MmiProjectionResultCategory.PROJECTION_BLOCKED,
            exc.code,
        )
    except _PortfolioContractFailure as exc:
        return _build_result(
            MmiProjectionResultCategory.PROJECTION_CONTRACT_FAILURE,
            exc.code,
        )
    except Exception:
        return _build_result(
            MmiProjectionResultCategory.PROJECTION_CONTRACT_FAILURE,
            "MMI_PORTFOLIO_PROJECTION_INTERNAL_INVARIANT_FAILED",
        )
    gaps = projection["known_gaps"]
    return _build_result(
        MmiProjectionResultCategory.PROJECTION_VALID_WITH_GAPS,
        *(gap["code"] for gap in gaps),
        projection=projection,
    )


def _validate_mmi_portfolio_snapshot_projection_from_source_bytes(
    value: object,
    *,
    raw_bytes: bytes | None,
    source_record_identity_sha256: str | None,
    policy_projection_identity_sha256: str,
    policy_roles: dict[str, str],
    run_context: MmiProjectionRunContext,
) -> MmiPortfolioProjectionValidationResult:
    try:
        expected, policy_roles_ignored = _build_mmi_portfolio_snapshot_projection_from_source_bytes(
            raw_bytes,
            source_record_identity_sha256=source_record_identity_sha256,
            policy_projection_identity_sha256=policy_projection_identity_sha256,
            policy_roles=policy_roles,
            run_context=run_context,
        )
    except _PortfolioBlocked as exc:
        return _validation_result(
            MmiProjectionResultCategory.PROJECTION_BLOCKED,
            exc.code,
        )
    except _PortfolioContractFailure as exc:
        return _validation_result(
            MmiProjectionResultCategory.PROJECTION_CONTRACT_FAILURE,
            exc.code,
        )
    except Exception:
        return _validation_result(
            MmiProjectionResultCategory.PROJECTION_CONTRACT_FAILURE,
            "MMI_PORTFOLIO_PROJECTION_INTERNAL_INVARIANT_FAILED",
        )
    if type(value) is not dict:
        return _validation_result(
            MmiProjectionResultCategory.PROJECTION_BLOCKED,
            "MMI_PORTFOLIO_PROJECTION_SCHEMA_INVALID",
        )
    try:
        validate_artifact_schema(
            value,
            schema_name=_PORTFOLIO_SCHEMA_NAME,
        )
    except Exception:
        return _validation_result(
            MmiProjectionResultCategory.PROJECTION_BLOCKED,
            "MMI_PORTFOLIO_PROJECTION_SCHEMA_INVALID",
        )
    policy_identity = expected.get(
        "policy_projection_identity_sha256"
    )
    if type(policy_identity) is not str:
        return _validation_result(
            MmiProjectionResultCategory.PROJECTION_CONTRACT_FAILURE,
            "MMI_PORTFOLIO_PROJECTION_INTERNAL_INVARIANT_FAILED",
        )
    try:
        _validate_portfolio_semantics(
            value,
            policy_projection_identity_sha256=policy_identity,
            policy_roles=policy_roles,
            run_context=run_context,
        )
    except _PortfolioContractFailure as exc:
        return _validation_result(
            MmiProjectionResultCategory.PROJECTION_CONTRACT_FAILURE,
            exc.code,
        )
    if value != expected:
        return _validation_result(
            MmiProjectionResultCategory.PROJECTION_CONTRACT_FAILURE,
            "MMI_PORTFOLIO_SOURCE_FIDELITY_MISMATCH",
        )
    return _validation_result(
        MmiProjectionResultCategory.PROJECTION_VALID_WITH_GAPS
    )


def validate_mmi_portfolio_snapshot_projection(
    value: object,
    *,
    portfolio_source: MmiCapturedSource | None,
    policy_projection: Mapping[str, object],
    policy_source: MmiCapturedSource,
    run_context: MmiProjectionRunContext,
) -> MmiPortfolioProjectionValidationResult:
    """Validate a candidate against the exact same-run trusted inputs."""
    try:
        policy_identity, policy_roles = _validate_policy_inputs(
            policy_projection,
            policy_source=policy_source,
            run_context=run_context,
        )
        if portfolio_source is None:
            raw_bytes = None
            source_identity = None
        else:
            source_record = _validate_portfolio_source(portfolio_source)
            raw_bytes = portfolio_source.raw_bytes
            source_identity = source_record.get(
                "source_record_identity_sha256"
            )
            if type(source_identity) is not str:
                raise _PortfolioContractFailure(
                    "MMI_PORTFOLIO_SOURCE_RECORD_CONTRACT_INVALID"
                )
    except _PortfolioBlocked as exc:
        return _validation_result(
            MmiProjectionResultCategory.PROJECTION_BLOCKED,
            exc.code,
        )
    except _PortfolioContractFailure as exc:
        return _validation_result(
            MmiProjectionResultCategory.PROJECTION_CONTRACT_FAILURE,
            exc.code,
        )
    except Exception:
        return _validation_result(
            MmiProjectionResultCategory.PROJECTION_CONTRACT_FAILURE,
            "MMI_PORTFOLIO_PROJECTION_INTERNAL_INVARIANT_FAILED",
        )
    return _validate_mmi_portfolio_snapshot_projection_from_source_bytes(
        value,
        raw_bytes=raw_bytes,
        source_record_identity_sha256=source_identity,
        policy_projection_identity_sha256=policy_identity,
        policy_roles=policy_roles,
        run_context=run_context,
    )
