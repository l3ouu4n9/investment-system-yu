from __future__ import annotations

import base64
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import inspect
import json
from pathlib import Path
import struct

import pytest

import investment_orchestrator.mmi as mmi
from investment_orchestrator.common.schema_validation import (
    validate_artifact_schema,
)
from investment_orchestrator.mmi.analyst_visible_evidence_view_v2 import (
    build_mmi_analyst_visible_evidence_view_v2,
)
from investment_orchestrator.mmi.contracts import (
    MmiCapturedSource,
    MmiProjectionRunContext,
    MmiSourceRole,
    _begin_mmi_projection_run_with_clock,
)
from investment_orchestrator.mmi.evidence_bundle import (
    build_mmi_authenticated_evidence_bundle,
)
from investment_orchestrator.mmi.grounded_prompt_v2 import (
    build_mmi_grounded_prompt_v2,
)
from investment_orchestrator.mmi.policy_projection import (
    build_mmi_policy_projection,
)
from investment_orchestrator.mmi.raw_response_envelope_v2 import (
    MmiRawResponseEnvelopeV2Error,
    build_mmi_raw_response_envelope_v2,
)
import _mmi_hermetic_source_checkout as hermetic


VIEW_DOMAIN = b"mmi_analyst_visible_evidence_view_v2\0"
CONTEXT_DOMAIN = b"mmi_grounded_prompt_context_binding_v2\0"
PROMPT_DOMAIN = b"mmi_grounded_prompt_artifact_v2\0"
ENVELOPE_DOMAIN = b"mmi_raw_response_envelope_v2\0"
VIEW_IDENTITY_FIELD = "analyst_visible_evidence_view_identity_sha256"
PROMPT_IDENTITY_FIELD = "grounded_prompt_artifact_identity_sha256"
ENVELOPE_IDENTITY_FIELD = "raw_response_envelope_identity_sha256"
EXPECTED_FIELDS = {
    "schema_version",
    "artifact_kind",
    "report_only",
    "authority_effect",
    "manual_handoff_required",
    "grounded_prompt_artifact_identity_sha256",
    "raw_response_byte_length",
    "raw_response_sha256",
    "raw_response_base64",
    ENVELOPE_IDENTITY_FIELD,
}
EVALUATION_TIME = datetime(
    2026,
    6,
    29,
    12,
    tzinfo=timezone.utc,
)
# Test-owned source dates, fixed before ``EVALUATION_TIME`` so that an
# operational ``inputs/current`` refresh cannot reach this module.
SOURCE_AS_OF = "2026-06-28"
SOURCE_RUN_TIMESTAMP_ET = "2026-06-28 10:00 ET"


class _FixedClock:
    def now_utc(self) -> datetime:
        return EVALUATION_TIME


class _Inputs:
    def __init__(
        self,
        *,
        policy: dict[str, object],
        policy_source: MmiCapturedSource,
        evidence: dict[str, object],
        run_context: MmiProjectionRunContext,
        view: dict[str, object],
    ) -> None:
        self.policy = policy
        self.policy_source = policy_source
        self.evidence = evidence
        self.run_context = run_context
        self.view = view


@pytest.fixture(scope="module", autouse=True)
def _no_live_operational_inputs():
    with hermetic.live_operational_input_access_forbidden():
        yield


@pytest.fixture(scope="module")
def checkout(
    tmp_path_factory: pytest.TempPathFactory,
) -> hermetic.HermeticSourceCheckout:
    return hermetic.build_checkout(
        tmp_path_factory,
        "r1c-hermetic-checkout",
        as_of=SOURCE_AS_OF,
        run_timestamp_et=SOURCE_RUN_TIMESTAMP_ET,
        updated=SOURCE_AS_OF,
    )


@pytest.fixture(scope="module")
def inputs(checkout: hermetic.HermeticSourceCheckout) -> _Inputs:
    capture = checkout.policy_source
    run_context = _begin_mmi_projection_run_with_clock(_FixedClock())
    policy_result = build_mmi_policy_projection(
        capture,
        run_context=run_context,
    )
    assert policy_result.valid, policy_result.reason_codes
    assert policy_result.projection is not None
    policy = dict(policy_result.projection)
    evidence_result = build_mmi_authenticated_evidence_bundle(
        policy_projection=deepcopy(policy),
        policy_source=capture,
        portfolio_projection=None,
        portfolio_source=None,
        run_context=run_context,
    )
    assert evidence_result.valid, evidence_result.reason_codes
    assert evidence_result.projection is not None
    evidence = dict(evidence_result.projection)
    view_result = build_mmi_analyst_visible_evidence_view_v2(
        evidence_bundle=deepcopy(evidence),
        policy_projection=deepcopy(policy),
        policy_source=capture,
        portfolio_projection=None,
        portfolio_source=None,
        run_context=run_context,
    )
    assert view_result.valid, view_result.reason_codes
    assert view_result.projection is not None
    return _Inputs(
        policy=policy,
        policy_source=capture,
        evidence=evidence,
        run_context=run_context,
        view=dict(view_result.projection),
    )


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _record_identity(
    domain: bytes,
    value: dict[str, object],
    identity_field: str,
) -> str:
    preimage = deepcopy(value)
    preimage.pop(identity_field, None)
    canonical = _canonical(preimage)
    return hashlib.sha256(
        domain + struct.pack(">Q", len(canonical)) + canonical
    ).hexdigest()


def _framed_identity(domain: bytes, value: object) -> str:
    canonical = _canonical(value)
    return hashlib.sha256(
        domain + struct.pack(">Q", len(canonical)) + canonical
    ).hexdigest()


def _valid_view(inputs: _Inputs) -> dict[str, object]:
    return deepcopy(inputs.view)


def _prompt(
    inputs: _Inputs,
    *,
    view: dict[str, object] | None = None,
) -> dict[str, object]:
    return build_mmi_grounded_prompt_v2(
        analyst_visible_evidence_view=(
            _valid_view(inputs) if view is None else view
        ),
        evidence_bundle=deepcopy(inputs.evidence),
        policy_projection=deepcopy(inputs.policy),
        policy_source=inputs.policy_source,
        portfolio_projection=None,
        portfolio_source=None,
        run_context=inputs.run_context,
    )


def _envelope(
    inputs: _Inputs,
    *,
    raw_response_bytes: object,
    prompt: dict[str, object] | None = None,
) -> dict[str, object]:
    return build_mmi_raw_response_envelope_v2(
        grounded_prompt=_prompt(inputs) if prompt is None else prompt,
        raw_response_bytes=raw_response_bytes,  # type: ignore[arg-type]
        evidence_bundle=deepcopy(inputs.evidence),
        policy_projection=deepcopy(inputs.policy),
        policy_source=inputs.policy_source,
        portfolio_projection=None,
        portfolio_source=None,
        run_context=inputs.run_context,
    )


def _resealed_ticker_mismatch(inputs: _Inputs) -> dict[str, object]:
    view = _valid_view(inputs)
    policy = view["policy_view"]
    assert type(policy) is dict
    instruments = policy["analysis_instruments"]
    assert type(instruments) is list
    qqq = next(
        item
        for item in instruments
        if type(item) is dict and item.get("ticker") == "QQQ"
    )
    assert all(
        type(item) is dict and item.get("ticker") != "IWM"
        for item in instruments
    )
    qqq["ticker"] = "IWM"
    view[VIEW_IDENTITY_FIELD] = _record_identity(
        VIEW_DOMAIN,
        view,
        VIEW_IDENTITY_FIELD,
    )
    validate_artifact_schema(
        view,
        schema_name="mmi_analyst_visible_evidence_view_v2.schema.json",
    )
    assert view[VIEW_IDENTITY_FIELD] == _record_identity(
        VIEW_DOMAIN,
        view,
        VIEW_IDENTITY_FIELD,
    )
    return view


def _correlated_resealed_prompt(
    inputs: _Inputs,
    fabricated_view: dict[str, object],
) -> dict[str, object]:
    valid_view = _valid_view(inputs)
    valid_prompt = _prompt(inputs, view=valid_view)
    fabricated = deepcopy(valid_prompt)
    fabricated[VIEW_IDENTITY_FIELD] = fabricated_view[
        VIEW_IDENTITY_FIELD
    ]
    fabricated["prompt_context_binding_sha256"] = _framed_identity(
        CONTEXT_DOMAIN,
        {
            VIEW_IDENTITY_FIELD: fabricated[VIEW_IDENTITY_FIELD],
            "instruction_set_version": fabricated[
                "instruction_set_version"
            ],
            "expected_response_schema_version": fabricated[
                "expected_response_schema_version"
            ],
            "report_only": fabricated["report_only"],
            "authority_effect": fabricated["authority_effect"],
            "manual_handoff_required": fabricated[
                "manual_handoff_required"
            ],
        },
    )
    prompt_text = valid_prompt["prompt_text"]
    old_context = valid_prompt["prompt_context_binding_sha256"]
    new_context = fabricated["prompt_context_binding_sha256"]
    assert (
        type(prompt_text) is str
        and type(old_context) is str
        and type(new_context) is str
    )
    valid_evidence = _canonical(valid_view).decode("utf-8")
    fabricated_evidence = _canonical(fabricated_view).decode("utf-8")
    assert len(valid_evidence.encode()) == len(fabricated_evidence.encode())
    assert prompt_text.count(old_context) == 1
    assert prompt_text.count(valid_evidence) == 1
    fabricated["prompt_text"] = prompt_text.replace(
        old_context,
        new_context,
        1,
    ).replace(
        valid_evidence,
        fabricated_evidence,
        1,
    )
    fabricated[PROMPT_IDENTITY_FIELD] = _record_identity(
        PROMPT_DOMAIN,
        fabricated,
        PROMPT_IDENTITY_FIELD,
    )
    validate_artifact_schema(
        fabricated,
        schema_name="mmi_grounded_prompt_v2.schema.json",
    )
    assert fabricated[PROMPT_IDENTITY_FIELD] == _record_identity(
        PROMPT_DOMAIN,
        fabricated,
        PROMPT_IDENTITY_FIELD,
    )
    return fabricated


def _independent_envelope_identity(
    artifact: dict[str, object],
) -> str:
    return _record_identity(
        ENVELOPE_DOMAIN,
        artifact,
        ENVELOPE_IDENTITY_FIELD,
    )


def test_r1c_v2_source_bound_api_is_explicit_keyword_only() -> None:
    assert tuple(
        inspect.signature(build_mmi_raw_response_envelope_v2).parameters
    ) == (
        "grounded_prompt",
        "raw_response_bytes",
        "evidence_bundle",
        "policy_projection",
        "policy_source",
        "portfolio_projection",
        "portfolio_source",
        "run_context",
    )
    assert all(
        parameter.kind is inspect.Parameter.KEYWORD_ONLY
        for parameter in inspect.signature(
            build_mmi_raw_response_envelope_v2
        ).parameters.values()
    )


def test_r1c_v2_builds_exact_closed_report_only_artifact(
    inputs: _Inputs,
) -> None:
    artifact = _envelope(
        inputs,
        raw_response_bytes=b"{}",
    )
    assert set(artifact) == EXPECTED_FIELDS
    assert artifact["schema_version"] == "mmi_raw_response_envelope_v2"
    assert artifact["artifact_kind"] == "MMI_RAW_RESPONSE_ENVELOPE"
    assert artifact["report_only"] is True
    assert artifact["authority_effect"] == "NONE"
    assert artifact["manual_handoff_required"] is True
    validate_artifact_schema(
        artifact,
        schema_name="mmi_raw_response_envelope_v2.schema.json",
    )


@pytest.mark.parametrize(
    "raw_bytes",
    [
        b"line one\r\nline two\r\n",
        b"line one\nline two\n",
        b" leading",
        b"trailing ",
        "multibyte: π".encode(),
        b"\x00\xff\x80not-json",
    ],
)
def test_r1c_v2_preserves_representative_exact_bytes(
    inputs: _Inputs,
    raw_bytes: bytes,
) -> None:
    artifact = _envelope(
        inputs,
        raw_response_bytes=raw_bytes,
    )
    assert artifact["raw_response_byte_length"] == len(raw_bytes)
    assert artifact["raw_response_sha256"] == hashlib.sha256(
        raw_bytes
    ).hexdigest()
    assert artifact["raw_response_base64"] == base64.b64encode(
        raw_bytes
    ).decode("ascii")
    assert artifact[ENVELOPE_IDENTITY_FIELD] == (
        _independent_envelope_identity(artifact)
    )


def test_r1c_v2_keeps_lexically_similar_bytes_distinct(
    inputs: _Inputs,
) -> None:
    values = (
        b"line\r\n",
        b"line\n",
        b"line",
        b" line",
        b"line ",
        "lineπ".encode(),
        b"line\xff",
    )
    artifacts = [
        _envelope(
            inputs,
            raw_response_bytes=value,
        )
        for value in values
    ]
    assert len({item["raw_response_sha256"] for item in artifacts}) == len(
        values
    )
    assert len(
        {item[ENVELOPE_IDENTITY_FIELD] for item in artifacts}
    ) == len(values)


def test_r1c_v2_exact_byte_bounds(inputs: _Inputs) -> None:
    maximum = b"x" * 262_144
    artifact = _envelope(
        inputs,
        raw_response_bytes=maximum,
    )
    assert artifact["raw_response_byte_length"] == 262_144
    assert len(artifact["raw_response_base64"]) == 349_528
    with pytest.raises(MmiRawResponseEnvelopeV2Error):
        _envelope(
            inputs,
            raw_response_bytes=b"",
        )
    with pytest.raises(MmiRawResponseEnvelopeV2Error):
        _envelope(
            inputs,
            raw_response_bytes=maximum + b"x",
        )


@pytest.mark.parametrize(
    "raw_response",
    [
        "text",
        bytearray(b"bytes"),
        memoryview(b"bytes"),
        Path("response.json"),
        {"response": "parsed"},
    ],
)
def test_r1c_v2_rejects_every_non_bytes_input(
    inputs: _Inputs,
    raw_response: object,
) -> None:
    with pytest.raises(MmiRawResponseEnvelopeV2Error):
        _envelope(
            inputs,
            raw_response_bytes=raw_response,
        )


def test_r1c_v2_rejects_v1_malformed_and_stale_g2_artifacts(
    inputs: _Inputs,
) -> None:
    v1 = _prompt(inputs)
    v1["schema_version"] = "mmi_grounded_prompt_v1"
    v1["instruction_set_version"] = (
        "mmi_grounded_prompt_instruction_set_v1"
    )
    v1["expected_response_schema_version"] = (
        "mmi_grounded_analysis_response_v1"
    )
    validate_artifact_schema(
        v1,
        schema_name="mmi_grounded_prompt_v1.schema.json",
    )
    with pytest.raises(MmiRawResponseEnvelopeV2Error):
        _envelope(
            inputs,
            prompt=v1,
            raw_response_bytes=b"{}",
        )

    malformed = _prompt(inputs)
    malformed["provider"] = "forbidden"
    with pytest.raises(MmiRawResponseEnvelopeV2Error):
        _envelope(
            inputs,
            prompt=malformed,
            raw_response_bytes=b"{}",
        )

    stale = _prompt(inputs)
    stale["grounded_prompt_artifact_identity_sha256"] = "f" * 64
    with pytest.raises(MmiRawResponseEnvelopeV2Error):
        _envelope(
            inputs,
            prompt=stale,
            raw_response_bytes=b"{}",
        )


def test_r1c_v2_rejects_correlated_resealed_source_mismatch(
    inputs: _Inputs,
) -> None:
    fabricated_view = _resealed_ticker_mismatch(inputs)
    fabricated_prompt = _correlated_resealed_prompt(
        inputs,
        fabricated_view,
    )
    with pytest.raises(MmiRawResponseEnvelopeV2Error):
        _envelope(
            inputs,
            prompt=fabricated_prompt,
            raw_response_bytes=b"exact operator bytes",
        )


def test_r1c_v2_identity_is_deterministic_and_prompt_bound(
    inputs: _Inputs,
) -> None:
    prompt = _prompt(inputs)
    first = _envelope(
        inputs,
        prompt=prompt,
        raw_response_bytes=b"exact bytes",
    )
    second = _envelope(
        inputs,
        prompt=dict(reversed(tuple(prompt.items()))),
        raw_response_bytes=b"exact bytes",
    )
    assert first == second
    assert first[ENVELOPE_IDENTITY_FIELD] == (
        _independent_envelope_identity(first)
    )
    assert first["grounded_prompt_artifact_identity_sha256"] == prompt[
        "grounded_prompt_artifact_identity_sha256"
    ]


def test_r1c_v2_does_not_parse_response_or_write_files(
    inputs: _Inputs,
    tmp_path: Path,
) -> None:
    before = tuple(tmp_path.iterdir())
    raw = b"\xffnot-json\x00\r\n  "
    artifact = _envelope(
        inputs,
        raw_response_bytes=raw,
    )
    assert tuple(tmp_path.iterdir()) == before == ()
    assert base64.b64decode(artifact["raw_response_base64"]) == raw


def test_r1c_v2_has_no_transport_or_authority_shaped_fields(
    inputs: _Inputs,
) -> None:
    artifact = _envelope(
        inputs,
        raw_response_bytes=b"{}",
    )
    assert not set(artifact) & {
        "provider",
        "model",
        "timestamp",
        "path",
        "response_payload",
        "availability",
        "permission",
        "budget",
        "quantity",
        "gate",
        "publication",
        "order",
        "execution",
    }
    assert mmi.__all__ == ()
    assert not hasattr(mmi, "build_mmi_raw_response_envelope_v2")


def test_sources_are_test_owned_and_live_inputs_are_unreachable(
    checkout: hermetic.HermeticSourceCheckout,
    inputs: _Inputs,
) -> None:
    hermetic.assert_checkout_resolves_both_locators(checkout.root)
    hermetic.assert_test_owned_source(
        inputs.policy_source,
        role=MmiSourceRole.STRATEGY_SETTINGS,
        raw=checkout.strategy_settings_raw,
    )
    assert inputs.policy_source.source_record[
        "repository_relative_locator"
    ] == hermetic.STRATEGY_SETTINGS_LOCATOR
    hermetic.assert_live_operational_inputs_are_unreachable()
