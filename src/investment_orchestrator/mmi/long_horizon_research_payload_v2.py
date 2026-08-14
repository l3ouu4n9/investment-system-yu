"""Dormant versioned multi-entry payload contract for manual long-horizon research.

This module defines the strict, closed, role-specific payload schema and
reader for a *bundle* of operator-supplied long-horizon research entries (MMI
source role ``LONG_HORIZON_RESEARCH``, same fixed locator as V1).

Relationship to V1
-------------------
``mmi_long_horizon_research_payload_v1`` remains permanently exactly one
source object and is entirely unmodified by this module. V2 is an additive,
independent contract: ``mmi_long_horizon_research_payload_v2`` is a closed
container holding a non-empty array of source entries. There is no V1->V2
coercion, normalization, or migration; this module does not import, extend,
or share code with the V1 module beyond the identical intrinsic ticker/date
syntax rules restated here.

Authority & Provenance Boundary:
--------------------------------
- ``publisher`` and ``source_locator`` (per entry) represent operator-supplied
  declared provenance metadata. They do not constitute cryptographic or
  third-party authenticated publisher verification.
- ``excerpt_text`` (per entry) represents operator-supplied verbatim excerpt
  text. The repository verifies that the operator supplied these exact bytes
  locally; it performs no semantic, NLP, keyword, or external document
  verification.
- Intrinsic syntax only: ``published_at`` (per entry) is strictly validated as
  a canonical calendar date string (``YYYY-MM-DD``). Evaluation-time
  freshness, future-date policies, and staleness thresholds are not evaluated
  here and belong strictly to downstream orchestrators.
- Ticker domain: ``tickers`` (per entry) is strictly validated for syntax and
  uniqueness *within that entry*, preserving declared order. Overlapping
  tickers across *different* entries are intentionally permitted -- multiple
  independent operator-supplied sources may cover the same candidate.
  Candidate-domain binding and completeness against
  ``analysis_scope_instruments`` belong strictly to downstream projections.
- No investment authority: This module carries no availability status, no
  freshness status, no prompt visibility, and no permission, disposition,
  budget, or order authority.

Entry identity
--------------
Each validated entry carries a code-generated ``source_entry_identity_sha256``
computed by ``domain_separated_sha256`` over exactly that entry's five
semantic fields, under a narrow versioned domain separator. It is a stable
canonical source-entry identity -- NOT a raw external document hash, NOT
publisher authenticity, NOT URL verification, and NOT an external excerpt
match. It never depends on array index, bundle composition, bundle entry
ordering, or any bundle-level identity (``observed_sha256`` /
``source_record_identity_sha256``).

Ticker list ORDER is intentionally canonicalized (sorted) inside the entry
identity preimage only, because declared order is presentation-only and not a
semantic fact of the entry; the stored ``tickers`` tuple on the typed entry
always preserves the operator's declared order verbatim. Two entries whose
five semantic fields are identical except for ticker order therefore share
one entry identity and are treated as a duplicate entry (see below).

Duplicate entries
------------------
Two source entries that produce the same ``source_entry_identity_sha256``
are rejected: feeding the same research entry into the bundle twice (even
under a mere ticker-order permutation) could silently overweight it in a
future prompt. Duplicates are never silently collapsed; the whole payload
fails closed.
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
from investment_orchestrator.mmi.canonical import (
    _MMI_LONG_HORIZON_RESEARCH_SOURCE_ENTRY_V1_IDENTITY_DOMAIN,
    MmiCanonicalizationError,
    domain_separated_sha256,
)
from investment_orchestrator.mmi.contracts import (
    MmiCapturedSource,
    MmiSourceRole,
    _mmi_captured_source_provenance_is_valid,
)


__all__ = (
    "MMI_LONG_HORIZON_RESEARCH_PAYLOAD_V2_SCHEMA_NAME",
    "MMI_LONG_HORIZON_RESEARCH_PAYLOAD_V2_SCHEMA_VERSION",
    "MmiLongHorizonResearchPayloadV2",
    "MmiLongHorizonResearchPayloadV2Error",
    "MmiLongHorizonResearchSourceEntry",
    "parse_mmi_long_horizon_research_payload_v2",
    "read_mmi_long_horizon_research_payload_v2_from_captured_source",
    "validate_mmi_long_horizon_research_payload_v2",
)

MMI_LONG_HORIZON_RESEARCH_PAYLOAD_V2_SCHEMA_VERSION: Final = (
    "mmi_long_horizon_research_payload_v2"
)
MMI_LONG_HORIZON_RESEARCH_PAYLOAD_V2_SCHEMA_NAME: Final = (
    "mmi_long_horizon_research_payload_v2.schema.json"
)

_REQUIRED_PAYLOAD_FIELDS: Final = frozenset({"schema_version", "sources"})
_REQUIRED_SOURCE_ENTRY_FIELDS: Final = frozenset(
    {
        "publisher",
        "published_at",
        "source_locator",
        "tickers",
        "excerpt_text",
    }
)

_TICKER_RE: Final = re.compile(r"^[A-Z][A-Z0-9.-]{0,15}$")
_DATE_RE: Final = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class MmiLongHorizonResearchPayloadV2Error(ValueError):
    """Raised when an MMI long-horizon research payload V2 violates its contract."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _fail(code: str) -> NoReturn:
    raise MmiLongHorizonResearchPayloadV2Error(code) from None


@dataclass(frozen=True, slots=True)
class MmiLongHorizonResearchSourceEntry:
    """One validated manual long-horizon research source entry (V2).

    Carries only intrinsic validated entry facts plus a stable, code-generated
    ``source_entry_identity_sha256``. ``tickers`` preserves the operator's
    declared order exactly; the entry identity canonicalizes ticker order
    internally so ticker-order alone never changes identity (see module
    docstring). Does not own evaluation-time freshness, availability,
    candidate-domain binding, prompt visibility, or any permission/disposition
    authority.
    """

    publisher: str
    published_at: str
    source_locator: str
    tickers: tuple[str, ...]
    excerpt_text: str
    source_entry_identity_sha256: str


@dataclass(frozen=True, slots=True)
class MmiLongHorizonResearchPayloadV2:
    """One validated manual multi-entry long-horizon research bundle.

    ``sources`` preserves operator-declared entry order exactly; order is
    presentation order only and carries no priority, ranking, or selection
    authority. Does not own evaluation-time freshness, availability,
    candidate-domain binding, prompt visibility, or any permission/disposition
    authority.
    """

    schema_version: str
    sources: tuple[MmiLongHorizonResearchSourceEntry, ...]


def _compute_source_entry_identity(
    *,
    publisher: str,
    published_at: str,
    source_locator: str,
    tickers: tuple[str, ...],
    excerpt_text: str,
) -> str:
    """Stable identity over one source entry's semantic facts only.

    Ticker LIST ORDER is canonicalized (sorted) for this preimage only; it
    never affects the stored ``tickers`` tuple. No entry position, bundle
    composition, bundle ordering, or bundle-level identity enters this
    preimage.
    """
    preimage = {
        "publisher": publisher,
        "published_at": published_at,
        "source_locator": source_locator,
        "tickers": sorted(tickers),
        "excerpt_text": excerpt_text,
    }
    return domain_separated_sha256(
        _MMI_LONG_HORIZON_RESEARCH_SOURCE_ENTRY_V1_IDENTITY_DOMAIN,
        preimage,
    )


def _validate_source_entry(value: object) -> MmiLongHorizonResearchSourceEntry:
    """Validate one closed source-entry mapping against the intrinsic contract."""
    if not isinstance(value, Mapping) or type(value) is not dict:
        _fail("MMI_LONG_HORIZON_RESEARCH_PAYLOAD_V2_SOURCE_ENTRY_NOT_MAPPING")

    keys = set(value.keys())
    if any(type(k) is not str for k in keys):
        _fail("MMI_LONG_HORIZON_RESEARCH_PAYLOAD_V2_SOURCE_ENTRY_KEYS_INVALID")
    if keys != _REQUIRED_SOURCE_ENTRY_FIELDS:
        _fail("MMI_LONG_HORIZON_RESEARCH_PAYLOAD_V2_SOURCE_ENTRY_KEYS_INVALID")

    # Publisher (operator-declared provenance)
    publisher = value["publisher"]
    if type(publisher) is not str or len(publisher) == 0 or publisher.strip() == "":
        _fail("MMI_LONG_HORIZON_RESEARCH_PAYLOAD_V2_PUBLISHER_INVALID")

    # Published date (calendar syntax only; no freshness or clock checks)
    published_at = value["published_at"]
    if type(published_at) is not str or not _DATE_RE.fullmatch(published_at):
        _fail("MMI_LONG_HORIZON_RESEARCH_PAYLOAD_V2_PUBLISHED_AT_INVALID")
    try:
        parsed_date = date.fromisoformat(published_at)
    except ValueError:
        _fail("MMI_LONG_HORIZON_RESEARCH_PAYLOAD_V2_PUBLISHED_AT_INVALID")
    if parsed_date.isoformat() != published_at:
        _fail("MMI_LONG_HORIZON_RESEARCH_PAYLOAD_V2_PUBLISHED_AT_INVALID")

    # Source locator (operator-declared locator)
    source_locator = value["source_locator"]
    if (
        type(source_locator) is not str
        or len(source_locator) == 0
        or source_locator.strip() == ""
    ):
        _fail("MMI_LONG_HORIZON_RESEARCH_PAYLOAD_V2_SOURCE_LOCATOR_INVALID")

    # Tickers list (syntax and within-entry uniqueness; candidate domain
    # checked downstream; overlap ACROSS entries is intentionally permitted).
    tickers_val = value["tickers"]
    if type(tickers_val) is not list or len(tickers_val) == 0:
        _fail("MMI_LONG_HORIZON_RESEARCH_PAYLOAD_V2_TICKERS_INVALID")

    seen_tickers: set[str] = set()
    validated_tickers: list[str] = []
    for item in tickers_val:
        if type(item) is not str or not _TICKER_RE.fullmatch(item):
            _fail("MMI_LONG_HORIZON_RESEARCH_PAYLOAD_V2_TICKERS_INVALID")
        if item in seen_tickers:
            _fail("MMI_LONG_HORIZON_RESEARCH_PAYLOAD_V2_TICKERS_DUPLICATE")
        seen_tickers.add(item)
        validated_tickers.append(item)

    # Excerpt text (operator-supplied verbatim excerpt text)
    excerpt_text = value["excerpt_text"]
    if (
        type(excerpt_text) is not str
        or len(excerpt_text) == 0
        or excerpt_text.strip() == ""
    ):
        _fail("MMI_LONG_HORIZON_RESEARCH_PAYLOAD_V2_EXCERPT_TEXT_INVALID")

    tickers_tuple = tuple(validated_tickers)
    try:
        entry_identity = _compute_source_entry_identity(
            publisher=publisher,
            published_at=published_at,
            source_locator=source_locator,
            tickers=tickers_tuple,
            excerpt_text=excerpt_text,
        )
    except MmiCanonicalizationError:
        _fail(
            "MMI_LONG_HORIZON_RESEARCH_PAYLOAD_V2_SOURCE_ENTRY_IDENTITY_INVALID"
        )

    return MmiLongHorizonResearchSourceEntry(
        publisher=publisher,
        published_at=published_at,
        source_locator=source_locator,
        tickers=tickers_tuple,
        excerpt_text=excerpt_text,
        source_entry_identity_sha256=entry_identity,
    )


def validate_mmi_long_horizon_research_payload_v2(
    value: object,
) -> MmiLongHorizonResearchPayloadV2:
    """Validate a parsed mapping against the strict V2 bundle schema."""
    if not isinstance(value, Mapping) or type(value) is not dict:
        _fail("MMI_LONG_HORIZON_RESEARCH_PAYLOAD_V2_NOT_MAPPING")

    # Closed schema check: no missing keys, no extra keys.
    keys = set(value.keys())
    if any(type(k) is not str for k in keys):
        _fail("MMI_LONG_HORIZON_RESEARCH_PAYLOAD_V2_KEYS_INVALID")
    if keys != _REQUIRED_PAYLOAD_FIELDS:
        _fail("MMI_LONG_HORIZON_RESEARCH_PAYLOAD_V2_KEYS_INVALID")

    # Schema version
    schema_version = value["schema_version"]
    if (
        type(schema_version) is not str
        or schema_version != MMI_LONG_HORIZON_RESEARCH_PAYLOAD_V2_SCHEMA_VERSION
    ):
        _fail("MMI_LONG_HORIZON_RESEARCH_PAYLOAD_V2_SCHEMA_VERSION_INVALID")

    # Sources (non-empty array of closed source entries; declared order kept)
    sources_val = value["sources"]
    if type(sources_val) is not list or len(sources_val) == 0:
        _fail("MMI_LONG_HORIZON_RESEARCH_PAYLOAD_V2_SOURCES_INVALID")

    validated_entries: list[MmiLongHorizonResearchSourceEntry] = []
    seen_entry_identities: set[str] = set()
    for item in sources_val:
        entry = _validate_source_entry(item)
        if entry.source_entry_identity_sha256 in seen_entry_identities:
            _fail(
                "MMI_LONG_HORIZON_RESEARCH_PAYLOAD_V2_DUPLICATE_SOURCE_ENTRY"
            )
        seen_entry_identities.add(entry.source_entry_identity_sha256)
        validated_entries.append(entry)

    # JSON schema validator check (structural backstop; the Python checks
    # above remain the owning effective contract -- see module docstring).
    try:
        validate_artifact_schema(
            value,
            schema_name=MMI_LONG_HORIZON_RESEARCH_PAYLOAD_V2_SCHEMA_NAME,
        )
    except Exception:
        _fail("MMI_LONG_HORIZON_RESEARCH_PAYLOAD_V2_SCHEMA_VIOLATION")

    return MmiLongHorizonResearchPayloadV2(
        schema_version=schema_version,
        sources=tuple(validated_entries),
    )


def parse_mmi_long_horizon_research_payload_v2(
    raw_bytes: bytes | str,
) -> MmiLongHorizonResearchPayloadV2:
    """Parse and strictly validate raw JSON bytes or text into a typed V2 bundle."""
    if type(raw_bytes) is bytes:
        try:
            text = raw_bytes.decode("utf-8")
        except UnicodeDecodeError:
            _fail("MMI_LONG_HORIZON_RESEARCH_PAYLOAD_V2_JSON_INVALID")
    elif type(raw_bytes) is str:
        text = raw_bytes
    else:
        _fail("MMI_LONG_HORIZON_RESEARCH_PAYLOAD_V2_NOT_MAPPING")

    try:
        parsed = json.loads(text)
    except Exception:
        _fail("MMI_LONG_HORIZON_RESEARCH_PAYLOAD_V2_JSON_INVALID")

    return validate_mmi_long_horizon_research_payload_v2(parsed)


def read_mmi_long_horizon_research_payload_v2_from_captured_source(
    source: MmiCapturedSource,
) -> MmiLongHorizonResearchPayloadV2:
    """Extract and validate the V2 research bundle from a captured source."""
    if not _mmi_captured_source_provenance_is_valid(source):
        _fail("MMI_LONG_HORIZON_RESEARCH_PAYLOAD_V2_PROVENANCE_INVALID")
    if source.role is not MmiSourceRole.LONG_HORIZON_RESEARCH:
        _fail("MMI_LONG_HORIZON_RESEARCH_PAYLOAD_V2_ROLE_INVALID")
    return parse_mmi_long_horizon_research_payload_v2(source.raw_bytes)
