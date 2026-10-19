"""Extra-Aktivitäten und Events (Quests), z. B. die Halloween-Aufgaben.

Ein Event ist eine Liste von Aufgaben mit Statuszeile („0 von 6 Aufgaben“). Jedes Event ist anders, darum liest
das Tool die Aufgaben von der Seite (Titel, Beschreibung, Knopf, gesperrt/erledigt) und erkennt am Text, was zu
tun ist: Quest aktivieren, Link öffnen, zu einem Thema suchen, Quiz. Aufgaben über mehrere Tage („Suche an 5 von
10 Tagen … mindestens 24 Stunden auseinander“) werden in data/quests.json verfolgt. Unbekannte Aufgaben werden
übersprungen und geloggt.
"""
import json
import random
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urljoin, urlparse

from playwright.sync_api import TimeoutError as PwTimeout

from . import dashboard, runstate
from .activities import solve_quiz
from .searches import bing_search
from .util import LOGIN_HINT, log, pause, reject_consent

TRACK_FILE = runstate.DATA / "quests.json"
DEBUG_DIR = Path(__file__).resolve().parent.parent / "debug" / "quests"

ACTIVATE, MULTIDAY, SEARCH, VISIT, QUIZ, UNKNOWN = "activate", "multiday", "search", "visit", "quiz", "unknown"
KIND_NAMES = {ACTIVATE: "Quest aktivieren", MULTIDAY: "mehrere Tage", SEARCH: "Suche", VISIT: "Link öffnen",
              QUIZ: "Quiz", UNKNOWN: "unbekannt"}

MAX_ATTEMPTS = 3  # so oft wird eine einmalige Aufgabe versucht, wenn die Seite sie danach nicht als erledigt zeigt
MIN_GAP = timedelta(hours=24, minutes=5)  # "mindestens 24 Stunden auseinander" plus Puffer
ROUNDS = 3  # Aktivieren schaltet weitere Aufgaben frei: Seite danach neu lesen
WAIT_MIN, WAIT_MAX = 0, 180
DEFAULT_WAIT = 45

# ---------- Aufgaben erkennen (reine Textlogik, ohne Browser) ----------

_ACTIVATE = re.compile(r"aktivier|activate|quest starten|start (the )?quest|beginnt hier|begins here|teilnehmen|"
                       r"\bjoin\b|opt.?in", re.I)
_QUIZ = re.compile(r"quiz|umfrage|\bpoll\b|test (dein|your)", re.I)
_SEARCH = re.compile(r"\bsuch|search|\bfinde|\bfind\b|recherch|nachschlagen|look up", re.I)
_VISIT = re.compile(r"herunterlad|download|probier|\btry\b|ansehen|anschauen|\bwatch\b|entdeck|discover|explore|"
                    r"öffne|\bopen\b|lies |lesen|\bread\b|mehr erfahren|learn more|besuch|visit|spiel|\bplay\b", re.I)
_DAYS = re.compile(r"(\d+)\s*(?:von|of|out of|aus)\s*(\d+)\s*(?:tagen|tage|days)", re.I)
_DAY_PROGRESS = re.compile(r"\(?\s*(\d+)\s*/\s*(\d+)\s*(?:tage|tagen|days)", re.I)
_GAP = re.compile(r"(\d+)\s*(?:stunden|hours)\s*(?:auseinander|apart)", re.I)


@dataclass
class QuestTask:
    title: str
    description: str
    cta: str  # Beschriftung des Knopfs, z. B. "Kostüme suchen"
    href: str = ""
    locked: bool = False
    completed: bool = False
    mark: str = ""  # data-rt-quest des Knopfs auf der Seite

    @property
    def text(self) -> str:
        return f"{self.title} {self.description} {self.cta}"


@dataclass
class Quest:
    url: str
    done: int
    total: int
    tasks: list[QuestTask] = field(default_factory=list)
    name: str = ""  # Name des Events, z. B. "Halloween"

    @property
    def key(self) -> str:
        return self.name or urlparse(self.url).path


def classify(task: QuestTask) -> str:
    """Art der Aufgabe aus Titel, Beschreibung und Knopf."""
    if multiday_rule(task):
        return MULTIDAY
    if _QUIZ.search(task.text):
        return QUIZ
    if _ACTIVATE.search(task.cta) or _ACTIVATE.search(task.title):
        return ACTIVATE
    # Der Knopf sagt am deutlichsten, was passiert; die Beschreibung nur, wenn der Knopf nichts verrät
    for text in (task.cta, task.description, task.title):
        if _SEARCH.search(text):
            return SEARCH
        if _VISIT.search(text):
            return VISIT
    return UNKNOWN


def multiday_rule(task: QuestTask) -> dict | None:
    """'Suche an 5 von 10 Tagen … (0/5 Tage abgeschlossen)' -> {'days': 5, 'window': 10, 'done': 0, 'gap_hours': 24}."""
    m = _DAYS.search(task.text)
    p = _DAY_PROGRESS.search(task.text)
    if not m and not p:
        return None
    days = int(m.group(1)) if m else int(p.group(2))
    rule = {"days": days, "window": int(m.group(2)) if m else None, "done": int(p.group(1)) if p else None}
    g = _GAP.search(task.text)
    rule["gap_hours"] = int(g.group(1)) if g else 24
    return rule


def multiday_action(task: QuestTask) -> str:
    """Was an jedem der Tage zu tun ist (meist suchen)."""
    for text in (task.cta, task.description, task.title):
        if _SEARCH.search(text):
            return SEARCH
    return VISIT


_STOP = {"dein", "deine", "ihren", "ihre", "ihrem", "ihr", "sie", "mit", "auf", "und", "für", "der", "die", "das",
         "den", "dem", "des", "ein", "eine", "einen", "hier", "jetzt", "your", "with", "the", "and", "for", "bing",
         "suche", "suchen", "finde", "finden", "aktivieren", "quest", "beginnt", "machen", "mach", "gönnen",
         "verleihen", "verwirklichen", "entdecken", "tagen", "tage", "stunden"}


def event_name(tasks: list[QuestTask]) -> str:
    """Name des Events: Wort, das in den meisten Aufgaben vorkommt ('Halloween-Look' -> 'Halloween')."""
    counts = Counter()
    for t in tasks:
        words = {w for w in re.findall(r"[A-Za-zÄÖÜäöüß]{4,}", f"{t.title} {t.cta}") if w.lower() not in _STOP}
        counts.update(w for w in words if w[0].isupper())
    best = [(n, w) for w, n in counts.items() if n >= 2]
    return max(best)[1] if best else ""


_VERBS = re.compile(r"\b(suchen|suche|such|finde|finden|search|find|starten|start|entdecken|explore|jetzt|nach|"
                    r"for|on|auf|bing)\b", re.I)
_GENERIC = re.compile(r"^(suche|search|los|go|start|starten)?$", re.I)


def _fill(query: str, event: str) -> str:
    q = query.replace("{event}", event).strip()
    return re.sub(r"\s+", " ", q)


def search_queries(task: QuestTask, event: str, topics: dict) -> list[str]:
    """Suchbegriffe für eine Such-Aufgabe in Versuchsreihenfolge.

    1. Stichwort aus quests.topic_queries in config.json (Text oder Liste, {event} = Name des Events)
    2. Knopf ohne Verb ('Finde Halloween-Dekoration' -> 'Halloween-Dekoration')
    3. Beschreibung ('Suchen Sie nach Kostümideen, …' -> 'Kostümideen')
    4. allgemeine Begriffe zum Event (Schlüssel '*')
    """
    out = []
    text = task.text.lower()
    for keyword, query in topics.items():
        if keyword != "*" and keyword.lower() in text:
            items = list(query) if isinstance(query, list) else [query]
            random.shuffle(items)
            out += [_fill(q, event) for q in items]
            break
    cta = _VERBS.sub(" ", task.cta).strip(" -–:!.")
    if cta and not _GENERIC.match(cta) and len(cta) > 3:
        out.append(cta if not event or event.lower() in cta.lower() else f"{event} {cta}")
    m = re.search(r"(?:such\w*|search(?:es)?|find\w*)\s+(?:sie\s+)?(?:auf bing\s+)?(?:nach|for)\s+([^,.;]+)",
                  task.description, re.I)
    if m:
        q = m.group(1).strip()
        out.append(q if not event or event.lower() in q.lower() else f"{event} {q}")
    generic = topics.get("*") or ["{event}"]
    generic = list(generic) if isinstance(generic, list) else [generic]
    random.shuffle(generic)
    out += [_fill(q, event) for q in generic if event or "{event}" not in q]
    return [q for q in dict.fromkeys(out) if q]


# ---------- Verlauf über mehrere Tage (data/quests.json) ----------

def load_track() -> dict:
    return runstate.read_json(TRACK_FILE, {}) or {}


def save_track(track: dict) -> None:
    # Einträge abgelaufener Events nach 60 Tagen löschen
    cutoff = (datetime.now() - timedelta(days=60)).isoformat()
    for qkey in list(track):
        tasks = track[qkey]
        for tkey in list(tasks):
            if max(tasks[tkey].get("times") or [""]) < cutoff:
                del tasks[tkey]
        if not tasks:
            del track[qkey]
    runstate._write_json(TRACK_FILE, track)


def multiday_wait(entry: dict, now: datetime, gap_hours: int = 24) -> float:
    """Sekunden bis zur nächsten erlaubten Ausführung (0 = jetzt fällig)."""
    times = entry.get("times") or []
    if not times:
        return 0
    gap = MIN_GAP + timedelta(hours=gap_hours - 24)
    due = datetime.fromisoformat(max(times)) + gap
    return max(0.0, (due - now).total_seconds())


def record(entry: dict, now: datetime) -> None:
    entry.setdefault("times", []).append(now.isoformat(timespec="seconds"))


# ---------- Seite lesen ----------

# Findet Event-Listen anhand der Statuszeile ("0 von 6 Aufgaben") und liest jede Aufgabe aus.
# Der Knopf jeder Aufgabe bekommt data-rt-quest="<Event>-<Aufgabe>", damit Playwright ihn anklicken kann.
_READ_JS = r"""
() => {
  const norm = s => (s || '').replace(/[​­]/g, '').replace(/\s+/g, ' ').trim();
  const STATUS = /(\d+)\s*(?:von|of|\/)\s*(\d+)\s*(?:Aufgaben|tasks|Aktivitäten|activities)/i;
  const CTA = 'a, button, [role="button"], [role="link"]';
  const DONE = /abgeschlossen|erledigt|completed|done|checkmark|häkchen/i;
  const LOCK = /gesperrt|locked|\block\b/i;
  const shown = e => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  const ctas = box => [...box.querySelectorAll(CTA)].filter(e => !e.parentElement.closest(CTA) && norm(e.innerText).length >= 3 && shown(e));
  document.querySelectorAll('[data-rt-quest]').forEach(e => e.removeAttribute('data-rt-quest'));

  const statuses = [...document.querySelectorAll('body *')].filter(e =>
    shown(e) && norm(e.innerText).length < 120 && STATUS.test(norm(e.innerText))
    && ![...e.children].some(c => STATUS.test(norm(c.innerText))));
  const quests = [];
  statuses.forEach((status, qi) => {
    const [, done, total] = norm(status.innerText).match(STATUS);
    // Nach oben gehen, bis alle Aufgaben-Knöpfe im Bereich liegen (aber kein zweites Event)
    let box = status;
    for (let i = 0; i < 12 && box.parentElement && box !== document.body; i++) {
      if (ctas(box).length >= +total) break;
      const up = box.parentElement;
      if (statuses.some(s => s !== status && up.contains(s))) break;
      box = up;
    }
    const tasks = [];
    ctas(box).forEach(cta => {
      if (status.contains(cta)) return;
      let row = cta;
      while (row.parentElement && row.parentElement !== box && ctas(row.parentElement).length === 1
             && !row.parentElement.contains(status)) row = row.parentElement;
      const ctaText = norm(cta.innerText);
      const heading = row.querySelector('h1,h2,h3,h4,h5,h6,[role="heading"],strong,b');
      const lines = row.innerText.split('\n').map(norm).filter(l => l && l !== ctaText);
      const title = heading && !cta.contains(heading) ? norm(heading.innerText) : (lines[0] || '');
      const desc = lines.filter(l => l !== title).join(' ');
      const labels = [...row.querySelectorAll('[aria-label],[title],svg title,img[alt]')]
        .filter(e => !cta.contains(e) || e === cta)
        .map(e => e.getAttribute('aria-label') || e.getAttribute('title') || e.getAttribute('alt') || e.textContent || '')
        .join(' ');
      const style = getComputedStyle(cta);
      const locked = cta.matches(':disabled') || cta.getAttribute('aria-disabled') === 'true'
        || cta.hasAttribute('data-disabled') || !!cta.closest('[aria-disabled="true"],[data-disabled="true"]')
        || style.pointerEvents === 'none' || (cta.tagName === 'A' && !cta.getAttribute('href'))
        || LOCK.test(labels) || !!row.querySelector('[data-locked="true"],[data-icon*="lock" i],[class*="lock" i]');
      const completed = DONE.test(labels) || /^(abgeschlossen|erledigt|completed|done)$/i.test(ctaText)
        || !!row.querySelector('[data-completed="true"],[aria-checked="true"],[class*="complete" i],[class*="checkmark" i]');
      const mark = `${qi}-${tasks.length}`;
      cta.setAttribute('data-rt-quest', mark);
      tasks.push({mark, title, description: desc, cta: ctaText, href: cta.getAttribute('href') || '',
                  locked, completed});
    });
    quests.push({done: +done, total: +total, tasks});
  });
  return quests;
}
"""

# Links auf Event-Seiten (rewards.bing.com/…quest…) auf Dashboard und /earn
_LINKS_JS = r"""
() => [...new Set([...document.querySelectorAll('a[href]')].map(a => a.href).filter(h => {
  try { const u = new URL(h); return /(^|\.)rewards\.bing\.com$/.test(u.hostname)
        && /quest|punchcard|promotion|event|challenge/i.test(u.pathname + u.search); } catch (e) { return false; }
}))]
"""
_QUESTISH = re.compile(r"quest|punchcard|promotion|event|challenge", re.I)


def payload_links(payload: str) -> list[str]:
    """Event-Seiten aus den Seitendaten (Ziel-Links von Kacheln, die auf rewards.bing.com bleiben)."""
    out = []
    for dest in dashboard._values(payload, "destination"):
        if not isinstance(dest, str) or not dest:
            continue
        url = urljoin(dashboard.REWARDS, dest)
        u = urlparse(url)
        if u.hostname and u.hostname.endswith("rewards.bing.com") and _QUESTISH.search(u.path + u.query):
            out.append(url)
    return list(dict.fromkeys(out))


def _apply_payload(task: QuestTask, payload: str) -> None:
    """Erledigt/gesperrt aus den Seitendaten, falls die Aufgabe dort mit Titel steht (genauer als die Seite)."""
    i = payload.find(json.dumps(task.title, ensure_ascii=False)[1:-1])
    if i < 0:
        return
    raw = dashboard._enclosing_object(payload, i)
    if not raw:
        return
    if "isCompleted" in raw:
        task.completed = dashboard._flag(raw["isCompleted"])
    if "isLocked" in raw:
        task.locked = dashboard._flag(raw["isLocked"])


def read_quests(page, url: str) -> list[Quest]:
    try:
        raw = page.evaluate(_READ_JS)
    except Exception as e:
        log.warning("  Event-Seite nicht lesbar: %s", e)
        return []
    try:
        payload = dashboard.decode_payload(page.content())
    except Exception:
        payload = ""
    quests = []
    for q in raw:
        tasks = []
        for t in q["tasks"]:
            task = QuestTask(t["title"], t["description"], t["cta"], t["href"], t["locked"], t["completed"], t["mark"])
            if payload:
                _apply_payload(task, payload)
            tasks.append(task)
        quest = Quest(url=url, done=q["done"], total=q["total"], tasks=tasks)
        quest.name = event_name(tasks)
        quests.append(quest)
    return quests


def _shot(page, name: str) -> None:
    try:
        DEBUG_DIR.mkdir(parents=True, exist_ok=True)
        stem = DEBUG_DIR / f"{datetime.now():%Y%m%d-%H%M%S}_{re.sub(r'\W+', '_', name)[:50]}"
        page.screenshot(path=f"{stem}.png", full_page=True)
        (stem.with_suffix(".html")).write_text(page.content(), encoding="utf-8")
    except Exception:
        pass


def find_event_pages(ctx, cfg: dict) -> list[str]:
    """Rewards-Seiten, auf denen Events stehen können: Dashboard, /earn, verlinkte Event-Seiten, quests.urls."""
    urls = [dashboard.DASHBOARD_URL, dashboard.EARN_URL]
    for start in (dashboard.DASHBOARD_URL, dashboard.EARN_URL):
        page = dashboard.open_rewards_page(ctx, start)
        if not page:
            return []
        try:
            urls += page.evaluate(_LINKS_JS)
            urls += payload_links(dashboard.decode_payload(page.content()))
        except Exception as e:
            log.warning("Links auf %s nicht lesbar: %s", start, e)
        finally:
            page.close()
    urls += cfg.get("urls") or []
    return list(dict.fromkeys(u.split("#")[0] for u in urls))


# ---------- Aufgaben erledigen ----------

def _open_cta(ctx, page, task: QuestTask):
    """Klickt den Knopf der Aufgabe; liefert (Tab, eigener_tab)."""
    btn = page.locator(f"[data-rt-quest='{task.mark}']")
    btn.scroll_into_view_if_needed(timeout=5000)
    before, clicked = page.url, False
    try:
        with ctx.expect_page(timeout=15000) as new_page:
            btn.click(timeout=10000)
            clicked = True
    except PwTimeout:
        if not clicked:
            raise
        if page.url != before:
            return page, False  # Link öffnet im selben Tab
        return None, False  # Klick hat nichts geöffnet (z. B. Aktivieren direkt auf der Seite)
    tab = new_page.value
    tab.wait_for_load_state("domcontentloaded", timeout=45000)
    return tab, True


def _do(ctx, page, quest: Quest, task: QuestTask, kind: str, topics: dict, tried: list[str]) -> bool:
    """Führt eine Aufgabe aus. False, wenn nichts passiert ist."""
    tab, own = _open_cta(ctx, page, task)
    if tab is None:
        page.wait_for_timeout(random.randint(2000, 4000))
        return kind == ACTIVATE  # Aktivieren klappt auch ohne neuen Tab
    try:
        reject_consent(tab)
        if kind == QUIZ:
            solve_quiz(tab)
        elif kind == SEARCH:
            query = next((q for q in search_queries(task, quest.name, topics) if q not in tried), None)
            if query:
                tried.append(query)
                log.info("  -> Suche nach: %s", query)
                if not bing_search(tab, query):
                    log.warning("  Suche ohne Ergebnisseite (%s)", tab.url)
            else:
                log.warning("  Kein Suchbegriff gefunden – nur Link geöffnet")
        else:
            tab.mouse.wheel(0, random.randint(300, 900))
        tab.wait_for_timeout(random.randint(4000, 8000))
    finally:
        if own:
            tab.close()
        else:
            page.goto(quest.url, wait_until="load", timeout=60000)
            page.wait_for_timeout(1500)
            dashboard.expand_sections(page)
    return True


def _task_key(task: QuestTask) -> str:
    return task.title or task.cta


def _reload(page) -> None:
    page.reload(wait_until="load", timeout=60000)
    page.wait_for_timeout(1500)
    dashboard.expand_sections(page)


def _work_page(ctx, url: str, qcfg: dict, track: dict, deferred: list, seen: set) -> None:
    page = dashboard.open_rewards_page(ctx, url)
    if not page:
        return
    try:
        attempted: set[tuple[str, str]] = set()
        mine: set[str] = set()  # Events, die auf dieser Seite bearbeitet werden (nicht schon auf einer anderen)
        for rnd in range(ROUNDS):
            if rnd:
                _reload(page)  # Aktivieren schaltet die übrigen Aufgaben frei
            quests = [q for q in read_quests(page, url)
                      if (q.key not in seen if rnd == 0 else q.key in mine)]
            if not quests:
                break
            todo = []
            for quest in quests:
                if rnd == 0:
                    seen.add(quest.key)
                    mine.add(quest.key)
                    log.info("Event '%s': %s von %s Aufgaben erledigt (%s)", quest.name or "?", quest.done,
                             quest.total, url)
                    if not quest.tasks:
                        log.warning("  Keine Aufgaben erkannt – Screenshot und HTML: %s", DEBUG_DIR)
                        _shot(page, quest.key)
                for task in quest.tasks:
                    key = (quest.key, _task_key(task))
                    if key in attempted or task.completed:
                        continue
                    kind = classify(task)
                    if rnd == 0 or not task.locked:
                        log.info("  [%s] %s%s", KIND_NAMES[kind], task.title,
                                 " (gesperrt)" if task.locked else "")
                    if task.locked:
                        continue
                    todo.append((quest, task, kind))
            if not todo:
                break
            for quest, task, kind in todo:
                runstate.check_cancel()
                attempted.add((quest.key, _task_key(task)))
                _handle(ctx, page, quest, task, kind, qcfg, track, deferred)
        if mine:
            _reload(page)
            if any(not t.completed and not t.locked for q in read_quests(page, url) if q.key in mine for t in q.tasks):
                _shot(page, "nach_lauf")
    finally:
        page.close()


def _handle(ctx, page, quest, task, kind, qcfg, track, deferred) -> None:
    entry = track.setdefault(quest.key, {}).setdefault(_task_key(task), {"kind": kind})
    now = datetime.now()
    runstate.update_run(detail=f"{quest.name or 'Event'}: {task.title}")
    if kind == UNKNOWN:
        log.warning("  Unbekannte Aufgabe übersprungen: '%s' – %s [Knopf: %s]", task.title, task.description, task.cta)
        return
    if kind == MULTIDAY:
        rule = multiday_rule(task)
        done = rule["done"] if rule["done"] is not None else len(entry.get("times", []))
        if done >= rule["days"]:
            return
        wait = multiday_wait(entry, now, rule["gap_hours"])
        if wait > 0:
            if wait <= qcfg["max_wait_minutes"] * 60:
                log.info("  Mehrtägig (%s/%s Tage): nächster Tag erst in %s Min. möglich – wird am Ende nachgeholt",
                         done, rule["days"], round(wait / 60))
                deferred.append((quest, task, now + timedelta(seconds=wait)))
            else:
                log.info("  Mehrtägig (%s/%s Tage): heute schon erledigt, nächster Tag ab %s", done, rule["days"],
                         (now + timedelta(seconds=wait)).strftime("%d.%m. %H:%M"))
            return
        if _run_task(ctx, page, quest, task, multiday_action(task), qcfg):
            record(entry, datetime.now())
            save_track(track)
            log.info("  Mehrtägig: Tag %s von %s erledigt%s", done + 1, rule["days"],
                     f" (innerhalb von {rule['window']} Tagen)" if rule["window"] else "")
        return
    if len(entry.get("times", [])) >= MAX_ATTEMPTS:
        log.warning("  '%s' wurde schon %s-mal erledigt, zählt aber nicht – übersprungen", task.title, MAX_ATTEMPTS)
        return
    if _run_task(ctx, page, quest, task, kind, qcfg):
        record(entry, datetime.now())
        save_track(track)


def _run_task(ctx, page, quest, task, kind, qcfg) -> bool:
    try:
        ok = _do(ctx, page, quest, task, kind, qcfg["topic_queries"], [])
    except Exception as e:
        log.warning("  Aufgabe fehlgeschlagen: %s", e)
        return False
    pause(qcfg["delay_seconds"])
    return ok


def _run_deferred(ctx, deferred: list, qcfg: dict, track: dict) -> None:
    for quest, task, due in sorted(deferred, key=lambda d: d[2]):
        wait = (due - datetime.now()).total_seconds()
        if wait > 0:
            log.info("Warte %s Min. auf den nächsten Tag von '%s' (24 Stunden Abstand)", round(wait / 60), task.title)
            runstate.update_run(detail=f"Warte bis {due:%H:%M} ({task.title})")
            runstate.sleep(wait)
        page = dashboard.open_rewards_page(ctx, quest.url)
        if not page:
            return
        try:
            fresh = next((t for q in read_quests(page, quest.url) if q.key == quest.key
                          for t in q.tasks if _task_key(t) == _task_key(task)), None)
            if not fresh or fresh.locked or fresh.completed:
                continue
            _handle(ctx, page, quest, fresh, MULTIDAY, qcfg, track, [])
        finally:
            page.close()


def run(ctx, cfg: dict) -> None:
    qcfg = cfg["quests"]
    if not qcfg["enabled"]:
        log.info("Events sind in config.json ausgeschaltet (quests.enabled)")
        return
    urls = find_event_pages(ctx, qcfg)
    if not urls:
        log.error("Rewards-Seite nicht lesbar – eingeloggt? %s", LOGIN_HINT)
        return
    track = load_track()
    deferred, seen = [], set()
    for url in urls:
        runstate.check_cancel()
        try:
            _work_page(ctx, url, qcfg, track, deferred, seen)
        except Exception as e:
            log.warning("Event-Seite %s fehlgeschlagen: %s", url, e)
    if not seen:
        log.info("Keine Events gefunden. Steht eins auf der Rewards-Seite, die Adresse der Event-Seite in "
                 "config.json unter quests.urls eintragen.")
    if deferred:
        _run_deferred(ctx, deferred, qcfg, track)
    save_track(track)


def check_config(qcfg: dict) -> dict:
    """Vorgaben ergänzen und Grenzen prüfen (ValueError mit Hinweis bei ungültigen Werten)."""
    qcfg = {**DEFAULTS, **(qcfg or {})}
    wait = qcfg["max_wait_minutes"]
    if not isinstance(wait, int) or isinstance(wait, bool) or not WAIT_MIN <= wait <= WAIT_MAX:
        raise ValueError(f"quests.max_wait_minutes muss eine ganze Zahl zwischen {WAIT_MIN} und {WAIT_MAX} sein")
    delay = qcfg["delay_seconds"]
    if (not isinstance(delay, list) or len(delay) != 2 or not all(isinstance(d, (int, float)) for d in delay)
            or not 0 <= delay[0] <= delay[1] <= 600):
        raise ValueError("quests.delay_seconds muss [min, max] in Sekunden sein (0–600)")
    if not isinstance(qcfg["urls"], list) or not isinstance(qcfg["topic_queries"], dict):
        raise ValueError("quests.urls muss eine Liste und quests.topic_queries ein Objekt sein")
    qcfg["enabled"] = bool(qcfg["enabled"])
    return qcfg


DEFAULTS = {
    "enabled": True,
    "max_wait_minutes": DEFAULT_WAIT,
    "delay_seconds": [5, 12],
    "urls": [],
    "topic_queries": {},
}
