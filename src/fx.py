"""Курсы валют и приведение цен к landed USD.

fx_rate хранится в каждой записи, чтобы аномалию можно было отличить от
движения курса: цена падает в локальной валюте или доллар просто вырос.
"""

from __future__ import annotations

from typing import Iterable, Optional

import requests

from src.models import PriceRecord

FX_URL = "https://open.er-api.com/v6/latest/USD"
TIMEOUT = 20


class FxError(RuntimeError):
    """Курс недоступен или валюта неизвестна."""


def fetch_usd_rates(session: requests.Session, url: str = FX_URL) -> dict[str, float]:
    """Возвращает {валюта: сколько единиц валюты в 1 USD}, ключи в нижнем регистре."""
    try:
        response = session.get(url, timeout=TIMEOUT)
        response.raise_for_status()
        payload = response.json()
    except requests.RequestException as exc:
        raise FxError(f"курс недоступен: {exc}") from exc
    if payload.get("result") != "success":
        raise FxError(f"курс недоступен: {payload.get('error-type', 'unknown')}")
    return {str(k).lower(): float(v) for k, v in payload["rates"].items()}


def usd_per_unit(rates: dict[str, float], currency: str) -> float:
    """USD за одну единицу валюты — множитель для price_local."""
    key = currency.lower()
    if key not in rates:
        raise FxError(f"нет курса для валюты {key}")
    rate = rates[key]
    if rate <= 0:
        raise FxError(f"некорректный курс для {key}: {rate}")
    return 1.0 / rate


def landed_usd(price_local: float, fx_rate: float, markup: float) -> float:
    return price_local * fx_rate * (1.0 + markup)


def enrich(
    records: Iterable[PriceRecord], rates: dict[str, float], cfg: Optional[object]
) -> list[PriceRecord]:
    """Проставляет fx_rate и landed_usd. Записи с неизвестной валютой отбрасывает."""
    enriched: list[PriceRecord] = []
    for record in records:
        try:
            rate = usd_per_unit(rates, record.currency)
        except FxError:
            continue
        markup = cfg.markup_for(record.currency) if cfg is not None else 0.0
        enriched.append(record.with_landed(rate, landed_usd(record.price_local, rate, markup)))
    return enriched
