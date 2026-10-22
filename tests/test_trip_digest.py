import pytest

from src import store, trip_digest
from src.models import PriceRecord

NOW = "2026-10-22T01:00:00Z"


def _record(origin, destination, day, usd, airline="AK", transfers=0):
    return PriceRecord(
        source="aviasales_cache",
        origin=origin,
        destination=destination,
        market="ru",
        depart_date=day,
        return_date=None,
        price_local=round(usd * 92, 2),
        currency="rub",
        airline=airline,
        transfers=transfers,
        duration_min=300,
        search_url=f"https://www.aviasales.ru/search/{origin}{destination}",
        fx_rate=1 / 92,
        landed_usd=usd,
    )


BEST = [
    _record("DPS", "HKT", "2027-01-18", 86, transfers=1),
    _record("HKT", "SGN", "2027-01-23", 101, airline="VJ"),
    _record("SGN", "MNL", "2027-01-28", 117, airline="TR", transfers=1),
    _record("MNL", "DPS", "2027-01-31", 102, airline="5J"),
]


@pytest.fixture()
def conn(tmp_path):
    connection = store.connect(tmp_path / "history.sqlite")
    store.init_schema(connection)
    store.set_meta(connection, "last_scan_at", NOW)
    yield connection
    connection.close()


def test_digest_shows_best_itinerary(conn, trip_config):
    store.insert_prices(conn, BEST, now=NOW)
    result = trip_digest.build_text(conn, trip_config, now=NOW)
    assert result.best_total == 406
    assert "✈️ Бали → Пхукет → Вьетнам → Манила → Бали" in result.text
    assert "1. <b>$406</b> за взрослого" in result.text
    assert "18.01 Бали → Пхукет — $86 AirAsia, 1 пересадка" in result.text
    assert "23.01 Пхукет → Хошимин — $101 VietJet, без пересадок" in result.text
    assert "31.01 Манила → Бали — $102 Cebu Pacific, без пересадок" in result.text
    assert "13 дн." in result.text
    assert 'href="https://www.aviasales.ru/search/DPSHKT"' in result.text
    assert trip_digest.DISCLAIMER in result.text
    assert "🚨" not in result.text


def test_digest_compares_with_previous_best(conn, trip_config):
    store.insert_prices(conn, BEST, now=NOW)
    store.set_meta(conn, trip_digest.PREV_KEY, "412")
    result = trip_digest.build_text(conn, trip_config, now=NOW)
    assert "(прошлая сводка $412, −1%)" in result.text


def test_digest_without_itineraries_names_empty_stages(conn, trip_config):
    store.insert_prices(conn, BEST[:2], now=NOW)
    result = trip_digest.build_text(conn, trip_config, now=NOW)
    assert result.best_total is None
    assert "Подходящих маршрутов в кэше сейчас нет." in result.text
    assert "Вьетнам → Манила" in result.text
    assert "Манила → Бали" in result.text


def test_past_departures_are_ignored(conn, trip_config):
    past = [_record("DPS", "HKT", "2026-10-21", 10)]
    store.insert_prices(conn, past + BEST[1:], now=NOW)
    result = trip_digest.build_text(conn, trip_config, now=NOW)
    assert result.best_total is None


def test_alert_text(conn, trip_config):
    store.insert_prices(conn, BEST, now=NOW)
    itineraries, _ = trip_digest.load_itineraries(conn, trip_config, NOW)
    text = trip_digest.render_alert(itineraries[0], 450, NOW)
    assert text.startswith("🟢 Маршрут дешевле $450")
    assert "1. <b>$406</b> за взрослого" in text


def test_header_shows_departure_window(conn, trip_config):
    import dataclasses

    cfg = dataclasses.replace(
        trip_config, trip=dataclasses.replace(trip_config.trip, earliest_start="2027-01-15")
    )
    result = trip_digest.build_text(conn, cfg, now=NOW)
    assert "Вылет 15.01–22.01" in result.text


def test_missing_transfers_leave_no_dangling_comma(conn, trip_config):
    records = list(BEST)
    records[1] = _record("HKT", "SGN", "2027-01-23", 101, airline="VJ", transfers=None)
    store.insert_prices(conn, records, now=NOW)
    text = trip_digest.build_text(conn, trip_config, now=NOW).text
    assert "23.01 Пхукет → Хошимин — $101 VietJet\n" in text
