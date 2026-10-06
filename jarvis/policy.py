"""Guardrail deterministici: il modello propone, la policy decide."""
from dataclasses import dataclass
from datetime import datetime, time
from zoneinfo import ZoneInfo


@dataclass
class PolicyInput:
    urgency: int            # 0-10, stimato dal triage
    now: datetime           # timezone-aware
    calls_today: int
    at_pc: bool = False     # utente attivo al PC (vedi presence.py)
    busy: bool = False      # al PC ma in call / schermo intero / non disturbare


@dataclass
class PolicyConfig:
    tz: str = "Europe/Rome"
    quiet_start: int = 23
    quiet_end: int = 8
    max_calls_per_day: int = 3
    call_threshold: int = 8
    emergency_threshold: int = 10   # unico livello che può superare quiet hours e "occupato"


def in_quiet_hours(now: datetime, cfg: PolicyConfig) -> bool:
    local = now.astimezone(ZoneInfo(cfg.tz)).time()
    start, end = time(cfg.quiet_start), time(cfg.quiet_end)
    if start <= end:
        return start <= local < end
    return local >= start or local < end


def decide(inp: PolicyInput, cfg: PolicyConfig) -> str:
    """Restituisce 'ignore' | 'log' | 'notify' | 'speak' | 'call'.

    - notify: messaggio su Telegram (silenzioso, letto quando vuoi)
    - speak:  Jarvis parla dalle casse del PC (solo se sei lì e non sei occupato)
    - call:   telefonata (solo se sei lontano dal PC e l'urgenza lo merita)
    """
    if inp.urgency <= 2:
        return "ignore"
    if inp.urgency < 5:
        return "log"
    if inp.at_pc:
        if inp.busy and inp.urgency < cfg.emergency_threshold:
            return "notify"
        return "speak"
    wants_call = inp.urgency >= cfg.call_threshold
    if not wants_call:
        return "notify"
    if inp.calls_today >= cfg.max_calls_per_day:
        return "notify"
    if in_quiet_hours(inp.now, cfg) and inp.urgency < cfg.emergency_threshold:
        return "notify"
    return "call"
