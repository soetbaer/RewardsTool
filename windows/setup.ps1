# RewardsTool – komplette Einrichtung unter Windows (wird von Setup.bat aufgerufen)
# Erneut ausführen = Update/Reparatur; die Microsoft-Anmeldung bleibt erhalten.
param(
    # Nur für Tests: keine Änderungen am System (keine Python-Installation, Aufgabe, Verknüpfungen)
    [switch]$SkipSystem
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'   # macht Downloads in Windows PowerShell 5.1 deutlich schneller
# winget, pip & Co. schreiben UTF-8 – ohne Umstellung zeigt Windows PowerShell 5.1 die Umlaute kaputt an
try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch { }
$OutputEncoding = [Text.Encoding]::UTF8
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
$Host.UI.RawUI.WindowTitle = 'RewardsTool – Einrichtung'

$VenvPy  = Join-Path $Root '.venv\Scripts\python.exe'
$VenvPyw = Join-Path $Root '.venv\Scripts\pythonw.exe'
$TaskName = 'RewardsTool'
$PythonFallbackUrl = 'https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe'

function Step($n, $text) { Write-Host ''; Write-Host "[$n/5] $text" -ForegroundColor Cyan }
function Info($text) { Write-Host "      $text" }
function Fail($text) {
    Write-Host ''
    Write-Host "FEHLER: $text" -ForegroundColor Red
    exit 1
}
function Ask-YesNo($question, [bool]$default = $true) {
    $hint = if ($default) { '[J/n]' } else { '[j/N]' }
    $answer = Read-Host "      $question $hint"
    if ([string]::IsNullOrWhiteSpace($answer)) { return $default }
    return $answer.Trim().ToLower().StartsWith('j')
}

# ---------------------------------------------------------------- Begrüßung
Write-Host '=================================================' -ForegroundColor Cyan
Write-Host '  RewardsTool – Einrichtung' -ForegroundColor Cyan
Write-Host '=================================================' -ForegroundColor Cyan
Write-Host ''
Write-Host 'WICHTIG: Automatisches Punktesammeln verstößt gegen die Microsoft-Rewards-' -ForegroundColor Yellow
Write-Host 'Bedingungen. Microsoft kann Punkte streichen oder das Konto sperren.' -ForegroundColor Yellow
Write-Host 'Die Nutzung erfolgt auf eigenes Risiko.' -ForegroundColor Yellow
Write-Host ''
if (-not $SkipSystem -and -not (Ask-YesNo 'Einrichtung starten?')) { exit 1 }

# Aus dem Internet geladene Programmdateien freigeben (sonst blockiert Windows sie teilweise)
Get-ChildItem -Path $Root -Recurse -File -ErrorAction SilentlyContinue |
    Where-Object { $_.FullName -notmatch '\\(profile|\.venv|data|logs|debug)\\' } |
    Unblock-File -ErrorAction SilentlyContinue

# ---------------------------------------------------------------- 1. Python
function Test-Python($exe, [string[]]$prefix = @()) {
    try {
        $out = & $exe @prefix -c "import sys; print(sys.executable if sys.version_info >= (3, 10) else '')" 2>$null
        if ($LASTEXITCODE -eq 0 -and $out) {
            $path = "$out".Trim()
            if ($path -and (Test-Path $path)) { return $path }
        }
    } catch { }
    return $null
}

function Find-Python {
    $p = Test-Python 'py' @('-3'); if ($p) { return $p }
    $p = Test-Python 'python';     if ($p) { return $p }
    $dirs = Get-ChildItem "$env:LOCALAPPDATA\Programs\Python\Python3*", "$env:ProgramFiles\Python3*" `
        -Directory -ErrorAction SilentlyContinue | Sort-Object Name -Descending
    foreach ($d in $dirs) {
        $p = Test-Python (Join-Path $d.FullName 'python.exe'); if ($p) { return $p }
    }
    return $null
}

function Install-Python {
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        Info 'Installiere Python über winget (kann 1–3 Minuten dauern) ...'
        & winget install --exact --id Python.Python.3.12 --scope user --silent `
            --accept-package-agreements --accept-source-agreements --disable-interactivity
        if (Find-Python) { return }
        Info 'winget hat nicht geklappt – lade Python direkt von python.org ...'
    } else {
        Info 'Lade Python von python.org herunter ...'
    }
    $file = Join-Path $env:TEMP (Split-Path $PythonFallbackUrl -Leaf)
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    Invoke-WebRequest -Uri $PythonFallbackUrl -OutFile $file -UseBasicParsing
    # Nur ausführen, wenn der Installer gültig von der Python Software Foundation signiert ist
    $sig = Get-AuthenticodeSignature $file
    if ($sig.Status -ne 'Valid' -or $sig.SignerCertificate.Subject -notmatch 'Python Software Foundation') {
        Remove-Item $file -ErrorAction SilentlyContinue
        Fail 'Der heruntergeladene Python-Installer hat keine gültige Signatur – Abbruch.'
    }
    Info 'Installiere Python (still, nur für diesen Benutzer) ...'
    $proc = Start-Process -FilePath $file -Wait -PassThru -ArgumentList @(
        '/quiet', 'InstallAllUsers=0', 'PrependPath=1', 'Include_launcher=1', 'Include_test=0')
    Remove-Item $file -ErrorAction SilentlyContinue
    if ($proc.ExitCode -ne 0) { Fail "Python-Installation fehlgeschlagen (Code $($proc.ExitCode))." }
}

Step 1 'Python prüfen'
$Py = Find-Python
if (-not $Py) {
    if ($SkipSystem) { Fail 'Python fehlt (Testmodus: keine Installation).' }
    Info 'Python 3.10 oder neuer wurde nicht gefunden.'
    Install-Python
    $Py = Find-Python
    if (-not $Py) {
        Fail ('Python konnte nicht automatisch installiert werden. Bitte von https://www.python.org/downloads/ ' +
              'installieren (Haken "Add python.exe to PATH" setzen) und Setup.bat erneut starten.')
    }
}
Info "Python: $Py"

# ---------------------------------------------------------------- 2. Umgebung & Pakete
Step 2 'Programmpakete einrichten (beim ersten Mal 1–3 Minuten)'
if (-not (Test-Path $VenvPy)) {
    & $Py -m venv (Join-Path $Root '.venv')
    if ($LASTEXITCODE -ne 0) { Fail 'Python-Umgebung konnte nicht angelegt werden.' }
}
& $VenvPy -m pip install --disable-pip-version-check -q --upgrade pip
& $VenvPy -m pip install --disable-pip-version-check -q -r (Join-Path $Root 'requirements.txt')
if ($LASTEXITCODE -ne 0) { Fail 'Pakete konnten nicht installiert werden (Internetverbindung?).' }

$edge = @("${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe",
          "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe") | Where-Object { Test-Path $_ }
if ($edge) {
    Info 'Microsoft Edge gefunden – wird verwendet.'
} else {
    Info 'Edge nicht gefunden – lade Ersatz-Browser (ca. 150 MB) ...'
    & $VenvPy -m playwright install chromium
    if ($LASTEXITCODE -ne 0) { Fail 'Browser konnte nicht installiert werden.' }
}

# Laufendes Webinterface (z. B. bei Update) beenden, damit Profil und Port frei sind
Get-CimInstance Win32_Process -Filter "Name='pythonw.exe' OR Name='python.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like "*$Root*webui.py*" } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }

# ---------------------------------------------------------------- 3. Microsoft-Anmeldung
Step 3 'Microsoft-Anmeldung prüfen'
Info 'Prüfe, ob du schon angemeldet bist (ca. 20 Sekunden) ...'
# Warnungen des Tools landen auf stderr – in PowerShell 5.1 darf das hier nicht zum Abbruch führen
$ErrorActionPreference = 'Continue'
& $VenvPy (Join-Path $Root 'main.py') status *> $null
$ErrorActionPreference = 'Stop'
$status = $null
try { $status = Get-Content (Join-Path $Root 'data\status.json') -Raw -Encoding UTF8 | ConvertFrom-Json } catch { }
if ($status -and $status.logged_in) {
    Info "Bereits angemeldet – Punktestand: $($status.points)"
} elseif ($SkipSystem) {
    Info 'Nicht angemeldet (Testmodus: Anmeldung übersprungen).'
} else {
    Info 'Gleich öffnet sich ein Browserfenster mit zwei Tabs:'
    Info '  Tab 1: Mit deinem Microsoft-Konto anmelden ("Angemeldet bleiben" -> Ja).'
    Info '  Tab 2 (bing.com): oben rechts ebenfalls anmelden.'
    Info 'Danach hierher zurückkommen und Enter drücken.'
    while ($true) {
        Write-Host ''
        $ErrorActionPreference = 'Continue'
        & $VenvPy (Join-Path $Root 'main.py') login
        $ErrorActionPreference = 'Stop'
        $status = $null
        try { $status = Get-Content (Join-Path $Root 'data\status.json') -Raw -Encoding UTF8 | ConvertFrom-Json } catch { }
        if ($status -and $status.logged_in) { break }
        Write-Host '      Die Anmeldung hat nicht geklappt.' -ForegroundColor Yellow
        if (-not (Ask-YesNo 'Noch einmal versuchen?')) {
            Info 'Du kannst dich später im Webinterface über "Microsoft-Anmeldung" anmelden.'
            break
        }
    }
}

# ---------------------------------------------------------------- 4. Täglicher Lauf
Step 4 'Täglichen Lauf planen'
if ($SkipSystem) {
    Info 'Testmodus: übersprungen.'
} else {
    $time = ''
    while ($time -notmatch '^([01]?\d|2[0-3]):[0-5]\d$') {
        $time = Read-Host '      Uhrzeit für den täglichen Lauf (Enter = 09:00)'
        if ([string]::IsNullOrWhiteSpace($time)) { $time = '09:00' }
    }
    $action = New-ScheduledTaskAction -Execute $VenvPyw -Argument "`"$(Join-Path $Root 'main.py')`" run" -WorkingDirectory $Root
    $trigger = New-ScheduledTaskTrigger -Daily -At $time
    # Verpasste Läufe nachholen (PC war aus), nicht an Akkubetrieb scheitern, nach 2 Stunden abbrechen
    $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Hours 2)
    try {
        Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings `
            -Description 'RewardsTool: täglich Microsoft-Rewards-Punkte sammeln' -Force | Out-Null
        Info "Eingerichtet: täglich um $time Uhr (wenn der PC an ist und du angemeldet bist)."
        # Im Webinterface ausgeschalteter täglicher Lauf bleibt aus
        $settings = $null
        try { $settings = Get-Content (Join-Path $Root 'data\settings.json') -Raw -Encoding UTF8 | ConvertFrom-Json } catch { }
        if ($settings -and $settings.daily_run -eq $false) {
            Disable-ScheduledTask -TaskName $TaskName | Out-Null
            Info 'Der tägliche Lauf ist im Webinterface ausgeschaltet und bleibt aus.'
        }
        Info 'Während des Laufs erscheint ein Browserfenster – einfach in Ruhe lassen.'
    } catch {
        Write-Host "      Der tägliche Lauf konnte nicht eingerichtet werden: $($_.Exception.Message)" -ForegroundColor Yellow
        Info 'Du kannst Läufe trotzdem jederzeit im Webinterface starten.'
    }
}

# ---------------------------------------------------------------- 5. Webinterface
Step 5 'Webinterface einrichten'
$url = 'http://localhost:3333'
try {
    $port = (Get-Content (Join-Path $Root 'config.json') -Raw -Encoding UTF8 | ConvertFrom-Json).webui.port
    if ($port) { $url = "http://localhost:$port" }
} catch { }

if ($SkipSystem) {
    Info 'Testmodus: Autostart und Verknüpfungen übersprungen.'
} else {
    $shell = New-Object -ComObject WScript.Shell
    $startup = [Environment]::GetFolderPath('Startup')
    $autostart = Join-Path $startup 'RewardsTool Webinterface.lnk'
    if (Ask-YesNo 'Webinterface automatisch mit Windows starten? (empfohlen)') {
        $lnk = $shell.CreateShortcut($autostart)
        $lnk.TargetPath = $VenvPyw
        $lnk.Arguments = "`"$(Join-Path $Root 'webui.py')`""
        $lnk.WorkingDirectory = $Root
        $lnk.Description = 'RewardsTool Webinterface'
        $lnk.Save()
        Info 'Autostart eingerichtet.'
    } elseif (Test-Path $autostart) {
        Remove-Item $autostart
    }

    $desktop = [Environment]::GetFolderPath('Desktop')
    Set-Content -Path (Join-Path $desktop 'RewardsTool.url') -Encoding ASCII -Value "[InternetShortcut]`r`nURL=$url`r`n"
    Info 'Desktop-Verknüpfung "RewardsTool" angelegt.'

    Start-Process -FilePath $VenvPyw -ArgumentList "`"$(Join-Path $Root 'webui.py')`"" -WorkingDirectory $Root
    Start-Sleep -Seconds 3
    Start-Process $url
}

Write-Host ''
Write-Host '=================================================' -ForegroundColor Green
Write-Host '  Fertig!' -ForegroundColor Green
Write-Host '=================================================' -ForegroundColor Green
Write-Host "  Webinterface: $url  (Desktop-Verknüpfung 'RewardsTool')"
Write-Host '  Beim ersten Öffnen dort ein Passwort für das Webinterface festlegen.'
Write-Host '  Dort kannst du Läufe starten, den Status sehen und dich neu anmelden.'
Write-Host ''
exit 0
