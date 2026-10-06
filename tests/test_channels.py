from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

from jarvis import caller, presence, speech
from jarvis.channels import telegram
from jarvis.config import get_settings
from jarvis.policy import PolicyConfig, PolicyInput, decide

ROME = ZoneInfo("Europe/Rome")
CFG = PolicyConfig()


def at(h):
    return datetime(2026, 10, 5, h, 0, tzinfo=ROME)


# --- policy: presenza al PC --------------------------------------------------

def test_at_pc_speaks_instead_of_calling_or_notifying():
    assert decide(PolicyInput(6, at(12), 0, at_pc=True), CFG) == "speak"
    assert decide(PolicyInput(9, at(12), 0, at_pc=True), CFG) == "speak"
    assert decide(PolicyInput(9, at(2), 0, at_pc=True), CFG) == "speak"   # di notte ma sveglio al PC


def test_busy_at_pc_falls_back_to_silent_notify_unless_emergency():
    assert decide(PolicyInput(9, at(12), 0, at_pc=True, busy=True), CFG) == "notify"
    assert decide(PolicyInput(10, at(12), 0, at_pc=True, busy=True), CFG) == "speak"


def test_low_urgency_unchanged_by_presence():
    assert decide(PolicyInput(1, at(12), 0, at_pc=True), CFG) == "ignore"
    assert decide(PolicyInput(4, at(12), 0, at_pc=True), CFG) == "log"


# --- presenza: logica pura ---------------------------------------------------

def test_presence_evaluate():
    ev = presence.evaluate
    assert ev(10, presence.QUNS_ACCEPTS_NOTIFICATIONS, False, 120) == presence.Presence(True, False, "attivo")
    assert not ev(500, presence.QUNS_ACCEPTS_NOTIFICATIONS, False, 120).at_pc
    assert not ev(None, None, False, 120).at_pc
    assert not ev(10, presence.QUNS_NOT_PRESENT, False, 120).at_pc
    assert ev(10, presence.QUNS_ACCEPTS_NOTIFICATIONS, True, 120).busy
    assert ev(10, presence.QUNS_RUNNING_D3D_FULL_SCREEN, False, 120).busy
    assert ev(10, presence.QUNS_PRESENTATION_MODE, False, 120).busy
    assert ev(10, presence.QUNS_QUIET_TIME, False, 120) == presence.Presence(True, False, "attivo")


def test_presence_detect_outside_windows_is_absent(monkeypatch):
    monkeypatch.setattr(presence.sys, "platform", "linux")
    assert presence.detect() == presence.Presence(False, False, "non Windows")


# --- telegram ----------------------------------------------------------------

def _settings(monkeypatch, **kw):
    s = get_settings()
    for k, v in kw.items():
        monkeypatch.setattr(s, k, v)


def test_telegram_unconfigured_is_noop(monkeypatch):
    _settings(monkeypatch, telegram_bot_token="", telegram_chat_id="")
    assert telegram.send_text("ciao") is False


def test_telegram_send_text_and_voice(monkeypatch, tmp_path):
    _settings(monkeypatch, telegram_bot_token="T", telegram_chat_id="42")
    calls = []

    def handler(req: httpx.Request):
        calls.append(req)
        return httpx.Response(200, json={"ok": True})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    assert telegram.send_text("ciao", client)
    assert calls[0].url.path == "/botT/sendMessage"
    assert b"chat_id=42" in calls[0].content and b"ciao" in calls[0].content

    ogg = tmp_path / "v.ogg"
    ogg.write_bytes(b"OggS")
    assert telegram.send_voice(ogg, "ciao", client)
    assert calls[1].url.path == "/botT/sendVoice"
    assert b"audio/ogg" in calls[1].content


def test_telegram_api_error_returns_false(monkeypatch):
    _settings(monkeypatch, telegram_bot_token="T", telegram_chat_id="42")
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(401, json={"ok": False})))
    assert telegram.send_text("ciao", client) is False


# --- caller: instradamento ---------------------------------------------------

def test_notify_sends_text_and_voice_note_when_possible(monkeypatch, tmp_path):
    _settings(monkeypatch, telegram_bot_token="T", telegram_chat_id="42", telegram_voice_notes=True)
    sent = []
    wav, ogg = tmp_path / "a.wav", tmp_path / "a.ogg"
    wav.write_bytes(b"RIFF"); ogg.write_bytes(b"OggS")
    monkeypatch.setattr(telegram, "send_text", lambda t, client=None: sent.append(("text", t)) or True)
    monkeypatch.setattr(telegram, "send_voice", lambda p, c="", client=None: sent.append(("voice", p.name)) or True)
    monkeypatch.setattr(speech, "can_make_voice_note", lambda: True)
    monkeypatch.setattr(speech, "synthesize", lambda t: wav)
    monkeypatch.setattr(speech, "to_ogg", lambda w: ogg)
    caller.notify("prova")
    assert sent == [("text", "prova"), ("voice", "a.ogg")]
    assert not wav.exists() and not ogg.exists()   # file temporanei ripuliti


def test_notify_text_only_without_tts(monkeypatch):
    _settings(monkeypatch, telegram_bot_token="T", telegram_chat_id="42", telegram_voice_notes=True)
    sent = []
    monkeypatch.setattr(telegram, "send_text", lambda t, client=None: sent.append(t) or True)
    monkeypatch.setattr(telegram, "send_voice", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no voice")))
    monkeypatch.setattr(speech, "can_make_voice_note", lambda: False)
    monkeypatch.setattr(speech, "synthesize", lambda t: (_ for _ in ()).throw(AssertionError("no tts")))
    caller.notify("prova")
    assert sent == ["prova"]


def test_speak_falls_back_to_telegram(monkeypatch):
    sent = []
    monkeypatch.setattr(speech, "speak", lambda t: False)
    monkeypatch.setattr(caller, "notify", lambda t: sent.append(t))
    caller.speak("ehi")
    assert sent == ["ehi"]


def test_speech_without_backend_returns_none(monkeypatch):
    monkeypatch.setattr(speech.sys, "platform", "linux")
    _settings(monkeypatch, piper_path="", piper_model="")
    assert speech.synthesize("ciao") is None


def test_to_ogg_without_ffmpeg_returns_none(monkeypatch, tmp_path):
    monkeypatch.setattr(speech.shutil, "which", lambda name: None)
    assert speech.to_ogg(tmp_path / "a.wav") is None


# --- scheduler: dal watcher al canale ----------------------------------------

def test_tick_routes_speak_and_notify(monkeypatch, tmp_path):
    from jarvis import scheduler
    from jarvis.triage import Event, Triage

    _settings(monkeypatch, db_path=str(tmp_path / "t.db"))
    events = [Event(id="e1", source="t", title="urgente"), Event(id="e2", source="t", title="niente")]
    out = []
    monkeypatch.setattr(scheduler, "WATCHERS", [lambda: events])
    monkeypatch.setattr(scheduler, "triage", lambda ev: Triage(10 if ev.title == "urgente" else 1, ev.title))  # 10: ignora quiet hours
    monkeypatch.setattr(caller, "speak", lambda t: out.append(("speak", t)))
    monkeypatch.setattr(caller, "notify", lambda t: out.append(("notify", t)))
    monkeypatch.setattr(caller, "place_call", lambda t: out.append(("call", t)) or None)

    monkeypatch.setattr(scheduler.presence, "detect", lambda *_: presence.Presence(True, False))
    scheduler.tick()
    assert out == [("speak", "urgente")]

    out.clear()
    events[0] = Event(id="e3", source="t", title="urgente")
    monkeypatch.setattr(scheduler.presence, "detect", lambda *_: presence.Presence(False, False))
    scheduler.tick()
    assert out == [("call", "urgente"), ("notify", "urgente")]   # Twilio assente -> Telegram
