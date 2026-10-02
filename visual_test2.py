"""Visuelle Suche – Diagnose 2: Bings Upload-Code untersuchen und den Upload selbst (mit Token) abschicken.

Aufruf (auf dem Server):  xvfb-run -a .venv/bin/python visual_test2.py [bild.jpg]
"""
import base64
import json
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

from rewards import dashboard, runstate
from rewards.browser import open_context
from rewards.util import load_config, log, reject_consent, setup_logging
from rewards.visualsearch import STREAK_URL

BASE = Path(__file__).resolve().parent
OUT = BASE / "debug" / "visualtest"
ARCHIVE = "https://www.bing.com/HPImageArchive.aspx?format=js&idx=0&n=1&mkt=de-DE"

UPLOAD_JS = """async ([b64, prefix]) => {
  const form = document.getElementById('sbi_form');
  const token = form && form.getAttribute('data-kblobsttoken');
  const header = (form && form.getAttribute('data-kblobstheader')) || 'X-SNR-SignedToken-Kblob';
  const fd = new FormData();
  fd.append('imgurl', '');
  fd.append('cbir', 'sbi');
  fd.append('imageBin', prefix ? 'data:image/jpeg;base64,' + b64 : b64);
  const headers = {};
  if (token) headers[header] = token;
  const r = await fetch('/images/kblob?iss=sbiupload&FORM=SBIHMP&sbisrc=ImgPicker', {
    method: 'POST', body: fd, headers, credentials: 'include'});
  const text = await r.text();
  return {status: r.status, url: r.url, redirected: r.redirected, token: !!token,
          headers: Object.fromEntries([...r.headers].filter(([k]) => !k.startsWith('set-cookie'))),
          body: text.slice(0, 1500)};
}"""


def done(ctx):
    state = dashboard.load_state(ctx, earn=False)
    return state.visual_search if state else None


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
            log.info("Visuelle Suche vorher erledigt: %s", done(ctx))
            if len(sys.argv) > 1:
                data = Path(sys.argv[1]).expanduser().read_bytes()
            else:
                daily = "https://www.bing.com" + ctx.request.get(ARCHIVE).json()["images"][0]["url"].split("&")[0]
                data = ctx.request.get(daily.replace("_1920x1080", "_800x480")).body()
            b64 = base64.b64encode(data).decode()

            page = ctx.new_page()
            page.goto(STREAK_URL, wait_until="load")
            reject_consent(page)
            page.wait_for_timeout(4000)

            info = page.evaluate("""() => ({
              sbiUtil: typeof SbiUtil, ofsSignedToken: typeof (window.SbiUtil && SbiUtil.ofsSignedToken),
              ofs: typeof (window.SbiUtil && SbiUtil.ofs),
              ofsSrc: window.SbiUtil && SbiUtil.ofs ? String(SbiUtil.ofs).slice(0, 1200) : null,
              signedSrc: window.SbiUtil && SbiUtil.ofsSignedToken ? String(SbiUtil.ofsSignedToken).slice(0, 2000) : null,
              utilKeys: window.SbiUtil ? Object.keys(SbiUtil).join(',') : null,
              ua: navigator.userAgent})""")
            (OUT / "sbiutil.json").write_text(json.dumps(info, indent=2, ensure_ascii=False), encoding="utf-8")
            log.info("SbiUtil: %s | ofsSignedToken: %s | ofs: %s", info["sbiUtil"], info["ofsSignedToken"], info["ofs"])
            log.info("Funktionen in SbiUtil: %s", info["utilKeys"])
            log.info("User-Agent: %s", info["ua"])

            for prefix in (False, True):
                res = page.evaluate(UPLOAD_JS, [b64, prefix])
                log.info("Eigener Upload (%s): HTTP %s | Token: %s | umgeleitet: %s | URL: %s",
                         "mit data:-Präfix" if prefix else "nur Base64", res["status"], res["token"],
                         res["redirected"], res["url"][:300])
                log.info("  Antwort-Kopfzeilen: %s", json.dumps(res["headers"], ensure_ascii=False)[:800])
                log.info("  Antwort: %s", res["body"][:600].replace("\n", " "))
                target = res["url"] if res["redirected"] else None
                if not target and "bcid" in res["body"]:
                    log.info("  bcid in der Antwort gefunden")
                if target and "bing.com" in target:
                    page.goto(target, wait_until="load")
                    page.wait_for_timeout(10000)
                    page.screenshot(path=str(OUT / f"eigener_upload_{int(prefix)}.png"))
                    log.info("  Ergebnisseite: %s", page.url)
                    if done(ctx):
                        log.info("TREFFER – eigener Upload (%s) hat gezählt", "mit Präfix" if prefix else "nur Base64")
                        return
                    page.goto(STREAK_URL, wait_until="load")
                    page.wait_for_timeout(3000)
            log.info("Visuelle Suche danach erledigt: %s. Details: %s", done(ctx), OUT / "sbiutil.json")
            ctx.close()
    finally:
        lock.release()


if __name__ == "__main__":
    main()
