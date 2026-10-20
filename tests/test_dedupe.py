import pytest

from readlater.dedupe.canonicalize import canonicalize
from readlater.dedupe.content_hash import content_hash


@pytest.mark.parametrize("raw, expected", [
    ("https://Example.com/a/?utm_source=x&b=2&a=1#frag", "https://example.com/a?a=1&b=2"),
    ("http://www.example.com/a", "https://example.com/a"),
    ("https://example.com:443/a/", "https://example.com/a"),
    ("https://example.com/?fbclid=1&gclid=2&mc_eid=3&mc_cid=4&igshid=5&ref=hn&ref_src=t", "https://example.com/"),
    ("https://example.com/a?reference=7&refresh=1", "https://example.com/a?reference=7&refresh=1"),
    ("https://example.com", "https://example.com/"),
    ("https://example.com:8443/a", "https://example.com:8443/a"),
    ("  https://example.com/a  ", "https://example.com/a"),
])
def test_canonicalize(raw, expected):
    assert canonicalize(raw) == expected


def test_content_hash_ignores_whitespace():
    assert content_hash("a  b\n\tc ") == content_hash("a b c")
    assert content_hash("a b c") != content_hash("a b d")
    assert len(content_hash("x")) == 64
