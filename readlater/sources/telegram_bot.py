"""Telegram intake: long-polls the Bot API, turns your messages into saves, replies with summaries."""
import html
import logging
import os
import threading
import time
from datetime import datetime
from urllib.parse import urlsplit
from pathlib import Path
from typing import Any

import httpx

from readlater.export import flatten_points
from readlater.sources.links import find_items, item_key
from readlater.store.db import DB, Reply

log = logging.getLogger(__name__)
TELEGRAM_PRIORITY = 0
MAX_TEXT = 4000
POLL_TIMEOUT = 25
MAX_TITLE = 300

SAVED = "Saved. Summary in a few minutes."
IN_PROGRESS = "Summary still in progress."
HELP = "Send me a link, or paste an article."
START = "Share a link or paste an article and I'll summarize it. Commands: /recent, /retry"


class TelegramError(Exception):
    """A Bot API call failed; the message never contains the token."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


def _fit_utf16(text: str, limit: int = MAX_TEXT) -> str:
    """Telegram counts message length in UTF-16 code units, so emoji count double."""
    if len(text.encode("utf-16-le")) // 2 <= limit:
        return text
    out, units = [], 0
    for ch in text:
        units += 2 if ord(ch) > 0xFFFF else 1
        if units > limit:
            break
        out.append(ch)
    return "".join(out)


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
        except httpx.HTTPStatusError as e:
            try:
                detail = e.response.json().get("description") or str(e)
            except ValueError:
                detail = str(e)
            raise TelegramError(self.redact(detail), e.response.status_code) from None   # the URL holds the token
        except httpx.HTTPError as e:
            raise TelegramError(self.redact(str(e))) from None
        return r.json()["result"]

    def reply(self, chat_id: int, text: str, reply_to: int | None = None, html: bool = False) -> None:
        """html=True sends Telegram HTML; the caller escapes the text and keeps it within MAX_TEXT."""
        params = {"chat_id": chat_id, "text": text if html else _fit_utf16(text),
                  "link_preview_options": {"is_disabled": True}}
        if html:
            params["parse_mode"] = "HTML"
        if reply_to:   # still send if the user deleted the message we answer
            params["reply_parameters"] = {"message_id": reply_to, "allow_sending_without_reply": True}
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
            if res.retrying:
                api.reply(chat, f"Already saved on {_day(res.first_saved)}, but it failed last time — retrying now.", mid)
                continue
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


def _utf16_len(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def format_summary(r: Reply) -> str:
    """Telegram HTML: bold title, TL;DR, bullets, link. Drops trailing bullets to stay under MAX_TEXT."""
    esc = html.escape
    title = r.title if not r.title or len(r.title) <= MAX_TITLE else r.title[:MAX_TITLE].rstrip() + "…"
    head = f"<b>{esc(title)}</b>" if title else ""
    link = ""
    if r.url:
        host = (urlsplit(r.url).hostname or r.url).removeprefix("www.")
        link = f'<a href="{esc(r.url, quote=True)}">{esc(host)}</a>'
    tldr = r.tldr or ""
    points = flatten_points(r.key_points)

    def build(points: list[str], tldr: str) -> str:
        bullets = "\n".join(f"• {esc(p)}" for p in points)
        return "\n\n".join(part for part in (head, esc(tldr), bullets, link) if part)

    text = build(points, tldr)
    while _utf16_len(text) > MAX_TEXT and points:
        points = points[:-1]
        text = build(points, tldr)
    while _utf16_len(text) > MAX_TEXT and tldr:
        tldr = tldr[:-200].rstrip()
        text = build(points, tldr + "…" if tldr else "")
    return text


def deliver_replies(api, db: DB, now: int) -> int:
    sent = 0
    for r in db.pending_replies():
        done = r.status == "done"
        if done:
            text = format_summary(r)
        else:
            text = f"Couldn't summarize {r.url or 'your text'}: {r.error}\nSend /retry to try again."
        try:
            api.reply(r.chat_id, text, r.reply_to, html=done)
        except TelegramError as e:
            if e.status is None or e.status == 429 or e.status >= 500:
                raise   # Telegram or the network is down: try the whole batch again later
            log.warning("dropping undeliverable reply for save %s: %s", r.save_id, e)
            db.mark_replied(r.save_id, now)
            continue
        db.mark_replied(r.save_id, now)
        sent += 1
    return sent


def run(cfg, db: DB, api: TelegramAPI, stop: threading.Event, error_wait: float = 10) -> None:
    while not stop.is_set():
        try:
            offset = int(db.get_cursor("telegram") or 0)
            updates = api.call("getUpdates", offset=offset, timeout=POLL_TIMEOUT, allowed_updates=["message"])
            process_updates(api, db, cfg, updates, int(time.time()))
            deliver_replies(api, db, int(time.time()))
        except TelegramError as e:
            log.warning("telegram error: %s", e)
            stop.wait(error_wait)
        except Exception:
            log.exception("bot loop error")
            stop.wait(error_wait)
