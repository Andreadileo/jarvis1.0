"""Watcher della inbox HTTP: tutto ciò che un sistema esterno spinge su POST /events.

Una sola porta d'ingresso per Shortcut iOS, IFTTT, Zapier, webhook di GitHub,
sensori di casa: chi vuole parlare con Jarvis non ha bisogno di un watcher dedicato.
"""
from datetime import datetime

from .. import memory
from ..triage import Event


def poll(now: datetime | None = None, path: str | None = None) -> list[Event]:
    return [
        Event(id=row["id"], source=row["source"] or "esterno", title=row["title"], body=row["body"] or "")
        for row in memory.drain_inbox(path)
    ]
