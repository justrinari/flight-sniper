import pytest

from src.config import Config


@pytest.fixture()
def config_stub() -> Config:
    return Config(
        origins=["FRU", "ALA"],
        destinations=["HKT", "DPS"],
        markets=["kg", "ru"],
        market_currency={"kg": "kgs", "ru": "rub"},
        cross_market_delta=0.05,
        fx_markup={"default": 0.025, "usd": 0.0},
        departure_months=["2026-10"],
        return_months=["2026-10", "2026-11"],
        trip_type="round_trip",
        nights_range=(10, 16),
        report_currency="usd",
        max_transfer_hours=12,
        abs_threshold_usd=250,
        anomaly_percentile=10,
        yellow_delta=0.15,
        baseline_window_days=30,
        cache_ttl_hours=48,
        digest_time="09:00",
        timezone="Asia/Bishkek",
        # Тесты сеют офферы одной вставкой, то есть с нулевым временем жизни:
        # с боевым значением (10 минут) фильтр однопроходных записей отсёк бы
        # их все и каждый тест проверял бы только его. Сам фильтр включают явно
        # те тесты, которые его и проверяют — см. test_rules_vanished.py,
        # test_store_prices.py и «фантомы» в test_rules_anomaly.py.
        min_offer_lifetime_minutes=0.0,
    )
