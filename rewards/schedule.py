"""Täglichen Lauf steuern (Uhrzeit, an/aus): systemd-Timer (Linux) bzw. Aufgabenplanung (Windows)."""
import os
import re
import shutil
import subprocess

TASK_NAME = "RewardsTool"
# Von deploy/install.sh angelegt (gehört root, per sudoers ohne Passwort erlaubt)
LINUX_HELPER = "/usr/local/sbin/rewardstool-set-time"
TIME_RE = re.compile(r"([01]\d|2[0-3]):[0-5]\d")


def _run(cmd: list[str], timeout: int = 20) -> subprocess.CompletedProcess:
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    return subprocess.run(cmd, capture_output=True, timeout=timeout, creationflags=flags)


def _decode(raw: bytes) -> str:
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("cp850", errors="replace")


def _powershell(script: str) -> subprocess.CompletedProcess:
    return _run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script])


def get_time() -> str | None:
    """Eingestellte Uhrzeit als 'HH:MM', None ohne eingerichteten Timer."""
    try:
        if os.name == "nt":
            out = _decode(_powershell(
                f"(Get-ScheduledTask -TaskName '{TASK_NAME}' -ErrorAction Stop).Triggers[0].StartBoundary").stdout)
            m = re.search(r"T(\d\d):(\d\d)", out)
        elif shutil.which("systemctl"):
            out = _decode(_run(["systemctl", "show", "rewardstool.timer", "-p", "TimersCalendar", "--value"]).stdout)
            m = re.search(r"OnCalendar=\S+ (\d\d):(\d\d)", out)
        else:
            return None
        return f"{m.group(1)}:{m.group(2)}" if m else None
    except Exception:
        return None


def can_set() -> bool:
    if os.name == "nt":
        return True
    return os.path.exists(LINUX_HELPER)


def set_time(hhmm: str) -> None:
    """Stellt den täglichen Lauf um; wirft RuntimeError mit verständlicher Meldung."""
    if not TIME_RE.fullmatch(hhmm):
        raise RuntimeError("Ungültige Uhrzeit (Format HH:MM)")
    if os.name == "nt":
        res = _powershell(
            f"Set-ScheduledTask -TaskName '{TASK_NAME}' -Trigger (New-ScheduledTaskTrigger -Daily -At '{hhmm}')"
            " -ErrorAction Stop | Out-Null")
        if res.returncode != 0:
            raise RuntimeError("Aufgabe 'RewardsTool' nicht gefunden – bitte Setup.bat erneut ausführen. "
                               + _decode(res.stderr).strip()[:200])
        return
    if not os.path.exists(LINUX_HELPER):
        raise RuntimeError("Auf diesem Server fehlt die Berechtigung dafür – bitte einmal "
                           "'bash deploy/install.sh' erneut ausführen.")
    res = _run(["sudo", "-n", LINUX_HELPER, hhmm])
    if res.returncode != 0:
        raise RuntimeError("Uhrzeit konnte nicht geändert werden: " + _decode(res.stderr).strip()[:200])


def set_enabled(on: bool) -> str | None:
    """Schaltet den Timer bzw. die Aufgabe an/aus. Rückgabe: Hinweis, falls nur teilweise möglich.

    Zusätzlich überspringt main.py Timer-Läufe, solange der tägliche Lauf in den Einstellungen aus ist – das greift
    auch, wenn das System den Timer nicht abschalten konnte (z. B. Linux-Installation mit altem Hilfsskript).
    """
    if os.name == "nt":
        cmd = "Enable-ScheduledTask" if on else "Disable-ScheduledTask"
        res = _powershell(f"{cmd} -TaskName '{TASK_NAME}' -ErrorAction Stop | Out-Null")
        if res.returncode != 0:
            return "Aufgabe 'RewardsTool' nicht gefunden – bitte Setup.bat erneut ausführen."
        return None
    if not shutil.which("systemctl"):
        return None
    res = _run(["sudo", "-n", LINUX_HELPER, "on" if on else "off"]) if os.path.exists(LINUX_HELPER) else None
    if res is None or res.returncode != 0:
        return ("Der Timer selbst läuft weiter, seine Läufe werden aber übersprungen. Zum vollständigen Abschalten "
                "einmal 'bash deploy/install.sh' erneut ausführen.") if not on else None
    return None
