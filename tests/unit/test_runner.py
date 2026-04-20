from textwrap import dedent

import pytest

from investment_system.llm.runner import (
    ManualOutputValidationError,
    PromptRenderError,
    ensure_manual_output_metadata_template,
    inspect_manual_output_metadata,
    manual_output_metadata_path,
    render_prompt,
    strip_wrapped_block,
    validate_daily_quick_check_output,
    validate_override_event_notes_output,
)


def test_strip_wrapped_block_returns_inner_content_when_fully_wrapped() -> None:
    wrapped = dedent(
        """
        TEMPLATE_1A_OUTPUT_START
        {
          "foo": "bar"
        }
        TEMPLATE_1A_OUTPUT_END
        """
    )

    assert strip_wrapped_block(
        wrapped,
        "TEMPLATE_1A_OUTPUT_START",
        "TEMPLATE_1A_OUTPUT_END",
    ) == '{\n  "foo": "bar"\n}'


def test_strip_wrapped_block_returns_raw_text_when_markers_missing() -> None:
    raw = '{\n  "foo": "bar"\n}'

    assert (
        strip_wrapped_block(
            raw,
            "TEMPLATE_1A_OUTPUT_START",
            "TEMPLATE_1A_OUTPUT_END",
        )
        == raw
    )


def test_strip_wrapped_block_returns_raw_text_when_block_is_not_cleanly_wrapped() -> None:
    raw = "note\nTEMPLATE_1A_OUTPUT_START\n{\n  \"foo\": \"bar\"\n}\nTEMPLATE_1A_OUTPUT_END"

    assert (
        strip_wrapped_block(
            raw,
            "TEMPLATE_1A_OUTPUT_START",
            "TEMPLATE_1A_OUTPUT_END",
        )
        == raw
    )


def test_render_prompt_supports_standardized_brace_placeholders() -> None:
    template = "A={{ alpha }}\nB={{ beta_value }}"

    rendered = render_prompt(
        template,
        {
            "alpha": "one",
            "beta_value": "two",
        },
    )

    assert rendered == "A=one\nB=two"


def test_render_prompt_fails_clearly_when_required_placeholder_is_missing() -> None:
    with pytest.raises(PromptRenderError) as exc_info:
        render_prompt("{{ required_value }}", {})

    assert "{{ required_value }} -> provide 'required_value'" in str(exc_info.value)


def test_validate_override_event_notes_output_accepts_empty_list_block() -> None:
    text = dedent(
        """
        OVERRIDE_EVENT_NOTES_START
        []
        OVERRIDE_EVENT_NOTES_END
        """
    )

    assert validate_override_event_notes_output(text) == []


def test_validate_override_event_notes_output_rejects_missing_required_fields() -> None:
    text = dedent(
        """
        OVERRIDE_EVENT_NOTES_START
        - ticker: "IEF"
          source_type: "Fed"
        OVERRIDE_EVENT_NOTES_END
        """
    )

    with pytest.raises(ManualOutputValidationError) as exc_info:
        validate_override_event_notes_output(text)

    assert "missing required keys" in str(exc_info.value)


def test_validate_daily_quick_check_output_accepts_sample_artifact() -> None:
    sample = dedent(
        """
        DAILY_QUICK_CHECK_START
        * as_of:
          date_et: "2026-04-17"
          date_pt: "2026-04-16"
        * primary_status: "NO_ACTION"
        * evaluation_mode: "LIMITED_EVALUATION"
        * break_flags:
          thesis_break: false
          ranking_break: false
          event_shock: false
          concentration_break: false
          execution_break: false
          opportunity_activation: false
        * break_flags_count: 0
        * buy_open_order_maintenance: []
        * buy_open_orders_paste_ready_rows: []
        * sell_open_order_maintenance: []
        * sell_review_queue: []
        * full_rerun_decision:
          run_full_strategy_early: false
          threshold_met: "no"
          minimum_reason: "no"
        * do_today_only: []
        * do_not_do_today: []
        * next_check_trigger: []
        DAILY_QUICK_CHECK_END
        """
    )

    parsed = validate_daily_quick_check_output(sample)

    assert parsed["primary_status"] == "NO_ACTION"


def test_validate_daily_quick_check_output_rejects_invalid_primary_status() -> None:
    sample = dedent(
        """
        DAILY_QUICK_CHECK_START
        * as_of:
          date_et: "2026-04-17"
          date_pt: "2026-04-16"
        * primary_status: "SOMETHING_ELSE"
        * evaluation_mode: "LIMITED_EVALUATION"
        * break_flags:
          thesis_break: false
          ranking_break: false
          event_shock: false
          concentration_break: false
          execution_break: false
          opportunity_activation: false
        * break_flags_count: 0
        * buy_open_order_maintenance: []
        * buy_open_orders_paste_ready_rows: []
        * sell_open_order_maintenance: []
        * sell_review_queue: []
        * full_rerun_decision:
          run_full_strategy_early: false
          threshold_met: "no"
          minimum_reason: "no"
        * do_today_only: []
        * do_not_do_today: []
        * next_check_trigger: []
        DAILY_QUICK_CHECK_END
        """
    )

    with pytest.raises(ManualOutputValidationError) as exc_info:
        validate_daily_quick_check_output(sample)

    assert "primary_status must be one of" in str(exc_info.value)


def test_ensure_manual_output_metadata_template_creates_pending_template(tmp_path) -> None:
    output_path = tmp_path / "daily_quick_check.output.txt"
    prompt_path = tmp_path / "daily_quick_check.prompt.txt"

    metadata_path = ensure_manual_output_metadata_template(
        output_path,
        prompt_path=prompt_path,
    )

    assert metadata_path == manual_output_metadata_path(output_path)
    inspection = inspect_manual_output_metadata(output_path, prompt_path=prompt_path)
    assert inspection["status"] == "pending_manual_fill"
    assert "fill required metadata fields" in inspection["issues"][0]


def test_inspect_manual_output_metadata_accepts_filled_metadata(tmp_path) -> None:
    output_path = tmp_path / "override_event_notes.output.txt"
    prompt_path = tmp_path / "override_event_notes.prompt.txt"
    metadata_path = ensure_manual_output_metadata_template(
        output_path,
        prompt_path=prompt_path,
    )
    metadata_path.write_text(
        dedent(
            """
            {
              "schema_version": "1.0",
              "output_artifact": "override_event_notes.output.txt",
              "prompt_artifact": "override_event_notes.prompt.txt",
              "provider": "chatgpt",
              "model": "gpt-5.4",
              "generated_at": "2026-04-18T20:31:00-04:00",
              "edited_after_generation": false,
              "notes": ""
            }
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )

    inspection = inspect_manual_output_metadata(output_path, prompt_path=prompt_path)

    assert inspection["status"] == "valid"
    assert inspection["issues"] == []
