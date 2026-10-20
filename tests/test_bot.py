from pathlib import Path

import pytest

from readlater.config import load_config
from readlater.sources import telegram_bot
from readlater.sources.telegram_bot import deliver_replies, handle_message, process_updates

ME = 123456789


class FakeAPI:
    def __init__(self):
        self.sent = []

    def reply(self, chat_id, text, reply_to=None, html=False):
        self.sent.append((chat_id, text, reply_to))


@pytest.fixture
def api():
    return FakeAPI()


@pytest.fixture
def cfg():
    return load_config(Path("config.example.toml"))


def msg(text, uid=ME, mid=1, entities=()):
    return {"message_id": mid, "date": 1_700_000_000, "chat": {"id": 5}, "from": {"id": uid},
            "text": text, "entities": list(entities)}


def test_ignores_other_users(api, db, cfg):
    process_updates(api, db, cfg, [{"update_id": 1, "message": msg("https://a.com", uid=999)}], now=0)
    assert api.sent == [] and db.conn.execute("SELECT count(*) FROM saves").fetchone()[0] == 0


def test_link_is_saved_and_acknowledged(api, db, cfg):
    assert process_updates(api, db, cfg, [{"update_id": 7, "message": msg("https://a.com/x")}], now=0) == 8
    assert api.sent == [(5, "Saved. Summary in a few minutes.", 1)] and db.get_cursor("telegram") == "8"


def test_known_link_says_already_saved(api, db, cfg):
    handle_message(api, db, cfg, msg("https://a.com/x", mid=1), now=0)
    handle_message(api, db, cfg, msg("https://a.com/x?utm_source=t", mid=2), now=0)
    assert api.sent[-1][1].startswith("Already saved on ") and "Summary still in progress." in api.sent[-1][1]


def test_text_link_entity(api, db, cfg):
    handle_message(api, db, cfg, msg("this", entities=[{"type": "text_link", "url": "https://b.com/y"}]), now=0)
    assert db.conn.execute("SELECT payload FROM saves").fetchone()[0] == "https://b.com/y"


def test_no_items_gets_help(api, db, cfg):
    handle_message(api, db, cfg, msg("hello"), now=0)
    assert api.sent == [(5, "Send me a link, or paste an article.", 1)]


def test_bad_update_does_not_block_the_next(api, db, cfg, monkeypatch):
    real = telegram_bot.handle_message

    def flaky(api, db, cfg, m, now):
        if m["message_id"] == 1:
            raise RuntimeError("boom")
        return real(api, db, cfg, m, now)
    monkeypatch.setattr(telegram_bot, "handle_message", flaky)
    ups = [{"update_id": 1, "message": msg("https://a.com", mid=1)},
           {"update_id": 2, "message": msg("https://b.com", mid=2)}]
    assert process_updates(api, db, cfg, ups, now=0) == 3
    assert api.sent[0] == (5, "Sorry, I couldn't save that. Please send it again.", 1) and len(api.sent) == 2


def test_deliver_summary_once(api, db, cfg):
    handle_message(api, db, cfg, msg("https://a.com/x"), now=0)
    aid = db.conn.execute("SELECT article_id FROM saves").fetchone()[0]
    db.save_summary(aid, "m", {"title": "T", "tldr": "Short.", "key_points": ["a", "b", "c"], "tags": [],
                    "content_type": "news", "language": "en"}, now=1)
    assert deliver_replies(api, db, now=2) == 1 and "• a" in api.sent[-1][1]
    assert deliver_replies(api, db, now=3) == 0


def test_deliver_failed_job_tells_user(api, db, cfg):
    handle_message(api, db, cfg, msg("https://a.com/f.pdf"), now=0)
    job = db.claim(("extract",), now=0)
    db.fail(job.id, "not an HTML page: application/pdf", now=0, permanent=True)
    deliver_replies(api, db, now=1)
    assert api.sent[-1][1] == ("Couldn't summarize https://a.com/f.pdf: not an HTML page: application/pdf\n"
                               "Share the link again to retry.")


def test_deliver_exhausted_job_suggests_retry(api, db, cfg):
    handle_message(api, db, cfg, msg("https://a.com/x"), now=0)
    for t in (0, 300, 2100, 9300):
        db.fail(db.claim(("extract",), now=t).id, "ReadTimeout: timed out", now=t)
    deliver_replies(api, db, now=10_000)
    assert api.sent[-1][1].endswith("ReadTimeout: timed out\nSend /retry to try again.")


def test_commands(api, db, cfg):
    handle_message(api, db, cfg, msg("https://a.com/x", mid=1), now=0)
    handle_message(api, db, cfg, msg("/recent", mid=2), now=0)
    assert "https://a.com/x" in api.sent[-1][1]
    handle_message(api, db, cfg, msg("/retry", mid=3), now=0)
    assert api.sent[-1][1] == "Re-queued 0 failed jobs."
    handle_message(api, db, cfg, msg("/start", mid=4), now=0)
    assert api.sent[-1][1].startswith("Share a link or paste an article")


def _summarize(db, aid):
    db.save_summary(aid, "m", {"title": "T", "tldr": "Short.", "key_points": ["a", "b", "c"], "tags": [],
                    "content_type": "news", "language": "en"}, now=1)


def test_already_saved_with_tldr_is_not_sent_again(api, db, cfg):
    handle_message(api, db, cfg, msg("https://a.com/x", mid=1), now=0)
    _summarize(db, db.conn.execute("SELECT article_id FROM saves").fetchone()[0])
    deliver_replies(api, db, now=2)
    handle_message(api, db, cfg, msg("https://a.com/x", mid=2), now=3)
    assert "Short." in api.sent[-1][1]
    assert deliver_replies(api, db, now=4) == 0


def test_retry_replies_again_when_it_succeeds(api, db, cfg):
    handle_message(api, db, cfg, msg("https://a.com/x"), now=0)
    for t in (0, 300, 2100, 9300):
        db.fail(db.claim(("extract",), now=t).id, "timeout", now=t)
    deliver_replies(api, db, now=1)
    handle_message(api, db, cfg, msg("/retry", mid=2), now=2)
    job = db.claim(("extract",), now=2); db.finish(job.id, None, now=2)
    _summarize(db, job.article_id)
    assert deliver_replies(api, db, now=3) == 1 and "Short." in api.sent[-1][1]


def test_redact_hides_token():
    import httpx
    api = telegram_bot.TelegramAPI("123:SECRET", httpx.Client())
    assert api.redact("POST https://api.telegram.org/bot123:SECRET/getUpdates") == \
        "POST https://api.telegram.org/bot***/getUpdates"


def test_cli_silences_httpx_request_logging():
    import logging
    from readlater.cli import setup_logging
    setup_logging()
    assert logging.getLogger("httpx").getEffectiveLevel() >= logging.WARNING


def test_api_errors_do_not_leak_token():
    import httpx
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    api = telegram_bot.TelegramAPI("123:SECRET", client)
    with pytest.raises(telegram_bot.TelegramError) as e:
        api.call("getUpdates")
    assert "SECRET" not in str(e.value) and e.value.__cause__ is None and e.value.__suppress_context__


def test_resharing_failed_link_retries_it(api, db, cfg):
    handle_message(api, db, cfg, msg("https://a.com/x", mid=1), now=0)
    db.fail(db.claim(("extract",), now=0).id, "site down", now=0, permanent=True)
    deliver_replies(api, db, now=1)
    handle_message(api, db, cfg, msg("https://a.com/x", mid=2), now=2)
    assert api.sent[-1][1].startswith("Already saved on ") and "retrying" in api.sent[-1][1]
    assert db.claim(("extract",), now=2) is not None
    assert deliver_replies(api, db, now=3) == 0


class FlakyAPI(FakeAPI):
    def __init__(self, bad_reply_to):
        super().__init__()
        self.bad = bad_reply_to

    def reply(self, chat_id, text, reply_to=None, html=False):
        if reply_to == self.bad:
            raise telegram_bot.TelegramError("Bad Request: message to be replied not found", status=400)
        super().reply(chat_id, text, reply_to, html)


def test_one_undeliverable_reply_does_not_block_others(db, cfg):
    api = FlakyAPI(bad_reply_to=1)
    for mid, url in ((1, "https://a.com/1"), (2, "https://a.com/2")):
        handle_message(FakeAPI(), db, cfg, msg(url, mid=mid), now=0)
        _summarize(db, db.conn.execute("SELECT article_id FROM saves WHERE reply_to=?", (mid,)).fetchone()[0])
    assert deliver_replies(api, db, now=1) == 1
    assert deliver_replies(api, db, now=2) == 0   # the 4xx one is dropped, not retried forever


def test_reply_allows_missing_original_and_fits_utf16_limit():
    import httpx, json as _json
    seen = {}

    def h(req):
        seen.update(_json.loads(req.content))
        return httpx.Response(200, json={"ok": True, "result": {}})
    api = telegram_bot.TelegramAPI("1:T", httpx.Client(transport=httpx.MockTransport(h)))
    api.reply(5, "😀" * 3000, reply_to=9)
    assert seen["reply_parameters"] == {"message_id": 9, "allow_sending_without_reply": True}
    assert len(seen["text"].encode("utf-16-le")) // 2 <= 4000


def test_bot_loop_survives_unexpected_errors(db, cfg):
    import threading
    stop = threading.Event()
    calls = []

    class BoomAPI(FakeAPI):
        def call(self, method, **params):
            calls.append(method)
            if len(calls) == 1:
                raise RuntimeError("database is locked")
            stop.set()
            return []
    telegram_bot.run(cfg, db, BoomAPI(), stop, error_wait=0)
    assert len(calls) == 2


def _reply(**kw):
    from readlater.store.db import Reply
    base = dict(save_id=1, chat_id=5, reply_to=1, status="done", title="A <b>bold</b> & title", tldr="Short & sweet.",
                key_points=["one", "two < three", "four\n\nfive"], url="https://www.example.org/x?a=1&b=2", error=None)
    return Reply(**(base | kw))


def test_format_summary_is_escaped_telegram_html():
    text = telegram_bot.format_summary(_reply())
    assert text.startswith("<b>A &lt;b&gt;bold&lt;/b&gt; &amp; title</b>\n\nShort &amp; sweet.\n\n")
    assert "• one\n• two &lt; three\n• four\n• five" in text
    assert text.endswith('<a href="https://www.example.org/x?a=1&amp;b=2">example.org</a>')


def test_format_summary_stays_within_limit_without_cutting_tags():
    text = telegram_bot.format_summary(_reply(key_points=["p" * 900] * 7, tldr="t" * 900))
    assert len(text.encode("utf-16-le")) // 2 <= 4000
    assert text.count("<b>") == text.count("</b>") == 1 and text.endswith("</a>")


def test_summaries_are_sent_as_html(db, cfg):
    sent = []

    class HtmlAPI(FakeAPI):
        def reply(self, chat_id, text, reply_to=None, html=False):
            sent.append(html)
    handle_message(FakeAPI(), db, cfg, msg("https://a.com/x"), now=0)
    _summarize(db, db.conn.execute("SELECT article_id FROM saves").fetchone()[0])
    deliver_replies(HtmlAPI(), db, now=1)
    assert sent == [True]


def test_api_reply_sets_parse_mode_for_html():
    import httpx, json as _json
    seen = []
    api = telegram_bot.TelegramAPI("1:T", httpx.Client(transport=httpx.MockTransport(
        lambda r: (seen.append(_json.loads(r.content)), httpx.Response(200, json={"ok": True, "result": {}}))[1])))
    api.reply(5, "<b>x</b>", html=True)
    api.reply(5, "plain")
    assert seen[0]["parse_mode"] == "HTML" and "parse_mode" not in seen[1]


def test_format_summary_huge_title_still_fits():
    text = telegram_bot.format_summary(_reply(title="T" * 5000, tldr="x" * 3000))
    assert len(text.encode("utf-16-le")) // 2 <= 4000 and text.endswith("</a>")
