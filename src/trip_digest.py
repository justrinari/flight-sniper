"""Сводка и алерт режима поездки из нескольких перелётов.

Читает свежие цены каждого перелёта из базы, собирает из них маршруты
(src/itinerary.py) и рендерит текст для Telegram.
"""

from __future__ import annotations

import html
import sqlite3
from dataclasses import dataclass
from datetime import date
from typing import Optional, Sequence

from src import airlines, cities, itinerary, store
from src.digest import _local_date, dead_man_switch, format_age, format_delta, format_transfers
from src.itinerary import Itinerary, Leg

DISCLAIMER = "Цены из кэша Aviasales, на одного взрослого, без багажа."
# Сумма лучшего маршрута из прошлой сводки — для строки «прошлая сводка $…».
PREV_KEY = "trip_best_prev"


@dataclass(frozen=True)
class DigestResult:
    text: str
    best_total: Optional[float]


def load_itineraries(
    conn: sqlite3.Connection, cfg, now: str
) -> tuple[list[Itinerary], dict[tuple[str, str], list[Leg]]]:
    """Маршруты из свежих цен базы, от дешёвого к дорогому, и сами перелёты.

    Свежесть и отсев однопроходных фантомов — те же, что в маршрутной сводке.
    Перелёты с датой раньше сегодняшней (по часовому поясу владельца)
    отбрасываются: кэш ещё может их помнить, но купить их уже нельзя.
    """
    today = _local_date(now, cfg.timezone).date().isoformat()
    rows = []
    for origin, destination in cfg.routes():
        rows.extend(
            row
            for row in store.fresh_prices(
                conn,
                origin,
                destination,
                now=now,
                ttl_hours=cfg.cache_ttl_hours,
                min_lifetime_minutes=cfg.min_offer_lifetime_minutes,
            )
            if row["depart_date"] >= today
        )
    legs = itinerary.cheapest_legs(rows)
    return itinerary.build_itineraries(legs, cfg.trip), legs


def stage_label(stop: Sequence[str]) -> str:
    """Подпись остановки: город, если он один, иначе страна."""
    if len(stop) == 1:
        return cities.name(stop[0])
    countries = {cities.country(code) for code in stop}
    if len(countries) == 1 and None not in countries:
        return countries.pop()
    return " / ".join(cities.name(code) for code in stop)


def trip_title(trip) -> str:
    """'Бали → Пхукет → Вьетнам → Филиппины → Бали'."""
    return " → ".join(stage_label(stop) for stop in trip.stops)


def missing_stages(legs: dict[tuple[str, str], list[Leg]], trip) -> list[str]:
    """Отрезки поездки, для которых в кэше нет ни одной свежей цены."""
    missing = []
    for current, following in zip(trip.stops, trip.stops[1:]):
        if not any((o, d) in legs for o in current for d in following):
            missing.append(f"{stage_label(current)} → {stage_label(following)}")
    return missing


def format_duration(minutes: Optional[int]) -> str:
    """920 → '15 ч 20 мин'. Пусто, если длительность неизвестна."""
    if not minutes:
        return ""
    hours, rest = divmod(int(minutes), 60)
    if not hours:
        return f"{rest} мин"
    if not rest:
        return f"{hours} ч"
    return f"{hours} ч {rest} мин"


def _day_month(day: str) -> str:
    return date.fromisoformat(day).strftime("%d.%m")


def render_itinerary(
    rank: int, item: Itinerary, now: str, prev_total: Optional[float] = None
) -> list[str]:
    head = f"{rank}. <b>${item.total_usd:.0f}</b> за взрослого"
    if prev_total:
        delta = format_delta((item.total_usd - prev_total) / prev_total)[1:-1]
        head += f" (прошлая сводка ${prev_total:.0f}, {delta})"
    lines = [head]
    for leg in item.legs:
        route = html.escape(cities.route(leg.origin, leg.destination))
        carrier = html.escape(airlines.name(leg.airline))
        duration = format_duration(leg.duration_min)
        detail = ", ".join(
            part
            for part in (
                carrier,
                format_transfers(leg.transfers),
                f"в пути {duration}" if duration else "",
            )
            if part
        )
        lines.append(
            f"   {_day_month(leg.depart_date)} {route} — ${leg.landed_usd:.0f} {detail}"
        )
    oldest = min(leg.last_seen_at for leg in item.legs)
    tail = f"   {item.days} дн."
    travel = format_duration(item.travel_minutes)
    if travel:
        tail += f" · в самолётах и пересадках {travel}"
    tail += f" · цены {format_age(oldest, now)}"
    links = " ".join(
        f'<a href="{html.escape(leg.search_url, quote=True)}">{index}</a>'
        for index, leg in enumerate(item.legs, start=1)
        if leg.search_url
    )
    if links:
        tail += f" · ссылки: {links}"
    lines.append(tail)
    return lines


def _departure_window(trip) -> str:
    if trip.earliest_start:
        return f"{_day_month(trip.earliest_start)}–{_day_month(trip.latest_start)}"
    return f"до {_day_month(trip.latest_start)}"


def build_text(conn: sqlite3.Connection, cfg, now: str) -> DigestResult:
    trip = cfg.trip
    lines: list[str] = []
    switch = dead_man_switch(conn, now)
    if switch:
        lines += [switch, ""]

    lines.append("✈️ " + html.escape(trip_title(trip)))
    lines.append(
        f"Вылет {_departure_window(trip)} · до {trip.max_total_days} дней · "
        f"от {trip.min_stay_days} дн. в стране"
    )

    itineraries, legs = load_itineraries(conn, cfg, now)
    top = itinerary.top_distinct(itineraries, trip.top_n)
    if not top:
        lines += ["", "Подходящих маршрутов в кэше сейчас нет."]
        missing = missing_stages(legs, trip)
        if missing:
            lines.append("Нет ни одной цены: " + html.escape(", ".join(missing)))
    else:
        prev_raw = store.get_meta(conn, PREV_KEY)
        prev_total = float(prev_raw) if prev_raw else None
        for rank, item in enumerate(top, start=1):
            lines.append("")
            lines += render_itinerary(rank, item, now, prev_total if rank == 1 else None)

    lines += ["", DISCLAIMER]
    return DigestResult(
        text="\n".join(lines), best_total=top[0].total_usd if top else None
    )


def render_alert(item: Itinerary, threshold: float, now: str) -> str:
    lines = [f"🟢 Маршрут дешевле ${threshold:.0f}", ""]
    lines += render_itinerary(1, item, now)
    lines += ["", DISCLAIMER]
    return "\n".join(lines)
