import json
import logging
import random
import time
from pathlib import Path

log = logging.getLogger("rewards")

BING = "https://www.bing.com/"
LOGIN_HINT = "Bitte neu anmelden: im Webinterface auf 'Microsoft-Anmeldung' klicken (oder python main.py login)."


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


def load_config(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def pause(bounds) -> None:
    """Wartet eine zufällige Zeit zwischen bounds[0] und bounds[1] Sekunden."""
    time.sleep(random.uniform(*bounds))


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
