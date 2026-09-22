"""Tier 0: the free step, and the one that changes the most outcomes.

Canonicalization runs before any request. It costs nothing, and it does
two jobs — it stops the vault accumulating four URLs for one page, and
it keeps the person who shared a link out of the link. A campaign tag
says "Marge clicked this from a newsletter", and that ends up in chat
and in the wiki alongside the entry.
"""

from __future__ import annotations

import pytest

from stack.web.profiles import canonicalize, profile_for, strip_tracking, title_from_url


class TestTrackingParametersAreDropped:
    """Campaign and referrer tags identify the sharer, not the page."""

    @pytest.mark.parametrize("param", [
        "utm_source=newsletter", "utm_medium=email", "fbclid=abc123",
        "gclid=xyz", "igshid=99", "mc_eid=deadbeef", "ref_src=twsrc",
    ])
    def test_known_trackers_go(self, param):
        cleaned = strip_tracking(f"https://example.com/article?{param}")
        assert cleaned == "https://example.com/article"

    def test_load_bearing_parameters_stay(self):
        """A query string is usually the page. Stripping an article id
        or a page number would turn a good link into a 404, so anything
        unrecognised is kept."""
        url = "https://example.com/search?q=immich&page=3&id=4711"
        assert strip_tracking(url) == url

    def test_trackers_are_removed_from_among_real_parameters(self):
        cleaned = strip_tracking("https://example.com/p?id=7&utm_source=x&page=2")
        assert "id=7" in cleaned and "page=2" in cleaned
        assert "utm_source" not in cleaned

    def test_a_url_with_no_query_is_untouched(self):
        url = "https://example.com/article"
        assert strip_tracking(url) == url


class TestHostRewrites:
    """Reddit is the only rewrite that ships. It is kept even though it
    no longer recovers the post, because of what it does to the *reason*
    — see the profile's own comment."""

    @pytest.mark.parametrize("given", [
        "https://www.reddit.com/r/selfhosted/comments/abc/title/",
        "https://reddit.com/r/selfhosted/comments/abc/title/",
    ])
    def test_reddit_is_rewritten_to_the_old_frontend(self, given):
        assert canonicalize(given).startswith("https://old.reddit.com/r/selfhosted/")

    def test_a_path_survives_the_rewrite(self):
        out = canonicalize("https://www.reddit.com/r/selfhosted/comments/abc/title/")
        assert out.endswith("/r/selfhosted/comments/abc/title/")

    def test_already_canonical_urls_are_left_alone(self):
        url = "https://old.reddit.com/r/selfhosted/"
        assert canonicalize(url) == url

    def test_unprofiled_hosts_are_not_rewritten(self):
        url = "https://example.com/article"
        assert canonicalize(url) == url

    def test_canonicalize_strips_trackers_too(self):
        out = canonicalize("https://www.reddit.com/r/x/?utm_source=share")
        assert "utm_source" not in out
        assert "old.reddit.com" in out


class TestProfileLookup:
    def test_subdomains_resolve_to_the_parent_profile(self):
        assert profile_for("https://old.reddit.com/r/x/").name == "reddit"
        assert profile_for("https://www.reddit.com/r/x/").name == "reddit"

    def test_an_unknown_host_gets_the_default(self):
        assert profile_for("https://example.com/").name == "default"

    def test_a_lookalike_host_does_not_match(self):
        """Suffix matching must be on label boundaries: `notreddit.com`
        is somebody else's site."""
        assert profile_for("https://notreddit.com/r/x/").name == "default"

    def test_reddit_asks_for_recall_and_comments(self):
        """A Reddit page's content is the discussion under it. Measured
        at the time: precision returned 821 characters of sidebar,
        recall returned the 5513-character post."""
        profile = profile_for("https://old.reddit.com/r/x/")
        assert profile.favor_recall
        assert profile.include_comments

    def test_a_blocked_site_carries_its_own_explanation(self):
        """Generic advice is worse than naming the reason where we
        already know it."""
        assert "signed in" in (profile_for("https://old.reddit.com/r/x/").blocked_note or "")


class TestTitleFromPath:
    """Some pages have no text because they are an application. A
    Google Maps place is the worked example: HTTP 200, a full document,
    and the only prose is the site-wide meta description. The place
    name is in the path we were handed."""

    def test_a_maps_place_name_is_recovered_from_the_url(self):
        title = title_from_url("https://www.google.com/maps/place/Brandenburger+Tor/")
        assert title == "Brandenburger Tor"

    def test_percent_escapes_are_decoded(self):
        title = title_from_url("https://www.google.com/maps/place/Caf%C3%A9+Einstein/")
        assert title == "Café Einstein"

    def test_an_ordinary_article_url_yields_nothing(self):
        """A slug makes a poor title when the document has a real one,
        so this never guesses outside a profile that asked for it."""
        assert title_from_url("https://example.com/2026/why-local-llms-matter") is None

    def test_a_google_url_that_is_not_a_place_yields_nothing(self):
        assert title_from_url("https://www.google.com/search?q=famstack") is None
