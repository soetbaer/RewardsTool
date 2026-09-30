"""Testet Suchbegriffe für eine offene 'Auf Bing erkunden'-Aufgabe, bis einer zählt.

Aufruf (auf dem Server):
  xvfb-run -a .venv/bin/python explore_test.py "Liedtexte" "Wonderwall Lyrics" "Wonderwall Songtext" ...

Erster Parameter: ein Stück vom Kacheltitel. Danach die Suchbegriffe in der gewünschten Reihenfolge.
Pro Begriff: Kachel anklicken, Rewards-Seitenfenster öffnen, suchen, 20 s warten, Status neu laden.
Sobald die Aufgabe erledigt ist, wird der Begriff gemeldet und abgebrochen.
"""
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

from rewards import activities, dashboard, runstate
from rewards.browser import open_context
from rewards.searches import bing_search
from rewards.util import load_config, log, reject_consent, setup_logging

BASE = Path(__file__).resolve().parent


def main():
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    title, queries = sys.argv[1].lower(), sys.argv[2:]
    cfg = load_config(BASE / "config.json")
    setup_logging(BASE / "logs")
    lock = runstate.ProfileLock()
    if not lock.acquire():
        sys.exit("Das Browserprofil ist belegt (Lauf oder Microsoft-Anmeldung aktiv?) – später erneut versuchen.")
    try:
        with sync_playwright() as pw:
            ctx = open_context(pw, cfg, BASE, headless=False)

            def find():
                state = dashboard.load_state(ctx)
                return next((a for a in (state.open_activities() if state else []) if title in a.title.lower()), None)

            act = find()
            if not act:
                log.error("Keine offene Aufgabe mit '%s' im Titel gefunden.", sys.argv[1])
                return
            log.info("Aufgabe: %s | offerId: %s", act.title, act.offer_id)
            for query in queries:
                rewards_page = dashboard.open_rewards_page(ctx, act.page_url)
                if not rewards_page:
                    return
                tab = activities._open_card(ctx, rewards_page, act)
                if tab:
                    reject_consent(tab)
                    activities._open_rewards_flyout(tab, act)
                    log.info("Teste: %s", query)
                    bing_search(tab, query)
                    tab.wait_for_timeout(20000)
                    tab.close()
                rewards_page.close()
                act = find()
                if not act:
                    log.info("TREFFER – dieser Begriff hat gezählt: %s", query)
                    return
                log.info("  zählt nicht: %s", query)
            log.warning("Keiner der Begriffe hat gezählt.")
            ctx.close()
    finally:
        lock.release()


if __name__ == "__main__":
    main()
