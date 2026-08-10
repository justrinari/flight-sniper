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

