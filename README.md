# flight-sniper

Личный монитор цен FRU/ALA → HKT/DPS на октябрь 2026. Интерфейс — только Telegram.
Стоимость эксплуатации — $0/мес.

## Как это работает

| Воркфлоу | Расписание | Что делает |
|---|---|---|
| `scan` | каждые 4 часа | запросы к кэшу Aviasales (8 в режиме one_way, 16 для round-trip), запись истории |
| `digest` | 03:00 UTC (09:00 Бишкек) | сводка по 4 маршрутам в Telegram |
| `keepalive` | раз в 20 дней | пустой коммит: GitHub отключает cron после 60 дней без активности |
| `tests` | на push и PR | pytest + проверка синхронизации БД |

База живёт в orphan-ветке `data`: каждый ран перезаписывает её одним коммитом без
родителя. `main` содержит только код и не растёт от бинарника.

## Секреты

Settings → Secrets and variables → Actions:

| Секрет | Где взять |
|---|---|
| `TP_TOKEN` | travelpayouts.com → Инструменты → API |
| `TG_BOT_TOKEN` | @BotFather |
| `TG_CHAT_ID` | @userinfobot |

Токен Travelpayouts передаётся заголовком `X-Access-Token`, а не query-параметром:
иначе он попадал бы в текст сетевых исключений, а оттуда — в базу, которая
публикуется в ветке `data`.

## Локальный запуск

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt

export TP_TOKEN=... TG_BOT_TOKEN=... TG_CHAT_ID=...
.venv/bin/python monitor.py backfill   # один раз, на пустой базе
.venv/bin/python monitor.py scan
.venv/bin/python monitor.py digest
```

Тесты: `.venv/bin/pytest` и `bash tests/test_db_sync.sh`. Сеть не нужна — все
внешние вызовы замоканы.

## Метрика

Всё сравнение — в `landed_usd`:

```
landed_usd = price_local × fx_rate × (1 + fx_markup[currency])
```

`fx_rate` (USD за единицу валюты) пишется в каждую строку и обновляется при
каждой повторной встрече оффера, поэтому падение цены всегда отличимо от
движения курса.

Три уровня сравнения:
- аномалия во времени — внутри одного рынка, в локальной валюте;
- арбитраж между рынками и вердикт BUY — по `landed_usd`.

## Устройство

| Модуль | Ответственность |
|---|---|
| `monitor.py` | CLI-вход: `scan`, `backfill`, `digest` |
| `src/config.py` | дефолты из `config.yaml` + оверрайды из таблицы `meta` |
| `src/models.py` | `PriceRecord` — единственный тип обмена между слоями |
| `src/store.py` | SQLite: схема, вставка с дедупом офферов, выборки, `meta` |
| `src/fx.py` | курсы валют, `landed_usd` |
| `src/sources/aviasales.py` | Data API v3: парсинг, фильтры, HTTP, скан, бэкфилл |
| `src/rules.py` | перцентили, baseline, светофор |
| `src/digest.py` | сборка и рендер текста дайджеста |
| `src/notify.py` | транспорт до Telegram |
| `src/runner.py` | оркестрация одного рана |

Изменяемое состояние (`abs_threshold_usd`, пауза, время последнего скана) живёт в
таблице `meta`, а не в `config.yaml`: три воркфлоу, коммитящие один файл в `main`,
гонялись бы за него.

## Ограничения

Кэш Aviasales — сигнал, а не оферта: цены отражают чужие поиски за последние ~48
часов и на выкупе могут отличаться. Запись остаётся «свежей» весь TTL, поэтому
подорожавший вариант ещё какое-то время виден по старой цене — дайджест не
утверждает, что тариф доступен прямо сейчас.

Подтверждение цены через Amadeus, мгновенные BUY-алерты, кросс-рыночный арбитраж
и команды бота — это v1.1, см. `docs/superpowers/plans/2026-08-01-flight-sniper-v1.1.md`.
