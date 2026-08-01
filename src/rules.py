"""Статистика и светофор.

Baseline строится по маршруту в целом за окно (все даты октября вместе):
по конкретной дате записей слишком мало, чтобы перцентиль что-то значил.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from statistics import median as _median
from typing import Optional, Sequence

MIN_SAMPLE = 5

GREEN = "green"
YELLOW = "yellow"
GRAY = "gray"


@dataclass(frozen=True)
class Baseline:
    n: int
    median: float
    minimum: float
    anomaly_threshold: float  # значение перцентиля anomaly_percentile


def percentile(values: Sequence[float], p: float) -> float:
    """Линейная интерполяция между соседними точками, как numpy.percentile."""
    if not values:
        raise ValueError("percentile() на пустой выборке")
    ordered = sorted(float(v) for v in values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * (p / 100.0)
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[int(position)]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def compute_baseline(
    values: Sequence[float], anomaly_percentile: float, min_sample: int = MIN_SAMPLE
) -> Optional[Baseline]:
    if len(values) < min_sample:
        return None
    numbers = [float(v) for v in values]
    return Baseline(
        n=len(numbers),
        median=float(_median(numbers)),
        minimum=min(numbers),
        anomaly_threshold=percentile(numbers, anomaly_percentile),
    )


EMOJI = {GREEN: "🟢", YELLOW: "🟡", GRAY: "⚪"}


def classify(landed_usd: float, baseline: Optional[Baseline], cfg) -> str:
    """GREEN — покупать, YELLOW — упомянуть в дайджесте, GRAY — фон.

    В v1 GREEN ставится по кэшу; в v1.1 BUY-алерт требует подтверждения Amadeus.
    """
    if landed_usd < cfg.abs_threshold_usd:
        return GREEN
    if baseline is None:
        return GRAY
    if landed_usd < baseline.anomaly_threshold:
        return GREEN
    if landed_usd < baseline.median * (1.0 - cfg.yellow_delta):
        return YELLOW
    return GRAY


def delta_to_median(landed_usd: float, baseline: Optional[Baseline]) -> Optional[float]:
    """Относительное отклонение от медианы: -0.15 = на 15% дешевле."""
    if baseline is None or baseline.median == 0:
        return None
    return landed_usd / baseline.median - 1.0


def level_emoji(level: str) -> str:
    return EMOJI.get(level, "⚪")
