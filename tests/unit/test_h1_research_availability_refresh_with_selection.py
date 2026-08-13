"""PR-P2A: the SAME-RUN refresh threading seam.

Scope note: this module tests only what P2A adds to the refresh path —
``H1ResearchAvailabilityRefreshResult`` /
``refresh_research_availability_for_h1_replacement_with_selection`` — and that
the existing public ``refresh_research_availability_for_h1_replacement``
contract is unchanged. H1 selection precedence/freshness
(``test_h1_mapped_availability.py``), the projector itself
(``test_h1_mapped_research_selection_projection.py``), and the P2b
clear/engine/refresh lifecycle ordering (``test_h1_p3_availability_lifecycle.py``)
are deliberately not retested here.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from investment_orchestrator.common.io import write_json
from investment_orchestrator.common.paths import repo_root
from investment_orchestrator.state.research_availability import (
    H1MappedResearchSelectionProjection,
)
from investment_orchestrator.workflow import step1_research


@pytest.fixture
def step1_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect the Step 1 root so no test can touch the working tree (J)."""
    monkeypatch.setattr(step1_research, "repo_root", lambda: tmp_path)
    return tmp_path


def _write_legacy_base_context() -> None:
    write_json(step1_research.step1_research_handoff_candidate_path(), {})
    write_json(step1_research.step1_research_output_path(), {})


def _known_availability_artifact_names() -> set[str]:
    return {
        step1_research.step1_research_availability_path().name,
        step1_research.step1_research_freshness_report_path().name,
        step1_research.step1_research_degraded_mode_decision_path().name,
    }


# --- H. legacy refresh return contract is unchanged ----------------------------


def test_legacy_refresh_still_returns_a_plain_string_dict(step1_root: Path) -> None:
    _write_legacy_base_context()

    result = step1_research.refresh_research_availability_for_h1_replacement(
        strategy_settings={"as_of": "2026-06-30"},
    )

    assert isinstance(result, dict)
    assert set(result) == {
        "research_availability_state",
        "research_availability_decision_present",
        "h1_mapped_selected",
    }
    assert all(isinstance(value, str) for value in result.values())


def test_legacy_refresh_equals_the_composites_public_projection(
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Same hermetic input, both entry points: identical legacy-shaped output."""
    legacy_root = tmp_path_factory.mktemp("legacy")
    monkeypatch.setattr(step1_research, "repo_root", lambda: legacy_root)
    _write_legacy_base_context()
    legacy_result = step1_research.refresh_research_availability_for_h1_replacement(
        strategy_settings={"as_of": "2026-06-30"},
    )

    composite_root = tmp_path_factory.mktemp("composite")
    monkeypatch.setattr(step1_research, "repo_root", lambda: composite_root)
    _write_legacy_base_context()
    composite = step1_research.refresh_research_availability_for_h1_replacement_with_selection(
        strategy_settings={"as_of": "2026-06-30"},
    )

    assert composite.public_projection == legacy_result
    assert isinstance(composite.h1_selection, H1MappedResearchSelectionProjection)
    assert composite.h1_selection.h1_mapped_selected is False


# --- I. exactly one evaluation, one write set -----------------------------


def test_composite_path_adds_no_extra_evaluation_or_write_versus_legacy(
    tmp_path_factory: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    call_counts: dict[str, list[str]] = {"legacy": [], "composite": []}
    written: dict[str, list[str]] = {"legacy": [], "composite": []}

    # Captured once, before any monkeypatching, so the "composite" wrapper
    # below can never accidentally re-wrap the already-patched "legacy"
    # callable (which would double-count into the wrong label).
    real_evaluate = step1_research.evaluate_research_availability
    real_write_json = write_json

    def make_counting_evaluate(label: str):
        def counting(**kwargs: object):
            call_counts[label].append("evaluate")
            return real_evaluate(**kwargs)

        return counting

    def make_counting_write_json(label: str):
        def counting(path: Path, payload: object):
            written[label].append(Path(path).name)
            return real_write_json(path, payload)

        return counting

    legacy_root = tmp_path_factory.mktemp("legacy")
    monkeypatch.setattr(step1_research, "repo_root", lambda: legacy_root)
    monkeypatch.setattr(
        step1_research, "evaluate_research_availability", make_counting_evaluate("legacy")
    )
    monkeypatch.setattr(step1_research, "write_json", make_counting_write_json("legacy"))
    _write_legacy_base_context()
    step1_research.refresh_research_availability_for_h1_replacement(
        strategy_settings={"as_of": "2026-06-30"},
    )

    composite_root = tmp_path_factory.mktemp("composite")
    monkeypatch.setattr(step1_research, "repo_root", lambda: composite_root)
    monkeypatch.setattr(
        step1_research, "evaluate_research_availability", make_counting_evaluate("composite")
    )
    monkeypatch.setattr(step1_research, "write_json", make_counting_write_json("composite"))
    _write_legacy_base_context()
    step1_research.refresh_research_availability_for_h1_replacement_with_selection(
        strategy_settings={"as_of": "2026-06-30"},
    )

    # Threading the projection through added zero extra evaluator calls and
    # zero extra writes versus the pre-existing legacy call graph.
    assert len(call_counts["composite"]) == len(call_counts["legacy"])
    assert len(written["composite"]) == len(written["legacy"])

    # Each of the three core availability artifacts is written exactly once.
    known = _known_availability_artifact_names()
    for label in ("legacy", "composite"):
        present = [name for name in written[label] if name in known]
        assert sorted(present) == sorted(known), label


# --- J. hermetic: proves nothing under the real repo root moved -----------


def test_refresh_never_touches_the_real_repo_current_artifacts(step1_root: Path) -> None:
    real_availability_path = (
        repo_root() / "artifacts" / "current" / "step1_research" / "research_availability.json"
    )
    before = (
        real_availability_path.read_bytes() if real_availability_path.exists() else None
    )

    _write_legacy_base_context()
    step1_research.refresh_research_availability_for_h1_replacement_with_selection(
        strategy_settings={"as_of": "2026-06-30"},
    )

    after = real_availability_path.read_bytes() if real_availability_path.exists() else None
    assert after == before
    # And the write actually landed under the hermetic root, not the real one.
    assert (step1_root / "artifacts" / "current" / "step1_research" / "research_availability.json").exists()


# --- K. no qualitative / H1ConsumeResult dependency ------------------------


def _imported_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                names.add(alias.asname or alias.name)
    return names


def test_p2a_files_import_no_qualitative_or_consume_types() -> None:
    root = repo_root() / "src" / "investment_orchestrator"
    names = _imported_names(root / "state" / "research_availability.py")
    names |= _imported_names(root / "workflow" / "step1_research.py")
    assert names.isdisjoint({"H1ConsumeResult", "H1QualitativeResearchFacts"})


# --- L. no other production consumer yet -----------------------------------


def test_new_p2a_symbols_have_no_other_production_consumer() -> None:
    src_root = repo_root() / "src" / "investment_orchestrator"
    guarded_symbols = (
        "H1MappedResearchSelectionProjection",
        "build_h1_mapped_research_selection_projection",
        "H1ResearchAvailabilityRefreshResult",
        "refresh_research_availability_for_h1_replacement_with_selection",
    )
    defining_files = {
        src_root / "state" / "research_availability.py",
        src_root / "workflow" / "step1_research.py",
    }
    offenders: list[str] = []
    for path in sorted(src_root.rglob("*.py")):
        if path in defining_files:
            continue
        text = path.read_text(encoding="utf-8")
        for symbol in guarded_symbols:
            if symbol in text:
                offenders.append(f"{path}:{symbol}")
    assert offenders == []
