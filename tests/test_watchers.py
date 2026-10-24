import json
from datetime import datetime, timedelta, timezone

from jarvis import memory
from jarvis.config import get_settings
from jarvis.watchers import calendar_ics, commitments, email_imap, inbox, reminders

ICS = """BEGIN:VCALENDAR
BEGIN:VEVENT
UID:abc@google.com
DTSTART;TZID=Europe/Rome:20261005T143000
SUMMARY:Call con Paolo
DESCRIPTION:Parliamo del
  contratto
LOCATION:Meet
END:VEVENT
BEGIN:VEVENT
UID:annullato
DTSTART;TZID=Europe/Rome:20261005T144500
SUMMARY:Riunione annullata
STATUS:CANCELLED
END:VEVENT
BEGIN:VEVENT
UID:domani
DTSTART:20261006T080000Z
SUMMARY:Fuori finestra
END:VEVENT
END:VCALENDAR"""


def test_reminders_watcher(tmp_path):
    f = tmp_path / "r.json"
    f.write_text(json.dumps([
        {"id": "a", "at": "2026-10-05T09:00:00+02:00", "title": "passato"},
        {"id": "b", "at": "2026-10-05T18:00:00+02:00", "title": "futuro"},
    ]))
    evs = reminders.poll(now=datetime(2026, 10, 5, 10, 0, tzinfo=timezone.utc), path=f)
    assert [e.id for e in evs] == ["reminder:a"]


def test_ics_parsing_handles_folding_and_timezones():
    items = calendar_ics.parse_ics(ICS)
    assert len(items) == 3
    first = items[0]
    assert first["summary"] == "Call con Paolo"
    assert first["description"] == "Parliamo del contratto"
    assert first["start"].hour == 14 and first["start"].utcoffset() == timedelta(hours=2)


def test_calendar_watcher_only_fires_inside_window():
    now = datetime(2026, 10, 5, 12, 10, tzinfo=timezone.utc)   # 14:10 a Roma
    evs = calendar_ics.poll(now=now, urls=["x"], lookahead_minutes=45, fetch=lambda u: ICS)
    assert len(evs) == 1
    assert evs[0].title == "Call con Paolo"
    assert "tra 20 minuti" in evs[0].body


def test_calendar_watcher_survives_unreachable_feed():
    def boom(url):
        raise RuntimeError("rete giù")

    assert calendar_ics.poll(now=datetime.now(timezone.utc), urls=["x"], fetch=boom) == []


def test_calendar_watcher_disabled_without_urls():
    assert calendar_ics.poll(urls=[]) == []


def test_email_relevance():
    assert email_imap.is_relevant("Paolo <p@acme.com>", "Ciao", ["@acme.com"], [])
    assert email_imap.is_relevant("x@y.z", "URGENTE: bonifico", [], ["urgente"])
    assert not email_imap.is_relevant("x@y.z", "Newsletter", ["@acme.com"], ["urgente"])


def test_email_watcher_filters_and_is_off_without_config(monkeypatch):
    assert email_imap.poll() == []    # IMAP non configurato

    monkeypatch.setenv("IMAP_HOST", "imap.example.com")
    monkeypatch.setenv("IMAP_USER", "me@example.com")
    monkeypatch.setenv("IMAP_VIP_SENDERS", "@acme.com")
    get_settings.cache_clear()
    messages = [
        {"id": "<1@acme.com>", "sender": "Paolo <p@acme.com>", "subject": "Contratto", "date": ""},
        {"id": "<2@spam.com>", "sender": "promo@spam.com", "subject": "Offerta", "date": ""},
    ]
    evs = email_imap.poll(fetch=lambda hours: messages)
    assert [e.id for e in evs] == ["mail:<1@acme.com>"]
    assert evs[0].source == "email"


def test_email_watcher_swallows_imap_errors(monkeypatch):
    monkeypatch.setenv("IMAP_HOST", "imap.example.com")
    monkeypatch.setenv("IMAP_USER", "me@example.com")
    monkeypatch.setenv("IMAP_KEYWORDS", "urgente")
    get_settings.cache_clear()

    def boom(hours):
        raise OSError("connessione rifiutata")

    assert email_imap.poll(fetch=boom) == []


def test_commitments_fire_once(tmp_path):
    db = str(tmp_path / "c.db")
    past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    memory.add_commitment("chiamare il commercialista", past, path=db)
    memory.add_commitment("non ancora", future, path=db)
    assert [e.title for e in commitments.poll(path=db)] == ["chiamare il commercialista"]
    assert commitments.poll(path=db) == []


def test_inbox_drains_once(tmp_path):
    db = str(tmp_path / "i.db")
    memory.push_inbox("ext:1", "casa", "Allarme attivato", path=db)
    evs = inbox.poll(path=db)
    assert [e.id for e in evs] == ["ext:1"] and evs[0].source == "casa"
    assert inbox.poll(path=db) == []
