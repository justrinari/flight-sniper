# Режим поездки из нескольких перелётов — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Бот следит за ценами на все перелёты маршрута Бали → Пхукет → Вьетнам →
Филиппины → Бали и каждый день присылает три самых дешёвых маршрута целиком,
плюс алерт, когда весь маршрут дешевеет ниже порога.

**Architecture:** Новая секция `trip` в конфиге превращается в список пар городов
через `Config.routes()`, поэтому существующий скан (`aviasales.scan_all`) и
хранилище не меняются. Чистый модуль `src/itinerary.py` собирает маршруты из
лучших цен отдельных перелётов. `src/trip_digest.py` читает свежие цены из базы и
рендерит сводку и алерт. `runner.py` в режиме поездки выбирает новые функции
вместо маршрутных.

**Tech Stack:** Python 3.11, SQLite, requests, pytest + responses, GitHub Actions.

Спецификация: `docs/superpowers/specs/2026-10-22-multi-leg-trip-design.md`.

Все команды запускаются из корня репозитория `/Users/rinari/Fly-tool`.

---

## Карта файлов

| Файл | Что меняется |
|---|---|
| `src/config.py` | `TripConfig`, поле `Config.trip`, разбор секции, `routes()` в режиме поездки |
| `src/cities.py` | коды Вьетнама и Филиппин, `country()` |
| `src/airlines.py` | региональные лоукостеры |
| `src/itinerary.py` | **новый**: `Leg`, `Itinerary`, `cheapest_legs`, `build_itineraries`, `top_distinct` |
| `src/trip_digest.py` | **новый**: загрузка маршрутов из БД, сводка, алерт |
| `src/runner.py` | ветки режима поездки в `run_digest` и `_process_alerts` |
| `config.yaml` | секция `trip`, месяцы, часовой пояс, TTL |
| `.github/workflows/scan.yml` | скан только в первом проходе |
| `.github/workflows/digest.yml` | cron 01:00 UTC |
| `README.md` | раздел о режиме поездки |
| `tests/conftest.py` | фикстура `trip_config` |
| `tests/test_config_trip.py`, `tests/test_itinerary.py`, `tests/test_trip_digest.py`, `tests/test_runner_trip.py` | **новые** |

---

### Task 1: `TripConfig` в конфиге

**Files:**
- Modify: `src/config.py`
- Test: `tests/test_config_trip.py`

- [ ] **Step 1: Write the failing test**

Создать `tests/test_config_trip.py`:

```python
import textwrap

import pytest

from src.config import Config, TripConfig

BASE = """
origins: []
destinations: []
markets: [kg, ru]
market_currency:
  kg: kgs
  ru: rub
fx_markup:
  default: 0.025
  usd: 0.0
departure_months: ["2027-01", "2027-02"]
return_months: ["2027-01"]
trip_type: one_way
nights_range: [10, 16]
max_transfer_hours: 12
abs_threshold_usd: 250
anomaly_percentile: 10
yellow_delta: 0.15
"""

TRIP = """
trip:
  stops:
    - [DPS]
    - [HKT]
    - [SGN, DAD]
    - [MNL]
    - [DPS]
  latest_start: "2027-01-22"
  max_total_days: 14
  min_stay_days: 3
  alert_total_usd: 380
"""


def _load(tmp_path, text):
    path = tmp_path / "config.yaml"
    path.write_text(textwrap.dedent(text), encoding="utf-8")
    return Config.load(path)


def test_trip_section_is_parsed(tmp_path):
    cfg = _load(tmp_path, BASE + TRIP)
    assert cfg.trip == TripConfig(
        stops=(("DPS",), ("HKT",), ("SGN", "DAD"), ("MNL",), ("DPS",)),
        latest_start="2027-01-22",
        max_total_days=14,
        min_stay_days=3,
        alert_total_usd=380.0,
        top_n=3,
    )


def test_routes_come_from_trip_stops(tmp_path):
    cfg = _load(tmp_path, BASE + TRIP)
    assert cfg.routes() == [
        ("DPS", "HKT"),
        ("HKT", "SGN"),
        ("HKT", "DAD"),
        ("SGN", "MNL"),
        ("DAD", "MNL"),
        ("MNL", "DPS"),
    ]


def test_without_trip_routes_are_origins_by_destinations(tmp_path):
    text = BASE.replace("origins: []", "origins: [ALA]").replace(
        "destinations: []", "destinations: [HKT, DPS]"
    )
    cfg = _load(tmp_path, text)
    assert cfg.trip is None
    assert cfg.routes() == [("ALA", "HKT"), ("ALA", "DPS")]


def test_unquoted_yaml_date_is_accepted(tmp_path):
    cfg = _load(tmp_path, BASE + TRIP.replace('"2027-01-22"', "2027-01-22"))
    assert cfg.trip.latest_start == "2027-01-22"


def test_single_stop_is_rejected(tmp_path):
    text = BASE + TRIP.replace(
        "    - [HKT]\n    - [SGN, DAD]\n    - [MNL]\n    - [DPS]\n", ""
    )
    with pytest.raises(ValueError, match="минимум две остановки"):
        _load(tmp_path, text)


def test_empty_stop_is_rejected(tmp_path):
    with pytest.raises(ValueError, match=r"trip.stops\[1\]"):
        _load(tmp_path, BASE + TRIP.replace("- [HKT]", "- []"))


def test_min_stay_below_one_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="min_stay_days"):
        _load(tmp_path, BASE + TRIP.replace("min_stay_days: 3", "min_stay_days: 0"))


def test_bad_latest_start_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="latest_start"):
        _load(tmp_path, BASE + TRIP.replace('"2027-01-22"', '"22.01.2027"'))
```

Файл содержит 8 тестов: `test_trip_section_is_parsed`,
`test_routes_come_from_trip_stops`,
`test_without_trip_routes_are_origins_by_destinations`,
`test_unquoted_yaml_date_is_accepted`, `test_single_stop_is_rejected`,
`test_empty_stop_is_rejected`, `test_min_stay_below_one_is_rejected`,
`test_bad_latest_start_is_rejected`.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_config_trip.py -v`
Expected: FAIL — `ImportError: cannot import name 'TripConfig'`

- [ ] **Step 3: Write minimal implementation**

В `src/config.py` добавить импорты:

```python
from datetime import date
from typing import Any, Optional
```

(заменить существующую строку `from typing import Any`).

Перед `class Config` добавить:

```python
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
```

В `class Config` последним полем (после `route_thresholds_usd`) добавить:

```python
    # Режим поездки из нескольких перелётов. Если задан, маршруты берутся из
    # остановок поездки, а origins/destinations не используются.
    trip: Optional[TripConfig] = None
```

В `Config.load` в вызов `cls(...)` последним аргументом добавить:

```python
            trip=TripConfig.parse(raw["trip"]) if raw.get("trip") else None,
```

Заменить первую строку тела `routes()`:

```python
        base = (
            self.trip.pairs()
            if self.trip is not None
            else [(o, d) for o in self.origins for d in self.destinations]
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_config_trip.py tests/test_config.py -v`
Expected: PASS (все тесты обоих файлов)

- [ ] **Step 5: Commit**

```bash
git add src/config.py tests/test_config_trip.py
git commit -m "feat: parse a multi-leg trip section in the config"
```

---

### Task 2: Города и авиакомпании региона

**Files:**
- Modify: `src/cities.py`, `src/airlines.py`
- Test: `tests/test_cities.py`, `tests/test_airlines.py`

- [ ] **Step 1: Write the failing test**

Дописать в конец `tests/test_cities.py`:

```python
def test_trip_airports_have_names():
    assert cities.name("PQC") == "Фукуок"
    assert cities.name("CXR") == "Нячанг"
    assert cities.name("DAD") == "Дананг"
    assert cities.name("MNL") == "Манила"
    assert cities.name("CEB") == "Себу"
    assert cities.name("MPH") == "Боракай"


def test_country_of_airport():
    assert cities.country("SGN") == "Вьетнам"
    assert cities.country("mnl") == "Филиппины"
    assert cities.country("XXX") is None
    assert cities.country(None) is None
```

Если в `tests/test_cities.py` модуль импортирован под другим именем, привести
вызовы к нему (проверить первые строки файла).

Дописать в конец `tests/test_airlines.py`:

```python
def test_regional_low_cost_carriers_have_names():
    assert airlines.name("5J") == "Cebu Pacific"
    assert airlines.name("Z2") == "Philippines AirAsia"
    assert airlines.name("VZ") == "Thai Vietjet"
    assert airlines.name("TR") == "Scoot"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_cities.py tests/test_airlines.py -v`
Expected: FAIL — `AssertionError: assert 'PQC' == 'Фукуок'` и `AttributeError: module 'src.cities' has no attribute 'country'`

- [ ] **Step 3: Write minimal implementation**

В `src/cities.py` в `NAMES` после строки `"DPS": "Бали",` добавить:

```python
    # Поездка Бали → Пхукет → Вьетнам → Филиппины → Бали
    "DAD": "Дананг",
    "PQC": "Фукуок",
    "CXR": "Нячанг",
    "MNL": "Манила",
    "CEB": "Себу",
    "MPH": "Боракай",
    "PPS": "Пуэрто-Принсеса",
```

После словаря `NAMES` добавить:

```python
# Страна аэропорта — для подписи остановки поездки, где на выбор несколько
# городов одной страны («Вьетнам», а не «Хошимин / Ханой / Дананг …»).
COUNTRIES = {
    "SGN": "Вьетнам",
    "HAN": "Вьетнам",
    "DAD": "Вьетнам",
    "PQC": "Вьетнам",
    "CXR": "Вьетнам",
    "MNL": "Филиппины",
    "CEB": "Филиппины",
    "MPH": "Филиппины",
    "PPS": "Филиппины",
    "HKT": "Таиланд",
    "BKK": "Таиланд",
    "DPS": "Индонезия",
}
```

После функции `name` добавить:

```python
def country(code: Optional[str]) -> Optional[str]:
    """IATA-код → страна, если известна."""
    if not code:
        return None
    return COUNTRIES.get(code.strip().upper())
```

В `src/airlines.py` в `NAMES` после `"XJ": "Thai AirAsia X",` добавить:

```python
    # Региональные перевозчики поездки по Юго-Восточной Азии
    "VZ": "Thai Vietjet",
    "VU": "Vietravel Airlines",
    "5J": "Cebu Pacific",
    "DG": "Cebgo",
    "Z2": "Philippines AirAsia",
    "PR": "Philippine Airlines",
    "TR": "Scoot",
    "UO": "HK Express",
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_cities.py tests/test_airlines.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/cities.py src/airlines.py tests/test_cities.py tests/test_airlines.py
git commit -m "feat: name the Vietnam and Philippines airports and carriers"
```

---

### Task 3: Сборщик маршрутов `src/itinerary.py`

**Files:**
- Create: `src/itinerary.py`
- Test: `tests/test_itinerary.py`

- [ ] **Step 1: Write the failing test**

Создать `tests/test_itinerary.py`:

```python
from src import itinerary
from src.config import TripConfig
from src.itinerary import Leg

TRIP = TripConfig(
    stops=(("DPS",), ("HKT",), ("SGN", "DAD"), ("MNL",), ("DPS",)),
    latest_start="2027-01-22",
    max_total_days=14,
    min_stay_days=3,
    alert_total_usd=380.0,
)


def _leg(origin, destination, day, usd, airline="AK"):
    return Leg(
        origin=origin,
        destination=destination,
        depart_date=day,
        landed_usd=usd,
        airline=airline,
        transfers=0,
        market="ru",
        currency="rub",
        search_url=f"https://www.aviasales.ru/search/{origin}{destination}",
        last_seen_at="2026-10-22T00:00:00Z",
    )


def _group(*legs):
    grouped = {}
    for leg in legs:
        grouped.setdefault((leg.origin, leg.destination), []).append(leg)
    return grouped


BASE_LEGS = (
    _leg("DPS", "HKT", "2027-01-18", 86),
    _leg("HKT", "SGN", "2027-01-23", 101),
    _leg("SGN", "MNL", "2027-01-28", 117),
    _leg("MNL", "DPS", "2027-01-31", 102),
)


def test_builds_the_only_valid_itinerary():
    result = itinerary.build_itineraries(_group(*BASE_LEGS), TRIP)
    assert len(result) == 1
    assert result[0].total_usd == 406
    assert result[0].airports == ("HKT", "SGN", "MNL", "DPS")
    assert result[0].days == 13


def test_start_after_latest_start_is_rejected():
    legs = (_leg("DPS", "HKT", "2027-01-23", 86),) + BASE_LEGS[1:]
    assert itinerary.build_itineraries(_group(*legs), TRIP) == []


def test_stay_shorter_than_minimum_is_rejected():
    legs = (BASE_LEGS[0], _leg("HKT", "SGN", "2027-01-20", 101)) + BASE_LEGS[2:]
    assert itinerary.build_itineraries(_group(*legs), TRIP) == []


def test_trip_longer_than_maximum_is_rejected():
    legs = BASE_LEGS[:3] + (_leg("MNL", "DPS", "2027-02-02", 102),)
    assert itinerary.build_itineraries(_group(*legs), TRIP) == []


def test_trip_of_exactly_maximum_is_accepted():
    legs = BASE_LEGS[:3] + (_leg("MNL", "DPS", "2027-02-01", 102),)
    assert len(itinerary.build_itineraries(_group(*legs), TRIP)) == 1


def test_legs_must_connect_through_the_same_city():
    legs = BASE_LEGS[:2] + (_leg("DAD", "MNL", "2027-01-28", 117), BASE_LEGS[3])
    assert itinerary.build_itineraries(_group(*legs), TRIP) == []


def test_itineraries_are_sorted_by_total():
    legs = BASE_LEGS + (
        _leg("HKT", "DAD", "2027-01-23", 80),
        _leg("DAD", "MNL", "2027-01-28", 200),
    )
    result = itinerary.build_itineraries(_group(*legs), TRIP)
    assert [r.total_usd for r in result] == [406, 468]


def test_cheapest_legs_keeps_minimum_per_date_and_sorts_by_date():
    rows = [
        {**_leg("DPS", "HKT", "2027-01-20", 90).__dict__},
        {**_leg("DPS", "HKT", "2027-01-18", 95).__dict__},
        {**_leg("DPS", "HKT", "2027-01-18", 86, airline="FD").__dict__},
        {**_leg("DPS", "HKT", "2027-01-19", 70).__dict__, "landed_usd": None},
    ]
    grouped = itinerary.cheapest_legs(rows)
    legs = grouped[("DPS", "HKT")]
    assert [(leg.depart_date, leg.landed_usd, leg.airline) for leg in legs] == [
        ("2027-01-18", 86, "FD"),
        ("2027-01-20", 90, "AK"),
    ]


def test_top_distinct_skips_same_city_set():
    legs = BASE_LEGS + (
        _leg("DPS", "HKT", "2027-01-17", 90),
        _leg("HKT", "DAD", "2027-01-23", 110),
        _leg("DAD", "MNL", "2027-01-28", 120),
    )
    result = itinerary.build_itineraries(_group(*legs), TRIP)
    top = itinerary.top_distinct(result, 3)
    assert [t.airports for t in top] == [
        ("HKT", "SGN", "MNL", "DPS"),
        ("HKT", "DAD", "MNL", "DPS"),
    ]
    assert top[0].total_usd == 406
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_itinerary.py -v`
Expected: FAIL — `ImportError: cannot import name 'itinerary' from 'src'`

- [ ] **Step 3: Write minimal implementation**

Создать `src/itinerary.py`:

```python
"""Сборка маршрутов из нескольких перелётов.

Кэш Aviasales знает цены только отдельных перелётов. Модуль перебирает их и
собирает целые маршруты, подходящие под ограничения поездки: вылет не позже
latest_start, между перелётами не меньше min_stay_days, вся поездка не длиннее
max_total_days, каждый следующий перелёт вылетает оттуда, куда прилетел
предыдущий. Сети и базы здесь нет — только данные на входе и на выходе.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Iterable, Optional, Sequence

from src.config import TripConfig


@dataclass(frozen=True)
class Leg:
    """Лучшая цена одного перелёта на одну дату."""

    origin: str
    destination: str
    depart_date: str  # YYYY-MM-DD
    landed_usd: float
    airline: str
    transfers: Optional[int]
    market: str
    currency: str
    search_url: str
    last_seen_at: str


@dataclass(frozen=True)
class Itinerary:
    legs: tuple[Leg, ...]
    total_usd: float

    @property
    def airports(self) -> tuple[str, ...]:
        """Куда прилетает каждый перелёт — по этому набору топ отличается."""
        return tuple(leg.destination for leg in self.legs)

    @property
    def days(self) -> int:
        first = date.fromisoformat(self.legs[0].depart_date)
        last = date.fromisoformat(self.legs[-1].depart_date)
        return (last - first).days


def cheapest_legs(rows: Iterable) -> dict[tuple[str, str], list[Leg]]:
    """Строки price_history → самая дешёвая цена на (откуда, куда, дата).

    Строки без landed_usd (не нашёлся курс) пропускаются. Перелёты каждой
    пары отсортированы по дате.
    """
    best: dict[tuple[str, str, str], Leg] = {}
    for row in rows:
        if row["landed_usd"] is None:
            continue
        leg = Leg(
            origin=row["origin"],
            destination=row["destination"],
            depart_date=row["depart_date"],
            landed_usd=float(row["landed_usd"]),
            airline=row["airline"] or "",
            transfers=row["transfers"],
            market=row["market"],
            currency=row["currency"],
            search_url=row["search_url"] or "",
            last_seen_at=row["last_seen_at"],
        )
        key = (leg.origin, leg.destination, leg.depart_date)
        if key not in best or leg.landed_usd < best[key].landed_usd:
            best[key] = leg

    grouped: dict[tuple[str, str], list[Leg]] = {}
    for leg in sorted(best.values(), key=lambda item: item.depart_date):
        grouped.setdefault((leg.origin, leg.destination), []).append(leg)
    return grouped


def build_itineraries(
    legs: dict[tuple[str, str], list[Leg]], trip: TripConfig
) -> list[Itinerary]:
    """Все маршруты, подходящие под ограничения, от дешёвого к дорогому.

    Перебор в глубину по остановкам. Ветка обрывается сразу, как только
    нарушено ограничение по датам, поэтому до последней остановки доходят
    только допустимые сочетания.
    """
    results: list[Itinerary] = []
    last_stop = len(trip.stops) - 1

    def walk(stop: int, city: str, chosen: list[Leg], total: float) -> None:
        if stop == last_stop:
            results.append(Itinerary(legs=tuple(chosen), total_usd=round(total, 2)))
            return
        start = date.fromisoformat(chosen[0].depart_date) if chosen else None
        previous = date.fromisoformat(chosen[-1].depart_date) if chosen else None
        for destination in trip.stops[stop + 1]:
            for leg in legs.get((city, destination), []):
                day = date.fromisoformat(leg.depart_date)
                if previous is None:
                    if leg.depart_date > trip.latest_start:
                        continue
                else:
                    if (day - previous).days < trip.min_stay_days:
                        continue
                    if (day - start).days > trip.max_total_days:
                        continue
                walk(stop + 1, destination, chosen + [leg], total + leg.landed_usd)

    for origin in trip.stops[0]:
        walk(0, origin, [], 0.0)

    results.sort(key=lambda item: (item.total_usd, item.legs[0].depart_date))
    return results


def top_distinct(itineraries: Sequence[Itinerary], n: int) -> list[Itinerary]:
    """Первые n маршрутов с разными наборами городов.

    Без этого топ-3 заняли бы почти одинаковые маршруты, отличающиеся датой
    одного перелёта на день.
    """
    seen: set[tuple[str, ...]] = set()
    top: list[Itinerary] = []
    for item in itineraries:
        if item.airports in seen:
            continue
        seen.add(item.airports)
        top.append(item)
        if len(top) >= n:
            break
    return top
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_itinerary.py -v`
Expected: PASS (9 тестов)

- [ ] **Step 5: Commit**

```bash
git add src/itinerary.py tests/test_itinerary.py
git commit -m "feat: assemble multi-leg itineraries from single-leg prices"
```

---

### Task 4: Фикстура `trip_config`

**Files:**
- Modify: `tests/conftest.py`

- [ ] **Step 1: Добавить фикстуру**

В `tests/conftest.py` добавить импорты в начало:

```python
import dataclasses

from src.config import TripConfig
```

и в конец файла:

```python
@pytest.fixture()
def trip_config(config_stub) -> Config:
    """Режим поездки Бали → Пхукет → Вьетнам → Филиппины → Бали в миниатюре."""
    return dataclasses.replace(
        config_stub,
        origins=[],
        destinations=[],
        trip_type="one_way",
        departure_months=["2027-01", "2027-02"],
        timezone="Asia/Makassar",
        trip=TripConfig(
            stops=(("DPS",), ("HKT",), ("SGN", "DAD"), ("MNL",), ("DPS",)),
            latest_start="2027-01-22",
            max_total_days=14,
            min_stay_days=3,
            alert_total_usd=380.0,
        ),
    )
```

- [ ] **Step 2: Run the suite**

Run: `.venv/bin/pytest -q`
Expected: PASS, число тестов то же плюс новые из задач 1–3.

- [ ] **Step 3: Commit**

```bash
git add tests/conftest.py
git commit -m "test: add a trip-mode config fixture"
```

---

### Task 5: Сводка и алерт `src/trip_digest.py`

**Files:**
- Create: `src/trip_digest.py`
- Test: `tests/test_trip_digest.py`

- [ ] **Step 1: Write the failing test**

Создать `tests/test_trip_digest.py`:

```python
import pytest

from src import store, trip_digest
from src.models import PriceRecord

NOW = "2026-10-22T01:00:00Z"


def _record(origin, destination, day, usd, airline="AK", transfers=0):
    return PriceRecord(
        source="aviasales_cache",
        origin=origin,
        destination=destination,
        market="ru",
        depart_date=day,
        return_date=None,
        price_local=round(usd * 92, 2),
        currency="rub",
        airline=airline,
        transfers=transfers,
        duration_min=300,
        search_url=f"https://www.aviasales.ru/search/{origin}{destination}",
        fx_rate=1 / 92,
        landed_usd=usd,
    )


BEST = [
    _record("DPS", "HKT", "2027-01-18", 86, transfers=1),
    _record("HKT", "SGN", "2027-01-23", 101, airline="VJ"),
    _record("SGN", "MNL", "2027-01-28", 117, airline="TR", transfers=1),
    _record("MNL", "DPS", "2027-01-31", 102, airline="5J"),
]


@pytest.fixture()
def conn(tmp_path):
    connection = store.connect(tmp_path / "history.sqlite")
    store.init_schema(connection)
    store.set_meta(connection, "last_scan_at", NOW)
    yield connection
    connection.close()


def test_digest_shows_best_itinerary(conn, trip_config):
    store.insert_prices(conn, BEST, now=NOW)
    result = trip_digest.build_text(conn, trip_config, now=NOW)
    assert result.best_total == 406
    assert "✈️ Бали → Пхукет → Вьетнам → Манила → Бали" in result.text
    assert "1. <b>$406</b> за взрослого" in result.text
    assert "18.01 Бали → Пхукет — $86 AirAsia, 1 пересадка" in result.text
    assert "23.01 Пхукет → Хошимин — $101 VietJet, без пересадок" in result.text
    assert "31.01 Манила → Бали — $102 Cebu Pacific, без пересадок" in result.text
    assert "13 дн." in result.text
    assert 'href="https://www.aviasales.ru/search/DPSHKT"' in result.text
    assert trip_digest.DISCLAIMER in result.text
    assert "🚨" not in result.text


def test_digest_compares_with_previous_best(conn, trip_config):
    store.insert_prices(conn, BEST, now=NOW)
    store.set_meta(conn, trip_digest.PREV_KEY, "412")
    result = trip_digest.build_text(conn, trip_config, now=NOW)
    assert "(вчера $412, −1%)" in result.text


def test_digest_without_itineraries_names_empty_stages(conn, trip_config):
    store.insert_prices(conn, BEST[:2], now=NOW)
    result = trip_digest.build_text(conn, trip_config, now=NOW)
    assert result.best_total is None
    assert "Подходящих маршрутов в кэше сейчас нет." in result.text
    assert "Вьетнам → Манила" in result.text
    assert "Манила → Бали" in result.text


def test_past_departures_are_ignored(conn, trip_config):
    past = [_record("DPS", "HKT", "2026-10-21", 10)]
    store.insert_prices(conn, past + BEST[1:], now=NOW)
    result = trip_digest.build_text(conn, trip_config, now=NOW)
    assert result.best_total is None


def test_alert_text(conn, trip_config):
    store.insert_prices(conn, BEST, now=NOW)
    itineraries, _ = trip_digest.load_itineraries(conn, trip_config, NOW)
    text = trip_digest.render_alert(itineraries[0], 450, NOW)
    assert text.startswith("🟢 Маршрут дешевле $450")
    assert "1. <b>$406</b> за взрослого" in text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_trip_digest.py -v`
Expected: FAIL — `ImportError: cannot import name 'trip_digest' from 'src'`

- [ ] **Step 3: Write minimal implementation**

Создать `src/trip_digest.py`:

```python
"""Сводка и алерт режима поездки из нескольких перелётов.

Читает свежие цены каждого перелёта из базы, собирает из них маршруты
(src/itinerary.py) и рендерит текст для Telegram.
"""

from __future__ import annotations

import html
import sqlite3
from dataclasses import dataclass
from datetime import date
from typing import Optional, Sequence

from src import airlines, cities, itinerary, store
from src.digest import _local_date, dead_man_switch, format_age, format_delta, format_transfers
from src.itinerary import Itinerary, Leg

DISCLAIMER = "Цены из кэша Aviasales, на одного взрослого, без багажа."
# Сумма лучшего маршрута из прошлой сводки — для строки «вчера $…».
PREV_KEY = "trip_best_prev"


@dataclass(frozen=True)
class DigestResult:
    text: str
    best_total: Optional[float]


def load_itineraries(
    conn: sqlite3.Connection, cfg, now: str
) -> tuple[list[Itinerary], dict[tuple[str, str], list[Leg]]]:
    """Маршруты из свежих цен базы, от дешёвого к дорогому, и сами перелёты.

    Свежесть и отсев однопроходных фантомов — те же, что в маршрутной сводке.
    Перелёты с датой раньше сегодняшней (по часовому поясу владельца)
    отбрасываются: кэш ещё может их помнить, но купить их уже нельзя.
    """
    today = _local_date(now, cfg.timezone).date().isoformat()
    rows = []
    for origin, destination in cfg.routes():
        rows.extend(
            row
            for row in store.fresh_prices(
                conn,
                origin,
                destination,
                now=now,
                ttl_hours=cfg.cache_ttl_hours,
                min_lifetime_minutes=cfg.min_offer_lifetime_minutes,
            )
            if row["depart_date"] >= today
        )
    legs = itinerary.cheapest_legs(rows)
    return itinerary.build_itineraries(legs, cfg.trip), legs


def stage_label(stop: Sequence[str]) -> str:
    """Подпись остановки: город, если он один, иначе страна."""
    if len(stop) == 1:
        return cities.name(stop[0])
    countries = {cities.country(code) for code in stop}
    if len(countries) == 1 and None not in countries:
        return countries.pop()
    return " / ".join(cities.name(code) for code in stop)


def missing_stages(legs: dict[tuple[str, str], list[Leg]], trip) -> list[str]:
    """Отрезки поездки, для которых в кэше нет ни одной свежей цены."""
    missing = []
    for current, following in zip(trip.stops, trip.stops[1:]):
        if not any((o, d) in legs for o in current for d in following):
            missing.append(f"{stage_label(current)} → {stage_label(following)}")
    return missing


def _day_month(day: str) -> str:
    return date.fromisoformat(day).strftime("%d.%m")


def render_itinerary(
    rank: int, item: Itinerary, now: str, prev_total: Optional[float] = None
) -> list[str]:
    head = f"{rank}. <b>${item.total_usd:.0f}</b> за взрослого"
    if prev_total:
        delta = format_delta((item.total_usd - prev_total) / prev_total)[1:-1]
        head += f" (вчера ${prev_total:.0f}, {delta})"
    lines = [head]
    for leg in item.legs:
        route = html.escape(cities.route(leg.origin, leg.destination))
        carrier = html.escape(airlines.name(leg.airline))
        lines.append(
            f"   {_day_month(leg.depart_date)} {route} — ${leg.landed_usd:.0f} "
            f"{carrier}, {format_transfers(leg.transfers)}"
        )
    oldest = min(leg.last_seen_at for leg in item.legs)
    tail = f"   {item.days} дн. · цены {format_age(oldest, now)}"
    links = " ".join(
        f'<a href="{html.escape(leg.search_url, quote=True)}">{index}</a>'
        for index, leg in enumerate(item.legs, start=1)
        if leg.search_url
    )
    if links:
        tail += f" · ссылки: {links}"
    lines.append(tail)
    return lines


def build_text(conn: sqlite3.Connection, cfg, now: str) -> DigestResult:
    trip = cfg.trip
    lines: list[str] = []
    switch = dead_man_switch(conn, now)
    if switch:
        lines += [switch, ""]

    lines.append("✈️ " + html.escape(" → ".join(stage_label(stop) for stop in trip.stops)))
    lines.append(
        f"Вылет до {_day_month(trip.latest_start)} · до {trip.max_total_days} дней · "
        f"от {trip.min_stay_days} дн. в стране"
    )

    itineraries, legs = load_itineraries(conn, cfg, now)
    top = itinerary.top_distinct(itineraries, trip.top_n)
    if not top:
        lines += ["", "Подходящих маршрутов в кэше сейчас нет."]
        missing = missing_stages(legs, trip)
        if missing:
            lines.append("Нет ни одной цены: " + ", ".join(missing))
    else:
        prev_raw = store.get_meta(conn, PREV_KEY)
        prev_total = float(prev_raw) if prev_raw else None
        for rank, item in enumerate(top, start=1):
            lines.append("")
            lines += render_itinerary(rank, item, now, prev_total if rank == 1 else None)

    lines += ["", DISCLAIMER]
    return DigestResult(
        text="\n".join(lines), best_total=top[0].total_usd if top else None
    )


def render_alert(item: Itinerary, threshold: float, now: str) -> str:
    lines = [f"🟢 Маршрут дешевле ${threshold:.0f}", ""]
    lines += render_itinerary(1, item, now)
    lines += ["", DISCLAIMER]
    return "\n".join(lines)
```

Примечание: в фикстуре `trip_config` третья остановка — `("SGN", "DAD")`, обе
во Вьетнаме, поэтому подпись «Вьетнам»; четвёртая — `("MNL",)`, поэтому
«Манила». Это и проверяет первый тест.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_trip_digest.py -v`
Expected: PASS (5 тестов)

- [ ] **Step 5: Commit**

```bash
git add src/trip_digest.py tests/test_trip_digest.py
git commit -m "feat: render the trip digest and alert"
```

---

### Task 6: Режим поездки в `runner.py`

**Files:**
- Modify: `src/runner.py`
- Test: `tests/test_runner_trip.py`

- [ ] **Step 1: Write the failing test**

Создать `tests/test_runner_trip.py`:

```python
import json

import pytest
import requests
import responses

from src import runner, store, trip_digest
from src.models import PriceRecord

PRICES_URL = "https://api.travelpayouts.com/aviasales/v3/prices_for_dates"
FX_URL = "https://open.er-api.com/v6/latest/USD"
TG_URL = "https://api.telegram.org/botTG/sendMessage"
FX_PAYLOAD = {"result": "success", "rates": {"USD": 1.0, "KGS": 87.5, "RUB": 92.0}}
NOW = "2026-10-22T01:00:00Z"


def _record(origin, destination, day, usd):
    return PriceRecord(
        source="aviasales_cache",
        origin=origin,
        destination=destination,
        market="ru",
        depart_date=day,
        return_date=None,
        price_local=round(usd * 92, 2),
        currency="rub",
        airline="AK",
        transfers=0,
        duration_min=300,
        search_url="https://www.aviasales.ru/search/x",
        fx_rate=1 / 92,
        landed_usd=usd,
    )


def _seed(path, prices=(86, 101, 117, 102)):
    conn = store.connect(path)
    store.init_schema(conn)
    days = ["2027-01-18", "2027-01-23", "2027-01-28", "2027-01-31"]
    pairs = [("DPS", "HKT"), ("HKT", "SGN"), ("SGN", "MNL"), ("MNL", "DPS")]
    store.insert_prices(
        conn,
        [_record(o, d, day, usd) for (o, d), day, usd in zip(pairs, days, prices)],
        now=NOW,
    )
    store.set_meta(conn, "last_scan_at", NOW)
    return conn


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("TP_TOKEN", "TP")
    monkeypatch.setenv("TG_BOT_TOKEN", "TG")
    monkeypatch.setenv("TG_CHAT_ID", "999")
    return tmp_path


def _sent_texts():
    return [
        json.loads(call.request.body)["text"]
        for call in responses.calls
        if call.request.url.startswith(TG_URL)
    ]


@responses.activate
def test_digest_in_trip_mode_sends_itinerary_and_remembers_total(env, trip_config):
    db = env / "history.sqlite"
    _seed(db).close()
    responses.add(responses.GET, FX_URL, json=FX_PAYLOAD, status=200)
    responses.add(responses.GET, PRICES_URL, json={"success": True, "data": []}, status=200)
    responses.add(responses.POST, TG_URL, json={"ok": True}, status=200)

    result = runner.run_digest(trip_config, db_path=db, now=NOW)

    texts = _sent_texts()
    assert len(texts) == 1
    assert "<b>$406</b>" in texts[0]
    price_calls = [c for c in responses.calls if c.request.url.startswith(PRICES_URL)]
    assert len(price_calls) == 4  # освежение каждого перелёта лучшего маршрута
    assert result["sent"] is True
    conn = store.connect(db)
    assert store.get_meta(conn, trip_digest.PREV_KEY) == "406.00"
    conn.close()


@responses.activate
def test_trip_alert_fires_below_threshold_once(env, trip_config):
    conn = _seed(env / "history.sqlite", prices=(80, 90, 100, 100))
    responses.add(responses.POST, TG_URL, json={"ok": True}, status=200)
    session = requests.Session()

    first = runner._process_alerts(conn, trip_config, session, "TP", {}, NOW)
    second = runner._process_alerts(conn, trip_config, session, "TP", {}, NOW)

    assert first == (1, 0)
    assert second == (0, 0)
    texts = _sent_texts()
    assert len(texts) == 1
    assert texts[0].startswith("🟢 Маршрут дешевле $380")
    conn.close()


@responses.activate
def test_trip_alert_stays_silent_above_threshold(env, trip_config):
    conn = _seed(env / "history.sqlite")  # $406 > $380
    responses.add(responses.POST, TG_URL, json={"ok": True}, status=200)

    sent = runner._process_alerts(conn, trip_config, requests.Session(), "TP", {}, NOW)

    assert sent == (0, 0)
    assert _sent_texts() == []
    conn.close()


@responses.activate
def test_trip_alert_respects_pause(env, trip_config):
    conn = _seed(env / "history.sqlite", prices=(80, 90, 100, 100))
    store.set_meta(conn, "paused", "1")

    sent = runner._process_alerts(conn, trip_config, requests.Session(), "TP", {}, NOW)

    assert sent == (0, 0)
    conn.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_runner_trip.py -v`
Expected: FAIL — в сводке нет `<b>$406</b>` (уходит маршрутная сводка), алерт возвращает `(0, 0)`.

- [ ] **Step 3: Write minimal implementation**

В `src/runner.py`:

Заменить строку импорта:

```python
from src import alerts, commands, digest, fx, notify, rules, store, trip_digest
```

После `MAX_ALERTS_PER_RUN = 5` добавить:

```python
# Ключ дедупа алертов режима поездки: маршрут один, поэтому и ключ один.
TRIP_ALERT_KEY = "trip"
```

Перед `def _process_alerts` добавить:

```python
def _process_trip_alert(conn, cfg, session, bot_token: str, chat_id: str, now: str) -> int:
    """Алерт, когда лучший маршрут поездки дешевле порога. Возвращает число отправленных."""
    itineraries, _ = trip_digest.load_itineraries(conn, cfg, now)
    if not itineraries:
        return 0
    best = itineraries[0]
    threshold = cfg.trip.alert_total_usd
    if best.total_usd >= threshold:
        return 0
    if not store.should_alert(conn, TRIP_ALERT_KEY, best.total_usd, ALERT_DROP_RATIO):
        return 0

    text = trip_digest.render_alert(best, threshold, now)
    try:
        notify.send_message(session, bot_token, chat_id, text)
    except notify.TelegramError as exc:
        LOG.warning("не удалось отправить алерт поездки: %s", exc)
        return 0
    store.record_alert(conn, TRIP_ALERT_KEY, best.total_usd, now, rules.GREEN)
    return 1
```

В `_process_alerts` сразу после блока проверки `bot_token`/`chat_id`
(после `return 0, 0` с предупреждением `TG_BOT_TOKEN/TG_CHAT_ID не заданы`)
добавить:

```python
    if cfg.trip is not None:
        # Алерты по 30 отдельным перелётам бессмысленны: важна сумма маршрута.
        return _process_trip_alert(conn, cfg, session, bot_token, chat_id, now), 0
```

Перед `def run_digest` добавить:

```python
def _refresh_trip_legs(conn, cfg, session, token, rates, now: str) -> int:
    """Перепроверяет каждый перелёт лучшего маршрута точечным запросом.

    Та же идея, что в _refresh_best_prices: цифра в утренней сводке должна
    быть настоящей. Четыре запроса в сутки. Ошибки не фатальны.
    """
    itineraries, _ = trip_digest.load_itineraries(conn, cfg, now)
    if not itineraries:
        return 0
    inserted = 0
    for leg in itineraries[0].legs:
        try:
            records = aviasales.fetch_prices_for_dates(
                session,
                token,
                origin=leg.origin,
                destination=leg.destination,
                market=leg.market,
                currency=leg.currency,
                departure_at=leg.depart_date,
                return_at=None,
                one_way=True,
                limit=30,
            )
        except aviasales.AviasalesError as exc:
            LOG.warning(
                "освежение перелёта %s->%s %s не удалось: %s",
                leg.origin,
                leg.destination,
                leg.depart_date,
                exc,
            )
            continue
        enriched = fx.enrich(aviasales.filter_records(records, cfg), rates, cfg)
        inserted += store.insert_prices(conn, enriched, now=now)
    return inserted
```

В `run_digest` заменить блок от `else:` до конца функции:

```python
    else:
        refresh = _refresh_trip_legs if cfg.trip is not None else _refresh_best_prices
        refreshed = refresh(conn, cfg, session, token, rates, now)

    routes = len(cfg.routes())
    if cfg.trip is not None:
        result = trip_digest.build_text(conn, cfg, now=now)
        notify.send_message(session, bot_token, chat_id, result.text)
        if result.best_total is not None:
            store.set_meta(conn, trip_digest.PREV_KEY, f"{result.best_total:.2f}")
        conn.close()
        return {"sent": True, "routes": routes, "refreshed": refreshed}

    text = digest.build_digest_text(conn, cfg, now=now)
    conn.close()

    notify.send_message(session, bot_token, chat_id, text)
    return {"sent": True, "routes": routes, "refreshed": refreshed}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_runner_trip.py tests/test_runner.py tests/test_runner_alerts.py -v`
Expected: PASS (новые 4 теста и все старые тесты runner)

- [ ] **Step 5: Run the whole suite**

Run: `.venv/bin/pytest -q && bash tests/test_db_sync.sh`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/runner.py tests/test_runner_trip.py
git commit -m "feat: run the digest and alerts in trip mode"
```

---

### Task 7: Боевой конфиг поездки

**Files:**
- Modify: `config.yaml`, `docs/superpowers/specs/2026-10-22-multi-leg-trip-design.md`

- [ ] **Step 1: Обновить `config.yaml`**

Заменить строки `origins:` и `destinations:` (вместе с комментарием про BSZ над
ними оставить комментарий как есть) на:

```yaml
# В режиме поездки маршруты берутся из trip.stops (см. ниже), а эти списки
# пусты. Вернуть мониторинг пары направлений — заполнить их и убрать trip.
origins: []
destinations: []
```

Заменить блок `departure_months` и `return_months`:

```yaml
# Поездка в конце января 2027; последний перелёт может прийтись на февраль.
departure_months: ["2027-01", "2027-02"]
return_months: ["2027-01"]  # не применяется при one_way
```

Заменить `cache_ttl_hours: 2` и комментарий над ним:

```yaml
# Скан идёт раз в час (первый проход рана, см. .github/workflows/scan.yml).
# Три часа — чтобы один пропущенный планировщиком запуск не выбрасывал живые
# цены из сводки.
cache_ttl_hours: 3
```

В комментарии над `min_offer_lifetime_minutes` заменить фразу «Проходы скана
идут раз в 14 минут, так что запись, увиденная хотя бы дважды, порог проходит.»
на «Скан идёт раз в час, так что запись, увиденная хотя бы в двух сканах, порог
проходит.»

Заменить `timezone: "Asia/Bishkek"` на `timezone: "Asia/Makassar"  # Бали, UTC+8`.

В конец файла добавить:

```yaml
# Поездка Бали → Пхукет → Вьетнам → Филиппины → Бали.
# Покрытие кэша замерено 22.10.2026 (docs/superpowers/specs/2026-10-22-multi-leg-trip-design.md):
# PPS (Пуэрто-Принсеса) почти пуст, поэтому не включён — добавить одной строкой.
trip:
  stops:
    - [DPS]
    - [HKT]
    - [SGN, HAN, DAD, PQC, CXR]
    - [MNL, CEB, MPH]
    - [DPS]
  latest_start: "2027-01-22"
  max_total_days: 14
  min_stay_days: 3
  alert_total_usd: 380
  top_n: 3
```

- [ ] **Step 2: Проверить загрузку**

Run: `.venv/bin/python -c "from src.config import Config; c = Config.load('config.yaml'); print(len(c.routes()), c.routes()[:3])"`
Expected: `24 [('DPS', 'HKT'), ('HKT', 'SGN'), ('HKT', 'HAN')]`

24 пары × 2 рынка × 2 месяца = 96 запросов за скан.

- [ ] **Step 3: Поправить число пар в спецификации**

В `docs/superpowers/specs/2026-10-22-multi-leg-trip-design.md`, раздел «Скан»,
заменить «30 пар × 2 рынка × 2 месяца = 120 запросов за скан, столько же,
сколько в замере.» на «24 пары (без PPS) × 2 рынка × 2 месяца = 96 запросов за
скан — меньше, чем в замере (120 без единой ошибки).» В разделе «Алерты»
заменить «на 30 парах» на «на 24 парах».

- [ ] **Step 4: Прогнать тесты**

Run: `.venv/bin/pytest -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add config.yaml docs/superpowers/specs/2026-10-22-multi-leg-trip-design.md
git commit -m "feat: switch the config to the January multi-leg trip"
```

---

### Task 8: Расписание воркфлоу

**Files:**
- Modify: `.github/workflows/scan.yml`, `.github/workflows/digest.yml`

- [ ] **Step 1: Скан только в первом проходе**

В `.github/workflows/scan.yml` заменить блок от `echo "скан: итерация $i старт"`
до первого `fi` после него на:

```bash
            # Кэш на даты через три месяца меняется медленно, а скан поездки —
            # почти сотня запросов. Сканируем раз в час (первый проход), а
            # команды Telegram по-прежнему обрабатываем в каждом проходе.
            if [ "$i" -eq 1 ]; then
              echo "скан: итерация $i старт"
              python monitor.py scan
              if [ $? -ne 0 ]; then
                echo "::warning::скан: итерация $i упала, продолжаю цикл"
              else
                echo "скан: итерация $i завершена успешно"
              fi
            fi
```

- [ ] **Step 2: Сводка в 09:00 по Бали**

В `.github/workflows/digest.yml` заменить
`- cron: "0 3 * * *" # 09:00 Asia/Bishkek (UTC+6)` на
`- cron: "0 1 * * *" # 09:00 Asia/Makassar (Бали, UTC+8)`.

- [ ] **Step 3: Проверить синтаксис YAML**

Run: `.venv/bin/python -c "import yaml; [yaml.safe_load(open(f'.github/workflows/{n}.yml')) for n in ('scan', 'digest')]; print('ok')"`
Expected: `ok`

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/scan.yml .github/workflows/digest.yml
git commit -m "feat: scan hourly and send the digest at 09:00 Bali time"
```

---

### Task 9: README

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Обновить README**

Заменить вторую строку README («Личный монитор цен FRU/ALA → HKT/DPS на октябрь
2026. Интерфейс — только Telegram.») на:

```markdown
Личный монитор цен на авиабилеты. Сейчас следит за поездкой Бали → Пхукет →
Вьетнам → Филиппины → Бали в конце января 2027. Интерфейс — только Telegram.
```

В таблице «Как это работает» заменить строки `scan` и `digest`:

```markdown
| `scan` | раз в час, 4 прохода внутри рана | в первом проходе — запросы к кэшу Aviasales по всем перелётам поездки (24 пары × 2 рынка × 2 месяца = 96), запись истории; в каждом проходе — команды из Telegram |
| `digest` | 01:00 UTC (09:00 Бали) | три самых дешёвых маршрута поездки целиком |
```

После раздела «Почему `scan` не запускается по частому cron» добавить:

```markdown
## Режим поездки

Если в `config.yaml` есть секция `trip`, бот следит не за парой направлений, а
за маршрутом из нескольких перелётов. `trip.stops` — остановки по порядку, в
каждой — аэропорты на выбор. Скан обходит все пары соседних остановок,
`src/itinerary.py` собирает из лучших цен маршруты с ограничениями
(`latest_start`, `min_stay_days`, `max_total_days`), а сводка показывает
`top_n` самых дешёвых с разными городами. Алерт приходит, когда лучший маршрут
дешевле `alert_total_usd`, повторно — только при падении ещё на 5%.

Цены — из кэша, на одного взрослого, без багажа. Живой поиск по запросу
(`/check`) — следующий этап, см.
`docs/superpowers/specs/2026-10-22-multi-leg-trip-design.md`.
```

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs: describe the trip mode in the README"
```

---

### Task 10: Запуск

- [ ] **Step 1: Финальная проверка**

Run: `.venv/bin/pytest -q && bash tests/test_db_sync.sh`
Expected: PASS

- [ ] **Step 2: Пробный скан и сводка локально на копии базы**

```bash
SP=/private/tmp/claude-501/-Users-rinari-Fly-tool/3887eb4e-9a59-4924-83b4-9f1bcdfc2db1/scratchpad
git fetch -q origin data && git show origin/data:history.sqlite > $SP/history.sqlite
set -a && . ./.env && set +a
.venv/bin/python monitor.py --db $SP/history.sqlite scan
```

Expected: в логе `скан: получено N, вставлено M, ошибок 0`, N > 0. Сводку
локально не отправлять (она уйдёт в Telegram владельцу) — проверить текст:

```bash
.venv/bin/python -c "
from src.config import Config; from src import store, trip_digest
cfg = Config.load('config.yaml'); conn = store.connect('$SP/history.sqlite')
cfg = cfg.with_overrides(store.all_meta(conn))
from dataclasses import replace; cfg = replace(cfg, min_offer_lifetime_minutes=0)
print(trip_digest.build_text(conn, cfg, now=store.utcnow()).text)"
```

Expected: заголовок поездки и три маршрута порядка $400–450. База лежит в
корне ветки `data` под именем `history.sqlite` (см. `scripts/db_pull.sh`).

- [ ] **Step 3: Отправить на GitHub**

```bash
git push origin main
```

- [ ] **Step 4: Включить воркфлоу**

```bash
gh workflow enable scan.yml && gh workflow enable digest.yml && gh workflow enable keepalive.yml
gh workflow list --all
```

Expected: `scan`, `digest`, `keepalive` — `active`.

- [ ] **Step 5: Запустить скан и проверить ран**

```bash
gh workflow run scan.yml
gh run list --workflow scan.yml --limit 1
```

Дождаться завершения первого прохода (~2–5 минут) и проверить лог:
`gh run view <id> --log | grep "скан:"` → `ошибок 0`.

- [ ] **Step 6: Сообщить владельцу**

Первая сводка с маршрутами придёт в 09:00 по Бали после двух сканов (офферы
должны пройти порог времени жизни). Ручной запуск сводки —
`gh workflow run digest.yml`, только с согласия владельца.
