"""IATA-коды городов → человекочитаемые названия.

По образцу src/airlines.py: неизвестный код читается как код, и это нормально.

Отдельный повод для этого модуля: в справочнике Travelpayouts Бишкек — это
BSZ, а не привычный FRU (их API на FRU отвечает "unknown location code").
Код BSZ сам по себе ни о чём не говорит, поэтому в дайджесте нужны названия.
"""

from __future__ import annotations

from typing import Optional

NAMES = {
    # Точки вылета
    "BSZ": "Бишкек",
    "FRU": "Бишкек",  # на случай, если код всё же встретится
    "ALA": "Алматы",
    "NQZ": "Астана",
    "TSE": "Астана",  # старый код аэропорта Астаны до переименования
    "TAS": "Ташкент",
    # Целевые направления
    "HKT": "Пхукет",
    "DPS": "Бали",
    # Хабы и пересадки, встречающиеся на маршрутах FRU/ALA → HKT/DPS
    "KUL": "Куала-Лумпур",
    "BKK": "Бангкок",
    "DMK": "Бангкок (Дон Мыанг)",
    "DEL": "Дели",
    "BOM": "Мумбаи",
    "DXB": "Дубай",
    "SHJ": "Шарджа",
    "AUH": "Абу-Даби",
    "IST": "Стамбул",
    "URC": "Урумчи",
    "PEK": "Пекин",
    "PKX": "Пекин",
    "CAN": "Гуанчжоу",
    "PVG": "Шанхай",
    "ICN": "Сеул",
    "SGN": "Хошимин",
    "HAN": "Ханой",
    "SIN": "Сингапур",
    "CGK": "Джакарта",
    "MOW": "Москва",
    "LED": "Санкт-Петербург",
}

PLACEHOLDER = "—"


def name(code: Optional[str]) -> str:
    """IATA-код → название города. Неизвестный код возвращается как есть."""
    if not code:
        return PLACEHOLDER
    return NAMES.get(code.strip().upper(), code.strip().upper())


def route(origin: Optional[str], destination: Optional[str]) -> str:
    """'BSZ', 'HKT' → 'Бишкек → Пхукет'."""
    return f"{name(origin)} → {name(destination)}"
