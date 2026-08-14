"""Unit tests for MMI long-horizon research payload V2 schema/reader (PR-LH1b)."""

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
    canonical as mmi_canonical,
    long_horizon_research_payload_v1 as lh_v1,
    long_horizon_research_payload_v2 as lh_v2,
)
from investment_orchestrator.mmi.contracts import (
    MMI_SOURCE_CATALOG,
    MmiCapturedSource,
    MmiSourceRole,
)


def _valid_v1_payload_dict() -> dict[str, object]:
    return {
        "schema_version": "mmi_long_horizon_research_payload_v1",
        "publisher": "Example Research Institute",
        "published_at": "2026-06-15",
        "source_locator": "docs/thematic/clean_energy_2026.pdf",
        "tickers": ["ICLN", "QCLN"],
        "excerpt_text": "Clean energy infrastructure growth trends remain positive over 5-10 year horizon.",
    }


def _entry_dict(
    *,
    publisher: str = "Example Research Institute",
    published_at: str = "2026-06-15",
    source_locator: str = "docs/thematic/clean_energy_2026.pdf",
    tickers: tuple[str, ...] = ("ICLN", "QCLN"),
    excerpt_text: str = (
        "Clean energy infrastructure growth trends remain positive over "
        "5-10 year horizon."
    ),
) -> dict[str, object]:
    return {
        "publisher": publisher,
        "published_at": published_at,
        "source_locator": source_locator,
        "tickers": list(tickers),
        "excerpt_text": excerpt_text,
    }


def _valid_v2_payload_dict(
    *, sources: list[dict[str, object]] | None = None
) -> dict[str, object]:
    return {
        "schema_version": "mmi_long_horizon_research_payload_v2",
        "sources": sources if sources is not None else [_entry_dict()],
    }


def _valid_v2_payload_bytes(
    *, sources: list[dict[str, object]] | None = None
) -> bytes:
    return json.dumps(
        _valid_v2_payload_dict(sources=sources), indent=2
    ).encode("utf-8")


def _entry_identity(payload: lh_v2.MmiLongHorizonResearchPayloadV2) -> str:
    return payload.sources[0].source_entry_identity_sha256


# --------------------------------------------------------------------------
# A/B/C: V1/V2 independence and closed version discrimination.
# --------------------------------------------------------------------------
def test_v1_still_accepted_by_v1_reader_exactly_as_before() -> None:
    raw = json.dumps(_valid_v1_payload_dict(), indent=2).encode("utf-8")
    payload = lh_v1.parse_mmi_long_horizon_research_payload_v1(raw)
    assert payload.schema_version == "mmi_long_horizon_research_payload_v1"
    assert payload.tickers == ("ICLN", "QCLN")


def test_v1_payload_rejected_by_v2_reader() -> None:
    raw = json.dumps(_valid_v1_payload_dict(), indent=2).encode("utf-8")
    with pytest.raises(lh_v2.MmiLongHorizonResearchPayloadV2Error):
        lh_v2.parse_mmi_long_horizon_research_payload_v2(raw)


def test_v2_payload_rejected_by_v1_reader() -> None:
    raw = _valid_v2_payload_bytes()
    with pytest.raises(lh_v1.MmiLongHorizonResearchPayloadError):
        lh_v1.parse_mmi_long_horizon_research_payload_v1(raw)


def test_v2_schema_version_discriminator_rejects_v1_string() -> None:
    data = _valid_v2_payload_dict()
    data["schema_version"] = "mmi_long_horizon_research_payload_v1"
    with pytest.raises(
        lh_v2.MmiLongHorizonResearchPayloadV2Error
    ) as exc_info:
        lh_v2.validate_mmi_long_horizon_research_payload_v2(data)
    assert (
        exc_info.value.code
        == "MMI_LONG_HORIZON_RESEARCH_PAYLOAD_V2_SCHEMA_VERSION_INVALID"
    )


def test_v1_schema_version_discriminator_rejects_v2_string() -> None:
    data = _valid_v1_payload_dict()
    data["schema_version"] = "mmi_long_horizon_research_payload_v2"
    with pytest.raises(lh_v1.MmiLongHorizonResearchPayloadError) as exc_info:
        lh_v1.validate_mmi_long_horizon_research_payload_v1(data)
    assert (
        exc_info.value.code
        == "MMI_LONG_HORIZON_RESEARCH_PAYLOAD_SCHEMA_VERSION_INVALID"
    )


def test_v2_module_does_not_import_v1_module() -> None:
    module_path = Path(lh_v2.__file__)
    tree = ast.parse(module_path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            assert "long_horizon_research_payload_v1" not in node.module
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert "long_horizon_research_payload_v1" not in alias.name


# --------------------------------------------------------------------------
# D/E/F: valid multi-entry bundle, entry order, ticker order.
# --------------------------------------------------------------------------
def test_valid_multi_entry_bundle_parses() -> None:
    data = _valid_v2_payload_dict(
        sources=[
            _entry_dict(publisher="Publisher A", tickers=("SMH",)),
            _entry_dict(publisher="Publisher B", tickers=("ICLN", "QCLN")),
        ]
    )
    payload = lh_v2.validate_mmi_long_horizon_research_payload_v2(data)
    assert payload.schema_version == "mmi_long_horizon_research_payload_v2"
    assert len(payload.sources) == 2
    assert payload.sources[0].publisher == "Publisher A"
    assert payload.sources[1].publisher == "Publisher B"


def test_declared_source_entry_order_preserved() -> None:
    data = _valid_v2_payload_dict(
        sources=[
            _entry_dict(publisher="Zeta Research", tickers=("SMH",)),
            _entry_dict(publisher="Alpha Research", tickers=("IGV",)),
        ]
    )
    payload = lh_v2.validate_mmi_long_horizon_research_payload_v2(data)
    assert [s.publisher for s in payload.sources] == [
        "Zeta Research",
        "Alpha Research",
    ]


def test_declared_ticker_order_preserved_in_typed_object() -> None:
    data = _valid_v2_payload_dict(
        sources=[_entry_dict(tickers=("VOO", "ARKK", "QQQ"))]
    )
    payload = lh_v2.validate_mmi_long_horizon_research_payload_v2(data)
    assert payload.sources[0].tickers == ("VOO", "ARKK", "QQQ")


# --------------------------------------------------------------------------
# G/H: overlap across entries permitted; duplicate ticker within one rejected.
# --------------------------------------------------------------------------
def test_overlapping_ticker_across_different_entries_allowed() -> None:
    data = _valid_v2_payload_dict(
        sources=[
            _entry_dict(
                publisher="Publisher A",
                tickers=("SMH",),
                excerpt_text="Excerpt A about semiconductors.",
            ),
            _entry_dict(
                publisher="Publisher B",
                tickers=("SMH",),
                excerpt_text="Excerpt B about semiconductors.",
            ),
        ]
    )
    payload = lh_v2.validate_mmi_long_horizon_research_payload_v2(data)
    assert len(payload.sources) == 2
    assert payload.sources[0].tickers == ("SMH",)
    assert payload.sources[1].tickers == ("SMH",)


def test_duplicate_ticker_inside_one_entry_rejected() -> None:
    data = _valid_v2_payload_dict(
        sources=[_entry_dict(tickers=("SMH", "IGV", "SMH"))]
    )
    with pytest.raises(
        lh_v2.MmiLongHorizonResearchPayloadV2Error
    ) as exc_info:
        lh_v2.validate_mmi_long_horizon_research_payload_v2(data)
    assert (
        exc_info.value.code
        == "MMI_LONG_HORIZON_RESEARCH_PAYLOAD_V2_TICKERS_DUPLICATE"
    )


# --------------------------------------------------------------------------
# I/J/K: exact duplicate entry rejected; ticker-permutation identity equality.
# --------------------------------------------------------------------------
def test_exact_duplicate_entry_rejected() -> None:
    entry = _entry_dict()
    data = _valid_v2_payload_dict(sources=[entry, dict(entry)])
    with pytest.raises(
        lh_v2.MmiLongHorizonResearchPayloadV2Error
    ) as exc_info:
        lh_v2.validate_mmi_long_horizon_research_payload_v2(data)
    assert (
        exc_info.value.code
        == "MMI_LONG_HORIZON_RESEARCH_PAYLOAD_V2_DUPLICATE_SOURCE_ENTRY"
    )


def test_ticker_permutation_only_yields_same_entry_identity() -> None:
    data_a = _valid_v2_payload_dict(
        sources=[_entry_dict(tickers=("SMH", "IGV"))]
    )
    data_b = _valid_v2_payload_dict(
        sources=[_entry_dict(tickers=("IGV", "SMH"))]
    )
    payload_a = lh_v2.validate_mmi_long_horizon_research_payload_v2(data_a)
    payload_b = lh_v2.validate_mmi_long_horizon_research_payload_v2(data_b)

    assert _entry_identity(payload_a) == _entry_identity(payload_b)
    # Declared order itself remains distinct on the typed object.
    assert payload_a.sources[0].tickers == ("SMH", "IGV")
    assert payload_b.sources[0].tickers == ("IGV", "SMH")


def test_permutation_only_duplicate_entries_in_one_bundle_rejected() -> None:
    data = _valid_v2_payload_dict(
        sources=[
            _entry_dict(tickers=("SMH", "IGV")),
            _entry_dict(tickers=("IGV", "SMH")),
        ]
    )
    with pytest.raises(
        lh_v2.MmiLongHorizonResearchPayloadV2Error
    ) as exc_info:
        lh_v2.validate_mmi_long_horizon_research_payload_v2(data)
    assert (
        exc_info.value.code
        == "MMI_LONG_HORIZON_RESEARCH_PAYLOAD_V2_DUPLICATE_SOURCE_ENTRY"
    )


# --------------------------------------------------------------------------
# L-P: each identity-bearing field independently changes entry identity.
# --------------------------------------------------------------------------
def test_changing_ticker_membership_changes_identity() -> None:
    baseline = lh_v2.validate_mmi_long_horizon_research_payload_v2(
        _valid_v2_payload_dict(sources=[_entry_dict(tickers=("SMH", "IGV"))])
    )
    changed = lh_v2.validate_mmi_long_horizon_research_payload_v2(
        _valid_v2_payload_dict(sources=[_entry_dict(tickers=("SMH", "QQQ"))])
    )
    assert _entry_identity(baseline) != _entry_identity(changed)


def test_changing_publisher_changes_identity() -> None:
    baseline = lh_v2.validate_mmi_long_horizon_research_payload_v2(
        _valid_v2_payload_dict(sources=[_entry_dict(publisher="Publisher A")])
    )
    changed = lh_v2.validate_mmi_long_horizon_research_payload_v2(
        _valid_v2_payload_dict(sources=[_entry_dict(publisher="Publisher B")])
    )
    assert _entry_identity(baseline) != _entry_identity(changed)


def test_changing_published_at_changes_identity() -> None:
    baseline = lh_v2.validate_mmi_long_horizon_research_payload_v2(
        _valid_v2_payload_dict(
            sources=[_entry_dict(published_at="2026-06-15")]
        )
    )
    changed = lh_v2.validate_mmi_long_horizon_research_payload_v2(
        _valid_v2_payload_dict(
            sources=[_entry_dict(published_at="2026-07-01")]
        )
    )
    assert _entry_identity(baseline) != _entry_identity(changed)


def test_changing_source_locator_changes_identity() -> None:
    baseline = lh_v2.validate_mmi_long_horizon_research_payload_v2(
        _valid_v2_payload_dict(
            sources=[_entry_dict(source_locator="docs/a.pdf")]
        )
    )
    changed = lh_v2.validate_mmi_long_horizon_research_payload_v2(
        _valid_v2_payload_dict(
            sources=[_entry_dict(source_locator="docs/b.pdf")]
        )
    )
    assert _entry_identity(baseline) != _entry_identity(changed)


def test_changing_excerpt_text_changes_identity() -> None:
    baseline = lh_v2.validate_mmi_long_horizon_research_payload_v2(
        _valid_v2_payload_dict(
            sources=[_entry_dict(excerpt_text="Excerpt version one.")]
        )
    )
    changed = lh_v2.validate_mmi_long_horizon_research_payload_v2(
        _valid_v2_payload_dict(
            sources=[_entry_dict(excerpt_text="Excerpt version two.")]
        )
    )
    assert _entry_identity(baseline) != _entry_identity(changed)


# --------------------------------------------------------------------------
# Q/R/S: entry identity is independent of unrelated bundle composition/order.
# --------------------------------------------------------------------------
def test_adding_unrelated_entry_does_not_change_existing_identity() -> None:
    entry_a = _entry_dict(publisher="Publisher A", tickers=("SMH",))
    entry_b = _entry_dict(publisher="Publisher B", tickers=("IGV",))

    alone = lh_v2.validate_mmi_long_horizon_research_payload_v2(
        _valid_v2_payload_dict(sources=[entry_a])
    )
    with_extra = lh_v2.validate_mmi_long_horizon_research_payload_v2(
        _valid_v2_payload_dict(sources=[entry_a, entry_b])
    )
    assert (
        alone.sources[0].source_entry_identity_sha256
        == with_extra.sources[0].source_entry_identity_sha256
    )


def test_removing_unrelated_entry_does_not_change_existing_identity() -> None:
    entry_a = _entry_dict(publisher="Publisher A", tickers=("SMH",))
    entry_b = _entry_dict(publisher="Publisher B", tickers=("IGV",))

    with_both = lh_v2.validate_mmi_long_horizon_research_payload_v2(
        _valid_v2_payload_dict(sources=[entry_a, entry_b])
    )
    only_a = lh_v2.validate_mmi_long_horizon_research_payload_v2(
        _valid_v2_payload_dict(sources=[entry_a])
    )
    assert (
        with_both.sources[0].source_entry_identity_sha256
        == only_a.sources[0].source_entry_identity_sha256
    )


def test_reordering_bundle_entries_does_not_change_each_entry_identity() -> (
    None
):
    entry_a = _entry_dict(publisher="Publisher A", tickers=("SMH",))
    entry_b = _entry_dict(publisher="Publisher B", tickers=("IGV",))

    forward = lh_v2.validate_mmi_long_horizon_research_payload_v2(
        _valid_v2_payload_dict(sources=[entry_a, entry_b])
    )
    backward = lh_v2.validate_mmi_long_horizon_research_payload_v2(
        _valid_v2_payload_dict(sources=[entry_b, entry_a])
    )

    forward_ids = {
        s.publisher: s.source_entry_identity_sha256 for s in forward.sources
    }
    backward_ids = {
        s.publisher: s.source_entry_identity_sha256
        for s in backward.sources
    }
    assert forward_ids == backward_ids

    # The bundle-declared order itself is preserved, not sorted/ranked.
    assert [s.publisher for s in forward.sources] == [
        "Publisher A",
        "Publisher B",
    ]
    assert [s.publisher for s in backward.sources] == [
        "Publisher B",
        "Publisher A",
    ]


# --------------------------------------------------------------------------
# T/U/V: provenance boundary (forged / tampered / genuine captured source).
# --------------------------------------------------------------------------
def test_forged_captured_source_without_registered_provenance_is_rejected() -> (
    None
):
    """An object.__new__-forged MmiCapturedSource must fail closed on read."""
    raw = _valid_v2_payload_bytes()
    forged = object.__new__(MmiCapturedSource)
    object.__setattr__(forged, "role", MmiSourceRole.LONG_HORIZON_RESEARCH)
    object.__setattr__(forged, "raw_bytes", raw)
    object.__setattr__(forged, "source_record", {})
    object.__setattr__(forged, "_provenance_token", secrets.token_bytes(32))
    object.__setattr__(forged, "_provenance_seal", secrets.token_bytes(32))

    assert isinstance(forged, MmiCapturedSource)
    with pytest.raises(
        lh_v2.MmiLongHorizonResearchPayloadV2Error
    ) as exc_info:
        lh_v2.read_mmi_long_horizon_research_payload_v2_from_captured_source(
            forged
        )
    assert (
        exc_info.value.code
        == "MMI_LONG_HORIZON_RESEARCH_PAYLOAD_V2_PROVENANCE_INVALID"
    )


def test_genuine_source_parses_then_tampered_raw_bytes_reject_closed(
    tmp_path: Path,
) -> None:
    """A genuine captured source parses; tampering it after capture must not."""
    raw = _valid_v2_payload_bytes()
    source = hermetic.capture_source(
        tmp_path,
        role=MmiSourceRole.LONG_HORIZON_RESEARCH,
        raw=raw,
    )

    payload = (
        lh_v2.read_mmi_long_horizon_research_payload_v2_from_captured_source(
            source
        )
    )
    assert len(payload.sources) == 1

    object.__setattr__(source, "raw_bytes", raw + b"\n")
    with pytest.raises(
        lh_v2.MmiLongHorizonResearchPayloadV2Error
    ) as exc_info:
        lh_v2.read_mmi_long_horizon_research_payload_v2_from_captured_source(
            source
        )
    assert (
        exc_info.value.code
        == "MMI_LONG_HORIZON_RESEARCH_PAYLOAD_V2_PROVENANCE_INVALID"
    )


def test_captured_source_raw_bytes_retain_exact_observed_sha256(
    tmp_path: Path,
) -> None:
    raw = _valid_v2_payload_bytes()
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
        lh_v2.read_mmi_long_horizon_research_payload_v2_from_captured_source(
            source
        )
    )
    assert payload.sources[0].publisher == "Example Research Institute"


def test_read_from_captured_source_rejects_wrong_role(
    tmp_path: Path,
) -> None:
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
        lh_v2.MmiLongHorizonResearchPayloadV2Error
    ) as exc_info:
        lh_v2.read_mmi_long_horizon_research_payload_v2_from_captured_source(
            source
        )
    assert (
        exc_info.value.code
        == "MMI_LONG_HORIZON_RESEARCH_PAYLOAD_V2_ROLE_INVALID"
    )


# --------------------------------------------------------------------------
# Representative malformed JSON / closed-schema coverage (not exhaustive).
# --------------------------------------------------------------------------
def test_malformed_json_rejected() -> None:
    with pytest.raises(
        lh_v2.MmiLongHorizonResearchPayloadV2Error
    ) as exc_info:
        lh_v2.parse_mmi_long_horizon_research_payload_v2(b"{not valid json")
    assert (
        exc_info.value.code == "MMI_LONG_HORIZON_RESEARCH_PAYLOAD_V2_JSON_INVALID"
    )

    with pytest.raises(
        lh_v2.MmiLongHorizonResearchPayloadV2Error
    ) as exc_info:
        lh_v2.parse_mmi_long_horizon_research_payload_v2(b"[]")
    assert (
        exc_info.value.code
        == "MMI_LONG_HORIZON_RESEARCH_PAYLOAD_V2_NOT_MAPPING"
    )


def test_missing_or_extra_top_level_key_rejected() -> None:
    for key in ("schema_version", "sources"):
        data = _valid_v2_payload_dict()
        del data[key]
        with pytest.raises(
            lh_v2.MmiLongHorizonResearchPayloadV2Error
        ) as exc_info:
            lh_v2.validate_mmi_long_horizon_research_payload_v2(data)
        assert (
            exc_info.value.code
            == "MMI_LONG_HORIZON_RESEARCH_PAYLOAD_V2_KEYS_INVALID"
        )

    data = _valid_v2_payload_dict()
    data["extra_unauthorized_key"] = "forbidden"
    with pytest.raises(
        lh_v2.MmiLongHorizonResearchPayloadV2Error
    ) as exc_info:
        lh_v2.validate_mmi_long_horizon_research_payload_v2(data)
    assert (
        exc_info.value.code
        == "MMI_LONG_HORIZON_RESEARCH_PAYLOAD_V2_KEYS_INVALID"
    )


def test_empty_sources_rejected() -> None:
    data = _valid_v2_payload_dict(sources=[])
    with pytest.raises(
        lh_v2.MmiLongHorizonResearchPayloadV2Error
    ) as exc_info:
        lh_v2.validate_mmi_long_horizon_research_payload_v2(data)
    assert (
        exc_info.value.code
        == "MMI_LONG_HORIZON_RESEARCH_PAYLOAD_V2_SOURCES_INVALID"
    )


def test_malformed_source_entry_fails_closed() -> None:
    missing_key_entry = {
        k: v for k, v in _entry_dict().items() if k != "excerpt_text"
    }
    data = _valid_v2_payload_dict(sources=[missing_key_entry])
    with pytest.raises(
        lh_v2.MmiLongHorizonResearchPayloadV2Error
    ) as exc_info:
        lh_v2.validate_mmi_long_horizon_research_payload_v2(data)
    assert (
        exc_info.value.code
        == "MMI_LONG_HORIZON_RESEARCH_PAYLOAD_V2_SOURCE_ENTRY_KEYS_INVALID"
    )

    extra_key_entry = _entry_dict()
    extra_key_entry["disposition"] = "BUY"
    data = _valid_v2_payload_dict(sources=[extra_key_entry])
    with pytest.raises(
        lh_v2.MmiLongHorizonResearchPayloadV2Error
    ) as exc_info:
        lh_v2.validate_mmi_long_horizon_research_payload_v2(data)
    assert (
        exc_info.value.code
        == "MMI_LONG_HORIZON_RESEARCH_PAYLOAD_V2_SOURCE_ENTRY_KEYS_INVALID"
    )


def test_source_entry_identity_field_rejected_when_operator_supplied() -> (
    None
):
    entry = _entry_dict()
    entry["source_entry_identity_sha256"] = "0" * 64
    data = _valid_v2_payload_dict(sources=[entry])
    with pytest.raises(
        lh_v2.MmiLongHorizonResearchPayloadV2Error
    ) as exc_info:
        lh_v2.validate_mmi_long_horizon_research_payload_v2(data)
    assert (
        exc_info.value.code
        == "MMI_LONG_HORIZON_RESEARCH_PAYLOAD_V2_SOURCE_ENTRY_KEYS_INVALID"
    )


def test_published_at_invalid_date_syntax_rejected() -> None:
    for bad_date in (
        "2026/06/15",
        "2026-6-15",
        "2026-02-30",
        "2026-13-01",
        "invalid-date",
    ):
        data = _valid_v2_payload_dict(
            sources=[_entry_dict(published_at=bad_date)]
        )
        with pytest.raises(
            lh_v2.MmiLongHorizonResearchPayloadV2Error
        ) as exc_info:
            lh_v2.validate_mmi_long_horizon_research_payload_v2(data)
        assert (
            exc_info.value.code
            == "MMI_LONG_HORIZON_RESEARCH_PAYLOAD_V2_PUBLISHED_AT_INVALID"
        )


def test_syntactically_valid_future_date_not_evaluated_in_v2() -> None:
    data = _valid_v2_payload_dict(
        sources=[_entry_dict(published_at="2099-12-31")]
    )
    payload = lh_v2.validate_mmi_long_horizon_research_payload_v2(data)
    assert payload.sources[0].published_at == "2099-12-31"


def test_excerpt_text_and_provenance_not_silently_normalized() -> None:
    data = _valid_v2_payload_dict(
        sources=[
            _entry_dict(
                publisher="  Leading  Spaced  Publisher  ",
                excerpt_text="  Leading and trailing whitespace preserved. \n\t",
            )
        ]
    )
    payload = lh_v2.validate_mmi_long_horizon_research_payload_v2(data)
    assert payload.sources[0].publisher == "  Leading  Spaced  Publisher  "
    assert (
        payload.sources[0].excerpt_text
        == "  Leading and trailing whitespace preserved. \n\t"
    )


# --------------------------------------------------------------------------
# No candidate-domain/freshness/authority activation.
# --------------------------------------------------------------------------
def test_typed_result_surfaces_have_no_authority_or_disposition_fields() -> (
    None
):
    assert is_dataclass(lh_v2.MmiLongHorizonResearchPayloadV2)
    assert is_dataclass(lh_v2.MmiLongHorizonResearchSourceEntry)

    payload_fields = {
        f.name for f in fields(lh_v2.MmiLongHorizonResearchPayloadV2)
    }
    entry_fields = {
        f.name for f in fields(lh_v2.MmiLongHorizonResearchSourceEntry)
    }

    assert payload_fields == {"schema_version", "sources"}
    assert entry_fields == {
        "publisher",
        "published_at",
        "source_locator",
        "tickers",
        "excerpt_text",
        "source_entry_identity_sha256",
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
        "valid_from",
        "valid_until",
    }
    assert payload_fields.isdisjoint(prohibited_fields)
    assert entry_fields.isdisjoint(prohibited_fields)


def test_no_freshness_or_availability_vocabulary_in_v2_module() -> None:
    module_path = Path(lh_v2.__file__)
    text = module_path.read_text(encoding="utf-8")
    forbidden = (
        "AVAILABLE",
        "SOURCE_UNAVAILABLE",
        "STALE_SOURCE",
        "NO_SOURCE",
        "age_days",
        "valid_until",
        "valid_from",
        "maximum_age_days",
    )
    for token in forbidden:
        assert token not in text, token


def test_no_prohibited_imports_or_network_in_long_horizon_v2_module() -> (
    None
):
    module_path = Path(lh_v2.__file__)
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


# --------------------------------------------------------------------------
# No new resource-policy value; generic catalog/identity compatibility.
# --------------------------------------------------------------------------
def test_existing_source_catalog_and_maximum_bytes_unchanged() -> None:
    assert tuple(MMI_SOURCE_CATALOG) == (
        MmiSourceRole.STRATEGY_SETTINGS,
        MmiSourceRole.PORTFOLIO_SNAPSHOT,
        MmiSourceRole.LONG_HORIZON_RESEARCH,
    )
    spec = MMI_SOURCE_CATALOG[MmiSourceRole.LONG_HORIZON_RESEARCH]
    assert spec.source_id == "MMI_LONG_HORIZON_RESEARCH"
    assert spec.maximum_bytes == 262_144
    assert not hasattr(lh_v2, "max_sources")
    assert not hasattr(lh_v2, "MAX_SOURCES")
    assert not hasattr(lh_v2, "max_excerpt_length")
    assert not hasattr(lh_v2, "MAX_EXCERPT_LENGTH")


def test_generic_source_identities_unaffected_by_v2_existence(
    tmp_path: Path,
) -> None:
    """V2's mere existence must not alter any other role's captured identity."""
    settings_raw = hermetic.strategy_settings_bytes(
        as_of=hermetic.DEFAULT_AS_OF,
        run_timestamp_et=hermetic.DEFAULT_RUN_TIMESTAMP_ET,
    )
    settings_source = hermetic.capture_source(
        tmp_path, role=MmiSourceRole.STRATEGY_SETTINGS, raw=settings_raw
    )
    portfolio_raw = hermetic.portfolio_snapshot_bytes(
        updated=hermetic.DEFAULT_PORTFOLIO_UPDATED,
        rows=hermetic.DEFAULT_PORTFOLIO_ROWS,
    )
    portfolio_source = hermetic.capture_source(
        tmp_path, role=MmiSourceRole.PORTFOLIO_SNAPSHOT, raw=portfolio_raw
    )
    v1_raw = json.dumps(_valid_v1_payload_dict(), indent=2).encode("utf-8")
    v1_source = hermetic.capture_source(
        tmp_path, role=MmiSourceRole.LONG_HORIZON_RESEARCH, raw=v1_raw
    )

    assert settings_source.source_record[
        "observed_sha256"
    ] == hashlib.sha256(settings_raw).hexdigest()
    assert portfolio_source.source_record[
        "observed_sha256"
    ] == hashlib.sha256(portfolio_raw).hexdigest()
    assert v1_source.source_record["observed_sha256"] == hashlib.sha256(
        v1_raw
    ).hexdigest()


# --------------------------------------------------------------------------
# W: source-entry identity canonicalization exceptions stay inside the V2
# reader's own fail-closed error contract (they must not escape as the
# lower-layer MmiCanonicalizationError).
# --------------------------------------------------------------------------
def test_source_entry_identity_canonicalization_exception_translated_to_v2_error() -> (
    None
):
    """A structurally valid entry whose identity preimage exceeds the
    existing generic canonical-JSON node bound must fail closed under
    ``MmiLongHorizonResearchPayloadV2Error``, not the lower-layer
    ``MmiCanonicalizationError``."""
    many_tickers = tuple(f"A{i:04d}" for i in range(17_000))
    assert len(set(many_tickers)) == len(many_tickers)

    # Confirm the lower layer really does reject this preimage (i.e. the
    # gap this test guards against is genuinely reachable, not hypothetical).
    with pytest.raises(mmi_canonical.MmiCanonicalizationError) as canon_exc:
        mmi_canonical.domain_separated_sha256(
            mmi_canonical._MMI_LONG_HORIZON_RESEARCH_SOURCE_ENTRY_V1_IDENTITY_DOMAIN,
            {
                "publisher": "Example Research Institute",
                "published_at": "2026-06-15",
                "source_locator": "docs/thematic/semis_2026.pdf",
                "tickers": sorted(many_tickers),
                "excerpt_text": "Semiconductor capex cycle remains constructive.",
            },
        )
    assert canon_exc.value.code == "MMI_CANONICAL_NODE_COUNT_EXCEEDED"

    raw = json.dumps(
        _valid_v2_payload_dict(sources=[_entry_dict(tickers=many_tickers)]),
        separators=(",", ":"),
    ).encode("utf-8")
    assert len(raw) < 262_144  # stays within the existing source byte bound

    with pytest.raises(
        lh_v2.MmiLongHorizonResearchPayloadV2Error
    ) as exc_info:
        lh_v2.parse_mmi_long_horizon_research_payload_v2(raw)
    assert (
        exc_info.value.code
        == "MMI_LONG_HORIZON_RESEARCH_PAYLOAD_V2_SOURCE_ENTRY_IDENTITY_INVALID"
    )


def test_ordinary_source_entry_identity_bytes_unchanged() -> None:
    """The exception-translation fix must not alter identity bytes for any
    input that previously produced a valid identity."""
    data = _entry_dict()
    payload = lh_v2.validate_mmi_long_horizon_research_payload_v2(
        _valid_v2_payload_dict(sources=[data])
    )
    produced = payload.sources[0].source_entry_identity_sha256

    independent = mmi_canonical.domain_separated_sha256(
        mmi_canonical._MMI_LONG_HORIZON_RESEARCH_SOURCE_ENTRY_V1_IDENTITY_DOMAIN,
        {
            "publisher": data["publisher"],
            "published_at": data["published_at"],
            "source_locator": data["source_locator"],
            "tickers": sorted(data["tickers"]),
            "excerpt_text": data["excerpt_text"],
        },
    )
    assert produced == independent
