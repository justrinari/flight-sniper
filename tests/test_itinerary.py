from src import itinerary
from src.config import TripConfig
from src.itinerary import Leg

TRIP = TripConfig(
    stops=(("DPS",), ("HKT",), ("SGN", "DAD"), ("MNL",), ("DPS",)),
    latest_start="2027-01-22",
    max_total_days=14,
    min_stay_days=3,
    alert_total_usd=380.0,
)


def _leg(origin, destination, day, usd, airline="AK", depart_at=None, arrive_at=None):
    return Leg(
        origin=origin,
        destination=destination,
        depart_date=day,
        landed_usd=usd,
        airline=airline,
        transfers=0,
        market="ru",
        currency="rub",
        search_url=f"https://www.aviasales.ru/search/{origin}{destination}",
        last_seen_at="2026-10-22T00:00:00Z",
        duration_min=300,
        depart_at=depart_at,
        arrive_at=arrive_at,
    )


def _group(*legs):
    grouped = {}
    for leg in legs:
        grouped.setdefault((leg.origin, leg.destination), []).append(leg)
    return grouped


BASE_LEGS = (
    _leg("DPS", "HKT", "2027-01-18", 86),
    _leg("HKT", "SGN", "2027-01-23", 101),
    _leg("SGN", "MNL", "2027-01-28", 117),
    _leg("MNL", "DPS", "2027-01-31", 102),
)


def test_builds_the_only_valid_itinerary():
    result = itinerary.build_itineraries(_group(*BASE_LEGS), TRIP)
    assert len(result) == 1
    assert result[0].total_usd == 406
    assert result[0].airports == ("HKT", "SGN", "MNL", "DPS")
    assert result[0].days == 13


def test_start_after_latest_start_is_rejected():
    legs = (_leg("DPS", "HKT", "2027-01-23", 86),) + BASE_LEGS[1:]
    assert itinerary.build_itineraries(_group(*legs), TRIP) == []


def test_stay_shorter_than_minimum_is_rejected():
    legs = (BASE_LEGS[0], _leg("HKT", "SGN", "2027-01-20", 101)) + BASE_LEGS[2:]
    assert itinerary.build_itineraries(_group(*legs), TRIP) == []


def test_trip_longer_than_maximum_is_rejected():
    legs = BASE_LEGS[:3] + (_leg("MNL", "DPS", "2027-02-02", 102),)
    assert itinerary.build_itineraries(_group(*legs), TRIP) == []


def test_trip_of_exactly_maximum_is_accepted():
    legs = BASE_LEGS[:3] + (_leg("MNL", "DPS", "2027-02-01", 102),)
    assert len(itinerary.build_itineraries(_group(*legs), TRIP)) == 1


def test_legs_must_connect_through_the_same_city():
    legs = BASE_LEGS[:2] + (_leg("DAD", "MNL", "2027-01-28", 117), BASE_LEGS[3])
    assert itinerary.build_itineraries(_group(*legs), TRIP) == []


def test_itineraries_are_sorted_by_total():
    legs = BASE_LEGS + (
        _leg("HKT", "DAD", "2027-01-23", 80),
        _leg("DAD", "MNL", "2027-01-28", 200),
    )
    result = itinerary.build_itineraries(_group(*legs), TRIP)
    assert [r.total_usd for r in result] == [406, 468]


def test_cheapest_legs_skips_unpriced_rows_and_sorts_by_date_then_price():
    rows = [
        {**_leg("DPS", "HKT", "2027-01-20", 90).__dict__},
        {**_leg("DPS", "HKT", "2027-01-18", 95).__dict__},
        {**_leg("DPS", "HKT", "2027-01-18", 86, airline="FD").__dict__},
        {**_leg("DPS", "HKT", "2027-01-19", 70).__dict__, "landed_usd": None},
    ]
    grouped = itinerary.cheapest_legs(rows)
    legs = grouped[("DPS", "HKT")]
    assert [(leg.depart_date, leg.landed_usd, leg.airline) for leg in legs] == [
        ("2027-01-18", 86, "FD"),
        ("2027-01-18", 95, "AK"),
        ("2027-01-20", 90, "AK"),
    ]


def test_top_distinct_skips_same_city_set():
    legs = BASE_LEGS + (
        _leg("DPS", "HKT", "2027-01-17", 90),
        _leg("HKT", "DAD", "2027-01-23", 110),
        _leg("DAD", "MNL", "2027-01-28", 120),
    )
    result = itinerary.build_itineraries(_group(*legs), TRIP)
    top = itinerary.top_distinct(result, 3)
    assert [t.airports for t in top] == [
        ("HKT", "SGN", "MNL", "DPS"),
        ("HKT", "DAD", "MNL", "DPS"),
    ]
    assert top[0].total_usd == 406


def test_start_before_earliest_start_is_rejected():
    import dataclasses

    trip = dataclasses.replace(TRIP, earliest_start="2027-01-19")
    assert itinerary.build_itineraries(_group(*BASE_LEGS), trip) == []
    trip = dataclasses.replace(TRIP, earliest_start="2027-01-18")
    assert len(itinerary.build_itineraries(_group(*BASE_LEGS), trip)) == 1


def test_cheapest_legs_keeps_duration_and_itinerary_sums_it():
    rows = [{**leg.__dict__} for leg in BASE_LEGS]
    rows[0]["duration_min"] = 920
    legs = itinerary.cheapest_legs(rows)
    assert legs[("DPS", "HKT")][0].duration_min == 920
    result = itinerary.build_itineraries(legs, TRIP)
    assert result[0].travel_minutes == 920 + 300 * 3


def test_layover_is_total_minus_air_time():
    rows = [{**leg.__dict__} for leg in BASE_LEGS]
    rows[0].update(duration_min=920, duration_to_min=285)
    legs = itinerary.cheapest_legs(rows)
    leg = legs[("DPS", "HKT")][0]
    assert leg.layover_min == 635
    assert legs[("HKT", "SGN")][0].layover_min == 0
    result = itinerary.build_itineraries(legs, TRIP)
    assert result[0].layover_minutes == 635


def _at(text):
    from datetime import datetime

    return datetime.fromisoformat(text)


def test_stay_is_counted_from_arrival_not_departure_date():
    # Вылет 28.01 днём, прилёт в Манилу 29.01 вечером, дальше — 31.01 ночью:
    # по датам вылета это 3 дня, на деле в стране 1 сутки 9 часов.
    late = _leg("SGN", "MNL", "2027-01-28", 117,
                depart_at=_at("2027-01-28T15:45"), arrive_at=_at("2027-01-29T18:30"))
    red_eye = _leg("MNL", "DPS", "2027-01-31", 102,
                   depart_at=_at("2027-01-31T03:45"), arrive_at=_at("2027-01-31T07:50"))
    legs = BASE_LEGS[:2] + (late, red_eye)
    assert itinerary.build_itineraries(_group(*legs), TRIP) == []


def test_stay_of_full_days_after_arrival_is_accepted():
    late = _leg("SGN", "MNL", "2027-01-28", 117,
                depart_at=_at("2027-01-28T15:45"), arrive_at=_at("2027-01-29T18:30"))
    later = _leg("MNL", "DPS", "2027-02-01", 102,
                 depart_at=_at("2027-02-01T20:00"), arrive_at=_at("2027-02-02T00:05"))
    legs = BASE_LEGS[:2] + (late, later)
    assert len(itinerary.build_itineraries(_group(*legs), TRIP)) == 1


def test_cheapest_legs_keeps_a_few_offers_per_date():
    rows = [
        {**_leg("DPS", "HKT", "2027-01-18", usd).__dict__}
        for usd in (99, 86, 95, 90)
    ]
    legs = itinerary.cheapest_legs(rows)[("DPS", "HKT")]
    assert [leg.landed_usd for leg in legs] == [86, 90, 95]
