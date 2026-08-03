"""Focused tests for the pure explicit-input legacy Step 1 prompt compiler.

These tests prove that extracting placeholder rendering, sanitization, and
approved-list derivation into ``llm.legacy_step1_prompt_compiler`` preserved
the exact pre-extraction behavior of ``step1_research.build_step1_prompt_text``,
including its double read of ``inputs/current/strategy_settings.yaml``.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from investment_orchestrator.common.io import read_text
from investment_orchestrator.llm.legacy_step1_prompt_compiler import (
    CURRENT_RUN_INPUT_NOTES_RE,
    compile_legacy_step1_prompt_text,
    derive_legacy_approved_extended_etf_json,
    sanitize_rendered_step1_prompt,
)
from investment_orchestrator.llm.manual_output import PromptRenderError, render_prompt
from investment_orchestrator.validators.strategy_settings import (
    StrategySettingsValidationError,
    parse_strategy_settings_text,
)
from investment_orchestrator.workflow import step1_research


# ---------------------------------------------------------------------------
# Sanitizer literal oracle (§5 / §11): the pattern must equal an independently
# copied literal, not a hash. The pattern is copied here by hand from the
# committed source at src/investment_orchestrator/llm/legacy_step1_prompt_compiler.py.
# ---------------------------------------------------------------------------

_LITERAL_NOTES_PATTERN = (
    r"(?:\r?\n)*────────────────────────────────────────\r?\n"
    r"【Current Run Inputs（injected by workflow; rendered prompt must contain actual values, not placeholder notes）】"
    r"[\s\S]*?(?=CURRENT_RUN_INPUTS_START)"
)


def test_sanitizer_pattern_equals_independently_copied_literal() -> None:
    assert CURRENT_RUN_INPUT_NOTES_RE.pattern == _LITERAL_NOTES_PATTERN


_NOTES_HEADER = (
    "────────────────────────────────────────\n"
    "【Current Run Inputs（injected by workflow; rendered prompt must contain "
    "actual values, not placeholder notes）】"
)


def test_sanitizer_removes_only_the_first_notes_block() -> None:
    text = (
        "PRELUDE\n"
        + _NOTES_HEADER
        + "\nfirst block body\n"
        + "CURRENT_RUN_INPUTS_START\n"
        + "MIDDLE\n"
        + _NOTES_HEADER
        + "\nsecond block body\n"
        + "CURRENT_RUN_INPUTS_START\n"
        + "TAIL\n"
    )
    sanitized = sanitize_rendered_step1_prompt(text)
    assert "first block body" not in sanitized
    assert "second block body" in sanitized
    assert sanitized.count("CURRENT_RUN_INPUTS_START") == 2


def test_poison_in_injected_settings_does_not_move_committed_template_first_match() -> None:
    """Substitution occurs before sanitization, and this is inert for the real template.

    The three real placeholders sit strictly after the notes-removal region's
    lookahead terminus in the committed template, so injecting sanitizer-like
    text into any placeholder value cannot move the first match. This proves
    substitution happens first (the injected text is present in the rendered
    string when the regex scans it) and that this ordering is harmless for the
    committed template today.
    """
    template_text = read_text(step1_research.resolve_step1_prompt_template_path())
    real_settings_text = read_text(
        step1_research.current_inputs_dir() / "strategy_settings.yaml"
    )
    real_portfolio_text = read_text(
        step1_research.current_inputs_dir() / "portfolio_snapshot.txt"
    )
    approved_json = derive_legacy_approved_extended_etf_json(
        strategy_settings_text=real_settings_text,
    )

    poison = (
        "\n────────────────────────────────────────\n"
        "【Current Run Inputs（injected by workflow; rendered prompt must contain "
        "actual values, not placeholder notes）】\n"
        "INJECTED_POISON_BODY\n"
        "CURRENT_RUN_INPUTS_START\n"
    )

    baseline_rendered = render_prompt(
        template_text.rstrip(),
        {
            "current_run_user_approved_extended_etf_static_list_json": approved_json,
            "strategy_settings_yaml": real_settings_text,
            "portfolio_snapshot": real_portfolio_text,
        },
    )
    poisoned_rendered = render_prompt(
        template_text.rstrip(),
        {
            "current_run_user_approved_extended_etf_static_list_json": approved_json,
            "strategy_settings_yaml": real_settings_text + poison,
            "portfolio_snapshot": real_portfolio_text,
        },
    )

    baseline_match = CURRENT_RUN_INPUT_NOTES_RE.search(baseline_rendered)
    poisoned_match = CURRENT_RUN_INPUT_NOTES_RE.search(poisoned_rendered)
    assert baseline_match is not None
    assert poisoned_match is not None
    assert baseline_match.start() == poisoned_match.start()
    assert baseline_match.group(0) == poisoned_match.group(0)

    sanitized_baseline = sanitize_rendered_step1_prompt(baseline_rendered)
    sanitized_poisoned = sanitize_rendered_step1_prompt(poisoned_rendered)
    assert "INJECTED_POISON_BODY" in sanitized_poisoned
    assert sanitized_poisoned.replace(poison, "") == sanitized_baseline


def test_output_always_has_exactly_one_terminal_newline() -> None:
    for suffix in ("", "\n", "\n\n\n"):
        result = compile_legacy_step1_prompt_text(
            template_text=(
                "Header {{ strategy_settings_yaml }} {{ portfolio_snapshot }} "
                "{{ current_run_user_approved_extended_etf_static_list_json }}" + suffix
            ),
            strategy_settings_text="settings-body",
            portfolio_snapshot_text="portfolio-body",
            approved_extended_etf_json="[]",
        )
        assert result.endswith("\n")
        assert not result.endswith("\n\n")


def test_crlf_template_preserves_existing_text_level_rendering_behavior() -> None:
    template_text = (
        "Header\r\n"
        "{{ strategy_settings_yaml }}\r\n"
        "{{ portfolio_snapshot }}\r\n"
        "{{ current_run_user_approved_extended_etf_static_list_json }}\r\n"
    )
    result = compile_legacy_step1_prompt_text(
        template_text=template_text,
        strategy_settings_text="settings-body",
        portfolio_snapshot_text="portfolio-body",
        approved_extended_etf_json="[]",
    )
    assert "settings-body" in result
    assert "portfolio-body" in result
    assert "[]" in result
    assert result.endswith("\n")
    assert not result.endswith("\n\n")


# ---------------------------------------------------------------------------
# Approved-list derivation owner (§6 / §12).
# ---------------------------------------------------------------------------


def test_missing_approved_list_field_raises_verbatim_message() -> None:
    with pytest.raises(
        ValueError,
        match=r"Missing required field 'user_approved_extended_etf_static_list'",
    ):
        derive_legacy_approved_extended_etf_json(strategy_settings_text="{}\n")


def test_non_list_approved_value_raises_verbatim_message() -> None:
    with pytest.raises(ValueError, match=r"must be a list\."):
        derive_legacy_approved_extended_etf_json(
            strategy_settings_text="user_approved_extended_etf_static_list: not-a-list\n",
        )


def test_non_string_item_raises_verbatim_message() -> None:
    with pytest.raises(ValueError, match=r"must contain only strings\."):
        derive_legacy_approved_extended_etf_json(
            strategy_settings_text=(
                "user_approved_extended_etf_static_list:\n"
                "  - 123\n"
            ),
        )


def test_non_ascii_entries_remain_unescaped_with_two_space_indent() -> None:
    strategy_settings_text = (
        "user_approved_extended_etf_static_list:\n"
        "  - Ticker-Omega-Ω\n"
        "  - 日本国債\n"
    )
    result = derive_legacy_approved_extended_etf_json(
        strategy_settings_text=strategy_settings_text,
    )
    assert "Ticker-Omega-Ω" in result
    assert "日本国債" in result
    assert "\\u" not in result
    assert result == json.dumps(
        ["Ticker-Omega-Ω", "日本国債"],
        ensure_ascii=False,
        indent=2,
    )


# ---------------------------------------------------------------------------
# Explicit-input prompt compiler placeholder behavior (§7 / §12).
# ---------------------------------------------------------------------------


def test_unknown_placeholder_propagates_prompt_render_error() -> None:
    with pytest.raises(PromptRenderError):
        compile_legacy_step1_prompt_text(
            template_text="{{ unknown_placeholder }}",
            strategy_settings_text="settings",
            portfolio_snapshot_text="portfolio",
            approved_extended_etf_json="[]",
        )


def test_all_three_placeholders_are_substituted() -> None:
    template_text = (
        "A:{{ strategy_settings_yaml }}\n"
        "B:{{ portfolio_snapshot }}\n"
        "C:{{ current_run_user_approved_extended_etf_static_list_json }}\n"
    )
    result = compile_legacy_step1_prompt_text(
        template_text=template_text,
        strategy_settings_text="SETTINGS_VALUE",
        portfolio_snapshot_text="PORTFOLIO_VALUE",
        approved_extended_etf_json="APPROVED_VALUE",
    )
    assert "A:SETTINGS_VALUE" in result
    assert "B:PORTFOLIO_VALUE" in result
    assert "C:APPROVED_VALUE" in result


# ---------------------------------------------------------------------------
# Oracle 1 (§9): an independent test-only reference composition that shares
# only render_prompt and parse_strategy_settings_text with the new compiler,
# and never calls derive_legacy_approved_extended_etf_json,
# compile_legacy_step1_prompt_text, or sanitize_rendered_step1_prompt on the
# reference side.
# ---------------------------------------------------------------------------


def _independent_reference_render(
    *,
    template_text: str,
    strategy_settings_text: str,
    portfolio_snapshot_text: str,
) -> str:
    settings_mapping = parse_strategy_settings_text(strategy_settings_text)
    approved_list = settings_mapping["user_approved_extended_etf_static_list"]
    if not isinstance(approved_list, list) or not all(
        isinstance(item, str) for item in approved_list
    ):
        raise ValueError("reference fixture must supply a valid approved list")
    approved_json_reference = json.dumps(approved_list, ensure_ascii=False, indent=2)

    rendered = render_prompt(
        template_text.rstrip(),
        {
            "current_run_user_approved_extended_etf_static_list_json": approved_json_reference,
            "strategy_settings_yaml": strategy_settings_text,
            "portfolio_snapshot": portfolio_snapshot_text,
        },
    )
    sanitized = re.compile(_LITERAL_NOTES_PATTERN).sub("\n", rendered, count=1)
    return sanitized.rstrip() + "\n"


def test_independent_reference_composition_matches_new_compiler() -> None:
    template_text = (
        "HEADER_LINE\n"
        "────────────────────────────────────────\n"
        "【Current Run Inputs（injected by workflow; rendered prompt must contain "
        "actual values, not placeholder notes）】\n"
        "placeholder notes explaining the injected values go here\n"
        "CURRENT_RUN_INPUTS_START\n"
        "SETTINGS: {{ strategy_settings_yaml }}\n"
        "PORTFOLIO: {{ portfolio_snapshot }}\n"
        "APPROVED: {{ current_run_user_approved_extended_etf_static_list_json }}\n"
    )
    strategy_settings_text = (
        "user_approved_extended_etf_static_list:\n"
        "  - REF_TICKER_ONE\n"
        "  - REF_TICKER_TWO\n"
    )
    portfolio_snapshot_text = "REF_PORTFOLIO_TEXT_BODY\n"

    reference_output = _independent_reference_render(
        template_text=template_text,
        strategy_settings_text=strategy_settings_text,
        portfolio_snapshot_text=portfolio_snapshot_text,
    )

    approved_extended_etf_json = derive_legacy_approved_extended_etf_json(
        strategy_settings_text=strategy_settings_text,
    )
    actual_output = compile_legacy_step1_prompt_text(
        template_text=template_text,
        strategy_settings_text=strategy_settings_text,
        portfolio_snapshot_text=portfolio_snapshot_text,
        approved_extended_etf_json=approved_extended_etf_json,
    )

    assert reference_output == actual_output


def test_independent_reference_composition_matches_committed_inputs() -> None:
    """Differential check against the committed template/current inputs.

    This exists to catch drift between the reference composition and the new
    compiler on the real production inputs, but it does not pin their
    rendered SHA-256 — operator edits to inputs/current/* must not require
    re-pinning this test.
    """
    template_text = read_text(step1_research.resolve_step1_prompt_template_path())
    strategy_settings_text = read_text(
        step1_research.current_inputs_dir() / "strategy_settings.yaml"
    )
    portfolio_snapshot_text = read_text(
        step1_research.current_inputs_dir() / "portfolio_snapshot.txt"
    )

    reference_output = _independent_reference_render(
        template_text=template_text,
        strategy_settings_text=strategy_settings_text,
        portfolio_snapshot_text=portfolio_snapshot_text,
    )

    approved_extended_etf_json = derive_legacy_approved_extended_etf_json(
        strategy_settings_text=strategy_settings_text,
    )
    actual_output = compile_legacy_step1_prompt_text(
        template_text=template_text,
        strategy_settings_text=strategy_settings_text,
        portfolio_snapshot_text=portfolio_snapshot_text,
        approved_extended_etf_json=approved_extended_etf_json,
    )

    assert reference_output == actual_output
    assert reference_output == step1_research.build_step1_prompt_text()


# ---------------------------------------------------------------------------
# Double-read compatibility (§8 / §10): build_step1_prompt_text() must still
# read inputs/current/strategy_settings.yaml twice, independently, in the
# order template -> settings -> portfolio -> settings.
# ---------------------------------------------------------------------------

FIRST_SETTINGS_TEXT = "FIRST_SETTINGS_UNIQUE_MARKER_9f3a1c\n"
SECOND_SETTINGS_TEXT = (
    "user_approved_extended_etf_static_list:\n"
    "  - TICKER_SECOND_A\n"
    "  - TICKER_SECOND_B\n"
)


def test_first_settings_text_is_invalid_as_strategy_settings() -> None:
    with pytest.raises(StrategySettingsValidationError):
        parse_strategy_settings_text(FIRST_SETTINGS_TEXT)


def test_wrapper_reads_settings_twice_independently_in_the_existing_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_read_text = step1_research.read_text
    expected_template_path = step1_research.resolve_step1_prompt_template_path()
    expected_settings_path = step1_research.current_inputs_dir() / "strategy_settings.yaml"
    expected_portfolio_path = step1_research.current_inputs_dir() / "portfolio_snapshot.txt"

    read_events: list[tuple[Path, str]] = []
    settings_read_count = 0

    def fake_read_text(path: str | Path) -> str:
        nonlocal settings_read_count
        normalized = Path(path)

        if normalized == expected_settings_path:
            settings_read_count += 1
            returned = (
                FIRST_SETTINGS_TEXT
                if settings_read_count == 1
                else SECOND_SETTINGS_TEXT
            )
        else:
            returned = real_read_text(path)

        read_events.append((normalized, returned))
        return returned

    monkeypatch.setattr(step1_research, "read_text", fake_read_text)

    rendered = step1_research.build_step1_prompt_text()

    assert settings_read_count == 2
    read_paths = [event[0] for event in read_events]
    assert read_paths == [
        expected_template_path,
        expected_settings_path,
        expected_portfolio_path,
        expected_settings_path,
    ]

    # The first settings read supplies raw prompt injection ...
    assert "FIRST_SETTINGS_UNIQUE_MARKER_9f3a1c" in rendered
    # ... and is never parsed as strategy settings: rendering succeeded even
    # though FIRST_SETTINGS_TEXT would raise if parsed (proven above).

    # The second settings read alone supplies the approved-list derivation.
    expected_approved_json = json.dumps(
        ["TICKER_SECOND_A", "TICKER_SECOND_B"],
        ensure_ascii=False,
        indent=2,
    )
    assert expected_approved_json in rendered
