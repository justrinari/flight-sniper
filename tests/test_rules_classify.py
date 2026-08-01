import pytest

from src import rules


@pytest.fixture()
def baseline():
    # median 300, p10 = 240
    return rules.Baseline(n=20, median=300.0, minimum=230.0, anomaly_threshold=240.0)


def test_below_absolute_threshold_is_green(baseline, config_stub):
    assert rules.classify(249.0, baseline, config_stub) == rules.GREEN


def test_below_anomaly_percentile_is_green(baseline, config_stub):
    high_threshold_cfg = config_stub.with_overrides({"abs_threshold_usd": "100"})
    assert rules.classify(239.0, baseline, high_threshold_cfg) == rules.GREEN


def test_below_median_minus_delta_is_yellow(baseline, config_stub):
    cfg = config_stub.with_overrides({"abs_threshold_usd": "100"})
    # median*0.85 = 255; цена 250 — жёлтая, но выше p10=240
    assert rules.classify(250.0, baseline, cfg) == rules.YELLOW


def test_ordinary_price_is_gray(baseline, config_stub):
    cfg = config_stub.with_overrides({"abs_threshold_usd": "100"})
    assert rules.classify(290.0, baseline, cfg) == rules.GRAY


def test_exactly_at_yellow_boundary_is_gray(baseline, config_stub):
    cfg = config_stub.with_overrides({"abs_threshold_usd": "100"})
    assert rules.classify(255.0, baseline, cfg) == rules.GRAY


def test_without_baseline_only_absolute_threshold_applies(config_stub):
    assert rules.classify(240.0, None, config_stub) == rules.GREEN
    assert rules.classify(400.0, None, config_stub) == rules.GRAY


def test_delta_to_median_is_negative_when_cheaper(baseline):
    assert rules.delta_to_median(255.0, baseline) == pytest.approx(-0.15)


def test_delta_to_median_is_positive_when_pricier(baseline):
    assert rules.delta_to_median(330.0, baseline) == pytest.approx(0.10)


def test_delta_to_median_without_baseline_is_none():
    assert rules.delta_to_median(300.0, None) is None


def test_level_emoji_mapping():
    assert rules.level_emoji(rules.GREEN) == "🟢"
    assert rules.level_emoji(rules.YELLOW) == "🟡"
    assert rules.level_emoji(rules.GRAY) == "⚪"
