"""Ciclo autonomo: watcher -> triage -> policy -> canale.

Un tick non costa niente se non succede niente: il modello viene interpellato
solo per gli eventi nuovi, e mai per rivalutare una decisione già presa.
"""
import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from . import agent, caller, memory
from .config import get_settings
from .policy import CALL, DIGEST, NOTIFY, PolicyConfig, PolicyInput, Rule, decide, match_rule, retry_at
from .triage import Event, triage, triage_context
from .watchers import calendar_ics, commitments, email_imap, inbox, reminders

log = logging.getLogger("jarvis.scheduler")

# Ogni watcher restituisce una lista di Event e non deve mai sollevare eccezioni fuori da qui.
WATCHERS = [
    reminders.poll,
    commitments.poll,
    inbox.poll,
    calendar_ics.poll,
    email_imap.poll,
]


def _policy_config() -> PolicyConfig:
    s = get_settings()
    return PolicyConfig(
        tz=s.timezone,
        quiet_start=s.quiet_hours_start,
        quiet_end=s.quiet_hours_end,
        max_calls_per_day=s.max_calls_per_day,
        call_threshold=s.call_urgency_threshold,
        max_notifications_per_hour=s.max_notifications_per_hour,
    )


def _start_of_local_day(now: datetime, tz: str) -> str:
    local = now.astimezone(ZoneInfo(tz)).replace(hour=0, minute=0, second=0, microsecond=0)
    return local.astimezone(timezone.utc).isoformat()


def _policy_input(urgency: int, now: datetime, cfg: PolicyConfig, rule: str | None) -> PolicyInput:
    return PolicyInput(
        urgency=urgency,
        now=now,
        calls_today=memory.outbound_calls_since(_start_of_local_day(now, cfg.tz)),
        notifications_last_hour=memory.notifications_since((now - timedelta(hours=1)).isoformat()),
        rule=rule,
    )


def _dispatch(action: str, event_id: str, text: str) -> None:
    if action == CALL:
        caller.place_call(text, event_id=event_id)
    elif action == NOTIFY:
        caller.notify(text)
    elif action == DIGEST:
        memory.queue_digest(text)


def process_pending(now: datetime, cfg: PolicyConfig) -> None:
    """Rivaluta le chiamate rimandate: nessun costo LLM, l'urgenza è già nota.

    Un solo rinvio: se anche adesso la policy dice di no, degrada a notifica
    invece di rimbalzare all'infinito.
    """
    for row in memory.due_pending(now.isoformat()):
        inp = _policy_input(int(row["urgency"]), now, cfg, None)
        action = decide(inp, cfg)
        text = row["summary"] or row["title"]
        _dispatch(CALL if action == CALL else NOTIFY, row["event_id"], text)
        memory.clear_pending(row["event_id"])
        log.info("rimandato %s -> %s", row["event_id"], action)


def handle_event(ev: Event, now: datetime, cfg: PolicyConfig, rules: list[Rule], context: str) -> str:
    t = triage(ev, context=context)
    rule = match_rule(f"{ev.source} {ev.title} {ev.body}", rules)
    inp = _policy_input(t.urgency, now, cfg, rule)
    action = decide(inp, cfg)
    text = t.summary or ev.title
    memory.save_event(ev, t.urgency, action, summary=text)
    log.info("%s -> urgency=%s rule=%s action=%s", ev.id, t.urgency, rule, action)

    if action == NOTIFY:
        when = retry_at(inp, cfg)
        if when:   # era da chiamata: la rimandiamo invece di perderla
            memory.add_pending(
                ev.id, ev.source, ev.title, text, t.urgency, when.astimezone(timezone.utc).isoformat()
            )
            log.info("%s: chiamata rimandata a %s", ev.id, when)
    _dispatch(action, ev.id, text)
    return action


def tick(now: datetime | None = None) -> None:
    cfg = _policy_config()
    now = now or datetime.now(timezone.utc)

    try:
        process_pending(now, cfg)
    except Exception:
        log.exception("rivalutazione dei rimandati fallita")

    stored_rules = memory.active_rules(now.isoformat())
    rules = [Rule(r["pattern"], r["action"]) for r in stored_rules]
    context = triage_context(stored_rules, memory.feedback_stats())

    for watcher in WATCHERS:
        try:
            events = watcher()
        except Exception:
            log.exception("watcher %s fallito", watcher)
            continue
        for ev in events:
            if memory.event_seen(ev.id):
                continue
            try:
                handle_event(ev, now, cfg, rules, context)
            except Exception:
                # alcune fonti (inbox HTTP, impegni) consumano l'evento leggendolo:
                # se il triage esplode, l'evento non deve sparire nel nulla
                log.exception("evento %s non gestito", ev.id)
                memory.save_event(ev, 0, "error", summary=ev.title)
                memory.queue_digest(f"(non valutato, errore tecnico) {ev.title}")

    try:
        agent.maybe_run_briefing(now)
    except Exception:
        log.exception("briefing fallito")


def start() -> AsyncIOScheduler:
    sched = AsyncIOScheduler()
    sched.add_job(tick, "interval", seconds=get_settings().watch_interval_seconds, max_instances=1)
    sched.start()
    return sched
