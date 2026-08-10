import pytest

from src import rules, store
from src.models import PriceRecord


def test_percentile_median_of_odd_list():
    assert rules.percentile([1, 2, 3, 4, 5], 50) == 3.0


def test_percentile_interpolates():
    assert rules.percentile([1, 2, 3, 4], 10) == pytest.approx(1.3)


def test_percentile_edges():
    assert rules.percentile([10, 20, 30], 0) == 10.0
    assert rules.percentile([10, 20, 30], 100) == 30.0


def test_percentile_single_value():
    assert rules.percentile([42], 10) == 42.0


def test_percentile_unsorted_input():
    assert rules.percentile([5, 1, 3], 50) == 3.0


def test_percentile_empty_raises():
    with pytest.raises(ValueError):
        rules.percentile([], 50)


def test_compute_baseline_returns_stats():
    values = [100, 110, 120, 130, 140, 150, 160, 170, 180, 190]
    baseline = rules.compute_baseline(values, anomaly_percentile=10)
    assert baseline.n == 10
    assert baseline.median == pytest.approx(145.0)
    assert baseline.minimum == 100.0
    assert baseline.anomaly_threshold == pytest.approx(109.0)


def test_compute_baseline_returns_none_below_minimum_sample():
    assert rules.compute_baseline([100, 110], anomaly_percentile=10) is None


def test_compute_baseline_honors_custom_min_sample():
    baseline = rules.compute_baseline([100, 110], anomaly_percentile=10, min_sample=2)
    assert baseline is not None
    assert baseline.n == 2


def test_compute_baseline_of_empty_is_none():
    assert rules.compute_baseline([], anomaly_percentile=10) is None


# --- baseline по конкретной дате вылета -------------------------------------


@pytest.fixture()
def conn(tmp_path):
    connection = store.connect(tmp_path / "history.sqlite")
    store.init_schema(connection)
    return connection


def _record(price_local, depart, market="kg"):
    return PriceRecord(
        source="aviasales_cache",
        origin="ALA",
        destination="HKT",
        market=market,
        depart_date=depart,
        return_date=None,
        price_local=price_local,
        currency="kgs" if market == "kg" else "rub",
        airline="D7",
        transfers=1,
        duration_min=960,
        duration_to_min=960,
        search_url="https://x",
        fx_rate=0.0114,
        landed_usd=round(price_local * 0.0114, 4),
        gate="Tickets.kz",
    )


def _seed(conn, prices_by_date, now):
    store.insert_prices(
        conn,
        [_record(p, depart) for depart, p in prices_by_date.items()],
        now=now,
    )


def test_daily_minimums_by_date_groups_per_departure_date(conn):
    _seed(conn, {"2026-10-17": 300.0, "2026-10-18": 500.0}, now="2026-07-20T06:00:00Z")
    _seed(conn, {"2026-10-17": 290.0, "2026-10-18": 480.0}, now="2026-07-21T06:00:00Z")

    grouped = rules.daily_minimums_by_date(
        conn, "ALA", "HKT", since="2026-07-01T00:00:00Z", market="kg", column="price_local"
    )

    assert grouped["2026-10-17"] == [("2026-07-20", 300.0), ("2026-07-21", 290.0)]
    assert grouped["2026-10-18"] == [("2026-07-20", 500.0), ("2026-07-21", 480.0)]


def test_daily_minimums_by_date_takes_daily_minimum_not_every_offer(conn):
    store.insert_prices(
        conn,
        [_record(500.0, "2026-10-18"), _record(460.0, "2026-10-18")],
        now="2026-07-20T06:00:00Z",
    )
    grouped = rules.daily_minimums_by_date(
        conn, "ALA", "HKT", since="2026-07-01T00:00:00Z", market="kg", column="price_local"
    )
    assert grouped["2026-10-18"] == [("2026-07-20", 460.0)]


def test_daily_minimums_by_date_rejects_invalid_column(conn):
    with pytest.raises(ValueError):
        rules.daily_minimums_by_date(
            conn, "ALA", "HKT", since="2026-07-01T00:00:00Z", column="1; DROP TABLE x"
        )


# --- фантомы не попадают в статистику ---------------------------------------


def _seen_for(conn, landed, depart, first_seen, minutes_alive):
    store.insert_prices(conn, [_record(landed, depart)], now=first_seen)
    conn.execute(
        "UPDATE price_history SET last_seen_at = ? WHERE landed_usd = ?",
        (store.shift_hours(first_seen, minutes_alive / 60.0), round(landed * 0.0114, 4)),
    )
    conn.commit()


def test_daily_minimums_can_exclude_single_pass_offers(conn):
    """Фантом за 3 секунды не должен становиться дневным минимумом.

    Именно так $171 от 02.08.2026 попал в недельный тренд и превратил
    падение $280 → $272 в «на 58% дороже, чем неделю назад».
    """
    _seen_for(conn, 171.0, "2026-10-06", "2026-07-20T06:00:00Z", minutes_alive=0.05)
    _seen_for(conn, 300.0, "2026-10-06", "2026-07-20T06:00:00Z", minutes_alive=14)

    result = rules.daily_minimums(
        conn, "ALA", "HKT", since="2026-07-01T00:00:00Z", min_lifetime_minutes=10
    )

    assert result == [("2026-07-20", round(300.0 * 0.0114, 4))]


def test_daily_minimums_by_date_can_exclude_single_pass_offers(conn):
    _seen_for(conn, 171.0, "2026-10-06", "2026-07-20T06:00:00Z", minutes_alive=0.05)
    _seen_for(conn, 300.0, "2026-10-06", "2026-07-20T06:00:00Z", minutes_alive=14)

    grouped = rules.daily_minimums_by_date(
        conn, "ALA", "HKT", since="2026-07-01T00:00:00Z", min_lifetime_minutes=10
    )

    assert grouped["2026-10-06"] == [("2026-07-20", round(300.0 * 0.0114, 4))]


def test_week_delta_ignores_single_pass_offers(conn):
    """Регрессия на конкретный случай: было $280 → стало $272, это −3%, а не +58%."""
    _seen_for(conn, 171.0, "2026-10-06", "2026-07-20T06:00:00Z", minutes_alive=0.05)
    _seen_for(conn, 280.0, "2026-10-06", "2026-07-20T06:00:00Z", minutes_alive=14)
    _seen_for(conn, 272.0, "2026-10-06", "2026-07-28T06:00:00Z", minutes_alive=14)

    delta = rules.week_delta(
        conn, "ALA", "HKT", now="2026-07-30T06:00:00Z", min_lifetime_minutes=10
    )

    assert delta == pytest.approx(272.0 / 280.0 - 1.0, abs=1e-3)


def test_dow_factor_ignores_single_pass_offers(conn):
    """Фантом на четверге сделал бы четверг «дешёвым днём» на пустом месте."""
    for day, depart in enumerate(["2026-10-01", "2026-10-08", "2026-10-15",
                                  "2026-10-22", "2026-10-29"], start=20):
        _seen_for(conn, 300.0 + day, depart, f"2026-07-{day}T06:00:00Z", minutes_alive=14)
    for day, depart in enumerate(["2026-10-02", "2026-10-09", "2026-10-16",
                                  "2026-10-23", "2026-10-30"], start=20):
        _seen_for(conn, 300.0 + day, depart, f"2026-07-{day}T06:00:00Z", minutes_alive=14)
    # фантом на 01.10 (четверг) вдесятеро дешевле всего остального
    _seen_for(conn, 30.0, "2026-10-01", "2026-07-25T06:00:00Z", minutes_alive=0.05)

    clean = rules.dow_factor(
        conn, "ALA", "HKT", "2026-10-01", now="2026-07-30T06:00:00Z",
        min_lifetime_minutes=10,
    )
    polluted = rules.dow_factor(conn, "ALA", "HKT", "2026-10-01", now="2026-07-30T06:00:00Z")

    assert clean > polluted
