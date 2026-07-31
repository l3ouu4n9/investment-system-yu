from __future__ import annotations

import ast
from collections.abc import Iterator, Mapping
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import inspect
import json
import struct
from types import MappingProxyType

import pytest

from investment_orchestrator.common.paths import repo_root
from investment_orchestrator.common.schema_validation import (
    validate_artifact_schema,
)
from investment_orchestrator.mmi import (
    validated_grounded_analysis_response,
)
from investment_orchestrator.mmi.analyst_visible_evidence_view import (
    build_mmi_analyst_visible_evidence_view,
)
from investment_orchestrator.mmi.canonical import (
    MmiCanonicalizationError,
)
from investment_orchestrator.mmi.contracts import (
    MmiCapturedSource,
    MmiPolicyProjectionBuildResult,
    MmiPolicyProjectionValidationResult,
    MmiProjectionResultCategory,
    MmiProjectionRunContext,
    MmiSourceRole,
    _begin_mmi_projection_run_with_clock,
    mmi_validated_grounded_analysis_response_identity_sha256,
)
from investment_orchestrator.mmi.evidence_bundle import (
    build_mmi_authenticated_evidence_bundle,
)
from investment_orchestrator.mmi.grounded_prompt import (
    build_mmi_grounded_prompt,
)
from investment_orchestrator.mmi.policy_projection import (
    build_mmi_policy_projection,
)
from investment_orchestrator.mmi.portfolio_projection import (
    build_mmi_portfolio_snapshot_projection,
)
from investment_orchestrator.mmi.raw_response_envelope import (
    build_mmi_raw_response_envelope,
)
from investment_orchestrator.mmi.source_capture import (
    capture_current_mmi_source,
)
from investment_orchestrator.mmi.validated_grounded_analysis_response import (
    build_mmi_validated_grounded_analysis_response,
    validate_mmi_validated_grounded_analysis_response,
)


EVALUATION_TIME = datetime(2026, 7, 27, 12, tzinfo=timezone.utc)
SCHEMA_NAME = (
    "mmi_validated_grounded_analysis_response_v1.schema.json"
)
IDENTITY_DOMAIN = b"mmi_validated_grounded_analysis_response_v1\0"
IDENTITY_FIELD = (
    "validated_grounded_analysis_response_identity_sha256"
)
R1_IDENTITY_FIELD = "raw_response_envelope_identity_sha256"
CONTEXT_FIELD = "prompt_context_binding_sha256"
EXPECTED_FIELDS = frozenset(
    {
        "schema_version",
        "artifact_kind",
        "report_only",
        "authority_effect",
        "manual_handoff_required",
        R1_IDENTITY_FIELD,
        "response_payload",
        IDENTITY_FIELD,
    }
)
TASK_ARRAY_FIELDS = (
    "evidence_observations",
    "risks",
    "uncertainties",
    "contradictions",
    "research_questions",
)
ALWAYS_REFERENCES = frozenset(
    {
        "VIEW.EVALUATION_TIMESTAMP",
        "VIEW.COMPLETENESS_STATUS",
        "POLICY.AS_OF_DATE",
        "POLICY.METHOD",
        "POLICY.BENCHMARK.0001",
        "POLICY.EXTENDED_ACTIVATION_STATUS",
        "POLICY.INSTRUMENT_AVAILABILITY_STATUS",
        "POLICY.TARGET_WEIGHTS_ABSENCE_REASON",
        "PORTFOLIO.PRESENCE_STATUS",
    }
)
PRESENT_PORTFOLIO_REFERENCES = frozenset(
    {
        "PORTFOLIO.SOURCE_DATE",
        "PORTFOLIO.OPEN_BUY_STATUS",
        "PORTFOLIO.COVERAGE.HOLDINGS",
        "PORTFOLIO.COVERAGE.CASH",
        "PORTFOLIO.COVERAGE.DEPLOYABLE_CASH",
        "PORTFOLIO.COVERAGE.OPEN_SELLS",
        "PORTFOLIO.COVERAGE.TAX_LOTS",
        "PORTFOLIO.COVERAGE.HOLDING_DATES",
        "PORTFOLIO.COVERAGE.GAINS_LOSSES",
        "PORTFOLIO.COVERAGE.WEIGHTS",
        "PORTFOLIO.COVERAGE.NAV_CONCENTRATION",
        "PORTFOLIO.COVERAGE.LOOK_THROUGH_EXPOSURE",
    }
)
_MISSING = object()


class _FixedClock:
    def now_utc(self) -> datetime:
        return EVALUATION_TIME


class _OneSnapshotMapping(Mapping[str, object]):
    def __init__(self, value: Mapping[str, object]) -> None:
        self._value = dict(value)
        self.iterations = 0
        self.length_reads = 0
        self.lookup_counts: dict[str, int] = {}

    def __iter__(self) -> Iterator[str]:
        self.iterations += 1
        if self.iterations > 1:
            raise AssertionError("caller mapping received a second iterator")
        return iter(self._value)

    def __len__(self) -> int:
        self.length_reads += 1
        return len(self._value)

    def __getitem__(self, key: str) -> object:
        self.lookup_counts[key] = self.lookup_counts.get(key, 0) + 1
        if self.lookup_counts[key] > 1:
            raise AssertionError("caller mapping key was read twice")
        return self._value[key]

    def assert_read_once(self) -> None:
        assert self.iterations == 1
        assert self.length_reads == 0
        assert self.lookup_counts == dict.fromkeys(self._value, 1)


class _DuplicateKeyMapping(Mapping[str, object]):
    def __init__(self, value: Mapping[str, object]) -> None:
        self._value = dict(value)
        self._keys = (*self._value, next(iter(self._value)))

    def __iter__(self) -> Iterator[str]:
        return iter(self._keys)

    def __len__(self) -> int:
        return len(self._keys)

    def __getitem__(self, key: str) -> object:
        return self._value[key]


class _BytesSubclass(bytes):
    pass


@dataclass(frozen=True, slots=True)
class _Branch:
    name: str
    evidence_bundle: dict[str, object]
    portfolio_projection: dict[str, object] | None
    portfolio_source: MmiCapturedSource | None
    view: dict[str, object]
    grounded_prompt: dict[str, object]


@dataclass(frozen=True, slots=True)
class _TrustedInputs:
    policy_projection: dict[str, object]
    policy_source: MmiCapturedSource
    run_context: MmiProjectionRunContext
    branches: Mapping[str, _Branch]


def _projection(
    result: MmiPolicyProjectionBuildResult,
) -> dict[str, object]:
    assert result.valid, result.reason_codes
    assert result.projection is not None
    return deepcopy(dict(result.projection))


def _capture_current(role: MmiSourceRole) -> MmiCapturedSource:
    relative = {
        MmiSourceRole.STRATEGY_SETTINGS: (
            "inputs/current/strategy_settings.yaml"
        ),
        MmiSourceRole.PORTFOLIO_SNAPSHOT: (
            "inputs/current/portfolio_snapshot.txt"
        ),
    }[role]
    raw = (repo_root() / relative).read_bytes()
    result = capture_current_mmi_source(
        role,
        expected_source_sha256=hashlib.sha256(raw).hexdigest(),
    )
    assert result.valid, result.reason_codes
    assert result.source is not None
    return result.source


def _build_portfolio(
    source: MmiCapturedSource | None,
    *,
    policy_projection: dict[str, object],
    policy_source: MmiCapturedSource,
    run_context: MmiProjectionRunContext,
) -> dict[str, object]:
    return _projection(
        build_mmi_portfolio_snapshot_projection(
            source,
            policy_projection=deepcopy(policy_projection),
            policy_source=policy_source,
            run_context=run_context,
        )
    )


def _make_branch(
    *,
    name: str,
    policy_projection: dict[str, object],
    policy_source: MmiCapturedSource,
    portfolio_projection: dict[str, object] | None,
    portfolio_source: MmiCapturedSource | None,
    run_context: MmiProjectionRunContext,
) -> _Branch:
    evidence_bundle = _projection(
        build_mmi_authenticated_evidence_bundle(
            policy_projection=deepcopy(policy_projection),
            policy_source=policy_source,
            portfolio_projection=(
                None
                if portfolio_projection is None
                else deepcopy(portfolio_projection)
            ),
            portfolio_source=portfolio_source,
            run_context=run_context,
        )
    )
    view = _projection(
        build_mmi_analyst_visible_evidence_view(
            evidence_bundle=deepcopy(evidence_bundle),
            policy_projection=deepcopy(policy_projection),
            policy_source=policy_source,
            portfolio_projection=(
                None
                if portfolio_projection is None
                else deepcopy(portfolio_projection)
            ),
            portfolio_source=portfolio_source,
            run_context=run_context,
        )
    )
    grounded_prompt = _projection(
        build_mmi_grounded_prompt(
            analyst_visible_evidence_view=deepcopy(view),
            evidence_bundle=deepcopy(evidence_bundle),
            policy_projection=deepcopy(policy_projection),
            policy_source=policy_source,
            portfolio_projection=(
                None
                if portfolio_projection is None
                else deepcopy(portfolio_projection)
            ),
            portfolio_source=portfolio_source,
            run_context=run_context,
        )
    )
    return _Branch(
        name=name,
        evidence_bundle=evidence_bundle,
        portfolio_projection=portfolio_projection,
        portfolio_source=portfolio_source,
        view=view,
        grounded_prompt=grounded_prompt,
    )


@pytest.fixture(scope="module")
def trusted_inputs() -> _TrustedInputs:
    run_context = _begin_mmi_projection_run_with_clock(_FixedClock())
    policy_source = _capture_current(MmiSourceRole.STRATEGY_SETTINGS)
    policy_projection = _projection(
        build_mmi_policy_projection(
            policy_source,
            run_context=run_context,
        )
    )
    source_absent_portfolio = _build_portfolio(
        None,
        policy_projection=policy_projection,
        policy_source=policy_source,
        run_context=run_context,
    )
    source_bound_source = _capture_current(
        MmiSourceRole.PORTFOLIO_SNAPSHOT
    )
    source_bound_portfolio = _build_portfolio(
        source_bound_source,
        policy_projection=policy_projection,
        policy_source=policy_source,
        run_context=run_context,
    )
    branches = {
        "NOT_SUPPLIED": _make_branch(
            name="NOT_SUPPLIED",
            policy_projection=policy_projection,
            policy_source=policy_source,
            portfolio_projection=None,
            portfolio_source=None,
            run_context=run_context,
        ),
        "PRESENT_VALIDATED_SOURCE_ABSENT": _make_branch(
            name="PRESENT_VALIDATED_SOURCE_ABSENT",
            policy_projection=policy_projection,
            policy_source=policy_source,
            portfolio_projection=source_absent_portfolio,
            portfolio_source=None,
            run_context=run_context,
        ),
        "PRESENT_SOURCE_BOUND_VALIDATED": _make_branch(
            name="PRESENT_SOURCE_BOUND_VALIDATED",
            policy_projection=policy_projection,
            policy_source=policy_source,
            portfolio_projection=source_bound_portfolio,
            portfolio_source=source_bound_source,
            run_context=run_context,
        ),
    }
    return _TrustedInputs(
        policy_projection=policy_projection,
        policy_source=policy_source,
        run_context=run_context,
        branches=branches,
    )


def _branch(
    inputs: _TrustedInputs,
    name: str = "PRESENT_SOURCE_BOUND_VALIDATED",
) -> _Branch:
    return inputs.branches[name]


def _item(
    text: str = "Evidence-linked observation.",
    *,
    references: list[str] | None = None,
    hypothesis: bool = False,
) -> dict[str, object]:
    return {
        "text": text,
        "references": (
            ["VIEW.EVALUATION_TIMESTAMP"]
            if references is None
            else list(references)
        ),
        "hypothesis": hypothesis,
    }


def _payload(
    branch: _Branch,
    *,
    context: str | None = None,
    status: str = "QUALITATIVE_ANALYSIS_PROVIDED",
    summary_text: str = "Research-only synthesis.",
    summary_references: list[str] | None = None,
) -> dict[str, object]:
    trusted_context = branch.grounded_prompt[CONTEXT_FIELD]
    assert type(trusted_context) is str
    return {
        "response_schema_version": "mmi_grounded_analysis_response_v1",
        CONTEXT_FIELD: trusted_context if context is None else context,
        "analysis_status": status,
        "evidence_observations": [_item()],
        "risks": [],
        "uncertainties": [],
        "contradictions": [],
        "research_questions": [],
        "summary": _item(
            summary_text,
            references=summary_references,
        ),
    }


def _raw_json(payload: Mapping[str, object]) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _default_raw(branch: _Branch) -> bytes:
    return _raw_json(_payload(branch))


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _independent_identity(value: Mapping[str, object]) -> str:
    preimage = deepcopy(dict(value))
    preimage.pop(IDENTITY_FIELD, None)
    encoded = _canonical(preimage)
    return hashlib.sha256(
        IDENTITY_DOMAIN
        + struct.pack(">Q", len(encoded))
        + encoded
    ).hexdigest()


def _independent_artifact(
    *,
    envelope: Mapping[str, object],
    payload: dict[str, object],
) -> dict[str, object]:
    value: dict[str, object] = {
        "schema_version": (
            "mmi_validated_grounded_analysis_response_v1"
        ),
        "artifact_kind": (
            "MMI_VALIDATED_GROUNDED_ANALYSIS_RESPONSE"
        ),
        "report_only": True,
        "authority_effect": "NONE",
        "manual_handoff_required": True,
        R1_IDENTITY_FIELD: envelope[R1_IDENTITY_FIELD],
        "response_payload": deepcopy(payload),
        IDENTITY_FIELD: "0" * 64,
    }
    value[IDENTITY_FIELD] = _independent_identity(value)
    return value


def _reseal(value: dict[str, object]) -> None:
    value[IDENTITY_FIELD] = _independent_identity(value)
    assert value[IDENTITY_FIELD] == _independent_identity(value)


def _context_kwargs(
    inputs: _TrustedInputs,
    branch: _Branch,
) -> dict[str, object]:
    return {
        "grounded_prompt": deepcopy(branch.grounded_prompt),
        "analyst_visible_evidence_view": deepcopy(branch.view),
        "evidence_bundle": deepcopy(branch.evidence_bundle),
        "policy_projection": deepcopy(inputs.policy_projection),
        "policy_source": inputs.policy_source,
        "portfolio_projection": (
            None
            if branch.portfolio_projection is None
            else deepcopy(branch.portfolio_projection)
        ),
        "portfolio_source": branch.portfolio_source,
        "run_context": inputs.run_context,
    }


def _envelope(
    inputs: _TrustedInputs,
    branch: _Branch,
    raw_response_bytes: bytes,
) -> dict[str, object]:
    result = build_mmi_raw_response_envelope(
        raw_response_bytes=raw_response_bytes,
        **_context_kwargs(inputs, branch),  # type: ignore[arg-type]
    )
    assert result.valid, result.reason_codes
    assert result.projection is not None
    return deepcopy(dict(result.projection))


def _build(
    inputs: _TrustedInputs,
    *,
    branch: _Branch | None = None,
    raw_response_bytes: object = _MISSING,
    raw_response_envelope: object | None = None,
    **overrides: object,
) -> MmiPolicyProjectionBuildResult:
    selected = _branch(inputs) if branch is None else branch
    raw = (
        _default_raw(selected)
        if raw_response_bytes is _MISSING
        else raw_response_bytes
    )
    envelope = (
        _envelope(inputs, selected, raw)  # type: ignore[arg-type]
        if raw_response_envelope is None
        else raw_response_envelope
    )
    kwargs = _context_kwargs(inputs, selected)
    kwargs.update(overrides)
    return build_mmi_validated_grounded_analysis_response(
        raw_response_envelope=envelope,  # type: ignore[arg-type]
        raw_response_bytes=raw,  # type: ignore[arg-type]
        **kwargs,  # type: ignore[arg-type]
    )


def _valid_artifact(
    inputs: _TrustedInputs,
    *,
    branch: _Branch | None = None,
    raw_response_bytes: bytes | None = None,
) -> dict[str, object]:
    selected = _branch(inputs) if branch is None else branch
    raw = (
        _default_raw(selected)
        if raw_response_bytes is None
        else raw_response_bytes
    )
    result = _build(
        inputs,
        branch=selected,
        raw_response_bytes=raw,
    )
    assert result.status is (
        MmiProjectionResultCategory.PROJECTION_VALID_WITH_GAPS
    ), result.reason_codes
    assert result.authority_effect == "NONE"
    assert result.projection is not None
    return deepcopy(dict(result.projection))


def _validate(
    candidate: object,
    inputs: _TrustedInputs,
    *,
    branch: _Branch | None = None,
    raw_response_bytes: bytes | None = None,
    raw_response_envelope: object | None = None,
    **overrides: object,
) -> MmiPolicyProjectionValidationResult:
    selected = _branch(inputs) if branch is None else branch
    raw = (
        _default_raw(selected)
        if raw_response_bytes is None
        else raw_response_bytes
    )
    envelope = (
        _envelope(inputs, selected, raw)
        if raw_response_envelope is None
        else raw_response_envelope
    )
    kwargs = _context_kwargs(inputs, selected)
    kwargs.update(overrides)
    return validate_mmi_validated_grounded_analysis_response(
        value=candidate,  # type: ignore[arg-type]
        raw_response_envelope=envelope,  # type: ignore[arg-type]
        raw_response_bytes=raw,
        **kwargs,  # type: ignore[arg-type]
    )


def _assert_blocked(
    result: (
        MmiPolicyProjectionBuildResult
        | MmiPolicyProjectionValidationResult
    ),
    reason: str | None = None,
) -> None:
    assert result.status is MmiProjectionResultCategory.PROJECTION_BLOCKED
    assert result.authority_effect == "NONE"
    assert len(result.reason_codes) == 1
    if reason is not None:
        assert result.reason_codes == (reason,)
    if isinstance(result, MmiPolicyProjectionBuildResult):
        assert result.projection is None


def _assert_contract_failure(
    result: (
        MmiPolicyProjectionBuildResult
        | MmiPolicyProjectionValidationResult
    ),
    reason: str | None = None,
) -> None:
    assert result.status is (
        MmiProjectionResultCategory.PROJECTION_CONTRACT_FAILURE
    )
    assert result.authority_effect == "NONE"
    assert len(result.reason_codes) == 1
    if reason is not None:
        assert result.reason_codes == (reason,)
    if isinstance(result, MmiPolicyProjectionBuildResult):
        assert result.projection is None


def _independent_catalog(view: Mapping[str, object]) -> frozenset[str]:
    policy = view["policy_view"]
    portfolio = view["portfolio_view"]
    limitations = view["known_view_limitations"]
    assert type(policy) is dict
    assert type(portfolio) is dict
    assert type(limitations) is list
    instruments = policy["analysis_instruments"]
    assert type(instruments) is list
    allowed = set(ALWAYS_REFERENCES)
    allowed.update(
        f"POLICY.INSTRUMENT.{index:04d}"
        for index in range(1, len(instruments) + 1)
    )
    allowed.update(
        f"LIMITATION.{index:04d}"
        for index in range(1, len(limitations) + 1)
    )
    if portfolio["presence_status"] != "NOT_SUPPLIED":
        observations = portfolio["open_buy_observations"]
        assert type(observations) is list
        allowed.update(PRESENT_PORTFOLIO_REFERENCES)
        allowed.update(
            f"PORTFOLIO.OBSERVATION.{index:04d}"
            for index in range(1, len(observations) + 1)
        )
    return frozenset(allowed)


def test_public_surface_is_exact_keyword_only_and_not_reexported() -> None:
    build_names = (
        "raw_response_envelope",
        "raw_response_bytes",
        "grounded_prompt",
        "analyst_visible_evidence_view",
        "evidence_bundle",
        "policy_projection",
        "policy_source",
        "portfolio_projection",
        "portfolio_source",
        "run_context",
    )
    validate_names = ("value", *build_names)
    assert tuple(
        inspect.signature(
            build_mmi_validated_grounded_analysis_response
        ).parameters
    ) == build_names
    assert tuple(
        inspect.signature(
            validate_mmi_validated_grounded_analysis_response
        ).parameters
    ) == validate_names
    for function in (
        build_mmi_validated_grounded_analysis_response,
        validate_mmi_validated_grounded_analysis_response,
    ):
        assert all(
            parameter.kind is inspect.Parameter.KEYWORD_ONLY
            and parameter.default is inspect.Parameter.empty
            for parameter in inspect.signature(
                function
            ).parameters.values()
        )
    assert validated_grounded_analysis_response.__all__ == (
        "build_mmi_validated_grounded_analysis_response",
        "validate_mmi_validated_grounded_analysis_response",
    )
    assert tuple(
        name
        for name, value in vars(
            validated_grounded_analysis_response
        ).items()
        if inspect.isfunction(value)
        and value.__module__
        == validated_grounded_analysis_response.__name__
        and not name.startswith("_")
    ) == validated_grounded_analysis_response.__all__
    import investment_orchestrator.mmi as mmi

    assert mmi.__all__ == ()
    assert not hasattr(
        mmi,
        "build_mmi_validated_grounded_analysis_response",
    )


def test_builder_matches_independent_complete_artifact_oracle(
    trusted_inputs: _TrustedInputs,
) -> None:
    branch = _branch(trusted_inputs)
    raw = _default_raw(branch)
    envelope = _envelope(trusted_inputs, branch, raw)
    artifact = _valid_artifact(
        trusted_inputs,
        branch=branch,
        raw_response_bytes=raw,
    )
    expected = _independent_artifact(
        envelope=envelope,
        payload=_payload(branch),
    )
    assert artifact == expected
    assert set(artifact) == EXPECTED_FIELDS
    validate_artifact_schema(artifact, schema_name=SCHEMA_NAME)
    assert (
        mmi_validated_grounded_analysis_response_identity_sha256(
            artifact
        )
        == artifact[IDENTITY_FIELD]
        == _independent_identity(artifact)
    )


def test_builder_is_deterministic_and_validator_requires_complete_equality(
    trusted_inputs: _TrustedInputs,
) -> None:
    first = _valid_artifact(trusted_inputs)
    second = _valid_artifact(trusted_inputs)
    assert first == second
    result = _validate(MappingProxyType(first), trusted_inputs)
    assert result.status is (
        MmiProjectionResultCategory.PROJECTION_VALID_WITH_GAPS
    )
    assert result.reason_codes == ()
    assert result.authority_effect == "NONE"


@pytest.mark.parametrize(
    "status",
    (
        MmiProjectionResultCategory.PROJECTION_BLOCKED,
        MmiProjectionResultCategory.PROJECTION_CONTRACT_FAILURE,
    ),
    ids=("blocked", "contract-failure"),
)
def test_r1c_failures_and_reason_codes_propagate_exactly(
    trusted_inputs: _TrustedInputs,
    monkeypatch: pytest.MonkeyPatch,
    status: MmiProjectionResultCategory,
) -> None:
    upstream = MmiPolicyProjectionValidationResult(
        status=status,
        authority_effect="NONE",
        reason_codes=("MMI_RAW_RESPONSE_ENVELOPE_UPSTREAM_TEST",),
    )
    monkeypatch.setattr(
        validated_grounded_analysis_response,
        "validate_mmi_raw_response_envelope",
        lambda **_kwargs: upstream,
    )
    branch = _branch(trusted_inputs)
    raw = _default_raw(branch)
    envelope = _envelope(trusted_inputs, branch, raw)
    result = _build(
        trusted_inputs,
        branch=branch,
        raw_response_bytes=raw,
        raw_response_envelope=envelope,
    )
    assert result.status is status
    assert result.authority_effect == "NONE"
    assert result.reason_codes == upstream.reason_codes
    assert result.projection is None


def test_unexpected_upstream_success_shape_is_contract_failure(
    trusted_inputs: _TrustedInputs,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    branch = _branch(trusted_inputs)
    raw = _default_raw(branch)
    envelope = _envelope(trusted_inputs, branch, raw)
    for upstream in (
        MmiPolicyProjectionValidationResult(
            status=(
                MmiProjectionResultCategory.PROJECTION_VALID_COMPLETE
            ),
            authority_effect="NONE",
            reason_codes=(),
        ),
        MmiPolicyProjectionValidationResult(
            status=(
                MmiProjectionResultCategory.PROJECTION_VALID_WITH_GAPS
            ),
            authority_effect="ACTION",
            reason_codes=(),
        ),
    ):
        monkeypatch.setattr(
            validated_grounded_analysis_response,
            "validate_mmi_raw_response_envelope",
            lambda **_kwargs: upstream,
        )
        _assert_contract_failure(
            _build(
                trusted_inputs,
                branch=branch,
                raw_response_bytes=raw,
                raw_response_envelope=envelope,
            ),
            (
                "MMI_VALIDATED_GROUNDED_ANALYSIS_RESPONSE_"
                "UPSTREAM_RESULT_INVALID"
            ),
        )


@pytest.mark.parametrize(
    "raw_response_bytes",
    (
        None,
        "response",
        bytearray(b"response"),
        memoryview(b"response"),
        _BytesSubclass(b"response"),
        object(),
        b"",
        b"x" * 262_145,
    ),
    ids=(
        "none",
        "text",
        "bytearray",
        "memoryview",
        "bytes-subclass",
        "custom-object",
        "empty",
        "one-byte-over",
    ),
)
def test_exact_raw_byte_boundary_is_owned_and_propagated_from_r1c(
    trusted_inputs: _TrustedInputs,
    raw_response_bytes: object,
) -> None:
    branch = _branch(trusted_inputs)
    valid_raw = _default_raw(branch)
    envelope = _envelope(trusted_inputs, branch, valid_raw)
    result = _build(
        trusted_inputs,
        branch=branch,
        raw_response_bytes=raw_response_bytes,
        raw_response_envelope=envelope,
    )
    _assert_blocked(
        result,
        "MMI_RAW_RESPONSE_ENVELOPE_RAW_RESPONSE_INPUT_INVALID",
    )


@pytest.mark.parametrize(
    ("raw_response_bytes", "reason"),
    (
        (
            b"\xff",
            "MMI_VALIDATED_GROUNDED_ANALYSIS_RESPONSE_UTF8_INVALID",
        ),
        (
            b"\xef\xbb\xbf{}",
            "MMI_VALIDATED_GROUNDED_ANALYSIS_RESPONSE_UTF8_BOM_INVALID",
        ),
        (
            b"\xc2\xa0{}",
            "MMI_VALIDATED_GROUNDED_ANALYSIS_RESPONSE_JSON_INVALID",
        ),
    ),
    ids=(
        "invalid-utf8",
        "leading-bom",
        "unicode-surrounding-whitespace",
    ),
)
def test_strict_utf8_and_json_whitespace_policy(
    trusted_inputs: _TrustedInputs,
    raw_response_bytes: bytes,
    reason: str,
) -> None:
    _assert_blocked(
        _build(
            trusted_inputs,
            raw_response_bytes=raw_response_bytes,
        ),
        reason,
    )


def test_each_rfc_json_whitespace_only_response_is_blocked(
    trusted_inputs: _TrustedInputs,
) -> None:
    for raw_response_bytes in (
        b" ",
        b"\t",
        b"\r",
        b"\n",
        b" \t\r\n",
    ):
        _assert_blocked(
            _build(
                trusted_inputs,
                raw_response_bytes=raw_response_bytes,
            ),
            (
                "MMI_VALIDATED_GROUNDED_ANALYSIS_RESPONSE_"
                "EMPTY_RESPONSE_INVALID"
            ),
        )


@pytest.mark.parametrize(
    "mutation",
    (
        "markdown",
        "prefix",
        "suffix",
        "multiple-values",
        "comment",
        "duplicate-top",
        "duplicate-nested",
        "nan",
        "infinity",
        "negative-infinity",
        "unpaired-surrogate",
    ),
)
def test_malformed_or_ambiguous_json_is_blocked(
    trusted_inputs: _TrustedInputs,
    mutation: str,
) -> None:
    branch = _branch(trusted_inputs)
    raw = _default_raw(branch)
    if mutation == "markdown":
        candidate = b"```json\n" + raw + b"\n```"
    elif mutation == "prefix":
        candidate = b"Here is the response:\n" + raw
    elif mutation == "suffix":
        candidate = raw + b"\nDone."
    elif mutation == "multiple-values":
        candidate = raw + b" {}"
    elif mutation == "comment":
        candidate = b"/* response */" + raw
    elif mutation == "duplicate-top":
        candidate = raw[:-1] + (
            b',"analysis_status":"INSUFFICIENT_EVIDENCE"}'
        )
    elif mutation == "duplicate-nested":
        candidate = raw.replace(
            b'"text":"Research-only synthesis."',
            b'"text":"Research-only synthesis.","text":"duplicate"',
            1,
        )
    elif mutation in {"nan", "infinity", "negative-infinity"}:
        token = {
            "nan": b"NaN",
            "infinity": b"Infinity",
            "negative-infinity": b"-Infinity",
        }[mutation]
        candidate = raw.replace(
            b'"analysis_status":"QUALITATIVE_ANALYSIS_PROVIDED"',
            b'"analysis_status":' + token,
            1,
        )
    elif mutation == "unpaired-surrogate":
        candidate = raw.replace(
            b'"text":"Research-only synthesis."',
            b'"text":"\\ud800"',
            1,
        )
    else:
        raise AssertionError(mutation)
    _assert_blocked(
        _build(
            trusted_inputs,
            branch=branch,
            raw_response_bytes=candidate,
        ),
        "MMI_VALIDATED_GROUNDED_ANALYSIS_RESPONSE_JSON_INVALID",
    )


@pytest.mark.parametrize(
    "top_level",
    (
        b"[]",
        b'"text"',
        b"1",
        b"true",
        b"null",
    ),
    ids=("array", "string", "number", "boolean", "null"),
)
def test_non_object_top_level_is_blocked(
    trusted_inputs: _TrustedInputs,
    top_level: bytes,
) -> None:
    _assert_blocked(
        _build(
            trusted_inputs,
            raw_response_bytes=top_level,
        ),
        "MMI_VALIDATED_GROUNDED_ANALYSIS_RESPONSE_JSON_INVALID",
    )


def test_rfc_json_surrounding_whitespace_is_allowed_without_rewriting(
    trusted_inputs: _TrustedInputs,
) -> None:
    branch = _branch(trusted_inputs)
    payload = _payload(branch)
    raw = b" \t\r\n" + _raw_json(payload) + b"\n\t "
    artifact = _valid_artifact(
        trusted_inputs,
        branch=branch,
        raw_response_bytes=raw,
    )
    assert artifact["response_payload"] == payload
    envelope = _envelope(trusted_inputs, branch, raw)
    assert artifact[R1_IDENTITY_FIELD] == envelope[R1_IDENTITY_FIELD]


def test_bom_character_inside_json_string_remains_exact_data(
    trusted_inputs: _TrustedInputs,
) -> None:
    branch = _branch(trusted_inputs)
    payload = _payload(branch, summary_text="\ufeffresearch data")
    raw = _raw_json(payload)
    artifact = _valid_artifact(
        trusted_inputs,
        branch=branch,
        raw_response_bytes=raw,
    )
    assert artifact["response_payload"] == payload
    assert artifact["response_payload"]["summary"]["text"].startswith(  # type: ignore[index]
        "\ufeff"
    )


@pytest.mark.parametrize(
    "mutation",
    (
        "finite-number",
        "wrong-version",
        "malformed-context",
        "missing-reference",
        "duplicate-reference",
        "invalid-reference",
    ),
)
def test_parsed_response_contract_failures_are_blocked(
    trusted_inputs: _TrustedInputs,
    mutation: str,
) -> None:
    branch = _branch(trusted_inputs)
    payload = _payload(branch)
    if mutation == "finite-number":
        payload["analysis_status"] = 1
    elif mutation == "wrong-version":
        payload["response_schema_version"] = (
            "mmi_grounded_analysis_response_v2"
        )
    elif mutation == "malformed-context":
        payload[CONTEXT_FIELD] = "bad"
    elif mutation == "missing-reference":
        payload["summary"]["references"] = []  # type: ignore[index]
    elif mutation == "duplicate-reference":
        payload["summary"]["references"] = [  # type: ignore[index]
            "VIEW.EVALUATION_TIMESTAMP",
            "VIEW.EVALUATION_TIMESTAMP",
        ]
    elif mutation == "invalid-reference":
        payload["summary"]["references"] = [  # type: ignore[index]
            "POLICY.INSTRUMENT.0000"
        ]
    else:
        raise AssertionError(mutation)
    _assert_blocked(
        _build(
            trusted_inputs,
            branch=branch,
            raw_response_bytes=_raw_json(payload),
        ),
        (
            "MMI_VALIDATED_GROUNDED_ANALYSIS_RESPONSE_"
            "RESPONSE_SCHEMA_INVALID"
        ),
    )


def test_well_formed_context_mismatch_is_contract_failure(
    trusted_inputs: _TrustedInputs,
) -> None:
    branch = _branch(trusted_inputs)
    raw = _raw_json(_payload(branch, context="f" * 64))
    _assert_contract_failure(
        _build(
            trusted_inputs,
            branch=branch,
            raw_response_bytes=raw,
        ),
        (
            "MMI_VALIDATED_GROUNDED_ANALYSIS_RESPONSE_"
            "CONTEXT_BINDING_MISMATCH"
        ),
    )


@pytest.mark.parametrize(
    "branch_name",
    (
        "NOT_SUPPLIED",
        "PRESENT_VALIDATED_SOURCE_ABSENT",
        "PRESENT_SOURCE_BOUND_VALIDATED",
    ),
)
def test_all_portfolio_catalog_branches_build_deterministically(
    trusted_inputs: _TrustedInputs,
    branch_name: str,
) -> None:
    branch = _branch(trusted_inputs, branch_name)
    reference = (
        "PORTFOLIO.PRESENCE_STATUS"
        if branch_name == "NOT_SUPPLIED"
        else "PORTFOLIO.SOURCE_DATE"
    )
    raw = _raw_json(
        _payload(branch, summary_references=[reference])
    )
    first = _valid_artifact(
        trusted_inputs,
        branch=branch,
        raw_response_bytes=raw,
    )
    second = _valid_artifact(
        trusted_inputs,
        branch=branch,
        raw_response_bytes=raw,
    )
    assert first == second
    assert _validate(
        first,
        trusted_inputs,
        branch=branch,
        raw_response_bytes=raw,
    ).valid


def test_not_supplied_allows_only_portfolio_presence_reference(
    trusted_inputs: _TrustedInputs,
) -> None:
    branch = _branch(trusted_inputs, "NOT_SUPPLIED")
    raw = _raw_json(
        _payload(
            branch,
            summary_references=["PORTFOLIO.SOURCE_DATE"],
        )
    )
    _assert_contract_failure(
        _build(
            trusted_inputs,
            branch=branch,
            raw_response_bytes=raw,
        ),
        (
            "MMI_VALIDATED_GROUNDED_ANALYSIS_RESPONSE_"
            "REFERENCE_MEMBERSHIP_MISMATCH"
        ),
    )


def test_present_null_source_date_is_referenceable(
    trusted_inputs: _TrustedInputs,
) -> None:
    branch = _branch(
        trusted_inputs,
        "PRESENT_VALIDATED_SOURCE_ABSENT",
    )
    portfolio = branch.view["portfolio_view"]
    assert type(portfolio) is dict
    assert portfolio["portfolio_source_date"] is None
    raw = _raw_json(
        _payload(
            branch,
            summary_references=["PORTFOLIO.SOURCE_DATE"],
        )
    )
    assert _build(
        trusted_inputs,
        branch=branch,
        raw_response_bytes=raw,
    ).valid


@pytest.mark.parametrize(
    "namespace",
    ("policy", "portfolio", "limitation"),
)
def test_only_actual_source_bound_numbered_positions_are_allowed(
    trusted_inputs: _TrustedInputs,
    namespace: str,
) -> None:
    branch = _branch(trusted_inputs)
    policy = branch.view["policy_view"]
    portfolio = branch.view["portfolio_view"]
    limitations = branch.view["known_view_limitations"]
    assert type(policy) is dict
    assert type(portfolio) is dict
    assert type(limitations) is list
    if namespace == "policy":
        values = policy["analysis_instruments"]
        prefix = "POLICY.INSTRUMENT"
        global_maximum = 256
    elif namespace == "portfolio":
        values = portfolio["open_buy_observations"]
        prefix = "PORTFOLIO.OBSERVATION"
        global_maximum = 256
    else:
        values = limitations
        prefix = "LIMITATION"
        global_maximum = 14
    assert type(values) is list
    present = f"{prefix}.{len(values):04d}"
    assert _build(
        trusted_inputs,
        branch=branch,
        raw_response_bytes=_raw_json(
            _payload(branch, summary_references=[present])
        ),
    ).valid
    assert len(values) < global_maximum
    absent = f"{prefix}.{len(values) + 1:04d}"
    _assert_contract_failure(
        _build(
            trusted_inputs,
            branch=branch,
            raw_response_bytes=_raw_json(
                _payload(branch, summary_references=[absent])
            ),
        ),
        (
            "MMI_VALIDATED_GROUNDED_ANALYSIS_RESPONSE_"
            "REFERENCE_MEMBERSHIP_MISMATCH"
        ),
    )


def test_independent_catalog_matches_all_visible_reference_positions(
    trusted_inputs: _TrustedInputs,
) -> None:
    branch = _branch(trusted_inputs)
    catalog = _independent_catalog(branch.view)
    assert ALWAYS_REFERENCES <= catalog
    chunks = [sorted(catalog)[index : index + 8] for index in range(0, len(catalog), 8)]
    payload = _payload(branch)
    payload["evidence_observations"] = [
        _item(f"Catalog group {index}.", references=references)
        for index, references in enumerate(chunks, 1)
    ]
    assert len(payload["evidence_observations"]) <= 12
    raw = _raw_json(payload)
    artifact = _valid_artifact(
        trusted_inputs,
        branch=branch,
        raw_response_bytes=raw,
    )
    assert "reference_catalog" not in artifact
    assert "allowed_references" not in artifact


def test_reference_and_array_order_are_preserved(
    trusted_inputs: _TrustedInputs,
) -> None:
    branch = _branch(trusted_inputs)
    payload = _payload(branch)
    payload["evidence_observations"] = [
        _item(
            "First.",
            references=[
                "POLICY.METHOD",
                "VIEW.EVALUATION_TIMESTAMP",
            ],
        ),
        _item("Second.", references=["POLICY.AS_OF_DATE"]),
    ]
    raw = _raw_json(payload)
    artifact = _valid_artifact(
        trusted_inputs,
        branch=branch,
        raw_response_bytes=raw,
    )
    assert artifact["response_payload"] == payload


def test_each_analysis_status_remains_inert_payload_content(
    trusted_inputs: _TrustedInputs,
) -> None:
    branch = _branch(trusted_inputs)
    for status in (
        "QUALITATIVE_ANALYSIS_PROVIDED",
        "INSUFFICIENT_EVIDENCE",
        "EVIDENCE_CONTRADICTIONS_IDENTIFIED",
    ):
        raw = _raw_json(_payload(branch, status=status))
        result = _build(
            trusted_inputs,
            branch=branch,
            raw_response_bytes=raw,
        )
        assert result.status is (
            MmiProjectionResultCategory.PROJECTION_VALID_WITH_GAPS
        )
        assert result.authority_effect == "NONE"


def test_validator_uses_builder_classification_for_authoritative_content(
    trusted_inputs: _TrustedInputs,
) -> None:
    valid_candidate = _valid_artifact(trusted_inputs)
    branch = _branch(trusted_inputs)
    for raw, expected_status in (
        (
            b"\xff",
            MmiProjectionResultCategory.PROJECTION_BLOCKED,
        ),
        (
            _raw_json(_payload(branch, context="f" * 64)),
            MmiProjectionResultCategory.PROJECTION_CONTRACT_FAILURE,
        ),
    ):
        envelope = _envelope(trusted_inputs, branch, raw)
        result = _validate(
            valid_candidate,
            trusted_inputs,
            branch=branch,
            raw_response_bytes=raw,
            raw_response_envelope=envelope,
        )
        assert result.status is expected_status
        assert result.authority_effect == "NONE"
        assert len(result.reason_codes) == 1


def test_candidate_schema_invalidity_is_blocked(
    trusted_inputs: _TrustedInputs,
) -> None:
    candidate = _valid_artifact(trusted_inputs)
    candidate.pop("response_payload")
    _assert_blocked(
        _validate(candidate, trusted_inputs),
        (
            "MMI_VALIDATED_GROUNDED_ANALYSIS_RESPONSE_"
            "CANDIDATE_SCHEMA_INVALID"
        ),
    )


def test_candidate_identity_contradiction_is_contract_failure(
    trusted_inputs: _TrustedInputs,
) -> None:
    candidate = _valid_artifact(trusted_inputs)
    candidate[IDENTITY_FIELD] = "f" * 64
    _assert_contract_failure(
        _validate(candidate, trusted_inputs),
        (
            "MMI_VALIDATED_GROUNDED_ANALYSIS_RESPONSE_"
            "IDENTITY_CONTRADICTION"
        ),
    )


def test_resealed_alternate_r1_association_fails_complete_equality(
    trusted_inputs: _TrustedInputs,
) -> None:
    candidate = _valid_artifact(trusted_inputs)
    candidate[R1_IDENTITY_FIELD] = "f" * 64
    _reseal(candidate)
    assert (
        mmi_validated_grounded_analysis_response_identity_sha256(
            candidate
        )
        == candidate[IDENTITY_FIELD]
    )
    _assert_contract_failure(
        _validate(candidate, trusted_inputs),
        (
            "MMI_VALIDATED_GROUNDED_ANALYSIS_RESPONSE_"
            "SOURCE_FIDELITY_MISMATCH"
        ),
    )


@pytest.mark.parametrize(
    "mutation",
    ("context", "qualitative-text"),
)
def test_resealed_alternate_payload_fails_complete_equality(
    trusted_inputs: _TrustedInputs,
    mutation: str,
) -> None:
    candidate = _valid_artifact(trusted_inputs)
    payload = candidate["response_payload"]
    assert type(payload) is dict
    if mutation == "context":
        payload[CONTEXT_FIELD] = "f" * 64
    else:
        summary = payload["summary"]
        assert type(summary) is dict
        summary["text"] = "Different but structurally valid prose."
    _reseal(candidate)
    assert (
        mmi_validated_grounded_analysis_response_identity_sha256(
            candidate
        )
        == candidate[IDENTITY_FIELD]
    )
    _assert_contract_failure(
        _validate(candidate, trusted_inputs),
        (
            "MMI_VALIDATED_GROUNDED_ANALYSIS_RESPONSE_"
            "SOURCE_FIDELITY_MISMATCH"
        ),
    )


def test_resealed_candidate_with_static_reference_defect_is_blocked(
    trusted_inputs: _TrustedInputs,
) -> None:
    candidate = _valid_artifact(trusted_inputs)
    payload = candidate["response_payload"]
    assert type(payload) is dict
    summary = payload["summary"]
    assert type(summary) is dict
    summary["references"] = ["POLICY.INSTRUMENT.0000"]
    _reseal(candidate)
    _assert_blocked(_validate(candidate, trusted_inputs))


def test_each_boundary_mapping_is_detached_once(
    trusted_inputs: _TrustedInputs,
) -> None:
    branch = _branch(trusted_inputs)
    raw = _default_raw(branch)
    envelope = _OneSnapshotMapping(
        _envelope(trusted_inputs, branch, raw)
    )
    prompt = _OneSnapshotMapping(branch.grounded_prompt)
    view = _OneSnapshotMapping(branch.view)
    evidence = _OneSnapshotMapping(branch.evidence_bundle)
    policy = _OneSnapshotMapping(trusted_inputs.policy_projection)
    portfolio = _OneSnapshotMapping(
        branch.portfolio_projection  # type: ignore[arg-type]
    )
    result = _build(
        trusted_inputs,
        branch=branch,
        raw_response_bytes=raw,
        raw_response_envelope=envelope,
        grounded_prompt=prompt,
        analyst_visible_evidence_view=view,
        evidence_bundle=evidence,
        policy_projection=policy,
        portfolio_projection=portfolio,
    )
    assert result.valid, result.reason_codes
    for supplied in (
        envelope,
        prompt,
        view,
        evidence,
        policy,
        portfolio,
    ):
        supplied.assert_read_once()


def test_candidate_mapping_is_detached_once(
    trusted_inputs: _TrustedInputs,
) -> None:
    candidate = _OneSnapshotMapping(_valid_artifact(trusted_inputs))
    result = _validate(candidate, trusted_inputs)
    assert result.valid, result.reason_codes
    candidate.assert_read_once()


def test_r1_envelope_mutation_after_snapshot_cannot_change_association(
    trusted_inputs: _TrustedInputs,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    branch = _branch(trusted_inputs)
    raw = _default_raw(branch)
    caller_envelope = _envelope(trusted_inputs, branch, raw)
    original_identity = caller_envelope[R1_IDENTITY_FIELD]
    original_validate = (
        validated_grounded_analysis_response
        .validate_mmi_raw_response_envelope
    )

    def validate_then_mutate(**kwargs: object):
        result = original_validate(**kwargs)  # type: ignore[arg-type]
        caller_envelope[R1_IDENTITY_FIELD] = "f" * 64
        return result

    monkeypatch.setattr(
        validated_grounded_analysis_response,
        "validate_mmi_raw_response_envelope",
        validate_then_mutate,
    )
    result = _build(
        trusted_inputs,
        branch=branch,
        raw_response_bytes=raw,
        raw_response_envelope=caller_envelope,
    )
    assert result.valid, result.reason_codes
    assert result.projection is not None
    assert result.projection[R1_IDENTITY_FIELD] == original_identity


def test_prompt_context_mutation_after_snapshot_cannot_change_correlation(
    trusted_inputs: _TrustedInputs,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    branch = _branch(trusted_inputs)
    raw = _default_raw(branch)
    caller_prompt = deepcopy(branch.grounded_prompt)
    envelope = _envelope(trusted_inputs, branch, raw)
    original_validate = (
        validated_grounded_analysis_response
        .validate_mmi_raw_response_envelope
    )

    def validate_then_mutate(**kwargs: object):
        result = original_validate(**kwargs)  # type: ignore[arg-type]
        caller_prompt[CONTEXT_FIELD] = "f" * 64
        return result

    monkeypatch.setattr(
        validated_grounded_analysis_response,
        "validate_mmi_raw_response_envelope",
        validate_then_mutate,
    )
    result = _build(
        trusted_inputs,
        branch=branch,
        raw_response_bytes=raw,
        raw_response_envelope=envelope,
        grounded_prompt=caller_prompt,
    )
    assert result.valid, result.reason_codes
    assert caller_prompt[CONTEXT_FIELD] == "f" * 64


def test_v1_mutation_after_snapshot_cannot_expand_reference_catalog(
    trusted_inputs: _TrustedInputs,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    branch = _branch(trusted_inputs)
    policy = branch.view["policy_view"]
    assert type(policy) is dict
    instruments = policy["analysis_instruments"]
    assert type(instruments) is list
    absent = f"POLICY.INSTRUMENT.{len(instruments) + 1:04d}"
    raw = _raw_json(
        _payload(branch, summary_references=[absent])
    )
    envelope = _envelope(trusted_inputs, branch, raw)
    caller_view = deepcopy(branch.view)
    original_validate = (
        validated_grounded_analysis_response
        .validate_mmi_raw_response_envelope
    )

    def validate_then_mutate(**kwargs: object):
        result = original_validate(**kwargs)  # type: ignore[arg-type]
        caller_policy = caller_view["policy_view"]
        assert type(caller_policy) is dict
        caller_instruments = caller_policy["analysis_instruments"]
        assert type(caller_instruments) is list
        caller_instruments.append(
            {"ticker": "MUTATED", "policy_role": "CORE"}
        )
        return result

    monkeypatch.setattr(
        validated_grounded_analysis_response,
        "validate_mmi_raw_response_envelope",
        validate_then_mutate,
    )
    _assert_contract_failure(
        _build(
            trusted_inputs,
            branch=branch,
            raw_response_bytes=raw,
            raw_response_envelope=envelope,
            analyst_visible_evidence_view=caller_view,
        ),
        (
            "MMI_VALIDATED_GROUNDED_ANALYSIS_RESPONSE_"
            "REFERENCE_MEMBERSHIP_MISMATCH"
        ),
    )


def test_candidate_mutation_after_snapshot_cannot_change_validation(
    trusted_inputs: _TrustedInputs,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = _valid_artifact(trusted_inputs)
    original_validate = (
        validated_grounded_analysis_response
        .validate_mmi_raw_response_envelope
    )

    def validate_then_mutate(**kwargs: object):
        result = original_validate(**kwargs)  # type: ignore[arg-type]
        candidate[IDENTITY_FIELD] = "f" * 64
        return result

    monkeypatch.setattr(
        validated_grounded_analysis_response,
        "validate_mmi_raw_response_envelope",
        validate_then_mutate,
    )
    result = _validate(candidate, trusted_inputs)
    assert result.valid, result.reason_codes
    assert candidate[IDENTITY_FIELD] == "f" * 64


@pytest.mark.parametrize(
    "location",
    ("envelope-cycle", "context-set", "candidate-cycle"),
)
def test_cycles_and_unsupported_mappings_fail_closed(
    trusted_inputs: _TrustedInputs,
    location: str,
) -> None:
    branch = _branch(trusted_inputs)
    if location == "envelope-cycle":
        envelope = _envelope(
            trusted_inputs,
            branch,
            _default_raw(branch),
        )
        cycle: list[object] = []
        cycle.append(cycle)
        envelope["cycle"] = cycle
        _assert_blocked(
            _build(
                trusted_inputs,
                raw_response_envelope=envelope,
            ),
            (
                "MMI_VALIDATED_GROUNDED_ANALYSIS_RESPONSE_"
                "INPUT_SNAPSHOT_INVALID"
            ),
        )
    elif location == "context-set":
        evidence = deepcopy(branch.evidence_bundle)
        evidence["unsupported"] = {"value"}
        _assert_blocked(
            _build(
                trusted_inputs,
                evidence_bundle=evidence,
            ),
            (
                "MMI_VALIDATED_GROUNDED_ANALYSIS_RESPONSE_"
                "INPUT_SNAPSHOT_INVALID"
            ),
        )
    elif location == "candidate-cycle":
        candidate = _valid_artifact(trusted_inputs)
        cycle = []
        cycle.append(cycle)
        candidate["cycle"] = cycle
        _assert_blocked(
            _validate(candidate, trusted_inputs),
            (
                "MMI_VALIDATED_GROUNDED_ANALYSIS_RESPONSE_"
                "CANDIDATE_SCHEMA_INVALID"
            ),
        )
    else:
        raise AssertionError(location)


@pytest.mark.parametrize(
    "location",
    ("upstream", "candidate"),
)
def test_duplicate_mapping_keys_fail_before_validation(
    trusted_inputs: _TrustedInputs,
    location: str,
) -> None:
    if location == "upstream":
        branch = _branch(trusted_inputs)
        envelope = _DuplicateKeyMapping(
            _envelope(
                trusted_inputs,
                branch,
                _default_raw(branch),
            )
        )
        _assert_blocked(
            _build(
                trusted_inputs,
                raw_response_envelope=envelope,
            ),
            (
                "MMI_VALIDATED_GROUNDED_ANALYSIS_RESPONSE_"
                "INPUT_SNAPSHOT_INVALID"
            ),
        )
    else:
        _assert_blocked(
            _validate(
                _DuplicateKeyMapping(
                    _valid_artifact(trusted_inputs)
                ),
                trusted_inputs,
            ),
            (
                "MMI_VALIDATED_GROUNDED_ANALYSIS_RESPONSE_"
                "CANDIDATE_SCHEMA_INVALID"
            ),
        )


def test_source_bound_validator_receives_exact_detached_snapshots(
    trusted_inputs: _TrustedInputs,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    branch = _branch(trusted_inputs)
    raw = _default_raw(branch)
    caller_envelope = _envelope(trusted_inputs, branch, raw)
    caller_prompt = deepcopy(branch.grounded_prompt)
    caller_view = deepcopy(branch.view)
    observed: list[dict[str, object]] = []
    original_validate = (
        validated_grounded_analysis_response
        .validate_mmi_raw_response_envelope
    )

    def observe(**kwargs: object):
        observed.append(kwargs)
        return original_validate(**kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(
        validated_grounded_analysis_response,
        "validate_mmi_raw_response_envelope",
        observe,
    )
    result = _build(
        trusted_inputs,
        branch=branch,
        raw_response_bytes=raw,
        raw_response_envelope=caller_envelope,
        grounded_prompt=caller_prompt,
        analyst_visible_evidence_view=caller_view,
    )
    assert result.valid, result.reason_codes
    assert len(observed) == 1
    call = observed[0]
    assert set(call) == {
        "value",
        "raw_response_bytes",
        "grounded_prompt",
        "analyst_visible_evidence_view",
        "evidence_bundle",
        "policy_projection",
        "policy_source",
        "portfolio_projection",
        "portfolio_source",
        "run_context",
    }
    assert call["value"] == caller_envelope
    assert call["value"] is not caller_envelope
    assert call["raw_response_bytes"] is raw
    assert call["grounded_prompt"] == caller_prompt
    assert call["grounded_prompt"] is not caller_prompt
    assert call["analyst_visible_evidence_view"] == caller_view
    assert call["analyst_visible_evidence_view"] is not caller_view


def test_derived_schema_failure_is_contract_failure(
    trusted_inputs: _TrustedInputs,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_schema(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("private")

    monkeypatch.setattr(
        validated_grounded_analysis_response,
        "validate_artifact_schema",
        fail_schema,
    )
    _assert_contract_failure(
        _build(trusted_inputs),
        (
            "MMI_VALIDATED_GROUNDED_ANALYSIS_RESPONSE_"
            "DERIVED_SCHEMA_INVALID"
        ),
    )


def test_derived_identity_failure_is_contract_failure(
    trusted_inputs: _TrustedInputs,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_identity(*_args: object, **_kwargs: object) -> str:
        raise MmiCanonicalizationError("PRIVATE")

    monkeypatch.setattr(
        validated_grounded_analysis_response,
        "record_identity_sha256",
        fail_identity,
    )
    _assert_contract_failure(
        _build(trusted_inputs),
        (
            "MMI_VALIDATED_GROUNDED_ANALYSIS_RESPONSE_"
            "DERIVED_CONTRACT_INVALID"
        ),
    )


def test_action_shaped_response_prose_remains_inert(
    trusted_inputs: _TrustedInputs,
) -> None:
    branch = _branch(trusted_inputs)
    prose = (
        "BUY SELL HOLD NO_TRADE NEW_BUY ORDER_COMPILATION "
        "permission gate budget quantity order execution"
    )
    raw = _raw_json(_payload(branch, summary_text=prose))
    artifact = _valid_artifact(
        trusted_inputs,
        branch=branch,
        raw_response_bytes=raw,
    )
    assert artifact["response_payload"]["summary"]["text"] == prose  # type: ignore[index]
    assert artifact["report_only"] is True
    assert artifact["authority_effect"] == "NONE"
    assert artifact["manual_handoff_required"] is True


def test_reason_codes_never_expose_response_or_identifiers(
    trusted_inputs: _TrustedInputs,
) -> None:
    branch = _branch(trusted_inputs)
    private_text = "PRIVATE RESPONSE BUY"
    private_reference = "POLICY.INSTRUMENT.0256"
    raw = _raw_json(
        _payload(
            branch,
            summary_text=private_text,
            summary_references=[private_reference],
        )
    )
    result = _build(
        trusted_inputs,
        branch=branch,
        raw_response_bytes=raw,
    )
    _assert_contract_failure(result)
    diagnostics = repr(result.reason_codes)
    for forbidden in (
        private_text,
        private_reference,
        branch.grounded_prompt[CONTEXT_FIELD],
    ):
        assert forbidden not in diagnostics
    assert "/" not in diagnostics and "\\" not in diagnostics


def test_module_has_no_interpreter_writer_transport_or_authority_surface() -> None:
    path = (
        repo_root()
        / "src/investment_orchestrator/mmi/"
        "validated_grounded_analysis_response.py"
    )
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and node.module is not None
    } | {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    assert not {
        "os",
        "pathlib",
        "socket",
        "subprocess",
        "requests",
        "httpx",
        "openai",
        "anthropic",
        "codecs",
        "yaml",
    } & imported
    calls = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
    }
    assert not {
        "open",
        "read_bytes",
        "read_text",
        "write_bytes",
        "write_text",
    } & calls
    assert source.count("validate_mmi_raw_response_envelope(") == 1
    assert "validate_mmi_grounded_prompt" not in source
    assert "validate_mmi_analyst_visible_evidence_view" not in source
    assert "build_mmi_" not in source.replace(
        "build_mmi_validated_grounded_analysis_response",
        "",
    )


def test_r2c_has_exact_phase_ownership_and_no_consumer() -> None:
    root = repo_root()
    production_paths = tuple(
        sorted((root / "src/investment_orchestrator").rglob("*.py"))
    )
    assert len(production_paths) == 135
    relative = {
        path: path.relative_to(root).as_posix()
        for path in production_paths
    }
    trees = {
        path: ast.parse(path.read_text(encoding="utf-8"))
        for path in production_paths
    }
    raw_module = "investment_orchestrator.mmi.raw_response_envelope"
    response_module = (
        "investment_orchestrator.mmi."
        "validated_grounded_analysis_response"
    )
    response_relative = (
        "src/investment_orchestrator/mmi/"
        "validated_grounded_analysis_response.py"
    )

    def importers(module: str) -> tuple[str, ...]:
        return tuple(
            relative[path]
            for path, tree in trees.items()
            if any(
                isinstance(node, ast.ImportFrom)
                and node.module == module
                for node in ast.walk(tree)
            )
        )

    assert importers(raw_module) == (response_relative,)
    assert importers(response_module) == ()
    assert (root / response_relative).is_file()
    assert (
        root / "src/investment_orchestrator/mmi/__init__.py"
    ).read_text(encoding="utf-8") == (
        '"""Manual-model-interface report-only deterministic '
        'projection contracts."""\n\n__all__ = ()\n'
    )
