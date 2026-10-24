import sqlite3
from datetime import datetime, timedelta, timezone

from jarvis import memory
from jarvis.triage import Event


def test_event_roundtrip(tmp_path):
    db = str(tmp_path / "t.db")
    ev = Event(id="x", source="test", title="t")
    assert not memory.event_seen("x", db)
    memory.save_event(ev, 5, "notify", db, summary="riassunto")
    assert memory.event_seen("x", db)
    assert memory.get_event("x", db)["summary"] == "riassunto"
    memory.log_call("out", "r", db)
    assert memory.outbound_calls_since("2000-01-01", db) == 1


def test_call_status_and_lookup(tmp_path):
    db = str(tmp_path / "t.db")
    memory.log_call("out", "motivo", db, sid="CA1", event_id="ev1")
    memory.set_call_status("CA1", "no-answer", db)
    row = memory.call_by_sid("CA1", db)
    assert row["status"] == "no-answer" and row["event_id"] == "ev1"


def test_rules_expire(tmp_path):
    db = str(tmp_path / "t.db")
    now = datetime.now(timezone.utc)
    memory.add_rule("Newsletter", "mute", (now + timedelta(days=1)).isoformat(), db)
    memory.add_rule("scaduta", "mute", (now - timedelta(days=1)).isoformat(), db)
    memory.add_rule("per sempre", "always_call", None, db)
    patterns = [r["pattern"] for r in memory.active_rules(now.isoformat(), db)]
    assert patterns == ["newsletter", "per sempre"]   # salvate normalizzate in minuscolo


def test_digest_is_taken_once(tmp_path):
    db = str(tmp_path / "t.db")
    memory.queue_digest("a", db)
    memory.queue_digest("b", db)
    assert memory.take_digest(db) == ["a", "b"]
    assert memory.take_digest(db) == []


def test_pending_lifecycle(tmp_path):
    db = str(tmp_path / "t.db")
    now = datetime.now(timezone.utc)
    memory.add_pending("ev1", "email", "titolo", "riassunto", 9, (now - timedelta(minutes=1)).isoformat(), db)
    memory.add_pending("ev2", "email", "titolo", "riassunto", 9, (now + timedelta(hours=5)).isoformat(), db)
    assert [r["event_id"] for r in memory.due_pending(now.isoformat(), db)] == ["ev1"]
    memory.clear_pending("ev1", db)
    assert memory.due_pending(now.isoformat(), db) == []


def test_feedback_stats_group_by_source(tmp_path):
    db = str(tmp_path / "t.db")
    memory.add_feedback("e1", "email", 8, "noise", db)
    memory.add_feedback("e2", "email", 8, "noise", db)
    memory.add_feedback("e3", "calendario", 9, "useful", db)
    stats = {(r["source"], r["verdict"]): r["n"] for r in memory.feedback_stats(db)}
    assert stats == {("email", "noise"): 2, ("calendario", "useful"): 1}


def test_job_run_marker(tmp_path):
    db = str(tmp_path / "t.db")
    assert memory.job_last_run("briefing", db) is None
    memory.mark_job_run("briefing", "2026-10-24T08:00:00+00:00", db)
    memory.mark_job_run("briefing", "2026-10-25T08:00:00+00:00", db)
    assert memory.job_last_run("briefing", db) == "2026-10-25T08:00:00+00:00"


def test_migration_adds_columns_to_an_old_database(tmp_path):
    """Un DB creato dalla prima versione deve continuare a funzionare."""
    db = str(tmp_path / "old.db")
    conn = sqlite3.connect(db)
    conn.executescript(
        "CREATE TABLE events (id TEXT PRIMARY KEY, source TEXT, title TEXT, body TEXT,"
        " urgency INTEGER, decision TEXT, created_at TEXT);"
        "CREATE TABLE calls (id INTEGER PRIMARY KEY AUTOINCREMENT, direction TEXT, reason TEXT,"
        " created_at TEXT);"
    )
    conn.execute("INSERT INTO events VALUES ('vecchio','x','t','b',5,'notify','2026-01-01')")
    conn.commit()
    conn.close()

    memory.log_call("out", "nuovo", db, sid="CA9")
    assert memory.get_event("vecchio", db)["summary"] is None
    assert memory.call_by_sid("CA9", db)["reason"] == "nuovo"
