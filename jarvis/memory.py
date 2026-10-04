"""Persistenza minima su SQLite: eventi visti, chiamate fatte, note."""
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

from .config import get_settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id TEXT PRIMARY KEY, source TEXT, title TEXT, body TEXT,
    urgency INTEGER, decision TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT, direction TEXT, reason TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT, text TEXT, created_at TEXT
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def db(path: str | None = None):
    conn = sqlite3.connect(path or get_settings().db_path)
    conn.row_factory = sqlite3.Row
    try:
        conn.executescript(SCHEMA)
        yield conn
        conn.commit()
    finally:
        conn.close()


def event_seen(event_id: str, path: str | None = None) -> bool:
    with db(path) as c:
        return c.execute("SELECT 1 FROM events WHERE id=?", (event_id,)).fetchone() is not None


def save_event(event, urgency: int, decision: str, path: str | None = None) -> None:
    with db(path) as c:
        c.execute(
            "INSERT OR REPLACE INTO events VALUES (?,?,?,?,?,?,?)",
            (event.id, event.source, event.title, event.body, urgency, decision, _now()),
        )


def log_call(direction: str, reason: str, path: str | None = None) -> None:
    with db(path) as c:
        c.execute("INSERT INTO calls (direction, reason, created_at) VALUES (?,?,?)", (direction, reason, _now()))


def outbound_calls_since(since_iso: str, path: str | None = None) -> int:
    with db(path) as c:
        row = c.execute(
            "SELECT COUNT(*) FROM calls WHERE direction='out' AND created_at >= ?", (since_iso,)
        ).fetchone()
        return row[0]


def add_note(text: str, path: str | None = None) -> None:
    with db(path) as c:
        c.execute("INSERT INTO notes (text, created_at) VALUES (?,?)", (text, _now()))


def recent_notes(limit: int = 20, path: str | None = None) -> list[str]:
    with db(path) as c:
        rows = c.execute("SELECT text FROM notes ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [r["text"] for r in rows]
