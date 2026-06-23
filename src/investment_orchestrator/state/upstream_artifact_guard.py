"""Deterministic guards for downstream workflow artifact dependencies."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

from investment_orchestrator.common.io import write_json


UPSTREAM_GATE_BLOCKED_REASON = "upstream_research_gate_blocked"
MISSING_REQUIRED_UPSTREAM_ARTIFACT_REASON = "missing_required_upstream_artifact"

# Permission fields copied verbatim from an upstream permission / blocked
# artifact into the downstream blocked artifact for self-contained diagnostics.
_PERMISSION_FIELDS = (
    "state",
    "research_availability",
    "allowed_actions",
    "blocked_actions",
    "manual_review_required",
    "blocker_reasons",
    "non_blocker_reasons",
    "recommended_result",
)


class UpstreamArtifactGuardError(RuntimeError):
    """Raised when a downstream step must not consume upstream artifacts."""


@dataclass(frozen=True)
class UpstreamArtifactGuardResult:
    """Result of checking whether a downstream step may proceed."""

    blocked: bool
    reason: str | None
    blocked_by_artifact: Path | None
    missing_required_artifacts: list[Path]
    stale_or_inconsistent_artifacts: list[str]


def enforce_upstream_artifact_guard(
    *,
    blocked_artifact_path: Path,
    upstream_blocked_artifacts: list[Path],
    required_artifacts: list[Path],
    repo_root_path: Path | None = None,
    permission_fallback_artifacts: list[Path] | None = None,
) -> UpstreamArtifactGuardResult:
    """Fail closed when an upstream gate blocked or required artifacts are absent.

    When blocked, upstream research/degraded-mode permission metadata is read
    (best-effort) and embedded into the blocked artifact for self-contained
    diagnostics. A metadata read failure never relaxes the gate; it is recorded
    in ``upstream_permission_read_errors`` and the guard still fails closed.
    """
    result = evaluate_upstream_artifact_guard(
        upstream_blocked_artifacts=upstream_blocked_artifacts,
        required_artifacts=required_artifacts,
    )
    if not result.blocked:
        return result

    permission, read_errors = resolve_upstream_permission_metadata(
        blocked_by_artifact=result.blocked_by_artifact,
        fallback_artifacts=permission_fallback_artifacts or [],
        repo_root_path=repo_root_path,
    )
    write_json(
        blocked_artifact_path,
        upstream_blocked_artifact_payload(
            result,
            repo_root_path=repo_root_path,
            upstream_permission=permission,
            upstream_permission_read_errors=read_errors,
        ),
    )
    raise UpstreamArtifactGuardError(
        "Downstream workflow blocked by upstream artifact guard: "
        f"reason={result.reason}; "
        f"blocked_artifact={_display_path(blocked_artifact_path, repo_root_path)}"
    )


def evaluate_upstream_artifact_guard(
    *,
    upstream_blocked_artifacts: list[Path],
    required_artifacts: list[Path],
) -> UpstreamArtifactGuardResult:
    """Evaluate upstream blocked artifacts and required artifact presence."""
    for artifact_path in upstream_blocked_artifacts:
        if artifact_path.is_file():
            return UpstreamArtifactGuardResult(
                blocked=True,
                reason=UPSTREAM_GATE_BLOCKED_REASON,
                blocked_by_artifact=artifact_path,
                missing_required_artifacts=[],
                stale_or_inconsistent_artifacts=[],
            )

    missing_required_artifacts = [
        artifact_path for artifact_path in required_artifacts if not artifact_path.is_file()
    ]
    if missing_required_artifacts:
        return UpstreamArtifactGuardResult(
            blocked=True,
            reason=MISSING_REQUIRED_UPSTREAM_ARTIFACT_REASON,
            blocked_by_artifact=None,
            missing_required_artifacts=missing_required_artifacts,
            stale_or_inconsistent_artifacts=[],
        )

    return UpstreamArtifactGuardResult(
        blocked=False,
        reason=None,
        blocked_by_artifact=None,
        missing_required_artifacts=[],
        stale_or_inconsistent_artifacts=[],
    )


def upstream_blocked_artifact_payload(
    result: UpstreamArtifactGuardResult,
    *,
    repo_root_path: Path | None = None,
    upstream_permission: dict[str, Any] | None = None,
    upstream_permission_read_errors: list[str] | None = None,
) -> dict[str, Any]:
    """Build the deterministic no-trade blocked artifact for Step 3/4.

    ``manual_review_required`` is inherited from upstream permission metadata
    when present (a boolean); otherwise it falls back to the conservative
    default of ``False``. ``stale_or_inconsistent_artifacts`` records that the
    upstream gate already determined the no-trade state.
    """
    read_errors = list(upstream_permission_read_errors or [])

    manual_review_required = False
    if isinstance(upstream_permission, dict) and isinstance(
        upstream_permission.get("manual_review_required"), bool
    ):
        manual_review_required = upstream_permission["manual_review_required"]

    stale_or_inconsistent = list(result.stale_or_inconsistent_artifacts)
    if result.reason == UPSTREAM_GATE_BLOCKED_REASON and result.blocked_by_artifact is not None:
        stale_or_inconsistent.append(
            "upstream_gate_already_blocked:"
            + _display_path(result.blocked_by_artifact, repo_root_path)
        )

    return {
        "blocked": True,
        "reason": result.reason,
        "blocked_by_artifact": (
            _display_path(result.blocked_by_artifact, repo_root_path)
            if result.blocked_by_artifact is not None
            else None
        ),
        "missing_required_artifacts": [
            _display_path(path, repo_root_path) for path in result.missing_required_artifacts
        ],
        "stale_or_inconsistent_artifacts": stale_or_inconsistent,
        "recommended_result": "NO_TRADE",
        "manual_review_required": manual_review_required,
        "report_only": False,
        "upstream_permission": upstream_permission,
        "upstream_permission_read_errors": read_errors,
    }


def resolve_upstream_permission_metadata(
    *,
    blocked_by_artifact: Path | None,
    fallback_artifacts: list[Path],
    repo_root_path: Path | None = None,
) -> tuple[dict[str, Any] | None, list[str]]:
    """Resolve upstream permission metadata for a blocked downstream step.

    Source priority: (A) the upstream blocked artifact that triggered the block
    (e.g. the Step 2 / Step 3 blocked artifact), then (B) its ``source_artifact``
    pointer (the Step 1 degraded-mode decision) for enrichment, then (C) any
    fallback artifacts supplied by the caller (the Step 1 decision directly).
    Read failures are recorded and never relax the gate.
    """
    read_errors: list[str] = []

    sources: list[Path] = []
    if blocked_by_artifact is not None:
        sources.append(blocked_by_artifact)
    sources.extend(fallback_artifacts)

    for source in sources:
        obj, error = _read_json_object(source, repo_root_path)
        if error is not None:
            read_errors.append(error)
            continue

        nested = obj.get("upstream_permission")
        if isinstance(nested, dict) and _has_permission_fields(nested):
            return _normalize_permission(nested, source, repo_root_path), read_errors

        if _has_permission_fields(obj):
            permission = _normalize_permission(obj, source, repo_root_path)
            _enrich_from_source_artifact(permission, obj, read_errors, repo_root_path)
            return permission, read_errors

        read_errors.append(
            f"{_display_path(source, repo_root_path)} contains no upstream permission metadata."
        )

    return None, read_errors


def _read_json_object(
    path: Path,
    repo_root_path: Path | None,
) -> tuple[dict[str, Any] | None, str | None]:
    if not path.is_file():
        return None, f"upstream permission source not found: {_display_path(path, repo_root_path)}"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return None, f"could not read {_display_path(path, repo_root_path)}: {exc}"
    if not isinstance(payload, dict):
        return None, f"{_display_path(path, repo_root_path)} is not a JSON object."
    return payload, None


def _has_permission_fields(obj: dict[str, Any]) -> bool:
    return "state" in obj or "allowed_actions" in obj


def _normalize_permission(
    obj: dict[str, Any],
    source: Path,
    repo_root_path: Path | None,
) -> dict[str, Any]:
    permission = {field: obj.get(field) for field in _PERMISSION_FIELDS}
    source_artifact = obj.get("source_artifact")
    permission["source_artifact"] = (
        source_artifact if isinstance(source_artifact, str) else _display_path(source, repo_root_path)
    )
    return permission


def _enrich_from_source_artifact(
    permission: dict[str, Any],
    obj: dict[str, Any],
    read_errors: list[str],
    repo_root_path: Path | None,
) -> None:
    """Enrich permission with Step 1 decision fields via the source_artifact pointer."""
    source_artifact = obj.get("source_artifact")
    if not isinstance(source_artifact, str) or repo_root_path is None:
        return
    decision_path = repo_root_path / source_artifact
    decision, error = _read_json_object(decision_path, repo_root_path)
    if error is not None:
        read_errors.append(error)
        return
    for field in ("research_availability", "non_blocker_reasons", "state"):
        if not permission.get(field):
            permission[field] = decision.get(field)


def _display_path(path: Path, repo_root_path: Path | None) -> str:
    if repo_root_path is not None:
        try:
            return str(path.relative_to(repo_root_path))
        except ValueError:
            pass
    return str(path)
