import math
import random
import time
import urllib.request
import xml.etree.ElementTree as ET
from urllib.parse import quote_plus

from . import dashboard
from .util import BING, log, reject_consent

FALLBACK_TERMS = [
    "Wetter Wochenende", "Rezept Apfelkuchen", "Bundesliga Tabelle", "Aktienkurs DAX",
    "Zugverbindung Berlin München", "Kinoprogramm", "Nachrichten heute", "Fahrrad kaufen",
    "Sehenswürdigkeiten Rom", "Tipps gegen Erkältung", "Serien Empfehlungen", "Gartenarbeit Herbst",
    "Elektroauto Reichweite", "Weihnachtsmarkt 2026", "Formel 1 Ergebnisse", "Pilze sammeln",
    "Kaffee Zubereitung", "Laufschuhe Test", "Urlaub Ostsee", "Brot backen",
    "Smartphone Vergleich", "Vulkane Island", "Wandern Alpen", "Hausmittel Kopfschmerzen",
    "Schach lernen", "Nordlichter Deutschland", "Balkonkraftwerk", "Oktoberfest Geschichte",
    "Gitarre lernen", "Vegane Rezepte", "Wie funktioniert KI", "Sternbilder Herbst",
    "Fußball WM Geschichte", "Hunderassen", "Zimmerpflanzen pflegen", "Städtetrip Wien",
    "Photovoltaik Kosten", "Podcast Empfehlungen", "Tiramisu Rezept", "Sonnenfinsternis",
    "Energiesparen Tipps", "Marathon Training", "Museen Berlin", "Brettspiele Neuheiten",
]


def trending_terms(geo: str) -> list[str]:
    """Aktuelle Suchtrends aus dem Google-Trends-RSS-Feed."""
    try:
        url = f"https://trends.google.com/trending/rss?geo={geo}"
        with urllib.request.urlopen(url, timeout=15) as r:
            root = ET.fromstring(r.read())
        return [t.text.strip() for t in root.iter("title") if t.text][1:]  # 1. Titel = Feedname
    except Exception as e:
        log.warning("Trends konnten nicht geladen werden (%s), nutze Fallback-Liste", e)
        return []


def build_terms(count: int, geo: str) -> list[str]:
    terms = list(dict.fromkeys(trending_terms(geo)))
    fallback = FALLBACK_TERMS[:]
    random.shuffle(fallback)
    for t in fallback:
        if len(terms) >= count:
            break
        if t not in terms:
            terms.append(t)
    random.shuffle(terms)
    return terms[:count]


def _on_results(page) -> bool:
    return "bing.com/search" in page.url


def _wait_loaded(page) -> None:
    try:
        page.wait_for_load_state("load", timeout=20000)
    except Exception:
        pass


def bing_search(page, query: str) -> bool:
    """Sucht über das Bing-Suchfeld; True, wenn eine Ergebnisseite geöffnet wurde."""
    if "bing.com" not in page.url or page.locator("#sb_form_q").count() == 0:
        page.goto(BING, wait_until="load")
        reject_consent(page)
    _wait_loaded(page)  # Auf langsamen Rechnern greift Enter sonst, bevor das Suchfeld bereit ist
    box = page.locator("#sb_form_q")
    box.click()
    box.fill("")
    box.press_sequentially(query, delay=random.randint(40, 110))
    box.press("Enter")
    try:
        page.wait_for_url("**/search?**", timeout=10000)
    except Exception:
        pass
    if not _on_results(page):
        log.warning("Suchfeld hat nicht abgeschickt – rufe Suche direkt auf: %s", query)
        page.goto(f"https://www.bing.com/search?q={quote_plus(query)}&form=QBLH", wait_until="load")
    # Bing meldet die Suche erst nach dem Laden per Skript an Rewards – Seite so lange offen lassen
    _wait_loaded(page)
    page.wait_for_timeout(3000)
    return _on_results(page)


def search_progress(ctx) -> tuple[int, int] | None:
    state = dashboard.load_state(ctx, dashboard=False)
    return state.search if state else None


def run(ctx, cfg: dict) -> None:
    scfg = cfg["search"]
    progress = search_progress(ctx)

    if progress:
        done, total = progress
        log.info("Suche: %s/%s Punkte", done, total)
        if done >= total:
            log.info("Suchpunkte bereits vollständig.")
            return
        needed = math.ceil((total - done) / scfg["points_per_search"]) + scfg["extra_searches"]
    else:
        needed = scfg["fallback_count"]
        log.info("Suchfortschritt unbekannt, führe %s Suchen aus", needed)

    delay = scfg["delay"]
    log.info("Pause zwischen Suchen: %s s", delay)
    page = ctx.new_page()
    terms = build_terms(needed, scfg["trends_geo"])
    for i, term in enumerate(terms, 1):
        try:
            if bing_search(page, term):
                log.info("Suche %s/%s: %s", i, len(terms), term)
            else:
                log.warning("Suche %s/%s: keine Ergebnisseite (%s)", i, len(terms), page.url)
        except Exception as e:
            log.warning("Suche '%s' fehlgeschlagen: %s", term, e)
        time.sleep(delay)

        # Alle 10 Suchen prüfen, ob das Tageslimit erreicht ist und ob überhaupt gezählt wird
        if i % 10 == 0:
            before = progress
            progress = search_progress(ctx)
            if progress and progress[0] >= progress[1]:
                log.info("Suchpunkte vollständig (%s/%s).", *progress)
                break
            if progress and before and progress[0] <= before[0]:
                log.error(
                    "Die letzten 10 Suchen wurden NICHT gezählt (%s/%s). Vermutlich ist die Bing-Anmeldung "
                    "abgelaufen – Sitzung neu exportieren/importieren. Breche ab.", *progress
                )
                break
            if progress:
                log.info("Fortschritt: %s/%s", *progress)
    page.close()
    if progress:
        log.info("Suche beendet bei %s/%s Punkten", *progress)
