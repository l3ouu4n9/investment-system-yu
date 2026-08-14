import hashlib
import inspect
from pathlib import Path

import pytest

from investment_orchestrator.common.paths import repo_root
from investment_orchestrator.mmi.contracts import MmiSourceRole
from investment_orchestrator.holdings import current_strict_positive_etf_positions as holdings


def _strict_section(rows: tuple[str, ...]) -> bytes:
    return "\n".join(
        (
            "[STRICT_POSITIVE_ETF_POSITIONS_V1]",
            "schema_version = strict_positive_etf_positions_v1",
            "portfolio_scope_id = yu_etf_portfolio",
            "operator_scope_complete = true",
            "TICKER | shares",
            *rows,
            "[/STRICT_POSITIVE_ETF_POSITIONS_V1]",
        )
    ).encode("utf-8")


def _portfolio(rows: tuple[str, ...] = ("QQQ | 2.5", "SMH | 3")) -> bytes:
    current = (repo_root() / "inputs/current/portfolio_snapshot.txt").read_bytes()
    base, marker, _existing = current.partition(
        b"\n[STRICT_POSITIVE_ETF_POSITIONS_V1]\n"
    )
    assert marker
    return base + b"\n" + _strict_section(rows) + b"\n"


def test_capture_current_validated_strict_holdings_domain_signature_has_no_bypass() -> None:
    parameters = inspect.signature(
        holdings.capture_current_validated_strict_holdings_domain
    ).parameters
    assert set(parameters) == {"portfolio_snapshot_expected_sha256"}
    assert not {"tickers", "holdings", "portfolio_path", "raw_bytes", "scope_id", "alternate_checkout"} & set(parameters)


def test_strict_holdings_domain_accessor_binds_existing_observed_sha(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    portfolio_bytes = _portfolio(("SMH | 3", "QQQ | 2.5"))
    expected_sha256 = hashlib.sha256(portfolio_bytes).hexdigest()

    def _capture(role: MmiSourceRole, *, expected_source_sha256: str):
        from investment_orchestrator.mmi.source_capture import _capture_mmi_source_at_root
        # Write to tmp_path to mock capture
        current = tmp_path / "inputs/current"
        current.mkdir(parents=True, exist_ok=True)
        (current / "portfolio_snapshot.txt").write_bytes(portfolio_bytes)
        (current / "strategy_settings.yaml").write_bytes(b"")
        return _capture_mmi_source_at_root(
            tmp_path,
            role=role,
            expected_source_sha256=expected_source_sha256,
        )

    monkeypatch.setattr(holdings, "capture_current_mmi_source", _capture)
    
    # Valid capture binds SHA and preserves source order
    domain = holdings.capture_current_validated_strict_holdings_domain(
        portfolio_snapshot_expected_sha256=expected_sha256,
    )
    assert domain.portfolio_source_sha256 == expected_sha256
    assert domain.tickers == ("SMH", "QQQ")


def test_strict_holdings_domain_accessor_fails_closed_on_invalid_or_duplicate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _run_with_rows(rows: tuple[str, ...]) -> tuple[str, ...]:
        portfolio_bytes = _portfolio(rows)
        expected_sha256 = hashlib.sha256(portfolio_bytes).hexdigest()

        def _capture(role: MmiSourceRole, *, expected_source_sha256: str):
            from investment_orchestrator.mmi.source_capture import _capture_mmi_source_at_root
            current = tmp_path / "inputs/current"
            current.mkdir(parents=True, exist_ok=True)
            (current / "portfolio_snapshot.txt").write_bytes(portfolio_bytes)
            (current / "strategy_settings.yaml").write_bytes(b"")
            return _capture_mmi_source_at_root(
                tmp_path,
                role=role,
                expected_source_sha256=expected_source_sha256,
            )

        monkeypatch.setattr(holdings, "capture_current_mmi_source", _capture)
        try:
            holdings.capture_current_validated_strict_holdings_domain(
                portfolio_snapshot_expected_sha256=expected_sha256,
            )
            assert False, "Should have failed"
        except holdings.StrictHoldingsDomainError as exc:
            return exc.reason_codes

    # Invalid shares (0 is not positive)
    assert _run_with_rows(("QQQ | 0",)) == ("REPORT_ONLY_EXPOSURE_STRICT_HOLDINGS_SHARES_INVALID",)
    
    # Duplicate ticker
    assert _run_with_rows(("QQQ | 1", "QQQ | 2")) == ("REPORT_ONLY_EXPOSURE_STRICT_HOLDINGS_TICKER_DUPLICATE",)
    
    # Invalid section format
    assert _run_with_rows(("holdings_as_of_date = 2026-08-12", "QQQ | 1")) == ("REPORT_ONLY_EXPOSURE_STRICT_HOLDINGS_ROW_INVALID",)
