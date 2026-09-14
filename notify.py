"""Push aufs Handy.

Standard ist ntfy.sh: App installieren, Thema abonnieren, fertig - kein Konto,
kein API-Key, kein Bot-Setup. Telegram und Pushover gehen genauso.
"""
from __future__ import annotations

import json
import logging
import re
import urllib.error
import urllib.parse
import urllib.request

log = logging.getLogger(__name__)


class Notifier:
    def send(self, title: str, body: str, priority: str = "default",
             tags: list[str] | None = None) -> bool:
        raise NotImplementedError


class ConsoleNotifier(Notifier):
    def send(self, title, body, priority="default", tags=None) -> bool:
        print(f"\n=== {title} ===\n{body}\n")
        return True


class NtfyNotifier(Notifier):
    """ntfy.sh - der einfachste Weg.

    1. App "ntfy" aus dem Store installieren
    2. Thema abonnieren, z.B. seekampf-a7f3k9x2   (ausreichend lang + zufaellig
       waehlen, denn wer das Thema kennt, liest mit)
    3. denselben Namen unten in config.toml eintragen
    """

    def __init__(self, topic: str, server: str = "https://ntfy.sh",
                 token: str = "", click_url: str = ""):
        self.topic = topic
        self.server = server.rstrip("/")
        self.token = token
        self.click_url = click_url

    def send(self, title, body, priority="default", tags=None) -> bool:
        headers = {
            "Title": title.encode("utf-8").decode("latin-1", "replace"),
            "Priority": priority,
            "Markdown": "yes",
        }
        if tags:
            headers["Tags"] = ",".join(tags)
        if self.click_url:
            headers["Click"] = self.click_url
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        req = urllib.request.Request(
            f"{self.server}/{self.topic}",
            data=body.encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                return r.status < 300
        except Exception as e:
            log.error("ntfy fehlgeschlagen: %s", e)
            return False


class TelegramNotifier(Notifier):
    """Telegram.

    Sendet als HTML statt Markdown: der Report enthaelt Unterstriche, Pfeile
    und Klammern, an denen Telegrams Markdown-Parser regelmaessig scheitert.
    Lange Reports werden automatisch auf mehrere Nachrichten aufgeteilt
    (Telegram schneidet bei 4096 Zeichen ab).
    """

    LIMIT = 3900

    def __init__(self, bot_token: str, chat_id: str, silent: bool = False):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.silent = silent

    # -- Markdown des Reports nach Telegram-HTML uebersetzen --
    @staticmethod
    def _to_html(text: str) -> str:
        text = (text.replace("&", "&amp;")
                    .replace("<", "&lt;")
                    .replace(">", "&gt;"))
        # **fett** -> <b>fett</b>
        return re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)

    def _chunks(self, text: str):
        """An Zeilengrenzen teilen, damit keine Formatierung zerrissen wird."""
        buf: list[str] = []
        size = 0
        for line in text.split("\n"):
            if size + len(line) + 1 > self.LIMIT and buf:
                yield "\n".join(buf)
                buf, size = [], 0
            buf.append(line)
            size += len(line) + 1
        if buf:
            yield "\n".join(buf)

    def _send_one(self, html: str) -> bool:
        payload = urllib.parse.urlencode({
            "chat_id": self.chat_id,
            "text": html,
            "parse_mode": "HTML",
            "disable_web_page_preview": "true",
            "disable_notification": "true" if self.silent else "false",
        }).encode()
        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        try:
            with urllib.request.urlopen(
                urllib.request.Request(url, data=payload), timeout=15
            ) as r:
                res = json.loads(r.read())
        except urllib.error.HTTPError as e:
            log.error("Telegram lehnte ab: %s", e.read().decode("utf-8", "replace")[:300])
            return False
        except Exception as e:
            log.error("Telegram fehlgeschlagen: %s", e)
            return False
        if not res.get("ok"):
            log.error("Telegram: %s", res.get("description"))
        return bool(res.get("ok"))

    def send(self, title, body, priority="default", tags=None) -> bool:
        if not self.bot_token or not self.chat_id:
            log.error("bot_token oder chat_id fehlt - siehe [notify] in config.toml")
            return False
        full = f"**{title}**\n\n{body}"
        ok = True
        for chunk in self._chunks(self._to_html(full)):
            ok = self._send_one(chunk) and ok
        return ok

    # -- Einrichtungshilfe --
    def find_chat_id(self) -> list[tuple[str, str]]:
        """Liest getUpdates und gibt gefundene (chat_id, Name) zurueck."""
        url = f"https://api.telegram.org/bot{self.bot_token}/getUpdates"
        with urllib.request.urlopen(url, timeout=15) as r:
            res = json.loads(r.read())
        out: list[tuple[str, str]] = []
        for upd in res.get("result", []):
            msg = upd.get("message") or upd.get("channel_post") or {}
            chat = msg.get("chat") or {}
            if not chat.get("id"):
                continue
            name = (chat.get("title")
                    or " ".join(filter(None, [chat.get("first_name"),
                                              chat.get("last_name")]))
                    or chat.get("username") or "?")
            pair = (str(chat["id"]), name)
            if pair not in out:
                out.append(pair)
        return out


class PushoverNotifier(Notifier):
    def __init__(self, token: str, user: str):
        self.token = token
        self.user = user

    def send(self, title, body, priority="default", tags=None) -> bool:
        prio = {"min": -2, "low": -1, "default": 0, "high": 1, "urgent": 2}
        payload = urllib.parse.urlencode({
            "token": self.token, "user": self.user,
            "title": title, "message": body,
            "priority": prio.get(priority, 0),
        }).encode()
        try:
            with urllib.request.urlopen(
                urllib.request.Request("https://api.pushover.net/1/messages.json",
                                       data=payload), timeout=15
            ) as r:
                return json.loads(r.read()).get("status") == 1
        except Exception as e:
            log.error("Pushover fehlgeschlagen: %s", e)
            return False


def build_notifier(cfg: dict) -> Notifier:
    kind = (cfg.get("service") or "console").lower()
    if kind == "ntfy":
        return NtfyNotifier(
            topic=cfg.get("topic", ""),
            server=cfg.get("server", "https://ntfy.sh"),
            token=cfg.get("token", ""),
            click_url=cfg.get("click_url", ""),
        )
    if kind == "telegram":
        return TelegramNotifier(cfg.get("bot_token", ""), cfg.get("chat_id", ""),
                                silent=bool(cfg.get("silent", False)))
    if kind == "pushover":
        return PushoverNotifier(cfg.get("token", ""), cfg.get("user", ""))
    return ConsoleNotifier()
