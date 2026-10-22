"""Загрузка конфигурации flight-sniper.

config.yaml содержит дефолты и не перезаписывается воркфлоу. Изменяемое
состояние (например, порог из команды /threshold) живёт в таблице meta
и накладывается поверх через with_overrides().
"""

from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Optional

import yaml

REQUIRED_KEYS = (
    "origins",
    "destinations",
    "markets",
    "market_currency",
    "fx_markup",
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
    "digest_quiet_delta": float,
    "vanished_delta": float,
    "min_offer_lifetime_minutes": float,
    "extra_routes": lambda value: [tuple(pair) for pair in json.loads(value)],
}


@dataclass(frozen=True)
class TripConfig:
    """Поездка из нескольких перелётов.

    `stops` — остановки по порядку, в каждой — альтернативные аэропорты.
    Бот следит за всеми парами соседних остановок и сам выбирает, через какие
    города маршрут дешевле.
    """

    stops: tuple[tuple[str, ...], ...]
    latest_start: str  # YYYY-MM-DD, вылет первого перелёта не позже
    max_total_days: int  # от вылета первого до вылета последнего перелёта
    min_stay_days: int  # минимум дней между соседними перелётами
    alert_total_usd: float
    top_n: int = 3

    @classmethod
    def parse(cls, raw: dict) -> "TripConfig":
        stops_raw = raw.get("stops") or []
        if len(stops_raw) < 2:
            raise ValueError("config.yaml: trip.stops — нужно минимум две остановки")
        stops = []
        for index, stop in enumerate(stops_raw):
            items = stop if isinstance(stop, list) else [stop]
            codes = tuple(str(code).strip().upper() for code in items)
            if not codes or any(not code for code in codes):
                raise ValueError(f"config.yaml: trip.stops[{index}] пустая")
            stops.append(codes)

        latest_start = str(raw.get("latest_start", ""))
        try:
            date.fromisoformat(latest_start)
        except ValueError as exc:
            raise ValueError(
                f"config.yaml: trip.latest_start должна быть датой YYYY-MM-DD, а не {latest_start!r}"
            ) from exc

        min_stay_days = int(raw.get("min_stay_days", 1))
        if min_stay_days < 1:
            raise ValueError("config.yaml: trip.min_stay_days должно быть не меньше 1")

        return cls(
            stops=tuple(stops),
            latest_start=latest_start,
            max_total_days=int(raw["max_total_days"]),
            min_stay_days=min_stay_days,
            alert_total_usd=float(raw["alert_total_usd"]),
            top_n=int(raw.get("top_n", 3)),
        )

    def pairs(self) -> list[tuple[str, str]]:
        """Все пары аэропортов соседних остановок, в порядке маршрута."""
        result: list[tuple[str, str]] = []
        for current, following in zip(self.stops, self.stops[1:]):
            for origin in current:
                for destination in following:
                    pair = (origin, destination)
                    if origin != destination and pair not in result:
                        result.append(pair)
        return result


@dataclass(frozen=True)
class Config:
    origins: list[str]
    destinations: list[str]
    markets: list[str]
    market_currency: dict[str, str]
    cross_market_delta: float
    fx_markup: dict[str, float]
    departure_months: list[str]
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
    digest_quiet_delta: float = 0.05
    # Насколько лучшая свежая цена на дату должна быть хуже пропавшей из
    # кэша, чтобы об этом стоило сказать.
    vanished_delta: float = 0.07
    # Сколько минут оффер должен прожить, чтобы система считала его
    # существующим — в статистике, в алертах и в сводке. Проходы скана идут
    # раз в 14 минут, так что оффер, увиденный хотя бы дважды, этот порог
    # проходит, а мелькнувшая в одном проходе запись кэша — нет.
    min_offer_lifetime_minutes: float = 10.0
    # Порог BUY по конкретному маршруту, ключ "ORIGIN-DESTINATION". Общий
    # abs_threshold_usd — фон для маршрутов, которых здесь нет.
    route_thresholds_usd: dict[str, float] = dataclasses.field(default_factory=dict)
    # Режим поездки из нескольких перелётов. Если задан, маршруты берутся из
    # остановок поездки, а origins/destinations не используются.
    trip: Optional[TripConfig] = None

    @classmethod
    def load(cls, path: str | Path) -> "Config":
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        missing = [key for key in REQUIRED_KEYS if key not in raw]
        if "departure_months" not in raw and "departure_month" not in raw:
            missing.append("departure_months")
        if missing:
            raise ValueError(f"config.yaml: отсутствуют обязательные ключи: {', '.join(missing)}")
        nights = raw["nights_range"]
        # Обратная совместимость: старый конфиг мог задавать один месяц строкой
        # через departure_month — оборачиваем его в список и работаем дальше.
        raw_months = raw.get("departure_months", raw.get("departure_month"))
        if isinstance(raw_months, str):
            departure_months = [raw_months]
        else:
            departure_months = [str(m) for m in raw_months]
        return cls(
            origins=list(raw["origins"]),
            destinations=list(raw["destinations"]),
            markets=list(raw["markets"]),
            market_currency=dict(raw["market_currency"]),
            cross_market_delta=float(raw.get("cross_market_delta", 0.05)),
            fx_markup={str(k): float(v) for k, v in raw["fx_markup"].items()},
            departure_months=departure_months,
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
            digest_quiet_delta=float(raw.get("digest_quiet_delta", 0.05)),
            vanished_delta=float(raw.get("vanished_delta", 0.07)),
            min_offer_lifetime_minutes=float(
                raw.get("min_offer_lifetime_minutes", 10.0)
            ),
            route_thresholds_usd={
                str(k): float(v)
                for k, v in (raw.get("route_thresholds_usd") or {}).items()
            },
            trip=TripConfig.parse(raw["trip"]) if raw.get("trip") else None,
        )

    def routes(self) -> list[tuple[str, str]]:
        base = (
            self.trip.pairs()
            if self.trip is not None
            else [(o, d) for o in self.origins for d in self.destinations]
        )
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

    def threshold_for(self, origin: str, destination: str) -> float:
        """Порог BUY для маршрута.

        Единый порог на все маршруты бесполезен: $250 недостижимы для Бали и
        для Бишкека, поэтому абсолютная ветка светофора никогда не срабатывала
        (замер на живой базе за 02–10.08.2026: ноль срабатываний).
        """
        return self.route_thresholds_usd.get(
            f"{origin}-{destination}", self.abs_threshold_usd
        )

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

    @property
    def departure_month(self) -> str:
        """Первый месяц — для мест, где нужен один (например, fallback-даты)."""
        return self.departure_months[0]
