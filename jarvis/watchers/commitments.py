"""Watcher degli impegni che Jarvis si è preso durante una conversazione.

È il pezzo che rende l'assistente persistente: "ricordamelo domani alle 9"
diventa una riga nel DB che torna a galla da sola, anche a settimane di distanza.
"""
from datetime import datetime, timezone

from .. import memory
from ..triage import Event


def poll(now: datetime | None = None, path: str | None = None) -> list[Event]:
    now = now or datetime.now(timezone.utc)
    events = []
    for row in memory.due_commitments(now.isoformat(), path):
        memory.complete_commitment(row["id"], path)
        events.append(
            Event(
                id=f"commitment:{row['id']}",
                source="impegno",
                title=row["text"],
                body=f"Andrea ha chiesto di ricordarglielo entro {row['due_at']}.",
            )
        )
    return events
