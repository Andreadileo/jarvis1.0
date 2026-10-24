from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from jarvis import memory, scheduler
from jarvis.policy import PolicyConfig, Rule
from jarvis.triage import Event, Triage

ROME = ZoneInfo("Europe/Rome")
NOON = datetime(2026, 10, 5, 12, 0, tzinfo=ZoneInfo("Europe/Rome")).astimezone(timezone.utc)
CFG = PolicyConfig()


class FakeCaller:
    def __init__(self):
        self.calls, self.notes = [], []

    def place_call(self, text, event_id=""):
        self.calls.append((text, event_id))
        memory.log_call("out", text, sid=f"CA{len(self.calls)}", event_id=event_id)

    def notify(self, text):
        self.notes.append(text)
        memory.record_notification(text, "test")


@pytest.fixture
def fake(monkeypatch):
    c = FakeCaller()
    monkeypatch.setattr(scheduler, "caller", c)
    return c


def set_triage(monkeypatch, urgency, summary="riassunto"):
    monkeypatch.setattr(scheduler, "triage", lambda ev, context="": Triage(urgency, summary))


def test_urgent_event_calls(monkeypatch, fake):
    set_triage(monkeypatch, 9)
    now = datetime(2026, 10, 5, 12, 0, tzinfo=ROME).astimezone(timezone.utc)
    action = scheduler.handle_event(Event("e1", "email", "Contratto"), now, CFG, [], "")
    assert action == "call" and fake.calls == [("riassunto", "e1")]


def test_night_call_is_deferred_then_placed_in_the_morning(monkeypatch, fake):
    set_triage(monkeypatch, 9)
    night = datetime(2026, 10, 5, 3, 0, tzinfo=ROME).astimezone(timezone.utc)
    assert scheduler.handle_event(Event("e1", "email", "Contratto"), night, CFG, [], "") == "notify"
    assert fake.notes == ["riassunto"] and fake.calls == []

    pending = memory.due_pending("2100-01-01")
    assert len(pending) == 1
    assert datetime.fromisoformat(pending[0]["retry_at"]).astimezone(ROME).hour == 8

    morning = datetime(2026, 10, 5, 8, 1, tzinfo=ROME).astimezone(timezone.utc)
    scheduler.process_pending(morning, CFG)
    assert fake.calls == [("riassunto", "e1")]
    assert memory.due_pending("2100-01-01") == []


def test_deferral_degrades_to_a_notification_instead_of_looping(monkeypatch, fake):
    """Se anche al secondo giro la policy dice no, si notifica e si chiude."""
    set_triage(monkeypatch, 9)
    night = datetime(2026, 10, 5, 3, 0, tzinfo=ROME).astimezone(timezone.utc)
    scheduler.handle_event(Event("e1", "email", "x"), night, CFG, [], "")
    for i in range(CFG.max_calls_per_day):     # quota di chiamate esaurita nel frattempo
        memory.log_call("out", "altro", sid=f"X{i}")

    morning = datetime(2026, 10, 5, 8, 1, tzinfo=ROME).astimezone(timezone.utc)
    scheduler.process_pending(morning, CFG)
    assert fake.calls == [] and fake.notes[-1] == "riassunto"
    assert memory.due_pending("2100-01-01") == []


def test_muted_rule_silences_the_event(monkeypatch, fake):
    set_triage(monkeypatch, 9)
    now = datetime(2026, 10, 5, 12, 0, tzinfo=ROME).astimezone(timezone.utc)
    rules = [Rule("newsletter", "mute")]
    action = scheduler.handle_event(Event("e1", "email", "La newsletter di ottobre"), now, CFG, rules, "")
    assert action == "log" and fake.calls == [] and fake.notes == []
    assert memory.get_event("e1")["decision"] == "log"      # tracciato comunque


def test_notification_flood_goes_to_the_digest(monkeypatch, fake):
    set_triage(monkeypatch, 6)
    now = datetime(2026, 10, 5, 12, 0, tzinfo=ROME).astimezone(timezone.utc)
    for i in range(6):
        scheduler.handle_event(Event(f"e{i}", "rss", f"notizia {i}"), now, CFG, [], "")
    assert len(fake.notes) == CFG.max_notifications_per_hour
    assert len(memory.take_digest()) == 6 - CFG.max_notifications_per_hour


def test_tick_skips_events_already_seen(monkeypatch, fake):
    set_triage(monkeypatch, 9)
    seen = []

    def watcher():
        seen.append(1)
        return [Event("e1", "email", "Contratto")]

    monkeypatch.setattr(scheduler, "WATCHERS", [watcher])
    monkeypatch.setattr(scheduler.agent, "maybe_run_briefing", lambda now: None)
    scheduler.tick(NOON)
    scheduler.tick(NOON)
    assert len(seen) == 2 and len(fake.calls) == 1


def test_tick_survives_a_broken_watcher(monkeypatch, fake):
    set_triage(monkeypatch, 9)

    def broken():
        raise RuntimeError("API giù")

    monkeypatch.setattr(scheduler, "WATCHERS", [broken, lambda: [Event("e1", "email", "Contratto")]])
    monkeypatch.setattr(scheduler.agent, "maybe_run_briefing", lambda now: None)
    scheduler.tick(NOON)
    assert len(fake.calls) == 1


def test_tick_survives_a_failing_triage(monkeypatch, fake):
    def boom(ev, context=""):
        raise RuntimeError("Anthropic giù")

    monkeypatch.setattr(scheduler, "triage", boom)
    monkeypatch.setattr(scheduler, "WATCHERS", [lambda: [Event("e1", "email", "Contratto")]])
    monkeypatch.setattr(scheduler.agent, "maybe_run_briefing", lambda now: None)
    scheduler.tick(NOON)     # non deve sollevare
    assert fake.calls == [] and fake.notes == []
    # l'evento non è perso: resta tracciato e finisce nel prossimo briefing
    assert memory.get_event("e1")["decision"] == "error"
    assert memory.take_digest() == ["(non valutato, errore tecnico) Contratto"]
