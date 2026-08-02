"""Алерты, встроенные в run_scan (задача D)."""

import dataclasses

import pytest
import responses

from src import runner, store
from src.models import PriceRecord

PRICES_URL = "https://api.travelpayouts.com/aviasales/v3/prices_for_dates"
FX_URL = "https://open.er-api.com/v6/latest/USD"
TG_URL = "https://api.telegram.org/botTG/sendMessage"

FX_PAYLOAD = {"result": "success", "rates": {"USD": 1.0, "KGS": 87.5, "RUB": 92.0}}

ROUTE_DATE_KEY = "ALA-HKT-2026-10-06-2026-10-16"


def _cheap_payload(price=150):
    return {
        "success": True,
        "currency": "kgs",
        "data": [
            {
                "origin": "ALA",
                "destination": "HKT",
                "price": price,
                "airline": "KC",
                "departure_at": "2026-10-06T10:15:00+06:00",
                "return_at": "2026-10-16T20:00:00+07:00",
                "transfers": 0,
                "duration": 1500,
                "duration_to": 760,
                "link": "/search/cheap",
                "currency": "kgs",
                "gate": "City.Travel",
            }
        ],
    }


@pytest.fixture()
def env(monkeypatch):
    monkeypatch.setenv("TP_TOKEN", "TP")


@pytest.fixture()
def tg_env(env, monkeypatch):
    monkeypatch.setenv("TG_BOT_TOKEN", "TG")
    monkeypatch.setenv("TG_CHAT_ID", "999")


@pytest.fixture()
def db(env, tmp_path):
    path = tmp_path / "history.sqlite"
    conn = store.connect(path)
    store.init_schema(conn)
    conn.close()
    return path


@pytest.fixture()
def alert_cfg(config_stub):
    # Один маршрут и один рынок — чтобы не гадать, сколько кандидатов найдётся.
    return dataclasses.replace(
        config_stub,
        origins=["ALA"],
        destinations=["HKT"],
        markets=["kg"],
        market_currency={"kg": "kgs"},
    )


def _seed_history(db_path):
    conn = store.connect(db_path)
    store.init_schema(conn)
    for day, price in zip(range(20, 25), [300.0, 305.0, 310.0, 315.0, 320.0]):
        rec = PriceRecord(
            source="aviasales_cache",
            origin="ALA",
            destination="HKT",
            market="kg",
            depart_date="2026-10-06",
            return_date="2026-10-16",
            price_local=price,
            currency="kgs",
            airline="KC",
            transfers=0,
            duration_min=1500,
            duration_to_min=760,
            search_url="https://x/search",
            gate="City.Travel",
        )
        store.insert_prices(conn, [rec], now=f"2026-07-{day}T06:00:00Z")
    conn.close()


@responses.activate
def test_anomaly_confirmed_sends_one_buy_message(db, alert_cfg, tg_env):
    _seed_history(db)
    responses.add(responses.GET, FX_URL, json=FX_PAYLOAD, status=200)
    responses.add(responses.GET, PRICES_URL, json=_cheap_payload(), status=200)
    responses.add(responses.POST, TG_URL, json={"ok": True}, status=200)

    result = runner.run_scan(alert_cfg, db_path=db, now="2026-08-01T06:00:00Z")

    assert result["alerts"] == 1
    sent = [c for c in responses.calls if c.request.url.startswith(TG_URL)]
    assert len(sent) == 1
    assert "BUY" in sent[0].request.body.decode()

    conn = store.connect(db)
    row = conn.execute("SELECT * FROM alerts_sent").fetchone()
    assert row["route_date_key"] == ROUTE_DATE_KEY
    assert row["level"] == "buy"


@responses.activate
def test_second_scan_with_same_price_sends_nothing(db, alert_cfg, tg_env):
    _seed_history(db)
    responses.add(responses.GET, FX_URL, json=FX_PAYLOAD, status=200)
    responses.add(responses.GET, PRICES_URL, json=_cheap_payload(), status=200)
    responses.add(responses.POST, TG_URL, json={"ok": True}, status=200)

    first = runner.run_scan(alert_cfg, db_path=db, now="2026-08-01T06:00:00Z")
    assert first["alerts"] == 1

    second = runner.run_scan(alert_cfg, db_path=db, now="2026-08-01T10:00:00Z")
    assert second["alerts"] == 0
    sent = [c for c in responses.calls if c.request.url.startswith(TG_URL)]
    assert len(sent) == 1  # не второе сообщение


@responses.activate
def test_paused_flag_suppresses_all_alerts(db, alert_cfg, tg_env):
    _seed_history(db)
    conn = store.connect(db)
    store.set_meta(conn, "paused", "1")
    conn.close()

    responses.add(responses.GET, FX_URL, json=FX_PAYLOAD, status=200)
    responses.add(responses.GET, PRICES_URL, json=_cheap_payload(), status=200)

    result = runner.run_scan(alert_cfg, db_path=db, now="2026-08-01T06:00:00Z")
    assert result["alerts"] == 0
    assert not [c for c in responses.calls if "telegram" in c.request.url]


@responses.activate
def test_missing_telegram_env_skips_alerts_without_raising(db, alert_cfg, env):
    # env задаёт только TP_TOKEN — TG_BOT_TOKEN/TG_CHAT_ID отсутствуют.
    _seed_history(db)
    responses.add(responses.GET, FX_URL, json=FX_PAYLOAD, status=200)
    responses.add(responses.GET, PRICES_URL, json=_cheap_payload(), status=200)

    result = runner.run_scan(alert_cfg, db_path=db, now="2026-08-01T06:00:00Z")
    assert result["alerts"] == 0
    assert not [c for c in responses.calls if "telegram" in c.request.url]


@responses.activate
def test_telegram_error_leaves_alert_unrecorded_for_retry(db, alert_cfg, tg_env):
    _seed_history(db)
    responses.add(responses.GET, FX_URL, json=FX_PAYLOAD, status=200)
    responses.add(responses.GET, PRICES_URL, json=_cheap_payload(), status=200)
    responses.add(
        responses.POST, TG_URL, json={"ok": False, "description": "blocked"}, status=200
    )

    result = runner.run_scan(alert_cfg, db_path=db, now="2026-08-01T06:00:00Z")
    assert result["alerts"] == 0

    conn = store.connect(db)
    assert conn.execute("SELECT COUNT(*) FROM alerts_sent").fetchone()[0] == 0
    # т.к. запись не сделана, следующий скан снова попробует отправить алерт
    assert store.should_alert(conn, ROUTE_DATE_KEY, 1.76, drop_ratio=runner.ALERT_DROP_RATIO)
