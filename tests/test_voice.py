"""Punti 2-4: streaming + interrupt, firma Twilio, /debug/call. Twilio e Anthropic sono mock."""
import asyncio
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from twilio.request_validator import RequestValidator

from jarvis import brain, caller, config
from jarvis import main as main_mod

AUTH_TOKEN = "test-auth-token"
PUBLIC_URL = "https://jarvis.example.test"


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", AUTH_TOKEN)
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "ACtest")
    monkeypatch.setenv("TWILIO_FROM_NUMBER", "+390000000001")
    monkeypatch.setenv("USER_PHONE_NUMBER", "+390000000002")
    monkeypatch.setenv("PUBLIC_URL", PUBLIC_URL)
    monkeypatch.setenv("DEBUG_TOKEN", "dbg-secret")
    monkeypatch.setenv("DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.delenv("TWILIO_SKIP_SIGNATURE_CHECK", raising=False)
    config.get_settings.cache_clear()
    yield monkeypatch
    config.get_settings.cache_clear()


@pytest.fixture
def client(env):
    return TestClient(main_mod.app)  # senza `with`: lo scheduler non parte


# ---------- mock Anthropic ----------

class FakeStream:
    """Imita `async with client.messages.stream(...) as s` del SDK Anthropic."""

    def __init__(self, tokens, final, delay=0.0):
        self.tokens, self.final, self.delay = tokens, final, delay

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    @property
    async def text_stream(self):
        for t in self.tokens:
            yield t
            if self.delay:
                await asyncio.sleep(self.delay)

    async def get_final_message(self):
        return self.final


def text_msg(text):
    return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text=text)])


class FakeAnthropic:
    def __init__(self, *streams):
        self.streams, self.calls = list(streams), []
        self.messages = self

    def stream(self, **kwargs):
        self.calls.append(kwargs)
        return self.streams.pop(0)


def make_convo(*streams):
    fake = FakeAnthropic(*streams)
    convo = brain.Conversation(client=fake)
    return convo, fake


# ---------- 2. streaming ----------

def test_stream_yields_tokens_in_order(env):
    convo, _ = make_convo(FakeStream(["Ciao", " Andrea"], text_msg("Ciao Andrea")))
    tokens = asyncio.run(_collect(convo.stream("ehi")))
    assert tokens == ["Ciao", " Andrea"]
    assert convo.messages[-1]["role"] == "assistant"


async def _collect(agen):
    return [t async for t in agen]


def test_stream_runs_tool_then_continues(env, monkeypatch):
    saved = []
    monkeypatch.setattr(brain.memory, "add_note", lambda text, path=None: saved.append(text))
    tool_msg = SimpleNamespace(
        stop_reason="tool_use",
        content=[SimpleNamespace(type="tool_use", id="tu1", name="save_note", input={"text": "latte"})],
    )
    convo, fake = make_convo(FakeStream([], tool_msg), FakeStream(["Fatto"], text_msg("Fatto")))
    assert asyncio.run(_collect(convo.stream("ricorda il latte"))) == ["Fatto"]
    assert saved == ["latte"]
    assert convo.messages[2]["content"][0] == {"type": "tool_result", "tool_use_id": "tu1", "content": "Nota salvata."}
    assert len(fake.calls) == 2


def test_relay_streams_tokens_last_flag(client, monkeypatch):
    fake = FakeAnthropic(FakeStream(["Ciao", " a", " te"], text_msg("Ciao a te")))
    monkeypatch.setattr(main_mod, "Conversation", lambda reason=None: brain.Conversation(reason=reason, client=fake))
    with client.websocket_connect("/twilio/relay") as ws:
        ws.send_json({"type": "setup", "callSid": "CA1", "customParameters": {}})
        ws.send_json({"type": "prompt", "voicePrompt": "ehi", "lang": "it-IT", "last": True})
        got = [ws.receive_json() for _ in range(3)]
    assert [m["token"] for m in got] == ["Ciao", " a", " te"]
    assert [m["last"] for m in got] == [False, False, True]
    assert all(m["type"] == "text" for m in got)


def test_relay_outbound_opens_with_reason(client, monkeypatch):
    fake = FakeAnthropic(FakeStream(["Pronto"], text_msg("Pronto")))
    convos = []

    def factory(reason=None):
        convos.append(brain.Conversation(reason=reason, client=fake))
        return convos[-1]

    monkeypatch.setattr(main_mod, "Conversation", factory)
    with client.websocket_connect("/twilio/relay") as ws:
        ws.send_json({"type": "setup", "callSid": "CA2", "customParameters": {"reason": "il forno è acceso"}})
        assert ws.receive_json() == {"type": "text", "token": "Pronto", "last": True}
    assert convos[0].reason == "il forno è acceso"
    assert "Motivo di questa chiamata: il forno è acceso" in fake.calls[0]["system"]


def test_relay_interrupt_cancels_and_truncates_history(client, monkeypatch):
    slow = FakeStream(["Uno", " due", " tre", " quattro"], text_msg("Uno due tre quattro"), delay=5)
    fake = FakeAnthropic(slow, FakeStream(["Ok"], text_msg("Ok")))
    monkeypatch.setattr(main_mod, "Conversation", lambda reason=None: brain.Conversation(reason=reason, client=fake))
    with client.websocket_connect("/twilio/relay") as ws:
        ws.send_json({"type": "setup", "callSid": "CA3"})
        ws.send_json({"type": "prompt", "voicePrompt": "conta", "last": True})
        # Il primo token resta in attesa del successivo (serve a marcare last=true), quindi non arriva nulla:
        # l'interrupt deve annullare la generazione in corso prima del secondo token.
        ws.send_json({"type": "interrupt", "utteranceUntilInterrupt": "Uno", "durationUntilInterruptMs": "300"})
        ws.send_json({"type": "prompt", "voicePrompt": "basta", "last": True})
        assert ws.receive_json() == {"type": "text", "token": "Ok", "last": True}
    history = fake.calls[1]["messages"][:3]  # ciò che Claude ha visto alla seconda chiamata
    assert [m["role"] for m in history] == ["user", "assistant", "user"]
    assert history[1]["content"] == "Uno"
    assert history[2]["content"] == "basta"


# ---------- 3. firma Twilio ----------

def _signed(path, params):
    url = PUBLIC_URL + path
    return url, {"X-Twilio-Signature": RequestValidator(AUTH_TOKEN).compute_signature(url, params)}


def test_voice_accepts_valid_signature(client):
    params = {"CallSid": "CA1", "From": "+390000000002"}
    _, headers = _signed("/twilio/voice", params)
    r = client.post("/twilio/voice", data=params, headers=headers)
    assert r.status_code == 200
    body = r.text
    assert 'language="it-IT"' in body and 'voice="Bianca-Neural"' in body and 'ttsProvider="Amazon"' in body
    assert 'url="wss://jarvis.example.test/twilio/relay"' in body
    assert "<Parameter" not in body


def test_voice_outbound_passes_reason_as_parameter(client):
    params = {"CallSid": "CA1"}
    _, headers = _signed("/twilio/voice?reason=forno+acceso", params)
    r = client.post("/twilio/voice?reason=forno+acceso", data=params, headers=headers)
    assert r.status_code == 200
    assert '<Parameter name="reason" value="forno acceso" />' in r.text


def test_voice_rejects_bad_or_missing_signature(client):
    params = {"CallSid": "CA1"}
    assert client.post("/twilio/voice", data=params).status_code == 403
    _, headers = _signed("/twilio/voice", {"CallSid": "ALTRO"})
    assert client.post("/twilio/voice", data=params, headers=headers).status_code == 403


def test_voice_signature_check_can_be_skipped_for_dev(client, env):
    env.setenv("TWILIO_SKIP_SIGNATURE_CHECK", "true")
    config.get_settings.cache_clear()
    assert client.post("/twilio/voice", data={"CallSid": "CA1"}).status_code == 200


# ---------- 4. /debug/call ----------

class FakeTwilioClient:
    created = []

    def __init__(self, sid, token):
        self.calls = self

    def create(self, **kwargs):
        self.created.append(kwargs)
        return SimpleNamespace(sid="CAtest123")


def test_debug_call_requires_token(client, monkeypatch):
    monkeypatch.setattr(caller, "Client", FakeTwilioClient)
    assert client.post("/debug/call").status_code == 403
    assert client.post("/debug/call", headers={"X-Debug-Token": "sbagliato"}).status_code == 403
    assert FakeTwilioClient.created == []


def test_debug_call_places_call(client, monkeypatch):
    FakeTwilioClient.created.clear()
    monkeypatch.setattr(caller, "Client", FakeTwilioClient)
    r = client.post("/debug/call", headers={"X-Debug-Token": "dbg-secret"})
    assert r.status_code == 200 and r.json() == {"call_sid": "CAtest123"}
    (call,) = FakeTwilioClient.created
    assert call["to"] == "+390000000002" and call["from_"] == "+390000000001"
    assert call["url"].startswith(PUBLIC_URL + "/twilio/voice?reason=")


def test_debug_call_disabled_without_token(client, env):
    env.setenv("DEBUG_TOKEN", "")
    config.get_settings.cache_clear()
    assert client.post("/debug/call", headers={"X-Debug-Token": ""}).status_code == 403
