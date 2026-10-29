"""`stack core web fetch`, as an admin runs it when a link was filed badly.

Each test runs `./stack core web fetch` as a process against the Simpsons'
instance (`tests/instance.py`), with the page on `httpserver`. The read
runs in the bot runner, as `python -m stack.web`, so these cover the
command, the module's entry point and the fetch ladder together. The
pages are the captured ones in `tests/fixtures/web/`.
"""

from __future__ import annotations

from tests.instance import running


def test_fetch_reads_a_page_the_way_the_stack_reads_a_link(tmp_path, httpserver, web_fixture):
    """Given a recipe page that publishes its own structured data, when an
    admin runs `stack core web fetch <url> --json`, then the page is read
    from that data (tier 1), and its title and text come back."""
    httpserver.expect_request("/rezept").respond_with_data(web_fixture("recipe-jsonld"),
                                                           content_type="text/html")
    with running(tmp_path) as stack:
        ran = stack.run("core", "web", "fetch", httpserver.url_for("/rezept"), "--json")

    assert ran.code == 0, ran
    assert (ran.json["verdict"], ran.json["tier"]) == ("ok", "1")
    assert ran.json["title"] and ran.json["text"]


def test_a_blocked_page_is_a_read_not_a_failure(tmp_path, httpserver, web_fixture):
    """Given a page that serves a bot-protection challenge with a 403,
    when an admin fetches it, then the command exits 0 and says which
    refusal it was, with no text: the stack read the page, and the page
    was a challenge."""
    httpserver.expect_request("/shop").respond_with_data(web_fixture("cloudflare-challenge"),
                                                         status=403, content_type="text/html")
    with running(tmp_path) as stack:
        ran = stack.run("core", "web", "fetch", httpserver.url_for("/shop"), "--json")

    assert ran.code == 0, ran
    assert ran.json["verdict"] == "challenge"
    assert ran.json["text"] is None


def test_people_see_the_text_of_the_page(tmp_path, httpserver, web_fixture):
    """Given the recipe page, when an admin fetches it without `--json`,
    then the text the stack read is on screen, and no JSON."""
    httpserver.expect_request("/rezept").respond_with_data(web_fixture("recipe-jsonld"),
                                                           content_type="text/html")
    with running(tmp_path) as stack:
        answer = stack.run("core", "web", "fetch", httpserver.url_for("/rezept"), "--json").json
        ran = stack.run("core", "web", "fetch", httpserver.url_for("/rezept"))

    assert ran.code == 0, ran
    assert answer["text"].strip() in ran.out
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
