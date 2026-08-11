"""Reconstruct an MMI run context from validated durable evidence only.

This small current-lane owner centralizes the final, non-authoritative step
from a detached durable artifact to its already-validated evaluation-time run
context.  It owns no artifact contract, persistence, workflow, availability,
permissions, freshness, publication, order, provider, or retry behavior.

Its one P2a production consumer is the offline H2c prepared-case wrapper.  A
future caller must remain an explicitly reviewed production consumer and must
provide its own artifact validator, identity field, and size cap.  In
particular, this module accepts no caller-supplied timestamp.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
import json
import re
from typing import Final, NoReturn

import investment_orchestrator.mmi.contracts as _contracts
from investment_orchestrator.mmi.canonical import (
    MmiCanonicalizationError,
    canonical_json_bytes,
)
from investment_orchestrator.mmi.contracts import MmiProjectionRunContext


__all__ = (
    "MmiRunContextResumptionError",
    "resume_mmi_projection_run_context_from_validated_artifact",
)

_ERROR: Final = "MMI_RUN_CONTEXT_RESUMPTION_INVALID"
_SHA256_RE: Final = re.compile(r"^[0-9a-f]{64}$")


class MmiRunContextResumptionError(ValueError):
    """Raised when durable evidence cannot authorize run-context resumption."""

    code: Final[str] = _ERROR

    def __init__(self) -> None:
        super().__init__(_ERROR)


def _fail() -> NoReturn:
    raise MmiRunContextResumptionError() from None


def _snapshot_mapping(
    value: Mapping[str, object],
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


def resume_mmi_projection_run_context_from_validated_artifact(
    *,
    artifact: Mapping[str, object],
    expected_artifact_identity_sha256: str,
    validate_artifact: Callable[[Mapping[str, object]], None],
    artifact_identity_field: str,
    maximum_canonical_bytes: int,
) -> MmiProjectionRunContext:
    """Mint only after durable evidence validates and matches its identity.

    The artifact is detached once before validation.  The validator owns its
    contract and self-identity checks; this owner adds mandatory expected
    identity equality, then reads the timestamp only from that validated
    detached snapshot.  There is deliberately no timestamp argument.
    """
    if (
        type(artifact_identity_field) is not str
        or not artifact_identity_field
        or type(maximum_canonical_bytes) is not int
        or maximum_canonical_bytes <= 0
        or not callable(validate_artifact)
    ):
        _fail()
    snapshot = _snapshot_mapping(
        artifact,
        maximum_bytes=maximum_canonical_bytes,
    )
    if (
        type(expected_artifact_identity_sha256) is not str
        or _SHA256_RE.fullmatch(expected_artifact_identity_sha256) is None
    ):
        _fail()
    validate_artifact(snapshot)
    if snapshot.get(artifact_identity_field) != (
        expected_artifact_identity_sha256
    ):
        _fail()
    evaluation_timestamp_utc = snapshot.get("evaluation_timestamp_utc")
    if type(evaluation_timestamp_utc) is not str:
        _fail()
    return _contracts.mint_mmi_projection_run_context_from_canonical_timestamp(
        evaluation_timestamp_utc=evaluation_timestamp_utc,
    )
