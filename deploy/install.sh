#!/usr/bin/env bash
# Richtet RewardsTool auf einem Linux-Server ein: venv, Chromium, systemd-Timer.
# Aufruf als normaler Benutzer (nicht root):  bash deploy/install.sh [Uhrzeit, Standard 08:00]
set -euo pipefail

DIR="$(cd "$(dirname "$0")/.." && pwd)"
RUN_USER="$(id -un)"
TIME="${1:-08:00}"

if [ "$RUN_USER" = "root" ]; then
  echo "Bitte als normaler Benutzer ausführen (sudo wird bei Bedarf selbst aufgerufen)." >&2
  exit 1
fi

echo ">> Python-Umgebung in $DIR/.venv"
# Debian/Ubuntu liefern venv als eigenes Paket aus
python3 -m venv --help >/dev/null 2>&1 && python3 -c "import ensurepip" 2>/dev/null   || sudo apt-get install -y -q python3-venv
python3 -m venv "$DIR/.venv"
"$DIR/.venv/bin/pip" install -q --upgrade pip
"$DIR/.venv/bin/pip" install -q -r "$DIR/requirements.txt"

echo ">> Chromium, Systembibliotheken und virtueller Bildschirm (Xvfb)"
sudo "$DIR/.venv/bin/python" -m playwright install-deps chromium
sudo apt-get install -y -q xvfb
"$DIR/.venv/bin/python" -m playwright install chromium

echo ">> systemd-Dienst und Timer (täglich $TIME)"
sudo tee /etc/systemd/system/rewardstool.service >/dev/null <<EOF
[Unit]
Description=Microsoft Rewards Tool
Wants=network-online.target
After=network-online.target

[Service]
Type=oneshot
User=$RUN_USER
WorkingDirectory=$DIR
# Browser mit Fenster auf virtuellem Bildschirm: "Auf Bing erkunden" wird nur so gutgeschrieben
ExecStart=/usr/bin/xvfb-run -a --server-args="-screen 0 1366x900x24" $DIR/.venv/bin/python $DIR/main.py run
Environment=REWARDS_TRIGGER=timer
TimeoutStartSec=2h
EOF

sudo tee /etc/systemd/system/rewardstool.timer >/dev/null <<EOF
[Unit]
Description=Microsoft Rewards Tool täglich starten

[Timer]
OnCalendar=*-*-* $TIME:00
# Lief der Server zur geplanten Zeit nicht, wird der Lauf nach dem Hochfahren nachgeholt
Persistent=true

[Install]
WantedBy=timers.target
EOF

echo ">> Webinterface-Dienst (Port aus config.json, Standard 3333)"
sudo tee /etc/systemd/system/rewardstool-web.service >/dev/null <<EOF
[Unit]
Description=Microsoft Rewards Tool – Webinterface
Wants=network-online.target
After=network-online.target

[Service]
User=$RUN_USER
WorkingDirectory=$DIR
Environment=PYTHONUNBUFFERED=1
# Virtueller Bildschirm: Läufe und die Microsoft-Anmeldung laufen mit sichtbarem Browser
ExecStart=/usr/bin/xvfb-run -a $DIR/.venv/bin/python $DIR/webui.py
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now rewardstool.timer
sudo systemctl enable rewardstool-web.service
sudo systemctl restart rewardstool-web.service

echo
echo "Fertig. Nächster Lauf:"
systemctl list-timers rewardstool.timer --no-pager | head -n 2
PORT=$(python3 -c "import json;print(json.load(open('$DIR/config.json')).get('webui',{}).get('port',3333))")
echo
echo "Webinterface: http://$(hostname -I | awk '{print $1}'):$PORT"
echo "Beim ersten Aufruf dort ein Passwort festlegen, danach über 'Microsoft-Anmeldung' anmelden."
