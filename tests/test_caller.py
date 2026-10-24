import httpx

from jarvis import caller, memory
from jarvis.config import get_settings


def test_notify_falls_back_to_log_without_telegram():
    caller.notify("messaggio")
    assert memory.notifications_since("2000-01-01") == 1


def test_notify_uses_telegram_when_configured(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:abc")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")
    get_settings.cache_clear()
    sent = {}

    def fake_post(url, json, timeout):
        sent.update(url=url, payload=json)
        return httpx.Response(200, request=httpx.Request("POST", url))

    monkeypatch.setattr(caller.httpx, "post", fake_post)
    caller.notify("ciao")
    assert sent["payload"] == {"chat_id": "42", "text": "ciao"}
    assert "123:abc" in sent["url"]


def test_telegram_failure_does_not_propagate(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:abc")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")
    get_settings.cache_clear()
    monkeypatch.setattr(caller.httpx, "post", lambda **kw: (_ for _ in ()).throw(httpx.ConnectError("giù")))
    caller.notify("ciao")      # non deve sollevare
    assert memory.notifications_since("2000-01-01") == 1


def test_place_call_is_skipped_without_twilio():
    assert caller.place_call("motivo") is None
    assert memory.outbound_calls_since("2000-01-01") == 0


def test_place_call_falls_back_to_a_notification_on_error(monkeypatch):
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "AC1")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "t")
    monkeypatch.setenv("USER_PHONE_NUMBER", "+390000")
    monkeypatch.setenv("TWILIO_FROM_NUMBER", "+391111")
    get_settings.cache_clear()

    class Boom:
        def __init__(self, *a, **k):
            pass

        @property
        def calls(self):
            raise RuntimeError("Twilio giù")

    monkeypatch.setattr(caller, "Client", Boom)
    assert caller.place_call("il forno è acceso") is None
    assert memory.notifications_since("2000-01-01") == 1


def test_successful_call_is_logged(monkeypatch):
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "AC1")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "t")
    monkeypatch.setenv("USER_PHONE_NUMBER", "+390000")
    monkeypatch.setenv("TWILIO_FROM_NUMBER", "+391111")
    monkeypatch.setenv("PUBLIC_URL", "https://jarvis.example.com")
    get_settings.cache_clear()
    captured = {}

    class FakeCalls:
        def create(self, **kwargs):
            captured.update(kwargs)
            return type("Call", (), {"sid": "CA123"})()

    monkeypatch.setattr(caller, "Client", lambda sid, token: type("C", (), {"calls": FakeCalls()})())
    assert caller.place_call("il forno è acceso", event_id="casa:1") == "CA123"
    assert "reason=il%20forno%20%C3%A8%20acceso" in captured["url"]
    assert "event_id=casa%3A1" in captured["url"]
    assert captured["status_callback"].endswith("/twilio/status")
    assert memory.call_by_sid("CA123")["event_id"] == "casa:1"
