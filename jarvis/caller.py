"""Canali in uscita: telefonata (Twilio) e notifica."""
import logging
from urllib.parse import quote

from twilio.rest import Client

from . import memory
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
    # TODO: sostituire con Telegram / push. Per ora solo log.
    log.info("NOTIFICA: %s", text)
