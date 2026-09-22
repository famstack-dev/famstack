"""The ladder's promise: cheap first, expensive only on proven failure.

Two things are being pinned here, and the second matters more than the
first.

The obvious one is ordering — structured data beats extraction, and a
browser is reached only when a browser could actually help.

The load-bearing one is that *no tier skips the gate*. The bug that
started this work was an extractor whose success condition was "the
string is not empty". A 19-second stealth fetch that returns a
challenge page is exactly as wrong as an instant one, and costs more,
so tier 3 gets judged on the same terms as tier 2.

Transports are plain callables here — the ladder takes one rather than
importing a client, so these tests drive the real ladder over real
fixture HTML with no network and no mocking of anything internal.
"""

from __future__ import annotations

from stack.web.fetch import Response, fetch_url


def serving(html: str, *, url: str = "https://example.com/article",
            status: int = 200, content_type: str = "text/html"):
    """A transport that answers every request with one page."""
    async def _transport(_url: str, _headers: dict) -> Response:
        return Response(url=url, status=status, html=html, content_type=content_type)
    return _transport


def unreachable():
    async def _transport(_url: str, _headers: dict):
        raise OSError("connection refused")
    return _transport


def recording(html: str, **kwargs):
    """A transport that also records what it was asked for, so a test
    can assert on the URL the ladder actually requested."""
    calls: list[str] = []

    async def _transport(url: str, headers: dict) -> Response:
        calls.append(url)
        return Response(url=kwargs.get("url", url), status=kwargs.get("status", 200),
                        html=html, content_type="text/html")
    return _transport, calls


ARTICLE = """<!DOCTYPE html><html><head><title>Why local LLMs matter</title></head>
<body><main><article><h1>Why local LLMs matter</h1>
<p>Running models on your own hardware changes the privacy calculus entirely.
Your prompts never leave the machine, the model never phones home, and you can
iterate without worrying about quotas or per-token billing at any point.</p>
<p>For a family this means one server in a closet replaces three separate cloud
subscriptions. The arithmetic works out somewhere around the fourth month, give
or take whatever the power company decides to charge this year.</p>
</article></main></body></html>"""


class TestTheHappyPath:
    async def test_an_article_becomes_content_with_a_title(self):
        outcome = await fetch_url("https://example.com/article", transport=serving(ARTICLE))

        assert outcome.ok
        assert outcome.verdict.name == "ok"
        assert outcome.content is not None
        assert "privacy calculus" in outcome.content.text
        assert outcome.content.title_hint == "Why local LLMs matter"

    async def test_the_source_uri_is_where_we_landed(self):
        """Redirects are normal and the vault should point at the page
        that actually exists, not the link somebody pasted."""
        outcome = await fetch_url(
            "https://example.com/old",
            transport=serving(ARTICLE, url="https://example.com/new"),
        )
        assert outcome.content is not None
        assert outcome.content.source_uri == "https://example.com/new"

    async def test_tier_zero_runs_before_any_request(self):
        """The transport must be asked for the canonical URL, not the
        one handed in — otherwise the rewrite is decorative."""
        transport, calls = recording(ARTICLE)
        await fetch_url("https://www.reddit.com/r/x/?utm_source=share", transport=transport)

        assert calls == ["https://old.reddit.com/r/x/"]


class TestStructuredDataWins:
    async def test_json_ld_short_circuits_extraction(self, web_fixture):
        """When a site publishes its own Recipe object there is nothing
        to be gained by guessing at the rendered page."""
        outcome = await fetch_url(
            "https://www.essen-und-trinken.de/rezepte/48816-rzpt-griechischer-salat",
            transport=serving(web_fixture("recipe-jsonld")),
        )

        assert outcome.ok
        assert outcome.tier == "1"
        assert outcome.content is not None
        assert "## Ingredients" in outcome.content.text

    async def test_a_page_without_markup_falls_through_to_extraction(self):
        outcome = await fetch_url("https://example.com/article", transport=serving(ARTICLE))
        assert outcome.tier == "2"


class TestBlockedPagesProduceAReasonNotContent:
    """Every one of these used to produce a vault entry."""

    async def test_a_challenge_yields_no_content(self, web_fixture):
        outcome = await fetch_url(
            "https://www.decathlon.de/",
            transport=serving(web_fixture("cloudflare-challenge"), status=403),
        )

        assert not outcome.ok
        assert outcome.content is None
        assert outcome.verdict.name == "challenge"

    async def test_a_login_wall_yields_no_content(self, web_fixture):
        """The 200 status and the 320 KB body are what made this file
        as an article. The landing URL is the honest signal."""
        outcome = await fetch_url(
            "https://www.reddit.com/r/selfhosted/",
            transport=serving(
                web_fixture("reddit-login-wall"),
                url="https://old.reddit.com/login/?reason=lor2",
            ),
        )

        assert not outcome.ok
        assert outcome.verdict.name == "login"

    async def test_the_reason_names_the_site_where_we_know_it(self, web_fixture):
        """A profile's note beats generic advice: the family is told
        what is actually true about reddit."""
        outcome = await fetch_url(
            "https://www.reddit.com/r/selfhosted/",
            transport=serving(
                web_fixture("reddit-login-wall"),
                url="https://old.reddit.com/login/?reason=lor2",
            ),
        )
        assert "signed in" in outcome.verdict.detail

    async def test_a_non_html_url_is_declined_with_its_type(self):
        """PDFs reach the archivist by a different road (upload to
        Paperless). This path is web pages only, and says so."""
        outcome = await fetch_url(
            "https://example.com/report.pdf",
            transport=serving("%PDF-1.7", content_type="application/pdf"),
        )

        assert not outcome.ok
        assert "application/pdf" in outcome.verdict.detail

    async def test_an_unreachable_host_is_a_verdict_not_an_exception(self):
        """The ladder's contract is that it never raises: a caller
        rendering a chat reply has nothing to do with a socket error."""
        outcome = await fetch_url("https://example.invalid/x", transport=unreachable())

        assert not outcome.ok
        assert outcome.verdict.detail


class TestEscalation:
    """Tier 3 is the only expensive rung, so when it runs is the whole
    cost model."""

    async def test_a_challenge_escalates_and_the_browser_result_is_used(self, web_fixture):
        outcome = await fetch_url(
            "https://www.decathlon.de/",
            transport=serving(web_fixture("cloudflare-challenge"), status=403),
            stealth=serving(ARTICLE, url="https://www.decathlon.de/"),
        )

        assert outcome.ok
        assert outcome.tier == "3"

    async def test_a_login_wall_does_not_escalate(self, web_fixture):
        """A browser is served the same login wall, slower. Escalating
        anything a browser cannot fix turns tier 3 into a tax."""
        calls = []

        async def _stealth(url: str, headers: dict):
            calls.append(url)
            return Response(url=url, status=200, html=ARTICLE)

        await fetch_url(
            "https://www.reddit.com/r/x/",
            transport=serving(web_fixture("reddit-login-wall"),
                              url="https://old.reddit.com/login/?reason=lor2"),
            stealth=_stealth,
        )

        assert calls == [], "a login wall must not reach the browser tier"

    async def test_an_empty_page_does_not_escalate(self):
        calls = []

        async def _stealth(url: str, headers: dict):
            calls.append(url)
            return Response(url=url, status=200, html=ARTICLE)

        await fetch_url(
            "https://example.com/x",
            transport=serving("<html><body></body></html>"),
            stealth=_stealth,
        )

        assert calls == []

    async def test_the_browser_tier_is_judged_too(self, web_fixture):
        """A stealth fetch that is also served a challenge is exactly
        as wrong as a cheap one, and cost 19 seconds. It gets the same
        gate."""
        challenge = web_fixture("cloudflare-challenge")
        outcome = await fetch_url(
            "https://www.decathlon.de/",
            transport=serving(challenge, status=403),
            stealth=serving(challenge, status=403),
        )

        assert not outcome.ok
        assert outcome.content is None

    async def test_escalation_happens_at_most_once(self, web_fixture):
        """There is no loop back to tier 2. If the browser is blocked
        too, the answer is that we cannot read this page."""
        challenge = web_fixture("cloudflare-challenge")
        calls = []

        async def _stealth(url: str, headers: dict):
            calls.append(url)
            return Response(url=url, status=403, html=challenge)

        await fetch_url(
            "https://www.decathlon.de/",
            transport=serving(challenge, status=403),
            stealth=_stealth,
        )

        assert len(calls) == 1

    async def test_without_the_web_stacklet_a_challenge_is_terminal(self, web_fixture):
        """`stealth=None` is what "the web stacklet is not installed"
        looks like. It must degrade to an honest reason, not an error."""
        outcome = await fetch_url(
            "https://www.decathlon.de/",
            transport=serving(web_fixture("cloudflare-challenge"), status=403),
            stealth=None,
        )

        assert outcome.verdict.name == "challenge"
        assert outcome.verdict.detail

    async def test_a_broken_browser_tier_falls_back_to_the_cheap_verdict(self, web_fixture):
        """If the stealth service is down, the family still gets told
        the site was blocked rather than nothing at all."""
        outcome = await fetch_url(
            "https://www.decathlon.de/",
            transport=serving(web_fixture("cloudflare-challenge"), status=403),
            stealth=unreachable(),
        )

        assert outcome.verdict.name == "challenge"


class TestShellPages:
    """A page can be HTTP 200, well-formed, and have nothing to read."""

    async def test_a_maps_place_is_answered_from_the_url(self, web_fixture):
        """Google Maps renders in the browser. Rather than a link card,
        the place name in the path makes a real entry the family can
        find again."""
        outcome = await fetch_url(
            "https://www.google.com/maps/place/Brandenburger+Tor/",
            transport=serving(web_fixture("google-maps-shell")),
        )

        assert outcome.ok
        assert outcome.tier == "0"
        assert outcome.content is not None
        assert outcome.content.title_hint == "Brandenburger Tor"

    async def test_an_ordinary_shell_page_is_declined(self):
        """Without a profile saying the URL carries the subject, an
        empty document is just empty."""
        outcome = await fetch_url(
            "https://example.com/app",
            transport=serving("<html><head><title>App</title></head><body></body></html>"),
        )

        assert not outcome.ok
        assert outcome.verdict.name == "empty"
