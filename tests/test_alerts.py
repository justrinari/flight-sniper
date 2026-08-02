import pytest
import requests
import responses

from src import alerts, rules
from src.sources import aviasales

URL = f"{aviasales.BASE_URL}/prices_for_dates"


@pytest.fixture()
def session():
    return requests.Session()


@pytest.fixture()
def baseline():
    return rules.Baseline(n=20, median=45000.0, minimum=34000.0, anomaly_threshold=38000.0)


@pytest.fixture()
def candidate(baseline):
    return rules.Candidate(
        origin="ALA",
        destination="HKT",
        market="kg",
        depart_date="2026-10-06",
        return_date="2026-10-16",
        price_local=36000.0,
        currency="kgs",
        landed_usd=411.0,
        airline="KC",
        transfers=0,
        gate="City.Travel",
        search_url="https://www.aviasales.kg/search/x",
        baseline=baseline,
        last_seen_at="2026-07-31T23:50:00Z",
    )


@pytest.fixture()
def rates():
    return {"kgs": 89.0}


def _item(**overrides):
    item = {
        "origin": "ALA",
        "destination": "HKT",
        "price": 36000,
        "airline": "KC",
        "departure_at": "2026-10-06T10:15:00+06:00",
        "return_at": "2026-10-16T20:00:00+07:00",
        "transfers": 0,
        "duration": 1500,
        "duration_to": 760,
        "link": "/search/x",
        "currency": "kgs",
        "gate": "City.Travel",
    }
    item.update(overrides)
    return item


def _payload(*items):
    return {"success": True, "currency": "kgs", "data": list(items)}


# ---------------------------------------------------------------------------
# confirm()
# ---------------------------------------------------------------------------


@responses.activate
def test_confirm_within_tolerance_is_buy(session, config_stub, candidate, rates):
    responses.add(responses.GET, URL, json=_payload(_item(price=36500)), status=200)
    result = alerts.confirm(session, "TOKEN", config_stub, candidate, rates, "2026-08-01T00:00:00Z")
    assert result.level == alerts.LEVEL_BUY
    assert result.confirmed_local == pytest.approx(36500.0)


@responses.activate
def test_confirm_beyond_tolerance_is_unconfirmed(session, config_stub, candidate, rates):
    responses.add(responses.GET, URL, json=_payload(_item(price=41400)), status=200)  # +15%
    result = alerts.confirm(session, "TOKEN", config_stub, candidate, rates, "2026-08-01T00:00:00Z")
    assert result.level == alerts.LEVEL_UNCONFIRMED
    assert result.confirmed_local == pytest.approx(41400.0)


@responses.activate
def test_confirm_exact_boundary_is_buy(session, config_stub, candidate, rates):
    boundary_price = candidate.price_local * alerts.CONFIRM_TOLERANCE
    responses.add(responses.GET, URL, json=_payload(_item(price=boundary_price)), status=200)
    result = alerts.confirm(session, "TOKEN", config_stub, candidate, rates, "2026-08-01T00:00:00Z")
    assert result.level == alerts.LEVEL_BUY


@responses.activate
def test_confirm_empty_cache_is_unconfirmed(session, config_stub, candidate, rates):
    responses.add(responses.GET, URL, json=_payload(), status=200)
    result = alerts.confirm(session, "TOKEN", config_stub, candidate, rates, "2026-08-01T00:00:00Z")
    assert result.level == alerts.LEVEL_UNCONFIRMED
    assert result.confirmed_local is None
    assert result.confirmed_usd is None
    assert "исчез" in result.note


@responses.activate
def test_confirm_survives_aviasales_error(session, config_stub, candidate, rates):
    responses.add(responses.GET, URL, json={}, status=429)
    result = alerts.confirm(session, "TOKEN", config_stub, candidate, rates, "2026-08-01T00:00:00Z")
    assert result.level == alerts.LEVEL_UNCONFIRMED
    assert result.confirmed_local is None
    assert result.note  # текст ошибки не пустой


@responses.activate
def test_confirm_requests_exact_date_not_month(session, config_stub, candidate, rates):
    responses.add(responses.GET, URL, json=_payload(_item()), status=200)
    alerts.confirm(session, "TOKEN", config_stub, candidate, rates, "2026-08-01T00:00:00Z")
    request_url = responses.calls[0].request.url
    assert f"departure_at={candidate.depart_date}" in request_url
    assert "departure_at=2026-10&" not in request_url


@responses.activate
def test_confirm_prefers_exact_airline_and_transfers_match(session, config_stub, candidate, rates):
    cheaper_other_combo = _item(price=30000, airline="QH", transfers=1)
    exact_combo = _item(price=36200, airline="KC", transfers=0)
    responses.add(responses.GET, URL, json=_payload(cheaper_other_combo, exact_combo), status=200)
    result = alerts.confirm(session, "TOKEN", config_stub, candidate, rates, "2026-08-01T00:00:00Z")
    assert result.confirmed_local == pytest.approx(36200.0)
    assert result.note == ""


@responses.activate
def test_confirm_falls_back_to_best_price_when_no_exact_combo(session, config_stub, candidate, rates):
    other_combo_a = _item(price=39000, airline="QH", transfers=1)
    other_combo_b = _item(price=37000, airline="FZ", transfers=2)
    responses.add(responses.GET, URL, json=_payload(other_combo_a, other_combo_b), status=200)
    result = alerts.confirm(session, "TOKEN", config_stub, candidate, rates, "2026-08-01T00:00:00Z")
    assert result.confirmed_local == pytest.approx(37000.0)
    assert "точная связка не найдена" in result.note


@responses.activate
def test_confirm_compares_in_local_currency_regardless_of_fx_move(session, config_stub, candidate):
    # Курс валюты сильно "изменился" по сравнению с тем, что мог использоваться
    # изначально — но сравнение цены всё равно идёт в локальной валюте.
    weird_rates = {"kgs": 250.0}
    responses.add(responses.GET, URL, json=_payload(_item(price=candidate.price_local)), status=200)
    result = alerts.confirm(session, "TOKEN", config_stub, candidate, weird_rates, "2026-08-01T00:00:00Z")
    assert result.level == alerts.LEVEL_BUY


@responses.activate
def test_confirmed_usd_is_computed_correctly(session, config_stub, candidate, rates):
    responses.add(responses.GET, URL, json=_payload(_item(price=36500)), status=200)
    result = alerts.confirm(session, "TOKEN", config_stub, candidate, rates, "2026-08-01T00:00:00Z")
    expected_rate = 1.0 / rates["kgs"]
    expected_markup = config_stub.markup_for("kgs")
    expected_usd = 36500.0 * expected_rate * (1.0 + expected_markup)
    assert result.confirmed_usd == pytest.approx(expected_usd)


@responses.activate
def test_confirmed_usd_is_none_without_fx_rate(session, config_stub, candidate):
    responses.add(responses.GET, URL, json=_payload(_item(price=36500)), status=200)
    result = alerts.confirm(session, "TOKEN", config_stub, candidate, {}, "2026-08-01T00:00:00Z")
    assert result.confirmed_usd is None
    assert result.level == alerts.LEVEL_BUY  # уровень определяется по локальной цене


# ---------------------------------------------------------------------------
# render_alert()
# ---------------------------------------------------------------------------


def test_render_buy_alert_contains_expected_pieces(candidate):
    result = alerts.ConfirmResult(
        candidate=candidate,
        level=alerts.LEVEL_BUY,
        confirmed_local=36500.0,
        confirmed_usd=417.0,
        note="",
    )
    text = alerts.render_alert(result, "2026-08-01T00:10:00Z")
    assert "Алматы" in text and "Пхукет" in text
    assert "$411" in text  # исходная цена кандидата
    assert "$417" in text  # подтверждённая цена
    assert "06.10" in text
    assert "Air Astana" in text  # KC
    assert "без пересадок" in text
    assert "kg" in text
    assert "City.Travel" in text
    assert candidate.search_url in text
    assert "/bought" in text


def test_render_buy_alert_shows_price_age(candidate):
    result = alerts.ConfirmResult(
        candidate=candidate,
        level=alerts.LEVEL_BUY,
        confirmed_local=36500.0,
        confirmed_usd=417.0,
        note="",
    )
    # candidate.last_seen_at = 2026-07-31T23:50:00Z, now = 2026-08-01T00:10:00Z -> 20 минут
    text = alerts.render_alert(result, "2026-08-01T00:10:00Z")
    assert "20 минут назад" in text


def test_render_unconfirmed_alert_is_marked(candidate):
    result = alerts.ConfirmResult(
        candidate=candidate,
        level=alerts.LEVEL_UNCONFIRMED,
        confirmed_local=None,
        confirmed_usd=None,
        note="предложение исчезло из выдачи кэша",
    )
    text = alerts.render_alert(result, "2026-08-01T00:10:00Z")
    assert "не подтвердилось" in text or "не подтверж" in text
    assert "/bought" not in text


def test_render_escapes_html(baseline):
    dangerous = rules.Candidate(
        origin="ALA",
        destination="HKT",
        market="kg",
        depart_date="2026-10-06",
        return_date="2026-10-16",
        price_local=36000.0,
        currency="kgs",
        landed_usd=411.0,
        airline="KC",
        transfers=0,
        gate="A&B",
        search_url="https://example.com/?a=1&b=2",
        baseline=baseline,
    )
    result = alerts.ConfirmResult(
        candidate=dangerous,
        level=alerts.LEVEL_BUY,
        confirmed_local=36500.0,
        confirmed_usd=417.0,
        note="",
    )
    text = alerts.render_alert(result, "2026-08-01T00:10:00Z")
    assert "A&amp;B" in text
    assert "A&B" not in text
