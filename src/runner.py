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

from src import alerts, digest, fx, notify, rules, store
from src.config import Config
from src.sources import aviasales

LOG = logging.getLogger(__name__)
DEFAULT_DB = Path("data/history.sqlite")
RETENTION_DAYS = 120
ALERT_DROP_RATIO = 0.05  # повторный алерт только при падении ещё на 5%


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


def _process_alerts(conn, cfg, session, token, rates, now: str) -> int:
    """Ищет аномалии, подтверждает их и шлёт алерты. Возвращает число отправленных."""
    if store.get_meta(conn, "paused") == "1":
        return 0

    bot_token = os.environ.get("TG_BOT_TOKEN")
    chat_id = os.environ.get("TG_CHAT_ID")
    if not bot_token or not chat_id:
        LOG.warning("TG_BOT_TOKEN/TG_CHAT_ID не заданы — алерты пропущены")
        return 0

    sent = 0
    for candidate in rules.find_candidates(conn, cfg, now):
        if not store.should_alert(
            conn, candidate.route_date_key, candidate.landed_usd, ALERT_DROP_RATIO
        ):
            continue

        result = alerts.confirm(session, token, cfg, candidate, rates, now)
        text = alerts.render_alert(result)
        try:
            notify.send_message(session, bot_token, chat_id, text)
        except notify.TelegramError as exc:
            LOG.warning("не удалось отправить алерт по %s: %s", candidate.route_date_key, exc)
            continue

        store.record_alert(conn, candidate.route_date_key, candidate.landed_usd, now, result.level)
        sent += 1

    return sent


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
    sent_alerts = _process_alerts(conn, cfg, session, token, rates, now)
    removed = store.prune(conn, before=store.shift_days(now, -RETENTION_DAYS))
    conn.close()

    LOG.info(
        "скан: получено %d, вставлено %d, ошибок %d, алертов %d",
        len(records),
        inserted,
        len(errors),
        sent_alerts,
    )
    return {
        "fetched": len(records),
        "inserted": inserted,
        "pruned": removed,
        "errors": errors,
        "alerts": sent_alerts,
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


def run_digest(
    cfg: Config, db_path: Path = DEFAULT_DB, now: Optional[str] = None, session=None
) -> dict:
    now = now or store.utcnow()
    bot_token = require_env("TG_BOT_TOKEN")
    chat_id = require_env("TG_CHAT_ID")
    session = session or requests.Session()
    conn = _open(db_path)
    cfg = _effective_config(conn, cfg)

    summaries = digest.build_all_summaries(conn, cfg, now=now)
    text = digest.render_digest(summaries, cfg, now=now)
    conn.close()

    notify.send_message(session, bot_token, chat_id, text)
    return {"sent": True, "routes": len(summaries)}
