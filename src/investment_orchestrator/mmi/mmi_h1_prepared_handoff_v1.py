"""Dormant report-only envelope for one manual H1 prepared handoff.

This owner holds the closed nine-field prepared-handoff contract: its field
set, its constant assertions, its canonical timestamp, its two source digests,
its bounded embedded ``mmi_grounded_prompt_v2`` object, its canonical size, and
its own self-identity.  The embedded grounded prompt is opaque here and is the
sole identity-bearing prompt truth; whether the live sources still reproduce it
belongs to the consuming workflow, before any response byte is read.

``portfolio_snapshot_source_sha256`` is JSON ``null`` exactly when preparation
proved the code-owned portfolio source genuinely absent.  Absence is a recorded
shape, never a digest: no sentinel, no zero hash, no synthesized record.

One narrow addition reconstructs this envelope's own evaluation-time run
context through the current generic resumption owner, and only after complete
validation plus mandatory expected-identity equality, so a later process can
rebuild the exact prepared chain without ever choosing that time itself.  It
reads no clock.

This owner has no filesystem, response-file, live-source, capability, workflow,
availability, permission, freshness, gate, publication, pointer, order, broker,
provider, network, retry, or execution behavior.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
import json
import re
from typing import Final, Literal, NoReturn

from investment_orchestrator.common.schema_validation import (
    validate_artifact_schema,
)
from investment_orchestrator.mmi.canonical import (
    MAX_MMI_H1_PREPARED_HANDOFF_V1_CANONICAL_BYTES,
    MmiCanonicalizationError,
    _MMI_H1_PREPARED_HANDOFF_V1_IDENTITY_DOMAIN,
    canonical_json_bytes,
    record_identity_sha256,
)
from investment_orchestrator.mmi.contracts import (
    AUTHORITY_EFFECT_NONE,
    CANONICAL_UTC_TIMESTAMP_FORMAT,
    MmiProjectionRunContext,
)
from investment_orchestrator.mmi.run_context_resumption import (
    MmiRunContextResumptionError,
    resume_mmi_projection_run_context_from_validated_artifact,
)


__all__ = (
    "MMI_H1_PREPARED_HANDOFF_ARTIFACT_KIND",
    "MMI_H1_PREPARED_HANDOFF_V1_SCHEMA_VERSION",
    "MmiH1PreparedHandoffV1Error",
    "resume_mmi_h1_prepared_handoff_run_context",
    "validate_mmi_h1_prepared_handoff_v1",
)

MMI_H1_PREPARED_HANDOFF_V1_SCHEMA_VERSION: Final = "mmi_h1_prepared_handoff_v1"
MMI_H1_PREPARED_HANDOFF_ARTIFACT_KIND: Final = "MMI_H1_PREPARED_HANDOFF"

_ERROR: Final = "MMI_H1_PREPARED_HANDOFF_V1_INVALID"
_SCHEMA: Final = "mmi_h1_prepared_handoff_v1.schema.json"
_IDENTITY_FIELD: Final = "prepared_handoff_identity_sha256"
_MAXIMUM_CANONICAL_BYTES: Final = (
    MAX_MMI_H1_PREPARED_HANDOFF_V1_CANONICAL_BYTES
)
# The existing bounded embedded G2 maximum.  It is a derivation input for the
# envelope bound above and is never loosened here.
_GROUNDED_PROMPT_MAXIMUM_CANONICAL_BYTES: Final = 393_852
_ZERO_SHA256: Final = "0" * 64
_SHA256_RE: Final = re.compile(r"^[0-9a-f]{64}$")
_FIELDS: Final = (
    "schema_version",
    "artifact_kind",
    "report_only",
    "authority_effect",
    "evaluation_timestamp_utc",
    "strategy_settings_source_sha256",
    "portfolio_snapshot_source_sha256",
    "grounded_prompt",
    _IDENTITY_FIELD,
)

_PreparedHandoffErrorCode = Literal["MMI_H1_PREPARED_HANDOFF_V1_INVALID"]


class MmiH1PreparedHandoffV1Error(ValueError):
    """Raised when a prepared-handoff envelope is not complete and exact."""

    code: _PreparedHandoffErrorCode

    def __init__(self, code: _PreparedHandoffErrorCode) -> None:
        if code != _ERROR:
            raise TypeError("unsupported H1 prepared-handoff error code")
        super().__init__(code)
        self.code = code


def _fail() -> NoReturn:
    raise MmiH1PreparedHandoffV1Error(_ERROR) from None


def _snapshot_mapping(
    value: object,
    *,
    maximum_bytes: int,
) -> dict[str, object]:
    """Detach one caller mapping through the established canonical boundary."""
    if not isinstance(value, Mapping):
        _fail()
    try:
        encoded = canonical_json_bytes(
            dict(value),
            maximum_bytes=maximum_bytes,
        )
    except MmiCanonicalizationError:
        _fail()
    parsed = json.loads(encoded)
    if type(parsed) is not dict:
        _fail()
    return parsed


def _require_canonical_timestamp(value: object) -> None:
    if type(value) is not str:
        _fail()
    try:
        observed = datetime.strptime(value, CANONICAL_UTC_TIMESTAMP_FORMAT)
    except ValueError:
        _fail()
    if observed.strftime(CANONICAL_UTC_TIMESTAMP_FORMAT) != value:
        _fail()


def _require_source_digest(value: object, *, optional: bool) -> None:
    if optional and value is None:
        return
    if type(value) is not str or _SHA256_RE.fullmatch(value) is None:
        _fail()


def _validate_prepared_handoff_snapshot(
    *,
    value: object,
) -> dict[str, object]:
    prepared = _snapshot_mapping(
        value,
        maximum_bytes=_MAXIMUM_CANONICAL_BYTES,
    )
    if tuple(sorted(prepared)) != tuple(sorted(_FIELDS)):
        _fail()
    try:
        validate_artifact_schema(prepared, schema_name=_SCHEMA)
    except Exception:
        _fail()
    if (
        prepared.get("schema_version")
        != MMI_H1_PREPARED_HANDOFF_V1_SCHEMA_VERSION
        or prepared.get("artifact_kind")
        != MMI_H1_PREPARED_HANDOFF_ARTIFACT_KIND
        or prepared.get("report_only") is not True
        or prepared.get("authority_effect") != AUTHORITY_EFFECT_NONE
    ):
        _fail()
    _require_canonical_timestamp(prepared.get("evaluation_timestamp_utc"))
    _require_source_digest(
        prepared.get("strategy_settings_source_sha256"),
        optional=False,
    )
    _require_source_digest(
        prepared.get("portfolio_snapshot_source_sha256"),
        optional=True,
    )
    _snapshot_mapping(
        prepared.get("grounded_prompt"),
        maximum_bytes=_GROUNDED_PROMPT_MAXIMUM_CANONICAL_BYTES,
    )
    try:
        expected = record_identity_sha256(
            prepared,
            identity_field=_IDENTITY_FIELD,
            domain=_MMI_H1_PREPARED_HANDOFF_V1_IDENTITY_DOMAIN,
            maximum_bytes=_MAXIMUM_CANONICAL_BYTES,
        )
    except MmiCanonicalizationError:
        _fail()
    if prepared.get(_IDENTITY_FIELD) != expected:
        _fail()
    return prepared


def _build_mmi_h1_prepared_handoff_v1(
    *,
    evaluation_timestamp_utc: str,
    strategy_settings_source_sha256: str,
    portfolio_snapshot_source_sha256: str | None,
    grounded_prompt: Mapping[str, object],
) -> dict[str, object]:
    """Build one detached dormant envelope from already-validated inputs."""
    _require_canonical_timestamp(evaluation_timestamp_utc)
    _require_source_digest(strategy_settings_source_sha256, optional=False)
    _require_source_digest(portfolio_snapshot_source_sha256, optional=True)
    prompt = _snapshot_mapping(
        grounded_prompt,
        maximum_bytes=_GROUNDED_PROMPT_MAXIMUM_CANONICAL_BYTES,
    )
    prepared: dict[str, object] = {
        "schema_version": MMI_H1_PREPARED_HANDOFF_V1_SCHEMA_VERSION,
        "artifact_kind": MMI_H1_PREPARED_HANDOFF_ARTIFACT_KIND,
        "report_only": True,
        "authority_effect": AUTHORITY_EFFECT_NONE,
        "evaluation_timestamp_utc": evaluation_timestamp_utc,
        "strategy_settings_source_sha256": strategy_settings_source_sha256,
        "portfolio_snapshot_source_sha256": portfolio_snapshot_source_sha256,
        "grounded_prompt": prompt,
        _IDENTITY_FIELD: _ZERO_SHA256,
    }
    try:
        prepared[_IDENTITY_FIELD] = record_identity_sha256(
            prepared,
            identity_field=_IDENTITY_FIELD,
            domain=_MMI_H1_PREPARED_HANDOFF_V1_IDENTITY_DOMAIN,
            maximum_bytes=_MAXIMUM_CANONICAL_BYTES,
        )
    except MmiCanonicalizationError:
        _fail()
    return _validate_prepared_handoff_snapshot(value=prepared)


def validate_mmi_h1_prepared_handoff_v1(
    *,
    prepared_handoff: Mapping[str, object],
) -> dict[str, object]:
    """Validate only the closed envelope, its bound, and its self-identity."""
    return _validate_prepared_handoff_snapshot(value=prepared_handoff)


def _validate_mmi_h1_prepared_handoff_for_resumption(
    prepared_handoff: Mapping[str, object],
) -> None:
    """Apply this owner's closed validator to a detached resumption snapshot."""
    validate_mmi_h1_prepared_handoff_v1(prepared_handoff=prepared_handoff)


def resume_mmi_h1_prepared_handoff_run_context(
    *,
    prepared_handoff: Mapping[str, object],
    expected_prepared_handoff_identity_sha256: str,
) -> MmiProjectionRunContext:
    """Reconstruct one validated envelope's own evaluation-time context.

    The envelope validator above stays the sole owner of canonical identity
    recomputation and of ``recomputed == embedded``.  The current generic
    resumption owner adds mandatory ``embedded == expected`` equality and only
    then mints from the timestamp inside that validated envelope, so no caller
    can choose the reconstructed evaluation time and no substituted or stale
    envelope can reach the mint.

    This reconstructs one evaluation time.  It resumes no workflow, retries
    nothing, reads no response, and grants no availability, permission, gate,
    publication, order, broker, or execution authority.
    """
    try:
        return resume_mmi_projection_run_context_from_validated_artifact(
            artifact=prepared_handoff,
            expected_artifact_identity_sha256=(
                expected_prepared_handoff_identity_sha256
            ),
            validate_artifact=(
                _validate_mmi_h1_prepared_handoff_for_resumption
            ),
            artifact_identity_field=_IDENTITY_FIELD,
            maximum_canonical_bytes=_MAXIMUM_CANONICAL_BYTES,
        )
    except MmiRunContextResumptionError:
        _fail()
