"""Streak 'Visuelle Suche': täglich eine Bing-Suche mit einem Bild.

Lädt das Bing-Bild des Tages über das Kamera-Symbol im Suchfeld hoch. Die Ergebnisseite meldet die Aktivität
an Rewards (reportActivity) und der Streak gilt für heute als erledigt.
"""
import tempfile
from pathlib import Path

from . import dashboard
from .util import LOGIN_HINT, log, reject_consent

STREAK_URL = "https://www.bing.com/?features=vsstreak,vstooltip&form=ML2XES"


def _image_url(page) -> str | None:
    """Bild des Tages der Bing-Startseite (Download-Link, sonst og:image)."""
    return page.evaluate("""() => document.querySelector('a.downloadLink')?.href
        || document.querySelector('meta[property="og:image"]')?.content || null""")


def visual_search(page) -> bool:
    """Führt eine visuelle Suche aus; True, wenn eine Ergebnisseite geöffnet wurde."""
    page.goto(STREAK_URL, wait_until="load")
    reject_consent(page)
    page.wait_for_timeout(3000)
    url = _image_url(page)
    if not url:
        log.warning("  Kein Bild des Tages auf bing.com gefunden")
        return False
    resp = page.request.get(url)
    if not resp.ok:
        log.warning("  Bild nicht ladbar (HTTP %s)", resp.status)
        return False
    with tempfile.TemporaryDirectory() as tmp:
        img = Path(tmp) / "bild.jpg"
        img.write_bytes(resp.body())
        page.locator("#sb_sbi").click(timeout=10000)  # Kamera-Symbol im Suchfeld
        page.locator("#sb_fileinput").set_input_files(str(img))
        try:
            page.wait_for_url("**/search?**", timeout=30000)
        except Exception:
            log.warning("  Keine Ergebnisseite nach dem Hochladen (%s)", page.url)
            return False
    page.wait_for_timeout(8000)  # Ergebnisseite meldet die Aktivität erst nach dem Laden
    return True


def run(ctx, cfg: dict) -> None:
    state = dashboard.load_state(ctx, earn=False)
    if not state:
        log.error("Dashboard nicht lesbar – eingeloggt? %s", LOGIN_HINT)
        return
    if state.visual_search is None:
        log.info("Visuelle Suche wird nicht angeboten")
        return
    if state.visual_search:
        log.info("Visuelle Suche heute schon erledigt")
        return
    page = ctx.new_page()
    try:
        ok = visual_search(page)
    finally:
        page.close()
    if not ok:
        return
    state = dashboard.load_state(ctx, earn=False)
    if state and state.visual_search:
        log.info("Visuelle Suche erledigt")
    else:
        log.warning("Visuelle Suche wurde nicht gutgeschrieben")
