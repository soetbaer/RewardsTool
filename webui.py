"""Webinterface für RewardsTool: Status, Laufverlauf, Lauf starten, Microsoft-Anmeldung im Browser.

Start:  python webui.py            (Server: über den Dienst rewardstool-web, siehe deploy/install.sh)
Aufruf: http://<server-ip>:3333
"""
import concurrent.futures
import hashlib
import hmac
import os
import queue
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
from functools import wraps
from pathlib import Path

from flask import Flask, Response, abort, jsonify, request, send_from_directory

from rewards import dashboard, runstate
from rewards.browser import open_context
from rewards.util import BING, load_config, log, setup_logging

BASE = Path(__file__).resolve().parent
WEB_DIR = BASE / "web"
AUTH_FILE = runstate.DATA / "webui_auth.json"
SESSION_COOKIE = "rt_session"
SESSION_MAX_AGE = 7 * 24 * 3600
LOGIN_IDLE_TIMEOUT = 15 * 60

app = Flask(__name__, static_folder=None)
cfg = load_config(BASE / "config.json")
sessions: dict[str, float] = {}  # Token -> Ablaufzeit
state_lock = threading.Lock()
run_proc: subprocess.Popen | None = None
refresh_proc: subprocess.Popen | None = None
ms_session = None  # aktive Microsoft-Anmeldung (MsLoginSession)


# ---------- Passwort für das Webinterface ----------

def _hash(password: str, salt: bytes) -> str:
    return hashlib.scrypt(password.encode(), salt=salt, n=2 ** 14, r=8, p=1).hex()


def auth_configured() -> bool:
    return AUTH_FILE.exists()


def check_password(password: str) -> bool:
    data = runstate.read_json(AUTH_FILE, {})
    if not data:
        return False
    return hmac.compare_digest(_hash(password, bytes.fromhex(data["salt"])), data["hash"])


def set_password(password: str) -> None:
    salt = secrets.token_bytes(16)
    runstate._write_json(AUTH_FILE, {"salt": salt.hex(), "hash": _hash(password, salt)})
    try:
        os.chmod(AUTH_FILE, 0o600)
    except OSError:
        pass


def new_session() -> str:
    token = secrets.token_urlsafe(32)
    sessions[token] = time.time() + SESSION_MAX_AGE
    return token


def logged_in() -> bool:
    token = request.cookies.get(SESSION_COOKIE, "")
    exp = sessions.get(token)
    return bool(exp and exp > time.time())


def api(fn):
    """Nur für angemeldete Nutzer; schreibende Aufrufe zusätzlich nur per fetch (Schutz gegen fremde Formulare)."""
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not logged_in():
            return jsonify(error="nicht angemeldet"), 401
        if request.method == "POST" and request.headers.get("X-Requested-With") != "RewardsTool":
            return jsonify(error="ungültige Anfrage"), 400
        return fn(*args, **kwargs)
    return wrapper


def with_session_cookie(resp, token: str):
    resp.set_cookie(SESSION_COOKIE, token, max_age=SESSION_MAX_AGE, httponly=True, samesite="Strict")
    return resp


# ---------- Prozesse (Lauf / Status aktualisieren) ----------

def _cli(*args: str) -> list[str]:
    python = Path(sys.executable)
    if os.name == "nt" and python.with_name("pythonw.exe").exists():
        python = python.with_name("pythonw.exe")  # Windows: kein Konsolenfenster für Läufe
    cmd = [str(python), str(BASE / "main.py"), *args]
    # Läufe brauchen einen Bildschirm ("Auf Bing erkunden" zählt nur mit sichtbarem Browser)
    if sys.platform.startswith("linux") and not os.environ.get("DISPLAY") and shutil.which("xvfb-run"):
        cmd = ["xvfb-run", "-a", *cmd]
    return cmd


def _spawn(*args: str) -> subprocess.Popen:
    env = {**os.environ, "REWARDS_TRIGGER": "webinterface", "PYTHONIOENCODING": "utf-8"}
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    return subprocess.Popen(_cli(*args), cwd=BASE, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            creationflags=flags)


def _alive(proc) -> bool:
    return proc is not None and proc.poll() is None


def busy_reason() -> str | None:
    if ms_session and ms_session.is_alive():
        return "Microsoft-Anmeldung läuft"
    if _alive(run_proc) or runstate.CURRENT_RUN_FILE.exists() and runstate.ProfileLock().is_busy():
        return "Lauf aktiv"
    if _alive(refresh_proc):
        return "Status wird aktualisiert"
    if runstate.ProfileLock().is_busy():
        return "Profil belegt"
    return None


_timer_cache: tuple[float, str | None] = (0.0, None)


def next_timer() -> str | None:
    """Nächster geplanter Lauf, eine Minute zwischengespeichert (die Seite fragt alle 3 Sekunden)."""
    global _timer_cache
    if time.time() - _timer_cache[0] > 60:
        _timer_cache = (time.time(), _query_next_timer())
    return _timer_cache[1]


def _query_next_timer() -> str | None:
    """systemd-Timer (Linux) bzw. Aufgabe 'RewardsTool' der Aufgabenplanung (Windows)."""
    try:
        if os.name == "nt":
            # Ohne CREATE_NO_WINDOW öffnet Windows für schtasks jedes Mal kurz ein Konsolenfenster
            raw = subprocess.run(["schtasks", "/Query", "/TN", "RewardsTool", "/FO", "LIST"],
                                 capture_output=True, timeout=10, creationflags=subprocess.CREATE_NO_WINDOW).stdout
            try:
                out = raw.decode("utf-8")
            except UnicodeDecodeError:
                out = raw.decode("cp850", errors="replace")
            for line in out.splitlines():
                if "chste Laufzeit" in line or "Next Run Time" in line:  # deutsch/englisch
                    return line.split(":", 1)[1].strip() or None
            return None
        if shutil.which("systemctl"):
            out = subprocess.run(["systemctl", "show", "rewardstool.timer", "-p", "NextElapseUSecRealtime", "--value"],
                                 capture_output=True, text=True, timeout=5).stdout.strip()
            return out or None
    except Exception:
        pass
    return None


def tail(path: Path, max_bytes: int = 20000) -> str:
    try:
        data = path.read_bytes()[-max_bytes:]
        return data.decode("utf-8", errors="replace")
    except OSError:
        return ""


# ---------- Microsoft-Anmeldung im Browser ----------

class MsLoginSession(threading.Thread):
    """Eigener Browser (dauerhaftes Profil) in einem Thread; das Webinterface zeigt Bilder davon und
    leitet Klicks/Tastatur weiter. Das Microsoft-Passwort geht nur an die Microsoft-Seite."""

    def __init__(self):
        super().__init__(daemon=True)
        self.jobs: queue.Queue = queue.Queue()
        self.ready = threading.Event()
        self.error: str | None = None
        self.last_used = time.time()
        self.lock = runstate.ProfileLock()
        self.ctx = None
        self.current = 0

    # läuft im Thread
    def run(self):
        from playwright.sync_api import sync_playwright
        if not self.lock.acquire():
            self.error = "Das Browserprofil wird gerade benutzt (Lauf aktiv?)."
            self.ready.set()
            return
        try:
            with sync_playwright() as pw:
                headless = not (os.name == "nt" or os.environ.get("DISPLAY"))
                self.ctx = open_context(pw, cfg, BASE, headless=headless)
                first = self.ctx.pages[0] if self.ctx.pages else self.ctx.new_page()
                for _ in range(2):  # Bing zeigt beim ersten Aufruf gelegentlich eine Fehlerseite
                    first.goto(dashboard.DASHBOARD_URL)
                    if "rewards.bing.com" in first.url or "login" in first.url:
                        break
                self.ctx.new_page().goto(BING)
                self.ready.set()
                while True:
                    try:
                        fn, fut = self.jobs.get(timeout=5)
                    except queue.Empty:
                        if time.time() - self.last_used > LOGIN_IDLE_TIMEOUT:
                            log.info("Microsoft-Anmeldung wegen Inaktivität beendet")
                            break
                        continue
                    if fn is None:
                        break
                    try:
                        fut.set_result(fn())
                    except Exception as e:
                        fut.set_exception(e)
                self.ctx.close()
        except Exception as e:
            self.error = str(e)
            self.ready.set()
        finally:
            self.lock.release()

    def call(self, fn, timeout: float = 30):
        if not self.is_alive():
            raise RuntimeError(self.error or "Anmeldung nicht aktiv")
        self.last_used = time.time()
        fut = concurrent.futures.Future()
        self.jobs.put((fn, fut))
        return fut.result(timeout)

    def stop(self):
        self.jobs.put((None, None))

    # nur im Thread über call() benutzen
    def page(self):
        pages = [p for p in self.ctx.pages if not p.is_closed()]
        if not pages:
            pages = [self.ctx.new_page()]
        self.current = min(self.current, len(pages) - 1)
        return pages[self.current]

    def tabs(self):
        return [{"title": (p.title() or p.url)[:60], "url": p.url[:120], "active": i == self.current}
                for i, p in enumerate(p for p in self.ctx.pages if not p.is_closed())]


def ms_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not (ms_session and ms_session.is_alive()):
            return jsonify(error="Keine Anmeldung aktiv"), 409
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            return jsonify(error=str(e)), 500
    return wrapper


# ---------- Routen: Seite & Webinterface-Login ----------

@app.get("/")
def index():
    return send_from_directory(WEB_DIR, "index.html")


@app.get("/<any('favicon.ico', 'favicon.svg', 'apple-touch-icon.png', 'logo.svg'):name>")
def brand_asset(name):
    return send_from_directory(WEB_DIR, name, max_age=86400)


@app.get("/api/auth")
def auth_status():
    return jsonify(configured=auth_configured(), logged_in=logged_in())


@app.post("/api/setup")
def setup():
    if auth_configured():
        abort(403)
    password = (request.json or {}).get("password", "")
    if len(password) < 8:
        return jsonify(error="Mindestens 8 Zeichen"), 400
    set_password(password)
    return with_session_cookie(jsonify(ok=True), new_session())


@app.post("/api/login")
def login():
    password = (request.json or {}).get("password", "")
    if not check_password(password):
        time.sleep(1.5)  # bremst Rateversuche
        return jsonify(error="Falsches Passwort"), 403
    return with_session_cookie(jsonify(ok=True), new_session())


@app.post("/api/logout")
@api
def logout():
    sessions.pop(request.cookies.get(SESSION_COOKIE, ""), None)
    resp = jsonify(ok=True)
    resp.delete_cookie(SESSION_COOKIE)
    return resp


# ---------- Routen: Status & Läufe ----------

@app.get("/api/state")
@api
def get_state():
    current = runstate.read_json(runstate.CURRENT_RUN_FILE)
    running = bool(current) and runstate.ProfileLock().is_busy() and not (ms_session and ms_session.is_alive())
    return jsonify(
        busy=busy_reason(),
        status=runstate.read_json(runstate.STATUS_FILE),
        run={**current, "log_text": tail(runstate.RUN_LOGS / current["log"])} if running else None,
        history=runstate.history(30),
        next_timer=next_timer(),
        ms_login=bool(ms_session and ms_session.is_alive()),
    )


@app.post("/api/run")
@api
def start_run():
    global run_proc
    with state_lock:
        reason = busy_reason()
        if reason:
            return jsonify(error=reason), 409
        run_proc = _spawn("run")
    return jsonify(ok=True)


@app.post("/api/refresh")
@api
def refresh():
    global refresh_proc
    with state_lock:
        reason = busy_reason()
        if reason:
            return jsonify(error=reason), 409
        refresh_proc = _spawn("status")
    return jsonify(ok=True)


@app.get("/api/log/<name>")
@api
def get_log(name):
    if not re.fullmatch(r"\d{8}-\d{6}\.log", name):
        abort(404)
    path = runstate.RUN_LOGS / name
    if not path.exists():
        abort(404)
    return Response(path.read_text(encoding="utf-8", errors="replace"), mimetype="text/plain; charset=utf-8")


# ---------- Routen: Microsoft-Anmeldung ----------

@app.post("/api/ms/start")
@api
def ms_start():
    global ms_session
    with state_lock:
        if ms_session and ms_session.is_alive():
            return jsonify(ok=True)
        reason = busy_reason()
        if reason:
            return jsonify(error=reason), 409
        ms_session = MsLoginSession()
        ms_session.start()
    ms_session.ready.wait(60)
    if ms_session.error:
        return jsonify(error=ms_session.error), 500
    return jsonify(ok=True)


@app.get("/api/ms/frame")
@api
@ms_required
def ms_frame():
    img = ms_session.call(lambda: ms_session.page().screenshot(type="jpeg", quality=70))
    return Response(img, mimetype="image/jpeg", headers={"Cache-Control": "no-store"})


@app.get("/api/ms/tabs")
@api
@ms_required
def ms_tabs():
    return jsonify(tabs=ms_session.call(ms_session.tabs))


@app.post("/api/ms/tab")
@api
@ms_required
def ms_tab():
    index = int((request.json or {}).get("index", 0))

    def switch():
        ms_session.current = index
        ms_session.page().bring_to_front()
    ms_session.call(switch)
    return jsonify(ok=True)


@app.post("/api/ms/goto")
@api
@ms_required
def ms_goto():
    target = {"rewards": dashboard.DASHBOARD_URL, "bing": BING}.get((request.json or {}).get("target"))
    if not target:
        abort(400)
    ms_session.call(lambda: ms_session.page().goto(target), timeout=60)
    return jsonify(ok=True)


@app.post("/api/ms/click")
@api
@ms_required
def ms_click():
    data = request.json or {}
    x, y = float(data["x"]), float(data["y"])
    ms_session.call(lambda: ms_session.page().mouse.click(x, y))
    return jsonify(ok=True)


@app.post("/api/ms/scroll")
@api
@ms_required
def ms_scroll():
    dy = float((request.json or {}).get("dy", 0))
    ms_session.call(lambda: ms_session.page().mouse.wheel(0, dy))
    return jsonify(ok=True)


ALLOWED_KEYS = {"Enter", "Tab", "Backspace", "Delete", "Escape", "ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown",
                "Home", "End", "Shift+Tab"}


@app.post("/api/ms/key")
@api
@ms_required
def ms_key():
    key = (request.json or {}).get("key", "")
    if key not in ALLOWED_KEYS:
        abort(400)
    ms_session.call(lambda: ms_session.page().keyboard.press(key))
    return jsonify(ok=True)


@app.post("/api/ms/text")
@api
@ms_required
def ms_text():
    text = (request.json or {}).get("text", "")
    if not text or len(text) > 500:
        abort(400)
    ms_session.call(lambda: ms_session.page().keyboard.type(text))
    return jsonify(ok=True)


@app.post("/api/ms/finish")
@api
@ms_required
def ms_finish():
    """Prüft, ob die Anmeldung geklappt hat, speichert den Status und beendet den Anmelde-Browser."""
    state = ms_session.call(lambda: dashboard.load_state(ms_session.ctx), timeout=120)
    runstate.save_status(state)
    ms_session.stop()
    ms_session.join(30)
    if not state:
        return jsonify(ok=False, error="Noch nicht angemeldet – bitte erneut versuchen.")
    return jsonify(ok=True, points=state.points)


@app.post("/api/ms/cancel")
@api
def ms_cancel():
    if ms_session and ms_session.is_alive():
        ms_session.stop()
        ms_session.join(30)
    return jsonify(ok=True)


def main():
    setup_logging(BASE / "logs")
    web = cfg.get("webui", {})
    host, port = web.get("host", "0.0.0.0"), int(web.get("port", 3333))
    log.info("Webinterface läuft auf http://%s:%s", host, port)
    try:
        from waitress import serve
        serve(app, host=host, port=port, threads=8)
    except ImportError:
        app.run(host=host, port=port, threaded=True)


if __name__ == "__main__":
    main()
