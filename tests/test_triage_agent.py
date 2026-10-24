from datetime import datetime, timedelta, timezone

import pytest

from jarvis import agent, memory
from jarvis.triage import parse_triage, triage_context


def test_parse_triage_clamps_and_tolerates_text():
    t = parse_triage('Ecco: {"urgency": 14, "summary": "Ciao", "category": "lavoro"} fine')
    assert t.urgency == 10 and t.summary == "Ciao" and t.category == "lavoro"


def test_parse_triage_defaults_missing_fields():
    t = parse_triage('{"urgency": 3}')
    assert (t.urgency, t.summary, t.category) == (3, "", "")


def test_triage_context_is_empty_without_history():
    assert triage_context([], []) == ""


def test_triage_context_carries_rules_and_calibration():
    rules = [{"pattern": "newsletter", "action": "mute"}, {"pattern": "bonifico", "action": "always_call"}]
    stats = [
        {"source": "email", "verdict": "noise", "n": 4, "avg_urgency": 7},
        {"source": "email", "verdict": "useful", "n": 1, "avg_urgency": 9},
        {"source": "calendario", "verdict": "useful", "n": 3, "avg_urgency": 8},
    ]
    text = triage_context(rules, stats)
    assert "NON essere disturbato per: newsletter" in text
    assert "sempre importanti: bonifico" in text
    assert "Storico 'email'" in text and "Abbassa" in text
    assert "Storico 'calendario'" in text and "Non sottovalutare" in text


def test_briefing_runs_once_a_day_after_the_hour():
    tz = "Europe/Rome"
    before = datetime(2026, 10, 24, 5, 0, tzinfo=timezone.utc)   # 07:00 locali
    after = datetime(2026, 10, 24, 7, 0, tzinfo=timezone.utc)    # 09:00 locali
    assert not agent.should_run(before, None, 8, tz)
    assert agent.should_run(after, None, 8, tz)
    assert not agent.should_run(after, after.isoformat(), 8, tz)
    tomorrow = after + timedelta(days=1)
    assert agent.should_run(tomorrow, after.isoformat(), 8, tz)


def test_briefing_collects_digest_commitments_and_events(tmp_path):
    db = str(tmp_path / "a.db")
    now = datetime(2026, 10, 24, 7, 0, tzinfo=timezone.utc)

    class Ev:
        id, source, title, body = "e1", "email", "Contratto", ""

    memory.save_event(Ev(), 7, "notify", db, summary="Paolo aspetta il contratto")
    memory.save_event(type("E", (), {"id": "e2", "source": "rss", "title": "nulla", "body": ""})(), 1, "ignore", db)
    memory.queue_digest("tre email dalla banca", db)
    memory.add_commitment("rinnovare assicurazione", (now + timedelta(hours=5)).isoformat(), path=db)
    memory.add_commitment("fra un mese", (now + timedelta(days=30)).isoformat(), path=db)

    data = agent.collect(now, "Europe/Rome", db)
    assert [c["text"] for c in data["commitments"]] == ["rinnovare assicurazione"]
    assert len(data["notified"]) == 1 and len(data["silenced"]) == 1

    text = agent.render(data)
    assert "sabato 24 ottobre" in text
    assert "Paolo aspetta il contratto" in text
    assert "tre email dalla banca" in text
    assert "1 eventi filtrati" in text
    # collect non consuma: il digest resta finché il briefing non è partito davvero
    assert agent.collect(now, "Europe/Rome", db)["digest"] == ["tre email dalla banca"]


def test_briefing_without_material_says_so(tmp_path):
    data = agent.collect(datetime.now(timezone.utc), "Europe/Rome", str(tmp_path / "a.db"))
    assert "Niente di rilevante." in agent.render(data)


def test_compose_falls_back_to_raw_text_without_api_key(tmp_path):
    data = agent.collect(datetime.now(timezone.utc), "Europe/Rome", str(tmp_path / "a.db"))
    assert agent.compose(data) == agent.render(data)


def test_compose_uses_the_model_when_available():
    class FakeBlock:
        type, text = "text", "Oggi: niente di urgente."

    class FakeMessages:
        def create(self, **kwargs):
            return type("Msg", (), {"content": [FakeBlock()]})()

    fake = type("Client", (), {"messages": FakeMessages()})()
    assert agent.compose({"local_date": "x", "digest": [], "commitments": [], "notified": [],
                          "silenced": []}, fake) == "Oggi: niente di urgente."


def test_run_briefing_sends_marks_the_job_and_empties_the_digest(tmp_path):
    db = str(tmp_path / "a.db")
    sent = []
    now = datetime(2026, 10, 24, 7, 0, tzinfo=timezone.utc)
    memory.queue_digest("tre email dalla banca", db)
    agent.run_briefing(now=now, path=db, send=sent.append)
    assert "tre email dalla banca" in sent[0]
    assert memory.job_last_run(agent.JOB, db) == now.isoformat()
    assert memory.pending_digest(db) == []


def test_digest_survives_a_failing_channel(tmp_path):
    """Se il canale di notifica è giù, le voci non vanno perse."""
    db = str(tmp_path / "a.db")
    memory.queue_digest("tre email dalla banca", db)

    def broken(text):
        raise RuntimeError("Telegram giù")

    with pytest.raises(RuntimeError):
        agent.run_briefing(now=datetime.now(timezone.utc), path=db, send=broken)
    assert memory.pending_digest(db) == ["tre email dalla banca"]
