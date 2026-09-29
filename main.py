import argparse
import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright

from rewards import activities, dashboard, runstate, searches
from rewards.browser import open_context
from rewards.util import BING, LOGIN_HINT, load_config, log, setup_logging

BASE = Path(__file__).parent
DELAY_MIN, DELAY_MAX = 1, 120
TASKS = {"activities": activities.run, "searches": searches.run}


REMOTE_PORT = 9222


def cmd_login(pw, cfg, args):
    if args.remote:
        # Anmeldung auf einem Server ohne Bildschirm: Browser läuft unter xvfb-run, die Seite wird über
        # die DevTools-Schnittstelle (nur auf 127.0.0.1) per SSH-Tunnel in Edge/Chrome am PC bedient.
        ctx = open_context(pw, cfg, BASE, headless=False,
                           args=[f"--remote-debugging-port={REMOTE_PORT}", "--remote-debugging-address=127.0.0.1"])
    else:
        ctx = open_context(pw, cfg, BASE, headless=False)
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    page.goto(dashboard.DASHBOARD_URL)
    ctx.new_page().goto(BING)
    if args.remote:
        print(
            "\nFernanmeldung vorbereitet. Auf dem PC:\n"
            f"  1. Neues Terminal: ssh -N -L {REMOTE_PORT}:127.0.0.1:{REMOTE_PORT} <user>@<server>   (offen lassen)\n"
            "  2. In Edge edge://inspect öffnen (Chrome: chrome://inspect)\n"
            f"     'Configure...' -> localhost:{REMOTE_PORT} eintragen -> Done\n"
            "  3. Unter 'Remote Target' bei rewards.bing.com auf 'inspect' klicken und im Vorschaubild anmelden\n"
            "  4. Danach beim bing.com-Tab auf 'inspect' klicken und oben rechts ebenfalls anmelden\n"
        )
    else:
        print(
            "\nBitte im Browserfenster anmelden:\n"
            "  1. Tab 1: Microsoft-Konto auf rewards.bing.com anmelden\n"
            "  2. Tab 2: auf bing.com oben rechts ebenfalls anmelden\n"
        )
    input("Danach hier Enter drücken (das Browserfenster darfst du vorher schließen) ...")
    print("Prüfe Anmeldung ...")
    state = check_login(pw, cfg, ctx)
    runstate.save_status(state)
    if state:
        print(f"Login erfolgreich. Punktestand: {state.points}")
    else:
        print("Login konnte nicht bestätigt werden – bitte erneut versuchen.")


def check_login(pw, cfg, ctx):
    """Prüft die Anmeldung im offenen Browser – oder, falls das Fenster schon geschlossen wurde,
    mit dem gespeicherten Profil im Hintergrund."""
    try:
        state = dashboard.load_state(ctx, earn=False)
    except Exception:
        state = None
        closed = True
    else:
        closed = False
    try:
        ctx.close()
    except Exception:
        pass
    if closed:
        ctx = open_context(pw, cfg, BASE, headless=True)
        try:
            state = dashboard.load_state(ctx, earn=False)
        finally:
            ctx.close()
    return state


def cmd_status(pw, cfg, args):
    ctx = open_context(pw, cfg, BASE, headless=True)
    state = dashboard.load_state(ctx)
    ctx.close()
    runstate.save_status(state)
    if not state:
        print(f"Nicht eingeloggt. {LOGIN_HINT}")
        return
    print(f"Punktestand: {state.points}")
    if state.search:
        print(f"PC-Suche:    {state.search[0]}/{state.search[1]}")
    for name, acts in (("Tägliche Aktionen", state.daily_set), ("Auf Bing erkunden", state.explore),
                       ("Weiter verdienen", state.more)):
        done = sum(a.completed for a in acts)
        print(f"{name}: {done}/{len(acts)} erledigt")
        for a in acts:
            if not a.completed:
                print(f"  offen: {a.title} ({a.points} Pkt.)")


def delay_seconds(value: str) -> int:
    try:
        seconds = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"'{value}' ist keine ganze Zahl")
    if not DELAY_MIN <= seconds <= DELAY_MAX:
        raise argparse.ArgumentTypeError(f"muss zwischen {DELAY_MIN} und {DELAY_MAX} Sekunden liegen")
    return seconds


def cmd_run(pw, cfg, args):
    if args.delay is not None:
        cfg["search"]["delay"] = args.delay
    selected = args.only.split(",") if args.only else list(TASKS)
    cfg["_headless"] = args.headless or cfg["headless"]

    # Eigenes Log pro Lauf (für Verlauf und Live-Anzeige im Webinterface)
    runstate.RUN_LOGS.mkdir(parents=True, exist_ok=True)
    run_log = runstate.RUN_LOGS / f"{datetime.now():%Y%m%d-%H%M%S}.log"
    handler = logging.FileHandler(run_log, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", "%H:%M:%S"))
    log.addHandler(handler)
    run = runstate.start_run(run_log, os.environ.get("REWARDS_TRIGGER", "manuell"))
    result = {"result": "fehler"}
    ctx = open_context(pw, cfg, BASE, headless=args.headless or None)
    try:
        before = dashboard.load_state(ctx, earn=False)
        if not before:
            log.error("Nicht eingeloggt. %s", LOGIN_HINT)
            runstate.save_status(None)
            result = {"result": "nicht eingeloggt"}
            return
        result["points_before"] = before.points
        for name in selected:
            log.info("=== %s ===", name)
            try:
                TASKS[name](ctx, cfg)
            except Exception:
                log.exception("Fehler in %s", name)
        state = dashboard.load_state(ctx)
        runstate.save_status(state)
        if state:
            log.info("Fertig. Punktestand: %s", state.points)
            result.update(
                result="ok",
                points_after=state.points,
                search=list(state.search) if state.search else None,
                open=len(state.open_activities()),
            )
    finally:
        ctx.close()
        runstate.finish_run(run, **result)
        log.removeHandler(handler)
        handler.close()


def cmd_export_session(pw, cfg, args):
    """Schreibt die Login-Cookies in eine Datei, um sie auf einem anderen Rechner zu importieren."""
    ctx = open_context(pw, cfg, BASE, headless=True)
    if not dashboard.load_state(ctx, earn=False):
        ctx.close()
        print(f"Nicht eingeloggt. {LOGIN_HINT}")
        return
    ctx.storage_state(path=args.file)
    ctx.close()
    print(f"Sitzung gespeichert in {args.file}")
    print("ACHTUNG: Die Datei enthält deine Login-Cookies. Nur auf den eigenen Server kopieren und danach löschen.")


def cmd_import_session(pw, cfg, args):
    """Übernimmt exportierte Login-Cookies in das Browserprofil."""
    with open(args.file, encoding="utf-8") as f:
        cookies = json.load(f)["cookies"]
    ctx = open_context(pw, cfg, BASE, headless=True)
    ctx.add_cookies(cookies)
    state = dashboard.load_state(ctx, earn=False)
    ctx.close()
    if state:
        print(f"Import erfolgreich ({len(cookies)} Cookies). Punktestand: {state.points}")
        print(f"Bitte {args.file} jetzt löschen.")
    else:
        print("Import fehlgeschlagen – Sitzung abgelaufen? Auf dem PC neu anmelden und erneut exportieren.")


def cmd_diagnose(pw, cfg, args):
    """Prüft Login und ob eine Bing-Suche tatsächlich gezählt wird."""
    import platform
    import time

    ctx = open_context(pw, cfg, BASE, headless=True)
    try:
        print(f"System:  {platform.system()} {platform.machine()}, Zeit: {datetime.now():%Y-%m-%d %H:%M %Z}")
        ua = ctx.new_page()
        print(f"Browser: {ua.evaluate('navigator.userAgent')}")
        ua.close()
        cookies = {c["name"] for c in ctx.cookies(["https://www.bing.com", "https://rewards.bing.com"])}
        print(f"Bing-Login-Cookie (_U): {'vorhanden' if '_U' in cookies else 'FEHLT'}")
        before = searches.search_progress(ctx)
        if not before:
            print("Rewards-Seite: NICHT eingeloggt -> Sitzung neu importieren")
            return
        print(f"Rewards-Seite: eingeloggt, Suche {before[0]}/{before[1]}")
        if before[0] >= before[1]:
            print("Suchlimit heute schon erreicht – Zähltest nicht möglich, morgen erneut prüfen.")
            return
        # Meldungen der Ergebnisseite an Rewards mitschneiden (reportActivity = Suche wird gutgeschrieben)
        reports = []
        page = ctx.new_page()
        page.on("requestfinished", lambda r: "/rewardsapp/" in r.url and "widgetassets" not in r.url and reports.append(r))
        # Verlauf der Hauptseite: HTTP-Antworten (inkl. Weiterleitungen) und jede Navigation
        trace = []
        page.on("response", lambda r: r.request.is_navigation_request() and r.request.frame == page.main_frame
                and trace.append(f"HTTP {r.status} {r.url[:110]}"
                                 + (f"  -> {r.headers.get('location', '')[:110]}" if 300 <= r.status < 400 else "")))
        page.on("framenavigated", lambda f: f == page.main_frame and trace.append(f"Navigiert zu {f.url[:110]}"))
        if not searches.bing_search(page, "Wetter morgen " + datetime.now().strftime("%H%M")):
            print("WARNUNG: Es wurde keine Ergebnisseite geöffnet – die Suche fand nicht statt.")
        time.sleep(8)
        out = BASE / "debug"
        out.mkdir(exist_ok=True)
        page.screenshot(path=str(out / "diagnose.png"))
        (out / "diagnose.html").write_text(page.content(), encoding="utf-8")
        print(f"Ergebnisseite: {page.url[:90]} | {page.title()}")
        print("Seitenverlauf:")
        for line in trace:
            print(f"  {line}")
        print(f"Rewards-Meldungen der Seite: {len(reports)}")
        for r in reports:
            resp = r.response()
            body = ""
            try:
                body = resp.text()[:150].replace("\n", " ") if resp else ""
            except Exception:
                pass
            print(f"  {r.method} {resp.status if resp else '-'} {r.url.split('?')[0]}  {body}")
        page.close()
        after = searches.search_progress(ctx)
        counted = after and after[0] > before[0]
        print(f"Testsuche: {before[0]} -> {after[0] if after else '?'} Punkte = {'GEZÄHLT' if counted else 'NICHT GEZÄHLT'}")
        print(f"Screenshot/HTML der Ergebnisseite: {out / 'diagnose.png'}")
    finally:
        ctx.close()


def cmd_dump(pw, cfg, args):
    """Speichert HTML, Screenshot und dekodierte Daten der Rewards-Seiten zur Fehlersuche."""
    out = BASE / "debug" / datetime.now().strftime("%Y%m%d-%H%M%S")
    out.mkdir(parents=True)
    ctx = open_context(pw, cfg, BASE, headless=True)
    for name, url in (("dashboard", dashboard.DASHBOARD_URL), ("earn", dashboard.EARN_URL)):
        page = dashboard.open_rewards_page(ctx, url)
        if not page:
            break
        html = page.content()
        (out / f"{name}.html").write_text(html, encoding="utf-8")
        (out / f"{name}_payload.txt").write_text(dashboard.decode_payload(html), encoding="utf-8")
        page.screenshot(path=str(out / f"{name}.png"), full_page=True)
        page.close()
    ctx.close()
    print(f"Gespeichert in {out}")


def main():
    parser = argparse.ArgumentParser(description="Microsoft Rewards Automatisierung")
    sub = parser.add_subparsers(dest="cmd", required=True)
    login = sub.add_parser("login", help="Browser öffnen und einmalig anmelden")
    login.add_argument("--remote", action="store_true",
                       help="Anmeldung auf einem Server ohne Bildschirm (mit xvfb-run starten, Bedienung per SSH-Tunnel)")
    sub.add_parser("status", help="Punktestand und offene Aufgaben anzeigen")
    run = sub.add_parser("run", help="Punkte sammeln", prefix_chars="-/")
    run.add_argument("--only", help=f"Kommagetrennt: {','.join(TASKS)}")
    run.add_argument("--headless", action="store_true", help="Ohne sichtbares Fenster")
    run.add_argument("/delay", "--delay", type=delay_seconds, metavar="SEK",
                     help=f"Pause zwischen Suchen in Sekunden ({DELAY_MIN}-{DELAY_MAX}, Standard aus config.json: 10)")
    sub.add_parser("dump", help="Dashboard zur Fehlersuche speichern")
    sub.add_parser("diagnose", help="Prüfen, ob Login gültig ist und Suchen gezählt werden")
    exp = sub.add_parser("export-session", help="Login-Sitzung in Datei exportieren (für den Server)")
    exp.add_argument("file", nargs="?", default="session.json")
    imp = sub.add_parser("import-session", help="Exportierte Login-Sitzung übernehmen")
    imp.add_argument("file", nargs="?", default="session.json")
    args = parser.parse_args()

    setup_logging(BASE / "logs")
    cfg = load_config(BASE / "config.json")
    try:
        cfg["search"]["delay"] = delay_seconds(str(cfg["search"].get("delay", 10)))
    except argparse.ArgumentTypeError as e:
        parser.error(f"search.delay in config.json {e}")
    handlers = {
        "login": cmd_login, "status": cmd_status, "run": cmd_run, "dump": cmd_dump, "diagnose": cmd_diagnose,
        "export-session": cmd_export_session, "import-session": cmd_import_session,
    }
    lock = runstate.ProfileLock()
    if not lock.acquire():
        msg = "Das Browserprofil wird gerade benutzt (anderer Lauf, Timer oder Anmeldung im Webinterface)."
        log.error(msg)
        if args.cmd == "run":
            runstate.finish_run({"start": runstate.now_iso(), "trigger": os.environ.get("REWARDS_TRIGGER", "manuell")},
                                result="übersprungen – Profil belegt")
        sys.exit(3)
    try:
        with sync_playwright() as pw:
            handlers[args.cmd](pw, cfg, args)
    finally:
        lock.release()


if __name__ == "__main__":
    main()
