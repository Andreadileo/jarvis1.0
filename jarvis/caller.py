"""Canali in uscita: telefonata (Twilio, opzionale), voce locale, Telegram."""
import logging
from urllib.parse import quote

from twilio.rest import Client

from . import memory, speech
from .channels import telegram
from .config import get_settings

log = logging.getLogger("jarvis.caller")


def place_call(reason: str) -> str | None:
    s = get_settings()
    if not (s.twilio_account_sid and s.user_phone_number):
        log.warning("Twilio non configurato, chiamata saltata: %s", reason)
        return None
    client = Client(s.twilio_account_sid, s.twilio_auth_token)
    call = client.calls.create(
        to=s.user_phone_number,
        from_=s.twilio_from_number,
        url=f"{s.public_url}/twilio/voice?reason={quote(reason)}",
    )
    memory.log_call("out", reason)
    return call.sid


def notify(text: str) -> None:
    """Telegram: testo sempre, nota vocale se c'è un sintetizzatore e ffmpeg."""
    log.info("NOTIFICA: %s", text)
    if not telegram.configured():
        return
    telegram.send_text(text)
    if get_settings().telegram_voice_notes and speech.can_make_voice_note():
        wav = speech.synthesize(text)
        if wav is None:
            return
        try:
            ogg = speech.to_ogg(wav)
            if ogg is not None:
                telegram.send_voice(ogg)
                ogg.unlink(missing_ok=True)
        finally:
            wav.unlink(missing_ok=True)


def speak(text: str) -> None:
    """Voce dalle casse del PC; se non riesce, ripiega su Telegram."""
    log.info("VOCE: %s", text)
    if not speech.speak(text):
        notify(text)
