import hashlib

import json

import os

import copy

from pathlib import Path



import pytest



from investment_orchestrator.offline.mmi_h2c_archived_source_v1 import (

    MmiH2cArchivedSourceV1Error,

    _build_mmi_h2c_archived_prepared_case_snapshot,

)

from investment_orchestrator.mmi.canonical import canonical_json_bytes





def _identity(value: dict[str, object], domain: bytes) -> str:

    encoded = canonical_json_bytes(value)

    framed = domain + len(encoded).to_bytes(8, "big") + encoded

    return hashlib.sha256(framed).hexdigest()


def _write_manifest_leaf(root: Path, payload: bytes) -> None:
    """Write the one canonical prepared-case leaf the prepare owner defines.

    ``prepared/prepared_case.json`` is the prepare owner's committed choice
    (``mmi_h2c_prepare_persisted_case_v1.py``); no root ``manifest.json``
    contract exists in production.
    """
    prepared_dir = root / "prepared"
    prepared_dir.mkdir(exist_ok=True)
    (prepared_dir / "prepared_case.json").write_bytes(payload)





@pytest.fixture

def valid_case() -> tuple[dict[str, object], bytes, bytes]:

    strategy_content = b"strategy content"

    strategy_sha = hashlib.sha256(strategy_content).hexdigest()

    portfolio_content = b"portfolio content"

    portfolio_sha = hashlib.sha256(portfolio_content).hexdigest()



    strategy_record = {

        "schema_version": "mmi_source_record_v1",

        "source_role": "STRATEGY_SETTINGS",

        "source_id": "MMI_STRATEGY_SETTINGS",

        "repository_relative_locator": "inputs/current/strategy_settings.yaml",

        "maximum_bytes": 262144,

        "observed_size_bytes": len(strategy_content),

        "expected_sha256": strategy_sha,

        "observed_sha256": strategy_sha,

        "content_binding_status": "EXPECTED_SHA256_MATCHED",

        "operator_origin_authentication": "NOT_ESTABLISHED",

        "stable_read_status": "STABLE_BEFORE_AND_AFTER",

        "regular_file_status": "REGULAR_FILE",

        "authority_effect": "NONE",

    }

    strategy_record["source_record_identity_sha256"] = _identity(strategy_record, b"mmi_source_record_v1\0")



    portfolio_record = {

        "schema_version": "mmi_source_record_v1",

        "source_role": "PORTFOLIO_SNAPSHOT",

        "source_id": "MMI_PORTFOLIO_SNAPSHOT",

        "repository_relative_locator": "inputs/current/portfolio_snapshot.txt",

        "maximum_bytes": 1048576,

        "observed_size_bytes": len(portfolio_content),

        "expected_sha256": portfolio_sha,

        "observed_sha256": portfolio_sha,

        "content_binding_status": "EXPECTED_SHA256_MATCHED",

        "operator_origin_authentication": "NOT_ESTABLISHED",

        "stable_read_status": "STABLE_BEFORE_AND_AFTER",

        "regular_file_status": "REGULAR_FILE",

        "authority_effect": "NONE",

    }

    portfolio_record["source_record_identity_sha256"] = _identity(portfolio_record, b"mmi_source_record_v1\0")



    manifest = {

        "schema_version": "mmi_h2c_prepared_case_v1",

        "artifact_kind": "MMI_H2C_PREPARED_CASE",

        "preparation_contract_version": "mmi_h2c_persisted_case_prepare_v1",

        "report_only": True,

        "authority_effect": "NONE",

        "workflow_status": "AWAITING_OPERATOR_RESPONSES",

        "evaluation_timestamp_utc": "2026-08-04T01:15:34.942524Z",

        "strategy_settings_source": {

            "source_record": strategy_record,

            "archive_relative_path": "archive/strategy_settings.yaml",

        },

        "portfolio_snapshot_source": {

            "source_record": portfolio_record,

            "archive_relative_path": "archive/portfolio_snapshot.txt",

        },

        "legacy_prompt_template": {

            "repository_relative_locator": "prompts/research_dual_lane.txt",

            "archive_relative_path": "archive/research_dual_lane.txt",

            "byte_length": 262144,

            "sha256": "0"*64,

        },

        "grounded_prompt": {},

        "h1_prompt": {

            "relative_path": "prompts/h1_prompt.txt",

            "byte_length": 65536,

            "sha256": "0"*64,

        },

        "legacy_prompt": {

            "relative_path": "prompts/legacy_prompt.txt",

            "byte_length": 3170307,

            "sha256": "0"*64,

            "compiler_contract_version": "mmi_legacy_step1_compatibility_compiler_v1",

        },

        "response_leaves": {

            "h1": "responses/h1_response.raw",

            "legacy": "responses/legacy_response.raw",

        },

        "result_leaves": {

            "case_evidence_bundle": "artifacts/case_evidence_bundle.json",

            "comparison_report": "artifacts/comparison_report.json",

            "receipt": "artifacts/receipt.json",

        },

    }

    manifest["prepared_case_identity_sha256"] = _identity(manifest, b"mmi_h2c_prepared_case_v1\0")



    return manifest, strategy_content, portfolio_content





@pytest.fixture

def case_fd(tmp_path: Path, valid_case: tuple[dict[str, object], bytes, bytes]) -> tuple[int, str]:

    manifest, strategy, portfolio = valid_case

    archive_dir = tmp_path / "archive"

    archive_dir.mkdir()



    _write_manifest_leaf(tmp_path, json.dumps(manifest).encode("utf-8"))



    (tmp_path / "archive/strategy_settings.yaml").write_bytes(strategy)

    (tmp_path / "archive/portfolio_snapshot.txt").write_bytes(portfolio)



    fd = os.open(str(tmp_path), os.O_RDONLY | os.O_DIRECTORY)

    yield fd, manifest["prepared_case_identity_sha256"]

    os.close(fd)





def test_positive_immutable_snapshot(case_fd) -> None:

    fd, expected_id = case_fd

    snapshot = _build_mmi_h2c_archived_prepared_case_snapshot(

        case_fd=fd, expected_prepared_case_identity_sha256=expected_id

    )

    assert snapshot.prepared_case_identity_sha256 == expected_id

    assert snapshot.strategy_archived_bytes == b"strategy content"

    assert snapshot.portfolio_archived_bytes == b"portfolio content"

    assert snapshot.projection.workflow_status == "AWAITING_OPERATOR_RESPONSES"

    # validated actual prepared identity retained

    assert snapshot.prepared_case_identity_sha256 == expected_id



    # projection contains no mutable nested object

    import dataclasses

    from types import MappingProxyType



    def assert_immutable(val):

        if dataclasses.is_dataclass(val):

            for field in dataclasses.fields(val):

                assert_immutable(getattr(val, field.name))

        elif isinstance(val, (str, int, bytes, bool)):

            pass

        elif isinstance(val, tuple):

            for item in val:

                assert_immutable(item)

        elif isinstance(val, MappingProxyType):

            pass

        else:

            pytest.fail(f"Mutable field found: {type(val)}")



    assert_immutable(snapshot.projection)





def test_malformed_expected_identity(case_fd) -> None:

    fd, _ = case_fd

    with pytest.raises(MmiH2cArchivedSourceV1Error, match="ARCHIVED_ARGUMENT_INVALID"):

        _build_mmi_h2c_archived_prepared_case_snapshot(case_fd=fd, expected_prepared_case_identity_sha256="not_a_sha")





def test_valid_manifest_vs_expected_identity_mismatch(case_fd) -> None:

    fd, _ = case_fd

    wrong_id = "1" * 64

    with pytest.raises(MmiH2cArchivedSourceV1Error, match="ARCHIVED_ARGUMENT_INVALID"):

        _build_mmi_h2c_archived_prepared_case_snapshot(case_fd=fd, expected_prepared_case_identity_sha256=wrong_id)





def test_manifest_exactly_one_read(case_fd, monkeypatch) -> None:

    fd, expected_id = case_fd



    calls = []

    import investment_orchestrator.offline.mmi_h2c_archived_source_v1 as mod

    original = mod._stable_read_exact_bytes



    def mock_stable_read(case_fd, path, *, maximum_bytes):

        calls.append(path)

        return original(case_fd, path, maximum_bytes=maximum_bytes)



    monkeypatch.setattr(mod, "_stable_read_exact_bytes", mock_stable_read)



    _build_mmi_h2c_archived_prepared_case_snapshot(

        case_fd=fd, expected_prepared_case_identity_sha256=expected_id

    )



    assert calls.count("prepared/prepared_case.json") == 1

    assert calls.count("archive/strategy_settings.yaml") == 1

    assert calls.count("archive/portfolio_snapshot.txt") == 1

    assert len(calls) == 3





def test_no_case_root_reopen(tmp_path, valid_case, monkeypatch) -> None:

    manifest, strategy, portfolio = valid_case

    archive_dir = tmp_path / "archive"

    archive_dir.mkdir()

    _write_manifest_leaf(tmp_path, json.dumps(manifest).encode("utf-8"))

    (tmp_path / "archive/strategy_settings.yaml").write_bytes(strategy)

    (tmp_path / "archive/portfolio_snapshot.txt").write_bytes(portfolio)



    fd = os.open(str(tmp_path), os.O_RDONLY | os.O_DIRECTORY)



    opens = []

    original_open = os.open

    def mock_open(path, flags, *args, **kwargs):

        if path == str(tmp_path) or path == tmp_path:

            opens.append(path)

        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", mock_open)



    _build_mmi_h2c_archived_prepared_case_snapshot(

        case_fd=fd, expected_prepared_case_identity_sha256=manifest["prepared_case_identity_sha256"]

    )

    os.close(fd)

    assert not opens





def test_symlink_rejection(tmp_path, valid_case) -> None:

    manifest, strategy, portfolio = valid_case

    archive_dir = tmp_path / "archive"

    archive_dir.mkdir()

    _write_manifest_leaf(tmp_path, json.dumps(manifest).encode("utf-8"))



    actual_strategy = tmp_path / "actual_strategy.yaml"

    actual_strategy.write_bytes(strategy)

    os.symlink("actual_strategy.yaml", tmp_path / "archive/strategy_settings.yaml")



    (tmp_path / "archive/portfolio_snapshot.txt").write_bytes(portfolio)



    fd = os.open(str(tmp_path), os.O_RDONLY | os.O_DIRECTORY)

    with pytest.raises(MmiH2cArchivedSourceV1Error, match="ARCHIVE_SOURCE_INPUT_INVALID"):

        _build_mmi_h2c_archived_prepared_case_snapshot(

            case_fd=fd, expected_prepared_case_identity_sha256=manifest["prepared_case_identity_sha256"]

        )

    os.close(fd)





def test_directory_rejection(tmp_path, valid_case) -> None:

    manifest, strategy, portfolio = valid_case

    archive_dir = tmp_path / "archive"

    archive_dir.mkdir()

    _write_manifest_leaf(tmp_path, json.dumps(manifest).encode("utf-8"))



    (tmp_path / "archive/strategy_settings.yaml").mkdir()

    (tmp_path / "archive/portfolio_snapshot.txt").write_bytes(portfolio)



    fd = os.open(str(tmp_path), os.O_RDONLY | os.O_DIRECTORY)

    with pytest.raises(MmiH2cArchivedSourceV1Error, match="ARCHIVE_SOURCE_INPUT_INVALID"):

        _build_mmi_h2c_archived_prepared_case_snapshot(

            case_fd=fd, expected_prepared_case_identity_sha256=manifest["prepared_case_identity_sha256"]

        )

    os.close(fd)





def test_fifo_rejection_without_blocking(tmp_path, valid_case) -> None:

    manifest, strategy, portfolio = valid_case

    archive_dir = tmp_path / "archive"

    archive_dir.mkdir()

    _write_manifest_leaf(tmp_path, json.dumps(manifest).encode("utf-8"))



    os.mkfifo(tmp_path / "archive/strategy_settings.yaml")

    (tmp_path / "archive/portfolio_snapshot.txt").write_bytes(portfolio)



    fd = os.open(str(tmp_path), os.O_RDONLY | os.O_DIRECTORY)

    with pytest.raises(MmiH2cArchivedSourceV1Error, match="ARCHIVE_SOURCE_INPUT_INVALID"):

        _build_mmi_h2c_archived_prepared_case_snapshot(

            case_fd=fd, expected_prepared_case_identity_sha256=manifest["prepared_case_identity_sha256"]

        )

    os.close(fd)





def test_length_mismatch(tmp_path, valid_case) -> None:

    manifest, strategy, portfolio = valid_case

    archive_dir = tmp_path / "archive"

    archive_dir.mkdir()

    _write_manifest_leaf(tmp_path, json.dumps(manifest).encode("utf-8"))



    (tmp_path / "archive/strategy_settings.yaml").write_bytes(strategy + b"extra")

    (tmp_path / "archive/portfolio_snapshot.txt").write_bytes(portfolio)



    fd = os.open(str(tmp_path), os.O_RDONLY | os.O_DIRECTORY)

    with pytest.raises(MmiH2cArchivedSourceV1Error, match="ARCHIVE_SOURCE_INPUT_INVALID"):

        _build_mmi_h2c_archived_prepared_case_snapshot(

            case_fd=fd, expected_prepared_case_identity_sha256=manifest["prepared_case_identity_sha256"]

        )

    os.close(fd)





def test_digest_mismatch(tmp_path, valid_case) -> None:

    manifest, strategy, portfolio = valid_case

    archive_dir = tmp_path / "archive"

    archive_dir.mkdir()

    _write_manifest_leaf(tmp_path, json.dumps(manifest).encode("utf-8"))



    wrong_strategy = b"wrongcontent    "

    assert len(wrong_strategy) == len(strategy)

    (tmp_path / "archive/strategy_settings.yaml").write_bytes(wrong_strategy)

    (tmp_path / "archive/portfolio_snapshot.txt").write_bytes(portfolio)



    fd = os.open(str(tmp_path), os.O_RDONLY | os.O_DIRECTORY)

    with pytest.raises(MmiH2cArchivedSourceV1Error, match="ARCHIVE_SOURCE_INPUT_INVALID"):

        _build_mmi_h2c_archived_prepared_case_snapshot(

            case_fd=fd, expected_prepared_case_identity_sha256=manifest["prepared_case_identity_sha256"]

        )

    os.close(fd)





def test_source_record_schema_rejection(tmp_path, valid_case) -> None:

    manifest, strategy, portfolio = valid_case

    manifest["strategy_settings_source"]["source_record"]["extra_field"] = "bad"

    manifest["prepared_case_identity_sha256"] = _identity(manifest, b"mmi_h2c_prepared_case_v1\0")



    archive_dir = tmp_path / "archive"

    archive_dir.mkdir()

    _write_manifest_leaf(tmp_path, json.dumps(manifest).encode("utf-8"))

    (tmp_path / "archive/strategy_settings.yaml").write_bytes(strategy)

    (tmp_path / "archive/portfolio_snapshot.txt").write_bytes(portfolio)



    fd = os.open(str(tmp_path), os.O_RDONLY | os.O_DIRECTORY)

    with pytest.raises(MmiH2cArchivedSourceV1Error, match="PREPARED_CASE_SCHEMA_INVALID"):

        _build_mmi_h2c_archived_prepared_case_snapshot(

            case_fd=fd, expected_prepared_case_identity_sha256=manifest["prepared_case_identity_sha256"]

        )

    os.close(fd)





def test_source_record_identity_mismatch(tmp_path, valid_case) -> None:

    manifest, strategy, portfolio = valid_case

    manifest["strategy_settings_source"]["source_record"]["source_record_identity_sha256"] = "1" * 64

    manifest["prepared_case_identity_sha256"] = _identity(manifest, b"mmi_h2c_prepared_case_v1\0")



    archive_dir = tmp_path / "archive"

    archive_dir.mkdir()

    _write_manifest_leaf(tmp_path, json.dumps(manifest).encode("utf-8"))

    (tmp_path / "archive/strategy_settings.yaml").write_bytes(strategy)

    (tmp_path / "archive/portfolio_snapshot.txt").write_bytes(portfolio)



    fd = os.open(str(tmp_path), os.O_RDONLY | os.O_DIRECTORY)

    with pytest.raises(MmiH2cArchivedSourceV1Error, match="PREPARED_CASE_SCHEMA_INVALID"):

        _build_mmi_h2c_archived_prepared_case_snapshot(

            case_fd=fd, expected_prepared_case_identity_sha256=manifest["prepared_case_identity_sha256"]

        )

    os.close(fd)





def test_role_path_swap(tmp_path, valid_case) -> None:

    manifest, strategy, portfolio = valid_case

    manifest["strategy_settings_source"]["source_record"]["source_role"] = "PORTFOLIO_SNAPSHOT"

    manifest["strategy_settings_source"]["source_record"]["source_record_identity_sha256"] = _identity(manifest["strategy_settings_source"]["source_record"], b"mmi_source_record_v1\0")

    manifest["prepared_case_identity_sha256"] = _identity(manifest, b"mmi_h2c_prepared_case_v1\0")



    archive_dir = tmp_path / "archive"

    archive_dir.mkdir()

    _write_manifest_leaf(tmp_path, json.dumps(manifest).encode("utf-8"))

    (tmp_path / "archive/strategy_settings.yaml").write_bytes(strategy)

    (tmp_path / "archive/portfolio_snapshot.txt").write_bytes(portfolio)



    fd = os.open(str(tmp_path), os.O_RDONLY | os.O_DIRECTORY)

    with pytest.raises(MmiH2cArchivedSourceV1Error, match="PREPARED_CASE_SCHEMA_INVALID"):

        _build_mmi_h2c_archived_prepared_case_snapshot(

            case_fd=fd, expected_prepared_case_identity_sha256=manifest["prepared_case_identity_sha256"]

        )

    os.close(fd)





def test_source_record_mapping_proxy_type_cannot_mutate(case_fd) -> None:

    fd, expected_id = case_fd

    snapshot = _build_mmi_h2c_archived_prepared_case_snapshot(

        case_fd=fd, expected_prepared_case_identity_sha256=expected_id

    )

    with pytest.raises(TypeError):

        snapshot.strategy_source_record["source_role"] = "test"





def test_zero_d4d_invocation(case_fd, monkeypatch) -> None:

    import subprocess



    fd, expected_id = case_fd

    invoked = False

    original_run = subprocess.run

    def mock_run(*args, **kwargs):

        nonlocal invoked

        invoked = True

        return original_run(*args, **kwargs)

    monkeypatch.setattr(subprocess, "run", mock_run)



    _build_mmi_h2c_archived_prepared_case_snapshot(

        case_fd=fd, expected_prepared_case_identity_sha256=expected_id

    )

    assert not invoked



def test_strategy_limit_plus_one(tmp_path, valid_case) -> None:

    manifest, _, portfolio = valid_case

    archive_dir = tmp_path / "archive"

    archive_dir.mkdir()



    too_large = b"x" * (262144 + 1)

    strategy_record = manifest["strategy_settings_source"]["source_record"]

    strategy_record["observed_size_bytes"] = len(too_large)

    strategy_record["observed_sha256"] = hashlib.sha256(too_large).hexdigest()

    strategy_record["source_record_identity_sha256"] = _identity(strategy_record, b"mmi_source_record_v1\0")

    manifest["prepared_case_identity_sha256"] = _identity(manifest, b"mmi_h2c_prepared_case_v1\0")



    _write_manifest_leaf(tmp_path, json.dumps(manifest).encode("utf-8"))

    (tmp_path / "archive/strategy_settings.yaml").write_bytes(too_large)

    (tmp_path / "archive/portfolio_snapshot.txt").write_bytes(portfolio)



    fd = os.open(str(tmp_path), os.O_RDONLY | os.O_DIRECTORY)

    with pytest.raises(MmiH2cArchivedSourceV1Error, match="PREPARED_CASE_SCHEMA_INVALID"):

        _build_mmi_h2c_archived_prepared_case_snapshot(

            case_fd=fd, expected_prepared_case_identity_sha256=manifest["prepared_case_identity_sha256"]

        )

    os.close(fd)





def test_portfolio_limit_plus_one(tmp_path, valid_case) -> None:

    manifest, strategy, _ = valid_case

    archive_dir = tmp_path / "archive"

    archive_dir.mkdir()



    too_large = b"x" * (1048576 + 1)

    portfolio_record = manifest["portfolio_snapshot_source"]["source_record"]

    portfolio_record["observed_size_bytes"] = len(too_large)

    portfolio_record["observed_sha256"] = hashlib.sha256(too_large).hexdigest()

    portfolio_record["source_record_identity_sha256"] = _identity(portfolio_record, b"mmi_source_record_v1\0")

    manifest["prepared_case_identity_sha256"] = _identity(manifest, b"mmi_h2c_prepared_case_v1\0")



    _write_manifest_leaf(tmp_path, json.dumps(manifest).encode("utf-8"))

    (tmp_path / "archive/strategy_settings.yaml").write_bytes(strategy)

    (tmp_path / "archive/portfolio_snapshot.txt").write_bytes(too_large)



    fd = os.open(str(tmp_path), os.O_RDONLY | os.O_DIRECTORY)

    with pytest.raises(MmiH2cArchivedSourceV1Error, match="PREPARED_CASE_SCHEMA_INVALID"):

        _build_mmi_h2c_archived_prepared_case_snapshot(

            case_fd=fd, expected_prepared_case_identity_sha256=manifest["prepared_case_identity_sha256"]

        )

    os.close(fd)





def test_cross_case_substitution(tmp_path, valid_case) -> None:

    manifest, strategy, portfolio = valid_case

    # Create two case directories.

    case1 = tmp_path / "case1"

    case2 = tmp_path / "case2"

    case1.mkdir()

    (case1 / "archive").mkdir()

    case2.mkdir()

    (case2 / "archive").mkdir()



    _write_manifest_leaf(case1, json.dumps(manifest).encode("utf-8"))

    (case1 / "archive/strategy_settings.yaml").write_bytes(strategy)

    (case1 / "archive/portfolio_snapshot.txt").write_bytes(portfolio)



    # Intentionally missing from case2, but present in case1.

    _write_manifest_leaf(case2, json.dumps(manifest).encode("utf-8"))



    fd2 = os.open(str(case2), os.O_RDONLY | os.O_DIRECTORY)

    with pytest.raises(MmiH2cArchivedSourceV1Error, match="ARCHIVE_SOURCE_INPUT_INVALID"):

        _build_mmi_h2c_archived_prepared_case_snapshot(

            case_fd=fd2, expected_prepared_case_identity_sha256=manifest["prepared_case_identity_sha256"]

        )

    os.close(fd2)





def test_repository_relative_locator_never_opened(case_fd, monkeypatch) -> None:

    fd, expected_id = case_fd

    opened_paths = []

    original_open = os.open

    def mock_open(path, flags, *args, **kwargs):

        if isinstance(path, str) and ("repository_relative" in path or "research_dual_lane" in path):

            opened_paths.append(path)

        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", mock_open)



    _build_mmi_h2c_archived_prepared_case_snapshot(

        case_fd=fd, expected_prepared_case_identity_sha256=expected_id

    )

    assert not opened_paths





def test_no_prompt_reads(case_fd, monkeypatch) -> None:

    fd, expected_id = case_fd

    opened_paths = []

    original_open = os.open

    def mock_open(path, flags, *args, **kwargs):

        if isinstance(path, str) and ("prompt" in path):

            opened_paths.append(path)

        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", mock_open)



    _build_mmi_h2c_archived_prepared_case_snapshot(

        case_fd=fd, expected_prepared_case_identity_sha256=expected_id

    )

    assert not opened_paths





def test_no_response_reads(case_fd, monkeypatch) -> None:

    fd, expected_id = case_fd

    opened_paths = []

    original_open = os.open

    def mock_open(path, flags, *args, **kwargs):

        if isinstance(path, str) and ("response" in path):

            opened_paths.append(path)

        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", mock_open)



    _build_mmi_h2c_archived_prepared_case_snapshot(

        case_fd=fd, expected_prepared_case_identity_sha256=expected_id

    )

    assert not opened_paths





def test_no_persistence(case_fd, monkeypatch) -> None:

    fd, expected_id = case_fd

    written = False

    original_write = os.write

    def mock_write(*args, **kwargs):

        nonlocal written

        written = True

        return original_write(*args, **kwargs)

    monkeypatch.setattr(os, "write", mock_write)



    _build_mmi_h2c_archived_prepared_case_snapshot(

        case_fd=fd, expected_prepared_case_identity_sha256=expected_id

    )

    assert not written



def test_does_not_own_or_duplicate_identity_algorithms() -> None:

    from investment_orchestrator.offline import mmi_h2c_archived_source_v1

    assert not hasattr(mmi_h2c_archived_source_v1, "record_identity_sha256")

    assert not hasattr(mmi_h2c_archived_source_v1, "domain_separated_sha256")

    assert not hasattr(mmi_h2c_archived_source_v1, "MMI_SOURCE_RECORD_IDENTITY_DOMAIN")



    source_text = Path(mmi_h2c_archived_source_v1.__file__).read_text()

    assert "mmi_source_record_v1\\0" not in source_text

    assert "mmi_h2c_prepared_case_v1\\0" not in source_text





def test_mid_read_mutation(case_fd, monkeypatch) -> None:

    fd, expected_id = case_fd



    import investment_orchestrator.offline.mmi_h2c_archived_source_v1 as mod

    from investment_orchestrator.mmi.stable_read import MmiStableReadError as MmiH2cStableReadError, MmiStableReadErrorCode as MmiH2cStableReadErrorCode


    original = os.read

    read_count = 0



    def side_effect(inner_fd, length):

        nonlocal read_count

        read_count += 1

        if read_count == 3:

            import errno

            raise OSError(errno.ESTALE, "stale file handle")

        return original(inner_fd, length)



    monkeypatch.setattr(os, "read", side_effect)



    with pytest.raises(MmiH2cArchivedSourceV1Error, match="ARCHIVE_SOURCE_INPUT_INVALID"):

        _build_mmi_h2c_archived_prepared_case_snapshot(

            case_fd=fd, expected_prepared_case_identity_sha256=expected_id

        )



def test_validate_portable_source_record_rejection_leakage(case_fd, monkeypatch) -> None:

    from investment_orchestrator.offline.mmi_h2c_archived_source_v1 import MmiH2cDualSideManualHandoffContextReceiptV1Error

    fd, expected_id = case_fd



    def mock_validate(*args, **kwargs):

        raise MmiH2cDualSideManualHandoffContextReceiptV1Error("MMI_H2C_PORTABLE_EVIDENCE_INVALID")



    monkeypatch.setattr("investment_orchestrator.offline.mmi_h2c_archived_source_v1.validate_portable_source_record_v1", mock_validate)



    with pytest.raises(MmiH2cArchivedSourceV1Error, match="ARCHIVE_SOURCE_SCHEMA_INVALID") as exc_info:

        _build_mmi_h2c_archived_prepared_case_snapshot(case_fd=fd, expected_prepared_case_identity_sha256=expected_id)



    assert isinstance(exc_info.value.__cause__, MmiH2cDualSideManualHandoffContextReceiptV1Error)



def test_json_decode_ownership_proof(tmp_path, valid_case) -> None:

    manifest, strategy, portfolio = valid_case

    archive_dir = tmp_path / "archive"

    archive_dir.mkdir()

    _write_manifest_leaf(tmp_path, b"invalid json")



    fd = os.open(str(tmp_path), os.O_RDONLY | os.O_DIRECTORY)

    try:

        with pytest.raises(MmiH2cArchivedSourceV1Error, match="PREPARED_CASE_INPUT_INVALID") as exc_info:

            _build_mmi_h2c_archived_prepared_case_snapshot(case_fd=fd, expected_prepared_case_identity_sha256=manifest["prepared_case_identity_sha256"])

        assert isinstance(exc_info.value.__cause__, json.JSONDecodeError)

    finally:

        os.close(fd)



def test_unexpected_error_proof(case_fd, monkeypatch) -> None:

    fd, expected_id = case_fd



    def mock_loads(*args, **kwargs):

        raise RuntimeError("unexpected internal error")



    monkeypatch.setattr("json.loads", mock_loads)



    with pytest.raises(RuntimeError, match="unexpected internal error"):

        _build_mmi_h2c_archived_prepared_case_snapshot(case_fd=fd, expected_prepared_case_identity_sha256=expected_id)


def test_root_manifest_json_is_never_a_fallback_leaf(tmp_path, valid_case) -> None:
    """The prepared-case leaf has one canonical location: ``prepared/prepared_case.json``.

    A root ``manifest.json`` (the obsolete test-only layout this file used to
    fabricate) must never be consulted, even when it is present and would
    otherwise parse and validate successfully.
    """
    manifest, strategy, portfolio = valid_case

    archive_dir = tmp_path / "archive"
    archive_dir.mkdir()

    # The canonical leaf is intentionally absent: no ``prepared/`` directory
    # is created and ``_write_manifest_leaf`` is never called.
    (tmp_path / "manifest.json").write_bytes(json.dumps(manifest).encode("utf-8"))
    (tmp_path / "archive/strategy_settings.yaml").write_bytes(strategy)
    (tmp_path / "archive/portfolio_snapshot.txt").write_bytes(portfolio)

    fd = os.open(str(tmp_path), os.O_RDONLY | os.O_DIRECTORY)
    try:
        with pytest.raises(MmiH2cArchivedSourceV1Error, match="PREPARED_CASE_INPUT_INVALID"):
            _build_mmi_h2c_archived_prepared_case_snapshot(
                case_fd=fd,
                expected_prepared_case_identity_sha256=manifest["prepared_case_identity_sha256"],
            )
    finally:
        os.close(fd)


def test_genuine_prepare_owner_case_is_consumed_without_root_manifest(
    tmp_path, monkeypatch
) -> None:
    """One committed prepare-owner case must be consumable by E1 as-is.

    This is the integration oracle for the leaf-alignment fix: it builds a
    real case root with the committed ``prepare_h2c_persisted_case`` owner
    (not a hand-fabricated manifest) and proves ``_build_mmi_h2c_archived_prepared_case_snapshot``
    reads it successfully from ``prepared/prepared_case.json`` alone, with no
    root ``manifest.json`` ever created or required.
    """
    from tests.unit.test_mmi_h2c_consume_persisted_case_v1 import (
        _capture_at,
        _create_prepared_case,
    )
    import investment_orchestrator.offline.mmi_h2c_prepare_persisted_case_v1 as engine_prepare

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        engine_prepare, "capture_current_mmi_source", _capture_at(tmp_path)
    )

    case_root, prepared_identity, _settings_sha256, _portfolio_sha256 = (
        _create_prepared_case(tmp_path)
    )

    assert not (case_root / "manifest.json").exists()
    assert (case_root / "prepared/prepared_case.json").exists()

    expected_strategy_bytes = (case_root / "archive/strategy_settings.yaml").read_bytes()
    expected_portfolio_bytes = (case_root / "archive/portfolio_snapshot.txt").read_bytes()

    case_fd = os.open(os.fspath(case_root), os.O_RDONLY | os.O_DIRECTORY)
    try:
        snapshot = _build_mmi_h2c_archived_prepared_case_snapshot(
            case_fd=case_fd,
            expected_prepared_case_identity_sha256=prepared_identity,
        )
    finally:
        os.close(case_fd)

    assert snapshot.prepared_case_identity_sha256 == prepared_identity
    assert snapshot.strategy_archived_bytes == expected_strategy_bytes
    assert snapshot.portfolio_archived_bytes == expected_portfolio_bytes
    assert snapshot.strategy_source_record["source_role"] == "STRATEGY_SETTINGS"
    assert snapshot.strategy_source_record["observed_sha256"] == hashlib.sha256(
        expected_strategy_bytes
    ).hexdigest()
    assert snapshot.portfolio_source_record["source_role"] == "PORTFOLIO_SNAPSHOT"
    assert snapshot.portfolio_source_record["observed_sha256"] == hashlib.sha256(
        expected_portfolio_bytes
    ).hexdigest()
    assert snapshot.projection.workflow_status == "AWAITING_OPERATOR_RESPONSES"
    assert snapshot.run_context.evaluation_timestamp_utc
