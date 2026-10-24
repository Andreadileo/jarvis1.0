"""Canali in uscita: telefonata (Twilio) e notifica (Telegram, con fallback a log)."""
import logging
from urllib.parse import quote

import httpx
from twilio.rest import Client

from . import memory
from .config import get_settings

log = logging.getLogger("jarvis.caller")

TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"


def place_call(reason: str, event_id: str = "") -> str | None:
    """Chiama Andrea. Il motivo e l'evento viaggiano nell'URL del webhook vocale."""
    s = get_settings()
    if not (s.twilio_account_sid and s.user_phone_number):
        log.warning("Twilio non configurato, chiamata saltata: %s", reason)
        return None
    client = Client(s.twilio_account_sid, s.twilio_auth_token)
    url = f"{s.public_url}/twilio/voice?reason={quote(reason)}"
    if event_id:
        url += f"&event_id={quote(event_id)}"
    try:
        call = client.calls.create(
            to=s.user_phone_number,
            from_=s.twilio_from_number,
            url=url,
            status_callback=f"{s.public_url}/twilio/status",
            status_callback_event=["completed", "no-answer", "busy", "failed"],
        )
    except Exception:
        log.exception("chiamata fallita, ripiego sulla notifica: %s", reason)
        notify(f"(chiamata non riuscita) {reason}")
        return None
    memory.log_call("out", reason, sid=call.sid, event_id=event_id)
    return call.sid


def send_telegram(text: str) -> bool:
    s = get_settings()
    if not (s.telegram_bot_token and s.telegram_chat_id):
        return False
    try:
        resp = httpx.post(
            TELEGRAM_API.format(token=s.telegram_bot_token),
            json={"chat_id": s.telegram_chat_id, "text": text},
            timeout=10,
        )
        resp.raise_for_status()
        return True
    except Exception:
        log.exception("invio Telegram fallito")
        return False


def notify(text: str) -> None:
    """Canale non urgente. Telegram se configurato, altrimenti log."""
    channel = "telegram" if send_telegram(text) else "log"
    if channel == "log":
        log.info("NOTIFICA: %s", text)
    memory.record_notification(text, channel)
