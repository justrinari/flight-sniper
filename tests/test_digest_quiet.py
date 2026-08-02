"""Тихий дайджест: полная сводка только когда есть новость.

decide_digest() решает full/quiet и объясняет причину; render_quiet() рисует
короткую строку; build_digest_text() всё это связывает и не забывает
записать last_digest_prices при каждой отправке.
"""

import json

import pytest

from src import arbitrage, digest, store
from src.models import PriceRecord


@pytest.fixture()
def conn(tmp_path):
    connection = store.connect(tmp_path / "history.sqlite")
    store.init_schema(connection)
    return connection


def record(landed, depart="2026-10-12", origin="ALA", destination="HKT", market="kg"):
    currency = "kgs" if market == "kg" else "rub"
    return PriceRecord(
        source="aviasales_cache",
        origin=origin,
        destination=destination,
        market=market,
        depart_date=depart,
        return_date="2026-10-24",
        price_local=landed * 87.5,
        currency=currency,
        airline="KC",
        transfers=1,
        duration_min=1500,
        duration_to_min=760,
        search_url="https://x",
        fx_rate=1 / 87.5,
        landed_usd=landed,
    )


def seed(conn, landed, now, **kwargs):
    store.insert_prices(conn, [record(landed, **kwargs)], now=now)


# Все четыре маршрута config_stub: FRU-HKT, FRU-DPS, ALA-HKT, ALA-DPS.
STABLE_PRICES = {"FRU-HKT": 300.0, "FRU-DPS": 340.0, "ALA-HKT": 270.0, "ALA-DPS": 331.0}
NOW = "2026-08-01T07:00:00Z"  # суббота в Asia/Bishkek — не понедельник
SEEN_AT = "2026-08-01T06:00:00Z"


def seed_stable(conn, prices=STABLE_PRICES, now=SEEN_AT):
    for key, price in prices.items():
        origin, destination = key.split("-")
        seed(conn, price, now=now, origin=origin, destination=destination)


def quiet_setup(conn, prices=STABLE_PRICES):
    """Готовит конн так, будто вчерашняя сводка была отправлена с теми же ценами."""
    seed_stable(conn, prices)
    store.set_meta(conn, "last_scan_at", SEEN_AT)
    store.set_meta(conn, "last_digest_prices", json.dumps(prices))


# ---------------------------------------------------------------------------
# decide_digest
# ---------------------------------------------------------------------------


def test_decide_digest_first_run_is_full(conn, config_stub):
    store.set_meta(conn, "last_scan_at", SEEN_AT)
    summaries = digest.build_all_summaries(conn, config_stub, now=NOW)
    decision = digest.decide_digest(conn, config_stub, summaries, now=NOW)
    assert decision.full is True
    assert decision.reasons  # первый запуск — причина обязана быть


def test_decide_digest_quiet_when_nothing_changed(conn, config_stub):
    quiet_setup(conn)
    summaries = digest.build_all_summaries(conn, config_stub, now=NOW)
    decision = digest.decide_digest(conn, config_stub, summaries, now=NOW)
    assert decision.full is False
    assert decision.reasons == []


def test_decide_digest_price_change_at_threshold_is_full(conn, config_stub):
    quiet_setup(conn)
    # ALA-HKT: 270 -> 256.5, ровно -5% (порог digest_quiet_delta по умолчанию 0.05)
    seed(conn, 256.5, now=SEEN_AT, origin="ALA", destination="HKT")
    summaries = digest.build_all_summaries(conn, config_stub, now=NOW)
    decision = digest.decide_digest(conn, config_stub, summaries, now=NOW)
    assert decision.full is True
    assert any("Алматы → Пхукет" in r for r in decision.reasons)


def test_decide_digest_price_change_below_threshold_is_quiet(conn, config_stub):
    quiet_setup(conn)
    # ALA-HKT: 270 -> 261, -3.3%, меньше порога 5%
    seed(conn, 261.0, now=SEEN_AT, origin="ALA", destination="HKT")
    summaries = digest.build_all_summaries(conn, config_stub, now=NOW)
    decision = digest.decide_digest(conn, config_stub, summaries, now=NOW)
    assert decision.full is False


def test_decide_digest_new_route_data_is_full(conn, config_stub):
    prices = dict(STABLE_PRICES)
    prices["FRU-DPS"] = None
    seed_stable(conn, {k: v for k, v in STABLE_PRICES.items() if k != "FRU-DPS"})
    store.set_meta(conn, "last_scan_at", SEEN_AT)
    store.set_meta(conn, "last_digest_prices", json.dumps(prices))
    # теперь данные по FRU-DPS появились
    seed(conn, STABLE_PRICES["FRU-DPS"], now=SEEN_AT, origin="FRU", destination="DPS")
    summaries = digest.build_all_summaries(conn, config_stub, now=NOW)
    decision = digest.decide_digest(conn, config_stub, summaries, now=NOW)
    assert decision.full is True
    assert any("Бишкек → Бали" in r for r in decision.reasons)


def test_decide_digest_route_data_disappeared_is_full(conn, config_stub):
    # раньше данные по FRU-DPS были, сейчас их нет вообще
    seed_stable(conn, {k: v for k, v in STABLE_PRICES.items() if k != "FRU-DPS"})
    store.set_meta(conn, "last_scan_at", SEEN_AT)
    store.set_meta(conn, "last_digest_prices", json.dumps(STABLE_PRICES))
    summaries = digest.build_all_summaries(conn, config_stub, now=NOW)
    decision = digest.decide_digest(conn, config_stub, summaries, now=NOW)
    assert decision.full is True
    assert any("Бишкек → Бали" in r for r in decision.reasons)


def test_decide_digest_monday_is_full_even_when_quiet(conn, config_stub):
    monday = "2026-08-03T05:00:00Z"  # понедельник в Asia/Bishkek
    quiet_setup(conn)
    store.set_meta(conn, "last_scan_at", monday)
    summaries = digest.build_all_summaries(conn, config_stub, now=monday)
    decision = digest.decide_digest(conn, config_stub, summaries, now=monday)
    assert decision.full is True
    assert any("понедельник" in r for r in decision.reasons)


def test_decide_digest_dead_man_switch_is_full(conn, config_stub):
    quiet_setup(conn)
    store.set_meta(conn, "last_scan_at", "2026-07-20T05:00:00Z")  # давно
    summaries = digest.build_all_summaries(conn, config_stub, now=NOW)
    decision = digest.decide_digest(conn, config_stub, summaries, now=NOW)
    assert decision.full is True


def test_build_digest_text_dead_man_switch_starts_with_siren(conn, config_stub):
    quiet_setup(conn)
    store.set_meta(conn, "last_scan_at", "2026-07-20T05:00:00Z")  # давно — сторож сработает
    text = digest.build_digest_text(conn, config_stub, now=NOW)
    assert text.startswith("🚨")


def test_decide_digest_new_arbitrage_is_full(conn, config_stub):
    """Расхождение kg/ru у этого владельца структурное — оно есть почти всегда,
    поэтому «арбитраж существует» не повод слать полную сводку (иначе тихого
    дня не было бы вообще). Повод — то, что арбитраж НОВЫЙ: его не было в
    last_digest_arbitrage прошлой сводки."""
    quiet_setup(conn)
    seed(conn, 268.0, now=SEEN_AT, origin="ALA", destination="HKT", market="kg")
    seed(conn, 200.0, now=SEEN_AT, origin="ALA", destination="HKT", market="ru")
    summaries = digest.build_all_summaries(conn, config_stub, now=NOW)
    decision = digest.decide_digest(conn, config_stub, summaries, now=NOW)
    assert decision.full is True
    assert any("новый арбитраж" in r for r in decision.reasons)


def test_decide_digest_same_arbitrage_is_quiet(conn, config_stub):
    """Ключевой тест: то же расхождение kg/ru с той же экономией, что и в
    прошлой сводке (записанной в last_digest_arbitrage), — не новость.

    kg держит цену STABLE_PRICES (270) — она же lastdigest_prices, ru дороже:
    так меняется только арбитраж, а не сама цена маршрута."""
    quiet_setup(conn)
    seed(conn, 270.0, now=SEEN_AT, origin="ALA", destination="HKT", market="kg")
    seed(conn, 360.0, now=SEEN_AT, origin="ALA", destination="HKT", market="ru")
    summaries = digest.build_all_summaries(conn, config_stub, now=NOW)
    findings = arbitrage.find(conn, config_stub, now=NOW)
    store.set_meta(
        conn, "last_digest_arbitrage", json.dumps(digest._current_arbitrage(findings))
    )
    decision = digest.decide_digest(conn, config_stub, summaries, now=NOW)
    assert decision.full is False
    assert decision.reasons == []


def test_decide_digest_arbitrage_strengthened_is_full(conn, config_stub):
    quiet_setup(conn)
    seed(conn, 270.0, now=SEEN_AT, origin="ALA", destination="HKT", market="kg")
    seed(conn, 360.0, now=SEEN_AT, origin="ALA", destination="HKT", market="ru")
    summaries = digest.build_all_summaries(conn, config_stub, now=NOW)
    findings = arbitrage.find(conn, config_stub, now=NOW)
    old_arbitrage = digest._current_arbitrage(findings)
    key = next(iter(old_arbitrage))
    # экономия была ~25%; "прошлая" сводка запомнила её заметно (>порога) ниже
    old_arbitrage[key] = old_arbitrage[key] - config_stub.digest_quiet_delta - 0.02
    store.set_meta(conn, "last_digest_arbitrage", json.dumps(old_arbitrage))
    decision = digest.decide_digest(conn, config_stub, summaries, now=NOW)
    assert decision.full is True
    assert any("арбитраж усилился" in r for r in decision.reasons)


def test_decide_digest_new_arbitrage_leg_while_old_stays_is_full(conn, config_stub):
    """Появилась новая связка (ALA-DPS), при этом старая (ALA-HKT) не изменилась."""
    quiet_setup(conn)
    seed(conn, 270.0, now=SEEN_AT, origin="ALA", destination="HKT", market="kg")
    seed(conn, 360.0, now=SEEN_AT, origin="ALA", destination="HKT", market="ru")
    summaries = digest.build_all_summaries(conn, config_stub, now=NOW)
    findings = arbitrage.find(conn, config_stub, now=NOW)
    store.set_meta(
        conn, "last_digest_arbitrage", json.dumps(digest._current_arbitrage(findings))
    )

    # теперь появляется вторая связка с расхождением; kg держит цену STABLE_PRICES (331)
    seed(conn, 331.0, now=SEEN_AT, origin="ALA", destination="DPS", market="kg")
    seed(conn, 420.0, now=SEEN_AT, origin="ALA", destination="DPS", market="ru")
    summaries = digest.build_all_summaries(conn, config_stub, now=NOW)
    decision = digest.decide_digest(conn, config_stub, summaries, now=NOW)
    assert decision.full is True
    assert any("новый арбитраж" in r for r in decision.reasons)


def test_decide_digest_arbitrage_disappeared_is_full(conn, config_stub):
    """Прошлая сводка помнит находку (в meta), а сейчас арбитража нет вообще —
    рынки сошлись, возможность закрылась."""
    quiet_setup(conn)  # только kg-market, реального арбитража сейчас нет
    fake_key = "ALA-HKT-2026-10-12-KC-1"
    store.set_meta(conn, "last_digest_arbitrage", json.dumps({fake_key: 0.25}))

    summaries = digest.build_all_summaries(conn, config_stub, now=NOW)
    decision = digest.decide_digest(conn, config_stub, summaries, now=NOW)
    assert decision.full is True
    assert any("арбитраж исчез" in r for r in decision.reasons)


def test_build_digest_text_writes_last_digest_arbitrage_on_full(conn, config_stub):
    store.set_meta(conn, "last_scan_at", SEEN_AT)
    seed_stable(conn)
    seed(conn, 268.0, now=SEEN_AT, origin="ALA", destination="HKT", market="kg")
    seed(conn, 200.0, now=SEEN_AT, origin="ALA", destination="HKT", market="ru")
    digest.build_digest_text(conn, config_stub, now=NOW)
    saved = json.loads(store.get_meta(conn, "last_digest_arbitrage"))
    assert saved  # непустой словарь — арбитраж был записан


def test_build_digest_text_writes_last_digest_arbitrage_on_quiet(conn, config_stub):
    quiet_setup(conn)
    # kg держит цену STABLE_PRICES (270), ru дороже — цена маршрута не меняется,
    # меняется только наличие арбитража, а его состояние уже записано ниже.
    seed(conn, 270.0, now=SEEN_AT, origin="ALA", destination="HKT", market="kg")
    seed(conn, 360.0, now=SEEN_AT, origin="ALA", destination="HKT", market="ru")
    findings = arbitrage.find(conn, config_stub, now=NOW)
    store.set_meta(
        conn, "last_digest_arbitrage", json.dumps(digest._current_arbitrage(findings))
    )
    # ничего не поменялось — сегодня должна быть тихая сводка
    text = digest.build_digest_text(conn, config_stub, now=NOW)
    assert "landed USD" not in text
    saved = json.loads(store.get_meta(conn, "last_digest_arbitrage"))
    assert saved == digest._current_arbitrage(findings)


# ---------------------------------------------------------------------------
# render_quiet
# ---------------------------------------------------------------------------


def test_render_quiet_includes_all_routes_and_dash_for_missing(conn, config_stub):
    seed_stable(conn, {k: v for k, v in STABLE_PRICES.items() if k != "FRU-DPS"})
    summaries = digest.build_all_summaries(conn, config_stub, now=NOW)
    text = digest.render_quiet(summaries, config_stub, now=NOW)
    assert "Бишкек → Пхукет" in text
    assert "Бишкек → Бали —" in text
    assert "Алматы → Пхукет" in text
    assert "Алматы → Бали" in text
    assert "$300" in text
    assert len(text.splitlines()) <= 3


def test_render_quiet_shows_freshness_of_newest_price(conn, config_stub):
    seed_stable(conn)
    summaries = digest.build_all_summaries(conn, config_stub, now=NOW)
    text = digest.render_quiet(summaries, config_stub, now=NOW)
    assert "назад" in text or "только что" in text


def test_render_quiet_shows_best_arbitrage_when_present(conn, config_stub):
    seed_stable(conn)
    seed(conn, 268.0, now=SEEN_AT, origin="ALA", destination="HKT", market="kg")
    seed(conn, 200.0, now=SEEN_AT, origin="ALA", destination="HKT", market="ru")
    summaries = digest.build_all_summaries(conn, config_stub, now=NOW)
    findings = arbitrage.find(conn, config_stub, now=NOW)
    text = digest.render_quiet(summaries, config_stub, now=NOW, findings=findings)
    assert "арбитраж" in text
    assert "Алматы → Пхукет" in text
    assert len(text.splitlines()) <= 4


def test_render_quiet_hides_arbitrage_line_when_no_findings(conn, config_stub):
    seed_stable(conn)
    summaries = digest.build_all_summaries(conn, config_stub, now=NOW)
    text = digest.render_quiet(summaries, config_stub, now=NOW, findings=[])
    assert "арбитраж" not in text


# ---------------------------------------------------------------------------
# build_digest_text integration
# ---------------------------------------------------------------------------


def test_build_digest_text_quiet_when_nothing_changed(conn, config_stub):
    quiet_setup(conn)
    text = digest.build_digest_text(conn, config_stub, now=NOW)
    assert "Бишкек → Пхукет" in text
    assert "Бишкек → Бали" in text
    assert "Алматы → Пхукет" in text
    assert "Алматы → Бали" in text
    assert "landed USD" not in text  # это подпись полной сводки


def test_build_digest_text_writes_last_digest_prices_on_full(conn, config_stub):
    store.set_meta(conn, "last_scan_at", SEEN_AT)
    seed_stable(conn)
    digest.build_digest_text(conn, config_stub, now=NOW)
    saved = json.loads(store.get_meta(conn, "last_digest_prices"))
    assert saved == STABLE_PRICES


def test_build_digest_text_writes_last_digest_prices_on_quiet(conn, config_stub):
    quiet_setup(conn)
    seed(conn, 255.0, now=SEEN_AT, origin="ALA", destination="DPS")  # ниже порога не меняем
    digest.build_digest_text(conn, config_stub, now=NOW)
    saved = json.loads(store.get_meta(conn, "last_digest_prices"))
    assert saved["ALA-HKT"] == STABLE_PRICES["ALA-HKT"]


def test_build_digest_text_quiet_does_not_record_precision(conn, config_stub):
    quiet_setup(conn)
    store.record_alert(
        conn, "k1", 300.0, sent_at="2026-07-01T06:00:00Z", level="green", feedback="bought"
    )
    assert store.get_meta(conn, "precision_reported_at") is None
    digest.build_digest_text(conn, config_stub, now=NOW)
    assert store.get_meta(conn, "precision_reported_at") is None


def test_build_digest_text_force_full_overrides_quiet(conn, config_stub):
    quiet_setup(conn)
    text = digest.build_digest_text(conn, config_stub, now=NOW, force_full=True)
    assert "landed USD" in text
