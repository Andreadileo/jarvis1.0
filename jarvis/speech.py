"""Sintesi vocale locale, gratuita.

Backend in ordine di preferenza:
1. Piper (PIPER_PATH + PIPER_MODEL in .env): voce migliore, nessun costo.
2. Windows SAPI via PowerShell: zero dipendenze, voce italiana di sistema ("Elsa").
Se nessuno è disponibile, synthesize() restituisce None e si resta al solo testo.
"""
import logging
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from .config import get_settings

log = logging.getLogger("jarvis.speech")


def _ps_quote(text: str) -> str:
    return "'" + " ".join(text.split()).replace("'", "''") + "'"


def _piper_available() -> bool:
    s = get_settings()
    return bool(s.piper_path and s.piper_model) and Path(s.piper_path).exists()


def tts_available() -> bool:
    return _piper_available() or sys.platform == "win32"


def can_make_voice_note() -> bool:
    """Nota vocale Telegram = sintesi locale + ffmpeg per l'OGG/Opus."""
    return tts_available() and shutil.which("ffmpeg") is not None


def synthesize(text: str) -> Path | None:
    """Testo -> file WAV temporaneo, oppure None se nessun backend funziona."""
    out = Path(tempfile.mkstemp(prefix="jarvis-", suffix=".wav")[1])
    try:
        if _piper_available():
            s = get_settings()
            subprocess.run(
                [s.piper_path, "--model", s.piper_model, "--output_file", str(out)],
                input=text.encode("utf-8"), check=True, capture_output=True, timeout=60,
            )
            return out
        if sys.platform == "win32":
            script = (
                "Add-Type -AssemblyName System.Speech; "
                "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
                f"$s.SetOutputToWaveFile({_ps_quote(str(out))}); "
                f"$s.Speak({_ps_quote(text)}); $s.Dispose()"
            )
            subprocess.run(["powershell", "-NoProfile", "-Command", script], check=True, capture_output=True, timeout=60)
            return out
    except Exception:
        log.exception("sintesi vocale fallita")
    out.unlink(missing_ok=True)
    return None


def to_ogg(wav: Path) -> Path | None:
    """WAV -> OGG/Opus (formato richiesto da Telegram per le note vocali). Richiede ffmpeg nel PATH."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return None
    ogg = wav.with_suffix(".ogg")
    try:
        subprocess.run([ffmpeg, "-y", "-loglevel", "error", "-i", str(wav), "-c:a", "libopus", "-b:a", "32k", str(ogg)],
                       check=True, capture_output=True, timeout=60)
        return ogg
    except Exception:
        log.exception("conversione ogg fallita")
        return None


def speak(text: str) -> bool:
    """Legge il testo dalle casse del PC. True se qualcosa è stato riprodotto."""
    try:
        if _piper_available() or sys.platform != "win32":
            wav = synthesize(text)
            if wav is None:
                return False
            try:
                if sys.platform == "win32":
                    script = f"(New-Object Media.SoundPlayer {_ps_quote(str(wav))}).PlaySync()"
                    subprocess.run(["powershell", "-NoProfile", "-Command", script], check=True, capture_output=True, timeout=120)
                else:
                    player = shutil.which("aplay") or shutil.which("afplay") or shutil.which("ffplay")
                    if not player:
                        return False
                    subprocess.run([player, str(wav)], check=True, capture_output=True, timeout=120)
                return True
            finally:
                wav.unlink(missing_ok=True)
        script = (
            "Add-Type -AssemblyName System.Speech; "
            f"(New-Object System.Speech.Synthesis.SpeechSynthesizer).Speak({_ps_quote(text)})"
        )
        subprocess.run(["powershell", "-NoProfile", "-Command", script], check=True, capture_output=True, timeout=120)
        return True
    except Exception:
        log.exception("riproduzione vocale fallita")
        return False
