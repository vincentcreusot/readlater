import json

import httpx
import pydantic
import pytest

from readlater.config import OllamaCfg
from readlater.errors import TransientError
from readlater.summarizer.ollama import MAX_INPUT_CHARS, SYSTEM, Summary, summarize

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


def test_truncates_long_text():
    seen = {}

    def h(req):
        seen["c"] = json.loads(req.content)["messages"][1]["content"]
        return httpx.Response(200, json={"message": {"content": json.dumps(OK)}})
    run(h, text="x" * 60_000, title=None)
    assert seen["c"] == "<article>\n" + "x" * MAX_INPUT_CHARS + "\n</article>"


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
