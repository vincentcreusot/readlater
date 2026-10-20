"""Command-line entry point: readlater <command>."""
import argparse
import json
import logging
import signal
import sys
import threading
import time

from readlater.config import load_config
from readlater.sources.links import item_key
from readlater.store.db import DB


def _db(cfg) -> DB:
    return DB(cfg.db_path)


def cmd_init_db(cfg, args) -> int:
    db = _db(cfg)
    db.init_schema()
    print(f"database ready at {cfg.db_path}")
    return 0


def cmd_add(cfg, args) -> int:
    now = int(time.time())
    r = _db(cfg).add_save(source="cli", source_id=item_key(args.item), saved_at=now, payload=args.item,
                          priority=0, now=now)
    if r.created and not r.known_article:
        print(f"queued article {r.article_id}")
    else:
        print(f"already saved as article {r.article_id}")
    return 0


def cmd_worker(cfg, args) -> int:
    from readlater import worker
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    try:
        worker.run(cfg, stop, once=args.once)
    except KeyboardInterrupt:
        stop.set()
    return 0


def cmd_show(cfg, args) -> int:
    db = _db(cfg)
    art = db.get_article(args.article_id)
    if not art:
        print(f"no article {args.article_id}", file=sys.stderr)
        return 1
    s = db.conn.execute("SELECT * FROM summaries WHERE article_id=?", (art["id"],)).fetchone()
    print(s["title"] if s and not art["title"] else art["title"] or "(untitled)")
    print(art["url"] or "(pasted text)")
    if not s:
        jobs = db.conn.execute("SELECT stage, status, last_error FROM jobs WHERE article_id=?", (art["id"],))
        for j in jobs:
            print(f"[{j['stage']}: {j['status']}{' — ' + j['last_error'] if j['last_error'] else ''}]")
        return 0
    print()
    print(s["tldr"])
    print()
    for p in json.loads(s["key_points"]):
        print(f"• {p}")
    return 0


def cmd_retry(cfg, args) -> int:
    print(f"re-queued {_db(cfg).retry_failed()} failed jobs")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="readlater")
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("init-db", help="create the database").set_defaults(fn=cmd_init_db)
    a = sub.add_parser("add", help="save a URL or pasted text")
    a.add_argument("item")
    a.set_defaults(fn=cmd_add)
    w = sub.add_parser("worker", help="run the extract and summarize queue")
    w.add_argument("--once", action="store_true", help="exit when the queue is empty")
    w.set_defaults(fn=cmd_worker)
    s = sub.add_parser("show", help="print an article's summary")
    s.add_argument("article_id", type=int)
    s.set_defaults(fn=cmd_show)
    sub.add_parser("retry", help="re-queue failed jobs").set_defaults(fn=cmd_retry)
    return p


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    args = build_parser().parse_args(argv)
    return args.fn(load_config(), args)


if __name__ == "__main__":
    sys.exit(main())
