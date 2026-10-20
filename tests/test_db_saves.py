def add(db, sid, payload, at=1000, src="telegram", prio=0):
    return db.add_save(source=src, source_id=sid, saved_at=at, payload=payload, priority=prio, now=at)


def test_url_save_creates_article_and_extract_job(db):
    r = add(db, "1:1:a", "https://example.com/a?utm_source=x")
    assert r.created and not r.known_article
    art = db.get_article(r.article_id)
    assert art["canonical_url"] == "https://example.com/a"
    job = db.conn.execute("SELECT stage, priority, status FROM jobs").fetchone()
    assert tuple(job) == ("extract", 0, "pending")


def test_same_source_id_twice_is_noop(db):
    add(db, "1:1:a", "https://example.com/a")
    r = add(db, "1:1:a", "https://example.com/a")
    assert not r.created and not r.known_article
    assert db.conn.execute("SELECT count(*) FROM saves").fetchone()[0] == 1


def test_same_article_other_url_form_is_known(db):
    first = add(db, "1:1:a", "https://example.com/a", at=1000)
    db.save_summary(first.article_id, "m", {"title": "T", "tldr": "Short.", "key_points": ["a", "b", "c"],
                    "tags": [], "content_type": "news", "language": "en"}, now=1100)
    r = add(db, "1:2:a", "http://www.example.com/a/#x", at=2000)
    assert r.created and r.known_article and r.article_id == first.article_id
    assert r.first_saved == 1000 and r.tldr == "Short."
    assert db.conn.execute("SELECT count(*) FROM jobs").fetchone()[0] == 1


def test_pasted_text_goes_straight_to_summarize(db):
    text = " ".join(["word"] * 301)
    r = add(db, "1:3:a", text)
    art = db.get_article(r.article_id)
    assert art["url"] is None and art["text"] == text and art["word_count"] == 301
    assert db.conn.execute("SELECT stage FROM jobs").fetchone()[0] == "summarize"
    assert add(db, "1:4:a", text + "  ").known_article


def test_cursor_roundtrip_and_email_dedupe(db):
    assert db.get_cursor("telegram") is None
    db.set_cursor("telegram", "42"); assert db.get_cursor("telegram") == "42"
    db.add_email("<m@x>", 5, "me@x.com"); db.add_email("<m@x>", 5, "me@x.com")
    assert db.conn.execute("SELECT count(*) FROM emails").fetchone()[0] == 1


def test_reshared_short_link_is_known_without_refetching(db):
    from tests_helpers_extracted import extracted
    r = add(db, "1:1:a", "https://t.co/abc")
    db.set_extracted(r.article_id, extracted("https://blog.com/post/1"))
    again = add(db, "1:2:a", "https://t.co/abc")
    assert again.known_article and again.article_id == r.article_id
    assert db.conn.execute("SELECT count(*) FROM jobs WHERE stage='extract'").fetchone()[0] == 1


def test_short_link_of_a_merged_duplicate_is_remembered(db):
    from tests_helpers_extracted import extracted
    keep = add(db, "1:1:a", "https://blog.com/post/1").article_id
    db.set_extracted(keep, extracted("https://blog.com/post/1"))
    dup = add(db, "1:2:a", "https://bit.ly/q").article_id
    db.set_extracted(dup, extracted("https://blog.com/post/1"))
    assert add(db, "1:3:a", "https://bit.ly/q").article_id == keep
