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


# --- предел на число алертов за один ран ------------------------------------
#
# С появлением триггера по дате вылета кандидатов стало на порядок больше:
# каждая из трёх десятков дат месяца может дать свой. Без предела один ран
# мог бы выстрелить полусотней сообщений и полусотней запросов подтверждения.

CHEAP_DATES = [f"2026-10-{day:02d}" for day in range(1, 11)]


def _many_dates_payload(price=150):
    return {
        "success": True,
        "currency": "kgs",
        "data": [
            {
                "origin": "ALA",
                "destination": "HKT",
                "price": price,
                "airline": "KC",
                "departure_at": f"{depart}T10:15:00+06:00",
                "transfers": 0,
                "duration": 1500,
                "duration_to": 760,
                "link": f"/search/{depart}",
                "currency": "kgs",
                "gate": "City.Travel",
            }
            for depart in CHEAP_DATES
        ],
    }


def _seed_many_dates(db_path):
    """По пять дней истории на каждую дату — чтобы у всех был свой baseline."""
    conn = store.connect(db_path)
    store.init_schema(conn)
    for depart in CHEAP_DATES:
        for day, price in zip(range(20, 25), [300.0, 305.0, 310.0, 315.0, 320.0]):
            rec = PriceRecord(
                source="aviasales_cache",
                origin="ALA",
                destination="HKT",
                market="kg",
                depart_date=depart,
                return_date=None,
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
def test_alerts_per_run_are_capped(db, alert_cfg, tg_env):
    _seed_many_dates(db)
    responses.add(responses.GET, FX_URL, json=FX_PAYLOAD, status=200)
    responses.add(responses.GET, PRICES_URL, json=_many_dates_payload(), status=200)
    responses.add(responses.POST, TG_URL, json={"ok": True}, status=200)

    one_way_cfg = dataclasses.replace(alert_cfg, trip_type="one_way")
    result = runner.run_scan(one_way_cfg, db_path=db, now="2026-08-01T06:00:00Z")

    assert result["alerts"] == runner.MAX_ALERTS_PER_RUN
    assert result["alerts_skipped"] == len(CHEAP_DATES) - runner.MAX_ALERTS_PER_RUN
    sent = [c for c in responses.calls if c.request.url.startswith(TG_URL)]
    assert len(sent) == runner.MAX_ALERTS_PER_RUN


@responses.activate
def test_cheapest_candidates_win_the_cap(db, alert_cfg, tg_env):
    """Предел отрезает хвост, а не случайные находки: уходят самые дешёвые."""
    _seed_many_dates(db)
    payload = _many_dates_payload()
    # первая дата заметно дешевле остальных — она обязана пройти
    payload["data"][0]["price"] = 100
    responses.add(responses.GET, FX_URL, json=FX_PAYLOAD, status=200)
    responses.add(responses.GET, PRICES_URL, json=payload, status=200)
    responses.add(responses.POST, TG_URL, json={"ok": True}, status=200)

    one_way_cfg = dataclasses.replace(alert_cfg, trip_type="one_way")
    runner.run_scan(one_way_cfg, db_path=db, now="2026-08-01T06:00:00Z")

    conn = store.connect(db)
    keys = [r["route_date_key"] for r in conn.execute("SELECT * FROM alerts_sent")]
    assert f"ALA-HKT-{CHEAP_DATES[0]}-" in keys


# --- подтверждение вторым проходом ------------------------------------------


@responses.activate
def test_new_offer_alerts_only_after_a_second_pass(db, alert_cfg, tg_env):
    """Цена, увиденная впервые, ждёт подтверждения следующим проходом.

    Стоит 14 минут задержки и отсекает записи, которых при следующем запросе
    уже нет: на живой базе 18,4% офферов живут меньше одного прохода, и оба
    оффера с невозможными ценами были из их числа.
    """
    _seed_history(db)
    responses.add(responses.GET, FX_URL, json=FX_PAYLOAD, status=200)
    responses.add(responses.GET, PRICES_URL, json=_cheap_payload(), status=200)
    responses.add(responses.POST, TG_URL, json={"ok": True}, status=200)

    cfg = dataclasses.replace(alert_cfg, min_offer_lifetime_minutes=10.0)

    first = runner.run_scan(cfg, db_path=db, now="2026-08-01T06:00:00Z")
    assert first["alerts"] == 0  # оффер увиден впервые — молчим

    second = runner.run_scan(cfg, db_path=db, now="2026-08-01T06:14:00Z")
    assert second["alerts"] == 1  # тот же оффер подтвердился — можно будить


@responses.activate
def test_offer_that_disappeared_after_one_pass_never_alerts(db, alert_cfg, tg_env):
    """Невозможная цена мелькнула в одном проходе и исчезла — алерта нет никогда.

    Во втором проходе кэш отдаёт уже другую цену, поэтому у записи с первой
    так и остаётся одна встреча: подтвердить её нечем.
    """
    _seed_history(db)
    responses.add(responses.GET, FX_URL, json=FX_PAYLOAD, status=200)
    responses.add(responses.GET, PRICES_URL, json=_cheap_payload(price=50), status=200)
    responses.add(responses.GET, PRICES_URL, json=_cheap_payload(price=320), status=200)
    responses.add(responses.POST, TG_URL, json={"ok": True}, status=200)

    cfg = dataclasses.replace(alert_cfg, min_offer_lifetime_minutes=10.0)

    runner.run_scan(cfg, db_path=db, now="2026-08-01T06:00:00Z")
    runner.run_scan(cfg, db_path=db, now="2026-08-01T06:14:00Z")
    # третий проход подтверждает уже вторую, настоящую цену
    runner.run_scan(cfg, db_path=db, now="2026-08-01T06:28:00Z")

    conn = store.connect(db)
    alerted = [round(r["landed_usd"], 2) for r in conn.execute("SELECT * FROM alerts_sent")]
    phantom_usd = round(50 / 87.5 * 1.025, 2)
    real_usd = round(320 / 87.5 * 1.025, 2)
    assert phantom_usd not in alerted
    assert alerted == [real_usd]
