import email
from email.message import EmailMessage
from types import SimpleNamespace

import pytest
from mail_helpers import make_msg

from readlater.config import MailCfg
from readlater.sources.importer import iter_imap

MAIL = MailCfg(host="imap.example.com", user="you@example.com", folder="INBOX", own_addresses=["me@x.com"])
KEY = "imap:you@example.com:INBOX"


class FakeBox:
    def __init__(self, uids, uidvalidity=7):
        self.uids, self.uidvalidity = uids, uidvalidity
        self.readonly = None
        self.fetch_kwargs = None
        self.folder = SimpleNamespace(set=self._set, status=lambda folder, opts: {"UIDVALIDITY": self.uidvalidity})

    def _set(self, folder, readonly=False):
        self.readonly = readonly

    def factory(self, host):
        return self

    def login(self, user, password):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def fetch(self, criteria, **kwargs):
        self.fetch_kwargs = kwargs
        legacy = email.message_from_bytes(make_msg().as_bytes())   # imap-tools gives compat32 Messages
        for uid in self.uids:
            yield SimpleNamespace(uid=str(uid), obj=legacy)


def run(db, box, **kw):
    args = {"full": False, "commit_cursor": True} | kw
    return list(iter_imap(MAIL, "pw", db, mailbox_factory=box.factory, **args))


def test_readonly_and_cursor(db):
    box = FakeBox([3, 5])
    msgs = run(db, box)
    assert len(msgs) == 2 and all(isinstance(m, EmailMessage) for m in msgs)
    assert box.readonly is True and box.fetch_kwargs["mark_seen"] is False
    assert db.get_cursor(KEY) == "7:5"


def test_resumes_after_cursor_and_skips_star_repeat(db):
    db.set_cursor(KEY, "7:5")
    assert run(db, FakeBox([5])) == []


def test_uidvalidity_change_or_full_restarts(db):
    db.set_cursor(KEY, "6:5")
    assert len(run(db, FakeBox([3]))) == 1
    db.set_cursor(KEY, "7:5")
    assert len(run(db, FakeBox([3]), full=True)) == 1


def test_dry_run_does_not_move_cursor(db):
    run(db, FakeBox([3]), commit_cursor=False)
    assert db.get_cursor(KEY) is None
