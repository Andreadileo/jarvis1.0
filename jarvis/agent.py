"""Iniziativa propria: il briefing quotidiano.

È l'unico punto in cui il modello "pensa" senza che sia successo niente, e
succede una volta al giorno: un heartbeat LLM continuo costerebbe molto e
produrrebbe interruzioni inutili (vedi README). Tutto ciò che è maturato
sotto la soglia di notifica viene accorpato qui invece di vibrare dieci volte.
"""
import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import anthropic

from . import caller, memory
from .dates import day_it
from .config import get_settings

log = logging.getLogger("jarvis.agent")

JOB = "briefing"

BRIEFING_PROMPT = """Sei Jarvis, l'assistente personale di Andrea.
Scrivi il briefing del mattino: massimo 6 righe, italiano, tono asciutto e concreto.
Prima le cose che richiedono una decisione oggi, poi il resto. Niente markdown, niente saluti lunghi.
Se non c'è niente di rilevante, dillo in una riga sola."""


def should_run(now: datetime, last_run_iso: str | None, hour: int, tz: str) -> bool:
    """Vero se oggi, ora locale, il briefing è dovuto e non è ancora stato fatto."""
    local = now.astimezone(ZoneInfo(tz))
    if local.hour < hour:
        return False
    if not last_run_iso:
        return True
    try:
        last_local = datetime.fromisoformat(last_run_iso).astimezone(ZoneInfo(tz))
    except ValueError:
        return True
    return last_local.date() < local.date()


def collect(now: datetime, tz: str, path: str | None = None) -> dict:
    """Materiale del briefing. Deterministico: nessuna chiamata al modello."""
    since = (now - timedelta(hours=24)).isoformat()
    events = memory.events_since(since, path)
    horizon = (now + timedelta(hours=24)).isoformat()
    return {
        "digest": memory.pending_digest(path),
        "commitments": [
            c for c in memory.open_commitments(limit=20, path=path) if c["due_at"] <= horizon
        ],
        "notified": [e for e in events if e["decision"] in ("notify", "call")],
        "silenced": [e for e in events if e["decision"] in ("log", "ignore")],
        "local_date": day_it(now.astimezone(ZoneInfo(tz))),
    }


def render(data: dict) -> str:
    """Versione testuale del contesto, usata sia per il prompt sia come fallback."""
    lines = [f"Briefing di {data['local_date']}."]
    for c in data["commitments"]:
        when = datetime.fromisoformat(c["due_at"]).strftime("%d/%m %H:%M")
        lines.append(f"- Impegno entro {when}: {c['text']}")
    for e in data["notified"]:
        lines.append(f"- Già segnalato ({e['source']}): {e['summary'] or e['title']}")
    for item in data["digest"]:
        lines.append(f"- In sospeso: {item}")
    if len(data["silenced"]) > 0:
        lines.append(f"- Altri {len(data['silenced'])} eventi filtrati senza disturbarti.")
    if len(lines) == 1:
        lines.append("Niente di rilevante.")
    return "\n".join(lines)


def compose(data: dict, client: anthropic.Anthropic | None = None) -> str:
    """Fa riscrivere il briefing al modello; se non è possibile, usa il testo grezzo."""
    raw = render(data)
    s = get_settings()
    if not s.anthropic_api_key and client is None:
        return raw
    try:
        client = client or anthropic.Anthropic(api_key=s.anthropic_api_key)
        msg = client.messages.create(
            model=s.chat_model,
            max_tokens=400,
            system=BRIEFING_PROMPT,
            messages=[{"role": "user", "content": raw}],
        )
        text = "".join(b.text for b in msg.content if b.type == "text").strip()
        return text or raw
    except Exception:
        log.exception("briefing non generato dal modello, invio il testo grezzo")
        return raw


def run_briefing(now: datetime | None = None, client: anthropic.Anthropic | None = None,
                 path: str | None = None, send=caller.notify) -> str:
    s = get_settings()
    now = now or datetime.now(timezone.utc)
    data = collect(now, s.timezone, path)
    text = compose(data, client)
    send(text)
    # il digest si svuota solo a invio avvenuto: se il canale è giù, le voci restano
    memory.take_digest(path)
    memory.mark_job_run(JOB, now.isoformat(), path)
    return text


def maybe_run_briefing(now: datetime | None = None, path: str | None = None, **kwargs) -> str | None:
    s = get_settings()
    if not s.briefing_enabled:
        return None
    now = now or datetime.now(timezone.utc)
    if not should_run(now, memory.job_last_run(JOB, path), s.briefing_hour, s.timezone):
        return None
    return run_briefing(now, path=path, **kwargs)
