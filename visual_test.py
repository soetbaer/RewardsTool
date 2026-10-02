"""Testet die 'Visuelle Suche' mit verschiedenen Bildern und protokolliert Bings Upload-Antwort.

Aufruf (auf dem Server):  xvfb-run -a .venv/bin/python visual_test.py [eigenes-bild.jpg]
Mit eigenem Bild wird dieses zuerst getestet.
Screenshots landen in debug/visualtest/.
"""
import re
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
    def on_request(req):
        if "/images/kblob" in req.url:
            h = req.headers
            body = req.post_data_buffer or b""
            log.info("  Anfrage an Bing: %s, %s Bytes, Content-Type: %s", req.method, len(body),
                     h.get("content-type", "-")[:80])
            log.info("    Kopfzeilen: %s", ", ".join(sorted(h.keys())))
            log.info("    Token-Kopfzeile X-SNR-SignedToken-Kblob: %s",
                     "vorhanden" if any(k.lower() == "x-snr-signedtoken-kblob" for k in h) else "FEHLT")
            fields = sorted({n.decode() for n in re.findall(rb'name="([^"]+)"', body)})
            log.info("    Formularfelder: %s", ", ".join(fields) or "-")
    page.on("request", on_request)
    page.on("response", on_response)


def add_token(page):
    """Hängt Bings Upload-Token (steht im Formular #sbi_form) an die Upload-Anfrage – wie Bings eigener Code."""
    form = page.locator("#sbi_form")
    header = form.get_attribute("data-kblobstheader") or "X-SNR-SignedToken-Kblob"
    token = form.get_attribute("data-kblobsttoken")
    log.info("  Token im Formular: %s", "ja" if token else "NEIN")
    if token:
        page.route("**/images/kblob*", lambda route: route.continue_(
            headers={**route.request.headers, header: token}))


def try_image(ctx, name, img: Path, token: bool = False) -> bool:
    page = ctx.new_page()
    log_upload_responses(page)
    try:
        page.goto(STREAK_URL, wait_until="load")
        reject_consent(page)
        page.wait_for_timeout(3000)
        if token:
            add_token(page)
        page.locator("#sb_sbi").click(timeout=10000)
        page.wait_for_timeout(2500)
        log.info("Teste %s (%s KB)", name, img.stat().st_size // 1024)
        page.locator("#sb_fileinput").set_input_files(str(img))
        page.wait_for_timeout(12000)
        shot(page, f"{name}_nach_upload")
        err = page.locator("#bcid-err").is_visible()
        pinned = page.evaluate("() => !!document.querySelector('.sbi-paste-pin, [class*=pastepin], #sb_form img')")
        log.info("  Fehlermeldung sichtbar: %s | Bild im Suchfeld: %s | URL: %s", err, pinned, page.url)
        if not err:
            # Bing setzt den Cursor nach dem Anpinnen selbst ins Suchfeld ("Eingabetaste drücken, um mit diesem
            # Bild zu suchen") – also nur Enter, wie von Hand
            page.keyboard.press("Enter")
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
            # Zuerst: mit Upload-Token (eigenes Bild oder Bild des Tages)
            first = Path(sys.argv[1]).expanduser().resolve() if len(sys.argv) > 1 else None
            if not first:
                first = OUT / "tagesbild.jpg"
                first.write_bytes(req.get(daily.replace("_1920x1080", "_800x480")).body())
            if try_image(ctx, "mit_token", first, token=True):
                log.info("TREFFER – mit Upload-Token hat die Visuelle Suche gezählt")
                return
            log.info("  zählt nicht: mit Token")
            # Screenshot der Bing-Startseite als Bild (PNG)
            shot_page = ctx.new_page()
            shot_page.goto("https://www.bing.com/", wait_until="load")
            shot_page.wait_for_timeout(3000)
            screenshot = OUT / "screenshot_upload.png"
            shot_page.screenshot(path=str(screenshot))
            shot_page.close()
            if try_image(ctx, "screenshot", screenshot):
                log.info("TREFFER – mit einem Screenshot hat die Visuelle Suche gezählt")
                return
            log.info("  zählt nicht: screenshot")
            images = [("klein", daily.replace("_1920x1080", "_800x480") + "&w=800&h=480"),
                      ("wikimedia", WIKI), ("gross", daily)]
            if len(sys.argv) > 1:
                own = Path(sys.argv[1]).expanduser().resolve()
                log.info("Eigenes Bild: %s", own)
                if try_image(ctx, "eigenes", own):
                    log.info("TREFFER – mit dem eigenen Bild hat die Visuelle Suche gezählt")
                    return
                log.info("  zählt nicht: eigenes Bild")
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
