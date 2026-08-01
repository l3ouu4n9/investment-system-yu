"""Deterministic validation of one dormant MMI R2c-v2 response."""

from __future__ import annotations

from collections.abc import Mapping
import json
from typing import Final, NoReturn

from investment_orchestrator.common.schema_validation import (
    validate_artifact_schema,
)
from investment_orchestrator.mmi.canonical import (
    MAXIMUM_CANONICAL_JSON_BYTES,
    MAX_MMI_GROUNDED_ANALYSIS_RESPONSE_V2_CANONICAL_BYTES,
    MmiCanonicalizationError,
    _MMI_VALIDATED_GROUNDED_ANALYSIS_RESPONSE_V2_IDENTITY_DOMAIN,
    canonical_json_bytes,
    record_identity_sha256,
)
from investment_orchestrator.mmi.contracts import (
    AUTHORITY_EFFECT_NONE,
    MMI_VALIDATED_GROUNDED_ANALYSIS_RESPONSE_ARTIFACT_KIND,
    MmiCapturedSource,
    MmiProjectionRunContext,
    _MMI_GROUNDED_ANALYSIS_RESPONSE_V2_SCHEMA_VERSION,
    _MMI_GROUNDED_PROMPT_MANUAL_HANDOFF_REQUIRED,
    _MMI_VALIDATED_GROUNDED_ANALYSIS_RESPONSE_V2_SCHEMA_VERSION,
)
from investment_orchestrator.mmi.raw_response_envelope_v2 import (
    MmiRawResponseEnvelopeV2Error,
    _validated_envelope_context,
)


__all__ = (
    "MmiValidatedGroundedAnalysisResponseV2Error",
    "build_mmi_validated_grounded_analysis_response_v2",
    "validate_mmi_validated_grounded_analysis_response_v2",
)

_SCHEMA_NAME: Final = (
    "mmi_validated_grounded_analysis_response_v2.schema.json"
)
_R1_IDENTITY_FIELD: Final = "raw_response_envelope_identity_sha256"
_ARTIFACT_IDENTITY_FIELD: Final = (
    "validated_grounded_analysis_response_identity_sha256"
)
_CONTEXT_FIELD: Final = "prompt_context_binding_sha256"
_ZERO_SHA256: Final = "0" * 64
_JSON_WHITESPACE: Final = frozenset(" \t\r\n")

_WRAPPER_FIELDS: Final = frozenset(
    {
        "schema_version",
        "artifact_kind",
        "report_only",
        "authority_effect",
        "manual_handoff_required",
        _R1_IDENTITY_FIELD,
        "response_payload",
        _ARTIFACT_IDENTITY_FIELD,
    }
)
_QUALITATIVE_ARRAY_FIELDS: Final = (
    "evidence_observations",
    "risks",
    "uncertainties",
    "contradictions",
    "research_questions",
)
_ALWAYS_ALLOWED_REFERENCES: Final = frozenset(
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
_PRESENT_PORTFOLIO_REFERENCES: Final = frozenset(
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
_PRESENT_PORTFOLIO_STATUSES: Final = frozenset(
    {
        "PRESENT_VALIDATED_SOURCE_ABSENT",
        "PRESENT_SOURCE_BOUND_VALIDATED",
    }
)


class MmiValidatedGroundedAnalysisResponseV2Error(ValueError):
    """Raised when no valid dormant R2c-v2 artifact can be returned."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class _DuplicateJsonKey(ValueError):
    pass


class _NonstandardJsonConstant(ValueError):
    pass


def _fail(code: str) -> NoReturn:
    raise MmiValidatedGroundedAnalysisResponseV2Error(code)


def _snapshot_value(
    value: object,
    *,
    active_container_ids: set[int],
) -> object:
    if value is None or type(value) in {bool, int, float, str}:
        return value
    if isinstance(value, Mapping):
        container_id = id(value)
        if container_id in active_container_ids:
            _fail("MMI_VALIDATED_RESPONSE_V2_INPUT_INVALID")
        active_container_ids.add(container_id)
        try:
            snapshot: dict[str, object] = {}
            try:
                keys = tuple(value.keys())
                if (
                    any(type(key) is not str for key in keys)
                    or len(keys) != len(set(keys))
                ):
                    _fail("MMI_VALIDATED_RESPONSE_V2_INPUT_INVALID")
                for key in keys:
                    snapshot[key] = _snapshot_value(
                        value[key],
                        active_container_ids=active_container_ids,
                    )
            except MmiValidatedGroundedAnalysisResponseV2Error:
                raise
            except Exception:
                _fail("MMI_VALIDATED_RESPONSE_V2_INPUT_INVALID")
            return snapshot
        finally:
            active_container_ids.remove(container_id)
    if type(value) is list:
        container_id = id(value)
        if container_id in active_container_ids:
            _fail("MMI_VALIDATED_RESPONSE_V2_INPUT_INVALID")
        active_container_ids.add(container_id)
        try:
            return [
                _snapshot_value(
                    item,
                    active_container_ids=active_container_ids,
                )
                for item in value
            ]
        finally:
            active_container_ids.remove(container_id)
    _fail("MMI_VALIDATED_RESPONSE_V2_INPUT_INVALID")


def _snapshot_mapping(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping):
        _fail("MMI_VALIDATED_RESPONSE_V2_INPUT_INVALID")
    snapshot = _snapshot_value(value, active_container_ids=set())
    if type(snapshot) is not dict:
        _fail("MMI_VALIDATED_RESPONSE_V2_INPUT_INVALID")
    return snapshot


def _validated_upstream(
    *,
    raw_response_envelope: Mapping[str, object],
    evidence_bundle: Mapping[str, object],
    policy_projection: Mapping[str, object],
    policy_source: MmiCapturedSource,
    portfolio_projection: Mapping[str, object] | None,
    portfolio_source: MmiCapturedSource | None,
    run_context: MmiProjectionRunContext,
) -> tuple[
    dict[str, object],
    bytes,
    dict[str, object],
    dict[str, object],
]:
    evidence = _snapshot_mapping(evidence_bundle)
    policy = _snapshot_mapping(policy_projection)
    portfolio = (
        None
        if portfolio_projection is None
        else _snapshot_mapping(portfolio_projection)
    )
    envelope = _snapshot_mapping(raw_response_envelope)
    try:
        (
            validated_envelope,
            exact_bytes,
            view,
            prompt,
        ) = _validated_envelope_context(
            envelope,
            evidence_bundle=evidence,
            policy_projection=policy,
            policy_source=policy_source,
            portfolio_projection=portfolio,
            portfolio_source=portfolio_source,
            run_context=run_context,
        )
    except MmiRawResponseEnvelopeV2Error:
        _fail("MMI_VALIDATED_RESPONSE_V2_UPSTREAM_INVALID")
    return validated_envelope, exact_bytes, view, prompt


def _reject_duplicate_keys(
    pairs: list[tuple[str, object]],
) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJsonKey
        result[key] = value
    return result


def _reject_nonstandard_constant(_value: str) -> object:
    raise _NonstandardJsonConstant


def _reject_unpaired_surrogates(value: object) -> None:
    if type(value) is str:
        try:
            value.encode("utf-8", errors="strict")
        except UnicodeEncodeError:
            _fail("MMI_VALIDATED_RESPONSE_V2_JSON_INVALID")
        return
    if type(value) is dict:
        for key, nested in value.items():
            _reject_unpaired_surrogates(key)
            _reject_unpaired_surrogates(nested)
        return
    if type(value) is list:
        for nested in value:
            _reject_unpaired_surrogates(nested)


def _decode_and_parse_response(exact_bytes: bytes) -> dict[str, object]:
    try:
        text = exact_bytes.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        _fail("MMI_VALIDATED_RESPONSE_V2_UTF8_INVALID")
    if text.startswith("\ufeff"):
        _fail("MMI_VALIDATED_RESPONSE_V2_UTF8_BOM_INVALID")
    if not text or all(character in _JSON_WHITESPACE for character in text):
        _fail("MMI_VALIDATED_RESPONSE_V2_JSON_INVALID")
    try:
        parsed = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_nonstandard_constant,
        )
    except (
        json.JSONDecodeError,
        _DuplicateJsonKey,
        _NonstandardJsonConstant,
        RecursionError,
        ValueError,
    ):
        _fail("MMI_VALIDATED_RESPONSE_V2_JSON_INVALID")
    if type(parsed) is not dict:
        _fail("MMI_VALIDATED_RESPONSE_V2_JSON_INVALID")
    _reject_unpaired_surrogates(parsed)
    return parsed


def _validate_response_payload_canonical_size(payload: object) -> None:
    try:
        canonical_json_bytes(
            payload,
            maximum_bytes=(
                MAX_MMI_GROUNDED_ANALYSIS_RESPONSE_V2_CANONICAL_BYTES
            ),
        )
    except MmiCanonicalizationError:
        _fail("MMI_VALIDATED_RESPONSE_V2_PAYLOAD_SIZE_INVALID")


def _provisional_artifact(
    *,
    envelope_identity: str,
    payload: dict[str, object],
) -> dict[str, object]:
    return {
        "schema_version": (
            _MMI_VALIDATED_GROUNDED_ANALYSIS_RESPONSE_V2_SCHEMA_VERSION
        ),
        "artifact_kind": (
            MMI_VALIDATED_GROUNDED_ANALYSIS_RESPONSE_ARTIFACT_KIND
        ),
        "report_only": True,
        "authority_effect": AUTHORITY_EFFECT_NONE,
        "manual_handoff_required": (
            _MMI_GROUNDED_PROMPT_MANUAL_HANDOFF_REQUIRED
        ),
        _R1_IDENTITY_FIELD: envelope_identity,
        "response_payload": payload,
        _ARTIFACT_IDENTITY_FIELD: _ZERO_SHA256,
    }


def _validate_payload_schema(
    *,
    payload: dict[str, object],
    envelope_identity: str,
) -> None:
    try:
        validate_artifact_schema(
            _provisional_artifact(
                envelope_identity=envelope_identity,
                payload=payload,
            ),
            schema_name=_SCHEMA_NAME,
        )
    except Exception:
        _fail("MMI_VALIDATED_RESPONSE_V2_PAYLOAD_SCHEMA_INVALID")
    _validate_response_payload_canonical_size(payload)


def _expected_instrument_tickers(
    view: Mapping[str, object],
) -> tuple[str, ...]:
    try:
        policy_view = view["policy_view"]
        if type(policy_view) is not dict:
            raise TypeError
        instruments = policy_view["analysis_instruments"]
        if type(instruments) is not list:
            raise TypeError
        tickers = tuple(
            item["ticker"]
            for item in instruments
            if type(item) is dict and type(item.get("ticker")) is str
        )
    except (KeyError, TypeError):
        _fail("MMI_VALIDATED_RESPONSE_V2_UPSTREAM_INVALID")
    if len(tickers) != len(instruments):
        _fail("MMI_VALIDATED_RESPONSE_V2_UPSTREAM_INVALID")
    return tickers


def _require_instrument_equality(
    *,
    payload: Mapping[str, object],
    view: Mapping[str, object],
) -> None:
    instrument_views = payload.get("instrument_views")
    if type(instrument_views) is not list:
        _fail("MMI_VALIDATED_RESPONSE_V2_INSTRUMENT_MISMATCH")
    try:
        observed = tuple(
            item["ticker"]
            for item in instrument_views
            if type(item) is dict and type(item.get("ticker")) is str
        )
    except (KeyError, TypeError):
        _fail("MMI_VALIDATED_RESPONSE_V2_INSTRUMENT_MISMATCH")
    if (
        len(observed) != len(instrument_views)
        or observed != _expected_instrument_tickers(view)
    ):
        _fail("MMI_VALIDATED_RESPONSE_V2_INSTRUMENT_MISMATCH")


def _source_bound_reference_catalog(
    view: Mapping[str, object],
) -> frozenset[str]:
    try:
        policy_view = view["policy_view"]
        limitations = view["known_view_limitations"]
        portfolio_view = view["portfolio_view"]
        if (
            type(policy_view) is not dict
            or type(limitations) is not list
            or type(portfolio_view) is not dict
        ):
            raise TypeError
        instruments = policy_view["analysis_instruments"]
        presence_status = portfolio_view["presence_status"]
        if type(instruments) is not list or type(presence_status) is not str:
            raise TypeError
    except (KeyError, TypeError):
        _fail("MMI_VALIDATED_RESPONSE_V2_UPSTREAM_INVALID")
    allowed = set(_ALWAYS_ALLOWED_REFERENCES)
    allowed.update(
        f"POLICY.INSTRUMENT.{index:04d}"
        for index in range(1, len(instruments) + 1)
    )
    allowed.update(
        f"LIMITATION.{index:04d}"
        for index in range(1, len(limitations) + 1)
    )
    if presence_status == "NOT_SUPPLIED":
        return frozenset(allowed)
    if presence_status not in _PRESENT_PORTFOLIO_STATUSES:
        _fail("MMI_VALIDATED_RESPONSE_V2_UPSTREAM_INVALID")
    observations = portfolio_view.get("open_buy_observations")
    if type(observations) is not list:
        _fail("MMI_VALIDATED_RESPONSE_V2_UPSTREAM_INVALID")
    allowed.update(_PRESENT_PORTFOLIO_REFERENCES)
    allowed.update(
        f"PORTFOLIO.OBSERVATION.{index:04d}"
        for index in range(1, len(observations) + 1)
    )
    return frozenset(allowed)


def _iter_references(payload: Mapping[str, object]) -> tuple[str, ...]:
    references: list[str] = []
    instrument_views = payload.get("instrument_views")
    if type(instrument_views) is not list:
        _fail("MMI_VALIDATED_RESPONSE_V2_PAYLOAD_SCHEMA_INVALID")
    for item in instrument_views:
        if type(item) is not dict or type(item.get("references")) is not list:
            _fail("MMI_VALIDATED_RESPONSE_V2_PAYLOAD_SCHEMA_INVALID")
        references.extend(item["references"])
    for field in _QUALITATIVE_ARRAY_FIELDS:
        items = payload.get(field)
        if type(items) is not list:
            _fail("MMI_VALIDATED_RESPONSE_V2_PAYLOAD_SCHEMA_INVALID")
        for item in items:
            if type(item) is not dict or type(item.get("references")) is not list:
                _fail("MMI_VALIDATED_RESPONSE_V2_PAYLOAD_SCHEMA_INVALID")
            references.extend(item["references"])
    summary = payload.get("summary")
    if type(summary) is not dict or type(summary.get("references")) is not list:
        _fail("MMI_VALIDATED_RESPONSE_V2_PAYLOAD_SCHEMA_INVALID")
    references.extend(summary["references"])
    if any(type(reference) is not str for reference in references):
        _fail("MMI_VALIDATED_RESPONSE_V2_PAYLOAD_SCHEMA_INVALID")
    return tuple(references)


def _require_reference_membership(
    *,
    payload: Mapping[str, object],
    view: Mapping[str, object],
) -> None:
    allowed = _source_bound_reference_catalog(view)
    if any(reference not in allowed for reference in _iter_references(payload)):
        _fail("MMI_VALIDATED_RESPONSE_V2_REFERENCE_MISMATCH")


def _artifact_identity(artifact: dict[str, object]) -> str:
    try:
        return record_identity_sha256(
            artifact,
            identity_field=_ARTIFACT_IDENTITY_FIELD,
            domain=(
                _MMI_VALIDATED_GROUNDED_ANALYSIS_RESPONSE_V2_IDENTITY_DOMAIN
            ),
            maximum_bytes=MAXIMUM_CANONICAL_JSON_BYTES,
        )
    except MmiCanonicalizationError:
        _fail("MMI_VALIDATED_RESPONSE_V2_IDENTITY_INVALID")


def _derive_expected_artifact(
    *,
    raw_response_envelope: Mapping[str, object],
    evidence_bundle: Mapping[str, object],
    policy_projection: Mapping[str, object],
    policy_source: MmiCapturedSource,
    portfolio_projection: Mapping[str, object] | None,
    portfolio_source: MmiCapturedSource | None,
    run_context: MmiProjectionRunContext,
) -> dict[str, object]:
    envelope, exact_bytes, view, prompt = _validated_upstream(
        raw_response_envelope=raw_response_envelope,
        evidence_bundle=evidence_bundle,
        policy_projection=policy_projection,
        policy_source=policy_source,
        portfolio_projection=portfolio_projection,
        portfolio_source=portfolio_source,
        run_context=run_context,
    )
    envelope_identity = envelope.get(_R1_IDENTITY_FIELD)
    if type(envelope_identity) is not str:
        _fail("MMI_VALIDATED_RESPONSE_V2_UPSTREAM_INVALID")
    payload = _decode_and_parse_response(exact_bytes)
    _validate_payload_schema(
        payload=payload,
        envelope_identity=envelope_identity,
    )
    trusted_context = prompt.get(_CONTEXT_FIELD)
    if (
        type(trusted_context) is not str
        or payload.get(_CONTEXT_FIELD) != trusted_context
    ):
        _fail("MMI_VALIDATED_RESPONSE_V2_CONTEXT_MISMATCH")
    if payload.get("response_schema_version") != (
        _MMI_GROUNDED_ANALYSIS_RESPONSE_V2_SCHEMA_VERSION
    ):
        _fail("MMI_VALIDATED_RESPONSE_V2_PAYLOAD_SCHEMA_INVALID")
    _require_instrument_equality(payload=payload, view=view)
    _require_reference_membership(payload=payload, view=view)
    artifact = _provisional_artifact(
        envelope_identity=envelope_identity,
        payload=payload,
    )
    artifact[_ARTIFACT_IDENTITY_FIELD] = _artifact_identity(artifact)
    try:
        validate_artifact_schema(artifact, schema_name=_SCHEMA_NAME)
    except Exception:
        _fail("MMI_VALIDATED_RESPONSE_V2_SCHEMA_INVALID")
    if artifact[_ARTIFACT_IDENTITY_FIELD] != _artifact_identity(artifact):
        _fail("MMI_VALIDATED_RESPONSE_V2_IDENTITY_INVALID")
    return artifact


def build_mmi_validated_grounded_analysis_response_v2(
    *,
    raw_response_envelope: Mapping[str, object],
    evidence_bundle: Mapping[str, object],
    policy_projection: Mapping[str, object],
    policy_source: MmiCapturedSource,
    portfolio_projection: Mapping[str, object] | None,
    portfolio_source: MmiCapturedSource | None,
    run_context: MmiProjectionRunContext,
) -> dict[str, object]:
    """Build one source-bound, report-only R2c-v2 artifact."""
    return _derive_expected_artifact(
        raw_response_envelope=raw_response_envelope,
        evidence_bundle=evidence_bundle,
        policy_projection=policy_projection,
        policy_source=policy_source,
        portfolio_projection=portfolio_projection,
        portfolio_source=portfolio_source,
        run_context=run_context,
    )


def validate_mmi_validated_grounded_analysis_response_v2(
    *,
    value: Mapping[str, object],
    raw_response_envelope: Mapping[str, object],
    evidence_bundle: Mapping[str, object],
    policy_projection: Mapping[str, object],
    policy_source: MmiCapturedSource,
    portfolio_projection: Mapping[str, object] | None,
    portfolio_source: MmiCapturedSource | None,
    run_context: MmiProjectionRunContext,
) -> dict[str, object]:
    """Return a stable snapshot only for the exact source-bound R2c-v2."""
    candidate = _snapshot_mapping(value)
    expected = _derive_expected_artifact(
        raw_response_envelope=raw_response_envelope,
        evidence_bundle=evidence_bundle,
        policy_projection=policy_projection,
        policy_source=policy_source,
        portfolio_projection=portfolio_projection,
        portfolio_source=portfolio_source,
        run_context=run_context,
    )
    try:
        validate_artifact_schema(candidate, schema_name=_SCHEMA_NAME)
    except Exception:
        _fail("MMI_VALIDATED_RESPONSE_V2_SCHEMA_INVALID")
    payload = candidate.get("response_payload")
    if type(payload) is not dict:
        _fail("MMI_VALIDATED_RESPONSE_V2_PAYLOAD_SCHEMA_INVALID")
    _validate_response_payload_canonical_size(payload)
    if set(candidate) != _WRAPPER_FIELDS:
        _fail("MMI_VALIDATED_RESPONSE_V2_SCHEMA_INVALID")
    if candidate.get(_ARTIFACT_IDENTITY_FIELD) != _artifact_identity(candidate):
        _fail("MMI_VALIDATED_RESPONSE_V2_IDENTITY_INVALID")
    if candidate != expected:
        _fail("MMI_VALIDATED_RESPONSE_V2_SOURCE_FIDELITY_INVALID")
    return candidate
