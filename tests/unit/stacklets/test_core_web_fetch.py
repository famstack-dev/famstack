"""`stack core web fetch`, as an admin runs it when a link was filed badly.

Each test runs `./stack core web fetch` as a process against the Simpsons'
instance (`tests/instance.py`). The read runs in the bot runner, as
`python -m stack.web`, so these cover the command, the module's entry
point and the fetch ladder's rules together. The command reads public
pages only, and a test has none: a page on `httpserver` is on this
machine, inside the home network. So these tests pin what the command
refuses and how it says so; reading a page over the wire is
`tests/unit/framework/test_web_transport.py`.
"""

from __future__ import annotations

from tests.instance import running


def test_a_page_in_the_home_network_is_not_read(tmp_path, httpserver):
    """Given a page at a home-network address (here this machine), when an
    admin or an agent fetches it with `--json`, then the command exits 0
    with a refusal, no title and no text, and the page is never requested.
    Why: the bot runner sits on the stack's network, next to the Mac and
    the home network, and reads public pages only, as the archivist does."""
    httpserver.expect_request("/router").respond_with_data(SAVED_PAGE, content_type="text/html")
    with running(tmp_path) as stack:
        ran = stack.run("core", "web", "fetch", httpserver.url_for("/router"), "--json")

    assert ran.code == 0, ran
    assert ran.json["verdict"] == "empty"
    assert (ran.json["title"], ran.json["text"]) == (None, None)
    assert httpserver.log == []


def test_people_see_why_a_page_was_not_read(tmp_path, httpserver):
    """Given the same page, when an admin fetches it without `--json`, then
    the reason the `--json` answer gives is on screen, and nothing of the
    page."""
    httpserver.expect_request("/router").respond_with_data(SAVED_PAGE, content_type="text/html")
    with running(tmp_path) as stack:
        answer = stack.run("core", "web", "fetch", httpserver.url_for("/router"), "--json").json
        ran = stack.run("core", "web", "fetch", httpserver.url_for("/router"))

    assert ran.code == 0, ran
    assert answer["detail"] in ran.out
    assert "flowerpot" not in ran.out + ran.err
    assert '"verdict"' not in ran.out


def test_a_local_file_is_never_read(tmp_path):
    """Given a web page saved on the machine that reads pages, when an
    admin or an agent fetches its `file:` address, then nothing of the
    file comes back: the verdict is a refusal, with no title and no text.
    Why: the bot runner sees the family's data and the stack's settings,
    so a link must never be a way to read its disk."""
    page = tmp_path / "saved" / "notes.html"
    page.parent.mkdir()
    page.write_text(SAVED_PAGE)
    with running(tmp_path) as stack:
        ran = stack.run("core", "web", "fetch", page.as_uri(), "--json")

    assert ran.code == 0, ran
    assert ran.json["verdict"] == "empty"
    assert (ran.json["title"], ran.json["text"]) == (None, None)
    assert "flowerpot" not in ran.out + ran.err


# A page as a family member might save it: long enough for the extractor
# to keep, with a sentence that must not leave the disk. Made up.
SAVED_PAGE = """<!DOCTYPE html><html><head><title>Notes for the sitter</title></head>
<body><article><h1>Notes for the sitter</h1>
<p>The spare key is under the blue flowerpot by the back door. Dinner is in
the fridge, and the children go to bed at eight on school nights.</p>
<p>The dog gets one scoop in the morning and one in the evening. Please water
the tomatoes if it does not rain, and lock the shed when you leave.</p>
</article></body></html>"""


def test_core_web_alone_names_its_verb(tmp_path):
    """Given no verb, when an admin runs `stack core web`, then it shows
    the usage of `fetch` and fails nothing."""
    with running(tmp_path) as stack:
        ran = stack.run("core", "web")

    assert ran.code == 0, ran
    assert "stack core web fetch" in ran.out
