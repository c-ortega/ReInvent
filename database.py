from __future__ import annotations

import json
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent / "data/planner.db"

def openConnection():
    DB_PATH.parent.mkdir(exist_ok=True)
    connection = sqlite3.connect(DB_PATH)
    connection.execute("CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY, rawJson TEXT NOT NULL)")
    connection.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, valueJson TEXT NOT NULL)")
    sessionColumns = {row[1] for row in connection.execute("PRAGMA table_info(sessions)")}
    settingColumns = {row[1] for row in connection.execute("PRAGMA table_info(settings)")}
    if "raw_json" in sessionColumns:
        connection.execute("ALTER TABLE sessions RENAME COLUMN raw_json TO rawJson")
    if "value_json" in settingColumns:
        connection.execute("ALTER TABLE settings RENAME COLUMN value_json TO valueJson")
    connection.execute("UPDATE OR IGNORE settings SET key='dailyLimits' WHERE key='daily_limits'")
    connection.commit()
    return connection


def saveCatalog(sessions: list[dict]) -> None:
    with openConnection() as db:
        db.execute("DELETE FROM sessions")
        db.executemany(
            "INSERT INTO sessions (id, raw_json) VALUES (?, ?)",
            [(str(s["sessionId"]), json.dumps(s)) for s in sessions if s.get("sessionId") is not None]
        )

def loadCatalog() -> list[dict]:
    with openConnection() as db:
        return [json.loads(row[0]) for row in db.execute("SELECT raw_json FROM sessions")]

def getSetting(key: str, default):
    with openConnection() as db:
        row = db.execute("SELECT value_json FROM settings WHERE id = ?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

def setSetting(key: str, value) -> None:
    with openConnection() as db:
        db.execute(
            "INSERT INTO settings (key, value_json) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json",
            (key, json.dumps(value)),
        )