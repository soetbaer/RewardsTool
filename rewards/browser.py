from pathlib import Path

from .util import log


def open_context(pw, cfg: dict, base_dir: Path, headless: bool | None = None, args: list[str] | None = None):
    """Startet einen Browser mit dauerhaftem Profil, damit der Login erhalten bleibt."""
    profile = (base_dir / cfg["profile_dir"]).resolve()
    profile.mkdir(parents=True, exist_ok=True)
    kwargs = dict(
        user_data_dir=str(profile),
        headless=cfg["headless"] if headless is None else headless,
        locale=cfg["locale"],
        viewport={"width": 1366, "height": 900},
        args=args or [],
    )
    if cfg.get("browser_channel"):
        kwargs["channel"] = cfg["browser_channel"]
    try:
        return pw.chromium.launch_persistent_context(**kwargs)
    except Exception as e:
        # z. B. Linux-Server ohne Edge: auf das mitgelieferte Playwright-Chromium ausweichen
        if "channel" not in kwargs or "not found" not in str(e).lower():
            raise
        log.warning("Browser '%s' nicht installiert – nutze Playwright-Chromium", kwargs.pop("channel"))
        return pw.chromium.launch_persistent_context(**kwargs)
