"""Сборка и рендер ежедневного дайджеста.

Все цены — landed USD: только так рынки сравнимы между собой.
Baseline считается по всему окну (включая протухшие записи — это история),
а «лучшая цена сегодня» — только по свежим.
"""

from __future__ import annotations

import html
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Sequence
from zoneinfo import ZoneInfo

from src import airlines, rules, store

MONTHS_RU = {
    1: "январь",
    2: "февраль",
    3: "март",
    4: "апрель",
    5: "май",
    6: "июнь",
    7: "июль",
    8: "август",
    9: "сентябрь",
    10: "октябрь",
    11: "ноябрь",
    12: "декабрь",
}


@dataclass(frozen=True)
class RouteSummary:
    origin: str
    destination: str
    best_landed: Optional[float]
    depart_date: Optional[str]
    return_date: Optional[str]
    airline: Optional[str]
    transfers: Optional[int]
    market: Optional[str]
    search_url: Optional[str]
    baseline: Optional[rules.Baseline]
    level: str
    delta_pct: Optional[float]


def build_route_summary(
    conn: sqlite3.Connection, cfg, origin: str, destination: str, now: str
) -> RouteSummary:
    window_start = store.shift_days(now, -cfg.baseline_window_days)
    history = store.recent_prices(conn, origin, destination, since=window_start)
    baseline = rules.compute_baseline(
        [row["landed_usd"] for row in history if row["landed_usd"] is not None],
        cfg.anomaly_percentile,
    )

    fresh = [
        row
        for row in store.fresh_prices(
            conn, origin, destination, now=now, ttl_hours=cfg.cache_ttl_hours
        )
        if row["landed_usd"] is not None
    ]
    if not fresh:
        return RouteSummary(
            origin=origin,
            destination=destination,
            best_landed=None,
            depart_date=None,
            return_date=None,
            airline=None,
            transfers=None,
            market=None,
            search_url=None,
            baseline=baseline,
            level=rules.GRAY,
            delta_pct=None,
        )

    best = min(fresh, key=lambda row: row["landed_usd"])
    return RouteSummary(
        origin=origin,
        destination=destination,
        best_landed=float(best["landed_usd"]),
        depart_date=best["depart_date"],
        return_date=best["return_date"],
        airline=best["airline"],
        transfers=best["transfers"],
        market=best["market"],
        search_url=best["search_url"],
        baseline=baseline,
        level=rules.classify(float(best["landed_usd"]), baseline, cfg),
        delta_pct=rules.delta_to_median(float(best["landed_usd"]), baseline),
    )


def build_all_summaries(conn: sqlite3.Connection, cfg, now: str) -> list[RouteSummary]:
    return [
        build_route_summary(conn, cfg, origin, destination, now)
        for origin, destination in cfg.routes()
    ]


def format_delta(delta_pct: Optional[float]) -> str:
    if delta_pct is None:
        return ""
    percent = round(delta_pct * 100)
    if percent == 0:
        return "(±0%)"
    sign = "−" if percent < 0 else "+"
    return f"({sign}{abs(percent)}%)"


def format_day(date_iso: Optional[str]) -> str:
    if not date_iso:
        return ""
    return f"{date_iso[8:10]}.{date_iso[5:7]}"


def format_transfers(transfers: Optional[int]) -> str:
    if transfers is None:
        return ""
    if transfers == 0:
        return "без пересадок"
    if transfers == 1:
        return "1 пересадка"
    return f"{transfers} пересадки"


def _local_date(now: str, tz: str) -> datetime:
    moment = datetime.fromisoformat(now.replace("Z", "+00:00"))
    return moment.astimezone(ZoneInfo(tz))


def render_digest(summaries: Sequence[RouteSummary], cfg, now: str) -> str:
    local = _local_date(now, cfg.timezone)
    year, month = cfg.departure_month.split("-")
    header = f"✈️ <b>{local.strftime('%d.%m')}</b> · {MONTHS_RU[int(month)]} {year}"
    lines = [header, ""]

    for summary in summaries:
        route = f"{summary.origin}→{summary.destination}"
        if summary.best_landed is None:
            lines.append(f"{route}  нет данных ⚪")
            continue
        emoji = rules.level_emoji(summary.level)
        price = f"${summary.best_landed:.0f}"
        delta = format_delta(summary.delta_pct)
        detail = ", ".join(
            part
            for part in (
                format_day(summary.depart_date),
                html.escape(airlines.name(summary.airline)),
                format_transfers(summary.transfers),
            )
            if part
        )
        line = f"{route}  мин <b>{price}</b> {delta}  {emoji}"
        if detail:
            line += f"  {detail}"
        if summary.market:
            line += f"  [{summary.market}]"
        if summary.search_url:
            line += f'  <a href="{html.escape(summary.search_url, quote=True)}">поиск</a>'
        lines.append(line)

    lines.append("")
    lines.append(
        f"<i>landed USD: цена рынка × курс × (1 + надбавка). Порог BUY: "
        f"${cfg.abs_threshold_usd:.0f}</i>"
    )
    return "\n".join(lines)
