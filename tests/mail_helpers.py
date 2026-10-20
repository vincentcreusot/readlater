from email.message import EmailMessage

OWN = ["me@x.com"]


def make_msg(subject=None, frm="Me <ME@x.com>", to="me@x.com", cc=None, body="look https://a.com/x",
             html=None, headers=None):
    m = EmailMessage()
    if subject is not None:
        m["Subject"] = subject
    m["From"] = frm
    m["To"] = to
    if cc:
        m["Cc"] = cc
    for k, v in (headers or {}).items():
        m[k] = v
    if body is not None:
        m.set_content(body)
        if html:
            m.add_alternative(html, subtype="html")
    elif html:
        m.set_content(html, subtype="html")
    return m
