import pytest

from src import digest, store
from src.models import PriceRecord


@pytest.fixture()
def conn(tmp_path):
    connection = store.connect(tmp_path / "history.sqlite")
    store.init_schema(connection)
    return connection


def record(landed, depart="2026-10-12", origin="ALA", destination="HKT", market="kg"):
    currency = "kgs" if market == "kg" else "rub"
    return PriceRecord(
        source="aviasales_cache",
        origin=origin,
        destination=destination,
        market=market,
        depart_date=depart,
        return_date="2026-10-24",
        price_local=landed * 87.5,
        currency=currency,
        airline="KC",
        transfers=1,
        duration_min=1500,
        duration_to_min=760,
        search_url="https://x",
        fx_rate=1 / 87.5,
        landed_usd=landed,
    )


def seed(conn, landed, now, **kwargs):
    store.insert_prices(conn, [record(landed, **kwargs)], now=now)


# ---------------------------------------------------------------------------
# dead_man_switch
# ---------------------------------------------------------------------------


def test_dead_man_switch_none_when_recent(conn):
    store.set_meta(conn, "last_scan_at", "2026-08-01T05:00:00Z")
    assert digest.dead_man_switch(conn, now="2026-08-01T07:00:00Z") is None


def test_dead_man_switch_triggers_when_stale(conn):
    store.set_meta(conn, "last_scan_at", "2026-07-30T05:00:00Z")
    msg = digest.dead_man_switch(conn, now="2026-08-01T07:00:00Z")
    assert msg is not None
    assert msg.startswith("🚨")
    assert "2026-07-30T05:00:00Z" in msg
    assert "Actions" in msg


def test_dead_man_switch_triggers_when_missing(conn):
    msg = digest.dead_man_switch(conn, now="2026-08-01T07:00:00Z")
    assert msg is not None
    assert msg.startswith("🚨")
    assert "Actions" in msg


def test_build_digest_text_starts_with_switch_when_triggered(conn, config_stub):
    text = digest.build_digest_text(conn, config_stub, now="2026-08-01T07:00:00Z")
    assert text.startswith("🚨")


# ---------------------------------------------------------------------------
# render_trends
# ---------------------------------------------------------------------------


def test_render_trends_reports_falling_streak(conn, config_stub):
    for day, price in zip(
        ["2026-07-28", "2026-07-29", "2026-07-30", "2026-07-31"],
        [500.0, 480.0, 450.0, 400.0],
    ):
        seed(conn, price, now=f"{day}T06:00:00Z")
    lines = digest.render_trends(conn, config_stub, now="2026-08-01T07:00:00Z")
    assert any("Алматы → Пхукет" in line and "3-й день подряд" in line for line in lines)


def test_render_trends_empty_without_movement(conn, config_stub):
    seed(conn, 500.0, now="2026-07-31T06:00:00Z")
    lines = digest.render_trends(conn, config_stub, now="2026-08-01T07:00:00Z")
    assert lines == []


# ---------------------------------------------------------------------------
# weekly_manual_block
# ---------------------------------------------------------------------------


def test_weekly_manual_block_present_on_monday(config_stub):
    # 03.08.2026 — понедельник в Asia/Bishkek
    text = digest.weekly_manual_block(config_stub, now="2026-08-03T05:00:00Z")
    assert text is not None
    assert "DEL" in text
    assert "KUL" in text
    assert "DXB" in text
    assert "google.com/flights" in text


def test_weekly_manual_block_absent_on_tuesday(config_stub):
    text = digest.weekly_manual_block(config_stub, now="2026-08-04T05:00:00Z")
    assert text is None


# ---------------------------------------------------------------------------
# precision_block
# ---------------------------------------------------------------------------


def test_precision_block_none_without_alerts(conn):
    assert digest.precision_block(conn, now="2026-08-01T07:00:00Z", last_report_at=None) is None


def test_precision_block_asks_for_feedback_when_no_reviews(conn):
    store.record_alert(conn, "k1", 300.0, sent_at="2026-07-20T06:00:00Z", level="green")
    text = digest.precision_block(conn, now="2026-08-01T07:00:00Z", last_report_at=None)
    assert text is not None
    assert "/bought" in text
    assert "/mismatch" in text


def test_precision_block_reports_ratio(conn):
    for i in range(3):
        store.record_alert(
            conn, f"b{i}", 300.0, sent_at="2026-07-20T06:00:00Z", level="green", feedback="bought"
        )
    store.record_alert(
        conn, "m1", 310.0, sent_at="2026-07-21T06:00:00Z", level="green", feedback="mismatch"
    )
    for i in range(3):
        store.record_alert(conn, f"n{i}", 320.0, sent_at="2026-07-22T06:00:00Z", level="yellow")
    text = digest.precision_block(conn, now="2026-08-01T07:00:00Z", last_report_at=None)
    assert text is not None
    assert "3/4" in text
    assert "75%" in text
    assert "всего алертов 7" in text


def test_precision_block_none_when_reported_recently(conn):
    store.record_alert(
        conn, "k1", 300.0, sent_at="2026-07-20T06:00:00Z", level="green", feedback="bought"
    )
    text = digest.precision_block(
        conn, now="2026-08-01T07:00:00Z", last_report_at="2026-07-25T00:00:00Z"
    )
    assert text is None


def test_build_digest_text_records_precision_reported_at(conn, config_stub):
    store.record_alert(
        conn, "k1", 300.0, sent_at="2026-07-20T06:00:00Z", level="green", feedback="bought"
    )
    store.set_meta(conn, "last_scan_at", "2026-08-01T06:30:00Z")
    digest.build_digest_text(conn, config_stub, now="2026-08-01T07:00:00Z")
    assert store.get_meta(conn, "precision_reported_at") == "2026-08-01T07:00:00Z"


# ---------------------------------------------------------------------------
# build_digest_text: arbitrage block
# ---------------------------------------------------------------------------


def test_build_digest_text_includes_arbitrage_block(conn, config_stub):
    store.set_meta(conn, "last_scan_at", "2026-08-01T06:30:00Z")
    seed(conn, 268.0, now="2026-08-01T06:00:00Z", origin="ALA", destination="HKT", market="kg")
    seed(conn, 200.0, now="2026-08-01T06:00:00Z", origin="ALA", destination="HKT", market="ru")
    text = digest.build_digest_text(conn, config_stub, now="2026-08-01T07:00:00Z")
    assert "Кросс-рыночный арбитраж" in text
