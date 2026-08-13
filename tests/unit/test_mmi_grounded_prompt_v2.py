from __future__ import annotations

from collections import OrderedDict
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import inspect
import json
import struct

import pytest

import investment_orchestrator.mmi as mmi
from investment_orchestrator.common.schema_validation import (
    validate_artifact_schema,
)
from investment_orchestrator.mmi import grounded_prompt_v2 as g2
from investment_orchestrator.mmi.analyst_visible_evidence_view_v2 import (
    build_mmi_analyst_visible_evidence_view_v2,
    validate_mmi_analyst_visible_evidence_view_v2,
)
from investment_orchestrator.mmi.canonical import (
    MAXIMUM_ANALYST_VISIBLE_EVIDENCE_VIEW_CANONICAL_BYTES,
    MAXIMUM_GROUNDED_PROMPT_TEXT_BYTES,
)
from investment_orchestrator.mmi.contracts import (
    MmiCapturedSource,
    MmiProjectionResultCategory,
    MmiProjectionRunContext,
    MmiSourceRole,
    _begin_mmi_projection_run_with_clock,
)
from investment_orchestrator.mmi.evidence_bundle import (
    build_mmi_authenticated_evidence_bundle,
)
from investment_orchestrator.mmi.grounded_prompt_v2 import (
    MmiGroundedPromptV2Error,
    build_mmi_grounded_prompt_v2,
    validate_mmi_grounded_prompt_v2,
)
from investment_orchestrator.mmi.policy_projection import (
    build_mmi_policy_projection,
)
from investment_orchestrator.mmi.portfolio_projection import (
    build_mmi_portfolio_snapshot_projection,
)
import _mmi_hermetic_source_checkout as hermetic


VIEW_DOMAIN = b"mmi_analyst_visible_evidence_view_v2\0"
CONTEXT_DOMAIN = b"mmi_grounded_prompt_context_binding_v2\0"
ARTIFACT_DOMAIN = b"mmi_grounded_prompt_artifact_v2\0"
VIEW_IDENTITY_FIELD = "analyst_visible_evidence_view_identity_sha256"
ARTIFACT_IDENTITY_FIELD = "grounded_prompt_artifact_identity_sha256"
EXPECTED_FIELDS = {
    "schema_version",
    "artifact_kind",
    "report_only",
    "authority_effect",
    VIEW_IDENTITY_FIELD,
    "instruction_set_version",
    "expected_response_schema_version",
    "manual_handoff_required",
    "prompt_context_binding_sha256",
    "prompt_text",
    ARTIFACT_IDENTITY_FIELD,
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
        portfolio: dict[str, object],
        portfolio_source: MmiCapturedSource,
        evidence: dict[str, object],
        run_context: MmiProjectionRunContext,
        view: dict[str, object],
    ) -> None:
        self.policy = policy
        self.policy_source = policy_source
        self.portfolio = portfolio
        self.portfolio_source = portfolio_source
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
        "g2c-hermetic-checkout",
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
    portfolio_capture = checkout.portfolio_source
    portfolio_result = build_mmi_portfolio_snapshot_projection(
        portfolio_capture,
        policy_projection=deepcopy(policy),
        policy_source=capture,
        run_context=run_context,
    )
    assert portfolio_result.valid, portfolio_result.reason_codes
    assert portfolio_result.projection is not None
    portfolio = dict(portfolio_result.projection)
    evidence_result = build_mmi_authenticated_evidence_bundle(
        policy_projection=deepcopy(policy),
        policy_source=capture,
        portfolio_projection=deepcopy(portfolio),
        portfolio_source=portfolio_capture,
        run_context=run_context,
    )
    assert evidence_result.valid, evidence_result.reason_codes
    assert evidence_result.projection is not None
    evidence = dict(evidence_result.projection)
    view_result = build_mmi_analyst_visible_evidence_view_v2(
        evidence_bundle=deepcopy(evidence),
        policy_projection=deepcopy(policy),
        policy_source=capture,
        portfolio_projection=deepcopy(portfolio),
        portfolio_source=portfolio_capture,
        run_context=run_context,
    )
    assert view_result.valid, view_result.reason_codes
    assert view_result.projection is not None
    return _Inputs(
        policy=policy,
        policy_source=capture,
        portfolio=portfolio,
        portfolio_source=portfolio_capture,
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


def _framed_identity(domain: bytes, value: object) -> str:
    canonical = _canonical(value)
    return hashlib.sha256(
        domain + struct.pack(">Q", len(canonical)) + canonical
    ).hexdigest()


def _record_identity(
    domain: bytes,
    value: dict[str, object],
    identity_field: str,
) -> str:
    preimage = deepcopy(value)
    preimage.pop(identity_field, None)
    return _framed_identity(domain, preimage)


def _valid_view(inputs: _Inputs) -> dict[str, object]:
    return deepcopy(inputs.view)


def _build_prompt(
    inputs: _Inputs,
    *,
    view: object | None = None,
) -> dict[str, object]:
    candidate = _valid_view(inputs) if view is None else view
    return build_mmi_grounded_prompt_v2(
        analyst_visible_evidence_view=candidate,  # type: ignore[arg-type]
        evidence_bundle=deepcopy(inputs.evidence),
        policy_projection=deepcopy(inputs.policy),
        policy_source=inputs.policy_source,
        portfolio_projection=deepcopy(inputs.portfolio),
        portfolio_source=inputs.portfolio_source,
        run_context=inputs.run_context,
    )


def _validate_prompt(
    value: dict[str, object],
    inputs: _Inputs,
) -> dict[str, object]:
    return validate_mmi_grounded_prompt_v2(
        value=value,
        evidence_bundle=deepcopy(inputs.evidence),
        policy_projection=deepcopy(inputs.policy),
        policy_source=inputs.policy_source,
        portfolio_projection=deepcopy(inputs.portfolio),
        portfolio_source=inputs.portfolio_source,
        run_context=inputs.run_context,
    )


def _validate_view(
    value: dict[str, object],
    inputs: _Inputs,
):
    return validate_mmi_analyst_visible_evidence_view_v2(
        value=value,
        evidence_bundle=deepcopy(inputs.evidence),
        policy_projection=deepcopy(inputs.policy),
        policy_source=inputs.policy_source,
        portfolio_projection=deepcopy(inputs.portfolio),
        portfolio_source=inputs.portfolio_source,
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
    artifact = _build_prompt(inputs, view=valid_view)
    fabricated = deepcopy(artifact)
    fabricated[VIEW_IDENTITY_FIELD] = fabricated_view[
        VIEW_IDENTITY_FIELD
    ]
    fabricated["prompt_context_binding_sha256"] = _independent_context(
        fabricated
    )
    prompt_text = artifact["prompt_text"]
    assert type(prompt_text) is str
    old_context = artifact["prompt_context_binding_sha256"]
    new_context = fabricated["prompt_context_binding_sha256"]
    assert type(old_context) is str and type(new_context) is str
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
    fabricated[ARTIFACT_IDENTITY_FIELD] = (
        _independent_artifact_identity(fabricated)
    )
    validate_artifact_schema(
        fabricated,
        schema_name="mmi_grounded_prompt_v2.schema.json",
    )
    assert fabricated[ARTIFACT_IDENTITY_FIELD] == (
        _independent_artifact_identity(fabricated)
    )
    return fabricated


def _independent_context(artifact: dict[str, object]) -> str:
    return _framed_identity(
        CONTEXT_DOMAIN,
        {
            VIEW_IDENTITY_FIELD: artifact[VIEW_IDENTITY_FIELD],
            "instruction_set_version": artifact[
                "instruction_set_version"
            ],
            "expected_response_schema_version": artifact[
                "expected_response_schema_version"
            ],
            "report_only": artifact["report_only"],
            "authority_effect": artifact["authority_effect"],
            "manual_handoff_required": artifact[
                "manual_handoff_required"
            ],
        },
    )


def _independent_artifact_identity(
    artifact: dict[str, object],
) -> str:
    return _record_identity(
        ARTIFACT_DOMAIN,
        artifact,
        ARTIFACT_IDENTITY_FIELD,
    )


def test_g2_source_bound_api_is_explicit_keyword_only() -> None:
    source_inputs = (
        "evidence_bundle",
        "policy_projection",
        "policy_source",
        "portfolio_projection",
        "portfolio_source",
        "run_context",
    )
    assert tuple(inspect.signature(build_mmi_grounded_prompt_v2).parameters) == (
        "analyst_visible_evidence_view",
        *source_inputs,
    )
    assert tuple(
        inspect.signature(validate_mmi_grounded_prompt_v2).parameters
    ) == ("value", *source_inputs)
    for function in (
        build_mmi_grounded_prompt_v2,
        validate_mmi_grounded_prompt_v2,
    ):
        assert all(
            parameter.kind is inspect.Parameter.KEYWORD_ONLY
            for parameter in inspect.signature(function).parameters.values()
        )


def test_g2_builds_exact_closed_report_only_artifact(
    inputs: _Inputs,
) -> None:
    artifact = _build_prompt(inputs)
    assert set(artifact) == EXPECTED_FIELDS
    assert artifact["schema_version"] == "mmi_grounded_prompt_v2"
    assert artifact["artifact_kind"] == "MMI_GROUNDED_PROMPT"
    assert artifact["report_only"] is True
    assert artifact["authority_effect"] == "NONE"
    assert artifact["manual_handoff_required"] is True
    assert artifact["instruction_set_version"] == (
        "mmi_grounded_prompt_instruction_set_v3"
    )
    assert artifact["expected_response_schema_version"] == (
        "mmi_grounded_analysis_response_v2"
    )
    validate_artifact_schema(
        artifact,
        schema_name="mmi_grounded_prompt_v2.schema.json",
    )
    assert _validate_prompt(artifact, inputs) == artifact


def test_g2_context_and_artifact_identities_have_independent_oracles(
    inputs: _Inputs,
) -> None:
    artifact = _build_prompt(inputs)
    assert artifact["prompt_context_binding_sha256"] == (
        _independent_context(artifact)
    )
    assert artifact[ARTIFACT_IDENTITY_FIELD] == (
        _independent_artifact_identity(artifact)
    )


def test_g2_is_deterministic_across_equivalent_mapping_order(
    inputs: _Inputs,
) -> None:
    view = _valid_view(inputs)
    reversed_view = OrderedDict(reversed(tuple(view.items())))
    reversed_policy = OrderedDict(
        reversed(tuple(view["policy_view"].items()))  # type: ignore[union-attr]
    )
    reversed_view["policy_view"] = reversed_policy
    first = _build_prompt(inputs, view=view)
    second = _build_prompt(inputs, view=reversed_view)
    assert second == first
    assert second["prompt_context_binding_sha256"] == first[
        "prompt_context_binding_sha256"
    ]
    assert second["prompt_text"] == first["prompt_text"]
    assert second[ARTIFACT_IDENTITY_FIELD] == first[
        ARTIFACT_IDENTITY_FIELD
    ]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema_version", "mmi_analyst_visible_evidence_view_v1"),
        ("artifact_kind", "OTHER"),
        ("report_only", False),
        ("authority_effect", "HOLD"),
    ],
)
def test_g2_rejects_wrong_view_contract(
    inputs: _Inputs,
    field: str,
    value: object,
) -> None:
    view = _valid_view(inputs)
    view[field] = value
    view[VIEW_IDENTITY_FIELD] = _record_identity(
        VIEW_DOMAIN,
        view,
        VIEW_IDENTITY_FIELD,
    )
    with pytest.raises(MmiGroundedPromptV2Error):
        _build_prompt(inputs, view=view)


def test_g2_rejects_malformed_view_and_stale_upstream_identity(
    inputs: _Inputs,
) -> None:
    malformed = _valid_view(inputs)
    malformed["provider"] = "forbidden"
    with pytest.raises(MmiGroundedPromptV2Error):
        _build_prompt(inputs, view=malformed)

    stale = _valid_view(inputs)
    stale["evaluation_timestamp_utc"] = "2026-07-31T12:00:01.000000Z"
    with pytest.raises(MmiGroundedPromptV2Error):
        _build_prompt(inputs, view=stale)


def test_g2_rejects_resealed_source_bound_ticker_mismatch(
    inputs: _Inputs,
) -> None:
    fabricated_view = _resealed_ticker_mismatch(inputs)
    direct = _validate_view(fabricated_view, inputs)
    assert direct.status is (
        MmiProjectionResultCategory.PROJECTION_CONTRACT_FAILURE
    )
    assert direct.reason_codes == (
        "MMI_ANALYST_VIEW_V2_SOURCE_FIDELITY_MISMATCH",
    )
    with pytest.raises(MmiGroundedPromptV2Error):
        _build_prompt(inputs, view=fabricated_view)

    fabricated_prompt = _correlated_resealed_prompt(
        inputs,
        fabricated_view,
    )
    with pytest.raises(MmiGroundedPromptV2Error):
        _validate_prompt(fabricated_prompt, inputs)


def test_g2_requires_exact_v2_research_component_statuses(
    inputs: _Inputs,
) -> None:
    view = _valid_view(inputs)
    statuses = view["research_component_statuses"]
    assert type(statuses) is dict
    statuses["anchor_associations"] = "AVAILABLE"
    view[VIEW_IDENTITY_FIELD] = _record_identity(
        VIEW_DOMAIN,
        view,
        VIEW_IDENTITY_FIELD,
    )
    with pytest.raises(MmiGroundedPromptV2Error):
        _build_prompt(inputs, view=view)


def test_g2_prompt_preserves_manual_and_non_authority_boundary(
    inputs: _Inputs,
) -> None:
    artifact = _build_prompt(inputs)
    prompt = artifact["prompt_text"]
    assert type(prompt) is str
    assert "An operator manually submits this prompt." in prompt
    assert "The repository does not call an LLM." in prompt
    assert (
        "Only exact operator-supplied response bytes may later enter R1c-v2."
        in prompt
    )
    assert (
        "Qualitative output cannot grant availability, permissions, budgets, "
        "quantities, gates, publication, order readiness, or execution "
        "authority."
        in prompt
    )
    assert "ANCHOR_ASSOCIATIONS_STATUS=UNAVAILABLE" in prompt
    assert "SCHEDULED_EVENTS_STATUS=UNAVAILABLE" in prompt
    assert "REGIME_OBSERVATION_STATUS=UNAVAILABLE" in prompt
    assert "ANCHOR_ASSOCIATIONS_STATUS=AVAILABLE\n" not in prompt
    assert "SCHEDULED_EVENTS_STATUS=AVAILABLE\n" not in prompt
    assert "REGIME_OBSERVATION_STATUS=AVAILABLE\n" not in prompt


def test_g2_prompt_requires_each_referenced_view_to_include_own_reference(
    inputs: _Inputs,
) -> None:
    prompt = _build_prompt(inputs)["prompt_text"]
    assert type(prompt) is str
    assert (
        "UNAVAILABLE requires null rationale and no references; every other "
        "evidence status requires a nonempty rationale and 1-8 unique allowed "
        "references."
    ) in prompt
    assert (
        "For every non-UNAVAILABLE instrument view, references must include "
        "that instrument's own source-bound POLICY.INSTRUMENT.NNNN reference."
    ) in prompt
    assert (
        "Its position in the references array is not significant."
        in prompt
    )


def test_g2_prompt_text_utf8_guard_accepts_exact_limit() -> None:
    exact = "x" * 65_536
    assert len(exact.encode("utf-8")) == 65_536
    g2._validate_prompt_text_utf8_size(exact)


def test_g2_prompt_text_utf8_guard_rejects_one_byte_over() -> None:
    oversized = "x" * 65_537
    assert len(oversized.encode("utf-8")) == 65_537
    with pytest.raises(MmiGroundedPromptV2Error):
        g2._validate_prompt_text_utf8_size(oversized)


def test_g2_prompt_text_utf8_guard_counts_encoded_non_ascii_bytes() -> None:
    exact = "é" * 32_768
    assert len(exact) == 32_768
    assert len(exact.encode("utf-8")) == 65_536
    g2._validate_prompt_text_utf8_size(exact)
    with pytest.raises(MmiGroundedPromptV2Error):
        g2._validate_prompt_text_utf8_size(exact + "x")


def test_g2_builder_enforces_reachable_public_resource_contract(
    inputs: _Inputs,
) -> None:
    view = _valid_view(inputs)
    evidence_bytes = _canonical(view)
    artifact = _build_prompt(inputs, view=view)
    prompt_text = artifact["prompt_text"]
    assert type(prompt_text) is str
    prompt_bytes = prompt_text.encode("utf-8")
    assert (
        len(evidence_bytes)
        <= MAXIMUM_ANALYST_VISIBLE_EVIDENCE_VIEW_CANONICAL_BYTES
        < MAXIMUM_GROUNDED_PROMPT_TEXT_BYTES
    )
    assert len(prompt_bytes) <= MAXIMUM_GROUNDED_PROMPT_TEXT_BYTES
    assert (
        f"EVIDENCE_UTF8_BYTE_LENGTH={len(evidence_bytes)}\n".encode()
        in prompt_bytes
    )
    assert evidence_bytes in prompt_bytes


def test_g2_validator_rejects_resealed_nondeterministic_prompt(
    inputs: _Inputs,
) -> None:
    artifact = _build_prompt(inputs)
    artifact["prompt_text"] = "schema-valid but not a G2 rendering"
    artifact[ARTIFACT_IDENTITY_FIELD] = _independent_artifact_identity(
        artifact
    )
    validate_artifact_schema(
        artifact,
        schema_name="mmi_grounded_prompt_v2.schema.json",
    )
    with pytest.raises(MmiGroundedPromptV2Error):
        _validate_prompt(artifact, inputs)


def test_g2_artifact_has_no_transport_or_authority_shaped_fields(
    inputs: _Inputs,
) -> None:
    artifact = _build_prompt(inputs)
    assert not set(artifact) & {
        "provider",
        "model",
        "path",
        "timestamp",
        "availability",
        "permission",
        "budget",
        "quantity",
        "gate",
        "publication",
        "order",
        "execution",
    }
    module_source = inspect.getsource(g2)
    assert "_MMI_ANALYST_VISIBLE_EVIDENCE_VIEW_V2_IDENTITY_DOMAIN" not in (
        module_source
    )
    assert "MAXIMUM_ANALYST_VISIBLE_EVIDENCE_VIEW_CANONICAL_BYTES" not in (
        module_source
    )
    assert "MMI_ANALYST_VISIBLE_EVIDENCE_VIEW_V2_SCHEMA_VERSION" not in (
        module_source
    )
    assert "MMI_ANALYST_VISIBLE_EVIDENCE_VIEW_V2_ARTIFACT_KIND" not in (
        module_source
    )
    assert (
        "MMI_ANALYST_VISIBLE_EVIDENCE_VIEW_V2_RESEARCH_COMPONENT_STATUSES"
        not in module_source
    )
    assert mmi.__all__ == ()
    assert not hasattr(mmi, "build_mmi_grounded_prompt_v2")


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
    hermetic.assert_test_owned_source(
        inputs.portfolio_source,
        role=MmiSourceRole.PORTFOLIO_SNAPSHOT,
        raw=checkout.portfolio_snapshot_raw,
    )
    assert inputs.policy_source.source_record[
        "repository_relative_locator"
    ] == hermetic.STRATEGY_SETTINGS_LOCATOR
    assert inputs.portfolio_source.source_record[
        "repository_relative_locator"
    ] == hermetic.PORTFOLIO_SNAPSHOT_LOCATOR
    hermetic.assert_live_operational_inputs_are_unreachable()
