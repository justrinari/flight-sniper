"""Связывание слоёв: чтение окружения, оркестрация одного рана.

Логики здесь нет — только последовательность вызовов и работа с БД,
чтобы monitor.py оставался тонким CLI.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

import requests

from src import alerts, commands, digest, fx, notify, rules, store, trip_digest
from src.config import Config
from src.sources import aviasales

LOG = logging.getLogger(__name__)
DEFAULT_DB = Path("data/history.sqlite")
RETENTION_DAYS = 120
ALERT_DROP_RATIO = 0.05  # повторный алерт только при падении ещё на 5%
# Кандидатов стало на порядок больше с триггером по дате вылета: каждая из
# трёх десятков дат месяца может дать свой. Ран без предела выстрелил бы
# десятками сообщений подряд и таким же числом запросов подтверждения.
# Отрезается хвост: кандидаты отсортированы по цене, уходят самые дешёвые.
MAX_ALERTS_PER_RUN = 5
# Ключ дедупа алертов режима поездки: маршрут один, поэтому и ключ один.
TRIP_ALERT_KEY = "trip"


class ConfigurationError(RuntimeError):
    """Не хватает переменной окружения."""


def require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise ConfigurationError(
            f"переменная окружения {name} не задана — проверь GitHub Secrets"
        )
    return value


def _open(db_path: Path):
    conn = store.connect(db_path)
    store.init_schema(conn)
    return conn


def _effective_config(conn, cfg: Config) -> Config:
    return cfg.with_overrides(store.all_meta(conn))


def _process_trip_alert(conn, cfg, session, bot_token: str, chat_id: str, now: str) -> int:
    """Алерт, когда лучший маршрут поездки дешевле порога. Возвращает число отправленных."""
    itineraries, _ = trip_digest.load_itineraries(conn, cfg, now)
    if not itineraries:
        return 0
    best = itineraries[0]
    threshold = cfg.trip.alert_total_usd
    if best.total_usd >= threshold:
        return 0
    if not store.should_alert(conn, TRIP_ALERT_KEY, best.total_usd, ALERT_DROP_RATIO):
        return 0

    text = trip_digest.render_alert(best, threshold, now)
    try:
        notify.send_message(session, bot_token, chat_id, text)
    except notify.TelegramError as exc:
        LOG.warning("не удалось отправить алерт поездки: %s", exc)
        return 0
    store.record_alert(conn, TRIP_ALERT_KEY, best.total_usd, now, rules.GREEN)
    return 1


def _process_alerts(conn, cfg, session, token, rates, now: str) -> tuple[int, int]:
    """Ищет аномалии, подтверждает их и шлёт алерты.

    Возвращает (отправлено, отброшено пределом).
    """
    if store.get_meta(conn, "paused") == "1":
        return 0, 0

    bot_token = os.environ.get("TG_BOT_TOKEN")
    chat_id = os.environ.get("TG_CHAT_ID")
    if not bot_token or not chat_id:
        LOG.warning("TG_BOT_TOKEN/TG_CHAT_ID не заданы — алерты пропущены")
        return 0, 0

    if cfg.trip is not None:
        # Алерты по 30 отдельным перелётам бессмысленны: важна сумма маршрута.
        return _process_trip_alert(conn, cfg, session, bot_token, chat_id, now), 0

    candidates = rules.find_candidates(conn, cfg, now)
    allowed, skipped = candidates[:MAX_ALERTS_PER_RUN], candidates[MAX_ALERTS_PER_RUN:]
    if skipped:
        LOG.warning(
            "предел %d алертов за ран: отложено %d находок (самая дешёвая из них %s $%.0f)",
            MAX_ALERTS_PER_RUN,
            len(skipped),
            skipped[0].route_date_key,
            skipped[0].landed_usd,
        )

    sent = 0
    for candidate in allowed:
        if not store.should_alert(
            conn, candidate.route_date_key, candidate.landed_usd, ALERT_DROP_RATIO
        ):
            continue

        result = alerts.confirm(session, token, cfg, candidate, rates, now)
        text = alerts.render_alert(result, now)
        try:
            notify.send_message(session, bot_token, chat_id, text)
        except notify.TelegramError as exc:
            LOG.warning("не удалось отправить алерт по %s: %s", candidate.route_date_key, exc)
            continue

        store.record_alert(conn, candidate.route_date_key, candidate.landed_usd, now, result.level)
        sent += 1

    return sent, len(skipped)


def run_scan(
    cfg: Config, db_path: Path = DEFAULT_DB, now: Optional[str] = None, session=None
) -> dict:
    now = now or store.utcnow()
    token = require_env("TP_TOKEN")
    session = session or requests.Session()
    conn = _open(db_path)
    cfg = _effective_config(conn, cfg)

    rates = fx.fetch_usd_rates(session)
    records, errors = aviasales.scan_all(session, token, cfg)
    enriched = fx.enrich(records, rates, cfg)
    inserted = store.insert_prices(conn, enriched, now=now)

    if records:
        store.set_meta(conn, "last_scan_at", now)
    store.set_meta(conn, "last_scan_errors", str(len(errors)))
    sent_alerts, skipped_alerts = _process_alerts(conn, cfg, session, token, rates, now)
    removed = store.prune(conn, before=store.shift_days(now, -RETENTION_DAYS))
    conn.close()

    LOG.info(
        "скан: получено %d, вставлено %d, ошибок %d, алертов %d (отложено %d)",
        len(records),
        inserted,
        len(errors),
        sent_alerts,
        skipped_alerts,
    )
    return {
        "fetched": len(records),
        "inserted": inserted,
        "pruned": removed,
        "errors": errors,
        "alerts": sent_alerts,
        "alerts_skipped": skipped_alerts,
    }


def run_backfill(
    cfg: Config,
    db_path: Path = DEFAULT_DB,
    now: Optional[str] = None,
    force: bool = False,
    session=None,
) -> dict:
    now = now or store.utcnow()
    token = require_env("TP_TOKEN")
    session = session or requests.Session()
    conn = _open(db_path)
    cfg = _effective_config(conn, cfg)

    if store.count_rows(conn) > 0 and not force:
        conn.close()
        LOG.info("бэкфилл пропущен: база не пуста")
        return {"skipped": True, "inserted": 0, "errors": []}

    rates = fx.fetch_usd_rates(session)
    records, errors = aviasales.backfill(session, token, cfg)
    enriched = fx.enrich(records, rates, cfg)
    inserted = store.insert_prices(conn, enriched, now=now)
    store.set_meta(conn, "backfilled_at", now)
    conn.close()
    return {"skipped": False, "inserted": inserted, "errors": errors}


def _refresh_best_prices(conn, cfg, session, token, rates, now: str) -> int:
    """Перепроверяет лучшую цену каждого маршрута точечным запросом.

    Дайджест уходит раз в сутки, поэтому несколько лишних запросов ничего не
    стоят, а цифра в утреннем сообщении обязана быть настоящей: точечный
    запрос по дате лучшей свежей записи одновременно освежает её last_seen_at
    (если оффер жив) и добавляет новые ценовые точки в базу.

    Возвращает число вставленных записей. Ошибки не фатальны: сводка должна
    уйти даже если освежение не удалось.
    """
    inserted = 0
    for origin, destination in cfg.routes():
        fresh = [
            row
            for row in store.fresh_prices(
                conn,
                origin,
                destination,
                now=now,
                ttl_hours=cfg.cache_ttl_hours,
                min_lifetime_minutes=cfg.min_offer_lifetime_minutes,
            )
            if row["landed_usd"] is not None
        ]
        if not fresh:
            continue

        best = min(fresh, key=lambda row: row["landed_usd"])
        try:
            records = aviasales.fetch_prices_for_dates(
                session,
                token,
                origin=origin,
                destination=destination,
                market=best["market"],
                currency=best["currency"],
                departure_at=best["depart_date"],
                return_at=best["return_date"],
                one_way=cfg.one_way,
                limit=30,
            )
        except aviasales.AviasalesError as exc:
            LOG.warning("освежение лучшей цены %s->%s не удалось: %s", origin, destination, exc)
            continue

        enriched = fx.enrich(records, rates, cfg)
        inserted += store.insert_prices(conn, enriched, now=now)

    return inserted


def _refresh_trip_legs(conn, cfg, session, token, rates, now: str) -> int:
    """Перепроверяет каждый перелёт лучшего маршрута точечным запросом.

    Та же идея, что в _refresh_best_prices: цифра в утренней сводке должна
    быть настоящей. Четыре запроса в сутки. Ошибки не фатальны.
    """
    itineraries, _ = trip_digest.load_itineraries(conn, cfg, now)
    if not itineraries:
        return 0
    inserted = 0
    for leg in itineraries[0].legs:
        try:
            records = aviasales.fetch_prices_for_dates(
                session,
                token,
                origin=leg.origin,
                destination=leg.destination,
                market=leg.market,
                currency=leg.currency,
                departure_at=leg.depart_date,
                return_at=None,
                one_way=True,
                limit=30,
            )
        except aviasales.AviasalesError as exc:
            LOG.warning(
                "освежение перелёта %s->%s %s не удалось: %s",
                leg.origin,
                leg.destination,
                leg.depart_date,
                exc,
            )
            continue
        enriched = fx.enrich(aviasales.filter_records(records, cfg), rates, cfg)
        inserted += store.insert_prices(conn, enriched, now=now)
    return inserted


def run_digest(
    cfg: Config, db_path: Path = DEFAULT_DB, now: Optional[str] = None, session=None
) -> dict:
    now = now or store.utcnow()
    bot_token = require_env("TG_BOT_TOKEN")
    chat_id = require_env("TG_CHAT_ID")
    session = session or requests.Session()
    conn = _open(db_path)
    cfg = _effective_config(conn, cfg)

    refreshed = 0
    try:
        token = require_env("TP_TOKEN")
        rates = fx.fetch_usd_rates(session)
    except (ConfigurationError, fx.FxError) as exc:
        LOG.warning("освежение лучших цен перед сводкой пропущено: %s", exc)
    else:
        refresh = _refresh_trip_legs if cfg.trip is not None else _refresh_best_prices
        refreshed = refresh(conn, cfg, session, token, rates, now)

    routes = len(cfg.routes())
    if cfg.trip is not None:
        result = trip_digest.build_text(conn, cfg, now=now)
        notify.send_message(session, bot_token, chat_id, result.text)
        if result.best_total is not None:
            store.set_meta(conn, trip_digest.PREV_KEY, f"{result.best_total:.2f}")
        conn.close()
        return {"sent": True, "routes": routes, "refreshed": refreshed}

    text = digest.build_digest_text(conn, cfg, now=now)
    conn.close()

    notify.send_message(session, bot_token, chat_id, text)
    return {"sent": True, "routes": routes, "refreshed": refreshed}


def run_commands(
    cfg: Config, db_path: Path = DEFAULT_DB, now: Optional[str] = None, session=None
) -> dict:
    """Забирает команды через getUpdates и отвечает на каждую распознанную.

    Для каждой команды берётся свежий _effective_config: порог (или другой
    override) мог измениться предыдущей командой в этой же пачке.
    """
    now = now or store.utcnow()
    bot_token = require_env("TG_BOT_TOKEN")
    chat_id = require_env("TG_CHAT_ID")
    session = session or requests.Session()
    conn = _open(db_path)

    texts = commands.fetch_updates(session, bot_token, conn, allowed_chat_id=chat_id)

    handled = 0
    for text in texts:
        command, args = commands.parse(text)
        if command is None:
            continue

        effective_cfg = _effective_config(conn, cfg)
        reply = commands.handle(conn, effective_cfg, command, args, now)
        try:
            notify.send_message(session, bot_token, chat_id, reply)
        except notify.TelegramError as exc:
            LOG.warning("не удалось отправить ответ на %s: %s", command, exc)
            continue
        handled += 1

    conn.close()
    LOG.info("команды: обработано %d", handled)
    return {"handled": handled}
