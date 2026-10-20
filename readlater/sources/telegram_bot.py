"""Telegram intake: long-polls the Bot API, turns your messages into saves, replies with summaries."""
import logging
import os
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

from readlater.sources.links import find_items, item_key
from readlater.store.db import DB, Reply

log = logging.getLogger(__name__)
TELEGRAM_PRIORITY = 0
MAX_TEXT = 4000
POLL_TIMEOUT = 25

SAVED = "Saved. Summary in a few minutes."
IN_PROGRESS = "Summary still in progress."
HELP = "Send me a link, or paste an article."
START = "Share a link or paste an article and I'll summarize it. Commands: /recent, /retry"


class TelegramError(Exception):
    """A Bot API call failed; the message never contains the token."""


class TelegramAPI:
    def __init__(self, token: str, client: httpx.Client):
        self._token = token
        self.base = f"https://api.telegram.org/bot{token}/"
        self.http = client

    def redact(self, text: str) -> str:
        return text.replace(self._token, "***")

    def call(self, method: str, **params) -> Any:
        try:
            r = self.http.post(self.base + method, json=params)
            r.raise_for_status()
        except httpx.HTTPError as e:
            raise TelegramError(self.redact(str(e))) from None   # the URL holds the token
        return r.json()["result"]

    def reply(self, chat_id: int, text: str, reply_to: int | None = None) -> None:
        params = {"chat_id": chat_id, "text": text[:MAX_TEXT], "link_preview_options": {"is_disabled": True}}
        if reply_to:
            params["reply_parameters"] = {"message_id": reply_to}
        self.call("sendMessage", **params)


def load_token() -> str:
    creds = os.environ.get("CREDENTIALS_DIRECTORY")
    if creds and (Path(creds) / "telegram").exists():
        return (Path(creds) / "telegram").read_text().strip()
    token = os.environ.get("READLATER_TELEGRAM_TOKEN")
    if not token:
        raise RuntimeError("no bot token: set READLATER_TELEGRAM_TOKEN or use systemd LoadCredential=telegram")
    return token.strip()


def _day(ts: int) -> str:
    return f"{datetime.fromtimestamp(ts):%Y-%m-%d}"


def _command(api, db: DB, chat: int, mid: int, text: str) -> None:
    name = text.split()[0].split("@")[0].lower()
    if name == "/recent":
        rows = db.recent(10)
        api.reply(chat, "\n".join(f"{_day(r['saved_at'])} · {r['title']}" for r in rows) or "Nothing saved yet.", mid)
    elif name == "/retry":
        api.reply(chat, f"Re-queued {db.retry_failed()} failed jobs.", mid)
    else:
        api.reply(chat, START, mid)


def handle_message(api, db: DB, cfg, m: dict, now: int) -> None:
    chat, mid = m["chat"]["id"], m["message_id"]
    text = m.get("text") or m.get("caption") or ""
    if text.startswith("/"):
        return _command(api, db, chat, mid, text)
    links = [e["url"] for e in (*m.get("entities", ()), *m.get("caption_entities", ())) if e.get("type") == "text_link"]
    items = find_items(text, links)
    if not items:
        return api.reply(chat, HELP, mid)
    for item in items:
        res = db.add_save(source="telegram", source_id=f"{chat}:{mid}:{item_key(item)}", saved_at=m["date"],
                          payload=item, priority=TELEGRAM_PRIORITY, reply_chat=chat, reply_to=mid, now=now)
        if not res.created:
            continue
        if res.known_article:
            api.reply(chat, f"Already saved on {_day(res.first_saved)}\n\n{res.tldr or IN_PROGRESS}", mid)
            if res.tldr:   # already answered; without a TL;DR the summary follows when ready
                save_id = db.conn.execute("SELECT id FROM saves WHERE source='telegram' AND source_id=?",
                                          (f"{chat}:{mid}:{item_key(item)}",)).fetchone()[0]
                db.mark_replied(save_id, now)
        else:
            api.reply(chat, SAVED, mid)


def process_updates(api, db: DB, cfg, updates: list[dict], now: int) -> int:
    offset = int(db.get_cursor("telegram") or 0)
    allowed = set(cfg.telegram.allowed_user_ids)
    for u in updates:
        m = u.get("message")
        try:
            sender = (m or {}).get("from", {}).get("id")
            if m and sender in allowed:
                handle_message(api, db, cfg, m, now)
            elif m:
                log.info("ignoring message from user id %s", sender)
        except Exception:
            log.exception("failed to handle update %s", u.get("update_id"))
        offset = u["update_id"] + 1
        db.set_cursor("telegram", str(offset))   # after handling: at-least-once; saves dedupe repeats
    return offset


def format_summary(r: Reply) -> str:
    parts = [r.title, r.tldr, "\n".join(f"• {p}" for p in r.key_points), r.url]
    return "\n\n".join(p for p in parts if p)


def deliver_replies(api, db: DB, now: int) -> int:
    sent = 0
    for r in db.pending_replies():
        if r.status == "done":
            text = format_summary(r)
        else:
            text = f"Couldn't summarize {r.url or 'your text'}: {r.error}\nSend /retry to try again."
        api.reply(r.chat_id, text, r.reply_to)
        db.mark_replied(r.save_id, now)
        sent += 1
    return sent


def run(cfg, db: DB, api: TelegramAPI, stop: threading.Event) -> None:
    while not stop.is_set():
        try:
            offset = int(db.get_cursor("telegram") or 0)
            updates = api.call("getUpdates", offset=offset, timeout=POLL_TIMEOUT, allowed_updates=["message"])
            process_updates(api, db, cfg, updates, int(time.time()))
            deliver_replies(api, db, int(time.time()))
        except TelegramError as e:
            log.warning("telegram error: %s", e)
            stop.wait(10)
