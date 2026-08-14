"""Private test-owned hermetic MMI source checkout.

This module is deliberately not collected by pytest and is never imported by
production code.  It exists so that every MMI test module that needs a valid
``policy`` or ``portfolio`` source can obtain one from bytes constructed here
and installed under a temporary checkout that preserves the production
relative layout::

    inputs/current/strategy_settings.yaml
    inputs/current/portfolio_snapshot.txt

Nothing in this module reads the operational ``inputs/current`` files of the
working tree, and no operational byte or hash is frozen into it.  The source
dates are fixed test inputs chosen to sit before each consuming module's
frozen evaluation timestamp, so an operational input refresh cannot change any
test outcome.

``live_operational_input_access_forbidden`` installs the source-access oracle
that turns any residual dependency on the real ``inputs/current`` files into a
test failure instead of a silent read.
"""

from __future__ import annotations

import builtins
from contextlib import contextmanager
from collections.abc import Iterator
from dataclasses import dataclass
from functools import cache
import hashlib
import os
from pathlib import Path

import pytest
import yaml

from investment_orchestrator.common.paths import repo_root
from investment_orchestrator.mmi import source_capture
from investment_orchestrator.mmi.contracts import (
    MmiCapturedSource,
    MmiSourceRole,
)


STRATEGY_SETTINGS_LOCATOR = "inputs/current/strategy_settings.yaml"
PORTFOLIO_SNAPSHOT_LOCATOR = "inputs/current/portfolio_snapshot.txt"
LONG_HORIZON_RESEARCH_LOCATOR = "inputs/current/long_horizon_research.json"
LOCATOR_BY_ROLE = {
    MmiSourceRole.STRATEGY_SETTINGS: STRATEGY_SETTINGS_LOCATOR,
    MmiSourceRole.PORTFOLIO_SNAPSHOT: PORTFOLIO_SNAPSHOT_LOCATOR,
    MmiSourceRole.LONG_HORIZON_RESEARCH: LONG_HORIZON_RESEARCH_LOCATOR,
}

PORTFOLIO_SECTION_START = (
    "(2a) existing_buy_open_orders_summary"
    "（optional, ticker-level summary; buy-side existing open orders SSOT）"
)
PORTFOLIO_SECTION_END = (
    "(2b) sell_open_orders"
    "（optional, lot-aware open sell orders summary）"
)
OPEN_BUY_HEADER = (
    "TICKER | budget | compiled_open_order_notional(optional) | "
    "residual_cash_not_allocated(optional) | template_id | "
    "anchor_baseline_last_close | anchor_price_asof | "
    "last_refresh_date_et(optional) | highest_live_limit(optional) | "
    "lowest_live_limit(optional) | live_step_count(optional) | "
    "live_order_steps_summary(optional) | "
    "live_order_qtys_summary(optional)"
)

DEFAULT_AS_OF = "2026-07-26"
DEFAULT_RUN_TIMESTAMP_ET = "2026-07-26 10:00 ET"
DEFAULT_PORTFOLIO_UPDATED = "2026-07-26"
DEFAULT_ANCHOR_PRICE_ASOF = "2026-07-20"
DEFAULT_PORTFOLIO_ROWS = (("QQQ", "100.00"), ("ARKK", "200.00"))


# --------------------------------------------------------------------------
# Test-owned synthetic source bytes.
# --------------------------------------------------------------------------
def strategy_settings_mapping(
    *,
    as_of: str = DEFAULT_AS_OF,
    run_timestamp_et: str = DEFAULT_RUN_TIMESTAMP_ET,
) -> dict[str, object]:
    return {
        "as_of": as_of,
        "run_timestamp_et": run_timestamp_et,
        "benchmark": "QQQ",
        "hard_cap_open_orders_budget": 38211.29,
        "target_new_buy_budget_this_run": 12000.00,
        "relative_rotation_enabled": True,
        "relative_rotation_guardrails": {
            "require_same_role_for_rotation": True,
            "min_score_gap_to_rotate": 2,
            "do_not_rotate_if_current_holding_still_role_valid": True,
            "no_rotation_on_one_rank_change_only": True,
        },
        "core_universe": ["QQQ", "VOO", "VTI", "VT"],
        "satellite_universe": ["SMH", "IGV"],
        "user_approved_extended_etf_static_list": ["QUAL", "CIBR"],
        "user_approved_extended_etf_theme_map": {
            "QUAL": {"theme_bucket": "quality_factor"},
            "CIBR": {"theme_bucket": "cybersecurity"},
        },
        "active_shortlist_size_rule": {
            "benchmark_carrier": 1,
            "diversified_core_buffer_max": 1,
            "sector_alpha_tilt_max": 1,
            "extended_etf_minority_sleeve_max": 2,
        },
        "max_new_tickers_per_week": {
            "base_universe_new_tickers_per_week": 0,
            "extended_etf_sleeve_new_tickers_per_week": 2,
        },
        "extended_etf_constraints": {
            "sleeve_budget_cap_pct_of_total_open_orders": 0.35,
            "single_extended_etf_budget_cap_pct_of_total_open_orders": (
                0.20
            ),
            "activation_minimum_effective_budget_pct_of_total_open_orders": (
                0.04
            ),
            "max_same_theme_extended_etf_count": 1,
            "max_same_theme_budget_pct_of_total_open_orders": 0.25,
            "require_distinct_theme_buckets_when_multiple_extended_etfs": (
                True
            ),
        },
    }


def strategy_settings_bytes(
    *,
    as_of: str = DEFAULT_AS_OF,
    run_timestamp_et: str = DEFAULT_RUN_TIMESTAMP_ET,
) -> bytes:
    return yaml.safe_dump(
        strategy_settings_mapping(
            as_of=as_of,
            run_timestamp_et=run_timestamp_et,
        ),
        sort_keys=False,
        allow_unicode=True,
    ).encode("utf-8")


def portfolio_row(ticker: str, budget: str) -> str:
    return " | ".join(
        (
            ticker,
            budget,
            "",
            "",
            "T4-E",
            "700.00",
            DEFAULT_ANCHOR_PRICE_ASOF,
            "",
            "",
            "",
            "",
            "",
            "",
        )
    )


def portfolio_snapshot_bytes(
    *,
    updated: str = DEFAULT_PORTFOLIO_UPDATED,
    rows: tuple[tuple[str, str], ...] = DEFAULT_PORTFOLIO_ROWS,
) -> bytes:
    return (
        "\n".join(
            (
                "【Portfolio Snapshot】",
                f"# updated {updated}",
                "(1) current_holdings_base",
                "PRIVATE_BROKER | QQQ | 9 | 123.45",
                PORTFOLIO_SECTION_START,
                "- exact code-owned explanatory line",
                OPEN_BUY_HEADER,
                *(portfolio_row(*row) for row in rows),
                "",
                PORTFOLIO_SECTION_END,
                "PRIVATE_ACCOUNT | QQQ | raw sell instruction",
                "(3) LTCG_ELIGIBLE_SELLABLE",
                "QQQ | 9 | 2020-01-01 | private tax lot",
            )
        )
        + "\n"
    ).encode("utf-8")


# --------------------------------------------------------------------------
# Temporary checkout installation and real-owner capture.
# --------------------------------------------------------------------------
def install_source(root: Path, *, role: MmiSourceRole, raw: bytes) -> Path:
    path = root / LOCATOR_BY_ROLE[role]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return path


def capture_source(
    root: Path,
    *,
    role: MmiSourceRole,
    raw: bytes,
) -> MmiCapturedSource:
    """Install ``raw`` under ``root`` and capture it with the real owner."""
    install_source(root, role=role, raw=raw)
    result = source_capture._capture_mmi_source_at_root(
        root,
        role=role,
        expected_source_sha256=hashlib.sha256(raw).hexdigest(),
    )
    assert result.valid, result.reason_codes
    assert result.source is not None
    assert_test_owned_source(result.source, role=role, raw=raw)
    return result.source


@dataclass(frozen=True, slots=True)
class HermeticSourceCheckout:
    """One temporary checkout holding both approved source locators."""

    root: Path
    strategy_settings_raw: bytes
    portfolio_snapshot_raw: bytes
    policy_source: MmiCapturedSource
    portfolio_source: MmiCapturedSource


def build_checkout(
    tmp_path_factory: pytest.TempPathFactory,
    name: str,
    *,
    as_of: str = DEFAULT_AS_OF,
    run_timestamp_et: str = DEFAULT_RUN_TIMESTAMP_ET,
    updated: str = DEFAULT_PORTFOLIO_UPDATED,
    rows: tuple[tuple[str, str], ...] = DEFAULT_PORTFOLIO_ROWS,
) -> HermeticSourceCheckout:
    root = tmp_path_factory.mktemp(name)
    settings_raw = strategy_settings_bytes(
        as_of=as_of,
        run_timestamp_et=run_timestamp_et,
    )
    portfolio_raw = portfolio_snapshot_bytes(updated=updated, rows=rows)
    policy_source = capture_source(
        root,
        role=MmiSourceRole.STRATEGY_SETTINGS,
        raw=settings_raw,
    )
    portfolio_source = capture_source(
        root,
        role=MmiSourceRole.PORTFOLIO_SNAPSHOT,
        raw=portfolio_raw,
    )
    assert_checkout_resolves_both_locators(root)
    return HermeticSourceCheckout(
        root=root,
        strategy_settings_raw=settings_raw,
        portfolio_snapshot_raw=portfolio_raw,
        policy_source=policy_source,
        portfolio_source=portfolio_source,
    )


# --------------------------------------------------------------------------
# Hermeticity oracle.
# --------------------------------------------------------------------------
def assert_test_owned_source(
    source: MmiCapturedSource,
    *,
    role: MmiSourceRole,
    raw: bytes,
) -> None:
    """Bind a captured source to the exact test-owned bytes and locator."""
    digest = hashlib.sha256(raw).hexdigest()
    record = source.source_record
    assert source.role is role
    assert source.raw_bytes == raw
    assert record["repository_relative_locator"] == LOCATOR_BY_ROLE[role]
    assert record["expected_sha256"] == digest
    assert record["observed_sha256"] == digest
    assert record["observed_size_bytes"] == len(raw)


def assert_checkout_resolves_both_locators(root: Path) -> None:
    """Both approved locators exist as regular files inside ``root`` only."""
    assert root.is_absolute()
    assert not _is_live_repository_root(root)
    for locator in (STRATEGY_SETTINGS_LOCATOR, PORTFOLIO_SNAPSHOT_LOCATOR):
        installed = root / locator
        assert installed.is_file()
        assert not installed.is_symlink()
        assert _is_live_operational_path(installed) is False


@cache
def _live_repository_root() -> str:
    return os.path.normpath(str(repo_root().resolve()))


@cache
def _live_operational_directory() -> str:
    return os.path.join(_live_repository_root(), "inputs", "current")


def _normalized(candidate: object) -> str | None:
    try:
        raw = os.fspath(candidate)  # type: ignore[arg-type]
    except TypeError:
        return None
    if type(raw) is bytes:
        try:
            raw = os.fsdecode(raw)
        except ValueError:
            return None
    if type(raw) is not str:
        return None
    if not os.path.isabs(raw):
        raw = os.path.join(os.getcwd(), raw)
    return os.path.normpath(raw)


def _is_live_repository_root(candidate: object) -> bool:
    normalized = _normalized(candidate)
    return normalized is not None and normalized == _live_repository_root()


def _is_live_operational_path(candidate: object) -> bool:
    normalized = _normalized(candidate)
    if normalized is None:
        return False
    live = _live_operational_directory()
    return normalized == live or normalized.startswith(live + os.sep)


class LiveOperationalInputAccess(AssertionError):
    """Raised when a hermetic module touches the real ``inputs/current``."""


def _forbidden(detail: str) -> LiveOperationalInputAccess:
    return LiveOperationalInputAccess(
        "hermetic MMI test module attempted live operational input access: "
        f"{detail}"
    )


@contextmanager
def live_operational_input_access_forbidden() -> Iterator[None]:
    """Fail on any read of the real ``inputs/current`` operational bytes.

    The source owner is left fully in charge of temporary checkouts: only the
    production entry points that resolve the real repository checkout, and any
    direct read of a real ``inputs/current`` path, are turned into failures.
    """
    patch = pytest.MonkeyPatch()
    real_capture_at_root = source_capture._capture_mmi_source_at_root
    real_absence_at_root = source_capture._capture_mmi_source_absence_at_root
    real_read_bytes = Path.read_bytes
    real_read_text = Path.read_text
    real_path_open = Path.open
    real_open = builtins.open

    def guarded_capture_at_root(repository_root, **kwargs):
        if _is_live_repository_root(repository_root):
            raise _forbidden(f"capture at repository root {repository_root!r}")
        return real_capture_at_root(repository_root, **kwargs)

    def guarded_absence_at_root(repository_root, **kwargs):
        if _is_live_repository_root(repository_root):
            raise _forbidden(
                f"absence proof at repository root {repository_root!r}"
            )
        return real_absence_at_root(repository_root, **kwargs)

    def blocked_current_capture(*_args: object, **_kwargs: object):
        raise _forbidden("capture_current_mmi_source")

    def blocked_current_absence(*_args: object, **_kwargs: object):
        raise _forbidden("capture_current_mmi_source_absence")

    def guarded_read_bytes(self, *args, **kwargs):
        if _is_live_operational_path(self):
            raise _forbidden(f"read_bytes {self!s}")
        return real_read_bytes(self, *args, **kwargs)

    def guarded_read_text(self, *args, **kwargs):
        if _is_live_operational_path(self):
            raise _forbidden(f"read_text {self!s}")
        return real_read_text(self, *args, **kwargs)

    def guarded_path_open(self, *args, **kwargs):
        if _is_live_operational_path(self):
            raise _forbidden(f"open {self!s}")
        return real_path_open(self, *args, **kwargs)

    def guarded_open(file, *args, **kwargs):
        if _is_live_operational_path(file):
            raise _forbidden(f"open {file!r}")
        return real_open(file, *args, **kwargs)

    patch.setattr(
        source_capture,
        "_capture_mmi_source_at_root",
        guarded_capture_at_root,
    )
    patch.setattr(
        source_capture,
        "capture_current_mmi_source",
        blocked_current_capture,
    )
    patch.setattr(
        source_capture,
        "_capture_current_mmi_source_from_module_path",
        blocked_current_capture,
    )
    patch.setattr(
        source_capture,
        "_capture_mmi_source_absence_at_root",
        guarded_absence_at_root,
    )
    patch.setattr(
        source_capture,
        "capture_current_mmi_source_absence",
        blocked_current_absence,
    )
    patch.setattr(
        source_capture,
        "_capture_current_mmi_source_absence_from_module_path",
        blocked_current_absence,
    )
    patch.setattr(Path, "read_bytes", guarded_read_bytes)
    patch.setattr(Path, "read_text", guarded_read_text)
    patch.setattr(Path, "open", guarded_path_open)
    patch.setattr(builtins, "open", guarded_open)
    try:
        yield
    finally:
        patch.undo()


def assert_live_operational_inputs_are_unreachable() -> None:
    """The oracle must fail a real read rather than let it succeed."""
    live_root = Path(_live_repository_root())
    for locator in (STRATEGY_SETTINGS_LOCATOR, PORTFOLIO_SNAPSHOT_LOCATOR):
        path = live_root / locator
        assert _is_live_operational_path(path)
        with pytest.raises(LiveOperationalInputAccess):
            path.read_bytes()
        with pytest.raises(LiveOperationalInputAccess):
            open(path, "rb")
    with pytest.raises(LiveOperationalInputAccess):
        source_capture.capture_current_mmi_source(
            MmiSourceRole.STRATEGY_SETTINGS,
            expected_source_sha256="0" * 64,
        )
    with pytest.raises(LiveOperationalInputAccess):
        source_capture._capture_mmi_source_at_root(
            repo_root(),
            role=MmiSourceRole.STRATEGY_SETTINGS,
            expected_source_sha256="0" * 64,
        )
    # P2b introduced the first production consumer of the absence prover, so
    # its three entry points are blocked on exactly the same terms.
    with pytest.raises(LiveOperationalInputAccess):
        source_capture.capture_current_mmi_source_absence(
            MmiSourceRole.PORTFOLIO_SNAPSHOT,
        )
    with pytest.raises(LiveOperationalInputAccess):
        source_capture._capture_current_mmi_source_absence_from_module_path(
            source_capture._PRODUCTION_MODULE_FILE,
            MmiSourceRole.PORTFOLIO_SNAPSHOT,
        )
    with pytest.raises(LiveOperationalInputAccess):
        source_capture._capture_mmi_source_absence_at_root(
            repo_root(),
            role=MmiSourceRole.PORTFOLIO_SNAPSHOT,
        )
