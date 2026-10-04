"""Conversazione vocale con Claude, con tool locali."""
from collections.abc import AsyncIterator
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

FALLBACK = "Scusa, mi sono incartato. Puoi ripetere?"

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

    async def stream(self, user_text: str) -> AsyncIterator[str]:
        """Yield the reply token by token. Runs tools between turns (max 5 rounds)."""
        self.messages.append({"role": "user", "content": user_text})
        for _ in range(5):
            async with self.client.messages.stream(
                model=self.settings.chat_model,
                max_tokens=400,
                system=self._system(),
                tools=TOOLS,
                messages=self.messages,
            ) as s:
                async for token in s.text_stream:
                    yield token
                final = await s.get_final_message()
            self.messages.append({"role": "assistant", "content": final.content})
            if final.stop_reason != "tool_use":
                return
            results = [
                {"type": "tool_result", "tool_use_id": b.id, "content": run_tool(b.name, b.input)}
                for b in final.content
                if b.type == "tool_use"
            ]
            self.messages.append({"role": "user", "content": results})
        yield FALLBACK

    async def reply(self, user_text: str) -> str:
        return "".join([t async for t in self.stream(user_text)]).strip()

    def interrupted(self, spoken: str) -> None:
        """Record what Andrea actually heard before cutting in, so Claude does not assume the rest was said."""
        if self.messages and self.messages[-1]["role"] == "user":
            self.messages.append({"role": "assistant", "content": spoken.strip() or "..."})
