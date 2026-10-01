# RewardsTool – Technik & Befehle

Für Fortgeschrittene: alle Kommandozeilen-Befehle und wie das Tool intern arbeitet.
Die Schritt-für-Schritt-Anleitung für Einsteiger steht in der [README.md](README.md).

Das Tool steuert per Playwright einen echten Browser (Edge, sonst Chromium) mit eigenem Profil.

> **Hinweis:** Automatisiertes Punktesammeln verstößt gegen die Microsoft-Rewards-Bedingungen.
> Microsoft kann Punkte streichen oder das Konto sperren. Nutzung auf eigenes Risiko.

## Installation von Hand

```
python -m venv .venv
.venv/bin/pip install -r requirements.txt        # Windows: .venv\Scripts\pip
.venv/bin/python -m playwright install chromium   # Ersatz, falls Edge nicht installiert ist
```

## Nutzung

| Befehl | Zweck |
|---|---|
| `python main.py login` | Öffnet den Browser. Du meldest dich dort selbst an (rewards.bing.com und bing.com). Das Passwort wird nicht gespeichert, nur die Sitzung im Ordner `profile/`. |
| `python main.py status` | Punktestand, Suchfortschritt und offene Aktivitäten |
| `python main.py run` | Alle Aufgaben: Aktivitäten und Suchen |
| `python main.py run --only searches` | Nur einzelne Aufgaben (`activities`, `searches`, `visualsearch`, `claim`) |
| `python main.py run --headless` | Ohne sichtbares Fenster – dann werden „Auf Bing erkunden“-Aufgaben nicht gutgeschrieben |
| `python main.py run /delay=20` | Pause zwischen zwei Suchen in Sekunden (1–120, Standard 10 aus `search.delay` in `config.json`) |
| `python main.py diagnose` | Prüft Login und macht eine Testsuche, ob sie gezählt wird |
| `python main.py export-session` / `import-session` | Login-Sitzung auf einen anderen Rechner übertragen |
| `python main.py dump` | Speichert HTML, Screenshot und dekodierte Seitendaten von `/dashboard` und `/earn` in `debug/`, falls Microsoft die Seite ändert |

## Was passiert

- **Tägliche Aktionen, Weiter verdienen, Auf Bing erkunden:** Das Dashboard (Next.js) liefert seine Daten
  eingebettet im HTML (`self.__next_f`). Das Tool liest daraus die offenen Kacheln von `/dashboard` und `/earn`
  und **klickt sie auf der Rewards-Seite an**. Das ist nötig, weil die Ziel-Links `rnoreward=1` tragen und die
  Punkte erst durch den Klick gutgeschrieben werden. Im geöffneten Tab beantwortet es Quizze (best effort).
  Für „Auf Bing erkunden“ wird passend zum Thema gesucht (`activities.topic_queries` in `config.json`,
  Schlüssel = Thema aus der offerId, z. B. `cars`, `jobs`, oder ein Wort aus dem Kacheltext, z. B. `liedtext`).
  Der Wert kann auch eine Liste sein: Der erste Eintrag wird zuerst versucht, danach die übrigen in zufälliger
  Reihenfolge. Ist die Aufgabe nach dem Lauf noch offen, versucht das Tool bis zu zwei weitere Begriffe aus der Liste
  (Liedtexte zählen z. B. nur bei Liedern, zu denen Bing eine Liedtext-Box zeigt). Fehlt ein Thema, steht im Log
  „Kein Suchbegriff für Thema …“ – dann dort ergänzen.
- **Tägliche Suche:** Suchbegriffe aus Google Trends (DE) mit Fallback-Liste. Der Fortschritt
  (`pointsCounters.pc` auf `/earn`) wird alle 10 Suchen geprüft, bis das Limit erreicht ist.
- **Visuelle Suche (Streak):** Lädt das Bing-Bild des Tages über das Kamera-Symbol im Suchfeld hoch.
  Erledigt-Status aus dem Streak `partner: visualsearch` (`isCurrentDayCompleted`).
- **Bereit zum Anfordern:** Bonuspunkte (Suchbonus, Monatsbonus, Streaks) müssen auf dem Dashboard beansprucht
  werden, sonst verfallen sie nach einem Monat. Das Tool klickt die Kachel und im Seitenfenster „Punkte
  beanspruchen“ – als letzter Schritt eines Laufs. Screenshots dazu in `debug/claim/`.
- **Ziel:** Ein in Rewards gesetztes Einlöse-Ziel zeigen `status` und das Webinterface mit Fortschritt an.

News-Punkte gibt es nur für das Lesen in der Bing-App auf dem Handy; das Tool deckt sie nicht ab.

Logs: `logs/rewards.log`

## Webinterface

`deploy/install.sh` richtet den Dienst `rewardstool-web` ein. Aufruf: `http://<server-ip>:3333`
(Port in `config.json` unter `webui.port`).

- **Erster Aufruf:** Passwort für das Webinterface festlegen.
- **Microsoft-Anmeldung:** zeigt den Browser des Servers als Live-Bild. Hineinklicken und tippen wie gewohnt,
  zuerst auf der Rewards-Seite, dann auf bing.com oben rechts; danach „Fertig – Anmeldung prüfen“.
  Das Microsoft-Passwort wird nicht gespeichert.
- **Status:** Punkte, Suchfortschritt, offene Aufgaben, nächster Timer-Lauf.
- **Lauf starten**, laufender Lauf mit Live-Log, Verlauf aller Läufe (Timer, Webinterface, von Hand) mit Log.

Timer, Webinterface und Aufrufe von Hand sperren sich gegenseitig, damit nie zwei Prozesse gleichzeitig das
Browserprofil benutzen. Ein Timer-Lauf während einer Anmeldung wird im Verlauf als „übersprungen“ vermerkt.

- **Einstellungen** (`data/settings.json`): Design, Update-Modus. Die Uhrzeit des täglichen Laufs wird direkt in der
  Aufgabenplanung (Windows, `Set-ScheduledTask`) bzw. im systemd-Timer geändert. Auf Linux darf der Dienst-Benutzer
  dafür per `/etc/sudoers.d/rewardstool` genau ein Skript als root ausführen: `/usr/local/sbin/rewardstool-set-time HH:MM`
  (root-eigen, außerhalb des Programmordners, prüft das Format). Beides legt `deploy/install.sh` an.

### Updates

Das Webinterface fragt alle 6 Stunden `api.github.com/repos/soetbaer/RewardsTool/releases/latest` ab und vergleicht
das Tag mit `VERSION` in `rewards/version.py`. Installiert wird das Release-Asset `RewardsTool-<Version>.zip`:

1. Download (max. 100 MB), SHA-256 gegen die Prüfsumme aus der GitHub-API prüfen.
2. Entpacken nach `data/update/new/`. Abbruch bei Pfaden außerhalb des Programmordners oder in `profile/`, `data/`,
   `logs/`, `debug/`, `.venv/`; `rewards/version.py` im Paket muss zur Release-Version passen.
3. Bisherige Dateien nach `data/update/backup/` sichern, dann überschreiben. `config.json` wird zusammengeführt:
   neue Einträge kommen dazu, selbst geänderte Werte bleiben. Werte, die noch der alten Vorgabe entsprechen
   (Vergleich mit `config.default.json`, die `pack.py` mit ins Paket legt), bekommen die neue Vorgabe.
4. Hat sich `requirements.txt` geändert: `pip install` (und Chromium). Schlägt das fehl, wird die Sicherung
   zurückgespielt.
5. Webinterface neu starten (Linux: `exec` im selben Prozess, damit systemd/xvfb-run passen; Windows: neuer Prozess).

Während der Installation hält das Webinterface die Profilsperre, ein Timer-Lauf wird dann übersprungen.
Änderungen an `deploy/install.sh` bzw. `windows/setup.ps1` (Dienste, Aufgaben) wirken erst nach erneutem Ausführen.

**Release erstellen:** `VERSION` in `rewards/version.py` erhöhen, Release-Notes nach `docs/releases/v<Version>.md`
schreiben, committen und das Tag `v<Version>` pushen. Der Workflow `.github/workflows/release.yml` baut dann mit
`pack.py` das Paket `RewardsTool-<Version>.zip` und legt das Release mit diesen Notes an.

**Sicherheit:** Das Webinterface spricht unverschlüsseltes HTTP. Nur im eigenen Heimnetz verwenden und
**keine Portfreigabe im Router** einrichten – bei der Microsoft-Anmeldung laufen die Eingaben über diese Verbindung.
Passwort vergessen: auf dem Server `data/webui_auth.json` löschen, dann beim nächsten Aufruf neu festlegen.

## Auf einem Linux-Server betreiben

Voraussetzungen: Debian/Ubuntu (oder kompatibel), x86-64 oder ARM64, Python 3.10+.
Edge wird nicht benötigt; ohne Edge nutzt das Tool automatisch das Playwright-Chromium.

1. **Projekt auf den Server kopieren**, ohne die Ordner `profile/`, `logs/` und `debug/`. Das Windows-Profil ist
   auf Linux nicht verwendbar, weil die Cookies an dein Windows-Konto gebunden verschlüsselt sind.
2. **Installieren** (auf dem Server, als normaler Benutzer):
   ```
   bash deploy/install.sh 08:00
   ```
   Das legt eine venv an, installiert Chromium und Xvfb (virtueller Bildschirm) und richtet einen systemd-Timer ein.
   Der Browser läuft auf dem Server mit Fenster auf dem virtuellen Bildschirm, weil Bing die Aufgaben unter
   „Auf Bing erkunden“ nur dann gutschreibt – im Headless-Modus zählen sie nicht. Lief der Server zur
   geplanten Zeit nicht, holt der Timer den Lauf nach dem Hochfahren nach.
3. **Sitzung übertragen**: Der Server hat keinen Bildschirm für `login`, darum:
   ```
   # auf dem PC (nach python main.py login)
   python main.py export-session session.json
   scp session.json user@server:~/RewardsTool/
   del session.json

   # auf dem Server
   .venv/bin/python main.py import-session session.json && rm session.json
   ```
   `session.json` enthält deine Login-Cookies – nicht weitergeben, nach dem Import löschen.

   **Alternative: direkt auf dem Server anmelden** (ohne PC-Export):
   ```
   # auf dem Server
   xvfb-run -a .venv/bin/python main.py login --remote

   # auf dem PC, in einem zweiten Terminal (offen lassen)
   ssh -N -L 9222:127.0.0.1:9222 user@server
   ```
   Dann in Edge `edge://inspect` öffnen, unter „Configure…“ `localhost:9222` eintragen und beim Tab
   rewards.bing.com auf „inspect“ klicken. Im Vorschaubild meldest du dich wie gewohnt an, danach
   genauso im bing.com-Tab. Zum Schluss auf dem Server Enter drücken. Die Schnittstelle lauscht nur auf
   127.0.0.1 des Servers und ist nur über den SSH-Tunnel erreichbar.
4. **Prüfen**: `.venv/bin/python main.py status`

Nützlich auf dem Server:

| Befehl | Zweck |
|---|---|
| `sudo systemctl start rewardstool` | Sofort einen Lauf starten |
| `xvfb-run -a .venv/bin/python main.py run` | Lauf von Hand im Terminal starten (nicht `--headless` verwenden) |
| `systemctl list-timers rewardstool.timer` | Nächsten geplanten Lauf anzeigen |
| `journalctl -u rewardstool -n 100` | Ausgabe des letzten Laufs (zusätzlich `logs/rewards.log`) |

Läuft die Sitzung irgendwann ab (Log: „Nicht eingeloggt“), auf dem PC neu anmelden und Schritt 3 wiederholen.
Die Zeitzone des Servers sollte stimmen (`timedatectl set-timezone Europe/Berlin`), damit der Lauf nach dem
täglichen Reset startet.

## Windows-Einrichtung (`Setup.bat` → `windows\setup.ps1`)

1. Python ≥ 3.10 suchen (`py -3`, `python`, `%LOCALAPPDATA%\Programs\Python\Python3*`). Fehlt es:
   `winget install Python.Python.3.12 --scope user --silent`, sonst Download von python.org. Der Installer
   wird nur ausgeführt, wenn seine Authenticode-Signatur gültig ist und von der Python Software Foundation stammt;
   Installation still, nur für den Benutzer, ohne Admin-Rechte.
2. venv `.venv\`, `pip install -r requirements.txt`; Chromium nur, wenn Edge fehlt.
3. `main.py status` – ist man nicht angemeldet, folgt `main.py login`.
4. Aufgabe `RewardsTool` in der Aufgabenplanung: täglich `pythonw.exe main.py run`, verpasste Läufe werden
   nachgeholt. Läuft nur bei angemeldetem Benutzer, weil der Browser ein sichtbares Fenster braucht.
5. Autostart-Verknüpfung für `pythonw.exe webui.py`, Desktop-Verknüpfung `RewardsTool.url`.

Erneut ausführen = Update (Anmeldung bleibt erhalten). Testmodus ohne Systemänderungen:
`powershell -ExecutionPolicy Bypass -File windows\setup.ps1 -SkipSystem`.
Entfernen: `windows\Deinstallieren.bat`.

## Weitergabe-Paket

`python pack.py` baut `dist/RewardsTool-<Version>.zip` nur aus freigegebenen Dateien (Positivliste) und bricht ab,
falls doch etwas Privates (`profile/`, `data/`, `session.json` …) hineingeraten würde.
