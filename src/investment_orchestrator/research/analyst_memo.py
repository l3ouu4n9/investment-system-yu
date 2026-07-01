"""Step 1B small analyst-memo schema + parser + validator (R2C, report-only).

The analyst memo is the *only* LLM output in the evidence-first Step 1
architecture, and it is intentionally tiny: **qualitative opinion only**. It
cannot create an allowed universe, cannot set budgets, cannot emit orders or an
execution-authority field, and cannot bypass the validator / degraded-mode gate.
It is an advisory *input* for a future deterministic compiler (R2D) — never an
execution authority on its own.

This module is strictly report-only. ``parse_analyst_memo_text`` is a pure
function (text in, result out; never raises) so it is fully testable without
disk. The Step 1 workflow writes ``analyst_memo.json`` /
``analyst_memo_validation.json`` only when a raw memo output exists, and an
invalid memo never changes ``research_degraded_mode_decision`` or any allowed
action: a memo can never permit ``NEW_BUY``.

Safety posture: the parser does **not** trust the LLM. Every rule below is
enforced deterministically; a memo that violates any rule is marked invalid
(``valid=False``) and simply not consumed — it never widens permissions.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
import json
from typing import Any, Iterator


SCHEMA_VERSION = "analyst_memo_v1"

# The qualitative confidence is constrained to exactly these values.
CONFIDENCE_VALUES = ("low", "medium", "high")

# Per-ticker relative stance is a qualitative lean only — never an order action.
STANCE_VALUES = ("prefer", "neutral", "deprioritize")

# Keys that, if present at ANY depth, would let the memo recreate an allowed
# universe / strict handoff (which must stay deterministic). Hard reject.
FORBIDDEN_UNIVERSE_KEYS = (
    "trade_universe",
    "allowed_buy_tickers",
    "buy_universe_scorecard",
    "strategy_a_research_handoff",
    "allowed_buy_universe",
    "effective_allowed_buy_universe",
    "extended_etf_candidate_universe",
)

# Named budget keys the memo must never carry (budgets stay deterministic).
FORBIDDEN_BUDGET_KEYS = (
    "hard_cap_open_orders_budget",
    "target_new_buy_budget_this_run",
)

# Any key containing one of these substrings implies budget / sizing authority
# the memo must not have. The analyst_memo_v1 schema contains none of these
# substrings, so this is a safe over-broad guard with no false positives.
FORBIDDEN_BUDGET_SUBSTRINGS = ("budget", "allocation", "cap")

# Keys that would make the memo an authoritative action / order intent. Reject.
FORBIDDEN_ACTION_KEYS = (
    "allowed_actions",
    "final_action",
    "action",
    "actions",
    "order",
    "orders",
    "order_intent",
    "order_compilation",
    "order_instruction",
    "order_sizing",
    "buy_order",
    "buy_orders",
    "sell_order",
    "sell_orders",
    "new_buy",
    "compile_ready",
    "execution_authorization",
    "authorize",
    "authorize_execution",
)

# Authoritative action tokens that must not appear as a standalone scalar value
# (exact match, case-insensitive). A narrative sentence is never exactly one of
# these, so free-text rationale is unaffected.
FORBIDDEN_ACTION_VALUE_TOKENS = (
    "new_buy",
    "order_compilation",
    "buy_order",
    "sell_order",
    "order_instruction",
)

# Fields whose values carry tickers that must live inside the deterministic
# evidence universe. Free-text exposure descriptions (preferred_exposures,
# avoid_or_deprioritize) are NOT ticker fields and are not checked here.
TICKER_FIELDS = ("ticker_relative_view",)


@dataclass(frozen=True)
class AnalystMemoParseResult:
    """Outcome of parsing + validating a raw analyst-memo output. Report-only."""

    present: bool
    valid: bool
    memo: dict[str, Any] | None
    problems: list[str] = field(default_factory=list)
    parse_error: str | None = None
    evidence_universe: list[str] = field(default_factory=list)


# --- evidence universe -------------------------------------------------------


def evidence_universe_from_packet(packet: Any) -> list[str]:
    """Return the in-universe ticker set the memo may reference.

    This is the deterministic ``allowed_buy_tickers`` plus the pre-approved
    extended ETF static list (both qualitative-opinion-eligible). The memo can
    express a relative view only on these; anything else is rejected.
    """
    if not isinstance(packet, Mapping):
        return []
    universe = packet.get("universe")
    if not isinstance(universe, Mapping):
        return []
    allowed = _normalize_tickers(universe.get("allowed_buy_tickers"))
    approved = _normalize_tickers(universe.get("approved_extended_etf"))
    return _dedupe_preserve_order([*allowed, *approved])


# --- parse + validate (pure; never raises) -----------------------------------


def parse_analyst_memo_text(
    raw_text: str | None,
    *,
    evidence_universe: list[str] | None = None,
) -> AnalystMemoParseResult:
    """Parse raw memo text and validate it against the evidence universe.

    Pure function: an absent / empty raw output yields ``present=False`` (not an
    error), malformed JSON yields ``present=True, valid=False`` with a
    ``parse_error``, and a structurally valid object is validated by
    :func:`validate_analyst_memo`. Never raises.
    """
    universe = list(evidence_universe or [])
    present = isinstance(raw_text, str) and raw_text.strip() != ""
    if not present:
        return AnalystMemoParseResult(
            present=False,
            valid=False,
            memo=None,
            problems=["analyst memo raw output is absent or empty (treated as absent, not an error)."],
            parse_error=None,
            evidence_universe=universe,
        )

    obj, parse_error = _extract_memo_object(raw_text)  # type: ignore[arg-type]
    if obj is None:
        return AnalystMemoParseResult(
            present=True,
            valid=False,
            memo=None,
            problems=[f"analyst memo is not valid JSON: {parse_error}"],
            parse_error=parse_error,
            evidence_universe=universe,
        )
    if not isinstance(obj, Mapping):
        return AnalystMemoParseResult(
            present=True,
            valid=False,
            memo=None,
            problems=["analyst memo top-level must be a JSON object."],
            parse_error=None,
            evidence_universe=universe,
        )

    problems = validate_analyst_memo(obj, evidence_universe=universe)
    return AnalystMemoParseResult(
        present=True,
        valid=not problems,
        memo=dict(obj),
        problems=problems,
        parse_error=None,
        evidence_universe=universe,
    )


def validate_analyst_memo(
    memo: Mapping[str, Any],
    *,
    evidence_universe: list[str] | None = None,
) -> list[str]:
    """Return a list of safety/contract violations (empty list = valid).

    Enforced deterministically (the LLM is never trusted):

    * ``schema_version`` must be ``analyst_memo_v1``.
    * ``is_llm_generated`` must be exactly ``True``.
    * ``confidence`` must be one of low / medium / high.
    * No budget keys anywhere (named or any key containing budget/cap/allocation).
    * No allowed-universe / strict-handoff keys anywhere.
    * No execution-authority / order-intent keys anywhere, and no authoritative
      action token (NEW_BUY / ORDER_COMPILATION / BUY_ORDER / …) as a scalar value.
    * Every ``ticker_relative_view`` ticker must be inside the evidence universe.
    * Each ``ticker_relative_view`` stance must be prefer / neutral / deprioritize.
    """
    universe = {t for t in (evidence_universe or [])}
    problems: list[str] = []

    if not isinstance(memo, Mapping):
        return ["analyst memo top-level must be a JSON object."]

    # schema_version
    schema_version = memo.get("schema_version")
    if schema_version != SCHEMA_VERSION:
        problems.append(
            f"schema_version must be {SCHEMA_VERSION!r} (got {schema_version!r})."
        )

    # is_llm_generated must be exactly True (the memo is, by definition, LLM opinion).
    if memo.get("is_llm_generated") is not True:
        problems.append("is_llm_generated must be exactly true.")

    # confidence enum
    confidence = memo.get("confidence")
    if not (isinstance(confidence, str) and confidence.strip().lower() in CONFIDENCE_VALUES):
        problems.append(
            f"confidence must be one of {list(CONFIDENCE_VALUES)} (got {confidence!r})."
        )

    # Forbidden keys (recursive, at any depth).
    for raw_key in _iter_keys(memo):
        if not isinstance(raw_key, str):
            continue
        key = raw_key.strip().lower()
        if key in {k.lower() for k in FORBIDDEN_UNIVERSE_KEYS}:
            problems.append(f"forbidden allowed-universe/handoff key present: {raw_key!r}.")
        if key in {k.lower() for k in FORBIDDEN_BUDGET_KEYS}:
            problems.append(f"forbidden budget key present: {raw_key!r}.")
        elif any(sub in key for sub in FORBIDDEN_BUDGET_SUBSTRINGS):
            problems.append(f"forbidden budget/sizing key present (implies authority): {raw_key!r}.")
        if key in {k.lower() for k in FORBIDDEN_ACTION_KEYS}:
            problems.append(f"forbidden execution-authority/order-intent key present: {raw_key!r}.")

    # Forbidden authoritative action tokens as scalar values (exact match).
    for value in _iter_string_values(memo):
        if value.strip().lower() in FORBIDDEN_ACTION_VALUE_TOKENS:
            problems.append(
                f"forbidden authoritative action token used as a value: {value!r} "
                "(the memo cannot set a final action / order intent)."
            )

    # ticker_relative_view: in-universe tickers only, valid stance.
    view = memo.get("ticker_relative_view")
    if view is not None:
        if not isinstance(view, list):
            problems.append("ticker_relative_view must be a list when present.")
        else:
            for index, row in enumerate(view):
                if not isinstance(row, Mapping):
                    problems.append(f"ticker_relative_view[{index}] must be an object.")
                    continue
                ticker_raw = row.get("ticker")
                ticker = ticker_raw.strip().upper() if isinstance(ticker_raw, str) else None
                if not ticker:
                    problems.append(f"ticker_relative_view[{index}] is missing a ticker.")
                elif ticker not in universe:
                    problems.append(
                        f"ticker_relative_view[{index}] ticker {ticker!r} is outside the "
                        "deterministic evidence universe (the memo cannot create new tickers)."
                    )
                stance = row.get("stance")
                if stance is not None and not (
                    isinstance(stance, str) and stance.strip().lower() in STANCE_VALUES
                ):
                    problems.append(
                        f"ticker_relative_view[{index}] stance must be one of {list(STANCE_VALUES)} "
                        f"(got {stance!r})."
                    )

                # Optional anchor_id_refs: type/format only. The memo may only
                # *reference* existing research anchors — it can never create one;
                # existence / freshness / applicability are validated deterministically
                # downstream (support_signals against evidence_packet.research_anchors).
                refs = row.get("anchor_id_refs")
                if refs is not None:
                    if not isinstance(refs, list):
                        problems.append(
                            f"ticker_relative_view[{index}].anchor_id_refs must be a list when present."
                        )
                    else:
                        for ref_index, ref in enumerate(refs):
                            if not (isinstance(ref, str) and ref.strip()):
                                problems.append(
                                    f"ticker_relative_view[{index}].anchor_id_refs[{ref_index}] "
                                    "must be a non-empty string."
                                )

    return problems


# --- report-only artifact serialization --------------------------------------


def analyst_memo_parse_result_to_dict(result: AnalystMemoParseResult) -> dict[str, Any]:
    """Serialize the parse result into the report-only validation artifact."""
    memo = result.memo if isinstance(result.memo, Mapping) else None
    return {
        "schema_version": SCHEMA_VERSION,
        "report_only": True,
        "present": result.present,
        "valid": result.valid,
        "problems": list(result.problems),
        "parse_error": result.parse_error,
        "evidence_universe": list(result.evidence_universe),
        "memo_schema_version": memo.get("schema_version") if memo else None,
        "memo_is_llm_generated_claim": memo.get("is_llm_generated") if memo else None,
        "memo_confidence": memo.get("confidence") if memo else None,
        "permission_effect": (
            "none (report-only): the analyst memo never permits NEW_BUY, never sets budgets, "
            "never creates an allowed universe, and does not change allowed_actions or the "
            "research_degraded_mode_decision. It is an advisory input only."
        ),
    }


# --- prompt rendering --------------------------------------------------------


def render_analyst_memo_prompt(*, prompt_template: str, evidence_packet: Any) -> str:
    """Render the analyst-memo prompt with the evidence packet injected.

    Uses the shared strict placeholder renderer; the template's only placeholder
    is ``{{ evidence_packet_json }}``.
    """
    from investment_orchestrator.llm.manual_output import render_prompt

    evidence_json = json.dumps(evidence_packet, ensure_ascii=False, indent=2)
    return render_prompt(prompt_template, {"evidence_packet_json": evidence_json})


# --- helpers -----------------------------------------------------------------


def _extract_memo_object(raw_text: str) -> tuple[Any | None, str | None]:
    """Best-effort extraction of a single JSON object from a raw memo output."""
    text = raw_text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    try:
        return json.loads(text), None
    except json.JSONDecodeError as exc:
        first_error = str(exc)

    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(text[start : end + 1]), None
        except json.JSONDecodeError as exc:
            return None, str(exc)
    return None, first_error


def _iter_keys(obj: Any) -> Iterator[Any]:
    if isinstance(obj, Mapping):
        for key, value in obj.items():
            yield key
            yield from _iter_keys(value)
    elif isinstance(obj, list):
        for item in obj:
            yield from _iter_keys(item)


def _iter_string_values(obj: Any) -> Iterator[str]:
    if isinstance(obj, Mapping):
        for value in obj.values():
            yield from _iter_string_values(value)
    elif isinstance(obj, list):
        for item in obj:
            yield from _iter_string_values(item)
    elif isinstance(obj, str):
        yield obj


def _normalize_tickers(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        if isinstance(item, str) and item.strip():
            out.append(item.strip().upper())
    return _dedupe_preserve_order(out)


def _dedupe_preserve_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            out.append(value)
    return out
