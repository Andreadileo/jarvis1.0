"""Formattazione delle date in italiano senza dipendere dal locale di sistema."""
from datetime import datetime

GIORNI = ("lunedì", "martedì", "mercoledì", "giovedì", "venerdì", "sabato", "domenica")
MESI = ("gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno",
        "luglio", "agosto", "settembre", "ottobre", "novembre", "dicembre")


def long_it(dt: datetime) -> str:
    """'sabato 24 ottobre 2026, 09:00'"""
    return f"{GIORNI[dt.weekday()]} {dt.day} {MESI[dt.month - 1]} {dt.year}, {dt:%H:%M}"


def day_it(dt: datetime) -> str:
    """'sabato 24 ottobre'"""
    return f"{GIORNI[dt.weekday()]} {dt.day} {MESI[dt.month - 1]}"
