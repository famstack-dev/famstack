"""The host CLI's transport: stdlib only, and a block is a page.

`stack web fetch` has to work on a Mac with nothing running and no
virtualenv — that is the whole reason tiers 0 to 2 are not in a
container. So the host gets a urllib transport rather than the bot's
aiohttp one, and the ladder is handed whichever fits.

The subtle requirement is the error path. urllib raises on any 4xx,
but a 403 carrying a Cloudflare challenge is not a transport failure —
it is the page we most need the gate to read. A transport that lets
that exception escape would turn every blocked site into "could not be
reached", which is both wrong and less useful than the truth.

Driven against pytest-httpserver, so the real urllib code path runs.
"""

from __future__ import annotations

from stack.web.fetch import BROWSER_HEADERS, fetch_url, urllib_transport

ARTICLE = """<!DOCTYPE html><html><head><title>Why local LLMs matter</title></head>
<body><main><article><h1>Why local LLMs matter</h1>
<p>Running models on your own hardware changes the privacy calculus entirely.
Your prompts never leave the machine, the model never phones home, and you can
iterate without worrying about quotas or per-token billing at any point.</p>
<p>For a family this means one server in a closet replaces three separate cloud
subscriptions. The arithmetic works out somewhere around the fourth month.</p>
</article></main></body></html>"""

CHALLENGE = (
    '<html><head><title>Just a moment...</title></head>'
    '<body><span id="challenge-error-text">Enable JavaScript and cookies to '
    "continue</span></body></html>"
)


class TestTheHostCanReadAPage:
    async def test_an_article_fetches_and_extracts(self, httpserver):
        httpserver.expect_request("/article").respond_with_data(
            ARTICLE, content_type="text/html",
        )

        outcome = await fetch_url(
            httpserver.url_for("/article"), transport=urllib_transport(),
        )

        assert outcome.ok
        assert outcome.content is not None
        assert "privacy calculus" in outcome.content.text
        assert outcome.content.title_hint == "Why local LLMs matter"

    async def test_browser_headers_are_actually_sent(self, httpserver):
        """Plenty of CDNs answer a bare library user-agent with a 403 out
        of habit. Sending a browser's headers is the difference between
        reading a public page and not, so it is worth pinning that they
        reach the wire rather than sitting in a dict."""
        seen = {}

        def _handler(request):
            seen.update(request.headers)
            from werkzeug.wrappers import Response as WerkzeugResponse
            return WerkzeugResponse(ARTICLE, content_type="text/html")

        httpserver.expect_request("/article").respond_with_handler(_handler)
        await fetch_url(httpserver.url_for("/article"), transport=urllib_transport())

        assert seen.get("User-Agent") == BROWSER_HEADERS["User-Agent"]


class TestABlockIsAPageNotAnError:
    async def test_a_403_challenge_reaches_the_gate(self, httpserver):
        """urllib raises HTTPError on a 403. If that escapes, every
        blocked site reports as unreachable and the family is told
        something false."""
        httpserver.expect_request("/blocked").respond_with_data(
            CHALLENGE, status=403, content_type="text/html",
        )

        outcome = await fetch_url(
            httpserver.url_for("/blocked"), transport=urllib_transport(),
        )

        assert outcome.verdict.name == "challenge"
        assert outcome.content is None

    async def test_a_404_is_reported_as_empty_not_unreachable(self, httpserver):
        httpserver.expect_request("/missing").respond_with_data(
            "<html><body>Not found</body></html>", status=404,
            content_type="text/html",
        )

        outcome = await fetch_url(
            httpserver.url_for("/missing"), transport=urllib_transport(),
        )

        assert not outcome.ok
        assert outcome.verdict.name == "empty"

    async def test_a_dead_host_is_still_a_verdict(self):
        """A genuine transport failure — nothing listening — must also
        come back as a verdict, because the CLI prints one either way."""
        outcome = await fetch_url(
            "http://127.0.0.1:1/article", transport=urllib_transport(timeout=2),
        )

        assert not outcome.ok
        assert outcome.verdict.detail


class TestStructuredDataOverTheWire:
    async def test_a_recipe_served_by_http_is_read_from_its_markup(
        self, httpserver, web_fixture,
    ):
        """The Phase 2 promise: `stack web fetch` on a recipe prints
        ingredients with no container running at all."""
        httpserver.expect_request("/rezept").respond_with_data(
            web_fixture("recipe-jsonld"), content_type="text/html",
        )

        outcome = await fetch_url(
            httpserver.url_for("/rezept"), transport=urllib_transport(),
        )

        assert outcome.ok
        assert outcome.tier == "1"
        assert "## Ingredients" in outcome.content.text
