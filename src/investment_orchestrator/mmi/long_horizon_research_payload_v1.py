"""Dormant role-specific payload contract for manual long-horizon ETF research.

This module defines the strict, closed, role-specific payload schema and reader
for operator-supplied long-horizon research (MMI source role
``LONG_HORIZON_RESEARCH``).

Authority & Provenance Boundary:
--------------------------------
- ``publisher`` and ``source_locator`` represent operator-supplied declared
  provenance metadata. They do not constitute cryptographic or third-party
  authenticated publisher verification.
- ``excerpt_text`` represents operator-supplied verbatim excerpt text. The
  repository verifies that the operator supplied these exact bytes locally; it
  performs no semantic, NLP, keyword, or external document verification.
- Intrinsic syntax only: ``published_at`` is strictly validated as a canonical
  calendar date string (``YYYY-MM-DD``). Evaluation-time freshness, future-date
  policies, and staleness thresholds are not evaluated here and belong strictly
  to downstream orchestrators.
- Ticker domain: ``tickers`` is strictly validated for syntax and uniqueness,
  preserving declared order. Candidate-domain binding and completeness against
  ``analysis_scope_instruments`` belong strictly to downstream projections.
- No investment authority: This module carries no availability status, no
  freshness status, no prompt visibility, and no permission, disposition,
  budget, or order authority.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
import json
import re
from typing import Final, NoReturn

from investment_orchestrator.common.schema_validation import (
    validate_artifact_schema,
)
from investment_orchestrator.mmi.contracts import (
    MmiCapturedSource,
    MmiSourceRole,
    _mmi_captured_source_provenance_is_valid,
)


__all__ = (
    "MMI_LONG_HORIZON_RESEARCH_PAYLOAD_V1_SCHEMA_NAME",
    "MMI_LONG_HORIZON_RESEARCH_PAYLOAD_V1_SCHEMA_VERSION",
    "MmiLongHorizonResearchPayload",
    "MmiLongHorizonResearchPayloadError",
    "parse_mmi_long_horizon_research_payload_v1",
    "read_mmi_long_horizon_research_payload_v1_from_captured_source",
    "validate_mmi_long_horizon_research_payload_v1",
)

MMI_LONG_HORIZON_RESEARCH_PAYLOAD_V1_SCHEMA_VERSION: Final = (
    "mmi_long_horizon_research_payload_v1"
)
MMI_LONG_HORIZON_RESEARCH_PAYLOAD_V1_SCHEMA_NAME: Final = (
    "mmi_long_horizon_research_payload_v1.schema.json"
)

_REQUIRED_PAYLOAD_FIELDS: Final = frozenset(
    {
        "schema_version",
        "publisher",
        "published_at",
        "source_locator",
        "tickers",
        "excerpt_text",
    }
)

_TICKER_RE: Final = re.compile(r"^[A-Z][A-Z0-9.-]{0,15}$")
_DATE_RE: Final = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class MmiLongHorizonResearchPayloadError(ValueError):
    """Raised when an MMI long-horizon research payload violates its contract."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _fail(code: str) -> NoReturn:
    raise MmiLongHorizonResearchPayloadError(code) from None


@dataclass(frozen=True, slots=True)
class MmiLongHorizonResearchPayload:
    """One validated manual long-horizon research payload.

    Carries only intrinsic validated payload facts from operator-supplied
    verbatim research excerpts and declared provenance metadata.

    Does not own evaluation-time freshness, availability, candidate-domain
    binding, prompt visibility, or any permission/disposition authority.
    """

    schema_version: str
    publisher: str
    published_at: str
    source_locator: str
    tickers: tuple[str, ...]
    excerpt_text: str


def validate_mmi_long_horizon_research_payload_v1(
    value: object,
) -> MmiLongHorizonResearchPayload:
    """Validate a parsed mapping against the strict payload schema."""
    if not isinstance(value, Mapping) or type(value) is not dict:
        _fail("MMI_LONG_HORIZON_RESEARCH_PAYLOAD_NOT_MAPPING")

    # Closed schema check: no missing keys, no extra keys.
    keys = set(value.keys())
    if any(type(k) is not str for k in keys):
        _fail("MMI_LONG_HORIZON_RESEARCH_PAYLOAD_KEYS_INVALID")
    if keys != _REQUIRED_PAYLOAD_FIELDS:
        _fail("MMI_LONG_HORIZON_RESEARCH_PAYLOAD_KEYS_INVALID")

    # Schema version
    schema_version = value["schema_version"]
    if (
        type(schema_version) is not str
        or schema_version != MMI_LONG_HORIZON_RESEARCH_PAYLOAD_V1_SCHEMA_VERSION
    ):
        _fail("MMI_LONG_HORIZON_RESEARCH_PAYLOAD_SCHEMA_VERSION_INVALID")

    # Publisher (operator-declared provenance)
    publisher = value["publisher"]
    if type(publisher) is not str or len(publisher) == 0 or publisher.strip() == "":
        _fail("MMI_LONG_HORIZON_RESEARCH_PAYLOAD_PUBLISHER_INVALID")

    # Published date (calendar syntax only; no freshness or clock checks)
    published_at = value["published_at"]
    if type(published_at) is not str or not _DATE_RE.fullmatch(published_at):
        _fail("MMI_LONG_HORIZON_RESEARCH_PAYLOAD_PUBLISHED_AT_INVALID")
    try:
        parsed_date = date.fromisoformat(published_at)
    except ValueError:
        _fail("MMI_LONG_HORIZON_RESEARCH_PAYLOAD_PUBLISHED_AT_INVALID")
    if parsed_date.isoformat() != published_at:
        _fail("MMI_LONG_HORIZON_RESEARCH_PAYLOAD_PUBLISHED_AT_INVALID")

    # Source locator (operator-declared locator)
    source_locator = value["source_locator"]
    if (
        type(source_locator) is not str
        or len(source_locator) == 0
        or source_locator.strip() == ""
    ):
        _fail("MMI_LONG_HORIZON_RESEARCH_PAYLOAD_SOURCE_LOCATOR_INVALID")

    # Tickers list (syntax and uniqueness; candidate domain checked downstream)
    tickers_val = value["tickers"]
    if type(tickers_val) is not list or len(tickers_val) == 0:
        _fail("MMI_LONG_HORIZON_RESEARCH_PAYLOAD_TICKERS_INVALID")

    seen_tickers: set[str] = set()
    validated_tickers: list[str] = []
    for item in tickers_val:
        if type(item) is not str or not _TICKER_RE.fullmatch(item):
            _fail("MMI_LONG_HORIZON_RESEARCH_PAYLOAD_TICKERS_INVALID")
        if item in seen_tickers:
            _fail("MMI_LONG_HORIZON_RESEARCH_PAYLOAD_TICKERS_DUPLICATE")
        seen_tickers.add(item)
        validated_tickers.append(item)

    # Excerpt text (operator-supplied verbatim excerpt text)
    excerpt_text = value["excerpt_text"]
    if (
        type(excerpt_text) is not str
        or len(excerpt_text) == 0
        or excerpt_text.strip() == ""
    ):
        _fail("MMI_LONG_HORIZON_RESEARCH_PAYLOAD_EXCERPT_TEXT_INVALID")

    # JSON schema validator check
    try:
        validate_artifact_schema(
            value,
            schema_name=MMI_LONG_HORIZON_RESEARCH_PAYLOAD_V1_SCHEMA_NAME,
        )
    except Exception:
        _fail("MMI_LONG_HORIZON_RESEARCH_PAYLOAD_SCHEMA_VIOLATION")

    return MmiLongHorizonResearchPayload(
        schema_version=schema_version,
        publisher=publisher,
        published_at=published_at,
        source_locator=source_locator,
        tickers=tuple(validated_tickers),
        excerpt_text=excerpt_text,
    )


def parse_mmi_long_horizon_research_payload_v1(
    raw_bytes: bytes | str,
) -> MmiLongHorizonResearchPayload:
    """Parse and strictly validate raw JSON bytes or text into a typed payload."""
    if type(raw_bytes) is bytes:
        try:
            text = raw_bytes.decode("utf-8")
        except UnicodeDecodeError:
            _fail("MMI_LONG_HORIZON_RESEARCH_PAYLOAD_JSON_INVALID")
    elif type(raw_bytes) is str:
        text = raw_bytes
    else:
        _fail("MMI_LONG_HORIZON_RESEARCH_PAYLOAD_NOT_MAPPING")

    try:
        parsed = json.loads(text)
    except Exception:
        _fail("MMI_LONG_HORIZON_RESEARCH_PAYLOAD_JSON_INVALID")

    return validate_mmi_long_horizon_research_payload_v1(parsed)


def read_mmi_long_horizon_research_payload_v1_from_captured_source(
    source: MmiCapturedSource,
) -> MmiLongHorizonResearchPayload:
    """Extract and validate the long-horizon research payload from a captured source."""
    if not _mmi_captured_source_provenance_is_valid(source):
        _fail("MMI_LONG_HORIZON_RESEARCH_PAYLOAD_PROVENANCE_INVALID")
    if source.role is not MmiSourceRole.LONG_HORIZON_RESEARCH:
        _fail("MMI_LONG_HORIZON_RESEARCH_PAYLOAD_ROLE_INVALID")
    return parse_mmi_long_horizon_research_payload_v1(source.raw_bytes)
