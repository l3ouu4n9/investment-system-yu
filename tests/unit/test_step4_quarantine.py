from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from investment_orchestrator.common.io import read_text, write_text
from investment_orchestrator.parsers import extract_orders_and_summary as extract_mod
from investment_orchestrator.parsers.extract_orders_and_summary import (
    QUARANTINE_DIRNAME,
    extract_orders_and_summary,
)


def settings() -> dict[str, Any]:
    return {
        "core_universe": ["QQQ", "VOO", "VTI", "VT"],
        "satellite_universe": ["SMH", "IGV"],
        "user_approved_extended_etf_static_list": ["GRID", "CIBR"],
    }


def raw_output(buy_body: str) -> str:
    return (
        "TEMPLATE4_ORDERS_START\n"
        "TEMPLATE4_ORDERS\nSELL_ORDERS\nNONE\nBUY_ORDERS\n" + buy_body + "\n"
        "TEMPLATE4_ORDERS_END\n"
        "ORDER_STATE_EXPORT_START\nORDER_STATE_EXPORT\nNONE\nORDER_STATE_EXPORT_END\n"
        "TEMPLATE5_EXEC_SUMMARY_START\nTEMPLATE5_EXEC_SUMMARY\nno diagnostics\nTEMPLATE5_EXEC_SUMMARY_END\n"
    )


def step4_paths(tmp_path: Path) -> dict[str, Path]:
    base = tmp_path / "artifacts" / "current" / "step4_order_compiler"
    return {
        "raw_output_path": tmp_path / "raw_output.txt",
        "template4_orders_path": base / "template4_orders.txt",
        "order_state_export_path": base / "order_state_export.txt",
        "exec_summary_path": base / "exec_summary.txt",
    }


def quarantine_of(path: Path) -> Path:
    return path.parent / QUARANTINE_DIRNAME / path.name


VALID_BUY = "ticker=QQQ | step_name=L1 | shares=2 | limit_price=10.00 | order_intent=NEW_ORDER"
# ZZZZ is outside the strategy universe -> G1 universe allowlist fails.
INVALID_BUY = "ticker=ZZZZ | step_name=L1 | shares=2 | limit_price=10.00 | order_intent=NEW_ORDER"


def test_validation_success_publishes_canonical_and_cleans_quarantine(tmp_path: Path) -> None:
    paths = step4_paths(tmp_path)
    write_text(paths["raw_output_path"], raw_output(VALID_BUY))

    extract_orders_and_summary(
        raw_output_path=paths["raw_output_path"],
        template4_orders_path=paths["template4_orders_path"],
        order_state_export_path=paths["order_state_export_path"],
        exec_summary_path=paths["exec_summary_path"],
        strategy_settings=settings(),
    )

    # Canonical artifacts published.
    assert paths["template4_orders_path"].is_file()
    assert paths["order_state_export_path"].is_file()
    assert paths["exec_summary_path"].is_file()
    assert "ticker=QQQ" in read_text(paths["template4_orders_path"])

    # Quarantine files removed and the (now empty) quarantine dir cleaned up.
    assert not quarantine_of(paths["template4_orders_path"]).exists()
    assert not quarantine_of(paths["order_state_export_path"]).exists()
    assert not quarantine_of(paths["exec_summary_path"]).exists()
    assert not (paths["template4_orders_path"].parent / QUARANTINE_DIRNAME).exists()


def test_validation_failure_raises_and_leaves_rejected_quarantine(tmp_path: Path) -> None:
    paths = step4_paths(tmp_path)
    write_text(paths["raw_output_path"], raw_output(INVALID_BUY))

    with pytest.raises(ValueError, match="outside the allowed buy universe"):
        extract_orders_and_summary(
            raw_output_path=paths["raw_output_path"],
            template4_orders_path=paths["template4_orders_path"],
            order_state_export_path=paths["order_state_export_path"],
            exec_summary_path=paths["exec_summary_path"],
            strategy_settings=settings(),
        )

    # Canonical artifacts were never written.
    assert not paths["template4_orders_path"].exists()
    assert not paths["order_state_export_path"].exists()
    assert not paths["exec_summary_path"].exists()

    # Rejected candidates remain under quarantine as diagnostics.
    q_template4 = quarantine_of(paths["template4_orders_path"])
    assert q_template4.is_file()
    assert "ticker=ZZZZ" in read_text(q_template4)
    assert quarantine_of(paths["order_state_export_path"]).is_file()
    assert quarantine_of(paths["exec_summary_path"]).is_file()


def test_validation_failure_preserves_pre_existing_canonical_byte_for_byte(tmp_path: Path) -> None:
    paths = step4_paths(tmp_path)

    # A prior-good canonical set exists from an earlier successful run.
    prior_template4 = "TEMPLATE4_ORDERS\nBUY_ORDERS\nticker=QQQ | step_name=L1 | shares=1 | limit_price=9.99 | order_intent=NEW_ORDER\n"
    prior_state = "ORDER_STATE_EXPORT\nPRIOR_GOOD\n"
    prior_summary = "TEMPLATE5_EXEC_SUMMARY\nPRIOR_GOOD\n"
    write_text(paths["template4_orders_path"], prior_template4)
    write_text(paths["order_state_export_path"], prior_state)
    write_text(paths["exec_summary_path"], prior_summary)

    write_text(paths["raw_output_path"], raw_output(INVALID_BUY))

    with pytest.raises(ValueError, match="outside the allowed buy universe"):
        extract_orders_and_summary(
            raw_output_path=paths["raw_output_path"],
            template4_orders_path=paths["template4_orders_path"],
            order_state_export_path=paths["order_state_export_path"],
            exec_summary_path=paths["exec_summary_path"],
            strategy_settings=settings(),
        )

    # Prior-good canonical artifacts are preserved exactly.
    assert read_text(paths["template4_orders_path"]) == prior_template4
    assert read_text(paths["order_state_export_path"]) == prior_state
    assert read_text(paths["exec_summary_path"]) == prior_summary


def test_mid_publish_canonical_write_failure_is_recoverable_via_quarantine(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """G2.1: canonical publish is sequential and NOT cross-file atomic.

    If the second canonical write fails mid-publish, this documents that the
    exception propagates and the full validated set survives in quarantine for
    recovery -- it does NOT claim all-or-nothing canonical atomicity.
    """
    paths = step4_paths(tmp_path)
    write_text(paths["raw_output_path"], raw_output(VALID_BUY))

    real_write_text = extract_mod.write_text
    canonical_writes: list[Path] = []

    def flaky_write_text(path: Any, text: str) -> Path:
        target = Path(path)
        # Quarantine writes (and validation reads) proceed normally.
        if QUARANTINE_DIRNAME in target.parts:
            return real_write_text(target, text)
        # Canonical publish: first write succeeds, second raises.
        canonical_writes.append(target)
        if len(canonical_writes) == 1:
            return real_write_text(target, text)
        raise OSError("simulated mid-publish write failure")

    monkeypatch.setattr(extract_mod, "write_text", flaky_write_text)

    with pytest.raises(OSError, match="simulated mid-publish write failure"):
        extract_orders_and_summary(
            raw_output_path=paths["raw_output_path"],
            template4_orders_path=paths["template4_orders_path"],
            order_state_export_path=paths["order_state_export_path"],
            exec_summary_path=paths["exec_summary_path"],
            strategy_settings=settings(),
        )

    # Partial canonical set: the first canonical write may have landed, the
    # second did not -> NOT all-or-nothing.
    assert paths["template4_orders_path"].is_file()  # first canonical write succeeded
    assert not paths["order_state_export_path"].exists()  # second canonical write failed
    assert not paths["exec_summary_path"].exists()

    # Recoverability: the full validated set still remains in quarantine
    # (cleanup runs only after all three canonical writes succeed).
    assert quarantine_of(paths["template4_orders_path"]).is_file()
    assert quarantine_of(paths["order_state_export_path"]).is_file()
    assert quarantine_of(paths["exec_summary_path"]).is_file()
    assert "ticker=QQQ" in read_text(quarantine_of(paths["template4_orders_path"]))
