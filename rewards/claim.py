"""Beansprucht die Punkte der Kachel 'Bereit zum Anfordern' auf dem Dashboard.

Bonuspunkte (z. B. Suchbonus, Monatsbonus, Anmelde-Serie) landen dort und werden erst nach einem Klick auf
'Punkte beanspruchen' im Seitenfenster gutgeschrieben. Nicht beanspruchte Punkte verfallen nach einem Monat.
"""
import re
from datetime import datetime
from pathlib import Path

from . import dashboard
from .util import LOGIN_HINT, log

DEBUG_DIR = Path(__file__).resolve().parent.parent / "debug" / "claim"
# Falls die Beschriftungen nicht aus den Seitendaten zu lesen sind
TILE_FALLBACK = "Bereit zum Anfordern|Ready to claim"
BUTTON_FALLBACK = "Punkte beanspruchen|Claim points"


def _shot(page, step: str) -> None:
    try:
        DEBUG_DIR.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(DEBUG_DIR / f"{datetime.now():%Y%m%d-%H%M%S}_{step}.png"))
    except Exception:
        pass


def _pattern(text: str | None, fallback: str) -> re.Pattern:
    return re.compile("|".join(filter(None, [re.escape(text) if text else None, fallback])), re.I)


def _claim(page, tile_text: str | None, button_text: str | None) -> bool:
    """Öffnet das Seitenfenster über die Kachel und klickt 'Punkte beanspruchen'."""
    tile = page.locator("button", has_text=_pattern(tile_text, TILE_FALLBACK)).first
    try:
        tile.scroll_into_view_if_needed(timeout=5000)
        tile.click(timeout=10000)
    except Exception as e:
        log.warning("  Kachel 'Bereit zum Anfordern' nicht anklickbar: %s", e)
        _shot(page, "1_kachel")
        return False
    button = page.get_by_role("button", name=_pattern(button_text, BUTTON_FALLBACK)).first
    try:
        button.wait_for(state="visible", timeout=15000)
        page.wait_for_timeout(1000)
        _shot(page, "1_seitenfenster")
        button.click(timeout=15000)  # wartet auch, bis der Knopf aktiv ist
    except Exception as e:
        log.warning("  Knopf 'Punkte beanspruchen' nicht gefunden: %s", e)
        _shot(page, "1_seitenfenster")
        return False
    page.wait_for_timeout(5000)  # "Wird beansprucht" -> "Erfolgreich beansprucht!"
    _shot(page, "2_beansprucht")
    return True


def run(ctx, cfg: dict) -> None:
    page = dashboard.open_rewards_page(ctx, dashboard.DASHBOARD_URL)
    if not page:
        log.error("Dashboard nicht lesbar – eingeloggt? %s", LOGIN_HINT)
        return
    try:
        payload = dashboard.decode_payload(page.content())
        points = dashboard.claimable_points(payload)
        if points is None:
            log.warning("Kachel 'Bereit zum Anfordern' nicht gefunden – hat Microsoft die Seite geändert?")
            return
        if points <= 0:
            log.info("Keine Punkte zum Beanspruchen")
            return
        log.info("Bereit zum Anfordern: %s Punkte", points)
        if not _claim(page, *dashboard.claim_texts(payload)):
            log.warning("Screenshots: %s", DEBUG_DIR)
            return
    finally:
        page.close()

    state = dashboard.load_state(ctx, earn=False)
    if not state or state.claimable is None:
        log.warning("Ergebnis nicht prüfbar")
    elif state.claimable < points:
        log.info("Beansprucht: %s Punkte, Punktestand jetzt %s", points - state.claimable, state.points)
    else:
        log.warning("Punkte wurden nicht beansprucht (noch %s bereit). Screenshots: %s", state.claimable, DEBUG_DIR)
