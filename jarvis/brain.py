"""Conversazione vocale con Claude, con tool locali."""
from datetime import datetime
from zoneinfo import ZoneInfo

import anthropic

from . import memory
from .config import get_settings

SYSTEM = """Sei Jarvis, l'assistente personale di Andrea. Parli al telefono, in italiano.
Frasi brevi e naturali, niente elenchi o markdown: tutto verrà letto ad alta voce.
Se hai chiamato tu, vai subito al motivo della chiamata.
Prima di qualsiasi azione irreversibile chiedi conferma esplicita.
Ora locale: {now}.
Note salvate:
{notes}"""

TOOLS = [
    {
        "name": "save_note",
        "description": "Salva una nota o un promemoria che Andrea chiede di ricordare.",
        "input_schema": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
    },
]


def run_tool(name: str, args: dict) -> str:
    if name == "save_note":
        memory.add_note(args["text"])
        return "Nota salvata."
    return f"Tool sconosciuto: {name}"


class Conversation:
    def __init__(self, reason: str | None = None, client: anthropic.AsyncAnthropic | None = None):
        s = get_settings()
        self.settings = s
        self.client = client or anthropic.AsyncAnthropic(api_key=s.anthropic_api_key)
        self.messages: list[dict] = []
        self.reason = reason

    def _system(self) -> str:
        now = datetime.now(ZoneInfo(self.settings.timezone)).strftime("%A %d %B %Y, %H:%M")
        notes = "\n".join(f"- {n}" for n in memory.recent_notes()) or "(nessuna)"
        sys = SYSTEM.format(now=now, notes=notes)
        if self.reason:
            sys += f"\nMotivo di questa chiamata: {self.reason}"
        return sys

    async def reply(self, user_text: str) -> str:
        self.messages.append({"role": "user", "content": user_text})
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
                return "".join(b.text for b in resp.content if b.type == "text").strip()
            results = [
                {"type": "tool_result", "tool_use_id": b.id, "content": run_tool(b.name, b.input)}
                for b in resp.content
                if b.type == "tool_use"
            ]
            self.messages.append({"role": "user", "content": results})
        return "Scusa, mi sono incartato. Puoi ripetere?"
