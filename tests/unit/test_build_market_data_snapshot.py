from investment_system.market.build_market_data_snapshot import parse_run_timestamp_et


def test_parse_run_timestamp_et_attaches_new_york_timezone() -> None:
    parsed = parse_run_timestamp_et("2026-04-18 20:30 ET")

    assert parsed.tzinfo is not None
    assert parsed.strftime("%Y-%m-%d %H:%M ET") == "2026-04-18 20:30 ET"
