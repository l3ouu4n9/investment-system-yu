"""Extract and validate RESEARCH_JSON from a manual Step 1 output."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from investment_orchestrator.common.io import read_text, write_json
from investment_orchestrator.parsers import _json_text
from investment_orchestrator.validators.validate_research_output import validate_research_output


class ResearchExtractionError(ValueError):
    """Raised when a Step 1 raw output cannot be parsed into RESEARCH_JSON."""


def extract_first_balanced_json(text: str) -> str | None:
    """Find the first balanced JSON object in free-form text."""
    start = text.find("{")
    while start != -1:
        depth = 0
        in_string = False
        escape = False
        for index in range(start, len(text)):
            char = text[index]
            if in_string:
                if escape:
                    escape = False
                elif char == "\\":
                    escape = True
                elif char == '"':
                    in_string = False
                continue

            if char == '"':
                in_string = True
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    return text[start : index + 1]
        start = text.find("{", start + 1)
    return None


def parse_json_like_mapping(text: str) -> dict[str, Any]:
    """Parse JSON first, then YAML as a fallback, and require a mapping root."""
    try:
        return _json_text.parse_json_like_mapping(
            text,
            allow_yaml=True,
            context="RESEARCH_JSON",
        )
    except _json_text.JsonTextParseError as exc:
        raise ResearchExtractionError(str(exc)) from exc


def parse_research_output_text(raw_text: str) -> dict[str, Any]:
    """Parse a raw Step 1 output string into a validated RESEARCH_JSON payload."""
    try:
        candidate = _json_text.extract_marked_block(
            raw_text,
            "RESEARCH_JSON_START",
            "RESEARCH_JSON_END",
        )
    except _json_text.JsonTextParseError as exc:
        raise ResearchExtractionError(str(exc)) from exc
    if candidate is None:
        candidate = extract_first_balanced_json(raw_text)
    if candidate is None:
        raise ResearchExtractionError(
            "Could not find RESEARCH_JSON_START/END or any balanced JSON object in Step 1 raw output."
        )

    payload = parse_json_like_mapping(candidate)
    validate_research_output(payload)
    return payload


def extract_research_json(
    *,
    raw_output_path: str | Path,
    output_path: str | Path,
    pretty: bool = True,
) -> dict[str, Any]:
    """Read, parse, validate, and write RESEARCH_JSON."""
    payload = parse_research_output_text(read_text(raw_output_path))
    write_json(output_path, payload if pretty else payload)
    return payload


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Extract RESEARCH_JSON from a manual Step 1 output.")
    parser.add_argument("--raw-output", required=True, help="Path to step1 raw_output.txt")
    parser.add_argument("--output", required=True, help="Path to write research_output.json")
    args = parser.parse_args()

    extract_research_json(
        raw_output_path=Path(args.raw_output),
        output_path=Path(args.output),
        pretty=True,
    )
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
