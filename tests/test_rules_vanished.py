"""Оффер, пропавший из кэша, отличается от подорожания.

Наблюдение на живой базе за 02–10.08.2026: на 14, 15, 17 и 22 октября рейсы
AirAsia X на Пхукет стояли по $280–302 и выпали из выдачи Aviasales. Дайджест
после этого честно показывал $331–356 (Air China) как лучшую цену дня — и это
дезинформация: рейсы никуда не делись, пропала запись о них. Сильнее всего
разошлось 17 октября: было $280, показывается $356.
"""

import dataclasses

import pytest

from src import rules, store
from src.models import PriceRecord


@pytest.fixture()
def conn(tmp_path):
    connection = store.connect(tmp_path / "history.sqlite")
    store.init_schema(connection)
    return connection


@pytest.fixture()
def cfg(config_stub):
    return dataclasses.replace(
        config_stub,
        origins=["ALA"],
        destinations=["HKT"],
        markets=["kg"],
        market_currency={"kg": "kgs"},
        cache_ttl_hours=2,
        min_offer_lifetime_minutes=10.0,  # боевое значение: тут его и проверяем
    )


def _record(landed, depart, airline="D7"):
    return PriceRecord(
        source="aviasales_cache",
        origin="ALA",
        destination="HKT",
        market="kg",
        depart_date=depart,
        return_date=None,
        price_local=landed * 87.5,
        currency="kgs",
        airline=airline,
        transfers=1,
        duration_min=960,
        duration_to_min=960,
        search_url="https://x",
        fx_rate=1 / 87.5,
        landed_usd=landed,
        gate="Tickets.kz",
    )


def _seed(conn, landed, depart, now, airline="D7", lifetime_minutes=60.0):
    """Оффер, впервые увиденный в `now` и в последний раз — lifetime_minutes спустя.

    Время жизни задаётся явно, потому что от него зависит, считается ли оффер
    настоящим: увиденный в одном проходе и исчезнувший — это фантом кэша.
    """
    store.insert_prices(conn, [_record(landed, depart, airline)], now=now)
    conn.execute(
        "UPDATE price_history SET last_seen_at = ?"
        " WHERE depart_date = ? AND landed_usd = ?",
        (store.shift_hours(now, lifetime_minutes / 60.0), depart, landed),
    )
    conn.commit()


NOW = "2026-08-10T00:00:00Z"


def test_reports_date_whose_best_offer_left_the_cache(conn, cfg):
    _seed(conn, 280.0, "2026-10-17", now="2026-08-03T09:00:00Z")
    _seed(conn, 356.0, "2026-10-17", now=NOW, airline="CA")

    gone = rules.vanished_offers(conn, cfg, now=NOW)

    assert len(gone) == 1
    assert gone[0].depart_date == "2026-10-17"
    assert gone[0].gone_landed_usd == 280.0
    assert gone[0].gone_airline == "D7"
    assert gone[0].current_landed_usd == 356.0
    assert gone[0].gone_last_seen_at == "2026-08-03T10:00:00Z"


def test_gap_ratio_is_how_much_worse_it_got(conn, cfg):
    _seed(conn, 200.0, "2026-10-17", now="2026-08-03T09:00:00Z")
    _seed(conn, 250.0, "2026-10-17", now=NOW, airline="CA")

    gone = rules.vanished_offers(conn, cfg, now=NOW)

    assert gone[0].gap_ratio == pytest.approx(0.25)


def test_small_gap_is_not_worth_reporting(conn, cfg):
    _seed(conn, 300.0, "2026-10-17", now="2026-08-03T09:00:00Z")
    _seed(conn, 310.0, "2026-10-17", now=NOW, airline="CA")  # +3.3%, ниже 7%

    assert rules.vanished_offers(conn, cfg, now=NOW) == []


def test_still_fresh_best_offer_is_not_vanished(conn, cfg):
    _seed(conn, 280.0, "2026-10-17", now="2026-08-09T23:30:00Z")  # внутри TTL
    _seed(conn, 356.0, "2026-10-17", now=NOW, airline="CA")

    assert rules.vanished_offers(conn, cfg, now=NOW) == []


def test_date_without_any_fresh_price_is_not_reported_here(conn, cfg):
    """Полное отсутствие данных — отдельный сюжет, у дайджеста для него своя строка."""
    _seed(conn, 280.0, "2026-10-17", now="2026-08-03T09:00:00Z")

    assert rules.vanished_offers(conn, cfg, now=NOW) == []


def test_offer_older_than_baseline_window_is_forgotten(conn, cfg):
    window = dataclasses.replace(cfg, baseline_window_days=3)
    _seed(conn, 280.0, "2026-10-17", now="2026-08-03T09:00:00Z")
    _seed(conn, 356.0, "2026-10-17", now=NOW, airline="CA")

    assert rules.vanished_offers(conn, window, now=NOW) == []


def test_sorted_by_gap_descending(conn, cfg):
    _seed(conn, 280.0, "2026-10-17", now="2026-08-03T09:00:00Z")
    _seed(conn, 356.0, "2026-10-17", now=NOW, airline="CA")
    _seed(conn, 300.0, "2026-10-22", now="2026-08-03T09:00:00Z")
    _seed(conn, 330.0, "2026-10-22", now=NOW, airline="CA")

    gone = rules.vanished_offers(conn, cfg, now=NOW)

    assert [g.depart_date for g in gone] == ["2026-10-17", "2026-10-22"]


def test_offer_seen_in_a_single_pass_is_not_a_loss(conn, cfg):
    """Фантом кэша не «пропал» — его никогда и не было.

    На живой базе верхней строкой блока шёл оффер ALA-HKT на 06.10 за $171,
    существовавший 3 секунды в одном проходе бэкфилла 02.08.2026. Блок,
    затеянный против дезинформации, начинался бы с самой яркой из них.
    """
    _seed(conn, 171.0, "2026-10-06", now="2026-08-03T09:00:00Z", lifetime_minutes=0.05)
    _seed(conn, 282.0, "2026-10-06", now=NOW, airline="CA")

    assert rules.vanished_offers(conn, cfg, now=NOW) == []


def test_offer_that_survived_several_passes_still_counts(conn, cfg):
    """Два прохода скана — это 14 минут, порог ниже с запасом."""
    _seed(conn, 280.0, "2026-10-17", now="2026-08-03T09:00:00Z", lifetime_minutes=14)
    _seed(conn, 356.0, "2026-10-17", now=NOW, airline="CA")

    gone = rules.vanished_offers(conn, cfg, now=NOW)

    assert [g.depart_date for g in gone] == ["2026-10-17"]


def test_best_fresh_for_date_returns_cheapest_live_price(conn, cfg):
    _seed(conn, 320.0, "2026-10-17", now="2026-08-09T23:00:00Z", lifetime_minutes=30)
    _seed(conn, 281.0, "2026-10-17", now="2026-08-09T23:00:00Z",
          airline="CA", lifetime_minutes=30)

    price = rules.best_fresh_for_date(conn, cfg, "ALA", "HKT", "2026-10-17", now=NOW)

    assert price == 281.0


def test_best_fresh_for_date_is_none_without_live_prices(conn, cfg):
    _seed(conn, 281.0, "2026-10-17", now="2026-08-01T09:00:00Z", lifetime_minutes=30)

    assert rules.best_fresh_for_date(conn, cfg, "ALA", "HKT", "2026-10-17", now=NOW) is None
