"""Storage: one SQLite database, WAL mode, explicit transactions only."""
from __future__ import annotations

import sqlite3
import time
from collections.abc import Mapping
from pathlib import Path

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS agents (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  harness TEXT NOT NULL,
  session_key TEXT NOT NULL UNIQUE,
  pid INTEGER NOT NULL,
  pid_start TEXT NOT NULL,
  route TEXT NOT NULL,
  cwd TEXT,
  branch TEXT,
  status TEXT NOT NULL DEFAULT 'working'
    CHECK (status IN ('working','idle','waiting','offline')),
  task TEXT,
  created_at TEXT NOT NULL,
  last_seen TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_agents_live_name
  ON agents(name) WHERE status != 'offline';

CREATE TABLE IF NOT EXISTS messages (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  sender_id INTEGER NOT NULL REFERENCES agents(id),
  body TEXT NOT NULL,
  priority INTEGER NOT NULL DEFAULT 1 CHECK (priority BETWEEN 0 AND 2),
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS message_recipients (
  message_id INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
  agent_id INTEGER NOT NULL REFERENCES agents(id),
  announced_at TEXT,
  read_at TEXT,
  PRIMARY KEY (message_id, agent_id)
);
CREATE INDEX IF NOT EXISTS idx_recipients_open
  ON message_recipients(agent_id) WHERE read_at IS NULL;
"""


def amail_home(env: Mapping[str, str]) -> Path:
    home = Path(env.get("AMAIL_HOME", "~/.amail")).expanduser()
    for d in (home, home / "doorbells", home / "waiters"):
        d.mkdir(parents=True, exist_ok=True)
    home.chmod(0o700)
    return home


def _enable_wal(conn: sqlite3.Connection) -> None:
    """Switching journal modes needs a lock, and SQLite answers SQLITE_BUSY
    without consulting the busy handler — so retry, and skip the pragma once
    another connection has already put the database in WAL."""
    for attempt in range(20):
        if conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal":
            return
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            return
        except sqlite3.OperationalError:
            time.sleep(0.05)
    raise RuntimeError("could not put mail.db into WAL mode")


def connect(home: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(home / "mail.db", timeout=5.0)
    conn.isolation_level = None
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000")   # before any lock is taken
    _enable_wal(conn)
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA)
    row = conn.execute(
        "SELECT value FROM meta WHERE key='schema_version'").fetchone()
    if row is None:
        conn.execute("INSERT OR IGNORE INTO meta VALUES ('schema_version', ?)",
                     (str(SCHEMA_VERSION),))
    elif int(row["value"]) > SCHEMA_VERSION:
        conn.close()
        raise RuntimeError(
            f"mail.db schema v{row['value']} is newer than this amail"
            f" (v{SCHEMA_VERSION}); upgrade amail")
    return conn
