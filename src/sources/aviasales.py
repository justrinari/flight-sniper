"""Aviasales Data API v3 — бесплатный кэш поисков.

Кэш отражает реальные поиски за ~48 часов. Это сигнал, а не оферта:
подтверждение цены делает слой Amadeus (v1.1).
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import Optional, Sequence

import requests

from src.models import PriceRecord

BASE_URL = "https://api.travelpayouts.com/aviasales/v3"
SOURCE = "aviasales_cache"

MARKET_DOMAIN = {
    "kg": "https://www.aviasales.kg",
    "ru": "https://www.aviasales.ru",
    "kz": "https://www.aviasales.kz",
}
DEFAULT_DOMAIN = "https://www.aviasales.com"


def full_link(link: str, market: str) -> str:
    if not link:
        return ""
    if link.startswith("http"):
        return link
    return MARKET_DOMAIN.get(market, DEFAULT_DOMAIN) + link


def _date_part(value: Optional[str]) -> Optional[str]:
    """'2026-10-12T10:15:00+06:00' -> '2026-10-12'."""
    if not value:
        return None
    return str(value)[:10]


def _normalize_expires_at(value) -> Optional[str]:
    """Приводит expires_at к канонической форме '2026-08-03T00:00:00Z'.

    Документация v3 это поле не описывает; legacy-эндпоинт отдавал Unix-таймстамп.
    Всё, что не распознали, обнуляем: лучше показать запись лишний раз,
    чем молча объявить протухшим весь кэш.
    """
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), tz=timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
    text = str(value).strip()
    if text.isdigit():
        return datetime.fromtimestamp(float(text), tz=timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
    try:
        moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_prices_for_dates(
    payload: dict, market: str, currency: Optional[str] = None
) -> list[PriceRecord]:
    records: list[PriceRecord] = []
    payload_currency = payload.get("currency")
    for item in payload.get("data") or []:
        try:
            depart_date = _date_part(item["departure_at"])
            if depart_date is None:
                continue
            return_date = _date_part(item.get("return_at"))
            date.fromisoformat(depart_date)
            if return_date is not None:
                date.fromisoformat(return_date)
            price_local = float(item["price"])
            if price_local <= 0:
                continue
            duration_to = item.get("duration_to")
            duration_to_min = int(duration_to) if duration_to is not None else None
            if duration_to_min is not None and duration_to_min <= 0:
                duration_to_min = None
            records.append(
                PriceRecord(
                    source=SOURCE,
                    origin=str(item["origin"]),
                    destination=str(item["destination"]),
                    market=market,
                    depart_date=depart_date,
                    return_date=return_date,
                    price_local=price_local,
                    currency=str(
                        item.get("currency") or payload_currency or currency or ""
                    ).lower(),
                    airline=str(item.get("airline") or ""),
                    transfers=int(item.get("transfers") or 0),
                    duration_min=int(item.get("duration") or 0),
                    duration_to_min=duration_to_min,
                    search_url=full_link(str(item.get("link") or ""), market),
                    expires_at=_normalize_expires_at(item.get("expires_at")),
                )
            )
        except (KeyError, TypeError, ValueError):
            # Кривая запись в кэше не должна ронять весь скан.
            continue
    return records


def filter_by_nights(
    records: Sequence[PriceRecord], nights_range: tuple[int, int]
) -> list[PriceRecord]:
    low, high = nights_range
    kept = []
    for record in records:
        nights = record.nights
        if nights is None or low <= nights <= high:
            kept.append(record)
    return kept


def filter_by_month(records: Sequence[PriceRecord], month: str) -> list[PriceRecord]:
    return [r for r in records if r.depart_date.startswith(month)]


def filter_by_transfer_time(
    records: Sequence[PriceRecord], max_transfer_hours: int
) -> list[PriceRecord]:
    """Отсекает связки с чрезмерной пересадкой.

    API не отдаёт длительность пересадки, поэтому сравниваем длительность плеча
    «туда» с самым коротким плечом на том же маршруте: разница сверх бюджета
    пересадки — это и есть сидение в аэропорту.
    """
    budget = max_transfer_hours * 60
    shortest: dict[tuple[str, str], int] = {}
    for record in records:
        if record.duration_to_min is None:
            continue
        key = (record.origin, record.destination)
        if key not in shortest or record.duration_to_min < shortest[key]:
            shortest[key] = record.duration_to_min

    kept = []
    for record in records:
        if record.duration_to_min is None:
            kept.append(record)
            continue
        baseline = shortest[(record.origin, record.destination)]
        if record.duration_to_min <= baseline + budget:
            kept.append(record)
    return kept


def filter_records(records: Sequence[PriceRecord], cfg) -> list[PriceRecord]:
    filtered = filter_by_month(records, cfg.departure_month)
    filtered = filter_by_nights(filtered, cfg.nights_range)
    return filter_by_transfer_time(filtered, cfg.max_transfer_hours)


LOG = logging.getLogger(__name__)
TIMEOUT = 30
DEFAULT_LIMIT = 100


class AviasalesError(RuntimeError):
    """Ошибка обращения к Data API."""


def _get(session: requests.Session, path: str, params: dict, token: str) -> dict:
    try:
        response = session.get(
            f"{BASE_URL}/{path}",
            params=params,
            headers={"X-Access-Token": token},
            timeout=TIMEOUT,
        )
        response.raise_for_status()
        payload = response.json()
    except requests.RequestException as exc:
        raise AviasalesError(f"{path}: {exc}") from exc
    except ValueError as exc:
        raise AviasalesError(f"{path}: ответ не является JSON") from exc
    if payload.get("success") is False:
        raise AviasalesError(f"{path}: {payload.get('error', 'unknown error')}")
    return payload


def fetch_prices_for_dates(
    session: requests.Session,
    token: str,
    origin: str,
    destination: str,
    market: str,
    currency: str,
    departure_at: str,
    return_at: Optional[str],
    one_way: bool = False,
    limit: int = DEFAULT_LIMIT,
) -> list[PriceRecord]:
    params = {
        "origin": origin,
        "destination": destination,
        "departure_at": departure_at,
        "currency": currency,
        "market": market,
        "sorting": "price",
        "direct": "false",
        "one_way": "true" if one_way else "false",
        "limit": limit,
    }
    if return_at and not one_way:
        params["return_at"] = return_at
    payload = _get(session, "prices_for_dates", params, token)
    return parse_prices_for_dates(payload, market=market, currency=currency)


def scan_all(session: requests.Session, token: str, cfg) -> tuple[list[PriceRecord], list[str]]:
    """Полный обход маршрутов × рынков × месяцев возврата.

    Возвращает (записи, тексты ошибок). Падение одного запроса не отменяет скан.
    """
    collected: dict[tuple, PriceRecord] = {}
    errors: list[str] = []
    return_months = [None] if cfg.one_way else cfg.return_months

    for origin, destination in cfg.routes():
        for market in cfg.markets:
            currency = cfg.currency_for(market)
            for return_at in return_months:
                try:
                    records = fetch_prices_for_dates(
                        session,
                        token,
                        origin,
                        destination,
                        market,
                        currency,
                        cfg.departure_month,
                        return_at,
                        one_way=cfg.one_way,
                    )
                except AviasalesError as exc:
                    message = f"{origin}->{destination} [{market}] {return_at}: {exc}"
                    LOG.warning("скан не удался: %s", message)
                    errors.append(message)
                    continue
                for record in filter_records(records, cfg):
                    key = (
                        record.market,
                        record.origin,
                        record.destination,
                        record.depart_date,
                        record.return_date,
                        record.airline,
                        record.transfers,
                        record.price_local,
                    )
                    collected.setdefault(key, record)
    return list(collected.values()), errors


def parse_grouped_prices(payload: dict, market: str, currency: str) -> list[PriceRecord]:
    """grouped_prices отдаёт словарь {дата: оффер} — раскладываем в плоский список."""
    data = payload.get("data") or {}
    items = list(data.values()) if isinstance(data, dict) else list(data)
    return parse_prices_for_dates({"data": items}, market=market, currency=currency)


def fetch_grouped_prices(
    session: requests.Session,
    token: str,
    origin: str,
    destination: str,
    market: str,
    currency: str,
    departure_at: str,
) -> list[PriceRecord]:
    params = {
        "origin": origin,
        "destination": destination,
        "departure_at": departure_at,
        "group_by": "departure_at",
        "currency": currency,
        "market": market,
        "direct": "false",
    }
    payload = _get(session, "grouped_prices", params, token)
    return parse_grouped_prices(payload, market=market, currency=currency)


def backfill(session: requests.Session, token: str, cfg) -> tuple[list[PriceRecord], list[str]]:
    """Однократный старт: минимальная цена на каждый день месяца по каждому рынку."""
    collected: list[PriceRecord] = []
    errors: list[str] = []
    for origin, destination in cfg.routes():
        for market in cfg.markets:
            currency = cfg.currency_for(market)
            try:
                records = fetch_grouped_prices(
                    session, token, origin, destination, market, currency, cfg.departure_month
                )
            except AviasalesError as exc:
                message = f"backfill {origin}->{destination} [{market}]: {exc}"
                LOG.warning("%s", message)
                errors.append(message)
                continue
            # Бэкфилл — историческая база, коридор ночей здесь не применяем:
            # grouped_prices отдаёт по одному самому дешёвому варианту на дату.
            collected.extend(filter_by_month(records, cfg.departure_month))
    return collected, errors
