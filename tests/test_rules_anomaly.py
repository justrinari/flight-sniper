import pytest

from src import rules, store
from src.models import PriceRecord

KG_FX = 0.0114
RU_FX = 0.0105


@pytest.fixture()
def conn(tmp_path):
    connection = store.connect(tmp_path / "history.sqlite")
    store.init_schema(connection)
    return connection


def _fx(market: str) -> float:
    return KG_FX if market == "kg" else RU_FX


def record(
    price_local,
    market="kg",
    currency=None,
    depart="2026-10-12",
    return_date="2026-10-24",
    origin="FRU",
    destination="HKT",
    airline="KC",
    transfers=1,
    gate="Trip.com",
    search_url="https://www.aviasales.kg/search/x",
) -> PriceRecord:
    currency = currency or ("kgs" if market == "kg" else "rub")
    fx = _fx(market)
    return PriceRecord(
        source="aviasales_cache",
        origin=origin,
        destination=destination,
        market=market,
        depart_date=depart,
        return_date=return_date,
        price_local=price_local,
        currency=currency,
        airline=airline,
        transfers=transfers,
        duration_min=1500,
        duration_to_min=760,
        search_url=search_url,
        fx_rate=fx,
        landed_usd=round(price_local * fx, 4),
        gate=gate,
    )


def seed(conn, prices, now, **kwargs):
    store.insert_prices(conn, [record(p, **kwargs) for p in prices], now=now)


def test_no_candidates_when_history_below_min_sample(conn, config_stub):
    # "сегодня" тоже становится одним из дней окна, поэтому 3 дня истории + 1
    # сегодняшний = 4 дня < MIN_SAMPLE(5)
    for day, price in enumerate([300.0, 305.0, 310.0], start=20):
        seed(conn, [price], now=f"2026-07-{day}T06:00:00Z")
    seed(conn, [150.0], now="2026-08-01T06:00:00Z")  # была бы аномалией, будь baseline
    candidates = rules.find_candidates(conn, config_stub, now="2026-08-01T07:00:00Z")
    assert candidates == []


def test_candidate_found_when_today_below_p10(conn, config_stub):
    for day, price in enumerate([300.0, 305.0, 310.0, 315.0, 320.0], start=20):
        seed(conn, [price], now=f"2026-07-{day}T06:00:00Z")
    seed(conn, [200.0], now="2026-08-01T06:00:00Z", gate="Wingie", search_url="https://x/cheap")
    candidates = rules.find_candidates(conn, config_stub, now="2026-08-01T07:00:00Z")
    assert len(candidates) == 1
    c = candidates[0]
    assert c.origin == "FRU"
    assert c.destination == "HKT"
    assert c.market == "kg"
    assert c.price_local == 200.0
    assert c.depart_date == "2026-10-12"
    assert c.return_date == "2026-10-24"
    assert c.gate == "Wingie"
    assert c.route_date_key == "FRU-HKT-2026-10-12-2026-10-24"
    assert c.baseline is not None
    assert c.baseline.n == 6  # 5 дней истории + сегодняшний день


def test_candidate_includes_last_seen_at_of_selected_row(conn, config_stub):
    for day, price in enumerate([300.0, 305.0, 310.0, 315.0, 320.0], start=20):
        seed(conn, [price], now=f"2026-07-{day}T06:00:00Z")
    seed(conn, [200.0], now="2026-08-01T06:00:00Z")
    candidates = rules.find_candidates(conn, config_stub, now="2026-08-01T07:00:00Z")
    assert len(candidates) == 1
    assert candidates[0].last_seen_at == "2026-08-01T06:00:00Z"


def test_no_candidate_for_ordinary_price(conn, config_stub):
    for day, price in enumerate([300.0, 305.0, 310.0, 315.0, 320.0], start=20):
        seed(conn, [price], now=f"2026-07-{day}T06:00:00Z")
    seed(conn, [310.0], now="2026-08-01T06:00:00Z")  # обычная цена, выше p10
    candidates = rules.find_candidates(conn, config_stub, now="2026-08-01T07:00:00Z")
    assert candidates == []


def test_stale_cheap_offer_is_not_a_candidate(conn, config_stub):
    for day, price in enumerate([300.0, 305.0, 310.0, 315.0, 320.0], start=20):
        seed(conn, [price], now=f"2026-07-{day}T06:00:00Z")
    # протухшая дешёвая запись: старше cache_ttl_hours (48ч) от "now"
    seed(conn, [150.0], now="2026-07-29T06:00:00Z")
    # свежая, но обычная цена — единственное, что должно попасть в выборку "сегодня"
    seed(conn, [318.0], now="2026-08-01T06:00:00Z")
    candidates = rules.find_candidates(conn, config_stub, now="2026-08-01T07:00:00Z")
    assert candidates == []


def test_markets_are_independent(conn, config_stub):
    kg_history = [300.0, 305.0, 310.0, 315.0, 320.0]
    ru_history = [3000.0, 3050.0, 3100.0, 3150.0, 3200.0]
    for day, (kg_price, ru_price) in enumerate(zip(kg_history, ru_history), start=20):
        seed(conn, [kg_price], now=f"2026-07-{day}T06:00:00Z", market="kg")
        seed(conn, [ru_price], now=f"2026-07-{day}T06:00:00Z", market="ru")
    seed(conn, [310.0], now="2026-08-01T06:00:00Z", market="kg")  # обычная цена
    seed(conn, [2000.0], now="2026-08-01T06:00:00Z", market="ru")  # аномально дёшево

    candidates = rules.find_candidates(conn, config_stub, now="2026-08-01T07:00:00Z")
    assert len(candidates) == 1
    assert candidates[0].market == "ru"


def test_no_candidate_when_history_is_single_day_of_many_offers(conn, config_stub):
    """Регрессия: baseline не должен строиться по сырым офферам вместо дневных минимумов.

    День 20 даёт шесть офферов, кучно расположенных около 200. Если бы они
    использовались как есть (без группировки по дню), сэмпл достиг бы
    MIN_SAMPLE и p10 у него оказался бы лишь чуть ниже 200 — сегодняшняя
    цена 190 сошла бы за "аномалию". Но дней наблюдения всего два
    (день 20 и сегодня), а MIN_SAMPLE=5 — значит, кандидатов быть не должно.
    """
    seed(conn, [200.0, 205.0, 210.0, 215.0, 220.0, 225.0], now="2026-07-20T06:00:00Z")
    seed(conn, [190.0], now="2026-08-01T06:00:00Z")
    candidates = rules.find_candidates(conn, config_stub, now="2026-08-01T07:00:00Z")
    assert candidates == []


def test_candidates_sorted_by_landed_usd_ascending(conn, config_stub):
    history = [300.0, 305.0, 310.0, 315.0, 320.0]
    for day, price in enumerate(history, start=20):
        seed(conn, [price], now=f"2026-07-{day}T06:00:00Z", destination="HKT")
        seed(conn, [price], now=f"2026-07-{day}T06:00:00Z", destination="DPS")
    seed(conn, [220.0], now="2026-08-01T06:00:00Z", destination="HKT")
    seed(conn, [150.0], now="2026-08-01T06:00:00Z", destination="DPS")
    candidates = rules.find_candidates(conn, config_stub, now="2026-08-01T07:00:00Z")
    assert len(candidates) == 2
    assert candidates[0].destination == "DPS"
    assert candidates[0].landed_usd < candidates[1].landed_usd


def test_nights_text_pluralization():
    baseline = rules.Baseline(n=5, median=300.0, minimum=280.0, anomaly_threshold=290.0)

    def make(depart, return_date):
        return rules.Candidate(
            origin="FRU",
            destination="HKT",
            market="kg",
            depart_date=depart,
            return_date=return_date,
            price_local=200.0,
            currency="kgs",
            landed_usd=2.28,
            airline="KC",
            transfers=1,
            gate="Trip.com",
            search_url="https://x",
            baseline=baseline,
        )

    assert make("2026-10-12", "2026-10-13").nights_text == "1 ночь"
    assert make("2026-10-12", "2026-10-14").nights_text == "2 ночи"
    assert make("2026-10-12", "2026-10-17").nights_text == "5 ночей"
    assert make("2026-10-01", "2026-10-22").nights_text == "21 ночь"
    assert make("2026-10-01", "2026-10-23").nights_text == "22 ночи"
    assert make("2026-10-12", None).nights_text == "в один конец"


def test_daily_minimums_with_price_local_and_market_filter(conn):
    seed(conn, [300.0, 250.0], now="2026-07-20T06:00:00Z", market="kg")
    seed(conn, [900.0], now="2026-07-20T06:00:00Z", market="ru")
    seed(conn, [260.0], now="2026-07-21T06:00:00Z", market="kg")
    result = rules.daily_minimums(
        conn, "FRU", "HKT", since="2026-07-01T00:00:00Z", market="kg", column="price_local"
    )
    assert result == [("2026-07-20", 250.0), ("2026-07-21", 260.0)]


def test_daily_minimums_rejects_invalid_column(conn):
    with pytest.raises(ValueError):
        rules.daily_minimums(
            conn, "FRU", "HKT", since="2026-07-01T00:00:00Z", column="price_local; DROP TABLE x"
        )
