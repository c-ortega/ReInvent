from __future__ import annotations

import json
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent / "data/planner.db"

def _connection():
    DB_PATH.parent.mkdir(exist_ok=True)
    connection = sqlite3.connect(DB_PATH)
    connection.execute("CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY, raw_json TEXT NOT NULL)")
    connection.execute("CREATE TABLE IF NOT EXISTS settings (id TEXT PRIMARY KEY, value_json TEXT NOT NULL)")
    return connection


def saveCatalog(sessions: list[dict]) -> None:
    with _connection() as db:
        db.execute("DELETE FROM sessions")
        db.executemany(
            "INSERT INTO sessions (id, raw_json) VALUES (?, ?)",
            [(str(s["sessionId"]), json.dumps(s)) for s in sessions if s.get("sessionId") is not None]
        )

def loadCatalog() -> list[dict]:
    with _connection() as db:
        return [json.loads(row[0]) for row in db.execute("SELECT raw_json FROM sessions")]

def getSetting(key: str, default):
    with _connection() as db:
        row = db.execute("SELECT value_json FROM settings WHERE id = ?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

def setSetting(key: str, value) -> None:
    with _connection() as db:
        db.execute(
            "INSERT INTO settings (key, value_json) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json",
            (key, json.dumps(value)),
        )