import pytest

from src import store


@pytest.fixture()
def conn(tmp_path):
    connection = store.connect(tmp_path / "history.sqlite")
    store.init_schema(connection)
    return connection


def test_first_alert_for_key_is_always_allowed(conn):
    assert store.should_alert(conn, "FRU-HKT-2026-10-12-2026-10-24", 300.0, drop_ratio=0.05) is True


def test_same_price_is_suppressed(conn):
    store.record_alert(conn, "key1", 300.0, sent_at="2026-08-01T06:00:00Z", level="green")
    assert store.should_alert(conn, "key1", 300.0, drop_ratio=0.05) is False


def test_drop_below_ratio_is_suppressed(conn):
    store.record_alert(conn, "key1", 300.0, sent_at="2026-08-01T06:00:00Z", level="green")
    # 300 * (1 - 0.05) = 285; 294 упал недостаточно
    assert store.should_alert(conn, "key1", 294.0, drop_ratio=0.05) is False


def test_drop_at_or_beyond_ratio_is_allowed(conn):
    store.record_alert(conn, "key1", 300.0, sent_at="2026-08-01T06:00:00Z", level="green")
    # 300 * (1 - 0.05) = 285; 280 упал достаточно
    assert store.should_alert(conn, "key1", 280.0, drop_ratio=0.05) is True


def test_price_increase_is_suppressed(conn):
    store.record_alert(conn, "key1", 300.0, sent_at="2026-08-01T06:00:00Z", level="green")
    assert store.should_alert(conn, "key1", 310.0, drop_ratio=0.05) is False


def test_different_route_date_key_is_independent(conn):
    store.record_alert(conn, "key1", 200.0, sent_at="2026-08-01T06:00:00Z", level="green")
    assert store.should_alert(conn, "key2", 500.0, drop_ratio=0.05) is True


def test_compares_against_minimum_of_previous_alerts_not_last(conn):
    """300 → 280 → 290: минимум остаётся 280, не последний отправленный (290)."""
    store.record_alert(conn, "key1", 300.0, sent_at="2026-08-01T06:00:00Z", level="green")
    store.record_alert(conn, "key1", 280.0, sent_at="2026-08-01T10:00:00Z", level="green")
    store.record_alert(conn, "key1", 290.0, sent_at="2026-08-01T14:00:00Z", level="green")
    # 280 * (1 - 0.05) = 266
    assert store.should_alert(conn, "key1", 275.0, drop_ratio=0.05) is False
    assert store.should_alert(conn, "key1", 265.0, drop_ratio=0.05) is True


def test_last_alert_returns_most_recent(conn):
    store.record_alert(conn, "key1", 300.0, sent_at="2026-08-01T06:00:00Z", level="green")
    store.record_alert(conn, "key2", 250.0, sent_at="2026-08-01T10:00:00Z", level="yellow")
    row = store.last_alert(conn)
    assert row["route_date_key"] == "key2"
    assert row["landed_usd"] == 250.0


def test_last_alert_on_empty_table_is_none(conn):
    assert store.last_alert(conn) is None


def test_set_last_feedback_updates_most_recent_alert(conn):
    store.record_alert(conn, "key1", 300.0, sent_at="2026-08-01T06:00:00Z", level="green")
    store.record_alert(conn, "key2", 250.0, sent_at="2026-08-01T10:00:00Z", level="yellow")
    ok = store.set_last_feedback(conn, "bought")
    assert ok is True
    latest = store.last_alert(conn)
    assert latest["route_date_key"] == "key2"
    assert latest["feedback"] == "bought"
    earlier = conn.execute(
        "SELECT feedback FROM alerts_sent WHERE route_date_key = 'key1'"
    ).fetchone()
    assert earlier["feedback"] is None


def test_set_last_feedback_on_empty_table_is_false(conn):
    assert store.set_last_feedback(conn, "bought") is False


def test_alert_stats_counts_by_feedback_within_window(conn):
    store.record_alert(
        conn, "key1", 300.0, sent_at="2026-07-01T06:00:00Z", level="green", feedback="bought"
    )  # до окна
    store.record_alert(
        conn, "key2", 250.0, sent_at="2026-08-01T06:00:00Z", level="green", feedback="bought"
    )
    store.record_alert(
        conn, "key3", 260.0, sent_at="2026-08-02T06:00:00Z", level="yellow", feedback="mismatch"
    )
    store.record_alert(conn, "key4", 270.0, sent_at="2026-08-03T06:00:00Z", level="green")
    stats = store.alert_stats(conn, since="2026-08-01T00:00:00Z")
    assert stats == {"total": 3, "bought": 1, "mismatch": 1, "no_feedback": 1}
