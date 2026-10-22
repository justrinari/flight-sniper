"""Сборка маршрутов из нескольких перелётов.

Кэш Aviasales знает цены только отдельных перелётов. Модуль перебирает их и
собирает целые маршруты, подходящие под ограничения поездки: вылет не позже
latest_start, между перелётами не меньше min_stay_days, вся поездка не длиннее
max_total_days, каждый следующий перелёт вылетает оттуда, куда прилетел
предыдущий. Сети и базы здесь нет — только данные на входе и на выходе.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Iterable, Optional, Sequence

from src.config import TripConfig


@dataclass(frozen=True)
class Leg:
    """Лучшая цена одного перелёта на одну дату."""

    origin: str
    destination: str
    depart_date: str  # YYYY-MM-DD
    landed_usd: float
    airline: str
    transfers: Optional[int]
    market: str
    currency: str
    search_url: str
    last_seen_at: str


@dataclass(frozen=True)
class Itinerary:
    legs: tuple[Leg, ...]
    total_usd: float

    @property
    def airports(self) -> tuple[str, ...]:
        """Куда прилетает каждый перелёт — по этому набору топ отличается."""
        return tuple(leg.destination for leg in self.legs)

    @property
    def days(self) -> int:
        first = date.fromisoformat(self.legs[0].depart_date)
        last = date.fromisoformat(self.legs[-1].depart_date)
        return (last - first).days


def cheapest_legs(rows: Iterable) -> dict[tuple[str, str], list[Leg]]:
    """Строки price_history → самая дешёвая цена на (откуда, куда, дата).

    Строки без landed_usd (не нашёлся курс) пропускаются. Перелёты каждой
    пары отсортированы по дате.
    """
    best: dict[tuple[str, str, str], Leg] = {}
    for row in rows:
        if row["landed_usd"] is None:
            continue
        leg = Leg(
            origin=row["origin"],
            destination=row["destination"],
            depart_date=row["depart_date"],
            landed_usd=float(row["landed_usd"]),
            airline=row["airline"] or "",
            transfers=row["transfers"],
            market=row["market"],
            currency=row["currency"],
            search_url=row["search_url"] or "",
            last_seen_at=row["last_seen_at"],
        )
        key = (leg.origin, leg.destination, leg.depart_date)
        if key not in best or leg.landed_usd < best[key].landed_usd:
            best[key] = leg

    grouped: dict[tuple[str, str], list[Leg]] = {}
    for leg in sorted(best.values(), key=lambda item: item.depart_date):
        grouped.setdefault((leg.origin, leg.destination), []).append(leg)
    return grouped


def build_itineraries(
    legs: dict[tuple[str, str], list[Leg]], trip: TripConfig
) -> list[Itinerary]:
    """Все маршруты, подходящие под ограничения, от дешёвого к дорогому.

    Перебор в глубину по остановкам. Ветка обрывается сразу, как только
    нарушено ограничение по датам, поэтому до последней остановки доходят
    только допустимые сочетания.
    """
    results: list[Itinerary] = []
    last_stop = len(trip.stops) - 1

    def walk(stop: int, city: str, chosen: list[Leg], total: float) -> None:
        if stop == last_stop:
            results.append(Itinerary(legs=tuple(chosen), total_usd=round(total, 2)))
            return
        start = date.fromisoformat(chosen[0].depart_date) if chosen else None
        previous = date.fromisoformat(chosen[-1].depart_date) if chosen else None
        for destination in trip.stops[stop + 1]:
            for leg in legs.get((city, destination), []):
                day = date.fromisoformat(leg.depart_date)
                if previous is None:
                    if leg.depart_date > trip.latest_start:
                        continue
                else:
                    if (day - previous).days < trip.min_stay_days:
                        continue
                    if (day - start).days > trip.max_total_days:
                        continue
                walk(stop + 1, destination, chosen + [leg], total + leg.landed_usd)

    for origin in trip.stops[0]:
        walk(0, origin, [], 0.0)

    results.sort(key=lambda item: (item.total_usd, item.legs[0].depart_date))
    return results


def top_distinct(itineraries: Sequence[Itinerary], n: int) -> list[Itinerary]:
    """Первые n маршрутов с разными наборами городов.

    Без этого топ-3 заняли бы почти одинаковые маршруты, отличающиеся датой
    одного перелёта на день.
    """
    seen: set[tuple[str, ...]] = set()
    top: list[Itinerary] = []
    for item in itineraries:
        if item.airports in seen:
            continue
        seen.add(item.airports)
        top.append(item)
        if len(top) >= n:
            break
    return top
