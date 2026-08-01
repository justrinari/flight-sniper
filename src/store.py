"""SQLite-хранилище истории цен.

Файл базы синхронизируется с orphan-веткой `data`, поэтому режим журнала —
дефолтный (rollback), чтобы база всегда была одним файлом без -wal/-shm.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone as _timezone
from pathlib import Path
from typing import Iterable, Optional

from src.models import PriceRecord

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
