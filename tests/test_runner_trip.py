import json

import pytest
import requests
import responses

from src import runner, store, trip_digest
from src.models import PriceRecord

PRICES_URL = "https://api.travelpayouts.com/aviasales/v3/prices_for_dates"
FX_URL = "https://open.er-api.com/v6/latest/USD"
TG_URL = "https://api.telegram.org/botTG/sendMessage"
FX_PAYLOAD = {"result": "success", "rates": {"USD": 1.0, "KGS": 87.5, "RUB": 92.0}}
NOW = "2026-10-22T01:00:00Z"


def _record(origin, destination, day, usd):
    return PriceRecord(
        source="aviasales_cache",
        origin=origin,
        destination=destination,
        market="ru",
        depart_date=day,
        return_date=None,
        price_local=round(usd * 92, 2),
        currency="rub",
        airline="AK",
        transfers=0,
        duration_min=300,
        search_url="https://www.aviasales.ru/search/x",
        fx_rate=1 / 92,
        landed_usd=usd,
    )


def _seed(path, prices=(86, 101, 117, 102)):
    conn = store.connect(path)
    store.init_schema(conn)
    days = ["2027-01-18", "2027-01-23", "2027-01-28", "2027-01-31"]
    pairs = [("DPS", "HKT"), ("HKT", "SGN"), ("SGN", "MNL"), ("MNL", "DPS")]
    store.insert_prices(
        conn,
        [_record(o, d, day, usd) for (o, d), day, usd in zip(pairs, days, prices)],
        now=NOW,
    )
    store.set_meta(conn, "last_scan_at", NOW)
    return conn


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("TP_TOKEN", "TP")
    monkeypatch.setenv("TG_BOT_TOKEN", "TG")
    monkeypatch.setenv("TG_CHAT_ID", "999")
    return tmp_path


def _sent_texts():
    return [
        json.loads(call.request.body)["text"]
        for call in responses.calls
        if call.request.url.startswith(TG_URL)
    ]


@responses.activate
def test_digest_in_trip_mode_sends_itinerary_and_remembers_total(env, trip_config):
    db = env / "history.sqlite"
    _seed(db).close()
    responses.add(responses.GET, FX_URL, json=FX_PAYLOAD, status=200)
    responses.add(responses.GET, PRICES_URL, json={"success": True, "data": []}, status=200)
    responses.add(responses.POST, TG_URL, json={"ok": True}, status=200)

    result = runner.run_digest(trip_config, db_path=db, now=NOW)

    texts = _sent_texts()
    assert len(texts) == 1
    assert "<b>$406</b>" in texts[0]
    price_calls = [c for c in responses.calls if c.request.url.startswith(PRICES_URL)]
    assert len(price_calls) == 4  # освежение каждого перелёта лучшего маршрута
    assert result["sent"] is True
    conn = store.connect(db)
    assert store.get_meta(conn, trip_digest.PREV_KEY) == "406.00"
    conn.close()


@responses.activate
def test_trip_alert_fires_below_threshold_once(env, trip_config):
    conn = _seed(env / "history.sqlite", prices=(80, 90, 100, 100))
    responses.add(responses.POST, TG_URL, json={"ok": True}, status=200)
    session = requests.Session()

    first = runner._process_alerts(conn, trip_config, session, "TP", {}, NOW)
    second = runner._process_alerts(conn, trip_config, session, "TP", {}, NOW)

    assert first == (1, 0)
    assert second == (0, 0)
    texts = _sent_texts()
    assert len(texts) == 1
    assert texts[0].startswith("🟢 Маршрут дешевле $380")
    conn.close()


@responses.activate
def test_trip_alert_stays_silent_above_threshold(env, trip_config):
    conn = _seed(env / "history.sqlite")  # $406 > $380
    responses.add(responses.POST, TG_URL, json={"ok": True}, status=200)

    sent = runner._process_alerts(conn, trip_config, requests.Session(), "TP", {}, NOW)

    assert sent == (0, 0)
    assert _sent_texts() == []
    conn.close()


@responses.activate
def test_trip_alert_respects_pause(env, trip_config):
    conn = _seed(env / "history.sqlite", prices=(80, 90, 100, 100))
    store.set_meta(conn, "paused", "1")

    sent = runner._process_alerts(conn, trip_config, requests.Session(), "TP", {}, NOW)

    assert sent == (0, 0)
    conn.close()


@responses.activate
def test_trip_alert_needs_every_leg_seen_in_this_scan(env, trip_config):
    conn = _seed(env / "history.sqlite", prices=(80, 90, 100, 100))
    responses.add(responses.POST, TG_URL, json={"ok": True}, status=200)
    later = "2026-10-22T02:00:00Z"
    # Скан в 02:00 подтвердил только первый перелёт; остальные видны с 01:00.
    store.insert_prices(
        conn, [_record("DPS", "HKT", "2027-01-18", 80)], now=later
    )

    sent = runner._process_alerts(conn, trip_config, requests.Session(), "TP", {}, later)

    assert sent == (0, 0)
    conn.close()


@responses.activate
def test_trip_alert_dedup_forgets_alerts_older_than_a_week(env, trip_config):
    conn = _seed(env / "history.sqlite", prices=(80, 90, 100, 100))
    store.record_alert(conn, "trip", 360.0, sent_at="2026-10-10T00:00:00Z", level="green")
    responses.add(responses.POST, TG_URL, json={"ok": True}, status=200)

    sent = runner._process_alerts(conn, trip_config, requests.Session(), "TP", {}, NOW)

    assert sent == (1, 0)
    conn.close()
