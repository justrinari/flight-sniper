import dataclasses

from src.models import PriceRecord


def make_record(**kwargs) -> PriceRecord:
    base = dict(
        source="aviasales_cache",
        origin="FRU",
        destination="HKT",
        market="kg",
        depart_date="2026-10-12",
        return_date="2026-10-24",
        price_local=38000.0,
        currency="kgs",
        airline="KC",
        transfers=1,
        duration_min=1500,
        duration_to_min=760,
        search_url="https://www.aviasales.kg/search/FRU1210HKT2410",
    )
    base.update(kwargs)
    return PriceRecord(**base)


def test_record_is_frozen():
    record = make_record()
    try:
        record.price_local = 1.0
    except dataclasses.FrozenInstanceError:
        return
    raise AssertionError("PriceRecord должен быть неизменяемым")


def test_nights_counts_days_between_dates():
    assert make_record().nights == 12


def test_nights_is_none_for_one_way():
    assert make_record(return_date=None).nights is None


def test_route_key_identifies_route_and_dates():
    assert make_record().route_key == "FRU-HKT-2026-10-12-2026-10-24"


def test_route_key_for_one_way_omits_return():
    assert make_record(return_date=None).route_key == "FRU-HKT-2026-10-12-"


def test_with_landed_returns_new_record():
    record = make_record()
    enriched = record.with_landed(fx_rate=0.0114, landed_usd=444.0)
    assert enriched.landed_usd == 444.0
    assert enriched.fx_rate == 0.0114
    assert record.landed_usd is None
