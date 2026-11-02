"""SQLite storage. One connection per request; schema is versioned.

Two ways a DCS server authenticates:
  * its own server key (servers.key_hash), issued at registration; or
  * a fleet key (fleets.key_hash) shared by a hosting fleet. A fleet server row is
    created on first contact and identified by fleet_identity = "<client ip>:<port>".

SCHEMA_VERSION is stored in PRAGMA user_version. A database created by an older
schema is refused at startup with an explicit message rather than half-migrated:
CREATE TABLE IF NOT EXISTS cannot add columns or constraints to existing tables.
"""

from __future__ import annotations

import sqlite3

SCHEMA_VERSION = 2

SCHEMA = """
CREATE TABLE IF NOT EXISTS fleets (
  id          INTEGER PRIMARY KEY,
  key_hash    TEXT NOT NULL UNIQUE,
  name        TEXT NOT NULL,
  discord_url TEXT,
  website     TEXT,
  created_at  REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS servers (
  id             INTEGER PRIMARY KEY,
  key_hash       TEXT UNIQUE,
  fleet_id       INTEGER REFERENCES fleets(id) ON DELETE CASCADE,
  fleet_identity TEXT,
  name           TEXT NOT NULL,
  address        TEXT,
  port           INTEGER,
  max_players    INTEGER,
  mission        TEXT,
  description    TEXT,
  discord_url    TEXT,
  srs            TEXT,
  website        TEXT,
  created_at     REAL NOT NULL,
  last_report_at REAL,
  UNIQUE (fleet_id, fleet_identity),
  CHECK (key_hash IS NOT NULL OR (fleet_id IS NOT NULL AND fleet_identity IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS servers_fleet ON servers(fleet_id);
CREATE TABLE IF NOT EXISTS pilots (
  pid        TEXT PRIMARY KEY,
  name       TEXT NOT NULL,
  hidden     INTEGER NOT NULL DEFAULT 0,
  first_seen REAL NOT NULL,
  last_seen  REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS presence (
  pid       TEXT PRIMARY KEY REFERENCES pilots(pid) ON DELETE CASCADE,
  server_id INTEGER NOT NULL REFERENCES servers(id) ON DELETE CASCADE,
  side      INTEGER NOT NULL DEFAULT 0,
  slot      TEXT,
  since     REAL NOT NULL,
  last_seen REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS presence_server ON presence(server_id);
CREATE TABLE IF NOT EXISTS friend_requests (
  from_pid   TEXT NOT NULL REFERENCES pilots(pid) ON DELETE CASCADE,
  to_pid     TEXT NOT NULL REFERENCES pilots(pid) ON DELETE CASCADE,
  created_at REAL NOT NULL,
  PRIMARY KEY (from_pid, to_pid),
  CHECK (from_pid <> to_pid)
);
CREATE TABLE IF NOT EXISTS friendships (
  a_pid      TEXT NOT NULL REFERENCES pilots(pid) ON DELETE CASCADE,
  b_pid      TEXT NOT NULL REFERENCES pilots(pid) ON DELETE CASCADE,
  created_at REAL NOT NULL,
  PRIMARY KEY (a_pid, b_pid),
  CHECK (a_pid < b_pid)
);
CREATE INDEX IF NOT EXISTS friendships_b ON friendships(b_pid);
CREATE TABLE IF NOT EXISTS greetings (
  pid       TEXT NOT NULL REFERENCES pilots(pid) ON DELETE CASCADE,
  server_id INTEGER NOT NULL REFERENCES servers(id) ON DELETE CASCADE,
  at        REAL NOT NULL,
  PRIMARY KEY (pid, server_id)
);
"""


def connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path, timeout=10, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=10000")
    return conn


class SchemaError(RuntimeError):
    pass


def init_schema(path: str) -> None:
    """Create the schema on a fresh database, or verify an existing one is current."""
    conn = connect(path)
    try:
        has_tables = conn.execute(
            "SELECT COUNT(*) AS n FROM sqlite_master WHERE type = 'table' AND name = 'servers'"
        ).fetchone()["n"] > 0
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if has_tables and version != SCHEMA_VERSION:
            raise SchemaError(
                f"database {path} has schema version {version}, this build needs {SCHEMA_VERSION}. "
                "No automatic migration exists yet: back up the file and rebuild, or run a migration."
            )
        conn.executescript(SCHEMA)
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    finally:
        conn.close()
