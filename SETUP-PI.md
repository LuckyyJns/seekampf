# Umzug auf den Raspberry Pi

## 1. Repo auf den Pi holen

```bash
git clone <REPO-URL> ~/seekampf-bot
cd ~/seekampf-bot
```

## 2. Python-Umgebung einrichten (uv, wie auf Windows)

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
source $HOME/.local/bin/env   # oder neues Terminal oeffnen
uv sync
```

`uv sync` loest die Abhaengigkeiten fuer die Pi-Architektur neu auf (`uv.lock`
wird dabei automatisch aktualisiert) - unabhaengig vom Windows-Lock.

## 3. `.env` übertragen (NICHT über Git!)

Von deinem Windows-PC aus, per SCP (PowerShell oder Git-Bash):

```bash
scp ".env" pi@<PI-IP-ODER-HOSTNAME>:~/seekampf-bot/.env
```

Ersetze `pi@<PI-IP>` durch deinen tatsächlichen Nutzernamen/Hostnamen auf dem Pi.
Die `.env` enthält den Seekampf-API-Key sowie `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID` -
deshalb bewusst getrennt vom Git-Repo (`.gitignore` schließt sie aus).

## 4. Systemd-Service einrichten

`seekampf-bot.service` geht davon aus, dass der Pi-Nutzer `pi` heißt und das Repo
unter `/home/pi/seekampf-bot` liegt. **Vor dem Kopieren prüfen/anpassen**, falls dein
Nutzername oder Pfad abweicht (`User=`, `WorkingDirectory=`, `ExecStart=`).

```bash
sudo cp seekampf-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now seekampf-bot.service
```

`Restart=always` sorgt dafür, dass systemd den Bot bei einem Absturz automatisch
neu startet - das ist sogar robuster als der bisherige Windows-Autostart (der nur
beim Login greift).

## 5. Prüfen, ob es läuft

```bash
systemctl status seekampf-bot.service
journalctl -u seekampf-bot.service -f     # Live-Systemd-Log
tail -f ~/seekampf-bot/logs/bot.log        # Bot-eigenes Log
```

## 6. Windows-Bot abschalten

Erst wenn der Pi-Bot bestätigt läuft (siehe Schritt 5) - dann auf Windows:

```powershell
Get-CimInstance Win32_Process -Filter "Name='pythonw.exe'" | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
Remove-Item "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Startup\Seekampf-Bot (Autostart).lnk"
```

## Updates später

```bash
cd ~/seekampf-bot
git pull
uv sync
sudo systemctl restart seekampf-bot.service
```
