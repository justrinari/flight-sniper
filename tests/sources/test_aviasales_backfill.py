import pytest
import requests
import responses

from src.sources import aviasales

URL = f"{aviasales.BASE_URL}/grouped_prices"

GROUPED = {
    "success": True,
    "data": {
        "2026-10-05": {
            "origin": "FRU",
            "destination": "HKT",
            "price": 36000,
            "airline": "KC",
            "departure_at": "2026-10-05T10:00:00+06:00",
            "return_at": "2026-10-17T20:00:00+07:00",
            "transfers": 1,
            "duration": 1490,
            "duration_to": 750,
            "link": "/search/a",
        },
        "2026-10-12": {
            "origin": "FRU",
            "destination": "HKT",
            "price": 38000,
            "airline": "TK",
            "departure_at": "2026-10-12T10:00:00+06:00",
            "return_at": "2026-10-24T20:00:00+07:00",
            "transfers": 1,
            "duration": 1500,
            "duration_to": 760,
            "link": "/search/b",
        },
    },
}


@pytest.fixture()
def session():
    return requests.Session()


def test_parse_grouped_prices_flattens_dict_into_records():
    records = aviasales.parse_grouped_prices(GROUPED, market="kg", currency="kgs")
    assert len(records) == 2
    assert {r.depart_date for r in records} == {"2026-10-05", "2026-10-12"}
    assert records[0].currency == "kgs"
    assert records[0].source == "aviasales_cache"


def test_parse_grouped_prices_handles_empty_payload():
    assert aviasales.parse_grouped_prices({"data": {}}, market="kg", currency="kgs") == []


def test_parse_grouped_prices_skips_malformed_entries():
    payload = {"data": {"2026-10-05": {"origin": "FRU"}, "2026-10-12": GROUPED["data"]["2026-10-12"]}}
    assert len(aviasales.parse_grouped_prices(payload, market="kg", currency="kgs")) == 1


@responses.activate
def test_fetch_grouped_prices_sends_group_by(session):
    responses.add(responses.GET, URL, json=GROUPED, status=200)
    aviasales.fetch_grouped_prices(
        session, "TOKEN", "FRU", "HKT", "kg", "kgs", "2026-10"
    )
    assert "group_by=departure_at" in responses.calls[0].request.url
    assert "departure_at=2026-10" in responses.calls[0].request.url


@responses.activate
def test_backfill_covers_routes_and_markets(session, config_stub):
    responses.add(responses.GET, URL, json=GROUPED, status=200)
    records, errors = aviasales.backfill(session, "TOKEN", config_stub)
    assert len(responses.calls) == 8  # 4 маршрута × 2 рынка
    assert errors == []
    assert len(records) > 0


@responses.activate
def test_backfill_survives_errors(session, config_stub):
    responses.add(responses.GET, URL, json={"success": False, "error": "nope"}, status=200)
    records, errors = aviasales.backfill(session, "TOKEN", config_stub)
    assert records == []
    assert len(errors) == 8
