"""Einstellungen aus dem Webinterface (data/settings.json)."""
from rewards import runstate

SETTINGS_FILE = runstate.DATA / "settings.json"
DEFAULTS = {
    "theme": "auto",          # auto | light | dark
    "auto_update": False,     # Updates automatisch installieren
}
THEMES = ("auto", "light", "dark")


def load() -> dict:
    data = runstate.read_json(SETTINGS_FILE, {}) or {}
    return {**DEFAULTS, **{k: v for k, v in data.items() if k in DEFAULTS}}


def save(**changes) -> dict:
    data = {**load(), **changes}
    runstate._write_json(SETTINGS_FILE, data)
    return data
