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
