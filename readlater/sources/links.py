"""Find what a message saves: the links in it, or the whole text if it is a pasted article."""
import hashlib
import re
from collections.abc import Iterable

URL_RE = re.compile(r"https?://[^\s<>\"']+")
TRAILING_PUNCT = ".,;:!?…。、，．！？»›”’」』"   # sentence punctuation and closing quotes, incl. French and CJK
CLOSERS = {")": "(", "]": "[", "）": "（", "】": "【"}
PASTED_MIN_WORDS = 300


def _clean(url: str) -> str:
    while url:
        last = url[-1]
        if last in TRAILING_PUNCT:
            url = url[:-1]
        elif last in CLOSERS and url.count(CLOSERS[last]) < url.count(last):
            url = url[:-1]
        else:
            break
    return url


def find_items(text: str, extra_urls: Iterable[str] = ()) -> list[str]:
    urls = [_clean(u) for u in URL_RE.findall(text)] + list(extra_urls)
    urls = list(dict.fromkeys(u for u in urls if u))
    if urls:
        return urls
    if len(text.split()) > PASTED_MIN_WORDS:
        return [text.strip()]
    return []


def is_url(item: str) -> bool:
    return item.startswith(("http://", "https://"))


def item_key(item: str) -> str:
    return hashlib.sha256(item.encode()).hexdigest()[:16]
