"""Роутер команд Telegram-бота.

Своего сервера у проекта нет — только cron в GitHub Actions. Команды
забираются методом getUpdates при очередном запуске воркфлоу commands.yml
(раз в 15 минут), а не через webhook. Отсюда честное ограничение: ответ на
команду приходит с задержкой до 15 минут. Это push-first инструмент: сводки
и алерты приходят сами, команды — вспомогательное средство.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Optional

from src import digest, notify, store

LOG = logging.getLogger(__name__)

IATA_RE = re.compile(r"^[A-Z]{3}$")

HELP_TEXT = "\n".join(
    [
        "<b>flight-sniper</b> — монитор цен на авиабилеты.",
        "",
        "Сводки и алерты приходят сами — это push-first инструмент. Команды "
        "вспомогательные: сервера у бота нет, он забирает их при очередном "
        "запуске воркфлоу, поэтому ответ приходит с задержкой до 15 минут.",
        "",
        "Команды:",
        "/status — состояние сканера, число записей, порог, маршруты",
        "/now — сводка прямо сейчас",
        "/threshold [сумма] — показать или изменить порог BUY",
        "/pause — остановить алерты",
        "/resume — возобновить алерты",
        "/route add ORIGIN DEST — добавить маршрут, например /route add TAS HKT",
        "/bought — отметить, что билет из последнего алерта куплен",
        "/mismatch — отметить, что последний алерт не совпал с реальностью",
        "/help — эта справка",
    ]
)


def fetch_updates(
    session,
    bot_token: str,
    conn,
    allowed_chat_id: Optional[object] = None,
    timeout: int = 0,
) -> list[str]:
    """Забирает новые сообщения через getUpdates.

    offset двигается по ВСЕМ полученным обновлениям, включая чужие и
    нетекстовые — иначе застрявшее чужое сообщение возвращалось бы вечно.
    Возвращает тексты только от allowed_chat_id (если задан).
    """
    payload: dict = {"timeout": timeout}
    offset = store.get_meta(conn, "tg_offset")
    if offset is not None:
        payload["offset"] = int(offset)

    body = notify.call(session, bot_token, "getUpdates", payload)
    updates = body.get("result", [])

    texts: list[str] = []
    max_update_id: Optional[int] = None
    for update in updates:
        update_id = update.get("update_id")
        if update_id is not None and (max_update_id is None or update_id > max_update_id):
            max_update_id = update_id

        message = update.get("message")
        if message is None:
            LOG.info("обновление %s без message — пропущено", update_id)
            continue

        text = message.get("text")
        if text is None:
            LOG.info("обновление %s без текста — пропущено", update_id)
            continue

        chat_id = message.get("chat", {}).get("id")
        if allowed_chat_id is not None and str(chat_id) != str(allowed_chat_id):
            LOG.warning("сообщение от постороннего чата %s проигнорировано", chat_id)
            continue

        texts.append(text)

    if max_update_id is not None:
        store.set_meta(conn, "tg_offset", str(max_update_id + 1))

    return texts


def parse(text: str) -> tuple[Optional[str], list[str]]:
    """"/threshold 220" -> ("/threshold", ["220"]). Не команда -> (None, [])."""
    text = (text or "").strip()
    if not text.startswith("/"):
        return None, []

    parts = text.split()
    if not parts:
        return None, []

    command = parts[0].lower().split("@", 1)[0]
    if len(command) <= 1:
        return None, []

    return command, parts[1:]


def _status(conn, cfg, now: str) -> str:
    last_scan_at = store.get_meta(conn, "last_scan_at")
    scan_line = (
        f"Последний скан: {last_scan_at}"
        if last_scan_at
        else "Последний скан: ни разу не было"
    )
    rows = store.count_rows(conn)
    errors = store.get_meta(conn, "last_scan_errors", "0")
    routes = ", ".join(f"{o}→{d}" for o, d in cfg.routes())

    lines = [
        "<b>Статус flight-sniper</b>",
        scan_line,
        f"Записей в истории: {rows}",
        f"Ошибок последнего скана: {errors}",
        f"Текущий порог BUY: ${cfg.abs_threshold_usd:.0f}",
        f"Маршруты: {routes}" if routes else "Маршруты: не заданы",
    ]
    if store.get_meta(conn, "paused") == "1":
        lines.append("⏸ Бот на паузе — алерты не отправляются. /resume чтобы возобновить.")
    return "\n".join(lines)


def _threshold(conn, cfg, args: list[str]) -> str:
    if not args:
        return f"Текущий порог BUY: ${cfg.abs_threshold_usd:.0f}"

    raw = args[0].replace(",", ".")
    try:
        value = float(raw)
    except ValueError:
        return "Не понял число. Формат: /threshold 220"

    if value <= 0:
        return "Порог должен быть положительным числом. Формат: /threshold 220"

    store.set_meta(conn, "abs_threshold_usd", str(value))
    return f"Порог BUY обновлён: ${value:.0f}"


def _route_add(conn, args: list[str]) -> str:
    if len(args) != 2:
        return "Формат: /route add ORIGIN DEST, например /route add TAS HKT"

    origin, destination = args[0].upper(), args[1].upper()
    if not IATA_RE.match(origin) or not IATA_RE.match(destination):
        return "Коды аэропортов — три латинские буквы, например /route add TAS HKT"

    raw = store.get_meta(conn, "extra_routes")
    routes = [list(pair) for pair in json.loads(raw)] if raw else []
    pair = [origin, destination]
    if pair in routes:
        return f"Маршрут {origin}→{destination} уже есть в списке."

    routes.append(pair)
    store.set_meta(conn, "extra_routes", json.dumps(routes))
    return f"Маршрут {origin}→{destination} добавлен."


def _feedback(conn, feedback: str) -> str:
    ok = store.set_last_feedback(conn, feedback)
    if not ok:
        return "Алертов ещё не было — отмечать нечего."
    label = "куплен" if feedback == "bought" else "не совпал с реальностью"
    return f"Записал: последний алерт — {label}. Спасибо!"


def handle(conn, cfg, command: str, args: list[str], now: str) -> str:
    """Роутер команд. cfg должен быть уже эффективным (с наложенными meta-overrides)."""
    if command in ("/help", "/start"):
        return HELP_TEXT
    if command == "/status":
        return _status(conn, cfg, now)
    if command == "/now":
        return digest.build_digest_text(conn, cfg, now=now)
    if command == "/threshold":
        return _threshold(conn, cfg, args)
    if command == "/pause":
        store.set_meta(conn, "paused", "1")
        return "⏸ Бот на паузе — алерты не отправляются. /resume чтобы возобновить."
    if command == "/resume":
        store.set_meta(conn, "paused", "0")
        return "▶️ Бот снова активен — алерты возобновлены."
    if command == "/route":
        if args and args[0].lower() == "add":
            return _route_add(conn, args[1:])
        return "Формат: /route add ORIGIN DEST, например /route add TAS HKT"
    if command == "/bought":
        return _feedback(conn, "bought")
    if command == "/mismatch":
        return _feedback(conn, "mismatch")

    return "Не знаю такую команду. Наберите /help — там список доступных."
