from pathlib import Path
from unittest.mock import Mock

import httpx
import pytest

from readlater import worker
from readlater.config import load_config
from readlater.errors import PermanentError, TransientError
from readlater.extractor.extract import Extracted
from readlater.summarizer.ollama import Summary
from readlater.worker import DEFER_SECONDS, Ctx, process_one

EX = Extracted("https://a.com/x", "https://a.com/x", "T", None, None, None, "word " * 200)
SUM = Summary(title="T", tldr="Short.", key_points=["a", "b", "c"], tags=[], content_type="news", language="en")


@pytest.fixture
def ctx(db):
    return Ctx(cfg=load_config(Path("config.example.toml")), db=db, http=httpx.Client(), now=lambda: 1000)


def save(ctx, url="https://a.com/x"):
    return ctx.db.add_save(source="t", source_id=url, saved_at=0, payload=url, priority=0, now=0).article_id


def test_extract_then_summarize(ctx, monkeypatch):
    monkeypatch.setattr(worker, "extract", lambda url, client: EX)
    monkeypatch.setattr(worker, "summarize", lambda cfg, text, title, client: SUM)
    aid = save(ctx)
    assert process_one(ctx, ("extract",)) == "done"
    assert process_one(ctx, ("summarize",)) == "done"
    row = ctx.db.conn.execute("SELECT tldr, model FROM summaries WHERE article_id=?", (aid,)).fetchone()
    assert tuple(row) == ("Short.", "qwen3.5:4b")


def test_ollama_down_defers_without_attempt(ctx, monkeypatch):
    monkeypatch.setattr(worker, "summarize", Mock(side_effect=TransientError("ollama unreachable")))
    ctx.db.add_save(source="t", source_id="p", saved_at=0, payload="word " * 301, priority=0, now=0)
    assert process_one(ctx, ("summarize",)) == "deferred"
    row = ctx.db.conn.execute("SELECT status, attempts, next_try_at FROM jobs").fetchone()
    assert tuple(row) == ("pending", 0, 1000 + DEFER_SECONDS)


def test_permanent_error_fails_immediately(ctx, monkeypatch):
    monkeypatch.setattr(worker, "extract", Mock(side_effect=PermanentError("not an HTML page: application/pdf")))
    save(ctx)
    assert process_one(ctx, ("extract",)) == "failed"
    assert ctx.db.conn.execute("SELECT status FROM jobs").fetchone()[0] == "rejected"


def test_other_error_backs_off(ctx, monkeypatch):
    monkeypatch.setattr(worker, "extract", Mock(side_effect=RuntimeError("boom")))
    save(ctx)
    assert process_one(ctx, ("extract",)) == "failed"
    assert tuple(ctx.db.conn.execute("SELECT status, attempts FROM jobs").fetchone()) == ("pending", 1)


def test_merged_article_queues_nothing(ctx, monkeypatch):
    monkeypatch.setattr(worker, "extract", lambda url, client: EX)
    save(ctx, "https://a.com/x"); process_one(ctx, ("extract",))
    save(ctx, "https://t.co/1"); process_one(ctx, ("extract",))
    assert ctx.db.conn.execute("SELECT count(*) FROM jobs WHERE stage='summarize'").fetchone()[0] == 1


def test_idle(ctx):
    assert process_one(ctx, ("extract",)) == "idle"


def test_run_once_returns_when_ollama_is_down(tmp_path, monkeypatch):
    import threading
    cfg = load_config(Path("config.example.toml"))
    cfg.paths.data = tmp_path
    from readlater.store.db import DB
    d = DB(cfg.db_path); d.init_schema()
    d.add_save(source="t", source_id="p", saved_at=0, payload="word " * 301, priority=0, now=0); d.close()
    monkeypatch.setattr(worker, "summarize", Mock(side_effect=TransientError("ollama unreachable")))
    t = threading.Thread(target=worker.run, args=(cfg, threading.Event()), kwargs={"once": True}, daemon=True)
    t.start(); t.join(timeout=5)
    assert not t.is_alive()


def test_error_message_keeps_first_line(ctx, monkeypatch):
    monkeypatch.setattr(worker, "extract", Mock(side_effect=RuntimeError("Client error '403'\nFor more information check: x")))
    save(ctx)
    process_one(ctx, ("extract",))
    assert ctx.db.conn.execute("SELECT last_error FROM jobs").fetchone()[0] == "RuntimeError: Client error '403'"


def test_worker_loop_survives_errors_outside_a_stage(tmp_path, monkeypatch):
    import threading
    cfg = load_config(Path("config.example.toml"))
    cfg.paths.data = tmp_path
    from readlater.store.db import DB
    d = DB(cfg.db_path); d.init_schema(); d.close()
    results = iter([RuntimeError("database is locked"), "idle"])

    def fake(ctx, stages):
        r = next(results)
        if isinstance(r, Exception):
            raise r
        return r
    monkeypatch.setattr(worker, "process_one", fake)
    monkeypatch.setattr(worker, "IDLE_SECONDS", 0)
    worker._loop(cfg, ("extract",), httpx.Client(), threading.Event(), once=True)


def test_worker_run_creates_schema(tmp_path):
    import threading
    cfg = load_config(Path("config.example.toml")); cfg.paths.data = tmp_path
    worker.run(cfg, threading.Event(), once=True)        # must not raise "no such table"


def test_run_once_leaves_another_workers_running_job_alone(tmp_path, monkeypatch):
    import threading
    cfg = load_config(Path("config.example.toml")); cfg.paths.data = tmp_path
    from readlater.store.db import DB
    d = DB(cfg.db_path); d.init_schema()
    d.add_save(source="t", source_id="u", saved_at=0, payload="https://a.com/x", priority=0, now=0)
    d.claim(("extract",), now=0)      # a long-running worker is busy with it
    worker.run(cfg, threading.Event(), once=True)
    assert d.conn.execute("SELECT status FROM jobs").fetchone()[0] == "running"
