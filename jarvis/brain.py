"""Conversazione vocale con Claude, con tool locali.

I tool che agiscono verso l'esterno o cancellano richiedono una conferma
esplicita: il controllo è nel codice (`CONFIRM_REQUIRED`), non nel prompt,
perché un prompt si può aggirare parlando.
"""
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import anthropic

from . import caller, memory
from .config import get_settings
from .dates import long_it

log = logging.getLogger("jarvis.brain")

SYSTEM = """Sei Jarvis, l'assistente personale di Andrea. Parli al telefono, in italiano.
Frasi brevi e naturali, niente elenchi o markdown: tutto verrà letto ad alta voce.
Se hai chiamato tu, vai subito al motivo della chiamata.
Prima di qualsiasi azione irreversibile chiedi conferma esplicita ad alta voce,
poi richiama lo stesso tool con confirmed=true.
Se Andrea dice che una cosa non era importante, registra il giudizio con rate_event:
serve a non disturbarlo più per cose simili.
Ora locale: {now}.
Impegni aperti:
{commitments}
Note salvate:
{notes}"""

TOOLS = [
    {
        "name": "save_note",
        "description": "Salva una nota che Andrea chiede di ricordare, senza scadenza.",
        "input_schema": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
    },
    {
        "name": "remember_commitment",
        "description": (
            "Programma un promemoria: Jarvis tornerà a farsi vivo a quella data e ora. "
            "Usalo per 'ricordamelo domani', 'richiamami lunedì'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Cosa ricordare, in italiano"},
                "due_at": {
                    "type": "string",
                    "description": "Data e ora in formato ISO 8601, es. 2026-10-25T09:00 (ora locale)",
                },
            },
            "required": ["text", "due_at"],
        },
    },
    {
        "name": "list_commitments",
        "description": "Elenca gli impegni ancora aperti con le relative scadenze.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "cancel_commitment",
        "description": "Cancella un impegno. Azione irreversibile: serve conferma a voce.",
        "input_schema": {
            "type": "object",
            "properties": {
                "commitment_id": {"type": "integer"},
                "confirmed": {"type": "boolean", "description": "true solo dopo un sì esplicito di Andrea"},
            },
            "required": ["commitment_id"],
        },
    },
    {
        "name": "mute_topic",
        "description": "Smetti di disturbare Andrea per un argomento. Esempio: 'newsletter', 'fatture'.",
        "input_schema": {
            "type": "object",
            "properties": {
                "topic": {"type": "string"},
                "days": {"type": "integer", "description": "Per quanti giorni. 0 = per sempre"},
            },
            "required": ["topic"],
        },
    },
    {
        "name": "prioritize_topic",
        "description": "Segna un argomento come sempre degno di una telefonata.",
        "input_schema": {
            "type": "object",
            "properties": {"topic": {"type": "string"}},
            "required": ["topic"],
        },
    },
    {
        "name": "rate_event",
        "description": (
            "Registra se il motivo di questa chiamata meritava davvero di disturbare Andrea. "
            "verdict: 'useful' oppure 'noise'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"verdict": {"type": "string", "enum": ["useful", "noise"]}},
            "required": ["verdict"],
        },
    },
    {
        "name": "send_message",
        "description": (
            "Manda ad Andrea un messaggio scritto sul canale di notifica (link, indirizzi, numeri "
            "che a voce si perdono). Azione verso l'esterno: serve conferma."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {"type": "string"},
                "confirmed": {"type": "boolean", "description": "true solo dopo un sì esplicito di Andrea"},
            },
            "required": ["text"],
        },
    },
]

# Applicato in codice: nessun prompt può farne a meno.
CONFIRM_REQUIRED = {"cancel_commitment", "send_message"}


@dataclass
class ToolContext:
    """Da dove nasce questa conversazione: serve a rate_event."""

    event_id: str = ""
    source: str = ""
    urgency: int = 0
    db_path: str | None = None


def _parse_due(value: str, tz: str) -> str | None:
    """Accetta ISO con o senza fuso; senza fuso assume l'ora locale di Andrea."""
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ZoneInfo(tz))
    return parsed.isoformat()


def run_tool(name: str, args: dict, ctx: ToolContext | None = None, tz: str = "Europe/Rome") -> str:
    ctx = ctx or ToolContext()
    path = ctx.db_path

    if name in CONFIRM_REQUIRED and args.get("confirmed") is not True:
        return (
            "CONFERMA MANCANTE: non ho eseguito niente. Chiedi ad Andrea a voce se vuole procedere "
            "e, solo se risponde di sì, richiama questo tool con confirmed=true."
        )

    if name == "save_note":
        memory.add_note(args["text"], path)
        return "Nota salvata."

    if name == "remember_commitment":
        due = _parse_due(str(args.get("due_at", "")), tz)
        if not due:
            return "Data non valida: usa il formato 2026-10-25T09:00."
        memory.add_commitment(args["text"], due, "voce", path)
        return f"Promemoria programmato per {datetime.fromisoformat(due):%d/%m alle %H:%M}."

    if name == "list_commitments":
        rows = memory.open_commitments(path=path)
        if not rows:
            return "Nessun impegno aperto."
        return "; ".join(
            f"#{r['id']} {r['text']} ({datetime.fromisoformat(r['due_at']):%d/%m %H:%M})" for r in rows
        )

    if name == "cancel_commitment":
        done = memory.complete_commitment(int(args["commitment_id"]), path)
        return "Impegno cancellato." if done else "Non ho trovato quell'impegno."

    if name == "mute_topic":
        days = int(args.get("days", 0) or 0)
        until = (datetime.now(ZoneInfo(tz)) + timedelta(days=days)).isoformat() if days > 0 else None
        memory.add_rule(args["topic"], "mute", until, path)
        return f"Non ti disturberò più per '{args['topic']}'" + (f" per {days} giorni." if days else ".")

    if name == "prioritize_topic":
        memory.add_rule(args["topic"], "always_call", None, path)
        return f"'{args['topic']}' ora vale una telefonata."

    if name == "rate_event":
        if not ctx.event_id:
            return "Questa conversazione non nasce da un evento: niente da valutare."
        verdict = "useful" if args.get("verdict") == "useful" else "noise"
        memory.add_feedback(ctx.event_id, ctx.source or "sconosciuta", ctx.urgency, verdict, path)
        return "Giudizio registrato."

    if name == "send_message":
        caller.notify(args["text"])
        return "Messaggio inviato."

    return f"Tool sconosciuto: {name}"


class Conversation:
    def __init__(self, reason: str | None = None, client: anthropic.AsyncAnthropic | None = None,
                 event_id: str = "", call_sid: str = ""):
        s = get_settings()
        self.settings = s
        self.client = client or anthropic.AsyncAnthropic(api_key=s.anthropic_api_key)
        self.messages: list[dict] = []
        self.reason = reason
        self.call_sid = call_sid
        event = memory.get_event(event_id) if event_id else None
        self.ctx = ToolContext(
            event_id=event_id,
            source=(event or {}).get("source", ""),
            urgency=int((event or {}).get("urgency") or 0),
        )

    def _system(self) -> str:
        now = long_it(datetime.now(ZoneInfo(self.settings.timezone)))
        notes = "\n".join(f"- {n}" for n in memory.recent_notes()) or "(nessuna)"
        commitments = "\n".join(
            f"- #{c['id']} {c['text']} (entro {datetime.fromisoformat(c['due_at']):%d/%m %H:%M})"
            for c in memory.open_commitments(limit=10)
        ) or "(nessuno)"
        sys = SYSTEM.format(now=now, notes=notes, commitments=commitments)
        if self.reason:
            sys += f"\nMotivo di questa chiamata: {self.reason}"
        return sys

    async def reply(self, user_text: str) -> str:
        self.messages.append({"role": "user", "content": user_text})
        memory.log_turn(self.call_sid, "user", user_text)
        for _ in range(5):  # limite ai giri di tool
            resp = await self.client.messages.create(
                model=self.settings.chat_model,
                max_tokens=400,
                system=self._system(),
                tools=TOOLS,
                messages=self.messages,
            )
            self.messages.append({"role": "assistant", "content": resp.content})
            if resp.stop_reason != "tool_use":
                answer = "".join(b.text for b in resp.content if b.type == "text").strip()
                memory.log_turn(self.call_sid, "jarvis", answer)
                return answer
            results = []
            for block in resp.content:
                if block.type != "tool_use":
                    continue
                try:
                    output = run_tool(block.name, block.input, self.ctx, self.settings.timezone)
                except Exception:
                    log.exception("tool %s fallito", block.name)
                    output = "Il tool ha dato errore, dillo ad Andrea senza inventare."
                results.append({"type": "tool_result", "tool_use_id": block.id, "content": output})
            self.messages.append({"role": "user", "content": results})
        return "Scusa, mi sono incartato. Puoi ripetere?"
