"""Сборка маршрутов из нескольких перелётов.

Кэш Aviasales знает цены только отдельных перелётов. Модуль перебирает их и
собирает целые маршруты, подходящие под ограничения поездки: вылет не позже
latest_start, между перелётами не меньше min_stay_days, вся поездка не длиннее
max_total_days, каждый следующий перелёт вылетает оттуда, куда прилетел
предыдущий. Сети и базы здесь нет — только данные на входе и на выходе.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Iterable, Optional, Sequence

from src.config import TripConfig
from src.sources import aviasales

# Сколько самых дешёвых вариантов перелёта на одну дату держать. Одного мало:
# самый дешёвый может прилетать слишком поздно, чтобы уложиться в дни в
# стране, а чуть более дорогой — подходить.
OFFERS_PER_DATE = 3


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
    # Время в пути от вылета до прилёта, с пересадками (поле duration кэша).
    duration_min: Optional[int] = None
    # Время в воздухе (поле duration_to). Документация его не описывает, но на
    # данных 22.10.2026 у всех прямых рейсов оно равно duration, у всех рейсов
    # с пересадкой — меньше, и сходится с расписанием сегментов (Scoot
    # SGN→SIN→MNL: 6 ч в воздухе при 25 ч 45 мин в пути).
    duration_to_min: Optional[int] = None
    # Местное время вылета и прилёта, если его удалось достать из ссылки.
    depart_at: Optional[datetime] = None
    arrive_at: Optional[datetime] = None

    @property
    def layover_min(self) -> int:
        """Сколько ждать на пересадках: время в пути минус время в воздухе."""
        if not self.duration_min or not self.duration_to_min:
            return 0
        return max(0, self.duration_min - self.duration_to_min)


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

    @property
    def travel_minutes(self) -> int:
        """Сумма времени в пути всех перелётов (неизвестное считается нулём)."""
        return sum(leg.duration_min or 0 for leg in self.legs)

    @property
    def layover_minutes(self) -> int:
        return sum(leg.layover_min for leg in self.legs)


def cheapest_legs(rows: Iterable) -> dict[tuple[str, str], list[Leg]]:
    """Строки price_history → несколько самых дешёвых вариантов на (откуда, куда, дата).

    Один и тот же рейс из разных рынков считается одним вариантом (по
    перевозчику и времени). Строки без landed_usd (не нашёлся курс)
    пропускаются. Перелёты каждой пары отсортированы по дате, затем по цене.
    """
    offers: dict[tuple, Leg] = {}
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
            duration_min=row["duration_min"],
            duration_to_min=row["duration_to_min"],
        )
        times = aviasales.ticket_times(leg.search_url)
        if times and times[0].date().isoformat() == leg.depart_date:
            leg = dataclasses.replace(leg, depart_at=times[0], arrive_at=times[1])
        if leg.depart_at is not None:
            key = (leg.origin, leg.destination, leg.airline, leg.transfers, leg.depart_at, leg.arrive_at)
        else:
            key = (id(row),)
        if key not in offers or leg.landed_usd < offers[key].landed_usd:
            offers[key] = leg

    per_date: dict[tuple[str, str, str], list[Leg]] = {}
    for leg in sorted(offers.values(), key=lambda item: item.landed_usd):
        bucket = per_date.setdefault((leg.origin, leg.destination, leg.depart_date), [])
        if len(bucket) < OFFERS_PER_DATE:
            bucket.append(leg)

    grouped: dict[tuple[str, str], list[Leg]] = {}
    for (origin, destination, _), bucket in sorted(per_date.items(), key=lambda item: item[0][2]):
        grouped.setdefault((origin, destination), []).extend(bucket)
    return grouped


def _long_enough_stay(arrived: Leg, leaving: Leg, date_gap: timedelta, min_days: int) -> bool:
    """Хватает ли дней в стране между прилётом одного перелёта и вылетом следующего.

    Если известно время, считаются полные сутки от прилёта до вылета: рейс,
    прилетевший 29.01 вечером, и вылет 31.01 ночью — это 1 сутки 9 часов, а
    не «3 дня» по датам вылета. Без времени — по датам вылета, как раньше.
    """
    if arrived.arrive_at is not None and leaving.depart_at is not None:
        return leaving.depart_at - arrived.arrive_at >= timedelta(days=min_days)
    return date_gap.days >= min_days


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
                    if trip.earliest_start and leg.depart_date < trip.earliest_start:
                        continue
                else:
                    if not _long_enough_stay(chosen[-1], leg, day - previous, trip.min_stay_days):
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
