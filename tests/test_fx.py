import pytest
import requests
import responses

from src import fx
from src.config import Config
from src.models import PriceRecord

RATES_PAYLOAD = {
    "result": "success",
    "base_code": "USD",
    "time_last_update_unix": 1785000000,
    "rates": {"USD": 1.0, "KGS": 87.5, "RUB": 92.0},
}


@pytest.fixture()
def session():
    return requests.Session()


@responses.activate
def test_fetch_usd_rates_returns_lowercase_mapping(session):
    responses.add(responses.GET, fx.FX_URL, json=RATES_PAYLOAD, status=200)
    rates = fx.fetch_usd_rates(session)
    assert rates["kgs"] == 87.5
    assert rates["rub"] == 92.0
    assert rates["usd"] == 1.0


@responses.activate
def test_fetch_usd_rates_raises_on_error_result(session):
    responses.add(
        responses.GET, fx.FX_URL, json={"result": "error", "error-type": "quota"}, status=200
    )
    with pytest.raises(fx.FxError, match="quota"):
        fx.fetch_usd_rates(session)


@responses.activate
def test_fetch_usd_rates_raises_on_http_error(session):
    responses.add(responses.GET, fx.FX_URL, json={}, status=503)
    with pytest.raises(fx.FxError):
        fx.fetch_usd_rates(session)


@responses.activate
def test_missing_rates_key_raises_fx_error(session):
    responses.add(responses.GET, fx.FX_URL, json={"result": "success"}, status=200)
    with pytest.raises(fx.FxError, match="rates"):
        fx.fetch_usd_rates(session)


@responses.activate
def test_rates_of_wrong_type_raises_fx_error(session):
    responses.add(
        responses.GET, fx.FX_URL, json={"result": "success", "rates": "нет"}, status=200
    )
    with pytest.raises(fx.FxError):
        fx.fetch_usd_rates(session)


def test_usd_per_unit_inverts_the_rate():
    assert fx.usd_per_unit({"kgs": 87.5}, "kgs") == pytest.approx(1 / 87.5)


def test_usd_per_unit_is_case_insensitive():
    assert fx.usd_per_unit({"kgs": 87.5}, "KGS") == pytest.approx(1 / 87.5)


def test_usd_per_unit_raises_for_unknown_currency():
    with pytest.raises(fx.FxError, match="tjs"):
        fx.usd_per_unit({"kgs": 87.5}, "tjs")


def test_landed_usd_applies_markup():
    # 38000 сом × (1/87.5) USD × 1.025 markup
    assert fx.landed_usd(38000.0, 1 / 87.5, 0.025) == pytest.approx(445.14, abs=0.01)


def test_landed_usd_without_markup():
    assert fx.landed_usd(400.0, 1.0, 0.0) == pytest.approx(400.0)


def test_enrich_fills_fx_rate_and_landed(tmp_path):
    cfg = Config(
        origins=["FRU"],
        destinations=["HKT"],
        markets=["kg"],
        market_currency={"kg": "kgs"},
        cross_market_delta=0.05,
        fx_markup={"default": 0.025, "usd": 0.0},
        departure_month="2026-10",
        return_months=["2026-10"],
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
    )
    record = PriceRecord(
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
    )
    enriched = fx.enrich([record], {"kgs": 87.5}, cfg)
    assert enriched[0].fx_rate == pytest.approx(1 / 87.5)
    assert enriched[0].landed_usd == pytest.approx(445.14, abs=0.01)


def test_enrich_skips_records_with_unknown_currency():
    cfg_rates = {"kgs": 87.5}
    record = PriceRecord(
        source="aviasales_cache",
        origin="FRU",
        destination="HKT",
        market="xx",
        depart_date="2026-10-12",
        return_date=None,
        price_local=100.0,
        currency="zzz",
        airline="KC",
        transfers=0,
        duration_min=600,
    )
    assert fx.enrich([record], cfg_rates, None) == []


def test_enrich_logs_dropped_records(caplog):
    record = PriceRecord(
        source="aviasales_cache",
        origin="FRU",
        destination="HKT",
        market="xx",
        depart_date="2026-10-12",
        return_date=None,
        price_local=100.0,
        currency="zzz",
        airline="KC",
        transfers=0,
        duration_min=600,
    )
    with caplog.at_level("WARNING"):
        assert fx.enrich([record], {"kgs": 87.5}, None) == []
    assert "zzz" in caplog.text
