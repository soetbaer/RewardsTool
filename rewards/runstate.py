"""Gemeinsamer Zustand für CLI, Timer und Webinterface: Profilsperre, Status-Snapshot, Laufverlauf."""
import json
import os
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
DATA = BASE / "data"
RUN_LOGS = BASE / "logs" / "runs"
STATUS_FILE = DATA / "status.json"
HISTORY_FILE = DATA / "history.jsonl"
CURRENT_RUN_FILE = DATA / "current_run.json"


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
    _write_json(STATUS_FILE, {
        "updated": now_iso(),
        "logged_in": True,
        "points": state.points,
        "search": list(state.search) if state.search else None,
        "sections": sections,
    })


def start_run(log_file: Path, trigger: str) -> dict:
    run = {"start": now_iso(), "trigger": trigger, "log": log_file.name, "pid": os.getpid()}
    _write_json(CURRENT_RUN_FILE, run)
    return run


def finish_run(run: dict, **result) -> None:
    run = {**run, **result, "end": now_iso()}
    run.pop("pid", None)
    DATA.mkdir(exist_ok=True)
    with open(HISTORY_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(run, ensure_ascii=False) + "\n")
    try:
        CURRENT_RUN_FILE.unlink()
    except OSError:
        pass


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
