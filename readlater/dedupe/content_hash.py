import hashlib


def content_hash(text: str) -> str:
    """SHA-256 of the text with all whitespace runs collapsed to one space."""
    return hashlib.sha256(" ".join(text.split()).encode()).hexdigest()
