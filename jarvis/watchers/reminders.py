"""Watcher di esempio: promemoria da un file JSON locale.

Formato di reminders.json:
[{"id": "r1", "at": "2026-10-05T09:00:00+02:00", "title": "Call con Paolo", "body": "..."}]
Sostituire/affiancare con watcher reali (Google Calendar, Gmail, ...).
"""
import json
from datetime import datetime, timezone
from pathlib import Path

from ..triage import Event

FILE = Path("reminders.json")


def poll(now: datetime | None = None, path: Path = FILE) -> list[Event]:
    now = now or datetime.now(timezone.utc)
    if not path.exists():
        return []
    items = json.loads(path.read_text())
    return [
        Event(id=f"reminder:{it['id']}", source="promemoria", title=it["title"], body=it.get("body", ""))
        for it in items
        if datetime.fromisoformat(it["at"]) <= now
    ]
