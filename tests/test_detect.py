import pytest
from mail_helpers import OWN, make_msg

from readlater.sources.detect import extract_body, is_candidate


@pytest.mark.parametrize("kw, ok", [
    ({}, True), ({"subject": "(no subject)"}, True), ({"subject": "   "}, True), ({"subject": "Hello"}, False),
    ({"frm": "other@y.com"}, False), ({"to": "other@y.com"}, False),
    ({"to": "other@y.com", "cc": "me@x.com"}, True),
    ({"headers": {"In-Reply-To": "<a@b>"}}, False), ({"headers": {"References": "<a@b>"}}, False),
    ({"body": "just a note"}, False), ({"body": " ".join(["w"] * 301)}, True),
])
def test_is_candidate(kw, ok):
    assert is_candidate(make_msg(**kw), OWN) is ok


def test_html_only_body_links():
    m = make_msg(body=None, html='<p>read <a href="https://a.com/h">this</a></p>')
    assert is_candidate(m, OWN) and extract_body(m)[1] == ["https://a.com/h"]
