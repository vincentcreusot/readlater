import httpx
import pytest

from readlater.errors import PermanentError
from readlater.extractor.fetch import fetch_html, is_public_host

PUBLIC = lambda host: ["93.184.216.34"]


def client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False)


def html(body="<p>hi</p>", ctype="text/html; charset=utf-8", status=200):
    return httpx.Response(status, headers={"content-type": ctype}, content=body.encode())


def test_follows_redirect_and_returns_final_url():
    def h(req):
        if req.url.path == "/short":
            return httpx.Response(301, headers={"location": "/long"})
        return html()
    assert fetch_html("https://a.com/short", client=client(h), resolve=PUBLIC) == ("https://a.com/long", "<p>hi</p>")


def test_blocks_private_address():
    with pytest.raises(PermanentError, match="non-public"):
        fetch_html("https://nas.local/", client=client(lambda r: html()), resolve=lambda h: ["192.168.1.5"])


def test_blocks_private_redirect_target():
    resolve = lambda h: ["10.0.0.1"] if h == "internal" else PUBLIC(h)
    h = lambda r: httpx.Response(302, headers={"location": "http://internal/admin"})
    with pytest.raises(PermanentError, match="non-public"):
        fetch_html("https://a.com/", client=client(h), resolve=resolve)


def test_too_many_redirects():
    h = lambda r: httpx.Response(302, headers={"location": "/again"})
    with pytest.raises(PermanentError, match="redirects"):
        fetch_html("https://a.com/", client=client(h), resolve=PUBLIC)


def test_caps_body():
    _, body = fetch_html("https://a.com/", client=client(lambda r: html("x" * 100)), resolve=PUBLIC, max_bytes=10)
    assert body == "x" * 10


def test_rejects_non_html():
    with pytest.raises(PermanentError, match="not an HTML page: application/pdf"):
        fetch_html("https://a.com/f.pdf", client=client(lambda r: html(ctype="application/pdf")), resolve=PUBLIC)


def test_rejects_non_http_scheme():
    with pytest.raises(PermanentError):
        fetch_html("file:///etc/passwd", client=client(lambda r: html()), resolve=PUBLIC)


def test_404_is_permanent_500_is_retryable():
    with pytest.raises(PermanentError):
        fetch_html("https://a.com/", client=client(lambda r: html(status=404)), resolve=PUBLIC)
    with pytest.raises(httpx.HTTPStatusError):
        fetch_html("https://a.com/", client=client(lambda r: html(status=500)), resolve=PUBLIC)


def test_is_public_host_requires_every_address_global():
    assert is_public_host("a.com", resolve=PUBLIC)
    assert not is_public_host("a.com", resolve=lambda h: ["93.184.216.34", "127.0.0.1"])
