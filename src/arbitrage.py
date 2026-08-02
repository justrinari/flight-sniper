"""Кросс-рыночный арбитраж: один и тот же рейс дешевле на другом рынке Aviasales.

Кэш не отдаёт идентификатор рейса, поэтому «тот же самый билет» определяется
по совпадению: маршрут + дата вылета + дата возврата + авиакомпания + число
пересадок. Это приближение может совпасть у двух разных рейсов той же
авиакомпании в один день — поэтому в тексте всегда пишем «≈ тот же рейс».
"""

from __future__ import annotations

import html
import sqlite3
from dataclasses import dataclass
from typing import Optional

from src import airlines, digest, store


@dataclass(frozen=True)
class Finding:
    origin: str
    destination: str
    depart_date: str
    return_date: Optional[str]
    airline: Optional[str]
    transfers: Optional[int]
    expensive_market: str
    expensive_usd: float
    cheap_market: str
    cheap_usd: float
    cheap_url: Optional[str]
    cheap_gate: Optional[str]

    @property
    def saving_ratio(self) -> float:
        if self.expensive_usd == 0:
            return 0.0
        return (self.expensive_usd - self.cheap_usd) / self.expensive_usd


def _flight_key(row: sqlite3.Row) -> tuple:
    """Ключ связки: маршрут + даты + авиакомпания + пересадки — см. модуль docstring."""
    return (row["depart_date"], row["return_date"], row["airline"], row["transfers"])


def _cheapest_per_key(rows: list[sqlite3.Row]) -> dict[tuple, sqlite3.Row]:
    """Внутри одного рынка — минимальная цена на каждую связку."""
    best: dict[tuple, sqlite3.Row] = {}
    for row in rows:
        key = _flight_key(row)
        current = best.get(key)
        if current is None or row["landed_usd"] < current["landed_usd"]:
            best[key] = row
    return best


def find(conn: sqlite3.Connection, cfg, now: str) -> list[Finding]:
    findings: list[Finding] = []
    if len(cfg.markets) < 2:
        return findings

    for origin, destination in cfg.routes():
        per_market: dict[str, dict[tuple, sqlite3.Row]] = {}
        for market in cfg.markets:
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
            per_market[market] = _cheapest_per_key(fresh)

        common_keys = set.intersection(*(set(best.keys()) for best in per_market.values()))

        for key in common_keys:
            entries = [(market, per_market[market][key]) for market in cfg.markets]
            cheap_market, cheap_row = min(entries, key=lambda e: e[1]["landed_usd"])
            expensive_market, expensive_row = max(entries, key=lambda e: e[1]["landed_usd"])
            expensive_usd = float(expensive_row["landed_usd"])
            cheap_usd = float(cheap_row["landed_usd"])
            if expensive_usd <= 0:
                continue
            ratio = (expensive_usd - cheap_usd) / expensive_usd
            if ratio <= cfg.cross_market_delta:
                continue

            depart_date, return_date, airline_code, transfers = key
            findings.append(
                Finding(
                    origin=origin,
                    destination=destination,
                    depart_date=depart_date,
                    return_date=return_date,
                    airline=airline_code,
                    transfers=transfers,
                    expensive_market=expensive_market,
                    expensive_usd=expensive_usd,
                    cheap_market=cheap_market,
                    cheap_usd=cheap_usd,
                    cheap_url=cheap_row["search_url"],
                    cheap_gate=cheap_row["gate"],
                )
            )

    findings.sort(key=lambda f: f.saving_ratio, reverse=True)
    return findings


def render_line(finding: Finding) -> str:
    route = f"{html.escape(finding.origin)}→{html.escape(finding.destination)}"
    day = digest.format_day(finding.depart_date)
    airline_name = html.escape(airlines.name(finding.airline))
    percent = round(finding.saving_ratio * 100)

    line = (
        f"{route} {day} {airline_name}: "
        f"${finding.expensive_usd:.0f} ({html.escape(finding.expensive_market)}) / "
        f"<b>${finding.cheap_usd:.0f} landed ({html.escape(finding.cheap_market)})</b> "
        f"🔄 арбитраж −{percent}% <i>(≈ тот же рейс)</i>"
    )
    if finding.cheap_url:
        line += f' <a href="{html.escape(finding.cheap_url, quote=True)}">поиск</a>'
    return line
