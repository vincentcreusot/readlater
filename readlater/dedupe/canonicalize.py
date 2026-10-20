"""Turn the many spellings of one article URL into a single dedupe key."""
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

TRACKING_PREFIXES = ("utm_",)
TRACKING_KEYS = {"fbclid", "gclid", "mc_eid", "mc_cid", "igshid", "ref", "ref_src"}
DEFAULT_PORTS = {80, 443}


def _is_tracking(key: str) -> bool:
    k = key.lower()
    return k.startswith(TRACKING_PREFIXES) or k in TRACKING_KEYS


def canonicalize(url: str) -> str:
    s = urlsplit(url.strip())
    host = (s.hostname or "").lower().removeprefix("www.")
    if s.port and s.port not in DEFAULT_PORTS:
        host = f"{host}:{s.port}"
    query = sorted((k, v) for k, v in parse_qsl(s.query, keep_blank_values=True) if not _is_tracking(k))
    path = s.path.rstrip("/") or "/"
    return urlunsplit(("https", host, path, urlencode(query), ""))
