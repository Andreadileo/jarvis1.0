"""Canale Telegram: testo e, se possibile, nota vocale. Gratuito.

Setup: crea un bot con @BotFather (TELEGRAM_BOT_TOKEN), scrivigli un messaggio,
poi leggi il tuo chat id da https://api.telegram.org/bot<TOKEN>/getUpdates (TELEGRAM_CHAT_ID).
"""
import logging
from pathlib import Path

import httpx

from ..config import get_settings

log = logging.getLogger("jarvis.telegram")
API = "https://api.telegram.org/bot{token}/{method}"


def configured() -> bool:
    s = get_settings()
    return bool(s.telegram_bot_token and s.telegram_chat_id)


def _post(method: str, client: httpx.Client | None = None, **kwargs) -> bool:
    s = get_settings()
    url = API.format(token=s.telegram_bot_token, method=method)
    try:
        own = client is None
        client = client or httpx.Client(timeout=20)
        try:
            r = client.post(url, **kwargs)
        finally:
            if own:
                client.close()
        if r.status_code != 200 or not r.json().get("ok"):
            log.error("telegram %s fallito: %s %s", method, r.status_code, r.text[:200])
            return False
        return True
    except Exception:
        log.exception("telegram %s errore di rete", method)
        return False


def send_text(text: str, client: httpx.Client | None = None) -> bool:
    if not configured():
        log.warning("Telegram non configurato, messaggio solo in log: %s", text)
        return False
    return _post("sendMessage", client, data={"chat_id": get_settings().telegram_chat_id, "text": text})


def send_voice(ogg: Path, caption: str = "", client: httpx.Client | None = None) -> bool:
    if not configured():
        return False
    with ogg.open("rb") as f:
        return _post(
            "sendVoice", client,
            data={"chat_id": get_settings().telegram_chat_id, "caption": caption[:1024]},
            files={"voice": (ogg.name, f, "audio/ogg")},
        )
