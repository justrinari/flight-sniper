import pytest
import requests
import responses

from src.sources import aviasales

URL = f"{aviasales.BASE_URL}/prices_for_dates"

PAYLOAD = {
    "success": True,
    "data": [
        {
            "origin": "FRU",
            "destination": "HKT",
            "price": 38000,
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
def session():
    return requests.Session()


@responses.activate
def test_fetch_sends_expected_query_params(session):
    responses.add(responses.GET, URL, json=PAYLOAD, status=200)
    aviasales.fetch_prices_for_dates(
        session,
        token="TOKEN",
        origin="FRU",
        destination="HKT",
        market="kg",
        currency="kgs",
        departure_at="2026-10",
        return_at="2026-10",
    )
    request = responses.calls[0].request
    assert "origin=FRU" in request.url
    assert "destination=HKT" in request.url
    assert "departure_at=2026-10" in request.url
    assert "return_at=2026-10" in request.url
    assert "currency=kgs" in request.url
    assert "market=kg" in request.url
    assert "one_way=false" in request.url
    assert "limit=100" in request.url
    assert "token=" not in request.url
    assert request.headers["X-Access-Token"] == "TOKEN"


@responses.activate
def test_fetch_returns_parsed_records(session):
    responses.add(responses.GET, URL, json=PAYLOAD, status=200)
    records = aviasales.fetch_prices_for_dates(
        session, "TOKEN", "FRU", "HKT", "kg", "kgs", "2026-10", "2026-10"
    )
    assert len(records) == 1
    assert records[0].market == "kg"


@responses.activate
def test_fetch_raises_on_http_error(session):
    responses.add(responses.GET, URL, json={}, status=429)
    with pytest.raises(aviasales.AviasalesError):
        aviasales.fetch_prices_for_dates(
            session, "TOKEN", "FRU", "HKT", "kg", "kgs", "2026-10", "2026-10"
        )


@responses.activate
def test_fetch_raises_when_api_reports_failure(session):
    responses.add(
        responses.GET, URL, json={"success": False, "error": "bad token"}, status=200
    )
    with pytest.raises(aviasales.AviasalesError, match="bad token"):
        aviasales.fetch_prices_for_dates(
            session, "TOKEN", "FRU", "HKT", "kg", "kgs", "2026-10", "2026-10"
        )


@responses.activate
def test_token_never_appears_in_error_text(session):
    responses.add(responses.GET, URL, json={}, status=429)
    with pytest.raises(aviasales.AviasalesError) as excinfo:
        aviasales.fetch_prices_for_dates(
            session, "SECRET_TOKEN", "FRU", "HKT", "kg", "kgs", "2026-10", "2026-10"
        )
    assert "SECRET_TOKEN" not in str(excinfo.value)


@responses.activate
def test_scan_all_covers_routes_markets_and_return_months(session, config_stub):
    responses.add(responses.GET, URL, json=PAYLOAD, status=200)
    records, errors = aviasales.scan_all(session, "TOKEN", config_stub)
    # 4 маршрута × 2 рынка × 2 месяца возврата
    assert len(responses.calls) == 16
    assert errors == []
    assert len(records) > 0


@responses.activate
def test_scan_all_survives_single_route_failure(session, config_stub):
    responses.add(responses.GET, URL, json={"success": False, "error": "boom"}, status=200)
    records, errors = aviasales.scan_all(session, "TOKEN", config_stub)
    assert records == []
    assert len(errors) == 16


@responses.activate
def test_scan_all_deduplicates_identical_offers_within_run(session, config_stub):
    responses.add(responses.GET, URL, json=PAYLOAD, status=200)
    records, _ = aviasales.scan_all(session, "TOKEN", config_stub)
    keys = {
        (r.market, r.origin, r.destination, r.depart_date, r.return_date, r.airline, r.price_local)
        for r in records
    }
    assert len(keys) == len(records)
