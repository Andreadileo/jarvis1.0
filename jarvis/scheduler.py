"""Ciclo autonomo: watcher -> triage -> policy -> canale."""
import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from . import caller, memory
from .config import get_settings
from .policy import PolicyConfig, PolicyInput, decide
from .triage import triage
from .watchers import reminders

log = logging.getLogger("jarvis.scheduler")

WATCHERS = [reminders.poll]


def _policy_config() -> PolicyConfig:
    s = get_settings()
    return PolicyConfig(
        tz=s.timezone,
        quiet_start=s.quiet_hours_start,
        quiet_end=s.quiet_hours_end,
        max_calls_per_day=s.max_calls_per_day,
        call_threshold=s.call_urgency_threshold,
    )


def _start_of_local_day(now: datetime, tz: str) -> str:
    local = now.astimezone(ZoneInfo(tz)).replace(hour=0, minute=0, second=0, microsecond=0)
    return local.astimezone(timezone.utc).isoformat()


def tick() -> None:
    cfg = _policy_config()
    now = datetime.now(timezone.utc)
    for watcher in WATCHERS:
        try:
            events = watcher()
        except Exception:
            log.exception("watcher %s fallito", watcher)
            continue
        for ev in events:
            if memory.event_seen(ev.id):
                continue
            t = triage(ev)
            calls_today = memory.outbound_calls_since(_start_of_local_day(now, cfg.tz))
            action = decide(PolicyInput(t.urgency, now, calls_today), cfg)
            memory.save_event(ev, t.urgency, action)
            log.info("%s -> urgency=%s action=%s", ev.id, t.urgency, action)
            if action == "call":
                caller.place_call(t.summary or ev.title)
            elif action == "notify":
                caller.notify(t.summary or ev.title)


def start() -> AsyncIOScheduler:
    sched = AsyncIOScheduler()
    sched.add_job(tick, "interval", seconds=get_settings().watch_interval_seconds, max_instances=1)
    sched.start()
    return sched
