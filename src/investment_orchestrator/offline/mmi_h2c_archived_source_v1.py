from __future__ import annotations



import hashlib

import json

import re

from dataclasses import dataclass

from types import MappingProxyType

from typing import Final, Literal, Mapping



from investment_orchestrator.common.schema_validation import (

    validate_artifact_schema,

)

from investment_orchestrator.mmi.canonical import (

    canonical_json_bytes,

)

from investment_orchestrator.mmi.contracts import (

    MMI_SOURCE_CATALOG,

    MmiProjectionRunContext,

    MmiSourceRole,

)

from investment_orchestrator.offline._mmi_h2c_stable_read_v1 import (

    MmiH2cStableReadError,

    MmiH2cStableReadErrorCode,

    _stable_read_exact_bytes,

)

from investment_orchestrator.offline.mmi_h2c_dual_side_manual_handoff_context_receipt_v1 import (

    MmiH2cDualSideManualHandoffContextReceiptV1Error,

    validate_portable_source_record_v1,

)

from investment_orchestrator.offline.mmi_h2c_prepared_case_v1 import (

    _MAXIMUM_CANONICAL_BYTES,

    MmiH2cPreparedCaseV1Error,

    resume_mmi_h2c_prepared_case_run_context,

    validate_mmi_h2c_prepared_case_v1,

)





_ArchivedErrorCode = Literal[

    "ARCHIVED_ARGUMENT_INVALID",

    "PREPARED_CASE_INPUT_INVALID",

    "PREPARED_CASE_SCHEMA_INVALID",

    "ARCHIVE_SOURCE_INPUT_INVALID",

    "ARCHIVE_SOURCE_SCHEMA_INVALID",

    "CAPABILITY_UNAVAILABLE",

]





class MmiH2cArchivedSourceV1Error(ValueError):

    def __init__(self, code: _ArchivedErrorCode) -> None:

        super().__init__(code)

        self.code = code





@dataclass(frozen=True, slots=True)

class _MmiH2cArchivedPreparedCaseProjection:

    workflow_status: str

    legacy_prompt_template_sha256: str

    legacy_prompt_sha256: str

    h1_prompt_sha256: str

    grounded_prompt_canonical_bytes: bytes





@dataclass(frozen=True, slots=True)

class _MmiH2cArchivedPreparedCaseSnapshot:

    prepared_case_identity_sha256: str

    projection: _MmiH2cArchivedPreparedCaseProjection

    run_context: MmiProjectionRunContext

    strategy_archived_bytes: bytes

    strategy_source_record: Mapping[str, str | int]

    portfolio_archived_bytes: bytes

    portfolio_source_record: Mapping[str, str | int]





_SHA256_RE: Final = re.compile(r"^[0-9a-f]{64}$")





def _read_manifest(case_fd: int) -> bytes:

    try:

        return _stable_read_exact_bytes(

            case_fd,

            "prepared/prepared_case.json",

            maximum_bytes=_MAXIMUM_CANONICAL_BYTES,

        )

    except MmiH2cStableReadError as exc:

        if exc.code == MmiH2cStableReadErrorCode.STABLE_READ_CAPABILITY_UNAVAILABLE:

            raise MmiH2cArchivedSourceV1Error("CAPABILITY_UNAVAILABLE") from exc

        raise MmiH2cArchivedSourceV1Error("PREPARED_CASE_INPUT_INVALID") from exc





def _read_archive(case_fd: int, path: str, maximum_bytes: int) -> bytes:

    try:

        return _stable_read_exact_bytes(

            case_fd,

            path,

            maximum_bytes=maximum_bytes,

        )

    except MmiH2cStableReadError as exc:

        if exc.code == MmiH2cStableReadErrorCode.STABLE_READ_CAPABILITY_UNAVAILABLE:

            raise MmiH2cArchivedSourceV1Error("CAPABILITY_UNAVAILABLE") from exc

        raise MmiH2cArchivedSourceV1Error("ARCHIVE_SOURCE_INPUT_INVALID") from exc





def _validate_path_syntax(path: str) -> None:

    if not path or path.startswith("/") or ".." in path.split("/"):

        raise MmiH2cArchivedSourceV1Error("PREPARED_CASE_SCHEMA_INVALID")





def _bind_role(

    case_fd: int,

    manifest: dict[str, object],

    slot: str,

    role: MmiSourceRole,

) -> tuple[bytes, Mapping[str, str | int]]:

    wrapper = manifest[slot]

    assert isinstance(wrapper, dict)

    archive_path = wrapper["archive_relative_path"]

    assert isinstance(archive_path, str)

    _validate_path_syntax(archive_path)



    spec = MMI_SOURCE_CATALOG[role]

    archived_bytes = _read_archive(case_fd, archive_path, spec.maximum_bytes)



    source_record = wrapper["source_record"]

    assert isinstance(source_record, dict)



    if source_record.get("source_role") != role.value:

        raise MmiH2cArchivedSourceV1Error("ARCHIVE_SOURCE_INPUT_INVALID")



    if source_record.get("observed_size_bytes") != len(archived_bytes):

        raise MmiH2cArchivedSourceV1Error("ARCHIVE_SOURCE_INPUT_INVALID")



    expected_sha256 = source_record.get("observed_sha256")

    if expected_sha256 != hashlib.sha256(archived_bytes).hexdigest():

        raise MmiH2cArchivedSourceV1Error("ARCHIVE_SOURCE_INPUT_INVALID")



    try:

        validated_record = validate_portable_source_record_v1(

            value=source_record,

            expected_role=role,

            archived_source_bytes=archived_bytes,

        )

    except MmiH2cDualSideManualHandoffContextReceiptV1Error as exc:

        raise MmiH2cArchivedSourceV1Error("ARCHIVE_SOURCE_SCHEMA_INVALID") from exc



    frozen_record: dict[str, str | int] = {}

    for k, v in validated_record.items():

        if not isinstance(v, (str, int)):

            raise TypeError("validator contract violation")

        frozen_record[k] = v



    return archived_bytes, MappingProxyType(frozen_record)





def _build_mmi_h2c_archived_prepared_case_snapshot(

    *,

    case_fd: int,

    expected_prepared_case_identity_sha256: str,

) -> _MmiH2cArchivedPreparedCaseSnapshot:

    if not isinstance(expected_prepared_case_identity_sha256, str) or not _SHA256_RE.match(

        expected_prepared_case_identity_sha256

    ):

        raise MmiH2cArchivedSourceV1Error("ARCHIVED_ARGUMENT_INVALID")



    manifest_bytes = _read_manifest(case_fd)



    try:

        manifest = json.loads(manifest_bytes)

    except json.JSONDecodeError as exc:

        raise MmiH2cArchivedSourceV1Error("PREPARED_CASE_INPUT_INVALID") from exc



    try:

        validate_mmi_h2c_prepared_case_v1(prepared_case=manifest)

    except MmiH2cPreparedCaseV1Error as exc:

        raise MmiH2cArchivedSourceV1Error("PREPARED_CASE_SCHEMA_INVALID") from exc



    actual_identity = manifest.get("prepared_case_identity_sha256")

    if type(actual_identity) is not str or actual_identity != expected_prepared_case_identity_sha256:

        raise MmiH2cArchivedSourceV1Error("ARCHIVED_ARGUMENT_INVALID")



    run_context = resume_mmi_h2c_prepared_case_run_context(

        prepared_case=manifest,

        expected_prepared_case_identity_sha256=expected_prepared_case_identity_sha256,

    )



    strategy_bytes, strategy_record = _bind_role(

        case_fd, manifest, "strategy_settings_source", MmiSourceRole.STRATEGY_SETTINGS

    )

    portfolio_bytes, portfolio_record = _bind_role(

        case_fd, manifest, "portfolio_snapshot_source", MmiSourceRole.PORTFOLIO_SNAPSHOT

    )



    grounded_prompt = manifest["grounded_prompt"]

    assert isinstance(grounded_prompt, dict)



    template_wrapper = manifest["legacy_prompt_template"]

    assert isinstance(template_wrapper, dict)



    h1_wrapper = manifest.get("h1_prompt")

    if not h1_wrapper:

        h1_wrapper = {"sha256": "0" * 64}

    assert isinstance(h1_wrapper, dict)



    legacy_wrapper = manifest.get("legacy_prompt")

    if not legacy_wrapper:

        legacy_wrapper = {"sha256": "0" * 64}

    assert isinstance(legacy_wrapper, dict)



    projection = _MmiH2cArchivedPreparedCaseProjection(

        workflow_status=str(manifest["workflow_status"]),

        legacy_prompt_template_sha256=str(template_wrapper["sha256"]),

        legacy_prompt_sha256=str(legacy_wrapper["sha256"]),

        h1_prompt_sha256=str(h1_wrapper.get("sha256", "0" * 64)),

        grounded_prompt_canonical_bytes=canonical_json_bytes(grounded_prompt),

    )



    return _MmiH2cArchivedPreparedCaseSnapshot(

        prepared_case_identity_sha256=actual_identity,

        projection=projection,

        run_context=run_context,

        strategy_archived_bytes=strategy_bytes,

        strategy_source_record=strategy_record,

        portfolio_archived_bytes=portfolio_bytes,

        portfolio_source_record=portfolio_record,

    )
