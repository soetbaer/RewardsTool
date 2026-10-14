import functools
import json
import logging
import random
import ssl
import urllib.request
from pathlib import Path

from . import runstate

log = logging.getLogger("rewards")

BING = "https://www.bing.com/"
LOGIN_HINT = "Bitte neu anmelden: im Webinterface auf 'Microsoft-Anmeldung' klicken (oder python main.py login)."


@functools.cache
def ssl_context() -> ssl.SSLContext:
    """HTTPS-Zertifikate über den Zertifikatspeicher des Betriebssystems prüfen (truststore).

    Pythons eigene Prüfung scheitert unter Windows, wenn eine Stammzertifizierungsstelle noch nicht lokal
    vorliegt (Windows lädt sie erst bei Bedarf nach) oder Virenscanner/Firmen-Proxy HTTPS mit eigenem
    Zertifikat prüfen – Fehler „CERTIFICATE_VERIFY_FAILED: unable to get local issuer certificate“.
    Ohne truststore (noch nicht installiert) gilt die normale Prüfung von Python.
    """
    try:
        import truststore
        return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    except Exception as e:
        log.warning("truststore nicht verfügbar (%s) – nutze Pythons eigene Zertifikatsprüfung", e)
        return ssl.create_default_context()


def urlopen(url, timeout: float):
    return urllib.request.urlopen(url, timeout=timeout, context=ssl_context())


def setup_logging(log_dir: Path) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", "%H:%M:%S")
    console = logging.StreamHandler()
    console.setFormatter(fmt)
    file = logging.FileHandler(log_dir / "rewards.log", encoding="utf-8")
    file.setFormatter(fmt)
    log.setLevel(logging.INFO)
    log.addHandler(console)
    log.addHandler(file)


# Frühere Vorgaben in config.json, die ein Update nicht ersetzen konnte (der Updater bis 1.1.1 behielt immer den
# vorhandenen Wert). Steht noch genau die alte Vorgabe drin, wird sie beim Start durch die neue ersetzt.
_LYRICS_1_1_1 = ["Lyrics Bohemian Rhapsody", "Lyrics Atemlos durch die Nacht", "Lyrics Imagine John Lennon", "Lyrics Major Tom", "Lyrics Hotel California", "Lyrics Ein Hoch auf uns", "Lyrics Wonderwall", "Lyrics 99 Luftballons"]
_LYRICS = ["lyrics let it be", "lyrics hey jude", "lyrics yesterday", "lyrics imagine", "lyrics bohemian rhapsody", "lyrics hotel california", "lyrics yellow submarine", "lyrics stairway to heaven", "lyrics perfect ed sheeran", "lyrics shallow"]
_MIGRATIONS = [(("activities", "topic_queries", key), _LYRICS_1_1_1, _LYRICS)
               for key in ("lyrics", "songlyrics", "liedtext")]


def _migrate(cfg: dict) -> bool:
    changed = False
    for keys, old, new in _MIGRATIONS:
        parent = cfg
        for k in keys[:-1]:
            parent = parent.get(k) if isinstance(parent, dict) else None
        if isinstance(parent, dict) and parent.get(keys[-1]) == old:
            parent[keys[-1]] = new
            changed = True
    return changed


def load_config(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        cfg = json.load(f)
    if _migrate(cfg):
        try:
            tmp = path.with_name(path.name + ".tmp")
            tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            tmp.replace(path)
        except OSError:
            pass  # gilt trotzdem für diesen Lauf
    return cfg


def pause(bounds) -> None:
    """Wartet eine zufällige Zeit zwischen bounds[0] und bounds[1] Sekunden (abbrechbar)."""
    runstate.sleep(random.uniform(*bounds))


def reject_consent(page) -> None:
    """Klickt im Bing-Cookie-Banner auf 'Ablehnen', falls vorhanden."""
    for sel in ("#bnp_btn_reject", "button:has-text('Ablehnen')", "button:has-text('Reject')"):
        try:
            btn = page.locator(sel).first
            if btn.is_visible(timeout=1000):
                btn.click()
                return
        except Exception:
            pass
