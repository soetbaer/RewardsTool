"""Baut ein Weitergabe-Paket: dist/RewardsTool-<Version>.zip (als Asset ans GitHub-Release hängen – der Updater lädt es)

Aufnahme nur per Positivliste – persönliche Daten (Browserprofil, Login-Cookies, Verlauf, Logs,
Screenshots) können so nicht versehentlich in das Paket geraten. Zusätzlich wird am Ende geprüft.

Aufruf: python pack.py
"""
import sys
import zipfile
from datetime import date
from pathlib import Path

from rewards.version import VERSION

BASE = Path(__file__).resolve().parent
NAME = "RewardsTool"

# Nur diese Dateien kommen in das Paket
INCLUDE = [
    "main.py", "webui.py", "config.json", "requirements.txt", "README.md", "TECHNIK.md", ".gitignore", "LICENSE",
    "Setup.bat", "rewards/*.py", "web/index.html", "web/*.svg", "web/*.ico", "web/*.png", "deploy/install.sh", "windows/*.bat", "windows/*.ps1", "docs/screenshots/*.png",
]
# Diese Namen dürfen nirgends im Paket auftauchen
FORBIDDEN_PARTS = {"profile", "data", "logs", "debug", ".venv", ".claude", "__pycache__", "dist"}
FORBIDDEN_FILES = {"session.json", "webui_auth.json", "history.jsonl", "status.json"}


def collect() -> list[Path]:
    files = []
    for pattern in INCLUDE:
        matches = sorted(BASE.glob(pattern))
        if not matches:
            sys.exit(f"FEHLER: '{pattern}' nicht gefunden – Paket wird nicht gebaut.")
        files += [m for m in matches if m.is_file()]
    return files


def check(names: list[str]) -> None:
    for name in names:
        parts = set(Path(name).parts)
        if parts & FORBIDDEN_PARTS or Path(name).name in FORBIDDEN_FILES or name.endswith(".session.json"):
            sys.exit(f"FEHLER: private Datei im Paket entdeckt: {name} – Abbruch.")


def main():
    files = collect()
    rel = [f.relative_to(BASE).as_posix() for f in files]
    check(rel)

    out_dir = BASE / "dist"
    out_dir.mkdir(exist_ok=True)
    target = out_dir / f"{NAME}-{VERSION}.zip"
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zf:
        for path, name in zip(files, rel):
            data = path.read_bytes()
            info = zipfile.ZipInfo(f"{NAME}/{name}", date_time=(*date.today().timetuple()[:3], 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            if name.endswith(".sh"):
                data = data.replace(b"\r\n", b"\n")  # Linux-Skripte brauchen LF
                info.external_attr = 0o100755 << 16  # ausführbar
            elif name.endswith((".bat", ".ps1")):
                data = data.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")  # Windows braucht CRLF
                info.external_attr = 0o100644 << 16
            else:
                info.external_attr = 0o100644 << 16
            zf.writestr(info, data)

    with zipfile.ZipFile(target) as zf:
        check(zf.namelist())
        print(f"Paket gebaut: {target}  ({target.stat().st_size // 1024} KB, {len(zf.namelist())} Dateien)")
        for n in zf.namelist():
            print("  ", n)


if __name__ == "__main__":
    main()
