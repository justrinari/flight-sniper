"""Aviasales Data API v3 — бесплатный кэш поисков.

Кэш отражает реальные поиски за ~48 часов. Это сигнал, а не оферта:
подтверждение цены делает слой Amadeus (v1.1).
"""

from __future__ import annotations

from typing import Optional

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


def parse_prices_for_dates(
    payload: dict, market: str, currency: Optional[str] = None
) -> list[PriceRecord]:
    records: list[PriceRecord] = []
    for item in payload.get("data") or []:
        try:
            depart_date = _date_part(item["departure_at"])
            if depart_date is None:
                continue
            records.append(
                PriceRecord(
                    source=SOURCE,
                    origin=str(item["origin"]),
                    destination=str(item["destination"]),
                    market=market,
                    depart_date=depart_date,
                    return_date=_date_part(item.get("return_at")),
                    price_local=float(item["price"]),
                    currency=str(item.get("currency") or currency or "").lower(),
                    airline=str(item.get("airline") or ""),
                    transfers=int(item.get("transfers") or 0),
                    duration_min=int(item.get("duration") or 0),
                    duration_to_min=(
                        int(item["duration_to"]) if item.get("duration_to") is not None else None
                    ),
                    search_url=full_link(str(item.get("link") or ""), market),
                    expires_at=item.get("expires_at"),
                )
            )
        except (KeyError, TypeError, ValueError):
            # Кривая запись в кэше не должна ронять весь скан.
            continue
    return records
