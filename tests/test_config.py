import textwrap

import pytest

from src.config import Config


@pytest.fixture()
def config_path(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(
        textwrap.dedent(
            """
            origins: [FRU, ALA]
            destinations: [HKT, DPS]
            markets: [kg, ru]
            market_currency:
              kg: kgs
              ru: rub
            cross_market_delta: 0.05
            fx_markup:
              default: 0.025
              usd: 0.0
            departure_month: "2026-10"
            return_months: ["2026-10", "2026-11"]
            trip_type: round_trip
            nights_range: [10, 16]
            report_currency: usd
            max_transfer_hours: 12
            abs_threshold_usd: 250
            anomaly_percentile: 10
            yellow_delta: 0.15
            baseline_window_days: 30
            cache_ttl_hours: 48
            digest_time: "09:00"
            timezone: "Asia/Bishkek"
            """
        ),
        encoding="utf-8",
    )
    return path


def test_load_reads_all_fields(config_path):
    cfg = Config.load(config_path)
    assert cfg.origins == ["FRU", "ALA"]
    assert cfg.markets == ["kg", "ru"]
    assert cfg.nights_range == (10, 16)
    assert cfg.abs_threshold_usd == 250


def test_routes_is_cartesian_product(config_path):
    cfg = Config.load(config_path)
    assert cfg.routes() == [
        ("FRU", "HKT"),
        ("FRU", "DPS"),
        ("ALA", "HKT"),
        ("ALA", "DPS"),
    ]


def test_currency_for_market(config_path):
    cfg = Config.load(config_path)
    assert cfg.currency_for("kg") == "kgs"
    assert cfg.currency_for("ru") == "rub"


def test_currency_for_unknown_market_raises(config_path):
    cfg = Config.load(config_path)
    with pytest.raises(KeyError):
        cfg.currency_for("tj")


def test_markup_falls_back_to_default(config_path):
    cfg = Config.load(config_path)
    assert cfg.markup_for("usd") == 0.0
    assert cfg.markup_for("kgs") == 0.025


def test_with_overrides_replaces_threshold(config_path):
    cfg = Config.load(config_path)
    updated = cfg.with_overrides({"abs_threshold_usd": "199.5"})
    assert updated.abs_threshold_usd == 199.5
    assert cfg.abs_threshold_usd == 250  # исходный объект не мутирован


def test_with_overrides_ignores_unknown_keys(config_path):
    cfg = Config.load(config_path)
    updated = cfg.with_overrides({"nonsense": "1"})
    assert updated.abs_threshold_usd == 250


def test_missing_required_key_raises(tmp_path):
    path = tmp_path / "bad.yaml"
    path.write_text("origins: [FRU]\n", encoding="utf-8")
    with pytest.raises(ValueError, match="destinations"):
        Config.load(path)


def test_extra_routes_defaults_to_empty(config_path):
    cfg = Config.load(config_path)
    assert cfg.extra_routes == ()


def test_routes_includes_extra_routes(config_path):
    cfg = Config.load(config_path)
    updated = cfg.with_overrides({"extra_routes": '[["TAS", "HKT"]]'})
    assert updated.routes() == [
        ("FRU", "HKT"),
        ("FRU", "DPS"),
        ("ALA", "HKT"),
        ("ALA", "DPS"),
        ("TAS", "HKT"),
    ]


def test_routes_deduplicates_extra_routes_already_in_base(config_path):
    cfg = Config.load(config_path)
    updated = cfg.with_overrides({"extra_routes": '[["FRU", "HKT"], ["TAS", "HKT"]]'})
    assert updated.routes() == [
        ("FRU", "HKT"),
        ("FRU", "DPS"),
        ("ALA", "HKT"),
        ("ALA", "DPS"),
        ("TAS", "HKT"),
    ]


def test_with_overrides_extra_routes_is_tuple_of_tuples(config_path):
    cfg = Config.load(config_path)
    updated = cfg.with_overrides({"extra_routes": '[["TAS", "HKT"]]'})
    assert updated.extra_routes == (("TAS", "HKT"),)
