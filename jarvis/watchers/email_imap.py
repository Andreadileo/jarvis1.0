"""Watcher email via IMAP: solo mittenti VIP o parole chiave, mai tutta la casella.

Scelta: IMAP con app password invece delle API Gmail. Nessun OAuth da
mantenere e funziona con qualunque provider. La casella viene aperta in
sola lettura (BODY.PEEK): Jarvis non deve mai marcare le tue email come lette.
"""
import email
import imaplib
import logging
from datetime import datetime, timedelta, timezone
from email.header import decode_header, make_header
from email.utils import parsedate_to_datetime

from ..config import get_settings
from ..triage import Event

log = logging.getLogger("jarvis.watchers.email")

MAX_MESSAGES = 20           # tetto per tick: una casella arretrata non deve generare una valanga
DEFAULT_MAX_AGE_HOURS = 24


def _decode(raw: str | None) -> str:
    if not raw:
        return ""
    try:
        return str(make_header(decode_header(raw)))
    except Exception:
        return raw


def is_relevant(sender: str, subject: str, vips: list[str], keywords: list[str]) -> bool:
    """Un'email conta se arriva da un mittente VIP o contiene una parola chiave.

    Senza filtri configurati non conta niente: meglio muto che invadente.
    """
    sender, subject = sender.lower(), subject.lower()
    if any(vip in sender for vip in vips):
        return True
    return any(word in subject for word in keywords)


def _fetch_messages(max_age_hours: int) -> list[dict]:
    s = get_settings()
    since = (datetime.now(timezone.utc) - timedelta(hours=max_age_hours)).strftime("%d-%b-%Y")
    conn = imaplib.IMAP4_SSL(s.imap_host)
    try:
        conn.login(s.imap_user, s.imap_password)
        conn.select(s.imap_folder, readonly=True)
        status, data = conn.search(None, "UNSEEN", "SINCE", since)
        if status != "OK":
            return []
        uids = data[0].split()[-MAX_MESSAGES:]
        out = []
        for uid in uids:
            status, payload = conn.fetch(
                uid, "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE MESSAGE-ID)])"
            )
            if status != "OK" or not payload or not isinstance(payload[0], tuple):
                continue
            headers = email.message_from_bytes(payload[0][1])
            out.append(
                {
                    "id": headers.get("Message-ID") or f"uid:{uid.decode()}",
                    "sender": _decode(headers.get("From")),
                    "subject": _decode(headers.get("Subject")),
                    "date": headers.get("Date", ""),
                }
            )
        return out
    finally:
        try:
            conn.logout()
        except Exception:
            pass


def poll(now: datetime | None = None, fetch=_fetch_messages,
         max_age_hours: int = DEFAULT_MAX_AGE_HOURS) -> list[Event]:
    s = get_settings()
    if not (s.imap_host and s.imap_user):
        return []
    vips, keywords = s.vip_senders, s.keywords
    if not (vips or keywords):
        log.info("IMAP configurato ma senza VIP né parole chiave: watcher inattivo")
        return []
    now = now or datetime.now(timezone.utc)
    try:
        messages = fetch(max_age_hours)
    except Exception:
        log.exception("lettura IMAP fallita")
        return []

    events = []
    for msg in messages:
        if not is_relevant(msg["sender"], msg["subject"], vips, keywords):
            continue
        when = ""
        try:
            received = parsedate_to_datetime(msg["date"])
            if received and received.tzinfo and received < now - timedelta(hours=max_age_hours):
                continue
            when = f" Ricevuta il {received:%d/%m alle %H:%M}." if received else ""
        except (TypeError, ValueError):
            pass
        events.append(
            Event(
                id=f"mail:{msg['id']}",
                source="email",
                title=msg["subject"] or "(senza oggetto)",
                body=f"Da: {msg['sender']}.{when}",
            )
        )
    return events
