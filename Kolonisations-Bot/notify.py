"""Telegram-Benachrichtigung fuer den Morgenreport.

Uebernommen aus dem Upgrade-Bot, auf das Noetige gekuerzt: HTML statt
Markdown (an Unterstrichen und Klammern scheitert Telegrams Markdown-Parser
regelmaessig) und Aufteilung langer Texte auf mehrere Nachrichten.
"""
from __future__ import annotations

import json
import logging
import re
import urllib.error
import urllib.parse
import urllib.request

log = logging.getLogger("seekampf_kolonisation")


class TelegramNotifier:
    LIMIT = 3900

    def __init__(self, bot_token: str, chat_id: str):
        self.bot_token = bot_token
        self.chat_id = chat_id

    @property
    def aktiv(self) -> bool:
        return bool(self.bot_token and self.chat_id)

    @staticmethod
    def _to_html(text: str) -> str:
        text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        return re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)

    def _chunks(self, text: str):
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
            "chat_id": self.chat_id, "text": html, "parse_mode": "HTML",
            "disable_web_page_preview": "true",
        }).encode()
        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        try:
            with urllib.request.urlopen(urllib.request.Request(url, data=payload), timeout=15) as r:
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

    def send(self, title: str, body: str) -> bool:
        if not self.aktiv:
            log.error("Telegram nicht eingerichtet - TELEGRAM_BOT_TOKEN/CHAT_ID fehlen in der .env")
            return False
        ok = True
        for chunk in self._chunks(self._to_html(f"**{title}**\n\n{body}")):
            ok = self._send_one(chunk) and ok
        return ok
