from src.sources import aviasales

PAYLOAD = {
    "success": True,
    "data": [
        {
            "origin": "FRU",
            "destination": "HKT",
            "origin_airport": "FRU",
            "destination_airport": "HKT",
            "price": 38000,
            "airline": "KC",
            "flight_number": "123",
            "departure_at": "2026-10-12T10:15:00+06:00",
            "return_at": "2026-10-24T20:00:00+07:00",
            "transfers": 1,
            "return_transfers": 1,
            "duration": 1500,
            "duration_to": 760,
            "duration_back": 740,
            "link": "/search/FRU1210HKT2410?t=abc",
            "currency": "kgs",
        },
        {
            "origin": "FRU",
            "destination": "HKT",
            "price": 41000,
            "airline": "TK",
            "departure_at": "2026-10-15T02:00:00+06:00",
            "return_at": None,
            "transfers": 2,
            "duration": 2100,
            "duration_to": 1100,
            "link": "/search/FRU1510HKT?t=def",
            "currency": "kgs",
        },
    ],
}


def test_parses_all_records():
    records = aviasales.parse_prices_for_dates(PAYLOAD, market="kg")
    assert len(records) == 2


def test_maps_fields_onto_price_record():
    record = aviasales.parse_prices_for_dates(PAYLOAD, market="kg")[0]
    assert record.source == "aviasales_cache"
    assert record.origin == "FRU"
    assert record.destination == "HKT"
    assert record.market == "kg"
    assert record.depart_date == "2026-10-12"
    assert record.return_date == "2026-10-24"
    assert record.price_local == 38000.0
    assert record.currency == "kgs"
    assert record.airline == "KC"
    assert record.transfers == 1
    assert record.duration_min == 1500
    assert record.duration_to_min == 760


def test_builds_market_specific_absolute_link():
    record = aviasales.parse_prices_for_dates(PAYLOAD, market="kg")[0]
    assert record.search_url == "https://www.aviasales.kg/search/FRU1210HKT2410?t=abc"
    ru_record = aviasales.parse_prices_for_dates(PAYLOAD, market="ru")[0]
    assert ru_record.search_url.startswith("https://www.aviasales.ru/search/")


def test_unknown_market_falls_back_to_com_domain():
    record = aviasales.parse_prices_for_dates(PAYLOAD, market="tj")[0]
    assert record.search_url.startswith("https://www.aviasales.com/search/")


def test_missing_return_at_gives_one_way_record():
    record = aviasales.parse_prices_for_dates(PAYLOAD, market="kg")[1]
    assert record.return_date is None
    assert record.nights is None


def test_expires_at_is_captured_when_present():
    payload = {"data": [dict(PAYLOAD["data"][0], expires_at="2026-08-03T00:00:00Z")]}
    record = aviasales.parse_prices_for_dates(payload, market="kg")[0]
    assert record.expires_at == "2026-08-03T00:00:00Z"


def test_expires_at_is_none_when_api_omits_it():
    record = aviasales.parse_prices_for_dates(PAYLOAD, market="kg")[0]
    assert record.expires_at is None


def test_empty_data_yields_empty_list():
    assert aviasales.parse_prices_for_dates({"success": True, "data": []}, market="kg") == []


def test_missing_data_key_yields_empty_list():
    assert aviasales.parse_prices_for_dates({"success": False}, market="kg") == []


def test_malformed_entry_is_skipped_not_fatal():
    payload = {"data": [{"origin": "FRU"}, PAYLOAD["data"][0]]}
    records = aviasales.parse_prices_for_dates(payload, market="kg")
    assert len(records) == 1


def test_currency_falls_back_to_argument_when_absent():
    payload = {"data": [{k: v for k, v in PAYLOAD["data"][0].items() if k != "currency"}]}
    records = aviasales.parse_prices_for_dates(payload, market="kg", currency="kgs")
    assert records[0].currency == "kgs"


def test_invalid_departure_date_is_skipped():
    payload = {"data": [dict(PAYLOAD["data"][0], departure_at="2026-10-32T10:00:00+06:00")]}
    assert aviasales.parse_prices_for_dates(payload, market="kg") == []


def test_invalid_return_date_is_skipped():
    payload = {"data": [dict(PAYLOAD["data"][0], return_at="2026-13-01T10:00:00+06:00")]}
    assert aviasales.parse_prices_for_dates(payload, market="kg") == []


def test_valid_records_survive_alongside_broken_dates():
    payload = {
        "data": [
            dict(PAYLOAD["data"][0], departure_at="2026-10-32T10:00:00+06:00"),
            PAYLOAD["data"][0],
        ]
    }
    assert len(aviasales.parse_prices_for_dates(payload, market="kg")) == 1


def test_expires_at_unix_timestamp_is_normalized():
    payload = {"data": [dict(PAYLOAD["data"][0], expires_at=1785000000)]}
    record = aviasales.parse_prices_for_dates(payload, market="kg")[0]
    assert record.expires_at is not None
    assert record.expires_at.startswith("20")
    assert record.expires_at.endswith("Z")


def test_expires_at_unix_timestamp_as_string_is_normalized():
    payload = {"data": [dict(PAYLOAD["data"][0], expires_at="1785000000")]}
    record = aviasales.parse_prices_for_dates(payload, market="kg")[0]
    assert record.expires_at.endswith("Z")
    assert "T" in record.expires_at


def test_expires_at_iso_without_zone_gets_utc_marker():
    payload = {"data": [dict(PAYLOAD["data"][0], expires_at="2026-08-03T00:00:00")]}
    record = aviasales.parse_prices_for_dates(payload, market="kg")[0]
    assert record.expires_at == "2026-08-03T00:00:00Z"


def test_unparseable_expires_at_becomes_none():
    payload = {"data": [dict(PAYLOAD["data"][0], expires_at="скоро")]}
    assert aviasales.parse_prices_for_dates(payload, market="kg")[0].expires_at is None


def test_non_positive_price_is_skipped():
    payload = {"data": [dict(PAYLOAD["data"][0], price=0)]}
    assert aviasales.parse_prices_for_dates(payload, market="kg") == []


def test_zero_duration_to_becomes_none():
    payload = {"data": [dict(PAYLOAD["data"][0], duration_to=0)]}
    assert aviasales.parse_prices_for_dates(payload, market="kg")[0].duration_to_min is None
