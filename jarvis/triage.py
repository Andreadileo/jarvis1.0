"""Il modello economico valuta quanto un evento è urgente per Andrea.

Il triage impara: le regole esplicite dell'utente e lo storico dei feedback
("era rumore" / "era utile") entrano nel system prompt come contesto.
"""
import json
from dataclasses import dataclass

import anthropic

from .config import get_settings

TRIAGE_PROMPT = """Sei il filtro di un assistente personale. Valuta l'evento e rispondi SOLO con JSON:
{"urgency": <0-10>, "summary": "<una frase in italiano da dire all'utente>", "category": "<una parola>"}
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
    category: str = ""


def parse_triage(text: str) -> Triage:
    start, end = text.find("{"), text.rfind("}")
    data = json.loads(text[start : end + 1])
    urgency = max(0, min(10, int(data.get("urgency", 0))))
    return Triage(
        urgency=urgency,
        summary=str(data.get("summary", "")),
        category=str(data.get("category", "")),
    )


def triage_context(rules: list[dict], stats: list[dict]) -> str:
    """Contesto appreso da aggiungere al prompt di triage.

    Deterministico e testabile: nessuna chiamata al modello qui dentro.
    """
    lines: list[str] = []
    muted = [r["pattern"] for r in rules if r.get("action") == "mute"]
    if muted:
        lines.append("Andrea ha chiesto di NON essere disturbato per: " + ", ".join(muted) + " (urgency <= 2).")
    priority = [r["pattern"] for r in rules if r.get("action") in ("always_call", "always_notify")]
    if priority:
        lines.append("Andrea considera sempre importanti: " + ", ".join(priority) + ".")

    by_source: dict[str, dict[str, int]] = {}
    for row in stats:
        by_source.setdefault(row["source"], {})[row["verdict"]] = int(row["n"])
    for source, verdicts in sorted(by_source.items()):
        noise, useful = verdicts.get("noise", 0), verdicts.get("useful", 0)
        if noise > useful:
            lines.append(f"Storico '{source}': {noise} valutazioni di troppo contro {useful} azzeccate. Abbassa.")
        elif useful > noise and useful:
            lines.append(f"Storico '{source}': {useful} volte era davvero importante. Non sottovalutare.")
    if not lines:
        return ""
    return "\nCosa hai imparato finora su Andrea:\n" + "\n".join(f"- {line}" for line in lines)


def triage(event: Event, client: anthropic.Anthropic | None = None, context: str = "") -> Triage:
    s = get_settings()
    client = client or anthropic.Anthropic(api_key=s.anthropic_api_key)
    msg = client.messages.create(
        model=s.triage_model,
        max_tokens=200,
        system=TRIAGE_PROMPT + context,
        messages=[{"role": "user", "content": f"Fonte: {event.source}\nTitolo: {event.title}\n{event.body}"}],
    )
    try:
        return parse_triage(msg.content[0].text)
    except (ValueError, KeyError, IndexError):
        return Triage(urgency=0, summary="")
