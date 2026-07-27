"""Closed types and run context for report-only MMI projections."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
import hashlib
import hmac
import json
from pathlib import PurePosixPath
import secrets
from types import MappingProxyType
from typing import Final, Mapping, NoReturn, Protocol

from investment_orchestrator.mmi.canonical import (
    MAXIMUM_AUTHENTICATED_EVIDENCE_BUNDLE_CANONICAL_BYTES,
    MMI_AUTHENTICATED_EVIDENCE_BUNDLE_IDENTITY_DOMAIN,
    MmiCanonicalizationError,
    record_identity_sha256,
)


AUTHORITY_EFFECT_NONE: Final = "NONE"
CANONICAL_UTC_TIMESTAMP_FORMAT: Final = "%Y-%m-%dT%H:%M:%S.%fZ"
MMI_AUTHENTICATED_EVIDENCE_BUNDLE_SCHEMA_VERSION: Final = (
    "mmi_authenticated_evidence_bundle_v1"
)
MMI_AUTHENTICATED_EVIDENCE_BUNDLE_ARTIFACT_KIND: Final = (
    "MMI_AUTHENTICATED_EVIDENCE_BUNDLE"
)
MMI_EVIDENCE_POLICY_COMPONENT_PRESENCE_STATUS: Final = (
    "PRESENT_SOURCE_BOUND_VALIDATED"
)
MMI_EVIDENCE_PORTFOLIO_NOT_SUPPLIED_STATUS: Final = "NOT_SUPPLIED"
MMI_EVIDENCE_PORTFOLIO_SOURCE_ABSENT_STATUS: Final = (
    "PRESENT_VALIDATED_SOURCE_ABSENT"
)
MMI_EVIDENCE_PORTFOLIO_SOURCE_BOUND_STATUS: Final = (
    "PRESENT_SOURCE_BOUND_VALIDATED"
)
MMI_EVIDENCE_PORTFOLIO_NOT_SUPPLIED_GAP_CODE: Final = (
    "EVIDENCE_PORTFOLIO_COMPONENT_NOT_SUPPLIED"
)
MMI_EVIDENCE_ASSEMBLY_GAP_SCOPE: Final = "EVIDENCE_ASSEMBLY"
MMI_EVIDENCE_PORTFOLIO_GAP_COMPONENT: Final = "PORTFOLIO_PROJECTION"
_MMI_RUN_CONTEXT_PROVENANCE_KEY: Final = secrets.token_bytes(32)
_MMI_CAPTURED_SOURCE_PROVENANCE_KEY: Final = secrets.token_bytes(32)
_MMI_RUN_CONTEXT_PROVENANCE_INSTANCES: Final[dict[bytes, object]] = {}
_MMI_CAPTURED_SOURCE_PROVENANCE_INSTANCES: Final[
    dict[bytes, object]
] = {}
_MMI_RUN_CONTEXT_PROVENANCE_DOMAIN: Final = (
    b"mmi_projection_run_context_provenance_v1\0"
)
_MMI_CAPTURED_SOURCE_PROVENANCE_DOMAIN: Final = (
    b"mmi_captured_source_provenance_v1\0"
)


class MmiClock(Protocol):
    """Internal clock boundary used to make projection-run time testable."""

    def now_utc(self) -> datetime:
        """Return one timezone-aware UTC timestamp."""


class MmiClockContractError(ValueError):
    """Raised when the code-owned run clock violates its frozen contract."""


@dataclass(frozen=True, slots=True, init=False)
class MmiProjectionRunContext:
    """One immutable code-owned evaluation time shared by one projection run."""

    evaluation_time_utc: datetime
    evaluation_timestamp_utc: str
    authority_effect: str
    _provenance_token: bytes = field(repr=False, compare=False)
    _provenance_seal: bytes = field(repr=False, compare=False)

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        raise TypeError(
            "MmiProjectionRunContext is created only by an MMI clock factory."
        )

    def __copy__(self) -> MmiProjectionRunContext:
        return self

    def __deepcopy__(
        self,
        _memo: dict[int, object],
    ) -> MmiProjectionRunContext:
        return self


class _SystemUtcClock:
    __slots__ = ()

    def now_utc(self) -> datetime:
        return datetime.now(timezone.utc)


def _provenance_json_bytes(value: Mapping[str, object]) -> bytes:
    try:
        return json.dumps(
            dict(value),
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("ascii")
    except (TypeError, ValueError, UnicodeEncodeError):
        return b""


def _run_context_provenance_seal(
    *,
    evaluation_time_utc: datetime,
    evaluation_timestamp_utc: str,
    authority_effect: str,
    provenance_token: bytes,
) -> bytes:
    if (
        type(evaluation_time_utc) is not datetime
        or type(provenance_token) is not bytes
        or len(provenance_token) != 32
    ):
        return b""
    payload = _provenance_json_bytes(
        {
            "authority_effect": authority_effect,
            "evaluation_time_isoformat": evaluation_time_utc.isoformat(
                timespec="microseconds"
            ),
            "evaluation_timestamp_utc": evaluation_timestamp_utc,
            "provenance_token_sha256": hashlib.sha256(
                provenance_token
            ).hexdigest(),
        }
    )
    if not payload:
        return b""
    return hmac.new(
        _MMI_RUN_CONTEXT_PROVENANCE_KEY,
        _MMI_RUN_CONTEXT_PROVENANCE_DOMAIN + payload,
        hashlib.sha256,
    ).digest()


def _new_mmi_projection_run_context(
    *,
    evaluation_time_utc: datetime,
    evaluation_timestamp_utc: str,
) -> MmiProjectionRunContext:
    provenance_token = secrets.token_bytes(32)
    while provenance_token in _MMI_RUN_CONTEXT_PROVENANCE_INSTANCES:
        provenance_token = secrets.token_bytes(32)
    instance = object.__new__(MmiProjectionRunContext)
    object.__setattr__(instance, "evaluation_time_utc", evaluation_time_utc)
    object.__setattr__(
        instance,
        "evaluation_timestamp_utc",
        evaluation_timestamp_utc,
    )
    object.__setattr__(
        instance,
        "authority_effect",
        AUTHORITY_EFFECT_NONE,
    )
    object.__setattr__(
        instance,
        "_provenance_token",
        provenance_token,
    )
    object.__setattr__(
        instance,
        "_provenance_seal",
        _run_context_provenance_seal(
            evaluation_time_utc=evaluation_time_utc,
            evaluation_timestamp_utc=evaluation_timestamp_utc,
            authority_effect=AUTHORITY_EFFECT_NONE,
            provenance_token=provenance_token,
        ),
    )
    _MMI_RUN_CONTEXT_PROVENANCE_INSTANCES[provenance_token] = instance
    return instance


def _mmi_projection_run_context_provenance_is_valid(
    value: object,
) -> bool:
    if type(value) is not MmiProjectionRunContext:
        return False
    try:
        provenance_token = value._provenance_token
        if (
            type(provenance_token) is not bytes
            or _MMI_RUN_CONTEXT_PROVENANCE_INSTANCES.get(
                provenance_token
            )
            is not value
        ):
            return False
        seal = value._provenance_seal
        expected = _run_context_provenance_seal(
            evaluation_time_utc=value.evaluation_time_utc,
            evaluation_timestamp_utc=value.evaluation_timestamp_utc,
            authority_effect=value.authority_effect,
            provenance_token=provenance_token,
        )
    except (AttributeError, TypeError, ValueError):
        return False
    return (
        type(seal) is bytes
        and len(seal) == hashlib.sha256().digest_size
        and len(expected) == hashlib.sha256().digest_size
        and hmac.compare_digest(seal, expected)
    )


def _begin_mmi_projection_run_with_clock(
    clock: MmiClock,
) -> MmiProjectionRunContext:
    """Build a run context from one internal clock read."""
    try:
        observed = clock.now_utc()
    except Exception as exc:
        raise MmiClockContractError("MMI_CLOCK_READ_FAILED") from None
    if type(observed) is not datetime:
        raise MmiClockContractError("MMI_CLOCK_RESULT_INVALID")
    if observed.tzinfo is None or observed.utcoffset() is None:
        raise MmiClockContractError("MMI_CLOCK_TIMESTAMP_NAIVE")
    if observed.utcoffset() != timedelta(0):
        raise MmiClockContractError("MMI_CLOCK_TIMESTAMP_NOT_UTC")
    normalized = observed.astimezone(timezone.utc)
    canonical = normalized.strftime(CANONICAL_UTC_TIMESTAMP_FORMAT)
    return _new_mmi_projection_run_context(
        evaluation_time_utc=normalized,
        evaluation_timestamp_utc=canonical,
    )


def begin_mmi_projection_run() -> MmiProjectionRunContext:
    """Read the code-owned UTC clock exactly once for an MMI projection run."""
    return _begin_mmi_projection_run_with_clock(_SystemUtcClock())


class MmiSourceRole(str, Enum):
    """Closed source roles reserved across MMI-P1a and MMI-P1b."""

    STRATEGY_SETTINGS = "STRATEGY_SETTINGS"
    PORTFOLIO_SNAPSHOT = "PORTFOLIO_SNAPSHOT"


@dataclass(frozen=True, slots=True)
class MmiSourceSpec:
    """One immutable code-owned local source locator."""

    role: MmiSourceRole
    source_id: str
    path_components: tuple[str, ...]
    repository_relative_locator: PurePosixPath
    maximum_bytes: int


_STRATEGY_SETTINGS_SPEC: Final = MmiSourceSpec(
    role=MmiSourceRole.STRATEGY_SETTINGS,
    source_id="MMI_STRATEGY_SETTINGS",
    path_components=("inputs", "current", "strategy_settings.yaml"),
    repository_relative_locator=PurePosixPath(
        "inputs/current/strategy_settings.yaml"
    ),
    maximum_bytes=262_144,
)
_PORTFOLIO_SNAPSHOT_SPEC: Final = MmiSourceSpec(
    role=MmiSourceRole.PORTFOLIO_SNAPSHOT,
    source_id="MMI_PORTFOLIO_SNAPSHOT",
    path_components=("inputs", "current", "portfolio_snapshot.txt"),
    repository_relative_locator=PurePosixPath(
        "inputs/current/portfolio_snapshot.txt"
    ),
    maximum_bytes=1_048_576,
)

MMI_SOURCE_CATALOG: Final[Mapping[MmiSourceRole, MmiSourceSpec]] = (
    MappingProxyType(
        {
            MmiSourceRole.STRATEGY_SETTINGS: _STRATEGY_SETTINGS_SPEC,
            MmiSourceRole.PORTFOLIO_SNAPSHOT: _PORTFOLIO_SNAPSHOT_SPEC,
        }
    )
)


class MmiProjectionResultCategory(str, Enum):
    """Closed report-only build and validation result categories."""

    PROJECTION_VALID_COMPLETE = "PROJECTION_VALID_COMPLETE"
    PROJECTION_VALID_WITH_GAPS = "PROJECTION_VALID_WITH_GAPS"
    PROJECTION_BLOCKED = "PROJECTION_BLOCKED"
    PROJECTION_CONTRACT_FAILURE = "PROJECTION_CONTRACT_FAILURE"


_EVIDENCE_TOP_LEVEL_FIELDS: Final = frozenset(
    {
        "schema_version",
        "artifact_kind",
        "report_only",
        "authority_effect",
        "evaluation_timestamp_utc",
        "policy_component",
        "portfolio_component",
        "known_evidence_gaps",
        "evidence_completeness_status",
        "evidence_bundle_identity_sha256",
    }
)
_EVIDENCE_POLICY_COMPONENT_FIELDS: Final = frozenset(
    {
        "presence_status",
        "strategy_source_schema_version",
        "strategy_source_role",
        "strategy_source_record_identity_sha256",
        "universe_schema_version",
        "universe_artifact_kind",
        "universe_projection_identity_sha256",
        "policy_schema_version",
        "policy_artifact_kind",
        "policy_projection_identity_sha256",
        "validation_result_category",
    }
)
_EVIDENCE_PORTFOLIO_SOURCE_ABSENT_FIELDS: Final = frozenset(
    {
        "presence_status",
        "portfolio_schema_version",
        "portfolio_artifact_kind",
        "portfolio_projection_identity_sha256",
        "policy_projection_identity_sha256",
        "portfolio_source_status",
        "validation_result_category",
    }
)
_EVIDENCE_PORTFOLIO_SOURCE_BOUND_FIELDS: Final = frozenset(
    {
        *_EVIDENCE_PORTFOLIO_SOURCE_ABSENT_FIELDS,
        "portfolio_source_schema_version",
        "portfolio_source_role",
        "portfolio_source_record_identity_sha256",
    }
)
_EVIDENCE_GAP_FIELDS: Final = frozenset(
    {
        "code",
        "scope",
        "component",
    }
)
_LOWER_HEX_CHARACTERS: Final = frozenset("0123456789abcdef")


def _evidence_bundle_contract_failure() -> NoReturn:
    raise MmiCanonicalizationError(
        "MMI_AUTHENTICATED_EVIDENCE_BUNDLE_CONTRACT_INVALID"
    )


def _require_exact_dict(
    value: object,
    expected_fields: frozenset[str],
) -> dict[str, object]:
    if type(value) is not dict or set(value) != expected_fields:
        _evidence_bundle_contract_failure()
    return value


def _is_sha256(value: object) -> bool:
    return (
        type(value) is str
        and len(value) == 64
        and set(value) <= _LOWER_HEX_CHARACTERS
    )


def _require_sha256(value: object) -> None:
    if not _is_sha256(value):
        _evidence_bundle_contract_failure()


def _require_canonical_utc_timestamp(value: object) -> None:
    if type(value) is not str or len(value) != 27:
        _evidence_bundle_contract_failure()
    try:
        parsed = datetime.strptime(
            value,
            CANONICAL_UTC_TIMESTAMP_FORMAT,
        )
    except ValueError:
        _evidence_bundle_contract_failure()
    if parsed.strftime(CANONICAL_UTC_TIMESTAMP_FORMAT) != value:
        _evidence_bundle_contract_failure()


def _validate_evidence_policy_component(
    value: object,
) -> dict[str, object]:
    component = _require_exact_dict(
        value,
        _EVIDENCE_POLICY_COMPONENT_FIELDS,
    )
    expected_constants = {
        "presence_status": (
            MMI_EVIDENCE_POLICY_COMPONENT_PRESENCE_STATUS
        ),
        "strategy_source_schema_version": "mmi_source_record_v1",
        "strategy_source_role": MmiSourceRole.STRATEGY_SETTINGS.value,
        "universe_schema_version": "mmi_universe_projection_v1",
        "universe_artifact_kind": "MMI_UNIVERSE_PROJECTION",
        "policy_schema_version": "mmi_policy_projection_v1",
        "policy_artifact_kind": "MMI_POLICY_PROJECTION",
        "validation_result_category": (
            MmiProjectionResultCategory.PROJECTION_VALID_WITH_GAPS.value
        ),
    }
    if any(
        component.get(field) != expected
        for field, expected in expected_constants.items()
    ):
        _evidence_bundle_contract_failure()
    for field in (
        "strategy_source_record_identity_sha256",
        "universe_projection_identity_sha256",
        "policy_projection_identity_sha256",
    ):
        _require_sha256(component.get(field))
    return component


def _validate_evidence_portfolio_component(
    value: object,
    *,
    policy_identity: object,
) -> str:
    if type(value) is not dict:
        _evidence_bundle_contract_failure()
    presence_status = value.get("presence_status")
    if presence_status == MMI_EVIDENCE_PORTFOLIO_NOT_SUPPLIED_STATUS:
        _require_exact_dict(value, frozenset({"presence_status"}))
        return presence_status

    if presence_status == MMI_EVIDENCE_PORTFOLIO_SOURCE_ABSENT_STATUS:
        component = _require_exact_dict(
            value,
            _EVIDENCE_PORTFOLIO_SOURCE_ABSENT_FIELDS,
        )
        expected_source_status = "SOURCE_ABSENT"
    elif presence_status == MMI_EVIDENCE_PORTFOLIO_SOURCE_BOUND_STATUS:
        component = _require_exact_dict(
            value,
            _EVIDENCE_PORTFOLIO_SOURCE_BOUND_FIELDS,
        )
        expected_source_status = "SOURCE_PRESENT_CONTENT_BOUND"
        if (
            component.get("portfolio_source_schema_version")
            != "mmi_source_record_v1"
            or component.get("portfolio_source_role")
            != MmiSourceRole.PORTFOLIO_SNAPSHOT.value
        ):
            _evidence_bundle_contract_failure()
        _require_sha256(
            component.get("portfolio_source_record_identity_sha256")
        )
    else:
        _evidence_bundle_contract_failure()

    if (
        component.get("portfolio_schema_version")
        != "mmi_portfolio_snapshot_projection_v1"
        or component.get("portfolio_artifact_kind")
        != "MMI_PORTFOLIO_SNAPSHOT_PROJECTION"
        or component.get("portfolio_source_status")
        != expected_source_status
        or component.get("validation_result_category")
        != (
            MmiProjectionResultCategory.PROJECTION_VALID_WITH_GAPS.value
        )
        or component.get("policy_projection_identity_sha256")
        != policy_identity
    ):
        _evidence_bundle_contract_failure()
    _require_sha256(
        component.get("portfolio_projection_identity_sha256")
    )
    _require_sha256(
        component.get("policy_projection_identity_sha256")
    )
    return presence_status


def _validate_evidence_gaps(
    value: object,
    *,
    portfolio_presence_status: str,
) -> None:
    if type(value) is not list:
        _evidence_bundle_contract_failure()
    if (
        portfolio_presence_status
        == MMI_EVIDENCE_PORTFOLIO_NOT_SUPPLIED_STATUS
    ):
        if len(value) != 1:
            _evidence_bundle_contract_failure()
        gap = _require_exact_dict(value[0], _EVIDENCE_GAP_FIELDS)
        if gap != {
            "code": MMI_EVIDENCE_PORTFOLIO_NOT_SUPPLIED_GAP_CODE,
            "scope": MMI_EVIDENCE_ASSEMBLY_GAP_SCOPE,
            "component": MMI_EVIDENCE_PORTFOLIO_GAP_COMPONENT,
        }:
            _evidence_bundle_contract_failure()
    elif value:
        _evidence_bundle_contract_failure()


def mmi_authenticated_evidence_bundle_identity_sha256(
    value: Mapping[str, object],
) -> str:
    """Calculate structural bundle identity without authenticating inputs."""
    if not isinstance(value, Mapping):
        _evidence_bundle_contract_failure()
    try:
        manifest = dict(value)
    except (TypeError, ValueError):
        _evidence_bundle_contract_failure()
    if set(manifest) != _EVIDENCE_TOP_LEVEL_FIELDS:
        _evidence_bundle_contract_failure()
    if (
        manifest.get("schema_version")
        != MMI_AUTHENTICATED_EVIDENCE_BUNDLE_SCHEMA_VERSION
        or manifest.get("artifact_kind")
        != MMI_AUTHENTICATED_EVIDENCE_BUNDLE_ARTIFACT_KIND
        or manifest.get("report_only") is not True
        or manifest.get("authority_effect") != AUTHORITY_EFFECT_NONE
        or manifest.get("evidence_completeness_status")
        != (
            MmiProjectionResultCategory.PROJECTION_VALID_WITH_GAPS.value
        )
    ):
        _evidence_bundle_contract_failure()
    _require_canonical_utc_timestamp(
        manifest.get("evaluation_timestamp_utc")
    )
    policy_component = _validate_evidence_policy_component(
        manifest.get("policy_component")
    )
    portfolio_presence_status = (
        _validate_evidence_portfolio_component(
            manifest.get("portfolio_component"),
            policy_identity=policy_component.get(
                "policy_projection_identity_sha256"
            ),
        )
    )
    _validate_evidence_gaps(
        manifest.get("known_evidence_gaps"),
        portfolio_presence_status=portfolio_presence_status,
    )
    _require_sha256(manifest.get("evidence_bundle_identity_sha256"))
    return record_identity_sha256(
        manifest,
        identity_field="evidence_bundle_identity_sha256",
        domain=MMI_AUTHENTICATED_EVIDENCE_BUNDLE_IDENTITY_DOMAIN,
        maximum_bytes=(
            MAXIMUM_AUTHENTICATED_EVIDENCE_BUNDLE_CANONICAL_BYTES
        ),
    )


@dataclass(frozen=True, slots=True, init=False)
class MmiCapturedSource:
    """Exact source bytes and their closed, identity-bound source record."""

    role: MmiSourceRole
    raw_bytes: bytes
    source_record: Mapping[str, object]
    _provenance_token: bytes = field(repr=False, compare=False)
    _provenance_seal: bytes = field(repr=False, compare=False)

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        raise TypeError(
            "MmiCapturedSource is created only by fixed-role source capture."
        )

    def __copy__(self) -> MmiCapturedSource:
        return self

    def __deepcopy__(self, _memo: dict[int, object]) -> MmiCapturedSource:
        return self


def _captured_source_provenance_seal(
    *,
    role: MmiSourceRole,
    raw_bytes: bytes,
    source_record: Mapping[str, object],
    provenance_token: bytes,
) -> bytes:
    if (
        type(role) is not MmiSourceRole
        or type(raw_bytes) is not bytes
        or type(provenance_token) is not bytes
        or len(provenance_token) != 32
    ):
        return b""
    try:
        record = dict(source_record)
    except (TypeError, ValueError):
        return b""
    record_bytes = _provenance_json_bytes(record)
    if not record_bytes:
        return b""
    payload = _provenance_json_bytes(
        {
            "expected_sha256": record.get("expected_sha256"),
            "maximum_bytes": record.get("maximum_bytes"),
            "observed_sha256": hashlib.sha256(raw_bytes).hexdigest(),
            "observed_size_bytes": len(raw_bytes),
            "provenance_token_sha256": hashlib.sha256(
                provenance_token
            ).hexdigest(),
            "record_sha256": hashlib.sha256(record_bytes).hexdigest(),
            "regular_file_status": record.get("regular_file_status"),
            "repository_relative_locator": record.get(
                "repository_relative_locator"
            ),
            "role": role.value,
            "source_id": record.get("source_id"),
            "source_record_identity_sha256": record.get(
                "source_record_identity_sha256"
            ),
            "stable_read_status": record.get("stable_read_status"),
        }
    )
    if not payload:
        return b""
    return hmac.new(
        _MMI_CAPTURED_SOURCE_PROVENANCE_KEY,
        _MMI_CAPTURED_SOURCE_PROVENANCE_DOMAIN + payload,
        hashlib.sha256,
    ).digest()


def _create_mmi_captured_source(
    *,
    role: MmiSourceRole,
    raw_bytes: bytes,
    source_record: Mapping[str, object],
) -> MmiCapturedSource:
    provenance_token = secrets.token_bytes(32)
    while provenance_token in _MMI_CAPTURED_SOURCE_PROVENANCE_INSTANCES:
        provenance_token = secrets.token_bytes(32)
    record = MappingProxyType(dict(source_record))
    instance = object.__new__(MmiCapturedSource)
    object.__setattr__(instance, "role", role)
    object.__setattr__(instance, "raw_bytes", raw_bytes)
    object.__setattr__(instance, "source_record", record)
    object.__setattr__(
        instance,
        "_provenance_token",
        provenance_token,
    )
    object.__setattr__(
        instance,
        "_provenance_seal",
        _captured_source_provenance_seal(
            role=role,
            raw_bytes=raw_bytes,
            source_record=record,
            provenance_token=provenance_token,
        ),
    )
    _MMI_CAPTURED_SOURCE_PROVENANCE_INSTANCES[
        provenance_token
    ] = instance
    return instance


def _mmi_captured_source_provenance_is_valid(value: object) -> bool:
    if type(value) is not MmiCapturedSource:
        return False
    try:
        provenance_token = value._provenance_token
        if (
            type(provenance_token) is not bytes
            or _MMI_CAPTURED_SOURCE_PROVENANCE_INSTANCES.get(
                provenance_token
            )
            is not value
        ):
            return False
        seal = value._provenance_seal
        expected = _captured_source_provenance_seal(
            role=value.role,
            raw_bytes=value.raw_bytes,
            source_record=value.source_record,
            provenance_token=provenance_token,
        )
    except (AttributeError, TypeError, ValueError):
        return False
    return (
        type(seal) is bytes
        and len(seal) == hashlib.sha256().digest_size
        and len(expected) == hashlib.sha256().digest_size
        and hmac.compare_digest(seal, expected)
    )


@dataclass(frozen=True, slots=True)
class MmiSourceCaptureResult:
    """No-write outcome of one closed-role source capture."""

    status: MmiProjectionResultCategory
    authority_effect: str
    reason_codes: tuple[str, ...]
    source: MmiCapturedSource | None

    @property
    def valid(self) -> bool:
        return self.status in {
            MmiProjectionResultCategory.PROJECTION_VALID_COMPLETE,
            MmiProjectionResultCategory.PROJECTION_VALID_WITH_GAPS,
        }


@dataclass(frozen=True, slots=True)
class MmiPolicyProjectionBuildResult:
    """No-write outcome of policy/universe projection construction."""

    status: MmiProjectionResultCategory
    authority_effect: str
    reason_codes: tuple[str, ...]
    projection: Mapping[str, object] | None

    @property
    def valid(self) -> bool:
        return self.status in {
            MmiProjectionResultCategory.PROJECTION_VALID_COMPLETE,
            MmiProjectionResultCategory.PROJECTION_VALID_WITH_GAPS,
        }


@dataclass(frozen=True, slots=True)
class MmiPolicyProjectionValidationResult:
    """Closed validation result for an in-memory MMI policy projection."""

    status: MmiProjectionResultCategory
    authority_effect: str
    reason_codes: tuple[str, ...]

    @property
    def valid(self) -> bool:
        return self.status in {
            MmiProjectionResultCategory.PROJECTION_VALID_COMPLETE,
            MmiProjectionResultCategory.PROJECTION_VALID_WITH_GAPS,
        }


@dataclass(frozen=True, slots=True)
class MmiPortfolioProjectionBuildResult:
    """No-write outcome of portfolio snapshot projection construction."""

    status: MmiProjectionResultCategory
    authority_effect: str
    reason_codes: tuple[str, ...]
    projection: Mapping[str, object] | None

    @property
    def valid(self) -> bool:
        return self.status in {
            MmiProjectionResultCategory.PROJECTION_VALID_COMPLETE,
            MmiProjectionResultCategory.PROJECTION_VALID_WITH_GAPS,
        }


@dataclass(frozen=True, slots=True)
class MmiPortfolioProjectionValidationResult:
    """Source-bound validation result for an in-memory portfolio projection."""

    status: MmiProjectionResultCategory
    authority_effect: str
    reason_codes: tuple[str, ...]

    @property
    def valid(self) -> bool:
        return self.status in {
            MmiProjectionResultCategory.PROJECTION_VALID_COMPLETE,
            MmiProjectionResultCategory.PROJECTION_VALID_WITH_GAPS,
        }
