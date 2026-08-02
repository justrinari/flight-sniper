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
from datetime import date
from statistics import median as _median
from typing import Optional, Sequence

from src import store

MIN_SAMPLE = 5

_DAILY_MIN_COLUMNS = ("landed_usd", "price_local")

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
    conn: sqlite3.Connection,
    origin: str,
    destination: str,
    since: str,
    market: Optional[str] = None,
    column: str = "landed_usd",
) -> list[tuple[str, float]]:
    """Минимальная цена по каждому дню наблюдения, по возрастанию даты.

    Это и есть выборка для baseline: одна точка на день, а не все офферы подряд.
    `column` — "landed_usd" (сравнение рынков) или "price_local" (сравнение
    внутри одного рынка, без шума курса валюты); значение не параметризуется
    через `?`, поэтому допустимы только эти два литерала.
    """
    if column not in _DAILY_MIN_COLUMNS:
        raise ValueError(f"daily_minimums: недопустимая колонка {column!r}")

    sql = (
        f"SELECT substr(last_seen_at, 1, 10) AS day, MIN({column}) AS best"
        " FROM price_history"
        " WHERE origin = ? AND destination = ? AND last_seen_at >= ?"
        f" AND {column} IS NOT NULL"
    )
    params: list[object] = [origin, destination, since]
    if market is not None:
        sql += " AND market = ?"
        params.append(market)
    sql += " GROUP BY day ORDER BY day"

    rows = conn.execute(sql, params).fetchall()
    return [(row["day"], float(row["best"])) for row in rows]


_NIGHT_WORDS = ("ночь", "ночи", "ночей")


def _nights_word(n: int) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return _NIGHT_WORDS[0]
    if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14):
        return _NIGHT_WORDS[1]
    return _NIGHT_WORDS[2]


@dataclass(frozen=True)
class Candidate:
    origin: str
    destination: str
    market: str
    depart_date: str
    return_date: Optional[str]
    price_local: float
    currency: str
    landed_usd: float
    airline: Optional[str]
    transfers: Optional[int]
    gate: Optional[str]
    search_url: Optional[str]
    baseline: Baseline  # в локальной валюте своего рынка, по дневным минимумам

    @property
    def route_date_key(self) -> str:
        return f"{self.origin}-{self.destination}-{self.depart_date}-{self.return_date or ''}"

    @property
    def nights_text(self) -> str:
        """'12 ночей' / '1 ночь' / 'в один конец' — для текста алерта."""
        if not self.return_date:
            return "в один конец"
        nights = (
            date.fromisoformat(self.return_date) - date.fromisoformat(self.depart_date)
        ).days
        return f"{nights} {_nights_word(nights)}"


def find_candidates(conn: sqlite3.Connection, cfg, now: str) -> list[Candidate]:
    """Кандидаты-аномалии: сегодняшний минимум дешевле p10 истории дневных минимумов.

    Один кандидат на пару маршрут × рынок (самый дешёвый из свежих офферов),
    не по одному на каждую дату вылета. Сравнение — в локальной валюте рынка,
    чтобы движение курса не выглядело падением цены.
    """
    window_start = store.shift_days(now, -cfg.baseline_window_days)
    candidates: list[Candidate] = []

    for origin, destination in cfg.routes():
        for market in cfg.markets:
            daily = daily_minimums(
                conn,
                origin,
                destination,
                since=window_start,
                market=market,
                column="price_local",
            )
            baseline = compute_baseline(
                [price for _, price in daily], cfg.anomaly_percentile
            )
            if baseline is None:
                continue

            fresh = [
                row
                for row in store.fresh_prices(
                    conn,
                    origin,
                    destination,
                    now=now,
                    ttl_hours=cfg.cache_ttl_hours,
                    market=market,
                )
                if row["landed_usd"] is not None
            ]
            if not fresh:
                continue

            best = min(fresh, key=lambda row: row["price_local"])
            if best["price_local"] < baseline.anomaly_threshold:
                candidates.append(
                    Candidate(
                        origin=origin,
                        destination=destination,
                        market=market,
                        depart_date=best["depart_date"],
                        return_date=best["return_date"],
                        price_local=float(best["price_local"]),
                        currency=best["currency"],
                        landed_usd=float(best["landed_usd"]),
                        airline=best["airline"],
                        transfers=best["transfers"],
                        gate=best["gate"],
                        search_url=best["search_url"],
                        baseline=baseline,
                    )
                )

    candidates.sort(key=lambda c: c.landed_usd)
    return candidates
