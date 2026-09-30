"""Updates über GitHub-Releases: neue Version erkennen, Paket laden, prüfen und installieren.

Installiert wird das ZIP aus den Release-Assets (gebaut mit pack.py). Persönliche Ordner (profile, data, logs …)
werden nie angefasst, eigene Werte in config.json bleiben erhalten. Vor dem Überschreiben landen die bisherigen
Programmdateien in data/update/backup/.
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

from rewards import runstate
from rewards.util import log
from rewards.version import REPO, VERSION

BASE = runstate.BASE
API_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
UPDATE_DIR = runstate.DATA / "update"
STATE_FILE = runstate.DATA / "update.json"
# Unveränderte Kopie der mit dem Paket ausgelieferten config.json (legt pack.py an) – für den Abgleich beim Update
CONFIG_DEFAULTS = "config.default.json"
ASSET_RE = re.compile(r"RewardsTool-.+\.zip")
ZIP_ROOT = "RewardsTool/"
MAX_DOWNLOAD = 100 * 1024 * 1024
# Diese Ordner gehören dem Nutzer und werden von einem Update nie geschrieben
PROTECTED = {"profile", "data", "logs", "debug", ".venv", "dist", ".git", "__pycache__"}
REQUIRED = ("main.py", "webui.py", "rewards/version.py")


def parse_version(text: str) -> tuple[int, ...]:
    nums = re.findall(r"\d+", (text or "").split("-")[0])
    return tuple(int(n) for n in nums) or (0,)


def is_newer(candidate: str, current: str = VERSION) -> bool:
    return parse_version(candidate) > parse_version(current)


def _get(url: str, accept: str = "application/vnd.github+json", timeout: int = 30):
    req = urllib.request.Request(url, headers={"Accept": accept, "User-Agent": f"RewardsTool/{VERSION}"})
    return urllib.request.urlopen(req, timeout=timeout)


# ---------- Prüfen ----------

def check() -> dict:
    """Fragt das neueste Release ab und merkt sich das Ergebnis in data/update.json."""
    old = runstate.read_json(STATE_FILE, {}) or {}
    try:
        with _get(API_URL) as resp:
            rel = json.load(resp)
        asset = next((a for a in rel.get("assets", []) if ASSET_RE.fullmatch(a.get("name", ""))), None)
        info = {
            "checked": runstate.now_iso(),
            "latest": rel["tag_name"].lstrip("vV"),
            "name": rel.get("name") or rel["tag_name"],
            "notes": (rel.get("body") or "")[:5000],
            "url": rel.get("html_url"),
            "published": rel.get("published_at"),
            "asset_url": asset and asset["browser_download_url"],
            "asset_size": asset and asset.get("size"),
            "digest": asset and asset.get("digest"),
            "error": None,
        }
    except Exception as e:
        log.warning("Update-Prüfung fehlgeschlagen: %s", e)
        info = {**old, "checked": runstate.now_iso(), "error": f"Prüfung fehlgeschlagen: {e}"}
    runstate._write_json(STATE_FILE, info)
    return status()


def status() -> dict:
    st = runstate.read_json(STATE_FILE, {}) or {}
    latest = st.get("latest")
    return {
        "current": VERSION,
        "latest": latest,
        "available": bool(latest and st.get("asset_url") and is_newer(latest)),
        "name": st.get("name"),
        "notes": st.get("notes"),
        "url": st.get("url"),
        "published": st.get("published"),
        "checked": st.get("checked"),
        "error": st.get("error"),
    }


# ---------- Installieren ----------

def _download(url: str, target: Path, digest: str | None) -> None:
    sha = hashlib.sha256()
    size = 0
    with _get(url, accept="application/octet-stream", timeout=60) as resp, open(target, "wb") as f:
        while chunk := resp.read(1 << 16):
            size += len(chunk)
            if size > MAX_DOWNLOAD:
                raise RuntimeError("Update-Paket ist unerwartet groß – abgebrochen")
            sha.update(chunk)
            f.write(chunk)
    if digest and digest.startswith("sha256:") and sha.hexdigest() != digest.split(":", 1)[1]:
        raise RuntimeError("Prüfsumme des Update-Pakets stimmt nicht – abgebrochen")


def _safe_rel(name: str) -> str | None:
    """Pfad im ZIP -> Pfad relativ zum Programmordner; None für Ordner. Wirft bei verdächtigen Einträgen."""
    if not name.startswith(ZIP_ROOT):
        raise RuntimeError(f"Unerwarteter Eintrag im Update-Paket: {name}")
    rel = name[len(ZIP_ROOT):]
    if not rel or rel.endswith("/"):
        return None
    parts = Path(rel).parts
    if rel.startswith(("/", "\\")) or ".." in parts or ":" in rel or set(parts) & PROTECTED \
            or "\\" in rel or Path(rel).name in {"session.json", "webui_auth.json"}:
        raise RuntimeError(f"Unzulässiger Eintrag im Update-Paket: {name}")
    return rel


def _extract(zip_path: Path, target: Path) -> list[str]:
    files = []
    with zipfile.ZipFile(zip_path) as zf:
        for info in zf.infolist():
            rel = _safe_rel(info.filename)
            if rel is None:
                continue
            dest = target / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(zf.read(info))
            files.append(rel)
    for req in REQUIRED:
        if req not in files:
            raise RuntimeError(f"Update-Paket unvollständig ({req} fehlt)")
    return files


def _merge_config(new_defaults: dict, current: dict, old_defaults: dict | None = None) -> dict:
    """Neue Einträge aus dem Update übernehmen, eigene Werte des Nutzers behalten.

    old_defaults: die mit der bisherigen Version ausgelieferte config.json. Steht beim Nutzer noch genau diese alte
    Vorgabe, hat er den Wert nicht geändert – dann gilt die neue Vorgabe.
    """
    out = dict(new_defaults)
    old_defaults = old_defaults or {}
    for key, value in current.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            old = old_defaults.get(key)
            out[key] = _merge_config(out[key], value, old if isinstance(old, dict) else None)
        elif key in out and key in old_defaults and value == old_defaults[key]:
            pass  # unveränderte alte Vorgabe -> neue Vorgabe
        else:
            out[key] = value
    return out


def _python() -> str:
    exe = Path(sys.executable)
    if exe.name.lower() == "pythonw.exe" and exe.with_name("python.exe").exists():
        exe = exe.with_name("python.exe")
    return str(exe)


def _needs_chromium() -> bool:
    if os.name != "nt":
        return True
    edge = [Path(os.environ.get(v, "")) / "Microsoft/Edge/Application/msedge.exe"
            for v in ("ProgramFiles(x86)", "ProgramFiles")]
    return not any(p.exists() for p in edge)


def _sh(cmd: list[str], what: str) -> None:
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    res = subprocess.run(cmd, cwd=BASE, capture_output=True, text=True, timeout=1800, creationflags=flags)
    if res.returncode != 0:
        raise RuntimeError(f"{what} fehlgeschlagen: {(res.stderr or res.stdout).strip()[-300:]}")


def install(progress=lambda msg: None) -> str:
    """Lädt und installiert das neueste Release. Gibt die neue Version zurück; bei Fehlern bleibt die alte."""
    st = runstate.read_json(STATE_FILE, {}) or {}
    latest = st.get("latest")
    if not (latest and st.get("asset_url") and is_newer(latest)):
        raise RuntimeError("Kein Update verfügbar")

    shutil.rmtree(UPDATE_DIR, ignore_errors=True)
    new_dir, backup_dir = UPDATE_DIR / "new", UPDATE_DIR / "backup"
    new_dir.mkdir(parents=True)
    zip_path = UPDATE_DIR / "download.zip"

    progress(f"Lade Version {latest} herunter …")
    _download(st["asset_url"], zip_path, st.get("digest"))
    progress("Prüfe Update-Paket …")
    files = _extract(zip_path, new_dir)
    m = re.search(r'VERSION\s*=\s*"([^"]+)"', (new_dir / "rewards/version.py").read_text(encoding="utf-8"))
    if not m or parse_version(m.group(1)) != parse_version(latest):
        raise RuntimeError("Versionsnummer im Update-Paket passt nicht zum Release – abgebrochen")

    progress("Sichere bisherige Version …")
    for rel in files:
        if (BASE / rel).is_file():
            (backup_dir / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(BASE / rel, backup_dir / rel)
    old_defaults = runstate.read_json(BASE / CONFIG_DEFAULTS)  # Vorgaben der bisherigen Version (vor dem Überschreiben)
    old_requirements = (BASE / "requirements.txt").read_text(encoding="utf-8") if (BASE / "requirements.txt").exists() else ""

    progress("Installiere neue Dateien …")
    try:
        for rel in files:
            dest = BASE / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            if rel == "config.json" and dest.exists():
                merged = _merge_config(json.loads((new_dir / rel).read_text(encoding="utf-8")),
                                       json.loads(dest.read_text(encoding="utf-8")),
                                       old_defaults)
                data = (json.dumps(merged, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
            else:
                data = (new_dir / rel).read_bytes()
            tmp = dest.with_name(dest.name + ".update-tmp")
            tmp.write_bytes(data)
            if dest.exists():
                shutil.copymode(dest, tmp)
            elif rel.endswith(".sh"):
                tmp.chmod(0o755)
            tmp.replace(dest)
        if (BASE / "requirements.txt").read_text(encoding="utf-8") != old_requirements:
            progress("Aktualisiere Programmpakete (kann einige Minuten dauern) …")
            _sh([_python(), "-m", "pip", "install", "--disable-pip-version-check", "-q", "-r", "requirements.txt"],
                "Paketinstallation")
            if _needs_chromium():
                _sh([_python(), "-m", "playwright", "install", "chromium"], "Browser-Installation")
    except Exception:
        log.exception("Update fehlgeschlagen – stelle bisherige Version wieder her")
        for rel in files:
            if (backup_dir / rel).is_file():
                shutil.copy2(backup_dir / rel, BASE / rel)
            elif (BASE / rel).exists():
                (BASE / rel).unlink()  # neue Datei, gab es vorher nicht
        raise

    zip_path.unlink(missing_ok=True)
    shutil.rmtree(new_dir, ignore_errors=True)
    log.info("Update auf Version %s installiert (vorher %s)", latest, VERSION)
    return latest
