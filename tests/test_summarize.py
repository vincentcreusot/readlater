import json

import httpx
import pydantic
import pytest

from readlater.config import OllamaCfg
from readlater.errors import TransientError
from readlater.summarizer.ollama import (CHUNK_CHARS, COMBINE_SYSTEM, MAX_CHUNKS, MAX_INPUT_CHARS, SYSTEM,
                                        Summary, summarize)

CFG = OllamaCfg(url="http://ollama:11434")
OK = {"title": "T", "tldr": "One. Two.", "key_points": ["a", "b", "c"], "tags": ["x"],
      "content_type": "news", "language": "en"}


def run(handler, text="body", title="T"):
    return summarize(CFG, text, title, client=httpx.Client(transport=httpx.MockTransport(handler)))


def test_request_shape():
    seen = {}

    def h(req):
        seen.update(json.loads(req.content))
        return httpx.Response(200, json={"message": {"content": json.dumps(OK)}})
    assert run(h).tldr == "One. Two."
    assert seen["model"] == "qwen3.5:4b" and seen["stream"] is False and seen["think"] is False
    assert seen["format"] == Summary.model_json_schema()
    assert seen["options"] == {"temperature": 0.2, "num_ctx": 16384}
    assert seen["messages"][0] == {"role": "system", "content": SYSTEM}
    assert seen["messages"][1]["content"] == "Title: T\n<article>\nbody\n</article>"


def chat_log(responses=None):
    """A handler that records each request's messages and answers with numbered summaries."""
    calls = []

    def h(req):
        body = json.loads(req.content)
        calls.append(body["messages"])
        return httpx.Response(200, json={"message": {"content": json.dumps(dict(OK, tldr=f"Part {len(calls)}."))}})
    return calls, h


def test_short_text_is_one_call():
    calls, h = chat_log()
    run(h, text="x" * MAX_INPUT_CHARS)
    assert len(calls) == 1


def test_long_text_is_summarized_in_parts_then_combined():
    para = "word " * 1000                      # ~5,000 chars per paragraph
    text = "\n".join([para] * 20)              # ~100,000 chars -> 3 parts
    calls, h = chat_log()
    result = run(h, text=text, title="Long")
    parts, final = calls[:-1], calls[-1]
    assert len(parts) == 3
    for i, msgs in enumerate(parts, 1):
        user = msgs[1]["content"]
        assert user.startswith(f"Title: Long (part {i} of 3)\n<article>\n")
        body = user.split("<article>\n", 1)[1].rsplit("\n</article>", 1)[0]
        assert len(body) <= CHUNK_CHARS and not body.startswith(" ")   # split on paragraph boundaries
    assert final[0]["content"] == COMBINE_SYSTEM
    assert "Part 1." in final[1]["content"] and "Part 3." in final[1]["content"]
    assert result.tldr == "Part 4."


def test_very_long_text_stops_at_max_parts():
    text = "\n".join(["word " * 1000] * 200)    # ~1,000,000 chars
    calls, h = chat_log()
    run(h, text=text)
    assert len(calls) == MAX_CHUNKS + 1


def test_unbreakable_text_is_hard_split():
    calls, h = chat_log()
    run(h, text="x" * (CHUNK_CHARS * 2 + 10))
    assert len(calls) == 4


def test_connect_error_is_transient():
    def h(req):
        raise httpx.ConnectError("refused")
    with pytest.raises(TransientError, match="unreachable"):
        run(h)


def test_model_not_found_is_transient():
    with pytest.raises(TransientError, match="model not found"):
        run(lambda r: httpx.Response(404, json={"error": "model not found"}))


def test_bad_output_is_validation_error():
    bad = dict(OK, key_points=["only one"])
    with pytest.raises(pydantic.ValidationError):
        run(lambda r: httpx.Response(200, json={"message": {"content": json.dumps(bad)}}))


def test_ollama_busy_is_transient():
    with pytest.raises(TransientError, match="busy"):
        run(lambda r: httpx.Response(503, json={"error": "server busy"}))


def test_connect_timeout_is_short_read_timeout_long():
    seen = {}

    def h(req):
        seen.update(req.extensions["timeout"])
        return httpx.Response(200, json={"message": {"content": json.dumps(OK)}})
    run(h)
    assert seen["connect"] == 10 and seen["read"] == 900


def test_content_type_is_one_of_the_five():
    schema = Summary.model_json_schema()
    assert schema["properties"]["content_type"]["enum"] == ["news", "how-to", "opinion", "research", "other"]
