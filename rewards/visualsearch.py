"""Streak 'Visuelle Suche': täglich eine Bing-Suche mit einem Bild.

Ablauf wie von Hand: Kamera-Symbol im Suchfeld, Bing-Bild des Tages hochladen. Bing heftet das Bild ins Suchfeld
(„uploadAndPin“), danach startet Enter die Suche. Die Ergebnisseite meldet die Aktivität an Rewards.

Bing lehnt den Upload bei manchen Konten ab (HTTP 400 von /images/kblob, auch bei der Suche von Hand) – dann wird
die Aufgabe mit einem klaren Hinweis übersprungen.
"""
import tempfile
from pathlib import Path

from . import dashboard
from .util import LOGIN_HINT, log, reject_consent

STREAK_URL = "https://www.bing.com/?features=vsstreak,vstooltip&form=ML2XES"
ARCHIVE_URL = "https://www.bing.com/HPImageArchive.aspx?format=js&idx=0&n=1&mkt=de-DE"


class UploadRejected(Exception):
    """Bing hat den Bild-Upload abgelehnt."""


def _image_url(page) -> str | None:
    """Bild des Tages: von der Startseite (lädt teils verzögert), sonst über Bings Bild-Archiv."""
    for _ in range(5):
        url = page.evaluate("""() => document.querySelector('a.downloadLink')?.href
            || document.querySelector('meta[property="og:image"]')?.content || null""")
        if url:
            return url
        page.wait_for_timeout(2000)
    try:
        return "https://www.bing.com" + page.request.get(ARCHIVE_URL).json()["images"][0]["url"]
    except Exception:
        return None


def visual_search(page) -> bool:
    """Führt eine visuelle Suche aus; True, wenn eine Ergebnisseite geöffnet wurde."""
    page.goto(STREAK_URL, wait_until="load")
    reject_consent(page)
    url = _image_url(page)
    if not url:
        log.warning("  Kein Bild des Tages gefunden")
        return False
    resp = page.request.get(url)
    if not resp.ok:
        log.warning("  Bild nicht ladbar (HTTP %s)", resp.status)
        return False

    upload = {}
    page.on("response", lambda r: "/images/kblob" in r.url and upload.setdefault("status", r.status))
    with tempfile.TemporaryDirectory() as tmp:
        img = Path(tmp) / "bild.jpg"
        img.write_bytes(resp.body())
        page.locator("#sb_sbi").click(timeout=10000)  # Kamera-Symbol im Suchfeld
        page.wait_for_timeout(2000)
        page.locator("#sb_fileinput").set_input_files(str(img))
        for _ in range(20):  # Upload und Anheften abwarten
            page.wait_for_timeout(500)
            if upload.get("status", 200) != 200 or page.locator("#bcid-err").is_visible():
                raise UploadRejected(f"HTTP {upload.get('status', '?')}")
            if "/search" in page.url:  # ältere Variante: Ergebnisseite öffnet sich direkt
                break
        page.wait_for_timeout(2000)
        if "/search" not in page.url:
            # Bild hängt im Suchfeld, Cursor steht dort – Enter startet die Suche
            page.keyboard.press("Enter")
            try:
                page.wait_for_url("**/search?**", timeout=20000)
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
    except UploadRejected as e:
        log.warning("Visuelle Suche übersprungen: Bing lehnt den Bild-Upload für dieses Konto ab (%s). "
                    "Das passiert auch bei der Suche von Hand – das Tool kann daran nichts ändern.", e)
        return
    finally:
        page.close()
    if not ok:
        return
    state = dashboard.load_state(ctx, earn=False)
    if state and state.visual_search:
        log.info("Visuelle Suche erledigt")
    else:
        log.warning("Visuelle Suche wurde nicht gutgeschrieben")
