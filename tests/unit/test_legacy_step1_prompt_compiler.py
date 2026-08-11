"""Focused tests for the pure explicit-input legacy Step 1 prompt compiler.

These tests prove that extracting placeholder rendering, sanitization, and
approved-list derivation into ``llm.legacy_step1_prompt_compiler`` preserved
the exact pre-extraction behavior of ``step1_research.build_step1_prompt_text``,
including its single strategy-settings snapshot behavior.
"""

from __future__ import annotations

import ast
import hashlib
import json
import re
from pathlib import Path

import pytest

import investment_orchestrator as package
from investment_orchestrator.common.io import read_text
from investment_orchestrator.common.paths import repo_root
from investment_orchestrator.llm.legacy_step1_prompt_compiler import (
    CURRENT_RUN_INPUT_NOTES_RE,
    compile_legacy_step1_prompt_text,
    derive_legacy_approved_extended_etf_json,
    sanitize_rendered_step1_prompt,
)
from investment_orchestrator.llm.manual_output import PromptRenderError, render_prompt
from investment_orchestrator.validators.strategy_settings import (
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
# Single-snapshot consistency (§8 / §10): build_step1_prompt_text() must read
# inputs/current/strategy_settings.yaml and portfolio_snapshot.txt once each as
# bytes in the order template -> settings -> portfolio, then derive both
# strategy representations from that one retained strategy source read.
# ---------------------------------------------------------------------------

S1_SETTINGS_TEXT = (
    "strategy_snapshot_marker: STRATEGY_S1_UNIQUE_MARKER_9f3a1c\n"
    "user_approved_extended_etf_static_list:\n"
    "  - TICKER_S1_A\n"
    "  - TICKER_S1_B\n"
)
S2_SETTINGS_TEXT = (
    "strategy_snapshot_marker: STRATEGY_S2_UNIQUE_MARKER_4b8de0\n"
    "user_approved_extended_etf_static_list:\n"
    "  - TICKER_S2_A\n"
    "  - TICKER_S2_B\n"
)


def test_private_strategy_byte_decoder_matches_legacy_read_text(
    tmp_path: Path,
) -> None:
    raw_bytes = (
        b"\xef\xbb\xbfmarker: caf\xc3\xa9\r\n"
        b"second: \xe6\x9d\xb1\xe4\xba\xac\r"
        b"third: \xce\xa9\n"
        b"last: no-trailing-newline"
    )
    path = tmp_path / "strategy_settings.yaml"
    path.write_bytes(raw_bytes)

    assert step1_research._decode_legacy_text_from_exact_bytes(  # noqa: SLF001
        raw_bytes
    ) == path.read_text(encoding="utf-8")


def test_strategy_loader_preserves_strict_invalid_utf8_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "strategy_settings.yaml").write_bytes(b"\xff")
    monkeypatch.setattr(step1_research, "current_inputs_dir", lambda: tmp_path)

    with pytest.raises(UnicodeDecodeError) as raised:
        step1_research.load_strategy_settings_yaml_text()

    assert raised.value.encoding == "utf-8"
    assert raised.value.object == b"\xff"
    assert raised.value.start == 0
    assert raised.value.end == 1


def test_strategy_loader_preserves_missing_error_and_cause(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "strategy_settings.yaml"
    monkeypatch.setattr(step1_research, "current_inputs_dir", lambda: tmp_path)

    with pytest.raises(
        FileNotFoundError,
        match=re.escape(f"Missing required strategy settings YAML input: {path}"),
    ) as raised:
        step1_research.load_strategy_settings_yaml_text()

    assert isinstance(raised.value.__cause__, FileNotFoundError)


@pytest.mark.parametrize("text", ["", " \t\r\n"], ids=["empty", "whitespace"])
def test_strategy_loader_validates_decoded_empty_text(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    text: str,
) -> None:
    path = tmp_path / "strategy_settings.yaml"
    path.write_bytes(text.encode("utf-8"))
    monkeypatch.setattr(step1_research, "current_inputs_dir", lambda: tmp_path)

    with pytest.raises(
        ValueError,
        match=re.escape(f"Required strategy settings YAML input is empty: {path}"),
    ):
        step1_research.load_strategy_settings_yaml_text()


def test_strategy_loader_follows_strategy_settings_symlink(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "actual_strategy_settings.yaml"
    target.write_bytes(b"strategy_marker: follows-symlink\r\n")
    strategy_path = tmp_path / "strategy_settings.yaml"
    strategy_path.symlink_to(target)
    monkeypatch.setattr(step1_research, "current_inputs_dir", lambda: tmp_path)

    assert step1_research.load_strategy_settings_yaml_text() == target.read_text(
        encoding="utf-8"
    )


def test_strategy_loader_preserves_directory_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "strategy_settings.yaml").mkdir()
    monkeypatch.setattr(step1_research, "current_inputs_dir", lambda: tmp_path)

    with pytest.raises(IsADirectoryError):
        step1_research.load_strategy_settings_yaml_text()


def test_portfolio_loader_uses_one_exact_byte_read_and_legacy_text_semantics(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_bytes = (
        b"\xef\xbb\xbfportfolio: caf\xc3\xa9\r\n"
        b"second: \xe6\x9d\xb1\xe4\xba\xac\r"
        b"third: \xce\xa9\n"
        b"last: no-trailing-newline"
    )
    expected_text = (
        "\ufeffportfolio: café\n"
        "second: 東京\n"
        "third: Ω\n"
        "last: no-trailing-newline"
    )
    path = tmp_path / "portfolio_snapshot.txt"
    path.write_bytes(raw_bytes)
    monkeypatch.setattr(step1_research, "current_inputs_dir", lambda: tmp_path)

    real_read_bytes = Path.read_bytes
    byte_read_paths: list[Path] = []

    def fake_read_bytes(read_path: Path) -> bytes:
        byte_read_paths.append(read_path)
        return real_read_bytes(read_path)

    def forbidden_read_text(*args: object, **kwargs: object) -> str:
        pytest.fail("portfolio snapshot must not be reopened through Path.read_text")

    monkeypatch.setattr(Path, "read_bytes", fake_read_bytes)
    monkeypatch.setattr(Path, "read_text", forbidden_read_text)

    assert step1_research.load_portfolio_snapshot_text() == expected_text
    assert byte_read_paths == [path]


def test_portfolio_loader_preserves_missing_error_and_cause(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "portfolio_snapshot.txt"
    monkeypatch.setattr(step1_research, "current_inputs_dir", lambda: tmp_path)

    with pytest.raises(
        FileNotFoundError,
        match=re.escape(f"Missing required portfolio snapshot input: {path}"),
    ) as raised:
        step1_research.load_portfolio_snapshot_text()

    assert isinstance(raised.value.__cause__, FileNotFoundError)


@pytest.mark.parametrize(
    "raw_bytes",
    [b"", b" \t\r\n\r "],
    ids=["empty", "whitespace-after-universal-newlines"],
)
def test_portfolio_loader_validates_decoded_empty_text(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    raw_bytes: bytes,
) -> None:
    path = tmp_path / "portfolio_snapshot.txt"
    path.write_bytes(raw_bytes)
    monkeypatch.setattr(step1_research, "current_inputs_dir", lambda: tmp_path)

    with pytest.raises(
        ValueError,
        match=re.escape(f"Required portfolio snapshot input is empty: {path}"),
    ):
        step1_research.load_portfolio_snapshot_text()


PORTFOLIO_P1_TEXT = "portfolio_snapshot_marker: PORTFOLIO_P1_UNIQUE_MARKER\n"
PORTFOLIO_P2_TEXT = "portfolio_snapshot_marker: PORTFOLIO_P2_MUST_NOT_APPEAR\n"


class _RecordedStep1Sources:
    """Acquisition history for one Step 1 render."""

    def __init__(self, *, template_path: Path, strategy_path: Path, portfolio_path: Path) -> None:
        self.template_path = template_path
        self.strategy_path = strategy_path
        self.portfolio_path = portfolio_path
        self.read_events: list[Path] = []
        self.strategy_byte_reads = 0
        self.portfolio_byte_reads = 0


def _install_recorded_step1_sources(
    monkeypatch: pytest.MonkeyPatch,
) -> _RecordedStep1Sources:
    """Serve B1/P1 to the first acquisition and B2/P2 to any second, recording order.

    Acquiring either source through ``read_text`` fails outright, so the
    recorded byte reads are the complete acquisition history. Serving distinct
    second-acquisition buffers is what makes a hidden reread observable rather
    than merely uncounted.
    """
    real_read_text = step1_research.read_text
    real_read_bytes = Path.read_bytes
    recorder = _RecordedStep1Sources(
        template_path=step1_research.resolve_step1_prompt_template_path(),
        strategy_path=step1_research.current_inputs_dir() / "strategy_settings.yaml",
        portfolio_path=step1_research.current_inputs_dir() / "portfolio_snapshot.txt",
    )

    def fake_read_text(path: str | Path) -> str:
        normalized = Path(path)
        if normalized == recorder.strategy_path:
            pytest.fail("strategy settings must be acquired through Path.read_bytes")
        if normalized == recorder.portfolio_path:
            pytest.fail("portfolio snapshot must be acquired through Path.read_bytes")

        returned = real_read_text(path)
        recorder.read_events.append(normalized)
        return returned

    def fake_read_bytes(path: Path) -> bytes:
        normalized = Path(path)
        if normalized == recorder.strategy_path:
            recorder.strategy_byte_reads += 1
            returned = (
                S1_SETTINGS_TEXT
                if recorder.strategy_byte_reads == 1
                else S2_SETTINGS_TEXT
            ).encode("utf-8")
        elif normalized == recorder.portfolio_path:
            recorder.portfolio_byte_reads += 1
            returned = (
                PORTFOLIO_P1_TEXT
                if recorder.portfolio_byte_reads == 1
                else PORTFOLIO_P2_TEXT
            ).encode("utf-8")
        else:
            returned = real_read_bytes(path)

        recorder.read_events.append(normalized)
        return returned

    monkeypatch.setattr(step1_research, "read_text", fake_read_text)
    monkeypatch.setattr(Path, "read_bytes", fake_read_bytes)
    return recorder


def _assert_single_acquisition_in_order(recorder: _RecordedStep1Sources) -> None:
    assert recorder.strategy_byte_reads == 1
    assert recorder.portfolio_byte_reads == 1
    assert recorder.read_events == [
        recorder.template_path,
        recorder.strategy_path,
        recorder.portfolio_path,
    ]


def _assert_prompt_carries_only_the_first_acquisition(rendered: str) -> None:
    assert "STRATEGY_S1_UNIQUE_MARKER_9f3a1c" in rendered
    expected_approved_json = json.dumps(
        ["TICKER_S1_A", "TICKER_S1_B"],
        ensure_ascii=False,
        indent=2,
    )
    assert expected_approved_json in rendered
    assert "STRATEGY_S2_UNIQUE_MARKER_4b8de0" not in rendered
    assert "TICKER_S2_A" not in rendered
    assert PORTFOLIO_P1_TEXT in rendered
    assert PORTFOLIO_P2_TEXT not in rendered


def test_render_uses_one_strategy_snapshot_for_settings_and_approved_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = _install_recorded_step1_sources(monkeypatch)

    rendered = step1_research.build_step1_prompt_text()

    _assert_single_acquisition_in_order(recorder)
    _assert_prompt_carries_only_the_first_acquisition(rendered)


def test_render_commits_digests_of_the_exact_first_acquisition_buffers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The persisted digests must originate from the exact rendered B1/P1.

    A producer that reopened either source to hash it would consume the B2/P2
    buffers this fake serves to a second acquisition, so both the read counts
    and the committed digests would diverge. Expected digests are computed here
    directly from the same literal buffers the fake serves — never by rereading
    a fixture back through a production helper.
    """
    monkeypatch.setattr(step1_research, "repo_root", lambda: tmp_path)
    recorder = _install_recorded_step1_sources(monkeypatch)

    result = step1_research.render_step1_prompt()

    _assert_single_acquisition_in_order(recorder)
    _assert_prompt_carries_only_the_first_acquisition(
        Path(result["prompt_path"]).read_text(encoding="utf-8")
    )

    commitment = json.loads(
        (
            tmp_path
            / "artifacts"
            / "current"
            / "step1_research"
            / "render_source_commitment.json"
        ).read_text(encoding="utf-8")
    )
    assert commitment["strategy_settings_sha256"] == hashlib.sha256(
        S1_SETTINGS_TEXT.encode("utf-8")
    ).hexdigest()
    assert commitment["portfolio_snapshot_sha256"] == hashlib.sha256(
        PORTFOLIO_P1_TEXT.encode("utf-8")
    ).hexdigest()
    assert commitment["strategy_settings_sha256"] != hashlib.sha256(
        S2_SETTINGS_TEXT.encode("utf-8")
    ).hexdigest()
    assert commitment["portfolio_snapshot_sha256"] != hashlib.sha256(
        PORTFOLIO_P2_TEXT.encode("utf-8")
    ).hexdigest()


# ---------------------------------------------------------------------------
# Purity guard (follow-up): the compiler module must not directly acquire
# runtime capabilities or authority-bearing dependencies. This inspects only
# the compiler module's own source and AST; it does not walk the import
# closures of render_prompt or parse_strategy_settings_text, which are pure
# dependencies the compiler is authorized to use. Purity here is a direct
# module contract, not a transitive-import contract.
# ---------------------------------------------------------------------------

_COMPILER_RELATIVE_PATH = "src/investment_orchestrator/llm/legacy_step1_prompt_compiler.py"

_PROHIBITED_DIRECT_IMPORT_PREFIXES = (
    # Filesystem and process
    "os",
    "pathlib",
    "shutil",
    "tempfile",
    "io",
    "subprocess",
    "multiprocessing",
    # Concurrency and scheduling
    "threading",
    "asyncio",
    "sched",
    "concurrent",
    # Network and provider access
    "socket",
    "urllib",
    "requests",
    "httpx",
    "openai",
    "anthropic",
    "langchain",
    "cohere",
    "google.generativeai",
    # Nondeterministic authority inputs
    "time",
    "datetime",
    "random",
    "secrets",
    # Repository capability and authority owners
    f"{package.__name__}.common.io",
    f"{package.__name__}.mmi",
    f"{package.__name__}.offline",
    f"{package.__name__}.workflow",
    f"{package.__name__}.cli",
    f"{package.__name__}.state",
    f"{package.__name__}.observability",
    f"{package.__name__}.orders",
    f"{package.__name__}.broker",
    f"{package.__name__}.permissions",
)

_PROHIBITED_DIRECT_CALL_NAMES = frozenset({"open", "eval", "exec", "__import__"})


def _direct_imported_module_names(tree: ast.AST) -> set[str]:
    """Every module path this source directly imports (not transitively)."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module)
            names.update(f"{node.module}.{alias.name}" for alias in node.names)
    return names


def _prohibited_import_hits(imported: set[str]) -> list[str]:
    return sorted(
        name
        for name in imported
        for prefix in _PROHIBITED_DIRECT_IMPORT_PREFIXES
        if name == prefix or name.startswith(f"{prefix}.")
    )


def _direct_capability_call_hits(tree: ast.AST) -> list[str]:
    """Direct calls to prohibited dynamic-capability functions, by exact node shape."""
    hits: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name) and func.id in _PROHIBITED_DIRECT_CALL_NAMES:
            hits.append(func.id)
        elif (
            isinstance(func, ast.Attribute)
            and func.attr == "import_module"
            and isinstance(func.value, ast.Name)
            and func.value.id == "importlib"
        ):
            hits.append("importlib.import_module")
    return sorted(hits)


def test_compiler_module_acquires_no_direct_runtime_capability() -> None:
    path = repo_root() / _COMPILER_RELATIVE_PATH
    tree = ast.parse(path.read_text(encoding="utf-8"))

    import_hits = _prohibited_import_hits(_direct_imported_module_names(tree))
    assert not import_hits, (
        "legacy_step1_prompt_compiler.py directly imports prohibited "
        f"capability module(s): {import_hits}"
    )

    call_hits = _direct_capability_call_hits(tree)
    assert not call_hits, (
        "legacy_step1_prompt_compiler.py directly calls prohibited "
        f"dynamic-capability function(s): {call_hits}"
    )


def test_direct_import_detector_catches_aliases_and_prefixes() -> None:
    """Synthetic proof the detector above is not vacuously passing.

    Covers ``import x``, ``import x as y``, dotted-prefix rejection (without
    rejecting an unrelated name that merely shares a substring), and
    ``from x import y`` where ``y`` names a prohibited submodule.
    """
    synthetic = ast.parse(
        "import os as o\n"
        "import urllib.request\n"
        "import asynciomodule\n"  # must NOT be flagged: not the "asyncio" prefix
        "from investment_orchestrator.mmi import canonical\n"
        "from investment_orchestrator.common import io\n"
        "from investment_orchestrator.validators.strategy_settings import (\n"
        "    parse_strategy_settings_text,\n"
        ")\n"
    )
    hits = _prohibited_import_hits(_direct_imported_module_names(synthetic))
    assert hits == [
        "investment_orchestrator.common.io",
        "investment_orchestrator.mmi",
        "investment_orchestrator.mmi.canonical",
        "os",
        "urllib.request",
    ]


def test_direct_call_detector_catches_dynamic_capability_acquisition() -> None:
    """Synthetic proof the call-detector distinguishes direct builtins from lookalikes."""
    synthetic = ast.parse(
        "open('x')\n"
        "eval('1')\n"
        "exec('pass')\n"
        "__import__('os')\n"
        "importlib.import_module('os')\n"
        "some_object.import_module('os')\n"  # unrelated method: must NOT be flagged
        "text.rstrip()\n"  # unrelated method: must NOT be flagged
    )
    hits = _direct_capability_call_hits(synthetic)
    assert hits == ["__import__", "eval", "exec", "importlib.import_module", "open"]
