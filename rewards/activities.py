import random
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import dashboard
from .searches import bing_search
from .util import LOGIN_HINT, log, pause, reject_consent

# Markiert die Ziel-Kachel per data-Attribut, damit Playwright sie echt anklicken kann
_MARK_JS = """
([section, href, title, desc]) => {
  const norm = s => (s || '').replace(/\\u200b/g, '').replace(/\\s+/g, ' ').trim();
  document.querySelectorAll('[data-rt-target]').forEach(e => e.removeAttribute('data-rt-target'));
  const scope = section ? document.getElementById(section) : document;
  if (!scope) return false;
  const links = [...scope.querySelectorAll('a[href]')];
  const hit = links.find(a => a.getAttribute('href') === href && norm(a.innerText).includes(norm(title))
                              && norm(a.innerText).includes(norm(desc)))
           || links.find(a => a.getAttribute('href') === href && norm(a.innerText).includes(norm(title)))
           || links.find(a => norm(a.innerText).includes(norm(title)) && norm(a.innerText).includes(norm(desc)));
  if (!hit) return false;
  hit.setAttribute('data-rt-target', '1');
  return true;
}
"""


def _has_query(url: str) -> bool:
    u = urlparse(url)
    return "bing.com" in u.netloc and u.path.startswith("/search") and bool(parse_qs(u.query).get("q"))


def explore_query(act: dashboard.Activity, topics: dict) -> str | None:
    """Suchbegriff für 'Auf Bing erkunden'-Aufgaben (aus offerId-Thema oder Kacheltext)."""
    if _has_query(act.url):
        return None  # Kachel-Link führt bereits eine Suche aus
    m = re.search(r"_([a-z]+)_exploreonbing", act.offer_id.lower())
    if m and m.group(1) in topics:
        return topics[m.group(1)]
    text = f"{act.title} {act.description}".lower()
    for keyword, query in topics.items():
        if keyword in text:
            return query
    # "Suchen Sie auf Bing nach den besten Streaming-Plattformen" -> "den besten Streaming-Plattformen"
    m = re.search(r"(?:suchen sie|suche|search)\s+(?:auf|on)\s+bing\s+(?:nach|for)\s+(.+)", act.description, re.I)
    if m:
        return m.group(1).rstrip(".!")
    # "Suchen Sie auf Bing, um Internetpläne in Ihrer Region zu vergleichen" -> "Internetpläne in Ihrer Region vergleichen"
    m = re.search(r"bing,?\s+um\s+(.+?)\s+zu\s+(\w+)", act.description, re.I)
    if m:
        return f"{m.group(1)} {m.group(2)}"
    return act.title if act.section == "Auf Bing erkunden" else None


def _click(page, locator) -> bool:
    try:
        locator.click(timeout=5000)
        page.wait_for_timeout(random.randint(1500, 3000))
        return True
    except Exception:
        return False


def solve_quiz(page) -> None:
    """Best-Effort-Lösung für Umfragen und Quizze (klassisches Bing-Quiz-Layout)."""
    page.wait_for_timeout(2500)

    if page.locator("#btoption0").count():
        _click(page, page.locator(f"#btoption{random.randint(0, 1)}"))
        return

    if page.locator("#rqStartQuiz").count():
        _click(page, page.locator("#rqStartQuiz"))

    for _ in range(30):
        opts = page.locator("[id^='rqAnswerOption']")
        if opts.count() == 0 or page.locator("#quizCompleteContainer").is_visible():
            break
        correct = page.locator("[id^='rqAnswerOption'][iscorrectoption='True']")
        if correct.count():
            for i in range(correct.count()):
                _click(page, correct.nth(i))
            continue
        answer = page.evaluate(
            "() => (window._w && _w.rewardsQuizRenderInfo) ? _w.rewardsQuizRenderInfo.correctAnswer : null"
        )
        target = page.locator(f"[id^='rqAnswerOption'][data-option='{answer}']") if answer else None
        if not (target and target.count() and _click(page, target.first)):
            _click(page, opts.nth(random.randrange(opts.count())))

    for _ in range(10):
        abc = page.locator(".wk_OptionClickClass")
        if abc.count() == 0:
            break
        _click(page, abc.nth(random.randrange(abc.count())))
        _click(page, page.locator(".wk_buttons div, #nextQuestionbtn").first)


def _open_card(ctx, rewards_page, act: dashboard.Activity):
    """Klickt die Kachel auf der Rewards-Seite an und liefert den geöffneten Tab."""
    found = rewards_page.evaluate(_MARK_JS, [act.dom_section, act.url, act.title, act.description])
    if not found:
        log.warning("  Kachel nicht auf der Seite gefunden")
        return None
    card = rewards_page.locator("[data-rt-target='1']")
    card.scroll_into_view_if_needed()
    with ctx.expect_page(timeout=20000) as new_page:
        card.click()
    tab = new_page.value
    tab.wait_for_load_state("domcontentloaded", timeout=45000)
    return tab


DEBUG_DIR = Path(__file__).resolve().parent.parent / "debug" / "explore"
FLYOUT_FRAME = "rewards/panelflyout"


def _shot(tab, act: dashboard.Activity, step: str) -> None:
    """Screenshot eines Ablaufschritts (zur Fehlersuche bei 'Auf Bing erkunden')."""
    try:
        DEBUG_DIR.mkdir(parents=True, exist_ok=True)
        topic = re.sub(r"\W+", "_", act.offer_id)[:60]
        tab.screenshot(path=str(DEBUG_DIR / f"{datetime.now():%Y%m%d-%H%M%S}_{topic}_{step}.png"))
    except Exception:
        pass


def _open_rewards_flyout(tab, act: dashboard.Activity) -> None:
    """Öffnet das Rewards-Seitenfenster auf bing.com.

    Im normalen Browser öffnet es sich über rwAutoFlyout=exb von selbst und meldet dabei die Aktivierung
    der Kategorie auf Bing-Seite. In der Automatisierung bleibt es zu – dann über das Punkte-Symbol öffnen.
    """
    tab.wait_for_timeout(4000)
    if not any(FLYOUT_FRAME in f.url for f in tab.frames):
        medallion = tab.locator("#rh_rwm, #id_rh_w").first
        try:
            medallion.click(timeout=10000)
        except Exception as e:
            log.warning("  Rewards-Seitenfenster nicht zu öffnen: %s", e)
            return
    try:
        tab.wait_for_function(f"() => [...document.querySelectorAll('iframe')].some(f => f.src.includes('{FLYOUT_FRAME}'))",
                              timeout=15000)
    except Exception:
        log.warning("  Rewards-Seitenfenster hat sich nicht geöffnet")
        return
    tab.wait_for_timeout(6000)  # Seitenfenster lädt und meldet seine Aktivität
    _shot(tab, act, "1_seitenfenster")
    log.info("  Rewards-Seitenfenster geöffnet")
    tab.keyboard.press("Escape")
    tab.wait_for_timeout(1000)


def _work(ctx, rewards_page, act: dashboard.Activity, topics: dict) -> None:
    tab = _open_card(ctx, rewards_page, act)
    if not tab:
        return
    try:
        reject_consent(tab)
        is_explore = act.section == "Auf Bing erkunden"
        if is_explore:
            _open_rewards_flyout(tab, act)
        else:
            solve_quiz(tab)
        query = explore_query(act, topics)
        if query:
            log.info("  -> Suche nach: %s", query)
            if not bing_search(tab, query):
                log.warning("  Suche ohne Ergebnisseite (%s)", tab.url)
        tab.wait_for_timeout(random.randint(4000, 7000))
        if is_explore:
            _shot(tab, act, "2_suche")
    finally:
        tab.close()


def run(ctx, cfg: dict) -> None:
    acfg = cfg["activities"]
    state = dashboard.load_state(ctx)
    if not state:
        log.error("Dashboard nicht lesbar – eingeloggt? %s", LOGIN_HINT)
        return

    todo = state.open_activities()
    log.info("%s offene Aktivitäten", len(todo))
    if cfg.get("_headless") and any(a.section == "Auf Bing erkunden" for a in todo):
        log.warning("'Auf Bing erkunden' wird nur mit sichtbarem Browser gutgeschrieben. Ohne --headless starten "
                    "(auf dem Server mit xvfb-run).")
    pages = {}
    try:
        for act in todo:
            log.info("[%s] %s (%s Pkt.)", act.section, act.title, act.points)
            if act.page_url not in pages:
                pages[act.page_url] = dashboard.open_rewards_page(ctx, act.page_url)
            rewards_page = pages[act.page_url]
            if not rewards_page:
                continue
            try:
                _work(ctx, rewards_page, act, acfg["topic_queries"])
            except Exception as e:
                log.warning("  Aktivität fehlgeschlagen: %s", e)
            pause(acfg["delay_seconds"])
    finally:
        for p in pages.values():
            if p:
                p.close()

    state = dashboard.load_state(ctx)
    still_open = state.open_activities() if state else []
    for act in still_open:
        log.warning("Noch offen: [%s] %s", act.section, act.title)
    if any(a.section == "Auf Bing erkunden" for a in still_open):
        log.warning("Screenshots der 'Auf Bing erkunden'-Schritte: %s", DEBUG_DIR)
