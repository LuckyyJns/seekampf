# ============================================================
#  Richtet den Seekampf-Bot fuer den vollautomatischen Betrieb ein:
#   Autostart beim Windows-Login (versteckt, im Hintergrund)
#  Einmal ausfuehren. Kein Admin noetig.
# ============================================================

$ErrorActionPreference = "Stop"
$dir = $PSScriptRoot
$vbs = Join-Path $dir "autostart.vbs"

if (-not (Test-Path (Join-Path $dir ".venv"))) {
    Write-Host "[!] Keine .venv gefunden. Bitte zuerst 'uv sync' in diesem Ordner ausfuehren." -ForegroundColor Yellow
    exit 1
}

$envPath = Join-Path $dir ".env"
if (-not (Test-Path $envPath) -or (Select-String -Path $envPath -Pattern "sk_live_\.\.\." -Quiet)) {
    Write-Host "[!] .env fehlt oder enthaelt noch den Platzhalter-Key. Bitte SEEKAMPF_API_KEY in .env eintragen." -ForegroundColor Yellow
    exit 1
}

$WshShell = New-Object -ComObject WScript.Shell
$startup  = [Environment]::GetFolderPath("Startup")

# --- Autostart-Verknuepfung im Startup-Ordner ---
$lnkStartup = Join-Path $startup "Seekampf-Bot (Autostart).lnk"
$s = $WshShell.CreateShortcut($lnkStartup)
$s.TargetPath       = "wscript.exe"
$s.Arguments        = """$vbs"""
$s.WorkingDirectory = $dir
$s.WindowStyle       = 7
$s.Description       = "Startet den Seekampf-Ressourcen-Bot beim Login im Hintergrund"
$s.Save()
Write-Host "[OK] Autostart eingerichtet: $lnkStartup" -ForegroundColor Green

# --- Sofort starten (versteckt) ---
Start-Process "wscript.exe" -ArgumentList """$vbs"""
Write-Host "[OK] Bot im Hintergrund gestartet." -ForegroundColor Green
Write-Host ""
Write-Host "Log-Datei: $dir\logs\bot.log" -ForegroundColor Cyan
Write-Host "Ab sofort startet der Bot automatisch bei jedem Windows-Login." -ForegroundColor Cyan
