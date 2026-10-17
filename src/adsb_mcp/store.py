"""SQLite store for the historical tier: one row per PASS (a visit), never one per poll.

ACCUMULATES and is not reproducible (the live feed is gone once it's gone) — back the file up. Open passes
are written through on every poll (`open=1`), so a restart resumes them instead of losing or splitting them.
No coordinates are stored: distances only, so the file never holds the receiver's location.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS passes (
    id                  INTEGER PRIMARY KEY,
    hex                 TEXT NOT NULL,     -- ICAO 24-bit address: the airframe
    callsign            TEXT,              -- the route / service
    reg                 TEXT,
    type                TEXT,              -- ICAO type designator: the model
    category            TEXT,
    first_seen          REAL NOT NULL,     -- epoch seconds, feeder clock
    last_seen           REAL NOT NULL,
    last_in_zone        REAL NOT NULL,     -- last airborne sample inside the pass zone
    min_alt_ft          INTEGER,
    max_alt_ft          INTEGER,
    min_dist_km         REAL,              -- closest approach to the reference point
    max_rssi            REAL,
    points              INTEGER NOT NULL,  -- snapshots the aircraft appeared in during the pass
    emergency           TEXT,
    reference           TEXT,              -- site | airport: what min_dist_km is measured from
    departure           INTEGER NOT NULL DEFAULT 0,
    on_approach         INTEGER NOT NULL DEFAULT 0,  -- last airborne sample was on approach to the airport
    approach_low_agl_ft REAL,              -- lowest height above the field since the approach began
    go_arounds          INTEGER NOT NULL DEFAULT 0,
    landed_at           REAL,              -- seen on the ground at the airport after an approach
    outcome             TEXT,              -- set on close: arrival | arrival_presumed | go_around | departure | overflight
    open                INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS passes_hex ON passes (hex, first_seen DESC);
CREATE INDEX IF NOT EXISTS passes_callsign ON passes (callsign, first_seen DESC);
CREATE INDEX IF NOT EXISTS passes_first_seen ON passes (first_seen DESC);
CREATE INDEX IF NOT EXISTS passes_open ON passes (open) WHERE open = 1;

-- Recorder health: last poll time / status (so a stale recorder is visible, never silent).
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")  # the poller writes while the tools read
    conn.execute("PRAGMA busy_timeout=30000")
    conn.executescript(SCHEMA)
    return conn


def get_meta(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row["value"] if row else None


def set_meta(conn: sqlite3.Connection, key: str, value: str | float | None) -> None:
    conn.execute(
        "INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, None if value is None else str(value)),
    )
