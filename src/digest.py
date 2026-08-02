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

from src import airlines, arbitrage, rules, store

MAX_SCAN_AGE_HOURS = 12
PRECISION_PERIOD_DAYS = 14

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
    # Выборка — история дневных минимумов, а не все офферы: иначе сегодняшний
    # минимум сравнивался бы с распределением, в котором он сам и есть минимум,
    # и светофор всегда горел бы зелёным. Пока дней наблюдения мало,
    # compute_baseline вернёт None — и это честнее выдуманного вердикта.
    daily = rules.daily_minimums(conn, origin, destination, since=window_start)
    baseline = rules.compute_baseline(
        [price for _, price in daily], cfg.anomaly_percentile
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


def dead_man_switch(
    conn: sqlite3.Connection, now: str, max_age_hours: int = MAX_SCAN_AGE_HOURS
) -> Optional[str]:
    """Отличает молчание сканера от честного отсутствия находок.

    Без этого сторожа сломанный скан выглядит как «дешёвых билетов нет» —
    а это разные вещи.
    """
    last_scan_at = store.get_meta(conn, "last_scan_at")
    if last_scan_at is None:
        return "🚨 Сканер ни разу не отработал успешно — проверь GitHub Actions."
    cutoff = store.shift_hours(now, -max_age_hours)
    if last_scan_at < cutoff:
        return f"🚨 Сканер молчит с {last_scan_at} — проверь GitHub Actions."
    return None


def render_trends(conn: sqlite3.Connection, cfg, now: str) -> list[str]:
    """По строке на маршрут: серия снижений или недельная разница, если есть что сказать."""
    window_start = store.shift_days(now, -cfg.baseline_window_days)
    lines: list[str] = []
    for origin, destination in cfg.routes():
        route = f"{origin}→{destination}"
        daily = rules.daily_minimums(conn, origin, destination, since=window_start)
        streak = rules.falling_streak(daily)
        if streak >= 2:
            lines.append(f"{route} дешевеет {streak}-й день подряд.")
            continue

        delta = rules.week_delta(conn, origin, destination, now=now)
        if delta is not None and abs(delta) >= 0.05:
            percent = round(abs(delta) * 100)
            word = "дешевле" if delta < 0 else "дороже"
            lines.append(f"{route}: на {percent}% {word}, чем неделю назад.")
    return lines


def weekly_manual_block(cfg, now: str) -> Optional[str]:
    """Раз в неделю (по понедельникам) — напоминание проверить лоукостеров руками."""
    local = _local_date(now, cfg.timezone)
    if local.weekday() != 0:
        return None
    return "\n".join(
        [
            "🧭 <b>Ручная проверка (раз в неделю)</b>",
            "Метапоиск не видит часть лоукостеров — раз в неделю стоит проверить руками "
            "склейки через хабы:",
            '• <a href="https://www.google.com/flights">Дели (DEL)</a> — IndiGo и Air India '
            "держат дешёвые стыковки в Азию, которых нет в кэше.",
            '• <a href="https://www.google.com/flights">Куала-Лумпур (KUL)</a> — домашний хаб '
            "AirAsia, метапоиск часто не индексирует её тарифы.",
            '• <a href="https://www.google.com/flights">Дубай (DXB)</a> — flydubai закрывает '
            "стыковки, которых нет в кэше.",
        ]
    )


def precision_block(
    conn: sqlite3.Connection, now: str, last_report_at: Optional[str]
) -> Optional[str]:
    """Раз в PRECISION_PERIOD_DAYS дней — отчёт о том, сколько алертов подтвердилось."""
    if last_report_at:
        cutoff = store.shift_days(now, -PRECISION_PERIOD_DAYS)
        if last_report_at >= cutoff:
            return None

    since = store.shift_days(now, -PRECISION_PERIOD_DAYS * 2)
    stats = store.alert_stats(conn, since=since)
    total = stats["total"]
    if total == 0:
        return None

    reviewed = stats["bought"] + stats["mismatch"]
    if reviewed == 0:
        return (
            "📋 <b>Точность алертов</b>\n"
            "За последние недели не было ни одного отзыва — ответь /bought или /mismatch "
            "на алерт, по этим отметкам калибруются пороги."
        )

    percent = round(stats["bought"] / reviewed * 100)
    return (
        "📋 <b>Точность алертов</b>\n"
        f"Precision алертов: {stats['bought']}/{reviewed} ({percent}%), "
        f"всего алертов {total}"
    )


def build_digest_text(conn: sqlite3.Connection, cfg, now: str) -> str:
    blocks: list[str] = []

    switch = dead_man_switch(conn, now)
    if switch:
        blocks.append(switch)

    summaries = build_all_summaries(conn, cfg, now=now)
    blocks.append(render_digest(summaries, cfg, now=now))

    findings = arbitrage.find(conn, cfg, now=now)
    if findings:
        arb_lines = ["🔄 <b>Кросс-рыночный арбитраж</b>"]
        arb_lines.extend(arbitrage.render_line(f) for f in findings[:5])
        blocks.append("\n".join(arb_lines))

    trends = render_trends(conn, cfg, now=now)
    if trends:
        blocks.append("\n".join(["📈 <b>Тренды</b>", *trends]))

    last_report_at = store.get_meta(conn, "precision_reported_at")
    precision = precision_block(conn, now=now, last_report_at=last_report_at)
    if precision:
        blocks.append(precision)
        store.set_meta(conn, "precision_reported_at", now)

    weekly = weekly_manual_block(cfg, now)
    if weekly:
        blocks.append(weekly)

    return "\n\n".join(blocks)
