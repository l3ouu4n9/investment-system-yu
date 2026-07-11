"""Exact committed-Phase-2A versus coordinated-working-tree compatibility."""

from __future__ import annotations

import json
import subprocess
import sys
import types
from copy import deepcopy
from pathlib import Path
from typing import Any

from investment_orchestrator.offline.retirement_evidence import archive_contract as c
from investment_orchestrator.offline.retirement_evidence import archive_coordination as coord
from investment_orchestrator.offline.retirement_evidence import archive_record_contract as rc
from investment_orchestrator.offline.retirement_evidence import ingest as current
from investment_orchestrator.research.step1a_retirement_observation import (
    _minimal_incomplete_observation,
)

from test_retirement_evidence_archive import _STAMP, _TOOL, _obs
from test_step1a_retirement_observation import _builder_inputs


def _committed_ingest_module() -> types.ModuleType:
    relative = "src/investment_orchestrator/offline/retirement_evidence/ingest.py"
    completed = subprocess.run(
        ["git", "show", f"HEAD:{relative}"],
        check=True,
        capture_output=True,
        text=True,
    )
    name = "investment_orchestrator.offline.retirement_evidence._committed_ingest_baseline"
    module = types.ModuleType(name)
    module.__file__ = f"<git-HEAD:{relative}>"
    module.__package__ = "investment_orchestrator.offline.retirement_evidence"
    sys.modules[name] = module
    exec(compile(completed.stdout, module.__file__, "exec"), module.__dict__)
    return module


def _write(path: Path, payload: Any, *, compact: bool = False) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(payload, bytes):
        path.write_bytes(payload)
    elif compact:
        path.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    else:
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


def _call(module: types.ModuleType, source: Path, root: Path, anchor: Path, **kwargs: Any):
    arguments = {
        "source_path": source,
        "dest_root": root,
        "tool_identity": _TOOL,
        "archived_at": _STAMP,
        **kwargs,
    }
    if module is current:
        arguments["coordination_path"] = anchor
    return module.ingest_observation(**arguments)


def _normalized_result(result: Any, root: Path) -> dict[str, Any]:
    archived = Path(result.archived_path) if result.archived_path is not None else None
    return {
        "decision": result.decision,
        "reason_tokens": tuple(result.reason_tokens),
        "archived_path": str(archived.relative_to(root)) if archived is not None else None,
        "duplicate": result.duplicate,
        "conflict": result.conflict,
        "record_content_sha256": result.record_content_sha256,
        "source_file_sha256": result.source_file_sha256,
        "source_canonical_payload_sha256": result.source_canonical_payload_sha256,
    }


def _normalized_exception(exc: Exception) -> dict[str, Any]:
    return {
        "type": type(exc).__name__,
        "token": getattr(exc, "token", None),
        "record_basename": getattr(exc, "record_basename", None),
    }


def _snapshot(root: Path) -> dict[str, tuple[str, bytes | None]]:
    if not root.exists():
        return {}
    result: dict[str, tuple[str, bytes | None]] = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if path.is_dir():
            result[relative] = ("directory", None)
        else:
            result[relative] = ("file", path.read_bytes())
    return result


def _run_scenario(module: types.ModuleType, base: Path, scenario: str) -> dict[str, Any]:
    root = base / "archive"
    anchor = base / "coordination.anchor"
    base.mkdir(parents=True, exist_ok=True)
    anchor.write_bytes(coord.COORDINATION_ANCHOR_BYTES)
    outcomes: list[dict[str, Any]] = []

    def ingest(payload: Any, name: str, **kwargs: Any) -> Any:
        source = _write(base / "sources" / name, payload)
        try:
            value = _call(module, source, root, anchor, **kwargs)
        except Exception as exc:  # expected integrity scenarios are normalized
            outcomes.append({"exception": _normalized_exception(exc)})
            return exc
        outcomes.append({"result": _normalized_result(value, root)})
        return value

    if scenario == "accepted_default":
        ingest(_obs(), "source.json")
    elif scenario == "accepted_explicit":
        ingest(
            _obs(),
            "source.json",
            claimed_provenance=c.PROVENANCE_INTEGRATION_TEST,
            provenance_claim_source=c.PROVENANCE_CLAIM_SOURCE_OPERATOR,
        )
    elif scenario == "quarantined":
        packet = deepcopy(_builder_inputs()["evidence_packet"])
        packet.pop("strategy_settings_hash")
        ingest(_obs(evidence_packet=packet), "source.json")
    elif scenario == "dirty_identity":
        ingest(
            _obs(code_identity={"git_commit": "1" * 40, "git_state": "dirty"}),
            "source.json",
        )
    elif scenario == "unavailable_identity":
        ingest(
            _obs(code_identity={"git_commit": None, "git_state": "unavailable"}),
            "source.json",
        )
    elif scenario == "minimal_builder_error":
        ingest(_minimal_incomplete_observation(_STAMP), "source.json")
    elif scenario == "malformed_rejection":
        ingest(b"{ not valid json ", "source.json")
    elif scenario == "unknown_schema_rejection":
        payload = _obs()
        payload["schema_version"] = "other_schema_v9"
        ingest(payload, "source.json")
    elif scenario == "completeness_contradiction":
        payload = _obs()
        payload["missing_observation_fields"] = ["missing_one"]
        ingest(payload, "source.json")
    elif scenario == "duplicate_noop":
        payload = _obs()
        ingest(payload, "first.json")
        ingest(payload, "second.json")
    elif scenario == "canonical_payload_duplicate":
        payload = _obs()
        first = _write(base / "sources" / "pretty.json", payload)
        second = _write(base / "sources" / "compact.json", payload, compact=True)
        for source in (first, second):
            value = _call(module, source, root, anchor)
            outcomes.append({"result": _normalized_result(value, root)})
    elif scenario == "observation_id_conflict":
        payload = _obs()
        ingest(payload, "first.json")
        changed = deepcopy(payload)
        changed["guard_summaries"]["evidence_packet"]["differences_count"] = 1
        ingest(changed, "changed.json")
    elif scenario == "filename_collision":
        original = _obs()
        first = ingest(original, "first.json")
        assert not isinstance(first, Exception)
        changed = _obs(generated_at="2026-07-10T12:00:01+00:00")
        changed_sha = current.p1a.canonical_sha256(changed)
        assert changed_sha is not None
        collision_name = rc.expected_observation_record_filename(
            changed,
            rc.stored_observation_id(changed),
            changed_sha,
        )
        collision = root / c.PARTITION_ACCEPTED / collision_name
        collision.write_bytes(Path(first.archived_path).read_bytes())
        ingest(changed, "changed.json")
    elif scenario == "existing_record_integrity_failure":
        payload = _obs()
        first = ingest(payload, "first.json")
        assert not isinstance(first, Exception)
        archived = Path(first.archived_path)
        record = json.loads(archived.read_text(encoding="utf-8"))
        record["archive_record_content_sha256"] = "b" * 64
        archived.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        ingest(payload, "second.json")
    else:  # pragma: no cover - fixed code-owned scenario list
        raise AssertionError(scenario)

    snapshot = _snapshot(root)
    serialized = b"".join(data or b"" for kind, data in snapshot.values() if kind == "file")
    assert b"coordination" not in serialized
    return {"outcomes": outcomes, "snapshot": snapshot}


def test_complete_committed_phase2a_behavior_matches_coordinated_working_tree(
    tmp_path: Path,
) -> None:
    committed = _committed_ingest_module()
    scenarios = (
        "accepted_default",
        "accepted_explicit",
        "quarantined",
        "dirty_identity",
        "unavailable_identity",
        "minimal_builder_error",
        "malformed_rejection",
        "unknown_schema_rejection",
        "completeness_contradiction",
        "duplicate_noop",
        "canonical_payload_duplicate",
        "observation_id_conflict",
        "filename_collision",
        "existing_record_integrity_failure",
    )
    for scenario in scenarios:
        baseline = _run_scenario(committed, tmp_path / "baseline" / scenario, scenario)
        coordinated = _run_scenario(current, tmp_path / "coordinated" / scenario, scenario)
        assert coordinated == baseline, scenario
