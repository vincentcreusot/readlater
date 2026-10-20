"""Claims jobs from the queue and runs each stage: extract, then summarize."""
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

import httpx

from readlater.config import Config
from readlater.errors import PermanentError, TransientError
from readlater.extractor.extract import extract
from readlater.extractor.fetch import make_client
from readlater.store.db import DB
from readlater.summarizer.ollama import summarize

log = logging.getLogger(__name__)
DEFER_SECONDS = 60
IDLE_SECONDS = 5


@dataclass
class Ctx:
    cfg: Config
    db: DB
    http: httpx.Client
    now: Callable[[], int] = lambda: int(time.time())


def run_extract(ctx: Ctx, article_id: int) -> bool:
    """Returns False when the article was merged into an existing one (nothing left to summarize)."""
    article = ctx.db.get_article(article_id)
    ex = extract(article["url"], client=ctx.http)
    _, merged = ctx.db.set_extracted(article_id, ex)
    return not merged


def run_summarize(ctx: Ctx, article_id: int) -> bool:
    article = ctx.db.get_article(article_id)
    s = summarize(ctx.cfg.ollama, article["text"], article["title"], client=ctx.http)
    ctx.db.save_summary(article_id, ctx.cfg.ollama.model, s.model_dump(), ctx.now())
    return True


def _first_line(e: Exception) -> str:
    return (str(e).splitlines() or [""])[0]


STAGES = {"extract": run_extract, "summarize": run_summarize}
NEXT = {"extract": "summarize", "summarize": None}


def process_one(ctx: Ctx, stages: tuple[str, ...]) -> str:
    """Run one job. Returns 'idle', 'done', 'deferred' or 'failed'."""
    job = ctx.db.claim(stages, ctx.now())
    if not job:
        return "idle"
    try:
        go_on = STAGES[job.stage](ctx, job.article_id)
    except TransientError as e:
        log.warning("job %s (%s) deferred: %s", job.id, job.stage, e)
        ctx.db.defer(job.id, ctx.now() + DEFER_SECONDS, _first_line(e))
        return "deferred"
    except PermanentError as e:
        log.warning("job %s (%s) failed: %s", job.id, job.stage, e)
        ctx.db.fail(job.id, _first_line(e), ctx.now(), permanent=True)
        return "failed"
    except Exception as e:
        log.exception("job %s (%s) error", job.id, job.stage)
        ctx.db.fail(job.id, f"{type(e).__name__}: {_first_line(e)}", ctx.now())
        return "failed"
    ctx.db.finish(job.id, NEXT[job.stage] if go_on else None, ctx.now())
    log.info("job %s (%s) done for article %s", job.id, job.stage, job.article_id)
    return "done"


def _loop(cfg: Config, stages: tuple[str, ...], http: httpx.Client, stop: threading.Event, once: bool) -> None:
    db = DB(cfg.db_path)
    ctx = Ctx(cfg=cfg, db=db, http=http)
    try:
        while not stop.is_set():
            try:
                result = process_one(ctx, stages)
            except Exception:   # e.g. "database is locked"; never let the loop die silently
                log.exception("%s loop error", stages[0])
                stop.wait(IDLE_SECONDS)
                continue
            if result == "idle":
                if once:
                    return
                stop.wait(IDLE_SECONDS)
            elif result == "deferred":
                if once:   # a dependency is down; don't spin until it returns
                    log.warning("stopping: %s is unavailable, jobs stay queued", stages[0])
                    return
                stop.wait(DEFER_SECONDS)
    finally:
        db.close()
        http.close()


def run(cfg: Config, stop: threading.Event, once: bool = False) -> None:
    db = DB(cfg.db_path)
    db.init_schema()
    if not once:   # a one-off run must not steal jobs a long-running worker is processing
        db.reset_running()
    db.close()
    if once:
        # extract everything first so the summarize pass sees all of it
        _loop(cfg, ("extract",), make_client(cfg.fetch.contact), stop, once=True)
        _loop(cfg, ("summarize",), httpx.Client(), stop, once=True)
        return
    threads = [threading.Thread(target=_loop, args=(cfg, ("extract",), make_client(cfg.fetch.contact), stop, False), daemon=True),
               threading.Thread(target=_loop, args=(cfg, ("summarize",), httpx.Client(), stop, False), daemon=True)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
