"""Einstellungen aus dem Webinterface (data/settings.json)."""
from rewards import runstate

SETTINGS_FILE = runstate.DATA / "settings.json"
DEFAULTS = {
    "theme": "auto",          # auto | light | dark
    "auto_update": False,     # Updates automatisch installieren
    "daily_run": True,        # täglicher Lauf (Timer) an/aus
    "allowed_hosts": [],      # zusätzliche Namen, unter denen das Webinterface aufgerufen werden darf
    "search_delay_max": None,  # Obergrenze der zufälligen Pause zwischen Suchen (s); None = search.delay aus config.json
}
THEMES = ("auto", "light", "dark")
# Pause zwischen zwei Suchen: zufällig zwischen SEARCH_DELAY_MIN und der eingestellten Obergrenze
SEARCH_DELAY_MIN, SEARCH_DELAY_MAX, SEARCH_DELAY_DEFAULT = 5, 600, 10


def load() -> dict:
    data = runstate.read_json(SETTINGS_FILE, {}) or {}
    return {**DEFAULTS, **{k: v for k, v in data.items() if k in DEFAULTS}}


def save(**changes) -> dict:
    data = {**load(), **changes}
    runstate._write_json(SETTINGS_FILE, data)
    return data


def valid_search_delay(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and SEARCH_DELAY_MIN <= value <= SEARCH_DELAY_MAX


def search_delay_max(cfg: dict) -> int:
    """Obergrenze der Suchpause: Wert aus dem Webinterface, sonst search.delay aus config.json (in die Grenzen geholt)."""
    value = load()["search_delay_max"]
    if valid_search_delay(value):
        return value
    try:
        value = int(cfg.get("search", {}).get("delay", SEARCH_DELAY_DEFAULT))
    except (TypeError, ValueError):
        return SEARCH_DELAY_DEFAULT
    return min(max(value, SEARCH_DELAY_MIN), SEARCH_DELAY_MAX)
