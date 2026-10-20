from datetime import datetime

from readlater.export import article_markdown, export_markdown

SAVED = 1_773_482_400   # 2026-03-14 10:00 UTC
TEXT = "First paragraph of the article.\nSecond paragraph."


def make_article(db, url="https://blog.com/post/1", title='Small "models": why', summarize=True):
    aid = db.add_save(source="cli", source_id=url, saved_at=SAVED, payload=url, priority=0, now=SAVED).article_id
    db.conn.execute("UPDATE articles SET title=?, site='Blog', author='Jane', published_at='2026-01-02', text=? "
                    "WHERE id=?", (title, TEXT, aid))
    if summarize:
        db.save_summary(aid, "qwen3.5:4b", {"title": "S", "tldr": "Short. Sweet.", "key_points": ["a", "b", "c"],
                        "tags": ["llm", "cpu"], "content_type": "research", "language": "en"}, now=SAVED)
    return aid




def test_show_markdown_has_heading_tldr_and_points(db):
    md = article_markdown(db, make_article(db))
    assert md.startswith('# Small "models": why\n')
    assert "**Source:** [Blog](https://blog.com/post/1) · Jane · 2026-01-02" in md
    assert "## TL;DR\n\nShort. Sweet." in md
    assert "## Key points\n\n- a\n- b\n- c" in md
    assert "**Tags:** llm, cpu" in md
    assert "## Article" not in md and not md.startswith("---")


def test_full_markdown_has_frontmatter_and_text(db):
    md = article_markdown(db, make_article(db), full=True)
    assert md.startswith('---\ntitle: "Small \\"models\\": why"\nurl: https://blog.com/post/1\n')
    assert '\ntags: ["llm", "cpu"]\n' in md and "\nmodel: qwen3.5:4b\n" in md
    assert md.rstrip().endswith("## Article\n\nFirst paragraph of the article.\n\nSecond paragraph.")


def test_unsummarized_article_shows_job_status(db):
    aid = make_article(db, summarize=False)
    db.fail(db.claim(("extract",), now=SAVED).id, "not an HTML page: application/pdf", now=SAVED, permanent=True)
    assert "_extract: failed — not an HTML page: application/pdf_" in article_markdown(db, aid)


def test_missing_article_is_none(db):
    assert article_markdown(db, 99) is None


def test_export_writes_one_file_per_summarized_article(db, tmp_path):
    tmp_path = tmp_path / "vault"
    make_article(db)
    make_article(db, url="https://blog.com/other", title="Other", summarize=False)
    assert export_markdown(db, tmp_path) == 1
    day = f"{datetime.fromtimestamp(SAVED):%Y-%m-%d}"
    files = sorted(p.name for p in tmp_path.iterdir())
    assert files == [f"{day}-small-models-why.md"]
    assert "## Article" in (tmp_path / files[0]).read_text()
    assert export_markdown(db, tmp_path) == 1 and len(list(tmp_path.iterdir())) == 1


def test_points_with_embedded_lines_become_separate_bullets(db):
    aid = make_article(db)
    db.save_summary(aid, "m", {"title": "S", "tldr": "T.", "key_points": ["a", "b", "c\n\nd\ne"], "tags": [],
                    "content_type": "news", "language": "en"}, now=SAVED)
    assert "## Key points\n\n- a\n- b\n- c\n- d\n- e\n" in article_markdown(db, aid)


def test_source_link_falls_back_to_domain(db):
    aid = make_article(db, url="https://www.example.org/x")
    db.conn.execute("UPDATE articles SET site=NULL, author=NULL WHERE id=?", (aid,))
    assert "**Source:** [example.org](https://www.example.org/x) · 2026-01-02" in article_markdown(db, aid)
