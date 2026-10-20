import dataclasses
from pathlib import Path

import pytest

from readlater.errors import PermanentError
from readlater.extractor.extract import find_canonical_link, parse_html

FIX = Path("tests/fixtures/article.html").read_text()


def test_parse_html_uses_canonical_link_and_metadata():
    ex = parse_html(FIX, "https://blog.com/p?id=1&utm_source=x")
    assert ex.canonical_url == "https://blog.com/post/1"
    assert ex.title and len(ex.text.split()) >= 150


def test_parse_html_without_text_is_permanent():
    with pytest.raises(PermanentError, match="no article text"):
        parse_html("<html><body><nav>menu</nav></body></html>", "https://a.com/")


def test_find_canonical_link_absent():
    assert find_canonical_link("<html><head></head></html>", "https://a.com/") is None


def test_set_extracted_updates_article(db):
    aid = db.add_save(source="x", source_id="1", saved_at=0, payload="https://t.co/abc", priority=0, now=0).article_id
    ex = parse_html(FIX, "https://blog.com/post/1")
    assert db.set_extracted(aid, ex) == (aid, False)
    assert db.get_article(aid)["canonical_url"] == "https://blog.com/post/1"


def test_set_extracted_merges_on_canonical_conflict(db):
    ex = parse_html(FIX, "https://blog.com/post/1")
    keep = db.add_save(source="x", source_id="1", saved_at=0, payload="https://blog.com/post/1", priority=0, now=0).article_id
    db.set_extracted(keep, ex)
    dup = db.add_save(source="x", source_id="2", saved_at=5, payload="https://t.co/abc", priority=0, now=5).article_id
    assert db.set_extracted(dup, ex) == (keep, True)
    assert db.get_article(dup) is None
    assert db.conn.execute("SELECT count(*) FROM saves WHERE article_id=?", (keep,)).fetchone()[0] == 2
    assert db.conn.execute("SELECT count(*) FROM jobs WHERE article_id=?", (dup,)).fetchone()[0] == 0


def test_set_extracted_merges_on_same_text(db):
    ex = parse_html(FIX, "https://blog.com/post/1")
    keep = db.add_save(source="x", source_id="1", saved_at=0, payload="https://blog.com/post/1", priority=0, now=0).article_id
    db.set_extracted(keep, ex)
    dup = db.add_save(source="x", source_id="2", saved_at=5, payload="https://mirror.com/copy", priority=0, now=5).article_id
    mirror = dataclasses.replace(ex, final_url="https://mirror.com/copy", canonical_url="https://mirror.com/copy")
    assert db.set_extracted(dup, mirror) == (keep, True)


def test_redirect_into_failed_article_revives_it(db):
    ex = parse_html(FIX, "https://blog.com/post/1")
    old = db.add_save(source="x", source_id="1", saved_at=0, payload="https://blog.com/post/1", priority=0, now=0).article_id
    db.fail(db.claim(("extract",), now=0).id, "site down", now=0, permanent=True)
    dup = db.add_save(source="x", source_id="2", saved_at=5, payload="https://bit.ly/q", priority=0, now=5).article_id
    assert db.set_extracted(dup, ex) == (old, True)
    assert db.get_article(old)["text"] == ex.text
    jobs = dict(db.conn.execute("SELECT stage, status FROM jobs WHERE article_id=?", (old,)).fetchall())
    assert jobs == {"extract": "done", "summarize": "pending"}


def test_same_text_on_same_site_is_not_merged(db):
    ex = parse_html(FIX, "https://news.com/story-1")
    a = db.add_save(source="x", source_id="1", saved_at=0, payload="https://news.com/story-1", priority=0, now=0).article_id
    db.set_extracted(a, dataclasses.replace(ex, final_url="https://news.com/story-1", canonical_url="https://news.com/story-1"))
    b = db.add_save(source="x", source_id="2", saved_at=0, payload="https://news.com/story-2", priority=0, now=0).article_id
    wall = dataclasses.replace(ex, final_url="https://news.com/story-2", canonical_url="https://news.com/story-2")
    assert db.set_extracted(b, wall) == (b, False)
