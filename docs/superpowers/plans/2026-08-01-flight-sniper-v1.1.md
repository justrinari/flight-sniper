# flight-sniper v1.1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **Предусловие:** выполнен план `2026-08-01-flight-sniper-v1.md` — скан пишет историю, дайджест приходит в Telegram.

> ## ⚠️ ПЕРЕСМОТР ОТ 02.08.2026: Amadeus исключён
>
> Живые данные показали, что слой Amadeus для этих маршрутов бесполезен.
> **AirAsia X (D7) — 71 запись из 180 и лучшая цена на обоих маршрутах из Алматы
> ($171 ALA→HKT, $331 ALA→DPS).** Это лоукостер, которого в GDS нет. Amadeus
> систематически отвечал бы «не подтвердилось» именно на лучших находках и
> подтверждал бы только дорогие традиционные варианты — то есть добавлял бы шум,
> а не сигнал. Цены к тому же идут через посредников (City.Travel, Farera,
> Mytrip), чьи тарифы в GDS тоже отсутствуют.
>
> **Отменены задачи 1, 2, 5 (в части Amadeus), 7, 14** — квота, OAuth,
> Flight Offers Search, fallback-скан, получение production key.
>
> **Взамен — подтверждение по свежему кэшу:** перед отправкой алерта делается
> точечный запрос `prices_for_dates` по конкретной дате кандидата. Если цена
> ещё в кэше и не выше кандидата более чем на 10% — алерт уровня BUY; если
> исчезла или подорожала — уровень «кэш видел, но не подтвердилось». Это не
> гарантия оферты, но и Amadeus ею не был: он подтверждал наличие тарифа в GDS,
> тогда как покупка всё равно идёт у посредника по его цене.
>
> Плюсы: бесплатно, без ожидания одобрения ключа, без счётчиков квоты, минус
> целый слой кода. Таблица `api_usage` в схеме остаётся неиспользованной —
> удалять её не нужно, миграций у проекта нет.
>
> Ниже по тексту задачи 1, 2, 5, 7, 14 сохранены как есть для истории решения,
> но **исполнению не подлежат**. Актуальная задача подтверждения — «Задача 5-bis»
> в конце документа.

**Goal:** Превратить пассивный дайджест в активного сторожа: ловить аномальные падения цены, подтверждать их свежим запросом к кэшу, слать мгновенные BUY-алерты, находить кросс-рыночный арбитраж и принимать команды из Telegram.

**Architecture:** Детекция аномалий работает по локальной валюте внутри рынка (чтобы движение курса не выглядело как падение цены), вердикт и арбитраж — по `landed_usd`. Подтверждение — точечный повторный запрос к тому же бесплатному кэшу. Команды обрабатываются лёгким воркфлоу каждые 15 минут через `getUpdates`; изменяемое состояние живёт в таблице `meta`.

**Tech Stack:** тот же, что в v1. Новое: Telegram getUpdates. Внешних платных зависимостей не добавляется.

---

## File Structure (дельта к v1)

| Файл | Ответственность |
|---|---|
| ~~`src/sources/amadeus.py`~~ | отменено — см. пересмотр в шапке |
| ~~`src/quota.py`~~ | отменено — квота нужна была только Amadeus |
| `src/alerts.py` | **новый** — кандидаты → подтверждение по свежему кэшу → дедуп → текст алерта |
| `src/arbitrage.py` | **новый** — матчинг «≈ того же рейса» между рынками |
| `src/commands.py` | **новый** — getUpdates, фильтр chat_id, роутер команд |
| `src/rules.py` | +детекция кандидатов, +поправка на день недели, +тренды |
| `src/digest.py` | +тренды, +арбитраж, +dead man's switch, +еженедельный блок, +precision |
| `src/config.py` | +`extra_routes` в оверрайдах (команда `/route add`) |
| `src/runner.py` | +`run_commands`, алерты внутри `run_scan` |
| `monitor.py` | +подкоманда `commands` |
| `.github/workflows/commands.yml` | **новый** — cron каждые 15 минут |

---

### Task 1: Учёт квоты внешних API

**Files:**
- Create: `src/quota.py`
- Test: `tests/test_quota.py`

- [ ] **Step 1: Написать падающий тест**

```python
# tests/test_quota.py
import pytest

from src import quota, store


@pytest.fixture()
def conn(tmp_path):
    connection = store.connect(tmp_path / "history.sqlite")
    store.init_schema(connection)
    return connection


def test_record_call_increments_month_counter(conn):
    quota.record_call(conn, "amadeus", "2026-08-01T06:00:00Z", purpose="confirm")
    quota.record_call(conn, "amadeus", "2026-08-14T06:00:00Z", purpose="confirm")
    assert quota.used_this_month(conn, "amadeus", now="2026-08-20T06:00:00Z") == 2


def test_previous_month_calls_do_not_count(conn):
    quota.record_call(conn, "amadeus", "2026-07-31T23:59:59Z")
    assert quota.used_this_month(conn, "amadeus", now="2026-08-01T00:00:01Z") == 0


def test_other_apis_are_counted_separately(conn):
    quota.record_call(conn, "amadeus", "2026-08-01T06:00:00Z")
    quota.record_call(conn, "aviasales", "2026-08-01T06:00:00Z")
    assert quota.used_this_month(conn, "amadeus", now="2026-08-02T00:00:00Z") == 1


def test_used_today_counts_only_current_day(conn):
    quota.record_call(conn, "amadeus", "2026-08-01T23:00:00Z", purpose="fallback")
    quota.record_call(conn, "amadeus", "2026-08-02T01:00:00Z", purpose="fallback")
    assert quota.used_today(conn, "amadeus", now="2026-08-02T12:00:00Z") == 1


def test_used_today_filters_by_purpose(conn):
    quota.record_call(conn, "amadeus", "2026-08-02T01:00:00Z", purpose="fallback")
    quota.record_call(conn, "amadeus", "2026-08-02T02:00:00Z", purpose="confirm")
    assert quota.used_today(conn, "amadeus", now="2026-08-02T12:00:00Z", purpose="fallback") == 1


def test_check_allows_call_below_limit(conn):
    assert quota.check(conn, "amadeus", now="2026-08-01T06:00:00Z", monthly_limit=3) is True


def test_check_blocks_at_hard_stop(conn):
    for hour in range(3):
        quota.record_call(conn, "amadeus", f"2026-08-01T0{hour}:00:00Z")
    assert quota.check(conn, "amadeus", now="2026-08-01T06:00:00Z", monthly_limit=3) is False


def test_check_blocks_when_daily_budget_exhausted(conn):
    quota.record_call(conn, "amadeus", "2026-08-01T01:00:00Z", purpose="fallback")
    quota.record_call(conn, "amadeus", "2026-08-01T02:00:00Z", purpose="fallback")
    allowed = quota.check(
        conn,
        "amadeus",
        now="2026-08-01T06:00:00Z",
        monthly_limit=1800,
        daily_limit=2,
        purpose="fallback",
    )
    assert allowed is False


def test_guard_raises_when_exhausted(conn):
    for hour in range(2):
        quota.record_call(conn, "amadeus", f"2026-08-01T0{hour}:00:00Z")
    with pytest.raises(quota.QuotaExceeded, match="1800"):
        with quota.guard(conn, "amadeus", now="2026-08-01T06:00:00Z", monthly_limit=2):
            pass


def test_guard_records_call_on_success(conn):
    with quota.guard(conn, "amadeus", now="2026-08-01T06:00:00Z", monthly_limit=10):
        pass
    assert quota.used_this_month(conn, "amadeus", now="2026-08-01T07:00:00Z") == 1


def test_guard_does_not_record_when_body_raises(conn):
    with pytest.raises(ValueError):
        with quota.guard(conn, "amadeus", now="2026-08-01T06:00:00Z", monthly_limit=10):
            raise ValueError("сеть отвалилась")
    assert quota.used_this_month(conn, "amadeus", now="2026-08-01T07:00:00Z") == 0
```

- [ ] **Step 2: Запустить тест, убедиться в падении**

Run: `.venv/bin/pytest tests/test_quota.py -v`
Ожидается: FAIL, `ModuleNotFoundError: No module named 'src.quota'`

- [ ] **Step 3: Реализовать `src/quota.py`**

```python
"""Учёт вызовов платных-по-квоте API.

Amadeus Self-Service даёт 2000 бесплатных запросов в месяц. Hard stop стоит
на 1800, чтобы случайный цикл не выбил нас за бесплатный тариф. Счётчик живёт
в той же БД, что и история, — значит переживает раны Actions.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from typing import Optional

MONTHLY_LIMIT = 1800
FALLBACK_DAILY_LIMIT = 20


class QuotaExceeded(RuntimeError):
    """Лимит исчерпан — вызов не выполняется."""


def record_call(
    conn: sqlite3.Connection, api: str, called_at: str, purpose: Optional[str] = None
) -> None:
    conn.execute(
        "INSERT INTO api_usage (api, called_at, purpose) VALUES (?, ?, ?)",
        (api, called_at, purpose),
    )
    conn.commit()


def used_this_month(conn: sqlite3.Connection, api: str, now: str) -> int:
    month = now[:7]
    row = conn.execute(
        "SELECT COUNT(*) FROM api_usage WHERE api = ? AND substr(called_at, 1, 7) = ?",
        (api, month),
    ).fetchone()
    return int(row[0])


def used_today(
    conn: sqlite3.Connection, api: str, now: str, purpose: Optional[str] = None
) -> int:
    day = now[:10]
    sql = "SELECT COUNT(*) FROM api_usage WHERE api = ? AND substr(called_at, 1, 10) = ?"
    params: list[object] = [api, day]
    if purpose is not None:
        sql += " AND purpose = ?"
        params.append(purpose)
    return int(conn.execute(sql, params).fetchone()[0])


def check(
    conn: sqlite3.Connection,
    api: str,
    now: str,
    monthly_limit: int = MONTHLY_LIMIT,
    daily_limit: Optional[int] = None,
    purpose: Optional[str] = None,
) -> bool:
    if used_this_month(conn, api, now) >= monthly_limit:
        return False
    if daily_limit is not None and used_today(conn, api, now, purpose) >= daily_limit:
        return False
    return True


@contextmanager
def guard(
    conn: sqlite3.Connection,
    api: str,
    now: str,
    monthly_limit: int = MONTHLY_LIMIT,
    daily_limit: Optional[int] = None,
    purpose: Optional[str] = None,
):
    """Пропускает вызов и засчитывает его только при успехе.

    Провалившийся из-за сети запрос квоту не тратит — Amadeus его тоже не считает.
    """
    if not check(conn, api, now, monthly_limit, daily_limit, purpose):
        raise QuotaExceeded(
            f"{api}: квота исчерпана (месяц {used_this_month(conn, api, now)}/{monthly_limit})"
        )
    yield
    record_call(conn, api, now, purpose)
```

- [ ] **Step 4: Запустить тесты**

Run: `.venv/bin/pytest tests/test_quota.py -v`
Ожидается: 11 passed

- [ ] **Step 5: Коммит**

```bash
git add src/quota.py tests/test_quota.py
git commit -m "feat: api quota accounting with hard stop"
```

---

### Task 2: Amadeus — авторизация и поиск офферов

**Files:**
- Create: `src/sources/amadeus.py`
- Test: `tests/sources/test_amadeus.py`

- [ ] **Step 1: Написать падающий тест**

```python
# tests/sources/test_amadeus.py
import pytest
import requests
import responses

from src.sources import amadeus

TOKEN_URL = f"{amadeus.BASE_URL}/v1/security/oauth2/token"
SEARCH_URL = f"{amadeus.BASE_URL}/v2/shopping/flight-offers"

TOKEN_PAYLOAD = {"access_token": "AT", "expires_in": 1799, "type": "amadeusOAuth2Token"}

OFFERS_PAYLOAD = {
    "data": [
        {
            "id": "1",
            "price": {"grandTotal": "512.30", "currency": "USD"},
            "validatingAirlineCodes": ["KC"],
            "itineraries": [
                {
                    "duration": "PT18H30M",
                    "segments": [
                        {"carrierCode": "KC", "departure": {"at": "2026-10-12T10:15:00"}},
                        {"carrierCode": "KC", "departure": {"at": "2026-10-12T20:00:00"}},
                    ],
                },
                {
                    "duration": "PT17H05M",
                    "segments": [
                        {"carrierCode": "KC", "departure": {"at": "2026-10-24T22:00:00"}}
                    ],
                },
            ],
        },
        {
            "id": "2",
            "price": {"grandTotal": "489.00", "currency": "USD"},
            "validatingAirlineCodes": ["TK"],
            "itineraries": [
                {
                    "duration": "PT21H00M",
                    "segments": [
                        {"carrierCode": "TK", "departure": {"at": "2026-10-12T04:00:00"}},
                        {"carrierCode": "TK", "departure": {"at": "2026-10-12T14:00:00"}},
                        {"carrierCode": "TK", "departure": {"at": "2026-10-12T22:00:00"}},
                    ],
                }
            ],
        },
    ]
}


@pytest.fixture()
def session():
    return requests.Session()


def test_iso_duration_to_minutes_hours_and_minutes():
    assert amadeus.duration_minutes("PT18H30M") == 1110


def test_iso_duration_to_minutes_hours_only():
    assert amadeus.duration_minutes("PT9H") == 540


def test_iso_duration_to_minutes_minutes_only():
    assert amadeus.duration_minutes("PT45M") == 45


def test_iso_duration_with_days():
    assert amadeus.duration_minutes("P1DT2H") == 1560


def test_iso_duration_invalid_returns_zero():
    assert amadeus.duration_minutes("не длительность") == 0
    assert amadeus.duration_minutes(None) == 0


@responses.activate
def test_get_token_posts_client_credentials(session):
    responses.add(responses.POST, TOKEN_URL, json=TOKEN_PAYLOAD, status=200)
    token = amadeus.get_token(session, "KEY", "SECRET")
    assert token == "AT"
    body = responses.calls[0].request.body
    assert "grant_type=client_credentials" in body
    assert "client_id=KEY" in body
    assert "client_secret=SECRET" in body


@responses.activate
def test_get_token_raises_on_bad_credentials(session):
    responses.add(
        responses.POST,
        TOKEN_URL,
        json={"error": "invalid_client", "error_description": "wrong key"},
        status=401,
    )
    with pytest.raises(amadeus.AmadeusError, match="invalid_client"):
        amadeus.get_token(session, "KEY", "SECRET")


@responses.activate
def test_search_sends_expected_params(session):
    responses.add(responses.GET, SEARCH_URL, json=OFFERS_PAYLOAD, status=200)
    amadeus.search_offers(
        session, "AT", "FRU", "HKT", "2026-10-12", "2026-10-24", max_offers=5
    )
    url = responses.calls[0].request.url
    assert "originLocationCode=FRU" in url
    assert "destinationLocationCode=HKT" in url
    assert "departureDate=2026-10-12" in url
    assert "returnDate=2026-10-24" in url
    assert "adults=1" in url
    assert "currencyCode=USD" in url
    assert "max=5" in url


@responses.activate
def test_search_sends_bearer_token(session):
    responses.add(responses.GET, SEARCH_URL, json=OFFERS_PAYLOAD, status=200)
    amadeus.search_offers(session, "AT", "FRU", "HKT", "2026-10-12", "2026-10-24")
    assert responses.calls[0].request.headers["Authorization"] == "Bearer AT"


@responses.activate
def test_search_omits_return_date_for_one_way(session):
    responses.add(responses.GET, SEARCH_URL, json=OFFERS_PAYLOAD, status=200)
    amadeus.search_offers(session, "AT", "FRU", "HKT", "2026-10-12", None)
    assert "returnDate" not in responses.calls[0].request.url


@responses.activate
def test_search_returns_price_records(session):
    responses.add(responses.GET, SEARCH_URL, json=OFFERS_PAYLOAD, status=200)
    records = amadeus.search_offers(session, "AT", "FRU", "HKT", "2026-10-12", "2026-10-24")
    assert len(records) == 2
    first = records[0]
    assert first.source == "amadeus"
    assert first.market == "gds"
    assert first.currency == "usd"
    assert first.price_local == 512.30
    assert first.airline == "KC"
    assert first.transfers == 1  # два сегмента в плече «туда»
    assert first.duration_to_min == 1110
    assert first.duration_min == 1110 + 1025


@responses.activate
def test_search_counts_transfers_per_outbound_itinerary(session):
    responses.add(responses.GET, SEARCH_URL, json=OFFERS_PAYLOAD, status=200)
    records = amadeus.search_offers(session, "AT", "FRU", "HKT", "2026-10-12", "2026-10-24")
    assert records[1].transfers == 2  # три сегмента


@responses.activate
def test_search_raises_on_http_error(session):
    responses.add(
        responses.GET,
        SEARCH_URL,
        json={"errors": [{"detail": "Invalid date"}]},
        status=400,
    )
    with pytest.raises(amadeus.AmadeusError, match="Invalid date"):
        amadeus.search_offers(session, "AT", "FRU", "HKT", "2026-10-12", "2026-10-24")


@responses.activate
def test_search_with_empty_data_returns_empty_list(session):
    responses.add(responses.GET, SEARCH_URL, json={"data": []}, status=200)
    assert amadeus.search_offers(session, "AT", "FRU", "HKT", "2026-10-12", "2026-10-24") == []


@responses.activate
def test_cheapest_returns_lowest_price_record(session):
    responses.add(responses.GET, SEARCH_URL, json=OFFERS_PAYLOAD, status=200)
    records = amadeus.search_offers(session, "AT", "FRU", "HKT", "2026-10-12", "2026-10-24")
    assert amadeus.cheapest(records).price_local == 489.00


def test_cheapest_of_empty_is_none():
    assert amadeus.cheapest([]) is None
```

- [ ] **Step 2: Запустить тест, убедиться в падении**

Run: `.venv/bin/pytest tests/sources/test_amadeus.py -v`
Ожидается: FAIL, `ModuleNotFoundError: No module named 'src.sources.amadeus'`

- [ ] **Step 3: Реализовать `src/sources/amadeus.py`**

```python
"""Amadeus Self-Service — realtime-подтверждение кандидатов.

ВАЖНО: нужен production key. Test-окружение отдаёт урезанный кэшированный набор
и для реальных цен непригодно — там оно годится только для отладки кода.
Базовый URL переключается переменной AMADEUS_BASE_URL.

Amadeus подтверждает существование тарифа в GDS; покупка идёт через Aviasales
или сайт авиакомпании, финальная цена может разойтись — для этого есть /bought
и /mismatch.
"""

from __future__ import annotations

import os
import re
from typing import Optional, Sequence

import requests

from src.models import PriceRecord

BASE_URL = os.environ.get("AMADEUS_BASE_URL", "https://api.amadeus.com")
SOURCE = "amadeus"
MARKET = "gds"
TIMEOUT = 30

_DURATION_RE = re.compile(
    r"^P(?:(?P<days>\d+)D)?T(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?$"
)


class AmadeusError(RuntimeError):
    """Amadeus отверг запрос или не ответил."""


def duration_minutes(value: Optional[str]) -> int:
    if not value:
        return 0
    match = _DURATION_RE.match(value.strip())
    if not match:
        return 0
    days = int(match.group("days") or 0)
    hours = int(match.group("hours") or 0)
    minutes = int(match.group("minutes") or 0)
    return days * 24 * 60 + hours * 60 + minutes


def _error_text(response: requests.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return response.text[:200]
    if "errors" in payload:
        return "; ".join(
            str(err.get("detail") or err.get("title") or err) for err in payload["errors"]
        )
    return str(payload.get("error_description") or payload.get("error") or payload)[:200]


def get_token(session: requests.Session, key: str, secret: str) -> str:
    try:
        response = session.post(
            f"{BASE_URL}/v1/security/oauth2/token",
            data={
                "grant_type": "client_credentials",
                "client_id": key,
                "client_secret": secret,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=TIMEOUT,
        )
    except requests.RequestException as exc:
        raise AmadeusError(f"token: {exc}") from exc
    if response.status_code != 200:
        raise AmadeusError(f"token: {_error_text(response)}")
    try:
        return response.json()["access_token"]
    except (ValueError, KeyError) as exc:
        raise AmadeusError("token: в ответе нет access_token") from exc


def _parse_offers(
    payload: dict, origin: str, destination: str, depart_date: str, return_date: Optional[str]
) -> list[PriceRecord]:
    records: list[PriceRecord] = []
    for offer in payload.get("data") or []:
        try:
            itineraries = offer.get("itineraries") or []
            if not itineraries:
                continue
            outbound = itineraries[0]
            outbound_minutes = duration_minutes(outbound.get("duration"))
            total_minutes = sum(duration_minutes(it.get("duration")) for it in itineraries)
            segments = outbound.get("segments") or []
            airline = (offer.get("validatingAirlineCodes") or [""])[0] or (
                segments[0].get("carrierCode", "") if segments else ""
            )
            records.append(
                PriceRecord(
                    source=SOURCE,
                    origin=origin,
                    destination=destination,
                    market=MARKET,
                    depart_date=depart_date,
                    return_date=return_date,
                    price_local=float(offer["price"]["grandTotal"]),
                    currency=str(offer["price"].get("currency", "USD")).lower(),
                    airline=airline,
                    transfers=max(len(segments) - 1, 0),
                    duration_min=total_minutes,
                    duration_to_min=outbound_minutes,
                    search_url="",
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    return records


def search_offers(
    session: requests.Session,
    access_token: str,
    origin: str,
    destination: str,
    depart_date: str,
    return_date: Optional[str],
    adults: int = 1,
    max_offers: int = 5,
) -> list[PriceRecord]:
    params = {
        "originLocationCode": origin,
        "destinationLocationCode": destination,
        "departureDate": depart_date,
        "adults": adults,
        "currencyCode": "USD",
        "max": max_offers,
        "nonStop": "false",
    }
    if return_date:
        params["returnDate"] = return_date
    try:
        response = session.get(
            f"{BASE_URL}/v2/shopping/flight-offers",
            params=params,
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=TIMEOUT,
        )
    except requests.RequestException as exc:
        raise AmadeusError(f"flight-offers: {exc}") from exc
    if response.status_code != 200:
        raise AmadeusError(f"flight-offers: {_error_text(response)}")
    try:
        payload = response.json()
    except ValueError as exc:
        raise AmadeusError("flight-offers: ответ не является JSON") from exc
    return _parse_offers(payload, origin, destination, depart_date, return_date)


def cheapest(records: Sequence[PriceRecord]) -> Optional[PriceRecord]:
    if not records:
        return None
    return min(records, key=lambda r: r.price_local)
```

- [ ] **Step 4: Запустить тесты**

Run: `.venv/bin/pytest tests/sources/test_amadeus.py -v`
Ожидается: 16 passed

- [ ] **Step 5: Коммит**

```bash
git add src/sources/amadeus.py tests/sources/test_amadeus.py
git commit -m "feat: amadeus oauth and flight offers search"
```

---

### Task 3: Детекция кандидатов-аномалий

**Files:**
- Modify: `src/rules.py` (дописать в конец)
- Test: `tests/test_rules_anomaly.py`

- [ ] **Step 1: Написать падающий тест**

```python
# tests/test_rules_anomaly.py
import pytest

from src import rules, store
from src.models import PriceRecord


@pytest.fixture()
def conn(tmp_path):
    connection = store.connect(tmp_path / "history.sqlite")
    store.init_schema(connection)
    return connection


def record(price_local, depart="2026-10-12", market="kg", landed=None, airline="KC"):
    return PriceRecord(
        source="aviasales_cache",
        origin="FRU",
        destination="HKT",
        market=market,
        depart_date=depart,
        return_date="2026-10-24",
        price_local=price_local,
        currency="kgs" if market == "kg" else "rub",
        airline=airline,
        transfers=1,
        duration_min=1500,
        duration_to_min=760,
        search_url="https://x",
        fx_rate=1 / 87.5,
        landed_usd=landed if landed is not None else price_local / 87.5,
    )


def seed(conn, prices, now, **kwargs):
    store.insert_prices(conn, [record(p, **kwargs) for p in prices], now=now)


def test_no_candidates_without_enough_history(conn, config_stub):
    seed(conn, [30000, 31000], now="2026-08-01T00:00:00Z")
    candidates = rules.find_candidates(conn, config_stub, now="2026-08-01T06:00:00Z")
    assert candidates == []


def test_price_below_local_p10_becomes_candidate(conn, config_stub):
    seed(conn, [30000, 31000, 32000, 33000, 34000, 35000], now="2026-07-25T00:00:00Z")
    seed(conn, [24000], now="2026-08-01T05:00:00Z")
    candidates = rules.find_candidates(conn, config_stub, now="2026-08-01T06:00:00Z")
    assert len(candidates) == 1
    assert candidates[0].price_local == 24000
    assert candidates[0].origin == "FRU"
    assert candidates[0].destination == "HKT"


def test_ordinary_price_is_not_a_candidate(conn, config_stub):
    seed(conn, [30000, 31000, 32000, 33000, 34000, 35000], now="2026-07-25T00:00:00Z")
    seed(conn, [32500], now="2026-08-01T05:00:00Z")
    assert rules.find_candidates(conn, config_stub, now="2026-08-01T06:00:00Z") == []


def test_stale_cheap_row_is_not_a_candidate(conn, config_stub):
    seed(conn, [30000, 31000, 32000, 33000, 34000, 35000], now="2026-07-25T00:00:00Z")
    seed(conn, [24000], now="2026-07-26T00:00:00Z")  # старше 48 ч
    assert rules.find_candidates(conn, config_stub, now="2026-08-01T06:00:00Z") == []


def test_baseline_is_per_market_in_local_currency(conn, config_stub):
    # Дешёвый рубль не должен делать российские цены «аномальными» для kg.
    seed(conn, [30000, 31000, 32000, 33000, 34000, 35000], now="2026-07-25T00:00:00Z", market="kg")
    seed(conn, [24000], now="2026-08-01T05:00:00Z", market="ru")
    assert rules.find_candidates(conn, config_stub, now="2026-08-01T06:00:00Z") == []


def test_candidate_carries_baseline_and_row(conn, config_stub):
    seed(conn, [30000, 31000, 32000, 33000, 34000, 35000], now="2026-07-25T00:00:00Z")
    seed(conn, [24000], now="2026-08-01T05:00:00Z")
    candidate = rules.find_candidates(conn, config_stub, now="2026-08-01T06:00:00Z")[0]
    assert candidate.baseline.n == 7
    assert candidate.landed_usd == pytest.approx(24000 / 87.5)
    assert candidate.market == "kg"
    assert candidate.depart_date == "2026-10-12"
    assert candidate.route_date_key == "FRU-HKT-2026-10-12-2026-10-24"


def test_only_cheapest_candidate_per_route_date_is_returned(conn, config_stub):
    seed(conn, [30000, 31000, 32000, 33000, 34000, 35000], now="2026-07-25T00:00:00Z")
    seed(conn, [24000], now="2026-08-01T05:00:00Z", airline="KC")
    seed(conn, [23000], now="2026-08-01T05:00:00Z", airline="TK")
    candidates = rules.find_candidates(conn, config_stub, now="2026-08-01T06:00:00Z")
    assert len(candidates) == 1
    assert candidates[0].price_local == 23000


def test_candidates_sorted_by_landed_price(conn, config_stub):
    seed(conn, [30000, 31000, 32000, 33000, 34000, 35000], now="2026-07-25T00:00:00Z")
    seed(conn, [24000], now="2026-08-01T05:00:00Z", depart="2026-10-12")
    seed(conn, [22000], now="2026-08-01T05:00:00Z", depart="2026-10-14")
    candidates = rules.find_candidates(conn, config_stub, now="2026-08-01T06:00:00Z")
    assert [c.price_local for c in candidates] == [22000, 24000]
```

- [ ] **Step 2: Запустить тест, убедиться в падении**

Run: `.venv/bin/pytest tests/test_rules_anomaly.py -v`
Ожидается: FAIL, `AttributeError: module 'src.rules' has no attribute 'find_candidates'`

- [ ] **Step 3: Дописать детекцию в `src/rules.py`**

```python
# --- дописать в конец src/rules.py ---

import sqlite3  # noqa: E402

from src import store  # noqa: E402


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
    search_url: Optional[str]
    baseline: Baseline  # в локальной валюте того же рынка

    @property
    def route_date_key(self) -> str:
        return f"{self.origin}-{self.destination}-{self.depart_date}-{self.return_date or ''}"


def find_candidates(conn: sqlite3.Connection, cfg, now: str) -> list[Candidate]:
    """Свежие цены ниже p10 своего рынка.

    Сравнение идёт в локальной валюте внутри одного рынка: так падение цены
    не путается с движением курса. Вердикт BUY дальше считается по landed_usd.
    """
    window_start = store.shift_days(now, -cfg.baseline_window_days)
    candidates: list[Candidate] = []

    for origin, destination in cfg.routes():
        for market in cfg.markets:
            history = store.recent_prices(
                conn, origin, destination, since=window_start, market=market
            )
            baseline = compute_baseline(
                [row["price_local"] for row in history], cfg.anomaly_percentile
            )
            if baseline is None:
                continue

            fresh = [
                row
                for row in store.fresh_prices(
                    conn, origin, destination, now=now, ttl_hours=cfg.cache_ttl_hours, market=market
                )
                if row["landed_usd"] is not None
                and row["price_local"] < baseline.anomaly_threshold
            ]
            best_per_date: dict[str, sqlite3.Row] = {}
            for row in fresh:
                key = f"{row['depart_date']}-{row['return_date'] or ''}"
                current = best_per_date.get(key)
                if current is None or row["price_local"] < current["price_local"]:
                    best_per_date[key] = row

            for row in best_per_date.values():
                candidates.append(
                    Candidate(
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
                        search_url=row["search_url"],
                        baseline=baseline,
                    )
                )

    return sorted(candidates, key=lambda c: c.landed_usd)
```

- [ ] **Step 4: Запустить тесты**

Run: `.venv/bin/pytest tests/test_rules_anomaly.py -v`
Ожидается: 8 passed

- [ ] **Step 5: Коммит**

```bash
git add src/rules.py tests/test_rules_anomaly.py
git commit -m "feat: anomaly candidate detection per market in local currency"
```

---

### Task 4: Дедупликация алертов

**Files:**
- Modify: `src/store.py` (дописать в конец)
- Test: `tests/test_store_alerts.py`

- [ ] **Step 1: Написать падающий тест**

```python
# tests/test_store_alerts.py
import pytest

from src import store


@pytest.fixture()
def conn(tmp_path):
    connection = store.connect(tmp_path / "history.sqlite")
    store.init_schema(connection)
    return connection


KEY = "FRU-HKT-2026-10-12-2026-10-24"


def test_first_alert_is_always_allowed(conn):
    assert store.should_alert(conn, KEY, landed_usd=300.0, drop_ratio=0.05) is True


def test_same_price_is_suppressed(conn):
    store.record_alert(conn, KEY, 300.0, "2026-08-01T06:00:00Z", "buy")
    assert store.should_alert(conn, KEY, landed_usd=300.0, drop_ratio=0.05) is False


def test_small_drop_is_suppressed(conn):
    store.record_alert(conn, KEY, 300.0, "2026-08-01T06:00:00Z", "buy")
    assert store.should_alert(conn, KEY, landed_usd=290.0, drop_ratio=0.05) is False


def test_drop_beyond_threshold_reopens_alert(conn):
    store.record_alert(conn, KEY, 300.0, "2026-08-01T06:00:00Z", "buy")
    assert store.should_alert(conn, KEY, landed_usd=284.0, drop_ratio=0.05) is True


def test_price_increase_is_suppressed(conn):
    store.record_alert(conn, KEY, 300.0, "2026-08-01T06:00:00Z", "buy")
    assert store.should_alert(conn, KEY, landed_usd=320.0, drop_ratio=0.05) is False


def test_other_route_is_independent(conn):
    store.record_alert(conn, KEY, 300.0, "2026-08-01T06:00:00Z", "buy")
    assert store.should_alert(conn, "ALA-DPS-2026-10-12-", 300.0, 0.05) is True


def test_comparison_uses_the_cheapest_previous_alert(conn):
    store.record_alert(conn, KEY, 300.0, "2026-08-01T06:00:00Z", "buy")
    store.record_alert(conn, KEY, 280.0, "2026-08-02T06:00:00Z", "buy")
    store.record_alert(conn, KEY, 290.0, "2026-08-03T06:00:00Z", "buy")
    assert store.should_alert(conn, KEY, landed_usd=275.0, drop_ratio=0.05) is False
    assert store.should_alert(conn, KEY, landed_usd=265.0, drop_ratio=0.05) is True


def test_last_alert_returns_most_recent_row(conn):
    store.record_alert(conn, KEY, 300.0, "2026-08-01T06:00:00Z", "buy")
    store.record_alert(conn, KEY, 280.0, "2026-08-02T06:00:00Z", "buy")
    row = store.last_alert(conn)
    assert row["landed_usd"] == 280.0
    assert row["route_date_key"] == KEY


def test_last_alert_on_empty_table_is_none(conn):
    assert store.last_alert(conn) is None


def test_set_feedback_updates_latest_alert(conn):
    store.record_alert(conn, KEY, 300.0, "2026-08-01T06:00:00Z", "buy")
    store.record_alert(conn, KEY, 280.0, "2026-08-02T06:00:00Z", "buy")
    assert store.set_last_feedback(conn, "bought") is True
    assert store.last_alert(conn)["feedback"] == "bought"


def test_set_feedback_without_alerts_returns_false(conn):
    assert store.set_last_feedback(conn, "bought") is False


def test_precision_counts_feedback(conn):
    store.record_alert(conn, KEY, 300.0, "2026-08-01T06:00:00Z", "buy", feedback="bought")
    store.record_alert(conn, KEY, 280.0, "2026-08-02T06:00:00Z", "buy", feedback="mismatch")
    store.record_alert(conn, KEY, 270.0, "2026-08-03T06:00:00Z", "buy")
    stats = store.alert_stats(conn, since="2026-07-01T00:00:00Z")
    assert stats == {"total": 3, "bought": 1, "mismatch": 1, "no_feedback": 1}
```

- [ ] **Step 2: Запустить тест, убедиться в падении**

Run: `.venv/bin/pytest tests/test_store_alerts.py -v`
Ожидается: FAIL, `AttributeError: module 'src.store' has no attribute 'should_alert'`

- [ ] **Step 3: Дописать функции алертов в `src/store.py`**

```python
# --- дописать в конец src/store.py ---


def record_alert(
    conn: sqlite3.Connection,
    route_date_key: str,
    landed_usd: float,
    sent_at: str,
    level: str,
    feedback: Optional[str] = None,
) -> None:
    conn.execute(
        "INSERT INTO alerts_sent (route_date_key, landed_usd, sent_at, level, feedback)"
        " VALUES (?, ?, ?, ?, ?)",
        (route_date_key, landed_usd, sent_at, level, feedback),
    )
    conn.commit()


def should_alert(
    conn: sqlite3.Connection, route_date_key: str, landed_usd: float, drop_ratio: float
) -> bool:
    """Повторный алерт по той же связке — только если цена упала ещё на drop_ratio."""
    row = conn.execute(
        "SELECT MIN(landed_usd) AS best FROM alerts_sent WHERE route_date_key = ?",
        (route_date_key,),
    ).fetchone()
    if row is None or row["best"] is None:
        return True
    return landed_usd <= float(row["best"]) * (1.0 - drop_ratio)


def last_alert(conn: sqlite3.Connection) -> Optional[sqlite3.Row]:
    return conn.execute("SELECT * FROM alerts_sent ORDER BY id DESC LIMIT 1").fetchone()


def set_last_feedback(conn: sqlite3.Connection, feedback: str) -> bool:
    row = last_alert(conn)
    if row is None:
        return False
    conn.execute("UPDATE alerts_sent SET feedback = ? WHERE id = ?", (feedback, row["id"]))
    conn.commit()
    return True


def alert_stats(conn: sqlite3.Connection, since: str) -> dict[str, int]:
    rows = list(conn.execute("SELECT feedback FROM alerts_sent WHERE sent_at >= ?", (since,)))
    return {
        "total": len(rows),
        "bought": sum(1 for r in rows if r["feedback"] == "bought"),
        "mismatch": sum(1 for r in rows if r["feedback"] == "mismatch"),
        "no_feedback": sum(1 for r in rows if r["feedback"] is None),
    }
```

- [ ] **Step 4: Запустить тесты**

Run: `.venv/bin/pytest tests/test_store_alerts.py -v`
Ожидается: 12 passed

- [ ] **Step 5: Коммит**

```bash
git add src/store.py tests/test_store_alerts.py
git commit -m "feat: alert dedup, feedback and precision stats"
```

---

### Task 5: Подтверждение кандидатов и текст алерта

**Files:**
- Create: `src/alerts.py`
- Test: `tests/test_alerts.py`

- [ ] **Step 1: Написать падающий тест**

```python
# tests/test_alerts.py
import pytest
import requests
import responses

from src import alerts, quota, rules, store
from src.sources import amadeus

TOKEN_URL = f"{amadeus.BASE_URL}/v1/security/oauth2/token"
SEARCH_URL = f"{amadeus.BASE_URL}/v2/shopping/flight-offers"
TOKEN_PAYLOAD = {"access_token": "AT", "expires_in": 1799}


@pytest.fixture()
def conn(tmp_path):
    connection = store.connect(tmp_path / "history.sqlite")
    store.init_schema(connection)
    return connection


@pytest.fixture()
def session():
    return requests.Session()


def offers(price):
    return {
        "data": [
            {
                "id": "1",
                "price": {"grandTotal": f"{price:.2f}", "currency": "USD"},
                "validatingAirlineCodes": ["KC"],
                "itineraries": [
                    {
                        "duration": "PT18H30M",
                        "segments": [{"carrierCode": "KC", "departure": {"at": "2026-10-12"}}],
                    }
                ],
            }
        ]
    }


def candidate(landed=250.0):
    return rules.Candidate(
        origin="FRU",
        destination="HKT",
        market="kg",
        depart_date="2026-10-12",
        return_date="2026-10-24",
        price_local=landed * 87.5,
        currency="kgs",
        landed_usd=landed,
        airline="KC",
        transfers=1,
        search_url="https://www.aviasales.kg/search/x",
        baseline=rules.Baseline(
            n=30, median=30000.0, minimum=21000.0, anomaly_threshold=24000.0
        ),
    )


@responses.activate
def test_confirm_returns_buy_when_price_matches(conn, session):
    responses.add(responses.POST, TOKEN_URL, json=TOKEN_PAYLOAD, status=200)
    responses.add(responses.GET, SEARCH_URL, json=offers(262.0), status=200)
    result = alerts.confirm(conn, session, "KEY", "SECRET", candidate(250.0), now="2026-08-01T06:00:00Z")
    assert result.level == alerts.LEVEL_BUY
    assert result.confirmed_usd == 262.0


@responses.activate
def test_confirm_returns_unconfirmed_when_price_is_far_higher(conn, session):
    responses.add(responses.POST, TOKEN_URL, json=TOKEN_PAYLOAD, status=200)
    responses.add(responses.GET, SEARCH_URL, json=offers(340.0), status=200)
    result = alerts.confirm(conn, session, "KEY", "SECRET", candidate(250.0), now="2026-08-01T06:00:00Z")
    assert result.level == alerts.LEVEL_UNCONFIRMED
    assert result.confirmed_usd == 340.0


@responses.activate
def test_confirm_boundary_of_ten_percent_is_buy(conn, session):
    responses.add(responses.POST, TOKEN_URL, json=TOKEN_PAYLOAD, status=200)
    responses.add(responses.GET, SEARCH_URL, json=offers(275.0), status=200)
    result = alerts.confirm(conn, session, "KEY", "SECRET", candidate(250.0), now="2026-08-01T06:00:00Z")
    assert result.level == alerts.LEVEL_BUY


@responses.activate
def test_confirm_spends_quota(conn, session):
    responses.add(responses.POST, TOKEN_URL, json=TOKEN_PAYLOAD, status=200)
    responses.add(responses.GET, SEARCH_URL, json=offers(262.0), status=200)
    alerts.confirm(conn, session, "KEY", "SECRET", candidate(), now="2026-08-01T06:00:00Z")
    assert quota.used_this_month(conn, "amadeus", now="2026-08-01T07:00:00Z") == 1


@responses.activate
def test_confirm_without_offers_is_unconfirmed(conn, session):
    responses.add(responses.POST, TOKEN_URL, json=TOKEN_PAYLOAD, status=200)
    responses.add(responses.GET, SEARCH_URL, json={"data": []}, status=200)
    result = alerts.confirm(conn, session, "KEY", "SECRET", candidate(), now="2026-08-01T06:00:00Z")
    assert result.level == alerts.LEVEL_UNCONFIRMED
    assert result.confirmed_usd is None
    assert "не нашёл" in result.note


@responses.activate
def test_confirm_survives_amadeus_error(conn, session):
    responses.add(responses.POST, TOKEN_URL, json=TOKEN_PAYLOAD, status=200)
    responses.add(responses.GET, SEARCH_URL, json={"errors": [{"detail": "boom"}]}, status=400)
    result = alerts.confirm(conn, session, "KEY", "SECRET", candidate(), now="2026-08-01T06:00:00Z")
    assert result.level == alerts.LEVEL_UNCONFIRMED
    assert "boom" in result.note


def test_confirm_blocked_by_quota(conn, session):
    for index in range(1800):
        conn.execute(
            "INSERT INTO api_usage (api, called_at, purpose) VALUES ('amadeus', ?, 'confirm')",
            (f"2026-08-01T06:00:{index % 60:02d}Z",),
        )
    conn.commit()
    result = alerts.confirm(conn, session, "KEY", "SECRET", candidate(), now="2026-08-01T07:00:00Z")
    assert result.level == alerts.LEVEL_UNCONFIRMED
    assert "квота" in result.note


def test_render_buy_alert_contains_essentials():
    result = alerts.ConfirmResult(
        candidate=candidate(250.0),
        level=alerts.LEVEL_BUY,
        confirmed_usd=262.0,
        note="",
    )
    text = alerts.render_alert(result)
    assert "BUY" in text
    assert "FRU→HKT" in text
    assert "$250" in text
    assert "$262" in text
    assert "12.10" in text
    assert "24.10" in text
    assert "Air Astana" in text
    assert "1 пересадка" in text
    assert "aviasales.kg" in text
    assert "kg" in text


def test_render_unconfirmed_alert_is_marked_as_calibration():
    result = alerts.ConfirmResult(
        candidate=candidate(250.0),
        level=alerts.LEVEL_UNCONFIRMED,
        confirmed_usd=340.0,
        note="",
    )
    text = alerts.render_alert(result)
    assert "не подтвердилось" in text
    assert "$340" in text


def test_render_escapes_html():
    bad = candidate(250.0)
    bad = rules.Candidate(**{**bad.__dict__, "search_url": "https://x/?a=1&b=2"})
    text = alerts.render_alert(
        alerts.ConfirmResult(candidate=bad, level=alerts.LEVEL_BUY, confirmed_usd=250.0, note="")
    )
    assert "&amp;b=2" in text
```

- [ ] **Step 2: Запустить тест, убедиться в падении**

Run: `.venv/bin/pytest tests/test_alerts.py -v`
Ожидается: FAIL, `ModuleNotFoundError: No module named 'src.alerts'`

- [ ] **Step 3: Реализовать `src/alerts.py`**

```python
"""Подтверждение кандидатов через Amadeus и текст алерта.

Кэш Aviasales — это чужие поиски за последние двое суток. Прежде чем будить
пользователя словом BUY, спрашиваем у GDS realtime-цену. Не подтвердилось —
всё равно сообщаем, но другим уровнем: по этим случаям калибруются пороги.
"""

from __future__ import annotations

import html
import logging
import sqlite3
from dataclasses import dataclass
from typing import Optional

import requests

from src import airlines, quota, rules
from src.digest import format_day, format_transfers
from src.sources import amadeus

LOG = logging.getLogger(__name__)

LEVEL_BUY = "buy"
LEVEL_UNCONFIRMED = "unconfirmed"

CONFIRM_TOLERANCE = 1.1  # подтверждённая цена ≤ кандидат × 1.1 → BUY


@dataclass(frozen=True)
class ConfirmResult:
    candidate: rules.Candidate
    level: str
    confirmed_usd: Optional[float]
    note: str


def confirm(
    conn: sqlite3.Connection,
    session: requests.Session,
    key: str,
    secret: str,
    candidate: rules.Candidate,
    now: str,
    monthly_limit: int = quota.MONTHLY_LIMIT,
) -> ConfirmResult:
    try:
        with quota.guard(conn, "amadeus", now, monthly_limit=monthly_limit, purpose="confirm"):
            token = amadeus.get_token(session, key, secret)
            offers = amadeus.search_offers(
                session,
                token,
                candidate.origin,
                candidate.destination,
                candidate.depart_date,
                candidate.return_date,
            )
    except quota.QuotaExceeded as exc:
        LOG.warning("подтверждение пропущено: %s", exc)
        return ConfirmResult(candidate, LEVEL_UNCONFIRMED, None, f"квота Amadeus: {exc}")
    except amadeus.AmadeusError as exc:
        LOG.warning("Amadeus не ответил: %s", exc)
        return ConfirmResult(candidate, LEVEL_UNCONFIRMED, None, str(exc))

    best = amadeus.cheapest(offers)
    if best is None:
        return ConfirmResult(
            candidate, LEVEL_UNCONFIRMED, None, "Amadeus не нашёл тариф на эти даты"
        )

    level = (
        LEVEL_BUY
        if best.price_local <= candidate.landed_usd * CONFIRM_TOLERANCE
        else LEVEL_UNCONFIRMED
    )
    return ConfirmResult(candidate, level, best.price_local, "")


def render_alert(result: ConfirmResult) -> str:
    candidate = result.candidate
    route = f"{candidate.origin}→{candidate.destination}"
    dates = format_day(candidate.depart_date)
    if candidate.return_date:
        dates += f" → {format_day(candidate.return_date)} ({candidate.nights_text})"
    details = ", ".join(
        part
        for part in (html.escape(airlines.name(candidate.airline)), format_transfers(candidate.transfers))
        if part
    )

    if result.level == LEVEL_BUY:
        head = f"🟢 <b>BUY</b> · {route} <b>${candidate.landed_usd:.0f}</b>"
    else:
        head = f"🔍 {route} <b>${candidate.landed_usd:.0f}</b> — кэш видел, но не подтвердилось"

    lines = [head, f"{dates} · {details} · рынок {candidate.market}"]

    if result.confirmed_usd is not None:
        lines.append(f"Amadeus сейчас: <b>${result.confirmed_usd:.0f}</b>")
    elif result.note:
        lines.append(f"Amadeus: {html.escape(result.note)}")

    lines.append(
        f"медиана рынка {candidate.baseline.median:.0f} {candidate.currency}, "
        f"p10 {candidate.baseline.anomaly_threshold:.0f} (n={candidate.baseline.n})"
    )
    if candidate.search_url:
        lines.append(f'<a href="{html.escape(candidate.search_url, quote=True)}">открыть поиск</a>')
    if result.level == LEVEL_BUY:
        lines.append("<i>Купил — /bought. Цена разошлась — /mismatch.</i>")
    return "\n".join(lines)
```

- [ ] **Step 4: Добавить `nights_text` в `rules.Candidate`**

```python
# --- в src/rules.py, внутрь класса Candidate, после route_date_key ---

    @property
    def nights_text(self) -> str:
        if not self.return_date:
            return "в один конец"
        from datetime import date

        nights = (
            date.fromisoformat(self.return_date) - date.fromisoformat(self.depart_date)
        ).days
        if nights % 10 == 1 and nights % 100 != 11:
            return f"{nights} ночь"
        if nights % 10 in (2, 3, 4) and nights % 100 not in (12, 13, 14):
            return f"{nights} ночи"
        return f"{nights} ночей"
```

- [ ] **Step 5: Запустить тесты**

Run: `.venv/bin/pytest tests/test_alerts.py -v`
Ожидается: 10 passed

- [ ] **Step 6: Коммит**

```bash
git add src/alerts.py src/rules.py tests/test_alerts.py
git commit -m "feat: amadeus confirmation and alert rendering"
```

---

### Task 6: Алерты внутри скана

**Files:**
- Modify: `src/runner.py`
- Test: `tests/test_runner_alerts.py`

- [ ] **Step 1: Написать падающий тест**

```python
# tests/test_runner_alerts.py
import json

import pytest
import requests
import responses

from src import runner, store
from src.models import PriceRecord
from src.sources import amadeus

PRICES_URL = "https://api.travelpayouts.com/aviasales/v3/prices_for_dates"
FX_URL = "https://open.er-api.com/v6/latest/USD"
TG_URL = "https://api.telegram.org/botTG/sendMessage"
TOKEN_URL = f"{amadeus.BASE_URL}/v1/security/oauth2/token"
SEARCH_URL = f"{amadeus.BASE_URL}/v2/shopping/flight-offers"

FX_PAYLOAD = {"result": "success", "rates": {"USD": 1.0, "KGS": 87.5, "RUB": 92.0}}


def cache_payload(price):
    return {
        "success": True,
        "data": [
            {
                "origin": "FRU",
                "destination": "HKT",
                "price": price,
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


def offers(price):
    return {
        "data": [
            {
                "price": {"grandTotal": f"{price:.2f}", "currency": "USD"},
                "validatingAirlineCodes": ["KC"],
                "itineraries": [
                    {"duration": "PT18H30M", "segments": [{"carrierCode": "KC"}]}
                ],
            }
        ]
    }


@pytest.fixture()
def env(monkeypatch):
    monkeypatch.setenv("TP_TOKEN", "TP")
    monkeypatch.setenv("TG_BOT_TOKEN", "TG")
    monkeypatch.setenv("TG_CHAT_ID", "999")
    monkeypatch.setenv("AMADEUS_KEY", "KEY")
    monkeypatch.setenv("AMADEUS_SECRET", "SECRET")


@pytest.fixture()
def db(tmp_path, env):
    path = tmp_path / "history.sqlite"
    conn = store.connect(path)
    store.init_schema(conn)
    # История: 6 записей около 30000 сом, чтобы p10 был около 24000
    for index, price in enumerate([30000, 31000, 32000, 33000, 34000, 35000]):
        store.insert_prices(
            conn,
            [
                PriceRecord(
                    source="aviasales_cache",
                    origin="FRU",
                    destination="HKT",
                    market="kg",
                    depart_date="2026-10-12",
                    return_date="2026-10-24",
                    price_local=price,
                    currency="kgs",
                    airline="KC",
                    transfers=1,
                    duration_min=1500,
                    duration_to_min=760,
                    search_url="https://x",
                    fx_rate=1 / 87.5,
                    landed_usd=price / 87.5,
                )
            ],
            now=f"2026-07-2{index}T00:00:00Z",
        )
    conn.close()
    return path


@responses.activate
def test_scan_sends_buy_alert_for_confirmed_anomaly(db, config_stub):
    responses.add(responses.GET, FX_URL, json=FX_PAYLOAD, status=200)
    responses.add(responses.GET, PRICES_URL, json=cache_payload(20000), status=200)
    responses.add(responses.POST, TOKEN_URL, json={"access_token": "AT"}, status=200)
    responses.add(responses.GET, SEARCH_URL, json=offers(240.0), status=200)
    responses.add(responses.POST, TG_URL, json={"ok": True}, status=200)

    result = runner.run_scan(config_stub, db_path=db, now="2026-08-01T06:00:00Z")
    assert result["alerts"] == 1
    sent = [c for c in responses.calls if c.request.url.startswith(TG_URL)]
    assert "BUY" in json.loads(sent[0].request.body)["text"]


@responses.activate
def test_repeated_scan_does_not_resend_same_alert(db, config_stub):
    responses.add(responses.GET, FX_URL, json=FX_PAYLOAD, status=200)
    responses.add(responses.GET, PRICES_URL, json=cache_payload(20000), status=200)
    responses.add(responses.POST, TOKEN_URL, json={"access_token": "AT"}, status=200)
    responses.add(responses.GET, SEARCH_URL, json=offers(240.0), status=200)
    responses.add(responses.POST, TG_URL, json={"ok": True}, status=200)

    runner.run_scan(config_stub, db_path=db, now="2026-08-01T06:00:00Z")
    second = runner.run_scan(config_stub, db_path=db, now="2026-08-01T10:00:00Z")
    assert second["alerts"] == 0


@responses.activate
def test_paused_state_suppresses_alerts(db, config_stub):
    conn = store.connect(db)
    store.set_meta(conn, "paused", "1")
    conn.close()
    responses.add(responses.GET, FX_URL, json=FX_PAYLOAD, status=200)
    responses.add(responses.GET, PRICES_URL, json=cache_payload(20000), status=200)
    responses.add(responses.POST, TG_URL, json={"ok": True}, status=200)

    result = runner.run_scan(config_stub, db_path=db, now="2026-08-01T06:00:00Z")
    assert result["alerts"] == 0
    assert not [c for c in responses.calls if c.request.url.startswith(TG_URL)]


@responses.activate
def test_scan_without_amadeus_credentials_still_works(db, config_stub, monkeypatch):
    monkeypatch.delenv("AMADEUS_KEY", raising=False)
    responses.add(responses.GET, FX_URL, json=FX_PAYLOAD, status=200)
    responses.add(responses.GET, PRICES_URL, json=cache_payload(20000), status=200)
    responses.add(responses.POST, TG_URL, json={"ok": True}, status=200)
    result = runner.run_scan(config_stub, db_path=db, now="2026-08-01T06:00:00Z")
    assert result["alerts"] == 1  # алерт уходит, но помеченный как неподтверждённый
    text = json.loads(
        [c for c in responses.calls if c.request.url.startswith(TG_URL)][0].request.body
    )["text"]
    assert "не подтвердилось" in text


@responses.activate
def test_alert_is_recorded_for_dedup_and_feedback(db, config_stub):
    responses.add(responses.GET, FX_URL, json=FX_PAYLOAD, status=200)
    responses.add(responses.GET, PRICES_URL, json=cache_payload(20000), status=200)
    responses.add(responses.POST, TOKEN_URL, json={"access_token": "AT"}, status=200)
    responses.add(responses.GET, SEARCH_URL, json=offers(240.0), status=200)
    responses.add(responses.POST, TG_URL, json={"ok": True}, status=200)

    runner.run_scan(config_stub, db_path=db, now="2026-08-01T06:00:00Z")
    conn = store.connect(db)
    row = store.last_alert(conn)
    assert row["level"] == "buy"
    assert row["route_date_key"] == "FRU-HKT-2026-10-12-2026-10-24"
```

- [ ] **Step 2: Запустить тест, убедиться в падении**

Run: `.venv/bin/pytest tests/test_runner_alerts.py -v`
Ожидается: FAIL, `KeyError: 'alerts'`

- [ ] **Step 3: Расширить `src/runner.py`**

Добавить импорты в шапку файла:

```python
from src import alerts, digest, fx, notify, quota, rules, store
from src.sources import amadeus, aviasales
```

Добавить константу рядом с `RETENTION_DAYS`:

```python
ALERT_DROP_RATIO = 0.05  # повторный алерт только при падении ещё на 5%
```

Дописать функцию перед `run_scan`:

```python
def _process_alerts(conn, cfg, session, now: str) -> int:
    """Ищет аномалии, подтверждает их и шлёт алерты. Возвращает число отправленных."""
    if store.get_meta(conn, "paused") == "1":
        LOG.info("алерты на паузе")
        return 0

    bot_token = os.environ.get("TG_BOT_TOKEN")
    chat_id = os.environ.get("TG_CHAT_ID")
    if not bot_token or not chat_id:
        LOG.warning("нет TG_BOT_TOKEN/TG_CHAT_ID — алерты не отправляются")
        return 0

    key = os.environ.get("AMADEUS_KEY")
    secret = os.environ.get("AMADEUS_SECRET")
    sent = 0

    for candidate in rules.find_candidates(conn, cfg, now=now):
        if not store.should_alert(
            conn, candidate.route_date_key, candidate.landed_usd, ALERT_DROP_RATIO
        ):
            continue
        if key and secret:
            result = alerts.confirm(conn, session, key, secret, candidate, now=now)
        else:
            result = alerts.ConfirmResult(
                candidate, alerts.LEVEL_UNCONFIRMED, None, "ключи Amadeus не заданы"
            )
        try:
            notify.send_message(session, bot_token, chat_id, alerts.render_alert(result))
        except notify.TelegramError as exc:
            LOG.error("алерт не ушёл: %s", exc)
            continue
        store.record_alert(conn, candidate.route_date_key, candidate.landed_usd, now, result.level)
        sent += 1
    return sent
```

Заменить хвост `run_scan` (от `if records:` до `return`) на:

```python
    if records:
        store.set_meta(conn, "last_scan_at", now)
    store.set_meta(conn, "last_scan_errors", str(len(errors)))

    alerts_sent = _process_alerts(conn, cfg, session, now)

    removed = store.prune(conn, before=store.shift_days(now, -RETENTION_DAYS))
    conn.close()

    LOG.info(
        "скан: получено %d, вставлено %d, алертов %d, ошибок %d",
        len(records),
        inserted,
        alerts_sent,
        len(errors),
    )
    return {
        "fetched": len(records),
        "inserted": inserted,
        "alerts": alerts_sent,
        "pruned": removed,
        "errors": errors,
    }
```

- [ ] **Step 4: Запустить тесты**

Run: `.venv/bin/pytest tests/test_runner_alerts.py tests/test_runner.py -v`
Ожидается: 13 passed

- [ ] **Step 5: Коммит**

```bash
git add src/runner.py tests/test_runner_alerts.py
git commit -m "feat: buy alerts inside scan with dedup and pause support"
```

---

### Task 7: Fallback-скан Amadeus при тонком кэше

**Files:**
- Modify: `src/store.py`, `src/runner.py`
- Test: `tests/test_fallback.py`

- [ ] **Step 1: Написать падающий тест**

```python
# tests/test_fallback.py
import pytest
import requests
import responses

from src import quota, runner, store
from src.models import PriceRecord
from src.sources import amadeus

TOKEN_URL = f"{amadeus.BASE_URL}/v1/security/oauth2/token"
SEARCH_URL = f"{amadeus.BASE_URL}/v2/shopping/flight-offers"


@pytest.fixture()
def conn(tmp_path):
    connection = store.connect(tmp_path / "history.sqlite")
    store.init_schema(connection)
    return connection


@pytest.fixture()
def session():
    return requests.Session()


def thin(conn, count, route=("FRU", "HKT")):
    store.insert_prices(
        conn,
        [
            PriceRecord(
                source="aviasales_cache",
                origin=route[0],
                destination=route[1],
                market="kg",
                depart_date="2026-10-12",
                return_date="2026-10-24",
                price_local=30000.0 + index,
                currency="kgs",
                airline="KC",
                transfers=1,
                duration_min=1500,
                fx_rate=1 / 87.5,
                landed_usd=(30000.0 + index) / 87.5,
            )
            for index in range(count)
        ],
        now="2026-08-01T00:00:00Z",
    )


def test_thin_routes_lists_routes_with_few_fresh_rows(conn, config_stub):
    thin(conn, 2, route=("FRU", "HKT"))
    thin(conn, 8, route=("ALA", "HKT"))
    routes = runner.thin_routes(conn, config_stub, now="2026-08-01T06:00:00Z", minimum=5)
    assert ("FRU", "HKT") in routes
    assert ("ALA", "HKT") not in routes
    assert ("FRU", "DPS") in routes  # пустой маршрут тоже тонкий


def test_thin_counter_increments_and_resets(conn):
    assert runner.bump_thin_streak(conn, ("FRU", "HKT"), thin=True) == 1
    assert runner.bump_thin_streak(conn, ("FRU", "HKT"), thin=True) == 2
    assert runner.bump_thin_streak(conn, ("FRU", "HKT"), thin=False) == 0
    assert runner.bump_thin_streak(conn, ("FRU", "HKT"), thin=True) == 1


@responses.activate
def test_fallback_runs_after_three_thin_scans(conn, session, config_stub, monkeypatch):
    monkeypatch.setenv("AMADEUS_KEY", "KEY")
    monkeypatch.setenv("AMADEUS_SECRET", "SECRET")
    responses.add(responses.POST, TOKEN_URL, json={"access_token": "AT"}, status=200)
    responses.add(
        responses.GET,
        SEARCH_URL,
        json={
            "data": [
                {
                    "price": {"grandTotal": "410.00", "currency": "USD"},
                    "validatingAirlineCodes": ["KC"],
                    "itineraries": [{"duration": "PT18H", "segments": [{"carrierCode": "KC"}]}],
                }
            ]
        },
        status=200,
    )
    store.set_meta(conn, "thin_streak_FRU-HKT", "2")
    inserted = runner.run_fallback(
        conn, config_stub, session, [("FRU", "HKT")], now="2026-08-01T06:00:00Z"
    )
    assert inserted > 0
    row = conn.execute("SELECT * FROM price_history WHERE source='amadeus'").fetchone()
    assert row["landed_usd"] == 410.0
    assert row["currency"] == "usd"


@responses.activate
def test_fallback_respects_daily_budget(conn, session, config_stub, monkeypatch):
    monkeypatch.setenv("AMADEUS_KEY", "KEY")
    monkeypatch.setenv("AMADEUS_SECRET", "SECRET")
    for index in range(quota.FALLBACK_DAILY_LIMIT):
        quota.record_call(conn, "amadeus", f"2026-08-01T0{index % 10}:00:00Z", purpose="fallback")
    store.set_meta(conn, "thin_streak_FRU-HKT", "3")
    inserted = runner.run_fallback(
        conn, config_stub, session, [("FRU", "HKT")], now="2026-08-01T12:00:00Z"
    )
    assert inserted == 0
    assert not responses.calls


def test_fallback_without_credentials_is_noop(conn, session, config_stub, monkeypatch):
    monkeypatch.delenv("AMADEUS_KEY", raising=False)
    store.set_meta(conn, "thin_streak_FRU-HKT", "3")
    assert runner.run_fallback(
        conn, config_stub, session, [("FRU", "HKT")], now="2026-08-01T06:00:00Z"
    ) == 0
```

- [ ] **Step 2: Запустить тест, убедиться в падении**

Run: `.venv/bin/pytest tests/test_fallback.py -v`
Ожидается: FAIL, `AttributeError: module 'src.runner' has no attribute 'thin_routes'`

- [ ] **Step 3: Дописать в `src/runner.py`**

```python
# --- дописать в src/runner.py, перед run_scan ---

THIN_CACHE_MINIMUM = 5
THIN_STREAK_TRIGGER = 3
FALLBACK_DATES_PER_ROUTE = 3


def thin_routes(conn, cfg, now: str, minimum: int = THIN_CACHE_MINIMUM) -> list[tuple[str, str]]:
    """Маршруты, по которым свежих записей кэша меньше порога."""
    thin = []
    for origin, destination in cfg.routes():
        rows = store.fresh_prices(
            conn, origin, destination, now=now, ttl_hours=cfg.cache_ttl_hours
        )
        if len(rows) < minimum:
            thin.append((origin, destination))
    return thin


def bump_thin_streak(conn, route: tuple[str, str], thin: bool) -> int:
    key = f"thin_streak_{route[0]}-{route[1]}"
    if not thin:
        store.set_meta(conn, key, "0")
        return 0
    streak = int(store.get_meta(conn, key, "0") or 0) + 1
    store.set_meta(conn, key, str(streak))
    return streak


def _fallback_dates(cfg) -> list[tuple[str, Optional[str]]]:
    """Несколько представительных дат месяца: начало, середина, конец."""
    year, month = (int(part) for part in cfg.departure_month.split("-"))
    nights = cfg.nights_range[0]
    from calendar import monthrange
    from datetime import date, timedelta

    last_day = monthrange(year, month)[1]
    days = [5, 15, min(25, last_day)][:FALLBACK_DATES_PER_ROUTE]
    dates = []
    for day in days:
        depart = date(year, month, day)
        return_date = None if cfg.one_way else (depart + timedelta(days=nights)).isoformat()
        dates.append((depart.isoformat(), return_date))
    return dates


def run_fallback(conn, cfg, session, routes, now: str) -> int:
    """Прямой скан Amadeus по маршрутам с систематически пустым кэшем."""
    key = os.environ.get("AMADEUS_KEY")
    secret = os.environ.get("AMADEUS_SECRET")
    if not key or not secret:
        LOG.warning("fallback пропущен: нет ключей Amadeus")
        return 0

    collected = []
    for origin, destination in routes:
        streak = int(store.get_meta(conn, f"thin_streak_{origin}-{destination}", "0") or 0)
        if streak < THIN_STREAK_TRIGGER:
            continue
        for depart_date, return_date in _fallback_dates(cfg):
            try:
                with quota.guard(
                    conn,
                    "amadeus",
                    now,
                    daily_limit=quota.FALLBACK_DAILY_LIMIT,
                    purpose="fallback",
                ):
                    token = amadeus.get_token(session, key, secret)
                    offers = amadeus.search_offers(
                        session, token, origin, destination, depart_date, return_date
                    )
            except quota.QuotaExceeded as exc:
                LOG.warning("fallback остановлен: %s", exc)
                return store.insert_prices(conn, collected, now=now) if collected else 0
            except amadeus.AmadeusError as exc:
                LOG.warning("fallback %s->%s: %s", origin, destination, exc)
                continue
            collected.extend(
                record.with_landed(1.0, record.price_local) for record in offers
            )

    if not collected:
        return 0
    return store.insert_prices(conn, collected, now=now)
```

Вставить вызов в `run_scan` — сразу после `store.set_meta(conn, "last_scan_errors", ...)`:

```python
    thin = thin_routes(conn, cfg, now=now)
    for route in cfg.routes():
        bump_thin_streak(conn, route, thin=route in thin)
    fallback_inserted = run_fallback(conn, cfg, session, thin, now=now)
```

И добавить `"fallback_inserted": fallback_inserted` в возвращаемый словарь.

- [ ] **Step 4: Запустить тесты**

Run: `.venv/bin/pytest tests/test_fallback.py tests/test_runner_alerts.py -v`
Ожидается: 11 passed

- [ ] **Step 5: Коммит**

```bash
git add src/runner.py tests/test_fallback.py
git commit -m "feat: amadeus fallback scan for routes with thin cache"
```

---

### Task 8: Кросс-рыночный арбитраж

**Files:**
- Create: `src/arbitrage.py`
- Test: `tests/test_arbitrage.py`

- [ ] **Step 1: Написать падающий тест**

```python
# tests/test_arbitrage.py
import pytest

from src import arbitrage, store
from src.models import PriceRecord


@pytest.fixture()
def conn(tmp_path):
    connection = store.connect(tmp_path / "history.sqlite")
    store.init_schema(connection)
    return connection


def record(market, landed, airline="KC", transfers=1, depart="2026-10-12", currency=None):
    return PriceRecord(
        source="aviasales_cache",
        origin="FRU",
        destination="HKT",
        market=market,
        depart_date=depart,
        return_date="2026-10-24",
        price_local=landed * 87.5,
        currency=currency or ("kgs" if market == "kg" else "rub"),
        airline=airline,
        transfers=transfers,
        duration_min=1500,
        search_url=f"https://www.aviasales.{market}/search/x",
        fx_rate=1 / 87.5,
        landed_usd=landed,
    )


def test_finds_cheaper_equivalent_on_other_market(conn, config_stub):
    store.insert_prices(
        conn, [record("kg", 268.0), record("ru", 239.0)], now="2026-08-01T06:00:00Z"
    )
    found = arbitrage.find(conn, config_stub, now="2026-08-01T07:00:00Z")
    assert len(found) == 1
    assert found[0].expensive_market == "kg"
    assert found[0].cheap_market == "ru"
    assert found[0].saving_ratio == pytest.approx((268 - 239) / 268)


def test_ignores_difference_below_threshold(conn, config_stub):
    store.insert_prices(
        conn, [record("kg", 268.0), record("ru", 260.0)], now="2026-08-01T06:00:00Z"
    )
    assert arbitrage.find(conn, config_stub, now="2026-08-01T07:00:00Z") == []


def test_different_airline_is_not_the_same_flight(conn, config_stub):
    store.insert_prices(
        conn,
        [record("kg", 268.0, airline="KC"), record("ru", 200.0, airline="TK")],
        now="2026-08-01T06:00:00Z",
    )
    assert arbitrage.find(conn, config_stub, now="2026-08-01T07:00:00Z") == []


def test_different_transfer_count_is_not_the_same_flight(conn, config_stub):
    store.insert_prices(
        conn,
        [record("kg", 268.0, transfers=1), record("ru", 200.0, transfers=2)],
        now="2026-08-01T06:00:00Z",
    )
    assert arbitrage.find(conn, config_stub, now="2026-08-01T07:00:00Z") == []


def test_different_date_is_not_the_same_flight(conn, config_stub):
    store.insert_prices(
        conn,
        [record("kg", 268.0, depart="2026-10-12"), record("ru", 200.0, depart="2026-10-14")],
        now="2026-08-01T06:00:00Z",
    )
    assert arbitrage.find(conn, config_stub, now="2026-08-01T07:00:00Z") == []


def test_stale_rows_are_ignored(conn, config_stub):
    store.insert_prices(conn, [record("kg", 268.0)], now="2026-08-01T06:00:00Z")
    store.insert_prices(conn, [record("ru", 239.0)], now="2026-07-20T06:00:00Z")
    assert arbitrage.find(conn, config_stub, now="2026-08-01T07:00:00Z") == []


def test_results_sorted_by_saving(conn, config_stub):
    store.insert_prices(
        conn,
        [
            record("kg", 268.0),
            record("ru", 239.0),
            record("kg", 400.0, depart="2026-10-14"),
            record("ru", 300.0, depart="2026-10-14"),
        ],
        now="2026-08-01T06:00:00Z",
    )
    found = arbitrage.find(conn, config_stub, now="2026-08-01T07:00:00Z")
    assert [round(f.saving_ratio, 2) for f in found] == [0.25, 0.11]


def test_render_line_matches_spec_format(config_stub):
    finding = arbitrage.Finding(
        origin="FRU",
        destination="HKT",
        depart_date="2026-10-12",
        return_date="2026-10-24",
        airline="KC",
        transfers=1,
        expensive_market="kg",
        expensive_usd=268.0,
        cheap_market="ru",
        cheap_usd=239.0,
        cheap_url="https://www.aviasales.ru/search/x",
    )
    line = arbitrage.render_line(finding)
    assert "$268 (kg)" in line
    assert "$239 landed (ru)" in line
    assert "🔄" in line
    assert "−11%" in line
    assert "≈ тот же рейс" in line
```

- [ ] **Step 2: Запустить тест, убедиться в падении**

Run: `.venv/bin/pytest tests/test_arbitrage.py -v`
Ожидается: FAIL, `ModuleNotFoundError: No module named 'src.arbitrage'`

- [ ] **Step 3: Реализовать `src/arbitrage.py`**

```python
"""Кросс-рыночный арбитраж.

Кэш не отдаёт ID рейса, поэтому «тот же билет» определяется приближённо:
маршрут + даты + авиакомпания + число пересадок. В сообщении это честно
помечается как «≈ тот же рейс». Сравнение — по landed_usd, где надбавка за
оплату иновалютного тарифа уже учтена, так что порог покрывает только шум кэша.
"""

from __future__ import annotations

import html
import sqlite3
from dataclasses import dataclass
from typing import Optional

from src import airlines
from src.digest import format_day


@dataclass(frozen=True)
class Finding:
    origin: str
    destination: str
    depart_date: str
    return_date: Optional[str]
    airline: Optional[str]
    transfers: Optional[int]
    expensive_market: str
    expensive_usd: float
    cheap_market: str
    cheap_usd: float
    cheap_url: Optional[str]

    @property
    def saving_ratio(self) -> float:
        if self.expensive_usd == 0:
            return 0.0
        return (self.expensive_usd - self.cheap_usd) / self.expensive_usd


def _flight_key(row) -> tuple:
    return (
        row["depart_date"],
        row["return_date"] or "",
        (row["airline"] or "").upper(),
        row["transfers"] if row["transfers"] is not None else -1,
    )


def find(conn: sqlite3.Connection, cfg, now: str) -> list[Finding]:
    from src import store

    findings: list[Finding] = []
    for origin, destination in cfg.routes():
        by_market: dict[str, dict[tuple, object]] = {}
        for market in cfg.markets:
            cheapest: dict[tuple, object] = {}
            for row in store.fresh_prices(
                conn, origin, destination, now=now, ttl_hours=cfg.cache_ttl_hours, market=market
            ):
                if row["landed_usd"] is None:
                    continue
                key = _flight_key(row)
                current = cheapest.get(key)
                if current is None or row["landed_usd"] < current["landed_usd"]:
                    cheapest[key] = row
            by_market[market] = cheapest

        shared_keys = set.intersection(*(set(m) for m in by_market.values())) if by_market else set()
        for key in shared_keys:
            rows = [(market, by_market[market][key]) for market in cfg.markets]
            cheap_market, cheap = min(rows, key=lambda pair: pair[1]["landed_usd"])
            expensive_market, expensive = max(rows, key=lambda pair: pair[1]["landed_usd"])
            if expensive["landed_usd"] <= 0:
                continue
            saving = (expensive["landed_usd"] - cheap["landed_usd"]) / expensive["landed_usd"]
            if saving <= cfg.cross_market_delta:
                continue
            findings.append(
                Finding(
                    origin=origin,
                    destination=destination,
                    depart_date=cheap["depart_date"],
                    return_date=cheap["return_date"],
                    airline=cheap["airline"],
                    transfers=cheap["transfers"],
                    expensive_market=expensive_market,
                    expensive_usd=float(expensive["landed_usd"]),
                    cheap_market=cheap_market,
                    cheap_usd=float(cheap["landed_usd"]),
                    cheap_url=cheap["search_url"],
                )
            )
    return sorted(findings, key=lambda f: f.saving_ratio, reverse=True)


def render_line(finding: Finding) -> str:
    percent = round(finding.saving_ratio * 100)
    route = f"{finding.origin}→{finding.destination}"
    line = (
        f"{route} {format_day(finding.depart_date)} "
        f"{html.escape(airlines.name(finding.airline))}: "
        f"${finding.expensive_usd:.0f} ({finding.expensive_market}) / "
        f"<b>${finding.cheap_usd:.0f} landed ({finding.cheap_market})</b> "
        f"🔄 арбитраж −{percent}% <i>(≈ тот же рейс)</i>"
    )
    if finding.cheap_url:
        line += f' <a href="{html.escape(finding.cheap_url, quote=True)}">поиск</a>'
    return line
```

- [ ] **Step 4: Запустить тесты**

Run: `.venv/bin/pytest tests/test_arbitrage.py -v`
Ожидается: 8 passed

- [ ] **Step 5: Коммит**

```bash
git add src/arbitrage.py tests/test_arbitrage.py
git commit -m "feat: cross-market arbitrage detection"
```

---

### Task 9: Тренды и поправка на день недели

**Files:**
- Modify: `src/rules.py`
- Test: `tests/test_rules_trend.py`

- [ ] **Step 1: Написать падающий тест**

```python
# tests/test_rules_trend.py
import pytest

from src import rules, store
from src.models import PriceRecord


@pytest.fixture()
def conn(tmp_path):
    connection = store.connect(tmp_path / "history.sqlite")
    store.init_schema(connection)
    return connection


def seed(conn, day_to_price, depart="2026-10-12"):
    for day, price in day_to_price.items():
        store.insert_prices(
            conn,
            [
                PriceRecord(
                    source="aviasales_cache",
                    origin="FRU",
                    destination="HKT",
                    market="kg",
                    depart_date=depart,
                    return_date="2026-10-24",
                    price_local=price * 87.5,
                    currency="kgs",
                    airline="KC",
                    transfers=1,
                    duration_min=1500,
                    fx_rate=1 / 87.5,
                    landed_usd=float(price),
                )
            ],
            now=f"{day}T06:00:00Z",
        )


def test_daily_minimums_are_grouped_by_day(conn):
    seed(conn, {"2026-07-30": 320, "2026-07-31": 310})
    daily = rules.daily_minimums(conn, "FRU", "HKT", since="2026-07-01T00:00:00Z")
    assert daily == [("2026-07-30", 320.0), ("2026-07-31", 310.0)]


def test_falling_streak_counts_consecutive_declines(conn):
    seed(conn, {"2026-07-29": 340, "2026-07-30": 330, "2026-07-31": 320, "2026-08-01": 310})
    daily = rules.daily_minimums(conn, "FRU", "HKT", since="2026-07-01T00:00:00Z")
    assert rules.falling_streak(daily) == 3


def test_streak_is_zero_when_price_rose_today(conn):
    seed(conn, {"2026-07-30": 320, "2026-07-31": 310, "2026-08-01": 330})
    daily = rules.daily_minimums(conn, "FRU", "HKT", since="2026-07-01T00:00:00Z")
    assert rules.falling_streak(daily) == 0


def test_streak_needs_at_least_two_days():
    assert rules.falling_streak([("2026-08-01", 300.0)]) == 0
    assert rules.falling_streak([]) == 0


def test_week_delta_compares_last_week_to_previous(conn):
    seed(conn, {f"2026-07-{day:02d}": 400 for day in range(18, 25)})
    seed(conn, {f"2026-07-{day:02d}": 360 for day in range(25, 32)})
    delta = rules.week_delta(conn, "FRU", "HKT", now="2026-07-31T23:00:00Z")
    assert delta == pytest.approx(-0.10)


def test_week_delta_is_none_without_previous_week(conn):
    seed(conn, {"2026-07-30": 320})
    assert rules.week_delta(conn, "FRU", "HKT", now="2026-07-31T00:00:00Z") is None


def test_dow_factor_is_one_without_enough_data(conn):
    seed(conn, {"2026-07-30": 320}, depart="2026-10-12")
    assert rules.dow_factor(conn, "FRU", "HKT", "2026-10-12", now="2026-08-01T00:00:00Z") == 1.0


def test_dow_factor_reflects_cheaper_weekday(conn):
    # 2026-10-12 — понедельник, 2026-10-17 — суббота
    for index in range(6):
        seed(conn, {f"2026-07-{20 + index:02d}": 300}, depart="2026-10-12")
    for index in range(6):
        seed(conn, {f"2026-07-{20 + index:02d}": 400}, depart="2026-10-17")
    factor = rules.dow_factor(conn, "FRU", "HKT", "2026-10-12", now="2026-08-01T00:00:00Z")
    assert factor < 1.0
```

- [ ] **Step 2: Запустить тест, убедиться в падении**

Run: `.venv/bin/pytest tests/test_rules_trend.py -v`
Ожидается: FAIL, `AttributeError: module 'src.rules' has no attribute 'daily_minimums'`

- [ ] **Step 3: Дописать в `src/rules.py`**

```python
# --- дописать в конец src/rules.py ---

from datetime import date as _date  # noqa: E402


def daily_minimums(
    conn: sqlite3.Connection, origin: str, destination: str, since: str
) -> list[tuple[str, float]]:
    """Минимальная landed-цена по каждому дню наблюдения."""
    rows = conn.execute(
        "SELECT substr(last_seen_at, 1, 10) AS day, MIN(landed_usd) AS best"
        " FROM price_history"
        " WHERE origin = ? AND destination = ? AND last_seen_at >= ? AND landed_usd IS NOT NULL"
        " GROUP BY day ORDER BY day",
        (origin, destination, since),
    ).fetchall()
    return [(row["day"], float(row["best"])) for row in rows]


def falling_streak(daily: Sequence[tuple[str, float]]) -> int:
    """Сколько дней подряд минимум снижается, считая от последнего дня."""
    streak = 0
    for index in range(len(daily) - 1, 0, -1):
        if daily[index][1] < daily[index - 1][1]:
            streak += 1
        else:
            break
    return streak


def week_delta(
    conn: sqlite3.Connection, origin: str, destination: str, now: str
) -> Optional[float]:
    """Относительная разница минимума последних 7 дней к предыдущим 7."""
    daily = dict(daily_minimums(conn, origin, destination, since=store.shift_days(now, -14)))
    today = now[:10]
    week_ago = store.shift_days(now, -7)[:10]
    two_weeks_ago = store.shift_days(now, -14)[:10]

    recent = [price for day, price in daily.items() if week_ago < day <= today]
    previous = [price for day, price in daily.items() if two_weeks_ago < day <= week_ago]
    if not recent or not previous:
        return None
    return min(recent) / min(previous) - 1.0


def dow_factor(
    conn: sqlite3.Connection,
    origin: str,
    destination: str,
    depart_date: str,
    now: str,
    min_sample: int = MIN_SAMPLE,
) -> float:
    """Множитель к baseline: насколько этот день недели дешевле среднего.

    Возвращает 1.0, пока данных по дню недели недостаточно.
    """
    since = store.shift_days(now, -60)
    rows = conn.execute(
        "SELECT depart_date, landed_usd FROM price_history"
        " WHERE origin = ? AND destination = ? AND last_seen_at >= ? AND landed_usd IS NOT NULL",
        (origin, destination, since),
    ).fetchall()
    if len(rows) < min_sample:
        return 1.0

    target_dow = _date.fromisoformat(depart_date).weekday()
    same_dow = [
        float(row["landed_usd"])
        for row in rows
        if _date.fromisoformat(row["depart_date"]).weekday() == target_dow
    ]
    if len(same_dow) < min_sample:
        return 1.0
    overall = _median([float(row["landed_usd"]) for row in rows])
    if overall == 0:
        return 1.0
    return float(_median(same_dow)) / overall
```

- [ ] **Step 4: Запустить тесты**

Run: `.venv/bin/pytest tests/test_rules_trend.py -v`
Ожидается: 8 passed

- [ ] **Step 5: Коммит**

```bash
git add src/rules.py tests/test_rules_trend.py
git commit -m "feat: weekly trend, falling streak and day-of-week factor"
```

---

### Task 10: Дайджест v1.1 — dead man's switch, тренды, арбитраж, еженедельный блок

**Files:**
- Modify: `src/digest.py`
- Test: `tests/test_digest_v11.py`

- [ ] **Step 1: Написать падающий тест**

```python
# tests/test_digest_v11.py
import pytest

from src import digest, rules, store
from src.models import PriceRecord


@pytest.fixture()
def conn(tmp_path):
    connection = store.connect(tmp_path / "history.sqlite")
    store.init_schema(connection)
    return connection


def seed(conn, prices, now, market="kg", destination="HKT", airline="KC"):
    store.insert_prices(
        conn,
        [
            PriceRecord(
                source="aviasales_cache",
                origin="FRU",
                destination=destination,
                market=market,
                depart_date="2026-10-12",
                return_date="2026-10-24",
                price_local=price * 87.5,
                currency="kgs" if market == "kg" else "rub",
                airline=airline,
                transfers=1,
                duration_min=1500,
                search_url="https://x",
                fx_rate=1 / 87.5,
                landed_usd=float(price),
            )
            for price in prices
        ],
        now=now,
    )


def test_dead_man_switch_fires_when_scanner_is_silent(conn, config_stub):
    store.set_meta(conn, "last_scan_at", "2026-07-31T00:00:00Z")
    warning = digest.dead_man_switch(conn, now="2026-08-01T03:00:00Z", max_age_hours=12)
    assert warning is not None
    assert "сканер молчит" in warning
    assert "2026-07-31" in warning


def test_dead_man_switch_silent_when_scan_is_recent(conn, config_stub):
    store.set_meta(conn, "last_scan_at", "2026-08-01T00:00:00Z")
    assert digest.dead_man_switch(conn, now="2026-08-01T03:00:00Z", max_age_hours=12) is None


def test_dead_man_switch_fires_when_never_scanned(conn):
    warning = digest.dead_man_switch(conn, now="2026-08-01T03:00:00Z", max_age_hours=12)
    assert warning is not None
    assert "ни разу" in warning


def test_trend_line_mentions_falling_streak(conn, config_stub):
    for index, price in enumerate([340, 330, 320, 310]):
        seed(conn, [price], now=f"2026-07-{29 + index:02d}T06:00:00Z" if index < 3 else "2026-08-01T06:00:00Z")
    lines = digest.render_trends(conn, config_stub, now="2026-08-01T07:00:00Z")
    assert any("дешевеет 3-й день" in line for line in lines)


def test_trend_line_is_empty_without_movement(conn, config_stub):
    seed(conn, [320], now="2026-08-01T06:00:00Z")
    assert digest.render_trends(conn, config_stub, now="2026-08-01T07:00:00Z") == []


def test_weekly_manual_block_appears_on_monday(config_stub):
    # 2026-08-03 — понедельник
    block = digest.weekly_manual_block(config_stub, now="2026-08-03T03:00:00Z")
    assert block is not None
    assert "проверь руками" in block
    assert "DEL" in block and "KUL" in block


def test_weekly_manual_block_is_absent_on_other_days(config_stub):
    assert digest.weekly_manual_block(config_stub, now="2026-08-04T03:00:00Z") is None


def test_precision_block_appears_every_two_weeks(conn, config_stub):
    store.record_alert(conn, "FRU-HKT-2026-10-12-", 250.0, "2026-07-25T06:00:00Z", "buy", "bought")
    store.record_alert(conn, "FRU-HKT-2026-10-14-", 260.0, "2026-07-26T06:00:00Z", "buy", "mismatch")
    block = digest.precision_block(conn, now="2026-08-01T03:00:00Z", last_report_at=None)
    assert block is not None
    assert "1/2" in block or "50%" in block


def test_precision_block_absent_if_reported_recently(conn, config_stub):
    store.record_alert(conn, "FRU-HKT-2026-10-12-", 250.0, "2026-07-25T06:00:00Z", "buy", "bought")
    block = digest.precision_block(
        conn, now="2026-08-01T03:00:00Z", last_report_at="2026-07-28T03:00:00Z"
    )
    assert block is None


def test_precision_block_absent_without_alerts(conn):
    assert digest.precision_block(conn, now="2026-08-01T03:00:00Z", last_report_at=None) is None


def test_full_digest_includes_arbitrage_section(conn, config_stub):
    seed(conn, [268.0], now="2026-08-01T02:00:00Z", market="kg")
    seed(conn, [239.0], now="2026-08-01T02:00:00Z", market="ru")
    store.set_meta(conn, "last_scan_at", "2026-08-01T02:00:00Z")
    text = digest.build_digest_text(conn, config_stub, now="2026-08-01T03:00:00Z")
    assert "арбитраж" in text


def test_full_digest_starts_with_alarm_when_scanner_silent(conn, config_stub):
    seed(conn, [268.0], now="2026-08-01T02:00:00Z")
    store.set_meta(conn, "last_scan_at", "2026-07-29T02:00:00Z")
    text = digest.build_digest_text(conn, config_stub, now="2026-08-01T03:00:00Z")
    assert text.splitlines()[0].startswith("🚨")
```

- [ ] **Step 2: Запустить тест, убедиться в падении**

Run: `.venv/bin/pytest tests/test_digest_v11.py -v`
Ожидается: FAIL, `AttributeError: module 'src.digest' has no attribute 'dead_man_switch'`

- [ ] **Step 3: Дописать в `src/digest.py`**

```python
# --- дописать в конец src/digest.py ---

from src import arbitrage  # noqa: E402

MAX_SCAN_AGE_HOURS = 12
PRECISION_PERIOD_DAYS = 14

MANUAL_HUBS = [
    ("DEL", "Дели — IndiGo/Air India, склейки на HKT и DPS"),
    ("KUL", "Куала-Лумпур — AirAsia, самый дешёвый вход в DPS"),
    ("DXB", "Дубай — flydubai, часто дешевле прямых связок"),
]


def dead_man_switch(
    conn: sqlite3.Connection, now: str, max_age_hours: int = MAX_SCAN_AGE_HOURS
) -> Optional[str]:
    """Молчание инструмента не должно выглядеть как «дешёвых билетов нет»."""
    last_scan = store.get_meta(conn, "last_scan_at")
    if last_scan is None:
        return "🚨 <b>Сканер ещё ни разу не отработал успешно</b> — проверь GitHub Actions."
    if last_scan < store.shift_hours(now, -max_age_hours):
        return (
            f"🚨 <b>Сканер молчит с {last_scan}</b> "
            f"(больше {max_age_hours} ч) — проверь GitHub Actions."
        )
    return None


def render_trends(conn: sqlite3.Connection, cfg, now: str) -> list[str]:
    lines = []
    for origin, destination in cfg.routes():
        daily = rules.daily_minimums(
            conn, origin, destination, since=store.shift_days(now, -cfg.baseline_window_days)
        )
        streak = rules.falling_streak(daily)
        delta = rules.week_delta(conn, origin, destination, now=now)
        route = f"{origin}→{destination}"
        if streak >= 2:
            lines.append(f"{route} дешевеет {streak}-й день подряд.")
        elif delta is not None and abs(delta) >= 0.05:
            direction = "дешевле" if delta < 0 else "дороже"
            lines.append(f"{route}: на {abs(round(delta * 100))}% {direction}, чем неделю назад.")
    return lines


def weekly_manual_block(cfg, now: str) -> Optional[str]:
    """По понедельникам — напоминание проверить лоукостеров вручную."""
    local = _local_date(now, cfg.timezone)
    if local.weekday() != 0:
        return None
    lines = ["🧭 <b>Проверь руками</b> (вне GDS и метапоиска):"]
    for code, description in MANUAL_HUBS:
        for origin in cfg.origins:
            for destination in cfg.destinations:
                url = (
                    f"https://www.google.com/travel/flights?q=Flights%20from%20{origin}%20"
                    f"to%20{destination}%20via%20{code}"
                )
                lines.append(f'· {origin}→{code}→{destination}: <a href="{url}">поиск</a>')
                break
            break
        lines[-1] += f" — {description}"
    return "\n".join(lines)


def precision_block(
    conn: sqlite3.Connection, now: str, last_report_at: Optional[str]
) -> Optional[str]:
    """Раз в две недели — насколько BUY-алерты оказались настоящими."""
    if last_report_at is not None and last_report_at > store.shift_days(
        now, -PRECISION_PERIOD_DAYS
    ):
        return None
    stats = store.alert_stats(conn, since=store.shift_days(now, -PRECISION_PERIOD_DAYS * 2))
    if stats["total"] == 0:
        return None
    answered = stats["bought"] + stats["mismatch"]
    if answered == 0:
        return (
            f"📊 За период было {stats['total']} алертов, обратной связи нет. "
            "Отвечай /bought или /mismatch — по этому калибруются пороги."
        )
    precision = stats["bought"] / answered
    return (
        f"📊 Precision алертов: {stats['bought']}/{answered} ({round(precision * 100)}%), "
        f"всего алертов {stats['total']}."
    )


def build_digest_text(conn: sqlite3.Connection, cfg, now: str) -> str:
    parts: list[str] = []

    alarm = dead_man_switch(conn, now)
    if alarm:
        parts.append(alarm)

    summaries = build_all_summaries(conn, cfg, now=now)
    parts.append(render_digest(summaries, cfg, now=now))

    findings = arbitrage.find(conn, cfg, now=now)
    if findings:
        parts.append(
            "\n".join(["🔄 <b>Кросс-рыночный арбитраж</b>"] + [
                arbitrage.render_line(finding) for finding in findings[:5]
            ])
        )

    trends = render_trends(conn, cfg, now=now)
    if trends:
        parts.append("📈 <b>Тренды</b>\n" + "\n".join(trends))

    precision = precision_block(conn, now, store.get_meta(conn, "precision_reported_at"))
    if precision:
        parts.append(precision)
        store.set_meta(conn, "precision_reported_at", now)

    manual = weekly_manual_block(cfg, now)
    if manual:
        parts.append(manual)

    return "\n\n".join(parts)
```

- [ ] **Step 4: Переключить `run_digest` на новую сборку**

В `src/runner.py`, в `run_digest` заменить

```python
    summaries = digest.build_all_summaries(conn, cfg, now=now)
    text = digest.render_digest(summaries, cfg, now=now)
    conn.close()
```

на

```python
    text = digest.build_digest_text(conn, cfg, now=now)
    conn.close()
```

- [ ] **Step 5: Запустить тесты**

Run: `.venv/bin/pytest tests/test_digest_v11.py tests/test_digest.py tests/test_runner.py -v`
Ожидается: 28 passed

- [ ] **Step 6: Коммит**

```bash
git add src/digest.py src/runner.py tests/test_digest_v11.py
git commit -m "feat: dead man switch, trends, arbitrage and weekly blocks in digest"
```

---

### Task 11: Команды Telegram — приём и роутер

**Files:**
- Create: `src/commands.py`
- Modify: `src/config.py` (поддержка `extra_routes`)
- Test: `tests/test_commands.py`

- [ ] **Step 1: Написать падающий тест**

```python
# tests/test_commands.py
import json

import pytest
import requests
import responses

from src import commands, store

UPDATES_URL = "https://api.telegram.org/botTG/getUpdates"
SEND_URL = "https://api.telegram.org/botTG/sendMessage"


@pytest.fixture()
def conn(tmp_path):
    connection = store.connect(tmp_path / "history.sqlite")
    store.init_schema(connection)
    return connection


@pytest.fixture()
def session():
    return requests.Session()


def update(update_id, text, chat_id=999):
    return {
        "update_id": update_id,
        "message": {
            "message_id": update_id,
            "chat": {"id": chat_id, "type": "private"},
            "text": text,
        },
    }


@responses.activate
def test_fetch_updates_sends_stored_offset(conn, session):
    store.set_meta(conn, "tg_offset", "42")
    responses.add(responses.GET, UPDATES_URL, json={"ok": True, "result": []}, status=200)
    commands.fetch_updates(session, "TG", conn)
    assert "offset=42" in responses.calls[0].request.url


@responses.activate
def test_fetch_updates_advances_offset(conn, session):
    responses.add(
        responses.GET,
        UPDATES_URL,
        json={"ok": True, "result": [update(10, "/now"), update(11, "/status")]},
        status=200,
    )
    commands.fetch_updates(session, "TG", conn)
    assert store.get_meta(conn, "tg_offset") == "12"


@responses.activate
def test_updates_from_other_chats_are_ignored(conn, session):
    responses.add(
        responses.GET,
        UPDATES_URL,
        json={"ok": True, "result": [update(10, "/now", chat_id=777)]},
        status=200,
    )
    messages = commands.fetch_updates(session, "TG", conn, allowed_chat_id="999")
    assert messages == []
    assert store.get_meta(conn, "tg_offset") == "11"  # но offset двигаем, иначе застрянем


@responses.activate
def test_non_text_updates_are_skipped(conn, session):
    responses.add(
        responses.GET,
        UPDATES_URL,
        json={"ok": True, "result": [{"update_id": 10, "edited_message": {}}]},
        status=200,
    )
    assert commands.fetch_updates(session, "TG", conn, allowed_chat_id="999") == []


def test_parse_extracts_command_and_args():
    assert commands.parse("/threshold 220") == ("/threshold", ["220"])
    assert commands.parse("/now") == ("/now", [])
    assert commands.parse("/route add TAS HKT") == ("/route", ["add", "TAS", "HKT"])


def test_parse_strips_bot_suffix():
    assert commands.parse("/status@flight_sniper_bot") == ("/status", [])


def test_parse_lowercases_command_but_not_args():
    assert commands.parse("/ROUTE add tas hkt") == ("/route", ["add", "tas", "hkt"])


def test_parse_non_command_returns_none():
    assert commands.parse("привет") == (None, [])
    assert commands.parse("") == (None, [])


def test_help_lists_all_commands(conn, config_stub):
    reply = commands.handle(conn, config_stub, "/help", [], now="2026-08-01T06:00:00Z")
    for command in ("/now", "/status", "/threshold", "/pause", "/resume", "/route", "/bought", "/mismatch"):
        assert command in reply
    assert "15 минут" in reply  # честно про задержку


def test_unknown_command_suggests_help(conn, config_stub):
    reply = commands.handle(conn, config_stub, "/wat", [], now="2026-08-01T06:00:00Z")
    assert "/help" in reply


def test_status_reports_scan_age_and_quota(conn, config_stub):
    store.set_meta(conn, "last_scan_at", "2026-08-01T04:00:00Z")
    conn.execute("INSERT INTO api_usage (api, called_at, purpose) VALUES ('amadeus', '2026-08-01T04:00:00Z', 'confirm')")
    conn.commit()
    reply = commands.handle(conn, config_stub, "/status", [], now="2026-08-01T06:00:00Z")
    assert "2026-08-01T04:00:00Z" in reply
    assert "1/1800" in reply
    assert "записей" in reply


def test_status_without_scans_says_so(conn, config_stub):
    reply = commands.handle(conn, config_stub, "/status", [], now="2026-08-01T06:00:00Z")
    assert "ни разу" in reply


def test_threshold_updates_meta(conn, config_stub):
    reply = commands.handle(conn, config_stub, "/threshold", ["220"], now="2026-08-01T06:00:00Z")
    assert store.get_meta(conn, "abs_threshold_usd") == "220.0"
    assert "220" in reply


def test_threshold_without_argument_shows_current(conn, config_stub):
    reply = commands.handle(conn, config_stub, "/threshold", [], now="2026-08-01T06:00:00Z")
    assert "250" in reply


def test_threshold_rejects_garbage(conn, config_stub):
    reply = commands.handle(conn, config_stub, "/threshold", ["дёшево"], now="2026-08-01T06:00:00Z")
    assert "число" in reply
    assert store.get_meta(conn, "abs_threshold_usd") is None


def test_pause_and_resume_toggle_meta(conn, config_stub):
    commands.handle(conn, config_stub, "/pause", [], now="2026-08-01T06:00:00Z")
    assert store.get_meta(conn, "paused") == "1"
    commands.handle(conn, config_stub, "/resume", [], now="2026-08-01T06:00:00Z")
    assert store.get_meta(conn, "paused") == "0"


def test_route_add_stores_extra_route(conn, config_stub):
    reply = commands.handle(conn, config_stub, "/route", ["add", "tas", "hkt"], now="2026-08-01T06:00:00Z")
    assert json.loads(store.get_meta(conn, "extra_routes")) == [["TAS", "HKT"]]
    assert "TAS→HKT" in reply


def test_route_add_is_idempotent(conn, config_stub):
    commands.handle(conn, config_stub, "/route", ["add", "TAS", "HKT"], now="2026-08-01T06:00:00Z")
    commands.handle(conn, config_stub, "/route", ["add", "TAS", "HKT"], now="2026-08-01T06:00:00Z")
    assert json.loads(store.get_meta(conn, "extra_routes")) == [["TAS", "HKT"]]


def test_route_rejects_bad_iata(conn, config_stub):
    reply = commands.handle(conn, config_stub, "/route", ["add", "TASHKENT", "HKT"], now="2026-08-01T06:00:00Z")
    assert "IATA" in reply
    assert store.get_meta(conn, "extra_routes") is None


def test_bought_records_feedback(conn, config_stub):
    store.record_alert(conn, "FRU-HKT-2026-10-12-", 250.0, "2026-08-01T05:00:00Z", "buy")
    reply = commands.handle(conn, config_stub, "/bought", [], now="2026-08-01T06:00:00Z")
    assert store.last_alert(conn)["feedback"] == "bought"
    assert "записал" in reply.lower()


def test_mismatch_records_feedback(conn, config_stub):
    store.record_alert(conn, "FRU-HKT-2026-10-12-", 250.0, "2026-08-01T05:00:00Z", "buy")
    commands.handle(conn, config_stub, "/mismatch", [], now="2026-08-01T06:00:00Z")
    assert store.last_alert(conn)["feedback"] == "mismatch"


def test_feedback_without_alerts_is_explained(conn, config_stub):
    reply = commands.handle(conn, config_stub, "/bought", [], now="2026-08-01T06:00:00Z")
    assert "нет" in reply.lower()


def test_now_returns_digest_text(conn, config_stub):
    store.set_meta(conn, "last_scan_at", "2026-08-01T05:00:00Z")
    reply = commands.handle(conn, config_stub, "/now", [], now="2026-08-01T06:00:00Z")
    assert "FRU→HKT" in reply
```

- [ ] **Step 2: Запустить тест, убедиться в падении**

Run: `.venv/bin/pytest tests/test_commands.py -v`
Ожидается: FAIL, `ModuleNotFoundError: No module named 'src.commands'`

- [ ] **Step 3: Расширить `src/config.py` для `extra_routes`**

Заменить блок `OVERRIDABLE` и метод `routes()`:

```python
import json  # добавить к импортам в шапке

OVERRIDABLE: dict[str, Any] = {
    "abs_threshold_usd": float,
    "yellow_delta": float,
    "anomaly_percentile": float,
    "cross_market_delta": float,
    "extra_routes": lambda value: [tuple(pair) for pair in json.loads(value)],
}
```

Добавить поле в датакласс (после `timezone`):

```python
    extra_routes: tuple[tuple[str, str], ...] = ()
```

И заменить `routes()`:

```python
    def routes(self) -> list[tuple[str, str]]:
        base = [(o, d) for o in self.origins for d in self.destinations]
        for pair in self.extra_routes:
            entry = (pair[0], pair[1])
            if entry not in base:
                base.append(entry)
        return base
```

В `with_overrides` привести `extra_routes` к кортежу:

```python
        if "extra_routes" in changes:
            changes["extra_routes"] = tuple(tuple(pair) for pair in changes["extra_routes"])
```

(вставить непосредственно перед `return dataclasses.replace(self, **changes)`)

- [ ] **Step 4: Реализовать `src/commands.py`**

```python
"""Обработка команд Telegram.

Сервера нет: команды забираются через getUpdates в начале каждого рана,
поэтому ответ приходит с задержкой до 15 минут. Это push-first инструмент,
и /help говорит об этом прямо.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
from typing import Optional

import requests

from src import digest, notify, quota, store

LOG = logging.getLogger(__name__)
IATA_RE = re.compile(r"^[A-Z]{3}$")

HELP_TEXT = """<b>flight-sniper</b> — команды:

/now — внеочередной дайджест по текущей истории
/status — последний скан, объём истории, расход квоты Amadeus
/threshold 220 — изменить порог «покупать не думая» (landed USD)
/pause — приостановить алерты
/resume — возобновить алерты
/route add TAS HKT — добавить маршрут
/bought — по последнему BUY-алерту: цена совпала, купил
/mismatch — по последнему BUY-алерту: цена разошлась
/help — эта справка

<i>Ответ на команду приходит при следующем запуске воркфлоу — задержка до 15 минут.
Дайджесты и алерты приходят сами.</i>"""


def fetch_updates(
    session: requests.Session,
    bot_token: str,
    conn: sqlite3.Connection,
    allowed_chat_id: Optional[str] = None,
    timeout: int = 0,
) -> list[str]:
    """Забирает новые сообщения и двигает offset. Возвращает тексты своих сообщений."""
    offset = store.get_meta(conn, "tg_offset")
    params = {"timeout": timeout}
    if offset:
        params["offset"] = offset
    try:
        response = session.get(
            notify.API.format(token=bot_token, method="getUpdates"),
            params=params,
            timeout=notify.TIMEOUT,
        )
        payload = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise notify.TelegramError(f"getUpdates: {exc}") from exc
    if not payload.get("ok"):
        raise notify.TelegramError(f"getUpdates: {payload.get('description')}")

    texts: list[str] = []
    highest = None
    for item in payload.get("result", []):
        highest = item["update_id"]
        message = item.get("message") or {}
        text = message.get("text")
        chat_id = str((message.get("chat") or {}).get("id", ""))
        if not text:
            continue
        if allowed_chat_id is not None and chat_id != str(allowed_chat_id):
            LOG.warning("сообщение от чужого чата %s проигнорировано", chat_id)
            continue
        texts.append(text)

    if highest is not None:
        # Смещаем offset даже для чужих сообщений, иначе они возвращаются вечно.
        store.set_meta(conn, "tg_offset", str(highest + 1))
    return texts


def parse(text: str) -> tuple[Optional[str], list[str]]:
    text = (text or "").strip()
    if not text.startswith("/"):
        return None, []
    parts = text.split()
    command = parts[0].split("@")[0].lower()
    return command, parts[1:]


def _cmd_status(conn: sqlite3.Connection, cfg, now: str) -> str:
    last_scan = store.get_meta(conn, "last_scan_at")
    used = quota.used_this_month(conn, "amadeus", now)
    rows = store.count_rows(conn)
    paused = store.get_meta(conn, "paused") == "1"
    errors = store.get_meta(conn, "last_scan_errors", "0")
    lines = [
        "<b>Статус</b>",
        f"последний скан: {last_scan}" if last_scan else "сканер ещё ни разу не отработал",
        f"записей в истории: {rows}",
        f"квота Amadeus: {used}/{quota.MONTHLY_LIMIT} за месяц",
        f"ошибок в последнем скане: {errors}",
        f"порог BUY: ${cfg.abs_threshold_usd:.0f}",
        f"маршруты: {', '.join(f'{o}→{d}' for o, d in cfg.routes())}",
    ]
    if paused:
        lines.append("⏸ алерты на паузе (/resume чтобы вернуть)")
    return "\n".join(lines)


def _cmd_threshold(conn: sqlite3.Connection, cfg, args: list[str]) -> str:
    if not args:
        return f"Текущий порог BUY: ${cfg.abs_threshold_usd:.0f}. Изменить: /threshold 220"
    try:
        value = float(args[0].replace("$", "").replace(",", "."))
    except ValueError:
        return "Порог должен быть числом, например: /threshold 220"
    if value <= 0:
        return "Порог должен быть больше нуля."
    store.set_meta(conn, "abs_threshold_usd", str(value))
    return f"Порог BUY теперь ${value:.0f}."


def _cmd_route(conn: sqlite3.Connection, cfg, args: list[str]) -> str:
    if len(args) != 3 or args[0].lower() != "add":
        return "Формат: /route add TAS HKT"
    origin, destination = args[1].upper(), args[2].upper()
    if not IATA_RE.match(origin) or not IATA_RE.match(destination):
        return "Коды должны быть трёхбуквенными IATA, например: /route add TAS HKT"
    existing = json.loads(store.get_meta(conn, "extra_routes", "[]"))
    pair = [origin, destination]
    if pair not in existing:
        existing.append(pair)
        store.set_meta(conn, "extra_routes", json.dumps(existing))
    return f"Маршрут {origin}→{destination} добавлен. Появится в следующем скане."


def _cmd_feedback(conn: sqlite3.Connection, kind: str) -> str:
    if store.set_last_feedback(conn, kind):
        word = "цена совпала" if kind == "bought" else "цена разошлась"
        return f"Записал: {word}. Спасибо — по этому калибруются пороги."
    return "Пока нет ни одного алерта, к которому это можно отнести."


def handle(conn: sqlite3.Connection, cfg, command: str, args: list[str], now: str) -> str:
    if command == "/help" or command == "/start":
        return HELP_TEXT
    if command == "/status":
        return _cmd_status(conn, cfg, now)
    if command == "/now":
        return digest.build_digest_text(conn, cfg, now=now)
    if command == "/threshold":
        return _cmd_threshold(conn, cfg, args)
    if command == "/pause":
        store.set_meta(conn, "paused", "1")
        return "⏸ Алерты приостановлены. /resume — вернуть."
    if command == "/resume":
        store.set_meta(conn, "paused", "0")
        return "▶️ Алерты снова включены."
    if command == "/route":
        return _cmd_route(conn, cfg, args)
    if command == "/bought":
        return _cmd_feedback(conn, "bought")
    if command == "/mismatch":
        return _cmd_feedback(conn, "mismatch")
    return f"Не знаю команду {command}. Список — /help."
```

- [ ] **Step 5: Запустить тесты**

Run: `.venv/bin/pytest tests/test_commands.py tests/test_config.py -v`
Ожидается: 30 passed

- [ ] **Step 6: Коммит**

```bash
git add src/commands.py src/config.py tests/test_commands.py
git commit -m "feat: telegram command router with chat filtering"
```

---

### Task 12: Подкоманда commands и воркфлоу

**Files:**
- Modify: `src/runner.py`, `monitor.py`
- Create: `.github/workflows/commands.yml`
- Test: `tests/test_runner_commands.py`

- [ ] **Step 1: Написать падающий тест**

```python
# tests/test_runner_commands.py
import json

import pytest
import responses

from src import runner, store

UPDATES_URL = "https://api.telegram.org/botTG/getUpdates"
SEND_URL = "https://api.telegram.org/botTG/sendMessage"


@pytest.fixture()
def env(monkeypatch):
    monkeypatch.setenv("TG_BOT_TOKEN", "TG")
    monkeypatch.setenv("TG_CHAT_ID", "999")


@pytest.fixture()
def db(tmp_path, env):
    path = tmp_path / "history.sqlite"
    conn = store.connect(path)
    store.init_schema(conn)
    conn.close()
    return path


def update(update_id, text, chat_id=999):
    return {
        "update_id": update_id,
        "message": {"message_id": update_id, "chat": {"id": chat_id}, "text": text},
    }


@responses.activate
def test_run_commands_answers_each_command(db, config_stub):
    responses.add(
        responses.GET,
        UPDATES_URL,
        json={"ok": True, "result": [update(1, "/status"), update(2, "/help")]},
        status=200,
    )
    responses.add(responses.POST, SEND_URL, json={"ok": True}, status=200)
    result = runner.run_commands(config_stub, db_path=db, now="2026-08-01T06:00:00Z")
    assert result["handled"] == 2
    sent = [json.loads(c.request.body)["text"] for c in responses.calls if c.request.url.startswith(SEND_URL)]
    assert any("Статус" in text for text in sent)
    assert any("/threshold" in text for text in sent)


@responses.activate
def test_run_commands_with_no_updates_is_noop(db, config_stub):
    responses.add(responses.GET, UPDATES_URL, json={"ok": True, "result": []}, status=200)
    result = runner.run_commands(config_stub, db_path=db, now="2026-08-01T06:00:00Z")
    assert result["handled"] == 0
    assert not [c for c in responses.calls if c.request.url.startswith(SEND_URL)]


@responses.activate
def test_run_commands_ignores_plain_text(db, config_stub):
    responses.add(
        responses.GET, UPDATES_URL, json={"ok": True, "result": [update(1, "привет")]}, status=200
    )
    result = runner.run_commands(config_stub, db_path=db, now="2026-08-01T06:00:00Z")
    assert result["handled"] == 0


@responses.activate
def test_run_commands_ignores_foreign_chat(db, config_stub):
    responses.add(
        responses.GET,
        UPDATES_URL,
        json={"ok": True, "result": [update(1, "/status", chat_id=777)]},
        status=200,
    )
    result = runner.run_commands(config_stub, db_path=db, now="2026-08-01T06:00:00Z")
    assert result["handled"] == 0


@responses.activate
def test_threshold_command_persists_to_meta(db, config_stub):
    responses.add(
        responses.GET, UPDATES_URL, json={"ok": True, "result": [update(1, "/threshold 199")]}, status=200
    )
    responses.add(responses.POST, SEND_URL, json={"ok": True}, status=200)
    runner.run_commands(config_stub, db_path=db, now="2026-08-01T06:00:00Z")
    conn = store.connect(db)
    assert store.get_meta(conn, "abs_threshold_usd") == "199.0"
```

- [ ] **Step 2: Запустить тест, убедиться в падении**

Run: `.venv/bin/pytest tests/test_runner_commands.py -v`
Ожидается: FAIL, `AttributeError: module 'src.runner' has no attribute 'run_commands'`

- [ ] **Step 3: Дописать `run_commands` в `src/runner.py`**

Добавить `commands` к импортам из `src`, затем в конец файла:

```python
def run_commands(
    cfg: Config, db_path: Path = DEFAULT_DB, now: Optional[str] = None, session=None
) -> dict:
    now = now or store.utcnow()
    bot_token = require_env("TG_BOT_TOKEN")
    chat_id = require_env("TG_CHAT_ID")
    session = session or requests.Session()
    conn = _open(db_path)

    texts = commands.fetch_updates(session, bot_token, conn, allowed_chat_id=chat_id)
    handled = 0
    for text in texts:
        command, args = commands.parse(text)
        if command is None:
            continue
        effective = _effective_config(conn, cfg)
        reply = commands.handle(conn, effective, command, args, now=now)
        try:
            notify.send_message(session, bot_token, chat_id, reply)
        except notify.TelegramError as exc:
            LOG.error("ответ на %s не ушёл: %s", command, exc)
            continue
        handled += 1

    conn.close()
    return {"handled": handled}
```

- [ ] **Step 4: Добавить подкоманду в `monitor.py`**

В `build_parser()` после `sub.add_parser("digest", ...)`:

```python
    sub.add_parser("commands", help="обработать команды из Telegram")
```

И в `main()` заменить ветвление на:

```python
        if args.command == "scan":
            result = runner.run_scan(cfg, db_path=args.db)
        elif args.command == "backfill":
            result = runner.run_backfill(cfg, db_path=args.db, force=args.force)
        elif args.command == "commands":
            result = runner.run_commands(cfg, db_path=args.db)
        else:
            result = runner.run_digest(cfg, db_path=args.db)
```

- [ ] **Step 5: Создать `.github/workflows/commands.yml`**

```yaml
# Лёгкий воркфлоу: только getUpdates и ответ, без скана.
# Для публичного репозитория минуты бесплатны, поэтому раз в 15 минут — норм.
name: commands

on:
  schedule:
    - cron: "*/15 * * * *"
  workflow_dispatch:

permissions:
  contents: write

concurrency:
  group: flight-sniper-repo
  cancel-in-progress: false

jobs:
  commands:
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
      - name: Handle commands
        run: python monitor.py commands
        env:
          TG_BOT_TOKEN: ${{ secrets.TG_BOT_TOKEN }}
          TG_CHAT_ID: ${{ secrets.TG_CHAT_ID }}
      - name: Persist database
        run: bash scripts/db_push.sh "commands $(date -u +%Y-%m-%dT%H:%M:%SZ)"
```

- [ ] **Step 6: Добавить ключи Amadeus в scan.yml**

В `.github/workflows/scan.yml`, в шаге `Scan`, дополнить `env`:

```yaml
        env:
          TP_TOKEN: ${{ secrets.TP_TOKEN }}
          AMADEUS_KEY: ${{ secrets.AMADEUS_KEY }}
          AMADEUS_SECRET: ${{ secrets.AMADEUS_SECRET }}
          TG_BOT_TOKEN: ${{ secrets.TG_BOT_TOKEN }}
          TG_CHAT_ID: ${{ secrets.TG_CHAT_ID }}
```

- [ ] **Step 7: Запустить тесты и проверить YAML**

Run: `.venv/bin/pytest tests/test_runner_commands.py -v`
Ожидается: 5 passed

```bash
.venv/bin/python - <<'PY'
import pathlib, yaml
for path in sorted(pathlib.Path(".github/workflows").glob("*.yml")):
    yaml.safe_load(path.read_text())
    print("ok", path)
PY
```

Ожидается: пять строк `ok`

- [ ] **Step 8: Проверить CLI**

Run: `.venv/bin/python monitor.py --help`
Ожидается: подкоманды `scan`, `backfill`, `digest`, `commands`

- [ ] **Step 9: Коммит**

```bash
git add src/runner.py monitor.py .github/workflows tests/test_runner_commands.py
git commit -m "feat: commands subcommand and 15-minute workflow"
```

---

### Task 13: Полный прогон и обновление документации

**Files:**
- Modify: `README.md`, `ASSUMPTIONS.md`

- [ ] **Step 1: Прогнать весь набор тестов**

Run: `.venv/bin/pytest && bash tests/test_db_sync.sh`
Ожидается: `~200 passed` и `OK: db sync`

- [ ] **Step 2: Дополнить `README.md`**

Заменить таблицу воркфлоу на:

```markdown
| Воркфлоу | Расписание | Что делает |
|---|---|---|
| `scan` | каждые 4 часа | кэш Aviasales → SQLite, детекция аномалий, подтверждение Amadeus, BUY-алерты |
| `digest` | 03:00 UTC (09:00 Бишкек) | сводка, арбитраж, тренды, dead man's switch, по понедельникам — ручные проверки |
| `commands` | каждые 15 минут | getUpdates → ответ на команды |
| `keepalive` | раз в 20 дней | пустой коммит, чтобы GitHub не отключил cron |
| `tests` | на push/PR | pytest |
```

Дополнить таблицу секретов:

```markdown
| `AMADEUS_KEY` / `AMADEUS_SECRET` | developers.amadeus.com → Self-Service app, **production key** |
```

Добавить раздел в конец:

````markdown
## Команды бота

| Команда | Что делает |
|---|---|
| `/now` | внеочередной дайджест |
| `/status` | последний скан, объём истории, расход квоты Amadeus |
| `/threshold 220` | изменить порог «покупать не думая» |
| `/pause` / `/resume` | приостановить и вернуть алерты |
| `/route add TAS HKT` | добавить маршрут |
| `/bought` / `/mismatch` | обратная связь по последнему BUY-алерту |
| `/help` | справка |

Ответ приходит при следующем запуске `commands` — до 15 минут. Это push-first
инструмент: дайджесты и алерты приходят сами.

## Квоты

Amadeus вызывается только на подтверждение кандидатов (<100/мес) и как fallback
при пустом кэше (до 20/день). Hard stop — 1800 вызовов в календарный месяц,
счётчик в таблице `api_usage`. При исчерпании алерты продолжают уходить, но с
пометкой «не подтвердилось».

## Калибровка (первые две недели)

1. `abs_threshold_usd` изначально консервативный. После двух недель истории
   выставить его равным p15 фактических цен: `/threshold <значение>`.
2. Если Amadeus не подтверждает больше 30% кандидатов, снизить чувствительность:
   `anomaly_percentile` с 10 до 5 в `config.yaml`.
3. Отвечать `/bought` и `/mismatch` на каждый BUY-алерт — раз в две недели
   дайджест покажет precision.
````

- [ ] **Step 3: Дополнить `ASSUMPTIONS.md`**

Добавить раздел:

```markdown
## Допущения v1.1

1. **Amadeus подтверждает существование тарифа в GDS, а не цену покупки.**
   Покупка идёт через Aviasales или сайт авиакомпании, финальная цена может
   разойтись. Для калибровки — `/bought` и `/mismatch`.
2. **Production key обязателен.** Test-окружение отдаёт урезанный кэшированный
   набор; переключение — переменной `AMADEUS_BASE_URL`.
3. **Матчинг «≈ того же рейса» между рынками** — по маршруту + датам +
   авиакомпании + числу пересадок. Кэш не даёт ID рейса, совпадение приближённое;
   в сообщении это помечено.
4. **Fallback-даты.** При пустом кэше Amadeus опрашивается по трём
   представительным датам месяца (5, 15, 25 число) с минимальной длительностью
   из `nights_range` — полный перебор месяца не влезает в квоту.
5. **Поправка на день недели** включается только при 5+ наблюдениях по этому дню
   недели, иначе множитель равен 1.0.
6. **Задержка ответа на команды до 15 минут** — следствие отсутствия сервера,
   зафиксирована в `/help`.
```

- [ ] **Step 4: Коммит и пуш**

```bash
git add README.md ASSUMPTIONS.md
git commit -m "docs: v1.1 commands, quotas and calibration"
git push
```

---

### Task 14: Amadeus production key и живая проверка

**Files:** изменений в коде нет.

- [ ] **Step 1: Получить production key**

1. developers.amadeus.com → My Self-Service Workspace → создать приложение.
2. Запросить **production key** (бесплатная квота на нём же). Одобрение занимает
   до суток.
3. Test-ключ подходит только для отладки кода — реальных цен там нет.

- [ ] **Step 2: Положить секреты**

```bash
gh secret set AMADEUS_KEY
gh secret set AMADEUS_SECRET
gh secret list
```

Ожидается: пять секретов.

- [ ] **Step 3: Проверить авторизацию и поиск вживую**

```bash
export AMADEUS_KEY=<key> AMADEUS_SECRET=<secret>
.venv/bin/python - <<'PY'
import os, requests
from src.sources import amadeus
session = requests.Session()
token = amadeus.get_token(session, os.environ["AMADEUS_KEY"], os.environ["AMADEUS_SECRET"])
print("токен получен")
offers = amadeus.search_offers(session, token, "ALA", "HKT", "2026-10-12", "2026-10-24")
for offer in offers:
    print(offer.airline, offer.price_local, offer.currency, offer.transfers, offer.duration_min)
PY
```

Ожидается: несколько офферов с ценами в USD. Пустой ответ на всех датах — признак test-ключа.

- [ ] **Step 4: Проверить команды**

```bash
export TG_BOT_TOKEN=<token> TG_CHAT_ID=<id>
```

Отправить боту `/help`, затем:

```bash
.venv/bin/python monitor.py commands --verbose
```

Ожидается: справка приходит в Telegram. Повторить с `/status` и `/threshold 240`.

- [ ] **Step 5: Проверить, что чужие сообщения игнорируются**

Попросить кого-нибудь написать боту `/status` (или написать со второго аккаунта), затем:

```bash
.venv/bin/python monitor.py commands --verbose
```

Ожидается: в логах `сообщение от чужого чата ... проигнорировано`, ответ не отправлен.

- [ ] **Step 6: Прогнать скан с алертами**

```bash
export TP_TOKEN=<token>
.venv/bin/python monitor.py scan --verbose
```

Ожидается: в логе строка `скан: получено N, вставлено M, алертов K`. Если K > 0 — проверить текст алерта в Telegram глазами: цена, даты, ссылка, пометка уровня.

- [ ] **Step 7: Проверить дайджест целиком**

```bash
.venv/bin/python monitor.py digest --verbose
```

Ожидается: сводка + (если есть) арбитраж + тренды. Проверить, что dead man's switch не срабатывает ложно.

- [ ] **Step 8: Включить облачные воркфлоу**

```bash
bash scripts/db_push.sh "v1.1 local run $(date -u +%F)"
gh workflow run commands.yml
sleep 90
gh run list --workflow=commands.yml --limit 1
gh workflow run scan.yml
sleep 120
gh run list --workflow=scan.yml --limit 1
```

Ожидается: оба рана `completed success`.

- [ ] **Step 9: Проверить расход квоты после первых суток**

Отправить боту `/status`, дождаться ответа.
Ожидается: `квота Amadeus: N/1800`, где N — небольшое число (единицы-десятки). Если N растёт на сотни в сутки — что-то дёргает confirm в цикле, смотреть `api_usage`.

---

## Definition of Done (v1.1)

- [ ] Amadeus confirm + BUY-алерты с дедупом, счётчик квоты с hard stop 1800
- [ ] Fallback-скан Amadeus при тонком кэше (3 скана подряд, до 20 запросов/день)
- [ ] Кросс-рыночный арбитраж с матчингом ≈рейсов и порогом `cross_market_delta`
- [ ] Команды `/now`, `/status`, `/threshold`, `/pause`, `/resume`, `/route add`, `/bought`, `/mismatch`, `/help` с фильтром по chat_id
- [ ] Dead man's switch в дайджесте
- [ ] Тренды (дельта 7 дней, серия снижений) и поправка на день недели
- [ ] Еженедельный блок ручных проверок по понедельникам
- [ ] Precision-отчёт раз в две недели
- [ ] `pytest` зелёный, сеть в тестах не используется

---

## Self-Review: покрытие спеки v1.1

| Требование спеки | Задача |
|---|---|
| Amadeus confirm, только production key | 2, 14 |
| Подтверждённая цена ≤ кандидат × 1.1 → BUY, иначе «не подтвердилось» | 5 |
| Счётчик запросов с hard stop 1800 | 1 |
| Fallback при пустом кэше <5 записей 3 скана подряд, бюджет 20/день | 7 |
| Аномалия → немедленный алерт, не ждёт дайджеста | 6 |
| Дедуп: не слать повторно, если цена не упала на 5%+ | 4 |
| Кросс-рыночный арбитраж, формат `$268 (kg) / $239 landed (ru) 🔄 −11%` | 8 |
| В BUY-алертах указывать рынок/источник цены | 5 |
| Dead man's switch: >12 ч без скана → тревога первым сообщением | 10 |
| Тренды (дельта 7 дней) | 9, 10 |
| Поправка на день недели | 9 |
| Еженедельный блок ручных проверок со ссылками на DEL/KUL/DXB | 10 |
| Команды бота + фильтр chat_id | 11, 12 |
| `/bought` /`/mismatch` → `alerts_sent.feedback`, precision раз в 2 недели | 4, 10, 11 |
| Отдельный лёгкий `commands.yml` каждые 15 минут | 12 |
| Честное упоминание задержки 15 минут в `/help` | 11 |
| Аномалия в локальной валюте, вердикт по landed_usd | 3 |

Остаётся вне скоупа (по самой спеке): лоукостеры вне GDS, Kiwi Tequila,
ML-предсказание, веб-дашборд, мультипользовательность.

---

## Задача 5-bis: Подтверждение кандидата свежим запросом к кэшу

Заменяет отменённые задачи 1, 2, 5, 7 (весь слой Amadeus).

**Files:**
- Create: `src/alerts.py`
- Test: `tests/test_alerts.py`

**Идея.** Кандидат приходит из базы — это цена, увиденная в кэше когда-то за
последние 48 часов. Перед тем как будить пользователя, делаем точечный запрос
`prices_for_dates` по **конкретной дате вылета** кандидата (не по всему месяцу).
Три исхода:

| Что вернул кэш | Уровень | Смысл |
|---|---|---|
| цена ≤ кандидат × 1.1 | `buy` | предложение живо, можно брать |
| цена > кандидат × 1.1 | `unconfirmed` | подорожало, кэш отставал |
| ничего не вернул | `unconfirmed` | вариант исчез из выдачи |

Порог 1.1 тот же, что планировался для Amadeus: кэш обновляется рывками, и
разница в пределах 10% — это шум, а не движение цены.

**Стоимость.** Один запрос на кандидата, лимиты Travelpayouts щедрые. Отдельный
счётчик не нужен: кандидатов единицы в сутки, а на дедупе (`store.should_alert`)
отсекаются повторы по той же связке.

**Интерфейс.**

```python
LEVEL_BUY = "buy"
LEVEL_UNCONFIRMED = "unconfirmed"
CONFIRM_TOLERANCE = 1.1

@dataclass(frozen=True)
class ConfirmResult:
    candidate: rules.Candidate
    level: str
    confirmed_local: Optional[float]   # цена из свежего запроса, локальная валюта
    confirmed_usd: Optional[float]
    note: str

def confirm(session, token, cfg, candidate, rates, now) -> ConfirmResult
def render_alert(result: ConfirmResult) -> str
```

`confirm` вызывает `aviasales.fetch_prices_for_dates` с `departure_at`, равным
**дате кандидата** (`YYYY-MM-DD`, а не месяцу), рынком и валютой кандидата,
`limit=30`. Из ответа берёт минимальную цену по той же связке
(та же дата вылета, та же авиакомпания, то же число пересадок); если такой нет —
минимальную по дате вообще, и помечает это в `note`. Сравнение делает в
**локальной валюте** (движение курса не должно превращаться в «подорожало»),
а в текст алерта выводит landed USD.

`AviasalesError` не должен ронять скан: ловится внутри, даёт `unconfirmed`
с текстом ошибки в `note`.

**Текст алерта** — как в отменённой задаче 5, но вместо строки «Amadeus сейчас»
пишется «в кэше сейчас», и добавляется продавец из поля `gate`
(«City.Travel», «Trip.com»): дешёвый тариф посредника означает худшую поддержку
при отмене рейса, пользователь должен видеть это до перехода по ссылке.

**Тесты** (замокать `responses` на `prices_for_dates`):
- цена подтвердилась в пределах 10% → `buy`
- цена выросла больше чем на 10% → `unconfirmed`
- ровно на границе 10% → `buy`
- кэш вернул пусто → `unconfirmed`, в `note` «исчез из выдачи»
- `AviasalesError` → `unconfirmed`, скан не падает
- запрос уходит с `departure_at` равным дате кандидата, а не месяцу
- сравнение идёт в локальной валюте: при выросшем курсе, но той же цене → `buy`
- в тексте алерта есть маршрут, обе цены, дата, авиакомпания, продавец, рынок, ссылка
- HTML экранируется

**Коммит:** `feat: confirm candidates with a fresh cache lookup`
