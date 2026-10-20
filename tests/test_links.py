import pytest

from readlater.sources.links import find_items, is_url, item_key


@pytest.mark.parametrize("text, expected", [
    ("read this: https://a.com/x.", ["https://a.com/x"]),
    ("see (https://a.com/x)", ["https://a.com/x"]),
    ("https://en.wikipedia.org/wiki/Foo_(bar)", ["https://en.wikipedia.org/wiki/Foo_(bar)"]),
    ("https://a.com/x and again https://a.com/x", ["https://a.com/x"]),
    ("two: https://a.com/1, https://b.com/2!", ["https://a.com/1", "https://b.com/2"]),
    ("no link here", []),
])
def test_find_items_urls(text, expected):
    assert find_items(text) == expected


def test_long_text_without_url_is_pasted_article():
    text = " ".join(["word"] * 301)
    assert find_items(text) == [text]
    assert find_items(" ".join(["word"] * 300)) == []


def test_extra_urls_are_appended_and_deduped():
    assert find_items("hi https://a.com", ["https://b.com", "https://a.com"]) == ["https://a.com", "https://b.com"]


def test_item_key_is_stable():
    assert item_key("https://a.com") == item_key("https://a.com") and len(item_key("x")) == 16


def test_is_url():
    assert is_url("https://a.com") and is_url("http://a.com") and not is_url("some pasted text")


@pytest.mark.parametrize("text", ["«https://a.com/x»", "“https://a.com/x”", "see https://a.com/x…", "見て https://a.com/x。",
                                  "‘https://a.com/x’,"])
def test_unicode_punctuation_is_not_part_of_the_url(text):
    assert find_items(text) == ["https://a.com/x"]
