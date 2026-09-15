"""What the quality gate promises: no blocked page reaches the vault.

The archivist used to treat "trafilatura returned some text" as success.
A Cloudflare interstitial, a login wall and a JavaScript shell all
return text, so all three were filed as if they were articles. This
module pins the opposite promise: every fetched page is classified
before anything downstream sees it, and the classification is a *reason*
rather than a boolean, because the reason is what the family reads in
chat and what we grep for in the logs.

The fixtures under `tests/fixtures/web/` are real captures, not
hand-written HTML. That matters: a fixture written next to the detector
would encode the same assumption as the detector and both would agree
while reality disagreed. Each was fetched from the live site, then had
inline script and style *bodies* stripped (see the fixtures README) --
every tag, attribute, meta element and ld+json block the gate reads is
exactly what the site served.
"""

from __future__ import annotations

import pytest

from stack.web.quality import Page, assess


# ── Blocked pages are named, not merely rejected ──────────────────────

class TestBlockedPagesAreClassifiedByReason:
    """Each block shape gets its own verdict. The caller renders a
    different chat reply per reason, so collapsing them to "failed"
    would be a regression in what the family is told."""

    def test_cloudflare_interstitial_is_a_challenge(self, web_fixture):
        """decathlon.de answers an anonymous fetch with Cloudflare's
        "Just a moment..." page at HTTP 403. This is the page that a
        browser tier could plausibly get past, so it must be named
        `challenge` and not lumped in with a hard refusal."""
        verdict = assess(Page(
            url="https://www.decathlon.de/",
            status=403,
            html=web_fixture("cloudflare-challenge"),
        ))

        assert verdict.name == "challenge"
        assert not verdict.ok

    def test_login_wall_is_detected_from_the_final_url(self, web_fixture):
        """old.reddit.com 302s an anonymous reader to `/login/?reason=lor2`
        and serves 320 KB of JavaScript shell titled "Welcome to Reddit".

        Nothing in the body says "you must log in", so a detector that
        only reads HTML is blind to it -- and the 200 status plus the
        large body is exactly what made this file as a real article. The
        landing URL is the honest signal, which is why the gate is given
        the URL it ended on rather than the one it was asked for."""
        verdict = assess(Page(
            url="https://old.reddit.com/login/?reason=lor2&dest=https%3A%2F%2Fold.reddit.com%2Fr%2Fselfhosted%2F",
            status=200,
            html=web_fixture("reddit-login-wall"),
            text="Welcome to Reddit\n\nThe heart of the internet",
        ))

        assert verdict.name == "login"

    def test_javascript_shell_with_no_article_is_empty(self, web_fixture):
        """A Google Maps place URL returns HTTP 200 and a full HTML
        document whose only prose is the site-wide meta description.
        There is no page to read, so the verdict is `empty` -- the
        caller turns that into a link card rather than an entry
        summarising "Find local businesses, view maps"."""
        verdict = assess(Page(
            url="https://www.google.com/maps/place/Brandenburger+Tor/",
            status=200,
            html=web_fixture("google-maps-shell"),
            text="",
        ))

        assert verdict.name == "empty"

    def test_consent_redirect_is_named_consent(self):
        """Google bounces EU traffic to `consent.google.com` before it
        will serve anything. The host is the contract here -- it is a
        documented, stable redirect target, unlike the wording of the
        banner, which is localised and changes."""
        verdict = assess(Page(
            url="https://consent.google.com/m?continue=https://www.google.com/search",
            status=200,
            html="<html><body>Bevor du zu Google weitergehst</body></html>",
        ))

        assert verdict.name == "consent"

    def test_payment_required_status_is_a_paywall(self):
        """HTTP 402 is rare but unambiguous. Naming it separately keeps
        "we could fetch this with a browser" (challenge) apart from
        "no amount of fetching will help" (paywall)."""
        verdict = assess(Page(
            url="https://example.com/article",
            status=402,
            html="<html><body>Subscribe to continue</body></html>",
        ))

        assert verdict.name == "paywall"


# ── Good pages must survive the gate ──────────────────────────────────

class TestRealPagesPass:
    """A gate that rejects everything is not a fix. These are live
    captures of pages we *want* filed, including ones carrying the
    cookie banners and bot-protection vocabulary that a careless
    detector would trip over."""

    def test_article_with_a_body_is_ok(self, web_fixture, extracted):
        """essen-und-trinken.de serves a recipe page to a plain fetch.
        Once trafilatura has a body, the verdict is `ok`."""
        html = web_fixture("recipe-jsonld")
        verdict = assess(Page(
            url="https://www.essen-und-trinken.de/rezepte/48816-rzpt-griechischer-salat",
            status=200,
            html=html,
            text=extracted(html),
        ))

        assert verdict.name == "ok"
        assert verdict.ok

    def test_shop_page_with_a_cookie_banner_is_still_ok(self, web_fixture, extracted):
        """geizhals.de serves its real listing to an anonymous fetch and
        carries a cookie consent banner in the same document. The banner
        must not be read as a consent *wall*: the content is right
        there. This is the false-positive guard for consent detection."""
        html = web_fixture("shop-listing-ok")
        verdict = assess(Page(
            url="https://geizhals.de/",
            status=200,
            html=html,
            text=extracted(html),
        ))

        assert verdict.name == "ok"

    def test_prose_about_bot_protection_is_not_a_challenge(self):
        """An article *about* Cloudflare contains every word the
        challenge page does. Detection keys on structure -- the exact
        `<title>`, Cloudflare's own DOM ids, its challenge host -- so
        writing about the thing does not trip the detector for it."""
        verdict = assess(Page(
            url="https://example.com/blog/cloudflare",
            status=200,
            html="<html><head><title>How Cloudflare's Just a moment page works</title></head><body></body></html>",
            text=(
                "Cloudflare's interstitial shows the text 'Just a moment...' "
                "while it runs a challenge. Enable JavaScript and cookies to "
                "continue is the fallback message shown to clients that cannot "
                "execute the challenge script. This post explains what the "
                "browser is actually doing during those few seconds and why "
                "the check exists at all for high-traffic origins."
            ),
        ))

        assert verdict.name == "ok"


# ── The empty floor ───────────────────────────────────────────────────

class TestEmptyFloor:
    """`ok` requires enough prose to be worth a vault entry. The floor
    exists because the failure it prevents is silent: a sidebar, a
    cookie notice or a nav rail extracts cleanly and reads like content
    to everything downstream."""

    def test_no_text_is_empty(self):
        verdict = assess(Page(url="https://example.com", status=200, html="<html></html>", text=""))
        assert verdict.name == "empty"

    def test_whitespace_only_text_is_empty(self):
        verdict = assess(Page(url="https://example.com", status=200, html="<html></html>", text="   \n\n  "))
        assert verdict.name == "empty"

    def test_a_nav_rail_sized_body_is_empty(self):
        """trafilatura on a blocked Reddit page returned 821 characters
        of subreddit sidebar. Short extractions are the signature of
        having scraped furniture instead of an article."""
        verdict = assess(Page(
            url="https://example.com",
            status=200,
            html="<html></html>",
            text="Home | About | Archive | Subscribe | Contact",
        ))
        assert verdict.name == "empty"

    def test_the_floor_is_caller_tunable(self):
        """A site profile may legitimately produce short bodies. The
        floor is an argument so a profile can lower it rather than
        forcing the caller to bypass the gate entirely."""
        short = "Two short sentences. That is the whole page."
        assert assess(Page(url="https://e.com", status=200, html="", text=short)).name == "empty"
        assert assess(Page(url="https://e.com", status=200, html="", text=short), min_chars=10).name == "ok"


# ── Verdicts carry a reason ───────────────────────────────────────────

class TestVerdictCarriesDetail:
    """The reason is a value because it has two readers: the chat reply
    the family sees, and whoever greps the logs asking why a link did
    not file."""

    @pytest.mark.parametrize("name", ["challenge", "login", "consent", "paywall", "empty"])
    def test_every_failure_explains_itself(self, name, web_fixture):
        pages = {
            "challenge": Page(url="https://d.de/", status=403, html=web_fixture("cloudflare-challenge")),
            "login": Page(url="https://old.reddit.com/login/?reason=lor2", status=200, html="<html></html>", text="x" * 400),
            "consent": Page(url="https://consent.google.com/m", status=200, html="<html></html>"),
            "paywall": Page(url="https://e.com/a", status=402, html="<html></html>"),
            "empty": Page(url="https://e.com/a", status=200, html="<html></html>", text=""),
        }
        verdict = assess(pages[name])
        assert verdict.name == name
        assert verdict.detail, "a failure verdict with no detail tells nobody anything"
