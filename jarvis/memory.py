"""Persistenza su SQLite: eventi, chiamate, note, impegni, regole, feedback.

Tutte le funzioni accettano `path` per i test; in produzione usano DB_PATH.
"""
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
-- impegni che Jarvis deve riportare a galla al momento giusto
CREATE TABLE IF NOT EXISTS commitments (
    id INTEGER PRIMARY KEY AUTOINCREMENT, text TEXT, due_at TEXT, source TEXT,
    created_at TEXT, done_at TEXT
);
-- regole esplicite dell'utente ("non disturbarmi per X"): applicate in policy.py
CREATE TABLE IF NOT EXISTS rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT, pattern TEXT, action TEXT, until TEXT, created_at TEXT
);
-- esito delle decisioni: alimenta la calibrazione del triage
CREATE TABLE IF NOT EXISTS feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT, source TEXT, urgency INTEGER,
    verdict TEXT, created_at TEXT
);
-- chiamate rimandate (quiet hours / limite giornaliero): rivalutate senza nuovo costo LLM
CREATE TABLE IF NOT EXISTS pending (
    event_id TEXT PRIMARY KEY, source TEXT, title TEXT, summary TEXT,
    urgency INTEGER, retry_at TEXT
);
CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT, text TEXT, channel TEXT, created_at TEXT
);
-- cose non urgenti accumulate per il briefing
CREATE TABLE IF NOT EXISTS digest (
    id INTEGER PRIMARY KEY AUTOINCREMENT, text TEXT, created_at TEXT, sent_at TEXT
);
-- eventi spinti da fuori via POST /events
CREATE TABLE IF NOT EXISTS inbox (
    id TEXT PRIMARY KEY, source TEXT, title TEXT, body TEXT, created_at TEXT, consumed_at TEXT
);
-- trascritto delle telefonate: continuità tra una chiamata e l'altra
CREATE TABLE IF NOT EXISTS turns (
    id INTEGER PRIMARY KEY AUTOINCREMENT, call_sid TEXT, role TEXT, text TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS jobs (name TEXT PRIMARY KEY, last_run_at TEXT);
"""

# colonne aggiunte dopo la prima versione dello schema
MIGRATIONS = {
    "events": {"summary": "TEXT"},
    "calls": {"sid": "TEXT", "status": "TEXT", "event_id": "TEXT"},
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _migrate(conn: sqlite3.Connection) -> None:
    for table, columns in MIGRATIONS.items():
        existing = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        for name, decl in columns.items():
            if name not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")


@contextmanager
def db(path: str | None = None):
    conn = sqlite3.connect(path or get_settings().db_path)
    conn.row_factory = sqlite3.Row
    try:
        conn.executescript(SCHEMA)
        _migrate(conn)
        yield conn
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------- eventi

def event_seen(event_id: str, path: str | None = None) -> bool:
    with db(path) as c:
        return c.execute("SELECT 1 FROM events WHERE id=?", (event_id,)).fetchone() is not None


def save_event(event, urgency: int, decision: str, path: str | None = None, summary: str = "") -> None:
    with db(path) as c:
        c.execute(
            "INSERT OR REPLACE INTO events (id, source, title, body, urgency, decision, created_at, summary)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (event.id, event.source, event.title, event.body, urgency, decision, _now(), summary),
        )


def events_since(since_iso: str, path: str | None = None) -> list[dict]:
    with db(path) as c:
        rows = c.execute(
            "SELECT * FROM events WHERE created_at >= ? ORDER BY created_at", (since_iso,)
        ).fetchall()
        return [dict(r) for r in rows]


def get_event(event_id: str, path: str | None = None) -> dict | None:
    with db(path) as c:
        row = c.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone()
        return dict(row) if row else None


# ---------------------------------------------------------------- chiamate

def log_call(direction: str, reason: str, path: str | None = None, sid: str = "", event_id: str = "") -> None:
    with db(path) as c:
        c.execute(
            "INSERT INTO calls (direction, reason, created_at, sid, event_id) VALUES (?,?,?,?,?)",
            (direction, reason, _now(), sid, event_id),
        )


def set_call_status(sid: str, status: str, path: str | None = None) -> None:
    with db(path) as c:
        c.execute("UPDATE calls SET status=? WHERE sid=?", (status, sid))


def call_by_sid(sid: str, path: str | None = None) -> dict | None:
    with db(path) as c:
        row = c.execute("SELECT * FROM calls WHERE sid=?", (sid,)).fetchone()
        return dict(row) if row else None


def outbound_calls_since(since_iso: str, path: str | None = None) -> int:
    with db(path) as c:
        row = c.execute(
            "SELECT COUNT(*) FROM calls WHERE direction='out' AND created_at >= ?", (since_iso,)
        ).fetchone()
        return row[0]


# ---------------------------------------------------------------- note

def add_note(text: str, path: str | None = None) -> None:
    with db(path) as c:
        c.execute("INSERT INTO notes (text, created_at) VALUES (?,?)", (text, _now()))


def recent_notes(limit: int = 20, path: str | None = None) -> list[str]:
    with db(path) as c:
        rows = c.execute("SELECT text FROM notes ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [r["text"] for r in rows]


# ---------------------------------------------------------------- impegni

def add_commitment(text: str, due_at: str, source: str = "voce", path: str | None = None) -> int:
    with db(path) as c:
        cur = c.execute(
            "INSERT INTO commitments (text, due_at, source, created_at) VALUES (?,?,?,?)",
            (text, due_at, source, _now()),
        )
        return int(cur.lastrowid)


def due_commitments(now_iso: str, path: str | None = None) -> list[dict]:
    with db(path) as c:
        rows = c.execute(
            "SELECT * FROM commitments WHERE done_at IS NULL AND due_at <= ? ORDER BY due_at", (now_iso,)
        ).fetchall()
        return [dict(r) for r in rows]


def open_commitments(limit: int = 20, path: str | None = None) -> list[dict]:
    with db(path) as c:
        rows = c.execute(
            "SELECT * FROM commitments WHERE done_at IS NULL ORDER BY due_at LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]


def complete_commitment(commitment_id: int, path: str | None = None) -> bool:
    with db(path) as c:
        cur = c.execute(
            "UPDATE commitments SET done_at=? WHERE id=? AND done_at IS NULL", (_now(), commitment_id)
        )
        return cur.rowcount > 0


# ---------------------------------------------------------------- regole

def add_rule(pattern: str, action: str, until_iso: str | None = None, path: str | None = None) -> int:
    with db(path) as c:
        cur = c.execute(
            "INSERT INTO rules (pattern, action, until, created_at) VALUES (?,?,?,?)",
            (pattern.strip().lower(), action, until_iso, _now()),
        )
        return int(cur.lastrowid)


def active_rules(now_iso: str, path: str | None = None) -> list[dict]:
    with db(path) as c:
        rows = c.execute(
            "SELECT * FROM rules WHERE until IS NULL OR until > ? ORDER BY id", (now_iso,)
        ).fetchall()
        return [dict(r) for r in rows]


# ---------------------------------------------------------------- feedback

def add_feedback(event_id: str, source: str, urgency: int, verdict: str, path: str | None = None) -> None:
    with db(path) as c:
        c.execute(
            "INSERT INTO feedback (event_id, source, urgency, verdict, created_at) VALUES (?,?,?,?,?)",
            (event_id, source, urgency, verdict, _now()),
        )


def feedback_stats(path: str | None = None) -> list[dict]:
    with db(path) as c:
        rows = c.execute(
            "SELECT source, verdict, COUNT(*) AS n, AVG(urgency) AS avg_urgency"
            " FROM feedback GROUP BY source, verdict"
        ).fetchall()
        return [dict(r) for r in rows]


# ---------------------------------------------------------------- rimandati

def add_pending(event_id: str, source: str, title: str, summary: str, urgency: int,
                retry_at_iso: str, path: str | None = None) -> None:
    with db(path) as c:
        c.execute(
            "INSERT OR REPLACE INTO pending VALUES (?,?,?,?,?,?)",
            (event_id, source, title, summary, urgency, retry_at_iso),
        )


def due_pending(now_iso: str, path: str | None = None) -> list[dict]:
    with db(path) as c:
        rows = c.execute("SELECT * FROM pending WHERE retry_at <= ?", (now_iso,)).fetchall()
        return [dict(r) for r in rows]


def clear_pending(event_id: str, path: str | None = None) -> None:
    with db(path) as c:
        c.execute("DELETE FROM pending WHERE event_id=?", (event_id,))


# ---------------------------------------------------------------- notifiche e digest

def record_notification(text: str, channel: str, path: str | None = None) -> None:
    with db(path) as c:
        c.execute(
            "INSERT INTO notifications (text, channel, created_at) VALUES (?,?,?)", (text, channel, _now())
        )


def notifications_since(since_iso: str, path: str | None = None) -> int:
    with db(path) as c:
        row = c.execute("SELECT COUNT(*) FROM notifications WHERE created_at >= ?", (since_iso,)).fetchone()
        return row[0]


def queue_digest(text: str, path: str | None = None) -> None:
    with db(path) as c:
        c.execute("INSERT INTO digest (text, created_at) VALUES (?,?)", (text, _now()))


def pending_digest(path: str | None = None) -> list[str]:
    """Voci non ancora inviate, senza consumarle."""
    with db(path) as c:
        rows = c.execute("SELECT text FROM digest WHERE sent_at IS NULL ORDER BY id").fetchall()
        return [r["text"] for r in rows]


def take_digest(path: str | None = None) -> list[str]:
    """Restituisce le voci non ancora inviate e le marca come inviate."""
    with db(path) as c:
        rows = c.execute("SELECT id, text FROM digest WHERE sent_at IS NULL ORDER BY id").fetchall()
        if rows:
            c.execute(
                f"UPDATE digest SET sent_at=? WHERE id IN ({','.join('?' * len(rows))})",
                (_now(), *[r["id"] for r in rows]),
            )
        return [r["text"] for r in rows]


# ---------------------------------------------------------------- inbox esterna

def push_inbox(event_id: str, source: str, title: str, body: str = "", path: str | None = None) -> None:
    with db(path) as c:
        c.execute(
            "INSERT OR IGNORE INTO inbox (id, source, title, body, created_at) VALUES (?,?,?,?,?)",
            (event_id, source, title, body, _now()),
        )


def drain_inbox(path: str | None = None) -> list[dict]:
    with db(path) as c:
        rows = c.execute("SELECT * FROM inbox WHERE consumed_at IS NULL ORDER BY created_at").fetchall()
        if rows:
            c.execute(
                f"UPDATE inbox SET consumed_at=? WHERE id IN ({','.join('?' * len(rows))})",
                (_now(), *[r["id"] for r in rows]),
            )
        return [dict(r) for r in rows]


# ---------------------------------------------------------------- conversazioni

def log_turn(call_sid: str, role: str, text: str, path: str | None = None) -> None:
    with db(path) as c:
        c.execute(
            "INSERT INTO turns (call_sid, role, text, created_at) VALUES (?,?,?,?)",
            (call_sid, role, text, _now()),
        )


def recent_turns(limit: int = 10, path: str | None = None) -> list[dict]:
    with db(path) as c:
        rows = c.execute("SELECT * FROM turns ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in reversed(rows)]


# ---------------------------------------------------------------- job periodici

def job_last_run(name: str, path: str | None = None) -> str | None:
    with db(path) as c:
        row = c.execute("SELECT last_run_at FROM jobs WHERE name=?", (name,)).fetchone()
        return row["last_run_at"] if row else None


def mark_job_run(name: str, when_iso: str | None = None, path: str | None = None) -> None:
    with db(path) as c:
        c.execute(
            "INSERT INTO jobs (name, last_run_at) VALUES (?,?)"
            " ON CONFLICT(name) DO UPDATE SET last_run_at=excluded.last_run_at",
            (name, when_iso or _now()),
        )
