"""Watcher calendario via feed iCal (URL segreto di Google Calendar, Apple, Outlook...).

Scelta: iCal e non le API Google. Un URL in .env invece di un flusso OAuth con
refresh token da custodire; in cambio si perde la scrittura e si vedono le
ricorrenze solo come le espande il feed. Per un assistente che deve *leggere*
l'agenda è il compromesso giusto.

Limite noto: le RRULE non vengono espanse. Google e Apple pubblicano le
occorrenze già espanse nel feed, Outlook non sempre.
"""
import logging
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx

from ..config import get_settings
from ..triage import Event

log = logging.getLogger("jarvis.watchers.calendar")


def _unfold(text: str) -> list[str]:
    lines: list[str] = []
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if raw[:1] in (" ", "\t") and lines:
            lines[-1] += raw[1:]
        else:
            lines.append(raw)
    return lines


def _unescape(value: str) -> str:
    return value.replace("\\n", " ").replace("\\,", ",").replace("\\;", ";").replace("\\\\", "\\")


def _parse_dt(value: str, params: dict[str, str], default_tz: str) -> datetime | None:
    value = value.strip()
    try:
        if value.endswith("Z"):
            return datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
        if params.get("VALUE") == "DATE" or len(value) == 8:
            day = datetime.strptime(value, "%Y%m%d").date()
            tz = _zone(params.get("TZID"), default_tz)
            return datetime.combine(day, time(0, 0), tzinfo=tz)
        naive = datetime.strptime(value, "%Y%m%dT%H%M%S")
        return naive.replace(tzinfo=_zone(params.get("TZID"), default_tz))
    except ValueError:
        log.warning("DTSTART non interpretabile: %s", value)
        return None


def _zone(tzid: str | None, default_tz: str) -> ZoneInfo:
    for candidate in (tzid, default_tz):
        if candidate:
            try:
                return ZoneInfo(candidate)
            except (ZoneInfoNotFoundError, ValueError):
                continue
    return ZoneInfo("UTC")


def parse_ics(text: str, default_tz: str = "Europe/Rome") -> list[dict]:
    """Estrae i VEVENT: uid, start, summary, description, location."""
    events: list[dict] = []
    current: dict | None = None
    for line in _unfold(text):
        if line == "BEGIN:VEVENT":
            current = {}
            continue
        if line == "END:VEVENT":
            if current and current.get("start"):
                events.append(current)
            current = None
            continue
        if current is None or ":" not in line:
            continue
        name_part, _, value = line.partition(":")
        name, *raw_params = name_part.split(";")
        params = dict(p.split("=", 1) for p in raw_params if "=" in p)
        name = name.upper()
        if name == "UID":
            current["uid"] = value.strip()
        elif name == "DTSTART":
            current["start"] = _parse_dt(value, params, default_tz)
        elif name == "SUMMARY":
            current["summary"] = _unescape(value.strip())
        elif name == "DESCRIPTION":
            current["description"] = _unescape(value.strip())
        elif name == "LOCATION":
            current["location"] = _unescape(value.strip())
        elif name == "STATUS":
            current["status"] = value.strip().upper()
    return events


def _fetch(url: str) -> str:
    return httpx.get(url, timeout=20, follow_redirects=True).raise_for_status().text


def poll(now: datetime | None = None, urls: list[str] | None = None,
         lookahead_minutes: int | None = None, fetch=_fetch, default_tz: str | None = None) -> list[Event]:
    """Eventi per gli appuntamenti che iniziano entro la finestra di preavviso."""
    s = get_settings()
    urls = s.ics_urls if urls is None else urls
    if not urls:
        return []
    now = now or datetime.now(timezone.utc)
    window = timedelta(minutes=lookahead_minutes or s.calendar_lookahead_minutes)
    default_tz = default_tz or s.timezone

    out: list[Event] = []
    for url in urls:
        try:
            raw = fetch(url)
        except Exception:
            log.exception("feed iCal non raggiungibile: %s", url)
            continue
        for item in parse_ics(raw, default_tz):
            start = item["start"]
            if item.get("status") == "CANCELLED" or not now <= start <= now + window:
                continue
            local = start.astimezone(ZoneInfo(default_tz))
            minutes = max(0, int((start - now).total_seconds() // 60))
            title = item.get("summary", "Appuntamento")
            body = (
                f"Inizia alle {local:%H:%M} (tra {minutes} minuti). "
                f"Luogo: {item.get('location', 'non indicato')}. {item.get('description', '')}"
            ).strip()
            uid = item.get("uid") or f"{title}:{start.isoformat()}"
            out.append(Event(id=f"cal:{uid}:{start.isoformat()}", source="calendario", title=title, body=body))
    return out
