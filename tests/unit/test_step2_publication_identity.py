from __future__ import annotations

import ast
from collections.abc import Mapping
from copy import deepcopy
import hashlib
import inspect
import json
from pathlib import Path
import random
import sys
import threading
from typing import Any, Callable

from jsonschema import Draft202012Validator
import pytest

import investment_orchestrator.state.step2_publication_identity as publication_identity
from investment_orchestrator.common.paths import repo_root
from investment_orchestrator.research.actionable_handoff_candidate import (
    CANDIDATE_SCHEMA_VERSION,
)
from investment_orchestrator.research.actionable_promotion_pointer import (
    PERMISSION_EFFECT_PENDING_GATES,
    POINTER_SOURCE,
    PROMOTION_STATUS_PENDING_GATES,
    SCHEMA_VERSION as ACTIVE_POINTER_SCHEMA_VERSION,
)
from investment_orchestrator.state.research_degraded_mode_gate import (
    MODE_PROMOTED_STEP2_DECISION_ONLY,
    MODE_STRICT_FRESH_ACTIONABLE,
    PROMOTED_RESEARCH_DECISION_ACTION,
    PROMOTED_SOURCE,
    PROMOTED_STEP2_DECISION_ONLY_STATE,
)
from investment_orchestrator.state.step2_publication_identity import (
    GATE_ALLOWED_REASON_CODE,
    MAX_JSON_NESTING_DEPTH,
    MAX_JSON_NODE_COUNT,
    RECEIPT_SCHEMA_FILENAME,
    RECEIPT_SCHEMA_VERSION,
    VERIFICATION_BOOLEAN_COERCION_ERROR,
    Step2PublicationIdentityDiagnostic,
    Step2PublicationIdentityError,
    build_step2_publication_receipt,
    canonical_json_bytes,
    derive_generation_id,
    is_step2_publication_receipt_schema_valid,
    sha256_exact_bytes,
    verify_step2_publication_receipt,
)


EVALUATED_DATE = "2026-06-28"
TEMPLATE_BYTES = b"TEMPLATE2_OUTPUT_START\nHOLD / NO_TRADE fixture\nTEMPLATE2_OUTPUT_END\n"
PACKET_BYTES = b'{"decision_builder_ready_for_audit":true}\n'
PROMPT_BYTES = b"captured prompt\r\n"
RAW_BYTES = b"captured raw output\n"
SETTINGS_BYTES = b"as_of: 2026-06-28\n"


def _json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _canonical_mapping_sha256(value: Any) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _legacy_permission() -> dict[str, Any]:
    return {
        "state": "STRICT_FRESH",
        "allowed_actions": ["HOLD", "NO_TRADE", "SELL", "NEW_BUY", "ORDER_COMPILATION"],
        "blocked_actions": [],
        "manual_review_required": False,
        "blocker_reasons": [],
    }


def _promoted_permission() -> dict[str, Any]:
    return {
        "state": PROMOTED_STEP2_DECISION_ONLY_STATE,
        "allowed_actions": ["HOLD", "NO_TRADE", PROMOTED_RESEARCH_DECISION_ACTION],
        "blocked_actions": ["NEW_BUY", "ORDER_COMPILATION"],
        "manual_review_required": False,
        "blocker_reasons": [],
        "source": PROMOTED_SOURCE,
        "promoted_step2_decision_only": True,
    }


def _effective_handoff() -> dict[str, Any]:
    return {
        "schema_version": CANDIDATE_SCHEMA_VERSION,
        "is_llm_generated": False,
        "report_only": True,
        "not_authorization": True,
        "trade_universe": {"allowed_buy_tickers": ["QQQ", "VOO", "SMH"]},
        "optional_extended_etf_sleeve": {"enabled": False},
        "buy_universe_scorecard": [
            {"ticker": "QQQ", "actionability_status": "actionable_this_run"},
            {"ticker": "VOO", "actionability_status": "ranking_hold_watch_only"},
        ],
        "strategy_a_research_handoff": {
            "positive_delta_research_supported": ["QQQ"]
        },
    }


def _promoted_sources() -> dict[str, bytes]:
    effective = _effective_handoff()
    effective_hash = _canonical_mapping_sha256(effective)
    pointer = {
        "schema_version": ACTIVE_POINTER_SCHEMA_VERSION,
        "source": POINTER_SOURCE,
        "promotion_status": PROMOTION_STATUS_PENDING_GATES,
        "permission_effect": PERMISSION_EFFECT_PENDING_GATES,
        "not_authorization": True,
        "future_pr_required": True,
        "consumed_by_availability": False,
        "consumed_by_step2": False,
        "consumed_by_gates": False,
        "candidate_actionable_row_count": 1,
        "actionable_this_run_tickers": ["QQQ"],
        "promotion_expires_at": "2026-07-31",
        "effective_handoff_sha256": effective_hash,
        "candidate_sha256": effective_hash,
    }
    validation = {
        "valid": True,
        "effective_handoff_sha256": effective_hash,
    }
    return {
        "promoted_active_pointer_bytes": _json_bytes(pointer),
        "promoted_effective_handoff_bytes": _json_bytes(effective),
        "promoted_effective_validation_bytes": _json_bytes(validation),
    }


def _legacy_kwargs() -> dict[str, Any]:
    return {
        "publication_mode": MODE_STRICT_FRESH_ACTIONABLE,
        "evaluated_date": EVALUATED_DATE,
        "template2_output_bytes": TEMPLATE_BYTES,
        "decision_packet_bytes": PACKET_BYTES,
        "prompt_bytes": PROMPT_BYTES,
        "raw_output_bytes": RAW_BYTES,
        "normalization_settings_bytes": SETTINGS_BYTES,
        "permission_artifact_bytes": _json_bytes(_legacy_permission()),
    }


def _promoted_kwargs() -> dict[str, Any]:
    return {
        **_legacy_kwargs(),
        "publication_mode": MODE_PROMOTED_STEP2_DECISION_ONLY,
        "permission_artifact_bytes": _json_bytes(_promoted_permission()),
        **_promoted_sources(),
    }


def _verify(receipt: Any, kwargs: dict[str, Any], **overrides: Any):
    inputs = {**kwargs, **overrides}
    inputs["expected_publication_mode"] = inputs.pop("publication_mode")
    inputs["expected_evaluated_date"] = inputs.pop("evaluated_date")
    return verify_step2_publication_receipt(receipt=receipt, **inputs)


def _receipt_schema() -> dict[str, Any]:
    return json.loads(
        (repo_root() / "schemas" / RECEIPT_SCHEMA_FILENAME).read_text(
            encoding="utf-8"
        )
    )


def _schema_is_valid(receipt: Any) -> bool:
    return is_step2_publication_receipt_schema_valid(
        receipt,
        schema=_receipt_schema(),
    )


def _mapping_chain(root: dict[str, Any], depth: int) -> dict[str, Any]:
    cursor = root
    for _ in range(depth):
        child: dict[str, Any] = {}
        cursor["padding"] = child
        cursor = child
    return root


def _list_chain(depth: int) -> list[Any]:
    root: list[Any] = []
    cursor = root
    for _ in range(depth):
        child: list[Any] = []
        cursor.append(child)
        cursor = child
    return root


def _parser_recursion_json_bytes() -> bytes:
    return b'{"padding":' + (b"[" * 2000) + b"0" + (b"]" * 2000) + b"}"


def _excessive_node_json_bytes() -> bytes:
    # root object + child list + scalar elements == MAX_JSON_NODE_COUNT + 1
    return _json_bytes({"padding": [None] * (MAX_JSON_NODE_COUNT - 1)})


def _malformed_source_bytes(case: str) -> bytes:
    if case == "malformed_utf8":
        return b"\xff"
    if case == "malformed_json":
        return b'{"broken":'
    if case == "excessive_nodes":
        return _excessive_node_json_bytes()
    if case == "parser_recursion":
        return _parser_recursion_json_bytes()
    if case == "semantic_mismatch":
        return _json_bytes({})
    raise AssertionError("unknown test case")


def _canonical_json_oracle(value: dict[str, Any]) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _generated_supported_json_corpus() -> list[dict[str, Any]]:
    random_source = random.Random(0x2B_C1)
    scalars: list[Any] = [
        None,
        False,
        True,
        0,
        -1,
        2**127,
        "",
        "plain",
        "caf\N{LATIN SMALL LETTER E WITH ACUTE}",
        "quote\" slash\\ control\n",
    ]

    def generated_value(depth: int) -> Any:
        if depth == 4 or random_source.randrange(3) == 0:
            return random_source.choice(scalars)
        if random_source.randrange(2) == 0:
            return [
                generated_value(depth + 1)
                for _ in range(random_source.randrange(4))
            ]
        return {
            f"k{index}_{random_source.randrange(7)}": generated_value(depth + 1)
            for index in range(random_source.randrange(4))
        }

    return [
        {"case": generated_value(0), "ordinal": index}
        for index in range(64)
    ]


def _promoted_kwargs_with_expiration(value: str) -> dict[str, Any]:
    kwargs = _promoted_kwargs()
    kwargs["evaluated_date"] = "2024-01-01"
    pointer = json.loads(kwargs["promoted_active_pointer_bytes"])
    pointer["promotion_expires_at"] = value
    kwargs["promoted_active_pointer_bytes"] = _json_bytes(pointer)
    return kwargs


def _promoted_kwargs_with_source_depth(
    source_name: str,
    depth: int,
) -> dict[str, Any]:
    kwargs = _promoted_kwargs()
    pointer = json.loads(kwargs["promoted_active_pointer_bytes"])
    effective = json.loads(kwargs["promoted_effective_handoff_bytes"])
    validation = json.loads(kwargs["promoted_effective_validation_bytes"])
    values = {
        "promoted_active_pointer_bytes": pointer,
        "promoted_effective_handoff_bytes": effective,
        "promoted_effective_validation_bytes": validation,
    }
    _mapping_chain(values[source_name], depth)
    if source_name == "promoted_effective_handoff_bytes":
        effective_hash = _canonical_mapping_sha256(effective)
        pointer["effective_handoff_sha256"] = effective_hash
        pointer["candidate_sha256"] = effective_hash
        validation["effective_handoff_sha256"] = effective_hash
    kwargs.update({name: _json_bytes(value) for name, value in values.items()})
    return kwargs


def _assert_diagnostic(result: Any, expected: Step2PublicationIdentityDiagnostic) -> None:
    assert result.identity_consistent is False
    assert result.diagnostic_code is expected
    assert result.generation_id is None
    assert result.identity_only is True
    assert result.not_authorization is True
    assert result.permission_effect == "none"
    assert result.to_dict()["diagnostic_code"] == expected.value


def test_valid_legacy_contract_hashes_exact_bytes_and_verifies() -> None:
    kwargs = _legacy_kwargs()
    receipt = build_step2_publication_receipt(**kwargs)

    assert receipt["schema_version"] == RECEIPT_SCHEMA_VERSION
    assert receipt["publication_mode"] == MODE_STRICT_FRESH_ACTIONABLE
    assert receipt["evaluated_date"] == EVALUATED_DATE
    assert receipt["candidate_identities"] == {
        "template2_output_sha256": hashlib.sha256(TEMPLATE_BYTES).hexdigest(),
        "decision_packet_sha256": hashlib.sha256(PACKET_BYTES).hexdigest(),
    }
    assert receipt["input_identities"] == {
        "prompt_sha256": hashlib.sha256(PROMPT_BYTES).hexdigest(),
        "raw_output_sha256": hashlib.sha256(RAW_BYTES).hexdigest(),
        "normalization_settings_sha256": hashlib.sha256(SETTINGS_BYTES).hexdigest(),
        "permission_artifact_sha256": hashlib.sha256(
            kwargs["permission_artifact_bytes"]
        ).hexdigest(),
    }
    assert receipt["promoted_source_identities"] is None
    assert receipt["gate_result"]["allowed"] is True
    assert receipt["gate_result"]["reason_code"] == GATE_ALLOWED_REASON_CODE
    assert "allowed_actions" not in receipt["gate_result"]
    assert receipt["identity_only"] is True
    assert receipt["not_authorization"] is True
    assert receipt["permission_effect"] == "none"

    result = _verify(receipt, kwargs)
    assert result.identity_consistent is True
    assert result.diagnostic_code is Step2PublicationIdentityDiagnostic.IDENTITY_CONSISTENT
    assert result.generation_id == receipt["generation_id"]


def test_repeated_legacy_build_is_deterministic_and_order_independent() -> None:
    kwargs = _legacy_kwargs()
    first = build_step2_publication_receipt(**kwargs)
    second = build_step2_publication_receipt(**dict(reversed(list(kwargs.items()))))
    material = {key: value for key, value in first.items() if key != "generation_id"}

    assert first == second
    assert first["generation_id"] == derive_generation_id(material)
    assert canonical_json_bytes({"b": 2, "a": 1}) == canonical_json_bytes(
        {"a": 1, "b": 2}
    )


def test_exact_bytes_preserve_line_endings_and_terminal_newline_identity() -> None:
    assert sha256_exact_bytes(b"x\n") != sha256_exact_bytes(b"x\r\n")
    assert sha256_exact_bytes(b"x") != sha256_exact_bytes(b"x\n")


@pytest.mark.parametrize(
    "payload",
    [
        b"",
        b"\n",
        b"line\n",
        b"line\r\n",
        "caf\N{LATIN SMALL LETTER E WITH ACUTE}".encode("utf-8"),
        b"\xef\xbb\xbfBOM",
        b"\x00\xff\x80binary\x00",
    ],
)
def test_arbitrary_candidate_bytes_remain_exact_and_unnormalized(payload: bytes) -> None:
    kwargs = {**_legacy_kwargs(), "template2_output_bytes": payload}
    receipt = build_step2_publication_receipt(**kwargs)
    assert receipt["candidate_identities"]["template2_output_sha256"] == (
        hashlib.sha256(payload).hexdigest()
    )
    assert sha256_exact_bytes(payload) == hashlib.sha256(payload).hexdigest()
    assert _verify(receipt, kwargs).identity_consistent is True


def test_valid_promoted_contract_requires_and_verifies_all_source_identities() -> None:
    kwargs = _promoted_kwargs()
    receipt = build_step2_publication_receipt(**kwargs)
    promoted = receipt["promoted_source_identities"]

    assert receipt["publication_mode"] == MODE_PROMOTED_STEP2_DECISION_ONLY
    assert receipt["gate_result"]["state"] == PROMOTED_STEP2_DECISION_ONLY_STATE
    assert promoted == {
        **promoted,
        "active_research_handoff_source_sha256": hashlib.sha256(
            kwargs["promoted_active_pointer_bytes"]
        ).hexdigest(),
        "research_handoff_candidate_effective_sha256": hashlib.sha256(
            kwargs["promoted_effective_handoff_bytes"]
        ).hexdigest(),
        "research_handoff_candidate_effective_validation_sha256": hashlib.sha256(
            kwargs["promoted_effective_validation_bytes"]
        ).hexdigest(),
    }
    assert promoted["verified_effective_handoff_sha256"] == promoted[
        "pointer_effective_handoff_sha256"
    ]
    assert promoted["promotion_expires_at"] == "2026-07-31"

    result = _verify(receipt, kwargs)
    assert result.identity_consistent is True


def test_schema_file_is_valid_closed_draft_2020_12_and_accepts_both_modes() -> None:
    schema = _receipt_schema()
    Draft202012Validator.check_schema(schema)

    assert schema["additionalProperties"] is False
    assert schema["$defs"]["candidate_identities"]["additionalProperties"] is False
    assert schema["$defs"]["input_identities"]["additionalProperties"] is False
    assert schema["$defs"]["gate_result"]["additionalProperties"] is False
    assert schema["$defs"]["promoted_source_identities"]["additionalProperties"] is False
    assert schema["$defs"]["iso_date"] == {
        "type": "string",
        "pattern": r"^\d{4}-\d{2}-\d{2}$",
        "format": "date",
    }
    assert _schema_is_valid(build_step2_publication_receipt(**_legacy_kwargs()))
    assert _schema_is_valid(build_step2_publication_receipt(**_promoted_kwargs()))


@pytest.mark.parametrize(
    ("date_value", "expected_valid"),
    [
        ("2026-02-28", True),
        ("2024-02-29", True),
        ("2026-02-29", False),
        ("2026-02-30", False),
        ("2026-00-10", False),
        ("2026-13-10", False),
        ("2026-01-00", False),
        ("2026-02-28T00:00:00", False),
        ("2026-02-28+00:00", False),
        ("2026-01-01Z", False),
        (" 2026-02-28", False),
        ("2026-02-28 ", False),
        ("2026/02/28", False),
        ("2026-2-28", False),
    ],
)
@pytest.mark.parametrize(
    "publication_mode",
    [MODE_STRICT_FRESH_ACTIONABLE, MODE_PROMOTED_STEP2_DECISION_ONLY],
)
def test_evaluated_date_schema_python_parity_for_both_modes(
    date_value: str,
    expected_valid: bool,
    publication_mode: str,
) -> None:
    kwargs = (
        _legacy_kwargs()
        if publication_mode == MODE_STRICT_FRESH_ACTIONABLE
        else _promoted_kwargs()
    )
    kwargs["evaluated_date"] = date_value
    if expected_valid:
        receipt = build_step2_publication_receipt(**kwargs)
    else:
        receipt = build_step2_publication_receipt(
            **{
                **kwargs,
                "evaluated_date": EVALUATED_DATE,
            }
        )
        receipt["evaluated_date"] = date_value

    schema_valid = _schema_is_valid(receipt)
    python_valid = _verify(receipt, kwargs).identity_consistent

    assert schema_valid == python_valid
    assert schema_valid is expected_valid


@pytest.mark.parametrize(
    ("date_value", "expected_valid"),
    [
        ("2026-02-28", True),
        ("2024-02-29", True),
        ("2026-02-29", False),
        ("2026-02-30", False),
        ("2026-00-10", False),
        ("2026-13-10", False),
        ("2026-01-00", False),
        ("2026-02-28T00:00:00", False),
        ("2026-02-28+00:00", False),
        ("2026-01-01Z", False),
        (" 2026-02-28", False),
        ("2026-02-28 ", False),
        ("2026/02/28", False),
        ("2026-2-28", False),
    ],
)
def test_promotion_expiration_schema_python_parity(
    date_value: str,
    expected_valid: bool,
) -> None:
    if expected_valid:
        kwargs = _promoted_kwargs_with_expiration(date_value)
        receipt = build_step2_publication_receipt(**kwargs)
    else:
        kwargs = _promoted_kwargs_with_expiration("2026-02-28")
        receipt = build_step2_publication_receipt(**kwargs)
        receipt["promoted_source_identities"]["promotion_expires_at"] = date_value

    schema_valid = _schema_is_valid(receipt)
    python_valid = _verify(receipt, kwargs).identity_consistent

    assert schema_valid == python_valid
    assert schema_valid is expected_valid


@pytest.mark.parametrize("date_value", ["2026-02-29", "2026-02-30"])
@pytest.mark.parametrize("promoted", [False, True])
def test_builder_rejects_invalid_evaluated_calendar_dates(
    date_value: str,
    promoted: bool,
) -> None:
    kwargs = _promoted_kwargs() if promoted else _legacy_kwargs()
    kwargs["evaluated_date"] = date_value
    with pytest.raises(Step2PublicationIdentityError) as exc_info:
        build_step2_publication_receipt(**kwargs)
    assert (
        exc_info.value.diagnostic_code
        is Step2PublicationIdentityDiagnostic.RECEIPT_SCHEMA_INVALID
    )


@pytest.mark.parametrize(
    ("date_value", "expected_diagnostic"),
    [
        (
            "2026-02-29",
            Step2PublicationIdentityDiagnostic.RECEIPT_PROMOTED_SOURCE_MISMATCH,
        ),
        (
            "2026-02-30",
            Step2PublicationIdentityDiagnostic.RECEIPT_PROMOTED_SOURCE_MISMATCH,
        ),
        (
            " 2026-02-28",
            Step2PublicationIdentityDiagnostic.RECEIPT_SCHEMA_INVALID,
        ),
        (
            "2026-02-28T00:00:00",
            Step2PublicationIdentityDiagnostic.RECEIPT_PROMOTED_SOURCE_MISMATCH,
        ),
    ],
)
def test_builder_rejects_invalid_or_noncanonical_promoted_expiration(
    date_value: str,
    expected_diagnostic: Step2PublicationIdentityDiagnostic,
) -> None:
    kwargs = _promoted_kwargs_with_expiration(date_value)
    with pytest.raises(Step2PublicationIdentityError) as exc_info:
        build_step2_publication_receipt(**kwargs)
    assert exc_info.value.diagnostic_code is expected_diagnostic
    assert str(exc_info.value) == expected_diagnostic.value


@pytest.mark.parametrize(
    "field",
    [
        "schema_version",
        "generation_id",
        "publication_mode",
        "evaluated_date",
        "candidate_identities",
        "input_identities",
        "gate_result",
        "promoted_source_identities",
        "identity_only",
        "not_authorization",
        "permission_effect",
    ],
)
def test_missing_every_required_top_level_field_fails_closed(field: str) -> None:
    kwargs = _legacy_kwargs()
    receipt = build_step2_publication_receipt(**kwargs)
    del receipt[field]

    result = _verify(receipt, kwargs)
    assert result.identity_consistent is False
    assert result.diagnostic_code in {
        Step2PublicationIdentityDiagnostic.RECEIPT_SCHEMA_INVALID,
        Step2PublicationIdentityDiagnostic.RECEIPT_IDENTITY_MISSING,
    }


@pytest.mark.parametrize(
    ("object_name", "field"),
    [
        ("candidate_identities", "template2_output_sha256"),
        ("candidate_identities", "decision_packet_sha256"),
        ("input_identities", "prompt_sha256"),
        ("input_identities", "raw_output_sha256"),
        ("input_identities", "normalization_settings_sha256"),
        ("input_identities", "permission_artifact_sha256"),
        ("gate_result", "policy"),
        ("gate_result", "outcome"),
        ("gate_result", "reason_code"),
        ("gate_result", "allowed"),
        ("gate_result", "mode"),
        ("gate_result", "state"),
        ("gate_result", "manual_review_required"),
        ("gate_result", "gate_result_sha256"),
    ],
)
def test_missing_every_required_nested_field_fails_closed(
    object_name: str, field: str
) -> None:
    kwargs = _legacy_kwargs()
    receipt = build_step2_publication_receipt(**kwargs)
    del receipt[object_name][field]

    _assert_diagnostic(
        _verify(receipt, kwargs),
        Step2PublicationIdentityDiagnostic.RECEIPT_IDENTITY_MISSING,
    )


@pytest.mark.parametrize(
    "field",
    [
        "active_research_handoff_source_sha256",
        "research_handoff_candidate_effective_sha256",
        "research_handoff_candidate_effective_validation_sha256",
        "verified_effective_handoff_sha256",
        "pointer_effective_handoff_sha256",
        "promoted_verification_sha256",
        "promotion_expires_at",
    ],
)
def test_missing_every_promoted_identity_fails_closed(field: str) -> None:
    kwargs = _promoted_kwargs()
    receipt = build_step2_publication_receipt(**kwargs)
    del receipt["promoted_source_identities"][field]

    _assert_diagnostic(
        _verify(receipt, kwargs),
        Step2PublicationIdentityDiagnostic.RECEIPT_IDENTITY_MISSING,
    )


@pytest.mark.parametrize(
    "mutator",
    [
        lambda receipt: receipt.__setitem__("unknown", True),
        lambda receipt: receipt["candidate_identities"].__setitem__("unknown", "0" * 64),
        lambda receipt: receipt["input_identities"].__setitem__("unknown", "0" * 64),
        lambda receipt: receipt["gate_result"].__setitem__("unknown", True),
    ],
)
def test_unknown_fields_fail_closed(mutator: Callable[[dict[str, Any]], None]) -> None:
    kwargs = _legacy_kwargs()
    receipt = build_step2_publication_receipt(**kwargs)
    mutator(receipt)
    _assert_diagnostic(
        _verify(receipt, kwargs),
        Step2PublicationIdentityDiagnostic.RECEIPT_SCHEMA_INVALID,
    )


def test_unknown_promoted_nested_field_fails_closed() -> None:
    kwargs = _promoted_kwargs()
    receipt = build_step2_publication_receipt(**kwargs)
    receipt["promoted_source_identities"]["unknown"] = "0" * 64
    _assert_diagnostic(
        _verify(receipt, kwargs),
        Step2PublicationIdentityDiagnostic.RECEIPT_SCHEMA_INVALID,
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema_version", 1),
        ("generation_id", True),
        ("publication_mode", True),
        ("evaluated_date", 20260628),
        ("candidate_identities", []),
        ("input_identities", "invalid"),
        ("gate_result", []),
        ("identity_only", 1),
        ("not_authorization", "true"),
        ("permission_effect", False),
    ],
)
def test_wrong_types_fail_closed(field: str, value: Any) -> None:
    kwargs = _legacy_kwargs()
    receipt = build_step2_publication_receipt(**kwargs)
    receipt[field] = value

    assert _verify(receipt, kwargs).identity_consistent is False


def test_wrong_schema_version_unknown_mode_and_malformed_date_fail_closed() -> None:
    kwargs = _legacy_kwargs()
    receipt = build_step2_publication_receipt(**kwargs)

    wrong_version = deepcopy(receipt)
    wrong_version["schema_version"] = "step2_publication_receipt_v999"
    _assert_diagnostic(
        _verify(wrong_version, kwargs),
        Step2PublicationIdentityDiagnostic.RECEIPT_SCHEMA_INVALID,
    )

    unknown_mode = deepcopy(receipt)
    unknown_mode["publication_mode"] = "free_form_success"
    _assert_diagnostic(
        _verify(unknown_mode, kwargs),
        Step2PublicationIdentityDiagnostic.RECEIPT_MODE_INVALID,
    )

    malformed_date = deepcopy(receipt)
    malformed_date["evaluated_date"] = "2026-6-28"
    _assert_diagnostic(
        _verify(malformed_date, kwargs),
        Step2PublicationIdentityDiagnostic.RECEIPT_SCHEMA_INVALID,
    )


@pytest.mark.parametrize(
    ("object_name", "field"),
    [
        (None, "generation_id"),
        ("candidate_identities", "template2_output_sha256"),
        ("candidate_identities", "decision_packet_sha256"),
        ("input_identities", "prompt_sha256"),
        ("input_identities", "raw_output_sha256"),
        ("input_identities", "normalization_settings_sha256"),
        ("input_identities", "permission_artifact_sha256"),
        ("gate_result", "gate_result_sha256"),
    ],
)
def test_every_legacy_hash_must_be_lowercase_sha256(
    object_name: str | None, field: str
) -> None:
    kwargs = _legacy_kwargs()
    receipt = build_step2_publication_receipt(**kwargs)
    target = receipt if object_name is None else receipt[object_name]
    target[field] = "A" * 64

    _assert_diagnostic(
        _verify(receipt, kwargs),
        Step2PublicationIdentityDiagnostic.RECEIPT_IDENTITY_MALFORMED,
    )


def test_missing_and_non_mapping_receipts_have_bounded_results() -> None:
    kwargs = _legacy_kwargs()
    _assert_diagnostic(
        _verify(None, kwargs),
        Step2PublicationIdentityDiagnostic.RECEIPT_MISSING,
    )
    _assert_diagnostic(
        _verify("not-a-mapping", kwargs),
        Step2PublicationIdentityDiagnostic.RECEIPT_SCHEMA_INVALID,
    )


@pytest.mark.parametrize(
    ("input_name", "changed", "expected"),
    [
        (
            "template2_output_bytes",
            TEMPLATE_BYTES + b"changed",
            Step2PublicationIdentityDiagnostic.RECEIPT_TEMPLATE_MISMATCH,
        ),
        (
            "decision_packet_bytes",
            PACKET_BYTES + b"changed",
            Step2PublicationIdentityDiagnostic.RECEIPT_PACKET_MISMATCH,
        ),
        (
            "prompt_bytes",
            PROMPT_BYTES + b"changed",
            Step2PublicationIdentityDiagnostic.RECEIPT_PROMPT_MISMATCH,
        ),
        (
            "raw_output_bytes",
            RAW_BYTES + b"changed",
            Step2PublicationIdentityDiagnostic.RECEIPT_RAW_OUTPUT_MISMATCH,
        ),
        (
            "normalization_settings_bytes",
            SETTINGS_BYTES + b"changed",
            Step2PublicationIdentityDiagnostic.RECEIPT_SETTINGS_MISMATCH,
        ),
    ],
)
def test_each_exact_byte_identity_mismatch_has_bounded_code(
    input_name: str,
    changed: bytes,
    expected: Step2PublicationIdentityDiagnostic,
) -> None:
    kwargs = _legacy_kwargs()
    receipt = build_step2_publication_receipt(**kwargs)
    _assert_diagnostic(_verify(receipt, kwargs, **{input_name: changed}), expected)


def test_permission_byte_mismatch_is_distinct_even_when_semantics_are_identical() -> None:
    kwargs = _legacy_kwargs()
    receipt = build_step2_publication_receipt(**kwargs)
    same_permission_different_bytes = json.dumps(_legacy_permission(), indent=2).encode()

    _assert_diagnostic(
        _verify(
            receipt,
            kwargs,
            permission_artifact_bytes=same_permission_different_bytes,
        ),
        Step2PublicationIdentityDiagnostic.RECEIPT_PERMISSION_MISMATCH,
    )


def test_gate_result_change_unknown_reason_and_contradiction_fail_closed() -> None:
    kwargs = _legacy_kwargs()
    receipt = build_step2_publication_receipt(**kwargs)

    changed_hash = deepcopy(receipt)
    changed_hash["gate_result"]["gate_result_sha256"] = "0" * 64
    _assert_diagnostic(
        _verify(changed_hash, kwargs),
        Step2PublicationIdentityDiagnostic.RECEIPT_GATE_MISMATCH,
    )

    unknown_reason = deepcopy(receipt)
    unknown_reason["gate_result"]["reason_code"] = "caller_claimed_success"
    _assert_diagnostic(
        _verify(unknown_reason, kwargs),
        Step2PublicationIdentityDiagnostic.RECEIPT_GATE_MISMATCH,
    )

    contradictory = deepcopy(receipt)
    contradictory["gate_result"]["allowed"] = False
    _assert_diagnostic(
        _verify(contradictory, kwargs),
        Step2PublicationIdentityDiagnostic.RECEIPT_GATE_MISMATCH,
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("identity_only", False),
        ("not_authorization", False),
        ("permission_effect", "some_other_string"),
    ],
)
def test_same_type_non_authorizing_contradictions_fail_schema_and_python(
    field: str,
    value: Any,
) -> None:
    kwargs = _legacy_kwargs()
    receipt = build_step2_publication_receipt(**kwargs)
    receipt[field] = value

    assert _schema_is_valid(receipt) is False
    _assert_diagnostic(
        _verify(receipt, kwargs),
        Step2PublicationIdentityDiagnostic.RECEIPT_SCHEMA_INVALID,
    )


def test_generation_id_and_evaluated_date_mismatches_are_distinct() -> None:
    kwargs = _legacy_kwargs()
    receipt = build_step2_publication_receipt(**kwargs)

    changed_generation = deepcopy(receipt)
    changed_generation["generation_id"] = "0" * 64
    _assert_diagnostic(
        _verify(changed_generation, kwargs),
        Step2PublicationIdentityDiagnostic.RECEIPT_GENERATION_MISMATCH,
    )

    _assert_diagnostic(
        _verify(receipt, kwargs, evaluated_date="2026-06-29"),
        Step2PublicationIdentityDiagnostic.RECEIPT_EVALUATED_DATE_MISMATCH,
    )


@pytest.mark.parametrize("promoted", [False, True])
def test_every_top_level_material_field_changes_generation_id(promoted: bool) -> None:
    receipt = build_step2_publication_receipt(
        **(_promoted_kwargs() if promoted else _legacy_kwargs())
    )
    material = {
        key: deepcopy(value)
        for key, value in receipt.items()
        if key != "generation_id"
    }
    baseline = derive_generation_id(material)

    for field, original in material.items():
        changed = deepcopy(material)
        if type(original) is str:
            changed[field] = original + "_changed"
        elif type(original) is bool:
            changed[field] = not original
        elif type(original) is dict:
            changed[field]["generation_probe"] = True
        elif original is None:
            changed[field] = {}
        else:
            raise AssertionError(f"unhandled material type for {field}")
        assert derive_generation_id(changed) != baseline


@pytest.mark.parametrize(
    "source_name",
    [
        "promoted_active_pointer_bytes",
        "promoted_effective_handoff_bytes",
        "promoted_effective_validation_bytes",
    ],
)
def test_each_promoted_source_identity_mismatch_is_bounded(source_name: str) -> None:
    kwargs = _promoted_kwargs()
    receipt = build_step2_publication_receipt(**kwargs)
    semantically_same_different_bytes = b"\n" + kwargs[source_name]

    _assert_diagnostic(
        _verify(receipt, kwargs, **{source_name: semantically_same_different_bytes}),
        Step2PublicationIdentityDiagnostic.RECEIPT_PROMOTED_SOURCE_MISMATCH,
    )


@pytest.mark.parametrize(
    "missing_source",
    [
        "promoted_active_pointer_bytes",
        "promoted_effective_handoff_bytes",
        "promoted_effective_validation_bytes",
    ],
)
def test_promoted_mode_missing_each_source_identity_fails_closed(
    missing_source: str,
) -> None:
    kwargs = _promoted_kwargs()
    kwargs[missing_source] = None
    with pytest.raises(Step2PublicationIdentityError) as exc_info:
        build_step2_publication_receipt(**kwargs)
    assert (
        exc_info.value.diagnostic_code
        is Step2PublicationIdentityDiagnostic.RECEIPT_IDENTITY_MISSING
    )
    assert str(exc_info.value) == "receipt_identity_missing"


def test_mode_matrix_rejects_legacy_promoted_fields_unknown_mode_and_gate_contradiction() -> None:
    legacy = _legacy_kwargs()
    with pytest.raises(Step2PublicationIdentityError) as legacy_extra:
        build_step2_publication_receipt(**legacy, **_promoted_sources())
    assert (
        legacy_extra.value.diagnostic_code
        is Step2PublicationIdentityDiagnostic.RECEIPT_MODE_INVALID
    )

    unknown = {**legacy, "publication_mode": "caller_supplied_allowed"}
    with pytest.raises(Step2PublicationIdentityError) as unknown_mode:
        build_step2_publication_receipt(**unknown)
    assert (
        unknown_mode.value.diagnostic_code
        is Step2PublicationIdentityDiagnostic.RECEIPT_MODE_INVALID
    )

    contradiction = {**legacy, "publication_mode": MODE_PROMOTED_STEP2_DECISION_ONLY}
    with pytest.raises(Step2PublicationIdentityError) as gate_mismatch:
        build_step2_publication_receipt(**contradiction)
    assert (
        gate_mismatch.value.diagnostic_code
        is Step2PublicationIdentityDiagnostic.RECEIPT_GATE_MISMATCH
    )


def test_builder_rejects_blocked_permission_and_arbitrary_gate_mappings_are_not_inputs() -> None:
    kwargs = _legacy_kwargs()
    blocked = _legacy_permission()
    blocked["state"] = "STRICT_STALE"
    kwargs["permission_artifact_bytes"] = _json_bytes(blocked)

    with pytest.raises(Step2PublicationIdentityError) as exc_info:
        build_step2_publication_receipt(**kwargs)
    assert (
        exc_info.value.diagnostic_code
        is Step2PublicationIdentityDiagnostic.RECEIPT_GATE_MISMATCH
    )
    assert "gate_result" not in inspect.signature(build_step2_publication_receipt).parameters
    assert "valid" not in inspect.signature(build_step2_publication_receipt).parameters


def test_builder_rejects_duplicate_or_non_object_permission_json() -> None:
    for permission_bytes in (b'{"state":"STRICT_FRESH","state":"STRICT_FRESH"}', b"[]"):
        kwargs = {**_legacy_kwargs(), "permission_artifact_bytes": permission_bytes}
        with pytest.raises(Step2PublicationIdentityError) as exc_info:
            build_step2_publication_receipt(**kwargs)
        assert (
            exc_info.value.diagnostic_code
            is Step2PublicationIdentityDiagnostic.RECEIPT_GATE_MISMATCH
        )


@pytest.mark.parametrize(
    ("exception_type", "message"),
    [
        (TypeError, "injected gate type failure"),
        (ValueError, "injected gate value failure"),
        (RecursionError, "injected gate recursion failure"),
        (AssertionError, "injected gate assertion failure"),
        (AttributeError, "injected gate attribute failure"),
    ],
)
def test_builder_propagates_gate_dependency_programming_failures(
    monkeypatch: pytest.MonkeyPatch,
    exception_type: type[Exception],
    message: str,
) -> None:
    def fail_gate(_: Mapping[str, Any]) -> Any:
        raise exception_type(message)

    monkeypatch.setattr(
        publication_identity,
        "evaluate_step2_research_gate",
        fail_gate,
    )
    with pytest.raises(exception_type) as exc_info:
        build_step2_publication_receipt(**_legacy_kwargs())
    assert type(exc_info.value) is exception_type
    assert str(exc_info.value) == message


@pytest.mark.parametrize(
    ("exception_type", "message"),
    [
        (TypeError, "injected verifier gate type failure"),
        (ValueError, "injected verifier gate value failure"),
        (RecursionError, "injected verifier gate recursion failure"),
        (AssertionError, "injected verifier gate assertion failure"),
        (AttributeError, "injected verifier gate attribute failure"),
    ],
)
def test_verifier_propagates_gate_dependency_programming_failures(
    monkeypatch: pytest.MonkeyPatch,
    exception_type: type[Exception],
    message: str,
) -> None:
    kwargs = _legacy_kwargs()
    receipt = build_step2_publication_receipt(**kwargs)

    def fail_gate(_: Mapping[str, Any]) -> Any:
        raise exception_type(message)

    monkeypatch.setattr(
        publication_identity,
        "evaluate_step2_research_gate",
        fail_gate,
    )
    with pytest.raises(exception_type) as exc_info:
        _verify(receipt, kwargs)
    assert type(exc_info.value) is exception_type
    assert str(exc_info.value) == message


def test_mutable_bytearrays_are_copied_and_retained_caller_authority_is_absent() -> None:
    originals = _legacy_kwargs()
    mutable = {
        key: bytearray(value) if isinstance(value, bytes) else value
        for key, value in originals.items()
    }
    receipt = build_step2_publication_receipt(**mutable)
    receipt_snapshot = deepcopy(receipt)

    for value in mutable.values():
        if isinstance(value, bytearray):
            value.extend(b"caller mutation")

    assert receipt == receipt_snapshot
    assert _verify(receipt, originals).identity_consistent is True
    assert _verify(receipt, mutable).identity_consistent is False


def test_canonical_json_bytes_snapshots_mutable_mapping() -> None:
    source = {"b": [2], "a": {"value": 1}}
    captured = canonical_json_bytes(source)
    source["a"]["value"] = 99
    source["b"].append(3)
    assert captured == b'{"a":{"value":1},"b":[2]}'


@pytest.mark.parametrize(
    "value",
    [
        pytest.param({}, id="empty-object"),
        pytest.param({"array": []}, id="empty-array"),
        pytest.param(
            {"nested": [{"z": 2, "a": [None, True, False, -7]}]},
            id="nested-containers",
        ),
        pytest.param(
            {"unicode": "caf\N{LATIN SMALL LETTER E WITH ACUTE} \N{SNOWMAN}"},
            id="unicode",
        ),
        pytest.param(
            {"escaped": "quote\" backslash\\ slash/"},
            id="quotes-and-backslashes",
        ),
        pytest.param(
            {"control": "\x00\b\f\n\r\t\x1f"},
            id="control-characters",
        ),
        pytest.param(
            {"integers": [-(2**4096), -1, 0, 1, 2**4096]},
            id="negative-and-large-integers",
        ),
        pytest.param(
            {"types": [True, 1, False, 0, None, ""]},
            id="boolean-integer-null-distinction",
        ),
    ],
)
def test_iterative_canonical_serializer_is_byte_identical_to_json_oracle(
    value: dict[str, Any],
) -> None:
    assert canonical_json_bytes(value) == _canonical_json_oracle(value)


def test_iterative_canonical_serializer_matches_fixed_generated_corpus() -> None:
    for index, value in enumerate(_generated_supported_json_corpus()):
        assert canonical_json_bytes(value) == _canonical_json_oracle(value), index


def test_iterative_canonical_serializer_is_order_independent() -> None:
    first = {"z": [{"b": 2, "a": 1}], "a": "first"}
    second = {"a": "first", "z": [{"a": 1, "b": 2}]}

    assert canonical_json_bytes(first) == _canonical_json_oracle(first)
    assert canonical_json_bytes(second) == _canonical_json_oracle(second)
    assert canonical_json_bytes(first) == canonical_json_bytes(second)


def test_canonical_json_permanently_rejects_tuple_without_list_coercion() -> None:
    with pytest.raises(Step2PublicationIdentityError) as exc_info:
        canonical_json_bytes({"items": (1, 2)})
    assert (
        exc_info.value.diagnostic_code
        is Step2PublicationIdentityDiagnostic.RECEIPT_SCHEMA_INVALID
    )
    assert str(exc_info.value) == "receipt_schema_invalid"


@pytest.mark.parametrize(
    "candidate",
    [
        {"outer": {"items": (1, 2)}},
        {"gate_projection": {"allowed_actions": ("HOLD", "NO_TRADE")}},
        {"promoted_verification": {"checks": ({"passed": True},)}},
    ],
)
def test_nested_gate_and_promoted_projection_tuples_are_rejected(
    candidate: dict[str, Any],
) -> None:
    with pytest.raises(Step2PublicationIdentityError) as exc_info:
        canonical_json_bytes(candidate)
    assert (
        exc_info.value.diagnostic_code
        is Step2PublicationIdentityDiagnostic.RECEIPT_SCHEMA_INVALID
    )


class _CustomJsonObject:
    pass


class _CustomDict(dict[str, Any]):
    pass


class _CustomList(list[Any]):
    pass


class _HostileMapping(Mapping[str, Any]):
    def __init__(self) -> None:
        self.methods_called: list[str] = []

    def __getitem__(self, key: str) -> Any:
        self.methods_called.append("getitem")
        raise AssertionError("custom mapping access must not run")

    def __iter__(self):
        self.methods_called.append("iter")
        raise AssertionError("custom mapping iteration must not run")

    def __len__(self) -> int:
        self.methods_called.append("len")
        raise AssertionError("custom mapping length must not run")


@pytest.mark.parametrize(
    "candidate_factory",
    [
        lambda: {"value": {1, 2}},
        lambda: {"value": frozenset({1, 2})},
        lambda: {"value": b"bytes"},
        lambda: {"value": bytearray(b"bytes")},
        lambda: {"value": _CustomJsonObject()},
        lambda: {"value": (item for item in [1, 2])},
        lambda: {1: "non-string-key"},
        lambda: {"value": 1.5},
        lambda: {"value": float("nan")},
        lambda: {"value": float("inf")},
        lambda: {"value": float("-inf")},
        lambda: {"value": _CustomDict({"nested": True})},
        lambda: {"value": _CustomList([1, 2])},
    ],
)
def test_canonical_json_rejects_every_unsupported_type_without_coercion(
    candidate_factory: Callable[[], dict[Any, Any]],
) -> None:
    with pytest.raises(Step2PublicationIdentityError) as exc_info:
        canonical_json_bytes(candidate_factory())
    assert (
        exc_info.value.diagnostic_code
        is Step2PublicationIdentityDiagnostic.RECEIPT_SCHEMA_INVALID
    )
    assert str(exc_info.value) == "receipt_schema_invalid"


def test_custom_mapping_is_rejected_without_invoking_hostile_methods() -> None:
    custom_mapping = _HostileMapping()
    with pytest.raises(Step2PublicationIdentityError) as exc_info:
        canonical_json_bytes({"value": custom_mapping})
    assert (
        exc_info.value.diagnostic_code
        is Step2PublicationIdentityDiagnostic.RECEIPT_SCHEMA_INVALID
    )
    assert custom_mapping.methods_called == []


def test_exact_builtin_dict_and_list_are_accepted() -> None:
    value = {"object": {"accepted": True}, "array": [1, None, "value"]}
    assert type(value) is dict
    assert type(value["array"]) is list
    assert canonical_json_bytes(value) == _canonical_json_oracle(value)


def test_canonical_json_accepts_only_supported_native_scalar_and_list_values() -> None:
    assert canonical_json_bytes(
        {
            "null": None,
            "bool": True,
            "integer": 7,
            "string": "value",
            "list": [False, 0, "", None],
        }
    ) == (
        b'{"bool":true,"integer":7,"list":[false,0,"",null],'
        b'"null":null,"string":"value"}'
    )


def test_structural_depth_boundary_is_accepted_and_one_over_is_rejected() -> None:
    at_limit = _mapping_chain({}, MAX_JSON_NESTING_DEPTH)
    over_limit = _mapping_chain({}, MAX_JSON_NESTING_DEPTH + 1)

    assert canonical_json_bytes(at_limit).startswith(b'{"padding":')
    with pytest.raises(Step2PublicationIdentityError) as exc_info:
        canonical_json_bytes(over_limit)
    assert (
        exc_info.value.diagnostic_code
        is Step2PublicationIdentityDiagnostic.RECEIPT_SCHEMA_INVALID
    )


def test_nested_list_depth_boundary_is_accepted_and_one_over_is_rejected() -> None:
    at_limit = {"items": _list_chain(MAX_JSON_NESTING_DEPTH - 1)}
    over_limit = {"items": _list_chain(MAX_JSON_NESTING_DEPTH)}

    assert canonical_json_bytes(at_limit) == _canonical_json_oracle(at_limit)
    with pytest.raises(Step2PublicationIdentityError) as exc_info:
        canonical_json_bytes(over_limit)
    assert (
        exc_info.value.diagnostic_code
        is Step2PublicationIdentityDiagnostic.RECEIPT_SCHEMA_INVALID
    )


def test_valid_depth_boundary_is_independent_of_low_python_recursion_limit() -> None:
    value = _mapping_chain({}, MAX_JSON_NESTING_DEPTH)
    expected = _canonical_json_oracle(value)
    kwargs = _legacy_kwargs()
    original_recursion_limit = sys.getrecursionlimit()
    outcomes: list[Any] = []

    def exercise_under_low_limit() -> None:
        try:
            # The former whole-container json.dumps path failed at this limit.
            sys.setrecursionlimit(36)
            encoded = canonical_json_bytes(value)
            receipt = build_step2_publication_receipt(**kwargs)
            verified = _verify(receipt, kwargs)
            outcomes.append((encoded, verified.identity_consistent))
        except Exception as exc:  # pragma: no cover - asserted below
            outcomes.append(exc)
        finally:
            sys.setrecursionlimit(original_recursion_limit)

    worker = threading.Thread(target=exercise_under_low_limit)
    worker.start()
    worker.join()

    assert sys.getrecursionlimit() == original_recursion_limit
    assert outcomes == [(expected, True)]


def test_structural_node_count_boundary_is_accepted_and_one_over_is_rejected() -> None:
    # root object + child list + scalar elements == total node count
    at_limit = {"items": [None] * (MAX_JSON_NODE_COUNT - 2)}
    over_limit = {"items": [None] * (MAX_JSON_NODE_COUNT - 1)}

    assert canonical_json_bytes(at_limit)
    with pytest.raises(Step2PublicationIdentityError) as exc_info:
        canonical_json_bytes(over_limit)
    assert (
        exc_info.value.diagnostic_code
        is Step2PublicationIdentityDiagnostic.RECEIPT_SCHEMA_INVALID
    )


def test_canonical_json_rejects_direct_and_nested_cycles() -> None:
    direct: dict[str, Any] = {}
    direct["cycle"] = direct
    outer: dict[str, Any] = {}
    inner = {"back": outer}
    outer["inner"] = inner

    for candidate in (direct, outer):
        with pytest.raises(Step2PublicationIdentityError) as exc_info:
            canonical_json_bytes(candidate)
        assert (
            exc_info.value.diagnostic_code
            is Step2PublicationIdentityDiagnostic.RECEIPT_SCHEMA_INVALID
        )


def test_canonical_json_rejects_self_referential_list() -> None:
    cyclic_list: list[Any] = []
    cyclic_list.append(cyclic_list)

    with pytest.raises(Step2PublicationIdentityError) as exc_info:
        canonical_json_bytes({"cycle": cyclic_list})
    assert (
        exc_info.value.diagnostic_code
        is Step2PublicationIdentityDiagnostic.RECEIPT_SCHEMA_INVALID
    )


def test_canonical_json_rejects_dictionary_list_mutual_cycle() -> None:
    cyclic_dict: dict[str, Any] = {}
    cyclic_list = [cyclic_dict]
    cyclic_dict["back"] = cyclic_list

    with pytest.raises(Step2PublicationIdentityError) as exc_info:
        canonical_json_bytes({"cycle": cyclic_dict})
    assert (
        exc_info.value.diagnostic_code
        is Step2PublicationIdentityDiagnostic.RECEIPT_SCHEMA_INVALID
    )


def test_canonical_json_rejects_nested_mixed_container_cycle() -> None:
    root: dict[str, Any] = {"level_one": [{"level_two": []}]}
    root["level_one"][0]["level_two"].append(root)

    with pytest.raises(Step2PublicationIdentityError) as exc_info:
        canonical_json_bytes(root)
    assert (
        exc_info.value.diagnostic_code
        is Step2PublicationIdentityDiagnostic.RECEIPT_SCHEMA_INVALID
    )


def test_canonical_json_accepts_shared_acyclic_dict_and_list_aliases() -> None:
    shared_dict = {"nested": [1, 2]}
    shared_list = [{"accepted": True}]
    value = {
        "dict_first": shared_dict,
        "dict_second": shared_dict,
        "list_first": shared_list,
        "list_second": shared_list,
    }

    assert canonical_json_bytes(value) == _canonical_json_oracle(value)


def test_verifier_rejects_direct_and_nested_receipt_cycles_with_bounded_result() -> None:
    kwargs = _legacy_kwargs()
    direct = build_step2_publication_receipt(**kwargs)
    direct["cycle"] = direct

    nested = build_step2_publication_receipt(**kwargs)
    outer: dict[str, Any] = {}
    inner = {"back": outer}
    outer["inner"] = inner
    nested["cycle"] = outer

    for receipt in (direct, nested):
        _assert_diagnostic(
            _verify(receipt, kwargs),
            Step2PublicationIdentityDiagnostic.RECEIPT_SCHEMA_INVALID,
        )
        assert _schema_is_valid(receipt) is False


def test_deep_and_excessive_node_receipts_fail_without_recursion_or_truncation() -> None:
    kwargs = _legacy_kwargs()
    at_depth_limit = _mapping_chain(
        build_step2_publication_receipt(**kwargs),
        MAX_JSON_NESTING_DEPTH,
    )
    over_depth_limit = _mapping_chain(
        build_step2_publication_receipt(**kwargs),
        MAX_JSON_NESTING_DEPTH + 1,
    )
    excessive_nodes = build_step2_publication_receipt(**kwargs)
    excessive_nodes["padding"] = [None] * MAX_JSON_NODE_COUNT

    for receipt in (at_depth_limit, over_depth_limit, excessive_nodes):
        _assert_diagnostic(
            _verify(receipt, kwargs),
            Step2PublicationIdentityDiagnostic.RECEIPT_SCHEMA_INVALID,
        )
        assert _schema_is_valid(receipt) is False


def test_permission_json_at_maximum_depth_remains_accepted() -> None:
    permission = _mapping_chain(_legacy_permission(), MAX_JSON_NESTING_DEPTH)
    kwargs = {
        **_legacy_kwargs(),
        "permission_artifact_bytes": _json_bytes(permission),
    }
    receipt = build_step2_publication_receipt(**kwargs)
    assert _verify(receipt, kwargs).identity_consistent is True


def test_deep_permission_json_builder_and_verifier_fail_closed() -> None:
    permission = _mapping_chain(_legacy_permission(), MAX_JSON_NESTING_DEPTH + 1)
    payload = _json_bytes(permission)
    assert isinstance(json.loads(payload), dict)

    kwargs = {**_legacy_kwargs(), "permission_artifact_bytes": payload}
    with pytest.raises(Step2PublicationIdentityError) as exc_info:
        build_step2_publication_receipt(**kwargs)
    assert (
        exc_info.value.diagnostic_code
        is Step2PublicationIdentityDiagnostic.RECEIPT_GATE_MISMATCH
    )
    assert str(exc_info.value) == "receipt_gate_mismatch"

    valid_kwargs = _legacy_kwargs()
    receipt = build_step2_publication_receipt(**valid_kwargs)
    _assert_diagnostic(
        _verify(receipt, valid_kwargs, permission_artifact_bytes=payload),
        Step2PublicationIdentityDiagnostic.RECEIPT_GATE_MISMATCH,
    )


def test_permission_json_parser_recursion_is_bounded_for_builder_and_verifier() -> None:
    payload = _parser_recursion_json_bytes()
    with pytest.raises(RecursionError):
        json.loads(payload)

    kwargs = {**_legacy_kwargs(), "permission_artifact_bytes": payload}
    with pytest.raises(Step2PublicationIdentityError) as exc_info:
        build_step2_publication_receipt(**kwargs)
    assert (
        exc_info.value.diagnostic_code
        is Step2PublicationIdentityDiagnostic.RECEIPT_GATE_MISMATCH
    )
    assert str(exc_info.value) == "receipt_gate_mismatch"

    valid_kwargs = _legacy_kwargs()
    receipt = build_step2_publication_receipt(**valid_kwargs)
    _assert_diagnostic(
        _verify(receipt, valid_kwargs, permission_artifact_bytes=payload),
        Step2PublicationIdentityDiagnostic.RECEIPT_GATE_MISMATCH,
    )


@pytest.mark.parametrize(
    "case",
    [
        "malformed_utf8",
        "malformed_json",
        "excessive_nodes",
        "parser_recursion",
        "semantic_mismatch",
    ],
)
def test_malformed_permission_source_is_bounded_at_builder_and_verifier_entries(
    case: str,
) -> None:
    payload = _malformed_source_bytes(case)
    kwargs = {**_legacy_kwargs(), "permission_artifact_bytes": payload}

    with pytest.raises(Step2PublicationIdentityError) as exc_info:
        build_step2_publication_receipt(**kwargs)
    assert (
        exc_info.value.diagnostic_code
        is Step2PublicationIdentityDiagnostic.RECEIPT_GATE_MISMATCH
    )
    assert str(exc_info.value) == "receipt_gate_mismatch"

    valid_kwargs = _legacy_kwargs()
    receipt = build_step2_publication_receipt(**valid_kwargs)
    _assert_diagnostic(
        _verify(receipt, valid_kwargs, permission_artifact_bytes=payload),
        Step2PublicationIdentityDiagnostic.RECEIPT_GATE_MISMATCH,
    )


@pytest.mark.parametrize(
    "source_name",
    [
        "promoted_active_pointer_bytes",
        "promoted_effective_handoff_bytes",
        "promoted_effective_validation_bytes",
    ],
)
def test_promoted_json_source_at_maximum_depth_remains_accepted(
    source_name: str,
) -> None:
    kwargs = _promoted_kwargs_with_source_depth(
        source_name,
        MAX_JSON_NESTING_DEPTH,
    )
    receipt = build_step2_publication_receipt(**kwargs)
    assert _verify(receipt, kwargs).identity_consistent is True


@pytest.mark.parametrize(
    "source_name",
    [
        "promoted_active_pointer_bytes",
        "promoted_effective_handoff_bytes",
        "promoted_effective_validation_bytes",
    ],
)
@pytest.mark.parametrize("parser_recursion", [False, True])
def test_deep_promoted_json_sources_fail_closed_for_builder_and_verifier(
    source_name: str,
    parser_recursion: bool,
) -> None:
    if parser_recursion:
        payload = _parser_recursion_json_bytes()
        with pytest.raises(RecursionError):
            json.loads(payload)
    else:
        payload = _json_bytes(_mapping_chain({}, MAX_JSON_NESTING_DEPTH + 1))
        assert isinstance(json.loads(payload), dict)

    kwargs = {**_promoted_kwargs(), source_name: payload}
    with pytest.raises(Step2PublicationIdentityError) as exc_info:
        build_step2_publication_receipt(**kwargs)
    assert (
        exc_info.value.diagnostic_code
        is Step2PublicationIdentityDiagnostic.RECEIPT_PROMOTED_SOURCE_MISMATCH
    )
    assert str(exc_info.value) == "receipt_promoted_source_mismatch"

    valid_kwargs = _promoted_kwargs()
    receipt = build_step2_publication_receipt(**valid_kwargs)
    _assert_diagnostic(
        _verify(receipt, valid_kwargs, **{source_name: payload}),
        Step2PublicationIdentityDiagnostic.RECEIPT_PROMOTED_SOURCE_MISMATCH,
    )


@pytest.mark.parametrize(
    "source_name",
    [
        "promoted_active_pointer_bytes",
        "promoted_effective_handoff_bytes",
        "promoted_effective_validation_bytes",
    ],
)
@pytest.mark.parametrize(
    "case",
    [
        "malformed_utf8",
        "malformed_json",
        "excessive_nodes",
        "parser_recursion",
        "semantic_mismatch",
    ],
)
def test_each_malformed_promoted_source_is_bounded_at_builder_and_verifier_entries(
    source_name: str,
    case: str,
) -> None:
    payload = _malformed_source_bytes(case)
    kwargs = {**_promoted_kwargs(), source_name: payload}

    with pytest.raises(Step2PublicationIdentityError) as exc_info:
        build_step2_publication_receipt(**kwargs)
    assert (
        exc_info.value.diagnostic_code
        is Step2PublicationIdentityDiagnostic.RECEIPT_PROMOTED_SOURCE_MISMATCH
    )
    assert str(exc_info.value) == "receipt_promoted_source_mismatch"

    valid_kwargs = _promoted_kwargs()
    receipt = build_step2_publication_receipt(**valid_kwargs)
    _assert_diagnostic(
        _verify(receipt, valid_kwargs, **{source_name: payload}),
        Step2PublicationIdentityDiagnostic.RECEIPT_PROMOTED_SOURCE_MISMATCH,
    )


def test_pure_builder_and_verifier_write_no_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    kwargs = _legacy_kwargs()
    receipt = build_step2_publication_receipt(**kwargs)
    assert _verify(receipt, kwargs).identity_consistent is True
    assert list(tmp_path.iterdir()) == []


def test_receipt_and_result_are_explicitly_non_authorizing() -> None:
    receipt = build_step2_publication_receipt(**_legacy_kwargs())
    forbidden_fields = {
        "ready",
        "readiness",
        "allowed_actions",
        "blocked_actions",
        "sell_allowed",
        "new_buy_permission",
        "order_compilation_allowed",
        "step3_allowed",
        "step4_allowed",
        "final_execution_allowed",
        "broker_automation_allowed",
        "eligible_rows",
    }

    assert forbidden_fields.isdisjoint(receipt)
    assert forbidden_fields.isdisjoint(receipt["gate_result"])
    assert receipt["identity_only"] is True
    assert receipt["not_authorization"] is True
    assert receipt["permission_effect"] == "none"


def test_valid_and_invalid_verification_results_reject_boolean_coercion() -> None:
    kwargs = _legacy_kwargs()
    receipt = build_step2_publication_receipt(**kwargs)
    valid_result = _verify(receipt, kwargs)
    invalid_result = _verify(None, kwargs)

    assert valid_result.identity_consistent is True
    assert invalid_result.identity_consistent is False
    for result in (valid_result, invalid_result):
        with pytest.raises(TypeError, match=VERIFICATION_BOOLEAN_COERCION_ERROR):
            bool(result)
        assert result.identity_only is True
        assert result.not_authorization is True
        assert result.permission_effect == "none"


def test_module_has_no_filesystem_clock_writer_pointer_or_workflow_surface() -> None:
    import investment_orchestrator.state.step2_publication_identity as module

    source = inspect.getsource(module)
    tree = ast.parse(source)
    imported_roots: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            imported_roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported_roots.add(node.module.split(".")[0])
    called_names = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    called_attributes = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }

    assert imported_roots.isdisjoint({"os", "pathlib", "shutil", "time"})
    assert called_names.isdisjoint({"open", "write_text", "write_json"})
    assert called_attributes.isdisjoint(
        {"open", "write_text", "write_bytes", "replace", "rename", "symlink_to", "today", "now"}
    )
    assert "artifacts/current" not in source
    assert "step2_decision_builder" not in source
    assert "step3_audit_engine" not in source
    assert "step4_order_compiler" not in source


def test_no_production_consumer_imports_publication_identity_contract() -> None:
    module_path = (
        repo_root()
        / "src"
        / "investment_orchestrator"
        / "state"
        / "step2_publication_identity.py"
    )
    consumers: list[str] = []
    for path in (repo_root() / "src" / "investment_orchestrator").rglob("*.py"):
        if path == module_path:
            continue
        if "step2_publication_identity" in path.read_text(encoding="utf-8"):
            consumers.append(str(path.relative_to(repo_root())))
    assert consumers == []


def test_receipt_schema_and_module_define_no_canonical_writer_or_pointer() -> None:
    schema_text = (repo_root() / "schemas" / RECEIPT_SCHEMA_FILENAME).read_text()
    module_text = (
        repo_root()
        / "src"
        / "investment_orchestrator"
        / "state"
        / "step2_publication_identity.py"
    ).read_text()
    combined = schema_text + module_text

    assert "canonical_path" not in combined
    assert "current_path" not in combined
    assert "pointer_path" not in combined
    assert "artifact_path" not in combined
    assert "broker_account" not in combined
