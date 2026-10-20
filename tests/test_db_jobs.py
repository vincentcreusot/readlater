def mk(db, url, prio):
    return db.add_save(source="x", source_id=url, saved_at=0, payload=url, priority=prio, now=0).article_id


def test_claim_orders_by_priority_then_id(db):
    mk(db, "https://a.com/email", 10); tg = mk(db, "https://a.com/tg", 0)
    assert db.claim(("extract",), now=0).article_id == tg


def test_claim_filters_stage_and_time(db):
    mk(db, "https://a.com/1", 0)
    assert db.claim(("summarize",), now=0) is None
    job = db.claim(("extract",), now=0)
    db.fail(job.id, "boom", now=0)
    assert db.claim(("extract",), now=299) is None
    assert db.claim(("extract",), now=300).id == job.id


def test_fail_backs_off_then_parks(db):
    mk(db, "https://a.com/1", 0); t = 0
    for wait in (300, 1800, 7200):
        job = db.claim(("extract",), now=t); db.fail(job.id, "e", now=t); t += wait
    job = db.claim(("extract",), now=t); db.fail(job.id, "last", now=t)
    row = db.conn.execute("SELECT status, attempts, last_error FROM jobs").fetchone()
    assert tuple(row) == ("failed", 4, "last")


def test_permanent_fail_is_rejected_and_skipped_by_retry(db):
    mk(db, "https://a.com/1", 0)
    db.fail(db.claim(("extract",), now=0).id, "404", now=0, permanent=True)
    assert db.conn.execute("SELECT status FROM jobs").fetchone()[0] == "rejected"
    assert db.retry_failed() == 0


def test_retry_failed_requeues_exhausted_jobs(db):
    mk(db, "https://a.com/1", 0)
    for t in (0, 300, 2100, 9300):
        db.fail(db.claim(("extract",), now=t).id, "timeout", now=t)
    assert db.retry_failed() == 1 and db.claim(("extract",), now=0).attempts == 0


def test_defer_keeps_attempts(db):
    mk(db, "https://a.com/1", 0)
    job = db.claim(("extract",), now=0); db.defer(job.id, until=60, error="ollama down")
    assert db.claim(("extract",), now=59) is None and db.claim(("extract",), now=60).attempts == 0


def test_finish_enqueues_next_with_same_priority(db):
    mk(db, "https://a.com/1", 10)
    db.finish(db.claim(("extract",), now=0).id, next_stage="summarize", now=0)
    nxt = db.claim(("summarize",), now=0)
    assert nxt.stage == "summarize" and nxt.priority == 10


def test_reset_running(db):
    mk(db, "https://a.com/1", 0); db.claim(("extract",), now=0)
    assert db.reset_running() == 1 and db.claim(("extract",), now=0) is not None
