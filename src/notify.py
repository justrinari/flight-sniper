"""Транспорт до Telegram. Форматированием занимается digest.py."""

from __future__ import annotations

from typing import Iterable

import requests

API = "https://api.telegram.org/bot{token}/{method}"
TIMEOUT = 30
TELEGRAM_LIMIT = 4096
SAFE_LIMIT = 3800  # запас на HTML-разметку и склейку


class TelegramError(RuntimeError):
    """Telegram отверг запрос или не ответил."""


def call(session: requests.Session, token: str, method: str, payload: dict) -> dict:
    try:
        response = session.post(
            API.format(token=token, method=method), json=payload, timeout=TIMEOUT
        )
        body = response.json()
    except requests.RequestException as exc:
        raise TelegramError(f"{method}: {exc}") from exc
    except ValueError as exc:
        raise TelegramError(f"{method}: ответ не является JSON") from exc
    if not body.get("ok"):
        raise TelegramError(f"{method}: {body.get('description', 'unknown error')}")
    return body


def split_message(text: str, limit: int = SAFE_LIMIT) -> list[str]:
    """Режет текст по границам строк, а слишком длинную строку — по символам.

    Разделитель между строками резервируется всегда (даже для первой строки
    чанка), поэтому итоговый чанк гарантированно короче limit — с запасом,
    но без риска склеить чанк, который при отправке в Telegram окажется
    длиннее заявленного лимита.
    """
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    for line in text.split("\n"):
        while len(line) > limit:
            if current:
                chunks.append("\n".join(current))
                current, current_len = [], 0
            chunks.append(line[:limit])
            line = line[limit:]
        extra = len(line) + 1
        if current_len + extra >= limit:
            if current:
                chunks.append("\n".join(current))
            current, current_len = [line], len(line) + 1
        else:
            current.append(line)
            current_len += extra
    if current:
        chunks.append("\n".join(current))
    return [chunk for chunk in chunks if chunk]


def send_message(
    session: requests.Session,
    token: str,
    chat_id: str,
    text: str,
    parse_mode: str = "HTML",
    disable_preview: bool = True,
) -> None:
    for chunk in split_message(text):
        call(
            session,
            token,
            "sendMessage",
            {
                "chat_id": chat_id,
                "text": chunk,
                "parse_mode": parse_mode,
                "disable_web_page_preview": disable_preview,
            },
        )


def send_all(
    session: requests.Session, token: str, chat_id: str, texts: Iterable[str]
) -> None:
    for text in texts:
        send_message(session, token, chat_id, text)
