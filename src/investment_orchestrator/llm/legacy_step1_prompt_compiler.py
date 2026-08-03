"""Pure, explicit-input legacy Step 1 prompt rendering policy.

This module owns placeholder rendering, sanitization, and terminal-newline
policy for the legacy Step 1 prompt. It performs no filesystem, environment,
clock, MMI, provider, network, publication, order, or broker access; every
input arrives as an already-read text value from its caller.
"""

from __future__ import annotations

import json
import re

from investment_orchestrator.llm.manual_output import render_prompt
from investment_orchestrator.validators.strategy_settings import parse_strategy_settings_text


CURRENT_RUN_INPUT_NOTES_RE = re.compile(
    r"(?:\r?\n)*────────────────────────────────────────\r?\n"
    r"【Current Run Inputs（injected by workflow; rendered prompt must contain actual values, not placeholder notes）】"
    r"[\s\S]*?(?=CURRENT_RUN_INPUTS_START)",
)


def sanitize_rendered_step1_prompt(text: str) -> str:
    """Remove workflow-only current-run explanatory notes from the rendered prompt."""
    return CURRENT_RUN_INPUT_NOTES_RE.sub("\n", text, count=1)


def derive_legacy_approved_extended_etf_json(
    *,
    strategy_settings_text: str,
) -> str:
    """Derive the approved extended-ETF static list JSON from settings text."""
    strategy_settings = parse_strategy_settings_text(strategy_settings_text)
    approved_static_list = strategy_settings.get("user_approved_extended_etf_static_list")
    if approved_static_list is None:
        raise ValueError(
            "Missing required field 'user_approved_extended_etf_static_list' in "
            "inputs/current/strategy_settings.yaml"
        )
    if not isinstance(approved_static_list, list):
        raise ValueError(
            "inputs/current/strategy_settings.yaml field "
            "'user_approved_extended_etf_static_list' must be a list."
        )
    if not all(isinstance(item, str) for item in approved_static_list):
        raise ValueError(
            "inputs/current/strategy_settings.yaml field "
            "'user_approved_extended_etf_static_list' must contain only strings."
        )
    return json.dumps(approved_static_list, ensure_ascii=False, indent=2)


def compile_legacy_step1_prompt_text(
    *,
    template_text: str,
    strategy_settings_text: str,
    portfolio_snapshot_text: str,
    approved_extended_etf_json: str,
) -> str:
    """Render the Step 1 prompt from explicit, already-read input text."""
    rendered_prompt = render_prompt(
        template_text.rstrip(),
        {
            "current_run_user_approved_extended_etf_static_list_json": approved_extended_etf_json,
            "strategy_settings_yaml": strategy_settings_text,
            "portfolio_snapshot": portfolio_snapshot_text,
        },
    )
    return sanitize_rendered_step1_prompt(rendered_prompt).rstrip() + "\n"
