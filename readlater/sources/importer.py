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
