"""The email-migration rule: which old messages are articles you sent yourself."""
from email.message import EmailMessage
from email.utils import getaddresses
from html.parser import HTMLParser

from readlater.sources.links import find_items

EMPTY_SUBJECTS = {"", "(no subject)", "no subject"}


class _HTMLText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.text: list[str] = []
        self.hrefs: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            href = dict(attrs).get("href")
            if href and href.startswith(("http://", "https://")):
                self.hrefs.append(href)

    def handle_data(self, data):
        self.text.append(data)


def _content(part) -> str:
    try:
        return part.get_content()
    except (LookupError, UnicodeError):   # unknown or broken charset
        return part.get_payload(decode=True).decode("utf-8", "replace")


def extract_body(msg: EmailMessage) -> tuple[str, list[str]]:
    """Returns (text, hrefs): the plain-text part if any, else the HTML part's text; links from the HTML part."""
    plain = msg.get_body(preferencelist=("plain",))
    html = msg.get_body(preferencelist=("html",))
    parsed = _HTMLText()
    if html is not None:
        parsed.feed(_content(html))
    text = _content(plain) if plain is not None else " ".join(parsed.text)
    return text, parsed.hrefs


def _addresses(msg: EmailMessage, *headers: str) -> set[str]:
    values = [str(v) for h in headers for v in msg.get_all(h, [])]
    return {addr.lower() for _, addr in getaddresses(values) if addr}


def is_candidate(msg: EmailMessage, own: list[str]) -> bool:
    own_set = {a.lower() for a in own}
    if str(msg.get("Subject", "")).strip().lower() not in EMPTY_SUBJECTS:
        return False
    if not _addresses(msg, "From") & own_set or not _addresses(msg, "To", "Cc") & own_set:
        return False
    if msg.get("In-Reply-To") or msg.get("References"):
        return False
    text, hrefs = extract_body(msg)
    return bool(find_items(text, hrefs))
