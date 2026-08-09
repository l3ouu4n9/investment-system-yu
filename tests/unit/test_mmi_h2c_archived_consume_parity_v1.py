"""Independent live/archive byte-parity oracle for D4e E2 archived consume.

This file proves that, for one genuine prepared case and identical
operator-supplied raw responses, the archived path
(``consume_h2c_persisted_case_from_archives``) produces byte-identical
persisted artifacts and an identical ``H2cConsumeResult`` compared with the
existing live path (``consume_h2c_persisted_case``).

The archived implementation is never used to compute its own expected
output: both paths run independently against separate copies of the same
prepared case, and the comparison is a raw-byte equality check.
"""
from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

import pytest

from investment_orchestrator.offline.mmi_h2c_consume_persisted_case_v1 import (
    consume_h2c_persisted_case,
    consume_h2c_persisted_case_from_archives,
)
from investment_orchestrator.offline.mmi_h2c_prepare_persisted_case_v1 import (
    prepare_h2c_persisted_case,
)

from tests.unit.test_mmi_h2c_prepare_persisted_case_v1 import (
    _settings_bytes,
    _portfolio_bytes,
)
from tests.unit.test_mmi_h2c_consume_persisted_case_v1 import (
    _capture_at,
    _response_handoff,
)

_ARTIFACT_LEAVES = (
    "artifacts/case_evidence_bundle.json",
    "artifacts/comparison_report.json",
    "artifacts/receipt.json",
)


def test_archived_consume_matches_live_consume_byte_for_byte(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import investment_orchestrator.offline.mmi_h2c_prepare_persisted_case_v1 as prepare_engine
    import investment_orchestrator.offline.mmi_h2c_consume_persisted_case_v1 as consume_engine

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        prepare_engine, "capture_current_mmi_source", _capture_at(tmp_path)
    )
    monkeypatch.setattr(
        consume_engine, "capture_current_mmi_source", _capture_at(tmp_path)
    )

    # 1. Create ONE genuine prepared case through the real prepare owner.
    settings, portfolio = _settings_bytes(), _portfolio_bytes()
    current = tmp_path / "inputs/current"
    current.mkdir(parents=True, exist_ok=True)
    (current / "strategy_settings.yaml").write_bytes(settings)
    (current / "portfolio_snapshot.txt").write_bytes(portfolio)
    strategy_sha256 = hashlib.sha256(settings).hexdigest()
    portfolio_sha256 = hashlib.sha256(portfolio).hexdigest()

    origin_root = tmp_path / "case_origin"
    prepared = prepare_h2c_persisted_case(
        strategy_settings_expected_sha256=strategy_sha256,
        portfolio_snapshot_expected_sha256=portfolio_sha256,
        case_root=origin_root,
    )

    # 2. Write identical operator-supplied raw H1 and legacy responses.
    h1_response_bytes, legacy_response_bytes = _response_handoff(
        origin_root / "prompts/h1_prompt.txt"
    )
    (origin_root / "responses/h1_response.raw").write_bytes(h1_response_bytes)
    (origin_root / "responses/legacy_response.raw").write_bytes(
        legacy_response_bytes
    )

    # 3. Duplicate the case BEFORE consume into live_case / archived_case.
    live_root = tmp_path / "live_case"
    archived_root = tmp_path / "archived_case"
    shutil.copytree(origin_root, live_root)
    shutil.copytree(origin_root, archived_root)

    # 4. Consume live_case with the existing live entry.
    live_result = consume_h2c_persisted_case(
        case_root=live_root,
        expected_prepared_case_identity_sha256=(
            prepared.prepared_case_identity_sha256
        ),
        strategy_settings_expected_sha256=strategy_sha256,
        portfolio_snapshot_expected_sha256=portfolio_sha256,
    )

    # 5. Consume archived_case with the new archived entry.
    archived_result = consume_h2c_persisted_case_from_archives(
        case_root=archived_root,
        expected_prepared_case_identity_sha256=(
            prepared.prepared_case_identity_sha256
        ),
    )

    # 6. Compare raw bytes exactly for all three persisted artifacts.
    for leaf in _ARTIFACT_LEAVES:
        live_bytes = (live_root / leaf).read_bytes()
        archived_bytes = (archived_root / leaf).read_bytes()
        assert archived_bytes == live_bytes, f"byte parity failed for {leaf}"

    # 7. Compare H2cConsumeResult exactly.
    assert archived_result == live_result
    assert live_result.workflow_status == "COMPLETED"
