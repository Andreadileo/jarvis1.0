"""Il modello economico valuta quanto un evento è urgente per Andrea."""
import json
from dataclasses import dataclass

import anthropic

from .config import get_settings

TRIAGE_PROMPT = """Sei il filtro di un assistente personale. Valuta l'evento e rispondi SOLO con JSON:
{"urgency": <0-10>, "summary": "<una frase in italiano da dire all'utente>"}
Scala: 0-2 irrilevante, 3-4 da registrare, 5-7 da notificare, 8-9 merita una telefonata, 10 emergenza.
Sii severo: le telefonate disturbano. Nel dubbio, abbassa il punteggio."""


@dataclass
class Event:
    id: str
    source: str
    title: str
    body: str = ""


@dataclass
class Triage:
    urgency: int
    summary: str


def parse_triage(text: str) -> Triage:
    start, end = text.find("{"), text.rfind("}")
    data = json.loads(text[start : end + 1])
    urgency = max(0, min(10, int(data.get("urgency", 0))))
    return Triage(urgency=urgency, summary=str(data.get("summary", "")))


def triage(event: Event, client: anthropic.Anthropic | None = None) -> Triage:
    s = get_settings()
    client = client or anthropic.Anthropic(api_key=s.anthropic_api_key)
    msg = client.messages.create(
        model=s.triage_model,
        max_tokens=200,
        system=TRIAGE_PROMPT,
        messages=[{"role": "user", "content": f"Fonte: {event.source}\nTitolo: {event.title}\n{event.body}"}],
    )
    try:
        return parse_triage(msg.content[0].text)
    except (ValueError, KeyError, IndexError):
        return Triage(urgency=0, summary="")
