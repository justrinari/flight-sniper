import dataclasses
import json

import pytest
import requests
import responses

from src import runner, store
from src.models import PriceRecord

PRICES_URL = "https://api.travelpayouts.com/aviasales/v3/prices_for_dates"
GROUPED_URL = "https://api.travelpayouts.com/aviasales/v3/grouped_prices"
FX_URL = "https://open.er-api.com/v6/latest/USD"
TG_URL = "https://api.telegram.org/botTG/sendMessage"

FX_PAYLOAD = {"result": "success", "rates": {"USD": 1.0, "KGS": 87.5, "RUB": 92.0}}
FX_RATES = {"usd": 1.0, "kgs": 87.5, "rub": 92.0}

CACHE_PAYLOAD = {
    "success": True,
    "data": [
        {
            "origin": "FRU",
            "destination": "HKT",
            "price": 27000,
            "airline": "KC",
            "departure_at": "2026-10-12T10:15:00+06:00",
            "return_at": "2026-10-24T20:00:00+07:00",
            "transfers": 1,
            "duration": 1500,
            "duration_to": 760,
            "link": "/search/x",
            "currency": "kgs",
        }
    ],
}


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("TP_TOKEN", "TP")
    monkeypatch.setenv("TG_BOT_TOKEN", "TG")
    monkeypatch.setenv("TG_CHAT_ID", "999")
    return tmp_path


@pytest.fixture()
def db(env):
    path = env / "history.sqlite"
    conn = store.connect(path)
    store.init_schema(conn)
    conn.close()
    return path


@responses.activate
def test_run_scan_writes_rows_and_stamps_meta(db, config_stub):
    responses.add(responses.GET, FX_URL, json=FX_PAYLOAD, status=200)
    responses.add(responses.GET, PRICES_URL, json=CACHE_PAYLOAD, status=200)
    result = runner.run_scan(config_stub, db_path=db, now="2026-08-01T06:00:00Z")
    conn = store.connect(db)
    # config_stub has 2 markets; the mocked prices endpoint matches any query
    # string and always returns the same item, so scan_all legitimately
    # produces one distinct row per market (market is part of the row's
    # identity in both scan_all's dedup key and the DB's unique index).
    assert store.count_rows(conn) == 2
    assert store.get_meta(conn, "last_scan_at") == "2026-08-01T06:00:00Z"
    assert result["inserted"] == 2
    assert result["errors"] == []


@responses.activate
def test_run_scan_records_landed_usd(db, config_stub):
    responses.add(responses.GET, FX_URL, json=FX_PAYLOAD, status=200)
    responses.add(responses.GET, PRICES_URL, json=CACHE_PAYLOAD, status=200)
    runner.run_scan(config_stub, db_path=db, now="2026-08-01T06:00:00Z")
    conn = store.connect(db)
    row = conn.execute("SELECT landed_usd FROM price_history").fetchone()
    assert row["landed_usd"] == pytest.approx(27000 / 87.5 * 1.025, rel=1e-6)


@responses.activate
def test_run_scan_does_not_stamp_meta_when_everything_failed(db, config_stub):
    responses.add(responses.GET, FX_URL, json=FX_PAYLOAD, status=200)
    responses.add(responses.GET, PRICES_URL, json={"success": False, "error": "x"}, status=200)
    result = runner.run_scan(config_stub, db_path=db, now="2026-08-01T06:00:00Z")
    conn = store.connect(db)
    assert store.get_meta(conn, "last_scan_at") is None
    assert len(result["errors"]) == 16


@responses.activate
def test_run_backfill_only_runs_on_empty_database(db, config_stub):
    responses.add(responses.GET, FX_URL, json=FX_PAYLOAD, status=200)
    responses.add(responses.GET, GROUPED_URL, json={"success": True, "data": {}}, status=200)
    first = runner.run_backfill(config_stub, db_path=db, now="2026-08-01T06:00:00Z")
    assert first["skipped"] is False

    conn = store.connect(db)
    conn.execute(
        "INSERT INTO price_history (scanned_at, last_seen_at, source, origin, destination,"
        " market, depart_date, price_local, currency) VALUES"
        " ('2026-08-01T00:00:00Z','2026-08-01T00:00:00Z','aviasales_cache','FRU','HKT','kg',"
        " '2026-10-12', 1.0, 'kgs')"
    )
    conn.commit()
    conn.close()
    second = runner.run_backfill(config_stub, db_path=db, now="2026-08-01T07:00:00Z")
    assert second["skipped"] is True


@responses.activate
def test_run_backfill_force_ignores_existing_rows(db, config_stub):
    responses.add(responses.GET, FX_URL, json=FX_PAYLOAD, status=200)
    responses.add(responses.GET, GROUPED_URL, json={"success": True, "data": {}}, status=200)
    runner.run_backfill(config_stub, db_path=db, now="2026-08-01T06:00:00Z")
    result = runner.run_backfill(config_stub, db_path=db, now="2026-08-01T07:00:00Z", force=True)
    assert result["skipped"] is False


@responses.activate
def test_run_digest_sends_message(db, config_stub):
    responses.add(responses.GET, FX_URL, json=FX_PAYLOAD, status=200)
    responses.add(responses.GET, PRICES_URL, json=CACHE_PAYLOAD, status=200)
    responses.add(responses.POST, TG_URL, json={"ok": True}, status=200)
    runner.run_scan(config_stub, db_path=db, now="2026-08-01T06:00:00Z")
    runner.run_digest(config_stub, db_path=db, now="2026-08-01T03:00:00Z")
    sent = [c for c in responses.calls if c.request.url.startswith(TG_URL)]
    assert len(sent) == 1
    body = json.loads(sent[0].request.body)
    assert "Бишкек → Пхукет" in body["text"]
    assert body["chat_id"] == "999"


@responses.activate
def test_run_digest_applies_meta_overrides(db, config_stub):
    responses.add(responses.GET, FX_URL, json=FX_PAYLOAD, status=200)
    responses.add(responses.GET, PRICES_URL, json=CACHE_PAYLOAD, status=200)
    responses.add(responses.POST, TG_URL, json={"ok": True}, status=200)
    runner.run_scan(config_stub, db_path=db, now="2026-08-01T06:00:00Z")
    conn = store.connect(db)
    store.set_meta(conn, "abs_threshold_usd", "111")
    conn.close()
    runner.run_digest(config_stub, db_path=db, now="2026-08-01T03:00:00Z")
    body = json.loads(
        [c for c in responses.calls if c.request.url.startswith(TG_URL)][0].request.body
    )
    assert "$111" in body["text"]


def test_require_env_raises_with_helpful_message(monkeypatch):
    monkeypatch.delenv("TP_TOKEN", raising=False)
    with pytest.raises(runner.ConfigurationError, match="TP_TOKEN"):
        runner.require_env("TP_TOKEN")


# ---------------------------------------------------------------------------
# _refresh_best_prices()
# ---------------------------------------------------------------------------


def _price_record(
    landed,
    depart="2026-10-12",
    return_date="2026-10-24",
    market="kg",
    currency="kgs",
    price_local=27000.0,
    origin="FRU",
    destination="HKT",
):
    return PriceRecord(
        source="aviasales_cache",
        origin=origin,
        destination=destination,
        market=market,
        depart_date=depart,
        return_date=return_date,
        price_local=price_local,
        currency=currency,
        airline="KC",
        transfers=1,
        duration_min=1500,
        duration_to_min=760,
        search_url="https://www.aviasales.kg/search/x",
        fx_rate=1 / 87.5,
        landed_usd=landed,
    )


@pytest.fixture()
def single_route_cfg(config_stub):
    return dataclasses.replace(
        config_stub,
        origins=["FRU"],
        destinations=["HKT"],
        markets=["kg"],
        market_currency={"kg": "kgs"},
    )


@responses.activate
def test_refresh_best_prices_requests_specific_date_of_best_record(db, single_route_cfg):
    conn = store.connect(db)
    store.insert_prices(conn, [_price_record(landed=300.0, depart="2026-10-12")], now="2026-08-01T06:00:00Z")
    responses.add(responses.GET, PRICES_URL, json=CACHE_PAYLOAD, status=200)

    runner._refresh_best_prices(
        conn, single_route_cfg, requests.Session(), "TP", FX_RATES, now="2026-08-01T06:30:00Z"
    )

    request_url = responses.calls[0].request.url
    assert "departure_at=2026-10-12" in request_url
    assert "departure_at=2026-10&" not in request_url
    conn.close()


@responses.activate
def test_refresh_best_prices_inserts_returned_records(db, single_route_cfg):
    conn = store.connect(db)
    store.insert_prices(conn, [_price_record(landed=300.0, price_local=27000.0)], now="2026-08-01T06:00:00Z")
    new_payload = json.loads(json.dumps(CACHE_PAYLOAD))
    new_payload["data"][0]["price"] = 27500  # distinct price_local -> a genuinely new row
    responses.add(responses.GET, PRICES_URL, json=new_payload, status=200)

    inserted = runner._refresh_best_prices(
        conn, single_route_cfg, requests.Session(), "TP", FX_RATES, now="2026-08-01T06:30:00Z"
    )

    assert inserted == 1
    conn.close()


@responses.activate
def test_refresh_best_prices_skips_route_without_fresh_data(db, single_route_cfg):
    conn = store.connect(db)
    inserted = runner._refresh_best_prices(
        conn, single_route_cfg, requests.Session(), "TP", FX_RATES, now="2026-08-01T06:30:00Z"
    )
    assert inserted == 0
    assert len(responses.calls) == 0
    conn.close()


@responses.activate
def test_refresh_best_prices_continues_after_aviasales_error(db, config_stub):
    cfg = dataclasses.replace(
        config_stub,
        origins=["FRU", "ALA"],
        destinations=["HKT"],
        markets=["kg"],
        market_currency={"kg": "kgs"},
    )
    conn = store.connect(db)
    store.insert_prices(
        conn, [_price_record(landed=300.0, origin="FRU", destination="HKT")], now="2026-08-01T06:00:00Z"
    )
    store.insert_prices(
        conn, [_price_record(landed=310.0, origin="ALA", destination="HKT")], now="2026-08-01T06:00:00Z"
    )
    responses.add(responses.GET, PRICES_URL, json={"success": False, "error": "boom"}, status=200)
    second_payload = json.loads(json.dumps(CACHE_PAYLOAD))
    second_payload["data"][0]["origin"] = "ALA"
    second_payload["data"][0]["price"] = 15500
    responses.add(responses.GET, PRICES_URL, json=second_payload, status=200)

    inserted = runner._refresh_best_prices(
        conn, cfg, requests.Session(), "TP", FX_RATES, now="2026-08-01T06:30:00Z"
    )

    assert inserted == 1  # первый маршрут упал с ошибкой, второй всё равно обработан
    assert len(responses.calls) == 2
    conn.close()


# ---------------------------------------------------------------------------
# run_digest() refreshes best prices first
# ---------------------------------------------------------------------------


@responses.activate
def test_run_digest_returns_refreshed_count_and_still_sends_message(db, config_stub):
    responses.add(responses.GET, FX_URL, json=FX_PAYLOAD, status=200)
    responses.add(responses.GET, PRICES_URL, json=CACHE_PAYLOAD, status=200)
    responses.add(responses.POST, TG_URL, json={"ok": True}, status=200)
    runner.run_scan(config_stub, db_path=db, now="2026-08-01T06:00:00Z")

    result = runner.run_digest(config_stub, db_path=db, now="2026-08-01T06:10:00Z")

    assert "refreshed" in result
    assert result["sent"] is True
    sent = [c for c in responses.calls if c.request.url.startswith(TG_URL)]
    assert len(sent) == 1


@responses.activate
def test_run_digest_without_tp_token_still_sends_digest(db, config_stub, monkeypatch):
    responses.add(responses.GET, FX_URL, json=FX_PAYLOAD, status=200)
    responses.add(responses.GET, PRICES_URL, json=CACHE_PAYLOAD, status=200)
    responses.add(responses.POST, TG_URL, json={"ok": True}, status=200)
    runner.run_scan(config_stub, db_path=db, now="2026-08-01T06:00:00Z")

    monkeypatch.delenv("TP_TOKEN", raising=False)
    result = runner.run_digest(config_stub, db_path=db, now="2026-08-01T06:10:00Z")

    assert result["sent"] is True
    assert result["refreshed"] == 0
    sent = [c for c in responses.calls if c.request.url.startswith(TG_URL)]
    assert len(sent) == 1
