"""Render saved articles as Markdown, for reading in a terminal or an Obsidian vault."""
import json
import re
import unicodedata
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

from readlater.store.db import DB


def _day(ts: int) -> str:
    return f"{datetime.fromtimestamp(ts):%Y-%m-%d}"


def _slug(title: str | None, article_id: int) -> str:
    ascii_title = unicodedata.normalize("NFKD", title or "").encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_title.lower()).strip("-")[:60].rstrip("-")
    return slug or f"article-{article_id}"


def _paragraphs(text: str) -> str:
    return "\n\n".join(line.strip() for line in text.splitlines() if line.strip())


def _frontmatter(art, s, saved: int) -> str:
    fields = [("title", json.dumps(_title(art, s), ensure_ascii=False)),
              ("url", art["url"]),
              ("saved", _day(saved)),
              ("site", art["site"] and json.dumps(art["site"], ensure_ascii=False)),
              ("author", art["author"] and json.dumps(art["author"], ensure_ascii=False)),
              ("published", art["published_at"] and art["published_at"][:10]),
              ("tags", s and json.dumps(json.loads(s["tags"]), ensure_ascii=False)),
              ("type", s and s["content_type"]),
              ("model", s and s["model"])]
    return "---\n" + "".join(f"{k}: {v}\n" for k, v in fields if v) + "---\n\n"


def _title(art, s) -> str:
    return art["title"] or (s and s["title"]) or art["url"] or "(untitled)"


def article_markdown(db: DB, article_id: int, *, full: bool = False) -> str | None:
    """The summary as Markdown; with full=True, YAML frontmatter on top and the article text below."""
    art = db.get_article(article_id)
    if not art:
        return None
    s = db.conn.execute("SELECT * FROM summaries WHERE article_id=?", (article_id,)).fetchone()
    saved, times = db.conn.execute("SELECT min(saved_at), count(*) FROM saves WHERE article_id=?",
                                   (article_id,)).fetchone()

    out = [_frontmatter(art, s, saved)] if full else []
    out.append(f"# {_title(art, s)}\n\n")
    host = art["url"] and (urlsplit(art["url"]).hostname or "").removeprefix("www.")
    source = [f"[{art['site'] or host or art['url']}]({art['url']})" if art["url"] else "Pasted text",
              art["author"], art["published_at"] and art["published_at"][:10]]
    out.append("**Source:** " + " · ".join(p for p in source if p) + "  \n")
    out.append(f"**Saved:** {_day(saved)}" + (f" (saved {times} times)" if times > 1 else "") + "\n\n")

    if s:
        out.append(f"## TL;DR\n\n{s['tldr']}\n\n")
        # models sometimes pack extra points into one item, separated by line breaks
        points = [line.strip() for p in json.loads(s["key_points"]) for line in p.splitlines() if line.strip()]
        out.append("## Key points\n\n" + "\n".join(f"- {p}" for p in points) + "\n\n")
        tags = json.loads(s["tags"])
        if tags:
            out.append(f"**Tags:** {', '.join(tags)}\n\n")
    else:
        for j in db.conn.execute("SELECT stage, status, last_error FROM jobs WHERE article_id=? ORDER BY id",
                                 (article_id,)):
            reason = f" — {j['last_error']}" if j["last_error"] else ""
            out.append(f"_{j['stage']}: {j['status']}{reason}_\n\n")

    if full and art["text"]:
        out.append(f"## Article\n\n{_paragraphs(art['text'])}\n")
    return "".join(out).rstrip() + "\n"


def export_markdown(db: DB, out_dir: Path) -> int:
    """Write one <saved-date>-<title>.md per summarized article; re-running overwrites the same files."""
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = db.conn.execute("""
        SELECT a.id, coalesce(a.title, sm.title) AS title, (SELECT min(saved_at) FROM saves WHERE article_id=a.id) AS saved
          FROM articles a JOIN summaries sm ON sm.article_id = a.id ORDER BY a.id""").fetchall()
    for r in rows:
        path = out_dir / f"{_day(r['saved'])}-{_slug(r['title'], r['id'])}.md"
        path.write_text(article_markdown(db, r["id"], full=True))
    return len(rows)
