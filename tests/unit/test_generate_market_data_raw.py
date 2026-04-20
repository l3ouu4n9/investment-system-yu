import json
from pathlib import Path

from investment_system.market.generate_market_data_raw import infer_target_close_date, load_research_tickers, parse_run_timestamp_et


def test_infer_target_close_date_uses_previous_weekday_before_close() -> None:
    run_dt = parse_run_timestamp_et("2026-04-20 15:30 ET")

    assert infer_target_close_date(run_dt).isoformat() == "2026-04-17"


def test_load_research_tickers_prefers_seed_tickers_and_normalizes_case(tmp_path: Path) -> None:
    research_json = tmp_path / "research.json"
    research_json.write_text(
        json.dumps(
            {
                "seed_tickers": [" nvda ", "AVGO", "nvda"],
                "alpha_ranking_2_4y": [{"ticker": "ignored"}],
            }
        ),
        encoding="utf-8",
    )

    assert load_research_tickers(research_json) == ["AVGO", "NVDA"]
