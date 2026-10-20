"""One-time migration of old self-sent emails into the queue. Read-only; never posts to Telegram."""
import email
import email.policy
import hashlib
import mailbox
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from datetime import datetime
from email.message import EmailMessage
from email.utils import parseaddr, parsedate_to_datetime
from pathlib import Path

import imap_tools
from imap_tools import AND, U

from readlater.config import MailCfg
from readlater.sources.detect import extract_body, is_candidate
from readlater.sources.links import find_items, item_key
from readlater.store.db import DB

EMAIL_PRIORITY = 10


@dataclass
class ImportStats:
    messages: int = 0
    found: int = 0
    queued: int = 0


def _date(msg: EmailMessage, default: int) -> int:
    try:
        return int(parsedate_to_datetime(str(msg["Date"])).timestamp())
    except (TypeError, ValueError):
        return default


def _message_id(msg: EmailMessage) -> str:
    mid = str(msg.get("Message-ID", "")).strip()
    return mid or hashlib.sha256(msg.as_bytes()).hexdigest()


def import_messages(db: DB, messages: Iterable[EmailMessage], own: list[str], *, dry_run: bool, now: int,
                    out: Callable[..., None] = print) -> ImportStats:
    st = ImportStats()
    for msg in messages:
        st.messages += 1
        if not is_candidate(msg, own):
            continue
        items = find_items(*extract_body(msg))
        st.found += len(items)
        date = _date(msg, now)
        if dry_run:
            for item in items:
                out(f"{datetime.fromtimestamp(date):%Y-%m-%d} {item[:120]}")
            continue
        mid = _message_id(msg)
        for item in items:
            r = db.add_save(source="email", source_id=f"{mid}:{item_key(item)}", saved_at=date, payload=item,
                            priority=EMAIL_PRIORITY, now=now)
            st.queued += r.created
        db.add_email(mid, date, parseaddr(str(msg.get("From", "")))[1])
    return st


def iter_mbox(path: Path) -> Iterator[EmailMessage]:
    box = mailbox.mbox(path, factory=lambda f: email.message_from_binary_file(f, policy=email.policy.default),
                       create=False)
    yield from box


def iter_imap(cfg: MailCfg, password: str, db: DB, *, full: bool, commit_cursor: bool,
              mailbox_factory=imap_tools.MailBox) -> Iterator[EmailMessage]:
    """Read the folder read-only (EXAMINE, nothing marked seen), resuming after the stored UID."""
    key = f"imap:{cfg.user}:{cfg.folder}"
    with mailbox_factory(cfg.host).login(cfg.user, password) as mb:
        mb.folder.set(cfg.folder, readonly=True)
        uidvalidity = str(mb.folder.status(cfg.folder, ["UIDVALIDITY"])["UIDVALIDITY"])
        stored_validity, _, stored_uid = (db.get_cursor(key) or "").partition(":")
        last = int(stored_uid) if stored_uid and stored_validity == uidvalidity and not full else 0
        for msg in mb.fetch(AND(uid=U(last + 1, "*")), mark_seen=False, bulk=50):
            uid = int(msg.uid)
            if uid <= last:   # "N:*" returns the newest message even when N is past it
                continue
            yield email.message_from_bytes(msg.obj.as_bytes(), policy=email.policy.default)
            if commit_cursor:   # only after the caller has stored this message
                db.set_cursor(key, f"{uidvalidity}:{uid}")
