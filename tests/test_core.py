import json
from datetime import datetime
from zoneinfo import ZoneInfo

from jarvis import memory
from jarvis.policy import PolicyConfig, PolicyInput, decide, in_quiet_hours
from jarvis.triage import Event, parse_triage
from jarvis.watchers import reminders

ROME = ZoneInfo("Europe/Rome")
CFG = PolicyConfig()


def at(h):
    return datetime(2026, 10, 5, h, 0, tzinfo=ROME)


def test_quiet_hours_wrap_midnight():
    assert in_quiet_hours(at(23), CFG)
    assert in_quiet_hours(at(3), CFG)
    assert not in_quiet_hours(at(12), CFG)


def test_decide_levels():
    assert decide(PolicyInput(1, at(12), 0), CFG) == "ignore"
    assert decide(PolicyInput(4, at(12), 0), CFG) == "log"
    assert decide(PolicyInput(6, at(12), 0), CFG) == "notify"
    assert decide(PolicyInput(8, at(12), 0), CFG) == "call"


def test_call_downgraded_by_limits():
    assert decide(PolicyInput(9, at(12), 3), CFG) == "notify"   # limite giornaliero
    assert decide(PolicyInput(9, at(2), 0), CFG) == "notify"    # notte
    assert decide(PolicyInput(10, at(2), 0), CFG) == "call"     # emergenza


def test_parse_triage_clamps_and_tolerates_text():
    t = parse_triage('Ecco: {"urgency": 14, "summary": "Ciao"} fine')
    assert t.urgency == 10 and t.summary == "Ciao"


def test_reminders_watcher(tmp_path):
    f = tmp_path / "r.json"
    f.write_text(json.dumps([
        {"id": "a", "at": "2026-10-05T09:00:00+02:00", "title": "passato"},
        {"id": "b", "at": "2026-10-05T18:00:00+02:00", "title": "futuro"},
    ]))
    evs = reminders.poll(now=at(12), path=f)
    assert [e.id for e in evs] == ["reminder:a"]


def test_memory_roundtrip(tmp_path):
    db = str(tmp_path / "t.db")
    ev = Event(id="x", source="test", title="t")
    assert not memory.event_seen("x", db)
    memory.save_event(ev, 5, "notify", db)
    assert memory.event_seen("x", db)
    memory.log_call("out", "r", db)
    assert memory.outbound_calls_since("2000-01-01", db) == 1
