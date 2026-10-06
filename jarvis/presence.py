"""Presenza dell'utente al PC (solo Windows; altrove risponde sempre 'assente').

Tre segnali, tutti letti dal sistema operativo e senza modello:
- secondi dall'ultimo input (tastiera/mouse)      -> at_pc
- stato notifiche di Windows (schermo intero,
  presentazione, "non disturbare")                -> busy
- microfono in uso da qualche app (call in corso) -> busy
"""
import logging
import sys
from dataclasses import dataclass

log = logging.getLogger("jarvis.presence")

# Valori di SHQueryUserNotificationState (shellapi.h)
QUNS_NOT_PRESENT = 1
QUNS_BUSY = 2
QUNS_RUNNING_D3D_FULL_SCREEN = 3
QUNS_PRESENTATION_MODE = 4
QUNS_ACCEPTS_NOTIFICATIONS = 5
QUNS_QUIET_TIME = 6
QUNS_APP = 7
_BUSY_STATES = {QUNS_BUSY, QUNS_RUNNING_D3D_FULL_SCREEN, QUNS_PRESENTATION_MODE, QUNS_APP}

_MIC_KEY = r"Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\microphone"


@dataclass
class Presence:
    at_pc: bool
    busy: bool
    reason: str = ""


def evaluate(idle_seconds: float | None, notif_state: int | None, mic_in_use: bool, idle_threshold: int) -> Presence:
    """Logica pura, testabile senza Windows."""
    if idle_seconds is None or idle_seconds > idle_threshold or notif_state == QUNS_NOT_PRESENT:
        return Presence(at_pc=False, busy=False, reason="inattivo")
    if mic_in_use:
        return Presence(at_pc=True, busy=True, reason="microfono in uso")
    if notif_state in _BUSY_STATES:
        return Presence(at_pc=True, busy=True, reason="schermo intero o non disturbare")
    return Presence(at_pc=True, busy=False, reason="attivo")


# --- letture Windows -------------------------------------------------------

def _idle_seconds() -> float | None:
    import ctypes
    from ctypes import wintypes

    class LASTINPUTINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]

    info = LASTINPUTINFO()
    info.cbSize = ctypes.sizeof(LASTINPUTINFO)
    if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
        return None
    tick = ctypes.windll.kernel32.GetTickCount()
    return ((tick - info.dwTime) & 0xFFFFFFFF) / 1000.0


def _notification_state() -> int | None:
    import ctypes

    state = ctypes.c_int(0)
    if ctypes.windll.shell32.SHQueryUserNotificationState(ctypes.byref(state)) != 0:
        return None
    return state.value


def _mic_in_use() -> bool:
    """Windows segna LastUsedTimeStop=0 per ogni app che sta usando il microfono."""
    import winreg

    def scan(key) -> bool:
        i = 0
        while True:
            try:
                name = winreg.EnumKey(key, i)
            except OSError:
                return False
            i += 1
            with winreg.OpenKey(key, name) as sub:
                if name == "NonPackaged" and scan(sub):
                    return True
                try:
                    stop, _ = winreg.QueryValueEx(sub, "LastUsedTimeStop")
                    if stop == 0:
                        return True
                except OSError:
                    pass

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _MIC_KEY) as key:
            return scan(key)
    except OSError:
        return False


def detect(idle_threshold: int = 120) -> Presence:
    if sys.platform != "win32":
        return Presence(at_pc=False, busy=False, reason="non Windows")
    try:
        return evaluate(_idle_seconds(), _notification_state(), _mic_in_use(), idle_threshold)
    except Exception:
        log.exception("rilevamento presenza fallito, assumo assente")
        return Presence(at_pc=False, busy=False, reason="errore")
