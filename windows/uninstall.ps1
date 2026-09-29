# RewardsTool – unter Windows entfernen (wird von windows\Deinstallieren.bat aufgerufen)
$ErrorActionPreference = 'Continue'
$Root = Split-Path -Parent $PSScriptRoot

Write-Host 'RewardsTool wird entfernt ...' -ForegroundColor Cyan

Get-CimInstance Win32_Process -Filter "Name='pythonw.exe' OR Name='python.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like "*$Root*" } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Write-Host '  Laufende Prozesse beendet.'

if (Get-ScheduledTask -TaskName 'RewardsTool' -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName 'RewardsTool' -Confirm:$false
    Write-Host '  Täglicher Lauf entfernt.'
}

$files = @(
    (Join-Path ([Environment]::GetFolderPath('Startup')) 'RewardsTool Webinterface.lnk'),
    (Join-Path ([Environment]::GetFolderPath('Desktop')) 'RewardsTool.url')
)
foreach ($f in $files) { if (Test-Path $f) { Remove-Item $f; Write-Host "  Entfernt: $f" } }

Write-Host ''
Write-Host 'Fertig. Zum vollständigen Entfernen jetzt noch diesen Ordner löschen:' -ForegroundColor Green
Write-Host "  $Root"
Write-Host 'Darin liegt auch deine gespeicherte Microsoft-Anmeldung (Ordner "profile").'
