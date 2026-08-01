# flight-sniper v1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ежедневный Telegram-дайджест цен на перелёты FRU/ALA → HKT/DPS на октябрь 2026, собранный бесплатным кэшем Aviasales и приведённый к единой метрике landed USD.

**Architecture:** GitHub Actions по cron дёргает `monitor.py scan` (каждые 4 часа) и `monitor.py digest` (03:00 UTC). SQLite-база живёт в orphan-ветке `data` (история в один коммит, force-push), подтягивается в начале рана и выталкивается в конце. Внутри — чистые функции: HTTP-слой отделён от парсинга, парсинг от статистики, статистика от рендера, поэтому всё тестируется на моках без сетевых вызовов.

**Tech Stack:** Python 3.11, `requests`, `PyYAML`, стандартный `sqlite3`, `pytest` + `responses` для тестов, GitHub Actions, Telegram Bot API, Aviasales Data API v3, open.er-api.com для курсов.

---

## Отклонения от спеки (сознательные, согласованы)

1. **Хранение БД.** Спека: коммит `data/history.sqlite` в main после каждого рана. Здесь: orphan-ветка `data`, каждый ран перезаписывает её одним коммитом без родителя (force-push). Причина: ~360 коммитов бинарника за два месяца раздули бы репозиторий до гигабайта. Main остаётся чистым.
2. **Изменяемое состояние — в БД, а не в config.yaml.** `abs_threshold_usd` (команда `/threshold`), `paused`, telegram offset и т.п. пишутся в таблицу `meta`. `config.yaml` — только дефолты, он не переписывается воркфлоу. Причина: три воркфлоу, коммитящие один файл в main, — гарантированные гонки и мусорная история; плюс `yaml.safe_dump` убил бы комментарии в конфиге.
3. **Дедупликация офферов при вставке.** Спека: «писать ВСЕ записи». Здесь: уникальный индекс по идентичности оффера (маршрут + даты + а/к + пересадки + цена); повтор того же оффера обновляет `last_seen_at` вместо новой строки. История цен полностью сохраняется (любое изменение цены = новая строка), но объём падает на порядок.
4. **`expires_at` в v3 API, скорее всего, отсутствует.** Поле есть в legacy `/v1/prices/cheap`, в `/aviasales/v3/prices_for_dates` его в документации нет. Парсер читает поле опционально; свежесть записи определяется по `last_seen_at` в пределах 48 часов. Проверить на живом токене — задача 18.
5. **`max_transfer_hours` считается приближённо.** API не отдаёт длительность пересадки. Отсекаем по длительности плеча относительно самого короткого плеча на этом маршруте: `duration_to > min_duration_to + max_transfer_hours * 60` → отбросить.
6. **`digest.py` вынесён отдельно от `notify.py`.** Спека складывала рендер в notify; рендер — чистая функция и главный объект тестирования, транспорт — тонкая обёртка над HTTP.
7. **Запросов за скан 16, а не 8.** Возврат в октябре и ноябре — это два разных значения `return_at`, значит 4 маршрута × 2 рынка × 2 месяца возврата. Всё ещё доли процента от лимитов.

---

## File Structure

| Файл | Ответственность |
|---|---|
| `config.yaml` | Дефолты: маршруты, рынки, пороги, валюты рынков |
| `monitor.py` | CLI-вход: `scan`, `backfill`, `digest`. Только связывание, без логики |
| `src/models.py` | `PriceRecord` — единственный тип, которым обмениваются слои |
| `src/config.py` | Загрузка/валидация конфига, оверрайды из `meta` |
| `src/store.py` | SQLite: схема, вставка с дедупом, выборки, meta |
| `src/fx.py` | Курсы валют, `landed_usd`, обогащение записей |
| `src/sources/aviasales.py` | Data API v3: HTTP, парсинг, фильтры, скан, бэкфилл |
| `src/rules.py` | Перцентили, baseline, светофор |
| `src/airlines.py` | IATA-код → человекочитаемое имя |
| `src/digest.py` | Сборка и рендер текста дайджеста |
| `src/notify.py` | Telegram sendMessage, нарезка длинных сообщений |
| `scripts/db_pull.sh`, `scripts/db_push.sh` | Синхронизация БД с веткой `data` |
| `.github/workflows/*.yml` | scan, digest, keepalive |
| `tests/**` | pytest, все внешние вызовы замоканы |

---

### Task 1: Bootstrap проекта

**Files:**
- Create: `.gitignore`, `requirements.txt`, `requirements-dev.txt`, `pytest.ini`, `README.md`, `src/__init__.py`, `src/sources/__init__.py`, `tests/__init__.py`

- [ ] **Step 1: Инициализировать git и структуру каталогов**

```bash
cd /Users/rinari/Fly-tool
git init -b main
mkdir -p src/sources tests/sources data docs/superpowers/plans scripts .github/workflows
touch src/__init__.py src/sources/__init__.py tests/__init__.py tests/sources/__init__.py
```

- [ ] **Step 2: Создать `.gitignore`**

```gitignore
__pycache__/
*.py[cod]
.venv/
venv/
.pytest_cache/
.DS_Store
data/*.sqlite
data/*.sqlite-journal
.env
```

- [ ] **Step 3: Создать `requirements.txt`**

```
requests==2.32.3
PyYAML==6.0.2
```

- [ ] **Step 4: Создать `requirements-dev.txt`**

```
-r requirements.txt
pytest==8.3.3
responses==0.25.3
```

- [ ] **Step 5: Создать `pytest.ini`**

```ini
[pytest]
testpaths = tests
python_files = test_*.py
addopts = -q
```

- [ ] **Step 6: Создать виртуальное окружение и установить зависимости**

```bash
cd /Users/rinari/Fly-tool
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
```

Ожидается: `Successfully installed ... pytest-8.3.3 requests-2.32.3 responses-0.25.3 PyYAML-6.0.2`

- [ ] **Step 7: Проверить, что pytest запускается**

Run: `.venv/bin/pytest`
Ожидается: `no tests ran` (exit code 5) — это нормально, тестов ещё нет.

- [ ] **Step 8: Коммит**

```bash
git add -A
git commit -m "chore: bootstrap flight-sniper project skeleton"
```

---

### Task 2: config.yaml и загрузка конфига

**Files:**
- Create: `config.yaml`, `src/config.py`
- Test: `tests/test_config.py`

- [ ] **Step 1: Создать `config.yaml`**

```yaml
origins: [FRU, ALA]
destinations: [HKT, DPS]
markets: [kg, ru]
market_currency:
  kg: kgs
  ru: rub
cross_market_delta: 0.05
fx_markup:
  default: 0.025
  usd: 0.0
departure_month: "2026-10"
return_months: ["2026-10", "2026-11"]
trip_type: round_trip
nights_range: [10, 16]
report_currency: usd
max_transfer_hours: 12
abs_threshold_usd: 250
anomaly_percentile: 10
yellow_delta: 0.15
baseline_window_days: 30
cache_ttl_hours: 48
digest_time: "09:00"
timezone: "Asia/Bishkek"
```

- [ ] **Step 2: Написать падающий тест**

```python
# tests/test_config.py
import textwrap

import pytest

from src.config import Config


@pytest.fixture()
def config_path(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(
        textwrap.dedent(
            """
            origins: [FRU, ALA]
            destinations: [HKT, DPS]
            markets: [kg, ru]
            market_currency:
              kg: kgs
              ru: rub
            cross_market_delta: 0.05
            fx_markup:
              default: 0.025
              usd: 0.0
            departure_month: "2026-10"
            return_months: ["2026-10", "2026-11"]
            trip_type: round_trip
            nights_range: [10, 16]
            report_currency: usd
            max_transfer_hours: 12
            abs_threshold_usd: 250
            anomaly_percentile: 10
            yellow_delta: 0.15
            baseline_window_days: 30
            cache_ttl_hours: 48
            digest_time: "09:00"
            timezone: "Asia/Bishkek"
            """
        ),
        encoding="utf-8",
    )
    return path


def test_load_reads_all_fields(config_path):
    cfg = Config.load(config_path)
    assert cfg.origins == ["FRU", "ALA"]
    assert cfg.markets == ["kg", "ru"]
    assert cfg.nights_range == (10, 16)
    assert cfg.abs_threshold_usd == 250


def test_routes_is_cartesian_product(config_path):
    cfg = Config.load(config_path)
    assert cfg.routes() == [
        ("FRU", "HKT"),
        ("FRU", "DPS"),
        ("ALA", "HKT"),
        ("ALA", "DPS"),
    ]


def test_currency_for_market(config_path):
    cfg = Config.load(config_path)
    assert cfg.currency_for("kg") == "kgs"
    assert cfg.currency_for("ru") == "rub"


def test_currency_for_unknown_market_raises(config_path):
    cfg = Config.load(config_path)
    with pytest.raises(KeyError):
        cfg.currency_for("tj")


def test_markup_falls_back_to_default(config_path):
    cfg = Config.load(config_path)
    assert cfg.markup_for("usd") == 0.0
    assert cfg.markup_for("kgs") == 0.025


def test_with_overrides_replaces_threshold(config_path):
    cfg = Config.load(config_path)
    updated = cfg.with_overrides({"abs_threshold_usd": "199.5"})
    assert updated.abs_threshold_usd == 199.5
    assert cfg.abs_threshold_usd == 250  # исходный объект не мутирован


def test_with_overrides_ignores_unknown_keys(config_path):
    cfg = Config.load(config_path)
    updated = cfg.with_overrides({"nonsense": "1"})
    assert updated.abs_threshold_usd == 250


def test_missing_required_key_raises(tmp_path):
    path = tmp_path / "bad.yaml"
    path.write_text("origins: [FRU]\n", encoding="utf-8")
    with pytest.raises(ValueError, match="destinations"):
        Config.load(path)
```

- [ ] **Step 3: Запустить тесты и убедиться, что падают**

Run: `.venv/bin/pytest tests/test_config.py -v`
Ожидается: FAIL, `ModuleNotFoundError: No module named 'src.config'`

- [ ] **Step 4: Реализовать `src/config.py`**

```python
"""Загрузка конфигурации flight-sniper.

config.yaml содержит дефолты и не перезаписывается воркфлоу. Изменяемое
состояние (например, порог из команды /threshold) живёт в таблице meta
и накладывается поверх через with_overrides().
"""

from __future__ import annotations

import dataclasses
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
        return [(o, d) for o in self.origins for d in self.destinations]

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
        if not changes:
            return self
        return dataclasses.replace(self, **changes)

    @property
    def one_way(self) -> bool:
        return self.trip_type != "round_trip"
```

- [ ] **Step 5: Запустить тесты**

Run: `.venv/bin/pytest tests/test_config.py -v`
Ожидается: 8 passed

- [ ] **Step 6: Коммит**

```bash
git add config.yaml src/config.py tests/test_config.py
git commit -m "feat: config loading with meta overrides"
```

---

### Task 3: Модель PriceRecord

**Files:**
- Create: `src/models.py`
- Test: `tests/test_models.py`

- [ ] **Step 1: Написать падающий тест**

```python
# tests/test_models.py
import dataclasses

from src.models import PriceRecord


def make_record(**kwargs) -> PriceRecord:
    base = dict(
        source="aviasales_cache",
        origin="FRU",
        destination="HKT",
        market="kg",
        depart_date="2026-10-12",
        return_date="2026-10-24",
        price_local=38000.0,
        currency="kgs",
        airline="KC",
        transfers=1,
        duration_min=1500,
        duration_to_min=760,
        search_url="https://www.aviasales.kg/search/FRU1210HKT2410",
    )
    base.update(kwargs)
    return PriceRecord(**base)


def test_record_is_frozen():
    record = make_record()
    try:
        record.price_local = 1.0
    except dataclasses.FrozenInstanceError:
        return
    raise AssertionError("PriceRecord должен быть неизменяемым")


def test_nights_counts_days_between_dates():
    assert make_record().nights == 12


def test_nights_is_none_for_one_way():
    assert make_record(return_date=None).nights is None


def test_route_key_identifies_route_and_dates():
    assert make_record().route_key == "FRU-HKT-2026-10-12-2026-10-24"


def test_route_key_for_one_way_omits_return():
    assert make_record(return_date=None).route_key == "FRU-HKT-2026-10-12-"


def test_with_landed_returns_new_record():
    record = make_record()
    enriched = record.with_landed(fx_rate=0.0114, landed_usd=444.0)
    assert enriched.landed_usd == 444.0
    assert enriched.fx_rate == 0.0114
    assert record.landed_usd is None
```

- [ ] **Step 2: Запустить тест, убедиться в падении**

Run: `.venv/bin/pytest tests/test_models.py -v`
Ожидается: FAIL, `ModuleNotFoundError: No module named 'src.models'`

- [ ] **Step 3: Реализовать `src/models.py`**

```python
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
```

- [ ] **Step 4: Запустить тесты**

Run: `.venv/bin/pytest tests/test_models.py -v`
Ожидается: 6 passed

- [ ] **Step 5: Коммит**

```bash
git add src/models.py tests/test_models.py
git commit -m "feat: PriceRecord model"
```

---

### Task 4: Схема SQLite и подключение

**Files:**
- Create: `src/store.py`
- Test: `tests/test_store_schema.py`

- [ ] **Step 1: Написать падающий тест**

```python
# tests/test_store_schema.py
from src import store


def test_connect_creates_file_and_schema(tmp_path):
    db = tmp_path / "history.sqlite"
    conn = store.connect(db)
    store.init_schema(conn)
    tables = {
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert {"price_history", "alerts_sent", "meta", "api_usage"} <= tables
    assert db.exists()


def test_init_schema_is_idempotent(tmp_path):
    conn = store.connect(tmp_path / "history.sqlite")
    store.init_schema(conn)
    store.init_schema(conn)  # не должно бросать
    assert store.count_rows(conn) == 0


def test_rows_come_back_as_mappings(tmp_path):
    conn = store.connect(tmp_path / "history.sqlite")
    store.init_schema(conn)
    conn.execute(
        "INSERT INTO price_history (scanned_at, last_seen_at, source, origin, destination,"
        " market, depart_date, price_local, currency) VALUES"
        " ('2026-08-01T00:00:00Z','2026-08-01T00:00:00Z','aviasales_cache','FRU','HKT','kg',"
        " '2026-10-12', 38000.0, 'kgs')"
    )
    row = conn.execute("SELECT * FROM price_history").fetchone()
    assert row["origin"] == "FRU"


def test_meta_roundtrip(tmp_path):
    conn = store.connect(tmp_path / "history.sqlite")
    store.init_schema(conn)
    assert store.get_meta(conn, "last_scan_at") is None
    assert store.get_meta(conn, "last_scan_at", "never") == "never"
    store.set_meta(conn, "last_scan_at", "2026-08-01T06:00:00Z")
    assert store.get_meta(conn, "last_scan_at") == "2026-08-01T06:00:00Z"
    store.set_meta(conn, "last_scan_at", "2026-08-01T10:00:00Z")
    assert store.get_meta(conn, "last_scan_at") == "2026-08-01T10:00:00Z"


def test_all_meta_returns_dict(tmp_path):
    conn = store.connect(tmp_path / "history.sqlite")
    store.init_schema(conn)
    store.set_meta(conn, "paused", "1")
    store.set_meta(conn, "abs_threshold_usd", "199")
    assert store.all_meta(conn) == {"paused": "1", "abs_threshold_usd": "199"}
```

- [ ] **Step 2: Запустить тест, убедиться в падении**

Run: `.venv/bin/pytest tests/test_store_schema.py -v`
Ожидается: FAIL, `ModuleNotFoundError: No module named 'src.store'`

- [ ] **Step 3: Реализовать первую часть `src/store.py`**

```python
"""SQLite-хранилище истории цен.

Файл базы синхронизируется с orphan-веткой `data`, поэтому режим журнала —
дефолтный (rollback), чтобы база всегда была одним файлом без -wal/-shm.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS price_history (
  id INTEGER PRIMARY KEY,
  scanned_at TEXT NOT NULL,
  last_seen_at TEXT NOT NULL,
  source TEXT NOT NULL,
  origin TEXT NOT NULL,
  destination TEXT NOT NULL,
  market TEXT NOT NULL,
  depart_date TEXT NOT NULL,
  return_date TEXT,
  price_local REAL NOT NULL,
  currency TEXT NOT NULL,
  fx_rate REAL,
  landed_usd REAL,
  airline TEXT,
  transfers INTEGER,
  duration_min INTEGER,
  duration_to_min INTEGER,
  expires_at TEXT,
  search_url TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_offer_identity ON price_history(
  source, market, origin, destination, depart_date,
  IFNULL(return_date, ''), IFNULL(airline, ''), IFNULL(transfers, -1), price_local
);
CREATE INDEX IF NOT EXISTS idx_route_date ON price_history(origin, destination, depart_date);
CREATE INDEX IF NOT EXISTS idx_route_seen ON price_history(origin, destination, last_seen_at);

CREATE TABLE IF NOT EXISTS alerts_sent (
  id INTEGER PRIMARY KEY,
  route_date_key TEXT NOT NULL,
  landed_usd REAL NOT NULL,
  sent_at TEXT NOT NULL,
  level TEXT NOT NULL,
  feedback TEXT
);
CREATE INDEX IF NOT EXISTS idx_alerts_key ON alerts_sent(route_date_key, sent_at);

CREATE TABLE IF NOT EXISTS meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS api_usage (
  id INTEGER PRIMARY KEY,
  api TEXT NOT NULL,
  called_at TEXT NOT NULL,
  purpose TEXT
);
CREATE INDEX IF NOT EXISTS idx_usage_api_time ON api_usage(api, called_at);
"""


def connect(path: str | Path) -> sqlite3.Connection:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


def count_rows(conn: sqlite3.Connection) -> int:
    return int(conn.execute("SELECT COUNT(*) FROM price_history").fetchone()[0])


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO meta (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, str(value)),
    )
    conn.commit()


def get_meta(conn: sqlite3.Connection, key: str, default: Optional[str] = None) -> Optional[str]:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def all_meta(conn: sqlite3.Connection) -> dict[str, str]:
    return {row["key"]: row["value"] for row in conn.execute("SELECT key, value FROM meta")}
```

- [ ] **Step 4: Запустить тесты**

Run: `.venv/bin/pytest tests/test_store_schema.py -v`
Ожидается: 5 passed

- [ ] **Step 5: Коммит**

```bash
git add src/store.py tests/test_store_schema.py
git commit -m "feat: sqlite schema and meta key-value store"
```

---

### Task 5: Вставка цен с дедупом и выборки

**Files:**
- Modify: `src/store.py` (дописать функции в конец файла)
- Test: `tests/test_store_prices.py`

- [ ] **Step 1: Написать падающий тест**

```python
# tests/test_store_prices.py
import pytest

from src import store
from src.models import PriceRecord


@pytest.fixture()
def conn(tmp_path):
    connection = store.connect(tmp_path / "history.sqlite")
    store.init_schema(connection)
    return connection


def make_record(**kwargs) -> PriceRecord:
    base = dict(
        source="aviasales_cache",
        origin="FRU",
        destination="HKT",
        market="kg",
        depart_date="2026-10-12",
        return_date="2026-10-24",
        price_local=38000.0,
        currency="kgs",
        airline="KC",
        transfers=1,
        duration_min=1500,
        duration_to_min=760,
        search_url="https://www.aviasales.kg/search/x",
        fx_rate=0.0114,
        landed_usd=444.0,
    )
    base.update(kwargs)
    return PriceRecord(**base)


def test_insert_writes_row_and_returns_count(conn):
    inserted = store.insert_prices(conn, [make_record()], now="2026-08-01T06:00:00Z")
    assert inserted == 1
    row = conn.execute("SELECT * FROM price_history").fetchone()
    assert row["landed_usd"] == 444.0
    assert row["scanned_at"] == "2026-08-01T06:00:00Z"
    assert row["last_seen_at"] == "2026-08-01T06:00:00Z"


def test_identical_offer_updates_last_seen_instead_of_duplicating(conn):
    store.insert_prices(conn, [make_record()], now="2026-08-01T06:00:00Z")
    inserted = store.insert_prices(conn, [make_record()], now="2026-08-01T10:00:00Z")
    assert inserted == 0
    assert store.count_rows(conn) == 1
    row = conn.execute("SELECT * FROM price_history").fetchone()
    assert row["scanned_at"] == "2026-08-01T06:00:00Z"  # первое появление сохранено
    assert row["last_seen_at"] == "2026-08-01T10:00:00Z"


def test_price_change_creates_new_row(conn):
    store.insert_prices(conn, [make_record()], now="2026-08-01T06:00:00Z")
    store.insert_prices(conn, [make_record(price_local=35000.0)], now="2026-08-01T10:00:00Z")
    assert store.count_rows(conn) == 2


def test_different_market_creates_new_row(conn):
    store.insert_prices(conn, [make_record()], now="2026-08-01T06:00:00Z")
    store.insert_prices(
        conn, [make_record(market="ru", currency="rub")], now="2026-08-01T06:00:00Z"
    )
    assert store.count_rows(conn) == 2


def test_recent_prices_filters_by_route_and_window(conn):
    store.insert_prices(
        conn,
        [
            make_record(price_local=38000.0),
            make_record(destination="DPS", price_local=50000.0),
        ],
        now="2026-08-01T06:00:00Z",
    )
    store.insert_prices(
        conn, [make_record(price_local=30000.0)], now="2026-06-01T06:00:00Z"
    )
    rows = store.recent_prices(conn, "FRU", "HKT", since="2026-07-01T00:00:00Z")
    assert [r["price_local"] for r in rows] == [38000.0]


def test_recent_prices_filters_by_market(conn):
    store.insert_prices(
        conn,
        [make_record(), make_record(market="ru", currency="rub", price_local=41000.0)],
        now="2026-08-01T06:00:00Z",
    )
    rows = store.recent_prices(conn, "FRU", "HKT", since="2026-07-01T00:00:00Z", market="ru")
    assert [r["market"] for r in rows] == ["ru"]


def test_fresh_prices_excludes_stale_cache(conn):
    store.insert_prices(conn, [make_record(price_local=38000.0)], now="2026-08-01T00:00:00Z")
    store.insert_prices(conn, [make_record(price_local=31000.0)], now="2026-07-25T00:00:00Z")
    rows = store.fresh_prices(
        conn, "FRU", "HKT", now="2026-08-01T12:00:00Z", ttl_hours=48
    )
    assert [r["price_local"] for r in rows] == [38000.0]


def test_fresh_prices_respects_expires_at_in_the_past(conn):
    store.insert_prices(
        conn,
        [make_record(price_local=38000.0, expires_at="2026-07-31T00:00:00Z")],
        now="2026-08-01T00:00:00Z",
    )
    rows = store.fresh_prices(conn, "FRU", "HKT", now="2026-08-01T12:00:00Z", ttl_hours=48)
    assert rows == []


def test_prune_removes_old_rows(conn):
    store.insert_prices(conn, [make_record()], now="2026-01-01T00:00:00Z")
    store.insert_prices(conn, [make_record(price_local=1.0)], now="2026-08-01T00:00:00Z")
    removed = store.prune(conn, before="2026-06-01T00:00:00Z")
    assert removed == 1
    assert store.count_rows(conn) == 1
```

- [ ] **Step 2: Запустить тест, убедиться в падении**

Run: `.venv/bin/pytest tests/test_store_prices.py -v`
Ожидается: FAIL, `AttributeError: module 'src.store' has no attribute 'insert_prices'`

- [ ] **Step 3: Дописать функции в `src/store.py`**

```python
# --- дописать в конец src/store.py ---

from datetime import datetime, timedelta, timezone as _timezone  # noqa: E402
from typing import Iterable, Sequence  # noqa: E402

from src.models import PriceRecord  # noqa: E402

_INSERT = """
INSERT INTO price_history (
  scanned_at, last_seen_at, source, origin, destination, market,
  depart_date, return_date, price_local, currency, fx_rate, landed_usd,
  airline, transfers, duration_min, duration_to_min, expires_at, search_url
) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
ON CONFLICT DO UPDATE SET last_seen_at = excluded.last_seen_at
"""


def insert_prices(conn: sqlite3.Connection, records: Iterable[PriceRecord], now: str) -> int:
    """Вставляет записи. Идентичный оффер не дублируется — обновляется last_seen_at.

    Возвращает число реально созданных строк.
    """
    before = count_rows(conn)
    conn.executemany(
        _INSERT,
        [
            (
                now,
                now,
                r.source,
                r.origin,
                r.destination,
                r.market,
                r.depart_date,
                r.return_date,
                r.price_local,
                r.currency,
                r.fx_rate,
                r.landed_usd,
                r.airline,
                r.transfers,
                r.duration_min,
                r.duration_to_min,
                r.expires_at,
                r.search_url,
            )
            for r in records
        ],
    )
    conn.commit()
    return count_rows(conn) - before


def recent_prices(
    conn: sqlite3.Connection,
    origin: str,
    destination: str,
    since: str,
    market: Optional[str] = None,
    source: Optional[str] = None,
) -> list[sqlite3.Row]:
    sql = (
        "SELECT * FROM price_history WHERE origin = ? AND destination = ? AND last_seen_at >= ?"
    )
    params: list[object] = [origin, destination, since]
    if market is not None:
        sql += " AND market = ?"
        params.append(market)
    if source is not None:
        sql += " AND source = ?"
        params.append(source)
    sql += " ORDER BY last_seen_at"
    return list(conn.execute(sql, params))


def fresh_prices(
    conn: sqlite3.Connection,
    origin: str,
    destination: str,
    now: str,
    ttl_hours: int,
    market: Optional[str] = None,
) -> list[sqlite3.Row]:
    """Записи, которые ещё можно показывать пользователю.

    Кэш Aviasales живёт ~48 часов. Протухшие строки остаются в базе для
    статистики, но в дайджест и алерты не попадают.
    """
    cutoff = shift_hours(now, -ttl_hours)
    rows = recent_prices(conn, origin, destination, since=cutoff, market=market)
    return [r for r in rows if r["expires_at"] is None or r["expires_at"] > now]


def prune(conn: sqlite3.Connection, before: str) -> int:
    cursor = conn.execute("DELETE FROM price_history WHERE last_seen_at < ?", (before,))
    conn.commit()
    return cursor.rowcount


def shift_hours(iso_ts: str, hours: float) -> str:
    moment = datetime.fromisoformat(iso_ts.replace("Z", "+00:00"))
    return (moment + timedelta(hours=hours)).astimezone(_timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def shift_days(iso_ts: str, days: float) -> str:
    return shift_hours(iso_ts, days * 24)


def utcnow() -> str:
    return datetime.now(_timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
```

- [ ] **Step 4: Запустить тесты**

Run: `.venv/bin/pytest tests/test_store_prices.py -v`
Ожидается: 9 passed

- [ ] **Step 5: Прогнать весь набор**

Run: `.venv/bin/pytest`
Ожидается: 28 passed

- [ ] **Step 6: Коммит**

```bash
git add src/store.py tests/test_store_prices.py
git commit -m "feat: price insertion with offer dedup, freshness and pruning"
```

---

### Task 6: Курсы валют и landed cost

**Files:**
- Create: `src/fx.py`
- Test: `tests/test_fx.py`

- [ ] **Step 1: Написать падающий тест**

```python
# tests/test_fx.py
import pytest
import requests
import responses

from src import fx
from src.config import Config
from src.models import PriceRecord

RATES_PAYLOAD = {
    "result": "success",
    "base_code": "USD",
    "time_last_update_unix": 1785000000,
    "rates": {"USD": 1.0, "KGS": 87.5, "RUB": 92.0},
}


@pytest.fixture()
def session():
    return requests.Session()


@responses.activate
def test_fetch_usd_rates_returns_lowercase_mapping(session):
    responses.add(responses.GET, fx.FX_URL, json=RATES_PAYLOAD, status=200)
    rates = fx.fetch_usd_rates(session)
    assert rates["kgs"] == 87.5
    assert rates["rub"] == 92.0
    assert rates["usd"] == 1.0


@responses.activate
def test_fetch_usd_rates_raises_on_error_result(session):
    responses.add(
        responses.GET, fx.FX_URL, json={"result": "error", "error-type": "quota"}, status=200
    )
    with pytest.raises(fx.FxError, match="quota"):
        fx.fetch_usd_rates(session)


@responses.activate
def test_fetch_usd_rates_raises_on_http_error(session):
    responses.add(responses.GET, fx.FX_URL, json={}, status=503)
    with pytest.raises(fx.FxError):
        fx.fetch_usd_rates(session)


def test_usd_per_unit_inverts_the_rate():
    assert fx.usd_per_unit({"kgs": 87.5}, "kgs") == pytest.approx(1 / 87.5)


def test_usd_per_unit_is_case_insensitive():
    assert fx.usd_per_unit({"kgs": 87.5}, "KGS") == pytest.approx(1 / 87.5)


def test_usd_per_unit_raises_for_unknown_currency():
    with pytest.raises(fx.FxError, match="tjs"):
        fx.usd_per_unit({"kgs": 87.5}, "tjs")


def test_landed_usd_applies_markup():
    # 38000 сом × (1/87.5) USD × 1.025 markup
    assert fx.landed_usd(38000.0, 1 / 87.5, 0.025) == pytest.approx(445.14, abs=0.01)


def test_landed_usd_without_markup():
    assert fx.landed_usd(400.0, 1.0, 0.0) == pytest.approx(400.0)


def test_enrich_fills_fx_rate_and_landed(tmp_path):
    cfg = Config(
        origins=["FRU"],
        destinations=["HKT"],
        markets=["kg"],
        market_currency={"kg": "kgs"},
        cross_market_delta=0.05,
        fx_markup={"default": 0.025, "usd": 0.0},
        departure_month="2026-10",
        return_months=["2026-10"],
        trip_type="round_trip",
        nights_range=(10, 16),
        report_currency="usd",
        max_transfer_hours=12,
        abs_threshold_usd=250,
        anomaly_percentile=10,
        yellow_delta=0.15,
        baseline_window_days=30,
        cache_ttl_hours=48,
        digest_time="09:00",
        timezone="Asia/Bishkek",
    )
    record = PriceRecord(
        source="aviasales_cache",
        origin="FRU",
        destination="HKT",
        market="kg",
        depart_date="2026-10-12",
        return_date="2026-10-24",
        price_local=38000.0,
        currency="kgs",
        airline="KC",
        transfers=1,
        duration_min=1500,
    )
    enriched = fx.enrich([record], {"kgs": 87.5}, cfg)
    assert enriched[0].fx_rate == pytest.approx(1 / 87.5)
    assert enriched[0].landed_usd == pytest.approx(445.14, abs=0.01)


def test_enrich_skips_records_with_unknown_currency():
    cfg_rates = {"kgs": 87.5}
    record = PriceRecord(
        source="aviasales_cache",
        origin="FRU",
        destination="HKT",
        market="xx",
        depart_date="2026-10-12",
        return_date=None,
        price_local=100.0,
        currency="zzz",
        airline="KC",
        transfers=0,
        duration_min=600,
    )
    assert fx.enrich([record], cfg_rates, None) == []
```

- [ ] **Step 2: Запустить тест, убедиться в падении**

Run: `.venv/bin/pytest tests/test_fx.py -v`
Ожидается: FAIL, `ModuleNotFoundError: No module named 'src.fx'`

- [ ] **Step 3: Реализовать `src/fx.py`**

```python
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
```

- [ ] **Step 4: Запустить тесты**

Run: `.venv/bin/pytest tests/test_fx.py -v`
Ожидается: 10 passed

- [ ] **Step 5: Коммит**

```bash
git add src/fx.py tests/test_fx.py
git commit -m "feat: fx rates and landed usd calculation"
```

---

### Task 7: Парсинг ответа Aviasales prices_for_dates

**Files:**
- Create: `src/sources/aviasales.py`
- Test: `tests/sources/test_aviasales_parse.py`

- [ ] **Step 1: Написать падающий тест**

```python
# tests/sources/test_aviasales_parse.py
from src.sources import aviasales

PAYLOAD = {
    "success": True,
    "data": [
        {
            "origin": "FRU",
            "destination": "HKT",
            "origin_airport": "FRU",
            "destination_airport": "HKT",
            "price": 38000,
            "airline": "KC",
            "flight_number": "123",
            "departure_at": "2026-10-12T10:15:00+06:00",
            "return_at": "2026-10-24T20:00:00+07:00",
            "transfers": 1,
            "return_transfers": 1,
            "duration": 1500,
            "duration_to": 760,
            "duration_back": 740,
            "link": "/search/FRU1210HKT2410?t=abc",
            "currency": "kgs",
        },
        {
            "origin": "FRU",
            "destination": "HKT",
            "price": 41000,
            "airline": "TK",
            "departure_at": "2026-10-15T02:00:00+06:00",
            "return_at": None,
            "transfers": 2,
            "duration": 2100,
            "duration_to": 1100,
            "link": "/search/FRU1510HKT?t=def",
            "currency": "kgs",
        },
    ],
}


def test_parses_all_records():
    records = aviasales.parse_prices_for_dates(PAYLOAD, market="kg")
    assert len(records) == 2


def test_maps_fields_onto_price_record():
    record = aviasales.parse_prices_for_dates(PAYLOAD, market="kg")[0]
    assert record.source == "aviasales_cache"
    assert record.origin == "FRU"
    assert record.destination == "HKT"
    assert record.market == "kg"
    assert record.depart_date == "2026-10-12"
    assert record.return_date == "2026-10-24"
    assert record.price_local == 38000.0
    assert record.currency == "kgs"
    assert record.airline == "KC"
    assert record.transfers == 1
    assert record.duration_min == 1500
    assert record.duration_to_min == 760


def test_builds_market_specific_absolute_link():
    record = aviasales.parse_prices_for_dates(PAYLOAD, market="kg")[0]
    assert record.search_url == "https://www.aviasales.kg/search/FRU1210HKT2410?t=abc"
    ru_record = aviasales.parse_prices_for_dates(PAYLOAD, market="ru")[0]
    assert ru_record.search_url.startswith("https://www.aviasales.ru/search/")


def test_unknown_market_falls_back_to_com_domain():
    record = aviasales.parse_prices_for_dates(PAYLOAD, market="tj")[0]
    assert record.search_url.startswith("https://www.aviasales.com/search/")


def test_missing_return_at_gives_one_way_record():
    record = aviasales.parse_prices_for_dates(PAYLOAD, market="kg")[1]
    assert record.return_date is None
    assert record.nights is None


def test_expires_at_is_captured_when_present():
    payload = {"data": [dict(PAYLOAD["data"][0], expires_at="2026-08-03T00:00:00Z")]}
    record = aviasales.parse_prices_for_dates(payload, market="kg")[0]
    assert record.expires_at == "2026-08-03T00:00:00Z"


def test_expires_at_is_none_when_api_omits_it():
    record = aviasales.parse_prices_for_dates(PAYLOAD, market="kg")[0]
    assert record.expires_at is None


def test_empty_data_yields_empty_list():
    assert aviasales.parse_prices_for_dates({"success": True, "data": []}, market="kg") == []


def test_missing_data_key_yields_empty_list():
    assert aviasales.parse_prices_for_dates({"success": False}, market="kg") == []


def test_malformed_entry_is_skipped_not_fatal():
    payload = {"data": [{"origin": "FRU"}, PAYLOAD["data"][0]]}
    records = aviasales.parse_prices_for_dates(payload, market="kg")
    assert len(records) == 1


def test_currency_falls_back_to_argument_when_absent():
    payload = {"data": [{k: v for k, v in PAYLOAD["data"][0].items() if k != "currency"}]}
    records = aviasales.parse_prices_for_dates(payload, market="kg", currency="kgs")
    assert records[0].currency == "kgs"
```

- [ ] **Step 2: Запустить тест, убедиться в падении**

Run: `.venv/bin/pytest tests/sources/test_aviasales_parse.py -v`
Ожидается: FAIL, `ModuleNotFoundError: No module named 'src.sources.aviasales'`

- [ ] **Step 3: Реализовать парсер в `src/sources/aviasales.py`**

```python
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
```

- [ ] **Step 4: Запустить тесты**

Run: `.venv/bin/pytest tests/sources/test_aviasales_parse.py -v`
Ожидается: 11 passed

- [ ] **Step 5: Коммит**

```bash
git add src/sources/aviasales.py tests/sources/test_aviasales_parse.py
git commit -m "feat: parse aviasales prices_for_dates payload"
```

---

### Task 8: Фильтры записей (коридор ночей, месяц, длина пересадки)

**Files:**
- Modify: `src/sources/aviasales.py` (дописать в конец)
- Test: `tests/sources/test_aviasales_filters.py`

- [ ] **Step 1: Написать падающий тест**

```python
# tests/sources/test_aviasales_filters.py
from src.models import PriceRecord
from src.sources import aviasales


def make(depart="2026-10-12", ret="2026-10-24", duration_to=760, transfers=1, price=38000.0):
    return PriceRecord(
        source="aviasales_cache",
        origin="FRU",
        destination="HKT",
        market="kg",
        depart_date=depart,
        return_date=ret,
        price_local=price,
        currency="kgs",
        airline="KC",
        transfers=transfers,
        duration_min=(duration_to or 0) * 2,
        duration_to_min=duration_to,
    )


def test_keeps_records_inside_nights_range():
    kept = aviasales.filter_by_nights([make(ret="2026-10-24")], (10, 16))
    assert len(kept) == 1


def test_drops_too_short_trips():
    assert aviasales.filter_by_nights([make(ret="2026-10-15")], (10, 16)) == []


def test_drops_too_long_trips():
    assert aviasales.filter_by_nights([make(ret="2026-11-05")], (10, 16)) == []


def test_boundaries_are_inclusive():
    assert len(aviasales.filter_by_nights([make(ret="2026-10-22")], (10, 16))) == 1
    assert len(aviasales.filter_by_nights([make(ret="2026-10-28")], (10, 16))) == 1


def test_one_way_records_survive_nights_filter():
    assert len(aviasales.filter_by_nights([make(ret=None)], (10, 16))) == 1


def test_departure_month_filter_keeps_only_target_month():
    records = [make(depart="2026-10-12"), make(depart="2026-09-30"), make(depart="2026-11-01")]
    kept = aviasales.filter_by_month(records, "2026-10")
    assert [r.depart_date for r in kept] == ["2026-10-12"]


def test_transfer_filter_drops_legs_longer_than_shortest_plus_budget():
    records = [
        make(duration_to=600, transfers=0),  # эталон
        make(duration_to=900, transfers=1),  # +5 ч — оставляем
        make(duration_to=1400, transfers=2),  # +13.3 ч — режем при бюджете 12 ч
    ]
    kept = aviasales.filter_by_transfer_time(records, max_transfer_hours=12)
    assert [r.duration_to_min for r in kept] == [600, 900]


def test_transfer_filter_keeps_records_without_duration_to():
    records = [make(duration_to=None), make(duration_to=600)]
    kept = aviasales.filter_by_transfer_time(records, max_transfer_hours=12)
    assert len(kept) == 2


def test_transfer_filter_is_per_route():
    records = [
        make(duration_to=600),
        PriceRecord(
            source="aviasales_cache",
            origin="ALA",
            destination="DPS",
            market="kg",
            depart_date="2026-10-12",
            return_date="2026-10-24",
            price_local=50000.0,
            currency="kgs",
            airline="KC",
            transfers=1,
            duration_min=2000,
            duration_to_min=1300,
        ),
    ]
    kept = aviasales.filter_by_transfer_time(records, max_transfer_hours=12)
    assert len(kept) == 2  # 1300 — единственный на своём маршруте, значит эталон


def test_filter_records_applies_all_three(config_stub):
    records = [
        make(depart="2026-10-12", ret="2026-10-24", duration_to=600),  # ок
        make(depart="2026-09-12", ret="2026-09-24", duration_to=600),  # не тот месяц
        make(depart="2026-10-12", ret="2026-10-14", duration_to=600),  # мало ночей
        make(depart="2026-10-12", ret="2026-10-24", duration_to=1400),  # длинная пересадка
    ]
    kept = aviasales.filter_records(records, config_stub)
    assert len(kept) == 1
    assert kept[0].duration_to_min == 600
```

Добавить фикстуру в `tests/conftest.py`:

```python
# tests/conftest.py
import pytest

from src.config import Config


@pytest.fixture()
def config_stub() -> Config:
    return Config(
        origins=["FRU", "ALA"],
        destinations=["HKT", "DPS"],
        markets=["kg", "ru"],
        market_currency={"kg": "kgs", "ru": "rub"},
        cross_market_delta=0.05,
        fx_markup={"default": 0.025, "usd": 0.0},
        departure_month="2026-10",
        return_months=["2026-10", "2026-11"],
        trip_type="round_trip",
        nights_range=(10, 16),
        report_currency="usd",
        max_transfer_hours=12,
        abs_threshold_usd=250,
        anomaly_percentile=10,
        yellow_delta=0.15,
        baseline_window_days=30,
        cache_ttl_hours=48,
        digest_time="09:00",
        timezone="Asia/Bishkek",
    )
```

- [ ] **Step 2: Запустить тест, убедиться в падении**

Run: `.venv/bin/pytest tests/sources/test_aviasales_filters.py -v`
Ожидается: FAIL, `AttributeError: module 'src.sources.aviasales' has no attribute 'filter_by_nights'`

- [ ] **Step 3: Дописать фильтры в `src/sources/aviasales.py`**

```python
# --- дописать в конец src/sources/aviasales.py ---

from collections import defaultdict  # noqa: E402
from typing import Sequence  # noqa: E402


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
```

- [ ] **Step 4: Запустить тесты**

Run: `.venv/bin/pytest tests/sources/test_aviasales_filters.py -v`
Ожидается: 10 passed

- [ ] **Step 5: Коммит**

```bash
git add src/sources/aviasales.py tests/sources/test_aviasales_filters.py tests/conftest.py
git commit -m "feat: nights, month and transfer-time filters for cache records"
```

---

### Task 9: HTTP-слой Aviasales и полный скан

**Files:**
- Modify: `src/sources/aviasales.py` (дописать в конец)
- Test: `tests/sources/test_aviasales_http.py`

- [ ] **Step 1: Написать падающий тест**

```python
# tests/sources/test_aviasales_http.py
import pytest
import requests
import responses

from src.sources import aviasales

URL = f"{aviasales.BASE_URL}/prices_for_dates"

PAYLOAD = {
    "success": True,
    "data": [
        {
            "origin": "FRU",
            "destination": "HKT",
            "price": 38000,
            "airline": "KC",
            "departure_at": "2026-10-12T10:15:00+06:00",
            "return_at": "2026-10-24T20:00:00+07:00",
            "transfers": 1,
            "duration": 1500,
            "duration_to": 760,
            "link": "/search/x",
            "currency": "kgs",
        }
    ],
}


@pytest.fixture()
def session():
    return requests.Session()


@responses.activate
def test_fetch_sends_expected_query_params(session):
    responses.add(responses.GET, URL, json=PAYLOAD, status=200)
    aviasales.fetch_prices_for_dates(
        session,
        token="TOKEN",
        origin="FRU",
        destination="HKT",
        market="kg",
        currency="kgs",
        departure_at="2026-10",
        return_at="2026-10",
    )
    request = responses.calls[0].request
    assert "origin=FRU" in request.url
    assert "destination=HKT" in request.url
    assert "departure_at=2026-10" in request.url
    assert "return_at=2026-10" in request.url
    assert "currency=kgs" in request.url
    assert "market=kg" in request.url
    assert "one_way=false" in request.url
    assert "limit=100" in request.url
    assert "token=TOKEN" in request.url


@responses.activate
def test_fetch_returns_parsed_records(session):
    responses.add(responses.GET, URL, json=PAYLOAD, status=200)
    records = aviasales.fetch_prices_for_dates(
        session, "TOKEN", "FRU", "HKT", "kg", "kgs", "2026-10", "2026-10"
    )
    assert len(records) == 1
    assert records[0].market == "kg"


@responses.activate
def test_fetch_raises_on_http_error(session):
    responses.add(responses.GET, URL, json={}, status=429)
    with pytest.raises(aviasales.AviasalesError):
        aviasales.fetch_prices_for_dates(
            session, "TOKEN", "FRU", "HKT", "kg", "kgs", "2026-10", "2026-10"
        )


@responses.activate
def test_fetch_raises_when_api_reports_failure(session):
    responses.add(
        responses.GET, URL, json={"success": False, "error": "bad token"}, status=200
    )
    with pytest.raises(aviasales.AviasalesError, match="bad token"):
        aviasales.fetch_prices_for_dates(
            session, "TOKEN", "FRU", "HKT", "kg", "kgs", "2026-10", "2026-10"
        )


@responses.activate
def test_scan_all_covers_routes_markets_and_return_months(session, config_stub):
    responses.add(responses.GET, URL, json=PAYLOAD, status=200)
    records, errors = aviasales.scan_all(session, "TOKEN", config_stub)
    # 4 маршрута × 2 рынка × 2 месяца возврата
    assert len(responses.calls) == 16
    assert errors == []
    assert len(records) > 0


@responses.activate
def test_scan_all_survives_single_route_failure(session, config_stub):
    responses.add(responses.GET, URL, json={"success": False, "error": "boom"}, status=200)
    records, errors = aviasales.scan_all(session, "TOKEN", config_stub)
    assert records == []
    assert len(errors) == 16


@responses.activate
def test_scan_all_deduplicates_identical_offers_within_run(session, config_stub):
    responses.add(responses.GET, URL, json=PAYLOAD, status=200)
    records, _ = aviasales.scan_all(session, "TOKEN", config_stub)
    keys = {
        (r.market, r.origin, r.destination, r.depart_date, r.return_date, r.airline, r.price_local)
        for r in records
    }
    assert len(keys) == len(records)
```

- [ ] **Step 2: Запустить тест, убедиться в падении**

Run: `.venv/bin/pytest tests/sources/test_aviasales_http.py -v`
Ожидается: FAIL, `AttributeError: module 'src.sources.aviasales' has no attribute 'fetch_prices_for_dates'`

- [ ] **Step 3: Дописать HTTP-слой в `src/sources/aviasales.py`**

```python
# --- дописать в конец src/sources/aviasales.py ---

import logging  # noqa: E402

import requests  # noqa: E402

LOG = logging.getLogger(__name__)
TIMEOUT = 30
DEFAULT_LIMIT = 100


class AviasalesError(RuntimeError):
    """Ошибка обращения к Data API."""


def _get(session: requests.Session, path: str, params: dict) -> dict:
    try:
        response = session.get(f"{BASE_URL}/{path}", params=params, timeout=TIMEOUT)
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
        "token": token,
    }
    if return_at and not one_way:
        params["return_at"] = return_at
    payload = _get(session, "prices_for_dates", params)
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
```

- [ ] **Step 4: Запустить тесты**

Run: `.venv/bin/pytest tests/sources/test_aviasales_http.py -v`
Ожидается: 7 passed

- [ ] **Step 5: Коммит**

```bash
git add src/sources/aviasales.py tests/sources/test_aviasales_http.py
git commit -m "feat: aviasales http client and full route scan"
```

---

### Task 10: Бэкфилл месячной матрицы через grouped_prices

**Files:**
- Modify: `src/sources/aviasales.py` (дописать в конец)
- Test: `tests/sources/test_aviasales_backfill.py`

- [ ] **Step 1: Написать падающий тест**

```python
# tests/sources/test_aviasales_backfill.py
import pytest
import requests
import responses

from src.sources import aviasales

URL = f"{aviasales.BASE_URL}/grouped_prices"

GROUPED = {
    "success": True,
    "data": {
        "2026-10-05": {
            "origin": "FRU",
            "destination": "HKT",
            "price": 36000,
            "airline": "KC",
            "departure_at": "2026-10-05T10:00:00+06:00",
            "return_at": "2026-10-17T20:00:00+07:00",
            "transfers": 1,
            "duration": 1490,
            "duration_to": 750,
            "link": "/search/a",
        },
        "2026-10-12": {
            "origin": "FRU",
            "destination": "HKT",
            "price": 38000,
            "airline": "TK",
            "departure_at": "2026-10-12T10:00:00+06:00",
            "return_at": "2026-10-24T20:00:00+07:00",
            "transfers": 1,
            "duration": 1500,
            "duration_to": 760,
            "link": "/search/b",
        },
    },
}


@pytest.fixture()
def session():
    return requests.Session()


def test_parse_grouped_prices_flattens_dict_into_records():
    records = aviasales.parse_grouped_prices(GROUPED, market="kg", currency="kgs")
    assert len(records) == 2
    assert {r.depart_date for r in records} == {"2026-10-05", "2026-10-12"}
    assert records[0].currency == "kgs"
    assert records[0].source == "aviasales_cache"


def test_parse_grouped_prices_handles_empty_payload():
    assert aviasales.parse_grouped_prices({"data": {}}, market="kg", currency="kgs") == []


def test_parse_grouped_prices_skips_malformed_entries():
    payload = {"data": {"2026-10-05": {"origin": "FRU"}, "2026-10-12": GROUPED["data"]["2026-10-12"]}}
    assert len(aviasales.parse_grouped_prices(payload, market="kg", currency="kgs")) == 1


@responses.activate
def test_fetch_grouped_prices_sends_group_by(session):
    responses.add(responses.GET, URL, json=GROUPED, status=200)
    aviasales.fetch_grouped_prices(
        session, "TOKEN", "FRU", "HKT", "kg", "kgs", "2026-10"
    )
    assert "group_by=departure_at" in responses.calls[0].request.url
    assert "departure_at=2026-10" in responses.calls[0].request.url


@responses.activate
def test_backfill_covers_routes_and_markets(session, config_stub):
    responses.add(responses.GET, URL, json=GROUPED, status=200)
    records, errors = aviasales.backfill(session, "TOKEN", config_stub)
    assert len(responses.calls) == 8  # 4 маршрута × 2 рынка
    assert errors == []
    assert len(records) > 0


@responses.activate
def test_backfill_survives_errors(session, config_stub):
    responses.add(responses.GET, URL, json={"success": False, "error": "nope"}, status=200)
    records, errors = aviasales.backfill(session, "TOKEN", config_stub)
    assert records == []
    assert len(errors) == 8
```

- [ ] **Step 2: Запустить тест, убедиться в падении**

Run: `.venv/bin/pytest tests/sources/test_aviasales_backfill.py -v`
Ожидается: FAIL, `AttributeError: module 'src.sources.aviasales' has no attribute 'parse_grouped_prices'`

- [ ] **Step 3: Дописать бэкфилл в `src/sources/aviasales.py`**

```python
# --- дописать в конец src/sources/aviasales.py ---


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
        "token": token,
    }
    payload = _get(session, "grouped_prices", params)
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
```

- [ ] **Step 4: Запустить тесты**

Run: `.venv/bin/pytest tests/sources/test_aviasales_backfill.py -v`
Ожидается: 6 passed

- [ ] **Step 5: Коммит**

```bash
git add src/sources/aviasales.py tests/sources/test_aviasales_backfill.py
git commit -m "feat: monthly matrix backfill via grouped_prices"
```

---

### Task 11: Перцентили и baseline

**Files:**
- Create: `src/rules.py`
- Test: `tests/test_rules_baseline.py`

- [ ] **Step 1: Написать падающий тест**

```python
# tests/test_rules_baseline.py
import pytest

from src import rules


def test_percentile_median_of_odd_list():
    assert rules.percentile([1, 2, 3, 4, 5], 50) == 3.0


def test_percentile_interpolates():
    assert rules.percentile([1, 2, 3, 4], 10) == pytest.approx(1.3)


def test_percentile_edges():
    assert rules.percentile([10, 20, 30], 0) == 10.0
    assert rules.percentile([10, 20, 30], 100) == 30.0


def test_percentile_single_value():
    assert rules.percentile([42], 10) == 42.0


def test_percentile_unsorted_input():
    assert rules.percentile([5, 1, 3], 50) == 3.0


def test_percentile_empty_raises():
    with pytest.raises(ValueError):
        rules.percentile([], 50)


def test_compute_baseline_returns_stats():
    values = [100, 110, 120, 130, 140, 150, 160, 170, 180, 190]
    baseline = rules.compute_baseline(values, anomaly_percentile=10)
    assert baseline.n == 10
    assert baseline.median == pytest.approx(145.0)
    assert baseline.minimum == 100.0
    assert baseline.anomaly_threshold == pytest.approx(109.0)


def test_compute_baseline_returns_none_below_minimum_sample():
    assert rules.compute_baseline([100, 110], anomaly_percentile=10) is None


def test_compute_baseline_honors_custom_min_sample():
    baseline = rules.compute_baseline([100, 110], anomaly_percentile=10, min_sample=2)
    assert baseline is not None
    assert baseline.n == 2


def test_compute_baseline_of_empty_is_none():
    assert rules.compute_baseline([], anomaly_percentile=10) is None
```

- [ ] **Step 2: Запустить тест, убедиться в падении**

Run: `.venv/bin/pytest tests/test_rules_baseline.py -v`
Ожидается: FAIL, `ModuleNotFoundError: No module named 'src.rules'`

- [ ] **Step 3: Реализовать `src/rules.py`**

```python
"""Статистика и светофор.

Baseline строится по маршруту в целом за окно (все даты октября вместе):
по конкретной дате записей слишком мало, чтобы перцентиль что-то значил.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from statistics import median as _median
from typing import Optional, Sequence

MIN_SAMPLE = 5

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
```

- [ ] **Step 4: Запустить тесты**

Run: `.venv/bin/pytest tests/test_rules_baseline.py -v`
Ожидается: 10 passed

- [ ] **Step 5: Коммит**

```bash
git add src/rules.py tests/test_rules_baseline.py
git commit -m "feat: percentile and route baseline statistics"
```

---

### Task 12: Светофор

**Files:**
- Modify: `src/rules.py` (дописать в конец)
- Test: `tests/test_rules_classify.py`

- [ ] **Step 1: Написать падающий тест**

```python
# tests/test_rules_classify.py
import pytest

from src import rules


@pytest.fixture()
def baseline():
    # median 300, p10 = 240
    return rules.Baseline(n=20, median=300.0, minimum=230.0, anomaly_threshold=240.0)


def test_below_absolute_threshold_is_green(baseline, config_stub):
    assert rules.classify(249.0, baseline, config_stub) == rules.GREEN


def test_below_anomaly_percentile_is_green(baseline, config_stub):
    # 239 < p10=240, при этом выше abs_threshold=250? нет — берём случай выше порога
    high_threshold_cfg = config_stub.with_overrides({"abs_threshold_usd": "100"})
    assert rules.classify(239.0, baseline, high_threshold_cfg) == rules.GREEN


def test_below_median_minus_delta_is_yellow(baseline, config_stub):
    cfg = config_stub.with_overrides({"abs_threshold_usd": "100"})
    # median*0.85 = 255; цена 250 — жёлтая, но выше p10=240
    assert rules.classify(250.0, baseline, cfg) == rules.YELLOW


def test_ordinary_price_is_gray(baseline, config_stub):
    cfg = config_stub.with_overrides({"abs_threshold_usd": "100"})
    assert rules.classify(290.0, baseline, cfg) == rules.GRAY


def test_exactly_at_yellow_boundary_is_gray(baseline, config_stub):
    cfg = config_stub.with_overrides({"abs_threshold_usd": "100"})
    assert rules.classify(255.0, baseline, cfg) == rules.GRAY


def test_without_baseline_only_absolute_threshold_applies(config_stub):
    assert rules.classify(240.0, None, config_stub) == rules.GREEN
    assert rules.classify(400.0, None, config_stub) == rules.GRAY


def test_delta_to_median_is_negative_when_cheaper(baseline):
    assert rules.delta_to_median(255.0, baseline) == pytest.approx(-0.15)


def test_delta_to_median_is_positive_when_pricier(baseline):
    assert rules.delta_to_median(330.0, baseline) == pytest.approx(0.10)


def test_delta_to_median_without_baseline_is_none():
    assert rules.delta_to_median(300.0, None) is None


def test_level_emoji_mapping():
    assert rules.level_emoji(rules.GREEN) == "🟢"
    assert rules.level_emoji(rules.YELLOW) == "🟡"
    assert rules.level_emoji(rules.GRAY) == "⚪"
```

- [ ] **Step 2: Запустить тест, убедиться в падении**

Run: `.venv/bin/pytest tests/test_rules_classify.py -v`
Ожидается: FAIL, `AttributeError: module 'src.rules' has no attribute 'classify'`

- [ ] **Step 3: Дописать светофор в `src/rules.py`**

```python
# --- дописать в конец src/rules.py ---

EMOJI = {GREEN: "🟢", YELLOW: "🟡", GRAY: "⚪"}


def classify(landed_usd: float, baseline: Optional[Baseline], cfg) -> str:
    """GREEN — покупать, YELLOW — упомянуть в дайджесте, GRAY — фон.

    В v1 GREEN ставится по кэшу; в v1.1 BUY-алерт требует подтверждения Amadeus.
    """
    if landed_usd < cfg.abs_threshold_usd:
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
```

- [ ] **Step 4: Запустить тесты**

Run: `.venv/bin/pytest tests/test_rules_classify.py -v`
Ожидается: 10 passed

- [ ] **Step 5: Коммит**

```bash
git add src/rules.py tests/test_rules_classify.py
git commit -m "feat: traffic-light classification and median delta"
```

---

### Task 13: Справочник авиакомпаний

**Files:**
- Create: `src/airlines.py`
- Test: `tests/test_airlines.py`

- [ ] **Step 1: Написать падающий тест**

```python
# tests/test_airlines.py
from src import airlines


def test_known_code_resolves_to_name():
    assert airlines.name("KC") == "Air Astana"


def test_lookup_is_case_insensitive():
    assert airlines.name("kc") == "Air Astana"


def test_unknown_code_returns_code_itself():
    assert airlines.name("ZZ") == "ZZ"


def test_empty_code_returns_placeholder():
    assert airlines.name("") == "—"


def test_none_returns_placeholder():
    assert airlines.name(None) == "—"
```

- [ ] **Step 2: Запустить тест, убедиться в падении**

Run: `.venv/bin/pytest tests/test_airlines.py -v`
Ожидается: FAIL, `ModuleNotFoundError: No module named 'src.airlines'`

- [ ] **Step 3: Реализовать `src/airlines.py`**

```python
"""IATA-коды авиакомпаний, реально встречающихся на FRU/ALA → HKT/DPS.

Полный справочник Travelpayouts тянуть не стали: в дайджест попадают
единицы перевозчиков, а неизвестный код читается как код и это нормально.
"""

from __future__ import annotations

from typing import Optional

NAMES = {
    "KC": "Air Astana",
    "KC ": "Air Astana",
    "QH": "Jazeera",
    "HY": "Uzbekistan Airways",
    "TK": "Turkish Airlines",
    "FZ": "flydubai",
    "EK": "Emirates",
    "QR": "Qatar Airways",
    "SU": "Aeroflot",
    "S7": "S7 Airlines",
    "U6": "Ural Airlines",
    "PC": "Pegasus",
    "GA": "Garuda",
    "SQ": "Singapore Airlines",
    "MH": "Malaysia Airlines",
    "AK": "AirAsia",
    "D7": "AirAsia X",
    "TG": "Thai Airways",
    "FD": "Thai AirAsia",
    "CZ": "China Southern",
    "CA": "Air China",
    "MU": "China Eastern",
    "HU": "Hainan Airlines",
    "3U": "Sichuan Airlines",
    "KE": "Korean Air",
    "OZ": "Asiana",
    "VN": "Vietnam Airlines",
    "VJ": "VietJet",
    "6E": "IndiGo",
    "AI": "Air India",
    "UK": "Vistara",
    "ZF": "Azur Air",
    "N4": "Nordwind",
    "7R": "RusLine",
    "IO": "IrAero",
    "KGA": "Asman Airlines",
    "QN": "Air Armenia",
    "J2": "AZAL",
    "GF": "Gulf Air",
    "WY": "Oman Air",
    "SV": "Saudia",
    "ET": "Ethiopian",
    "PS": "Ukraine Intl",
    "LO": "LOT",
    "OS": "Austrian",
    "LH": "Lufthansa",
    "AF": "Air France",
    "KL": "KLM",
    "BA": "British Airways",
    "CX": "Cathay Pacific",
    "PG": "Bangkok Airways",
    "ID": "Batik Air",
    "QZ": "Indonesia AirAsia",
    "JT": "Lion Air",
    "SL": "Thai Lion Air",
    "XJ": "Thai AirAsia X",
}

PLACEHOLDER = "—"


def name(code: Optional[str]) -> str:
    if not code:
        return PLACEHOLDER
    return NAMES.get(code.strip().upper(), code.strip().upper())
```

- [ ] **Step 4: Запустить тесты**

Run: `.venv/bin/pytest tests/test_airlines.py -v`
Ожидается: 5 passed

- [ ] **Step 5: Коммит**

```bash
git add src/airlines.py tests/test_airlines.py
git commit -m "feat: airline code to name lookup"
```

---

### Task 14: Сборка и рендер дайджеста

**Files:**
- Create: `src/digest.py`
- Test: `tests/test_digest.py`

- [ ] **Step 1: Написать падающий тест**

```python
# tests/test_digest.py
import pytest

from src import digest, rules, store
from src.models import PriceRecord


@pytest.fixture()
def conn(tmp_path):
    connection = store.connect(tmp_path / "history.sqlite")
    store.init_schema(connection)
    return connection


def record(landed, depart="2026-10-12", market="kg", airline="KC", destination="HKT"):
    return PriceRecord(
        source="aviasales_cache",
        origin="FRU",
        destination=destination,
        market=market,
        depart_date=depart,
        return_date="2026-10-24",
        price_local=landed * 87.5,
        currency="kgs",
        airline=airline,
        transfers=1,
        duration_min=1500,
        duration_to_min=760,
        search_url="https://www.aviasales.kg/search/x",
        fx_rate=1 / 87.5,
        landed_usd=landed,
    )


def seed(conn, landed_values, now="2026-08-01T06:00:00Z", **kwargs):
    store.insert_prices(conn, [record(v, **kwargs) for v in landed_values], now=now)


def test_summary_picks_cheapest_fresh_offer(conn, config_stub):
    seed(conn, [400.0, 312.0, 355.0])
    summary = digest.build_route_summary(
        conn, config_stub, "FRU", "HKT", now="2026-08-01T07:00:00Z"
    )
    assert summary.best_landed == 312.0
    assert summary.airline == "KC"
    assert summary.transfers == 1


def test_summary_ignores_stale_rows(conn, config_stub):
    seed(conn, [312.0], now="2026-07-20T06:00:00Z")  # старше 48 ч
    seed(conn, [400.0], now="2026-08-01T06:00:00Z")
    summary = digest.build_route_summary(
        conn, config_stub, "FRU", "HKT", now="2026-08-01T07:00:00Z"
    )
    assert summary.best_landed == 400.0


def test_summary_with_no_data_is_empty(conn, config_stub):
    summary = digest.build_route_summary(
        conn, config_stub, "ALA", "DPS", now="2026-08-01T07:00:00Z"
    )
    assert summary.best_landed is None
    assert summary.level == rules.GRAY


def test_summary_baseline_uses_full_window_not_just_fresh(conn, config_stub):
    seed(conn, [500.0, 510.0, 520.0, 530.0, 540.0], now="2026-07-10T06:00:00Z")
    seed(conn, [400.0], now="2026-08-01T06:00:00Z")
    summary = digest.build_route_summary(
        conn, config_stub, "FRU", "HKT", now="2026-08-01T07:00:00Z"
    )
    assert summary.baseline is not None
    assert summary.baseline.n == 6
    assert summary.delta_pct is not None and summary.delta_pct < 0


def test_render_matches_expected_shape(config_stub):
    summaries = [
        digest.RouteSummary(
            origin="FRU",
            destination="HKT",
            best_landed=312.0,
            depart_date="2026-10-12",
            return_date="2026-10-24",
            airline="KC",
            transfers=1,
            market="kg",
            search_url="https://www.aviasales.kg/search/x",
            baseline=rules.Baseline(n=30, median=325.0, minimum=300.0, anomaly_threshold=305.0),
            level=rules.YELLOW,
            delta_pct=-0.04,
        ),
        digest.RouteSummary(
            origin="FRU",
            destination="DPS",
            best_landed=None,
            depart_date=None,
            return_date=None,
            airline=None,
            transfers=None,
            market=None,
            search_url=None,
            baseline=None,
            level=rules.GRAY,
            delta_pct=None,
        ),
    ]
    text = digest.render_digest(summaries, config_stub, now="2026-08-01T03:00:00Z")
    assert "октябрь 2026" in text
    assert "FRU→HKT" in text
    assert "$312" in text
    assert "−4%" in text
    assert "🟡" in text
    assert "Air Astana" in text
    assert "12.10" in text
    assert "нет данных" in text


def test_render_escapes_html_special_chars(config_stub):
    summaries = [
        digest.RouteSummary(
            origin="FRU",
            destination="HKT",
            best_landed=312.0,
            depart_date="2026-10-12",
            return_date="2026-10-24",
            airline="A&B",
            transfers=0,
            market="kg",
            search_url="https://x/?a=1&b=2",
            baseline=None,
            level=rules.GRAY,
            delta_pct=None,
        )
    ]
    text = digest.render_digest(summaries, config_stub, now="2026-08-01T03:00:00Z")
    assert "A&amp;B" in text


def test_render_shows_direct_flight_wording(config_stub):
    summaries = [
        digest.RouteSummary(
            origin="ALA",
            destination="HKT",
            best_landed=268.0,
            depart_date="2026-10-08",
            return_date="2026-10-20",
            airline="KC",
            transfers=0,
            market="kg",
            search_url="https://x",
            baseline=rules.Baseline(n=30, median=327.0, minimum=250.0, anomaly_threshold=270.0),
            level=rules.GREEN,
            delta_pct=-0.18,
        )
    ]
    text = digest.render_digest(summaries, config_stub, now="2026-08-01T03:00:00Z")
    assert "без пересадок" in text
    assert "−18%" in text


def test_build_all_summaries_covers_every_route(conn, config_stub):
    summaries = digest.build_all_summaries(conn, config_stub, now="2026-08-01T07:00:00Z")
    assert [(s.origin, s.destination) for s in summaries] == config_stub.routes()
```

- [ ] **Step 2: Запустить тест, убедиться в падении**

Run: `.venv/bin/pytest tests/test_digest.py -v`
Ожидается: FAIL, `ModuleNotFoundError: No module named 'src.digest'`

- [ ] **Step 3: Реализовать `src/digest.py`**

```python
"""Сборка и рендер ежедневного дайджеста.

Все цены — landed USD: только так рынки сравнимы между собой.
Baseline считается по всему окну (включая протухшие записи — это история),
а «лучшая цена сегодня» — только по свежим.
"""

from __future__ import annotations

import html
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Sequence
from zoneinfo import ZoneInfo

from src import airlines, rules, store

MONTHS_RU = {
    1: "январь",
    2: "февраль",
    3: "март",
    4: "апрель",
    5: "май",
    6: "июнь",
    7: "июль",
    8: "август",
    9: "сентябрь",
    10: "октябрь",
    11: "ноябрь",
    12: "декабрь",
}


@dataclass(frozen=True)
class RouteSummary:
    origin: str
    destination: str
    best_landed: Optional[float]
    depart_date: Optional[str]
    return_date: Optional[str]
    airline: Optional[str]
    transfers: Optional[int]
    market: Optional[str]
    search_url: Optional[str]
    baseline: Optional[rules.Baseline]
    level: str
    delta_pct: Optional[float]


def build_route_summary(
    conn: sqlite3.Connection, cfg, origin: str, destination: str, now: str
) -> RouteSummary:
    window_start = store.shift_days(now, -cfg.baseline_window_days)
    history = store.recent_prices(conn, origin, destination, since=window_start)
    baseline = rules.compute_baseline(
        [row["landed_usd"] for row in history if row["landed_usd"] is not None],
        cfg.anomaly_percentile,
    )

    fresh = [
        row
        for row in store.fresh_prices(
            conn, origin, destination, now=now, ttl_hours=cfg.cache_ttl_hours
        )
        if row["landed_usd"] is not None
    ]
    if not fresh:
        return RouteSummary(
            origin=origin,
            destination=destination,
            best_landed=None,
            depart_date=None,
            return_date=None,
            airline=None,
            transfers=None,
            market=None,
            search_url=None,
            baseline=baseline,
            level=rules.GRAY,
            delta_pct=None,
        )

    best = min(fresh, key=lambda row: row["landed_usd"])
    return RouteSummary(
        origin=origin,
        destination=destination,
        best_landed=float(best["landed_usd"]),
        depart_date=best["depart_date"],
        return_date=best["return_date"],
        airline=best["airline"],
        transfers=best["transfers"],
        market=best["market"],
        search_url=best["search_url"],
        baseline=baseline,
        level=rules.classify(float(best["landed_usd"]), baseline, cfg),
        delta_pct=rules.delta_to_median(float(best["landed_usd"]), baseline),
    )


def build_all_summaries(conn: sqlite3.Connection, cfg, now: str) -> list[RouteSummary]:
    return [
        build_route_summary(conn, cfg, origin, destination, now)
        for origin, destination in cfg.routes()
    ]


def format_delta(delta_pct: Optional[float]) -> str:
    if delta_pct is None:
        return ""
    percent = round(delta_pct * 100)
    if percent == 0:
        return "(±0%)"
    sign = "−" if percent < 0 else "+"
    return f"({sign}{abs(percent)}%)"


def format_day(date_iso: Optional[str]) -> str:
    if not date_iso:
        return ""
    return f"{date_iso[8:10]}.{date_iso[5:7]}"


def format_transfers(transfers: Optional[int]) -> str:
    if transfers is None:
        return ""
    if transfers == 0:
        return "без пересадок"
    if transfers == 1:
        return "1 пересадка"
    return f"{transfers} пересадки"


def _local_date(now: str, tz: str) -> datetime:
    moment = datetime.fromisoformat(now.replace("Z", "+00:00"))
    return moment.astimezone(ZoneInfo(tz))


def render_digest(summaries: Sequence[RouteSummary], cfg, now: str) -> str:
    local = _local_date(now, cfg.timezone)
    year, month = cfg.departure_month.split("-")
    header = (
        f"✈️ <b>{local.strftime('%d.%m')}</b> · {MONTHS_RU[int(month)]} {year}"
    )
    lines = [header, ""]

    for summary in summaries:
        route = f"{summary.origin}→{summary.destination}"
        if summary.best_landed is None:
            lines.append(f"{route}  нет данных ⚪")
            continue
        emoji = rules.level_emoji(summary.level)
        price = f"${summary.best_landed:.0f}"
        delta = format_delta(summary.delta_pct)
        detail = ", ".join(
            part
            for part in (
                format_day(summary.depart_date),
                html.escape(airlines.name(summary.airline)),
                format_transfers(summary.transfers),
            )
            if part
        )
        line = f"{route}  мин <b>{price}</b> {delta}  {emoji}"
        if detail:
            line += f"  {detail}"
        if summary.market:
            line += f"  [{summary.market}]"
        if summary.search_url:
            line += f'  <a href="{html.escape(summary.search_url, quote=True)}">поиск</a>'
        lines.append(line)

    lines.append("")
    lines.append(
        f"<i>landed USD: цена рынка × курс × (1 + надбавка). Порог BUY: "
        f"${cfg.abs_threshold_usd:.0f}</i>"
    )
    return "\n".join(lines)
```

- [ ] **Step 4: Запустить тесты**

Run: `.venv/bin/pytest tests/test_digest.py -v`
Ожидается: 9 passed

- [ ] **Step 5: Коммит**

```bash
git add src/digest.py tests/test_digest.py
git commit -m "feat: digest assembly and rendering"
```

---

### Task 15: Отправка в Telegram

**Files:**
- Create: `src/notify.py`
- Test: `tests/test_notify.py`

- [ ] **Step 1: Написать падающий тест**

```python
# tests/test_notify.py
import json

import pytest
import requests
import responses

from src import notify

URL = "https://api.telegram.org/botTOKEN/sendMessage"


@pytest.fixture()
def session():
    return requests.Session()


@responses.activate
def test_send_message_posts_expected_payload(session):
    responses.add(responses.POST, URL, json={"ok": True}, status=200)
    notify.send_message(session, "TOKEN", "12345", "привет")
    body = json.loads(responses.calls[0].request.body)
    assert body["chat_id"] == "12345"
    assert body["text"] == "привет"
    assert body["parse_mode"] == "HTML"
    assert body["disable_web_page_preview"] is True


@responses.activate
def test_send_message_raises_on_api_error(session):
    responses.add(
        responses.POST, URL, json={"ok": False, "description": "chat not found"}, status=400
    )
    with pytest.raises(notify.TelegramError, match="chat not found"):
        notify.send_message(session, "TOKEN", "12345", "привет")


@responses.activate
def test_send_message_raises_on_transport_error(session):
    responses.add(responses.POST, URL, body=requests.ConnectionError("boom"))
    with pytest.raises(notify.TelegramError):
        notify.send_message(session, "TOKEN", "12345", "привет")


@responses.activate
def test_long_text_is_split_into_several_calls(session):
    responses.add(responses.POST, URL, json={"ok": True}, status=200)
    text = "\n".join(f"строка {i}" for i in range(1200))
    notify.send_message(session, "TOKEN", "12345", text)
    assert len(responses.calls) > 1


def test_split_message_keeps_chunks_under_limit():
    text = "\n".join("x" * 100 for _ in range(200))
    chunks = notify.split_message(text, limit=1000)
    assert all(len(chunk) <= 1000 for chunk in chunks)
    assert "".join(chunks).replace("\n", "") == text.replace("\n", "")


def test_split_message_prefers_newline_boundaries():
    text = "первая\nвторая\nтретья"
    chunks = notify.split_message(text, limit=14)
    assert chunks[0] == "первая"


def test_split_message_handles_single_oversized_line():
    text = "y" * 50
    chunks = notify.split_message(text, limit=20)
    assert len(chunks) == 3
    assert all(len(chunk) <= 20 for chunk in chunks)


def test_split_message_short_text_is_one_chunk():
    assert notify.split_message("коротко", limit=100) == ["коротко"]
```

- [ ] **Step 2: Запустить тест, убедиться в падении**

Run: `.venv/bin/pytest tests/test_notify.py -v`
Ожидается: FAIL, `ModuleNotFoundError: No module named 'src.notify'`

- [ ] **Step 3: Реализовать `src/notify.py`**

```python
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
    """Режет текст по границам строк, а слишком длинную строку — по символам."""
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
        extra = len(line) + (1 if current else 0)
        if current_len + extra > limit:
            chunks.append("\n".join(current))
            current, current_len = [line], len(line)
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
```

- [ ] **Step 4: Запустить тесты**

Run: `.venv/bin/pytest tests/test_notify.py -v`
Ожидается: 8 passed

- [ ] **Step 5: Коммит**

```bash
git add src/notify.py tests/test_notify.py
git commit -m "feat: telegram transport with message splitting"
```

---

### Task 16: CLI monitor.py — scan, backfill, digest

**Files:**
- Create: `monitor.py`, `src/runner.py`
- Test: `tests/test_runner.py`

- [ ] **Step 1: Написать падающий тест**

```python
# tests/test_runner.py
import json

import pytest
import requests
import responses

from src import runner, store

PRICES_URL = "https://api.travelpayouts.com/aviasales/v3/prices_for_dates"
GROUPED_URL = "https://api.travelpayouts.com/aviasales/v3/grouped_prices"
FX_URL = "https://open.er-api.com/v6/latest/USD"
TG_URL = "https://api.telegram.org/botTG/sendMessage"

FX_PAYLOAD = {"result": "success", "rates": {"USD": 1.0, "KGS": 87.5, "RUB": 92.0}}

CACHE_PAYLOAD = {
    "success": True,
    "data": [
        {
            "origin": "FRU",
            "destination": "HKT",
            "price": 27000,
            "airline": "KC",
            "departure_at": "2026-10-12T10:15:00+06:00",
            "return_at": "2026-10-24T20:00:00+07:00",
            "transfers": 1,
            "duration": 1500,
            "duration_to": 760,
            "link": "/search/x",
            "currency": "kgs",
        }
    ],
}


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("TP_TOKEN", "TP")
    monkeypatch.setenv("TG_BOT_TOKEN", "TG")
    monkeypatch.setenv("TG_CHAT_ID", "999")
    return tmp_path


@pytest.fixture()
def db(env):
    path = env / "history.sqlite"
    conn = store.connect(path)
    store.init_schema(conn)
    conn.close()
    return path


@responses.activate
def test_run_scan_writes_rows_and_stamps_meta(db, config_stub):
    responses.add(responses.GET, FX_URL, json=FX_PAYLOAD, status=200)
    responses.add(responses.GET, PRICES_URL, json=CACHE_PAYLOAD, status=200)
    result = runner.run_scan(config_stub, db_path=db, now="2026-08-01T06:00:00Z")
    conn = store.connect(db)
    assert store.count_rows(conn) == 1
    assert store.get_meta(conn, "last_scan_at") == "2026-08-01T06:00:00Z"
    assert result["inserted"] == 1
    assert result["errors"] == []


@responses.activate
def test_run_scan_records_landed_usd(db, config_stub):
    responses.add(responses.GET, FX_URL, json=FX_PAYLOAD, status=200)
    responses.add(responses.GET, PRICES_URL, json=CACHE_PAYLOAD, status=200)
    runner.run_scan(config_stub, db_path=db, now="2026-08-01T06:00:00Z")
    conn = store.connect(db)
    row = conn.execute("SELECT landed_usd FROM price_history").fetchone()
    assert row["landed_usd"] == pytest.approx(27000 / 87.5 * 1.025, rel=1e-6)


@responses.activate
def test_run_scan_does_not_stamp_meta_when_everything_failed(db, config_stub):
    responses.add(responses.GET, FX_URL, json=FX_PAYLOAD, status=200)
    responses.add(responses.GET, PRICES_URL, json={"success": False, "error": "x"}, status=200)
    result = runner.run_scan(config_stub, db_path=db, now="2026-08-01T06:00:00Z")
    conn = store.connect(db)
    assert store.get_meta(conn, "last_scan_at") is None
    assert len(result["errors"]) == 16


@responses.activate
def test_run_backfill_only_runs_on_empty_database(db, config_stub):
    responses.add(responses.GET, FX_URL, json=FX_PAYLOAD, status=200)
    responses.add(responses.GET, GROUPED_URL, json={"success": True, "data": {}}, status=200)
    first = runner.run_backfill(config_stub, db_path=db, now="2026-08-01T06:00:00Z")
    assert first["skipped"] is False

    conn = store.connect(db)
    conn.execute(
        "INSERT INTO price_history (scanned_at, last_seen_at, source, origin, destination,"
        " market, depart_date, price_local, currency) VALUES"
        " ('2026-08-01T00:00:00Z','2026-08-01T00:00:00Z','aviasales_cache','FRU','HKT','kg',"
        " '2026-10-12', 1.0, 'kgs')"
    )
    conn.commit()
    conn.close()
    second = runner.run_backfill(config_stub, db_path=db, now="2026-08-01T07:00:00Z")
    assert second["skipped"] is True


@responses.activate
def test_run_backfill_force_ignores_existing_rows(db, config_stub):
    responses.add(responses.GET, FX_URL, json=FX_PAYLOAD, status=200)
    responses.add(responses.GET, GROUPED_URL, json={"success": True, "data": {}}, status=200)
    runner.run_backfill(config_stub, db_path=db, now="2026-08-01T06:00:00Z")
    result = runner.run_backfill(config_stub, db_path=db, now="2026-08-01T07:00:00Z", force=True)
    assert result["skipped"] is False


@responses.activate
def test_run_digest_sends_message(db, config_stub):
    responses.add(responses.GET, FX_URL, json=FX_PAYLOAD, status=200)
    responses.add(responses.GET, PRICES_URL, json=CACHE_PAYLOAD, status=200)
    responses.add(responses.POST, TG_URL, json={"ok": True}, status=200)
    runner.run_scan(config_stub, db_path=db, now="2026-08-01T06:00:00Z")
    runner.run_digest(config_stub, db_path=db, now="2026-08-01T03:00:00Z")
    sent = [c for c in responses.calls if c.request.url.startswith(TG_URL)]
    assert len(sent) == 1
    body = json.loads(sent[0].request.body)
    assert "FRU→HKT" in body["text"]
    assert body["chat_id"] == "999"


@responses.activate
def test_run_digest_applies_meta_overrides(db, config_stub):
    responses.add(responses.GET, FX_URL, json=FX_PAYLOAD, status=200)
    responses.add(responses.GET, PRICES_URL, json=CACHE_PAYLOAD, status=200)
    responses.add(responses.POST, TG_URL, json={"ok": True}, status=200)
    runner.run_scan(config_stub, db_path=db, now="2026-08-01T06:00:00Z")
    conn = store.connect(db)
    store.set_meta(conn, "abs_threshold_usd", "111")
    conn.close()
    runner.run_digest(config_stub, db_path=db, now="2026-08-01T03:00:00Z")
    body = json.loads(
        [c for c in responses.calls if c.request.url.startswith(TG_URL)][0].request.body
    )
    assert "$111" in body["text"]


def test_require_env_raises_with_helpful_message(monkeypatch):
    monkeypatch.delenv("TP_TOKEN", raising=False)
    with pytest.raises(runner.ConfigurationError, match="TP_TOKEN"):
        runner.require_env("TP_TOKEN")
```

- [ ] **Step 2: Запустить тест, убедиться в падении**

Run: `.venv/bin/pytest tests/test_runner.py -v`
Ожидается: FAIL, `ModuleNotFoundError: No module named 'src.runner'`

- [ ] **Step 3: Реализовать `src/runner.py`**

```python
"""Связывание слоёв: чтение окружения, оркестрация одного рана.

Логики здесь нет — только последовательность вызовов и работа с БД,
чтобы monitor.py оставался тонким CLI.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

import requests

from src import digest, fx, notify, store
from src.config import Config
from src.sources import aviasales

LOG = logging.getLogger(__name__)
DEFAULT_DB = Path("data/history.sqlite")
RETENTION_DAYS = 120


class ConfigurationError(RuntimeError):
    """Не хватает переменной окружения."""


def require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise ConfigurationError(
            f"переменная окружения {name} не задана — проверь GitHub Secrets"
        )
    return value


def _open(db_path: Path):
    conn = store.connect(db_path)
    store.init_schema(conn)
    return conn


def _effective_config(conn, cfg: Config) -> Config:
    return cfg.with_overrides(store.all_meta(conn))


def run_scan(
    cfg: Config, db_path: Path = DEFAULT_DB, now: Optional[str] = None, session=None
) -> dict:
    now = now or store.utcnow()
    token = require_env("TP_TOKEN")
    session = session or requests.Session()
    conn = _open(db_path)
    cfg = _effective_config(conn, cfg)

    rates = fx.fetch_usd_rates(session)
    records, errors = aviasales.scan_all(session, token, cfg)
    enriched = fx.enrich(records, rates, cfg)
    inserted = store.insert_prices(conn, enriched, now=now)

    if records:
        store.set_meta(conn, "last_scan_at", now)
    store.set_meta(conn, "last_scan_errors", str(len(errors)))
    removed = store.prune(conn, before=store.shift_days(now, -RETENTION_DAYS))
    conn.close()

    LOG.info("скан: получено %d, вставлено %d, ошибок %d", len(records), inserted, len(errors))
    return {"fetched": len(records), "inserted": inserted, "pruned": removed, "errors": errors}


def run_backfill(
    cfg: Config,
    db_path: Path = DEFAULT_DB,
    now: Optional[str] = None,
    force: bool = False,
    session=None,
) -> dict:
    now = now or store.utcnow()
    token = require_env("TP_TOKEN")
    session = session or requests.Session()
    conn = _open(db_path)
    cfg = _effective_config(conn, cfg)

    if store.count_rows(conn) > 0 and not force:
        conn.close()
        LOG.info("бэкфилл пропущен: база не пуста")
        return {"skipped": True, "inserted": 0, "errors": []}

    rates = fx.fetch_usd_rates(session)
    records, errors = aviasales.backfill(session, token, cfg)
    enriched = fx.enrich(records, rates, cfg)
    inserted = store.insert_prices(conn, enriched, now=now)
    store.set_meta(conn, "backfilled_at", now)
    conn.close()
    return {"skipped": False, "inserted": inserted, "errors": errors}


def run_digest(
    cfg: Config, db_path: Path = DEFAULT_DB, now: Optional[str] = None, session=None
) -> dict:
    now = now or store.utcnow()
    bot_token = require_env("TG_BOT_TOKEN")
    chat_id = require_env("TG_CHAT_ID")
    session = session or requests.Session()
    conn = _open(db_path)
    cfg = _effective_config(conn, cfg)

    summaries = digest.build_all_summaries(conn, cfg, now=now)
    text = digest.render_digest(summaries, cfg, now=now)
    conn.close()

    notify.send_message(session, bot_token, chat_id, text)
    return {"sent": True, "routes": len(summaries)}
```

- [ ] **Step 4: Реализовать `monitor.py`**

```python
#!/usr/bin/env python3
"""flight-sniper — точка входа.

Команды:
  scan      — обход кэша Aviasales, запись истории
  backfill  — стартовая месячная матрица (пропускается, если база не пуста)
  digest    — ежедневный дайджест в Telegram
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from src import runner
from src.config import Config

DEFAULT_CONFIG = Path("config.yaml")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="monitor.py", description="flight-sniper")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--db", type=Path, default=runner.DEFAULT_DB)
    parser.add_argument("--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("scan", help="скан кэша Aviasales")
    backfill = sub.add_parser("backfill", help="стартовая месячная матрица")
    backfill.add_argument("--force", action="store_true", help="игнорировать непустую базу")
    sub.add_parser("digest", help="дайджест в Telegram")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    cfg = Config.load(args.config)

    try:
        if args.command == "scan":
            result = runner.run_scan(cfg, db_path=args.db)
        elif args.command == "backfill":
            result = runner.run_backfill(cfg, db_path=args.db, force=args.force)
        else:
            result = runner.run_digest(cfg, db_path=args.db)
    except runner.ConfigurationError as exc:
        logging.error("%s", exc)
        return 2

    logging.info("готово: %s", result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 5: Запустить тесты**

Run: `.venv/bin/pytest tests/test_runner.py -v`
Ожидается: 8 passed

- [ ] **Step 6: Проверить CLI вручную**

Run: `.venv/bin/python monitor.py --help`
Ожидается: справка с подкомандами `scan`, `backfill`, `digest`

Run: `.venv/bin/python monitor.py scan`
Ожидается: `ERROR ... переменная окружения TP_TOKEN не задана`, код возврата 2

- [ ] **Step 7: Прогнать весь набор**

Run: `.venv/bin/pytest`
Ожидается: 102 passed

- [ ] **Step 8: Коммит**

```bash
git add monitor.py src/runner.py tests/test_runner.py
git commit -m "feat: monitor CLI with scan, backfill and digest commands"
```

---

### Task 17: Синхронизация БД с orphan-веткой data

**Files:**
- Create: `scripts/db_pull.sh`, `scripts/db_push.sh`
- Test: `tests/test_db_sync.sh` (bash-тест на временном репозитории)

- [ ] **Step 1: Написать `scripts/db_pull.sh`**

```bash
#!/usr/bin/env bash
# Достаёт data/history.sqlite из ветки data. Если ветки нет — тихо стартуем с нуля.
set -euo pipefail

DB_PATH="${DB_PATH:-data/history.sqlite}"
BRANCH="${DATA_BRANCH:-data}"

mkdir -p "$(dirname "$DB_PATH")"

if git ls-remote --exit-code --heads origin "$BRANCH" >/dev/null 2>&1; then
  git fetch --depth 1 origin "$BRANCH"
  git show "FETCH_HEAD:$(basename "$DB_PATH")" > "$DB_PATH"
  echo "db_pull: восстановлено $(wc -c < "$DB_PATH") байт из ветки $BRANCH"
else
  echo "db_pull: ветки $BRANCH ещё нет, стартуем с пустой базы"
fi
```

- [ ] **Step 2: Написать `scripts/db_push.sh`**

```bash
#!/usr/bin/env bash
# Кладёт базу в ветку data одним orphan-коммитом (без родителя).
# История ветки всегда состоит ровно из одного коммита — репозиторий не растёт.
set -euo pipefail

DB_PATH="${DB_PATH:-data/history.sqlite}"
BRANCH="${DATA_BRANCH:-data}"
MESSAGE="${1:-db update}"

if [ ! -f "$DB_PATH" ]; then
  echo "db_push: $DB_PATH не найден, нечего пушить" >&2
  exit 1
fi

git config user.name "flight-sniper[bot]"
git config user.email "flight-sniper@users.noreply.github.com"

BLOB="$(git hash-object -w "$DB_PATH")"
TMP_INDEX="$(mktemp)"
rm -f "$TMP_INDEX"

GIT_INDEX_FILE="$TMP_INDEX" git update-index --add \
  --cacheinfo "100644,$BLOB,$(basename "$DB_PATH")"
TREE="$(GIT_INDEX_FILE="$TMP_INDEX" git write-tree)"
rm -f "$TMP_INDEX"

COMMIT="$(git commit-tree "$TREE" -m "$MESSAGE")"
git push --force origin "$COMMIT:refs/heads/$BRANCH"
echo "db_push: ветка $BRANCH обновлена ($(wc -c < "$DB_PATH") байт)"
```

- [ ] **Step 3: Сделать скрипты исполняемыми**

```bash
chmod +x scripts/db_pull.sh scripts/db_push.sh
```

- [ ] **Step 4: Написать интеграционный тест на локальных репозиториях**

```bash
# tests/test_db_sync.sh
#!/usr/bin/env bash
# Проверяет цикл push → pull на паре локальных репозиториев.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

git init --bare -q "$WORK/remote.git"
git init -q "$WORK/local"
cd "$WORK/local"
git remote add origin "$WORK/remote.git"
git config user.email t@t; git config user.name t
mkdir -p data scripts
cp "$ROOT/scripts/db_pull.sh" "$ROOT/scripts/db_push.sh" scripts/
echo placeholder > README.md
git add -A && git commit -qm init && git push -q origin HEAD:main

# Ветки data ещё нет — pull должен пройти без ошибки
bash scripts/db_pull.sh > /dev/null
[ ! -s data/history.sqlite ] || { echo "FAIL: база не должна была появиться"; exit 1; }

printf 'first' > data/history.sqlite
bash scripts/db_push.sh "first" > /dev/null

rm -f data/history.sqlite
bash scripts/db_pull.sh > /dev/null
[ "$(cat data/history.sqlite)" = "first" ] || { echo "FAIL: контент не совпал"; exit 1; }

printf 'second' > data/history.sqlite
bash scripts/db_push.sh "second" > /dev/null
COUNT="$(git --git-dir="$WORK/remote.git" rev-list --count data)"
[ "$COUNT" = "1" ] || { echo "FAIL: в ветке data $COUNT коммитов, ожидался 1"; exit 1; }

rm -f data/history.sqlite
bash scripts/db_pull.sh > /dev/null
[ "$(cat data/history.sqlite)" = "second" ] || { echo "FAIL: не обновилось"; exit 1; }

echo "OK: db sync"
```

- [ ] **Step 5: Запустить тест**

Run: `chmod +x tests/test_db_sync.sh && bash tests/test_db_sync.sh`
Ожидается: `OK: db sync`

- [ ] **Step 6: Коммит**

```bash
git add scripts/db_pull.sh scripts/db_push.sh tests/test_db_sync.sh
git commit -m "feat: sqlite sync with orphan data branch"
```

---

### Task 18: GitHub Actions — scan, digest, tests, keepalive

**Files:**
- Create: `.github/workflows/scan.yml`, `.github/workflows/digest.yml`, `.github/workflows/tests.yml`, `.github/workflows/keepalive.yml`

- [ ] **Step 1: Создать `.github/workflows/scan.yml`**

```yaml
name: scan

on:
  schedule:
    - cron: "5 */4 * * *"
  workflow_dispatch:
    inputs:
      backfill:
        description: "Прогнать бэкфилл перед сканом"
        type: boolean
        default: false

permissions:
  contents: write

concurrency:
  group: flight-sniper-repo
  cancel-in-progress: false

jobs:
  scan:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
          cache: pip
      - run: pip install -r requirements.txt
      - name: Restore database
        run: bash scripts/db_pull.sh
      - name: Backfill
        if: inputs.backfill
        run: python monitor.py backfill
        env:
          TP_TOKEN: ${{ secrets.TP_TOKEN }}
      - name: Scan
        run: python monitor.py scan
        env:
          TP_TOKEN: ${{ secrets.TP_TOKEN }}
      - name: Persist database
        run: bash scripts/db_push.sh "scan $(date -u +%Y-%m-%dT%H:%M:%SZ)"
```

- [ ] **Step 2: Создать `.github/workflows/digest.yml`**

```yaml
name: digest

on:
  schedule:
    - cron: "0 3 * * *" # 09:00 Asia/Bishkek (UTC+6)
  workflow_dispatch:

permissions:
  contents: write

concurrency:
  group: flight-sniper-repo
  cancel-in-progress: false

jobs:
  digest:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
          cache: pip
      - run: pip install -r requirements.txt
      - name: Restore database
        run: bash scripts/db_pull.sh
      - name: Send digest
        run: python monitor.py digest
        env:
          TG_BOT_TOKEN: ${{ secrets.TG_BOT_TOKEN }}
          TG_CHAT_ID: ${{ secrets.TG_CHAT_ID }}
      - name: Persist database
        run: bash scripts/db_push.sh "digest $(date -u +%Y-%m-%dT%H:%M:%SZ)"
```

- [ ] **Step 3: Создать `.github/workflows/tests.yml`**

```yaml
name: tests

on:
  push:
    branches: [main]
  pull_request:
  workflow_dispatch:

jobs:
  pytest:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
          cache: pip
      - run: pip install -r requirements-dev.txt
      - run: pytest -v
      - run: bash tests/test_db_sync.sh
```

- [ ] **Step 4: Создать `.github/workflows/keepalive.yml`**

```yaml
# GitHub отключает cron-воркфлоу после 60 дней без активности в репозитории.
# Пуш в ветку data такой активностью не считается, поэтому раз в 20 дней
# делаем пустой коммит в main.
name: keepalive

on:
  schedule:
    - cron: "0 5 */20 * *"
  workflow_dispatch:

permissions:
  contents: write

concurrency:
  group: flight-sniper-repo
  cancel-in-progress: false

jobs:
  ping:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Empty commit
        run: |
          git config user.name "flight-sniper[bot]"
          git config user.email "flight-sniper@users.noreply.github.com"
          git commit --allow-empty -m "chore: keepalive $(date -u +%F)"
          git push origin HEAD:main
```

- [ ] **Step 5: Проверить синтаксис YAML локально**

```bash
.venv/bin/python - <<'PY'
import pathlib, yaml
for path in sorted(pathlib.Path(".github/workflows").glob("*.yml")):
    yaml.safe_load(path.read_text())
    print("ok", path)
PY
```

Ожидается: четыре строки `ok .github/workflows/...`

- [ ] **Step 6: Коммит**

```bash
git add .github/workflows
git commit -m "ci: scan, digest, tests and keepalive workflows"
```

---

### Task 19: README, ASSUMPTIONS и создание репозитория

**Files:**
- Create: `README.md`, `ASSUMPTIONS.md`

- [ ] **Step 1: Написать `README.md`**

````markdown
# flight-sniper

Личный монитор цен FRU/ALA → HKT/DPS на октябрь 2026. Интерфейс — только Telegram.
Стоимость эксплуатации — $0/мес.

## Как это работает

| Воркфлоу | Расписание | Что делает |
|---|---|---|
| `scan` | каждые 4 часа | 16 запросов к кэшу Aviasales, запись истории в SQLite |
| `digest` | 03:00 UTC (09:00 Бишкек) | сводка по 4 маршрутам в Telegram |
| `keepalive` | раз в 20 дней | пустой коммит, чтобы GitHub не отключил cron |
| `tests` | на push/PR | pytest |

База живёт в orphan-ветке `data` (один файл, один коммит, force-push).
`main` содержит только код.

## Секреты (Settings → Secrets and variables → Actions)

| Секрет | Где взять |
|---|---|
| `TP_TOKEN` | travelpayouts.com → Инструменты → API |
| `TG_BOT_TOKEN` | @BotFather |
| `TG_CHAT_ID` | @userinfobot |

## Локальный запуск

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
export TP_TOKEN=... TG_BOT_TOKEN=... TG_CHAT_ID=...
.venv/bin/python monitor.py backfill   # один раз, на пустой базе
.venv/bin/python monitor.py scan
.venv/bin/python monitor.py digest
```

Тесты: `.venv/bin/pytest` и `bash tests/test_db_sync.sh`. Сеть в тестах не нужна.

## Метрика

Всё сравнение — в `landed_usd`:

```
landed_usd = price_local × fx_rate × (1 + fx_markup[currency])
```

`fx_rate` (USD за единицу валюты) пишется в каждую строку, поэтому падение цены
всегда можно отличить от движения курса.

## Ограничения

Кэш Aviasales — это сигнал, а не оферта: цены отражают чужие поиски за последние
~48 часов и на выкупе могут отличаться. Подтверждение через Amadeus появится в v1.1.
````

- [ ] **Step 2: Написать `ASSUMPTIONS.md`**

```markdown
# Допущения и вещи вне скоупа

## Вне скоупа v1

- **Лоукостеры вне GDS и метапоиска** (AirAsia, IndiGo, склейки через DEL/KUL/DXB).
  Не скрейпим: стоимость поддержки выше пользы. С v1.1 еженедельный блок дайджеста
  напоминает проверить руками.
- **Kiwi Tequila** — доступ по заявке. Если одобрят, встраивается слоем 1.5.
- **ML-предсказание цен** — двух месяцев истории не хватит, перцентилей достаточно.
- **Веб-дашборд** — интерфейс только Telegram.
- **Мультипользовательность** — один chat_id.

## Допущения, требующие проверки на живом токене

1. **`expires_at` в `/aviasales/v3/prices_for_dates`.** В документации поля нет
   (оно есть в legacy `/v1/prices/cheap`). Парсер читает его опционально; свежесть
   определяется по `last_seen_at` в пределах `cache_ttl_hours` (48). Если поле
   придёт — фильтр начнёт использовать его автоматически.
   **Как проверить:** сохранить сырой ответ первого живого скана и посмотреть ключи.
2. **Домен ссылки по рынку.** Считаем, что `link` относительный и клеится с
   `aviasales.kg` / `aviasales.ru`. Проверить, что ссылка из дайджеста открывается.
3. **Длительность пересадки.** API её не отдаёт. Отсекаем связки, у которых плечо
   «туда» длиннее самого короткого плеча на маршруте более чем на `max_transfer_hours`.
   Приближение: если на маршруте вообще нет коротких вариантов, эталон завышен и
   фильтр пропускает больше, чем хотелось бы.
4. **Курс.** `open.er-api.com` — бесплатный, без ключа, обновляется раз в сутки.
   Внутридневное движение курса мы не ловим; для месячного горизонта это шум.
5. **Матчинг «того же рейса» между рынками** (v1.1) — по ключу маршрут + дата +
   авиакомпания + число пересадок. Кэш не даёт ID рейса, поэтому в алерте
   помечаем «≈ тот же рейс».

## Сознательные отклонения от исходной спеки

1. База в orphan-ветке `data`, а не коммитами в `main` — иначе репозиторий
   раздувается бинарником до гигабайта за два месяца.
2. Изменяемое состояние (`abs_threshold_usd`, `paused`, telegram offset) — в таблице
   `meta`, а не в `config.yaml`: три воркфлоу, пишущие один файл в main, гонялись бы
   за него, а `yaml.safe_dump` стёр бы комментарии.
3. Дедуп офферов при вставке вместо «писать все записи»: идентичный оффер обновляет
   `last_seen_at`. Любое изменение цены по-прежнему создаёт новую строку.
4. Запросов за скан 16, а не 8: возврат в октябре и ноябре — два разных `return_at`.
```

- [ ] **Step 3: Прогнать весь набор тестов**

Run: `.venv/bin/pytest && bash tests/test_db_sync.sh`
Ожидается: `102 passed` и `OK: db sync`

- [ ] **Step 4: Коммит**

```bash
git add README.md ASSUMPTIONS.md
git commit -m "docs: readme and assumptions"
```

- [ ] **Step 5: Создать публичный репозиторий и запушить**

```bash
cd /Users/rinari/Fly-tool
gh repo create flight-sniper --public --source=. --remote=origin --push
```

Ожидается: `✓ Created repository justrinari/flight-sniper on GitHub` и успешный push в main.

- [ ] **Step 6: Проверить, что воркфлоу видны**

Run: `gh workflow list`
Ожидается: четыре строки — `scan`, `digest`, `tests`, `keepalive`

---

### Task 20: Ввод секретов и первый живой прогон

**Files:** нет изменений в коде — это шаги настройки и верификации допущений.

- [ ] **Step 1: Получить токены**

1. `TP_TOKEN` — travelpayouts.com → Инструменты → API.
2. Бот у @BotFather → `TG_BOT_TOKEN`; свой chat_id у @userinfobot → `TG_CHAT_ID`.
3. Написать боту любое сообщение — иначе он не сможет ответить первым.

- [ ] **Step 2: Положить секреты в репозиторий**

```bash
gh secret set TP_TOKEN
gh secret set TG_BOT_TOKEN
gh secret set TG_CHAT_ID
gh secret list
```

Ожидается: три секрета в списке.

- [ ] **Step 3: Проверить, что кэш вообще отдаёт данные по нашим маршрутам**

```bash
cd /Users/rinari/Fly-tool
export TP_TOKEN=<токен>
.venv/bin/python - <<'PY'
import json, os, requests
params = dict(
    origin="FRU", destination="HKT", departure_at="2026-10", return_at="2026-10",
    currency="kgs", market="kg", sorting="price", direct="false", one_way="false",
    limit=5, token=os.environ["TP_TOKEN"],
)
r = requests.get("https://api.travelpayouts.com/aviasales/v3/prices_for_dates", params=params, timeout=30)
payload = r.json()
print("статус:", r.status_code, "записей:", len(payload.get("data") or []))
if payload.get("data"):
    print("ключи записи:", sorted(payload["data"][0]))
    print(json.dumps(payload["data"][0], ensure_ascii=False, indent=2))
PY
```

Ожидается: непустой список и перечень ключей. **Зафиксировать в ASSUMPTIONS.md:** есть ли `expires_at`, как выглядит `link`.

- [ ] **Step 4: Если `expires_at` в ответе есть — снять пункт 1 из ASSUMPTIONS.md**

```bash
git add ASSUMPTIONS.md
git commit -m "docs: confirm expires_at presence in v3 payload"
git push
```

- [ ] **Step 5: Прогнать бэкфилл и первый скан локально**

```bash
export TP_TOKEN=<токен> TG_BOT_TOKEN=<токен> TG_CHAT_ID=<id>
.venv/bin/python monitor.py backfill --verbose
.venv/bin/python monitor.py scan --verbose
.venv/bin/python -c "
from src import store
conn = store.connect('data/history.sqlite')
print('строк:', store.count_rows(conn))
for row in conn.execute('SELECT origin, destination, market, COUNT(*) c, MIN(landed_usd) m FROM price_history GROUP BY 1,2,3'):
    print(dict(row))
"
```

Ожидается: строки по всем четырём маршрутам и обоим рынкам, `landed_usd` в правдоподобном диапазоне (сотни долларов, не единицы и не десятки тысяч).

- [ ] **Step 6: Отправить первый дайджест себе**

```bash
.venv/bin/python monitor.py digest --verbose
```

Ожидается: сообщение в Telegram. Проверить глазами: цены правдоподобны, ссылки открываются, даты в пределах октября, длительность поездки в коридоре 10–16 ночей.

- [ ] **Step 7: Залить локальную базу в ветку data**

```bash
bash scripts/db_push.sh "initial backfill $(date -u +%F)"
git branch -r | grep data
```

Ожидается: `origin/data` существует.

- [ ] **Step 8: Прогнать воркфлоу в облаке**

```bash
gh workflow run scan.yml
sleep 60
gh run list --workflow=scan.yml --limit 1
```

Ожидается: статус `completed success`. При провале — `gh run view --log-failed`.

- [ ] **Step 9: Прогнать дайджест в облаке**

```bash
gh workflow run digest.yml
sleep 60
gh run list --workflow=digest.yml --limit 1
```

Ожидается: `completed success` и сообщение в Telegram.

- [ ] **Step 10: Зафиксировать состояние**

```bash
git add -A
git commit -m "chore: v1 live and verified" --allow-empty
git push
```

---

## Definition of Done (v1)

- [ ] Скан 4 маршрутов × 2 рынков (RT, коридор ночей) пишет в SQLite, история сохраняется между ранами
- [ ] Landed cost считается и пишется в каждую запись, `fx_rate` из бесплатного API
- [ ] Бэкфилл месячной матрицы по обоим рынкам отрабатывает на пустой базе
- [ ] Ежедневный дайджест приходит в Telegram в 09:00 Бишкек
- [ ] `pytest` зелёный, сеть в тестах не используется
- [ ] Допущения из ASSUMPTIONS.md проверены на живом ответе API

---

## Self-Review: покрытие спеки

| Требование спеки | Задача |
|---|---|
| Интерфейс только Telegram | 15, 16 — CLI существует лишь как вход для Actions |
| $0/мес, деградация вместо платных тарифов | 18 — публичный репо; Amadeus в v1 не трогается вовсе |
| Скан 4 маршрута × 2 рынка каждые 4 часа | 9, 16, 18 |
| Мультирыночный скан, колонка market | 4, 9 |
| landed_usd = price_local × fx_rate × (1 + markup), fx_rate в каждой строке | 6, 16 |
| Валюта запроса — локальная для рынка | 2 (`market_currency`), 9 |
| Коридор ночей для RT на своей стороне | 8 |
| Фильтр `max_transfer_hours` | 8 (приближение задокументировано) |
| Фильтр протухших: пишем, но не показываем | 5 (`fresh_prices`), 14 |
| Бэкфилл через grouped_prices при первом запуске | 10, 16 |
| Двухуровневый baseline, маршрутный уровень в v1 | 11, 14 |
| Светофор GREEN/YELLOW/GRAY | 12 |
| Дайджест в формате из спеки | 14 |
| Схема БД | 4 (+ `last_seen_at`, `duration_to_min`, `expires_at`, `meta`, `api_usage`) |
| Публичный репо, секреты в GitHub Secrets | 19, 20 |
| `concurrency: flight-sniper-repo` во всех yml | 18 |

Переехало в v1.1 (по плану самой спеки): Amadeus confirm, BUY-алерты и дедуп, fallback-скан,
кросс-рыночный арбитраж, dead man's switch, команды бота, тренды, еженедельный блок.
