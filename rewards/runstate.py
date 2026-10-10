"""Gemeinsamer Zustand für CLI, Timer und Webinterface: Profilsperre, Status-Snapshot, Laufverlauf."""
import json
import os
import time
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
DATA = BASE / "data"
RUN_LOGS = BASE / "logs" / "runs"
STATUS_FILE = DATA / "status.json"
HISTORY_FILE = DATA / "history.jsonl"
CURRENT_RUN_FILE = DATA / "current_run.json"
CANCEL_FILE = DATA / "cancel_run"  # vom Webinterface angelegt: laufender Lauf soll abbrechen


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


class ProfileLock:
    """Das Browserprofil darf immer nur ein Prozess benutzen (Timer, Webinterface, Aufruf von Hand)."""

    def __init__(self):
        DATA.mkdir(exist_ok=True)
        self.path = DATA / "profile.lock"
        self.fh = None

    def acquire(self) -> bool:
        self.fh = open(self.path, "a+")
        try:
            if os.name == "nt":
                import msvcrt
                self.fh.seek(0)
                msvcrt.locking(self.fh.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            self.fh.close()
            self.fh = None
            return False

    def release(self) -> None:
        if not self.fh:
            return
        try:
            if os.name == "nt":
                import msvcrt
                self.fh.seek(0)
                msvcrt.locking(self.fh.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.fh, fcntl.LOCK_UN)
        finally:
            self.fh.close()
            self.fh = None

    def is_busy(self) -> bool:
        if self.fh:
            return True
        if self.acquire():
            self.release()
            return False
        return True


def _write_json(path: Path, data) -> None:
    DATA.mkdir(exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def read_json(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def save_status(state) -> None:
    """Schreibt den zuletzt gelesenen Rewards-Zustand für das Webinterface (state=None: nicht eingeloggt)."""
    if state is None:
        old = read_json(STATUS_FILE, {}) or {}
        old.pop("pending", None)
        _write_json(STATUS_FILE, {**old, "updated": now_iso(), "logged_in": False})
        return
    sections = []
    for name, acts in (("Tägliche Aktionen", state.daily_set), ("Auf Bing erkunden", state.explore),
                       ("Weiter verdienen", state.more)):
        sections.append({
            "name": name,
            "done": sum(a.completed for a in acts),
            "total": len(acts),
            "open": [{"title": a.title, "points": a.points} for a in acts if not a.completed],
        })
    if state.visual_search is not None:
        sections.append({
            "name": "Visuelle Suche",
            "done": int(state.visual_search),
            "total": 1,
            "open": [] if state.visual_search else [{"title": "Suche mit einem Bild", "points": None}],
        })
    goal = state.goal
    _write_json(STATUS_FILE, {
        "updated": now_iso(),
        "logged_in": True,
        "points": state.points,
        "claimable": state.claimable,
        "goal": {"title": goal.title, "price": goal.price, "points": goal.points, "remaining": goal.remaining,
                 "url": goal.url, "image": goal.image} if goal else None,
        "search": list(state.search) if state.search else None,
        "sections": sections,
    })


def update_status(**fields) -> None:
    """Ändert einzelne Werte des gespeicherten Zustands (Live-Anzeige während eines Laufs)."""
    status = read_json(STATUS_FILE)
    if not status:
        return
    for key, value in fields.items():
        if value is None:
            status.pop(key, None)
        else:
            status[key] = value
    _write_json(STATUS_FILE, status)


def start_run(log_file: Path, trigger: str) -> dict:
    CANCEL_FILE.unlink(missing_ok=True)
    run = {"start": now_iso(), "trigger": trigger, "log": log_file.name, "pid": os.getpid()}
    _write_json(CURRENT_RUN_FILE, run)
    # Alter Stand gilt nicht mehr: das Webinterface zeigt ihn ausgegraut, bis der Lauf den neuen gelesen hat
    update_status(pending=True)
    return run


def update_run(**fields) -> None:
    """Aktueller Schritt des Laufs für die Live-Anzeige (z. B. step="Suchen", detail="Suche 3/30")."""
    run = read_json(CURRENT_RUN_FILE)
    if run and run.get("pid") == os.getpid():
        _write_json(CURRENT_RUN_FILE, {**run, **fields})


def finish_run(run: dict, **result) -> None:
    """Schreibt den Lauf in den Verlauf. Nur ein gestarteter Lauf (mit pid) räumt den Laufzustand auf –
    ein wegen belegtem Profil übersprungener darf den des aktiven Laufs nicht löschen."""
    started = run.get("pid") is not None
    run = {**run, **result, "end": now_iso()}
    for key in ("pid", "step", "detail"):
        run.pop(key, None)
    DATA.mkdir(exist_ok=True)
    with open(HISTORY_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(run, ensure_ascii=False) + "\n")
    if not started:
        return
    try:
        CURRENT_RUN_FILE.unlink()
    except OSError:
        pass
    CANCEL_FILE.unlink(missing_ok=True)
    update_status(pending=None)


# ---------- Lauf abbrechen ----------

class RunCancelled(BaseException):
    """Lauf wurde im Webinterface abgebrochen. BaseException, damit die 'except Exception' in den
    einzelnen Aufgaben den Abbruch nicht abfangen."""


def request_cancel() -> None:
    DATA.mkdir(exist_ok=True)
    CANCEL_FILE.touch()


def cancel_requested() -> bool:
    return CANCEL_FILE.exists()


def check_cancel() -> None:
    if CANCEL_FILE.exists() and CURRENT_RUN_FILE.exists():
        raise RunCancelled()


def sleep(seconds: float) -> None:
    """Wie time.sleep, bricht aber sofort ab, wenn der Lauf abgebrochen werden soll."""
    end = time.monotonic() + seconds
    while True:
        check_cancel()
        left = end - time.monotonic()
        if left <= 0:
            return
        time.sleep(min(0.5, left))


def history(limit: int = 30) -> list[dict]:
    try:
        lines = HISTORY_FILE.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for line in reversed(lines[-limit:]):
        try:
            out.append(json.loads(line))
        except ValueError:
            pass
    return out
