import mailbox

from mail_helpers import OWN, make_msg

from readlater.sources.detect import is_candidate
from readlater.sources.importer import import_messages, iter_mbox


def test_import_queues_with_original_date_and_low_priority(db):
    m = make_msg(headers={"Message-ID": "<m1@x>", "Date": "Sat, 14 Mar 2026 10:00:00 +0000"})
    st = import_messages(db, [m], OWN, dry_run=False, now=0)
    assert (st.found, st.queued) == (1, 1)
    row = db.conn.execute("SELECT source, saved_at, payload FROM saves").fetchone()
    assert tuple(row) == ("email", 1773482400, "https://a.com/x")
    assert db.conn.execute("SELECT priority FROM jobs").fetchone()[0] == 10


def test_reimport_adds_nothing(db):
    m = make_msg(headers={"Message-ID": "<m1@x>"})
    import_messages(db, [m], OWN, dry_run=False, now=0)
    assert import_messages(db, [m], OWN, dry_run=False, now=0).queued == 0


def test_dry_run_writes_nothing(db):
    import_messages(db, [make_msg()], OWN, dry_run=True, now=0, out=lambda *a: None)
    assert db.conn.execute("SELECT count(*) FROM saves").fetchone()[0] == 0


def test_missing_message_id_uses_content_hash(db):
    import_messages(db, [make_msg()], OWN, dry_run=False, now=0)
    sid = db.conn.execute("SELECT source_id FROM saves").fetchone()[0]
    assert len(sid.split(":")[0]) == 64


def test_telegram_share_of_imported_link_is_known(db):
    import_messages(db, [make_msg(headers={"Message-ID": "<m1@x>", "Date": "Sat, 14 Mar 2026 10:00:00 +0000"})],
                    OWN, dry_run=False, now=0)
    r = db.add_save(source="telegram", source_id="5:1:a", saved_at=2_000_000_000, payload="https://a.com/x",
                    priority=0, now=0)
    assert r.known_article and r.first_saved == 1773482400


def test_iter_mbox(tmp_path):
    box = mailbox.mbox(tmp_path / "m.mbox"); box.add(make_msg()); box.flush()
    assert [is_candidate(m, OWN) for m in iter_mbox(tmp_path / "m.mbox")] == [True]
