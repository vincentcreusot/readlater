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


BACKOFF_SECONDS = (300, 1800, 7200)
MAX_ATTEMPTS = 4


@dataclass(frozen=True)
class Job:
    id: int
    article_id: int
    stage: str
    priority: int
    attempts: int


@dataclass(frozen=True)
class Reply:
    save_id: int
    chat_id: int
    reply_to: int
    status: str            # done | failed
    title: str | None
    tldr: str | None
    key_points: list[str]
    url: str | None
    error: str | None


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

    def set_extracted(self, article_id: int, ex) -> tuple[int, bool]:
        """Store extraction results, or merge into an existing article with the same URL or text.

        Returns (surviving article id, merged).
        """
        digest = content_hash(ex.text)
        with self.tx() as c:
            other = c.execute("SELECT id FROM articles WHERE id != ? AND (canonical_url=? OR content_hash=?) "
                              "ORDER BY id LIMIT 1", (article_id, ex.canonical_url, digest)).fetchone()
            if other:
                c.execute("UPDATE saves SET article_id=? WHERE article_id=?", (other[0], article_id))
                c.execute("DELETE FROM jobs WHERE article_id=?", (article_id,))
                c.execute("DELETE FROM summaries WHERE article_id=?", (article_id,))
                c.execute("DELETE FROM articles WHERE id=?", (article_id,))
                return other[0], True
            c.execute("UPDATE articles SET url=?, canonical_url=?, title=?, site=?, author=?, published_at=?, "
                      "text=?, content_hash=?, word_count=? WHERE id=?",
                      (ex.final_url, ex.canonical_url, ex.title, ex.site, ex.author, ex.published_at,
                       ex.text, digest, len(ex.text.split()), article_id))
            return article_id, False

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

    def claim(self, stages: tuple[str, ...], now: int) -> Job | None:
        marks = ",".join("?" * len(stages))
        row = self.conn.execute(
            f"""UPDATE jobs SET status='running'
                 WHERE id = (SELECT id FROM jobs
                              WHERE status='pending' AND next_try_at <= ? AND stage IN ({marks})
                              ORDER BY priority, id LIMIT 1)
             RETURNING id, article_id, stage, priority, attempts""",
            (now, *stages)).fetchone()
        return Job(*row) if row else None

    def finish(self, job_id: int, next_stage: str | None, now: int) -> None:
        with self.tx() as c:
            row = c.execute("UPDATE jobs SET status='done', last_error=NULL WHERE id=? "
                            "RETURNING article_id, priority", (job_id,)).fetchone()
            if row and next_stage:   # row is gone when the article was merged away
                self.enqueue(row[0], next_stage, row[1], now)

    def fail(self, job_id: int, error: str, now: int, permanent: bool = False) -> None:
        with self.tx() as c:
            attempts = c.execute("UPDATE jobs SET attempts=attempts+1, last_error=? WHERE id=? RETURNING attempts",
                                 (error, job_id)).fetchone()[0]
            if permanent or attempts >= MAX_ATTEMPTS:
                c.execute("UPDATE jobs SET status='failed' WHERE id=?", (job_id,))
            else:
                c.execute("UPDATE jobs SET status='pending', next_try_at=? WHERE id=?",
                          (now + BACKOFF_SECONDS[attempts - 1], job_id))

    def defer(self, job_id: int, until: int, error: str) -> None:
        self.conn.execute("UPDATE jobs SET status='pending', next_try_at=?, last_error=? WHERE id=?",
                          (until, error, job_id))

    def reset_running(self) -> int:
        return self.conn.execute("UPDATE jobs SET status='pending' WHERE status='running'").rowcount

    def retry_failed(self) -> int:
        with self.tx() as c:
            # saves already told "couldn't summarize" get a reply again once the retry succeeds
            c.execute("UPDATE saves SET replied_at=NULL WHERE reply_chat IS NOT NULL AND article_id IN "
                      "(SELECT article_id FROM jobs WHERE status='failed')")
            return c.execute("UPDATE jobs SET status='pending', attempts=0, next_try_at=0 "
                             "WHERE status='failed'").rowcount

    # Telegram replies

    def pending_replies(self) -> list[Reply]:
        rows = self.conn.execute("""
            SELECT s.id, s.reply_chat, s.reply_to, a.url, coalesce(a.title, sm.title) AS title,
                   sm.tldr, sm.key_points,
                   (SELECT last_error FROM jobs j WHERE j.article_id=a.id AND j.status='failed' LIMIT 1) AS error
              FROM saves s JOIN articles a ON a.id = s.article_id
              LEFT JOIN summaries sm ON sm.article_id = a.id
             WHERE s.reply_chat IS NOT NULL AND s.replied_at IS NULL
               AND (sm.article_id IS NOT NULL
                    OR EXISTS (SELECT 1 FROM jobs j WHERE j.article_id=a.id AND j.status='failed'))
             ORDER BY s.id""").fetchall()
        return [Reply(save_id=r["id"], chat_id=r["reply_chat"], reply_to=r["reply_to"],
                      status="done" if r["tldr"] is not None else "failed", title=r["title"], tldr=r["tldr"],
                      key_points=json.loads(r["key_points"]) if r["key_points"] else [],
                      url=r["url"], error=r["error"]) for r in rows]

    def mark_replied(self, save_id: int, now: int) -> None:
        self.conn.execute("UPDATE saves SET replied_at=? WHERE id=?", (now, save_id))

    def recent(self, limit: int = 10) -> list[sqlite3.Row]:
        return self.conn.execute("""
            SELECT s.saved_at, coalesce(a.title, sm.title, s.payload) AS title
              FROM saves s JOIN articles a ON a.id = s.article_id
              LEFT JOIN summaries sm ON sm.article_id = a.id
             ORDER BY s.saved_at DESC, s.id DESC LIMIT ?""", (limit,)).fetchall()
