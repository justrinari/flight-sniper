"""Загрузка конфигурации flight-sniper.

config.yaml содержит дефолты и не перезаписывается воркфлоу. Изменяемое
состояние (например, порог из команды /threshold) живёт в таблице meta
и накладывается поверх через with_overrides().
"""

from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

REQUIRED_KEYS = (
    "origins",
    "destinations",
    "markets",
    "market_currency",
    "fx_markup",
    "departure_month",
    "return_months",
    "nights_range",
    "max_transfer_hours",
    "abs_threshold_usd",
    "anomaly_percentile",
    "yellow_delta",
)

# Какие ключи meta могут переопределять конфиг и как их приводить к типу.
OVERRIDABLE: dict[str, Any] = {
    "abs_threshold_usd": float,
    "yellow_delta": float,
    "anomaly_percentile": float,
    "cross_market_delta": float,
    "extra_routes": lambda value: [tuple(pair) for pair in json.loads(value)],
}


@dataclass(frozen=True)
class Config:
    origins: list[str]
    destinations: list[str]
    markets: list[str]
    market_currency: dict[str, str]
    cross_market_delta: float
    fx_markup: dict[str, float]
    departure_month: str
    return_months: list[str]
    trip_type: str
    nights_range: tuple[int, int]
    report_currency: str
    max_transfer_hours: int
    abs_threshold_usd: float
    anomaly_percentile: float
    yellow_delta: float
    baseline_window_days: int
    cache_ttl_hours: int
    digest_time: str
    timezone: str
    extra_routes: tuple[tuple[str, str], ...] = ()

    @classmethod
    def load(cls, path: str | Path) -> "Config":
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        missing = [key for key in REQUIRED_KEYS if key not in raw]
        if missing:
            raise ValueError(f"config.yaml: отсутствуют обязательные ключи: {', '.join(missing)}")
        nights = raw["nights_range"]
        return cls(
            origins=list(raw["origins"]),
            destinations=list(raw["destinations"]),
            markets=list(raw["markets"]),
            market_currency=dict(raw["market_currency"]),
            cross_market_delta=float(raw.get("cross_market_delta", 0.05)),
            fx_markup={str(k): float(v) for k, v in raw["fx_markup"].items()},
            departure_month=str(raw["departure_month"]),
            return_months=[str(m) for m in raw["return_months"]],
            trip_type=str(raw.get("trip_type", "round_trip")),
            nights_range=(int(nights[0]), int(nights[1])),
            report_currency=str(raw.get("report_currency", "usd")),
            max_transfer_hours=int(raw["max_transfer_hours"]),
            abs_threshold_usd=float(raw["abs_threshold_usd"]),
            anomaly_percentile=float(raw["anomaly_percentile"]),
            yellow_delta=float(raw["yellow_delta"]),
            baseline_window_days=int(raw.get("baseline_window_days", 30)),
            cache_ttl_hours=int(raw.get("cache_ttl_hours", 48)),
            digest_time=str(raw.get("digest_time", "09:00")),
            timezone=str(raw.get("timezone", "Asia/Bishkek")),
        )

    def routes(self) -> list[tuple[str, str]]:
        base = [(o, d) for o in self.origins for d in self.destinations]
        for pair in self.extra_routes:
            route = tuple(pair)
            if route not in base:
                base.append(route)
        return base

    def currency_for(self, market: str) -> str:
        try:
            return self.market_currency[market]
        except KeyError as exc:
            raise KeyError(f"нет валюты для рынка {market!r} в market_currency") from exc

    def markup_for(self, currency: str) -> float:
        return self.fx_markup.get(currency.lower(), self.fx_markup["default"])

    def with_overrides(self, overrides: dict[str, str]) -> "Config":
        changes = {}
        for key, caster in OVERRIDABLE.items():
            if key in overrides:
                changes[key] = caster(overrides[key])
        if "extra_routes" in changes:
            changes["extra_routes"] = tuple(tuple(pair) for pair in changes["extra_routes"])
        if not changes:
            return self
        return dataclasses.replace(self, **changes)

    @property
    def one_way(self) -> bool:
        return self.trip_type != "round_trip"
