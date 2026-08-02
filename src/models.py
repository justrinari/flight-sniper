"""Единый тип обмена между слоями: источник → обогащение → хранилище → правила."""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from datetime import date
from typing import Optional


@dataclass(frozen=True)
class PriceRecord:
    source: str  # 'aviasales_cache' | 'amadeus'
    origin: str
    destination: str
    market: str
    depart_date: str  # YYYY-MM-DD
    return_date: Optional[str]  # YYYY-MM-DD | None для one_way
    price_local: float
    currency: str
    airline: str
    transfers: int
    duration_min: int
    duration_to_min: Optional[int] = None  # длительность плеча «туда», для фильтра пересадок
    search_url: str = ""
    gate: Optional[str] = None  # продавец, у которого найдена цена (ОТА или а/к)
    scanned_at: Optional[str] = None  # ISO UTC, проставляется при вставке
    fx_rate: Optional[float] = None  # USD за 1 единицу currency
    landed_usd: Optional[float] = None
    expires_at: Optional[str] = None

    @property
    def nights(self) -> Optional[int]:
        if not self.return_date:
            return None
        return (date.fromisoformat(self.return_date) - date.fromisoformat(self.depart_date)).days

    @property
    def route_key(self) -> str:
        return f"{self.origin}-{self.destination}-{self.depart_date}-{self.return_date or ''}"

    def with_landed(self, fx_rate: float, landed_usd: float) -> "PriceRecord":
        return dataclasses.replace(self, fx_rate=fx_rate, landed_usd=landed_usd)
