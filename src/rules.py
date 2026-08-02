"""Статистика и светофор.

Baseline строится по маршруту в целом (все даты месяца вместе): по конкретной
дате вылета записей слишком мало, чтобы перцентиль что-то значил.

Важно, ЧТО именно попадает в выборку. Сравнивать сегодняшнюю минимальную цену
со всеми ценами подряд бессмысленно: минимум по определению лежит ниже любого
перцентиля того же распределения, и светофор всегда горел бы зелёным. Поэтому
выборка — это история ДНЕВНЫХ МИНИМУМОВ (см. daily_minimums): вопрос, на который
отвечает светофор, звучит как «дешевле ли сегодняшняя лучшая цена, чем бывала
лучшая цена в прошлые дни».
"""

from __future__ import annotations

import math
import sqlite3
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


def daily_minimums(
    conn: sqlite3.Connection, origin: str, destination: str, since: str
) -> list[tuple[str, float]]:
    """Минимальная landed-цена по каждому дню наблюдения, по возрастанию даты.

    Это и есть выборка для baseline: одна точка на день, а не все офферы подряд.
    """
    rows = conn.execute(
        "SELECT substr(last_seen_at, 1, 10) AS day, MIN(landed_usd) AS best"
        " FROM price_history"
        " WHERE origin = ? AND destination = ? AND last_seen_at >= ? AND landed_usd IS NOT NULL"
        " GROUP BY day ORDER BY day",
        (origin, destination, since),
    ).fetchall()
    return [(row["day"], float(row["best"])) for row in rows]
