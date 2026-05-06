"""Extract Template 3 audit artifacts from a manual Step 3 output."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import yaml

from investment_orchestrator.common.io import read_text, write_json, write_text
from investment_orchestrator.validators.validate_audited_decision_packet import (
    validate_audited_decision_packet,
)


class Step3ExtractionError(ValueError):
    """Raised when a Step 3 raw output cannot be parsed safely."""


TOP_LEVEL_PACKET_KEY_RE = re.compile(r"^(?P<key>[A-Za-z0-9_]+):(?:\s*(?P<inline>.*))?$")
CITATION_TAIL_RE = re.compile(r"\s*\(\[[^\]]+\]\[\d+\]\)\.?\s*$")
KV_SEGMENT_RE = re.compile(r"(?P<key>[A-Za-z0-9_]+)=(?P<value>.*)")


def strip_code_fence(text: str) -> str:
    """Remove one surrounding Markdown fence when present."""
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped

    lines = stripped.splitlines()
    if len(lines) < 3 or not lines[-1].strip().startswith("```"):
        return stripped
    return "\n".join(lines[1:-1]).strip()


def extract_required_block(text: str, start_marker: str, end_marker: str) -> str:
    """Return the text between two required markers."""
    start = text.find(start_marker)
    if start == -1:
        raise Step3ExtractionError(f"Missing required marker {start_marker!r}.")
    end = text.rfind(end_marker)
    if end == -1 or end <= start:
        raise Step3ExtractionError(f"Missing or malformed closing marker {end_marker!r}.")
    return text[start + len(start_marker) : end].strip()


def extract_optional_block(text: str, start_marker: str, end_marker: str) -> str:
    """Return optional block contents or an empty string when missing."""
    start = text.find(start_marker)
    if start == -1:
        return ""
    end = text.rfind(end_marker)
    if end == -1 or end <= start:
        raise Step3ExtractionError(f"Missing or malformed closing marker {end_marker!r}.")
    return text[start + len(start_marker) : end].strip()


def _clean_value_text(value: str) -> str:
    """Trim surrounding whitespace and common citation tails."""
    return CITATION_TAIL_RE.sub("", value.strip()).strip()


def _maybe_scalar(value: str) -> Any:
    """Convert a scalar-looking string to bool/int/float when safe."""
    cleaned = value.replace(",", "").strip()
    low = cleaned.lower()
    if low == "true":
        return True
    if low == "false":
        return False
    if low == "null":
        return None
    if re.fullmatch(r"-?\d+", cleaned):
        return int(cleaned)
    if re.fullmatch(r"-?\d+\.\d+", cleaned):
        return float(cleaned)
    return value.strip()


def _parse_semicolon_kv_line(text: str) -> dict[str, Any]:
    """Parse `a=1; b=2` style segments into a mapping."""
    parsed: dict[str, Any] = {}
    for segment in [part.strip() for part in text.split(";") if part.strip()]:
        match = KV_SEGMENT_RE.fullmatch(segment)
        if not match:
            parsed.setdefault("_raw", []).append(_clean_value_text(segment))
            continue
        key = match.group("key")
        parsed[key] = _maybe_scalar(_clean_value_text(match.group("value")))
    return parsed


def _packet_lines_to_sections(text: str) -> dict[str, Any]:
    """Split the outline-style audited packet into top-level sections."""
    sections: dict[str, Any] = {}
    current_key: str | None = None
    current_lines: list[str] = []

    def flush() -> None:
        nonlocal current_key, current_lines
        if current_key is None:
            return
        while current_lines and not current_lines[-1].strip():
            current_lines.pop()
        sections[current_key] = current_lines[:]
        current_key = None
        current_lines = []

    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()
        if not stripped:
            if current_key is not None:
                current_lines.append("")
            continue

        match = TOP_LEVEL_PACKET_KEY_RE.fullmatch(line)
        if match and not line.startswith((" ", "\t", "*")):
            flush()
            current_key = match.group("key")
            inline_value = match.group("inline") or ""
            current_lines = [inline_value] if inline_value else []
            continue

        if current_key is None:
            continue
        current_lines.append(line)

    flush()
    return sections


def _section_to_bullets(lines: list[str]) -> list[str]:
    """Extract top-level bullet lines from a section."""
    bullets: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("* "):
            bullets.append(stripped[2:].strip())
    return bullets


def _parse_structured_list(lines: list[str]) -> list[Any]:
    """Parse common bullet-list sections into strings or key/value mappings."""
    output: list[Any] = []
    for bullet in _section_to_bullets(lines):
        if ";" in bullet or "=" in bullet:
            output.append(_parse_semicolon_kv_line(bullet))
            continue
        if ":" in bullet:
            left, right = bullet.split(":", 1)
            output.append({"label": left.strip(), "value": _clean_value_text(right)})
            continue
        output.append(_clean_value_text(bullet))
    return output


def parse_outline_audited_decision_packet(text: str) -> dict[str, Any]:
    """Parse an outline-style AUDITED_DECISION_PACKET into a JSON-like mapping."""
    sections = _packet_lines_to_sections(strip_code_fence(text))
    if not sections:
        raise Step3ExtractionError(
            "AUDITED_DECISION_PACKET is not valid JSON/YAML or supported outline text."
        )

    required = {
        "audit_passed",
        "order_compiler_ready",
        "final_buy_side_delta_table",
        "final_sell_side_delta_table",
        "final_execution_plans",
        "final_sell_execution_plans",
    }
    missing = [key for key in required if key not in sections]
    if missing:
        raise Step3ExtractionError(
            "AUDITED_DECISION_PACKET outline parse failed because required sections were missing: "
            + ", ".join(missing)
        )

    def parse_bool_section(key: str) -> bool:
        lines = sections.get(key, [])
        value = lines[0].strip().lower() if lines else ""
        if value == "true":
            return True
        if value == "false":
            return False
        raise Step3ExtractionError(f"{key} must be true or false in AUDITED_DECISION_PACKET.")

    packet = {
        "audit_passed": parse_bool_section("audit_passed"),
        "order_compiler_ready": parse_bool_section("order_compiler_ready"),
        "final_buy_side_delta_table": _parse_structured_list(sections["final_buy_side_delta_table"]),
        "final_sell_side_delta_table": _parse_structured_list(sections["final_sell_side_delta_table"]),
        "final_execution_plans": _parse_structured_list(sections["final_execution_plans"]),
        "final_sell_execution_plans": _parse_structured_list(sections["final_sell_execution_plans"]),
    }

    for optional_key in (
        "audit_fail_reasons",
        "patches_applied",
        "compiler_blockers",
        "core_deployment_diagnostics",
    ):
        if optional_key in sections:
            packet[optional_key] = _parse_structured_list(sections[optional_key])

    return packet


def parse_audited_decision_packet_text(text: str) -> dict[str, Any]:
    """Parse the audited packet as JSON, YAML, or supported outline text."""
    cleaned = strip_code_fence(text)
    payload: Any

    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError:
        try:
            payload = yaml.safe_load(cleaned)
        except yaml.YAMLError as exc:
            try:
                return parse_outline_audited_decision_packet(cleaned)
            except Step3ExtractionError:
                raise Step3ExtractionError(
                    f"AUDITED_DECISION_PACKET is not valid JSON/YAML: {exc}"
                ) from exc

    if not isinstance(payload, dict):
        return parse_outline_audited_decision_packet(cleaned)
    return payload


def parse_step3_output_text(raw_text: str) -> tuple[str, str, dict[str, Any]]:
    """Parse a raw Step 3 response into audit text, optional patch text, and audited packet."""
    template3_audit_text = extract_required_block(
        raw_text,
        "TEMPLATE3_AUDIT_START",
        "TEMPLATE3_AUDIT_END",
    )
    template2_patch_text = extract_optional_block(
        raw_text,
        "TEMPLATE2_PATCH_START",
        "TEMPLATE2_PATCH_END",
    )
    audited_packet_block = extract_required_block(
        raw_text,
        "AUDITED_DECISION_PACKET_START",
        "AUDITED_DECISION_PACKET_END",
    )
    audited_packet = parse_audited_decision_packet_text(audited_packet_block)
    validate_audited_decision_packet(audited_packet)
    return template3_audit_text, template2_patch_text, audited_packet


def extract_audit_and_audited_packet(
    *,
    raw_output_path: str | Path,
    template3_audit_path: str | Path,
    template2_patch_path: str | Path,
    audited_decision_packet_path: str | Path,
) -> tuple[str, str, dict[str, Any]]:
    """Read, parse, validate, and write Step 3 artifacts."""
    template3_audit_text, template2_patch_text, audited_packet = parse_step3_output_text(
        read_text(raw_output_path)
    )
    write_text(template3_audit_path, template3_audit_text.rstrip() + "\n")
    write_text(
        template2_patch_path,
        template2_patch_text.rstrip() + "\n" if template2_patch_text else "",
    )
    write_json(audited_decision_packet_path, audited_packet)
    return template3_audit_text, template2_patch_text, audited_packet


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="Extract TEMPLATE3_AUDIT, optional TEMPLATE2_PATCH, and AUDITED_DECISION_PACKET from Step 3 output."
    )
    parser.add_argument("--raw-output", required=True, help="Path to step3 raw_output.txt")
    parser.add_argument("--template3-audit", required=True, help="Path to write template3_audit.txt")
    parser.add_argument("--template2-patch", required=True, help="Path to write template2_patch.txt")
    parser.add_argument(
        "--audited-decision-packet",
        required=True,
        help="Path to write audited_decision_packet.json",
    )
    args = parser.parse_args()

    extract_audit_and_audited_packet(
        raw_output_path=Path(args.raw_output),
        template3_audit_path=Path(args.template3_audit),
        template2_patch_path=Path(args.template2_patch),
        audited_decision_packet_path=Path(args.audited_decision_packet),
    )
    print(args.audited_decision_packet)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
