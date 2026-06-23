from __future__ import annotations

import inspect
from typing import Any

import pytest

from investment_orchestrator.parsers import _json_text
from investment_orchestrator.parsers import extract_audit_and_audited_packet as step3_parser
from investment_orchestrator.parsers import extract_daily_execution_check as daily_parser
from investment_orchestrator.parsers import extract_research_json as research_parser
from investment_orchestrator.parsers import extract_template2_and_decision_packet as step2_parser


def test_robust_json_parse_accepts_normal_json() -> None:
    assert _json_text.robust_json_parse('{"ticker": "SPY", "score": 1}') == {
        "ticker": "SPY",
        "score": 1,
    }


def test_robust_json_parse_accepts_fenced_json() -> None:
    assert _json_text.robust_json_parse('```json\n{"ticker": "SPY"}\n```') == {
        "ticker": "SPY"
    }


def test_marked_block_can_be_extracted_and_parsed() -> None:
    block = _json_text.extract_marked_block(
        'prefix\nPAYLOAD_START\n```json\n{"ticker": "SPY"}\n```\nPAYLOAD_END\nsuffix',
        "PAYLOAD_START",
        "PAYLOAD_END",
    )

    assert block is not None
    assert _json_text.robust_json_parse(block) == {"ticker": "SPY"}


def test_invalid_json_escapes_are_repaired_before_parse() -> None:
    payload = _json_text.robust_json_parse(
        r'{"index": "S\&P 500", "pct": "10\%", "field": "snake\_case"}'
    )

    assert payload == {
        "index": "S&P 500",
        "pct": "10%",
        "field": "snake_case",
    }


def test_malformed_json_still_fails_after_repair_and_yaml_fallback() -> None:
    with pytest.raises(_json_text.JsonTextParseError, match="initial JSON parse failed"):
        _json_text.robust_json_parse('{"ticker": [1, }', allow_yaml=True)


def test_yaml_fallback_uses_repaired_text_for_yaml_parsers() -> None:
    assert research_parser.parse_json_like_mapping(r'label: "S\&P 500"') == {
        "label": "S&P 500"
    }
    assert step2_parser.parse_json_like_mapping(
        r'label: "S\%P"',
        context_text="",
    ) == {"label": "S%P"}
    assert step3_parser.parse_audited_decision_packet_text(r'label: "snake\_case"') == {
        "label": "snake_case"
    }


def test_four_target_parsers_delegate_json_decoding_to_common_helper(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, bool, str, bool]] = []

    def fake_robust_json_parse(
        text: str,
        *,
        allow_yaml: bool = False,
        context: str = "JSON text",
        strip_fence: bool = True,
    ) -> dict[str, str]:
        calls.append((text, allow_yaml, context, strip_fence))
        return {"parsed_by": context}

    monkeypatch.setattr(_json_text, "robust_json_parse", fake_robust_json_parse)

    assert research_parser.parse_json_like_mapping("{}") == {"parsed_by": "RESEARCH_JSON"}
    assert daily_parser.parse_daily_execution_actions_text(
        "DAILY_EXECUTION_ACTIONS_START\n{}\nDAILY_EXECUTION_ACTIONS_END"
    ) == {"parsed_by": "DAILY_EXECUTION_ACTIONS block"}
    assert step2_parser.parse_json_like_mapping("{}", context_text="") == {
        "parsed_by": "DECISION_PACKET"
    }
    assert step3_parser.parse_audited_decision_packet_text("{}") == {
        "parsed_by": "AUDITED_DECISION_PACKET"
    }

    assert [(allow_yaml, context) for _, allow_yaml, context, _ in calls] == [
        (True, "RESEARCH_JSON"),
        (False, "DAILY_EXECUTION_ACTIONS block"),
        (True, "DECISION_PACKET"),
        (True, "AUDITED_DECISION_PACKET"),
    ]


def test_target_parsers_no_longer_define_duplicate_json_text_helpers() -> None:
    modules: list[Any] = [
        research_parser,
        daily_parser,
        step2_parser,
        step3_parser,
    ]

    for module in modules:
        source = inspect.getsource(module)
        assert "def strip_code_fence" not in source
        assert "def repair_invalid_json_escapes" not in source
        assert "def extract_marked_block" not in source
