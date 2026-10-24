from datetime import datetime, timedelta, timezone

from jarvis import memory
from jarvis.brain import CONFIRM_REQUIRED, ToolContext, run_tool


def ctx(tmp_path):
    return ToolContext(db_path=str(tmp_path / "b.db"))


def test_save_note(tmp_path):
    c = ctx(tmp_path)
    run_tool("save_note", {"text": "targa auto AB123CD"}, c)
    assert memory.recent_notes(path=c.db_path) == ["targa auto AB123CD"]


def test_commitment_roundtrip_and_date_validation(tmp_path):
    c = ctx(tmp_path)
    assert "25/10" in run_tool("remember_commitment", {"text": "Paolo", "due_at": "2026-10-25T09:00"}, c)
    assert "non valida" in run_tool("remember_commitment", {"text": "x", "due_at": "domani"}, c).lower()
    assert "#1 Paolo" in run_tool("list_commitments", {}, c)


def test_naive_due_date_is_interpreted_in_user_timezone(tmp_path):
    """Senza fuso orario l'ora è quella di Andrea, ora legale compresa.

    Il 25 ottobre 2026 l'ora legale finisce: le 9:00 locali sono +01:00, non +02:00.
    """
    c = ctx(tmp_path)
    run_tool("remember_commitment", {"text": "estate", "due_at": "2026-07-01T09:00"}, c, tz="Europe/Rome")
    run_tool("remember_commitment", {"text": "inverno", "due_at": "2026-10-25T09:00"}, c, tz="Europe/Rome")
    offsets = [datetime.fromisoformat(r["due_at"]).utcoffset() for r in memory.open_commitments(path=c.db_path)]
    assert offsets == [timedelta(hours=2), timedelta(hours=1)]


def test_destructive_tools_need_explicit_confirmation(tmp_path):
    c = ctx(tmp_path)
    run_tool("remember_commitment", {"text": "Paolo", "due_at": "2026-10-25T09:00"}, c)

    refused = run_tool("cancel_commitment", {"commitment_id": 1}, c)
    assert "CONFERMA MANCANTE" in refused
    assert memory.open_commitments(path=c.db_path), "l'impegno non deve essere toccato"

    # anche un 'confirmed' non booleano non basta
    assert "CONFERMA MANCANTE" in run_tool("cancel_commitment", {"commitment_id": 1, "confirmed": "sì"}, c)
    assert memory.open_commitments(path=c.db_path)

    assert run_tool("cancel_commitment", {"commitment_id": 1, "confirmed": True}, c) == "Impegno cancellato."
    assert memory.open_commitments(path=c.db_path) == []


def test_outgoing_message_is_gated(monkeypatch, tmp_path):
    sent = []
    monkeypatch.setattr("jarvis.brain.caller.notify", lambda text: sent.append(text))
    c = ctx(tmp_path)
    run_tool("send_message", {"text": "ciao"}, c)
    assert sent == []
    run_tool("send_message", {"text": "ciao", "confirmed": True}, c)
    assert sent == ["ciao"]


def test_confirm_required_covers_the_outward_tools():
    assert CONFIRM_REQUIRED == {"cancel_commitment", "send_message"}


def test_mute_and_prioritize_write_rules(tmp_path):
    c = ctx(tmp_path)
    run_tool("mute_topic", {"topic": "Newsletter", "days": 7}, c)
    run_tool("prioritize_topic", {"topic": "bonifico"}, c)
    rules = memory.active_rules(datetime.now(timezone.utc).isoformat(), c.db_path)
    assert [(r["pattern"], r["action"]) for r in rules] == [
        ("newsletter", "mute"),
        ("bonifico", "always_call"),
    ]


def test_mute_without_days_is_permanent(tmp_path):
    c = ctx(tmp_path)
    run_tool("mute_topic", {"topic": "spam"}, c)
    far_future = (datetime.now(timezone.utc) + timedelta(days=3650)).isoformat()
    assert memory.active_rules(far_future, c.db_path)[0]["until"] is None


def test_rate_event_records_feedback(tmp_path):
    c = ToolContext(event_id="mail:1", source="email", urgency=8, db_path=str(tmp_path / "b.db"))
    run_tool("rate_event", {"verdict": "noise"}, c)
    assert memory.feedback_stats(c.db_path) == [
        {"source": "email", "verdict": "noise", "n": 1, "avg_urgency": 8.0}
    ]


def test_rate_event_without_an_event(tmp_path):
    assert "niente da valutare" in run_tool("rate_event", {"verdict": "noise"}, ctx(tmp_path))


def test_unknown_tool_is_reported_not_raised(tmp_path):
    assert "sconosciuto" in run_tool("lancia_missile", {}, ctx(tmp_path))
