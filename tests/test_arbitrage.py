import dataclasses

import pytest

from src import arbitrage, store
from src.models import PriceRecord


@pytest.fixture()
def conn(tmp_path):
    connection = store.connect(tmp_path / "history.sqlite")
    store.init_schema(connection)
    return connection


def record(
    landed_usd,
    market="kg",
    currency="kgs",
    origin="ALA",
    destination="HKT",
    depart="2026-10-06",
    return_date="2026-10-16",
    airline="KC",
    transfers=0,
    gate=None,
    search_url="https://x/search",
) -> PriceRecord:
    return PriceRecord(
        source="aviasales_cache",
        origin=origin,
        destination=destination,
        market=market,
        depart_date=depart,
        return_date=return_date,
        price_local=landed_usd,
        currency=currency,
        airline=airline,
        transfers=transfers,
        duration_min=1500,
        duration_to_min=760,
        search_url=search_url,
        gate=gate,
        fx_rate=1.0,
        landed_usd=landed_usd,
    )


def seed(conn, now, **kwargs):
    store.insert_prices(conn, [record(**kwargs)], now=now)


NOW = "2026-08-01T06:00:00Z"


# ---------------------------------------------------------------------------
# find()
# ---------------------------------------------------------------------------


def test_finds_when_saving_above_threshold(conn, config_stub):
    seed(conn, NOW, market="kg", landed_usd=268.0)
    seed(conn, NOW, market="ru", landed_usd=239.0)  # (268-239)/268 = 10.8% > 5%
    findings = arbitrage.find(conn, config_stub, now=NOW)
    assert len(findings) == 1
    f = findings[0]
    assert f.origin == "ALA" and f.destination == "HKT"
    assert f.expensive_market == "kg"
    assert f.expensive_usd == pytest.approx(268.0)
    assert f.cheap_market == "ru"
    assert f.cheap_usd == pytest.approx(239.0)
    assert f.saving_ratio == pytest.approx((268.0 - 239.0) / 268.0)


def test_no_finding_when_saving_below_threshold(conn, config_stub):
    seed(conn, NOW, market="kg", landed_usd=100.0)
    seed(conn, NOW, market="ru", landed_usd=97.0)  # 3% < 5%
    assert arbitrage.find(conn, config_stub, now=NOW) == []


def test_no_finding_when_saving_exactly_at_threshold(conn, config_stub):
    seed(conn, NOW, market="kg", landed_usd=100.0)
    seed(conn, NOW, market="ru", landed_usd=95.0)  # ratio exactly 0.05 == cross_market_delta
    assert arbitrage.find(conn, config_stub, now=NOW) == []


def test_different_airlines_are_not_treated_as_same_flight(conn, config_stub):
    seed(conn, NOW, market="kg", landed_usd=268.0, airline="KC")
    seed(conn, NOW, market="ru", landed_usd=200.0, airline="SU")
    assert arbitrage.find(conn, config_stub, now=NOW) == []


def test_different_transfer_counts_are_not_treated_as_same_flight(conn, config_stub):
    seed(conn, NOW, market="kg", landed_usd=268.0, transfers=0)
    seed(conn, NOW, market="ru", landed_usd=200.0, transfers=1)
    assert arbitrage.find(conn, config_stub, now=NOW) == []


def test_different_dates_are_not_treated_as_same_flight(conn, config_stub):
    seed(conn, NOW, market="kg", landed_usd=268.0, depart="2026-10-06")
    seed(conn, NOW, market="ru", landed_usd=200.0, depart="2026-10-07")
    assert arbitrage.find(conn, config_stub, now=NOW) == []


def test_stale_prices_are_ignored(conn, config_stub):
    stale_at = "2026-07-29T06:00:00Z"  # > 48h cache_ttl_hours before NOW
    seed(conn, stale_at, market="kg", landed_usd=268.0)
    seed(conn, NOW, market="ru", landed_usd=200.0)
    assert arbitrage.find(conn, config_stub, now=NOW) == []


def test_sorted_by_saving_ratio_descending(conn, config_stub):
    seed(conn, NOW, market="kg", landed_usd=268.0, depart="2026-10-06")
    seed(conn, NOW, market="ru", landed_usd=239.0, depart="2026-10-06")  # ~10.8%
    seed(conn, NOW, market="kg", landed_usd=300.0, depart="2026-10-12")
    seed(conn, NOW, market="ru", landed_usd=150.0, depart="2026-10-12")  # 50%
    findings = arbitrage.find(conn, config_stub, now=NOW)
    assert len(findings) == 2
    assert findings[0].depart_date == "2026-10-12"
    assert findings[0].saving_ratio > findings[1].saving_ratio


def test_single_market_config_does_not_crash(conn, config_stub):
    single_market_cfg = dataclasses.replace(
        config_stub, markets=["kg"], market_currency={"kg": "kgs"}
    )
    seed(conn, NOW, market="kg", landed_usd=268.0)
    assert arbitrage.find(conn, single_market_cfg, now=NOW) == []


def test_carries_cheap_url_and_gate(conn, config_stub):
    seed(conn, NOW, market="kg", landed_usd=268.0)
    seed(
        conn,
        NOW,
        market="ru",
        landed_usd=239.0,
        search_url="https://ru.example/search",
        gate="City.Travel",
    )
    findings = arbitrage.find(conn, config_stub, now=NOW)
    assert findings[0].cheap_url == "https://ru.example/search"
    assert findings[0].cheap_gate == "City.Travel"


# ---------------------------------------------------------------------------
# render_line()
# ---------------------------------------------------------------------------


@pytest.fixture()
def finding():
    return arbitrage.Finding(
        origin="ALA",
        destination="HKT",
        depart_date="2026-10-06",
        return_date="2026-10-16",
        airline="D7",
        transfers=0,
        expensive_market="kg",
        expensive_usd=268.0,
        cheap_market="ru",
        cheap_usd=239.0,
        cheap_url="https://ru.example/search",
        cheap_gate="City.Travel",
    )


def test_render_line_contains_both_markets_and_prices(finding):
    line = arbitrage.render_line(finding)
    assert "Алматы" in line and "Пхукет" in line
    assert "06.10" in line
    assert "AirAsia X" in line  # D7
    assert "$268" in line and "(kg)" in line
    assert "$239" in line and "(ru)" in line
    assert "landed" in line


def test_render_line_shows_saving_percent_and_same_flight_caveat(finding):
    line = arbitrage.render_line(finding)
    percent = round((268.0 - 239.0) / 268.0 * 100)
    assert f"{percent}%" in line
    assert "тот же рейс" in line


def test_render_line_includes_link_when_present(finding):
    line = arbitrage.render_line(finding)
    assert "https://ru.example/search" in line


def test_render_line_omits_link_when_absent(finding):
    no_link = dataclasses.replace(finding, cheap_url=None)
    line = arbitrage.render_line(no_link)
    assert "<a href" not in line


def test_render_line_escapes_html(finding):
    dangerous = dataclasses.replace(
        finding, airline="A&B", cheap_url="https://example.com/?a=1&b=2"
    )
    line = arbitrage.render_line(dangerous)
    assert "A&amp;B" in line
    assert "A&B" not in line
    assert "a=1&amp;b=2" in line
