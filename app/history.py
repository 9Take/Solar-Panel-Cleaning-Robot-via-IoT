"""History storage in SQLite (default logs/gateway.db).

Tables (ts = Unix time in seconds, UTC):
  latest     one row (id = 1): newest reading, updated every poll -> live view
  snapshots  all tag values every SNAPSHOT_INTERVAL_S, kept HISTORY_RETENTION_DAYS
  events     state/alarm/mode/E-stop changes and online/offline, never pruned

Tag values are stored as a JSON object per row, so tag map changes need no
schema migration. Query one value with json_extract, e.g.
  SELECT ts, json_extract(data, '$.battery_pct') FROM snapshots

WAL mode lets a reader (the dashboard) query while the gateway writes.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS latest (
    id      INTEGER PRIMARY KEY CHECK (id = 1),
    ts      REAL NOT NULL,
    online  INTEGER NOT NULL,
    data    TEXT            -- JSON {tag: value}; NULL while never read
);
CREATE TABLE IF NOT EXISTS snapshots (
    ts      REAL NOT NULL,
    data    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS snapshots_ts ON snapshots (ts);
CREATE TABLE IF NOT EXISTS events (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    ts      REAL NOT NULL,
    kind    TEXT NOT NULL,  -- change | online | offline | read_error
    tag     TEXT,
    old     TEXT,
    new     TEXT,
    message TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS events_ts ON events (ts);
"""


class HistoryStore:
    def __init__(self, path: Path | str) -> None:
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(SCHEMA)
        self.db.commit()

    def close(self) -> None:
        self.db.close()

    def set_latest(self, ts: float, online: bool, values: dict | None) -> None:
        """Update the live row. values=None keeps the last known values (e.g. while offline)."""
        if values is None:
            self.db.execute(
                "INSERT INTO latest (id, ts, online, data) VALUES (1, ?, ?, NULL) "
                "ON CONFLICT (id) DO UPDATE SET ts = excluded.ts, online = excluded.online",
                (ts, int(online)))
        else:
            self.db.execute(
                "INSERT INTO latest (id, ts, online, data) VALUES (1, ?, ?, ?) "
                "ON CONFLICT (id) DO UPDATE SET ts = excluded.ts, online = excluded.online, data = excluded.data",
                (ts, int(online), json.dumps(values)))
        self.db.commit()

    def add_snapshot(self, ts: float, values: dict) -> None:
        self.db.execute("INSERT INTO snapshots (ts, data) VALUES (?, ?)", (ts, json.dumps(values)))
        self.db.commit()

    def add_event(self, ts: float, kind: str, message: str,
                  tag: str | None = None, old=None, new=None) -> None:
        self.db.execute(
            "INSERT INTO events (ts, kind, tag, old, new, message) VALUES (?, ?, ?, ?, ?, ?)",
            (ts, kind, tag, None if old is None else str(old), None if new is None else str(new), message))
        self.db.commit()

    def prune_snapshots(self, older_than_ts: float) -> int:
        deleted = self.db.execute("DELETE FROM snapshots WHERE ts < ?", (older_than_ts,)).rowcount
        self.db.commit()
        return deleted

    # --- read helpers (tests, CLI, dashboard examples) -------------------------

    def latest(self) -> dict | None:
        row = self.db.execute("SELECT ts, online, data FROM latest WHERE id = 1").fetchone()
        if row is None:
            return None
        return {"ts": row[0], "online": bool(row[1]), "values": json.loads(row[2]) if row[2] else None}

    def snapshots(self, since_ts: float = 0) -> list[tuple[float, dict]]:
        rows = self.db.execute("SELECT ts, data FROM snapshots WHERE ts >= ? ORDER BY ts", (since_ts,))
        return [(ts, json.loads(data)) for ts, data in rows]

    def events(self, since_ts: float = 0) -> list[dict]:
        rows = self.db.execute(
            "SELECT ts, kind, tag, old, new, message FROM events WHERE ts >= ? ORDER BY id", (since_ts,))
        keys = ("ts", "kind", "tag", "old", "new", "message")
        return [dict(zip(keys, row)) for row in rows]
