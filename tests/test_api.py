import pytest
from fastapi.testclient import TestClient
from twilio.request_validator import RequestValidator

from jarvis import memory
from jarvis.config import get_settings
from jarvis.main import app
from jarvis.watchers import inbox

TOKEN = "segreto"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("INBOUND_TOKEN", TOKEN)
    monkeypatch.setenv("PUBLIC_URL", "https://jarvis.example.com")
    get_settings.cache_clear()
    with TestClient(app) as c:      # il lifespan avvia e ferma lo scheduler
        yield c


def test_health_and_status(client):
    assert client.get("/health").json() == {"ok": True}
    body = client.get("/status").json()
    assert body["events_24h"] == 0 and body["last_briefing"] is None


def test_inbound_events_need_the_token(client):
    assert client.post("/events", json={"title": "x"}).status_code == 401
    assert client.post("/events", json={"title": "x"}, headers={"X-Jarvis-Token": "sbagliato"}).status_code == 401


def test_inbound_event_reaches_the_watcher(client):
    resp = client.post(
        "/events",
        json={"id": "1", "source": "casa", "title": "Allarme attivato", "body": "porta di servizio"},
        headers={"X-Jarvis-Token": TOKEN},
    )
    assert resp.status_code == 200
    events = inbox.poll()
    assert [(e.source, e.title) for e in events] == [("casa", "Allarme attivato")]


def test_inbound_event_requires_a_title(client):
    resp = client.post("/events", json={"body": "solo corpo"}, headers={"X-Jarvis-Token": TOKEN})
    assert resp.status_code == 422


def test_duplicate_inbound_event_is_ignored(client):
    for _ in range(2):
        client.post("/events", json={"id": "1", "title": "stesso"}, headers={"X-Jarvis-Token": TOKEN})
    assert len(inbox.poll()) == 1


def test_voice_twiml_for_an_incoming_call(client):
    xml = client.post("/twilio/voice").text
    assert "wss://jarvis.example.com/twilio/relay" in xml
    assert "dimmi pure" in xml
    assert memory.outbound_calls_since("2000-01-01") == 0   # è entrante, non conta sulla quota


def test_voice_twiml_for_an_outgoing_call_carries_reason_and_event(client):
    xml = client.post("/twilio/voice?reason=Paolo%20aspetta&event_id=mail%3A1").text
    assert "reason=Paolo%20aspetta" in xml and "event_id=mail%3A1" in xml
    assert "Ti chiamo per una cosa" in xml


def test_signature_is_enforced_when_configured(monkeypatch):
    monkeypatch.setenv("VERIFY_TWILIO_SIGNATURE", "true")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "auth-token")
    monkeypatch.setenv("PUBLIC_URL", "https://jarvis.example.com")
    get_settings.cache_clear()
    with TestClient(app) as c:
        assert c.post("/twilio/voice", data={"CallSid": "CA1"}).status_code == 403

        url = "https://jarvis.example.com/twilio/voice"
        signature = RequestValidator("auth-token").compute_signature(url, {"CallSid": "CA1"})
        ok = c.post("/twilio/voice", data={"CallSid": "CA1"}, headers={"X-Twilio-Signature": signature})
        assert ok.status_code == 200


def test_call_status_records_the_outcome_and_keeps_the_reason(client):
    memory.log_call("out", "Paolo aspetta il contratto", sid="CA1", event_id="mail:1")
    resp = client.post("/twilio/status", data={"CallSid": "CA1", "CallStatus": "no-answer"})
    assert resp.json() == {"ok": True}
    assert memory.call_by_sid("CA1")["status"] == "no-answer"
    assert memory.take_digest() == ["(chiamata senza risposta) Paolo aspetta il contratto"]


def test_answered_call_does_not_fill_the_digest(client):
    memory.log_call("out", "motivo", sid="CA2", event_id="mail:2")
    client.post("/twilio/status", data={"CallSid": "CA2", "CallStatus": "completed"})
    assert memory.take_digest() == []


def test_manual_briefing_endpoint(client, monkeypatch):
    monkeypatch.setattr("jarvis.main.agent.run_briefing", lambda: "briefing finto")
    assert client.post("/briefing", headers={"X-Jarvis-Token": TOKEN}).json() == {"text": "briefing finto"}
    assert client.post("/briefing").status_code == 401
