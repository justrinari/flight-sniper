from datetime import date, timedelta

import pytest

from src import rules, store
from src.models import PriceRecord

NOW = "2026-08-01T06:00:00Z"


@pytest.fixture()
def conn(tmp_path):
    connection = store.connect(tmp_path / "history.sqlite")
    store.init_schema(connection)
    return connection


def record(landed, depart="2026-10-12", origin="ALA", destination="HKT", market="kg"):
    return PriceRecord(
        source="aviasales_cache",
        origin=origin,
        destination=destination,
        market=market,
        depart_date=depart,
        return_date=None,
        price_local=landed,
        currency="usd",
        airline="KC",
        transfers=1,
        duration_min=1500,
        duration_to_min=760,
        search_url="https://x",
        fx_rate=1.0,
        landed_usd=landed,
    )


def seed(conn, landed, now, **kwargs):
    store.insert_prices(conn, [record(landed, **kwargs)], now=now)


def _dates_with_weekday(start: date, weekday: int, count: int) -> list[str]:
    d = start
    while d.weekday() != weekday:
        d += timedelta(days=1)
    return [(d + timedelta(weeks=i)).isoformat() for i in range(count)]


# ---------------------------------------------------------------------------
# falling_streak
# ---------------------------------------------------------------------------


def test_falling_streak_counts_consecutive_drops():
    daily = [
        ("2026-07-28", 500.0),
        ("2026-07-29", 480.0),
        ("2026-07-30", 450.0),
        ("2026-07-31", 400.0),
    ]
    assert rules.falling_streak(daily) == 3


def test_falling_streak_zero_when_price_rose_today():
    daily = [
        ("2026-07-30", 400.0),
        ("2026-07-31", 450.0),
    ]
    assert rules.falling_streak(daily) == 0


def test_falling_streak_zero_with_fewer_than_two_days():
    assert rules.falling_streak([("2026-07-31", 400.0)]) == 0
    assert rules.falling_streak([]) == 0


# ---------------------------------------------------------------------------
# week_delta
# ---------------------------------------------------------------------------


def test_week_delta_minus_ten_percent(conn):
    # предыдущая неделя 18-24 июля: минимум 500
    for day in range(18, 25):
        seed(conn, 500.0, now=f"2026-07-{day:02d}T06:00:00Z")
    # последняя неделя 25-31 июля: минимум 450 (-10%)
    for day in range(25, 32):
        seed(conn, 450.0, now=f"2026-07-{day:02d}T06:00:00Z")
    delta = rules.week_delta(conn, "ALA", "HKT", now=NOW)
    assert delta == pytest.approx(-0.10)


def test_week_delta_none_without_previous_week(conn):
    seed(conn, 450.0, now="2026-07-30T06:00:00Z")
    seed(conn, 400.0, now="2026-08-01T06:00:00Z")
    assert rules.week_delta(conn, "ALA", "HKT", now=NOW) is None


# ---------------------------------------------------------------------------
# dow_factor
# ---------------------------------------------------------------------------


def test_dow_factor_is_one_with_small_sample(conn):
    monday_dates = _dates_with_weekday(date(2026, 1, 1), 0, 3)  # меньше MIN_SAMPLE
    for i, d in enumerate(monday_dates):
        seed(conn, 300.0 + i, now=f"2026-07-{i + 1:02d}T06:00:00Z", depart=d)
    factor = rules.dow_factor(conn, "ALA", "HKT", depart_date="2026-10-12", now=NOW)
    assert factor == 1.0


def test_dow_factor_below_one_when_day_is_systematically_cheaper(conn):
    monday_dates = _dates_with_weekday(date(2026, 1, 1), 0, 6)
    saturday_dates = _dates_with_weekday(date(2026, 1, 1), 5, 6)
    for i, d in enumerate(monday_dates):
        seed(conn, 250.0 + i, now=f"2026-07-{i + 1:02d}T06:00:00Z", depart=d)
    for i, d in enumerate(saturday_dates):
        seed(conn, 400.0 + i, now=f"2026-07-{i + 10:02d}T06:00:00Z", depart=d)
    # "2026-10-12" — понедельник
    factor = rules.dow_factor(conn, "ALA", "HKT", depart_date="2026-10-12", now=NOW)
    assert factor < 1.0
