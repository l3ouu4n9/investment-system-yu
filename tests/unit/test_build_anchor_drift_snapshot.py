from investment_orchestrator.market.build_anchor_drift_snapshot import (
    build_snapshot,
    compute_thresholds,
)


def test_compute_thresholds_uses_defaults_when_threshold_values_are_missing() -> None:
    thresholds = compute_thresholds(None, {"keep_floor_pct": None, "mini_atr_mult": None})

    assert thresholds == {
        "keep_threshold_pct": 2.0,
        "mini_threshold_pct": 5.0,
    }


def test_build_snapshot_ignores_non_dict_anchor_rows() -> None:
    market_snapshot = {
        "run_timestamp_et": "2026-04-18 10:28 ET",
        "tickers": [
            {
                "ticker": "NVDA",
                "last_close": 120.0,
                "price_asof": "2026-04-18",
                "atr_20_30d_pct": 4.0,
            }
        ],
    }
    anchor_input = {
        "tickers": [
            "not-a-dict",
            {
                "ticker": "NVDA",
                "side": "buy",
                "old_baseline": 100.0,
            },
        ]
    }

    snapshot = build_snapshot(market_snapshot, anchor_input)

    assert snapshot["run_timestamp_et"] == "2026-04-18 10:28 ET"
    assert [row["ticker"] for row in snapshot["tickers"]] == ["NVDA"]
