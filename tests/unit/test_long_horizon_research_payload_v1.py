"""Unit tests for MMI long-horizon research payload schema and reader (PR-LH1)."""

from __future__ import annotations

import ast
from dataclasses import fields, is_dataclass
import hashlib
import json
from pathlib import Path
import secrets

import pytest

import _mmi_hermetic_source_checkout as hermetic
from investment_orchestrator.mmi import (
    contracts,
    long_horizon_research_payload_v1 as lh_payload,
    source_capture,
)
from investment_orchestrator.mmi.contracts import (
    MMI_SOURCE_CATALOG,
    MmiCapturedSource,
    MmiSourceRole,
)


def _valid_payload_dict() -> dict[str, object]:
    return {
        "schema_version": "mmi_long_horizon_research_payload_v1",
        "publisher": "Example Research Institute",
        "published_at": "2026-06-15",
        "source_locator": "docs/thematic/clean_energy_2026.pdf",
        "tickers": ["ICLN", "QCLN"],
        "excerpt_text": "Clean energy infrastructure growth trends remain positive over 5-10 year horizon.",
    }


def _valid_payload_bytes() -> bytes:
    return json.dumps(_valid_payload_dict(), indent=2).encode("utf-8")


def test_valid_payload_parses_exactly() -> None:
    raw = _valid_payload_bytes()
    payload = lh_payload.parse_mmi_long_horizon_research_payload_v1(raw)

    assert payload.schema_version == "mmi_long_horizon_research_payload_v1"
    assert payload.publisher == "Example Research Institute"
    assert payload.published_at == "2026-06-15"
    assert payload.source_locator == "docs/thematic/clean_energy_2026.pdf"
    assert payload.tickers == ("ICLN", "QCLN")
    assert payload.excerpt_text == (
        "Clean energy infrastructure growth trends remain positive over 5-10 year horizon."
    )


def test_captured_source_raw_bytes_retain_exact_observed_sha256(
    tmp_path: Path,
) -> None:
    raw = _valid_payload_bytes()
    source = hermetic.capture_source(
        tmp_path,
        role=MmiSourceRole.LONG_HORIZON_RESEARCH,
        raw=raw,
    )

    assert isinstance(source, MmiCapturedSource)
    assert source.role is MmiSourceRole.LONG_HORIZON_RESEARCH
    assert source.raw_bytes == raw
    expected_digest = hashlib.sha256(raw).hexdigest()
    assert source.source_record["observed_sha256"] == expected_digest
    assert (
        source.source_record["repository_relative_locator"]
        == "inputs/current/long_horizon_research.json"
    )

    payload = (
        lh_payload.read_mmi_long_horizon_research_payload_v1_from_captured_source(
            source
        )
    )
    assert payload.publisher == "Example Research Institute"
    assert payload.tickers == ("ICLN", "QCLN")


def test_malformed_json_rejected() -> None:
    with pytest.raises(
        lh_payload.MmiLongHorizonResearchPayloadError
    ) as exc_info:
        lh_payload.parse_mmi_long_horizon_research_payload_v1(b"{not valid json")
    assert exc_info.value.code == "MMI_LONG_HORIZON_RESEARCH_PAYLOAD_JSON_INVALID"

    with pytest.raises(
        lh_payload.MmiLongHorizonResearchPayloadError
    ) as exc_info:
        lh_payload.parse_mmi_long_horizon_research_payload_v1(b"[\"a\", \"b\"]")
    assert exc_info.value.code == "MMI_LONG_HORIZON_RESEARCH_PAYLOAD_NOT_MAPPING"


def test_missing_or_extra_payload_key_rejected() -> None:
    # Missing key
    for key in (
        "schema_version",
        "publisher",
        "published_at",
        "source_locator",
        "tickers",
        "excerpt_text",
    ):
        data = _valid_payload_dict()
        del data[key]
        with pytest.raises(
            lh_payload.MmiLongHorizonResearchPayloadError
        ) as exc_info:
            lh_payload.validate_mmi_long_horizon_research_payload_v1(data)
        assert (
            exc_info.value.code
            == "MMI_LONG_HORIZON_RESEARCH_PAYLOAD_KEYS_INVALID"
        )

    # Extra key
    data = _valid_payload_dict()
    data["extra_unauthorized_key"] = "forbidden"
    with pytest.raises(
        lh_payload.MmiLongHorizonResearchPayloadError
    ) as exc_info:
        lh_payload.validate_mmi_long_horizon_research_payload_v1(data)
    assert (
        exc_info.value.code
        == "MMI_LONG_HORIZON_RESEARCH_PAYLOAD_KEYS_INVALID"
    )


def test_wrong_schema_version_rejected() -> None:
    data = _valid_payload_dict()
    data["schema_version"] = "mmi_long_horizon_research_payload_v2"
    with pytest.raises(
        lh_payload.MmiLongHorizonResearchPayloadError
    ) as exc_info:
        lh_payload.validate_mmi_long_horizon_research_payload_v1(data)
    assert (
        exc_info.value.code
        == "MMI_LONG_HORIZON_RESEARCH_PAYLOAD_SCHEMA_VERSION_INVALID"
    )


def test_wrong_field_types_rejected() -> None:
    # publisher as int
    data = _valid_payload_dict()
    data["publisher"] = 12345
    with pytest.raises(
        lh_payload.MmiLongHorizonResearchPayloadError
    ) as exc_info:
        lh_payload.validate_mmi_long_horizon_research_payload_v1(data)
    assert (
        exc_info.value.code
        == "MMI_LONG_HORIZON_RESEARCH_PAYLOAD_PUBLISHER_INVALID"
    )

    # empty publisher string
    data = _valid_payload_dict()
    data["publisher"] = "   "
    with pytest.raises(
        lh_payload.MmiLongHorizonResearchPayloadError
    ) as exc_info:
        lh_payload.validate_mmi_long_horizon_research_payload_v1(data)
    assert (
        exc_info.value.code
        == "MMI_LONG_HORIZON_RESEARCH_PAYLOAD_PUBLISHER_INVALID"
    )

    # source_locator as None
    data = _valid_payload_dict()
    data["source_locator"] = None
    with pytest.raises(
        lh_payload.MmiLongHorizonResearchPayloadError
    ) as exc_info:
        lh_payload.validate_mmi_long_horizon_research_payload_v1(data)
    assert (
        exc_info.value.code
        == "MMI_LONG_HORIZON_RESEARCH_PAYLOAD_SOURCE_LOCATOR_INVALID"
    )

    # excerpt_text as empty
    data = _valid_payload_dict()
    data["excerpt_text"] = ""
    with pytest.raises(
        lh_payload.MmiLongHorizonResearchPayloadError
    ) as exc_info:
        lh_payload.validate_mmi_long_horizon_research_payload_v1(data)
    assert (
        exc_info.value.code
        == "MMI_LONG_HORIZON_RESEARCH_PAYLOAD_EXCERPT_TEXT_INVALID"
    )


def test_duplicate_ticker_rejected() -> None:
    data = _valid_payload_dict()
    data["tickers"] = ["SPY", "QQQ", "SPY"]
    with pytest.raises(
        lh_payload.MmiLongHorizonResearchPayloadError
    ) as exc_info:
        lh_payload.validate_mmi_long_horizon_research_payload_v1(data)
    assert (
        exc_info.value.code
        == "MMI_LONG_HORIZON_RESEARCH_PAYLOAD_TICKERS_DUPLICATE"
    )


def test_invalid_ticker_syntax_rejected() -> None:
    # Empty tickers list
    data = _valid_payload_dict()
    data["tickers"] = []
    with pytest.raises(
        lh_payload.MmiLongHorizonResearchPayloadError
    ) as exc_info:
        lh_payload.validate_mmi_long_horizon_research_payload_v1(data)
    assert (
        exc_info.value.code
        == "MMI_LONG_HORIZON_RESEARCH_PAYLOAD_TICKERS_INVALID"
    )

    # Lowercase ticker
    data = _valid_payload_dict()
    data["tickers"] = ["spy"]
    with pytest.raises(
        lh_payload.MmiLongHorizonResearchPayloadError
    ) as exc_info:
        lh_payload.validate_mmi_long_horizon_research_payload_v1(data)
    assert (
        exc_info.value.code
        == "MMI_LONG_HORIZON_RESEARCH_PAYLOAD_TICKERS_INVALID"
    )

    # Invalid characters
    data = _valid_payload_dict()
    data["tickers"] = ["SPY$1"]
    with pytest.raises(
        lh_payload.MmiLongHorizonResearchPayloadError
    ) as exc_info:
        lh_payload.validate_mmi_long_horizon_research_payload_v1(data)
    assert (
        exc_info.value.code
        == "MMI_LONG_HORIZON_RESEARCH_PAYLOAD_TICKERS_INVALID"
    )

    # Non-string element
    data = _valid_payload_dict()
    data["tickers"] = [123]
    with pytest.raises(
        lh_payload.MmiLongHorizonResearchPayloadError
    ) as exc_info:
        lh_payload.validate_mmi_long_horizon_research_payload_v1(data)
    assert (
        exc_info.value.code
        == "MMI_LONG_HORIZON_RESEARCH_PAYLOAD_TICKERS_INVALID"
    )


def test_published_at_invalid_date_syntax_rejected() -> None:
    for bad_date in (
        "2026/06/15",
        "06-15-2026",
        "2026-6-15",
        "2026-02-30",
        "2026-13-01",
        "invalid-date",
        "2026-06-15T00:00:00Z",
    ):
        data = _valid_payload_dict()
        data["published_at"] = bad_date
        with pytest.raises(
            lh_payload.MmiLongHorizonResearchPayloadError
        ) as exc_info:
            lh_payload.validate_mmi_long_horizon_research_payload_v1(data)
        assert (
            exc_info.value.code
            == "MMI_LONG_HORIZON_RESEARCH_PAYLOAD_PUBLISHED_AT_INVALID"
        )


def test_syntactically_valid_future_date_not_evaluated_in_lh1() -> None:
    # Far-future calendar date is valid syntax; LH1 does not check freshness or future dates.
    data = _valid_payload_dict()
    data["published_at"] = "2099-12-31"
    payload = lh_payload.validate_mmi_long_horizon_research_payload_v1(data)
    assert payload.published_at == "2099-12-31"


def test_excerpt_text_and_provenance_not_silently_normalized() -> None:
    data = _valid_payload_dict()
    data["publisher"] = "  Leading  Spaced  Publisher  "
    data["excerpt_text"] = "  Leading and trailing whitespace preserved. \n\t"
    payload = lh_payload.validate_mmi_long_horizon_research_payload_v1(data)

    # Values must remain exact, unstripped, un-lowercased
    assert payload.publisher == "  Leading  Spaced  Publisher  "
    assert (
        payload.excerpt_text
        == "  Leading and trailing whitespace preserved. \n\t"
    )


def test_forged_captured_source_without_registered_provenance_is_rejected() -> None:
    """An object.__new__-forged MmiCapturedSource must fail closed on read.

    ``MmiCapturedSource.__init__`` refuses construction, but a caller could
    still bypass it with ``object.__new__``/``object.__setattr__``.  Such an
    envelope has a provenance token that was never registered by the real
    capture path, so it must be rejected before role/payload parsing.
    """
    raw = _valid_payload_bytes()
    forged = object.__new__(MmiCapturedSource)
    object.__setattr__(forged, "role", MmiSourceRole.LONG_HORIZON_RESEARCH)
    object.__setattr__(forged, "raw_bytes", raw)
    object.__setattr__(forged, "source_record", {})
    object.__setattr__(forged, "_provenance_token", secrets.token_bytes(32))
    object.__setattr__(forged, "_provenance_seal", secrets.token_bytes(32))

    assert isinstance(forged, MmiCapturedSource)
    with pytest.raises(
        lh_payload.MmiLongHorizonResearchPayloadError
    ) as exc_info:
        lh_payload.read_mmi_long_horizon_research_payload_v1_from_captured_source(
            forged
        )
    assert (
        exc_info.value.code
        == "MMI_LONG_HORIZON_RESEARCH_PAYLOAD_PROVENANCE_INVALID"
    )


def test_genuine_source_parses_then_tampered_raw_bytes_reject_closed(
    tmp_path: Path,
) -> None:
    """A genuine captured source parses; tampering it after capture must not.

    Mutating ``raw_bytes`` on an already-captured, registered instance
    invalidates the HMAC seal bound to the original bytes, proving the
    reader's provenance gate -- not merely a type check -- guards the read.
    """
    raw = _valid_payload_bytes()
    source = hermetic.capture_source(
        tmp_path,
        role=MmiSourceRole.LONG_HORIZON_RESEARCH,
        raw=raw,
    )

    payload = (
        lh_payload.read_mmi_long_horizon_research_payload_v1_from_captured_source(
            source
        )
    )
    assert payload.publisher == "Example Research Institute"

    object.__setattr__(source, "raw_bytes", raw + b"\n")
    with pytest.raises(
        lh_payload.MmiLongHorizonResearchPayloadError
    ) as exc_info:
        lh_payload.read_mmi_long_horizon_research_payload_v1_from_captured_source(
            source
        )
    assert (
        exc_info.value.code
        == "MMI_LONG_HORIZON_RESEARCH_PAYLOAD_PROVENANCE_INVALID"
    )


def test_read_from_captured_source_rejects_wrong_role(tmp_path: Path) -> None:
    raw = hermetic.strategy_settings_bytes(
        as_of=hermetic.DEFAULT_AS_OF,
        run_timestamp_et=hermetic.DEFAULT_RUN_TIMESTAMP_ET,
    )
    source = hermetic.capture_source(
        tmp_path,
        role=MmiSourceRole.STRATEGY_SETTINGS,
        raw=raw,
    )
    with pytest.raises(
        lh_payload.MmiLongHorizonResearchPayloadError
    ) as exc_info:
        lh_payload.read_mmi_long_horizon_research_payload_v1_from_captured_source(
            source
        )
    assert (
        exc_info.value.code
        == "MMI_LONG_HORIZON_RESEARCH_PAYLOAD_ROLE_INVALID"
    )


def test_existing_source_catalog_and_roles_unchanged() -> None:
    assert tuple(MMI_SOURCE_CATALOG) == (
        MmiSourceRole.STRATEGY_SETTINGS,
        MmiSourceRole.PORTFOLIO_SNAPSHOT,
        MmiSourceRole.LONG_HORIZON_RESEARCH,
    )
    assert (
        MMI_SOURCE_CATALOG[MmiSourceRole.STRATEGY_SETTINGS].source_id
        == "MMI_STRATEGY_SETTINGS"
    )
    assert (
        MMI_SOURCE_CATALOG[MmiSourceRole.PORTFOLIO_SNAPSHOT].source_id
        == "MMI_PORTFOLIO_SNAPSHOT"
    )
    assert (
        MMI_SOURCE_CATALOG[MmiSourceRole.LONG_HORIZON_RESEARCH].source_id
        == "MMI_LONG_HORIZON_RESEARCH"
    )


def test_no_prohibited_imports_or_network_in_long_horizon_module() -> None:
    module_path = Path(lh_payload.__file__)
    tree = ast.parse(module_path.read_text(encoding="utf-8"))

    prohibited_modules = {
        "urllib",
        "requests",
        "http",
        "httpx",
        "aiohttp",
        "socket",
        "asyncio",
        "openai",
        "anthropic",
        "google",
        "scheduled",
        "schedule",
    }

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root_pkg = alias.name.split(".")[0]
                assert root_pkg not in prohibited_modules, root_pkg
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                root_pkg = node.module.split(".")[0]
                assert root_pkg not in prohibited_modules, root_pkg


def test_typed_result_surface_has_no_authority_or_disposition_fields() -> None:
    assert is_dataclass(lh_payload.MmiLongHorizonResearchPayload)
    field_names = {
        f.name for f in fields(lh_payload.MmiLongHorizonResearchPayload)
    }

    assert field_names == {
        "schema_version",
        "publisher",
        "published_at",
        "source_locator",
        "tickers",
        "excerpt_text",
    }

    prohibited_fields = {
        "status",
        "available",
        "source_status",
        "freshness",
        "is_fresh",
        "is_stale",
        "disposition",
        "permission",
        "permissions",
        "priority",
        "budget",
        "allocation",
        "target_weight",
        "quantity",
        "order",
        "orders",
        "allowed_actions",
        "blocked_actions",
    }

    assert field_names.isdisjoint(prohibited_fields)
