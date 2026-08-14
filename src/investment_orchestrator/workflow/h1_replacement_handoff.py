"""Restart-safe manual H1 prepare/consume orchestration (report-only).

Two foreground engines share one code-owned current directory,
``artifacts/current/h1_replacement``:

``prepare_h1_replacement_handoff``
    Captures the exact current MMI sources (or proves the portfolio source
    genuinely absent), drives the existing deterministic chain to the complete
    ``mmi_grounded_prompt_v2`` artifact, freezes it into one
    ``mmi_h1_prepared_handoff_v1`` envelope, and publishes that envelope last
    as the sole preparation completion claim.

``consume_h1_replacement_handoff``
    Reads that envelope back under an operator-supplied expected identity,
    resumes the prepared evaluation time through the current generic
    resumption owner, re-proves source continuity, rebuilds the complete
    grounded prompt and requires exact equality with the prepared one, and
    only then acquires the operator-placed raw response bytes exactly once.
    It ends by constructing validated ``H1MappedRecognitionFacts`` in memory
    and publishing the H1 mapping report last.

The handoff between them is entirely manual: an operator copies the emitted
prompt to an LLM of their choosing and writes the exact raw response bytes to
the code-owned ``h1_response.raw`` leaf.  Nothing here calls a provider, SDK,
network endpoint, browser, agent, poller, or retry loop, and no response path
is operator-configurable.

H1 remains dormant.  These engines return facts and write report-only
artifacts; they wire no availability, mint no permission, change no freshness,
gate, publication, pointer, order, or broker behavior, and never persist the
mapped recognition facts they construct.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
import errno
import json
import os
from pathlib import Path
import re
import stat
from typing import Final, NoReturn

from investment_orchestrator.common.paths import artifacts_dir
from investment_orchestrator.mmi import analyst_visible_evidence_view_v2 as _view_v2
from investment_orchestrator.mmi import evidence_bundle as _evidence_bundle
from investment_orchestrator.mmi import grounded_prompt_v2 as _grounded_prompt_v2
from investment_orchestrator.mmi import (
    legacy_step1_compatibility_candidate_v1 as _candidate_v1,
)
from investment_orchestrator.mmi import (
    mmi_h1_legacy_step1_mapping_report_v1 as _mapping_report_v1,
)
from investment_orchestrator.mmi import mmi_h1_prepared_handoff_v1 as _prepared_handoff
from investment_orchestrator.mmi import policy_projection as _policy_projection
from investment_orchestrator.mmi import portfolio_projection as _portfolio_projection
from investment_orchestrator.mmi import raw_response_envelope_v2 as _raw_response_v2
from investment_orchestrator.mmi import source_capture as _source_capture
from investment_orchestrator.mmi import (
    validated_grounded_analysis_response_v2 as _validated_response_v2,
)
from investment_orchestrator.mmi.canonical import (
    MAXIMUM_MMI_RAW_RESPONSE_BYTES,
    MAX_MMI_H1_LEGACY_STEP1_MAPPING_REPORT_V1_CANONICAL_BYTES,
    MAX_MMI_H1_PREPARED_HANDOFF_V1_CANONICAL_BYTES,
    MmiCanonicalizationError,
    canonical_json_bytes,
)
from investment_orchestrator.mmi.contracts import (
    AUTHORITY_EFFECT_NONE,
    MmiCapturedSource,
    MmiClockContractError,
    MmiProjectionRunContext,
    MmiSourceRole,
    begin_mmi_projection_run,
)
from investment_orchestrator.common.stable_read import (
    MmiStableReadError,
    MmiStableReadErrorCode,
    stable_read_exact_bytes,
)
from investment_orchestrator.research.h1_mapped_recognition import (
    H1MappedRecognitionError,
    H1MappedRecognitionFacts,
    build_validated_h1_mapped_recognition_facts,
)


__all__ = (
    "H1ConsumeResult",
    "H1PrepareResult",
    "H1QualitativeInstrumentView",
    "H1QualitativeResearchFacts",
    "H1ReplacementHandoffError",
    "H1ReplacementHandoffErrorCode",
    "PORTFOLIO_SNAPSHOT_PRESENT",
    "PORTFOLIO_SNAPSHOT_PROVEN_ABSENT",
    "consume_h1_replacement_handoff",
    "prepare_h1_replacement_handoff",
)


class H1ReplacementHandoffErrorCode(str, Enum):
    """Closed operator-facing failure codes for both engines."""

    H1_HANDOFF_ARGUMENT_INVALID = "H1_HANDOFF_ARGUMENT_INVALID"
    H1_HANDOFF_CAPABILITY_UNAVAILABLE = "H1_HANDOFF_CAPABILITY_UNAVAILABLE"
    H1_HANDOFF_PATH_CONTRACT_INVALID = "H1_HANDOFF_PATH_CONTRACT_INVALID"
    H1_HANDOFF_SOURCE_CAPTURE_INVALID = "H1_HANDOFF_SOURCE_CAPTURE_INVALID"
    H1_HANDOFF_PORTFOLIO_PRESENCE_INVALID = (
        "H1_HANDOFF_PORTFOLIO_PRESENCE_INVALID"
    )
    H1_HANDOFF_LIVE_CHAIN_INVALID = "H1_HANDOFF_LIVE_CHAIN_INVALID"
    H1_HANDOFF_PROMPT_CONTRACT_INVALID = "H1_HANDOFF_PROMPT_CONTRACT_INVALID"
    H1_HANDOFF_PREPARED_HANDOFF_INVALID = (
        "H1_HANDOFF_PREPARED_HANDOFF_INVALID"
    )
    H1_HANDOFF_PROMPT_CONTINUITY_INVALID = (
        "H1_HANDOFF_PROMPT_CONTINUITY_INVALID"
    )
    H1_HANDOFF_RESPONSE_INPUT_INVALID = "H1_HANDOFF_RESPONSE_INPUT_INVALID"
    H1_HANDOFF_RESPONSE_CONTENT_INVALID = (
        "H1_HANDOFF_RESPONSE_CONTENT_INVALID"
    )
    H1_HANDOFF_MAPPING_INVALID = "H1_HANDOFF_MAPPING_INVALID"
    H1_HANDOFF_FACTS_INVALID = "H1_HANDOFF_FACTS_INVALID"
    H1_HANDOFF_PERSISTENCE_FAILED = "H1_HANDOFF_PERSISTENCE_FAILED"


class H1ReplacementHandoffError(RuntimeError):
    """Raised when neither engine can complete its report-only work."""

    def __init__(
        self,
        code: H1ReplacementHandoffErrorCode,
        *,
        owner_reason_codes: tuple[str, ...] = (),
    ) -> None:
        super().__init__(code.value)
        self.code = code
        self.owner_reason_codes = owner_reason_codes


PORTFOLIO_SNAPSHOT_PRESENT: Final = "PRESENT"
PORTFOLIO_SNAPSHOT_PROVEN_ABSENT: Final = "PROVEN_ABSENT"

_PREPARE_WORKFLOW_STATUS: Final = "AWAITING_OPERATOR_RESPONSE"
_CONSUME_WORKFLOW_STATUS: Final = "COMPLETED"

_CURRENT_DIRECTORY: Final = "current"
_H1_REPLACEMENT_DIRECTORY: Final = "h1_replacement"
_PREPARED_HANDOFF_LEAF: Final = "h1_prepared_handoff.json"
_RESPONSE_LEAF: Final = "h1_response.raw"
_MAPPING_REPORT_LEAF: Final = "h1_legacy_step1_mapping_report.json"
_PUBLISH_TEMPORARY_SUFFIX: Final = ".publish.tmp"
# Consume invalidates only the mapping completion; prepare additionally
# invalidates the prior prepared handoff, in exactly this order.
_PREPARE_INVALIDATION_ORDER: Final = (
    _MAPPING_REPORT_LEAF,
    _PREPARED_HANDOFF_LEAF,
)
_CONSUME_INVALIDATION_ORDER: Final = (_MAPPING_REPORT_LEAF,)

_DIRECTORY_MODE: Final = 0o700
_FILE_MODE: Final = 0o600
_SHA256_RE: Final = re.compile(r"^[0-9a-f]{64}$")
_GROUNDED_PROMPT_MAXIMUM_CANONICAL_BYTES: Final = 393_852
_CONFIRMED_ABSENT: Final = "MMI_SOURCE_CONFIRMED_ABSENT"
_CLOCK_CODES: Final = frozenset(
    {
        "MMI_CLOCK_UNAVAILABLE",
        "MMI_CLOCK_NOT_UTC",
        "MMI_CLOCK_NOT_MONOTONIC_SAFE",
    }
)
_CONTROLLED_PERSISTENCE_ERRNOS: Final = frozenset(
    {
        errno.EACCES,
        errno.EDQUOT,
        errno.EEXIST,
        errno.EIO,
        errno.EISDIR,
        errno.ELOOP,
        errno.ENAMETOOLONG,
        errno.ENOENT,
        errno.ENOSPC,
        errno.ENOTDIR,
        errno.EPERM,
        errno.EROFS,
    }
)


@dataclass(frozen=True, slots=True)
class H1PrepareResult:
    """Report-only outcome of one prepared-handoff publication."""

    workflow_status: str
    prepared_handoff_identity_sha256: str
    portfolio_snapshot_presence: str
    prompt_text: str


@dataclass(frozen=True, slots=True)
class H1QualitativeInstrumentView:
    """One already-validated per-instrument qualitative research row.

    Every field is copied unchanged from the response object the existing
    grounded-response validator already accepted; this type performs no
    parsing, re-validation, or interpretation of the qualitative text.
    """

    ticker: str
    evidence_status: str
    rationale_12m_plus: str | None
    references: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class H1QualitativeResearchFacts:
    """Already-validated H1 qualitative research, exposed unchanged.

    Carries no availability, selection, or admission meaning: this module
    never wires it into any availability, permission, gate, publication, or
    order surface, and building it proves nothing about whether mapped H1
    research is ever selected by a future availability owner.

    ``validated_grounded_analysis_response_identity_sha256`` is a
    run-instance identity — it transitively binds this consume run's
    evaluation timestamp.  It is kept here only so a future same-run
    consumer can prove this projection and this result's
    ``mapped_recognition_facts`` came from the same consume run; it is never
    a stable cross-run content, comparability, or currentness identity.
    """

    analysis_status: str
    instrument_views: tuple[H1QualitativeInstrumentView, ...]
    validated_grounded_analysis_response_identity_sha256: str


@dataclass(frozen=True, slots=True)
class H1ConsumeResult:
    """Report-only outcome of one consumed prepared handoff.

    ``mapped_recognition_facts`` and ``qualitative_research_facts`` are
    returned in memory for a future explicitly reviewed availability /
    Phase-3 owner.  This module never persists either and never passes
    either to any availability, permission, gate, publication, or order
    surface.
    """

    workflow_status: str
    prepared_handoff_identity_sha256: str
    mapping_report_identity_sha256: str
    portfolio_snapshot_presence: str
    mapped_recognition_facts: H1MappedRecognitionFacts
    qualitative_research_facts: H1QualitativeResearchFacts


# --------------------------------------------------------------------------
# Controlled failures.
# --------------------------------------------------------------------------
def _raise(
    code: H1ReplacementHandoffErrorCode,
    *,
    owner_reason_codes: tuple[str, ...] = (),
) -> NoReturn:
    raise H1ReplacementHandoffError(code, owner_reason_codes=owner_reason_codes)


def _owner_reason_codes(value: object) -> tuple[str, ...]:
    reasons = getattr(value, "reason_codes", ())
    if type(reasons) is not tuple or any(
        type(code) is not str for code in reasons
    ):
        return ()
    return reasons


# --------------------------------------------------------------------------
# Code-owned current directory and its fixed leaves.
# --------------------------------------------------------------------------
def _handoff_directory() -> Path:
    """Resolve the single code-owned H1 replacement directory.

    There is deliberately no operator argument for this location, for the
    prepared handoff, for the response leaf, or for the mapping report.
    """
    return artifacts_dir() / _CURRENT_DIRECTORY / _H1_REPLACEMENT_DIRECTORY


def _require_filesystem_capabilities() -> None:
    if (
        not hasattr(os, "O_DIRECTORY")
        or not hasattr(os, "O_NOFOLLOW")
        or not hasattr(os, "O_CLOEXEC")
        or os.open not in os.supports_dir_fd
        or os.stat not in os.supports_dir_fd
        or os.unlink not in os.supports_dir_fd
        or os.rename not in os.supports_dir_fd
    ):
        _raise(
            H1ReplacementHandoffErrorCode.H1_HANDOFF_CAPABILITY_UNAVAILABLE
        )


def _open_handoff_directory(*, create: bool) -> int:
    directory = _handoff_directory()
    if create:
        try:
            directory.mkdir(mode=_DIRECTORY_MODE, parents=True, exist_ok=True)
        except OSError:
            _raise(
                H1ReplacementHandoffErrorCode.H1_HANDOFF_PERSISTENCE_FAILED
            )
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW
    try:
        directory_fd = os.open(os.fspath(directory), flags)
    except OSError:
        _raise(H1ReplacementHandoffErrorCode.H1_HANDOFF_PATH_CONTRACT_INVALID)
    try:
        if not stat.S_ISDIR(os.fstat(directory_fd).st_mode):
            raise OSError(errno.ENOTDIR, "not a directory")
    except OSError:
        os.close(directory_fd)
        _raise(H1ReplacementHandoffErrorCode.H1_HANDOFF_PATH_CONTRACT_INVALID)
    return directory_fd


def _invalidate_leaf(name: str, *, directory_fd: int) -> None:
    """Remove one code-owned completion claim; absence is benign."""
    try:
        os.unlink(name, dir_fd=directory_fd)
    except FileNotFoundError:
        return
    except OSError:
        _raise(H1ReplacementHandoffErrorCode.H1_HANDOFF_PERSISTENCE_FAILED)


def _publish_exact_bytes(
    name: str,
    exact_bytes: bytes,
    *,
    directory_fd: int,
) -> None:
    """Publish one completion artifact atomically under its fixed leaf."""
    temporary = f"{name}{_PUBLISH_TEMPORARY_SUFFIX}"
    _invalidate_leaf(temporary, directory_fd=directory_fd)
    flags = (
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC
    )
    fd: int | None = None
    try:
        fd = os.open(temporary, flags, _FILE_MODE, dir_fd=directory_fd)
        os.fchmod(fd, _FILE_MODE)
        offset = 0
        while offset < len(exact_bytes):
            written = os.write(fd, exact_bytes[offset:])
            if written <= 0:
                raise OSError(errno.EIO, "short write")
            offset += written
        os.fsync(fd)
        os.close(fd)
        fd = None
        observed = stable_read_exact_bytes(
            directory_fd,
            temporary,
            maximum_bytes=len(exact_bytes),
        )
        if observed != exact_bytes:
            raise OSError(errno.EIO, "persisted bytes differ")
        os.rename(
            temporary,
            name,
            src_dir_fd=directory_fd,
            dst_dir_fd=directory_fd,
        )
        os.fsync(directory_fd)
    except (MmiStableReadError, OSError) as exc:
        if isinstance(exc, OSError) and (
            exc.errno not in _CONTROLLED_PERSISTENCE_ERRNOS
        ):
            raise
        _raise(H1ReplacementHandoffErrorCode.H1_HANDOFF_PERSISTENCE_FAILED)
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass


def _canonical_artifact_bytes(
    artifact: Mapping[str, object],
    *,
    maximum_bytes: int,
) -> bytes:
    try:
        return canonical_json_bytes(dict(artifact), maximum_bytes=maximum_bytes)
    except MmiCanonicalizationError:
        _raise(
            H1ReplacementHandoffErrorCode.H1_HANDOFF_PREPARED_HANDOFF_INVALID
        )


# --------------------------------------------------------------------------
# Source acquisition.
# --------------------------------------------------------------------------
def _validate_expected_sha256(value: object) -> str:
    if type(value) is not str or _SHA256_RE.fullmatch(value) is None:
        _raise(H1ReplacementHandoffErrorCode.H1_HANDOFF_ARGUMENT_INVALID)
    return value


def _capture_present_source(
    role: MmiSourceRole,
    *,
    expected_source_sha256: str,
) -> MmiCapturedSource:
    """Capture one exact current source under a mandatory expected digest."""
    result = _source_capture.capture_current_mmi_source(
        role,
        expected_source_sha256=expected_source_sha256,
    )
    if (
        not result.valid
        or result.authority_effect != AUTHORITY_EFFECT_NONE
        or type(result.source) is not MmiCapturedSource
        or result.source.role is not role
    ):
        _raise(
            H1ReplacementHandoffErrorCode.H1_HANDOFF_SOURCE_CAPTURE_INVALID,
            owner_reason_codes=_owner_reason_codes(result),
        )
    return result.source


def _prove_portfolio_absence() -> None:
    """Require the code-owned portfolio source to be genuinely absent.

    Only the explicit confirmed-absent proof is accepted.  A present source, a
    symlink, an unreadable entry, an untrusted checkout, and a missing
    intermediate parent each fail closed under their own owner reason code and
    are never reinterpreted as absence.
    """
    result = _source_capture.capture_current_mmi_source_absence(
        MmiSourceRole.PORTFOLIO_SNAPSHOT,
    )
    if (
        not result.valid
        or result.authority_effect != AUTHORITY_EFFECT_NONE
        or result.source is not None
        or _owner_reason_codes(result) != (_CONFIRMED_ABSENT,)
    ):
        _raise(
            H1ReplacementHandoffErrorCode
            .H1_HANDOFF_PORTFOLIO_PRESENCE_INVALID,
            owner_reason_codes=_owner_reason_codes(result),
        )


def _acquire_portfolio_source(
    *,
    portfolio_snapshot_expected_sha256: str | None,
) -> MmiCapturedSource | None:
    if portfolio_snapshot_expected_sha256 is None:
        _prove_portfolio_absence()
        return None
    return _capture_present_source(
        MmiSourceRole.PORTFOLIO_SNAPSHOT,
        expected_source_sha256=portfolio_snapshot_expected_sha256,
    )


# --------------------------------------------------------------------------
# The existing deterministic chain, driven identically by both engines.
# --------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class _Chain:
    policy_projection: dict[str, object]
    portfolio_projection: dict[str, object] | None
    evidence_bundle: dict[str, object]
    analyst_visible_evidence_view: dict[str, object]
    grounded_prompt: dict[str, object]


def _require_build(result: object) -> dict[str, object]:
    if (
        not getattr(result, "valid", False)
        or getattr(result, "authority_effect", None) != AUTHORITY_EFFECT_NONE
        or not isinstance(getattr(result, "projection", None), Mapping)
    ):
        _raise(
            H1ReplacementHandoffErrorCode.H1_HANDOFF_LIVE_CHAIN_INVALID,
            owner_reason_codes=_owner_reason_codes(result),
        )
    return dict(result.projection)


def _require_validation(result: object) -> None:
    if (
        not getattr(result, "valid", False)
        or getattr(result, "authority_effect", None) != AUTHORITY_EFFECT_NONE
    ):
        _raise(
            H1ReplacementHandoffErrorCode.H1_HANDOFF_LIVE_CHAIN_INVALID,
            owner_reason_codes=_owner_reason_codes(result),
        )


def _build_chain(
    *,
    policy_source: MmiCapturedSource,
    portfolio_source: MmiCapturedSource | None,
    run_context: MmiProjectionRunContext,
) -> _Chain:
    """Drive the existing owners from captured sources to a validated G2."""
    policy = _require_build(
        _policy_projection.build_mmi_policy_projection(
            policy_source,
            run_context=run_context,
        )
    )
    _require_validation(
        _policy_projection.validate_mmi_policy_projection(
            policy,
            source=policy_source,
            run_context=run_context,
        )
    )

    portfolio: dict[str, object] | None = None
    if portfolio_source is not None:
        portfolio = _require_build(
            _portfolio_projection.build_mmi_portfolio_snapshot_projection(
                portfolio_source,
                policy_projection=policy,
                policy_source=policy_source,
                run_context=run_context,
            )
        )
        _require_validation(
            _portfolio_projection.validate_mmi_portfolio_snapshot_projection(
                portfolio,
                portfolio_source=portfolio_source,
                policy_projection=policy,
                policy_source=policy_source,
                run_context=run_context,
            )
        )

    evidence = _require_build(
        _evidence_bundle.build_mmi_authenticated_evidence_bundle(
            policy_projection=policy,
            policy_source=policy_source,
            portfolio_projection=portfolio,
            portfolio_source=portfolio_source,
            run_context=run_context,
        )
    )
    _require_validation(
        _evidence_bundle.validate_mmi_authenticated_evidence_bundle(
            evidence,
            policy_projection=policy,
            policy_source=policy_source,
            portfolio_projection=portfolio,
            portfolio_source=portfolio_source,
            run_context=run_context,
        )
    )

    view = _require_build(
        _view_v2.build_mmi_analyst_visible_evidence_view_v2(
            evidence_bundle=evidence,
            policy_projection=policy,
            policy_source=policy_source,
            portfolio_projection=portfolio,
            portfolio_source=portfolio_source,
            run_context=run_context,
        )
    )
    _require_validation(
        _view_v2.validate_mmi_analyst_visible_evidence_view_v2(
            value=view,
            evidence_bundle=evidence,
            policy_projection=policy,
            policy_source=policy_source,
            portfolio_projection=portfolio,
            portfolio_source=portfolio_source,
            run_context=run_context,
        )
    )

    try:
        prompt = _grounded_prompt_v2.build_mmi_grounded_prompt_v2(
            analyst_visible_evidence_view=view,
            evidence_bundle=evidence,
            policy_projection=policy,
            policy_source=policy_source,
            portfolio_projection=portfolio,
            portfolio_source=portfolio_source,
            run_context=run_context,
        )
        prompt = _grounded_prompt_v2.validate_mmi_grounded_prompt_v2(
            value=prompt,
            evidence_bundle=evidence,
            policy_projection=policy,
            policy_source=policy_source,
            portfolio_projection=portfolio,
            portfolio_source=portfolio_source,
            run_context=run_context,
        )
    except _grounded_prompt_v2.MmiGroundedPromptV2Error as exc:
        _raise(
            H1ReplacementHandoffErrorCode.H1_HANDOFF_PROMPT_CONTRACT_INVALID,
            owner_reason_codes=(exc.code,),
        )
    return _Chain(
        policy_projection=policy,
        portfolio_projection=portfolio,
        evidence_bundle=evidence,
        analyst_visible_evidence_view=view,
        grounded_prompt=dict(prompt),
    )


def _prompt_text(grounded_prompt: Mapping[str, object]) -> str:
    value = grounded_prompt.get("prompt_text")
    if type(value) is not str or not value:
        _raise(
            H1ReplacementHandoffErrorCode.H1_HANDOFF_PROMPT_CONTRACT_INVALID
        )
    return value


# --------------------------------------------------------------------------
# Prepare.
# --------------------------------------------------------------------------
def prepare_h1_replacement_handoff(
    *,
    strategy_settings_expected_sha256: str,
    portfolio_snapshot_expected_sha256: str | None,
    portfolio_snapshot_absent: bool,
) -> H1PrepareResult:
    """Publish one prepared handoff and exit without reading any response.

    Exactly one portfolio input is accepted: a mandatory expected digest for a
    present source, or an explicit absence declaration that must then be proven
    by the source-capture absence primitive.  There is no self-authenticating
    discovery of whichever source happens to exist.
    """
    strategy_expected = _validate_expected_sha256(
        strategy_settings_expected_sha256
    )
    if type(portfolio_snapshot_absent) is not bool:
        _raise(H1ReplacementHandoffErrorCode.H1_HANDOFF_ARGUMENT_INVALID)
    if portfolio_snapshot_absent == (
        portfolio_snapshot_expected_sha256 is not None
    ):
        _raise(H1ReplacementHandoffErrorCode.H1_HANDOFF_ARGUMENT_INVALID)
    portfolio_expected: str | None = None
    if portfolio_snapshot_expected_sha256 is not None:
        portfolio_expected = _validate_expected_sha256(
            portfolio_snapshot_expected_sha256
        )

    _require_filesystem_capabilities()
    directory_fd = _open_handoff_directory(create=True)
    try:
        for leaf in _PREPARE_INVALIDATION_ORDER:
            _invalidate_leaf(leaf, directory_fd=directory_fd)

        try:
            run_context = begin_mmi_projection_run()
        except MmiClockContractError as exc:
            if exc.args and exc.args[0] in _CLOCK_CODES:
                _raise(
                    H1ReplacementHandoffErrorCode
                    .H1_HANDOFF_CAPABILITY_UNAVAILABLE
                )
            raise

        policy_source = _capture_present_source(
            MmiSourceRole.STRATEGY_SETTINGS,
            expected_source_sha256=strategy_expected,
        )
        portfolio_source = _acquire_portfolio_source(
            portfolio_snapshot_expected_sha256=portfolio_expected,
        )
        chain = _build_chain(
            policy_source=policy_source,
            portfolio_source=portfolio_source,
            run_context=run_context,
        )

        try:
            prepared = _prepared_handoff._build_mmi_h1_prepared_handoff_v1(
                evaluation_timestamp_utc=run_context.evaluation_timestamp_utc,
                strategy_settings_source_sha256=strategy_expected,
                portfolio_snapshot_source_sha256=portfolio_expected,
                grounded_prompt=chain.grounded_prompt,
            )
        except _prepared_handoff.MmiH1PreparedHandoffV1Error as exc:
            _raise(
                H1ReplacementHandoffErrorCode
                .H1_HANDOFF_PREPARED_HANDOFF_INVALID,
                owner_reason_codes=(exc.code,),
            )
        identity = prepared[_prepared_handoff._IDENTITY_FIELD]
        if type(identity) is not str:
            raise RuntimeError("validated prepared handoff omitted identity")
        prompt_text = _prompt_text(chain.grounded_prompt)

        # Every deterministic step above has now succeeded.  Publishing the
        # prepared handoff is the last action and the sole preparation
        # completion claim; no mapping report is written here.
        _publish_exact_bytes(
            _PREPARED_HANDOFF_LEAF,
            _canonical_artifact_bytes(
                prepared,
                maximum_bytes=MAX_MMI_H1_PREPARED_HANDOFF_V1_CANONICAL_BYTES,
            ),
            directory_fd=directory_fd,
        )
    finally:
        os.close(directory_fd)
    return H1PrepareResult(
        workflow_status=_PREPARE_WORKFLOW_STATUS,
        prepared_handoff_identity_sha256=identity,
        portfolio_snapshot_presence=(
            PORTFOLIO_SNAPSHOT_PROVEN_ABSENT
            if portfolio_source is None
            else PORTFOLIO_SNAPSHOT_PRESENT
        ),
        prompt_text=prompt_text,
    )


# --------------------------------------------------------------------------
# Consume.
# --------------------------------------------------------------------------
def _reject_duplicate_json_keys(
    pairs: list[tuple[str, object]],
) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value:
            _raise(
                H1ReplacementHandoffErrorCode
                .H1_HANDOFF_PREPARED_HANDOFF_INVALID
            )
        value[key] = item
    return value


def _read_prepared_handoff(directory_fd: int) -> dict[str, object]:
    """Read and strictly parse the durable prepared handoff exactly once."""
    try:
        exact_bytes = stable_read_exact_bytes(
            directory_fd,
            _PREPARED_HANDOFF_LEAF,
            maximum_bytes=MAX_MMI_H1_PREPARED_HANDOFF_V1_CANONICAL_BYTES,
        )
    except MmiStableReadError as exc:
        if exc.code is (
            MmiStableReadErrorCode.STABLE_READ_CAPABILITY_UNAVAILABLE
        ):
            _raise(
                H1ReplacementHandoffErrorCode
                .H1_HANDOFF_CAPABILITY_UNAVAILABLE
            )
        _raise(
            H1ReplacementHandoffErrorCode
            .H1_HANDOFF_PREPARED_HANDOFF_INVALID
        )
    try:
        parsed = json.loads(
            exact_bytes.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_json_keys,
        )
    except (UnicodeDecodeError, ValueError):
        _raise(
            H1ReplacementHandoffErrorCode
            .H1_HANDOFF_PREPARED_HANDOFF_INVALID
        )
    if type(parsed) is not dict:
        _raise(
            H1ReplacementHandoffErrorCode
            .H1_HANDOFF_PREPARED_HANDOFF_INVALID
        )
    return parsed


def _acquire_response_bytes(directory_fd: int) -> bytes:
    """Acquire the operator-placed raw response bytes exactly once.

    This is the only response acquisition in the module.  Its single result is
    the immutable ``bytes`` object that binds the envelope; nothing rereads the
    leaf to hash, parse, decode, re-encode, or normalize it, and there is no
    alternate source, retry, poll, or provider path.
    """
    try:
        return stable_read_exact_bytes(
            directory_fd,
            _RESPONSE_LEAF,
            maximum_bytes=MAXIMUM_MMI_RAW_RESPONSE_BYTES,
        )
    except MmiStableReadError as exc:
        if exc.code is (
            MmiStableReadErrorCode.STABLE_READ_CAPABILITY_UNAVAILABLE
        ):
            _raise(
                H1ReplacementHandoffErrorCode
                .H1_HANDOFF_CAPABILITY_UNAVAILABLE
            )
        _raise(
            H1ReplacementHandoffErrorCode.H1_HANDOFF_RESPONSE_INPUT_INVALID
        )


def _require_complete_prompt_equality(
    *,
    rebuilt: Mapping[str, object],
    prepared: object,
) -> None:
    """Require the rebuilt G2 object to equal the prepared one completely.

    Equality is over the whole deterministic ``mmi_grounded_prompt_v2`` object
    through its canonical contract, never over prompt text alone and never
    through any field subset, prose comparison, or normalization.
    """
    if not isinstance(prepared, Mapping):
        _raise(
            H1ReplacementHandoffErrorCode
            .H1_HANDOFF_PROMPT_CONTINUITY_INVALID
        )
    try:
        rebuilt_canonical = canonical_json_bytes(
            dict(rebuilt),
            maximum_bytes=_GROUNDED_PROMPT_MAXIMUM_CANONICAL_BYTES,
        )
        prepared_canonical = canonical_json_bytes(
            dict(prepared),
            maximum_bytes=_GROUNDED_PROMPT_MAXIMUM_CANONICAL_BYTES,
        )
    except MmiCanonicalizationError:
        _raise(
            H1ReplacementHandoffErrorCode
            .H1_HANDOFF_PROMPT_CONTINUITY_INVALID
        )
    if (
        dict(rebuilt) != dict(prepared)
        or rebuilt_canonical != prepared_canonical
    ):
        _raise(
            H1ReplacementHandoffErrorCode
            .H1_HANDOFF_PROMPT_CONTINUITY_INVALID
        )


def consume_h1_replacement_handoff(
    *,
    expected_prepared_handoff_identity_sha256: str,
) -> H1ConsumeResult:
    """Consume one prepared handoff under its operator-supplied identity.

    Every restart, source, and prompt continuity proof completes before the
    response leaf is touched.  The operator-supplied expected identity is the
    only admissible selector: no current pointer, no newest-file rule, and no
    self-authenticating use of whichever prepared handoff happens to exist.
    """
    expected_identity = _validate_expected_sha256(
        expected_prepared_handoff_identity_sha256
    )
    _require_filesystem_capabilities()
    directory_fd = _open_handoff_directory(create=False)
    try:
        for leaf in _CONSUME_INVALIDATION_ORDER:
            _invalidate_leaf(leaf, directory_fd=directory_fd)

        prepared = _read_prepared_handoff(directory_fd)
        try:
            validated = _prepared_handoff.validate_mmi_h1_prepared_handoff_v1(
                prepared_handoff=prepared,
            )
        except _prepared_handoff.MmiH1PreparedHandoffV1Error as exc:
            _raise(
                H1ReplacementHandoffErrorCode
                .H1_HANDOFF_PREPARED_HANDOFF_INVALID,
                owner_reason_codes=(exc.code,),
            )
        if validated[_prepared_handoff._IDENTITY_FIELD] != expected_identity:
            _raise(
                H1ReplacementHandoffErrorCode
                .H1_HANDOFF_PREPARED_HANDOFF_INVALID
            )

        try:
            run_context = (
                _prepared_handoff.resume_mmi_h1_prepared_handoff_run_context(
                    prepared_handoff=validated,
                    expected_prepared_handoff_identity_sha256=(
                        expected_identity
                    ),
                )
            )
        except _prepared_handoff.MmiH1PreparedHandoffV1Error as exc:
            _raise(
                H1ReplacementHandoffErrorCode
                .H1_HANDOFF_PREPARED_HANDOFF_INVALID,
                owner_reason_codes=(exc.code,),
            )

        strategy_expected = validated["strategy_settings_source_sha256"]
        portfolio_expected = validated["portfolio_snapshot_source_sha256"]
        if type(strategy_expected) is not str or not (
            portfolio_expected is None or type(portfolio_expected) is str
        ):
            _raise(
                H1ReplacementHandoffErrorCode
                .H1_HANDOFF_PREPARED_HANDOFF_INVALID
            )
        policy_source = _capture_present_source(
            MmiSourceRole.STRATEGY_SETTINGS,
            expected_source_sha256=strategy_expected,
        )
        portfolio_source = _acquire_portfolio_source(
            portfolio_snapshot_expected_sha256=portfolio_expected,
        )
        chain = _build_chain(
            policy_source=policy_source,
            portfolio_source=portfolio_source,
            run_context=run_context,
        )
        _require_complete_prompt_equality(
            rebuilt=chain.grounded_prompt,
            prepared=validated["grounded_prompt"],
        )

        # Restart, source, and complete prompt continuity are proven.  Only
        # now may operator response bytes enter this process, exactly once.
        response_bytes = _acquire_response_bytes(directory_fd)
        mapping_report, facts, qualitative_facts = (
            _build_mapping_report_and_facts(
                chain=chain,
                policy_source=policy_source,
                portfolio_source=portfolio_source,
                run_context=run_context,
                response_bytes=response_bytes,
            )
        )
        mapping_identity = mapping_report["mapping_report_identity_sha256"]
        if type(mapping_identity) is not str:
            raise RuntimeError("validated mapping report omitted identity")

        # Valid facts exist, so the mapping report becomes the consume
        # completion claim.  The facts themselves are never persisted.
        _publish_exact_bytes(
            _MAPPING_REPORT_LEAF,
            _canonical_artifact_bytes(
                mapping_report,
                maximum_bytes=(
                    MAX_MMI_H1_LEGACY_STEP1_MAPPING_REPORT_V1_CANONICAL_BYTES
                ),
            ),
            directory_fd=directory_fd,
        )
    finally:
        os.close(directory_fd)
    return H1ConsumeResult(
        workflow_status=_CONSUME_WORKFLOW_STATUS,
        prepared_handoff_identity_sha256=expected_identity,
        mapping_report_identity_sha256=mapping_identity,
        portfolio_snapshot_presence=(
            PORTFOLIO_SNAPSHOT_PROVEN_ABSENT
            if portfolio_source is None
            else PORTFOLIO_SNAPSHOT_PRESENT
        ),
        mapped_recognition_facts=facts,
        qualitative_research_facts=qualitative_facts,
    )


@dataclass(frozen=True, slots=True)
class _ResponseChain:
    raw_response_envelope: dict[str, object]
    validated_grounded_analysis_response: dict[str, object]
    legacy_step1_compatibility_candidate: dict[str, object]


def _build_response_chain(
    *,
    chain: _Chain,
    policy_source: MmiCapturedSource,
    portfolio_source: MmiCapturedSource | None,
    run_context: MmiProjectionRunContext,
    response_bytes: bytes,
) -> _ResponseChain:
    """Bind exactly the acquired bytes to the existing downstream owners."""
    common: dict[str, object] = {
        "evidence_bundle": chain.evidence_bundle,
        "policy_projection": chain.policy_projection,
        "policy_source": policy_source,
        "portfolio_projection": chain.portfolio_projection,
        "portfolio_source": portfolio_source,
        "run_context": run_context,
    }
    try:
        envelope = _raw_response_v2.build_mmi_raw_response_envelope_v2(
            grounded_prompt=chain.grounded_prompt,
            raw_response_bytes=response_bytes,
            **common,
        )
        envelope = _raw_response_v2.validate_mmi_raw_response_envelope_v2(
            value=envelope,
            **common,
        )
    except _raw_response_v2.MmiRawResponseEnvelopeV2Error as exc:
        _raise(
            H1ReplacementHandoffErrorCode.H1_HANDOFF_RESPONSE_CONTENT_INVALID,
            owner_reason_codes=(exc.code,),
        )
    try:
        response = (
            _validated_response_v2
            .build_mmi_validated_grounded_analysis_response_v2(
                raw_response_envelope=envelope,
                **common,
            )
        )
        response = (
            _validated_response_v2
            .validate_mmi_validated_grounded_analysis_response_v2(
                value=response,
                raw_response_envelope=envelope,
                **common,
            )
        )
    except (
        _validated_response_v2.MmiValidatedGroundedAnalysisResponseV2Error
    ) as exc:
        _raise(
            H1ReplacementHandoffErrorCode.H1_HANDOFF_RESPONSE_CONTENT_INVALID,
            owner_reason_codes=(exc.code,),
        )
    try:
        candidate = (
            _candidate_v1.build_mmi_legacy_step1_compatibility_candidate_v1(
                validated_grounded_analysis_response=response,
                raw_response_envelope=envelope,
                **common,
            )
        )
    except (
        _candidate_v1.MmiLegacyStep1CompatibilityCandidateV1Error
    ) as exc:
        _raise(
            H1ReplacementHandoffErrorCode.H1_HANDOFF_RESPONSE_CONTENT_INVALID,
            owner_reason_codes=(exc.code,),
        )
    return _ResponseChain(
        raw_response_envelope=dict(envelope),
        validated_grounded_analysis_response=dict(response),
        legacy_step1_compatibility_candidate=dict(candidate),
    )


def _build_qualitative_research_facts(
    validated_grounded_analysis_response: dict[str, object],
) -> H1QualitativeResearchFacts:
    """Project the already-validated qualitative response, unchanged.

    Reads only ``validated_grounded_analysis_response`` — the object the
    existing grounded-response validator already produced and validated.
    Performs no schema check, no instrument-tuple check, and no reference
    check of its own: those remain owned exactly where they already are.
    """
    payload = validated_grounded_analysis_response["response_payload"]
    instrument_views = tuple(
        H1QualitativeInstrumentView(
            ticker=row["ticker"],
            evidence_status=row["evidence_status"],
            rationale_12m_plus=row["rationale_12m_plus"],
            references=tuple(row["references"]),
        )
        for row in payload["instrument_views"]
    )
    return H1QualitativeResearchFacts(
        analysis_status=payload["analysis_status"],
        instrument_views=instrument_views,
        validated_grounded_analysis_response_identity_sha256=(
            validated_grounded_analysis_response[
                "validated_grounded_analysis_response_identity_sha256"
            ]
        ),
    )


def _build_mapping_report_and_facts(
    *,
    chain: _Chain,
    policy_source: MmiCapturedSource,
    portfolio_source: MmiCapturedSource | None,
    run_context: MmiProjectionRunContext,
    response_bytes: bytes,
) -> tuple[dict[str, object], H1MappedRecognitionFacts, H1QualitativeResearchFacts]:
    """Complete the response chain, the mapping report, and then the facts.

    Facts are constructed last and only in memory.  A portfolio source that
    was proven absent reaches the existing H1 mapping contract unchanged and
    fails closed there, so no completion artifact can be published for it.
    ``qualitative_facts`` is projected directly from the validated response,
    independent of the mapping report and mapped-recognition facts built
    below it.
    """
    response_chain = _build_response_chain(
        chain=chain,
        policy_source=policy_source,
        portfolio_source=portfolio_source,
        run_context=run_context,
        response_bytes=response_bytes,
    )
    qualitative_facts = _build_qualitative_research_facts(
        response_chain.validated_grounded_analysis_response
    )
    try:
        report = (
            _mapping_report_v1.build_mmi_h1_legacy_step1_mapping_report_v1(
                legacy_step1_compatibility_candidate=(
                    response_chain.legacy_step1_compatibility_candidate
                ),
                validated_grounded_analysis_response=(
                    response_chain.validated_grounded_analysis_response
                ),
                raw_response_envelope=response_chain.raw_response_envelope,
                evidence_bundle=chain.evidence_bundle,
                policy_projection=chain.policy_projection,
                policy_source=policy_source,
                portfolio_projection=chain.portfolio_projection,
                portfolio_source=portfolio_source,
                run_context=run_context,
            )
        )
    except (
        _mapping_report_v1.MmiH1LegacyStep1MappingReportV1Error
    ) as exc:
        _raise(
            H1ReplacementHandoffErrorCode.H1_HANDOFF_MAPPING_INVALID,
            owner_reason_codes=(exc.code,),
        )
    mapping_report = dict(report)
    try:
        facts = build_validated_h1_mapped_recognition_facts(
            mapping_report=mapping_report,
            legacy_step1_compatibility_candidate=(
                response_chain.legacy_step1_compatibility_candidate
            ),
            validated_grounded_analysis_response=(
                response_chain.validated_grounded_analysis_response
            ),
            raw_response_envelope=response_chain.raw_response_envelope,
            evidence_bundle=chain.evidence_bundle,
            policy_projection=chain.policy_projection,
            policy_source=policy_source,
            portfolio_projection=chain.portfolio_projection,
            portfolio_source=portfolio_source,
            run_context=run_context,
        )
    except H1MappedRecognitionError as exc:
        _raise(
            H1ReplacementHandoffErrorCode.H1_HANDOFF_FACTS_INVALID,
            owner_reason_codes=(exc.code,),
        )
    return mapping_report, facts, qualitative_facts
