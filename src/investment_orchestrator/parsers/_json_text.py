"""Shared JSON-text parsing helpers for LLM parser outputs."""

from __future__ import annotations

import json
import re
from typing import Any

import yaml


class JsonTextParseError(ValueError):
    """Raised when JSON-like parser text cannot be decoded."""


# A backslash that does not begin a valid JSON escape sequence. The first
# alternative consumes well-formed escapes (including ``\\``) so they are never
# rewritten; only stray backslashes reach the capturing group.
_INVALID_JSON_ESCAPE_RE = re.compile(r'\\(?:["\\/bfnrtu])|\\(.)', re.DOTALL)


def strip_code_fence(text: str) -> str:
    """Remove one surrounding Markdown code fence when present."""
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped

    lines = stripped.splitlines()
    if len(lines) < 3 or not lines[-1].strip().startswith("```"):
        return stripped
    return "\n".join(lines[1:-1]).strip()


def extract_marked_block(text: str, start_marker: str, end_marker: str) -> str | None:
    """Return the trimmed text between two markers when both exist."""
    start = text.find(start_marker)
    if start == -1:
        return None
    end = text.rfind(end_marker)
    if end == -1 or end <= start:
        raise JsonTextParseError(
            f"Missing or malformed closing marker {end_marker!r} for {start_marker!r}."
        )
    return text[start + len(start_marker) : end].strip()


def repair_invalid_json_escapes(text: str) -> str:
    """Drop stray backslashes that LLM output over-escapes.

    Valid JSON escapes (``\\"`` ``\\\\`` ``\\/`` ``\\b`` ``\\f`` ``\\n`` ``\\r`` ``\\t``
    ``\\uXXXX``) are preserved; a backslash before any other character is removed,
    leaving the intended literal character.
    """

    def _replace(match: re.Match[str]) -> str:
        stray = match.group(1)
        return match.group(0) if stray is None else stray

    return _INVALID_JSON_ESCAPE_RE.sub(_replace, text)


def _format_json_error(exc: json.JSONDecodeError) -> str:
    return f"{exc.msg} at line {exc.lineno} column {exc.colno} (char {exc.pos})"


def robust_json_parse(
    text: str,
    *,
    allow_yaml: bool = False,
    context: str = "JSON text",
    strip_fence: bool = True,
) -> Any:
    """Parse JSON text, repairing invalid escapes before retrying.

    The parse order is intentionally stable:
    1. ``json.loads`` on the cleaned text.
    2. Repair stray JSON backslashes and retry ``json.loads``.
    3. If enabled, run ``yaml.safe_load`` on the repaired text.
    """
    cleaned = strip_code_fence(text) if strip_fence else text.strip()
    failures: list[str] = []

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as exc:
        failures.append(f"initial JSON parse failed: {_format_json_error(exc)}")

    repaired = repair_invalid_json_escapes(cleaned)
    try:
        return json.loads(repaired)
    except json.JSONDecodeError as exc:
        failures.append(f"JSON parse after invalid-escape repair failed: {_format_json_error(exc)}")

    if allow_yaml:
        try:
            return yaml.safe_load(repaired)
        except yaml.YAMLError as exc:
            failures.append(f"YAML fallback failed: {exc}")

    expected = "JSON/YAML" if allow_yaml else "JSON"
    raise JsonTextParseError(f"{context} is not valid {expected}: " + " | ".join(failures))


def parse_json_like_mapping(
    text: str,
    *,
    allow_yaml: bool = False,
    context: str = "JSON text",
    strip_fence: bool = True,
) -> dict[str, Any]:
    """Parse JSON-like text and require a mapping root."""
    payload = robust_json_parse(
        text,
        allow_yaml=allow_yaml,
        context=context,
        strip_fence=strip_fence,
    )
    if not isinstance(payload, dict):
        raise JsonTextParseError(f"{context} must parse to a JSON object.")
    return payload
