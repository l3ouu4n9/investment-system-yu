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
from investment_orchestrator.mmi.canonical import (
    MAX_MMI_GROUNDED_ANALYSIS_RESPONSE_V2_CANONICAL_BYTES,
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
    validate_mmi_raw_response_envelope_v2,
)
from investment_orchestrator.mmi.validated_grounded_analysis_response_v2 import (
    MmiValidatedGroundedAnalysisResponseV2Error,
    _validate_response_payload_canonical_size,
    build_mmi_validated_grounded_analysis_response_v2,
    _build_mmi_validated_grounded_analysis_response_v2_from_source_record_identities,
    validate_mmi_validated_grounded_analysis_response_v2,
)
import _mmi_hermetic_source_checkout as hermetic


VIEW_DOMAIN = b"mmi_analyst_visible_evidence_view_v2\0"
CONTEXT_DOMAIN = b"mmi_grounded_prompt_context_binding_v2\0"
PROMPT_DOMAIN = b"mmi_grounded_prompt_artifact_v2\0"
ENVELOPE_DOMAIN = b"mmi_raw_response_envelope_v2\0"
RESPONSE_DOMAIN = b"mmi_validated_grounded_analysis_response_v2\0"
VIEW_IDENTITY_FIELD = "analyst_visible_evidence_view_identity_sha256"
PROMPT_IDENTITY_FIELD = "grounded_prompt_artifact_identity_sha256"
ENVELOPE_IDENTITY_FIELD = "raw_response_envelope_identity_sha256"
RESPONSE_IDENTITY_FIELD = (
    "validated_grounded_analysis_response_identity_sha256"
)
EXPECTED_FIELDS = {
    "schema_version",
    "artifact_kind",
    "report_only",
    "authority_effect",
    "manual_handoff_required",
    ENVELOPE_IDENTITY_FIELD,
    "response_payload",
    RESPONSE_IDENTITY_FIELD,
}
EVALUATION_TIME = datetime(
    2026,
    7,
    31,
    12,
    tzinfo=timezone.utc,
)
# Test-owned source dates, fixed before ``EVALUATION_TIME`` so that an
# operational ``inputs/current`` refresh cannot reach this module.
SOURCE_AS_OF = "2026-07-30"
SOURCE_RUN_TIMESTAMP_ET = "2026-07-30 10:00 ET"


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
        prompt: dict[str, object],
    ) -> None:
        self.policy = policy
        self.policy_source = policy_source
        self.evidence = evidence
        self.run_context = run_context
        self.view = view
        self.prompt = prompt


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
        "r2d-hermetic-checkout",
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
    view = dict(view_result.projection)
    prompt = build_mmi_grounded_prompt_v2(
        analyst_visible_evidence_view=deepcopy(view),
        evidence_bundle=deepcopy(evidence),
        policy_projection=deepcopy(policy),
        policy_source=capture,
        portfolio_projection=None,
        portfolio_source=None,
        run_context=run_context,
    )
    return _Inputs(
        policy=policy,
        policy_source=capture,
        evidence=evidence,
        run_context=run_context,
        view=view,
        prompt=prompt,
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


def _context_kwargs(inputs: _Inputs) -> dict[str, object]:
    return {
        "evidence_bundle": deepcopy(inputs.evidence),
        "policy_projection": deepcopy(inputs.policy),
        "policy_source": inputs.policy_source,
        "portfolio_projection": None,
        "portfolio_source": None,
        "run_context": inputs.run_context,
    }


def _expected_tickers(inputs: _Inputs) -> list[str]:
    policy_view = inputs.view["policy_view"]
    assert type(policy_view) is dict
    instruments = policy_view["analysis_instruments"]
    assert type(instruments) is list
    return [
        item["ticker"]
        for item in instruments
        if type(item) is dict and type(item.get("ticker")) is str
    ]


def _payload(
    inputs: _Inputs,
    *,
    context_binding: str | None = None,
) -> dict[str, object]:
    context = inputs.prompt["prompt_context_binding_sha256"]
    assert type(context) is str
    return {
        "response_schema_version": "mmi_grounded_analysis_response_v2",
        "prompt_context_binding_sha256": (
            context if context_binding is None else context_binding
        ),
        "analysis_status": "INSUFFICIENT_EVIDENCE",
        "instrument_views": [
            {
                "ticker": ticker,
                "evidence_status": "UNAVAILABLE",
                "rationale_12m_plus": None,
                "references": [],
            }
            for ticker in _expected_tickers(inputs)
        ],
        "anchor_associations_status": "UNAVAILABLE",
        "scheduled_events_status": "UNAVAILABLE",
        "regime_observation_status": "UNAVAILABLE",
        "evidence_observations": [],
        "risks": [],
        "uncertainties": [],
        "contradictions": [],
        "research_questions": [],
        "summary": {
            "text": "Evidence remains insufficient for action. π",
            "references": ["VIEW.EVALUATION_TIMESTAMP"],
            "hypothesis": False,
        },
    }


def _raw(payload: dict[str, object], *, surrounding: bytes = b"") -> bytes:
    return surrounding + json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8") + surrounding


def _envelope(inputs: _Inputs, exact_bytes: bytes) -> dict[str, object]:
    return build_mmi_raw_response_envelope_v2(
        grounded_prompt=deepcopy(inputs.prompt),
        raw_response_bytes=exact_bytes,
        **_context_kwargs(inputs),
    )


def _build(
    inputs: _Inputs,
    *,
    payload: dict[str, object] | None = None,
    exact_bytes: bytes | None = None,
    envelope: dict[str, object] | None = None,
) -> dict[str, object]:
    response_bytes = (
        _raw(_payload(inputs) if payload is None else payload)
        if exact_bytes is None
        else exact_bytes
    )
    return build_mmi_validated_grounded_analysis_response_v2(
        raw_response_envelope=(
            _envelope(inputs, response_bytes)
            if envelope is None
            else envelope
        ),
        **_context_kwargs(inputs),
    )


def _reseal_envelope(envelope: dict[str, object]) -> None:
    envelope[ENVELOPE_IDENTITY_FIELD] = _record_identity(
        ENVELOPE_DOMAIN,
        envelope,
        ENVELOPE_IDENTITY_FIELD,
    )


def test_r2c_v2_api_is_explicit_and_keyword_only() -> None:
    assert tuple(
        inspect.signature(validate_mmi_raw_response_envelope_v2).parameters
    ) == (
        "value",
        "evidence_bundle",
        "policy_projection",
        "policy_source",
        "portfolio_projection",
        "portfolio_source",
        "run_context",
    )
    assert tuple(
        inspect.signature(
            build_mmi_validated_grounded_analysis_response_v2
        ).parameters
    ) == (
        "raw_response_envelope",
        "evidence_bundle",
        "policy_projection",
        "policy_source",
        "portfolio_projection",
        "portfolio_source",
        "run_context",
    )
    assert tuple(
        inspect.signature(
            validate_mmi_validated_grounded_analysis_response_v2
        ).parameters
    ) == (
        "value",
        "raw_response_envelope",
        "evidence_bundle",
        "policy_projection",
        "policy_source",
        "portfolio_projection",
        "portfolio_source",
        "run_context",
    )
    for function in (
        validate_mmi_raw_response_envelope_v2,
        build_mmi_validated_grounded_analysis_response_v2,
        validate_mmi_validated_grounded_analysis_response_v2,
    ):
        assert all(
            parameter.kind is inspect.Parameter.KEYWORD_ONLY
            and parameter.default is inspect.Parameter.empty
            for parameter in inspect.signature(function).parameters.values()
        )


def test_r2c_v2_builds_exact_closed_deterministic_artifact(
    inputs: _Inputs,
) -> None:
    exact_bytes = _raw(_payload(inputs))
    envelope = _envelope(inputs, exact_bytes)
    first = _build(inputs, exact_bytes=exact_bytes, envelope=envelope)
    second = _build(inputs, exact_bytes=exact_bytes, envelope=envelope)
    assert first == second
    assert set(first) == EXPECTED_FIELDS
    assert first["schema_version"] == (
        "mmi_validated_grounded_analysis_response_v2"
    )
    assert first["artifact_kind"] == (
        "MMI_VALIDATED_GROUNDED_ANALYSIS_RESPONSE"
    )
    assert first["report_only"] is True
    assert first["authority_effect"] == "NONE"
    assert first["manual_handoff_required"] is True
    assert first[ENVELOPE_IDENTITY_FIELD] == envelope[
        ENVELOPE_IDENTITY_FIELD
    ]
    assert first[RESPONSE_IDENTITY_FIELD] == _record_identity(
        RESPONSE_DOMAIN,
        first,
        RESPONSE_IDENTITY_FIELD,
    )
    validate_artifact_schema(
        first,
        schema_name=(
            "mmi_validated_grounded_analysis_response_v2.schema.json"
        ),
    )
    assert validate_mmi_validated_grounded_analysis_response_v2(
        value=first,
        raw_response_envelope=envelope,
        **_context_kwargs(inputs),
    ) == first


def test_candidate_validator_rejects_stale_and_resealed_changes(
    inputs: _Inputs,
) -> None:
    exact_bytes = _raw(_payload(inputs))
    envelope = _envelope(inputs, exact_bytes)
    artifact = _build(inputs, exact_bytes=exact_bytes, envelope=envelope)
    changed = deepcopy(artifact)
    payload = changed["response_payload"]
    assert type(payload) is dict
    summary = payload["summary"]
    assert type(summary) is dict
    summary["text"] = "Changed but still schema-valid."
    for candidate in (changed, deepcopy(changed)):
        if candidate is not changed:
            candidate[RESPONSE_IDENTITY_FIELD] = _record_identity(
                RESPONSE_DOMAIN,
                candidate,
                RESPONSE_IDENTITY_FIELD,
            )
        with pytest.raises(MmiValidatedGroundedAnalysisResponseV2Error):
            validate_mmi_validated_grounded_analysis_response_v2(
                value=candidate,
                raw_response_envelope=envelope,
                **_context_kwargs(inputs),
            )


@pytest.mark.parametrize("surrounding", [b"\n", b"\r\n", b" "])
def test_exact_json_whitespace_remains_identity_bound(
    inputs: _Inputs,
    surrounding: bytes,
) -> None:
    payload = _payload(inputs)
    plain = _raw(payload)
    surrounded = _raw(payload, surrounding=surrounding)
    assert plain != surrounded
    plain_artifact = _build(inputs, exact_bytes=plain)
    surrounded_artifact = _build(inputs, exact_bytes=surrounded)
    assert plain_artifact["response_payload"] == surrounded_artifact[
        "response_payload"
    ]
    assert plain_artifact[ENVELOPE_IDENTITY_FIELD] != surrounded_artifact[
        ENVELOPE_IDENTITY_FIELD
    ]
    assert plain_artifact[RESPONSE_IDENTITY_FIELD] != surrounded_artifact[
        RESPONSE_IDENTITY_FIELD
    ]


def test_valid_non_ascii_json_is_preserved_and_parsed(inputs: _Inputs) -> None:
    payload = _payload(inputs)
    raw = _raw(payload)
    assert "π".encode("utf-8") in raw
    artifact = _build(inputs, exact_bytes=raw)
    assert artifact["response_payload"] == payload


@pytest.mark.parametrize(
    "raw",
    [
        b"\xff",
        b"\xef\xbb\xbf{}",
        b'{"text":"\x80"}',
        b"{",
        b"[]",
    ],
)
def test_strict_utf8_and_json_reject_invalid_inputs(
    inputs: _Inputs,
    raw: bytes,
) -> None:
    with pytest.raises(MmiValidatedGroundedAnalysisResponseV2Error):
        _build(inputs, exact_bytes=raw)


def test_json_duplicate_keys_nonfinite_and_trailing_content_are_rejected(
    inputs: _Inputs,
) -> None:
    valid = _raw(_payload(inputs))
    duplicate = b'{"response_schema_version":"duplicate",' + valid[1:]
    nonfinite = valid.replace(
        b'"hypothesis":false',
        b'"hypothesis":NaN',
        1,
    )
    for raw in (duplicate, nonfinite, valid + b"{}"):
        with pytest.raises(MmiValidatedGroundedAnalysisResponseV2Error):
            _build(inputs, exact_bytes=raw)


def test_malformed_r1c_v2_envelope_cannot_reach_r2c_v2(
    inputs: _Inputs,
) -> None:
    envelope = _envelope(inputs, _raw(_payload(inputs)))
    envelope["raw_response_sha256"] = "0" * 64
    _reseal_envelope(envelope)
    with pytest.raises(MmiValidatedGroundedAnalysisResponseV2Error):
        _build(inputs, envelope=envelope)


def test_prompt_context_mismatch_fails(inputs: _Inputs) -> None:
    with pytest.raises(MmiValidatedGroundedAnalysisResponseV2Error):
        _build(inputs, payload=_payload(inputs, context_binding="0" * 64))


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "foreign", "reordered"])
def test_complete_instrument_membership_and_order_are_required(
    inputs: _Inputs,
    mutation: str,
) -> None:
    payload = _payload(inputs)
    views = payload["instrument_views"]
    assert type(views) is list and len(views) >= 2
    if mutation == "missing":
        views.pop()
    elif mutation == "duplicate":
        views[-1] = deepcopy(views[0])
    elif mutation == "foreign":
        assert type(views[-1]) is dict
        views[-1]["ticker"] = "IWM"
    else:
        views[0], views[1] = views[1], views[0]
    with pytest.raises(MmiValidatedGroundedAnalysisResponseV2Error):
        _build(inputs, payload=payload)


def test_evidence_status_conditional_branches_are_enforced(
    inputs: _Inputs,
) -> None:
    unavailable = _payload(inputs)
    unavailable_view = unavailable["instrument_views"][0]  # type: ignore[index]
    assert type(unavailable_view) is dict
    unavailable_view["rationale_12m_plus"] = "not allowed"
    supported = _payload(inputs)
    supported_view = supported["instrument_views"][0]  # type: ignore[index]
    assert type(supported_view) is dict
    supported_view["evidence_status"] = "EVIDENCE_SUPPORTED"
    for payload in (unavailable, supported):
        with pytest.raises(MmiValidatedGroundedAnalysisResponseV2Error):
            _build(inputs, payload=payload)


def test_component_constants_and_source_reference_membership_are_enforced(
    inputs: _Inputs,
) -> None:
    wrong_component = _payload(inputs)
    wrong_component["anchor_associations_status"] = "AVAILABLE"
    out_of_context_reference = _payload(inputs)
    summary = out_of_context_reference["summary"]
    assert type(summary) is dict
    summary["references"] = ["POLICY.INSTRUMENT.0256"]
    unknown_reference = _payload(inputs)
    summary = unknown_reference["summary"]
    assert type(summary) is dict
    summary["references"] = ["ANCHOR.0001"]
    for payload in (
        wrong_component,
        out_of_context_reference,
        unknown_reference,
    ):
        with pytest.raises(MmiValidatedGroundedAnalysisResponseV2Error):
            _build(inputs, payload=payload)


def test_exact_canonical_payload_guard_owns_245760_byte_limit() -> None:
    framing = len(_canonical({"x": ""}))
    accepted = {
        "x": "x"
        * (
            MAX_MMI_GROUNDED_ANALYSIS_RESPONSE_V2_CANONICAL_BYTES
            - framing
        )
    }
    rejected = {"x": accepted["x"] + "x"}
    assert len(_canonical(accepted)) == 245_760
    assert len(_canonical(rejected)) == 245_761
    _validate_response_payload_canonical_size(accepted)
    with pytest.raises(MmiValidatedGroundedAnalysisResponseV2Error):
        _validate_response_payload_canonical_size(rejected)


def test_largest_practical_valid_payload_passes_real_builder(
    inputs: _Inputs,
) -> None:
    payload = _payload(inputs)
    summary = payload["summary"]
    assert type(summary) is dict
    summary["text"] = "é" * 4_000
    views = payload["instrument_views"]
    assert type(views) is list
    for position, view in enumerate(views, 1):
        assert type(view) is dict
        view["evidence_status"] = "INSUFFICIENT_EVIDENCE"
        view["rationale_12m_plus"] = "é" * 2_000
        view["references"] = [f"POLICY.INSTRUMENT.{position:04d}"]
    canonical = _canonical(payload)
    assert len(canonical) < 245_760
    artifact = _build(inputs, payload=payload)
    assert artifact["response_payload"] == payload


def _fabricated_source_chain(
    inputs: _Inputs,
) -> dict[str, object]:
    fabricated_view = deepcopy(inputs.view)
    policy_view = fabricated_view["policy_view"]
    assert type(policy_view) is dict
    instruments = policy_view["analysis_instruments"]
    assert type(instruments) is list
    qqq = next(
        item
        for item in instruments
        if type(item) is dict and item.get("ticker") == "QQQ"
    )
    qqq["ticker"] = "IWM"
    fabricated_view[VIEW_IDENTITY_FIELD] = _record_identity(
        VIEW_DOMAIN,
        fabricated_view,
        VIEW_IDENTITY_FIELD,
    )
    validate_artifact_schema(
        fabricated_view,
        schema_name="mmi_analyst_visible_evidence_view_v2.schema.json",
    )

    fabricated_prompt = deepcopy(inputs.prompt)
    fabricated_prompt[VIEW_IDENTITY_FIELD] = fabricated_view[
        VIEW_IDENTITY_FIELD
    ]
    fabricated_prompt["prompt_context_binding_sha256"] = _framed_identity(
        CONTEXT_DOMAIN,
        {
            VIEW_IDENTITY_FIELD: fabricated_prompt[VIEW_IDENTITY_FIELD],
            "instruction_set_version": fabricated_prompt[
                "instruction_set_version"
            ],
            "expected_response_schema_version": fabricated_prompt[
                "expected_response_schema_version"
            ],
            "report_only": True,
            "authority_effect": "NONE",
            "manual_handoff_required": True,
        },
    )
    valid_evidence = _canonical(inputs.view).decode("utf-8")
    fabricated_evidence = _canonical(fabricated_view).decode("utf-8")
    prompt_text = fabricated_prompt["prompt_text"]
    valid_context = inputs.prompt["prompt_context_binding_sha256"]
    fabricated_context = fabricated_prompt["prompt_context_binding_sha256"]
    assert all(
        type(value) is str
        for value in (prompt_text, valid_context, fabricated_context)
    )
    assert len(valid_evidence.encode()) == len(fabricated_evidence.encode())
    fabricated_prompt["prompt_text"] = prompt_text.replace(  # type: ignore[union-attr]
        valid_context,  # type: ignore[arg-type]
        fabricated_context,  # type: ignore[arg-type]
        1,
    ).replace(valid_evidence, fabricated_evidence, 1)
    fabricated_prompt[PROMPT_IDENTITY_FIELD] = _record_identity(
        PROMPT_DOMAIN,
        fabricated_prompt,
        PROMPT_IDENTITY_FIELD,
    )
    validate_artifact_schema(
        fabricated_prompt,
        schema_name="mmi_grounded_prompt_v2.schema.json",
    )

    payload = _payload(
        inputs,
        context_binding=fabricated_prompt[
            "prompt_context_binding_sha256"
        ],  # type: ignore[arg-type]
    )
    payload["instrument_views"] = [
        {
            "ticker": item["ticker"],
            "evidence_status": "UNAVAILABLE",
            "rationale_12m_plus": None,
            "references": [],
        }
        for item in instruments
        if type(item) is dict
    ]
    exact = _raw(payload)
    envelope = {
        "schema_version": "mmi_raw_response_envelope_v2",
        "artifact_kind": "MMI_RAW_RESPONSE_ENVELOPE",
        "report_only": True,
        "authority_effect": "NONE",
        "manual_handoff_required": True,
        PROMPT_IDENTITY_FIELD: fabricated_prompt[PROMPT_IDENTITY_FIELD],
        "raw_response_byte_length": len(exact),
        "raw_response_sha256": hashlib.sha256(exact).hexdigest(),
        "raw_response_base64": base64.b64encode(exact).decode("ascii"),
        ENVELOPE_IDENTITY_FIELD: "0" * 64,
    }
    _reseal_envelope(envelope)
    validate_artifact_schema(
        envelope,
        schema_name="mmi_raw_response_envelope_v2.schema.json",
    )
    return envelope


def test_fully_resealed_source_invalid_chain_cannot_reach_r2c_v2(
    inputs: _Inputs,
) -> None:
    envelope = _fabricated_source_chain(inputs)
    with pytest.raises(MmiValidatedGroundedAnalysisResponseV2Error):
        _build(inputs, envelope=envelope)


def test_r2c_v2_has_no_io_export_or_authority_surface(
    inputs: _Inputs,
    tmp_path: Path,
) -> None:
    before = tuple(tmp_path.iterdir())
    artifact = _build(inputs)
    assert tuple(tmp_path.iterdir()) == before == ()
    assert mmi.__all__ == ()
    assert not hasattr(
        mmi,
        "build_mmi_validated_grounded_analysis_response_v2",
    )
    assert not set(artifact) & {
        "availability",
        "freshness",
        "permission",
        "action",
        "budget",
        "cap",
        "quantity",
        "gate",
        "publication",
        "order",
        "execution",
        "broker",
        "provider",
        "model",
        "path",
        "timestamp",
    }


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


def test_r2c_v2_deterministic_builder_regression_oracle(inputs: _Inputs) -> None:
    """Prove identical output bytes and preservation of live provenance gates."""
    envelope = _envelope(inputs, _raw(_payload(inputs)))

    # 1. Live wrapper succeeds
    live_response = build_mmi_validated_grounded_analysis_response_v2(
        raw_response_envelope=envelope,
        evidence_bundle=deepcopy(inputs.evidence),
        policy_projection=deepcopy(inputs.policy),
        policy_source=inputs.policy_source,
        portfolio_projection=None,
        portfolio_source=None,
        run_context=inputs.run_context,
    )

    policy_identity = dict(inputs.policy_source.source_record)["source_record_identity_sha256"]

    assert type(policy_identity) is str

    # 2. Deterministic helper succeeds with identical output
    deterministic_response = _build_mmi_validated_grounded_analysis_response_v2_from_source_record_identities(
        raw_response_envelope=envelope,
        evidence_bundle=deepcopy(inputs.evidence),
        policy_projection=deepcopy(inputs.policy),
        policy_source_record_identity_sha256=policy_identity,
        portfolio_projection=None,
        portfolio_source_record_identity_sha256=None,
        run_context=inputs.run_context,
    )

    assert live_response == deterministic_response
