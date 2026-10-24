"""Guardrail deterministici: il modello propone, la policy decide.

Niente di quanto sta qui dentro è negoziabile dal prompt: urgenza, regole
dell'utente e budget di interruzioni sono applicati in codice.
"""
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

# azioni possibili, dalla meno alla più invasiva
IGNORE, LOG, DIGEST, NOTIFY, CALL = "ignore", "log", "digest", "notify", "call"

# azioni che l'utente può imporre con una regola esplicita
MUTE, ALWAYS_NOTIFY, ALWAYS_CALL = "mute", "always_notify", "always_call"


@dataclass
class Rule:
    """Regola esplicita dell'utente: match per sottostringa, non regex.

    Le regole arrivano dalla voce ("non disturbarmi per le newsletter"):
    una regex scritta a voce sarebbe imprevedibile.
    """

    pattern: str
    action: str


@dataclass
class PolicyInput:
    urgency: int                        # 0-10, stimato dal triage
    now: datetime                       # timezone-aware
    calls_today: int
    notifications_last_hour: int = 0
    rule: str | None = None             # azione imposta da una regola dell'utente


@dataclass
class PolicyConfig:
    tz: str = "Europe/Rome"
    quiet_start: int = 23
    quiet_end: int = 8
    max_calls_per_day: int = 3
    call_threshold: int = 8
    emergency_threshold: int = 10       # unico livello che può superare le quiet hours
    max_notifications_per_hour: int = 4


def in_quiet_hours(now: datetime, cfg: PolicyConfig) -> bool:
    local = now.astimezone(ZoneInfo(cfg.tz)).time()
    start, end = time(cfg.quiet_start), time(cfg.quiet_end)
    if start <= end:
        return start <= local < end
    return local >= start or local < end


def match_rule(text: str, rules: list[Rule]) -> str | None:
    """Prima regola il cui pattern compare nel testo dell'evento."""
    haystack = text.lower()
    for rule in rules:
        if rule.pattern and rule.pattern.lower() in haystack:
            return rule.action
    return None


def decide(inp: PolicyInput, cfg: PolicyConfig) -> str:
    """Restituisce 'ignore' | 'log' | 'digest' | 'notify' | 'call'."""
    if inp.rule == MUTE:
        return LOG                      # silenziato ma tracciato: resta nel briefing storico
    if inp.urgency <= 2:
        # nemmeno una regola esplicita promuove il rumore puro: un triage fallito
        # restituisce urgency=0, e non deve poter far squillare il telefono
        return IGNORE

    forced = inp.rule in (ALWAYS_CALL, ALWAYS_NOTIFY)
    if inp.urgency < 5 and not forced:
        return LOG

    wants_call = inp.urgency >= cfg.call_threshold or inp.rule == ALWAYS_CALL
    if not wants_call:
        over_budget = inp.notifications_last_hour >= cfg.max_notifications_per_hour
        if over_budget and inp.rule != ALWAYS_NOTIFY:
            return DIGEST               # accorpato nel prossimo briefing, non una vibrazione in più
        return NOTIFY
    if inp.calls_today >= cfg.max_calls_per_day:
        return NOTIFY
    if in_quiet_hours(inp.now, cfg) and inp.urgency < cfg.emergency_threshold:
        return NOTIFY
    return CALL


def _next_local(now: datetime, hour: int, cfg: PolicyConfig) -> datetime:
    tz = ZoneInfo(cfg.tz)
    local = now.astimezone(tz)
    candidate = local.replace(hour=hour % 24, minute=0, second=0, microsecond=0)
    if hour >= 24 or candidate <= local:
        candidate += timedelta(days=1)
    return candidate


def retry_at(inp: PolicyInput, cfg: PolicyConfig) -> datetime | None:
    """Quando rivalutare una chiamata declassata, invece di perderla.

    Una cosa urgente arrivata alle 3 di notte non diventa meno urgente alle 8:
    la policy la rimanda, non la butta.
    """
    if decide(inp, cfg) != NOTIFY:
        return None
    if inp.urgency < cfg.call_threshold and inp.rule != ALWAYS_CALL:
        return None
    if in_quiet_hours(inp.now, cfg):
        return _next_local(inp.now, cfg.quiet_end, cfg)
    if inp.calls_today >= cfg.max_calls_per_day:
        return _next_local(inp.now, 24, cfg)    # domani a mezzanotte locale
    return None
