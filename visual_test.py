"""Testet die 'Visuelle Suche' mit verschiedenen Bildern und protokolliert Bings Upload-Antwort.

Aufruf (auf dem Server):  xvfb-run -a .venv/bin/python visual_test.py
Screenshots landen in debug/visualtest/.
"""
import sys
import tempfile
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

from rewards import dashboard, runstate
from rewards.browser import open_context
from rewards.util import load_config, log, reject_consent, setup_logging
from rewards.visualsearch import STREAK_URL

BASE = Path(__file__).resolve().parent
OUT = BASE / "debug" / "visualtest"
ARCHIVE = "https://www.bing.com/HPImageArchive.aspx?format=js&idx=0&n=1&mkt=de-DE"
WIKI = "https://upload.wikimedia.org/wikipedia/commons/thumb/3/3a/Cat03.jpg/640px-Cat03.jpg"


def shot(page, name):
    try:
        page.screenshot(path=str(OUT / f"{time.strftime('%H%M%S')}_{name}.png"))
    except Exception:
        pass


def done(ctx):
    state = dashboard.load_state(ctx, earn=False)
    return state.visual_search if state else None


def log_upload_responses(page):
    def on_response(resp):
        if "/images/kblob" in resp.url or "/images/blob" in resp.url or "bcid=" in resp.url:
            try:
                body = resp.text()[:300].replace("\n", " ")
            except Exception:
                body = "?"
            log.info("  Bing-Antwort %s %s | Location: %s | %s", resp.status, resp.url[:160],
                     resp.headers.get("location", "-")[:200], body)
    page.on("response", on_response)


def try_image(ctx, name, img: Path) -> bool:
    page = ctx.new_page()
    log_upload_responses(page)
    try:
        page.goto(STREAK_URL, wait_until="load")
        reject_consent(page)
        page.wait_for_timeout(3000)
        page.locator("#sb_sbi").click(timeout=10000)
        page.wait_for_timeout(2500)
        log.info("Teste %s (%s KB)", name, img.stat().st_size // 1024)
        page.locator("#sb_fileinput").set_input_files(str(img))
        page.wait_for_timeout(12000)
        shot(page, f"{name}_nach_upload")
        err = page.locator("#bcid-err").is_visible()
        pinned = page.evaluate("() => !!document.querySelector('.sbi-paste-pin, [class*=pastepin], #sb_form img')")
        log.info("  Fehlermeldung sichtbar: %s | Bild im Suchfeld: %s | URL: %s", err, pinned, page.url)
        if pinned and page.url.startswith("https://www.bing.com/?"):
            page.keyboard.press("Escape")
            page.locator("#sb_form_q").press("Enter")
            page.wait_for_timeout(10000)
            log.info("  Nach Enter: %s", page.url)
            shot(page, f"{name}_ergebnis")
    except Exception as e:
        log.warning("  Fehler: %s", str(e).splitlines()[0])
        shot(page, f"{name}_fehler")
    finally:
        page.close()
    return bool(done(ctx))


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
            if status is not False:
                log.warning("Nicht offen (erledigt oder nicht angeboten) – nichts zu testen.")
                return
            req = ctx.request
            daily = "https://www.bing.com" + req.get(ARCHIVE).json()["images"][0]["url"].split("&")[0]
            images = [("klein", daily.replace("_1920x1080", "_800x480") + "&w=800&h=480"),
                      ("wikimedia", WIKI), ("gross", daily)]
            with tempfile.TemporaryDirectory() as tmp:
                for name, url in images:
                    resp = req.get(url)
                    log.info("Bild %s: %s -> HTTP %s, %s", name, url, resp.status, resp.headers.get("content-type"))
                    if not resp.ok:
                        continue
                    img = Path(tmp) / f"{name}.jpg"
                    img.write_bytes(resp.body())
                    if try_image(ctx, name, img):
                        log.info("TREFFER – mit Bild '%s' hat die Visuelle Suche gezählt", name)
                        return
                    log.info("  zählt nicht: %s", name)
            log.warning("Nichts hat gezählt. Screenshots: %s", OUT)
            ctx.close()
    finally:
        lock.release()


if __name__ == "__main__":
    main()
