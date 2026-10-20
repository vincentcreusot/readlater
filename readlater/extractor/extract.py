"""Turn a fetched page into clean article text plus metadata."""
import json
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urljoin

import httpx
import trafilatura

from readlater.dedupe.canonicalize import canonicalize
from readlater.errors import PermanentError
from readlater.extractor.fetch import Resolver, default_resolve, fetch_html

MIN_WORDS = 50


@dataclass(frozen=True)
class Extracted:
    final_url: str
    canonical_url: str
    title: str | None
    site: str | None
    author: str | None
    published_at: str | None
    text: str


class _CanonicalFinder(HTMLParser):
    def __init__(self):
        super().__init__()
        self.href: str | None = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if self.href is None and tag == "link" and "canonical" in (a.get("rel") or "").lower().split():
            self.href = a.get("href")


def find_canonical_link(html: str, base_url: str) -> str | None:
    finder = _CanonicalFinder()
    finder.feed(html)
    return urljoin(base_url, finder.href.strip()) if finder.href else None


def parse_html(html: str, url: str) -> Extracted:
    raw = trafilatura.extract(html, url=url, output_format="json", with_metadata=True)
    data = json.loads(raw) if raw else {}
    text = data.get("text") or ""
    if len(text.split()) < MIN_WORDS:
        raise PermanentError("no article text found")
    return Extracted(
        final_url=url,
        canonical_url=canonicalize(find_canonical_link(html, url) or url),
        title=data.get("title"),
        site=data.get("sitename"),
        author=data.get("author"),
        published_at=data.get("date"),
        text=text,
    )


def extract(url: str, *, client: httpx.Client, resolve: Resolver = default_resolve) -> Extracted:
    final_url, html = fetch_html(url, client=client, resolve=resolve)
    return parse_html(html, final_url)
