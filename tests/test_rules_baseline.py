import pytest

from src import rules


def test_percentile_median_of_odd_list():
    assert rules.percentile([1, 2, 3, 4, 5], 50) == 3.0


def test_percentile_interpolates():
    assert rules.percentile([1, 2, 3, 4], 10) == pytest.approx(1.3)


def test_percentile_edges():
    assert rules.percentile([10, 20, 30], 0) == 10.0
    assert rules.percentile([10, 20, 30], 100) == 30.0


def test_percentile_single_value():
    assert rules.percentile([42], 10) == 42.0


def test_percentile_unsorted_input():
    assert rules.percentile([5, 1, 3], 50) == 3.0


def test_percentile_empty_raises():
    with pytest.raises(ValueError):
        rules.percentile([], 50)


def test_compute_baseline_returns_stats():
    values = [100, 110, 120, 130, 140, 150, 160, 170, 180, 190]
    baseline = rules.compute_baseline(values, anomaly_percentile=10)
    assert baseline.n == 10
    assert baseline.median == pytest.approx(145.0)
    assert baseline.minimum == 100.0
    assert baseline.anomaly_threshold == pytest.approx(109.0)


def test_compute_baseline_returns_none_below_minimum_sample():
    assert rules.compute_baseline([100, 110], anomaly_percentile=10) is None


def test_compute_baseline_honors_custom_min_sample():
    baseline = rules.compute_baseline([100, 110], anomaly_percentile=10, min_sample=2)
    assert baseline is not None
    assert baseline.n == 2


def test_compute_baseline_of_empty_is_none():
    assert rules.compute_baseline([], anomaly_percentile=10) is None
