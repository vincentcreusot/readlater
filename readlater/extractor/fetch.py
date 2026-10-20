"""Fetch a page safely: public addresses only, bounded size, redirects re-checked on every hop."""
import codecs
import ipaddress
import re
import socket
import time
from collections.abc import Callable
from urllib.parse import urljoin, urlsplit

import httpx

from readlater.errors import PermanentError

Resolver = Callable[[str], list[str]]
USER_AGENT = "readlater/1.0 (personal archive)"
HTML_TYPES = ("text/html", "application/xhtml+xml")
GONE = (404, 410)
REFUSED = (401, 403, 451)
META_CHARSET = re.compile(rb"""<meta[^>]+charset\s*=\s*["']?([A-Za-z0-9_.:-]+)""", re.I)


def default_resolve(host: str) -> list[str]:
    return [ai[4][0] for ai in socket.getaddrinfo(host, None)]


def is_public_host(host: str, resolve: Resolver = default_resolve) -> bool:
    addrs = resolve(host)
    return bool(addrs) and all(ipaddress.ip_address(a).is_global for a in addrs)


def _peer_is_public(r: httpx.Response) -> bool:
    """The address actually connected to, which DNS rebinding could make differ from the checked one."""
    stream = r.extensions.get("network_stream")
    addr = stream.get_extra_info("server_addr") if stream else None
    return not addr or ipaddress.ip_address(addr[0]).is_global


def _decode(body: bytes, header_charset: str | None) -> str:
    sniffed = META_CHARSET.search(body[:4096])
    for name in (header_charset, sniffed and sniffed.group(1).decode("ascii"), "utf-8"):
        if name:
            try:
                return body.decode(codecs.lookup(name).name, "replace")
            except LookupError:
                continue
    return body.decode("utf-8", "replace")


def make_client(contact: str = "") -> httpx.Client:
    ua = f"{USER_AGENT[:-1]}; contact: {contact})" if contact else USER_AGENT
    return httpx.Client(timeout=10, follow_redirects=False, headers={"User-Agent": ua})


def fetch_html(url: str, *, client: httpx.Client, resolve: Resolver = default_resolve,
               max_bytes: int = 5_000_000, max_redirects: int = 5, deadline_s: float = 30,
               clock: Callable[[], float] = time.monotonic) -> tuple[str, str]:
    deadline = clock() + deadline_s
    for _ in range(max_redirects + 1):
        if clock() > deadline:
            raise TimeoutError(f"fetch took longer than {deadline_s}s: {url}")
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise PermanentError(f"unsupported URL: {url}")
        if not is_public_host(parts.hostname, resolve):
            raise PermanentError(f"blocked non-public address: {url}")
        with client.stream("GET", url) as r:
            if not _peer_is_public(r):
                raise PermanentError(f"blocked non-public address: {url}")
            if r.is_redirect:
                url = urljoin(url, r.headers["location"])
                continue
            if r.status_code in GONE:
                raise PermanentError(f"page not found ({r.status_code}): {url}")
            if r.status_code in REFUSED:
                raise PermanentError(f"site refused access ({r.status_code}): {url}")
            r.raise_for_status()
            ctype = r.headers.get("content-type", "").split(";")[0].strip().lower()
            if ctype not in HTML_TYPES:
                raise PermanentError(f"not an HTML page: {ctype or 'unknown type'}")
            body = bytearray()
            for chunk in r.iter_bytes():
                body += chunk
                if len(body) >= max_bytes:
                    break
                if clock() > deadline:
                    raise TimeoutError(f"download took longer than {deadline_s}s: {url}")
            return url, _decode(bytes(body[:max_bytes]), r.charset_encoding)
    raise PermanentError(f"too many redirects: {url}")
