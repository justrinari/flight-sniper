from src import store


def test_connect_creates_file_and_schema(tmp_path):
    db = tmp_path / "history.sqlite"
    conn = store.connect(db)
    store.init_schema(conn)
    tables = {
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert {"price_history", "alerts_sent", "meta", "api_usage"} <= tables
    assert db.exists()


def test_init_schema_is_idempotent(tmp_path):
    conn = store.connect(tmp_path / "history.sqlite")
    store.init_schema(conn)
    store.init_schema(conn)  # не должно бросать
    assert store.count_rows(conn) == 0


def test_rows_come_back_as_mappings(tmp_path):
    conn = store.connect(tmp_path / "history.sqlite")
    store.init_schema(conn)
    conn.execute(
        "INSERT INTO price_history (scanned_at, last_seen_at, source, origin, destination,"
        " market, depart_date, price_local, currency) VALUES"
        " ('2026-08-01T00:00:00Z','2026-08-01T00:00:00Z','aviasales_cache','FRU','HKT','kg',"
        " '2026-10-12', 38000.0, 'kgs')"
    )
    row = conn.execute("SELECT * FROM price_history").fetchone()
    assert row["origin"] == "FRU"


def test_meta_roundtrip(tmp_path):
    conn = store.connect(tmp_path / "history.sqlite")
    store.init_schema(conn)
    assert store.get_meta(conn, "last_scan_at") is None
    assert store.get_meta(conn, "last_scan_at", "never") == "never"
    store.set_meta(conn, "last_scan_at", "2026-08-01T06:00:00Z")
    assert store.get_meta(conn, "last_scan_at") == "2026-08-01T06:00:00Z"
    store.set_meta(conn, "last_scan_at", "2026-08-01T10:00:00Z")
    assert store.get_meta(conn, "last_scan_at") == "2026-08-01T10:00:00Z"


def test_all_meta_returns_dict(tmp_path):
    conn = store.connect(tmp_path / "history.sqlite")
    store.init_schema(conn)
    store.set_meta(conn, "paused", "1")
    store.set_meta(conn, "abs_threshold_usd", "199")
    assert store.all_meta(conn) == {"paused": "1", "abs_threshold_usd": "199"}
