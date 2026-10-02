"""Testet Wege für den Streak 'Visuelle Suche', bis einer zählt. Hält jeden Schritt per Screenshot fest.

Aufruf (auf dem Server):  xvfb-run -a .venv/bin/python visual_test.py
Screenshots und HTML landen in debug/visualtest/.
"""
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import quote

from playwright.sync_api import sync_playwright

from rewards import dashboard, runstate
from rewards.browser import open_context
from rewards.util import load_config, log, reject_consent, setup_logging
from rewards.visualsearch import STREAK_URL, _image_url

BASE = Path(__file__).resolve().parent
OUT = BASE / "debug" / "visualtest"


def shot(page, name):
    try:
        page.screenshot(path=str(OUT / f"{time.strftime('%H%M%S')}_{name}.png"))
    except Exception:
        pass


def done(ctx):
    state = dashboard.load_state(ctx, earn=False)
    return state.visual_search if state else None


def prepare(page):
    page.goto(STREAK_URL, wait_until="load")
    reject_consent(page)
    page.wait_for_timeout(3000)
    url = _image_url(page)
    log.info("Bild des Tages: %s", url)
    return url


def wait_result(page, name) -> bool:
    start = STREAK_URL
    for _ in range(30):
        page.wait_for_timeout(1000)
        if page.url != start:
            break
    log.info("  URL danach: %s", page.url)
    shot(page, f"{name}_ergebnis")
    page.wait_for_timeout(8000)
    return page.url != start


def way_upload(page, img: Path):
    """Wie bisher: Kamera-Symbol, Datei ins (erste) Datei-Feld."""
    page.locator("#sb_sbi").click(timeout=10000)
    page.wait_for_timeout(2500)
    shot(page, "upload_panel")
    (OUT / "panel.html").write_text(page.content(), encoding="utf-8")
    inputs = page.evaluate("() => [...document.querySelectorAll('input')].map(i => "
                           "`${i.type} id=${i.id} name=${i.name} placeholder=${i.placeholder} visible=${!!i.offsetParent}`)")
    log.info("  Eingabefelder nach Klick aufs Kamera-Symbol:\n    %s", "\n    ".join(inputs))
    page.locator("input[type=file]").first.set_input_files(str(img))
    return wait_result(page, "upload")


def way_pin(page, img: Path, question: str = ""):
    """Bing pinnt das hochgeladene Bild ins Suchfeld (uploadAndPin) – danach muss die Suche abgeschickt werden."""
    page.locator("#sb_sbi").click(timeout=10000)
    page.wait_for_timeout(2500)
    page.locator("#sb_fileinput").set_input_files(str(img))
    page.wait_for_timeout(10000)  # Hochladen und Anpinnen
    shot(page, "pin_angeheftet")
    fields = page.evaluate("() => [...document.querySelectorAll('#sb_form input')].map(i => "
                           "`${i.type} name=${i.name} value=${(i.value || '').slice(0, 60)}`)")
    log.info("  Felder im Suchformular nach dem Anpinnen:\n    %s", "\n    ".join(fields))
    box = page.locator("#sb_form_q")
    box.click()
    if question:
        box.press_sequentially(question, delay=60)
    pages_before = len(page.context.pages)
    box.press("Enter")
    page.wait_for_timeout(3000)
    if len(page.context.pages) > pages_before:
        page = page.context.pages[-1]
        log.info("  Ergebnis in neuem Tab")
    elif page.url.startswith("https://www.bing.com/?"):
        page.locator("#sb_form_go, #search_icon").first.click(timeout=5000)
    return wait_result(page, "pin")


def way_paste_url(page, url: str):
    """Kamera-Symbol, Bild-URL ins Textfeld 'Bild oder Link einfügen'."""
    page.locator("#sb_sbi").click(timeout=10000)
    page.wait_for_timeout(2500)
    field = page.locator("input[type=text]:visible, input[type=url]:visible, input:not([type]):visible").filter(
        has_not=page.locator("#sb_form_q")).last
    field.fill(url)
    shot(page, "paste_eingefuegt")
    field.press("Enter")
    return wait_result(page, "paste")


def way_direct(page, url: str):
    """Ergebnisseite der visuellen Suche direkt mit der Bild-URL aufrufen."""
    page.goto("https://www.bing.com/images/search?view=detailv2&iss=sbi&form=SBIHMP&sbisrc=UrlPaste&q=imgurl:"
              + quote(url, safe=""), wait_until="load")
    shot(page, "direkt_ergebnis")
    page.wait_for_timeout(8000)
    log.info("  URL danach: %s", page.url)
    return True


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    cfg = load_config(BASE / "config.json")
    setup_logging(BASE / "logs")
    lock = runstate.ProfileLock()
    if not lock.acquire():
        sys.exit("Das Browserprofil ist belegt (Lauf oder Microsoft-Anmeldung aktiv?) – später erneut versuchen.")
    try:
        with sync_playwright() as pw:
            ctx = open_context(pw, cfg, BASE, headless=False)
            status = done(ctx)
            log.info("Visuelle Suche vorher erledigt: %s", status)
            if status is None:
                log.error("Streak 'Visuelle Suche' wird nicht angeboten.")
                return
            if status:
                log.warning("Heute schon erledigt – Test morgen vor dem Lauf wiederholen (Timer kurz ausschalten).")
                return
            with tempfile.TemporaryDirectory() as tmp:
                img = Path(tmp) / "bild.jpg"
                for name, way in (("Anpinnen + Enter", "pin"), ("Anpinnen + Frage", "pinq"), ("Upload", "upload"),
                                  ("Bild-URL einfügen", "paste"), ("Direktaufruf", "direct")):
                    page = ctx.new_page()
                    try:
                        url = prepare(page)
                        if not url:
                            log.error("Kein Bild des Tages gefunden")
                            return
                        if not img.exists():
                            img.write_bytes(page.request.get(url).body())
                        if way == "pin":
                            way_pin(page, img)
                        elif way == "pinq":
                            way_pin(page, img, "Was ist auf diesem Bild?")
                        elif way == "upload":
                            way_upload(page, img)
                        elif way == "paste":
                            way_paste_url(page, url)
                        else:
                            way_direct(page, url)
                    except Exception as e:
                        log.warning("  %s fehlgeschlagen: %s", name, str(e).splitlines()[0])
                        shot(page, f"{way}_fehler")
                    finally:
                        page.close()
                    if done(ctx):
                        log.info("TREFFER – dieser Weg hat gezählt: %s", name)
                        return
                    log.info("  zählt nicht: %s", name)
            log.warning("Kein Weg hat gezählt. Screenshots: %s", OUT)
            ctx.close()
    finally:
        lock.release()


if __name__ == "__main__":
    main()
