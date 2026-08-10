"""Статистика и светофор.

Baseline считается на двух уровнях сразу: по маршруту в целом (все даты месяца
вместе) и отдельно по каждой дате вылета. Изначально был только маршрутный, и
это оказалось дырой: замер на живой базе за 02–10.08.2026 дал шесть алертов и
ни одного по ALA-HKT, хотя именно там были две лучшие находки месяца — $280 на
17.10 при обычных для этой даты $356 и $271 на 30.10. На фоне минимума всего
маршрута ($272) обе цены выглядят рядовыми; аномальны они только внутри своей
даты. Датный baseline требует MIN_SAMPLE дней наблюдений по этой дате, поэтому
на маршрутах с редкими данными он просто молчит.

Важно, ЧТО именно попадает в выборку. Сравнивать сегодняшнюю минимальную цену
со всеми ценами подряд бессмысленно: минимум по определению лежит ниже любого
перцентиля того же распределения, и светофор всегда горел бы зелёным. Поэтому
выборка — это история ДНЕВНЫХ МИНИМУМОВ (см. daily_minimums): вопрос, на который
отвечает светофор, звучит как «дешевле ли сегодняшняя лучшая цена, чем бывала
лучшая цена в прошлые дни».
"""

from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass
from datetime import date
from statistics import median as _median
from typing import Optional, Sequence

from src import store

MIN_SAMPLE = 5

_DAILY_MIN_COLUMNS = ("landed_usd", "price_local")

GREEN = "green"
YELLOW = "yellow"
GRAY = "gray"


@dataclass(frozen=True)
class Baseline:
    n: int
    median: float
    minimum: float
    anomaly_threshold: float  # значение перцентиля anomaly_percentile


def percentile(values: Sequence[float], p: float) -> float:
    """Линейная интерполяция между соседними точками, как numpy.percentile."""
    if not values:
        raise ValueError("percentile() на пустой выборке")
    ordered = sorted(float(v) for v in values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * (p / 100.0)
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[int(position)]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def compute_baseline(
    values: Sequence[float], anomaly_percentile: float, min_sample: int = MIN_SAMPLE
) -> Optional[Baseline]:
    if len(values) < min_sample:
        return None
    numbers = [float(v) for v in values]
    return Baseline(
        n=len(numbers),
        median=float(_median(numbers)),
        minimum=min(numbers),
        anomaly_threshold=percentile(numbers, anomaly_percentile),
    )


EMOJI = {GREEN: "🟢", YELLOW: "🟡", GRAY: "⚪"}


def threshold_for(cfg, route: Optional[tuple[str, str]]) -> float:
    """Порог BUY маршрута, если он известен, иначе общий."""
    if route is None:
        return cfg.abs_threshold_usd
    return cfg.threshold_for(*route)


def classify(
    landed_usd: float,
    baseline: Optional[Baseline],
    cfg,
    route: Optional[tuple[str, str]] = None,
) -> str:
    """GREEN — покупать, YELLOW — упомянуть в дайджесте, GRAY — фон.

    В v1 GREEN ставится по кэшу; в v1.1 BUY-алерт требует подтверждения Amadeus.

    `route` включает порог этого маршрута вместо общего: цена, отличная для
    Пхукета, для Бали — обычный фон.
    """
    if landed_usd < threshold_for(cfg, route):
        return GREEN
    if baseline is None:
        return GRAY
    if landed_usd < baseline.anomaly_threshold:
        return GREEN
    if landed_usd < baseline.median * (1.0 - cfg.yellow_delta):
        return YELLOW
    return GRAY


def delta_to_median(landed_usd: float, baseline: Optional[Baseline]) -> Optional[float]:
    """Относительное отклонение от медианы: -0.15 = на 15% дешевле."""
    if baseline is None or baseline.median == 0:
        return None
    return landed_usd / baseline.median - 1.0


def level_emoji(level: str) -> str:
    return EMOJI.get(level, "⚪")


def daily_minimums(
    conn: sqlite3.Connection,
    origin: str,
    destination: str,
    since: str,
    market: Optional[str] = None,
    column: str = "landed_usd",
    min_lifetime_minutes: float = 0.0,
) -> list[tuple[str, float]]:
    """Минимальная цена по каждому дню наблюдения, по возрастанию даты.

    Это и есть выборка для baseline: одна точка на день, а не все офферы подряд.
    `column` — "landed_usd" (сравнение рынков) или "price_local" (сравнение
    внутри одного рынка, без шума курса валюты); значение не параметризуется
    через `?`, поэтому допустимы только эти два литерала.
    """
    if column not in _DAILY_MIN_COLUMNS:
        raise ValueError(f"daily_minimums: недопустимая колонка {column!r}")

    sql = (
        f"SELECT substr(last_seen_at, 1, 10) AS day, MIN({column}) AS best"
        " FROM price_history"
        " WHERE origin = ? AND destination = ? AND last_seen_at >= ?"
        f" AND {column} IS NOT NULL"
    )
    params: list[object] = [origin, destination, since]
    if market is not None:
        sql += " AND market = ?"
        params.append(market)
    if min_lifetime_minutes > 0:
        sql += f" AND {store.LIFETIME_MINUTES_SQL} >= ?"
        params.append(min_lifetime_minutes)
    sql += " GROUP BY day ORDER BY day"

    rows = conn.execute(sql, params).fetchall()
    return [(row["day"], float(row["best"])) for row in rows]


def daily_minimums_by_date(
    conn: sqlite3.Connection,
    origin: str,
    destination: str,
    since: str,
    market: Optional[str] = None,
    column: str = "landed_usd",
    min_lifetime_minutes: float = 0.0,
) -> dict[str, list[tuple[str, float]]]:
    """То же, что daily_minimums, но отдельной историей на каждую дату вылета.

    Одним запросом на маршрут, а не по запросу на дату: дат в месяце три
    десятка, и цикл из тридцати SELECT'ов на каждый скан ничем не оправдан.
    """
    if column not in _DAILY_MIN_COLUMNS:
        raise ValueError(f"daily_minimums_by_date: недопустимая колонка {column!r}")

    sql = (
        f"SELECT depart_date, substr(last_seen_at, 1, 10) AS day, MIN({column}) AS best"
        " FROM price_history"
        " WHERE origin = ? AND destination = ? AND last_seen_at >= ?"
        f" AND {column} IS NOT NULL"
    )
    params: list[object] = [origin, destination, since]
    if market is not None:
        sql += " AND market = ?"
        params.append(market)
    if min_lifetime_minutes > 0:
        sql += f" AND {store.LIFETIME_MINUTES_SQL} >= ?"
        params.append(min_lifetime_minutes)
    sql += " GROUP BY depart_date, day ORDER BY depart_date, day"

    grouped: dict[str, list[tuple[str, float]]] = {}
    for row in conn.execute(sql, params):
        grouped.setdefault(row["depart_date"], []).append(
            (row["day"], float(row["best"]))
        )
    return grouped


def falling_streak(daily: Sequence[tuple[str, float]]) -> int:
    """Сколько дней подряд минимум снижается, считая от последнего дня.

    Дни должны быть упорядочены по возрастанию даты (как возвращает
    daily_minimums). Меньше двух точек или рост в последний день — 0.
    """
    if len(daily) < 2:
        return 0
    streak = 0
    for i in range(len(daily) - 1, 0, -1):
        if daily[i][1] < daily[i - 1][1]:
            streak += 1
        else:
            break
    return streak


def week_delta(
    conn: sqlite3.Connection,
    origin: str,
    destination: str,
    now: str,
    min_lifetime_minutes: float = 0.0,
) -> Optional[float]:
    """Относительная разница минимума последних 7 дней к предыдущим 7.

    None, если одного из периодов нет в данных.
    """
    since_recent = store.shift_days(now, -7)
    since_prev = store.shift_days(now, -14)
    daily = daily_minimums(
        conn,
        origin,
        destination,
        since=since_prev,
        min_lifetime_minutes=min_lifetime_minutes,
    )

    recent_cutoff = since_recent[:10]
    recent = [price for day, price in daily if day >= recent_cutoff]
    previous = [price for day, price in daily if day < recent_cutoff]
    if not recent or not previous:
        return None

    prev_min = min(previous)
    if prev_min == 0:
        return None
    return min(recent) / prev_min - 1.0


def dow_factor(
    conn: sqlite3.Connection,
    origin: str,
    destination: str,
    depart_date: str,
    now: str,
    min_sample: int = MIN_SAMPLE,
    min_lifetime_minutes: float = 0.0,
) -> float:
    """Множитель к baseline: насколько этот день недели дешевле среднего.

    Смотрит на день недели ДАТЫ ВЫЛЕТА (не дня наблюдения), в окне 60 дней
    наблюдений. 1.0, пока наблюдений по этому дню недели меньше min_sample —
    поправка не должна срабатывать на шуме.
    """
    window_start = store.shift_days(now, -60)
    sql = (
        "SELECT depart_date, landed_usd FROM price_history"
        " WHERE origin = ? AND destination = ? AND last_seen_at >= ?"
        " AND landed_usd IS NOT NULL"
    )
    params: list[object] = [origin, destination, window_start]
    if min_lifetime_minutes > 0:
        sql += f" AND {store.LIFETIME_MINUTES_SQL} >= ?"
        params.append(min_lifetime_minutes)
    rows = conn.execute(sql, params).fetchall()
    if not rows:
        return 1.0

    target_weekday = date.fromisoformat(depart_date).weekday()
    target_values: list[float] = []
    all_values: list[float] = []
    for row in rows:
        value = float(row["landed_usd"])
        all_values.append(value)
        if date.fromisoformat(row["depart_date"]).weekday() == target_weekday:
            target_values.append(value)

    if len(target_values) < min_sample:
        return 1.0

    overall_median = _median(all_values)
    if overall_median == 0:
        return 1.0
    return _median(target_values) / overall_median


_NIGHT_WORDS = ("ночь", "ночи", "ночей")


def _nights_word(n: int) -> str:
    if n % 10 == 1 and n % 100 != 11:
        return _NIGHT_WORDS[0]
    if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14):
        return _NIGHT_WORDS[1]
    return _NIGHT_WORDS[2]


TRIGGER_ROUTE = "route"  # дешевле p10 истории маршрута
TRIGGER_DATE = "date"  # дешевле p10 истории этой даты вылета
TRIGGER_ABSOLUTE = "absolute"  # дешевле абсолютного порога этого маршрута

# Чем меньше число, тем сильнее утверждение: «дешевле, чем бывал весь маршрут»
# весомее, чем «дешевле, чем бывала эта дата», а то и другое — весомее, чем
# «дешевле круглой цифры». Порядок нужен, когда на одну находку сработало
# несколько веток: в алерт уходит одна, самая сильная.
_TRIGGER_PRECEDENCE = {TRIGGER_ROUTE: 0, TRIGGER_DATE: 1, TRIGGER_ABSOLUTE: 2}


@dataclass(frozen=True)
class Candidate:
    origin: str
    destination: str
    market: str
    depart_date: str
    return_date: Optional[str]
    price_local: float
    currency: str
    landed_usd: float
    airline: Optional[str]
    transfers: Optional[int]
    gate: Optional[str]
    search_url: Optional[str]
    # В локальной валюте своего рынка, по дневным минимумам. None, когда
    # кандидат пришёл из абсолютного порога: тот не требует статистики и
    # работает на маршрутах, где истории ещё нет.
    baseline: Optional[Baseline]
    last_seen_at: Optional[str] = None
    trigger: str = TRIGGER_ROUTE
    threshold_usd: Optional[float] = None  # порог BUY маршрута на момент находки

    @property
    def route_date_key(self) -> str:
        return f"{self.origin}-{self.destination}-{self.depart_date}-{self.return_date or ''}"

    @property
    def nights_text(self) -> str:
        """'12 ночей' / '1 ночь' / 'в один конец' — для текста алерта."""
        if not self.return_date:
            return "в один конец"
        nights = (
            date.fromisoformat(self.return_date) - date.fromisoformat(self.depart_date)
        ).days
        return f"{nights} {_nights_word(nights)}"


def _candidate(
    row: sqlite3.Row,
    origin: str,
    destination: str,
    market: str,
    baseline: Optional[Baseline],
    trigger: str,
    threshold_usd: float,
) -> Candidate:
    return Candidate(
        origin=origin,
        destination=destination,
        market=market,
        depart_date=row["depart_date"],
        return_date=row["return_date"],
        price_local=float(row["price_local"]),
        currency=row["currency"],
        landed_usd=float(row["landed_usd"]),
        airline=row["airline"],
        transfers=row["transfers"],
        gate=row["gate"],
        search_url=row["search_url"],
        baseline=baseline,
        last_seen_at=row["last_seen_at"],
        trigger=trigger,
        threshold_usd=threshold_usd,
    )


def find_candidates(conn: sqlite3.Connection, cfg, now: str) -> list[Candidate]:
    """Кандидаты на алерт по паре маршрут × рынок (самый дешёвый свежий оффер).

    Два независимых триггера:
    * перцентильный — сегодняшний минимум дешевле p10 истории дневных минимумов;
      сравнение в локальной валюте рынка, чтобы движение курса не выглядело
      падением цены;
    * абсолютный — landed-цена ниже порога этого маршрута. Он не требует
      статистики и потому работает там, где истории ещё нет.

    Если сработали оба, кандидат помечается перцентильным: «дешевле, чем
    бывало» — более сильное утверждение, чем «дешевле круглой цифры».
    """
    window_start = store.shift_days(now, -cfg.baseline_window_days)
    candidates: list[Candidate] = []

    for origin, destination in cfg.routes():
        threshold = cfg.threshold_for(origin, destination)
        for market in cfg.markets:
            daily = daily_minimums(
                conn,
                origin,
                destination,
                since=window_start,
                market=market,
                column="price_local",
                min_lifetime_minutes=cfg.min_offer_lifetime_minutes,
            )
            baseline = compute_baseline(
                [price for _, price in daily], cfg.anomaly_percentile
            )

            fresh = [
                row
                for row in store.fresh_prices(
                    conn,
                    origin,
                    destination,
                    now=now,
                    ttl_hours=cfg.cache_ttl_hours,
                    market=market,
                    min_lifetime_minutes=cfg.min_offer_lifetime_minutes,
                )
                if row["landed_usd"] is not None
            ]
            if not fresh:
                continue

            best = min(fresh, key=lambda row: row["price_local"])
            if baseline is not None and best["price_local"] < baseline.anomaly_threshold:
                candidates.append(
                    _candidate(
                        best, origin, destination, market, baseline, TRIGGER_ROUTE, threshold
                    )
                )
            elif float(best["landed_usd"]) < threshold:
                candidates.append(
                    _candidate(
                        best, origin, destination, market, None, TRIGGER_ABSOLUTE, threshold
                    )
                )

            by_date = daily_minimums_by_date(
                conn,
                origin,
                destination,
                since=window_start,
                market=market,
                column="price_local",
                min_lifetime_minutes=cfg.min_offer_lifetime_minutes,
            )
            for row in _cheapest_per_date(fresh):
                date_baseline = compute_baseline(
                    [price for _, price in by_date.get(row["depart_date"], ())],
                    cfg.anomaly_percentile,
                )
                if date_baseline is None:
                    continue
                if row["price_local"] < date_baseline.anomaly_threshold:
                    candidates.append(
                        _candidate(
                            row,
                            origin,
                            destination,
                            market,
                            date_baseline,
                            TRIGGER_DATE,
                            threshold,
                        )
                    )

    candidates = _strongest_per_offer(candidates)
    candidates.sort(key=lambda c: c.landed_usd)
    return candidates


@dataclass(frozen=True)
class VanishedOffer:
    origin: str
    destination: str
    depart_date: str
    gone_landed_usd: float
    gone_airline: Optional[str]
    gone_last_seen_at: str
    current_landed_usd: float

    @property
    def gap_ratio(self) -> float:
        """На сколько текущая лучшая цена хуже пропавшей: 0.27 = на 27% дороже."""
        return self.current_landed_usd / self.gone_landed_usd - 1.0


def best_fresh_for_date(
    conn: sqlite3.Connection,
    cfg,
    origin: str,
    destination: str,
    depart_date: str,
    now: str,
) -> Optional[float]:
    """Лучшая живая цена на конкретную дату вылета, None — если её нет."""
    prices = [
        float(row["landed_usd"])
        for row in store.fresh_prices(
            conn,
            origin,
            destination,
            now=now,
            ttl_hours=cfg.cache_ttl_hours,
            min_lifetime_minutes=cfg.min_offer_lifetime_minutes,
        )
        if row["landed_usd"] is not None and row["depart_date"] == depart_date
    ]
    return min(prices) if prices else None


def vanished_offers(conn: sqlite3.Connection, cfg, now: str) -> list[VanishedOffer]:
    """Даты, где лучший оффер выпал из кэша, а замена заметно дороже.

    Без этого пропажа неотличима от подорожания: дайджест показывает следующую
    по цене запись как «лучшую цену дня», хотя рейс никуда не делся — исчезла
    только запись о нём. Считается по датам вылета, потому что пропадают
    именно даты: у маршрута в целом минимум обычно держит какая-нибудь другая.
    """
    window_start = store.shift_days(now, -cfg.baseline_window_days)
    found: list[VanishedOffer] = []

    for origin, destination in cfg.routes():
        best_ever: dict[str, sqlite3.Row] = {}
        for row in store.recent_prices(
            conn,
            origin,
            destination,
            since=window_start,
            min_lifetime_minutes=cfg.min_offer_lifetime_minutes,
        ):
            if row["landed_usd"] is None:
                continue
            current = best_ever.get(row["depart_date"])
            if current is None or row["landed_usd"] < current["landed_usd"]:
                best_ever[row["depart_date"]] = row

        best_fresh: dict[str, float] = {}
        for row in store.fresh_prices(
            conn, origin, destination, now=now, ttl_hours=cfg.cache_ttl_hours
        ):
            if row["landed_usd"] is None:
                continue
            price = float(row["landed_usd"])
            if price < best_fresh.get(row["depart_date"], float("inf")):
                best_fresh[row["depart_date"]] = price

        for depart_date, gone in best_ever.items():
            current_price = best_fresh.get(depart_date)
            # Даты без единой свежей цены сюда не попадают: это не «пропал
            # хороший оффер», а «нет данных», и об этом дайджест говорит
            # отдельной строкой по маршруту.
            if current_price is None:
                continue
            gone_price = float(gone["landed_usd"])
            if current_price < gone_price * (1.0 + cfg.vanished_delta):
                continue
            found.append(
                VanishedOffer(
                    origin=origin,
                    destination=destination,
                    depart_date=depart_date,
                    gone_landed_usd=gone_price,
                    gone_airline=gone["airline"],
                    gone_last_seen_at=gone["last_seen_at"],
                    current_landed_usd=current_price,
                )
            )

    found.sort(key=lambda item: item.gap_ratio, reverse=True)
    return found


def _cheapest_per_date(rows: Sequence[sqlite3.Row]) -> list[sqlite3.Row]:
    """По одной самой дешёвой записи на каждую дату вылета."""
    best: dict[str, sqlite3.Row] = {}
    for row in rows:
        current = best.get(row["depart_date"])
        if current is None or row["price_local"] < current["price_local"]:
            best[row["depart_date"]] = row
    return list(best.values())


def _strongest_per_offer(candidates: Sequence[Candidate]) -> list[Candidate]:
    """Одна находка — один алерт, даже если её нашли сразу несколько веток."""
    best: dict[tuple[str, str], Candidate] = {}
    for candidate in candidates:
        key = (candidate.market, candidate.route_date_key)
        current = best.get(key)
        if current is None or (
            _TRIGGER_PRECEDENCE[candidate.trigger] < _TRIGGER_PRECEDENCE[current.trigger]
        ):
            best[key] = candidate
    return list(best.values())
