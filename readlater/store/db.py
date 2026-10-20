"""The SQLite store: saves, articles, summaries and the job queue in one file."""
import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from readlater.dedupe.canonicalize import canonicalize
from readlater.dedupe.content_hash import content_hash
from readlater.sources.links import is_url

SCHEMA = Path(__file__).with_name("schema.sql")


@dataclass(frozen=True)
class SaveResult:
    created: bool
    known_article: bool
    article_id: int | None
    first_saved: int | None
    tldr: str | None


class DB:
    """One connection; open a separate DB per thread or process."""

    def __init__(self, path: Path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA busy_timeout=5000")
        self.conn.execute("PRAGMA foreign_keys=ON")

    def close(self) -> None:
        self.conn.close()

    @contextmanager
    def tx(self):
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            yield self.conn
        except BaseException:
            self.conn.execute("ROLLBACK")
            raise
        self.conn.execute("COMMIT")

    def init_schema(self) -> None:
        self.conn.executescript(SCHEMA.read_text())

    # sync cursors

    def get_cursor(self, source: str) -> str | None:
        row = self.conn.execute("SELECT cursor FROM sync_state WHERE source=?", (source,)).fetchone()
        return row[0] if row else None

    def set_cursor(self, source: str, value: str) -> None:
        self.conn.execute("INSERT INTO sync_state (source, cursor) VALUES (?, ?) "
                          "ON CONFLICT(source) DO UPDATE SET cursor=excluded.cursor", (source, value))

    # saves and articles

    def add_save(self, *, source: str, source_id: str, saved_at: int, payload: str, priority: int,
                 reply_chat: int | None = None, reply_to: int | None = None, now: int) -> SaveResult:
        with self.tx() as c:
            row = c.execute("SELECT article_id FROM saves WHERE source=? AND source_id=?",
                            (source, source_id)).fetchone()
            if row:
                return SaveResult(False, False, row[0], None, None)

            if is_url(payload):
                key = canonicalize(payload)
                found = c.execute("SELECT id FROM articles WHERE canonical_url=?", (key,)).fetchone()
            else:
                text = payload.strip()
                digest = content_hash(text)
                found = c.execute("SELECT id FROM articles WHERE content_hash=?", (digest,)).fetchone()

            if found:
                article_id = found[0]
                first = c.execute("SELECT min(saved_at) FROM saves WHERE article_id=?", (article_id,)).fetchone()[0]
                tldr = c.execute("SELECT tldr FROM summaries WHERE article_id=?", (article_id,)).fetchone()
            elif is_url(payload):
                article_id = c.execute("INSERT INTO articles (url, canonical_url, created_at) VALUES (?, ?, ?)",
                                       (payload.strip(), key, now)).lastrowid
                self.enqueue(article_id, "extract", priority, now)
            else:
                article_id = c.execute(
                    "INSERT INTO articles (text, content_hash, word_count, created_at) VALUES (?, ?, ?, ?)",
                    (text, digest, len(text.split()), now)).lastrowid
                self.enqueue(article_id, "summarize", priority, now)

            c.execute("INSERT INTO saves (source, source_id, saved_at, payload, article_id, reply_chat, reply_to) "
                      "VALUES (?, ?, ?, ?, ?, ?, ?)",
                      (source, source_id, saved_at, payload, article_id, reply_chat, reply_to))
            if found:
                return SaveResult(True, True, article_id, first, tldr[0] if tldr else None)
            return SaveResult(True, False, article_id, None, None)

    def add_email(self, message_id: str, date: int, from_addr: str) -> None:
        self.conn.execute("INSERT OR IGNORE INTO emails (message_id, date, from_addr) VALUES (?, ?, ?)",
                          (message_id, date, from_addr))

    def get_article(self, article_id: int) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM articles WHERE id=?", (article_id,)).fetchone()

    def save_summary(self, article_id: int, model: str, data: dict, now: int) -> None:
        self.conn.execute(
            "INSERT INTO summaries (article_id, model, title, tldr, key_points, tags, content_type, language, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(article_id) DO UPDATE SET "
            "model=excluded.model, title=excluded.title, tldr=excluded.tldr, key_points=excluded.key_points, "
            "tags=excluded.tags, content_type=excluded.content_type, language=excluded.language, "
            "created_at=excluded.created_at",
            (article_id, model, data.get("title"), data.get("tldr"), json.dumps(data.get("key_points", [])),
             json.dumps(data.get("tags", [])), data.get("content_type"), data.get("language"), now))

    # job queue

    def enqueue(self, article_id: int, stage: str, priority: int, now: int) -> None:
        self.conn.execute("INSERT OR IGNORE INTO jobs (article_id, stage, priority, next_try_at) VALUES (?, ?, ?, ?)",
                          (article_id, stage, priority, now))
