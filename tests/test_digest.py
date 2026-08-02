import pytest

from src import digest, rules, store
from src.models import PriceRecord


@pytest.fixture()
def conn(tmp_path):
    connection = store.connect(tmp_path / "history.sqlite")
    store.init_schema(connection)
    return connection


def record(landed, depart="2026-10-12", market="kg", airline="KC", destination="HKT"):
    return PriceRecord(
        source="aviasales_cache",
        origin="FRU",
        destination=destination,
        market=market,
        depart_date=depart,
        return_date="2026-10-24",
        price_local=landed * 87.5,
        currency="kgs",
        airline=airline,
        transfers=1,
        duration_min=1500,
        duration_to_min=760,
        search_url="https://www.aviasales.kg/search/x",
        fx_rate=1 / 87.5,
        landed_usd=landed,
    )


def seed(conn, landed_values, now="2026-08-01T06:00:00Z", **kwargs):
    store.insert_prices(conn, [record(v, **kwargs) for v in landed_values], now=now)


def test_summary_picks_cheapest_fresh_offer(conn, config_stub):
    seed(conn, [400.0, 312.0, 355.0])
    summary = digest.build_route_summary(
        conn, config_stub, "FRU", "HKT", now="2026-08-01T07:00:00Z"
    )
    assert summary.best_landed == 312.0
    assert summary.airline == "KC"
    assert summary.transfers == 1


def test_summary_ignores_stale_rows(conn, config_stub):
    seed(conn, [312.0], now="2026-07-20T06:00:00Z")  # старше 48 ч
    seed(conn, [400.0], now="2026-08-01T06:00:00Z")
    summary = digest.build_route_summary(
        conn, config_stub, "FRU", "HKT", now="2026-08-01T07:00:00Z"
    )
    assert summary.best_landed == 400.0


def test_summary_with_no_data_is_empty(conn, config_stub):
    summary = digest.build_route_summary(
        conn, config_stub, "ALA", "DPS", now="2026-08-01T07:00:00Z"
    )
    assert summary.best_landed is None
    assert summary.level == rules.GRAY


def test_baseline_is_built_from_daily_minimums_not_every_offer(conn, config_stub):
    """Одна точка на день наблюдения, а не все офферы подряд."""
    for day, price in enumerate([500.0, 510.0, 520.0, 530.0, 540.0], start=20):
        # по три оффера в день: в выборку должен попасть только минимум
        seed(conn, [price, price + 30, price + 60], now=f"2026-07-{day}T06:00:00Z")
    seed(conn, [400.0], now="2026-08-01T06:00:00Z")
    summary = digest.build_route_summary(
        conn, config_stub, "FRU", "HKT", now="2026-08-01T07:00:00Z"
    )
    assert summary.baseline is not None
    assert summary.baseline.n == 6  # шесть дней, а не восемнадцать офферов
    assert summary.baseline.median == pytest.approx(515.0)  # медиана дневных минимумов
    assert summary.delta_pct is not None and summary.delta_pct < 0


def test_baseline_is_none_until_enough_days_observed(conn, config_stub):
    """С одним днём истории вердикта нет — это честнее выдуманного."""
    seed(conn, [400.0, 450.0, 500.0, 550.0, 600.0], now="2026-08-01T06:00:00Z")
    summary = digest.build_route_summary(
        conn, config_stub, "FRU", "HKT", now="2026-08-01T07:00:00Z"
    )
    assert summary.baseline is None
    assert summary.level == rules.GRAY
    assert summary.delta_pct is None


def test_cheapest_offer_is_not_automatically_green(conn, config_stub):
    """Регрессия: минимум сравнивался с перцентилем того же распределения.

    Минимум всегда ниже любого перцентиля своей выборки, поэтому светофор
    горел зелёным независимо от того, дорого сегодня или дёшево.
    """
    cfg = config_stub.with_overrides({"abs_threshold_usd": "1"})
    for day, price in enumerate([300.0, 305.0, 310.0, 315.0, 320.0], start=20):
        seed(conn, [price], now=f"2026-07-{day}T06:00:00Z")
    seed(conn, [318.0], now="2026-08-01T06:00:00Z")  # обычная цена, не находка
    summary = digest.build_route_summary(conn, cfg, "FRU", "HKT", now="2026-08-01T07:00:00Z")
    assert summary.best_landed == 318.0
    assert summary.level == rules.GRAY


def test_render_matches_expected_shape(config_stub):
    summaries = [
        digest.RouteSummary(
            origin="FRU",
            destination="HKT",
            best_landed=312.0,
            depart_date="2026-10-12",
            return_date="2026-10-24",
            airline="KC",
            transfers=1,
            market="kg",
            search_url="https://www.aviasales.kg/search/x",
            baseline=rules.Baseline(n=30, median=325.0, minimum=300.0, anomaly_threshold=305.0),
            level=rules.YELLOW,
            delta_pct=-0.04,
        ),
        digest.RouteSummary(
            origin="FRU",
            destination="DPS",
            best_landed=None,
            depart_date=None,
            return_date=None,
            airline=None,
            transfers=None,
            market=None,
            search_url=None,
            baseline=None,
            level=rules.GRAY,
            delta_pct=None,
        ),
    ]
    text = digest.render_digest(summaries, config_stub, now="2026-08-01T03:00:00Z")
    assert "октябрь 2026" in text
    assert "FRU→HKT" in text
    assert "$312" in text
    assert "−4%" in text
    assert "🟡" in text
    assert "Air Astana" in text
    assert "12.10" in text
    assert "нет данных" in text


def test_render_escapes_html_special_chars(config_stub):
    summaries = [
        digest.RouteSummary(
            origin="FRU",
            destination="HKT",
            best_landed=312.0,
            depart_date="2026-10-12",
            return_date="2026-10-24",
            airline="A&B",
            transfers=0,
            market="kg",
            search_url="https://x/?a=1&b=2",
            baseline=None,
            level=rules.GRAY,
            delta_pct=None,
        )
    ]
    text = digest.render_digest(summaries, config_stub, now="2026-08-01T03:00:00Z")
    assert "A&amp;B" in text


def test_render_shows_direct_flight_wording(config_stub):
    summaries = [
        digest.RouteSummary(
            origin="ALA",
            destination="HKT",
            best_landed=268.0,
            depart_date="2026-10-08",
            return_date="2026-10-20",
            airline="KC",
            transfers=0,
            market="kg",
            search_url="https://x",
            baseline=rules.Baseline(n=30, median=327.0, minimum=250.0, anomaly_threshold=270.0),
            level=rules.GREEN,
            delta_pct=-0.18,
        )
    ]
    text = digest.render_digest(summaries, config_stub, now="2026-08-01T03:00:00Z")
    assert "без пересадок" in text
    assert "−18%" in text


def test_build_all_summaries_covers_every_route(conn, config_stub):
    summaries = digest.build_all_summaries(conn, config_stub, now="2026-08-01T07:00:00Z")
    assert [(s.origin, s.destination) for s in summaries] == config_stub.routes()


# ---------------------------------------------------------------------------
# format_month_range() / заголовок дайджеста
# ---------------------------------------------------------------------------


def test_format_month_range_single_month():
    assert digest.format_month_range(["2026-10"]) == "октябрь 2026"


def test_format_month_range_two_consecutive_months_same_year():
    assert digest.format_month_range(["2026-10", "2026-11"]) == "октябрь–ноябрь 2026"


def test_format_month_range_uses_en_dash():
    assert "–" in digest.format_month_range(["2026-10", "2026-11"])


def test_format_month_range_different_years():
    assert (
        digest.format_month_range(["2026-12", "2027-01"])
        == "декабрь 2026 – январь 2027"
    )


def test_render_digest_header_two_months(config_stub):
    import dataclasses

    cfg = dataclasses.replace(config_stub, departure_months=["2026-10", "2026-11"])
    text = digest.render_digest([], cfg, now="2026-08-01T03:00:00Z")
    assert "октябрь–ноябрь 2026" in text


def test_render_digest_header_different_years(config_stub):
    import dataclasses

    cfg = dataclasses.replace(config_stub, departure_months=["2026-12", "2027-01"])
    text = digest.render_digest([], cfg, now="2026-08-01T03:00:00Z")
    assert "декабрь 2026 – январь 2027" in text
