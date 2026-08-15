from __future__ import annotations

import json

import pytest

from investment_orchestrator.mmi.canonical import canonical_json_bytes
from investment_orchestrator.mmi.contracts import MmiSourceRole
from investment_orchestrator.mmi.lh2_manual_capture_receipt_v1 import (
    LH2_MANUAL_CAPTURE_RECEIPT_PATH_COMPONENTS,
    LH2_MANUAL_CAPTURE_RECEIPT_V1_SCHEMA_VERSION,
    Lh2ManualCaptureReceiptV1Error,
    build_lh2_manual_capture_receipt_v1,
    lh2_manual_capture_receipt_v1_canonical_text,
    validate_lh2_manual_capture_receipt_v1,
)


_SHA = "a" * 64


def _receipt(**overrides: object) -> dict[str, object]:
    receipt: dict[str, object] = {
        "schema_version": "lh2_manual_capture_receipt_v1",
        "source_role": "LONG_HORIZON_RESEARCH",
        "observed_sha256": _SHA,
        "observed_size_bytes": 48,
    }
    receipt.update(overrides)
    return receipt


def test_receipt_is_the_exact_closed_four_field_contract() -> None:
    """The built receipt equals the closed contract exactly, field for field."""
    built = build_lh2_manual_capture_receipt_v1(
        source_role=MmiSourceRole.LONG_HORIZON_RESEARCH,
        observed_sha256=_SHA,
        observed_size_bytes=48,
    )

    assert built == {
        "schema_version": "lh2_manual_capture_receipt_v1",
        "source_role": "LONG_HORIZON_RESEARCH",
        "observed_sha256": _SHA,
        "observed_size_bytes": 48,
    }
    assert LH2_MANUAL_CAPTURE_RECEIPT_V1_SCHEMA_VERSION == (
        "lh2_manual_capture_receipt_v1"
    )
    assert LH2_MANUAL_CAPTURE_RECEIPT_PATH_COMPONENTS == (
        "inputs",
        "current",
        "lh2_manual_capture_receipt.json",
    )
    validate_lh2_manual_capture_receipt_v1(built)


def test_receipt_carries_no_identity_temporal_or_authority_field() -> None:
    """Complete key equality proves no fifth field can appear.

    The captured content digest is itself the cross-run continuity binding, so
    no receipt identity hash, expected digest, timestamp, run or operator id,
    freshness, permission, or authority field is carried.
    """
    built = build_lh2_manual_capture_receipt_v1(
        source_role=MmiSourceRole.LONG_HORIZON_RESEARCH,
        observed_sha256=_SHA,
        observed_size_bytes=0,
    )

    assert set(built) == {
        "schema_version",
        "source_role",
        "observed_sha256",
        "observed_size_bytes",
    }
    # A zero-byte source is a legitimate capture, not a contract violation.
    assert built["observed_size_bytes"] == 0


@pytest.mark.parametrize(
    "invalid",
    (
        pytest.param(_receipt(unexpected="field"), id="unknown-field"),
        pytest.param(
            {
                key: value
                for key, value in _receipt().items()
                if key != "observed_sha256"
            },
            id="missing-field",
        ),
        pytest.param(
            _receipt(source_role="STRATEGY_SETTINGS"), id="wrong-role"
        ),
        pytest.param(_receipt(observed_sha256="A" * 64), id="uppercase-sha"),
        pytest.param(_receipt(observed_sha256="a" * 63), id="short-sha"),
        pytest.param(_receipt(observed_size_bytes=-1), id="negative-size"),
        pytest.param(
            _receipt(schema_version="lh2_manual_capture_receipt_v2"),
            id="wrong-schema-version",
        ),
    ),
)
def test_receipt_rejects_representative_contract_violations(
    invalid: dict[str, object],
) -> None:
    """Structural closure is owned by the schema and fails closed."""
    with pytest.raises(Lh2ManualCaptureReceiptV1Error) as caught:
        validate_lh2_manual_capture_receipt_v1(invalid)
    assert caught.value.code == "LH2_MANUAL_CAPTURE_RECEIPT_V1_INVALID"

    with pytest.raises(Lh2ManualCaptureReceiptV1Error):
        lh2_manual_capture_receipt_v1_canonical_text(invalid)


def test_receipt_builder_rejects_any_other_catalog_role() -> None:
    """This contract records the fixed long-horizon research role only."""
    for role in (
        MmiSourceRole.STRATEGY_SETTINGS,
        MmiSourceRole.PORTFOLIO_SNAPSHOT,
    ):
        with pytest.raises(Lh2ManualCaptureReceiptV1Error) as caught:
            build_lh2_manual_capture_receipt_v1(
                source_role=role,
                observed_sha256=_SHA,
                observed_size_bytes=48,
            )
        assert caught.value.code == (
            "LH2_MANUAL_CAPTURE_RECEIPT_V1_SOURCE_ROLE_INVALID"
        )


def test_receipt_serialization_uses_the_shared_canonical_owner() -> None:
    """Serialization is deterministic and produced by the existing owner."""
    built = build_lh2_manual_capture_receipt_v1(
        source_role=MmiSourceRole.LONG_HORIZON_RESEARCH,
        observed_sha256=_SHA,
        observed_size_bytes=48,
    )

    text = lh2_manual_capture_receipt_v1_canonical_text(built)

    assert text == lh2_manual_capture_receipt_v1_canonical_text(built)
    assert text == canonical_json_bytes(built).decode("utf-8")
    # Key order in the input does not change the serialized artifact.
    reordered = dict(reversed(list(built.items())))
    assert lh2_manual_capture_receipt_v1_canonical_text(reordered) == text
    # The artifact round-trips to exactly the same closed contract.
    assert json.loads(text) == built
