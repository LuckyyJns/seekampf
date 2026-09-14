' Startet den Seekampf-Bot komplett unsichtbar im Hintergrund (kein Fenster).
' Wird vom Autostart-Eintrag beim Windows-Login aufgerufen.
Dim shell, fso, scriptDir, botScript, pythonw
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
botScript = scriptDir & "\bot.py"
pythonw = scriptDir & "\.venv\Scripts\pythonw.exe"
shell.CurrentDirectory = scriptDir
shell.Run """" & pythonw & """ """ & botScript & """", 0, False
