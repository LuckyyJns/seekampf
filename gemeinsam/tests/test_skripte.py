"""sicherung.sh und pruefe_geheimnisse.sh - in einem abgeschotteten Temp-HOME mit
eigenem Repo und lokalem "GitHub" (bare Remote). Das echte Repo und GitHub
werden nie beruehrt, es geht keine Telegram-Nachricht raus (keine .env im Hub).

    .venv/bin/python -m unittest discover -s tests
"""
import json
import os
import shutil
import subprocess
import tarfile
import tempfile
import unittest

SEEKAMPF = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@unittest.skipUnless(shutil.which("git") and shutil.which("tar"), "git/tar fehlt")
class Sicherung(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="sicherung-test-")
        self.addCleanup(shutil.rmtree, self.home, True)
        self.repo = os.path.join(self.home, "Seekampf")
        os.makedirs(self.repo)
        for skript in ("sicherung.sh", "pruefe_geheimnisse.sh"):
            shutil.copy2(os.path.join(SEEKAMPF, skript), self.repo)
        self.schreiben(".gitignore", "**/data/*\n**/logs/\n**/.venv/\n.env\n*/.env\n")
        self.schreiben("Bot/bot.py", "print('hallo')\n")
        self.schreiben("Bot/.env", "SEEKAMPF_API_KEY=geheimwert-1234567890\n")
        self.schreiben("Bot/data/state.json", "{}")
        self.schreiben("Bot/logs/bot-2026-10-20.log", "x")
        self.schreiben("Bot/.venv/lib.py", "x")
        self.remote = os.path.join(self.home, "github.git")
        self.git("init", "-q", "--bare", "-b", "master", self.remote, cwd=self.home)
        self.git("init", "-q", "-b", "master")
        self.git("remote", "add", "origin", self.remote)
        self.git("add", "-A")
        self.git("-c", "user.name=T", "-c", "user.email=t@t", "commit", "-q", "-m", "Start")
        self.git("push", "-q", "-u", "origin", "master")

    def schreiben(self, pfad, inhalt):
        voll = os.path.join(self.repo, pfad)
        os.makedirs(os.path.dirname(voll), exist_ok=True)
        with open(voll, "w") as f:
            f.write(inhalt)

    def git(self, *args, cwd=None):
        return subprocess.run(["git", *args], cwd=cwd or self.repo, capture_output=True, text=True,
                              env=self.env(), check=True).stdout.strip()

    def env(self):
        return {"HOME": self.home, "PATH": os.environ.get("PATH", "/usr/bin:/bin"), "LANG": "C.UTF-8"}

    def sichern(self):
        return subprocess.run(["bash", os.path.join(self.repo, "sicherung.sh")], capture_output=True, text=True,
                              env=self.env(), timeout=120)

    def lauf(self):
        with open(os.path.join(self.home, "Seekampf-Sicherungen", "letzter-lauf.json")) as f:
            return json.load(f)

    def remote_log(self):
        return self.git("--git-dir", self.remote, "log", "--format=%s", cwd=self.home).splitlines()

    def test_archiv_commit_und_push(self):
        self.schreiben("Bot/bot.py", "print('neu')\n")
        erg = self.sichern()
        self.assertEqual(erg.returncode, 0, erg.stderr + erg.stdout)
        [archiv] = [n for n in os.listdir(os.path.join(self.home, "Seekampf-Sicherungen")) if n.endswith(".tar.gz")]
        with tarfile.open(os.path.join(self.home, "Seekampf-Sicherungen", archiv)) as t:
            namen = t.getnames()
        self.assertIn("Seekampf/Bot/.env", namen)                  # Zugangsdaten gehoeren in die Sicherung
        self.assertIn("Seekampf/Bot/data/state.json", namen)
        self.assertFalse(any("/.venv" in n or "/logs" in n for n in namen))
        self.assertEqual(oct(os.stat(os.path.join(self.home, "Seekampf-Sicherungen")).st_mode & 0o777), "0o700")
        lauf = self.lauf()
        self.assertTrue(lauf["ok"])
        self.assertIn("GitHub aktuell", lauf["meldung"])
        self.assertTrue(self.remote_log()[0].startswith("Automatische Sicherung"))

    def test_geheimnis_stoppt_commit_und_push(self):
        self.schreiben("Bot/notiz.py", "KEY = 'geheimwert-1234567890'\n")
        erg = self.sichern()
        self.assertEqual(erg.returncode, 1)
        lauf = self.lauf()
        self.assertFalse(lauf["ok"])
        self.assertIn("Commit gestoppt", lauf["meldung"])
        self.assertNotIn("geheimwert", lauf["meldung"])            # der Wert selbst steht nirgends
        self.assertEqual(self.remote_log(), ["Start"])
        self.assertEqual(self.git("diff", "--cached", "--name-only"), "")   # nichts vorgemerkt


if __name__ == "__main__":
    unittest.main()
