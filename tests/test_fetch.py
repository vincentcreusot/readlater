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


def test_user_agent_includes_optional_contact():
    from readlater.extractor.fetch import make_client
    assert make_client().headers["user-agent"] == "readlater/1.0 (personal archive)"
    assert make_client("me@example.com").headers["user-agent"] == "readlater/1.0 (personal archive; contact: me@example.com)"


class FakeStream:
    def __init__(self, ip):
        self.ip = ip

    def get_extra_info(self, name):
        return (self.ip, 443) if name == "server_addr" else None


def test_connected_address_must_be_public():
    """DNS can answer differently on the second lookup (rebinding): check the address actually used."""
    h = lambda r: httpx.Response(200, headers={"content-type": "text/html"}, content=b"<p>x</p>",
                                 extensions={"network_stream": FakeStream("10.0.0.5")})
    with pytest.raises(PermanentError, match="non-public"):
        fetch_html("https://a.com/", client=client(h), resolve=PUBLIC)


def test_deadline_is_checked_between_redirects():
    calls, now = [], iter([0, 0, 31, 31, 31])
    def h(req):
        calls.append(req.url.path)
        return httpx.Response(302, headers={"location": f"/{len(calls)}"})
    with pytest.raises(TimeoutError):
        fetch_html("https://a.com/", client=client(h), resolve=PUBLIC, clock=lambda: next(now))
    assert len(calls) == 1


@pytest.mark.parametrize("status", [401, 403, 451])
def test_refused_access_is_permanent(status):
    with pytest.raises(PermanentError, match=f"refused access \\({status}\\)"):
        fetch_html("https://a.com/", client=client(lambda r: html(status=status)), resolve=PUBLIC)


def test_meta_charset_is_used_when_the_header_has_none():
    body = '<html><head><meta charset="iso-8859-1"></head><body>café</body></html>'.encode("latin-1")
    h = lambda r: httpx.Response(200, headers={"content-type": "text/html"}, content=body)
    assert "café" in fetch_html("https://a.com/", client=client(h), resolve=PUBLIC)[1]


def test_unknown_charset_falls_back_to_utf8():
    h = lambda r: httpx.Response(200, headers={"content-type": "text/html; charset=x-bogus"}, content="é".encode())
    assert fetch_html("https://a.com/", client=client(h), resolve=PUBLIC)[1] == "é"
