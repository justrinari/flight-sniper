"""Сборка и рендер ежедневного дайджеста.

Все цены — landed USD: только так рынки сравнимы между собой.
Baseline считается по всему окну (включая протухшие записи — это история),
а «лучшая цена сегодня» — только по свежим.
"""

from __future__ import annotations

import html
import json
import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Sequence
from zoneinfo import ZoneInfo

from src import airlines, arbitrage, cities, rules, store

LOG = logging.getLogger(__name__)

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
    last_seen_at: Optional[str] = None


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
        last_seen_at=best["last_seen_at"],
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


_MINUTE_JUST_NOW_THRESHOLD = 5


def _minute_word(n: int) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return "минуту"
    if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14):
        return "минуты"
    return "минут"


def _hour_word(n: int) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return "час"
    if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14):
        return "часа"
    return "часов"


def format_age(last_seen_at: Optional[str], now: str) -> str:
    """Насколько давно цена в последний раз подтвердилась в кэше.

    Показываем это в дайджесте и алерте, чтобы владелец видел свежесть цифры
    ещё до перехода по ссылке — сам переход уже показал бы устаревшую цену.
    """
    if not last_seen_at:
        return ""
    seen = datetime.fromisoformat(last_seen_at.replace("Z", "+00:00"))
    current = datetime.fromisoformat(now.replace("Z", "+00:00"))
    minutes = max(0, int((current - seen).total_seconds() // 60))
    if minutes < _MINUTE_JUST_NOW_THRESHOLD:
        return "только что"
    if minutes < 60:
        return f"{minutes} {_minute_word(minutes)} назад"
    hours = minutes // 60
    return f"{hours} {_hour_word(hours)} назад"


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


def format_month_range(months: Sequence[str]) -> str:
    """Один месяц → 'октябрь 2026'; несколько подряд → 'октябрь–ноябрь 2026';
    разные годы → 'декабрь 2026 – январь 2027' (тире — типографское, U+2013)."""
    parsed = [(int(y), int(m)) for y, m in (month.split("-") for month in months)]
    first_year, first_month = parsed[0]
    last_year, last_month = parsed[-1]
    dash = "–"
    if len(parsed) == 1:
        return f"{MONTHS_RU[first_month]} {first_year}"
    if first_year == last_year:
        return f"{MONTHS_RU[first_month]}{dash}{MONTHS_RU[last_month]} {first_year}"
    return f"{MONTHS_RU[first_month]} {first_year} {dash} {MONTHS_RU[last_month]} {last_year}"


def render_digest(summaries: Sequence[RouteSummary], cfg, now: str) -> str:
    local = _local_date(now, cfg.timezone)
    header = f"✈️ <b>{local.strftime('%d.%m')}</b> · {format_month_range(cfg.departure_months)}"
    lines = [header, ""]

    for summary in summaries:
        route = cities.route(summary.origin, summary.destination)
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
        age = format_age(summary.last_seen_at, now)
        if age:
            line += f"  ({age})"
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
        route = cities.route(origin, destination)
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


@dataclass(frozen=True)
class DigestDecision:
    full: bool
    reasons: list[str]


def _price_key(origin: str, destination: str) -> str:
    return f"{origin}-{destination}"


def _current_prices(summaries: Sequence[RouteSummary]) -> dict[str, Optional[float]]:
    return {_price_key(s.origin, s.destination): s.best_landed for s in summaries}


def _arbitrage_key(finding: "arbitrage.Finding") -> str:
    """Ключ связки — тот же смысл, что и в arbitrage._flight_key, но с маршрутом:
    без origin/destination находки с разных маршрутов и одинаковой датой/рейсом
    схлопнулись бы в один ключ."""
    return (
        f"{finding.origin}-{finding.destination}-{finding.depart_date}-"
        f"{finding.airline}-{finding.transfers}"
    )


def _current_arbitrage(findings: Sequence["arbitrage.Finding"]) -> dict[str, float]:
    return {_arbitrage_key(f): f.saving_ratio for f in findings}


def _arbitrage_reason_new(finding: "arbitrage.Finding") -> str:
    route = cities.route(finding.origin, finding.destination)
    day = format_day(finding.depart_date)
    percent = round(finding.saving_ratio * 100)
    return f"новый арбитраж: {route} {day} (−{percent}%)"


def _arbitrage_reason_changed(finding: "arbitrage.Finding", last_ratio: float) -> str:
    route = cities.route(finding.origin, finding.destination)
    day = format_day(finding.depart_date)
    old_percent = round(last_ratio * 100)
    new_percent = round(finding.saving_ratio * 100)
    direction = "усилился" if finding.saving_ratio > last_ratio else "ослаб"
    return f"арбитраж {direction}: {route} {day} (−{old_percent}% → −{new_percent}%)"


def decide_digest(
    conn: sqlite3.Connection, cfg, summaries: Sequence[RouteSummary], now: str
) -> DigestDecision:
    """Решает, заслуживает ли сегодняшний день полной сводки или хватит одной строки.

    Полная сводка — единственный признак жизни системы (пока история короче
    пяти дней, вердиктов baseline ещё нет), поэтому сторож молчания и
    понедельничный якорь всегда перевешивают тишину в ценах.
    """
    reasons: list[str] = []

    switch = dead_man_switch(conn, now)
    if switch:
        reasons.append("сканер молчит")

    local = _local_date(now, cfg.timezone)
    if local.weekday() == 0:
        reasons.append("понедельник — недельный обзор")

    raw_last = store.get_meta(conn, "last_digest_prices")
    current_prices = _current_prices(summaries)
    if raw_last is None:
        reasons.append("первая сводка — прошлой ещё не было")
    else:
        last_prices: dict[str, Optional[float]] = json.loads(raw_last)
        for summary in summaries:
            key = _price_key(summary.origin, summary.destination)
            last_value = last_prices.get(key)
            current_value = current_prices[key]
            route = cities.route(summary.origin, summary.destination)
            if last_value is None and current_value is not None:
                reasons.append(f"{route}: появились данные")
            elif last_value is not None and current_value is None:
                reasons.append(f"{route}: данные пропали")
            elif (
                last_value is not None
                and current_value is not None
                and last_value != 0
            ):
                change = (current_value - last_value) / last_value
                if abs(change) >= cfg.digest_quiet_delta:
                    percent = round(change * 100)
                    sign = "−" if percent < 0 else "+"
                    reasons.append(f"{route}: цена изменилась на {sign}{abs(percent)}%")

    # Расхождение между рынками у этого владельца структурное — оно есть
    # почти всегда, поэтому «арбитраж существует» не новость. Новость — то,
    # что изменилось с прошлой сводки: появилась/исчезла связка или заметно
    # сдвинулась экономия.
    findings = arbitrage.find(conn, cfg, now=now)
    current_arbitrage = _current_arbitrage(findings)
    raw_last_arbitrage = store.get_meta(conn, "last_digest_arbitrage")
    last_arbitrage: dict[str, float] = (
        json.loads(raw_last_arbitrage) if raw_last_arbitrage else {}
    )
    for finding in findings:
        key = _arbitrage_key(finding)
        if key not in last_arbitrage:
            reasons.append(_arbitrage_reason_new(finding))
        else:
            change = abs(finding.saving_ratio - last_arbitrage[key])
            if change >= cfg.digest_quiet_delta:
                reasons.append(_arbitrage_reason_changed(finding, last_arbitrage[key]))
    if last_arbitrage and not current_arbitrage:
        reasons.append("арбитраж исчез")

    trends = render_trends(conn, cfg, now=now)
    if trends:
        reasons.append("есть тренды")

    last_report_at = store.get_meta(conn, "precision_reported_at")
    if precision_block(conn, now=now, last_report_at=last_report_at):
        reasons.append("готов отчёт о точности")

    return DigestDecision(full=bool(reasons), reasons=reasons)


def render_quiet(
    summaries: Sequence[RouteSummary],
    cfg,
    now: str,
    findings: Sequence["arbitrage.Finding"] = (),
) -> str:
    """Одна-две строки: подтверждение, что система жива, без полотна текста.

    Расхождение между рынками присутствует почти всегда и само по себе не
    новость (см. decide_digest), но это ценная информация — поэтому даже
    тихая сводка называет лучшую находку на сегодня, если она есть.
    """
    local = _local_date(now, cfg.timezone)
    parts = []
    freshest: Optional[str] = None
    for summary in summaries:
        route = cities.route(summary.origin, summary.destination)
        if summary.best_landed is None:
            parts.append(f"{route} {cities.PLACEHOLDER}")
            continue
        parts.append(f"{route} ${summary.best_landed:.0f}")
        if summary.last_seen_at and (freshest is None or summary.last_seen_at > freshest):
            freshest = summary.last_seen_at

    lines = [f"✈️ {local.strftime('%d.%m')} · тихо, цены на месте", " · ".join(parts)]
    age = format_age(freshest, now)
    if age:
        lines.append(f"проверено {age}")
    if findings:
        best = findings[0]
        route = cities.route(best.origin, best.destination)
        percent = round(best.saving_ratio * 100)
        lines.append(
            f"🔄 лучший арбитраж: {route} −{percent}% "
            f"({best.cheap_market} дешевле {best.expensive_market})"
        )
    return "\n".join(lines)


def build_digest_text(
    conn: sqlite3.Connection, cfg, now: str, force_full: bool = False
) -> str:
    summaries = build_all_summaries(conn, cfg, now=now)
    decision = decide_digest(conn, cfg, summaries, now=now)
    full = force_full or decision.full
    findings = arbitrage.find(conn, cfg, now=now)

    # Прошлые цены и арбитраж сравниваются со следующей сводкой независимо от
    # того, была ли эта сводка полной или короткой — иначе после тихого дня
    # сравнение окажется со сводкой недельной давности.
    store.set_meta(conn, "last_digest_prices", json.dumps(_current_prices(summaries)))
    store.set_meta(
        conn, "last_digest_arbitrage", json.dumps(_current_arbitrage(findings))
    )

    if force_full and not decision.full:
        LOG.info("дайджест: полная сводка по запросу (force_full)")
    else:
        LOG.info(
            "дайджест: %s — %s",
            "полная" if full else "короткая",
            "; ".join(decision.reasons) if decision.reasons else "изменений нет",
        )

    if not full:
        return render_quiet(summaries, cfg, now=now, findings=findings)

    blocks: list[str] = []

    switch = dead_man_switch(conn, now)
    if switch:
        blocks.append(switch)

    blocks.append(render_digest(summaries, cfg, now=now))

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
