"""Strict pure R2F-1b-a memo-envelope contract tests."""

from __future__ import annotations

from datetime import date
import hashlib
import inspect
import json
from pathlib import Path
from typing import Any
import unicodedata

import pytest
import yaml

from investment_orchestrator.research import replacement_generation_reader as reader
from investment_orchestrator.research import replacement_memo_contract as memo
from investment_orchestrator.research import replacement_observation as r2f


def _write(path: Path, value: str | bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(value, bytes):
        path.write_bytes(value)
    else:
        path.write_text(value, encoding="utf-8")


def _setup_repo(root: Path) -> None:
    _write(
        root / "inputs/current/strategy_settings.yaml",
        """as_of: \"2026-07-12\"
benchmark: \"FIXA\"
core_universe: [FIXA]
satellite_universe: [FIXB]
user_approved_extended_etf_static_list: [FIXC]
hard_cap_open_orders_budget: 100
target_new_buy_budget_this_run: 10
max_new_tickers_per_week: 0
ticker_role_fallback:
  FIXA: benchmark_carrier_core
  FIXB: sector_alpha_tilt
  FIXC: extended_etf_minority_sleeve
""",
    )
    _write(root / "inputs/current/portfolio_snapshot.txt", "fixture portfolio\n")
    _write(
        root / "inputs/current/research_anchors.yaml",
        yaml.safe_dump(
            {
                "schema_version": "research_anchors_v1",
                "as_of_date": "2026-07-12",
                "is_llm_generated": False,
                "anchors": [
                    {
                        "anchor_id": "ANCHOR_FIXA",
                        "anchor_type": "structural_theme",
                        "applicable_tickers": ["FIXA"],
                        "anchor_date_et": "2026-07-01",
                        "valid_from": "2026-07-01",
                        "valid_until": "2026-12-31",
                        "source_type": "operator",
                        "confidence_floor": "medium",
                    }
                ],
            },
            sort_keys=False,
        ),
    )
    _write(
        root / "inputs/current/research_anchor_approvals.yaml",
        yaml.safe_dump(
            {
                "schema_version": "research_anchor_approvals_v1",
                "is_llm_generated": False,
                "as_of_date": "2026-07-12",
                "approvals": [],
            },
            sort_keys=False,
        ),
    )
    _write(root / "prompts/analyst_memo.txt", "MEMO\n{{ evidence_packet_json }}\n")


def _capture(root: Path, generation_id: str) -> reader._VerifiedMemoInput:
    return reader._validate_generation_memo_operation_at_root_for_tests(
        generation_id,
        root,
        lambda value: value,
    )


@pytest.fixture
def source_context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Path, str, Path, reader.VerifiedSourceBinding, tuple[reader.EligibleInstrument, ...], tuple[str, ...]]:
    root = tmp_path / "repo"
    _setup_repo(root)
    monkeypatch.setattr(r2f, "repo_root", lambda: root)
    monkeypatch.setattr(r2f, "_today", lambda: date(2026, 7, 12))
    result = r2f.replacement_render()
    generation = Path(result["generation_path"])
    verified = _capture(root, result["generation_id"])
    return (
        root,
        result["generation_id"],
        generation,
        verified.source_binding,
        verified.eligible_instruments,
        verified.active_anchor_ids,
    )


def _raw(value: dict[str, Any], *, newline: str = "\n") -> reader.MemoRawRead:
    encoded = json.dumps(value, ensure_ascii=False, indent=2).replace("\n", newline).encode("utf-8")
    return reader.MemoRawRead(
        raw_bytes=encoded,
        byte_size=len(encoded),
        file_sha256=hashlib.sha256(encoded).hexdigest(),
    )


def _envelope(
    source: reader.VerifiedSourceBinding,
    *,
    memo_result: str = "NO_TRADE",
    observations: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": memo.RAW_MEMO_SCHEMA_VERSION,
        "source_binding": source.to_dict(),
        "memo_result": memo_result,
        "confidence": "MEDIUM",
        "instrument_observations": [] if observations is None else observations,
    }


def _observation(
    *,
    instrument_id: str = "FIXA",
    evidence_id: str = "ANCHOR_FIXA",
    rationale: str = "Bounded qualitative interpretation.",
    research_view: str = "PREFER",
) -> dict[str, Any]:
    return {
        "instrument_id": instrument_id,
        "research_view": research_view,
        "rationale": rationale,
        "evidence_references": [
            {"namespace": memo.ACTIVE_ANCHOR_NAMESPACE, "evidence_id": evidence_id}
        ],
    }


def _validate(
    value: dict[str, Any],
    source: reader.VerifiedSourceBinding,
    eligible: tuple[reader.EligibleInstrument, ...],
    active_ids: tuple[str, ...],
) -> memo.ValidatedMemoEnvelope:
    return memo._validate_memo_raw(
        _raw(value),
        source_binding=source,
        eligible_instruments=eligible,
        active_anchor_ids=active_ids,
    )


def _validate_from_generation(
    root: Path,
    generation_id: str,
    generation: Path,
    raw_bytes: bytes,
) -> memo.ValidatedMemoEnvelope:
    (generation / reader.MEMO_RAW_FILENAME).write_bytes(raw_bytes)
    return memo._validate_generation_memo_at_root_for_tests(generation_id, root)


def _write_valid_bound_memo(generation: Path, source: reader.VerifiedSourceBinding) -> bytes:
    raw = json.dumps(_envelope(source), ensure_ascii=False, indent=2).encode("utf-8")
    (generation / reader.MEMO_RAW_FILENAME).write_bytes(raw)
    return raw


def _assert_private_exception(error: BaseException, code: str) -> None:
    assert error.args == (code,)
    assert error.__cause__ is None
    assert error.__context__ is None
    assert vars(error) == {"code": code}


def test_valid_no_trade_is_strict_and_non_authoritative(
    source_context: tuple[Path, str, Path, reader.VerifiedSourceBinding, tuple[reader.EligibleInstrument, ...], tuple[str, ...]],
) -> None:
    _root, _generation_id, _generation, source, eligible, active_ids = source_context
    result = _validate(_envelope(source), source, eligible, active_ids)
    assert result.payload["normalized_memo"] == {
        "memo_result": "NO_TRADE",
        "confidence": "MEDIUM",
        "instrument_observations": (),
    }
    assert result.payload["artifact_role"] == "NON_AUTHORITATIVE_RESEARCH_OBSERVATION"
    assert result.payload["runtime_consumed"] is False
    assert result.payload["permission_effect"] == "NONE"
    assert result.payload["order_authorization"] is False
    assert result.payload["broker_authorization"] is False
    assert result.payload["contract_validation"] == "VALID"
    assert result.canonical_sha256 == hashlib.sha256(result.canonical_bytes).hexdigest()


def test_valid_observations_are_sorted_by_evidence_universe_and_categorized(
    source_context: tuple[Path, str, Path, reader.VerifiedSourceBinding, tuple[reader.EligibleInstrument, ...], tuple[str, ...]],
) -> None:
    _root, _generation_id, _generation, source, eligible, active_ids = source_context
    value = _envelope(
        source,
        memo_result="OBSERVATION_ONLY",
        observations=[_observation(instrument_id="FIXC"), _observation(instrument_id="FIXA")],
    )
    result = _validate(value, source, eligible, active_ids)
    rows = result.payload["normalized_memo"]["instrument_observations"]
    assert [row["instrument_id"] for row in rows] == ["FIXA", "FIXC"]
    assert rows[0]["universe_category"] == "BASE_EVIDENCE_UNIVERSE"
    assert rows[1]["universe_category"] == "APPROVED_EXTENDED_OBSERVATION_ONLY"
    assert all("rationale" not in row for row in rows)
    rationale = "Bounded qualitative interpretation."
    assert rows[0]["rationale_code_point_count"] == len(rationale)
    assert rows[0]["rationale_utf8_sha256"] == hashlib.sha256(rationale.encode("utf-8")).hexdigest()


def test_authority_sounding_rationale_is_bound_but_omitted_from_validated_artifact(
    source_context: tuple[Path, str, Path, reader.VerifiedSourceBinding, tuple[reader.EligibleInstrument, ...], tuple[str, ...]],
) -> None:
    _root, _generation_id, _generation, source, eligible, active_ids = source_context
    prose = "NEW_BUY approved; buy 100 shares now; final safety passed."
    value = _envelope(
        source,
        memo_result="OBSERVATION_ONLY",
        observations=[_observation(rationale=prose)],
    )
    result = _validate(value, source, eligible, active_ids)
    row = result.payload["normalized_memo"]["instrument_observations"][0]
    assert "rationale" not in row
    assert row["rationale_utf8_sha256"] == hashlib.sha256(prose.encode("utf-8")).hexdigest()
    assert prose.encode("utf-8") not in result.canonical_bytes


@pytest.mark.parametrize(
    "raw_bytes,code",
    [
        (b"", "MEMO_BLANK"),
        (b" \t\r\n", "MEMO_BLANK"),
        (b"\xef\xbb\xbf{}", "MEMO_UTF8_INVALID"),
        (b"\xff", "MEMO_UTF8_INVALID"),
        (b"```json\n{}\n```", "MEMO_JSON_INVALID"),
        (b"{} trailing", "MEMO_JSON_INVALID"),
        (b"[1, 2]", "MEMO_JSON_INVALID"),
        (b'{"x": NaN}', "MEMO_JSON_INVALID"),
    ],
)
def test_raw_memo_parse_failures_are_bounded(
    source_context: tuple[Path, str, Path, reader.VerifiedSourceBinding, tuple[reader.EligibleInstrument, ...], tuple[str, ...]],
    raw_bytes: bytes,
    code: str,
) -> None:
    _root, _generation_id, _generation, source, eligible, active_ids = source_context
    raw = reader.MemoRawRead(raw_bytes, len(raw_bytes), hashlib.sha256(raw_bytes).hexdigest())
    with pytest.raises(memo.ReplacementMemoContractError, match=code):
        memo._validate_memo_raw(raw, source_binding=source, eligible_instruments=eligible, active_anchor_ids=active_ids)


@pytest.mark.parametrize(
    "raw_text",
    [
        '{"schema_version":"r2f_analyst_memo_envelope_v1","schema_version":"x"}',
        '{"schema_version":"r2f_analyst_memo_envelope_v1","source_binding":{"r2f1a_generation_id":"a","r2f1a_generation_id":"b"}}',
        '{"schema_version":"r2f_analyst_memo_envelope_v1","source_binding":{},"memo_result":"OBSERVATION_ONLY","confidence":"LOW","instrument_observations":[{"instrument_id":"FIXA","instrument_id":"FIXB"}]}',
        '{"schema_version":"r2f_analyst_memo_envelope_v1","source_binding":{},"memo_result":"OBSERVATION_ONLY","confidence":"LOW","instrument_observations":[{"instrument_id":"FIXA","research_view":"PREFER","rationale":"x","evidence_references":[{"namespace":"ACTIVE_ANCHOR","namespace":"x","evidence_id":"ANCHOR_FIXA"}]}]}',
    ],
)
def test_duplicate_keys_at_every_object_level_are_rejected(
    source_context: tuple[Path, str, Path, reader.VerifiedSourceBinding, tuple[reader.EligibleInstrument, ...], tuple[str, ...]],
    raw_text: str,
) -> None:
    _root, _generation_id, _generation, source, eligible, active_ids = source_context
    raw_bytes = raw_text.encode("utf-8")
    raw = reader.MemoRawRead(raw_bytes, len(raw_bytes), hashlib.sha256(raw_bytes).hexdigest())
    with pytest.raises(memo.ReplacementMemoContractError, match="MEMO_DUPLICATE_KEY"):
        memo._validate_memo_raw(raw, source_binding=source, eligible_instruments=eligible, active_anchor_ids=active_ids)


@pytest.mark.parametrize("location", ["top", "binding", "observation", "reference"])
def test_unknown_fields_are_rejected_at_every_object_level(
    source_context: tuple[Path, str, Path, reader.VerifiedSourceBinding, tuple[reader.EligibleInstrument, ...], tuple[str, ...]],
    location: str,
) -> None:
    _root, _generation_id, _generation, source, eligible, active_ids = source_context
    value = _envelope(source, memo_result="OBSERVATION_ONLY", observations=[_observation()])
    if location == "top":
        value["unknown"] = True
    elif location == "binding":
        value["source_binding"]["unknown"] = True
    elif location == "observation":
        value["instrument_observations"][0]["unknown"] = True
    else:
        value["instrument_observations"][0]["evidence_references"][0]["unknown"] = True
    with pytest.raises(memo.ReplacementMemoContractError, match="MEMO_KEY_CLOSURE_INVALID|MEMO_SOURCE_BINDING_MISMATCH"):
        _validate(value, source, eligible, active_ids)


@pytest.mark.parametrize("field", ["schema_version", "source_binding", "memo_result", "confidence", "instrument_observations"])
def test_missing_required_top_level_field_is_rejected(
    source_context: tuple[Path, str, Path, reader.VerifiedSourceBinding, tuple[reader.EligibleInstrument, ...], tuple[str, ...]],
    field: str,
) -> None:
    _root, _generation_id, _generation, source, eligible, active_ids = source_context
    value = _envelope(source)
    value.pop(field)
    with pytest.raises(memo.ReplacementMemoContractError, match="MEMO_KEY_CLOSURE_INVALID"):
        _validate(value, source, eligible, active_ids)


@pytest.mark.parametrize("location,key", [("binding", "as_of"), ("observation", "rationale"), ("reference", "evidence_id")])
def test_missing_required_nested_field_is_rejected(
    source_context: tuple[Path, str, Path, reader.VerifiedSourceBinding, tuple[reader.EligibleInstrument, ...], tuple[str, ...]],
    location: str,
    key: str,
) -> None:
    _root, _generation_id, _generation, source, eligible, active_ids = source_context
    value = _envelope(source, memo_result="OBSERVATION_ONLY", observations=[_observation()])
    if location == "binding":
        value["source_binding"].pop(key)
    elif location == "observation":
        value["instrument_observations"][0].pop(key)
    else:
        value["instrument_observations"][0]["evidence_references"][0].pop(key)
    with pytest.raises(memo.ReplacementMemoContractError, match="MEMO_KEY_CLOSURE_INVALID|MEMO_SOURCE_BINDING_MISMATCH"):
        _validate(value, source, eligible, active_ids)


def test_oversize_memo_is_rejected_before_json_processing(
    source_context: tuple[Path, str, Path, reader.VerifiedSourceBinding, tuple[reader.EligibleInstrument, ...], tuple[str, ...]],
) -> None:
    _root, _generation_id, _generation, source, eligible, active_ids = source_context
    raw_bytes = b"x" * (memo.MAXIMUM_MEMO_BYTES + 1)
    raw = reader.MemoRawRead(raw_bytes, len(raw_bytes), hashlib.sha256(raw_bytes).hexdigest())
    with pytest.raises(memo.ReplacementMemoContractError, match="MEMO_TOO_LARGE"):
        memo._validate_memo_raw(raw, source_binding=source, eligible_instruments=eligible, active_anchor_ids=active_ids)


@pytest.mark.parametrize("field", [
    "r2f1a_generation_id",
    "replacement_input_manifest_file_sha256",
    "replacement_input_manifest_canonical_sha256",
    "evidence_packet_file_sha256",
    "evidence_packet_canonical_sha256",
    "analyst_memo_prompt_file_sha256",
    "as_of",
])
def test_every_source_binding_value_is_verified(
    source_context: tuple[Path, str, Path, reader.VerifiedSourceBinding, tuple[reader.EligibleInstrument, ...], tuple[str, ...]],
    field: str,
) -> None:
    _root, _generation_id, _generation, source, eligible, active_ids = source_context
    value = _envelope(source)
    value["source_binding"][field] = "0" * 64 if field != "as_of" else "2026-07-11"
    with pytest.raises(memo.ReplacementMemoContractError, match="MEMO_SOURCE_BINDING_MISMATCH"):
        _validate(value, source, eligible, active_ids)


@pytest.mark.parametrize(
    "mutation,code",
    [
        (lambda value: value.update({"schema_version": "analyst_memo_v1"}), "MEMO_SCHEMA_UNSUPPORTED"),
        (lambda value: value.update({"memo_result": "no_trade"}), "MEMO_SCHEMA_UNSUPPORTED"),
        (lambda value: value.update({"confidence": "medium"}), "MEMO_SCHEMA_UNSUPPORTED"),
        (lambda value: value.update({"memo_result": "NO_TRADE", "instrument_observations": [_observation()]}), "MEMO_RESULT_CONTRADICTORY"),
    ],
)
def test_legacy_or_noncanonical_enums_and_contradictory_result_fail_closed(
    source_context: tuple[Path, str, Path, reader.VerifiedSourceBinding, tuple[reader.EligibleInstrument, ...], tuple[str, ...]],
    mutation: Any,
    code: str,
) -> None:
    _root, _generation_id, _generation, source, eligible, active_ids = source_context
    value = _envelope(source)
    mutation(value)
    with pytest.raises(memo.ReplacementMemoContractError, match=code):
        _validate(value, source, eligible, active_ids)


@pytest.mark.parametrize(
    "instrument_id,code",
    [
        ("fixa", "MEMO_IDENTIFIER_INVALID"),
        ("FIXA ", "MEMO_IDENTIFIER_INVALID"),
        ("UNKNOWN", "MEMO_IDENTIFIER_INVALID"),
    ],
)
def test_unknown_lowercase_and_aliased_identifiers_fail_closed(
    source_context: tuple[Path, str, Path, reader.VerifiedSourceBinding, tuple[reader.EligibleInstrument, ...], tuple[str, ...]],
    instrument_id: str,
    code: str,
) -> None:
    _root, _generation_id, _generation, source, eligible, active_ids = source_context
    value = _envelope(source, memo_result="OBSERVATION_ONLY", observations=[_observation(instrument_id=instrument_id)])
    with pytest.raises(memo.ReplacementMemoContractError, match=code):
        _validate(value, source, eligible, active_ids)


def test_duplicate_instrument_and_evidence_references_fail_closed(
    source_context: tuple[Path, str, Path, reader.VerifiedSourceBinding, tuple[reader.EligibleInstrument, ...], tuple[str, ...]],
) -> None:
    _root, _generation_id, _generation, source, eligible, active_ids = source_context
    duplicate_instrument = _envelope(
        source,
        memo_result="OBSERVATION_ONLY",
        observations=[_observation(), _observation()],
    )
    with pytest.raises(memo.ReplacementMemoContractError, match="MEMO_IDENTIFIER_INVALID"):
        _validate(duplicate_instrument, source, eligible, active_ids)

    duplicate_reference = _envelope(source, memo_result="OBSERVATION_ONLY", observations=[_observation()])
    duplicate_reference["instrument_observations"][0]["evidence_references"] *= 2
    with pytest.raises(memo.ReplacementMemoContractError, match="MEMO_EVIDENCE_REFERENCE_INVALID"):
        _validate(duplicate_reference, source, eligible, active_ids)


@pytest.mark.parametrize("evidence_id", ["UNKNOWN", "anchor_fixa", "ANCHOR_FIXA "])
def test_unknown_or_noncanonical_evidence_reference_fails_closed(
    source_context: tuple[Path, str, Path, reader.VerifiedSourceBinding, tuple[reader.EligibleInstrument, ...], tuple[str, ...]],
    evidence_id: str,
) -> None:
    _root, _generation_id, _generation, source, eligible, active_ids = source_context
    value = _envelope(source, memo_result="OBSERVATION_ONLY", observations=[_observation(evidence_id=evidence_id)])
    with pytest.raises(memo.ReplacementMemoContractError, match="MEMO_EVIDENCE_REFERENCE_INVALID"):
        _validate(value, source, eligible, active_ids)


def test_reference_order_is_canonical_and_inactive_anchor_set_is_not_accepted(
    source_context: tuple[Path, str, Path, reader.VerifiedSourceBinding, tuple[reader.EligibleInstrument, ...], tuple[str, ...]],
) -> None:
    _root, _generation_id, _generation, source, eligible, _active_ids = source_context
    value = _envelope(source, memo_result="OBSERVATION_ONLY", observations=[_observation()])
    value["instrument_observations"][0]["evidence_references"] = [
        {"namespace": "ACTIVE_ANCHOR", "evidence_id": "Z_ANCHOR"},
        {"namespace": "ACTIVE_ANCHOR", "evidence_id": "ANCHOR_FIXA"},
    ]
    result = _validate(value, source, eligible, ("ANCHOR_FIXA", "Z_ANCHOR"))
    refs = result.payload["normalized_memo"]["instrument_observations"][0]["evidence_references"]
    assert [row["evidence_id"] for row in refs] == ["ANCHOR_FIXA", "Z_ANCHOR"]
    with pytest.raises(memo.ReplacementMemoContractError, match="MEMO_EVIDENCE_REFERENCE_INVALID"):
        _validate(value, source, eligible, ())


@pytest.mark.parametrize(
    "rationale",
    [
        "",
        "   ",
        "x" * 281,
        "line one\nline two",
        "line one\u2028line two",
        "line one\u2029line two",
        "control\x00character",
        "control\x7fcharacter",
        "control\x85character",
        unicodedata.normalize("NFD", "é"),
    ],
)
def test_rationale_must_be_bounded_single_line_and_pre_normalized_nfc(
    source_context: tuple[Path, str, Path, reader.VerifiedSourceBinding, tuple[reader.EligibleInstrument, ...], tuple[str, ...]],
    rationale: str,
) -> None:
    _root, _generation_id, _generation, source, eligible, active_ids = source_context
    value = _envelope(source, memo_result="OBSERVATION_ONLY", observations=[_observation(rationale=rationale)])
    with pytest.raises(memo.ReplacementMemoContractError, match="MEMO_SCHEMA_UNSUPPORTED"):
        _validate(value, source, eligible, active_ids)


@pytest.mark.parametrize("rationale", ["x", "x" * 280, "ordinary\u2003Unicode spacing"])
def test_rationale_accepts_exact_bounds_and_ordinary_unicode_spacing(
    source_context: tuple[Path, str, Path, reader.VerifiedSourceBinding, tuple[reader.EligibleInstrument, ...], tuple[str, ...]],
    rationale: str,
) -> None:
    _root, _generation_id, _generation, source, eligible, active_ids = source_context
    result = _validate(
        _envelope(source, memo_result="OBSERVATION_ONLY", observations=[_observation(rationale=rationale)]),
        source,
        eligible,
        active_ids,
    )
    row = result.payload["normalized_memo"]["instrument_observations"][0]
    assert row["rationale_code_point_count"] == len(rationale)
    assert row["rationale_utf8_sha256"] == hashlib.sha256(rationale.encode("utf-8")).hexdigest()


def test_raw_and_normalized_newline_identities_remain_distinct_but_deterministic(
    source_context: tuple[Path, str, Path, reader.VerifiedSourceBinding, tuple[reader.EligibleInstrument, ...], tuple[str, ...]],
) -> None:
    _root, _generation_id, _generation, source, eligible, active_ids = source_context
    value = _envelope(source)
    lf = memo._validate_memo_raw(_raw(value, newline="\n"), source_binding=source, eligible_instruments=eligible, active_anchor_ids=active_ids)
    crlf = memo._validate_memo_raw(_raw(value, newline="\r\n"), source_binding=source, eligible_instruments=eligible, active_anchor_ids=active_ids)
    lone_cr = memo._validate_memo_raw(_raw(value, newline="\r"), source_binding=source, eligible_instruments=eligible, active_anchor_ids=active_ids)
    raw_hashes = {
        lf.payload["memo_input"]["file_sha256"],
        crlf.payload["memo_input"]["file_sha256"],
        lone_cr.payload["memo_input"]["file_sha256"],
    }
    assert len(raw_hashes) == 3
    normalized_hashes = {
        lf.payload["memo_input"]["normalized_text_sha256"],
        crlf.payload["memo_input"]["normalized_text_sha256"],
        lone_cr.payload["memo_input"]["normalized_text_sha256"],
    }
    assert len(normalized_hashes) == 1
    assert lf.payload["normalized_memo"] == crlf.payload["normalized_memo"] == lone_cr.payload["normalized_memo"]
    assert len({lf.canonical_bytes, crlf.canonical_bytes, lone_cr.canonical_bytes}) == 3


def test_validate_verified_generation_memo_captures_editable_file_and_writes_nothing(
    source_context: tuple[Path, str, Path, reader.VerifiedSourceBinding, tuple[reader.EligibleInstrument, ...], tuple[str, ...]],
) -> None:
    root, generation_id, generation, source, _eligible, _active_ids = source_context
    value = _envelope(source, memo_result="OBSERVATION_ONLY", observations=[_observation()])
    raw = json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8")
    (generation / reader.MEMO_RAW_FILENAME).write_bytes(raw)
    before = {path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()}
    result = memo._validate_generation_memo_at_root_for_tests(generation_id, root)
    after = {path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()}
    assert result.payload["memo_input"]["file_sha256"] == hashlib.sha256(raw).hexdigest()
    assert after == before


@pytest.mark.parametrize(
    "raw_bytes,code",
    [
        (b'{"private":"SENTINEL"', "MEMO_JSON_INVALID"),
        (b'{"schema_version":"a","schema_version":"SENTINEL"}', "MEMO_DUPLICATE_KEY"),
        (b'{"source_binding":{"as_of":"a","as_of":"SENTINEL"}}', "MEMO_DUPLICATE_KEY"),
    ],
)
def test_memo_parser_exceptions_retain_only_bounded_code(
    source_context: tuple[Path, str, Path, reader.VerifiedSourceBinding, tuple[reader.EligibleInstrument, ...], tuple[str, ...]],
    raw_bytes: bytes,
    code: str,
) -> None:
    root, generation_id, generation, _source, _eligible, _active_ids = source_context
    with pytest.raises(memo.ReplacementMemoContractError) as raised:
        _validate_from_generation(root, generation_id, generation, raw_bytes)
    _assert_private_exception(raised.value, code)


def test_production_validator_is_one_shot_and_has_no_claim_or_handle_api() -> None:
    assert tuple(inspect.signature(memo.validate_generation_memo).parameters) == ("generation_id",)
    assert "validate_generation_memo" in memo.__all__
    assert not hasattr(memo, "validate_verified_generation_memo")
    assert not hasattr(reader, "VerifiedR2F1aGeneration")
    assert not hasattr(reader, "open_verified_generation")
    assert not hasattr(reader, "_LIVE_VERIFIED_HANDLES")
    assert not hasattr(reader, "_VerifiedHandleSeal")
    assert "_validate_memo_raw" not in memo.__all__
    assert not hasattr(memo, "validate_memo_raw")


def test_public_one_shot_api_returns_deeply_immutable_descriptor_free_result(
    source_context: tuple[Path, str, Path, reader.VerifiedSourceBinding, tuple[reader.EligibleInstrument, ...], tuple[str, ...]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, generation_id, generation, source, _eligible, _active_ids = source_context
    _write_valid_bound_memo(generation, source)
    monkeypatch.setattr(reader, "repo_root", lambda: root)
    result = memo.validate_generation_memo(generation_id)
    assert result.payload["contract_validation"] == "VALID"
    with pytest.raises(TypeError):
        result.payload["contract_validation"] = "INVALID"  # type: ignore[index]
    with pytest.raises(TypeError):
        result.payload["normalized_memo"]["memo_result"] = "OTHER"  # type: ignore[index]
    with pytest.raises(AttributeError):
        result.canonical_bytes = b"changed"  # type: ignore[misc]
    assert not hasattr(result, "close")
    assert all(token not in repr(result.payload).lower() for token in ("repository_fd", "generation_fd", "directory_chain", "weakref"))
    assert tuple(inspect.signature(memo.validate_generation_memo).parameters) == ("generation_id",)


def test_validated_result_construction_failure_closes_operation_descriptors(
    source_context: tuple[Path, str, Path, reader.VerifiedSourceBinding, tuple[reader.EligibleInstrument, ...], tuple[str, ...]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if not Path("/proc/self/fd").exists():
        pytest.skip("/proc fd accounting unavailable")
    root, generation_id, generation, source, _eligible, _active_ids = source_context
    _write_valid_bound_memo(generation, source)
    baseline = len(list(Path("/proc/self/fd").iterdir()))
    monkeypatch.setattr(
        memo,
        "_deep_freeze",
        lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("PRIVATE_RESULT_FAILURE")),
    )
    with pytest.raises(reader.ReplacementGenerationReaderError) as raised:
        memo._validate_generation_memo_at_root_for_tests(generation_id, root)
    assert raised.value.args == ("SOURCE_GENERATION_INVALID",)
    assert raised.value.__cause__ is None and raised.value.__context__ is None
    assert len(list(Path("/proc/self/fd").iterdir())) == baseline


def test_multi_defect_failure_precedence_is_deterministic(
    source_context: tuple[Path, str, Path, reader.VerifiedSourceBinding, tuple[reader.EligibleInstrument, ...], tuple[str, ...]],
) -> None:
    _root, _generation_id, _generation, source, eligible, active_ids = source_context

    binding_first = _envelope(source, memo_result="NO_TRADE", observations=[_observation()])
    binding_first["source_binding"]["as_of"] = "2026-07-11"
    with pytest.raises(memo.ReplacementMemoContractError, match="MEMO_SOURCE_BINDING_MISMATCH"):
        _validate(binding_first, source, eligible, active_ids)

    contradiction_first = _envelope(
        source,
        memo_result="NO_TRADE",
        observations=[_observation(instrument_id="UNKNOWN", evidence_id="UNKNOWN", rationale="bad\nline")],
    )
    with pytest.raises(memo.ReplacementMemoContractError, match="MEMO_RESULT_CONTRADICTORY"):
        _validate(contradiction_first, source, eligible, active_ids)

    identifier_first = _envelope(
        source,
        memo_result="OBSERVATION_ONLY",
        observations=[_observation(instrument_id="UNKNOWN", evidence_id="UNKNOWN", rationale="bad\nline")],
    )
    with pytest.raises(memo.ReplacementMemoContractError, match="MEMO_IDENTIFIER_INVALID"):
        _validate(identifier_first, source, eligible, active_ids)

    reference_first = _envelope(
        source,
        memo_result="OBSERVATION_ONLY",
        observations=[_observation(evidence_id="UNKNOWN", rationale="bad\nline")],
    )
    with pytest.raises(memo.ReplacementMemoContractError, match="MEMO_EVIDENCE_REFERENCE_INVALID"):
        _validate(reference_first, source, eligible, active_ids)


def test_canonical_bytes_are_reproducible(
    source_context: tuple[Path, str, Path, reader.VerifiedSourceBinding, tuple[reader.EligibleInstrument, ...], tuple[str, ...]],
) -> None:
    _root, _generation_id, _generation, source, eligible, active_ids = source_context
    value = _envelope(source, memo_result="OBSERVATION_ONLY", observations=[_observation()])
    first = _validate(value, source, eligible, active_ids)
    second = _validate(value, source, eligible, active_ids)
    assert first.canonical_bytes == second.canonical_bytes
    assert first.canonical_sha256 == second.canonical_sha256


def test_no_runtime_consumer_or_permission_effect() -> None:
    root = Path(__file__).resolve().parents[2]
    prohibited = (
        root / "src/investment_orchestrator/state",
        root / "src/investment_orchestrator/workflow",
        root / "src/investment_orchestrator/cli",
    )
    for directory in prohibited:
        for path in directory.rglob("*.py"):
            if path.name == "replacement_memo_contract.py":
                continue
            text = path.read_text(encoding="utf-8")
            assert "replacement_memo_contract" not in text
            assert "replacement_generation_reader" not in text


def test_nonactionable_state_and_order_permissions_remain_closed() -> None:
    from investment_orchestrator.state.research_availability import (  # local to prove no import path from contracts
        STRICT_FRESH_GROUNDED_MEMO_NON_ACTIONABLE,
        _ALLOWED_ACTIONS_BY_STATE,
    )

    allowed = _ALLOWED_ACTIONS_BY_STATE[STRICT_FRESH_GROUNDED_MEMO_NON_ACTIONABLE]
    assert allowed == ("HOLD", "NO_TRADE")
    for action in ("NEW_BUY", "SELL", "ORDER_COMPILATION"):
        assert action not in allowed
