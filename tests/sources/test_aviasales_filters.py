from src.models import PriceRecord
from src.sources import aviasales


def make(depart="2026-10-12", ret="2026-10-24", duration_to=760, transfers=1, price=38000.0):
    return PriceRecord(
        source="aviasales_cache",
        origin="FRU",
        destination="HKT",
        market="kg",
        depart_date=depart,
        return_date=ret,
        price_local=price,
        currency="kgs",
        airline="KC",
        transfers=transfers,
        duration_min=(duration_to or 0) * 2,
        duration_to_min=duration_to,
    )


def test_keeps_records_inside_nights_range():
    kept = aviasales.filter_by_nights([make(ret="2026-10-24")], (10, 16))
    assert len(kept) == 1


def test_drops_too_short_trips():
    assert aviasales.filter_by_nights([make(ret="2026-10-15")], (10, 16)) == []


def test_drops_too_long_trips():
    assert aviasales.filter_by_nights([make(ret="2026-11-05")], (10, 16)) == []


def test_boundaries_are_inclusive():
    assert len(aviasales.filter_by_nights([make(ret="2026-10-22")], (10, 16))) == 1
    assert len(aviasales.filter_by_nights([make(ret="2026-10-28")], (10, 16))) == 1


def test_one_way_records_survive_nights_filter():
    assert len(aviasales.filter_by_nights([make(ret=None)], (10, 16))) == 1


def test_departure_month_filter_keeps_only_target_month():
    records = [make(depart="2026-10-12"), make(depart="2026-09-30"), make(depart="2026-11-01")]
    kept = aviasales.filter_by_months(records, ["2026-10"])
    assert [r.depart_date for r in kept] == ["2026-10-12"]


def test_departure_months_filter_keeps_several_months():
    records = [make(depart="2026-10-12"), make(depart="2026-11-01"), make(depart="2026-09-30")]
    kept = aviasales.filter_by_months(records, ["2026-10", "2026-11"])
    assert [r.depart_date for r in kept] == ["2026-10-12", "2026-11-01"]


def test_transfer_filter_drops_legs_longer_than_shortest_plus_budget():
    records = [
        make(duration_to=600, transfers=0),  # эталон
        make(duration_to=900, transfers=1),  # +5 ч — оставляем
        make(duration_to=1400, transfers=2),  # +13.3 ч — режем при бюджете 12 ч
    ]
    kept = aviasales.filter_by_transfer_time(records, max_transfer_hours=12)
    assert [r.duration_to_min for r in kept] == [600, 900]


def test_transfer_filter_keeps_records_without_duration_to():
    records = [make(duration_to=None), make(duration_to=600)]
    kept = aviasales.filter_by_transfer_time(records, max_transfer_hours=12)
    assert len(kept) == 2


def test_transfer_filter_is_per_route():
    records = [
        make(duration_to=600),
        PriceRecord(
            source="aviasales_cache",
            origin="ALA",
            destination="DPS",
            market="kg",
            depart_date="2026-10-12",
            return_date="2026-10-24",
            price_local=50000.0,
            currency="kgs",
            airline="KC",
            transfers=1,
            duration_min=2000,
            duration_to_min=1300,
        ),
    ]
    kept = aviasales.filter_by_transfer_time(records, max_transfer_hours=12)
    assert len(kept) == 2  # 1300 — единственный на своём маршруте, значит эталон


def test_filter_records_drops_one_way_when_round_trip_requested(config_stub):
    records = [make(ret=None, price=15000.0), make(ret="2026-10-24", price=38000.0)]
    kept = aviasales.filter_records(records, config_stub)
    assert [r.price_local for r in kept] == [38000.0]


def test_filter_records_keeps_one_way_in_one_way_mode(config_stub):
    import dataclasses

    cfg = dataclasses.replace(config_stub, trip_type="one_way")
    records = [make(ret=None, price=15000.0)]
    assert len(aviasales.filter_records(records, cfg)) == 1


def test_filter_records_applies_all_three(config_stub):
    records = [
        make(depart="2026-10-12", ret="2026-10-24", duration_to=600),  # ок
        make(depart="2026-09-12", ret="2026-09-24", duration_to=600),  # не тот месяц
        make(depart="2026-10-12", ret="2026-10-14", duration_to=600),  # мало ночей
        make(depart="2026-10-12", ret="2026-10-24", duration_to=1400),  # длинная пересадка
    ]
    kept = aviasales.filter_records(records, config_stub)
    assert len(kept) == 1
    assert kept[0].duration_to_min == 600
