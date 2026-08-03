from __future__ import annotations

import ast
import base64
from collections.abc import Iterator, Mapping
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import inspect
import json
from pathlib import Path
import struct
from types import MappingProxyType

import pytest

from investment_orchestrator.common.paths import repo_root
from investment_orchestrator.common.schema_validation import (
    validate_artifact_schema,
)
from investment_orchestrator.mmi import (
    canonical,
    raw_response_envelope,
)
from investment_orchestrator.mmi.analyst_visible_evidence_view import (
    build_mmi_analyst_visible_evidence_view,
)
from investment_orchestrator.mmi.contracts import (
    MAXIMUM_MMI_RAW_RESPONSE_BYTES,
    MMI_RAW_RESPONSE_ENVELOPE_ARTIFACT_KIND,
    MMI_RAW_RESPONSE_ENVELOPE_SCHEMA_VERSION,
    MmiCapturedSource,
    MmiPolicyProjectionBuildResult,
    MmiPolicyProjectionValidationResult,
    MmiProjectionResultCategory,
    MmiProjectionRunContext,
    MmiSourceRole,
    _begin_mmi_projection_run_with_clock,
    mmi_raw_response_envelope_identity_sha256,
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
    validate_mmi_raw_response_envelope,
)
from investment_orchestrator.mmi.source_capture import (
    capture_current_mmi_source,
)


SCHEMA_NAME = "mmi_raw_response_envelope_v1.schema.json"
IDENTITY_DOMAIN = b"mmi_raw_response_envelope_v1\0"
IDENTITY_FIELD = "raw_response_envelope_identity_sha256"
PROMPT_IDENTITY_FIELD = "grounded_prompt_artifact_identity_sha256"
RAW_LENGTH_FIELD = "raw_response_byte_length"
RAW_DIGEST_FIELD = "raw_response_sha256"
RAW_BASE64_FIELD = "raw_response_base64"
RAW_BYTES = b'{"analysis":"manual response"}\r\n'
EVALUATION_TIME = datetime(2026, 7, 27, 12, tzinfo=timezone.utc)
EXPECTED_FIELDS = frozenset(
    {
        "schema_version",
        "artifact_kind",
        "report_only",
        "authority_effect",
        "manual_handoff_required",
        PROMPT_IDENTITY_FIELD,
        RAW_LENGTH_FIELD,
        RAW_DIGEST_FIELD,
        RAW_BASE64_FIELD,
        IDENTITY_FIELD,
    }
)


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
class _TrustedInputs:
    grounded_prompt: dict[str, object]
    view: dict[str, object]
    evidence_bundle: dict[str, object]
    policy_projection: dict[str, object]
    policy_source: MmiCapturedSource
    portfolio_projection: dict[str, object]
    portfolio_source: MmiCapturedSource
    run_context: MmiProjectionRunContext


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
    portfolio_source = _capture_current(
        MmiSourceRole.PORTFOLIO_SNAPSHOT
    )
    portfolio_projection = _projection(
        build_mmi_portfolio_snapshot_projection(
            portfolio_source,
            policy_projection=deepcopy(policy_projection),
            policy_source=policy_source,
            run_context=run_context,
        )
    )
    evidence_bundle = _projection(
        build_mmi_authenticated_evidence_bundle(
            policy_projection=deepcopy(policy_projection),
            policy_source=policy_source,
            portfolio_projection=deepcopy(portfolio_projection),
            portfolio_source=portfolio_source,
            run_context=run_context,
        )
    )
    view = _projection(
        build_mmi_analyst_visible_evidence_view(
            evidence_bundle=deepcopy(evidence_bundle),
            policy_projection=deepcopy(policy_projection),
            policy_source=policy_source,
            portfolio_projection=deepcopy(portfolio_projection),
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
            portfolio_projection=deepcopy(portfolio_projection),
            portfolio_source=portfolio_source,
            run_context=run_context,
        )
    )
    return _TrustedInputs(
        grounded_prompt=grounded_prompt,
        view=view,
        evidence_bundle=evidence_bundle,
        policy_projection=policy_projection,
        policy_source=policy_source,
        portfolio_projection=portfolio_projection,
        portfolio_source=portfolio_source,
        run_context=run_context,
    )


def _build_kwargs(
    inputs: _TrustedInputs,
) -> dict[str, object]:
    return {
        "grounded_prompt": deepcopy(inputs.grounded_prompt),
        "raw_response_bytes": RAW_BYTES,
        "analyst_visible_evidence_view": deepcopy(inputs.view),
        "evidence_bundle": deepcopy(inputs.evidence_bundle),
        "policy_projection": deepcopy(inputs.policy_projection),
        "policy_source": inputs.policy_source,
        "portfolio_projection": deepcopy(
            inputs.portfolio_projection
        ),
        "portfolio_source": inputs.portfolio_source,
        "run_context": inputs.run_context,
    }


def _build(
    inputs: _TrustedInputs,
    **overrides: object,
) -> MmiPolicyProjectionBuildResult:
    kwargs = _build_kwargs(inputs)
    kwargs.update(overrides)
    return build_mmi_raw_response_envelope(  # type: ignore[arg-type]
        **kwargs
    )


def _valid_envelope(
    inputs: _TrustedInputs,
    *,
    raw_response_bytes: bytes = RAW_BYTES,
) -> dict[str, object]:
    result = _build(
        inputs,
        raw_response_bytes=raw_response_bytes,
    )
    assert result.status is (
        MmiProjectionResultCategory.PROJECTION_VALID_WITH_GAPS
    )
    assert result.authority_effect == "NONE"
    assert result.projection is not None
    return deepcopy(dict(result.projection))


def _validate(
    candidate: object,
    inputs: _TrustedInputs,
    **overrides: object,
) -> MmiPolicyProjectionValidationResult:
    kwargs = _build_kwargs(inputs)
    kwargs["value"] = candidate
    kwargs.update(overrides)
    return validate_mmi_raw_response_envelope(  # type: ignore[arg-type]
        **kwargs
    )


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


def _independent_envelope(
    grounded_prompt: Mapping[str, object],
    raw_response_bytes: bytes,
) -> dict[str, object]:
    value: dict[str, object] = {
        "schema_version": "mmi_raw_response_envelope_v1",
        "artifact_kind": "MMI_RAW_RESPONSE_ENVELOPE",
        "report_only": True,
        "authority_effect": "NONE",
        "manual_handoff_required": True,
        PROMPT_IDENTITY_FIELD: grounded_prompt[
            PROMPT_IDENTITY_FIELD
        ],
        RAW_LENGTH_FIELD: len(raw_response_bytes),
        RAW_DIGEST_FIELD: hashlib.sha256(
            raw_response_bytes
        ).hexdigest(),
        RAW_BASE64_FIELD: base64.b64encode(
            raw_response_bytes
        ).decode("ascii"),
        IDENTITY_FIELD: "0" * 64,
    }
    value[IDENTITY_FIELD] = _independent_identity(value)
    return value


def _reseal(value: dict[str, object]) -> None:
    value[IDENTITY_FIELD] = _independent_identity(value)
    assert value[IDENTITY_FIELD] == _independent_identity(value)


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


def test_public_surface_is_exact_keyword_only_and_not_reexported() -> None:
    build_names = (
        "grounded_prompt",
        "raw_response_bytes",
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
            build_mmi_raw_response_envelope
        ).parameters
    ) == build_names
    assert tuple(
        inspect.signature(
            validate_mmi_raw_response_envelope
        ).parameters
    ) == validate_names
    for function in (
        build_mmi_raw_response_envelope,
        validate_mmi_raw_response_envelope,
    ):
        assert all(
            parameter.kind is inspect.Parameter.KEYWORD_ONLY
            and parameter.default is inspect.Parameter.empty
            for parameter in inspect.signature(
                function
            ).parameters.values()
        )
    assert raw_response_envelope.__all__ == (
        "build_mmi_raw_response_envelope",
        "validate_mmi_raw_response_envelope",
    )
    assert tuple(
        name
        for name, value in vars(raw_response_envelope).items()
        if inspect.isfunction(value)
        and value.__module__ == raw_response_envelope.__name__
        and not name.startswith("_")
    ) == raw_response_envelope.__all__
    import investment_orchestrator.mmi as mmi

    assert mmi.__all__ == ()
    assert not hasattr(mmi, "build_mmi_raw_response_envelope")


def test_builder_matches_independent_complete_envelope_oracle(
    trusted_inputs: _TrustedInputs,
) -> None:
    artifact = _valid_envelope(trusted_inputs)
    expected = _independent_envelope(
        trusted_inputs.grounded_prompt,
        RAW_BYTES,
    )
    assert artifact == expected
    assert set(artifact) == EXPECTED_FIELDS
    assert MMI_RAW_RESPONSE_ENVELOPE_SCHEMA_VERSION == (
        "mmi_raw_response_envelope_v1"
    )
    assert MMI_RAW_RESPONSE_ENVELOPE_ARTIFACT_KIND == (
        "MMI_RAW_RESPONSE_ENVELOPE"
    )
    validate_artifact_schema(artifact, schema_name=SCHEMA_NAME)
    assert (
        mmi_raw_response_envelope_identity_sha256(artifact)
        == artifact[IDENTITY_FIELD]
        == _independent_identity(artifact)
    )


def test_builder_is_deterministic_and_validator_requires_complete_equality(
    trusted_inputs: _TrustedInputs,
) -> None:
    first = _valid_envelope(trusted_inputs)
    second = _valid_envelope(trusted_inputs)
    assert first == second
    result = _validate(
        MappingProxyType(first),
        trusted_inputs,
    )
    assert result.status is (
        MmiProjectionResultCategory.PROJECTION_VALID_WITH_GAPS
    )
    assert result.authority_effect == "NONE"
    assert result.reason_codes == ()


@pytest.mark.parametrize(
    "raw_response_bytes",
    (
        b"x",
        b"\xff\xfe\x80not-utf8",
        b"\xef\xbb\xbf\x00\r\n\t\x01\x1f\x7f",
        bytes(range(256)),
    ),
    ids=("one-byte", "non-utf8", "bom-nul-controls", "all-bytes"),
)
def test_exact_arbitrary_bytes_round_trip_without_transformation(
    trusted_inputs: _TrustedInputs,
    raw_response_bytes: bytes,
) -> None:
    artifact = _valid_envelope(
        trusted_inputs,
        raw_response_bytes=raw_response_bytes,
    )
    encoded = artifact[RAW_BASE64_FIELD]
    assert type(encoded) is str
    decoded = base64.b64decode(
        encoded.encode("ascii"),
        validate=True,
    )
    assert decoded == raw_response_bytes
    assert artifact[RAW_LENGTH_FIELD] == len(raw_response_bytes)
    assert artifact[RAW_DIGEST_FIELD] == hashlib.sha256(
        raw_response_bytes
    ).hexdigest()
    assert encoded == base64.b64encode(
        raw_response_bytes
    ).decode("ascii")


@pytest.mark.parametrize(
    "raw_response_bytes",
    (
        None,
        "response",
        bytearray(b"response"),
        memoryview(b"response"),
        _BytesSubclass(b"response"),
        1,
    ),
    ids=(
        "none",
        "text",
        "bytearray",
        "memoryview",
        "bytes-subclass",
        "integer",
    ),
)
def test_wrong_or_missing_raw_byte_type_is_blocked(
    trusted_inputs: _TrustedInputs,
    raw_response_bytes: object,
) -> None:
    _assert_blocked(
        _build(
            trusted_inputs,
            raw_response_bytes=raw_response_bytes,
        ),
        "MMI_RAW_RESPONSE_ENVELOPE_RAW_RESPONSE_INPUT_INVALID",
    )
    _assert_blocked(
        _validate(
            _valid_envelope(trusted_inputs),
            trusted_inputs,
            raw_response_bytes=raw_response_bytes,
        ),
        "MMI_RAW_RESPONSE_ENVELOPE_RAW_RESPONSE_INPUT_INVALID",
    )


def test_raw_response_keyword_is_mandatory() -> None:
    signature = inspect.signature(build_mmi_raw_response_envelope)
    assert (
        signature.parameters["raw_response_bytes"].default
        is inspect.Parameter.empty
    )
    with pytest.raises(TypeError):
        build_mmi_raw_response_envelope()  # type: ignore[call-arg]


@pytest.mark.parametrize(
    "raw_response_bytes",
    (
        b"",
        b"x" * (MAXIMUM_MMI_RAW_RESPONSE_BYTES + 1),
    ),
    ids=("empty", "one-over"),
)
def test_empty_and_oversized_raw_bytes_are_blocked_without_artifact(
    trusted_inputs: _TrustedInputs,
    raw_response_bytes: bytes,
) -> None:
    result = _build(
        trusted_inputs,
        raw_response_bytes=raw_response_bytes,
    )
    _assert_blocked(
        result,
        "MMI_RAW_RESPONSE_ENVELOPE_RAW_RESPONSE_INPUT_INVALID",
    )
    assert result.projection is None


def test_exact_maximum_raw_response_passes_without_truncation(
    trusted_inputs: _TrustedInputs,
) -> None:
    raw_response_bytes = b"\xff" * MAXIMUM_MMI_RAW_RESPONSE_BYTES
    artifact = _valid_envelope(
        trusted_inputs,
        raw_response_bytes=raw_response_bytes,
    )
    assert artifact[RAW_LENGTH_FIELD] == 262_144
    decoded = base64.b64decode(
        artifact[RAW_BASE64_FIELD],  # type: ignore[arg-type]
        validate=True,
    )
    assert decoded == raw_response_bytes
    assert len(decoded) == MAXIMUM_MMI_RAW_RESPONSE_BYTES


@pytest.mark.parametrize(
    "status",
    (
        MmiProjectionResultCategory.PROJECTION_BLOCKED,
        MmiProjectionResultCategory.PROJECTION_CONTRACT_FAILURE,
    ),
    ids=("blocked", "contract-failure"),
)
def test_g1c_failure_classification_and_reason_codes_propagate_exactly(
    trusted_inputs: _TrustedInputs,
    monkeypatch: pytest.MonkeyPatch,
    status: MmiProjectionResultCategory,
) -> None:
    upstream = MmiPolicyProjectionValidationResult(
        status=status,
        authority_effect="NONE",
        reason_codes=("MMI_GROUNDED_PROMPT_UPSTREAM_TEST",),
    )
    monkeypatch.setattr(
        raw_response_envelope,
        "validate_mmi_grounded_prompt",
        lambda **_kwargs: upstream,
    )
    build_result = _build(trusted_inputs)
    validation_result = _validate(
        _independent_envelope(
            trusted_inputs.grounded_prompt,
            RAW_BYTES,
        ),
        trusted_inputs,
    )
    assert build_result.status is status
    assert validation_result.status is status
    assert build_result.reason_codes == upstream.reason_codes
    assert validation_result.reason_codes == upstream.reason_codes
    assert build_result.projection is None


@pytest.mark.parametrize(
    ("status", "authority"),
    (
        (
            MmiProjectionResultCategory.PROJECTION_VALID_COMPLETE,
            "NONE",
        ),
        (
            MmiProjectionResultCategory.PROJECTION_VALID_WITH_GAPS,
            "READY",
        ),
    ),
)
def test_unexpected_g1c_success_contract_is_rejected(
    trusted_inputs: _TrustedInputs,
    monkeypatch: pytest.MonkeyPatch,
    status: MmiProjectionResultCategory,
    authority: str,
) -> None:
    monkeypatch.setattr(
        raw_response_envelope,
        "validate_mmi_grounded_prompt",
        lambda **_kwargs: MmiPolicyProjectionValidationResult(
            status=status,
            authority_effect=authority,
            reason_codes=(),
        ),
    )
    _assert_contract_failure(
        _build(trusted_inputs),
        "MMI_RAW_RESPONSE_ENVELOPE_UPSTREAM_RESULT_INVALID",
    )


def test_invalid_raw_bytes_are_rejected_before_g1c_validation(
    trusted_inputs: _TrustedInputs,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def forbidden_g1c(**_kwargs: object) -> object:
        nonlocal calls
        calls += 1
        raise AssertionError("G1c must not run")

    monkeypatch.setattr(
        raw_response_envelope,
        "validate_mmi_grounded_prompt",
        forbidden_g1c,
    )
    _assert_blocked(
        _build(trusted_inputs, raw_response_bytes=b""),
        "MMI_RAW_RESPONSE_ENVELOPE_RAW_RESPONSE_INPUT_INVALID",
    )
    assert calls == 0


def test_derived_schema_and_identity_invariants_are_contract_failures(
    trusted_inputs: _TrustedInputs,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_schema = raw_response_envelope.validate_artifact_schema
    monkeypatch.setattr(
        raw_response_envelope,
        "validate_artifact_schema",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            ValueError("schema invariant")
        ),
    )
    _assert_contract_failure(
        _build(trusted_inputs),
        "MMI_RAW_RESPONSE_ENVELOPE_DERIVED_SCHEMA_INVALID",
    )
    monkeypatch.setattr(
        raw_response_envelope,
        "validate_artifact_schema",
        original_schema,
    )
    monkeypatch.setattr(
        raw_response_envelope,
        "mmi_raw_response_envelope_identity_sha256",
        lambda _value: (_ for _ in ()).throw(
            canonical.MmiCanonicalizationError(
                "MMI_RAW_RESPONSE_ENVELOPE_REPRESENTATION_INVALID"
            )
        ),
    )
    _assert_contract_failure(
        _build(trusted_inputs),
        "MMI_RAW_RESPONSE_ENVELOPE_DERIVED_CONTRACT_INVALID",
    )


@pytest.mark.parametrize("kind", ("missing", "extra", "fixed-marker"))
def test_representative_candidate_schema_failures_are_blocked(
    trusted_inputs: _TrustedInputs,
    kind: str,
) -> None:
    candidate = _valid_envelope(trusted_inputs)
    if kind == "missing":
        candidate.pop(RAW_BASE64_FIELD)
    elif kind == "extra":
        candidate["metadata"] = {}
    elif kind == "fixed-marker":
        candidate["report_only"] = False
        _reseal(candidate)
    else:
        raise AssertionError(kind)
    _assert_blocked(
        _validate(candidate, trusted_inputs),
        "MMI_RAW_RESPONSE_ENVELOPE_CANDIDATE_SCHEMA_INVALID",
    )


def test_schema_valid_noncanonical_base64_is_blocked_by_r1b_contract(
    trusted_inputs: _TrustedInputs,
) -> None:
    candidate = _valid_envelope(
        trusted_inputs,
        raw_response_bytes=b"\x00",
    )
    candidate[RAW_BASE64_FIELD] = "AB=="
    _reseal(candidate)
    validate_artifact_schema(candidate, schema_name=SCHEMA_NAME)
    assert base64.b64decode("AB==", validate=True) == b"\x00"
    _assert_blocked(
        _validate(
            candidate,
            trusted_inputs,
            raw_response_bytes=b"\x00",
        ),
        "MMI_RAW_RESPONSE_ENVELOPE_REPRESENTATION_INVALID",
    )


def test_candidate_decoding_over_raw_bound_is_blocked(
    trusted_inputs: _TrustedInputs,
) -> None:
    over = b"x" * (MAXIMUM_MMI_RAW_RESPONSE_BYTES + 1)
    candidate = _independent_envelope(
        trusted_inputs.grounded_prompt,
        over,
    )
    candidate[RAW_LENGTH_FIELD] = MAXIMUM_MMI_RAW_RESPONSE_BYTES
    _reseal(candidate)
    validate_artifact_schema(candidate, schema_name=SCHEMA_NAME)
    _assert_blocked(
        _validate(candidate, trusted_inputs),
        "MMI_RAW_RESPONSE_ENVELOPE_REPRESENTATION_INVALID",
    )


@pytest.mark.parametrize(
    ("kind", "reason"),
    (
        (
            "length",
            "MMI_RAW_RESPONSE_ENVELOPE_LENGTH_CONTRADICTION",
        ),
        (
            "digest",
            "MMI_RAW_RESPONSE_ENVELOPE_DIGEST_CONTRADICTION",
        ),
        (
            "identity",
            "MMI_RAW_RESPONSE_ENVELOPE_IDENTITY_CONTRADICTION",
        ),
    ),
)
def test_candidate_contradictions_map_to_contract_failure(
    trusted_inputs: _TrustedInputs,
    kind: str,
    reason: str,
) -> None:
    candidate = _valid_envelope(trusted_inputs)
    if kind == "length":
        candidate[RAW_LENGTH_FIELD] = len(RAW_BYTES) + 1
        _reseal(candidate)
    elif kind == "digest":
        candidate[RAW_DIGEST_FIELD] = "f" * 64
        _reseal(candidate)
    elif kind == "identity":
        candidate[IDENTITY_FIELD] = "f" * 64
    else:
        raise AssertionError(kind)
    validate_artifact_schema(candidate, schema_name=SCHEMA_NAME)
    _assert_contract_failure(
        _validate(candidate, trusted_inputs),
        reason,
    )


def test_correctly_resealed_different_prompt_association_fails_equality(
    trusted_inputs: _TrustedInputs,
) -> None:
    candidate = _valid_envelope(trusted_inputs)
    replacement = "f" * 64
    assert candidate[PROMPT_IDENTITY_FIELD] != replacement
    candidate[PROMPT_IDENTITY_FIELD] = replacement
    _reseal(candidate)
    assert (
        mmi_raw_response_envelope_identity_sha256(candidate)
        == candidate[IDENTITY_FIELD]
    )
    _assert_contract_failure(
        _validate(candidate, trusted_inputs),
        "MMI_RAW_RESPONSE_ENVELOPE_SOURCE_FIDELITY_MISMATCH",
    )


def test_coherent_alternate_raw_bytes_cannot_become_authoritative(
    trusted_inputs: _TrustedInputs,
) -> None:
    alternate = b"fully coherent alternate response bytes"
    candidate = _independent_envelope(
        trusted_inputs.grounded_prompt,
        alternate,
    )
    assert (
        mmi_raw_response_envelope_identity_sha256(candidate)
        == candidate[IDENTITY_FIELD]
    )
    assert base64.b64decode(
        candidate[RAW_BASE64_FIELD],  # type: ignore[arg-type]
        validate=True,
    ) == alternate
    _assert_contract_failure(
        _validate(candidate, trusted_inputs),
        "MMI_RAW_RESPONSE_ENVELOPE_SOURCE_FIDELITY_MISMATCH",
    )


def test_each_supplied_mapping_is_snapshotted_once_for_builder(
    trusted_inputs: _TrustedInputs,
) -> None:
    prompt = _OneSnapshotMapping(trusted_inputs.grounded_prompt)
    view = _OneSnapshotMapping(trusted_inputs.view)
    evidence = _OneSnapshotMapping(trusted_inputs.evidence_bundle)
    policy = _OneSnapshotMapping(trusted_inputs.policy_projection)
    result = _build(
        trusted_inputs,
        grounded_prompt=prompt,
        analyst_visible_evidence_view=view,
        evidence_bundle=evidence,
        policy_projection=policy,
    )
    assert result.valid, result.reason_codes
    for value in (prompt, view, evidence, policy):
        value.assert_read_once()


def test_candidate_and_context_mappings_are_snapshotted_once_for_validator(
    trusted_inputs: _TrustedInputs,
) -> None:
    candidate = _OneSnapshotMapping(_valid_envelope(trusted_inputs))
    prompt = _OneSnapshotMapping(trusted_inputs.grounded_prompt)
    view = _OneSnapshotMapping(trusted_inputs.view)
    evidence = _OneSnapshotMapping(trusted_inputs.evidence_bundle)
    policy = _OneSnapshotMapping(trusted_inputs.policy_projection)
    result = _validate(
        candidate,
        trusted_inputs,
        grounded_prompt=prompt,
        analyst_visible_evidence_view=view,
        evidence_bundle=evidence,
        policy_projection=policy,
    )
    assert result.valid, result.reason_codes
    for value in (candidate, prompt, view, evidence, policy):
        value.assert_read_once()


def test_grounded_prompt_mutation_after_g1c_cannot_change_association(
    trusted_inputs: _TrustedInputs,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    caller_prompt = deepcopy(trusted_inputs.grounded_prompt)
    original_identity = caller_prompt[PROMPT_IDENTITY_FIELD]
    original_validate = (
        raw_response_envelope.validate_mmi_grounded_prompt
    )

    def validate_then_mutate(**kwargs: object):
        result = original_validate(**kwargs)  # type: ignore[arg-type]
        caller_prompt[PROMPT_IDENTITY_FIELD] = "f" * 64
        return result

    monkeypatch.setattr(
        raw_response_envelope,
        "validate_mmi_grounded_prompt",
        validate_then_mutate,
    )
    result = _build(
        trusted_inputs,
        grounded_prompt=caller_prompt,
    )
    assert result.valid, result.reason_codes
    assert result.projection is not None
    assert result.projection[PROMPT_IDENTITY_FIELD] == original_identity
    assert caller_prompt[PROMPT_IDENTITY_FIELD] != original_identity


def test_validation_context_mutation_after_g1c_uses_detached_snapshot(
    trusted_inputs: _TrustedInputs,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    caller_evidence = deepcopy(trusted_inputs.evidence_bundle)
    original_validate = (
        raw_response_envelope.validate_mmi_grounded_prompt
    )

    def validate_then_mutate(**kwargs: object):
        result = original_validate(**kwargs)  # type: ignore[arg-type]
        caller_evidence["artifact_kind"] = "MUTATED"
        return result

    monkeypatch.setattr(
        raw_response_envelope,
        "validate_mmi_grounded_prompt",
        validate_then_mutate,
    )
    result = _build(
        trusted_inputs,
        evidence_bundle=caller_evidence,
    )
    assert result.valid, result.reason_codes
    assert caller_evidence["artifact_kind"] == "MUTATED"


def test_candidate_mutation_after_snapshot_cannot_change_validation(
    trusted_inputs: _TrustedInputs,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = _valid_envelope(trusted_inputs)
    original_validate = (
        raw_response_envelope.validate_mmi_grounded_prompt
    )

    def validate_then_mutate(**kwargs: object):
        result = original_validate(**kwargs)  # type: ignore[arg-type]
        candidate[RAW_DIGEST_FIELD] = "f" * 64
        return result

    monkeypatch.setattr(
        raw_response_envelope,
        "validate_mmi_grounded_prompt",
        validate_then_mutate,
    )
    result = _validate(candidate, trusted_inputs)
    assert result.valid, result.reason_codes
    assert candidate[RAW_DIGEST_FIELD] == "f" * 64


@pytest.mark.parametrize(
    "location",
    ("prompt-cycle", "context-set", "candidate-cycle"),
)
def test_cycle_and_unsupported_mutable_inputs_fail_closed(
    trusted_inputs: _TrustedInputs,
    location: str,
) -> None:
    if location == "prompt-cycle":
        prompt = deepcopy(trusted_inputs.grounded_prompt)
        cycle: list[object] = []
        cycle.append(cycle)
        prompt["cycle"] = cycle
        _assert_blocked(
            _build(trusted_inputs, grounded_prompt=prompt),
            "MMI_RAW_RESPONSE_ENVELOPE_INPUT_SNAPSHOT_INVALID",
        )
    elif location == "context-set":
        evidence = deepcopy(trusted_inputs.evidence_bundle)
        evidence["unsupported"] = {"value"}
        _assert_blocked(
            _build(trusted_inputs, evidence_bundle=evidence),
            "MMI_RAW_RESPONSE_ENVELOPE_INPUT_SNAPSHOT_INVALID",
        )
    elif location == "candidate-cycle":
        candidate = _valid_envelope(trusted_inputs)
        cycle = []
        cycle.append(cycle)
        candidate["cycle"] = cycle
        _assert_blocked(
            _validate(candidate, trusted_inputs),
            "MMI_RAW_RESPONSE_ENVELOPE_CANDIDATE_SCHEMA_INVALID",
        )
    else:
        raise AssertionError(location)


def test_duplicate_keys_fail_before_downstream_validation(
    trusted_inputs: _TrustedInputs,
) -> None:
    _assert_blocked(
        _build(
            trusted_inputs,
            grounded_prompt=_DuplicateKeyMapping(
                trusted_inputs.grounded_prompt
            ),
        ),
        "MMI_RAW_RESPONSE_ENVELOPE_INPUT_SNAPSHOT_INVALID",
    )
    _assert_blocked(
        _validate(
            _DuplicateKeyMapping(_valid_envelope(trusted_inputs)),
            trusted_inputs,
        ),
        "MMI_RAW_RESPONSE_ENVELOPE_CANDIDATE_SCHEMA_INVALID",
    )


def test_source_bound_validator_receives_detached_exact_snapshots(
    trusted_inputs: _TrustedInputs,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    caller = _build_kwargs(trusted_inputs)
    observed: list[dict[str, object]] = []
    original_validate = (
        raw_response_envelope.validate_mmi_grounded_prompt
    )

    def observe(**kwargs: object):
        observed.append(kwargs)
        return original_validate(**kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(
        raw_response_envelope,
        "validate_mmi_grounded_prompt",
        observe,
    )
    result = build_mmi_raw_response_envelope(  # type: ignore[arg-type]
        **caller
    )
    assert result.valid, result.reason_codes
    assert len(observed) == 1
    call = observed[0]
    assert set(call) == {
        "value",
        "analyst_visible_evidence_view",
        "evidence_bundle",
        "policy_projection",
        "policy_source",
        "portfolio_projection",
        "portfolio_source",
        "run_context",
    }
    assert call["value"] == caller["grounded_prompt"]
    assert call["value"] is not caller["grounded_prompt"]
    assert call["evidence_bundle"] == caller["evidence_bundle"]
    assert call["evidence_bundle"] is not caller["evidence_bundle"]


def test_prompt_association_and_identity_semantics_are_distinct(
    trusted_inputs: _TrustedInputs,
) -> None:
    artifact = _valid_envelope(trusted_inputs)
    assert artifact[PROMPT_IDENTITY_FIELD] == (
        trusted_inputs.grounded_prompt[PROMPT_IDENTITY_FIELD]
    )
    assert artifact[RAW_DIGEST_FIELD] == hashlib.sha256(
        RAW_BYTES
    ).hexdigest()
    assert artifact[IDENTITY_FIELD] == _independent_identity(artifact)
    assert "prompt_context_binding_sha256" not in artifact
    assert set(artifact) == EXPECTED_FIELDS


def test_raw_authority_shaped_content_remains_inert_exact_bytes(
    trusted_inputs: _TrustedInputs,
) -> None:
    raw = (
        b"BUY SELL HOLD NO_TRADE NEW_BUY ORDER_COMPILATION "
        b"permission gate budget quantity order execution\x00\xff"
    )
    artifact = _valid_envelope(
        trusted_inputs,
        raw_response_bytes=raw,
    )
    assert artifact["report_only"] is True
    assert artifact["authority_effect"] == "NONE"
    assert artifact["manual_handoff_required"] is True
    assert base64.b64decode(
        artifact[RAW_BASE64_FIELD],  # type: ignore[arg-type]
        validate=True,
    ) == raw


def test_reason_codes_never_expose_raw_or_candidate_values(
    trusted_inputs: _TrustedInputs,
) -> None:
    private = b"PRIVATE RESPONSE BUY\x00\xff"
    candidate = _valid_envelope(
        trusted_inputs,
        raw_response_bytes=private,
    )
    candidate[RAW_DIGEST_FIELD] = "f" * 64
    _reseal(candidate)
    result = _validate(
        candidate,
        trusted_inputs,
        raw_response_bytes=private,
    )
    _assert_contract_failure(result)
    serialized = repr(result.reason_codes)
    assert private.hex() not in serialized
    assert candidate[RAW_BASE64_FIELD] not in serialized
    assert candidate[PROMPT_IDENTITY_FIELD] not in serialized
    assert "PRIVATE" not in serialized
    assert "/" not in serialized and "\\" not in serialized


def test_r1c_has_exact_r2c_consumer_and_phase_ownership() -> None:
    root = repo_root()
    production_paths = tuple(
        sorted((root / "src/investment_orchestrator").rglob("*.py"))
    )
    assert len(production_paths) == 144
    relative = {
        path: path.relative_to(root).as_posix()
        for path in production_paths
    }
    trees = {
        path: ast.parse(path.read_text(encoding="utf-8"))
        for path in production_paths
    }
    grounded_module = "investment_orchestrator.mmi.grounded_prompt"
    raw_module = (
        "investment_orchestrator.mmi.raw_response_envelope"
    )
    raw_relative = (
        "src/investment_orchestrator/mmi/raw_response_envelope.py"
    )
    response_module = (
        "investment_orchestrator.mmi."
        "validated_grounded_analysis_response"
    )
    response_relative = (
        "src/investment_orchestrator/mmi/"
        "validated_grounded_analysis_response.py"
    )
    grounded_importers = tuple(
        relative[path]
        for path, tree in trees.items()
        if any(
            isinstance(node, ast.ImportFrom)
            and node.module == grounded_module
            for node in ast.walk(tree)
        )
    )
    raw_importers = tuple(
        relative[path]
        for path, tree in trees.items()
        if any(
            isinstance(node, ast.ImportFrom)
            and node.module == raw_module
            for node in ast.walk(tree)
        )
    )
    response_importers = tuple(
        relative[path]
        for path, tree in trees.items()
        if any(
            isinstance(node, ast.ImportFrom)
            and node.module == response_module
            for node in ast.walk(tree)
        )
    )
    assert grounded_importers == (raw_relative,)
    assert raw_importers == (response_relative,)
    assert response_importers == ()
    assert (root / raw_relative).is_file()
    assert (root / response_relative).is_file()
    assert (
        root / "src/investment_orchestrator/mmi/__init__.py"
    ).read_text(encoding="utf-8") == (
        '"""Manual-model-interface report-only deterministic '
        'projection contracts."""\n\n__all__ = ()\n'
    )


def test_eleven_domains_and_g1_artifact_are_unchanged(
    trusted_inputs: _TrustedInputs,
) -> None:
    public = {
        name: value
        for name, value in vars(canonical).items()
        if name.startswith("MMI_")
        and name.endswith("_IDENTITY_DOMAIN")
    }
    private = (
        canonical._MMI_ANALYST_VISIBLE_EVIDENCE_VIEW_IDENTITY_DOMAIN,
        canonical._MMI_ANALYST_VISIBLE_EVIDENCE_VIEW_V2_IDENTITY_DOMAIN,
        canonical._MMI_GROUNDED_PROMPT_CONTEXT_BINDING_DOMAIN,
        canonical._MMI_GROUNDED_PROMPT_ARTIFACT_IDENTITY_DOMAIN,
        canonical._MMI_RAW_RESPONSE_ENVELOPE_IDENTITY_DOMAIN,
        canonical._MMI_VALIDATED_GROUNDED_ANALYSIS_RESPONSE_IDENTITY_DOMAIN,
    )
    domains = (*public.values(), *private)
    assert len(domains) == len(set(domains)) == 11
    prompt_before = deepcopy(trusted_inputs.grounded_prompt)
    artifact = _valid_envelope(trusted_inputs)
    validation = _validate(artifact, trusted_inputs)
    assert validation.valid
    assert trusted_inputs.grounded_prompt == prompt_before


def test_module_has_no_parser_writer_transport_or_authority_capability() -> None:
    path = (
        repo_root()
        / "src/investment_orchestrator/mmi/raw_response_envelope.py"
    )
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    } | {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    assert {"base64", "hashlib"} <= imported
    prohibited = {
        "json",
        "codecs",
        "os",
        "pathlib",
        "pickle",
        "subprocess",
        "socket",
        "requests",
        "httpx",
        "openai",
        "anthropic",
        "investment_orchestrator.cli",
        "investment_orchestrator.workflow",
        "investment_orchestrator.state",
        "investment_orchestrator.permissions",
        "investment_orchestrator.orders",
        "investment_orchestrator.broker",
    }
    assert not any(
        module == prefix or module.startswith(f"{prefix}.")
        for module in imported
        for prefix in prohibited
    )
    calls = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
    }
    assert "b64decode" not in calls
    assert not {
        "open",
        "read_bytes",
        "read_text",
        "write_bytes",
        "write_text",
    } & calls
