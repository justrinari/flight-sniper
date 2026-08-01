import pytest

from src import store
from src.models import PriceRecord


@pytest.fixture()
def conn(tmp_path):
    connection = store.connect(tmp_path / "history.sqlite")
    store.init_schema(connection)
    return connection


def make_record(**kwargs) -> PriceRecord:
    base = dict(
        source="aviasales_cache",
        origin="FRU",
        destination="HKT",
        market="kg",
        depart_date="2026-10-12",
        return_date="2026-10-24",
        price_local=38000.0,
        currency="kgs",
        airline="KC",
        transfers=1,
        duration_min=1500,
        duration_to_min=760,
        search_url="https://www.aviasales.kg/search/x",
        fx_rate=0.0114,
        landed_usd=444.0,
    )
    base.update(kwargs)
    return PriceRecord(**base)


def test_insert_writes_row_and_returns_count(conn):
    inserted = store.insert_prices(conn, [make_record()], now="2026-08-01T06:00:00Z")
    assert inserted == 1
    row = conn.execute("SELECT * FROM price_history").fetchone()
    assert row["landed_usd"] == 444.0
    assert row["scanned_at"] == "2026-08-01T06:00:00Z"
    assert row["last_seen_at"] == "2026-08-01T06:00:00Z"


def test_identical_offer_updates_last_seen_instead_of_duplicating(conn):
    store.insert_prices(conn, [make_record()], now="2026-08-01T06:00:00Z")
    inserted = store.insert_prices(conn, [make_record()], now="2026-08-01T10:00:00Z")
    assert inserted == 0
    assert store.count_rows(conn) == 1
    row = conn.execute("SELECT * FROM price_history").fetchone()
    assert row["scanned_at"] == "2026-08-01T06:00:00Z"  # первое появление сохранено
    assert row["last_seen_at"] == "2026-08-01T10:00:00Z"


def test_price_change_creates_new_row(conn):
    store.insert_prices(conn, [make_record()], now="2026-08-01T06:00:00Z")
    store.insert_prices(conn, [make_record(price_local=35000.0)], now="2026-08-01T10:00:00Z")
    assert store.count_rows(conn) == 2


def test_different_market_creates_new_row(conn):
    store.insert_prices(conn, [make_record()], now="2026-08-01T06:00:00Z")
    store.insert_prices(
        conn, [make_record(market="ru", currency="rub")], now="2026-08-01T06:00:00Z"
    )
    assert store.count_rows(conn) == 2


def test_recent_prices_filters_by_route_and_window(conn):
    store.insert_prices(
        conn,
        [
            make_record(price_local=38000.0),
            make_record(destination="DPS", price_local=50000.0),
        ],
        now="2026-08-01T06:00:00Z",
    )
    store.insert_prices(
        conn, [make_record(price_local=30000.0)], now="2026-06-01T06:00:00Z"
    )
    rows = store.recent_prices(conn, "FRU", "HKT", since="2026-07-01T00:00:00Z")
    assert [r["price_local"] for r in rows] == [38000.0]


def test_recent_prices_filters_by_market(conn):
    store.insert_prices(
        conn,
        [make_record(), make_record(market="ru", currency="rub", price_local=41000.0)],
        now="2026-08-01T06:00:00Z",
    )
    rows = store.recent_prices(conn, "FRU", "HKT", since="2026-07-01T00:00:00Z", market="ru")
    assert [r["market"] for r in rows] == ["ru"]


def test_fresh_prices_excludes_stale_cache(conn):
    store.insert_prices(conn, [make_record(price_local=38000.0)], now="2026-08-01T00:00:00Z")
    store.insert_prices(conn, [make_record(price_local=31000.0)], now="2026-07-25T00:00:00Z")
    rows = store.fresh_prices(
        conn, "FRU", "HKT", now="2026-08-01T12:00:00Z", ttl_hours=48
    )
    assert [r["price_local"] for r in rows] == [38000.0]


def test_fresh_prices_respects_expires_at_in_the_past(conn):
    store.insert_prices(
        conn,
        [make_record(price_local=38000.0, expires_at="2026-07-31T00:00:00Z")],
        now="2026-08-01T00:00:00Z",
    )
    rows = store.fresh_prices(conn, "FRU", "HKT", now="2026-08-01T12:00:00Z", ttl_hours=48)
    assert rows == []


def test_reinsert_refreshes_fx_rate_and_landed(conn):
    store.insert_prices(conn, [make_record()], now="2026-08-01T06:00:00Z")
    store.insert_prices(
        conn,
        [make_record(fx_rate=0.0105, landed_usd=410.0)],
        now="2026-08-02T00:00:00Z",
    )
    row = conn.execute("SELECT * FROM price_history").fetchone()
    assert store.count_rows(conn) == 1
    assert row["landed_usd"] == 410.0
    assert row["fx_rate"] == 0.0105
    assert row["last_seen_at"] == "2026-08-02T00:00:00Z"


def test_fresh_prices_keeps_row_with_future_expiry(conn):
    store.insert_prices(
        conn,
        [make_record(price_local=38000.0, expires_at="2026-08-05T00:00:00Z")],
        now="2026-08-01T00:00:00Z",
    )
    rows = store.fresh_prices(conn, "FRU", "HKT", now="2026-08-01T12:00:00Z", ttl_hours=48)
    assert len(rows) == 1


def test_prune_removes_old_rows(conn):
    store.insert_prices(conn, [make_record()], now="2026-01-01T00:00:00Z")
    store.insert_prices(conn, [make_record(price_local=1.0)], now="2026-08-01T00:00:00Z")
    removed = store.prune(conn, before="2026-06-01T00:00:00Z")
    assert removed == 1
    assert store.count_rows(conn) == 1
