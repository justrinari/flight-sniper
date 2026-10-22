import textwrap

import pytest

from src.config import Config, TripConfig

BASE = """
origins: []
destinations: []
markets: [kg, ru]
market_currency:
  kg: kgs
  ru: rub
fx_markup:
  default: 0.025
  usd: 0.0
departure_months: ["2027-01", "2027-02"]
return_months: ["2027-01"]
trip_type: one_way
nights_range: [10, 16]
max_transfer_hours: 12
abs_threshold_usd: 250
anomaly_percentile: 10
yellow_delta: 0.15
"""

TRIP = """
trip:
  stops:
    - [DPS]
    - [HKT]
    - [SGN, DAD]
    - [MNL]
    - [DPS]
  latest_start: "2027-01-22"
  max_total_days: 14
  min_stay_days: 3
  alert_total_usd: 380
"""


def _load(tmp_path, text):
    path = tmp_path / "config.yaml"
    path.write_text(textwrap.dedent(text), encoding="utf-8")
    return Config.load(path)


def test_trip_section_is_parsed(tmp_path):
    cfg = _load(tmp_path, BASE + TRIP)
    assert cfg.trip == TripConfig(
        stops=(("DPS",), ("HKT",), ("SGN", "DAD"), ("MNL",), ("DPS",)),
        latest_start="2027-01-22",
        max_total_days=14,
        min_stay_days=3,
        alert_total_usd=380.0,
        top_n=3,
    )


def test_routes_come_from_trip_stops(tmp_path):
    cfg = _load(tmp_path, BASE + TRIP)
    assert cfg.routes() == [
        ("DPS", "HKT"),
        ("HKT", "SGN"),
        ("HKT", "DAD"),
        ("SGN", "MNL"),
        ("DAD", "MNL"),
        ("MNL", "DPS"),
    ]


def test_without_trip_routes_are_origins_by_destinations(tmp_path):
    text = BASE.replace("origins: []", "origins: [ALA]").replace(
        "destinations: []", "destinations: [HKT, DPS]"
    )
    cfg = _load(tmp_path, text)
    assert cfg.trip is None
    assert cfg.routes() == [("ALA", "HKT"), ("ALA", "DPS")]


def test_unquoted_yaml_date_is_accepted(tmp_path):
    cfg = _load(tmp_path, BASE + TRIP.replace('"2027-01-22"', "2027-01-22"))
    assert cfg.trip.latest_start == "2027-01-22"


def test_single_stop_is_rejected(tmp_path):
    text = BASE + TRIP.replace(
        "    - [HKT]\n    - [SGN, DAD]\n    - [MNL]\n    - [DPS]\n", ""
    )
    with pytest.raises(ValueError, match="минимум две остановки"):
        _load(tmp_path, text)


def test_empty_stop_is_rejected(tmp_path):
    with pytest.raises(ValueError, match=r"trip.stops\[1\]"):
        _load(tmp_path, BASE + TRIP.replace("- [HKT]", "- []"))


def test_min_stay_below_one_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="min_stay_days"):
        _load(tmp_path, BASE + TRIP.replace("min_stay_days: 3", "min_stay_days: 0"))


def test_bad_latest_start_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="latest_start"):
        _load(tmp_path, BASE + TRIP.replace('"2027-01-22"', '"22.01.2027"'))


def test_earliest_start_is_optional_and_parsed(tmp_path):
    assert _load(tmp_path, BASE + TRIP).trip.earliest_start is None
    text = BASE + TRIP + '  earliest_start: "2027-01-15"\n'
    assert _load(tmp_path, text).trip.earliest_start == "2027-01-15"


def test_earliest_start_after_latest_start_is_rejected(tmp_path):
    text = BASE + TRIP + '  earliest_start: "2027-01-23"\n'
    with pytest.raises(ValueError, match="earliest_start"):
        _load(tmp_path, text)
